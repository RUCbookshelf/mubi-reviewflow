"""检索式管理 API（2.1.5 Wave G1/W3）。

16 个端点 + living-review 兼容层所依赖的存储原语。全部错误为结构化
``{"error_code", "message_key", "params"}``；出站仅 PubMed E-utilities 与
ERIC 官方 API 两域，且只在显式 ``preview-counts`` 请求内发生；单库失败降级
为 ``unavailable``，绝不 500。

存储/纯函数在 ``coscreen.search_strategy``；本模块只做 HTTP 接线、权限闸与
出站计数（测试经 ``url_opener`` 注入确定性夹具）。

权限：读 = 任务参与者；写 = 任务属主（``can_manage`` 同义）；desktop 模式
legacy 任务本人全权；server 模式 legacy 任务写 409 ``legacy_owner_unset``。
"""

from __future__ import annotations

import http.client
import json
import logging
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
from contextlib import closing
from pathlib import Path
from typing import Any, Callable

from fastapi import Body, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from coscreen import search_strategy as core
from coscreen import task_collaboration
from custom_backend.eric_search_api import EricSearchError, search_eric_preview

__all__ = ["register_search_strategy_routes"]

logger = logging.getLogger(__name__)

#: PubMed 计数端点（esearch retmax=0 只取 count）。用户输入只进入 ``term``
#: 查询参数，host/scheme 恒为声明常量，无法把请求引向其他主机。
PUBMED_COUNT_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
_PUBMED_USER_AGENT = "ReviewFlow-SearchStrategy/2.1.5 (offline-first; contact: local-user)"
_MAX_COUNT_BYTES = 1024 * 1024
_COUNT_TIMEOUT_CAP = 20.0

#: preview-counts 单请求条数上限（2.1.5 安全修复）：``items``+``strategies``
#: 去重后合计最多 :data:`PREVIEW_MAX_ITEMS` 条。此前无上限，普通成员一次
#: 请求可触发任意多次逐项串行出站（终审探针实测 300 次 PubMed 调用）→
#: 上游限流/封 IP。超限 400 ``search_strategy.preview_too_many``。
PREVIEW_MAX_ITEMS = 10


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """拒绝跟随 3xx（先例：custom_backend/ai_suggest_api.py）。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        return None


#: 终审 #31：显式空 ProxyHandler（口径同 pdf_fetch_api._build_opener）——否则
#: 代理环境下检索式会整包发往代理主机。拒重定向 handler 保留。独立成工厂
#: 便于测试在代理环境下重建断言 handler 组成。
def _build_count_opener():
    return urllib.request.build_opener(urllib.request.ProxyHandler({}),
                                       _NoRedirectHandler)


_COUNT_OPENER = _build_count_opener()


class PubmedCountError(RuntimeError):
    """PubMed 计数失败（网络/HTTP/坏响应）；路由层降级为 unavailable。"""


def count_pubmed_hits(query: str, *, timeout: float = 10.0,
                      url_opener: Callable[..., bytes] | None = None) -> int:
    """esearch ``retmax=0`` 取总命中数（显式触发、短超时、失败抛可捕获异常）。"""
    text = str(query or "").strip()
    if not text:
        raise PubmedCountError("检索式不能为空。")
    params = urllib.parse.urlencode({
        "db": "pubmed", "term": text, "retmode": "json", "retmax": 0,
        "tool": "ReviewFlow",
    })
    url = f"{PUBMED_COUNT_URL}?{params}"
    opener = url_opener or _COUNT_OPENER.open
    try:
        if url_opener is None:
            with _COUNT_OPENER.open(
                    urllib.request.Request(url, headers={"User-Agent": _PUBMED_USER_AGENT}),
                    timeout=timeout) as response:
                raw = response.read(_MAX_COUNT_BYTES + 1)
        else:
            raw = opener(url, timeout)
    except urllib.error.HTTPError as exc:
        raise PubmedCountError(f"PubMed 返回 HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError,
            http.client.HTTPException) as exc:
        raise PubmedCountError(f"PubMed 不可达：{exc}") from exc
    except Exception as exc:  # noqa: BLE001  注入 opener 的任意故障归一
        raise PubmedCountError(f"PubMed 不可达：{exc}") from exc
    if not isinstance(raw, (bytes, bytearray)):
        raise PubmedCountError("计数响应不是字节流。")
    if len(raw) > _MAX_COUNT_BYTES:
        raise PubmedCountError("计数响应超过读取上限。")
    try:
        payload = json.loads(bytes(raw).decode("utf-8"))
        count = payload["esearchresult"]["count"]
    except (UnicodeDecodeError, ValueError, KeyError, TypeError) as exc:
        raise PubmedCountError(f"PubMed 计数响应不可解析：{exc}") from exc
    if isinstance(count, bool):
        raise PubmedCountError("PubMed 计数不是整数。")
    try:
        value = int(count)
    except (TypeError, ValueError) as exc:
        raise PubmedCountError("PubMed 计数不是整数。") from exc
    if value < 0:
        raise PubmedCountError("PubMed 计数不是非负整数。")
    return value


# ---------------------------------------------------------------------------
# 请求体
# ---------------------------------------------------------------------------

class PicoBlock(BaseModel):
    key: str = ""
    label: str = ""
    value: str = ""
    synonyms: list[str] = Field(default_factory=list)
    mesh_terms: list[str] = Field(default_factory=list)


class TranslateIn(BaseModel):
    query_text: str | None = None
    pico: list[PicoBlock] | None = None
    date_from: str = ""
    date_to: str = ""


class PreviewItem(BaseModel):
    database: str = ""
    query_text: str = ""


class PreviewIn(BaseModel):
    items: list[PreviewItem] | None = None
    strategies: list[PreviewItem] | None = None
    timeout_seconds: float = 10.0


class StrategyIn(BaseModel):
    title: str = ""
    database: str = ""
    query_text: str = ""
    syntax_note: str = ""
    note: str = ""
    snapshot_id: int | None = None
    subscribe: bool = False
    source: str = "manual"


class StrategyUpdateIn(BaseModel):
    title: str | None = None
    database: str | None = None
    query_text: str | None = None
    syntax_note: str | None = None
    note: str | None = None
    subscribe: bool | None = None


class FinalizeIn(BaseModel):
    executed_at: str = ""
    hit_count: int | None = None
    hit_source: str = ""
    note: str | None = None


class SubscribeIn(BaseModel):
    subscribe: bool = False


class SnapshotIn(BaseModel):
    pico: list[dict] = Field(default_factory=list)
    strategies: list[dict] = Field(default_factory=list)
    counts: dict[str, int | None] | None = None
    counts_source: str = ""
    note: str = ""
    make_current: bool = False


class ManualCountIn(BaseModel):
    counts: dict[str, int | None] = Field(default_factory=dict)
    source: str = "manual"


class ToStrategyIn(BaseModel):
    databases: list[str] = Field(default_factory=list)
    subscribe: bool = False
    title_prefix: str = ""


class PrismaIn(BaseModel):
    only_with_counts: bool = True


# ---------------------------------------------------------------------------
# 注册
# ---------------------------------------------------------------------------

def register_search_strategy_routes(
    app: Any,
    require_user: Callable[..., dict],
    task_lookup: Callable[[str], Any],
    task_visible: Callable[[Any, dict], bool] | None = None,
    server_mode: Callable[[], bool] | None = None,
) -> None:
    """在 ``app`` 上注册检索式管理端点（main.py 注册区一行调用）。"""

    def _error(status: int, code: str, params: dict | None = None,
               key_prefix: str = "search_strategy.") -> HTTPException:
        return HTTPException(status, {
            "error_code": code,
            "message_key": key_prefix + code,
            "params": params or {},
        })

    # ---- 任务/权限 -------------------------------------------------------
    def _read_info(task_id: str, user: dict):
        info = task_lookup(task_id)
        if task_visible is not None and not task_visible(info, user):
            raise _error(403, "task_forbidden")
        return info

    def _can_manage(info, user: dict) -> bool:
        owner = task_collaboration.get_owner(
            task_collaboration.shared_store_path(info.dir), task_id=info.task_id)
        if owner is None:
            return not (server_mode() if server_mode is not None else False)
        return owner["owner_user_id"] == user["user_id"]

    def _write_info(task_id: str, user: dict):
        info = _read_info(task_id, user)
        owner = task_collaboration.get_owner(
            task_collaboration.shared_store_path(info.dir), task_id=info.task_id)
        if owner is None:
            if server_mode is not None and server_mode():
                raise _error(409, "legacy_owner_unset")
            return info
        if owner["owner_user_id"] != user["user_id"]:
            raise _error(403, "write_forbidden")
        return info

    # ---- 存储 -----------------------------------------------------------
    def _legacy_migration_pending(info, user: dict) -> bool:
        """review67 M-12：读分支是否仍需可写打开（触发一次性 legacy 迁移）。

        只读 GET 的纪律是「零磁盘写」；唯一例外是**可管理任务的一方**访问
        且 ``living_review.json`` 迁移尚未完成——「首次访问触发一次性迁移」
        是既有产品行为（test_2_1_5_w3_strategy.py 两颗钉：迁移成功 + 坏
        .bak 409）。普通参与者与已迁移/无旧文件的任务一律只读打开。
        """
        if not (Path(info.dir) / core.LEGACY_FILENAME).is_file():
            return False  # 无旧文件：任何读都零写，直接走只读打开。
        if not _can_manage(info, user):
            return False  # 普通参与者不得触发迁移写（review67 M-12）。
        store = core.store_path(info.dir)
        if not store.is_file():
            return True  # 库尚不存在 → 标记必然未置，迁移待触发。
        try:
            with closing(core.connect(store, readonly=True)) as probe:
                marker = probe.execute(
                    "SELECT value FROM search_strategy_meta WHERE key = ?",
                    (core.LEGACY_MARKER_KEY,)).fetchone()
        except (sqlite3.Error, OSError):
            # 现存库打不开/损坏：交回可写路径，按既有错误口径映射（503）。
            return True
        return marker is None

    def _library(task_id: str, user: dict, *, write: bool = False):
        info = _write_info(task_id, user) if write else _read_info(task_id, user)
        path = core.store_path(info.dir)
        try:
            path.resolve().relative_to(Path(info.dir).resolve())
        except (OSError, ValueError):
            # 路径越界（如库文件被换成指向任务目录外的符号链接）——与其它
            # storage_unavailable 路径同一状态码（契约 §七：503）。
            raise _error(503, "storage_unavailable") from None
        try:
            # review67 M-12：读分支默认 readonly=True（mode=ro 不建库、不跑
            # DDL、不迁移；库缺失时上层拿到空内存库按「暂无条目」展示），
            # 只读 GET 绝不产生磁盘写；仅 legacy 迁移待完成且调用方可管理
            # 时例外（见 _legacy_migration_pending）。
            if write or _legacy_migration_pending(info, user):
                return core.ensure_library(info.dir)
            return core.ensure_library(info.dir, readonly=True)
        except core.SearchStrategyError as exc:
            raise _translate_error(exc) from None
        except sqlite3.OperationalError as exc:
            logger.warning("检索式库打开失败（降级 503）：%s", exc)
            raise _error(503, "storage_unavailable") from None
        except sqlite3.DatabaseError as exc:
            logger.exception("检索式库损坏")
            raise _error(503, "storage_unavailable") from None

    def _translate_error(exc: core.SearchStrategyError) -> HTTPException:
        status = 503 if exc.code == "storage_unavailable" else 409 if exc.code in {
            "strategy_not_draft", "final_not_deletable",
            "no_final_strategies", "legacy_backup_failed",
        } else 404 if exc.code in {
            "strategy_not_found", "snapshot_not_found",
        } else 400
        return _error(status, exc.code, exc.params)

    def _run(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        try:
            return func(*args, **kwargs)
        except core.SearchStrategyError as exc:
            raise _translate_error(exc) from None
        except sqlite3.OperationalError as exc:
            logger.warning("检索式库操作失败（降级 503）：%s", exc)
            raise _error(503, "storage_unavailable") from None
        except sqlite3.DatabaseError as exc:
            logger.exception("检索式库损坏")
            raise _error(503, "storage_unavailable") from None

    # ---- translate / preview -------------------------------------------
    @app.post("/api/tasks/{task_id}/search-strategy/translate")
    def search_strategy_translate(task_id: str, body: TranslateIn,
                                  user: dict = Depends(require_user)) -> dict:
        """纯计算转写/生成（零 IO、零联网）。"""
        _read_info(task_id, user)
        has_query = bool((body.query_text or "").strip())
        has_pico = body.pico is not None
        if has_query and has_pico:
            raise _error(400, "input_ambiguous")
        if has_query:
            return core.translate_pubmed_query(body.query_text)
        if has_pico:
            try:
                return core.translate_pico(
                    [block.model_dump() for block in body.pico or []],
                    body.date_from, body.date_to)
            except core.SearchStrategyError as exc:
                raise _translate_error(exc) from None
        raise _error(400, "query_required")

    @app.post("/api/tasks/{task_id}/search-strategy/preview-counts")
    async def search_strategy_preview(task_id: str, body: PreviewIn,
                                      user: dict = Depends(require_user)) -> dict:
        """真命中数：仅 PubMed/ERIC 出站；其余如实标 ``manual_required``。"""
        _read_info(task_id, user)
        items = body.items if body.items is not None else body.strategies
        if items is None:
            raise _error(400, "preview_items_required")
        # 条数闸门（2.1.5 安全修复）：items+strategies 合并、按
        # (database, query_text) 去重后合计不得超过 PREVIEW_MAX_ITEMS，
        # 超限 400 且零出站；处理列表同样只保留去重后的条目（重复项只算
        # 一次，不重复出站）。
        def _item_key(item: PreviewItem) -> tuple[str, str]:
            return (str(item.database or "").strip().lower(),
                    str(item.query_text or "").strip())

        seen: set[tuple[str, str]] = set()
        unique_items: list[PreviewItem] = []
        for item in items:
            key = _item_key(item)
            if key not in seen:
                seen.add(key)
                unique_items.append(item)
        requested = len(unique_items)
        if body.items is not None and body.strategies is not None:
            for extra in body.strategies:
                key = _item_key(extra)
                if key not in seen:
                    seen.add(key)
                    requested += 1
        if requested > PREVIEW_MAX_ITEMS:
            raise _error(400, "preview_too_many",
                         {"limit": PREVIEW_MAX_ITEMS, "requested": requested})
        timeout = max(1.0, min(float(body.timeout_seconds or 10.0),
                               _COUNT_TIMEOUT_CAP))
        results: list[dict[str, Any]] = []
        counts: dict[str, int | None] = {}
        for item in unique_items:
            database = str(item.database or "").strip().lower()
            query = str(item.query_text or "").strip()
            if database not in core.DATABASES:
                raise _error(400, "database_invalid", {"database": database})
            if not query:
                raise _error(400, "query_required")
            if database == "pubmed":
                try:
                    count = await run_in_threadpool(
                        count_pubmed_hits, query, timeout=timeout)
                except PubmedCountError as exc:
                    results.append({"database": database, "query_text": query,
                                    "status": "unavailable", "count": None,
                                    "error_code": "pubmed_unavailable",
                                    "hint": "retry_or_manual"})
                    logger.info("PubMed 计数降级：%s", exc)
                    counts[database] = None
                else:
                    results.append({"database": database, "query_text": query,
                                    "status": "counted", "count": count})
                    counts[database] = count
            elif database == "eric":
                try:
                    fetched = await run_in_threadpool(
                        search_eric_preview, query, 1, timeout=timeout)
                except EricSearchError as exc:
                    results.append({"database": database, "query_text": query,
                                    "status": "unavailable", "count": None,
                                    "error_code": exc.code,
                                    "hint": ("fix_query"
                                             if exc.code == "eric_query_error"
                                             else "retry_or_manual")})
                    counts[database] = None
                else:
                    count = int(fetched["total_hits"])
                    results.append({"database": database, "query_text": query,
                                    "status": "counted", "count": count})
                    counts[database] = count
            else:
                results.append({"database": database, "query_text": query,
                                "status": "manual_required", "count": None,
                                "hint": "run_in_database"})
                counts[database] = None
        return {"counts": counts, "results": results,
                "outbound": ["eutils.ncbi.nlm.nih.gov", "api.ies.ed.gov"]}

    # ---- strategies ------------------------------------------------------
    @app.get("/api/tasks/{task_id}/search-strategy/strategies")
    def search_strategy_list(task_id: str,
                             user: dict = Depends(require_user)) -> dict:
        info = _read_info(task_id, user)
        with closing(_library(task_id, user)) as conn:
            migration = core.migration_info(conn)
            return {
                "strategies": core.list_strategies(conn),
                "counts": core.strategy_counts(conn),
                "can_manage": _can_manage(info, user),
                "migration": migration,
            }

    @app.post("/api/tasks/{task_id}/search-strategy/strategies", status_code=201)
    def search_strategy_create(task_id: str, body: StrategyIn,
                               user: dict = Depends(require_user)) -> dict:
        with closing(_library(task_id, user, write=True)) as conn:
            strategy = _run(core.create_strategy, conn,
                            body.model_dump(exclude_unset=True),
                            actor=user["username"])
            return {"strategy": strategy}

    @app.put("/api/tasks/{task_id}/search-strategy/strategies/{sid}")
    def search_strategy_update(task_id: str, sid: int, body: StrategyUpdateIn,
                               user: dict = Depends(require_user)) -> dict:
        data = body.model_dump(exclude_unset=True)
        with closing(_library(task_id, user, write=True)) as conn:
            strategy = _run(core.update_draft, conn, sid, data)
            return {"strategy": strategy}

    @app.post("/api/tasks/{task_id}/search-strategy/strategies/{sid}/finalize")
    def search_strategy_finalize(task_id: str, sid: int, body: FinalizeIn,
                                 user: dict = Depends(require_user)) -> dict:
        data: dict[str, Any] = {}
        if body.executed_at:
            data["executed_at"] = body.executed_at
        if body.hit_count is not None:
            data["hit_count"] = body.hit_count
        if body.hit_source:
            data["hit_source"] = body.hit_source
        if body.note is not None:
            data["note"] = body.note
        with closing(_library(task_id, user, write=True)) as conn:
            return _run(core.finalize_strategy, conn, sid, data)

    @app.post("/api/tasks/{task_id}/search-strategy/strategies/{sid}/revise",
              status_code=201)
    def search_strategy_revise(task_id: str, sid: int,
                               user: dict = Depends(require_user)) -> dict:
        with closing(_library(task_id, user, write=True)) as conn:
            strategy = _run(core.revise_strategy, conn, sid,
                            actor=user["username"])
            return {"strategy": strategy}

    @app.delete("/api/tasks/{task_id}/search-strategy/strategies/{sid}")
    def search_strategy_delete(task_id: str, sid: int,
                               user: dict = Depends(require_user)) -> dict:
        with closing(_library(task_id, user, write=True)) as conn:
            _run(core.delete_draft, conn, sid)
            return {"deleted": True, "id": sid}

    @app.put("/api/tasks/{task_id}/search-strategy/strategies/{sid}/subscribe")
    def search_strategy_subscribe(task_id: str, sid: int, body: SubscribeIn,
                                  user: dict = Depends(require_user)) -> dict:
        with closing(_library(task_id, user, write=True)) as conn:
            strategy = _run(core.set_subscription, conn, sid, body.subscribe)
            return {"strategy": strategy}

    # ---- snapshots -------------------------------------------------------
    @app.get("/api/tasks/{task_id}/search-strategy/snapshots")
    def search_strategy_snapshots(task_id: str,
                                  user: dict = Depends(require_user)) -> dict:
        with closing(_library(task_id, user)) as conn:
            return core.list_snapshots(conn)

    @app.post("/api/tasks/{task_id}/search-strategy/snapshots", status_code=201)
    def search_strategy_snapshot_create(task_id: str, body: SnapshotIn,
                                        user: dict = Depends(require_user)) -> dict:
        with closing(_library(task_id, user, write=True)) as conn:
            snapshot = _run(core.create_snapshot, conn,
                            body.model_dump(exclude_unset=True),
                            actor=user["username"])
            return {"snapshot": snapshot}

    @app.put("/api/tasks/{task_id}/search-strategy/snapshots/{sid}/current")
    def search_strategy_snapshot_current(task_id: str, sid: int,
                                         user: dict = Depends(require_user)) -> dict:
        with closing(_library(task_id, user, write=True)) as conn:
            return _run(core.set_current_snapshot, conn, sid)

    @app.put("/api/tasks/{task_id}/search-strategy/snapshots/{sid}/manual-count")
    def search_strategy_snapshot_count(task_id: str, sid: int,
                                       body: ManualCountIn,
                                       user: dict = Depends(require_user)) -> dict:
        with closing(_library(task_id, user, write=True)) as conn:
            # 端点语义即"人工回填"：counts_source 固定 manual，不接受任意字符串
            snapshot = _run(core.set_snapshot_counts, conn, sid, body.counts,
                            source="manual")
            return {"snapshot": snapshot}

    @app.post("/api/tasks/{task_id}/search-strategy/snapshots/{sid}/to-strategy",
              status_code=201)
    def search_strategy_snapshot_to_strategy(task_id: str, sid: int,
                                             body: ToStrategyIn,
                                             user: dict = Depends(require_user)) -> dict:
        with closing(_library(task_id, user, write=True)) as conn:
            return _run(core.snapshot_to_strategies, conn, sid, body.databases,
                        actor=user["username"], subscribe=body.subscribe,
                        title_prefix=body.title_prefix)

    # ---- 附录 / PRISMA ---------------------------------------------------
    @app.get("/api/tasks/{task_id}/search-strategy/appendix")
    def search_strategy_appendix(task_id: str,
                                 format: str = Query(default="text"),
                                 user: dict = Depends(require_user)) -> Response:
        fmt = str(format or "text").strip().lower()
        if fmt not in ("text", "csv"):
            raise _error(400, "format_invalid", {"format": fmt})
        with closing(_library(task_id, user)) as conn:
            if fmt == "csv":
                payload = _run(core.appendix_csv, conn)
                body = ("\ufeff" + payload).encode("utf-8")
                media = "text/csv; charset=utf-8"
                filename = "search-strategy-appendix.csv"
            else:
                payload = _run(core.appendix_text, conn)
                body = payload.encode("utf-8")
                media = "text/plain; charset=utf-8"
                filename = "search-strategy-appendix.txt"
        return Response(content=body, media_type=media, headers={
            "Content-Disposition": f'attachment; filename="{filename}"'})

    @app.post("/api/tasks/{task_id}/search-strategy/prisma-prefill")
    def search_strategy_prisma(task_id: str, body: PrismaIn | None = Body(default=None),
                               user: dict = Depends(require_user)) -> dict:
        with closing(_library(task_id, user)) as conn:
            return _run(core.prisma_prefill, conn,
                        only_with_counts=(body.only_with_counts if body else True))
