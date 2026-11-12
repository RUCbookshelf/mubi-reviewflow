"""Append-only resolution of conflicts from independent screening exports.

This operates on the merged snapshot and a task-scoped SQLite file. It never
writes back to any screener's personal ``decisions`` or ``stage2_decisions``.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
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
        if "unable to open" in message and path.is_file():
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
