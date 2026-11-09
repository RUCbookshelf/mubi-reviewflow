"""ERIC 增量（Living Review 多源化，2.1.5 Wave G2/W4）——ERIC 通道 + 增量预览端点。

设计依据：``out/release_plan/drafts/design_2.1.5_eric_incremental.md`` v1.0。
权威存储 = 2.1.5 W3 的统一检索式库（``search_strategies.sqlite3``，条目
``database='eric'``）；增量入口仍是既有 ``POST …/living-review/check-now``
（按条目自带源分派，不新增路径），本模块只新增**预览**端点。

为什么是 ID 差集法
------------------
ERIC 官方 API 只有 ``search/format/rows/start/fields`` 五个参数，**没有日期
过滤参数**，无法复刻 PubMed 的 ``mindate`` 窗。因此 ERIC 增量 = 「检索式当前
命中集合」减「任务目录已知 ERIC accession 键（EJ…/ED…）」：

- 补录的旧文献天然被覆盖（不依赖日期窗口），这是差集法相对日期窗的优势；
- 重复键天然被排除，增量与现有文献不会互相冒充；
- 单次只比对 ``ERIC_INCREMENT_LIMIT``（500）条命中：超上限时**诚实披露**
  （``scanned/limit/truncated/remaining_hits`` + 结构化
  ``living_review.eric.truncated``），而不是静默截断；截断断点持久化在
  ``living_review.json`` 的 ``eric_scan`` 游标（终审 #61 用户裁定
  「断点续查」）——下一次 check-now 从断点继续比对尾部，而不是从第 1 条
  重来；整段比完后游标清零，回到头部继续捕获新命中。

治理红线（与 ``eric_search_api`` / ``living_review_plugin`` 同一口径）
--------------------------------------------------------------------
- 出站仅 ``api.ies.ed.gov``（ERIC）与 ``eutils.ncbi.nlm.nih.gov``（PubMed）；
  本模块不新增任何主机，恒由 ``eric_search_api.ERIC_API_URL`` 拼 URL。
- 显式手动触发：本模块零后台线程/定时器，只在请求内运行。
- 短超时、拒绝跟随 3xx、单响应读取上限全部沿用 ``eric_search_api`` 的实现。
- 失败统一 :class:`EricSearchError` → 409（与 PubMed check-now 同形），绝不 500；
  入参类错误 400。
- 不直接入库：增量题录只经调用方合成虚拟 RIS → ``review_imports.upload()``
  受保护导入（staged 待审/激活/溯源全部复用），本模块零数据库写入。
"""
from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any, Callable

from fastapi import Depends, HTTPException, Query
from starlette.concurrency import run_in_threadpool

from coscreen import task_collaboration
from coscreen import tasks as tasks_mod
from coscreen import review_workflow as workflow
from coscreen.eric_incremental import diff_new_records, is_eric_accession, normalize_key
from coscreen.search_strategy import (
    SearchStrategyError,
    ensure_library,
    resolve_check_target,
)
from custom_backend import eric_search_api as eric
from custom_backend.eric_search_api import EricSearchError

logger = logging.getLogger(__name__)

__all__ = [
    "ERIC_INCREMENT_LIMIT",
    "PREVIEW_RECORD_LIMIT",
    "VIRTUAL_FILENAME",
    "collect_known_eric_keys",
    "disclosure_warnings",
    "diff_new_records",
    "fetch_eric_incremental",
    "increment_ris",
    "register_eric_incremental_routes",
    "unavailable_error",
]

#: 单次检查最多比对/导入的命中数（= ERIC 检索并导入的既有上限 500）。
#: 超上限只比对前 500 条并在响应里诚实披露，不做静默截断。
ERIC_INCREMENT_LIMIT = eric.MAX_IMPORT_RECORDS

#: 预览最多回显的新题录条数（前端「本次预计新增 N 条」的展示样本）。
PREVIEW_RECORD_LIMIT = 20

#: 增量虚拟 RIS 的固定文件名：source 身份 = 完整文件名（``import_sources``
#: 的 ``identity='filename:…'``）。固定名让同一份增量集合重复检查时复用既有
#: 源记录（与 PubMed 增量 ``pubmed-update.ris`` 同口径）。
VIRTUAL_FILENAME = "eric-update.ris"

#: 结构化文案键（前端词典按此渲染；后端不新增自由文案）。
KEY_UNAVAILABLE = "living_review.eric.unavailable"
KEY_BASELINE = "living_review.eric.baseline"
KEY_STAGED = "living_review.eric.increment_staged"
KEY_DIRECT = "living_review.eric.increment_direct"
KEY_NONE = "living_review.eric.increment_none"
KEY_TRUNCATED = "living_review.eric.truncated"
KEY_UNSTABLE = "living_review.eric.unstable_records"
KEY_PREVIEW = "living_review.eric.preview"
KEY_SOURCE_REQUIRED = "living_review.eric_source_required"
KEY_SOURCE_CONFLICT = "living_review.source_conflict"
KEY_KNOWN_DEGRADED = "living_review.eric.known_keys_unavailable"


# ---------------------------------------------------------------------------
# 取数（注入 opener，生产走 eric_search_api._open）
# ---------------------------------------------------------------------------

def fetch_eric_incremental(
    query: str,
    *,
    limit: int = ERIC_INCREMENT_LIMIT,
    start: int = 0,
    timeout: float = eric.DEFAULT_TIMEOUT_SECONDS,
    url_opener: Callable[..., bytes] | None = None,
) -> dict[str, Any]:
    """拉取检索式的当前命中快照（第 ``start`` 条起最多 ``limit`` 条）。

    与 ``fetch_eric_import`` 的区别只在**超上限的处置**：导入端点对超上限
    直接 422 拒绝（避免把不完整结果当完整检索入库）；增量是差集语义——
    单次只比对 ``[start, start+limit)`` 一段，超出的部分由调用方持久化
    断点（终审 #61 裁定「断点续查」），下次检查从 ``start+scanned``
    继续，而不是永远从第 1 条重来。响应给 ``remaining_hits``（剩余 N 条
    未比对）供「其余将在下次检查继续」的界面承诺兑现。

    失败契约沿 ``eric_search_api``：``_normalize_query`` 的入参错误抛
    ``eric_bad_query``；网络/HTTP/坏 JSON/坏结构一律 :class:`EricSearchError`。
    """
    text = eric._normalize_query(query)
    limit = eric._validate_rows(
        limit, default=ERIC_INCREMENT_LIMIT, minimum=1,
        maximum=ERIC_INCREMENT_LIMIT, name="limit")
    start = max(0, int(start))
    fetch = eric._wrap_opener(url_opener or eric._open, timeout)

    total, first_page = eric._request_page(
        text, start, min(eric.PAGE_SIZE, limit), fetch=fetch)
    raw_docs = list(first_page)
    while raw_docs and len(raw_docs) < min(max(total - start, 0), limit):
        # ERIC 的 start 为 0 基偏移（与 fetch_eric_import 同一分页口径）。
        offset = start + len(raw_docs)
        _, docs = eric._request_page(
            text, offset, min(eric.PAGE_SIZE, limit - len(raw_docs)), fetch=fetch)
        if not docs:
            break
        raw_docs.extend(docs)
        if len(raw_docs) >= limit:
            break
    if total and not raw_docs and total > start:
        # 上游声称有命中却一页都不给：报坏响应，不把「0 条可见」当增量。
        raise EricSearchError("ERIC 声称有命中但未返回任何题录。",
                              code="eric_bad_response")

    records: list[dict] = []
    unstable = 0
    for doc in raw_docs:
        article = eric.doc_to_article(doc)
        if article is None:
            unstable += 1
        else:
            records.append(article)
    if raw_docs and not records:
        raise EricSearchError("ERIC 返回的题录均缺少稳定标识。",
                              code="eric_bad_response")
    scanned = len(raw_docs)
    return {
        "source": "eric",
        "endpoint": eric.ERIC_API_URL,
        "query": text,
        "total_hits": total,
        "start": start,
        "scanned": scanned,
        "limit": limit,
        "unstable": unstable,
        "truncated": total > start + scanned,
        "remaining_hits": max(0, total - (start + scanned)),
        "scan_next": (start + scanned) if total > start + scanned else 0,
        "records": records,
    }


# ---------------------------------------------------------------------------
# 已知 ERIC 键（只读打开任务属主目录）
# ---------------------------------------------------------------------------

def collect_known_eric_keys(db_path: str | Path) -> dict[str, Any]:
    """任务目录已知键集：active articles + 受保护导入源记录（待审也计入）。

    - 只读打开（``mode=ro``），库不存在/不可读返回空集，绝不创建或修复库；
    - 同时收 ``workflow_source_records`` 的 ``incoming_key``/``staged_key``：
      上次增量进待审但尚未激活的 ERIC 键也是「已知」，否则每次检查都会把
      同一批待审文献重新拉回来；
    - ``baseline`` = 已知键里没有任何 ERIC accession（首查建立在空基线上）。

    返回 ``{"keys": set[str], "eric_keys": int, "known_rows": int,
    "baseline": bool}``；``keys`` 不随响应序列化（只在进程内用于差集）。
    """
    empty = {"keys": set(), "eric_keys": 0, "known_rows": 0,
             "baseline": True, "degraded": False}
    path = Path(db_path)
    if not path.is_file():
        return empty
    keys: set[str] = set()
    try:
        with closing(workflow.connect(path, read_only=True)) as conn:
            tables = {
                row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")
            }
            if "articles" in tables:
                keys.update(
                    normalize_key(row[0]) for row in conn.execute(
                        "SELECT zotero_key FROM articles"))
            if "workflow_source_records" in tables:
                keys.update(
                    normalize_key(row[0]) for row in conn.execute(
                        "SELECT incoming_key FROM workflow_source_records"))
                keys.update(
                    normalize_key(row[0]) for row in conn.execute(
                        "SELECT staged_key FROM workflow_source_records"))
    except sqlite3.Error as exc:
        # 读失败时按空基线继续（不阻断增量），但必须披露：本次「增量」可能被
        # 高估；去重仍由受保护导入管线按来源键兜底，不会真的重复入库。
        logger.warning("ERIC 增量读取已知键失败（按空基线继续）：%s", exc)
        return {**empty, "degraded": True}
    keys.discard("")
    eric_keys = sum(1 for key in keys if is_eric_accession(key))
    return {
        "keys": keys,
        "eric_keys": eric_keys,
        "known_rows": len(keys),
        "baseline": eric_keys == 0,
        "degraded": False,
    }


def increment_ris(records: list[dict]) -> str:
    """增量题录 → 虚拟 RIS（复用 ``eric_search_api.articles_to_ris``）。

    ``ID  - EJ…`` 是 ERIC 的全局稳定来源键，导入管线据此精确识别既有文献。
    """
    return eric.articles_to_ris(records)


# ---------------------------------------------------------------------------
# 失败形状（与 PubMed check-now 的 409 同形 + 结构化 message_key）
# ---------------------------------------------------------------------------

def unavailable_error(task_id: str, exc: Exception) -> HTTPException:
    """``EricSearchError`` → 409（入参类 400），形状与 ``_pubmed_unavailable`` 一致。

    PubMed check-now 的 409 字段集合是
    ``code/message/hint/limits/import_url/review_url``；这里逐字段保留并**追加**
    ``message_key/params/reason/endpoint``（结构化文案键，前端按 key 渲染）。
    ``message`` 只放上游技术诊断文本（不是 UI 文案）。
    """
    reason = str(getattr(exc, "code", "") or "eric_unavailable")
    status = 400 if reason == "eric_bad_query" else 409
    if reason == "eric_query_error":
        hint = "fix_query"
    elif reason == "eric_bad_query":
        hint = "fix_query"
    else:
        hint = "retry_or_manual_import"
    return HTTPException(status, {
        "code": "eric_unavailable",
        "message": str(exc)[:300],
        "message_key": KEY_UNAVAILABLE,
        "params": {"reason": reason},
        "reason": reason,
        "hint": hint,
        "endpoint": eric.ERIC_API_URL,
        "limits": {
            "max_records_per_check": ERIC_INCREMENT_LIMIT,
            "page_size": eric.PAGE_SIZE,
            "max_response_bytes": eric.MAX_RESPONSE_BYTES,
        },
        "import_url": f"/api/tasks/{task_id}/import",
        "review_url": f"/api/tasks/{task_id}/import-batches",
    })


def _owner_catalogue_db(info: Any, user: dict) -> Path:
    """任务属主目录路径（不建库、不迁移）：预览只读取已知键用。

    协作任务 = 属主筛选员库；legacy（无属主）desktop 任务 = 当前用户库
    （与 ``review_workflow.context`` 的 legacy 分支同一口径）。
    """
    try:
        owner = task_collaboration.get_owner(
            task_collaboration.shared_store_path(info.dir),
            task_id=info.task_id)
    except Exception:  # noqa: BLE001  属主库不可读：降级为当前用户库（只读）
        owner = None
    username = owner["owner_username"] if owner is not None else user["username"]
    return Path(info.dir) / (tasks_mod.slugify_screener(username) + ".db")


def _resolve_eric_strategy(info: Any, strategy_id: int) -> dict[str, Any]:
    """按 id 取订阅条目并断言源=ERIC（预览用；错误形状沿 search_strategy）。"""
    try:
        conn = ensure_library(info.dir)
    except SearchStrategyError as exc:
        raise HTTPException(503, {
            "error_code": exc.code,
            "message_key": "search_strategy." + exc.code,
            "params": exc.params,
        }) from exc
    except sqlite3.Error as exc:
        logger.warning("检索式库不可读（预览降级 503）：%s", exc)
        raise HTTPException(503, {
            "error_code": "storage_unavailable",
            "message_key": "search_strategy.storage_unavailable",
            "params": {},
        }) from exc
    try:
        record = resolve_check_target(conn, strategy_id=int(strategy_id))
    except SearchStrategyError as exc:
        raise HTTPException(404, {
            "error_code": exc.code,
            "message_key": "search_strategy." + exc.code,
            "params": exc.params,
        }) from exc
    finally:
        conn.close()
    if str(record.get("database") or "") != "eric":
        raise HTTPException(400, {
            "error_code": "source_conflict",
            "message_key": KEY_SOURCE_CONFLICT,
            "params": {"database": str(record.get("database") or "")},
        })
    return record


def disclosure_warnings(snapshot: dict[str, Any],
                        known: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """结构化披露项：截断 / 不稳定题录 / 已知键读取降级（诚实披露，不是错误）。

    ``known`` 为 :func:`collect_known_eric_keys` 的返回：读取失败时差集会按
    空基线计算（增量可能被高估），因此必须在结果里显式告知用户，而不是静默
    把它当成「全部都是新增」。
    """
    warnings: list[dict[str, Any]] = []
    if snapshot.get("truncated"):
        warnings.append({
            "message_key": KEY_TRUNCATED,
            "params": {
                "scanned": int(snapshot["scanned"]),
                "limit": int(snapshot["limit"]),
                "total_hits": int(snapshot["total_hits"]),
                "remaining": int(snapshot.get("remaining_hits")
                                 or max(0, int(snapshot["total_hits"])
                                        - int(snapshot.get("start") or 0)
                                        - int(snapshot["scanned"]))),
            },
        })
    if snapshot.get("unstable"):
        warnings.append({
            "message_key": KEY_UNSTABLE,
            "params": {"count": int(snapshot["unstable"])},
        })
    if known is not None and known.get("degraded"):
        warnings.append({"message_key": KEY_KNOWN_DEGRADED, "params": {}})
    return warnings


# ---------------------------------------------------------------------------
# 断点续查游标（终审 #61 用户裁定）：持久化在 living_review.json 的
# ``eric_scan`` 字段（既有存储落点、同文件原子写；不新增表/文件）。
# ---------------------------------------------------------------------------

#: living_review.json 里的游标字段名：{cursor_key: {offset,total_hits,updated_at,query_sha}}。
SCAN_CURSOR_FIELD = "eric_scan"


def scan_query_fingerprint(query: str) -> str:
    """断点归属的检索式文本指纹（规范化空白后 sha256）。"""
    import hashlib

    return hashlib.sha256(str(query or "").strip().encode("utf-8")).hexdigest()


def scan_cursor_key(strategy_id: int | None, query: str) -> str:
    """游标键：显式条目按 id；临时检索式按规范化文本指纹。"""
    if strategy_id is not None:
        return "s" + str(int(strategy_id))
    return "q" + scan_query_fingerprint(query)[:16]


def read_scan_offset(state: Any, key: str, *, query: str = "") -> int:
    """从 living_review 状态读断点（缺字段/损坏一律 0 = 从头比对，幂等安全）。

    ``query`` 给定时按指纹校验断点归属：条目 ``query_text`` 原地修改后
    旧断点不再适用（检索式变了 → 尾部偏移属于另一份命中集），按 0 起查；
    旧形状游标（无 ``query_sha``）不受影响。
    """
    if not isinstance(state, dict):
        return 0
    cursors = state.get(SCAN_CURSOR_FIELD)
    if not isinstance(cursors, dict):
        return 0
    record = cursors.get(key)
    if not isinstance(record, dict):
        return 0
    offset = record.get("offset")
    if type(offset) is not int or offset < 0:
        return 0
    stored_sha = record.get("query_sha")
    if (query and isinstance(stored_sha, str)
            and stored_sha != scan_query_fingerprint(query)):
        return 0
    return offset


def apply_scan_cursor(state: Any, key: str, *, offset: int, total_hits: int,
                      now: str, query: str = "") -> None:
    """成功检查后推进/清零断点（在 ``_mutate_living_review_state`` 回调内执行）。

    ``offset > 0``（仍有未比对尾部）→ 记 ``scan_next``；``0``（本轮已比完
    整个命中集）→ 删游标，下次检查回到头部捕获新命中。重复写同一值幂等。
    """
    if not isinstance(state, dict):
        return
    cursors = state.get(SCAN_CURSOR_FIELD)
    if not isinstance(cursors, dict):
        cursors = {}
        state[SCAN_CURSOR_FIELD] = cursors
    if offset > 0:
        cursors[key] = {"offset": int(offset), "total_hits": int(total_hits),
                        "updated_at": now,
                        "query_sha": scan_query_fingerprint(query)}
    else:
        cursors.pop(key, None)


def remaining_hint(snapshot: dict[str, Any]) -> dict[str, Any]:
    """断点续查披露（响应/历史条目追加字段；纯追加，旧客户端忽略）。"""
    return {
        "scan_start": int(snapshot.get("start") or 0),
        "remaining_hits": int(snapshot.get("remaining_hits") or 0),
        "scan_next": int(snapshot.get("scan_next") or 0),
    }


def preview_scan_start(info: Any, strategy_id: int | None,
                       query: str) -> int:
    """预览与 check-now 同一起点：读同一断点游标（只读，不推进）。

    living_review.json 缺失/损坏 → 0（预览是只读建议，不 409）；
    原子替换写入保证读到旧值或新值，不会读到半截 JSON。
    """
    try:
        state = json.loads(
            (Path(info.dir) / "living_review.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    return read_scan_offset(state, scan_cursor_key(strategy_id, query), query=query)


def match_subscribed_eric_strategy(info: Any, query: str) -> int | None:
    """query-only 预览与 check-now 的同一条目解析（终审第 7 波复审阻断项）。

    check-now 对 query-only 调用（``source=eric``）会把 query_text 精确匹配
    到订阅 ERIC 条目并以 ``s<id>`` 为游标键；预览此前直接用 ``q<指纹>`` 键，
    同一检索式在两处起点不同（预览显示「从第 1 条起、预计新增 0 条」而
    check-now 从断点续查并导入尾部条目）。这里复用 ``resolve_check_target``
    的精确匹配（database='eric'，与 check-now 的 ``match_database`` 口径
    一致），命中 → 条目 id（同用 ``s<id>`` 键）；未命中 → None（回退
    ``q<指纹>`` 键——check-now 对无匹配条目的 query-only 调用同样用 q 键）。

    库打开/读取失败沿用 :func:`_resolve_eric_strategy` 的 503 降级形状
    （R3：不包此层时 query-only 预览会落到全局兜底 500，与 strategy_id
    寻址的 503 不一致）。与 check-now 的 writable ``ensure_library`` 同一
    迁移路径（含必要时建库——幂等副作用，契约 §四 披露）。
    """
    text = str(query or "").strip()
    if not text:
        return None
    try:
        conn = ensure_library(info.dir)
    except SearchStrategyError as exc:
        raise HTTPException(503, {
            "error_code": exc.code,
            "message_key": "search_strategy." + exc.code,
            "params": exc.params,
        }) from exc
    except sqlite3.Error as exc:
        logger.warning("检索式库不可读（预览降级 503）：%s", exc)
        raise HTTPException(503, {
            "error_code": "storage_unavailable",
            "message_key": "search_strategy.storage_unavailable",
            "params": {},
        }) from exc
    try:
        record = resolve_check_target(conn, query_text=text, database="eric")
    except sqlite3.Error as exc:
        logger.warning("检索式库不可读（预览降级 503）：%s", exc)
        raise HTTPException(503, {
            "error_code": "storage_unavailable",
            "message_key": "search_strategy.storage_unavailable",
            "params": {},
        }) from exc
    finally:
        conn.close()
    if record is None:
        return None
    try:
        return int(record.get("id"))
    except (TypeError, ValueError):
        return None


def _preview_rows(records: list[dict]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for record in records[:PREVIEW_RECORD_LIMIT]:
        rows.append({
            "zotero_key": record.get("zotero_key"),
            "title": record.get("title") or "",
            "year": record.get("year"),
            "journal": record.get("journal") or "",
            "url": record.get("url") or "",
        })
    return rows


# ---------------------------------------------------------------------------
# 路由注册（main.py 注册区一行调用）
# ---------------------------------------------------------------------------

def register_eric_incremental_routes(
    app: Any,
    require_user: Callable[..., dict],
    task_lookup: Callable[[str], Any],
    task_visible: Callable[[Any, dict], bool] | None = None,
) -> None:
    """注册 ERIC 增量预览端点（``GET …/living-review/preview``）。

    增量本身的入口仍是既有 ``check-now``（不新增路径）；本端点只回答
    「现在检查会新增多少条」——显式触发、只读、失败 409 降级。
    """

    def _error(status: int, code: str, message_key: str,
               params: dict | None = None) -> HTTPException:
        return HTTPException(status, {
            "error_code": code,
            "message_key": message_key,
            "params": params or {},
        })

    def _read_info(task_id: str, user: dict) -> Any:
        info = task_lookup(task_id)
        if task_visible is not None and not task_visible(info, user):
            raise _error(403, "task_forbidden", "living_review.task_forbidden")
        return info

    @app.get("/api/tasks/{task_id}/living-review/preview")
    async def living_review_preview(
        task_id: str,
        source: str = Query(default="eric"),
        query: str = Query(default="", max_length=eric.MAX_QUERY_CHARS),
        strategy_id: int | None = Query(default=None, ge=1),
        user: dict = Depends(require_user),
    ) -> dict:
        """ERIC 增量预览：差集大小 + 前若干条新题录（只读，不入库）。"""
        info = _read_info(task_id, user)
        if str(source or "").strip().lower() != "eric":
            raise _error(400, "eric_source_required", KEY_SOURCE_REQUIRED,
                         {"source": str(source or "")})
        text = (query or "").strip()
        record = _resolve_eric_strategy(info, strategy_id) if strategy_id else None
        if not text and record is not None:
            text = str(record.get("query_text") or "").strip()
        if not text:
            raise _error(400, "eric_source_required", KEY_SOURCE_REQUIRED)

        known = collect_known_eric_keys(_owner_catalogue_db(info, user))
        # #61 复审：query-only 预览也要与 check-now 同一 strategy 解析——
        # query 精确命中订阅 ERIC 条目时同用 s<id> 游标键，两处起点一致。
        if record is not None:
            cursor_strategy_id = int(record["id"])
        else:
            cursor_strategy_id = match_subscribed_eric_strategy(info, text)
        scan_start = preview_scan_start(info, cursor_strategy_id, text)
        try:
            snapshot = await run_in_threadpool(
                fetch_eric_incremental, text, start=scan_start)
        except EricSearchError as exc:
            raise unavailable_error(task_id, exc) from exc
        increment = diff_new_records(snapshot["records"], known["keys"])
        rows = _preview_rows(increment)
        return {
            "source": "eric",
            "endpoint": eric.ERIC_API_URL,
            "query": snapshot["query"],
            "strategy_id": record.get("id") if record is not None else None,
            "total_hits": snapshot["total_hits"],
            "scanned": snapshot["scanned"],
            "limit": snapshot["limit"],
            "unstable": snapshot["unstable"],
            "truncated": snapshot["truncated"],
            # 断点续查（#61）：预览与 check-now 同一起点（只读游标，不推进）。
            **remaining_hint(snapshot),
            "known_keys": known["eric_keys"],
            "baseline": known["baseline"],
            "new_count": len(increment),
            "returned": len(rows),
            "records": rows,
            "message_key": KEY_PREVIEW,
            "message_params": {
                "new_count": len(increment),
                "total_hits": snapshot["total_hits"],
                "baseline": known["baseline"],
            },
            "warnings": disclosure_warnings(snapshot, known),
            "outbound": ["api.ies.ed.gov"],
            "import_url": f"/api/tasks/{task_id}/import",
            "review_url": f"/api/tasks/{task_id}/import-batches",
        }
