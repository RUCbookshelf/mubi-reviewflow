# -*- coding: utf-8 -*-
"""2.1.5 安全修复波 sec29（fix/2.1.5-sec29）· F 组出站通道钉组（专属新文件）。

deepseek 安全复审六项的「修复前必红」钉子（探针原件：
E:/rf214tmp/hetero-review/DS·出站 HTTP、DS.AI出站通道）：

F-1/F-2（update_check.py，中危/低危）：
- 默认 urlopen 跟随重定向 + 读环境代理 → 显式空 ProxyHandler + 拒重定向 opener；
- ``json.load(response)`` 无读取上限 → ``read(MAX+1)`` 有界读取；
- 上游形状（非 dict / assets 非 list-of-dict）→ 按「无更新」降级，不 500；
- ``release_url``/``asset_url`` 只回 http/https（前端 window.open 直开）。

F-3（pdf_fetch_api.py，中危）：``_get_json`` 未把 ``_guard_base_url_outbound``
解析到的地址传给 ``_get_bytes`` 钉扎分支——guard 校验（公网）与连接（可被
DNS 重绑定到内网）之间有缝；修复后 base 端点连接钉扎同一次解析的地址。

F-4（coscreen/notify.py，中危）：apprise URL 的 ``[IPv6]`` 主机绕过
``_check_host_literal`` 私网检查（``ip_address("[::1]")`` 解析失败 → 放行）；
修复后剥方括号拦截 ULA/链路本地/IPv4 映射内网。钉在
test_2_1_5_notify_sec_pins.py（notify 对应套件）与本文件各半。

F-5（custom_backend/ai_keys.py，中危）：RFC1918/CGNAT 私网段拦截（回环按
运行模式例外：desktop 放行本地 Ollama、server 拦截）+ 域名一次 DNS 解析
复核（解析到私网/元数据/链路本地即拒，保存与出站两处）。

F-6（custom_backend/ai_suggest_api.py，低危）：出站调用无并发限 → 模块级
信号量（8），超限 503 结构化 ``ai_provider_busy``。

全部零真实外网出站：假 opener / DNS 桩 / 受控回环探针服务器（127.0.0.1）。
"""
from __future__ import annotations

import http.server
import io
import ipaddress
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from email.message import Message
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

_fixture = tempfile.TemporaryDirectory(prefix="rf_215sec29_f_")
os.environ.setdefault("COBOOKSHELF_DATA_DIR", _fixture.name)

from coscreen import notify as notify_core  # noqa: E402
from custom_backend import ai_keys  # noqa: E402
from custom_backend import ai_suggest_api  # noqa: E402
from custom_backend import pdf_fetch_api  # noqa: E402
from custom_backend import update_check as uc  # noqa: E402
from fastapi import HTTPException  # noqa: E402

PUBLIC_IP = "93.184.216.34"

#: 与 update_check._platform_asset 同口径的本机资产名（跨平台跑测试用）。
_ASSET_NAME = ("reviewflow-setup-x64.exe" if sys.platform == "win32"
               else "reviewflow.dmg" if sys.platform == "darwin"
               else "reviewflow-x86_64.appimage")


def _desktop():
    return mock.patch.dict(os.environ, {"REVIEWFLOW_MODE": "desktop"})


def _server_mode():
    return mock.patch.dict(os.environ, {"REVIEWFLOW_MODE": "server"})


class _FakeResponse:
    """受控响应桩：记录 read(limit) 的 limit（读取上限钉用）。"""

    def __init__(self, body: bytes):
        self.body = body
        self.read_limits: list = []

    def read(self, limit=-1):
        self.read_limits.append(limit)
        if limit is None or limit < 0:
            return self.body
        return self.body[:limit]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _RecordingOpener:
    """受控 opener 桩：记录 (url, timeout, pinned_address, allow_private)。"""

    def __init__(self, body: bytes = b"{}"):
        self.body = body
        self.calls: list[dict] = []
        self.responses: list[_FakeResponse] = []

    def open(self, request, timeout=None):
        response = _FakeResponse(self.body)
        self.calls.append({
            "url": request.full_url,
            "timeout": timeout,
            "pinned": getattr(request, "pinned_address", None),
            "allow_private": getattr(request, "pinned_allow_private", None),
        })
        self.responses.append(response)
        return response


# ---------------------------------------------------------------------------
# F-1/F-2：update_check 出站治理（opener / 读取上限 / 形状 / scheme）
# ---------------------------------------------------------------------------

class UpdateCheckOpenerPins(unittest.TestCase):
    """F-1①：更新检查必须用显式空代理 + 拒重定向 opener（修复前用默认
    urlopen：代理环境整包外发、30x 被跟随——probe_update_check_urllib.py）。"""

    def test_opener_ignores_proxy_environment(self):
        def proxy_routing(opener):
            return sorted(name for name, _func in opener.handle_open.items()
                          for _h in opener.handle_open[name]
                          if type(_h).__name__ == "ProxyHandler")

        with mock.patch.dict(os.environ, {"HTTP_PROXY": "http://127.0.0.1:9",
                                          "HTTPS_PROXY": "http://127.0.0.1:9",
                                          "http_proxy": "http://127.0.0.1:9"}):
            default_opener = urllib.request.build_opener()
            self.assertTrue(proxy_routing(default_opener),
                            "前提：默认 opener 在代理环境下会走代理（漏洞形态）")
            ours = uc._build_opener()
            self.assertEqual(proxy_routing(ours), [],
                             "更新检查 opener 必须忽略 HTTP(S)_PROXY（空 ProxyHandler）")
        self.assertTrue(any(type(h).__name__ == "_NoRedirectHandler"
                            for h in ours.handlers),
                        "拒绝重定向 handler 必须在位")

    def test_redirect_refused_over_real_socket(self):
        """127.0.0.1 真实探针服务器：302 必须变成 HTTPError，/target 零命中
        （修复前默认 urlopen 跟随 → 200 + target 被打开）。"""
        hits = {"target": 0}

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/start":
                    self.send_response(302)
                    self.send_header("Location", "/target")
                    self.end_headers()
                elif self.path == "/target":
                    hits["target"] += 1
                    body = b'{"tag_name": "v9.9.9"}'
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    self.send_response(404)
                    self.end_headers()

            def log_message(self, *args):
                pass

        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            with self.assertRaises(urllib.error.HTTPError):
                uc._OPENER.open(
                    f"http://127.0.0.1:{srv.server_address[1]}/start", timeout=5)
            self.assertEqual(hits["target"], 0, "重定向目标绝不能被打开")
        finally:
            srv.shutdown()
            srv.server_close()


class UpdateCheckBodyPins(unittest.TestCase):
    """F-1②③ + F-2：读取上限 / 形状降级 / URL scheme 白名单。"""

    def setUp(self):
        uc._cache = None                     # 逐用例绕开 15 分钟缓存
        self.addCleanup(setattr, uc, "_cache", None)

    def _release_body(self, **overrides) -> bytes:
        payload = {"tag_name": "v0.0.1", "name": "r", "body": "notes",
                   "html_url": "https://github.com/x/y/releases/tag/v0.0.1",
                   "assets": [{"name": _ASSET_NAME,
                               "browser_download_url": "https://github.com/x/y/z.pkg"}]}
        payload.update(overrides)
        return json.dumps(payload).encode("utf-8")

    def _check(self, body: bytes) -> dict:
        """清缓存 + 受控 opener 下跑一次 check_latest_release。"""
        uc._cache = None
        opener = _RecordingOpener(body)
        with mock.patch.object(uc, "_OPENER", opener):
            return uc.check_latest_release()

    def test_check_uses_module_opener_with_bounded_read(self):
        """check_latest_release 走模块 opener，read 携带上限参数（修复前
        json.load(response) 全量吞 body、且用默认 urlopen）。"""
        opener = _RecordingOpener(self._release_body())
        with mock.patch.object(uc, "_OPENER", opener):
            uc.check_latest_release()
        self.assertEqual(len(opener.calls), 1)
        self.assertEqual(opener.calls[0]["url"],
                         "https://api.github.com/repos/RUCbookshelf/mubi-reviewflow/releases/latest")
        cap = uc._MAX_RELEASE_JSON_BYTES
        self.assertEqual(opener.responses[0].read_limits, [cap + 1],
                         "read 必须带 MAX+1 的 limit（内存上界）")

    def test_response_over_cap_is_503(self):
        cap = uc._MAX_RELEASE_JSON_BYTES
        opener = _RecordingOpener(b"x" * (cap + 1))
        with mock.patch.object(uc, "_OPENER", opener):
            with self.assertRaises(HTTPException) as ctx:
                uc.check_latest_release()
        self.assertEqual(ctx.exception.status_code, 503)

    def test_malformed_shapes_degrade_to_no_update_never_raise(self):
        """形状闸：非 dict / assets 非 list-of-dict → has_update=False、不抛
        （修复前 ``[].get`` / dict 迭代取 .get → AttributeError → 500）。"""
        for body in (b"[]", b'"a-string"', b"42", b"null",
                     b'{"tag_name": "v9.9.9", "assets": {"a": 1}}',
                     b'{"tag_name": "v9.9.9", "assets": [1, "x", null]}',
                     b'{"tag_name": {"nested": 1}, "assets": []}'):
            with self.subTest(body=body[:48]):
                result = self._check(body)
                self.assertFalse(result["has_update"], body[:48])
                self.assertIsNone(result["release_url"], body[:48])
                self.assertIsNone(result["asset_url"], body[:48])
        # 形状坏的结果不得进缓存：换一个正常上游应立刻反映新结果。
        self.assertTrue(self._check(self._release_body(tag_name="v9.9.9"))["has_update"])

    def test_release_and_asset_urls_http_schemes_only(self):
        """F-2：javascript:/file:/data: 等 scheme 一律置 None（前端
        window.open 直开；修复前原样回传）。"""
        result = self._check(self._release_body(
            html_url="javascript:alert(1)",
            assets=[{"name": _ASSET_NAME,
                     "browser_download_url": "jAvAsCrIpT:alert(2)"}]))
        self.assertIsNone(result["release_url"])
        self.assertIsNone(result["asset_url"])
        # 合法 http/https 原样回传；缺失/畸形同样置 None（逐场景绕开缓存）。
        ok = self._check(self._release_body(
            html_url="https://github.com/x/y",
            assets=[{"name": _ASSET_NAME,
                     "browser_download_url": "https://github.com/x/y/z.pkg"}]))
        self.assertEqual(ok["release_url"], "https://github.com/x/y")
        self.assertEqual(ok["asset_url"], "https://github.com/x/y/z.pkg")
        missing = self._check(self._release_body(
            html_url="https://github.com/x/y", assets=[{"name": _ASSET_NAME}]))
        self.assertIsNone(missing["asset_url"])
        weird = self._check(self._release_body(html_url="http://[::1"))
        self.assertIsNone(weird["release_url"])

    def test_mutation_scheme_filter_neutered_pin_goes_red(self):
        """破坏性变异：_http_url_or_none 恒放行 → scheme 钉必红。"""
        with mock.patch.object(uc, "_http_url_or_none",
                               lambda value: str(value or "")):
            opener = _RecordingOpener(self._release_body(
                html_url="javascript:alert(1)"))
            with mock.patch.object(uc, "_OPENER", opener):
                result = uc.check_latest_release()
        self.assertEqual(result["release_url"], "javascript:alert(1)",
                         "防护被拆时危险 URL 真的回传（= 钉必红）")

    def test_mutation_shape_guard_neutered_pin_goes_red(self):
        """破坏性变异：形状闸恒过 → assets 非 list-of-dict 直接炸（钉必红）。"""
        with mock.patch.object(uc, "_release_shape_ok", lambda release: True):
            opener = _RecordingOpener(b'{"tag_name": "v9.9.9", "assets": {"a": 1}}')
            with mock.patch.object(uc, "_OPENER", opener):
                with self.assertRaises((AttributeError, TypeError)):
                    uc.check_latest_release()


# ---------------------------------------------------------------------------
# F-3：pdf_fetch base_url 钉扎（DNS 重绑定缝）
# ---------------------------------------------------------------------------

class PdfBaseUrlPinningPins(unittest.TestCase):
    """F-3：base 端点（Unpaywall/Crossref/W9）连接必须钉扎 guard 同次解析的
    地址（修复前 _get_json 未传 pinned_address → 连接重新解析，重绑定窗口）。"""

    def setUp(self):
        self.resolve_calls: list[str] = []
        patcher = mock.patch.object(
            pdf_fetch_api, "_resolve_host", self._fake_resolve)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _fake_resolve(self, host, port=443):
        self.resolve_calls.append(host)
        if host == "rebind.example.test":
            # 前 2 次（guard 判定 + W9 路径的钉扎取址）答公网；此后若还有
            # 解析（= 连接阶段重新解析，漏洞形态）就答 127.0.0.1。
            return ([PUBLIC_IP] if len(self.resolve_calls) <= 2 else ["127.0.0.1"])
        raise OSError("dns stub: unknown host " + host)

    def test_unpaywall_query_pins_guard_resolved_address(self):
        opener = _RecordingOpener(json.dumps({"is_oa": False}).encode())
        settings = {"unpaywall_base_url": "http://rebind.example.test",
                    "email": ""}
        with mock.patch.object(pdf_fetch_api, "_OPENER", opener):
            pdf_fetch_api._unpaywall_pdf_candidates("10.9999/x", settings)
        self.assertEqual(self.resolve_calls, ["rebind.example.test"],
                         "guard 与连接共用同一次解析（不得二次解析）")
        self.assertEqual(len(opener.calls), 1, opener.calls)
        self.assertEqual(opener.calls[0]["pinned"], PUBLIC_IP,
                         "出站请求必须钉扎 guard 解析到的地址（防重绑定）")
        self.assertTrue(opener.calls[0]["allow_private"],
                        "base 端点钉扎走 guard 口径（回环/RFC1918 放行）")

    def test_crossref_query_pins_guard_resolved_address(self):
        opener = _RecordingOpener(json.dumps({"message": {}}).encode())
        settings = {"crossref_base_url": "http://rebind.example.test"}
        with mock.patch.object(pdf_fetch_api, "_OPENER", opener):
            pdf_fetch_api._crossref_message({"doi": "10.9999/x"}, settings)
        self.assertEqual(opener.calls[0]["pinned"], PUBLIC_IP, opener.calls)

    def test_w9_fetch_crossref_work_pins_guard_resolved_address(self):
        """W9 缝（crossref_works_url + fetch_crossref_work）：两道 guard 都在
        guard 层完成（共 2 次解析，都是判定/取址），连接不再解析。"""
        opener = _RecordingOpener(json.dumps({"message": {}}).encode())
        with mock.patch.object(pdf_fetch_api, "_OPENER", opener):
            pdf_fetch_api.fetch_crossref_work(
                "http://rebind.example.test", "10.9999/x")
        self.assertEqual(len(self.resolve_calls), 2, self.resolve_calls)
        self.assertEqual(opener.calls[0]["pinned"], PUBLIC_IP, opener.calls)

    def test_loopback_self_hosted_still_reachable_over_real_socket(self):
        """127.0.0.1 真实探针：字面量 base 钉扎后仍可达（回环自托管正钉
        不得红；Host 头保持原主机名）。"""
        seen = {}

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                seen["host"] = self.headers.get("Host")
                seen["path"] = self.path
                body = json.dumps({"message": {"DOI": "10.9999/x"}}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            base = f"http://127.0.0.1:{srv.server_address[1]}"
            message = pdf_fetch_api.fetch_crossref_work(base, "10.9999/x")
            self.assertEqual(message, {"DOI": "10.9999/x"})
            self.assertEqual(seen["path"], "/works/10.9999%2Fx", seen)
            self.assertEqual(seen["host"], f"127.0.0.1:{srv.server_address[1]}",
                             "钉扎连接的 Host 头必须仍是原主机名")
        finally:
            srv.shutdown()
            srv.server_close()

    def test_mutation_pin_dropped_pin_goes_red(self):
        """破坏性变异：_get_json 丢弃 pinned_addresses（旧口径）→ 钉必红。"""
        real = pdf_fetch_api._get_json

        def _unpinned(url, *, timeout=pdf_fetch_api.API_TIMEOUT_SECONDS,
                      pinned_addresses=None):
            return real(url, timeout=timeout)

        opener = _RecordingOpener(json.dumps({"is_oa": False}).encode())
        settings = {"unpaywall_base_url": "http://rebind.example.test",
                    "email": ""}
        with mock.patch.object(pdf_fetch_api, "_OPENER", opener), \
                mock.patch.object(pdf_fetch_api, "_get_json", _unpinned):
            pdf_fetch_api._unpaywall_pdf_candidates("10.9999/x", settings)
        self.assertIsNone(opener.calls[0]["pinned"],
                          "防护被拆时连接未钉扎（= 钉必红）")


# ---------------------------------------------------------------------------
# F-4（函数层半）：apprise [IPv6] 字面量判定单元钉
#（端到端发送钉在 test_2_1_5_notify_sec_pins.AppriseBracketedIPv6Pins）
# ---------------------------------------------------------------------------

class AppriseBracketedIPv6UnitPins(unittest.TestCase):
    def test_bracketed_private_literals_blocked(self):
        for host in ("::1", "::ffff:127.0.0.1", "::ffff:192.168.1.5",
                     "::ffff:169.254.169.254", "fd00::2", "fc00::1",
                     "fe80::1", "fe80::1%25eth0", "notanip", ""):
            with self.subTest(host=host):
                self.assertTrue(notify_core._bracketed_ipv6_host_private(host),
                                host)

    def test_bracketed_public_literals_allowed(self):
        for host in ("::ffff:" + PUBLIC_IP, "2606:4700::6810:85e5"):
            with self.subTest(host=host):
                self.assertFalse(notify_core._bracketed_ipv6_host_private(host))


# ---------------------------------------------------------------------------
# F-5：ai_keys base_url 私网段 + 模式化回环 + DNS 复核
# ---------------------------------------------------------------------------

class AiKeysBaseUrlGovernancePins(unittest.TestCase):
    """RFC1918/CGNAT 两种模式都拦；回环 desktop 放行（本地 Ollama）、server
    拦截；域名一次 DNS 复核（保存与出站两处，先例
    pdf_fetch_api._resolved_blocked_reason：解析失败不拦）。"""

    def test_rfc1918_and_cgnat_literals_blocked_in_both_modes(self):
        bad = ("http://10.0.0.5/v1", "http://192.168.1.10/v1",
               "http://172.16.0.9/v1", "http://100.100.100.200/v1",
               "http://100.64.0.1/v1", "http://[::ffff:10.0.0.5]/v1",
               "http://167772160/v1",              # 十进制 IPv4 = 10.0.0.0
               "http://0.0.0.0:8080/v1")           # 未指定地址（连到本机）
        for mode in (_desktop(), _server_mode()):
            with mode:
                for base in bad:
                    with self.subTest(mode=os.environ["REVIEWFLOW_MODE"], base=base):
                        with self.assertRaises(ai_keys.KeyShapeError, msg=base):
                            ai_keys.base_url_of({"base_url": base})

    def test_loopback_mode_split(self):
        """回环例外按运行模式：desktop 放行本地 Ollama；server 拦截。"""
        loopbacks = ("http://127.0.0.1:11434/v1", "http://127.0.0.2/v1",
                     "http://[::1]:11434/v1", "http://2130706433:11434/v1")
        with _desktop():
            for base in loopbacks:
                self.assertTrue(ai_keys.base_url_of({"base_url": base}), base)
        with _server_mode():
            for base in loopbacks:
                with self.assertRaises(ai_keys.KeyShapeError, msg=base):
                    ai_keys.base_url_of({"base_url": base})
        # server 模式的专用文案（指引回 desktop 语义）
        with _server_mode(), self.assertRaises(ai_keys.KeyShapeError) as ctx:
            ai_keys.base_url_of({"base_url": "http://127.0.0.1:11434/v1"})
        self.assertIn("回环", str(ctx.exception))

    def test_dns_recheck_rejects_domain_resolving_to_private(self):
        """域名解析回私网/元数据/链路本地 → 保存与出站两处都拒。"""
        blocked = {"rebind.example.test": ["192.168.0.1"],
                   "meta.example.test": ["169.254.169.254"],
                   "cgnat.example.test": ["100.100.100.200"],
                   "ula.example.test": ["fd00:ec2::254"],
                   "localhost-ish.example.test": ["127.0.0.1"]}
        for host, addresses in blocked.items():
            stub = mock.patch.object(ai_keys, "_resolve_host",
                                     lambda h, port=443, a=addresses: list(a))
            with stub, _desktop():
                # desktop 也拦（RFC1918/元数据/链路本地不随模式豁免）
                if host == "localhost-ish.example.test":
                    continue      # 回环域名走模式分流，见下一用例
                with self.assertRaises(ai_keys.KeyShapeError, msg=host):
                    ai_keys.base_url_of({"base_url": f"http://{host}/v1"})

    def test_dns_recheck_loopback_domain_mode_split(self):
        for mode, blocked in ((_desktop(), False), (_server_mode(), True)):
            with mode, mock.patch.object(ai_keys, "_resolve_host",
                                         lambda h, port=443: ["127.0.0.1"]):
                if blocked:
                    with self.assertRaises(ai_keys.KeyShapeError):
                        ai_keys.base_url_of({"base_url": "http://my-ollama.example/v1"})
                else:
                    self.assertTrue(ai_keys.base_url_of(
                        {"base_url": "http://my-ollama.example/v1"}))

    def test_dns_recheck_public_resolution_and_failure_pass(self):
        """公网解析放行；保存/元数据口径解析失败不拦（隔离网络自托管域名；
        sec29b W4② 起**出站侧**改失败关闭——见 test_2_1_5_sec29b_pinning）。"""
        with mock.patch.object(ai_keys, "_resolve_host",
                               lambda h, port=443: [PUBLIC_IP, "2001:db8::1"]):
            self.assertEqual(ai_keys.base_url_of(
                {"base_url": "http://probe.example.test/v1"}),
                "http://probe.example.test/v1")

            def _unresolvable(host, port=443):
                raise OSError("no dns")

            with mock.patch.object(ai_keys, "_resolve_host", _unresolvable):
                self.assertTrue(ai_keys.base_url_of(
                    {"base_url": "http://isolated.example.test/v1"}))

    def test_save_side_applies_same_dns_recheck(self):
        """保存侧（save_personal_key）同样复核（出站侧=base_url_of 已证）。"""
        with mock.patch.object(ai_keys, "_resolve_host",
                               lambda h, port=443: ["10.0.0.9"]), \
                tempfile.TemporaryDirectory() as root:
            with self.assertRaises(ai_keys.KeyShapeError):
                ai_keys.save_personal_key(
                    1, provider="openai", key="sk-test-abcdef123456",
                    base_url="http://rebind.example.test/v1",
                    data_root=Path(root))

    def test_read_metadata_path_skips_dns(self):
        """只读元数据（dns_recheck=False）绝不联网（r7 复审#1 口径不回退）。"""
        calls = []

        def _resolve(host, port=443):
            calls.append(host)
            return [PUBLIC_IP]

        with mock.patch.object(ai_keys, "_resolve_host", _resolve):
            self.assertEqual(
                ai_keys.base_url_of({"base_url": "http://meta.example.test/v1"},
                                    dns_recheck=False),
                "http://meta.example.test/v1")
        self.assertEqual(calls, [], "读取路径不得发起 DNS 解析")

    def test_mutation_address_check_neutered_pin_goes_red(self):
        """破坏性变异：_address_block_reason 恒放行 → RFC1918/CGNAT 钉必红。"""
        with mock.patch.object(ai_keys, "_address_block_reason",
                               lambda value: None):
            # 防护被拆时 RFC1918 字面量真的入库/放行（= 正钉必红的前提）
            self.assertTrue(ai_keys.base_url_of({"base_url": "http://10.0.0.5/v1"}))


# ---------------------------------------------------------------------------
# F-6：AI 出站并发闸（信号量 8 → 503 ai_provider_busy）
# ---------------------------------------------------------------------------

class ProviderConcurrencyPins(unittest.TestCase):
    BODY = json.dumps({"choices": [{"message": {"content": "{}"}}],
                       "usage": {}, "model": "m"}).encode()

    def setUp(self):
        # sec29b W4①：_provider_generate 自带出站闸（guard 单次解析 + 钉扎表），
        # 真跑时把 DNS 缝钉到公网桩（否则 probe.example.test 真解析 → 失败关闭
        # 503，与并发闸语义无关地红）。
        patcher = mock.patch.object(ai_keys, "_resolve_host",
                                    lambda host, port=443: [PUBLIC_IP])
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_over_limit_fails_fast_with_busy_503(self):
        """占满 8 个闸位后第 9 次出站快速失败（不排队、不重试）。"""
        held = []
        try:
            for _ in range(ai_suggest_api._PROVIDER_MAX_CONCURRENCY):
                self.assertTrue(ai_suggest_api._OUTBOUND_SEMAPHORE.acquire(False))
                held.append(True)
            opener = _RecordingOpener(self.BODY)
            with mock.patch.object(ai_suggest_api, "_OPENER", opener):
                with self.assertRaises(ai_suggest_api._ProviderBusyError) as ctx:
                    ai_suggest_api._provider_generate(
                        "s", "u", api_key="sk-test-abcdef123456",
                        base_url="http://probe.example.test/v1", model="m")
            self.assertEqual(ctx.exception.status, 503)
            self.assertEqual(opener.calls, [], "超限请求绝不出站")
        finally:
            for _ in held:
                ai_suggest_api._OUTBOUND_SEMAPHORE.release()

    def test_busy_maps_to_structured_503(self):
        error = ai_suggest_api._provider_http_error(
            ai_suggest_api._ProviderBusyError(), "sk-test-abcdef123456")
        self.assertEqual(error.status_code, 503)
        self.assertEqual(error.detail["code"], "ai_provider_busy")
        self.assertIn("稍后重试", error.detail["detail"])

    def test_busy_is_not_retried_by_call_provider(self):
        """_call_provider 对 busy 不重试（重试只会把闸位占得更久）。"""
        calls = {"n": 0}

        def _busy(*args, **kwargs):
            calls["n"] += 1
            raise ai_suggest_api._ProviderBusyError()

        with mock.patch.object(ai_suggest_api, "_provider_generate", _busy):
            with self.assertRaises(ai_suggest_api._ProviderBusyError):
                ai_suggest_api._call_provider(
                    "s", "u", api_key="k-test-abcdef123456",
                    base_url="http://probe.example.test/v1", model="m")
        self.assertEqual(calls["n"], 1)

    def test_within_limit_works_and_slot_returned_on_error(self):
        """未超限正常出站；异常后闸位归还（finally 语义，不泄漏）。"""
        opener = _RecordingOpener(self.BODY)
        with mock.patch.object(ai_suggest_api, "_OPENER", opener):
            result = ai_suggest_api._provider_generate(
                "s", "u", api_key="sk-test-abcdef123456",
                base_url="http://probe.example.test/v1", model="m")
        self.assertEqual(result["model"], "m")
        boom = _RecordingOpener(b"")
        boom.open = lambda request, timeout=None: (_ for _ in ()).throw(
            urllib.error.URLError("broken"))
        with mock.patch.object(ai_suggest_api, "_OPENER", boom):
            with self.assertRaises(ai_suggest_api.ProviderError):
                ai_suggest_api._provider_generate(
                    "s", "u", api_key="sk-test-abcdef123456",
                    base_url="http://probe.example.test/v1", model="m")
        # 闸位已归还：仍可再发起（连续 8 次全成功）。
        for _ in range(ai_suggest_api._PROVIDER_MAX_CONCURRENCY):
            with mock.patch.object(ai_suggest_api, "_OPENER", _RecordingOpener(self.BODY)):
                ai_suggest_api._provider_generate(
                    "s", "u", api_key="sk-test-abcdef123456",
                    base_url="http://probe.example.test/v1", model="m")

    def test_mutation_semaphore_removed_pin_goes_red(self):
        """破坏性变异：并发闸拆除（换超大信号量）→ 第 9 次出站真的发生。"""
        with mock.patch.object(ai_suggest_api, "_OUTBOUND_SEMAPHORE",
                               threading.Semaphore(10 ** 9)):
            held = []
            try:
                for _ in range(ai_suggest_api._PROVIDER_MAX_CONCURRENCY):
                    ai_suggest_api._OUTBOUND_SEMAPHORE.acquire(False)
                    held.append(True)
                opener = _RecordingOpener(self.BODY)
                with mock.patch.object(ai_suggest_api, "_OPENER", opener):
                    result = ai_suggest_api._provider_generate(
                        "s", "u", api_key="sk-test-abcdef123456",
                        base_url="http://probe.example.test/v1", model="m")
                self.assertEqual(result["model"], "m",
                                 "闸被拆时超限出站真的发生（= 超限钉必红）")
            finally:
                for _ in held:
                    ai_suggest_api._OUTBOUND_SEMAPHORE.release()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
