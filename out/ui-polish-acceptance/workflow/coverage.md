# Workflow lane UI polish coverage

Source baseline: `E:/rf215-authfe`, `feat/2.1.5-ui-polish`, 2230523. Product edits remain frontend-only. The parent installs these scratch files and owns index/CI. No real research data was used.

## Delivery

`custom_frontend/`: workflow_controls.js, workflow_locale.js, workspace_undo.js, sidebar_sheet.js, pilot_screening.js, pilot_screening_locale.js, living_review.js, workspace_shell.js, screen_reader.js, al_stop_panel.js, al_profile_selector.js, local_storage.js, protocol_designer.js, research_trace.js, update_check.js, pdf_fetch.js.

Tests: new `build/tests/workflow_ui_polish.test.cjs`; modified `build/tests/pdf_fetch_ui.test.cjs`. The scratch copy of living_review.test.cjs is an unchanged verification fixture, not a delivery change. No index.html copy is present.

## Item disposition

| ID | Implementation | Verification |
|---|---|---|
| A-DYN-01 | One zero-task overview card; eight English-filled Chinese rows corrected | Locale regression; DOM implementation |
| A-DYN-02 | Hide duplicate home heading; breadcrumb remains visible | Source inspection; parent visual gate |
| A-DYN-03 | Settings tabs remember `.view` scroll per tab | Real Edge: 300 → 300 |
| A-DYN-05 | Navigation animation completes in 300 ms, rather than cumulative 1.86 s | Timing definition and syntax check; parent visual gate |
| A-DYN-07 | Workflow save bar and explanation appear only in Tasks/Coding settings | Real Edge appearance tab save bar hidden |
| A-DYN-09 | Add current-task Full-text screening action | Source inspection |
| A-DYN-10 | Same signatures preserve policy/source/reason/overview nodes | Real Edge unchanged refresh retains focused source select and current-task child |
| A-DYN-12 | Source name uses explicit read-only text; source type remains editable | Source inspection |
| A-DYN-15 | Workflow save lock, disabled controls, saving/saved/error notice | Source + regression gate; parent slow-response gate |
| D-01 | Active-stage signatures prevent unrelated coding policy reload; concurrency version rebases without rebuilding reading | Real Edge four natural cycles, 124 seconds; coding paused in another client; card/abstract/options identical, scroll 850, focus excReason |
| D-02 | Reason collections and dropdown decoration are signature guarded | Same 124-second probe + Node same-library test |
| D-03 | Parent index save-in-progress handling | Parent-owned |
| D-04 | Living Review import entry navigates once; remove dead file selector, keep separate clearly labeled analysis context | Existing 7 Living Review tests pass; parent integrated navigation gate |
| D-05 | Chinese draft legend/action translated | Locale regression |
| D-06 | Accessible reason label; Save as reusable becomes an explicit adjacent button, not an option triggered by End | Real Edge reading screenshot; parent keyboard gate |
| D-07 | Parent index atomic card replacement | Parent-owned |
| D-08 | Sampling starts only after draft guard; account/task/screener scoped local sample state restores on return | Node scope restoration/account isolation test; real backend stores decisions independently |
| D-09 | Training note/maybe aliases registered in ten languages | Node alias test; real English training shows Comments (global vocabulary normalizes Notes) |
| D-10 | Loading notice and 50-record pages; selection remains across pages | Node 1500-record fixture renders 50; real 60-record task renders 50 and loading notice |
| D-11 | Training dialog `[hidden]` overrides grid display | Real curated step: zero visible threshold/size field grids |
| D-12 | One dismissible connection notice and exponential backoff up to five minutes; explicit retry bypasses delay | Node offline/recovery test; real request abort, one notice, dismissal/recovery |
| D-13 | Parent index screening toolbar grouping | Parent-owned |
| D-DYN-20 | Parent index transition cleanup | Parent-owned |
| D-DYN-21 | Pure curated sample size derives from selection; 10–100 limit validated before final step; final step shows selected count | Real POST 201 with 20 selected and size=20; Node test |
| D-DYN-22 | Gate dismiss button; recheck on navigation; expose decisionSaved() to parent successful decision | Source + parent successful-decision call |
| D-DYN-23 | Reading help has visible label | Real Edge screenshot |
| D-DYN-24 | Parent index non-decision navigation/history titles | Parent-owned |
| D-DYN-25 | Screening Batch fetch navigates to the coding PDF fetch settings | Real visible button; parent target-click gate |
| E-10 | Sync an already-created screening hint after asynchronous strategy load/save; hide inclusion-probability ML guidance when strategy is cluster, restore it correctly on return to ML | New screen_reader_e10 regression fails before patch and passes after; e10.patch is a minimal patch against the parent's current screen_reader. Browser strategy max -> saved cluster -> active cluster ranking: stale tip hidden, record loaded, button says topic cluster, zero JS errors (qa/e10-results.json, qa/e10-settled-results.json, qa/e10-cluster-settled.png) |
| Supplement: page-number i18n | Add ten-language page-number label; this is not E-10 | Locale regression |
| E-03/E-20 and other draft workspaces | registerDraft(name,{dirty,save,discard,saving?}) participates in navigation, save, discard, beforeunload and refresh protection; registrations survive task changes | Node registration test; evidence/relations agents own their handlers and full flows |
| F-DYN-09 | Guard starts at Stay; cancellation restores invoking focus | Source + parent modal keyboard gate |
| F-DYN-13 | Global Undo checks shared RFModalActive before dispatch | Executed actual keyboard callback in Node with modal active |
| F-DYN-15 | Workflow and both reason-library reads start concurrently; versions still gate render | Source + relevant refresh regression |
| F-DYN-16 | No-op workflow DOM stable; import notice polling retained for other-client changes with import lane's signature guard | Workflow Node/Edge evidence plus import lane coverage |
| F-DYN-20 | Direct internal page changes push history and update stage memory | Real go(export) from Help then browser Back returns Help |
| P0 shared modal boundary | RFModalActive checks visible native dialogs and aria-modal custom dialogs | Node helper+Undo test; parent numeric handler uses helper |
| P1 native dialogs family | ML preset, data migration/cleanup, protocol task creation, trace clear, PDF overwrite use awaited RFConfirm; updater uses toast | Source scan; trace cancel/context isolation + update 3-branch tests; PDF 13 logic cases |

Other A/B/C/E/F report IDs remain with their assigned lanes. The backend-only 55/22 RoB distinction and unconfirmed static findings were not changed.

## Wording changes

| Before | After |
|---|---|
| Chinese `Unsaved screening reason` | 未保存的筛选理由 |
| Chinese `Clear draft` | 清除草稿 |
| Chinese `No task yet` | 还没有任务 |
| Chinese `Create a task to import records and start screening.` | 创建任务以导入文献并开始筛选。 |
| Chinese `Create a task` | 创建任务 |
| Chinese `First step: create a task` | 第一步：创建一个任务 |
| Chinese `Screening, coding and analysis all run inside a task. Create one to start.` | 筛选、编码和分析都在任务中进行。请先创建任务。 |
| Chinese legacy `A reason is saved together with Include, Maybe or Exclude...` | 理由随纳入或排除决定一同保存。此对话框打开时无法进行筛选判定。 |
| Training `备注` lookup without a row | pilot.note, ten-language row; English Notes/Comments |
| Training shared `暂不确定` lookup | pilot.decision_maybe; English Not Sure Now |
| `页码` missing standalone row | Ten-language Page label |
| Repeated network error toast | 持续、可收起的连接中断提示 + 重试 |
| Silent workflow save | 正在保存… / 已保存。 / localized error |

Root-requested 17 additional loading/auth/navigation strings and two import-lane workspace-loading strings were supplied in workflow_locale.js, all ten columns. Added helper rows for training loading, page counts, 10–100 selection validation, offline dismissal and analysis-context purpose are also ten-language.

## Evidence

- `qa/workflow-results.json`: four natural polling checkpoints and unchanged source focus, zero page errors.
- `qa/followup-results.json`: training 201, size20, hidden steps, scroll300 retention, history return, zero page errors.
- `qa/reading-before.png` and `qa/reading-after-policy-poll.png`.
- `qa/sources-focus-after-poll.png`.
- `qa/training-curated-final-en.png`.
- `qa/connection-notice-en.png`: recaptured after the fix in Chinese; notice is below the top bar and clear of the navigation button.
- `qa/final-results.json`: real model-switch confirmation opens, shared modal detector returns true, Escape cancels with zero profile writes; 390 px screen has no horizontal overflow, zero page errors.
- `qa/profile-confirm-cancel-only.png`, `qa/screen-mobile.png`.

Browser plugin/skill absent; regular Playwright drove real Edge against the new tree's backend at 127.0.0.1:18931. Browser API was used for fixture creation and controlled other-client policy change; no production data. CSP bypass was enabled only for instrumentation using page.evaluate/wait_for_function.

Node: workflow_ui_polish 11/11; living_review 7/7; pdf_fetch 13 logic tests pass. The PDF loader assertion was updated from r67 to the parent's new UI-polish token and accepts the deferred data-rf-app-src registration. Its actual callback passes against the installed index.html; parent must copy the updated test before the full gate. All 16 JS files pass `node --check`.

GitNexus impact: indexed functions returned LOW (0–3 direct callers, no indexed process memberships). Property-assigned handlers/new symbols and pilot functions were not indexed (UNKNOWN); source callers were inspected, and this limitation was reported to parent. No HIGH/CRITICAL result. Parent owns final detect_changes, integrated checks and commit decisions.
