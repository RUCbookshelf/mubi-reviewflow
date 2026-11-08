"""R8 FL8 nails - build/packaging hygiene (MM9-07/09/11/13, N-01, GLM3-01, F1-3..F1-12).

Every fix in this file is pinned by a nail that is RED at the a080fde baseline
(each test docstring says which pre-fix behaviour it pins). Structural nails
parse the packaging scripts; behaviour nails run the REAL scripts (or the REAL
subroutines extracted out of them) in throwaway trees:

* build_linux.sh runs end to end under stubbed python3/curl with a real
  custom_frontend copy, so the validator wiring (MM9-07), the download mutex
  (F1-3), the extract-and-run environment (F1-4/F1-6) and the portable
  SHA256SUMS.txt (F1-5) are exercised by execution, not by grep;
* build_windows.bat subroutines (lock, certutil digest check, verified fetch)
  are extracted verbatim into a CRLF harness and executed through cmd.exe;
* the F1-9 version guard is extracted from build_macos.sh between its
  F1-9 markers and executed in bash;
* GLM3-01 imports build/windows_start.py and lets probe() hit a real
  subprocess.TimeoutExpired.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
BASH = shutil.which("bash")
CMD = shutil.which("cmd.exe") if sys.platform == "win32" else None
GIT = shutil.which("git")
NODE = (
    os.environ.get("RF_NODE")
    or shutil.which("node")
    or r"C:\Users\16073\AppData\Local\hermes\node\node.exe"
)

LINUX = REPO / "build" / "build_linux.sh"
MACOS = REPO / "build" / "build_macos.sh"
WINBAT = REPO / "build" / "build_windows.bat"

FOUR_VALIDATORS = (
    "validate_import_review.py",
    "validate_screening_rounds.py",
    "validate_import_review_static.cjs",
    "validate_team_prisma_static.js",
)

# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _p(path: Path) -> str:
    return path.resolve().as_posix()


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _bat_section(text: str, start_label: str, end_label: str) -> str:
    """Slice a build_windows.bat region between two top-level :labels (labels
    must be matched at line start - `goto :label` mentions come first)."""
    start = re.search(rf"(?m)^{re.escape(start_label)}\s*$", text)
    end = re.search(rf"(?m)^{re.escape(end_label)}\s*$", text[start.end():])
    assert start and end, f"labels {start_label}..{end_label} not found"
    return text[start.start():start.end() + end.start()]


PYTHON_STUB_BODY = """#!/bin/sh
# python3 stand-in: logs every invocation to $RF_PY_LOG so the nails can prove
# which validators/gates the REAL build script invoked, then serves the venv
# and PyInstaller stages the appimagetool stage needs.
printf '%s\\n' "$*" >> "$RF_PY_LOG"
if [ "$1" = "-m" ] && [ "$2" = "venv" ]; then
  mkdir -p "$3/bin"
  cp "$RF_PYTHON_STUB" "$3/bin/python"
  exit 0
fi
if [ "$1" = "-m" ] && [ "$2" = "PyInstaller" ]; then
  dist=""
  prev=""
  for arg in "$@"; do
    if [ "$prev" = "--distpath" ]; then dist="$arg"; fi
    prev="$arg"
  done
  mkdir -p "$dist/ReviewFlow"
  printf 'stub-binary' > "$dist/ReviewFlow/ReviewFlow"
  exit 0
fi
exit 0
"""

CURL_STUB = """#!/bin/sh
url=""
out=""
prev=""
for arg in "$@"; do
  case "$arg" in
    https://*) url="$arg" ;;
  esac
  if [ "$prev" = "-o" ]; then out="$arg"; fi
  prev="$arg"
done
printf '%s\\n' "$url" >> "$RF_CURL_LOG"
cat "$RF_FAKE_PAYLOAD" > "$out"
"""

FAKE_APPIMAGETOOL = """#!/bin/sh
# records the environment the build hands to appimagetool (F1-4/F1-6), then
# produces the expected artifact so the later stages can run.
printf 'AER=%s ARCH=%s\\n' "${APPIMAGE_EXTRACT_AND_RUN:-}" "${ARCH:-}" > "$RF_TOOL_LOG"
out="$2"
mkdir -p "$(dirname "$out")"
printf 'fake-appimage' > "$out"
"""

PINNED_SHA_RE = re.compile(r'APPIMAGETOOL_SHA256="([0-9a-f]{64})"')


def _stub(base: Path, rel: str, body: str) -> Path:
    target = base / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8", newline="\n")
    target.chmod(0o755)
    return target


def _fake_tree(tmp_path: Path, *, serve_stub_digest: bool = False) -> tuple[Path, Path, Path]:
    """Fake checkout for running build_linux.sh end to end (stubbed python/curl,
    REAL custom_frontend + REAL node validators, REAL appimagetool stub).
    With serve_stub_digest=True the script's pinned digest is rewritten to the
    stub payload's real digest so the F1-1 gate passes (r7 pattern)."""
    tree = tmp_path / "tree"
    (tree / "build").mkdir(parents=True)
    script = _read(LINUX)
    payload = tmp_path / "fake-appimagetool"
    payload.write_text(FAKE_APPIMAGETOOL, encoding="utf-8", newline="\n")
    payload.chmod(0o755)
    if serve_stub_digest:
        assert PINNED_SHA_RE.search(script), "pinned digest constant missing from script"
        digest = hashlib.sha256(payload.read_bytes()).hexdigest()
        script = PINNED_SHA_RE.sub(f'APPIMAGETOOL_SHA256="{digest}"', script)
    (tree / "build" / "build_linux.sh").write_text(script, encoding="utf-8", newline="\n")
    (tree / "build" / "requirements-app.txt").write_text("fastapi>=0.110\n", encoding="utf-8")
    (tree / "build" / "launcher.py").write_text("print('launcher')\n", encoding="utf-8")
    (tree / "build" / "icon_256.png").write_bytes(b"\x89PNG stub icon\n")
    (tree / "LICENSE").write_text("license\n", encoding="utf-8")
    (tree / "THIRD_PARTY_NOTICES.md").write_text("notices\n", encoding="utf-8")
    # 跳过未跟踪的 Mimosa 钩子状态目录（非产品文件；其深层长路径会让 copytree 失败）
    shutil.copytree(REPO / "custom_frontend", tree / "custom_frontend",
                    ignore=shutil.ignore_patterns(".mimosa"))
    for validator in ("script_manifest.cjs", "validate_import_review_static.cjs",
                      "validate_team_prisma_static.js", "verify_release_payload.py"):
        shutil.copy2(REPO / "build" / validator, tree / "build" / validator)

    stubs = tmp_path / "pathstubs"
    python_stub = tmp_path / "python-stub.sh"
    python_stub.write_text(PYTHON_STUB_BODY, encoding="utf-8", newline="\n")
    python_stub.chmod(0o755)
    _stub(stubs, "python3", PYTHON_STUB_BODY)
    _stub(stubs, "curl", CURL_STUB)
    return tree, stubs, python_stub


def _build_env(tree: Path, stubs: Path, tmp_path: Path, python_stub: Path) -> dict:
    env = dict(os.environ)
    env["PATH"] = str(stubs) + os.pathsep + env["PATH"]
    if NODE and Path(NODE).exists():
        env["PATH"] = str(Path(NODE).parent) + os.pathsep + env["PATH"]
    env["RF_PYTHON_STUB"] = _p(python_stub)
    env["RF_FAKE_PAYLOAD"] = _p(tmp_path / "fake-appimagetool")
    env["RF_CURL_LOG"] = _p(tmp_path / "curl.log")
    env["RF_TOOL_LOG"] = _p(tmp_path / "tool.log")
    env["RF_PY_LOG"] = _p(tmp_path / "py.log")
    return env


def _run_build(tree: Path, stubs: Path, tmp_path: Path, python_stub: Path,
               *, env_extra: dict | None = None) -> tuple[subprocess.CompletedProcess, Path, Path, Path]:
    env = _build_env(tree, stubs, tmp_path, python_stub)
    if env_extra:
        env.update(env_extra)
    proc = subprocess.run(
        [BASH, str(tree / "build" / "build_linux.sh")],
        cwd=tree, env=env, capture_output=True, text=True, timeout=240,
    )
    return proc, tmp_path / "curl.log", tmp_path / "tool.log", tmp_path / "py.log"




def _extract_version_helpers() -> str:
    text = _read(MACOS)
    begin = text.index("# F1-9:begin version helpers")
    end = text.index("# F1-9:end version helpers")
    return text[begin:end].replace("\r\n", "\n")


def _bat_tail_subroutines() -> str:
    """The :err/:acquire_lock/:release_lock/:fetch_verified/:sha256_matches
    subroutines, verbatim, out of the real build_windows.bat."""
    text = _read(WINBAT)
    return text[text.index("\n:err\n"):]


# --------------------------------------------------------------------------- #
# MM9-07 - local packaging runs the CI gates (validators + payload compare)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("script", [LINUX, MACOS])
def test_mm9_07_sh_scripts_wire_all_four_validators_and_payload(script):
    """RED at baseline: neither sh packaging script invoked any validator or
    verify_release_payload (mm9_verify_g1 §4.2 grep exit 1)."""
    text = _read(script)
    for validator in FOUR_VALIDATORS:
        assert validator in text, f"{script.name} must run the {validator} gate"
    assert "verify_release_payload.py" in text, (
        f"{script.name} must verify the finished payload against the sources"
    )


def test_mm9_07_windows_bat_runs_payload_gate():
    """RED at baseline: build_windows.bat ran the four validators but never
    verify_release_payload.py (git grep: release.yml only)."""
    text = _read(WINBAT)
    for validator in FOUR_VALIDATORS:
        assert validator in text
    assert re.search(r'python\.exe"\s+"%SRCDIR%\\verify_release_payload\.py"\s+windows', text), (
        "build_windows.bat must run verify_release_payload.py windows on the staging tree"
    )


@pytest.mark.skipif(BASH is None, reason="bash not found")
@pytest.mark.skipif(not (NODE and Path(NODE).exists()), reason="node runtime not found")
def test_mm9_07_linux_build_actually_runs_validators_and_payload(tmp_path):
    """Behaviour: the real build_linux.sh (stubbed downloads) must invoke the
    two Python validators, the two node validators and the payload verifier.
    RED at baseline - RF_PY_LOG stays empty of all five invocations and the
    node validator success JSON never reaches stdout."""
    tree, stubs, python_stub = _fake_tree(tmp_path, serve_stub_digest=True)
    (tmp_path / "py.log").write_text("", encoding="utf-8", newline="\n")
    proc, curl_log, tool_log, py_log = _run_build(tree, stubs, tmp_path, python_stub)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    invoked = py_log.read_text(encoding="utf-8")
    assert "validate_import_review.py" in invoked
    assert "validate_screening_rounds.py" in invoked
    assert "verify_release_payload.py" in invoked
    # the node gates are real here: their success output flows through stdout
    assert '"status": "passed"' in proc.stdout, "node validators did not run green"


# --------------------------------------------------------------------------- #
# F1-3 - concurrent builds are mutually exclusive
# --------------------------------------------------------------------------- #


def test_f1_3_sh_scripts_lock_the_shared_work_and_dist_dirs():
    """RED at baseline: no lock/mutex existed in either sh script.

    Design note (round 2): the fix deliberately does NOT auto-reclaim an
    abandoned lock. A pid-based reclaim has to rd+mkdir the directory, and a
    contender that sampled the stale pid then takes over the freshly created
    LIVE lock - measured 4/4 concurrent builds inside the section. mkdir
    success is the only race-free acquisition available on both platforms, so
    the contract is: mkdir wins, the owner's trap releases, and a dead lock is
    cleared only through the explicit REVIEWFLOW_BUILD_LOCK_FORCE=1 override."""
    for script in (LINUX, MACOS):
        text = _read(script)
        assert '.build-dist.lock' in text, f"{script.name} must lock $BUILD/.build-dist.lock"
        assert 'if ! mkdir "$LOCK" 2>/dev/null; then' in text
        assert "trap 'rm -rf \"$LOCK\"' EXIT" in text
        assert 'REVIEWFLOW_BUILD_LOCK_FORCE' in text, (
            "a dead lock needs an explicit, documented override"
        )
        assert 'kill -0' not in text, (
            "no automatic pid-based reclaim: it cannot be made race-free with mkdir"
        )


def test_f1_3_windows_bat_locks_and_releases():
    """RED at baseline: no lock existed in build_windows.bat."""
    text = _read(WINBAT)
    assert ":acquire_lock" in text and ":release_lock" in text
    assert '.build-windows.lock' in text
    # the :build stage locks unless the outer UNC wrapper already holds it
    build_section = _bat_section(text, ":build", ":done")
    assert "RF_BUILD_LOCK_HELD" in build_section and "acquire_lock" in build_section
    # every exit path releases: the success block and the shared failure block
    assert "call :release_lock" in _bat_section(text, ":done", ":fail_nolocal")
    assert "call :release_lock" in _bat_section(text, ":end_fail", ":acquire_lock")


@pytest.mark.skipif(BASH is None, reason="bash not found")
@pytest.mark.skipif(not (NODE and Path(NODE).exists()), reason="node runtime not found")
def test_f1_3_linux_held_lock_blocks_the_second_build(tmp_path):
    """Behaviour: a lock whose owner is alive must abort the build before any
    download. RED at baseline - the baseline script simply deleted nothing and
    built anyway (exit 0, curl invoked)."""
    tree, stubs, python_stub = _fake_tree(tmp_path, serve_stub_digest=True)
    lock = tree / "build" / ".build-dist.lock"
    lock.mkdir(parents=True)
    holder = subprocess.Popen(
        [BASH, "-c",
         f'echo $$ > {_p(lock)}/pid; sleep 60'],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        proc, curl_log, tool_log, _ = _run_build(tree, stubs, tmp_path, python_stub)
        assert proc.returncode != 0, "a held lock must abort the second build"
        assert ".build-dist.lock" in proc.stdout + proc.stderr
        assert not curl_log.exists(), "a blocked build must not download anything"
        assert not (tree / "build" / "dist").exists(), "a blocked build must not touch $DIST"
    finally:
        holder.terminate()
        holder.wait(timeout=30)


@pytest.mark.skipif(BASH is None, reason="bash not found")
@pytest.mark.skipif(not (NODE and Path(NODE).exists()), reason="node runtime not found")
def test_f1_3_linux_dead_lock_stops_with_actionable_message(tmp_path):
    """Round-2 design: a leftover lock is NOT silently reclaimed - that reclaim
    is exactly the race. It must stop the build with a message naming the lock
    and telling the operator how to clear it."""
    tree, stubs, python_stub = _fake_tree(tmp_path, serve_stub_digest=True)
    lock = tree / "build" / ".build-dist.lock"
    lock.mkdir(parents=True)
    (lock / "pid").write_text("3999999\n", encoding="utf-8")
    proc, curl_log, tool_log, _ = _run_build(tree, stubs, tmp_path, python_stub)
    output = proc.stdout + proc.stderr
    assert proc.returncode != 0, "a leftover lock must stop the build"
    assert ".build-dist.lock" in output and "REVIEWFLOW_BUILD_LOCK_FORCE=1" in output, output
    assert not curl_log.exists(), "a blocked build must not download anything"
    assert lock.exists(), "the blocked build must not delete somebody else's lock"


@pytest.mark.skipif(BASH is None, reason="bash not found")
@pytest.mark.skipif(not (NODE and Path(NODE).exists()), reason="node runtime not found")
def test_f1_3_linux_force_override_clears_a_dead_lock(tmp_path):
    """The documented escape hatch: with REVIEWFLOW_BUILD_LOCK_FORCE=1 the
    operator's explicit decision is honoured and the build proceeds."""
    tree, stubs, python_stub = _fake_tree(tmp_path, serve_stub_digest=True)
    lock = tree / "build" / ".build-dist.lock"
    lock.mkdir(parents=True)
    (lock / "pid").write_text("3999999\n", encoding="utf-8")
    proc, _curl_log, tool_log, _ = _run_build(
        tree, stubs, tmp_path, python_stub,
        env_extra={"REVIEWFLOW_BUILD_LOCK_FORCE": "1"})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert tool_log.exists()
    assert not lock.exists(), "the trap must remove the lock the build itself created"


@pytest.mark.skipif(BASH is None, reason="bash not found")
def test_f1_3_lock_acquisition_is_race_free_under_contention(tmp_path):
    """Review finding (round 2): the ordering nails only proved the happy
    paths. Racing the REAL lock block from build_linux.sh with N
    barrier-synchronised contenders measured two separate defects in the
    earlier pid-based reclaim - `rm -rf`+`mkdir` let every contender delete and
    recreate (peak 4/4 inside), and even the rename variant stole a freshly
    created live lock when the pid was sampled before it was written.

    With mkdir-success as the only acquisition, at most one contender may ever
    be inside the critical section, with no stale lock and with one."""
    text = _read(LINUX)
    start = text.index("# F1-3:")
    end = text.index("trap ", start)
    end = text.index("\n", end) + 1
    block = text[start:end]
    assert "mkdir" in block and "kill -0" not in block

    for seeded in (False, True):
        contenders, rounds = 6, 4
        peaks = []
        for round_index in range(rounds):
            base = tmp_path / f"{'stale' if seeded else 'clean'}{round_index}"
            (base / "build").mkdir(parents=True)
            lock = base / "build" / ".build-dist.lock"
            if seeded:
                lock.mkdir()
                (lock / "pid").write_text("3999999\n", encoding="utf-8")
            script = base / "crit.sh"
            started, entered = base / "start", base / "in.log"
            script.write_text(
                "set -uo pipefail\n"
                f'BUILD="{_p(base / "build")}"\n'
                f'while [ ! -f "{_p(started)}" ]; do sleep 0.005; done\n'
                "{\n" + block + "\n} || exit 0\n"
                f'echo IN >> "{_p(entered)}"\n'
                "sleep 0.40\n"
                'rm -rf "$LOCK"\n',
                encoding="utf-8", newline="\n")
            procs = [subprocess.Popen([BASH, str(script)], stdout=subprocess.DEVNULL,
                                      stderr=subprocess.DEVNULL) for _ in range(contenders)]
            started.write_text("")
            for proc in procs:
                proc.communicate(timeout=120)
            peaks.append(len(entered.read_text().splitlines()) if entered.exists() else 0)
        # Without a lock exactly one contender gets in; with a leftover lock
        # NOBODY does (nobody is allowed to take over a lock it did not create).
        expected = 0 if seeded else 1
        label = "with a leftover lock" if seeded else "with no lock"
        assert peaks == [expected] * rounds, f"{label}: peaks={peaks}"


@pytest.mark.skipif(CMD is None, reason="cmd.exe not available (Windows-only nail)")
def test_f1_3_bat_lock_reclaim_is_exclusive_under_contention(tmp_path):
    """Same race for the REAL bat subroutines (cmd's rd+mkdir window is wider
    than sh's, so this was the worse of the two)."""
    subs = _bat_tail_subroutines().replace("\n", "\r\n")
    contenders, rounds = 4, 3
    for leftover in (False, True):
        peaks = []
        for round_index in range(rounds):
            base = tmp_path / f"bat{'leftover' if leftover else 'clean'}{round_index}"
            base.mkdir(parents=True)
            lock = base / ".build-windows.lock"
            if leftover:
                lock.mkdir()
                (lock / "pid").write_text("3999999\r\n")
            gate, entered = base / "go.flag", base / "in.log"
            harness = base / "crit.bat"
            harness.write_bytes("\r\n".join([
                "@echo off", "setlocal EnableExtensions",
                f'set "LOG={base.as_posix()}/nail.log"',
                f'set "LOCKDIR={lock.as_posix()}"',
                ":wait_gate",
                f'if not exist "{gate.as_posix()}" goto :wait_gate',
                "call :acquire_lock",
                "if errorlevel 1 exit /b 1",
                f'>> "{entered.as_posix()}" echo IN',
                "ping -n 2 127.0.0.1 >nul",
                "call :release_lock",
                "exit /b 0",
                "",
                subs.rstrip("\r\n"),
                "",
            ]).encode("utf-8"))
            procs = [subprocess.Popen([CMD, "/c", str(harness)], stdout=subprocess.DEVNULL,
                                      stderr=subprocess.DEVNULL, cwd=str(base))
                     for _ in range(contenders)]
            gate.write_text("go")
            for proc in procs:
                try:
                    proc.wait(timeout=180)
                except subprocess.TimeoutExpired:
                    proc.kill()
            peaks.append(len(entered.read_text().splitlines()) if entered.exists() else 0)
        expected = 0 if leftover else 1
        label = "with a leftover lock" if leftover else "with no lock"
        assert peaks == [expected] * rounds, f"bat {label}: peaks={peaks}"


@pytest.mark.skipif(CMD is None, reason="cmd.exe not available (Windows-only nail)")
def test_f1_3_unc_inner_build_does_not_release_the_wrapper_lock(tmp_path):
    """Review finding (round 2): the inner build released the outer wrapper's
    lock at :done, so the installer copy-back ran unprotected. With
    RF_LOCK_HANDED_DOWN set, :release_lock must be a no-op."""
    lockdir = tmp_path / "handed"
    lockdir.mkdir()
    lock = lockdir / ".build-windows.lock"
    lock.mkdir()
    (lock / "pid").write_text("424242\r\n")
    harness = lockdir / "handed_nail.bat"
    harness.write_bytes("\r\n".join([
        "@echo off", "setlocal EnableExtensions",
        f'set "LOG={lockdir.resolve()}/nail.log"',
        f'set "LOCKDIR={lock.resolve()}"',
        'set "RF_LOCK_OWNED=1"',
        'set "RF_LOCK_HANDED_DOWN=1"',
        "call :release_lock",
        'if exist "%LOCKDIR%" (echo KEPT=OK) else (echo KEPT=LOST)',
        "exit /b 0",
        "",
        _bat_tail_subroutines().replace("\n", "\r\n").rstrip("\r\n"),
        "",
    ]).encode("utf-8"))
    proc = subprocess.run([CMD, "/c", str(harness)], cwd=lockdir,
                          capture_output=True, text=True, timeout=120)
    assert "KEPT=OK" in (proc.stdout + proc.stderr), (
        "the inner build deleted the wrapper's lock"
    )



    """Behaviour: the REAL lock subroutines extracted from build_windows.bat -
    second acquire goes busy, release allows re-acquire, dead-PID lock is
    reclaimed, and a failed acquire never deletes the owner's lock.
    RED at baseline: the subroutines do not exist, so the harness cannot even
    run (cmd reports the missing label and every check fails)."""
    harness = tmp_path / "lock_nail.bat"
    body = "\r\n".join([
        "@echo off",
        "setlocal EnableExtensions",
        f'set "SRCDIR={tmp_path.resolve()}"',
        f'set "LOG={tmp_path.resolve()}/nail.log"',
        'set "LOCKDIR=%SRCDIR%\\.build-windows.lock"',
        'if exist "%LOCKDIR%" rd /s /q "%LOCKDIR%"',
        'call :acquire_lock',
        'if errorlevel 1 (echo FIRST=FAIL) else echo FIRST=OK',
        'call :acquire_lock',
        'if errorlevel 1 (echo SECOND=BUSY) else echo SECOND=WRONG-OK',
        'call :release_lock',
        'call :acquire_lock',
        'if errorlevel 1 (echo REACQUIRE=FAIL) else echo REACQUIRE=OK',
        '> "%LOCKDIR%\\pid" echo 3999999',
        'call :release_lock',
        'call :acquire_lock',
        'if errorlevel 1 (echo STALE=FAIL) else echo STALE=OK',
        'call :release_lock',
        "exit /b 0",
        "",
        _bat_tail_subroutines().replace("\n", "\r\n").rstrip("\r\n"),
        "",
    ])
    harness.write_bytes(body.encode("utf-8"))
    proc = subprocess.run([CMD, "/c", str(harness)], cwd=tmp_path,
                          capture_output=True, text=True, timeout=120)
    out = proc.stdout + proc.stderr
    for marker in ("FIRST=OK", "SECOND=BUSY", "REACQUIRE=OK", "STALE=OK"):
        assert marker in out, f"expected {marker} in: {out}"


@pytest.mark.skipif(CMD is None, reason="cmd.exe not available (Windows-only nail)")
def test_f1_3_bat_leftover_lock_is_reported_not_taken_over(tmp_path):
    """Round-2 design: a leftover lock (including one whose pid file says 0 or
    4 - the Windows idle/system pseudo-processes, always present in tasklist)
    is NEVER taken over automatically. The earlier pid-based reclaim both
    wedged the checkout on those ids and raced with live builds, so the
    contract is now: report the holder and stop, and let the operator delete
    the folder once they know no build is running."""
    for pid in ("0", "4", "3999999"):
        lockdir = tmp_path / f"case{pid}"
        lockdir.mkdir(parents=True)
        lock = lockdir / ".build-windows.lock"
        lock.mkdir()
        (lock / "pid").write_text(pid + "\r\n")
        harness = lockdir / "pid_nail.bat"
        harness.write_bytes("\r\n".join([
            "@echo off",
            "setlocal EnableExtensions",
            f'set "LOG={lockdir.resolve()}/nail.log"',
            f'set "LOCKDIR={lock.resolve()}"',
            "call :acquire_lock",
            "if errorlevel 1 (echo REFUSED=OK) else echo REFUSED=WRONG-TOOK-OVER",
            "exit /b 0",
            "",
            _bat_tail_subroutines().replace("\n", "\r\n").rstrip("\r\n"),
            "",
        ]).encode("utf-8"))
        proc = subprocess.run([CMD, "/c", str(harness)], cwd=lockdir,
                              capture_output=True, text=True, timeout=120)
        out = proc.stdout + proc.stderr
        assert "REFUSED=OK" in out, f"pid {pid}: lock was taken over: {out}"
        assert lock.exists(), f"pid {pid}: the refused build deleted the lock"


# --------------------------------------------------------------------------- #
# F1-4 / F1-6 - appimagetool runs extracted (no FUSE2 dependency), arch pinned per digest
# --------------------------------------------------------------------------- #


def test_f1_4_6_linux_uses_extract_and_run_and_pinned_arch_table():
    """RED at baseline: the invocation was `ARCH=x86_64 "$APPIMAGETOOL"`
    with no APPIMAGE_EXTRACT_AND_RUN and no per-arch digest table."""
    text = _read(LINUX)
    assert 'APPIMAGE_EXTRACT_AND_RUN=1 "$APPIMAGETOOL"' in text
    assert 'APPIMAGE_ARCH="${APPIMAGE_ARCH:-x86_64}"' in text
    assert re.search(r'aarch64\)\s*\n\s*APPIMAGETOOL_URL="[^"]+appimagetool-aarch64\.AppImage"', text)
    assert re.search(r'appimagetool-aarch64\.AppImage"\s*\n\s*APPIMAGETOOL_SHA256="[0-9a-f]{64}"', text)
    assert 'unsupported APPIMAGE_ARCH' in text


@pytest.mark.skipif(BASH is None, reason="bash not found")
@pytest.mark.skipif(not (NODE and Path(NODE).exists()), reason="node runtime not found")
def test_f1_4_6_appimagetool_receives_extract_and_run_env(tmp_path):
    """Behaviour: the executed tool sees APPIMAGE_EXTRACT_AND_RUN=1 and the
    resolved ARCH. RED at baseline: the stub records AER= (empty) ARCH=x86_64
    hardcoded prefix is indistinguishable - the env var is absent."""
    tree, stubs, python_stub = _fake_tree(tmp_path, serve_stub_digest=True)
    proc, curl_log, tool_log, _ = _run_build(tree, stubs, tmp_path, python_stub)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    recorded = tool_log.read_text(encoding="utf-8").strip()
    assert recorded == "AER=1 ARCH=x86_64", f"appimagetool env wrong: {recorded!r}"


# --------------------------------------------------------------------------- #
# F1-5 / F1-12 - SHA256SUMS.txt records the bare artifact name
# --------------------------------------------------------------------------- #


def test_f1_5_f1_12_sumfiles_are_written_from_inside_dist():
    """RED at baseline: both scripts wrote `sha256sum "$DIST/<abs-path artifact>"
    > "$DIST/SHA256SUMS.txt"`, embedding the build machine's path."""
    linux = _read(LINUX)
    macos = _read(MACOS)
    assert '( cd "$DIST" && sha256sum "ReviewFlow-${APPIMAGE_ARCH}.AppImage" > SHA256SUMS.txt )' in linux
    assert 'sha256sum "$DIST/ReviewFlow-x86_64.AppImage" > "$DIST/SHA256SUMS.txt"' not in linux
    assert '( cd "$DIST" && shasum -a 256 "$APP_NAME.dmg" > SHA256SUMS.txt )' in macos
    assert 'shasum -a 256 "$DIST/$APP_NAME.dmg" > "$DIST/SHA256SUMS.txt"' not in macos


@pytest.mark.skipif(BASH is None, reason="bash not found")
@pytest.mark.skipif(not (NODE and Path(NODE).exists()), reason="node runtime not found")
def test_f1_5_linux_sumfile_is_portable(tmp_path):
    """Behaviour: the emitted dist/SHA256SUMS.txt verifies from any directory -
    basename only, no build-machine path. RED at baseline: the file contained
    the absolute $DIST path (wave_01 F1-5 probe: renamed dir -> FAILED open)."""
    tree, stubs, python_stub = _fake_tree(tmp_path, serve_stub_digest=True)
    proc, _curl_log, tool_log, _ = _run_build(tree, stubs, tmp_path, python_stub)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    sums = (tree / "build" / "dist" / "SHA256SUMS.txt").read_text(encoding="utf-8")
    assert re.fullmatch(
        r"[0-9a-f]{64}[ \t]+\*?ReviewFlow-x86_64\.AppImage\r?\n", sums
    ), f"SHA256SUMS.txt is not a portable basename entry: {sums!r}"
    check = subprocess.run(
        [BASH, "-c", f'cd "{_p(tree / "build" / "dist")}" && sha256sum -c SHA256SUMS.txt'],
        cwd=tmp_path, capture_output=True, text=True, timeout=60,
    )
    assert check.returncode == 0, check.stdout + check.stderr


# --------------------------------------------------------------------------- #
# F1-8 / F1-11 - iconset slots carry their true pixel size
# --------------------------------------------------------------------------- #


def test_f1_8_f1_11_iconset_slots_are_correct():
    """RED at baseline: icon_512x512.png and icon_256x256@2x.png were both
    overwritten with the 256px source, icon_64x64.png stayed in the iconset and
    icon_512x512@2x.png never existed (wave_01 F1-8/F1-11)."""
    text = _read(MACOS)
    assert 'cp "$ICONSET/icon_256x256.png" "$ICONSET/icon_512x512.png"' not in text
    assert 'cp "$ICONSET/icon_256x256.png" "$ICONSET/icon_256x256@2x.png"' not in text
    assert 'cp "$ICONSET/icon_512x512.png" "$ICONSET/icon_256x256@2x.png"' in text
    assert re.search(r'sips -z 1024 1024 .*--out "\$ICONSET/icon_512x512@2x\.png"', text)
    assert 'rm -f "$ICONSET/icon_64x64.png"' in text


# --------------------------------------------------------------------------- #
# F1-9 - VERSION format validation with a readable error
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(BASH is None, reason="bash not found")
def test_f1_9_version_guard_accepts_and_rejects():
    """Behaviour on the REAL guard extracted from build_macos.sh: the four
    historical formats parse to the same bundle values the old arithmetic
    produced, malformed values exit non-zero with a readable message.
    RED at baseline: the F1-9 marker block does not exist, so there is nothing
    to extract (and the baseline script died on '10#' arithmetic errors)."""
    helpers = _extract_version_helpers()
    assert "validate_app_version()" in helpers and "compute_bundle_versions()" in helpers
    probe = (
        f"set -uo pipefail\n{helpers}\n"
        'check() { out="$(compute_bundle_versions "$1" 2>&1)"; rc=$?; '
        'if [ $rc -ne 0 ]; then echo "REJ:$1:$out"; else '
        "short=\"${out%%$'\\n'*}\"; build=\"${out##*$'\\n'}\"; "
        'echo "OK:$1:$short:$build"; fi; }\n'
        'for v in 2.1.4 2.1.4.1 2.1 2.0.5.3 "" "2.1.4-rc1" 1.2.3.4.5 v2.1.4 2.x.4; do check "$v"; done\n'
    )
    proc = subprocess.run([BASH, "-c", probe], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    lines = proc.stdout.strip().splitlines()
    expect = {
        "2.1.4": ("2.1.4", "2.1.400"),
        "2.1.4.1": ("2.1.4", "2.1.401"),
        "2.1": ("2.1", "2.1.0"),
        "2.0.5.3": ("2.0.5", "2.0.503"),
    }
    for line in lines:
        if line.startswith("OK:"):
            _, value, short, build = line.split(":", 3)
            assert value in expect, f"unexpected accept: {line}"
            assert (short, build) == expect[value], line
        else:
            assert line.startswith("REJ:"), line
            assert "VERSION contains" in line, f"rejection must be readable: {line}"
    accepted = {line.split(":")[1] for line in lines if line.startswith("OK:")}
    rejected = {line.split(":")[1] for line in lines if line.startswith("REJ:")}
    assert accepted == set(expect)
    assert rejected == {"", "2.1.4-rc1", "1.2.3.4.5", "v2.1.4", "2.x.4"}


def test_f1_9_macos_script_parses_via_the_guard():
    """RED at baseline: the version arithmetic ran unguarded at the top of
    build_macos.sh."""
    text = _read(MACOS)
    assert 'BUNDLE="$(compute_bundle_versions "$APP_VERSION")" || exit 1' in text
    assert "BUNDLE_VERSION=\"${BUNDLE%%$'\\n'*}\"" in text
    assert "APP_VERSION=\"$(tr -d '\\r\\n' < \"$ROOT/VERSION\")\"" in text


# --------------------------------------------------------------------------- #
# F1-10 - scoped .gitattributes (local solution, no repo-wide renormalization)
# --------------------------------------------------------------------------- #


def test_f1_10_gitattributes_pins_sh_lf_without_repo_wide_rules():
    """RED at baseline: no .gitattributes existed. Also guards the R1 side of
    the fix: the file must not contain repo-wide `* text=auto`-style rules
    (those would renormalize every checkout)."""
    attr = REPO / ".gitattributes"
    assert attr.is_file(), "a scoped .gitattributes must exist"
    text = _read(attr)
    assert "*.sh text eol=lf" in text
    assert not re.search(r'^\*\s+text', text, re.MULTILINE), (
        "repo-wide text rules would renormalize the whole tree (R1)"
    )
    if GIT:
        proc = subprocess.run(
            [GIT, "-C", str(REPO), "check-attr", "eol", "--", "build/build_linux.sh"],
            capture_output=True, text=True, timeout=60,
        )
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.strip().endswith("lf"), proc.stdout


# --------------------------------------------------------------------------- #
# MM9-09 - dead mobile_workspace.* files are gone and cannot come back silently
# --------------------------------------------------------------------------- #


def test_mm9_09_dead_mobile_workspace_files_are_deleted():
    """RED at baseline: both files existed on disk (git ls-files) and were
    copied into every package by the whole-directory packaging copies."""
    assert not (REPO / "custom_frontend" / "mobile_workspace.js").exists()
    assert not (REPO / "custom_frontend" / "mobile_workspace.css").exists()


@pytest.mark.skipif(not (NODE and Path(NODE).exists()), reason="node runtime not found")
def test_mm9_09_reverse_manifest_rule_holds():
    """Behaviour: the reverse rule (a top-level frontend file index.html never
    loads fails the gate) is exported and green on the current tree.
    RED at baseline twice over: the helper did not exist, and the tree still
    contained the two dead files it would have flagged."""
    code = (
        "const m = require(" + json.dumps(str(REPO / "build" / "script_manifest.cjs")) + ");"
        "m.assertNoUnloadedFrontendFiles(" + json.dumps(str(REPO)) + ");"
        "console.log('reverse-rule-green');"
    )
    proc = subprocess.run([NODE, "-e", code], cwd=REPO, capture_output=True,
                          text=True, timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "reverse-rule-green" in proc.stdout


@pytest.mark.skipif(not (NODE and Path(NODE).exists()), reason="node runtime not found")
def test_mm9_09_reverse_rule_catches_substring_css_and_mjs(tmp_path):
    """Review finding (round 2): the first reverse rule MISSED three cases -
    a dead CSS whose name is a SUFFIX of a live one (bare `html.includes`),
    a dead top-level .mjs, and (separately) a missing required validator input.
    Each planted file must now be reported."""
    tree = tmp_path / "tree"
    # 跳过未跟踪的 Mimosa 钩子状态目录（非产品文件；其深层长路径会让 copytree 失败）
    shutil.copytree(REPO / "custom_frontend", tree / "custom_frontend",
                    ignore=shutil.ignore_patterns(".mimosa"))
    (tree / "custom_frontend" / "concept.css").write_text("/*dead*/\n", encoding="utf-8")
    (tree / "custom_frontend" / "extra.mjs").write_text("export const x=1;\n", encoding="utf-8")
    code = (
        "const m = require(" + json.dumps(str(REPO / "build" / "script_manifest.cjs")) + ");"
        "try { m.assertNoUnloadedFrontendFiles(" + json.dumps(str(tree)) + ");"
        "console.log('MISSED'); } catch (e) { console.log(e.message); }"
    )
    proc = subprocess.run([NODE, "-e", code], cwd=str(tree), capture_output=True,
                          text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    message = proc.stdout
    assert "MISSED" not in message, "the reverse rule accepted planted dead files"
    assert "concept.css" in message, f"dead CSS whose name is a suffix of a live one was not caught: {message}"
    assert "extra.mjs" in message, f"dead top-level .mjs was not caught: {message}"


def test_mm9_09_required_validator_inputs_cannot_be_silently_dropped():
    """Review finding (round 2): the existence filter also stripped the
    `required` list, so deleting team_roles.js left validate_team_prisma_static
    green. The `required` contract must now fail loudly."""
    text = _read(REPO / "build" / "validate_team_prisma_static.js")
    assert "missingRequired" in text, (
        "the validator must fail when a required input file is missing, "
        "not filter it out of the check list"
    )
    assert "changed.filter(existing)" in text and "extra.filter(existing)" in text, (
        "only the git-derived lists may be existence-filtered"
    )


def test_mm9_09_static_gate_wires_the_reverse_rule():
    """RED at baseline: validate_import_review_static.cjs had no reverse rule
    (mm9_verify_g2 §2.1 noted the missing direction)."""
    text = _read(REPO / "build" / "validate_import_review_static.cjs")
    assert "assertNoUnloadedFrontendFiles" in text


# --------------------------------------------------------------------------- #
# MM9-11 - the stale SHA256SUMS.json snapshot is renamed and annotated
# --------------------------------------------------------------------------- #


def test_mm9_11_stale_snapshot_renamed_and_documented():
    """RED at baseline: the repo root tracked SHA256SUMS.json - a 298-entry
    snapshot of the initial release with 68 blob-level mismatches, 2 ghost
    keys and zero consumers (mm9_verify_g2 §4)."""
    assert not (REPO / "SHA256SUMS.json").exists(), (
        "the stale live-named manifest must not exist at the old path"
    )
    snapshot = REPO / "SHA256SUMS.initial-release-snapshot.json"
    assert snapshot.is_file()
    data = json.loads(snapshot.read_text(encoding="utf-8"))
    assert len(data) >= 290, "the snapshot must stay the untouched initial-release ledger"
    readme = _read(REPO / "README.md")
    assert "SHA256SUMS.initial-release-snapshot.json" in readme, (
        "README must say the file is a frozen snapshot, not a live manifest"
    )


# --------------------------------------------------------------------------- #
# MM9-13 - Windows bootstrap downloads are certutil-verified (cache included)
# --------------------------------------------------------------------------- #


def test_mm9_13_windows_pins_and_verifies_both_bootstrap_downloads():
    """RED at baseline: grep -i "sha256|certutil|hash" build_windows.bat was
    empty (mm9_verify_g1 §7.2), and cached files were trusted as-is."""
    text = _read(WINBAT)
    assert re.search(r'set "PYEMBED_SHA256=[0-9a-f]{64}"', text)
    assert re.search(r'set "GETPIP_SHA256=[0-9a-f]{64}"', text)
    assert ":fetch_verified" in text and ":sha256_matches" in text
    assert "certutil -hashfile" in text
    # both downloads go through the verified fetcher; no raw download remains
    assert 'curl -L --fail --retry 2 -o "%DOWNLOADS%\\%PYEMBED_ZIP%"' not in text
    assert 'curl -L --fail --retry 2 -o "%DOWNLOADS%\\get-pip.py"' not in text
    assert 'call :fetch_verified "%PYEMBED_URL%"' in text
    assert 'call :fetch_verified "%GETPIP_URL%"' in text


@pytest.mark.skipif(CMD is None, reason="cmd.exe not available (Windows-only nail)")
def test_mm9_13_digest_subroutine_behaviour(tmp_path):
    """Behaviour on the REAL subroutines extracted from build_windows.bat:
    correct pin passes, wrong pin fails, missing file fails.
    RED at baseline: :sha256_matches does not exist, so every call falls
    through to exit 1 / the wrong branch."""
    sample = tmp_path / "sample.bin"
    sample.write_bytes(b"hello fl8")
    digest = hashlib.sha256(b"hello fl8").hexdigest()
    harness = tmp_path / "hash_nail.bat"
    body = "\r\n".join([
        "@echo off",
        "setlocal EnableExtensions",
        f'set "SRCDIR={tmp_path.resolve()}"',
        f'set "LOG={tmp_path.resolve()}\\nail.log"',
        f'call :sha256_matches "%SRCDIR%\\sample.bin" "{digest}"',
        'if errorlevel 1 (echo MATCH=FAIL) else echo MATCH=OK',
        f'call :sha256_matches "%SRCDIR%\\sample.bin" "{"0" * 64}"',
        'if errorlevel 1 (echo MISMATCH=OK) else echo MISMATCH=WRONG-PASSED',
        'call :sha256_matches "%SRCDIR%\\missing.bin" "x"',
        'if errorlevel 1 (echo MISSING=OK) else echo MISSING=WRONG-PASSED',
        "exit /b 0",
        "",
        _bat_tail_subroutines().replace("\n", "\r\n").rstrip("\r\n"),
        "",
    ])
    harness.write_bytes(body.encode("utf-8"))
    proc = subprocess.run([CMD, "/c", str(harness)], cwd=tmp_path,
                          capture_output=True, text=True, timeout=120)
    out = proc.stdout + proc.stderr
    assert "MATCH=OK" in out, out
    assert "MISMATCH=OK" in out, out
    assert "MISSING=OK" in out, out


@pytest.mark.skipif(CMD is None, reason="cmd.exe not available (Windows-only nail)")
def test_mm9_13_fetch_verified_aborts_on_unmatched_pin(tmp_path):
    """Behaviour: a cached-then-redownloaded file whose digest still does not
    match the pin must abort (exit 1). RED at baseline: :fetch_verified does
    not exist."""
    sample = tmp_path / "remote.bin"
    sample.write_bytes(b"remote payload")
    cache = tmp_path / "cache" / "remote.bin"
    cache.parent.mkdir()
    cache.write_bytes(b"tampered cache content")
    harness = tmp_path / "fetch_nail.bat"
    body = "\r\n".join([
        "@echo off",
        "setlocal EnableExtensions",
        f'set "SRCDIR={tmp_path.resolve()}"',
        f'set "LOG={tmp_path.resolve()}\\nail.log"',
        f'call :fetch_verified "{cache.resolve().as_uri()}" "{cache.resolve()}" "{"0" * 64}"',
        'if errorlevel 1 (echo BADPIN=ABORT-OK) else echo BADPIN=WRONG-PASSED',
        "exit /b 0",
        "",
        _bat_tail_subroutines().replace("\n", "\r\n").rstrip("\r\n"),
        "",
    ])
    harness.write_bytes(body.encode("utf-8"))
    proc = subprocess.run([CMD, "/c", str(harness)], cwd=tmp_path,
                          capture_output=True, text=True, timeout=120)
    out = proc.stdout + proc.stderr
    assert "BADPIN=ABORT-OK" in out, out


# --------------------------------------------------------------------------- #
# N-01 - the UNC relocation copies the root VERSION file
# --------------------------------------------------------------------------- #


def test_n01_unc_migration_copies_version():
    """RED at baseline: the UNC branch copied build/, the three source dirs and
    THIRD_PARTY_NOTICES.md but never VERSION (glm_verify_v1 §6.2)."""
    text = _read(WINBAT)
    unc = _bat_section(text, ":detect_unc", ":build")
    assert 'copy /y "%SRCPARENT%\\VERSION" "%BUILDROOT%\\VERSION"' in unc, (
        "the relocated tree must receive the root VERSION file"
    )
    assert 'if errorlevel 1 goto :fail_copy' in unc.split('copy /y "%SRCPARENT%\\VERSION"')[1][:200]


# --------------------------------------------------------------------------- #
# GLM3-01 - probe() timeout is caught locally, not by the fatal outer except
# --------------------------------------------------------------------------- #


def test_glm3_01_probe_timeout_returns_a_finding(monkeypatch, tmp_path):
    """RED at baseline: subprocess.TimeoutExpired escaped probe() into the
    outer `except Exception` in main(), i.e. the reinstall dialog (glm_verify_v2 §3).
    After the fix probe() returns it as an ordinary finding list."""
    spec = importlib.util.spec_from_file_location(
        "rf_windows_start", REPO / "build" / "windows_start.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def raise_timeout(*args, **kwargs):
        raise module.subprocess.TimeoutExpired(cmd=["python.exe"], timeout=120)

    monkeypatch.setattr(module.subprocess, "run", raise_timeout)
    findings = module.probe()
    assert isinstance(findings, list) and findings, "probe() must return the timeout as a finding"
    assert any("超时" in item for item in findings), findings
