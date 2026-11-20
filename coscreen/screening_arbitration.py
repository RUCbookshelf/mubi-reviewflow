"""Append-only resolution of conflicts from independent screening exports.

This operates on the merged snapshot and a task-scoped SQLite file. It never
writes back to any screener's personal ``decisions`` or ``stage2_decisions``.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import stat
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from coscreen.config import DECISIONS
from coscreen.stats.merge import merge_decisions_multi

StrPath = str | Path

#: 个人库只读打开的 busy 超时（秒）。测试可 patch 缩短以确定性复现「锁忙」。
SCREENER_READ_TIMEOUT = 15


#: 仲裁裁决只允许二选一：用户裁定仲裁不得保存为「暂不确定」（maybe）。
#: 只约束 *仲裁结论*；筛选员个人投票仍可用 maybe（读取/合并不受影响）。
ARBITRATION_DECISIONS = ("include", "exclude")


class ArbitrationDecisionRejected(ValueError):
    """仲裁结论不满足「二选一 + 排除须附理由」契约。

    可结构化映射为 400 的载体：``code`` / ``message_key`` 由 HTTP 层原样输出，
    ``message`` 是中文源串（前端十语言词典按此注册），``params`` 携带出错的
    文献键供界面定位。
    """

    code = "arbitration_decision_invalid"
    message_key = "team.arbitration_decision_invalid"

    def __init__(self, message: str, *, zotero_key: str | None = None) -> None:
        super().__init__(message)
        self.zotero_key = zotero_key
        self.params = {"zotero_key": zotero_key} if zotero_key else {}
        self.details = message


class ArbitrationDecisionInvalid(ArbitrationDecisionRejected):
    """裁决值不是 include/exclude（含 maybe、空值与任意其它值）。"""

    code = "arbitration_decision_invalid"
    message_key = "team.arbitration_decision_invalid"


class ArbitrationReasonRequired(ArbitrationDecisionRejected):
    """裁决为 exclude 但未附非空理由。"""

    code = "arbitration_reason_required"
    message_key = "team.arbitration_reason_required"


class ScreenerLibraryUnavailableError(RuntimeError):
    """筛选员个人库**暂时**不可读（锁忙超时/IO 故障）。

    与「确无决策」「永久损坏」严格区分：后两者沿用既有口径从合并集剔除，
    而暂时不可读必须让调用方可见（HTTP 层映射 503 稍后重试）——否则缺员的
    κ/一致率/冲突集会照常计算，并可能被 server-save 落成 append-only 记录。
    """


_ARBITRATION_SCHEMA = """
CREATE TABLE IF NOT EXISTS screening_arbitrations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  stage TEXT NOT NULL CHECK(stage IN ('stage1', 'stage2')),
  merge_sha256 TEXT NOT NULL CHECK(length(merge_sha256) = 64),
  result_sha256 TEXT NOT NULL CHECK(length(result_sha256) = 64),
  supersedes_id INTEGER REFERENCES screening_arbitrations(id),
  arbitrator_id TEXT NOT NULL,
  records_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(stage, merge_sha256, result_sha256)
);
CREATE TRIGGER IF NOT EXISTS screening_arbitrations_no_update
BEFORE UPDATE ON screening_arbitrations
BEGIN SELECT RAISE(ABORT, 'screening arbitrations are append-only'); END;
CREATE TRIGGER IF NOT EXISTS screening_arbitrations_no_delete
BEFORE DELETE ON screening_arbitrations
BEGIN SELECT RAISE(ABORT, 'screening arbitrations are append-only'); END;
"""


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _decision_package_sha256(frame: pd.DataFrame) -> str:
    """Fingerprint exported decision fields so a renamed duplicate upload is rejected."""
    required = {"zotero_key", "decision"}
    if not required <= set(frame.columns):
        raise ValueError("决策包必须包含 zotero_key 和 decision 列")
    columns = ("zotero_key", "decision", "exclusion_reason", "notes", "tags", "screened_at")
    records = []
    for row in frame.to_dict("records"):
        record = {name: "" if pd.isna(row.get(name, "")) else str(row.get(name, "")) for name in columns}
        record["decision"] = record["decision"].strip().lower()
        records.append(record)
    records.sort(key=lambda row: row["zotero_key"])
    return _sha256(records)


def merge_decision_packages(named_frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Merge 2–9 reviewer exports, rejecting exact duplicate packages first."""
    if not 2 <= len(named_frames) <= 9:
        raise ValueError("请提供 2 到 9 份筛选员决策包")
    fingerprints: dict[str, str] = {}
    normalized: dict[str, pd.DataFrame] = {}
    for name, frame in named_frames.items():
        if not isinstance(name, str):
            raise ValueError("筛选员名称必须为文本")
        label = name.strip()
        if not label or len(label) > 100 or label in normalized:
            raise ValueError("筛选员名称必须为 1 到 100 个字符")
        fingerprint = _decision_package_sha256(frame)
        if fingerprint in fingerprints:
            raise ValueError(
                f"{label} 与 {fingerprints[fingerprint]} 的决策包内容相同；请检查是否重复导入"
            )
        fingerprints[fingerprint] = label
        normalized[label] = frame
    return merge_decisions_multi(normalized)


#: 决策 CSV 的列形状（与 ``list_decisions`` / ``list_stage2_decisions`` 对齐）。
_SCREENER_DECISION_COLUMNS = (
    "zotero_key", "decision", "exclusion_reason", "notes", "tags", "screened_at",
)


def _decision_rows_read_only(path: Path, stage: str) -> list[dict[str, str]]:
    """只读读取单个筛选员库的决策行；库损坏/缺表/无 tags 列一律降级。

    SQL 全部为模块内固定字面量 + ``?`` 参数绑定；``mode=ro`` 保证不执行任何
    写入（含自愈 DDL）。stage 已由调用方校验为固定两值，不参与 SQL 拼装。

    故障口径（补扫修复）：**锁忙超时**、**IO 失败**与**现存文件打不开**
    （SQLite ``unable to open database file``：ACL 拒绝/网络盘抖动/WAL
    ``-shm`` 不可写，review67 M-08）都是暂时不可读，抛
    :class:`ScreenerLibraryUnavailableError`（调用方按 503 重试语义处理）；
    只有「确无决策 / 永久损坏（无法解析的库）」才降级为 ``[]``——不存在
    或非普通文件的路径沿用「无决策」跳过（生产入口 ``TaskInfo.db_files``
    已上游过滤，测试也依赖该口径不创建个人库）。
    """
    columns_no_tags = tuple(c for c in _SCREENER_DECISION_COLUMNS if c != "tags")
    try:
        with closing(sqlite3.connect(
                f"{path.resolve().as_uri()}?mode=ro", uri=True,
                timeout=SCREENER_READ_TIMEOUT)) as conn:
            if stage == "stage2":
                try:
                    rows = conn.execute(
                        "SELECT zotero_key, decision, exclusion_reason, notes, tags, screened_at FROM stage2_decisions WHERE zotero_key <> ? ORDER BY zotero_key",
                        ("",),
                    ).fetchall()
                    return [dict(zip(_SCREENER_DECISION_COLUMNS, row)) for row in rows]
                except sqlite3.OperationalError as exc:
                    if "no such column: tags" not in str(exc).lower():
                        raise
                    rows = conn.execute(
                        "SELECT zotero_key, decision, exclusion_reason, notes, screened_at FROM stage2_decisions WHERE zotero_key <> ? ORDER BY zotero_key",
                        ("",),
                    ).fetchall()
                    return [{**dict(zip(columns_no_tags, row)), "tags": ""} for row in rows]
            try:
                rows = conn.execute(
                    "SELECT zotero_key, decision, exclusion_reason, notes, tags, screened_at FROM decisions WHERE zotero_key <> ? ORDER BY zotero_key",
                    ("",),
                ).fetchall()
                return [dict(zip(_SCREENER_DECISION_COLUMNS, row)) for row in rows]
            except sqlite3.OperationalError as exc:
                if "no such column: tags" not in str(exc).lower():
                    raise
                rows = conn.execute(
                    "SELECT zotero_key, decision, exclusion_reason, notes, screened_at FROM decisions WHERE zotero_key <> ? ORDER BY zotero_key",
                    ("",),
                ).fetchall()
                return [{**dict(zip(columns_no_tags, row)), "tags": ""} for row in rows]
    # 锁忙 / 读写冲突：暂时不可读，绝不等同于「该成员没有决策」。
    except sqlite3.OperationalError as exc:
        message = str(exc).lower()
        if "locked" in message or "busy" in message:
            raise ScreenerLibraryUnavailableError(
                f"screener library is temporarily locked: {path.name}") from exc
        # review67 M-08：现存文件却打不开（ACL 拒绝/网络盘抖动/WAL -shm
        # 不可写）同样是「暂时不可读」——此前一律 return [] 会把缺员面板
        # 当成「该成员无决策」照常算 κ/一致率，还可能被 server-save 落成
        # append-only 记录。不存在/非普通文件的路径维持「无决策」降级。
        # round2 收口（deepseek 复审）：字面量 'unable to open' 覆盖不全
        # （disk I/O error 同类）——现存文件上任何非 locked/busy 的
        # OperationalError 一律 503。
        if path.is_file():
            raise ScreenerLibraryUnavailableError(
                f"screener library cannot be opened: {path.name}") from exc
        return []
    except OSError as exc:
        # IO 故障（磁盘/权限/网络盘抖动）同样是暂时不可读。
        raise ScreenerLibraryUnavailableError(
            f"screener library is temporarily unreadable: {path.name}") from exc
    # 永久损坏/无法解析：沿用既有口径跳过该成员（不阻塞其余筛选员）。
    except sqlite3.DatabaseError:
        return []


def load_screener_decision_packages(
    db_files: Iterable[StrPath], *, stage: str = "stage1",
) -> dict[str, pd.DataFrame]:
    """读取任务目录下全部筛选员个人库的决策，产出可交给
    :func:`merge_decision_packages` 的 ``{筛选员名: DataFrame}``。

    参数
    ----
    db_files:
        任务目录内的库文件列表（通常为 ``TaskInfo.db_files``）。仅接受
        ``*.db``；``collaboration.db`` 即使混入也会被跳过。
    stage:
        ``stage1`` 读 ``decisions``，``stage2`` 读 ``stage2_decisions``。

    规则（确定性，供复现）
    ----------------------
    - 筛选员名取 ``{slug}.db`` 的文件名主干（与 CSV 上传以文件名主干命名一致）。
    - 同一物理路径重复出现只读一次；**不同路径但同主干**时按
      ``(文件名 casefold, 完整路径)`` 排序，先剔除无决策/损坏库，再保留
      排序首个**可读出决策**的同名库（不静默混入第二份决策）。文件系统
      同目录下不可能出现同主干，此规则只对跨目录输入/手工拼接列表生效。
    - 无决策行（空库/无表/永久损坏）的库剔除，不阻塞其余筛选员；**暂时不可读**
      （锁忙超时/IO 故障）抛 :class:`ScreenerLibraryUnavailableError`，绝不把
      「读不到」当成「没有决策」而产出缺员合并表。
    - 有效筛选员 <2 或 >9 抛 ``ValueError``，文案与 CSV 2-9 份上传同口径。

    铁律
    ----
    本函数以 SQLite ``mode=ro`` 打开个人库，**绝不写入任何筛选员个人库**，
    也不执行自愈 DDL；永久损坏只降级为「该库无决策」，暂时不可读则抛
    :class:`ScreenerLibraryUnavailableError`。
    """
    if stage not in {"stage1", "stage2"}:
        raise ValueError("stage 必须为 stage1 或 stage2")
    packages: dict[str, pd.DataFrame] = {}
    seen_paths: set[str] = set()
    ordered = sorted(
        (Path(raw) for raw in db_files),
        key=lambda path: (path.name.casefold(), str(path)),
    )
    for path in ordered:
        if path.suffix.casefold() != ".db" or path.name.casefold() == "collaboration.db":
            continue
        try:
            identity = str(path.resolve())
        except OSError as exc:
            # review67 M-08 同族收紧：连路径都无法解析（权限/网络盘故障）＝
            # 暂时不可读，不得静默 continue 折叠成「该成员没有决策」。
            raise ScreenerLibraryUnavailableError(
                f"screener library path is temporarily unreadable: {path.name}") from exc
        if identity in seen_paths:
            continue
        seen_paths.add(identity)
        label = path.stem
        if label in packages:
            continue  # 同名规则：排序后首个「有决策」的库胜出，见 docstring。
        rows = _decision_rows_read_only(path, stage)
        if not rows:
            continue
        packages[label] = pd.DataFrame(rows, columns=list(_SCREENER_DECISION_COLUMNS))
    if not 2 <= len(packages) <= 9:
        raise ValueError("请提供 2 到 9 份筛选员决策包")
    return packages


def _validated_merge(merged: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    if not isinstance(merged, pd.DataFrame):
        raise ValueError("merged 必须是多人决策合并表")
    decision_columns = [column for column in merged.columns if column.startswith("decision_")]
    names = [column[len("decision_"):] for column in decision_columns]
    required = {"zotero_key", "status", "n_screened", *decision_columns}
    if (len(names) < 2 or len(names) != len(set(names))
            or any(not name or len(name) > 100 or name != name.strip() for name in names)
            or not required <= set(merged.columns)):
        raise ValueError("输入必须是 merge_decisions_multi 的合并结果")

    out = merged.copy()
    if out["zotero_key"].isna().any():
        raise ValueError("合并表含空 zotero_key")
    out["zotero_key"] = out["zotero_key"].astype(str)
    if out["zotero_key"].str.strip().eq("").any():
        raise ValueError("合并表含空 zotero_key")
    if out["zotero_key"].duplicated().any():
        raise ValueError("合并表 zotero_key 重复")

    # CSV readers commonly turn blank one-sided fields into NaN; normalize every
    # text/status column before hashing or JSON serialization.
    for column in out.columns:
        if column not in {"zotero_key", "n_screened"}:
            out[column] = out[column].where(out[column].notna(), "")

    for column in decision_columns:
        out[column] = out[column].fillna("").astype(str).str.strip().str.lower()
        invalid = sorted(set(out[column]) - {"", *DECISIONS})
        if invalid:
            raise ValueError(f"{column} 含非法决策: {invalid[:5]}")

    for _, row in out.iterrows():
        votes = [row[column] for column in decision_columns if row[column]]
        expected_status = (
            "incomplete" if len(votes) < 2 else "agree" if len(set(votes)) == 1 else "conflict"
        )
        try:
            n_screened = float(row["n_screened"])
        except (TypeError, ValueError):
            n_screened = -1
        if row["status"] != expected_status or n_screened != len(votes):
            raise ValueError(f"{row['zotero_key']} 的合并状态与原始决策不一致")
    return out, names


def validate_arbitration_decision(
    decision: Any,
    rationale: Any = "",
    *,
    zotero_key: str | None = None,
) -> tuple[str, str]:
    """r18 两道闸的单条校验：二选一 + 排除必须附非空理由。

    2.1.5 逐条盲裁决会话（``coscreen.arbitration_session``）与 CSV /
    server-save 汇总提交共用本函数，拒绝语义、``error_code``、
    ``message_key`` 与文案逐字一致（复用，不重写）。返回规范化后的
    ``(decision, rationale)``；``rationale`` 为 ``None`` 时按空串处理。
    """
    if not isinstance(decision, str) or decision.strip().lower() not in ARBITRATION_DECISIONS:
        raise ArbitrationDecisionInvalid(
            "仲裁决定必须为「纳入」或「排除」，不能保存为「暂不确定」或其他值。",
            zotero_key=zotero_key,
        )
    if rationale is None:
        rationale = ""
    if not isinstance(rationale, str) or len(rationale) > 4000:
        raise ValueError(f"{zotero_key} 的仲裁说明最多 4000 个字符")
    if decision.strip().lower() == "exclude" and not rationale.strip():
        raise ArbitrationReasonRequired(
            "仲裁决定为「排除」时必须填写仲裁理由后再保存。",
            zotero_key=zotero_key,
        )
    return decision.strip().lower(), rationale


def resolve_decision_conflicts(
    merged: pd.DataFrame,
    resolutions: list[dict[str, Any]],
) -> pd.DataFrame:
    """Add final decisions while retaining every original screener vote.

    r18 用户裁定：仲裁必须做出选择，「暂不确定」不能作为仲裁结论保存；
    裁决为 exclude 时必须附非空理由。两条闸只作用于 ``resolutions``（仲裁
    结论），已保存的历史仲裁记录读取/导出路径与筛选员原始投票（含 maybe）
    均不受影响。
    """
    if not isinstance(resolutions, list):
        raise ValueError("resolutions 必须为数组")
    out, names = _validated_merge(merged)
    by_key: dict[str, tuple[str, str]] = {}
    known = dict(zip(out["zotero_key"], out["status"]))
    for item in resolutions:
        if not isinstance(item, dict):
            raise ValueError("仲裁项必须为对象")
        key = item.get("zotero_key")
        decision = item.get("decision")
        rationale = item.get("rationale", "")
        if not isinstance(key, str) or not key or len(key) > 300 or key != key.strip():
            raise ValueError("仲裁项 zotero_key 必须为 1 到 300 个非空字符")
        if key not in known:
            raise ValueError(f"仲裁键不在本次合并结果中: {key}")
        if known[key] != "conflict":
            raise ValueError(f"仅可仲裁 conflict 行: {key}")
        if key in by_key:
            raise ValueError(f"仲裁键重复: {key}")
        normalized_decision, rationale = validate_arbitration_decision(
            decision, rationale, zotero_key=key)
        by_key[key] = (normalized_decision, rationale)

    finals: list[str] = []
    statuses: list[str] = []
    rationales: list[str] = []
    for _, row in out.iterrows():
        key = row["zotero_key"]
        status = row["status"]
        if status == "agree":
            final = next(row[f"decision_{name}"] for name in names if row[f"decision_{name}"])
            finals.append(final)
            statuses.append("agreed")
            rationales.append("")
        elif key in by_key:
            finals.append(by_key[key][0])
            statuses.append("adjudicated")
            rationales.append(by_key[key][1])
        else:
            finals.append("")
            statuses.append("unresolved")
            rationales.append("")
    out["final_decision"] = finals
    out["arbitration_status"] = statuses
    out["arbitration_reason"] = rationales
    return out


def _read_record(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "stage": row["stage"],
        "merge_sha256": row["merge_sha256"],
        "result_sha256": row["result_sha256"],
        "supersedes_id": row["supersedes_id"],
        "arbitrator_id": row["arbitrator_id"],
        "created_at": row["created_at"],
        "records": json.loads(row["records_json"]),
    }


def append_decision_arbitration(
    store_path: StrPath,
    *,
    stage: str,
    merged: pd.DataFrame,
    resolutions: list[dict[str, Any]],
    arbitrator_id: str | int,
) -> dict[str, Any]:
    """Persist an immutable, idempotent resolution snapshot in task-scoped SQLite."""
    if not isinstance(stage, str) or stage not in {"stage1", "stage2"}:
        raise ValueError("stage 必须为 stage1 或 stage2")
    if not isinstance(arbitrator_id, (str, int)) or isinstance(arbitrator_id, bool):
        raise ValueError("arbitrator_id 必须来自已认证用户")
    actor = str(arbitrator_id).strip()
    if not actor or len(actor) > 128:
        raise ValueError("arbitrator_id 必须为 1 到 128 个字符")

    normalized, _ = _validated_merge(merged)
    source_records = json.loads(_canonical_json(normalized.to_dict("records")))
    resolved = resolve_decision_conflicts(normalized, resolutions)
    records = json.loads(_canonical_json(resolved.to_dict("records")))
    merge_sha = _sha256({"stage": stage, "records": source_records})
    result_sha = _sha256({"stage": stage, "merge_sha256": merge_sha,
                          "arbitrator_id": actor, "records": records})
    created_at = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")

    path = Path(store_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path, timeout=15)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(_ARBITRATION_SCHEMA)
        conn.execute("BEGIN IMMEDIATE")
        try:
            existing = conn.execute(
                "SELECT * FROM screening_arbitrations "
                "WHERE stage=? AND merge_sha256=? AND result_sha256=?",
                (stage, merge_sha, result_sha),
            ).fetchone()
            if existing:
                result = _read_record(existing)
                conn.commit()
                return result
            previous = conn.execute(
                "SELECT id FROM screening_arbitrations WHERE stage=? AND merge_sha256=? "
                "ORDER BY id DESC LIMIT 1",
                (stage, merge_sha),
            ).fetchone()
            cursor = conn.execute(
                "INSERT INTO screening_arbitrations "
                "(stage,merge_sha256,result_sha256,supersedes_id,arbitrator_id,records_json,created_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (stage, merge_sha, result_sha, previous["id"] if previous else None,
                 actor, _canonical_json(records), created_at),
            )
            saved = conn.execute(
                "SELECT * FROM screening_arbitrations WHERE id=?", (cursor.lastrowid,)
            ).fetchone()
            result = _read_record(saved)
            conn.commit()
            return result
        except Exception:
            conn.rollback()
            raise


def get_decision_arbitration(store_path: StrPath, record_id: int) -> dict[str, Any] | None:
    """Read one saved arbitration snapshot without mutating it."""
    path = Path(store_path)
    if not path.is_file():
        return None
    with closing(sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT * FROM screening_arbitrations WHERE id=?", (record_id,)
            ).fetchone()
        # MM11-08：只捕 OperationalError 时，局部页损坏（属主表可读、仲裁表
        # b-tree 页损坏）抛父类 sqlite3.DatabaseError 冒泡成 500。按「读不到
        # 保存的仲裁快照 = 无记录」降级为 None（与表缺失/锁同口径）；但排除
        # ProgrammingError（调用写法错，如参数绑定失败）——那是缺陷而非损坏，
        # 不得冒充「无记录」被静默吞掉。
        except sqlite3.ProgrammingError:
            raise
        except sqlite3.DatabaseError:
            return None
    return _read_record(row) if row else None


def latest_decision_arbitration(store_path: StrPath, stage: str) -> dict[str, Any] | None:
    if stage not in {"stage1", "stage2"}:
        raise ValueError("stage 必须为 stage1 或 stage2")
    path = Path(store_path)
    if not path.is_file():
        return None
    with closing(sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT * FROM screening_arbitrations WHERE stage=? ORDER BY id DESC LIMIT 1",
                (stage,),
            ).fetchone()
        # MM11-08：同 get_decision_arbitration——捕父类 DatabaseError，局部
        # 页损坏降级为 None（无已保存仲裁），不再 500；ProgrammingError 除外。
        except sqlite3.ProgrammingError:
            raise
        except sqlite3.DatabaseError:
            return None
    return _read_record(row) if row else None


# ---------------------------------------------------------------------------
# S1b（2.1.6 team flow）：授权仲裁 attestation 存储——纯新增，generic 语义零改动。
#
# 既有 generic append/latest/get/hash/supersedes、CSV 工具与装载器原义和代码
# 全部保持字节不变；本节只新增 attestation DDL、prepare、借用连接的 attested
# append 与零 DDL 只读查询。多 attestation 可引用同一 generic 记录（无
# UNIQUE(record_id)）：A 改回后内容相同 → generic 幂等复用同 ID，而 input_proof
# 版本变化 → 新 attestation 新 ID 与真实新时间。
# ---------------------------------------------------------------------------

#: 可写入 attestation「保存时有效角色」快照的业务角色值域（当前仅 reconciler，
#: 与 coding_consensus 的 reconciler 同一业务角色族）。这只是**快照取值域**，
#: 不是授权事实：授权名册、写窗口与完成门由后续 trusted runtime 强制，本
#: 存储层不查角色/账本。
ARBITRATION_ATTESTOR_ROLES = ("reconciler",)


class ArbitrationAttestationMetaInvalid(ValueError):
    """attestation 元数据形状不符合冻结契约（键集/类型/取值域/proof 形状）。

    可结构化映射为 400 的载体：``code`` / ``message_key`` 由 HTTP 层原样输出，
    ``params.field`` 携带首个违约字段供界面定位。本层只校形状，不校授权。
    """

    code = "arbitration_attestation_meta_invalid"
    message_key = "team.arbitration_attestation_meta_invalid"

    def __init__(self, message: str, *, field: str | None = None) -> None:
        super().__init__(message)
        self.field = field
        self.params = {"field": field} if field else {}
        self.details = message


class ArbitrationAttestationProofInvalid(ArbitrationAttestationMetaInvalid):
    """input_proof 形状违约（library_id 非 32 位小写 hex、revision 非正整数等）。"""

    code = "arbitration_attestation_proof_invalid"
    message_key = "team.arbitration_attestation_proof_invalid"


class ArbitrationConnectionContractError(RuntimeError):
    """借用连接协议违约（未传 Connection / 调用方事务未开始）。

    编程错误：不降级、不暗开事务、不吞——调用方修复调用方式后重试；本函数
    既不代为 BEGIN，也不建 SAVEPOINT 伪原子。
    """


class ArbitrationAttestationStoreUnavailable(RuntimeError):
    """协作库存有 attestation 但当前不可信读（损坏/锁忙/IO/权限故障）。

    与「尚无 attestation（未 prepare / 无该 ID）」严格区分：后者只读查询返回
    None/[] 且绝不创建库或表；本异常必须让调用方可见（HTTP 层映射 503），
    绝不把「读不到」伪装成「没有仲裁」。``sqlite3.ProgrammingError``（调用
    写法缺陷）不属此捕获族，原样上抛。
    """


#: attestation 表 DDL（逐条语句，仅供 prepare 在自有维护事务内逐条执行）。
#: - ``id``：独立正整数 AUTOINCREMENT 追加序（不与 generic 详情 ID 混用）；
#: - ``record_id`` 引用 generic 记录，**无 UNIQUE**（多 attestation 同 generic）；
#: - ``round_id``：显式存调用方传入值（当前 runtime 恒 None，不忽略非 None
#:   强置 NULL）；
#: - ``attest_sha256``：重试幂等键（摘要含 task/round/stage/generic
#:   record_id/arbitrator/required/proof/resolutions digest）。
_ARBITRATION_ATTESTATION_DDL: tuple[str, ...] = (
    """CREATE TABLE IF NOT EXISTS screening_arbitration_attestations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  record_id INTEGER NOT NULL REFERENCES screening_arbitrations(id),
  task_id TEXT NOT NULL,
  round_id TEXT,
  stage TEXT NOT NULL CHECK(stage IN ('stage1', 'stage2')),
  arbitrator_user_id INTEGER NOT NULL CHECK(typeof(arbitrator_user_id) = 'integer' AND arbitrator_user_id > 0),
  arbitrator_role TEXT NOT NULL,
  required_names_json TEXT NOT NULL,
  input_proof_json TEXT NOT NULL,
  resolutions_digest TEXT NOT NULL CHECK(length(resolutions_digest) = 64),
  attest_sha256 TEXT NOT NULL UNIQUE CHECK(length(attest_sha256) = 64),
  attested_at TEXT NOT NULL
)""",
    """CREATE TRIGGER IF NOT EXISTS screening_arbitration_attestations_no_update
BEFORE UPDATE ON screening_arbitration_attestations
BEGIN SELECT RAISE(ABORT, 'screening arbitration attestations are append-only'); END""",
    """CREATE TRIGGER IF NOT EXISTS screening_arbitration_attestations_no_delete
BEFORE DELETE ON screening_arbitration_attestations
BEGIN SELECT RAISE(ABORT, 'screening arbitration attestations are append-only'); END""",
)


#: attestation 冻结键集（多余/缺失均违约；未来扩展须显式修订本契约）。
_ATTESTATION_META_KEYS = {
    "task_id", "round", "required_names", "arbitrator_user_id",
    "arbitrator_role", "input_proof",
}

_HEX32 = set("0123456789abcdef")


def _is_hex32(value: str) -> bool:
    return len(value) == 32 and all(ch in _HEX32 for ch in value)


def _validate_attestation_meta(attestation_meta: Any) -> dict[str, Any]:
    """校验并规范化 attestation 元数据（形状层；授权/窗口/完成门不在此）。

    字段约束只采用既有 canonical 真实约束并注明出处，不凭空新增会缩窄合法
    身份字符串的 cap：
    - ``task_id``/``round``/``arbitrator_role``/proof 的 user_id 键：仅非空文本
      （无既有 canonical 长度上限，不臆造）；
    - ``required_names`` 每项 1..100：出处 ``merge_decision_packages`` 筛选员
      名称 1..100 规则；≥2：出处 ``_validated_merge`` 的 ≥2 decision 列门
      （单人无冲突不造仲裁，本接口不放宽）；
    - proof ``item_key`` 1..300：出处 ``resolve_decision_conflicts`` 的
      zotero_key 1..300 规则；
    - ``library_id`` 32 位小写 hex：出处 ledger meta ``CHECK length=32`` 与
      ``uuid4().hex``（team_vote_ledger）；
    - ``revision`` 正整数非布尔：出处 ledger ``team_vote_revisions`` 的
      ``CHECK(typeof='integer' AND >=1)``（无 bool/字符串伪整数）。
    """
    if not isinstance(attestation_meta, dict):
        raise ArbitrationAttestationMetaInvalid(
            "attestation_meta 必须为对象。", field="attestation_meta")
    keys = set(attestation_meta)
    missing = sorted(_ATTESTATION_META_KEYS - keys)
    unknown = sorted(keys - _ATTESTATION_META_KEYS)
    if missing or unknown:
        raise ArbitrationAttestationMetaInvalid(
            "attestation_meta 键集不符合冻结契约"
            + (f"，缺失: {missing}" if missing else "")
            + (f"，未知: {unknown}" if unknown else "") + "。",
            field="attestation_meta",
        )
    task_id = attestation_meta["task_id"]
    if not isinstance(task_id, str) or not task_id:
        raise ArbitrationAttestationMetaInvalid(
            "task_id 必须为非空文本。", field="task_id")
    round_value = attestation_meta["round"]
    if round_value is not None and (not isinstance(round_value, str) or not round_value):
        raise ArbitrationAttestationMetaInvalid(
            "round 必须显式为 None 或非空文本（当前 runtime 恒传 None）。", field="round")
    names = attestation_meta["required_names"]
    if (not isinstance(names, list) or len(names) < 2
            or any(not isinstance(name, str) or not name or len(name) > 100 for name in names)
            or len(set(names)) != len(names)):
        raise ArbitrationAttestationMetaInvalid(
            "required_names 必须为 ≥2 个唯一的 1 到 100 字符文本（保序快照）。",
            field="required_names")
    user_id = attestation_meta["arbitrator_user_id"]
    if not isinstance(user_id, int) or isinstance(user_id, bool) or user_id < 1:
        raise ArbitrationAttestationMetaInvalid(
            "arbitrator_user_id 必须为正整数（不能是布尔值）。", field="arbitrator_user_id")
    role = attestation_meta["arbitrator_role"]
    if not isinstance(role, str) or role not in ARBITRATION_ATTESTOR_ROLES:
        raise ArbitrationAttestationMetaInvalid(
            f"arbitrator_role 必须为有效仲裁业务角色之一: {list(ARBITRATION_ATTESTOR_ROLES)}。",
            field="arbitrator_role")
    proof = attestation_meta["input_proof"]
    if not isinstance(proof, dict) or not proof:
        raise ArbitrationAttestationProofInvalid(
            "input_proof 必须为非空对象（item_key → user_id → {library_id, revision}）。",
            field="input_proof")
    normalized_proof: dict[str, dict[str, dict[str, Any]]] = {}
    for item_key, per_user in proof.items():
        if not isinstance(item_key, str) or not item_key or len(item_key) > 300:
            raise ArbitrationAttestationProofInvalid(
                "input_proof 的 item_key 必须为 1 到 300 的非空文本。", field="input_proof")
        if not isinstance(per_user, dict) or not per_user:
            raise ArbitrationAttestationProofInvalid(
                f"input_proof[{item_key}] 必须为非空对象（user_id → 版本条目）。",
                field="input_proof")
        normalized_users: dict[str, dict[str, Any]] = {}
        for user_key, entry in per_user.items():
            if not isinstance(user_key, str) or not user_key:
                raise ArbitrationAttestationProofInvalid(
                    f"input_proof[{item_key}] 的 user_id 必须为非空文本。", field="input_proof")
            if not isinstance(entry, dict) or set(entry) != {"library_id", "revision"}:
                raise ArbitrationAttestationProofInvalid(
                    f"input_proof[{item_key}][{user_key}] 必须恰含 library_id 与 revision。",
                    field="input_proof")
            library_id = entry["library_id"]
            revision = entry["revision"]
            if not isinstance(library_id, str) or not _is_hex32(library_id):
                raise ArbitrationAttestationProofInvalid(
                    f"input_proof[{item_key}][{user_key}].library_id 必须为 32 位小写十六进制。",
                    field="input_proof")
            if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
                raise ArbitrationAttestationProofInvalid(
                    f"input_proof[{item_key}][{user_key}].revision 必须为正整数（不能是布尔值）。",
                    field="input_proof")
            normalized_users[user_key] = {"library_id": library_id, "revision": revision}
        normalized_proof[item_key] = normalized_users
    return {
        "task_id": task_id,
        "round_id": round_value,
        "required_names": list(names),
        "arbitrator_user_id": user_id,
        "arbitrator_role": role,
        "input_proof": normalized_proof,
    }


#: generic 表显式列序（借用连接不改 row_factory，按位置读取；输出键集与
#: ``_read_record`` 一致，但实现独立——不改既有函数）。
_GENERIC_RECORD_COLUMNS = (
    "id", "stage", "merge_sha256", "result_sha256", "supersedes_id",
    "arbitrator_id", "records_json", "created_at",
)

_ATTESTATION_COLUMNS = (
    "id", "record_id", "task_id", "round_id", "stage", "arbitrator_user_id",
    "arbitrator_role", "required_names_json", "input_proof_json",
    "resolutions_digest", "attest_sha256", "attested_at",
)


def _load_persisted_json(raw: Any, *, column: str) -> Any:
    """持久行 JSON 解码边界：仅窄捕 JSON/编码解码损坏（非法 JSON、列被写
    成非文本、非法 UTF-8），转 :class:`ArbitrationAttestationStoreUnavailable`
    （携带列名定位）。输入侧 MetaInvalid/ProofInvalid 与
    ``sqlite3.ProgrammingError`` 不经此边界、不被吞；不做全函数宽 Exception。"""
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError, UnicodeDecodeError) as exc:
        raise ArbitrationAttestationStoreUnavailable(
            f"arbitration attestation store has corrupted persisted JSON in {column}") from exc


def _generic_record_from_row(row: tuple) -> dict[str, Any]:
    values = dict(zip(_GENERIC_RECORD_COLUMNS, row))
    return {
        "id": int(values["id"]),
        "stage": values["stage"],
        "merge_sha256": values["merge_sha256"],
        "result_sha256": values["result_sha256"],
        "supersedes_id": values["supersedes_id"],
        "arbitrator_id": values["arbitrator_id"],
        "created_at": values["created_at"],
        "records": _load_persisted_json(values["records_json"], column="records_json"),
    }


def _attestation_from_row(row: tuple) -> dict[str, Any]:
    values = dict(zip(_ATTESTATION_COLUMNS, row))
    return {
        "id": int(values["id"]),
        "record_id": int(values["record_id"]),
        "task_id": values["task_id"],
        "round_id": values["round_id"],
        "stage": values["stage"],
        "arbitrator_user_id": int(values["arbitrator_user_id"]),
        "arbitrator_role": values["arbitrator_role"],
        "required_names": _load_persisted_json(
            values["required_names_json"], column="required_names_json"),
        "input_proof": _load_persisted_json(
            values["input_proof_json"], column="input_proof_json"),
        "resolutions_digest": values["resolutions_digest"],
        "attest_sha256": values["attest_sha256"],
        "attested_at": values["attested_at"],
    }


def _split_sql_statements(script: str) -> list[str]:
    """用 ``sqlite3.complete_statement`` 累积分割 SQL 脚本为完整语句。

    不能 naive 按 ``;`` 拆：触发器体 ``BEGIN … RAISE(ABORT, '…'); END;`` 含
    内层分号，``complete_statement`` 只在语句真正闭合（含 trigger 的
    ``END;``）时为真。仅分号/空白组成的尾巴（脚本末尾换行）不是语句、不
    入列；尾部不完整视为脚本缺陷，抛 ``ValueError``。
    """
    statements: list[str] = []
    buffer = ""
    for segment in script.split(";"):
        buffer += segment + ";"
        if buffer.strip(" \t\r\n;") and sqlite3.complete_statement(buffer):
            statements.append(buffer.strip())
            buffer = ""
    if buffer.strip(" \t\r\n;"):
        raise ValueError("SQL script has an incomplete trailing statement")
    return statements


def prepare_arbitration_attestation_storage(store_path: StrPath) -> None:
    """以单一维护事务安装 generic＋attestation 存储（幂等；写窗口**前**调用）。

    单 ``BEGIN IMMEDIATE``：既有 ``_ARBITRATION_SCHEMA`` 常量经
    :func:`_split_sql_statements`（``complete_statement`` 完整语句解析，不
    naive 分号拆 trigger 体；常量本身字节零改动）解析出的 generic DDL，与
    新 attestation DDL（``_ARBITRATION_ATTESTATION_DDL``）逐条
    ``conn.execute``，统一 commit/rollback——任一步失败（如授权拒绝 attest
    建表）整体回滚、**无半装对象**（全新 store 回滚后本次不新增任何 schema
    对象）；既有 generic 数据/对象因 ``IF NOT EXISTS`` 幂等原地保留；失败
    解除后重试可成功。与 :func:`append_decision_arbitration_attested` 职责
    分开：本函数自有连接、自有事务；append 只借用调用方连接执行业务语句、
    不建 schema。
    """
    path = Path(store_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path, timeout=15)) as conn:
        statements = (_split_sql_statements(_ARBITRATION_SCHEMA)
                      + list(_ARBITRATION_ATTESTATION_DDL))
        conn.execute("BEGIN IMMEDIATE")
        try:
            for statement in statements:
                conn.execute(statement)
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def append_decision_arbitration_attested(
    conn: sqlite3.Connection,
    *,
    stage: str,
    merged: pd.DataFrame,
    resolutions: list[dict[str, Any]],
    arbitrator_id: str | int,
    attestation_meta: dict[str, Any],
) -> dict[str, Any]:
    """在调用方既有协作库事务连接上，幂等写入 generic 仲裁记录＋授权 attestation。

    借用连接契约（违约即抛 :class:`ArbitrationConnectionContractError`，不降级）：
    - 只执行业务语句：不连接/不迁移/不开 BEGIN/不 commit/不 rollback/不
      close/不 executescript/不改 PRAGMA/不动 row_factory；
    - 调用方必须已开启真实事务；未开启视为编程违约，不暗开；
    - 任一语句失败原样上抛，由调用方对**整个事务** rollback（本函数不建
      SAVEPOINT 伪原子、不代为回滚）；返回不等于已提交，提交权归调用方。

    身份矩阵（幂等键 ``attest_sha256``＝task/round/stage/generic record_id/
    arbitrator_user_id/required_names/input_proof/resolutions_digest 的规范哈希）：
    - 同输入同结论重试 → generic 同 ID、attestation 同 ID（原 ``attested_at``
      原样返回，不取新时间）；
    - proof 版本变化（A 改回后内容相同）→ generic 同 ID 复用、attestation
      新 ID 与真实新 ``attested_at``；
    - task/round/stage/required/proof/actor/结论摘要任一不同 → 不同
      attestation；generic 记录的 ``arbitrator_id`` 只是历史写入者快照，不冒充
      新 attestation 的 ``arbitrator_user_id`` 身份（attestation 身份只来自本次
      meta）。actor 字串不同还意味着不同 result_sha → 不同 generic（既有公式
      原样继承）。

    ``merged``/``resolutions`` 语义与 :func:`append_decision_arbitration` 逐字段
    相同：复用 ``_validated_merge``/``resolve_decision_conflicts``/
    ``validate_arbitration_decision`` 与 canonical JSON/哈希辅助，merge_sha/
    result_sha 公式与 legacy 完全一致（同输入跨两条入口命中同一 generic 记
    录），≥2 人 validated merge 门原样继承——单人不成仲裁。attestation 表未
    prepare 时 ``sqlite3.OperationalError`` 原样上抛（fail-closed，不静默建表）。
    """
    if not isinstance(conn, sqlite3.Connection):
        raise ArbitrationConnectionContractError(
            "append_decision_arbitration_attested 需要既有协作库连接（sqlite3.Connection）。")
    if not conn.in_transaction:
        raise ArbitrationConnectionContractError(
            "调用方必须已开启事务（BEGIN IMMEDIATE）再调用本函数；本函数不自行开启事务。")
    if not isinstance(stage, str) or stage not in {"stage1", "stage2"}:
        raise ValueError("stage 必须为 stage1 或 stage2")
    if not isinstance(arbitrator_id, (str, int)) or isinstance(arbitrator_id, bool):
        raise ValueError("arbitrator_id 必须来自已认证用户")
    actor = str(arbitrator_id).strip()
    if not actor or len(actor) > 128:
        raise ValueError("arbitrator_id 必须为 1 到 128 个字符")
    meta = _validate_attestation_meta(attestation_meta)

    # generic 记录：与 append_decision_arbitration 同一公式/同一幂等键/同一
    # supersedes 语义；借用连接不改 row_factory，故用显式列按位读取。
    normalized, _ = _validated_merge(merged)
    source_records = json.loads(_canonical_json(normalized.to_dict("records")))
    resolved = resolve_decision_conflicts(normalized, resolutions)
    records = json.loads(_canonical_json(resolved.to_dict("records")))
    merge_sha = _sha256({"stage": stage, "records": source_records})
    result_sha = _sha256({"stage": stage, "merge_sha256": merge_sha,
                          "arbitrator_id": actor, "records": records})

    select_generic = (
        "SELECT id,stage,merge_sha256,result_sha256,supersedes_id,arbitrator_id,"
        "records_json,created_at FROM screening_arbitrations "
        "WHERE stage=? AND merge_sha256=? AND result_sha256=?"
    )
    existing = conn.execute(select_generic, (stage, merge_sha, result_sha)).fetchone()
    if existing is None:
        previous = conn.execute(
            "SELECT id FROM screening_arbitrations WHERE stage=? AND merge_sha256=? "
            "ORDER BY id DESC LIMIT 1",
            (stage, merge_sha),
        ).fetchone()
        created_at = datetime.now(timezone.utc).isoformat(
            timespec="milliseconds").replace("+00:00", "Z")
        cursor = conn.execute(
            "INSERT INTO screening_arbitrations "
            "(stage,merge_sha256,result_sha256,supersedes_id,arbitrator_id,records_json,created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (stage, merge_sha, result_sha, previous[0] if previous else None,
             actor, _canonical_json(records), created_at),
        )
        existing = conn.execute(
            "SELECT id,stage,merge_sha256,result_sha256,supersedes_id,arbitrator_id,"
            "records_json,created_at FROM screening_arbitrations WHERE id=?",
            (cursor.lastrowid,),
        ).fetchone()
    record = _generic_record_from_row(tuple(existing))

    # resolutions 摘要：经同一两道闸规范化后按 zotero_key 排序的确定性摘要
    # （同输入同结论恒同值；与 records 存储解耦，改票改回不误命中）。
    digest_entries = []
    for item in resolutions:
        key = item.get("zotero_key")
        decision, rationale = validate_arbitration_decision(
            item.get("decision"), item.get("rationale", ""), zotero_key=key)
        digest_entries.append(
            {"zotero_key": key, "decision": decision, "rationale": rationale})
    digest_entries.sort(key=lambda entry: entry["zotero_key"])
    resolutions_digest = _sha256(digest_entries)

    attest_sha = _sha256({
        "task_id": meta["task_id"],
        "round_id": meta["round_id"],
        "stage": stage,
        "record_id": record["id"],
        "arbitrator_user_id": meta["arbitrator_user_id"],
        "required_names": meta["required_names"],
        "input_proof": meta["input_proof"],
        "resolutions_digest": resolutions_digest,
    })

    select_attestation = (
        "SELECT id,record_id,task_id,round_id,stage,arbitrator_user_id,arbitrator_role,"
        "required_names_json,input_proof_json,resolutions_digest,attest_sha256,attested_at "
        "FROM screening_arbitration_attestations WHERE attest_sha256=?"
    )
    attestation_row = conn.execute(select_attestation, (attest_sha,)).fetchone()
    if attestation_row is None:
        # 真实时间只在确有新 attestation 要落时取（幂等命中不取新时间、不伪时间）。
        attested_at = datetime.now(timezone.utc).isoformat(
            timespec="milliseconds").replace("+00:00", "Z")
        cursor = conn.execute(
            "INSERT INTO screening_arbitration_attestations "
            "(record_id,task_id,round_id,stage,arbitrator_user_id,arbitrator_role,"
            "required_names_json,input_proof_json,resolutions_digest,attest_sha256,attested_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (record["id"], meta["task_id"], meta["round_id"], stage,
             meta["arbitrator_user_id"], meta["arbitrator_role"],
             _canonical_json(meta["required_names"]),
             _canonical_json(meta["input_proof"]),
             resolutions_digest, attest_sha, attested_at),
        )
        attestation_row = conn.execute(
            "SELECT id,record_id,task_id,round_id,stage,arbitrator_user_id,arbitrator_role,"
            "required_names_json,input_proof_json,resolutions_digest,attest_sha256,attested_at "
            "FROM screening_arbitration_attestations WHERE id=?",
            (cursor.lastrowid,),
        ).fetchone()
    attestation = _attestation_from_row(tuple(attestation_row))
    # 双 ID 明确分列：generic 详情/URL 只能用 record_id；核心消费的追加序用
    # attestation_id（二者不得混用）。
    return {
        "record": record,
        "attestation": attestation,
        "record_id": record["id"],
        "attestation_id": attestation["id"],
    }


def _attestation_store_readable(path: Path) -> bool:
    """只读前置：真实 ``FileNotFoundError`` → 尚无 attestation 存储（返回
    False，调用方返回空且零创建）；stat 成功但**非普通文件**（真实目录等）
    与 stat 级 IO/权限故障一律 :class:`ArbitrationAttestationStoreUnavailable`
    ——错误路径不得伪成「无记录」。绝不创建任何文件。"""
    try:
        entry = path.stat()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise ArbitrationAttestationStoreUnavailable(
            f"arbitration attestation store is temporarily unreadable: {path.name}") from exc
    if not stat.S_ISREG(entry.st_mode):
        raise ArbitrationAttestationStoreUnavailable(
            f"arbitration attestation store is not a regular file: {path.name}")
    return True


def _read_attestations_on_conn(
    conn: sqlite3.Connection, where_sql: str, params: tuple,
) -> list[dict[str, Any]] | None:
    """在**调用方连接**上单快照读取 attestation 及其引用的 generic 全量记录
    （S1c seam：path 版 :func:`_read_attestations_ro` 与公开
    :func:`list_decision_arbitration_attestations_on` 的共用读取体——零第二
    实现，持久 JSON 解码与孤儿错误语义与 S1b 完全一致）。

    借用契约（S1c ruling §5）：
    - caller 已有事务 → 零 BEGIN/COMMIT/ROLLBACK/CLOSE，可读其**未提交**的
      attestation 与 generic 来源行；
    - 无事务 → 自开 ``BEGIN DEFERRED``，finally 只 rollback 自有读事务、
      **连接保持打开**；
    - 不 ``with conn``、不改 row_factory/PRAGMA（默认 tuple 与 Row 均可——
      解码全按 ``_ATTESTATION_COLUMNS``/``_GENERIC_RECORD_COLUMNS`` 位置）。

    同一 snapshot **先查 ``sqlite_master``**：仅明确无
    ``screening_arbitration_attestations`` 表 → ``None``（未 prepare 的合法
    空）；表在而缺列/缺 generic 表/孤儿/busy/IO →
    :class:`ArbitrationAttestationStoreUnavailable`；
    ``ProgrammingError`` 透传。**禁止泛 no-such-table → 空**。
    """
    if not isinstance(conn, sqlite3.Connection):
        raise ArbitrationConnectionContractError(
            "attestation 读取需要既有 sqlite3.Connection（借用连接，不自行打开）。")
    select_attestations = (
        "SELECT id,record_id,task_id,round_id,stage,arbitrator_user_id,arbitrator_role,"
        "required_names_json,input_proof_json,resolutions_digest,attest_sha256,attested_at "
        f"FROM screening_arbitration_attestations {where_sql} ORDER BY id"
    )
    select_generic = (
        "SELECT id,stage,merge_sha256,result_sha256,supersedes_id,arbitrator_id,"
        "records_json,created_at FROM screening_arbitrations WHERE id=?"
    )
    own_txn = not conn.in_transaction
    try:
        if own_txn:
            conn.execute("BEGIN DEFERRED")
        has_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='screening_arbitration_attestations'").fetchone()
        if has_table is None:
            return None  # 尚未 prepare：合法「无 attestation」。
        # FIX-R1 F7：attestations 表存在后，同一 snapshot 内确认 generic 表
        # 存在且两表列集完整（复用 decoder 列常量；PRAGMA 只读、零 DDL）。
        # 空 attest 表而缺 generic 表/缺列＝存储不完整，不得伪成「无证明」。
        has_generic = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='screening_arbitrations'").fetchone()
        if has_generic is None:
            raise ArbitrationAttestationStoreUnavailable(
                "arbitration attestation store is missing the generic "
                "screening_arbitrations table while attestations exist")
        attest_columns = {row[1] for row in conn.execute(
            "PRAGMA table_info(screening_arbitration_attestations)")}
        missing_attest = [c for c in _ATTESTATION_COLUMNS if c not in attest_columns]
        if missing_attest:
            raise ArbitrationAttestationStoreUnavailable(
                "screening_arbitration_attestations is missing columns: "
                + ",".join(missing_attest))
        generic_columns = {row[1] for row in conn.execute(
            "PRAGMA table_info(screening_arbitrations)")}
        missing_generic = [c for c in _GENERIC_RECORD_COLUMNS if c not in generic_columns]
        if missing_generic:
            raise ArbitrationAttestationStoreUnavailable(
                "screening_arbitrations is missing columns: "
                + ",".join(missing_generic))
        attestation_rows = conn.execute(select_attestations, params).fetchall()
        records: dict[int, dict[str, Any]] = {}
        for row in attestation_rows:
            record_id = int(row[1])
            if record_id not in records:
                generic_row = conn.execute(select_generic, (record_id,)).fetchone()
                if generic_row is None:
                    raise ArbitrationAttestationStoreUnavailable(
                        "arbitration attestation store is missing the referenced "
                        f"generic record {record_id} for attestation {int(row[0])}")
                records[record_id] = _generic_record_from_row(tuple(generic_row))
    except sqlite3.ProgrammingError:
        raise
    except sqlite3.Error as exc:
        raise ArbitrationAttestationStoreUnavailable(
            "arbitration attestation store cannot be read on the caller "
            f"connection: {exc}") from exc
    finally:
        if own_txn and conn.in_transaction:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass  # 连接已坏时尽力收尾自有读事务；原始异常优先上抛。
    return [
        {
            "attestation": _attestation_from_row(tuple(row)),
            "record": records[int(row[1])],
            "attestation_id": int(row[0]),
            "record_id": int(row[1]),
        }
        for row in attestation_rows
    ]


def list_decision_arbitration_attestations_on(
    conn: sqlite3.Connection,
    *,
    stage: str | None = None,
    task_id: str | None = None,
    record_id: int | None = None,
) -> list[dict[str, Any]]:
    """在调用方连接上只读列出 attestation（可按 stage/task_id/record_id 精确
    过滤），零 DDL／零建库／零自开连接（S1c seam）。

    返回结构与 :func:`list_decision_arbitration_attestations` 逐字段一致
    （``{'attestation':…, 'record':…, 'attestation_id': int, 'record_id': int}``，
    按 attestation id 升序）；**caller 事务内可读其未提交行**（供 S2 写窗口
    消费同事务新证明）。尚无 attestation 表 → ``[]``（未 prepare 的合法空）；
    表在而缺列/缺 generic 表/孤儿/坏 JSON/busy/IO →
    :class:`ArbitrationAttestationStoreUnavailable`；``ProgrammingError`` 透传；
    过滤参数非法 → ``ValueError``（校验与 path 版逐字同款，不改动已 PASS 的
    path 公共函数）。逐 key 适用性裁剪归 S1c 消费方，本层不整记录过滤。
    """
    if not isinstance(conn, sqlite3.Connection):
        raise ArbitrationConnectionContractError(
            "list_decision_arbitration_attestations_on 需要既有协作库连接"
            "（sqlite3.Connection，借用连接，不自行打开）。")
    clauses: list[str] = []
    params: list[Any] = []
    if stage is not None:
        if stage not in {"stage1", "stage2"}:
            raise ValueError("stage 必须为 stage1 或 stage2")
        clauses.append("stage=?")
        params.append(stage)
    if task_id is not None:
        if not isinstance(task_id, str) or not task_id:
            raise ValueError("task_id 过滤必须为非空文本")
        clauses.append("task_id=?")
        params.append(task_id)
    if record_id is not None:
        if not isinstance(record_id, int) or isinstance(record_id, bool) or record_id < 1:
            raise ValueError("record_id 必须为正整数")
        clauses.append("record_id=?")
        params.append(record_id)
    where_sql = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    found = _read_attestations_on_conn(conn, where_sql, tuple(params))
    return found if found else []


def _read_attestations_ro(
    path: Path, where_sql: str, params: tuple,
) -> list[dict[str, Any]] | None:
    """开 mode=ro 只读连接后委托 :func:`_read_attestations_on_conn`（S1c 起
    为共用读取体的 path 包装；对外行为与 S1b path 版逐字段一致：单 DEFERRED
    快照、同 JSON/孤儿/损坏错误语义、绝不建库/建表/迁移）。

    ``where_sql`` 只由本模块内的固定列名拼接（值全部参数化）。**尚无
    attestation 表 → None**（未 prepare 的合法状态）；**本次查询命中的
    attestation 缺其 generic 来源行** →
    :class:`ArbitrationAttestationStoreUnavailable`；``ProgrammingError`` 原样
    上抛。逐 key 适用性裁剪归 S1c 消费方。
    """
    try:
        uri = path.resolve().as_uri() + "?mode=ro"
    except OSError as exc:
        raise ArbitrationAttestationStoreUnavailable(
            f"arbitration attestation store path is temporarily unreadable: {path.name}") from exc
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=SCREENER_READ_TIMEOUT)
    except sqlite3.Error as exc:
        # 连接阶段失败（现存文件打不开/IO）＝暂时不可读，不裸抛底层英文错误
        # （与 S1b path 版口径一致：缺文件/未建表由 readable 预检与 sqlite_master
        # 判定，此处只剩真故障）。
        raise ArbitrationAttestationStoreUnavailable(
            f"arbitration attestation store cannot be read: {path.name}") from exc
    with closing(conn):
        return _read_attestations_on_conn(conn, where_sql, params)


def get_decision_arbitration_attestation(
    store_path: StrPath, attestation_id: int,
) -> dict[str, Any] | None:
    """只读读取一条 attestation 及其引用的 generic 全量记录（零 DDL/零建库）。

    缺文件/尚无 attestation 表/无该 ID → ``None``（绝不创建库或表）；
    ``attestation_id`` 非正整数 → ``ValueError``；协作库损坏/锁忙/IO/权限故
    障、路径非普通文件、持久 JSON 解码失败、**命中 attestation 却缺其
    generic 来源行** → :class:`ArbitrationAttestationStoreUnavailable`（503 语
    义，不伪「无仲裁」，也不返回 ``record=None``）；``ProgrammingError``（调
    用写法缺陷）原样上抛。
    """
    if not isinstance(attestation_id, int) or isinstance(attestation_id, bool) or attestation_id < 1:
        raise ValueError("attestation_id 必须为正整数")
    path = Path(store_path)
    if not _attestation_store_readable(path):
        return None
    found = _read_attestations_ro(path, "WHERE id=?", (attestation_id,))
    return found[0] if found else None


def list_decision_arbitration_attestations(
    store_path: StrPath,
    *,
    stage: str | None = None,
    task_id: str | None = None,
    record_id: int | None = None,
) -> list[dict[str, Any]]:
    """只读列出 attestation（可按 stage/task_id/record_id 精确过滤），零 DDL。

    返回完整来源数据（attestation ＋ 引用 generic 记录全量 rows，按
    attestation id 升序）：逐 key 适用性裁剪归 S1c 消费方，本层不整记录过
    滤。缺文件/尚无表 → ``[]``；损坏/锁忙/IO、路径非普通文件、持久 JSON
    解码失败、**命中 attestation 却缺其 generic 来源行** →
    :class:`ArbitrationAttestationStoreUnavailable`；过滤参数非法 →
    ``ValueError``。未被过滤条件匹配的健康记录不受个别孤儿/损坏行影响。
    """
    clauses: list[str] = []
    params: list[Any] = []
    if stage is not None:
        if stage not in {"stage1", "stage2"}:
            raise ValueError("stage 必须为 stage1 或 stage2")
        clauses.append("stage=?")
        params.append(stage)
    if task_id is not None:
        if not isinstance(task_id, str) or not task_id:
            raise ValueError("task_id 过滤必须为非空文本")
        clauses.append("task_id=?")
        params.append(task_id)
    if record_id is not None:
        if not isinstance(record_id, int) or isinstance(record_id, bool) or record_id < 1:
            raise ValueError("record_id 必须为正整数")
        clauses.append("record_id=?")
        params.append(record_id)
    where_sql = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    path = Path(store_path)
    if not _attestation_store_readable(path):
        return []
    found = _read_attestations_ro(path, where_sql, tuple(params))
    return found if found else []


# ---------------------------------------------------------------------------
# P05（2.1.6 team flow，合同 C06）：仲裁按键证据读取——纯新增，generic 语义零改动。
#
# 既有 generic append/latest/get/list、S1b attestation 追加与 S1c 借用连接读取
# 全部保持字节不变；本节只新增 :class:`KeyedAttestations` 与借用连接的按键证据
# 读取 :func:`read_attestations_for_keys_on`。SQL 只按可信 header（task/stage/
# round）有界筛选并按记录 ID 分页；完整 records 键域 membership、按 proof 证明
# 与时间全部在 Python 侧逐记录解码——不用裸 json_each、不依赖 JSON1，也不以
# stage-global merge SHA 判定按键命中（merge SHA 只作为记录数据原样带回）。
# ---------------------------------------------------------------------------

from dataclasses import dataclass
from typing import Mapping

#: 按键证据读取的 SQL 分页上界（每块至多 200 行，与 C02 有界读取口径一致）。
_KEYED_ATTESTATION_READ_PAGE = 200


@dataclass(frozen=True)
class KeyedAttestations:
    """按键仲裁证据读取结果（合同 C06 冻结形状）。

    ``by_key``：请求键 → 命中的证据条目元组（按 attestation id 升序；每条含
    完整 attestation/record、双 ID、该键裁决与按 proof 节选）；无命中的键不在
    其中。``issues``：请求键 → 首个损害原因（同键多记录损害时按 id 升序取首
    因）；已被有效命中的键不再列入（调用方视为已有证据，不属于「仍需仲裁」）。
    """

    by_key: Mapping[str, tuple[Mapping[str, object], ...]]
    issues: Mapping[str, str]


def _require_keyed_read_connection(conn: Any, *, api: str) -> None:
    """C02 新 ``*_on`` 借用连接门：真实 native :class:`sqlite3.Connection`（或
    真实子类，用 ``type`` 继承判断、拒 ``__class__`` 伪装代理）＋调用方活动事务
    ＋ ``row_factory is sqlite3.Row``。

    违约抛 :class:`ArbitrationConnectionContractError`（编程错误，不降级、不暗
    开事务）；**closed 连接的** ``sqlite3.ProgrammingError`` 不经本门捕获、原样
    透传（属性读取在类型判断之后，closed 连接触发底层 ProgrammingError 而非
    合同错误）。本门零 SQL、零 PRAGMA、零事务动作。
    """
    if not issubclass(type(conn), sqlite3.Connection):
        raise ArbitrationConnectionContractError(
            f"{api} 需要既有协作库原生连接（sqlite3.Connection 或真实子类，"
            "借用连接，不自行打开）。")
    if not conn.in_transaction:
        raise ArbitrationConnectionContractError(
            f"{api} 需要调用方已开启活动事务（本 API 不 BEGIN/commit/rollback/"
            "close，也不改 PRAGMA/row_factory）。")
    if conn.row_factory is not sqlite3.Row:
        raise ArbitrationConnectionContractError(
            f"{api} 需要调用方连接 row_factory 为 sqlite3.Row。")


def _fetch_rows_closed(conn: sqlite3.Connection, sql: str, params: tuple) -> list:
    """单条有界 SELECT：完整物化并确定关闭自建 Cursor（C02）。

    读取主错误优先原样上抛（连接已坏时尽力收尾自建 Cursor，不吞主错）；成功路
    径的关闭失败同样上抛（不吞清理错）。``sql`` 只由本模块固定字面量传入，值全
    部参数绑定。
    """
    cursor = conn.execute(sql, params)
    try:
        rows = cursor.fetchall()
    except BaseException:
        try:
            cursor.close()
        except sqlite3.Error:
            pass  # 主错误优先；连接已坏时尽力收尾自建 Cursor。
        raise
    cursor.close()
    return rows


def _note_keyed_issues(issues: dict[str, str], keys: Iterable[str], reason: str) -> None:
    """按首因留底记录按键 issue（同键多处损害取 id 升序首个原因）。"""
    for key in keys:
        issues.setdefault(key, reason)


def _keyed_attestation_time_ok(value: Any) -> bool:
    """attested_at/created_at 时间核验：非空文本且可 ISO 解析。"""
    if not isinstance(value, str) or not value:
        return False
    try:
        datetime.fromisoformat(value)
    except ValueError:
        return False
    return True


def _keyed_attestation_digest_ok(value: Any) -> bool:
    """attest_sha256/resolutions_digest 核验：恰 64 位小写十六进制文本。"""
    return (isinstance(value, str) and len(value) == 64
            and all(ch in _HEX32 for ch in value))


def _keyed_attestation_proof_entry_ok(entry: Any) -> bool:
    """按键 input_proof 条目形状核验（镜像 :func:`_validate_attestation_meta`
    的 proof 规则但只判形状不抛错）：非空对象、user_id 文本键 → 恰含
    ``library_id``（32 位小写 hex）与 ``revision``（正整数非布尔）。"""
    if not isinstance(entry, dict) or not entry:
        return False
    for user_key, user_entry in entry.items():
        if not isinstance(user_key, str) or not user_key:
            return False
        if not isinstance(user_entry, dict) or set(user_entry) != {"library_id", "revision"}:
            return False
        library_id = user_entry["library_id"]
        revision = user_entry["revision"]
        if not isinstance(library_id, str) or not _is_hex32(library_id):
            return False
        if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
            return False
    return True


def _keyed_attestation_store_present(conn: sqlite3.Connection) -> bool:
    """按键读取的存储前置：尚无 attestation 表 → ``False``（未 prepare 的合法
    空）；表在而缺 generic 表/任一侧缺列 →
    :class:`ArbitrationAttestationStoreUnavailable`（存储不完整不得伪成「无证
    据」）。列集校验复用 S1b/S1c 的 ``_ATTESTATION_COLUMNS``/
    ``_GENERIC_RECORD_COLUMNS`` 冻结常量；PRAGMA 只读、零 DDL。"""
    attest_table = _fetch_rows_closed(
        conn,
        "SELECT 1 FROM sqlite_master WHERE type='table' "
        "AND name='screening_arbitration_attestations'",
        (),
    )
    if not attest_table:
        return False
    generic_table = _fetch_rows_closed(
        conn,
        "SELECT 1 FROM sqlite_master WHERE type='table' "
        "AND name='screening_arbitrations'",
        (),
    )
    if not generic_table:
        raise ArbitrationAttestationStoreUnavailable(
            "arbitration attestation store is missing the generic "
            "screening_arbitrations table while attestations exist")
    attest_columns = {row[1] for row in _fetch_rows_closed(
        conn, "PRAGMA table_info(screening_arbitration_attestations)", ())}
    missing_attest = [c for c in _ATTESTATION_COLUMNS if c not in attest_columns]
    if missing_attest:
        raise ArbitrationAttestationStoreUnavailable(
            "screening_arbitration_attestations is missing columns: "
            + ",".join(missing_attest))
    generic_columns = {row[1] for row in _fetch_rows_closed(
        conn, "PRAGMA table_info(screening_arbitrations)", ())}
    missing_generic = [c for c in _GENERIC_RECORD_COLUMNS if c not in generic_columns]
    if missing_generic:
        raise ArbitrationAttestationStoreUnavailable(
            "screening_arbitrations is missing columns: " + ",".join(missing_generic))
    return True


def read_attestations_for_keys_on(
    conn: sqlite3.Connection,
    *,
    task_id: str,
    stage: str,
    round_id: str | None,
    item_keys: Iterable[str],
) -> KeyedAttestations:
    """在调用方借用连接上按请求键读取仲裁 attestation 证据（合同 C06；零 DDL、
    零建库、零自开连接、零事务动作）。

    调用方契约（C02 统一口径）：native :class:`sqlite3.Connection`（或真实子
    类）＋活动事务＋``row_factory is sqlite3.Row``（违约抛
    :class:`ArbitrationConnectionContractError`；closed 连接的
    ``ProgrammingError`` 原样透传）。``item_keys`` 经
    :func:`coscreen.team_flow_types.normalize_item_keys` 规范化（拒 bare
    str/bytes/空串/NUL/非法 Unicode，去重保序，空集合拒绝）；**已封存/当前一
    致的键不应进入本请求**（调用方职责，本 API 不复核）。

    读取语义（C06）：
    - 先按可信 SQL header（task_id/stage/round_id）有界筛选，再按 attestation
      id 升序、每块至多 ``_KEYED_ATTESTATION_READ_PAGE`` 行分页；不做全域读取
      后过滤，不用裸 json_each、不依赖 JSON1。
    - 逐记录在 Python 侧解码**完整 records 键域**定 membership；命中键（该键
      在 records 中 ``arbitration_status`` 为 ``adjudicated``）再核完整
      generic＋attestation（required_names/双摘要/行结构）、attested_at 与
      created_at 时间、以及该键 input_proof 条目——全部通过才计入
      ``by_key``。不以 stage-global merge SHA 判定命中。
    - 损害隔离：membership 已知时，记录级损害（时间/摘要/required_names/
      proof 载体/行结构）只影响「请求键 ∩ 该记录键域」；**可证明单键局部的损
      害**（该键 proof 缺失/坏形状、裁决行坏值）只记该键 issue 并跳过该键的
      其余核验；membership 未知（孤儿 attestation、records JSON 无效/键域含
      空键/重复键）时无法定位，影响该请求中仍需仲裁的全部键。已获有效命中的
      键不再列入 issues。
    - 尚无 attestation 表 → 空结果（未 prepare 的合法空）；表在而存储不完整
      → :class:`ArbitrationAttestationStoreUnavailable`；锁忙/IO/页损坏同样
      typed 上抛（绝不把「读不到」伪成「无证据」）。
    """
    # 局部导入（P05 产品决策，2026-11-18）：normalize_item_keys 的共同归宿是
    # coscreen.team_flow_types（C02「不依赖 backend 模块」）；其 re-export 尚
    # 未落入当前基线时，仅本 API 报 ImportError，不影响模块其余部分的导入。
    from coscreen.team_flow_types import normalize_item_keys

    requested = list(normalize_item_keys(item_keys))
    _require_keyed_read_connection(conn, api="read_attestations_for_keys_on")
    if not isinstance(task_id, str) or not task_id:
        raise ValueError("task_id 必须为非空文本")
    if not isinstance(stage, str) or stage not in {"stage1", "stage2"}:
        raise ValueError("stage 必须为 stage1 或 stage2")
    if round_id is not None and (not isinstance(round_id, str) or not round_id):
        raise ValueError("round_id 必须显式为 None 或非空文本")

    by_key: dict[str, list[dict[str, Any]]] = {key: [] for key in requested}
    issues: dict[str, str] = {}
    round_clause = "round_id IS NULL" if round_id is None else "round_id=?"
    header_params: tuple[Any, ...] = (task_id, stage) + (
        (round_id,) if round_id is not None else ())
    page_sql = (
        "SELECT id,record_id,task_id,round_id,stage,arbitrator_user_id,arbitrator_role,"
        "required_names_json,input_proof_json,resolutions_digest,attest_sha256,attested_at "
        "FROM screening_arbitration_attestations "
        f"WHERE task_id=? AND stage=? AND {round_clause} AND id>? "
        "ORDER BY id LIMIT ?"
    )
    select_generic = (
        "SELECT id,stage,merge_sha256,result_sha256,supersedes_id,arbitrator_id,"
        "records_json,created_at FROM screening_arbitrations WHERE id=?"
    )
    try:
        if not _keyed_attestation_store_present(conn):
            return KeyedAttestations(by_key={}, issues={})
        last_id = 0
        while True:
            page = _fetch_rows_closed(
                conn, page_sql,
                header_params + (last_id, _KEYED_ATTESTATION_READ_PAGE))
            if not page:
                break
            for attest_row in page:
                last_id = int(attest_row["id"])
                generic_rows = _fetch_rows_closed(
                    conn, select_generic, (attest_row["record_id"],))
                if not generic_rows:
                    # 孤儿 attestation：membership 未知 → 影响全部仍需仲裁键。
                    _note_keyed_issues(issues, requested, "attestation_orphan")
                    continue
                generic_row = generic_rows[0]
                try:
                    records_value = json.loads(generic_row["records_json"])
                except (json.JSONDecodeError, TypeError, UnicodeDecodeError):
                    _note_keyed_issues(issues, requested, "records_unreadable")
                    continue
                if not isinstance(records_value, list) or any(
                        not isinstance(item, dict) for item in records_value):
                    _note_keyed_issues(issues, requested, "records_unreadable")
                    continue
                domain: dict[str, dict[str, Any]] = {}
                domain_ok = True
                for item in records_value:
                    item_key = item.get("zotero_key")
                    if not isinstance(item_key, str) or not item_key or item_key in domain:
                        domain_ok = False
                        break
                    domain[item_key] = item
                if not domain_ok:
                    # 键域含空键/非文本/重复键 → membership 不可信（未知）。
                    _note_keyed_issues(issues, requested, "records_unreadable")
                    continue
                affected = [key for key in requested if key in domain]
                if attest_row["stage"] != generic_row["stage"]:
                    _note_keyed_issues(issues, affected, "stage_mismatch")
                    continue
                try:
                    required_names_value = json.loads(attest_row["required_names_json"])
                except (json.JSONDecodeError, TypeError, UnicodeDecodeError):
                    _note_keyed_issues(issues, affected, "required_names_invalid")
                    continue
                if (not isinstance(required_names_value, list)
                        or len(required_names_value) < 2
                        or any(not isinstance(name, str) or not name or len(name) > 100
                               for name in required_names_value)
                        or len(set(required_names_value)) != len(required_names_value)):
                    _note_keyed_issues(issues, affected, "required_names_invalid")
                    continue
                if (not _keyed_attestation_digest_ok(attest_row["attest_sha256"])
                        or not _keyed_attestation_digest_ok(
                            attest_row["resolutions_digest"])):
                    _note_keyed_issues(issues, affected, "attestation_digest_invalid")
                    continue
                if not _keyed_attestation_time_ok(attest_row["attested_at"]):
                    _note_keyed_issues(issues, affected, "attested_at_invalid")
                    continue
                if not _keyed_attestation_time_ok(generic_row["created_at"]):
                    _note_keyed_issues(issues, affected, "created_at_invalid")
                    continue
                try:
                    proof_value = json.loads(attest_row["input_proof_json"])
                except (json.JSONDecodeError, TypeError, UnicodeDecodeError):
                    _note_keyed_issues(issues, affected, "input_proof_invalid")
                    continue
                if not isinstance(proof_value, dict):
                    _note_keyed_issues(issues, affected, "input_proof_invalid")
                    continue
                try:
                    attestation = {
                        "id": int(attest_row["id"]),
                        "record_id": int(attest_row["record_id"]),
                        "task_id": attest_row["task_id"],
                        "round_id": attest_row["round_id"],
                        "stage": attest_row["stage"],
                        "arbitrator_user_id": int(attest_row["arbitrator_user_id"]),
                        "arbitrator_role": attest_row["arbitrator_role"],
                        "required_names": list(required_names_value),
                        "input_proof": proof_value,
                        "resolutions_digest": attest_row["resolutions_digest"],
                        "attest_sha256": attest_row["attest_sha256"],
                        "attested_at": attest_row["attested_at"],
                    }
                except (TypeError, ValueError):
                    _note_keyed_issues(issues, affected, "attestation_row_invalid")
                    continue
                try:
                    record = {
                        "id": int(generic_row["id"]),
                        "stage": generic_row["stage"],
                        "merge_sha256": generic_row["merge_sha256"],
                        "result_sha256": generic_row["result_sha256"],
                        "supersedes_id": generic_row["supersedes_id"],
                        "arbitrator_id": generic_row["arbitrator_id"],
                        "created_at": generic_row["created_at"],
                        "records": records_value,
                    }
                except (TypeError, ValueError):
                    _note_keyed_issues(issues, affected, "record_row_invalid")
                    continue
                for key in affected:
                    row_value = domain[key]
                    if row_value.get("arbitration_status") != "adjudicated":
                        continue  # agreed/unresolved：本记录不为该键提供仲裁证据。
                    decision = row_value.get("final_decision")
                    if decision not in ARBITRATION_DECISIONS:
                        # 可证明单键局部损害：只记该键，不连坐同记录其他键。
                        _note_keyed_issues(issues, [key], "adjudication_row_invalid")
                        continue
                    if key not in proof_value:
                        _note_keyed_issues(issues, [key], "proof_missing")
                        continue
                    if not _keyed_attestation_proof_entry_ok(proof_value[key]):
                        _note_keyed_issues(issues, [key], "proof_invalid")
                        continue
                    by_key[key].append({
                        "attestation": attestation,
                        "record": record,
                        "attestation_id": attestation["id"],
                        "record_id": record["id"],
                        "item_key": key,
                        "decision": decision,
                        "rationale": row_value.get("arbitration_reason", ""),
                        "proof": proof_value[key],
                    })
            if len(page) < _KEYED_ATTESTATION_READ_PAGE:
                break
    except sqlite3.ProgrammingError:
        raise
    except sqlite3.Error as exc:
        raise ArbitrationAttestationStoreUnavailable(
            "arbitration attestation store cannot be read on the caller "
            f"connection: {exc}") from exc
    return KeyedAttestations(
        by_key={key: tuple(entries) for key, entries in by_key.items() if entries},
        issues={key: reason for key, reason in issues.items() if not by_key[key]},
    )
