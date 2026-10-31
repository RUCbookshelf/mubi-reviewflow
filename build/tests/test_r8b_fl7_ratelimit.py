"""r8b FL7 限流域回归钉（r6 #38/#39/#40/#41/#42/#45/#46）。

每条用例对准 r6 复审探针（E:/mubi-reviewflow/out/external_reviews/
r6_verify_upload.md）的同款形态，破坏即红（修复前 a080fde 上必失败）：

- #39/#45：重名注册不烧 bcrypt（修复前 12 连重名 = 12 次 rounds=12
  hashpw、2.17s CPU、限流桶恒空可无限打）；失败路径退款语义保留。
- #40：rate_limit_key 文档含 risk-accepted 登记与可信代理 opt-in 说明
  （修复前全仓无登记载体）。
- #41：SSE 票据 TTL 60s（修复前 300）＋旧 token 查询参数 410（修复前
  200 + Deprecation 头）。
- #42：IPv6 /64 归一（修复前同 /64 两地址各 5 次、合计 10 次/窗口）。
- #38：requirements-app.txt 声明 fastapi 上界与 starlette（修复前
  `fastapi>=0.110` 无上界、starlette 完全未声明）。
- #46：账号维 429 分支可达（修复前账号桶只按 peer+username 分键，
  len(acct)≤len(peer) 恒成立、分支为死分支）；同 peer 双桶皆满仍按
  peer 维文案（生效顺序：peer 先行）。
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
fixture = tempfile.TemporaryDirectory(prefix="rf_r8b_fl7_rl_")
os.environ["COBOOKSHELF_DATA_DIR"] = fixture.name
os.environ["REVIEWFLOW_MODE"] = "server"

from fastapi.testclient import TestClient

from custom_backend import auth as auth_mod
from custom_backend.main import app
from custom_backend.security import (
    EventTicketStore,
    RateLimiter,
    login_limiter,
    rate_limit_key,
)

REPO = Path(__file__).resolve().parents[2]

def _users_exec(sql: str, args: tuple) -> None:
    """开-关一次性连接（Windows 上未关闭的 sqlite 句柄会锁住临时目录，
    tearDownModule 的 fixture.cleanup() 因此失败）。"""
    import sqlite3
    conn = sqlite3.connect(str(auth_mod._users_db()))
    try:
        conn.execute(sql, args)
        conn.commit()
    finally:
        conn.close()



def setUpModule():
    """模块级共享数据目录（多测试类顺序执行，统一在 tearDownModule 清理）。"""


def tearDownModule():
    fixture.cleanup()


class DuplicateRegisterNoBcryptTests(unittest.TestCase):
    """#39/#45：重名检查前移到 hash_password 之前 + 失败路径退款。"""

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.client.__enter__()
        cls.existing = auth_mod.create_user("dup-probe@example.test", "Dup-Probe-2026")

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        _users_exec("DELETE FROM users WHERE id=?", (cls.existing["id"],))

    def tearDown(self):
        login_limiter._events.clear()

    def test_duplicate_register_never_hashes_password(self):
        """r6 探针 phaseA 同款：12 连重名全 400 且 hashpw 0 次（修复前 12 次）。"""
        def _no_hash(_password):
            raise AssertionError(
                "重名注册不得执行 bcrypt 哈希（r6 #45：检查需前移到 hash 前）")

        with mock.patch.object(auth_mod, "hash_password", side_effect=_no_hash):
            for _ in range(12):
                r = self.client.post("/api/auth/register", json={
                    "username": self.existing["username"],
                    "password": "Whatever-Pass-9",
                })
                self.assertEqual(r.status_code, 400, r.text)
                self.assertIn("冲突", r.json()["detail"])
        # 失败退款语义保留：重名失败不占 peer 配额（桶空，合法注册仍可行）
        fresh = "fresh-after-dup-%d@example.test" % os.getpid()
        r = self.client.post("/api/auth/register", json={
            "username": fresh, "password": "Fresh-Pass-2026"})
        self.assertEqual(r.status_code, 200, r.text)
        import sqlite3
        conn = sqlite3.connect(str(auth_mod._users_db()))
        try:
            row = conn.execute(
                "SELECT id FROM users WHERE username=?", (fresh,)).fetchone()
            conn.execute("DELETE FROM users WHERE id=?", (row[0],))
            conn.commit()
        finally:
            conn.close()

    def test_invalid_password_also_never_hashes(self):
        """#39/#45 二轮：非法口令（校验失败）同样不得执行 bcrypt——
        二轮评审实测把 validate_password 回退到 INSERT 前时原钉子仍全绿。"""
        def _no_hash(_password):
            raise AssertionError("非法口令的注册不得执行 bcrypt 哈希")

        # 25 个汉字 = 75 字节 > 72 字节上限（G-01 口径）：Pydantic 放行
        # （8~128 字符），只有 auth.validate_password 的字节上限能挡——
        # 正好用于验证「口令校验也在 hash 之前」。
        with mock.patch.object(auth_mod, "hash_password", side_effect=_no_hash):
            r = self.client.post("/api/auth/register", json={
                "username": "long-pw-probe@example.test", "password": "密" * 25})
        self.assertEqual(r.status_code, 400, r.text)
        self.assertIn("字节", r.json()["detail"])

    def test_reserved_name_also_never_hashes(self):
        """#39/#45 三轮：保留名拒绝（collaboration.db 撞名）同样在 hash 之前。"""
        def _no_hash(_password):
            raise AssertionError("保留名注册不得执行 bcrypt 哈希")

        with mock.patch.object(auth_mod, "hash_password", side_effect=_no_hash):
            # slug 后 + ".db" 即撞保留文件名 → 'collaboration' / 大小写变体
            for name in ("collaboration", "Collaboration"):
                r = self.client.post("/api/auth/register", json={
                    "username": name, "password": "Reserved-Pass-9"})
                self.assertEqual(r.status_code, 400, r.text)
                self.assertIn("保留文件名", r.json()["detail"])

    def test_success_register_still_counts_toward_peer_quota(self):
        """#39 成功占额面（既有语义钉）：同 peer 连续成功注册第 6 次 429。"""
        codes = []
        for i in range(6):
            r = self.client.post("/api/auth/register", json={
                "username": "quota-%d-%d@example.test" % (os.getpid(), i),
                "password": "Quota-Pass-2026"})
            codes.append(r.status_code)
            if r.status_code == 200:
                _users_exec("DELETE FROM users WHERE id=?",
                            (r.json()["user"]["id"],))
        self.assertEqual(codes, [200] * 5 + [429], codes)


class RateLimitKeyCaliberTests(unittest.TestCase):
    """#42：IPv6 /64 归一 + 既有口径不回归（非 IP / IPv4 / XFF 忽略）。"""

    def test_same_prefix64_shares_bucket(self):
        k1 = rate_limit_key("login", "2001:db8:0:1::1", None)
        k2 = rate_limit_key("login", "2001:db8:0:1::2", None)
        self.assertEqual(k1, k2)                       # 修复前两键不同
        self.assertIn("/64", k1)

    def test_different_prefix64_separate(self):
        self.assertNotEqual(
            rate_limit_key("login", "2001:db8:0:1::1", None),
            rate_limit_key("login", "2001:db8:0:2::1", None))

    def test_ipv4_and_hostname_unchanged(self):
        self.assertEqual(rate_limit_key("login", "203.0.113.9", None),
                         "login:203.0.113.9")
        self.assertEqual(rate_limit_key("login", "testclient", None),
                         "login:testclient")
        # XFF 一律不影响桶键（U5-P5 既有口径）
        self.assertEqual(rate_limit_key("login", "testclient", "9.9.9.9"),
                         rate_limit_key("login", "testclient", None))

    def test_v4_mapped_ipv6_not_collapsed(self):
        # ::ffff:a.b.c.d 按 IPv4 处理：不折叠进同一 /64 桶
        self.assertNotEqual(
            rate_limit_key("login", "::ffff:203.0.113.9", None),
            rate_limit_key("login", "::ffff:198.51.100.7", None))

    def test_ipv4_prefix64_helper_returns_none(self):
        from custom_backend.security import _ipv6_prefix64_key  # r6 #42 新增
        self.assertIsNone(_ipv6_prefix64_key("203.0.113.9"))
        self.assertIsNone(_ipv6_prefix64_key("testclient"))
        self.assertIsNone(_ipv6_prefix64_key("::ffff:203.0.113.9"))

    def test_limiter_window_closes_across_prefix_rotation(self):
        """r6 探针同款（函数层）：同 /64 两地址轮换合计只放行 5 次。"""
        limiter = RateLimiter(max_events=5, window_seconds=60.0)
        results = []
        for addr in ("2001:db8:0:1::1", "2001:db8:0:1::2"):
            for _ in range(6):
                allowed, _retry = limiter.check(rate_limit_key("login", addr, None))
                results.append(allowed)
        # 修复前：两桶独立 → 前 10 次 True；修复后：同桶 → 第 6 次起全 False
        self.assertEqual(results[:5], [True] * 5)
        self.assertFalse(
            any(results[5:]),
            f"同 /64 轮换第 6 次起必须全拒（r6 #42）：{results}")

    def test_real_endpoint_ipv6_rotation_blocked(self):
        """真实端点（#42）：addr1 5 次失败后，同 /64 的 addr2 第 1 次即 429。"""
        victim = auth_mod.create_user("v6-probe@example.test", "V6-Probe-2026")
        try:
            c1 = TestClient(app, client=("2001:db8:0:1::1", 40001))
            c2 = TestClient(app, client=("2001:db8:0:1::2", 40002))
            with c1, c2:
                for _ in range(5):
                    r = c1.post("/api/auth/login", json={
                        "username": victim["username"],
                        "password": "Wrong-Pass-8"})
                    self.assertEqual(r.status_code, 401, r.text)
                r = c2.post("/api/auth/login", json={
                    "username": victim["username"],
                    "password": "V6-Probe-2026"})
                # 修复前：addr2 桶为空 → 200；修复后：同 /64 桶已满 → 429
                self.assertEqual(r.status_code, 429, r.text)
        finally:
            login_limiter._events.clear()
            _users_exec("DELETE FROM users WHERE id=?", (victim["id"],))


class AccountDimensionOrderTests(unittest.TestCase):
    """#46：账号维 429 分支可达（跨对端全局键）＋ peer 维先行顺序不变。"""

    @classmethod
    def setUpClass(cls):
        cls.victim = auth_mod.create_user("acct-global@example.test", "Acct-Global-9")

    @classmethod
    def tearDownClass(cls):
        _users_exec("DELETE FROM users WHERE id=?", (cls.victim["id"],))

    def tearDown(self):
        login_limiter._events.clear()

    def test_cross_peer_account_lockout_reaches_account_dimension(self):
        """跨对端：A peer 5 次失败后，B peer 正确密码登录 →「按账号计数」429。

        修复前账号桶只按 peer+username 分键：B peer 的 peer/账号桶皆空 →
        200，「按账号计数」分支不可达（r6 #46 死分支）。
        """
        peer_a = "203.0.113.77"
        ca = TestClient(app, client=(peer_a, 40101))
        with ca:
            for _ in range(5):
                r = ca.post("/api/auth/login", json={
                    "username": self.victim["username"],
                    "password": "Wrong-Pass-8"})
                self.assertEqual(r.status_code, 401, r.text)
        cb = TestClient(app, client=("198.51.100.66", 40102))
        with cb:
            r = cb.post("/api/auth/login", json={
                "username": self.victim["username"],
                "password": "Acct-Global-9"})
        self.assertEqual(r.status_code, 429, r.text)
        self.assertIn("按账号计数", r.json()["detail"])

    def test_account_lock_docstring_is_not_overclaimed(self):
        """#46 三轮：注释不得宣称「低成本续锁已闭合」——只对单来源成立，
        轮换来源/中继仍可绕开；必须显式登记该残余。"""
        src = (REPO / "custom_backend" / "main.py").read_text(encoding="utf-8")
        doc = src.split("def login(")[1].split("peer_key = rate_limit_key")[0]
        self.assertIn("轮换来源", doc, "必须如实登记轮换来源残余")
        self.assertNotIn("不能零成本", doc, "不得宣称零成本续锁已闭合")
        self.assertNotIn("旁路已闭合", doc)

    def test_global_lock_consumes_attacker_peer_quota(self):
        """#46：全局账号键命中时**不退** peer 占位（缓解的一部分，非闭环）：
        同一来源反复锁号会吃满自己的 peer 桶。"""
        from custom_backend.security import login_limiter as lim
        lim._events.clear()
        victim = self.victim["username"]
        # 用 5 个不同来源各失败 1 次把全局账号键打满
        for i in range(5):
            c = TestClient(app, client=("192.0.2.%d" % (10 + i), 40200 + i))
            with c:
                r = c.post("/api/auth/login", json={
                    "username": victim, "password": "Wrong-Pass-8"})
                self.assertEqual(r.status_code, 401, r.text)
        # 第 6 个来源第 1 次：账号维命中 429，且其 peer 桶被占 1（不退）
        c6 = TestClient(app, client=("192.0.2.60", 40260))
        with c6:
            r = c6.post("/api/auth/login", json={
                "username": victim, "password": "Wrong-Pass-8"})
            self.assertEqual(r.status_code, 429, r.text)
            self.assertIn("按账号计数", r.json()["detail"])
            peer_key = rate_limit_key("login", "192.0.2.60", None)
            self.assertEqual(len(lim._events.get(peer_key, [])), 1,
                             "锁号命中必须占住攻击者 peer 配额（不退占位）")

    def test_same_peer_both_buckets_full_keeps_peer_dimension(self):
        """生效顺序钉：同 peer 双桶皆满时仍按 peer 维文案（既有回归钉同款
        预置，r6 探针自然序列同状态）——peer 维先行判定不变。"""
        peer_key = rate_limit_key("login", "203.0.113.78", None)
        acct_key = peer_key + "|acct=" + self.victim["username"]
        for _ in range(5):
            login_limiter.record(peer_key)
            login_limiter.record(acct_key)
        cc = TestClient(app, client=("203.0.113.78", 40103))
        with cc:
            r = cc.post("/api/auth/login", json={
                "username": self.victim["username"],
                "password": "Acct-Global-9"})
        self.assertEqual(r.status_code, 429, r.text)
        self.assertIn("按连接来源 IP", r.json()["detail"])


class EventTicketTtlTests(unittest.TestCase):
    """#41：票据 TTL 60s + 旧 token 查询参数 410。"""

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.client.__enter__()
        user = auth_mod.create_user("ticket-ttl@example.test", "Ticket-Ttl-9")
        cls.token = auth_mod.create_access_token(user["id"], user["username"])
        cls.uid = user["id"]

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        _users_exec("DELETE FROM users WHERE id=?", (cls.uid,))

    def test_default_and_app_ticket_ttl_is_60(self):
        # 修复前默认/应用实例均为 300（r6 实测 issue_expires_in=300）
        self.assertEqual(EventTicketStore().ttl, 60.0)
        from custom_backend.main import _event_tickets
        self.assertEqual(_event_tickets.ttl, 60)
        ticket, expires_in = _event_tickets.issue(7, "alice")
        self.assertEqual(expires_in, 60)
        _event_tickets.consume(ticket)

    def test_issue_endpoint_reports_60(self):
        r = self.client.post(
            "/api/events/ticket",
            headers={"Authorization": "Bearer " + self.token})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["expires_in"], 60)

    def test_legacy_token_query_param_is_410(self):
        """修复前：200 + Deprecation/Warning 头（r6 #41 只到 deprecation 阶段）。

        直接驱动端点协程（与既有 test_u5p10 系列同法）：修复前走旧 token
        路径会返回 200 流式响应（TestClient 拉流不退出），协程直驱则干净地
        返回 response 对象 → 本断言红；修复后构造即抛 HTTPException(410)。
        """
        import asyncio
        from urllib.parse import quote

        from fastapi import HTTPException
        from starlette.requests import Request

        from custom_backend.main import task_events

        query = f"token={quote(self.token, safe='')}"
        scope = {"type": "http", "method": "GET", "path": "/api/events",
                 "headers": [(b"content-type", b"text/plain")],
                 "query_string": query.encode("ascii")}
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(task_events(Request(scope), ticket=None,
                                    token=self.token, authorization=None))
        self.assertEqual(ctx.exception.status_code, 410)
        self.assertIn("ticket", str(ctx.exception.detail))
        self.assertEqual(ctx.exception.headers.get("Deprecation"), "true")

    def test_token_plus_bearer_still_410(self):
        """#41 三轮阻断项：?token= 下线**不看**是否另带 Bearer。

        上一版加的 `and not authorization` 门控把下线收窄成「只对
        EventSource 生效」——而 EventSource 恰是新前端已不用 token 的路径；
        24h JWT 进 URL 的泄露面与 Header 凭据无关。"""
        import asyncio
        from urllib.parse import quote

        from fastapi import HTTPException
        from starlette.requests import Request

        from custom_backend.main import task_events

        query = f"token={quote(self.token, safe='')}"
        scope = {"type": "http", "method": "GET", "path": "/api/events",
                 "headers": [(b"content-type", b"text/plain"),
                             (b"authorization", ("Bearer " + self.token).encode())],
                 "query_string": query.encode("ascii")}
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(task_events(Request(scope), ticket=None,
                                    token=self.token,
                                    authorization=f"Bearer {self.token}"))
        self.assertEqual(ctx.exception.status_code, 410)

class RequirementsLockTests(unittest.TestCase):
    """#38 锁版本子项：fastapi 上界 + starlette 显式声明。"""

    def test_requirements_app_pins_fastapi_upper_bound_and_starlette(self):
        text = (REPO / "build" / "requirements-app.txt").read_text(encoding="utf-8")
        active = [
            line.strip() for line in text.splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        fastapi = [l for l in active if l.lower().startswith("fastapi")]
        starlette = [l for l in active if l.lower().startswith("starlette")]
        self.assertEqual(len(fastapi), 1, "fastapi 必须恰好一行")
        self.assertEqual(len(starlette), 1,
                         "starlette 必须显式声明（修复前完全未声明）")
        self.assertIn("<", fastapi[0], "fastapi 必须带上界（修复前 >=0.110 浮动）")
        self.assertIn("<", starlette[0], "starlette 必须带上界")
        # 二轮：上界必须覆盖实际验证过的 venv 版本（fastapi 0.143 / starlette 1.8），
        # 修复前的 <0.134/<1.4 与实际验证环境相反。
        import fastapi as _f, starlette as _s  # noqa: E401
        def _upper(spec):
            return float(spec.split("<")[1])
        self.assertGreater(_upper(fastapi[0]), float("0." + _f.__version__.split(".")[1]),
                           "fastapi 上界必须高于已验证版本 %s" % _f.__version__)
        self.assertGreater(_upper(starlette[0]), float("%s.%s" % tuple(_s.__version__.split(".")[:2])),
                           "starlette 上界必须高于已验证版本 %s" % _s.__version__)
        # 注释说明锁定理由（413 契约 + 回归复验要求）
        comments = "\n".join(
            l for l in text.splitlines() if l.strip().startswith("#"))
        self.assertIn("413", comments)
        self.assertIn("test_upload_security_regression", comments)


class MultiWorkerDocGuardTests(unittest.TestCase):
    """#43（注释降级支）：唯一的交付物是注释/示例，必须有静态钉——
    二轮实测把模块 docstring 改回 --workers 2 后三个钉子文件仍 38 全绿。"""

    def test_module_example_is_single_worker_and_declares_gap(self):
        main_src = (REPO / "custom_backend" / "main.py").read_text(encoding="utf-8")
        doc = main_src.split('"""')[1]
        example = [ln for ln in doc.splitlines() if ln.strip().startswith("uvicorn ")]
        self.assertTrue(example, "模块 docstring 缺生产示例")
        self.assertNotIn("--workers", example[0],
                         "生产示例不得再教多 worker（r6 #41/#43 进程内状态）")
        self.assertIn("r6 #41/#43", doc, "缺多 worker 缺口声明")

    def test_docs_do_not_teach_multi_worker(self):
        """#43 三轮：部署文档/README 也不得再教多 worker（本轮实测全仓
        `--workers` 命中 0，但无钉守护——回滚即静默）。"""
        docs = [REPO / "README.md", REPO / "docs" / "deployment" / "UBUNTU_SERVER.md"]
        for path in docs:
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            with self.subTest(doc=path.name):
                self.assertNotIn("--workers 2", text)

    def test_docstring_states_amplification_direction(self):
        """#43 三轮：多 worker 下限流是「N×5、方向放宽」，必须写明方向。"""
        doc = (REPO / "custom_backend" / "main.py").read_text(
            encoding="utf-8").split('"""')[1]
        self.assertIn("放宽", doc)
        self.assertIn("5 次/分钟", doc)

    def test_task_lock_middleware_docstring_declares_inprocess_limit(self):
        main_src = (REPO / "custom_backend" / "main.py").read_text(encoding="utf-8")
        cls_doc = main_src.split("class TaskLockMiddleware")[1].split('"""')[1]
        self.assertIn("r6 #43", cls_doc)
        self.assertIn("进程内", cls_doc)
        self.assertIn("全局列表读不取任务锁文件", cls_doc)


class RiskAcceptedRegistrationTests(unittest.TestCase):
    """#40：共享桶取舍的 risk-accepted 登记与可信代理 opt-in 说明。"""

    def test_rate_limit_key_documents_risk_accepted_and_optin(self):
        source = (REPO / "custom_backend" / "security.py").read_text(encoding="utf-8")
        doc = source.split('def rate_limit_key(')[1].split('"""')[1]
        self.assertIn("risk-accepted", doc)
        self.assertIn("opt-in", doc)
        self.assertIn("r6 #40", doc)


if __name__ == "__main__":
    unittest.main()
