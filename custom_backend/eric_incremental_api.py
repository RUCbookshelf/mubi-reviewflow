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
- 单次只比对前 ``ERIC_INCREMENT_LIMIT``（500）条命中：超上限时**诚实披露**
  （``scanned/limit/truncated`` + 结构化 ``living_review.eric.truncated``），
  而不是静默截断；差集不推进任何「已看过的位置」，所以本次没比对的命中
  在下次检查仍是未知键，不会被永久跳过（与 PubMed 日期游标语义不同）。

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
    timeout: float = eric.DEFAULT_TIMEOUT_SECONDS,
    url_opener: Callable[..., bytes] | None = None,
) -> dict[str, Any]:
    """拉取检索式的当前命中快照（最多 ``limit`` 条），不做差集、不写库。

    与 ``fetch_eric_import`` 的区别只在**超上限的处置**：导入端点对超上限
    直接 422 拒绝（避免把不完整结果当完整检索入库）；增量是差集语义——
    只比对前 ``limit`` 条即可，超出部分在下次检查继续比对，因此这里截断
    并如实返回 ``scanned/limit/truncated``，根本不请求第 ``limit+1`` 条。

    失败契约沿 ``eric_search_api``：``_normalize_query`` 的入参错误抛
    ``eric_bad_query``；网络/HTTP/坏 JSON/坏结构一律 :class:`EricSearchError`。
    """
    text = eric._normalize_query(query)
    limit = eric._validate_rows(
        limit, default=ERIC_INCREMENT_LIMIT, minimum=1,
        maximum=ERIC_INCREMENT_LIMIT, name="limit")
    fetch = eric._wrap_opener(url_opener or eric._open, timeout)

    total, first_page = eric._request_page(
        text, 0, min(eric.PAGE_SIZE, limit), fetch=fetch)
    raw_docs = list(first_page)
    while raw_docs and len(raw_docs) < min(total, limit):
        # ERIC 的 start 为 0 基偏移（与 fetch_eric_import 同一分页口径）。
        start = len(raw_docs)
        _, docs = eric._request_page(
            text, start, min(eric.PAGE_SIZE, limit - start), fetch=fetch)
        if not docs:
            break
        raw_docs.extend(docs)
        if len(raw_docs) >= limit:
            break
    if total and not raw_docs:
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
    return {
        "source": "eric",
        "endpoint": eric.ERIC_API_URL,
        "query": text,
        "total_hits": total,
        "scanned": len(raw_docs),
        "limit": limit,
        "unstable": unstable,
        "truncated": total > len(raw_docs),
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
        try:
            snapshot = await run_in_threadpool(fetch_eric_incremental, text)
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
