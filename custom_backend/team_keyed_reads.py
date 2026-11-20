"""custom_backend/team_keyed_reads.py — P09 受保护按键只读 scope（C07/C08）。

合同（冻结，实现不偏离）：

- **屏障**：同 canonical 任务锁（``runtime.lock``，与写窗/协议操作同锁
  串行）＋自开 collab 专用 ``mode=rw`` 连接上的 ``BEGIN IMMEDIATE``——
  与 B1 写窗同型的屏障序（lock → collab IMMEDIATE → plan → 读取），但
  **退出只 ROLLBACK、绝不 commit**：零 prepare、零业务 DML、零 seal、
  零 receipt、不借 B1 假选 target commit（C07 只读段）。缺 collab 文件＝
  typed ``collab_unreadable``（只读屏障拒绝创建）；打开/上锁失败＝typed
  ``collab_unreadable``；``ProgrammingError`` 原样透传。
- **发现**：屏障内调用 P08 ``build_keyed_read_plan(ctx, collab_conn=屏障,
  operation, item_keys)``；``KeyedPlanUnavailableError``（task_not_registered/
  collab_seals_unreadable/registry_unavailable）包装为
  ``TeamFlowUnavailableError("completion_source_unresolved")``（C02 新增
  reason；HTTP 层走既有 503 映射）。``operation``/``item_keys`` 形状错误
  （ValueError/TypeError）原样上抛＝编程误用，不伪装任务不可用。
- **保留连接**：plan 选出的 holder 见证路径在计划返回后立即开 ``mode=ro``
  ＋Row＋显式 BEGIN 的保留 reader（活动事务满足 F1 ``*_on`` 借用合同并
  固定该 holder 读取快照）；打开失败的路径记入
  ``unreadable_witness_paths``——对应键由视图按 ``witness_unreadable``
  呈现，**不遮蔽其他已见证键**。其他来源（accounts/owner catalog/成员票
  库）由 fresh 形态 ``TeamReadScope`` 按需懒开（``_ro_connect`` 的 ro＋
  Row＋DEFERRED；同物理库去重/claim/F3 containment 沿用）。
- **退出顺序**（确定性收尾，与写窗同构）：scope 自开连接
  （``close_fresh`` 逆序 rollback+close）→ holder 见证 reader（rollback+
  close）→ collab 屏障（rollback+close）。清理失败不吞主错（``add_note``
  附着于在飞主异常）；无主错时如实上抛。所有自开 handle 退出关闭。
- **一致性边界（不夸大）**：collab 屏障固定，ro 来源各自单库快照——各库
  **不是**跨库原子快照，也不对外部 CLI/跨进程并发提供全局原子性；本
  manager 仅保证锁内协议操作与本 scope 读取的串行化。
- **身份/访问**：``runtime.task_lookup`` 规范化 task_id（canonical info）；
  ``runtime.readonly_context``＝零建库/零迁移/零 ensure 的规范只读入口
  （403 拒绝原样透传；不调用 ``runtime.context``——那会懒建 owner/actor
  库，违反零 prepare）。``user`` 形状非法＝typed
  ``identity_unconfirmed``（编程/会话错误不伪装逐键待办）。

依赖归属：本模块与 ``custom_backend/team_screening_runtime`` 同属 P09
（文件互斥表），故导入其模块级 ``_build_scope``（fresh 形态构造＋info 形状
校验复用，避免双份漂移）；``TeamReadScope`` 的 plan/见证附件接口见其
``attach_keyed_plan``/``keyed_plan``/``witness_connection``。
"""

from __future__ import annotations

import sqlite3
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from coscreen import task_collaboration as collaboration_mod
from coscreen.team_flow_types import TeamOperation, normalize_item_keys

from custom_backend.team_keyed_inputs import (
    KeyedPlanUnavailableError,
    build_keyed_read_plan,
)
from custom_backend.team_screening_runtime import (
    TeamFlowUnavailableError,
    TeamReadScope,
    _build_scope,
)

__all__ = ["protected_keyed_read_scope"]

#: 屏障/保留 reader 的打开超时（秒）；与 team_screening_runtime._RO_TIMEOUT
#: 同值。
_BARRIER_TIMEOUT = 15.0


def _task_id_from_info(info: object) -> str:
    """info（TaskInfo 形状或 str）→ 非空 task_id（形状错误＝ValueError）。"""
    raw = info if isinstance(info, str) else getattr(info, "task_id", None)
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("info must expose a non-empty task_id")
    return raw


def _open_barrier_connection(collab_path: Path) -> sqlite3.Connection:
    """collab 屏障连接：mode=rw（绝不创建）＋Row＋``BEGIN IMMEDIATE``。

    只持锁不写行：整个 scope 生命周期内不执行任何 DML/DDL/PRAGMA，退出由
    :func:`_release_read_handle` ROLLBACK（绝不 commit）。
    """
    if not collab_path.is_file():
        raise TeamFlowUnavailableError(
            "collab_unreadable",
            detail=f"协作库缺失（只读屏障拒绝创建）: {collab_path.name}")
    conn = sqlite3.connect(
        collab_path.resolve().as_uri() + "?mode=rw",
        uri=True, timeout=_BARRIER_TIMEOUT)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN IMMEDIATE")
    except BaseException:
        try:
            conn.close()
        except sqlite3.Error:
            pass
        raise
    return conn


def _open_readonly_reader(path: Path) -> sqlite3.Connection:
    """holder 见证保留 reader：mode=ro（绝不创建）＋Row＋显式 BEGIN。"""
    conn = sqlite3.connect(
        path.resolve().as_uri() + "?mode=ro",
        uri=True, timeout=_BARRIER_TIMEOUT)
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


def _release_read_handle(conn: sqlite3.Connection, *,
                         primary: BaseException | None) -> None:
    """rollback（如仍在事务）并 close 一个自开连接；清理失败不吞主错。

    有在飞主异常时以 ``add_note`` 附着清理失败（不遮蔽、不改类型）；无主错
    时如实上抛清理错误（成功路径的资源失败不当被 GC 吞掉）。
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
                f"protected keyed read handle release failure: "
                f"{cleanup_error!r}")
        else:
            raise cleanup_error


@contextmanager
def protected_keyed_read_scope(
    runtime, info, user, *, operation: TeamOperation,
    item_keys,
) -> Iterator[TeamReadScope]:
    """C07 只读段：canonical 锁＋collab IMMEDIATE 屏障内的按键只读作用域。

    yield 一个附着了 ``KeyedReadPlan`` 与 holder 见证连接的 fresh 形态
    ``TeamReadScope``；``team_stage_view``/``team_chain_view`` 在其上以
    ``item_keys`` 给出带 keys/issues/completion 的按键视图（完成事实优先，
    全局 config 诊断不推翻完成 A）。一切读取都在屏障内；退出只回滚关闭，
    绝不提交任何业务变更——**本 manager 从不持久化观察/seal/receipt**。
    """
    if not isinstance(operation, TeamOperation):
        raise TypeError(
            "operation must be coscreen.team_flow_types.TeamOperation")
    keys = normalize_item_keys(item_keys)
    user_id = user.get("user_id") if isinstance(user, dict) else None
    if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id <= 0:
        raise TeamFlowUnavailableError(
            "identity_unconfirmed",
            detail="authenticated user_id is missing or invalid")
    user_username = user.get("username") if isinstance(user, dict) else None
    if not isinstance(user_username, str) or not user_username:
        # P09 复审 NOTE-3 防御性收口（P10 落地）：缺 username 时
        # ``runtime.readonly_context`` 内 ``user["username"]`` 会以裸
        # KeyError 逃逸——先验形状，typed ``identity_unconfirmed``（编程/
        # 会话错误不伪装逐键待办，也不冒充任务不可用）。
        raise TeamFlowUnavailableError(
            "identity_unconfirmed",
            detail="authenticated username is missing or invalid")

    canonical_info = runtime.task_lookup(_task_id_from_info(info))
    task_id = getattr(canonical_info, "task_id", None)
    if not isinstance(task_id, str) or not task_id.strip():
        raise ValueError("canonical task lookup returned no task_id")
    accounts_path = (Path(runtime.data_dir) / "users.db").resolve()
    collab_path = Path(
        collaboration_mod.shared_store_path(canonical_info.dir))

    with runtime.lock(task_id):
        try:
            barrier = _open_barrier_connection(collab_path)
        except TeamFlowUnavailableError:
            raise
        except (sqlite3.Error, OSError) as exc:
            raise TeamFlowUnavailableError(
                "collab_unreadable",
                detail=f"协作库只读屏障打开失败: {exc}") from exc
        readers: dict[Path, sqlite3.Connection] = {}
        try:
            ctx = runtime.readonly_context(task_id, user)
            if getattr(ctx.info, "task_id", None) != task_id:
                raise TeamFlowUnavailableError(
                    "unconfirmed_task_state",
                    detail="readonly context task id differs from canonical id")
            try:
                plan = build_keyed_read_plan(
                    ctx, collab_conn=barrier, operation=operation,
                    item_keys=keys)
            except KeyedPlanUnavailableError as exc:
                raise TeamFlowUnavailableError(
                    "completion_source_unresolved",
                    detail=f"按键读取计划不可用: {exc}") from exc
            for path in plan.selected_paths:
                try:
                    readers[path] = _open_readonly_reader(path)
                except (sqlite3.Error, OSError):
                    # 保留失败≠遮蔽：该路径键由视图按 witness_unreadable
                    # 呈现（attach 的 unreadable_witness_paths）。
                    continue
            unreadable = frozenset(
                path for path in plan.selected_paths if path not in readers)
            scope = _build_scope(
                ctx.info, accounts_db=accounts_path, collab_path=None,
                owner_path=None, member_paths={}, connections={},
                missing_members=frozenset(), fresh=True, collab_conn=barrier)
            scope.attach_keyed_plan(plan, readers, unreadable)
            try:
                yield scope
            finally:
                scope.close_fresh()
        finally:
            primary = sys.exc_info()[1]
            for reader in list(readers.values()):
                _release_read_handle(reader, primary=primary)
            _release_read_handle(barrier, primary=primary)
