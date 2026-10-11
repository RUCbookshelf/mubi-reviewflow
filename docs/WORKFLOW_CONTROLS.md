# Workflow controls: integration and maintenance

This document describes the implementation on `codex/workflow-controls`, based on `56cf864`. It is a developer delivery record, not a release or a claim that every acceptance scenario has passed. The implementation preserves reviewer independence, saved decisions and coding, historical reasons, article identities, and existing member roles. It adds no Kappa, adjudication, or remote-operation interface.

See [WORKFLOW_VALIDATION.md](WORKFLOW_VALIDATION.md) for the final repair scope, source gates and bounded manual acceptance record.

## Isolated launch

Worktree: `C:/Users/16073/.codex/worktrees/workflow-controls/mubi-reviewflow`. Do not launch against the installed application's data or the main checkout. The controller owns the existing fictional-data preview on port `18738`; do not start a second process there or change its files while it is being reviewed.

For a separate, explicitly authorized manual preview, choose a fresh directory and free port. The following is a launch recipe, not a command run by the delivery implementer:

```powershell
Set-Location 'C:/Users/16073/.codex/worktrees/workflow-controls/mubi-reviewflow'
$workflowPreview = Join-Path $env:TEMP ('ReviewFlow-workflow-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $workflowPreview | Out-Null
$env:COBOOKSHELF_DATA_DIR = Join-Path $workflowPreview 'data'
$env:REVIEWFLOW_STORAGE_CONFIG = Join-Path $workflowPreview 'control/local-storage.json'
$env:REVIEWFLOW_PORT_FILE = Join-Path $workflowPreview 'control/port.txt'
$env:REVIEWFLOW_PORT = '18739'
$env:REVIEWFLOW_MODE = 'desktop'
Remove-Item Env:REVIEWFLOW_RUNTIME_OVERLAY -ErrorAction SilentlyContinue
& '.venv/Scripts/python.exe' 'build/launcher.py'
```

Use an installed interpreter with the dependencies in `build/requirements-app.txt` if the isolated worktree has no environment. Set all isolation variables before any application import. Isolating the storage configuration is essential: the launcher also stores its lock, restart request and port in that configuration's parent directory. The launcher may choose a nearby free port; read the isolated port file. It starts a child server and opens its local browser page. Stop only the preview you started. Remove preview-specific environment variables in that shell after it exits. Create fictional accounts and records only; do not copy real data for this acceptance run.

## Storage and numbered migrations

`workflow_migrations(version, applied_at)` records additive, idempotent upgrades. Authentication resolves the actor and verified task owner before workflow initialization. Unknown legacy ownership is never invented: its reads use compatible defaults and owner management is unavailable. An absent reviewer store follows existing base initialization; an existing reviewer store is migrated before catalogue synchronization.

| Version | Implementation | Additions and backup |
| --- | --- | --- |
| 1 | `coscreen/review_workflow.py` | Workflow state/version, sources, article-source links, reason libraries, persistent random order and catalogue synchronization marker; catalogue revision triggers where articles exist. Before the first upgrade of an existing database, SQLite's backup API creates `<database>.pre-workflow-v1.bak` if absent. |
| 2 | `coscreen/review_imports.py` | Import research latch, batches, files, immutable source records, batch memberships, metadata proposals, candidate groups/members, accepted metadata and reviewer application markers. Invokes migration 1 first; **there is no separate v2 backup file**. |
| 3 | `coscreen/coding_evidence.py` | Stable coding fields, requirement definition snapshots, staged uploads, form request digests, and nullable `workspace_undo.request_id` on an existing journal. Calls migration 1, then makes `<database>.pre-workflow-v3.bak` if absent before v3 DDL. New journals add the link column through their normal initializer. |

Backup files are not overwritten. Version 2 and version 3 perform their version checks within `BEGIN IMMEDIATE` transactions; version 3 also checks before creating its backup. Saved notes/images continue to use `coding_notes` and `coding_note_images`. There is no migration 4. Migrations do not renumber saved dimensions or rewrite decisions. Uncertain historical provenance receives the reserved `00000000-0000-0000-0000-000000000000` Unassigned source; filenames are not provenance evidence.

Personal reason stores live at `<data>/workflow_accounts/<owner_user_id>.sqlite3` and follow migration 1. Shared reasons live in the owner task store. A team latch remembers verified non-owner grants once observed, including before revocation, but cannot reconstruct grants removed before this upgrade. Becoming a shared task does not automatically reveal personal reason libraries.

## Workflow, source and reason contracts

The following paths begin `/api/tasks/{task_id}` and require authentication and current task access. Stages are `screen`, `fulltext`, and `coding`; the UI calls coding `bench`. Actor identity comes from authentication, never from another member's name supplied by the client.

| Method and suffix | Contract |
| --- | --- |
| `GET /workflow-settings` | Returns `version`, `stages`, `evidence_required_field_ids`, `task_kind`, `can_manage`, `sources`, `evidence_fields`, `mapping_issues`, and `submission_evidence` discovery. |
| `PUT /workflow-settings` | Owner body: `{expected_version, stages, evidence_required_field_ids}`. Supply all three stages. Each has `{mode: 'all'|'selected'|'paused', source_ids: [...]}`; screen/fulltext also have Boolean `show_maybe`. Selected requires at least one valid source. Required evidence IDs are owner stable UUID strings. Saving requirements explicitly confirms their current definitions. |
| `GET /sources` | Lists stable source identities. A source carries `id`, `name`, `active`, `reserved`. |
| `POST /sources`, `PUT /sources/{source_id}` | Owner body `{name, active, expected_version?}`. Name is 1–200 characters; normalized names are unique. The reserved Unassigned source cannot be renamed. Sources are activated/paused rather than deleting provenance. |
| `GET /reason-library?stage=screen|fulltext` | Returns `entries`, effective task kind and management capability. Personal entries are account-owned; shared entries are owner-managed. |
| `POST /reason-library`, `PUT /reason-library/{reason_id}` | Body `{text, stage?}`; stage is required for creation via body or query. Text is 1–2000 characters, trimmed and normalized for duplicate detection. Existing decision snapshots retain their original strings. |
| `DELETE /reason-library/{reason_id}` | Removes a reusable entry, not historical exclusion text. |
| `GET /work-queue/{stage}?source_id=...&order=import|random` | Returns the authoritative eligible actor queue, counts, `policy_version`, allowed source IDs and paused state. Omitted source means all permitted sources. Overall counts deduplicate articles; source counts may overlap. |
| `POST /work-queue/screen/shuffle?source_id=...` | Explicitly reshuffles only pending records in that actor/task/source journal. Completed positions are retained. Newly eligible keys append; temporarily removed keys retain their stored positions. |
| `GET /work-items/{stage}/{key}` | Loads current article/saved state and `writable`, `reason`, `policy_version`. Coding adds evidence/mapping diagnostics. Historical readable work may be outside the writable queue. |

Maybe defaults to enabled in both screening stages. Hiding it prevents new Maybe saves, including keyboard actions, without converting old decisions. Initial Include or Maybe progresses to full text. Existing coding eligibility rules remain distinct. Machine Learning ranking trains on the current reviewer's Include/Exclude labels and intersects its returned keys with the current permitted pending queue; its existing model strategy identifiers are unchanged.

Save requests send the loaded item's `expected_policy_version` where the endpoint supports it. A conflict returns HTTP 409 with `detail.code`, `version`, and, for policy/scope conflicts, current `workflow_settings`. Codes include `workflow_version_conflict`, `stage_paused`, `article_outside_scope`, `article_not_eligible`, and `maybe_disabled`. The frontend retains drafts, refreshes policy and offers explicit application of the current version to that retained draft. It does not silently retry a changed policy as a write.

The existing EventSource handles `task-workflow-changed`. Visible pages poll every 30 seconds and refresh on focus/visibility/entry/conflict. Dirty forms retain values, notes and pending associations; clean reloads use generation, actor, task, page and edit-revision guards. Source and reason refreshes also occur when the policy version is unchanged. Notifications contain no private evidence.

## Protected imports

The existing `POST /import` and `POST /import-batch` multipart endpoints accept `source_id` for one file or JSON `source_ids` aligned one-for-one with multiple files. Batch upload accepts 1–20 files. Only the verified owner catalogue receives imports. The frontend asks for an existing/new/Unassigned source for every file before upload.

When saved research exists, new source records remain in `workflow_source_records` with state `staged`, outside active `articles`. Existing exact matches link to current identities; metadata differences become explicit proposals. Title/fuzzy or identity-conflict groups block the affected new records without stopping existing work. Original source payloads and original uploaded files remain available. Same-source/same-file/same-row identity uses `(source_id, digest, row_index)` for idempotent reimport.

| Method and suffix | Contract |
| --- | --- |
| `GET /import-batches` | `limit` 1–200 (default 50), nonnegative `offset`; returns `batches`, `total`, `next_offset`, `can_manage`. |
| `GET /import-batches/{batch_id}` | Same pagination for source records; returns current batch summary, records, `next_offset`, `can_manage`. |
| `GET /import-batches/{batch_id}/candidates` | Separate candidate pagination; returns `candidates`, `total`, `next_offset`. |
| `POST /import-batches/{batch_id}/candidates/{review_id}` | `{status: 'duplicate'|'not_duplicate'|'undecided', keeper_key?, keeper_record_id?, expected_policy_version?}`. A duplicate decision selects the active key or staged source record to retain. Multiple active records cannot be destructively merged here. |
| `POST /import-batches/{batch_id}/metadata/{record_id}` | `{action: 'keep_existing'|'accept_incoming', expected_policy_version?}`; no silent metadata overwrite. |
| `POST /import-batches/{batch_id}/activate` | `{expected_policy_version?}`; activates only resolved records and leaves unresolved records staged. |
| `GET /import-batches/{batch_id}/export` | Authenticated provenance CSV; original source fields remain literal. |

`activated_new` and `activated_source_records` are **individual activation-call deltas**. `activated_new_total` counts distinct activated new roots attributed to their first batch; repeated source rows do not claim another new article. `activated_source_records_total` counts currently active source records in this batch. `current_screening_total` covers the current task, excluding duplicates and affected pending legacy title candidates. These are server projections. Receipt/parser snapshots, current batch state, activation and screening totals are displayed separately. Import history hydrates current summaries by `batch_id`; original parsing snapshots remain explicit.

## Coding evidence and Undo

Stable UUID mapping uses exact semantic definitions: name, dtype, ordered options, multi-select, unit and description. Numeric dimension equality between actors is never evidence of identity. Missing/ambiguous/changed required definitions block completion without clearing saved values. Scheme replacement cannot silently remove research-bearing fields. Owner removal/change of required fields requires explicit requirement review.

`POST /coding/values-batch` accepts:

```json
{
  "zotero_key": "FICTIONAL-ARTICLE",
  "values": {"17": "42"},
  "notes": {"17": "Table 2, page 4"},
  "evidence": {"17": {"keep_image_ids": [9], "upload_ids": ["staged-upload-id"]}},
  "expected_policy_version": 12,
  "draft": false
}
```

Map keys are actor-local dimension IDs; requirements use stable owner field UUIDs. Omitted/null notes or evidence preserve existing content. Omitted dimensions remain unchanged; explicit blank notes clear text. An explicit evidence object specifies desired image associations. Empty lists detach images for that field. Each map allows at most 1000 fields, values 4000 characters, notes 100000 characters, and each image list 100 IDs. At least one value/note/evidence field is required. Current values, notes, associations, replay journal, Trace and one Undo snapshot commit in the same actor SQLite transaction. `X-Request-ID` deduplicates identical form intent and rejects different intent for the same ID.

The response includes `saved`, `progress`, `complete`, `draft`, `evidence`, `evidence_complete`, `missing_evidence`, and `mapping_issues`. Required evidence accepts either a nonblank note or an attached image. Pending uploads are not saved evidence. Missing values/evidence yield a saved draft, not completed coding. Completion counts derive from current saved values/evidence; an explicit draft flag is not a separate persistent completion override. Notes and quotes are plain text rendered with `translate="no"`.

### Actor-private images

- `POST /coding/evidence/uploads`: multipart `zotero_key`, `dim_id`, `file`. This personal staging route checks current policy/writability but **does not bind an `expected_policy_version` form field**; final form save checks its version. Returns `upload_id`, `sha256`, `preview_url`.
- `GET /coding/evidence/uploads/{upload_id}` and `GET /coding/evidence/images/{image_id}` authenticate the current actor and exact database association. Another actor cannot choose a store through request parameters.
- PNG/JPEG/WebP only, up to 10 MiB (`10 * 1024 * 1024`) and 25 million decoded pixels. Signature, format, Pillow verification and full decoding must agree. Original names never form storage paths.
- New files reside at `<task>/coding_assets/<sha256(actor-db-name)[:24]>/<UUID>.<validated-extension>`. Existing attached legacy files are served only through that actor's exact association. Paths and symlinks are checked. Responses include an explicit image MIME, `private, no-store` and `nosniff`.
- The frontend uses authenticated blob fetches and revocable object URLs. Staged uploads do not create coding values/notes, Trace saves or completed evidence. Failed uploads retain entered notes and already attached images.
- Detached/replaced attached evidence binaries are retained for Undo/history. Each successful staged upload triggers cleanup of unattached staged uploads older than seven days; cleanup checks paths and existing associations before deleting eligible staged files. Preserve the entire assets tree when backing up or rolling back.

### Shared submission evidence

`workflow-settings.submission_evidence` advertises `available`, `access_context`, `queue_url`, `read_url_template`, `save_url`, `upload_url`, and `undo_url`. Current routes are `GET /coding/submission-evidence`, `GET /coding/submission-evidence/{zotero_key}`, `POST /coding/submission-evidence`, and `POST /coding/submission-evidence/uploads`. Availability requires current coding-submit permission. The UI entry appears for team tasks and can work with an empty personal coding queue. The canonical queue accepts optional `source_id` narrowing within owner-permitted coding targets and returns `allowed_sources`, `scope_progress`, `overall_progress`, `policy_version` and `paused`. Its progress counts current actor-private evidence requirements satisfied, independently of coding values; with no required evidence and no mapping issues, all eligible targets satisfy evidence requirements. The UI labels these counts as evidence requirements, not completed coding. Source filtering never broadens the owner policy.

The save body contains `zotero_key`, `notes`, `evidence`, `expected_policy_version?`, `draft?`. Extra fields, including `values`, `screener`, and client access-context overrides, are forbidden. Upload has the same fields as personal upload plus optional `expected_policy_version`. These routes edit actor-private notes/images for canonical targets; they neither submit shared coding values nor reconcile them. `saved:true, evidence_complete:true, complete:false` is a valid evidence-only result. Required evidence is checked again by the existing shared submission path.

### Trace and current-state correction

Trace menus request `GET /trace/events/{event_id}` for authoritative `actions`, including Overview Recent rows. Actions include `can_open`, `can_undo`, `undo_entry_id`, `writable`, `reason`, `access_context`, and `work_item_url`. Coding actions additionally expose `current_field` only when the authenticated actor has an unretired Trace lifecycle with the same current semantic definition; canonical evidence also requires the current owner mapping. The returned actor-local dimension and definition are checked against the current rendered card, without comparing Trace and workflow UUID namespaces. Opening follows the validated current work-item route and never writes the event's old after-state. Out-of-scope articles open read-only; missing historical field mappings do not restore removed fields.

`GET /undo/{stage}` returns `{latest}`. `POST /undo/{stage}` takes `{entry_id, expected_policy_version?}` and returns the restored current context. Undo requires the authenticated actor's latest unchanged save for that stage, exact journal linkage and current authorization. Imported/baseline/unlinked events cannot overwrite current research. Personal and canonical evidence share the actor/stage latest-save rule; canonical context is derived from server journal snapshots, not client declarations. Save/Discard/Stay guards protect navigation, Trace correction, Undo, task changes and logout while preserving values and evidence.

## Packaging and cache delivery

Deliver the backend and frontend together. New Python modules are `coscreen/review_workflow.py`, `coscreen/review_imports.py`, `coscreen/coding_evidence.py`, `custom_backend/review_workflow_api.py`, and `custom_backend/review_imports_api.py`. New browser assets are `workflow_locale.js`, `workflow_controls.js`, `workflow_evidence.js`, `workflow_imports.js`, and `workflow_controls.css`. Existing main/Trace/Undo/import-history and coding modules contain integration changes and are also required.

Windows packaging recursively copies `custom_backend`, `custom_frontend`, and `coscreen`; its exclusions do not exclude these files. Linux/macOS add the complete frontend/backend directories and PyInstaller follows the explicit Python imports from the launcher/main modules into the new `coscreen` modules. Pillow is declared in both backend and packaged runtime requirements. This is source inspection only: no installer or frozen executable was built or launched.

Every branch-modified external script reference in `index.html`, including import guide/history and changed language packs, uses the common final version `20261012-workflow6`. The workflow stylesheet also uses `20261012-workflow6`, retaining the controller-observed Trace drawer menu clipping correction: its dropdown is anchored inside the drawer and constrained to the available content width, while table menu placement stays unchanged. Hard-refreshing an old cached script can make an apparent missing Trace action disappear; cache versioning is part of the delivery and must accompany the HTML. Do not publish only the new locale file.

## Maintenance rollback

1. Stop writes and stop the affected application processes. Make a current consistent backup of **all** owner/reviewer/account/shared databases, Trace/Undo/import stores, original import files, PDFs, coding assets (including staged and detached files), task metadata and storage configuration. Preserve the post-upgrade tree independently of the pre-upgrade migration snapshots.
2. Switch application code only in a separate checkout/package after recording the exact commit. Additional tables are additive; old code can leave them in place. Staged imports remain outside active `articles` and must not be manually inserted or discarded to make an old UI look complete.
3. **Never restore `.pre-workflow-v1.bak` or `.pre-workflow-v3.bak` over post-upgrade work.** Those files represent older snapshots and would lose later decisions, evidence, imports and provenance. Any recovery from them must happen on a separate copy with an explicit data reconciliation plan.
4. Old clients do not enforce the new owner source/paused/Maybe policies or evidence completeness requirements. Before allowing writes with older code, maintenance must review policy consequences, reviewer access, protected import state, saved evidence and Undo compatibility. Keeping extra tables is not proof that mixed-version operation is safe. Do not run old/new writers concurrently against the same data.
5. If a forward repair is needed, retain all new tables, file associations and UUIDs. Remove no staged/detached assets until a retention and Undo review has established they are unreferenced. Re-run only the explicitly authorized acceptance work on an isolated copy before returning to normal use.

## Validation and remaining acceptance

Task 5 uses source-only Python AST parsing for every branch-changed Python file, Node syntax checks for every branch-changed standalone script and every language pack, compilation of extracted inline scripts without execution, translation row/placeholder inspection, cache-reference inspection and Git whitespace checks. No automated tests, application imports, migrations, databases, HTTP requests, browser actions, package builds, installed-client operations or remote publication are performed by this delivery implementer.

The controller separately reported that the suspected missing Trace detail actions came from a stale cached frontend: after a normal reload with cache temporarily disabled, an exact event-detail request returned `can_open`, `can_undo`, entry 2 and `coding_submission_evidence`. This supersedes the earlier suspected backend issue; it is controller evidence from the Task 4 preview, not Task 5 runtime validation.

Additional controller observations with fictional data: keyboard Trace correction opened the current member note; Trace Undo restored the previous blank note and required-evidence gap without changing coding values or screening decisions. Rejection of an actual invalid PNG fixture cleared the upload busy state and re-enabled Save. Batch B's current report/history showed cumulative 1 new article, 3 active source records, no staged records or metadata proposals, and a task queue of 7. Importing two exact existing records under explicit source Database D activated 0 new articles and 2 provenance records, leaving the queue at 7; repeating the same file/source added no new research work (a new receipt batch is allowed). Database D's scope retained the same Maybe history and showed 1/2 progress, with overall total 7. Two earlier upload attempts referenced nonexistent fixture paths and provide no acceptance evidence. These observations cover the stated examples only. Subsequent controller checks confirmed the drawer mouse correction, the six Home tab labels in all ten languages, keyboard roving focus, dark mode and reduced motion at an actual minimum viewport width of 599 CSS pixels. They did not establish 390-pixel phone acceptance or every localized message.

Manual acceptance of the final assets remains pending: all ten languages and live language changes; narrow/wide/dark layouts; keyboard and reduced motion; retained edits during policy refresh; two actors and revoked roles; required evidence drafts/images/atomic Undo; protected reimport/candidate/metadata/activation flows; migration idempotence and restore-on-copy; and packaged platform launches. The final repair wave disables context selectors during a clean automatic replacement and excludes rejected selector input from edit revisions; focused manual follow-through on these final assets remains pending. Backend guarantees remain bounded by same-process task locks and per-database transactions; cross-process races, crash recovery and file-retention maintenance require their own acceptance evidence.


## Living Review import boundary and refresh recovery

The two legacy article-entry routes, `POST /living-review/import-update` and `POST /living-review/check-now`, now authorize the verified owner through `review_imports.authorize` and then return `409` with `code: protected_import_required`, `import_url`, `review_url` and an actionable message. Unauthorized members receive the owner guard's denial. No original file is consumed, no PubMed fetch is performed, and no legacy incremental article write, fuzzy auto-resolution, analysis rerun, history append or last-run date advance happens through these routes.

Both existing UI handlers show a ten-language explanation and an **Open import review** action. Export search results or select the original source file in the normal Import screen, assign its source, and follow protected metadata/candidate review and activation. Selection in the old Living Review picker is not transferred or imported silently. Original bytes and provenance are recorded when the normal protected import service receives that file. Living Review saved searches, settings, analysis results and history remain intact; search editing and historical viewing remain available. Analysis can be run separately after activation; automatic incremental analysis is not delegated in this release. Source inspection found no other production callers or scheduled paths for `process_incremental_update` / `process_incremental_articles`; the legacy library functions remain callable by external Python consumers outside the application route contract.

Policy reads share an in-flight promise for each task/actor scope. Queue consumers await readiness, validate the current scope and policy version, and reject stale responses. Mutation/event refreshes arriving during a read request another read. Task transitions clear prior readers/history and disable save/source controls until initialization. Automatic clean replacements temporarily disable task/screener selectors; genuine input still invalidates replacement so drafts are preserved. Only explicit policy/scope conflicts or changed-settings payloads open policy recovery. Undo state and duplicate-name conflicts keep their own message. A successful clean queue reload or explicit policy reload clears resolved recovery guidance.
