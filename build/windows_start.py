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
    result = subprocess.run(command, cwd=APP, env={**os.environ, 'PYTHONIOENCODING':'utf-8'}, capture_output=True, text=True, encoding='utf-8',
                            errors='replace', creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), timeout=120)
    if result.stdout.strip():
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            pass
    return [result.stderr.strip() or f'Python 检测进程退出：{result.returncode}']


def main(force_repair: bool = False) -> int:
    os.chdir(APP)
    sys.path.insert(0, str(APP))
    runtime_python = APP / 'runtime' / 'python.exe'
    if sys.platform == 'win32' and not runtime_python.is_file():
        message('ReviewFlow bundled Python is missing. Reinstall ReviewFlow, then try repair again.')
        return 1
    root = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'ReviewFlow'
    root.mkdir(parents=True, exist_ok=True)
    log_path = root / 'startup_diagnostic.log'
    lock = APP / 'runtime-lock.txt'
    requirements = lock if lock.exists() else APP / 'requirements-app.txt'
    version = hashlib.sha256(requirements.read_bytes()).hexdigest()[:12]
    overlay = root / 'runtime-deps' / version
    repaired = False
    try:
        errors = probe(overlay if overlay.exists() else None)
        if errors or force_repair:
            if errors:
                reason = '检测到运行依赖缺失或损坏：\n' + '\n'.join(errors)[:1200]
                log_path.write_text('\n'.join(errors), encoding='utf-8')
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
                with log_path.open('a', encoding='utf-8') as log:
                    result = subprocess.run(command, cwd=APP, stdout=log, stderr=log,
                                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), timeout=900)
                if result.returncode or probe(target):
                    message(f'依赖修复未完成，请重新安装应用或查看日志：\n{log_path}')
                    return 1
                if overlay.exists():
                    shutil.rmtree(overlay)
                target.rename(overlay)
            repaired = True
            message('依赖修复完成，点击确定后启动 ReviewFlow。', error=False)
        marker = root / ('runtime-checked-' + version)
        if not marker.exists():
            if not errors and not repaired:
                message('运行环境检查通过。应用已自带 Python 和所需依赖，点击确定后启动。', error=False)
            marker.touch()
        if overlay.exists():
            sys.path.insert(0, str(overlay))
        from launcher import main as launch
        return launch()
    except Exception:
        with log_path.open('a', encoding='utf-8') as log:
            traceback.print_exc(file=log)
        message(f'ReviewFlow 未能启动。请将以下日志提供给维护者：\n{log_path}\n若运行环境被删除，请重新安装应用。')
        return 1


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--check':
        if len(sys.argv) > 2:
            sys.path.insert(0, sys.argv[2])
        failures = check_dependencies()
        print(json.dumps(failures, ensure_ascii=False))
        raise SystemExit(bool(failures))
    else:
        raise SystemExit(main(force_repair='--repair' in sys.argv[1:]))
