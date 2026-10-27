"""2.1.4 上传与路径安全（U5-P1..P12）回归测试。

覆盖：任务锁别名归一（P1）、同进程删除互斥（P2）、上传 Content-Length 预检
（P3）、中间件鉴权前置（P4）、限流 key 防伪造（P5）、Content-Disposition
百分号编码（P6）、IntegrityError 映射（P7）、文件名截断/保留名（P8）、
PDF 0 页拒绝（P9/P11）、SSE 短期票据（P10/P12）；
r6 复审追加：R6-S1（限流按直连 peer 分桶回退）、R6-S2（删除中标记 +
在途计数只计写方法）、R6-S3（boundary 对齐 Starlette/字段 part 上限/
放弃分类保守计数）、R6-S4（出货前端换票流静态守护）、R6-S5（中间件早拒
响应补安全头/CORS）。
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
    MAX_FIELD_PART_BYTES,
    MAX_FIELD_PARTS,
    EventTicketStore,
    MultipartStructureRejected,
    UploadSizeLimitMiddleware,
    _MultipartPartCounter,
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
        TaskLockMiddleware._deleting.clear()
        TaskLockMiddleware._list_readers = 0
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
    # U5-P2：同进程在途**写**操作时 DELETE 必须 409（导入进行中删除任务）；
    # 只读轮询（GET）不再计入（R6-S2：此前后台轮询会让 DELETE 误报 409）
    # ------------------------------------------------------------------
    def test_u5p2_delete_rejected_while_inflight_same_process(self):
        lock = tasks_mod.acquire_task_lock(self.info.task_id, backend_main.DATA_DIR)
        self.assertIsNotNone(lock)
        # [在途请求数(全部), 锁路径, 在途写请求数]：写口径 >1 才拒绝。
        # 模拟 1 个导入 POST 在途（ref=1/writes=1）；DELETE 请求自身经中间件
        # 再 +1 → 端点读到 writes=2 → 409。
        TaskLockMiddleware._held[self.info.task_id] = [1, lock, 1]
        try:
            r = self.client.delete(f"/api/tasks/{self.task}", headers=self.auth())
            self.assertEqual(r.status_code, 409, r.text)
            self.assertIn("任务有操作进行中", r.json()["detail"])
            self.assertTrue(self.info.dir.is_dir(), "在途时任务目录不得被移入 .trash")
            # coscreen 层直调 delete_task 同样拒绝（钩子已由 main 注册）；
            # 直调无中间件加成，需自行把写口径抬到 >1 验证钩子路径
            TaskLockMiddleware._held[self.info.task_id] = [2, lock, 2]
            with self.assertRaises(ValueError) as ctx:
                tasks_mod.delete_task(self.task, backend_main.DATA_DIR)
            self.assertIn("任务有操作进行中", str(ctx.exception))
        finally:
            TaskLockMiddleware._held.clear()
            tasks_mod.release_task_lock(lock, force=True)
        # R6-S2：在途的是只读轮询（ref=1/writes=0，DELETE 自身 +1 后 writes=1）
        # ——写口径不触发「任务有操作进行中」；但合成的在途读永不结束，
        # 会在排空超时后按读排空口径 409（真实读请求的排空→200 路径由
        # test_r6s2_inflight_read_delete_drains_then_moves 覆盖）。
        from unittest import mock
        lock = tasks_mod.acquire_task_lock(self.info.task_id, backend_main.DATA_DIR)
        TaskLockMiddleware._held[self.info.task_id] = [1, lock, 0]
        try:
            with mock.patch.object(TaskLockMiddleware, "READ_DRAIN_TIMEOUT", 0.1):
                r = self.client.delete(f"/api/tasks/{self.task}", headers=self.auth())
            self.assertEqual(r.status_code, 409, r.text)
            self.assertIn("仍有读取请求未完成", r.json()["detail"])
            self.assertNotIn("任务有操作进行中", r.json()["detail"])
            self.assertTrue(self.info.dir.is_dir(), "任务目录不得被移动/半删")
        finally:
            TaskLockMiddleware._held.clear()
            tasks_mod.release_task_lock(lock, force=True)

    # ------------------------------------------------------------------
    # R6-S2：删除中标记——置位期间中间件对该任务的一切请求（含 GET/导入）
    # 直接 409；检查通过到 shutil.move 之间不再有并发读写窗口
    # ------------------------------------------------------------------
    def test_r6s2_deleting_flag_blocks_all_requests_during_move(self):
        import threading
        entered = threading.Event()
        release = threading.Event()
        original = tasks_mod.delete_task

        def paused_delete(task_id, data_dir):
            # 「暂停 move」：endpoint 已置 deleting 标记（_begin_delete 先于
            # 本调用），此刻另一窗口的并发读/写必须 409 而非 200+目录消失
            entered.set()
            release.wait(timeout=10)
            return original(task_id, data_dir)

        tasks_mod.delete_task = paused_delete
        result = {}

        def do_delete():
            client = TestClient(app)  # 另一窗口：独立客户端/独立传输
            try:
                result["resp"] = client.delete(
                    f"/api/tasks/{self.task}", headers=self.auth())
            except Exception as exc:  # noqa: BLE001  线程内异常带回主线程断言
                result["error"] = exc

        try:
            worker = threading.Thread(target=do_delete)
            worker.start()
            self.assertTrue(entered.wait(timeout=15),
                            "DELETE 未进入删除阶段（标记未置位）")
            r_articles = self.client.get(
                f"/api/tasks/{self.task}/articles", headers=self.auth())
            r_import = self.client.post(
                f"/api/tasks/{self.task}/import", json={}, headers=self.auth())
        finally:
            release.set()
            worker.join(timeout=30)
            tasks_mod.delete_task = original
            TaskLockMiddleware._deleting.clear()
        self.assertNotIn("error", result)
        self.assertEqual(result["resp"].status_code, 200, result["resp"].text)
        self.assertEqual(r_articles.status_code, 409, r_articles.text)
        self.assertIn("任务正在被删除", r_articles.json()["detail"])
        # 早拒 409 同样带安全头（R6-S5）
        self.assertEqual(r_articles.headers.get("x-content-type-options"), "nosniff")
        self.assertEqual(r_import.status_code, 409, r_import.text)
        # 删除完成后标记清除、任务确实消失
        self.assertEqual(TaskLockMiddleware._deleting, set())
        r = self.client.get(f"/api/tasks/{self.task}/articles", headers=self.auth())
        self.assertEqual(r.status_code, 404, r.text)

    def test_r6s2_deleting_flag_via_middleware_asgi(self):
        """中间件层直证：标记置位即拒（含 GET），不进入内层 app。"""
        hits = []

        async def stub_app(scope, receive, send):  # pragma: no cover - 不应被调
            hits.append(scope["path"])
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})

        middleware = TaskLockMiddleware(stub_app)
        token = self.user["token"]
        TaskLockMiddleware._begin_delete(self.info.task_id)
        try:
            replies = []

            async def send(message):
                replies.append(message)

            asyncio.run(middleware(
                {"type": "http", "method": "GET",
                 "path": f"/api/tasks/{self.task}/articles",
                 "headers": [(b"authorization", f"Bearer {token}".encode("ascii"))]},
                None, send))
            self.assertEqual(replies[0]["status"], 409)
            self.assertEqual(hits, [])
        finally:
            TaskLockMiddleware._end_delete(self.info.task_id)
        # 清除后同一请求放行
        replies = []

        async def send(message):
            replies.append(message)

        asyncio.run(middleware(
            {"type": "http", "method": "GET",
             "path": f"/api/tasks/{self.task}/articles",
             "headers": [(b"authorization", f"Bearer {token}".encode("ascii"))]},
            None, send))
        self.assertEqual(replies[0]["status"], 200)
        self.assertEqual(hits, [f"/api/tasks/{self.task}/articles"])

    # ------------------------------------------------------------------
    # R6-S2 评审二轮：在途读 + DELETE——读请求持有 SQLite 句柄时不得触发
    # shutil.move 的 copytree+rmtree 回退（半删原目录 + .trash 全量副本 + 500）
    # ------------------------------------------------------------------
    def _holding_read_setup(self):
        """把 list_articles 换成『打开 SQLite 连接 → 阻塞 → 关闭』的复现手法。"""
        import sqlite3
        import threading
        entered = threading.Event()
        original = db_mod.list_articles

        def holding_read(db_path, include_duplicates=False, hold_seconds=0.6):
            conn = sqlite3.connect(db_path)  # 持有任务库文件句柄（评审手法）
            try:
                entered.set()
                time_holder["stop"].wait(timeout=hold_seconds)
            finally:
                conn.close()
            return original(db_path, include_duplicates)

        time_holder = {"stop": threading.Event()}

        def install():
            backend_main.db_mod.list_articles = holding_read

        def restore():
            backend_main.db_mod.list_articles = original

        return entered, time_holder, install, restore

    def test_r6s2_inflight_read_delete_drains_then_moves(self):
        """在途读 0.6s 内结束：DELETE 排空后正常 move——无 500、无半删。"""
        import threading
        entered, holder, install, restore = self._holding_read_setup()
        result = {}
        worker = None
        install()
        try:
            def do_get():
                client = TestClient(app)
                try:
                    result["get"] = client.get(
                        f"/api/tasks/{self.task}/articles", headers=self.auth())
                except Exception as exc:  # noqa: BLE001
                    result["get_error"] = exc

            worker = threading.Thread(target=do_get)
            worker.start()
            self.assertTrue(entered.wait(timeout=10), "读请求未进入持有段")
            # 读在途（句柄打开）时发 DELETE：drain 等待读完成再 move
            r = self.client.delete(f"/api/tasks/{self.task}", headers=self.auth())
            worker.join(timeout=15)
        finally:
            holder["stop"].set()
            if worker is not None:
                worker.join(timeout=15)
            restore()
            TaskLockMiddleware._deleting.clear()
        self.assertEqual(r.status_code, 200, r.text)
        self.assertNotIn("get_error", result)
        self.assertEqual(result["get"].status_code, 200, result["get"].text)
        # 整目录移入 .trash（原子 move，无半删残留）
        self.assertFalse(self.info.dir.exists())
        trash_root = backend_main.DATA_DIR / ".trash"
        self.assertTrue(any(self.info.task_id in p.name for p in trash_root.iterdir()))
        self.assertEqual(TaskLockMiddleware._deleting, set())

    def test_r6s2_inflight_read_delete_timeout_keeps_task_intact(self):
        """在途读超过排空上限：受控 409，任务目录原样、无 .trash 副本。"""
        import threading
        from unittest import mock
        entered, holder, install, restore = self._holding_read_setup()
        # 快照在读请求进入前拍（任务锁文件由中间件创建/释放，属簿记而非数据）
        before = sorted(p.name for p in self.info.dir.iterdir()
                        if not p.name.endswith(".lock"))
        worker = None
        install()
        try:
            def do_get():
                client = TestClient(app)
                try:
                    result["get"] = client.get(
                        f"/api/tasks/{self.task}/articles", headers=self.auth())
                except Exception as exc:  # noqa: BLE001
                    result["get_error"] = exc

            result = {}
            worker = threading.Thread(target=do_get)
            worker.start()
            self.assertTrue(entered.wait(timeout=10), "读请求未进入持有段")
            with mock.patch.object(TaskLockMiddleware, "READ_DRAIN_TIMEOUT", 0.2):
                r = self.client.delete(f"/api/tasks/{self.task}", headers=self.auth())
            holder["stop"].set()
            worker.join(timeout=15)
        finally:
            holder["stop"].set()
            if worker is not None:
                worker.join(timeout=15)
            restore()
            TaskLockMiddleware._deleting.clear()
        self.assertEqual(r.status_code, 409, r.text)
        self.assertIn("仍有读取请求未完成", r.json()["detail"])
        # 数据不被破坏：删除前的文件一个不少（读请求自建 screening 库属正常
        # 新增），.trash 没有本任务副本——半删/copytree 回退不得发生
        after = sorted(p.name for p in self.info.dir.iterdir())
        self.assertTrue(set(before) <= set(after), f"{before} 不在 {after} 中")
        trash_root = backend_main.DATA_DIR / ".trash"
        self.assertFalse(trash_root.exists()
                         and any(self.info.task_id in p.name for p in trash_root.iterdir()))
        self.assertNotIn("get_error", result)
        # 读结束后重试删除成功
        r = self.client.delete(f"/api/tasks/{self.task}", headers=self.auth())
        self.assertEqual(r.status_code, 200, r.text)
        self.assertFalse(self.info.dir.exists())

    # ------------------------------------------------------------------
    # R6-S2 三轮：全局列表读（GET /api/tasks，打开所有任务的 task.json）
    # 必须纳入删除互斥——列表持 task.json 时 DELETE 曾触发 copytree+rmtree
    # 半删（评审实测：DELETE 500、collaboration.db 从原目录消失、.trash 全量副本）
    # ------------------------------------------------------------------
    def _holding_list_setup(self, hold_seconds):
        """把 list_tasks 换成『打开 task.json → 阻塞 → 关闭』的评审手法。"""
        import threading
        entered = threading.Event()
        holder = {"stop": threading.Event()}
        original = tasks_mod.list_tasks

        def holding_list(data_dir, include_archived=False, **kwargs):
            handle = open(self.info.dir / "task.json", "r", encoding="utf-8")
            try:
                entered.set()
                holder["stop"].wait(timeout=hold_seconds)
            finally:
                handle.close()
            return original(data_dir, include_archived=include_archived, **kwargs)

        def install():
            tasks_mod.list_tasks = holding_list

        def restore():
            tasks_mod.list_tasks = original

        return entered, holder, install, restore

    def test_r6s2_list_read_delete_drains_then_moves(self):
        """评审三轮手法回放：列表持 task.json 0.8s 内结束——DELETE 排空后
        正常 move（不再 500/半删），列表请求自身 200。"""
        import threading
        entered, holder, install, restore = self._holding_list_setup(0.8)
        result = {}
        worker = None
        install()
        try:
            def do_list():
                client = TestClient(app)
                try:
                    result["list"] = client.get("/api/tasks", headers=self.auth())
                except Exception as exc:  # noqa: BLE001
                    result["list_error"] = exc

            worker = threading.Thread(target=do_list)
            worker.start()
            self.assertTrue(entered.wait(timeout=10), "列表读未进入持有段")
            r = self.client.delete(f"/api/tasks/{self.task}", headers=self.auth())
            holder["stop"].set()
        finally:
            holder["stop"].set()
            if worker is not None:
                worker.join(timeout=15)
            restore()
            TaskLockMiddleware._deleting.clear()
        self.assertEqual(r.status_code, 200, r.text)
        self.assertNotIn("list_error", result)
        self.assertEqual(result["list"].status_code, 200, result["list"].text)
        # 整目录原子移入 .trash（评审旧症状：collaboration.db 半删 + 全量副本）
        self.assertFalse(self.info.dir.exists())
        trash_root = backend_main.DATA_DIR / ".trash"
        self.assertTrue(any(self.info.task_id in p.name for p in trash_root.iterdir()))
        self.assertEqual(TaskLockMiddleware._deleting, set())
        self.assertEqual(TaskLockMiddleware._list_readers, 0)

    def test_r6s2_list_read_delete_timeout_keeps_task_intact(self):
        """列表读超过排空上限：受控 409，目录原样、无 .trash 副本，重试成功。"""
        import threading
        from unittest import mock
        entered, holder, install, restore = self._holding_list_setup(6.0)
        before = sorted(p.name for p in self.info.dir.iterdir()
                        if not p.name.endswith(".lock"))
        worker = None
        r = None
        install()
        try:
            def do_list():
                client = TestClient(app)
                client.get("/api/tasks", headers=self.auth())

            worker = threading.Thread(target=do_list)
            worker.start()
            self.assertTrue(entered.wait(timeout=10), "列表读未进入持有段")
            with mock.patch.object(TaskLockMiddleware, "READ_DRAIN_TIMEOUT", 0.2):
                r = self.client.delete(f"/api/tasks/{self.task}", headers=self.auth())
            after = sorted(p.name for p in self.info.dir.iterdir())
            trash_root = backend_main.DATA_DIR / ".trash"
            self.assertFalse(trash_root.exists()
                             and any(self.info.task_id in p.name
                                     for p in trash_root.iterdir()))
        finally:
            holder["stop"].set()
            if worker is not None:
                worker.join(timeout=15)
            restore()
            TaskLockMiddleware._deleting.clear()
        self.assertEqual(r.status_code, 409, r.text)
        self.assertIn("仍有读取请求未完成", r.json()["detail"])
        # 数据不被破坏：删除前的文件一个不少（半删/copytree 回退不得发生）
        after_final = sorted(p.name for p in self.info.dir.iterdir())
        self.assertTrue(set(before) <= set(after_final),
                        f"{before} 不在 {after_final} 中")
        # 列表结束后重试删除成功
        r = self.client.delete(f"/api/tasks/{self.task}", headers=self.auth())
        self.assertEqual(r.status_code, 200, r.text)
        self.assertFalse(self.info.dir.exists())

    def test_r6s2_list_endpoint_defers_during_delete(self):
        """删除进行中（deleting 置位）时全局列表读延迟准入，标志清除后放行。"""
        import threading
        done = threading.Event()
        result = {}

        def do_list():
            client = TestClient(app)
            result["resp"] = client.get("/api/tasks", headers=self.auth())
            done.set()

        TaskLockMiddleware._begin_delete(self.info.task_id)
        try:
            worker = threading.Thread(target=do_list)
            worker.start()
            # 列表在 deleting 置位期间不得完成读（有界等待循环中）
            self.assertFalse(done.wait(timeout=0.4),
                             "删除进行中列表读不得立即完成")
        finally:
            TaskLockMiddleware._end_delete(self.info.task_id)
        self.assertTrue(done.wait(timeout=15), "标志清除后列表读应完成")
        worker.join(timeout=15)
        self.assertEqual(result["resp"].status_code, 200, result["resp"].text)
        self.assertEqual(TaskLockMiddleware._list_readers, 0)

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
        """part 计数状态机：跨 chunk 分隔符/头部拆分、字段分开计数（R6-S3）。"""

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
        # 整体一次 feed：2 个文件 part + 2 个字段 part、max=2 不抛
        counter = _MultipartPartCounter(b"B", 2)
        counter.feed(body)
        self.assertEqual(counter.files, 2)
        self.assertEqual(counter.fields, 2)
        # 3 个文件 part：第 3 个 part 头出现即抛（内容未进入解析器）
        counter = _MultipartPartCounter(b"B", 2)
        with self.assertRaises(MultipartStructureRejected):
            counter.feed(file_part("files", 1) + file_part("files", 2)
                         + file_part("files", 3) + b"X" * 1024 + b"\r\n--B--\r\n")
        # 逐字节喂（分隔符/头部全部跨 chunk 拆分）：同样在第 3 个文件头抛
        counter = _MultipartPartCounter(b"B", 2)
        data = file_part("files", 1) + file_part("files", 2) + file_part("files", 3)
        with self.assertRaises(MultipartStructureRejected):
            for i in range(0, len(data), 7):
                counter.feed(data[i:i + 7])
        # 结束边界不算 part；字段单独计数不占文件配额
        counter = _MultipartPartCounter(b"B", 2)
        counter.feed(b"preamble\r\n" + file_part("files", 1) + field_part("a", "1")
                     + b"\r\n--B--\r\nepilogue")
        self.assertEqual(counter.files, 1)
        self.assertEqual(counter.fields, 1)

    def test_r6s3_field_parts_counted_and_capped(self):
        """R6-S3 复审 #38 复现：字段 part 不再无限制（曾 50×1MiB 整体进内存）。"""

        def field_part(name, value):
            return (f"--B\r\nContent-Disposition: form-data; name=\"{name}\"\r\n"
                    f"\r\n{value}\r\n").encode()

        # 合法范围：16 个小字段（端点真实字段 ≤4 个，16 已是 4 倍余量）
        counter = _MultipartPartCounter(b"B", 2)
        counter.feed(b"".join(field_part(f"f{i}", "v") for i in range(16))
                     + b"--B--\r\n")
        self.assertEqual(counter.fields, 16)
        # 第 17 个字段 part：拒绝
        counter = _MultipartPartCounter(b"B", 2)
        with self.assertRaises(MultipartStructureRejected):
            counter.feed(b"".join(field_part(f"f{i}", "v")
                                  for i in range(MAX_FIELD_PARTS + 1))
                         + b"--B--\r\n")
        # 单字段超过 1MB+余量：拒绝（跨 chunk 累计同样拒绝）
        counter = _MultipartPartCounter(b"B", 2)
        header = b'--B\r\nContent-Disposition: form-data; name="big"\r\n\r\n'
        value = b"v" * (MAX_FIELD_PART_BYTES + 1)
        with self.assertRaises(MultipartStructureRejected):
            counter.feed(header)
            for i in range(0, len(value), 65536):
                counter.feed(value[i:i + 65536])
            counter.feed(b"\r\n--B--\r\n")
        # 大文件 part 的 body 不受字段大小限制（只受文件数/CL 限制）
        counter = _MultipartPartCounter(b"B", 1)
        payload = b"z" * (2 * 1024 * 1024)  # 2MB > 字段上限，文件 part 合法
        counter.feed(b'--B\r\nContent-Disposition: form-data; name="file"; '
                     b'filename="a.csv"\r\n\r\n' + payload + b"\r\n--B--\r\n")
        self.assertEqual(counter.files, 1)
        self.assertEqual(counter.fields, 0)

    def test_r6s3_giveup_classification_counts_as_file_part(self):
        """R6-S3 复审 #44 复现：超观察窗的 part 头按「可能是文件 part」保守计数。

        旧行为：头块跨 chunk 超 4096B 即放弃分类且丢弃已观察字节——
        3 个 4.2KB 头部的文件 part 在 max=2 下 files=0/2 漏计。
        """
        # 4.2KB 头（python-multipart 单行上限 4224 内，合法形状）、100B 分块：
        # 观察窗 8192 内可正常完成分类——按文件 part 计数
        part = (b'--B\r\nContent-Disposition: form-data; name="files"; '
                b'filename="f.csv"\r\nX-Pad: ' + b"h" * 4100 + b"\r\n\r\ndata\r\n")
        data = part * 3 + b"\r\n--B--\r\n"
        counter = _MultipartPartCounter(b"B", 2)
        with self.assertRaises(MultipartStructureRejected):
            for i in range(0, len(data), 100):
                counter.feed(data[i:i + 100])
        # 8KB+ 仍未见空行的异常头（无法分类）：保守按文件 part 计数，
        # max=2 时第 3 个即拒绝（旧代码全部漏计放行）
        weird = b"--B\r\nX-Pad: " + b"z" * 9000  # 无 \r\n\r\n 终止
        counter = _MultipartPartCounter(b"B", 2)
        for i in range(0, len(weird), 512):
            counter.feed(weird[i:i + 512])
        self.assertEqual(counter.files, 1)

    def test_r6s3_boundary_parsed_like_starlette(self):
        """R6-S3 复审 #37 复现：boundary 解析与 Starlette 同源（python-multipart
        parse_options_header：重复参数取最后一个、引号去引号、值 strip）。

        旧提取器取**第一个** boundary：`boundary=abc; boundary=def` 且 body
        用 def 时计数器装错 key（21 个 part 全部落盘）；`boundary=";abc"`
        返回 None 计数器不装（fail-open）。
        """
        rules = [(re.compile(r"^/api/tasks/[^/]+/import-batch$"), 10 ** 9, 2)]
        seen = []

        async def stub_app(scope, receive, send):
            while True:
                message = await receive()
                if message["type"] != "http.request":
                    break
                seen.append(message.get("body", b""))
                if not message.get("more_body", False):
                    break
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})

        def file_part(i):
            return (f"--def\r\nContent-Disposition: form-data; name=\"files\"; "
                    f"filename=\"f{i}.csv\"\r\n\r\nc\r\n").encode()

        def run(content_type, chunks):
            body = b"".join(chunks)
            stream = iter(chunks)
            replies = []

            async def receive():
                try:
                    return {"type": "http.request", "body": next(stream),
                            "more_body": True}
                except StopIteration:
                    return {"type": "http.request", "body": b"", "more_body": False}

            async def send(message):  # pragma: no cover - 异常先于响应
                replies.append(message)

            middleware = UploadSizeLimitMiddleware(stub_app, rules)
            return asyncio.run(middleware(
                {"type": "http", "method": "POST",
                 "path": "/api/tasks/t/import-batch",
                 "headers": [(b"content-type", content_type),
                             (b"content-length", str(len(body)).encode())]},
                receive, send))

        # 重复 boundary：Starlette/本中间件都取最后一个（def）——3 个文件
        # part（max=2）必须在 receive 层被拒，而不是装错 key 后放行
        with self.assertRaises(MultipartStructureRejected):
            run(b"multipart/form-data; boundary=abc; boundary=def",
                [file_part(1) + file_part(2) + file_part(3) + b"\r\n--def--\r\n"])
        # 引号内含分号的 boundary：";abc" 整体有效（旧提取器 None → fail-open）
        quoted = (b"--;abc\r\nContent-Disposition: form-data; name=\"files\"; "
                  b"filename=\"f1.csv\"\r\n\r\nc\r\n"
                  b"--;abc\r\nContent-Disposition: form-data; name=\"files\"; "
                  b"filename=\"f2.csv\"\r\n\r\nc\r\n"
                  b"--;abc\r\nContent-Disposition: form-data; name=\"files\"; "
                  b"filename=\"f3.csv\"\r\n\r\nc\r\n\r\n--;abc--\r\n")
        with self.assertRaises(MultipartStructureRejected):
            run(b'multipart/form-data; boundary=";abc"', [quoted])

    def test_r6s3_first_boundary_recognized_across_chunks(self):
        """R6-S3 评审二轮实测绕过（handoff 原构造）：首边界跨 chunk。

        旧实现只在**首个 feed 块**以完整 ``--boundary`` 开头时识别首 part，
        之后只匹配带前导 CRLF 的 ``\\r\\n--boundary``——首块 '-'、boundary
        前缀或逐字节滴灌时第一个 part 永不被计数（38 字节 boundary、2 个
        文件 part、max=1 实测 files=1 放行；ASGI 层 1 字节首块同样放行
        2 个 filename part 到解析器）。
        """
        boundary = b"----WebKitFormBoundary" + b"X" * 17  # 38 字节真实形状
        lead = b"--" + boundary

        def file_part(i):
            return (lead + b'\r\nContent-Disposition: form-data; name="files"; '
                    b'filename="f' + str(i).encode("ascii")
                    + b'.csv"\r\n\r\nk,v\r\n')

        def field_part(name, value=b"v"):
            return (lead + b'\r\nContent-Disposition: form-data; name="'
                    + name.encode("ascii") + b'"\r\n\r\n' + value + b"\r\n")

        body = file_part(1) + file_part(2) + b"\r\n" + lead + b"--\r\n"

        def attempt(chunks):
            counter = _MultipartPartCounter(boundary, 1)
            try:
                for chunk in chunks:
                    counter.feed(chunk)
                return None, counter.files
            except MultipartStructureRejected as exc:
                return exc, counter.files

        # 对照：整包一次 feed → 拒
        exc, _ = attempt([body])
        self.assertIsNotNone(exc)
        # 评审实测的三种绕过构造：全部必须在计数层拒绝
        exc, files = attempt([b"-", body[1:]])
        self.assertIsNotNone(exc, "首块 '-' 不得放行第 2 个文件 part")
        exc, files = attempt([body[:10], body[10:]])
        self.assertIsNotNone(exc, "首块 10 字节 lead 前缀不得放行")
        exc, files = attempt([body[i:i + 1] for i in range(len(body))])
        self.assertIsNotNone(exc, "1 字节滴灌不得放行")
        # 首个 part 是字段：数量与大小同样从第 1 个 part 起计入
        fields17 = b"".join(field_part(f"f{i}") for i in range(17)) + b"\r\n" + lead + b"--\r\n"
        counter = _MultipartPartCounter(boundary, 2)
        with self.assertRaises(MultipartStructureRejected):
            for i in range(0, len(fields17), 3):  # 滴灌跨首边界
                counter.feed(fields17[i:i + 3])
        big_field = field_part("big", b"v" * (MAX_FIELD_PART_BYTES + 1)) + b"\r\n" + lead + b"--\r\n"
        counter = _MultipartPartCounter(boundary, 2)
        with self.assertRaises(MultipartStructureRejected):
            for i in range(0, len(big_field), 8192):
                counter.feed(big_field[i:i + 8192])
        # 正常单文件 + 前导 preamble：不误拒
        counter = _MultipartPartCounter(boundary, 1)
        counter.feed(b"preamble\r\n" + file_part(1) + b"\r\n" + lead + b"--\r\n")
        self.assertEqual(counter.files, 1)
        # 空 multipart（--boundary-- 直接结束）：零 part、不抛
        counter = _MultipartPartCounter(boundary, 1)
        counter.feed(lead + b"--\r\n")
        self.assertEqual((counter.files, counter.fields), (0, 0))

        # ASGI 中间件层复现：1 字节首块 receive —— 第 2 个文件 part 的头部
        # 进入解析器之前必须被拒（评审实测旧代码放行 2 个 filename part）
        rules = [(re.compile(r"^/api/tasks/[^/]+/import-batch$"), 10 ** 9, 1)]
        forwarded = []

        async def stub_app(scope, receive, send):
            while True:
                message = await receive()
                if message["type"] != "http.request":
                    break
                forwarded.append(message.get("body", b""))
                if not message.get("more_body", False):
                    break
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})

        stream = iter([body[:1]] + [body[i:i + 4096] for i in range(1, len(body), 4096)])

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
                 "headers": [(b"content-type", b"multipart/form-data; boundary=" + boundary),
                             (b"content-length", str(len(body)).encode())]},
                receive, send))
        # 评审实测旧代码放行 2 个 filename part；现在第 2 个文件 part 的
        # 头部到达解析器之前即中断（拒绝发生在含该头部的 chunk 转发前，
        # 转发量 ≤ 1 个 filename——此处 1 字节首块场景实际为 0）
        self.assertLess(b"".join(forwarded).count(b"filename"), 2,
                        "第 2 个文件 part 的头部不得再进入解析器")

    def test_r6s3_missing_or_empty_boundary_rejected_400(self):
        """R6-S3：boundary 缺失/空值 → 400（Starlette 解析期本就 400，提前到落盘前）。"""
        rules = [(re.compile(r"^/api/tasks/[^/]+/import$"), 10 ** 9, 1)]
        seen = []

        async def stub_app(scope, receive, send):  # pragma: no cover - 不应被调
            seen.append(scope["path"])
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})

        def run(content_type):
            replies = []

            async def receive():  # pragma: no cover
                raise AssertionError("request body must not be read")

            async def send(message):
                replies.append(message)

            middleware = UploadSizeLimitMiddleware(stub_app, rules)
            asyncio.run(middleware(
                {"type": "http", "method": "POST",
                 "path": "/api/tasks/t/import",
                 "headers": [(b"content-type", content_type),
                             (b"content-length", b"100")]},
                receive, send))
            return replies

        for bad in (b"multipart/form-data",                    # 无 boundary
                    b"multipart/form-data; boundary=",         # 空值
                    b'multipart/form-data; boundary=""',       # 空引号
                    b"multipart/form-data; charset=utf-8"):    # 有参数但无 boundary
            replies = run(bad)
            self.assertEqual(replies[0]["status"], 400, bad)
        self.assertEqual(seen, [])
        # 正常 boundary：放行进入内层
        self.assertEqual(run(b"multipart/form-data; boundary=ok")[0]["status"], 200)

    def test_r6s3_content_type_leading_ows_treated_as_multipart(self):
        """R6-S3 复审 #43：前导空白的 Content-Type 与 Starlette 同样按 multipart
        处理（parse_options_header 值 strip）；旧 startswith 判定在 ASGI 直调层
        完整绕过预检。"""
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
                 "path": "/api/tasks/t/import", "headers": headers},
                receive, send))
            return replies

        spaced = (b"content-type", b"  multipart/form-data; boundary=x")
        # 无 Content-Length 的（带前导空白的）multipart：411 拒绝（旧代码放行）
        self.assertEqual(run([spaced])[0]["status"], 411)
        # 超 CL：413
        self.assertEqual(run([spaced, (b"content-length", b"101")])[0]["status"], 413)
        self.assertEqual(seen, [])

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
    # U5-P5/R6-S1：限流按直连 peer IP 分桶（回退修复前语义），XFF 一律忽略
    # ------------------------------------------------------------------
    def test_u5p5_r6s1_rate_limit_key_ignores_forwarded_for(self):
        """XFF 不参与分桶：同对端轮换 XFF 拿不到独立配额（绕过不可复现）。"""
        self.assertEqual(rate_limit_key("login", "1.2.3.4", None), "login:1.2.3.4")
        self.assertEqual(rate_limit_key("login", "1.2.3.4", "5.6.7.8"), "login:1.2.3.4")
        self.assertEqual(rate_limit_key("login", "1.2.3.4", "9.9.9.9"), "login:1.2.3.4")
        self.assertEqual(rate_limit_key("login", None, None), "login:?")
        self.assertEqual(rate_limit_key("login", None, "1.2.3.4"), "login:?")
        # 不同对端互相独立（反代场景应用层退化为共享桶 → 由反代 limit_req 兜底）
        from custom_backend.security import RateLimiter
        limiter = RateLimiter(max_events=5, window_seconds=60.0)
        self.assertTrue(all(limiter.check("login:203.0.113.1", now=100.0)[0]
                            for _ in range(5)))
        allowed, _ = limiter.check("login:203.0.113.1", now=100.5)
        self.assertFalse(allowed)          # 同对端第 6 次：拒绝
        self.assertTrue(limiter.check("login:198.51.100.7", now=100.5)[0])  # 其他对端不受影响
        # launcher 保持 proxy_headers=False（uvicorn 不用可伪造 XFF 改写对端）
        launcher_src = (Path(__file__).resolve().parents[2]
                        / "build" / "launcher.py").read_text(encoding="utf-8")
        self.assertIn("proxy_headers=False", launcher_src,
                      "uvicorn Config 必须显式关闭 proxy_headers（XFF 伪造防护）")

    def test_r6s1_rotating_xff_cannot_bypass_and_no_global_bucket(self):
        """R6-S1 复现回放：同 peer 轮换 XFF 5 次错误后第 6 次（正确密码）仍被
        拒——轮换绕过不可复现；且不再存在「带 XFF 即全局共享桶」的退化
        （r5 的 xff-untrusted 单桶曾让无关请求互相挤占）。
        """
        from custom_backend import auth as auth_mod
        from custom_backend.security import login_limiter
        username = "ratelimit-probe"
        auth_mod.create_user(username, "Correct-Horse-9")
        headers = {"Content-Type": "application/json"}
        # 5 次错误密码，每次换一个 XFF（复审 #39/#42 手法）
        for i in range(5):
            r = self.client.post(
                "/api/auth/login",
                json={"username": username, "password": "WrongPass-8chars"},
                headers={**headers, "X-Forwarded-For": f"203.0.113.{i}"})
            self.assertEqual(r.status_code, 401, r.text)
        # 第 6 次：同对端（TestClient peer 固定）正确密码——按对端桶 429
        # （修复前语义：同 IP 5 次/分钟），轮换 XFF 拿不到第 6 次机会
        r = self.client.post(
            "/api/auth/login",
            json={"username": username, "password": "Correct-Horse-9"},
            headers={**headers, "X-Forwarded-For": "203.0.113.99"})
        self.assertEqual(r.status_code, 429, r.text)
        self.assertIn("按连接来源 IP", r.json()["detail"])  # 429 说明计数维度
        self.assertTrue(r.headers.get("retry-after"))
        # 关键回归：r5 的 xff-untrusted 全局桶不复存在——无 XFF 的请求
        # 不与「带 XFF 的请求」共享桶（两者同属对端桶但登记在先的错误已
        # 计满，此处验证的是桶 key 不再因 XFF 存在而改变）
        self.assertEqual(rate_limit_key("login", "testclient", "1.2.3.4"),
                         rate_limit_key("login", "testclient", None))
        login_limiter._events.clear()  # 不污染同进程其他用例的对端桶

    # ------------------------------------------------------------------
    # R6-S5：中间件早拒响应（411/401/413/400/409）必须带安全头，跨域白名单
    # 内的 Origin 补 CORS 简单响应头（否则浏览器把上传错误吞成网络错误）
    # ------------------------------------------------------------------
    def test_r6s5_early_rejects_carry_security_and_cors_headers(self):
        origin = "http://localhost:8614"  # 默认 CORS 白名单（_cors_origins）
        # 401：未认证 multipart（解析/落盘前）
        r = self.client.post(
            f"/api/tasks/{self.task}/import",
            files={"file": ("a.csv", b"k\n", "text/csv")},
            headers={"Origin": origin})
        self.assertEqual(r.status_code, 401, r.text)
        self.assertEqual(r.headers.get("x-content-type-options"), "nosniff")
        self.assertTrue(r.headers.get("content-security-policy"))
        self.assertEqual(r.headers.get("access-control-allow-origin"), origin)
        self.assertEqual(r.headers.get("access-control-allow-credentials"), "true")
        # 413：超 Content-Length 预检（已认证 + 合法 boundary）
        huge = 101 * 1024 * 1024
        r = self.client.post(
            f"/api/tasks/{self.task}/import",
            content=b"--x\r\nContent-Disposition: form-data; name=\"file\"; "
                    b"filename=\"a.csv\"\r\n\r\n" + b"a" * huge + b"\r\n--x--\r\n",
            headers={"Authorization": self.auth()["Authorization"],
                     "Content-Type": "multipart/form-data; boundary=x",
                     "Content-Length": str(huge + 128),
                     "Origin": origin})
        self.assertEqual(r.status_code, 413, r.text)
        self.assertEqual(r.headers.get("x-content-type-options"), "nosniff")
        self.assertEqual(r.headers.get("access-control-allow-origin"), origin)
        # 白名单外 Origin：安全头仍全，但不回 CORS 头
        r = self.client.post(
            f"/api/tasks/{self.task}/import",
            files={"file": ("a.csv", b"k\n", "text/csv")},
            headers={"Origin": "http://evil.example"})
        self.assertEqual(r.status_code, 401, r.text)
        self.assertEqual(r.headers.get("x-content-type-options"), "nosniff")
        self.assertIsNone(r.headers.get("access-control-allow-origin"))
        # CORS 预检（OPTIONS）照常由内层 CORSMiddleware 应答且带头（不回归）
        r = self.client.options(
            f"/api/tasks/{self.task}/import",
            headers={"Origin": origin,
                     "Access-Control-Request-Method": "POST",
                     "Access-Control-Request-Headers": "authorization,content-type"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.headers.get("access-control-allow-origin"), origin)
        self.assertIn("POST", r.headers.get("access-control-allow-methods", ""))
        self.assertEqual(r.headers.get("x-content-type-options"), "nosniff")

    def test_r6s5_tasklock_409_carries_security_and_cors_headers(self):
        """TaskLock 的 409 早拒（另一进程占用/删除中）同样带安全头 + CORS。

        锁文件写入真实存活的子进程 PID（Windows 上 os.kill 对不存在的 PID
        抛 WinError 87 而非 ProcessLookupError，伪造大号 PID 会让探测路径
        直接 500——那本身是 coscreen.pid_alive 的既有缺口，此处用真实子进程
        绕开，只验证中间件 409 的响应头契约）。
        """
        import subprocess
        origin = "http://localhost:8614"
        holder = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"])
        try:
            self.assertIsNone(holder.poll())  # 持锁进程存活
            lock_path = self.info.dir / f"{self.info.task_id}.lock"
            lock_path.write_text(f"{holder.pid} 2026-01-01T00:00:00",
                                 encoding="utf-8")
            r = self.client.get(f"/api/tasks/{self.task}/articles",
                                headers={**self.auth(), "Origin": origin})
            self.assertEqual(r.status_code, 409, r.text)
            self.assertEqual(r.headers.get("x-content-type-options"), "nosniff")
            self.assertTrue(r.headers.get("content-security-policy"))
            self.assertEqual(r.headers.get("access-control-allow-origin"), origin)
        finally:
            lock_path.unlink(missing_ok=True)
            holder.kill()
            holder.wait(timeout=10)

    # ------------------------------------------------------------------
    # R6-S4：出货前端换票流（静态守护——浏览器行为不可在 pytest 内驱动）
    # ------------------------------------------------------------------
    def test_r6s4_frontend_uses_ticket_flow_not_token_query(self):
        frontend = (Path(__file__).resolve().parents[2]
                    / "custom_frontend" / "index.html").read_text(encoding="utf-8")
        # 24h JWT 不得再拼进 EventSource URL（U5-P12 的原始风险路径）
        self.assertNotIn("/api/events?token=", frontend)
        self.assertNotIn("events?token=' + encodeURIComponent", frontend)
        # 先 POST 换票、再以一次性票据连流
        self.assertIn("api('/api/events/ticket'", frontend)
        self.assertIn("/api/events?ticket=' + encodeURIComponent(ticket)", frontend)
        # 票据一次性：断线后不得沿用旧 URL 自动重连，必须整链换新票
        self.assertIn("source.onerror", frontend)

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
