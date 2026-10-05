#!/usr/bin/env python3
"""木笔ReviewFlow — 跨平台启动器（Path B：FastAPI 后端 + 静态前端）.

单个 uvicorn 进程同时服务 API 与前端静态文件：把 StaticFiles 挂载到现有
FastAPI 应用的 ``/`` 上（API 路由先注册、优先匹配），并自动打开浏览器。

数据目录解析（在导入 custom_backend 之前设置 ``COBOOKSHELF_DATA_DIR``，
因 main.py / auth.py 在导入时读取该环境变量）：

1. 环境变量 ``COBOOKSHELF_DATA_DIR`` 已设置 → 直接使用；
2. 仓库开发布局（launcher.py 位于 ``build/``）→ 用仓库根的 ``data/``；
3. 安装版使用平台用户数据目录
   （Linux: ``~/.local/share/ReviewFlow``；
    macOS: ``~/Library/Application Support/ReviewFlow``；
    Windows: ``%LOCALAPPDATA%/ReviewFlow``）。
"""
from __future__ import annotations

import os
import subprocess
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
DEFAULT_PORT = 8613


def find_free_port(start: int = DEFAULT_PORT) -> int:
    """从 start 起向后找 100 个可用端口，全占用则退回 start。"""
    for port in range(start, start + 100):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    return start


def resolve_data_dir() -> Path:
    """按模块 docstring 的优先级解析可写数据目录。"""
    env = os.environ.get("COBOOKSHELF_DATA_DIR")
    if env:
        return Path(env)

    return default_data_dir()


def default_data_dir() -> Path:
    """Resolve the repository or platform data folder without environment overrides."""

    # 仓库开发布局：launcher.py 在 build/ 里，包在仓库根。
    if APP_DIR.name == "build" and (APP_DIR.parent / "custom_backend").is_dir():
        return APP_DIR.parent / "data"

    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
        user = base / "ReviewFlow"
    elif sys.platform == "darwin":
        user = Path.home() / "Library" / "Application Support" / "ReviewFlow"
    else:
        xdg = os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))
        user = Path(xdg) / "ReviewFlow"
    user.mkdir(parents=True, exist_ok=True)
    return user


def acquire_process_lock():
    """Hold one exclusive desktop-app lock until Uvicorn exits."""
    from coscreen import local_storage

    path = local_storage.lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    try:
        if sys.platform == "win32":
            import msvcrt

            handle.seek(0)
            if not handle.read(1):
                handle.seek(0)
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, BlockingIOError) as exc:
        handle.close()
        raise RuntimeError("ReviewFlow is already running. Close the other window before starting it again.") from exc
    return handle


def open_browser(url: str) -> None:
    """Keep Linux desktop/browser diagnostics out of the backend error stream."""
    import shutil
    import subprocess

    command = shutil.which("xdg-open") if sys.platform.startswith("linux") else None
    if command:
        log_dir = resolve_data_dir()
        log_dir.mkdir(parents=True, exist_ok=True)
        with (log_dir / "browser-launch.log").open("ab") as log:
            subprocess.Popen([command, url], stdout=subprocess.DEVNULL, stderr=log,
                             start_new_session=True)
    else:
        webbrowser.open(url, new=2)


def reopen_running_app() -> bool:
    """A second shortcut click reopens the existing backend instead of starting two."""
    import json
    import urllib.request
    from coscreen import local_storage

    try:
        port = int((local_storage.control_dir() / "desktop-port.txt").read_text(encoding="ascii"))
        if not 1024 <= port <= 65535:
            return False
        url = f"http://127.0.0.1:{port}"
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(url + "/api/health", timeout=2) as response:
            status = json.load(response)
            if not isinstance(status, dict) or status.get("status") != "ok":
                return False
        open_browser(url)
        return True
    except (OSError, ValueError):
        return False


def release_process_lock(handle) -> None:
    try:
        if sys.platform == "win32":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


def wait_and_open(url: str, port: int) -> None:
    """等服务端端口就绪后打开浏览器（最多等 60 秒）。"""
    for _ in range(60):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(1.0)
                s.connect(("127.0.0.1", port))
                break
        except OSError:
            time.sleep(1)
    try:
        open_browser(url)
    except Exception:
        pass  # 无图形界面/无默认浏览器时静默忽略


def build_app():
    """导入 FastAPI 应用并把前端静态目录挂载到根路径。"""
    from fastapi.staticfiles import StaticFiles

    from custom_backend.main import app as api_app

    frontend_dir = APP_DIR / "custom_frontend"
    if not frontend_dir.is_dir() and APP_DIR.name == "build":
        frontend_dir = APP_DIR.parent / "custom_frontend"
    if frontend_dir.is_dir():
        # API 路由已注册在前，挂在 / 的静态目录只兜底其余路径；
        # 直接复用 api_app 以保留其异常处理器与中间件。
        api_app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="static")
    return api_app


def ensure_standard_streams(data_dir: Path | str) -> None:
    """pythonw 没有控制台；为 Uvicorn 提供可写的日志流。"""
    if sys.stdout is None or sys.stderr is None:
        log_dir = Path(data_dir)
        log_dir.mkdir(parents=True, exist_ok=True)
        log = (log_dir / "reviewflow.log").open("a", encoding="utf-8", buffering=1)
        if sys.stdout is None:
            sys.stdout = log
        if sys.stderr is None:
            sys.stderr = log


def _run_server(port: int) -> int:
    """Run one API/UI worker; the parent launcher owns locking and restarts."""
    app = build_app()
    from custom_backend import main as backend_main

    import uvicorn

    server = uvicorn.Server(uvicorn.Config(
        app,
        host=os.environ.get("REVIEWFLOW_HOST", "127.0.0.1"),
        port=port,
        log_level="warning",
    ))
    backend_main.configure_desktop_restart(lambda: setattr(server, "should_exit", True))
    server.run()
    return 0


def _spawn_server(port: int, restart_file: Path) -> subprocess.Popen:
    env = os.environ.copy()
    env["REVIEWFLOW_MODE"] = "desktop"
    env["REVIEWFLOW_PORT"] = str(port)
    env["REVIEWFLOW_RESTART_REQUEST_FILE"] = str(restart_file)
    env["REVIEWFLOW_RESTART_CHILD"] = "1"
    overlay = env.get("REVIEWFLOW_RUNTIME_OVERLAY")
    python_paths = [str(APP_DIR), str(APP_DIR.parent)]
    if overlay:
        python_paths.insert(0, overlay)
    if env.get("PYTHONPATH"):
        python_paths.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(python_paths)

    if getattr(sys, "frozen", False):
        command = [sys.executable, "--restart-child"]
    else:
        command = [sys.executable, str(Path(__file__).resolve()), "--restart-child"]
    log_path = Path(env["COBOOKSHELF_DATA_DIR"]) / "reviewflow.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("ab") as log:
        return subprocess.Popen(command, cwd=APP_DIR, env=env, stdin=subprocess.DEVNULL,
                                stdout=log, stderr=log,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def main() -> int:
    # 包内布局：launcher.py 与 custom_backend/ coscreen/ 同级；仓库布局：包在上一级。
    for p in (os.environ.get("REVIEWFLOW_RUNTIME_OVERLAY"), str(APP_DIR), str(APP_DIR.parent)):
        if p and p not in sys.path:
            sys.path.insert(0, p)

    if os.environ.get("REVIEWFLOW_MODE") == "server":
        os.environ.pop("COBOOKSHELF_DATA_DIR", None)
    os.environ["REVIEWFLOW_MODE"] = "desktop"
    child_mode = "--restart-child" in sys.argv[1:]

    from coscreen import local_storage

    explicit_config = bool(os.environ.get(local_storage.CONFIG_ENV))
    external_data = os.environ.get("COBOOKSHELF_DATA_DIR")
    migration_enabled = explicit_config or not external_data
    if migration_enabled:
        os.environ[local_storage.CONFIG_ENV] = str(local_storage.config_path())
    os.environ[local_storage.ENABLED_ENV] = "1" if migration_enabled else "0"

    process_lock = None
    try:
        if not child_mode:
            try:
                process_lock = acquire_process_lock()
            except RuntimeError:
                if reopen_running_app():
                    return 0
                raise
        default = default_data_dir()
        if migration_enabled and not child_mode:
            queued = local_storage._read_config().get("pending")
            if os.name == "nt" and queued:
                import ctypes

                ctypes.windll.user32.MessageBoxW(
                    None,
                    "即将迁移研究数据。文件较多时可能需要几分钟；完成后会打开应用。请勿重复启动。",
                    "木笔ReviewFlow",
                    0x40,
                )
            data_dir = local_storage.initialize_data_dir(default, prefer_config=True)
        else:
            data_dir = Path(external_data).expanduser().resolve()
        os.environ["COBOOKSHELF_DATA_DIR"] = str(data_dir)
        ensure_standard_streams(data_dir)

        instance_port = local_storage.control_dir() / "desktop-port.txt"
        if child_mode:
            return _run_server(int(os.environ.get("REVIEWFLOW_PORT", DEFAULT_PORT)))

        port = find_free_port(int(os.environ.get("REVIEWFLOW_PORT", DEFAULT_PORT)))
        restart_file = local_storage.control_dir() / "desktop-restart.request"
        restart_file.unlink(missing_ok=True)
        instance_port.write_text(str(port), encoding="ascii")
        port_file = os.environ.get("REVIEWFLOW_PORT_FILE")
        if port_file:
            port_path = Path(port_file)
            port_path.parent.mkdir(parents=True, exist_ok=True)
            port_path.write_text(str(port), encoding="ascii")

        url = f"http://127.0.0.1:{port}"
        print(f"木笔ReviewFlow 启动中... 浏览器将自动打开 {url}")
        print(f"数据目录: {data_dir}")
        print("按 Ctrl+C 停止")
        child = _spawn_server(port, restart_file)
        threading.Thread(target=wait_and_open, args=(url, port), daemon=True).start()
        while True:
            child.wait()
            if not restart_file.exists():
                break
            restart_file.unlink(missing_ok=True)
            child = _spawn_server(port, restart_file)
        return child.returncode or 0
    except RuntimeError as exc:
        if os.name == "nt":
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, str(exc), "木笔ReviewFlow", 0x10)
        else:
            print(str(exc), file=sys.stderr)
        return 1
    finally:
        if process_lock is not None:
            try:
                (local_storage.control_dir() / "desktop-port.txt").unlink(missing_ok=True)
            except OSError:
                pass  # A stale port is checked against health before it is reused.
            finally:
                release_process_lock(process_lock)


if __name__ == "__main__":
    sys.exit(main())
