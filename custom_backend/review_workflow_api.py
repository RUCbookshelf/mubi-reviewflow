"""Task workflow policies and queues using identities verified by the host."""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal
from urllib.parse import quote

from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field

from coscreen import binary_reset, coding, coding_evidence, review_workflow as store, task_collaboration, tasks
from coscreen.models import Article
from custom_backend.events import BUS

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids runtime coupling
    from collections.abc import Callable, Mapping  # P15 mutate 注解专用

    from custom_backend.team_screening_runtime import TeamReadScope  # P23 注解专用
    from custom_backend.team_screening_write import TeamWriteInputs

Stage = Literal["screen", "fulltext", "coding"]

#: P23 keyed 读服务：内部 SQL 每块至多 200 键（C02——owner 分页域枚举，
#: 不全域读取后过滤）。
_KEYED_READ_PAGE = 200
#: 界面阶段 → 链目标（与 team_write_checks._OPERATIONS 的 member_vote/
#: member_coding 规则同源：screen 无链；fulltext→stage2；coding→coding）。
_STAGE_CHAIN_TARGET = {"screen": None, "fulltext": "stage2", "coding": "coding"}
#: 界面阶段 → 同阶段完成/当前一致检查所用的 ledger 视图阶段（coding 无
#: 筛选完成投影，不外推成 coding 历史锁）。
_STAGE_OPEN_VIEW = {"screen": "stage1", "fulltext": "stage2", "coding": None}
#: 界面阶段 → 成员票 ledger 阶段（coding 值不是票，无 own_vote 投影）。
_STAGE_VOTE_LEDGER = {"screen": "stage1", "fulltext": "stage2"}

#: fix-r1 R5：筛选职责拒绝的结构化 403 载荷——与 custom_backend/main.py 的
#: ``_screening_disabled_error()`` / ``SCREENING_NOT_ENABLED_MESSAGE`` 逐字节同形
#: （常量锚定 main.py；十语言词条已按 message_key=team.screening_not_enabled 注册，
#: 不重复注册）。两处必须同步修改。
_SCREENING_NOT_ENABLED_MESSAGE = (
    "你尚未获得参与本任务筛选的权限，请联系任务负责人开启「允许参与筛选」后再试。")
_SCREENING_NOT_ENABLED_PAYLOAD = {
    "error_code": "screening_not_enabled",
    "message_key": "team.screening_not_enabled",
    "params": {},
    "details": "Screening participation is not enabled for this member",
    "message": _SCREENING_NOT_ENABLED_MESSAGE,
}


@dataclass
class WorkflowContext:
    info: tasks.TaskInfo
    user: dict
    owner: dict | None
    owner_db: Path
    actor_db: Path
    can_manage: bool
    task_kind: str
    collaboration_db: Path
    #: Only set inside a confirmed team write window.  None keeps every
    #: personal/legacy caller on the original path-based reads.
    write_inputs: "TeamWriteInputs | None" = None
    #: P23（C08）：本请求受保护只读 scope（protected_keyed_read_scope 的
    #: yield 值）。仅在该请求存活期内非 None（服务侧 try/finally 恢复），
    #: 离开请求必为 None——读取通道不串请求。solo/legacy 恒 None。
    team_read_scope: "TeamReadScope | None" = None


def _require_read_conn(conn: sqlite3.Connection, *, api: str) -> None:
    """Borrowed-read contract: native, active caller txn, caller Row factory."""
    if not isinstance(conn, sqlite3.Connection):
        raise RuntimeError(
            f"{api} requires an existing sqlite3.Connection (borrowed read; never opened here).")
    if not conn.in_transaction:
        raise RuntimeError(
            f"{api} requires an active caller transaction; this function never starts one.")
    if conn.row_factory is not sqlite3.Row:
        raise RuntimeError(
            f"{api} requires the caller connection to use sqlite3.Row; "
            "this function never changes row_factory.")


def _team_inputs(ctx: WorkflowContext) -> "TeamWriteInputs | None":
    """Return the live team window inputs or None; never fall back to path.

    Once ``ctx.write_inputs`` is present it must be the same context's confirmed
    team window with all retained connections native/active/Row.  A stale or
    malformed window raises instead of silently reconnecting by path.
    """
    inputs = getattr(ctx, "write_inputs", None)
    if inputs is None:
        return None
    if getattr(ctx, "task_kind", None) != "team":
        raise RuntimeError("write_inputs is only valid for confirmed team tasks")
    if getattr(inputs, "ctx", None) is not ctx:
        raise RuntimeError("write_inputs.ctx must be the same WorkflowContext")
    connections = tuple(getattr(inputs, "connections", ()))
    if not connections:
        raise RuntimeError("write_inputs has no retained connections")
    for conn in connections:
        _require_read_conn(conn, api="write_inputs.connections")
    for name in ("owner_conn", "actor_conn"):
        conn = getattr(inputs, name, None)
        if not any(conn is candidate for candidate in connections):
            raise RuntimeError(f"write_inputs.{name} is not in retained connections")
    return inputs


def _effective_read_scope(ctx: WorkflowContext) -> "TeamReadScope | None":
    """P23（C08）：读取通道解析——``write_inputs.scope`` 优先，否则本请求
    ``team_read_scope``；二者皆无（solo/legacy/未绑 scope 的 team 读）为 None。

    ``team_read_scope`` 校同 task（scope.task_id == ctx.info.task_id）、同
    team 任务、且 scope 自持 collab 屏障连接为 native/活动事务/Row（借读
    合同门）。违约 fail-closed（RuntimeError），绝不静默降级回 path-only
    自连读 owner/evidence。绑定生命周期由服务侧 try/finally 恢复，本函数
    只读不写。"""
    inputs = _team_inputs(ctx)
    if inputs is not None:
        return inputs.scope
    scope = getattr(ctx, "team_read_scope", None)
    if scope is None:
        return None
    if getattr(ctx, "task_kind", None) != "team":
        raise RuntimeError("team_read_scope is only valid for confirmed team tasks")
    if getattr(scope, "task_id", None) != getattr(ctx.info, "task_id", None):
        raise RuntimeError("team_read_scope task differs from this context's task")
    _require_read_conn(scope.collab_connection(), api="team_read_scope.collab_connection")
    return scope


def _article_rows_on_conn(conn: sqlite3.Connection, *, compact: bool) -> list[dict]:
    if not store.has_table(conn, "articles"):
        return []
    # compact 的两种取值都是固定枚举：各写一条字面量 SQL。
    if compact:
        rows = conn.execute(
            "SELECT zotero_key,title,authors,journal,year,doi,item_type,is_duplicate_of,import_order "
            "FROM articles ORDER BY import_order,zotero_key")
    else:
        rows = conn.execute("SELECT * FROM articles ORDER BY import_order,zotero_key")
    return [dict(row) for row in rows]


def _article_row_on_conn(conn: sqlite3.Connection, key: str) -> dict | None:
    if not store.has_table(conn, "articles"):
        return None
    row = conn.execute("SELECT * FROM articles WHERE zotero_key=?", (key,)).fetchone()
    return dict(row) if row else None


def _saved_rows_on_conn(conn: sqlite3.Connection, stage: str) -> dict:
    table = {"screen": "decisions", "fulltext": "stage2_decisions", "coding": "coding_values"}[stage]
    if not store.has_table(conn, table):
        return {}
    if stage == "coding":
        result: dict = {}
        for row in conn.execute("SELECT zotero_key,dimension_id,value FROM coding_values"):
            result.setdefault(row[0], {})[row[1]] = row[2]
        return result
    # table 只来自上方固定枚举（coding 已提前返回）：两条字面量 SQL。
    if table == "stage2_decisions":
        rows = conn.execute("SELECT * FROM stage2_decisions")
    else:
        rows = conn.execute("SELECT * FROM decisions")
    return {row["zotero_key"]: dict(row) for row in rows}


def _saved_item_on_conn(conn: sqlite3.Connection, stage: str, key: str) -> dict | None:
    table = {"screen": "decisions", "fulltext": "stage2_decisions", "coding": "coding_values"}[stage]
    if not store.has_table(conn, table):
        return None
    if stage == "coding":
        return {row[0]: row[1] for row in conn.execute(
            "SELECT dimension_id,value FROM coding_values WHERE zotero_key=?", (key,))}
    # table 只来自上方固定枚举（coding 已提前返回）：两条字面量 SQL。
    if table == "stage2_decisions":
        row = conn.execute("SELECT * FROM stage2_decisions WHERE zotero_key=?", (key,)).fetchone()
    else:
        row = conn.execute("SELECT * FROM decisions WHERE zotero_key=?", (key,)).fetchone()
    return dict(row) if row else None


class WorkflowRuntime:
    def __init__(self, task_lookup, screener_db, data_dir, visible,
                 queue_order=None) -> None:
        self.task_lookup, self.screener_db = task_lookup, screener_db
        self.data_dir, self.visible = Path(data_dir), visible
        # 2.1.5 W6：可选的队列排序钩子 ``(ctx, stage, keys) -> keys | None``。
        # 由宿主（custom_backend/main.py）注入近邻聚类排序；None = 保持既有
        # 导入序/随机序行为不变。钩子返回 None 表示“不改顺序”。
        self.queue_order = queue_order
        self._locks: dict[str, threading.RLock] = {}
        self._locks_guard = threading.Lock()

    def lock(self, task_id: str) -> threading.RLock:
        with self._locks_guard:
            return self._locks.setdefault(task_id, threading.RLock())

    @contextmanager
    def task_lock(self, task_id: str):
        """canonical task 锁的规范 id 入口（fix-r1 R4）：先解析尾点别名为规范
        task_id 再取 ``lock``，供 report-evidence 等需要与 write_guard/settings
        PUT/角色切换 API 同锁串行、但不走 write_guard 的端点复用。新符号
        （fix-r1 新增，无既有上游调用者）。"""
        task_id = self.task_lookup(task_id).task_id
        with self.lock(task_id):
            yield

    def readonly_context(self, task_id: str, user: dict) -> WorkflowContext:
        """只读上下文（R6-U4/#24）：与 context() 同一访问判定，但**不建库、
        不跑迁移/ensure_fields**——只解析权限与 actor 库路径。

        用于撤销状态等纯读端点：修复前 status 走 context()，会让
        screener_db(ensure=True) 的懒建链重建个人库、跑 store.migrate/
        ensure_fields 自愈 DDL，甚至为成员创建**属主**库并灌入整套表
        （r6 复审实测 48 张表）。属主/成员的个人库按既有命名规则只拼路径，
        不触达磁盘写。"""
        info = self.task_lookup(task_id)
        task_id = info.task_id
        collab = task_collaboration.shared_store_path(info.dir)
        owner = task_collaboration.get_owner(collab, task_id=task_id)
        if owner is not None:
            if not (owner["owner_user_id"] == user["user_id"]
                    or task_collaboration.is_participant(
                        collab, user["user_id"], task_id=task_id)):
                raise HTTPException(403, "Task access denied.")
        elif not self.visible(info, user):
            raise HTTPException(403, "Task access denied.")
        actor_db = info.dir / (tasks.slugify_screener(user["username"]) + ".db")
        if owner is None:
            return WorkflowContext(info, user, None, actor_db, actor_db, False, "legacy", collab)
        owner_db = info.dir / (tasks.slugify_screener(owner["owner_username"]) + ".db")
        return WorkflowContext(info, user, owner, owner_db, actor_db,
                               owner["owner_user_id"] == user["user_id"],
                               self._readonly_task_kind(info, owner), collab)

    def _readonly_task_kind(self, info, owner) -> str:
        """只读派生 task_kind（2.1.6 FLOW II-A；fix-r1 R6 与 context 事实一致）。

        与 :meth:`context` 同一判定**事实**——``team_latched==1`` 或协作库存在
        当前非属主 grant（邀请 grant 与 latch 两步提交的现实窗口不再判
        personal）——但**零写**：不建库、不迁移、不 latch。属主库确无文件/
        无表 = personal（兼容口径：从未开过工作流的属主任务）；锁忙/读失败/
        协作库中途不可读抛 503——读失败不得伪装成 personal（fail-closed，
        fix-r1 §9.2：PermissionError 不笼统折叠为无 grants）。"""
        owner_db = info.dir / (tasks.slugify_screener(owner["owner_username"]) + ".db")
        latched = False
        if owner_db.is_file():
            try:
                with closing(store.connect(owner_db, read_only=True)) as conn:
                    if store.has_table(conn, "workflow_state"):
                        row = conn.execute(
                            "SELECT team_latched FROM workflow_state WHERE singleton=1"
                        ).fetchone()
                        latched = row is not None and int(row[0] or 0) == 1
            except sqlite3.OperationalError as exc:
                raise HTTPException(
                    503, "任务协作状态暂时无法读取，请稍后重试。") from exc
            except OSError as exc:
                raise HTTPException(
                    503, "任务协作状态暂时无法读取，请稍后重试。") from exc
        if latched:
            return "team"
        try:
            if task_collaboration.has_non_owner_grants(
                    task_collaboration.shared_store_path(info.dir),
                    task_id=info.task_id,
                    owner_user_id=int(owner["owner_user_id"])):
                return "team"
        except task_collaboration.CollaborationStoreCorruptError:
            # fix-r2 B2：Corrupt 是 OwnerUnavailableError 的子类——必须先于
            # 父类 catch 按原类型上抛（desktop 409 修库指引 / server 503
            # fail-closed，交全局处理器）；否则被父类折成"暂不可用"503，
            # 下方"原样上抛"注释不可达（B2 回归）。
            raise
        except task_collaboration.OwnerUnavailableError as exc:
            raise HTTPException(
                503, "任务协作状态暂时无法读取，请稍后重试。") from exc
        # CollaborationStoreCorruptError 原样上抛（desktop 409 修库 / server 503）。
        return "personal"

    def is_team(self, task_id: str) -> bool:
        """task_kind 是否 team（只读派生；供角色门/投影调用，不进 context 热路径）。

        规范 task_id + 属主绑定照 context() 口径；legacy（无属主）恒 False。
        判定事实＝``team_latched==1`` 或协作库存在当前非属主 grant（fix-r1 R6：
        与 context() 的 latch 写入前事实一致，grant/latch 两步窗口不再误判
        personal）；撤销全员的已 latch 团队仍为 team（team_latched append-only，
        不能用"当前还有 grant" 替代）。读失败按 503 契约上抛，不折叠成 False。"""
        info = self.task_lookup(task_id)
        task_id = info.task_id
        collab = task_collaboration.shared_store_path(info.dir)
        owner = task_collaboration.get_owner(collab, task_id=task_id)
        if owner is None:
            return False
        return self._readonly_task_kind(info, owner) == "team"

    def context(self, task_id: str, user: dict) -> WorkflowContext:
        info = self.task_lookup(task_id)
        # R6-C3/#35：锁与权限键统一规范 task_id——find_task 会把尾点别名解析到
        # 同一物理任务（Windows 'T.' 与 'T' 同目录），但字符串 id 不同会把
        # get_owner 判成 legacy（owner paused 守卫被绕过）、锁分键（写互斥失效）。
        # 对外路径不再以原始字符串作为权限/锁键；find_task 本身保留别名解析。
        task_id = info.task_id
        collab = task_collaboration.shared_store_path(info.dir)
        owner = task_collaboration.get_owner(collab, task_id=task_id)
        # 门禁墙钟（可见性合并）：属主/协作者判定直接复用已取的 owner——
        # 原先注入的 visible() 会再开 2 个协作库连接重复 get_owner+
        # is_participant（每请求 4 连接 → 2）。owner 为 None 的 legacy 分支
        # 仍交回注入判定（保留 server/desktop 口径与路径检查）。
        if owner is not None:
            if not (owner["owner_user_id"] == user["user_id"]
                    or task_collaboration.is_participant(
                        collab, user["user_id"], task_id=task_id)):
                raise HTTPException(403, "Task access denied.")
        elif not self.visible(info, user):
            raise HTTPException(403, "Task access denied.")
        # Legacy（无属主记录）分支：打开不**新建**数据库；但已存在的库在版本
        # 不齐时会按 #25 补跑 W1 迁移（含备份文件），见下方 legacy 分支。
        actor_db = info.dir / (tasks.slugify_screener(user["username"]) + ".db")
        if owner is None:
            # 2.1.5 终审 #25（裁定：打开即触发）：legacy（无属主记录）任务的
            # 筛选库**已存在**且版本不齐时，与属主/成员分支同一处理——补跑
            # store.migrate + binary_reset.migrate，兑现「升级首次打开即重置」
            # 的设计承诺（design_2.1.5_binary_screening.md §一）。「legacy 只读
            # 不建库」边界保留：库不存在时不建、不迁移（版本探针先查文件）。
            # 幂等：binary_reset.migrate 外层版本判定 + 事务内复检，二跑空操作。
            with self.lock(task_id):
                if actor_db.is_file():
                    legacy_versions = store.migration_state(actor_db)
                    if legacy_versions is None or not {
                            store.SCHEMA_VERSION, binary_reset.RESET_VERSION,
                    } <= legacy_versions:
                        store.migrate(actor_db)
                        binary_reset.migrate(actor_db, task_id=task_id,
                                             actor_id=user["user_id"])
            return WorkflowContext(info, user, None, actor_db, actor_db, False, "legacy", collab)
        with self.lock(task_id):
            owner_db = self.screener_db(info, owner["owner_username"])
            # 基础迁移由 import_sources.migrate 内部首步（_migrate_v4 先跑
            # workflow.migrate）与 coding_evidence.ensure_fields 共同兜底，此处
            # 不再单独调 store.migrate——每次请求省一次只读连接与版本查询。
            from coscreen import import_sources
            # 热路径合并（门禁墙钟）：v1/v4/v5/v3 版本齐全时各 migrate 本就是
            # 无操作——一次只读连接判齐后整段跳过（原先每个 API 请求对同一
            # 库开 4 个只读连接逐个重查）；未建库/旧库走完整链，首建与升级
            # 行为不变。ensure_fields 的字段种子检查不受影响（仍每请求执行）。
            owner_versions = store.migration_state(owner_db)
            if owner_versions is None or not {
                    store.SCHEMA_VERSION, import_sources.VERSION,
                    import_sources.FILENAME_VERSION, coding_evidence.MIGRATION_VERSION,
            } <= owner_versions:
                import_sources.migrate(owner_db)
            coding_evidence.ensure_fields(owner_db)
            grants = task_collaboration.list_role_grants(collab, actor_user_id=owner["owner_user_id"], task_id=task_id)
            if any(grant["user_id"] != owner["owner_user_id"] for grant in grants):
                self.latch_team_db(owner_db)
            kind = "team" if store.settings(owner_db)["team_latched"] else "personal"
            # 属主同人时复用 owner 库路径（省一次 _connect/PRAGMA；同文件
            # 同名，语义不变），否则按成员名建/取个人库
            if actor_db.name == owner_db.name:
                actor_db = owner_db
            else:
                actor_db = self.screener_db(info, user["username"])
            # 同法：actor 库 v1/v8 齐全时跳过重复重查；属主同人时 actor 库
            # 即 owner 库，直接复用 owner 的版本判定与 ensure_fields 结果。
            if actor_db is owner_db or actor_db.name == owner_db.name:
                actor_versions = owner_versions
                if actor_versions is None or not {
                        store.SCHEMA_VERSION, binary_reset.RESET_VERSION,
                } <= actor_versions:
                    store.migrate(actor_db)
                    binary_reset.migrate(actor_db, task_id=task_id,
                                         actor_id=user["user_id"])
            else:
                actor_versions = store.migration_state(actor_db)
                if actor_versions is None or not {
                        store.SCHEMA_VERSION, binary_reset.RESET_VERSION,
                } <= actor_versions:
                    store.migrate(actor_db)
                    binary_reset.migrate(actor_db, task_id=task_id,
                                         actor_id=user["user_id"])
                coding_evidence.ensure_fields(actor_db)
            return WorkflowContext(info, user, owner, owner_db, actor_db,
                                   owner["owner_user_id"] == user["user_id"], kind, collab)

    @staticmethod
    def latch_team_db(db: Path) -> None:
        with closing(store.connect(db)) as conn, conn:
            conn.execute("UPDATE workflow_state SET team_latched=1 WHERE singleton=1 AND team_latched=0")

    def latch_team(self, task_id: str, user: dict) -> None:
        task_id = self.task_lookup(task_id).task_id  # R6-C3：锁键用规范 id
        with self.lock(task_id):
            # context discovers only non-owner grants; a self-grant must not
            # change the task kind. Call before revoking an existing grant.
            self.context(task_id, user)

    def settings(self, ctx: WorkflowContext) -> dict:
        inputs = _team_inputs(ctx)
        if inputs is None:
            data = store.settings(ctx.owner_db)
            sources = store.sources(ctx.owner_db)
            evidence_fields = coding_evidence.field_rows(ctx.owner_db)
            mapping_issues = coding_evidence.mapping(ctx.actor_db, ctx.owner_db)[1]
        else:
            data = store.settings(ctx.owner_db, conn=inputs.owner_conn)
            sources = store.sources(ctx.owner_db, conn=inputs.owner_conn)
            evidence_fields = coding_evidence.field_rows(
                ctx.owner_db, conn=inputs.owner_conn)
            mapping_issues = coding_evidence.mapping(
                ctx.actor_db, ctx.owner_db,
                actor_conn=inputs.actor_conn, owner_conn=inputs.owner_conn)[1]
        # 2.1.5 W1：show_maybe 与轮次标记退役——读取时剥离历史存储值，
        # 载荷不再下发（PUT 端也忽略该键，见 set_settings）。
        stages = {stage: {key: value for key, value in policy.items()
                          if key != "show_maybe"}
                  for stage, policy in data["stages"].items()}
        return {key: data[key] for key in ("version", "evidence_required_field_ids")} | {
            "stages": stages,
            "task_kind": ctx.task_kind, "can_manage": ctx.can_manage, "sources": sources,
            "evidence_fields": [{**d, "dimension_id": d['id']}
                                for d in evidence_fields],
            "mapping_issues": mapping_issues,
            'submission_evidence': self.submission_evidence_capability(ctx)}

    @staticmethod
    def can_submit_coding(ctx: WorkflowContext) -> bool:
        # 2.1.6 FLOW II-A：team 按单一有效业务角色判定（属主不凭管理身份
        # 直通编码提交）；personal/legacy 保持 authorize_action 旧语义——
        # 属主（owner 分支）照旧 True，solo/手工路径零回归。
        if ctx.owner is None:
            return False
        inputs = _team_inputs(ctx)
        if inputs is not None:
            user_id = ctx.user.get("user_id") if isinstance(ctx.user, dict) else None
            role_state = inputs.scope.role_state()
            for entry in role_state.get("participants") or ():
                if entry.get("user_id") != user_id:
                    continue
                return bool(entry.get("role") == "coder"
                            and entry.get("conflict") is not True)
            return False
        if ctx.task_kind == 'team':
            return task_collaboration.has_team_action_role(
                ctx.collaboration_db, user_id=ctx.user['user_id'],
                action='coding:submit', task_id=ctx.info.task_id)
        return bool(task_collaboration.authorize_action(
            ctx.collaboration_db, ctx.user['user_id'], 'coding:submit', task_id=ctx.info.task_id))

    @staticmethod
    def _team_role_http(exc: Exception) -> HTTPException:
        """task_collaboration.TeamRoleGateError -> 结构化 409/403（RoB 同形）。"""
        return HTTPException(exc.status, {
            "error_code": exc.code, "message_key": exc.message_key,
            "params": exc.params, "details": str(exc), "message": str(exc)})

    def submission_evidence_capability(self, ctx: WorkflowContext) -> dict:
        from coscreen.workspace_undo import SUBMISSION_EVIDENCE
        base = f'/api/tasks/{quote(ctx.info.task_id, safe="")}'
        return {'available': self.can_submit_coding(ctx), 'access_context': SUBMISSION_EVIDENCE,
                'queue_url': base + '/coding/submission-evidence',
                'read_url_template': base + '/coding/submission-evidence/{zotero_key}',
                'save_url': base + '/coding/submission-evidence',
                'upload_url': base + '/coding/submission-evidence/uploads',
                'undo_url': base + '/undo/coding'}

    @staticmethod
    def validate_evidence_dimensions(ctx: WorkflowContext, dimensions: set[int]) -> None:
        inputs = _team_inputs(ctx)
        if inputs is None:
            mapped, issues, _ = coding_evidence.mapping(ctx.actor_db, ctx.owner_db)
        else:
            mapped, issues, _ = coding_evidence.mapping(
                ctx.actor_db, ctx.owner_db,
                actor_conn=inputs.actor_conn, owner_conn=inputs.owner_conn)
        if not dimensions <= set(mapped):
            raise HTTPException(409, {'code': 'requirement_mapping_gap',
                'message': 'Evidence fields must map exactly to the current task coding scheme.',
                'dimension_ids': sorted(dimensions - set(mapped)), 'mapping_issues': issues})

    @contextmanager
    def schema_guard(self, task_id: str, user: dict, db: Path):
        task_id = self.task_lookup(task_id).task_id  # R6-C3：锁键用规范 id
        with self.lock(task_id):
            ctx = self.context(task_id, user)
            if ctx.actor_db.resolve() != db.resolve():
                raise HTTPException(403, 'Coding schemes are limited to your own workspace.')
            before = coding_evidence.field_rows(db)
            yield ctx
            coding_evidence.ensure_fields(db)
            if before != coding_evidence.field_rows(db):
                if not ctx.can_manage and ctx.owner is not None:
                    # U4-P3：**团队**成员编辑的是自己的私有 coding scheme，其他
                    # 参与者既不可见也不依赖它——不 bump owner 库 workflow_state
                    # .version（那是 owner 的全局乐观锁），也不向全员广播
                    # coding-scheme，避免一名成员反复让全员的后续写请求 409。
                    # owner 版本只由 owner 级写操作推进。
                    # legacy（owner=None）单机任务不属该口径（#33）：actor 即
                    # 唯一用户，方案编辑恢复 bump（修复前行为），版本随方案
                    # 变化推进、依赖版本号的写互斥保持有效。
                    return
                with closing(store.connect(ctx.owner_db)) as conn, conn:
                    self.bump_version(conn)
                self.notify(ctx, 'coding-scheme')

    def evidence_state(self, ctx: WorkflowContext, key: str) -> dict:
        inputs = _team_inputs(ctx)
        if inputs is None:
            return coding_evidence.evidence_state(
                ctx.actor_db, key, ctx.owner_db, ctx.info.task_id)
        return coding_evidence.evidence_state(
            ctx.actor_db, key, ctx.owner_db, ctx.info.task_id,
            actor_conn=inputs.actor_conn, owner_conn=inputs.owner_conn)

    def coding_result(self, ctx: WorkflowContext, key: str, draft: bool = False) -> dict:
        inputs = _team_inputs(ctx)
        state = self.evidence_state(ctx, key)
        if inputs is None:
            complete = key in coding_evidence.complete_keys(
                ctx.actor_db, ctx.owner_db, [key])
            progress = coding.coding_progress(ctx.actor_db, owner_db=ctx.owner_db)
        else:
            complete = key in coding_evidence.complete_keys(
                ctx.actor_db, ctx.owner_db, [key],
                actor_conn=inputs.actor_conn, owner_conn=inputs.owner_conn)
            from custom_backend.team_screening_runtime import (
                chain_cleared_keys,
                team_chain_view,
            )
            queue = chain_cleared_keys(team_chain_view(inputs.scope, "coding"))
            progress = coding.coding_progress_on(
                inputs.actor_conn,
                owner_conn=inputs.owner_conn,
                actor_db=ctx.actor_db,
                owner_db=ctx.owner_db,
                queue=queue,
            )
        return {'saved': True, 'progress': progress,
                **state, 'complete': complete, 'draft': bool(draft or not complete)}

    def trace_actions(self, task_id: str, user: dict, events: list[dict]) -> None:
        from coscreen import workspace_undo
        from coscreen.research_trace import TraceContext
        task_id = self.task_lookup(task_id).task_id  # R6-C3：锁键/journal 查询用规范 id
        with self.lock(task_id):
            ctx = self.context(task_id, user)
            context = TraceContext(task_id=task_id, actor_id=user['user_id'])
            can_submit = self.can_submit_coding(ctx)
            mapped = coding_evidence.mapping(ctx.actor_db, ctx.owner_db)[0]
            mapped_dimensions = set(mapped)
            actor_fields = {dim['id']: dim for dim in coding_evidence.field_rows(ctx.actor_db)}
            with closing(store.connect(ctx.actor_db, read_only=True)) as conn:
                linked = store.has_table(conn, 'workspace_undo') and 'request_id' in {
                    r[1] for r in conn.execute('PRAGMA table_info(workspace_undo)')}
                for event in events:
                    stage, key = event.get('stage'), event.get('subject_id')
                    imported = bool(event.get('origin_store_id') or event.get('source') == 'import')
                    document_action = event.get('action') in {'baseline_value', 'baseline_rater_revision'} or any(
                        word in event.get('action', '') for word in
                        ('decision', 'coding_value', 'coding_note', 'coding_evidence', 'rater_record'))
                    operation = None
                    local_event = not imported and event.get('partition') == 'personal'
                    if linked and local_event and event.get('source') == 'manual' and event.get('request_id'):
                        operation = conn.execute(
                            'SELECT id,before_json,after_json FROM workspace_undo WHERE task_id=? AND actor_id=? '
                            'AND stage=? AND zotero_key=? AND request_id=? ORDER BY id DESC LIMIT 1',
                            (task_id, str(user['user_id']), stage, key, event['request_id'])).fetchone()
                    # Restorations have an explicit server-assigned link to their original operation.
                    restore_link = str(event.get('source_record_id') or '')
                    if linked and local_event and event.get('source') == 'undo' and restore_link.startswith('workspace_undo:'):
                        identity = restore_link.removeprefix('workspace_undo:')
                        if identity.isdecimal():
                            operation = conn.execute(
                                'SELECT id,before_json,after_json FROM workspace_undo WHERE id=? AND task_id=? '
                                'AND actor_id=? AND stage=? AND zotero_key=?',
                                (int(identity), task_id, str(user['user_id']), stage, key)).fetchone()
                    before = json.loads(operation[1]) if operation else {}
                    after = json.loads(operation[2]) if operation else {}
                    canonical_access = stage == 'coding' and workspace_undo.is_submission_evidence(before, after)
                    own = self.article_row(ctx.actor_db, key) if key else None
                    canonical = self.article_row(ctx.owner_db, key) if key else None
                    canonical_exists = bool(canonical and canonical.get('is_duplicate_of') is None)
                    can_open = bool(not imported and document_action and stage in store.STAGES and (own or canonical_exists))
                    if canonical_access:
                        can_open = can_open and can_submit and canonical_exists
                    if canonical_access and not can_submit:
                        reason = 'coding_submission_access_denied'
                    else:
                        reason = self.writable_reason(ctx, stage, key, canonical_only=canonical_access) if can_open else 'unlinked_subject'
                    if canonical_access and reason is None and not {int(d) for d in after.get('values', {})} <= mapped_dimensions:
                        reason = 'requirement_mapping_gap'
                    access_context = workspace_undo.SUBMISSION_EVIDENCE if canonical_access else 'personal'
                    item_path = ('coding/submission-evidence/' if canonical_access else f'work-items/{stage}/')
                    item_url = f'/api/tasks/{quote(task_id, safe="")}/{item_path}{quote(str(key), safe="")}'
                    actions = {'can_open': can_open, 'can_undo': False, 'undo_entry_id': None,
                               'writable': can_open and reason is None, 'reason': reason or 'no_linked_latest_save',
                               'access_context': access_context, 'work_item_url': item_url if can_open else None}
                    if can_open and operation and event.get('source') == 'manual':
                        last = workspace_undo._latest(conn, context, stage)
                        if last and last['entry_id'] == operation[0]:
                            same = workspace_undo.snapshot_matches(conn, stage, key, before, after)
                            # r6 #26：can_undo 与 undo() 的 MISSING_EVIDENCE_FILE
                            # 拒绝同口径地检查待恢复图片文件存在性——修复前只比
                            # DB 状态，文件被删后轨迹抽屉的 Undo this save 仍可点、
                            # 点击后 409。
                            files_ok = (stage != 'coding' or 'evidence' not in before
                                        or workspace_undo._evidence_files_available(
                                            ctx.actor_db, before['evidence']))
                            actions.update(undo_entry_id=last['entry_id'],
                                           can_undo=reason is None and same and files_ok,
                                           reason=reason or (None if (same and files_ok) else
                                                             ('missing_evidence_file' if same else 'saved_state_changed')))
                    # Trace lifecycle UUIDs and workflow field UUIDs are separate namespaces.
                    # Resolve only this authenticated actor's current, unretired lifecycle.
                    actions['current_field'] = None
                    field = event.get('field') or {}
                    if (can_open and local_event and stage == 'coding' and field.get('field_id')
                            and str(event.get('actor_id')) == str(user['user_id'])
                            and store.has_table(conn, 'trace_field_lifecycles')):
                        lifecycle = conn.execute(
                            'SELECT legacy_field_id FROM trace_field_lifecycles WHERE task_id=? AND actor_id=? '
                            'AND field_uuid=? AND deleted_at IS NULL',
                            (task_id, str(user['user_id']), field['field_id'])).fetchone()
                        local_id = int(lifecycle[0]) if lifecycle and str(lifecycle[0]).isdecimal() else None
                        dimension = actor_fields.get(local_id)
                        historical = {**field, 'dtype': field.get('type')}
                        definition_keys = ('name', 'dtype', 'options', 'multi_select', 'unit', 'description')
                        same_definition = dimension and all(k in historical for k in definition_keys) and (
                            coding_evidence.definition(historical) == coding_evidence.definition(dimension))
                        if same_definition and (not canonical_access or local_id in mapped):
                            actions['current_field'] = {'dimension_id': local_id,
                                'field_id': mapped.get(local_id) if canonical_access else dimension.get('field_id'),
                                'definition': [dimension[k] for k in definition_keys]}
                    event['actions'] = actions

    def notify(self, ctx: WorkflowContext, reason: str) -> None:
        if ctx.owner is None:
            return
        # Recheck participant IDs after commit; events contain no personal work.
        try:
            ids = task_collaboration.list_participant_ids(ctx.collaboration_db,
                actor_user_id=ctx.user["user_id"], task_id=ctx.info.task_id)
        except PermissionError:
            return
        except (task_collaboration.OwnerUnavailableError,
                task_collaboration.CollaborationStoreCorruptError) as exc:
            # R6-C6：读取失败不再折叠成"无参与者"静默丢事件——显式记日志
            import logging
            logging.getLogger(__name__).warning(
                "task %s notify skipped: participant lookup failed: %s",
                ctx.info.task_id, exc)
            return
        event = {"type": "task-workflow-changed", "task_id": ctx.info.task_id,
                 "version": store.settings(ctx.owner_db)["version"], "reason": reason}
        for actor in ids:
            BUS.publish(actor, event)

    def sync_catalog(self, ctx: WorkflowContext) -> None:
        if ctx.owner is not None and not ctx.can_manage:
            from custom_backend.shared_corpus import (ReservedScreenerCollisionError,
                                                      sync_shared_articles)
            try:
                sync_shared_articles(ctx.info.dir, owner_username=ctx.owner["owner_username"],
                                     member_username=ctx.user["username"],
                                     catalog_revision=store.catalog_revision(ctx.owner_db))
            except ReservedScreenerCollisionError:
                # r6 #29：升级前已存在的撞名授权成员的目录同步收敛为 409 撞名
                # 改名指引（修复前裸 ValueError -> work-queue/articles 500）。
                from custom_backend.main import COLLABORATION_RESERVED_NAME_MESSAGE
                raise HTTPException(409, {"error_code": "collaboration_reserved_name",
                                          "message_key": "coding.collaboration_reserved_name", "params": {},
                                          "details": "Username collides with the reserved collaboration database filename",
                                          "message": COLLABORATION_RESERVED_NAME_MESSAGE})

    @staticmethod
    def article_rows(db: Path, *, compact: bool = False,
                     conn: sqlite3.Connection | None = None) -> list[dict]:
        if conn is not None:
            _require_read_conn(conn, api="article_rows")
            return _article_rows_on_conn(conn, compact=compact)
        if not db.is_file():
            return []
        with closing(store.connect(db, read_only=True)) as path_conn:
            return _article_rows_on_conn(path_conn, compact=compact)

    @staticmethod
    def article_row(db: Path, key: str,
                    *, conn: sqlite3.Connection | None = None) -> dict | None:
        if conn is not None:
            _require_read_conn(conn, api="article_row")
            return _article_row_on_conn(conn, key)
        if not db.is_file():
            return None
        with closing(store.connect(db, read_only=True)) as path_conn:
            return _article_row_on_conn(path_conn, key)

    @staticmethod
    def saved_rows(db: Path, stage: str,
                   *, conn: sqlite3.Connection | None = None) -> dict:
        if conn is not None:
            _require_read_conn(conn, api="saved_rows")
            return _saved_rows_on_conn(conn, stage)
        if not db.is_file():
            return {}
        with closing(store.connect(db, read_only=True)) as path_conn:
            return _saved_rows_on_conn(path_conn, stage)

    @staticmethod
    def saved_item(db: Path, stage: str, key: str,
                   *, conn: sqlite3.Connection | None = None) -> dict | None:
        if conn is not None:
            _require_read_conn(conn, api="saved_item")
            return _saved_item_on_conn(conn, stage, key)
        if not db.is_file():
            return None
        with closing(store.connect(db, read_only=True)) as path_conn:
            return _saved_item_on_conn(path_conn, stage, key)

    def eligible(self, ctx: WorkflowContext, stage: str) -> tuple[list[dict], dict]:
        rows = [row for row in self.article_rows(ctx.owner_db, compact=True) if row.get("is_duplicate_of") is None]
        # Owner's per-key title review status is authoritative for every reviewer.
        if ctx.owner_db.is_file():
            with closing(store.connect(ctx.owner_db, read_only=True)) as conn:
                blocked = set()
                if store.has_table(conn, "title_duplicate_members"):
                    blocked = {row[0] for row in conn.execute("SELECT m.zotero_key FROM title_duplicate_members m JOIN title_duplicate_reviews r ON r.review_id=m.review_id WHERE m.imported_in_batch=1 AND r.status IN ('pending','undecided')")}
            rows = [row for row in rows if row["zotero_key"] not in blocked]
        saved = self.saved_rows(ctx.actor_db, stage)
        if stage == "fulltext":
            # 2.1.5 W1：复筛只接收初筛 include；maybe 不再入队（轮次机制移除）。
            initial = self.saved_rows(ctx.actor_db, "screen")
            rows = [row for row in rows
                    if initial.get(row['zotero_key'], {}).get('decision') == 'include']
        elif stage == "coding":
            initial = self.saved_rows(ctx.actor_db, "screen")
            second = self.saved_rows(ctx.actor_db, "fulltext")
            included = {key for key, decision in second.items() if decision["decision"] == "include"}
            if not included:
                included = {key for key, decision in initial.items() if decision["decision"] == "include"
                            and second.get(key, {}).get("decision") != "maybe"}
            rows = [row for row in rows if row["zotero_key"] in included]
        return rows, saved

    def allowed_sources(self, ctx: WorkflowContext, stage: str) -> list[str]:
        inputs = _team_inputs(ctx)
        if inputs is None:
            policy = store.settings(ctx.owner_db)["stages"][stage]
            active = {source["id"] for source in store.sources(ctx.owner_db) if source["active"]}
        else:
            policy = store.settings(
                ctx.owner_db, conn=inputs.owner_conn)["stages"][stage]
            active = {source["id"] for source in store.sources(
                ctx.owner_db, conn=inputs.owner_conn) if source["active"]}
        return sorted(active & set(policy["source_ids"]) if policy["mode"] == "selected" else active)

    def scoped(self, ctx: WorkflowContext, stage: str, rows: list[dict], source_id: str | None = None) -> list[dict]:
        inputs = _team_inputs(ctx)
        allowed = set(self.allowed_sources(ctx, stage))
        if source_id is not None:
            sources = store.sources(
                ctx.owner_db, conn=inputs.owner_conn) if inputs is not None else store.sources(ctx.owner_db)
            if source_id not in {source["id"] for source in sources}:
                raise HTTPException(400, "Unknown source ID.")
            allowed &= {source_id}
        links = (store.source_links(ctx.owner_db, conn=inputs.owner_conn)
                 if inputs is not None else store.source_links(ctx.owner_db))
        return [row for row in rows if links.get(row["zotero_key"], {store.UNASSIGNED}) & allowed]

    def writable_reason(self, ctx: WorkflowContext, stage: str, key: str, *, canonical_only: bool = False) -> str | None:
        if ctx.info.degraded:
            return "task_read_only"
        # P23（同保存门）：team 任务且有可用读取 scope（写窗 write_inputs 或
        # 本请求受保护 team_read_scope）时，资格判定与团队保存门
        # （team_write_checks.require_team_write 的逐键检查）同序同令牌——
        # canonical 域 → paused → source 域 → 链（issue/gate）→ 同阶段完成/
        # raw 历史锁。队列/详情读到的 reason 即随后真实保存 POST 被 409 拒
        # 绝的 code（test_queue_detail_save_reason_agree 锚）。solo/legacy
        # （scope=None）保持既有字节路径，零回归。
        scope = None
        if not canonical_only:
            try:
                scope = _effective_read_scope(ctx)
            except RuntimeError:
                raise
        if scope is not None and ctx.task_kind == "team":
            return self._team_gate_reason(ctx, scope, stage, key)
        if store.settings(ctx.owner_db)["stages"][stage]["mode"] == "paused":
            return "stage_paused"
        row = self.article_row(ctx.owner_db, key)
        if row is None or row.get("is_duplicate_of") is not None:
            return "article_not_eligible"
        with closing(store.connect(ctx.owner_db, read_only=True)) as conn:
            if store.has_table(conn, "title_duplicate_members") and conn.execute(
                    "SELECT 1 FROM title_duplicate_members m JOIN title_duplicate_reviews r ON r.review_id=m.review_id "
                    "WHERE m.zotero_key=? AND m.imported_in_batch=1 AND r.status IN ('pending','undecided') LIMIT 1", (key,)).fetchone():
                return "article_not_eligible"
            links = {entry[0] for entry in conn.execute("SELECT source_id FROM workflow_article_sources WHERE zotero_key=?", (key,))} if store.has_table(conn, "workflow_article_sources") else set()
        if not canonical_only and stage != "screen":
            initial = self.saved_item(ctx.actor_db, "screen", key) or {}
            if stage == "fulltext" and initial.get("decision") != "include":
                return "article_not_eligible"
            if stage == "coding":
                second = self.saved_item(ctx.actor_db, "fulltext", key) or {}
                any_second_include = False
                if ctx.actor_db.is_file():
                    with closing(store.connect(ctx.actor_db, read_only=True)) as conn:
                        if store.has_table(conn, "stage2_decisions"):
                            any_second_include = bool(conn.execute("SELECT 1 FROM stage2_decisions WHERE decision='include' LIMIT 1").fetchone())
                if (any_second_include and second.get("decision") != "include") or (
                        not any_second_include and (initial.get("decision") != "include" or second.get("decision") == "maybe")):
                    return "article_not_eligible"
        if not (links or {store.UNASSIGNED}) & set(self.allowed_sources(ctx, stage)):
            return "article_outside_scope"
        return None

    @contextmanager
    def write_guard(self, task_id: str, user: dict, stage: str, key: str | None = None,
                    expected_version: int | None = None, *, canonical_only: bool = False):
        if stage not in store.STAGES:
            raise HTTPException(400, "Unknown workflow stage.")
        # R6-C3/#22：任务锁键统一规范 task_id（尾点别名与规范 id 互斥），
        # 与撤销/保存路径持同一把锁。
        task_id = self.task_lookup(task_id).task_id
        # Same-process RLock is held through the caller's actual save/commit. It
        # complements SQLite transactions and the preexisting task file lock.
        with self.lock(task_id):
            ctx = self.context(task_id, user)
            state = store.settings(ctx.owner_db)
            if expected_version is not None and state["version"] != expected_version:
                raise HTTPException(409, {"code": "workflow_version_conflict", "version": state["version"],
                                          "workflow_settings": self.settings(ctx)})
            if key is None:
                if not ctx.can_manage:
                    raise HTTPException(403, "Only a verified task owner can manage workflow settings.")
            else:
                if stage == 'coding' and ctx.task_kind == 'team':
                    # fix-r1 R2：team 的**全部** coding 业务写（direct 个人库
                    # 保存、values-batch、canonical 提交证据、coding undo 恢复、
                    # 非 canonical 证据上传）都须当前有效单一 coder——reconciler
                    # （含属主 reconciler）不能借个人库写绕过；拒绝带 team.role.*
                    # 结构化码（409/403）；能力显示仍走 can_submit_coding 布尔口径。
                    try:
                        if not task_collaboration.authorize_team_action(
                                ctx.collaboration_db, ctx.user['user_id'],
                                'coding:submit', task_id=ctx.info.task_id):
                            raise HTTPException(403, "Task coding submission access denied.")
                    except task_collaboration.TeamRoleGateError as exc:
                        raise self._team_role_http(exc) from exc
                elif canonical_only and (stage != 'coding' or not self.can_submit_coding(ctx)):
                    raise HTTPException(403, "Task coding submission access denied.")
                if stage in ('screen', 'fulltext') and ctx.owner is not None:
                    # fix-r1 R5：筛选职责检查移入**实际保存保护窗口**（本
                    # canonical task 锁内）——与角色切换/参与开关/撤销（同持
                    # 此锁的协作管理 API）串行，检查后切角色/关参与不能让在途
                    # 写在切换完成后落盘。外层 _screening_gate（main）保留为
                    # 快失败前置，错误形状与此一致；legacy（owner=None）跳过。
                    self._screening_responsibility_gate(ctx)
                self.sync_catalog(ctx)
                reason = self.writable_reason(ctx, stage, key, canonical_only=canonical_only)
                if reason:
                    raise HTTPException(409, {"code": reason, "version": state["version"],
                                              "workflow_settings": self.settings(ctx)})
            yield ctx

    def _screening_responsibility_gate(self, ctx: WorkflowContext) -> None:
        """fix-r1 R5：锁内筛选职责复查（仅 write_guard key≠None 且
        stage∈{screen,fulltext} 且 ctx.owner 非 None 时调用）。

        - team：单一有效业务角色口径（coder 放行；reconciler 按参与开关；
          未配置/冲突→ team.role.unconfigured/conflict 结构化 409；关参与→
          screening_not_enabled 结构化 403）——与 main._screening_gate 完全
          同形同文案；
        - personal（非 team 有属主）：旧开关口径（纯 reconciler 关闭→同一
          结构化 403），与外层 _screening_gate(personal 分支) 判定一致，零
          新增拒绝面；
        - 基础设施异常：OwnerUnavailableError→503（同 main 文案）；
          CollaborationStoreCorruptError 原样上抛交全局处理器。"""
        user_id = ctx.user['user_id']
        try:
            if ctx.task_kind == 'team':
                permission = task_collaboration.team_screening_permission(
                    ctx.collaboration_db, user_id=user_id, task_id=ctx.info.task_id)
                if permission['allowed']:
                    return
                if permission['reason'] in ('unconfigured', 'conflict'):
                    detail = ("该成员的业务角色尚未配置，请先在成员管理中选择编码者或仲裁者。"
                              if permission['reason'] == 'unconfigured' else
                              "该成员同时持有编码者与仲裁者授权，请先选择单一有效业务角色。")
                    exc_class = (task_collaboration.TeamRoleUnconfigured
                                 if permission['reason'] == 'unconfigured'
                                 else task_collaboration.TeamRoleConflict)
                    raise self._team_role_http(exc_class(
                        detail, params={"participant": user_id}))
                raise HTTPException(403, _SCREENING_NOT_ENABLED_PAYLOAD)
            if not task_collaboration.screening_allowed(
                    ctx.collaboration_db, user_id, task_id=ctx.info.task_id,
                    team=False):
                raise HTTPException(403, _SCREENING_NOT_ENABLED_PAYLOAD)
        except task_collaboration.CollaborationStoreCorruptError:
            # fix-r2 B3：同 _readonly_task_kind（B2）——Corrupt 子类先于父类
            # 按原类型上抛交全局处理器（desktop 409 修库指引 / server 503
            # fail-closed），与 docstring 承诺一致；父类仍映射暂时不可用 503。
            raise
        except task_collaboration.OwnerUnavailableError as exc:
            raise HTTPException(
                503, "筛选参与权状态暂时无法确认，请稍后重试。") from exc

    def check_maybe(self, ctx: WorkflowContext, stage: str, decision: str,
                    key: str | None = None, *, db: Path | None = None) -> None:
        """2.1.5 W1 写闸门：初筛与复筛全程禁止新保存「暂不确定」。

        任务级 ``show_maybe`` 开关已退役（设置载荷不再下发、PUT 忽略）；唯一
        居所是筛选训练（W2，另行实现）。``key``/``db`` 参数保留仅为调用点
        兼容，不再参与判定。撤销/回导不是本闸门路径（恢复既有 maybe 不受
        影响；但存量 maybe 已由一次性迁移重置）。
        """
        if decision != "maybe":
            return
        state = store.settings(ctx.owner_db)
        raise HTTPException(409, {"code": "maybe_disabled",
                                  "message_key": "workflow.maybe_disabled",
                                  "version": state["version"],
                                  "workflow_settings": self.settings(ctx)})

    def coding_complete(self, ctx: WorkflowContext, key: str, values: dict, dimension_ids: set[int]) -> bool:
        """Current owner requirements apply to every actor's current saved evidence."""
        inputs = _team_inputs(ctx)
        if inputs is None:
            return key in coding_evidence.complete_keys(ctx.actor_db, ctx.owner_db, [key])
        return key in coding_evidence.complete_keys(
            ctx.actor_db, ctx.owner_db, [key],
            actor_conn=inputs.actor_conn, owner_conn=inputs.owner_conn)

    def queue(self, task_id: str, user: dict, stage: str, source_id: str | None = None,
              order: str = "import", *, shuffle: bool = False) -> dict:
        task_id = self.task_lookup(task_id).task_id  # R6-C3：锁键用规范 id
        with self.lock(task_id):
            ctx = self.context(task_id, user)
            self.sync_catalog(ctx)
            rows, saved = self.eligible(ctx, stage)
            scoped = self.scoped(ctx, stage, rows, source_id)
            overall = {'total':len(rows),'completed':sum(bool(saved.get(row['zotero_key'])) for row in rows)}
            overall['pending']=overall['total']-overall['completed']
            completed_coding = coding_evidence.complete_keys(ctx.actor_db, ctx.owner_db,
                [row['zotero_key'] for row in rows]) if stage == 'coding' else set()
            def complete(key: str) -> bool:
                return bool(saved.get(key)) if stage != "coding" else key in completed_coding
            def progress(items: list[dict]) -> dict:
                done = sum(complete(row["zotero_key"]) for row in items)
                return {"total": len(items), "completed": done, "pending": len(items) - done}
            keys = [row["zotero_key"] for row in scoped]
            if order == "random" and ctx.owner is not None:
                # UI selection identifies the journal; the current policy only
                # filters keys. Source/policy changes must not reset positions.
                scope = json.dumps({"stage": stage, "source_id": source_id}, sort_keys=True)
                keys = store.random_order(ctx.actor_db, str(user["user_id"]), task_id, scope, keys,
                                          {key for key in keys if not complete(key)}, shuffle=shuffle)
            elif order == "import" and self.queue_order is not None:
                # 2.1.5 W6：复筛队列的主题聚类排序（只换顺序，载荷不变）。
                # 钩子自己兜底（出错返回 None / 原序），这里再校验一次排列，
                # 保证队列绝不因排序失败而丢条目或重复。
                reordered = self.queue_order(ctx, stage, keys)
                if reordered is not None:
                    candidate = list(reordered)
                    if len(candidate) == len(keys) and sorted(candidate) == sorted(keys):
                        keys = candidate
            # 「暂时跳过」沉底分区（2026-11-18 裁定 #3/#4/#5）：主序（导入
            # 序/聚类钩子/随机序）产出最终 keys 之后、构造队列载荷之前做
            # 稳定分区——未跳过文献保持主序在前，跳过文献按 skip_seq 沉
            # 底。不改 random_order()/queue_order 钩子/shuffle 的任何代码
            # 与语义（排序隔离，裁定 #4）；刷新/重排只重排主序，分区在读
            # 取时重新施加，跳过状态是文献级属性、不随队列重置清除
            # （裁定 #5）。无跳过记录=原序（零行为差异）。coding 阶段无
            # 跳过写入，skips 恒为空。
            skips = self.skip_order(ctx, task_id, user, stage) if stage in ("screen", "fulltext") else {}
            if skips:
                in_queue = set(keys)
                keys = ([key for key in keys if key not in skips]
                        + [key for _, key in sorted((seq, key) for key, seq in skips.items()
                                                    if key in in_queue)])
            skipped_pending = sum(1 for key in keys if key in skips and not complete(key))
            by_key = {row["zotero_key"]: row for row in scoped}
            queue = [{"key": key, "zotero_key": key, **{name: by_key[key].get(name) for name in
                      ("title", "authors", "journal", "year", "doi", "item_type")}} for key in keys]
            state = store.settings(ctx.owner_db)
            # 2.1.5 W1：轮次机制移除——不再下发 remaining_uncertain /
            # screening_round（表与历史快照保留，供回滚读取）。
            return {"queue": queue, "total": len(queue), "pending": progress(scoped)["pending"],
                    "skipped_pending": skipped_pending,
                    "scope_progress": progress(scoped), "overall_progress": overall if stage == "screen" else progress(rows),
                    "policy_version": state["version"], "allowed_sources": self.allowed_sources(ctx, stage),
                    "paused": state["stages"][stage]["mode"] == "paused"}

    def skip_order(self, ctx: WorkflowContext, task_id: str, user: dict, stage: str) -> dict[str, int]:
        """读取「暂时跳过」覆盖层：{zotero_key: skip_seq}（2026-11-18 裁定 #3/#4）。

        纯读取合成——跳过表与 workflow_random_order/queue_order 钩子互不
        引用；表不存在（存量 v1 库首次跳过写入前）或库文件不存在 → 空
        dict，沉底分区自然退化为原序（零行为差异）。coding 阶段无跳过
        写入，读到的恒为空。
        """
        if not ctx.actor_db.is_file():
            return {}
        with closing(store.connect(ctx.actor_db, read_only=True)) as conn:
            if not store.has_table(conn, "workflow_skip_order"):
                return {}
            return {row[0]: int(row[1]) for row in conn.execute(
                "SELECT zotero_key,skip_seq FROM workflow_skip_order WHERE actor_id=? AND task_id=? AND stage=?",
                (str(user["user_id"]), task_id, stage))}

    def submission_evidence_queue(self, task_id: str, user: dict, source_id: str | None = None) -> dict:
        task_id = self.task_lookup(task_id).task_id  # R6-C3：锁键用规范 id
        with self.lock(task_id):
            ctx = self.context(task_id, user)
            if not self.can_submit_coding(ctx):
                raise HTTPException(403, 'Task coding submission access denied.')
            self.sync_catalog(ctx)
            # Screen eligibility supplies only current canonical, reviewed catalogue
            # records. Coding source policy applies; actor screening decisions do not.
            canonical, _ = self.eligible(ctx, 'screen')
            rows = self.scoped(ctx, 'coding', canonical)
            scoped = self.scoped(ctx, 'coding', canonical, source_id)
            # Evidence completion uses only this actor's private evidence requirements.
            # It does not read or count personal/shared coding values.
            def progress(targets):
                done = sum(self.evidence_state(ctx, row['zotero_key'])['evidence_complete'] for row in targets)
                return {'total': len(targets), 'completed': done, 'pending': len(targets) - done}
            queue = [{'key': row['zotero_key'], 'zotero_key': row['zotero_key'],
                      **{field: row.get(field) for field in ('title', 'authors', 'journal', 'year', 'doi', 'item_type')}}
                     for row in scoped]
            state = store.settings(ctx.owner_db)
            return {'scope_progress': progress(scoped), 'overall_progress': progress(rows),
                    'allowed_sources': self.allowed_sources(ctx, 'coding'), 'queue': queue, 'total': len(queue), 'policy_version': state['version'],
                    'paused': state['stages']['coding']['mode'] == 'paused',
                    'submission_evidence': self.submission_evidence_capability(ctx)}

    def work_item(self, task_id: str, user: dict, stage: str, key: str, *, submission_evidence: bool = False) -> dict:
        task_id = self.task_lookup(task_id).task_id  # R6-C3：锁键用规范 id
        with self.lock(task_id):
            ctx = self.context(task_id, user)
            if submission_evidence and (stage != 'coding' or not self.can_submit_coding(ctx)):
                raise HTTPException(403, 'Task coding submission access denied.')
            self.sync_catalog(ctx)
            row = self.article_row(ctx.owner_db, key)
            if submission_evidence and (row is None or row.get('is_duplicate_of') is not None):
                raise HTTPException(404, 'Canonical coding article not found.')
            if row is None:
                # An actor's own historical active record remains readable.
                row = self.article_row(ctx.actor_db, key)
            if row is None:
                raise HTTPException(404, "Article not found.")
            reason = self.writable_reason(ctx, stage, key, canonical_only=submission_evidence)
            try:
                raw = json.loads(row.get("raw_json") or "{}")
            except (ValueError, TypeError):
                raw = {}
            article = Article.from_dict({**row, "raw": raw}).to_dict()
            article["raw"] = json.loads(article.pop("raw_json") or "{}")
            submission = {}
            if submission_evidence:
                from coscreen.workspace_undo import SUBMISSION_EVIDENCE
                mapped, _, required = coding_evidence.mapping(ctx.actor_db, ctx.owner_db)
                submission = {'submission_evidence': self.submission_evidence_capability(ctx),
                              'access_context': SUBMISSION_EVIDENCE,
                              'dimensions': [{**dim, 'field_id': mapped.get(dim['id']),
                                              'required': mapped.get(dim['id']) in required,
                                              'writable': dim['id'] in mapped and reason is None}
                                             for dim in coding_evidence.field_rows(ctx.actor_db)]}
            # 2.1.5 W1：正式两阶段全程二选一；maybe 只存在于筛选训练（W2）。
            maybe_allowed = False
            return {"article": article, "saved": self.saved_item(ctx.actor_db, stage, key),
                    **(self.evidence_state(ctx, key) if stage == 'coding' else {}),
                    **submission,
                    "writable": reason is None, "reason": reason,
                    "maybe_allowed": maybe_allowed,
                    "policy_version": store.settings(ctx.owner_db)["version"]}

    # ------------------------------------------------------------------
    # P23（C12 作者侧；2.1.6 team contracts P23 节）：统一 keyed 读服务。
    #
    # 实际 HTTP 队列/详情/计数的同一后端：按键请求 → items_by_key +
    # item_errors + counts（断言锚：两键集并集 == 请求键集）。team 路径走
    # P09 ``protected_keyed_read_scope``（canonical 锁＋collab IMMEDIATE 屏障
    # ＋holder 见证 reader；零持久化），owner 键域/成员票按键窄读、每块
    # ≤200 键（C02），不再全 stage 载入后过滤；逐键资格与团队保存门
    # （require_team_write）同序同令牌。completion/issue 字段只含结论/诊断，
    # 双盲票面与私有 source（路径/登记/世代）永不进入载荷。
    # ------------------------------------------------------------------

    def _team_gate_reason(self, ctx: WorkflowContext, scope, stage: str,
                          key: str) -> str | None:
        """团队逐键保存门同构判定（P23；序/令牌 == require_team_write）。

        canonical 域（article_not_eligible）→ paused（stage_paused）→
        source 域（article_outside_scope）→ 链（issue.reason /
        team_item_gate_reason 令牌）→ 同阶段完成/当前一致/raw 历史锁
        （stage_completed_read_only）。通过返回 None。
        policy 事实（settings/sources/links）与 solo 分支同一只读通道；
        链/完成/issue 事实经受保护 scope 派生（同一锁内屏障快照）。
        """
        from coscreen.team_history_guard import adjudicated_history_keys_on
        from custom_backend.team_screening_runtime import (
            team_chain_view,
            team_item_gate_reason,
            team_stage_view,
        )
        # D-1（复审快修）：degraded→task_read_only 与保存门同位（
        # require_team_write 在 canonical/policy/链之前检查 degraded，409
        # code=task_read_only）——降级团队任务的队列/详情 reason 与真实
        # 保存拒绝同令牌；keyed 服务直调本方法，故检查必须在此处而非仅
        # writable_reason 顶层。
        if getattr(ctx.info, "degraded", False) is True:
            return "task_read_only"
        if key not in scope.canonical_keys():
            return "article_not_eligible"
        state = store.settings(ctx.owner_db)
        if state["stages"][stage]["mode"] == "paused":
            return "stage_paused"
        links = store.source_links(ctx.owner_db).get(key) or {store.UNASSIGNED}
        if not set(links) & set(self.allowed_sources(ctx, stage)):
            return "article_outside_scope"
        target = _STAGE_CHAIN_TARGET[stage]
        if target is not None:
            view = team_chain_view(scope, target, item_keys=(key,))
            issue = view.issues.get(key)
            if issue is not None:
                return issue.reason
            item = view.chain.get(key)
            if item is None:
                return "team_item_upstream_stage1_not_evaluated"
            if item.cleared is not True:
                return team_item_gate_reason(item) or "team_item_unknown"
        open_stage = _STAGE_OPEN_VIEW[stage]
        if open_stage is not None:
            view = team_stage_view(scope, open_stage, item_keys=(key,))
            issue = view.issues.get(key)
            if issue is not None:
                return issue.reason
            if key in view.completion:
                return "stage_completed_read_only"
            protected = adjudicated_history_keys_on(
                scope.collab_connection(), task_id=scope.task_id, stage=stage)
            if key in protected:
                return "stage_completed_read_only"
        return None

    @staticmethod
    def _completion_dto(projection) -> dict | None:
        """CompletionProjection → 载荷投影（无票面、无 source 绑定字段）。"""
        import dataclasses
        if projection is None:
            return None
        return dict(dataclasses.asdict(projection))

    @staticmethod
    def _stage_status_dto(status) -> dict | None:
        """ItemStatus → 状态摘要（P22 同形：名单/完成度诊断，非意见票面）。"""
        if status is None:
            return None
        return {
            "status": status.status,
            "decided_by": status.decided_by,
            "applied_arbitration": status.applied_arbitration,
            "pending_members": list(status.pending_members),
            "pending_reasons": [
                {"member": member, "reason": reason}
                for member, reason in status.pending_reasons],
            "stale_arbitrations": [
                {"record_id": record_id, "reason": reason}
                for record_id, reason in status.stale_arbitrations],
        }

    def _own_vote_rows(self, ctx: WorkflowContext, scope, stage: str,
                       keys) -> dict[str, dict]:
        """当前 actor 自己的按键票摘要（仅本人 decision；peer 票永不进）。"""
        ledger_stage = _STAGE_VOTE_LEDGER.get(stage)
        if ledger_stage is None:
            return {}
        # D-2（复审快修）：本人票账本的 P04 typed 错误在此折叠为
        # TeamFlowUnavailableError（member_db_unreadable）——由
        # _keyed_work_items_team 统一映射 typed 503，与 scope 其余来源错误
        # 同族；VoteLedgerUnavailable 不再以裸 RuntimeError 逃逸成 500。
        from coscreen.team_vote_ledger import VoteLedgerUnavailable
        from custom_backend.team_screening_runtime import (
            TeamFlowUnavailableError,
        )
        user = ctx.user
        slug = tasks.slugify_screener(user["username"])
        rows: dict[str, dict] = {}
        for i in range(0, len(keys), _KEYED_READ_PAGE):
            block = tuple(keys[i:i + _KEYED_READ_PAGE])
            try:
                snap = scope.member_vote_snapshot_keys(
                    int(user["user_id"]), user["username"], slug,
                    ledger_stage, block)
            except VoteLedgerUnavailable as exc:
                raise TeamFlowUnavailableError(
                    "member_db_unreadable", member=user["username"],
                    detail=f"本人票账本按键读取失败: {exc}") from exc
            if snap.absent:
                return {}
            for key, row in snap.rows.items():
                decision = row.get("decision") if isinstance(row, dict) else None
                rows[key] = {"present": True, "decision": decision}
        return rows

    def _compact_articles_on_scope(self, scope, keys) -> dict[str, dict]:
        """owner 分页域文章摘要：经 plan 保留的 holder 见证 reader 按键窄读
        （≤200/块）；无可用保留 reader 的键摘要缺省（gate 事实仍完整）。"""
        plan = scope.keyed_plan()
        if plan is None:
            return {}
        for path in plan.selected_paths:
            conn = scope.witness_connection(path)
            if conn is None:
                continue
            try:
                if not store.has_table(conn, "articles"):
                    continue
                rows: dict[str, dict] = {}
                for i in range(0, len(keys), _KEYED_READ_PAGE):
                    block = list(keys[i:i + _KEYED_READ_PAGE])
                    marks = ",".join("?" for _ in block)
                    found = conn.execute(
                        "SELECT zotero_key,title,authors,journal,year,doi,"
                        f"item_type FROM articles WHERE zotero_key IN ({marks})",
                        block).fetchall()
                    rows.update({row[0]: dict(row) for row in found})
                if rows:
                    return rows
            except sqlite3.Error:
                continue
        return {}

    @staticmethod
    def _keyed_counts(keys, items_by_key, item_errors) -> dict:
        """keyed 计数（P22 锚移植）：completed+writable+blocked_rest 不变式在
        键域全部可判定时成立（known_total=具体整数）；存在不可判定键时
        known_total=None——**不把未知当零**（unknown 键不折进任何计数）。"""
        completed = writable = 0
        for item in items_by_key.values():
            if item.get("completed"):
                completed += 1
            if item.get("writable"):
                writable += 1
        unknown = len(item_errors)
        return {
            "requested": len(keys),
            "known_total": len(keys) if unknown == 0 else None,
            "completed": completed,
            "writable": writable,
            "blocked": len(items_by_key) - writable,
            "unknown": unknown,
        }

    def keyed_work_items(self, task_id: str, user: dict, stage: str,
                         keys) -> dict:
        """P23 统一 keyed 读服务入口（HTTP 路由的唯一后端）。

        team → ``_keyed_work_items_team``（protected read scope；owner 分页
        域；保存门同构 reason）；solo/legacy → ``_keyed_work_items_solo``
        （既有 path 口径按键化，write_inputs/team_read_scope 恒 None——
        solo 手工路径零变化）。返回载荷恒含 ``items_by_key`` /
        ``item_errors`` / ``counts``，两键集互斥且并集 == 规范请求键集。
        """
        from coscreen.team_flow_types import normalize_item_keys
        task_id = self.task_lookup(task_id).task_id  # R6-C3 canonical id
        if stage not in store.STAGES:
            raise HTTPException(400, "Unknown workflow stage.")
        try:
            keys = normalize_item_keys(keys)
        except (TypeError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from exc
        if self.is_team(task_id):
            return self._keyed_work_items_team(task_id, user, stage, keys)
        return self._keyed_work_items_solo(task_id, user, stage, keys)

    def _keyed_work_items_team(self, task_id: str, user: dict, stage: str,
                               keys) -> dict:
        """team 路径：受保护按键只读 scope 内一次派生（零持久化）。"""
        from coscreen.team_flow_types import TeamOperation
        from custom_backend.team_keyed_reads import protected_keyed_read_scope
        from custom_backend.team_screening_runtime import (
            TeamFlowUnavailableError,
            team_chain_view,
            team_stage_view,
        )
        info = self.task_lookup(task_id)
        operation = TeamOperation(name="work_items_read", stage=stage)
        try:
            with protected_keyed_read_scope(
                    self, info, dict(user), operation=operation,
                    item_keys=keys) as scope:
                ctx = self.readonly_context(task_id, user)
                # C08：本请求受保护 scope 绑定到 ctx.team_read_scope；离开
                # 请求（成功/异常）finally 恢复 None——读取 scope 生命周期
                # 不串请求。
                ctx.team_read_scope = scope
                try:
                    return self._keyed_items_from_scope(
                        ctx, scope, stage, keys, team_chain_view,
                        team_stage_view)
                finally:
                    ctx.team_read_scope = None
        except TeamFlowUnavailableError as exc:
            raise HTTPException(getattr(exc, "status", 503), {
                "error_code": getattr(exc, "code", "team.flow.unavailable"),
                "message_key": getattr(exc, "message_key",
                                       "team.flow.unavailable"),
                "params": exc.params,
                "details": str(exc),
                "message": "任务团队读取暂时不可用，请稍后重试。",
            }) from exc

    def _keyed_items_from_scope(self, ctx: WorkflowContext, scope, stage: str,
                                keys, team_chain_view, team_stage_view) -> dict:
        """scope 内逐键分类：域外/issue → item_errors（不连坐其他键）；
        其余 → items_by_key（保存门同构 reason + 完成投影 + 本人票摘要）。"""
        canonical: set[str] = set()
        for i in range(0, len(keys), _KEYED_READ_PAGE):
            canonical.update(scope.keyed_canonical_keys(
                tuple(keys[i:i + _KEYED_READ_PAGE])))
        target = _STAGE_CHAIN_TARGET[stage]
        chain_view = (team_chain_view(scope, target, item_keys=keys)
                      if target is not None else None)
        open_stage = _STAGE_OPEN_VIEW[stage]
        open_view = (team_stage_view(scope, open_stage, item_keys=keys)
                     if open_stage is not None else None)
        own_votes = self._own_vote_rows(ctx, scope, stage, keys)
        articles = self._compact_articles_on_scope(scope, keys)
        items_by_key: dict[str, dict] = {}
        item_errors: dict[str, dict] = {}
        for key in keys:
            if key not in canonical:
                item_errors[key] = {"reason": "key_not_in_domain",
                                    "stage": stage, "source_role": "owner",
                                    "retryable": False}
                continue
            issue = None
            if open_view is not None and key in open_view.issues:
                issue = open_view.issues[key]
            elif chain_view is not None and key in chain_view.issues:
                issue = chain_view.issues[key]
            if issue is not None:
                item_errors[key] = {"reason": issue.reason, "stage": stage,
                                    "source_role": issue.source_role,
                                    "retryable": bool(issue.retryable)}
                continue
            reason = self._team_gate_reason(ctx, scope, stage, key)
            completion = None
            if open_view is not None:
                completion = self._completion_dto(
                    open_view.completion.get(key))
            elif chain_view is not None:
                completion = self._completion_dto(
                    chain_view.completion.get(key))
            items_by_key[key] = {
                "key": key,
                "article": articles.get(key),
                "writable": reason is None,
                "reason": reason,
                "completed": reason == "stage_completed_read_only",
                "saved": own_votes.get(key),
                "completion": completion,
                "stage_status": self._stage_status_dto(
                    open_view.statuses.get(key) if open_view is not None
                    else (chain_view.stage1.statuses.get(key)
                          if chain_view is not None else None)),
            }
        counts = self._keyed_counts(keys, items_by_key, item_errors)
        state = store.settings(ctx.owner_db)
        return {"stage": stage, "task_kind": ctx.task_kind,
                "keys": list(keys), "items_by_key": items_by_key,
                "item_errors": item_errors, "counts": counts,
                "policy_version": state["version"],
                "allowed_sources": self.allowed_sources(ctx, stage),
                "paused": state["stages"][stage]["mode"] == "paused"}

    def _keyed_work_items_solo(self, task_id: str, user: dict, stage: str,
                               keys) -> dict:
        """solo/legacy 路径：既有 path 口径按键化（write_inputs/
        team_read_scope 恒 None；solo 手工上下文零变化）。"""
        with self.lock(task_id):
            ctx = self.context(task_id, user)
            self.sync_catalog(ctx)
            items_by_key: dict[str, dict] = {}
            item_errors: dict[str, dict] = {}
            for key in keys:
                row = self.article_row(ctx.owner_db, key)
                if row is None:
                    # 本人历史活跃行仍可读（与 work_item 同口径）。
                    row = self.article_row(ctx.actor_db, key)
                if row is None:
                    item_errors[key] = {"reason": "article_not_found",
                                        "stage": stage, "source_role": "owner",
                                        "retryable": False}
                    continue
                reason = self.writable_reason(ctx, stage, key)
                saved = self.saved_item(ctx.actor_db, stage, key)
                items_by_key[key] = {
                    "key": key,
                    "article": {name: row.get(name) for name in (
                        "zotero_key", "title", "authors", "journal", "year",
                        "doi", "item_type")},
                    "writable": reason is None, "reason": reason,
                    "completed": bool(saved), "saved": saved,
                    "completion": None, "stage_status": None,
                }
            counts = self._keyed_counts(keys, items_by_key, item_errors)
            state = store.settings(ctx.owner_db)
            return {"stage": stage, "task_kind": ctx.task_kind,
                    "keys": list(keys), "items_by_key": items_by_key,
                    "item_errors": item_errors, "counts": counts,
                    "policy_version": state["version"],
                    "allowed_sources": self.allowed_sources(ctx, stage),
                    "paused": state["stages"][stage]["mode"] == "paused"}

    def bump_version(self, conn: sqlite3.Connection,
                     expected_version: int | None = None,
                     ctx: WorkflowContext | None = None) -> None:
        """推进 owner 库 workflow_state.version（全局乐观锁）。

        sec29（DS·锁与写闸）：``expected_version`` 给定时**条件推进**——版本
        已被并发写推进则 rowcount=0，抛与 write_guard 前置检查同码的 409，
        由调用方 ``with conn`` 事务回滚本次数据写入（先写数据后条件推进，
        失败即整体回滚，提交是原子的）。write_guard 的进程内 RLock 只覆盖
        单进程；跨进程并发写（多 worker/桌面与服务器共用数据目录）的检测
        落到 SQLite 原子条件更新上——前置读版本与提交写之间的窗口不再可穿。
        内部流程（如迁移后同步）不传 ``expected_version``，保持无条件推进。
        """
        if expected_version is None:
            conn.execute("UPDATE workflow_state SET version=version+1 WHERE singleton=1")
            return
        cur = conn.execute(
            "UPDATE workflow_state SET version=version+1 WHERE singleton=1 AND version=?",
            (expected_version,))
        if cur.rowcount == 0:
            row = conn.execute(
                "SELECT version FROM workflow_state WHERE singleton=1").fetchone()
            payload = {"code": "workflow_version_conflict",
                       "version": row[0] if row else expected_version}
            if ctx is not None:
                payload["workflow_settings"] = self.settings(ctx)
            raise HTTPException(409, payload)

    def reason_db(self, ctx: WorkflowContext) -> Path:
        if ctx.task_kind == "personal":
            return self.data_dir / "workflow_accounts" / f"{int(ctx.owner['owner_user_id'])}.sqlite3"
        return ctx.owner_db

    # ------------------------------------------------------------------
    # P15 workflow mutation 适配器（C12 作者侧；2.1.6 team contracts P15 节）
    #
    # 宿主写路由的统一入口：按**可信服务端状态**把一次业务写路由到
    # P13 team 提交协议（``run_team_mutation``）或旧 solo write_guard
    # 路径；team 路径在窗口 yield 内把 ``TeamWriteInputs`` 临时绑到
    # ``ctx.write_inputs``，让既有 ``_team_inputs`` 感知的依赖读取
    # （settings/can_submit_coding/evidence_*）全部走保留连接。
    # 本包不改路由注册（P24 负责最终接线与无漏网检查）。
    # ------------------------------------------------------------------
    #: ``TeamMutationError.code`` 中属请求形状问题的子集（客户端可修正，
    #: 映射 400）；其余为编程级违约，映射 500。
    _REQUEST_SHAPE_ERROR_CODES = frozenset({
        "invalid_request_id", "invalid_request_sha256"})

    def mutate(
        self,
        task_id: str,
        user: dict,
        *,
        operation,
        item_keys,
        request_id: str,
        request_sha256: str,
        writer: "Callable[[WorkflowContext], Mapping[str, object]]",
    ) -> "Mapping[str, object]":
        """P15 写适配入口（typed 结果映射；通知仍由调用方锁外处理）。

        需求对照（P15 节）：

        1. **模式判定只认可信状态**：team 与否由 :meth:`is_team`（锁外
           只读派生的同一 latch/非属主 grant 事实）判定；``operation``
           必须是服务端路由代码选定的 ``TeamOperation`` DTO——客户端
           载荷绝不能充当路由键，缺失/伪造型在此 fail-closed
           （TypeError/ValueError），不可能借伪 mode 进入 team 协议。
           本方法只应被**显式传 operation** 的新路由调用；旧 solo 路由
           继续直接走 write_guard（P24 负责最终无漏网检查）。
        2. **保留连接读取**：team 路径的宿主 writer 收到绑定了
           ``write_inputs`` 的原生 ``WorkflowContext``，yield 内全部依赖
           读取（settings/can_submit_coding/证据合同）经 ``_team_inputs``
           走 retained owner/actor/collab 连接；适配器自身在 yield 内
           不做任何 path-only ensure 或 settings 自连。
        3. **绑定不泄漏**：``ctx.write_inputs`` 只在 writer 存活期内非
           None——成功、拒绝还是异常，离开 writer（乃至整个请求）后必为
           None（finally 恢复 + 窗口关闭后连接已归还，二次绑定显式报错）。
        4. **solo 零回归**：personal/legacy 任务保持原 write_guard 语义
           （``write_inputs`` 恒 None、path-based 读取、既有门与版本
           冲突载荷零变化）。

        返回映射（两路径同形）：``outcome``（'committed' | 'unknown'）、
        ``idempotent``、``request_id``、``result``、``receipt``（team 才
        有，solo 恒 None）、``seals``、``cleanup_errors``，另附
        ``headers``（路由层可直接落响应头：``X-Request-Id`` /
        ``X-Receipt-Id`` / ``X-Idempotent-Replay``）。
        """
        from coscreen.team_flow_types import TeamOperation, normalize_item_keys
        from custom_backend.team_write_checks import team_operation_target

        if not isinstance(operation, TeamOperation):
            # 需求 1：operation 只能由服务端路由代码构造；任何客户端输入
            # （字符串、dict、伪造对象）在此即被拒，不进入任何模式分支。
            raise TypeError(
                "operation must be coscreen.team_flow_types.TeamOperation")
        if not callable(writer):
            raise TypeError("writer must be callable")
        # 未知 name/stage 组合在窗口前拒绝（编程错误，保持 ValueError）。
        team_operation_target(operation)
        keys = normalize_item_keys(item_keys)
        task_id = self.task_lookup(task_id).task_id  # R6-C3 canonical id
        if self.is_team(task_id):
            return self._mutate_team(
                task_id, user, operation=operation, keys=keys,
                request_id=request_id, request_sha256=request_sha256,
                writer=writer)
        return self._mutate_solo(
            task_id, user, operation=operation, keys=keys,
            request_id=request_id, writer=writer)

    def _mutate_team(
        self, task_id: str, user: dict, *, operation, keys,
        request_id: str, request_sha256: str, writer,
    ) -> "Mapping[str, object]":
        """team 路径：P13 提交协议 + 窗口内 ``ctx.write_inputs`` 绑定/恢复。"""
        from collections.abc import Mapping

        from coscreen import task_collaboration
        from coscreen.team_flow_receipts import TeamFlowReceiptConflict
        from custom_backend.team_flow_commit import (
            TeamMutationError,
            run_team_mutation,
        )
        from custom_backend.team_screening_runtime import TeamFlowUnavailableError
        from custom_backend.team_write_checks import TeamWriteBlocked

        info = self.task_lookup(task_id)
        entered = {"writer": False}

        def bound_writer(inputs):
            # 需求 2/3：窗口 yield 内把 retained inputs 绑到 ``inputs.ctx``
            # （P10 窗口构建的同一 WorkflowContext），宿主 writer 收到的
            # 就是原生 ctx；读取经 ``_team_inputs`` 自动走保留连接。
            ctx = inputs.ctx
            if getattr(ctx, "write_inputs", None) is not None:
                raise RuntimeError(
                    "write_inputs already bound for this context")
            ctx.write_inputs = inputs
            entered["writer"] = True
            try:
                return writer(ctx)
            finally:
                # 需求 3 锚：无论 writer 成功、拒绝（异常）还是协议后续
                # 步骤失败，绑定都先于窗口退出恢复；ctx 离开本次请求时
                # ``write_inputs`` 必为 None，不泄漏到下一次请求。
                ctx.write_inputs = None

        try:
            result = run_team_mutation(
                self, info, dict(user),
                operation=operation, item_keys=keys,
                request_id=request_id, request_sha256=request_sha256,
                writer=bound_writer,
                # 主控裁定（P13 复审集成期 checklist）：trace 必须显式传入，
                # 不依赖 P13 的默认值。本适配器上下文没有宿主
                # research_trace 步骤对象——P15 只做模式路由与绑定/恢复，
                # trace 钩子由 P24 最终路由接线时提供，故显式传 None。
                trace=None,
            )
        except TeamWriteBlocked as exc:
            raise self._team_write_blocked_http(task_id, user, exc) from exc
        except task_collaboration.TeamRoleGateError as exc:
            raise self._team_role_http(exc) from exc
        except TeamFlowUnavailableError as exc:
            raise HTTPException(getattr(exc, "status", 503), {
                "error_code": getattr(exc, "code", "team.flow.unavailable"),
                "message_key": getattr(exc, "message_key", "team.flow.unavailable"),
                "params": getattr(exc, "params", {}),
                "details": str(exc), "message": str(exc)}) from exc
        except TeamFlowReceiptConflict as exc:
            raise HTTPException(409, {
                "code": "request_receipt_conflict",
                "message_key": "team.flow.request_receipt_conflict",
                "params": {"request_id": request_id},
                "details": str(exc),
                "message": "该请求编号已有内容不同的成功回执，请更换请求编号重试。",
            }) from exc
        except TeamMutationError as exc:
            status = (400 if exc.code in self._REQUEST_SHAPE_ERROR_CODES
                      else 500)
            raise HTTPException(status, {
                "code": exc.code, "request_id": request_id,
                "details": exc.detail or str(exc),
                "message": "请求参数不合法。" if status == 400 else str(exc),
            }) from exc
        except ValueError as exc:
            if entered["writer"]:
                raise  # writer 自身异常原样上抛（同目标已回滚），不吞不改
            message = str(exc)
            # 窗口在 yield 前抛出的「不是 team 任务」信号（writer 从未运
            # 行、零业务写）：is_team 判定与开窗之间任务模式漂移。按 409
            # 让客户端重试——重试将按当前可信状态重新路由，无双写风险。
            # P15-D1（P24 一并落地）：捕获面收窄——只有这两条开窗处的
            # 模式漂移字面信号（team_screening_write.py 的
            # retained_team_write_inputs 稳定契约文本）才映射 409；其余
            # pre-yield ValueError（_verify_owner_domain 的客户端坏键/域
            # 外键、normalize_item_keys 的键形状拒绝）是请求自身的确定性
            # 错误，同请求重试永不成功——修复前误标 409 task_mode_changed
            # 造成「409 徒劳重试」；现如实 400 结构化客户端错误。
            if ("requires a team task" in message
                    or "canonical task lookup returned no task_id" in message):
                raise HTTPException(409, {
                    "code": "task_mode_changed",
                    "message_key": "team.flow.task_mode_changed",
                    "params": {"task_id": task_id},
                    "details": message,
                    "message": "任务协作模式已变化，请重试本次操作。",
                }) from exc
            raise HTTPException(400, {
                "code": "team_flow_invalid_item_keys",
                "message_key": "team.flow.invalid_item_keys",
                "params": {"task_id": task_id},
                "details": message,
                "message": "请求键域不在本任务的可写域内（未知/重复/待题录键）。",
            }) from exc
        # receipt 头（P09/C09）：路由层（P24 接线）可直接使用的回执头。
        headers = {
            "X-Request-Id": request_id,
            "X-Idempotent-Replay": "true" if result.get("idempotent") else "false",
        }
        receipt = result.get("receipt")
        receipt_id = (receipt.get("receipt_id")
                      if isinstance(receipt, Mapping) else None)
        if receipt_id:
            headers["X-Receipt-Id"] = str(receipt_id)
        return {**result, "headers": headers}

    def _mutate_solo(
        self, task_id: str, user: dict, *, operation, keys, request_id: str,
        writer,
    ) -> "Mapping[str, object]":
        """solo/legacy 路径（需求 4）：原 write_guard，默认语义零变化。

        ``ctx.write_inputs`` 恒 None（write_guard 从不绑定）——依赖读取
        保持 path-based 旧口径；既有角色门/筛选职责门/版本冲突载荷与
        拒绝形状完全不变。solo 业务写历来单键（决策/编码/证据保存），
        多键请求在此显式 400，绝不静默换语义。
        """
        from collections.abc import Mapping

        if len(keys) != 1:
            raise HTTPException(400, "Solo mutations accept exactly one item key.")
        with self.write_guard(task_id, user, operation.stage, keys[0],
                              operation.expected_version) as ctx:
            result = writer(ctx)
        if not isinstance(result, Mapping):
            raise HTTPException(500, "writer must return a Mapping.")
        return {"outcome": "committed", "idempotent": False,
                "request_id": request_id, "result": dict(result),
                "receipt": None, "seals": (), "cleanup_errors": (),
                "headers": {"X-Request-Id": request_id,
                            "X-Idempotent-Replay": "false"}}

    def _team_write_blocked_http(
        self, task_id: str, user: dict, exc: Exception,
    ) -> HTTPException:
        """``TeamWriteBlocked``（P12）→ 结构化 409/403，保持既有拒绝载荷形状。

        旧 write_guard 拒绝载荷为 ``{"code", "version", "workflow_settings"}``；
        P12 携带 ``code/message_key/params/stage/item_keys/version``。此处
        合成两者（``code`` 键名与旧形状一致；i18n 键与触发键整批给出）。
        ``workflow_settings`` 仅在 P12 读到版本时回填：窗口已关闭、目标
        已回滚，只能按当前事实 fresh 重读（``readonly_context``+settings，
        零建库零迁移的 path 口径）；回读自身失败不吞原始业务拒绝（保持
        无 settings 的载荷）。
        """
        detail: dict = {"code": exc.code, "stage": exc.stage,
                        "item_keys": list(exc.item_keys),
                        "details": str(exc), "message": str(exc)}
        if exc.message_key is not None:
            detail["message_key"] = exc.message_key
            detail["params"] = dict(exc.params)
        if exc.version is not None:
            detail["version"] = exc.version
            try:
                with self.lock(task_id):
                    detail["workflow_settings"] = self.settings(
                        self.readonly_context(task_id, user))
            except (HTTPException, sqlite3.Error, OSError):
                pass  # 回显失败不吞业务拒绝
        return HTTPException(exc.status, detail)


class SettingsIn(BaseModel):
    expected_version: int = Field(ge=1)
    stages: dict
    evidence_required_field_ids: list[str] = Field(default_factory=list, max_length=1000)


class SourceIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    active: bool = True
    expected_version: int | None = Field(default=None, ge=1)
    source_type: Literal['database', 'register', 'website', 'organisation', 'citation_searching', 'other'] | None = None


class ReasonIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    stage: Literal["screen", "fulltext"] | None = None


class SkipIn(BaseModel):
    """「暂时跳过」端点请求体（正式筛选；zotero_key 约束与 DecisionIn 一致）。"""
    zotero_key: str = Field(min_length=1, max_length=300)


class KeyedItemsIn(BaseModel):
    """P23 统一 keyed 读服务请求体（队列/详情/计数同一入口）。

    不新增任意接受域上限（C02）：服务端按 200 键/块分页派生。
    """
    zotero_keys: list[str] = Field(min_length=1)


def register_review_workflow_routes(app, require_user, task_lookup, screener_db, data_dir, visible,
                                    queue_order=None) -> WorkflowRuntime:
    runtime = WorkflowRuntime(task_lookup, screener_db, data_dir, visible,
                              queue_order=queue_order)
    app.state.review_workflow = runtime

    @app.get("/api/tasks/{task_id}/workflow-settings")
    def settings(task_id: str, user: dict = Depends(require_user)) -> dict:
        return runtime.settings(runtime.context(task_id, user))

    @app.put("/api/tasks/{task_id}/workflow-settings")
    def set_settings(task_id: str, body: SettingsIn, user: dict = Depends(require_user)) -> dict:
        with runtime.write_guard(task_id, user, "screen", expected_version=body.expected_version) as ctx:
            valid = {source["id"] for source in store.sources(ctx.owner_db)}
            stages = {}
            if set(body.stages) != set(store.STAGES):
                raise HTTPException(400, "Supply all three workflow stages.")
            for stage in store.STAGES:
                raw = body.stages[stage]
                if not isinstance(raw, dict) or raw.get("mode") not in {"all", "selected", "paused"}:
                    raise HTTPException(400, "Invalid workflow mode.")
                ids = raw.get("source_ids", [])
                if not isinstance(ids, list) or any(not isinstance(sid, str) for sid in ids) or not set(ids) <= valid:
                    raise HTTPException(400, "Invalid workflow source IDs.")
                if raw["mode"] == "selected" and not ids:
                    raise HTTPException(400, "Selected mode requires at least one source.")
                stages[stage] = {"mode": raw["mode"], "source_ids": sorted(set(ids))}
                # 2.1.5 W1：show_maybe 退役——旧前端携带该键时静默忽略
                # （不 422、不落盘），不再参与任何读写语义。
            field_ids = body.evidence_required_field_ids
            dimensions = {dim['field_id']: dim for dim in coding_evidence.field_rows(ctx.owner_db)}
            if any(fid not in dimensions for fid in field_ids):
                raise HTTPException(400, "Required evidence fields must exist in the task coding scheme.")
            with closing(store.connect(ctx.owner_db)) as conn, conn:
                conn.execute("UPDATE workflow_state SET stages_json=?,evidence_json=? WHERE singleton=1",
                             (json.dumps(stages), json.dumps(sorted(set(field_ids)))))
                conn.execute('DELETE FROM workflow_evidence_requirements')
                conn.executemany('INSERT INTO workflow_evidence_requirements VALUES(?,?)',
                                 [(fid, coding_evidence.definition(dimensions[fid])) for fid in set(field_ids)])
                # sec29：条件推进——跨进程并发写在提交时原子检出（409 回滚
                # 本事务的全部写入），不再依赖前置读版本的 TOCTOU 窗口。
                runtime.bump_version(conn, expected_version=body.expected_version, ctx=ctx)
            runtime.notify(ctx, "settings")
            return runtime.settings(ctx)

    @app.get("/api/tasks/{task_id}/sources")
    def sources(task_id: str, user: dict = Depends(require_user)) -> dict:
        ctx = runtime.context(task_id, user)
        return {"sources": store.sources(ctx.owner_db), "can_manage": ctx.can_manage,
                "version": store.settings(ctx.owner_db)["version"]}

    def save_source(task_id: str, user: dict, body: SourceIn, source_id: str | None) -> dict:
        with runtime.write_guard(task_id, user, "screen", expected_version=body.expected_version) as ctx:
            text, norm = body.name.strip(), store.normalized(body.name)
            if not norm:
                raise HTTPException(400, "Source name is required.")
            if source_id == store.UNASSIGNED:
                raise HTTPException(400, "Unassigned is a reserved source.")
            sid = source_id or str(uuid.uuid4())
            try:
                with closing(store.connect(ctx.owner_db)) as conn, conn:
                    if source_id:
                        if not conn.execute("SELECT 1 FROM workflow_sources WHERE id=?", (sid,)).fetchone():
                            raise HTTPException(404, "Source not found.")
                        detail = conn.execute('SELECT identity FROM workflow_source_details WHERE source_id=?',(sid,)).fetchone()
                        if detail and detail[0].startswith('filename:'):
                            filename = conn.execute('SELECT name FROM workflow_sources WHERE id=?',(sid,)).fetchone()[0]
                            if text != filename:
                                raise HTTPException(400, 'Imported source names follow their filenames.')
                        conn.execute("UPDATE workflow_sources SET name=?,normalized_name=?,active=? WHERE id=?", (text, norm, body.active, sid))
                        if body.source_type is not None:
                            conn.execute('''INSERT INTO workflow_source_details VALUES(?, 'confirmed', ?, '', '', '{}', ?)
                                ON CONFLICT(source_id) DO UPDATE SET identification='confirmed',source_type=excluded.source_type''',
                                         (sid, body.source_type, 'confirmed:' + sid))
                    else:
                        conn.execute("INSERT INTO workflow_sources VALUES(?,?,?,?,datetime('now'))", (sid, text, norm, body.active))
                    # sec29：同 settings PUT——expected_version 给定时条件推进
                    # （SourceIn 可不传，None 走无条件路径保持旧行为）。
                    runtime.bump_version(conn, expected_version=body.expected_version, ctx=ctx)
            except sqlite3.IntegrityError as exc:
                raise HTTPException(409, "A source with this name already exists.") from exc
            runtime.notify(ctx, "source")
            # sec29b（复审#5，存量）：孤儿来源被 import_sources.decorate 过滤
            # 后 next() 无默认值 → StopIteration 兜成 500；改 404（写入已提交，
            # 仅取回展示失败——与列表不可见口径一致）。
            found_source = next(
                (source for source in store.sources(ctx.owner_db)
                 if source["id"] == sid), None)
            if found_source is None:
                raise HTTPException(404, "Source not found.")
            return {"source": found_source,
                    "version": store.settings(ctx.owner_db)["version"]}

    @app.post("/api/tasks/{task_id}/sources", status_code=201)
    def create_source(task_id: str, body: SourceIn, user: dict = Depends(require_user)) -> dict:
        with runtime.write_guard(task_id,user,'screen',expected_version=body.expected_version):
            raise HTTPException(400, 'Import sources are created by importing files.')

    @app.put("/api/tasks/{task_id}/sources/{source_id}")
    def update_source(task_id: str, source_id: str, body: SourceIn, user: dict = Depends(require_user)) -> dict:
        return save_source(task_id, user, body, source_id)

    @app.get("/api/tasks/{task_id}/reason-library")
    def reasons(task_id: str, stage: Literal["screen", "fulltext"], user: dict = Depends(require_user)) -> dict:
        ctx = runtime.context(task_id, user)
        path = runtime.reason_db(ctx)
        entries = []
        if path.is_file():
            with closing(store.connect(path, read_only=True)) as conn:
                if store.has_table(conn, "workflow_reasons"):
                    entries = [dict(row) for row in conn.execute("SELECT id,text,stage FROM workflow_reasons WHERE stage=? ORDER BY rowid", (stage,))]
        return {"entries": entries, "can_manage": ctx.can_manage, "task_kind": ctx.task_kind}

    def edit_reason(task_id: str, user: dict, body: ReasonIn | None, reason_id: str | None, stage: str | None) -> dict:
        with runtime.write_guard(task_id, user, "screen") as ctx:
            path = runtime.reason_db(ctx)
            store.migrate(path)
            sid = reason_id or str(uuid.uuid4())
            try:
                with closing(store.connect(path)) as conn, conn:
                    old = conn.execute("SELECT stage FROM workflow_reasons WHERE id=?", (sid,)).fetchone() if reason_id else None
                    if reason_id and old is None:
                        raise HTTPException(404, "Reason not found.")
                    if body is None:
                        conn.execute("DELETE FROM workflow_reasons WHERE id=?", (sid,))
                    else:
                        text, norm = body.text.strip(), store.normalized(body.text)
                        if not norm:
                            raise HTTPException(400, "Reason text is required.")
                        selected_stage = old[0] if old else stage or body.stage
                        if selected_stage not in {"screen", "fulltext"}:
                            raise HTTPException(400, "Select a reason stage.")
                        conn.execute("INSERT INTO workflow_reasons VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET text=excluded.text,normalized_text=excluded.normalized_text",
                                     (sid, selected_stage, text, norm))
            except sqlite3.IntegrityError as exc:
                raise HTTPException(409, "This reason already exists in the stage library.") from exc
            if ctx.task_kind == "team":
                runtime.notify(ctx, "reason-library")
            return {"id": sid, "deleted": body is None}

    @app.post("/api/tasks/{task_id}/reason-library", status_code=201)
    def create_reason(task_id: str, body: ReasonIn, stage: Literal["screen", "fulltext"] | None = None, user: dict = Depends(require_user)) -> dict:
        return edit_reason(task_id, user, body, None, stage)

    @app.put("/api/tasks/{task_id}/reason-library/{reason_id}")
    def update_reason(task_id: str, reason_id: str, body: ReasonIn, user: dict = Depends(require_user)) -> dict:
        return edit_reason(task_id, user, body, reason_id, None)

    @app.delete("/api/tasks/{task_id}/reason-library/{reason_id}")
    def delete_reason(task_id: str, reason_id: str, user: dict = Depends(require_user)) -> dict:
        return edit_reason(task_id, user, None, reason_id, None)

    @app.get("/api/tasks/{task_id}/work-queue/{stage}")
    def queue(task_id: str, stage: Stage, source_id: str | None = None, order: Literal["import", "random"] = "import", user: dict = Depends(require_user)) -> dict:
        return runtime.queue(task_id, user, stage, source_id, order)

    @app.post("/api/tasks/{task_id}/work-queue/screen/shuffle")
    def shuffle(task_id: str, source_id: str | None = None, user: dict = Depends(require_user)) -> dict:
        return runtime.queue(task_id, user, "screen", source_id, "random", shuffle=True)

    @app.post("/api/tasks/{task_id}/work-queue/{stage}/skip")
    def skip_queue_item(task_id: str, stage: Literal["screen", "fulltext"], body: SkipIn,
                        user: dict = Depends(require_user)) -> dict:
        """「暂时跳过」（2026-11-18 裁定 #1-#8，正式筛选侧）。

        权限/锁/上下文与决策保存同路径：write_guard（canonical task 锁 +
        context + 筛选职责门 + sync_catalog + writable_reason——与
        main.save_decision/save_stage2_decision 的保存保护窗口同一方
        法）。不写决策表，只写跳过覆盖层（裁定 #2/#7）；返回当前阶段
        （全部允许来源，与 work-queue 默认口径一致）的
        {pending, skipped_pending} 供前端推进与完成门引导（裁定 #6）。
        已决策文献 → 结构化 409（带 pending/skipped_pending 计数）。
        """
        with runtime.write_guard(task_id, user, stage, body.zotero_key) as ctx:
            rows, saved = runtime.eligible(ctx, stage)
            scoped = runtime.scoped(ctx, stage, rows)

            def counts(skips: dict[str, int]) -> tuple[int, int]:
                pending = sum(1 for row in scoped if not saved.get(row["zotero_key"]))
                skipped = sum(1 for row in scoped if row["zotero_key"] in skips
                              and not saved.get(row["zotero_key"]))
                return pending, skipped
            try:
                store.skip_queue_item(ctx.actor_db, str(user["user_id"]),
                                      ctx.info.task_id, stage, body.zotero_key)
            except store.SkipDecisionExistsError as exc:
                pending, skipped_pending = counts(
                    runtime.skip_order(ctx, ctx.info.task_id, user, stage))
                raise HTTPException(409, {
                    "code": "skip_decision_exists",
                    "message_key": "workflow.skip_decision_exists",
                    "params": {"zotero_key": body.zotero_key},
                    "pending": pending, "skipped_pending": skipped_pending,
                    "zotero_key": body.zotero_key, "stage": stage,
                    "details": str(exc),
                    "message": "该文献已有纳入/排除决策，无需跳过；如需重看请先撤销该决策。",
                }) from exc
            pending, skipped_pending = counts(
                runtime.skip_order(ctx, ctx.info.task_id, user, stage))
        return {"pending": pending, "skipped_pending": skipped_pending}

    @app.get("/api/tasks/{task_id}/work-items/{stage}/{key}")
    def work_item(task_id: str, stage: Stage, key: str, user: dict = Depends(require_user)) -> dict:
        return runtime.work_item(task_id, user, stage, key)

    @app.post("/api/tasks/{task_id}/work-items/{stage}/keyed")
    def work_items_keyed(task_id: str, stage: Stage, body: KeyedItemsIn,
                         user: dict = Depends(require_user)) -> dict:
        """P23 统一 keyed 读服务（队列/详情/计数同一后端；只读可部分成功：
        A 可用/B 域外或来源坏 → 200 + items_by_key{A} + item_errors{B}）。"""
        return runtime.keyed_work_items(task_id, user, stage, body.zotero_keys)

    return runtime
