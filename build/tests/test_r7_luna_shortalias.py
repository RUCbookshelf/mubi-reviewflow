"""r10 钉子：Windows 8.3 短名别名不得绕过 DELETE 的预检读排空/删除互斥。

r9 评审实测阻断：API 真实接受 8.3 短名 URL——``GET /api/tasks/AVERYL~1/
decisions`` 属主 200/非参与者 403（``find_task`` 经 Win32 路径解析命中同一
长名任务目录）。而预检计数与「删除中」匹配只做尾点/空格＋大小写归一：

- 短名 URL 的预检计数以**短名**为键；``delete_task`` 的读排空按**规范长名**
  匹配 → 排空看不见短名键 → ``shutil.move`` 期间下游 require_user 重开
  task.json/collaboration.db → 每任务 12 并发非参与者短名 GET 探针
  10/10 DELETE 500（WinError 32 半删；基线 5cb6314 为 0/10）；
- 删除进行中（``_deleting``）的短名 GET 同样匹配不上 → 本应 409 早拒的
  请求穿透到下游。

修复语义（钉子即红点）：登记与匹配前把 URL task_id 规范为长目录名
（``TaskLockMiddleware._url_task_key``：``Path(tasks_root/{id}).resolve()
.name``，目录不存在/解析逃逸时原样保留——幽灵任务基线放行语义不变）。

- 钉子①：非参与者短名 GET 的**下游重读挂起**期间（沿用 r9 钉子的
  ``_task_visible_to`` 桩手法），DELETE 不得完成——计数必须以规范长名
  登记、被 ``precheck_readers_for`` 看见；
- 钉子②：挂起释放后干净收尾：GET 403（口径不变）、DELETE 200 无 500、
  任务目录移净（.trash 有全量副本）、计数归零；
- 钉子③：删除进行中，短名 GET（含属主/非参与者）一律 409 早拒，不触
  任务文件；
- 钉子④：幽灵短名（无对应目录）与 ``..`` 上跳的键规范**原样保留**，
  幽灵请求计数不残留（基线放行语义）。

卷未启用 8.3（``GetShortPathNameW`` 返回原名，短名==长名）时 skipif。
破坏即红：撤掉 ``_url_task_key`` 规范化（登记/匹配回退裸 URL task_id），
钉子①②③必失败。
"""
import ctypes
import sys
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
fixture = tempfile.TemporaryDirectory(prefix="rf_luna_shortalias_")
import os  # noqa: E402
os.environ["COBOOKSHELF_DATA_DIR"] = fixture.name

from fastapi.testclient import TestClient  # noqa: E402

from custom_backend import auth as auth_mod  # noqa: E402
from custom_backend import main as backend_main  # noqa: E402
from custom_backend.main import TaskLockMiddleware, app  # noqa: E402
from coscreen import tasks as tasks_mod  # noqa: E402


def _short_name_of(path: Path) -> str:
    """GetShortPathNameW 取目录的 8.3 短名（卷未启用 8.3 时返回原名）。"""
    buf = ctypes.create_unicode_buffer(1024)
    n = ctypes.windll.kernel32.GetShortPathNameW(str(path), buf, 1024)
    return Path(buf.value).name if n else path.name


@unittest.skipIf(os.name != "nt", "8.3 短名别名是 Win32 路径解析行为")
class LunaShortAliasDeleteDrainTests(unittest.TestCase):

    def _make_user(self):
        username = f"lunashort{uuid.uuid4().hex[:8]}"
        user = auth_mod.create_user(username, "LunaShort-Passw0rd")
        return user, {"Authorization":
                      f"Bearer {auth_mod.create_access_token(user['id'], user['username'])}"}

    def _make_task(self, client, auth):
        created = client.post("/api/tasks", headers=auth, json={
            "name": f"AveryLongTaskName-{uuid.uuid4().hex[:6]}",
            "description": ""})
        self.assertEqual(created.status_code, 201, created.text)
        return created.json()["task_id"]

    def _short_alias(self, task_id: str) -> str:
        """任务目录的 8.3 短名；卷未启用 8.3（短名==长名）时 skip 本用例。"""
        alias = _short_name_of(tasks_mod.tasks_root(backend_main.DATA_DIR) / task_id)
        if alias == task_id:
            self.skipTest(
                f"卷未启用 8.3 短名（{backend_main.DATA_DIR} 所在卷），"
                "短名别名绕过场景不适用")
        self.assertIn("~", alias, f"短名缺少 ~1 代号：{alias!r}")
        return alias

    def test_short_alias_get_drained_by_delete(self):
        """钉子①②：非参与者短名 GET 下游重读挂起期间 DELETE 不得完成；
        释放后 GET 403、DELETE 200 无 500、目录移净、计数归零。"""
        client = TestClient(app)
        owner, owner_auth = self._make_user()
        outsider, outsider_auth = self._make_user()
        task_id = self._make_task(client, owner_auth)
        alias = self._short_alias(task_id)
        # 前置事实（r9 评审同款观测）：API 真实接受短名 URL——非参与者 403
        baseline = client.get(f"/api/tasks/{alias}/decisions",
                              headers=outsider_auth)
        self.assertEqual(baseline.status_code, 403, baseline.text)
        self.assertEqual(TaskLockMiddleware.any_precheck_readers(), 0)

        real_visible_to = backend_main._task_visible_to
        entered = threading.Event()
        release = threading.Event()
        calls = {"n": 0}
        calls_lock = threading.Lock()

        def visible_to_blocking_second(info, user):  # noqa: ANN001
            """第 1 次=短名 GET 预检（直通）；第 2 次=GET 下游 require_user
            （挂起，模拟下游重开 task.json/collaboration.db 的慢读）；后续
            （DELETE 自身预检/鉴权）一律直通。"""
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
                    f"/api/tasks/{alias}/decisions",
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
            # 等短名 GET 的下游鉴权读真正挂起（第 2 次 _task_visible_to）
            self.assertTrue(entered.wait(timeout=5),
                            "短名 GET 的下游 _task_visible_to 未执行")
            # 钉子①前置：短名 URL 的预检计数必须以**规范长名**登记
            #（修前登记在短名键下——precheck_readers(canonical)==0）
            self.assertGreaterEqual(
                TaskLockMiddleware.precheck_readers(task_id), 1,
                "短名 URL 的预检计数未落到规范长名键（DELETE 排空看不见）")
            self.assertGreaterEqual(
                TaskLockMiddleware.precheck_readers_for(task_id), 1,
                "短名 URL 的预检计数未被 precheck_readers_for(规范名) 计入")
            t_del = threading.Thread(target=do_delete, daemon=True)
            t_del.start()
            time.sleep(0.6)
            # 钉子①：不可见短名请求的下游重读在途时 DELETE 不得完成
            #（修前：短名键不被排空看见 → move 跨句柄 → WinError 32 半删）
            self.assertNotIn(
                "delete", results,
                "DELETE 在短名 GET 的下游任务文件重读在途时即返回"
                "（短名键未被读排空看见 → shutil.move 可跨句柄，"
                "Windows 半删/500）")
            self.assertNotIn("delete_exc", results, results)
            release.set()
            t_del.join(timeout=15)
            t_get.join(timeout=15)
        # 钉子②：排空后干净收尾：GET 403（非参与者口径不变）、DELETE 200
        # 无 500、任务目录真正移净（.trash 全量副本）、计数归零
        self.assertEqual(results.get("get"), 403, results)
        self.assertEqual(results.get("delete"), 200, results)
        self.assertIsNone(tasks_mod.find_task(task_id, backend_main.DATA_DIR))
        self.assertFalse((tasks_mod.tasks_root(backend_main.DATA_DIR) / task_id).exists(),
                         "删除 200 后任务目录仍在 tasks 根（半删）")
        self.assertEqual(TaskLockMiddleware.any_precheck_readers(), 0)

    def test_short_alias_get_409_while_deleting(self):
        """钉子③：删除进行中（_deleting=规范长名），短名 GET 一律 409 早拒
        且**不触达 find_task**（不读任何任务文件）——修前短名匹配不上
        _deleting，穿透到 find_task/可见性预检（触碰任务目录内文件）。"""
        client = TestClient(app)
        owner, owner_auth = self._make_user()
        outsider, outsider_auth = self._make_user()
        task_id = self._make_task(client, owner_auth)
        alias = self._short_alias(task_id)

        real_find = tasks_mod.find_task
        finds = {"n": 0}

        def counting_find(tid, data_dir):  # noqa: ANN001
            finds["n"] += 1
            return real_find(tid, data_dir)

        TaskLockMiddleware._begin_delete(task_id)
        try:
            with mock.patch.object(tasks_mod, "find_task", counting_find):
                for auth_headers in (owner_auth, outsider_auth):
                    resp = client.get(f"/api/tasks/{alias}/decisions",
                                      headers=auth_headers)
                    self.assertEqual(
                        resp.status_code, 409,
                        f"删除进行中的短名 GET 必须 409 早拒（实测 {resp.status_code}："
                        "短名未匹配 _deleting → 穿透到下游触碰任务文件）")
                    self.assertIn("删除", resp.text)
        finally:
            TaskLockMiddleware._end_delete(task_id)
        # 钉子③核心：409 早拒发生在 find_task 之前——deleting 命中的请求
        # 不得读任何任务文件（修前 deleting_hit 匹配不上短名 → find_task
        # 被触达，任务目录内 task.json 在删除窗口被打开）
        self.assertEqual(
            finds["n"], 0,
            "删除进行中的短名 GET 触达了 find_task（deleting 早拒未命中短名）")
        # 409 早拒路径不登记任何计数；标志清除后恢复常态
        self.assertEqual(TaskLockMiddleware.any_precheck_readers(), 0)
        self.assertEqual(
            client.get(f"/api/tasks/{alias}/decisions",
                       headers=owner_auth).status_code, 200)

    def test_url_task_key_ghost_and_escape_unchanged(self):
        """钉子④：幽灵短名（无目录）与 ``..`` 上跳的键规范**原样保留**——
        幽灵任务保持基线放行语义，不把任务根之外的目录名登记进计数表。"""
        task_id = self._make_task(TestClient(app), self._make_user()[1])
        key_fn = TaskLockMiddleware._url_task_key
        ghost = f"Ghost{uuid.uuid4().hex[:4].upper()}~1"
        self.assertEqual(key_fn(ghost), ghost,
                         "幽灵短名必须原样保留（不虚构不存在的目录名）")
        self.assertEqual(key_fn(".."), "..", "字面上跳必须原样保留")
        self.assertEqual(key_fn("a\\..\\..\\x"), "a\\..\\..\\x",
                         "分隔符上跳必须原样保留")
        # 真实短名/大小写/尾点别名折叠到规范长名（在磁盘上的真实名字）
        canonical = tasks_mod.find_task(task_id, backend_main.DATA_DIR).task_id
        self.assertEqual(key_fn(self._short_alias(task_id)), canonical)
        self.assertEqual(key_fn(task_id.upper()), canonical)
        # 幽灵短名 GET 放行语义：404、计数不残留
        resp = TestClient(app).get(f"/api/tasks/{ghost}/decisions",
                                   headers=self._make_user()[1])
        self.assertEqual(resp.status_code, 404, resp.text)
        self.assertEqual(TaskLockMiddleware.any_precheck_readers(), 0)


if __name__ == "__main__":
    unittest.main()
