"""2.1.5 任务删除策略（用户 2026-11-10 裁定）：彻底删除 + 回收站保留期。

钉契约 = out/release_plan/drafts/frontend_contract_2.1.5-task-delete.md：

F1 purge：
  ① 普通删除仍进 .trash（既有行为不变：响应含 trash 名，目录整移入隔离区）；
  ② purge 缺/错 confirm → 400 结构化（tasks.purge_confirm_required /
     tasks.purge_confirm_mismatch，params 带期望格式），任务**逐文件**原样在位、
     .trash 无新条目；
  ③ 正确 confirm → 任务目录彻底消失、.trash 无新条目、users.db（users 表行数
     + 旧令牌仍可用）与其他任务不受影响，响应 {deleted:true, purged:true}；
  ④ 非属主 purge → 403（属主闸门先于 confirm 校验——错误 confirm 也不泄露）；
  ⑤ 在途写时 purge → 409，任务保留（与 U5-P2 同闸：purge 不削弱排空纪律）。

F2 sweep：
  ⑥ 触点清扫：GET /api/tasks / POST /api/tasks 触发后 31 天前条目消失、
     29 天前仍在；TTL=0 禁用模式不清；坏目录（rmtree 失败）不阻断触点也不
     阻断其他条目清扫（单目录失败计数继续）；
  ⑦ TTL 边界（恰好 30 天 = 已满保留期 → 清除；差 1 秒未满 → 保留）；
     TTL 环境变量解析（缺省 30 / 整数 / 非整数回退 30 / <1 禁用）；
  ⑧ 两种删除自身也是触点（删除成功后过期条目被清）。

评审二轮追加（r2）：
  ⑨ purge_busy：任务目录内有打开句柄 → 原子 rename 失败 → 409
     tasks.purge_busy，任务逐文件原样、无暂存残骸，句柄释放后同路径成功；
  ⑩ purge_partial：rmtree 未竟 → 409 tasks.purge_partial 如实报暂存条目
     名与剩余数（暂存区在 tasks 根之外）；只读文件经清位重试后 purge 200
     全量清除、零残骸；
  ⑪ 评审 P2（不可逆错删目标）：task.json 的 task_id 与目录名不一致（手工
     篡改/备份还原）时——端点层 server 模式被 legacy 属主闸 409 拦下（双
     保险）；coscreen 层 purge_task 的删除目标必须是属主校验命中的目录
     （info.dir），绝不得按 json 内 id 合成目标错删另一任务。

运行：python -m pytest build/tests/test_2_1_5_task_delete.py -q
（定向；R6-S2/U5 既有套件另见 test_upload_security_regression.py /
 test_r7_luna_delete_race.py，按任务书入跑。）
"""

import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
fixture = tempfile.TemporaryDirectory(prefix="rf_task_del_")
os.environ["COBOOKSHELF_DATA_DIR"] = fixture.name
os.environ["REVIEWFLOW_MODE"] = "server"

from fastapi.testclient import TestClient  # noqa: E402

from custom_backend import main as backend_main  # noqa: E402
from custom_backend.main import TaskLockMiddleware, app  # noqa: E402
from coscreen import tasks as tasks_mod  # noqa: E402


def _trash_names() -> set:
    """当前 .trash 下的条目名快照（目录+文件；无 .trash 时空集）。"""
    root = backend_main.DATA_DIR / ".trash"
    if not root.is_dir():
        return set()
    return {p.name for p in root.iterdir()}


def _inventory(directory: Path) -> dict:
    """目录逐文件清单：相对路径 -> 字节大小（钉「逐文件原样在位」）。"""
    out = {}
    for p in sorted(directory.rglob("*")):
        if p.is_file():
            out[str(p.relative_to(directory)).replace("\\", "/")] = p.stat().st_size
    return out


def _users_count() -> int:
    """users.db 的 users 表行数（读连接；purge 前后必须不变）。

    注意取 auth 运行时口径的库路径（auth.data_dir() 动态读环境变量；合并
    跑多套件时后导入套件会改写 env，backend_main.DATA_DIR 是首导入时冻结
    的 Path，两者可能不同——与建用户同源才比得对）。
    """
    import sqlite3

    from custom_backend import auth as _auth

    path = Path(_auth._users_db())  # noqa: SLF001  测试直取，与 create_user 同源
    assert path.exists(), f"users.db 应存在（setUpClass 已建用户）：{path}"
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    finally:
        conn.close()


def _fabricate_trash_entry(ts: datetime, tag: str) -> Path:
    """在 .trash 下手工构造 {ts}_{tag}/ 条目（内含一个占位文件）。"""
    root = backend_main.DATA_DIR / ".trash"
    entry = root / f"{ts.strftime('%Y-%m-%dT%H%M%S')}_{tag}"
    entry.mkdir(parents=True, exist_ok=True)
    (entry / "task.json").write_text("{}", encoding="utf-8")
    return entry


def _tamper_task_json_id(task_dir: Path, new_task_id: str) -> None:
    """把 task.json 的 task_id 改成别的值（模拟手工拷贝/备份还原后 json 内
    id 与目录名不一致——_info_from_json 本就容忍的状态）。"""
    import json as _json

    payload = _json.loads((task_dir / "task.json").read_text(encoding="utf-8"))
    payload["task_id"] = new_task_id
    (task_dir / "task.json").write_text(
        _json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _staging_leftovers() -> list:
    """purge 暂存区（{data}/.purging）现存条目名（无则空表）。"""
    root = backend_main.DATA_DIR / tasks_mod.PURGE_STAGING_DIRNAME
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir())


class TaskDeletePolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from custom_backend import auth as auth_mod

        cls.client = TestClient(app)
        cls.client.__enter__()
        # 与 test_upload_security_regression 同法：不走 /api/auth/register
        # （全套 pytest 共享登录限流桶），直接建用户 + 签发令牌。
        cls.user = auth_mod.create_user("delpol@example.test", "Del-Pol-2026")
        cls.token = auth_mod.create_access_token(
            cls.user["id"], cls.user["username"])
        other = auth_mod.create_user("delpol-other@example.test", "Other-2026")
        cls.other = {"user_id": other["id"], "username": other["username"],
                     "token": auth_mod.create_access_token(
                         other["id"], other["username"])}

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        fixture.cleanup()

    def auth(self):
        return {"Authorization": "Bearer " + self.token}

    def other_auth(self):
        return {"Authorization": "Bearer " + self.other["token"]}

    def setUp(self):
        TaskLockMiddleware._held.clear()
        TaskLockMiddleware._deleting.clear()
        TaskLockMiddleware._list_readers = 0
        for path in list(tasks_mod._HELD_LOCKS):
            tasks_mod.release_task_lock(path, force=True)
        r = self.client.post("/api/tasks",
                             json={"name": "钉-" + self._testMethodName},
                             headers=self.auth())
        self.assertEqual(r.status_code, 201, r.text)
        self.task = r.json()["task_id"]
        self.info = tasks_mod.find_task(self.task, backend_main.DATA_DIR)
        self.assertIsNotNone(self.info)
        self.canonical = tasks_mod.tasks_root(backend_main.DATA_DIR) / self.info.task_id
        self.assertTrue(self.canonical.is_dir())

    def tearDown(self):
        TaskLockMiddleware._held.clear()
        TaskLockMiddleware._deleting.clear()
        TaskLockMiddleware._list_readers = 0
        for path in list(tasks_mod._HELD_LOCKS):
            tasks_mod.release_task_lock(path, force=True)
        # 清 .trash 与 .purging：本钉的过期条目/暂存残骸不该影响后续用例
        # （触点会顺带清扫 .trash，但显式清掉更隔离；均不动任务本体）。
        import shutil

        shutil.rmtree(backend_main.DATA_DIR / ".trash", ignore_errors=True)
        staging_root = backend_main.DATA_DIR / tasks_mod.PURGE_STAGING_DIRNAME
        if staging_root.is_dir():
            for entry in list(staging_root.iterdir()):
                try:
                    tasks_mod._rmtree_with_readonly_recovery(entry)
                except OSError:
                    shutil.rmtree(entry, ignore_errors=True)

    # ------------------------------------------------------------------
    # ① 普通删除仍进 .trash（既有行为钉：无 purge 参数 = 现行为不变）
    # ------------------------------------------------------------------
    def test_nail1_normal_delete_moves_to_trash(self):
        before = _inventory(self.canonical)
        self.assertGreater(len(before), 0)
        r = self.client.delete(f"/api/tasks/{self.task}", headers=self.auth())
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(set(body), {"deleted", "trash"})
        self.assertTrue(body["deleted"])
        self.assertNotIn("purged", body)
        self.assertFalse(self.canonical.exists(), "普通删除后规范目录必须消失")
        root = backend_main.DATA_DIR / ".trash"
        entries = [p for p in root.iterdir() if self.info.task_id in p.name]
        self.assertEqual(len(entries), 1, "整目录应恰好移入 .trash 一份")
        moved = _inventory(entries[0])
        # DELETE 请求自身的请求期任务锁 {taskId}.lock 随目录一起移动（既有
        # 行为）；除它之外必须是删除前那批文件的原样快照。
        locks = {n for n in moved if n.endswith(".lock")}
        self.assertTrue(all(n == f"{self.info.task_id}.lock" for n in locks),
                        f"意外锁文件：{locks}")
        self.assertEqual(sorted(n for n in moved if not n.endswith(".lock")),
                         sorted(n for n in before if not n.endswith(".lock")),
                         "移入 .trash 的必须是与删除前一致的同一批文件")

    # ------------------------------------------------------------------
    # ② purge 缺/错 confirm → 400 结构化，任务逐文件原样在位
    # ------------------------------------------------------------------
    def test_nail2a_purge_without_confirm_400(self):
        before, trash_before = _inventory(self.canonical), _trash_names()
        r = self.client.delete(f"/api/tasks/{self.task}",
                               params={"purge": "true"}, headers=self.auth())
        self.assertEqual(r.status_code, 400, r.text)
        detail = r.json()["detail"]
        self.assertEqual(detail["message_key"], "tasks.purge_confirm_required")
        self.assertEqual(detail["error_code"], "purge_confirm_required")
        self.assertIn("expected", detail["params"])
        self.assertEqual(detail["params"]["task_id"], self.info.task_id)
        self.assertEqual(_inventory(self.canonical), before,
                         "400 后任务必须逐文件原样在位")
        self.assertEqual(_trash_names(), trash_before, ".trash 不得有新条目")
        # 确认失败不得遗留「删除中」标记：任务仍可正常访问
        r2 = self.client.get("/api/tasks", headers=self.auth())
        self.assertEqual(r2.status_code, 200, r2.text)
        self.assertIn(self.info.task_id,
                      [t["task_id"] for t in r2.json()["tasks"]])

    def test_nail2b_purge_wrong_confirm_400(self):
        before, trash_before = _inventory(self.canonical), _trash_names()
        for wrong in ("wrong", "", self.info.task_id + "-x"):
            r = self.client.delete(
                f"/api/tasks/{self.task}",
                params={"purge": "true", "confirm": wrong},
                headers=self.auth())
            self.assertEqual(r.status_code, 400, r.text)
            self.assertEqual(r.json()["detail"]["message_key"],
                             "tasks.purge_confirm_mismatch", wrong)
        # 大小写敏感：confirm 必须与规范 ID 精确匹配
        if self.info.task_id != self.info.task_id.upper():
            r = self.client.delete(
                f"/api/tasks/{self.task}",
                params={"purge": "true", "confirm": self.info.task_id.upper()},
                headers=self.auth())
            self.assertEqual(r.status_code, 400, r.text)
            self.assertEqual(r.json()["detail"]["message_key"],
                             "tasks.purge_confirm_mismatch")
        self.assertEqual(_inventory(self.canonical), before,
                         "400 后任务必须逐文件原样在位")
        self.assertEqual(_trash_names(), trash_before, ".trash 不得有新条目")

    # ------------------------------------------------------------------
    # ③ 正确 confirm → 彻底消失，.trash/users.db/其他任务不受影响
    # ------------------------------------------------------------------
    def test_nail3_purge_success_wipes_completely(self):
        # 另建一个任务作为「其他任务」的逐文件对照
        r = self.client.post("/api/tasks", json={"name": "钉3-邻居"},
                             headers=self.auth())
        self.assertEqual(r.status_code, 201, r.text)
        other_id = r.json()["task_id"]
        other_dir = tasks_mod.tasks_root(backend_main.DATA_DIR) / other_id
        other_before = _inventory(other_dir)
        trash_before, users_before = _trash_names(), _users_count()

        r = self.client.delete(
            f"/api/tasks/{self.task}",
            params={"purge": "true", "confirm": self.info.task_id},
            headers=self.auth())
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json(), {"deleted": True, "purged": True})
        self.assertFalse(self.canonical.exists(), "purge 后规范目录必须彻底消失")
        # 不留 .purging- 残骸（暂存目录已被 rmtree）
        residue = [p.name for p in self.canonical.parent.iterdir()
                   if p.name.startswith(self.info.task_id)]
        self.assertEqual(residue, [], "tasks 根下不得残留 purge 暂存目录")
        self.assertEqual(_trash_names(), trash_before,
                         "purge 不经 .trash：不得有新条目")
        self.assertEqual(_users_count(), users_before, "users 表行数不得变化")
        self.assertEqual(_inventory(other_dir), other_before,
                         "其他任务必须逐文件不受影响")
        # 旧令牌仍可用（用户库功能正常），列表里只剩邻居任务
        r = self.client.get("/api/tasks", headers=self.auth())
        self.assertEqual(r.status_code, 200, r.text)
        ids = [t["task_id"] for t in r.json()["tasks"]]
        self.assertNotIn(self.info.task_id, ids)
        self.assertIn(other_id, ids)

    # ------------------------------------------------------------------
    # ④ 非属主 purge → 403（属主闸门先于 confirm 校验）
    # ------------------------------------------------------------------
    def test_nail4_purge_non_owner_403(self):
        before, trash_before = _inventory(self.canonical), _trash_names()
        # confirm 全对也得 403：属主校验先行
        r = self.client.delete(
            f"/api/tasks/{self.task}",
            params={"purge": "true", "confirm": self.info.task_id},
            headers=self.other_auth())
        self.assertEqual(r.status_code, 403, r.text)
        # 缺 confirm 的非属主同样 403（不向非属主泄露 confirm 口径）
        r = self.client.delete(f"/api/tasks/{self.task}",
                               params={"purge": "true"},
                               headers=self.other_auth())
        self.assertEqual(r.status_code, 403, r.text)
        self.assertEqual(_inventory(self.canonical), before,
                         "403 后任务必须逐文件原样在位")
        self.assertEqual(_trash_names(), trash_before, ".trash 不得有新条目")

    # ------------------------------------------------------------------
    # ⑤ 在途写时 purge → 409 任务保留（与 U5-P2 同一道闸）
    # ------------------------------------------------------------------
    def test_nail5_purge_inflight_write_409(self):
        lock = tasks_mod.acquire_task_lock(self.info.task_id,
                                           backend_main.DATA_DIR)
        self.assertIsNotNone(lock)
        # 模拟 1 个导入在途（ref=1/writes=1）：DELETE 自身经中间件再 +1 → 2
        TaskLockMiddleware._held[self.info.task_id] = [1, lock, 1]
        before, trash_before = _inventory(self.canonical), _trash_names()
        try:
            r = self.client.delete(
                f"/api/tasks/{self.task}",
                params={"purge": "true", "confirm": self.info.task_id},
                headers=self.auth())
            self.assertEqual(r.status_code, 409, r.text)
            self.assertIn("任务有操作进行中", r.json()["detail"])
            self.assertEqual(_inventory(self.canonical), before,
                             "409 后任务必须逐文件原样在位")
            self.assertEqual(_trash_names(), trash_before)
        finally:
            TaskLockMiddleware._held.clear()
            tasks_mod.release_task_lock(lock, force=True)
        # 在途解除后同一请求路径立即成功（防误删闸不卡死正常路径）
        r = self.client.delete(
            f"/api/tasks/{self.task}",
            params={"purge": "true", "confirm": self.info.task_id},
            headers=self.auth())
        self.assertEqual(r.status_code, 200, r.text)
        self.assertFalse(self.canonical.exists())

    # ------------------------------------------------------------------
    # ⑥ 触点清扫：31 天消失 / 29 天在 / TTL=0 禁用 / 坏目录不阻断
    # ------------------------------------------------------------------
    def test_nail6a_sweep_via_list_and_create_touchpoints(self):
        old = _fabricate_trash_entry(datetime.now() - timedelta(days=31),
                                     "old31-" + self._testMethodName)
        fresh = _fabricate_trash_entry(datetime.now() - timedelta(days=29),
                                       "in29-" + self._testMethodName)
        r = self.client.get("/api/tasks", headers=self.auth())
        self.assertEqual(r.status_code, 200, r.text)
        self.assertFalse(old.exists(), "31 天前条目应在 GET /api/tasks 触点被清")
        self.assertTrue(fresh.is_dir(), "29 天前条目必须在保留期内")
        # 第二触点：POST /api/tasks（再放一个 31 天条目验证）
        old2 = _fabricate_trash_entry(datetime.now() - timedelta(days=31),
                                      "old31b-" + self._testMethodName)
        r = self.client.post("/api/tasks", json={"name": "钉6-触点"},
                             headers=self.auth())
        self.assertEqual(r.status_code, 201, r.text)
        self.assertFalse(old2.exists(), "31 天前条目应在 POST /api/tasks 触点被清")
        self.assertTrue(fresh.is_dir())

    def test_nail6b_sweep_ttl0_disables_auto_cleanup(self):
        expired = _fabricate_trash_entry(datetime.now() - timedelta(days=400),
                                         "ttl0-" + self._testMethodName)
        with mock.patch.dict(os.environ, {"REVIEWFLOW_TRASH_TTL_DAYS": "0"}):
            r = self.client.get("/api/tasks", headers=self.auth())
            self.assertEqual(r.status_code, 200, r.text)
            self.assertTrue(expired.is_dir(),
                            "TTL=0 = 禁用自动清理（纯手工模式）：过期条目不动")
        # 恢复默认后同一条目即被清（口径仍生效，非永久豁免）
        r = self.client.get("/api/tasks", headers=self.auth())
        self.assertEqual(r.status_code, 200, r.text)
        self.assertFalse(expired.is_dir())

    def test_nail6c_bad_entry_does_not_block_sweep(self):
        good = _fabricate_trash_entry(datetime.now() - timedelta(days=31),
                                      "good-" + self._testMethodName)
        bad = _fabricate_trash_entry(datetime.now() - timedelta(days=31),
                                     "bad-" + self._testMethodName)
        real_rmtree = tasks_mod.shutil.rmtree
        # 按完整目录名精确匹配「坏条目」（前缀重叠会误伤 bad2/good2）
        bad_names = {bad.name}

        def failing_rmtree(path, *args, **kwargs):
            if Path(path).name in bad_names:
                raise OSError("simulated handle not released")
            return real_rmtree(path, *args, **kwargs)

        with mock.patch.object(tasks_mod.shutil, "rmtree", failing_rmtree):
            r = self.client.get("/api/tasks", headers=self.auth())
            self.assertEqual(r.status_code, 200,
                             f"坏目录不得让触点请求 5xx：{r.text}")
            self.assertFalse(good.exists(), "好条目照常被清")
            self.assertTrue(bad.is_dir(), "失败条目如实保留（下次触点重试）")
            # 直调口径：同一次清扫里单目录失败计数继续（skipped），其余照常清
            good2 = _fabricate_trash_entry(datetime.now() - timedelta(days=31),
                                           "good2-" + self._testMethodName)
            bad2 = _fabricate_trash_entry(datetime.now() - timedelta(days=31),
                                          "bad2-" + self._testMethodName)
            bad_names.add(bad2.name)
            result = tasks_mod.sweep_trash(backend_main.DATA_DIR)
            self.assertFalse(good2.exists())
            self.assertTrue(bad2.is_dir())
            self.assertGreaterEqual(result["purged"], 1)
            self.assertGreaterEqual(result["skipped"], 1)

    # ------------------------------------------------------------------
    # ⑦ TTL 边界（恰好 30 天）与环境变量解析（纯单元，独立临时目录）
    # ------------------------------------------------------------------
    def test_nail7_ttl_boundary_exactly_30_days(self):
        with tempfile.TemporaryDirectory(prefix="rf_sweep_unit_") as tmp:
            now = datetime(2026, 11, 10, 12, 0, 0)
            root = Path(tmp) / ".trash"
            exact = root / "2026-10-11T120000_boundary-exact"
            second_under = root / "2026-10-11T120001_boundary-under"
            for d in (exact, second_under):
                d.mkdir(parents=True)
                (d / "task.json").write_text("{}", encoding="utf-8")
            # 恰好满 30 天（== cutoff）→ 已过保留期 → 清除
            result = tasks_mod.sweep_trash(tmp, ttl_days=30, now=now)
            self.assertEqual(result["purged"], 1)
            self.assertFalse(exact.exists())
            # 差 1 秒未满 → 保留
            self.assertTrue(second_under.is_dir())
            # mtime 兜底：名字不可解析 + mtime 过期 → 清；不可解析 + 新 → 留
            # （mtime 由虚构 now 派生，测试不随真实日期漂移）
            weird_old = root / "not-a-timestamp_old"
            weird_old.mkdir()
            (weird_old / "task.json").write_text("{}", encoding="utf-8")
            old_ts = now.timestamp() - 40 * 86400
            os.utime(weird_old, (old_ts, old_ts))
            weird_new = root / "not-a-timestamp_new"
            weird_new.mkdir()
            (weird_new / "task.json").write_text("{}", encoding="utf-8")
            os.utime(weird_new, (now.timestamp() - 86400,
                                 now.timestamp() - 86400))
            result = tasks_mod.sweep_trash(tmp, ttl_days=30, now=now)
            self.assertFalse(weird_old.exists(), "mtime 兜底应清除过期条目")
            self.assertTrue(weird_new.is_dir())
            # 禁用口径：ttl<1 空转（返回 disabled，绝不动任何条目）
            result = tasks_mod.sweep_trash(tmp, ttl_days=0, now=now)
            self.assertTrue(result["disabled"])
            self.assertEqual(result["purged"], 0)
            self.assertTrue(second_under.is_dir())
            self.assertTrue(weird_new.is_dir())

    def test_nail7b_ttl_env_parsing(self):
        def ttl_with(raw):
            env = dict(os.environ)
            env.pop("REVIEWFLOW_TRASH_TTL_DAYS", None)
            if raw is not None:
                env["REVIEWFLOW_TRASH_TTL_DAYS"] = raw
            with mock.patch.dict(os.environ, env, clear=True):
                return tasks_mod.trash_ttl_days()

        self.assertEqual(ttl_with(None), tasks_mod.DEFAULT_TRASH_TTL_DAYS)
        self.assertEqual(ttl_with(None), 30)
        self.assertEqual(ttl_with(""), 30)
        self.assertEqual(ttl_with("7"), 7)
        self.assertEqual(ttl_with(" 90 "), 90)
        self.assertEqual(ttl_with("abc"), 30, "非整数回退默认，不 500")
        self.assertEqual(ttl_with("0"), 0, "0 = 禁用自动清理")
        self.assertEqual(ttl_with("-3"), -3, "<1 = 禁用自动清理")

    # ------------------------------------------------------------------
    # ⑧ 两种删除自身也是触点（删除成功后过期条目被清）
    # ------------------------------------------------------------------
    def test_nail8_both_delete_paths_trigger_sweep(self):
        # 普通删除触点
        expired = _fabricate_trash_entry(datetime.now() - timedelta(days=31),
                                         "del-old-" + self._testMethodName)
        r = self.client.delete(f"/api/tasks/{self.task}", headers=self.auth())
        self.assertEqual(r.status_code, 200, r.text)
        self.assertFalse(expired.exists(), "普通删除出口应顺带清扫过期条目")
        # 刚移入的新条目必须仍在（TTL 不碰本条删除）
        self.assertTrue(any(p.name.endswith(self.info.task_id)
                            for p in (backend_main.DATA_DIR / ".trash").iterdir()))
        # purge 触点
        r = self.client.post("/api/tasks", json={"name": "钉8-purge"},
                             headers=self.auth())
        purged_task = r.json()["task_id"]
        expired2 = _fabricate_trash_entry(datetime.now() - timedelta(days=31),
                                          "purge-old-" + self._testMethodName)
        r = self.client.delete(f"/api/tasks/{purged_task}",
                               params={"purge": "true", "confirm": purged_task},
                               headers=self.auth())
        self.assertEqual(r.status_code, 200, r.text)
        self.assertFalse(expired2.exists(), "purge 出口应顺带清扫过期条目")

    # ------------------------------------------------------------------
    # ⑨ purge_busy：目录内有打开句柄 → rename 失败 → 409，任务原样保留
    # ------------------------------------------------------------------
    @unittest.skipUnless(sys.platform == "win32",
                         "打开句柄阻断目录 rename 是 Windows 文件系统语义")
    def test_nail9_purge_busy_open_handle_409(self):
        target_file = self.canonical / "collaboration.db"
        self.assertTrue(target_file.is_file())
        before, trash_before = _inventory(self.canonical), _trash_names()
        with open(target_file, "rb") as handle:
            r = self.client.delete(
                f"/api/tasks/{self.task}",
                params={"purge": "true", "confirm": self.info.task_id},
                headers=self.auth())
            self.assertEqual(r.status_code, 409, r.text)
            detail = r.json()["detail"]
            self.assertEqual(detail["error_code"], "purge_busy")
            self.assertEqual(detail["message_key"], "tasks.purge_busy")
            self.assertIn("reason", detail["params"])
            self.assertIn("原样保留", detail["message"])
            self.assertEqual(_inventory(self.canonical), before,
                             "409 后任务必须逐文件原样在位")
            self.assertEqual(_trash_names(), trash_before)
            self.assertEqual(_staging_leftovers(), [], "不得有暂存残骸")
        # 句柄释放后同一请求路径立即成功（防误删闸不卡死正常路径）
        r = self.client.delete(
            f"/api/tasks/{self.task}",
            params={"purge": "true", "confirm": self.info.task_id},
            headers=self.auth())
        self.assertEqual(r.status_code, 200, r.text)
        self.assertFalse(self.canonical.exists())
        self.assertEqual(_staging_leftovers(), [])

    # ------------------------------------------------------------------
    # ⑩ purge_partial：rmtree 未竟 → 409 如实报暂存条目；只读文件不阻断
    # ------------------------------------------------------------------
    def test_nail10a_purge_partial_reports_staging_truthfully(self):
        real_rmtree = tasks_mod.shutil.rmtree
        staging_root = backend_main.DATA_DIR / tasks_mod.PURGE_STAGING_DIRNAME

        def failing_rmtree(path, *args, **kwargs):
            if tasks_mod.PURGE_STAGING_SUFFIX in Path(path).name:
                raise OSError("simulated rmtree failure")
            return real_rmtree(path, *args, **kwargs)

        purged_name = tasks_mod.find_task(
            self.task, backend_main.DATA_DIR).display_name
        with mock.patch.object(tasks_mod.shutil, "rmtree", failing_rmtree):
            r = self.client.delete(
                f"/api/tasks/{self.task}",
                params={"purge": "true", "confirm": self.info.task_id},
                headers=self.auth())
        self.assertEqual(r.status_code, 409, r.text)
        detail = r.json()["detail"]
        self.assertEqual(detail["error_code"], "purge_partial")
        self.assertEqual(detail["message_key"], "tasks.purge_partial")
        params = detail["params"]
        self.assertIn(self.info.task_id, params["staging"])
        self.assertGreaterEqual(params["remaining"], 1,
                                "剩余条目数必须如实非零（rmtree 确实未竟）")
        # rename 已成（原子）：任务目录已移出注册表，残骸恰为所报暂存条目
        self.assertFalse(self.canonical.exists())
        self.assertEqual(_staging_leftovers(), [params["staging"]],
                         "暂存区残骸必须与 409 报告的条目一致（如实报告）")
        # 残骸在 tasks 根之外：不进注册表扫描、不撞建任务重名（评审
        # probe6 的幽灵场景——残骸若在 tasks 根内会同名 409）
        self.assertFalse(any(p.name.startswith(self.info.task_id)
                             for p in self.canonical.parent.iterdir()))
        r = self.client.post("/api/tasks", json={"name": purged_name},
                             headers=self.auth())
        self.assertEqual(r.status_code, 201,
                         f"残骸不得撞建任务重名检查（同名重建应放行）：{r.text}")

    def test_nail10b_readonly_files_do_not_block_purge(self):
        import stat as stat_mod

        marker = self.canonical / "readonly-marker.txt"
        marker.write_text("keep", encoding="utf-8")
        os.chmod(marker, stat_mod.S_IREAD)
        staging_root = backend_main.DATA_DIR / tasks_mod.PURGE_STAGING_DIRNAME
        r = self.client.delete(
            f"/api/tasks/{self.task}",
            params={"purge": "true", "confirm": self.info.task_id},
            headers=self.auth())
        self.assertEqual(r.status_code, 200,
                         f"只读文件应被清位重试兜住，不得变成 409/半删：{r.text}")
        self.assertEqual(r.json(), {"deleted": True, "purged": True})
        self.assertFalse(self.canonical.exists())
        self.assertFalse(marker.exists())
        self.assertTrue(not staging_root.exists()
                        or not any(staging_root.iterdir()),
                        "成功路径不得留暂存残骸")

    # ------------------------------------------------------------------
    # ⑪ 评审 P2：json 内 task_id ≠ 目录名时，删除目标=属主校验命中的目录
    # ------------------------------------------------------------------
    def test_nail11a_tampered_json_id_endpoint_gate_409(self):
        """端点层双保险：server 模式下 json-id 篡改任务在 legacy 属主闸
        即 409（owner 绑定不匹配），攻击者拿 victim id 作 confirm 也到不了
        purge——两个目录都必须原样在位。"""
        r = self.client.post("/api/tasks", json={"name": "钉11-受害者"},
                             headers=self.auth())
        victim_id = r.json()["task_id"]
        victim_dir = tasks_mod.tasks_root(backend_main.DATA_DIR) / victim_id
        victim_before = _inventory(victim_dir)
        _tamper_task_json_id(self.canonical, victim_id)
        # 快照取在篡改之后（篡改本身合法地改写了 task.json；要保的是此后
        # DELETE 不得再动分毫）。
        attacker_before = _inventory(self.canonical)
        # confirm=victim id 恰与篡改后的 info.task_id 匹配（评审实测的利用
        # 向量）——属主闸仍必须先拦下。
        r = self.client.delete(
            f"/api/tasks/{self.task}",
            params={"purge": "true", "confirm": victim_id},
            headers=self.auth())
        self.assertEqual(r.status_code, 409, r.text)
        self.assertEqual(r.json()["detail"]["message_key"],
                         "coding.collaboration_owner_unset")
        self.assertEqual(_inventory(self.canonical), attacker_before,
                         "发起方任务必须逐文件原样在位")
        self.assertEqual(_inventory(victim_dir), victim_before,
                         "受害任务必须逐文件原样在位（绝不错删）")
        self.assertEqual(_staging_leftovers(), [])

    def test_nail11b_purge_task_targets_owner_validated_dir(self):
        """coscreen 层红钉（评审 P2 修复）：purge_task 的删除目标 =
        find_task 命中的目录（info.dir，即属主校验/404 校验对象），绝不是
        按 task.json 内 id 合成的 tasks_root/<json-id>。"""
        r = self.client.post("/api/tasks", json={"name": "钉11b-受害者"},
                             headers=self.auth())
        victim_id = r.json()["task_id"]
        victim_dir = tasks_mod.tasks_root(backend_main.DATA_DIR) / victim_id
        victim_before = _inventory(victim_dir)
        _tamper_task_json_id(self.canonical, victim_id)
        staging = tasks_mod.purge_task(self.task, backend_main.DATA_DIR)
        self.assertIn(self.task, staging.name,
                      "暂存条目名应携带被删目录名（=URL 命中目录）")
        self.assertFalse(self.canonical.exists(),
                         "被删的必须是 URL/属主校验命中的目录")
        self.assertEqual(_inventory(victim_dir), victim_before,
                         "json-id 指向的受害任务绝不被触碰")
        self.assertEqual(_staging_leftovers(), [])


if __name__ == "__main__":
    unittest.main()
