"""R15 换钉（2.1.5 W1）：暂不确定正式两阶段全拒、轮次移除、子模块保留。

旧钉（2.1.4 r15）→ 新钉（2.1.5 W1）对照：
- 旧：第 1 轮可存 maybe，第 2 轮起 409 ``maybe_first_round_only``
  → 新：初筛与复筛**全程** 409 ``maybe_disabled`` +
    ``message_key='workflow.maybe_disabled'``，决定不落盘。
- 旧：``show_maybe=false`` 才回 maybe_disabled
  → 新：``show_maybe`` 退役（GET 剥离 / PUT 忽略），maybe 恒拒。
- 旧：``POST /screening-rounds`` start/finish 200，queue 带
  ``screening_round`` / ``remaining_uncertain``
  → 新：路由 404，queue/settings 载荷无轮次字段。
- 旧：复筛 maybe 合法（全文 maybe_allowed=true）
  → 新：全文同样 maybe_allowed=false、maybe 保存 409。
- 旧：``screening_rounds`` 快照决定 maybe 可用性
  → 新：轮次机制移除，但 ``coscreen.screening_rounds`` 模块与历史表**保留**，
    marker/snapshot 读取对缺失库/表不崩（回滚兼容）。
- 撤销恢复历史 maybe 不是新决定：不受写闸影响（旧语义保留）。
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# 与既有服务端夹具同法：先设环境再导入 main（多测试模块共享首个导入的环境）。
fixture = tempfile.TemporaryDirectory(prefix="rf_r15_round_maybe_")
os.environ["COBOOKSHELF_DATA_DIR"] = fixture.name
os.environ["REVIEWFLOW_MODE"] = "server"

from fastapi.testclient import TestClient

from custom_backend import auth as auth_mod
from custom_backend.main import app, DATA_DIR
from coscreen import db as db_mod
from coscreen import screening_rounds
from coscreen import tasks as tasks_mod
from coscreen.models import Article

KEYS = ("K1", "K2")


class BinaryScreeningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.client.__enter__()
        cls.users = []
        for index in range(3):
            user = auth_mod.create_user(
                f"r15round{index}@example.test", "R15-Round-Test-2026")
            cls.users.append({
                "user_id": user["id"],
                "username": user["username"],
                "token": auth_mod.create_access_token(user["id"], user["username"]),
            })

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        fixture.cleanup()

    def auth(self, index=0):
        return {"Authorization": "Bearer " + self.users[index]["token"]}

    def setUp(self):
        response = self.client.post(
            "/api/tasks", json={"name": self._testMethodName}, headers=self.auth())
        self.assertEqual(response.status_code, 201, response.text)
        self.task = response.json()["task_id"]
        self.info = tasks_mod.find_task(self.task, DATA_DIR)
        self.db = self.info.dir / (
            tasks_mod.slugify_screener(self.users[0]["username"]) + ".db")
        db_mod.init_db(self.db)
        db_mod.upsert_articles(
            self.db, [Article(zotero_key=key, title=f"Study {key}") for key in KEYS], [])
        # 让 actor 库先完成 v8 标记：之后直接种入的 maybe 属于「历史数据」，
        # 用于验证读路径/撤销边界，不会被一次性迁移清掉。
        self.assertIsInstance(self.settings().get("version"), int)

    # ---- 夹具 ---------------------------------------------------------

    def decide(self, key, decision, *, index=0, stage="screen"):
        suffix = "/stage2/decisions" if stage == "fulltext" else "/decisions"
        return self.client.post(
            f"/api/tasks/{self.task}{suffix}",
            json={"zotero_key": key, "decision": decision},
            headers=self.auth(index))

    def settings(self, index=0):
        return self.client.get(
            f"/api/tasks/{self.task}/workflow-settings", headers=self.auth(index)).json()

    def decisions(self, index=0):
        rows = self.client.get(
            f"/api/tasks/{self.task}/decisions", headers=self.auth(index)).json()["decisions"]
        return {row["zotero_key"]: row["decision"] for row in rows}

    def fulltext_keys(self, index=0):
        return {row["zotero_key"] for row in self.client.get(
            f"/api/tasks/{self.task}/work-queue/fulltext",
            headers=self.auth(index)).json()["queue"]}

    def work_item(self, key, *, index=0, stage="screen"):
        response = self.client.get(
            f"/api/tasks/{self.task}/work-items/{stage}/{key}", headers=self.auth(index))
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def assert_maybe_rejected(self, key, *, index=0, stage="screen"):
        response = self.decide(key, "maybe", index=index, stage=stage)
        self.assertEqual(response.status_code, 409, response.text)
        detail = response.json()["detail"]
        self.assertIsInstance(detail, dict, detail)
        self.assertEqual(detail.get("code"), "maybe_disabled", detail)
        self.assertEqual(detail.get("message_key"), "workflow.maybe_disabled", detail)
        self.assertEqual(detail.get("version"), self.settings(index)["version"], detail)
        self.assertEqual(detail["workflow_settings"]["version"], detail["version"], detail)
        return detail

    # ---- 1. 两阶段全拒 --------------------------------------------------

    def test_maybe_is_rejected_in_both_formal_stages(self):
        self.assert_maybe_rejected("K1")
        self.assertEqual(self.decisions(), {}, "被拒的初筛 maybe 不得落盘")
        self.assertEqual(self.decide("K2", "include").status_code, 200)
        self.assert_maybe_rejected("K2", stage="fulltext")
        rows = self.client.get(
            f"/api/tasks/{self.task}/stage2/decisions",
            headers=self.auth()).json()["decisions"]
        self.assertEqual(rows, [], "被拒的复筛 maybe 不得落盘")
        self.assertFalse(self.work_item("K1").get("maybe_allowed"))
        self.assertFalse(self.work_item("K2", stage="fulltext").get("maybe_allowed"))

    # ---- 2. 复筛入队仅 include ------------------------------------------

    def test_fulltext_queue_is_include_only(self):
        db_mod.save_decision(self.db, "K1", "maybe")
        db_mod.save_decision(self.db, "K2", "include")
        self.assertEqual(self.fulltext_keys(), {"K2"})
        self.assertFalse(self.work_item("K1", stage="fulltext").get("maybe_allowed"))

    # ---- 3. 轮次路由 404 / 载荷字段下线 ----------------------------------

    def test_round_route_is_unregistered_and_payloads_have_no_round_fields(self):
        removed = self.client.post(
            f"/api/tasks/{self.task}/screening-rounds",
            json={"action": "start", "expected_policy_version": self.settings()["version"]},
            headers=self.auth())
        self.assertEqual(removed.status_code, 404, removed.text)
        queue = self.client.get(
            f"/api/tasks/{self.task}/work-queue/screen",
            headers=self.auth()).json()
        self.assertNotIn("screening_round", queue)
        self.assertNotIn("remaining_uncertain", queue)
        settings = self.settings()
        self.assertNotIn("screening_round_marker", settings)
        for stage in ("screen", "fulltext"):
            self.assertNotIn("show_maybe", settings["stages"][stage])

    def test_show_maybe_put_is_ignored_not_422(self):
        current = self.settings()
        stages = {name: dict(value) for name, value in current["stages"].items()}
        stages["screen"]["show_maybe"] = False
        stages["fulltext"]["show_maybe"] = False
        response = self.client.put(
            f"/api/tasks/{self.task}/workflow-settings",
            json={"expected_version": current["version"], "stages": stages,
                  "evidence_required_field_ids": current["evidence_required_field_ids"]},
            headers=self.auth())
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn("show_maybe", response.json()["stages"]["screen"])
        self.assert_maybe_rejected("K1")

    # ---- 4. 撤销恢复历史 maybe 不是新决定 --------------------------------

    def test_undo_restore_of_legacy_maybe_is_not_blocked(self):
        db_mod.save_decision(self.db, "K1", "maybe")
        self.assertEqual(self.decide("K1", "include").status_code, 200)
        status = self.client.get(
            f"/api/tasks/{self.task}/undo/screen", headers=self.auth()).json()
        entry = status.get("latest") or status.get("entry") or {}
        self.assertTrue(entry.get("entry_id"), status)
        restored = self.client.post(
            f"/api/tasks/{self.task}/undo/screen",
            json={"entry_id": entry["entry_id"],
                  "expected_policy_version": self.settings()["version"]},
            headers=self.auth())
        self.assertEqual(restored.status_code, 200, restored.text)
        self.assertEqual(self.decisions()["K1"], "maybe", "撤销恢复的是原决定，不是新决定")

    # ---- 5. 成员独立（写闸不因他人轮次/决定变化） -------------------------

    def test_member_isolation_is_preserved(self):
        for index in (1, 2):
            invited = self.client.post(
                f"/api/tasks/{self.task}/collaboration/invite",
                json={"username": self.users[index]["username"], "role": "coder"},
                headers=self.auth())
            self.assertEqual(invited.status_code, 201, invited.text)
        for index in (0, 1, 2):
            self.assert_maybe_rejected("K1", index=index)


class SnapshotKeysReadOnlyTests(unittest.TestCase):
    """轮次机制移除但模块保留：读取历史快照不得因缺表/缺库崩溃。"""

    def test_snapshot_keys_empty_without_table_or_file(self):
        with tempfile.TemporaryDirectory(prefix="rf_r15_rounds_unit_") as tmp:
            path = Path(tmp) / "no-rounds.db"
            db_mod.init_db(path)
            self.assertEqual(screening_rounds.marker(path), None)
            self.assertEqual(screening_rounds.snapshot_keys(path), set())
            self.assertEqual(screening_rounds.snapshot_keys(Path(tmp) / "missing.db"), set())
            self.assertEqual(screening_rounds.marker(Path(tmp) / "missing.db"), None)


if __name__ == "__main__":
    unittest.main(verbosity=2)
