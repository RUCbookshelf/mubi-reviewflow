# -*- coding: utf-8 -*-
"""团队逐条筛选资格纯核心（FLOW-01 / TEAM_FLOW_REPAIR_CONTRACT_20261114 方案甲·阶段I）。

本模块是**纯函数资格服务**：零 I/O、零第三方依赖、零回写。调用方（后续接入
层的队列/计数/写闸/任务载荷）必须传入**显式解析完成**的输入：

- ``StageInputs.participants``：已解析的团队名册与业务角色配置状态。业务角色
  单值 coder/reconciler，属主也不例外（OWNER_BUSINESS_ROLE_DECISION_20261114：
  管理身份不授业务旁路）；属主 ``role`` 未配置或双角色冲突即待配置，**不猜选、
  不给默认值、不视为「已全员」**。
- ``StageInputs.required``：本阶段应筛参与者名（调用方按角色配置与参与开关
  解析）。非空已声明集合才合法，1 人集合合法；旧 CSV 合并工具的 2–9 包限制
  不借入本层。0 人=未配置，空集合不得自动满足。
- ``StageInputs.votes``：当前逐条判断（按人→按键）。正式判断域为
  include/exclude **二选一**；历史 ``maybe`` 是可识别的未完成值（不拒绝、
  不删除、计为待完成，见 awaiting_members）。非必需成员（如未开参与的
  仲裁者）的历史意见不参与终局计算，也不被删除。
- ``StageInputs.arbitrations``：有来源资格的仲裁快照，且须全部来自**同一
  仲裁持久store**（装载层职责）；``record_id`` 为该store自增正整数主键。
  来源资格（store 归属验证、保存链合法性）由装载层负责；本模块只做
  **逐条绑定校验**。采信仲裁结论要求 ``inputs.task_id`` 与记录 ``task_id``
  **双方均为显式非空标识且相等**——None/空串不是「当前任务」通配，
  ``task_id`` 只能由装载层验证 store 归属后填入，本模块不把「未声明」当
  「当前」。纯共识评估（不带采信仲裁）不强制 task_id。

逐条状态语义（合同 §3）
----------------------
- ``awaiting_config``：必需参与者为空，或名册中任一成员（含属主）业务角色
  未配置/双角色冲突。优先级最高，全阶段统一。
- ``awaiting_members``：任一必需成员本轮未完成该条目——未给判断，或仅给出
  历史 ``maybe`` 等非正式值（正式有效判断域严格 include/exclude 二选一；
  maybe 持有者必须先补齐二选一，不得计入「已完成」、不得进入仲裁、更不能
  经仲裁快照终局；初筛/复筛同规则）。带缺席名单与逐成员机读原因
  （missing_vote / non_formal_value）。
- ``awaiting_arbitration``：必需成员均已给出正式二选一判断但纳/排分歧，
  且无仍适用的授权仲裁结论（带对该条目已过期/不适用的记录清单与原因）。
- ``final_include`` / ``final_exclude``：全员一致自动终局，或经绑定校验的
  授权仲裁终局。

仲裁绑定（合同 §4「逐条」）
--------------------------
对每条记录逐键独立校验，**B 的判断变化不废 A 的记录、A 自身变化废 A**：

1. 记录级 scope：stage 一致；task_id **双方显式非空且相等**（None/空串
   不是「当前任务」通配，绑定不可验证即不采信）；round_id 严格相等——
   双方 None 仅表示同一个明确的未分轮流，不是可匹配任意已命名轮次的
   通配。任一不匹配=无关记录，不计入该键的过期清单；
2. 快照行 ``decision_<name>`` 名集 == 当前必需参与集（成员集合变化即过期）；
3. 逐成员 ``decision`` / ``exclusion_reason`` / ``notes`` 字段级相等；
4. 成员判断时间 <= 快照 created_at（任一侧时间缺失/不可比 = 过期，
   fail-closed：宁可重裁，不放行过期结论）；
5. ``arbitration_status == "adjudicated"`` 且 ``final_decision`` 为
   include/exclude。

多条记录都适用时取**数值 record_id 最大者**为最新适用记录（record_id 为
同一仲裁持久store的自增正整数主键，数值即追加顺序；created_at 只用于绑定
的时间先后证明，不作决定新旧的主排序键——墙钟可回拨，追加序不因时钟倒退
翻转）。**最新但不适用的记录不能遮蔽仍适用的旧记录**。全员一致优先于仲裁
记录自动终局（个人意见改回一致后以当前一致为准）。仲裁终局 include 后，
曾个人 exclude 的应筛成员同样推进，其原始意见与历史记录不变。

跨阶段链（合同 §3「复筛→编码/评价」）
------------------------------------
- 复筛（stage2）：要求初筛（stage1）当前 ``final_include``；
- 编码/评价：要求复筛 ``final_include`` **且**初筛当前 ``final_include``
  （不能用旧复筛终局绕过已变化的上游状态——两段状态同一读时快照派生）。

时间戳：调用方须把个人判断时间与快照时间解析为 aware UTC ``datetime``。
历史库本地时间的换算是装载层职责；本模块把 naive ``datetime`` 按 UTC 处理
（fail-closed 方向见上）。快照行/成员列名沿用 ``merge_decisions_multi`` /
``resolve_decision_conflicts`` 的合并表列形（``decision_<name>``、
``exclusion_reason_<name>``、``notes_<name>``、``arbitration_status``、
``final_decision``），与 ``prisma_task_metrics.outcomes`` 的三关校验同口径。

本模块不接触路由、权限、存储符号；不回写、不删除任何个人判断、编码、
评价或历史仲裁（一切状态读时派生）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

#: 仲裁层的阶段值（与 screening_arbitration 一致；stage1=初筛，stage2=复筛）。
STAGES = ("stage1", "stage2")

#: 跨阶段链目标（编码/评价共享同一上游要求）。
CHAIN_TARGETS = ("stage2", "coding", "appraisal")

#: 合法业务角色（属主同规则，单值）。
BUSINESS_ROLES = ("coder", "reconciler")

#: 个人判断可识别取值域（与 coscreen.config.DECISIONS 同集）：域外值（如
#: yes）在输入校验即拒绝；maybe 是可识别的历史未完成值，不是正式判断。
VOTE_DECISIONS = frozenset({"include", "exclude", "maybe"})

#: 正式有效判断域：正式筛选阶段二选一（binary_screening v2.0 / 合同 §3）。
#: 历史 maybe 计为待完成（non_formal_value），不得计入「已完成」、不得
#: 进入仲裁或经仲裁快照终局。
FORMAL_VOTE_DECISIONS = frozenset({"include", "exclude"})

#: 仲裁结论取值域（ARBITRATION_DECISIONS 同集：二选一）。
ARBITRATION_FINALS = frozenset({"include", "exclude"})

AWAITING_CONFIG = "awaiting_config"
AWAITING_MEMBERS = "awaiting_members"
AWAITING_ARBITRATION = "awaiting_arbitration"
FINAL_INCLUDE = "final_include"
FINAL_EXCLUDE = "final_exclude"

#: 逐条状态全集（流程状态不是新的投票选项，正式判断仍二选一）。
ITEM_STATUSES = (
    AWAITING_CONFIG,
    AWAITING_MEMBERS,
    AWAITING_ARBITRATION,
    FINAL_INCLUDE,
    FINAL_EXCLUDE,
)

#: 必需成员待完成原因令牌（awaiting_members 的机读细节）。
MISSING_VOTE = "missing_vote"              # 未给判断（键缺席）
NON_FORMAL_VALUE = "non_formal_value"      # 历史 maybe 等非正式值，二选一未完成

#: 绑定校验失败原因（机器令牌，供「过期结论原因」展示）。
STALE_PARTICIPANTS = "participants"        # 名集 != 当前必需参与集
STALE_VOTES = "votes"                      # decision/exclusion_reason/notes 不等
STALE_VOTE_TIME = "vote_time"              # 判断时间晚于快照时间
STALE_RECORD_TIME = "record_time"          # 快照时间缺失，无法核验先后
STALE_INVALID_FINAL = "invalid_final"      # 非 adjudicated 或结论非二选一


def _aware(value: datetime) -> datetime:
    """naive datetime 按 UTC 处理（装载层应已完成本地时间换算，见模块头）。"""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _text(value: Any) -> str:
    """合并快照字段的规约读法：None/缺失 -> ''，其余 str()（不 strip，
    与 outcomes 的 ``str(x or '')`` 字段级比较同口径）。"""
    return "" if value is None else str(value)


def _decision_text(value: Any) -> str:
    """决策值比较前的规约：strip + lower（与 _validated_merge 同口径）。"""
    return _text(value).strip().lower()


@dataclass(frozen=True)
class Participant:
    """名册成员的业务角色配置状态（调用方已解析；本模块不做任何推导）。

    ``role`` 为 ``None`` 表示业务角色未配置（含属主未选择）；``dual_role``
    为 True 表示同时持有 coder/reconciler（既有 grants 主键允许双角色）。
    两者任一出现即 ``awaiting_config``：不猜选、不删历史，管理功能仍可用于
    完成配置。
    """

    name: str
    role: str | None = None
    dual_role: bool = False
    is_owner: bool = False


@dataclass(frozen=True)
class Vote:
    """一名必需成员对一条文献的当前判断。

    ``decision`` 正式取值 include/exclude（二选一）；历史 ``maybe`` 是可
    识别的未完成值——保留输入、不删不改、计为待完成（non_formal_value），
    不是可裁的正式判断。``decided_at`` 为该判断的落库时间（aware UTC；
    None=不可核验时间）。
    """

    decision: str
    exclusion_reason: str = ""
    notes: str = ""
    decided_at: datetime | None = None


@dataclass(frozen=True)
class ArbitrationRecord:
    """一条有来源资格的仲裁快照（append-only 记录的解析视图）。

    ``rows`` 键为 zotero_key，值为合并表行（含 ``decision_<name>``、
    ``exclusion_reason_<name>``、``notes_<name>``、``arbitration_status``、
    ``final_decision``）。``record_id`` 为所属仲裁持久store的自增**正整数**
    主键（评估时校验；bool/非整数/非正数一律拒绝），数值大小即追加顺序，
    多条适用时取数值最大者为最新。``task_id=None`` **不表示**「当前任务」：
    采信要求双方显式非空且相等，绑定不可验证即按无关记录处理。
    ``round_id=None`` 表示该记录属于明确的未分轮流，与 ``inputs.round_id``
    严格相等才匹配（None 只匹配 None，不是可匹配任意已命名轮次的通配）。
    """

    record_id: int
    stage: str
    created_at: datetime | None = None
    arbitrator_id: str | None = None
    source: str = ""
    task_id: str | None = None
    round_id: str | None = None
    rows: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)


@dataclass(frozen=True)
class ConfigurationStatus:
    """名册/角色配置完备性（awaiting_config 的判定与细节）。"""

    ok: bool
    empty_required: bool = False
    unconfigured: tuple[str, ...] = ()     # role 未配置（含属主未选择）
    conflicting: tuple[str, ...] = ()      # 双角色冲突
    required: tuple[str, ...] = ()

    @property
    def detail(self) -> str:
        if self.empty_required:
            return "no_required_participants"
        if self.unconfigured:
            return "role_unconfigured:" + ",".join(self.unconfigured)
        if self.conflicting:
            return "role_conflict:" + ",".join(self.conflicting)
        return ""


@dataclass(frozen=True)
class StageInputs:
    """单阶段逐条资格的全部输入（显式解析；本模块不做任何反推）。

    ``required``/``keys`` 声明为 Iterable：构造时**一次性物化**为不可耗尽的
    tuple，同一实例可重复评估且结果一致（生成器等一次性输入不会在第二次
    评估时被耗尽）；``keys`` 为 None 保持 None（键域缺省=全部判断键的并集）。
    ``task_id`` 仅在采信仲裁结论时必须显式非空（见模块头）；纯共识评估
    可不带。
    """

    stage: str
    participants: tuple[Participant, ...] = ()
    required: Iterable[str] = ()
    votes: Mapping[str, Mapping[str, Vote]] = field(default_factory=dict)
    arbitrations: tuple[ArbitrationRecord, ...] = ()
    task_id: str | None = None
    round_id: str | None = None
    keys: Iterable[str] | None = None      # 键域；None=全部判断键的并集

    def __post_init__(self) -> None:
        # Iterable 输入合同：构造时定影，保证纯函数可重复评估。
        object.__setattr__(self, "required", tuple(self.required))
        if self.keys is not None:
            object.__setattr__(self, "keys", tuple(self.keys))


@dataclass(frozen=True)
class ItemStatus:
    """一条文献在一个筛选阶段的逐条状态。"""

    key: str
    stage: str
    status: str
    pending_members: tuple[str, ...] = ()          # awaiting_members 的缺席名单
    #: awaiting_members 时逐成员的待完成原因（与 pending_members 同序对齐）：
    #: missing_vote=未给判断；non_formal_value=历史 maybe 等非正式值。
    pending_reasons: tuple[tuple[str, str], ...] = ()
    #: awaiting_arbitration **或仲裁终局**时：对该键存在快照行但绑定失效的
    #: (record_id, 原因) 清单（终局经仲裁的条目也会回填过期记录明细）。
    stale_arbitrations: tuple[tuple[int, str], ...] = ()
    #: 终局经仲裁时采信的 record_id（一致终局/其他状态为 None）。
    applied_arbitration: int | None = None
    decided_by: str | None = None                  # "consensus" | "arbitration"


@dataclass(frozen=True)
class ChainItem:
    """跨阶段链上一条文献对目标阶段的资格。

    ``cleared`` 仅表示**上游终局纳入条件**满足（stage2 目标=初筛终局纳入；
    coding/appraisal 目标=复筛与初筛都终局纳入）。目标阶段自身的筛选进度
    看 ``stage1_status`` / ``stage2_status``，不在本判定内。上游映射缺该键
    =该键未在对应阶段评估（缺输入诊断）：状态记 None、阻断令牌用
    ``upstream_<stage>_not_evaluated``，与真实的 ``awaiting_config`` 配置
    缺失区分，不为诊断扩充 evaluate_stage 的五种正式流程状态。
    """

    key: str
    target: str
    stage1_status: str | None    # None=该键未在初筛评估（键域缺键），非配置缺失
    stage2_status: str | None    # None=该键未在复筛评估（或目标=stage2 无此上游）
    cleared: bool
    #: 未通过时为 ``upstream_<stage>_<status>``（如 upstream_stage1_final_exclude）。
    blocked_reason: str | None


def _validated_name(value: Any, what: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{what} must be text")
    name = value.strip()
    if not name or len(name) > 100 or name != value:
        raise ValueError(f"{what} must be 1 to 100 characters without surrounding blanks")
    return name


def _validated_participants(participants: tuple[Participant, ...]) -> dict[str, Participant]:
    roster: dict[str, Participant] = {}
    for participant in participants:
        if not isinstance(participant, Participant):
            raise ValueError("participants must contain Participant instances")
        name = _validated_name(participant.name, "participant name")
        if name in roster:
            raise ValueError(f"duplicate participant name: {name}")
        if participant.dual_role:
            if participant.role is not None:
                raise ValueError(f"dual-role participant must leave role unchosen: {name}")
        elif participant.role is not None and participant.role not in BUSINESS_ROLES:
            # role=None 合法（=业务角色未配置，awaiting_config 处理，不猜选）。
            raise ValueError(
                f"participant role must be one of {BUSINESS_ROLES} or None: {name}")
        roster[name] = participant
    return roster


def configuration_status(inputs: StageInputs) -> ConfigurationStatus:
    """名册/角色配置是否完备（awaiting_config 判定，与键无关）。"""
    if inputs.stage not in STAGES:
        raise ValueError(f"stage must be one of {STAGES}")
    roster = _validated_participants(inputs.participants)
    required = tuple(_validated_name(name, "required participant") for name in inputs.required)
    if len(set(required)) != len(required):
        raise ValueError("required participants must not repeat")
    unknown = [name for name in required if name not in roster]
    if unknown:
        raise ValueError(f"required participants missing from roster: {sorted(unknown)}")
    unconfigured = tuple(sorted(
        name for name, participant in roster.items()
        if participant.role is None and not participant.dual_role))
    conflicting = tuple(sorted(
        name for name, participant in roster.items() if participant.dual_role))
    return ConfigurationStatus(
        ok=bool(required) and not unconfigured and not conflicting,
        empty_required=not required,
        unconfigured=unconfigured,
        conflicting=conflicting,
        required=required,
    )


def _key_domain(inputs: StageInputs) -> list[str]:
    if inputs.keys is not None:
        keys = [_validated_name(key, "key") for key in inputs.keys]
        if len(set(keys)) != len(keys):
            raise ValueError("keys must not repeat")
        return keys
    domain: set[str] = set()
    for by_key in inputs.votes.values():
        domain.update(by_key)
    return sorted(domain)


def _validated_votes(
    inputs: StageInputs, roster: dict[str, Participant],
) -> dict[str, dict[str, Vote]]:
    parsed: dict[str, dict[str, Vote]] = {}
    for name, by_key in inputs.votes.items():
        canonical = _validated_name(name, "voter name")
        if canonical not in roster:
            raise ValueError(f"votes contain a non-roster participant: {canonical}")
        bucket = parsed.setdefault(canonical, {})
        for key, vote in by_key.items():
            if not isinstance(vote, Vote):
                raise ValueError(f"vote for {canonical} must be a Vote instance")
            canonical_key = _validated_name(key, "vote key")
            if vote.decision not in VOTE_DECISIONS:
                raise ValueError(
                    f"vote decision must be one of {sorted(VOTE_DECISIONS)}: "
                    f"{canonical}/{canonical_key}")
            bucket[canonical_key] = vote
    return parsed


def _validated_arbitrations(
    arbitrations: tuple[ArbitrationRecord, ...],
) -> None:
    """仲裁输入的机读合同：实例类型与 record_id（新旧排序依据）必须可验证。

    record_id 收紧为所属持久store的自增**正整数**主键：bool（int 的子类但
    语义是开关）、非整数、非正数一律 ValueError——不给无法验证数值顺序的
    ID 授予「最新」含义，也不让 bool 借 0/1 混入优先级。
    """
    for record in arbitrations:
        if not isinstance(record, ArbitrationRecord):
            raise ValueError("arbitrations must contain ArbitrationRecord instances")
        rid = record.record_id
        if isinstance(rid, bool) or not isinstance(rid, int) or rid <= 0:
            raise ValueError(
                f"arbitration record_id must be a positive integer store id: {rid!r}")


def _explicit_task_id(value: Any) -> bool:
    """任务标识是否为显式非空文本（None/空白串都不是「当前任务」通配）。"""
    return isinstance(value, str) and value.strip() != ""


def _scope_matches(record: ArbitrationRecord, inputs: StageInputs) -> bool:
    """记录级 scope 过滤：stage/task/round 任一不匹配即为无关记录（非过期）。"""
    if record.stage != inputs.stage:
        return False
    if (not _explicit_task_id(inputs.task_id)
            or not _explicit_task_id(record.task_id)):
        # 任务绑定必须双方显式：调用方漏声明（None）时采信不了任何有/无
        # task 的记录，两侧都 None 也不算已绑定——fail-closed。
        return False
    if record.task_id != inputs.task_id:
        return False
    if record.round_id != inputs.round_id:
        return False
    return True


def _row_binding_failure(
    row: Mapping[str, Any],
    record: ArbitrationRecord,
    key: str,
    required: tuple[str, ...],
    votes: dict[str, dict[str, Vote]],
) -> str | None:
    """单条快照行对当前状态的绑定校验；返回 None=适用，否则失败原因令牌。

    与 prisma_task_metrics.outcomes 的三关同口径：①名集相等 ②逐成员字段级
    相等 ③判断时间 <= 快照时间。
    """
    snapshot_names = {
        column[len("decision_"):] for column in row if column.startswith("decision_")
    }
    if snapshot_names != set(required):
        return STALE_PARTICIPANTS
    for name in required:
        live = votes[name].get(key)
        if live is None:
            return STALE_VOTES
        if _decision_text(row.get(f"decision_{name}")) != _decision_text(live.decision):
            return STALE_VOTES
        if _text(row.get(f"exclusion_reason_{name}")) != _text(live.exclusion_reason):
            return STALE_VOTES
        if _text(row.get(f"notes_{name}")) != _text(live.notes):
            return STALE_VOTES
    if record.created_at is None:
        # 无快照时间即无法核验「判断未晚于快照」——fail-closed 过期。
        return STALE_RECORD_TIME
    snapshot_time = _aware(record.created_at)
    for name in required:
        live = votes[name][key]
        if live.decided_at is None:
            return STALE_VOTE_TIME
        if _aware(live.decided_at) > snapshot_time:
            return STALE_VOTE_TIME
    return None


def _row_final(row: Mapping[str, Any]) -> str | None:
    """快照行的仲裁终局值；非 adjudicated 或非二选一返回 None。"""
    if row.get("arbitration_status") != "adjudicated":
        return None
    final = _decision_text(row.get("final_decision"))
    return final if final in ARBITRATION_FINALS else None


def _member_pending_reason(vote: Vote | None) -> str | None:
    """必需成员对该键的待完成原因；None=已给出正式二选一判断。

    未给判断（键缺席）= missing_vote；仅给历史 maybe 等非正式值 =
    non_formal_value——都算未完成，不删不改原 Vote。
    """
    if vote is None:
        return MISSING_VOTE
    if vote.decision not in FORMAL_VOTE_DECISIONS:
        return NON_FORMAL_VALUE
    return None


def _evaluate_item(
    inputs: StageInputs,
    key: str,
    config: ConfigurationStatus,
    votes: dict[str, dict[str, Vote]],
) -> ItemStatus:
    if not config.ok:
        return ItemStatus(key=key, stage=inputs.stage, status=AWAITING_CONFIG)
    pending_reasons = tuple(sorted(
        (name, reason)
        for name in config.required
        if (reason := _member_pending_reason(votes.get(name, {}).get(key))) is not None))
    if pending_reasons:
        # 未给判断或仅给历史 maybe 等非正式值=未完成：先补齐二选一，
        # 不进入仲裁、更不能经仲裁快照终局。
        return ItemStatus(
            key=key, stage=inputs.stage, status=AWAITING_MEMBERS,
            pending_members=tuple(name for name, _ in pending_reasons),
            pending_reasons=pending_reasons)
    values = {votes[name][key].decision for name in config.required}
    # 走到这里全员均已给出正式二选一判断；全员一致优先自动终局。
    if values == {"include"}:
        return ItemStatus(
            key=key, stage=inputs.stage, status=FINAL_INCLUDE, decided_by="consensus")
    if values == {"exclude"}:
        return ItemStatus(
            key=key, stage=inputs.stage, status=FINAL_EXCLUDE, decided_by="consensus")
    # 分歧：查与当前状态绑定的授权仲裁结论；逐条独立（B 变不废 A）。
    stale: list[tuple[int, str]] = []
    applicable: list[tuple[int, ArbitrationRecord, str]] = []
    for record in inputs.arbitrations:
        if not _scope_matches(record, inputs):
            continue
        row = record.rows.get(key)
        if row is None:
            continue
        failure = _row_binding_failure(row, record, key, config.required, votes)
        if failure is not None:
            stale.append((record.record_id, failure))
            continue
        final = _row_final(row)
        if final is None:
            stale.append((record.record_id, STALE_INVALID_FINAL))
            continue
        applicable.append((record.record_id, record, final))
    if applicable:
        # 多条适用取数值 record_id 最大者（同store自增追加序；created_at 只
        # 作绑定的时间先后证明）；最新不适用不能遮蔽旧适用。
        _, record, final = max(applicable, key=lambda entry: entry[0])
        return ItemStatus(
            key=key, stage=inputs.stage,
            status=FINAL_INCLUDE if final == "include" else FINAL_EXCLUDE,
            stale_arbitrations=tuple(stale),
            applied_arbitration=record.record_id, decided_by="arbitration")
    return ItemStatus(
        key=key, stage=inputs.stage, status=AWAITING_ARBITRATION,
        stale_arbitrations=tuple(stale))


def evaluate_stage(inputs: StageInputs) -> dict[str, ItemStatus]:
    """计算一个阶段内全部（或指定键域内）文献的逐条状态。

    键域缺省为全部判断键的并集；无人投过的键同样参与评估（awaiting_members
    ——无决定成员仍是待完成者）。配置不完备时全键 awaiting_config。
    """
    config = configuration_status(inputs)
    roster = _validated_participants(inputs.participants)
    votes = _validated_votes(inputs, roster)
    _validated_arbitrations(inputs.arbitrations)
    return {
        key: _evaluate_item(inputs, key, config, votes)
        for key in _key_domain(inputs)
    }


def _chain_status(
    target: str,
    key: str,
    stage1: Mapping[str, ItemStatus],
    stage2: Mapping[str, ItemStatus],
) -> ChainItem:
    first = stage1.get(key)
    second = stage2.get(key)
    # None=该键未在对应阶段评估（键域缺键）：缺输入诊断，不是配置缺失；
    # 不为诊断扩充 evaluate_stage 的五种正式流程状态。
    first_status = first.status if first is not None else None
    second_status = second.status if second is not None else None
    if first_status != FINAL_INCLUDE:
        first_token = first.status if first is not None else "not_evaluated"
        return ChainItem(
            key=key, target=target, stage1_status=first_status,
            stage2_status=second_status, cleared=False,
            blocked_reason=f"upstream_stage1_{first_token}")
    if target != "stage2":
        if second is None:
            # 复筛评估缺该键（键域不含）= 无法证实上游复筛终局，按未通过处理。
            return ChainItem(
                key=key, target=target, stage1_status=first_status,
                stage2_status=None, cleared=False,
                blocked_reason="upstream_stage2_not_evaluated")
        if second.status != FINAL_INCLUDE:
            return ChainItem(
                key=key, target=target, stage1_status=first_status,
                stage2_status=second.status, cleared=False,
                blocked_reason=f"upstream_stage2_{second.status}")
    return ChainItem(
        key=key, target=target, stage1_status=first_status,
        stage2_status=second_status, cleared=True, blocked_reason=None)


def evaluate_chain(
    stage1: Mapping[str, ItemStatus],
    stage2: Mapping[str, ItemStatus],
    target: str,
    keys: Iterable[str] | None = None,
) -> dict[str, ChainItem]:
    """跨阶段链资格：stage2 需初筛终局纳入；coding/appraisal 需复筛与初筛
    都终局纳入（同一读时快照派生，防旧复筛终局绕过已变化的上游初筛）。

    ``stage1``/``stage2`` 为 :func:`evaluate_stage` 的输出。键域缺省为两段
    评估键的并集；映射缺某键=该键未在该阶段评估（状态 None、阻断令牌
    ``upstream_<stage>_not_evaluated``）。个人意见不参与本判定：仲裁终局
    include 后，曾个人 exclude 的成员同样获得该条目的下游资格。
    """
    if target not in CHAIN_TARGETS:
        raise ValueError(f"target must be one of {CHAIN_TARGETS}")
    domain: Iterable[str]
    if keys is not None:
        domain = keys
    else:
        domain = sorted(set(stage1) | set(stage2))
    return {key: _chain_status(target, key, stage1, stage2) for key in domain}


def cleared_keys(chain: Mapping[str, ChainItem]) -> list[str]:
    """链评估中上游终局纳入条件满足的键（工作队列的键集来源）。"""
    return [key for key, item in chain.items() if item.cleared]
