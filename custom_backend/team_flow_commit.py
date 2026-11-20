"""P13　团队写提交协议（C08 作者侧）——``run_team_mutation``。

合同权威：docs/superpowers/plans/2026-11-16-2.1.6-team-contracts.md 的
C08（mutation service 全节）、C03（F1 观察 v2）、C09（请求回执）与
2026-11-16-2.1.6-team-work-packages.md P13 节。

固定流程（C08 原文顺序，全部发生在 P10 ``retained_team_write_inputs``
保留窗口内，由窗口在正常退出时做 B1 唯一目标 commit）：

1. **原 receipt 幂等读取**（纯读）：同 scope 四字段
   （task_instance_id+actor_user_id+operation+request_id）已有成功 receipt
   且 ``request_sha256`` 一致 → 直接返回原成功结果（不观察、不写业务、
   不生成新时间）；任何差异 = ``TeamFlowReceiptConflict``（上游 409）。
2. **纯候选/全批检查**：P12 ``require_team_write``（权限/policy/链/同阶段
   完成与 raw 历史锁/footprint，整批一次，零业务写）。
3. **必要上游观察 append**：stage2（fulltext）写操作在上游 stage1 完成
   事实仅为「可信当前一致」（无持久 F1 seal）时，先以保留连接 fresh 读
   实际票面，构造 C03 v2 ``current_observation`` seal 追加到本窗口唯一
   提交目标（失败＝同目标回滚）。
4. **borrowed writer**：调用方 writer 只收到 ``TeamWriteInputs``（P10 写窗
   连接）——这是本协议唯一的业务写通道；本模块绝不另开直写路径。
5. **fresh 重读本阶段票**：以 ``vote_snapshot_keys_on`` 直读各 required
   成员保留连接的实际票面（绝不使用 scope 投票缓存、绝不从请求 body 猜
   一致）；全部 required 实际在票且一致＝真正最后一票 → 按键追加 v1
   ``last_vote`` unanimous seal。
6. **原 trace**：可选 ``trace`` 钩子在窗口内、receipt 之前调用（默认
   None＝该宿主请求无 trace 步骤）；trace 失败＝同目标回滚。research_trace
   自身合同不被本模块替代或吞掉（C09）。
7. **team receipt**：P07 ``append_receipt_on`` 在目标连接追加（序列化失败
   ＝同目标回滚）；``result['request_id']`` 恒等于稳定 request_id。

失败语义（P13 需求条目 2）：

- writer/trace/receipt/seal 任一失败：异常原样上抛，P10 窗口对同一目标
  回滚，**绝不另开补 seal 事务**；本模块不从 read/check helper 写 seal
  （seal 只在本提交协议内产生）。
- 成功 commit 后的清理故障：只作为 ``cleanup_errors`` 诊断随成功结果
  返回，不改报回滚/未知。
- commit 结果不可判定（body 全部成功、窗口退出时 commit/close 失败）：
  返回 typed ``outcome='unknown'`` 结果（含原异常摘要），**绝不重放**
  writer/receipt（防双写）。

通知（SSE/事件）由调用方在锁外、真实 commit 成功后处理；本模块不发
任何通知。

签名冻结说明：``run_team_mutation`` 的必填参数集与 C08 一致；``trace``
是唯一可选扩展（默认 None，向后兼容），用于把宿主既有 research_trace
步骤纳入同目标回滚语义——C12 最小修订，已在 REPORT 登记。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from coscreen import tasks as tasks_mod
from coscreen.team_flow_receipts import (
    TeamFlowReceiptConflict,
    append_receipt_on,
    read_receipt_on,
)
from coscreen.team_flow_types import TeamOperation, normalize_item_keys
from coscreen.team_terminal_facts import (
    OBSERVATION_PAYLOAD_SCHEMA,
    TerminalFactsUnavailable,
    append_seal_on,
    read_seal_on,
    sha256 as seal_sha256,
)
from coscreen.team_terminal_registry import list_sources_on
from coscreen.team_vote_ledger import vote_snapshot_keys_on
from custom_backend.team_screening_write import (
    TeamWriteInputs,
    retained_team_write_inputs,
)
from custom_backend.team_write_checks import (
    require_team_write,
    team_operation_target,
)

__all__ = [
    "TeamMutationError",
    "run_team_mutation",
    "receipt_operation_key",
]

#: 产生筛选阶段一致 seal 的 operation（coding 无 F1 阶段投影，不适用）。
_STAGE_SEAL_OPERATIONS = frozenset({"member_vote"})

#: 界面阶段 → (F1 ledger 阶段, 上游界面阶段)。screen 无上游。
_STAGE_TO_LEDGER = {"screen": "stage1", "fulltext": "stage2"}
_UPSTREAM_BY_STAGE = {"fulltext": ("stage1", "screen")}

_MAX_REQUEST_ID = 128
_HEX64 = set("0123456789abcdef")


class TeamMutationError(RuntimeError):
    """提交协议参数/编程级违约（非业务拒绝；业务拒绝原样透传 P12/P07 typed 异常）。"""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"team mutation error: {code}: {detail}")


def receipt_operation_key(operation: TeamOperation) -> str:
    """receipt scope 的 operation 字符串：``name:stage``（服务端选定，非客户端输入）。"""
    return f"{operation.name}:{operation.stage}"


def _utc_now() -> str:
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def _validate_request_id(request_id: str) -> str:
    if not isinstance(request_id, str) or not 1 <= len(request_id) <= _MAX_REQUEST_ID:
        raise TeamMutationError(
            "invalid_request_id", "request_id must be 1..128 characters")
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in request_id):
        raise TeamMutationError(
            "invalid_request_id", "request_id must not contain control characters")
    return request_id


def _validate_request_sha256(request_sha256: str) -> str:
    if (not isinstance(request_sha256, str) or len(request_sha256) != 64
            or not set(request_sha256) <= _HEX64):
        raise TeamMutationError(
            "invalid_request_sha256", "request_sha256 must be 64 lowercase hex")
    return request_sha256


# ---------------------------------------------------------------------------
# 事实读取（全部只读，绝不写 seal——seal 只在 _append_* 提交步骤产生）
# ---------------------------------------------------------------------------

def _seal_origin(inputs: TeamWriteInputs) -> str:
    return "actor" if inputs.target_conn is inputs.actor_conn else "collab"


def _participants(inputs: TeamWriteInputs) -> list[dict]:
    state = inputs.scope.role_state()
    if state.get("classification") != "owner_match":
        raise TeamMutationError("unconfirmed_task_state", "role state not owner_match")
    return [entry for entry in state.get("participants") or []
            if isinstance(entry, Mapping)]


def _usernames(inputs: TeamWriteInputs) -> dict[int, str]:
    """uid → username（scope 公开 accounts 装载；绝不接受客户端送名）。"""
    accounts_by_id, _casefold = inputs.scope.accounts()
    return {uid: name for uid, name in accounts_by_id.items()
            if isinstance(uid, int) and isinstance(name, str)}


def _roster_and_required(inputs: TeamWriteInputs) -> tuple[list[dict], list[int]]:
    """F1 roster/required_uids：只收 config=='ok' 的单一有效角色参与者。"""
    roster: list[dict] = []
    required: list[int] = []
    usernames = _usernames(inputs)
    for entry in _participants(inputs):
        if entry.get("config") != "ok" or entry.get("role") not in ("coder", "reconciler"):
            continue
        role = entry["role"]
        required_screener = entry.get("required_screener") is True
        screening_enabled = entry.get("screening_enabled")
        if role == "reconciler":
            if not isinstance(screening_enabled, bool) or screening_enabled != required_screener:
                continue  # 与 F1 校验同口径：reconciler 二者必须相等
        else:
            if not required_screener:
                continue  # F1：coder 必为 required screener
            screening_enabled = None if screening_enabled is None else bool(screening_enabled)
        roster.append({
            "uid": entry["user_id"],
            "username": usernames.get(entry["user_id"], ""),
            "role": role,
            "is_owner": entry.get("is_owner") is True,
            "screening_enabled": screening_enabled,
            "required_screener": required_screener,
            "source": str(entry.get("source") or "grant"),
            "config": "ok",
        })
        if required_screener:
            required.append(entry["user_id"])
    if not roster or not required:
        raise TeamMutationError("roster_unavailable", "no configured required screeners")
    return roster, sorted(required)


def _member_connection(inputs: TeamWriteInputs, uid: int, username: str):
    """成员保留连接（只从 P10 保留映射取，绝不自开连接）；不可得返回 None。"""
    task_dir = getattr(inputs.ctx.info, "dir", None)
    if task_dir is None:
        return None
    path = Path(task_dir) / f"{tasks_mod.slugify_screener(username)}.db"
    try:
        return inputs._reserved.connection(path)  # noqa: SLF001 - documented window channel
    except Exception:  # noqa: BLE001 - 保留集不含该成员＝不可确认，非协议失败
        return None


def _fresh_opinions(
    inputs: TeamWriteInputs, *, ledger_stage: str, keys: tuple[str, ...],
    required_uids: list[int],
) -> tuple[dict[int, dict[str, Any]] | None, list[str]]:
    """按键 fresh 直读全部 required 成员实际票面（不经 scope 投票缓存）。

    返回 ``(opinions_by_uid, unconfirmable)``：任一 required 成员的保留
    连接不可得、票行缺席或键有 issue 时 ``opinions_by_uid is None``（一致
    不可证明），``unconfirmable`` 为诊断名单（仅供 internal_evidence）。
    """
    username_by_uid = _usernames(inputs)
    opinions: dict[int, dict[str, Any]] = {}
    unconfirmable: list[str] = []
    for uid in required_uids:
        username = username_by_uid.get(uid, "")
        conn = _member_connection(inputs, uid, username)
        if conn is None:
            unconfirmable.append(f"{uid}:not_reserved")
            return None, unconfirmable
        try:
            snapshot = vote_snapshot_keys_on(conn, ledger_stage, keys)
        except Exception as exc:  # noqa: BLE001 - ledger typed 失败＝不可确认
            unconfirmable.append(f"{uid}:{type(exc).__name__}")
            return None, unconfirmable
        row = snapshot.rows.get(keys[0]) if len(keys) == 1 else None
        if len(keys) == 1 and row is not None and keys[0] not in snapshot.issues:
            opinions[uid] = _opinion_from_row(
                uid, snapshot.library_id, row)
            continue
        if snapshot.issues:
            unconfirmable.append(f"{uid}:issue")
            return None, unconfirmable
        unconfirmable.append(f"{uid}:absent")
        return None, unconfirmable
    return opinions, unconfirmable


def _opinion_from_row(uid: int, library_id: str, row: Mapping[str, Any]) -> dict[str, Any]:
    decision = row.get("decision")
    if not isinstance(decision, str):
        decision = ""
    exclusion_reason = row.get("exclusion_reason") or ""
    notes = row.get("notes") or ""
    source_row = {
        "tags": row.get("tags") or "",
        "screened_at": row.get("screened_at"),
        "updated_at": row.get("updated_at"),
    }
    return {
        "uid": uid,
        "decision": decision,
        "exclusion_reason": exclusion_reason,
        "notes": notes,
        "opinion_sha256": seal_sha256({
            "decision": decision.strip().lower(),
            "exclusion_reason": exclusion_reason,
            "notes": notes,
        }),
        "library_id": library_id,
        "revision": int(row.get("revision") or 0),
        "source_row": source_row,
        "source_row_sha256": seal_sha256(source_row),
    }


def _task_block(inputs: TeamWriteInputs, binding: Any, owner_username: str) -> dict[str, Any]:
    return {
        "task_id": binding.task_id,
        "task_instance_id": binding.task_instance_id,
        "owner_user_id": binding.owner_user_id,
        "owner_username": owner_username,
        "owner_library_id": binding.owner_library_id,
    }


def _owner_username(inputs: TeamWriteInputs) -> str:
    owner = inputs.scope.role_state().get("owner") or {}
    username = owner.get("owner_username")
    if not isinstance(username, str) or not username:
        raise TeamMutationError("unconfirmed_task_state", "owner username missing")
    return username


def _source_bindings(
    inputs: TeamWriteInputs, *, task_instance_id: str,
    required_uids: list[int], observer_uid: int,
) -> dict[int, dict[str, Any]] | None:
    """F2 active 源绑定（registration_id/generation/library_id）；不可得返回 None。"""
    try:
        records = list_sources_on(
            inputs.collab_conn, task_instance_id=task_instance_id, status="active")
    except Exception:  # noqa: BLE001 - F2 未准备/不可读＝观察不可构造（fail-closed 由调用方决定）
        return None
    by_uid: dict[int, dict[str, Any]] = {}
    for record in records:
        if record.get("status") != "active":
            continue
        by_uid[int(record["source_user_id"])] = {
            "registration_id": record["registration_id"],
            "generation": int(record["generation"]),
            "source_library_id": record["source_library_id"],
        }
    needed = set(required_uids) | {observer_uid}
    if not needed <= set(by_uid):
        return None
    return {uid: by_uid[uid] for uid in needed}


# ---------------------------------------------------------------------------
# seal 构造与追加（只在提交协议内调用——read/check helper 不写 seal）
# ---------------------------------------------------------------------------

def _append_last_vote_seals(
    inputs: TeamWriteInputs, *, operation: TeamOperation, keys: tuple[str, ...],
    request_id: str, request_sha256: str,
    sealed: list[dict[str, Any]],
) -> None:
    if operation.name not in _STAGE_SEAL_OPERATIONS:
        return
    ledger_stage = _STAGE_TO_LEDGER[operation.stage]
    roster, required_uids = _roster_and_required(inputs)
    binding = inputs.plan.binding
    task_block = _task_block(inputs, binding, _owner_username(inputs))
    for key in keys:
        opinions, _unconfirmable = _fresh_opinions(
            inputs, ledger_stage=ledger_stage, keys=(key,), required_uids=required_uids)
        if opinions is None:
            continue  # 一致不可证明（fresh 事实缺）→ 不写 seal（不猜请求 body）
        decisions = {opinion["decision"] for opinion in opinions.values()}
        if len(decisions) != 1:
            continue  # 仍有分歧/缺席＝不是最后一票
        final_decision = next(iter(decisions))
        if final_decision not in ("include", "exclude"):
            continue
        payload = {
            "schema": "team_terminal_seal.v1",
            "task": task_block,
            "stage": ledger_stage,
            "item_key": key,
            "round_id": None,
            "kind": "unanimous",
            "trigger": "last_vote",
            "final_decision": final_decision,
            "roster": roster,
            "required_uids": required_uids,
            "opinions": [opinions[uid] for uid in required_uids],
            "arbitration": None,
            "protocol": {"source_registration_id": None, "roster_revision": None},
            "source_request": {
                "operation": "vote_save",
                "request_id": request_id,
                "request_sha256": request_sha256,
            },
            "sealed_at": _utc_now(),
        }
        record = append_seal_on(
            inputs.target_conn, payload, origin=_seal_origin(inputs))
        sealed.append({
            "kind": "unanimous", "trigger": "last_vote",
            "stage": ledger_stage, "item_key": key,
            "seal_id": record["seal_id"],
        })


def _append_upstream_observation(
    inputs: TeamWriteInputs, *, operation: TeamOperation, keys: tuple[str, ...],
    request_id: str, request_sha256: str, actor_uid: int,
    sealed: list[dict[str, Any]],
) -> None:
    upstream = _UPSTREAM_BY_STAGE.get(operation.stage)
    if upstream is None or operation.name not in _STAGE_SEAL_OPERATIONS | {
            "member_undo", "member_annotation", "member_annotation_note"}:
        return
    ledger_stage, _ui_stage = upstream
    binding = inputs.plan.binding
    instance = binding.task_instance_id
    origin = _seal_origin(inputs)
    for key in keys:
        # HIGH#1 B4（B1 配套，2026-11-20）：plan 已见证该 (上游 stage,
        # key) 的持久 seal（holder 扫描发现并核验，B1 后 fulltext plan
        # 覆盖 stage1）→ 观察不必要。绝不能只看 target_conn（actor 自己
        # 库）：last_vote unanimous seal 常态落在**最后投票者**成员库，
        # 若此处漏看而在 actor 库伪造第二枚 current_observation seal，
        # 同键两枚互斥记录会让后续写/读的发现层判 conflicting_seals
        # （fail-closed），把合法 fulltext 流程卡死（m02c 回归实测）。
        if (ledger_stage, key) in getattr(inputs.plan, "witnesses", {}):
            continue  # plan 见证＝上游已有持久完成见证：观察不必要
        try:
            persisted = read_seal_on(
                inputs.target_conn, task_instance_id=instance,
                stage=ledger_stage, item_key=key)
        except TerminalFactsUnavailable:
            persisted = None  # 目标库未装 F1 表＝无持久 seal 事实（观察仍可构造）
        if persisted is not None:
            continue  # 上游已有持久完成见证：观察不必要
        if _append_one_observation(
                inputs, binding=binding, instance=instance,
                ledger_stage=ledger_stage, key=key, keys=keys,
                operation=operation, actor_uid=actor_uid,
                request_id=request_id, request_sha256=request_sha256,
                origin=origin, sealed=sealed):
            return  # 一批一条 host seal（host_item_keys 覆盖整批），不逐键重复


def _append_one_observation(
    inputs: TeamWriteInputs, *, binding: Any, instance: str, ledger_stage: str,
    key: str, keys: tuple[str, ...], operation: TeamOperation,
    actor_uid: int, request_id: str, request_sha256: str,
    origin: str, sealed: list[dict[str, Any]],
) -> bool:
    roster, required_uids = _roster_and_required(inputs)
    opinions, _unconfirmable = _fresh_opinions(
        inputs, ledger_stage=ledger_stage, keys=(key,), required_uids=required_uids)
    if opinions is None:
        return False  # 上游事实不可证明：不造完成档案（C03 fail-closed）
    decisions = {opinion["decision"] for opinion in opinions.values()}
    if len(decisions) != 1 or next(iter(decisions)) not in ("include", "exclude"):
        return False  # 上游并非当前一致：观察前提不成立
    bindings = _source_bindings(
        inputs, task_instance_id=instance,
        required_uids=required_uids, observer_uid=actor_uid)
    if bindings is None:
        return False  # F2 绑定不完整：不伪造 source_bindings（C03）
    payload = {
        "schema": OBSERVATION_PAYLOAD_SCHEMA,
        "task": _task_block(inputs, binding, _owner_username(inputs)),
        "stage": ledger_stage,
        "item_key": key,
        "round_id": None,
        "kind": "unanimous",
        "trigger": "current_observation",
        "final_decision": next(iter(decisions)),
        "roster": roster,
        "required_uids": required_uids,
        "opinions": [opinions[uid] for uid in required_uids],
        "arbitration": None,
        "protocol": {
            "source_registration_id": bindings[actor_uid]["registration_id"],
            "roster_revision": None,
        },
        "source_request": {
            "operation": "completion_observe",
            "request_id": request_id,
            "request_sha256": request_sha256,
        },
        "sealed_at": _utc_now(),
        "observation": {
            "host_operation": operation.name,
            "host_stage": operation.stage,
            "host_item_keys": list(keys),
            "observed_by_user_id": actor_uid,
            "basis": "current_snapshot",
        },
        "source_bindings": [
            {
                "source_user_id": uid,
                "registration_id": bindings[uid]["registration_id"],
                "generation": bindings[uid]["generation"],
                "source_library_id": bindings[uid]["source_library_id"],
            }
            for uid in sorted(bindings)
        ],
    }
    record = append_seal_on(inputs.target_conn, payload, origin=origin)
    sealed.append({
        "kind": "unanimous", "trigger": "current_observation",
        "stage": ledger_stage, "item_key": key,
        "seal_id": record["seal_id"],
    })
    return True


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def run_team_mutation(
    runtime: Any,
    info: object,
    user: Mapping[str, Any],
    *,
    operation: TeamOperation,
    item_keys: Iterable[str],
    request_id: str,
    request_sha256: str,
    writer: Callable[[TeamWriteInputs], Mapping[str, object]],
    trace: Callable[[TeamWriteInputs, Mapping[str, object]], Any] | None = None,
) -> Mapping[str, object]:
    """团队写提交协议（typed 结果；通知由调用方锁外处理）。

    返回映射：``outcome``（'committed' | 'unknown'）、``idempotent``、
    ``request_id``、``result``（writer 结果或幂等命中时的原结果）、
    ``receipt``（P07 公开记录）、``seals``（本协议追加的 seal 摘要）、
    ``cleanup_errors``（成功后清理诊断；不改成功语义）；``unknown`` 时另含
    ``error``（原异常摘要）。业务/资格拒绝不经本函数包装，原样透传
    P12/P07/P10 typed 异常（同目标回滚由 P10 窗口完成）。
    """
    _validate_request_id(request_id)
    _validate_request_sha256(request_sha256)
    if not callable(writer):
        raise TeamMutationError("invalid_writer", "writer must be callable")
    keys = normalize_item_keys(item_keys)
    target_kind = team_operation_target(operation)  # 未知组合：ValueError（窗口前）
    op_key = receipt_operation_key(operation)
    user_id = user.get("user_id") if isinstance(user, Mapping) else None
    if isinstance(user_id, bool) or type(user_id) is not int or user_id <= 0:
        raise TeamMutationError("invalid_user", "authenticated user_id missing")

    window_inputs: TeamWriteInputs | None = None
    body_completed = False
    payload: dict[str, Any] | None = None
    try:
        with retained_team_write_inputs(
                runtime, info, dict(user), target_kind=target_kind,
                operation=operation, item_keys=keys) as inputs:
            window_inputs = inputs
            instance = inputs.plan.binding.task_instance_id

            # 1. 原 receipt 幂等（纯读；P10 已完成现行身份/绑定复核）
            existing = read_receipt_on(
                inputs.target_conn, task_instance_id=instance,
                actor_user_id=user_id, operation=op_key, request_id=request_id)
            if existing is not None:
                if existing["request_sha256"] != request_sha256:
                    raise TeamFlowReceiptConflict(
                        "receipt already exists for this scope with different content")
                payload = {
                    "outcome": "committed",
                    "idempotent": True,
                    "request_id": request_id,
                    "result": existing["result"],
                    "receipt": existing,
                    "seals": (),
                }
            else:
                # 2. 纯候选/全批检查（零业务写）
                require_team_write(inputs, operation=operation, item_keys=keys)

                sealed: list[dict[str, Any]] = []
                # 3. 必要上游观察 append
                _append_upstream_observation(
                    inputs, operation=operation, keys=keys,
                    request_id=request_id, request_sha256=request_sha256,
                    actor_uid=user_id, sealed=sealed)
                # 4. borrowed writer（P10 写窗＝唯一业务写通道）
                writer_result = writer(inputs)
                if not isinstance(writer_result, Mapping):
                    raise TeamMutationError(
                        "invalid_writer_result",
                        "writer must return a Mapping (receipt result body)")
                # 5. fresh 重读 → 真正最后一票一致才 append last_vote
                _append_last_vote_seals(
                    inputs, operation=operation, keys=keys,
                    request_id=request_id, request_sha256=request_sha256,
                    sealed=sealed)
                # 6. 原 trace（可选；失败＝同目标回滚）
                if trace is not None:
                    trace(inputs, writer_result)
                # 7. team receipt（序列化失败＝同目标回滚）
                receipt = append_receipt_on(inputs.target_conn, {
                    "schema": "team_flow_receipt.v1",
                    "task_instance_id": instance,
                    "actor_user_id": user_id,
                    "operation": op_key,
                    "request_id": request_id,
                    "request_sha256": request_sha256,
                    "result": {
                        "request_id": request_id,
                        "operation": op_key,
                        "item_keys": list(keys),
                        "writer": dict(writer_result),
                        "seals": [dict(item) for item in sealed],
                    },
                    "internal_evidence": {
                        "target_kind": target_kind,
                        "idempotent": False,
                    },
                    "recorded_at": _utc_now(),
                    "source_registration_id": None,
                    "source_generation": None,
                })
                payload = {
                    "outcome": "committed",
                    "idempotent": False,
                    "request_id": request_id,
                    "result": receipt["result"],
                    "receipt": receipt,
                    "seals": tuple(dict(item) for item in sealed),
                }
            body_completed = True
    except BaseException as exc:  # noqa: BLE001 - 需区分 body 失败与 commit 不可判定
        if body_completed:
            # body（writer/seal/trace/receipt）已全部成功、窗口退出阶段失败：
            # commit 结果不可判定——如实报 unknown，绝不重放（防双写）。
            # B1 异常退出路径的 rollback/close 诊断由 reserved._cleanup()
            # 记录在 cleanup_errors：随 unknown 一并返回（tuple 语义不变，
            # 不改变 unknown/outcome 语义本身）。
            unknown_cleanup = (window_inputs.cleanup_errors
                               if window_inputs is not None else ())
            return {
                "outcome": "unknown",
                "idempotent": False,
                "request_id": request_id,
                "result": None,
                "receipt": None,
                "seals": (),
                "cleanup_errors": tuple(unknown_cleanup),
                "error": f"{type(exc).__name__}: {exc}",
            }
        raise
    assert payload is not None
    # 成功路径：cleanup 诊断随成功返回（绝不因清理故障改报回滚/未知）
    cleanup = window_inputs.cleanup_errors if window_inputs is not None else ()
    payload["cleanup_errors"] = tuple(cleanup)
    return payload
