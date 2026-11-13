# -*- coding: utf-8 -*-
"""r10-u3 移动端泳道真浏览器门控（起停隔离实例 + 驱动 Playwright 钉子）。

批准单 §三 的八条中，凡涉及交互/布局/时序的都必须用真实浏览器在**窄视口**
（360 / 390 / 492 / 760px）实测——真机不可得（见任务书「特别说明」），因此这里
用 Playwright 窄视口代替，并按批准单要求把触控命中区断言在 ≥44px。

本文件只负责：
  1. 用独立端口 + 独立数据目录起一个本工作树代码的实例（不碰任何既有实例）；
  2. 以 ``REVIEWFLOW_R10U3_URL`` 传给 ``test_r10_u3_mobile_browser.cjs``；
  3. 跑完无条件杀掉实例进程（Windows 上用 taskkill /T 结束整个进程树）。

浏览器钉子本体在 build/tests/test_r10_u3_mobile_browser.cjs。
"""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
NODE = (
    os.environ.get("RF_NODE")
    or shutil.which("node")
    or r"C:\Users\16073\AppData\Local\hermes\node\node.exe"
)
PLAYWRIGHT = os.environ.get("REVIEWFLOW_PLAYWRIGHT", r"E:/rf214-playwright/node_modules/playwright")
BROWSERS = os.environ.get("PLAYWRIGHT_BROWSERS_PATH",
                          r"C:/Users/16073/AppData/Local/ms-playwright")
TMP_ROOT = Path(os.environ.get("RF_R10U3_TMP", os.environ.get("RF_R9_TMP_ROOT","E:/rf214tmp")+"/r10/u3"))


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_health(url: str, deadline: float) -> bool:
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url + "/api/health", timeout=2) as response:
                if response.status == 200:
                    return True
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(0.4)
    return False


def _stop(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                       capture_output=True, check=False)
    else:
        proc.terminate()
    try:
        proc.wait(timeout=20)
    except subprocess.TimeoutExpired:  # pragma: no cover - 兜底
        proc.kill()


@pytest.fixture(scope="module")
def isolated_instance():
    """起一个隔离实例；端口随机、数据目录独立、结束后整树杀掉。"""
    if not os.path.exists(NODE):
        pytest.skip("node runtime not found")
    if not Path(PLAYWRIGHT).exists() and not shutil.which("playwright"):
        pytest.skip("playwright module not found")

    port = _free_port()
    data_dir = TMP_ROOT / ("gate-%d" % port)
    data_dir.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["COBOOKSHELF_DATA_DIR"] = str(data_dir)
    env["PYTHONPATH"] = str(REPO)
    env["REVIEWFLOW_PLAYWRIGHT"] = PLAYWRIGHT
    env["PLAYWRIGHT_BROWSERS_PATH"] = BROWSERS

    launcher = REPO / "build" / "tests" / "r10_u3_instance.py"
    assert launcher.is_file(), "隔离实例启动器缺失：build/tests/r10_u3_instance.py"
    proc = subprocess.Popen(
        [sys.executable, str(launcher), "--port", str(port), "--data-dir", str(data_dir)],
        cwd=str(REPO), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    url = "http://127.0.0.1:%d" % port
    try:
        if not _wait_health(url, time.time() + 90):
            pytest.fail("隔离实例 90 秒内未就绪（端口 %d）" % port)
        yield {"url": url, "env": env, "data_dir": data_dir, "port": port}
    finally:
        _stop(proc)


def test_mobile_lane_browser(isolated_instance):
    env = dict(isolated_instance["env"])
    env["REVIEWFLOW_R10U3_URL"] = isolated_instance["url"]
    script = REPO / "build" / "tests" / "test_r10_u3_mobile_browser.cjs"
    assert script.is_file(), "浏览器钉子缺失：build/tests/test_r10_u3_mobile_browser.cjs"
    result = subprocess.run(
        [NODE, str(script)],
        cwd=str(REPO), env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=900,
    )
    output = (result.stdout or "") + (result.stderr or "")
    assert result.returncode == 0, "移动端浏览器钉子失败：\n" + output[-6000:]
    passed = output.count("PASS ")
    # 破坏即红：任何一条 check 抛错都会让 returncode != 0 并在上面先失败；
    # 这里再钉住「不许静默少跑」——正常是 14 项（F37×2/F38×3/F39/F40×2/F41/F42/
    # F43/F44/桌面对照组/脚本错误），低于 14 说明有人删了断言。
    assert passed >= 14, "浏览器钉子只跑了 %d 项，少于预期的 14 项：\n%s" % (passed, output[-2000:])
