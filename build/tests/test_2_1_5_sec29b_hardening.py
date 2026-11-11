# -*- coding: utf-8 -*-
"""sec29b 修复波（主代理批次）回归钉：复审 21 条中我方八项的行为钉。

- 复审#2（W3）：空锁建窗不抢（size==0 放弃）/非空损坏仍可接管——见
  test_2_1_5_sec29_lock_takeover.py 追加两钉。
- 复审#10（W6）：撤销令牌跨另一次撤销后旧 id 重复 DELETE 仍幂等 200
  （此前 keep=found 裁剪使其变 404）。
- 复审#12（W6）：形状非法 id 的 404 同样执行时序垫片（判据：垫片必调
  coscreen.tasks.find_task，而形状非法的正路 404 在正则前置即返回、
  绝不调它）。
- 复审#3/#11（W6）：存量 users.db 进程内补收紧一次（POSIX 位有组/其他
  权限才 chmod；Windows 登记跳过）。
- 复审#17（W5）：update_check 捕获族补 http.client.HTTPException——
  BadStatusLine/IncompleteRead → 503 而非冒泡 500。
- 复审#19（W5）：release/asset URL 出口拒 userinfo 与反斜杠。
- 复审#18（W7）：apprise 通道十进制/八进制/短写 IPv4 经 inet_aton 归一
  后按私网拦（公网十进制形态对照放行）。
"""
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

fixture = tempfile.TemporaryDirectory(prefix="rf_sec29b_hard_")
os.environ["COBOOKSHELF_DATA_DIR"] = fixture.name
os.environ["REVIEWFLOW_MODE"] = "server"
os.environ["REVIEWFLOW_NOTIFY_SEC29B_URL"] = "json://2130706433:8080/x"

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import http.client  # noqa: E402

from fastapi import HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from coscreen import notify  # noqa: E402
from coscreen import tasks as tasks_mod  # noqa: E402
from custom_backend import auth as auth_mod  # noqa: E402
from custom_backend import update_check  # noqa: E402
from custom_backend.main import app  # noqa: E402


class RevokeCrossIdempotencyPins(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.client.__enter__()
        user = auth_mod.create_user("sec29b@example.test", "Sec29b-Test-2026")
        cls.headers = {"Authorization":
                       "Bearer " + auth_mod.create_access_token(user["id"], user["username"])}
        cls.task = cls.client.post(
            "/api/tasks", json={"name": "sec29b-hardening"},
            headers=cls.headers).json()["task_id"]

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        fixture.cleanup()

    def _create(self):
        return self.client.post(
            f"/api/tasks/{self.task}/screening/ics-tokens", json={},
            headers=self.headers).json()

    def test_revoke_old_id_still_idempotent_across_other_revocations(self):
        a = self._create()["token_id"]
        b = self._create()["token_id"]
        c = self._create()["token_id"]
        self.assertEqual(self.client.delete(
            f"/api/tasks/{self.task}/screening/ics-tokens/{a}",
            headers=self.headers).status_code, 200)
        self.assertEqual(self.client.delete(
            f"/api/tasks/{self.task}/screening/ics-tokens/{b}",
            headers=self.headers).status_code, 200)
        # sec29b 修前：A 已随 B 的撤销写入被裁剪 → 404；修后：跨撤销幂等 200。
        self.assertEqual(self.client.delete(
            f"/api/tasks/{self.task}/screening/ics-tokens/{a}",
            headers=self.headers).status_code, 200)
        self.assertEqual(self.client.delete(
            f"/api/tasks/{self.task}/screening/ics-tokens/{c}",
            headers=self.headers).status_code, 200)

    def test_shape_invalid_404_still_runs_timing_shim(self):
        # 判据：垫片必调 coscreen.tasks.find_task（探 scandir 首目录），
        # 而形状非法 id（长度>200）的正路 404 在 _task_or_404 正则前置即
        # 返回、绝不调 find_task——shim 运行与否由此可观测。
        shape_invalid = "a" * 201
        with mock.patch.object(tasks_mod, "find_task",
                               wraps=tasks_mod.find_task) as spy:
            resp = self.client.get(
                f"/api/tasks/{shape_invalid}/screening/daily-status.ics"
                "?token=whatever")
        self.assertEqual(resp.status_code, 404)
        self.assertGreaterEqual(spy.call_count, 1,
                                "形状非法 404 必须同垫（垫片会探任务目录）")


class AuthExistingDbPermsPins(unittest.TestCase):
    def test_existing_db_tightened_exactly_once_posix_bits(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "users.db"
            db.write_bytes(b"x")
            auth_mod._PERMS_TIGHTENED.clear()
            fake_stat = types.SimpleNamespace(st_mode=0o100644)  # 组/其他可读
            with mock.patch.object(auth_mod.os, "name", "posix"), \
                    mock.patch.object(auth_mod.os, "stat",
                                      return_value=fake_stat), \
                    mock.patch.object(auth_mod.os, "chmod") as mchmod:
                auth_mod._tighten_existing_db_permissions(db)
                auth_mod._tighten_existing_db_permissions(db)
            mchmod.assert_called_once_with(str(db.resolve()), 0o600)

    def test_tightened_db_without_group_bits_no_chmod(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "users.db"
            db.write_bytes(b"x")
            auth_mod._PERMS_TIGHTENED.clear()
            fake_stat = types.SimpleNamespace(st_mode=0o100600)
            with mock.patch.object(auth_mod.os, "name", "posix"), \
                    mock.patch.object(auth_mod.os, "stat",
                                      return_value=fake_stat), \
                    mock.patch.object(auth_mod.os, "chmod") as mchmod:
                auth_mod._tighten_existing_db_permissions(db)
            mchmod.assert_not_called()


class UpdateCheckHardeningPins(unittest.TestCase):
    def test_httpexception_family_maps_503(self):
        class _Boom:
            def open(self, *a, **k):
                raise http.client.BadStatusLine("garbage")

        with mock.patch.object(update_check, "_OPENER", _Boom()):
            with self.assertRaises(HTTPException) as cm:
                update_check.check_latest_release()
        self.assertEqual(cm.exception.status_code, 503)

    def test_url_filter_rejects_userinfo_and_backslash(self):
        for bad in ("http://user:pw@ok.test/", "https://ok.test\\@evil.test",
                    "https://evil.test\\@ok.test", "https://ok.test/x\\y"):
            self.assertIsNone(update_check._http_url_or_none(bad), bad)
        self.assertEqual(update_check._http_url_or_none("https://ok.test/x"),
                         "https://ok.test/x")


class AppriseDecimalIpv4Pins(unittest.TestCase):
    def _send(self, url):
        os.environ["REVIEWFLOW_NOTIFY_SEC29B_URL"] = url
        config = {"url_env": "REVIEWFLOW_NOTIFY_SEC29B_URL", "enabled": True}
        fake = types.ModuleType("apprise")

        class _Engine:
            def add(self, *a, **k):
                return True

            def notify(self, *a, **k):
                return True

        fake.Apprise = _Engine
        with mock.patch.dict(sys.modules, {"apprise": fake}):
            notify._send_apprise(config, "text", timeout=5.0)

    def test_decimal_octal_short_ipv4_blocked(self):
        for url in ("json://2130706433:8080/x", "json://0177.0.0.1:8080/x",
                    "json://127.1:8080/x"):
            with self.assertRaises(notify.NotifyError, msg=url):
                self._send(url)

    def test_public_decimal_ipv4_allowed_to_engine(self):
        self._send("json://134744072:8080/x")  # 8.8.8.8 的十进制形态


if __name__ == "__main__":
    unittest.main()
