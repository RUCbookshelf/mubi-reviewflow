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
import re
import subprocess
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
DEFAULT_PORT = 8613
PORT_SEARCH_LIMIT = 100
#: 单实例探测：/api/runtime 的 instance_id 形状（custom_backend/main.py `uuid4().hex`）
_INSTANCE_ID = re.compile(r"^[0-9a-f]{32}$")
#: `wait_and_open` 的等待上限（秒）——docstring 与实现同口径
READY_TIMEOUT = 60.0


def normalize_port(value: object, default: int = DEFAULT_PORT) -> int:
    """把 REVIEWFLOW_PORT 之类的输入收敛到合法端口；0/越界/非数字回落默认。"""
    try:
        port = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    if not 1 <= port <= 65535:
        return default
    return port


def _bind_host() -> str:
    """The host the backend actually binds (same default as `_run_server`)."""
    return os.environ.get("REVIEWFLOW_HOST", "127.0.0.1")


def _client_host() -> str:
    """A loopback address that can reach a backend bound to `_bind_host()`."""
    host = _bind_host().strip()
    if host in {"", "0.0.0.0", "::", "[::]", "*"}:
        return "127.0.0.1"
    if host in {"localhost", "::1"}:
        return host
    return host


def _url_host(host: str) -> str:
    return f"[{host}]" if ":" in host else host


def _bind_probe_socket(host: str, port: int) -> socket.socket:
    """Bind (not listen) *port* on *host* so the probe matches the real bind."""
    infos = socket.getaddrinfo(host or None, port, type=socket.SOCK_STREAM,
                               flags=socket.AI_PASSIVE)
    family, socktype, proto, _canon, sockaddr = infos[0]
    sock = socket.socket(family, socktype, proto)
    try:
        sock.bind(sockaddr)
    except OSError:
        sock.close()
        raise
    return sock


def _ephemeral_port() -> int:
    """端口段全部被占用时让系统分配一个空闲端口（仍是“可用端口”而非必败端口）。"""
    with _bind_probe_socket(_bind_host(), 0) as s:
        return int(s.getsockname()[1])


def find_free_port(start: int = DEFAULT_PORT) -> int:
    """从 start 起向后找可用端口（与后端实际绑定的 host 同口径探测）。

    端口段全被占用时不再把必被占用的 start 当结果返回，而是先让系统分配一个
    空闲端口；连系统分配都失败才抛 RuntimeError（调用方给用户明确提示）。
    """
    start = normalize_port(start)
    for port in range(start, min(start + PORT_SEARCH_LIMIT, 65536)):
        try:
            _bind_probe_socket(_bind_host(), port).close()
            return port
        except OSError:
            continue
    try:
        return _ephemeral_port()
    except OSError as exc:
        raise RuntimeError(
            "No local TCP port is available for ReviewFlow. Close other programs and try again."
        ) from exc


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
        # 空/相对 LOCALAPPDATA（b06·F1-103 的同型）会解析成相对路径；与
        # windows_start.user_root() 保持同一口径，落回用户配置目录。
        raw = (os.environ.get("LOCALAPPDATA") or "").strip()
        base = Path(raw) if raw and Path(raw).is_absolute() else Path.home() / "AppData" / "Local"
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


def read_instance_port() -> int | None:
    """Read the port a previous desktop instance recorded, if any."""
    from coscreen import local_storage

    try:
        port = int((local_storage.control_dir() / "desktop-port.txt")
                   .read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        return None
    return port if 1024 <= port <= 65535 else None


def record_port(instance_port: Path, port: int) -> None:
    """Publish the active port to the instance file and REVIEWFLOW_PORT_FILE."""
    try:
        instance_port.parent.mkdir(parents=True, exist_ok=True)
        instance_port.write_text(str(port), encoding="ascii")
    except OSError:
        pass
    port_file = os.environ.get("REVIEWFLOW_PORT_FILE")
    if port_file:
        try:
            port_path = Path(port_file)
            port_path.parent.mkdir(parents=True, exist_ok=True)
            port_path.write_text(str(port), encoding="ascii")
        except OSError:
            pass


def probe_running_instance(port: int | None, timeout: float = 2.0) -> str | None:
    """Return the base URL when *port* serves a live ReviewFlow backend.

    A bare TCP connect is not enough: any other program listening on the same
    port would satisfy it and the launcher would open a stranger's page (or
    treat an orphaned backend as absent and start a second, double-writing
    instance). ReviewFlow's own `/api/health` plus the `instance_id` shape that
    `/api/runtime` reports identify our backend.
    """
    import json
    import urllib.request

    if not port:
        return None
    url = f"http://{_url_host(_client_host())}:{port}"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url + "/api/health", timeout=timeout) as response:
            status = json.load(response)
        if not isinstance(status, dict) or status.get("status") != "ok":
            return None
        with opener.open(url + "/api/runtime", timeout=timeout) as response:
            runtime = json.load(response)
    except (OSError, ValueError):
        return None
    if not isinstance(runtime, dict):
        return None
    if not _INSTANCE_ID.match(str(runtime.get("instance_id", ""))):
        return None
    return url


def reopen_running_app() -> bool:
    """A second shortcut click reopens the existing backend instead of starting two."""
    url = probe_running_instance(read_instance_port())
    if not url:
        return False
    open_browser(url)
    return True


def reuse_or_reopen_running_app() -> bool:
    """With the process lock free, still prefer a live orphaned backend.

    The lock is released when its holder dies, but the backend child it spawned
    keeps serving until it exits on its own. Treating "lock free" as "nothing
    running" is what started a second backend against the same data folder.
    """
    url = probe_running_instance(read_instance_port())
    if not url:
        return False
    open_browser(url)
    return True


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
    """等服务端端口就绪后打开浏览器（最多等 READY_TIMEOUT 秒）。

    就绪判定用 `probe_running_instance`（/api/health + /api/runtime 的
    instance_id），不只看 TCP 能否连上：同端口上别的程序也能接受连接，旧实现
    会把陌生页面当成 ReviewFlow 打开。等待按整段 deadline 计时，不再把
    connect 超时与 sleep 各算 1 秒（旧实现实际最坏 120 秒）。
    """
    deadline = time.monotonic() + READY_TIMEOUT
    while time.monotonic() < deadline:
        if probe_running_instance(port, timeout=1.0):
            break
        time.sleep(1)
    try:
        open_browser(url)
    except Exception:
        pass  # 无图形界面/无默认浏览器时静默忽略


def report_child_failure(returncode: int, log_path: Path) -> None:
    """Surface a backend child that died instead of returning silently.

    A uvicorn bind failure (or any other startup crash) used to make the parent
    exit with the child's code and no dialog: the user saw a window that never
    appeared and no reason. The child's own output is already in the log file.
    """
    _notify_error(
        f"ReviewFlow 后端进程异常退出（退出码 {returncode}）。\n"
        f"请把日志提供给维护者：\n{log_path}")


def _notify_error(text: str) -> None:
    if os.name == "nt":
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, text, "木笔ReviewFlow", 0x10)
            return
        except Exception:
            pass
    _say(text, error=True)


def build_app():
    """导入 FastAPI 应用并把前端静态目录挂载到根路径。"""
    from fastapi.staticfiles import StaticFiles

    class CachedStaticFiles(StaticFiles):
        async def __call__(self, scope, receive, send):
            # 2.1.5 W1：未知的 /api/ 路径不得落到 SPA 静态兜底（StaticFiles
            # 对非 GET/HEAD 会回 405）。已删除的 API 路由（如 screening-rounds）
            # 必须表现为未注册——统一 404，而不是让旧前端收到误导性的 405。
            if (scope.get("type") == "http"
                    and str(scope.get("path") or "").startswith("/api/")):
                from starlette.responses import JSONResponse
                response = JSONResponse({"detail": "Not Found"}, status_code=404)
                await response(scope, receive, send)
                return
            await super().__call__(scope, receive, send)

        def file_response(self, full_path, stat_result, scope, status_code=200):
            response = super().file_response(full_path, stat_result, scope, status_code)
            # Conditional 304 responses otherwise lose the resolved filename.
            response.path = full_path
            return response

        async def get_response(self, path, scope):
            response = await super().get_response(path, scope)
            filename = Path(getattr(response, "path", "")).name
            response.headers["Cache-Control"] = (
                "no-cache" if filename in {"index.html", "404.html"}
                else "public, max-age=86400")
            return response

    from custom_backend.main import app as api_app

    frontend_dir = APP_DIR / "custom_frontend"
    if not frontend_dir.is_dir() and APP_DIR.name == "build":
        frontend_dir = APP_DIR.parent / "custom_frontend"
    if frontend_dir.is_dir():
        # API 路由已注册在前，挂在 / 的静态目录只兜底其余路径；
        # 直接复用 api_app 以保留其异常处理器与中间件。
        api_app.mount("/", CachedStaticFiles(directory=str(frontend_dir), html=True), name="static")
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
        # U5-P5：同源直连部署，不信任 X-Forwarded-For——uvicorn 默认
        # proxy_headers=True 会对环回对端用客户端可伪造的 XFF 改写
        # request.client.host，登录/注册的按 IP 限流可被无限轮换绕过。
        proxy_headers=False,
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


def _prepare_console() -> None:
    """Keep startup prints from crashing on a non-UTF-8 code page.

    `pythonw` has no console, but when the launcher runs from a terminal or a
    build harness whose stdout is redirected with a cp1252/latin-1 code page,
    printing the Chinese startup lines raised UnicodeEncodeError before the
    backend ever started. Replace unencodable characters instead of aborting.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(errors="replace")
        except (ValueError, OSError):
            pass


def _say(text: str, *, error: bool = False) -> None:
    """Print without letting an encoding failure abort startup."""
    stream = sys.stderr if error else sys.stdout
    try:
        print(text, file=stream)
    except (UnicodeEncodeError, AttributeError, ValueError, OSError):
        try:
            print(text.encode("ascii", "replace").decode("ascii"),
                  file=sys.stderr if error else sys.stdout)
        except Exception:
            pass


def reset_user(username: str) -> int:
    """Issue a local one-use recovery code without starting or migrating the app.

    Read the same active data location as normal startup. Pending migrations
    must finish through normal startup before recovery can touch either copy.
    The code is written only to the invoking terminal, never to app log files.
    """
    from coscreen import local_storage

    name = str(username or "").strip()
    if not name:
        _say("Usage: ReviewFlow --reset-user USERNAME", error=True)
        return 2
    stream = sys.stdout
    terminal_stream = None
    try:
        # A macOS windowed bundle may have stdout=None even when invoked in
        # Terminal. Recover the inherited descriptor, without opening a log.
        if stream is None:
            terminal_stream = os.fdopen(os.dup(1), "w", encoding="utf-8")
            stream = terminal_stream
        stream.write("")
        stream.flush()
        external = os.environ.get("COBOOKSHELF_DATA_DIR")
        use_config = bool(os.environ.get(local_storage.CONFIG_ENV)) or not external
        config = local_storage._read_config() if use_config else {}
        if config.get("pending"):
            _say("Open ReviewFlow to complete the pending data move, then retry recovery.", error=True)
            return 1
        data_path = Path(config.get("current_path") or external or default_data_dir()).expanduser().resolve()
        if not data_path.is_dir():
            _say("Recovery unavailable: check the account and active data folder.", error=True)
            return 1
        os.environ["COBOOKSHELF_DATA_DIR"] = str(data_path)
        from custom_backend import auth, auth_recovery
        if auth.get_user(name) is None:
            _say("Recovery unavailable: check the account and active data folder.", error=True)
            return 1
        code = auth_recovery.issue_reset_code(name)
        if not code:
            _say("Recovery unavailable: the data folder could not store a recovery code.", error=True)
            return 1
        stream.write("One-use recovery code (valid for 15 minutes):\n" + code + "\n")
        stream.flush()
        return 0
    except Exception:
        _say("Recovery unavailable. Run this command in Terminal and check the active data folder.", error=True)
        return 1
    finally:
        if terminal_stream is not None:
            terminal_stream.close()


def main(process_lock=None) -> int:
    """Start the desktop app.

    *process_lock* is an already-held single-instance handle (windows_start
    takes the lock before it touches the runtime overlay, so repair and launch
    are one exclusive critical section). Without it the lock is acquired here.
    """
    _prepare_console()
    # 包内布局：launcher.py 与 custom_backend/ coscreen/ 同级；仓库布局：包在上一级。
    for p in (os.environ.get("REVIEWFLOW_RUNTIME_OVERLAY"), str(APP_DIR), str(APP_DIR.parent)):
        if p and p not in sys.path:
            sys.path.insert(0, p)

    # r13 tailpiece ①: a caller that asked for the team server mode must not be
    # silently downgraded to the desktop app. The old code popped the caller's
    # explicit COBOOKSHELF_DATA_DIR, rewrote REVIEWFLOW_MODE and started on the
    # desktop default directory with no warning at all - the team UI was gone
    # and the data "looked lost". Refuse instead, before any data-directory
    # resolution, and point at the supported server entry point.
    if os.environ.get("REVIEWFLOW_MODE") == "server":
        # windows_start.py takes the single-instance lock before calling main()
        # and hands it over, so an early return has to release it here.
        if process_lock is not None:
            release_process_lock(process_lock)
        _notify_error(
            "REVIEWFLOW_MODE=server 是团队服务模式：桌面启动器不会以团队模式运行，"
            "也不会改动你的数据目录。请按 docs/deployment/UBUNTU_SERVER.md 用 "
            "uvicorn 启动团队服务；如需启动桌面版，请先取消环境变量 "
            "REVIEWFLOW_MODE=server。")
        return 2
    os.environ["REVIEWFLOW_MODE"] = "desktop"
    if "--reset-user" in sys.argv[1:]:
        if len(sys.argv) != 3 or sys.argv[1] != "--reset-user":
            _say("Usage: ReviewFlow --reset-user USERNAME", error=True)
            return 2
        try:
            return reset_user(sys.argv[2])
        finally:
            if process_lock is not None:
                release_process_lock(process_lock)
    child_mode = "--restart-child" in sys.argv[1:]

    from coscreen import local_storage

    explicit_config = bool(os.environ.get(local_storage.CONFIG_ENV))
    external_data = os.environ.get("COBOOKSHELF_DATA_DIR")
    migration_enabled = explicit_config or not external_data
    if migration_enabled:
        os.environ[local_storage.CONFIG_ENV] = str(local_storage.config_path())
    os.environ[local_storage.ENABLED_ENV] = "1" if migration_enabled else "0"

    try:
        handed_off = False
        if not child_mode:
            if process_lock is None:
                try:
                    process_lock = acquire_process_lock()
                except RuntimeError:
                    if reopen_running_app():
                        return 0
                    raise
            # 锁空闲不等于没有后端：持锁的父进程崩溃后，它派生的 uvicorn 子进程
            # 仍可能在服务（孤儿）。旧实现只在锁被占时才探测，于是孤儿场景下
            # 无条件再起第二套后端 → 同一数据目录双写。这里先按实例身份探测。
            # 显式指定 COBOOKSHELF_DATA_DIR 的调用方（开发/测试）可能确实想并存
            # 另一套后端，因此只在标准桌面路径上复用孤儿实例。
            if not external_data and reuse_or_reopen_running_app():
                # 已把用户导向仍在服务的实例：它的端口记录必须留给下次启动。
                handed_off = True
                return 0
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
            return _run_server(normalize_port(os.environ.get("REVIEWFLOW_PORT")))

        requested = os.environ.get("REVIEWFLOW_PORT")
        port = find_free_port(normalize_port(requested))
        if requested is not None and str(normalize_port(requested)) != str(requested).strip():
            _say(f"忽略无效端口设置 REVIEWFLOW_PORT={requested!r}，改用 {port}", error=True)
        restart_file = local_storage.control_dir() / "desktop-restart.request"
        restart_file.unlink(missing_ok=True)
        record_port(instance_port, port)
        url = f"http://{_url_host(_client_host())}:{port}"
        _say(f"木笔ReviewFlow 启动中... 浏览器将自动打开 {url}")
        _say(f"数据目录: {data_dir}")
        _say("按 Ctrl+C 停止")
        child = _spawn_server(port, restart_file)
        threading.Thread(target=wait_and_open, args=(url, port), daemon=True).start()
        while True:
            child.wait()
            if not restart_file.exists():
                break
            action = restart_file.read_text(encoding="ascii").strip()
            restart_file.unlink(missing_ok=True)
            if action != "restart":
                break
            # 重启窗口里端口可能已被别的程序抢占：沿用旧端口会静默绑定失败。
            new_port = find_free_port(port)
            if new_port != port:
                port = new_port
                record_port(instance_port, port)
                url = f"http://{_url_host(_client_host())}:{port}"
                threading.Thread(target=wait_and_open, args=(url, port), daemon=True).start()
            child = _spawn_server(port, restart_file)
        if child.returncode:
            report_child_failure(child.returncode, Path(data_dir) / "reviewflow.log")
        return child.returncode or 0
    except RuntimeError as exc:
        _notify_error(str(exc))
        return 1
    finally:
        if process_lock is not None:
            try:
                if not handed_off:
                    (local_storage.control_dir() / "desktop-port.txt").unlink(missing_ok=True)
            except OSError:
                pass  # A stale port is checked against health before it is reused.
            finally:
                release_process_lock(process_lock)


if __name__ == "__main__":
    if sys.platform == "darwin" and sys.argv[1:] == ["--check-rasterizer"]:
        from coscreen.svg_render import svg_to_png

        png = svg_to_png(
            b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10">'
            b'<rect width="10" height="10" fill="red"/></svg>', 20)
        sys.exit(0 if png.startswith(b"\x89PNG\r\n\x1a\n") else 1)
    sys.exit(main())
