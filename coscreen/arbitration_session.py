"""逐条盲裁决会话：冲突集的存储、逐条裁决、先判后示与完成快照。

产品设计定稿（2026-11-08，七条）在本模块的落点：

1. **先判后示** —— :func:`open_session` 重建的队列只含文献元数据与
   ``pending`` / ``decided`` 状态；队列载荷绝不包含任何筛选员姓名或单篇
   决定。:func:`decide` 之后才通过 reveal 返回该条的原始投票。
2. **仲裁不提供「暂不确定」** —— 逐条裁决复用 ``screening_arbitration``
   的 r18 两道闸（:func:`coscreen.screening_arbitration.validate_arbitration_decision`）：
   二选一（include/exclude）+ 排除必须附非空理由；拒绝时不落任何裁决。
3. **理由同盲** —— 裁决理由与裁决值同等对待：完成前只回给裁决者本人
   （reveal 载荷），不进入队列，也不回显给任何筛选员。
4. **κ 留汇总层** —— 一致率 / Light's κ / 两两 κ 只在会话汇总里以**匿名
   配对标签**（``pair_1``…）出现，不指向任何个人，也不逐篇展开。
5. **档案保全员决定** —— 完成时把会话快照交给既有
   :func:`coscreen.screening_arbitration.append_decision_arbitration`，
   记录格式与 r12/r18 完全相同（含全员原始决策列），本模块不另造格式。
6. **stage2 连全文** —— ``stage2`` 队列条目额外带 ``fulltext_available``，
   按**当前裁决者**的个人库 ``fulltexts`` 表（且 PDF 文件仍存在）判定；
   这与既有 ``GET /api/tasks/{id}/fulltext/{key}``（默认读当前用户库）的
   可见性一致，前端据该标记决定是否渲染「打开全文」。
7. **断点续作** —— ``open_session`` 可重复调用：已裁决条目与理由原样保留，
   只按当前冲突集刷新元数据与队列；已完成会话冻结返回，绝不重置。

存储（挂任务协作库，全部 ``CREATE TABLE IF NOT EXISTS``，幂等；旧版代码
不读取这两张表，自然忽略）：

- ``arbitration_session``：``(task_id, stage, arbitrator_id)`` 单例，
  ``status`` / ``summary_json`` / ``snapshot_id`` / 时间戳；
- ``arbitration_decision``：``(task_id, stage, arbitrator_id, zotero_key)``
  主键，``decision`` / ``reason`` / ``decided_at`` / ``revealed`` 以及仅供
  队列与 reveal 使用的 ``metadata_json`` / ``votes_json``。

铁律：本模块只写任务协作库（``store_path``），对筛选员个人库一律
``mode=ro`` 只读；SQL 全部为模块内固定字面量 + ``?`` 参数绑定（无任何
拼接/format/f-string），失败优雅降级为结构化错误（HTTP 层映射 4xx/503）。
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from coscreen.screening_arbitration import (
    SCREENER_READ_TIMEOUT,
    append_decision_arbitration,
    get_decision_arbitration,
    load_screener_decision_packages,
    merge_decision_packages,
    validate_arbitration_decision,
)
from coscreen.stats import light_kappa, pairwise_kappa, summarize_multi

StrPath = str | Path

STAGES = ("stage1", "stage2")
SESSION_OPEN = "open"
SESSION_COMPLETED = "completed"

#: 会话写在任务协作库上的 busy 超时（秒）。
STORE_TIMEOUT = 15

MAX_TASK_ID = 200
MAX_ACTOR = 128
MAX_KEY = 300

#: SQLite 只读 URI 的固定查询后缀（连接参数，不是 SQL 文本）。
_READ_ONLY_QUERY = "?mode=ro"

_SESSION_SCHEMA = """
CREATE TABLE IF NOT EXISTS arbitration_session (
  task_id TEXT NOT NULL,
  stage TEXT NOT NULL CHECK(stage IN ('stage1', 'stage2')),
  arbitrator_id TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('open', 'completed')),
  summary_json TEXT NOT NULL DEFAULT '{}',
  snapshot_id INTEGER,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  completed_at TEXT,
  PRIMARY KEY (task_id, stage, arbitrator_id)
);
CREATE TABLE IF NOT EXISTS arbitration_decision (
  task_id TEXT NOT NULL,
  stage TEXT NOT NULL CHECK(stage IN ('stage1', 'stage2')),
  arbitrator_id TEXT NOT NULL,
  zotero_key TEXT NOT NULL,
  decision TEXT CHECK(decision IS NULL OR decision IN ('include', 'exclude')),
  reason TEXT NOT NULL DEFAULT '',
  decided_at TEXT,
  revealed INTEGER NOT NULL DEFAULT 0 CHECK(revealed IN (0, 1)),
  metadata_json TEXT NOT NULL DEFAULT '{}',
  votes_json TEXT NOT NULL DEFAULT '[]',
  PRIMARY KEY (task_id, stage, arbitrator_id, zotero_key)
);
"""

#: 文献元数据联查（任务文献目录 = 各筛选员库里的 ``articles`` 表；
#: 固定字面量 + 绑定键，绝不拼装）。旧库可能没有 ``import_order`` 列，
#: 用 PRAGMA 探测后走无该列的固定字面量变体。
_ARTICLE_SQL = (
    "SELECT title, journal, year, doi, abstract, import_order "
    "FROM articles WHERE zotero_key=?"
)
_ARTICLE_SQL_LEGACY = (
    "SELECT title, journal, year, doi, abstract FROM articles WHERE zotero_key=?"
)


# ---------------------------------------------------------------------------
# 结构化错误（HTTP 层按 code/message_key/params 原样输出）
# ---------------------------------------------------------------------------

class ArbitrationSessionRejected(ValueError):
    """会话操作不满足状态机/契约；携带稳定 ``code`` 与 ``message_key``。"""

    code = "arbitration_session_invalid"
    message_key = "team.arbitration_session_invalid"
    status = 409

    def __init__(self, message: str, *, params: dict[str, Any] | None = None,
                 details: str | None = None) -> None:
        super().__init__(message)
        self.params = dict(params or {})
        self.details = details or message


class ArbitrationSessionNotOpen(ArbitrationSessionRejected):
    """尚未调用 open 建立会话（或会话行不存在）。"""

    code = "arbitration_session_not_open"
    message_key = "team.arbitration_session_not_open"


class ArbitrationSessionCompleted(ArbitrationSessionRejected):
    """会话已完成，不能继续裁决。"""

    code = "arbitration_session_completed"
    message_key = "team.arbitration_session_completed"


class ArbitrationSessionItemNotFound(ArbitrationSessionRejected):
    """该 zotero_key 不在本次会话的冲突集中。"""

    code = "arbitration_session_item_not_found"
    message_key = "team.arbitration_session_item_not_found"
    status = 404


class ArbitrationSessionRevealNotReady(ArbitrationSessionRejected):
    """先判后示：该条尚未裁决，不提供任何筛选员原始决定。"""

    code = "arbitration_session_reveal_not_ready"
    message_key = "team.arbitration_session_reveal_not_ready"


class ArbitrationSessionIncomplete(ArbitrationSessionRejected):
    """finish 时仍有冲突未裁决。"""

    code = "arbitration_session_incomplete"
    message_key = "team.arbitration_session_incomplete"


class ArbitrationStoreUnavailable(RuntimeError):
    """任务协作库暂时不可读/不可写（锁忙、IO 故障、文件损坏）。

    与「会话不存在」严格区分：HTTP 层映射 503 稍后重试，绝不 500，也绝不
    把读不到当成「没有会话」而产出空队列。
    """

    code = "arbitration_session_store_unavailable"
    message_key = "team.arbitration_session_store_unavailable"


def _read_only_connection(path: Path) -> sqlite3.Connection:
    """只读打开既有文献目录；不建库、不执行任何写入。"""
    return sqlite3.connect(path.resolve().as_uri() + _READ_ONLY_QUERY, uri=True,
                           timeout=SCREENER_READ_TIMEOUT)


# ---------------------------------------------------------------------------
# 校验与工具
# ---------------------------------------------------------------------------

def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _year(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _task_id(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("task_id 必须为文本")
    task = value.strip()
    if not task or len(task) > MAX_TASK_ID or any(ch in task for ch in "/\\\x00"):
        raise ValueError("task_id 必须为 1 到 200 个安全字符")
    return task


def _stage(value: Any) -> str:
    if value not in STAGES:
        raise ValueError("stage 必须为 stage1 或 stage2")
    return value


def _actor(value: Any) -> str:
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        raise ValueError("arbitrator_id 必须来自已认证用户")
    actor = str(value).strip()
    if not actor or len(actor) > MAX_ACTOR:
        raise ValueError("arbitrator_id 必须为 1 到 128 个字符")
    return actor


def _key(value: Any) -> str:
    if not isinstance(value, str) or not value or len(value) > MAX_KEY or value != value.strip():
        raise ValueError("zotero_key 必须为 1 到 300 个非空白字符")
    return value


def _json_loads(raw: Any, default: Any) -> Any:
    try:
        return json.loads(raw) if raw else default
    except (TypeError, ValueError):
        return default


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


# ---------------------------------------------------------------------------
# 连接与表
# ---------------------------------------------------------------------------

def _store_connection(path: Path) -> sqlite3.Connection:
    """打开任务协作库；任何底层失败都收敛为 503 语义的模块错误。"""
    try:
        conn = sqlite3.connect(path, timeout=STORE_TIMEOUT)
        conn.row_factory = sqlite3.Row
        return conn
    except (sqlite3.Error, OSError) as exc:  # pragma: no cover - connect 阶段极少失败
        raise ArbitrationStoreUnavailable(
            f"仲裁会话存储不可用：{path.name}") from exc


def _ensure_schema(conn: sqlite3.Connection) -> None:
    try:
        conn.executescript(_SESSION_SCHEMA)
    except sqlite3.Error as exc:
        raise ArbitrationStoreUnavailable("仲裁会话存储表初始化失败。") from exc


def _session_row(conn: sqlite3.Connection, task: str, stage: str,
                 actor: str) -> sqlite3.Row | None:
    try:
        return conn.execute(
            "SELECT * FROM arbitration_session "
            "WHERE task_id=? AND stage=? AND arbitrator_id=?",
            (task, stage, actor),
        ).fetchone()
    except sqlite3.Error as exc:
        raise ArbitrationStoreUnavailable("仲裁会话读取失败。") from exc


def _decision_rows(conn: sqlite3.Connection, task: str, stage: str,
                   actor: str) -> list[sqlite3.Row]:
    try:
        return conn.execute(
            "SELECT * FROM arbitration_decision "
            "WHERE task_id=? AND stage=? AND arbitrator_id=? ORDER BY zotero_key",
            (task, stage, actor),
        ).fetchall()
    except sqlite3.Error as exc:
        raise ArbitrationStoreUnavailable("仲裁会话条目读取失败。") from exc


# ---------------------------------------------------------------------------
# 文献元数据（任务文献目录联查）
# ---------------------------------------------------------------------------

def _catalog_paths(catalog_db: StrPath | None, db_files: Iterable[StrPath]) -> list[Path]:
    """目录来源顺序：属主库优先，其余按文件名确定性排序并去重。"""
    ordered: list[Path] = []
    if catalog_db is not None:
        ordered.append(Path(catalog_db))
    ordered.extend(sorted((Path(raw) for raw in db_files),
                          key=lambda path: (path.name.casefold(), str(path))))
    result: list[Path] = []
    seen: set[str] = set()
    for path in ordered:
        if path.suffix.casefold() != ".db" or not path.is_file():
            continue
        try:
            identity = str(path.resolve())
        except OSError:
            continue
        if identity in seen:
            continue
        seen.add(identity)
        result.append(path)
    return result


def _has_import_order(conn: sqlite3.Connection) -> bool:
    try:
        return any(row[1] == "import_order"
                   for row in conn.execute("PRAGMA table_info(articles)").fetchall())
    except sqlite3.Error:
        return False


def _load_article_metadata(
    db_files: Iterable[StrPath], catalog_db: StrPath | None, keys: list[str],
) -> dict[str, dict[str, Any]]:
    """按编号联查文献元数据；任一目录不可读只降级为该目录无数据。

    队列是阅读卡的数据来源（题录 + 摘要），必须绝不因为某个筛选员库被
    持锁而 500：元数据缺失时字段留空，冲突集本身仍由
    :func:`load_screener_decision_packages` 的 503 口径把关。
    """
    found: dict[str, dict[str, Any]] = {}
    if not keys:
        return found
    wanted = list(dict.fromkeys(keys))
    for path in _catalog_paths(catalog_db, db_files):
        remaining = [key for key in wanted if key not in found]
        if not remaining:
            break
        try:
            with closing(_read_only_connection(path)) as conn:
                sql = _ARTICLE_SQL if _has_import_order(conn) else _ARTICLE_SQL_LEGACY
                for key in remaining:
                    row = conn.execute(sql, (key,)).fetchone()
                    if row is None:
                        continue
                    if len(row) == 6:
                        title, journal, year, doi, abstract, order = row
                    else:  # 旧库无 import_order：保持稳定排序（按 zotero_key）
                        title, journal, year, doi, abstract = row
                        order = None
                    found[key] = {
                        "title": _text(title),
                        "journal": _text(journal),
                        "year": _year(year),
                        "doi": _text(doi),
                        "abstract": _text(abstract),
                        "order": _year(order),
                    }
        except (sqlite3.Error, OSError):
            continue
    return found


def _load_fulltext_keys(db_path: StrPath | None) -> set[str]:
    """stage2 阅读卡「连全文」：裁决者库里有 fulltexts 行且 PDF 仍存在才算可用。

    只读当前裁决者的个人库，与既有 ``GET /fulltext/{key}``（默认 ``screener``
    = 当前用户）的可见范围一致，避免队列标记「可用」但点开 404。
    """
    if db_path is None:
        return set()
    path = Path(db_path)
    if not path.is_file():
        return set()
    available: set[str] = set()
    try:
        with closing(_read_only_connection(path)) as conn:
            rows = conn.execute("SELECT zotero_key, path FROM fulltexts").fetchall()
    except (sqlite3.Error, OSError):
        return set()
    for key, pdf_path in rows:
        try:
            if pdf_path and Path(str(pdf_path)).is_file():
                available.add(str(key))
        except OSError:
            continue
    return available


# ---------------------------------------------------------------------------
# 汇总（只聚合，不指向单篇个人）
# ---------------------------------------------------------------------------

def _aggregate_summary(merged: Any, names: list[str]) -> dict[str, Any]:
    """一致率与 κ 只留在汇总层；两两配对以匿名 ``pair_N`` 标签呈现。

    绝不输出 ``participation``（含姓名）或任何逐篇字段——队列载荷的先判
    后示约束同样适用于汇总。
    """
    summary = summarize_multi(merged)
    labels = {name: merged[f"decision_{name}"].fillna("").tolist() for name in names}
    pairs = pairwise_kappa(labels)
    light = light_kappa(labels)
    return {
        "n_articles": int(len(merged)),
        "n_agree": int(summary.n_agree),
        "n_conflict": int(summary.n_conflict),
        "agreement_rate": round(float(summary.agreement_rate), 4),
        "light_kappa": None if light is None else round(float(light), 4),
        "pairwise_kappa": [
            {
                "pair": f"pair_{index}",
                "kappa": round(float(result.kappa), 4),
                "n": int(result.n),
                "interpretation": result.interpretation,
            }
            for index, result in enumerate(pairs.values(), start=1)
        ],
    }


# ---------------------------------------------------------------------------
# 载荷
# ---------------------------------------------------------------------------

def _queue_items(rows: list[sqlite3.Row], stage: str) -> list[dict[str, Any]]:
    decorated: list[tuple[bool, int, str, dict[str, Any]]] = []
    for row in rows:
        meta = _json_loads(row["metadata_json"], {})
        if not isinstance(meta, dict):
            meta = {}
        item: dict[str, Any] = {
            "zotero_key": row["zotero_key"],
            "title": _text(meta.get("title")),
            "journal": _text(meta.get("journal")),
            "year": meta.get("year") if isinstance(meta.get("year"), int) else None,
            "doi": _text(meta.get("doi")),
            "abstract": _text(meta.get("abstract")),
            "status": "decided" if row["decision"] else "pending",
        }
        if stage == "stage2":
            item["fulltext_available"] = bool(meta.get("fulltext_available"))
        order = meta.get("order")
        missing_order = not isinstance(order, int)
        decorated.append((missing_order, order if isinstance(order, int) else 0,
                          row["zotero_key"], item))
    decorated.sort(key=lambda entry: (entry[0], entry[1], entry[2]))
    return [entry[3] for entry in decorated]


def _session_payload(conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    task, stage, actor = row["task_id"], row["stage"], row["arbitrator_id"]
    items = _queue_items(_decision_rows(conn, task, stage, actor), stage)
    decided = sum(1 for item in items if item["status"] == "decided")
    stored = _json_loads(row["summary_json"], {})
    if not isinstance(stored, dict):
        stored = {}
    summary = dict(stored)
    summary.update({
        "total": len(items),
        "decided": decided,
        "remaining": len(items) - decided,
    })
    return {
        "stage": stage,
        "status": row["status"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "completed_at": row["completed_at"],
        "snapshot_id": row["snapshot_id"],
        "summary": summary,
        "items": items,
    }


def _reveal_payload(row: sqlite3.Row) -> dict[str, Any]:
    votes = _json_loads(row["votes_json"], [])
    if not isinstance(votes, list):
        votes = []
    return {
        "zotero_key": row["zotero_key"],
        "decision": row["decision"],
        "reason": row["reason"],
        "decided_at": row["decided_at"],
        "screeners": votes,
    }


# ---------------------------------------------------------------------------
# open
# ---------------------------------------------------------------------------

def _conflict_items(merged: Any, names: list[str]) -> list[tuple[str, list[dict[str, Any]]]]:
    """把合并表的 conflict 行转成 (key, votes) 列表（按合并表确定性顺序）。"""
    entries: list[tuple[str, list[dict[str, Any]]]] = []
    for _, row in merged.iterrows():
        if row["status"] != "conflict":
            continue
        key = str(row["zotero_key"])
        votes = [
            {
                "name": name,
                "decision": _text(row.get(f"decision_{name}")),
                "reason": _text(row.get(f"exclusion_reason_{name}")),
            }
            for name in names
        ]
        entries.append((key, votes))
    return entries


def open_session(
    store_path: StrPath,
    *,
    task_id: str,
    stage: str,
    db_files: Iterable[StrPath],
    arbitrator_id: str | int,
    catalog_db: StrPath | None = None,
    fulltext_db: StrPath | None = None,
) -> dict[str, Any]:
    """建立（或恢复）逐条裁决会话并返回队列；重复调用不丢已裁决进度。

    冲突集每次都由 :func:`load_screener_decision_packages` +
    :func:`merge_decision_packages` 从任务目录现取（服务器模式全员决策，
    503 锁忙口径与双盲守卫由 API 层在调用前执行）。已完成会话直接冻结
    返回，不重读筛选员库、也不改动任何既有裁决。
    """
    task = _task_id(task_id)
    stage = _stage(stage)
    actor = _actor(arbitrator_id)
    path = Path(store_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ArbitrationStoreUnavailable("仲裁会话存储目录不可写。") from exc

    with closing(_store_connection(path)) as conn:
        _ensure_schema(conn)
        existing = _session_row(conn, task, stage, actor)
        if existing is not None and existing["status"] == SESSION_COMPLETED:
            return _session_payload(conn, existing)

    # 冲突集重建：锁忙/IO 故障按 503 上抛，绝不落成缺员冲突集。
    named = load_screener_decision_packages(db_files, stage=stage)
    merged = merge_decision_packages(named)
    names = list(named)
    entries = _conflict_items(merged, names)
    keys = [key for key, _ in entries]
    metadata = _load_article_metadata(db_files, catalog_db, keys)
    fulltext = _load_fulltext_keys(fulltext_db) if stage == "stage2" else set()
    summary = _aggregate_summary(merged, names)
    now = _now()

    with closing(_store_connection(path)) as conn:
        _ensure_schema(conn)
        try:
            conn.execute("BEGIN IMMEDIATE")
            current = _session_row(conn, task, stage, actor)
            if current is None:
                conn.execute(
                    "INSERT INTO arbitration_session "
                    "(task_id,stage,arbitrator_id,status,summary_json,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?,?)",
                    (task, stage, actor, SESSION_OPEN, _canonical_json(summary), now, now),
                )
            elif current["status"] == SESSION_OPEN:
                conn.execute(
                    "UPDATE arbitration_session SET summary_json=?, updated_at=? "
                    "WHERE task_id=? AND stage=? AND arbitrator_id=?",
                    (_canonical_json(summary), now, task, stage, actor),
                )

            # 按当前冲突集重建队列：既有裁决（decision 非空）与理由原样保留，
            # 只刷新元数据/投票快照；不在冲突集中的旧条目清理（阶段已暂停，
            # 正常不会发生；若发生，该条已不是冲突，留着只会污染进度）。
            current_keys = set(keys)
            for row in _decision_rows(conn, task, stage, actor):
                if row["zotero_key"] not in current_keys:
                    conn.execute(
                        "DELETE FROM arbitration_decision "
                        "WHERE task_id=? AND stage=? AND arbitrator_id=? AND zotero_key=?",
                        (task, stage, actor, row["zotero_key"]),
                    )
            for key, votes in entries:
                meta = dict(metadata.get(key) or {})
                meta["fulltext_available"] = key in fulltext
                conn.execute(
                    "INSERT INTO arbitration_decision "
                    "(task_id,stage,arbitrator_id,zotero_key,metadata_json,votes_json) "
                    "VALUES(?,?,?,?,?,?) "
                    "ON CONFLICT(task_id,stage,arbitrator_id,zotero_key) DO UPDATE SET "
                    "metadata_json=excluded.metadata_json, votes_json=excluded.votes_json",
                    (task, stage, actor, key, _canonical_json(meta),
                     _canonical_json(votes)),
                )
            conn.commit()
        except sqlite3.Error as exc:
            conn.rollback()
            raise ArbitrationStoreUnavailable("仲裁会话写入失败。") from exc
        except Exception:
            conn.rollback()
            raise

        row = _session_row(conn, task, stage, actor)
        assert row is not None  # 事务内必定已插入
        return _session_payload(conn, row)


# ---------------------------------------------------------------------------
# decide / reveal
# ---------------------------------------------------------------------------

def decide(
    store_path: StrPath,
    *,
    task_id: str,
    stage: str,
    arbitrator_id: str | int,
    zotero_key: str,
    decision: Any,
    reason: Any = "",
) -> dict[str, Any]:
    """裁决单条冲突（r18 两道闸先行），返回该条的 reveal 载荷。

    拒绝（maybe / 排除无理由）在写库前抛出，会话与条目保持原状；成功后才把
    ``revealed`` 置 1，此后 :func:`reveal` 才可返回筛选员原始决定。
    """
    task = _task_id(task_id)
    stage = _stage(stage)
    actor = _actor(arbitrator_id)
    key = _key(zotero_key)
    # r18 两道闸：与 CSV / server-save 汇总提交共用同一函数，拒绝文案与
    # error_code 逐字一致（复用，不重写）。
    normalized, rationale = validate_arbitration_decision(decision, reason, zotero_key=key)

    path = Path(store_path)
    with closing(_store_connection(path)) as conn:
        _ensure_schema(conn)
        try:
            conn.execute("BEGIN IMMEDIATE")
            session = _session_row(conn, task, stage, actor)
            if session is None:
                raise ArbitrationSessionNotOpen("仲裁会话尚未开始，请先加载冲突集。")
            if session["status"] == SESSION_COMPLETED:
                raise ArbitrationSessionCompleted("该仲裁会话已完成，不能继续裁决。")
            item = conn.execute(
                "SELECT * FROM arbitration_decision "
                "WHERE task_id=? AND stage=? AND arbitrator_id=? AND zotero_key=?",
                (task, stage, actor, key),
            ).fetchone()
            if item is None:
                raise ArbitrationSessionItemNotFound(
                    "该文献不在本次仲裁冲突集中。", params={"zotero_key": key})
            now = _now()
            conn.execute(
                "UPDATE arbitration_decision SET decision=?, reason=?, decided_at=?, revealed=1 "
                "WHERE task_id=? AND stage=? AND arbitrator_id=? AND zotero_key=?",
                (normalized, rationale, now, task, stage, actor, key),
            )
            conn.execute(
                "UPDATE arbitration_session SET updated_at=? "
                "WHERE task_id=? AND stage=? AND arbitrator_id=?",
                (now, task, stage, actor),
            )
            conn.commit()
        except sqlite3.Error as exc:
            conn.rollback()
            raise ArbitrationStoreUnavailable("仲裁裁决写入失败。") from exc
        except Exception:
            conn.rollback()
            raise

        session = _session_row(conn, task, stage, actor)
        decided_row = conn.execute(
            "SELECT * FROM arbitration_decision "
            "WHERE task_id=? AND stage=? AND arbitrator_id=? AND zotero_key=?",
            (task, stage, actor, key),
        ).fetchone()
        payload = _session_payload(conn, session)  # type: ignore[arg-type]
        payload["item"] = {"zotero_key": key, "status": "decided"}
        payload["reveal"] = _reveal_payload(decided_row)  # type: ignore[arg-type]
        return payload


def reveal(
    store_path: StrPath,
    *,
    task_id: str,
    stage: str,
    arbitrator_id: str | int,
    zotero_key: str,
) -> dict[str, Any]:
    """已裁决条目的 reveal 载荷；未裁决条目绝不返回任何筛选员决定。"""
    task = _task_id(task_id)
    stage = _stage(stage)
    actor = _actor(arbitrator_id)
    key = _key(zotero_key)

    path = Path(store_path)
    with closing(_store_connection(path)) as conn:
        _ensure_schema(conn)
        session = _session_row(conn, task, stage, actor)
        if session is None:
            raise ArbitrationSessionNotOpen("仲裁会话尚未开始，请先加载冲突集。")
        row = conn.execute(
            "SELECT * FROM arbitration_decision "
            "WHERE task_id=? AND stage=? AND arbitrator_id=? AND zotero_key=?",
            (task, stage, actor, key),
        ).fetchone()
        if row is None:
            raise ArbitrationSessionItemNotFound(
                "该文献不在本次仲裁冲突集中。", params={"zotero_key": key})
        if not row["decision"] or not row["revealed"]:
            raise ArbitrationSessionRevealNotReady(
                "该文献尚未裁决，裁决后才能查看各筛选员的原始决定。",
                params={"zotero_key": key})
        return _reveal_payload(row)


# ---------------------------------------------------------------------------
# finish
# ---------------------------------------------------------------------------

def finish(
    store_path: StrPath,
    *,
    task_id: str,
    stage: str,
    db_files: Iterable[StrPath],
    arbitrator_id: str | int,
) -> dict[str, Any]:
    """全部冲突裁决后，把会话快照进既有 append-only 仲裁记录并置 completed。

    格式与 ``append_decision_arbitration`` 完全一致（含全员原始决策列）。
    未完成时抛 :class:`ArbitrationSessionIncomplete`（结构化 409，携带剩余
    条数），绝不落半成品记录。重复 finish 幂等返回同一条快照。
    """
    task = _task_id(task_id)
    stage = _stage(stage)
    actor = _actor(arbitrator_id)
    path = Path(store_path)

    with closing(_store_connection(path)) as conn:
        _ensure_schema(conn)
        session = _session_row(conn, task, stage, actor)
        if session is None:
            raise ArbitrationSessionNotOpen("仲裁会话尚未开始，请先加载冲突集。")
        if session["status"] == SESSION_COMPLETED:
            return _completed_payload(conn, session, path)

    # 现取合并表：与 open 同一加载器/合并器，保证记录格式与 r12/r18 一致。
    named = load_screener_decision_packages(db_files, stage=stage)
    merged = merge_decision_packages(named)
    conflicts = merged[merged["status"] == "conflict"]
    conflict_keys = [str(key) for key in conflicts["zotero_key"]]

    with closing(_store_connection(path)) as conn:
        _ensure_schema(conn)
        session = _session_row(conn, task, stage, actor)
        if session is None:
            raise ArbitrationSessionNotOpen("仲裁会话尚未开始，请先加载冲突集。")
        if session["status"] == SESSION_COMPLETED:
            return _completed_payload(conn, session, path)
        decided = {
            row["zotero_key"]: (str(row["decision"]), _text(row["reason"]))
            for row in _decision_rows(conn, task, stage, actor)
            if row["decision"]
        }
        missing = [key for key in conflict_keys if key not in decided]
        if missing:
            raise ArbitrationSessionIncomplete(
                f"仍有 {len(missing)} 条冲突未裁决，完成后才能生成仲裁记录。",
                params={"remaining": len(missing), "total": len(conflict_keys)},
            )
        resolutions = [
            {"zotero_key": key, "decision": decided[key][0], "rationale": decided[key][1]}
            for key in conflict_keys
        ]

    # append-only 记录（自带事务）：绝不与本模块的 BEGIN IMMEDIATE 嵌套。
    # 存储锁忙/IO 故障按 503 语义上抛（与筛选员库锁忙同口径），绝不把
    # 底层英文错误当 400 文案透出。
    try:
        record = append_decision_arbitration(
            path, stage=stage, merged=merged, resolutions=resolutions,
            arbitrator_id=actor,
        )
    except (sqlite3.Error, OSError) as exc:
        raise ArbitrationStoreUnavailable("仲裁记录写入失败，请稍后重试。") from exc

    now = _now()
    with closing(_store_connection(path)) as conn:
        _ensure_schema(conn)
        try:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(
                "UPDATE arbitration_session SET status=?, completed_at=?, snapshot_id=?, updated_at=? "
                "WHERE task_id=? AND stage=? AND arbitrator_id=? AND status=?",
                (SESSION_COMPLETED, now, int(record["id"]), now, task, stage, actor,
                 SESSION_OPEN),
            )
            conn.commit()
        except sqlite3.Error as exc:
            conn.rollback()
            raise ArbitrationStoreUnavailable("仲裁会话完成状态写入失败。") from exc
        except Exception:
            conn.rollback()
            raise
        session = _session_row(conn, task, stage, actor)
        if session is not None and cursor.rowcount == 0 and session["status"] == SESSION_COMPLETED:
            # 并发 finish：另一请求已把同一快照置完成；返回其记录（幂等）。
            return _completed_payload(conn, session, path)
        assert session is not None
        payload = _session_payload(conn, session)
        payload["record"] = record
        payload["already_completed"] = False
        return payload


def _completed_payload(conn: sqlite3.Connection, session: sqlite3.Row,
                       path: Path) -> dict[str, Any]:
    payload = _session_payload(conn, session)
    snapshot_id = session["snapshot_id"]
    record = get_decision_arbitration(path, int(snapshot_id)) if snapshot_id else None
    if record is None:
        raise ArbitrationSessionCompleted(
            "仲裁会话已完成，但快照记录不可读；请联系任务属主检查协作库。",
            params={"snapshot_id": snapshot_id})
    payload["record"] = record
    payload["already_completed"] = True
    return payload
