"""custom_backend/team_keyed_inputs.py — P08 按键发现与 KeyedReadPlan（C07）。

本模块只做发现与规划，不持久化、不提交：

- collab F1 按键先查；未命中才逐 F2 holder（ro 连接/Row 工厂/活动 DEFERRED
  事务）发现；holder 临时 reader 逐个打开、逐个 rollback/close（异常路径
  同样释放），任何读锁都不越出本函数。
- 找到可信 A 后坏 D 不遮蔽 A：已获见证的键不受后续来源失败影响；失败只
  记入仍无见证键的临时失败表。
- 可见冲突 seal 拒绝（冲突即拒、幂等兼容）：每个 holder 对全部待决键
  重扫（代价＝holder 数×pending 键数的逐键 F1 点查，键控 SELECT、无
  全域读，团队规模下可接受）；不同 holder 对同一 (stage, key) 给出互斥
  seal 时，该键改为 issue（conflicting_seals），不任选其一；四字段同
  seal＝幂等重复，保留确定序首个的归属。
- 负发现需完整覆盖（F2 coverage 为 new_covered/legacy_forward_only）且全部
  必要 holder 可核；未知≠None：覆盖未核（pending_verification/unknown）、
  枚举行损坏、来源身份不可解析、holder F1 不可读，一律按 KeyIssue 呈现，
  绝不当作"该键无完成"。
- stage1 exclude 已知（collab 命中）时不取 stage2；上游尚待 live 时照常
  规划 stage2 条件依赖（消费端在最终上游未过时不消费下游）。
- 跨 UID 物理别名 fail-closed：P06 resolve_source_identity 的 library 身份
  核验 + 本模块按物理路径×UID 二次去重，命中即撤销该路径上的既有见证。
- pending（coverage 未核）/ None（F1 无该键行）/ not_prepared（无 F1 表，
  视为该 holder 明确无键）三者分别处理，不混同。

类型唯一来自 P00（coscreen.team_flow_types）；plan/witness 不在本模块
重复定义。KeyIssue.reason 固定词表（本模块产出）：

    completion_source_unresolved  覆盖未核（registry, retryable）
    discovery_incomplete          F2 枚举行损坏/强读丢失（registry, retryable）
    source_identity_unresolved    P06 token/物理/library 身份解析失败（actor）
    holder_unreadable             holder F1 读取失败且非 not_prepared（actor, retryable）
    conflicting_seals             可见冲突 seal（actor, 不可重试）
    cross_uid_alias               跨 UID 物理别名（actor, 不可重试）

ctx 形态（C07 未定死；本模块冻结的最小结构合同，交付报告已备案）：

    ctx.info.task_id -> str（非空、不含 NUL）
    ctx.info.dir     -> pathlib.Path（任务目录，绝对路径）

与 custom_backend.review_workflow_api.WorkflowContext(tasks.TaskInfo) 兼容。
collab_conn 借用合同＝C02：真 native sqlite3.Connection（或真实子类，拒
__class__ 伪装代理）、活动事务、row_factory is sqlite3.Row；本模块不
BEGIN/SAVEPOINT/commit/rollback/close 它，不改 PRAGMA/factory，不重连。
closed 连接的 ProgrammingError 原样透传。

业务阶段适配（C02 固定适配器）：screen→(stage1,)、fulltext→(stage2,)、
coding/appraisal→(stage1, stage2)（链式，stage1 exclude 剪枝 stage2）；
其余业务阶段（含 management/history）无 F1 适配，属形状误用（ValueError）。

发现器对 owner 库不做特殊路径：owner 若为合法观察者，同样以 F2 登记
来源身份参与发现；本模块不打开任何 owner/accounts 连接（保留集选择与
reserve 归 P09/P10）。
"""

from __future__ import annotations

import sqlite3
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path

from coscreen.team_flow_types import (
    KeyIssue,
    KeyedReadPlan,
    SealWitness,
    TaskBinding,
    TeamOperation,
    normalize_item_keys,
)
from coscreen.team_terminal_facts import TerminalFactsUnavailable, read_seal_on
from coscreen.team_terminal_registry import (
    TerminalRegistryUnavailable,
    iter_source_discovery_on,
    read_source_on,
    read_task_discovery_header_on,
)
from custom_backend.team_source_lifecycle import (
    SourceIdentityIssue,
    resolve_source_identity,
)

__all__ = ["KeyedPlanUnavailableError", "build_keyed_read_plan"]


#: 业务阶段 → F1 seal 阶段（C02 固定适配器；链式=(stage1, stage2)）。
#: HIGH#1 B1（2026-11-20）：fulltext 由 ("stage2",) 扩为链式双阶段——
#: fulltext 类 op 的链目标 "stage2" 依赖 stage1 完成事实（上游腿），plan
#: 应覆盖链的全部上游阶段，与 coding/appraisal 口径一致；附带获得
#: chained=True 的 C01.4 剪枝（collab stage1-final_exclude seal 剪掉
#: stage2 目标）。读写同令牌（P23 work_items_read 同走本映射）。
_OPERATION_F1_STAGES: Mapping[str, tuple[str, ...]] = {
    "screen": ("stage1",),
    "fulltext": ("stage1", "stage2"),
    "coding": ("stage1", "stage2"),
    "appraisal": ("stage1", "stage2"),
}

#: 临时 ro reader 的打开超时（秒）；与 team_screening_runtime._RO_TIMEOUT 同值。
_PROVISIONAL_READ_TIMEOUT = 15.0

#: 覆盖完整（负发现可用）的 F2 coverage 状态。
_COMPLETE_COVERAGE_STATUSES = frozenset({"new_covered", "legacy_forward_only"})


class KeyedPlanUnavailableError(RuntimeError):
    """任务级发现不可用（fail-closed typed）；reason 取 REASONS 之一。"""

    REASONS = (
        "task_not_registered",     # F2 无绑定该 task_id 的 protocol 行
        "collab_seals_unreadable", # collab F1 损坏/不可读（非 not_prepared）
        "registry_unavailable",    # F2 发现/强读失败（含 collab 内 registry 未备）
    )

    def __init__(self, reason: str, *, detail: str = "") -> None:
        if reason not in self.REASONS:
            raise ValueError(
                f"unknown KeyedPlanUnavailableError reason: {reason!r}")
        super().__init__(f"[{reason}] {detail}" if detail else f"[{reason}]")
        self.reason = reason
        self.detail = detail


def _require_plan_context(ctx: object) -> tuple[str, Path]:
    """校验 ctx 最小结构合同，返回 (task_id, task_dir)。"""
    info = getattr(ctx, "info", None)
    if info is None:
        raise TypeError(
            "ctx must expose .info (WorkflowContext-shaped: info.task_id/dir)")
    task_id = getattr(info, "task_id", None)
    if not isinstance(task_id, str) or not task_id or "\x00" in task_id:
        raise TypeError("ctx.info.task_id must be a non-empty NUL-free str")
    task_dir = getattr(info, "dir", None)
    if not isinstance(task_dir, Path):
        raise TypeError("ctx.info.dir must be a pathlib.Path task directory")
    if not task_dir.is_absolute():
        raise ValueError("ctx.info.dir must be an absolute task directory")
    return task_id, task_dir


def _require_borrowed_collab_connection(
    conn: sqlite3.Connection, *, api: str
) -> None:
    """C02 借用合同门：native/真实子类 → 活动事务 → Row 工厂（固定顺序）。

    closed 连接在 ``in_transaction`` 处抛出的 ProgrammingError 原样透传。
    """
    if not issubclass(type(conn), sqlite3.Connection):
        raise TypeError(
            f"{api} requires a native sqlite3.Connection or native subclass")
    if not conn.in_transaction:
        raise RuntimeError(f"{api} requires an active caller transaction")
    if conn.row_factory is not sqlite3.Row:
        raise RuntimeError(f"{api} requires conn.row_factory is sqlite3.Row")


def _f1_stages_for_operation(operation: TeamOperation) -> tuple[tuple[str, ...], bool]:
    """业务阶段 → (F1 阶段元组, 是否 stage1→stage2 链式)。"""
    if not isinstance(operation, TeamOperation):
        raise TypeError(
            "operation must be coscreen.team_flow_types.TeamOperation")
    if not isinstance(operation.name, str) or not operation.name:
        raise TypeError("operation.name must be a non-empty str")
    stages = _OPERATION_F1_STAGES.get(operation.stage) if isinstance(
        operation.stage, str) else None
    if stages is None:
        raise ValueError(
            f"operation.stage {operation.stage!r} has no fixed F1 adapter "
            "(screen/fulltext/coding/appraisal)")
    return stages, len(stages) == 2


def _key_issue(stage: str, key: str, reason: str,
               source_role: str, retryable: bool) -> KeyIssue:
    """KeyIssue 构造点：reason/source_role/retryable 词表集中于模块 docstring。"""
    return KeyIssue(reason=reason, stage=stage, key=key,
                    source_role=source_role, retryable=retryable)


def _same_seal_record(left: Mapping[str, object],
                      right: Mapping[str, object]) -> bool:
    """同一 (stage, key) 的两份 F1 记录是否为同一 seal（幂等重复）。"""
    return (left["seal_id"] == right["seal_id"]
            and left["content_sha256"] == right["content_sha256"]
            and left["payload_sha256"] == right["payload_sha256"]
            and left["sealed_at"] == right["sealed_at"])


def _witness_from_record(record: Mapping[str, object], *, stage: str,
                         item_key: str, binding: TaskBinding,
                         source_path: Path | None,
                         registration_id: str | None,
                         generation: int | None,
                         library_id: str) -> SealWitness:
    """F1 记录 → SealWitness。

    collab 持有：source_path/registration_id/generation=None，
    library_id＝binding.owner_library_id（同一借用连接内可复核的 collab
    权威域锚点）。holder 持有：三者来自被强读核验的 F2 登记行（P06 已核
    实际 library 身份），供 P10 最终重验。
    """
    return SealWitness(
        stage=stage,
        item_key=item_key,
        source_path=source_path,
        registration_id=registration_id,
        generation=generation,
        library_id=library_id,
        seal_id=record["seal_id"],
        content_sha256=record["content_sha256"],
        payload_sha256=record["payload_sha256"],
        sealed_at=record["sealed_at"],
    )


def _open_provisional_reader(path: Path) -> sqlite3.Connection:
    """打开 holder 临时 ro 连接：mode=ro（绝不创建）、Row 工厂、显式 BEGIN。

    活动事务满足 F1 ``*_on`` 系列的活动事务要求，并固定该 holder 的读取
    快照；本连接只在本模块存活，绝不做任何写语句。
    """
    uri = f"{path.resolve().as_uri()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=_PROVISIONAL_READ_TIMEOUT)
    conn.row_factory = sqlite3.Row
    conn.execute("BEGIN")
    return conn


def _release_provisional_reader(conn: sqlite3.Connection, *,
                                primary: BaseException | None) -> None:
    """rollback（如仍在事务中）并 close 一个临时 reader。

    清理失败不吞：有 primary 时以 add_note 附着（不遮蔽主错）；无 primary
    时如实上抛。
    """
    cleanup_error: BaseException | None = None
    try:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
    except sqlite3.Error as exc:
        cleanup_error = exc
    try:
        conn.close()
    except sqlite3.Error as exc:
        if cleanup_error is None:
            cleanup_error = exc
    if cleanup_error is not None:
        if primary is not None:
            primary.add_note(
                f"provisional reader release failure: {cleanup_error!r}")
        else:
            raise cleanup_error


def _read_collab_seals(collab_conn: sqlite3.Connection, *,
                       task_instance_id: str,
                       stages: tuple[str, ...],
                       item_keys: tuple[str, ...],
                       chained: bool,
                       ) -> tuple[dict[tuple[str, str], Mapping[str, object]],
                                  list[tuple[str, str]],
                                  frozenset[str], bool]:
    """Phase 1：collab F1 按键先查。

    返回 (records, pending, pruned, not_prepared)：records＝命中记录
    （键 (stage, item_key)）；pending＝仍无见证且未被剪枝的目标（按
    stage×请求键序）；pruned＝因 stage1 exclude 已知而被剪掉 stage2 的键；
    not_prepared＝collab F1 表未备（视为零 collab seal，干净继续，不当
    issue）。collab F1 损坏（非 not_prepared/非 connection_contract）为
    任务级 fail-closed。
    """
    records: dict[tuple[str, str], Mapping[str, object]] = {}
    pending: list[tuple[str, str]] = []
    pruned: set[str] = set()
    not_prepared = False
    for stage in stages:
        for key in item_keys:
            if chained and stage == "stage2" and key in pruned:
                continue
            if not_prepared:
                pending.append((stage, key))
                continue
            try:
                record = read_seal_on(
                    collab_conn, task_instance_id=task_instance_id,
                    stage=stage, item_key=key)
            except TerminalFactsUnavailable as exc:
                if exc.reason == "not_prepared":
                    not_prepared = True
                    pending.append((stage, key))
                    continue
                if exc.reason == "connection_contract":
                    raise
                raise KeyedPlanUnavailableError(
                    "collab_seals_unreadable",
                    detail=f"collab F1 read failed for stage={stage} "
                           f"key={key!r}: {exc}") from exc
            if record is not None:
                records[(stage, key)] = record
                if (chained and stage == "stage1"
                        and record["payload"]["final_decision"] == "exclude"):
                    pruned.add(key)
            else:
                pending.append((stage, key))
    return records, pending, frozenset(pruned), not_prepared


def _scan_holder_seals(conn: sqlite3.Connection, *,
                       task_instance_id: str,
                       targets: tuple[tuple[str, str], ...],
                       ) -> tuple[dict[tuple[str, str], Mapping[str, object]],
                                  str | None, bool]:
    """Phase 2 单 holder 扫描：对给定目标逐键读该 holder 的 F1。

    调用方传**全 pending**（含已见证键），供跨 holder 互斥 seal 冲突检测；
    已见证键在本 holder 的命中由调用方做幂等/冲突裁决。

    返回 (found, failure, not_prepared)。not_prepared＝该库无 F1 表（明确
    无键，干净）；failure 非 None＝该 holder F1 读取失败且非 not_prepared
    （坏 D 候选）；found＝命中的完整校验记录。
    """
    found: dict[tuple[str, str], Mapping[str, object]] = {}
    for stage, key in targets:
        try:
            record = read_seal_on(
                conn, task_instance_id=task_instance_id,
                stage=stage, item_key=key)
        except TerminalFactsUnavailable as exc:
            if exc.reason == "not_prepared":
                return {}, None, True
            return {}, f"holder_seals_unreadable:{exc.reason}", False
        if record is not None:
            found[(stage, key)] = record
    return found, None, False


def _finalize_unresolved_keys(*, pending, holder_found, blocked,
                              provisional_failures, coverage_status,
                              verified_active_sources):
    """负发现终局：无见证键 → 冲突/别名 issue / 具体失败 issue / 覆盖未决
    issue / 干净负发现。

    blocked（conflicting_seals/cross_uid_alias，含被别名撤销的原见证键）
    直接进 issues（优先级最高：先有见证后见冲突的键以冲突为准，且不遮蔽
    存活见证键），并不再保留 live 依赖。干净负发现（覆盖完整且全部枚举
    holder 可核）不产生 issue，该键进入各已核 active 源的
    live_dependencies（(source_user_id, stage) → 键元组，键按请求序）；
    带失败/覆盖 issue 的键同样保留已核 active 源的 live 读依赖（issue 只
    阻塞完成主张，不阻塞当前票读取）。
    """
    issues: dict[tuple[str, str], KeyIssue] = {}
    live: dict[tuple[int, str], list[str]] = {}
    coverage_complete = coverage_status in _COMPLETE_COVERAGE_STATUSES
    for target in pending:
        if target in holder_found:
            continue
        blocked_issue = blocked.get(target)
        if blocked_issue is not None:
            issues[target] = blocked_issue
            continue
        failure = provisional_failures.get(target)
        if failure is not None:
            issues[target] = failure
        elif not coverage_complete:
            issues[target] = _key_issue(
                target[0], target[1], "completion_source_unresolved",
                "registry", True)
        for source in verified_active_sources:
            live.setdefault(
                (source["source_user_id"], target[0]), []).append(target[1])
    live_dependencies = {
        pair: tuple(dict.fromkeys(keys)) for pair, keys in live.items()}
    return issues, live_dependencies


def build_keyed_read_plan(ctx, *, collab_conn: sqlite3.Connection,
                          operation: TeamOperation,
                          item_keys: Iterable[str]) -> KeyedReadPlan:
    """C07 按键读取计划：发现真实完成见证并规划 live 依赖（零持久化）。

    调用前提：调用方已持有 canonical 锁并进入 collab 事务（collab_conn 为
    该活动事务的借用连接）。返回的 plan 只用于选保留集，不授写权限。
    """
    keys = normalize_item_keys(item_keys)
    stages, chained = _f1_stages_for_operation(operation)
    task_id, task_dir = _require_plan_context(ctx)
    _require_borrowed_collab_connection(collab_conn, api="build_keyed_read_plan")

    try:
        header = read_task_discovery_header_on(collab_conn, task_id=task_id)
    except TerminalRegistryUnavailable as exc:
        raise KeyedPlanUnavailableError(
            "registry_unavailable",
            detail=f"task discovery header read failed: {exc}") from exc
    if header is None:
        raise KeyedPlanUnavailableError(
            "task_not_registered",
            detail=f"no team protocol row is bound to task_id {task_id!r}")
    binding = header.binding
    instance = binding.task_instance_id

    records, pending, _pruned, _collab_not_prepared = _read_collab_seals(
        collab_conn, task_instance_id=instance, stages=stages,
        item_keys=keys, chained=chained)

    holder_found: dict[tuple[str, str],
                       tuple[Mapping[str, object], Mapping[str, object], Path]] = {}
    provisional_failures: dict[tuple[str, str], KeyIssue] = {}
    blocked: dict[tuple[str, str], KeyIssue] = {}
    verified_active_sources: list[Mapping[str, object]] = []
    path_owner: dict[Path, int] = {}
    scanned_paths: set[Path] = set()
    tainted_paths: set[Path] = set()
    unresolved: set[tuple[str, str]] = set(pending)

    open_readers: list[sqlite3.Connection] = []
    try:
        # collab 已覆盖全部（未剪枝）目标时零 holder 打开、零 live 依赖，
        # 不为已见证键触碰任何来源库；枚举器的连接/布局校验（含
        # not_prepared）在 try 内统一包装为 registry_unavailable。
        rows = (iter_source_discovery_on(
                    collab_conn, task_instance_id=instance)
                if pending else ())
        for row in rows:
            source = row.source
            if source is None:
                for target in unresolved:
                    provisional_failures.setdefault(target, _key_issue(
                        target[0], target[1], "discovery_incomplete",
                        "registry", True))
                continue
            try:
                strong = read_source_on(
                    collab_conn, registration_id=source["registration_id"])
            except TerminalRegistryUnavailable as exc:
                raise KeyedPlanUnavailableError(
                    "registry_unavailable",
                    detail=f"source strong read failed for "
                           f"{source['registration_id']!r}: {exc}") from exc
            if strong is None:
                for target in unresolved:
                    provisional_failures.setdefault(target, _key_issue(
                        target[0], target[1], "discovery_incomplete",
                        "registry", True))
                continue
            # HIGH#1 B5（重接轮，2026-11-20）：retired source 跳过强读/身份
            # 核验——P14/HIGH#2 退休移除把成员库改名移走且其全部 seals 已
            # 按 archive_watermark 归档进 collab（Phase1 可见证），该 holder
            # 对发现层已无可贡献；对其 resolve 必然 not_regular_file，若不
            # 跳过会把全部 pending 目标污染为不可重试的
            # source_identity_unresolved，使归档 seal 已可见的键读写永久
            # fail-closed（违背设计 §5 E4"B 退休后其库不再被保留/发现
            # 依赖"）。跳过不降低覆盖完备性：归档副本在 collab 权威域。
            if strong.get("status") == "retired":
                continue

            try:
                path = resolve_source_identity(
                    task_dir=task_dir, source=strong)
            except SourceIdentityIssue as exc:
                for target in unresolved:
                    provisional_failures.setdefault(target, _key_issue(
                        target[0], target[1], "source_identity_unresolved",
                        "actor", False))
                continue

            uid = strong["source_user_id"]
            prior_uid = path_owner.get(path)
            if prior_uid is None:
                path_owner[path] = uid
            elif prior_uid != uid or path in tainted_paths:
                # 跨 UID 物理别名（或既有别名路径复用）：fail-closed——
                # 撤销该路径上已采见证，仍无见证键记别名 issue。
                tainted_paths.add(path)
                for target in list(holder_found):
                    if holder_found[target][2] == path:
                        del holder_found[target]
                        blocked[target] = _key_issue(
                            target[0], target[1], "cross_uid_alias",
                            "actor", False)
                for target in unresolved:
                    provisional_failures.setdefault(target, _key_issue(
                        target[0], target[1], "cross_uid_alias",
                        "actor", False))
                continue
            if path in scanned_paths:
                # 同 UID 多代次同一物理库：F1 内容一致，不重复打开；active
                # 代次仍计入 live 依赖。
                if strong["status"] == "active":
                    verified_active_sources.append(strong)
                continue

            # 扫描快照＝全 pending：每个 holder 对全部待决键重扫（含已见
            # 证键），使两 holder 互斥 seal 可见（冲突即拒、幂等兼容）；
            # 代价＝holder 数×pending 键数的逐键点查，团队规模下可接受。
            targets_snapshot = pending
            try:
                reader = _open_provisional_reader(path)
            except sqlite3.DatabaseError:
                # 打开/上锁失败＝坏 D 候选（holder_unreadable，retryable），
                # 只记仍无见证键，不遮蔽已见证 A。
                for target in unresolved:
                    provisional_failures.setdefault(target, _key_issue(
                        target[0], target[1], "holder_unreadable",
                        "actor", True))
                continue
            open_readers.append(reader)
            try:
                found, failure, _not_prepared = _scan_holder_seals(
                    reader, task_instance_id=instance, targets=targets_snapshot)
            except BaseException as exc:
                _release_provisional_reader(reader, primary=exc)
                raise
            else:
                _release_provisional_reader(reader, primary=None)
            finally:
                if reader in open_readers:
                    open_readers.remove(reader)

            scanned_paths.add(path)
            if strong["status"] == "active":
                verified_active_sources.append(strong)
            if failure is not None:
                for target in unresolved:
                    if target not in found:
                        provisional_failures.setdefault(target, _key_issue(
                            target[0], target[1], "holder_unreadable",
                            "actor", True))
                continue
            for target, record in found.items():
                if target in blocked:
                    # 冲突/别名一经判定即终局：后续 holder 的 seal（含第
                    # 三方再证）不使 blocked 键复活为见证。
                    continue
                prior = holder_found.get(target)
                if prior is None:
                    holder_found[target] = (record, strong, path)
                    unresolved.discard(target)
                elif not _same_seal_record(prior[0], record):
                    # 互斥 seal＝数据完整性信号：拒绝并复用 blocked 呈报
                    # 通道（B1），unresolved 不回加。
                    del holder_found[target]
                    blocked[target] = _key_issue(
                        target[0], target[1], "conflicting_seals",
                        "actor", False)
                    unresolved.discard(target)
                # else：四字段同 seal＝幂等重复，保留确定序首个的归属。
    except TerminalRegistryUnavailable as exc:
        # 枚举分页/读取失败＝发现不完整，绝不能静默声称完整（C04）；
        # 任务级 fail-closed。
        raise KeyedPlanUnavailableError(
            "registry_unavailable",
            detail=f"source discovery enumeration failed: {exc}") from exc
    finally:
        primary = sys.exc_info()[1]
        for reader in open_readers:
            _release_provisional_reader(reader, primary=primary)

    witnesses: dict[tuple[str, str], SealWitness] = {}
    for target, record in records.items():
        witnesses[target] = _witness_from_record(
            record, stage=target[0], item_key=target[1], binding=binding,
            source_path=None, registration_id=None, generation=None,
            library_id=binding.owner_library_id)
    for target, (record, strong, path) in holder_found.items():
        witnesses[target] = _witness_from_record(
            record, stage=target[0], item_key=target[1], binding=binding,
            source_path=path, registration_id=strong["registration_id"],
            generation=strong["generation"],
            library_id=strong["source_library_id"])

    issues, live_dependencies = _finalize_unresolved_keys(
        pending=pending, holder_found=holder_found, blocked=blocked,
        provisional_failures=provisional_failures,
        coverage_status=header.coverage_status,
        verified_active_sources=verified_active_sources)

    selected_paths: list[Path] = []
    seen_paths: set[Path] = set()
    for stage in stages:
        for key in keys:
            witness = witnesses.get((stage, key))
            if (witness is not None and witness.source_path is not None
                    and witness.source_path not in seen_paths):
                seen_paths.add(witness.source_path)
                selected_paths.append(witness.source_path)

    return KeyedReadPlan(
        binding=binding,
        operation=operation,
        item_keys=keys,
        witnesses=witnesses,
        live_dependencies=live_dependencies,
        issues=issues,
        selected_paths=tuple(selected_paths),
    )
