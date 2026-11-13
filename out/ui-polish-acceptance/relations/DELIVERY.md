# Relations / SoF / legacy RoB UI wave delivery

Base: E:/rf215-authfe, release/2.1.5 at 2230523. Source changes are confined to frontend plus Node tests. Parent installs scratch outputs and applies index-replacements.json (two exact replacements).

## Per-ID coverage

| ID | Change | Evidence / boundary |
|---|---|---|
| E-DYN-31 | Editor binds the opened private revision. On 409, local inputs and latest record display side by side. Save remains locked until explicit acknowledgement; failed refresh stays locked. Editing preserves the stable link id and replaces the reviewed link rather than keeping both report pairs. Session drafts use task/user + stable link id, preserving inputs and the acknowledgement requirement on reopen. | 4 new real-module Node cases plus live Chromium two-tab / real backend test. Same-pair fields: private revision 2 -> 3 after acknowledgement. Changed pair: 4 -> 5, one link only. Shared revision stays 0. |
| E-04 | Legacy result-level RoB explains its separate storage and links back to the exact source report in personal complete appraisal. Guard cancellation stops navigation; unavailable source is explained without opening another report. | New production-module Node regression plus live browser: Stay retains dirty draft; missing source is explained; actual R1 inclusion opens R1 complete appraisal; desktop/mobile screenshots, zero page errors. |
| E-06 | Shared pair-conflict copy now states the list was automatically refreshed and asks to review it. Failed reload does not claim success. | Existing digest / compatibility tests retained and updated. |
| E-07 | Saved adopted source initializes as adopted; switching an existing source requires acknowledgement before PUT. | Node adopted/manual payload cases; live adopted record reopened and checked; source remains adopted when acknowledgement is missing, becomes manual only after acknowledgement. |
| E-08 | Changing wizard parameters replaces old preview with an explicit instruction to request a fresh preview. | Live outcome checkbox test. |
| E-12 | Qualitative JBI/CASP question expansion indices persist when changing report or tool. | Live report switch and tool switch both retain questions 1 and 6 expanded. |
| E-13 | Legacy RoB records show readable judgment/rationale/source cards and edit entries. DTA results show study/test/count/source cards while original raw data remains available. | Node literal text rendering and source record test; live legacy RoB screenshots. DTA editing/deletion was not added. |
| E-14 | SoF suggestions show readable high-risk count, I², CI bounds/width and p where available; full raw signals stay in collapsed audit details. Display numbers use four significant digits; raw values remain unchanged. | Code review and real SoF UI. Analysis GRADE side belongs to evidence agent. |
| E-15 | Legacy RoB saved-assessment selector fills all fields/domain rationales. Same backend identity revises one record. Changing scope asks whether to create another record or overwrite an existing target record. Drafts are scoped to task/user and registered with the shared guard. | 5 Node cases across E13/E15/E22, plus a sixth E04 navigation case. Live same-scope save retains 1 row; changed timepoint cancel retains 1, confirm yields 2. |
| E-17 / E-05 | SoF wizard labels its source as saved wizard judgments or current suggestions; explains analysis GRADE records are separate and links to that panel. Exposes RFOpenSoFWizard for the reciprocal link. | Live source caption; navigation coordinated with evidence agent. |
| E-18 | Existing prev values can be loaded into the manual editor for review and a new revision. UI states that returning to an unjudged state is unavailable. | Node confirms no immediate write and manual payload only; live previous-values control. **Partial / backend handoff**: current API exposes PUT only, no delete, undo or historical snapshot restoration. Cannot implement true clear/undo solely in frontend without misrepresenting provenance. |
| E-22 | Required scope, overall and domain judgments/rationales are checked before request; missing domain focuses its input and displays localized text. Known backend validation strings are mapped to translated messages. | Node + live Chinese missing-rationale test; no API write on invalid form. Backend structured error upgrade remains out of scope. |

## Files

- custom_frontend/report_evidence.js
- custom_frontend/report_evidence_locale.js
- custom_frontend/report_evidence.css
- custom_frontend/table_wizards.js
- custom_frontend/table_wizards_polish_locale.js (new, load before table_wizards.js)
- custom_frontend/analysis_rob_editor.js (new, load after workflow_controls.js)
- custom_frontend/analysis_rob_editor_locale.js (new, load before editor)
- build/tests/report_evidence_ui.test.cjs (15 tests)
- build/tests/analysis_rob_editor_ui.test.cjs (6 tests)
- build/tests/table_wizards_polish.test.cjs (4 tests)
- index-replacements.json (legacy RoB save delegation and loaded hook only)

All new vocabulary contains ten locales with aliases as appropriate. No native browser confirm/prompt/alert calls were introduced. Scope confirmation uses RFConfirm.

## Verification

Node: 25/25 passed. Tests execute actual product module behavior, including conflict write counts, revision acknowledgement, link identity, source payloads and record scope. Changed JavaScript syntax checks passed.

Real service: http://127.0.0.1:18931, same new source tree and isolated synthetic account/task. No research data used. Browser script A operates actual UI; tab B issues real authenticated backend requests. No mocked 409 responses.

Evidence:
- relation-browser-results.json
- relation-conflict-fields-desktop.png
- relation-conflict-changed-pair-desktop.png
- relation-conflict-mobile.png
- rob-sof-browser-results.json
- rob-editor-desktop.png
- rob-editor-mobile.png
- sof-judgment-desktop.png
- sof-judgment-mobile.png
- qual-browser-results.json
- e04-browser-results.json
- legacy-provenance-entry-desktop.png
- legacy-appraisal-link-desktop.png
- legacy-appraisal-link-mobile.png

Desktop 1440x1000 and mobile 390x844 checked, including no mobile horizontal overflow. Relation, RoB and settled SoF screenshots visually inspected. All completed browser cases recorded zero uncaught page errors. Shared rf-tables-choice[hidden] CSS was corrected by evidence agent; a fresh real browser check confirms the acknowledgement control is hidden when source is unchanged.

## Impact notes

GitNexus report_evidence change: MEDIUM, 9 direct callers; lockState MEDIUM, 2 direct; openRelated LOW, 4 direct; adjudicate LOW, 1 direct. Qualitative appraise LOW, no direct; draw LOW, 2 direct in appraisal family. No HIGH/CRITICAL results. Table wizard and inline index symbols are absent from the configured E:/mubi-reviewflow index: impact returned UNKNOWN/not found, recorded as an indexing limitation; actual new-tree direct callers were inspected. Parent owns final detect_changes and full integration validators.

## Final E04 patch verification

E04-editor-replacements.json contains two exact replacements; E04-locale-insert.txt contains three ten-language rows. Parent installed both and the regression test. The live check used the same isolated service. Initially R1 was unavailable in coding because a legacy synthetic title-duplicate review remained pending; the test recorded the explanatory branch, then used actual API decisions to resolve the synthetic candidate and include R1/R2 for screening and full-text. This fixture setup was not a product fix. R1 then opened correctly in complete appraisal. All 25 lane Node cases passed against the installed tree. The editor-file GitNexus query returned UNKNOWN because this new file is not in the configured index; source call paths were reviewed.
