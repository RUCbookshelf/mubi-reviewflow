"""筛选训练（Pilot Screening）API（2.1.5 G2/W2）。

权威设计：``out/release_plan/drafts/design_2.1.5_pilot_screening.md`` v1.0
§六 端点表 + §九 前端契约。存储/纯函数在 ``coscreen.pilot_screening``；
本模块只做 HTTP 接线、权限闸与 enforce_pass 拦截点。

权限口径（与既有协作/检索式模块一致）：
- 读 = 任务参与者（``task_visible``）；写 = 任务属主（建集/金标准/
  publish/archive/看板）；desktop 模式 legacy 任务（无属主记录）本人全权，
  server 模式 legacy 任务写 409 ``legacy_owner_unset``；
- 学员只能读自己的尝试/结果；负责人可看看板（结论，不含逐条原答案）。
- 题录池（建集抽样/my-attempt 题录）= 属主个人库；desktop legacy 任务回退
  本人库（终审 #12）。
- 只认首次成绩（终审 #19）：enforce 准入、看板结论、my-result 与方法学段
  只认每套题的首次交卷；后续交卷是练习（``practice=true``）。

盲纪律（评审红线，设计 §六）：``my-attempt`` 载荷只有题录字段与本人作答；
未交卷取结果 409；publish 后金标准只读；成员互不可见他人在途尝试。

错误形状统一为 ``{"error_code", "message_key": "pilot.<code>", "params"}``；
message_key 走十语言词典（本线只交键清单，词条归前端）。
"""

from __future__ import annotations

import logging
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any, Callable

from fastapi import Depends, HTTPException
from pydantic import BaseModel, field_validator

from coscreen import db as db_mod
from coscreen import pilot_screening as core
from coscreen import task_collaboration

__all__ = ["enforce_error", "enforce_gate", "register_pilot_screening_routes"]

logger = logging.getLogger(__name__)

#: code → HTTP 状态（其余按 400）。
_STATUS = {
    "storage_unavailable": 503,
    "task_forbidden": 403,
    "owner_required": 403,
    "enforce_blocked": 403,
    "set_not_found": 404,
    "item_not_in_set": 404,
    "set_not_draft": 409,
    "set_archived": 409,
    "gold_locked": 409,
    "gold_incomplete": 409,
    "not_published": 409,
    "attempt_not_started": 409,
    "attempt_submitted": 409,
    "attempt_not_submitted": 409,
    "attempt_not_answered": 409,
    "attempt_mismatch": 409,
    "verdict_not_borderline": 409,
    "legacy_owner_unset": 409,
}


def _error(status: int, code: str, params: dict | None = None,
           prefix: str = "pilot.") -> HTTPException:
    return HTTPException(status, {"error_code": code,
                                  "message_key": prefix + code,
                                  "params": params or {}})


def _translate(exc: core.PilotScreeningError) -> HTTPException:
    return _error(_STATUS.get(exc.code, 400), exc.code, exc.params)


def enforce_error(set_id: int) -> HTTPException:
    """正式初筛写入口的 403 引导（设计 §六 enforce_pass 拦截点）。"""
    return _error(403, "enforce_blocked", {"set_id": int(set_id)})


def enforce_gate(info: Any, user: dict) -> int | None:
    """返回"需要完成的训练集 id"（拦截），None = 放行。

    只在「pilot 开启 + enforce 开启 + 成员未达标/未留痕 + 有已发布训练集」
    时拦截；负责人豁免；**任何存储/解析故障一律降级放行**（可选模块永不
    把正式筛选写路径变成 500 或误拦——设计 S2 非强制）。
    """
    store = core.store_path(info.dir)
    try:
        if not Path(store).is_file():
            return None
        with closing(core.connect(store, readonly=True)) as conn:
            if not core.has_schema(conn):
                return None
            try:
                owner = task_collaboration.get_owner(store, task_id=info.task_id)
            except task_collaboration.OwnerUnavailableError:
                owner = None
            if owner is None:
                # legacy 任务（无属主记录，仅 desktop 单机可达）没有"负责人"
                # 概念：与 _can_manage 的 legacy 口径一致按本人全权处理，
                # 否则开启 enforce 的人会被自己的闸拦住（S2 负责人豁免失真）。
                return None
            blocked, set_id = core.enforce_blocked(
                conn, user_id=user["user_id"],
                owner_user_id=int(owner["owner_user_id"]))
            return set_id if blocked else None
    except Exception as exc:  # noqa: BLE001  可选模块：故障降级，绝不阻断正式筛
        logger.warning("筛选训练 enforce 状态不可读（降级放行）：%s", exc)
        return None


# ---------------------------------------------------------------------------
# 请求体
# ---------------------------------------------------------------------------

class SettingsIn(BaseModel):
    pilot_enabled: bool | None = None
    enforce_pass: bool | None = None


class SetIn(BaseModel):
    size: int | None = None
    sampling: str = "random"
    seed: int | None = None
    curated_keys: list[str] | None = None
    thresholds: dict | None = None
    note: str = ""

    @field_validator("seed", mode="before")
    @classmethod
    def _reject_bool_seed(cls, value):
        """复审 r2：pydantic 宽松模式会把 true/false 强转 1/0 静默建集——
        core.create_set 的 bool 防御在 HTTP 路径不可达。这里在强制转换前
        拒绝布尔（422），seed 只接受真正的整数（含可解析数字串）。"""
        if isinstance(value, bool):
            raise ValueError("seed 必须是 0 到 2147483647 之间的整数，不接受布尔值。")
        return value


class GoldIn(BaseModel):
    zotero_key: str
    decision: str
    reason: str = ""


class ItemIn(BaseModel):
    zotero_key: str
    decision: str
    reason: str = ""
    attempt_id: int | None = None


class DiscussionIn(BaseModel):
    note: str = ""


#: my-attempt 载荷的题录字段白名单（盲纪律断言依据，契约 §4 同源）。
ATTEMPT_ITEM_FIELDS = ("title", "authors", "journal", "year", "doi",
                       "item_type", "abstract")


def register_pilot_screening_routes(
    app: Any,
    require_user: Callable[..., dict],
    task_lookup: Callable[[str], Any],
    task_visible: Callable[[Any, dict], bool] | None = None,
    server_mode: Callable[[], bool] | None = None,
    screener_db: Callable[..., Path] | None = None,
    user_lookup: Callable[[int], dict | None] | None = None,
) -> None:
    """在 ``app`` 上注册筛选训练端点（main.py 注册区一行调用）。"""

    def _server() -> bool:
        return bool(server_mode()) if server_mode is not None else False

    # ---- 任务/权限 -------------------------------------------------------
    def _read_info(task_id: str, user: dict):
        info = task_lookup(task_id)
        if task_visible is not None and not task_visible(info, user):
            raise _error(403, "task_forbidden")
        return info

    def _owner_record(info):
        try:
            return task_collaboration.get_owner(
                core.store_path(info.dir), task_id=info.task_id)
        except task_collaboration.CollaborationStoreCorruptError:
            raise
        except task_collaboration.OwnerUnavailableError as exc:
            raise _error(503, "storage_unavailable") from exc

    def _can_manage(info, user: dict) -> bool:
        owner = _owner_record(info)
        if owner is None:
            return not _server()
        return owner["owner_user_id"] == user["user_id"]

    def _owner_info(task_id: str, user: dict):
        info = _read_info(task_id, user)
        owner = _owner_record(info)
        if owner is None:
            if _server():
                raise _error(409, "legacy_owner_unset")
            return info
        if owner["owner_user_id"] != user["user_id"]:
            raise _error(403, "owner_required")
        return info

    # ---- 存储 -----------------------------------------------------------
    def _memory_schema():
        """只读兜底：无协作库 / 尚无 pilot 表时给一个空的内存库（零磁盘写）。"""
        conn = sqlite3.connect(":memory:", isolation_level=None)
        conn.row_factory = sqlite3.Row
        core.ensure_schema(conn)
        return conn

    def _open(info, *, write: bool = False):
        path = core.store_path(info.dir)
        try:
            Path(path).resolve().relative_to(Path(info.dir).resolve())
        except (OSError, ValueError):
            raise _error(503, "storage_unavailable") from None
        try:
            if not Path(path).is_file():
                if not write:
                    # 只读端点绝不因 GET 产生磁盘写（无协作库 = 全部默认值）。
                    return _memory_schema()
                conn = core.connect(path)
                core.ensure_schema(conn)
                return conn
            if write:
                conn = core.connect(path)
                core.ensure_schema(conn)
                return conn
            conn = core.connect(path, readonly=True)
            if not core.has_schema(conn):
                # 协作库存在但还没建过 pilot 表（升级前的任务）：读端按
                # "暂无训练"展示，不 503、也不在只读连接上跑 DDL。
                conn.close()
                return _memory_schema()
            return conn
        except sqlite3.DatabaseError as exc:
            logger.warning("筛选训练库打开失败（降级 503）：%s", exc)
            raise _error(503, "storage_unavailable") from None

    def _run(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        try:
            return func(*args, **kwargs)
        except core.PilotScreeningError as exc:
            raise _translate(exc) from None
        except OverflowError as exc:
            # 终审 #20：仅 {set_id} 路径参数超出 SQLite INTEGER 域（sqlite3
            # 绑定时抛 "Python int too large to convert to SQLite INTEGER"）
            # 折叠为与"正常不存在"同形的 404 set_not_found。复审 r2：其余
            # OverflowError（如 thresholds 数值 float() 越界——已在
            # core._number 折叠为 400 threshold_invalid；或任何无关来源）
            # 不吞、照常上抛，绝不伪装成训练集不存在。
            if "SQLite INTEGER" not in str(exc):
                raise
            logger.warning("筛选训练 set_id 超出 SQLite INTEGER 范围（按不存在处理）：%s", exc)
            raise _error(404, "set_not_found") from None
        except sqlite3.OperationalError as exc:
            logger.warning("筛选训练库操作失败（降级 503）：%s", exc)
            raise _error(503, "storage_unavailable") from None
        except sqlite3.DatabaseError as exc:
            logger.exception("筛选训练库损坏")
            raise _error(503, "storage_unavailable") from None

    # ---- 文献目录（题录元数据与 random 抽样池） --------------------------
    def _catalog(info, user: dict) -> dict[str, dict[str, Any]]:
        """题录池 = 属主个人库；属主解析失败且非 server 模式时回退本人库。

        终审 #12（用户裁定「回退本人库」）：desktop 模式 legacy 任务（无
        协作库/无属主记录）权限层按本人全权放行，题录池也必须落在本人库，
        否则 curated/random/mixed 建集全部 400（池恒空）。模式对齐
        ``highlight_api._find_article`` 的属主→本人回退；只拼路径
        （``ensure=False``），不建库。server 模式 legacy 不回退（全局闸门
        先行 409 ``collaboration_owner_unset``，此处保持空池防御口径）。
        """
        if screener_db is None:
            return {}
        try:
            owner = _owner_record(info)
        except HTTPException:
            if _server():
                raise  # server：属主不可读仍是可重试 503，不回退
            owner = None
        if owner is not None:
            username = str(owner["owner_username"])
        elif not _server():
            username = str(user["username"])
        else:
            return {}
        try:
            path = screener_db(info, username, ensure=False)
        except (HTTPException, OSError):
            return {}
        if not Path(path).is_file():
            return {}
        try:
            articles = db_mod.list_articles(path, include_duplicates=False)
        except sqlite3.DatabaseError as exc:
            logger.warning("文献目录不可读（训练队列降级为空元数据）：%s", exc)
            return {}
        return {article.zotero_key: article for article in articles}

    def _view_set(info, data: dict, user: dict) -> dict:
        """负责人看全量；成员只看已发布集且带自己的最新档案状态。"""
        if _can_manage(info, user):
            payload = {key: data[key] for key in data if key != "items"}
            payload["can_manage"] = True
            return payload
        payload = {key: data[key] for key in
                   ("id", "size", "sampling", "seed", "note", "status",
                    "created_at", "published_at", "archived_at", "thresholds",
                    "item_count")}
        payload["can_manage"] = False
        return payload

    # ---- 设置（S1/S2） ---------------------------------------------------
    @app.get("/api/tasks/{task_id}/pilot/settings")
    def pilot_settings_get(task_id: str,
                           user: dict = Depends(require_user)) -> dict:
        info = _read_info(task_id, user)
        with closing(_open(info)) as conn:
            return {"settings": core.get_settings(conn),
                    "can_manage": _can_manage(info, user)}

    @app.put("/api/tasks/{task_id}/pilot/settings")
    def pilot_settings_put(task_id: str, body: SettingsIn,
                           user: dict = Depends(require_user)) -> dict:
        info = _owner_info(task_id, user)
        with closing(_open(info, write=True)) as conn:
            settings = _run(core.set_settings, conn, user_id=user["user_id"],
                            pilot_enabled=body.pilot_enabled,
                            enforce_pass=body.enforce_pass)
        return {"settings": settings}

    # ---- 训练集 ----------------------------------------------------------
    @app.post("/api/tasks/{task_id}/pilot/sets", status_code=201)
    def pilot_set_create(task_id: str, body: SetIn,
                         user: dict = Depends(require_user)) -> dict:
        info = _owner_info(task_id, user)
        with closing(_open(info, write=True)) as conn:
            catalog = _catalog(info, user)
            created = _run(core.create_set, conn, actor_id=user["user_id"],
                           available_keys=sorted(catalog), size=body.size,
                           sampling=body.sampling, seed=body.seed,
                           curated_keys=body.curated_keys,
                           thresholds=body.thresholds, note=body.note)
        return {"set": _view_set(info, created, user)}

    @app.get("/api/tasks/{task_id}/pilot/sets")
    def pilot_set_list(task_id: str, user: dict = Depends(require_user)) -> dict:
        info = _read_info(task_id, user)
        manage = _can_manage(info, user)
        with closing(_open(info)) as conn:
            rows = _run(core.list_sets, conn)
            views = []
            for row in rows:
                if not manage and row["status"] not in ("published", "archived"):
                    continue
                view = _view_set(info, row, user)
                if not manage:
                    view.update(_run(core.member_state, conn, set_id=row["id"],
                                     user_id=user["user_id"]))
                views.append(view)
            settings = core.get_settings(conn)
        return {"settings": settings, "can_manage": manage, "sets": views}

    @app.get("/api/tasks/{task_id}/pilot/sets/{set_id}")
    def pilot_set_detail(task_id: str, set_id: int,
                         user: dict = Depends(require_user)) -> dict:
        info = _read_info(task_id, user)
        manage = _can_manage(info, user)
        with closing(_open(info)) as conn:
            data = _run(core.get_set, conn, set_id)
            if not manage:
                if data["status"] not in ("published", "archived"):
                    raise _error(409, "not_published",
                                 {"status": data["status"]})
                view = _view_set(info, data, user)
                view.update(_run(core.member_state, conn, set_id=set_id,
                                 user_id=user["user_id"]))
                items = [{"zotero_key": row["zotero_key"],
                          "position": row["position"]} for row in data["items"]]
                return {"set": view, "items": items, "can_manage": False}
            view = _view_set(info, data, user)
            return {"set": view, "items": data["items"], "can_manage": True}

    @app.post("/api/tasks/{task_id}/pilot/sets/{set_id}/gold")
    def pilot_gold_save(task_id: str, set_id: int, body: GoldIn,
                        user: dict = Depends(require_user)) -> dict:
        info = _owner_info(task_id, user)
        with closing(_open(info, write=True)) as conn:
            return _run(core.save_gold, conn, set_id, zotero_key=body.zotero_key,
                        decision=body.decision, reason=body.reason,
                        actor_id=user["user_id"])

    @app.post("/api/tasks/{task_id}/pilot/sets/{set_id}/publish")
    def pilot_set_publish(task_id: str, set_id: int,
                          user: dict = Depends(require_user)) -> dict:
        info = _owner_info(task_id, user)
        with closing(_open(info, write=True)) as conn:
            data = _run(core.publish_set, conn, set_id)
        return {"set": _view_set(info, data, user)}

    @app.post("/api/tasks/{task_id}/pilot/sets/{set_id}/archive")
    def pilot_set_archive(task_id: str, set_id: int,
                          user: dict = Depends(require_user)) -> dict:
        info = _owner_info(task_id, user)
        with closing(_open(info, write=True)) as conn:
            data = _run(core.archive_set, conn, set_id)
        return {"set": _view_set(info, data, user)}

    @app.get("/api/tasks/{task_id}/pilot/sets/{set_id}/results")
    def pilot_set_results(task_id: str, set_id: int,
                          user: dict = Depends(require_user)) -> dict:
        info = _owner_info(task_id, user)
        with closing(_open(info)) as conn:
            board = _run(core.results_board, conn, set_id)
            data = _run(core.get_set, conn, set_id)
        for member in board["members"]:
            profile = user_lookup(member["user_id"]) if user_lookup else None
            member["username"] = profile["username"] if profile else ""
        return {"set": {"id": data["id"], "status": data["status"],
                        "size": data["size"], "sampling": data["sampling"],
                        "thresholds": data["thresholds"],
                        "item_count": data["item_count"],
                        "gold_count": data["gold_count"]},
                **board}

    # ---- 学员训练流 ------------------------------------------------------
    @app.post("/api/tasks/{task_id}/pilot/sets/{set_id}/attempts")
    def pilot_attempt_start(task_id: str, set_id: int,
                            user: dict = Depends(require_user)) -> dict:
        info = _read_info(task_id, user)
        with closing(_open(info, write=True)) as conn:
            return _run(core.start_attempt, conn, set_id,
                        user_id=user["user_id"])

    @app.get("/api/tasks/{task_id}/pilot/sets/{set_id}/my-attempt")
    def pilot_my_attempt(task_id: str, set_id: int,
                         user: dict = Depends(require_user)) -> dict:
        info = _read_info(task_id, user)
        with closing(_open(info)) as conn:
            queue = _run(core.attempt_queue, conn, set_id,
                         user_id=user["user_id"])
        catalog = _catalog(info, user)
        for item in queue["items"]:
            article = catalog.get(item["zotero_key"])
            for field in ATTEMPT_ITEM_FIELDS:
                value = getattr(article, field, None) if article else None
                if field == "year":
                    item[field] = value if isinstance(value, int) else None
                else:
                    item[field] = "" if value is None else str(value)
        return queue

    @app.post("/api/tasks/{task_id}/pilot/sets/{set_id}/my-attempt/item")
    def pilot_my_attempt_item(task_id: str, set_id: int, body: ItemIn,
                              user: dict = Depends(require_user)) -> dict:
        info = _read_info(task_id, user)
        with closing(_open(info, write=True)) as conn:
            return _run(core.save_item, conn, set_id, user_id=user["user_id"],
                        zotero_key=body.zotero_key, decision=body.decision,
                        reason=body.reason, attempt_id=body.attempt_id)

    @app.post("/api/tasks/{task_id}/pilot/sets/{set_id}/my-attempt/submit")
    def pilot_my_attempt_submit(task_id: str, set_id: int,
                                user: dict = Depends(require_user)) -> dict:
        info = _read_info(task_id, user)
        with closing(_open(info, write=True)) as conn:
            result = _run(core.submit_attempt, conn, set_id,
                          user_id=user["user_id"])
        return {"result": result}

    @app.get("/api/tasks/{task_id}/pilot/sets/{set_id}/my-result")
    def pilot_my_result(task_id: str, set_id: int,
                        user: dict = Depends(require_user)) -> dict:
        info = _read_info(task_id, user)
        with closing(_open(info)) as conn:
            result = _run(core.my_result, conn, set_id, user_id=user["user_id"])
        return {"result": result}

    @app.post("/api/tasks/{task_id}/pilot/sets/{set_id}/my-result/enter-by-discussion")
    def pilot_my_result_enter(task_id: str, set_id: int, body: DiscussionIn,
                              user: dict = Depends(require_user)) -> dict:
        info = _read_info(task_id, user)
        with closing(_open(info, write=True)) as conn:
            result = _run(core.enter_by_discussion, conn, set_id,
                          user_id=user["user_id"], note=body.note)
        return {"result": result}

    @app.get("/api/tasks/{task_id}/pilot/my-history")
    def pilot_my_history(task_id: str, user: dict = Depends(require_user)) -> dict:
        info = _read_info(task_id, user)
        with closing(_open(info)) as conn:
            attempts = _run(core.member_history, conn, user_id=user["user_id"])
            settings = core.get_settings(conn)
            cleared = _run(core.cleared, conn, user_id=user["user_id"])
        return {"attempts": attempts, "cleared": cleared,
                "enforce_pass": settings["enforce_pass"],
                "pilot_enabled": settings["pilot_enabled"]}
