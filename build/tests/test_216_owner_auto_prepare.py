"""包 A 属主成员库自动准备测试（C12 作者侧；用户裁定方案 A 2026-11-20）。

实证缺口：桌面模式创建的任务切服务器模式后，属主本人的成员库不经任何入口
获得团队准备——invite/grant 只准备**被邀成员**库（``_prepare_invited_member_
storage``），属主自身无触发点；属主首次团队操作两次 503（business_role 表缺 +
五张团队表缺，离线手工补齐验证过修法有效）。本件核验修法产品化
（``_ensure_owner_team_storage`` + 三入口 hook）：

1. 属主首次团队读（真实 HTTP team-progress）自动备齐属主库（5 表）；
2. 已备齐时幂等跳过（不重复调用 prepare）；
3. 准备失败如实 typed 503（``team_owner_storage_unprepared``），且重试安全；
4. 属主首次团队写（``_member_mutate_notified`` seam）：prepare 先于 mutate；
5. 非属主成员写不触发属主库准备（P18「成员写不顺手 prepare」口径保持）；
6. 属主管理写入口（真实 HTTP invite）同样自动备齐属主库（被邀成员库仍由
   既有邀请准备负责）；
7. 复审 D1：全新任务协作库缺业务角色两表（initialize_task_store 的 _SCHEMA
   不含；team 协调器借用写者预检要求表已存在且不建表——「set_business_role
   自 ensure 兜底」判断已被证伪：仅非借用分支自 ensure）时，属主 PUT
   business-role 前 hook 经 task_collaboration 自身 ensure 路径补齐两表，
   真实 HTTP 200 且表已建；
8. 复审 D2：就绪检查只读查询异常 → typed 503（不逃逸全局 500）。

夹具配方移植 test_216_management_routes.py=cf421e35（真实 HTTP/真实建任务；
产品导入推迟到 setUpClass，``python -I -B`` 可载；env 白名单
``COBOOKSHELF_DATA_DIR`` + ``REVIEWFLOW_MODE=server`` 由 conftest 钉）。每个
用例经真实 ``POST /api/tasks`` 建新任务——server 建任务只准备 collab/协议行
（``source_paths=()``），属主成员库天然停留在缺口态（等价桌面建任务切服后的
实测态），无需手工 DROP 表。
"""
from __future__ import annotations

import importlib
import sqlite3
import unittest
import uuid
from unittest import mock

OWNER = "pkgaowner"
MEMBER = "pkgamember"
MEMBER2 = "pkgamember2"
_MEMBER_TEAM_TABLES = (
    "team_flow_receipts", "team_flow_receipts_meta",
    "team_terminal_seals", "team_terminal_facts_meta", "appraisal_records")


class _RecordingRuntime:
    """seam 用记录 runtime：mutate 时快照属主库表集（证明 prepare 先行）。"""

    def __init__(self, probe_path, envelope):
        self._probe_path = probe_path
        self._envelope = envelope
        self.mutate_calls = []
        self.tables_at_mutate = None

    def mutate(self, task_id, user, **kwargs):
        from custom_backend import main as main_mod
        self.mutate_calls.append(
            {"task_id": task_id, "user_id": user.get("user_id"),
             "request_id": kwargs.get("request_id")})
        self.tables_at_mutate = main_mod._sqlite_tables_present(self._probe_path)
        return self._envelope


class OwnerAutoPrepareTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        from custom_backend import auth as auth_mod
        from custom_backend import main as main_mod

        cls.client = TestClient(main_mod.app)
        cls.client.__enter__()
        cls.main_mod = main_mod
        cls.users = {}
        for name in (OWNER, MEMBER, MEMBER2):
            user = auth_mod.create_user(name, "PkgA-Owner-Prepare-2026")
            cls.users[name] = {
                "user_id": user["id"], "username": user["username"],
                "token": auth_mod.create_access_token(
                    user["id"], user["username"])}
        cls.tasks_mod = importlib.import_module("coscreen.tasks")

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    @classmethod
    def _auth(cls, name):
        return {"Authorization": "Bearer " + cls.users[name]["token"]}

    def _new_task(self):
        """真实建任务并断言缺口前置：属主库存在（P04 ledger）但缺团队表。"""
        task_id = self.client.post(
            "/api/tasks", json={"name": "pkgA-" + uuid.uuid4().hex[:8]},
            headers=self._auth(OWNER)).json()["task_id"]
        info = self.tasks_mod.find_task(task_id, self.main_mod.DATA_DIR)
        slug = self.tasks_mod.slugify_screener(self.users[OWNER]["username"])
        owner_db = info.dir / f"{slug}.db"
        self.assertTrue(owner_db.is_file(), "create 后属主库应已建（P04 ledger）")
        tables = self.main_mod._sqlite_tables_present(owner_db)
        self.assertNotIn(
            "team_flow_receipts", tables,
            "前置失败：属主库已含团队表（夹具不再是缺口态）")
        return task_id, info, owner_db

    # ------------------------------------------------------------------
    # 1. 属主首次团队读：真实 HTTP team-progress 自动备齐属主库
    # ------------------------------------------------------------------
    def test_owner_first_team_read_prepares(self):
        task_id, info, owner_db = self._new_task()
        resp = self.client.get(
            f"/api/tasks/{task_id}/team-progress", headers=self._auth(OWNER))
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertTrue(body["can_manage"])
        self.assertIn("members", body)
        tables = self.main_mod._sqlite_tables_present(owner_db)
        for table in _MEMBER_TEAM_TABLES:
            self.assertIn(table, tables, f"属主库缺 {table}（读触发未备齐）")

    # ------------------------------------------------------------------
    # 2. 幂等：已备齐时二次读不再触发任何 prepare
    # ------------------------------------------------------------------
    def test_second_read_idempotent_no_reprepare(self):
        task_id, info, owner_db = self._new_task()
        first = self.client.get(
            f"/api/tasks/{task_id}/team-progress", headers=self._auth(OWNER))
        self.assertEqual(first.status_code, 200, first.text)
        with mock.patch(
                "custom_backend.team_source_lifecycle"
                ".prepare_team_protocol_storage") as team_prep, mock.patch(
                "custom_backend.appraisal_api"
                ".prepare_appraisal_storage_for") as appraisal_prep:
            second = self.client.get(
                f"/api/tasks/{task_id}/team-progress",
                headers=self._auth(OWNER))
        self.assertEqual(second.status_code, 200, second.text)
        team_prep.assert_not_called()
        appraisal_prep.assert_not_called()

    # ------------------------------------------------------------------
    # 3. 准备失败：如实 typed 503（不吞）；解除注入后重试安全
    # ------------------------------------------------------------------
    def test_prepare_failure_typed_503_and_retry_safe(self):
        task_id, info, owner_db = self._new_task()
        with mock.patch(
                "custom_backend.team_source_lifecycle"
                ".prepare_team_protocol_storage",
                side_effect=RuntimeError("pkgA-boom")):
            resp = self.client.get(
                f"/api/tasks/{task_id}/team-progress",
                headers=self._auth(OWNER))
        self.assertEqual(resp.status_code, 503, resp.text)
        body = resp.json()["detail"]
        self.assertEqual(body["error_code"], "team_owner_storage_unprepared")
        self.assertEqual(body["message_key"], "team.owner_storage_unprepared")
        self.assertIn("RuntimeError: pkgA-boom", body["details"])
        # 失败不伪成功：属主库仍缺团队表；解除注入后同请求重试即备齐。
        self.assertNotIn("team_flow_receipts",
                         self.main_mod._sqlite_tables_present(owner_db))
        retry = self.client.get(
            f"/api/tasks/{task_id}/team-progress", headers=self._auth(OWNER))
        self.assertEqual(retry.status_code, 200, retry.text)
        tables = self.main_mod._sqlite_tables_present(owner_db)
        for table in _MEMBER_TEAM_TABLES:
            self.assertIn(table, tables)

    # ------------------------------------------------------------------
    # 4. 属主首次团队写：prepare 先于 mutate（写窗/锁外先行）
    # ------------------------------------------------------------------
    def test_owner_first_team_write_prepares_before_mutate(self):
        task_id, info, owner_db = self._new_task()
        envelope = {"outcome": "committed", "idempotent": False,
                    "request_id": "req-pkgA-1",
                    "result": {"writer": {"saved": True}},
                    "headers": {"X-Request-Id": "req-pkgA-1"}}
        runtime = _RecordingRuntime(owner_db, envelope)
        payload, headers = self.main_mod._member_mutate_notified(
            runtime, info, self.users[OWNER],
            op_name="decision", stage="screen", key="KEY1",
            expected_version=1, request_id="req-pkgA-1",
            request_sha256="a" * 64,
            writer=lambda ctx: {"saved": True},
            change_kind="vote", reason="pkgA-test")
        self.assertEqual(payload, {"saved": True})
        self.assertEqual(headers.get("X-Request-Id"), "req-pkgA-1")
        self.assertEqual(len(runtime.mutate_calls), 1)
        self.assertEqual(runtime.mutate_calls[0]["user_id"],
                         self.users[OWNER]["user_id"])
        for table in _MEMBER_TEAM_TABLES:
            self.assertIn(table, runtime.tables_at_mutate,
                          f"mutate 时属主库仍缺 {table}（prepare 未先行）")

    # ------------------------------------------------------------------
    # 5. 非属主成员写：不触发属主库准备（成员写不顺手 prepare 口径保持）
    # ------------------------------------------------------------------
    def test_member_write_does_not_prepare_owner(self):
        task_id, info, owner_db = self._new_task()
        envelope = {"outcome": "committed", "idempotent": False,
                    "request_id": "req-pkgA-2",
                    "result": {"writer": {"saved": True}},
                    "headers": {}}
        runtime = _RecordingRuntime(owner_db, envelope)
        payload, _headers = self.main_mod._member_mutate_notified(
            runtime, info, self.users[MEMBER],
            op_name="decision", stage="screen", key="KEY1",
            expected_version=1, request_id="req-pkgA-2",
            request_sha256="b" * 64,
            writer=lambda ctx: {"saved": True},
            change_kind="vote", reason="pkgA-test")
        self.assertEqual(payload, {"saved": True})
        self.assertEqual(len(runtime.mutate_calls), 1)  # 成员写照常直达 mutate
        self.assertNotIn("team_flow_receipts",
                         self.main_mod._sqlite_tables_present(owner_db),
                         "非属主写不应顺手准备属主库")

    # ------------------------------------------------------------------
    # 6. 属主管理写入口（真实 HTTP invite x2）：首邀请走旧维护路径并 latch
    #    team（恰为生产桌面→服务器演化的真实路径，不做手工 SQL latch）；
    #    此后管理入口进 P14 协调器——属主库自动备齐 + 被邀成员库仍由既有
    #    邀请准备负责（library_prepared=True）。
    # ------------------------------------------------------------------
    def test_management_invite_prepares_owner_and_member(self):
        task_id, info, owner_db = self._new_task()
        # (a) 首邀请：legacy 维护路径（无 P18 信封），latch team；不准备属主库。
        first = self.client.post(
            f"/api/tasks/{task_id}/collaboration/invite",
            json={"username": self.users[MEMBER2]["username"],
                  "role": "coder"},
            headers=self._auth(OWNER))
        self.assertEqual(first.status_code, 201, first.text)
        self.assertNotIn("membership_committed", first.json())  # legacy 形状
        self.assertNotIn(
            "team_flow_receipts",
            self.main_mod._sqlite_tables_present(owner_db),
            "legacy 首邀请不应准备属主库（包 A 只挂团队入口）")
        # (b) 第二邀请：team 任务统一协调器 → 属主库自动备齐（包 A hook）。
        invited = self.client.post(
            f"/api/tasks/{task_id}/collaboration/invite",
            json={"username": self.users[MEMBER]["username"],
                  "role": "coder"},
            headers=self._auth(OWNER))
        self.assertEqual(invited.status_code, 201, invited.text)
        body = invited.json()
        self.assertTrue(body["membership_committed"])
        self.assertTrue(body["role_committed"])
        self.assertTrue(body["library_prepared"])  # 被邀成员库：既有邀请准备
        owner_tables = self.main_mod._sqlite_tables_present(owner_db)
        for table in _MEMBER_TEAM_TABLES:
            self.assertIn(table, owner_tables,
                          f"管理入口触发后属主库仍缺 {table}")
        member_slug = self.tasks_mod.slugify_screener(
            self.users[MEMBER]["username"])
        member_db = info.dir / f"{member_slug}.db"
        member_tables = self.main_mod._sqlite_tables_present(member_db)
        for table in _MEMBER_TEAM_TABLES:
            self.assertIn(table, member_tables,
                          f"被邀成员库缺 {table}（邀请准备回归）")

    # ------------------------------------------------------------------
    # 7. 复审 D1：全新任务协作库缺业务角色两表 → 属主 PUT business-role
    #    （team → 协调器借用写者）前 hook 补齐两表，真实 HTTP 200。
    #    基线事实：initialize_task_store 的 _SCHEMA 不含该两表；team 协调器
    #    借用分支预检要求表已存在且不建表（仅非借用 set_business_role
    #    自 ensure——既有夹具用非借用调用掩盖了盲区）。
    # ------------------------------------------------------------------
    def test_business_role_borrowed_path_prepared_by_owner_hook(self):
        task_id, info, owner_db = self._new_task()
        collab = self.main_mod._collaboration_store(info)
        tables = self.main_mod._sqlite_tables_present(collab)
        self.assertNotIn("collaboration_business_role", tables,
                         "前置失败：全新 collab 已含业务角色表")
        self.assertNotIn("collaboration_business_role_events", tables)
        # 首邀请走 legacy 维护路径 latch team（真实演化；legacy 路径不建
        # 业务角色表——盲区即在此之后暴露）。
        first = self.client.post(
            f"/api/tasks/{task_id}/collaboration/invite",
            json={"username": self.users[MEMBER2]["username"],
                  "role": "coder"},
            headers=self._auth(OWNER))
        self.assertEqual(first.status_code, 201, first.text)
        self.assertNotIn(
            "collaboration_business_role",
            self.main_mod._sqlite_tables_present(collab),
            "legacy 首邀请后协作库仍不应含业务角色表（夹具口径）")
        # PUT business-role（team → 协调器借用写者）：修复前缺表即 503 且不
        # 随重试收敛；修复后 hook 先补齐两表再进协调器。
        put = self.client.put(
            f"/api/tasks/{task_id}/collaboration/business-role/"
            f"{self.users[OWNER]['user_id']}",
            json={"business_role": "reconciler"},
            headers=self._auth(OWNER))
        self.assertEqual(put.status_code, 200, put.text)
        self.assertTrue(put.json()["membership_committed"])
        tables_after = self.main_mod._sqlite_tables_present(collab)
        self.assertIn("collaboration_business_role", tables_after)
        self.assertIn("collaboration_business_role_events", tables_after)

    # ------------------------------------------------------------------
    # 8. 复审 D2：就绪检查只读查询异常 → typed 503（不逃逸全局 500）
    # ------------------------------------------------------------------
    def test_readiness_read_failure_typed_503(self):
        task_id, info, owner_db = self._new_task()
        with mock.patch(
                "custom_backend.main._sqlite_tables_present",
                side_effect=sqlite3.OperationalError("database is locked")):
            resp = self.client.get(
                f"/api/tasks/{task_id}/team-progress",
                headers=self._auth(OWNER))
        self.assertEqual(resp.status_code, 503, resp.text)
        body = resp.json()["detail"]
        self.assertEqual(body["error_code"], "team_owner_storage_unprepared")
        self.assertEqual(body["message_key"], "team.owner_storage_unprepared")
        self.assertIn("readiness check failed", body["details"])
        self.assertIn("OperationalError", body["details"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
