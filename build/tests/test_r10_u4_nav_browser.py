# -*- coding: utf-8 -*-
"""r10-u4 导航泳道 · 真浏览器门控（起停隔离实例 → 跑 test_r10_u4_nav_browser.cjs）。

批准单 §四 的 F06/F10/F11/F17/F18/F19/F20 是交互/时序类改动，静态断言只能钉住源码
形态；「卡片真的出现在目录顶部」「抽屉真的收起」「菜单 title 真的写对目的地」必须由
真浏览器判定。本文件负责：

1. 在独立数据目录 + 独立端口（18991，避开其他泳道已占用的 187xx 段）起一个本工作树
   代码的实例（build.launcher.build_app，与生产同一入口）；
2. 等 /api/health 200 后把地址交给 cjs；
3. 无论成败都杀掉进程、删掉临时数据目录——不留常驻实例。

未配置 playwright 环境时如实 skip，不冒充通过：

    REVIEWFLOW_PLAYWRIGHT=E:/rf214-playwright/node_modules/playwright
    PLAYWRIGHT_BROWSERS_PATH=C:/Users/16073/AppData/Local/ms-playwright

也可单独跑（不经过 pytest 起停给自己用）：

    python build/tests/report_evidence_instance.py --port 18991 --data-dir E:/rf214tmp/r10/u4/data
    REVIEWFLOW_R10U4_URL=http://127.0.0.1:18991 node build/tests/test_r10_u4_nav_browser.cjs
"""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CJS = REPO / "build" / "tests" / "test_r10_u4_nav_browser.cjs"
NODE_CANDIDATES = (r"C:\Users\16073\AppData\Local\hermes\node\node.exe",)
PORT = 18991
READY_TIMEOUT = 90.0


def _node_exe() -> str | None:
    for exe in (*NODE_CANDIDATES, shutil.which("node")):
        if exe and Path(exe).is_file():
            return str(exe)
    return None


def _free_port(start: int = PORT) -> int:
    for port in range(start, start + 20):
        with socket.socket() as probe:
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise unittest.SkipTest(f"{start}-{start + 19} 无空闲端口（其他泳道可能正占用）")


def _healthy(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=2) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError):
        return False


class NavBrowserLaneTests(unittest.TestCase):
    """隔离实例上的真浏览器钉子（F06/F10/F11/F17/F18/F19/F20）。"""

    @unittest.skipUnless(os.environ.get("REVIEWFLOW_PLAYWRIGHT") and os.environ.get("PLAYWRIGHT_BROWSERS_PATH"),
                         "playwright runtime not configured")
    def test_nav_browser_nails(self):
        node = _node_exe()
        if node is None:
            self.skipTest("node 不可用")
        data_dir = Path(tempfile.mkdtemp(prefix="rf_r10u4_"))
        port = _free_port()
        env = {**os.environ, "COBOOKSHELF_DATA_DIR": str(data_dir), "PYTHONDONTWRITEBYTECODE": "1"}
        launch = (
            "import sys; sys.path.insert(0, %r)\n"
            "import uvicorn\n"
            "from build.launcher import build_app\n"
            "uvicorn.run(build_app(), host='127.0.0.1', port=%d, log_level='warning')\n"
        ) % (str(REPO), port)
        proc = subprocess.Popen([sys.executable, "-c", launch], cwd=str(REPO), env=env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic() + READY_TIMEOUT
            while not _healthy(port):
                if proc.poll() is not None:
                    err = proc.stderr.read().decode("utf-8", "replace")[-2500:] if proc.stderr else ""
                    self.fail(f"隔离实例提前退出（端口 {port}）：\n{err}")
                if time.monotonic() > deadline:
                    self.fail(f"隔离实例 {READY_TIMEOUT}s 内未就绪（端口 {port}）")
                time.sleep(0.3)
            result = subprocess.run([node, str(CJS)], cwd=str(REPO), env={**env, "REVIEWFLOW_R10U4_URL": f"http://127.0.0.1:{port}"},
                                    capture_output=True, text=True, encoding="utf-8", errors="replace",
                                    timeout=900)
            output = (result.stdout or "") + (result.stderr or "")
            self.assertEqual(result.returncode, 0, "导航泳道浏览器钉子失败：\n" + output[-6000:])
            self.assertIn('"checks":7', output, "七条浏览器断言必须全部跑到：\n" + output[-3000:])
            self.assertIn('"errors":0', output, "页面不得有脚本错误：\n" + output[-3000:])
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=10)
            if proc.stderr:
                proc.stderr.close()
            shutil.rmtree(data_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
