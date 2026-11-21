"""P1（2.1.6 缺口族根治主干包）：团队写路径 typed 异常 → HTTP 的统一映射。

设计依据 = ``E:/rf216-store-scan-20261120/GAP_DESIGN_SURVEY.md`` §5.2 统一
映射表。本模块是**纯映射层**：不 import ``custom_backend.main``（避免环），
不读盘、不建库、不持任何锁；调用方（``review_workflow_api._mutate_team``、
``arbitration_session_api._team_http_for``、``main._team_management_http_error``
与 ``main._ensure_owner_team_storage`` 的成员侧镜像）把捕获到的异常交给
:func:`typed_team_http`，得到结构化 ``HTTPException`` 或 ``None``（非本层可
判定的异常，调用方按既有全局处理——绝不伪造业务错误）。

口径（逐条对齐 §5.2，且与既有先例 appraisal_api/coding_consensus_api 的
P15-D1/P17 判定一致）：

- 确定性存储未准备/布局不符/损坏（``not_prepared``/``schema_mismatch``/
  ``corrupt``）→ **409 不可重试**（``retryable: false``）：同请求重试永不
  成功，只有宿主先跑准备生命周期才可能恢复——503 会被网关/客户端当作
  "稍后重试"，是口径错误。
- 暂时不可读（``unreadable``）→ **503 可重试**（``retryable: true``，带
  ``Retry-After``）：锁忙/暂时读失败是真实可重试条件。
- ``connection_contract`` → 原样上抛（返回 ``None``，编程违约不伪装业务态）。
- ``TerminalFactsPayloadInvalid``/``TerminalFactsConflict``/
  ``TerminalRegistryConflict``/``TerminalRegistryStateError``/
  ``TerminalRegistryPayloadInvalid``/``VoteLedgerUnavailable`` 等确定性族
  → 409 不可重试（各自 error_code）。
- ``TeamManagementError``：not_owner→403、invalid_*→400（保持既有），
  退休/归档确定性族→**409**（R5：``team_retirement_blocked`` + ``recovery``
  键 + 十语文案），仅 ``source_library_unverifiable`` 保持 503 可重试。
- ``TeamFlowUnavailableError`` → 保持既有 503 typed 形状（零语义漂移）。

结构化信封（所有非 2xx 一律带，禁裸异常串）：

    {"error_code": ..., "message_key": ..., "params": {...},
     "details": "...", "message": "...", "retryable": bool, ["recovery": {...}]}
"""

from __future__ import annotations

__all__ = [
    "typed_team_http",
    "team_management_http",
    "retirement_recovery",
    "typed_unprepared_envelope",
    "CONTRACT_OF_BREACH_REASON",
    "RETIREMENT_BLOCKED_MESSAGE",
    "OWNER_BUSINESS_ROLE_UNCONFIGURED_MESSAGE",
    "OWNER_BUSINESS_ROLE_UNCONFIGURED_MESSAGE_KEY",
    "OWNER_BUSINESS_ROLE_UNCONFIGURED_CODE",
    "RETIREMENT_RECOVERY_UNKNOWN_MESSAGE",
]

#: 借用连接合同违约的 reason 字面量（P15-D1 口径：编程错误，原样上抛，
#: 不伪装业务态）。三消费方（成员写/仲裁/管理）共用同一常量，防词表漂移。
CONTRACT_OF_BREACH_REASON = "connection_contract"

#: R4 的稳定 error_code。
OWNER_BUSINESS_ROLE_UNCONFIGURED_CODE = "team_owner_business_role_unconfigured"

#: R5：永久条件 409 的用户可见文案（十语言词条由前端 interface_locale.js
#: 按 message_key 渲染；本串是服务端兜底文本，保持与既有中文口径一致）。
RETIREMENT_BLOCKED_MESSAGE = (
    "该成员的来源登记无法归档确认，移除已被拒绝（重试同一请求不会成功）。"
    "请按 recovery 指引处理：成员库缺失/损坏需先修复或恢复文件；"
    "角色/筛选配置冲突需先选定单一业务角色并开启筛选参与。")

#: R4：owner 未配置业务角色时团队业务写的前置 409 文案。
OWNER_BUSINESS_ROLE_UNCONFIGURED_MESSAGE = (
    "任务属主尚未配置业务角色（coder/reconciler），团队成员无法达成一致完成。"
    "请先由属主在「协作成员」中设置业务角色后重试。")

#: R4 的稳定 message_key（前端十语文案注册口径）。
OWNER_BUSINESS_ROLE_UNCONFIGURED_MESSAGE_KEY = (
    "team.owner_business_role_unconfigured")

#: 退休恢复指引的未知兜底文案。
RETIREMENT_RECOVERY_UNKNOWN_MESSAGE = (
    "请检查该成员的来源登记与个人库状态；如无法确认证据，请联系运维按"
    "源登记修复流程处理。")


#: 确定性未准备/布局/损坏族的 reason 集合（409 不可重试）。
_DETERMINISTIC_REASONS = frozenset({"not_prepared", "schema_mismatch", "corrupt"})

#: 暂时性 reason（503 可重试）。
_TRANSIENT_REASONS = frozenset({"unreadable"})

#: 编程违约 reason（原样上抛，不映射）。与公开常量同一字面量，防漂移。
_CONTRACT_REASON = CONTRACT_OF_BREACH_REASON

#: 退休/归档族的确定性 TeamManagementError code →（error_code, recovery code）。
_RETIREMENT_CODES = {
    "retirement_source_unavailable": "team_retirement_blocked",
    "retirement_source_missing": "team_retirement_source_missing",
    "retirement_archive_incomplete": "team_retirement_archive_incomplete",
    "retirement_copy_mismatch": "team_retirement_copy_mismatch",
    "retirement_source_unreadable": "team_retirement_source_unreadable",
    "source_receipts_unreadable": "team_retirement_source_unreadable",
}

#: 退休恢复指引（recovery 键；按 error_code 给出可操作步骤）。
_RETIREMENT_RECOVERY = {
    "team_retirement_blocked": {
        "kind": "team_member_source",
        "actions": [
            "restore_or_repair_member_library",
            "choose_single_business_role",
            "enable_screening_participation",
        ],
        "doc": "team/retirement#recovery",
    },
    "team_retirement_source_missing": {
        "kind": "team_member_source",
        "actions": ["restore_member_library_file", "contact_operator"],
        "doc": "team/retirement#missing",
    },
    "team_retirement_archive_incomplete": {
        "kind": "team_member_source",
        "actions": ["contact_operator"],
        "doc": "team/retirement#archive",
    },
    "team_retirement_copy_mismatch": {
        "kind": "team_member_source",
        "actions": ["contact_operator"],
        "doc": "team/retirement#archive",
    },
    "team_retirement_source_unreadable": {
        "kind": "team_member_source",
        "actions": ["repair_corrupt_records", "contact_operator"],
        "doc": "team/retirement#corrupt",
    },
}


def retirement_recovery(error_code: str) -> dict:
    """退休族的 recovery 键（稳定结构；未知 code 给通用指引）。"""
    payload = _RETIREMENT_RECOVERY.get(error_code)
    if payload is None:
        return {
            "kind": "team_member_source",
            "actions": ["contact_operator"],
            "doc": "team/retirement",
            "message": RETIREMENT_RECOVERY_UNKNOWN_MESSAGE,
        }
    return dict(payload)


def typed_unprepared_envelope(*, error_code: str, message_key: str,
                              details: str, message: str,
                              recovery_kind: str,
                              actions=("contact_operator",),
                              doc: str = "") -> dict:
    """确定性未准备的统一信封（409 不可重试 + recovery 键）。

    供成员写咽喉自动准备失败（``team_member_storage_unprepared``）与属主
    A 方案就绪检查失败等「确定性、同请求重试不会成功」出口复用；与
    :func:`retirement_recovery` 同形（``recovery.kind/actions/doc``）。
    """
    recovery = {
        "kind": recovery_kind,
        "actions": list(actions or ("contact_operator",)),
    }
    if doc:
        recovery["doc"] = doc
    return _envelope(error_code=error_code, message_key=message_key,
                     params={}, details=details, message=message,
                     retryable=False, recovery=recovery)


def _envelope(*, error_code: str, message_key: str, params: dict,
              details: str, message: str, retryable: bool,
              recovery: dict | None = None,
              extra: dict | None = None) -> dict:
    body = {
        "error_code": error_code,
        "message_key": message_key,
        "params": dict(params or {}),
        "details": str(details),
        "message": str(message),
        "retryable": bool(retryable),
    }
    if recovery is not None:
        body["recovery"] = recovery
    if extra:
        body.update(extra)
    return body


def _http(status: int, body: dict, *, retry_after: bool = False):
    from fastapi import HTTPException
    headers = {"Retry-After": "1"} if retry_after else None
    return HTTPException(status, body, headers=headers)


def _reason_of(exc: Exception) -> str:
    reason = getattr(exc, "reason", None)
    return reason if isinstance(reason, str) else "unreadable"


def _map_reason_family(exc: Exception, *, error_code: str,
                       message_key: str, message: str,
                       extra_params: dict | None = None):
    """三族（receipts/F1/registry）共用：reason → 409/503/None。

    Returns:
        HTTPException | None：``connection_contract`` 返回 None（原样上抛）。
    """
    reason = _reason_of(exc)
    if reason == _CONTRACT_REASON:
        return None
    params = {"reason": reason}
    if extra_params:
        params.update(extra_params)
    if reason in _TRANSIENT_REASONS:
        return _http(503, _envelope(
            error_code=error_code, message_key=message_key, params=params,
            details=str(exc),
            message=message + "（存储暂时不可读，请稍后重试。）",
            retryable=True), retry_after=True)
    # 确定性族：not_prepared/schema_mismatch/corrupt（含未知 reason 的保守
    # fail-closed——只有 unreadable 被认定为可重试）。
    return _http(409, _envelope(
        error_code=error_code, message_key=message_key, params=params,
        details=str(exc),
        message=message + "（重试同一请求不会成功，需先修复存储。）",
        retryable=False))


def team_management_http(exc: Exception):
    """``TeamManagementError`` → 结构化 HTTP（R5 落点）。

    - not_owner → 403（既有口径，结构化信封）；
    - invalid_action/invalid_target/invalid_user/invalid_payload/
      invalid_request_id/invalid_request_sha256 → 400；
    - 退休/归档确定性族 → 409 + ``recovery`` 键（R5）；
    - source_library_unverifiable → 503 可重试（暂时不可核）；
    - 其余（unconfirmed_task_state 等）→ 409 确定性（不再 503 化）。
    """
    code = getattr(exc, "code", "")
    detail = getattr(exc, "detail", "") or str(exc)
    if code == "not_owner":
        return _http(403, _envelope(
            error_code="team_management_not_owner",
            message_key="team.management_not_owner", params={},
            details=detail, message="仅任务属主可管理协作成员。",
            retryable=False))
    if code.startswith("invalid_") or code in (
            "invalid_action", "invalid_target", "invalid_user",
            "invalid_payload", "invalid_request_id", "invalid_request_sha256"):
        return _http(400, _envelope(
            error_code=code, message_key=f"team.management.{code}",
            params={}, details=detail, message="团队管理请求参数不合法。",
            retryable=False))
    if code in _RETIREMENT_CODES:
        mapped = _RETIREMENT_CODES[code]
        return _http(409, _envelope(
            error_code=mapped,
            message_key=f"team.{mapped.removeprefix('team_')}"
                        if mapped.startswith("team_") else mapped,
            params={"management_code": code},
            details=detail, message=RETIREMENT_BLOCKED_MESSAGE,
            retryable=False, recovery=retirement_recovery(mapped)))
    if code == "source_library_unverifiable":
        return _http(503, _envelope(
            error_code="team_source_library_unverifiable",
            message_key="team.source_library_unverifiable",
            params={"management_code": code}, details=detail,
            message="成员库身份暂时无法核验，请稍后重试。",
            retryable=True), retry_after=True)
    return _http(409, _envelope(
        error_code=code or "team_management_rejected",
        message_key=f"team.management.{code or 'rejected'}",
        params={"management_code": code}, details=detail,
        message="团队管理操作被确定性拒绝（重试同一请求不会成功）。",
        retryable=False))


def typed_team_http(exc: Exception):
    """团队 typed 异常族 → 结构化 ``HTTPException``；不可判定返回 None。

    捕获顺序由调用方保证（HTTPException/TeamWriteBlocked/角色门等优先），
    本函数只处理写路径存储层族与 ``TeamManagementError``。
    """
    from fastapi import HTTPException

    if isinstance(exc, HTTPException):
        return exc

    # --- receipts / F1 / registry 三族（reason 词表同形）-----------------
    try:
        from coscreen.team_flow_receipts import (
            TeamFlowReceiptConflict,
            TeamFlowReceiptUnavailable,
        )
    except Exception:  # pragma: no cover - import 失败按不可判定上抛
        TeamFlowReceiptUnavailable = ()  # type: ignore[assignment]
        TeamFlowReceiptConflict = ()  # type: ignore[assignment]
    try:
        from coscreen.team_terminal_facts import (
            TerminalFactsConflict,
            TerminalFactsPayloadInvalid,
            TerminalFactsUnavailable,
        )
    except Exception:  # pragma: no cover
        TerminalFactsConflict = TerminalFactsPayloadInvalid = type(None)
        TerminalFactsUnavailable = ()  # type: ignore[assignment]
    try:
        from coscreen.team_terminal_registry import (
            TerminalRegistryConflict,
            TerminalRegistryPayloadInvalid,
            TerminalRegistryStateError,
            TerminalRegistryUnavailable,
        )
    except Exception:  # pragma: no cover
        (TerminalRegistryConflict, TerminalRegistryPayloadInvalid,
         TerminalRegistryStateError) = (type(None), type(None), type(None))
        TerminalRegistryUnavailable = ()  # type: ignore[assignment]
    try:
        from coscreen.team_vote_ledger import VoteLedgerUnavailable
    except Exception:  # pragma: no cover
        VoteLedgerUnavailable = ()  # type: ignore[assignment]

    if isinstance(exc, TeamFlowReceiptUnavailable):
        return _map_reason_family(
            exc, error_code="team_flow_receipt_unavailable",
            message_key="team.flow.receipt_unavailable",
            message="团队回执存储未就绪或不可用。")
    if isinstance(exc, TerminalFactsUnavailable):
        return _map_reason_family(
            exc, error_code="team_terminal_facts_unavailable",
            message_key="team.terminal_facts_unavailable",
            message="团队完成事实（F1）存储未就绪或不可用。")
    if isinstance(exc, TerminalRegistryUnavailable):
        return _map_reason_family(
            exc, error_code="team_registry_unavailable",
            message_key="team.registry_unavailable",
            message="团队来源登记（F2）存储未就绪或不可用。")
    if isinstance(exc, TerminalFactsPayloadInvalid):
        return _http(409, _envelope(
            error_code="team_seal_payload_invalid",
            message_key="team.seal_payload_invalid", params={},
            details=str(exc), message="团队完成事实载荷构造不合法。",
            retryable=False))
    if isinstance(exc, TerminalFactsConflict):
        return _http(409, _envelope(
            error_code="team_terminal_seal_conflict",
            message_key="team.terminal_seal_conflict", params={},
            details=str(exc),
            message="同一键已存在内容不同的完成公章，需人工修复。",
            retryable=False))
    if isinstance(exc, (TerminalRegistryConflict, TerminalRegistryStateError,
                        TerminalRegistryPayloadInvalid)):
        return _http(409, _envelope(
            error_code="team_registry_conflict",
            message_key="team.registry_conflict", params={},
            details=str(exc),
            message="团队来源登记状态冲突（重试同一请求不会成功）。",
            retryable=False))
    if isinstance(exc, VoteLedgerUnavailable):
        reason = _reason_of(exc)
        params = {"reason": reason}
        member = getattr(exc, "member", None)
        if member is not None:
            params["member"] = member
        if reason == "unreadable":
            return _http(503, _envelope(
                error_code=getattr(exc, "code",
                                   "team.flow.revision_unavailable"),
                message_key=getattr(exc, "message_key",
                                    "team.flow.revision_unavailable"),
                params=params, details=str(exc),
                message="团队票版本账本暂时不可读，请稍后重试。",
                retryable=True), retry_after=True)
        return _http(409, _envelope(
            error_code=getattr(exc, "code", "team.flow.revision_unavailable"),
            message_key=getattr(exc, "message_key",
                                "team.flow.revision_unavailable"),
            params=params, details=str(exc),
            message="团队票版本账本未就绪或损坏（重试同一请求不会成功）。",
            retryable=False))

    if isinstance(exc, TeamFlowReceiptConflict):
        # 同 scope 异内容（C09）：确定性冲突 409，保留既有结构化 code。
        return _http(409, _envelope(
            error_code="request_receipt_conflict",
            message_key="team.flow.request_receipt_conflict",
            params={}, details=str(exc),
            message="该请求编号已有内容不同的成功回执，请更换请求编号重试。",
            retryable=False,
            extra={"code": "request_receipt_conflict"}))

    # --- TeamManagementError（R5 落点；延迟 import 避免环）--------------
    try:
        from custom_backend.team_source_lifecycle import TeamManagementError
    except Exception:  # pragma: no cover
        TeamManagementError = ()  # type: ignore[assignment]
    if isinstance(exc, TeamManagementError):
        return team_management_http(exc)

    return None
