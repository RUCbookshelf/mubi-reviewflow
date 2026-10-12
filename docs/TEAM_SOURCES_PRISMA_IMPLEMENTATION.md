# Team sources and PRISMA compatibility update

Branch: `codex/team-sources-prisma`, based on `f1eee64` (2.0.5).

## Approved scope

1. Automatically discover imported sources from RIS database metadata, with clearly identified filename suggestions and file-source fallbacks. Recover reliable legacy provenance without changing research decisions.
2. One Team Management container: members/progress, source permissions, decision options, imported sources. Keep existing member menus and ownership checks.
3. Separate source scope from screening options; show source checkboxes only for Selected sources.
4. Display Needs review for saved `maybe` decisions; preserve stored values and historical decisions.
5. Task owner / Coder / Adjudicator SVG badges. Preserve internal `reconciler` permissions.
6. Optional PRISMA task metrics import. Preserve standalone manual entry, overrides, arithmetic help, all review/source modes, validation, citation styles and exports. Require confirmed study counts/grouping; one-report-per-study is an explicit shortcut. Complete previous/new/total and other-source fields.

## Boundaries

Work is isolated; no subagents, main merge, GitHub publication, installed-client replacement, real-data decisions, Kappa changes or adjudication-rule changes. `E:/ReviewFlow` remains untouched.

## Implementation record

- Read-only review confirmed manual generation has no task-ID requirement. Task import currently overwrites override flags, collapses source rows, and incorrectly infers retrieval failures from unassessed reports.
- Existing backend supports other-source and updated-review fields; existing frontend does not fully expose them.
- Validation uses syntax checks, disposable fixtures, API checks and an isolated browser preview. No actions in existing research tasks.

## Progress

- [x] Source discovery, recovery, correction and additive migration
- [x] Team container, roles and decision wording
- [x] Task-level PRISMA metrics with uncertainty and provenance
- [x] Manual/assisted form compatibility and completed review modes
- [x] Ten-language localization and cache versions
- [x] Independent validation and final diff review

## Delivered changes

| Area | Main files | Behavior |
| --- | --- | --- |
| Source discovery | `coscreen/import_sources.py`, import service hooks | Read each RIS record's DB metadata, preserve provider separately, support mixed-source files. Missing database metadata uses a file source; filename matches are suggestions requiring confirmation. |
| Legacy recovery | `import_sources.py`, workflow API | Recover reliable provenance, preserve source IDs and selected scopes, hide unused Unassigned, retain genuine unknown provenance as Source not recorded. |
| Team interface | `workflow_controls.js/.css`, `team_roles.js`, `main.py` | One Team Management container, Members & progress, Sources available by stage, Screening decision options, Imported sources. Keep existing member menus and permissions. |
| Decision wording | `team_prisma_locale.js`, `help_locale.js`, `index.html` | Needs review and stage-specific explanations; Task owner / Coder / Adjudicator badges. Stored `maybe` and `reconciler` values remain unchanged. |
| PRISMA task adapter | `custom_backend/prisma_task_metrics.py` | Optional reviewer or authorized consolidated-team snapshots, original source counts, duplicate excess, completed screening outcomes and CSV provenance. |
| PRISMA form | `prisma_task_ui.js`, `prisma_form.py`, `index.html` | Selective task import, retained manual values, updated-review and other-source fields, independently confirmed study counts and an explicit one-report-per-study shortcut. Existing exporter/validator retained. |
| Localization | `team_prisma_locale.js`, `help_locale.js` | 95 complete ten-language entries, compatible aliases for historical strings and cache-version refresh. |

No application-version bump is included. The base version remains 2.0.5.

## Validation record — 2026-10-12

All checks were performed by the primary agent without delegation.

### Disposable fixture/API checks

`build/validate_team_prisma.py` creates a new temporary data directory before importing the application. It never reads the installed application's data directory.

26 checks passed:

- Mixed RIS database sources; empty fallback hidden; repeated import idempotence; empty parsed-file source preparation.
- Initial Needs review progression; additional imports staged.
- Member role identity; grants private to the owner; unauthorized owner/team-metric operations refused.
- Incomplete team decisions not counted as resolved; CSV metric provenance.
- Current adjudication snapshots recognized; swapped reviewer votes refuse stale adjudication. Existing adjudication logic was not modified.
- Legacy migration backup and idempotence; existing articles, decisions, coding, notes and image-reference rows unchanged; selected source scopes retained.
- Provider-only metadata not mistaken for a database; filename suggestions labeled; owner corrections and legacy source IDs retained.
- Exact duplicate excess and overlapping database associations counted separately.
- Standalone manual generation; updated reviews; other-source branch; DTA; independent study/report counts; existing static-cache headers.

Repeat command in a Python environment containing the application dependencies and httpx:

```powershell
python build/validate_team_prisma.py
```

For this machine, the existing workflow-controls Python environment was reused read-only. The optional `REVIEWFLOW_VALIDATION_DEPENDENCIES` setting supplied the existing httpx package location; no dependency installation was performed.

### Static checks

- Python AST syntax checks: 9 changed/new Python files.
- Node syntax checks: 18 relevant JavaScript files.
- `build/validate_team_prisma_static.js`: 7 changed/new JavaScript files, both inline scripts, all 13 language-pack files, and 95 rows with ten nonblank translations and matching placeholders.
- `git diff --check`: passed.
- Existing control IDs and handlers, internal roles/decision values, and unrelated application behavior reviewed in the final diff.

```powershell
node build/validate_team_prisma_static.js
git diff --check
```

### Actual browser rendering

Isolated preview: `http://127.0.0.1:18740/`, disposable accounts and data only.

- Team Management contains the four subsections, role SVGs and existing member menus. Source checkboxes are hidden for All and shown for Selected.
- Manual PRISMA generation renders a valid diagram and retains the original SVG/PNG/PDF export controls.
- Task import preserves an explicitly entered duplicate count and leaves unknown study/retrieval counts for confirmation.
- Updated/other-source generation works. Switching New → Updated → New preserves the entered study/report drafts; Updated displays previous + new totals. Auto-fill resets derivable fields without inventing a study count.
- New fields render in all ten languages. Dark mode and 360/1280-pixel layouts checked without page overflow; viewport restored afterward.

Browser artifacts are saved outside the repository under the visualization workspace:

- `team-management-overview.jpg`
- `team-management-implementation.jpg`
- `team-management-final.jpg`
- `prisma-implementation.jpg`
- `team-prisma-language-checks.json`

Final fixture data directory: `C:/Users/16073/AppData/Local/Temp/reviewflow-team-prisma-validation-j_5gdvn7`. The running preview uses the separate disposable directory ending `s1ktjdcf`; neither directory contains real research data.

### Findings corrected during validation

- Source metadata may reside in `raw_json`, not only `raw`.
- A new source migration must use version 4, avoiding existing migration version 3.
- Flex styles overrode the HTML hidden attribute on source checkboxes.
- Existing page-layout code nests PRISMA cards; insertion must use their actual parent.
- Unconfirmed study counts must remain blank; manual report drafts must not leak into Updated-review totals.
- Empty parsed-file arrays need a guarded file-source fallback.
- Per-record fallback source arrays must be allocated once per file, not once per record.

## Metrics and remaining confirmation requirements

- Retained original source records differ from active canonical articles. Database-specific active totals may overlap and must not be summed into an overall unique total.
- Staged imports are excluded from active PRISMA identification/screening counts.
- Legacy original retrieval counts cannot be reconstructed when original records were not retained. Unknown sources/types need owner confirmation.
- Reports awaiting assessment are not retrieval failures. Retrieval-failure fields remain unknown until the user supplies the count.
- Studies and reports are distinct. Import a study count only when every included report has confirmed grouping; otherwise require manual confirmation.
- Team consolidation requires complete agreeing live votes or a current, verifiable adjudication snapshot. Unknown offline reviewer labels and stale snapshots remain unresolved.
- Language validation confirms syntax, completeness and rendered strings; it is not a native-speaker linguistic certification.
- Native installer builds and production deployment were not performed in this isolated implementation.

## Migration and rollback

The additive migration creates `workflow_source_details` and records version 4 in `workflow_migrations`. Before upgrading, SQLite's backup API saves `<database>.pre-source-v4.bak`. Repeated upgrades are idempotent.

The migration changes provenance associations and related source-policy IDs when reliable recovery is available. It does not rewrite article metadata, decisions, coding, notes or evidence. Genuine unknown origins remain visible. No ownership is invented for legacy tasks.

To revert the isolated implementation, stop its preview process and return to the base code. Added tables can remain; stable source IDs and saved research work are retained. Unknown source information still requires confirmation in older clients.

For a maintenance data rollback, stop the service and preserve the entire current data directory first. Do not restore an old backup over post-upgrade work. Export/reconcile newer imports, source policies and research records before any database restore. The pre-upgrade backup is a recovery artifact, not a safe automatic downgrade mechanism.

Main checkout and installed application were left untouched; this branch has not been merged, pushed or released.

## Follow-up: compact screening decision controls

On 2026-10-12, refined only the initial-screening decision area:

- Grouped the existing custom-reason input and reusable-reason button on one desktop row; retained their IDs, event handlers and text.
- Reduced desktop control heights to 40 px, spacing to 8 px and vertical padding to 10 px. Touch controls retain a 44 px minimum.
- Removed the unused decision column when Needs review is disabled; narrow layouts stack the reason input and save action.
- Updated the two affected asset references to avoid stale browser caches.

Changed product files: `screen_reader.css`, `workflow_controls.js` (four presentation-only lines), and `index.html` (two cache references). Backend logic, full-text screening and saved decisions were not changed.

Validation: JavaScript/inline-script/language-pack syntax checks and diff review passed. Actual browser checks covered custom and built-in reasons, light/dark themes and a 390 px layout without horizontal page overflow. The desktop decision area measured 178.7 px before and 108.7 px after, approximately 39% shorter. No screening decisions or reason-library writes were submitted. The original tab's unsaved custom-reason draft was retained; verification used a separate disposable preview tab.

Screenshot: `screen-decision-spacing-detail.jpg` in the external visualization workspace. No main merge, push, release or installed-client replacement.
