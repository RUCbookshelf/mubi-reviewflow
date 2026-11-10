# -*- coding: utf-8 -*-
"""r12 仲裁与开关 · 真实浏览器门控（起停两个隔离实例 → 跑 test_r12_arbitration_browser.cjs）。

r12 的两项功能（裁决员「允许参与筛选」开关、合并仲裁页「从服务器加载全员决策」）
此前只有服务端 pytest 钉子；「成员卡开关真的出现」「裁决员打开筛选页看到引导而非
裸 403」「暂停后一键加载真的显示 κ 与冲突表」「刷新后状态正确」「桌面模式入口隐藏」
只能在真实 Chromium 里按多账号走一遍。本文件负责：

1. 在独立数据目录 + 独立端口（19711/19712，避开其他泳道）分别起 server 模式与
   desktop 模式的实例（build.launcher.build_app，与生产同一入口）；
2. 等两个 /api/health 200 后把地址交给 cjs；
3. 无论成败都杀掉进程、删掉临时数据目录——不留常驻实例。

未配置 playwright 环境时如实 skip，不冒充通过：

    REVIEWFLOW_PLAYWRIGHT=E:/rf214-playwright/node_modules/playwright
    PLAYWRIGHT_BROWSERS_PATH=C:/Users/16073/AppData/Local/ms-playwright

也可单独跑（不经过 pytest 起停给自己用）：

    REVIEWFLOW_MODE=server python build/tests/report_evidence_instance.py --port 19711 --data-dir E:/rf214tmp/r14/arbit/data
    python build/tests/report_evidence_instance.py --port 19712 --data-dir E:/rf214tmp/r14/arbit/desktop
    REVIEWFLOW_R12_URL=http://127.0.0.1:19711 \
    REVIEWFLOW_R12_DESKTOP_URL=http://127.0.0.1:19712 \
    node build/tests/test_r12_arbitration_browser.cjs
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
CJS = REPO / "build" / "tests" / "test_r12_arbitration_browser.cjs"
NODE_CANDIDATES = (r"C:\Users\16073\AppData\Local\hermes\node\node.exe",)
SERVER_PORT = 19711
DESKTOP_PORT = 19712
READY_TIMEOUT = 90.0
EXPECTED_CHECKS = 21  # 2026-11-10 门禁收尾：+1 条「en 导航成功且目标页可见」（19/20 病灶对症钉）


def _node_exe() -> str | None:
    for exe in (*NODE_CANDIDATES, shutil.which("node")):
        if exe and Path(exe).is_file():
            return str(exe)
    return None


def _free_port(start: int) -> int:
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


def _spawn(port: int, data_dir: Path, mode: str | None):
    env = {**os.environ, "COBOOKSHELF_DATA_DIR": str(data_dir), "PYTHONDONTWRITEBYTECODE": "1"}
    if mode:
        env["REVIEWFLOW_MODE"] = mode
    else:
        env.pop("REVIEWFLOW_MODE", None)
    launch = (
        "import sys; sys.path.insert(0, %r)\n"
        "import uvicorn\n"
        "from build.launcher import build_app\n"
        "uvicorn.run(build_app(), host='127.0.0.1', port=%d, log_level='warning')\n"
    ) % (str(REPO), port)
    return subprocess.Popen([sys.executable, "-c", launch], cwd=str(REPO), env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def _stop(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)
    if proc.stderr:
        proc.stderr.close()


class ArbitrationBrowserAcceptanceTests(unittest.TestCase):
    """隔离实例上的 r12 真浏览器验收（场景 1~7）。"""

    @unittest.skipUnless(os.environ.get("REVIEWFLOW_PLAYWRIGHT") and os.environ.get("PLAYWRIGHT_BROWSERS_PATH"),
                         "playwright runtime not configured")
    def test_arbitration_browser_acceptance(self):
        node = _node_exe()
        if node is None:
            self.skipTest("node 不可用")
        server_dir = Path(tempfile.mkdtemp(prefix="rf_r12b_server_"))
        desktop_dir = Path(tempfile.mkdtemp(prefix="rf_r12b_desktop_"))
        server_port = _free_port(SERVER_PORT)
        desktop_port = _free_port(DESKTOP_PORT)
        if desktop_port == server_port:  # 端口探测只 bind 不占用，两次可能选到同一个
            desktop_port = _free_port(desktop_port + 1)
        server = _spawn(server_port, server_dir, "server")
        desktop = _spawn(desktop_port, desktop_dir, None)
        try:
            for proc, port, label in ((server, server_port, "server"), (desktop, desktop_port, "desktop")):
                deadline = time.monotonic() + READY_TIMEOUT
                while not _healthy(port):
                    if proc.poll() is not None:
                        err = proc.stderr.read().decode("utf-8", "replace")[-2500:] if proc.stderr else ""
                        self.fail(f"{label} 隔离实例提前退出（端口 {port}）：\n{err}")
                    if time.monotonic() > deadline:
                        self.fail(f"{label} 隔离实例 {READY_TIMEOUT}s 内未就绪（端口 {port}）")
                    time.sleep(0.3)
            artifacts = Path(tempfile.mkdtemp(prefix="rf_r12b_artifacts_"))
            tmp = Path(tempfile.mkdtemp(prefix="rf_r12b_csv_"))
            env = {
                **os.environ,
                "REVIEWFLOW_R12_URL": f"http://127.0.0.1:{server_port}",
                "REVIEWFLOW_R12_DESKTOP_URL": f"http://127.0.0.1:{desktop_port}",
                "REVIEWFLOW_R12_ARTIFACTS": str(artifacts),
                "REVIEWFLOW_R12_TMP": str(tmp),
            }
            result = subprocess.run([node, str(CJS)], cwd=str(REPO), env=env,
                                    capture_output=True, text=True, encoding="utf-8", errors="replace",
                                    timeout=1200)
            output = (result.stdout or "") + (result.stderr or "")
            self.assertEqual(result.returncode, 0, "r12 仲裁浏览器验收失败：\n" + output[-8000:])
            self.assertIn(f'"checks":{EXPECTED_CHECKS}', output,
                          f"{EXPECTED_CHECKS} 条浏览器断言必须全部跑到：\n" + output[-4000:])
            self.assertIn('"errors":0', output, "不得有失败断言：\n" + output[-4000:])
            shutil.rmtree(artifacts, ignore_errors=True)
            shutil.rmtree(tmp, ignore_errors=True)
        finally:
            _stop(server)
            _stop(desktop)
            shutil.rmtree(server_dir, ignore_errors=True)
            shutil.rmtree(desktop_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
