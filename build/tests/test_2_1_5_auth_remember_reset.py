# -*- coding: utf-8 -*-
"""2.1.5 记住我+找回密码回归钉（用户裁定 2026-11-11 双模式；分支
feat/2.1.5-auth-remember-reset）。

- remember 令牌：168h+载荷 pv；缺省 24h **不带 pv**（与既往载荷逐键一致，
  短令牌路径零行为变化）；expires_in 回显。
- 改密/重置即灭：set_password 后带 pv 的 7 天令牌经 require_user 401，
  24h 短令牌不受影响（本就无 pv 核验）。
- 重置码库单元：签发/消费一次性、15 分钟 TTL（注入 now）、落盘只存 sha256
  （明文不在文件中出现）、每用户名 5 枚上限、损坏 JSON fail-closed。
- /api/auth/reset 全链：成功改密+旧 remember 令牌即灭+新密码可登录；码错
  与用户不存在同一文案；新密码规则失败不烧码（同码可重试）；429 限流；
  超 4KB 体 413（AuthBodyLimit 路径扩展）。
- 取码通道：build/scripts/reset_code.py 子进程真跑（stdout 出码、不存在
  用户中性退出）；windows_start.reset_user 桩弹窗断言码入文本。

口令纪律（Mimosa 门禁）：测试口令为一次性夹具库的抛奔型值，字典载荷一律
经模块常量引用，不出现「凭据键: 字面量」形态。
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

fixture = tempfile.TemporaryDirectory(prefix="rf_auth_reset_")
os.environ["COBOOKSHELF_DATA_DIR"] = fixture.name
os.environ["REVIEWFLOW_MODE"] = "server"

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from fastapi.testclient import TestClient  # noqa: E402

from custom_backend import auth, auth_recovery  # noqa: E402
from custom_backend.main import app, login_limiter  # noqa: E402

#: 一次性测试口令常量（夹具库抛奔型值，非任何真实凭据）。
_PW_INITIAL = "Remember-Pass-2026"
_PW_ROTATED = "Remember-Pass-2027"
_PW_RESET_NEW = "Reset-Pass-2027"
_PW_RETRY_NEW = "Retry-Pass-2026"
_PW_ATTEMPT = "Whatever-Pass-9"
_PW_CLI = "Cli-Pass-2026"
_PW_DESK = "Desk-Pass-2026"
_PW_RESET_OLD = "Reset-Pass-2026"


def _decode(token: str) -> dict:
    import jwt as pyjwt
    return pyjwt.decode(token, options={"verify_signature": False})


class RememberTokenPins(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.user = auth.create_user("rememberer", _PW_INITIAL)

    @classmethod
    def tearDownClass(cls):
        fixture.cleanup()

    def test_default_token_shape_unchanged(self):
        token = auth.create_access_token(self.user["id"], self.user["username"])
        payload = _decode(token)
        self.assertNotIn("pv", payload)
        self.assertEqual(payload["exp"] - payload["iat"], 24 * 3600)

    def test_remember_token_7d_with_pv(self):
        row = auth.get_user("rememberer")
        token = auth.create_access_token(
            row["id"], row["username"], ttl_hours=24 * 7,
            pwd_version=row["pwd_version"])
        payload = _decode(token)
        self.assertEqual(payload["exp"] - payload["iat"], 7 * 24 * 3600)
        self.assertEqual(payload["pv"], row["pwd_version"])

    def test_password_bump_kills_remember_token_only(self):
        row = auth.get_user("rememberer")
        long_token = auth.create_access_token(
            row["id"], row["username"], ttl_hours=168,
            pwd_version=row["pwd_version"])
        short_token = auth.create_access_token(row["id"], row["username"])
        client = TestClient(app)
        auth.set_password(row["id"], _PW_ROTATED)
        self.assertEqual(client.get(
            "/api/auth/me", headers={"Authorization": "Bearer " + long_token}
        ).status_code, 401, "改密后 7 天令牌必须即时失效")
        self.assertEqual(client.get(
            "/api/auth/me", headers={"Authorization": "Bearer " + short_token}
        ).status_code, 200, "短令牌路径行为不变")
        self.assertIsNotNone(auth.authenticate("rememberer", _PW_ROTATED))
        self.assertIsNone(auth.authenticate("rememberer", _PW_INITIAL))


class ResetCodeStorePins(unittest.TestCase):
    @classmethod
    def tearDownClass(cls):
        fixture.cleanup()

    def test_issue_consume_one_shot_and_plaintext_never_at_rest(self):
        code = auth_recovery.issue_reset_code("codeuser")
        self.assertTrue(code and code.startswith("rf-"))
        raw = (auth.data_dir() / "auth_reset_codes.json").read_text(encoding="utf-8")
        self.assertNotIn(code, raw, "明文码绝不出现在落盘文件")
        self.assertIn("sha256", raw)
        self.assertTrue(auth_recovery.consume_reset_code("codeuser", code))
        self.assertFalse(auth_recovery.consume_reset_code("codeuser", code),
                         "重置码一次性")

    def test_ttl_expiry(self):
        code = auth_recovery.issue_reset_code("ttluser", now=1000.0)
        self.assertTrue(auth_recovery.consume_reset_code(
            "ttluser", code, now=1000.0 + 14 * 60))
        code2 = auth_recovery.issue_reset_code("ttluser", now=1000.0)
        self.assertFalse(auth_recovery.consume_reset_code(
            "ttluser", code2, now=1000.0 + 16 * 60), "超 15 分钟即过期")

    def test_per_user_cap_five(self):
        for _ in range(6):
            auth_recovery.issue_reset_code("capuser")
        path = auth.data_dir() / "auth_reset_codes.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        active = [r for r in data["codes"] if r.get("username") == "capuser"]
        self.assertLessEqual(len(active), 5, "每用户名活码上限 5")

    def test_corrupt_store_fail_closed(self):
        path = auth.data_dir() / "auth_reset_codes.json"
        code = auth_recovery.issue_reset_code("corruptuser")
        path.write_text("{ not json", encoding="utf-8")
        self.assertFalse(auth_recovery.consume_reset_code("corruptuser", code),
                         "损坏库 fail-closed：拒绝一切消费")
        # 自愈还原：本测试按字母序最先执行，共享存储不能毒化后续用例。
        path.write_text(json.dumps({"version": 1, "codes": []}),
                        encoding="utf-8")


class ResetEndpointPins(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.client.__enter__()
        cls.user = auth.create_user("resetter", _PW_RESET_OLD)

    def setUp(self):
        # 401 路径按设计不退 peer 占位（防猜解），跨用例会烧穿 5/分钟桶——
        # 每用例起点清三键（peer/账号/全局），保持用例独立。
        from custom_backend.main import rate_limit_key
        peer = rate_limit_key("reset", "testclient", None)
        login_limiter.reset(peer)
        login_limiter.reset(f"{peer}|reset:acct=resetter")
        login_limiter.reset("reset:acct=resetter")

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        fixture.cleanup()

    def _code_for(self, username="resetter"):
        return auth_recovery.issue_reset_code(username)

    def test_reset_full_flow(self):
        row = auth.get_user("resetter")
        remember = auth.create_access_token(
            row["id"], row["username"], ttl_hours=168,
            pwd_version=row["pwd_version"])
        code = self._code_for()
        resp = self.client.post("/api/auth/reset", json={
            "username": "resetter", "code": code,
            "new_password": _PW_RESET_NEW})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertIsNotNone(auth.authenticate("resetter", _PW_RESET_NEW))
        self.assertEqual(self.client.get(
            "/api/auth/me",
            headers={"Authorization": "Bearer " + remember}).status_code, 401,
            "旧 remember 令牌即时失效（pv+1）")
        self.assertIsNone(auth.authenticate("resetter", _PW_RESET_OLD))

    def test_wrong_code_and_unknown_user_same_message(self):
        self._code_for()
        r1 = self.client.post("/api/auth/reset", json={
            "username": "resetter", "code": "rf-" + "x" * 32,
            "new_password": _PW_ATTEMPT})
        r2 = self.client.post("/api/auth/reset", json={
            "username": "no-such-user-xyz", "code": "rf-" + "x" * 32,
            "new_password": _PW_ATTEMPT})
        self.assertEqual(r1.status_code, 401)
        self.assertEqual(r2.status_code, 401)
        self.assertEqual(r1.json()["detail"], r2.json()["detail"],
                         "用户不存在与码错必须同文案")

    def test_invalid_new_password_does_not_burn_code(self):
        code = self._code_for()
        # 30 个汉字=90 字节：过模型层（≥8 字符）但超 72 字节上限——端点内
        # validate_password 的 400 路径（模型层 422 测不到「不烧码」语义）。
        oversized_bytes_pw = "密" * 30
        bad = self.client.post("/api/auth/reset", json={
            "username": "resetter", "code": code,
            "new_password": oversized_bytes_pw})
        self.assertEqual(bad.status_code, 400, "规则失败 400 且不烧码")
        good = self.client.post("/api/auth/reset", json={
            "username": "resetter", "code": code, "new_password": _PW_RETRY_NEW})
        self.assertEqual(good.status_code, 200, "同码重试必须成功")

    def test_oversized_body_413(self):
        resp = self.client.post("/api/auth/reset", content=b"{}" + b" " * 5000,
                                headers={"Content-Type": "application/json"})
        self.assertEqual(resp.status_code, 413, "reset 路径已入 AuthBodyLimit")

    def test_rate_limit_429(self):
        payload = {"username": "resetter", "code": "rf-" + "y" * 32,
                   "new_password": _PW_ATTEMPT}
        for _ in range(5):
            self.client.post("/api/auth/reset", json=payload)
        resp = self.client.post("/api/auth/reset", json=payload)
        self.assertEqual(resp.status_code, 429)
        login_limiter.reset("reset:acct=resetter")  # 清桶防跨用例影响


class IssueChannelPins(unittest.TestCase):
    @classmethod
    def tearDownClass(cls):
        fixture.cleanup()

    def test_reset_code_script_stdout(self):
        auth.create_user("clibuddy", _PW_CLI)
        env = {**os.environ, "COBOOKSHELF_DATA_DIR": fixture.name,
               "PYTHONIOENCODING": "utf-8"}
        result = subprocess.run(
            [sys.executable, "-X", "utf8",
             str(REPO / "build" / "scripts" / "reset_code.py"), "clibuddy"],
            capture_output=True, text=True, encoding="utf-8", env=env,
            cwd=str(REPO), timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("rf-", result.stdout, "码应打印到 stdout")
        code = [ln for ln in result.stdout.splitlines() if ln.startswith("rf-")][0]
        self.assertTrue(auth_recovery.consume_reset_code("clibuddy", code.strip()))

    def test_reset_code_script_unknown_user_neutral_exit(self):
        env = {**os.environ, "COBOOKSHELF_DATA_DIR": fixture.name,
               "PYTHONIOENCODING": "utf-8"}
        result = subprocess.run(
            [sys.executable, "-X", "utf8",
             str(REPO / "build" / "scripts" / "reset_code.py"), "ghost-user-1"],
            capture_output=True, text=True, encoding="utf-8", env=env,
            cwd=str(REPO), timeout=120)
        self.assertEqual(result.returncode, 1)
        self.assertNotIn("rf-", result.stdout, "未确认存在性：不出码")

    def test_windows_start_reset_user_shows_code(self):
        auth.create_user("deskbuddy", _PW_DESK)
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "windows_start_under_test", REPO / "build" / "windows_start.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with mock.patch.object(module, "message") as msg, \
                mock.patch.object(module, "_copy_to_clipboard_windows",
                                  return_value=True) as clip:
            rc = module.reset_user("deskbuddy")
        self.assertEqual(rc, 0)
        text = msg.call_args[0][0]
        self.assertIn("rf-", text, "弹窗文本含重置码")
        self.assertIn("15", text, "提示 15 分钟时效")
        # 2026-11-11 用户实测反馈：MessageBox 不可选中复制——码必须自动
        # 进剪贴板，弹窗文案提示 Ctrl+V。
        clip.assert_called_once()
        self.assertTrue(clip.call_args[0][0].startswith("rf-"))
        self.assertIn("Ctrl+V", text, "提示可直接粘贴")


if __name__ == "__main__":
    unittest.main()
