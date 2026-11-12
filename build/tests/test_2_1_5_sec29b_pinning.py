# -*- coding: utf-8 -*-
"""sec29b 修复波 W4 组钉子：出站 DNS 钉扎 + IPv6 收口 + decision 入闸。

复审实测确认级四缺口（探针原件 E:/rf214tmp/sec29-reaudit/：probe_rebind /
probe_servfail / probe_pin_dns / conf13_probe_rebind / probe_variants /
probe_unbracketed）的「修复前必红」钉：

①【高】ai_keys/ai_suggest 出站 check-then-use（guard 解析后丢弃、urllib 连接
   期二次解析）——DNS 重绑定（公网→公网→回环）实测穿透直达服务器回环并送
   Bearer。修复：``_provider_generate`` 自带出站闸（guard 单次解析 + 全地址
   复核），地址表钉进连接层（复用 pdf_fetch_api 的 _PinnedHTTP(S)Handler），
   连接期对原域名**零次**解析、TCP 目标=钉扎地址。

②【中】DNS 解析失败口径改**失败关闭**：ai 侧出站 → ``_ProviderDnsUnavailable
   Error``（503 可重试，绝不裸发）；pdf_fetch guard 空表回退 → ``OutboundError
   (unresolvable_host)``（503 retryable 口径），``_get_bytes`` 空表也失败关闭。
   保存侧（put_settings / save_personal_key）保持「隔离网络自托管域名解析不
   出来不能当攻击拦」的存量放行口径（正钉防回退）。

③【低】IPv4 兼容回环 ``::7f00:1``（含 NAT64 ``64:ff9b::7f00:1``，Python 3.11
   ``is_global=True``）在 ai_keys._address_block_reason 与 pdf_fetch.
   _blocked_host_reason/_is_private_address 三处按低 32 位映射回 IPv4 追加判定；
   ai_keys 未括号 IPv6 base_url（urlsplit hostname='fd00' 错位逃逸）一律拒收。

④【低】decision 通道（post_decision/call_decision）并入 ai_suggest 的
   ``_OUTBOUND_SEMAPHORE``（同一对象，llm+decision 合计 8 并发），超限
   ``_ProviderBusyError`` 503、不重试——verify=true kind=decision 与导入复核
   duplicate/merge 两条路径都在最底层 post_decision 被盖住。

全部零真实外网出站：DNS 桩（ai_keys._resolve_host / pdf_fetch_api.
_resolve_host 缝）、受控 opener 桩、127.0.0.1 真实探针服务器；「钉扎地址
连不通」用 socket.create_connection 缝桩（记录 TCP 目标后立即失败），不向
公网地址发任何包。
"""
from __future__ import annotations

import http.server
import json
import os
import socket
import sys
import tempfile
import threading
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

_fixture = tempfile.TemporaryDirectory(prefix="rf_215sec29b_w4_")
os.environ.setdefault("COBOOKSHELF_DATA_DIR", _fixture.name)

from custom_backend import ai_keys  # noqa: E402
from custom_backend import ai_suggest_api as asa  # noqa: E402
from custom_backend import decision_model_adapter as dma  # noqa: E402
from custom_backend import pdf_fetch_api as pf  # noqa: E402

PUBLIC_IP = "93.184.216.34"
FAKE_KEY = "sk-" + "w4pin-" + "only-0123456789"      # 探针用哑值，非真实凭据


def _desktop():
    return mock.patch.dict(os.environ, {"REVIEWFLOW_MODE": "desktop"})


def _server_mode():
    return mock.patch.dict(os.environ, {"REVIEWFLOW_MODE": "server"})


class _FakeResponse:
    def __init__(self, body: bytes):
        self.body = body

    def read(self, limit=-1):
        if limit is None or limit < 0:
            return self.body
        return self.body[:limit]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _RecordingOpener:
    """受控 opener 桩：记录 (url, timeout, pinned_address, allow_private)。"""

    #: 默认回放体：合法 chat/completions 响应（decision 调用处自行传 systemone 形）。
    DEFAULT_BODY = json.dumps({"choices": [{"message": {"content": "{}"}}],
                               "usage": {}, "model": "m"}).encode()

    def __init__(self, body: bytes | None = None):
        self.body = self.DEFAULT_BODY if body is None else body
        self.calls: list[dict] = []

    def open(self, request, timeout=None):
        self.calls.append({
            "url": request.full_url,
            "timeout": timeout,
            "pinned": getattr(request, "pinned_address", None),
            "allow_private": getattr(request, "pinned_allow_private", None),
        })
        return _FakeResponse(self.body)


def _make_chat_server(seen: dict):
    """127.0.0.1 回环监听器（扮演「服务器自身回环上的内部端点」）。"""

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length)
            seen.setdefault("posts", []).append(
                {"host": self.headers.get("Host"), "path": self.path,
                 "auth": self.headers.get("Authorization"),
                 "body_head": body[:60]})
            out = json.dumps({"choices": [{"message": {"content": "{}"}}],
                              "model": "m", "usage": {}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        def log_message(self, *args):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


def _rebind_connect_seam(attack: str, port: int, connects: list, seen_hits: list):
    """socket.create_connection 缝：模拟「解析器把攻击域名答回环」。

    - 目标是攻击域名（= 连接层仍在按域名连接，未钉扎）→ 转连本机回环监听器
      （重绑定攻击得手，payload 送出）；
    - 目标是钉扎公网地址 → 立即失败（**不向公网发任何包**，只记录 TCP 目标）。
    """
    real_cc = socket.create_connection

    def fake_create_connection(address, *args, **kwargs):
        host = address[0]
        connects.append(host)
        if host == attack:
            seen_hits.append(host)
            return real_cc(("127.0.0.1", address[1] or port), *args, **kwargs)
        if host == PUBLIC_IP:
            raise OSError("pinned public address refused (test stub)")
        return real_cc(address, *args, **kwargs)

    return mock.patch("socket.create_connection", side_effect=fake_create_connection)


# ---------------------------------------------------------------------------
# ① ai 出站钉扎（重绑定缝）
# ---------------------------------------------------------------------------

class AiOutboundPinningPins(unittest.TestCase):
    """guard 单次解析的地址表必须钉进连接层；连接期对原域名零次解析。"""

    def test_guard_resolved_pins_flow_into_request(self):
        """_provider_generate 的出站请求携带 guard 地址（pinned/allow_private）。"""
        resolves: list[str] = []
        with mock.patch.object(ai_keys, "_resolve_host",
                               lambda h, p=443: (resolves.append(h) or [PUBLIC_IP])):
            opener = _RecordingOpener()
            with mock.patch.object(asa, "_OPENER", opener):
                asa._provider_generate("s", "u", api_key=FAKE_KEY,
                                       base_url="http://probe.example.test/v1",
                                       model="m", timeout=5)
        self.assertEqual(resolves, ["probe.example.test"],
                         "每次出站尝试恰好一次 guard 解析（guard 单次解析）")
        self.assertEqual(len(opener.calls), 1, opener.calls)
        self.assertEqual(opener.calls[0]["pinned"], PUBLIC_IP,
                         "连接必须钉扎 guard 解析到的地址")
        self.assertTrue(opener.calls[0]["allow_private"],
                        "ai base 端点钉扎走 guard 口径（desktop 回环放行）")

    def test_rebind_end_to_end_loopback_zero_hits(self):
        """重绑定剧本端到端（conf13 场景 A 复刻）：guard 桩答公网、连接期
        「解析」答回环——TCP 目标必须是钉扎的公网地址，回环监听器零命中
        （修复前 POST 直达回环并带 Bearer，conf13_probe_rebind 实测）。"""
        seen: dict = {}
        srv, port = _make_chat_server(seen)
        attack = "rebind-w4.example.test"
        guard_calls: list[str] = []
        connects: list[str] = []
        rebind_hits: list[str] = []
        try:
            with _server_mode(), \
                    mock.patch.object(ai_keys, "_resolve_host",
                                      lambda h, p=443: (guard_calls.append(h)
                                                        or [PUBLIC_IP])), \
                    _rebind_connect_seam(attack, port, connects, rebind_hits):
                base = ai_keys.base_url_of({"base_url": f"http://{attack}:{port}/v1"})
                with self.assertRaises(asa.ProviderError):
                    asa._provider_generate("s", "u", api_key=FAKE_KEY,
                                           base_url=base, model="m", timeout=5)
            self.assertEqual(connects, [PUBLIC_IP],
                             "TCP 目标必须是钉扎地址（不得按域名连接=不得二次解析）")
            self.assertEqual(rebind_hits, [], "重绑定不得得手")
            self.assertEqual(seen.get("posts") or [], [],
                             "服务器回环绝不能收到出站载荷（Bearer 钥匙不外送）")
            self.assertEqual(guard_calls, [attack] * 2,
                             "解析只发生在 guard 层（路由复核 + 出站闸各一次），"
                             "连接层零次")
        finally:
            srv.shutdown()
            srv.server_close()

    def test_pinned_connection_keeps_host_header_over_real_socket(self):
        """desktop 回环正钉：本地 Ollama 语义不受影响，Host 头仍是原主机名。"""
        seen: dict = {}
        srv, port = _make_chat_server(seen)
        try:
            with _desktop():
                result = asa._provider_generate(
                    "s", "u", api_key=FAKE_KEY,
                    base_url=f"http://127.0.0.1:{port}/v1", model="m", timeout=5)
            self.assertEqual(result["model"], "m")
            self.assertEqual(seen["posts"][0]["host"], f"127.0.0.1:{port}",
                             "钉扎连接的 Host 头必须仍是原主机名")
            self.assertTrue(seen["posts"][0]["auth"], "正钉载荷照常携带")
        finally:
            srv.shutdown()
            srv.server_close()

    def test_mutation_pins_dropped_pin_goes_red(self):
        """破坏性变异：出站闸拆掉（不钉扎、直接发）→ 按域名连接真的发生、
        回环真的收到 POST（= 钉必红）。"""
        seen: dict = {}
        srv, port = _make_chat_server(seen)
        attack = "rebind-mut.example.test"
        connects: list[str] = []
        rebind_hits: list[str] = []
        try:
            # 闸被拆（旧口径：不钉扎直接发）
            with _server_mode(), \
                    mock.patch.object(ai_keys, "_resolve_host",
                                      lambda h, p=443: [PUBLIC_IP]), \
                    mock.patch.object(asa, "_outbound_guard",
                                      lambda b: (b, None)), \
                    _rebind_connect_seam(attack, port, connects, rebind_hits):
                asa._provider_generate("s", "u", api_key=FAKE_KEY,
                                       base_url=f"http://{attack}:{port}/v1",
                                       model="m", timeout=5)
            self.assertEqual(connects, [attack],
                             "闸被拆时连接层按域名连接（= 未钉扎）")
            self.assertEqual(len(seen.get("posts") or []), 1,
                             "防护被拆时回环真的收到 POST（= 钉必红）")
        finally:
            srv.shutdown()
            srv.server_close()


# ---------------------------------------------------------------------------
# ② DNS 解析失败失败关闭（ai 侧 503 可重试 / pdf 侧 guard+空表）
# ---------------------------------------------------------------------------

class AiDnsFailClosedPins(unittest.TestCase):
    """ai 出站：解析失败绝不裸发 → _ProviderDnsUnavailableError（503 可重试）。"""

    def test_dns_failure_fails_closed_with_retryable_503(self):
        opener = _RecordingOpener()
        with mock.patch.object(ai_keys, "_resolve_host",
                               side_effect=OSError("SERVFAIL")), \
                mock.patch.object(asa, "_OPENER", opener):
            with self.assertRaises(asa._ProviderDnsUnavailableError) as ctx:
                asa._provider_generate("s", "u", api_key=FAKE_KEY,
                                       base_url="http://servfail-w4.test/v1",
                                       model="m", timeout=5)
        self.assertEqual(ctx.exception.status, 503)
        self.assertEqual(opener.calls, [], "解析失败绝不出站（不裸发、不钉扎发送）")
        mapped = asa._provider_http_error(ctx.exception, FAKE_KEY)
        self.assertEqual(mapped.status_code, 503)
        self.assertEqual(mapped.detail["code"], "ai_provider_dns_unavailable")
        self.assertIn("稍后重试", mapped.detail["detail"])

    def test_dns_failure_is_retried_once_by_call_provider(self):
        """503 属瞬时错误：_call_provider 重试一档（可重试的结构化降级；
        最终仍失败时按 503 语义抛出——base_url 域名解析失败不是配置错误）。"""
        calls = {"n": 0}

        def _dns_down(*args, **kwargs):
            calls["n"] += 1
            raise asa._ProviderDnsUnavailableError()

        with mock.patch.object(asa, "_provider_generate", _dns_down):
            with self.assertRaises(asa.ProviderError) as ctx:
                asa._call_provider("s", "u", api_key=FAKE_KEY,
                                   base_url="http://servfail-w4.test/v1", model="m")
        self.assertEqual(ctx.exception.status, 503, str(ctx.exception))
        self.assertEqual(calls["n"], asa.PROVIDER_RETRIES)

    def test_outbound_blocked_url_maps_to_422_not_busy(self):
        """guard 被拦（域名解析回回环，server 模式）→ 422 配置错误口径。"""
        with _server_mode(), mock.patch.object(ai_keys, "_resolve_host",
                                               lambda h, p=443: ["127.0.0.1"]):
            with self.assertRaises(asa._ProviderBaseUrlBlockedError) as ctx:
                asa._provider_generate("s", "u", api_key=FAKE_KEY,
                                       base_url="http://loopback-ish-w4.test/v1",
                                       model="m", timeout=5)
        self.assertEqual(ctx.exception.status, 422)
        mapped = asa._provider_http_error(ctx.exception, FAKE_KEY)
        self.assertEqual(mapped.status_code, 422)
        self.assertEqual(mapped.detail["code"], "ai_base_url_blocked")
        self.assertIn("回环", mapped.detail["detail"])

    def test_save_side_stays_lenient_on_dns_failure(self):
        """保存/元数据口径不回退：隔离网络自托管域名解析失败仍可保存。"""
        with mock.patch.object(ai_keys, "_resolve_host",
                               side_effect=OSError("SERVFAIL")):
            self.assertEqual(
                ai_keys.base_url_of({"base_url": "http://isolated-w4.test/v1"}),
                "http://isolated-w4.test/v1")


class PdfGuardFailClosedPins(unittest.TestCase):
    """pdf_fetch：guard 解析失败 / 空表都不再放行未钉扎发送。"""

    def test_guard_servfail_fails_closed_zero_outbound(self):
        """guard 期 SERVFAIL → 出站闸失败关闭，连接期零次按域名连接
        （修复前回环收到 GET，probe_rebind §1 实测）。"""
        seen: dict = {}

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                seen.setdefault("gets", []).append(self.path)
                body = json.dumps({"message": {"DOI": "10.9999/x"}}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        port = srv.server_address[1]
        host = "servfail-w4.example.test"
        connects: list[str] = []
        rebind_hits: list[str] = []
        try:
            with mock.patch.object(pf, "_resolve_host",
                                   side_effect=OSError("SERVFAIL")), \
                    _rebind_connect_seam(host, port, connects, rebind_hits):
                with self.assertRaises(pf.OutboundError) as ctx:
                    pf.fetch_crossref_work(f"http://{host}:{port}", "10.9999/x")
            self.assertEqual(ctx.exception.reason, "unresolvable_host")
            self.assertEqual(connects, [], "失败关闭：不再发起任何连接")
            self.assertEqual(rebind_hits, [])
            self.assertEqual(seen.get("gets") or [], [],
                             "回环监听器绝不能收到 GET（重绑定缝关闭）")
        finally:
            srv.shutdown()
            srv.server_close()

    def test_get_bytes_empty_pin_table_fails_closed(self):
        """空钉扎表（此前 falsy 完全跳过钉扎）→ 失败关闭，绝不出站。"""
        opener = _RecordingOpener()
        with mock.patch.object(pf, "_OPENER", opener):
            with self.assertRaises(pf.OutboundError) as ctx:
                pf._get_bytes("http://x-w4.test/a", timeout=1, max_bytes=10,
                              accept="*/*", pinned_address=[], allow_private_pin=True)
        self.assertEqual(ctx.exception.reason, "unreachable")
        self.assertEqual(opener.calls, [], "空表不得退化为未钉扎发送")

    def test_empty_table_guard_leniency_is_layered_not_bare(self):
        """纵深：即使出站闸退化为旧「空表」形态（guard 被拆），_get_bytes 的
        空表失败关闭仍拦住未钉扎发送——两层闸不共用同一个开关。"""
        connects: list[str] = []
        rebind_hits: list[str] = []
        with mock.patch.object(pf, "_guard_base_url_outbound",
                               lambda base: []), \
                _rebind_connect_seam("x-w4.test", 1, connects, rebind_hits):
            with self.assertRaises(pf.OutboundError) as ctx:
                pf.fetch_crossref_work("http://x-w4.test", "10.9999/x")
        self.assertEqual(ctx.exception.reason, "unreachable",
                         "空表在 _get_bytes 侧失败关闭（不是放行）")
        self.assertEqual(connects, [], "未钉扎发送被第二层拦住")

    def test_unresolvable_maps_to_retryable_503(self):
        """guard 解析失败 → _outbound_http_error 503 + retryable（文件内 503 口径）。"""
        error = pf.OutboundError(
            "unresolvable_host", "配置端点域名解析失败（已失败关闭，不再裸发），请稍后重试。")
        mapped = pf._outbound_http_error(error, {"email": "team@example.test"})
        self.assertEqual(mapped.status_code, 503)
        self.assertEqual(mapped.detail["code"], "pdf_fetch_upstream")
        self.assertEqual(mapped.detail["reason"], "unresolvable_host")
        self.assertTrue(mapped.detail["retryable"])

    def test_save_side_still_lenient_on_dns_failure(self):
        """保存侧（put_settings 的 _base_url_block_reason）对解析失败仍放行。"""
        with mock.patch.object(pf, "_resolve_host", side_effect=OSError("SERVFAIL")):
            self.assertIsNone(pf._base_url_block_reason("http://isolated-w4.example"))

    def test_mutation_guard_lenient_pin_goes_red(self):
        """破坏性变异：出站闸整体拆掉（恒 None=未钉扎直发）→ 回环真的收到
        GET（= 钉必红；对照组 test_empty_table_guard_leniency_is_layered）。"""
        seen: dict = {}

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                seen.setdefault("gets", []).append(self.path)
                body = json.dumps({"message": {"DOI": "10.9999/x"}}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        port = srv.server_address[1]
        host = "servfail-mut.example.test"
        connects: list[str] = []
        rebind_hits: list[str] = []
        try:
            # 闸整体被拆（恒 None=未钉扎直发）
            with mock.patch.object(pf, "_resolve_host",
                                   side_effect=OSError("SERVFAIL")), \
                    mock.patch.object(pf, "_guard_base_url_outbound",
                                      lambda base: None), \
                    _rebind_connect_seam(host, port, connects, rebind_hits):
                message = pf.fetch_crossref_work(f"http://{host}:{port}", "10.9999/x")
            self.assertEqual(message, {"DOI": "10.9999/x"})
            self.assertEqual(connects, [host], "闸被拆时按域名连接（= 未钉扎）")
            self.assertEqual(seen.get("gets"), ["/works/10.9999%2Fx"],
                             "闸被拆时回环真的收到 GET（= 钉必红）")
        finally:
            srv.shutdown()
            srv.server_close()


# ---------------------------------------------------------------------------
# ③ IPv4 兼容回环 / NAT64 归一 + 未括号 IPv6 base_url 拒收
# ---------------------------------------------------------------------------

class EmbeddedIpv4NormalizationPins(unittest.TestCase):
    """::x.y.z.w 与 64:ff9b::x.y.z.w 取低 32 位映射回 IPv4 再判（三处接入）。"""

    def test_ai_keys_blocks_ipv4_compatible_loopback_in_server_mode(self):
        """server 模式：::7f00:1 / 64:ff9b::7f00:1 ≡ 127.0.0.1 → 回环拦截。"""
        for bad in ("http://[::7f00:1]/v1", "http://[64:ff9b::7f00:1]/v1",
                    "http://[0:0:0:0:0:0:7f00:1]/v1"):
            with self.subTest(base=bad):
                with _server_mode():
                    with self.assertRaises(ai_keys.KeyShapeError, msg=bad):
                        ai_keys.base_url_of({"base_url": bad})

    def test_ai_keys_blocks_embedded_linklocal_and_metadata(self):
        """::a9fe:a9fe ≡ 169.254.169.254（云元数据面）：两种模式都拦。"""
        for bad in ("http://[::a9fe:a9fe]/v1", "http://[64:ff9b::a9fe:a9fe]/v1"):
            for mode in (_desktop(), _server_mode()):
                with mode, self.assertRaises(ai_keys.KeyShapeError, msg=bad):
                    ai_keys.base_url_of({"base_url": bad})

    def test_ai_keys_desktop_loopback_forms_consistent_with_v4(self):
        """desktop：::7f00:1 与 127.0.0.1 同口径放行（本地 Ollama 卖点不变）。"""
        with _desktop():
            self.assertTrue(ai_keys.base_url_of({"base_url": "http://[::7f00:1]:11434/v1"}))

    def test_pdf_fetch_blocked_host_reason_and_private_address(self):
        """pdf 侧三处判定接入：链路本地嵌入形拦、私网判定收紧、公网嵌入形放行。"""
        self.assertEqual(pf._blocked_host_reason("::a9fe:a9fe"), "link_local")
        self.assertEqual(pf._blocked_host_reason("64:ff9b::a9fe:a9fe"), "link_local")
        self.assertEqual(pf._base_url_block_reason("http://[64:ff9b::a9fe:a9fe]"),
                         "link_local")
        for value in ("::7f00:1", "64:ff9b::7f00:1", "::a9fe:a9fe",
                      "64:ff9b::a9fe:a9fe"):
            with self.subTest(value=value):
                self.assertTrue(pf._is_private_address(value),
                                "嵌入 IPv4 非公网段必须按内层 IPv4 判（fail closed）")
        # 公网嵌入形（8.8.8.8）不受牵连；回环形在 pdf 口径仍放行（自托管卖点）。
        self.assertFalse(pf._is_private_address("64:ff9b::8.8.8.8"))
        self.assertFalse(pf._is_private_address("::8.8.8.8"))
        self.assertIsNone(pf._blocked_host_reason("::7f00:1"))

    def test_unbracketed_ipv6_base_url_rejected(self):
        """未括号 IPv6（urlsplit hostname='fd00' 错位逃逸）一律拒收并提示方括号。"""
        for bad in ("http://fd00::2/v1", "http://fd00:ec2::254/latest/meta-data/",
                    "http://fe80::1/v1"):
            with self.subTest(base=bad):
                with _server_mode():
                    with self.assertRaises(ai_keys.KeyShapeError) as ctx:
                        ai_keys.base_url_of({"base_url": bad})
                self.assertIn("方括号", str(ctx.exception))
        # 括号形态走既有判定（ULA 拦、desktop 回环放行）不受影响。
        with _server_mode():
            with self.assertRaises(ai_keys.KeyShapeError):
                ai_keys.base_url_of({"base_url": "http://[fd00::2]/v1"})
        with _desktop():
            self.assertTrue(ai_keys.base_url_of({"base_url": "http://[::1]:11434/v1"}))

    def test_mutation_embedded_mapping_removed_pin_goes_red(self):
        """破坏性变异：归一化辅助被拆（恒 None）→ NAT64 链路本地嵌入形放行
        （= 钉必红）。"""
        with mock.patch.object(ai_keys, "_embedded_ipv4", lambda host: None), \
                _server_mode():
            try:
                ai_keys._clean_base_url("http://[64:ff9b::a9fe:a9fe]/v1")
                raised = False
            except ai_keys.KeyShapeError:
                raised = True
        self.assertFalse(raised,
                         "归一化被拆时 NAT64 链路本地嵌入形放行（= 钉必红）")


# ---------------------------------------------------------------------------
# ④ decision 通道并入 _OUTBOUND_SEMAPHORE
# ---------------------------------------------------------------------------

class DecisionSemaphorePins(unittest.TestCase):
    """decision 出站与 LLM 通道共用同一把并发闸（8），超限 503 不重试。"""

    def test_decision_shares_the_same_semaphore_object(self):
        self.assertIs(dma._OUTBOUND_SEMAPHORE, asa._OUTBOUND_SEMAPHORE,
                      "decision 必须复用 ai_suggest 的同一把闸（llm+decision 合计 8）")

    def test_decision_over_limit_fails_fast_busy_503(self):
        """闸满时 decision 出站（verify=true kind=decision 与导入复核
        duplicate/merge 全走 post_decision）快速失败，零出站。"""
        opener = _RecordingOpener()
        held = []
        try:
            for _ in range(asa._PROVIDER_MAX_CONCURRENCY):
                self.assertTrue(asa._OUTBOUND_SEMAPHORE.acquire(False))
                held.append(True)
            with mock.patch.object(dma, "_OPENER", opener):
                with self.assertRaises(asa._ProviderBusyError) as ctx:
                    dma.post_decision({"q": 1}, api_key=FAKE_KEY,
                                      base_url="http://probe-w4.example.test",
                                      model="m", timeout=5)
                with self.assertRaises(asa._ProviderBusyError):
                    dma.call_decision({"q": 1}, api_key=FAKE_KEY,
                                      base_url="http://probe-w4.example.test",
                                      model="m", timeout=5)
            self.assertEqual(ctx.exception.status, 503)
            self.assertEqual(opener.calls, [], "闸满时 decision 绝不出站")
            mapped = asa._provider_http_error(ctx.exception, FAKE_KEY)
            self.assertEqual(mapped.status_code, 503)
            self.assertEqual(mapped.detail["code"], "ai_provider_busy")
        finally:
            for _ in held:
                asa._OUTBOUND_SEMAPHORE.release()

    def test_decision_busy_is_not_retried(self):
        calls = {"n": 0}

        def _busy(*args, **kwargs):
            calls["n"] += 1
            raise asa._ProviderBusyError()

        with mock.patch.object(dma, "post_decision", _busy):
            with self.assertRaises(asa._ProviderBusyError):
                dma.call_decision({"q": 1}, api_key=FAKE_KEY,
                                  base_url="http://probe-w4.example.test",
                                  model="m", timeout=5)
        self.assertEqual(calls["n"], 1, "busy 不重试（重试只会把闸位占得更久）")

    def test_decision_within_limit_works_and_slot_returned_on_error(self):
        opener = _RecordingOpener(
            json.dumps({"model": "m", "answers": {"ping": {"type": "choice",
                                                           "choice": "ok"}},
                        "usage": {}}).encode())
        with mock.patch.object(dma, "_OPENER", opener):
            result = dma.post_decision({"q": 1}, api_key=FAKE_KEY,
                                       base_url="http://probe-w4.example.test",
                                       model="m", timeout=5)
        self.assertEqual(result["answers"]["ping"]["choice"], "ok")
        self.assertEqual(asa._OUTBOUND_SEMAPHORE._value, asa._PROVIDER_MAX_CONCURRENCY,
                         "成功路径闸位归还")

        def _boom(request, timeout=None):
            raise urllib.error.URLError("broken")

        boom = _RecordingOpener(b"")
        boom.open = _boom
        with mock.patch.object(dma, "_OPENER", boom):
            with self.assertRaises(asa.ProviderError):
                dma.post_decision({"q": 1}, api_key=FAKE_KEY,
                                  base_url="http://probe-w4.example.test",
                                  model="m", timeout=5)
        self.assertEqual(asa._OUTBOUND_SEMAPHORE._value, asa._PROVIDER_MAX_CONCURRENCY,
                         "异常路径闸位归还（finally 语义，不泄漏）")

    def test_llm_and_decision_compete_for_the_same_gate(self):
        """llm 占满 8 位后 decision 同样被拦（同一对象，不是两把各 8 的闸）。"""
        held = []
        try:
            for _ in range(asa._PROVIDER_MAX_CONCURRENCY):
                self.assertTrue(asa._OUTBOUND_SEMAPHORE.acquire(False))
                held.append(True)
            opener = _RecordingOpener()
            with mock.patch.object(asa, "_OPENER", opener):
                with self.assertRaises(asa._ProviderBusyError):
                    asa._provider_generate("s", "u", api_key=FAKE_KEY,
                                           base_url="http://probe-w4.example.test/v1",
                                           model="m", timeout=5)
                with self.assertRaises(asa._ProviderBusyError):
                    dma.post_decision({"q": 1}, api_key=FAKE_KEY,
                                      base_url="http://probe-w4.example.test",
                                      model="m", timeout=5)
        finally:
            for _ in held:
                asa._OUTBOUND_SEMAPHORE.release()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
