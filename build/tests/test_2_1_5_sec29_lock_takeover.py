# -*- coding: utf-8 -*-
"""sec29 回归钉（deepseek 异构安全复审 2026-11-11·锁与写闸模块）。

- PID 域钳制：``0``（Windows 空闲进程）/负数/超 32 位无符号域——只可能
  来自损坏或伪造的锁文件，一律判死；POSIX 侧同时堵住 ``kill(-1, 0)``
  （向全进程组探测）误判存活的口子。
- WAIT_FAILED(0xFFFFFFFF) 保守判活：等待本身失败=状态未知，此前
  ``== 258`` 的写法会把「未知」折叠成「死亡」，受保护/他户进程的活锁
  会被当死锁抢占。
- 接管 TOCTOU：判死到删除之间锁内容被并发接管者重写——删除前复核，
  内容与判定时不一致则放弃本轮（不再按陈旧快照误删活锁）。
- unlink 兜底：PermissionError/共享冲突 → None（按忙重试，不异常上冒）；
  FileNotFoundError（并发接管者已删）→ 继续 O_EXCL 重建。
"""
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from coscreen import tasks  # noqa: E402


class PidDomainClampTests(unittest.TestCase):
    def test_out_of_domain_pids_are_dead(self):
        for pid in (0, -1, -999999, 0x1_0000_0000, 2 ** 40):
            self.assertFalse(
                tasks.pid_alive(pid), f"pid={pid} 超出锁持有者合法域，必须判死")

    def test_in_domain_pid_still_probed(self):
        # 域内不误伤：本进程必活（钳制不得把合法 PID 也折叠成死）。
        self.assertTrue(tasks.pid_alive(os.getpid()))


@unittest.skipUnless(sys.platform == "win32", "Windows 探活分支")
class WindowsWaitSemanticsTests(unittest.TestCase):
    """桩替换 WinDLL：钉 OpenProcess/WaitForSingleObject 各分支语义。"""

    @staticmethod
    def _probe(open_result, wait_result, last_error=0):
        def _open(*_a):
            return open_result

        def _wait(*_a):
            return wait_result

        def _close(*_a):
            return 0

        k32 = types.SimpleNamespace(OpenProcess=_open,
                                    WaitForSingleObject=_wait,
                                    CloseHandle=_close)
        with mock.patch("ctypes.WinDLL", return_value=k32), \
                mock.patch("ctypes.get_last_error", return_value=last_error):
            return tasks._pid_alive_windows(1234)

    def test_wait_timeout_alive(self):
        self.assertTrue(self._probe(0x4242, 258))

    def test_wait_object_0_dead(self):
        self.assertFalse(self._probe(0x4242, 0))

    def test_wait_failed_conservative_alive(self):
        # sec29：WAIT_FAILED=状态未知，保守判活（初版 ==258 会折叠成死）。
        self.assertTrue(self._probe(0x4242, 0xFFFFFFFF))

    def test_access_denied_conservative_alive(self):
        self.assertTrue(self._probe(0, 0, last_error=5))

    def test_invalid_parameter_dead(self):
        self.assertFalse(self._probe(0, 0, last_error=87))


class TakeoverRaceTests(unittest.TestCase):
    def _lock(self, tmp, pid):
        directory = Path(tmp) / "taskdir"
        directory.mkdir(exist_ok=True)
        lock = directory / f"sec29task{tasks.LOCK_SUFFIX}"
        lock.write_text(f"{pid} 2026-11-11T00:00:00", encoding="utf-8")
        return directory, lock

    def test_takeover_aborts_when_lock_rewritten_underfoot(self):
        # 判死（pid_alive 假探针）后、删除前，并发接管者把锁重写为活进程
        # ——复核必须发现内容变化并放弃，不得误删（TOCTOU 主钉）。
        with tempfile.TemporaryDirectory(prefix="rf_sec29_to_") as tmp:
            directory, lock = self._lock(tmp, 999999)
            rewritten = f"{os.getpid()} 2026-11-11T00:00:01"

            def race_probe(_pid):
                lock.write_text(rewritten, encoding="utf-8")
                return False

            with mock.patch.object(tasks, "pid_alive", side_effect=race_probe):
                self.assertIsNone(tasks.acquire_task_lock(
                    "sec29task", data_dir=tmp, task_dir=directory))
            self.assertEqual(lock.read_text(encoding="utf-8"), rewritten,
                             "并发接管的活锁不得被陈旧快照误删")

    def test_takeover_unlink_permission_error_returns_none(self):
        with tempfile.TemporaryDirectory(prefix="rf_sec29_pe_") as tmp:
            directory, lock = self._lock(tmp, 999999)
            with mock.patch.object(tasks, "pid_alive", return_value=False), \
                    mock.patch.object(Path, "unlink",
                                      side_effect=PermissionError("busy")):
                self.assertIsNone(tasks.acquire_task_lock(
                    "sec29task", data_dir=tmp, task_dir=directory))
            self.assertTrue(lock.exists(), "兜底路径不得改删就删")

    def test_takeover_unlink_race_still_wins_create(self):
        # 删除瞬间被并发接管者抢先（FileNotFoundError）：继续争 O_EXCL，
        # 本轮重建成功。模拟=「探测到文件已被对方删除」：side_effect 先
        # 真实移除文件（模拟对方的删除动作）再抛 FileNotFoundError——
        # 只抛不删会让第二轮 O_EXCL 又撞原文件，两轮耗尽误返 None。
        with tempfile.TemporaryDirectory(prefix="rf_sec29_fnf_") as tmp:
            directory, lock = self._lock(tmp, 999999)

            def _raced_gone(*_a, **_k):
                os.remove(str(lock))
                raise FileNotFoundError("gone")

            with mock.patch.object(tasks, "pid_alive", return_value=False), \
                    mock.patch.object(Path, "unlink", side_effect=_raced_gone):
                got = tasks.acquire_task_lock(
                    "sec29task", data_dir=tmp, task_dir=directory)
            self.assertIsNotNone(got)
            self.assertEqual(tasks.lock_holder(got)["pid"], os.getpid())
            self.assertTrue(tasks.release_task_lock(got, force=True))

    def test_stale_reclaim_still_works_unpatched(self):
        # 无竞态时陈旧接管语义不变（P1-2 行为保持）。
        with tempfile.TemporaryDirectory(prefix="rf_sec29_ok_") as tmp:
            directory, lock = self._lock(tmp, 999999)
            got = tasks.acquire_task_lock(
                "sec29task", data_dir=tmp, task_dir=directory)
            self.assertIsNotNone(got)
            self.assertEqual(tasks.lock_holder(got)["pid"], os.getpid())
            self.assertTrue(tasks.release_task_lock(got, force=True))


    def test_empty_lock_creation_window_not_stolen(self):
        # sec29b（复审#2）：O_EXCL 刚建好、PID 尚未写入的空锁文件（size=0）
        # = 建窗——不得接管（此前 lock_holder 两侧 None、None!=None 恒假，
        # 复核拦不住空内容窗口，POSIX 实测双持锁）。
        with tempfile.TemporaryDirectory(prefix="rf_sec29b_empty_") as tmp:
            directory, lock = self._lock(tmp, 999999)
            lock.write_text("", encoding="utf-8")  # 清空=模拟建窗
            self.assertIsNone(tasks.acquire_task_lock(
                "sec29task", data_dir=tmp, task_dir=directory))
            self.assertTrue(lock.exists() and lock.stat().st_size == 0,
                            "建窗锁不得被删/重建")

    def test_nonempty_garbage_lock_still_takeoverable(self):
        # 对照：非空但不可解析=损坏残留，接管语义保持。
        with tempfile.TemporaryDirectory(prefix="rf_sec29b_gar_") as tmp:
            directory, lock = self._lock(tmp, 999999)
            lock.write_text("not-a-pid-line", encoding="utf-8")
            got = tasks.acquire_task_lock(
                "sec29task", data_dir=tmp, task_dir=directory)
            self.assertIsNotNone(got)
            self.assertTrue(tasks.release_task_lock(got, force=True))


if __name__ == "__main__":
    unittest.main()
