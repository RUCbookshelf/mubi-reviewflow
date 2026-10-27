"""2.1.4 上传与路径安全（U5-P1..P12）回归测试。

覆盖：任务锁别名归一（P1）、同进程删除互斥（P2）、上传 Content-Length 预检
（P3）、中间件鉴权前置（P4）、限流 key 防伪造（P5）、Content-Disposition
百分号编码（P6）、IntegrityError 映射（P7）、文件名截断/保留名（P8）、
PDF 0 页拒绝（P9/P11）、SSE 短期票据（P10/P12）。
"""
import asyncio
import io
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
fixture = tempfile.TemporaryDirectory(prefix="rf_upload_sec_")
os.environ["COBOOKSHELF_DATA_DIR"] = fixture.name
os.environ["REVIEWFLOW_MODE"] = "server"

from fastapi.testclient import TestClient
from pypdf import PdfWriter

from custom_backend import main as backend_main
from custom_backend.main import TaskLockMiddleware, app, _file_response
from coscreen import tasks as tasks_mod
from custom_backend.security import (
    EventTicketStore,
    MultipartStructureRejected,
    UploadSizeLimitMiddleware,
    check_extension,
    clean_upload_name,
    rate_limit_key,
    TEXT_EXTENSIONS,
)
from coscreen import db as db_mod
from coscreen.models import Article


def _one_page_pdf() -> bytes:
    """pypdf 生成单页空白 PDF（U5-P9 后伪 PDF 会被拒，测试需真实可解析件）。"""
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


class UploadSecurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.client.__enter__()
        # 不走 HTTP /api/auth/register：全套 pytest 中 TestClient 的 client.host
        # 共享同一个登录/注册限流桶（5 次/分钟），直接建用户+签发令牌。
        from custom_backend import auth as auth_mod
        user = auth_mod.create_user("uploadsec@example.test", "Upload-Sec-2026")
        cls.user = {"user_id": user["id"], "username": user["username"],
                    "token": auth_mod.create_access_token(user["id"], user["username"])}

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        fixture.cleanup()

    def auth(self):
        return {"Authorization": "Bearer " + self.user["token"]}

    def setUp(self):
        TaskLockMiddleware._held.clear()
        self.task = self.client.post(
            "/api/tasks", json={"name": self._testMethodName},
            headers=self.auth()).json()["task_id"]
        self.info = tasks_mod.find_task(self.task, backend_main.DATA_DIR)
        self.assertIsNotNone(self.info)

    def tearDown(self):
        TaskLockMiddleware._held.clear()
        for path in list(tasks_mod._HELD_LOCKS):
            tasks_mod.release_task_lock(path, force=True)

    def _task_locks(self):
        return sorted(p.name for p in self.info.dir.glob("*.lock"))

    # ------------------------------------------------------------------
    # U5-P1：锁别名——URL 尾点等别名必须命中规范名锁，不得派生第二把锁
    # ------------------------------------------------------------------
    @unittest.skipUnless(sys.platform == "win32",
                         "尾点别名仅 Windows 文件系统解析为同一目录")
    def test_u5p1_alias_request_uses_canonical_lock(self):
        recorded = []
        original = tasks_mod.acquire_task_lock

        def spy(task_id, data_dir, task_dir=None):
            recorded.append((task_id, Path(task_dir).name if task_dir else None))
            return original(task_id, data_dir, task_dir=task_dir)

        tasks_mod.acquire_task_lock = spy
        try:
            r = self.client.get(f"/api/tasks/{self.task}./articles", headers=self.auth())
            self.assertEqual(r.status_code, 200, r.text)
        finally:
            tasks_mod.acquire_task_lock = original
        # 锁参数是规范 task_id + 规范任务目录（不是 "task." / "task.." 之类别名）
        self.assertEqual(recorded, [(self.info.task_id, self.info.task_id)])
        self.assertNotIn(f"{self.info.task_id}..lock", self._task_locks())
        # 请求结束后锁已释放
        self.assertEqual(self._task_locks(), [])

    # ------------------------------------------------------------------
    # U5-P2：同进程在途操作时 DELETE 必须 409（导入进行中删除任务）
    # ------------------------------------------------------------------
    def test_u5p2_delete_rejected_while_inflight_same_process(self):
        lock = tasks_mod.acquire_task_lock(self.info.task_id, backend_main.DATA_DIR)
        self.assertIsNotNone(lock)
        TaskLockMiddleware._held[self.info.task_id] = [2, lock]  # 模拟 2 个在途请求
        try:
            r = self.client.delete(f"/api/tasks/{self.task}", headers=self.auth())
            self.assertEqual(r.status_code, 409, r.text)
            self.assertIn("任务有操作进行中", r.json()["detail"])
            self.assertTrue(self.info.dir.is_dir(), "在途时任务目录不得被移入 .trash")
            # coscreen 层直调 delete_task 同样拒绝（钩子已由 main 注册）
            with self.assertRaises(ValueError) as ctx:
                tasks_mod.delete_task(self.task, backend_main.DATA_DIR)
            self.assertIn("任务有操作进行中", str(ctx.exception))
        finally:
            TaskLockMiddleware._held.clear()
            tasks_mod.release_task_lock(lock, force=True)
        # 在途清零后正常删除仍可用（不被误伤）
        r = self.client.delete(f"/api/tasks/{self.task}", headers=self.auth())
        self.assertEqual(r.status_code, 200, r.text)
        self.assertFalse(self.info.dir.exists())

    # ------------------------------------------------------------------
    # U5-P4：未认证请求不得取锁/回 409（占用状态预言机与锁文件操作面）
    # ------------------------------------------------------------------
    def test_u5p4_unauthenticated_never_touches_task_lock(self):
        # 无凭据：401（放行给端点），且不产生任何锁文件
        r = self.client.get(f"/api/tasks/{self.task}/articles")
        self.assertEqual(r.status_code, 401)
        self.assertEqual(self._task_locks(), [])
        # 伪格式 Bearer：同样 401、不取锁
        r = self.client.get(f"/api/tasks/{self.task}/articles",
                            headers={"Authorization": "Bearer not-a-jwt"})
        self.assertEqual(r.status_code, 401)
        self.assertEqual(self._task_locks(), [])
        # 锁被"活进程"占用（本进程 PID、未走 _HELD_LOCKS 幂等路径）时，
        # 未认证请求仍必须是 401 而非 409——预言机关闭。
        lock_path = self.info.dir / f"{self.info.task_id}.lock"
        lock_path.write_text(f"{os.getpid()} 2026-01-01T00:00:00", encoding="utf-8")
        try:
            r = self.client.get(f"/api/tasks/{self.task}/articles")
            self.assertEqual(r.status_code, 401)
            r = self.client.get(f"/api/tasks/{self.task}/articles",
                                headers={"Authorization": "Bearer not-a-jwt"})
            self.assertEqual(r.status_code, 401)
            # 已认证请求此时确实会被 409（中间件互斥仍生效）
            r = self.client.get(f"/api/tasks/{self.task}/articles", headers=self.auth())
            self.assertEqual(r.status_code, 409, r.text)
            self.assertIn("任务正被其他窗口", r.json()["detail"])
        finally:
            lock_path.unlink(missing_ok=True)

    # ------------------------------------------------------------------
    # U5-P3：multipart 上传在解析请求体之前预检（400 重复头/411 无头/
    #        401 未认证/413 超限/413 文件 part 超数）
    # ------------------------------------------------------------------
    def test_u5p3_content_length_precheck_rejects_before_body_read(self):
        rules = [(re.compile(r"^/api/tasks/[^/]+/import$"), 100, 1)]
        seen = []

        async def stub_app(scope, receive, send):
            seen.append(scope["path"])
            await send({"type": "http.response.start", "status": 200,
                        "headers": [(b"content-type", b"text/plain")]})
            await send({"type": "http.response.body", "body": b"ok"})

        def run(headers, method="POST", path="/api/tasks/t1/import",
                token_validator=None):
            replies = []
            middleware = UploadSizeLimitMiddleware(stub_app, rules,
                                                   token_validator=token_validator)

            async def receive():  # pragma: no cover - 预检命中时不应被读
                raise AssertionError("request body must not be read")

            async def send(message):
                replies.append(message)

            asyncio.run(middleware(
                {"type": "http", "method": method, "path": path, "headers": headers},
                receive, send))
            return replies

        multipart = [(b"content-type", b"multipart/form-data; boundary=x")]
        # 超限：立即 413，不进入内层 app、不读请求体
        replies = run(multipart + [(b"content-length", b"101")])
        self.assertEqual(replies[0]["status"], 413)
        self.assertEqual(seen, [])
        # 未超限：进入内层 app
        self.assertEqual(run(multipart + [(b"content-length", b"100")])[0]["status"], 200)
        self.assertEqual(seen, ["/api/tasks/t1/import"])
        # 无 / 非法 Content-Length 的 multipart：411（不进入内层 app）。
        # 此前放行给 Starlette 解析会把 >1MB 的 part spool 到系统临时盘。
        self.assertEqual(run(multipart)[0]["status"], 411)
        self.assertEqual(run(multipart + [(b"content-length", b"abc")])[0]["status"], 411)
        self.assertEqual(seen, ["/api/tasks/t1/import"])
        # 非 multipart / GET：放行（JSON 端点与读操作不受影响）
        self.assertEqual(run([(b"content-type", b"application/json"),
                              (b"content-length", b"99999")])[0]["status"], 200)
        self.assertEqual(run(multipart + [(b"content-length", b"101")],
                             method="GET")[0]["status"], 200)
        # token_validator 注入后：未认证 multipart 在解析前 401
        validator = lambda token: token == "good-token"  # noqa: E731
        authed = multipart + [(b"content-length", b"100")]
        self.assertEqual(run(authed, token_validator=validator)[0]["status"], 401)
        self.assertEqual(
            run(authed + [(b"authorization", b"Bearer bad-token")],
                token_validator=validator)[0]["status"], 401)
        self.assertEqual(
            run(authed + [(b"authorization", b"Bearer good-token")],
                token_validator=validator)[0]["status"], 200)
        # 认证顺序在 413 之前：无凭据的超限请求按 401 拒绝
        self.assertEqual(
            run(multipart + [(b"content-length", b"101")],
                token_validator=validator)[0]["status"], 401)

    def test_u5p3_duplicate_content_type_cannot_bypass_prechecks(self):
        """评审三轮手法：双 Content-Type（前 multipart 后 json）曾按后值放行。

        Starlette Headers.get 取前值（multipart 照常解析），dict 覆盖后值
        使中间件误判 application/json——411/401/413 全部失效。现重复的
        Content-Type / Content-Length 一律 400。
        """
        rules = [(re.compile(r"^/api/tasks/[^/]+/import$"), 100, 1)]
        seen = []

        async def stub_app(scope, receive, send):  # pragma: no cover - 不应被调
            seen.append(scope["path"])
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})

        def run(headers):
            replies = []

            async def receive():  # pragma: no cover
                raise AssertionError("request body must not be read")

            async def send(message):
                replies.append(message)

            middleware = UploadSizeLimitMiddleware(stub_app, rules)
            asyncio.run(middleware(
                {"type": "http", "method": "POST",
                 "path": "/api/tasks/nope/import", "headers": headers},
                receive, send))
            return replies

        ct_multipart = (b"content-type", b"multipart/form-data; boundary=x")
        ct_json = (b"content-type", b"application/json")
        # 双 Content-Type + 无 Content-Length（评审原始场景）：400，不进入内层
        self.assertEqual(run([ct_multipart, ct_json])[0]["status"], 400)
        # 双 Content-Type + 超限 Content-Length：仍 400（重复头先拒）
        self.assertEqual(run([ct_multipart, ct_json, (b"content-length", b"999")])[0]["status"], 400)
        # 反序（json 在前）同样 400；双 Content-Length 同样 400
        self.assertEqual(run([ct_json, ct_multipart, (b"content-length", b"10")])[0]["status"], 400)
        self.assertEqual(run([ct_multipart, (b"content-length", b"10"),
                              (b"content-length", b"11")])[0]["status"], 400)
        self.assertEqual(seen, [])

    def test_u5p3_file_part_counter_streaming(self):
        """文件 part 计数状态机：跨 chunk 分隔符/头部拆分、字段不计数。"""
        from custom_backend.security import _MultipartFilePartCounter

        def file_part(name, i):
            return (f'--B\r\nContent-Disposition: form-data; name="{name}"; '
                    f'filename="f{i}.csv"\r\nContent-Type: text/csv\r\n\r\n'
                    f"content{i}\r\n").encode()

        def field_part(name, value):
            return (f"--B\r\nContent-Disposition: form-data; name=\"{name}\"\r\n"
                    f"\r\n{value}\r\n").encode()

        body = (file_part("files", 1) + file_part("files", 2)
                + field_part("job_id", "j1") + field_part("source_ids", "[]")
                + b"\r\n--B--\r\n")
        # 整体一次 feed：2 个文件 part、max=2 不抛
        counter = _MultipartFilePartCounter(b"B", 2)
        counter.feed(body)
        self.assertEqual(counter.files, 2)
        # 3 个文件 part：第 3 个 part 头出现即抛（内容未进入解析器）
        counter = _MultipartFilePartCounter(b"B", 2)
        with self.assertRaises(MultipartStructureRejected):
            counter.feed(file_part("files", 1) + file_part("files", 2)
                         + file_part("files", 3) + b"X" * 1024 + b"\r\n--B--\r\n")
        # 逐字节喂（分隔符/头部全部跨 chunk 拆分）：同样在第 3 个文件头抛
        counter = _MultipartFilePartCounter(b"B", 2)
        data = file_part("files", 1) + file_part("files", 2) + file_part("files", 3)
        with self.assertRaises(MultipartStructureRejected):
            for i in range(0, len(data), 7):
                counter.feed(data[i:i + 7])
        # 结束边界不算 opening；字段不计数
        counter = _MultipartFilePartCounter(b"B", 2)
        counter.feed(b"preamble\r\n" + file_part("files", 1) + field_part("a", "1")
                     + b"\r\n--B--\r\nepilogue")
        self.assertEqual(counter.files, 1)

    def test_u5p3_file_part_overrun_stops_forwarding_before_spool(self):
        """中间件层：超限文件 part 的内容不再转发给（解析器侧的）receive。"""
        rules = [(re.compile(r"^/api/tasks/[^/]+/import-batch$"), 10 ** 9, 2)]
        file_part = (b"--B\r\nContent-Disposition: form-data; name=\"files\"; "
                     b"filename=\"f.csv\"\r\n\r\n")
        big = b"z" * (1024 * 1024)  # 1MiB：超 Starlette spool 阈值
        chunks = [
            b"--B\r\nContent-Disposition: form-data; name=\"job_id\"\r\n\r\nj\r\n",
            file_part + big + b"\r\n",
            file_part + big + b"\r\n",
            file_part + big + b"\r\n--B--\r\n",  # 第 3 个文件 part：超限
        ]

        async def stub_app(scope, receive, send):
            while True:
                message = await receive()
                if message["type"] != "http.request":
                    break
                stub_app.forwarded.append(message.get("body", b""))
                if not message.get("more_body", False):
                    break
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})

        stub_app.forwarded = []
        stream = iter(chunks)

        async def receive():
            try:
                return {"type": "http.request", "body": next(stream), "more_body": True}
            except StopIteration:
                return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):  # pragma: no cover - 异常先于响应
            pass

        middleware = UploadSizeLimitMiddleware(stub_app, rules)
        with self.assertRaises(MultipartStructureRejected):
            asyncio.run(middleware(
                {"type": "http", "method": "POST",
                 "path": "/api/tasks/t/import-batch",
                 "headers": [(b"content-type", b"multipart/form-data; boundary=B"),
                             (b"content-length", b"9999999")]},
                receive, send))
        # 转发给解析器的字节不含第 3 个文件 part（头与 1MiB 内容均未转发）
        forwarded = b"".join(stub_app.forwarded)
        self.assertEqual(forwarded.count(b'filename="f.csv"'), 2)
        self.assertLess(len(forwarded), 3 * 1024 * 1024)

    def test_u5p3_authenticated_21_parts_rejected_before_parse(self):
        """评审三轮手法：已认证 21×1.1MiB import-batch 不得再全部 spool。

        第 21 个文件 part 的头部进入 receive 层即被计数器中断（FastAPI 把
        解析期异常统一映射 400 "There was an error parsing the body"）；
        以 UploadFile 构造计数证明第 21 个 part 从未被创建/落盘——
        评审实测的旧行为是 21 个 part 全部 spool 后才由端点 400。
        """
        from starlette.datastructures import UploadFile as _UploadFile
        from unittest import mock
        created = []

        class _CountingUploadFile(_UploadFile):
            def __init__(self, *args, **kwargs):
                created.append(1)
                super().__init__(*args, **kwargs)

        payload = b"p" * int(1.1 * 1024 * 1024)
        with mock.patch("starlette.formparsers.UploadFile", _CountingUploadFile):
            r = self.client.post(
                f"/api/tasks/{self.task}/import-batch",
                files=[(f"files", (f"{i}.csv", payload, "text/csv"))
                       for i in range(21)],
                headers=self.auth())
        self.assertEqual(r.status_code, 400, r.text)
        self.assertEqual(r.json()["detail"], "There was an error parsing the body")
        # 落盘的文件 part 数以端点声明上限（20）为界：TestClient 单块投递
        # 时计数器在第 21 个 part 头进入解析器之前即中断（created==0）；
        # 真实 uvicorn 分块传输时前 20 个（合法形状）可完成、第 21 个不建。
        self.assertLessEqual(len(created), 20)

    def test_u5p3_chunked_multipart_rejected_411_without_spool(self):
        """评审手法复现：无 Content-Length（chunked）multipart 不得被解析落盘。"""
        from unittest import mock

        def chunks():
            yield b"--x\r\nContent-Disposition: form-data; name=\"file\"; "
            yield b"filename=\"a.csv\"\r\n\r\n"
            yield b"a" * (2 * 1024 * 1024)  # 2MiB：超过 Starlette 1MB spool 阈值
            yield b"\r\n--x--\r\n"

        with mock.patch("starlette.requests.MultiPartParser",
                        side_effect=AssertionError("multipart parsed")):
            r = self.client.post(
                f"/api/tasks/{self.task}/import", content=chunks(),
                headers={"Content-Type": "multipart/form-data; boundary=x"})
        # 411 = 中间件在解析前拒绝（TestClient 生成 chunked 请求本就无 Content-Length）
        self.assertEqual(r.status_code, 411, r.text)

    def test_u5p3_unauthenticated_multipart_401_before_parse(self):
        """评审手法复现：未认证多文件 multipart 不得在 401 前被解析 spool。"""
        from unittest import mock
        payload = b"a" * (1024 * 1024 + 1)  # 单 part 即超 spool 阈值
        with mock.patch("starlette.requests.MultiPartParser",
                        side_effect=AssertionError("multipart parsed")):
            r = self.client.post(
                f"/api/tasks/{self.task}/import-batch",
                files=[(f"files", (f"{i}.csv", payload, "text/csv"))
                       for i in range(3)])
        self.assertEqual(r.status_code, 401, r.text)
        self.assertIn("www-authenticate",
                      {k.lower() for k in r.headers.keys()})

    def test_u5p3_authenticated_batch_file_count_bounded_by_endpoint(self):
        """已认证超文件数请求（小文件同样）：第 21 个文件 part 头即中断。

        端点侧 1..20 校验仍在（解析后兜底），但 >20 的结构不再进入解析器。
        2 个合法文件数的请求不被预检拦截（进入端点正常处理路径）。
        """
        r = self.client.post(
            f"/api/tasks/{self.task}/import-batch",
            files=[(f"files", (f"{i}.csv", b"zotero_key,decision\n", "text/csv"))
                   for i in range(21)],
            headers=self.auth())
        # 计数器在解析层中断（FastAPI 统一 400 parse error），而不是端点
        # 落盘后的 'Choose between 1 and 20'（那是旧行为的特征文案）
        self.assertEqual(r.status_code, 400, r.text)
        self.assertNotIn("Choose between 1 and 20", r.text)
        # 2 个文件（在 1..20 内）：预检放行、multipart 正常解析，进入端点
        # 导入流程（合法 CSV 导入成功）
        r = self.client.post(
            f"/api/tasks/{self.task}/import-batch",
            files=[(f"files", (f"{i}.csv",
                               f"zotero_key,Title,doi\nK{i},Study {i},10.1/{i}\n",
                               "text/csv"))
                   for i in range(2)],
            headers=self.auth())
        self.assertEqual(r.status_code, 200, r.text)
        self.assertNotIn("parsing the body", r.text)

    def test_u5p3_trace_110mib_package_enters_endpoint(self):
        """评审回归：真实 110MiB trace 包不得被预检误拒（此前 101MiB 上限）。

        以受控大体积（110MiB 零字节包体）经 TestClient 发送，multipart
        编码携带真实 Content-Length；包体非 zip 由端点业务层拒绝（400
        invalid_trace_package），证明请求穿过了 129MiB 预检并进入端点。
        """
        package = b"\0" * (110 * 1024 * 1024)
        r = self.client.post(
            f"/api/tasks/{self.task}/trace/imports/preview",
            files={"package": ("p.zip", package, "application/zip")},
            headers=self.auth())
        self.assertEqual(r.status_code, 400, r.text)
        self.assertEqual(r.json()["detail"]["error_code"], "invalid_trace_package")

    def test_u5p3_trace_precheck_aligned_with_endpoint_body_limit(self):
        """预检上限必须与端点 _body_within_limit 守卫同值（129MiB）。

        大于该值时 129–130MiB 的请求会穿过预检、解析落盘后才 413
        （评审三轮指出的 1MiB 缝隙）。
        """
        from custom_backend.main import _UPLOAD_SIZE_RULES
        from custom_backend.research_trace_api import MAX_TRACE_BODY_BYTES
        limits = {pattern.pattern: (limit, max_files)
                  for pattern, limit, max_files in _UPLOAD_SIZE_RULES}
        trace_limit, trace_files = limits[
            r"^/api/tasks/[^/]+/trace/imports/(preview|commit)$"]
        self.assertEqual(trace_limit, MAX_TRACE_BODY_BYTES)  # 129MiB，不多不少
        self.assertEqual(trace_files, 1)

    def test_u5p3_every_upload_endpoint_has_size_rule(self):
        """静态守护：新增 UploadFile 端点必须补预检规则（字节 + 文件数）。"""
        import inspect
        from custom_backend.main import _UPLOAD_SIZE_RULES
        uncovered = []
        for route in app.routes:
            if not getattr(route, "methods", None) or "POST" not in route.methods:
                continue
            try:
                params = inspect.signature(route.endpoint).parameters.values()
            except (TypeError, ValueError):
                continue
            # main.py 启用 from __future__ import annotations：注解是字符串
            if not any("UploadFile" in str(p.annotation) for p in params):
                continue
            sample = re.sub(r"\{[^}]+\}", "x", route.path)
            matched = [rule for rule in _UPLOAD_SIZE_RULES if rule[0].match(sample)]
            if not matched:
                uncovered.append(route.path)
            else:
                _, limit, max_files = matched[0]
                self.assertIsInstance(limit, int)
                self.assertIsInstance(max_files, int)
                self.assertGreaterEqual(max_files, 1)
        self.assertEqual(uncovered, [],
                         "这些上传端点缺少 Content-Length 预检规则")

    def test_u5p3_precheck_limits_match_endpoint_reality(self):
        """预检上限与端点真实限制对齐（防回归漂移）。"""
        from custom_backend.main import _UPLOAD_SIZE_RULES
        from coscreen.research_trace_export import MAX_PACKAGE_BYTES
        from coscreen.coding_evidence import MAX_UPLOAD_BYTES as EVIDENCE_MAX
        from custom_backend.research_trace_api import MAX_TRACE_BODY_BYTES
        limits = {pattern.pattern: (limit, max_files)
                  for pattern, limit, max_files in _UPLOAD_SIZE_RULES}
        trace, trace_files = limits[r"^/api/tasks/[^/]+/trace/imports/(preview|commit)$"]
        self.assertEqual(trace, MAX_TRACE_BODY_BYTES)               # 端点守卫同值（129MiB）
        self.assertEqual(MAX_TRACE_BODY_BYTES, MAX_PACKAGE_BYTES + 1024 * 1024)
        self.assertEqual(trace_files, 1)
        evidence, evidence_files = limits[
            r"^/api/tasks/[^/]+/coding/(submission-)?evidence/uploads$"]
        self.assertEqual(evidence, EVIDENCE_MAX + 1024 * 1024)      # 端点 10MB
        self.assertEqual(evidence_files, 1)
        screening, screening_files = limits[r"^/api/tasks/[^/]+/screening-arbitrations$"]
        self.assertEqual(screening, 9 * 100 * 1024 * 1024 + 1024 * 1024)  # 2..9 文件
        self.assertEqual(screening_files, 9)
        batch, batch_files = limits[r"^/api/tasks/[^/]+/import-batch$"]
        self.assertEqual(batch_files, 20)                           # review_imports 20 文件上限
        single, single_files = limits[r"^/api/tasks/[^/]+/import$"]
        self.assertEqual(single, 100 * 1024 * 1024 + 1024 * 1024)
        self.assertEqual(single_files, 1)
        living = limits[r"^/api/tasks/[^/]+/living-review/import-update$"][0]
        self.assertLessEqual(living, 11 * 1024 * 1024)              # 不读文件的小上限

    # ------------------------------------------------------------------
    # U5-P5：限流 key 不信任 XFF；launcher 显式 proxy_headers=False
    # ------------------------------------------------------------------
    def test_u5p5_rate_limit_key_ignores_forwarded_for(self):
        self.assertEqual(rate_limit_key("login", "1.2.3.4", None), "login:1.2.3.4")
        self.assertEqual(rate_limit_key("login", None, None), "login:?")
        # XFF 存在时全部并入固定桶：伪造者无法轮换出独立配额
        self.assertEqual(rate_limit_key("login", "1.2.3.4", "5.6.7.8"),
                         "login:xff-untrusted")
        self.assertEqual(rate_limit_key("login", "1.2.3.4", "9.9.9.9"),
                         "login:xff-untrusted")
        launcher_src = (Path(__file__).resolve().parents[2]
                        / "build" / "launcher.py").read_text(encoding="utf-8")
        self.assertIn("proxy_headers=False", launcher_src,
                      "uvicorn Config 必须显式关闭 proxy_headers（XFF 伪造防护）")

    # ------------------------------------------------------------------
    # U5-P6：非 ASCII 文件名的 Content-Disposition 必须百分号编码
    # ------------------------------------------------------------------
    def test_u5p6_content_disposition_quoted_for_chinese_names(self):
        name = "汇报2023-protocol.md"
        response = _file_response(b"data", "text/markdown; charset=utf-8", name)
        disposition = response.headers["content-disposition"]
        # 构造响应本身不再抛 UnicodeEncodeError（此前 starlette latin-1 编码 500）
        disposition.encode("latin-1")
        self.assertIn(f"filename*=UTF-8''{quote(name, safe='')}", disposition)
        self.assertIn('filename="2023-protocol.md"', disposition)

    def test_u5p6_chinese_fulltext_download_end_to_end(self):
        ctx = app.state.review_workflow.context(self.task, self.user)
        db_mod.upsert_articles(ctx.owner_db, [Article(
            zotero_key="汇报2023", title="中文文献", doi="10.test/cn")], [])
        pdf = _one_page_pdf()
        r = self.client.post(
            f"/api/tasks/{self.task}/fulltext/汇报2023/upload",
            files={"file": ("汇报2023.pdf", pdf, "application/pdf")},
            headers=self.auth())
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["n_pages"], 1)
        r = self.client.get(f"/api/tasks/{self.task}/fulltext/汇报2023",
                            headers=self.auth())
        self.assertEqual(r.status_code, 200, r.text)
        disposition = r.headers["content-disposition"]
        disposition.encode("latin-1")
        self.assertIn("inline", disposition)
        self.assertIn(f"filename*=UTF-8''{quote('汇报2023.pdf', safe='')}",
                      disposition)
        self.assertEqual(r.content, pdf)

    def test_u5p6_chinese_task_protocol_export_end_to_end(self):
        r = self.client.post("/api/tasks", json={"name": "汇报任务甲"},
                             headers=self.auth())
        self.assertEqual(r.status_code, 201, r.text)
        task_id = r.json()["task_id"]
        self.assertRegex(task_id, "汇报")  # task_id 保留汉字（旧代码此处会 500）
        r = self.client.post(f"/api/tasks/{task_id}/protocol", json={
            "title": "中文方案", "pico": [
                {"key": "P", "label": "人群", "value": "成人"}]},
            headers=self.auth())
        self.assertEqual(r.status_code, 200, r.text)
        r = self.client.get(f"/api/tasks/{task_id}/protocol/export",
                            headers=self.auth())
        self.assertEqual(r.status_code, 200, r.text)
        disposition = r.headers["content-disposition"]
        disposition.encode("latin-1")
        self.assertIn(f"filename*=UTF-8''{quote(f'{task_id}-protocol.md', safe='')}",
                      disposition)

    # ------------------------------------------------------------------
    # U5-P7：PDF 上传外键失败（文献不存在）映射 4xx，不再 500
    # ------------------------------------------------------------------
    def test_u5p7_integrity_error_maps_to_400_not_500(self):
        pdf = _one_page_pdf()
        r = self.client.post(
            f"/api/tasks/{self.task}/fulltext/doesnotexist/upload",
            files={"file": ("x.pdf", pdf, "application/pdf")},
            headers=self.auth())
        self.assertEqual(r.status_code, 400, r.text)
        self.assertIn("文献键不存在", r.json()["detail"])

    # ------------------------------------------------------------------
    # U5-P8/P10：文件名清洗——先留扩展名再截断；Windows 保留设备名加前缀
    # ------------------------------------------------------------------
    def test_u5p8_truncation_preserves_extension_and_reserved_names(self):
        long_csv = "x." + "a" * 184 + ".csv"  # 186 字符，旧代码截掉 .csv -> 415
        cleaned = clean_upload_name(long_csv)
        self.assertEqual(len(cleaned), 180)
        self.assertTrue(cleaned.endswith(".csv"))
        check_extension(long_csv, TEXT_EXTENSIONS)  # 不再 415
        # 评审回归："扩展名"本身超长（'a.'+'b'*300）不得突破 180 上限
        self.assertEqual(len(clean_upload_name("a." + "b" * 300)), 180)
        # 超长主体 + 长但合法（≤40 字符）的扩展名：封顶 180 且保留扩展名
        long_ext = "y." + "a" * 160 + "." + "b" * 35
        cleaned = clean_upload_name(long_ext)
        self.assertEqual(len(cleaned), 180)
        self.assertTrue(cleaned.endswith("b" * 35))
        # 保留设备名（含带扩展名形态）加下划线前缀
        self.assertEqual(clean_upload_name("CON"), "_CON")
        self.assertEqual(clean_upload_name("NUL"), "_NUL")
        self.assertEqual(clean_upload_name("COM1.csv"), "_COM1.csv")
        self.assertEqual(clean_upload_name("nul.tar.gz"), "_nul.tar.gz")
        self.assertEqual(clean_upload_name("lpt9"), "_lpt9")
        # 普通名字不受影响
        self.assertEqual(clean_upload_name("正常文件-1.csv"), "正常文件-1.csv")

    # ------------------------------------------------------------------
    # U5-P9/P11：pypdf 解析 0 页拒绝；magic 校验不是内容安全边界
    # ------------------------------------------------------------------
    def test_u5p9_zero_page_polyglot_rejected(self):
        polyglot = b"%PDF-1.4\n<html><script>alert(document.domain)</script></html>"
        r = self.client.post(
            f"/api/tasks/{self.task}/fulltext/polyglot-key/upload",
            files={"file": ("evil.pdf", polyglot, "application/pdf")},
            headers=self.auth())
        self.assertEqual(r.status_code, 400, r.text)
        self.assertIn("无法解析", r.json()["detail"])
        pdfs = backend_main._pdfs_dir(self.info)
        self.assertEqual(list(pdfs.glob("*.pdf")), [],
                         "0 页 PDF 不得落盘")

    # ------------------------------------------------------------------
    # U5-P10/P12：SSE 短期票据——签发/一次性消费/弃用提醒
    #
    # 注：TestClient 的传输层会缓冲完整响应体，无限 SSE 流上进入 stream
    # 上下文必然死锁（传输限制，非应用缺陷），因此这里直接驱动端点协程。
    # ------------------------------------------------------------------
    def _events_request(self, query: str, authorization: str | None = None):
        from starlette.requests import Request
        headers = [(b"content-type", b"text/plain")]
        if authorization:
            headers.append((b"authorization", authorization.encode("latin-1")))
        scope = {"type": "http", "method": "GET", "path": "/api/events",
                 "headers": headers, "query_string": query.encode("ascii")}
        return Request(scope)

    def _first_sse_chunk(self, response):
        async def read_first():
            iterator = response.body_iterator
            chunk = await iterator.__anext__()
            await iterator.aclose()
            return chunk

        return asyncio.run(read_first())

    def test_u5p10_event_ticket_lifecycle(self):
        from fastapi import HTTPException
        from custom_backend.main import task_events
        r = self.client.post("/api/events/ticket")
        self.assertEqual(r.status_code, 401)
        r = self.client.post("/api/events/ticket", headers=self.auth())
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["expires_in"], 300)
        ticket = body["ticket"]
        # 无效票据 -> 401（HTTPException，不建立事件流）
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(task_events(self._events_request("ticket=forged"),
                                    ticket="forged", token=None, authorization=None))
        self.assertEqual(ctx.exception.status_code, 401)
        # 有效票据可连流，且响应不带弃用头
        response = asyncio.run(task_events(
            self._events_request(f"ticket={quote(ticket, safe='')}"),
            ticket=ticket, token=None, authorization=None))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("deprecation", response.headers)
        self.assertIn("retry:", self._first_sse_chunk(response))
        # 票据一次性：重放 401
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(task_events(self._events_request(f"ticket={quote(ticket, safe='')}"),
                                    ticket=ticket, token=None, authorization=None))
        self.assertEqual(ctx.exception.status_code, 401)

    def test_u5p10_legacy_token_deprecation_header(self):
        from custom_backend.main import task_events
        query = f"token={quote(self.user['token'], safe='')}"
        response = asyncio.run(task_events(
            self._events_request(query), ticket=None,
            token=self.user["token"], authorization=None))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("deprecation"), "true")
        self.assertIn("deprecated", response.headers.get("warning", ""))
        # 响应头在构造时即完成 latin-1 编码（非 ASCII 会直接抛错），能取到
        # raw_headers 即证明弃用提醒头无 U5-P6 同型的编码问题。
        self.assertTrue(all(isinstance(v, bytes) for _k, v in response.raw_headers))
        self.assertIn("retry:", self._first_sse_chunk(response))
        # 完全无凭据：401
        from fastapi import HTTPException
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(task_events(self._events_request(""),
                                    ticket=None, token=None, authorization=None))
        self.assertEqual(ctx.exception.status_code, 401)

    def test_u5p10_event_ticket_store_one_time_and_expiry(self):
        store = EventTicketStore(ttl_seconds=300)
        ticket, expires_in = store.issue(7, "alice", now=100.0)
        self.assertEqual(expires_in, 300)
        self.assertEqual(store.consume(ticket, now=200.0),
                         {"user_id": 7, "username": "alice"})
        self.assertIsNone(store.consume(ticket, now=201.0))  # 一次性
        ticket2, _ = store.issue(8, "bob", now=100.0)
        self.assertIsNone(store.consume(ticket2, now=401.0))  # 过期


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
