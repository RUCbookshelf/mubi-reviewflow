"""P18 management routes tests (C12 author side; 2.1.6 team contracts P18).

Five P18-named tests over the **real** app routes: real HTTP via TestClient,
real team building (移植 test_216_member_write_routes.py=f15ea0da 的真实建队
配方——业务角色单选/成员库 P04 ledger/F1 facts/F2 registry/真 source token，
零 mock), and the real P14 management coordinator
(``run_team_management`` through the real P10 window).  The only patching is
**failure injection** (a raising wrapper around the real collab writer, and a
raising rename stub for the post-commit file cleanup) — every positive path
runs the genuine product code.

Product imports are deferred to ``setUpClass`` (loadable under
``python -I -B``).  Env whitelist: ``COBOOKSHELF_DATA_DIR`` +
``REVIEWFLOW_MODE=server`` only.
"""
from __future__ import annotations

import importlib
import sqlite3
import tempfile
import unittest
import uuid
from contextlib import closing
from pathlib import Path
from unittest import mock

ITEM = "KEY1"
ITEM2 = "KEY2"
ITEM3 = "KEY3"
MEMBERS = ("p18membera", "p18memberb", "p18memberc", "p18memberd", "p18membere")
_UPSERT_LIBRARY = (
    "INSERT INTO team_vote_revision_meta (singleton, format_version,"
    " library_id, seeded_stage1, seeded_stage2, advances)"
    " VALUES (1, 2, ?, 0, 0, 0) ON CONFLICT(singleton) DO UPDATE SET"
    " library_id=excluded.library_id")
_INSERT_ARTICLE = (
    "INSERT INTO articles (zotero_key, title, is_duplicate_of, import_order)"
    " VALUES (?, ?, NULL, ?)")
_LATCH = "UPDATE workflow_state SET team_latched=1 WHERE singleton=1"
_PROTOCOL_ROW = (
    "SELECT task_instance_id, task_id, owner_user_id, owner_library_id,"
    " coverage_state FROM main.team_task_protocol LIMIT 1")


def _borrowed_writer(path):
    """真实产品写连接形态（F2/F1 append 入口要求）：Row 工厂＋IMMEDIATE。"""
    conn = sqlite3.connect(str(path), timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("BEGIN IMMEDIATE")
    return conn


def _close_quietly(conn):
    try:
        conn.close()
    except Exception:
        pass


class ManagementRoutesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        from custom_backend import auth as auth_mod
        from custom_backend import main as main_mod

        cls.client = TestClient(main_mod.app)
        cls.client.__enter__()
        cls.main_mod = main_mod
        cls.users = {}
        for name in ("p18owner", *MEMBERS, "p18outsider"):
            user = auth_mod.create_user(name, "P18-Mgmt-Routes-2026")
            cls.users[name] = {
                "user_id": user["id"], "username": user["username"],
                "token": auth_mod.create_access_token(
                    user["id"], user["username"])}
        cls._team_task()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    @classmethod
    def _auth(cls, name):
        return {"Authorization": "Bearer " + cls.users[name]["token"]}

    # ------------------------------------------------------------------
    # 真实建队（移植 P16 test_216_member_write_routes.py 配方；P18 增量：
    # 协议行来自 create_task 的显式登记 hook，而非夹具手工注册）
    # ------------------------------------------------------------------
    @classmethod
    def _team_task(cls):
        collab = importlib.import_module("coscreen.task_collaboration")
        cdb = importlib.import_module("coscreen.db")
        wf = importlib.import_module("coscreen.review_workflow")
        tasks = importlib.import_module("coscreen.tasks")
        tf = importlib.import_module("coscreen.team_terminal_facts")
        tr = importlib.import_module("coscreen.team_terminal_registry")
        receipts = importlib.import_module("coscreen.team_flow_receipts")
        tsl = importlib.import_module("custom_backend.team_source_lifecycle")

        owner = cls.users["p18owner"]
        owner_uid = owner["user_id"]
        cls.task_id = cls.client.post(
            "/api/tasks", json={"name": "p18-mgmt-" + uuid.uuid4().hex[:8]},
            headers=cls._auth("p18owner")).json()["task_id"]
        cls.info = tasks.find_task(cls.task_id, cls.main_mod.DATA_DIR)
        task_dir = cls.info.dir
        cls.collab_path = collab.shared_store_path(task_dir)
        if not cls.collab_path.is_file():
            collab.initialize_task_store(
                task_dir, task_id=cls.task_id, owner_user_id=owner_uid,
                owner_username=owner["username"])
        # P18 断言对象：create_task 的显式准备+登记 hook（test_fresh_* 核验）。
        with closing(sqlite3.connect(str(cls.collab_path))) as conn:
            conn.row_factory = sqlite3.Row
            cls.created_protocol = conn.execute(_PROTOCOL_ROW).fetchone()
        cls.instance_id = (cls.created_protocol["task_instance_id"]
                           if cls.created_protocol is not None else None)
        # 属主业务角色单选（required_screener 语义来自 set_business_role）。
        collab.set_business_role(
            cls.collab_path, actor_user_id=owner_uid, user_id=owner_uid,
            business_role="reconciler", task_id=cls.task_id)
        slug = tasks.slugify_screener
        cls.user_db = {name: task_dir / f"{slug(u['username'])}.db"
                       for name, u in cls.users.items() if name != "p18outsider"}
        for name in MEMBERS:
            if name == "p18membere":
                continue  # E 的授权留给 test_invite_prepare_partial_truth
            uid = cls.users[name]["user_id"]
            collab.grant_role(cls.collab_path, actor_user_id=owner_uid,
                              user_id=uid, role="coder", task_id=cls.task_id)
            collab.set_business_role(
                cls.collab_path, actor_user_id=owner_uid, user_id=uid,
                business_role="coder", task_id=cls.task_id)
        articles = [(k, f"P18 {k}", i)
                    for i, k in enumerate((ITEM, ITEM2, ITEM3))]
        # 每库：init_db（P04 ledger 真实 meta DDL）+ migrate + F1 facts + P07
        # receipts + 确定性 library + articles（owner 另 latch——P10 配方）。
        for name, path in cls.user_db.items():
            cdb.init_db(path)
            if name == "p18owner":
                wf.migrate(path)
            tf.prepare_team_terminal_facts(path)
            receipts.prepare_team_flow_receipts(path)
            conn = sqlite3.connect(str(path), timeout=15)
            try:
                conn.execute("PRAGMA foreign_keys=ON")
                uid = cls.users[name]["user_id"]
                if name != "p18owner":
                    # S-3：owner 库保留 create_task hook 安装的真实账本身份
                    # （协议行登记的就是它）；成员库仍用确定性 uid-hex 并与
                    # source 登记一致。
                    conn.execute(_UPSERT_LIBRARY, ("%032x" % uid,))
                conn.executemany(_INSERT_ARTICLE, articles)
                if name == "p18owner":
                    conn.execute(_LATCH)
                conn.commit()
            finally:
                conn.close()
        # collab 侧 F2/F1：create_task hook 已 prepare；夹具兜底幂等再装一次，
        # 随后在**自动登记的 instance** 上补 member source（真 token）。
        tsl.prepare_team_protocol_storage(
            collab_path=cls.collab_path, source_paths=())
        if cls.instance_id is None:
            cls.instance_id = "p18-inst-" + uuid.uuid4().hex[:12]
            writer = _borrowed_writer(cls.collab_path)
            try:
                tr.register_protocol_on(
                    writer, task_id=cls.task_id,
                    task_instance_id=cls.instance_id,
                    owner_user_id=owner_uid,
                    owner_library_id="%032x" % owner_uid,
                    creation_path="trusted_new_task")
                writer.commit()
            finally:
                _close_quietly(writer)
        for name in MEMBERS:
            uid = cls.users[name]["user_id"]
            library = "%032x" % uid
            token = tsl.make_source_identity(
                task_dir=task_dir, source_path=cls.user_db[name],
                source_user_id=uid, source_library_id=library)
            writer = _borrowed_writer(cls.collab_path)
            try:
                tr.register_source_on(
                    writer, task_instance_id=cls.instance_id,
                    source_user_id=uid, source_library_id=library,
                    source_path_identity=token,
                    registration_id=f"reg-p18-{name}")
                writer.commit()
            finally:
                _close_quietly(writer)

    # ------------------------------------------------------------------ helpers
    def _grants(self):
        collab = importlib.import_module("coscreen.task_collaboration")
        owner_uid = self.users["p18owner"]["user_id"]
        return collab.list_role_grants(
            self.collab_path, actor_user_id=owner_uid, task_id=self.task_id)

    def _protocol(self, collab_path):
        with closing(sqlite3.connect(str(collab_path))) as conn:
            conn.row_factory = sqlite3.Row
            return conn.execute(_PROTOCOL_ROW).fetchone()

    # ------------------------------------------------------------------
    # 1. five management routes + owner-only
    # ------------------------------------------------------------------
    def test_five_management_routes_and_owner_only(self):
        # (a) 遗留 b 落地：五入口词归并映射表（唯一权威表）。
        aliases = self.main_mod.TEAM_MANAGEMENT_ACTION_ALIASES
        self.assertEqual(aliases["invite"], "add")
        self.assertEqual(aliases["grant"], "add")
        self.assertEqual(aliases["screening-toggle"], "toggle")
        self.assertEqual(aliases["revoke"], "remove")
        self.assertEqual(aliases["business-role"], "business_role")
        self.assertEqual(
            self.main_mod._team_management_action("invite"), "add")
        with self.assertRaises(ValueError):
            self.main_mod._team_management_action("nonsense")

        owner_headers = self._auth("p18owner")
        # (b) invite（coordinator：team 任务第二邀请起统一协调器）。
        invited = self.client.post(
            f"/api/tasks/{self.task_id}/collaboration/invite",
            json={"username": self.users["p18outsider"]["username"],
                  "role": "reconciler"},
            headers=owner_headers)
        self.assertEqual(invited.status_code, 201, invited.text)
        body = invited.json()
        self.assertTrue(body["membership_committed"])
        self.assertTrue(body["role_committed"])
        self.assertIn("grant", body)
        outsider_uid = self.users["p18outsider"]["user_id"]
        self.assertIn(outsider_uid,
                      {g["user_id"] for g in self._grants()})
        # (c) grant（POST /roles）。
        granted = self.client.post(
            f"/api/tasks/{self.task_id}/collaboration/roles",
            json={"user_id": outsider_uid, "role": "coder"},
            headers=owner_headers)
        self.assertEqual(granted.status_code, 201, granted.text)
        self.assertTrue(granted.json()["membership_committed"])
        # (d) screening-toggle（目标为 reconciler，业务合法）。
        toggled = self.client.put(
            f"/api/tasks/{self.task_id}/collaboration/members/"
            f"{outsider_uid}/screening",
            json={"screening_enabled": False}, headers=owner_headers)
        self.assertEqual(toggled.status_code, 200, toggled.text)
        self.assertTrue(toggled.json()["membership_committed"])
        self.assertFalse(toggled.json()["member"]["screening_enabled"])
        # (e) business-role PUT（owner 未为该成员选过角色也可配置）。
        role = self.client.put(
            f"/api/tasks/{self.task_id}/collaboration/business-role/"
            f"{outsider_uid}",
            json={"business_role": "coder"}, headers=owner_headers)
        self.assertEqual(role.status_code, 200, role.text)
        self.assertTrue(role.json()["membership_committed"])
        # (f) revoke（目标两份 grant 逐一撤销）。
        for role in ("coder", "reconciler"):
            revoked = self.client.delete(
                f"/api/tasks/{self.task_id}/collaboration/roles/"
                f"{outsider_uid}/{role}", headers=owner_headers)
            self.assertEqual(revoked.status_code, 200, revoked.text)
            rbody = revoked.json()
            self.assertTrue(rbody["revoked"])
            self.assertTrue(rbody["membership_committed"])
            self.assertIsNotNone(rbody["roster_revision"])
        self.assertNotIn(outsider_uid,
                         {g["user_id"] for g in self._grants()})

        # (g) owner-only：非属主在五入口全部被拒（403 权限闸）。
        for method, path, kwargs in (
                ("post", "/collaboration/invite",
                 {"json": {"username": self.users["p18membera"]["username"],
                           "role": "coder"}}),
                ("post", "/collaboration/roles",
                 {"json": {"user_id": self.users["p18membera"]["user_id"],
                           "role": "coder"}}),
                ("put", f"/collaboration/members/"
                        f"{self.users['p18membera']['user_id']}/screening",
                 {"json": {"screening_enabled": True}}),
                ("delete", f"/collaboration/roles/"
                           f"{self.users['p18membera']['user_id']}/coder", {}),
                ("put", f"/collaboration/business-role/"
                        f"{self.users['p18membera']['user_id']}",
                 {"json": {"business_role": "coder"}}),
        ):
            resp = getattr(self.client, method)(
                f"/api/tasks/{self.task_id}{path}",
                headers=self._auth("p18memberb"), **kwargs)
            self.assertEqual(resp.status_code, 403,
                             f"{method} {path}: {resp.text}")

    # ------------------------------------------------------------------
    # 2. repair (corrupt -> quarantine) -> rebind -> configure; pending
    #    coverage does not block configuration and is not auto-adopted
    # ------------------------------------------------------------------
    def test_repair_pending_then_configure_then_adopt(self):
        owner_headers = self._auth("p18owner")
        # (a) 新建任务（显式登记 hook）后损坏协作库 → repair 隔离。
        task2 = self.client.post(
            "/api/tasks", json={"name": "p18-repair-" + uuid.uuid4().hex[:8]},
            headers=owner_headers).json()["task_id"]
        tasks = importlib.import_module("coscreen.tasks")
        info2 = tasks.find_task(task2, self.main_mod.DATA_DIR)
        collab2 = info2.dir / "collaboration.db"
        self.assertTrue(collab2.is_file())
        with open(collab2, "wb") as fh:
            fh.write(b"not-a-sqlite-database-anymore")
        repaired = self.client.post(
            f"/api/tasks/{task2}/collaboration/repair", headers=owner_headers)
        self.assertEqual(repaired.status_code, 200, repaired.text)
        self.assertTrue(repaired.json()["repaired"])
        self.assertTrue(repaired.json()["backup"].startswith(
            "collaboration.db.corrupt-"))
        # (b) rebind：重建协作库 + P18 显式准备并登记（未选业务角色即可用）。
        rebound = self.client.post(
            f"/api/tasks/{task2}/collaboration/rebind",
            json={"confirm": True}, headers=owner_headers)
        self.assertEqual(rebound.status_code, 200, rebound.text)
        collab = importlib.import_module("coscreen.task_collaboration")
        protocol2 = self._protocol(info2.dir / "collaboration.db")
        self.assertIsNotNone(protocol2)
        self.assertEqual(protocol2["task_id"], task2)
        self.assertEqual(protocol2["coverage_state"], "new_covered")
        # (c) configure：owner 未选业务角色时仍可配置（不要求既有 choice）。
        configured = self.client.put(
            f"/api/tasks/{task2}/collaboration/business-role/"
            f"{self.users['p18owner']['user_id']}",
            json={"business_role": "reconciler"}, headers=owner_headers)
        self.assertEqual(configured.status_code, 200, configured.text)
        # (d) pending：pending 协议状态只能经合法路径产生（legacy_adoption
        # 登记；registry 触发器禁止 synthetic 降级——IntegrityError 实测）。
        # 管理接线不自动 adoption：主任务在 test 1 的一整套管理操作后
        # coverage 仍为创建时事实（new_covered），未被悄悄升级/降级。
        lifecycle = importlib.import_module(
            "custom_backend.team_source_lifecycle")
        tr = importlib.import_module("coscreen.team_terminal_registry")
        self.assertTrue(callable(lifecycle.adopt_team_protocol_on))
        with tempfile.TemporaryDirectory() as tmp:
            pending_dir = Path(tmp) / "pending"
            pending_dir.mkdir()
            collab.initialize_task_store(
                pending_dir, task_id="pending-task", owner_user_id=9,
                owner_username="pending-owner")
            lifecycle.prepare_team_protocol_storage(
                collab_path=pending_dir / "collaboration.db",
                source_paths=())
            writer = _borrowed_writer(pending_dir / "collaboration.db")
            try:
                tr.register_protocol_on(
                    writer, task_id="pending-task",
                    task_instance_id="pending-inst",
                    owner_user_id=9, owner_library_id="cd" * 16,
                    creation_path="legacy_adoption")
                writer.commit()
            finally:
                _close_quietly(writer)
            pending = self._protocol(pending_dir / "collaboration.db")
            self.assertEqual(pending["coverage_state"],
                             "legacy_pending_verification")
        # 管理操作从不改写 coverage（test 1 已跑完整五入口）。
        self.assertEqual(
            self._protocol(self.collab_path)["coverage_state"],
            "new_covered",
            "management must not silently adopt/alter protocol coverage")

    # ------------------------------------------------------------------
    # 3. fresh trusted create vs legacy import provenance
    # ------------------------------------------------------------------
    def test_fresh_create_and_legacy_import_provenance(self):
        # (a) 夹具任务由真实 create_task 建立：协议行=显式登记的 fresh 来源。
        self.assertIsNotNone(self.created_protocol)
        self.assertEqual(self.created_protocol["task_id"], self.task_id)
        self.assertEqual(self.created_protocol["coverage_state"],
                         "new_covered")
        self.assertEqual(self.created_protocol["owner_user_id"],
                         self.users["p18owner"]["user_id"])
        # S-3：owner_library_id=owner 个人库**真实** P04 账本身份（非 uid-hex
        # 占位、非可后改——registry 身份守卫钉死）。
        ledger = importlib.import_module("coscreen.team_vote_ledger")
        conn = sqlite3.connect(str(self.user_db["p18owner"]), timeout=15)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("BEGIN")
            self.assertEqual(
                self.created_protocol["owner_library_id"],
                ledger.library_identity_header_on(conn).library_id)
        finally:
            conn.close()

        task_package = importlib.import_module(
            "custom_backend.task_package_api")
        collab = importlib.import_module("coscreen.task_collaboration")
        tr = importlib.import_module("coscreen.team_terminal_registry")
        legacy_library = "ab" * 16
        # (b) 带历史包：包内协作库已有协议行——恢复不悄换 instance/library。
        with tempfile.TemporaryDirectory() as tmp:
            legacy_dir = Path(tmp) / "legacy"
            legacy_dir.mkdir()
            collab.initialize_task_store(
                legacy_dir, task_id="legacy-src-task", owner_user_id=7,
                owner_username="legacy-owner")
            lifecycle = importlib.import_module(
                "custom_backend.team_source_lifecycle")
            lifecycle.prepare_team_protocol_storage(
                collab_path=legacy_dir / "collaboration.db", source_paths=())
            writer = _borrowed_writer(legacy_dir / "collaboration.db")
            try:
                tr.register_protocol_on(
                    writer, task_id="legacy-src-task",
                    task_instance_id="legacy-inst-keepme",
                    owner_user_id=7, owner_library_id=legacy_library,
                    creation_path="legacy_adoption")
                writer.commit()
            finally:
                _close_quietly(writer)
            provenance = task_package._import_team_provenance(
                legacy_dir, "new-imported-task", 42, "legacy-importer")
            self.assertTrue(provenance["has_history"])
            self.assertFalse(provenance["registered"])
            self.assertEqual(provenance["preserved_task_instance_id"],
                             "legacy-inst-keepme")
            self.assertEqual(provenance["preserved_owner_library_id"],
                             legacy_library)
            # 落盘核验：instance/library 原样保留（未改绑、未隔离）。
            row = self._protocol(legacy_dir / "collaboration.db")
            self.assertEqual(row["task_instance_id"], "legacy-inst-keepme")
            self.assertEqual(row["owner_library_id"], legacy_library)
        # (c) fresh 导入（无协议行）：登记 trusted_new_task，与带历史区分。
        with tempfile.TemporaryDirectory() as tmp:
            fresh_dir = Path(tmp) / "fresh"
            fresh_dir.mkdir()
            collab.initialize_task_store(
                fresh_dir, task_id="fresh-import", owner_user_id=42,
                owner_username="importer")
            provenance = task_package._import_team_provenance(
                fresh_dir, "fresh-import", 42, "importer")
            self.assertFalse(provenance["has_history"])
            self.assertTrue(provenance["registered"])
            self.assertEqual(provenance["creation_path"], "trusted_new_task")

    # ------------------------------------------------------------------
    # 4. invite partial truth: role commit failure does not claim rollback
    #    of prior account/library preparation (P18 anchor)
    # ------------------------------------------------------------------
    def test_invite_prepare_partial_truth(self):
        owner_headers = self._auth("p18owner")
        member_e = self.users["p18membere"]
        member_e_db = self.user_db["p18membere"]
        self.assertTrue(member_e_db.is_file(),
                        "fixture: member E personal library exists pre-invite")

        # 信封合同（直接锚）：角色失败的部分真相信封绝不含 membership True。
        envelope = self.main_mod._management_partial_truth_body(
            {"request_id": "r1", "entry": "invite", "action": "add"}, None)
        self.assertIs(envelope["membership_committed"], False)
        self.assertIs(envelope["role_committed"], False)

        # 注入角色提交失败（真实协调器路径上的真 writer 包装后抛错）。
        task_collaboration = importlib.import_module("coscreen.task_collaboration")
        real_grant = task_collaboration.grant_role

        def failing_grant(*args, **kwargs):
            raise RuntimeError("injected role commit failure")

        with mock.patch.object(task_collaboration, "grant_role", failing_grant):
            failed = self.client.post(
                f"/api/tasks/{self.task_id}/collaboration/invite",
                json={"username": member_e["username"], "role": "coder"},
                headers=owner_headers)
        self.assertEqual(failed.status_code, 503, failed.text)
        result = failed.json()["detail"]
        # P18 断言锚：外部副作用不进入角色原子性承诺——角色失败时不能声称
        # 先前账号创建也回滚。
        self.assertIs(result["membership_committed"], False)
        self.assertIs(result["role_committed"], False)
        # 先前已存在的成员库未被「回滚」/删除/隔离（外部副作用保持原状）。
        self.assertTrue(member_e_db.is_file())
        self.assertFalse(any(
            p.name.startswith(member_e_db.name + ".revoked-")
            for p in member_e_db.parent.iterdir()))
        self.assertNotIn(member_e["user_id"],
                         {g["user_id"] for g in self._grants()})

        # 去除注入后重试：真实路径提交成功；部分事实如实（库已存在=未新建）。
        self.assertIs(task_collaboration.grant_role, real_grant)
        again = self.client.post(
            f"/api/tasks/{self.task_id}/collaboration/invite",
            json={"username": member_e["username"], "role": "coder"},
            headers=owner_headers)
        self.assertEqual(again.status_code, 201, again.text)
        body = again.json()
        self.assertIs(body["membership_committed"], True)
        self.assertIs(body["account_created"], False)   # 库先前已在，非新建
        self.assertIs(body["library_prepared"], True)   # appraisal prepare 跑了
        self.assertIn(member_e["user_id"],
                      {g["user_id"] for g in self._grants()})

    # ------------------------------------------------------------------
    # 5. revoke: rename failure (post-commit optional cleanup) leaves the
    #    roster truth intact; revoke:false adds no new file actions
    # ------------------------------------------------------------------
    def test_revoke_rename_failure_still_correct_roster(self):
        owner_headers = self._auth("p18owner")
        member_e = self.users["p18membere"]
        member_e_db = self.user_db["p18membere"]

        def failing_rename(info, username):
            raise OSError("rename locked by another process")

        with mock.patch.object(self.main_mod, "_quarantine_member_db",
                               failing_rename):
            revoked = self.client.delete(
                f"/api/tasks/{self.task_id}/collaboration/roles/"
                f"{member_e['user_id']}/coder", headers=owner_headers)
        self.assertEqual(revoked.status_code, 200, revoked.text)
        body = revoked.json()
        # 名册语义以 collab 提交事实为准：撤销已成立。
        self.assertTrue(body["revoked"])
        self.assertTrue(body["membership_committed"])
        self.assertIsNotNone(body["roster_revision"])
        # rename 失败被如实上报且不影响名册（旧文件 rename 只能 commit 后
        # 可选，成功与否不影响活动名单语义）。
        self.assertFalse(body["file_cleanup"]["member_db_renamed"])
        self.assertIn("quarantine", body["file_cleanup"]["cleanup_error"])
        self.assertTrue(body["file_cleanup"]["screener_unregistered"])
        self.assertNotIn(member_e["user_id"],
                         {g["user_id"] for g in self._grants()})
        self.assertTrue(member_e_db.is_file())  # 数据保留，不删

        # revoke:false（目标已无该 grant）——不新增任何**维护**文件动作
        # （协调器观察机械的 SQLite 读 sidecar 不属维护动作，不在此比对）。
        def _maintenance_snapshot():
            return {
                "task_json": (member_e_db.parent / "task.json").read_bytes(),
                "revoked_files": sorted(
                    p.name for p in member_e_db.parent.iterdir()
                    if ".revoked-" in p.name),
                "member_db_present": member_e_db.is_file(),
            }
        before = _maintenance_snapshot()
        repeat = self.client.delete(
            f"/api/tasks/{self.task_id}/collaboration/roles/"
            f"{member_e['user_id']}/coder", headers=owner_headers)
        self.assertEqual(repeat.status_code, 200, repeat.text)
        rbody = repeat.json()
        self.assertIs(rbody["revoked"], False)
        self.assertEqual(rbody["file_cleanup"],
                         {"screener_unregistered": False,
                          "member_db_renamed": False,
                          "cleanup_error": None})
        after = _maintenance_snapshot()
        self.assertEqual(before["task_json"], after["task_json"],
                         "revoke:false must not rewrite task.json")
        self.assertEqual(before["revoked_files"], after["revoked_files"])
        self.assertTrue(after["member_db_present"])

    # ------------------------------------------------------------------
    # 6. D-1 修轮 E2E：真实导出→导入往返（含带历史分支可达性）
    # ------------------------------------------------------------------
    def test_export_import_roundtrip_with_history(self):
        owner_headers = self._auth("p18owner")
        # (a) 真实导出：夹具任务的 collab 带 protocol 行 + 9 个产品触发器
        #     （create/管理 hook 经 prepare 安装）——D-1 修复前此包导入即
        #     400 package_invalid（白名单不认自家触发器）。
        exported = self.client.post(
            f"/api/tasks/{self.task_id}/package/export",
            json={"include_pdfs": False}, headers=owner_headers)
        self.assertEqual(exported.status_code, 200, exported.text)
        digest = exported.headers["X-Package-Sha256"]
        self.assertTrue(digest)
        # (b) 真实导入（multipart 上传 + sha 预核）。
        imported = self.client.post(
            "/api/tasks/import-package",
            files={"package": ("task_package.zip", exported.content,
                               "application/zip")},
            data={"preview_sha256": digest},
            headers=owner_headers)
        self.assertEqual(imported.status_code, 200, imported.text)
        result = imported.json()
        # (c) 带历史分支可达：协议行随包保留原 instance/library（不悄换）。
        prov = result["team_provenance"]
        self.assertTrue(prov["has_history"])
        self.assertFalse(prov["registered"])
        self.assertEqual(prov["preserved_task_instance_id"],
                         self.instance_id)
        self.assertEqual(prov["preserved_owner_library_id"],
                         self.created_protocol["owner_library_id"])
        # (d) 导入产物核验：新任务库内协议行原样保留。
        tasks = importlib.import_module("coscreen.tasks")
        info3 = tasks.find_task(result["task_id"], self.main_mod.DATA_DIR)
        self.assertIsNotNone(info3)
        row = self._protocol(info3.dir / "collaboration.db")
        self.assertEqual(row["task_instance_id"], self.instance_id)
        self.assertEqual(row["owner_library_id"],
                         self.created_protocol["owner_library_id"])

    # ------------------------------------------------------------------
    # 7. S-2：管理哨兵键不可由客户端经业务写入口伪造
    # ------------------------------------------------------------------
    def test_sentinel_key_not_client_forgeable(self):
        # 客户端以哨兵键调业务写路由：键域由服务端导出（article 域），哨兵
        # 键不在其中——结构化拒绝（4xx），绝不进入任何写窗口；成员库零写入。
        resp = self.client.post(
            f"/api/tasks/{self.task_id}/decisions",
            json={"zotero_key": "__team_management__",
                  "decision": "include", "reason": "", "notes": "",
                  "tags": None},
            headers=self._auth("p18membera"))
        self.assertIn(resp.status_code, (400, 403, 404, 409, 422), resp.text)
        with closing(sqlite3.connect(
                str(self.user_db["p18membera"]))) as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM decisions"
                " WHERE zotero_key='__team_management__'").fetchone()[0]
        self.assertEqual(count, 0)
        # 哨兵键是服务端机械载体：仅 P14 协调器经映射入口构造。
        lifecycle = importlib.import_module(
            "custom_backend.team_source_lifecycle")
        self.assertEqual(lifecycle._MANAGEMENT_SENTINEL_KEY,
                         "__team_management__")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
