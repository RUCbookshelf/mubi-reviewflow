"""Task8 fix R1 (2026-11-15): regression pins for the two P2 repairs.

F1 - .github/workflows/release.yml: BOTH upload-artifact@v6 steps (the build
job's per-platform upload and the assemble job's bundle upload) must
explicitly set ``with: overwrite: true``. A fixed artifact name alone does
NOT enable same-run rerun coverage (Sol review F1/P2): the pre-fix workflow
is red on this file.

F2 - build/run_validation.py: the node executable used by the plan must be
resolved as an actual executable file before the plan is built - a non-blank
RF_NODE that is missing/a directory/not executable is a PlanError (exit 2),
never a silent pass-through that detonates later, and never a silent PATH
fallback. Launch-time OSError (step argv cannot spawn, log file cannot open)
surfaces as the contract's environment error (exit 2 via PlanError), not a
bare traceback (Sol review F2/P2). Ordinary subcommand failures keep exit 1
and fail-fast.

Scope discipline:
- The real workflow YAML is parsed (never a copied fixture); the real entry
  is exercised only through its documented CLI and, via short driver
  subprocesses that import the module by file path, through the documented
  run_plan/main call shapes with same-type SINGLE synthetic steps.
- The full 8-step plan is never executed here and the pre-existing 29-test
  matrix is not duplicated.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
ENTRY = REPO / "build" / "run_validation.py"
WORKFLOW = REPO / ".github" / "workflows" / "release.yml"

# The subprocess interpreter is the interpreter running the tests; in the
# isolated validation box that is the pinned Python 3.12 venv, matching the
# interpreter CI provisions (release.yml python-version "3.12").
PY = sys.executable

# R26-I (Terra argv ruling): the fixed child bootstrap, byte-equal to the
# product PY_CHILD_BOOTSTRAP constant; any drift is red by design. The three
# Python validator rels are likewise pinned here on purpose (plan drift red).
PY_CHILD_BOOTSTRAP_RULING = (
    'import runpy,sys;'
    'root=sys.argv.pop(1);kind=sys.argv.pop(1);entry=sys.argv.pop(1);'
    'sys.path.insert(0,root);sys.argv[0]=entry;'
    "runpy.run_module(entry,run_name='__main__') if kind=='module' "
    "else runpy.run_path(entry,run_name='__main__')"
)
PY_VALIDATOR_RELS = (
    "build/validate_import_review.py",
    "build/validate_screening_rounds.py",
    "build/validate_team_prisma.py",
)


def _run_entry(args: list[str], env_overrides: dict[str, str] | None = None):
    """Run the real entry as a subprocess and return the completed process.

    env_overrides replace (not merge) the named variables on top of the
    inherited environment, so PATH scrubbing and RF_NODE pinning are exact.
    """
    env = dict(os.environ)
    env.update(env_overrides or {})
    return subprocess.run(
        [PY, "-I", "-B", str(ENTRY), *args],
        cwd=str(REPO),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=120,
    )


def _upload_steps(doc: dict) -> list[tuple[str, dict]]:
    steps: list[tuple[str, dict]] = []
    for job_name, job in doc["jobs"].items():
        for step in job.get("steps", []):
            if str(step.get("uses", "")).startswith("actions/upload-artifact"):
                steps.append((job_name, step))
    return steps


# ---------------------------------------------------------------------------
# Probe driver: one short-lived script per call, mode-dispatched. Each mode
# exercises one contract edge against the REAL module (imported by file path)
# and prints a MARKER + exits 0 only when the expected outcome happens.
# Expected failures are caught by their specific type (module.PlanError)
# only; anything else propagates as a driver crash so the test goes red.
# The driver also proves which source file was loaded (ENTRY_FILE).
# ---------------------------------------------------------------------------

DRIVER_SOURCE = r'''
"""Probe driver for test_2_1_6_task8_repair.py (same-type synthetic steps)."""
import contextlib
import importlib.util
import io
import sys
import tempfile
from pathlib import Path

entry = Path(sys.argv[1]).resolve()
mode = sys.argv[2]

spec = importlib.util.spec_from_file_location("run_validation_under_test", entry)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
assert Path(module.__file__).resolve() == entry, module.__file__
print("ENTRY_FILE:", module.__file__)
repo = entry.parents[1]

if mode == "launch-error":
    # Real run_plan + single synthetic step whose argv[0] cannot spawn:
    # the launch-time OSError must surface as PlanError, with the error
    # written into that step's log file.
    log_dir = Path(tempfile.mkdtemp(prefix="drv-launch-"))
    step = {
        "id": "boom-launch",
        "kind": "synthetic",
        "argv": [sys.argv[3], "unused-arg"],
        "cwd": str(repo),
        "description": "synthetic single step whose argv[0] cannot launch",
    }
    try:
        module.run_plan([step], log_dir)
    except module.PlanError as error:
        message = str(error)
        assert "boom-launch" in message, message
        log_file = log_dir / "01_boom-launch.log"
        assert log_file.is_file(), log_file
        logged = log_file.read_text(encoding="utf-8", errors="replace")
        assert "cannot launch step" in logged, logged
        print("MARKER_PLANERROR_LAUNCH_OK")
        sys.exit(0)
    print("MARKER_PLANERROR_LAUNCH_MISSING")
    sys.exit(3)

if mode == "log-open-error":
    # Real run_plan + single synthetic step whose log file cannot be opened
    # (log dir sits below an existing FILE): PlanError, and the unopenable
    # file is never written (no masking of the original OSError).
    blocker = Path(sys.argv[3])
    assert blocker.is_file(), blocker
    log_dir = blocker / "logs-under-file"
    step = {
        "id": "boom-logopen",
        "kind": "synthetic",
        "argv": [sys.executable, "-c", "pass"],
        "cwd": str(repo),
        "description": "synthetic single step with an unopenable log file",
    }
    try:
        module.run_plan([step], log_dir)
    except module.PlanError as error:
        message = str(error)
        assert "boom-logopen" in message, message
        assert "log" in message.lower(), message
        assert not log_dir.exists(), log_dir
        print("MARKER_PLANERROR_LOGOPEN_OK")
        sys.exit(0)
    print("MARKER_PLANERROR_LOGOPEN_MISSING")
    sys.exit(3)

if mode == "main-exit2":
    # Real main() with a same-type single-step plan injected at the documented
    # build_plan seam: the run-phase PlanError must map to exit 2 with a clear
    # stderr environment error and no traceback.
    log_dir = Path(tempfile.mkdtemp(prefix="drv-main-"))
    step = {
        "id": "boom-launch",
        "kind": "synthetic",
        "argv": [sys.argv[3], "unused-arg"],
        "cwd": str(repo),
        "description": "synthetic single plan whose step cannot launch",
    }
    module.build_plan = lambda: [step]
    stderr = io.StringIO()
    with contextlib.redirect_stderr(stderr):
        rc = module.main(["--log-dir", str(log_dir)])
    text = stderr.getvalue()
    assert rc == 2, (rc, text)
    assert "environment error" in text, text
    assert "boom-launch" in text, text
    assert "Traceback" not in text, text
    print("MARKER_MAIN_EXIT2_OK")
    sys.exit(0)

if mode == "exit3-failfast":
    # Real run_plan + two synthetic steps: the first is an ordinary subcommand
    # exiting 3, the second would leave a marker file if it ran. run_plan must
    # return 1 (fail-fast), the sentinel must not run, the first log must
    # exist, and no second log may be created.
    log_dir = Path(tempfile.mkdtemp(prefix="drv-exit3-"))
    sentinel = Path(sys.argv[3])
    steps = [
        {
            "id": "boom-exit3",
            "kind": "synthetic",
            "argv": [sys.executable, "-c", "import sys; sys.exit(3)"],
            "cwd": str(repo),
            "description": "ordinary subcommand failing with exit 3",
        },
        {
            "id": "sentinel",
            "kind": "synthetic",
            "argv": [sys.executable, "-c",
                     "import sys; open(sys.argv[1], 'w').write('ran')",
                     str(sentinel)],
            "cwd": str(repo),
            "description": "sentinel that must never run after fail-fast",
        },
    ]
    rc = module.run_plan(steps, log_dir)
    assert rc == 1, rc
    assert not sentinel.exists(), "sentinel step ran despite fail-fast"
    assert (log_dir / "01_boom-exit3.log").is_file()
    assert not (log_dir / "02_sentinel.log").exists()
    print("MARKER_EXIT3_FAILFAST_OK")
    sys.exit(0)

print("MARKER_UNKNOWN_MODE:", mode)
sys.exit(4)
'''


def _run_driver(mode: str, tmp_path: Path, extra_args: list[str] | None = None):
    driver = tmp_path / "drv_task8_repair_probe.py"
    driver.write_text(DRIVER_SOURCE, encoding="utf-8", newline="\n")
    return subprocess.run(
        [PY, "-I", "-B", str(driver), str(ENTRY), mode, *(extra_args or [])],
        cwd=str(REPO),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=120,
    )


class TestF1UploadOverwrite:
    """F1: both upload-artifact@v6 with blocks set overwrite: true (real YAML)."""

    def test_both_uploads_declare_overwrite_true(self):
        doc = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
        uploads = _upload_steps(doc)
        names = sorted(step["with"]["name"] for _, step in uploads)
        assert len(uploads) == 2, (
            f"expected exactly two upload-artifact steps (build platform "
            f"upload + assemble bundle upload), got {names}"
        )
        assert names == [
            "desktop-release-assets",
            "reviewflow-${{ matrix.platform }}",
        ], names
        for job_name, step in uploads:
            assert step["uses"] == "actions/upload-artifact@v6"
            assert step["with"].get("overwrite") is True, (
                f"{job_name} upload must set with.overwrite: true - a fixed "
                "artifact name alone does not cover same-run reruns"
            )
            assert step["with"]["if-no-files-found"] == "error"


class TestF2NodeResolution:
    """F2: RF_NODE / PATH node resolution verifies executability up front."""

    def test_invalid_rf_node_exits_2_with_clear_error(self, tmp_path):
        missing = tmp_path / "no-such-dir" / "node-ghost.exe"
        proc = _run_entry(["--list", "--json"], {"RF_NODE": str(missing)})
        assert proc.returncode == 2, (
            f"invalid RF_NODE must exit 2 before the plan runs; "
            f"got exit {proc.returncode}: {proc.stdout!r} {proc.stderr!r}"
        )
        err = proc.stderr.decode("utf-8", errors="replace")
        assert "RF_NODE" in err, err
        assert "Traceback" not in err, err

    def test_valid_absolute_path_with_spaces_resolves(self, tmp_path):
        spaced = tmp_path / "dir with space"
        spaced.mkdir()
        # Windows os.access(X_OK) is the CRT check: an explicit path must end
        # in an executable extension (.exe/.cmd/...) to be spawnable without
        # a shell - exactly what shutil.which enforces. On POSIX the exec bit
        # governs instead.
        fake_node = spaced / "fake node.exe"
        fake_node.write_bytes(b"")
        if os.name != "nt":
            fake_node.chmod(fake_node.stat().st_mode | stat.S_IEXEC)
        proc = _run_entry(["--list", "--json"], {"RF_NODE": str(fake_node)})
        assert proc.returncode == 0, proc.stderr.decode("utf-8", errors="replace")
        payload = json.loads(proc.stdout.decode("utf-8"))
        assert payload["node"] == str(fake_node), payload["node"]
        assert payload["step_count"] == 8
        node_argv0 = {
            step["argv"][0]
            for step in payload["steps"]
            if step["kind"] in ("validator-node", "node-test")
        }
        # The spaced path travels as ONE argv element (list spawning, no shell).
        assert node_argv0 == {str(fake_node)}, node_argv0

    def test_blank_rf_node_keeps_path_behaviour_exit_2_without_node(self, tmp_path):
        empty_path = tmp_path / "pathless"
        empty_path.mkdir()
        env = {
            "RF_NODE": "",
            "PATH": str(empty_path),
        }
        for keep in ("SYSTEMROOT", "SYSTEMDRIVE", "COMSPEC", "PATHEXT"):
            if keep in os.environ:
                env[keep] = os.environ[keep]
        proc = _run_entry(["--list", "--json"], env)
        assert proc.returncode == 2, (
            f"blank RF_NODE with no node on PATH must keep the original "
            f"exit-2 behaviour; got {proc.returncode}: "
            f"{proc.stdout!r} {proc.stderr!r}"
        )
        err = proc.stderr.decode("utf-8", errors="replace")
        assert "node is required" in err, err
        assert "Traceback" not in err, err


class TestF2LaunchErrors:
    """F2: launch-time OSError is the contract's environment error (exit 2)."""

    def test_launch_oserror_is_planerror_with_log_written(self, tmp_path):
        ghost = tmp_path / "cannot-launch-ghost.exe"
        proc = _run_driver("launch-error", tmp_path, [str(ghost)])
        out = proc.stdout.decode("utf-8", errors="replace")
        assert proc.returncode == 0, (
            f"driver exit {proc.returncode}\nSTDOUT:{out}\n"
            f"STDERR:{proc.stderr.decode('utf-8', errors='replace')}"
        )
        assert "MARKER_PLANERROR_LAUNCH_OK" in out, out
        assert str(ENTRY) in out, out  # the box/repo copy was loaded, not another

    def test_log_open_failure_is_planerror_without_masking(self, tmp_path):
        blocker = tmp_path / "blocker.txt"
        blocker.write_text("not a directory", encoding="utf-8")
        proc = _run_driver("log-open-error", tmp_path, [str(blocker)])
        out = proc.stdout.decode("utf-8", errors="replace")
        assert proc.returncode == 0, (
            f"driver exit {proc.returncode}\nSTDOUT:{out}\n"
            f"STDERR:{proc.stderr.decode('utf-8', errors='replace')}"
        )
        assert "MARKER_PLANERROR_LOGOPEN_OK" in out, out

    def test_main_maps_run_phase_planerror_to_exit_2(self, tmp_path):
        ghost = tmp_path / "cannot-launch-ghost.exe"
        proc = _run_driver("main-exit2", tmp_path, [str(ghost)])
        out = proc.stdout.decode("utf-8", errors="replace")
        assert proc.returncode == 0, (
            f"driver exit {proc.returncode}\nSTDOUT:{out}\n"
            f"STDERR:{proc.stderr.decode('utf-8', errors='replace')}"
        )
        assert "MARKER_MAIN_EXIT2_OK" in out, out

    def test_cli_bad_log_dir_exits_2_cleanly(self, tmp_path):
        blocker = tmp_path / "blocker.txt"
        blocker.write_text("not a directory", encoding="utf-8")
        proc = _run_entry(["--log-dir", str(blocker / "logs")])
        err = proc.stderr.decode("utf-8", errors="replace")
        assert proc.returncode == 2, (
            f"unusable log directory must exit 2 cleanly; got "
            f"{proc.returncode}: {err}"
        )
        assert "environment error" in err, err
        assert "Traceback" not in err, err


class TestF2FailFastUnchanged:
    def test_subcommand_exit3_keeps_exit1_failfast(self, tmp_path):
        sentinel = tmp_path / "sentinel-ran.marker"
        proc = _run_driver("exit3-failfast", tmp_path, [str(sentinel)])
        out = proc.stdout.decode("utf-8", errors="replace")
        assert proc.returncode == 0, (
            f"driver exit {proc.returncode}\nSTDOUT:{out}\n"
            f"STDERR:{proc.stderr.decode('utf-8', errors='replace')}"
        )
        assert "MARKER_EXIT3_FAILFAST_OK" in out, out


class TestPlanStability:
    def test_valid_plan_export_unchanged(self):
        """Valid environment: the exported 8-step plan keeps its contract."""
        proc = _run_entry(["--list", "--json"])
        assert proc.returncode == 0, proc.stderr.decode("utf-8", errors="replace")
        payload = json.loads(proc.stdout.decode("utf-8"))
        assert payload["step_count"] == 8
        kinds = [step["kind"] for step in payload["steps"]]
        assert kinds == (
            ["pytest"]
            + ["validator-py"] * 3
            + ["validator-node"] * 3
            + ["node-test"]
        ), kinds
        # R26-I (Terra argv ruling): the pytest step and the three Python
        # validator steps run through the isolated child (python -I -B
        # -X utf8=<entry parent actual mode> -c <fixed bootstrap> ...).
        # _run_entry spawns the entry with -I -B, so the entry parent's own
        # UTF-8 mode is whatever a same-flags interpreter reports; the
        # expected value is measured, never hard-coded (locale / interpreter
        # defaults must not be assumed).
        utf8_probe = subprocess.run(
            [PY, "-I", "-B", "-c", "import sys; print(sys.flags.utf8_mode)"],
            cwd=str(REPO), capture_output=True, text=True, timeout=120,
        )
        assert utf8_probe.returncode == 0, utf8_probe.stderr
        child_head = [
            sys.executable, "-I", "-B", "-X", f"utf8={utf8_probe.stdout.strip()}",
            "-c", PY_CHILD_BOOTSTRAP_RULING, str(REPO),
        ]
        pytest_step = payload["steps"][0]
        assert pytest_step["argv"] == child_head + ["module", "pytest",
                                                    "build/tests", "-q"]
        for index, rel in enumerate(PY_VALIDATOR_RELS, start=1):
            assert payload["steps"][index]["argv"] == child_head + [
                "script", str(REPO / rel)
            ], (index, rel)
        node = payload["node"]
        assert node == shutil.which("node"), (node, shutil.which("node"))
        node_step = payload["steps"][-1]
        assert node_step["argv"][0] == node
        assert node_step["argv"][1] == "--test"
        assert len(node_step["argv"]) - 2 == 24
