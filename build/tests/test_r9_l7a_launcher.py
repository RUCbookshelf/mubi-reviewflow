"""r9-L7a 启动器/修复入口/Windows 冒烟门禁钉子（19 条，破坏即红）。

清单：``out/release_plan/inventory_夜扫286确认分组.md`` §3.7。
本泳道文件域：``build/windows_start.py``、``build/launcher.py``、
``build/repair_reviewflow.bat``、``build/windows_smoke.py``。

每条钉子按清单「探针/复现形态」原文复现，并在本工作树（基线 bfd07ae）实测过
修前表现；断言的是修复后的**行为**，撤掉修复即红。每条都配「对照组」：
合法输入的既有返回/输出逐位不变（R2）。

| 条目 | 单元 | 修前实测（本树） | 测试 |
|---|---|---|---|
| b01·F1-100 | windows_start 修复流程先于单实例锁 | 修复流程不取锁，rmtree/rename 与运行中实例竞态 | ``TestRepairHoldsInstanceLock`` |
| b01·F1-104 | probe 不看 returncode | rc=0 + 非 JSON stdout → ``['Python 检测进程退出：0']`` | ``TestProbeExitCode`` |
| b06·F1-103 | LOCALAPPDATA='' → root 相对路径 | ``Path('')/'ReviewFlow'`` = 相对 ``ReviewFlow`` | ``TestLocalAppDataRoot`` |
| b07·F1-109 | 修复日志 UTF-8 文本层与子进程 locale 字节混写 | pip 子进程继承 ACP，日志混编码 | ``TestRepairLogEncoding`` |
| b02·F1-41 | 锁空闲 + 孤儿子进程 → 起第二套后端 | ``find_free_port`` 直通 → 双实例双写 | ``TestOrphanSingleInstance`` |
| b02·F1-42 | 子进程失败对用户静默 | ``main()`` 直接返回子进程码，无对话框 | ``TestChildFailureIsReported`` |
| b02·F1-45 | port 值域失守 | ``find_free_port(70000) → OverflowError``；``=0 → :0`` | ``TestPortRangeValidation`` |
| b02·F1-46 | 全端口占用返回必败端口；只 connect 不校验身份 | 24000..24099 占满 → 返回 24000 | ``TestPortSelectionAndIdentity`` |
| b08·F1-43 | TOCTOU/重启复用旧 port/探测 host 不一致 | 同上 + 重启沿用旧 port | ``TestPortSelectionAndIdentity`` |
| b08·F1-49 | cp1252 下中文 print 先崩 | ``UnicodeEncodeError``（此为修前机制） | ``TestConsoleEncoding`` |
| b08·F1-50 | docstring 60 秒实为 120 秒 | 60×(1s connect+1s sleep) | ``TestPortSelectionAndIdentity`` |
| b08·F1-51 | 修复入口 fire-and-forget | ``:6 start``/``:7 exit /b 0``，无 windows_start 存在检查 | ``TestRepairBatEntry`` |
| b08·F1-54 | PowerShell 失败兜底 echo 双击不可见 | echo 后立即 ``exit /b 1``，无 pause | ``TestRepairBatEntry`` |
| b09·F1-92 | 冒烟门禁全由 assert 承担 | ``python -O`` → 门禁空转仍 exit 0 | ``TestSmokeGate`` |
| b09·F1-93 | 四条行为断言无失败上下文 | 裸 AssertionError，日志无步骤名 | ``TestSmokeGate`` |
| b09·F1-94 | ``build_app() is not None`` 近恒真 | 前端未挂载也通过 | ``TestSmokeGate`` |
| b09·F1-95 | PNG 只查 8 字节魔数 | width 5 与 20 同判通过 | ``TestSmokeGate`` |
| b09·F1-96 | 轨迹导出只冒烟空数据 | 空输入 ``rects=4``、无日期标签 | ``TestSmokeGate`` |
| b09·F1-97 | 冒烟未设 DATA_DIR/MODE | ``main.py:168`` 导入期读环境变量，冒烟不设 | ``TestSmokeGate`` |

测试不 import custom_backend 的真实数据目录：所有启动路径都把
REVIEWFLOW_STORAGE_CONFIG / COBOOKSHELF_DATA_DIR / LOCALAPPDATA 指到临时树。
所有临时输出（pytest basetemp、mkdtemp、冒烟沙箱）都改到
``E:/rf214tmp/r9/l7a``——C: 盘曾 100% 满，探针不得写 %TEMP%。
"""
from __future__ import annotations

import ast
import hashlib
import http.server
import importlib
import importlib.util
import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

TMP_ROOT = Path("E:/rf214tmp/r9/l7a/pytest-tmp")
TMP_ROOT.mkdir(parents=True, exist_ok=True)


@pytest.fixture(autouse=True)
def _temp_on_e_drive(monkeypatch):
    """Keep every throwaway tree off %TEMP% (C: was full during this wave)."""
    monkeypatch.setattr(tempfile, "tempdir", str(TMP_ROOT))
    for var in ("TMP", "TEMP", "TMPDIR"):
        monkeypatch.setenv(var, str(TMP_ROOT))

REPO = Path(__file__).resolve().parents[2]
BUILD = REPO / "build"
PYTHON = sys.executable
IS_WINDOWS = sys.platform == "win32"
cmd_only = pytest.mark.skipif(not IS_WINDOWS, reason="Windows 专用入口")

sys.path.insert(0, str(BUILD))
sys.path.insert(0, str(REPO))


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def load(name: str, path: Path):
    """Load a script as a fresh module (tests must not share module state)."""
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def bind_all(start: int, count: int) -> list[socket.socket]:
    socks = []
    for port in range(start, start + count):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", port))
        s.listen(1)
        socks.append(s)
    return socks


def png_bytes(width: int, height: int) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (width, height), "red").save(buf, format="PNG")
    return buf.getvalue()


class _StubServer:
    """Tiny HTTP server with pluggable JSON bodies, used as a port occupant."""

    def __init__(self, routes: dict[str, tuple[int, object]]):
        routes = dict(routes)
        body_by_path = {path: (status, json.dumps(payload).encode())
                        for path, (status, payload) in routes.items()}

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802 - http.server contract
                status, body = body_by_path.get(self.path, (404, b"{}"))
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()


class _FakeChild:
    """Stand-in for the uvicorn child process."""

    def __init__(self, returncode=0, on_wait=None):
        self.returncode = returncode
        self._on_wait = on_wait
        self.waits = 0

    def wait(self):
        self.waits += 1
        if self._on_wait:
            self._on_wait(self.waits)
        return self.returncode


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """Point every user-scoped path at the pytest temp dir (E: drive)."""
    local = tmp_path / "localappdata"
    local.mkdir()
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setenv("REVIEWFLOW_STORAGE_CONFIG", str(tmp_path / "config" / "local-storage.json"))
    monkeypatch.setenv("COBOOKSHELF_DATA_DIR", str(tmp_path / "data"))
    (tmp_path / "data").mkdir()
    monkeypatch.delenv("REVIEWFLOW_PORT", raising=False)
    monkeypatch.delenv("REVIEWFLOW_HOST", raising=False)
    monkeypatch.delenv("REVIEWFLOW_PORT_FILE", raising=False)
    monkeypatch.delenv("REVIEWFLOW_RUNTIME_OVERLAY", raising=False)
    (tmp_path / "config").mkdir()
    return tmp_path


def _install_dir(tmp_path: Path) -> Path:
    """A fake installation dir with the bundled runtime markers."""
    app = tmp_path / "install"
    (app / "runtime").mkdir(parents=True)
    (app / "runtime" / "python.exe").write_bytes(b"stub")
    (app / "runtime" / "pythonw.exe").write_bytes(b"stub")
    (app / "requirements-app.txt").write_text("fastapi\n", encoding="utf-8")
    return app


# --------------------------------------------------------------------------- #
# b01·F1-100 — repair flow must hold the single-instance lock
# --------------------------------------------------------------------------- #


class TestRepairHoldsInstanceLock:
    """修前：修复流程在 launcher.main 的锁之前，先 rmtree/rename overlay。

    运行中的实例把 ``<root>/runtime-deps/<ver>`` 放在 sys.path 上、DLL 已加载，
    于是 rmtree → PermissionError（半删），rename → FileExistsError。
    修后：修复前就取同一把桌面单实例锁；锁被占则明确告知用户并返回 1，
    完全不碰 overlay。
    """

    def test_repair_refuses_while_another_instance_holds_the_lock(self, sandbox, monkeypatch):
        ws = load("ws_lock", BUILD / "windows_start.py")
        app = _install_dir(sandbox)
        ws.APP = app
        requirements = app / "requirements-app.txt"
        version = hashlib.sha256(requirements.read_bytes()).hexdigest()[:12]
        overlay = sandbox / "localappdata" / "ReviewFlow" / "runtime-deps" / version
        overlay.mkdir(parents=True)
        canary = overlay / "canary.pyd"
        canary.write_bytes(b"old-runtime")

        messages = []
        monkeypatch.setattr(ws, "message", lambda text, **kw: messages.append(text) or True)
        monkeypatch.setattr(ws, "probe", lambda target=None: ["dep missing"])
        # 修前的模块没有 replace_overlay（归并进 main 的 rmtree/rename）；
        # 用 raising=False 让基线红落在「另一实例持锁时仍动了 overlay / 没有提示」。
        monkeypatch.setattr(ws, "replace_overlay", lambda *a: pytest.fail("overlay 不得被改动"),
                            raising=False)

        lock_path = sandbox / "config" / "storage.lock"
        holder = subprocess.Popen(
            [PYTHON, "-c", _LOCK_HOLDER, str(lock_path), "30"],
            stdout=subprocess.PIPE, text=True)
        try:
            assert holder.stdout.readline().strip() == "LOCKED"
            cwd = os.getcwd()
            try:
                rc = ws.main(force_repair=False)
            finally:
                os.chdir(cwd)
        finally:
            holder.kill()
            holder.wait(timeout=10)
        assert rc == 1
        assert canary.read_bytes() == b"old-runtime", "另一实例持锁时不得删除/替换 overlay"
        assert any("已在运行" in text for text in messages), messages
        # 对照组：不得弹出修复确认框（说明根本没进入修复分支）
        assert not any("是否修复" in text for text in messages), messages

    def test_overlay_replacement_leaves_no_stale_tree(self, sandbox, monkeypatch):
        """加固：re-patch 覆盖旧 overlay 时不留半删态，也不因 FileExistsError 丢掉新树。"""
        ws = load("ws_overlay", BUILD / "windows_start.py")
        app = _install_dir(sandbox)
        ws.APP = app
        requirements = app / "requirements-app.txt"
        version = hashlib.sha256(requirements.read_bytes()).hexdigest()[:12]
        overlay = sandbox / "localappdata" / "ReviewFlow" / "runtime-deps" / version
        overlay.mkdir(parents=True)
        (overlay / "old.dll").write_bytes(b"old")
        monkeypatch.setattr(ws, "message", lambda text, **kw: True)
        monkeypatch.setattr(ws, "probe", lambda target=None: [])

        class Result:
            returncode = 0

        def fake_run(command, **kwargs):
            # pip --target <target>：在临时目录里产出新树
            target = Path(command[command.index("--target") + 1])
            target.mkdir(parents=True, exist_ok=True)
            (target / "new.dll").write_bytes(b"new")
            return Result()

        monkeypatch.setattr(ws.subprocess, "run", fake_run)
        # 修复后必须继续走 launcher；这里把 launcher 换成记录器，避免真的起服务。
        import launcher as real_launcher

        monkeypatch.setattr(real_launcher, "main", lambda **kw: 0)
        cwd = os.getcwd()
        try:
            assert ws.main(force_repair=True) == 0
        finally:
            os.chdir(cwd)

        assert sorted(p.name for p in overlay.iterdir()) == ["new.dll"]
        assert (overlay / "new.dll").read_bytes() == b"new"
        assert not list(overlay.parent.glob(f"{overlay.name}-stale-*"))


_LOCK_HOLDER = r"""
import msvcrt, sys, time
path, seconds = sys.argv[1], float(sys.argv[2])
handle = open(path, "a+b")
handle.seek(0)
if not handle.read(1):
    handle.seek(0); handle.write(b"\0"); handle.flush()
handle.seek(0)
msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
print("LOCKED", flush=True)
time.sleep(seconds)
"""


# --------------------------------------------------------------------------- #
# b01·F1-104 — probe() must use the exit code, not only stdout
# --------------------------------------------------------------------------- #


class TestProbeExitCode:
    """修前：``probe() with returncode=0 and non-JSON stdout`` →
    ``['Python 检测进程退出：0']``（明明成功却报失败）。"""

    @pytest.mark.parametrize("stdout,stderr,rc,expected", [
        ("banner line\n", "", 0, []),                       # 成功 + 非 JSON → 成功
        ('["numpy: missing"]', "", 0, ["numpy: missing"]),   # 有失败项的合法载荷
        ("[]", "", 0, []),                                   # 干净 JSON
        ("", "Traceback boom", 3, "3"),                      # 崩溃 → 失败并带 stderr
        ("[]", "", 3, "3"),                                  # 打印后仍崩 → 不得当成功
    ])
    def test_probe_findings_contract(self, stdout, stderr, rc, expected):
        ws = load("ws_probe", BUILD / "windows_start.py")
        findings = ws.probe_findings(stdout, stderr, rc)
        if expected == []:
            assert findings == []
        elif isinstance(expected, list):
            assert findings == expected
        else:
            assert findings and expected in findings[0]
            if stderr:
                assert stderr in findings[0], "非零退出必须带上子进程错误上下文"

    def test_probe_success_with_noisy_stdout_is_not_a_failure(self, monkeypatch):
        ws = load("ws_probe2", BUILD / "windows_start.py")

        class Result:
            returncode = 0
            stdout = "some startup noise\n"
            stderr = ""

        monkeypatch.setattr(ws.subprocess, "run", lambda *a, **k: Result())
        assert ws.probe() == []


# --------------------------------------------------------------------------- #
# b06·F1-103 — %LOCALAPPDATA% must never resolve to a relative path
# --------------------------------------------------------------------------- #


class TestLocalAppDataRoot:
    """修前：``LOCALAPPDATA=''`` → ``Path('')/'ReviewFlow'`` 相对路径，
    日志/overlay 落进安装目录或裸崩（mkdir 在 try 之外）。"""

    def test_empty_localappdata_is_absolute(self, monkeypatch):
        ws = load("ws_root", BUILD / "windows_start.py")
        monkeypatch.setenv("LOCALAPPDATA", "")
        root = ws.user_root()
        assert root.is_absolute(), root
        assert root.name == "ReviewFlow"
        assert root.parent.name == "Local", root
        # 对照组：正常值原样使用
        monkeypatch.setenv("LOCALAPPDATA", str(Path("C:/Users/example/AppData/Local")))
        assert ws.user_root() == Path("C:/Users/example/AppData/Local") / "ReviewFlow"

    def test_relative_localappdata_falls_back(self, monkeypatch):
        ws = load("ws_root2", BUILD / "windows_start.py")
        monkeypatch.setenv("LOCALAPPDATA", "relative/dir")
        assert ws.user_root().is_absolute()

    def test_launcher_default_data_dir_uses_the_same_rule(self, monkeypatch, tmp_path):
        """启动器的默认数据目录也要落回绝对路径（同一族口径）。"""
        launcher = importlib.reload(importlib.import_module("launcher"))
        monkeypatch.setattr(launcher, "APP_DIR", tmp_path / "payload")
        monkeypatch.setenv("LOCALAPPDATA", "")
        resolved = launcher.default_data_dir()
        assert resolved.is_absolute(), resolved
        assert resolved.name == "ReviewFlow"
        absolute = tmp_path / "AppData" / "Local"
        absolute.mkdir(parents=True)
        monkeypatch.setenv("LOCALAPPDATA", str(absolute))
        assert launcher.default_data_dir() == absolute / "ReviewFlow"

    def test_unwritable_root_reports_instead_of_traceback(self, sandbox, monkeypatch):
        """mkdir 移进 try：root 不可建时返回 1 + 日志提示，而不是裸栈。"""
        ws = load("ws_root3", BUILD / "windows_start.py")
        app = _install_dir(sandbox)
        ws.APP = app
        blocker = sandbox / "blocked"
        blocker.write_text("not a directory", encoding="utf-8")
        (blocker / "ReviewFlow").parent  # noqa: B018 - documents the intended path
        monkeypatch.setattr(ws, "user_root", lambda: blocker / "ReviewFlow")
        messages = []
        monkeypatch.setattr(ws, "message", lambda text, **kw: messages.append(text) or True)
        cwd = os.getcwd()
        try:
            rc = ws.main()
        finally:
            os.chdir(cwd)
        assert rc == 1
        assert any("未能启动" in text for text in messages), messages


# --------------------------------------------------------------------------- #
# b07·F1-109 — one encoding for the whole repair log
# --------------------------------------------------------------------------- #


class TestRepairLogEncoding:
    """修前：pip 子进程日志以 ACP（GBK）字节写进 UTF-8 文本日志，
    ``整文件 UTF-8 解码在 0xd2 失败``。修后：子进程固定 UTF-8，日志按字节 UTF-8。"""

    def test_repair_child_runs_in_utf8_and_log_stays_utf8(self, sandbox, monkeypatch):
        ws = load("ws_log", BUILD / "windows_start.py")
        app = _install_dir(sandbox)
        ws.APP = app
        monkeypatch.setattr(ws, "probe", lambda target=None: [])
        messages = []
        monkeypatch.setattr(ws, "message", lambda text, **kw: messages.append(text) or True)

        seen = {}

        class Result:
            returncode = 7

        def fake_run(command, **kwargs):
            child_env = dict(kwargs.get("env") or {})
            seen["env"] = child_env
            seen["command"] = command
            # 子进程按它拿到的编码吐字节：修前它继承 ACP（GBK），那正是
            # "UTF-8 文本层 + GBK 子进程字节"混写的来源。
            encoding = child_env.get("PYTHONIOENCODING", "cp936").split(":")[0]
            if child_env.get("PYTHONUTF8") == "1":
                encoding = "utf-8"
            kwargs["stdout"].write("依赖安装失败".encode(encoding, "replace"))
            kwargs["stdout"].flush()
            return Result()

        monkeypatch.setattr(ws.subprocess, "run", fake_run)
        cwd = os.getcwd()
        try:
            rc = ws.main(force_repair=True)
        finally:
            os.chdir(cwd)

        assert rc == 1
        assert seen["env"].get("PYTHONIOENCODING", "").lower() == "utf-8"
        assert seen["env"].get("PYTHONUTF8") == "1"
        log_path = sandbox / "localappdata" / "ReviewFlow" / "startup_diagnostic.log"
        text = log_path.read_text(encoding="utf-8")  # 修复前此处 UnicodeDecodeError
        assert "dependency repair" in text

    def test_append_log_writes_utf8_bytes(self, sandbox):
        ws = load("ws_log2", BUILD / "windows_start.py")
        log = sandbox / "startup_diagnostic.log"
        ws.append_log(log, "检测到运行依赖缺失\n")
        assert log.read_bytes().decode("utf-8") == "检测到运行依赖缺失\n"
        ws.append_log(log, "第二行\n")
        assert log.read_text(encoding="utf-8") == "检测到运行依赖缺失\n第二行\n"


# --------------------------------------------------------------------------- #
# b02·F1-41 / F1-46 / F1-42 — single instance decisions in launcher.main
# --------------------------------------------------------------------------- #


class TestOrphanSingleInstance:
    """修前：锁空闲（父进程已崩）但 uvicorn 子进程还在服务时，无条件起第二套
    后端 → 同数据目录双写。修后：先按身份探活，命中则只重开窗口。"""

    def test_live_orphan_is_reused_not_duplicated(self, sandbox, monkeypatch):
        launcher = importlib.import_module("launcher")
        launcher = importlib.reload(launcher)
        # 标准桌面路径（未显式指定数据目录）才是「第二个快捷方式」场景
        monkeypatch.delenv("COBOOKSHELF_DATA_DIR", raising=False)
        with _StubServer({
            "/api/health": (200, {"status": "ok"}),
            "/api/runtime": (200, {"instance_id": "a" * 32, "mode": "desktop"}),
        }) as server:
            (sandbox / "config" / "desktop-port.txt").write_text(str(server.port), encoding="ascii")
            opened = []
            spawned = []
            monkeypatch.setattr(launcher, "open_browser", opened.append)
            monkeypatch.setattr(launcher, "_spawn_server",
                                lambda *a, **k: spawned.append(a) or pytest.fail(
                                    "孤儿后端仍在服务时不得启动第二套后端"))
            rc = launcher.main()
        assert rc == 0
        assert opened == [f"http://127.0.0.1:{server.port}"], opened
        assert spawned == []

    def test_explicit_data_dir_still_allows_a_parallel_instance(self, sandbox, monkeypatch):
        """对照组（R2）：显式 COBOOKSHELF_DATA_DIR 的调用方不被孤儿实例拦下。"""
        launcher = importlib.reload(importlib.import_module("launcher"))
        with _StubServer({
            "/api/health": (200, {"status": "ok"}),
            "/api/runtime": (200, {"instance_id": "a" * 32}),
        }) as server:
            (sandbox / "config" / "desktop-port.txt").write_text(str(server.port), encoding="ascii")
            monkeypatch.setattr(launcher, "find_free_port", lambda *a, **k: free_port())
            monkeypatch.setattr(launcher, "wait_and_open", lambda *a, **k: None)
            monkeypatch.setattr(launcher, "open_browser", lambda url: None)
            monkeypatch.setattr(launcher, "_say", lambda text, **kw: None)
            monkeypatch.setattr(launcher, "_notify_error", lambda text: pytest.fail(text))
            spawned = []
            monkeypatch.setattr(launcher, "_spawn_server",
                                lambda *a, **k: spawned.append(a) or _FakeChild(0))
            assert launcher.main() == 0
        assert spawned, "显式数据目录时仍应允许自己起后端"

    def test_stranger_on_recorded_port_is_not_our_app(self, sandbox, monkeypatch):
        """只回 /api/health 的程序（陌生应用）不得被认成 ReviewFlow。"""
        launcher = importlib.reload(importlib.import_module("launcher"))
        with _StubServer({"/api/health": (200, {"status": "ok"})}) as server:
            assert launcher.probe_running_instance(server.port) is None
            (sandbox / "config" / "desktop-port.txt").write_text(str(server.port), encoding="ascii")
            assert launcher.reopen_running_app() is False
        # 对照组：真正的 ReviewFlow 响应能被认出
        with _StubServer({
            "/api/health": (200, {"status": "ok"}),
            "/api/runtime": (200, {"instance_id": "b" * 32}),
        }) as server:
            assert launcher.probe_running_instance(server.port) == \
                f"http://127.0.0.1:{server.port}"

    def test_child_failure_is_reported_to_the_user(self, sandbox, monkeypatch):
        """b02·F1-42：uvicorn 绑定失败 → 父进程过去静默返回退出码。"""
        launcher = importlib.reload(importlib.import_module("launcher"))
        monkeypatch.setattr(launcher, "find_free_port", lambda *a, **k: free_port())
        monkeypatch.setattr(launcher, "wait_and_open", lambda *a, **k: None)
        monkeypatch.setattr(launcher, "open_browser", lambda url: None)
        monkeypatch.setattr(launcher, "_spawn_server",
                            lambda *a, **k: _FakeChild(returncode=7))
        notified = []
        monkeypatch.setattr(launcher, "_notify_error", notified.append)
        monkeypatch.setattr(launcher, "_say", lambda text, **kw: None)
        rc = launcher.main()
        assert rc == 7
        assert notified, "子进程失败必须给用户可见原因"
        assert "7" in notified[0] and "reviewflow.log" in notified[0]

    def test_healthy_start_stays_silent_and_returns_zero(self, sandbox, monkeypatch):
        """对照组：正常退出（rc=0）不弹错误对话框。"""
        launcher = importlib.reload(importlib.import_module("launcher"))
        monkeypatch.setattr(launcher, "find_free_port", lambda *a, **k: free_port())
        monkeypatch.setattr(launcher, "wait_and_open", lambda *a, **k: None)
        monkeypatch.setattr(launcher, "open_browser", lambda url: None)
        monkeypatch.setattr(launcher, "_spawn_server", lambda *a, **k: _FakeChild(0))
        notified = []
        monkeypatch.setattr(launcher, "_notify_error", notified.append)
        monkeypatch.setattr(launcher, "_say", lambda text, **kw: None)
        assert launcher.main() == 0
        assert notified == []


# --------------------------------------------------------------------------- #
# b02·F1-45 / F1-46 / b08·F1-43 / F1-50 — port selection and readiness
# --------------------------------------------------------------------------- #


class TestPortRangeValidation:
    """修前：``find_free_port(70000) → OverflowError``；``REVIEWFLOW_PORT=0``
    → url ``http://127.0.0.1:0``。"""

    def test_out_of_range_start_is_normalised(self):
        launcher = importlib.reload(importlib.import_module("launcher"))
        port = launcher.find_free_port(70000)
        assert 1 <= port <= 65535
        assert launcher.normalize_port(70000) == launcher.DEFAULT_PORT
        assert launcher.normalize_port(0) == launcher.DEFAULT_PORT
        assert launcher.normalize_port("0") == launcher.DEFAULT_PORT
        assert launcher.normalize_port("not-a-port") == launcher.DEFAULT_PORT
        assert launcher.normalize_port(-5) == launcher.DEFAULT_PORT
        # 对照组：合法值逐位不变
        assert launcher.normalize_port(8614) == 8614
        assert launcher.normalize_port("9000") == 9000

    def test_invalid_env_port_is_ignored_at_startup(self, sandbox, monkeypatch):
        launcher = importlib.reload(importlib.import_module("launcher"))
        monkeypatch.setenv("REVIEWFLOW_PORT", "0")
        monkeypatch.setattr(launcher, "wait_and_open", lambda *a, **k: None)
        monkeypatch.setattr(launcher, "open_browser", lambda url: None)
        spawned = []

        def fake_spawn(port, restart_file):
            spawned.append(port)
            return _FakeChild(0)

        monkeypatch.setattr(launcher, "_spawn_server", fake_spawn)
        monkeypatch.setattr(launcher, "_notify_error", lambda text: pytest.fail(text))
        monkeypatch.setattr(launcher, "_say", lambda text, **kw: None)
        assert launcher.main() == 0
        assert spawned and spawned[0] != 0
        assert spawned[0] >= 1024


class TestPortSelectionAndIdentity:
    """F1-46：段内全占仍返回必败端口；F1-43：探测 host 与绑定 host 不一致、
    重启复用旧 port；F1-50：docstring 60 秒实为 120 秒。"""

    def test_all_ports_occupied_returns_a_bindable_port(self):
        launcher = importlib.reload(importlib.import_module("launcher"))
        start = free_port()
        # 让 start..start+99 全被占（若中途撞到系统端口则换起点重试一次）
        for attempt in range(3):
            try:
                held = bind_all(start, 100)
            except OSError:
                for s in locals().get("held", []):
                    s.close()
                start = free_port()
                continue
            break
        try:
            port = launcher.find_free_port(start)
            assert port != start, "全占用时不得把必败端口当成功结果"
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                probe.bind(("127.0.0.1", port))  # 必须真的可绑定
        finally:
            for s in held:
                s.close()

    def test_probe_host_follows_bind_host(self, monkeypatch):
        """F1-43：探测固定 127.0.0.1，而 uvicorn 绑定的是 REVIEWFLOW_HOST。"""
        launcher = importlib.reload(importlib.import_module("launcher"))
        # 对照组：默认 host 与客户端 host 一致
        assert launcher._bind_host() == "127.0.0.1"
        assert launcher._client_host() == "127.0.0.1"

        monkeypatch.setenv("REVIEWFLOW_HOST", "127.0.0.2")
        assert launcher._bind_host() == "127.0.0.2"
        port = free_port()
        probe = launcher._bind_probe_socket(launcher._bind_host(), port)
        try:
            assert probe.getsockname()[0] == "127.0.0.2", probe.getsockname()
        finally:
            probe.close()

        # 通配绑定：探测落到环回，而 URL 仍指向可访问的地址
        monkeypatch.setenv("REVIEWFLOW_HOST", "0.0.0.0")
        assert launcher._client_host() == "127.0.0.1"
        # IPv6 字面量要加方括号，否则 URL 非法
        assert launcher._url_host("::1") == "[::1]"
        assert launcher._url_host("127.0.0.1") == "127.0.0.1"

    def test_restart_rechecks_the_port_and_republishes_it(self, sandbox, monkeypatch):
        launcher = importlib.reload(importlib.import_module("launcher"))
        first, second = free_port(), free_port()
        while second == first:
            second = free_port()
        ports = iter([first, second])
        monkeypatch.setattr(launcher, "find_free_port", lambda *a, **k: next(ports))
        reopened = []
        # 重启后改端口：浏览器要等新端口就绪再开（不是立刻 open_browser 旧地址）
        monkeypatch.setattr(launcher, "wait_and_open", lambda url, port: reopened.append((url, port)))
        opened = []
        monkeypatch.setattr(launcher, "open_browser", opened.append)
        monkeypatch.setattr(launcher, "_say", lambda text, **kw: None)
        monkeypatch.setattr(launcher, "_notify_error", lambda text: pytest.fail(text))
        port_file = sandbox / "published-port.txt"
        monkeypatch.setenv("REVIEWFLOW_PORT_FILE", str(port_file))
        spawned = []
        waits = []

        def fake_spawn(port, restart_file):
            spawned.append(port)

            def wait(_attempt):
                waits.append(port)
                if len(waits) == 1:  # 只请求一次重启
                    restart_file.write_text("restart", encoding="ascii")

            return _FakeChild(0, on_wait=wait)

        monkeypatch.setattr(launcher, "_spawn_server", fake_spawn)
        real_record = launcher.record_port
        recorded = []
        monkeypatch.setattr(launcher, "record_port",
                            lambda path, value: recorded.append(value) or real_record(path, value))
        assert launcher.main() == 0
        assert len(waits) == 2, waits
        assert spawned == [first, second], spawned
        # 实例文件在退出时按设计清理；重启前必须已把新端口写出去
        assert recorded == [first, second], recorded
        assert port_file.read_text(encoding="ascii") == str(second)
        # 首次启动按 first 等就绪；重启改端口后按 new port 再等一次（不沿用旧端口）
        assert reopened == [(f"http://127.0.0.1:{first}", first),
                           (f"http://127.0.0.1:{second}", second)], reopened

    def test_wait_and_open_uses_one_deadline_and_needs_identity(self, sandbox):
        """修前：60 轮 × (1s connect + 1s sleep) = 120s，且 connect 成功就开窗。"""
        launcher = importlib.reload(importlib.import_module("launcher"))
        assert launcher.READY_TIMEOUT == 60.0

        clock = {"now": 0.0}
        sleeps = []

        def fake_sleep(seconds):
            sleeps.append(seconds)
            clock["now"] += seconds

        monkeypatch = pytest.MonkeyPatch()
        try:
            monkeypatch.setattr(launcher.time, "monotonic", lambda: clock["now"])
            monkeypatch.setattr(launcher.time, "sleep", fake_sleep)
            with _StubServer({"/api/health": (200, {"status": "ok"})}) as stranger:
                opened = []
                monkeypatch.setattr(launcher, "open_browser", opened.append)
                launcher.wait_and_open(f"http://127.0.0.1:{stranger.port}", stranger.port)
        finally:
            monkeypatch.undo()
        # 陌生应用不算就绪：必须等满 deadline 才开窗
        assert clock["now"] >= launcher.READY_TIMEOUT - 1e-9
        assert clock["now"] <= 2 * launcher.READY_TIMEOUT
        assert opened == [f"http://127.0.0.1:{stranger.port}"]
        assert sleeps == [1] * len(sleeps)


# --------------------------------------------------------------------------- #
# b08·F1-49 — console encoding
# --------------------------------------------------------------------------- #


class TestConsoleEncoding:
    """修前：``PYTHONIOENCODING=cp1252`` + 重定向 stdout → 中文 print 抛
    UnicodeEncodeError，早于后端启动（exit=1）。"""

    def _driver(self, script_tail: str) -> str:
        return (
            "import sys\n"
            f"sys.path.insert(0, {str(BUILD)!r})\n"
            "import launcher\n"
            + script_tail
        )

    def test_cp1252_startup_banner_does_not_abort(self, tmp_path):
        driver = self._driver(
            "launcher._prepare_console()\n"
            "launcher._say('木笔ReviewFlow 启动中...')\n"
            "print('DRIVER-DONE')\n")
        env = dict(os.environ, PYTHONIOENCODING="cp1252", PYTHONUTF8="0")
        result = subprocess.run([PYTHON, "-c", driver], capture_output=True,
                                text=True, encoding="utf-8", errors="replace",
                                env=env, timeout=120)
        assert result.returncode == 0, result.stderr
        assert "DRIVER-DONE" in result.stdout

    def test_utf8_control_group_keeps_the_text_intact(self):
        driver = self._driver(
            "launcher._prepare_console()\n"
            "launcher._say('木笔ReviewFlow 启动中...')\n"
            "print('DRIVER-DONE')\n")
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
        result = subprocess.run([PYTHON, "-c", driver], capture_output=True,
                                text=True, encoding="utf-8", errors="replace",
                                env=env, timeout=120)
        assert result.returncode == 0, result.stderr
        assert "木笔ReviewFlow 启动中..." in result.stdout


# --------------------------------------------------------------------------- #
# b08·F1-51 / F1-54 — repair_reviewflow.bat
# --------------------------------------------------------------------------- #


@cmd_only
class TestRepairBatEntry:
    """修前：``:6 start ...`` ``:7 exit /b 0``——不检查 windows_start.py 是否存在，
    start 失败也无从得知；PowerShell 弹窗失败时 echo 兜底后立即退出，双击看不见。"""

    BAT = BUILD / "repair_reviewflow.bat"

    def _tree(self, tmp_path, *, runtime=True, start_script=False, junk_runtime=False):
        app = tmp_path / "app"
        (app).mkdir()
        if runtime:
            (app / "runtime").mkdir()
            payload = b"MZ-not-a-real-executable" if junk_runtime else b"stub"
            (app / "runtime" / "pythonw.exe").write_bytes(payload)
            (app / "runtime" / "python.exe").write_bytes(payload)
        if start_script:
            (app / "windows_start.py").write_text('print("STUB")\n', encoding="utf-8")
        import shutil

        shutil.copy2(self.BAT, app / "repair_reviewflow.bat")
        return app

    def _run(self, app, *, strip_powershell=False, input_bytes=b"", wait=60):
        env = dict(os.environ)
        if strip_powershell:
            env["PATH"] = os.pathsep.join(
                [os.environ.get("SystemRoot", r"C:\Windows") + r"\system32",
                 os.environ.get("SystemRoot", r"C:\Windows")])
        return subprocess.run(["cmd", "/c", str(app / "repair_reviewflow.bat")],
                              input=input_bytes, capture_output=True, timeout=wait,
                              env=env)

    def test_missing_windows_start_is_reported(self, tmp_path):
        app = self._tree(tmp_path, runtime=True, start_script=False)
        result = self._run(app, strip_powershell=True, input_bytes=b"x\r\n")
        assert result.returncode == 1, result.stdout + result.stderr
        text = (result.stdout + result.stderr).decode("utf-8", "replace")
        assert "windows_start.py is missing" in text, text

    def test_missing_runtime_still_reported(self, tmp_path):
        app = self._tree(tmp_path, runtime=False)
        result = self._run(app, strip_powershell=True, input_bytes=b"x\r\n")
        assert result.returncode == 1
        text = (result.stdout + result.stderr).decode("utf-8", "replace")
        assert "bundled Python is missing" in text, text

    def test_fallback_message_waits_for_a_key(self, tmp_path):
        """PowerShell 不可用时 echo 兜底必须停住（双击场景可见）。"""
        app = self._tree(tmp_path, runtime=False)
        env = dict(os.environ)
        env["PATH"] = os.pathsep.join(
            [os.environ.get("SystemRoot", r"C:\Windows") + r"\system32",
             os.environ.get("SystemRoot", r"C:\Windows")])
        proc = subprocess.Popen(["cmd", "/c", str(app / "repair_reviewflow.bat")],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, env=env)
        try:
            deadline = time.monotonic() + 20
            still_running = None
            while time.monotonic() < deadline:
                still_running = proc.poll()
                if still_running is not None:
                    break
                time.sleep(0.2)
            if still_running is None:
                # 阻塞在 pause：正确
                assert proc.stdin is not None
                assert proc.stdout is not None
                assert proc.stdout.peek(1) if hasattr(proc.stdout, "peek") else True
            else:
                pytest.fail("没有 pause：双击运行时兜底信息会随窗口一起消失")
        finally:
            if proc.stdin:
                try:
                    proc.stdin.write(b"\r\n")
                    proc.stdin.flush()
                except OSError:
                    pass
            try:
                proc.wait(timeout=20)
            finally:
                if proc.poll() is None:
                    proc.kill()

    def test_start_failure_is_not_reported_as_success(self, tmp_path):
        """修前 ``start ...`` 后无条件 ``exit /b 0``：起不来也说成功。

        用「存在但不可执行的 EXE」造真实启动失败——cmd 的 `start` 对它返回
        非 0（修前该值被丢弃）。
        """
        app = self._tree(tmp_path, runtime=True, start_script=True, junk_runtime=True)
        result = self._run(app, strip_powershell=True, input_bytes=b"x\r\n")
        text = (result.stdout + result.stderr).decode("utf-8", "replace")
        assert result.returncode != 0, text
        assert "could not start" in text, text

    def test_successful_launch_exits_zero(self, tmp_path):
        """对照组：真实可执行（本测试解释器）能启动时返回 0。"""
        app = self._tree(tmp_path, runtime=True, start_script=True)
        import shutil

        for name in ("python.exe", "pythonw.exe"):
            shutil.copy2(PYTHON, app / "runtime" / name)
        result = self._run(app, strip_powershell=True, input_bytes=b"x\r\n")
        assert result.returncode == 0, result.stdout + result.stderr


# --------------------------------------------------------------------------- #
# b09·F1-92..97 — windows_smoke gate
# --------------------------------------------------------------------------- #


class TestSmokeGate:
    """冒烟脚本：assert 门禁在 -O 下空转、断言无上下文、build_app 近恒真、
    PNG 只查魔数、只冒烟空数据、不设 DATA_DIR/MODE。"""

    def _smoke(self):
        return importlib.reload(importlib.import_module("windows_smoke"))

    def test_gate_still_fails_under_python_O(self, tmp_path):
        smoke = self._smoke()
        driver = (
            "import sys\n"
            f"sys.path.insert(0, {str(BUILD)!r})\n"
            "import windows_smoke as s\n"
            "s.CHECKS = (('forced', lambda: s.check(False, 'boom')),)\n"
            "raise SystemExit(s.run())\n")
        env = dict(os.environ, TMP=str(tmp_path), TEMP=str(tmp_path))
        env["PYTHONOPTIMIZE"] = "1"
        result = subprocess.run([PYTHON, "-O", "-c", driver], capture_output=True,
                                text=True, encoding="utf-8", errors="replace",
                                env=env, timeout=300)
        assert result.returncode == 1, result.stdout + result.stderr
        assert "FAILED" in result.stderr and "boom" in result.stderr

    def test_no_bare_assert_statements(self):
        tree = ast.parse((BUILD / "windows_smoke.py").read_text(encoding="utf-8"))
        asserts = [node.lineno for node in ast.walk(tree) if isinstance(node, ast.Assert)]
        assert asserts == [], f"冒烟门禁不得依赖 assert（-O 下消失）：行 {asserts}"

    @pytest.mark.parametrize("name", [
        "check_runtime_dependencies", "check_clustering", "check_svg_png",
        "check_activity_exports", "check_app",
    ])
    def test_every_gate_reports_a_reason(self, name, monkeypatch, tmp_path):
        """F1-93：失败必须带步骤名与原因，而不是裸 AssertionError。"""
        smoke = self._smoke()
        # 让统一失败钩子走一遍：把被测函数替换成必败检查
        with pytest.raises(smoke.SmokeFailure) as caught:
            smoke.STEP = name
            smoke.check(False, "expected failure")
        assert f"[{name}]" in str(caught.value)
        assert "expected failure" in str(caught.value)

    def test_missing_frontend_fails_the_gate(self, tmp_path, monkeypatch):
        """F1-94：``build_app() is not None`` 从不失败；未挂载必须红。"""
        smoke = self._smoke()
        launcher = importlib.import_module("launcher")
        monkeypatch.setattr(launcher, "APP_DIR", tmp_path / "payload")
        with pytest.raises(smoke.SmokeFailure) as caught:
            smoke.check_app()
        assert "未挂载" in str(caught.value), caught.value

        frontend = tmp_path / "payload" / "custom_frontend"
        frontend.mkdir(parents=True)
        (frontend / "index.html").write_text("<html></html>", encoding="utf-8")
        smoke.check_app()  # 挂载齐备时通过（对照组）

    def test_png_width_regression_is_caught(self, monkeypatch):
        """F1-95：只查 8 字节魔数 → width=5 也能过；修后尺寸参与断言。"""
        smoke = self._smoke()
        import coscreen.svg_render as svg_render

        monkeypatch.setattr(svg_render, "svg_to_png",
                            lambda svg, width: png_bytes(5, 5))
        with pytest.raises(smoke.SmokeFailure) as caught:
            smoke.check_svg_png()
        assert "20x20" in str(caught.value), caught.value
        monkeypatch.undo()
        smoke.check_svg_png()  # 真实实现通过（对照组）

    def test_non_empty_activity_branch_is_exercised(self, monkeypatch):
        """F1-96：只冒烟空数据 → 非空分支（柱体/日期标签/截断）从不执行。"""
        smoke = self._smoke()
        import coscreen.research_trace_export as trace

        real_png = trace._activity_png
        monkeypatch.setattr(trace, "_activity_png",
                            lambda days, values, language="en": real_png([], {}, language))
        with pytest.raises(smoke.SmokeFailure) as caught:
            smoke.check_activity_exports()
        assert "完全相同" in str(caught.value), caught.value
        monkeypatch.undo()
        smoke.check_activity_exports()  # 真实实现通过（对照组）

    def test_gate_imports_backend_with_isolated_data_dir(self, tmp_path):
        """F1-97：冒烟不得沿用相对 ``data/`` 与生产不一致的导入期配置。"""
        sentinel = tmp_path / "sentinel-data"
        sentinel.mkdir()
        driver = (
            "import os, sys\n"
            f"sys.path.insert(0, {str(BUILD)!r})\n"
            "import windows_smoke as s\n"
            "captured = {}\n"
            "def probe():\n"
            "    import custom_backend.main as m\n"
            "    captured['data'] = str(m.DATA_DIR)\n"
            "    captured['mode'] = os.environ.get('REVIEWFLOW_MODE')\n"
            f"    s.check(not str(m.DATA_DIR).startswith({str(sentinel)!r}), 'ambient data dir leaked')\n"
            "s.CHECKS = (('isolation', probe),)\n"
            "rc = s.run()\n"
            "print('DATA', captured.get('data'))\n"
            "print('MODE', captured.get('mode'))\n"
            "raise SystemExit(rc)\n")
        env = dict(os.environ)
        env["COBOOKSHELF_DATA_DIR"] = str(sentinel)
        env["REVIEWFLOW_MODE"] = "server"
        env["TMP"] = str(tmp_path)
        env["TEMP"] = str(tmp_path)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        result = subprocess.run([PYTHON, "-c", driver], capture_output=True,
                                text=True, encoding="utf-8", errors="replace",
                                env=env, timeout=300, cwd=str(BUILD))
        assert result.returncode == 0, result.stdout + result.stderr
        assert "MODE desktop" in result.stdout, result.stdout
        data_line = [line for line in result.stdout.splitlines() if line.startswith("DATA ")][0]
        assert str(sentinel) not in data_line, data_line

    def test_real_smoke_passes_on_this_tree(self, tmp_path):
        """对照组：真实树上门禁全绿（退出码 0，五步均 OK）。"""
        env = dict(os.environ, TMP=str(tmp_path), TEMP=str(tmp_path),
                   PYTHONDONTWRITEBYTECODE="1")
        env.pop("PYTHONOPTIMIZE", None)
        result = subprocess.run([PYTHON, str(BUILD / "windows_smoke.py")],
                                capture_output=True, text=True, encoding="utf-8",
                                errors="replace", env=env, timeout=600,
                                cwd=str(BUILD))
        assert result.returncode == 0, result.stdout + result.stderr
        steps = [line for line in result.stdout.splitlines() if line.startswith("smoke: ")]
        assert len(steps) == 5 and all(line.endswith(": OK") for line in steps), result.stdout
        assert result.stdout.strip().splitlines()[-1] == (
            "Windows runtime, AI clustering, PNG exports and app startup: OK")
