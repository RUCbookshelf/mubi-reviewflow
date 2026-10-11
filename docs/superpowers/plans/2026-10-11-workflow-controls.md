# ReviewFlow workflow controls implementation ledger and plan

Approved by the user on 2026-10-11. Base: 56cf864. Branch: codex/workflow-controls.

## Binding constraints

- Work only in this isolated worktree. Do not change E:/mubi-reviewflow, E:/ReviewFlow, the running installation, or real research data. Do not merge/push/release.
- Preserve all saved decisions, reviewer independence, article identities, coding values, notes, images, import order, and historical trace records.
- Automated tests are not authorized. Do not add or run automated tests. Python/JavaScript syntax checks, diff review, disposable-data startup and manual browser acceptance checks are authorized.
- Use additive, numbered, idempotent migrations with backup before upgrade; uncertain source attribution stays Unassigned and unverifiable task ownership must never be invented.
- All ten languages: zh-CN, en, fr, ru, es, ja, pt, de, sr, ko. Preserve product logo, sidebar, reader layout, existing control IDs and handlers, and member menus.
- No Remote Operation, Kappa/adjudication redesign, installer replacement, GitHub publication, general application rewrite, or unrequested behavior changes.

## Deliverables in dependency order

1. Backend foundation: versioned owner policies (two Maybe switches; All/Selected/Paused per screen/fulltext/coding); stable sources; per-actor source queues/counts; persistent random order; personal/team exclusion reasons; write guards on existing routes; initial Include and Maybe advance to full text. Existing coding eligibility otherwise preserved. Shared catalogue synced without touching reviewer results. SSE policy/source notifications with polling fallback.
2. Protected imports: source assignment per file; initial import compatibility; additional imports after saved work staged outside active articles; exact matches link existing; metadata differences held for explicit owner review; title/fuzzy candidates only block affected new work; owner activates confirmed records; idempotent same-file/same-source; canonical existing keys and worked records preserved.
3. Coding evidence and Trace backend: stable field mapping without numeric-ID assumptions; note/image authorization and PNG/JPEG/WebP 10 MB validation; staged uploads; atomic form save values/notes/attachments and one Undo snapshot; evidence optional unless owner requires selected dimensions; incomplete evidence draft not complete; Trace capabilities and safe current-state reopening; last-save Undo remains guarded.
4. Frontend integration: real product implementation of six Home tabs (Overview, Tasks & team, Screening, Machine Learning, Coding, Appearance), reason libraries/custom-save shortcut, Maybe/source controls, Import/Machine Learning/Random, evidence UI and Save/Discard/Stay guards, staged import review/activation and Trace menus. Preserve unsaved drafts/stale-response guards. Elastic leading 360 ms, trailing delay 100 ms, total about 460 ms using left/width, reduced motion and responsive recalculation.
5. Localization/review/preview: ten languages, cache-versioned modules, static checks and whole-branch review, migration/rollback documentation, isolated preview with disposable data.

## Confirmed decisions

- Personal reason libraries reused across account-owned personal tasks; shared task reasons owner-managed; historical reason strings never rewritten.
- Maybe defaults enabled for both screening stages; no coding Maybe. Hiding never converts/removes historical decisions; keyboard and backend both enforce.
- A shared article is counted once overall; per-source counts may overlap. Scope selection never resets saved work.
- ML trains only on the current reviewer's Include/Exclude labels; rank within current allowed pending scope. Random persists per actor/task/scope and new eligible keys append; explicit shuffle touches pending only.
- Notes per article/dimension, text and images. Evidence required accepts either text or an image. Save incomplete evidence as draft; cannot claim completed/submitted coding.
- Trace menus View change / Open article to correct / Undo this save. Only latest eligible actor+stage save can be undone. Imported, baseline, older/unlinked events cannot overwrite current data. Out-of-scope linked article opens read-only.
- New additional imports remain staged until owner activation. Staged additions cannot globally stop existing screening. Existing metadata never silently overwritten.
- Defaults compatible for legacy data; live owner changes reach clean forms via SSE/30-second visible-page polling, dirty forms keep drafts and show notice. Recheck membership/policy inside write guard before commit.

## Acceptance checklist

- [ ] Reasons CRUD/select/reuse preserve historical text.
- [ ] Maybe controls enforce UI/shortcut/backend and initial Maybe progresses unchanged.
- [ ] Owner scopes enforce all stages, member management denied, paused read-only.
- [ ] Multi-source articles deduplicated overall, same result across views.
- [ ] Staged imports preserve earlier work and do not interrupt it.
- [ ] Activation respects scope; unresolved candidates remain unavailable; reimports idempotent.
- [ ] Random persists/appends/shuffles only pending.
- [ ] Evidence drafts/requirements/images/atomic Undo preserve values and previous evidence.
- [ ] Trace correction navigation has no implicit save and safe last-save Undo.
- [ ] Refresh/draft protection prevents stale task/page responses and data loss.
- [ ] Migrations backup/idempotence/provenance uncertainty/rollback documented.
- [ ] Real UI keyboard/responsive/dark/multilingual/reduced-motion checks.

## Progress

- Setup complete: managed isolated worktree, branch created, main checkout unchanged.
- Tasks 1–3 backend and Task 4 frontend implementation/review fixes are committed locally through `de878a1`. This is implementation status, not blanket runtime acceptance.
- Task 5 delivered 188 complete workflow vocabulary rows; final repair adds two rows (190 current rows) in Chinese, English, French, Russian, Spanish, Japanese, Portuguese, German, Serbian and Korean; 22 existing Machine Learning display rows updated in their existing catalogs. Language autonyms and authored content remain literal.
- Task 5 static checks: 16 branch-changed Python files parsed as AST; 25 standalone scripts (all changed scripts plus every language pack) passed Node syntax checking; 2 inline scripts compiled without execution. At that historical gate, scripts used `20261012-workflow3` and the later stylesheet used `20261012-workflow4`; the final repair wave supersedes both with common `20261012-workflow5`. No automated tests or runtime calls were run by the delivery implementer.
- Migration/API/private-image/packaging/maintenance rollback documentation is in [WORKFLOW_CONTROLS.md](../../WORKFLOW_CONTROLS.md). Migration execution and rollback-on-copy acceptance remain pending.
- Controller reported the Task 4 suspected missing Trace actions resolved after refreshing stale frontend cache; the current event-detail response exposed authorized canonical evidence actions. This observation does not validate all Task 5 assets.
- Final repair wave resolves the rejected task-selector render invalidation in source; focused final-asset manual validation remains pending.
- Controller-observed Trace drawer mouse menu clipping received a scoped CSS correction after localization commit `016fdb3`: left anchor below the toggle, bounded drawer width, wrapping labels. That historical stylesheet was `20261012-workflow4` and scripts were `20261012-workflow3`; the final common version is `20261012-workflow5`. Controller subsequently verified the mouse menu within the drawer.
- Controller manual subset evidence: keyboard Trace correction/current private note and latest Undo restored the prior blank-note gap without changing coding/screening values; invalid PNG rejection cleared busy state; Batch B cumulative counts and Database D exact-match source activation/reimport preserved the seven-article catalogue and existing Maybe history. These fictional-data observations are bounded examples, detailed in WORKFLOW_CONTROLS.md; two nonexistent-fixture upload attempts are excluded. They do not mark every functional acceptance box complete.

## Delivery checks completed; manual acceptance pending

- [x] Complete ten-language workflow dictionary, placeholder parity, runtime-call vocabulary review and Machine Learning display terminology.
- [x] Preserve source names, reasons, article metadata, notes, quotes and raw payloads as authored content.
- [x] Synchronize cache versions for every branch-modified external script/style, including import guide and import history.
- [x] Document exact versioned API payloads, numbered migrations/backups, isolated launch, actor-private images and non-destructive maintenance rollback.
- [x] Inspect packaging inclusion paths for new modules/assets; no package build claimed.
- [x] Perform source-only Python/JavaScript/inline syntax and diff checks; no automated tests.
- [ ] Controller acceptance of final assets in all ten languages, keyboard/responsive/dark/reduced-motion modes and all workflow scenarios above.
- [ ] Disposable-data migration/idempotence/restore and packaged launch acceptance as separately authorized.


## Final review repair wave (2026-10-12)

All nine source findings I1/I2/M1–M7 have minimal repairs: explicit owner-protected Living Review routing; shared policy readiness and stale queue rejection; authorized current Trace field targeting; reason-specific conflicts and cleared recovery; disabled context selectors during automatic replacement; canonical evidence source narrowing/progress; accurate seven-day staged-upload cleanup; Serbian phrase correction; common workflow5 asset references. The Living Review alternative deliberately requires the original export/file through normal Import review and a separate analysis run; retained searches/history are preserved.

The final implementation and source evidence are described in [WORKFLOW_VALIDATION.md](../../WORKFLOW_VALIDATION.md). The unchecked acceptance items above remain broad combinations, not missing implementation claims. Bounded manual examples are recorded there and in the controller's manual-acceptance ledger. No automated tests were authorized, added or run. Final-asset focused manual checks, restore-on-copy/idempotence, true phone width and packaged launch remain unverified. No merge, push, release, installation or real-data work is part of this delivery.
