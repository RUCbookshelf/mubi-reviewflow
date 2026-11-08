# -*- coding: utf-8 -*-
"""2.1.5 W1 · 正式两阶段二选一 + 轮次移除真浏览器门控。

起停一个隔离实例（默认 19701，197xx 段避开其他泳道），先用
build/tests/test_r15_round_maybe_prep.py 准备账号/任务/两篇未决策文献，再跑
build/tests/test_r15_round_maybe_browser.cjs。旧 r15 钉（第 1 轮暂不确定可用 /
第 2 轮隐藏）已换为 W1 新钉：

1. 初筛卡不再显示「2 暂不确定」，work item maybe_allowed=false；
2. 初筛按键盘 2 不保存（无 POST /decisions）；
3. 复筛卡同样不再显示暂不确定；
4. 轮次条 #wf-round-controls 整块移除/隐藏；
5. work-queue 载荷不再含 screening_round / remaining_uncertain。

前端轨（custom_frontend）按契约完成后才应转绿；后端波次默认跳过，需显式
REVIEWFLOW_W1_BROWSER=1 启用，绝不冒充通过：

    REVIEWFLOW_W1_BROWSER=1 \
    REVIEWFLOW_PLAYWRIGHT=E:/rf214-playwright/node_modules/playwright \
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
    @unittest.skipUnless(os.environ.get("REVIEWFLOW_W1_BROWSER") == "1"
                         and os.environ.get("REVIEWFLOW_PLAYWRIGHT")
                         and os.environ.get("PLAYWRIGHT_BROWSERS_PATH"),
                         "W1 browser gate is opt-in after the frontend lane lands")
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
