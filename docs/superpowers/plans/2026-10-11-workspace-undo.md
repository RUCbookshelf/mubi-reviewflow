# ReviewFlow workspace Undo implementation plan

**Approved behavior:** Option A. Restore the state before the last successful save and reopen its article in initial screening, secondary screening, or coding. Preserve article files, annotations, and unrelated stages. Keep version 2.0.4.

**Architecture:** Store before/after snapshots in an additive SQLite undo journal alongside each reviewer's data. Capture snapshots in the same transaction as the saved operation. Check the current state before restoring, and identify the specific journal entry in requests so retrying Undo cannot reverse another operation. A coding form Save is one atomic operation across all submitted dimensions.

## Scope and implementation

- [x] Add `coscreen/workspace_undo.py`: durable per-task, per-actor, per-stage journal; transactional snapshot capture; conflict checks; idempotent restoration; trace events.
- [x] Add small capture hooks in `coscreen/db.py`, `coscreen/fulltext.py`, and `coscreen/coding.py`. Add atomic coding batch save while retaining the existing single-value API.
- [x] Add `custom_backend/workspace_undo_api.py`, registered once in `custom_backend/main.py`, using the existing authenticated membership and personal database dependencies. Expose status, Undo, and coding batch save.
- [x] Add `custom_frontend/workspace_undo.js`: compact buttons in each stage and Ctrl+Z / Command+Z outside input fields. Refresh progress and reopen by article key. Guard pending writes, task/reviewer switches, and stale responses.
- [x] Update existing save callbacks and coding form render guards in `custom_frontend/index.html`; register and cache-version the new script. Support editing a restored initial decision even if its prior state was already decided.
- [x] Add the new interface text in all ten supported languages and label Undo events in the research trace.
- [x] Review the diff and syntax-check changed Python/JavaScript. Do not add or run tests unless requested; do not make screening or coding decisions in the real running research database.

## Persistence and rollback

The new table is created transactionally on the first journaled save. Existing decision and coding schemas are unchanged. Reverting the feature code leaves the extra table harmless; dropping `workspace_undo` removes only Undo history. Pre-feature operations cannot be undone. Undo histories do not contain PDFs or images. If a saved record has changed since the recorded operation, return HTTP 409 without changing data.

## Delivery boundary

Implement and review locally. Do not publish a release, modify the installed client, or push without a new instruction. Pending Undo confirmation is required only when discarding an unsaved coding form.

## Completion evidence

- Python AST syntax checks passed for all six affected Python files.
- JavaScript syntax checks passed for the new module, changed language pack, research trace module, and both inline scripts.
- `build_app()` initialized successfully with a disposable data directory, loading the new route registration and Pydantic models.
- `git diff --check` passed. No automated tests or live screening/coding actions were run.
- Installed client, running real research database, remote repository, and published release were not changed.
