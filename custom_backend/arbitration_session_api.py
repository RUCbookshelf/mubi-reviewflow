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

from typing import Literal, Mapping

from fastapi import Depends, Header, HTTPException, Query
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


# ===========================================================================
# P19　team 逐条仲裁（C10 作者侧）：显式协调 helper + 新 team 路由
# ===========================================================================
#
# - 新 team 路由挂 ``/api/tasks/{task_id}/team-arbitration/...``，独立于
#   legacy ``/screening-arbitrations``；**不经过** ``_arbitration_store``
#   （全 stage loader/2–9 包门/legacy paused 规则留在 generic 旧路径不删）；
#   desktop team 同样可达（不做 server_mode 门）；
# - 每次 session create/decide/reveal/finish 都重验 current access +
#   reconciler 授权 + 每键全员完成分歧/两阶段上游资格；
# - GET 只读 preview（不隐式 open/DDL/写入）；首次合法 decide POST 在同一
#   collab 事务内 open 后 decide；
# - finish 五工件（generic+attestation+F1+session+receipt）在同一次 B1
#   collab commit（锚 ``committed_targets == ['collab']``——不允许 generic
#   先提交后再另开 session 事务）；
# - 缺/旧 expected_key_version -> 409 arbitration_refresh_required。

_TEAM_BASE = "/api/tasks/{task_id}/team-arbitration"
_TEAM_OPERATION_NAME = "team_arbitration"

#: ledger/仲裁阶段 -> 窗口 operation 的 UI 阶段词表（P10 F1 适配器固定表：
#: screen/fulltext/coding/appraisal——本包不改 team_keyed_inputs，故在协调器
#: 内映射，业务载荷仍用 stage1/stage2 词汇）。
_TEAM_OPERATION_STAGE = {"stage1": "screen", "stage2": "fulltext"}


class _TeamArbitrationHttp(HTTPException):
    """team 仲裁结构化拒绝（稳定 code/message_key/params）。"""

    def __init__(self, status: int, code: str, message_key: str, message: str,
                 params: dict | None = None) -> None:
        super().__init__(status, {
            "error_code": code, "message_key": message_key,
            "params": dict(params or {}), "details": message, "message": message,
        })


def _utc_now_iso() -> str:
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def _team_decide_receipt_op(stage: str) -> str:
    return f"team_arbitration:decide:{stage}"


def _team_finish_receipt_op(stage: str) -> str:
    return f"team_arbitration:finish:{stage}"


def _require_reconciler_entry(state, user_id: int) -> dict:
    """操作者授权重验（每次请求）：单一有效业务角色 reconciler（含属主）。"""
    if not isinstance(state, Mapping) \
            or state.get("classification") != "owner_match":
        raise _TeamArbitrationHttp(
            503, "team_arbitration_state_unavailable",
            "team.arbitration_state_unavailable",
            "团队协作状态暂时无法确认，请稍后重试。")
    for entry in state.get("participants") or []:
        if not isinstance(entry, Mapping) or entry.get("user_id") != user_id:
            continue
        if entry.get("config") == "ok" and entry.get("role") == "reconciler":
            return dict(entry)
        raise _TeamArbitrationHttp(
            403, "team_arbitration_not_reconciler",
            "team.arbitration_not_reconciler",
            "仅当前任务的有效仲裁员（reconciler）可执行团队逐条仲裁。")
    raise _TeamArbitrationHttp(
        403, "team_arbitration_not_participant",
        "team.arbitration_not_participant",
        "你不是该任务的参与者，无法执行团队逐条仲裁。")


def _required_members(state) -> list[dict]:
    """required_screener 参与者（config ok）；<2 人不成可仲裁分歧。"""
    members = []
    for entry in state.get("participants") or []:
        if not isinstance(entry, Mapping):
            continue
        if entry.get("config") == "ok" and entry.get("required_screener") is True:
            members.append(dict(entry))
    if len(members) < 2:
        raise _TeamArbitrationHttp(
            409, "team_arbitration_roster_unavailable",
            "team.arbitration_roster_unavailable",
            "当前名册不足两名已配置必需筛选人，无法产生可仲裁分歧。")
    return members


#: 同一 (stage, key) 的 F1 记录身份四元组（与 team_keyed_inputs.
#: _same_seal_record 同口径：四字段全同 = 同一 seal 的幂等重复持有）。
_SEAL_IDENTITY_FIELDS = ("seal_id", "content_sha256", "payload_sha256",
                         "sealed_at")


def _stage1_seal_across_holders(member_connections, collab_conn, *,
                                task_instance_id: str, item_key: str):
    """stage1 上游 seal 的跨 holder 发现（HIGH#1 B3，2026-11-20 裁定 1）。

    last_vote unanimous seal 常态落在**最后投票者成员库**（origin
    'actor'），仅查 collab 会漏——对 collab（权威域）+ 全部 required
    成员连接补扫 F1：

    - 返回 dict：首个一致命中的 seal 记录（幂等重复按四字段判同，保留
      确定序首个）＝上游 settled；
    - 返回 ``"conflict"``：同 (stage1, key) 出现互斥 seal（数据完整性
      信号，调用方按不 ready 处理，绝不猜活票）；
    - 返回 ``None``：全部可读 holder 均无 seal（干净负发现）。

    单 holder F1 不可读（无表/暂时不可用）＝该 holder 无见证可采，跳过
    ——与票面路径"不可读＝缺席"的既有口径一致；该成员的缺席已由 votes
    路径 fail-closed 表达。
    """
    from coscreen.team_terminal_facts import (
        TerminalFactsUnavailable,
        read_seal_on,
    )
    holders = [collab_conn]
    holders.extend(member_connections[uid]
                   for uid in sorted(member_connections)
                   if member_connections.get(uid) is not None)
    found = None
    for conn in holders:
        try:
            record = read_seal_on(
                conn, task_instance_id=task_instance_id,
                stage="stage1", item_key=item_key)
        except TerminalFactsUnavailable:
            continue
        if record is None:
            continue
        if found is None:
            found = dict(record)
            continue
        if any(record[field] != found[field]
               for field in _SEAL_IDENTITY_FIELDS):
            return "conflict"
    return found


def _enumerate_ready_snapshot(member_connections, required, collab_conn, *,
                              stage: str, task_instance_id: str) -> dict:
    """受保护 ready 快照：required 成员 fresh 票面 -> 冲突键 + 每键 proof。

    ready ＝ 全员完成分歧（required 全投且意见不一）＋（stage2 时）上游
    stage1 已定（持久 seal 或当前全员一致）＋ 本阶段尚无 F1 seal。任一
    required 连接不可得＝该成员缺席→键未就绪（fail-closed，绝不伪造 proof）。
    """
    from coscreen.team_vote_ledger import vote_snapshot_keys_on
    from coscreen.team_terminal_facts import (
        TerminalFactsUnavailable,
        read_seal_on,
    )

    universe: set = set()
    votes: dict = {}
    proofs_raw: dict = {}
    stages = (stage, "stage1") if stage == "stage2" else (stage,)
    for ledger_stage in stages:
        stage_universe: set = set()
        table = "decisions" if ledger_stage == "stage1" else "stage2_decisions"
        listing_sql = ("SELECT DISTINCT zotero_key FROM decisions"
                       if ledger_stage == "stage1"
                       else "SELECT DISTINCT zotero_key FROM stage2_decisions")
        for uid in required:
            conn = member_connections.get(uid)
            if conn is None:
                continue
            try:
                for row in conn.execute(listing_sql):
                    stage_universe.add(str(row[0]))
            except Exception:  # noqa: BLE001 - 单成员库不可读＝缺席
                continue
        for key in sorted(stage_universe):
            per_uid: dict = {}
            per_proof: dict = {}
            for uid in required:
                conn = member_connections.get(uid)
                if conn is None:
                    per_uid[uid] = {}
                    continue
                try:
                    snapshot = vote_snapshot_keys_on(conn, ledger_stage, (key,))
                except Exception:  # noqa: BLE001 - ledger typed 失败＝不可确认
                    per_uid[uid] = {}
                    continue
                row = snapshot.rows.get(key)
                if row is None or key in snapshot.issues:
                    per_uid[uid] = {}
                    continue
                per_uid[uid] = {
                    "decision": str(row.get("decision") or ""),
                    "exclusion_reason": str(row.get("exclusion_reason") or ""),
                    "notes": str(row.get("notes") or ""),
                    "library_id": snapshot.library_id,
                    "revision": int(row.get("revision") or 0),
                    "raw_row": dict(row),
                }
                per_proof[uid] = {
                    "library_id": snapshot.library_id,
                    "revision": int(row.get("revision") or 0),
                }
            votes[ledger_stage + ":" + key] = per_uid
            proofs_raw[ledger_stage + ":" + key] = per_proof
            if ledger_stage == stage:
                universe.add(key)

    ready: dict = {}
    blocked: dict = {}
    for key in sorted(universe):
        per_uid = votes[stage + ":" + key]
        decisions = {uid: entry.get("decision") for uid, entry in per_uid.items()
                     if entry.get("decision") in ("include", "exclude")}
        if len(decisions) != len(required):
            blocked[key] = "pending_screening"
            continue
        if len(set(decisions.values())) == 1:
            blocked[key] = "no_conflict"
            continue
        if stage == "stage2":
            upstream = votes.get("stage1:" + key, {})
            upstream_decisions = {
                uid: entry.get("decision")
                for uid, entry in upstream.items()
                if entry.get("decision") in ("include", "exclude")}
            upstream_settled = (
                len(upstream_decisions) == len(required)
                and len(set(upstream_decisions.values())) == 1)
            if not upstream_settled:
                # HIGH#1 B3（2026-11-20 裁定 1）：上游 stage1 seal 跨
                # holder 补扫（collab + required 成员库）——任一一致命中
                # 即 settled，互斥 seal 冲突则不 ready。
                upstream_seal = _stage1_seal_across_holders(
                    member_connections, collab_conn,
                    task_instance_id=task_instance_id, item_key=key)
                if upstream_seal == "conflict":
                    blocked[key] = "upstream_conflict"
                    continue
                if upstream_seal is None:
                    blocked[key] = "upstream_pending"
                    continue
        try:
            final_seal = read_seal_on(collab_conn,
                                      task_instance_id=task_instance_id,
                                      stage=stage, item_key=key)
        except TerminalFactsUnavailable:
            final_seal = None
        if final_seal is not None:
            blocked[key] = "already_final"
            continue
        ready[key] = {
            "round_id": None,
            "required_uids": sorted(required),
            "attestation": {str(uid): proofs_raw[stage + ":" + key][uid]
                            for uid in sorted(required)},
            "opinions": {str(uid): {
                "decision": per_uid[uid]["decision"],
                "exclusion_reason": per_uid[uid]["exclusion_reason"],
                "notes": per_uid[uid]["notes"],
            } for uid in sorted(required)},
        }
    return {"ready": ready, "blocked": blocked, "votes": votes}


def _team_http_for(exc: Exception, unavailable_message: str, bad) -> HTTPException:
    """team 仲裁 typed 异常 -> 结构化 HTTP（拒绝不落盘、typed 不 500）。

    P1 异常收口（survey §3.1/§5.2）：本适配器修复前对 receipts/F1/registry
    族一律 ``bad(str(exc), 503)`` 纯文本兜底——状态码非 500 但不是 typed
    口径（确定性未准备被标"稍后重试"、无 ``error_code``、裸异常串）。现换接
    ``custom_backend.team_http_errors`` 统一映射：确定性未准备=409 不可重试、
    ``unreadable``=503 可重试、``connection_contract`` 原样上抛（交既有全局
    fail-closed），并保留本模块既有 typed 分支（仲裁存储/令牌形状）。
    """
    from coscreen.screening_arbitration import (
        ArbitrationDecisionRejected,
        ScreenerLibraryUnavailableError,
    )
    if isinstance(exc, HTTPException):
        return exc
    if isinstance(exc, ScreenerLibraryUnavailableError):
        # 不在本包范围（个人库不可读的标准 503 词条，响应形状保持原样）。
        return bad(unavailable_message, 503)
    if isinstance(exc, ArbitrationDecisionRejected):
        return HTTPException(400, {
            "error_code": exc.code, "message_key": exc.message_key,
            "params": exc.params, "details": exc.details, "message": str(exc),
        })
    if isinstance(exc, session_mod.ArbitrationStoreUnavailable):
        return HTTPException(503, {
            "error_code": exc.code, "message_key": exc.message_key,
            "params": {}, "details": str(exc),
            "message": "团队仲裁存储暂时不可用，请稍后重试。",
        })
    if isinstance(exc, session_mod.ArbitrationSessionRejected):
        return HTTPException(exc.status, {
            "error_code": exc.code, "message_key": exc.message_key,
            "params": exc.params, "details": exc.details, "message": str(exc),
        })
    from coscreen.team_flow_receipts import TeamFlowReceiptConflict
    if isinstance(exc, TeamFlowReceiptConflict):
        # 修轮（复审备注 b）：与成员写/管理侧同形——信封同时给出
        # ``error_code`` 与 ``code`` 两键（同族三处对齐，不再一处缺 code）。
        return HTTPException(409, {
            "error_code": "request_receipt_conflict",
            "code": "request_receipt_conflict",
            "message_key": "team.flow.request_receipt_conflict",
            "params": {}, "details": str(exc),
            "message": "团队仲裁请求回执冲突：请更换请求编号重试。",
            "retryable": False,
        })
    from custom_backend.team_screening_write import TeamFlowUnavailableError
    if isinstance(exc, TeamFlowUnavailableError):
        return HTTPException(getattr(exc, "status", 503), {
            "error_code": getattr(exc, "code", "team.flow.unavailable"),
            "message_key": getattr(exc, "message_key",
                                   "team.flow.unavailable"),
            "params": getattr(exc, "params", {}),
            "details": str(exc), "message": str(exc),
        })
    # P1 并集族：receipts/F1/registry/ledger/management（确定性 409 / 暂时
    # 503 / connection_contract 原样上抛）。返回 None＝本层不可判定 → 交
    # 既有兜底（绝不伪造业务错误）；typed 契约违约（connection_contract）
    # 属编程错误，原样上抛交全局 fail-closed，不伪装成 503 业务态。
    from custom_backend.team_http_errors import (
        CONTRACT_OF_BREACH_REASON,
        typed_team_http,
    )
    if getattr(exc, "reason", None) == CONTRACT_OF_BREACH_REASON:
        raise exc  # 编程违约：原样上抛（P15-D1 口径；两适配器同判）
    mapped = typed_team_http(exc)
    if mapped is not None:
        return mapped
    return bad(str(exc), 503)


def _unknown_outcome(request_id: str, exc: Exception, cleanup=()) -> dict:
    """P13/P18 惯例：body 全部成功、窗口退出失败 -> unknown（绝不重放）。"""
    return {
        "outcome": "unknown", "idempotent": False, "request_id": request_id,
        "result": None, "receipt": None, "seals": (),
        "cleanup_errors": tuple(cleanup),
        "error": f"{type(exc).__name__}: {exc}",
    }


def _window_member_connections(inputs, required):
    """窗口内 required 成员保留连接（只从 P10 保留映射取，绝不自开）。"""
    from pathlib import Path as _Path
    from coscreen import tasks as _tasks
    task_dir = getattr(inputs.ctx.info, "dir", None)
    if task_dir is None:
        return {uid: None for uid in required}
    out = {}
    for uid, username in required.items():
        path = _Path(task_dir) / f"{_tasks.slugify_screener(username)}.db"
        try:
            out[uid] = inputs._reserved.connection(path)  # noqa: SLF001 - P10 保留窗口通道
        except Exception:  # noqa: BLE001 - 未保留＝缺席（fail-closed）
            out[uid] = None
    return out


def _ro_member_connections(store_dir, required):
    """GET preview 的只读成员连接（mode=ro + BEGIN + Row，满足 C02 借用合同）。"""
    import sqlite3 as _sqlite3
    from pathlib import Path as _Path
    from coscreen import tasks as _tasks
    out: dict = {}
    for uid, username in required.items():
        path = _Path(store_dir) / f"{_tasks.slugify_screener(username)}.db"
        if not path.is_file():
            out[uid] = None
            continue
        try:
            conn = _sqlite3.connect(
                path.resolve().as_uri() + "?mode=ro", uri=True, timeout=15)
            conn.row_factory = _sqlite3.Row
            conn.execute("BEGIN")
            out[uid] = conn
        except Exception:  # noqa: BLE001
            out[uid] = None

    def _close():
        for conn in out.values():
            if conn is not None:
                try:
                    conn.close()
                except Exception:  # noqa: BLE001
                    pass
    return out, _close


def _ro_collab(store):
    """只读协作库连接（D1）：显式 BEGIN 开活动读事务——read_seal_on 的
    借用合同（team_terminal_facts._require_on_conn）要求活动事务+Row；
    缺 BEGIN 会令 seal 检查恒抛 TerminalFactsUnavailable 被吞为"无 seal"
    （fail-open：finished 键被重列 ready / stage2 持久 seal 分支恒死）。
    调用方 close 即回滚（只读事务，零写入）。"""
    import sqlite3 as _sqlite3
    conn = _sqlite3.connect(f"file:{store}?mode=ro", uri=True, timeout=15)
    conn.row_factory = _sqlite3.Row
    conn.execute("BEGIN")
    return conn


def _instance_id_ro(store) -> str:
    import sqlite3 as _sqlite3
    from contextlib import closing as _closing
    try:
        with _closing(_sqlite3.connect(
                f"file:{store}?mode=ro", uri=True, timeout=15)) as conn:
            row = conn.execute(
                "SELECT task_instance_id FROM main.team_task_protocol"
                " LIMIT 1").fetchone()
            return str(row[0]) if row is not None else ""
    except Exception:  # noqa: BLE001 - 表未建＝无实例绑定
        return ""


def _stored_revision(store, session_id: str, key: str) -> int:
    import sqlite3 as _sqlite3
    from contextlib import closing as _closing
    try:
        with _closing(_sqlite3.connect(
                f"file:{store}?mode=ro", uri=True, timeout=15)) as conn:
            row = conn.execute(
                "SELECT decision_revision FROM team_arbitration_decision"
                " WHERE session_id=? AND zotero_key=?",
                (session_id, key)).fetchone()
            return int(row[0]) if row is not None else 0
    except Exception:  # noqa: BLE001
        return 0


def _store_path_of(info):
    """协作库路径（注册时注入的 collaboration_store 解析器）。"""
    resolver = _STORE_PATH_RESOLVER.get("collaboration_store")
    if resolver is None:
        raise RuntimeError("team arbitration routes are not registered")
    return resolver(info)


_STORE_PATH_RESOLVER: dict = {}


def _session_decided_keys_ro(store_path, stage: str, user_id: int) -> tuple:
    """兼容路径的窗口外只读候选（真实集合在窗口内重解）。"""
    import sqlite3 as _sqlite3
    from contextlib import closing as _closing
    try:
        with _closing(_sqlite3.connect(
                f"file:{store_path}?mode=ro", uri=True, timeout=15)) as conn:
            session = conn.execute(
                "SELECT session_id FROM team_arbitration_session"
                " WHERE stage=? AND actor_user_id=?"
                " AND status != 'completed'"
                " ORDER BY created_at DESC LIMIT 1",
                (stage, user_id)).fetchone()
            if session is None:
                return ()
            rows = conn.execute(
                "SELECT zotero_key FROM team_arbitration_decision"
                " WHERE session_id=? AND finished=0 AND decision IS NOT NULL"
                " ORDER BY zotero_key", (session[0],)).fetchall()
            return tuple(str(row[0]) for row in rows)
    except Exception:  # noqa: BLE001 - 表未建/不可读＝无可提交键
        return ()


def _decided_reason_on(conn, session_id: str, key: str) -> str:
    row = conn.execute(
        "SELECT reason FROM team_arbitration_decision"
        " WHERE session_id=? AND zotero_key=?", (session_id, key)).fetchone()
    return str(row[0]) if row is not None and row[0] else ""


def _team_screeners_payload(required, votes, *, item_decided: bool) -> list[dict]:
    """盲到底（包 E，2026-11-20 用户裁定）——载荷层条件剥离筛选员理由。

    仅**已决/揭示**条目的载荷可携带各筛选员的排除理由（``reason``）；
    裁决未决（pending）条目连 ``reason`` 键都不发（缺席，非空串遮盖）。
    界面本就未渲染未决条目的理由——本 helper 保证**传输层**结构性缺席，
    不依赖 ``reveal_team_item_on``/``decide_team_item_on`` 的上游先判后示
    守卫：即使未来守卫被放宽或新增调用方，未决条目也无法把理由发到浏览器。

    ``votes`` 兼容两种键域：ready 快照 ``opinions`` 的 ``str(uid)`` 与
    ``_enumerate_ready_snapshot`` 票面的 ``int(uid)``。已决路径逐字段保持
    原形状（name/decision/reason 三键齐全，值原样）。
    """
    screeners: list[dict] = []
    for uid in sorted(required):
        entry = votes.get(uid)
        if entry is None:
            entry = votes.get(str(uid))
        entry = entry if isinstance(entry, Mapping) else {}
        screener = {"name": required[uid],
                    "decision": str(entry.get("decision") or "")}
        if item_decided:
            screener["reason"] = str(entry.get("exclusion_reason") or "")
        screeners.append(screener)
    return screeners


def _merged_frame(finish_keys, ready, required):
    """构造 P05 兼容 merged（仅本次 finish 键的 conflict 行；固定列名）。"""
    import pandas as pd
    names = [required[uid] for uid in sorted(required)]
    columns = {"zotero_key": [], "status": [], "n_screened": []}
    for name in names:
        columns[f"decision_{name}"] = []
        columns[f"exclusion_reason_{name}"] = []
    by_name = {required[uid]: uid for uid in sorted(required)}
    for key in finish_keys:
        entry = ready[key]
        columns["zotero_key"].append(key)
        columns["status"].append("conflict")
        columns["n_screened"].append(float(len(names)))
        for name in names:
            opinion = entry["opinions"][str(by_name[name])]
            columns[f"decision_{name}"].append(opinion["decision"])
            columns[f"exclusion_reason_{name}"].append(
                opinion["exclusion_reason"])
    return pd.DataFrame(columns)


def _adjudicated_seal_payload(*, inputs, stage, key, ready_entry, required,
                              roster, required_uids, resolutions,
                              record_id, attestation_id, attested_at,
                              attestation, request_id, request_sha256,
                              opinion_rows, arbitrator_user_id) -> dict:
    """F1 adjudicated seal v1 载荷（按 team_terminal_facts 校验合同构造）。"""
    from custom_backend.team_flow_commit import (  # noqa: SLF001 - P13 公式复用
        _opinion_from_row,
        _owner_username,
        _task_block,
    )
    rationale = next((item["rationale"] for item in resolutions
                      if item["zotero_key"] == key), "")
    final_decision = next(item["decision"] for item in resolutions
                          if item["zotero_key"] == key)
    opinions = []
    for uid in required_uids:
        entry = opinion_rows.get(uid) or {}
        raw = entry.get("raw_row") or {}
        row = {
            "decision": entry.get("decision") or "",
            "exclusion_reason": entry.get("exclusion_reason") or "",
            "notes": entry.get("notes") or "",
            "tags": raw.get("tags") or "",
            "screened_at": raw.get("screened_at"),
            "updated_at": raw.get("updated_at"),
            # ledger revision（票面行内）：_opinion_from_row 由此取 revision。
            "revision": entry.get("revision") or raw.get("revision") or 1,
        }
        opinions.append(_opinion_from_row(
            uid, entry.get("library_id") or "", row))
    return {
        "schema": "team_terminal_seal.v1",
        "task": _task_block(inputs, inputs.plan.binding, _owner_username(inputs)),
        "stage": stage,
        "item_key": key,
        "round_id": None,
        "kind": "adjudicated",
        "trigger": "arbitration",
        "final_decision": final_decision,
        "roster": roster,
        "required_uids": list(required_uids),
        "opinions": opinions,
        "arbitration": {
            "arbitrator_user_id": int(arbitrator_user_id),
            "arbitrator_role": "reconciler",
            "record_id": record_id,
            "attestation_id": attestation_id,
            "attested_at": attested_at,
            "resolutions_digest":
                attestation["attestation"]["resolutions_digest"],
            "result_sha256": attestation["record"]["result_sha256"],
            "attest_sha256": attestation["attestation"]["attest_sha256"],
            "rationale": rationale,
            "input_proof": {key: ready_entry["attestation"]},
        },
        "protocol": {"source_registration_id": None, "roster_revision": None},
        "source_request": {
            "operation": "arbitration_finish",
            "request_id": request_id,
            "request_sha256": request_sha256,
        },
        "sealed_at": attested_at,
    }


def run_team_arbitration_decide(
    runtime, info, user, *, stage: str, item_key: str, decision, reason,
    expected_key_version: str, request_id: str, request_sha256: str,
) -> Mapping:
    """decide 协调器：同一次 collab commit 内 open（首建/恢复）后 decide。"""
    from coscreen.team_flow_types import TeamOperation
    from coscreen.team_flow_receipts import append_receipt_on, read_receipt_on
    from custom_backend.team_screening_write import retained_team_write_inputs
    from custom_backend.team_flow_commit import (  # noqa: SLF001 - P13 helper 只读复用
        _roster_and_required,
        _usernames,
    )

    user_id = int(user["user_id"])
    op_key = _team_decide_receipt_op(stage)
    session_mod.prepare_team_session_storage(str(_store_path_of(info)))
    window_inputs = None
    body_completed = False
    payload = None
    try:
        with retained_team_write_inputs(
                runtime, info, dict(user), target_kind="collab",
                operation=TeamOperation(
                    name=_TEAM_OPERATION_NAME,
                    stage=_TEAM_OPERATION_STAGE[stage]),
                item_keys=(item_key,)) as inputs:
            window_inputs = inputs
            instance = inputs.plan.binding.task_instance_id
            existing = read_receipt_on(
                inputs.target_conn, task_instance_id=instance,
                actor_user_id=user_id, operation=op_key, request_id=request_id)
            if existing is not None:
                if existing["request_sha256"] != request_sha256:
                    from coscreen.team_flow_receipts import TeamFlowReceiptConflict
                    raise TeamFlowReceiptConflict(
                        "receipt already exists for this scope"
                        " with different content")
                # 幂等重放与首提交同形状（P16 member_mutate 解信封惯例）：
                # 回执 result.writer 即原业务载荷。
                prior = existing["result"]
                prior_writer = (prior.get("writer")
                                if isinstance(prior, Mapping) else None)
                payload = {
                    "outcome": "committed", "idempotent": True,
                    "request_id": request_id,
                    "result": prior_writer if prior_writer is not None
                    else prior,
                    "receipt": existing, "seals": (),
                }
            else:
                state = inputs.scope.role_state()
                _require_reconciler_entry(state, user_id)
                usernames = _usernames(inputs)
                roster, required_uids = _roster_and_required(inputs)
                required = {uid: usernames.get(uid, "")
                            for uid in required_uids}
                conns = _window_member_connections(inputs, required)
                snapshot = _enumerate_ready_snapshot(
                    conns, required, inputs.collab_conn,
                    stage=stage, task_instance_id=instance)
                ready = snapshot["ready"]
                if item_key not in ready:
                    raise _TeamArbitrationHttp(
                        409, "team_arbitration_key_not_ready",
                        "team.arbitration_key_not_ready",
                        "该文献当前不在团队仲裁就绪集"
                        "（全员完成分歧+上游资格）中。",
                        params={"zotero_key": item_key,
                                "reason": snapshot["blocked"].get(
                                    item_key, "not_conflict")})
                open_payload = session_mod.open_team_session_on(
                    inputs.target_conn,
                    task_id=inputs.plan.binding.task_id,
                    task_instance_id=instance, stage=stage, round_id=None,
                    actor_user_id=user_id, item_keys=sorted(ready),
                    input_proofs=ready)
                session_id = open_payload["session_id"]
                decide_payload = session_mod.decide_team_item_on(
                    inputs.target_conn, task_instance_id=instance, stage=stage,
                    actor_user_id=user_id, session_id=session_id,
                    item_key=item_key, decision=decision, reason=reason,
                    expected_key_version=expected_key_version)
                entry = ready[item_key]
                decide_payload["reveal"] = {
                    "zotero_key": item_key,
                    "decision": decide_payload["decision"],
                    "reason": decide_payload["reason"],
                    "decided_at": decide_payload["decided_at"],
                    # decide 刚成功＝该键已决；已决路径照常带完整人票理由
                    # （盲到底剥离只作用于未决条目——见 helper 契约）。
                    "screeners": _team_screeners_payload(
                        required, entry["opinions"], item_decided=True),
                }
                decide_payload["session"] = open_payload
                receipt = append_receipt_on(inputs.target_conn, {
                    "schema": "team_flow_receipt.v1",
                    "task_instance_id": instance,
                    "actor_user_id": user_id,
                    "operation": op_key,
                    "request_id": request_id,
                    "request_sha256": request_sha256,
                    "result": {
                        "request_id": request_id, "operation": op_key,
                        "item_keys": [item_key],
                        # D2：writer=完整业务载荷——幂等重放与首提交
                        # 逐字段同形（P16 双路径统一惯例）。
                        "writer": decide_payload,
                        "seals": [],
                    },
                    "internal_evidence": {
                        "target_kind": "collab", "idempotent": False},
                    "recorded_at": _utc_now_iso(),
                    "source_registration_id": None, "source_generation": None,
                })
                payload = {
                    "outcome": "committed", "idempotent": False,
                    "request_id": request_id, "result": decide_payload,
                    "receipt": receipt, "seals": (),
                }
            body_completed = True
    except BaseException as exc:  # noqa: BLE001 - body 成功+退出失败=unknown
        if body_completed:
            cleanup = (window_inputs.cleanup_errors
                       if window_inputs is not None else ())
            return _unknown_outcome(request_id, exc, cleanup)
        raise
    cleanup = window_inputs.cleanup_errors if window_inputs is not None else ()
    payload["cleanup_errors"] = tuple(cleanup)
    return payload


def run_team_arbitration_finish(
    runtime, info, user, *, stage: str, item_keys, expected_key_versions,
    request_id: str, request_sha256: str,
) -> Mapping:
    """finish 协调器：五工件一次 collab commit（generic+attestation+F1+
    session+receipt）。

    ``item_keys`` 为 None＝旧 UI 兼容：服务端取「本会话已决定且仍就绪」的
    键（仍就绪＝当前快照仍为 ready 冲突——版本不豁免）；空集拒绝（409）。
    绝不允许 generic 先提交后再另开 session 事务——全部 append 都在同一
    P10 collab 屏障事务内，由窗口做唯一目标 commit。
    """
    from coscreen.team_flow_types import TeamOperation
    from coscreen.team_flow_receipts import (
        append_receipt_on,
        prepare_team_flow_receipts,
        read_receipt_on,
    )
    from coscreen.team_terminal_facts import (
        append_seal_on,
        prepare_team_terminal_facts,
    )
    from coscreen.screening_arbitration import (
        append_decision_arbitration_attested,
        prepare_arbitration_attestation_storage,
    )
    from custom_backend.team_screening_write import retained_team_write_inputs
    from custom_backend.team_flow_commit import (  # noqa: SLF001 - P13 helper 只读复用
        _roster_and_required,
        _task_block,
        _usernames,
    )

    user_id = int(user["user_id"])
    op_key = _team_finish_receipt_op(stage)
    store_path = str(_store_path_of(info))
    # C05 显式准备（窗口外、幂等）：team 会话表+attestation+F1+receipts。
    session_mod.prepare_team_session_storage(store_path)
    prepare_arbitration_attestation_storage(store_path)
    prepare_team_terminal_facts(store_path)
    prepare_team_flow_receipts(store_path)

    window_keys = tuple(dict.fromkeys(item_keys)) if item_keys is not None else None
    if window_keys is None:
        window_keys = _session_decided_keys_ro(store_path, stage, user_id)
        if not window_keys:
            raise _TeamArbitrationHttp(
                409, "team_arbitration_finish_empty",
                "team.arbitration_finish_empty",
                "本会话没有可提交的已决定且仍就绪的键（空集拒绝）。")

    window_inputs = None
    body_completed = False
    payload = None
    try:
        with retained_team_write_inputs(
                runtime, info, dict(user), target_kind="collab",
                operation=TeamOperation(
                    name=_TEAM_OPERATION_NAME,
                    stage=_TEAM_OPERATION_STAGE[stage]),
                item_keys=window_keys) as inputs:
            window_inputs = inputs
            instance = inputs.plan.binding.task_instance_id
            existing = read_receipt_on(
                inputs.target_conn, task_instance_id=instance,
                actor_user_id=user_id, operation=op_key, request_id=request_id)
            if existing is not None:
                if existing["request_sha256"] != request_sha256:
                    from coscreen.team_flow_receipts import TeamFlowReceiptConflict
                    raise TeamFlowReceiptConflict(
                        "receipt already exists for this scope"
                        " with different content")
                # 幂等重放与首提交同形状（P16 member_mutate 解信封惯例）：
                # 回执 result.writer 即原业务载荷。
                prior = existing["result"]
                prior_writer = (prior.get("writer")
                                if isinstance(prior, Mapping) else None)
                payload = {
                    "outcome": "committed", "idempotent": True,
                    "request_id": request_id,
                    "result": prior_writer if prior_writer is not None
                    else prior,
                    "receipt": existing, "seals": (),
                }
            else:
                state = inputs.scope.role_state()
                _require_reconciler_entry(state, user_id)
                usernames = _usernames(inputs)
                roster, required_uids = _roster_and_required(inputs)
                required = {uid: usernames.get(uid, "")
                            for uid in required_uids}
                conns = _window_member_connections(inputs, required)
                snapshot = _enumerate_ready_snapshot(
                    conns, required, inputs.collab_conn,
                    stage=stage, task_instance_id=instance)
                ready = snapshot["ready"]

                current_session = session_mod.team_session_overview_on(
                    inputs.target_conn, task_instance_id=instance,
                    stage=stage, actor_user_id=user_id)
                if current_session is None:
                    raise session_mod.ArbitrationSessionNotOpen(
                        "team 仲裁会话尚未开始，请先裁决至少一条冲突。")
                session_id = current_session["session_id"]
                decided_rows = {item["zotero_key"]: item
                                for item in current_session["items"]}

                if item_keys is None:
                    finish_keys = [key for key in current_session["pending_keys"]
                                   if key in ready]
                else:
                    finish_keys = list(window_keys)
                    unknown = [key for key in window_keys
                               if key not in decided_rows]
                    if unknown:
                        raise session_mod.ArbitrationSessionItemNotFound(
                            "部分键不在本会话中。",
                            params={"zotero_key": unknown[0]})
                not_ready = [key for key in finish_keys if key not in ready]
                if not_ready:
                    raise _TeamArbitrationHttp(
                        409, "team_arbitration_key_not_ready",
                        "team.arbitration_key_not_ready",
                        "部分键当前不在就绪集（全员完成分歧+上游资格）中，"
                        "请刷新后重发。",
                        params={"stale_keys": sorted(not_ready),
                                "reasons": {key: snapshot["blocked"].get(
                                    key, "not_conflict")
                                    for key in sorted(not_ready)}})
                if not finish_keys:
                    raise _TeamArbitrationHttp(
                        409, "team_arbitration_finish_empty",
                        "team.arbitration_finish_empty",
                        "没有既已决定又仍就绪的键可提交（空集拒绝）。")
                undecided = [key for key in finish_keys
                             if decided_rows[key]["status"] == "pending"]
                if undecided:
                    raise session_mod.ArbitrationSessionIncomplete(
                        "仍有未裁决键混入 finish 请求。",
                        params={"undecided": undecided})

                open_payload = session_mod.open_team_session_on(
                    inputs.target_conn,
                    task_id=inputs.plan.binding.task_id,
                    task_instance_id=instance, stage=stage, round_id=None,
                    actor_user_id=user_id, item_keys=sorted(ready),
                    input_proofs=ready)
                stored_versions = {item["zotero_key"]: item["key_version"]
                                   for item in open_payload["items"]}
                if expected_key_versions is not None:
                    expected = dict(expected_key_versions)
                else:
                    expected = {key: stored_versions.get(key, "")
                                for key in finish_keys}

                # 工件 1+2：generic record + attestation（P05 attested append，
                # 同一借用事务；merged 只含本次 finish 键的 conflict 行）。
                merged = _merged_frame(finish_keys, ready, required)
                resolutions = [
                    {"zotero_key": key,
                     "decision": decided_rows[key]["decision"],
                     "rationale": _decided_reason_on(
                         inputs.target_conn, session_id, key)}
                    for key in finish_keys]
                attestation_proof = {
                    key: ready[key]["attestation"] for key in finish_keys}
                attested = append_decision_arbitration_attested(
                    inputs.target_conn, stage=stage, merged=merged,
                    resolutions=resolutions, arbitrator_id=user_id,
                    attestation_meta={
                        "task_id": inputs.plan.binding.task_id,
                        "round": None,
                        "required_names": [required[uid]
                                           for uid in sorted(required)],
                        "arbitrator_user_id": user_id,
                        "arbitrator_role": "reconciler",
                        "input_proof": attestation_proof,
                    })
                record_id = int(attested["record_id"])
                attestation_id = int(attested["attestation_id"])
                attested_at = attested["attestation"]["attested_at"]

                # 工件 3：F1 adjudicated seal（每键一枚；sealed_at=attested_at）。
                sealed_summary = []
                for key in finish_keys:
                    payload_seal = _adjudicated_seal_payload(
                        inputs=inputs, stage=stage, key=key,
                        ready_entry=ready[key], required=required,
                        roster=roster, required_uids=required_uids,
                        resolutions=resolutions, record_id=record_id,
                        attestation_id=attestation_id, attested_at=attested_at,
                        attestation=attested, request_id=request_id,
                        request_sha256=request_sha256,
                        opinion_rows=snapshot["votes"][stage + ":" + key],
                        arbitrator_user_id=user_id)
                    record = append_seal_on(inputs.target_conn, payload_seal,
                                            origin="collab")
                    sealed_summary.append({
                        "kind": "adjudicated", "trigger": "arbitration",
                        "stage": stage, "item_key": key,
                        "seal_id": record["seal_id"],
                    })

                # 工件 4：session finished_keys/状态（同事务）。
                finish_payload = session_mod.finish_team_keys_on(
                    inputs.target_conn, task_instance_id=instance,
                    stage=stage, actor_user_id=user_id, session_id=session_id,
                    item_keys=finish_keys, expected_key_versions=expected,
                    record_id=record_id, attestation_id=attestation_id)

                # 首提交业务载荷（D2：回执 writer 与重放 result 同此对象）。
                result_payload_body = {
                    "session_id": session_id, "stage": stage,
                    "finished_keys": list(finish_keys),
                    "pending_keys": finish_payload["pending_keys"],
                    "record_id": record_id,
                    "attestation_id": attestation_id,
                    "session": finish_payload,
                }

                # 工件 5：receipt（同事务收尾）。
                receipt = append_receipt_on(inputs.target_conn, {
                    "schema": "team_flow_receipt.v1",
                    "task_instance_id": instance,
                    "actor_user_id": user_id,
                    "operation": op_key,
                    "request_id": request_id,
                    "request_sha256": request_sha256,
                    "result": {
                        "request_id": request_id, "operation": op_key,
                        "item_keys": list(finish_keys),
                        # D2：writer=完整业务载荷（含 pending_keys）——
                        # 幂等重放与首提交逐字段同形。
                        "writer": result_payload_body,
                        "seals": sealed_summary,
                    },
                    "internal_evidence": {
                        "target_kind": "collab", "idempotent": False},
                    "recorded_at": _utc_now_iso(),
                    "source_registration_id": None, "source_generation": None,
                })
                payload = {
                    "outcome": "committed", "idempotent": False,
                    "request_id": request_id,
                    "result": result_payload_body,
                    "receipt": receipt,
                    "seals": tuple(sealed_summary),
                }
            body_completed = True
    except BaseException as exc:  # noqa: BLE001 - body 成功+退出失败=unknown
        if body_completed:
            cleanup = (window_inputs.cleanup_errors
                       if window_inputs is not None else ())
            return _unknown_outcome(request_id, exc, cleanup)
        raise
    cleanup = window_inputs.cleanup_errors if window_inputs is not None else ()
    payload["cleanup_errors"] = tuple(cleanup)
    return payload


class TeamDecideIn(BaseModel):
    stage: Literal["stage1", "stage2"]
    zotero_key: str = Field(min_length=1, max_length=300)
    decision: str | None = Field(default="")
    reason: str | None = Field(default=None, max_length=100_000)
    expected_key_version: str = Field(min_length=0, max_length=128)


class TeamFinishIn(BaseModel):
    stage: Literal["stage1", "stage2"]
    item_keys: list[str] | None = Field(default=None)
    expected_key_versions: dict[str, str] | None = Field(default=None)


def register_team_arbitration_routes(
    app,
    require_user,
    task_lookup,
    collaboration_store,
    is_team_task,
    bad,
    unavailable_message,
    runtime_getter=None,
    username_of=None,
) -> None:
    """注册 team 逐条仲裁端点（P19；独立守卫；不动 legacy 注册）。

    ``username_of(uid)`` 由宿主注入（accounts/users.db 只读解析）——
    active_screening_roster/role_state 均不带 username（调用方 enrich 契约），
    成员库路径 ``<slug(username)>.db`` 依赖它解析。"""
    from custom_backend.workspace_undo_api import member_request_fingerprint
    _STORE_PATH_RESOLVER["collaboration_store"] = collaboration_store

    def _context(task_id: str, user: dict):
        info = task_lookup(task_id)
        if not is_team_task(info):
            raise bad("团队逐条仲裁仅对 team 任务开放。", 404)
        return info

    def _runtime(info):
        return (runtime_getter or (lambda _info: app.state.review_workflow))(info)

    def _authorized_ro(info, user):
        from coscreen import task_collaboration as _collab
        store = collaboration_store(info)
        conn = _ro_collab(store)
        try:
            state = _collab.read_team_role_state_on(conn, task_id=info.task_id)
        finally:
            conn.close()
        _require_reconciler_entry(state, int(user["user_id"]))
        roster = _collab.active_screening_roster(store, task_id=info.task_id) or []
        by_uid = {int(entry.get("user_id") or 0): entry for entry in roster}
        required = {}
        for entry in _required_members(state):
            uid = int(entry["user_id"])
            name = ""
            if username_of is not None:
                try:
                    name = str(username_of(uid) or "")
                except Exception:  # noqa: BLE001 - 账号解析失败＝缺席
                    name = ""
            if not name:
                name = str((by_uid.get(uid) or {}).get("username")
                           or entry.get("username") or "")
            required[uid] = name or str(uid)
        return required

    @app.get(_TEAM_BASE + "/session")
    def get_team_arbitration_session(
        task_id: str,
        stage: Literal["stage1", "stage2"] = "stage1",
        user: dict = Depends(require_user),
    ) -> dict:
        """只读 ready preview：per-key 版本 + finished/pending；不隐式 open。"""
        from contextlib import closing as _closing
        info = _context(task_id, user)
        store = collaboration_store(info)
        required = _authorized_ro(info, user)
        instance = _instance_id_ro(store)
        conns, close_all = _ro_member_connections(info.dir, required)
        try:
            collab_ro = _ro_collab(store)
            try:
                snapshot = _enumerate_ready_snapshot(
                    conns, required, collab_ro,
                    stage=stage, task_instance_id=instance)
                overview = session_mod.team_session_overview_on(
                    collab_ro, task_instance_id=instance, stage=stage,
                    actor_user_id=int(user["user_id"]))
            finally:
                collab_ro.close()
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001 - 只读路径统一映射
            close_all()
            raise _team_http_for(exc, unavailable_message, bad) from exc
        close_all()
        ready = snapshot["ready"]
        blocked = snapshot["blocked"]
        decided_status = {item["zotero_key"]: item["status"]
                          for item in (overview or {}).get("items", [])}
        keys: dict = {}
        for key in sorted(set(ready) | set(blocked)):
            if key in ready:
                revision = 0
                if overview is not None \
                        and decided_status.get(key) == "decided":
                    revision = _stored_revision(
                        store, overview["session_id"], key)
                keys[key] = {
                    "ready": True,
                    "status": decided_status.get(key, "pending"),
                    "key_version": session_mod.team_key_version(
                        task_id=info.task_id, task_instance_id=instance,
                        stage=stage, item_key=key,
                        actor_user_id=int(user["user_id"]),
                        proof_entry=ready[key],
                        decision_revision=revision, finished=False),
                }
            else:
                keys[key] = {"ready": False, "reason": blocked[key]}
        return {
            "task_id": info.task_id,
            "stage": stage,
            "session_id": (overview or {}).get("session_id"),
            "status": (overview or {}).get("status"),
            "ready_keys": sorted(ready),
            "blocked": {key: blocked[key] for key in sorted(blocked)},
            "finished_keys": (overview or {}).get("finished_keys", []),
            "pending_keys": (overview or {}).get("pending_keys", []),
            "keys": keys,
        }

    @app.post(_TEAM_BASE + "/session/item")
    def decide_team_arbitration_item(
        task_id: str,
        body: TeamDecideIn,
        x_request_id: str | None = Header(default=None, max_length=128),
        user: dict = Depends(require_user),
    ) -> dict:
        """首次合法 decide POST 在同次 collab 事务 open 后 decide。"""
        info = _context(task_id, user)
        payload_id, payload_sha = member_request_fingerprint(
            x_request_id, {"task_id": info.task_id, "stage": body.stage,
                           "route": "team-arbitration-decide",
                           "zotero_key": body.zotero_key,
                           "decision": body.decision, "reason": body.reason,
                           "expected_key_version": body.expected_key_version})
        try:
            outcome = run_team_arbitration_decide(
                _runtime(info), info, user, stage=body.stage,
                item_key=body.zotero_key, decision=body.decision,
                reason=body.reason,
                expected_key_version=body.expected_key_version,
                request_id=payload_id, request_sha256=payload_sha)
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001 - 统一结构化映射
            raise _team_http_for(exc, unavailable_message, bad) from exc
        if outcome.get("outcome") == "unknown":
            raise _TeamArbitrationHttp(
                409, "team_flow_unknown_outcome",
                "team.flow.unknown_outcome",
                "提交结果不可判定（commit 阶段故障）；请勿重放写请求，"
                "刷新会话状态后再继续。",
                params={"request_id": payload_id})
        return outcome["result"]

    @app.get(_TEAM_BASE + "/session/reveal")
    def get_team_arbitration_reveal(
        task_id: str,
        stage: Literal["stage1", "stage2"] = "stage1",
        zotero_key: str = Query(min_length=1, max_length=300),
        expected_key_version: str = Query(min_length=0, max_length=128),
        session_id: str | None = Query(default=None),
        user: dict = Depends(require_user),
    ) -> dict:
        """reveal：只读；已裁决才返回该键结论+人票（只含本键，先判后示）。"""
        info = _context(task_id, user)
        store = collaboration_store(info)
        required = _authorized_ro(info, user)
        instance = _instance_id_ro(store)
        conns, close_all = _ro_member_connections(info.dir, required)
        try:
            collab_ro = _ro_collab(store)
            try:
                snapshot = _enumerate_ready_snapshot(
                    conns, required, collab_ro,
                    stage=stage, task_instance_id=instance)
                votes = snapshot["votes"].get(stage + ":" + zotero_key, {})
                if not session_id:
                    raise session_mod.ArbitrationSessionRevealNotReady(
                        "该文献尚未裁决，裁决后才能查看各筛选员的原始决定。",
                        params={"zotero_key": zotero_key})
                base = session_mod.reveal_team_item_on(
                    collab_ro, task_instance_id=instance, stage=stage,
                    actor_user_id=int(user["user_id"]),
                    session_id=session_id, item_key=zotero_key,
                    expected_key_version=expected_key_version)
            finally:
                collab_ro.close()
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001 - 只读路径同样统一映射
            close_all()
            raise _team_http_for(exc, unavailable_message, bad) from exc
        close_all()
        # 盲到底（包 E）：screeners 附件本身按条目状态条件携带理由——
        # reveal_team_item_on 已保证走到这里的键必然已决（item_decided
        # 恒真），此处再设一道载荷层闸：未决条目结构性缺席 reason。
        base["screeners"] = _team_screeners_payload(
            required, votes, item_decided=bool(base.get("decision")))
        return base

    @app.post(_TEAM_BASE + "/session/finish")
    def finish_team_arbitration_keys(
        task_id: str,
        body: TeamFinishIn,
        x_request_id: str | None = Header(default=None, max_length=128),
        user: dict = Depends(require_user),
    ) -> dict:
        """五工件（generic+attestation+F1+session+receipt）一次 collab commit。"""
        info = _context(task_id, user)
        payload_id, payload_sha = member_request_fingerprint(
            x_request_id, {"task_id": info.task_id, "stage": body.stage,
                           "route": "team-arbitration-finish",
                           "item_keys": body.item_keys,
                           "expected_key_versions": body.expected_key_versions})
        try:
            outcome = run_team_arbitration_finish(
                _runtime(info), info, user, stage=body.stage,
                item_keys=body.item_keys,
                expected_key_versions=body.expected_key_versions,
                request_id=payload_id, request_sha256=payload_sha)
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise _team_http_for(exc, unavailable_message, bad) from exc
        if outcome.get("outcome") == "unknown":
            raise _TeamArbitrationHttp(
                409, "team_flow_unknown_outcome",
                "team.flow.unknown_outcome",
                "提交结果不可判定（commit 阶段故障）；请勿重放写请求，"
                "刷新会话状态后再继续。",
                params={"request_id": payload_id})
        return outcome["result"]
