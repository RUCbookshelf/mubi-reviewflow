# Import review presentation

## Scope and plan

Implement the attached goal in `codex/import-review-clarity`, based on main
`17d83f5`. Preserve data, activation rules, screening/coding, source history,
candidate review, CSV export and the existing design system. No subagents.

1. Read-only presentation projection: priority-sorted attention/completed pages,
   distinct counts and current bibliographic metadata. Existing GET defaults
   and all write endpoints remain compatible. No schema migration.
2. Plain-language batch header, explanations, metadata comparison, clear action
   effects, ready count, collapsed completed section and accessible empty states.
3. Import guide: separate duplicate/metadata review and activation; retain the
   Trace step and hide only when all applicable steps are complete.
4. Unit/component and integration tests using disposable fixtures only; static
   checks, regression checks, keyboard/theme/desktop/tablet rendering evidence.

The filtered GET addition is required because filtering the existing 20-record
page in the browser would hide outstanding records on later pages and produce
incorrect global counts. It changes presentation only, not import behavior.

## Delivery evidence

Validated on 2026-10-12. All fixture records and accounts are disposable.
The user's running application, installed client and research databases were
not used for mutations. Main remained at `17d83f5` throughout this work.

### Implemented layout

- A dynamic review headline, file/date identity and an expandable internal reference.
- Duplicate candidates first, followed by metadata differences, dependencies and
  records ready for activation. Counts cover the whole batch before pagination.
- Eight bibliographic fields compared side by side: title, abstract, authors,
  year, journal, DOI, record type and URL. Changed fields are labeled; long values
  expand and all imported text is rendered literally.
- Keep/Accept buttons describe their effect and reference that explanation with
  `aria-describedby`. Stale proposals cannot be accepted. Management controls
  remain owner-only.
- Activation shows the ready count, disables when nothing is ready and prevents
  duplicate in-flight requests. Unresolved candidates and metadata stay staged.
- Completed records and reviewed candidate groups are collapsed by default,
  loaded when opened, and independently paginated. CSV/original-record access is retained.
- Preparing/failed imports have honest status messages rather than a success state.
- Cross-batch dependencies link to the import that needs review or activation.
- The guide separates Import, Review, Activate and Trace. It uses task-wide counts,
  refreshes after changes, retains resolved/total title-group progress, preserves its collapse preference and closes only when
  all four steps are complete. Its buttons load the relevant batch and focus the
  matching section. Desktop space is reserved beside an open guide; on tablets it
  sits in page flow. Reduced motion removes the jump animation and smooth scroll.
- All new text is registered in the ten supported languages; user-authored content
  is unchanged. DOI and URL retain their standard international abbreviations.

### Backend compatibility

Only read-side presentation additions were made: `view=all|attention|completed`,
whole-batch review counts, current metadata/staleness, a linked-import reference,
and a task-wide guide summary. Default GET ordering and candidate payloads retain
compatibility. Candidate details are expanded only for the requested page. Existing
write routes, parsing, identity matching, activation and decision/coding storage
are unchanged. Import-change events refresh the open panel, retained-record choices survive a language refresh, and shrinking lists return to a valid page. The report's duplicated raw batch ID was removed from its note row;
it remains available under Import details and in provenance exports.

### Tests and evidence

Run from the worktree with application dependencies available:

```text
node --test build/tests/import_review_model.test.cjs
python build/tests/test_import_review_projection.py
node build/tests/import_review_browser.cjs
python build/validate_import_review.py
node build/validate_import_review_static.cjs
```

The browser runner uses Playwright (`REVIEWFLOW_PLAYWRIGHT` may point to its module
when it is not on the normal Node module path). The API runner reuses the existing
`validate_team_prisma.py` setup and creates a new temporary data directory before
importing the app. `REVIEWFLOW_VALIDATION_DEPENDENCIES` can supply the existing
httpx installation. No dependency or database migration is added.

| Validation | Result |
|---|---|
| Presentation-model and locale tests | 6 passed |
| Read-only Python projection tests | 6 passed |
| Existing team/source/PRISMA acceptance checks | All 26 passed |
| New API checks | Five batches, including 359-record fixture; attention/completed pagination, 28 candidate groups, default payload compatibility, owner/member/outsider permissions, current metadata, unchanged read snapshots |
| Actual fixture activation | Displayed ready count 37 matched 37 processed source records; unresolved record stayed staged; repeat added zero; existing decisions and coding unchanged |
| Browser component checks | Current production files; counts, ordering, collapsed/lazy completed section, metadata table, Keep/Accept, stale/owner controls, double-click protection, completed-group pagination, literal untrusted text, failures/retry, guide jumps/completion, reduced motion |
| Language and layout checks | Ten languages; 1440, 1024, 820 and 768 px; light/dark; no horizontal overflow or runtime errors |
| Full application render | Disposable server at port 18742; actual authentication and language engine; read-only UI inspection in all ten languages and four widths/two themes; zero page errors |
| Contrast | Relevant light/dark text pairs exceed 4.5:1; minimum checked ratio 4.94:1 |
| Static review | 17 JavaScript/locale files, both inline scripts and four changed/new Python files pass syntax; translations preserve placeholders; diff whitespace clean |

Preview and images:

- `C:/Users/16073/.codex/visualizations/2026/10/12/import-review-concept/import-review-concept.html`
- Same directory: `desktop.png`, `tablet-dark.png`, `live-panel.png`,
  `live-desktop.png`, `live-tablet-dark.png`.

The self-contained preview uses fictional records and an in-memory API simulator.
Its buttons cannot contact the application or alter research data.

### Requirement audit and rollback

Dynamic counts and priority filtering are proved by model/projection/API checks,
including records beyond the first page. Browser tests prove collapsed completed
sections, metadata comparisons, action states and guide completion. API snapshots
prove added read operations do not change saved data; fixture activation confirms
existing idempotent behavior. Screenshots and live UI checks cover layout and the
actual language engine. The focused diff preserves original-file access, candidate
review, CSV export and unrelated screening/coding/PRISMA features.

There is no schema migration. Roll back by returning to the original code/branch;
no research-data restore is required. Guide collapse/Trace-visit preferences keep
the existing per-user/task browser key. No main merge, push, release or installed
application replacement is included.

Recommended follow-up: review the preview in your normal working language before
merging. A release or installed-client update is a separate delivery action.
