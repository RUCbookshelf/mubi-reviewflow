# -*- coding: utf-8 -*-
"""S2-B2a retained team write inputs（team_screening_write 内部输入窗口）合同测试。

合同来源（只读引用；产品源冻结，本文件仅修复 fixture 与观察质量）：
- .superpowers/sdd/2026-11-14-2.1.6-maintenance/s2b-input-window-brief.md（固定内部 API）
- .superpowers/sdd/2026-11-14-2.1.6-maintenance/s2b-resource-brief.md（B1 精确接口）
- out/maintenance216/team_flow_integration_design.md §13–16（冻结窗口顺序/失败语义）
- out/maintenance216/s2b_resource_20261115/B2_SEAMS_CC.txt（真实接缝清单）
- 主控/独审 2026-11-15 同轮补充裁定（R1）：
  1) actor 保留名 / 跨 uid hardlink / actor↔collab、actor↔accounts 物理别名
     必须在 runtime.context / runtime.sync_catalog 可能迁移**之前**拒绝
     （spy 两调用次数==0；错库 schema/行/字节前后不变；预检查短 ro+stat，
     最终 borrowed 仍核——成功路径 scope 断言覆盖"最终仍核"）。
  2) 缺 collab / 现存绑定 unconfirmed 必须 typed（TeamFlowUnavailableError 族，
     不裸 ValueError）；reserve owner/users BEGIN busy 分别映射
     owner_db_unreadable / accounts_unreadable（不全标 member），原 busy 异常
     对象经 __cause__/__context__ 链保留（不依赖新私有 helper 名，含
     B1 failed_path 只读诊断）。
  3) marker 漂移完整释放时若已产生 cleanup_errors 则不得再 sync/重试；
     业务 commit 成功后的 cleanup 错误仍算成功。
  4) 正常路径正控：正常 owner actor / 正常非 owner 现存 actor / 首用合法
     actor（宿主 context 既有准备建库）必须能到 yield 并实际 commit——
     拒绝面测试不得掩盖"正常路径全拒"回归；已知已移除账号残留 .db 与当前
     actor 库同物理（hardlink）时，只 stat/不打开的别名检查必须在
     context/sync 前拒绝（残留库不打开、不迁移、不恢复进票源）。

诚实边界：
- 本文件作者只做 AST/compile 静态冻结；不运行模块/SQL/pytest/unittest
  （主控统一箱验证）。运行需外部 launcher 提供 sys.path。
- 执行面（裁定 F4）：pytest 下经 build/tests/conftest.py 运行
  （``python -m pytest build/tests/test_s2b_team_write_inputs.py``，解释器
  需 -I -B + 临时 TEMP/TMP 与 COBOOKSHELF_DATA_DIR）；或 bare unittest
  launcher（sys.path.insert(0,'.') + importlib 载入 + TextTestRunner）。
  注意：conftest.py 的 pytest_configure 有意先 ``import custom_backend.main``
  （冻结 DATA_DIR 到会话根的防护合同），故 pytest 会话内
  custom_backend.auth/main 必然在 sys.modules——module_import 测试因此
  只做增量快照断言，这是执行面事实，不是产品回归信号。
- 顶层零产品导入、零环境改写、零 sys.path 改写；产品导入仅在 setUpClass
  执行；首次导入前六项绝对隔离环境由外部 launcher 保证，setUp 才创建
  每例 TemporaryDirectory 并注册 cleanup。缓存依赖下二次 import 的观察
  不代表首次冷启动零连接；完整冷导入由外部安全 adapter 单独验证。
- fake runtime 只提供宿主 seam（task_lookup/lock/context/readonly_context/
  sync_catalog/notify，全部真实观察记录；context 事实经真实 collab/owner 库
  现读派生；仅首用 actor 正控显式启用真实准备建库），不替代 SQLite/身份/锁/TSR：
  库是真实临时 SQLite，
  锁是真实 threading.RLock，身份经真实 initialize_task_store/grant_role/
  init_db/migrate 准备，TSR borrowed scope 为真实对象。
- 只钉冻结 reason 令牌与 brief 明文结构（catalog_sync_stale 三键）；
  不钉异常文案、不固定产品方法数、不依赖未定私有 helper 名。
- busy 用例按 B1 默认 busy timeout 真实等待（每例约 15s）。
"""
from __future__ import annotations

import dataclasses
import importlib
import inspect
import os
import sqlite3
import sys
import tempfile
import threading
import unittest
import urllib.parse
from contextlib import closing, contextmanager
from pathlib import Path
from unittest import mock

MODULE_NAME = "custom_backend.team_screening_write"

#: P10 合同驱动同步新增导入（operation/item_keys 新参数的默认构造）。
from coscreen.team_flow_types import TeamOperation  # noqa: E402


class _InjectedFailure(Exception):
    """注入故障的独立标记类型（验证异常对象身份原样传播）。"""


def _norm_target(target):
    """sqlite3.connect 首参 → (normcase 路径标签, URI 模式标记)。"""
    if isinstance(target, str) and target.startswith("file:"):
        raw = urllib.parse.unquote(urllib.parse.urlparse(target).path)
        if len(raw) > 2 and raw[0] == "/" and raw[2] == ":":
            raw = raw[1:]
        mode = "ro" if "mode=ro" in target else ("rw" if "mode=rw" in target else "")
        return os.path.normcase(raw), mode
    return os.path.normcase(os.fspath(target)), ""


def _label_of(path):
    return os.path.normcase(str(Path(path)))


class _ConnectControl:
    """全局 sqlite3.connect 观察 + 窄故障注入（真实 native Connection 子类）。

    - opens: [(label, mode)] 全部打开记录（URI mode=ro/rw 识别）。
    - boundaries: 每次真实 B1 reserve 成功返回时的 opens 长度快照；
      hidden_opens 观察各次 reserve 返回至清理结束，包含 select_target 之前。
    - 故障：commit_failure 仅保留连接；fail_close_labels 在 reserve 成功后
      绑定确切保留连接，绝不先命中 preflight/context 的短 ro 连接。
    - label_conns: label → [connection]，供路径↔连接映射断言。
    """

    def __init__(self):
        self.opens = []
        self.close_order = []
        self.commit_calls = []
        self.boundaries = []
        self.label_conns = {}
        self.attempt_starts = []
        self.reservations = []
        self.reservation_ends = {}
        self.hidden_opens = []
        self.active_reservation = None
        self.close_targets = set()
        self.close_faults = []
        self.begin_failures = []
        self.reserve_failures = []
        self.selected_targets = []
        self.commit_failure = None
        self.close_failure = _InjectedFailure("controlled close failure")
        self.fail_close_labels = ()
        self.raw_connect = None

    @contextmanager
    def installed(self, reservation_module):
        control = self
        original_connect = sqlite3.connect
        control.raw_connect = original_connect

        class ControlledConnection(sqlite3.Connection):
            def execute(self, sql, *args, **kwargs):
                try:
                    return super().execute(sql, *args, **kwargs)
                except sqlite3.OperationalError as exc:
                    if (sql.strip().upper() == "BEGIN IMMEDIATE"
                            and getattr(self, "_ctl_mode", "") == "rw"):
                        control.begin_failures.append((self, self._ctl_label, exc))
                    raise

            def commit(self):
                control.commit_calls.append(getattr(self, "_ctl_label", "?"))
                if (control.commit_failure is not None
                        and getattr(self, "_ctl_reserved", False)):
                    failure = control.commit_failure
                    control.commit_failure = None
                    raise failure
                return super().commit()

            def close(self):
                label = getattr(self, "_ctl_label", "?")
                control.close_order.append(label)
                if id(self) in control.close_targets:
                    control.close_targets.remove(id(self))
                    control.close_faults.append((self, label))
                    raise control.close_failure
                return super().close()

        def controlled_connect(*args, **kwargs):
            kwargs["factory"] = ControlledConnection
            connection = original_connect(*args, **kwargs)
            label, mode = _norm_target(args[0] if args else kwargs.get("database"))
            connection._ctl_label = label
            connection._ctl_mode = mode
            connection._ctl_reserved = False
            control.opens.append((label, mode))
            if control.active_reservation is not None:
                control.hidden_opens.append((control.active_reservation, label, mode))
            control.label_conns.setdefault(label, []).append(connection)
            return connection

        session_cls = reservation_module.ReservedWriteConnections
        original_open_collab = session_cls._open_collab
        original_reserve = session_cls.reserve
        original_cleanup = session_cls._cleanup
        original_select_target = session_cls.select_target

        def spied_open_collab(reserved):
            control.attempt_starts.append(len(control.opens))
            return original_open_collab(reserved)

        def spied_reserve(reserved, owner_path, other_paths):
            try:
                result = original_reserve(reserved, owner_path, other_paths)
            except BaseException as exc:
                control.reserve_failures.append((reserved, exc))
                raise
            control.reservations.append(reserved)
            control.boundaries.append(len(control.opens))
            control.active_reservation = len(control.reservations) - 1
            for connection in reserved.connections:
                connection._ctl_reserved = True
                if connection._ctl_label in control.fail_close_labels:
                    control.close_targets.add(id(connection))
            return result

        def spied_cleanup(reserved, **kwargs):
            try:
                return original_cleanup(reserved, **kwargs)
            finally:
                active = control.active_reservation
                if active is not None and control.reservations[active] is reserved:
                    control.reservation_ends[active] = len(control.opens)
                    control.active_reservation = None

        def spied_select_target(reserved, path):
            result = original_select_target(reserved, path)
            control.selected_targets.append(reserved.target_conn)
            return result

        try:
            with mock.patch("sqlite3.connect", controlled_connect), \
                    mock.patch.object(session_cls, "_open_collab", spied_open_collab), \
                    mock.patch.object(session_cls, "reserve", spied_reserve), \
                    mock.patch.object(session_cls, "_cleanup", spied_cleanup), \
                    mock.patch.object(session_cls, "select_target", spied_select_target):
                yield control
        finally:
            # A reported close failure deliberately leaves a real handle open.
            # Close only injected failures, without hiding other product leaks.
            for connection, _label in control.close_faults:
                sqlite3.Connection.close(connection)

    # -- 派生观察 -----------------------------------------------------------

    def labels(self):
        return {label for label, _mode in self.opens}

    def modes_for(self, label):
        return [mode for entry_label, mode in self.opens if entry_label == label]

    def conn_for(self, path):
        opened = self.label_conns.get(_label_of(path))
        return opened[-1] if opened else None

    def opens_after_last_boundary(self):
        if not self.boundaries:
            return []
        last = len(self.boundaries) - 1
        end = self.reservation_ends.get(last, len(self.opens))
        return self.opens[self.boundaries[-1]:end]


class _FakeRuntime:
    """最小宿主 runtime：只提供 seam 与观察，不替代 SQLite/身份/锁/TSR。

    context/readonly_context 的事实（owner/task_kind/路径）经真实 collab 与
    owner 库现读派生；通常由夹具建库，首用 actor 可显式启用真实准备。
    sync_catalog 对 owner/can_manage 为宿主 no-op，非 owner 默认真实
    shared_corpus.sync_shared_articles，可编程 noop（marker 漂移注入）。
    """

    def __init__(self, registry, data_dir):
        self.data_dir = Path(data_dir)
        self._registry = registry
        self._locks = {}
        self._locks_guard = threading.Lock()
        self.events = []
        self.lookups = []
        self.lock_requests = []
        self.context_calls = 0
        self.readonly_calls = 0
        self.sync_calls = 0       # method invocations, including host no-op
        self.actual_sync_calls = 0
        self.notify_calls = 0
        self.sync_mode = "real"   # 'real' | 'noop' | 'noop_once_then_real'
        #: True 时模拟真实宿主 context 的既有 owner/actor 准备（首用 actor
        #: 库经真实 init_db+migrate 建立）；缺省 False（库由夹具预置）。
        self.prepare_actor = False

    def task_lookup(self, task_id):
        self.lookups.append(str(task_id))
        self.events.append(("lookup", str(task_id)))
        key = str(task_id)
        if key.endswith("."):
            key = key[:-1]
        return self._registry[key]

    def lock(self, task_id):
        self.lock_requests.append(str(task_id))
        self.events.append(("lock", str(task_id)))
        with self._locks_guard:
            return self._locks.setdefault(str(task_id), threading.RLock())

    def _facts(self, task_id, user):
        info = self.task_lookup(task_id)
        task_id = info.task_id
        collab_path = collab_mod.shared_store_path(info.dir)
        owner = collab_mod.get_owner(collab_path, task_id=task_id)
        actor_db = info.dir / (tasks_mod.slugify_screener(user["username"]) + ".db")
        if owner is None:
            return (info, dict(user), None, actor_db, actor_db, False,
                    "legacy", collab_path)
        owner_db = info.dir / (tasks_mod.slugify_screener(
            owner["owner_username"]) + ".db")
        latched = bool(wf_mod.settings(owner_db)["team_latched"]) \
            if owner_db.is_file() else False
        grants = collab_mod.has_non_owner_grants(
            collab_path, task_id=task_id,
            owner_user_id=int(owner["owner_user_id"]))
        kind = "team" if (latched or grants) else "personal"
        can_manage = int(owner["owner_user_id"]) == int(user["user_id"])
        actor = owner_db if actor_db.name == owner_db.name else actor_db
        return (info, dict(user), dict(owner), owner_db, actor,
                can_manage, kind, collab_path)

    def context(self, task_id, user):
        self.context_calls += 1
        self.events.append(("context",))
        facts = self._facts(task_id, user)
        if self.prepare_actor and facts[2] is not None:
            actor_db = Path(facts[4])
            if not actor_db.is_file():
                dbm.init_db(actor_db)
                wf_mod.migrate(actor_db)
        return review_workflow_api.WorkflowContext(*facts)

    def readonly_context(self, task_id, user):
        self.readonly_calls += 1
        self.events.append(("readonly_context",))
        return review_workflow_api.WorkflowContext(*self._facts(task_id, user))

    def sync_catalog(self, ctx):
        self.sync_calls += 1
        self.events.append(("sync_catalog",))
        # Faithful WorkflowRuntime host seam: owner/can_manage never syncs itself.
        if ctx.owner is None or ctx.can_manage:
            return
        if self.sync_mode == "noop":
            return
        if self.sync_mode == "noop_once_then_real" and self.sync_calls == 1:
            return
        self.actual_sync_calls += 1
        self.events.append(("actual_sync",))
        shared_corpus.sync_shared_articles(
            ctx.info.dir,
            owner_username=ctx.owner["owner_username"],
            member_username=ctx.user["username"],
            catalog_revision=wf_mod.catalog_revision(ctx.owner_db))

    def notify(self, ctx, reason):
        self.notify_calls += 1
        self.events.append(("notify", reason))


#: 产品句柄在 setUpClass 填充；导入前隔离环境由外部 launcher 保证。
tws = b1 = collab_mod = tasks_mod = wf_mod = dbm = tsr = shared_corpus = None
review_workflow_api = None


class TeamWriteInputsWindowTests(unittest.TestCase):
    """retained_team_write_inputs / TeamWriteInputs 固定内部 API 行为合同。"""

    maxDiff = None

    @classmethod
    def setUpClass(cls):
        global tws, b1, collab_mod, tasks_mod, wf_mod, dbm, tsr, shared_corpus
        global review_workflow_api
        tws = importlib.import_module(MODULE_NAME)
        b1 = importlib.import_module("coscreen.team_write_reservation")
        collab_mod = importlib.import_module("coscreen.task_collaboration")
        tasks_mod = importlib.import_module("coscreen.tasks")
        wf_mod = importlib.import_module("coscreen.review_workflow")
        dbm = importlib.import_module("coscreen.db")
        tsr = importlib.import_module("custom_backend.team_screening_runtime")
        shared_corpus = importlib.import_module("custom_backend.shared_corpus")
        review_workflow_api = importlib.import_module(
            "custom_backend.review_workflow_api")
        for module in (tws, b1, collab_mod, tasks_mod, wf_mod, dbm, tsr,
                       shared_corpus, review_workflow_api):
            source = Path(module.__file__).resolve()
            assert "site-packages" not in str(source).lower(), (
                "product import escaped the repository",
                module.__name__, str(source))

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="s2b-twi-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.data_dir = self.root / "data"
        self.tasks_root = self.data_dir / "tasks"
        self.data_dir.mkdir(parents=True)
        self.users_db = self._make_users_db(("alice", "bob", "carol", "dave",
                                             "Dave", "collaboration"))
        self._current_target_kind = "actor"
        self._last_control = None

    # -- 夹具（真实准备 API；全部落临时根） ---------------------------------

    def _make_users_db(self, names):
        """真实 users 表形状的账号库（auth schema 同形直插种子；不经 auth）。"""
        path = self.data_dir / "users.db"
        conn = sqlite3.connect(path)
        try:
            conn.execute(
                "CREATE TABLE users ("
                " id INTEGER PRIMARY KEY AUTOINCREMENT,"
                " username TEXT NOT NULL UNIQUE,"
                " password_hash TEXT NOT NULL,"
                " created_at TEXT NOT NULL)")
            for name in names:
                conn.execute(
                    "INSERT INTO users (username, password_hash, created_at)"
                    " VALUES (?, ?, ?)",
                    (name, "fixture-not-a-real-hash",
                     "2026-11-15T00:00:00+00:00"))
            conn.commit()
        finally:
            conn.close()
        uids = {}
        with closing(sqlite3.connect(path)) as conn:
            for uid, name in conn.execute("SELECT id, username FROM users"):
                uids[str(name)] = int(uid)
        self.uids = uids
        return path

    def _world(self, *, task_id, owner_username="alice", grants=(),
               owner_articles=("K1", "K2"), owner_votes=None, latch=True,
               member_dbs=("bob",), member_votes=None, actor=None):
        """真实协作库+属主库+成员库世界；返回窗口入参组与路径句柄。"""
        task_dir = self.tasks_root / task_id
        task_dir.mkdir(parents=True, exist_ok=True)
        collab_path = self._init_collab(task_dir, task_id, owner_username)
        for username, role in grants:
            self._grant(collab_path, task_id, username, role)
        owner_db = self._screening_db(
            task_dir / (tasks_mod.slugify_screener(owner_username) + ".db"),
            articles=owner_articles, votes=owner_votes, latch=latch)
        # 裁定 F1：owner 库就位后补产品同款两步（prepare+register）。
        self._prepare_team_protocol(collab_path, task_id=task_id,
                                    owner_user_id=self.uids[owner_username],
                                    owner_db=owner_db)
        for username in member_dbs:
            self._screening_db(
                task_dir / (tasks_mod.slugify_screener(username) + ".db"),
                votes=(member_votes or {}).get(username, {}))
        registry = {task_id: tasks_mod.TaskInfo(
            task_id=task_id, dir=task_dir, display_name=task_id)}
        runtime = _FakeRuntime(registry, self.data_dir)
        actor_name = actor or owner_username
        user = {"user_id": self.uids[actor_name], "username": actor_name}
        info = tasks_mod.TaskInfo(task_id=task_id + ".", dir=task_dir,
                                  display_name="alias-probe")
        return task_dir, runtime, info, user, owner_db, collab_path

    def _init_collab(self, task_dir, task_id, owner_username):
        collab_mod.initialize_task_store(
            task_dir, task_id=task_id,
            owner_user_id=self.uids[owner_username],
            owner_username=owner_username)
        return task_dir / "collaboration.db"

    def _prepare_team_protocol(self, collab_path, *, task_id, owner_user_id,
                               owner_db):
        """P14 产品同款建队两步（裁定 F1；字段口径照 main.py:3313
        _register_fresh_team_protocol 的调用方式，借用产品函数，零手写 DDL）：

        ① team_source_lifecycle.prepare_team_protocol_storage：collab 装
        F2 registry+receipts+F1 facts+业务角色两表（幂等）；
        ② coscreen.team_terminal_registry.register_protocol_on：以 owner
        个人库的真实 P04 ledger 身份登记 protocol 行
        （creation_path="trusted_new_task" → coverage_state=new_covered）。
        真实流程建队必经此两步，写窗计划入口强依赖该布局；缺②计划报
        task_not_registered，缺①报 registry_unavailable（均先降级为
        completion_source_unresolved，测不到窗口本体）。
        """
        from custom_backend import team_source_lifecycle as team_lifecycle
        from coscreen import team_terminal_registry as registry_mod
        from coscreen.team_vote_ledger import library_identity_header_on
        collab_path = Path(collab_path)
        team_lifecycle.prepare_team_protocol_storage(
            collab_path=collab_path, source_paths=())
        with closing(sqlite3.connect(str(owner_db), timeout=15)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("BEGIN")
            header = library_identity_header_on(conn)
        owner_library = header.library_id if header is not None else None
        self.assertTrue(owner_library,
                        "夹具 owner 库必须已装真实 P04 ledger 身份")
        with closing(sqlite3.connect(str(collab_path), timeout=15)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("BEGIN IMMEDIATE")
            registry_mod.register_protocol_on(
                conn, task_id=task_id,
                task_instance_id=f"{task_id}-inst-fixture",
                owner_user_id=int(owner_user_id),
                owner_library_id=owner_library,
                creation_path="trusted_new_task")
            conn.commit()

    def _grant(self, collab_path, task_id, username, role):
        collab_mod.grant_role(collab_path, actor_user_id=self.uids["alice"],
                              user_id=self.uids[username], role=role,
                              task_id=task_id)

    def _screening_db(self, path, *, articles=(), votes=None, latch=False):
        """真实 init_db/migrate/ledger；先种文章，再在显式 FK 下种票。"""
        votes = {} if votes is None else votes
        article_keys = tuple(dict.fromkeys((*articles, *votes)))
        dbm.init_db(path)
        wf_mod.migrate(path)
        conn = sqlite3.connect(path)
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            self.assertEqual(conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            for order, key in enumerate(article_keys):
                conn.execute(
                    "INSERT INTO articles (zotero_key, title, is_duplicate_of,"
                    " import_order) VALUES (?, ?, NULL, ?)",
                    (key, "title-" + key, order))
            for key, (decision, updated, screened) in votes.items():
                conn.execute(
                    "INSERT INTO decisions (zotero_key, decision,"
                    " exclusion_reason, notes, tags, screened_at, updated_at)"
                    " VALUES (?, ?, '', '', '', ?, ?)",
                    (key, decision, screened, updated))
            if latch:
                conn.execute(
                    "UPDATE workflow_state SET team_latched=1 WHERE singleton=1")
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [],
                             "fixture 文章/票必须在真实 FK 与 ledger 下合法")
            conn.commit()
        finally:
            conn.close()
        return path

    # -- 观察与断言 helper ---------------------------------------------------

    def _observe(self):
        """观察真实连接和每次 B1 reserve 成功返回至清理结束的边界。"""
        return _ConnectControl().installed(b1)

    def _window(self, runtime, info, user, *, target_kind, operation=None,
                item_keys=None):
        """P10 合同驱动同步（2.1.6 单次 reserve 写窗）：窗口签名新增必填
        keyword-only ``operation``/``item_keys``——旧行形态（无二者）在 P10
        产品下 TypeError＝预期。默认值保持 27 个既有调用点零改动：
        screen 域＋各 world 夹具 owner_articles 恒含的 "K1"。"""
        if operation is None:
            operation = TeamOperation(name="s2b-window-default",
                                      stage="screen")
        if item_keys is None:
            item_keys = ("K1",)
        return tws.retained_team_write_inputs(
            runtime, info, user, target_kind=target_kind,
            operation=operation, item_keys=item_keys)

    def _bytes_of(self, paths):
        return {Path(path): Path(path).read_bytes() for path in paths}

    def _assert_bytes_unchanged(self, before):
        for path, data in before.items():
            self.assertTrue(path.is_file(), f"被拒绝的库不得被删除: {path}")
            self.assertEqual(path.read_bytes(), data,
                             f"拒绝路径不得改写字节: {path}")

    def _listing(self, directory):
        base = Path(directory)
        if not base.is_dir():
            return []
        return sorted(str(p.relative_to(base)) for p in base.rglob("*"))

    def _raw_connect(self, path):
        control = self._last_control
        connect = control.raw_connect if control is not None else sqlite3.connect
        return connect(str(path), timeout=0)

    def _probe_table(self, external):
        return {row[0] for row in external.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}

    def _assert_released(self, paths):
        """窗口退出后全部输入可被外部正常 SQLite 写（锁真实释放；no-op UPDATE）。"""
        for path in paths:
            path = Path(path)
            if not path.is_file():
                continue
            external = self._raw_connect(path)
            try:
                tables = self._probe_table(external)
                if "workflow_state" in tables:
                    external.execute("UPDATE workflow_state SET"
                                     " team_latched=team_latched WHERE singleton=1")
                elif "collaboration_task" in tables:
                    external.execute("UPDATE collaboration_task SET"
                                     " created_at=created_at WHERE singleton=1")
                elif "users" in tables:
                    external.execute("UPDATE users SET password_hash=password_hash")
                else:
                    self.fail(f"release probe: no known table in {path}")
                external.commit()
            finally:
                external.close()

    def _assert_external_write_locked(self, control, path):
        """窗口体内：该输入库必须阻挡外部正常 SQLite 写（BEGIN IMMEDIATE 在持）。"""
        external = control.raw_connect(str(path), timeout=0)
        try:
            tables = self._probe_table(external)
            if "workflow_state" in tables:
                statement = ("UPDATE workflow_state SET team_latched=team_latched"
                             " WHERE singleton=1")
            elif "collaboration_task" in tables:
                statement = ("UPDATE collaboration_task SET created_at=created_at"
                             " WHERE singleton=1")
            elif "users" in tables:
                statement = "UPDATE users SET password_hash=password_hash"
            else:
                self.fail(f"lock probe: no known table in {path}")
            with self.assertRaises(sqlite3.OperationalError,
                                   msg=f"窗口体内必须锁住 {Path(path).name}"):
                external.execute(statement)
        finally:
            external.close()

    def _lock_probe(self, runtime, task_id):
        """独立线程对规范 id 锁做非阻塞 acquire；返回是否可获取。"""
        result = {}

        def probe():
            lock = runtime.lock(task_id)
            acquired = lock.acquire(blocking=False)
            result["acquired"] = acquired
            if acquired:
                lock.release()

        thread = threading.Thread(target=probe)
        thread.start()
        thread.join(10)
        self.assertFalse(thread.is_alive(), "lock probe thread stuck")
        return result["acquired"]

    def _assert_connection_types(self, inputs):
        self.assertIsInstance(inputs.collab_conn, sqlite3.Connection)
        self.assertIsInstance(inputs.owner_conn, sqlite3.Connection)
        self.assertIsInstance(inputs.actor_conn, sqlite3.Connection)
        self.assertIsInstance(inputs.target_conn, sqlite3.Connection)
        self.assertIsInstance(inputs.connections, tuple)
        self.assertTrue(inputs.connections)
        for conn in inputs.connections:
            self.assertIsInstance(conn, sqlite3.Connection)
            self.assertTrue(conn.in_transaction,
                            "窗口体内全部保留连接必须已持 B1 写事务")
        for conn in (inputs.collab_conn, inputs.owner_conn,
                     inputs.actor_conn, inputs.target_conn):
            self.assertIn(conn, inputs.connections)
        expected_target = inputs.collab_conn if self._current_target_kind == "collab" \
            else inputs.actor_conn
        self.assertIs(inputs.target_conn, expected_target)
        self.assertIsInstance(inputs.scope, tsr.TeamReadScope)
        self.assertIsInstance(inputs.roster, tuple)
        for entry in inputs.roster:
            self.assertIsInstance(entry, dict)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            inputs.target_conn = None

    def _assert_no_journal_left(self, task_dir):
        leftovers = [name for name in self._listing(task_dir)
                     if name.endswith("-journal") or name.endswith("-wal")
                     or name.endswith("-shm")]
        self.assertEqual(leftovers, [], "不得残留 journal/wal 文件")

    @staticmethod
    def _structured_catalog_sync_stale(exc):
        candidates = []
        detail = getattr(exc, "detail", None)
        if isinstance(detail, dict):
            candidates.append(detail)
        for arg in getattr(exc, "args", ()):
            if isinstance(arg, dict):
                candidates.append(arg)
        attrs = {key: getattr(exc, key) for key in
                 ("code", "message_key", "params") if hasattr(exc, key)}
        if len(attrs) == 3:
            candidates.append(attrs)
        for candidate in candidates:
            if candidate.get("code") == "catalog_sync_stale":
                return candidate
        return None

    @staticmethod
    def _chain_has(exc, expected):
        """Identity in either exception-chain branch, never merely a matching type."""
        seen = set()
        pending = [exc]
        while pending:
            current = pending.pop()
            if current is None or id(current) in seen:
                continue
            seen.add(id(current))
            if current is expected:
                return True
            pending.extend((current.__cause__, current.__context__))
        return False

    def _assert_reservation_phases(self, control, *, users_db, collab_path,
                                   attempts):
        """Keep preflight distinct from each locked enumeration/reserve attempt."""
        self.assertEqual(len(control.attempt_starts), attempts)
        self.assertEqual(len(control.boundaries), attempts)
        self.assertEqual(len(control.reservations), attempts)
        users_label = _label_of(users_db)
        collab_label = _label_of(collab_path)
        prefix = control.opens[:control.attempt_starts[0]]
        preflight_users = [mode for label, mode in prefix if label == users_label]
        self.assertTrue(preflight_users, "prepare 前必须只读核账户")
        self.assertTrue(all(mode == "ro" for mode in preflight_users))
        for attempt, (start, boundary) in enumerate(zip(
                control.attempt_starts, control.boundaries)):
            with self.subTest(reservation_attempt=attempt):
                phase = control.opens[start:boundary]
                self.assertEqual([mode for label, mode in phase
                                  if label == users_label], ["ro", "rw"],
                                 "每次 collab 锁内独立枚举账户后再保留账户")
                self.assertEqual([mode for label, mode in phase
                                  if label == collab_label], ["rw"],
                                 "每次尝试重新取得真实 B1 collab 写锁")
                if attempt:
                    self.assertLessEqual(control.reservation_ends[attempt - 1], start,
                                         "前次保留清理结束后才可重试")
        self.assertEqual(control.hidden_opens, [],
                         "所有 reserve 成功返回至清理结束（含选目标前）零重连")

    # -- 固定接口形状 ---------------------------------------------------------

    def test_fixed_internal_api_shape(self):
        helper = getattr(tws.retained_team_write_inputs, "__wrapped__",
                         tws.retained_team_write_inputs)
        signature = inspect.signature(helper)
        # P10 合同驱动同步：签名追加 keyword-only operation/item_keys；
        # P2（2026-11-21 缺口二根治，survey §5.3-1 明文）：追加 keyword-only
        # extra_reserve_paths（退休归档目标成员库进保留集，读活票判据零改动）。
        self.assertEqual(list(signature.parameters),
                         ["runtime", "info", "user", "target_kind",
                          "operation", "item_keys", "extra_reserve_paths"])
        self.assertIs(signature.parameters["target_kind"].kind,
                      inspect.Parameter.KEYWORD_ONLY,
                      "target_kind 必须为 keyword-only（服务端枚举，禁客户端来源）")
        self.assertIs(signature.parameters["operation"].kind,
                      inspect.Parameter.KEYWORD_ONLY,
                      "operation 必须为 keyword-only（服务端选择的业务操作）")
        self.assertIs(signature.parameters["item_keys"].kind,
                      inspect.Parameter.KEYWORD_ONLY,
                      "item_keys 必须为 keyword-only（规范化请求键域）")
        # P10 合同驱动同步：TeamWriteInputs 追加 operation/item_keys/plan。
        fields = [field.name for field in dataclasses.fields(tws.TeamWriteInputs)
                  if not field.name.startswith("_")]
        # P2（2026-11-21）：dataclass 追加 retirement_extra_paths（归档目标
        # 保留库）/retirement_missing_paths（缺失不炸窗口的观测面）。
        self.assertEqual(fields, ["ctx", "scope", "collab_conn", "owner_conn",
                                  "actor_conn", "target_conn", "connections",
                                  "roster", "operation", "item_keys", "plan",
                                  "retirement_extra_paths",
                                  "retirement_missing_paths"])
        self.assertTrue(dataclasses.is_dataclass(tws.TeamWriteInputs))
        self.assertTrue(tws.TeamWriteInputs.__dataclass_params__.frozen)
        self.assertIsInstance(tws.TeamWriteInputs.cleanup_errors, property)
        self.assertIsNone(tws.TeamWriteInputs.cleanup_errors.fset)

    def test_module_import_is_side_effect_free(self):
        """已缓存依赖下二次导入输入窗零连接；不声称完整冷启动覆盖。

        断言为**增量**口径（裁定 F3）：对比导入前 sys.modules 快照，仅断言
        本次重导入不新增 auth/main——pytest 会话内 conftest.py:170 有意在
        pytest_configure 先 import custom_backend.main（冻结 DATA_DIR 的
        会话级防护），全量在场断言在 pytest 下结构性不可满足；bare launcher
        与 pytest 双环境均按增量口径可满足。"""
        control = _ConnectControl()
        cwd_before = sorted(p.name for p in Path.cwd().iterdir())
        data_before = (Path.cwd() / "data").exists()
        with control.installed(b1):
            snapshot = frozenset(sys.modules)
            sys.modules.pop(MODULE_NAME, None)
            importlib.import_module(MODULE_NAME)
            self.assertEqual(control.opens, [],
                             "产品模块导入期不得打开任何 SQLite 连接")
            fresh = {name for name in set(sys.modules) - snapshot
                     if name.startswith("custom_backend.")}
            self.assertNotIn("custom_backend.auth", fresh,
                             "输入窗口模块导入期不得新增导入 auth 全局")
            self.assertNotIn("custom_backend.main", fresh,
                             "输入窗口模块导入期不得新增导入 main 全局")
        cwd_after = sorted(p.name for p in Path.cwd().iterdir())
        self.assertEqual(cwd_before, cwd_after, "导入期不得改写 cwd 顶层条目")
        self.assertEqual(data_before, (Path.cwd() / "data").exists(),
                         "导入期不得新建默认 data 目录")

    # -- 成功路径合同 ---------------------------------------------------------

    def test_member_actor_window_success_contract(self):
        task_dir, runtime, info, user, owner_db, collab_path = self._world(
            task_id="t1", grants=(("bob", "coder"),),
            owner_votes={"K1": ("include", "2026-11-14T09:00:00+00:00",
                                "2026-11-14T08:00:00+00:00")},
            member_dbs=("bob",),
            member_votes={"bob": {"K1": ("include", "2026-11-14T09:05:00+00:00",
                                         "2026-11-14T08:05:00+00:00")}},
            actor="bob")
        decoy = task_dir / "users.db"
        decoy.write_bytes(b"decoy: task-dir users.db must never be opened")
        # 进入前一瞬的授权必须被锁内枚举采信（不接受夹具期缓存名册）
        self._grant(collab_path, "t1", "carol", "reconciler")
        self._current_target_kind = "actor"
        expected_labels = {_label_of(p) for p in
                           (collab_path, owner_db, task_dir / "bob.db",
                            self.users_db)}
        with self._observe() as control:
            self._last_control = control
            opens_in_body = None
            with self._window(runtime, info, user,
                              target_kind="actor") as inputs:
                opens_in_body = len(control.opens)
                self._assert_connection_types(inputs)
                # accounts 路径显式由 runtime.data_dir/'users.db' 派生；
                # 任务目录同名 decoy 绝不打开
                self.assertNotIn(_label_of(decoy), control.labels())
                self._assert_reservation_phases(
                    control, users_db=self.users_db, collab_path=collab_path,
                    attempts=1)
                state = inputs.scope.role_state()
                self.assertEqual(state["classification"], "owner_match")
                kind = tsr.task_kind(
                    state, owner_latched=inputs.scope.owner_latched())
                self.assertEqual(kind, "team")
                uids_in_roster = {entry["user_id"] for entry in inputs.roster}
                self.assertEqual(uids_in_roster,
                                 {self.uids["alice"], self.uids["bob"],
                                  self.uids["carol"]},
                                 "collab 锁内现读名册（进入前 grant 立即可见）")
                by_id, _by_casefold = inputs.scope.accounts()
                self.assertEqual(by_id[self.uids["bob"]], "bob")
                self.assertEqual(by_id[self.uids["alice"]], "alice")
                # 最终 borrowed 复读：required 成员票经保留连接真实可读
                snapshot = inputs.scope.member_vote_snapshot(
                    self.uids["bob"], "bob", "bob", "stage1")
                self.assertIsNotNone(snapshot)
                _library_id, rows = snapshot
                self.assertIn("K1", rows)
                # 业务写：目标(actor)持久；非目标(owner)回滚
                inputs.target_conn.execute(
                    "INSERT INTO decisions (zotero_key, decision,"
                    " exclusion_reason, notes, tags, screened_at, updated_at)"
                    " VALUES ('K2', 'exclude', '', '', '',"
                    " '2026-11-14T10:00:00+00:00', '2026-11-14T10:00:00+00:00')")
                inputs.owner_conn.execute(
                    "INSERT INTO articles (zotero_key, title, is_duplicate_of,"
                    " import_order) VALUES ('Z9', 'rollback-probe', NULL, 99)")
                self.assertEqual(opens_in_body, len(control.opens),
                                 "窗口体内不得新开任何连接（无隐藏 path helper）")
            self.assertIsNotNone(opens_in_body, "窗口必须 yield TeamWriteInputs")
            self.assertEqual(opens_in_body, len(control.opens),
                             "退出阶段同样零新打开")
            self.assertEqual(control.opens_after_last_boundary(), [],
                             "最终 reserve 成功返回之后不得再打开任何库")
            self.assertEqual(runtime.sync_calls, 1,
                             "非属主 actor 窗口前恰好一次 sync_catalog")
            self.assertEqual(runtime.context_calls, 1,
                             "runtime.context 仅在最终保留前调用一次")
            self.assertEqual(runtime.notify_calls, 0,
                             "本轮内部窗口不发 notify/SSE")
            self.assertEqual(control.labels(), expected_labels,
                             "只打开 required 集合（collab/owner/users/actor 成员）")
            self.assertEqual(inputs.cleanup_errors, ())
        # 目标提交持久、非目标回滚、连接关闭、锁释放
        for label in expected_labels:
            self.assertIn(label, control.close_order,
                          f"窗口退出后连接必须已关闭: {label}")
        with closing(sqlite3.connect(task_dir / "bob.db")) as conn:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM decisions WHERE zotero_key='K2'"
            ).fetchone()[0], 1, "actor 目标写必须在窗口退出后持久")
        with closing(sqlite3.connect(owner_db)) as conn:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM articles WHERE zotero_key='Z9'"
            ).fetchone()[0], 0, "非目标输入写必须回滚")
        self.assertTrue(self._lock_probe(runtime, "t1"), "退出后规范锁释放")
        self._assert_no_journal_left(task_dir)
        self._last_control = None

    def test_owner_actor_physical_reuse_and_no_marker(self):
        task_dir, runtime, info, user, owner_db, collab_path = self._world(
            task_id="t2", grants=(("bob", "coder"),), actor="alice")
        self._current_target_kind = "actor"
        with self._observe() as control:
            with self._window(runtime, info, user,
                              target_kind="actor") as inputs:
                self.assertIs(inputs.owner_conn, inputs.actor_conn,
                              "actor=owner 必须物理复用同一连接")
                self.assertIs(inputs.target_conn, inputs.owner_conn)
                self.assertEqual(len(inputs.connections), 4,
                                 "collab+owner(=actor)+users+bob，无重复物理库")
                self.assertEqual(runtime.sync_calls, 1,
                                 "每次尝试调用宿主 sync_catalog seam")
                self.assertEqual(runtime.actual_sync_calls, 0,
                                 "owner=actor 宿主 no-op，不发生自同步")
                inputs.target_conn.execute(
                    "INSERT INTO decisions (zotero_key, decision,"
                    " exclusion_reason, notes, tags, screened_at, updated_at)"
                    " VALUES ('K1', 'include', '', '', '',"
                    " '2026-11-14T10:00:00+00:00', '2026-11-14T10:00:00+00:00')")
            self.assertEqual(runtime.sync_calls, 1)
            self.assertEqual(runtime.actual_sync_calls, 0)
            self.assertEqual(runtime.notify_calls, 0)
        with closing(sqlite3.connect(owner_db)) as conn:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM decisions WHERE zotero_key='K1'"
            ).fetchone()[0], 1, "owner=actor 目标提交持久")

    def test_collab_target_unique_target_commit(self):
        task_dir, runtime, info, user, owner_db, collab_path = self._world(
            task_id="t3", grants=(("bob", "coder"),), actor="bob")
        self._current_target_kind = "collab"
        with self._observe() as control:
            with self._window(runtime, info, user,
                              target_kind="collab") as inputs:
                self.assertIs(inputs.target_conn, inputs.collab_conn)
                self.assertIsNot(inputs.target_conn, inputs.actor_conn)
                inputs.target_conn.execute(
                    "INSERT INTO collaboration_role_grants"
                    " (user_id, role, granted_by, granted_at) VALUES (?, ?, ?, ?)",
                    (self.uids["dave"], "coder", self.uids["alice"],
                     "2026-11-15T00:00:00+00:00"))
                inputs.actor_conn.execute(
                    "INSERT INTO articles (zotero_key, title, is_duplicate_of,"
                    " import_order) VALUES ('Z9', 'rollback-probe', NULL, 99)")
        with closing(sqlite3.connect(collab_path)) as conn:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM collaboration_role_grants WHERE user_id=?",
                (self.uids["dave"],)).fetchone()[0], 1, "collab 目标写必须持久")
        with closing(sqlite3.connect(task_dir / "bob.db")) as conn:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM articles WHERE zotero_key='Z9'"
            ).fetchone()[0], 0, "非目标 actor 写必须回滚")
        self.assertTrue(self._lock_probe(runtime, "t3"))

    def test_reconciler_actor_reserved_even_when_not_required_screener(self):
        task_dir, runtime, info, user, owner_db, collab_path = self._world(
            task_id="t4", grants=(("bob", "coder"), ("carol", "reconciler")),
            member_dbs=("bob", "carol"), actor="carol")
        # dave 曾授权后撤销：其残留库不得打开、roster 不得包含
        self._grant(collab_path, "t4", "dave", "coder")
        collab_mod.revoke_role(collab_path, actor_user_id=self.uids["alice"],
                               user_id=self.uids["dave"], role="coder",
                               task_id="t4")
        dave_db = self._screening_db(task_dir / "dave.db")
        carol_db = task_dir / "carol.db"
        self._current_target_kind = "actor"
        with self._observe() as control:
            with self._window(runtime, info, user,
                              target_kind="actor") as inputs:
                self.assertIn(_label_of(carol_db), control.labels(),
                              "非应筛 reconciler actor 的票库仍必须保留")
                self.assertNotIn(_label_of(dave_db), control.labels(),
                                 "被移除成员残留库不得打开")
                self.assertNotIn(self.uids["dave"],
                                 {entry["user_id"] for entry in inputs.roster})
                inputs.target_conn.execute(
                    "INSERT INTO decisions (zotero_key, decision,"
                    " exclusion_reason, notes, tags, screened_at, updated_at)"
                    " VALUES ('K1', 'include', '', '', '',"
                    " '2026-11-14T10:00:00+00:00', '2026-11-14T10:00:00+00:00')")
        self.assertTrue(dave_db.is_file(), "残留库不得被删除或重建")
        with closing(sqlite3.connect(carol_db)) as conn:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM decisions WHERE zotero_key='K1'"
            ).fetchone()[0], 1, "reconciler actor 目标提交持久")

    # -- 缺库 / 坏库语义 -------------------------------------------------------

    def test_missing_required_member_zero_create_and_scope_missing_vote(self):
        task_dir, runtime, info, user, owner_db, collab_path = self._world(
            task_id="t5", grants=(("bob", "coder"),), member_dbs=(),
            actor="alice")
        bob_db = task_dir / "bob.db"
        self.assertFalse(bob_db.exists())
        self._current_target_kind = "actor"
        with self._observe() as control:
            with self._window(runtime, info, user,
                              target_kind="actor") as inputs:
                self.assertNotIn(_label_of(bob_db), control.labels(),
                                 "缺票成员路径不得传给 B1/不得打开")
                snapshot = inputs.scope.member_vote_snapshot(
                    self.uids["bob"], "bob", "bob", "stage1")
                self.assertIsNone(snapshot,
                                  "缺 required 成员票必须经 scope 真实派生 missing vote")
                self.assertEqual(len(inputs.connections), 3,
                                 "collab+owner(=actor)+users；缺成员不占连接")
                inputs.target_conn.execute(
                    "INSERT INTO decisions (zotero_key, decision,"
                    " exclusion_reason, notes, tags, screened_at, updated_at)"
                    " VALUES ('K1', 'include', '', '', '',"
                    " '2026-11-14T10:00:00+00:00', '2026-11-14T10:00:00+00:00')")
        self.assertFalse(bob_db.exists(), "缺票成员库零建")
        with closing(sqlite3.connect(owner_db)) as conn:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM decisions WHERE zotero_key='K1'"
            ).fetchone()[0], 1, "缺票不拒绝本内部输入窗口（业务资格归 B2b），目标提交正常")

    def test_present_but_broken_member_db_not_missing(self):
        for variant in ("garbage", "directory"):
            with self.subTest(variant=variant):
                task_dir, runtime, info, user, owner_db, collab_path = self._world(
                    task_id=f"t6-{variant}", grants=(("bob", "coder"),),
                    member_dbs=(), actor="alice")
                bob_db = task_dir / "bob.db"
                if variant == "garbage":
                    bob_db.write_bytes(b"definitely not a sqlite database")
                else:
                    bob_db.mkdir()
                before = self._bytes_of((collab_path, self.users_db, owner_db))
                yielded = False
                error = None
                with self._observe() as control:
                    try:
                        with self._window(runtime, info, user,
                                          target_kind="actor") as inputs:
                            yielded = True
                    except BaseException as exc:
                        error = exc
                self.assertFalse(yielded, "现存坏成员库不得按缺票放行")
                self.assertIsInstance(error, tsr.TeamFlowUnavailableError)
                self.assertEqual(error.reason, "member_db_unreadable")
                if variant == "directory":
                    self.assertNotIn(_label_of(bob_db), control.labels(),
                                     "目录应在 stat 阶段拒绝，零打开")
                else:
                    self.assertIn(_label_of(bob_db), control.labels(),
                                  "垃圾普通文件须真实打开后辨认 SQLite 损坏")
                    self.assertEqual(control.modes_for(_label_of(bob_db)), ["rw"])
                    self.assertFalse(control.reservations,
                                     "损坏库使 reserve 失败，不能成为完整保留输入")
                    for connection in control.label_conns[_label_of(bob_db)]:
                        with self.assertRaises(sqlite3.ProgrammingError):
                            connection.execute("SELECT 1")
                self._assert_bytes_unchanged(before)
                if variant == "garbage":
                    self.assertEqual(bob_db.read_bytes(),
                                     b"definitely not a sqlite database",
                                     "坏成员库零重建零改写")
                else:
                    self.assertTrue(bob_db.is_dir())
                self.assertTrue(self._lock_probe(runtime, f"t6-{variant}"),
                                "失败后规范锁释放")

    def test_participant_casefold_conflict_typed(self):
        """§33：全账户 casefold 撞库的不同账号，须按 TSR typed 原因拒绝。"""
        task_dir, runtime, info, user, owner_db, collab_path = self._world(
            task_id="t7", grants=(("bob", "coder"), ("Dave", "coder")),
            member_dbs=("bob",), actor="alice")
        before = self._bytes_of((collab_path, self.users_db, owner_db))
        yielded = False
        error = None
        with self._observe() as control:
            try:
                with self._window(runtime, info, user,
                                  target_kind="actor") as inputs:
                    yielded = True
            except BaseException as exc:
                error = exc
        self.assertFalse(yielded)
        self.assertIsInstance(error, tsr.TeamFlowUnavailableError)
        self.assertEqual(error.reason, "storage_conflict",
                         "账号 casefold 碰撞必须按 TSR 现有 typed 原因")
        self._assert_bytes_unchanged(before)
        self.assertEqual(runtime.sync_calls, 0)
        self.assertTrue(self._lock_probe(runtime, "t7"))

    # -- 预检查（主控 R1-1：context/sync 之前拒绝） ----------------------------

    def _precheck_case(self, *, task_id, grants, actor, alias_target=None,
                       cross_uid=False):
        """actor 卷入的物理别名/保留名世界；断言准备前 typed 拒绝与零副作用。"""
        task_dir, runtime, info, user, owner_db, collab_path = self._world(
            task_id=task_id, grants=grants, member_dbs=(), actor=actor)
        actor_db = task_dir / (tasks_mod.slugify_screener(
            user["username"]) + ".db")
        if alias_target == "collab":
            os.link(collab_path, actor_db)
        elif alias_target == "accounts":
            os.link(self.users_db, actor_db)
        elif cross_uid:
            bob_db = self._screening_db(task_dir / "bob.db")
            carol_db = self._screening_db(task_dir / "carol.db")
            self.assertFalse(bob_db.samefile(carol_db))
            # Independent-path positive control rules out casefold/fixture rejection.
            with self._observe() as positive:
                with self._window(runtime, info, user,
                                  target_kind="actor") as inputs:
                    self._assert_connection_types(inputs)
                    inputs.target_conn.execute(
                        "INSERT INTO decisions (zotero_key, decision, screened_at,"
                        " updated_at) VALUES ('K1', 'include', 'positive', 'positive')")
                self._assert_reservation_phases(
                    positive, users_db=self.users_db, collab_path=collab_path,
                    attempts=1)
            with closing(sqlite3.connect(bob_db)) as conn:
                self.assertEqual(conn.execute(
                    "SELECT COUNT(*) FROM decisions WHERE zotero_key='K1'"
                ).fetchone()[0], 1, "独立路径正控必须真实 yield/commit")
            carol_db.unlink()
            os.link(bob_db, carol_db)
            self.assertTrue(bob_db.samefile(carol_db), "物理故障必须确已注入")
            runtime.context_calls = runtime.sync_calls = runtime.actual_sync_calls = 0
        watch = [collab_path, self.users_db, owner_db]
        for extra in (task_dir / "bob.db", task_dir / "carol.db", actor_db):
            if extra.exists() and extra not in watch:
                watch.append(extra)
        before = self._bytes_of(watch)
        listing_before = self._listing(task_dir)
        yielded = False
        error = None
        with self._observe() as control:
            try:
                with self._window(runtime, info, user,
                                  target_kind="actor") as inputs:
                    yielded = True
            except BaseException as exc:
                error = exc
            self.assertFalse(yielded, "物理别名/保留名必须在准备前拒绝")
            self.assertIsNotNone(error)
            self.assertIsInstance(error, tsr.TeamFlowUnavailableError,
                                  "必须是 typed 拒绝，不裸 ValueError")
            self.assertEqual(error.reason, "storage_conflict")
            self.assertEqual(runtime.context_calls, 0,
                             "必须在 runtime.context 可能迁移前拒绝")
            self.assertEqual(runtime.sync_calls, 0,
                             "必须在 runtime.sync_catalog 可能迁移前拒绝")
            self.assertEqual(runtime.notify_calls, 0)
            self._assert_bytes_unchanged(before)
            self.assertEqual(self._listing(task_dir), listing_before,
                             "拒绝路径不得新建/删除任务目录文件（含 journal）")
            self.assertTrue(self._lock_probe(runtime, task_id), "失败后规范锁释放")
        return error

    def test_precheck_actor_collab_alias_rejected_before_prepare(self):
        self._precheck_case(task_id="p1", grants=(("bob", "coder"),),
                            actor="bob", alias_target="collab")

    def test_precheck_actor_accounts_alias_rejected_before_prepare(self):
        self._precheck_case(task_id="p2", grants=(("bob", "coder"),),
                            actor="bob", alias_target="accounts")

    def test_precheck_cross_uid_hardlink_rejected_before_prepare(self):
        self._precheck_case(task_id="p3",
                            grants=(("bob", "coder"), ("carol", "coder")),
                            actor="bob", cross_uid=True)

    def test_precheck_actor_reserved_name_rejected_before_prepare(self):
        # 'collaboration' 账号的 actor 库路径即保留名 collaboration.db
        self._precheck_case(task_id="p4", grants=(("collaboration", "coder"),),
                            actor="collaboration")

    def test_member_collab_alias_rejected_storage_conflict(self):
        """非 actor 成员库与协作库同物理：仍必须 storage_conflict 拒绝。"""
        task_dir, runtime, info, user, owner_db, collab_path = self._world(
            task_id="p5", grants=(("bob", "coder"),), member_dbs=(),
            actor="alice")
        bob_db = task_dir / "bob.db"
        os.link(collab_path, bob_db)
        before = self._bytes_of((collab_path, self.users_db, owner_db))
        yielded = False
        error = None
        with self._observe() as control:
            try:
                with self._window(runtime, info, user,
                                  target_kind="actor") as inputs:
                    yielded = True
            except BaseException as exc:
                error = exc
        self.assertFalse(yielded)
        self.assertIsInstance(error, tsr.TeamFlowUnavailableError)
        self.assertEqual(error.reason, "storage_conflict")
        self._assert_bytes_unchanged(before)
        self.assertTrue(self._lock_probe(runtime, "p5"))

    def test_owner_snapshot_slug_mismatch_identity(self):
        """属主快照名与当前账号 slug 分叉：有快照库=identity_unconfirmed；
        快照库缺失=输入错误拒绝且零建。"""
        for present in (True, False):
            with self.subTest(snapshot_db_present=present):
                task_id = f"p6-{present}"
                task_dir = self.tasks_root / task_id
                task_dir.mkdir(parents=True)
                collab_path = self._init_collab(task_dir, task_id, "alice")
                self._grant(collab_path, task_id, "bob", "coder")
                alice_db = self._screening_db(task_dir / "alice.db", latch=True)
                # 裁定 F2：p6 走正常队形，经同一辅助补产品同款两步；其被测
                # 拒绝（identity_unconfirmed/输入错误）在 preflight/身份层、
                # 先于计划，补备不影响其断言。
                self._prepare_team_protocol(
                    collab_path, task_id=task_id,
                    owner_user_id=self.uids["alice"], owner_db=alice_db)
                # Keep the real owner uid; mutate only the stored snapshot name.
                with closing(sqlite3.connect(collab_path)) as conn:
                    conn.execute("PRAGMA foreign_keys=ON")
                    conn.execute("UPDATE collaboration_task SET owner_username=?"
                                 " WHERE singleton=1", ("al1ce",))
                    conn.commit()
                info = tasks_mod.TaskInfo(task_id=task_id, dir=task_dir,
                                          display_name=task_id)
                runtime = _FakeRuntime({task_id: info}, self.data_dir)
                user = {"user_id": self.uids["alice"], "username": "alice"}
                al1ce_db = task_dir / "al1ce.db"
                self.assertFalse(al1ce_db.exists(), "各子测必须独立准备")
                watch = [collab_path, self.users_db, alice_db]
                if present:
                    self._screening_db(al1ce_db, latch=True)
                    watch.append(al1ce_db)
                before = self._bytes_of(watch)
                listing_before = self._listing(task_dir)
                yielded = False
                error = None
                with self._observe() as control:
                    try:
                        with self._window(runtime, info, user,
                                          target_kind="actor") as inputs:
                            yielded = True
                    except BaseException as exc:
                        error = exc
                self.assertFalse(yielded)
                self.assertIsInstance(error, tsr.TeamFlowUnavailableError)
                self.assertEqual(error.reason, "identity_unconfirmed" if present
                                 else "owner_db_unreadable")
                self.assertNotIsInstance(error, ValueError)
                self.assertEqual(al1ce_db.exists(), present,
                                 "缺快照库零建，现存快照库不删除")
                self._assert_bytes_unchanged(before)
                self.assertEqual(self._listing(task_dir), listing_before)
                self.assertEqual(runtime.context_calls, 0)
                self.assertEqual(runtime.sync_calls, 0)
                self.assertTrue(self._lock_probe(runtime, task_id))

    # -- typed 输入错误（主控 R1-2） -------------------------------------------

    def test_legacy_missing_collab_typed_zero_creation(self):
        """缺 collab（legacy 未初始化）必须 typed 拒绝，不裸 ValueError、零建库。"""
        task_dir = self.tasks_root / "p7"
        task_dir.mkdir(parents=True)
        registry = {"p7": tasks_mod.TaskInfo(task_id="p7", dir=task_dir,
                                             display_name="p7")}
        runtime = _FakeRuntime(registry, self.data_dir)
        user = {"user_id": self.uids["alice"], "username": "alice"}
        info = tasks_mod.TaskInfo(task_id="p7", dir=task_dir, display_name="p7")
        yielded = False
        error = None
        with self._observe() as control:
            try:
                with self._window(runtime, info, user,
                                  target_kind="actor") as inputs:
                    yielded = True
            except BaseException as exc:
                error = exc
        self.assertFalse(yielded)
        self.assertIsNotNone(error)
        self.assertNotIsInstance(error, ValueError, "不得裸 ValueError")
        self.assertIsInstance(error, tsr.TeamFlowUnavailableError,
                              "缺 collab 必须 typed")
        self.assertIn(error.reason, tsr.TeamFlowUnavailableError.REASONS)
        self.assertFalse((task_dir / "collaboration.db").exists(), "零建协作库")
        self.assertEqual(self._listing(task_dir), [], "任务目录零新建文件")
        self.assertTrue(self._lock_probe(runtime, "p7"))

    def test_unconfirmed_collab_binding_typed(self):
        """现存协作库但绑定不可确认：必须 typed unconfirmed_task_state。"""
        task_dir, runtime, info, user, owner_db, collab_path = self._world(
            task_id="p8", grants=(("bob", "coder"),), actor="alice")
        conn = sqlite3.connect(collab_path)
        try:
            conn.execute("DELETE FROM collaboration_task WHERE singleton=1")
            conn.commit()
        finally:
            conn.close()
        before = self._bytes_of((collab_path, self.users_db, owner_db))
        yielded = False
        error = None
        with self._observe() as control:
            try:
                with self._window(runtime, info, user,
                                  target_kind="actor") as inputs:
                    yielded = True
            except BaseException as exc:
                error = exc
        self.assertFalse(yielded)
        self.assertIsInstance(error, tsr.TeamFlowUnavailableError)
        self.assertEqual(error.reason, "unconfirmed_task_state")
        self._assert_bytes_unchanged(before)
        self.assertTrue(self._lock_probe(runtime, "p8"))

    def test_owner_begin_busy_maps_owner_db_unreadable(self):
        """reserve 阶段 owner BEGIN busy 必须映射 owner_db_unreadable（原 busy
        异常对象保留在异常链）。本用例按 B1 默认 busy timeout 真实等待。"""
        task_dir, runtime, info, user, owner_db, collab_path = self._world(
            task_id="p9", grants=(("bob", "coder"),), actor="alice")
        holder = sqlite3.connect(str(owner_db), timeout=1000)
        holder.execute("BEGIN IMMEDIATE")
        self.addCleanup(holder.close)
        yielded = False
        error = None
        with self._observe() as control:
            try:
                with self._window(runtime, info, user,
                                  target_kind="actor") as inputs:
                    yielded = True
            except BaseException as exc:
                error = exc
        self.assertFalse(yielded)
        self.assertIsInstance(error, tsr.TeamFlowUnavailableError)
        self.assertEqual(error.reason, "owner_db_unreadable",
                         "owner 库 busy 不得全标 member/缺票")
        self.assertEqual(len(control.begin_failures), 1,
                         "必须命中真实保留连接 BEGIN IMMEDIATE 故障")
        failed_conn, failed_label, original_busy = control.begin_failures[0]
        self.assertEqual(failed_label, _label_of(owner_db))
        self.assertEqual(failed_conn._ctl_mode, "rw")
        self.assertEqual(original_busy.sqlite_errorcode & 0xff, sqlite3.SQLITE_BUSY)
        self.assertTrue(self._chain_has(error, original_busy),
                        "异常链必须含 SQLite 抛出的同一 busy 对象（is）")
        self.assertEqual(len(control.reserve_failures), 1)
        session, reserve_error = control.reserve_failures[0]
        self.assertIs(reserve_error, original_busy)
        self.assertEqual(session.failed_path, owner_db.resolve())
        holder.rollback()
        holder.close()
        self.assertTrue(self._lock_probe(runtime, "p9"), "失败后规范锁释放")

    def test_accounts_begin_busy_maps_accounts_unreadable(self):
        """reserve 阶段 users BEGIN busy 必须映射 accounts_unreadable。"""
        task_dir, runtime, info, user, owner_db, collab_path = self._world(
            task_id="p10", grants=(("bob", "coder"),), actor="alice")
        holder = sqlite3.connect(str(self.users_db), timeout=1000)
        holder.execute("BEGIN IMMEDIATE")
        self.addCleanup(holder.close)
        yielded = False
        error = None
        with self._observe() as control:
            try:
                with self._window(runtime, info, user,
                                  target_kind="actor") as inputs:
                    yielded = True
            except BaseException as exc:
                error = exc
        self.assertFalse(yielded)
        self.assertIsInstance(error, tsr.TeamFlowUnavailableError)
        self.assertEqual(error.reason, "accounts_unreadable",
                         "accounts 库 busy 不得全标 member/缺票")
        self.assertEqual(len(control.begin_failures), 1,
                         "账户 busy 必须来自真实保留连接 BEGIN IMMEDIATE")
        failed_conn, failed_label, original_busy = control.begin_failures[0]
        self.assertEqual(failed_label, _label_of(self.users_db))
        self.assertEqual(failed_conn._ctl_mode, "rw")
        self.assertEqual(original_busy.sqlite_errorcode & 0xff, sqlite3.SQLITE_BUSY)
        self.assertTrue(self._chain_has(error, original_busy))
        self.assertEqual(len(control.reserve_failures), 1)
        session, reserve_error = control.reserve_failures[0]
        self.assertIs(reserve_error, original_busy)
        self.assertEqual(session.failed_path, self.users_db.resolve())
        holder.rollback()
        holder.close()
        self.assertTrue(self._lock_probe(runtime, "p10"))

    # -- owner 缺失不让 context 创建 -------------------------------------------

    def test_missing_owner_catalog_no_context_creation(self):
        """owner catalog 缺失：输入错误拒绝；窗口不得再借 context 重建。"""
        task_dir, runtime, info, user, owner_db, collab_path = self._world(
            task_id="p11", grants=(("bob", "coder"),), actor="alice")
        owner_db.unlink()
        self.assertFalse(owner_db.exists())
        bob_db = task_dir / "bob.db"
        before = self._bytes_of((collab_path, self.users_db, bob_db))
        yielded = False
        error = None
        with self._observe() as control:
            try:
                with self._window(runtime, info, user,
                                  target_kind="actor") as inputs:
                    yielded = True
            except BaseException as exc:
                error = exc
        self.assertFalse(yielded, "owner 缺失是输入错误，不伪缺票")
        self.assertIsNotNone(error)
        self.assertNotIsInstance(error, ValueError)
        self.assertLessEqual(runtime.context_calls, 1,
                             "不得二次调用 context 借宿主准备重建 owner")
        self.assertFalse(owner_db.exists(), "owner catalog 缺失零重建")
        self._assert_bytes_unchanged(before)
        self.assertTrue(self._lock_probe(runtime, "p11"))

    # -- marker 漂移有限重试（主控 R1-3） --------------------------------------

    def test_marker_drift_one_retry_from_scratch_then_success(self):
        task_dir, runtime, info, user, owner_db, collab_path = self._world(
            task_id="p12", grants=(("bob", "coder"),), member_dbs=("bob",),
            actor="bob")
        runtime.sync_mode = "noop_once_then_real"
        bob_db = task_dir / "bob.db"
        self._current_target_kind = "actor"
        with self._observe() as control:
            with self._window(runtime, info, user,
                              target_kind="actor") as inputs:
                self._assert_connection_types(inputs)
                inputs.target_conn.execute(
                    "INSERT INTO decisions (zotero_key, decision,"
                    " exclusion_reason, notes, tags, screened_at, updated_at)"
                    " VALUES ('K2', 'include', '', '', '',"
                    " '2026-11-14T10:00:00+00:00', '2026-11-14T10:00:00+00:00')")
            self.assertEqual(runtime.sync_calls, 2,
                             "漂移后完整释放并恰一次重试（MAX_ATTEMPTS=2）")
            self._assert_reservation_phases(
                control, users_db=self.users_db, collab_path=collab_path,
                attempts=2)
            self.assertEqual(control.opens_after_last_boundary(), [],
                             "第二次保留后同样零隐藏打开")
            self.assertEqual(runtime.notify_calls, 0)
            self.assertEqual(inputs.cleanup_errors, ())
        with closing(sqlite3.connect(bob_db)) as conn:
            marker = conn.execute(
                "SELECT revision FROM workflow_catalog_sync"
                " WHERE owner_identity=?", (owner_db.name,)).fetchone()
        self.assertIsNotNone(marker, "第二次真实 sync 必须写入 marker")
        with closing(sqlite3.connect(owner_db)) as conn:
            revision = conn.execute(
                "SELECT catalog_revision FROM workflow_state"
                " WHERE singleton=1").fetchone()[0]
        self.assertEqual(int(marker[0]), int(revision),
                         "marker 必须与当前属主 revision 一致")
        with closing(sqlite3.connect(bob_db)) as conn:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM decisions WHERE zotero_key='K2'"
            ).fetchone()[0], 1, "重试成功后目标写恰提交一次")
        self.assertTrue(self._lock_probe(runtime, "p12"))

    def test_marker_stale_exhausts_attempts_structured_409(self):
        task_dir, runtime, info, user, owner_db, collab_path = self._world(
            task_id="p13", grants=(("bob", "coder"),), member_dbs=("bob",),
            actor="bob")
        runtime.sync_mode = "noop"
        bob_db = task_dir / "bob.db"
        before = self._bytes_of((collab_path, self.users_db, owner_db, bob_db))
        yielded = False
        error = None
        with self._observe() as control:
            self._last_control = control
            try:
                with self._window(runtime, info, user,
                                  target_kind="actor") as inputs:
                    yielded = True
            except BaseException as exc:
                error = exc
        self._last_control = None
        self.assertFalse(yielded, "marker 用尽必须拒绝")
        self.assertIsNotNone(error)
        payload = self._structured_catalog_sync_stale(error)
        self.assertIsNotNone(payload,
                             "用尽必须以 B2-local 结构化 catalog_sync_stale 拒绝")
        self.assertEqual(payload.get("code"), "catalog_sync_stale")
        self.assertEqual(payload.get("message_key"), "team.flow.catalog_sync_stale")
        self.assertEqual(payload.get("params"), {})
        self.assertEqual(runtime.sync_calls, 2, "MAX_ATTEMPTS=2，无第三次")
        self._assert_reservation_phases(
            control, users_db=self.users_db, collab_path=collab_path, attempts=2)
        self.assertEqual(runtime.notify_calls, 0)
        self._assert_bytes_unchanged(before)
        with closing(sqlite3.connect(bob_db)) as conn:
            self.assertIsNone(conn.execute(
                "SELECT revision FROM workflow_catalog_sync WHERE owner_identity=?",
                (owner_db.name,)).fetchone(), "业务零写：marker 保持缺失")
        self._assert_released((collab_path, bob_db, owner_db, self.users_db))
        self.assertTrue(self._lock_probe(runtime, "p13"))

    def test_marker_drift_with_cleanup_errors_blocks_retry(self):
        """漂移释放已产生 cleanup_errors 时，不得再 sync/重试（fail-closed）。"""
        task_dir, runtime, info, user, owner_db, collab_path = self._world(
            task_id="p14", grants=(("bob", "coder"),), member_dbs=("bob",),
            actor="bob")
        runtime.sync_mode = "noop"
        control = _ConnectControl()
        control.fail_close_labels = (_label_of(owner_db),)
        with control.installed(b1):
            yielded = False
            error = None
            try:
                with self._window(runtime, info, user,
                                  target_kind="actor") as inputs:
                    yielded = True
            except BaseException as exc:
                error = exc
            self.assertFalse(yielded)
            self.assertIsNotNone(error, "释放故障在册不得重试后放行")
            self.assertIsInstance(error, tsr.TeamFlowUnavailableError)
            self.assertEqual(error.reason, "connection_contract")
            self.assertEqual(runtime.sync_calls, 1,
                             "释放故障后不得第二次 sync_catalog")
            self.assertEqual(runtime.actual_sync_calls, 0)
            self._assert_reservation_phases(
                control, users_db=self.users_db, collab_path=collab_path,
                attempts=1)
            self.assertEqual(len(control.close_faults), 1,
                             "close 注入必须确实命中，而非 preflight/context 先失败")
            faulted_conn, faulted_label = control.close_faults[0]
            self.assertEqual(faulted_label, _label_of(owner_db))
            self.assertEqual(faulted_conn._ctl_mode, "rw")
            self.assertIn(faulted_conn, control.reservations[0].connections)
            self.assertIs(faulted_conn, control.conn_for(owner_db))
            cleanup_errors = control.reservations[0].cleanup_errors
            self.assertTrue(cleanup_errors, "必须观察到 B1 实际 cleanup_errors")
            self.assertTrue(any(str(control.close_failure) in item
                                and "close" in item for item in cleanup_errors))
            self.assertTrue(self._lock_probe(runtime, "p14"))
        self._assert_released((collab_path, owner_db, task_dir / "bob.db",
                               self.users_db))

    # -- 正常路径正控（独审 R1：拒绝面不得掩盖正常全拒） ------------------------

    def test_first_time_actor_legal_positive_control(self):
        """首用 actor：宿主 context 既有准备合法建库后，窗口必须到 yield 并
        实际 commit（防 unbound local 类正常路径崩溃）。"""
        task_dir, runtime, info, user, owner_db, collab_path = self._world(
            task_id="p20", grants=(("bob", "coder"),), member_dbs=(),
            actor="bob")
        runtime.prepare_actor = True   # 真实宿主 context 的既有 owner/actor 准备
        bob_db = task_dir / "bob.db"
        self.assertFalse(bob_db.exists())
        self._current_target_kind = "actor"
        with self._observe() as control:
            with self._window(runtime, info, user,
                              target_kind="actor") as inputs:
                self._assert_connection_types(inputs)
                inputs.target_conn.execute(
                    "INSERT INTO decisions (zotero_key, decision,"
                    " exclusion_reason, notes, tags, screened_at, updated_at)"
                    " VALUES ('K1', 'include', '', '', '',"
                    " '2026-11-14T10:00:00+00:00', '2026-11-14T10:00:00+00:00')")
            self.assertEqual(runtime.context_calls, 1)
            self.assertEqual(runtime.sync_calls, 1)
            self.assertEqual(inputs.cleanup_errors, ())
        with closing(sqlite3.connect(bob_db)) as conn:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM decisions WHERE zotero_key='K1'"
            ).fetchone()[0], 1, "首用 actor 正控必须真实到 yield 并 commit")
        for absent in (task_dir / "carol.db", task_dir / "dave.db"):
            self.assertFalse(absent.exists(), "除 actor 外不得新建成员库")
        self.assertTrue(self._lock_probe(runtime, "p20"))

    def test_removed_account_residual_hardlink_rejected_before_prepare(self):
        """已知已移除账号的残留 .db 与当前 actor 库同物理（hardlink）：
        只 stat / 不打开的别名检查必须在 context/sync 前拒绝；残留库不打开、
        不迁移、不恢复进票源（主控明确：仅 known accounts 候选 stat）。"""
        task_dir, runtime, info, user, owner_db, collab_path = self._world(
            task_id="p21", grants=(("bob", "coder"),), member_dbs=("bob",),
            actor="bob")
        # carol has no casefold-conflicting account; remove its real grant first.
        self._grant(collab_path, "p21", "carol", "coder")
        collab_mod.revoke_role(collab_path, actor_user_id=self.uids["alice"],
                               user_id=self.uids["carol"], role="coder",
                               task_id="p21")
        bob_db = task_dir / "bob.db"
        residual_db = self._screening_db(task_dir / "carol.db")
        self.assertFalse(bob_db.samefile(residual_db))
        with self._observe() as positive:
            with self._window(runtime, info, user, target_kind="actor") as inputs:
                self._assert_connection_types(inputs)
                self.assertNotIn(self.uids["carol"],
                                 {entry["user_id"] for entry in inputs.roster})
                self.assertNotIn(_label_of(residual_db), positive.labels(),
                                 "独立残留路径正控亦只 stat、零打开")
                inputs.target_conn.execute(
                    "INSERT INTO decisions (zotero_key, decision, screened_at,"
                    " updated_at) VALUES ('K1', 'include', 'positive', 'positive')")
            self._assert_reservation_phases(
                positive, users_db=self.users_db, collab_path=collab_path,
                attempts=1)
        with closing(sqlite3.connect(bob_db)) as conn:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM decisions WHERE zotero_key='K1'"
            ).fetchone()[0], 1, "独立路径正控真实 yield/commit，排除其他拒绝原因")
        residual_db.unlink()
        os.link(bob_db, residual_db)
        self.assertTrue(bob_db.samefile(residual_db), "跨 uid alias 必须确已注入")
        runtime.context_calls = runtime.sync_calls = runtime.actual_sync_calls = 0
        before = self._bytes_of((collab_path, self.users_db, owner_db, residual_db))
        listing_before = self._listing(task_dir)
        yielded = False
        error = None
        with self._observe() as control:
            try:
                with self._window(runtime, info, user,
                                  target_kind="actor") as inputs:
                    yielded = True
            except BaseException as exc:
                error = exc
            self.assertFalse(yielded, "残留库↔actor 同物理必须在准备前拒绝")
            self.assertIsNotNone(error)
            self.assertIsInstance(error, tsr.TeamFlowUnavailableError)
            self.assertEqual(error.reason, "storage_conflict")
            self.assertEqual(runtime.context_calls, 0)
            self.assertEqual(runtime.sync_calls, 0)
            self.assertNotIn(_label_of(residual_db), control.labels(),
                             "残留库只 stat 不得打开")
            self._assert_bytes_unchanged(before)
            self.assertEqual(self._listing(task_dir), listing_before)
        self.assertTrue(residual_db.is_file(), "残留库不得删除")
        self.assertTrue(self._lock_probe(runtime, "p21"))

    # -- 异常语义 ---------------------------------------------------------------

    def test_writer_exception_propagates_unchanged_no_replay(self):
        task_dir, runtime, info, user, owner_db, collab_path = self._world(
            task_id="p15", grants=(("bob", "coder"),), member_dbs=("bob",),
            actor="bob")
        injected = _InjectedFailure("writer failed inside window body")
        bob_db = task_dir / "bob.db"
        before = self._bytes_of((collab_path, self.users_db, owner_db))
        with self._observe() as control:
            self._last_control = control
            with self.assertRaises(_InjectedFailure) as caught:
                with self._window(runtime, info, user,
                                  target_kind="actor") as inputs:
                    inputs.target_conn.execute(
                        "INSERT INTO decisions (zotero_key, decision,"
                        " exclusion_reason, notes, tags, screened_at, updated_at)"
                        " VALUES ('K2', 'include', '', '', '',"
                        " '2026-11-14T10:00:00+00:00', '2026-11-14T10:00:00+00:00')")
                    raise injected
        # 异常退出后的断言（在 assertRaises 边界之外，确保真实可达）
        self.assertIs(caught.exception, injected, "writer 异常原样传播不重放")
        self.assertEqual(runtime.sync_calls, 1, "writer 异常不触发重试")
        self.assertEqual(control.commit_calls, [_label_of(bob_db)],
                         "唯一 commit 是窗口前 sync 的自有提交；目标零 commit 不重放")
        self.assertTrue(self._lock_probe(runtime, "p15"), "异常退出后规范锁释放")
        self._assert_bytes_unchanged(before)
        self._assert_released((collab_path, bob_db, owner_db, self.users_db))
        with closing(sqlite3.connect(bob_db)) as conn:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM decisions WHERE zotero_key='K2'"
            ).fetchone()[0], 0, "writer 异常的目标写必须同事务回滚")
        self._last_control = None

    def test_commit_failure_propagates_once(self):
        task_dir, runtime, info, user, owner_db, collab_path = self._world(
            task_id="p16", grants=(("bob", "coder"),), member_dbs=("bob",),
            actor="alice")
        control = _ConnectControl()
        failure = _InjectedFailure("target commit failed")
        control.commit_failure = failure
        bob_db = task_dir / "bob.db"
        before = self._bytes_of((collab_path, self.users_db, owner_db, bob_db))
        with control.installed(b1):
            self._last_control = control
            with self.assertRaises(_InjectedFailure) as caught:
                with self._window(runtime, info, user,
                                  target_kind="actor") as inputs:
                    inputs.target_conn.execute(
                        "INSERT INTO decisions (zotero_key, decision,"
                        " exclusion_reason, notes, tags, screened_at, updated_at)"
                        " VALUES ('K1', 'include', '', '', '',"
                        " '2026-11-14T10:00:00+00:00', '2026-11-14T10:00:00+00:00')")
        self.assertIs(caught.exception, failure, "commit 异常对象原样传播")
        self.assertEqual(len(control.commit_calls), 1,
                         "owner actor 无 sync 自有提交；唯一 commit 是失败的目标尝试")
        self.assertEqual(control.commit_calls[0], _label_of(owner_db))
        self.assertTrue(self._lock_probe(runtime, "p16"))
        self._assert_bytes_unchanged(before)
        self._assert_released((collab_path, bob_db, owner_db, self.users_db))
        self._last_control = None

    def test_cleanup_error_after_commit_keeps_success(self):
        task_dir, runtime, info, user, owner_db, collab_path = self._world(
            task_id="p17", grants=(("bob", "coder"),), member_dbs=("bob",),
            actor="alice")
        control = _ConnectControl()
        control.fail_close_labels = (_label_of(self.users_db),)
        bob_db = task_dir / "bob.db"
        with control.installed(b1):
            with self._window(runtime, info, user,
                              target_kind="actor") as inputs:
                inputs.target_conn.execute(
                    "INSERT INTO decisions (zotero_key, decision,"
                    " exclusion_reason, notes, tags, screened_at, updated_at)"
                    " VALUES ('K1', 'include', '', '', '',"
                    " '2026-11-14T10:00:00+00:00', '2026-11-14T10:00:00+00:00')")
            self.assertTrue(inputs.cleanup_errors,
                            "commit 成功后的清理故障必须经 cleanup_errors 如实暴露")
            self.assertTrue(all(isinstance(item, str) and item
                                for item in inputs.cleanup_errors))
            self.assertEqual(len(control.close_faults), 1)
            faulted_conn, faulted_label = control.close_faults[0]
            self.assertEqual(faulted_label, _label_of(self.users_db))
            self.assertEqual(faulted_conn._ctl_mode, "rw")
            self.assertIn(faulted_conn, inputs.connections)
            self.assertIs(faulted_conn, control.conn_for(self.users_db))
            self.assertTrue(any(str(control.close_failure) in item
                                and "close" in item for item in inputs.cleanup_errors))
            self.assertEqual(control.commit_calls, [_label_of(owner_db)],
                             "先真实目标提交一次，再发生所注入的保留连接 close 故障")
            self._assert_reservation_phases(
                control, users_db=self.users_db, collab_path=collab_path,
                attempts=1)
        with closing(sqlite3.connect(owner_db)) as conn:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM decisions WHERE zotero_key='K1'"
            ).fetchone()[0], 1, "清理故障不得改写提交成功语义")
        self.assertTrue(self._lock_probe(runtime, "p17"), "清理故障后锁仍释放")

    # -- task_kind 门与排序/锁观察 ----------------------------------------------

    def test_personal_task_rejected_no_write(self):
        task_dir, runtime, info, user, owner_db, collab_path = self._world(
            task_id="p18", grants=(), latch=False, member_dbs=(),
            actor="alice")
        before = self._bytes_of((collab_path, self.users_db, owner_db))
        yielded = False
        error = None
        with self._observe() as control:
            try:
                with self._window(runtime, info, user,
                                  target_kind="actor") as inputs:
                    yielded = True
            except BaseException as exc:
                error = exc
        self.assertFalse(yielded, "非 team 明确拒绝，不做 personal 回退")
        self.assertIsNotNone(error)
        self.assertEqual(runtime.sync_calls, 0)
        self._assert_bytes_unchanged(before)
        self.assertTrue(self._lock_probe(runtime, "p18"))

    def test_inputs_order_locks_held_until_commit_and_events(self):
        task_dir, runtime, info, user, owner_db, collab_path = self._world(
            task_id="p19", grants=(("bob", "coder"),), member_dbs=("bob",),
            actor="alice")
        bob_db = task_dir / "bob.db"
        input_paths = (collab_path, owner_db, bob_db, self.users_db)
        self._current_target_kind = "actor"
        with self._observe() as control:
            self._last_control = control
            with self._window(runtime, info, user,
                              target_kind="actor") as inputs:
                # 固定顺序：collab 首、owner 次、其余按规范路径排序
                self.assertIs(inputs.connections[0], inputs.collab_conn)
                self.assertIs(inputs.connections[1], inputs.owner_conn)
                self.assertIs(inputs.owner_conn, control.conn_for(owner_db))
                self.assertIs(inputs.collab_conn, control.conn_for(collab_path))
                others = sorted((bob_db, self.users_db),
                                key=lambda p: _label_of(p))
                self.assertEqual(len(inputs.connections), 4)
                for position, path in enumerate(others, start=2):
                    self.assertIs(inputs.connections[position],
                                  control.conn_for(path),
                                  f"第{position}个保留连接必须是 {path.name}")
                # 规范 id 重新解析 + 锁键
                self.assertIn("p19.", runtime.lookups,
                              "必须经 runtime.task_lookup(info.task_id) 重新解析别名")
                self.assertTrue(runtime.lock_requests)
                self.assertNotIn("p19.", runtime.lock_requests,
                                 "锁键必须用规范 id，不用别名")
                self.assertFalse(self._lock_probe(runtime, "p19"),
                                 "窗口体内规范锁被持有")
                # 全部输入在目标 commit 前仍阻挡外部写
                for path in input_paths:
                    self._assert_external_write_locked(control, path)
            self.assertTrue(self._lock_probe(runtime, "p19"), "退出后锁释放")
        self.assertEqual(runtime.context_calls, 1,
                         "runtime.context 仅在最终保留前调用一次")
        self.assertEqual(runtime.sync_calls, 1, "宿主方法每次尝试调用一次")
        self.assertEqual(runtime.actual_sync_calls, 0, "owner actor 无自同步")
        self.assertEqual(runtime.notify_calls, 0)
        for label in {_label_of(p) for p in input_paths}:
            self.assertIn(label, control.close_order)
        self._last_control = None


if __name__ == "__main__":  # pragma: no cover - unittest entry point
    unittest.main()
