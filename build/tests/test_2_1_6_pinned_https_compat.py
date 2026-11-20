# -*- coding: utf-8 -*-
"""2.1.6 钉扎 HTTPS handler 跨 stdlib 形态兼容回归（用户报 2.1.5 服务器 AI
测试/使用 500 的窄修正钉）。

事故与修（见 out/maintenance216/ai_https_compat_20261115/）：
- 2.1.5 ``_PinnedHTTPSHandler.https_open`` 无条件读 ``self._check_hostname``；
  Python 3.12 起 ``urllib.HTTPSHandler`` 不再设置该私有属性、
  ``http.client.HTTPSConnection`` 也不再接收 ``check_hostname`` 形参 →
  新箱（实测 3.12.13）钉扎 HTTPS 出站直接 AttributeError，AI Key 测试/
  使用两条路径 500。
- 2.1.6 修：context 恒透传；仅旧形态属性实际存在时才透传
  ``check_hostname``（保 ≤3.11 显式 False/True/None 语义）。

本文件正钉（全部真实 ``https_open`` 故障点，非源码字符串/假 handler 跳过）：
1. 3.12+ 形态（handler 无 ``_check_hostname``；≤3.11 运行箱上删除该实例
   属性模拟）钉扎请求经真实 do_open→原生 HTTPSConnection 初始化：只允许
   连接层 URLError，不得 AttributeError/unsupported-kwarg TypeError；TCP
   目标=钉扎 IP。同一形态再经 ``_build_opener()`` 真实接线复验。
2. 全链路（真实 do_open：原生连接初始化→request→getresponse，仅
   socket.create_connection 缝回放假套接字 + context.wrap_socket 缝捕获
   SNI）：Host 头与 TLS server_hostname=原始主机名、钉扎目标不漂移、
   响应正常回传。
3. 旧形态显式 False/True/None 原样透传（受控 do_open 观察——不把旧
   kwargs 强塞进现代原生连接）；context 同对象透传且 verify_mode/
   CHECK_HOSTNAME 不被改动；``_pinned_address``/``_pinned_allow_private``
   （双旗）正确落到钉扎连接类，私有 pin 旗不丢。
4. 无 pin 请求原样委托 stdlib ``HTTPSHandler.https_open``。

诚实边界：零真实外网出站（DNS 从不解析原主机名：钉扎连接不读
address[0]；TCP 走缝）；模块顶层零产品 imports、零环境变更（产品导入在
setUpClass ``_bare_env`` 借用之后——沿 test_2_1_6_team_screening_runtime
范式，不复制其生命周期框架）；本文由独立执行者在新源码箱实跑，作者本轮
只做 AST/static 验证、不自证 PASS。
"""
from __future__ import annotations

import io
import socket
import ssl
import sys
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
TESTS_DIR = Path(__file__).resolve().parent
for _p in (str(REPO_ROOT), str(TESTS_DIR)):
    if _p and _p not in sys.path:
        sys.path.insert(0, _p)

import _bare_env  # noqa: E402  build/tests 测试基建（非产品）

PUBLIC_IP = "93.184.216.34"       # 钉扎目标（哑公网值，零真实出站）
ORIG_HOST = "compat-216.invalid"  # 原始主机名（.invalid 且从不被解析）
RESPONSE_200 = b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok"


def _import_pdf():
    """setUpClass 环境 owned 后的产品导入（唯一入口；箱身份核验）。"""
    import custom_backend.pdf_fetch_api as pf
    src = Path(pf.__file__).resolve()
    assert src.is_relative_to(REPO_ROOT), ("product import escaped box", str(src))
    return pf


class PinnedHttpsCompatTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._token = _bare_env.acquire()
        cls.addClassCleanup(_bare_env.release, cls._token)
        cls.pf = _import_pdf()

    # -- 构造辅助 -----------------------------------------------------------

    def _pinned_req(self, *, allow_private=False):
        req = urllib.request.Request(f"https://{ORIG_HOST}/x")
        # stdlib 调用约定：OpenerDirector.open 会注入 req.timeout，裸
        # Request 直调 handler 时 do_open 构造连接也读它——须自带（GF 实跑
        # 缺口：真实 do_open 场景 Request 无 timeout）。
        req.timeout = 1.0
        req.pinned_address = PUBLIC_IP
        if allow_private:
            req.pinned_allow_private = True
        return req

    def _modern_handler(self, context=None):
        """真实产品 handler，3.12+ 形态：≤3.11 箱上删 _check_hostname 模拟
        （现代 stdlib 构造即无此属性；删除后 vars 与新箱一致）。"""
        handler = self.pf._PinnedHTTPSHandler()
        if hasattr(handler, "_check_hostname"):
            del handler._check_hostname
        self.assertNotIn("_check_hostname", vars(handler),
                         "前提：3.12+ 形态 handler 实例无 _check_hostname")
        if context is not None:
            handler._context = context
        return handler

    @staticmethod
    def _capturing_do_open(handler, captured):
        def fake_do_open(conn_class, request, **kwargs):
            captured["class"] = conn_class
            captured["kwargs"] = kwargs
            return "response"

        return mock.patch.object(handler, "do_open", fake_do_open)

    # -- ① 3.12+ 形态：真实 do_open + 原生连接初始化不得属性/kwarg 崩 -----

    def test_modern_shape_real_do_open_only_connection_error_allowed(self):
        """真实 https_open→真实 do_open→原生 HTTPSConnection 初始化（正是
        500 崩点两处）：只允许连接层 URLError；TCP 目标必须是钉扎 IP。"""
        dial: list[tuple] = []

        def refused(address, timeout=None, source_address=None):
            dial.append(address)
            raise ConnectionRefusedError(10061, "simulated down (seam)")

        handler = self._modern_handler(ssl.create_default_context())
        with mock.patch.object(socket, "create_connection", refused):
            with self.assertRaises(urllib.error.URLError):
                handler.https_open(self._pinned_req())
        self.assertEqual(dial, [(PUBLIC_IP, 443)],
                         "TCP 目标必须是钉扎 IP（不得解析原主机名）")

    def test_modern_shape_through_shared_opener_wiring(self):
        """同一 3.12+ 形态经 _build_opener() 真实接线（空代理/拒重定向/双
        钉扎 handler）出站：同样只允许连接层 URLError（新箱即事故路径）。"""
        dial: list[tuple] = []

        def refused(address, timeout=None, source_address=None):
            dial.append(address)
            raise ConnectionRefusedError(10061, "simulated down (seam)")

        opener = self.pf._build_opener()
        with mock.patch.object(socket, "create_connection", refused):
            with self.assertRaises(urllib.error.URLError):
                opener.open(self._pinned_req(), timeout=1.0)
        self.assertEqual(dial, [(PUBLIC_IP, 443)])

    # -- ② 3.12+ 形态全链路：Host/SNI/钉扎目标/响应回传 ---------------------

    def test_modern_shape_full_cycle_keeps_host_sni_and_pin(self):
        """真实 do_open 全链路（原生连接初始化→request→getresponse）；仅
        socket.create_connection 缝（记录目标+假套接字）与 context.
        wrap_socket 缝（捕获 server_hostname、回还原 sock）。"""
        dial: list[tuple] = []
        state = {"sent": b""}
        sni: dict = {}

        class FakeSock:
            def setsockopt(self, *args):
                pass

            def getpeername(self):
                return (PUBLIC_IP, 443)

            def sendall(self, data):
                state["sent"] += bytes(data)

            def makefile(self, mode, *args, **kwargs):
                return io.BytesIO(RESPONSE_200)

            def close(self):
                pass

        def fake_create(address, timeout=None, source_address=None):
            dial.append(address)
            return FakeSock()

        handler = self._modern_handler()
        handler._context = mock.Mock()
        handler._context.wrap_socket = lambda sock, server_hostname=None: (
            sni.setdefault("value", server_hostname), sock)[-1]

        with mock.patch.object(socket, "create_connection", fake_create):
            resp = handler.https_open(self._pinned_req())
        self.assertEqual(getattr(resp, "status", None), 200,
                         "真实 do_open 全链路必须正常回传响应")
        self.assertEqual(dial, [(PUBLIC_IP, 443)], "TCP 目标=钉扎 IP")
        self.assertEqual(sni.get("value"), ORIG_HOST,
                         "TLS server_hostname(SNI)=原始主机名")
        self.assertIn(f"Host: {ORIG_HOST}".encode(), state["sent"],
                      "Host 头=原始主机名")

    # -- ③ 旧形态参数语义 + context/旗标不变量（受控 do_open 观察） ---------

    def test_legacy_check_hostname_false_true_none_pass_through(self):
        """≤3.11 旧形态显式 False/True/None 必须原样透传（含 None——旧版
        stdlib 自身 https_open 亦恒传）；不把旧 kwargs 强塞现代原生连接。"""
        for value in (False, True, None):
            with self.subTest(check_hostname=value):
                handler = self.pf._PinnedHTTPSHandler()
                handler._context = "ctx-sentinel-216"
                handler._check_hostname = value
                captured: dict = {}
                with self._capturing_do_open(handler, captured):
                    self.assertEqual(handler.https_open(self._pinned_req()),
                                     "response")
                self.assertEqual(captured["kwargs"],
                                 {"context": "ctx-sentinel-216",
                                  "check_hostname": value})

    def test_context_identity_flags_and_pin_flags_preserved(self):
        """两分支共通不变量：context 同对象透传且 verify_mode/CHECK_HOSTNAME
        不被改动；钉扎地址与私有 pin 双旗（allow_private_pin/pinned_
        allow_private）单真+均假组合实测落到连接类、旗不丢。"""
        ctx = ssl.create_default_context()
        before = (ctx.verify_mode, ctx.check_hostname)
        for modern in (True, False):
            with self.subTest(modern=modern):
                handler = self.pf._PinnedHTTPSHandler()
                if modern:
                    if hasattr(handler, "_check_hostname"):
                        del handler._check_hostname
                else:
                    handler._check_hostname = None
                handler._context = ctx
                # Terra 补测：双旗 OR 语义逐旗单真+两旗均假（真实 handler +
                # 受控 do_open；_pinned_req helper 不动，旗 attr 直接设 req）。
                for label, flag_name in (
                        ("两旗均缺省(双 false)", None),
                        ("仅 allow_private_pin=True(_get_bytes 双写旗)",
                         "allow_private_pin"),
                        ("仅 pinned_allow_private=True(ai_suggest 直设旗)",
                         "pinned_allow_private")):
                    captured: dict = {}
                    req = self._pinned_req(
                        allow_private=flag_name == "pinned_allow_private")
                    if flag_name == "allow_private_pin":
                        req.allow_private_pin = True
                    with self._capturing_do_open(handler, captured):
                        handler.https_open(req)
                    self.assertIs(captured["kwargs"]["context"], ctx,
                                  "context 必须同对象透传")
                    self.assertEqual(captured["class"]._pinned_address,
                                     PUBLIC_IP)
                    self.assertIs(captured["class"]._pinned_allow_private,
                                  flag_name is not None,
                                  f"{label}: 双旗 OR 语义（单真→真、均假→假）")
                    if modern:
                        self.assertNotIn("check_hostname", captured["kwargs"])
                    else:
                        self.assertIs(captured["kwargs"]["check_hostname"], None)
        self.assertEqual((ctx.verify_mode, ctx.check_hostname), before,
                         "透传不得改动 context 的校验旗")

    # -- ④ 无 pin：原样委托 stdlib ------------------------------------------

    def test_no_pin_delegates_to_stdlib_https_open(self):
        """无 pinned_address 的请求必须走 stdlib HTTPSHandler.https_open；
        本类的钉扎连接类/do_open 一概不得介入。"""
        req = urllib.request.Request(f"https://{ORIG_HOST}/y")  # 无钉扎
        handler = self.pf._PinnedHTTPSHandler()
        parent = mock.Mock(return_value="stdlib-sentinel")
        with mock.patch.object(urllib.request.HTTPSHandler,
                               "https_open", parent):
            self.assertEqual(handler.https_open(req), "stdlib-sentinel")
        parent.assert_called_once_with(req)


if __name__ == "__main__":
    unittest.main()
