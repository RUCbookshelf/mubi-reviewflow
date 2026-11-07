"""R15（2026-11-07 用户裁定）：暂不确定只在初筛第一轮提供。

语义：某文献只要进过当前筛选员的任一复筛轮次快照
（workflow_screen_rounds.snapshot_json 的键，active/closed/released 都算），
该筛选员对它就不能再保存「暂不确定」；之后新导入、从未进过轮次的文献仍算
第一轮，按钮照常。第 2 轮里没处理完的文献进入全文筛选时按原决定带过去；
撤销恢复到 maybe 不受影响（撤销不是新决定）。任务级 show_maybe 开关语义
不变：show_maybe=false 仍回 maybe_disabled。

本文件先于实现写成（红钉先行）：在修复前，`screening_rounds.snapshot_keys`
不存在、`work_item.maybe_allowed` 未下发、第 2 轮 maybe 仍能保存，因此以下
用例按现状为红。
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


class RoundMaybeTests(unittest.TestCase):
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

    def start_round(self, index=0):
        return self.client.post(
            f"/api/tasks/{self.task}/screening-rounds",
            json={"action": "start", "expected_policy_version": self.settings(index)["version"]},
            headers=self.auth(index))

    def finish_round(self, index=0):
        return self.client.post(
            f"/api/tasks/{self.task}/screening-rounds",
            json={"action": "finish", "expected_policy_version": self.settings(index)["version"]},
            headers=self.auth(index))

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

    def assert_maybe_rejected(self, key, *, index=0):
        response = self.decide(key, "maybe", index=index)
        self.assertEqual(response.status_code, 409, response.text)
        detail = response.json()["detail"]
        self.assertIsInstance(detail, dict, detail)
        self.assertEqual(detail.get("code"), "maybe_first_round_only", detail)
        self.assertEqual(detail.get("version"), self.settings(index)["version"], detail)
        self.assertEqual(detail["workflow_settings"]["version"], detail["version"], detail)
        return detail

    # ---- 1. 第 1 轮可存 -------------------------------------------------

    def test_first_round_stores_maybe_and_advertises_it(self):
        for key in KEYS:
            response = self.decide(key, "maybe")
            self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.decisions(), {"K1": "maybe", "K2": "maybe"})
        # 尚未有轮次快照，工作项明确告知「可暂不确定」。
        self.assertEqual(screening_rounds.snapshot_keys(self.db), set())
        self.assertIs(self.work_item("K1").get("maybe_allowed"), True)

    # ---- 2. 第 2 轮起 409，决定不变 ------------------------------------

    def test_second_round_rejects_maybe_without_changing_decision(self):
        for key in KEYS:
            self.assertEqual(self.decide(key, "maybe").status_code, 200)
        started = self.start_round()
        self.assertEqual(started.status_code, 200, started.text)
        self.assertEqual(started.json()["screening_round"]["number"], 2)
        self.assertEqual(set(started.json()["screening_round"]["pending_keys"]), set(KEYS))
        # 轮次进行中：原 maybe 被扣留，全文队列为空。
        self.assertFalse(self.fulltext_keys())
        self.assertFalse(self.work_item("K1").get("maybe_allowed"))
        self.assert_maybe_rejected("K1")
        self.assertEqual(self.decisions()["K1"], "maybe", "被拒时决定必须原样保留")

    # ---- 3. released 之后仍 409 -----------------------------------------

    def test_released_round_still_rejects_maybe_for_same_article(self):
        for key in KEYS:
            self.assertEqual(self.decide(key, "maybe").status_code, 200)
        self.assertEqual(self.start_round().status_code, 200)
        self.assertEqual(self.finish_round().status_code, 200)
        # finish 后原 maybe 仍可进全文；但对同一文献不能再存 maybe。
        self.assertEqual(self.fulltext_keys(), set(KEYS))
        self.assert_maybe_rejected("K1")
        self.assertEqual(self.decisions()["K1"], "maybe")
        self.assertFalse(self.work_item("K1").get("maybe_allowed"))

    # ---- 4. 之后新导入、从未进过轮次的文献仍算第一轮 --------------------

    def test_new_article_never_in_a_round_can_still_store_maybe(self):
        self.assertEqual(self.decide("K1", "maybe").status_code, 200)
        self.assertEqual(self.decide("K2", "include").status_code, 200)
        self.assertEqual(self.start_round().status_code, 200)  # 快照只有 K1
        db_mod.upsert_articles(
            self.db, [Article(zotero_key="K3", title="Study K3")], [])
        response = self.decide("K3", "maybe")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.decisions()["K3"], "maybe")
        self.assertIs(self.work_item("K3").get("maybe_allowed"), True)
        # K1 仍在快照里，照常被拒。
        self.assert_maybe_rejected("K1")

    # ---- 5. 全文筛选不受影响 ---------------------------------------------

    def test_fulltext_maybe_is_unaffected(self):
        self.assertEqual(self.decide("K1", "maybe").status_code, 200)
        self.assertEqual(self.decide("K2", "include").status_code, 200)
        self.assertEqual(self.start_round().status_code, 200)
        self.assertEqual(self.finish_round().status_code, 200)
        response = self.decide("K1", "maybe", stage="fulltext")
        self.assertEqual(response.status_code, 200, response.text)
        rows = self.client.get(
            f"/api/tasks/{self.task}/stage2/decisions", headers=self.auth()).json()["decisions"]
        self.assertEqual({row["zotero_key"]: row["decision"] for row in rows}["K1"], "maybe")
        self.assertIs(self.work_item("K1", stage="fulltext").get("maybe_allowed"), True)

    # ---- 6. show_maybe=false 仍是 maybe_disabled -------------------------

    def test_show_maybe_false_still_reports_maybe_disabled(self):
        for key in KEYS:
            self.assertEqual(self.decide(key, "maybe").status_code, 200)
        self.assertEqual(self.start_round().status_code, 200)
        current = self.settings()
        stages = {name: dict(value) for name, value in current["stages"].items()}
        stages["screen"]["show_maybe"] = False
        response = self.client.put(
            f"/api/tasks/{self.task}/workflow-settings",
            json={"expected_version": current["version"], "stages": stages,
                  "evidence_required_field_ids": current["evidence_required_field_ids"]},
            headers=self.auth())
        self.assertEqual(response.status_code, 200, response.text)
        # 同时在快照内且开关关闭：开关语义优先，仍回既有 maybe_disabled。
        refused = self.decide("K1", "maybe")
        self.assertEqual(refused.status_code, 409, refused.text)
        self.assertEqual(refused.json()["detail"].get("code"), "maybe_disabled",
                         refused.json()["detail"])

    # ---- 7. 撤销恢复到 maybe 不受影响 ------------------------------------

    def test_undo_restore_to_maybe_is_not_blocked(self):
        for key in KEYS:
            self.assertEqual(self.decide(key, "maybe").status_code, 200)
        self.assertEqual(self.start_round().status_code, 200)
        self.assertEqual(self.decide("K1", "include").status_code, 200)
        status = self.client.get(
            f"/api/tasks/{self.task}/undo/screen", headers=self.auth()).json()
        entry = status.get("latest") or {}
        self.assertTrue(entry.get("entry_id"), status)
        restored = self.client.post(
            f"/api/tasks/{self.task}/undo/screen",
            json={"entry_id": entry["entry_id"],
                  "expected_policy_version": self.settings()["version"]},
            headers=self.auth())
        self.assertEqual(restored.status_code, 200, restored.text)
        self.assertEqual(self.decisions()["K1"], "maybe", "撤销恢复的是原决定，不是新决定")
        # 恢复成 maybe 之后，新的「暂不确定」决定仍被拒绝（语义不因撤销放宽）。
        self.assert_maybe_rejected("K1")

    # ---- 8. 成员 A 的轮次不影响成员 B ------------------------------------

    def test_member_round_does_not_affect_another_member(self):
        for index in (1, 2):
            invited = self.client.post(
                f"/api/tasks/{self.task}/collaboration/invite",
                json={"username": self.users[index]["username"], "role": "coder"},
                headers=self.auth())
            self.assertEqual(invited.status_code, 201, invited.text)
        # 成员 A（index=1）：存 maybe 并开启自己的第 2 轮。
        for key in KEYS:
            self.assertEqual(self.decide(key, "maybe", index=1).status_code, 200)
        self.assertEqual(self.start_round(1).status_code, 200)
        self.assertFalse(self.work_item("K1", index=1).get("maybe_allowed"))
        # 成员 B（index=2）的库从未进过轮次：照常可存 maybe。
        for key in KEYS:
            response = self.decide(key, "maybe", index=2)
            self.assertEqual(response.status_code, 200, response.text)
        self.assertIs(self.work_item("K1", index=2).get("maybe_allowed"), True)
        # 属主（index=0）同样不受成员轮次影响。
        self.assertEqual(self.decide("K1", "maybe").status_code, 200)


class SnapshotKeysReadOnlyTests(unittest.TestCase):
    def test_snapshot_keys_empty_without_table_or_file(self):
        with tempfile.TemporaryDirectory(prefix="rf_r15_rounds_unit_") as tmp:
            path = Path(tmp) / "no-rounds.db"
            db_mod.init_db(path)
            self.assertEqual(screening_rounds.snapshot_keys(path), set())
            self.assertEqual(screening_rounds.snapshot_keys(Path(tmp) / "missing.db"), set())


if __name__ == "__main__":
    unittest.main(verbosity=2)
