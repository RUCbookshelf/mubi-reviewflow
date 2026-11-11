# -*- coding: utf-8 -*-
"""sec29 回归钉（deepseek 异构安全复审 2026-11-11·上传闸发现 1/2）。

发现1（高）·UploadSizeLimitMiddleware：体积预检只读 Content-Length——
CL+TE 双带（RFC 7230 §3.3.3 明令拒绝的请求走私载体）在「前端按 CL 预检、
后端按 TE 读体」的分歧下可让实际读入远超预检上限。修复=双带一律 400；
纯 TE 不受新检查影响，仍走 411（multipart 缺有效 CL）路径。

发现2（高）·AuthBodyLimitMiddleware：CL≤limit 快路径不读体直接放行，
同样被 CL+TE 双带绕过。修复=双带 400；纯 TE 不信任 CL、落入有界读取
分支（r7 修过的「chunked 合法登录必须可用」语义原样保留）。

纵深·_MultipartPartCounter：CL 只校验声明值，「实际流过字节 > 声明」的
违规流此前照单全收；现按端点上限+64KB 预算在 feed() 累计拒绝（与文件
数/字段超限同一 413 通道）。三组钉：纯计数器单元、谎报 CL 的中途拒绝、
诚实小体（含逐字节滴灌）不受影响。
"""
import asyncio
import json
import re
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from custom_backend import security  # noqa: E402

_CT = (b"content-type", b"multipart/form-data; boundary=----bnd")
_RULES = [(re.compile(r"^/up$"), 200, 2)]
_BODY = (b"----bnd\r\nContent-Disposition: form-data; "
         b'name="f"; filename="a.txt"\r\n\r\nhi\r\n----bnd--\r\n')


class _Sink:
    """收 ASGI send 消息；附常用断言取值。"""

    def __init__(self):
        self.messages = []

    async def __call__(self, message):
        self.messages.append(message)

    @property
    def status(self):
        return self.messages[0]["status"]

    @property
    def detail(self):
        return json.loads(self.messages[-1]["body"].decode("utf-8"))["detail"]


class _App:
    """透传哨兵：预检放行时才会被调到；读完请求体记录之。"""

    def __init__(self):
        self.called = False
        self.body = b""

    async def __call__(self, scope, receive, send):
        self.called = True
        while True:
            message = await receive()
            if message["type"] != "http.request":
                continue
            self.body += message.get("body", b"")
            if not message.get("more_body", False):
                break
        await send({"type": "http.response.start", "status": 200,
                    "headers": []})
        await send({"type": "http.response.body", "body": b"{}"})


def _static_receive(chunks):
    it = iter(chunks)

    async def receive():
        try:
            body = next(it)
        except StopIteration:
            return {"type": "http.request", "body": b"", "more_body": False}
        return {"type": "http.request", "body": body, "more_body": True}

    return receive


def _scope(headers, path="/up"):
    return {"type": "http", "method": "POST", "path": path,
            "headers": list(headers), "query_string": b""}


class UploadClTeTests(unittest.TestCase):
    """发现1：上传预检对 CL+TE 双带请求 400，纯 TE 语义不变（411）。"""

    def test_cl_plus_te_rejected_400(self):
        app, sink = _App(), _Sink()
        mw = security.UploadSizeLimitMiddleware(app, _RULES)
        asyncio.run(mw(_scope([_CT, (b"content-length", b"100"),
                               (b"transfer-encoding", b"chunked")]),
                       _static_receive([b""]), sink))
        self.assertEqual(sink.status, 400)
        self.assertIn("Transfer-Encoding", sink.detail)
        self.assertFalse(app.called)

    def test_te_only_multipart_still_411(self):
        app, sink = _App(), _Sink()
        mw = security.UploadSizeLimitMiddleware(app, _RULES)
        asyncio.run(mw(_scope([_CT, (b"transfer-encoding", b"chunked")]),
                       _static_receive([b""]), sink))
        self.assertEqual(sink.status, 411)
        self.assertFalse(app.called)


class AuthClTeTests(unittest.TestCase):
    """发现2：auth 体积上限对双带 400；chunked 合法登录（r7 R2）不回退。"""

    def _mw(self, app):
        return security.AuthBodyLimitMiddleware(app, [r"^/api/auth/login$"])

    def test_cl_plus_te_rejected_400(self):
        app, sink = _App(), _Sink()
        asyncio.run(self._mw(app)(_scope(
            [(b"content-length", b"100"),
             (b"transfer-encoding", b"chunked")],
            path="/api/auth/login"), _static_receive([b""]), sink))
        self.assertEqual(sink.status, 400)
        self.assertIn("Transfer-Encoding", sink.detail)
        self.assertFalse(app.called)

    def test_te_only_small_chunked_login_still_works(self):
        app, sink = _App(), _Sink()
        chunks = [b'{"username": "a"', b'", "password": "b"}']
        asyncio.run(self._mw(app)(_scope(
            [(b"transfer-encoding", b"chunked")], path="/api/auth/login"),
            _static_receive(chunks), sink))
        self.assertTrue(app.called)
        self.assertEqual(app.body, b"".join(chunks))
        self.assertEqual(sink.status, 200)

    def test_te_only_oversized_chunked_still_413(self):
        app, sink = _App(), _Sink()
        asyncio.run(self._mw(app)(_scope(
            [(b"transfer-encoding", b"chunked")], path="/api/auth/login"),
            _static_receive([b"x" * 5000]), sink))
        self.assertEqual(sink.status, 413)
        self.assertFalse(app.called)

    def test_small_cl_fast_path_untouched(self):
        app, sink = _App(), _Sink()
        body = b'{"username": "a", "password": "b"}'
        asyncio.run(self._mw(app)(_scope(
            [(b"content-length", str(len(body)).encode())],
            path="/api/auth/login"),
            _static_receive([body]), sink))
        self.assertTrue(app.called)
        self.assertEqual(app.body, body)


class TotalBytesBudgetTests(unittest.TestCase):
    """纵深：计数器总字节预算——谎报 CL 的流中途 413，诚实体不误伤。"""

    def test_counter_unit_over_budget_raises_413_shape(self):
        counter = security._MultipartPartCounter(
            b"bnd", 2, max_total_bytes=100)
        counter.feed(b"x" * 60)
        with self.assertRaises(security.MultipartStructureRejected) as ctx:
            counter.feed(b"x" * 60)
        self.assertEqual(ctx.exception.status, 413)
        self.assertEqual(ctx.exception.status_code, 413)

    def test_honest_dribbled_body_under_budget_passes(self):
        # 逐字节滴灌：跨 chunk 缓冲重组下预算只按 feed 入口计一次。
        body = (b"--bnd\r\nContent-Disposition: form-data; "
                b'name="f"; filename="a.txt"\r\n\r\nhi\r\n--bnd--\r\n')
        counter = security._MultipartPartCounter(
            b"bnd", 2, max_total_bytes=len(body) + 10)
        for i in range(len(body)):
            counter.feed(body[i:i + 1])
        self.assertEqual(counter.files, 1)
        self.assertEqual(counter.fields, 0)

    def test_lying_content_length_rejected_midstream(self):
        # CL 声明 100（≤上限 200），实际流 3×50KB：预算=200+64KB，第二块
        # 起累计即超——MultipartStructureRejected 从 receive 层上抛（生产
        # 中由路由层按 HTTPException 映射 413），预检层自身不应答。
        app, sink = _App(), _Sink()
        mw = security.UploadSizeLimitMiddleware(app, _RULES)
        recv = _static_receive([b"a" * 50000, b"b" * 50000, b"c" * 50000])
        with self.assertRaises(security.MultipartStructureRejected):
            asyncio.run(mw(_scope([_CT, (b"content-length", b"100")]),
                           recv, sink))
        self.assertFalse(sink.messages)

    def test_honest_cl_body_within_limit_passes(self):
        app, sink = _App(), _Sink()
        mw = security.UploadSizeLimitMiddleware(app, _RULES)
        asyncio.run(mw(_scope([_CT, (b"content-length",
                                     str(len(_BODY)).encode())]),
                       _static_receive([_BODY]), sink))
        self.assertTrue(app.called)
        self.assertEqual(app.body, _BODY)


if __name__ == "__main__":
    unittest.main()
