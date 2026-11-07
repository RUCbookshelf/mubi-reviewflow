"""R7 dayfix nails (MM7-04): the dead duplicate import panel is fully removed.

The retired frontend module (name assembled below by concatenation so this nail
file itself never greps positive) defined a second window.WorkflowImports and
re-created the #wf-import-review host, fighting the live module
(unified_import_workspace.js) that index.html actually loads; the real breakage
point would have been import_export_workspace.js (exportCurrent/listIfEmpty
missing on the dead module's API). The file is deleted outright and every
reference is cleaned (docs, static gates, browser harnesses, validators), so
these nails assert:
  * the file no longer exists;
  * a whole-tree content grep finds ZERO mentions of its name;
  * the online load list parsed from custom_frontend/index.html never contained
    it (static manifest check, same parsing rules as build/script_manifest.cjs).
Pre-fix the first two are red. The manifest check was green even before the
deletion (index.html never loaded the module) and must stay green.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DEAD_BASENAME = "workflow_" + "imports"
DEAD_FILENAME = DEAD_BASENAME + ".js"
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".pytest_cache", "out",
             ".zcode", ".v2c", ".video_agent", "dist", "staging", "work-linux",
             "work-macos"}
MAX_FILE_BYTES = 2_000_000


def _repo_text_files() -> list[Path]:
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(REPO):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            path = Path(dirpath) / name
            try:
                if path.stat().st_size > MAX_FILE_BYTES:
                    continue
                files.append(path)
            except OSError:
                continue
    return files


def test_mm7_04_dead_module_file_is_deleted():
    assert not (REPO / "custom_frontend" / DEAD_FILENAME).exists(), (
        "the dead duplicate panel must be removed from the frontend payload"
    )


def test_mm7_04_zero_references_across_the_whole_tree():
    hits = []
    for path in _repo_text_files():
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if DEAD_BASENAME in text:
            hits.append(str(path.relative_to(REPO)).replace("\\", "/"))
    assert hits == [], f"stale references to the deleted module: {hits}"


def test_mm7_04_online_load_list_excludes_the_dead_module():
    html = (REPO / "custom_frontend" / "index.html").read_text(encoding="utf-8")
    sources = re.findall(r"<script\b[^>]*\bsrc\s*=\s*[\"']([^\"']+)[\"']", html)
    assert sources, "index.html must load frontend modules via <script src>"
    offenders = [src for src in sources if DEAD_BASENAME in src]
    assert offenders == [], f"dead module wired into the online load list: {offenders}"
    assert any(src.split("?")[0].endswith("unified_import_workspace.js") for src in sources), (
        "the live import-review workspace module must stay loaded"
    )
