# Task 1 backend workflow foundation

Status: implementation complete for foundation, policies, sources, reasons, queues and affected save guards. Source import staging/activation UI and coding evidence bodies/Trace actions remain the explicitly assigned following tasks. Validation is static syntax and self-review only; no automated tests, runtime imports, HTTP calls, or real databases were used. Running installation and research data were untouched.

## Files and commits

- `c609d9b` — additive storage, source helpers, revision-aware metadata-preserving catalogue sync, fulltext Include-or-Maybe eligibility, fulltext Maybe excluded from coding's initial fallback.
- `6e4aaf7` — routes and guards. Files: `custom_backend/review_workflow_api.py`, `main.py`, `workspace_undo_api.py`, `coding_consensus_api.py`.
- `fea009b` — point queries for work-item fetch and save eligibility; queue catalogue query selects compact columns without all abstracts/raw payloads.
- Storage files: `coscreen/review_workflow.py`, `coscreen/fulltext.py`, `coscreen/coding.py`, `custom_backend/shared_corpus.py`.
- This report is an ignored `.superpowers` task artifact, force-added specifically as the requested deliverable. The preexisting untracked approved plan is left for the parent to commit.

## Runtime contracts

Registered as `app.state.review_workflow` by `register_review_workflow_routes(app, require_user, task_lookup, screener_db, data_dir, visible)`.

- `context(task_id, user) -> WorkflowContext`: fields `info`, `user`, `owner`, `owner_db`, `actor_db`, `can_manage`, `task_kind`, `collaboration_db`. Ownership comes only from the existing collaboration owner record. Membership uses injected visibility logic, while actor ID/name always come from authenticated user. Verified task databases use injected screener DB resolution. Legacy owner-unset contexts resolve only authenticated actor DB without initialization/backup. No client path or owner-name parameters exist.
- `lock(task_id) -> RLock`: one reentrant lock per task, protected by a lock-map mutex.
- `write_guard(task_id, user, stage, key=None, expected_version=None, *, canonical_only=False)` context manager yields fresh context. It acquires RLock, rechecks visibility/owner, reads current policy, checks optional version, owner-only management when key absent, syncs catalogue, then checks current active canonical key, authoritative per-key pending title candidates, stage eligibility, Paused and active source scope before yielding. The caller's actual save and commit run inside this lock.
- `canonical_only=True` is intentional only for existing shared coding rater-record POST. Inside the lock it additionally rechecks `coding:submit`; it preserves that endpoint's prior canonical owner article/field eligibility without requiring an invited coder to repeat screening. Ordinary own coding queue/single/batch saves keep actor decision eligibility. Task 3 must preserve this distinction for evidence submission checks.
- `check_maybe(ctx, stage, decision)`: separately rejects a newly submitted Maybe when disabled. Undo does not call this, so a historical Maybe can be restored if current ownership, scope, eligibility and existing downstream snapshot protection allow it.
- `settings(ctx)`: returns public settings shape. `notify(ctx, reason)` publishes only task ID/version/reason after caller commits, using existing BUS and current participant IDs.
- `sync_catalog(ctx)`: revision-aware owner catalogue sync for members, no decision/coding/evidence synchronization.
- `queue(task_id,user,stage,source_id=None,order='import',shuffle=False)`: compact stage queue, counts/progress, version, allowed sources and paused state. `work_item(task_id,user,stage,key)` returns full article and actor saved state, writable/reason/version; own historical article remains readable even if no longer in owner queue.
- `eligible(ctx,stage)`, `scoped(ctx,stage,rows,source_id=None)`, `allowed_sources(ctx,stage)`, `writable_reason(ctx,stage,key,canonical_only=False)` expose reusable eligibility/scope checks.
- `coding_complete(ctx,key,values,dimension_ids)` is the Task 3 completion hook. Foundation requires every configured field nonempty; an empty scheme is incomplete. Required evidence checking belongs in this hook in Task 3, and global existing coding_progress should be extended consistently there.
- `article_rows(db,compact=False)`, `article_row(db,key)`, `saved_rows(db,stage)` and `saved_item(db,stage,key)` are read-only helpers. Work-item reads and write eligibility use point queries instead of loading the full catalogue. `reason_db(ctx)` selects account-owned personal library versus shared task library. `bump_version(conn)` joins caller transaction. `latch_team(task_id,user)` discovers/persists non-owner grants under the task lock.

Policy ordering: version is checked against freshly read current settings; management ownership or key membership is checked; stage Paused and eligibility/source scope are checked before writes; Maybe is checked separately within the same guarded decision operation. Sources in inactive state are excluded regardless of All/Selected. Selected accepts valid nonempty source ID lists; inactive selections may yield an empty queue. Saved decisions remain literal unchanged strings.

Same-process guarantee: policy writes, decision writes, coding single/batch saves, shared coding submission, Undo and member grant/revoke changes serialize on the same RLock. SQLite's transaction protects each database commit. This does not make a lock spanning multiple DB files across worker processes. The existing task file lock is not replaced and was designed for task lifecycle ownership; deployments using multiple backend processes need a common policy/write coordination mechanism before claiming cross-process serialization.

## API contracts

All paths begin `/api/tasks/{task_id}` and retain existing authenticated membership checks.

- `GET /workflow-settings`: `{version,task_kind,can_manage,stages,evidence_required_field_ids,sources}`. Stage values: screen/fulltext `{show_maybe,mode,source_ids}`; coding `{mode,source_ids}`. Defaults `all`, `[]`, Maybe true. Modes are lowercase `all|selected|paused`. Legacy defaults are readable; owner controls unavailable.
- `PUT /workflow-settings`: body `{expected_version,stages,evidence_required_field_ids:[]}`; requires all three stages, valid sources, nonempty Selected and valid unchanged numeric coding dimension IDs. Owner only, version mismatch 409; saves policy then broadcasts `task-workflow-changed`.
- `GET /sources`: `{sources:[{id,name,active,reserved}],can_manage,version}`. `POST /sources` body `{name,active:true,expected_version?:int}` creates UUID. `PUT /sources/{source_id}` same body edits name/activation; reserved Unassigned cannot be modified. Duplicate names normalize NFKC/case/whitespace, 409. Writes return `{source,version}` and broadcast.
- `GET /reason-library?stage=screen|fulltext`: `{entries:[{id,text,stage}],can_manage,task_kind}`. `POST` body `{text,stage?}`, with optional stage query. `PUT /reason-library/{id}` body `{text}` changes only text and preserves stage. `DELETE` removes library entry only. Owner only. Text is trimmed nonempty, <=2000 chars, stage duplicates normalized NFKC/case/whitespace rejected. Returns `{id,deleted}`. Personal libraries persist by authenticated account across its personal tasks; shared tasks use owner DB. No decision text is edited.
- `GET /work-queue/{screen|fulltext|coding}?source_id=<id>&order=import|random`: `{queue,total,pending,scope_progress,overall_progress,policy_version,allowed_sources,paused}`. Compact item fields `key,zotero_key,title,authors,journal,year,doi,item_type`. Progress `{total,completed,pending}` counts unique canonical articles; overlap never doubles counts. Queues include saved eligible records for history/correction. Sidebar/global existing counts unchanged.
- `GET /work-items/{stage}/{key}`: `{article,saved,writable,reason,policy_version}`. Saved is decision record or dimension-value map; later task adds evidence. Full article includes abstract and parsed raw. Out-of-scope/history can be read, writes denied.
- `POST /work-queue/screen/shuffle?source_id=<id>` returns same queue response and reshuffles only remaining eligible pending keys in scope.
- Old decision, stage2, coding single, batch, shared coding record and Undo bodies add optional `expected_policy_version`. Omitted version still obeys current policy. Existing response keys remain. Adjudication/Kappa routes are unchanged.

## Storage/migration contracts for Tasks 2 and 3

`coscreen.review_workflow` contains `SCHEMA_VERSION=1`, `STAGES`, `UNASSIGNED='00000000-0000-0000-0000-000000000000'`, `migrate(db)`, `connect(db,read_only=False)`, `has_table(conn,name)`, `defaults()`, `normalized(text)`, `settings(db)`, `sources(db)`, `source_links(db)`, `associate_sources(db,keys,source_ids,conn=None)`, `bump_catalog_revision(db,conn=None)`, `catalog_revision(db)`, `random_order(db,actor_id,task_id,scope,keys,pending,shuffle=False)`.

Migration 1 tables:

- `workflow_migrations(version PRIMARY KEY,applied_at)` records numbered additive migration.
- `workflow_state(singleton=1,version,team_latched,catalog_revision,stages_json,evidence_json)`.
- `workflow_sources(id PRIMARY KEY,name,normalized_name UNIQUE,active,created_at)`.
- `workflow_article_sources(zotero_key,source_id REFERENCES workflow_sources(id),PRIMARY KEY(zotero_key,source_id))`; source-key index. Intentionally no FK into active articles. Sources overlap. Do not use this absence as permission to insert staged articles into articles.
- `workflow_reasons(id PRIMARY KEY,stage,text,normalized_text,UNIQUE(stage,normalized_text))`.
- `workflow_random_order(actor_id,task_id,scope,zotero_key,position,PRIMARY KEY(actor_id,task_id,scope,zotero_key))`.
- `workflow_catalog_sync(owner_identity PRIMARY KEY,revision)` in actor stores.
- Three owner article insert/update/delete triggers bump catalogue revision. Insert also adds Unassigned, subsequently removed on explicit provenance association. Existing unknown links backfill Unassigned without name/filename inference.

Existing DB first upgrade uses SQLite backup API to `{original DB filename}.pre-workflow-v1.bak` before workflow schema changes; backup is not overwritten. New account library DB (`{data_dir}/workflow_accounts/{verified numeric owner ID}.sqlite3`) needs no old-DB backup. Legacy read-only workflow reads create no DB, directory, schema or backup. No original tables or numeric field IDs are remapped/deleted.

Task 2 integration: keep all new staged records outside articles. Use its separate provenance/staging tables for non-active keys. On verified batch activation, acquire `write_guard(...,key=None)`; use owner's existing active articles import transaction then `associate_sources(owner_db,canonical_keys,source_ids,conn=that_transaction)` to make overlapping links atomic. Helper checks key is present and canonical; it never activates or inserts articles. Explicit links remove placeholder Unassigned only. Use `bump_version(conn)` if version should change for activation, and `notify(ctx,'import-activated')` after successful commit. Article triggers update catalogue revision automatically; `bump_catalog_revision` is available if an activation needs to signal a catalogue change without row DML.

Task 3 integration: migrate evidence via its own numbered additive schema, leaving field IDs stable; `workflow_state.evidence_json` already persists validated required field IDs. Add evidence to work-item saved view and guarded single/batch/shared writes; extend `coding_complete` plus global progress semantics. Trace capture must join actual evidence/value transactions; this task does not fabricate evidence rows or Trace events.

## Self-review and syntax evidence

Read back both new complete files and changed shared-corpus/Undo/consensus files; inspected main diffs and fulltext/coding eligibility changes. Reviewed migration transaction/backup, normalized duplicates, source overlap, authoritative pending title exclusions, no field remapping, legacy read effects, per-key scope checks, version/Maybe checks inside locks, persisted random positions, member removal latch and preserved shared submit eligibility. Removed unnecessary random writes to completed/unmodified journal entries. Point-query refactor preserves stage2-include global fallback gating and per-key fulltext Maybe exclusion; scanned final diff before committing.

Command (run from this worktree):

```text
python -c "import ast,pathlib; files=['coscreen/review_workflow.py','custom_backend/review_workflow_api.py','custom_backend/shared_corpus.py','custom_backend/main.py','custom_backend/workspace_undo_api.py','custom_backend/coding_consensus_api.py','coscreen/fulltext.py','coscreen/coding.py']; [(ast.parse(pathlib.Path(p).read_text(encoding='utf-8-sig'),filename=p), print(p+': AST OK')) for p in files]"
git diff --check
```

Result: all eight files AST OK; diff check exited successfully with no output. This proves Python syntax only; runtime/API behavior and SQLite migration execution are not claimed as tested.

## Boundaries/concerns

- Required evidence IDs can be configured/stored, while evidence content enforcement is intentionally Task 3. Do not present that whole feature complete until then.
- Legacy unknown-ownership GETs remain read-only. Their random GET/shuffle falls back to import ordering without creating a journal; persistent random requires verified owner migration. Workflow owner controls remain unavailable.
- Historical collaboration grants already deleted before this upgrade cannot be reconstructed. Existing current grants are discovered/latch persisted; invite and revoke hooks latch before the last grant disappears.
- Runtime queue scope/eligibility is authoritative from owner, actor work remains independent. Shared sync fills missing metadata and canonical duplicate/import-order fields, preserving nonempty reviewer researched metadata; no decision/coding/evidence edits. Queues read owner metadata directly.
- Account reason libraries are private when personal. Becoming shared switches to task library; personal entries are not automatically exposed to members.
- Policy conflict response is 409 with `detail:{code:'workflow_version_conflict',version}`. Scope/Paused/ineligible conflicts use the same detail code form; frontend needs preserve drafts and refresh policy/item after 409.
- No browser/runtime or migration verification was performed under the user's no-tests/no-real-data gate. Parent should review this foundation before Task 2.
