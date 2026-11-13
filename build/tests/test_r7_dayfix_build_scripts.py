"""R7 dayfix nails (F1-1/F1-2) for the Linux/macOS packaging scripts.

F1-1: build_linux.sh must fetch appimagetool from an immutable tagged release (the
      AppImageKit "continuous" channel it used is marked obsolete upstream) and must
      verify the downloaded bytes against a pinned sha256 BEFORE executing them. The
      behaviour nails run the REAL script end to end in a fake tree with stubbed
      python3/curl (everything else - cp, chmod, sha256sum - stays real):
        * tampered download (bytes whose digest differs from the pinned one) must
          abort with a non-zero status and must never execute the downloaded file
          (pre-fix there is no gate at all, the file is chmod-ed and executed: red);
        * the same control flow with the pinned digest replaced by the stub payload's
          real digest must pass the gate and run the tool (proves the gate is a
          comparison, not an unconditional failure);
        * the URL the stub curl receives must be the pinned tagged-release URL.
F1-2: the directly installed build tools (pip, wheel, pyinstaller) must carry explicit
      lower+upper version bounds in both packaging scripts (pre-fix pyinstaller is
      installed bare: red). Full --require-hashes locking of requirements-app.txt is
      deliberately out of scope for this fix; the bounds only pin what the scripts
      install directly.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
BASH = shutil.which("bash")
# Same node discovery chain as test_r7_mm7_relchain.py: build_linux.sh runs
# the JavaScript validators through `node` (MM9-07), so the behaviour nails
# must be able to put a real node on the stubbed PATH even when the invoking
# shell does not export one.
NODE = (
    os.environ.get("RF_NODE")
    or shutil.which("node")
    or r"C:\Users\16073\AppData\Local\hermes\node\node.exe"
)

PINNED_URL_RE = re.compile(
    r'APPIMAGETOOL_URL="https://github\.com/AppImage/appimagetool/releases/download/'
    r'(?P<tag>[0-9][0-9.]*)/appimagetool-x86_64\.AppImage"'
)
PINNED_SHA_RE = re.compile(r'APPIMAGETOOL_SHA256="([0-9a-f]{64})"')


def _p(path: Path) -> str:
    """POSIX-style path string for values passed into bash via the environment."""
    return path.resolve().as_posix()


PYTHON_STUB_BODY = """#!/bin/sh
# Minimal python stand-in for the appimagetool stage of build_linux.sh. The same
# body serves both the outer `python3` (venv creation) and the venv's own python
# (pip no-op, PyInstaller produces a stub --distpath tree).
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
# curl stand-in: records the requested URL and serves a prepared payload file
# instead of the network. The build script's own sha256sum gate stays real.
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

# What the stub curl "downloads": a tiny sh tool that records its arguments and
# produces the expected output artifact. If the build script ever executes a
# download that failed its digest gate, RF_TOOL_LOG appears and the nail goes red.
FAKE_APPIMAGETOOL = """#!/bin/sh
printf '%s\\n' "$@" > "$RF_TOOL_LOG"
out="$2"
mkdir -p "$(dirname "$out")"
printf 'fake-appimage' > "$out"
"""


def _stub(base: Path, rel: str, body: str) -> Path:
    target = base / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8", newline="\n")
    target.chmod(0o755)
    return target


def _fake_tree(tmp_path: Path, *, digest_override: str | None = None) -> tuple[Path, Path, Path]:
    """Fake checkout of the files build_linux.sh touches before appimagetool.

    The build script itself is the REAL repo copy (optionally with its pinned
    digest constant rewritten, to prove the pass path of the same gate).
    MM9-07 wired the static validators into the script, so the fake tree also
    carries the real custom_frontend/ tree and the two JavaScript validators
    (+ their script_manifest.cjs helper): the node gates run for real inside
    the behaviour nails, exactly like a local build would.
    """
    tree = tmp_path / "tree"
    (tree / "build").mkdir(parents=True)
    script = (REPO / "build" / "build_linux.sh").read_text(encoding="utf-8")
    if digest_override is not None:
        assert PINNED_SHA_RE.search(script), "pinned digest constant missing from script"
        script = PINNED_SHA_RE.sub(f'APPIMAGETOOL_SHA256="{digest_override}"', script)
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
    payload = tmp_path / "fake-appimagetool"
    payload.write_text(FAKE_APPIMAGETOOL, encoding="utf-8", newline="\n")
    payload.chmod(0o755)
    python_stub = tmp_path / "python-stub.sh"
    python_stub.write_text(PYTHON_STUB_BODY, encoding="utf-8", newline="\n")
    python_stub.chmod(0o755)
    _stub(stubs, "python3", PYTHON_STUB_BODY)
    _stub(stubs, "curl", CURL_STUB)
    return tree, stubs, python_stub


def _run_build(tree: Path, stubs: Path, tmp_path: Path, python_stub: Path) -> tuple[subprocess.CompletedProcess, Path, Path]:
    curl_log = tmp_path / "curl.log"
    tool_log = tmp_path / "tool.log"
    env = dict(os.environ)
    # MM9-07: build_linux.sh now runs the JavaScript validators via `node`;
    # add the discovered node, but keep stubs first: Homebrew's bin directory
    # can also contain python3 and curl.
    if NODE and os.path.exists(NODE):
        env["PATH"] = str(Path(NODE).parent) + os.pathsep + env["PATH"]
    env["PATH"] = str(stubs) + os.pathsep + env["PATH"]
    env["RF_PYTHON_STUB"] = _p(python_stub)
    env["RF_FAKE_PAYLOAD"] = _p(tmp_path / "fake-appimagetool")
    env["RF_CURL_LOG"] = _p(curl_log)
    env["RF_TOOL_LOG"] = _p(tool_log)
    assert BASH, "bash is required for the F1-1 behaviour nails"
    proc = subprocess.run(
        [BASH, str(tree / "build" / "build_linux.sh")],
        cwd=tree, env=env, capture_output=True, text=True, timeout=120,
    )
    return proc, curl_log, tool_log


# ----------------------------------------------------------------------------- F1-1

def test_f1_1_appimagetool_url_is_pinned_tagged_release():
    script = (REPO / "build" / "build_linux.sh").read_text(encoding="utf-8")
    match = PINNED_URL_RE.search(script)
    assert match, "appimagetool must be fetched via APPIMAGETOOL_URL from a tagged release"
    assert "releases/download/continuous/" not in script, (
        "the obsolete continuous channel must not be downloaded from"
    )
    assert PINNED_SHA_RE.search(script), "the pinned sha256 constant must sit next to the URL"


@pytest.mark.skipif(BASH is None, reason="bash not found")
def test_f1_1_tampered_download_aborts_and_is_never_executed(tmp_path):
    """The served bytes never match the pinned digest, so the real sha256sum gate
    must abort the build before anything executes the download."""
    tree, stubs, python_stub = _fake_tree(tmp_path)
    proc, curl_log, tool_log = _run_build(tree, stubs, tmp_path, python_stub)
    assert proc.returncode != 0, "digest mismatch must abort the build"
    assert not tool_log.exists(), "a download that failed verification was executed"
    assert not (tree / "build" / "dist" / "ReviewFlow-x86_64.AppImage").exists()
    assert curl_log.exists() and curl_log.read_text(encoding="utf-8").strip()


@pytest.mark.skipif(BASH is None, reason="bash not found")
def test_f1_1_matching_digest_passes_gate_and_runs_tool(tmp_path):
    """Same control flow with the pinned digest replaced by the served payload's
    real digest: the gate passes, the tool runs, the artifact is produced."""
    payload = tmp_path / "fake-appimagetool"
    payload.write_text(FAKE_APPIMAGETOOL, encoding="utf-8", newline="\n")
    digest = hashlib.sha256(payload.read_bytes()).hexdigest()
    tree, stubs, python_stub = _fake_tree(tmp_path, digest_override=digest)
    proc, curl_log, tool_log = _run_build(tree, stubs, tmp_path, python_stub)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert tool_log.exists(), "the verified tool must have been executed"
    assert (tree / "build" / "dist" / "ReviewFlow-x86_64.AppImage").exists()
    assert "FAILED" not in proc.stdout + proc.stderr


@pytest.mark.skipif(BASH is None, reason="bash not found")
def test_f1_1_curl_receives_the_pinned_url(tmp_path):
    tree, stubs, python_stub = _fake_tree(tmp_path)
    proc, curl_log, _tool_log = _run_build(tree, stubs, tmp_path, python_stub)
    assert curl_log.exists(), proc.stdout + proc.stderr
    requested = curl_log.read_text(encoding="utf-8").strip().splitlines()
    assert requested and all("releases/download" in url for url in requested), requested
    assert all("/continuous/" not in url for url in requested), requested


# ----------------------------------------------------------------------------- F1-2

BOUNDED = {
    "pip": r'"pip>=\d+(?:\.\d+)*,<\d+"',
    "wheel": r'"wheel>=\d+(?:\.\d+)*,<\d+"',
    "pyinstaller": r'"pyinstaller>=\d+\.\d+(?:\.\d+)*,<\d+"',
}


@pytest.mark.parametrize("script_name", ["build_linux.sh", "build_macos.sh"])
def test_f1_2_directly_installed_build_tools_are_version_bounded(script_name):
    text = (REPO / "build" / script_name).read_text(encoding="utf-8")
    for tool, pattern in BOUNDED.items():
        assert re.search(pattern, text), (
            f"{script_name} must install {tool} with explicit lower+upper bounds"
        )
        if tool == "pyinstaller":
            for line in text.splitlines():
                if "pip install" in line:
                    assert "pyinstaller" not in line or "pyinstaller>=" in line, (
                        f"{script_name}: pyinstaller installed without a version specifier: {line.strip()}"
                    )
