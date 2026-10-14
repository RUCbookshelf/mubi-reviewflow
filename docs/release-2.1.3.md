# 2.1.3 release scope and validation

## Added

- Persisted personal report/sample proposals, owner/reconciler adjudication, confirmed relationship revisions, unlinking and separate personal/shared Undo.
- Qualitative evidence drafts with multiple findings per report, supporting quotations, locators, context and reviewer interpretations. Personal CSV and JSON downloads retain source filenames and bibliographic identifiers.
- Optional manual JBI, thematic, framework and meta-ethnography workspaces, mixed-method integration memos, JBI/CASP appraisal drafts and per-finding CERQual/ConQual confidence drafts.
- Theme/evidence matrix and human confirmation of shared original evidence; original reports and findings remain available.

## Improved

- Personal Machine Learning refresh interval defaults to 20 changed article decisions, configurable from 1 to 1000. Initial training readiness, immediate decision saves, model methods and labels retain their existing meaning.
- Model presets and settings are scoped to the authenticated member. Legacy task-wide model selection is inherited only by the verified owner until personally saved. Shared labels are unavailable during independent team work.
- Import wording and local spacing follow the approved product-aligned layout. Existing Todo card, capsule, Trace naming, navigation, themes and readers are retained.
- Ready activation uses the server's eligible count while remaining records load in the background.

## Fixed

- Import reads acknowledge their returned workflow version, preventing an outdated refresh baseline from repeatedly rebuilding a completed queue. Refresh requests are guarded against overlap.
- Activation invalidates stale background read tickets.
- Draft warnings compare current and saved values. Reverting a field to its saved value clears its dirty state; count/version-only changes do not masquerade as policy conflicts.
- Save dialogs expose invalid fields instead of asking for values in hidden inputs.
- Training minimum is system-managed (existing default 5); old manual values no longer block settings saves.

## Data and method boundaries

New additive SQLite tables retain personal workspace snapshots and request replay identifiers. Revision checks reject stale writes. Relationship annotations do not rewrite screening decisions, coding values, existing study IDs, effect selection or statistical models. Team adjudication requires paused independent stages and explicit existing roles. Optional tools store human reasoning; they do not automatically synthesize findings, score study quality, identify samples or run new models.

Desktop packages include the same frontend/backend. All validation data is synthetic and isolated; E:/ReviewFlow is outside this change. Back up research data before a user-managed upgrade. Reverting binaries leaves the additive workspace tables available for a later upgrade.

## Validation record

- 17 Node tests: presentation rules, cache overlays, DOI eligibility, refresh/activation gates, 20-decision batching, custom intervals, initial training, failed-rank recovery and ten-language labels.
- Seven authenticated evidence/ML integration tests: task/actor isolation, request replay, stale revision rejection, relationship confirm/unlink/Undo, multiple-finding persistence, CSV/JSON, Trace, large-workspace summaries, personal model/interval settings and independent training-label counts.
- Six existing read-side projection regressions and 12 transactional import regressions pass on isolated databases.
- Final isolated 1200-source-row/600-group benchmark: ten single saves took 287.7–329.3 ms. An earlier run took 166.6–212.9 ms; these are local measurements, not a deployment latency guarantee.
- Actual product instance on port 18752: saved a personal relationship and qualitative draft, confirmed the relation, changed synthesis settings and reloaded the matrix. Desktop 1440px and phone 390px checked; page width stayed within viewport, with table scrolling contained in the dialog.
- Restoring the Machine Learning interval to its saved value allowed normal navigation without a dirty prompt. Handled current Todo capsule without changing its established design.
- Completed import list stayed complete across periodic refresh checks; server log still contained only the original record/candidate page reads.
- Reviewed source-scope transitions, stale background overlays, permissions, personal/shared Undo, request validation, authored-text preservation, CSV formula safety, trace size and package payload inclusion. Corrected duplicated standalone record headings/original-source disclosures and stale adjudication Undo enablement.
- Node syntax checks, two inline scripts, Python compilation and git diff whitespace checks pass. Earlier guard-browser evidence is recorded separately in WORKFLOW_DRAFT_GUARD_FIX.md; that complete suite was not rerun for this release.
- Three-platform Actions checks compare 11 packaged source/version files against their checkout and emit platform build manifests before publication.
