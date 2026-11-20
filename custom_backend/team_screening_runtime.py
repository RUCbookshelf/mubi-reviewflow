"""团队逐条资格只读装载服务（TSR，2.1.6 FLOW S1c）。

设计权威（冻结契约，实现不偏离）：
- ``out/maintenance216/s1c_runtime_design.md`` §F/§F2/§F2.6；
- ``out/maintenance216/s1c_runtime_ruling_sol.md``（Sol 最终裁定）；
- 核心 ``coscreen/team_screening_eligibility``（已独立 PASS，只调用不修改）。

合同要点：
- **只读**：一切库连接 ``mode=ro``（fresh 自开自收尾，单库单 DEFERRED 快照、
  scope 生命周期内两阶段视图同快照）或调用方既有连接（borrowed：只读所给
  连接，绝不 BEGIN/commit/rollback/close/PRAGMA/row_factory/open/stat/
  resolve——完整规范路径映射由 S2 作为输入保证，缺连接且不在
  ``missing_members``＝编程违约）。零建库/零 DDL/零迁移/零 ensure。
- **账户**：只经 scope 连接读 ``users``（id/username 两列）；全账户
  casefold 冲突按「键 → 账号 id 集合」分组判定（>1 个不同 id 才冲突）；
  不导入/不调用 ``custom_backend.auth``（其 ``_connect`` 有建库/DDL 写路径）。
- **身份/名册**：``task_collaboration.read_team_role_state_on``（S1c 纯新增）
  给出严格三分类与完整 participants（owner＋全部 grant 者，不过滤）；
  ``team_latched`` 从 owner catalog 连接现读、由本模块合成 team 判定
  （``task_kind``）。属主当前账号名与协作库 owner 快照名 slug 不同 →
  ``identity_unconfirmed``（不改读新空库）。
- **票**：``team_vote_ledger.vote_snapshot``（S1a format2：结构＋守恒＋票面
  ＋library_id＋revision 同一 SQLite 快照；fail-closed 不假 rev1、不当缺票；
  VoteLedgerUnavailable 原语义透传）。missing_vote 仅 fresh＋required 成员
  票库＋open 前 stat FileNotFoundError。
- **时间**：``updated_at`` 真值优先（空/缺才 ``screened_at``）；非空非法不
  回退旧时间 → ``decided_at=None``；naive 按读取部署的历史本地规则逐时刻
  转 aware UTC（DST 历史）；就绪待裁键存在 required 成员时间不可核 →
  ``vote_time_unverifiable``（不交出可立即重裁 key、不补当前时间）。
- **证明**：``screening_arbitration.list_decision_arbitration_attestations_on``
  （S1c seam；fresh/borrowed 统一走 scope 连接，path 版不重连）；
  ``ArbitrationRecord.record_id=attestation.id``、``created_at=attested_at``
  （非法 → ``attestation_time_invalid``）；task/stage/round 精确过滤（历史
  task 不 strip 重绑）；input_proof 按 key/str(user_id)/library_id/revision
  逐键裁剪＋loader_stale（B 变不废 A、不整记录过滤）；generic record_id
  独立保留（详情 URL 不混用）。
- **核心**：五状态不增；上游缺键 ``None``＋既有 ``upstream_*`` reason；
  ``ArbitrationAttestationStoreUnavailable``/``VoteLedgerUnavailable`` 不换壳。

P09（2.1.6 keyed views，合同 C07/C08）：
- ``team_stage_view``/``team_chain_view`` 追加 keyword-only ``item_keys``：
  ``None`` 保持旧全域路径（既有 fresh/history 消费者字节级回归）；给定键域
  时走按键装载——**完成事实优先**（scope 附着的 KeyedReadPlan 见证经
  ``team_terminal_facts.read_seal_on`` 重读完整 payload，核 seal_id/双
  SHA/时间一致后直接给 FINAL_*，不读票、不受全局 config 诊断影响）；无
  见证键才读当前名册与按键票（``team_vote_ledger.vote_snapshot_keys_on``，
  cache 键含 ``(uid, stage, 键域)``，不复用全域脏票也不污染全域 cache）；
  仅"全员正式二选一且分歧"的冲突键调用
  ``screening_arbitration.read_attestations_for_keys_on``（C06 按键窄读）。
  每请求键恰落入 ``statuses`` 或 ``issues``（不交并集=keys）：非 canonical
  （duplicate/title-pending/不在 owner articles 域）＝``key_not_in_domain``；
  plan 发现 issue 原样透传；holder 见证保留后重读失败＝
  ``witness_unreadable``/身份漂移＝``witness_drift``；成员票库源级坏＝
  ``member_vote_unreadable``（A 键不连坐）；按键票行 row_missing/unreadable
  ＝``vote_row_missing``/``vote_row_unreadable``；就绪待裁键时间不可核＝
  ``vote_time_unverifiable``（按键路径逐键 issue，旧全域路径仍任务级
  typed raise）。身份/连接编程错误仍 typed 上抛，不伪装逐键待办。
- 视图 DTO 追加默认值字段 ``issues``/``completion``（chain 同）
  /``completion_required_uids``（C08）：completion——F1 见证＝persisted=True；
  当前一致（live consensus/已适用仲裁）＝persisted=False、seal_id/sealed_at
  为 None（不推造时间）。``applied`` 仍只含真正仲裁的
  ``(attestation_id, generic_record_id)``；unanimous 不造仲裁 ID。
- 受保护只读 scope（``custom_backend.team_keyed_reads.
  protected_keyed_read_scope``）以既有 canonical 锁＋collab IMMEDIATE 屏障
  构造 fresh 形态 scope（预置 collab 屏障连接＝``TeamReadScope.__init__``
  的 ``collab_conn``）并附着 plan/见证连接；本模块只消费附着物，屏障的
  打开/rollback/close 归该 manager。

对外 DTO（TeamStageView/TeamChainView）不含 scope/conn/accounts/votes/
input_proof（asdict 防泄露）。本模块不 import custom_backend.* 的既有约束
自 P09 起有一处精确例外：``custom_backend.team_keyed_domain``（P00 按键
owner 域读取，仅依赖 ``coscreen.team_flow_types``，零 data 触达、无环）；
其余 custom_backend 模块仍不 import（无环、无默认 data 触达）。
"""

from __future__ import annotations

import sqlite3
import stat as stat_mod
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Mapping, NamedTuple

from coscreen import task_collaboration as collaboration_mod
from coscreen import tasks as tasks_mod
from coscreen.security import sanitize_component
from coscreen.screening_arbitration import (
    ArbitrationAttestationStoreUnavailable,
    ArbitrationConnectionContractError,
    list_decision_arbitration_attestations_on,
    read_attestations_for_keys_on,
)
from coscreen.team_flow_types import (
    CompletionProjection,
    KeyIssue,
    KeyedReadPlan,
    SealWitness,
    normalize_item_keys,
)
from coscreen.team_screening_eligibility import (
    AWAITING_ARBITRATION,
    ArbitrationRecord,
    ChainItem,
    ConfigurationStatus,
    FINAL_EXCLUDE,
    FINAL_INCLUDE,
    ItemStatus,
    Participant,
    StageInputs,
    Vote,
    configuration_status,
    evaluate_chain,
    evaluate_stage,
)
from coscreen.team_terminal_facts import (
    TerminalFactsUnavailable,
    read_seal_on,
)
from coscreen.team_vote_ledger import (
    VoteLedgerUnavailable,
    vote_snapshot,
    vote_snapshot_keys_on,
)
from custom_backend.team_keyed_domain import (
    OwnerKeyedDomainUnavailableError,
    canonical_item_rows_on,
)

__all__ = [
    "TeamFlowUnavailableError",
    "TeamReadScope",
    "TeamStageView",
    "TeamChainView",
    "fresh_team_read_scope",
    "borrowed_team_read_scope",
    "task_kind",
    "team_stage_view",
    "team_chain_view",
    "keyed_progress_queue",
    "stage_final_keys",
    "chain_cleared_keys",
    "arbitration_ready_keys",
    "team_item_gate_reason",
]

_STAGES = ("stage1", "stage2")
_CHAIN_TARGETS = ("stage2", "coding", "appraisal")
_VOTE_DECISIONS = frozenset({"include", "exclude", "maybe"})
_SANITIZE_FALLBACK = "_"
_RESERVED_DB_CASEFOLD = "collaboration.db".casefold()
_RO_TIMEOUT = 15.0


class TeamFlowUnavailableError(RuntimeError):
    """TSR 结构化 fail-closed 错误（HTTP 层按 ``status``/``code`` 映射 503）。

    ``reason`` 取值见 :data:`REASONS`；``member``＝成员用户名、``key``＝
    zotero_key、``participant``＝成员 user_id（按语义选填，None 键不进
    ``params``）。
    """

    status = 503
    code = "team.flow.unavailable"
    message_key = "team.flow.unavailable"
    REASONS = (
        "collab_unreadable",        # 协作库打开/读取失败（非 legacy 缺文件）
        "roster_unreadable",        # 已确认 team 后名册不可读（保留令牌）
        "owner_db_unreadable",      # owner catalog 打开失败/articles 缺表/title 表不配对
        "accounts_unreadable",      # accounts 库缺失/无 users 表/读失败（不伪空）
        "member_unresolved",        # 参与者账号行缺失（保成员、不除名）
        "storage_conflict",         # 全账户 casefold 冲突 / collaboration.db 保留名
        "vote_domain_violation",    # 票 decision 域外脏值 / 核心输入形状拒绝
        "member_db_unreadable",     # 成员库存在但 stat/打开失败（非 FileNotFoundError）
        "vote_time_unverifiable",   # 就绪待裁键的 required 成员票时间不可核验
        "identity_unconfirmed",     # 属主当前账号名与 owner 快照名映射不同路径
        "unconfirmed_task_state",   # 协作库分类 unconfirmed / 非 owner_match 入口
        "attestation_time_invalid", # 命中 attestation 的 attested_at 非法
        "connection_contract",      # borrowed 连接映射违约（编程错误）
        "completion_source_unresolved",  # P09/C02：完成来源（F2 发现/F1 计划）未决
        "arbitration_source_unresolved", # P09/C02：仲裁证据来源未决（预留词表）
    )

    def __init__(self, reason: str, *, detail: str = "",
                 member: str | None = None, key: str | None = None,
                 participant: int | None = None) -> None:
        if reason not in self.REASONS:
            raise ValueError(
                f"未知 reason: {reason!r}，必须为 {self.REASONS} 之一")
        super().__init__(f"[{reason}] {detail}" if detail else f"[{reason}]")
        self.reason = reason
        self.detail = detail
        self.member = member
        self.key = key
        self.participant = participant

    @property
    def params(self) -> dict[str, object]:
        payload: dict[str, object] = {"reason": self.reason}
        if self.member is not None:
            payload["member"] = self.member
        if self.key is not None:
            payload["key"] = self.key
        if self.participant is not None:
            payload["participant"] = self.participant
        return payload


# ---------------------------------------------------------------------------
# DTO（对外；无 scope/conn/accounts/votes/input_proof）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TeamStageView:
    """一个阶段的逐条资格投影（票面永不进入）。"""

    stage: str
    task_id: str
    round_id: None
    config: ConfigurationStatus
    statuses: Mapping[str, ItemStatus]
    #: (attestation_id, key, 'vote_revision_changed'|'vote_proof_missing')
    loader_stale: tuple[tuple[int, str, str], ...]
    keys: tuple[str, ...]
    #: key -> (attestation_id, generic record_id)（双 ID 分列；generic 详情
    #: URL 只用 record_id，核心追加序只用 attestation_id）
    applied: Mapping[str, tuple[int, int]]
    #: P09（C08）：请求键 → 读取/发现期问题；与 ``statuses`` 不交、并集=
    #: ``keys``（None 全域路径恒为空）。reason 词表见模块头 P09 节。
    issues: Mapping[str, KeyIssue] = field(default_factory=dict)
    #: P09（C08）：键 → 完成投影。F1 见证＝persisted=True；当前一致（live
    #: consensus/已适用仲裁）＝persisted=False（seal_id/sealed_at=None）。
    completion: Mapping[str, CompletionProjection] = field(default_factory=dict)
    #: P09（C08）：本视图完成事实覆盖的 required uid 升序并集（无完成=()）。
    completion_required_uids: tuple[int, ...] = ()


@dataclass(frozen=True)
class TeamChainView:
    """跨阶段链投影（同一 scope 内一次派生两阶段，快照不分裂）。"""

    target: str
    chain: Mapping[str, ChainItem]
    stage1: TeamStageView
    stage2: TeamStageView
    #: P09（C08）：请求键中链结果不可定键 → 上游（stage1 优先，非 stage2
    #: 目标再看 stage2）的第一 issue；与 ``chain`` 不交、并集=请求键域。
    issues: Mapping[str, KeyIssue] = field(default_factory=dict)
    #: P09（C08）：键 → 真正决定该键链结果的阶段完成投影（决定阶段无完成
    #: 事实则不在映射）。
    completion: Mapping[str, CompletionProjection] = field(default_factory=dict)


class _KeyedMemberVotes(NamedTuple):
    """P09 内部：一名 required 成员对一个键域的按键票窄读结果。

    ``absent=True``＝missing_vote（fresh stat FileNotFoundError／borrowed
    missing_members）；``rows`` 仅含请求键；``issues``＝P04
    ``KeyedVoteIssue.reason``（row_missing/unreadable）逐键透传表。
    """

    absent: bool
    library_id: str | None
    rows: Mapping[str, Mapping[str, object]]
    issues: Mapping[str, str]


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------

def _aware_utc(raw: object) -> datetime | None:
    """库内时间字符串 → aware UTC；naive 按该时刻本地规则（DST 历史正确）。

    缺失/空/非文本/不可解析 → None（不伪时间、不兜底当前时间）。
    """
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.astimezone(timezone.utc)
    return parsed.astimezone(timezone.utc)


def _ro_connect(path: Path) -> sqlite3.Connection:
    """fresh 专用：mode=ro 打开现存库（绝不创建；失败由调用方映射 typed 错误）。

    P09（C07）：自开连接一律 ``sqlite3.Row`` 原生工厂——满足 P04/P05/P02
    ``*_on`` 系列借用读的 Row 合同（positional 访问双兼容，既有读取不变）。
    """
    uri = f"{path.resolve().as_uri()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=_RO_TIMEOUT)
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# TeamReadScope（内部能力对象；构造只经 fresh/borrowed_team_read_scope）
# ---------------------------------------------------------------------------

class TeamReadScope:
    """持有规范 task，路径→连接映射与从这些连接现读的身份/名册/键域/快照。

    fresh：accounts/collab 在进入时打开并 BEGIN DEFERRED；owner/成员连接按
    需懒开（同物理库去重，resolve 后键控），全部持有至退出（两阶段视图同
    快照）；退出逆序 rollback 自有读事务并 close 自开连接。borrowed：只读
    所给连接（零 FS：不 stat/resolve/open；缺连接且不在 missing_members＝
    ``connection_contract``）。本类不序列化、不进对外 DTO。

    P09：fresh 形态可经 ``collab_conn`` 预置受保护只读 scope 的 collab 屏障
    连接（调用方持有并负责 rollback/close；本 scope 不 stat/不重复打开/
    ``close_fresh`` 不关闭它）；``attach_keyed_plan`` 附着的 KeyedReadPlan
    与 holder 见证连接仅供按键视图消费。
    """

    def __init__(self, *, info, task_id: str, accounts_path: Path,
                 collab_path: Path, owner_path: Path | None,
                 member_paths: Mapping[int, Path],
                 connections: Mapping[Path, sqlite3.Connection],
                 missing_members: frozenset[int], fresh: bool,
                 collab_conn: sqlite3.Connection | None = None) -> None:
        if collab_conn is not None and not fresh:
            raise ValueError(
                "collab_conn 仅用于 fresh 形态（borrowed 经 connections 提供"
                "协作库连接）")
        self._info = info
        self.task_id = task_id
        self._accounts_path = accounts_path
        self._collab_path = collab_path
        self._owner_anchor_path = owner_path        # fresh：由 owner 快照名锚定
        self._member_paths = dict(member_paths)
        self._given_connections = dict(connections)  # borrowed：Path → conn
        self._missing_members = frozenset(missing_members)
        self._fresh = fresh
        self._owned: list[sqlite3.Connection] = []
        self._owned_by_key: dict[Path, sqlite3.Connection] = {}
        # FIX-R2 R2-1/R2-2：物理身份 (st_dev, st_ino) → **用途元组** claim：
        # ("collab",)＝协作库连接、("account", uid)＝owner catalog/成员票库。
        # 已登记元组 ≠ 新元组 → storage_conflict（member↔collab 别名、跨
        # 成员、跨 uid 全覆盖；同 owner uid 的 catalog＋本人票＝同元组合法
        # 去重）。st_ino==0（物理身份不可验证）对任务域库一律 fail-closed，
        # 不退路径去重（受控模拟已证可同票两计）；accounts 非票源例外。
        self._phys_claim: dict[tuple[int, int], tuple] = {}
        self._owned_by_ident: dict[tuple[int, int], sqlite3.Connection] = {}
        # P09：预置 collab 屏障连接（fresh 专用；调用方持有，close_fresh
        # 不关闭、不加入 _owned/_owned_by_ident——屏障路径不走 _own_conn）。
        self._collab_conn_owned = collab_conn
        self._role_state: dict | None = None
        self._accounts: tuple[dict[int, str], dict[str, set[int]]] | None = None
        self._latched: bool | None = None
        self._keys: tuple[str, ...] | None = None
        self._vote_cache: dict[tuple[int, str], object] = {}
        # P09 按键附件（零默认触达；由 attach_keyed_plan 一次性附着）。
        self._keyed_plan: KeyedReadPlan | None = None
        self._witness_readers: dict[Path, sqlite3.Connection] = {}
        self._unreadable_witness_paths: frozenset[Path] = frozenset()
        # P09 按键票 cache：键域含 (uid, stage, keys)——与全域 _vote_cache
        # 互不混用（全域快照已缓存时只读滤波复用，不反向污染）。
        self._keyed_vote_cache: dict[
            tuple[int, str, tuple[str, ...]], _KeyedMemberVotes] = {}

    # -- 连接解析 -----------------------------------------------------------

    def _own_conn(self, path: Path, *, reason: str, member: str | None = None,
                  claim: tuple | None = None,
                  task_scoped: bool = True) -> sqlite3.Connection:
        """fresh：懒开自有 ro 连接（开即 DEFERRED）。

        - F3 containment——``task_scoped=True``（任务目录派生路径）打开前做
          safe_path 同义真实路径校验（resolve 后须仍在 ``info.dir`` 内，否则
          按目标 reason typed 拒绝）；accounts 为宿主显式绝对路径，传
          ``task_scoped=False`` 豁免。
        - R2-1 物理用途 claim——stat 取 ``(st_dev, st_ino)`` 为键、
          ``claim`` 为用途元组（``("collab",)`` 或 ``("account", uid)``）：
          已登记用途 ≠ 新用途 → ``storage_conflict``（协作库经硬链接被成员
          冒用、跨账户共库均覆盖；同 owner uid 的 catalog＋本人票同元组合法
          去重）。
        - R2-2 身份不可验证 fail-closed——任务域库（task_scoped=True）
          ``st_ino==0`` 时**不退路径去重**（受控模拟已证可同票两计），按目标
          reason typed 拒绝；accounts（task_scoped=False、非票源）例外。
        """
        try:
            resolved = path.resolve()
        except OSError as exc:
            raise TeamFlowUnavailableError(
                reason, member=member, detail=f"路径不可解析: {path}") from exc
        if task_scoped:
            try:
                base = self._info.dir.resolve()
            except OSError as exc:
                raise TeamFlowUnavailableError(
                    reason, member=member,
                    detail=f"任务目录不可解析: {self._info.dir}") from exc
            if resolved != base and base not in resolved.parents:
                raise TeamFlowUnavailableError(
                    reason, member=member,
                    detail=f"路径逃逸任务目录（真实路径越界）: {path.name}")
        try:
            entry = resolved.stat()
        except OSError as exc:
            raise TeamFlowUnavailableError(
                reason, member=member,
                detail=f"stat 失败: {path.name}: {exc}") from exc
        ident: tuple[int, int] | None = None
        if entry.st_ino:
            ident = (entry.st_dev, entry.st_ino)
        elif task_scoped:
            # R2-2：物理身份不可验证——fail-closed（仅 accounts 例外）。
            raise TeamFlowUnavailableError(
                reason, member=member,
                detail=f"物理身份不可验证(st_ino=0)，拒绝按路径信任: {path.name}")
        if ident is not None and claim is not None:
            claimed = self._phys_claim.get(ident)
            if claimed is not None and claimed != claim:
                raise TeamFlowUnavailableError(
                    "storage_conflict", member=member,
                    detail=f"不同用途({claimed!r}/{claim!r})映射同一物理库"
                           f" (dev={ident[0]},ino={ident[1]})")
            self._phys_claim[ident] = claim
        conn = self._owned_by_ident.get(ident) if ident is not None else None
        if conn is None:
            conn = self._owned_by_key.get(resolved)
        if conn is not None:
            return conn
        try:
            conn = _ro_connect(resolved)
            conn.execute("BEGIN DEFERRED")
        except (sqlite3.Error, OSError) as exc:
            raise TeamFlowUnavailableError(
                reason, member=member, detail=f"只读打开失败: {path.name}: {exc}") from exc
        self._owned.append(conn)
        self._owned_by_key[resolved] = conn
        if ident is not None:
            self._owned_by_ident[ident] = conn
        return conn

    def collab_connection(self) -> sqlite3.Connection:
        if self._fresh:
            # fresh：role_state() 打开并持有的自有连接（同 scope 单快照）。
            if self._collab_conn_owned is None:
                self.role_state()   # 打开（或确认 legacy 缺库）并缓存连接/状态
            if self._collab_conn_owned is None:
                raise TeamFlowUnavailableError(
                    "collab_unreadable", detail="协作库连接不可用")
            return self._collab_conn_owned
        conn = self._given_connections.get(self._collab_path)
        if conn is None:
            raise TeamFlowUnavailableError(
                "connection_contract", detail="协作库连接缺失（borrowed 映射不完备）")
        return conn

    def _accounts_conn(self) -> sqlite3.Connection:
        if self._fresh:
            # accounts 为宿主显式绝对路径：不做任务目录 containment（F3 豁免）。
            return self._own_conn(self._accounts_path, reason="accounts_unreadable",
                                  task_scoped=False)
        conn = self._given_connections.get(self._accounts_path)
        if conn is None:
            raise TeamFlowUnavailableError(
                "connection_contract", detail="accounts 连接缺失（borrowed 映射不完备）")
        return conn

    def _owner_conn_or_none(self) -> sqlite3.Connection:
        """owner catalog 连接（FIX-R1 F4b：fresh 下 stat 分类——缺失/非普通
        文件/IO 一律 ``owner_db_unreadable``，**不再合成 latched=False**；
        归属 uid＝owner_user_id 供 F1 物理去重：owner catalog 与本人票共库
        （同 uid）合法复用）。"""
        if not self._fresh:
            conn = self._given_connections.get(self._owner_anchor_path) \
                if self._owner_anchor_path is not None else None
            if conn is None:
                raise TeamFlowUnavailableError(
                    "connection_contract", detail="owner catalog 连接缺失（borrowed 映射不完备）")
            return conn
        if self._owner_anchor_path is None:
            # fresh：按协作库 owner 快照名懒定锚（与 readonly_context 同口径）。
            self._owner_anchor_path = self.owner_anchor_path()
        try:
            entry = self._owner_anchor_path.stat()
        except FileNotFoundError as exc:
            raise TeamFlowUnavailableError(
                "owner_db_unreadable",
                detail="owner catalog 缺失，latched 不可确证（不合成 False）") from exc
        except OSError as exc:
            raise TeamFlowUnavailableError(
                "owner_db_unreadable", detail=f"owner catalog stat 失败: {exc}") from exc
        if not stat_mod.S_ISREG(entry.st_mode):
            raise TeamFlowUnavailableError(
                "owner_db_unreadable", detail="owner catalog 非普通文件")
        owner_uid = (self.role_state().get("owner") or {}).get("owner_user_id")
        return self._own_conn(self._owner_anchor_path, reason="owner_db_unreadable",
                              claim=("account", owner_uid))

    # -- 现读事实（scope 内缓存；不跨 scope） --------------------------------

    def role_state(self) -> dict:
        if self._role_state is None:
            if self._fresh:
                if self._collab_conn_owned is not None:
                    # P09：受保护只读 scope 预置的 collab 屏障连接——直接在
                    # 屏障上读角色事实（不 stat、不重复打开、退出不关闭）。
                    conn = self._collab_conn_owned
                else:
                    # FIX-R1 F4a：打开前显式 stat 分类——仅 FileNotFoundError 是
                    # "确证未初始化"（legacy）；目录/非普通文件/权限/IO 一律
                    # collab_unreadable（不降 legacy/personal）。
                    try:
                        entry = self._collab_path.stat()
                    except FileNotFoundError:
                        self._role_state = {
                            "classification": "legacy_uninitialized", "owner": None,
                            "has_non_owner_grants": False, "participants": []}
                        return self._role_state
                    except OSError as exc:
                        raise TeamFlowUnavailableError(
                            "collab_unreadable",
                            detail=f"协作库 stat 失败: {exc}") from exc
                    if not stat_mod.S_ISREG(entry.st_mode):
                        raise TeamFlowUnavailableError(
                            "collab_unreadable", detail="协作库路径非普通文件")
                    conn = self._own_conn(self._collab_path, reason="collab_unreadable",
                                          claim=("collab",))
                    self._collab_conn_owned = conn
            else:
                conn = self.collab_connection()
            try:
                self._role_state = collaboration_mod.read_team_role_state_on(
                    conn, task_id=self.task_id)
            except sqlite3.Error as exc:
                # FIX-R1 F5：结构损坏已在 reader 内转 unconfirmed；此处只兜
                # 瞬时读故障（busy/locked/IO）→ typed collab_unreadable。
                raise TeamFlowUnavailableError(
                    "collab_unreadable", detail=f"协作库角色读取失败: {exc}") from exc
        return self._role_state

    def owner_anchor_path(self) -> Path:
        """owner catalog 路径（协作库 owner 快照名锚定，与 readonly_context 同口径）。"""
        state = self.role_state()
        owner = state.get("owner") or {}
        username = owner.get("owner_username")
        if not isinstance(username, str) or not username:
            raise TeamFlowUnavailableError(
                "roster_unreadable", detail="owner 快照名缺失，无法锚定 catalog")
        return self._info.dir / (tasks_mod.slugify_screener(username) + ".db")

    def accounts(self) -> tuple[dict[int, str], dict[str, set[int]]]:
        """(user_id→username, casefold键→账号id集合)；同一 scope 只读一次。"""
        if self._accounts is None:
            conn = self._accounts_conn()
            try:
                has_users = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='users'"
                ).fetchone()
                if has_users is None:
                    raise TeamFlowUnavailableError(
                        "accounts_unreadable", detail="accounts 库缺 users 表（不伪空）")
                rows = conn.execute("SELECT id, username FROM users").fetchall()
            except sqlite3.Error as exc:
                raise TeamFlowUnavailableError(
                    "accounts_unreadable", detail=f"accounts 读取失败: {exc}") from exc
            by_id: dict[int, str] = {}
            by_casefold: dict[str, set[int]] = defaultdict(set)
            for row in rows:
                try:
                    uid = int(row[0])
                except (TypeError, ValueError):
                    continue  # 坏行不参与冲突判定（与业务无关的账户库脏行）
                username = row[1]
                if not isinstance(username, str) or not username:
                    continue
                by_id[uid] = username
                by_casefold[
                    sanitize_component(username, _SANITIZE_FALLBACK).casefold()].add(uid)
            self._accounts = (by_id, dict(by_casefold))
        return self._accounts

    def owner_latched(self) -> bool:
        if self._latched is None:
            conn = self._owner_conn_or_none()
            try:
                has_state = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' "
                    "AND name='workflow_state'").fetchone()
                latched = False
                if has_state is not None:
                    row = conn.execute(
                        "SELECT team_latched FROM workflow_state WHERE singleton=1"
                    ).fetchone()
                    # FIX-R2 R2-4：表在而 singleton 行缺＝损坏（不可确证未
                    # 锁），typed 拒绝；表缺＝确证旧版未引入（兼容 False）。
                    if row is None:
                        raise TeamFlowUnavailableError(
                            "owner_db_unreadable",
                            detail="workflow_state 存在但缺 singleton 行，"
                                   "latched 不可确证")
                    if row[0] is not None:
                        # FIX-R1 F4c：严格解码——只接受 SQLite INTEGER 0/1；
                        # 脏值（TEXT/浮点/越界 int 等）一律来源错误，不靠
                        # bool/int 真值吞脏状态。NULL＝未置位（既有语义）。
                        raw = row[0]
                        if isinstance(raw, bool) or not isinstance(raw, int) \
                                or raw not in (0, 1):
                            raise TeamFlowUnavailableError(
                                "owner_db_unreadable",
                                detail=f"team_latched 值非法: {raw!r}")
                        latched = raw == 1
                self._latched = latched
            except TeamFlowUnavailableError:
                raise
            except (TypeError, ValueError) as exc:
                raise TeamFlowUnavailableError(
                    "owner_db_unreadable", detail="team_latched 值非法") from exc
            except sqlite3.Error as exc:
                raise TeamFlowUnavailableError(
                    "owner_db_unreadable", detail=f"team_latched 读取失败: {exc}") from exc
        return self._latched

    def canonical_keys(self) -> tuple[str, ...]:
        """owner catalog 文章域：import_order,zotero_key 序、去 duplicate、
        排 title-pending（与 review_workflow eligible 同义；不叠加个人链）。"""
        if self._keys is None:
            conn = self._owner_conn_or_none()
            try:
                has_articles = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='articles'"
                ).fetchone()
                if has_articles is None:
                    raise TeamFlowUnavailableError(
                        "owner_db_unreadable", detail="articles 表缺失（不伪零文章）")
                rows = conn.execute(
                    "SELECT zotero_key FROM articles WHERE is_duplicate_of IS NULL "
                    "ORDER BY import_order, zotero_key").fetchall()
                keys = [row[0] for row in rows
                        if isinstance(row[0], str) and row[0]]
                has_members = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' "
                    "AND name='title_duplicate_members'").fetchone()
                if has_members is not None:
                    has_reviews = conn.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='table' "
                        "AND name='title_duplicate_reviews'").fetchone()
                    if has_reviews is None:
                        raise TeamFlowUnavailableError(
                            "owner_db_unreadable",
                            detail="title_duplicate_members 存在而 reviews 表缺失")
                    blocked = {row[0] for row in conn.execute(
                        "SELECT m.zotero_key FROM title_duplicate_members m "
                        "JOIN title_duplicate_reviews r ON r.review_id=m.review_id "
                        "WHERE m.imported_in_batch=1 "
                        "AND r.status IN ('pending','undecided')")}
                    keys = [key for key in keys if key not in blocked]
            except sqlite3.Error as exc:
                raise TeamFlowUnavailableError(
                    "owner_db_unreadable", detail=f"canonical 键域读取失败: {exc}") from exc
            self._keys = tuple(keys)
        return self._keys

    def _vote_snapshot_typed(self, conn: sqlite3.Connection, stage: str,
                             username: str):
        """装载来源错误契约（TESTFIX-R2 P-1）：真实 ledger 读取窄捕**预期**
        ``sqlite3.DatabaseError``（坏字节文件等——``BEGIN DEFERRED`` 不触盘，
        ledger 首个真实读才爆）→ ``member_db_unreadable`` typed；
        ``ProgrammingError``（调用写法缺陷）先原样 raise；
        ``VoteLedgerUnavailable``（RuntimeError 族）原样透传。零事务控制/
        零配置改动/零 fallback/零建库——单一映射，不改 S1a ledger。"""
        try:
            return vote_snapshot(conn, stage)
        except sqlite3.ProgrammingError:
            raise
        except sqlite3.DatabaseError as exc:
            raise TeamFlowUnavailableError(
                "member_db_unreadable", member=username,
                detail=f"成员库账本/票读取失败: {exc}") from exc

    def member_vote_snapshot(self, user_id: int, username: str, slug: str,
                             stage: str):
        """required 成员票快照：None＝missing_vote（仅 fresh＋stat
        FileNotFoundError／borrowed＋missing_members）；否则
        ``(library_id, {key: 行+revision})``（ledger 同快照；来源错误经
        :meth:`_vote_snapshot_typed` typed 映射）。"""
        cached = self._vote_cache.get((user_id, stage), False)
        if cached is not False:
            return cached
        snapshot: object
        if self._fresh:
            path = self._info.dir / f"{slug}.db"
            try:
                entry = path.stat()
            except FileNotFoundError:
                snapshot = None
            except OSError as exc:
                raise TeamFlowUnavailableError(
                    "member_db_unreadable", member=username,
                    detail=f"成员库 stat 失败: {slug}.db: {exc}") from exc
            else:
                if not stat_mod.S_ISREG(entry.st_mode):
                    raise TeamFlowUnavailableError(
                        "member_db_unreadable", member=username,
                        detail=f"成员库非普通文件: {slug}.db")
                # F3 containment＋R2-1 物理用途 claim＋R2-2 身份断言由 _own_conn
                # 统一执行。
                conn = self._own_conn(path, reason="member_db_unreadable",
                                      member=username, claim=("account", user_id))
                snapshot = self._vote_snapshot_typed(conn, stage, username)
        else:
            if user_id in self._missing_members:
                snapshot = None
            else:
                path = self._member_paths.get(user_id)
                conn = self._given_connections.get(path) if path is not None else None
                if conn is None:
                    raise TeamFlowUnavailableError(
                        "connection_contract", member=username,
                        detail="成员连接缺失且不在 missing_members（borrowed 映射不完备）")
                snapshot = self._vote_snapshot_typed(conn, stage, username)
        self._vote_cache[(user_id, stage)] = snapshot
        return snapshot

    # -- P09：按键读取附件 ---------------------------------------------------

    def attach_keyed_plan(self, plan: KeyedReadPlan,
                          witness_readers: Mapping[Path, sqlite3.Connection] | None = None,
                          unreadable_witness_paths: frozenset[Path] = frozenset()) -> None:
        """P09：附着受保护只读 scope 的按键读取计划与 holder 见证连接。

        一个 scope 恰附着一次（二次附着＝``connection_contract`` 编程错误）。
        ``witness_readers``＝{Path: conn}（调用方持有、退出自关；collab 持有
        见证仍经 :meth:`collab_connection` 读取）。``unreadable_witness_paths``
        ＝保留期打开失败的 holder 路径——对应键按 ``witness_unreadable``
        呈现，不遮蔽其他已见证键。
        """
        if self._keyed_plan is not None:
            raise TeamFlowUnavailableError(
                "connection_contract",
                detail="keyed plan 已附着（一个 scope 恰一个计划）")
        self._keyed_plan = plan
        self._witness_readers = dict(witness_readers or {})
        self._unreadable_witness_paths = frozenset(unreadable_witness_paths)

    def keyed_plan(self) -> KeyedReadPlan | None:
        """当前附着的 KeyedReadPlan；None＝无完成事实知识的按键视图。"""
        return self._keyed_plan

    def witness_connection(self, source_path: Path) -> sqlite3.Connection | None:
        """holder 见证的保留 ro 连接；未持有（含保留失败路径）返回 None。"""
        return self._witness_readers.get(source_path)

    def keyed_canonical_keys(self, item_keys: tuple[str, ...]) -> tuple[str, ...]:
        """P09：请求键中属 owner canonical 域的子集（保持请求序）。

        P00 ``canonical_item_rows_on`` 按键窄读（200 键/块、严格 schema：三表
        齐备、键域身份坏＝任务级 ``owner_db_unreadable`` typed；该严格性比旧
        全域 ``canonical_keys`` 的 title 表宽容更强，属 P00 冻结合同）。空
        键域短路返回 ``()``（链剪枝后 stage2 可为空；normalize 拒空集合）。
        """
        if not item_keys:
            return ()
        conn = self._owner_conn_or_none()
        try:
            rows = canonical_item_rows_on(conn, item_keys=item_keys)
        except sqlite3.ProgrammingError:
            raise
        except OwnerKeyedDomainUnavailableError as exc:
            raise TeamFlowUnavailableError(
                "owner_db_unreadable",
                detail=f"按键 canonical 键域读取失败: {exc}") from exc
        except RuntimeError as exc:
            # P09 复审 NOTE-2 防御性收口（P10 落地）：borrowed 连接非 Row 工厂
            # /无活动事务时 P00 连接门抛裸 RuntimeError——编程违约转 typed
            # ``connection_contract``（fresh 形态 ``_ro_connect`` 恒 Row＋BEGIN，
            # 本支不可达；OwnerKeyedDomainUnavailableError 已先捕获）。
            raise TeamFlowUnavailableError(
                "connection_contract",
                detail=f"按键 canonical 键域读取连接合同违约: {exc}") from exc
        return tuple(key for key in item_keys if key in rows)

    def member_vote_snapshot_keys(self, user_id: int, username: str, slug: str,
                                  stage: str,
                                  item_keys: tuple[str, ...]) -> _KeyedMemberVotes:
        """P09：required 成员对一个键域的按键票窄读（C04
        ``vote_snapshot_keys_on``：一次全局守恒校验＋仅请求主键读取）。

        cache 键含 ``(uid, stage, item_keys)``——同一键域/stage 复用，不同键域
        互不污染、绝不复用或写入全域 ``_vote_cache``（全域快照**已**缓存时可
        只读滤波复用：同一 SQLite 快照、已过全局守恒）。absent/来源错误语义
        与 :meth:`member_vote_snapshot` 同判据（missing_vote 不算坏；
        ProgrammingError 透传；``VoteLedgerUnavailable`` 原样上抛由按键视图
        折算逐键 issue，不连坐已见证键）。
        """
        keys = tuple(item_keys)
        cache_key = (user_id, stage, keys)
        cached = self._keyed_vote_cache.get(cache_key)
        if cached is not None:
            return cached
        full = self._vote_cache.get((user_id, stage), False)
        if full is not False:
            # 全域快照已在同一 scope 读过：同快照滤波复用（不触发全域读）。
            library_id, rows = full
            cached = _KeyedMemberVotes(
                absent=False, library_id=library_id,
                rows={key: rows[key] for key in keys if key in rows},
                issues={})
            self._keyed_vote_cache[cache_key] = cached
            return cached
        if self._fresh:
            path = self._info.dir / f"{slug}.db"
            try:
                entry = path.stat()
            except FileNotFoundError:
                cached = _KeyedMemberVotes(
                    absent=True, library_id=None, rows={}, issues={})
                self._keyed_vote_cache[cache_key] = cached
                return cached
            except OSError as exc:
                raise TeamFlowUnavailableError(
                    "member_db_unreadable", member=username,
                    detail=f"成员库 stat 失败: {slug}.db: {exc}") from exc
            if not stat_mod.S_ISREG(entry.st_mode):
                raise TeamFlowUnavailableError(
                    "member_db_unreadable", member=username,
                    detail=f"成员库非普通文件: {slug}.db")
            conn = self._own_conn(path, reason="member_db_unreadable",
                                  member=username, claim=("account", user_id))
        else:
            if user_id in self._missing_members:
                cached = _KeyedMemberVotes(
                    absent=True, library_id=None, rows={}, issues={})
                self._keyed_vote_cache[cache_key] = cached
                return cached
            path = self._member_paths.get(user_id)
            conn = self._given_connections.get(path) if path is not None else None
            if conn is None:
                raise TeamFlowUnavailableError(
                    "connection_contract", member=username,
                    detail="成员连接缺失且不在 missing_members（borrowed 映射不完备）")
        try:
            snap = vote_snapshot_keys_on(conn, stage, keys)
        except sqlite3.ProgrammingError:
            raise
        except VoteLedgerUnavailable:
            raise  # P04 typed 原语义透传（按键视图折算逐键 issue，不连坐）。
        except RuntimeError as exc:
            # P09 复审 NOTE-2 防御性收口（P10 落地）：borrowed 连接非 Row 工厂
            # /无活动事务时 P04 连接门抛裸 RuntimeError——编程违约转 typed
            # ``connection_contract``（fresh 形态 ``_ro_connect`` 恒 Row＋BEGIN，
            # 本支不可达；VoteLedgerUnavailable 已先捕获）。
            raise TeamFlowUnavailableError(
                "connection_contract", member=username,
                detail=f"成员库按键票读取连接合同违约: {exc}") from exc
        except sqlite3.DatabaseError as exc:
            raise TeamFlowUnavailableError(
                "member_db_unreadable", member=username,
                detail=f"成员库账本/票按键读取失败: {exc}") from exc
        cached = _KeyedMemberVotes(
            absent=False, library_id=snap.library_id, rows=snap.rows,
            issues={key: issue.reason for key, issue in snap.issues.items()})
        self._keyed_vote_cache[cache_key] = cached
        return cached

    def close_fresh(self) -> None:
        """fresh 退出：逆序 rollback 自有读事务并 close 自开连接。"""
        for conn in reversed(self._owned):
            try:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            finally:
                try:
                    conn.close()
                except sqlite3.Error:
                    pass
        self._owned.clear()
        self._owned_by_key.clear()


# ---------------------------------------------------------------------------
# scope 构造器
# ---------------------------------------------------------------------------

@contextmanager
def fresh_team_read_scope(info, *, accounts_db: Path) -> Iterator[TeamReadScope]:
    """fresh 只读作用域：accounts/collab/owner/成员连接按需懒开（mode=ro＋
    BEGIN DEFERRED，同物理库去重），scope 生命周期内两阶段视图同快照；退出
    只收尾自己（rollback 自有读事务＋close 自开连接）。

    ``info`` 要求且仅要求 ``task_id``（规范、strip 后非空）与 ``dir``（任务
    目录）；``accounts_db`` 为宿主显式传入的账号库路径（生产＝与 auth 同
    data_dir 的 users.db；本模块不默认 data、不从任务目录猜测）。
    """
    scope = _build_scope(info, accounts_db=accounts_db, collab_path=None,
                         owner_path=None, member_paths={}, connections={},
                         missing_members=frozenset(), fresh=True)
    try:
        yield scope
    finally:
        scope.close_fresh()


def borrowed_team_read_scope(info, *, accounts_path: Path, collab_path: Path,
                             owner_path: Path,
                             member_paths: Mapping[int, Path],
                             connections: Mapping[Path, sqlite3.Connection],
                             missing_members: frozenset[int]) -> TeamReadScope:
    """borrowed 只读作用域：只读 S2 已持的完整现存输入连接（每个已有 caller
    事务，可读未提交证明）；零 FS（不 stat/resolve/open）。

    完备性：``accounts_path/collab_path/owner_path`` 与每个 ``member_paths``
    值必须恰为 ``connections`` 的 Path 键（S2 已验证路径↔连接对应；缺连接且
    对应成员不在 ``missing_members`` → ``connection_contract`` 编程违约）。
    """
    return _build_scope(info, accounts_db=accounts_path, collab_path=collab_path,
                        owner_path=owner_path, member_paths=member_paths,
                        connections=connections,
                        missing_members=missing_members, fresh=False)


def _build_scope(info, *, accounts_db, collab_path, owner_path, member_paths,
                 connections, missing_members, fresh: bool,
                 collab_conn: sqlite3.Connection | None = None) -> TeamReadScope:
    task_id = getattr(info, "task_id", None)
    if not isinstance(task_id, str) or not task_id.strip():
        raise TeamFlowUnavailableError(
            "unconfirmed_task_state", detail="info.task_id 必须为规范非空文本")
    task_dir = getattr(info, "dir", None)
    if task_dir is None:
        raise TeamFlowUnavailableError(
            "unconfirmed_task_state", detail="info.dir 缺失（TaskInfo 不完整）")
    if not isinstance(accounts_db, Path):
        accounts_db = Path(accounts_db)
    resolved_collab = collab_path if collab_path is not None \
        else collaboration_mod.shared_store_path(task_dir)
    scope = TeamReadScope(
        info=info, task_id=task_id, accounts_path=accounts_db,
        collab_path=resolved_collab, owner_path=owner_path,
        member_paths=member_paths, connections=connections,
        missing_members=missing_members, fresh=fresh,
        collab_conn=collab_conn)
    if not fresh:
        # 借用前静态完备性（零 FS）：三主路径必须在 connections；成员路径
        # 在 connections 或其 uid ∈ missing_members。
        for label, path in (("accounts_path", accounts_db),
                            ("collab_path", resolved_collab),
                            ("owner_path", owner_path)):
            if path is None or path not in connections:
                raise TeamFlowUnavailableError(
                    "connection_contract", detail=f"{label} 连接缺失（borrowed 映射不完备）")
        for uid, path in member_paths.items():
            if path not in connections and uid not in missing_members:
                raise TeamFlowUnavailableError(
                    "connection_contract",
                    detail=f"成员 {uid} 路径连接缺失且不在 missing_members")
        for path, conn in connections.items():
            if not isinstance(conn, sqlite3.Connection):
                raise TeamFlowUnavailableError(
                    "connection_contract", detail=f"connections 值非 sqlite3.Connection: {path}")
            # FIX-R1 F2：借用连接必须已持调用方事务——任何读取之前拒绝无
            # 事务连接（否则后续读取会落入 reader 自开 DEFERRED，破坏借用
            # 协议与两阶段快照）。合法 caller 事务与配置保持不变。
            if not conn.in_transaction:
                raise TeamFlowUnavailableError(
                    "connection_contract",
                    detail=f"借用连接未持有调用方事务: {path}")
    return scope


# ---------------------------------------------------------------------------
# 分类
# ---------------------------------------------------------------------------

def task_kind(role_state: Mapping, *, owner_latched: bool) -> str:
    """严格三分类合成：legacy 仅当确证未初始化；unconfirmed 一律 typed 错误
    （不保旧 owner-None→legacy 漏洞）；owner_match → team iff
    owner_latched or has_non_owner_grants。"""
    classification = role_state.get("classification")
    if classification == "owner_match":
        if owner_latched or role_state.get("has_non_owner_grants"):
            return "team"
        return "personal"
    if classification == "legacy_uninitialized":
        return "legacy"
    raise TeamFlowUnavailableError(
        "unconfirmed_task_state",
        detail=f"协作库分类不可确认: {classification!r}")


# ---------------------------------------------------------------------------
# 阶段/链视图
# ---------------------------------------------------------------------------

def team_stage_view(scope: TeamReadScope, stage: str, *,
                    item_keys=None) -> TeamStageView:
    """单阶段逐条资格（fresh/borrowed 同一装载链；票面不进 DTO）。

    ``item_keys=None``（缺省）保持旧全域路径（回归不变）；给定键域时走
    :func:`_keyed_stage_view` 按键装载（完成事实优先；每请求键恰有 status
    或 issue——statuses 与 issues 不交、并集=keys）。
    """
    if stage not in _STAGES:
        raise ValueError(f"stage 必须为 {_STAGES} 之一: {stage!r}")
    if item_keys is not None:
        return _keyed_stage_view(scope, stage, normalize_item_keys(item_keys))
    state = scope.role_state()
    if state.get("classification") != "owner_match":
        # 入口即 fail-closed：legacy/unconfirmed 都不降级（ruling §3）。
        raise TeamFlowUnavailableError(
            "unconfirmed_task_state",
            detail=f"非 owner_match 任务不适用 team 资格视图: "
                   f"{state.get('classification')!r}")
    owner = state["owner"]
    accounts_by_id, casefold_ids = scope.accounts()

    participants_core: list[Participant] = []
    required_names: list[str] = []
    uid_of_name: dict[str, int] = {}
    slug_of_name: dict[str, str] = {}
    for entry in state["participants"]:
        uid = entry["user_id"]
        username = accounts_by_id.get(uid)
        if username is None:
            raise TeamFlowUnavailableError(
                "member_unresolved", participant=uid,
                detail="参与者账号行缺失（保成员，请修复账号后重试）")
        conflict_key = sanitize_component(username, _SANITIZE_FALLBACK).casefold()
        if len(casefold_ids.get(conflict_key, ())) > 1:
            raise TeamFlowUnavailableError(
                "storage_conflict", member=username,
                detail="全账户内存在同 casefold 库名冲突的不同账号")
        slug = tasks_mod.slugify_screener(username)
        if f"{slug}.db".casefold() == _RESERVED_DB_CASEFOLD:
            raise TeamFlowUnavailableError(
                "storage_conflict", member=username,
                detail="成员 slug 撞协作库保留名 collaboration.db")
        if entry["is_owner"]:
            snapshot_slug = tasks_mod.slugify_screener(owner["owner_username"])
            if slug != snapshot_slug:
                raise TeamFlowUnavailableError(
                    "identity_unconfirmed", member=username,
                    detail="属主当前账号名与协作库 owner 快照名映射到不同库路径")
        participants_core.append(Participant(
            name=username, role=entry["role"],
            dual_role=entry["conflict"], is_owner=entry["is_owner"]))
        if entry["required_screener"]:
            required_names.append(username)
            uid_of_name[username] = uid
            slug_of_name[username] = slug

    votes: dict[str, dict[str, Vote]] = {}
    library_of: dict[str, str] = {}
    revisions_of: dict[str, dict[str, int]] = {}
    for username in required_names:
        snapshot = scope.member_vote_snapshot(
            uid_of_name[username], username, slug_of_name[username], stage)
        if snapshot is None:
            votes[username] = {}
            continue
        library_id, rows = snapshot
        bucket: dict[str, Vote] = {}
        for key, row in rows.items():
            decision = row["decision"]
            if decision not in _VOTE_DECISIONS:
                raise TeamFlowUnavailableError(
                    "vote_domain_violation", member=username, key=key,
                    detail=f"票 decision 域外脏值: {decision!r}")
            bucket[key] = Vote(
                decision=decision,
                exclusion_reason=row["exclusion_reason"] or "",
                notes=row["notes"] or "",
                # updated_at 真值优先；非空非法不回退 screened_at（→None）。
                decided_at=_aware_utc(row["updated_at"] or row["screened_at"]))
        votes[username] = bucket
        library_of[username] = library_id
        revisions_of[username] = {
            key: int(row["revision"]) for key, row in rows.items()}

    keys = scope.canonical_keys()

    # 证明消费：fresh/borrowed 统一走 scope 的协作库连接（可读未提交行）。
    try:
        found = list_decision_arbitration_attestations_on(
            scope.collab_connection(), stage=stage, task_id=scope.task_id)
    except ArbitrationAttestationStoreUnavailable:
        raise  # S1b typed 错误原语义透传（不换壳、不伪空）。

    arbitrations: list[ArbitrationRecord] = []
    loader_stale: list[tuple[int, str, str]] = []
    generic_of_attestation: dict[int, int] = {}
    for combined in found:
        attestation = combined["attestation"]
        if attestation["task_id"] != scope.task_id:
            continue  # 精确 task 过滤（查询已滤，双保险；不 strip 重绑）
        if attestation["round_id"] is not None:
            continue  # 命名轮证明 vs 当前未分轮流＝无关记录（不入 stale）
        attested_at = _aware_utc(attestation["attested_at"])
        if attested_at is None:
            raise TeamFlowUnavailableError(
                "attestation_time_invalid",
                detail=f"attestation {attestation['id']} 的 attested_at 非法")
        rows_by_key: dict[str, dict] = {}
        for record_row in combined["record"]["records"]:
            if (isinstance(record_row, dict)
                    and isinstance(record_row.get("zotero_key"), str)):
                rows_by_key[record_row["zotero_key"]] = record_row
        proof = attestation["input_proof"] \
            if isinstance(attestation["input_proof"], dict) else {}
        kept: dict[str, dict] = {}
        for key in keys:
            row = rows_by_key.get(key)
            if row is None:
                continue
            per_user = proof.get(key)
            if not isinstance(per_user, dict) or not per_user:
                # FIX-R1 F8：证明行覆盖该键而整键 proof 缺失/空——逐键记
                # loader_stale（诊断冻结 DTO），A 键适用性不受影响。
                loader_stale.append(
                    (attestation["id"], key, "vote_proof_missing"))
                continue
            applicable = bool(required_names)
            if applicable:
                for username in required_names:
                    proof_entry = per_user.get(str(uid_of_name[username]))
                    if proof_entry is None:
                        applicable = False
                        loader_stale.append(
                            (attestation["id"], key, "vote_proof_missing"))
                        break
                    if (proof_entry.get("library_id") != library_of.get(username)
                            or proof_entry.get("revision")
                            != revisions_of[username].get(key)):
                        # B 键 revision/library 变化只裁本键；A 键不受影响。
                        applicable = False
                        loader_stale.append(
                            (attestation["id"], key, "vote_revision_changed"))
                        break
            if applicable:
                kept[key] = row
        if kept:
            arbitrations.append(ArbitrationRecord(
                record_id=attestation["id"], stage=stage,
                created_at=attested_at,
                arbitrator_id=str(attestation["arbitrator_user_id"]),
                source="attestation", task_id=attestation["task_id"],
                round_id=attestation["round_id"], rows=kept))
            generic_of_attestation[attestation["id"]] = combined["record_id"]

    inputs = StageInputs(
        stage=stage, participants=tuple(participants_core),
        required=tuple(required_names), votes=votes,
        arbitrations=tuple(arbitrations), task_id=scope.task_id,
        round_id=None, keys=keys)
    try:
        statuses = evaluate_stage(inputs)
        config = configuration_status(inputs)
    except ValueError as exc:
        raise TeamFlowUnavailableError(
            "vote_domain_violation", detail=f"核心输入校验拒绝: {exc}") from exc

    # 就绪待裁键的时间可核验性：required 成员 decided_at 不可核 → 不交出可
    # 立即重裁的 ready key（收敛路径＝成员正常保存使 updated_at/revision 真实
    # 前进；loader 不补当前时间；坏时间未变时重裁仍 stale）。
    for key, item in statuses.items():
        if item.status == AWAITING_ARBITRATION:
            for username in required_names:
                vote = votes[username].get(key)
                if vote is not None and vote.decided_at is None:
                    raise TeamFlowUnavailableError(
                        "vote_time_unverifiable", member=username, key=key,
                        detail="待裁键的成员票时间不可核验，需成员重新保存确认")

    applied: dict[str, tuple[int, int]] = {}
    for key, item in statuses.items():
        if item.applied_arbitration is not None:
            applied[key] = (item.applied_arbitration,
                            generic_of_attestation[item.applied_arbitration])

    return TeamStageView(
        stage=stage, task_id=scope.task_id, round_id=None, config=config,
        statuses=statuses, loader_stale=tuple(loader_stale), keys=keys,
        applied=applied)


def team_chain_view(scope: TeamReadScope, target: str, *,
                    item_keys=None) -> TeamChainView:
    """跨阶段链视图：同一 scope 一次派生两阶段（快照不分裂）。

    ``item_keys=None``（缺省）保持旧全域路径；给定键域时走
    :func:`_keyed_chain_view`（stage1 exclude 已知即剪枝 stage2 读取；每请求
    键恰有 chain 或 issue）。
    """
    if target not in _CHAIN_TARGETS:
        raise ValueError(f"target 必须为 {_CHAIN_TARGETS} 之一: {target!r}")
    if item_keys is not None:
        return _keyed_chain_view(scope, target, normalize_item_keys(item_keys))
    stage1 = team_stage_view(scope, "stage1")
    stage2 = team_stage_view(scope, "stage2")
    chain = evaluate_chain(stage1.statuses, stage2.statuses, target,
                           keys=list(stage1.keys))
    return TeamChainView(
        target=target, chain=chain, stage1=stage1, stage2=stage2)


# ---------------------------------------------------------------------------
# P09：按键装载（item_keys 给定时；完成事实 → 必要当前名单/票 → 仅冲突键仲裁）
# ---------------------------------------------------------------------------

def _keyed_roster(scope: TeamReadScope,
                  state: Mapping) -> tuple[tuple[Participant, ...], list[str],
                                           dict[str, int], dict[str, str]]:
    """按键路径的名册/身份解析（判据与旧全域路径逐字一致；独立成函数以保持
    旧函数体零改动——两侧漂移由合同测试锚定）。"""
    owner = state["owner"]
    accounts_by_id, casefold_ids = scope.accounts()

    participants_core: list[Participant] = []
    required_names: list[str] = []
    uid_of_name: dict[str, int] = {}
    slug_of_name: dict[str, str] = {}
    for entry in state["participants"]:
        uid = entry["user_id"]
        username = accounts_by_id.get(uid)
        if username is None:
            raise TeamFlowUnavailableError(
                "member_unresolved", participant=uid,
                detail="参与者账号行缺失（保成员，请修复账号后重试）")
        conflict_key = sanitize_component(username, _SANITIZE_FALLBACK).casefold()
        if len(casefold_ids.get(conflict_key, ())) > 1:
            raise TeamFlowUnavailableError(
                "storage_conflict", member=username,
                detail="全账户内存在同 casefold 库名冲突的不同账号")
        slug = tasks_mod.slugify_screener(username)
        if f"{slug}.db".casefold() == _RESERVED_DB_CASEFOLD:
            raise TeamFlowUnavailableError(
                "storage_conflict", member=username,
                detail="成员 slug 撞协作库保留名 collaboration.db")
        if entry["is_owner"]:
            snapshot_slug = tasks_mod.slugify_screener(owner["owner_username"])
            if slug != snapshot_slug:
                raise TeamFlowUnavailableError(
                    "identity_unconfirmed", member=username,
                    detail="属主当前账号名与协作库 owner 快照名映射到不同库路径")
        participants_core.append(Participant(
            name=username, role=entry["role"],
            dual_role=entry["conflict"], is_owner=entry["is_owner"]))
        if entry["required_screener"]:
            required_names.append(username)
            uid_of_name[username] = uid
            slug_of_name[username] = slug
    return tuple(participants_core), required_names, uid_of_name, slug_of_name


def _witness_payload(scope: TeamReadScope, witness: SealWitness,
                     task_instance_id: str,
                     stage: str, *,
                     reader_of=None) -> tuple[Mapping | None, KeyIssue | None]:
    """按 plan 见证重读 F1 完整记录并核身份四元组。

    返回 ``(payload, None)`` 或 ``(None, KeyIssue)``：holder 保留连接缺失/
    F1 重读失败（非 connection_contract）＝``witness_unreadable``（可重试）；
    行已不在或 seal_id/双 SHA/sealed_at 与 plan 见证不一致＝``witness_drift``
    （计划后漂移，重计划可收敛）。collab 持有见证经屏障连接读取。

    P24 收尾（C12 包 C）：``reader_of``（``Path -> conn | None``）允许调用方
    注入 holder reader overlay（写窗进度队列的短命 ro reader）；缺省沿用
    scope 附着的 ``witness_connection``（P09 语义零变化）。
    """
    if witness.source_path is None:
        conn = scope.collab_connection()
        source_role = "collab"
    else:
        conn = (reader_of(witness.source_path) if reader_of is not None
                else scope.witness_connection(witness.source_path))
        source_role = "actor"
        if conn is None:
            return None, KeyIssue(
                reason="witness_unreadable", stage=stage, key=witness.item_key,
                source_role=source_role, retryable=True)
    try:
        record = read_seal_on(
            conn, task_instance_id=task_instance_id,
            stage=stage, item_key=witness.item_key)
    except TerminalFactsUnavailable as exc:
        if exc.reason == "connection_contract":
            raise  # 编程错误（含借用合同违约）不伪装逐键待办。
        return None, KeyIssue(
            reason="witness_unreadable", stage=stage, key=witness.item_key,
            source_role=source_role, retryable=True)
    if record is None or (
            record["seal_id"] != witness.seal_id
            or record["content_sha256"] != witness.content_sha256
            or record["payload_sha256"] != witness.payload_sha256
            or record["sealed_at"] != witness.sealed_at):
        return None, KeyIssue(
            reason="witness_drift", stage=stage, key=witness.item_key,
            source_role=source_role, retryable=True)
    return record["payload"], None


def _keyed_arbitration_records(
    attested, conflict: tuple[str, ...], required_names: list[str],
    uid_of_name: dict[str, int], library_of: dict[str, str],
    revisions_of: dict[str, dict[str, int]], stage: str,
) -> tuple[list[ArbitrationRecord], dict[int, int],
           list[tuple[int, str, str]]]:
    """C06 按键证据 → 资格核心 ``ArbitrationRecord``（仅冲突键）。

    按 attestation id 分组、每记录只保留 proof 对当前票仍适用（library/
    revision 逐成员匹配）的键；不适用键沿旧全域路径同一对 loader_stale 令牌
    （vote_proof_missing/vote_revision_changed，B 变不废 A）。C06 自身的逐键
    损害 issue（孤儿/坏 JSON 等）不进 DTO：该键保持当前状态的
    ``awaiting_arbitration``（与旧路径 loader_stale 同处置）。
    """
    grouped: dict[int, dict] = {}
    stale: list[tuple[int, str, str]] = []
    for key in conflict:
        for entry in attested.by_key.get(key, ()):
            attestation = entry["attestation"]
            attested_at = _aware_utc(attestation["attested_at"])
            if attested_at is None:
                raise TeamFlowUnavailableError(
                    "attestation_time_invalid",
                    detail=f"attestation {attestation['id']} 的 attested_at 非法")
            row = None
            for record_row in entry["record"]["records"]:
                if (isinstance(record_row, dict)
                        and record_row.get("zotero_key") == key):
                    row = record_row
                    break
            if row is None:
                continue
            per_user = entry["proof"] if isinstance(entry["proof"], dict) else {}
            applicable = bool(required_names)
            if applicable:
                for username in required_names:
                    proof_entry = per_user.get(str(uid_of_name[username]))
                    if proof_entry is None:
                        applicable = False
                        stale.append(
                            (attestation["id"], key, "vote_proof_missing"))
                        break
                    if (proof_entry.get("library_id") != library_of.get(username)
                            or proof_entry.get("revision")
                            != revisions_of.get(username, {}).get(key)):
                        applicable = False
                        stale.append(
                            (attestation["id"], key, "vote_revision_changed"))
                        break
            if not applicable:
                continue
            group = grouped.get(attestation["id"])
            if group is None:
                group = {"attestation": attestation, "rows": {},
                         "generic_id": entry["record_id"]}
                grouped[attestation["id"]] = group
            group["rows"][key] = row
    records: list[ArbitrationRecord] = []
    generic_of: dict[int, int] = {}
    for attestation_id, group in grouped.items():
        records.append(ArbitrationRecord(
            record_id=attestation_id, stage=stage,
            created_at=_aware_utc(group["attestation"]["attested_at"]),
            arbitrator_id=str(group["attestation"]["arbitrator_user_id"]),
            source="attestation", task_id=group["attestation"]["task_id"],
            round_id=group["attestation"]["round_id"], rows=group["rows"]))
        generic_of[attestation_id] = group["generic_id"]
    return records, generic_of, stale


def _keyed_stage_view(scope: TeamReadScope, stage: str,
                      keys: tuple[str, ...]) -> TeamStageView:
    """按键单阶段视图（P09 核心）：完成事实 → 必要当前名单/票 → 仅冲突键仲裁。

    顺序合同：①canonical 键域（域外键＝issue，不静默丢）；②plan 见证重读
    F1（身份四元组核过即 FINAL_*，**不读票、不受 config 影响**——全局 config
    诊断不能推翻完成 A）；③无见证键读当前名册＋按键票（缺票库＝missing_vote
    待完成，不算坏）；④全员正式分歧的冲突键才走 C06 按键仲裁读取；⑤当前
    一致（consensus/已适用仲裁）补 persisted=False 完成投影（不造 seal/时间）。
    每请求键恰落入 statuses 或 issues（不交并集=keys）。
    """
    return _stage_view_on(scope, stage, keys,
                          plan=scope.keyed_plan(),
                          reader_of=scope.witness_connection)


def _stage_view_on(scope: TeamReadScope, stage: str,
                   keys: tuple[str, ...], *,
                   plan: KeyedReadPlan | None,
                   reader_of=None) -> TeamStageView:
    """_keyed_stage_view 的参数化核（P24 收尾，C12 包 C）：plan 与 holder
    reader 可注入——scope 附着 plan（P09 既有）或调用方的全域 overlay plan
    （写窗进度队列）；键域合同 ``set(keys) <= set(plan.item_keys)`` 与
    见证/issue/票/仲裁顺序两侧同守，评估语义零分叉。"""
    state = scope.role_state()
    if state.get("classification") != "owner_match":
        raise TeamFlowUnavailableError(
            "unconfirmed_task_state",
            detail=f"非 owner_match 任务不适用 team 资格视图: "
                   f"{state.get('classification')!r}")
    participants_core, required_names, uid_of_name, slug_of_name = \
        _keyed_roster(scope, state)

    statuses: dict[str, ItemStatus] = {}
    completion: dict[str, CompletionProjection] = {}
    applied: dict[str, tuple[int, int]] = {}
    issues: dict[str, KeyIssue] = {}
    loader_stale: list[tuple[int, str, str]] = []
    required_uids: set[int] = set()

    canonical_keys = scope.keyed_canonical_keys(keys)
    canonical_set = frozenset(canonical_keys)
    for key in keys:
        if key not in canonical_set:
            issues[key] = KeyIssue(
                reason="key_not_in_domain", stage=stage, key=key,
                source_role="owner", retryable=False)

    live: list[str] = []
    if plan is None:
        live = list(canonical_keys)
    else:
        if not set(keys) <= set(plan.item_keys):
            raise ValueError("item_keys 超出受保护 scope 的计划键域")
        instance = plan.binding.task_instance_id
        for key in canonical_keys:
            witness = plan.witnesses.get((stage, key))
            if witness is not None:
                payload, issue = _witness_payload(
                    scope, witness, instance, stage, reader_of=reader_of)
                if issue is not None:
                    issues[key] = issue
                    continue
                final = payload["final_decision"]
                kind = payload["kind"]
                arbitration = payload.get("arbitration")
                is_adjudicated = kind == "adjudicated" and arbitration is not None
                statuses[key] = ItemStatus(
                    key=key, stage=stage,
                    status=FINAL_INCLUDE if final == "include" else FINAL_EXCLUDE,
                    applied_arbitration=(
                        arbitration["attestation_id"] if is_adjudicated else None),
                    decided_by="arbitration" if is_adjudicated else "consensus")
                completion[key] = CompletionProjection(
                    seal_id=witness.seal_id, kind=kind,
                    trigger=payload["trigger"], sealed_at=witness.sealed_at,
                    final_decision=final, persisted=True)
                required_uids.update(payload["required_uids"])
                if is_adjudicated:
                    # applied 仅真正仲裁对；unanimous 不造仲裁 ID（C08）。
                    applied[key] = (arbitration["attestation_id"],
                                    arbitration["record_id"])
                continue
            plan_issue = plan.issues.get((stage, key))
            if plan_issue is not None:
                issues[key] = plan_issue
                continue
            live.append(key)

    votes: dict[str, dict[str, Vote]] = {}
    library_of: dict[str, str] = {}
    revisions_of: dict[str, dict[str, int]] = {}
    member_failed = False
    if live:
        for username in required_names:
            try:
                snap = scope.member_vote_snapshot_keys(
                    uid_of_name[username], username, slug_of_name[username],
                    stage, tuple(live))
            except sqlite3.ProgrammingError:
                raise
            except TeamFlowUnavailableError as exc:
                if exc.reason == "connection_contract":
                    raise
                member_failed = True
                continue
            except (VoteLedgerUnavailable, sqlite3.DatabaseError):
                member_failed = True
                continue
            if snap.absent:
                votes[username] = {}
                continue
            bucket: dict[str, Vote] = {}
            revs: dict[str, int] = {}
            for key, row in snap.rows.items():
                decision = row["decision"]
                if decision not in _VOTE_DECISIONS:
                    raise TeamFlowUnavailableError(
                        "vote_domain_violation", member=username, key=key,
                        detail=f"票 decision 域外脏值: {decision!r}")
                bucket[key] = Vote(
                    decision=decision,
                    exclusion_reason=row["exclusion_reason"] or "",
                    notes=row["notes"] or "",
                    decided_at=_aware_utc(row["updated_at"] or row["screened_at"]))
                revs[key] = int(row["revision"])
            votes[username] = bucket
            library_of[username] = snap.library_id
            revisions_of[username] = revs
            for key, reason in snap.issues.items():
                # P04 逐键损害：row_missing（有票缺 ledger 行）/unreadable
                # （存储类型/解码失败）——该键不可评，其余键不受连坐。
                issues.setdefault(key, KeyIssue(
                    reason="vote_row_missing" if reason == "row_missing"
                    else "vote_row_unreadable",
                    stage=stage, key=key, source_role="actor", retryable=False))
        if member_failed:
            for key in live:
                if key not in issues:
                    issues[key] = KeyIssue(
                        reason="member_vote_unreadable", stage=stage, key=key,
                        source_role="actor", retryable=True)

    remaining = [key for key in live if key not in issues]
    config: ConfigurationStatus | None = None
    if remaining:
        inputs = StageInputs(
            stage=stage, participants=participants_core,
            required=tuple(required_names), votes=votes,
            arbitrations=(), task_id=scope.task_id,
            round_id=None, keys=tuple(remaining))
        try:
            config = configuration_status(inputs)
            first = evaluate_stage(inputs)
        except ValueError as exc:
            raise TeamFlowUnavailableError(
                "vote_domain_violation", detail=f"核心输入校验拒绝: {exc}") from exc
        conflict = tuple(
            key for key in remaining
            if first[key].status == AWAITING_ARBITRATION)
        for key in remaining:
            if key not in conflict:
                statuses[key] = first[key]
        if conflict:
            try:
                attested = read_attestations_for_keys_on(
                    scope.collab_connection(), task_id=scope.task_id,
                    stage=stage, round_id=None, item_keys=conflict)
            except ArbitrationConnectionContractError as exc:
                raise TeamFlowUnavailableError(
                    "connection_contract",
                    detail=f"仲裁按键证据读取连接违约: {exc}") from exc
            except ArbitrationAttestationStoreUnavailable:
                raise  # S1b/P05 typed 错误原语义透传（不换壳、不伪空）。
            arbitrations, generic_of, stale = _keyed_arbitration_records(
                attested, conflict, required_names, uid_of_name,
                library_of, revisions_of, stage)
            loader_stale.extend(stale)
            inputs = StageInputs(
                stage=stage, participants=participants_core,
                required=tuple(required_names), votes=votes,
                arbitrations=tuple(arbitrations), task_id=scope.task_id,
                round_id=None, keys=conflict)
            try:
                second = evaluate_stage(inputs)
            except ValueError as exc:
                raise TeamFlowUnavailableError(
                    "vote_domain_violation", detail=f"核心输入校验拒绝: {exc}") from exc
            for key in conflict:
                item = second[key]
                statuses[key] = item
                if item.applied_arbitration is not None:
                    applied[key] = (item.applied_arbitration,
                                    generic_of[item.applied_arbitration])
        live_completion: dict[str, CompletionProjection] = {}
        for key in remaining:
            item = statuses[key]
            if item.status == FINAL_INCLUDE:
                final = "include"
            elif item.status == FINAL_EXCLUDE:
                final = "exclude"
            else:
                continue
            live_completion[key] = CompletionProjection(
                seal_id=None,
                kind="adjudicated" if item.decided_by == "arbitration"
                else "unanimous",
                trigger="arbitration" if item.decided_by == "arbitration"
                else "last_vote",
                sealed_at=None, final_decision=final, persisted=False)
        if live_completion:
            completion.update(live_completion)
            required_uids.update(
                uid_of_name[name] for name in required_names)
        # 就绪待裁键时间可核验（判据与旧全域路径一致：不交出可立即重裁
        # key）；按键路径按 C11 降为逐键 issue（A 键不连坐），旧全域路径
        # 仍为任务级 typed raise。
        time_unverifiable: dict[str, str] = {}
        for key in remaining:
            if statuses[key].status == AWAITING_ARBITRATION:
                for username in required_names:
                    vote = votes.get(username, {}).get(key)
                    if vote is not None and vote.decided_at is None:
                        time_unverifiable[key] = username
                        break
        for key, username in time_unverifiable.items():
            statuses.pop(key, None)
            issues[key] = KeyIssue(
                reason="vote_time_unverifiable", stage=stage, key=key,
                source_role="actor", retryable=True)
    if config is None:
        # 无 live 键（全部完成/已发 issue）也要给出全局 config 诊断（旧字段
        # 恒在）；roster/accounts 身份失败仍在此前 task-level typed 拒绝。
        config = configuration_status(StageInputs(
            stage=stage, participants=participants_core,
            required=tuple(required_names), votes={}, task_id=scope.task_id,
            round_id=None, keys=()))

    return TeamStageView(
        stage=stage, task_id=scope.task_id, round_id=None, config=config,
        statuses=statuses, loader_stale=tuple(loader_stale), keys=keys,
        applied=applied, issues=issues, completion=completion,
        completion_required_uids=tuple(sorted(required_uids)))


def _chain_completion_projection(target: str, key: str, item: ChainItem,
                                 stage1: TeamStageView,
                                 stage2: TeamStageView,
                                 ) -> CompletionProjection | None:
    """链级完成投影：仅取真正决定该键链结果的阶段完成事实。

    cleared → 末个必要阶段（stage2 目标=stage1；coding/appraisal=stage2 优先
    、缺则 stage1）；blocked → 阻断令牌指向的 final_exclude 阶段；其余
    （awaiting/上游缺评）不进映射——链结果并非由完成事实决定。
    """
    if item.cleared:
        if target == "stage2":
            return stage1.completion.get(key)
        return stage2.completion.get(key) or stage1.completion.get(key)
    reason = item.blocked_reason or ""
    if reason == "upstream_stage1_final_exclude":
        return stage1.completion.get(key)
    if reason == "upstream_stage2_final_exclude":
        return stage2.completion.get(key)
    return None


def _keyed_chain_view(scope: TeamReadScope, target: str,
                      keys: tuple[str, ...]) -> TeamChainView:
    """按键链视图：stage1 exclude 已知即剪枝 stage2 读取（C07）；每请求键恰
    有 chain 或 issue（stage1 issue 优先；非 stage2 目标再看 stage2 issue）。"""
    return _chain_view_on(scope, target, keys,
                          plan=scope.keyed_plan(),
                          reader_of=scope.witness_connection)


def _chain_view_on(scope: TeamReadScope, target: str,
                   keys: tuple[str, ...], *,
                   plan: KeyedReadPlan | None,
                   reader_of=None) -> TeamChainView:
    """_keyed_chain_view 的参数化核（P24 收尾，C12 包 C）：与
    ``_stage_view_on`` 同一注入面（scope 附着 plan 或全域 overlay plan），
    剪枝/issue 序/clean completion 投影语义零分叉。"""
    stage1 = _stage_view_on(scope, "stage1", keys,
                            plan=plan, reader_of=reader_of)
    # 剪枝：stage1 已终局 exclude 的键不再读 stage2（C01.4 短路；F1 发现侧
    # 由 P08 plan 同步剪枝）。
    stage2_keys = tuple(
        key for key in keys
        if stage1.statuses.get(key) is None
        or stage1.statuses[key].status != FINAL_EXCLUDE)
    stage2 = _stage_view_on(scope, "stage2", stage2_keys,
                            plan=plan, reader_of=reader_of)
    full_chain = evaluate_chain(stage1.statuses, stage2.statuses, target,
                                keys=list(keys))
    issues: dict[str, KeyIssue] = {}
    completion: dict[str, CompletionProjection] = {}
    chain: dict[str, ChainItem] = {}
    for key in keys:
        issue = stage1.issues.get(key)
        if issue is None and target != "stage2":
            issue = stage2.issues.get(key)
        if issue is not None:
            issues[key] = issue
            continue
        item = full_chain[key]
        chain[key] = item
        projection = _chain_completion_projection(
            target, key, item, stage1, stage2)
        if projection is not None:
            completion[key] = projection
    return TeamChainView(
        target=target, chain=chain, stage1=stage1, stage2=stage2,
        issues=issues, completion=completion)


# ---------------------------------------------------------------------------
# P24 收尾（C12 包 C）：写窗进度队列的团队化按键评估
# ---------------------------------------------------------------------------

def _queue_witness_reader(path: Path) -> sqlite3.Connection:
    """进度队列 overlay 的 holder 见证 reader：mode=ro＋Row＋显式 BEGIN
    （满足 F1 ``read_seal_on`` 借用合同并固定该 holder 读取快照）。

    与 ``team_keyed_reads._open_readonly_reader`` 同型（本模块不跨界导入
    私有符号）；打开失败由调用方按 ``witness_unreadable`` 呈现该 holder 的
    键，不遮蔽其他已见证键。
    """
    conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro",
                           uri=True, timeout=_RO_TIMEOUT)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN")
    except BaseException:
        try:
            conn.close()
        except sqlite3.Error:
            pass
        raise
    return conn


def keyed_progress_queue(scope: TeamReadScope, ctx, target: str) -> list[str]:
    """写窗进度队列的团队化按键评估（完成看 seal、未完成看票）。

    ``coding_result`` 的写窗进度队列此前走全域 ``team_chain_view``
    （votes-only），对 pre-join-sealed/归档键显示错误进度：seal 已终局的键
    仍按当前名册全员数票（后加入员无票 → 键被当未清出队、total 虚低）。本
    函数在 scope 的 collab 屏障连接上一次构建**全域** P08 keyed read plan
    （与 P23 ``keyed_work_items``/``protected_keyed_read_scope`` 同一发现
    通道、``work_items_read`` 读形操作），经 ``_chain_view_on`` 用该 overlay
    plan 评估链：见证键直读 F1 payload 给 FINAL_*（不读票），无见证键读
    当前名册＋按键票，冲突键走 C06 按键仲裁——与 HIGH#1 落地面
    （B1/B2：f19a4ead/0a79005a）读写同语义。返回链 cleared 键（canonical
    域序），供 ``coding_progress_on`` 物化进度；键级 issue（witness 不可读/
    漂移等）该键不计入队列（保守展示，不炸请求）。

    连接面：holder 见证经短命 ro reader（本函数自开自收，绝不动窗口保留
    连接）；collab 见证经 scope 屏障连接；票读经 scope 保留成员连接。task
    未注册 P08 协议（``KeyedPlanUnavailableError``）→ 回退修复前全域票面
    展示（真实 P13 写窗恒已注册——窗口自身的 plan 构建先于此失败；回退仅
    覆盖 legacy/合成路径，进度展示不因此 503）。其余 typed 错误原样上抛
    （与旧全域路径同口径）。只读：不 BEGIN/commit/rollback 窗口连接、
    零 DDL/零迁移/零建库。
    """
    if target not in _CHAIN_TARGETS:
        raise ValueError(f"target 必须为 {_CHAIN_TARGETS} 之一: {target!r}")
    domain = scope.canonical_keys()
    if not domain:
        return []
    from coscreen.team_flow_types import TeamOperation
    from custom_backend.team_keyed_inputs import (
        KeyedPlanUnavailableError,
        build_keyed_read_plan,
    )
    try:
        plan = build_keyed_read_plan(
            ctx, collab_conn=scope.collab_connection(),
            operation=TeamOperation(name="work_items_read", stage=target),
            item_keys=domain)
    except KeyedPlanUnavailableError:
        # legacy/合成路径（无 P08 协议注册）：维持修复前全域 votes-only 展示。
        return chain_cleared_keys(team_chain_view(scope, target))
    readers: dict[Path, sqlite3.Connection] = {}
    unreadable: set[Path] = set()

    def reader_of(path: Path):
        if path in unreadable:
            return None
        conn = readers.get(path)
        if conn is None:
            try:
                conn = _queue_witness_reader(path)
            except sqlite3.Error:
                unreadable.add(path)
                return None
            readers[path] = conn
        return conn

    try:
        view = _chain_view_on(scope, target, domain,
                              plan=plan, reader_of=reader_of)
        return chain_cleared_keys(view)
    finally:
        # 自开 reader 只读事务，确定性收尾（rollback→close）；释放失败不吞
        # 在飞主错（只可能发生在无主错的清理段，如实上抛）。
        for conn in readers.values():
            try:
                conn.rollback()
            except sqlite3.Error:
                pass
            try:
                conn.close()
            except sqlite3.Error:
                pass


# ---------------------------------------------------------------------------
# 键集/原因 helpers
# ---------------------------------------------------------------------------

def stage_final_keys(view: TeamStageView) -> list[str]:
    """final_include 键（按视图 keys 序；issue 键跳过——P09 视图 statuses
    不再覆盖全部 keys）。"""
    return [key for key in view.keys
            if key in view.statuses
            and view.statuses[key].status == FINAL_INCLUDE]


def chain_cleared_keys(view: TeamChainView) -> list[str]:
    """链评估 cleared 键。"""
    return [key for key, item in view.chain.items() if item.cleared]


def arbitration_ready_keys(view: TeamStageView) -> list[str]:
    """全员正式完成且分歧、无适用证明的键（就绪待裁集；issue 键跳过）。"""
    return [key for key in view.keys
            if key in view.statuses
            and view.statuses[key].status == AWAITING_ARBITRATION]


def team_item_gate_reason(item: object) -> str | None:
    """ItemStatus/ChainItem → 阻塞原因令牌；final_*/cleared → None。"""
    if isinstance(item, ChainItem):
        if item.cleared:
            return None
        return "team_item_" + (item.blocked_reason or "unknown")
    if isinstance(item, ItemStatus):
        if item.status in (FINAL_INCLUDE, FINAL_EXCLUDE):
            return None
        return "team_item_" + item.status
    raise TypeError(
        f"team_item_gate_reason 只接受 ItemStatus/ChainItem: {type(item)!r}")
