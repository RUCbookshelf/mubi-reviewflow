# -*- coding: utf-8 -*-
"""2.1.5 安全修复波（fix/2.1.5-sec1）· notify 出站防护钉组（专属新文件）。

终审确认：build/tests/ 里没有任何"去掉防护就红"的 notify 出站钉，且
test_2_1_5_streak.py 两处把 ``validate_public_url`` mock 成恒放行（本波已
改为真校验）。本文件补齐三组钉，全部零真实出站（假 opener / 解析桩 /
假 HTTP handler）：

F1（高危·通知通道环境变量外带）：

- 保存侧：非 ``REVIEWFLOW_NOTIFY_`` 前缀的环境变量名 → 400 结构化
  ``daily_nudge.channel_env_forbidden``（``params.name``）；形状非法 → 400
  ``daily_nudge.channel_env_invalid``（``params.field``）；合法前缀名端到端
  可用（保存 200 + 发送 ok=true + Bearer 值正确）。
- 发送侧（防御纵深）：库里直写的旧存量配置（未走保存校验）直发 →
  结构化降级 ``credential_missing/env_name_forbidden`` + 零出站。
- 变异：把前缀判定换回形状-only（旧口径）→ 保存钉必红（放行即红）。

F2（中危·出站防护零钉）：

- 私网/环回/链路本地/CGNAT webhook URL → send 零出站 + ``private_host``；
  保存侧 → 400 ``daily_nudge.channel_url_invalid``（含
  127.0.0.1 / 10.x / 192.168.x / 169.254.169.254 / 100.100.100.200）。
- 302 → ``redirect_refused``、绝不跟跳（真实 urllib handler 链构造）。
- 响应体 > 64KiB → ``response_too_large``。
- DNS 解析后复检：域名解析到私网 → 拒（``validate_public_hostname``）。
- 变异（运行期 monkeypatch，不改文件）：``_is_private_address`` 判宽 /
  拒重定向删除 / 读取上限删除 → 对应绿钉全部翻红（防护被删时危险行为
  真会发生——出站被放行/被跟随/大响应被全收）。
"""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
import urllib.request
import urllib.response
from email.message import Message
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

fixture = tempfile.TemporaryDirectory(prefix="rf_215sec_notify_")
os.environ["COBOOKSHELF_DATA_DIR"] = fixture.name
os.environ["REVIEWFLOW_MODE"] = "server"

from fastapi.testclient import TestClient  # noqa: E402

from coscreen import notify as notify_core  # noqa: E402
from coscreen import tasks as tasks_mod  # noqa: E402
from custom_backend import auth as auth_mod  # noqa: E402
from custom_backend.main import app, DATA_DIR  # noqa: E402

#: DNS 解析桩统一指向的公网 IP（example.com 的历史地址；不是任何内网段）。
PUBLIC_IP = "93.184.216.34"
PRIVATE_URLS = [
    "http://127.0.0.1:9/hook",                     # 环回
    "http://10.1.2.3/hook",                        # 私网 A 类
    "http://192.168.1.5/hook",                     # 私网 C 类
    "http://169.254.169.254/latest/meta-data/",    # 链路本地（云元数据）
    "http://100.100.100.200/hook",                 # CGNAT 段（云内网 DNS）
]


def _summary() -> dict:
    return notify_core.build_summary(
        {"screened_today": 1, "daily_target": 2, "remaining": 3,
         "streak": 0, "longest_streak": 0})


def _public_resolver(host: str, port: int = 443) -> list[str]:
    return [PUBLIC_IP]


class _CountingOpener:
    """假 opener：记录每次出站（URL/头），可注异常/响应体；零真实出站。"""

    def __init__(self, body: bytes = b"{}", exc: Exception | None = None):
        self.calls: list[dict] = []
        self.body = body
        self.exc = exc

    def open(self, req, timeout=None):
        self.calls.append({"url": req.full_url,
                           "headers": dict(req.header_items())})
        if self.exc is not None:
            raise self.exc
        opener = self

        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self, n):
                return opener.body

        return _Resp()


# ---------------------------------------------------------------------------
# F2 ① + DNS 复检：私网/环回/CGNAT 出站与保存双闸
# ---------------------------------------------------------------------------

class PrivateUrlPins(unittest.TestCase):
    def test_send_private_url_zero_outbound(self):
        """send 侧：私网/环回/链路本地/CGNAT 一律拒且零出站。"""
        for url in PRIVATE_URLS:
            with self.subTest(url=url):
                opener = _CountingOpener()
                result = notify_core.send(
                    {"enabled": True, "type": "webhook", "url": url},
                    _summary(), opener=opener)
                self.assertFalse(result["ok"], result)
                self.assertEqual(result["status"], "private_host", result)
                self.assertEqual(len(opener.calls), 0, url)

    def test_dns_resolves_to_private_rejected(self):
        """DNS 解析后复检钉：域名解析回私网（rebinding）→ 拒。"""
        with mock.patch.object(notify_core, "_resolve_host",
                               lambda host, port=443: [PUBLIC_IP, "192.168.0.1"]):
            with self.assertRaises(notify_core.NotifyError) as ctx:
                notify_core.validate_public_hostname("rebind.example.test")
        self.assertEqual(ctx.exception.code, "private_host")
        # 对照：仅公网解析 → 放行
        with mock.patch.object(notify_core, "_resolve_host", _public_resolver):
            self.assertIsNone(
                notify_core.validate_public_hostname("hooks.example.test"))


# ---------------------------------------------------------------------------
# F2 ② 302 拒跟跳（真实 urllib handler 链）+ 变异
# ---------------------------------------------------------------------------

class _StepHTTPHandler(urllib.request.HTTPHandler):
    """假 HTTP handler：首个请求回 302 指向内网 URL；若被跟随，对内网
    URL 回 200（危险信号）。真实 handler 链，零真实出站。"""

    INNER_URL = "http://192.168.1.7/inner"

    def __init__(self):
        super().__init__()
        self.hits: list[str] = []

    def http_open(self, req):
        self.hits.append(req.full_url)
        if len(self.hits) == 1:
            headers = Message()
            headers["Location"] = self.INNER_URL
            resp = urllib.response.addinfourl(
                io.BytesIO(b""), headers, req.full_url, 302)
        else:
            resp = urllib.response.addinfourl(
                io.BytesIO(b"{}"), Message(), req.full_url, 200)
        # 真实链路的 do_open 会给响应对象补 .msg；合成响应需自带
        resp.msg = "OK"
        return resp


class RedirectPins(unittest.TestCase):
    OUTER_URL = "http://hooks.example.test/step"

    def _send_over_real_chain(self):
        handler = _StepHTTPHandler()
        opener = urllib.request.build_opener(
            notify_core._NoRedirectHandler, handler)
        with mock.patch.object(notify_core, "_resolve_host", _public_resolver):
            result = notify_core.send(
                {"enabled": True, "type": "webhook", "url": self.OUTER_URL},
                _summary(), opener=opener)
        return result, handler

    def test_302_refused_and_never_followed(self):
        """302 → redirect_refused；内网目标绝不被打开（hits 只有外层一次）。"""
        result, handler = self._send_over_real_chain()
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["status"], "redirect_refused", result)
        self.assertEqual(handler.hits, [self.OUTER_URL], handler.hits)

    def test_mutation_redirect_refusal_removed_pin_goes_red(self):
        """破坏性变异：拒重定向被删（redirect_request 改为跟随）→ 钉必红
        （内网 URL 真会被打开且 send ok=true）。"""

        def _following(handler_self, req, fp, code, msg, headers, newurl):
            return urllib.request.Request(newurl)

        with mock.patch.object(notify_core._NoRedirectHandler,
                               "redirect_request", _following):
            result, handler = self._send_over_real_chain()
        # 防护被删后：请求被跟到内网 URL（第二个 hit）且整体 ok=true——
        # 与 test_302_refused_and_never_followed 的断言完全相反，证明那颗
        # 绿钉依赖"拒绝跟随重定向"这一防护。
        self.assertTrue(result["ok"], result)
        self.assertEqual(handler.hits,
                         [self.OUTER_URL, _StepHTTPHandler.INNER_URL], handler.hits)


# ---------------------------------------------------------------------------
# F2 ③ 响应体上限 + 变异
# ---------------------------------------------------------------------------

class ResponseSizePins(unittest.TestCase):
    URL = "https://hooks.example.test/hook"

    def test_response_over_64kib_rejected(self):
        opener = _CountingOpener(body=b"x" * (notify_core.MAX_RESPONSE_BYTES + 1))
        with mock.patch.object(notify_core, "_resolve_host", _public_resolver):
            result = notify_core.send(
                {"enabled": True, "type": "webhook", "url": self.URL},
                _summary(), opener=opener)
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["status"], "response_too_large", result)

    def test_mutation_cap_removed_pin_goes_red(self):
        """破坏性变异：读取上限被删（MAX_RESPONSE_BYTES → 1GB）→ 钉必红。"""
        opener = _CountingOpener(body=b"x" * (64 * 1024 + 1))
        with mock.patch.object(notify_core, "_resolve_host", _public_resolver), \
             mock.patch.object(notify_core, "MAX_RESPONSE_BYTES", 10 ** 9):
            result = notify_core.send(
                {"enabled": True, "type": "webhook", "url": self.URL},
                _summary(), opener=opener)
        # 上限被删后 >64KiB 的响应被照单全收（ok=true）——证明上面的绿钉
        # 依赖 MAX_RESPONSE_BYTES 这道闸。
        self.assertTrue(result["ok"], result)


# ---------------------------------------------------------------------------
# F2 ⑤ 私网判定变异
# ---------------------------------------------------------------------------

class MutationPrivateCheckPins(unittest.TestCase):
    def test_mutation_private_check_widened_pin_goes_red(self):
        """破坏性变异：_is_private_address 判宽（恒 False）→ 私网钉必红
        （解析到 192.168.1.5 的域名被放行出站）。"""
        opener = _CountingOpener()
        with mock.patch.object(notify_core, "_is_private_address",
                               lambda value: False), \
             mock.patch.object(notify_core, "_resolve_host",
                               lambda host, port=443: ["192.168.1.5"]):
            result = notify_core.send(
                {"enabled": True, "type": "webhook",
                 "url": "http://intranet.example.test/hook"},
                _summary(), opener=opener)
        # 判宽后：假 opener 被调用（危险出站发生）——与
        # PrivateUrlPins 的零出站断言相反，证明那颗钉依赖该防护。
        self.assertTrue(result["ok"], result)
        self.assertEqual(len(opener.calls), 1, opener.calls)


# ---------------------------------------------------------------------------
# F1（含 API 集成）：环境变量名前缀双闸 + 变异
# ---------------------------------------------------------------------------

class EnvNamePrefixApiPins(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.client.__enter__()
        user = auth_mod.create_user("r215sec@example.test", "R215Sec-Pins-2026")
        cls.auth = {"Authorization": "Bearer " + auth_mod.create_access_token(
            user["id"], user["username"])}

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        fixture.cleanup()

    def setUp(self):
        self.task = self.client.post(
            "/api/tasks", json={"name": self._testMethodName},
            headers=self.auth).json()["task_id"]
        self.info = tasks_mod.find_task(self.task, DATA_DIR)
        # 假 opener 兜底：本类任何测试都绝不允许真实出站
        self._opener = _CountingOpener()
        patcher = mock.patch.object(notify_core, "_OPENER", self._opener)
        patcher.start()
        self.addCleanup(patcher.stop)
        # 公网示例域的 DNS 解析桩（保存与发送侧真跑 URL 校验；IP 字面量
        # 私网钉不经过解析器，不受此桩影响）
        resolver = mock.patch.object(notify_core, "_resolve_host", _public_resolver)
        resolver.start()
        self.addCleanup(resolver.stop)

    def _put_channel(self, body):
        return self.client.put(
            f"/api/tasks/{self.task}/screening/notify/channels",
            json=body, headers=self.auth)

    def _store_legacy(self, config):
        """直写库文件（绕过保存校验）：模拟未走新校验的旧存量配置。"""
        (self.info.dir / "daily_nudge_channels.json").write_text(
            json.dumps(config), encoding="utf-8")

    def test_save_rejects_private_webhook_urls(self):
        """保存侧：私网/环回/CGNAT webhook URL → 400 结构化（reason=private_host）。"""
        for url in PRIVATE_URLS:
            with self.subTest(url=url):
                response = self._put_channel(
                    {"enabled": True, "type": "webhook", "url": url})
                self.assertEqual(response.status_code, 400, response.text)
                detail = response.json()["detail"]
                self.assertEqual(detail["message_key"],
                                 "daily_nudge.channel_url_invalid", detail)
                self.assertEqual(detail["params"].get("reason"), "private_host")
        self.assertEqual(len(self._opener.calls), 0)

    def test_save_rejects_unprefixed_env_name(self):
        """保存侧：合法形状但无 REVIEWFLOW_NOTIFY_ 前缀 → 400 专用键。"""
        response = self._put_channel({
            "enabled": True, "type": "webhook",
            "url": "https://hooks.example.test/hook",
            "auth_token_env": "COBOOKSHELF_JWT_SECRET"})
        self.assertEqual(response.status_code, 400, response.text)
        detail = response.json()["detail"]
        self.assertEqual(detail["error_code"], "daily_nudge_channel_env_forbidden")
        self.assertEqual(detail["message_key"],
                         "daily_nudge.channel_env_forbidden", detail)
        self.assertEqual(detail["params"], {"name": "COBOOKSHELF_JWT_SECRET"})
        self.assertEqual(len(self._opener.calls), 0)

    def test_save_rejects_malformed_env_name(self):
        """保存侧：形状非法 → 400 原键（params.field）。"""
        response = self._put_channel({
            "enabled": True, "type": "webhook",
            "url": "https://hooks.example.test/hook",
            "auth_token_env": "BAD-NAME"})
        self.assertEqual(response.status_code, 400, response.text)
        detail = response.json()["detail"]
        self.assertEqual(detail["message_key"],
                         "daily_nudge.channel_env_invalid", detail)
        self.assertEqual(detail["params"], {"field": "auth_token_env"})

    def test_prefixed_env_name_end_to_end(self):
        """合法前缀名端到端：保存 200 + 测试发送 ok=true + Bearer 值正确。"""
        os.environ["REVIEWFLOW_NOTIFY_TEST_TOKEN"] = "pin-token-value"
        self.addCleanup(os.environ.pop, "REVIEWFLOW_NOTIFY_TEST_TOKEN", None)
        saved = self._put_channel({
            "enabled": True, "type": "webhook",
            "url": "https://hooks.example.test/hook",
            "auth_token_env": "REVIEWFLOW_NOTIFY_TEST_TOKEN"})
        self.assertEqual(saved.status_code, 200, saved.text)
        response = self.client.post(
            f"/api/tasks/{self.task}/screening/notify/test", headers=self.auth)
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertTrue(body["ok"], body)
        self.assertEqual(body["status"], "sent")
        self.assertEqual(len(self._opener.calls), 1)
        self.assertEqual(self._opener.calls[0]["headers"].get("Authorization"),
                         "Bearer pin-token-value")

    def test_send_side_rejects_legacy_stored_env_name(self):
        """发送侧独立拦截（防御纵深）：库里旧存量配置直发 → 结构化降级 +
        零出站（保存校验被绕过也绝不把非前缀变量的值发出去）。

        ``reason=env_name_forbidden`` 证明前缀判定在环境变量取值**之前**
        生效（否则会是 ``env_not_set``）；端到端"值会被外带"的实证见
        E:/rf214tmp/fixsec/probe_f1_env_exfil.py（用随机哨兵值）。"""
        self._store_legacy({
            "enabled": True, "type": "webhook",
            "url": "https://hooks.example.test/hook",
            "auth_token_env": "COBOOKSHELF_JWT_SECRET"})
        response = self.client.post(
            f"/api/tasks/{self.task}/screening/notify/test", headers=self.auth)
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertFalse(body["ok"], body)
        self.assertEqual(body["status"], "credential_missing", body)
        self.assertEqual(body["reason"], "env_name_forbidden", body)
        self.assertEqual(len(self._opener.calls), 0)

    def test_mutation_env_prefix_dropped_pin_goes_red(self):
        """破坏性变异：前缀判定换回形状-only（旧口径）→ 保存钉必红。"""

        def _shape_only(name):
            env_name = name if isinstance(name, str) else ""
            if not notify_core._ENV_NAME_RE.fullmatch(env_name):
                raise notify_core.NotifyError(
                    "credential_missing", reason="invalid_env_name")
            return env_name

        with mock.patch.object(notify_core, "validate_env_name", _shape_only):
            response = self._put_channel({
                "enabled": True, "type": "webhook",
                "url": "https://hooks.example.test/hook",
                "auth_token_env": "COBOOKSHELF_JWT_SECRET"})
        # 前缀防护被删后：任意环境变量名保存被放行（200）——与
        # test_save_rejects_unprefixed_env_name 的 400 断言相反，证明那颗
        # 钉依赖 REVIEWFLOW_NOTIFY_ 前缀判定。
        self.assertEqual(response.status_code, 200, response.text)


class OpenerProxyPins(unittest.TestCase):
    """终审 #31：notify 出站 opener 必须忽略 HTTP(S)_PROXY 环境变量。

    沿 test_pdf_fetch_api.py:892 既有钉型：在代理环境下重建模块 opener，
    断言没有任何代理路由方法（模块级 _OPENER 在测试进程导入期不带代理环境，
    只有工厂重建能如实反映「生产进程带代理环境启动」的漏洞形态）。
    """

    def test_opener_ignores_proxy_environment(self):
        import urllib.request as _urllib

        def proxy_routing(opener):
            return sorted(name for name, _func in opener.handle_open.items()
                          for _h in opener.handle_open[name]
                          if type(_h).__name__ == "ProxyHandler")

        with mock.patch.dict(os.environ, {"HTTP_PROXY": "http://127.0.0.1:9",
                                          "HTTPS_PROXY": "http://127.0.0.1:9",
                                          "http_proxy": "http://127.0.0.1:9"}):
            default_opener = _urllib.build_opener()
            self.assertTrue(proxy_routing(default_opener),
                            "前提：默认 opener 在代理环境下会走代理（漏洞形态）")
            our_opener = notify_core._build_opener()
            self.assertEqual(proxy_routing(our_opener), [],
                             "notify opener 必须忽略 HTTP(S)_PROXY（空 ProxyHandler）")
        self.assertTrue(any(type(h).__name__ == "_NoRedirectHandler"
                            for h in our_opener.handlers),
                        "拒绝重定向 handler 仍需保留")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
