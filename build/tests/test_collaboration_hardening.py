"""2.1.4 协作权限加固回归（U4-P1..P8，对应 core_audit_findings 单元 4）。

- U4-P1/P6：server 模式 legacy（无属主记录）任务的管理操作 409 fail-closed
  （提示属主迁移）；desktop 模式维持历史可管理性；属主解析异常 -> 503。
- U4-P2：注册拒绝与 collaboration.db 保留名冲突的用户名；存量冲突账号
  登录返回改名指引（不阻断、不破坏数据）。
- U4-P3：成员私有 coding scheme 编辑不再 bump owner 全局版本、不广播，
  全员后续写不 409；owner 级方案编辑仍推进版本。
- U4-P4：协作 roles 的 GET/POST/DELETE 在 desktop 模式与 invite 同样 404。
- U4-P5：consensus/freeze 写前在同一把任务锁内复查授权，撤销后写入被拒。
- U4-P7：list_participant_ids 确定性否定返回空表；不确定故障（锁/损坏）
  抛 OwnerUnavailableError/CollaborationStoreCorruptError（R6-C6：锁窗口
  伪空表统计清零曾真实发生），notify 记日志跳过。
- U4-P8：current_owner_db 传入 task_id 做绑定校验；get_owner 缺 task_id
  时 fail-closed 返回 None。
"""
import os
import sys
import json
import shutil
import sqlite3
import tempfile
import unittest
import uuid
from contextlib import closing, contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
fixture = tempfile.TemporaryDirectory(prefix="rf_collab_hardening_")
os.environ["COBOOKSHELF_DATA_DIR"] = fixture.name
os.environ["REVIEWFLOW_MODE"] = "server"
from fastapi.testclient import TestClient

from custom_backend import auth as auth_mod
from custom_backend import security as backend_security
from custom_backend.main import app, DATA_DIR
from coscreen import coding as coding_mod
from coscreen import coding_consensus, coding_evidence, db as db_mod
from coscreen import task_collaboration, tasks as tasks_mod
from coscreen.models import Article


class CollaborationHardeningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.client.__enter__()
        # 不走 HTTP /api/auth/register：全套 pytest 里 TestClient 的 client.host
        # 固定，多模块注册共享同一限流桶会撞 429；直接建用户并签发令牌。
        cls.users = []
        for i in range(3):
            user = auth_mod.create_user(f"collab{i}@example.test", "Collab-Test-2026")
            cls.users.append({"user_id": user["id"], "username": user["username"],
                              "token": auth_mod.create_access_token(user["id"], user["username"])})
        # 门禁墙钟（类级模板库，同 test_report_evidence 手法）：原每用例首个
        # 请求都付一遍全量首建迁移链（~130ms）；类级快照库字节，每用例
        # POST 后复制（~3ms），首个请求即走热路径。隔离与内容同源自建。
        cls._slug = tasks_mod.slugify_screener(cls.users[0]["username"])
        template_task = cls.client.post(
            "/api/tasks", json={"name": "collab-template"},
            headers={"Authorization": "Bearer " + cls.users[0]["token"]}).json()["task_id"]
        ctx0 = app.state.review_workflow.context(template_task, cls.users[0])
        cls._template_db_bytes = Path(ctx0.owner_db).read_bytes()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        fixture.cleanup()

    def auth(self, i=0):
        return {"Authorization": "Bearer " + self.users[i]["token"]}

    def setUp(self):
        self.task = self.client.post(
            "/api/tasks", json={"name": self._testMethodName}, headers=self.auth()).json()["task_id"]
        self.info = tasks_mod.find_task(self.task, DATA_DIR)
        (self.info.dir / (self._slug + ".db")).write_bytes(self._template_db_bytes)
        self.store = task_collaboration.shared_store_path(self.info.dir)
        self.owner_id = self.users[0]["user_id"]

    @contextmanager
    def _mode(self, value):
        old = os.environ.get("REVIEWFLOW_MODE")
        os.environ["REVIEWFLOW_MODE"] = value
        try:
            yield
        finally:
            if old is None:
                os.environ.pop("REVIEWFLOW_MODE", None)
            else:
                os.environ["REVIEWFLOW_MODE"] = old

    @contextmanager
    def _limiter_budget(self):
        """借用限流桶做端点级断言，用后恢复，不占全套 pytest 的共享额度。"""
        events = backend_security.login_limiter._events
        saved = dict(events)
        events.clear()
        try:
            yield
        finally:
            events.clear()
            events.update(saved)

    def _legacy_task(self) -> str:
        """创建后移除协作库，模拟创建于协作机制之前的 legacy 任务。"""
        task = self.client.post(
            "/api/tasks", json={"name": "legacy-" + uuid.uuid4().hex[:6]},
            headers=self.auth()).json()["task_id"]
        info = tasks_mod.find_task(task, DATA_DIR)
        task_collaboration.shared_store_path(info.dir).unlink()
        return task

    def _task_ids(self, actor=0):
        return [t["task_id"] for t in
                self.client.get("/api/tasks", headers=self.auth(actor)).json()["tasks"]]

    # ------------------------------------------------------------------
    # U4-P1 / U4-P6：legacy fail-open -> server 模式 409 fail-closed
    # ------------------------------------------------------------------

    def test_u4p1_p6_server_legacy_management_fail_closed_409(self):
        task = self._legacy_task()
        for actor in (0, 2):  # 原属主与陌生账号同口径：409 + 属主迁移提示
            for method, path, kwargs in (
                ("put", f"/api/tasks/{task}", {"json": {"name": "hijack"}}),
                ("delete", f"/api/tasks/{task}", {}),
            ):
                r = getattr(self.client, method)(path, headers=self.auth(actor), **kwargs)
                self.assertEqual(r.status_code, 409, (method, actor, r.text))
                self.assertEqual(r.json()["detail"]["error_code"], "collaboration_owner_unset")
        # 协作管理同样 409（原属主），列表保持隐藏（server）
        r = self.client.get(f"/api/tasks/{task}/collaboration/roles", headers=self.auth())
        self.assertEqual(r.status_code, 409)
        self.assertNotIn(task, self._task_ids(2))

    def test_u4p1_desktop_legacy_stays_manageable_and_visible(self):
        task = self._legacy_task()
        with self._mode("desktop"):
            r = self.client.put(f"/api/tasks/{task}", json={"name": "renamed-ok"},
                                headers=self.auth())
            self.assertEqual(r.status_code, 200, r.text)
            self.assertIn(task, self._task_ids())

    def test_u4p1_owner_resolution_failure_maps_to_503(self):
        # 损坏协作库 -> sqlite3.DatabaseError -> OwnerUnavailableError -> 503
        self.store.write_bytes(b"this is not a sqlite database at all")
        with self.assertRaises(task_collaboration.OwnerUnavailableError):
            task_collaboration.get_owner(self.store, task_id=self.task)
        r = self.client.put(f"/api/tasks/{self.task}", json={"name": "x"}, headers=self.auth())
        self.assertEqual(r.status_code, 503, r.text)
        r = self.client.get(f"/api/tasks/{self.task}/team-progress", headers=self.auth())
        self.assertEqual(r.status_code, 503, r.text)
        # 任务列表不因单个任务的解析失败而 5xx：该任务本轮按不可见降级
        r = self.client.get("/api/tasks", headers=self.auth())
        self.assertEqual(r.status_code, 200)
        self.assertNotIn(self.task, self._task_ids())

    def test_u4p1_unbound_store_is_legacy_not_owner(self):
        # 目录整拷成新 task_id：绑定不匹配 -> legacy（None），不得解析出属主
        copied = self.info.dir.parent / (self.task + "-copy")
        shutil.copytree(self.info.dir, copied)
        self.assertIsNone(task_collaboration.get_owner(
            task_collaboration.shared_store_path(copied), task_id=copied.name))
        # 陌生人对副本目录内的库不可获得任何授权（fail-closed）
        self.assertFalse(task_collaboration.authorize_action(
            task_collaboration.shared_store_path(copied), 999999,
            "consensus:write", task_id=copied.name))
        shutil.rmtree(copied, ignore_errors=True)

    # ------------------------------------------------------------------
    # U4-P2：保留名碰撞
    # ------------------------------------------------------------------

    def test_u4p2_register_rejects_reserved_screener_collision(self):
        self.assertTrue(auth_mod.reserved_screener_collision("collaboration"))
        self.assertTrue(auth_mod.reserved_screener_collision("COLLABORATION"))
        self.assertFalse(auth_mod.reserved_screener_collision("bob"))
        for name in ("collaboration", "COLLABORATION"):
            with self.assertRaises(auth_mod.AuthError):
                auth_mod.create_user(name, "Whatever-Pass-2026")
        with self._limiter_budget():
            r = self.client.post("/api/auth/register",
                                 json={"username": "collaboration", "password": "Whatever-Pass-2026"})
        self.assertEqual(r.status_code, 400, r.text)
        self.assertIn("冲突", r.json()["detail"])

    def test_u4p2_existing_colliding_account_login_gets_guidance(self):
        # 存量账号：直接落库（历史数据不重写、不删除）
        with closing(sqlite3.connect(str(auth_mod._users_db()))) as conn, conn:
            conn.execute(
                "INSERT INTO users (username, password_hash, created_at) VALUES (?,?,?)",
                ("collaboration", auth_mod.hash_password("Legacy-Pass-2026"), "2026-01-01T00:00:00"))
        with self._limiter_budget():
            r = self.client.post("/api/auth/login",
                                 json={"username": "collaboration", "password": "Legacy-Pass-2026"})
            self.assertEqual(r.status_code, 200, r.text)
            warnings = r.json()["warnings"]
            self.assertTrue(warnings and "collaboration.db" in warnings[0])
            # 正常账号登录：无告警
            r = self.client.post("/api/auth/login",
                                 json={"username": self.users[0]["username"], "password": "Collab-Test-2026"})
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.json()["warnings"], [])

    # ------------------------------------------------------------------
    # U4-P3：成员私有方案不污染 owner 版本
    # ------------------------------------------------------------------

    def test_u4p3_member_scheme_edit_never_bumps_owner_version(self):
        r = self.client.post(f"/api/tasks/{self.task}/collaboration/invite",
                             json={"username": self.users[1]["username"], "role": "coder"},
                             headers=self.auth())
        self.assertEqual(r.status_code, 201, r.text)
        settings = self.client.get(f"/api/tasks/{self.task}/workflow-settings",
                                   headers=self.auth()).json()
        version = settings["version"]
        # 成员编辑自己的私有 coding scheme
        r = self.client.post(f"/api/tasks/{self.task}/coding/dimensions",
                             json={"name": "私有备注", "dtype": "text"},
                             headers=self.auth(1))
        self.assertEqual(r.status_code, 201, r.text)
        after = self.client.get(f"/api/tasks/{self.task}/workflow-settings",
                                headers=self.auth()).json()
        self.assertEqual(after["version"], version, "成员私有方案不得推进 owner 全局版本")
        # 全员（含 owner）持旧版本的写不再 409
        r = self.client.put(f"/api/tasks/{self.task}/workflow-settings",
                            json={"expected_version": version, "stages": settings["stages"],
                                  "evidence_required_field_ids": settings["evidence_required_field_ids"]},
                            headers=self.auth())
        self.assertEqual(r.status_code, 200, r.text)
        # owner 级方案编辑仍推进版本（成员需重映射）
        r = self.client.post(f"/api/tasks/{self.task}/coding/dimensions",
                             json={"name": "任务级维度", "dtype": "choice", "options": ["A", "B"]},
                             headers=self.auth())
        self.assertEqual(r.status_code, 201, r.text)
        final = self.client.get(f"/api/tasks/{self.task}/workflow-settings",
                                headers=self.auth()).json()
        self.assertEqual(final["version"], version + 2)  # settings 保存 +1，owner 方案 +1

    # ------------------------------------------------------------------
    # U4-P4：desktop 模式协作授权口
    # ------------------------------------------------------------------

    def test_u4p4_roles_endpoints_gated_to_server_mode(self):
        member_id = self.users[1]["user_id"]
        with self._mode("desktop"):
            checks = [
                self.client.get(f"/api/tasks/{self.task}/collaboration/roles", headers=self.auth()),
                self.client.post(f"/api/tasks/{self.task}/collaboration/roles",
                                 json={"user_id": member_id, "role": "coder"}, headers=self.auth()),
                self.client.delete(f"/api/tasks/{self.task}/collaboration/roles/{member_id}/coder",
                                   headers=self.auth()),
                self.client.post(f"/api/tasks/{self.task}/collaboration/invite",
                                 json={"username": self.users[1]["username"], "role": "coder"},
                                 headers=self.auth()),
            ]
            for r in checks:
                self.assertEqual(r.status_code, 404, (r.request.url, r.text))
        # server 模式恢复可用
        r = self.client.get(f"/api/tasks/{self.task}/collaboration/roles", headers=self.auth())
        self.assertEqual(r.status_code, 200, r.text)

    # ------------------------------------------------------------------
    # U4-P5：consensus/freeze 撤销竞态
    # ------------------------------------------------------------------

    def _seed_consensus_fixture(self):
        ctx = app.state.review_workflow.context(self.task, self.users[0])
        db_mod.upsert_articles(ctx.owner_db, [Article(
            zotero_key="R1", title="Race Probe", doi="10.test/race",
            raw={"source_file": "01-Wiley.ris"})], [])
        coding_mod.add_dimension(ctx.owner_db, "Sex", "choice", ["M", "F"])
        task_collaboration.grant_role(
            self.store, actor_user_id=self.owner_id, user_id=self.users[1]["user_id"],
            role="reconciler", task_id=self.task)

    @contextmanager
    def _racing_revocation(self, action):
        """在首次授权读与写前复查之间完成撤销（TOCTOU 前置条件）。

        首次 authorize 调用返回 True（模拟读发生在撤销提交前），并通过产品
        撤销端点的同款存储路径立即落地撤销；后续调用走真实判定。
        """
        real = task_collaboration.authorize_action
        fired = []

        def racing(store, user_id, act, *, task_id=None):
            if act == action and user_id == self.users[1]["user_id"] and not fired:
                fired.append(act)
                task_collaboration.revoke_role(
                    store, actor_user_id=self.owner_id, user_id=user_id,
                    role="reconciler", task_id=task_id)
                return True
            return real(store, user_id, act, task_id=task_id)

        task_collaboration.authorize_action = racing
        try:
            yield
        finally:
            task_collaboration.authorize_action = real

    def test_u4p5_consensus_write_recheck_rejects_revoked_actor(self):
        self._seed_consensus_fixture()
        body = {"item_key": "R1", "field_key": "1", "value": "M",
                "source": {"note": "probe"}, "rationale": "测试仲裁"}
        with self._racing_revocation("consensus:write"):
            r = self.client.post(f"/api/tasks/{self.task}/coding/consensus",
                                 json=body, headers=self.auth(1))
        self.assertEqual(r.status_code, 403, r.text)
        self.assertEqual(coding_consensus.list_consensus_records(
            self.store, item_key="R1", field_key="1"), [])

    def test_u4p5_freeze_recheck_rejects_revoked_actor(self):
        self._seed_consensus_fixture()
        with self._racing_revocation("consensus:freeze"):
            r = self.client.post(f"/api/tasks/{self.task}/coding/freezes",
                                 json={"name": "v1", "consensus_ids": [999]},
                                 headers=self.auth(1))
        self.assertEqual(r.status_code, 403, r.text)
        self.assertEqual(coding_consensus.list_freezes(self.store), [])

    def test_u4p5_authorized_reconciler_can_still_write(self):
        self._seed_consensus_fixture()
        body = {"item_key": "R1", "field_key": "1", "value": "M",
                "source": {"note": "probe"}, "rationale": "测试仲裁"}
        r = self.client.post(f"/api/tasks/{self.task}/coding/consensus",
                             json=body, headers=self.auth(1))
        self.assertEqual(r.status_code, 201, r.text)
        record_id = r.json()["id"]
        r = self.client.post(f"/api/tasks/{self.task}/coding/freezes",
                             json={"name": "v1", "consensus_ids": [record_id]},
                             headers=self.auth(1))
        self.assertEqual(r.status_code, 201, r.text)

    # ------------------------------------------------------------------
    # U4-P7：list_participant_ids 异常兜底
    # ------------------------------------------------------------------

    def test_u4p7_list_participant_ids_degrades_instead_of_raising(self):
        # task_id 不匹配：按无参与者降级（原为 ValueError 穿透 -> 500）
        self.assertEqual(task_collaboration.list_participant_ids(
            self.store, actor_user_id=self.owner_id, task_id="task-B"), [])
        # 非成员：仍抛 PermissionError（403 语义）
        with self.assertRaises(PermissionError):
            task_collaboration.list_participant_ids(
                self.store, actor_user_id=999999, task_id=self.task)
        # notify 路径：绑定被改写后不再抛异常（内部降级为无参与者）
        ctx = app.state.review_workflow.context(self.task, self.users[0])
        with closing(sqlite3.connect(str(self.store))) as conn, conn:
            conn.execute("UPDATE collaboration_task SET task_id='mismatched-task'")
        app.state.review_workflow.notify(ctx, "settings")  # 不得抛异常
        # team-progress 端点：409 迁移口径，而非 500
        r = self.client.get(f"/api/tasks/{self.task}/team-progress", headers=self.auth())
        self.assertEqual(r.status_code, 409, r.text)

    # ------------------------------------------------------------------
    # 集成轮复审（协作单元）：锁键规范 / 口径统一 / 保留名 / ghost / 回收
    # ------------------------------------------------------------------

    def test_alias_read_endpoints_use_canonical_lock_key(self):
        # 复审 R6-C3 扫尾：GET coding/dimensions 与 coding/scheme 曾按 URL
        # 原始串分锁——别名读 'T.' 与规范写 'T' 不互斥（读迁移中途状态）。
        # MM11-02（复审更正）：尾点别名解析是 Windows 路径语义；POSIX 上
        # find_task('T.') 为 None，别名读 404 于取锁之前——旧断言在 POSIX
        # 上 :371 恒红、:372 恒真（并非"永远绿"）。按实测解析语义分支：
        # 别名可解析（Windows）读必须 200 且只落规范键；不可解析（POSIX）
        # 读必须 404 且不得取任何锁。规范读先落规范键，assertNotIn 不再
        # 是无前提的恒真。
        runtime = app.state.review_workflow
        alias = self.task + "."
        alias_resolves = tasks_mod.find_task(alias, DATA_DIR) is not None
        for endpoint in ("coding/dimensions", "coding/scheme"):
            with self.subTest(endpoint=endpoint, alias_resolves=alias_resolves):
                r = self.client.get(f"/api/tasks/{alias}/{endpoint}",
                                    headers=self.auth())
                if alias_resolves:
                    self.assertEqual(r.status_code, 200, r.text)
                else:
                    self.assertEqual(r.status_code, 404, r.text)
                    self.assertNotIn(alias, runtime._locks)  # 404 于取锁之前
        canonical = self.client.get(f"/api/tasks/{self.task}/coding/dimensions",
                                    headers=self.auth())
        self.assertEqual(canonical.status_code, 200, canonical.text)
        keys = set(runtime._locks.keys())
        self.assertIn(self.task, keys)
        self.assertNotIn(alias, keys)  # 别名读不得新增分键

    def test_alias_read_lock_key_guard_runs_on_posix_via_windows_equivalent_lookup(self):
        # MM11-02：POSIX 上端点级别名读恒 404，"别名读不分键"守护在真实
        # POSIX 路径上结构性不可达。构造 Windows 语义的等价场景：临时让
        # find_task 把本任务的尾点别名解析回同一物理任务（Windows 行为），
        # 两种平台都能真实执行"别名读 → 锁键必须是规范 id"的守护；
        # R6-C3 回归（锁键退回 URL 原始串）时本用例必红。
        runtime = app.state.review_workflow
        alias = self.task + "."
        original = tasks_mod.find_task

        def windows_alias_semantics(task_id, *args, **kwargs):
            return original(self.task if task_id == alias else task_id,
                            *args, **kwargs)

        tasks_mod.find_task = windows_alias_semantics
        try:
            for endpoint in ("coding/dimensions", "coding/scheme"):
                r = self.client.get(f"/api/tasks/{alias}/{endpoint}",
                                    headers=self.auth())
                self.assertEqual(r.status_code, 200, r.text)
        finally:
            tasks_mod.find_task = original
        keys = set(runtime._locks.keys())
        self.assertIn(self.task, keys)
        self.assertNotIn(alias, keys)  # URL 原始串不得成为分键

    def test_report_evidence_alias_can_write_parity(self):
        # 复审：authorize_action 曾用 URL 原始 id——别名路径 can_write=False
        # 误 403（UI 能力漂移）。
        # MM11-02 同根因收口（第 2 轮评审）：尾点别名解析是 Windows 路径
        # 语义；POSIX 上 find_task('T.') 为 None、别名读 404——旧断言
        # r2==200 在真实 POSIX（WSL 实测 404 != 200）必红。按实测语义
        # 分支：可解析（Windows）断言 200 且 can_write 与规范读对齐；
        # 不可解析（POSIX）断言 404 fail-closed。再以 Windows 等价
        # lookup 构造别名解析，两种平台都真实执行 can_write 守护。
        task_collaboration.grant_role(
            self.store, actor_user_id=self.owner_id, user_id=self.users[1]["user_id"],
            role="coder", task_id=self.task)
        r1 = self.client.get(f"/api/tasks/{self.task}/report-evidence", headers=self.auth(1))
        self.assertEqual(r1.status_code, 200, r1.text)
        self.assertTrue(r1.json()["can_write"], r1.text)
        alias = self.task + "."
        alias_resolves = tasks_mod.find_task(alias, DATA_DIR) is not None
        r2 = self.client.get(f"/api/tasks/{alias}/report-evidence", headers=self.auth(1))
        if alias_resolves:
            self.assertEqual(r2.status_code, 200, r2.text)
            self.assertEqual(r1.json()["can_write"], r2.json()["can_write"])
            self.assertTrue(r2.json()["can_write"])
        else:
            self.assertEqual(r2.status_code, 404, r2.text)
        # Windows 等价 lookup 阶段：别名解析指回同一物理任务——POSIX 上
        # 也能真实执行"别名读 can_write 不漂移"守护（authorize_action
        # 回归用 URL 原始 id 时，此阶段必红）。
        original = tasks_mod.find_task

        def windows_alias_semantics(task_id, *args, **kwargs):
            return original(self.task if task_id == alias else task_id,
                            *args, **kwargs)

        tasks_mod.find_task = windows_alias_semantics
        try:
            r4 = self.client.get(f"/api/tasks/{alias}/report-evidence", headers=self.auth(1))
        finally:
            tasks_mod.find_task = original
        self.assertEqual(r4.status_code, 200, r4.text)
        self.assertEqual(r1.json()["can_write"], r4.json()["can_write"])
        self.assertTrue(r4.json()["can_write"])

    def test_list_participant_ids_corrupt_store_raises_not_empty(self):
        # 复审 R6-C6（D2 探针原样回放）：损坏库不得折叠成伪空表
        # （写入垃圾字节＝库文件永久损坏；删表属"未初始化"确定性否定）
        self.store.write_bytes(b"not a sqlite database at all")
        with self.assertRaises(task_collaboration.CollaborationStoreCorruptError):
            task_collaboration.list_participant_ids(
                self.store, actor_user_id=self.owner_id, task_id=self.task)
        with self.assertRaises(task_collaboration.CollaborationStoreCorruptError):
            task_collaboration.is_owner(self.store, self.owner_id, task_id=self.task)

    def test_authorize_and_grant_require_task_id(self):
        # handoff C5：缺 task_id 不得按"任意属主"放行
        with self.assertRaises(ValueError):
            task_collaboration.authorize_action(self.store, self.owner_id, "coding:submit")
        with self.assertRaises(ValueError):
            task_collaboration.grant_role(
                self.store, actor_user_id=self.owner_id,
                user_id=self.users[1]["user_id"], role="coder")

    def test_create_task_rejects_reserved_screener_name(self):
        # 复审 R6-C1（create_task 口）：撞 collaboration.db 保留名的账号
        # 不得成为属主（曾 201 并把整套筛选表灌进协作库）。
        collab_user = auth_mod.create_user("collab-owner@example.test", "Collab-2026-x")
        # _users_db() 而非 DATA_DIR 直拼：全套 pytest 下 main.DATA_DIR 绑定在
        # 首个导入模块的 fixture 目录，先跑模块的 cleanup 会删掉它
        with closing(sqlite3.connect(str(auth_mod._users_db()))) as conn:
            conn.execute("UPDATE users SET username='collaboration' WHERE id=?",
                         (collab_user["id"],))
            conn.commit()
        token = auth_mod.create_access_token(collab_user["id"], "collaboration")
        try:
            r = self.client.post("/api/tasks", json={"name": "collab-owner-task"},
                                 headers={"Authorization": "Bearer " + token})
            self.assertEqual(r.status_code, 409, r.text)
            self.assertEqual(r.json()["detail"]["error_code"], "collaboration_reserved_name")
        finally:
            # 清理：不清理会让后续用例（u4p2 直插 'collaboration'）撞 UNIQUE
            with closing(sqlite3.connect(str(auth_mod._users_db()))) as conn:
                conn.execute("DELETE FROM users WHERE id=?", (collab_user["id"],))
                conn.commit()

    def test_legacy_workflow_settings_marker_no_500(self):
        # 复审：legacy/ghost 任务首次 GET workflow-settings 曾因 screening_
        # rounds.marker 打不开未建库文件 500——现按无轮次标记处理。
        self.store.unlink(missing_ok=True)  # 模拟 legacy（无协作库文件）
        with self._mode("desktop"):  # 复审场景：desktop 模式 legacy 可见可管理
            r = self.client.get(f"/api/tasks/{self.task}/workflow-settings",
                                headers=self.auth())
        self.assertEqual(r.status_code, 200, r.text)

    def test_screening_rounds_works_for_owner_on_fresh_task(self):
        """复审（d85841e 回归）：owner 即 actor 时 context() 曾整段跳过
        screening_rounds.migrate——新任务无 workflow_screen_rounds 表，
        POST /screening-rounds 的 start/finish 500（复现手法：新建任务＋
        1 条 maybe 决策→work-queue remaining_uncertain=1→start/finish）。
        G2-01：曾用不存在的 /screening/record 端点＋宽断言 (200,201,404)，
        请求恒 404、maybe 分支从未执行；改走真实 /decisions 并收紧断言。"""
        from coscreen import db as _db
        from coscreen.models import Article as _Article
        _db.upsert_articles(self.info.dir / (self._slug + ".db"),
                            [_Article(zotero_key="K1", title="T")], [])
        r = self.client.post(f"/api/tasks/{self.task}/decisions",
                             json={"zotero_key": "K1", "decision": "maybe"},
                             headers=self.auth())
        self.assertEqual(r.status_code, 200, r.text)
        r = self.client.get(f"/api/tasks/{self.task}/work-queue/screen",
                            headers=self.auth())
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["remaining_uncertain"], 1, r.text)
        r = self.client.get(f"/api/tasks/{self.task}/workflow-settings",
                            headers=self.auth())
        version = r.json()["version"]
        r = self.client.post(f"/api/tasks/{self.task}/screening-rounds",
                             json={"action": "finish",
                                   "expected_policy_version": version},
                             headers=self.auth())
        self.assertEqual(r.status_code, 200, r.text)

    def test_revoke_recycles_screener_registration(self):
        # 复审 R6-C1 验收 2：撤销后 task.json 的 screeners 登记回收
        r = self.client.post(f"/api/tasks/{self.task}/collaboration/invite",
                             json={"username": self.users[1]["username"], "role": "coder"},
                             headers=self.auth())
        self.assertEqual(r.status_code, 201, r.text)
        self.assertIn(self.users[1]["username"],
                      tasks_mod.find_task(self.task, DATA_DIR).screeners)
        member_id = str(self.users[1]["user_id"])
        r = self.client.delete(
            f"/api/tasks/{self.task}/collaboration/roles/{member_id}/coder",
            headers=self.auth())
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["revoked"])
        self.assertNotIn(self.users[1]["username"],
                         tasks_mod.find_task(self.task, DATA_DIR).screeners)

    # ------------------------------------------------------------------
    # U4-P8：current_owner_db 传 task_id；get_owner 缺省 fail-closed
    # ------------------------------------------------------------------

    def test_u4p8_current_owner_db_binding_checked(self):
        task_collaboration.grant_role(
            self.store, actor_user_id=self.owner_id, user_id=self.users[1]["user_id"],
            role="coder", task_id=self.task)
        ctx_owner = app.state.review_workflow.context(self.task, self.users[0])
        app.state.review_workflow.context(self.task, self.users[1])  # 建成员库
        member_db = self.info.dir / f"{tasks_mod.slugify_screener(self.users[1]['username'])}.db"
        # 原目录：绑定匹配 -> 重定向到 owner 库
        self.assertEqual(coding_evidence.current_owner_db(member_db).resolve(),
                         ctx_owner.owner_db.resolve())
        # 整拷到新任务目录：绑定不匹配 -> 不再重定向（回退原路径）
        copied = self.info.dir.parent / (self.task + "-copy")
        shutil.copytree(self.info.dir, copied)
        member_db_copy = copied / member_db.name
        self.assertEqual(coding_evidence.current_owner_db(member_db_copy).resolve(),
                         member_db_copy.resolve())
        # get_owner 不传 task_id：fail-closed，不再跳过绑定校验
        self.assertIsNone(task_collaboration.get_owner(self.store))
        shutil.rmtree(copied, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
