# -*- coding: utf-8 -*-
"""P1-2 回归钉（发布前对抗审查 2026-11-11）：崩溃残留任务锁必须可被接管。

Windows 上 ``os.kill(pid, 0)`` 对已退出 PID 抛 WinError 87（OSError 未捕获）→
``pid_alive`` 异常上冒 → ``acquire_task_lock`` 按「锁被持有」处理 → 崩溃/被杀
进程的任务锁永久 409，只能手工删锁文件（桌面模式=主平台可用性缺陷）。
修复后：``pid_alive(死 PID)`` 必须返回 False（Windows 走 OpenProcess，POSIX 走
ProcessLookupError），陈旧锁被 unlink 接管；活进程（含他进程）持锁仍互斥。
单子进程脚本文件驱动（无 -c 内联），双平台同一套断言。
"""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from coscreen import tasks  # noqa: E402

#: 子进程脚本：sleep 足够久，由测试方 terminate 制造「已退出进程」。
_SLEEPER = "import time\ntime.sleep(120)\n"


class WindowsLockReclaimTests(unittest.TestCase):
    def test_pid_alive_true_for_self(self):
        self.assertTrue(tasks.pid_alive(os.getpid()))

    def test_lock_lifecycle_live_block_then_stale_reclaim(self):
        with tempfile.TemporaryDirectory(prefix="rf_p1_lock_") as tmp:
            sleeper = Path(tmp) / "sleeper.py"
            sleeper.write_text(_SLEEPER, encoding="utf-8")
            child = subprocess.Popen([sys.executable, str(sleeper)],
                                     stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL)
            try:
                # ① 活进程：探活为真、持锁互斥。
                self.assertTrue(tasks.pid_alive(child.pid), "活子进程必须视为存活")
                task_id = "p1locktask"
                lock = Path(tmp) / "taskdir"
                lock.mkdir()
                lock_file = lock / f"{task_id}{tasks.LOCK_SUFFIX}"
                lock_file.write_text(f"{child.pid} 2026-11-11T00:00:00",
                                     encoding="utf-8")
                self.assertIsNone(
                    tasks.acquire_task_lock(task_id, data_dir=tmp, task_dir=lock),
                    "活进程持有的锁不得被抢")
            finally:
                child.terminate()
                child.wait(timeout=60)
            # ② 已退出进程：探活为 False 且不抛（修复前 Windows 在此炸 WinError 87）。
            self.assertFalse(tasks.pid_alive(child.pid),
                             "已退出子进程必须判定死亡（Windows OpenProcess/POSIX ESRCH）")
            # ③ 残留锁接管：unlink 重建，持有者变为本进程；同进程幂等。
            got = tasks.acquire_task_lock(task_id, data_dir=tmp, task_dir=lock)
            self.assertIsNotNone(got, "已退出进程的残留锁必须被接管")
            self.assertEqual(tasks.lock_holder(got)["pid"], os.getpid())
            again = tasks.acquire_task_lock(task_id, data_dir=tmp, task_dir=lock)
            self.assertEqual(again, got)
            self.assertTrue(tasks.release_task_lock(got, force=True))


if __name__ == "__main__":
    unittest.main()
