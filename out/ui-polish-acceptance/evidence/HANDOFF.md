# Appraisal / GRADE / evidence UI wave handoff

Source: E:/rf215-authfe, feat/2.1.5-ui-polish, baseline 2230523. Product files are scratch copies under custom_frontend; parent installs them. No backend source or real research data was changed. No commit made by this agent.

## Install manifest

- custom_frontend/appraisal_workspace.js
- custom_frontend/analysis_grade.js
- custom_frontend/workflow_evidence.js
- custom_frontend/assessment_tables_locale.js
- build/tests/analysis_grade_ui.test.cjs
- build/tests/workflow_evidence_save_ui.test.cjs

Load assessment_tables_locale.js before analysis_grade.js. WorkflowControls.registerDraft(name, {dirty, save, discard, saving}) is supplied by the workflow agent. The draft registrations survive context changes; modules retain their own context guards. Include both Node files in the release workflow test list.

## Disposition for this agent's rows

| ID | Disposition | Implementation and evidence |
|---|---|---|
| E-01 | Fixed | Independent appraisal registration in the global guard; localStorage scoped by task, user, screener, article and tool. Live browser: comment-only edit opens leave guard; Stay retains page; confirmed reload restores comment; successful save clears dirty. |
| E-02 | Fixed | Sticky progress/save/next-unanswered toolbar plus domain jump buttons. Actual default RoB 2 form remains 22 questions, with 5 domain links. No tool content or algorithm changes. |
| E-03 | Fixed | Tool selector and save buttons use stable focus keys across render; save focus retained even while the temporary fieldset is disabled. Live keyboard focus checks passed for tool and top save. Popup close also restores its trigger focus. |
| E-04 | Fixed, shared with relations | Complete appraisal identifies its per-article/item source and links directly to the analysis risk-of-bias subview via the workspace router. Reverse/legacy entry belongs to relations agent. Live forward link passed. |
| E-05 | Fixed, shared with relations | GRADE links to RFOpenSoFWizard; exported RFOpenGrade navigates to the real report/grade subview and reports blocked navigation correctly. Live GRADE → populated SoF wizard and return opener passed. |
| E-09 | Fixed | Empty linked-appraisal result explains that article appraisal and effect source linkage must be completed. No unavailable prefill action is fabricated. Live empty CASP result and explanatory text checked. |
| E-11 | Fixed | Successful evidence save merges association IDs and reuses unchanged image objects/URLs; only changed galleries rebuild. Live note-only save produced no evidence-image GET and kept all image URLs. |
| E-14 | Fixed, shared with relations | Known RoB/I² rules and key counts/CI/p values use readable localized labels. Raw signal data remain in a collapsed audit disclosure. SoF snapshot half belongs to relations agent. Existing signal/rationale Node checks retained. |
| E-16 | Fixed | Evidence save no longer calls full E.render/openBenchPdf. Live page 8, zoom, scroll position and PDF DOM object stayed identical; no PDF GET on note-only save. |
| E-17 | Fixed, shared with relations | GRADE export explicitly states that it uses saved assessments, separate from wizard suggestions/judgments, and links to that workflow. Wizard's reverse source disclosure is relations agent's work. |
| E-19 | Fixed | Human input remains override_notes; existing signal_sources dict holds a small versioned _reviewflow_notes record with human text, legacy note, and machine overrides. Machine override differences are displayed separately. Legacy mixed notes are preserved read-only rather than guessed apart. Node repeated save roundtrip and real backend reload passed. |
| E-20 | Fixed | GRADE registers dirty/pending/save/discard; drafts persist per task/user/screener/comparison/outcome/timepoint. Successful save clears only the submitted revision; failure or typing during save retains dirty. Live refresh recovery and save cleanup passed; task/account isolation and concurrency covered by Node. |
| E-21 | Fixed | GRADE preview and CSV headers use local dictionary labels; open preview re-renders cached headers on language change. Live Chinese 9-column header check passed. |
| E-DYN-30 | Fixed, unit verified | Personal/shared entry buttons expose aria-pressed and selected styling, including cached discovery updates; shared save button states that it saves private evidence for a shared submission. Node mode/cache test passed. A real shared-permission team fixture was not available to this agent. |
| F-DYN-11 | Fixed in fdyn11/exact-change.json | Empty personal/shared schema now establishes its clean baseline and clears loading feedback; save stays disabled. Two tests execute the unmodified production E.render path: both failed before the one-branch fix, both pass after it. Parent should apply exact old/new plus append tests, not overwrite integrated files. |
| F-DYN-14 | Coding half fixed | Empty coding queue calls the existing no-article PDF empty-state path and offers full-text screening/coding-scheme entry buttons; empty schema links to coding scheme. Parent owns index-level fulltext/PDF controls. |
| F-DYN-19 | Fixed | Retain the coding load error object (messageKey/params/details) with its scope/ticket/node; language changes translate the existing error and status without a network retry. Live injected contract 404 changed Chinese → English with zero refetch. |

## Rows assigned to other agents

E-06/07/08/12/13/15/18/22/E-DYN-31: relations + legacy index agent. E-10: screening/workflow. E-DYN-11/32: analysis owner. These are not claimed as validated by this agent.

## Verification evidence

- Node: analysis_grade_ui.test.cjs, 6 passing behavioral tests.
- Node: workflow_evidence_save_ui.test.cjs, 5 passing behavioral tests after the F-DYN-11 increment.
- Product JavaScript syntax checks passed.
- Ten-language appended rows validated for 10 nonempty values each.
- browser-results.json: real new-tree backend at http://127.0.0.1:18931; synthetic account/task UIpolishappraisalGRADEsynthetic-080f. Checks E-01/02/03/11/16/19/20/21 passed; zero pageerror events.
- error-language-results.json: F-DYN-19 before/after strings and no-refetch check.
- Screenshots: evidence-after-save.png, appraisal-saved.png, grade-saved-sof.png, grade-empty-appraisal.png, grade-mobile.png, evidence-error-english.png.
- Additional browser script browser_links_mobile.py: populated GRADE → SoF wizard, RFOpenGrade return, appraisal → legacy risk-of-bias route; 390px viewport document width exactly 390 and no form controls outside viewport.
- Browser automation used bypass_csp=True solely to allow Playwright evaluation in this local synthetic test; it was not a security/CSP audit. Backend calls were real except F-DYN-19's deliberately injected contract 404.

The first fixture attempt exposed Windows path length in the long visualizations directory. Parent restarted the same new-tree backend with the synthetic database copied to E:/mubi-reviewflow/out/ui-polish-data. The existing synthetic account/task were preserved; subsequent screening, dimension, effect and PDF setup calls succeeded. This was not treated as a product backend regression.

## Impact analysis

GRADE indexed helpers and workflow_evidence drawImages/clear: LOW, at most 3 direct callers, no indexed execution flows. Appraisal helpers and arrow-assigned evidence save/render are absent from the main-tree index and reported UNKNOWN; their source-level callers were checked in the workbench/module boundary. No HIGH/CRITICAL report was ignored. Parent must run detect_changes before committing the integrated wave.

## Boundaries

- No API contract or backend algorithm edits; source and effect matching stay unchanged.
- No new undo API was invented. SoF undo capability/boundary is handled by the relations owner.
- Legacy combined GRADE notes are retained as a separate read-only historical block. New editable human text does not accumulate generated differences.
- Temporary browser scripts/session file are verification artifacts, not release product files. Do not commit browser_session.json (synthetic bearer token).

## Evidence correction and final rerun

The first browser script used a malformed embedded 1px PNG (bad IDAT checksum). Upload returned HTTP 500, and the script had not asserted upload status or a nonzero image count. Its initial E-11 browser conclusion was therefore insufficient and was withdrawn. The original screenshot/results were retained as *-invalid-fixture artifacts.

The corrected script generates/opens a valid 180x100 synthetic PNG, verifies it with Pillow, asserts both UI uploads return 200, requires two decoded images (naturalWidth > 0), and captures every API response >= 400. Two full reruns passed with two image blob URLs unchanged after note-only save, no PDF/image GET, page 8/9 at 116%, scrollTop 8646, and the same PDF DOM node. All API failures and pageerror arrays are empty. The current evidence-after-save.png is a fresh steady-state screenshot taken only after the save button becomes enabled again. The PDF intentionally consists of nine blank pages generated by pypdf; these checks establish reading-position preservation, not text rendering fidelity.
