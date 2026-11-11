# -*- coding: utf-8 -*-
"""2.1.5 记住我+找回密码回归钉（含 authb 复审处置；分支
feat/2.1.5-auth-remember-reset）。

authb（dwfrun-85601901 三域复审）后新增/改写：
- 码库 SQLite 化（H1 跨进程竞态/M8/L13/L16）：明文不落盘、一次性、TTL、
  上限、损坏 fail-closed 改 SQLite 口径断言。
- M1：一切新签令牌（login/register）恒带 pv=可吊销——重置后**短令牌也
  401**；不传 pwd_version 的直调保持旧形状（存量兼容钉）。
- M4：兄弟码回收——重置成功后同用户其余活码立即 401。
- M6：烧码后置——set_password 异常时码可重试（rearm）。
- L1：异形 pv → 401 非 500；L3：删号同名重建旧令牌 401（id 交叉校验）。
- L7：login 响应不回显 pwd_version。
- L15/L20：用户名带空白+正确码 → 200（两侧同键规范化）。
- M7：CLI 数据目录解析（环境变量优先/平台默认，无 CWD 影子库）。
"""
import json
import os
import sqlite3
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

#: 一次性测试口令常量（夹具库抛奔型值，非任何真实凭据；Mimosa 门禁口径）。
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


def _db_path() -> Path:
    return auth.data_dir() / "auth_reset_codes.db"


class RememberTokenPins(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.user = auth.create_user("rememberer", _PW_INITIAL)

    def test_legacy_direct_call_shape_unchanged(self):
        # 兼容钉：不传 pwd_version 的直调（测试/既有调用）载荷与旧版一致
        # （无 pv、24h）——存量令牌 ≤24h 自然过期，不受 pv 核验影响。
        token = auth.create_access_token(self.user["id"], self.user["username"])
        payload = _decode(token)
        self.assertNotIn("pv", payload)
        self.assertEqual(payload["exp"] - payload["iat"], 24 * 3600)

    def test_pwd_version_carry_even_without_ttl(self):
        # authb M1：pwd_version 给定即带 pv（与时长解耦）——一切新签可吊销。
        row = auth.get_user("rememberer")
        token = auth.create_access_token(
            row["id"], row["username"], pwd_version=row["pwd_version"])
        payload = _decode(token)
        self.assertEqual(payload["pv"], row["pwd_version"])
        self.assertEqual(payload["exp"] - payload["iat"], 24 * 3600)

    def test_remember_token_7d_with_pv(self):
        row = auth.get_user("rememberer")
        token = auth.create_access_token(
            row["id"], row["username"], ttl_hours=24 * 7,
            pwd_version=row["pwd_version"])
        payload = _decode(token)
        self.assertEqual(payload["exp"] - payload["iat"], 7 * 24 * 3600)
        self.assertEqual(payload["pv"], row["pwd_version"])

    def test_password_bump_kills_pv_tokens_keeps_legacy(self):
        row = auth.get_user("rememberer")
        pv_token = auth.create_access_token(
            row["id"], row["username"], pwd_version=row["pwd_version"])
        legacy = auth.create_access_token(row["id"], row["username"])
        client = TestClient(app)
        auth.set_password(row["id"], _PW_ROTATED)
        self.assertEqual(client.get(
            "/api/auth/me", headers={"Authorization": "Bearer " + pv_token}
        ).status_code, 401, "带 pv 令牌改密后必须即时失效")
        self.assertEqual(client.get(
            "/api/auth/me", headers={"Authorization": "Bearer " + legacy}
        ).status_code, 200, "存量无 pv 令牌按旧语义（≤24h 自然过期）")


class ResetCodeStorePins(unittest.TestCase):
    def test_issue_consume_one_shot_and_plaintext_never_at_rest(self):
        code = auth_recovery.issue_reset_code("codeuser")
        self.assertTrue(code and code.startswith("rf-"))
        raw = _db_path().read_bytes()
        self.assertNotIn(code.encode("utf-8"), raw, "明文码绝不出现在落盘库")
        self.assertIn(b"reset_codes", raw)
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
        with sqlite3.connect(str(_db_path())) as conn:
            active = conn.execute(
                "SELECT COUNT(*) FROM reset_codes WHERE username=?"
                " AND used_at IS NULL", ("capuser",)).fetchone()[0]
        self.assertLessEqual(active, 5, "每用户名活码上限 5")

    def test_whitespace_username_same_key(self):
        # L15/L20：strip 同键——带空白签发、干净名消费（反之亦然）。
        code = auth_recovery.issue_reset_code("  wsuser  ")
        self.assertTrue(auth_recovery.consume_reset_code("wsuser", code))

    def test_corrupt_store_fail_closed_then_self_heals(self):
        # L13：库文件损坏 → 一切消费 False（不抛 500/不做存在性判别器）；
        # 换路径重建即自愈。在**独立临时库**上做（monkeypatch _db_path）——
        # 共享库上做文件级毒化会因 Windows 句柄时序让 unlink 不稳且级联
        # 毒化后续用例。
        with tempfile.TemporaryDirectory(prefix="rf_corrupt_store_") as tmp:
            isolated = Path(tmp) / "auth_reset_codes.db"
            with mock.patch.object(auth_recovery, "_db_path",
                                   lambda: isolated):
                code = auth_recovery.issue_reset_code("corruptuser")
                self.assertTrue(code)
                isolated.write_bytes(b"not a sqlite file at all")
                self.assertFalse(
                    auth_recovery.consume_reset_code("corruptuser", code),
                    "损坏库 fail-closed：拒绝一切消费")
        # 原路径恢复后新库自愈（真实 _db_path 回来即可重新签发/消费）。
        fresh = auth_recovery.issue_reset_code("corruptuser")
        self.assertTrue(fresh and auth_recovery.consume_reset_code(
            "corruptuser", fresh), "换回好库后自愈")


class ResetEndpointPins(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.client.__enter__()
        cls.user = auth.create_user("resetter", _PW_RESET_OLD)

    @classmethod
    def _code_for(self, username="resetter"):
        return auth_recovery.issue_reset_code(username)

    def _reset_limiter(self):
        from custom_backend.main import rate_limit_key
        peer = rate_limit_key("reset", "testclient", None)
        login_limiter.reset(peer)
        login_limiter.reset(f"{peer}|reset:acct=resetter")
        login_limiter.reset("reset:acct=resetter")
        # login peer 桶同样跨用例共享（full_flow 的登录曾被 429 掉）。
        login_peer = rate_limit_key("login", "testclient", None)
        login_limiter.reset(login_peer)
        login_limiter.reset(f"{login_peer}|acct=resetter")
        login_limiter.reset("login:acct=resetter")

    def setUp(self):
        self._reset_limiter()

    def test_reset_full_flow_kills_short_and_long(self):
        # M1 端到端：专属用户（方法按字母序运行，共享 resetter 的密码会被
        # burn/invalid 等先序用例改掉）——重置前先真实 login 拿一颗**短令牌**
        # （新签恒带 pv）与一颗 remember 长令牌，重置后两者都必须 401。
        user = auth.create_user("flowbuddy", _PW_INITIAL)
        login = self.client.post("/api/auth/login", json={
            "username": "flowbuddy", "password": _PW_INITIAL}).json()
        short_token = login["token"]
        self.assertNotIn("pwd_version", login["user"], "L7：不回显改密计数")
        row = auth.get_user("flowbuddy")
        remember = auth.create_access_token(
            row["id"], row["username"], ttl_hours=168,
            pwd_version=row["pwd_version"])
        code = auth_recovery.issue_reset_code("flowbuddy")
        resp = self.client.post("/api/auth/reset", json={
            "username": "flowbuddy", "code": code,
            "new_password": _PW_RESET_NEW})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertIsNotNone(auth.authenticate("flowbuddy", _PW_RESET_NEW))
        self.assertEqual(self.client.get(
            "/api/auth/me",
            headers={"Authorization": "Bearer " + short_token}).status_code, 401,
            "M1：重置后短令牌（新签带 pv）必须失效")
        self.assertEqual(self.client.get(
            "/api/auth/me",
            headers={"Authorization": "Bearer " + remember}).status_code, 401)
        self.assertIsNone(auth.authenticate("flowbuddy", _PW_INITIAL))

    def test_sibling_codes_revoked_after_reset(self):
        # M4：两枚活码，用其一重置成功 → 兄弟码立即 401。
        self._code_for()
        leaked = self._code_for()
        used = self._code_for()
        resp = self.client.post("/api/auth/reset", json={
            "username": "resetter", "code": used,
            "new_password": _PW_RETRY_NEW})
        self.assertEqual(resp.status_code, 200, resp.text)
        self._reset_limiter()
        replay = self.client.post("/api/auth/reset", json={
            "username": "resetter", "code": leaked,
            "new_password": _PW_ATTEMPT})
        self.assertEqual(replay.status_code, 401, "M4：兄弟码必须随重置回收")

    def test_burn_rearmed_when_set_password_fails(self):
        # M6：set_password 抛异常 → 500 但码被还回（同码修好故障后可重试）。
        # TestClient 默认 raise_server_exceptions=True 会把端点 re-raise 的
        # mock 异常直接抛进测试体——本钉要的就是 HTTP 500 形状，关掉重抛。
        client = TestClient(app, raise_server_exceptions=False)
        code = self._code_for()
        with mock.patch.object(auth, "set_password",
                               side_effect=sqlite3.OperationalError(
                                   "database is locked")):
            resp = client.post("/api/auth/reset", json={
                "username": "resetter", "code": code,
                "new_password": _PW_ATTEMPT})
        self.assertEqual(resp.status_code, 500)
        self._reset_limiter()
        again = client.post("/api/auth/reset", json={
            "username": "resetter", "code": code,
            "new_password": _PW_ATTEMPT})
        self.assertEqual(again.status_code, 200, "M6：失败后码必须可重试")

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
        self.assertEqual(r1.json()["detail"], r2.json()["detail"])

    def test_whitespace_username_with_valid_code_succeeds(self):
        # L15/L20 端点侧：带空白用户名+正确码 → 200（不再 401 烧桶）。
        code = self._code_for()
        resp = self.client.post("/api/auth/reset", json={
            "username": "  resetter  ", "code": code,
            "new_password": _PW_ATTEMPT})
        self.assertEqual(resp.status_code, 200, resp.text)

    def test_malformed_pv_token_401_not_500(self):
        # L1：异形 pv（str/list）→ 401，不冒全局 500。
        import jwt as pyjwt
        row = auth.get_user("resetter")
        for bad_pv in ("abc", ["x"], None):
            payload = {"sub": str(row["id"]), "username": "resetter",
                       "iat": 1700000000, "exp": 9999999999,
                       "jti": "nail", "iss": auth.JWT_ISSUER, "pv": bad_pv}
            token = pyjwt.encode(payload, auth._secret(),
                                 algorithm=auth.JWT_ALGORITHM)
            resp = self.client.get(
                "/api/auth/me",
                headers={"Authorization": "Bearer " + token})
            self.assertEqual(resp.status_code, 401,
                             f"pv={bad_pv!r} 必须 401 而非 {resp.status_code}")

    def test_resurrected_account_old_token_401(self):
        # L3：删号同名重建（pv 同值）→ 旧令牌 401（id 交叉校验）。
        user = auth.create_user("lazarus", _PW_INITIAL)
        row = auth.get_user("lazarus")
        token = auth.create_access_token(
            row["id"], row["username"], ttl_hours=168,
            pwd_version=row["pwd_version"])
        conn = sqlite3.connect(str(auth.data_dir() / "users.db"))
        try:
            with conn:
                conn.execute("DELETE FROM users WHERE username=?", ("lazarus",))
        finally:
            conn.close()  # 裸连接不关会占住 users.db 句柄（teardown WinError 32）
        auth.create_user("lazarus", _PW_ROTATED)  # 同名重建 id+1/pv=1
        self.assertEqual(self.client.get(
            "/api/auth/me",
            headers={"Authorization": "Bearer " + token}).status_code, 401)

    def test_invalid_new_password_does_not_burn_code(self):
        code = self._code_for()
        oversized_bytes_pw = "密" * 30  # 过模型层、超 72 字节 → 端点 400
        bad = self.client.post("/api/auth/reset", json={
            "username": "resetter", "code": code,
            "new_password": oversized_bytes_pw})
        self.assertEqual(bad.status_code, 400)
        good = self.client.post("/api/auth/reset", json={
            "username": "resetter", "code": code, "new_password": _PW_ATTEMPT})
        self.assertEqual(good.status_code, 200, "同码重试必须成功")

    def test_oversized_body_413(self):
        resp = self.client.post("/api/auth/reset", content=b"{}" + b" " * 5000,
                                headers={"Content-Type": "application/json"})
        self.assertEqual(resp.status_code, 413)

    def test_rate_limit_429(self):
        payload = {"username": "resetter", "code": "rf-" + "y" * 32,
                   "new_password": _PW_ATTEMPT}
        for _ in range(5):
            self.client.post("/api/auth/reset", json=payload)
        resp = self.client.post("/api/auth/reset", json=payload)
        self.assertEqual(resp.status_code, 429)
        self._reset_limiter()


class IssueChannelPins(unittest.TestCase):
    @classmethod
    def tearDownClass(cls):
        fixture.cleanup()

    def test_reset_code_script_respects_env_data_dir(self):
        # M7：环境变量优先——不设时才落平台默认目录（无 CWD 影子库）。
        auth.create_user("clibuddy", _PW_CLI)
        env = {**os.environ, "COBOOKSHELF_DATA_DIR": fixture.name,
               "PYTHONIOENCODING": "utf-8"}
        result = subprocess.run(
            [sys.executable, "-X", "utf8",
             str(REPO / "build" / "scripts" / "reset_code.py"), "clibuddy"],
            capture_output=True, text=True, encoding="utf-8", env=env,
            cwd=tempfile.mkdtemp(prefix="rf_cli_cwd_"), timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr)
        code = [ln for ln in result.stdout.splitlines() if ln.startswith("rf-")][0]
        self.assertTrue(auth_recovery.consume_reset_code("clibuddy", code.strip()))

    def test_reset_code_script_unknown_user_neutral_exit(self):
        env = {**os.environ, "COBOOKSHELF_DATA_DIR": fixture.name,
               "PYTHONIOENCODING": "utf-8"}
        result = subprocess.run(
            [sys.executable, "-X", "utf8",
             str(REPO / "build" / "scripts" / "reset_code.py"), "ghost-user-1"],
            capture_output=True, text=True, encoding="utf-8", env=env,
            cwd=tempfile.mkdtemp(prefix="rf_cli_cwd_"), timeout=120)
        self.assertEqual(result.returncode, 1)
        self.assertNotIn("rf-", result.stdout)

    def test_windows_start_reset_user_shows_and_copies_code(self):
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
        self.assertIn("rf-", text)
        self.assertIn("15", text)
        clip.assert_called_once()
        self.assertTrue(clip.call_args[0][0].startswith("rf-"))
        self.assertIn("Ctrl+V", text)


if __name__ == "__main__":
    unittest.main()
