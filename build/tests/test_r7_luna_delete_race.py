"""r7-lunafix 复审阻断项钉子：可见性预检读必须计入 DELETE 的读排空。

复审阻断发现（r8）：L-01 的取锁前可见性预检在 ``_deleting`` 判定与读排空
计数之外打开 collaboration.db——并发 GET 的预检句柄可跨 ``shutil.move``
（Windows WinError 32 → copytree+rmtree 半删/500；复审探针 4/200 复现，
对 5cb6314 基线 0/180）。修复语义：

- 预检读**之前**与「删除中」判定同临界区登记 ``_precheck_readers`` 在途
  计数（删除中直接 409、不再读协作库）；
- ``delete_task`` 端点的读排空同时等空 ``precheck_readers``。

钉子（把修复删掉必须红）：桩住 ``TaskLockMiddleware._visible_to_payload``
让首个 GET 的预检可控挂起——预检在途期间 DELETE **不得完成**（未计入
读排空时立即 200 = 红）；释放后 DELETE 必须干净 200 且任务目录移入
.trash。桩只阻塞首个调用（GET），DELETE 自身的预检照常放行，确保检的是
端点排空而不是 DELETE 自己的预检。
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


if __name__ == "__main__":
    unittest.main()
