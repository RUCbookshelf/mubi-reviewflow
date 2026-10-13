# Release 2.1.2: progressive loading and compact, responsive review

## Original 2.1.2 changes

- `import_review_loading.js`: bounded 20-item page requests, incremental results, actual counts, resumable failures, circular status and initial placeholders.
- `unified_import_workspace.js`: progressively populate the existing review categories; retain current reader DOM, expanded details, keyboard focus and scroll state when the record is unchanged; ignore obsolete task/batch responses and scroll callbacks; enable activation after loading completes.
- `import_export_workspace.css` and `import_export_locale.js`: approved compact loading status, mobile spacing, reduced motion and ten-language labels.
- `import_export_workspace.js`, `index.html`, `VERSION`: runtime footer `© 木笔ReviewFlow | Version 2.1.2` and cache refresh.
- README and release notes describe user-facing changes.

## Original scope

Frontend loading and presentation only. Existing API endpoints, duplicate identification, transactional review saves, Undo safety, activation and owner/member authorization remain in place. No database schema migration is introduced. Source rows and candidate groups are counted as loading work; this number is not a unique-article total. Category counts show a lower bound until loading is complete.

## Original validation

- Node syntax checks: four changed JavaScript files, both inline scripts in index.html.
- Language-pack structure: 86 rows, ten nonempty language strings per row.
- `git diff --check`.
- Manual GUI verification in a separate preview with copied disposable accounts/data: small batch; 240-source-record batch; records available before completion; network interruption preserves loaded data and expanded details; retry resumes missing pages; completed status and activation re-enabled; runtime footer verified.
- Responsive/dark/reduced-motion checks recorded in the delivery summary.
- No automated application test suite or real research decisions were run.

## Replacement 2.1.2 changes (2026-10-13)

All original release entries remain in `RELEASE_NOTES.md`, in the existing ten-language Added / Improved / Fixed structure. The public version and runtime footer remain `2.1.2`; changed frontend resources use cache revision `2.1.2.2`.

- Compact batch/loading header, persistent loading counts, collapsed source-file menu, circular information icon, equal-width category tabs and phone record selector. Existing dynamic sun/moon controls are retained.
- Server-confirmed save responses include only affected review rows, related proposals and classifications. The frontend overlays these updates onto background pages and displays the next cached record without waiting for policy, progress or notice reloads. Failed saves retain selection/details. No optimistic research decisions are written.
- Explicit bulk confirmation for loaded candidates with matching nonempty DOI and no identity conflict, or bulk keeping existing metadata (up to 100 items). A preview lists affected records and their keeper; the server revalidates every action inside one transaction. Any invalid action rolls back all writes.
- Composite snapshot supports one guarded Undo of the bulk operation, subject to the existing version/activation guards. Saved research decisions, coding and original import payloads are not rewritten.
- Existing single-item routes remain compatible. The authenticated `/bulk-review` route is additive. No database schema migration or change to duplicate matching, screening or analysis algorithms.

## Replacement validation and code review

- 12 authenticated API integration tests, with a temporary data directory: single save/delta/Undo, atomic bulk/Undo, rollback, empty/duplicate actions, title-only and identity-conflict exclusions, stale version rejection, activation closing Undo, related stale metadata proposals, and unauthenticated write rejection.
- Six existing projection tests; seven existing presentation tests; four added cache/eligibility/ten-language tests.
- Chromium harness executes product UI helpers, review modules and mobile/disclosure styles. Passed 120 combinations of six widths (320, 390, 760, 820, 1024, 1440), two themes and ten languages; no horizontal overflow and equal tab widths. Single save, old background overlay, Undo, failure selection/details retention, 100-item bulk preview and bulk Undo passed.
- Harness delayed auxiliary statistics by two seconds: next record became enabled in 91–114 ms, including a 40 ms simulated save. This demonstrates that auxiliary requests no longer block the next record; it is not a latency guarantee for all deployments.
- Live launcher on a separate data directory and port 18747, 240 synthetic source rows / 120 candidate groups: saved and moved to next record in 309 ms, then Undo restored it. Runtime footer showed `© 木笔ReviewFlow | Version 2.1.2`; browser reported no uncaught errors.
- Disposable API benchmark: 1,200 source rows / 600 groups, ten single saves: 179.3–249.4 ms in the final integration run. Earlier concurrent test runs varied up to 356.1 ms. Network and server capacity still affect actual response time.
- Reviewed transaction boundaries, authorization, affected-record dependency propagation, failed-save behavior, cache versioning, legacy/new Undo precedence and stale background overwrites. Narrow-screen long-label overflow found during testing was corrected.
- Node syntax checks, Python compilation, inline-script syntax checks and `git diff --check` run before commit. Tests and live operations touched only synthetic data. No writes to `E:/ReviewFlow`.

Reproduce API tests with the application dependencies and the compatible Starlette TestClient dependency; run Node tests with `node --test build/tests/import_review_model.test.cjs build/tests/import_review_updates.test.cjs`. The browser harness uses `playwright` via `NODE_PATH` or `REVIEWFLOW_PLAYWRIGHT`. Test PNG files are local diagnostic artifacts, not installer inputs.

## Upgrade and rollback

Back up current research data using the existing maintenance procedure before replacing application binaries. This release does not require a new migration. Keep the data directory when upgrading or reverting binaries. Do not restore an older research-data backup over work saved after an upgrade. Development and GUI checks use an isolated worktree/data directory; E:/ReviewFlow is outside this change.
