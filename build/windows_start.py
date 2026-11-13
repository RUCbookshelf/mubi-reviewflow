"""No VBS, global Python, or administrator rights required."""
from __future__ import annotations

import ctypes
import hashlib
import importlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import traceback

APP = Path(__file__).resolve().parent
COMMON_MODULES = ('fastapi', 'uvicorn', 'multipart', 'jwt', 'passlib', 'bcrypt',
                  'pandas', 'rispy', 'rapidfuzz', 'pypdf', 'numpy', 'scipy.stats',
                  'statsmodels.api', 'sklearn.svm', 'docx', 'pptx', 'PIL.Image')
MODULES = COMMON_MODULES + ('svglib.svglib', 'reportlab.graphics.renderPM', '_rl_renderPM')


def check_dependencies(modules=MODULES) -> list[str]:
    failures = []
    for name in modules:
        try:
            importlib.import_module(name)
        except Exception as exc:
            failures.append(f'{name}: {exc}')
    return failures


def message(text: str, *, confirm: bool = False, error: bool = True) -> bool:
    if sys.platform == 'win32':
        return ctypes.windll.user32.MessageBoxW(None, text, '木笔ReviewFlow', 0x24 if confirm else (0x10 if error else 0x40)) == 6
    print(text, file=sys.stderr)
    return False


def probe(target: Path | None = None) -> list[str]:
    python = APP / 'runtime' / 'python.exe'
    command = [str(python), str(APP / 'windows_start.py'), '--check']
    if target:
        command.append(str(target))
    # GLM3-01: a slow/cold machine can make the 120s probe deadline expire even
    # though the runtime is healthy. subprocess.TimeoutExpired used to escape
    # to the outer except in main(), which reported "ReviewFlow 未能启动 ...
    # 请重新安装应用" (reinstall) instead of the repair/retry branch. Catch it
    # here and return it as an ordinary finding so the user lands in the
    # repair flow with an honest reason and can simply try again.
    try:
        result = subprocess.run(command, cwd=APP, env={**os.environ, 'PYTHONIOENCODING':'utf-8'}, capture_output=True, text=True, encoding='utf-8',
                                errors='replace', creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), timeout=120)
    except subprocess.TimeoutExpired:
        return ['依赖检测超时（120 秒未完成）：机器可能正忙，请关闭其他程序后重试；若持续超时请重新安装或联系维护者。']
    return probe_findings(result.stdout, result.stderr, result.returncode)


def probe_findings(stdout: str | None, stderr: str | None, returncode: int) -> list[str]:
    """Interpret one `--check` run, using BOTH the payload and the exit code.

    `--check` exits non-zero exactly when it found dependency failures, so the
    exit code is authoritative. The old code ignored it: a successful run whose
    stdout was not JSON (e.g. a stray banner) was reported as the failure
    "Python 检测进程退出：0", while a non-zero exit with an empty JSON list
    read as success.
    """
    text = (stdout or '').strip()
    payload = None
    if text:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            payload = None
    if isinstance(payload, list):
        findings = [str(item) for item in payload]
        if findings:
            return findings
        if returncode == 0:
            return []
    elif returncode == 0:
        # Successful exit with a non-JSON payload: the run itself is fine.
        return []
    detail = (stderr or '').strip() or text[-800:]
    if detail:
        return [f'Python 检测进程退出：{returncode}\n{detail}']
    return [f'Python 检测进程退出：{returncode}']


def append_log(log_path: Path, text: str) -> None:
    """Append one UTF-8 record to the diagnostic log.

    The log used to mix a UTF-8 text layer (write_text/print_exc) with child
    process bytes written straight into the same handle. On a GBK console that
    produced a file that failed UTF-8 decoding at the first Chinese message.
    The log is now byte-oriented and every writer is pinned to UTF-8.
    """
    with log_path.open('ab') as log:
        log.write(text.encode('utf-8', 'replace'))


def user_root() -> Path:
    """%LOCALAPPDATA%\\ReviewFlow, never a path relative to the install folder.

    An empty or relative LOCALAPPDATA (seen on stripped Windows images, and
    whenever LOCALAPPDATA is exported as "") made ``Path('') / 'ReviewFlow'``
    a relative path: the log/overlay landed under the install directory (or
    whatever the cwd was) instead of the user profile.
    """
    raw = (os.environ.get('LOCALAPPDATA') or '').strip()
    base = Path(raw) if raw and Path(raw).is_absolute() else Path.home() / 'AppData' / 'Local'
    return base / 'ReviewFlow'


def main(force_repair: bool = False) -> int:
    os.chdir(APP)
    sys.path.insert(0, str(APP))
    runtime_python = APP / 'runtime' / 'python.exe'
    if sys.platform == 'win32' and not runtime_python.is_file():
        message('ReviewFlow bundled Python is missing. Reinstall ReviewFlow, then try repair again.')
        return 1
    root = user_root()
    log_path = root / 'startup_diagnostic.log'
    repaired = False
    process_lock = None
    try:
        root.mkdir(parents=True, exist_ok=True)
        lock = APP / 'runtime-lock.txt'
        requirements = lock if lock.exists() else APP / 'requirements-app.txt'
        version = hashlib.sha256(requirements.read_bytes()).hexdigest()[:12]
        overlay = root / 'runtime-deps' / version
        # b01·F1-100: take the desktop single-instance lock BEFORE touching the
        # overlay. Repair rmtree's/renames `<root>/runtime-deps/<version>`,
        # which a running instance has on sys.path with its native DLLs loaded
        # -> PermissionError (partial delete) or FileExistsError on rename.
        # Holding the same lock the launcher would take makes the repair
        # exclusive; the lock is handed to launcher.main() further down.
        from launcher import acquire_process_lock, release_process_lock, reopen_running_app

        try:
            process_lock = acquire_process_lock()
        except RuntimeError:
            if reopen_running_app():
                return 0
            message('ReviewFlow 已在运行。请先关闭已打开的窗口，再重新运行“检查与修复”。')
            return 1
        # 锁空闲也可能仍有后端在服务：持锁的父进程崩溃后，它派生的 uvicorn
        # 子进程会一直跑（孤儿），并把 `<root>/runtime-deps/<version>` 挂在
        # sys.path 上、DLL 已加载。此时修复的 rmtree/rename 仍是同一个竞态，
        # 因此先按实例身份探活，命中就只把窗口交还给那个实例。
        # 显式指定 COBOOKSHELF_DATA_DIR 的调用方（开发/测试）要的是自己那套数据，
        # 不能被另一数据目录的实例接管——这与 launcher 的口径一致。
        if not os.environ.get('COBOOKSHELF_DATA_DIR') and reopen_running_app():
            return 0
        errors = probe(overlay if overlay.exists() else None)
        if errors or force_repair:
            if errors:
                reason = '检测到运行依赖缺失或损坏：\n' + '\n'.join(errors)[:1200]
                append_log(log_path, '\n'.join(errors) + '\n')
            else:
                reason = '当前依赖检查通过。此操作将重新安装 ReviewFlow 运行依赖。'
            offline = (APP / 'wheelhouse').is_dir()
            prompt = (reason + '\n\n是否修复？' +
                      ('将使用安装包自带的依赖文件，无需联网。' if offline else '需要联网下载依赖。') +
                      '\n文件只安装到当前用户的 ReviewFlow 目录，不修改系统 Python。\n修复可能需要几分钟。')
            if not message(prompt, confirm=True):
                return 1
            overlay.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix='repair-', dir=overlay.parent) as temporary:
                target = Path(temporary) / 'packages'
                command = [str(APP / 'runtime' / 'python.exe'), '-m', 'pip', 'install',
                           '--disable-pip-version-check', '--no-warn-script-location',
                           '--target', str(target), '-r', str(requirements)]
                if offline:
                    command += ['--no-index', '--find-links', str(APP / 'wheelhouse')]
                append_log(log_path, f"\n--- dependency repair {version} ---\n")
                child_env = {**os.environ, 'PYTHONIOENCODING': 'utf-8',
                             'PYTHONUTF8': '1'}
                with log_path.open('ab') as log:
                    result = subprocess.run(command, cwd=APP, env=child_env,
                                            stdout=log, stderr=log,
                                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), timeout=900)
                if result.returncode or probe(target):
                    message(f'依赖修复未完成，请重新安装应用或查看日志：\n{log_path}')
                    return 1
                replace_overlay(overlay, target)
            repaired = True
            message('依赖修复完成，点击确定后启动 ReviewFlow。', error=False)
        marker = root / ('runtime-checked-' + version)
        if not marker.exists():
            if not errors and not repaired:
                message('运行环境检查通过。应用已自带 Python 和所需依赖，点击确定后启动。', error=False)
            marker.touch()
        if overlay.exists():
            sys.path.insert(0, str(overlay))
            os.environ['REVIEWFLOW_RUNTIME_OVERLAY'] = str(overlay)
        from launcher import main as launch
        handed_lock, process_lock = process_lock, None
        return launch(process_lock=handed_lock)
    except Exception:
        try:
            append_log(log_path, traceback.format_exc())
        except OSError:
            pass
        message(f'ReviewFlow 未能启动。请将以下日志提供给维护者：\n{log_path}\n若运行环境被删除，请重新安装应用。')
        return 1
    finally:
        if process_lock is not None:
            release_process_lock(process_lock)


def replace_overlay(overlay: Path, target: Path) -> None:
    """Install the freshly built overlay over any previous one.

    A locked/undeletable previous overlay (a running app, an AV handle) used to
    surface as FileExistsError from `target.rename(overlay)`. Move the stale
    tree aside first so the new one always lands at the canonical path. If the
    stale tree really is locked, say so instead of leaving a half-deleted
    overlay behind: the failed rename must not lose the repair result.
    """
    if overlay.exists():
        shutil.rmtree(overlay, ignore_errors=True)
    if overlay.exists():
        stale = overlay.with_name(f'{overlay.name}-stale-{os.getpid()}')
        try:
            overlay.rename(stale)
        except OSError as exc:
            raise RuntimeError(
                f'旧依赖目录无法替换（可能仍被占用）：{overlay}：{exc}') from exc
        shutil.rmtree(stale, ignore_errors=True)
    target.rename(overlay)


def _copy_to_clipboard_windows(text: str) -> bool:
    """把文本放入系统剪贴板（纯 ctypes，无第三方依赖；2026-11-11 用户实测
    反馈：MessageBox 文本不可选中，手打 32 字符码太苦）。失败返回 False，
    调用方回落为仅展示。"""
    if sys.platform != 'win32':
        return False
    try:
        u32 = ctypes.windll.user32
        k32 = ctypes.windll.kernel32
        CF_UNICODETEXT = 13
        GMEM_MOVEABLE = 0x0002
        if not u32.OpenClipboard(None):
            return False
        try:
            u32.EmptyClipboard()
            size = (len(text) + 1) * ctypes.sizeof(ctypes.c_wchar)
            handle = k32.GlobalAlloc(GMEM_MOVEABLE, size)
            if not handle:
                return False
            ptr = k32.GlobalLock(handle)
            if not ptr:
                return False
            try:
                ctypes.memmove(ptr, ctypes.create_unicode_buffer(text), size)
            finally:
                k32.GlobalUnlock(handle)
            if not u32.SetClipboardData(CF_UNICODETEXT, handle):
                k32.GlobalFree(handle)
                return False
            return True  # 成功后系统接管 handle，勿再释放
        finally:
            u32.CloseClipboard()
    except Exception:
        return False


def _resolve_installed_data_dir() -> Path:
    """解析本机真实数据目录（authb 复审 M7）。

    安装版（installer.iss {app}=%LOCALAPPDATA%\\Programs\\ReviewFlow）真实
    数据目录=%LOCALAPPDATA%\\ReviewFlow（launcher.py:130/536 同口径）——
    此前工具回落相对 "data" 按 CWD 解析成安装目录影子库（真用户取不到码、
    影子库用户反而能取）。优先级：环境变量（开发/便携）→ 平台默认目录。
    """
    env = os.environ.get('COBOOKSHELF_DATA_DIR')
    if env:
        return Path(env)
    if sys.platform == 'win32':
        base = os.environ.get('LOCALAPPDATA') or os.path.expanduser('~/AppData/Local')
    else:
        base = os.environ.get('XDG_DATA_HOME') or os.path.expanduser('~/.local/share')
    return Path(base) / 'ReviewFlow'


def reset_user(username: str) -> int:
    """找回密码·本机取码（2.1.5）：签发一枚 15 分钟一次性重置码，
    自动复制到剪贴板并弹窗展示。

    authb（复审 M7/L25/L26）：数据目录按安装布局解析（不再 CWD 影子库）；
    get_user/issue 的异常兜底成弹窗（pythonw 无控制台，裸异常=无声死）；
    sys.path 安装目录排前（APP 在 APP.parent 之前——用户可写的 Programs
    目录同名包不得遮蔽安装目录内的真实现）。用户名 strip（码库同键）。
    """
    install_dir = str(APP)
    parent_dir = str(APP.parent)
    for entry in (install_dir, parent_dir):
        if entry not in sys.path:
            sys.path.insert(0, entry)
        elif sys.path.index(entry) > 1:
            sys.path.remove(entry)
            sys.path.insert(0, entry)
    try:
        os.environ['COBOOKSHELF_DATA_DIR'] = str(_resolve_installed_data_dir())
        from custom_backend import auth, auth_recovery
    except Exception as exc:  # 打包布局差异/依赖缺失：给可操作的提示
        message(f"重置码生成失败：无法加载应用模块（{exc}）。\n"
                f"请从安装目录运行本工具，或联系维护者。")
        return 1
    name = str(username or '').strip()
    try:
        user = auth.get_user(name)
        if user is None:
            # 中性提示：不区分「不存在」与「目录不对」。
            message(f"未能为「{name}」生成重置码：用户名不存在或数据目录不对。")
            return 1
        code = auth_recovery.issue_reset_code(name)
    except Exception as exc:  # authb L25：库锁/损坏等——pythonw 下无处可去
        message(f"重置码生成失败：{exc}")
        return 1
    if code is None:
        message("重置码生成失败：无法写入数据目录。")
        return 1
    copied = _copy_to_clipboard_windows(code)
    hint = ("已复制到剪贴板，直接在重置面板 Ctrl+V 粘贴即可。"
            if copied else "请手动完整复制下面的重置码。")
    message(f"用户「{name}」的重置码（15 分钟内有效、一次性）：\n\n"
            f"{code}\n\n{hint}", error=False)
    print(code, file=sys.stderr)  # 无 GUI/重定向场景的第二通道
    return 0


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--check':
        if len(sys.argv) > 2:
            sys.path.insert(0, sys.argv[2])
        failures = check_dependencies()
        print(json.dumps(failures, ensure_ascii=False))
        raise SystemExit(bool(failures))
    elif len(sys.argv) > 1 and sys.argv[1] == '--reset-user':
        if len(sys.argv) < 3:
            print('用法: windows_start.py --reset-user <用户名>', file=sys.stderr)
            raise SystemExit(2)
        raise SystemExit(reset_user(sys.argv[2]))
    else:
        raise SystemExit(main(force_repair='--repair' in sys.argv[1:]))
