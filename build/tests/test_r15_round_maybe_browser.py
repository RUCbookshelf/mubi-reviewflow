# -*- coding: utf-8 -*-
"""R15 · 「暂不确定只在初筛第一轮」真浏览器门控。

起停一个隔离实例（默认 19701，197xx 段避开其他泳道），先用
build/tests/test_r15_round_maybe_prep.py 准备账号/任务/两篇未决策文献，再跑
build/tests/test_r15_round_maybe_browser.cjs。断言只看用户可见结果：

1. 第 1 轮：初筛卡片「2 暂不确定」按钮存在且可用；
2. 第 2 轮：按钮消失、work item maybe_allowed=false；
3. 第 2 轮键盘 2 不保存（无 POST /decisions）且出现专门说明；
4. 轮次提示可见，切英文后同提示显示英文；
5. 全文筛选 #ftMay 仍在。

未配置 playwright 环境时如实 skip，不冒充通过：

    REVIEWFLOW_PLAYWRIGHT=E:/rf214-playwright/node_modules/playwright
    PLAYWRIGHT_BROWSERS_PATH=C:/Users/16073/AppData/Local/ms-playwright
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
PREP = REPO / "build" / "tests" / "test_r15_round_maybe_prep.py"
CJS = REPO / "build" / "tests" / "test_r15_round_maybe_browser.cjs"
NODE_CANDIDATES = (r"C:\Users\16073\AppData\Local\hermes\node\node.exe",)
PORT = 19701
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


class RoundMaybeBrowserTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("REVIEWFLOW_PLAYWRIGHT") and os.environ.get("PLAYWRIGHT_BROWSERS_PATH"),
                         "playwright runtime not configured")
    def test_round_maybe_browser_nails(self):
        node = _node_exe()
        if node is None:
            self.skipTest("node 不可用")
        data_dir = Path(tempfile.mkdtemp(prefix="rf_r15_round_maybe_"))
        port = _free_port()
        session_path = data_dir / "session.json"
        artifacts = data_dir / "artifacts"
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
            base = f"http://127.0.0.1:{port}"
            prep = subprocess.run(
                [sys.executable, str(PREP), "--url", base, "--data-dir", str(data_dir),
                 "--session-out", str(session_path)],
                cwd=str(REPO), env=env, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=180)
            self.assertEqual(prep.returncode, 0, "浏览器夹具准备失败：\n" + (prep.stdout or "") + (prep.stderr or ""))
            result = subprocess.run(
                [node, str(CJS)], cwd=str(REPO),
                env={**env, "REVIEWFLOW_R15_URL": base,
                     "REVIEWFLOW_R15_SESSION": str(session_path),
                     "REVIEWFLOW_R15_ARTIFACTS": str(artifacts)},
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900)
            output = (result.stdout or "") + (result.stderr or "")
            self.assertEqual(result.returncode, 0, "轮次 maybe 浏览器钉子失败：\n" + output[-6000:])
            self.assertIn('"checks":5', output, "五条浏览器断言必须全部跑到：\n" + output[-3000:])
            self.assertIn('"errors":[]', output, "页面不得有脚本错误：\n" + output[-3000:])
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
