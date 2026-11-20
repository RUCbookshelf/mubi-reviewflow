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
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

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

    2.1.5 收尾清债 C3：包含性校验与该端点同口径（终审 #39 r3 收窄后其
    合法根 = **仅任务目录**）。``fulltexts.path`` 是库内数据（自造/恶意
    导入行可控），只查 ``is_file`` 会把任务目录外的任意存在文件标成
    「可用」（点开即 404，且向队列观察者泄露任意路径存在性）。合法根取
    筛选员库所在目录——API 层传入的 ``fulltext_db`` 即 ``info.dir/<slug>.db``
    （custom_backend/arbitration_session_api.py），父目录就是任务目录；
    与 task_package 导出侧 ``_fulltext_allowed_roots`` 同一判定。
    """
    if db_path is None:
        return set()
    path = Path(db_path)
    if not path.is_file():
        return set()
    task_root = path.parent.resolve()
    available: set[str] = set()
    try:
        with closing(_read_only_connection(path)) as conn:
            rows = conn.execute("SELECT zotero_key, path FROM fulltexts").fetchall()
    except (sqlite3.Error, OSError):
        return set()
    for key, pdf_path in rows:
        try:
            if not pdf_path:
                continue
            resolved = Path(str(pdf_path)).resolve()
            resolved.relative_to(task_root)  # 包含性：逃出任务目录 → 不算可用
        except (OSError, ValueError):
            continue
        if resolved.is_file():
            available.add(str(key))
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
# 团队进度汇总（只读）
# ---------------------------------------------------------------------------

def session_progress(
    store_path: StrPath, task_id: str,
) -> dict[str, dict[str, dict]]:
    """按裁决员汇总各阶段会话进度（只读聚合，绝不建库建表）。

    团队进度口径（2026-11-18 冻结裁定）：

    - ``total`` = 该裁决员该阶段最近一次会话的队列规模
      （``arbitration_decision`` 行数；completed 会话冻结数字、open 会话
      下次 open 时刷新，此处不做实时冲突集重算）；
    - ``completed`` = ``decision`` 非空的条数；
    - ``status`` / ``updated_at`` 来自 ``arbitration_session`` 行；从未
      开会话的裁决员不产生键（调用方按零值对象渲染）；
    - 只输出计数与会话状态，绝不输出队列条目、votes 或筛选员姓名；
    - 复用本模块 ``mode=ro`` 只读连接与表名字面量；库缺失、表不存在或
      任何读失败都返回空 ``dict``（缺表/缺库 = 零进度，绝不写入）。

    返回 ``{str(arbitrator_user_id): {"stage1": {...}, "stage2": {...}}}``，
    每个阶段为 ``{"completed": int, "total": int,
    "status": "open" | "completed" | None, "updated_at": str | None}``。
    """
    path = Path(store_path)
    if not path.is_file():
        return {}
    try:
        with closing(_read_only_connection(path)) as conn:
            tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
                " AND name IN ('arbitration_session', 'arbitration_decision')",
            ).fetchall()
            if len(tables) != 2:
                return {}
            sessions = conn.execute(
                "SELECT stage, arbitrator_id, status, updated_at"
                " FROM arbitration_session WHERE task_id=?",
                (task_id,),
            ).fetchall()
            decisions = conn.execute(
                "SELECT stage, arbitrator_id, COUNT(*),"
                " SUM(CASE WHEN decision IS NOT NULL THEN 1 ELSE 0 END)"
                " FROM arbitration_decision WHERE task_id=?"
                " GROUP BY stage, arbitrator_id",
                (task_id,),
            ).fetchall()
    except FileNotFoundError:
        return {}
    except (sqlite3.Error, OSError):
        return {}
    progress: dict[str, dict[str, dict]] = {}
    for stage, actor, total, completed in decisions:
        progress.setdefault(str(actor), {})[str(stage)] = {
            "completed": int(completed or 0),
            "total": int(total or 0),
            "status": None,
            "updated_at": None,
        }
    for stage, actor, status, updated_at in sessions:
        item = progress.setdefault(str(actor), {}).setdefault(
            str(stage),
            {"completed": 0, "total": 0, "status": None, "updated_at": None})
        item["status"] = status
        item["updated_at"] = updated_at
    for stages in progress.values():
        for stage_name in STAGES:
            if stage_name not in stages:
                stages[stage_name] = {
                    "completed": 0,
                    "total": 0,
                    "status": None,
                    "updated_at": None,
                }
    return progress


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


# ===========================================================================
# P19　team 逐条仲裁分支（C10 合同；独立命名空间，generic 会话零迁移零改写）
# ===========================================================================
#
# 与 legacy 分支的关系（P19 需求 1）：
#
# - 新 team 分支使用独立表（``team_arbitration_session`` /
#   ``team_arbitration_decision``），绝不读写 legacy ``arbitration_session``
#   行；generic CSV/旧 bulk 暂停规则保留在 legacy 分支，不受本节影响；
# - 全部 ``*_on`` 接口遵 C02 借用连接契约：真 ``sqlite3.Connection``、
#   调用方已开启事务（P10 写窗的 collab 屏障事务）、不在本模块内
#   BEGIN/commit/rollback/close、不反向 import backend；
# - ``open_team_session_on``/``decide_team_item_on``/``finish_team_keys_on``
#   管理 session 行；``reveal_team_item_on`` 只读；
# - 每键版本（C10）＝ ``task_id/task_instance_id/stage/item_key/
#   actor_user_id + 该键输入 proof 摘要 + decision_revision + finished``
#   的 canonical SHA。proof 摘要绑定 task/instance/stage/round、required
#   UID 集合与每 UID 的 library/revision/完整意见摘要——B 键票变只改 B 键
#   摘要（不废 A）；A 键未完成输入变化使 A 的准备中结论 stale（409
#   refresh_required）；已 finished 键的 proof 冻结在该键 decision 行，
#   已有 F1 永不因此作废；
# - 缺/旧 ``expected_key_version`` 一律
#   :class:`ArbitrationTeamRefreshRequired`（409 ``arbitration_refresh_required``）。

_TEAM_SESSION_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS team_arbitration_session ("
    " task_instance_id TEXT NOT NULL,"
    " stage TEXT NOT NULL CHECK(stage IN ('stage1', 'stage2')),"
    " session_id TEXT NOT NULL,"
    " actor_user_id INTEGER NOT NULL CHECK(actor_user_id > 0),"
    " task_id TEXT NOT NULL,"
    " round_id TEXT,"
    " status TEXT NOT NULL CHECK(status IN ('open', 'partial', 'completed')),"
    " record_id INTEGER,"
    " attestation_id INTEGER,"
    " created_at TEXT NOT NULL,"
    " updated_at TEXT NOT NULL,"
    " completed_at TEXT,"
    " PRIMARY KEY (task_instance_id, stage, session_id))",
    "CREATE TABLE IF NOT EXISTS team_arbitration_decision ("
    " session_id TEXT NOT NULL,"
    " zotero_key TEXT NOT NULL,"
    " decision TEXT CHECK(decision IS NULL OR decision IN ('include', 'exclude')),"
    " reason TEXT NOT NULL DEFAULT '',"
    " decision_revision INTEGER NOT NULL DEFAULT 0"
    " CHECK(typeof(decision_revision)='integer' AND decision_revision >= 0),"
    " decided_at TEXT,"
    " revealed INTEGER NOT NULL DEFAULT 0 CHECK(revealed IN (0, 1)),"
    " finished INTEGER NOT NULL DEFAULT 0 CHECK(finished IN (0, 1)),"
    " finished_at TEXT,"
    " record_id INTEGER,"
    " attestation_id INTEGER,"
    " proof_json TEXT NOT NULL DEFAULT '{}',"
    " PRIMARY KEY (session_id, zotero_key))",
    "CREATE INDEX IF NOT EXISTS team_arbitration_session_actor"
    " ON team_arbitration_session(task_instance_id, stage, actor_user_id)",
)

TEAM_SESSION_TABLES = ("team_arbitration_session", "team_arbitration_decision")


class ArbitrationTeamRefreshRequired(ArbitrationSessionRejected):
    """缺/旧 expected_key_version：前端刷新后重发，不能绑旧页面判断。"""

    code = "arbitration_refresh_required"
    message_key = "team.arbitration_refresh_required"
    status = 409


class ArbitrationTeamKeyFinished(ArbitrationSessionRejected):
    """该键已在先前 finish 提交中完成（已有 F1），不能再裁决。"""

    code = "team_arbitration_key_finished"
    message_key = "team.arbitration_key_finished"
    status = 409


class ArbitrationTeamSessionCompleted(ArbitrationSessionRejected):
    """team 会话已全部完成。"""

    code = "team_arbitration_session_completed"
    message_key = "team.arbitration_session_completed"


def prepare_team_session_storage(store_path: StrPath) -> None:
    """C05 显式准备生命周期：幂等建立 team 会话两张表（独立连接、自提交）。

    由 backend 协调器在**进入 P10 写窗之前**调用（窗口内连接由 B1 屏障
    持有，不得另开连接做 DDL）；GET 路径绝不调用本函数（只读 preview 不
    隐式 DDL/写入）。
    """
    path = Path(store_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ArbitrationStoreUnavailable("团队仲裁会话存储目录不可写。") from exc
    try:
        with closing(sqlite3.connect(path, timeout=STORE_TIMEOUT)) as conn:
            conn.execute("BEGIN IMMEDIATE")
            for statement in _TEAM_SESSION_SCHEMA:
                conn.execute(statement)
            conn.commit()
    except sqlite3.Error as exc:
        raise ArbitrationStoreUnavailable("团队仲裁会话存储表初始化失败。") from exc


def _ensure_team_tables_on(conn: sqlite3.Connection) -> None:
    """借用连接上的幂等 DDL（表已存在时零行变更；新库首用建表）。"""
    if not isinstance(conn, sqlite3.Connection):
        raise ArbitrationSessionRejected("team 仲裁接口需要既有协作库连接。")
    try:
        for statement in _TEAM_SESSION_SCHEMA:
            conn.execute(statement)
    except sqlite3.Error as exc:
        raise ArbitrationStoreUnavailable("团队仲裁会话表初始化失败。") from exc


def _sha256_hex(value: Any) -> str:
    import hashlib
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def team_proof_digest(*, task_instance_id: str, stage: str, item_key: str,
                      proof_entry: Mapping[str, Any]) -> str:
    """逐键 proof 摘要：绑 task/instance/stage/round、required UID 集合、
    每 UID 的 library/revision 与完整意见摘要（decision/理由/notes）。

    绝不以整批 merge SHA 充当逐键版本——B 键意见变化只影响 B 键摘要。"""
    return _sha256_hex({
        "task_instance_id": task_instance_id,
        "stage": stage,
        "item_key": item_key,
        "round_id": proof_entry.get("round_id"),
        "required_uids": list(proof_entry.get("required_uids") or []),
        "attestation": proof_entry.get("attestation") or {},
        "opinions": proof_entry.get("opinions") or {},
    })


def team_key_version(*, task_id: str, task_instance_id: str, stage: str,
                     item_key: str, actor_user_id: int,
                     proof_entry: Mapping[str, Any],
                     decision_revision: int, finished: bool) -> str:
    """C10 每键版本：canonical SHA（task/instance/stage/key/actor、输入
    proof、该键 decision_revision、finished 标志）。未创建 preview 的
    ``decision_revision=0``。"""
    digest = team_proof_digest(
        task_instance_id=task_instance_id, stage=stage, item_key=item_key,
        proof_entry=proof_entry)
    return _sha256_hex({
        "task_id": task_id,
        "task_instance_id": task_instance_id,
        "stage": stage,
        "item_key": item_key,
        "actor_user_id": int(actor_user_id),
        "proof_sha256": digest,
        "decision_revision": int(decision_revision),
        "finished": bool(finished),
    })


def _team_session_row(conn: sqlite3.Connection, instance: str, stage: str,
                      actor: int) -> sqlite3.Row | None:
    """该裁决员当前（未 completed 的）team 会话行；completed 会话不复用。"""
    try:
        return conn.execute(
            "SELECT * FROM team_arbitration_session"
            " WHERE task_instance_id=? AND stage=? AND actor_user_id=?"
            " AND status != 'completed' ORDER BY created_at DESC LIMIT 1",
            (instance, stage, actor),
        ).fetchone()
    except sqlite3.OperationalError:
        return None  # 表未建＝尚无会话（GET 只读路径绝不建表）
    except sqlite3.Error as exc:
        raise ArbitrationStoreUnavailable("team 仲裁会话读取失败。") from exc


def _team_decision_row(conn: sqlite3.Connection, session_id: str,
                       key: str) -> sqlite3.Row | None:
    try:
        return conn.execute(
            "SELECT * FROM team_arbitration_decision"
            " WHERE session_id=? AND zotero_key=?",
            (session_id, key),
        ).fetchone()
    except sqlite3.Error as exc:
        raise ArbitrationStoreUnavailable("team 仲裁条目读取失败。") from exc


def _team_key_state(conn: sqlite3.Connection, session_id: str,
                    key: str) -> tuple[int, bool]:
    row = _team_decision_row(conn, session_id, key)
    if row is None:
        return 0, False
    return int(row["decision_revision"] or 0), bool(row["finished"])


def _team_proof_entry(raw: Any, key: str) -> dict[str, Any]:
    entry = _json_loads(raw, {})
    if not isinstance(entry, dict) or not entry:
        raise ArbitrationSessionRejected(
            "该键缺少输入 proof。", params={"zotero_key": key})
    return entry


def _team_version_for(conn: sqlite3.Connection, session: Mapping[str, Any],
                      key: str, proof_entry: Mapping[str, Any],
                      frozen: bool) -> str:
    """计算键版本：frozen（已 finish 键）用行内冻结 proof；否则用当前 proof。"""
    revision, finished = _team_key_state(conn, session["session_id"], key)
    effective = proof_entry
    if frozen:
        row = _team_decision_row(conn, session["session_id"], key)
        effective = _team_proof_entry(row["proof_json"] if row else "{}", key)
    return team_key_version(
        task_id=session["task_id"],
        task_instance_id=session["task_instance_id"],
        stage=session["stage"], item_key=key,
        actor_user_id=int(session["actor_user_id"]),
        proof_entry=effective, decision_revision=revision,
        finished=finished)


def _validate_team_proofs(item_keys: list[str],
                          input_proofs: Mapping[str, Any]) -> dict[str, dict]:
    keys = set(item_keys)
    proofs = dict(input_proofs or {})
    unknown = sorted(set(proofs) - keys)
    missing = sorted(keys - set(proofs))
    if unknown or missing:
        raise ArbitrationSessionRejected(
            "item_keys 与 input_proofs 键域不一致。",
            params={"unknown": unknown[:5], "missing": missing[:5]})
    normalized: dict[str, dict] = {}
    for key in item_keys:
        entry = proofs[key]
        if not isinstance(entry, Mapping):
            raise ArbitrationSessionRejected(
                "每键 proof 必须为对象。", params={"zotero_key": key})
        required = entry.get("required_uids")
        attestation = entry.get("attestation")
        opinions = entry.get("opinions")
        if (not isinstance(required, list) or not required
                or not isinstance(attestation, Mapping)
                or not isinstance(opinions, Mapping)):
            raise ArbitrationSessionRejected(
                "每键 proof 缺 required_uids/attestation/opinions。",
                params={"zotero_key": key})
        normalized[key] = {
            "round_id": entry.get("round_id"),
            "required_uids": [int(uid) for uid in required],
            "attestation": {str(uid): dict(value)
                            for uid, value in attestation.items()},
            "opinions": {str(uid): dict(value)
                         for uid, value in opinions.items()},
        }
    return normalized


def _team_session_payload(conn: sqlite3.Connection, session: sqlite3.Row,
                          current_proofs: Mapping[str, Mapping[str, Any]] | None,
                          ) -> dict[str, Any]:
    """team 会话载荷：finished/pending 键 + 每键版本（当前或冻结 proof）。"""
    instance, stage = session["task_instance_id"], session["stage"]
    session_id = session["session_id"]
    try:
        rows = conn.execute(
            "SELECT * FROM team_arbitration_decision WHERE session_id=?"
            " ORDER BY zotero_key", (session_id,)).fetchall()
    except sqlite3.Error as exc:
        raise ArbitrationStoreUnavailable("team 仲裁条目读取失败。") from exc
    items: list[dict[str, Any]] = []
    finished_keys: list[str] = []
    pending_keys: list[str] = []
    for row in rows:
        key = row["zotero_key"]
        frozen = bool(row["finished"])
        proof_entry = _team_proof_entry(row["proof_json"], key)
        version = team_key_version(
            task_id=session["task_id"], task_instance_id=instance,
            stage=stage, item_key=key,
            actor_user_id=int(session["actor_user_id"]),
            proof_entry=proof_entry,
            decision_revision=int(row["decision_revision"] or 0),
            finished=frozen)
        items.append({
            "zotero_key": key,
            "status": ("finished" if frozen
                       else "decided" if row["decision"] else "pending"),
            "decision": row["decision"],
            "key_version": version,
        })
        if frozen:
            finished_keys.append(key)
        else:
            # 剩余 pending＝一切未完成键（含未裁决——它们仍可准备/裁决）。
            pending_keys.append(key)
    return {
        "session_id": session_id,
        "task_instance_id": instance,
        "stage": stage,
        "status": session["status"],
        "record_id": session["record_id"],
        "attestation_id": session["attestation_id"],
        "created_at": session["created_at"],
        "updated_at": session["updated_at"],
        "completed_at": session["completed_at"],
        "items": items,
        "finished_keys": finished_keys,
        "pending_keys": pending_keys,
    }


def open_team_session_on(conn: sqlite3.Connection, *,
                         task_id: str,
                         task_instance_id: str,
                         stage: str,
                         round_id: str | None,
                         actor_user_id: int,
                         item_keys: Iterable[str],
                         input_proofs: Mapping[str, Mapping[str, object]],
                         ) -> dict[str, Any]:
    """建立/恢复 team 逐条仲裁会话（借用连接；幂等刷新 proof，不丢进度）。

    - 该裁决员存在未 completed 会话 → 复用（断点续作）：未完成键的
      ``proof_json`` 刷新为当前输入（decided 结论保留，版本随之变化），
      finished 键行**原样不动**（已有 F1 永不作废）；
    - 无会话 → 新建（``session_id`` = 服务端 UUID 字符串）；
    - 不在当前就绪键域中的未完成旧键删除（已非冲突/未就绪，留着只会
      污染 pending 域）；finished 键永不删除。
    """
    task = _task_id(task_id)
    _stage(stage)
    if isinstance(actor_user_id, bool) or type(actor_user_id) is not int \
            or actor_user_id <= 0:
        raise ArbitrationSessionRejected("actor_user_id 必须为正整数。")
    keys = list(dict.fromkeys(_key(key) for key in item_keys))
    proofs = _validate_team_proofs(keys, input_proofs)
    _ensure_team_tables_on(conn)

    existing = _team_session_row(conn, task_instance_id, stage, actor_user_id)
    now = _now()
    if existing is None:
        session_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO team_arbitration_session"
            " (task_instance_id,stage,session_id,actor_user_id,task_id,"
            "  round_id,status,record_id,attestation_id,created_at,"
            "  updated_at,completed_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (task_instance_id, stage, session_id, actor_user_id, task,
             round_id, "open", None, None, now, now, None))
    else:
        session_id = existing["session_id"]

    current = set(keys)
    try:
        rows = conn.execute(
            "SELECT zotero_key, finished FROM team_arbitration_decision"
            " WHERE session_id=?", (session_id,)).fetchall()
    except sqlite3.Error as exc:
        raise ArbitrationStoreUnavailable("team 仲裁条目读取失败。") from exc
    for row in rows:
        if not row["finished"] and row["zotero_key"] not in current:
            conn.execute(
                "DELETE FROM team_arbitration_decision"
                " WHERE session_id=? AND zotero_key=?",
                (session_id, row["zotero_key"]))
    for key in keys:
        stored = _team_decision_row(conn, session_id, key)
        if stored is not None and stored["finished"]:
            continue  # 冻结键：proof/结论/版本原样保留
        conn.execute(
            "INSERT INTO team_arbitration_decision"
            " (session_id,zotero_key,proof_json) VALUES(?,?,?)"
            " ON CONFLICT(session_id,zotero_key) DO UPDATE SET"
            " proof_json=excluded.proof_json",
            (session_id, key, _canonical_json(proofs[key])))
    conn.execute(
        "UPDATE team_arbitration_session SET updated_at=?, round_id=?, task_id=?"
        " WHERE task_instance_id=? AND stage=? AND session_id=?",
        (now, round_id, task, task_instance_id, stage, session_id))
    session = conn.execute(
        "SELECT * FROM team_arbitration_session"
        " WHERE task_instance_id=? AND stage=? AND session_id=?",
        (task_instance_id, stage, session_id)).fetchone()
    return _team_session_payload(conn, session, proofs)


def decide_team_item_on(conn: sqlite3.Connection, *,
                        task_instance_id: str,
                        stage: str,
                        actor_user_id: int,
                        session_id: str,
                        item_key: str,
                        decision: Any,
                        reason: Any = "",
                        expected_key_version: str,
                        ) -> dict[str, Any]:
    """裁决单键（r18 两道闸先行；版本不符 409 refresh_required）。

    ``decision_revision`` 原子推进（0→1→2…）；finished 键拒绝（已有 F1）。
    拒绝（maybe/排除无理由/版本不符）在写库前抛出，会话与条目保持原状。
    """
    _stage(stage)
    if not isinstance(session_id, str) or not session_id:
        raise ArbitrationSessionNotOpen("team 仲裁会话尚未开始。")
    key = _key(item_key)
    if not isinstance(expected_key_version, str) or not expected_key_version:
        raise ArbitrationTeamRefreshRequired(
            "缺少 expected_key_version，请刷新后重发。",
            params={"zotero_key": key})
    normalized, rationale = validate_arbitration_decision(
        decision, reason, zotero_key=key)

    _ensure_team_tables_on(conn)
    session = conn.execute(
        "SELECT * FROM team_arbitration_session WHERE session_id=?",
        (session_id,)).fetchone()
    if session is None or session["task_instance_id"] != task_instance_id \
            or session["stage"] != stage \
            or int(session["actor_user_id"]) != actor_user_id:
        raise ArbitrationSessionNotOpen("team 仲裁会话尚未开始。")
    row = _team_decision_row(conn, session_id, key)
    if row is None:
        raise ArbitrationSessionItemNotFound(
            "该文献不在本次 team 仲裁就绪集中。",
            params={"zotero_key": key})
    if row["finished"]:
        raise ArbitrationTeamKeyFinished(
            "该键已完成仲裁提交（已有 F1），不能再裁决。",
            params={"zotero_key": key})
    proof_entry = _team_proof_entry(row["proof_json"], key)
    current_version = team_key_version(
        task_id=session["task_id"], task_instance_id=task_instance_id,
        stage=stage, item_key=key, actor_user_id=actor_user_id,
        proof_entry=proof_entry,
        decision_revision=int(row["decision_revision"] or 0), finished=False)
    if expected_key_version != current_version:
        raise ArbitrationTeamRefreshRequired(
            "该键输入已变化（或页面版本过旧），请刷新后重发。",
            params={"zotero_key": key})

    now = _now()
    revision = int(row["decision_revision"] or 0) + 1
    conn.execute(
        "UPDATE team_arbitration_decision"
        " SET decision=?, reason=?, decided_at=?, revealed=1,"
        "     decision_revision=?"
        " WHERE session_id=? AND zotero_key=? AND finished=0",
        (normalized, rationale, now, revision, session_id, key))
    conn.execute(
        "UPDATE team_arbitration_session SET updated_at=? WHERE session_id=?",
        (now, session_id))
    new_version = team_key_version(
        task_id=session["task_id"], task_instance_id=task_instance_id,
        stage=stage, item_key=key, actor_user_id=actor_user_id,
        proof_entry=proof_entry, decision_revision=revision, finished=False)
    return {
        "session_id": session_id,
        "zotero_key": key,
        "decision": normalized,
        "reason": rationale,
        "decided_at": now,
        "key_version": new_version,
    }


def reveal_team_item_on(conn: sqlite3.Connection, *,
                        task_instance_id: str,
                        stage: str,
                        actor_user_id: int,
                        session_id: str,
                        item_key: str,
                        expected_key_version: str,
                        ) -> dict[str, Any]:
    """只读 reveal：已裁决键返回该键裁决结论；未裁决绝不返回任何人票。

    载荷只含**本键**（reveal A 不含 B 未完成人票）；筛选员原始票面由
    backend 从同一受保护 ready 快照附加（本模块不持有成员库）。
    无已存会话/条目时不建任何行——由 backend 直接从快照应答。
    """
    _stage(stage)
    key = _key(item_key)
    if not isinstance(session_id, str) or not session_id:
        raise ArbitrationSessionNotOpen("team 仲裁会话尚未开始。")
    if not isinstance(expected_key_version, str) or not expected_key_version:
        raise ArbitrationTeamRefreshRequired(
            "缺少 expected_key_version，请刷新后重发。",
            params={"zotero_key": key})
    try:
        session = conn.execute(
            "SELECT * FROM team_arbitration_session WHERE session_id=?",
            (session_id,)).fetchone()
    except sqlite3.OperationalError:
        raise ArbitrationSessionNotOpen("team 仲裁会话尚未开始。")
    if session is None or session["task_instance_id"] != task_instance_id \
            or session["stage"] != stage \
            or int(session["actor_user_id"]) != actor_user_id:
        raise ArbitrationSessionNotOpen("team 仲裁会话尚未开始。")
    row = _team_decision_row(conn, session_id, key)
    if row is None:
        raise ArbitrationSessionItemNotFound(
            "该文献不在本次 team 仲裁会话中。",
            params={"zotero_key": key})
    if not row["decision"] or not row["revealed"]:
        raise ArbitrationSessionRevealNotReady(
            "该文献尚未裁决，裁决后才能查看各筛选员的原始决定。",
            params={"zotero_key": key})
    proof_entry = _team_proof_entry(row["proof_json"], key)
    version = team_key_version(
        task_id=session["task_id"], task_instance_id=task_instance_id,
        stage=stage, item_key=key, actor_user_id=actor_user_id,
        proof_entry=proof_entry,
        decision_revision=int(row["decision_revision"] or 0),
        finished=bool(row["finished"]))
    if expected_key_version != version:
        raise ArbitrationTeamRefreshRequired(
            "该键输入已变化（或页面版本过旧），请刷新后重发。",
            params={"zotero_key": key})
    return {
        "session_id": session_id,
        "zotero_key": key,
        "decision": row["decision"],
        "reason": row["reason"],
        "decided_at": row["decided_at"],
        "key_version": version,
        "finished": bool(row["finished"]),
    }


def finish_team_keys_on(conn: sqlite3.Connection, *,
                        task_instance_id: str,
                        stage: str,
                        actor_user_id: int,
                        session_id: str,
                        item_keys: Iterable[str],
                        expected_key_versions: Mapping[str, str],
                        record_id: int,
                        attestation_id: int,
                        ) -> dict[str, Any]:
    """把本会话中已裁决且仍就绪的指定键标记完成（五工件之一，同 collab 提交）。

    - 逐键核 ``expected_key_versions``（不豁免：任一缺/旧 → 409
      refresh_required，列出全部不匹配键，绝不落半笔）；
    - 只接受已裁决、未 finished 的键；全批校验通过才写；
    - 部分结束后其余键仍可准备（status='partial'）；无剩余未完成键则
      'completed'；
    - ``record_id``/``attestation_id`` 为同事务内 P05 attested append 的
      真实 ID，写入会话与各键行（幂等重试由宿主 receipt 层短路，本函数
      重复调用同一批键时按已完成处理并原样返回）。
    """
    _stage(stage)
    if (isinstance(record_id, bool) or type(record_id) is not int
            or record_id <= 0):
        raise ArbitrationSessionRejected("record_id 必须为正整数。")
    if (isinstance(attestation_id, bool) or type(attestation_id) is not int
            or attestation_id <= 0):
        raise ArbitrationSessionRejected("attestation_id 必须为正整数。")
    keys = list(dict.fromkeys(_key(key) for key in item_keys))
    if not keys:
        raise ArbitrationSessionRejected(
            "finish 拒绝空键集：至少提交一个已就绪且有决议的键。")
    versions = dict(expected_key_versions or {})
    _ensure_team_tables_on(conn)
    session = conn.execute(
        "SELECT * FROM team_arbitration_session WHERE session_id=?",
        (session_id,)).fetchone()
    if session is None or session["task_instance_id"] != task_instance_id \
            or session["stage"] != stage \
            or int(session["actor_user_id"]) != actor_user_id:
        raise ArbitrationSessionNotOpen("team 仲裁会话尚未开始。")

    now = _now()
    already: list[str] = []
    stale: list[str] = []
    undecided: list[str] = []
    rows: dict[str, sqlite3.Row] = {}
    for key in keys:
        row = _team_decision_row(conn, session_id, key)
        if row is None:
            raise ArbitrationSessionItemNotFound(
                "该文献不在本次 team 仲裁会话中。",
                params={"zotero_key": key})
        if row["finished"]:
            already.append(key)
            continue
        if not row["decision"]:
            undecided.append(key)
            continue
        proof_entry = _team_proof_entry(row["proof_json"], key)
        current_version = team_key_version(
            task_id=session["task_id"], task_instance_id=task_instance_id,
            stage=stage, item_key=key, actor_user_id=actor_user_id,
            proof_entry=proof_entry,
            decision_revision=int(row["decision_revision"] or 0),
            finished=False)
        if versions.get(key) != current_version:
            stale.append(key)
        rows[key] = row
    if undecided:
        raise ArbitrationSessionIncomplete(
            "仍有未裁决键混入 finish 请求。",
            params={"undecided": undecided})
    if stale:
        raise ArbitrationTeamRefreshRequired(
            "部分键输入已变化（或页面版本过旧），请刷新后重发。",
            params={"stale_keys": sorted(stale)})

    for key in keys:
        if key in already:
            continue
        conn.execute(
            "UPDATE team_arbitration_decision"
            " SET finished=1, finished_at=?, record_id=?, attestation_id=?"
            " WHERE session_id=? AND zotero_key=? AND finished=0",
            (now, record_id, attestation_id, session_id, key))
    remaining = conn.execute(
        "SELECT COUNT(*) FROM team_arbitration_decision"
        " WHERE session_id=? AND finished=0", (session_id,)).fetchone()[0]
    status = "completed" if not remaining else "partial"
    conn.execute(
        "UPDATE team_arbitration_session"
        " SET status=?, record_id=?, attestation_id=?, updated_at=?,"
        "     completed_at=CASE WHEN ?='completed' THEN ? ELSE completed_at END"
        " WHERE session_id=?",
        (status, record_id, attestation_id, now, status, now, session_id))
    session = conn.execute(
        "SELECT * FROM team_arbitration_session WHERE session_id=?",
        (session_id,)).fetchone()
    payload = _team_session_payload(conn, session, None)
    payload["already_finished_keys"] = already
    return payload


def team_session_overview_on(conn: sqlite3.Connection, *,
                             task_instance_id: str,
                             stage: str,
                             actor_user_id: int,
                             ) -> dict[str, Any] | None:
    """GET 只读 helper：该裁决员当前 team 会话载荷；无会话/无表返回 None。

    绝不建表、绝不写入（GET 只读 preview；表缺失＝尚无会话）。"""
    _stage(stage)
    try:
        row = _team_session_row(conn, task_instance_id, stage, actor_user_id)
        if row is None:
            return None
        return _team_session_payload(conn, row, None)
    except sqlite3.OperationalError:
        return None
