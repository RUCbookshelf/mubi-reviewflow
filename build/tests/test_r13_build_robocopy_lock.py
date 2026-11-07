"""r13 build-0 nail: the UNC wrapper's ``robocopy /E /PURGE`` must not delete
the build lock it is holding.

RED at baseline (1d23f22): ``build_windows.bat:91-92`` creates
``%BUILDROOT%\\build\\.build-windows.lock`` and takes it before relocating, then
line 107 purges the destination directory with::

    robocopy "%SRCDIR%" "%BUILDROOT%\\build" /E /PURGE ...
        /XD __pycache__ .pytest_cache staging appdir Output downloads ...

``%SRCDIR%`` never contains the lock, so ``/PURGE`` treats it as "extra in the
destination" and deletes it (and its ``pid`` file) - reproduced with the probe
``E:/rf214tmp/dwave/build/probe_a_robocopy_purge_lock.bat``::

    *多余目录  -1  ...\\dst\\build\\.build-windows.lock\\
    *多余文件   6  ...\\dst\\build\\.build-windows.lock\\pid
    robocopy_exit=3
    RESULT=LOCK_DELETED
    RESULT=PIDFILE_GONE

So the F1-3 mutex is gone for the rest of the relocation/build, exactly when the
inner build starts and a second invocation is most likely.

Fix: add ``.build-windows.lock`` to the ``/XD`` list (robocopy honours /XD for
deletion too). The behaviour nail below replays the real flags lifted from the
script, so it goes red again if the exclusion is dropped.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
WINBAT = REPO / "build" / "build_windows.bat"
ROBOCOPY = shutil.which("robocopy") if sys.platform == "win32" else None
LOCK_NAME = ".build-windows.lock"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _wrapper_robocopy_line(text: str) -> str:
    """The relocation copy: source tree -> %BUILDROOT%\\build."""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("robocopy") and '%BUILDROOT%\\build"' in stripped:
            return stripped
    raise AssertionError("the UNC relocation robocopy call is missing")


def _excluded_dirs(line: str) -> list[str]:
    tokens = line.split()
    start = tokens.index("/XD")
    names: list[str] = []
    for token in tokens[start + 1:]:
        if token.startswith("/"):
            break
        names.append(token)
    return names


# --------------------------------------------------------------------------- #
# static: the purge must exclude the lock, and nothing else may eat it
# --------------------------------------------------------------------------- #


def test_unc_purge_excludes_the_build_lock_directory():
    """RED at baseline: /XD listed staging/appdir/Output/downloads only."""
    line = _wrapper_robocopy_line(_read(WINBAT))
    assert "/PURGE" in line, "the relocation must still purge stale files"
    assert LOCK_NAME in _excluded_dirs(line), (
        "the lock directory is created before this copy, so /PURGE deletes it "
        "unless /XD excludes it: " + line
    )


def test_lock_is_taken_before_the_purge_runs():
    """Precondition of the bug: the wrapper acquires the lock first, then
    purges the same directory."""
    text = _read(WINBAT)
    lock_create = text.index(f'set "LOCKDIR=%BUILDROOT%\\build\\{LOCK_NAME}"')
    purge = text.index(_wrapper_robocopy_line(text))
    assert lock_create < purge, "the lock must be held across the relocation"


def test_no_rmdir_removes_the_build_root_or_the_lock_it_holds():
    """A later cleanup step must not undo the /XD exclusion."""
    text = _read(WINBAT)
    release = text[text.index("\n:release_lock\n"):text.index("\n:fetch_verified\n")]
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped.lower().startswith(("rmdir", "rd ")):
            continue
        if LOCK_NAME in stripped or "%LOCKDIR%" in stripped:
            assert stripped in release, (
                f"build_windows.bat:{number} removes the lock outside "
                f":release_lock: {stripped}"
            )
        assert not stripped.lower().startswith('rmdir /s /q "%buildroot%'), (
            f"build_windows.bat:{number} deletes the whole relocated build root"
        )


# --------------------------------------------------------------------------- #
# behaviour: replay the script's own robocopy flags against a real directory
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(ROBOCOPY is None, reason="robocopy not available")
def test_purge_keeps_the_lock_and_its_pid(tmp_path):
    """The exact probe A replay, with the flags lifted from the real script."""
    line = _wrapper_robocopy_line(_read(WINBAT))
    flags = line.split()[3:]  # drop "robocopy", source and destination

    src = tmp_path / "src"
    dst = tmp_path / "dst" / "build"
    src.mkdir(parents=True)
    dst.mkdir(parents=True)
    (src / "installer.nsi").write_text("x", encoding="ascii")
    (src / "launcher.py").write_text("x", encoding="ascii")
    lock = dst / LOCK_NAME
    lock.mkdir()
    (lock / "pid").write_text("4242\r\n", encoding="ascii")

    proc = subprocess.run([ROBOCOPY, str(src), str(dst), *flags],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=180)
    assert proc.returncode < 8, proc.stdout + proc.stderr
    assert lock.is_dir(), "the relocation purge deleted the lock directory"
    assert (lock / "pid").read_text(encoding="ascii").strip() == "4242", (
        "the relocation purge deleted the lock's pid file"
    )


@pytest.mark.skipif(ROBOCOPY is None, reason="robocopy not available")
def test_purge_does_not_overwrite_the_lock_with_a_stale_source_copy(tmp_path):
    """A previous local build can leave ``%SRCDIR%\\.build-windows.lock``
    behind; /E without an exclusion copies it over the LIVE lock and its pid
    (probe A variant 2 measured pid 9999 in the destination)."""
    line = _wrapper_robocopy_line(_read(WINBAT))
    flags = line.split()[3:]

    src = tmp_path / "src"
    dst = tmp_path / "dst" / "build"
    src.mkdir(parents=True)
    dst.mkdir(parents=True)
    (src / "installer.nsi").write_text("x", encoding="ascii")
    stale = src / LOCK_NAME
    stale.mkdir()
    (stale / "pid").write_text("9999\r\n", encoding="ascii")
    live = dst / LOCK_NAME
    live.mkdir()
    (live / "pid").write_text("4242\r\n", encoding="ascii")

    subprocess.run([ROBOCOPY, str(src), str(dst), *flags],
                   capture_output=True, text=True, encoding="utf-8",
                   errors="replace", timeout=180)
    assert (live / "pid").read_text(encoding="ascii").strip() == "4242", (
        "a stale lock in the source tree replaced the live lock's pid"
    )


@pytest.mark.skipif(ROBOCOPY is None, reason="robocopy not available")
def test_purge_still_removes_a_genuinely_stale_file(tmp_path):
    """Control: the exclusion must not disable /PURGE for ordinary files."""
    line = _wrapper_robocopy_line(_read(WINBAT))
    flags = line.split()[3:]

    src = tmp_path / "src"
    dst = tmp_path / "dst" / "build"
    src.mkdir(parents=True)
    dst.mkdir(parents=True)
    (src / "keep.txt").write_text("keep", encoding="ascii")
    (dst / "deleted-upstream.txt").write_text("gone", encoding="ascii")
    (dst / "staging").mkdir()
    (dst / "staging" / "output.bin").write_text("artifact", encoding="ascii")

    subprocess.run([ROBOCOPY, str(src), str(dst), *flags],
                   capture_output=True, text=True, encoding="utf-8",
                   errors="replace", timeout=180)
    assert (dst / "keep.txt").exists()
    assert not (dst / "deleted-upstream.txt").exists(), "/PURGE stopped working"
    assert (dst / "staging" / "output.bin").exists(), (
        "the build output exclusions must stay in place"
    )
