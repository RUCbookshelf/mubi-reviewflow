"""r7 补充泳道 G-01 钉子：bcrypt 72 字节上限必须显式拒绝（400 族 + 限流退款）。

复现依据：E:/mubi-reviewflow/out/external_reviews/gemini_findings_verification.md
G-01（confirmed/必修）。baseline：80 位 ASCII 或 25 个汉字（75 字节）密码在
Pydantic 层合法（8~128 字符），``bcrypt.hashpw`` 对 >72 字节抛裸 ``ValueError``，
注册端点只捕获 ``auth.AuthError``（refund 在 except 块内）→ 500 且限流占位
不退（复审实测 500×5 + 第 6 次 429，桶残留 1）。

把对应修复删掉，下列断言必须变红：
- ``validate_password`` 的 UTF-8 字节数 ≤72 校验（G-01 主修）；
- ``hash_password`` 的 ``ValueError``→``AuthError`` 兜底（纵深防御）；
- 注册端点 400 族清晰拒绝且 ``login_limiter`` 占位退回；
- ``verify_password`` 超长 → False 不抛（baseline 已有兜底，防回退）；
- 合法 8~72 字节密码（含多字节）注册/登录行为不变（R2 回归钉）。
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
fixture = tempfile.TemporaryDirectory(prefix="rf_r7_g01_")
os.environ["COBOOKSHELF_DATA_DIR"] = fixture.name
os.environ["REVIEWFLOW_MODE"] = "server"

from fastapi.testclient import TestClient

from custom_backend import auth as auth_mod
from custom_backend import main as backend_main  # noqa: F401（DATA_DIR 环境初始化）
from custom_backend.main import app
from custom_backend.security import login_limiter, rate_limit_key

#: TestClient 默认直连对端（scope["client"] == ("testclient", 50000)）；
#: XFF 不参与分桶（rate_limit_key 语义），注册端点 peer 桶 key 固定如下。
_REGISTER_PEER_KEY = rate_limit_key("register", "testclient", None)


class G01ValidatePasswordByteLimitTests(unittest.TestCase):
    """G-01 主修钉：validate_password 按 UTF-8 字节数设 ≤72 上限。"""

    def test_over_72_bytes_ascii_rejected_with_message(self):
        with self.assertRaises(auth_mod.AuthError) as ctx:
            auth_mod.validate_password("A" * 73)  # 73 字符 = 73 字节
        self.assertIn("过长", str(ctx.exception))

    def test_cjk_byte_boundary_72_legal_75_rejected(self):
        # 24 个汉字 = 72 字节（合法边界，行为不变）；25 个 = 75 字节（拒绝）
        self.assertEqual(auth_mod.validate_password("汉" * 24), "汉" * 24)
        with self.assertRaises(auth_mod.AuthError):
            auth_mod.validate_password("汉" * 25)

    def test_char_length_policy_unchanged(self):
        # 8~128 字符策略原样保留：过短仍拒，8 字符（8 字节）放行
        with self.assertRaises(auth_mod.AuthError):
            auth_mod.validate_password("x" * 7)
        self.assertEqual(auth_mod.validate_password("y" * 8), "y" * 8)


class G01HashVerifyGuardTests(unittest.TestCase):
    """hash/verify 对 bcrypt ValueError 的兜底：hash→AuthError，verify→False。"""

    def test_hash_password_overlong_raises_autherror_not_valueerror(self):
        # hashpw 对 >72 字节在计算前即抛 ValueError（不耗 rounds，快）
        with self.assertRaises(auth_mod.AuthError):
            auth_mod.hash_password("D" * 80)

    def test_g01c_verify_password_overlong_returns_false(self):
        """钉 (c)：verify_password 超长口令 → False 不抛（防兜底被移除）。"""
        good_hash = auth_mod.hash_password("B" * 70)  # ≤72 字节，合法
        self.assertFalse(auth_mod.verify_password("C" * 80, good_hash))
        self.assertTrue(auth_mod.verify_password("B" * 70, good_hash))


class G01RegisterEndpointTests(unittest.TestCase):
    """钉 (a)(b)(d)：注册端点对 >72 字节密码 400 族清晰拒绝且占位退回。"""

    @classmethod
    def setUpClass(cls):
        # raise_server_exceptions=False：baseline 的裸 ValueError 由全局
        # 500 处理器接住呈 500；修复后 AuthError 在端点内被捕获，不到 500。
        cls.client = TestClient(app, raise_server_exceptions=False)
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    def setUp(self):
        login_limiter.reset(_REGISTER_PEER_KEY)  # 隔离同对端先前的注册占位

    def test_g01a_ascii80_register_rejected_bucket_refunded(self):
        r = self.client.post("/api/auth/register",
                             json={"username": "g01a-ascii80", "password": "A" * 80})
        self.assertIn(
            r.status_code, (400, 402, 422),
            f"80 位 ASCII（80 字节）密码必须清晰拒绝，不得 500：{r.status_code} {r.text}")
        bucket = login_limiter._events.get(_REGISTER_PEER_KEY)
        self.assertFalse(bucket, f"清晰拒绝后限流占位必须退回（桶残留 {bucket}）")

    def test_g01b_cjk25_register_rejected_bucket_refunded(self):
        r = self.client.post("/api/auth/register",
                             json={"username": "g01b-cjk25", "password": "汉" * 25})
        self.assertIn(
            r.status_code, (400, 402, 422),
            f"25 个汉字（75 字节）密码必须清晰拒绝，不得 500：{r.status_code} {r.text}")
        bucket = login_limiter._events.get(_REGISTER_PEER_KEY)
        self.assertFalse(bucket, f"清晰拒绝后限流占位必须退回（桶残留 {bucket}）")

    def test_boundary_72_bytes_accepted_73_rejected(self):
        ok = self.client.post("/api/auth/register",
                              json={"username": "g01-edge72", "password": "E" * 72})
        self.assertEqual(ok.status_code, 200, ok.text)
        # 成功注册计入配额（不退款）：先记录桶内基数，再验证被拒的注册不遗留占位
        before = len(login_limiter._events.get(_REGISTER_PEER_KEY, []))
        bad = self.client.post("/api/auth/register",
                               json={"username": "g01-edge73", "password": "E" * 73})
        self.assertIn(bad.status_code, (400, 402, 422),
                      f"73 字节（73 位 ASCII）必须清晰拒绝：{bad.status_code} {bad.text}")
        after = len(login_limiter._events.get(_REGISTER_PEER_KEY, []))
        self.assertEqual(after, before, "被清晰拒绝的注册不得遗留限流占位")

    def test_g01d_legal_passwords_roundtrip_unchanged(self):
        """钉 (d)：8~72 字节合法密码（含多字节 20 汉字=60 字节）注册/登录不变。"""
        for username, password in (("g01d-ascii8", "G01-Pass"),
                                   ("g01d-cjk20", "汉" * 20)):  # 60 字节
            reg = self.client.post("/api/auth/register",
                                   json={"username": username, "password": password})
            self.assertEqual(reg.status_code, 200, reg.text)
            self.assertIn("token", reg.json())
            good = self.client.post("/api/auth/login",
                                    json={"username": username, "password": password})
            self.assertEqual(good.status_code, 200, good.text)
            self.assertIn("token", good.json())
        # 错误密码（同为合法字节长度）→ 401 而非 500
        wrong = self.client.post("/api/auth/login",
                                 json={"username": "g01d-cjk20",
                                       "password": "汉" * 19 + "字"})
        self.assertEqual(wrong.status_code, 401, wrong.text)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
