"""R9 L7c nails - the Python gate validators (b02·F1-57/F1-83,
b08·F1-58/F1-59/F1-68/F1-72/F1-73, b09·F1-87/F1-88).

The behavioural nails run the REAL validator sources. F1-58, F1-68 and F1-73
are driven through a stub sibling gate so they need no HTTP stack; the
F1-83/F1-87/F1-88 nails load verify_release_payload.py and drive its CLI on a
synthetic source/payload tree. F1-59 and F1-72 are structural nails because the
defect is code shape (positional fixture INSERTs, an unremoved mkdtemp tree) and
the surrounding behaviour takes a full validation run.

Every test's docstring names the pre-fix behaviour (the inventory probe form)
and each check has a control that must stay unchanged.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
PYTHON = sys.executable
TEMP_ROOT = Path(os.environ.get("RF_R9_TMP_ROOT","E:/rf214tmp")+"/r9/l7c/temp")


def _env(**extra):
    """Child environment that keeps every scratch file on E: (C: must stay free)."""
    TEMP_ROOT.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, TEMP=str(TEMP_ROOT), TMP=str(TEMP_ROOT),
               PYTHONPATH=str(REPO), REVIEWFLOW_MODE="server")
    # 2026-11-13：剥掉环境变量 CI——夹具仓没有 build/staging 载荷树，
    # 泄漏进去会触发 verify_release_payload 的严格分支（"CI must not
    # degrade"），真 CI 与 CI 模拟下五钉皆假红。本组测试自注 GITHUB_SHA
    # 构造受控环境；验证器的真实 CI 行为由发布流水线自身把关。
    env.pop("CI", None)
    env.update(extra)
    return env


# --------------------------------------------------------------------------- #
# b08·F1-58 - fetchone() None must not become a bare TypeError
# --------------------------------------------------------------------------- #
STUB_HEADER = '''\
import sys, sqlite3
from pathlib import Path
REPO = r"{repo}"
sys.path.insert(0, REPO)
DATA = Path(r"{data}")
DATA.mkdir(parents=True, exist_ok=True)
owner_db = DATA / "owner.db"
from coscreen import review_imports as store
store.migrate(owner_db)
with sqlite3.connect(owner_db) as conn:
    conn.execute("DELETE FROM workflow_sources")
    conn.commit()
base = "/api/tasks/demo"

class Client:
    headers = {{}}
    status_code = 200
    text = "{{}}"
    def get(self, *args, **kwargs): return self
    def post(self, *args, **kwargs): return self

client = Client()

def call(method, path, **kwargs): return {{}}

class Ctx:
    owner_db = owner_db

ctx = Ctx()
team = {{"owner_user_id": 1}}
owner = {{"token": "t", "user_id": 1}}
member = {{"token": "m", "user_id": 2}}
'''


def _gate_with_stub(tmp_path: Path, gate: str) -> Path:
    work = tmp_path / "gate"
    work.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(REPO / "build" / gate, work / gate)
    (work / "validate_team_prisma.py").write_text(
        STUB_HEADER.format(repo=REPO.as_posix(), data=(work / "data").as_posix()),
        encoding="utf-8", newline="\n")
    return work / gate


def test_f1_58_empty_source_fixture_fails_with_a_reason_not_a_type_error(tmp_path):
    """RED at baseline: `source=conn.execute('SELECT id FROM workflow_sources LIMIT
    1').fetchone()[0]` raised `TypeError: 'NoneType' object is not subscriptable`
    when the fixture produced no source row - the message named neither the gate
    nor the missing precondition."""
    gate = _gate_with_stub(tmp_path, "validate_import_review.py")
    proc = subprocess.run([PYTHON, str(gate)], cwd=REPO, capture_output=True,
                          text=True, timeout=300, env=_env())
    assert proc.returncode != 0
    combined = proc.stdout + proc.stderr
    assert "fixture precondition failed" in combined, combined
    assert "workflow_sources" in combined
    assert "NoneType" not in combined


# --------------------------------------------------------------------------- #
# b08·F1-59 - fixture INSERTs must name their columns
# --------------------------------------------------------------------------- #
def test_f1_59_no_positional_values_insert_remains_in_the_fixture():
    """RED at baseline: 4 of the 7 fixture INSERTs were `INSERT INTO t VALUES(...)`,
    so a schema column added/reordered in coscreen/review_imports.py silently
    misplaced every subsequent value. The named-column form fails loudly instead
    (sqlite raises "no column named ..."), which is what the gate wants."""
    source = (REPO / "build" / "validate_import_review.py").read_text(encoding="utf-8")
    positional = re.findall(r"INSERT\s+INTO\s+[A-Za-z_]+\s+VALUES", source, flags=re.I)
    assert positional == [], f"positional fixture INSERT(s) remain: {positional}"
    # Control: the fixture still inserts into all four tables it always did.
    for table in ("workflow_import_batches", "workflow_import_files",
                  "workflow_import_members", "workflow_import_candidate_members"):
        assert re.search(rf"INSERT\s+INTO\s+{table}\s*\(", source), table


# --------------------------------------------------------------------------- #
# b02·F1-57 - the write endpoint needs negative authorization cases
# --------------------------------------------------------------------------- #
def test_f1_57_activate_has_member_and_outsider_403_negatives():
    """RED at baseline: :82-86 held only GET negatives and both activate calls
    (:91-100) were made by the owner, so a regression that let a member or a
    non-member activate records would have kept the gate green."""
    source = (REPO / "build" / "validate_import_review.py").read_text(encoding="utf-8")
    assert "POST activation of a real batch is owner-only" in source or \
           "F1-57" in source, "the negative cases must be documented in the gate"
    # Both refusals exist and are asserted as 403, on the activate path.
    activate_negatives = re.findall(
        r"refused=client\.post\(path\+'/activate'[^\n]*\nassert refused\.status_code==403",
        source)
    assert len(activate_negatives) == 2, activate_negatives
    # Control: the positive owner activation is still exercised, unchanged.
    assert "activated['activated_source_records']==attention['review_counts']['ready']==37" in source


# --------------------------------------------------------------------------- #
# b08·F1-68 / b08·F1-73 - the gates must refuse the optimized interpreter
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("gate", ["validate_screening_rounds.py", "validate_team_prisma.py"])
def test_f1_68_f1_73_optimized_interpreter_is_refused(gate):
    """RED at baseline: every check in both gates is a bare `assert`, so
    `python -O` compiled them away and the gate printed "passed" and exited 0
    (`gate OK under -O, exit 0` in the inventory probe)."""
    proc = subprocess.run([PYTHON, "-O", str(REPO / "build" / gate)], cwd=REPO,
                          capture_output=True, text=True, timeout=180, env=_env())
    assert proc.returncode != 0
    combined = proc.stdout + proc.stderr
    assert "without -O" in combined
    assert "passed" not in proc.stdout.lower(), "an unchecked run must not print a pass"


def test_f1_68_validate_import_review_also_refuses_o():
    """The sibling gate runs validate_team_prisma.py through runpy; under -O its
    own asserts vanish too. It must refuse as well, not inherit a silent pass."""
    proc = subprocess.run([PYTHON, "-O", str(REPO / "build" / "validate_import_review.py")],
                          cwd=REPO, capture_output=True, text=True, timeout=180, env=_env())
    assert proc.returncode != 0
    assert "without -O" in proc.stdout + proc.stderr


# --------------------------------------------------------------------------- #
# b08·F1-72 - the disposable validation tree must be removed
# --------------------------------------------------------------------------- #
def test_f1_72_disposable_tree_is_removed_when_the_gate_exits(tmp_path):
    """RED at baseline: 0 rmtree / atexit / finally / cleanup in
    validate_team_prisma.py - every run, passing or failing, left a
    research-shaped data directory behind in TEMP.

    The child's TEMP is a dedicated directory on E: (the run's C: drive must
    stay free, and the deep per-task backup filenames overflow the short
    pytest tmp prefix), so the assertion sees exactly the trees this one gate
    created and nothing else."""
    temp_dir = Path(os.environ.get("RF_R9_TMP_ROOT","E:/rf214tmp")+"/r9/l7c/temp") / f"f1_72_{os.getpid()}_{next(tempfile._get_candidate_names())}"
    temp_dir.mkdir(parents=True, exist_ok=True)
    try:
        env = _env(TEMP=str(temp_dir), TMP=str(temp_dir))
        proc = subprocess.run([PYTHON, str(REPO / "build" / "validate_team_prisma.py")],
                              cwd=REPO, capture_output=True, text=True, timeout=600, env=env)
        assert proc.returncode == 0, proc.stderr[-2000:]
        leftovers = [p for p in temp_dir.glob("reviewflow-team-prisma-validation-*")]
        assert leftovers == [], f"disposable data left behind: {leftovers}"
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_f1_72_cleanup_is_registered_not_left_to_normal_exit():
    source = (REPO / "build" / "validate_team_prisma.py").read_text(encoding="utf-8")
    assert "atexit.register(_remove_disposable_data)" in source, (
        "cleanup must survive a failing run (an exception ends the gate)")


# --------------------------------------------------------------------------- #
# b02·F1-83 - only the canonical VERSION location counts
# --------------------------------------------------------------------------- #
def _load_verifier():
    path = REPO / "build" / "verify_release_payload.py"
    spec = importlib.util.spec_from_file_location("rf_r9_verify_payload", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _source_tree(tmp_path: Path, *, version: str = "2.1.4\n") -> Path:
    source = tmp_path / "repo"
    (source / "build").mkdir(parents=True)
    (source / "VERSION").write_text(version, encoding="utf-8")
    for directory in ("custom_backend", "custom_frontend", "coscreen", "coscreen/assets"):
        (source / directory).mkdir(parents=True)
        (source / directory / "f.txt").write_text("x\n", encoding="utf-8")
    for rel in ("build/launcher.py", "build/windows_start.py", "build/requirements-app.txt",
                "build/repair_reviewflow.bat", "build/windows_smoke.py", "build/icon.ico",
                "build/icon_256.png", "THIRD_PARTY_NOTICES.md"):
        path = source / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("content of " + rel + "\n", encoding="utf-8")
    return source


def _windows_payload(source: Path) -> Path:
    payload = source / "build" / "staging" / "ReviewFlow"
    for directory in ("custom_backend", "custom_frontend", "coscreen", "coscreen/assets"):
        (payload / directory).mkdir(parents=True, exist_ok=True)
        (payload / directory / "f.txt").write_text("x\n", encoding="utf-8")
    for rel in ("build/launcher.py", "build/windows_start.py", "build/requirements-app.txt",
                "build/repair_reviewflow.bat", "build/windows_smoke.py", "build/icon.ico",
                "build/icon_256.png", "THIRD_PARTY_NOTICES.md"):
        (payload / Path(rel).name).write_bytes((source / rel).read_bytes())
    (payload / "VERSION").write_bytes((source / "VERSION").read_bytes())
    return payload


def test_f1_83_nested_copy_cannot_vouch_for_a_stale_canonical_version(tmp_path):
    """RED at baseline: `any(... for path in payload_root.rglob('VERSION'))`, so a
    payload whose canonical VERSION was 0.0.0 passed as soon as some nested copy
    (`runtime/embedded/VERSION`, a docs copy) matched the source bytes.
    Synthetic tree from the inventory: `规范位置 VERSION=0.0.0、顶层目录不存在 →
    ok=True`."""
    verifier = _load_verifier()
    source = _source_tree(tmp_path)
    payload = _windows_payload(source)
    (payload / "VERSION").write_text("0.0.0\n", encoding="utf-8")
    nested = payload / "runtime" / "embedded"
    nested.mkdir(parents=True)
    (nested / "VERSION").write_bytes((source / "VERSION").read_bytes())
    result = verifier.verify_payload(source, payload, "windows")
    assert result["ok"] is False
    assert any("VERSION" in problem for problem in result["problems"]), result["problems"]


def test_f1_83_canonical_version_alone_still_passes(tmp_path):
    """Control: the shipped canonical VERSION is unchanged by the new rule."""
    verifier = _load_verifier()
    source = _source_tree(tmp_path)
    payload = _windows_payload(source)
    result = verifier.verify_payload(source, payload, "windows")
    assert result["ok"] is True, result["problems"]
    assert result["mode"] == "payload-compare"


def test_f1_83_generated_tree_copy_cannot_vouch_for_a_missing_shipped_version(tmp_path):
    """The shipped VERSION is gone and only the build-time runtime copy carries
    the right bytes - exactly the reported fail-open (a downloaded tree vouching
    for a file the package does not ship)."""
    verifier = _load_verifier()
    source = _source_tree(tmp_path)
    payload = _windows_payload(source)
    (payload / "VERSION").unlink()
    runtime = payload / "runtime"
    runtime.mkdir()
    (runtime / "VERSION").write_bytes((source / "VERSION").read_bytes())
    result = verifier.verify_payload(source, payload, "windows")
    assert result["ok"] is False
    assert any("VERSION" in problem for problem in result["problems"]), result["problems"]


def test_f1_83_nested_non_generated_copy_is_still_accepted(tmp_path):
    """Control for R2: a legitimately nested shipped copy (PyInstaller 6.x puts
    --add-data output under _internal/) still passes - the rule only stops
    generated/downloaded trees from vouching, it does not demand one exact path."""
    verifier = _load_verifier()
    source = _source_tree(tmp_path)
    payload = _windows_payload(source)
    (payload / "VERSION").unlink()
    internal = payload / "usr" / "bin" / "_internal"
    internal.mkdir(parents=True)
    (internal / "VERSION").write_bytes((source / "VERSION").read_bytes())
    result = verifier.verify_payload(source, payload, "windows")
    assert result["ok"] is True, result["problems"]


# --------------------------------------------------------------------------- #
# b09·F1-88 - VERSION is read as UTF-8, like it is written
# --------------------------------------------------------------------------- #
def test_f1_88_version_read_is_encoding_symmetric():
    verifier = _load_verifier()
    assert "utf-8" in inspect_source(verifier.read_version), \
        "read_version must pin the codec instead of using the host default"


def inspect_source(function) -> str:
    import inspect
    return inspect.getsource(function)


def test_f1_88_non_ascii_version_survives_a_cp936_host(tmp_path):
    """RED at baseline: `(root/'VERSION').read_text()` used the host default
    codec - the inventory probe produced `'2.1.4-β' -> '2.1.4-尾'` under
    `-X utf8=0` and a BOM raised `UnicodeDecodeError: 'gbk' codec`."""
    source = _source_tree(tmp_path, version="2.1.4-β\n")
    script = source / "build" / "verify_release_payload.py"
    shutil.copyfile(REPO / "build" / "verify_release_payload.py", script)
    proc = subprocess.run([PYTHON, "-X", "utf8=0", str(script), "windows"],
                          cwd=source, capture_output=True, text=True, timeout=180,
                          env={k: v for k, v in _env().items() if k != "PYTHONIOENCODING"})
    assert proc.returncode == 0, proc.stderr
    manifest = json.loads((source / "release-assets" /
                           "build-manifest-windows.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "2.1.4-β"


def test_f1_88_bom_prefixed_version_is_not_a_decode_crash(tmp_path):
    source = _source_tree(tmp_path)
    (source / "VERSION").write_bytes("\ufeff2.1.4\n".encode("utf-8"))
    script = source / "build" / "verify_release_payload.py"
    shutil.copyfile(REPO / "build" / "verify_release_payload.py", script)
    proc = subprocess.run([PYTHON, "-X", "utf8=0", str(script), "windows"],
                          cwd=source, capture_output=True, text=True, timeout=180,
                          env={k: v for k, v in _env().items() if k != "PYTHONIOENCODING"})
    assert proc.returncode == 0, proc.stderr
    manifest = json.loads((source / "release-assets" /
                           "build-manifest-windows.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "2.1.4"


# --------------------------------------------------------------------------- #
# b09·F1-87 - the manifest commit is never null
# --------------------------------------------------------------------------- #
def test_f1_87_missing_github_sha_falls_back_to_head(tmp_path):
    """RED at baseline: `'commit': os.environ.get('GITHUB_SHA')` wrote
    `"commit": null` and exited 0 - the field tying hashes to a revision became
    meaningless without any signal."""
    source = _source_tree(tmp_path)
    script = source / "build" / "verify_release_payload.py"
    shutil.copyfile(REPO / "build" / "verify_release_payload.py", script)
    env = {k: v for k, v in _env().items() if k != "GITHUB_SHA"}
    proc = subprocess.run([PYTHON, str(script), "windows"], cwd=source,
                          capture_output=True, text=True, timeout=180, env=env)
    assert proc.returncode == 0, proc.stderr
    manifest = json.loads((source / "release-assets" /
                           "build-manifest-windows.json").read_text(encoding="utf-8"))
    assert manifest["commit"] is not None
    assert isinstance(manifest["commit"], str) and manifest["commit"].strip()


def test_f1_87_explicit_github_sha_is_used_verbatim(tmp_path):
    """Control: CI's GITHUB_SHA wins, exactly as before."""
    source = _source_tree(tmp_path)
    script = source / "build" / "verify_release_payload.py"
    shutil.copyfile(REPO / "build" / "verify_release_payload.py", script)
    env = _env(GITHUB_SHA="0123456789abcdef0123456789abcdef01234567")
    proc = subprocess.run([PYTHON, str(script), "windows"], cwd=source,
                          capture_output=True, text=True, timeout=180, env=env)
    assert proc.returncode == 0, proc.stderr
    manifest = json.loads((source / "release-assets" /
                           "build-manifest-windows.json").read_text(encoding="utf-8"))
    assert manifest["commit"] == "0123456789abcdef0123456789abcdef01234567"


def test_f1_87_resolve_commit_never_returns_none():
    verifier = _load_verifier()
    assert verifier.resolve_commit({}) not in (None, "")
