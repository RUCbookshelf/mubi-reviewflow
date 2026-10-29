"""r7-lunafix 复审阻断项钉子：可见性预检读必须计入 DELETE 的读排空。

复审阻断发现（r8）：L-01 的取锁前可见性预检在 ``_deleting`` 判定与读排空
计数之外打开 collaboration.db——并发 GET 的预检句柄可跨 ``shutil.move``
（Windows WinError 32 → copytree+rmtree 半删/500；复审探针 4/200，对
5cb6314 基线 0/180）。修复语义：

- 预检读**之前**与「删除中」判定同临界区登记 ``_precheck_readers`` 在途
  计数（删除中直接 409、不再读协作库）；
- ``delete_task`` 端点的读排空同时等空本任务（含 URL 别名族）的预检计数。

r9 复审阻断追加钉子：

- **不可见请求的下游重读**（require_user 的 _task_or_404/_task_visible_to
  重开 task.json/collaboration.db）必须同样在计数窗口内（r8 首版在放行前
  注销 → 16 并发非参与者探针 3/300 DELETE 500）；
- **计数异常安全**：预检段抛异常/协程取消不得残留计数（r8 首版无
  try/finally → RuntimeError 注入后 any_precheck_readers()==1 永久残留，
  该进程后续所有 DELETE 等满 5s 回 409）。
"""
import sys
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
fixture = tempfile.TemporaryDirectory(prefix="rf_luna_delrace_")
import os  # noqa: E402
os.environ["COBOOKSHELF_DATA_DIR"] = fixture.name

from fastapi.testclient import TestClient  # noqa: E402

from custom_backend import auth as auth_mod  # noqa: E402
from custom_backend import main as backend_main  # noqa: E402
from custom_backend.main import TaskLockMiddleware, app  # noqa: E402
from coscreen import tasks as tasks_mod  # noqa: E402


class LunaDeleteRacePrecheckTests(unittest.TestCase):

    def _make_task(self, client, auth):
        import uuid as uuid_mod
        created = client.post("/api/tasks", headers=auth, json={
            "name": f"LunaDelRace-{uuid_mod.uuid4().hex[:6]}",
            "description": ""})
        self.assertEqual(created.status_code, 201, created.text)
        return created.json()["task_id"]

    @staticmethod
    def _make_user():
        username = f"lunarace{uuid.uuid4().hex[:8]}"
        user = auth_mod.create_user(username, "LunaRace-Passw0rd")
        return user, {"Authorization":
                      f"Bearer {auth_mod.create_access_token(user['id'], user['username'])}"}

    def test_precheck_read_counted_in_delete_drain(self):
        client = TestClient(app)
        # 直建用户+令牌：不走 /api/auth/register（避免与其他模块共享限流桶）
        username = f"lunarace{uuid.uuid4().hex[:8]}"
        user = auth_mod.create_user(username, "LunaRace-Passw0rd")
        token = auth_mod.create_access_token(user["id"], user["username"])
        auth = {"Authorization": f"Bearer {token}"}
        name = f"LunaDelRace-{uuid.uuid4().hex[:6]}"
        created = client.post("/api/tasks", headers=auth,
                              json={"name": name, "description": ""})
        self.assertEqual(created.status_code, 201, created.text)
        task_id = created.json()["task_id"]

        real_visible = TaskLockMiddleware._visible_to_payload
        entered = threading.Event()
        release = threading.Event()
        first_call = {"blocked": False}

        def slow_visible_first_get(info, payload):  # noqa: ANN001
            """只阻塞首个调用（GET 的预检）；DELETE 自身预检直接放行。"""
            if not first_call["blocked"]:
                first_call["blocked"] = True
                entered.set()
                release.wait(timeout=10)
            return real_visible(info, payload)

        results: dict = {}

        def do_get():
            try:
                c = TestClient(app)
                results["get"] = c.get(
                    f"/api/tasks/{task_id}/decisions", headers=auth).status_code
            except Exception as exc:  # noqa: BLE001
                results["get_exc"] = repr(exc)

        def do_delete():
            try:
                c = TestClient(app)
                results["delete"] = c.delete(
                    f"/api/tasks/{task_id}", headers=auth).status_code
            except Exception as exc:  # noqa: BLE001
                results["delete_exc"] = repr(exc)

        with mock.patch.object(TaskLockMiddleware, "_visible_to_payload",
                               staticmethod(slow_visible_first_get)):
            t_get = threading.Thread(target=do_get, daemon=True)
            t_get.start()
            # 等首个预检真正在途（修复后可观测计数；修前无计数器则靠 entered）
            self.assertTrue(entered.wait(timeout=5), "GET 的可见性预检未执行")
            if hasattr(TaskLockMiddleware, "_precheck_readers"):
                deadline = time.monotonic() + 2
                while (TaskLockMiddleware.precheck_readers(task_id) < 1
                       and time.monotonic() < deadline):
                    time.sleep(0.02)
                self.assertGreaterEqual(
                    TaskLockMiddleware.precheck_readers(task_id), 1,
                    "预检在途时必须登记 _precheck_readers（DELETE 排空依赖）")
            t_del = threading.Thread(target=do_delete, daemon=True)
            t_del.start()
            time.sleep(0.6)
            # 钉子①：预检读在途时 DELETE 不得完成（未等空即 move = 半删窗口）
            self.assertNotIn(
                "delete", results,
                "DELETE 在可见性预检读在途时即返回（预检未计入读排空 → "
                "shutil.move 可跨预检的 collaboration.db 句柄，Windows 半删）")
            self.assertNotIn("delete_exc", results, results)
            release.set()
            t_del.join(timeout=15)
            t_get.join(timeout=15)
        # 钉子②：预检排空后删除干净收尾（200 + 任务目录真正移走）
        self.assertEqual(results.get("delete"), 200, results)
        self.assertIsNone(tasks_mod.find_task(task_id, backend_main.DATA_DIR),
                          "删除 200 后任务目录仍存在（半删）")

    def test_invisible_downstream_reread_counted_in_delete_drain(self):
        """r9 阻断：不可见请求的**下游重读**（require_user 的
        _task_or_404/_task_visible_to 重开 task.json/collaboration.db）必须
        在预检计数窗口内——r8 首版放行前注销，16 并发非参与者探针 3/300
        DELETE 500（WinError 32 on collaboration.db）。"""
        client = TestClient(app)
        owner, owner_auth = self._make_user()
        outsider, outsider_auth = self._make_user()
        task_id = self._make_task(client, owner_auth)

        real_visible_to = backend_main._task_visible_to
        entered = threading.Event()
        release = threading.Event()
        calls = {"n": 0}
        calls_lock = threading.Lock()

        def visible_to_blocking_second(info, user):  # noqa: ANN001
            """第 1 次=GET 预检（直通）；第 2 次=GET 下游 require_user（挂起）。

            后续调用（DELETE 自身的预检/鉴权）一律直通——检的是不可见请求
            下游的排空语义。
            """
            with calls_lock:
                calls["n"] += 1
                nth = calls["n"]
            if nth == 2:
                entered.set()
                release.wait(timeout=10)
            return real_visible_to(info, user)

        results: dict = {}

        def do_outsider_get():
            try:
                c = TestClient(app)
                results["get"] = c.get(
                    f"/api/tasks/{task_id}/decisions",
                    headers=outsider_auth).status_code
            except Exception as exc:  # noqa: BLE001
                results["get_exc"] = repr(exc)

        def do_delete():
            try:
                c = TestClient(app)
                results["delete"] = c.delete(
                    f"/api/tasks/{task_id}", headers=owner_auth).status_code
            except Exception as exc:  # noqa: BLE001
                results["delete_exc"] = repr(exc)

        with mock.patch.object(backend_main, "_task_visible_to",
                               visible_to_blocking_second):
            t_get = threading.Thread(target=do_outsider_get, daemon=True)
            t_get.start()
            # 等不可见 GET 的下游鉴权读真正挂起（第 2 次 _task_visible_to）
            self.assertTrue(entered.wait(timeout=5),
                            "不可见 GET 的下游 _task_visible_to 未执行")
            self.assertGreaterEqual(
                TaskLockMiddleware.precheck_readers(task_id), 1,
                "不可见请求下游在途时必须保持 _precheck_readers 计数")
            t_del = threading.Thread(target=do_delete, daemon=True)
            t_del.start()
            time.sleep(0.6)
            # 钉子①：不可见请求下游重读在途时 DELETE 不得完成
            self.assertNotIn(
                "delete", results,
                "DELETE 在不可见请求的下游任务文件重读在途时即返回"
                "（下游读未计入读排空 → shutil.move 可跨句柄，Windows 半删）")
            self.assertNotIn("delete_exc", results, results)
            release.set()
            t_del.join(timeout=15)
            t_get.join(timeout=15)
        # 钉子②：排空后干净收尾：GET 403（非参与者口径不变）、DELETE 200、
        # 任务目录真正移走、计数归零
        self.assertEqual(results.get("get"), 403, results)
        self.assertEqual(results.get("delete"), 200, results)
        self.assertIsNone(tasks_mod.find_task(task_id, backend_main.DATA_DIR))
        self.assertEqual(TaskLockMiddleware.any_precheck_readers(), 0)

    def test_precheck_count_released_on_exception(self):
        """r9 阻断：预检段抛异常不得残留 _precheck_readers——r8 首版无
        try/finally，RuntimeError 注入后计数永久残留（any_precheck_readers
        ==1），该进程后续所有 DELETE 等满 5s 回 409。"""
        client = TestClient(app)
        owner, owner_auth = self._make_user()
        task_id = self._make_task(client, owner_auth)

        def exploding_visible(info, payload):  # noqa: ANN001
            raise RuntimeError("injected precheck failure")

        with mock.patch.object(TaskLockMiddleware, "_visible_to_payload",
                               staticmethod(exploding_visible)):
            # GET 因预检异常 500（不得把异常吞成别的口径）
            resp = TestClient(app, raise_server_exceptions=False).get(
                f"/api/tasks/{task_id}/decisions", headers=owner_auth)
            self.assertEqual(resp.status_code, 500)
        # 钉子：异常后计数必须归零（r8 首版残留 1 → 红）
        self.assertEqual(
            TaskLockMiddleware.any_precheck_readers(), 0,
            "预检异常后 _precheck_readers 残留（后续 DELETE 将永久等满 5s 409）")
        # 钉子：同进程后续 DELETE 不被残留计数拖进排空超时（快速 200）
        started = time.monotonic()
        deleted = client.delete(f"/api/tasks/{task_id}", headers=owner_auth)
        elapsed = time.monotonic() - started
        self.assertEqual(deleted.status_code, 200, deleted.text)
        self.assertLess(elapsed, 3.0,
                        f"DELETE 被拖满排空等待（{elapsed:.2f}s）——计数泄漏")
        self.assertIsNone(tasks_mod.find_task(task_id, backend_main.DATA_DIR))


if __name__ == "__main__":
    unittest.main()
