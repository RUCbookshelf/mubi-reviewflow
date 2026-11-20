"""包 K 团队基线准备正向测试（C12 作者侧；用户裁定根本修法 2026-11-20）。

A 线遗留项：``initialize_task_store`` 的 ``_SCHEMA`` 不含业务角色两表
（``collaboration_business_role`` / ``collaboration_business_role_events``），
产品安装器是 ``task_collaboration._ensure_business_role_schema``；team 协调器
的借用写者（``_management_conn`` ``require_business=True``）**要求表已存在且
不建表**——基线不备齐时全新任务的 PUT business-role 即 503 且不随重试收敛。
A 线在属主首次团队操作时由 hook 层补齐；本包把两表前移到基线准备
（``prepare_team_protocol_storage``），hook 层保留为幂等双保险。

本件是 A 线 ``test_business_role_borrowed_path_prepared_by_owner_hook``
（test 7）的**前移版**：全新建队后**不做任何属主首次团队操作**
（不 team-progress、不 invite、不先 PUT 一次），直接 PUT business-role →
200 且两表已建。

覆盖：
1. 全新建队 → collab 库两表已在（create 的 fresh protocol 准备即基线）；
2. 零属主操作直接 PUT business-role → 真实 HTTP 200 + 行落库；
3. 幂等：同一 prepare 二次调用零 DDL 放大（表/触发器集不变）；
4. 独立 prepare 契约：对仅含 initialize_task_store 四表的"升级前旧库"仍
   幂等补齐两表（前移对存量库同样生效；模拟 A 线缺口态的修复面）；
5. 非借用路径回归：prepare 后 ``list_business_roles`` 可读、两守卫拒 UPDATE。

夹具配方同 ``test_216_owner_auto_prepare.py``（真实 HTTP/真实建任务；
产品导入推迟到 setUpClass，``python -I -B`` 可载；env 白名单
``COBOOKSHELF_DATA_DIR`` + ``REVIEWFLOW_MODE=server``）。

`COBOOKSHELF_DATA_DIR` + `REVIEWFLOW_MODE=server` 由 conftest 钉。
"""
from __future__ import annotations

import importlib
import sqlite3
import unittest
import uuid
from contextlib import closing
from pathlib import Path
from unittest import mock

OWNER = "pkgkowner"
MEMBER = "pkgkmember"
_BUSINESS_TABLES = ("collaboration_business_role",
                    "collaboration_business_role_events")
_BUSINESS_TRIGGERS = ("collaboration_business_role_events_no_update",
                      "collaboration_business_role_events_no_delete")


class RoleBaselineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        from custom_backend import auth as auth_mod
        from custom_backend import main as main_mod

        cls.client = TestClient(main_mod.app)
        cls.client.__enter__()
        cls.main_mod = main_mod
        cls.auth_mod = auth_mod
        user = auth_mod.create_user(OWNER, "PkgK-Role-Baseline-2026")
        cls.owner = {
            "user_id": user["id"], "username": user["username"],
            "token": auth_mod.create_access_token(
                user["id"], user["username"])}
        cls.tasks_mod = importlib.import_module("coscreen.tasks")
        cls.collab_mod = importlib.import_module(
            "coscreen.task_collaboration")
        cls.tsl = importlib.import_module(
            "custom_backend.team_source_lifecycle")
        member = auth_mod.create_user(MEMBER, "PkgK-Role-Baseline-2026")
        cls.member = {
            "user_id": member["id"], "username": member["username"],
            "token": auth_mod.create_access_token(
                member["id"], member["username"])}

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    @classmethod
    def _auth(cls):
        return {"Authorization": "Bearer " + cls.owner["token"]}

    def _new_task(self):
        task_id = self.client.post(
            "/api/tasks", json={"name": "pkgK-" + uuid.uuid4().hex[:8]},
            headers=self._auth()).json()["task_id"]
        info = self.tasks_mod.find_task(task_id, self.main_mod.DATA_DIR)
        self.assertIsNotNone(info)
        return task_id, info, self.main_mod._collaboration_store(info)

    @staticmethod
    def _objects(path: Path, kind: str) -> set:
        with closing(sqlite3.connect(str(path))) as conn:
            return {row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type=?", (kind,))}

    def _assert_business_schema(self, collab):
        tables = self._objects(collab, "table")
        for table in _BUSINESS_TABLES:
            self.assertIn(table, tables, f"collab 缺 {table}")
        triggers = self._objects(collab, "trigger")
        for trigger in _BUSINESS_TRIGGERS:
            self.assertIn(trigger, triggers, f"collab 缺守卫触发器 {trigger}")

    # ------------------------------------------------------------------
    # 1. 全新建队即备齐两表（不待任何属主团队操作）
    # ------------------------------------------------------------------
    def test_fresh_task_has_business_role_tables_without_owner_action(self):
        task_id, info, collab = self._new_task()
        self._assert_business_schema(collab)
        # 铁证：从建任务到本断言之间无任何属主团队端点调用（无 progress/
        # 无 invite/无 PUT）；表已存在即来自基线准备（create 的 fresh
        # protocol 登记前的 P14 prepare）。
        with closing(sqlite3.connect(str(collab))) as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM collaboration_business_role").fetchone()
        self.assertEqual(row[0], 0, "基线准备只建 schema，不写业务行")

    # ------------------------------------------------------------------
    # 2. 基线独立成事：零属主团队操作后直接 PUT business-role → 200，
    #    且**关掉 A 线 hook** 仍成功（A 线 test 7 前移版的最强形态：
    #    不依赖属主 hook，只靠建队基线准备）。
    # ------------------------------------------------------------------
    def test_business_role_put_succeeds_immediately_after_create(self):
        task_id, info, collab = self._new_task()
        self._assert_business_schema(collab)
        # 场景前提：任务须先进入 team 态（P18 首邀请走 legacy 维护路径并
        # latch；这也是 A 线 test 7 的真实演化路径）。首邀请即 A 线 hook
        # 首次可达之处——记录 hook 入口时两表**已在**（基线而非 hook 的产物）。
        self.client.post(
            f"/api/tasks/{task_id}/collaboration/invite",
            json={"username": self.member["username"], "role": "coder"},
            headers=self._auth())
        hook_seen = {}

        def _recording_hook(info_arg, user_arg):
            hook_seen["tables"] = self.main_mod._sqlite_tables_present(collab)
            return False

        with mock.patch("custom_backend.main._ensure_owner_team_storage",
                        side_effect=_recording_hook):
            put = self.client.put(
                f"/api/tasks/{task_id}/collaboration/business-role/"
                f"{self.owner['user_id']}",
                json={"business_role": "reconciler"},
                headers=self._auth())
        self.assertEqual(put.status_code, 200, put.text)
        body = put.json()
        self.assertIn("membership_committed", body,
                      "team 态 PUT 必须走协调器信封")
        self.assertTrue(body["membership_committed"])
        for table in _BUSINESS_TABLES:
            self.assertIn(table, hook_seen.get("tables", set()),
                          f"hook 入口时两表应已由基线准备建好：{table}")
        with closing(sqlite3.connect(str(collab))) as conn:
            choice = conn.execute(
                "SELECT business_role FROM collaboration_business_role"
                " WHERE user_id=?", (self.owner["user_id"],)).fetchone()
            events = conn.execute(
                "SELECT COUNT(*) FROM collaboration_business_role_events"
                " WHERE user_id=?", (self.owner["user_id"],)).fetchone()
        self.assertEqual(choice[0], "reconciler")
        self.assertEqual(events[0], 1, "改角色须留一条 append-only 审计")

    # ------------------------------------------------------------------
    # 3. 幂等：重复 prepare 不改放大 schema（表/触发器集不变）
    # ------------------------------------------------------------------
    def test_repeated_prepare_is_idempotent(self):
        task_id, info, collab = self._new_task()
        tables_before = self._objects(collab, "table")
        triggers_before = self._objects(collab, "trigger")
        for _ in range(2):
            self.tsl.prepare_team_protocol_storage(
                collab_path=collab, source_paths=())
        self.assertEqual(self._objects(collab, "table"), tables_before)
        self.assertEqual(self._objects(collab, "trigger"), triggers_before)

    # ------------------------------------------------------------------
    # 4. 存量旧库（仅 initialize_task_store 四表）→ prepare 幂等补齐
    # ------------------------------------------------------------------
    def test_prepare_fills_legacy_store_missing_business_tables(self):
        task_id, info, collab = self._new_task()
        with closing(sqlite3.connect(str(collab))) as conn, conn:
            conn.execute("DROP TABLE collaboration_business_role")
            conn.execute("DROP TABLE collaboration_business_role_events")
        self.assertFalse(
            set(_BUSINESS_TABLES) & self._objects(collab, "table"),
            "前置失败：缺口模拟后仍含业务角色表")
        self.tsl.prepare_team_protocol_storage(
            collab_path=collab, source_paths=())
        self._assert_business_schema(collab)
        # 补齐后真实 HTTP 借用写路径仍是 200（同队列一任务二写，
        # 证明补齐非一次性假状态）。
        put = self.client.put(
            f"/api/tasks/{task_id}/collaboration/business-role/"
            f"{self.owner['user_id']}",
            json={"business_role": "coder"},
            headers=self._auth())
        self.assertEqual(put.status_code, 200, put.text)

    # ------------------------------------------------------------------
    # 5. 非借用路径回归：读清单可用 + 守卫拒改审计
    # ------------------------------------------------------------------
    def test_business_role_read_and_append_only_guards(self):
        task_id, info, collab = self._new_task()
        put = self.client.put(
            f"/api/tasks/{task_id}/collaboration/business-role/"
            f"{self.owner['user_id']}",
            json={"business_role": "coder"},
            headers=self._auth())
        self.assertEqual(put.status_code, 200, put.text)
        listed = self.client.get(
            f"/api/tasks/{task_id}/collaboration/business-role",
            headers=self._auth())
        self.assertEqual(listed.status_code, 200, listed.text)
        rows = listed.json()["roles"]
        self.assertTrue(any(row["user_id"] == self.owner["user_id"]
                            for row in rows), rows)
        with closing(sqlite3.connect(str(collab))) as conn:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("UPDATE collaboration_business_role_events"
                             " SET new_role='reconciler'")
            conn.rollback()
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("DELETE FROM collaboration_business_role_events")
            conn.rollback()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
