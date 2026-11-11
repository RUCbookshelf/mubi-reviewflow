# -*- coding: utf-8 -*-
"""sec29 回归钉（deepseek 异构安全复审 2026-11-11·锁与写闸·写闸 TOCTOU）。

write_guard 的版本前置检查（store.settings 读 → 比对 expected_version）与
端点事务提交之间没有原子性：跨进程并发写（多 worker/桌面与服务器共用数据
目录；单进程已被 write_guard 的 RLock 覆盖）可在窗口内推进版本，后提交者
静默覆盖先提交者。修复=写入事务末尾的 bump_version 条件推进（UPDATE …
AND version=?），rowcount=0 → 409 workflow_version_conflict，由 with conn
回滚本事务已写的数据。

钉四层：①条件推进匹配时正常 +1；②过期 expected_version → 409 且同事务
数据写回滚、版本不动；③前置检查按陈旧视图放行的竞态窗口（模拟跨进程
写在前置读后推进版本）在提交时仍被检出；④settings PUT / sources POST
端点回环（正常路径不因条件推进受影响）。
"""
import os
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

fixture = tempfile.TemporaryDirectory(prefix="rf_sec29_wg_")
os.environ["COBOOKSHELF_DATA_DIR"] = fixture.name
os.environ["REVIEWFLOW_MODE"] = "server"

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fastapi import HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from coscreen import review_workflow as store  # noqa: E402
from custom_backend import auth as auth_mod  # noqa: E402
from custom_backend.main import app  # noqa: E402


class WriteGuardAtomicityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.client.__enter__()
        # 直建用户+签发令牌（不走 /api/auth/register：TestClient host 固定，
        # 多套件共享限流桶会撞 429——同 test_collaboration_hardening 手法）。
        user = auth_mod.create_user("sec29wg@example.test", "Sec29-Test-2026")
        cls.user = {"user_id": user["id"], "username": user["username"],
                    "token": auth_mod.create_access_token(user["id"], user["username"])}
        cls.task_id = cls.client.post(
            "/api/tasks", json={"name": "sec29-write-guard"},
            headers={"Authorization": "Bearer " + cls.user["token"]}).json()["task_id"]
        cls.runtime = app.state.review_workflow
        cls.headers = {"Authorization": "Bearer " + cls.user["token"]}

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        fixture.cleanup()

    # -- 小工具 ----------------------------------------------------------
    def _ctx(self):
        return self.runtime.context(self.task_id, self.user)

    def _version(self):
        return store.settings(self._ctx().owner_db)["version"]

    def _stages_json(self):
        with closing(store.connect(self._ctx().owner_db)) as conn:
            return conn.execute(
                "SELECT stages_json FROM workflow_state WHERE singleton=1"
            ).fetchone()[0]

    # -- 钉 ①：匹配推进 --------------------------------------------------
    def test_matching_bump_advances(self):
        before = self._version()
        with closing(store.connect(self._ctx().owner_db)) as conn, conn:
            self.runtime.bump_version(conn, expected_version=before)
        self.assertEqual(self._version(), before + 1)

    # -- 钉 ②：过期 409 + 同事务回滚 ------------------------------------
    def test_stale_bump_409_and_transaction_rollback(self):
        ctx = self._ctx()
        with closing(store.connect(ctx.owner_db)) as conn, conn:
            self.runtime.bump_version(conn)  # 确保存在「过期」版本可用
        stale = self._version() - 1
        current = self._version()
        original = self._stages_json()
        with self.assertRaises(HTTPException) as cm:
            with closing(store.connect(ctx.owner_db)) as conn, conn:
                conn.execute(
                    "UPDATE workflow_state SET stages_json=? WHERE singleton=1",
                    ('{"poison": true}',))
                self.runtime.bump_version(conn, expected_version=stale, ctx=ctx)
        self.assertEqual(cm.exception.status_code, 409)
        self.assertEqual(cm.exception.detail["code"], "workflow_version_conflict")
        self.assertEqual(cm.exception.detail["version"], current)
        self.assertEqual(self._version(), current)       # 版本未被过期方推进
        self.assertEqual(self._stages_json(), original)  # 毒写已随 409 回滚

    # -- 钉 ③：前置检查被陈旧视图骗过，提交时仍检出 ----------------------
    def test_guard_race_window_caught_at_commit(self):
        ctx = self._ctx()
        with closing(store.connect(ctx.owner_db)) as conn, conn:
            self.runtime.bump_version(conn)
            self.runtime.bump_version(conn)  # 模拟跨进程写者连推两版
        stale = self._version() - 2          # 并发写**前**的视图版本
        original = self._stages_json()
        real_settings = store.settings

        def stale_view(db, *_a, **_k):
            data = dict(real_settings(db))
            data["version"] = stale
            return data

        with mock.patch.object(store, "settings", side_effect=stale_view):
            with self.assertRaises(HTTPException) as cm:
                with self.runtime.write_guard(
                        self.task_id, self.user, "screen",
                        expected_version=stale) as guarded:
                    with closing(store.connect(guarded.owner_db)) as conn, conn:
                        conn.execute(
                            "UPDATE workflow_state SET stages_json=? "
                            "WHERE singleton=1", ('{"poison2": true}',))
                        self.runtime.bump_version(
                            conn, expected_version=stale, ctx=guarded)
        self.assertEqual(cm.exception.status_code, 409)
        self.assertEqual(cm.exception.detail["code"], "workflow_version_conflict")
        self.assertEqual(self._stages_json(), original)  # 竞态窗内毒写被回滚

    # -- 钉 ④：端点正常路径回环 -----------------------------------------
    def test_settings_put_roundtrip_conditional_path_ok(self):
        # settings PUT 走条件推进的完整 HTTP 链：正常路径必须 200 且恰好 +1。
        # （sources 的 save_source 与本端点共用同一条件 bump_version——其
        # 手工建源本就被产品规则 400 拒绝，机制层已由钉②③覆盖。）
        version = self.client.get(
            f"/api/tasks/{self.task_id}/workflow-settings",
            headers=self.headers).json()["version"]
        stages = {s: {"mode": "all", "source_ids": []} for s in store.STAGES}
        resp = self.client.put(
            f"/api/tasks/{self.task_id}/workflow-settings",
            json={"expected_version": version, "stages": stages,
                  "evidence_required_field_ids": []},
            headers=self.headers)
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["version"], version + 1)
        self.assertEqual(self._version(), version + 1)


if __name__ == "__main__":
    unittest.main()
