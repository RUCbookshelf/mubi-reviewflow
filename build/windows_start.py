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
MODULES = ('fastapi', 'uvicorn', 'multipart', 'jwt', 'passlib', 'bcrypt',
           'pandas', 'rispy', 'rapidfuzz', 'pypdf', 'numpy', 'scipy.stats',
           'statsmodels.api', 'sklearn.svm', 'docx', 'pptx', 'PIL.Image',
           'svglib.svglib', 'reportlab.graphics.renderPM', '_rl_renderPM')


def check_dependencies() -> list[str]:
    failures = []
    for name in MODULES:
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


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--check':
        if len(sys.argv) > 2:
            sys.path.insert(0, sys.argv[2])
        failures = check_dependencies()
        print(json.dumps(failures, ensure_ascii=False))
        raise SystemExit(bool(failures))
    else:
        raise SystemExit(main(force_repair='--repair' in sys.argv[1:]))
