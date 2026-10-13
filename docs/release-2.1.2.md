# Release 2.1.2: import review loading

## Changes

- `import_review_loading.js`: bounded 20-item page requests, incremental results, actual counts, resumable failures, circular status and initial placeholders.
- `unified_import_workspace.js`: progressively populate the existing review categories; retain current reader DOM, expanded details, keyboard focus and scroll state when the record is unchanged; ignore obsolete task/batch responses and scroll callbacks; enable activation after loading completes.
- `import_export_workspace.css` and `import_export_locale.js`: approved compact loading status, mobile spacing, reduced motion and ten-language labels.
- `import_export_workspace.js`, `index.html`, `VERSION`: runtime footer `© 木笔ReviewFlow | Version 2.1.2` and cache refresh.
- README and release notes describe user-facing changes.

## Scope

Frontend loading and presentation only. Existing API endpoints, duplicate identification, transactional review saves, Undo safety, activation and owner/member authorization remain in place. No database schema migration is introduced. Source rows and candidate groups are counted as loading work; this number is not a unique-article total. Category counts show a lower bound until loading is complete.

## Validation

- Node syntax checks: four changed JavaScript files, both inline scripts in index.html.
- Language-pack structure: 86 rows, ten nonempty language strings per row.
- `git diff --check`.
- Manual GUI verification in a separate preview with copied disposable accounts/data: small batch; 240-source-record batch; records available before completion; network interruption preserves loaded data and expanded details; retry resumes missing pages; completed status and activation re-enabled; runtime footer verified.
- Responsive/dark/reduced-motion checks recorded in the delivery summary.
- No automated application test suite or real research decisions were run.

## Upgrade and rollback

Back up current research data using the existing maintenance procedure before replacing application binaries. This release does not require a new migration. Keep the data directory when upgrading or reverting binaries. Do not restore an older research-data backup over work saved after an upgrade. Development and GUI checks use an isolated worktree/data directory; E:/ReviewFlow is outside this change.
