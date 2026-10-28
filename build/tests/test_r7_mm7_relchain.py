"""R7 release-chain nails (MM7-01/02/03/05) - each test turns red if its fix is reverted.

MM7-01: the release-payload verifier must compare the WHOLE payload tree against the
        source tree, not an 11-file whitelist. The fake payload below tampers with a
        file that was never whitelisted (custom_backend/hidden.py) and deletes another
        (coscreen/x.py); pre-fix the verifier exits 0 on exactly this state.
MM7-02: release.yml and build_windows.bat must invoke the four static validators; the
        wiring greps fail while the calls are absent, and the mutation nails prove the
        wired validators exit non-zero once an asserted object is broken.
MM7-03: the string source of validate_import_review_static.cjs and the injection list of
        import_review_browser.cjs must be resolved from the <script src> manifest parsed
        out of custom_frontend/index.html (live load list), never from workflow_imports.js.
MM7-05: both start.sh must install -r build/requirements-app.txt; custom_frontend/start.sh
        must additionally fail fast (set -e + uvicorn existence check) instead of printing
        success URLs when pip or uvicorn is unusable. Probed with a fake tree and stubs.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
NODE = (
    os.environ.get("RF_NODE")
    or shutil.which("node")
    or r"C:\Users\16073\AppData\Local\hermes\node\node.exe"
)
BASH = shutil.which("bash")

FOUR_VALIDATORS = (
    "build/validate_import_review.py",
    "build/validate_screening_rounds.py",
    "build/validate_import_review_static.cjs",
    "build/validate_team_prisma_static.js",
)


# --------------------------------------------------------------------------- MM7-01

SOURCE_FILES = {
    "VERSION": "2.1.4\n",
    "custom_backend/main.py": 'print("main")\n',
    "custom_backend/hidden.py": 'print("hidden")\n',  # never in the old 11-file whitelist
    "custom_frontend/index.html": "<html></html>\n",
    "coscreen/x.py": "x = 1\n",
    "coscreen/assets/logo.txt": "logo\n",
}


def _write_tree(base: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        target = base / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8", newline="\n")


def _load_verifier():
    path = REPO / "build" / "verify_release_payload.py"
    spec = importlib.util.spec_from_file_location("rf_verify_release_payload", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fake_release_tree(tmp_path: Path) -> tuple[Path, Path]:
    """Fake source tree + windows-style staging payload mirroring every source file."""
    source = tmp_path / "repo"
    _write_tree(source, SOURCE_FILES)
    payload = source / "build" / "staging" / "ReviewFlow"
    _write_tree(payload, SOURCE_FILES)
    return source, payload


def test_mm7_01_tampered_non_whitelist_file_must_fail(tmp_path):
    verifier = _load_verifier()
    source, payload = _fake_release_tree(tmp_path)
    (payload / "custom_backend" / "hidden.py").write_text(
        'print("TAMPERED")\n', encoding="utf-8"
    )
    result = verifier.verify_payload(source, payload, "windows")
    assert result["ok"] is False
    assert any("custom_backend/hidden.py" in problem for problem in result["problems"])


def test_mm7_01_missing_payload_file_must_fail(tmp_path):
    verifier = _load_verifier()
    source, payload = _fake_release_tree(tmp_path)
    (payload / "coscreen" / "x.py").unlink()
    result = verifier.verify_payload(source, payload, "windows")
    assert result["ok"] is False
    assert any("coscreen/x.py" in problem for problem in result["problems"])


def test_mm7_01_extra_payload_file_must_fail(tmp_path):
    verifier = _load_verifier()
    source, payload = _fake_release_tree(tmp_path)
    (payload / "custom_backend" / "extra.py").write_text(
        'print("extra")\n', encoding="utf-8"
    )
    result = verifier.verify_payload(source, payload, "windows")
    assert result["ok"] is False
    assert any("extra.py" in problem for problem in result["problems"])


def test_mm7_01_clean_payload_passes_with_complete_manifest(tmp_path):
    verifier = _load_verifier()
    source, payload = _fake_release_tree(tmp_path)
    result = verifier.verify_payload(source, payload, "windows")
    assert result["ok"] is True, result["problems"]
    assert result["mode"] == "payload-compare"
    assert set(result["files"]) == set(SOURCE_FILES)


def test_mm7_01_degrade_mode_emits_full_source_manifest(tmp_path):
    verifier = _load_verifier()
    source = tmp_path / "repo"
    _write_tree(source, SOURCE_FILES)
    result = verifier.verify_payload(source, None, "windows")
    assert result["ok"] is True, result["problems"]
    assert result["mode"] == "source-manifest"
    assert set(result["files"]) == set(SOURCE_FILES)


def test_mm7_01_ci_must_not_silently_degrade_without_payload(tmp_path):
    verifier = _load_verifier()
    source = tmp_path / "repo"
    _write_tree(source, SOURCE_FILES)
    result = verifier.verify_payload(
        source, source / "build" / "staging" / "ReviewFlow", "windows",
        require_payload=True,
    )
    assert result["ok"] is False
    assert result["problems"]


def test_mm7_01_cli_end_to_end_on_fake_tree(tmp_path):
    """Drive the real CLI (the entry release.yml calls) against the fake tree."""
    source, payload = _fake_release_tree(tmp_path)
    script = source / "build" / "verify_release_payload.py"
    shutil.copyfile(REPO / "build" / "verify_release_payload.py", script)

    clean = subprocess.run(
        [sys.executable, str(script), "windows"],
        cwd=source, capture_output=True, text=True, timeout=120,
    )
    assert clean.returncode == 0, clean.stderr
    manifest = json.loads(
        (source / "release-assets" / "build-manifest-windows.json").read_text("utf-8")
    )
    assert set(manifest["files"]) == set(SOURCE_FILES)

    (payload / "custom_backend" / "hidden.py").write_text(
        'print("TAMPERED")\n', encoding="utf-8"
    )
    (payload / "coscreen" / "x.py").unlink()
    broken = subprocess.run(
        [sys.executable, str(script), "windows"],
        cwd=source, capture_output=True, text=True, timeout=120,
    )
    assert broken.returncode != 0
    assert "custom_backend/hidden.py" in broken.stderr
    assert "coscreen/x.py" in broken.stderr


def _frozen_source_tree(tmp_path: Path) -> Path:
    """Source tree shaped like the frozen builds: only coscreen/assets ships as data."""
    source = tmp_path / "repo"
    _write_tree(source, SOURCE_FILES)
    return source


def _macos_payload(tmp_path: Path) -> tuple[Path, Path]:
    """macOS layout: --add-data destinations under ReviewFlow.app/Contents/Frameworks."""
    source = _frozen_source_tree(tmp_path)
    payload = source / "build" / "dist" / "ReviewFlow.app" / "Contents" / "Frameworks"
    loose = {k: v for k, v in SOURCE_FILES.items() if not k.startswith("coscreen/")}
    loose["coscreen/assets/logo.txt"] = SOURCE_FILES["coscreen/assets/logo.txt"]
    _write_tree(payload, loose)
    return source, payload


def _linux_payload(tmp_path: Path) -> tuple[Path, Path]:
    """Linux layout: PyInstaller onedir data under AppDir/usr/bin/_internal."""
    source = _frozen_source_tree(tmp_path)
    payload = source / "build" / "work-linux" / "AppDir" / "usr" / "bin" / "_internal"
    loose = {k: v for k, v in SOURCE_FILES.items() if not k.startswith("coscreen/")}
    loose["coscreen/assets/logo.txt"] = SOURCE_FILES["coscreen/assets/logo.txt"]
    _write_tree(payload, loose)
    return source, payload


def test_mm7_01_macos_clean_payload_passes(tmp_path):
    """Reviewer probe regression: the loose coscreen/assets subtree must line up with
    the source keys (a prefixing bug previously made every macos/linux build fail)."""
    verifier = _load_verifier()
    source, payload = _macos_payload(tmp_path)
    result = verifier.verify_payload(source, payload, "macos")
    assert result["ok"] is True, result["problems"]
    assert result["mode"] == "payload-compare"
    # coscreen/*.py is frozen into the PyInstaller archive on macos/linux, so the
    # comparable manifest covers everything except the frozen python files.
    assert set(result["files"]) == set(SOURCE_FILES) - {"coscreen/x.py"}


def test_mm7_01_macos_tampered_asset_must_fail(tmp_path):
    verifier = _load_verifier()
    source, payload = _macos_payload(tmp_path)
    (payload / "coscreen" / "assets" / "logo.txt").write_text("evil\n", encoding="utf-8")
    result = verifier.verify_payload(source, payload, "macos")
    assert result["ok"] is False
    assert any("coscreen/assets/logo.txt" in p for p in result["problems"])


def test_mm7_01_linux_missing_asset_must_fail(tmp_path):
    verifier = _load_verifier()
    source, payload = _linux_payload(tmp_path)
    (payload / "coscreen" / "assets" / "logo.txt").unlink()
    result = verifier.verify_payload(source, payload, "linux")
    assert result["ok"] is False
    assert any("coscreen/assets/logo.txt" in p for p in result["problems"])


def test_mm7_01_macos_cli_end_to_end_on_fake_tree(tmp_path):
    source, payload = _macos_payload(tmp_path)
    script = source / "build" / "verify_release_payload.py"
    shutil.copyfile(REPO / "build" / "verify_release_payload.py", script)
    clean = subprocess.run(
        [sys.executable, str(script), "macos"],
        cwd=source, capture_output=True, text=True, timeout=120,
    )
    assert clean.returncode == 0, clean.stderr
    (payload / "coscreen" / "assets" / "logo.txt").write_text("evil\n", encoding="utf-8")
    broken = subprocess.run(
        [sys.executable, str(script), "macos"],
        cwd=source, capture_output=True, text=True, timeout=120,
    )
    assert broken.returncode != 0
    assert "coscreen/assets/logo.txt" in broken.stderr


# --------------------------------------------------------------------------- MM7-02

def test_mm7_02_release_yml_runs_all_four_validators_after_payload_verify():
    text = (REPO / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    anchor = text.index("verify_release_payload.py")
    for rel in FOUR_VALIDATORS:
        pos = text.find(rel)
        assert pos > anchor, f"{rel} must run after verify_release_payload.py in release.yml"


def test_mm7_02_build_windows_bat_runs_all_four_validators_after_step_4():
    text = (REPO / "build" / "build_windows.bat").read_text(encoding="utf-8")
    step4 = text.index("[4/6]")
    step5 = text.index("[5/6]")
    for rel in FOUR_VALIDATORS:
        name = rel.split("/")[-1]  # the bat invokes them as %SRCDIR%\<name>
        pos = text.find(name)
        assert step4 < pos < step5, f"{rel} must run between [4/6] and [5/6] in build_windows.bat"
    # R1 review: the JavaScript validators must be a hard requirement, not a skip.
    assert ":fail_no_node" in text, "missing node.exe must fail the build, not skip validators"
    assert "validators_node_skip" not in text and "validators_git_skip" not in text, (
        "the bat must run the same four validators CI runs - no silent degradation"
    )


@pytest.mark.skipif(not os.path.exists(NODE), reason="node runtime not found")
def test_mm7_02_team_prisma_static_runs_outside_a_git_checkout(tmp_path):
    """R1 review: a local Windows build tree (relocated / extracted, no .git) must
    still run validate_team_prisma_static.js - the validator degrades its git-diff
    augmentation, it must not crash - and it must still bite on a broken locale."""
    tree = tmp_path / "tree"
    shutil.copytree(REPO / "custom_frontend", tree / "custom_frontend")
    (tree / "build").mkdir()
    shutil.copyfile(
        REPO / "build" / "validate_team_prisma_static.js",
        tree / "build" / "validate_team_prisma_static.js",
    )
    clean = subprocess.run(
        [NODE, str(tree / "build" / "validate_team_prisma_static.js")],
        cwd=tree, capture_output=True, text=True, timeout=120,
    )
    assert clean.returncode == 0, clean.stderr
    with (tree / "custom_frontend" / "team_prisma_locale.js").open("a", encoding="utf-8") as handle:
        handle.write(
            "\n/*r1 nail*/RFLang.register([['n','Nail {0}','Nail {1}',"
            "'Nail','Nail','Nail','Nail','Nail','Nail','Nail']]);\n"
        )
    broken = subprocess.run(
        [NODE, str(tree / "build" / "validate_team_prisma_static.js")],
        cwd=tree, capture_output=True, text=True, timeout=120,
    )
    assert broken.returncode != 0
    assert "Mismatched placeholders" in broken.stderr


@pytest.mark.skipif(not os.path.exists(NODE), reason="node runtime not found")
def test_mm7_02_import_review_static_bites_when_live_module_breaks():
    """MM7-02 + MM7-03 behaviour nail: an unregistered display string in the module the
    browser actually loads must make the wired validator exit non-zero."""
    target = REPO / "custom_frontend" / "unified_import_workspace.js"
    original = target.read_bytes()
    try:
        with target.open("a", encoding="utf-8") as handle:
            handle.write(
                "\n/*mm7-02 nail*/const mm7Nail='Definitely Unregistered Nail Copy';\n"
            )
        proc = subprocess.run(
            [NODE, str(REPO / "build" / "validate_import_review_static.cjs")],
            cwd=REPO, capture_output=True, text=True, timeout=180,
        )
        assert proc.returncode != 0
        assert "Unregistered display strings" in proc.stderr
    finally:
        target.write_bytes(original)


@pytest.mark.skipif(not os.path.exists(NODE), reason="node runtime not found")
def test_mm7_02_team_prisma_static_bites_when_locale_row_breaks():
    target = REPO / "custom_frontend" / "team_prisma_locale.js"
    original = target.read_bytes()
    try:
        with target.open("a", encoding="utf-8") as handle:
            handle.write(
                "\n/*mm7-02 nail*/RFLang.register([['nail','Nail {0}','Nail {1}',"
                "'Nail','Nail','Nail','Nail','Nail','Nail','Nail']]);\n"
            )
        proc = subprocess.run(
            [NODE, str(REPO / "build" / "validate_team_prisma_static.js")],
            cwd=REPO, capture_output=True, text=True, timeout=180,
        )
        assert proc.returncode != 0
        assert "Mismatched placeholders" in proc.stderr
    finally:
        target.write_bytes(original)


# --------------------------------------------------------------------------- MM7-03

@pytest.mark.skipif(not os.path.exists(NODE), reason="node runtime not found")
def test_mm7_03_manifest_helper_resolves_live_workspace_module():
    helper = REPO / "build" / "script_manifest.cjs"
    assert helper.exists(), "MM7-03: shared <script src> manifest helper missing"
    code = (
        f"const m=require({json.dumps(str(helper))});"
        f"const root={json.dumps(str(REPO))};"
        "const list=m.loadScriptManifest(root);"
        "if(!list.includes('unified_import_workspace.js'))process.exit(1);"
        "if(list.includes('workflow_imports.js'))process.exit(2);"
        "if(m.findWorkspaceModule(root,list)!=='unified_import_workspace.js')process.exit(3);"
        "if(m.manifestFiles(list,['import_review_model.js','import_guide.js']).length!==2)process.exit(4);"
        "if(list.length<50)process.exit(5);"
        "console.log(list.length);"
    )
    proc = subprocess.run(
        [NODE, "-e", code], cwd=REPO, capture_output=True, text=True, timeout=120
    )
    assert proc.returncode == 0, proc.stderr


def test_mm7_03_static_gate_sources_strings_from_parsed_manifest():
    text = (REPO / "build" / "validate_import_review_static.cjs").read_text(encoding="utf-8")
    assert "loadScriptManifest" in text, "gate must parse the online load list"
    assert "findWorkspaceModule" in text, "gate must discover the live workspace module"
    assert "path.join(frontend,'workflow_imports.js')" not in text, (
        "gate must not read the unloaded workflow_imports.js as its string source"
    )


def test_mm7_03_browser_test_injections_come_from_parsed_manifest():
    text = (REPO / "build" / "tests" / "import_review_browser.cjs").read_text(encoding="utf-8")
    assert "loadScriptManifest" in text, "browser test must derive modules from the manifest"
    assert "findWorkspaceModule" in text, "browser test must discover the live panel module"
    assert "['import_review_model.js','workflow_imports.js','import_guide.js']" not in text, (
        "browser test must not hardcode the dead module in its injection list"
    )
    assert "unified_import_workspace.js" in text, (
        "browser test must trip-wire on the live workspace module being loaded"
    )
    # R1 review: the harness must not inject or exercise the unloaded dead panel at all.
    # The filename may only appear inside manifest tripwire assertions.
    dead_lines = [line for line in text.splitlines() if "workflow_imports.js" in line]
    assert dead_lines, "the dead-module tripwire assertion must stay in the harness"
    assert all("manifest.includes" in line for line in dead_lines), (
        "workflow_imports.js may only appear in manifest tripwires, never as an injected source"
    )


# --------------------------------------------------------------------------- MM7-05

PIP_STUB = """#!/bin/sh
# pip stub: like real pip, a missing -r <file> argument is a hard error.
prev=""
for arg in "$@"; do
  if [ "$prev" = "-r" ] && [ ! -f "$arg" ]; then
    echo "ERROR: Could not open requirements file: $arg" >&2
    exit 1
  fi
  prev="$arg"
done
if [ -n "$MM7_PIP_FAIL" ]; then echo "pip stub forced failure" >&2; exit 1; fi
exit 0
"""
UVICORN_STUB = '#!/bin/sh\necho "uvicorn stub started: $*"\nsleep 1\nexit 0\n'
SLEEP_STUB = "#!/bin/sh\nsleep 1\nexit 0\n"


def _stub(base: Path, rel: str, body: str) -> Path:
    target = base / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8", newline="\n")
    target.chmod(0o755)
    return target


def _run_script(script: Path, cwd: Path, extra_env: dict[str, str] | None = None):
    assert BASH, "bash is required for the MM7-05 behaviour nails"
    env = dict(os.environ)
    env.update(extra_env or {})
    return subprocess.run(
        [BASH, str(script)], cwd=cwd, env=env,
        capture_output=True, text=True, timeout=60,
    )


@pytest.mark.skipif(BASH is None, reason="bash not found")
def test_mm7_05_backend_reaches_uvicorn_line_with_build_requirements(tmp_path):
    """Review scenario A, fixed: with build/requirements-app.txt present the backend
    script must get past pip and actually exec .venv/bin/uvicorn."""
    tree = tmp_path / "tree"
    _write_tree(tree, {
        "build/requirements-app.txt": "fastapi>=0.110\n",
        "custom_backend/main.py": "app = None\n",
    })
    _stub(tree, ".venv/bin/python", SLEEP_STUB)
    _stub(tree, ".venv/bin/pip", PIP_STUB)
    _stub(tree, ".venv/bin/uvicorn", UVICORN_STUB)
    shutil.copyfile(REPO / "custom_backend" / "start.sh", tree / "custom_backend" / "start.sh")
    proc = _run_script(tree / "custom_backend" / "start.sh", tree)
    assert proc.returncode == 0, proc.stderr
    assert "uvicorn stub started" in proc.stdout + proc.stderr


@pytest.mark.skipif(BASH is None, reason="bash not found")
def test_mm7_05_backend_fails_when_requirements_file_missing(tmp_path):
    tree = tmp_path / "tree"
    _write_tree(tree, {"custom_backend/main.py": "app = None\n"})
    _stub(tree, ".venv/bin/python", SLEEP_STUB)
    _stub(tree, ".venv/bin/pip", PIP_STUB)
    _stub(tree, ".venv/bin/uvicorn", UVICORN_STUB)
    shutil.copyfile(REPO / "custom_backend" / "start.sh", tree / "custom_backend" / "start.sh")
    proc = _run_script(tree / "custom_backend" / "start.sh", tree)
    assert proc.returncode != 0
    assert "uvicorn stub started" not in proc.stdout + proc.stderr


@pytest.mark.skipif(BASH is None, reason="bash not found")
def test_mm7_05_frontend_without_uvicorn_exits_nonzero_no_success_url(tmp_path):
    """Review scenario D: uvicorn missing must exit non-zero without success URLs.
    Pre-fix the script printed Backend:/Frontend: and exited 0."""
    tree = tmp_path / "tree"
    _write_tree(tree, {
        "build/requirements-app.txt": "fastapi>=0.110\n",
        "custom_frontend/index.html": "<html></html>\n",
    })
    _stub(tree, ".venv/bin/python", SLEEP_STUB)
    _stub(tree, ".venv/bin/pip", PIP_STUB)
    path_stub = tmp_path / "pathstubs"
    _stub(path_stub, "python3", SLEEP_STUB)
    shutil.copyfile(REPO / "custom_frontend" / "start.sh", tree / "custom_frontend" / "start.sh")
    proc = _run_script(
        tree / "custom_frontend" / "start.sh", tree,
        {"PATH": str(path_stub) + os.pathsep + os.environ["PATH"]},
    )
    assert proc.returncode != 0, proc.stdout + proc.stderr
    assert "Backend:" not in proc.stdout
    assert "Frontend:" not in proc.stdout


@pytest.mark.skipif(BASH is None, reason="bash not found")
def test_mm7_05_frontend_pip_failure_exits_without_success_url(tmp_path):
    """set -e nail: a failing pip must abort before any success message."""
    tree = tmp_path / "tree"
    _write_tree(tree, {
        "build/requirements-app.txt": "fastapi>=0.110\n",
        "custom_frontend/index.html": "<html></html>\n",
    })
    _stub(tree, ".venv/bin/python", SLEEP_STUB)
    _stub(tree, ".venv/bin/pip", PIP_STUB)
    _stub(tree, ".venv/bin/uvicorn", UVICORN_STUB)
    path_stub = tmp_path / "pathstubs"
    _stub(path_stub, "python3", SLEEP_STUB)
    shutil.copyfile(REPO / "custom_frontend" / "start.sh", tree / "custom_frontend" / "start.sh")
    proc = _run_script(
        tree / "custom_frontend" / "start.sh", tree,
        {
            "PATH": str(path_stub) + os.pathsep + os.environ["PATH"],
            "MM7_PIP_FAIL": "1",
        },
    )
    assert proc.returncode != 0
    assert "Backend:" not in proc.stdout
    assert "Frontend:" not in proc.stdout


@pytest.mark.skipif(BASH is None, reason="bash not found")
def test_mm7_05_frontend_healthy_tree_prints_success_urls(tmp_path):
    tree = tmp_path / "tree"
    _write_tree(tree, {
        "build/requirements-app.txt": "fastapi>=0.110\n",
        "custom_frontend/index.html": "<html></html>\n",
    })
    _stub(tree, ".venv/bin/python", SLEEP_STUB)
    _stub(tree, ".venv/bin/pip", PIP_STUB)
    _stub(tree, ".venv/bin/uvicorn", UVICORN_STUB)
    path_stub = tmp_path / "pathstubs"
    _stub(path_stub, "python3", SLEEP_STUB)
    shutil.copyfile(REPO / "custom_frontend" / "start.sh", tree / "custom_frontend" / "start.sh")
    proc = _run_script(
        tree / "custom_frontend" / "start.sh", tree,
        {"PATH": str(path_stub) + os.pathsep + os.environ["PATH"]},
    )
    assert proc.returncode == 0, proc.stderr
    assert "Backend: http://localhost:8613/api" in proc.stdout
    assert "Frontend: http://localhost:8614" in proc.stdout
