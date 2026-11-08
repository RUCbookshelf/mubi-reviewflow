"""逐条盲裁决会话 HTTP 契约（2.1.5 B1）。

端点（全部服务器模式；桌面模式 404）：

- ``GET  /api/tasks/{task_id}/screening-arbitrations/session?stage=``
  打开/恢复会话并返回逐篇队列 + 汇总（先判后示：队列不含任何筛选员姓名
  或其单篇决定）；
- ``POST /api/tasks/{task_id}/screening-arbitrations/session/item``
  裁决单条（r18 两道闸：include/exclude 二选一、exclude 必须附理由），
  返回该条的 reveal（各筛选员原决定 + 理由）；
- ``GET  /api/tasks/{task_id}/screening-arbitrations/session/reveal?stage=&zotero_key=``
  读取已裁决条目的 reveal；未裁决返回结构化 409；
- ``POST /api/tasks/{task_id}/screening-arbitrations/session/finish``
  全部冲突裁决后落 append-only 仲裁记录；未完成返回结构化 409。

守卫复用（不重写）：权限/legacy 409 用 ``_arbitration_store``，双盲 409 用
``_arbitration_double_blind_guard``，成员库锁忙 503 用
``SCREENER_LIBRARY_UNAVAILABLE_MESSAGE``——与 r12/r14 既有
``/screening-arbitrations/server-merge`` 完全同一函数。CSV 上传仲裁与桌面
模式维持现有表格流程不动（本模块只服务服务器模式的逐篇会话）。

路由顺序：main.py 的 ``GET /screening-arbitrations/{record_id}`` 通配路由
注册早于本模块；``/session`` 是单段路径会被它吞成 422。注册末尾把本模块
的路由提升到该通配路由之前（只在 ``app.router.routes`` 内移动，不改任何
既有路由），否则 ``GET .../session`` 永远到不了这里。
"""
from __future__ import annotations

from typing import Literal

from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel, Field

from coscreen import arbitration_session as session_mod
from coscreen import tasks as tasks_mod
from coscreen.screening_arbitration import (
    ArbitrationDecisionRejected,
    ScreenerLibraryUnavailableError,
)

_SESSION_PATH = "/api/tasks/{task_id}/screening-arbitrations/session"
_RECORD_ROUTE_PATH = "/api/tasks/{task_id}/screening-arbitrations/{record_id}"


class SessionDecideIn(BaseModel):
    """单条裁决请求体。

    ``decision`` 故意不加 pattern/min_length：r18 的两道闸必须对
    空值/maybe/任意值给出稳定 400（``arbitration_decision_invalid``），
    而不是 FastAPI 的 422（与 CSV / server-save 的拒绝形状同口径）。
    """

    stage: Literal["stage1", "stage2"]
    zotero_key: str = Field(min_length=1, max_length=300)
    decision: str | None = Field(default="")
    reason: str | None = Field(default=None, max_length=100_000)


class SessionStageIn(BaseModel):
    stage: Literal["stage1", "stage2"]


def _hoist_session_routes(app) -> None:
    """把本模块路由移到 ``/screening-arbitrations/{record_id}`` 之前。

    Starlette 按注册顺序取首个匹配；``{record_id}`` 的路径正则接受任意
    单段文本，晚注册的 ``/session`` 会被它抢走（422 int 解析失败）。
    这里只做顺序调整：既有路由对象、路径、处理函数一律不改。
    """
    routes = app.router.routes
    ours = [route for route in routes
            if getattr(route, "path", "").startswith(_SESSION_PATH)]
    if not ours:
        return
    wildcard = next((index for index, route in enumerate(routes)
                     if getattr(route, "path", "") == _RECORD_ROUTE_PATH), None)
    if wildcard is None:
        return
    if min(routes.index(route) for route in ours) < wildcard:
        return  # 已经在本模块自定义顺序里（或重复调用），无需再动。
    for route in ours:
        routes.remove(route)
    wildcard = next(index for index, route in enumerate(routes)
                    if getattr(route, "path", "") == _RECORD_ROUTE_PATH)
    routes[wildcard:wildcard] = ours


def register_arbitration_session_routes(
    app,
    require_user,
    task_lookup,
    arbitration_store,
    double_blind_guard,
    owner_db_for,
    server_mode,
    bad,
    unavailable_message,
) -> None:
    """注册逐条盲裁决会话端点（权限/双盲/503 全部复用既有守卫）。"""

    def _context(task_id: str, user: dict):
        if not server_mode():
            raise bad("团队协作仅在服务器模式下开放。", 404)
        store = arbitration_store(task_id, user)   # 403 / legacy 409
        double_blind_guard(task_id, user)          # 双盲 409（无成员内容）
        return task_lookup(task_id), store

    def _session_error(exc: Exception) -> HTTPException:
        """会话层异常 -> 结构化 HTTP（拒绝不落盘、503 不 500）。"""
        if isinstance(exc, ScreenerLibraryUnavailableError):
            return bad(unavailable_message, 503)
        if isinstance(exc, ArbitrationDecisionRejected):
            # r18 两道闸：与 `_arbitration_resolution_error` 同形。
            return HTTPException(400, {
                "error_code": exc.code,
                "message_key": exc.message_key,
                "params": exc.params,
                "details": exc.details,
                "message": str(exc),
            })
        if isinstance(exc, session_mod.ArbitrationStoreUnavailable):
            return HTTPException(503, {
                "error_code": exc.code,
                "message_key": exc.message_key,
                "params": {},
                "details": str(exc),
                "message": "仲裁会话存储暂时不可用，请稍后重试。",
            })
        if isinstance(exc, session_mod.ArbitrationSessionRejected):
            return HTTPException(exc.status, {
                "error_code": exc.code,
                "message_key": exc.message_key,
                "params": exc.params,
                "details": exc.details,
                "message": str(exc),
            })
        return bad(str(exc))

    @app.get(_SESSION_PATH)
    def get_arbitration_session(
        task_id: str,
        stage: Literal["stage1", "stage2"] = "stage1",
        user: dict = Depends(require_user),
    ) -> dict:
        """打开/恢复逐条裁决会话（队列零泄露；已完成会话冻结返回）。"""
        info, store = _context(task_id, user)
        try:
            return session_mod.open_session(
                store,
                task_id=info.task_id,
                stage=stage,
                db_files=info.db_files,
                arbitrator_id=user["user_id"],
                catalog_db=owner_db_for(info),
                # stage2「连全文」只看当前裁决者自己的库——与既有 fulltext
                # 端点（默认 screener=当前用户）可见范围一致，标记不会骗人。
                fulltext_db=info.dir / f"{tasks_mod.slugify_screener(user['username'])}.db",
            )
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001 —— 统一结构化映射，绝不 500
            raise _session_error(exc) from exc

    @app.post(_SESSION_PATH + "/item")
    def decide_arbitration_session_item(
        task_id: str,
        body: SessionDecideIn,
        user: dict = Depends(require_user),
    ) -> dict:
        """裁决单条冲突；成功后才返回该条的 reveal（先判后示）。"""
        info, store = _context(task_id, user)
        try:
            return session_mod.decide(
                store,
                task_id=info.task_id,
                stage=body.stage,
                arbitrator_id=user["user_id"],
                zotero_key=body.zotero_key,
                decision=body.decision,
                reason=body.reason,
            )
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise _session_error(exc) from exc

    @app.get(_SESSION_PATH + "/reveal")
    def get_arbitration_session_reveal(
        task_id: str,
        stage: Literal["stage1", "stage2"] = "stage1",
        zotero_key: str = Query(min_length=1, max_length=300),
        user: dict = Depends(require_user),
    ) -> dict:
        """已裁决条目的 reveal；未裁决一律不返回任何筛选员决定。"""
        info, store = _context(task_id, user)
        try:
            return session_mod.reveal(
                store,
                task_id=info.task_id,
                stage=stage,
                arbitrator_id=user["user_id"],
                zotero_key=zotero_key,
            )
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise _session_error(exc) from exc

    @app.post(_SESSION_PATH + "/finish")
    def finish_arbitration_session(
        task_id: str,
        body: SessionStageIn,
        user: dict = Depends(require_user),
    ) -> dict:
        """全部冲突已裁决后落 append-only 记录；未完成 -> 结构化 409。"""
        info, store = _context(task_id, user)
        try:
            return session_mod.finish(
                store,
                task_id=info.task_id,
                stage=body.stage,
                db_files=info.db_files,
                arbitrator_id=user["user_id"],
            )
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise _session_error(exc) from exc

    _hoist_session_routes(app)
