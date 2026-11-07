"""r13 · PDF 出站 DNS 钉扎钉子（终扫中危 be-outbound-0）。

问题：``pdf_fetch_api`` 的公网校验（``_resolve_host`` → ``getaddrinfo``）与真正
下载（urllib 连接时再解析）是**两次独立 DNS 解析**。攻击者可让第一次答公网、
第二次答 127.0.0.1（DNS rebinding），服务端就会访问回环/内网，并经
「上游返回 HTTP {code}」把内部端点状态回显成探测 oracle。

钉子口径（撤修复必红）：
- 解析一次 → 校验 → **钉住已校验 IP 连接**；校验后的 DNS 再变也不影响连接目标；
- Host 头与 HTTPS 的 SNI/证书主机名仍是原始主机名（不是钉住的 IP）；
- TCP 建立后再核对 peer 地址仍属公网，否则拒绝；
- ``_fetch_one`` 必须把候选目标里已校验的地址传给 ``_get_bytes``。

探针脚本来源：``E:/rf214tmp/dwave/be-outbound/probe_rebind.py`` 与
``E:/rf214tmp/dwave/confirm-be-outbound-0/probe_rebind_confirm.py``（改为 pytest，
零真实外网：假解析器 + 假 ``socket.create_connection``，本机探针服务只监听回环）。
"""
from __future__ import annotations

import socket
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from custom_backend import pdf_fetch_api  # noqa: E402

HOST = "rebind-r13.example"
#: 公网测试地址（RFC 5737 测试段之外仅作「假解析器第一答」，测试里不会真连）。
PUBLIC_IP = "93.184.216.34"
LOOPBACK = "127.0.0.1"


class _ProbeHandler(BaseHTTPRequestHandler):
    """本机探针服务：任何命中都记录路径（命中即证明被当成内网探针）。"""

    hits: list[str] = []

    def do_GET(self):  # noqa: N802  BaseHTTPRequestHandler 约定
        type(self).hits.append(self.path)
        body = b"%PDF-1.4 rebind-r13"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # noqa: D102  静默探针
        pass


class _FakeResolver:
    """DNS 桩：目标主机先答公网测试地址，之后答回环（rebinding 形态）。"""

    def __init__(self, host: str, answers: list[str]):
        self.host = host
        self.answers = list(answers)
        self.calls: list[str] = []

    def __call__(self, host, port=0, *args, **kwargs):
        if host == self.host:
            self.calls.append(host)
            ip = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
            socktype = kwargs.get("type") or (args[1] if len(args) > 1 else socket.SOCK_STREAM)
            return [(socket.AF_INET, socktype, socket.IPPROTO_TCP, "", (ip, port))]
        return _REAL_GETADDRINFO(host, port, *args, **kwargs)


_REAL_GETADDRINFO = socket.getaddrinfo
_REAL_CREATE_CONNECTION = socket.create_connection


class PinningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _ProbeHandler.hits = []
        cls.server = HTTPServer((LOOPBACK, 0), _ProbeHandler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def setUp(self):
        _ProbeHandler.hits = []
        self.resolver = _FakeResolver(HOST, [PUBLIC_IP, LOOPBACK])
        self.attempts: list[str] = []

        def fake_create_connection(address, *args, **kwargs):
            host = address[0]
            self.attempts.append(host)
            if host == PUBLIC_IP:
                # 假「公网测试地址不可达」：立即失败，避免真实外网连接。
                raise ConnectionRefusedError(10061, "simulated unreachable public test address")
            return _REAL_CREATE_CONNECTION(address, *args, **kwargs)

        self._patches = [
            mock.patch.object(socket, "getaddrinfo", self.resolver),
            mock.patch.object(socket, "create_connection", fake_create_connection),
        ]
        for patcher in self._patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def tearDown(self):
        _ProbeHandler.hits = []

    # -- 核心：解析一次、校验、钉住已校验 IP --------------------------------

    def test_rebinding_dns_cannot_pivot_to_loopback(self):
        """第一次答公网、第二次答 127.0.0.1：连接必须只去已校验的钉住地址。"""
        url = f"http://{HOST}:{self.port}/paper.pdf"
        with mock.patch.object(pdf_fetch_api, "_OPENER", pdf_fetch_api._build_opener()):
            target, reason = pdf_fetch_api._classify_fetchable_target(url)
            self.assertIsNone(reason)
            self.assertEqual(target["address"], PUBLIC_IP)
            with self.assertRaises(pdf_fetch_api.OutboundError) as caught:
                pdf_fetch_api._get_bytes(
                    target["url"], timeout=2.0, max_bytes=1024 * 1024,
                    accept="application/pdf", pinned_address=target["address"])

        self.assertIn(caught.exception.reason, ("unreachable", "timeout"))
        self.assertEqual(_ProbeHandler.hits, [],
                         "本机探针服务必须零命中（不得被 DNS 重绑定带到回环）")
        self.assertEqual(self.resolver.calls, [HOST],
                         "同一候选 URL 只允许解析一次，连接阶段不得再解析")
        self.assertIn(PUBLIC_IP, self.attempts, "连接目标必须是已校验的钉住地址")
        self.assertNotIn(LOOPBACK, self.attempts, "绝不允许连接回环地址")

    def test_download_path_passes_pinned_address(self):
        """``_fetch_one`` 必须把候选目标的已校验地址交给 ``_get_bytes``。"""
        target = {"url": "http://oa.example/p.pdf", "host": "oa.example",
                  "port": 80, "address": PUBLIC_IP}
        captured: dict = {}

        def fake_get_bytes(url, *, timeout, max_bytes, accept, pinned_address=None):
            captured.update(url=url, pinned_address=pinned_address)
            raise pdf_fetch_api.OutboundError("unreachable", "stub down")

        with mock.patch.object(pdf_fetch_api.fulltext_mod, "get_fulltext",
                               return_value=None), \
                mock.patch.object(pdf_fetch_api, "_unpaywall_pdf_candidates",
                                  return_value=([target], [])), \
                mock.patch.object(pdf_fetch_api, "_get_bytes", fake_get_bytes):
            with self.assertRaises(pdf_fetch_api.OutboundError):
                pdf_fetch_api._fetch_one(
                    Path("unused.db"), "KEY", {"doi": "10.9999/xyz"},
                    settings={}, pdfs_dir=Path("unused"), trace_context=None)

        self.assertEqual(captured["url"], target["url"])
        self.assertEqual(captured["pinned_address"], PUBLIC_IP,
                         "下载路径必须钉住分类阶段已校验的 IP")

    # -- Host 头 / SNI / 证书主机名保持原始主机名 ----------------------------

    def test_pinned_https_keeps_hostname_for_sni_and_host_header(self):
        made: list[tuple] = []
        wrapped: dict = {}

        class FakeSock:
            def setsockopt(self, *args):
                pass

            def getpeername(self):
                return (PUBLIC_IP, 443)

            def close(self):
                pass

        def fake_create(address, timeout=None, source_address=None):
            made.append(address)
            return FakeSock()

        conn = pdf_fetch_api._PinnedHTTPSConnection(HOST, 443)
        conn._pinned_address = PUBLIC_IP
        conn._create_connection = conn._connect_pinned
        conn._context = mock.Mock()
        conn._context.wrap_socket = lambda sock, server_hostname=None: (
            wrapped.setdefault("sni", server_hostname), sock)[-1]

        with mock.patch.object(socket, "create_connection", fake_create):
            conn.connect()

        self.assertEqual(made, [(PUBLIC_IP, 443)], "TCP 目标必须是钉住的 IP")
        self.assertEqual(conn.host, HOST, "Host 头仍用原始主机名")
        self.assertEqual(wrapped["sni"], HOST, "SNI/证书主机名必须是原始主机名")

    def test_private_peer_after_connect_is_rejected(self):
        """连接建立后 peer 若不属于公网，必须关闭并拒绝（第二道防线）。"""

        class FakeSock:
            closed = False

            def setsockopt(self, *args):
                pass

            def getpeername(self):
                return (LOOPBACK, 80)

            def close(self):
                FakeSock.closed = True

        conn = pdf_fetch_api._PinnedHTTPConnection(HOST, 80)
        conn._pinned_address = PUBLIC_IP
        conn._create_connection = conn._connect_pinned
        with mock.patch.object(socket, "create_connection", lambda *a, **k: FakeSock()):
            with self.assertRaises(OSError):
                conn.connect()
        self.assertTrue(FakeSock.closed, "peer 非公网时套接字必须关闭")

    # -- opener 接线：请求的 pinned_address 必须落到钉扎连接类 ---------------

    def test_http_handler_selects_pinned_connection_class(self):
        import urllib.request

        req = urllib.request.Request(f"http://{HOST}:{self.port}/x.pdf")
        req.pinned_address = PUBLIC_IP
        handler = pdf_fetch_api._PinnedHTTPHandler()
        captured: dict = {}

        def fake_do_open(conn_class, request, **kwargs):
            captured["class"] = conn_class
            captured["kwargs"] = kwargs
            return "response"

        with mock.patch.object(handler, "do_open", fake_do_open):
            self.assertEqual(handler.http_open(req), "response")
        self.assertTrue(issubclass(captured["class"],
                                   pdf_fetch_api._PinnedHTTPConnection))
        self.assertEqual(captured["class"]._pinned_address, PUBLIC_IP)

    def test_https_handler_selects_pinned_connection_class_and_keeps_tls_args(self):
        import urllib.request

        req = urllib.request.Request(f"https://{HOST}/x.pdf")
        req.pinned_address = PUBLIC_IP
        handler = pdf_fetch_api._PinnedHTTPSHandler()
        handler._context = "ctx-sentinel"
        handler._check_hostname = True
        captured: dict = {}

        def fake_do_open(conn_class, request, **kwargs):
            captured["class"] = conn_class
            captured["kwargs"] = kwargs
            return "response"

        with mock.patch.object(handler, "do_open", fake_do_open):
            self.assertEqual(handler.https_open(req), "response")
        self.assertTrue(issubclass(captured["class"],
                                   pdf_fetch_api._PinnedHTTPSConnection))
        self.assertEqual(captured["class"]._pinned_address, PUBLIC_IP)
        self.assertEqual(captured["kwargs"],
                         {"context": "ctx-sentinel", "check_hostname": True},
                         "TLS context/证书校验参数必须原样透传")

    def test_get_bytes_refuses_private_pinned_address(self):
        """防御纵深：即便调用方传了私网钉扎地址，也必须在建连前拒绝。"""
        with self.assertRaises(pdf_fetch_api.OutboundError) as caught:
            pdf_fetch_api._get_bytes(
                "http://example.invalid/x.pdf", timeout=1.0, max_bytes=16,
                accept="application/pdf", pinned_address=LOOPBACK)
        self.assertEqual(caught.exception.reason, "private_host")


if __name__ == "__main__":
    unittest.main()
