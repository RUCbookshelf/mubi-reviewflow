"""S2-B2b/P12 团队统一纯检查服务（纯检查，不控制任何连接/事务）。

本模块只回答：在已由 ``retained_team_write_inputs`` 保留好连接的团队写窗口
内，当前 actor 对给定 operation 与条目集合是否允许执行。它不接 HTTP、不读
客户端路径/名册/最终资格、不另开 fresh scope，也不初始化 trace、不通知、
不写任何业务行。

P12（C08 作者侧）统一口径：

- 旧 ``TeamWriteOp`` 不再是本模块的独立类，直接 re-export P00
  ``coscreen.team_flow_types.TeamOperation``（同字段序 ``name/stage/
  expected_version``），绝不并存两类。
- 首票例外（s2b-first-current-vote-addendum 的「当前新槽位」窄分支，
  即 ``_new_current_vote_slot_allowed`` 及其全部辅助/调用）已按 2026-11-15
  用户裁定移除：完成阶段保留当时名单/意见/结论，新成员只参与未完成阶段，
  不因「新成员没票/无旧痕迹」放行。同阶段持久完成（F1 见证）或可信当前
  一致（live consensus/已适用仲裁）一律拒写，拒绝不补 seal。
- 同阶段完成拒绝是结构化 409 ``TeamWriteBlocked``，``code ==
  'stage_completed_read_only'``，携带排序去重的触发键（C08「业务已完成
  用 409」）；读取/发现期 issue 用 P09 ``KeyIssue.reason`` 做键级错误，
  整批一次拒绝（零业务写）。
- 纯检查 pass 消费 P10（``team_stage_view``/``team_chain_view`` 的
  ``completion``/``issues`` 投影、raw 历史锁 ``adjudicated_history_keys_on``）
  与 P11（``mutation_footprint_on`` 的请求键主体 footprint 完整性）。

固定 operation 表（未列 name 或 stage 组合一律 ``ValueError``；与 P11
``team_write_footprints`` 的 name/stage 域保持一致）：

======================  ======  ======  ================================  ==============  ======
name                    stage   target  role                              chain target    policy
======================  ======  ======  ================================  ==============  ======
member_vote             screen  actor   有效应筛成员                       无                  是
member_vote             fulltext actor  有效应筛成员                       stage2              是
member_undo             screen  actor   有效应筛成员                       无                  是
member_undo             fulltext actor  有效应筛成员                       stage2              是
member_undo             coding  actor   coder（coding:submit）             coding              是
member_coding           coding  actor   coder（coding:submit）             coding              是
member_coding_note      coding  actor   coder（coding:submit）             coding              否
member_appraisal        coding  actor   coder（coding:submit）             appraisal           否
member_annotation       fulltext actor  有效应筛成员                       stage2              否
member_annotation_note  fulltext actor  有效应筛成员                       stage2              是
coding_rater_submit     coding  collab  coder（coding:submit）             coding              是
coding_consensus_append coding  collab  reconciler（consensus:write）    coding              否
coding_consensus_freeze coding  collab  reconciler（consensus:freeze）  coding          否
======================  ======  ======  ================================  ==============  ======

「有效应筛成员」＝当前 participant 的 ``required_screener`` 为真（coder，或
已开启筛选参与的 reconciler）；筛选关闭用结构化 403
``screening_not_enabled``。owner 与普通成员同规则，不因 ``can_manage`` 或
属主身份绕过（团队合同 v2 §9）。角色未配置/冲突/无权限沿用既有
``TeamRole*`` typed 异常。``FINAL_EXCLUDE`` 永不视为已推进：链检查只认
``ChainItem.cleared is True``。

A1/A2/A3 旧 op map addendum 保持：appraisal 只加两阶段链，不擅加
paused/source/expected 门；annotation add/delete（``member_annotation``，
含其 artifact 删除路径）加角色/资格/同阶段历史门，不继承 note-update
（``member_annotation_note``）的额外 policy；coding_note 同 annotation
口径；consensus append/freeze 仍按原 action 权限，不擅加个人写 policy。

不包含：新 team_session/server-save（S3 另接）、维度/value/evidence_complete/
source-record/冻结版本、trace 准备、fresh/SQL 连接/事务控制、notify/SSE。
artifact 行（undo journal/highlight/coding note/consensus freeze/trace/
import）的真实键域解析与再检查属 P13 ``team_flow_commit`` 提交层职责：那
一层持服务端 artifact ID，须以 ``mutation_footprint_on`` 解析实际单元格并
对 ``complete=False`` 整批拒绝；本层对「请求键即主体」的 operation 在同
一 pass 内做 footprint 完整性检查。这些仍是 C 在同窗口内的既有职责。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from coscreen import review_workflow as workflow_store
from coscreen import task_collaboration
from coscreen.team_flow_types import TeamOperation
from coscreen.team_history_guard import adjudicated_history_keys_on
from custom_backend.team_screening_runtime import (
    TeamFlowUnavailableError,
    team_chain_view,
    team_item_gate_reason,
    team_stage_view,
)
from custom_backend.team_write_footprints import mutation_footprint_on

if TYPE_CHECKING:  # pragma: no cover - typing only
    from custom_backend.team_screening_write import TeamWriteInputs

__all__ = [
    "TeamWriteBlocked",
    "TeamWriteOp",
    "TeamOperation",
    "require_team_write",
    "team_operation_target",
]

#: 旧名 re-export：P12 起唯一 operation DTO 是 P00 ``TeamOperation``。
#: 保留 ``TeamWriteOp`` 仅为既有调用/测试的兼容别名，二者是同一对象，
#: 不并存两个类（``team_write_checks.TeamWriteOp is TeamOperation``）。
TeamWriteOp = TeamOperation

#: 界面阶段 → P10 ``team_stage_view`` 的 ledger 阶段（F1/完成投影域）。
#: coding 无筛选裁决历史锁与完成投影，不进入此映射。
_STAGE_VIEW_STAGES = {"screen": "stage1", "fulltext": "stage2"}

#: P11 artifact 家族为「无（请求键即主体）」或可选 None（新增型）的
#: operation：本层在同一 pass 内做 footprint 完整性检查。其余（undo/
#: annotation_note/coding_note 更新/consensus_freeze/trace 族）需要服务端
#: artifact ID，由 P13 提交层解析真实键域后再检查（见模块头）。
_REQUEST_KEY_FOOTPRINT_OPS = frozenset({
    "member_vote",
    "member_coding",
    "member_appraisal",
    "member_annotation",  # artifact None＝新增；删除路径由 P13 按行解析
    "coding_rater_submit",
    "coding_consensus_append",
})


class TeamWriteBlocked(RuntimeError):
    """新资格/既有 policy 拒绝的结构化载荷（HTTP 层由 C 映射 409/403）。

    ``code``/``message_key``/``params`` 原样交给 C；``stage`` 为界面阶段，
    ``item_keys`` 是请求键中触发拒绝的排序去重集合，``version`` 在此刻已读
    到工作流版本时携带（policy operation 的 version 冲突/paused/source 拒绝
    与可用的 canonical/degraded 拒绝），供 C 保持既有 ``workflow_settings``
    载荷形状。P12 起「同阶段完成/当前一致」拒绝也是本类
    （``stage_completed_read_only``，409）；角色拒绝仍抛 ``TeamRole*``，
    本类不吞它们。P0x 原始 raw 历史锁的 typed fail-closed
    （``ArbitrationAttestationStoreUnavailable`` 等）原样透传。
    """

    status = 409

    def __init__(
        self,
        code: str,
        *,
        message_key: str | None = None,
        status: int = 409,
        params: Mapping[str, Any] | None = None,
        stage: str | None = None,
        item_keys: Iterable[str] = (),
        version: int | None = None,
    ) -> None:
        self.code = code
        self.message_key = message_key
        self.status = status
        self.params = dict(params or {})
        self.stage = stage
        self.item_keys = tuple(sorted(set(item_keys)))
        self.version = version
        super().__init__(f"team write blocked: {code}")


@dataclass(frozen=True)
class _StageRule:
    """单个 operation 在某界面阶段下的角色/链/历史规则。"""

    role: str                      # screener | coder | reconciler
    action: str | None             # 既有 _ACTION_ROLES 动作；screener 为 None
    chain_target: str | None       # None=无上游链检查
    history_stage: str | None      # None=无筛选仲裁历史锁/完成投影


@dataclass(frozen=True)
class _OperationSpec:
    target: str                    # actor | collab
    policy: bool                   # 是否施加既有 settings/expected/source门
    stages: Mapping[str, _StageRule]


def _rule(
    role: str,
    action: str | None,
    chain_target: str | None,
    history_stage: str | None,
) -> _StageRule:
    return _StageRule(role=role, action=action,
                      chain_target=chain_target, history_stage=history_stage)


_OPERATIONS: Mapping[str, _OperationSpec] = {
    "member_vote": _OperationSpec(target="actor", policy=True, stages={
        "screen": _rule("screener", None, None, "screen"),
        "fulltext": _rule("screener", None, "stage2", "fulltext"),
    }),
    "member_undo": _OperationSpec(target="actor", policy=True, stages={
        "screen": _rule("screener", None, None, "screen"),
        "fulltext": _rule("screener", None, "stage2", "fulltext"),
        "coding": _rule("coder", "coding:submit", "coding", None),
    }),
    "member_coding": _OperationSpec(target="actor", policy=True, stages={
        "coding": _rule("coder", "coding:submit", "coding", None),
    }),
    "member_coding_note": _OperationSpec(target="actor", policy=False, stages={
        "coding": _rule("coder", "coding:submit", "coding", None),
    }),
    "member_appraisal": _OperationSpec(target="actor", policy=False, stages={
        "coding": _rule("coder", "coding:submit", "appraisal", None),
    }),
    "member_annotation": _OperationSpec(target="actor", policy=False, stages={
        "fulltext": _rule("screener", None, "stage2", "fulltext"),
    }),
    "member_annotation_note": _OperationSpec(target="actor", policy=True, stages={
        "fulltext": _rule("screener", None, "stage2", "fulltext"),
    }),
    "coding_rater_submit": _OperationSpec(target="collab", policy=True, stages={
        "coding": _rule("coder", "coding:submit", "coding", None),
    }),
    "coding_consensus_append": _OperationSpec(target="collab", policy=False, stages={
        "coding": _rule("reconciler", "consensus:write", "coding", None),
    }),
    "coding_consensus_freeze": _OperationSpec(target="collab", policy=False, stages={
        "coding": _rule("reconciler", "consensus:freeze", "coding", None),
    }),
}


def _validated_stage(operation: TeamWriteOp) -> tuple[_OperationSpec, _StageRule]:
    if not isinstance(operation, TeamWriteOp):
        raise TypeError("operation 必须为 TeamWriteOp（P00 TeamOperation）")
    name = operation.name
    stage = operation.stage
    spec = _OPERATIONS.get(name) if isinstance(name, str) else None
    if spec is None:
        raise ValueError(f"未知 team 写 operation: {name!r}")
    if not isinstance(stage, str) or stage not in spec.stages:
        raise ValueError(f"operation {name!r} 不接受 stage {stage!r}")
    version = operation.expected_version
    if version is not None:
        if isinstance(version, bool) or type(version) is not int or version < 0:
            raise ValueError("expected_version 必须为非负整数")
        if not spec.policy:
            raise ValueError(
                f"operation {name!r} 不使用 expected_version（内部误用）")
    return spec, spec.stages[stage]


def team_operation_target(operation: TeamWriteOp) -> str:
    """返回 operation 的唯一业务目标：``'actor'`` 或 ``'collab'``。

    未列 name/stage 组合或非法 ``expected_version`` → ``ValueError``。
    """
    spec, _ = _validated_stage(operation)
    return spec.target


def _materialize_item_keys(item_keys: Iterable[str]) -> tuple[str, ...]:
    """一次性物化并逐项校验请求键；去重但绝不规范化键内容。"""
    if isinstance(item_keys, (str, bytes, bytearray)):
        raise ValueError("item_keys 必须为文本键的可迭代集合，不能传单个字符串")
    try:
        iterator = iter(item_keys)
    except TypeError as exc:
        raise ValueError("item_keys 必须为可迭代的文本键集合") from exc
    materialized: list[str] = []
    seen: set[str] = set()
    for key in iterator:
        if not isinstance(key, str) or not key:
            raise ValueError("item_keys 的每一项必须为非空文本")
        if key not in seen:
            seen.add(key)
            materialized.append(key)
    if not materialized:
        raise ValueError("item_keys 至少需要一个条目键")
    return tuple(materialized)


def _require_window_contract(inputs: TeamWriteInputs) -> None:
    """核保留窗口：原生连接、全部活动事务、四个主连接均在 connections 中。"""
    for name in ("ctx", "scope", "collab_conn", "owner_conn",
                 "actor_conn", "target_conn", "connections"):
        if not hasattr(inputs, name):
            raise TypeError(f"inputs 缺少 {name}（必须来自 retained_team_write_inputs）")
    connections = tuple(inputs.connections)
    if not connections:
        raise RuntimeError("保留窗口没有任何连接")
    for conn in connections:
        if not isinstance(conn, sqlite3.Connection):
            raise RuntimeError("保留窗口的全部连接必须为原生 sqlite3.Connection")
        # 已关闭连接在此处抛 sqlite3.ProgrammingError：保持原类型，不换壳。
        if not conn.in_transaction:
            raise RuntimeError("保留窗口的连接必须处于调用方活动事务中")
    for name in ("collab_conn", "owner_conn", "actor_conn", "target_conn"):
        conn = getattr(inputs, name)
        if not any(conn is candidate for candidate in connections):
            raise RuntimeError(f"inputs.{name} 不属于本保留窗口的连接集合")


def _require_team_host(inputs: TeamWriteInputs) -> tuple[Any, int]:
    """确认 team 宿主与当前 actor uid；绝不使用客户端提供的身份。"""
    ctx = inputs.ctx
    if getattr(ctx, "task_kind", None) != "team":
        raise RuntimeError("require_team_write 仅适用于已确认的 team 任务")
    info = getattr(ctx, "info", None)
    if info is None:
        raise RuntimeError("WorkflowContext 缺少 info")
    user = getattr(ctx, "user", None)
    user_id = user.get("user_id") if isinstance(user, dict) else None
    if isinstance(user_id, bool) or type(user_id) is not int or user_id <= 0:
        raise RuntimeError("WorkflowContext 缺少有效的认证 user_id")
    if not hasattr(ctx, "owner_db"):
        raise RuntimeError("WorkflowContext 缺少 owner_db")
    scope = inputs.scope
    for name in ("role_state", "canonical_keys", "task_id"):
        if not hasattr(scope, name):
            raise RuntimeError(f"scope 缺少 {name}（必须为 TSR TeamReadScope）")
    return info, user_id


def _require_role(
    role_state: Mapping[str, Any],
    actor_uid: int,
    rule: _StageRule,
    stage: str,
) -> None:
    """按 scope 锁内缓存的参与者事实核当前 actor；owner 无旁路。"""
    if role_state.get("classification") != "owner_match":
        raise TeamFlowUnavailableError(
            "unconfirmed_task_state",
            detail="写入检查要求当前锁内角色状态为 owner_match")
    entry = None
    for candidate in role_state.get("participants") or []:
        if isinstance(candidate, Mapping) and candidate.get("user_id") == actor_uid:
            entry = candidate
            break
    required_label = rule.role
    if entry is None:
        params: dict[str, Any] = {"participant": actor_uid}
        if rule.action is not None:
            params["action"] = rule.action
            params["required_role"] = required_label
        raise task_collaboration.TeamRoleDenied(
            "当前用户不是本任务参与者。", params=params)
    if entry.get("conflict") is True:
        raise task_collaboration.TeamRoleConflict(
            "该成员同时持有编码者与仲裁者授权，请先选择单一有效业务角色。",
            params={"participant": actor_uid})
    role = entry.get("role")
    if role is None:
        raise task_collaboration.TeamRoleUnconfigured(
            "该成员的业务角色尚未配置，请先在成员管理中选择编码者或仲裁者。",
            params={"participant": actor_uid})
    if rule.role == "screener":
        if entry.get("required_screener") is not True:
            # 旧 main/review_workflow_api 的 screening_not_enabled 载荷：C 层
            # 沿用其既有常量/文案，本层只给 code/message_key/status/stage。
            raise TeamWriteBlocked(
                "screening_not_enabled",
                message_key="team.screening_not_enabled",
                status=403,
                stage=stage,
            )
        return
    if role != rule.role:
        raise task_collaboration.TeamRoleDenied(
            "当前业务角色无权执行该操作。",
            params={"participant": actor_uid,
                    "action": rule.action,
                    "required_role": required_label},
        )


def _policy_settings(inputs: TeamWriteInputs, *, enabled: bool) -> dict[str, Any] | None:
    """只读读取当前 workflow settings（policy operation 才读；不施加判定）。"""
    if not enabled:
        return None
    ctx = inputs.ctx
    return workflow_store.settings(ctx.owner_db, conn=inputs.owner_conn)


def _current_version(settings: Mapping[str, Any] | None) -> int | None:
    version = settings.get("version") if settings is not None else None
    return version if isinstance(version, int) and not isinstance(version, bool) else None


def _require_canonical(
    inputs: TeamWriteInputs,
    keys: tuple[str, ...],
    stage: str,
    settings: Mapping[str, Any] | None,
) -> None:
    domain = set(inputs.scope.canonical_keys())
    offenders = [key for key in keys if key not in domain]
    if offenders:
        raise TeamWriteBlocked(
            "article_not_eligible",
            stage=stage,
            item_keys=offenders,
            version=_current_version(settings),
        )


def _require_policy(
    inputs: TeamWriteInputs,
    operation: TeamWriteOp,
    keys: tuple[str, ...],
    settings: Mapping[str, Any],
) -> None:
    """既有 settings/expected_version/paused/source 门（语义同旧 allowed_sources）。"""
    ctx = inputs.ctx
    version = _current_version(settings)
    if (operation.expected_version is not None
            and settings.get("version") != operation.expected_version):
        raise TeamWriteBlocked(
            "workflow_version_conflict",
            stage=operation.stage,
            version=version,
        )
    stage_policy = settings["stages"][operation.stage]
    if stage_policy.get("mode") == "paused":
        raise TeamWriteBlocked(
            "stage_paused", stage=operation.stage, version=version)
    sources = workflow_store.sources(ctx.owner_db, conn=inputs.owner_conn)
    active = {source["id"] for source in sources if source.get("active")}
    if stage_policy.get("mode") == "selected":
        allowed = active & set(stage_policy.get("source_ids") or ())
    else:
        allowed = set(active)
    links = workflow_store.source_links(ctx.owner_db, conn=inputs.owner_conn)
    offenders: list[str] = []
    for key in keys:
        linked = links.get(key)
        if not linked:
            linked = {workflow_store.UNASSIGNED}
        if not set(linked) & allowed:
            offenders.append(key)
    if offenders:
        raise TeamWriteBlocked(
            "article_outside_scope",
            stage=operation.stage,
            item_keys=offenders,
            version=version,
        )


def _require_chain(
    inputs: TeamWriteInputs,
    rule: _StageRule,
    keys: tuple[str, ...],
    stage: str,
) -> None:
    """跨阶段链检查：只认 ``cleared is True``；FINAL_EXCLUDE 永远阻断。

    P09 键级 issue（读取/发现期无法定链的键）用 ``KeyIssue.reason`` 做
    键级错误整批拒绝；仅在无 issue 词可用时回落到既有
    ``team_item_upstream_stage1_not_evaluated`` 令牌。
    """
    target = rule.chain_target
    if target is None:
        return
    # HIGH#1 B2（2026-11-20）：链检查切换到按键路径（P09 按键视图）——
    # 自动消费写窗 plan 见证（含 B1 的 fulltext 双阶段覆盖）：持久 seal
    # （unanimous/adjudicated）即 settled、发现层冲突键级 fail-closed，
    # 不再要求**当前**名册全员持有上游票（GAP-1 红侧 m02b）。keyed 合同
    # set(keys) <= set(plan.item_keys) 天然成立（plan 即由同一写 op 的
    # keys 构建）。域外键令牌随之与读侧一致化为 key_not_in_domain
    # （2026-11-20 裁定 3）。
    view = team_chain_view(inputs.scope, target, item_keys=keys)
    blocked: dict[str, str] = {}
    for key in keys:
        issue = view.issues.get(key)
        if issue is not None:
            blocked[key] = issue.reason
            continue
        item = view.chain.get(key)
        if item is None:
            blocked[key] = "team_item_upstream_stage1_not_evaluated"
            continue
        if getattr(item, "cleared", False) is True:
            continue
        reason = team_item_gate_reason(item)
        blocked[key] = reason or "team_item_unknown"
    if blocked:
        first = sorted(blocked)[0]
        raise TeamWriteBlocked(
            blocked[first], stage=stage, item_keys=blocked.keys())


def _require_stage_open(
    inputs: TeamWriteInputs,
    rule: _StageRule,
    keys: tuple[str, ...],
    stage: str,
) -> None:
    """同阶段完成/当前一致拒写（P12 统一纯检查；owner 亦无旁路）。

    三类事实任一命中即整批拒绝（零业务写、不补 seal）：

    - P10 ``team_stage_view`` 的键级 ``completion`` 投影：F1 见证
      （``persisted=True``）或可信当前一致（live consensus/已适用仲裁，
      ``persisted=False``）——完成事实与 actor 无关，空 actor row（新成员
      无任何旧票/痕迹）不构成例外；
    - P10 同视图的键级 ``issues``：完成事实不可判定（holder 不可读、证明
      陈旧等）按 ``KeyIssue.reason`` 键级 fail-closed 拒绝；
    - P0x raw 已生效仲裁历史锁 ``adjudicated_history_keys_on``：原始
      attestation 事实，proof 陈旧/形状坏不解锁（typed fail-closed
      原样透传）。

    拒绝统一为 ``TeamWriteBlocked('stage_completed_read_only')``（409），
    ``item_keys`` 携带触发键并集（排序去重）。coding 无筛选完成投影，
    不外推成 coding 历史锁。
    """
    history_stage = rule.history_stage
    if history_stage is None:
        return
    view = team_stage_view(
        inputs.scope, _STAGE_VIEW_STAGES[history_stage], item_keys=keys)
    issue_keys = [key for key in keys if key in view.issues]
    if issue_keys:
        first = sorted(issue_keys)[0]
        raise TeamWriteBlocked(
            view.issues[first].reason, stage=stage, item_keys=issue_keys)
    blocked = [key for key in keys if key in view.completion]
    protected = adjudicated_history_keys_on(
        inputs.collab_conn, task_id=inputs.scope.task_id, stage=history_stage)
    blocked.extend(key for key in keys if key in protected)
    if blocked:
        raise TeamWriteBlocked(
            "stage_completed_read_only",
            message_key="team.stage_completed_read_only",
            stage=stage,
            item_keys=blocked,
        )


def _require_footprint(
    inputs: TeamWriteInputs,
    operation: TeamWriteOp,
    keys: tuple[str, ...],
) -> None:
    """P11 footprint 一次纯检查（「请求键即主体」的 operation）。

    在保留窗口的目标连接上解析请求键主体 footprint；``complete=False``
    （不能安全执行）→ 键级整批拒绝，绝不降级成空集放行。artifact 家族
    operation（undo/note 更新/freeze/trace 族）的真实键域由 P13 提交层
    持服务端 artifact ID 解析（见模块头），本层不猜。
    """
    if operation.name not in _REQUEST_KEY_FOOTPRINT_OPS:
        return
    footprint = mutation_footprint_on(
        inputs.target_conn, operation=operation,
        artifact_id=None, request_keys=keys)
    if not footprint.complete:
        raise TeamWriteBlocked(
            "mutation_footprint_unresolvable",
            stage=operation.stage,
            item_keys=keys,
        )


def require_team_write(
    inputs: TeamWriteInputs,
    *,
    operation: TeamWriteOp,
    item_keys: Iterable[str],
) -> None:
    """在保留窗口内完成全部纯检查；通过返回 ``None``，否则抛结构化异常。

    顺序：operation/keys 校验 → 目标身份与连接契约 → team/角色 → degraded →
    canonical → policy（仅上表标记为是的 operation）→ 链 → 同阶段完成/
    当前一致/raw 历史锁（``stage_completed_read_only``）→ footprint。
    所有请求键先一次性物化校验，任一拒绝整批失败，零业务写入；本函数不
    BEGIN/COMMIT/ROLLBACK/CLOSE、不执行 DDL/迁移、不改 row_factory、不做
    notify/SSE、不初始化 trace。多个键的链/策略/完成拒绝在 ``item_keys``
    中给出全部触发键，保持批量全拒语义。首票例外已移除（2026-11-15 裁定）：
    同阶段已完成的键对新成员（空 actor row）同样拒绝。
    """
    spec, rule = _validated_stage(operation)
    keys = _materialize_item_keys(item_keys)
    _require_window_contract(inputs)
    if spec.target == "actor":
        if inputs.target_conn is not inputs.actor_conn:
            raise RuntimeError(
                f"operation {operation.name!r} 的目标必须是 actor_conn")
    else:
        if inputs.target_conn is not inputs.collab_conn:
            raise RuntimeError(
                f"operation {operation.name!r} 的目标必须是 collab_conn")
    info, actor_uid = _require_team_host(inputs)
    role_state = inputs.scope.role_state()
    _require_role(role_state, actor_uid, rule, operation.stage)
    if getattr(info, "degraded", False) is True:
        raise TeamWriteBlocked("task_read_only", stage=operation.stage)
    settings = _policy_settings(inputs, enabled=spec.policy)
    _require_canonical(inputs, keys, operation.stage, settings)
    if settings is not None:
        _require_policy(inputs, operation, keys, settings)
    _require_chain(inputs, rule, keys, operation.stage)
    _require_stage_open(inputs, rule, keys, operation.stage)
    _require_footprint(inputs, operation, keys)
