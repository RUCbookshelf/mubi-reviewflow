"""R26-I real-child isolation acceptance (Terra argv ruling, 2026-11-17).

Every real execution in this file happens inside a fresh per-test scratch run
root (``<run>``): a byte-copy of the real entry, the plan's 30 files
(existence stubs unless a case needs behaviour), one mini pytest file, plus
``<run>/state`` and ``<run>/tmp``. The outer entry is invoked directly as
``python -I -B -X utf8=<mode> <scratch>/build/run_validation.py``; the R26-I
child bootstrap is only ever emitted by the product's own build_plan().
Spawned interpreters always run with
``-I -B`` and the safe synthetic sub-run env (COBOOKSHELF_DATA_DIR +
TEMP/TMP/TMPDIR/RF_R9_TMP/RF_R9_TMP_ROOT under ``<run>``, no REVIEWFLOW_*,
no PYTHON*, no RF_* defaults), with cwd inside ``<run>`` and stdin closed.
Child flag evidence travels through a nonce-named state file the mini test
writes BEFORE asserting, so it survives pytest stdout capture and assert
short-circuit. Node steps are never launched (fail-fast stops at step 1 or
2); a real Node is only resolved via the product's own shutil.which
semantics, and cases needing resolution skip where no real Node resolves
(same convention as the rest of this suite; the missing-Node exit-2 case
never skips). Interpreter defaults (UTF-8 mode) are measured at runtime,
never assumed: locale / C-locale coercion / future interpreter defaults
must not flip these pins by accident.
"""
from __future__ import annotations

import dataclasses
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
ENTRY_REL = Path("build") / "run_validation.py"

# Real product resolve semantics (resolve_node): a non-blank RF_NODE goes
# through shutil.which (bare names resolve via PATH, absolute paths must
# exist and be accessible); a blank one falls back to PATH. No platform-
# pinned fallback is assumed. A real Node is required for plan resolution;
# the node steps themselves are never launched here (fail-fast bounds every
# execution at step 1 or 2).
_node_requested = (os.environ.get("RF_NODE") or "").strip()
_node_resolved = (shutil.which(_node_requested) if _node_requested
                  else shutil.which("node"))
NODE = os.path.abspath(_node_resolved) if _node_resolved else None
_needs_node = pytest.mark.skipif(
    NODE is None, reason="node runtime not resolvable via shutil.which")

# R26-I (Terra argv ruling): fixed child bootstrap, byte-equal to the product
# PY_CHILD_BOOTSTRAP constant; drift is red by design.
PY_CHILD_BOOTSTRAP_RULING = (
    'import runpy,sys;'
    'root=sys.argv.pop(1);kind=sys.argv.pop(1);entry=sys.argv.pop(1);'
    'sys.path.insert(0,root);sys.argv[0]=entry;'
    "runpy.run_module(entry,run_name='__main__') if kind=='module' "
    "else runpy.run_path(entry,run_name='__main__')"
)

PROBE_MINI_FAIL_SOURCE = '''\
"""R26-I real-child probe: records the actual child flags (nonce-tagged) to
the evidence file BEFORE asserting, so the evidence survives both assert
short-circuit and pytest stdout capture."""
import json
import os
import sys

import pytest


def test_r26i_real_child_flags():
    flags = {
        "isolated": sys.flags.isolated,
        "dont_write_bytecode": sys.flags.dont_write_bytecode,
        "utf8_mode": sys.flags.utf8_mode,
        "nonce": os.environ["RF_PROBE_EVIDENCE_NONCE"],
    }
    with open(os.environ["RF_PROBE_EVIDENCE"], "w", encoding="utf-8") as handle:
        json.dump(flags, handle)
    assert flags["isolated"] == 1, "isolated"
    assert flags["dont_write_bytecode"] == 1, "dont_write_bytecode"
    assert flags["utf8_mode"] == int(os.environ["RF_PROBE_EXPECT_UTF8"]), "utf8_mode"


def test_r26i_deliberate_stop():
    pytest.fail("deliberate stop after step 1 (fail-fast bound)")
'''

PROBE_MINI_PASS_SOURCE = '''\
"""R26-I real-child probe: records the actual child flags (nonce-tagged) to
the evidence file BEFORE asserting, then passes."""
import json
import os
import sys


def test_r26i_real_child_flags():
    flags = {
        "isolated": sys.flags.isolated,
        "dont_write_bytecode": sys.flags.dont_write_bytecode,
        "utf8_mode": sys.flags.utf8_mode,
        "nonce": os.environ["RF_PROBE_EVIDENCE_NONCE"],
    }
    with open(os.environ["RF_PROBE_EVIDENCE"], "w", encoding="utf-8") as handle:
        json.dump(flags, handle)
    assert flags["isolated"] == 1, "isolated"
    assert flags["dont_write_bytecode"] == 1, "dont_write_bytecode"
    assert flags["utf8_mode"] == int(os.environ["RF_PROBE_EXPECT_UTF8"]), "utf8_mode"
'''

VALIDATOR_EXIT3_SOURCE = '''\
"""R26-I validator-kind child probe: prints its entry argv[0], then exits 3."""
import sys

if sys.flags.isolated == 1:
    print("MARKER_VALIDATOR_ISOLATED_1", flush=True)
print("MARKER_VALIDATOR_ARGV0", sys.argv[0], flush=True)
sys.exit(3)
'''


@dataclasses.dataclass
class ScratchRun:
    """Attribute container for one scratch run root (no user methods)."""

    root: Path
    repo: Path
    entry: Path
    env: dict
    py_rels: tuple
    node_rels: tuple
    node_files: tuple
    mutated: bool = False
    nonce: str = ""                 # per-run nonce; names the evidence file
    evidence_path: Path = None      # nonce-named: no stale same-name evidence


def build_run_scratch(mini_test_source, *, extra_env=None, validator_body=b"",
                      omit_kind=None, mutate=None):
    """Assemble one fresh <run> scratch tree around a copy of the real entry.

    Everything after ``mkdtemp`` runs inside one try: any failure removes the
    whole self-created subtree (nothing outside ``<run>`` is ever touched);
    if the removal itself fails too, both the build error and the cleanup
    error stay observable (chained raise) instead of a silent residue.
    mutate applies a single textual mutation to the entry COPY (never the
    suite tree) and verifies the anchor count before writing, so a failed
    mutation can never fake a red side.
    """
    run_root = Path(tempfile.mkdtemp(prefix="r26i_child_run_"))
    run = None
    try:
        nonce = os.urandom(6).hex()
        repo_dir = run_root / "repo"
        run = ScratchRun(
            root=run_root,
            repo=repo_dir,
            entry=repo_dir / ENTRY_REL,
            env={},
            py_rels=(),
            node_rels=(),
            node_files=(),
            mutated=False,
            nonce=nonce,
            evidence_path=(
                run_root / "state" / f"probe_child_flags_{nonce}.json"),
        )
        _RUN_REGISTRY.append(run)
        entry = run.entry
        entry.parent.mkdir(parents=True)
        shutil.copyfile(REPO / ENTRY_REL, entry)
        mutated = False
        if mutate is not None:
            if mutate == "drop_I":
                anchor = 'sys.executable, "-I", "-B", "-X",'
                replacement = 'sys.executable, "-B", "-X",'
                expected_count = 2
            elif mutate == "drop_B":
                anchor = 'sys.executable, "-I", "-B", "-X",'
                replacement = 'sys.executable, "-I", "-X",'
                expected_count = 2
            elif mutate == "drop_X":
                anchor = ('"-X",\n                '
                          'f"utf8={sys.flags.utf8_mode}", "-c", ')
                replacement = '"-c", '
                expected_count = 2
            elif mutate == "drop_py_validator":
                anchor = '    "build/validate_import_review.py",\n'
                replacement = ""
                expected_count = 1
            else:  # pragma: no cover - guard against typos
                raise AssertionError(f"unknown mutation: {mutate}")
            text = entry.read_text(encoding="utf-8")
            assert text.count(anchor) == expected_count, (
                mutate, text.count(anchor))
            entry.write_text(text.replace(anchor, replacement),
                             encoding="utf-8")
            mutated = True
        spec = importlib.util.spec_from_file_location("r26i_entry_copy", entry)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        if omit_kind == "py":
            omit = {module.PY_VALIDATORS[0]}
        elif omit_kind == "node-validator":
            omit = {module.NODE_VALIDATORS[0]}
        elif omit_kind == "node-suite":
            omit = {module.NODE_TEST_FILES[0]}
        else:
            omit = set()
        for rel in (*module.PY_VALIDATORS, *module.NODE_VALIDATORS,
                    *module.NODE_TEST_FILES):
            if rel in omit:
                continue
            target = repo_dir / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"")
        if validator_body:
            (repo_dir / module.PY_VALIDATORS[0]).write_bytes(validator_body)
        tests_dir = repo_dir / "build" / "tests"
        (tests_dir / "test_r26i_probe.py").write_text(
            mini_test_source, encoding="utf-8", newline="\n")
        (run_root / "state").mkdir()
        (run_root / "tmp").mkdir()
        env = {key: value for key, value in os.environ.items()
               if not key.startswith(("PYTHON", "RF_", "REVIEWFLOW_"))}
        if sys.platform == "win32":
            for keep in ("SYSTEMROOT", "SYSTEMDRIVE", "COMSPEC", "PATHEXT"):
                if keep in os.environ:
                    env[keep] = os.environ[keep]
        if extra_env:
            # Caller extras merge BEFORE the reserved keys: scratch paths and
            # nonce/evidence targets are always builder-set, so child writes
            # can never be steered outside <run>.
            env.update(extra_env)
        env["COBOOKSHELF_DATA_DIR"] = str(run_root / "state")
        for name in ("TEMP", "TMP", "TMPDIR", "RF_R9_TMP", "RF_R9_TMP_ROOT"):
            env[name] = str(run_root / "tmp")
        if NODE is not None:
            env["RF_NODE"] = NODE
        else:
            # Node unresolved: never let extra_env re-introduce RF_NODE into
            # the child environment (product resolve_node would consume it).
            env.pop("RF_NODE", None)
        env["RF_PROBE_EVIDENCE"] = str(run.evidence_path)
        env["RF_PROBE_EVIDENCE_NONCE"] = nonce
        run.repo = entry.resolve().parents[1]
        run.env = env
        run.py_rels = tuple(module.PY_VALIDATORS)
        run.node_rels = tuple(module.NODE_VALIDATORS)
        run.node_files = tuple(module.NODE_TEST_FILES)
        run.mutated = mutated
        return run
    except BaseException as build_error:
        cleanup_error = None
        try:
            shutil.rmtree(run_root)
        except OSError as rmtree_error:
            cleanup_error = rmtree_error
        if cleanup_error is None and run is not None:
            try:
                _RUN_REGISTRY.remove(run)
            except ValueError:
                pass
        if cleanup_error is not None:
            # Both failures stay observable: the original build error travels
            # as explicit __cause__ and the real cleanup error is inlined
            # (type + errno) in the message. A failed cleanup also KEEPS the
            # registry entry so a later teardown can retry the removal.
            raise AssertionError(
                f"r26i scratch build failed AND its cleanup failed; "
                f"residue left at {run_root}; "
                f"cleanup error: {cleanup_error!r}"
            ) from build_error
        raise


def probe_utf8_mode(flags=(), env=None):
    """Measure an interpreter's actual UTF-8 mode under the given env/flags."""
    proc = subprocess.run(
        [sys.executable, *flags, "-c", "import sys; print(sys.flags.utf8_mode)"],
        cwd=str(REPO),
        env=dict(os.environ) if env is None else dict(env),
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


def entry_argv(run, args, *, utf8_parent):
    """Invoke the copied entry DIRECTLY (F1 fix).

    PY_CHILD_BOOTSTRAP_RULING belongs only to the inner pytest/validator
    argv that build_plan() exports - it pops root/kind/entry from sys.argv
    and must never be used to start the entry itself.
    """
    return [sys.executable, "-I", "-B", "-X", f"utf8={utf8_parent}",
            str(run.entry), *args]


def export_plan(run, *, utf8_parent=0):
    return subprocess.run(
        entry_argv(run, ["--list", "--json"], utf8_parent=utf8_parent),
        cwd=str(run.repo), env=run.env, stdin=subprocess.DEVNULL,
        capture_output=True, text=True, timeout=120,
    )


def execute_plan(run, *, utf8_parent=0):
    logs = run.root / "tmp" / "logs"
    return subprocess.run(
        entry_argv(run, ["--log-dir", str(logs)], utf8_parent=utf8_parent),
        cwd=str(run.repo), env=run.env, stdin=subprocess.DEVNULL,
        capture_output=True, encoding="utf-8", errors="replace", timeout=300,
    )


def read_step_log(run, seq, step_id):
    path = run.root / "tmp" / "logs" / f"{seq:02d}_{step_id}.log"
    assert path.is_file(), path
    return path.read_text(encoding="utf-8", errors="replace")


_RUN_REGISTRY = []


@pytest.fixture()
def scratch_run():
    yield
    # A cleanup failure must keep the test non-PASS (teardown ERROR) with the
    # residue path and the original exception preserved - never a silent green.
    # The failed run STAYS registered (pop only after a successful removal) so
    # a later teardown can retry the deletion.
    while _RUN_REGISTRY:
        run = _RUN_REGISTRY[0]
        try:
            shutil.rmtree(run.root)
        except OSError as cleanup_error:
            raise AssertionError(
                f"r26i scratch cleanup failed; residue left at {run.root}; "
                f"cleanup error: {cleanup_error!r}"
            ) from cleanup_error
        _RUN_REGISTRY.pop(0)


class TestRealChildIsolationFlags:
    @_needs_node
    def test_real_pytest_child_isolated_flags_utf8_mode_0(self, scratch_run):
        run = build_run_scratch(PROBE_MINI_FAIL_SOURCE,
                                extra_env={"RF_PROBE_EXPECT_UTF8": "0"})
        proc = execute_plan(run, utf8_parent=0)
        assert proc.returncode == 1, proc.stdout[-2000:]
        # Evidence is nonce-tagged and written by THIS run's child before any
        # assert, immune to pytest capture and assert short-circuit (F2).
        evidence = json.loads(run.evidence_path.read_text(encoding="utf-8"))
        assert evidence["nonce"] == run.nonce
        assert evidence["isolated"] == 1
        assert evidence["dont_write_bytecode"] == 1
        assert evidence["utf8_mode"] == 0
        assert "1 failed" in proc.stdout  # deliberate stop ran after the probe
        assert not (run.root / "tmp" / "logs"
                    / "02_validate_import_review.log").exists()

    @_needs_node
    def test_real_pytest_child_isolated_flags_utf8_mode_1(self, scratch_run):
        run = build_run_scratch(PROBE_MINI_FAIL_SOURCE,
                                extra_env={"RF_PROBE_EXPECT_UTF8": "1"})
        proc = execute_plan(run, utf8_parent=1)
        assert proc.returncode == 1, proc.stdout[-2000:]
        evidence = json.loads(run.evidence_path.read_text(encoding="utf-8"))
        assert evidence["nonce"] == run.nonce
        assert evidence["isolated"] == 1
        assert evidence["dont_write_bytecode"] == 1
        assert evidence["utf8_mode"] == 1
        assert "1 failed" in proc.stdout
        assert not (run.root / "tmp" / "logs"
                    / "02_validate_import_review.log").exists()


class TestChildUtf8FollowsParentMode:
    @_needs_node
    def test_exported_and_real_child_utf8_follows_parent_one(self, scratch_run):
        run = build_run_scratch(PROBE_MINI_FAIL_SOURCE,
                                extra_env={"RF_PROBE_EXPECT_UTF8": "1"})
        proc = export_plan(run, utf8_parent=1)
        assert proc.returncode == 0, proc.stderr
        pytest_argv = json.loads(proc.stdout)["steps"][0]["argv"]
        assert pytest_argv[3:5] == ["-X", "utf8=1"], pytest_argv
        exec_proc = execute_plan(run, utf8_parent=1)
        assert exec_proc.returncode == 1
        evidence = json.loads(run.evidence_path.read_text(encoding="utf-8"))
        assert evidence["nonce"] == run.nonce
        assert evidence["isolated"] == 1
        assert evidence["utf8_mode"] == 1  # real child measured utf8_mode

    @_needs_node
    def test_exported_and_real_child_utf8_follows_parent_zero_overrides_env(
            self, scratch_run):
        # Parent flag -X utf8=0 must override PYTHONUTF8=1, and the -I child
        # must stay at 0 instead of inheriting the env var.
        run = build_run_scratch(
            PROBE_MINI_FAIL_SOURCE,
            extra_env={"RF_PROBE_EXPECT_UTF8": "0", "PYTHONUTF8": "1"})
        proc = export_plan(run, utf8_parent=0)
        assert proc.returncode == 0, proc.stderr
        pytest_argv = json.loads(proc.stdout)["steps"][0]["argv"]
        assert pytest_argv[3:5] == ["-X", "utf8=0"], pytest_argv
        exec_proc = execute_plan(run, utf8_parent=0)
        assert exec_proc.returncode == 1
        evidence = json.loads(run.evidence_path.read_text(encoding="utf-8"))
        assert evidence["nonce"] == run.nonce
        assert evidence["isolated"] == 1
        assert evidence["utf8_mode"] == 0


class TestPlanFailureCodes:
    @_needs_node
    def test_missing_py_validator_file_exit_2(self, scratch_run):
        run = build_run_scratch(PROBE_MINI_FAIL_SOURCE, omit_kind="py")
        proc = export_plan(run, utf8_parent=0)
        assert proc.returncode == 2, (proc.returncode, proc.stderr[-1500:])
        assert f"plan references a missing file: {run.py_rels[0]}" in proc.stderr

    @_needs_node
    def test_missing_node_validator_file_exit_2(self, scratch_run):
        run = build_run_scratch(PROBE_MINI_FAIL_SOURCE,
                                omit_kind="node-validator")
        proc = export_plan(run, utf8_parent=0)
        assert proc.returncode == 2, (proc.returncode, proc.stderr[-1500:])
        assert f"plan references a missing file: {run.node_rels[0]}" in proc.stderr

    @_needs_node
    def test_missing_node_suite_file_exit_2(self, scratch_run):
        run = build_run_scratch(PROBE_MINI_FAIL_SOURCE, omit_kind="node-suite")
        proc = export_plan(run, utf8_parent=0)
        assert proc.returncode == 2, (proc.returncode, proc.stderr[-1500:])
        assert f"plan references a missing file: {run.node_files[0]}" in proc.stderr

    def test_missing_node_executable_exit_2(self, scratch_run):
        run = build_run_scratch(PROBE_MINI_FAIL_SOURCE)
        run.env["RF_NODE"] = str(run.root / "ghost" / "node.exe")
        proc = export_plan(run, utf8_parent=0)
        assert proc.returncode == 2, (proc.returncode, proc.stderr[-1500:])
        assert "RF_NODE" in proc.stderr

    @_needs_node
    def test_ordinary_pytest_child_failure_exit_1_fail_fast(self, scratch_run):
        run = build_run_scratch(PROBE_MINI_FAIL_SOURCE,
                                extra_env={"RF_PROBE_EXPECT_UTF8": "0"})
        proc = execute_plan(run, utf8_parent=0)
        assert proc.returncode == 1
        assert "FAILED at step 1/8" in proc.stdout
        log = read_step_log(run, 1, "pytest-suite")
        assert "test_r26i_deliberate_stop" in log
        assert "1 failed" in log
        assert not (run.root / "tmp" / "logs"
                    / "02_validate_import_review.log").exists()

    @_needs_node
    def test_validator_child_failure_exit_1_fail_fast(self, scratch_run):
        run = build_run_scratch(PROBE_MINI_PASS_SOURCE,
                                extra_env={"RF_PROBE_EXPECT_UTF8": "0"},
                                validator_body=VALIDATOR_EXIT3_SOURCE)
        proc = execute_plan(run, utf8_parent=0)
        assert proc.returncode == 1
        assert "FAILED at step 2/8" in proc.stdout
        assert "1 passed" in read_step_log(run, 1, "pytest-suite")
        log2 = read_step_log(run, 2, "validate_import_review")
        assert str(run.repo / run.py_rels[0]) in log2  # absolute entry argv[0]
        assert "MARKER_VALIDATOR_ISOLATED_1" in log2
        assert not (run.root / "tmp" / "logs"
                    / "03_validate_screening_rounds.log").exists()


class TestPlanShapeStable:
    @_needs_node
    def test_eight_steps_and_node_argv_unchanged(self, scratch_run):
        run = build_run_scratch(PROBE_MINI_PASS_SOURCE)
        proc = export_plan(run, utf8_parent=0)
        assert proc.returncode == 0, proc.stderr
        payload = json.loads(proc.stdout)
        assert payload["python"] == sys.executable
        assert payload["repo_root"] == str(run.repo)
        assert payload["node"] == NODE
        assert payload["step_count"] == 8
        steps = payload["steps"]
        assert [step["kind"] for step in steps] == (
            ["pytest"]
            + ["validator-py"] * len(run.py_rels)
            + ["validator-node"] * len(run.node_rels)
            + ["node-test"]
        )
        assert [step["id"] for step in steps] == (
            ["pytest-suite"]
            + [Path(rel).stem for rel in run.py_rels]
            + [Path(rel).stem for rel in run.node_rels]
            + ["node-test-suites"]
        )
        for index, rel in enumerate(run.node_rels, start=1 + len(run.py_rels)):
            assert steps[index]["argv"] == [NODE, rel]
        assert steps[-1]["argv"] == [NODE, "--test", *run.node_files]

    @_needs_node
    def test_validator_py_entries_are_absolute_script_kind(self, scratch_run):
        run = build_run_scratch(PROBE_MINI_PASS_SOURCE)
        proc = export_plan(run, utf8_parent=0)
        assert proc.returncode == 0, proc.stderr
        steps = json.loads(proc.stdout)["steps"]
        for index, rel in enumerate(run.py_rels, start=1):
            argv = steps[index]["argv"]
            assert argv[:8] == [
                sys.executable, "-I", "-B", "-X", "utf8=0",
                "-c", PY_CHILD_BOOTSTRAP_RULING, str(run.repo),
            ], (rel, argv)
            assert argv[8] == "script"
            assert argv[9] == str(run.repo / rel)


class TestRunpyRealPytestEntry:
    @_needs_node
    def test_real_pytest_entry_green_run_reports_passed(self, scratch_run):
        run = build_run_scratch(PROBE_MINI_PASS_SOURCE,
                                extra_env={"RF_PROBE_EXPECT_UTF8": "0"},
                                validator_body=VALIDATOR_EXIT3_SOURCE)
        proc = execute_plan(run, utf8_parent=0)
        assert proc.returncode == 1  # stopped by the step-2 stub, node-free
        log = read_step_log(run, 1, "pytest-suite")
        assert "1 passed" in log
        assert "no tests ran" not in log  # pytest really executed the mini file
        evidence = json.loads(run.evidence_path.read_text(encoding="utf-8"))
        assert evidence["nonce"] == run.nonce
        assert evidence["isolated"] == 1
        assert evidence["dont_write_bytecode"] == 1
        assert evidence["utf8_mode"] == 0

    @_needs_node
    def test_real_pytest_entry_red_run_reports_failing_test(self, scratch_run):
        run = build_run_scratch(PROBE_MINI_FAIL_SOURCE,
                                extra_env={"RF_PROBE_EXPECT_UTF8": "0"})
        proc = execute_plan(run, utf8_parent=0)
        assert proc.returncode == 1
        log = read_step_log(run, 1, "pytest-suite")
        assert "test_r26i_deliberate_stop" in log
        assert "failed" in log
        evidence = json.loads(run.evidence_path.read_text(encoding="utf-8"))
        assert evidence["nonce"] == run.nonce
        assert evidence["isolated"] == 1  # flag probe passed before the stop


class TestMutationRedSides:
    @_needs_node
    def test_mutation_delete_I_only_breaks_isolated_flag(self, scratch_run):
        run = build_run_scratch(PROBE_MINI_FAIL_SOURCE,
                                extra_env={"RF_PROBE_EXPECT_UTF8": "1"},
                                mutate="drop_I")
        assert run.mutated
        proc = export_plan(run, utf8_parent=1)
        argv = json.loads(proc.stdout)["steps"][0]["argv"]
        assert "-I" not in argv and argv[1] == "-B", argv
        exec_proc = execute_plan(run, utf8_parent=1)
        assert exec_proc.returncode == 1
        evidence = json.loads(run.evidence_path.read_text(encoding="utf-8"))
        assert evidence["nonce"] == run.nonce
        assert evidence["isolated"] == 0   # red side: -I loss un-isolates child
        assert evidence["dont_write_bytecode"] == 1  # -B untouched: single flip
        assert evidence["utf8_mode"] == 1  # -X utf8=1 still propagates
        assert "failed" in exec_proc.stdout  # flags probe now fails for real

    @_needs_node
    def test_mutation_delete_B_only_breaks_dont_write_bytecode(self, scratch_run):
        run = build_run_scratch(PROBE_MINI_FAIL_SOURCE,
                                extra_env={"RF_PROBE_EXPECT_UTF8": "0"},
                                mutate="drop_B")
        assert run.mutated
        proc = export_plan(run, utf8_parent=0)
        argv = json.loads(proc.stdout)["steps"][0]["argv"]
        assert "-B" not in argv and argv[1] == "-I", argv
        exec_proc = execute_plan(run, utf8_parent=0)
        assert exec_proc.returncode == 1
        evidence = json.loads(run.evidence_path.read_text(encoding="utf-8"))
        assert evidence["nonce"] == run.nonce
        assert evidence["isolated"] == 1   # -I untouched: only -B flipped
        assert evidence["dont_write_bytecode"] == 0  # red side: bytecode loss
        assert evidence["utf8_mode"] == 0
        assert "failed" in exec_proc.stdout

    @_needs_node
    def test_mutation_delete_X_utf8_element_breaks_mode_propagation(
            self, scratch_run):
        run = build_run_scratch(PROBE_MINI_FAIL_SOURCE,
                                extra_env={"RF_PROBE_EXPECT_UTF8": "1"},
                                mutate="drop_X")
        default_mode = probe_utf8_mode(env=run.env)
        assert default_mode in ("0", "1"), default_mode
        # Measure, never assume: the red side must be distinguishable from the
        # interpreter's actual default. With default 0 the parent-1 run (alpha)
        # distinguishes; with default 1 the parent-0 run (beta) does.
        if default_mode != "1":
            parent_mode = 1   # alpha: mutated child falls back to default 0
            expected_child_mode = 0
        else:
            parent_mode = 0   # beta: mutated child falls back to default 1
            expected_child_mode = 1
            run.env["RF_PROBE_EXPECT_UTF8"] = "0"
        proc = export_plan(run, utf8_parent=parent_mode)
        argv = json.loads(proc.stdout)["steps"][0]["argv"]
        assert "-X" not in argv, argv
        exec_proc = execute_plan(run, utf8_parent=parent_mode)
        assert exec_proc.returncode == 1
        evidence = json.loads(run.evidence_path.read_text(encoding="utf-8"))
        assert evidence["nonce"] == run.nonce
        assert evidence["isolated"] == 1   # m3 touches only the -X element
        assert evidence["dont_write_bytecode"] == 1
        assert evidence["utf8_mode"] == expected_child_mode
        assert evidence["utf8_mode"] != parent_mode  # propagation broken for real
        assert "failed" in exec_proc.stdout

    @_needs_node
    def test_mutation_delete_py_validator_entry_breaks_step_count(
            self, scratch_run):
        run = build_run_scratch(PROBE_MINI_PASS_SOURCE,
                                mutate="drop_py_validator")
        assert run.mutated
        proc = export_plan(run, utf8_parent=0)
        assert proc.returncode == 0, proc.stderr
        payload = json.loads(proc.stdout)
        kinds = [step["kind"] for step in payload["steps"]]
        # The red side of the eight-step pin genuinely fires under this
        # mutation (shape differs from TestPlanShapeStable's exact sequence).
        assert payload["step_count"] == 7
        assert kinds == (
            ["pytest"] + ["validator-py"] * 2
            + ["validator-node"] * 3 + ["node-test"]
        )
        assert kinds != (
            ["pytest"] + ["validator-py"] * 3
            + ["validator-node"] * 3 + ["node-test"]
        )
