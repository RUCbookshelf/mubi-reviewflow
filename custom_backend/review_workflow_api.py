"""Task workflow policies and queues using identities verified by the host."""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import quote

from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field

from coscreen import binary_reset, coding, coding_evidence, review_workflow as store, task_collaboration, tasks
from coscreen.models import Article
from custom_backend.events import BUS

Stage = Literal["screen", "fulltext", "coding"]


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
                               owner["owner_user_id"] == user["user_id"], "personal", collab)

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
        # Legacy reads must not initialize databases or create backup files.
        actor_db = info.dir / (tasks.slugify_screener(user["username"]) + ".db")
        if owner is None:
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
        data = store.settings(ctx.owner_db)
        # 2.1.5 W1：show_maybe 与轮次标记退役——读取时剥离历史存储值，
        # 载荷不再下发（PUT 端也忽略该键，见 set_settings）。
        stages = {stage: {key: value for key, value in policy.items()
                          if key != "show_maybe"}
                  for stage, policy in data["stages"].items()}
        return {key: data[key] for key in ("version", "evidence_required_field_ids")} | {
            "stages": stages,
            "task_kind": ctx.task_kind, "can_manage": ctx.can_manage, "sources": store.sources(ctx.owner_db),
            "evidence_fields": [{**d, "dimension_id": d['id']}
                                for d in coding_evidence.field_rows(ctx.owner_db)],
            "mapping_issues": coding_evidence.mapping(ctx.actor_db, ctx.owner_db)[1],
            'submission_evidence': self.submission_evidence_capability(ctx)}

    @staticmethod
    def can_submit_coding(ctx: WorkflowContext) -> bool:
        return bool(ctx.owner is not None and task_collaboration.authorize_action(
            ctx.collaboration_db, ctx.user['user_id'], 'coding:submit', task_id=ctx.info.task_id))

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
        mapped, issues, _ = coding_evidence.mapping(ctx.actor_db, ctx.owner_db)
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
        return coding_evidence.evidence_state(ctx.actor_db, key, ctx.owner_db, ctx.info.task_id)

    def coding_result(self, ctx: WorkflowContext, key: str, draft: bool = False) -> dict:
        state = self.evidence_state(ctx, key)
        complete = key in coding_evidence.complete_keys(ctx.actor_db, ctx.owner_db, [key])
        return {'saved': True, 'progress': coding.coding_progress(ctx.actor_db, owner_db=ctx.owner_db),
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
    def article_rows(db: Path, *, compact: bool = False) -> list[dict]:
        if not db.is_file():
            return []
        with closing(store.connect(db, read_only=True)) as conn:
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

    @staticmethod
    def article_row(db: Path, key: str) -> dict | None:
        if not db.is_file():
            return None
        with closing(store.connect(db, read_only=True)) as conn:
            if not store.has_table(conn, "articles"):
                return None
            row = conn.execute("SELECT * FROM articles WHERE zotero_key=?", (key,)).fetchone()
            return dict(row) if row else None

    @staticmethod
    def saved_rows(db: Path, stage: str) -> dict:
        table = {"screen": "decisions", "fulltext": "stage2_decisions", "coding": "coding_values"}[stage]
        if not db.is_file():
            return {}
        with closing(store.connect(db, read_only=True)) as conn:
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

    @staticmethod
    def saved_item(db: Path, stage: str, key: str) -> dict | None:
        table = {"screen": "decisions", "fulltext": "stage2_decisions", "coding": "coding_values"}[stage]
        if not db.is_file():
            return None
        with closing(store.connect(db, read_only=True)) as conn:
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
        policy = store.settings(ctx.owner_db)["stages"][stage]
        active = {source["id"] for source in store.sources(ctx.owner_db) if source["active"]}
        return sorted(active & set(policy["source_ids"]) if policy["mode"] == "selected" else active)

    def scoped(self, ctx: WorkflowContext, stage: str, rows: list[dict], source_id: str | None = None) -> list[dict]:
        allowed = set(self.allowed_sources(ctx, stage))
        if source_id is not None:
            if source_id not in {source["id"] for source in store.sources(ctx.owner_db)}:
                raise HTTPException(400, "Unknown source ID.")
            allowed &= {source_id}
        links = store.source_links(ctx.owner_db)
        return [row for row in rows if links.get(row["zotero_key"], {store.UNASSIGNED}) & allowed]

    def writable_reason(self, ctx: WorkflowContext, stage: str, key: str, *, canonical_only: bool = False) -> str | None:
        if ctx.info.degraded:
            return "task_read_only"
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
                if canonical_only and (stage != 'coding' or not self.can_submit_coding(ctx)):
                    raise HTTPException(403, "Task coding submission access denied.")
                self.sync_catalog(ctx)
                reason = self.writable_reason(ctx, stage, key, canonical_only=canonical_only)
                if reason:
                    raise HTTPException(409, {"code": reason, "version": state["version"],
                                              "workflow_settings": self.settings(ctx)})
            yield ctx

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
        return key in coding_evidence.complete_keys(ctx.actor_db, ctx.owner_db, [key])

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
            by_key = {row["zotero_key"]: row for row in scoped}
            queue = [{"key": key, "zotero_key": key, **{name: by_key[key].get(name) for name in
                      ("title", "authors", "journal", "year", "doi", "item_type")}} for key in keys]
            state = store.settings(ctx.owner_db)
            # 2.1.5 W1：轮次机制移除——不再下发 remaining_uncertain /
            # screening_round（表与历史快照保留，供回滚读取）。
            return {"queue": queue, "total": len(queue), "pending": progress(scoped)["pending"],
                    "scope_progress": progress(scoped), "overall_progress": overall if stage == "screen" else progress(rows),
                    "policy_version": state["version"], "allowed_sources": self.allowed_sources(ctx, stage),
                    "paused": state["stages"][stage]["mode"] == "paused"}

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

    @staticmethod
    def bump_version(conn: sqlite3.Connection) -> None:
        conn.execute("UPDATE workflow_state SET version=version+1 WHERE singleton=1")

    def reason_db(self, ctx: WorkflowContext) -> Path:
        if ctx.task_kind == "personal":
            return self.data_dir / "workflow_accounts" / f"{int(ctx.owner['owner_user_id'])}.sqlite3"
        return ctx.owner_db


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
                runtime.bump_version(conn)
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
                    runtime.bump_version(conn)
            except sqlite3.IntegrityError as exc:
                raise HTTPException(409, "A source with this name already exists.") from exc
            runtime.notify(ctx, "source")
            return {"source": next(source for source in store.sources(ctx.owner_db) if source["id"] == sid), "version": store.settings(ctx.owner_db)["version"]}

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

    @app.get("/api/tasks/{task_id}/work-items/{stage}/{key}")
    def work_item(task_id: str, stage: Stage, key: str, user: dict = Depends(require_user)) -> dict:
        return runtime.work_item(task_id, user, stage, key)

    return runtime
