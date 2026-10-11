# Workflow controls: delivery and validation record

Scope: isolated `codex/workflow-controls`, based on `56cf864`; final review baseline `a329838`. This record describes source delivery and bounded controller observations. It does not certify every runtime combination, an installer, or a production rollout. No automated tests were authorized, added or run.

## Delivered behavior and changed-file groups

| Area | Delivered behavior | Source group |
| --- | --- | --- |
| Policies and queues | Owner All/Selected/Paused and Maybe policies, source attribution, actor-specific queues/progress, persistent random order, scoped ML, reusable reasons with historical text retention | `coscreen/review_workflow.py`, `custom_backend/review_workflow_api.py`, existing decision/team/ML integrations |
| Import preservation | Original receipts/provenance, protected additional imports, explicit candidate/metadata review and activation; existing research remains available | `coscreen/review_imports.py`, `custom_backend/review_imports_api.py`, import/history/storage integrations |
| Living Review boundary | Both old incremental entry APIs authorize owners and return `protected_import_required`; both UI actions explain and route to normal Import review. Users export/select the original file there. Existing search/history/analysis remain intact; analysis is run separately after activation | `custom_backend/main.py`, `custom_frontend/living_review.js` |
| Private evidence | Optional/required notes and images, validation/staging, current field mapping, atomic form and Undo snapshot; shared evidence source selector and evidence-only scope progress | `coscreen/coding_evidence.py`, coding/Undo modules, `custom_backend/workspace_undo_api.py`, `workflow_evidence.js` |
| Trace | Authorized current article reopening, latest-save guarded Undo; current field target resolved from actor lifecycle and semantic definition, with owner mapping required for canonical evidence | Trace API/runtime, `research_trace.js`, `workflow_controls.js` |
| UI and localization | Six Home tabs, source controls, Save/Discard/Stay, shared policy-read readiness, stale response protection, context clearing, explicit conflict guidance, 190 ten-language workflow rows | `index.html`, workflow modules, locale files, workspace/reader integrations |
| Cache and packaging | Common `20261012-workflow5` on every branch-modified external script/style; source packaging includes new modules and Pillow | `index.html`, packaging manifests and dependency files |

The legacy `coscreen.living_review` library still exposes incremental functions for external Python consumers. Repository source inspection found no remaining production application caller or scheduled entry path after replacing the two HTTP call chains. This release does not redesign that standalone library or add automatic post-activation Living Review analysis.

## Final review dispositions

| Finding | Source repair | Focused manual follow-through |
| --- | --- | --- |
| I1 | Owner-guarded explicit block/routing before file read, PubMed fetch or legacy write; original upload goes through normal source assignment/review | Owner and member direct legacy requests; UI routing; original-file receipt and unchanged historical metadata after protected staging |
| I2 | Per-scope shared policy promise; events/mutations request a subsequent fresh read; queue validates task/actor/ticket and policy version, with bounded refresh retry; old presentation cleared during task transition | Repeated task-card and selector switches with delayed/overlapping policy reads, each stage and canonical evidence |
| M1 | Server `actions.current_field` requires own current unretired lifecycle and matching definition; canonical evidence requires valid owner mapping; editor verifies the current definition | Personal/canonical current field, different owner/member dimension IDs, renamed/deleted/recreated or unmapped field |
| M2 | Recovery only for explicit policy/scope conflict codes or settings payload; clean successful queue/policy reload clears notice | Downstream Undo refusal and duplicate source/reason conflicts show no policy remedy; dirty policy conflict retains draft; Discard plus reload clears notice |
| M3 | Task/screener controls disabled during clean replacement; rejected selector events do not count as draft edits; genuine edits still invalidate replacement | Attempt task selector during automatic reload; reader completes and dirty input is never overwritten |
| M4 | Canonical endpoint accepts authorized `source_id`, returns allowed sources and scope/overall evidence requirement counts; personal coding header is not replaced by evidence counts | Coding-only member narrows multiple sources, observes overlap/deduplicated total, Paused/revoked access, private values unchanged |
| M5 | Upload-triggered cleanup of unattached staging older than seven days documented separately from attached/detached Undo binaries | Retention/cleanup remains unexecuted in this wave |
| M6 | Grammatical Serbian blind-mode phrase | Native-language review remains separate |
| M7 | All current cache claims use workflow5; prior workflow3/workflow4 claims explicitly historical | Hard refresh and inspect final asset requests |

## Source gates

The final repair implementer ran source-only Python `ast.parse` over the 16 Python files changed since `56cf864`; all parsed. Node `--check` passed for 25 standalone scripts (changed files plus all language packs) and both extracted inline scripts from `index.html`; neither inline body nor application code was executed. `git diff --check` passed. Final small source adjustments received syntax/diff rechecking before commit.

The existing source audit expectations were updated to 190 rows and common workflow5. The final repair did not execute application imports, automated tests, databases/migrations, HTTP, browser automation, preview processes, package builds, main-checkout operations or installed-client operations. Static syntax establishes parseability only. The controller owns the fictional-data preview at loopback port 18738 and performs separate manual acceptance.

## Bounded manual evidence

The controller's detailed session ledger is [manual-acceptance.md](../.superpowers/sdd/2026-10-11-workflow-controls/manual-acceptance.md). It is local review scratch rather than a packaged acceptance certificate. This discoverable document preserves the bounds even if that scratch is absent in a later checkout.

Before the final repair wave, the controller recorded these particular fictional-data examples:

- Include/Maybe progression and counts; a final personal example retained initial 2/7 (Maybe 1, Exclude 1), full-text 1/1 Include, and coding value 42 with an evidence gap. These are separate stage counts, not whole-feature acceptance.
- Source C/D overlap retained a seven-article canonical catalogue and the same saved Maybe history. Exact-existing source activation added provenance without new articles; repeating the same file/source added no new work. Candidate/metadata review preserved earlier work. Two attempts using nonexistent fixture paths were excluded.
- Owner/member decisions stayed independent. Owner Paused policy reached the already-open member page. A dirty member canonical note survived live policy change; explicit discard eventually reloaded saved blank evidence. The old persistent notice was a final-review finding.
- Private note/image save and latest Undo; Trace reopening current canonical work; invalid PNG rejection cleared upload busy state. Downstream work correctly refused earlier screening Undo. False field-target guidance and unrelated policy-recovery guidance were final-review findings.
- Reusable exclusion reason A was saved in a decision, renamed to B in the library and removed; the historical decision still showed A.
- The six Home tab labels changed through all ten languages, with authored strings unchanged. Keyboard roving focus, mouse Trace menu positioning, dark mode and reduced motion were observed. The narrow browser actually rendered at 599 CSS pixels; a requested 390-pixel override did not establish phone-width acceptance.

These observations predate the final fixes and are not claimed as reruns against the final assets. The focused combinations in the table above remain for the controller. Also unverified: every owner/member role and revocation combination; all live refresh race orderings; crash recovery; all image/Undo retention cases; every localized message; migration idempotence/restore-on-copy; true phone width; packaged Windows/Linux/macOS launch.

## Numbered migrations and safe rollback

No migration is added by this repair wave. The branch has additive workflow versions 1–3:

1. `review_workflow.py`: workflow/source/reason/random-order state; first upgrade of an existing database creates `.pre-workflow-v1.bak` if absent.
2. `review_imports.py`: staged batches/source records/candidates/metadata/provenance; calls version 1 and has no separate v2 backup.
3. `coding_evidence.py`: stable fields/requirements/uploads/request identity and nullable Undo request link; creates `.pre-workflow-v3.bak` if absent before its DDL.

Version markers and transaction guards implement repeatability in source; this wave did not run migrations or restore them. See [WORKFLOW_CONTROLS.md](WORKFLOW_CONTROLS.md) for exact storage and API contracts.

For authorized maintenance, stop writers, preserve a current consistent copy of all owner/actor/account/shared databases, original imports, PDFs, assets including staged/detached images, task metadata and configuration. Record the code commit and work on a separate copy. Never restore pre-workflow snapshots over later research; they omit later decisions, evidence and imports. Older code does not enforce current source/Maybe/evidence policies or protected imports. Do not run mixed-version writers, delete added tables, renumber fields or manually activate staged records. Restore-on-copy and compatibility review must precede any production rollback. No rollback, merge, push, release or installation change was performed here.
