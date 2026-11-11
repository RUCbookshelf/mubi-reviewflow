# API reference — stable core subset

This document describes the stable core HTTP API of Co-Bookshelf / ReviewFlow, version **2.1.4** (the version in the repository `VERSION` file, also returned by `GET /api/runtime` as `version`). The contracts below are hand-written in the same style as the contract tables in [WORKFLOW_CONTROLS.md](WORKFLOW_CONTROLS.md); the machine-readable OpenAPI 3.1 document for every registered route lives in [openapi.json](openapi.json).

Two scopes, stated plainly:

- **This page covers a stable core subset only**: auth, task lifecycle, import (including the import review workspace), screening decisions, full text, exports, and active-learning (AL) ranking. Contracts here are intended to remain stable within the 2.1.x line; breaking changes are called out in [RELEASE_NOTES.md](../RELEASE_NOTES.md).
- **Experimental endpoints are not listed here.** The analysis (`analysis/…`), coding bench (`coding/…`), research trace (`trace/…`), report evidence, AI suggestion (`ai-suggest/…`, `ai/…`), living review, PRISMA form, local-storage, event stream (`/api/events`), merge, protocol, extraction, screening-arbitration and collaboration endpoints exist in the application and appear in `openapi.json`, but they are experimental or workflow-internal and may change without notice. The collaboration/workflow-controls family (`workflow-settings`, `sources`, `reason-library`, `work-queue`, `work-items`, `undo`, `collaboration`, `team-progress`) is documented separately in [WORKFLOW_CONTROLS.md](WORKFLOW_CONTROLS.md).

Every method-and-path token on this page is verified against the live route table by `build/tests/test_w6_openapi_export.py`; a documented endpoint that is removed or renamed fails that test, as does a stable-core endpoint missing from the tables below. New endpoints added by feature lanes under the same prefixes (for example the full-text fetch experiments) are experimental from birth and are intentionally not required here until promoted.

### Known limits of `openapi.json` (read this before code generation)

`openapi.json` is a **complete route snapshot, not a complete contract**. Three limits are deliberate and measured, and clients generated from it will be wrong if they are ignored:

- **No response models anywhere.** No route in the repository declares a `response_model` (count: **0** — routes are plain `def` handlers returning `dict`/`Response`, verified by AST walk over `custom_backend/` and `coscreen/`). Of the 371 operations, 283 have a 2xx `application/json` schema of `{type: object, additionalProperties: true, title: "<route-derived title>"}` — a titled object with no declared fields. Generated clients therefore get an untyped map. **The field-level contract is the text in this document**, not the schema.
- **No `securitySchemes`.** `components` contains only `schemas`; 364 of 371 operations instead expose `authorization` as an ordinary header parameter. Generated clients will **not** attach a Bearer token automatically — inject `Authorization: Bearer <token>` yourself. Note also that many real controls are semantic, not schema-level: task ownership/collaboration checks, `expected_policy_version` conflicts, the `screener` query restriction and per-stage policy gates all surface as runtime 401/403/404/409.
- **Query vs. body placement follows FastAPI's signature rules, not intent.** A handler parameter without `Form(...)`/`Body(...)` is a query parameter even next to `File(...)` fields; `screener` on the import endpoints is exactly such a case (see the import tables). Always read the tables here rather than inferring from the schema.


## Runtime disclosure: the app does not serve its own API docs

The deployed application deliberately runs FastAPI with `openapi_url=None` (see the comment "产品化：不对外暴露接口文档" in `custom_backend/main.py`): `GET /openapi.json`, `/docs` and `/redoc` return **404** in every shipped build. `docs/openapi.json` is produced **offline** by [build/scripts/export_openapi.py](../build/scripts/export_openapi.py), which builds the app in a throwaway data directory and reads `app.openapi()` directly. Each run asserts that `openapi_url`, `docs_url` and `redoc_url` are all `None` **and** — through the real ASGI stack — that `GET /openapi.json`, `/docs` and `/redoc` answer 404, so the export can never silently relax the runtime posture: moving the docs to a different path fails the attribute check, and registering a same-name route by hand fails the probes.

To regenerate after changing routes:

```bash
python build/scripts/export_openapi.py          # writes docs/openapi.json
python build/scripts/export_openapi.py --check  # CI/maintenance: fail if the committed file is stale
```

**Do the regeneration last.** `docs/openapi.json` must be regenerated *after* every route change in the release lands, and the refresh then re-verified (`--check` or `pytest build/tests/test_w6_openapi_export.py`). Multiple feature lanes touch the API surface, so an export taken before the final merge is stale by definition; `test_w6_committed_openapi_json_matches_fresh_export` turns that staleness into a red gate rather than a silent contract drift.

## Conventions

- **Base URL.** The desktop app serves on `http://127.0.0.1:8613` by default (the port is a port, not an error code). Server deployments put uvicorn behind a TLS-terminating reverse proxy (nginx/Caddy); see the deployment note in `custom_backend/main.py`.
- **Authentication.** JWT (HS256, 24 h by default; 7 days with the 2.1.5 "remember me" option, revocable by any password change/reset) issued by register/login. Every endpoint **on this page** requires `Authorization: Bearer <token>`; the public surface is the three auth endpoints — register, login, and `POST /api/auth/reset` (the reset endpoint is itself protected by a one-time 15-minute recovery code obtained out-of-band, not a token) — plus `GET /api/health`, `GET /api/runtime` and `GET /api/app-update/check`. Invalid or missing tokens answer **401**. (Outside this page, the experimental SSE stream `/api/events` also accepts a one-time short-lived ticket query parameter, because browser `EventSource` cannot set headers.)
- **Task access.** Endpoints under a task path require the caller to be the task owner or a current collaborator; otherwise **403** (or **404** when the task does not exist). Legacy no-owner tasks follow the visibility rules described in the application help.
- **Validation and errors.** Pydantic rejects malformed bodies with **422**; business rule violations answer **400** or **409** with a human-readable message; unexpected faults return a generic 500 message only (full stack traces stay in server logs).
- **Optimistic concurrency.** Writes that mutate workflow state accept `expected_policy_version`; submitting a stale version answers **409** so the client can refresh. Import (and import-review) responses return the new `policy_version`; the decision and coding writes on this page accept the field but their responses (`{saved: true, progress}`) do not echo it.
- **Task locking.** Requests to a task path hold that task's lock while running; concurrent destructive operations (for example deleting a task during an import) answer **409** instead of corrupting data. A deletion in progress briefly makes the task list answer **503**.
- **Uploads.** File uploads are checked by extension whitelist, magic bytes and a 100 MB per-file cap; file names are sanitized (`custom_backend/security.py`). CSV/TSV exports escape formula-injection prefixes.
- **Rate limits.** Registration and login are limited to 5 attempts per minute per connecting peer (and per account for failed logins); exceeding them answers **429** with `Retry-After`.

## Health and runtime

| Method and path | Contract |
| --- | --- |
| `GET /api/health` | Public liveness probe. Returns `{"status": "ok"}` and nothing else — no user or system information. |
| `GET /api/runtime` | Version and deployment mode. Returns `version` (from `VERSION`, here 2.1.4), `mode` (`server` or `desktop`), `storage` (`server`/`local`), `restart_supported` (desktop only) and a per-process `instance_id`. |
| `GET /api/app-update/check` | Public. Desktop builds query the public stable GitHub Release and return `{supported, current_version, has_update, ...}` (cached for 15 minutes). Server deployments cannot self-update and answer `{supported: false, has_update: false}`. |

## Authentication

| Method and path | Contract |
| --- | --- |
| `POST /api/auth/register` | Body `{username (3–64 chars), password (8–128 chars)}`. Creates the account and returns `{token, user}`. Answers 400 for invalid input, duplicate usernames, and usernames colliding with the reserved collaboration store name (`collaboration`, `Collaboration`, …), which are rejected outright; 429 when the peer exceeds 5 registrations per minute. |
| `POST /api/auth/login` | Same body as register. Returns `{token, user, warnings}`; `warnings` explains a reserved-name collision that disables collaboration — reachable only for legacy accounts registered before that rule existed (new registrations are rejected). Wrong credentials answer 401; 5 failed attempts lock that account for one minute (429, correct password included) to slow guessing. |
| `GET /api/auth/me` | Returns `{user_id, username}` for the bearer token. |

## Tasks

| Method and path | Contract |
| --- | --- |
| `GET /api/tasks` | Lists tasks visible to the caller (owned or collaborated). Query `include_archived=true` to also list archived tasks. Returns `{tasks: [...]}`. During another window's task deletion the list may briefly answer 503; retry. |
| `POST /api/tasks` | Body `{name (1–200 chars), description (0–2000 chars)}`. Creates a task owned by the caller, returns 201 with the task payload (including `task_id`). Duplicate names answer 409, as does the reserved collaboration store name (`collaboration`). |
| `PUT /api/tasks/{task_id}` | Owner only. Body `{name?, description?, archived?}` — only supplied fields change. Rename collisions answer 409. Returns the updated task payload. |
| `DELETE /api/tasks/{task_id}` | Owner only. Moves the task directory into the store's `.trash` area and returns `{deleted: true, trash}`. Answers 409 while other write requests are running or readers have not drained; the task is left intact in that case. |
| `GET /api/tasks/{task_id}/progress` | Screening progress per stage: `{stage1, stage2, coding}` counters. |
| `GET /api/tasks/{task_id}/articles` | Lists the catalogue. Query `include_duplicates=true` adds duplicate records; `screener` (optional, own username or omitted — any other value answers 403) selects which screener database to read, defaulting to the caller's. Returns `{articles, total, title_review_pending, title_review_progress}`. |
## Import

Imports write to the verified owner catalogue only (non-owner screeners receive 403). Supported bibliographic text formats are parsed server-side (RIS/CSV and friends); original bytes are archived per file and every record keeps its source provenance.

| Method and path | Contract |
| --- | --- |
| `POST /api/tasks/{task_id}/import` | `multipart/form-data` with one `file` (extension whitelist, magic bytes, ≤100 MB); optional **multipart fields** `job_id`, `source_id`. `screener` is a **query parameter**, not a form field: it must be omitted or equal the caller's own username, because the authentication layer rejects any other value with 403 ("只能访问自己的筛选员数据库"). Returns the import result: `staged` flag, `batch_id`, `import_id`, `policy_version`, `progress`, and `parse_warnings` listing per-file truncation/split/encoding notes when present. A protected (research-active) task always stages for review. 409 `import_catalogue_changed` asks the client to retry when the catalogue changed during matching. |
| `POST /api/tasks/{task_id}/import-batch` | Same contract with 1–20 `files` and `source_ids` (JSON array aligned with `files`, or `'auto'`); `screener` is likewise a query parameter limited to the caller. |
| `GET /api/tasks/{task_id}/import-progress/{job_id}` | Progress for a `job_id` supplied at upload: `{phase: starting\|parse\|fuzzy\|save\|complete\|error, percent, ...}`. Progress files are user-scoped; another user's `job_id` answers 404. |
| `GET /api/tasks/{task_id}/import-history` | Import history entries (per task) with per-file stats. |
| `GET /api/tasks/{task_id}/import-history/{batch_id}/files/{file_id}` | Downloads the archived original import file bytes. |

### Import review workspace (staged records)

Batches imported into a protected task wait in a review workspace; activation is an explicit review action, not a side effect of import.

| Method and path | Contract |
| --- | --- |
| `GET /api/tasks/{task_id}/import-review-summary` | Task-wide checklist counts: `source_records`, `pending_records` (staged), `metadata_review_count`, `pending_candidate_groups`, `can_manage`. Reimports do not double count. |
| `GET /api/tasks/{task_id}/import-batches` | Paged batch list (`limit` 1–200, `offset`): `{batches, total, next_offset, can_manage}`. |
| `GET /api/tasks/{task_id}/import-batches/{batch_id}` | Batch detail with records for one view (`view=all\|attention\|completed`, `limit`, `offset`): batch summary, `records`, `review_counts`, `policy_version`, `undo` availability and `legacy_undo_allowed`. Unknown batch answers 404. |
| `GET /api/tasks/{task_id}/import-batches/{batch_id}/candidates` | Duplicate candidate groups for the batch (`view`, paging as above): `{candidates, total, next_offset}`. |
| `POST /api/tasks/{task_id}/import-batches/{batch_id}/candidates/{review_id}` | Body `{status: duplicate\|not_duplicate\|undecided, keeper_key?, keeper_record_id?, expected_policy_version?}`. Records one candidate decision and returns the refreshed batch summary with `review_updates` and undo state. |
| `POST /api/tasks/{task_id}/import-batches/{batch_id}/metadata/{record_id}` | Body `{action: keep_existing\|accept_incoming, expected_policy_version?}`. Resolves one metadata proposal. |
| `POST /api/tasks/{task_id}/import-batches/{batch_id}/bulk-review` | Body `{actions: [{kind: candidate\|metadata, id, keeper_key?, keeper_record_id?}, ...] (1–100), expected_policy_version?}`. Applies a transactional bulk confirmation of review decisions. |
| `POST /api/tasks/{task_id}/import-batches/{batch_id}/activate` | Body `{expected_policy_version?}`. Promotes the batch's resolved staged records into the screening catalogue; unresolved records keep waiting and repeated calls add no work (idempotent). |
| `POST /api/tasks/{task_id}/import-batches/{batch_id}/undo` | Body `{expected_policy_version?}`. Reverts the most recent review action group for the batch (candidate/metadata/bulk), returning the batch to its prior state. |
| `GET /api/tasks/{task_id}/import-batches/{batch_id}/export` | CSV export of the batch's source records (provenance columns, formula-injection escaped). |

### Local Zotero 7 library (loopback only, opt-in per click)

These endpoints read the Zotero 7 local API that runs **on the same machine** at the fixed loopback address `http://127.0.0.1:23119`. Nothing is fetched at startup, on a timer or in the background: each call happens only because the user clicked a button in the Import page. Requests always go directly to `127.0.0.1` — proxy environment variables (`HTTP_PROXY`/`http_proxy`) are deliberately ignored, redirects are refused, and the only thing that ever leaves the process is a request to that loopback address (the Zotero cloud is never contacted).

| Method and path | Contract |
| --- | --- |
| `POST /api/tasks/{task_id}/zotero-local/test` | Read-only reachability probe. Returns `{ok: true, endpoint, latency_ms}`. Sends nothing but one collection-list request to the loopback API. |
| `GET /api/tasks/{task_id}/zotero-local/collections` | Collections in the local library: `{collections: [{key, name, parent, num_items}], count, endpoint}`. `parent` is the parent collection key (`null` for top level); `num_items` is `null` when Zotero did not report it. |
| `POST /api/tasks/{task_id}/zotero-local/import` | Body `{collection_keys: [1–20 keys]}`; `screener` is a **query parameter** limited to the caller, exactly as for `/import`. Fetches the top-level items of each selected collection (notes and attachments skipped, duplicates across collections collapsed), converts them to one synthesized RIS file per collection and injects them through the **protected import pipeline** — so deduplication, staging, metadata review, activation and provenance behave identically to a hand-uploaded RIS. Returns the normal import result plus `zotero: {endpoint, total_items, collections: [{key, name, items}]}`. |

Failure contract: the loopback API being unreachable, slow, protocol-garbled (for example another program squatting on port 23119), returning non-JSON, redirecting, or being larger than 8 MiB all answer **409** with `{code, message, endpoint}` — never 500. Codes: `zotero_unreachable`, `zotero_timeout`, `zotero_redirect`, `zotero_bad_response`, `zotero_http_error`, `zotero_response_too_large`, `zotero_collection_not_found`, `zotero_too_many_items`, `zotero_bad_collection_key`, `zotero_bad_path`. An empty selection answers 422 `zotero_no_items`. Paging reads up to 5 000 items per collection (50 pages × 100). Reaching that boundary is fine: after the last full page the server probes one extra item, so a collection of **exactly 5 000** items imports normally. Only when the probe returns something — meaning there really is more — does the call answer `zotero_too_many_items`, and then the remedy is to import a subcollection or split the selection; items are never silently truncated. Non-participants receive 403.

### Title-duplicate review (legacy companion flow)

| Method and path | Contract |
| --- | --- |
| `GET /api/tasks/{task_id}/title-duplicate-reviews` | Paged title-duplicate groups (`view=attention\|all`, `limit` 1–100, `offset`): `{groups, total, offset, progress, undo_available}`. |
| `POST /api/tasks/{task_id}/title-duplicate-reviews/{review_id}` | Body `{decision: duplicate\|not_duplicate\|undecided, keeper_key}`. Records the human verdict; screening a record with a pending title review answers 409 until resolved. |
| `POST /api/tasks/{task_id}/title-duplicate-undo` | Reverts the most recent title-duplicate verdict by the caller; 404 when nothing is undoable. |
| `GET /api/tasks/{task_id}/title-duplicate-reviews/export` | CSV export of all title-duplicate groups and members (formula-injection escaped). |

## Decisions (screening stages)

| Method and path | Contract |
| --- | --- |
| `GET /api/tasks/{task_id}/decisions` | Stage-1 (title/abstract) decisions with titles: `{decisions, total}`. Optional `screener` query parameter (own username or omitted; another value answers 403) selects which screener database is read. |
| `POST /api/tasks/{task_id}/decisions` | Body `{zotero_key (1–300), decision: include\|exclude\|maybe, reason (≤2000), notes (≤8000), tags (≤500), expected_policy_version?}`. Saves a stage-1 decision under the stage's workflow policy (hiding Maybe rejects new Maybe saves with 409). Returns `{saved: true, progress}`. Unknown article key answers 400; a pending title review answers 409. |
| `GET /api/tasks/{task_id}/stage2/decisions` | Full-text (stage-2) decisions, same shape as stage 1. |
| `POST /api/tasks/{task_id}/stage2/decisions` | Same body as stage-1 decisions; enforced against the full-text stage policy. |
| `GET /api/tasks/{task_id}/stage2/queue` | Full-text queue for double-blind mode: records the caller included at stage 1. When the caller's AL strategy is `cluster`, the queue order follows topic-cluster ordering (payload unchanged); records already decided at stage 2 stay in their import positions. |

Queues, stage policies, source scoping and per-stage undo are governed by the workflow controls family documented in [WORKFLOW_CONTROLS.md](WORKFLOW_CONTROLS.md).

## Full text

| Method and path | Contract |
| --- | --- |
| `GET /api/tasks/{task_id}/fulltext/{zotero_key}` | Serves the stored PDF inline (`application/pdf`, UTF-8 encoded filename). 404 when no PDF was uploaded or the file moved; 400 for malformed keys. |
| `POST /api/tasks/{task_id}/fulltext/{zotero_key}/upload` | `multipart/form-data` `file`; PDF magic bytes and ≤100 MB enforced. Returns `{zotero_key, filename, sha256, n_pages, added_at}`. Unknown article key answers 400. |
| `GET /api/tasks/{task_id}/highlights/{zotero_key}` | Highlight events with rectangles for one article: `{highlights}`. |
| `POST /api/tasks/{task_id}/highlights` | Body `{zotero_key, events (1–500)}`; every `rects` entry must have exactly 4 float components (`[x0, y0, x1, y1]`) — other lengths answer 400 rather than being stored silently. Returns `{saved, highlights}`. |
| `DELETE /api/tasks/{task_id}/highlights/{highlight_id}` | Deletes one highlight; 404 when already gone. |

## Exports

All exports require authentication and stream a file attachment; CSV cells that could be interpreted as spreadsheet formulas are escaped.

| Method and path | Contract |
| --- | --- |
| `GET /api/tasks/{task_id}/export/decisions` | Stage-1 decision list as `decisions.csv`. |
| `GET /api/tasks/{task_id}/export/stage2` | Stage-2 decision list as `decisions2.csv`. |
| `GET /api/tasks/{task_id}/export/matrix` | Coding matrix (articles × coding dimensions) as `coding_matrix.csv`. |
| `GET /api/tasks/{task_id}/export/ris` | Articles as RIS. Query `decision=include\|exclude\|maybe` (default include) and `source=stage1\|stage2` (default stage1). |
| `GET /api/tasks/{task_id}/export/highlights` | All highlights as Markdown (`highlights.md`). |

## Active learning (AL ranking)

AL trains on the current reviewer's include/exclude labels and only reorders records still permitted by the stage policy. Model profiles are explicit user choices (`legacy`, `tuned`, `cjk`, `svm`).

| Method and path | Contract |
| --- | --- |
| `GET /api/tasks/{task_id}/al/rank` | Active-learning ordering for the caller's pending queue: `{available, order: [{zotero_key, score}], n_labeled, min_labeled, strategy, seed, reorder_interval, stop_suggestion}`. When too few labels exist, `available` is false with `reason: "not_enough_labels"` and an empty order. The `svm`/`cjk` profiles with single-class labels answer 400 (label more records) instead of failing internally. With `strategy: "cluster"` (topic-cluster ordering) nothing is trained and no model is required: `available` is always true, `score` is `null` for every entry (cluster order derives no inclusion probability), and lexically similar titles/abstracts are placed next to each other while decided records keep their import positions; see `docs/TOPIC_CLUSTER_ORDER.md`. |
| `GET /api/tasks/{task_id}/al/status` | Lightweight status: `{available, n_labeled, strategy, last_streak, suggest_stop}`. Under `strategy: "cluster"` it reports `available: true` regardless of label count (nothing is trained). |
| `GET /api/tasks/{task_id}/al/settings` | Current AL settings for the caller in this task. |
| `PUT /api/tasks/{task_id}/al/settings` | Partial update (`strategy: max\|uncertainty\|mixed\|cluster`, `seed`, `stop_threshold`, `reorder_interval`; `min_labeled` is a legacy no-op). Unsupplied fields keep their stored values; returns the merged settings. Existing stored values need no migration; `cluster` requires no model profile or API key. |
| `GET /api/tasks/{task_id}/al/profiles` | Available model profiles with labels/descriptions, the default, and the task's current explicit selection. |
| `PUT /api/tasks/{task_id}/al/profile` | Body `{profile: legacy\|tuned\|cjk\|svm}`. Sets the task's model profile; existing labels are untouched and the next rerank uses it. Read-only (degraded) tasks answer 409. |
| `GET /api/tasks/{task_id}/al/shared-ranking` | Double-blind shared ranking: merges all screener databases of the task into one model. Query `top` (1–10000, default full order); `screener` must be the caller's own username. Requires adjudicator rights (403 otherwise) and paused stages (409 during independent screening). Response matches `/al/rank` plus `al_profile`, `n_screeners`, `screeners`. |
| `GET /api/tasks/{task_id}/al/stop-rules` | Static registry of the supported stopping rules. |
| `GET /api/tasks/{task_id}/al/stop-certificate/check` | Read-only recall-certificate check. Query `comparison`, `outcome`, `timepoint` are echoed as question context only. Reports whether a look point (50/70/85/95% screened) is reached, the required random sample size and the tolerated remaining relevant count; it never samples or decides for the user. |

## Not listed here (experimental or workflow-internal)

Route families that exist in `openapi.json` but are deliberately outside this stable subset: statistical analysis and diagnostics, the coding bench, consensus and agreement, research trace, report evidence, AI suggestions and keys, living-review retrieval, PRISMA form generation, local storage migration, server-sent events, decision-merge, protocol, extraction templates, screening arbitration and rounds — including the two server-loaded merge endpoints added in 2.1.4, `GET /api/tasks/{task_id}/screening-arbitrations/server-merge` and `POST /api/tasks/{task_id}/screening-arbitrations/server-save`, the owner-only screening switch `PUT /api/tasks/{task_id}/collaboration/members/{target_user_id}/screening`, and the blind-arbitration session family added in 2.1.5 (`GET …/screening-arbitrations/session`, `POST …/session/item`, `GET …/session/reveal`, `POST …/session/finish`) — the critical-appraisal family added in 2.1.5 (`GET …/appraisal/tools`, `GET …/appraisal/recommendation`, `GET/PUT …/appraisal/{article_key}`), the abstract keyword-highlight family added in 2.1.5 (`GET/PUT …/highlight/sets`, `POST …/highlight/compute`), evidence map, and the workflow-controls/collaboration family (see [WORKFLOW_CONTROLS.md](WORKFLOW_CONTROLS.md)). Treat their contracts as unstable: consult `openapi.json` for their current shapes, and expect changes within minor releases.

## Consistency nail

`build/tests/test_w6_openapi_export.py` (pytest) locks the claims of this page to the implementation:

1. every `METHOD /api/...` token written on this page must exist in the live route table (removed or renamed endpoints go red);
2. the stable core subset — the 55 baseline routes of the families above (including the five public-surface routes such as `GET /api/health`), pinned as an explicit list in the test — must still exist and be tabled here (a removed baseline route or a missing table row goes red);
3. `build/scripts/export_openapi.py` run twice in separate processes must produce byte-identical output, and each run re-proves the runtime posture: `openapi_url`/`docs_url`/`redoc_url` all `None` and 404 for `/openapi.json`, `/docs`, `/redoc` — the guard itself is unit-tested against fake apps that enable any one of those switches or hand-register the route;
4. the committed `docs/openapi.json` must equal a fresh offline export (a route added or removed anywhere in the app without regenerating goes red; the fix is one command: `python build/scripts/export_openapi.py`). The `--check` CLI mode reports `fresh`/`stale`/`missing`/`invalid` with controlled exit codes and a JSON summary — no tracebacks, including for structurally malformed files such as `{"paths": "oops"}`;
5. the three claims of the **Known limits** section above are pinned to the live schema **as exact counts, not lower bounds**: the operation total, the number of placeholder 2xx responses and the number of operations carrying an `authorization` header are read out of this page and must equal what a fresh export computes (change any of them here and the test fails until the numbers match the schema again); plus no `securitySchemes`, and `screener` present as a query parameter on the import endpoint.
