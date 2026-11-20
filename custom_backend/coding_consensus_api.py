"""Task API routes for persistent independent coding and consensus.

The host must supply a task-scoped shared DB path and an authorization callback.
The existing per-user screener DB is not a shared consensus store.

2.1.6 P17：team 任务的 rater/consensus/freeze 写改经 P15
``app.state.review_workflow.mutate`` 真实 team 门（coder 走
``coding_rater_submit``=collab 目标；reconciler 走
``coding_consensus_append``/``coding_consensus_freeze``=collab 目标，权限仍
是原 ``coding:submit``/``consensus:write``/``consensus:freeze`` action，不加
个人写 policy——C08 addendum 口径）。writer 在保留窗口内收到绑定的
``ctx.write_inputs``，存储写经 ``conn=inputs.collab_conn``（与检查/提交目标
同一对象，绝不重连）；freeze 的全部 consensus ID 在同一把 collab 保留连接上
解析真实键域，整批通过才写，单窗口整批提交（不逐键开短窗放锁再写）。
personal/legacy 任务保持原 write_guard/guarded_consensus_write 路径零变化。
"""

from __future__ import annotations

import hashlib
import json
import uuid
from contextlib import closing
from typing import Any, Callable

from fastapi import Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from coscreen import coding_consensus
from coscreen.coding_agreement import summarize_agreement


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


#: 编码共识端点的请求体大小上限（2MB；字段本身有 max_length，此处挡无界
#: value/source/ID 列表的整包放大）。
MAX_CONSENSUS_BODY_BYTES = 2 * 1024 * 1024


async def _body_within_limit(request: Request) -> None:
    # 先按 Content-Length 提前拒绝超限请求；头部缺失/非法（chunked 传输）
    # 时回退到读取实体后的精确校验（MM2-03/MM4-04：缺头曾完全不设卡——
    # 同一 3MB 体，带 Content-Length 时 413、chunked 时放行）。口径与
    # report_evidence_api.size_guard 一致。
    header = request.headers.get("content-length")
    if header:
        try:
            length = int(header)
        except ValueError:
            length = None
        if length is not None and length > MAX_CONSENSUS_BODY_BYTES:
            raise HTTPException(413, "Request body exceeds the coding consensus limit (2 MB).")
    if len(await request.body()) > MAX_CONSENSUS_BODY_BYTES:
        raise HTTPException(413, "Request body exceeds the coding consensus limit (2 MB).")


class RaterRecordIn(_Input):
    expected_policy_version: int | None = Field(default=None, ge=1)
    item_key: str = Field(min_length=1, max_length=300)
    field_key: str = Field(min_length=1, max_length=200)
    value: Any
    source: dict[str, Any]
    rationale: str = Field(default="", max_length=4000)


class ConsensusRecordIn(RaterRecordIn):
    rationale: str = Field(min_length=1, max_length=4000)
    rater_record_ids: list[int] = Field(default_factory=list, max_length=10000)


class FreezeIn(_Input):
    name: str = Field(min_length=1, max_length=200)
    consensus_ids: list[int] = Field(min_length=1, max_length=10000)


class AgreementIn(_Input):
    field_key: str = Field(min_length=1, max_length=200)
    rater_ids: tuple[str, str]
    absolute_tolerance: Any = None


# ---------------------------------------------------------------------------
# P17 team 写路径辅助（模块级，供三个写端点共用；legacy 路径零变化）
# ---------------------------------------------------------------------------
def _team_runtime(app: Any):
    """P15 ``WorkflowRuntime`` 惰性解析（宿主注册 ``app.state`` 后即生效）。"""
    return getattr(app.state, "review_workflow", None)


def _is_team_task(runtime: Any, task_id: str) -> bool:
    """team 判定只认可信派生（P15 ``is_team``）；解析失败退回 legacy 路径。"""
    try:
        return bool(runtime.is_team(task_id))
    except HTTPException:
        raise
    except Exception:  # noqa: BLE001 —— 无协作库/旧宿主：保持 legacy 行为
        return False


def _team_request_id(prefix: str) -> str:
    return f"{prefix}:{uuid.uuid4().hex}"


def _team_request_sha256(payload: dict) -> str:
    """幂等摘要（canonical JSON→SHA-256；仅作 receipt 一致性键，非业务校验）。"""
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _team_unknown_outcome_http(envelope: Any) -> HTTPException:
    """S2：``outcome != 'committed'`` 的 mutate 信封 → 显式结构化 409。

    unknown（提交结果不可判定，可能已提交）绝不能以 200+null 吞掉：统一按
    团队结构化错误惯例给出 typed 载荷 + 可重试提示——幂等 receipt 保证用
    **相同 request_id** 重试要么拿到原结果、要么确认提交，无双写。
    """
    request_id = envelope.get("request_id") if isinstance(envelope, dict) else None
    details = (envelope.get("error") if isinstance(envelope, dict) else None
               ) or "commit outcome could not be determined"
    return HTTPException(409, {
        "code": "team_flow_unknown_outcome",
        "message_key": "team.flow.unknown_outcome",
        "params": {"request_id": request_id},
        "details": details,
        "message": "提交结果暂不可判定（可能已提交）；请使用相同请求编号重试以确认结果。",
        "retryable": True,
    })


def _team_writer_result(envelope: Any, response=None) -> Any:
    """从 mutate 信封取回 writer 原结果（幂等重放同形），保持业务返回兼容。

    非 committed outcome（unknown）显式上抛 :func:`_team_unknown_outcome_http`
    （S2），绝不返回 null。
    P24/S3：传入 FastAPI ``Response`` 时把 P15 信封头（X-Request-Id /
    X-Receipt-Id / X-Idempotent-Replay）透传到真实 HTTP 响应——P17 四操作
    （rater/consensus/freeze/appraisal）的回执头接线义务，仅 committed 才附。
    """
    if not isinstance(envelope, dict) or envelope.get("outcome") != "committed":
        raise _team_unknown_outcome_http(envelope)
    if response is not None:
        headers = envelope.get("headers")
        if isinstance(headers, dict):
            for name, value in headers.items():
                response.headers[name] = str(value)
    result = envelope.get("result")
    if isinstance(result, dict) and "writer" in result:
        return result["writer"]
    return result


def _team_drift_http(task_id: str, exc: Exception) -> HTTPException:
    """S4：模式漂移字面信号 → 结构化可重试 409（对齐 P15 形状）。

    team 判定与写窗之间的竞态（协作库正被重配/属主变更/读失败）会以
    ``ValueError`` 冲出 mutate 边界——零写 fail-closed 但形状裸（500）。
    参照 ``_team_write_blocked_http`` 的载荷模式映射为 ``task_mode_changed``
    （与 P15 预飘移同码）+ 重试提示；幂等 receipt 使重试安全。unknown
    结果不经此路径（S2 单独显式上抛），确定性 typed 错误也不经此路径
    （见 :func:`_team_typed_unavailable_http`）。
    """
    return HTTPException(409, {
        "code": "task_mode_changed",
        "message_key": "team.flow.task_mode_changed",
        "params": {"task_id": task_id},
        "details": f"{type(exc).__name__}: {exc}",
        "message": "任务协作模式已变化，请重试本次操作。",
        "retryable": True,
    })


def _is_team_drift_signal(exc: Exception) -> bool:
    """P15-D1 口径：只有开窗处两条稳定字面信号才算可重试漂移。

    ``team_screening_write.retained_team_write_inputs`` 在 yield 前抛出
    的「不是 team 任务」系 ValueError（writer 从未运行、零业务写）＝
    is_team 判定与开窗之间的任务模式漂移，重试会按当前可信状态重新
    路由；其余 ValueError（键形状/域外键等请求自身错误）同请求重试
    永不成功，不在此列。
    """
    if not isinstance(exc, ValueError):
        return False
    message = str(exc)
    return ("requires a team task" in message
            or "canonical task lookup returned no task_id" in message)


def _team_typed_unavailable_http(exc: Exception) -> HTTPException | None:
    """typed 团队错误 → 结构化响应（P15-D1/P17 同族）。

    包 M（承包 J 复审观察②）：``TeamFlowReceiptUnavailable("not_prepared")``
    是确定性存储未准备错误，旧兜底把它标成 409 ``task_mode_changed`` +
    ``retryable: true``，客户端重试永不成功。这里如实映射：
    - ``TeamFlowReceiptUnavailable`` → 非可重试 409（``error_code``/
      ``message_key`` 同族，``retryable: false``，params 带 reason）。
      与 appraisal 路由族同口径：未准备属于预期前置条件未满足，同请求
      重试永不成功（只有宿主先跑准备生命周期才可能恢复）。
    - ``TeamFlowUnavailableError`` → P15 ``_mutate_team`` 同款 typed 503
      载荷（status/error_code/message_key/params/details/message 取自
      异常；该类语义是「暂时不可读，稍后重试」，与 P15/P17 一致不投
      ``retryable`` 键）。
    返回 None＝非本层可判定的异常，调用方按既有全局处理（绝不伪造 409）。
    """
    from coscreen.team_flow_receipts import TeamFlowReceiptUnavailable
    from custom_backend.team_screening_runtime import TeamFlowUnavailableError

    if isinstance(exc, TeamFlowReceiptUnavailable):
        if getattr(exc, "reason", None) == "connection_contract":
            return None  # 借用合同违约＝编程错误，如实上抛不伪装业务态
        return HTTPException(409, {
            "error_code": "team_flow_receipt_unavailable",
            "message_key": "team.flow.receipt_unavailable",
            "params": {"reason": getattr(exc, "reason", "unreadable")},
            "details": str(exc),
            "message": "团队回执存储未就绪或不可用，重试同一请求不会成功。",
            "retryable": False,
        })
    if isinstance(exc, TeamFlowUnavailableError):
        return HTTPException(getattr(exc, "status", 503), {
            "error_code": getattr(exc, "code", "team.flow.unavailable"),
            "message_key": getattr(exc, "message_key",
                                   "team.flow.unavailable"),
            "params": getattr(exc, "params", {}),
            "details": str(exc),
            "message": str(exc),
        })
    return None


def _freeze_item_keys_prewindow(path, consensus_ids) -> tuple[str, ...]:
    """freeze 的纯读预解析（窗口外，只读连接）：consensus ID → 条目键域。

    仅用于把键域交给 P15 ``mutate``（require_team_write 整批检查的请求键）；
    权威解析在写窗内经 :func:`_resolve_freeze_keys_on_collab` 于**同一把
    collab 保留连接**上重做并比对。任何 ID 不存在/表未准备 → 与存储层同文
    拒绝（400，零写入）。
    """
    ids = [int(value) for value in consensus_ids]
    try:
        with closing(coding_consensus._connect_read(path)) as conn:
            if not coding_consensus._table_exists(conn, "coding_consensus_records"):
                raise ValueError("all selected consensus records must exist")
            found = {}
            for artifact_id in ids:  # 常量 SQL 主键点查：全参数绑定，零拼接
                row = conn.execute(
                    "SELECT item_key FROM coding_consensus_records WHERE id=?",
                    (artifact_id,)).fetchone()
                if row is not None:
                    found[artifact_id] = row["item_key"]
    except FileNotFoundError as exc:
        raise ValueError("all selected consensus records must exist") from exc
    if any(artifact_id not in found for artifact_id in ids):
        raise ValueError("all selected consensus records must exist")
    return tuple(dict.fromkeys(found[artifact_id] for artifact_id in ids))


def _resolve_freeze_keys_on_collab(
    collab_conn, operation, consensus_ids, request_keys,
) -> tuple[str, ...]:
    """写窗内权威解析：全部 consensus ID 的真实键域（P11 footprint）。

    在与检查/提交同一把 collab 保留连接上逐 ID 解析并求并集；任一 ID 不能
    确认完整 footprint（缺行/结构未准备/键不合法）→ 整批拒绝（与存储层同
    文 400），零写入——绝不当空集放行，也不逐键放锁。
    """
    from custom_backend.team_write_footprints import mutation_footprint_on

    resolved: list[str] = []
    for artifact_id in (int(value) for value in consensus_ids):
        try:
            footprint = mutation_footprint_on(
                collab_conn, operation=operation, artifact_id=artifact_id,
                request_keys=request_keys)
        except Exception as exc:  # noqa: BLE001 —— 无法解析＝不能安全执行
            raise ValueError(
                "all selected consensus records must exist") from exc
        if not footprint.complete:
            raise ValueError("all selected consensus records must exist")
        resolved.extend(cell.item_key for cell in footprint.cells)
    return tuple(dict.fromkeys(resolved))


def register_coding_consensus_routes(
    app: Any,
    require_user: Callable[..., dict],
    task_or_404: Callable[[str], Any],
    store_path: Callable[[Any], str],
    authorize_task: Callable[[Any, dict, str], bool],
    validate_target: Callable[[Any, str, str], bool],
    bad: Callable[[Any], HTTPException],
    validate_value: Callable[[Any, str, Any], dict | None] | None = None,
    get_field: Callable[[Any, str], dict | None] | None = None,
    trace_context_factory: Callable[..., Any] | None = None,
) -> None:
    """Register routes; `authorize_task` must return True or the request is denied.

    Actions are `coding:submit`, `coding:read_all`, `consensus:write`,
    `consensus:read`, and `consensus:freeze`. `store_path` must return the
    shared task DB selected by the host. `validate_target` checks that the item
    and stable field key belong to the task. Actor IDs always come from `require_user`.
    """

    def context(
        task_id: str, user: dict, action: str,
        item_key: str | None = None, field_key: str | None = None,
    ):
        info = task_or_404(task_id)
        if authorize_task(info, user, action) is not True:
            raise HTTPException(403, "Task access denied.")
        if item_key is not None and field_key is not None and validate_target(info, item_key, field_key) is not True:
            raise HTTPException(400, "Coding item or field does not belong to this task.")
        if "user_id" not in user:
            raise HTTPException(401, "Authenticated user ID is missing.")
        return info, store_path(info), user["user_id"]

    def guarded_consensus_write(task_id: str, info, user: dict, action: str, write: Callable[[], Any]):
        """consensus/freeze 写路径的授权复查（U4-P5，对齐 rater 路径 write_guard 模式）。

        撤销端点（roles DELETE）在同一把 ``review_workflow.lock`` 内完成撤销；
        本路径在写前于同一把锁内复查 ``authorize_task``，使“授权读 -> 写入”
        与“撤销”串行化：context() 首次授权读之后被撤销的角色，写入被 403 拒绝。
        存储函数（append_consensus_record/freeze_consensus）自持连接并自行
        BEGIN IMMEDIATE，外层再持独立写事务会与存储写自锁，故按 rater 记录
        路径的同款进程内任务锁 + 锁内复查实现。
        """
        with app.state.review_workflow.lock(info.task_id):
            # R6-C3/#35（复审扫尾）：锁键用规范 task_id——尾点别名路径
            # （'T.'）曾与规范撤销（锁 'T'）分键不互斥，U4-P5 的角色撤销
            # TOCTOU 在别名路径重新打开。
            if authorize_task(info, user, action) is not True:
                raise HTTPException(403, "Task access denied.")
            return write()

    @app.post("/api/tasks/{task_id}/coding/records", status_code=201, dependencies=[Depends(_body_within_limit)])
    def append_rater_record(task_id: str, body: RaterRecordIn,
                          user: dict = Depends(require_user),
                          response: Response = None):
        info, path, actor_id = context(task_id, user, "coding:submit", body.item_key, body.field_key)
        # 2.1.6 P17：team 任务经 P15 mutate 真实 team 门（coder 业务，
        # coding_rater_submit=collab 目标，policy 门与旧 write_guard 同源）。
        runtime = _team_runtime(app)
        if runtime is not None and _is_team_task(runtime, task_id):
            try:
                return _rater_via_team(runtime, task_id, user, info, path,
                                       actor_id, body, response)
            except ValueError as exc:
                raise bad(str(exc)) from exc
        try:
            with app.state.review_workflow.write_guard(task_id, user, "coding", body.item_key,
                                                       body.expected_policy_version, canonical_only=True) as ctx:
                if validate_target(info, body.item_key, body.field_key) is not True:
                    raise HTTPException(400, 'Coding item or field does not belong to this task.')
                field = validate_value(info, body.field_key, body.value) if validate_value else None
                if validate_value and not isinstance(field, dict):
                    raise HTTPException(400, 'Value does not match the canonical coding field.')
                evidence = app.state.review_workflow.evidence_state(ctx, body.item_key)
                if not evidence['evidence_complete']:
                    raise HTTPException(409, {'code': 'coding_evidence_required',
                        'missing_evidence': evidence['missing_evidence'], 'mapping_issues': evidence['mapping_issues'],
                        'workflow_settings': app.state.review_workflow.settings(ctx)})
                return coding_consensus.append_rater_record(
                    path, item_key=body.item_key, field_key=body.field_key, rater_id=actor_id,
                    value=body.value, source={**body.source, "_reviewflow_field": field} if field else body.source,
                    rationale=body.rationale,
                    trace_context=trace_context_factory(info, user) if trace_context_factory else None,
                )
        except ValueError as exc:
            raise bad(str(exc)) from exc

    def _rater_via_team(runtime, task_id: str, user: dict, info, path, actor_id, body,
                        response=None):
        """team rater 写：单窗口（member 检查→writer→receipt）+ 保留 collab 连接。"""
        from coscreen.team_flow_types import TeamOperation

        operation = TeamOperation(name="coding_rater_submit", stage="coding",
                                  expected_version=body.expected_policy_version)

        def writer(ctx):
            inputs = ctx.write_inputs
            if validate_target(info, body.item_key, body.field_key) is not True:
                raise HTTPException(400, 'Coding item or field does not belong to this task.')
            field = validate_value(info, body.field_key, body.value) if validate_value else None
            if validate_value and not isinstance(field, dict):
                raise HTTPException(400, 'Value does not match the canonical coding field.')
            evidence = runtime.evidence_state(ctx, body.item_key)
            if not evidence['evidence_complete']:
                raise HTTPException(409, {'code': 'coding_evidence_required',
                    'missing_evidence': evidence['missing_evidence'], 'mapping_issues': evidence['mapping_issues'],
                    'workflow_settings': runtime.settings(ctx)})
            return coding_consensus.append_rater_record(
                path, item_key=body.item_key, field_key=body.field_key, rater_id=actor_id,
                value=body.value, source={**body.source, "_reviewflow_field": field} if field else body.source,
                rationale=body.rationale,
                trace_context=trace_context_factory(info, user) if trace_context_factory else None,
                conn=inputs.collab_conn)  # 与检查/提交目标同一保留连接，不重连

        try:
            envelope = runtime.mutate(
                task_id, user, operation=operation, item_keys=(body.item_key,),
                request_id=_team_request_id("rater"),
                request_sha256=_team_request_sha256(
                    {"item_key": body.item_key, "field_key": body.field_key,
                     "value": body.value, "source": body.source,
                     "rationale": body.rationale,
                     "expected_policy_version": body.expected_policy_version}),
                writer=writer)
        except HTTPException:
            raise  # P12/P15 结构化拒绝原样透传
        except ValueError as exc:
            # P15-D1 收窄（包 M）：只有开窗处两条漂移字面信号映射 409；
            # 其余 ValueError 是请求自身确定性错误，由路由层映射 400
            # （同请求重试永不成功，绝不标可重试 409 诱使徒劳重试）。
            if _is_team_drift_signal(exc):
                raise _team_drift_http(task_id, exc) from exc
            raise
        except Exception as exc:  # noqa: BLE001 —— typed 确定性 503；其余不兜底
            mapped = _team_typed_unavailable_http(exc)
            if mapped is None:
                raise
            raise mapped from exc
        return _team_writer_result(envelope, response)

    @app.get("/api/tasks/{task_id}/coding/records")
    def list_rater_records(
        task_id: str, item_key: str, field_key: str, rater_id: str | None = None,
        user: dict = Depends(require_user),
    ):
        info, path, _ = context(task_id, user, "coding:read_all", item_key, field_key)
        try:
            return {"records": coding_consensus.list_rater_records(
                path, item_key=item_key, field_key=field_key, rater_id=rater_id
            )}
        except ValueError as exc:
            raise bad(str(exc)) from exc

    @app.post("/api/tasks/{task_id}/coding/agreement", dependencies=[Depends(_body_within_limit)])
    def agreement(task_id: str, body: AgreementIn, user: dict = Depends(require_user)):
        info, path, _ = context(task_id, user, "coding:read_all")
        field = get_field(info, body.field_key) if get_field else None
        if field is None:
            raise HTTPException(400, "Coding field does not belong to this task.")
        field_type = ("multiselect" if field["multi_select"] else "categorical") \
            if field["dtype"] == "choice" else field["dtype"]
        try:
            records = coding_consensus.latest_rater_records_for_field(
                path, field_key=body.field_key, rater_ids=body.rater_ids)
            if any(record["source"].get("_reviewflow_field") != field for record in records):
                raise HTTPException(409, {"error_code": "coding_field_revision_mismatch",
                                          "message_key": "coding.field_revision_mismatch", "params": {},
                                          "details": "Coding field changed; select revisions under one field definition.",
                                          "message": "Coding field changed; select revisions under one field definition."})
            result = summarize_agreement(
                records, body.rater_ids, field_type,
                absolute_tolerance=body.absolute_tolerance,
            )
            return {"field": field, **result}
        except ValueError as exc:
            raise bad(str(exc)) from exc

    @app.post("/api/tasks/{task_id}/coding/consensus", status_code=201, dependencies=[Depends(_body_within_limit)])
    def append_consensus(task_id: str, body: ConsensusRecordIn,
                      user: dict = Depends(require_user),
                      response: Response = None):
        info, path, actor_id = context(task_id, user, "consensus:write", body.item_key, body.field_key)
        field = validate_value(info, body.field_key, body.value) if validate_value else None
        if validate_value and not isinstance(field, dict):
            raise HTTPException(400, "Value does not match the canonical coding field.")
        # 2.1.6 P17：team 任务经 P15 mutate 真实 team 门（reconciler 业务，
        # coding_consensus_append=collab 目标；仍按原 consensus:write action
        # 权限，不擅加个人写 policy——C08 addendum）。
        runtime = _team_runtime(app)
        if runtime is not None and _is_team_task(runtime, task_id):
            from coscreen.team_flow_types import TeamOperation

            operation = TeamOperation(name="coding_consensus_append",
                                      stage="coding")

            def writer(ctx):
                inputs = ctx.write_inputs
                return coding_consensus.append_consensus_record(
                    path, item_key=body.item_key, field_key=body.field_key,
                    reconciler_id=actor_id, value=body.value,
                    source={**body.source, "_reviewflow_field": field} if field else body.source,
                    rationale=body.rationale, rater_record_ids=body.rater_record_ids,
                    conn=inputs.collab_conn,  # 与检查/提交目标同一保留连接
                )

            try:
                envelope = runtime.mutate(
                    task_id, user, operation=operation, item_keys=(body.item_key,),
                    request_id=_team_request_id("consensus"),
                    request_sha256=_team_request_sha256(
                        {"item_key": body.item_key, "field_key": body.field_key,
                         "value": body.value, "source": body.source,
                         "rationale": body.rationale,
                         "rater_record_ids": list(body.rater_record_ids)}),
                    writer=writer)
                return _team_writer_result(envelope, response)
            except HTTPException:
                raise  # P12/P15/S2 结构化拒绝原样透传
            except ValueError as exc:
                # P15-D1 收窄（包 M）：漂移字面信号 → 409 可重试；
                # writer/存储业务拒绝保持 400（同请求重试永不成功）。
                if _is_team_drift_signal(exc):
                    raise _team_drift_http(task_id, exc) from exc
                raise bad(str(exc)) from exc
            except Exception as exc:  # noqa: BLE001 —— typed 确定性 503；其余不兜底
                mapped = _team_typed_unavailable_http(exc)
                if mapped is None:
                    raise
                raise mapped from exc
        try:
            # U4-P5：写前在任务锁内复查授权，撤销后落地被拒。
            return guarded_consensus_write(
                task_id, info, user, "consensus:write",
                lambda: coding_consensus.append_consensus_record(
                    path, item_key=body.item_key, field_key=body.field_key,
                    reconciler_id=actor_id, value=body.value,
                    source={**body.source, "_reviewflow_field": field} if field else body.source,
                    rationale=body.rationale, rater_record_ids=body.rater_record_ids,
                ))
        except ValueError as exc:
            raise bad(str(exc)) from exc

    @app.get("/api/tasks/{task_id}/coding/consensus")
    def list_consensus(task_id: str, item_key: str, field_key: str, user: dict = Depends(require_user)):
        info, path, _ = context(task_id, user, "consensus:read", item_key, field_key)
        try:
            return {"records": coding_consensus.list_consensus_records(
                path, item_key=item_key, field_key=field_key
            )}
        except ValueError as exc:
            raise bad(str(exc)) from exc

    @app.post("/api/tasks/{task_id}/coding/freezes", status_code=201, dependencies=[Depends(_body_within_limit)])
    def freeze(task_id: str, body: FreezeIn,
              user: dict = Depends(require_user),
              response: Response = None):
        info, path, actor_id = context(task_id, user, "consensus:freeze")
        # 2.1.6 P17：team 任务经 P15 mutate 真实 team 门（reconciler 业务，
        # coding_consensus_freeze=collab 目标）。freeze 的全部 consensus ID
        # 先在只读连接预解析出真实键域交给整批检查；写窗内在同一把 collab
        # 保留连接上权威重解析并比对——**整批通过才写**，单窗口整批提交，
        # 绝不逐键开短窗放锁再写（P17 需求 2）。
        runtime = _team_runtime(app)
        if runtime is not None and _is_team_task(runtime, task_id):
            from coscreen.team_flow_types import TeamOperation

            operation = TeamOperation(name="coding_consensus_freeze",
                                      stage="coding")

            def writer(ctx):
                inputs = ctx.write_inputs
                resolved = _resolve_freeze_keys_on_collab(
                    inputs.collab_conn, operation, body.consensus_ids, keys)
                if resolved != keys:
                    # 预解析与写窗内权威解析漂移：fail-closed，整批拒绝重试。
                    raise HTTPException(409, {
                        "code": "coding_freeze_key_drift",
                        "message_key": "coding.freeze_key_drift",
                        "params": {"request_keys": list(keys),
                                   "resolved_keys": list(resolved)},
                        "details": "Consensus key domain changed; retry the freeze.",
                        "message": "Consensus key domain changed; retry the freeze."})
                return coding_consensus.freeze_consensus(
                    path, name=body.name, frozen_by=actor_id,
                    consensus_ids=body.consensus_ids,
                    conn=inputs.collab_conn,  # 与检查/提交目标同一保留连接
                )

            try:
                keys = _freeze_item_keys_prewindow(path, body.consensus_ids)
                envelope = runtime.mutate(
                    task_id, user, operation=operation, item_keys=keys,
                    request_id=_team_request_id("freeze"),
                    request_sha256=_team_request_sha256(
                        {"name": body.name,
                         "consensus_ids": list(body.consensus_ids)}),
                    writer=writer)
                return _team_writer_result(envelope, response)
            except HTTPException:
                raise  # P12/P15/S2 结构化拒绝原样透传
            except ValueError as exc:
                # P15-D1 收窄（包 M）：漂移字面信号 → 409 可重试；
                # ID 不可解析等业务拒绝保持 400（同请求重试永不成功）。
                if _is_team_drift_signal(exc):
                    raise _team_drift_http(task_id, exc) from exc
                raise bad(str(exc)) from exc
            except Exception as exc:  # noqa: BLE001 —— typed 确定性 503；其余不兜底
                mapped = _team_typed_unavailable_http(exc)
                if mapped is None:
                    raise
                raise mapped from exc
        try:
            # U4-P5：写前在任务锁内复查授权，撤销后冻结被拒。
            return guarded_consensus_write(
                task_id, info, user, "consensus:freeze",
                lambda: coding_consensus.freeze_consensus(
                    path, name=body.name, frozen_by=actor_id, consensus_ids=body.consensus_ids
                ))
        except ValueError as exc:
            raise bad(str(exc)) from exc

    @app.get("/api/tasks/{task_id}/coding/freezes")
    def list_freezes(task_id: str, name: str | None = None, user: dict = Depends(require_user)):
        info, path, _ = context(task_id, user, "consensus:read")
        try:
            return {"freezes": coding_consensus.list_freezes(path, name=name)}
        except ValueError as exc:
            raise bad(str(exc)) from exc

    @app.get("/api/tasks/{task_id}/coding/freezes/{freeze_id}")
    def get_freeze(task_id: str, freeze_id: int, user: dict = Depends(require_user)):
        info, path, _ = context(task_id, user, "consensus:read")
        try:
            freeze_record = coding_consensus.get_freeze(path, freeze_id)
        except ValueError as exc:
            raise bad(str(exc)) from exc
        if freeze_record is None:
            raise HTTPException(404, "Coding freeze not found.")
        return freeze_record
