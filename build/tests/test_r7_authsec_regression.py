"""r7 修复泳道「守卫生效（认证/体积上限）」钉子测试。

覆盖（把对应修复删掉，断言必须变红）：
- MM-01：MultipartStructureRejected 可实例化且 413 契约成立（双 ``__init__``
  曾使类不可用 → 防线 400/500 而非 413）；
- MM-03：空 ``.jwt_secret`` 自愈重生成 + ``jwt.InvalidKeyError`` 兜住降级
  401（曾登录与全部鉴权永久 500）；
- MM2-02：/api/auth/login|register 请求体 4KB 上限（Content-Length 预检 +
  chunked 读后精确校验）＋ 422 不回显超大 ``input``（曾 20MB 整包读入 +
  422 原样回显放大）；
- MM2-03 / MM4-04：coding/trace 的 ``_body_within_limit`` 缺 Content-Length
  （chunked）时同样 413（曾缺头即完全不设卡）；multipart 上传不经路由守卫
  整包读入（保持流式 spool，由 UploadSizeLimitMiddleware 预检）；
- MM-05：标题重复复核 CSV 导出补 ``'`` 前缀公式注入防护（同仓其余 5 处）。
"""
import asyncio
import csv as csv_mod
import io
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
fixture = tempfile.TemporaryDirectory(prefix="rf_r7_authsec_")
os.environ["COBOOKSHELF_DATA_DIR"] = fixture.name
os.environ["REVIEWFLOW_MODE"] = "server"

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.testclient import TestClient
from starlette.requests import Request

from custom_backend import auth as auth_mod
from custom_backend import coding_consensus_api
from custom_backend import main as backend_main  # noqa: F401（DATA_DIR 环境初始化）
from custom_backend import research_trace_api
from custom_backend.main import app
from custom_backend.security import (
    MultipartStructureRejected,
    UploadSizeLimitMiddleware,
)

_FORMULA_CHARS = ("=", "+", "-", "@", "\t", "\r")


class Mm01MultipartRejectedContractTests(unittest.TestCase):
    """MM-01：双 ``__init__`` 修复后类可实例化、413 契约恢复。"""

    def test_mm01_instantiable_with_413_attributes(self):
        exc = MultipartStructureRejected("multipart 结构超限钉子消息")
        # 兼作 HTTPException(413)：真实端点据此按 413 呈现（曾 400/500）
        self.assertEqual(exc.status_code, 413)
        self.assertEqual(exc.detail, "multipart 结构超限钉子消息")
        # UploadRejected.status 供 app 级 handler 映射（main._upload_rejected_handler）
        self.assertEqual(exc.status, 413)
        self.assertIn("multipart 结构超限钉子消息", str(exc))
        from custom_backend.security import UploadRejected
        self.assertIsInstance(exc, UploadRejected)
        self.assertIsInstance(exc, HTTPException)

    def test_mm01_real_endpoint_returns_413_not_parse_error(self):
        """隔离最小应用：真实中间件 + File 端点，超文件 part 数必须 413。"""
        probe = FastAPI()
        probe.add_middleware(
            UploadSizeLimitMiddleware,
            rules=[(re.compile(r"^/probe$"), 10 ** 8, 1)])
        probe.state.anything = None

        @probe.post("/probe")
        async def probe_ep(file: UploadFile = File(...)):  # noqa: ANN001
            return {"ok": True}

        with TestClient(probe, raise_server_exceptions=False) as client:
            r = client.post("/probe", files=[
                ("file", ("a.csv", b"zotero_key\nK1\n", "text/csv")),
                ("file", ("b.csv", b"zotero_key\nK2\n", "text/csv")),
            ])
        self.assertEqual(r.status_code, 413, r.text)
        self.assertIn("上限", r.text)


class Mm03EmptyJwtSecretTests(unittest.TestCase):
    """MM-03：空 ``.jwt_secret`` 必须自愈，空钥解码降级 None（401）不 500。"""

    def tearDown(self):
        # 复位进程内应急密钥状态，避免影响后续用例的日志/告警口径
        auth_mod._EMERGENCY_SECRET = None
        auth_mod._EMERGENCY_WRITE_LOGGED = False

    def test_mm03_empty_secret_file_self_heals(self):
        secret_path = auth_mod.data_dir() / ".jwt_secret"
        secret_path.write_text("", encoding="utf-8")  # MM-03 触发态：空文件
        token = auth_mod.create_access_token(4242, "mm03-probe")
        healed = secret_path.read_text(encoding="utf-8").strip()
        self.assertTrue(healed, "空密钥文件必须原子重生成写回磁盘")
        payload = auth_mod.decode_access_token(token)
        self.assertIsNotNone(payload)
        self.assertEqual(payload["sub"], "4242")
        # 自愈后密钥稳定：后续读取不再每次换钥（曾竞态分支返回内存钥）
        self.assertEqual(auth_mod._secret(), healed)

    def test_mm03_decode_with_empty_key_returns_none(self):
        """docstring「绝不抛异常」：空 HMAC 钥必须落 None（→401），不冒泡 500。"""
        import base64

        def _b64(segment: bytes) -> str:
            return base64.urlsafe_b64encode(segment).rstrip(b"=").decode()

        # 形状合法（头/载荷可解析）的令牌：PyJWT 走到签名校验才碰钥——
        # 空钥在此抛 InvalidKeyError（非 InvalidTokenError 子类，曾未被兜住）
        token = (_b64(b'{"alg":"HS256","typ":"JWT"}') + "."
                 + _b64(b'{"sub":"1"}') + ".c2ln")
        with mock.patch.object(auth_mod, "_secret", return_value=""):
            self.assertIsNone(auth_mod.decode_access_token(token))

    def test_mm03_unwritable_store_keeps_process_stable_secret(self):
        """r7 复审：写回失败（只读/权限异常）时同一进程内必须稳定同钥——
        旧行为每次调用重生成内存钥：create 成功而 decode 恒 None（静默 401
        自锁），且写失败无任何日志。"""
        secret_path = auth_mod.data_dir() / ".jwt_secret"
        secret_path.write_text("", encoding="utf-8")
        with mock.patch.object(auth_mod, "_atomic_write_secret",
                               return_value=False):
            with self.assertLogs("custom_backend.auth", level="ERROR") as logs:
                first = auth_mod._secret()
            # 同一进程内后续调用返回**同一**应急密钥（签发/校验自洽）
            second = auth_mod._secret()
            token = auth_mod.create_access_token(7, "mm03b-probe")
            self.assertEqual(first, second)
            self.assertIsNotNone(auth_mod.decode_access_token(token))
        self.assertTrue(any("persist" in line for line in logs.output),
                        f"写回失败必须留 ERROR 诊断日志：{logs.output}")
        # 目录恢复可写后下一次调用即写回自愈（应急钥退役）
        healed = auth_mod._secret()
        self.assertTrue(secret_path.read_text(encoding="utf-8").strip())
        self.assertEqual(auth_mod._secret(), healed)


class Mm202AuthBodyLimitTests(unittest.TestCase):
    """MM2-02：auth 端点 4KB 请求体上限 ＋ 422 不回显超大 input。"""

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.client.__enter__()
        # 不走 HTTP /api/auth/register：避免与其他模块共享限流桶
        user = auth_mod.create_user("mm202@example.test", "Mm202-Pass-9")
        cls.user = user

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    def test_mm202_oversized_json_login_413(self):
        body = {"username": "a" * 60000, "password": "x" * 8}
        r = self.client.post("/api/auth/login", json=body)
        self.assertEqual(r.status_code, 413, r.text)
        self.assertLess(len(r.content), 8192, "413 早拒响应不得携带请求体内容")

    def test_mm202_oversized_json_register_413(self):
        body = {"username": "a" * 60000, "password": "x" * 8}
        r = self.client.post("/api/auth/register", json=body)
        self.assertEqual(r.status_code, 413, r.text)
        self.assertLess(len(r.content), 8192)

    def test_mm202_chunked_oversized_login_413(self):
        """缺 Content-Length（chunked）同样 413：上限不得只对带头请求生效。"""
        blob = ('{"username":"' + "a" * 60000 + '","password":"x"}').encode()

        def chunks():
            for i in range(0, len(blob), 8192):
                yield blob[i:i + 8192]

        r = self.client.post("/api/auth/login", content=chunks(),
                             headers={"Content-Type": "application/json"})
        self.assertEqual(r.status_code, 413, r.text)
        self.assertLess(len(r.content), 8192)

    def test_mm202_small_urlencoded_login_unchanged(self):
        """非超限请求行为不变（r6s3 既有断言同口径：21 字节表单体仍 422 族）。
        chunked 变体同时钉 replay 通道对非 JSON 体同样透明（曾 400）。"""
        r = self.client.post(
            "/api/auth/login", content="username=a&password=b",
            headers={"Content-Type": "application/x-www-form-urlencoded"})
        self.assertIn(r.status_code, (401, 422), r.text)

        def small_chunks():
            yield b"username=a&password=b"

        r2 = self.client.post(
            "/api/auth/login", content=small_chunks(),
            headers={"Content-Type": "application/x-www-form-urlencoded"})
        self.assertIn(r2.status_code, (401, 422), r2.text)

    def test_mm202_small_chunked_valid_login_succeeds(self):
        """r7 复审（R2）：≤上限的 chunked 合法登录必须成功——缺头分支的
        replay 通道曾把 async 生成器当 ASGI receive 传入，下游
        await receive() 抛 TypeError → FastAPI 兜成 400 parse error，
        拒绝了基线可用的合法输入。"""
        blob = json.dumps({"username": "mm202@example.test",
                           "password": "Mm202-Pass-9"}).encode()

        def chunks():
            yield blob[:30]
            yield blob[30:]

        r = self.client.post("/api/auth/login", content=chunks(),
                             headers={"Content-Type": "application/json"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("token", r.json())

    def test_mm202_422_does_not_echo_oversized_input(self):
        # ≤4KB 的畸形字段（username 超长）→ 422：超大 input 不得原样回显
        r = self.client.post("/api/auth/login",
                             json={"username": "a" * 2000, "password": "x" * 8})
        self.assertEqual(r.status_code, 422, r.text)
        self.assertNotIn("a" * 100, r.text, "422 不得回显超大 input（放大反射）")
        self.assertLess(len(r.content), 8192)
        # 小输入的回显保持不变（正常校验 UX 不受影响）
        r2 = self.client.post("/api/auth/login",
                              json={"username": "ab", "password": "x" * 8})
        self.assertEqual(r2.status_code, 422, r2.text)
        self.assertIn('"ab"', r2.text)


class Mm203ChunkedBodyGuardTests(unittest.TestCase):
    """MM2-03/MM4-04：JSON body 守卫缺 Content-Length（chunked）同样 413。"""

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    def test_mm203_chunked_over_limit_consensus_413(self):
        self.assertGreater(coding_consensus_api.MAX_CONSENSUS_BODY_BYTES,
                           1024 * 1024)
        blob = ('{"item_key":"' + "k" * (3 * 1024 * 1024) + '"}').encode()

        def chunks():
            for i in range(0, len(blob), 65536):
                yield blob[i:i + 65536]

        r = self.client.post("/api/tasks/mm203-probe/coding/records",
                             content=chunks(),
                             headers={"Content-Type": "application/json"})
        self.assertEqual(r.status_code, 413, r.text)
        self.assertIn("coding consensus limit", r.text)

    def test_mm203_content_length_over_limit_consensus_still_413(self):
        """对照：带 Content-Length 的超限请求既有 413 行为保持。"""
        r = self.client.post(
            "/api/tasks/mm203-probe/coding/records",
            json={"item_key": "k" * (3 * 1024 * 1024)})
        self.assertEqual(r.status_code, 413, r.text)

    def test_mm203_chunked_over_limit_trace_413(self):
        """trace 守卫同口径：缺头超限（chunked）同样 413（限值缩样，机制真实）。"""
        blob = ('{"q":"' + "t" * 65536 + '"}').encode()

        def chunks():
            for i in range(0, len(blob), 8192):
                yield blob[i:i + 8192]

        with mock.patch.object(research_trace_api, "MAX_TRACE_BODY_BYTES", 1024):
            r = self.client.put("/api/tasks/mm203-probe/trace/settings",
                                content=chunks(),
                                headers={"Content-Type": "application/json"})
        self.assertEqual(r.status_code, 413, r.text)

    def test_mm203_trace_multipart_not_whole_read_by_route_guard(self):
        """multipart 上传不经路由守卫整包读入（保持流式 spool；体积预检由
        UploadSizeLimitMiddleware 负责）——守卫对 multipart 不得按体判定 413。"""

        async def receive():
            return {"type": "http.request", "body": b"x" * 4096,
                    "more_body": False}

        scope = {"type": "http", "method": "POST", "path": "/t",
                 "headers": [(b"content-type",
                              b"multipart/form-data; boundary=B"),
                             (b"transfer-encoding", b"chunked")]}
        request = Request(scope, receive)
        with mock.patch.object(research_trace_api, "MAX_TRACE_BODY_BYTES", 1024):
            try:
                asyncio.run(research_trace_api._body_within_limit(request))
            except HTTPException as exc:
                self.fail(f"multipart 不应被路由守卫按体判定拒绝：{exc.detail}")


class Mm05TitleDuplicateExportFormulaTests(unittest.TestCase):
    """MM-05：标题重复复核 CSV 导出的公式注入防护（' 前缀）。"""

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.client.__enter__()
        user = auth_mod.create_user("mm05@example.test", "Mm05-Pass-9")
        cls.client.headers["Authorization"] = (
            "Bearer " + auth_mod.create_access_token(user["id"], user["username"]))
        cls.task = cls.client.post(
            "/api/tasks", json={"name": "mm05-formula-probe"}).json()["task_id"]
        csv_text = (
            "Key,Title,Author,DOI\n"
            'K1,Formula Injection Probe,=cmd| /c calc"!A0",10.test/f1\n'
            'K2,Formula Injection Probe,=cmd| /c calc"!A0",10.test/f2\n'
        )
        r = cls.client.post(
            f"/api/tasks/{cls.task}/import",
            files={"file": ("mm05.csv", csv_text.encode("utf-8"), "text/csv")})
        assert r.status_code == 200, f"导入失败：{r.text}"

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    def test_mm05_export_cells_formula_prefixed(self):
        r = self.client.get(
            f"/api/tasks/{self.task}/title-duplicate-reviews/export")
        self.assertEqual(r.status_code, 200, r.text)
        rows = list(csv_mod.reader(
            io.StringIO(r.content.decode("utf-8-sig"))))
        self.assertGreater(len(rows), 1, "标题重复组未生成，导出无数据行")
        data_rows = rows[1:]
        # 载荷确实进入了导出（authors/doi 至少一处携带公式前缀）
        joined = "\n".join(",".join(row) for row in data_rows)
        self.assertIn("'=", joined, f"公式载荷未按 ' 前缀防护写出：{joined!r}")
        # 防护后不得再有任何裸公式单元格
        for row in data_rows:
            for cell in row:
                self.assertFalse(
                    cell.startswith(_FORMULA_CHARS),
                    f"存在未防护的公式单元格：{cell!r}")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
