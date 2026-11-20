"""TST-03 (2.1.6 Task 8): the single validation entry local builds and CI share.

Why this file exists: before 2.1.6 the release gate was one hand-copied command
list in release.yml plus a parallel (and drifting) list in each packaging
script.  Two validators (build/validate_team_prisma.py and
build/validate_evidence_locale_static.cjs) were green but wired into neither,
so a local "all green" build could still ship a payload CI never fully
validated - the 4-vs-6 contradiction the 2.1.6 review flagged as B2.

Contract (docs/superpowers/plans/2026-11-14-2.1.6-maintenance.md Task 8):
- build_plan() is the single source of truth. The default mode executes it and
  ``--list``/``--json`` serialize the very same objects, so the plan a static
  nail reads IS the plan that runs - a stale comment cannot fake coverage.
- Every step runs as an argv array (no shell), Python children always through
  sys.executable, Node through RF_NODE (or PATH), resolved to an executable
  file before the plan runs - a missing or non-executable node is exit 2,
  never a silent skip, and no machine-specific fallback path is baked in.
- The pytest step runs the WHOLE build/tests directory in one process. The
  2.1.5-era five-file --ignore segmentation existed because of module-level env
  leakage; the 2.1.6 conftest (Task 2) restores per-module isolation, and the
  segmented bypasses were removed with that evidence. Hidden re-segmentation or
  new skips here are a gate regression.
- Python children are isolated explicitly, not by parent flag inheritance:
  each runs as ``[sys.executable, -I, -B, -X utf8=<parent mode>, -c,
  PY_CHILD_BOOTSTRAP, str(REPO), kind, entry, ...]`` with only a fixed
  module/script entry from this plan.  ``-I`` ignores PYTHON* startup
  variables (including PYTHONPATH, PYTHONUTF8 and PYTHONIOENCODING), the
  cwd/script directory and the user site; the parent's actual UTF-8 mode is
  propagated explicitly via ``-X utf8=0|1`` instead of inheriting the
  environment.  PYTHON* values may still be visible in ``os.environ`` but do
  not configure the child interpreter.  Other environment values
  (RF_R9_TMP*, COBOOKSHELF_*, DYLD_LIBRARY_PATH, ...) are still passed
  through unchanged and remain readable by child code.  Child interpreter
  flags are not inherited by grandchildren, and the bootstrap accepts no
  client-controlled script path.
- Environment is otherwise passed through unchanged: callers (release.yml,
  packaging scripts) own RF_R9_TMP / RF_R9_TMP_ROOT / DYLD_LIBRARY_PATH.
  This entry never installs anything - dependency provisioning stays with the
  caller (build/requirements-test.txt is the single dependency source).

Usage:
    python build/run_validation.py                  # run the full plan, fail-fast
    python build/run_validation.py --list           # read-only human-readable table
    python build/run_validation.py --list --json    # read-only JSON (test consumer)
    python build/run_validation.py --log-dir DIR    # per-step logs (default: temp dir)

Exit codes: 0 all steps green; 1 a step failed (fail-fast, log paths printed);
2 plan/environment error (missing/invalid node, missing plan file, unlaunchable
step, unusable log directory, bad usage).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# The 24 node --test suites of the release gate (release.yml:175-198 before
# Task 8; moved here verbatim - the entry is now the only place that lists
# them, the workflow calls this file instead).
NODE_TEST_FILES = (
    "build/tests/import_review_model.test.cjs",
    "build/tests/import_review_updates.test.cjs",
    "build/tests/ml_batch.test.cjs",
    "build/tests/study_design_badge.test.cjs",
    "build/tests/pdf_fetch_ui.test.cjs",
    "build/tests/living_review.test.cjs",
    "build/tests/auth_remember_reset.test.cjs",
    "build/tests/ai_providers_wizard.test.cjs",
    "build/tests/ai_import_bulk.test.cjs",
    "build/tests/aiux_locale.test.cjs",
    "build/tests/analysis_grade_ui.test.cjs",
    "build/tests/analysis_rob_locale.test.cjs",
    "build/tests/import_review_review67.test.cjs",
    "build/tests/report_evidence_ui.test.cjs",
    "build/tests/ai_settings_drafts.test.cjs",
    "build/tests/analysis_rob_editor_ui.test.cjs",
    "build/tests/table_wizards_polish.test.cjs",
    "build/tests/workflow_evidence_save_ui.test.cjs",
    "build/tests/workflow_ui_polish.test.cjs",
    "build/tests/import_ai_ui_polish.test.cjs",
    "build/tests/app_module_loader.test.cjs",
    "build/tests/ui_polish_core.test.cjs",
    "build/tests/ui_polish_recovery.test.cjs",
    "build/tests/screen_reader_e10.test.cjs",
)

PY_VALIDATORS = (
    "build/validate_import_review.py",
    "build/validate_screening_rounds.py",
    "build/validate_team_prisma.py",
)

NODE_VALIDATORS = (
    "build/validate_import_review_static.cjs",
    "build/validate_team_prisma_static.js",
    "build/validate_evidence_locale_static.cjs",
)

# R26-I: fixed bootstrap for every Python child.  Only the trusted entry REPO
# and one of the two plan-fixed kinds/entries (pytest module or a validator
# script from PY_VALIDATORS) travel through it; no client-controlled script
# path is accepted.  The parent UTF-8 mode is passed separately via -X.
PY_CHILD_BOOTSTRAP = (
    'import runpy,sys;'
    'root=sys.argv.pop(1);kind=sys.argv.pop(1);entry=sys.argv.pop(1);'
    'sys.path.insert(0,root);sys.argv[0]=entry;'
    "runpy.run_module(entry,run_name='__main__') if kind=='module' "
    "else runpy.run_path(entry,run_name='__main__')"
)


class PlanError(Exception):
    """The plan itself is broken (missing file / missing node) - exit 2."""


def resolve_node() -> str:
    """Resolve the node executable before the plan is built (F2, 2.1.6 R1).

    A non-blank RF_NODE is resolved through the same stdlib lookup as a PATH
    command name (shutil.which), so an explicit absolute/relative path -
    including a path with spaces - must point at an executable file. A missing
    or non-executable override is a PlanError (exit 2 before the plan runs),
    never a silent pass-through that detonates at launch time, and never a
    silent PATH fallback; a directory is rejected explicitly. An unset or
    blank RF_NODE keeps the original PATH behaviour. No version probe, no
    machine-specific fallback, no shell: the result travels as one argv
    element.
    """
    requested = (os.environ.get("RF_NODE") or "").strip()
    if requested:
        if Path(requested).is_dir():
            raise PlanError(
                "RF_NODE points at a directory, not a node executable: "
                f"{requested} (fix RF_NODE; there is no fallback)"
            )
        resolved = shutil.which(requested)
        if not resolved:
            raise PlanError(
                f"RF_NODE={requested!r} is not an executable node candidate "
                "(missing file or not executable); fix RF_NODE or unset it "
                "to use the node on PATH (no fallback is assumed)"
            )
        return os.path.abspath(resolved)
    node = shutil.which("node")
    if not node:
        raise PlanError(
            "node is required for the Node validators and the node --test "
            "suites: set RF_NODE or put node on PATH (no fallback is assumed)"
        )
    return node


def build_plan() -> list[dict]:
    """The single source of truth for the validation command plan.

    Both the executor and the --list/--json export consume this list; nothing
    else may carry command spellings for the gate.
    """
    node = resolve_node()
    steps: list[dict] = [
        {
            "id": "pytest-suite",
            "kind": "pytest",
            "argv": [
                sys.executable, "-I", "-B", "-X",
                f"utf8={sys.flags.utf8_mode}", "-c", PY_CHILD_BOOTSTRAP,
                str(REPO), "module", "pytest", "build/tests", "-q",
            ],
            "cwd": str(REPO),
            "description": "whole-directory pytest gate (1,222+ assertions; "
                           "single process, no --ignore segmentation)",
        },
    ]
    for rel in PY_VALIDATORS:
        steps.append({
            "id": Path(rel).stem,
            "kind": "validator-py",
            "argv": [
                sys.executable, "-I", "-B", "-X",
                f"utf8={sys.flags.utf8_mode}", "-c", PY_CHILD_BOOTSTRAP,
                str(REPO), "script", str(REPO / rel),
            ],
            "cwd": str(REPO),
            "description": f"Python static validator {rel}",
        })
    for rel in NODE_VALIDATORS:
        steps.append({
            "id": Path(rel).stem,
            "kind": "validator-node",
            "argv": [node, rel],
            "cwd": str(REPO),
            "description": f"Node static validator {rel}",
        })
    steps.append({
        "id": "node-test-suites",
        "kind": "node-test",
        "argv": [node, "--test", *NODE_TEST_FILES],
        "cwd": str(REPO),
        "description": f"the {len(NODE_TEST_FILES)} node --test behaviour suites",
    })
    for rel in (*PY_VALIDATORS, *NODE_VALIDATORS, *NODE_TEST_FILES):
        if not (REPO / rel).is_file():
            raise PlanError(f"plan references a missing file: {rel}")
    if not (REPO / "build" / "tests").is_dir():
        raise PlanError("build/tests directory is missing - run from a full checkout")
    return steps


def export_json(steps: list[dict]) -> str:
    payload = {
        "repo_root": str(REPO),
        "python": sys.executable,
        "node": resolve_node(),
        "step_count": len(steps),
        "steps": [
            {
                "seq": index + 1,
                "id": step["id"],
                "kind": step["kind"],
                "argv": step["argv"],
                "cwd": step["cwd"],
                "description": step["description"],
            }
            for index, step in enumerate(steps)
        ],
    }
    return json.dumps(payload, indent=2)


def export_table(steps: list[dict]) -> str:
    lines = [f"validation plan ({len(steps)} steps, cwd={REPO})"]
    for index, step in enumerate(steps):
        lines.append(f"{index + 1:>2}. [{step['kind']}] {step['id']}")
        lines.append(f"      argv: {step['argv']}")
    return "\n".join(lines)


def run_plan(steps: list[dict], log_dir: Path) -> int:
    for index, step in enumerate(steps):
        seq = index + 1
        log_path = log_dir / f"{seq:02d}_{step['id']}.log"
        started = time.monotonic()
        print(f"[{seq}/{len(steps)}] {step['id']}: {step['argv']}", flush=True)
        try:
            log = log_path.open("wb")
        except OSError as error:
            # The log file itself is unusable (parent missing, permissions,
            # ...): surface the environment error without writing to the file
            # that failed to open - that would mask the original OSError.
            raise PlanError(
                f"cannot open log file for step {seq}/{len(steps)} "
                f"({step['id']}): {error}"
            ) from error
        with log:
            try:
                proc = subprocess.Popen(
                    step["argv"],
                    cwd=step["cwd"],
                    env=os.environ,  # pure passthrough - RF_R9_TMP*/PYTHONUTF8/DYLD_* stay
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                )
            except OSError as error:
                # Launch-time environment error (file deleted between plan and
                # launch, permissions, bad executable format, ...): record it
                # in the step log and raise the contract's environment error,
                # never a bare traceback.
                message = (
                    f"cannot launch step {seq}/{len(steps)} ({step['id']}): "
                    f"{error}"
                )
                log.write(message.encode("utf-8", errors="replace"))
                raise PlanError(message) from error
            assert proc.stdout is not None
            for raw in proc.stdout:
                log.write(raw)
                log.flush()
                try:
                    print(raw.decode("utf-8", errors="replace"), end="")
                except UnicodeEncodeError:
                    sys.stdout.buffer.write(raw)
                    sys.stdout.flush()
            code = proc.wait()
        elapsed = time.monotonic() - started
        print(f"[{seq}/{len(steps)}] {step['id']}: exit {code} "
              f"({elapsed:.1f}s, log: {log_path})", flush=True)
        if code != 0:
            print(f"FAILED at step {seq}/{len(steps)} ({step['id']}) with exit "
                  f"{code} - stopping (fail-fast). Log: {log_path}", flush=True)
            return 1
    print(f"All {len(steps)} validation steps passed. Logs: {log_dir}", flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run (or list) the complete ReviewFlow validation plan.")
    parser.add_argument("--list", action="store_true",
                        help="read-only: print the plan instead of running it")
    parser.add_argument("--json", action="store_true",
                        help="with --list: emit JSON (test consumer)")
    parser.add_argument("--log-dir", type=Path, default=None,
                        help="directory for per-step logs "
                             "(default: a fresh temp directory)")
    args = parser.parse_args(argv)
    try:
        steps = build_plan()
    except PlanError as error:
        print(f"plan error: {error}", file=sys.stderr)
        return 2
    if args.list:
        print(export_json(steps) if args.json else export_table(steps))
        return 0
    if args.json:
        print("note: --json only affects --list; running the plan", file=sys.stderr)
    if args.log_dir is not None:
        log_dir = args.log_dir
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            print(
                f"environment error: cannot create log directory {log_dir}: "
                f"{error}",
                file=sys.stderr,
            )
            return 2
    else:
        try:
            log_dir = Path(tempfile.mkdtemp(prefix="rf-validation-"))
        except OSError as error:
            print(
                "environment error: cannot create a temporary log directory: "
                f"{error}",
                file=sys.stderr,
            )
            return 2
    print(f"validation run: {len(steps)} steps, logs in {log_dir}", flush=True)
    try:
        return run_plan(steps, log_dir)
    except PlanError as error:
        # Launch-phase environment errors (unlaunchable step, unusable log
        # file) surface as the contract's exit 2, never a bare traceback;
        # ordinary subcommand failures keep run_plan's exit 1 fail-fast.
        print(f"environment error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
