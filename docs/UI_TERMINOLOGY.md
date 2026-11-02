# ReviewFlow interface terminology

## Units and stages

PRISMA 2020 distinguishes a **record** (a database entry identifying a report), a **report** (a document describing a study), and a **study** (the underlying investigation). Multiple reports can describe the same study. See the [PRISMA 2020 statement, glossary](https://pmc.ncbi.nlm.nih.gov/articles/PMC8007028/) and [official flow diagrams](https://www.prisma-statement.org/prisma-2020-flow-diagram).

| Context | Preferred English | Meaning |
| --- | --- | --- |
| Title and abstract screening | Record | Bibliographic entry being screened |
| Full-text screening | Full-text report | Document being assessed for eligibility, including a report awaiting PDF retrieval |
| Data extraction & coding | Report / coding field | Source document and its extraction/coding controls; these are not automatically distinct studies |
| Study-level analysis | Study | Underlying investigation identified by the study grouping used for that analysis |
| Bibliography / citation sources | Reference | Bibliographic citation; also legitimate in statistical terms such as reference group/category/standard |
| Choosing item sequence | Screening order | Import, Machine Learning or Random queue order |
| Reviewer disagreement resolution | Adjudication | Recorded resolution of differing reviewer decisions; role name remains Reconciler |

The full-text queue position uses **Full-text report x / y**. This is an item position within the current queue, not a count of unique studies or proof that a PDF has been retrieved. These wording changes do not alter PRISMA calculations, study grouping, permissions or saved decisions.

## Role names in controls (UX approval list F47)

The role vocabulary is the pair **Coder** (筛选与编码员) and **Adjudicator** (裁决员). Controls show the translated name with the English term in parentheses — 「筛选与编码员（Coder）」/「裁决员（Adjudicator）」 — and the label is a dictionary entry (`team_prisma_locale.js`), never a hardcoded English `<option>` or literal. The English slot is the bare term, so English-language screens read Coder / Adjudicator exactly as before. **Reconciler** stays the internal grant identifier only (API role value `reconciler`); it is not shown to users. The help page uses the same translated names, so a Chinese owner who reads the help finds the same two labels in the invite and role menus.

## Decisions and completion

- **Maybe** is an explicitly saved uncertainty decision. Decision-history counts use Maybe rather than Pending.
- In Chinese the canon label is 「暂不确定」 (UX approval list F23); the older 「待定」 is kept only as a dictionary alias so cached controls still translate. Every place that renders the decision label goes through `T()` — including the trace/decision table and the adjudication dropdown, which used to concatenate the raw label.
- **Undecided** means no screening decision has been saved for that item.
- **Pending** remains appropriate for work awaiting action, such as duplicate review or adjudication. It must not stand in for a saved Maybe decision in a decision breakdown.
- A message that every queue item has a saved decision does not imply all uncertainty is resolved, every report is included, or coding is complete.
- **Data extraction & coding** is the workspace name. Coding dimension/field refers to the defined variable or category; statistical uses of dimension and reference retain their technical meanings.

## Review scope

The October 2026 wording pass covered the full-text counter, screening history breakdown, stage labels, PDF placeholders, queue-save completion notices, the main merge/adjudication label and the login subtitle. It is a focused interface review, not a certification of every analysis/help/export string or a native-language linguistic review. In-app Trace display applies no extra redaction, and user-authored titles, notes and reasons are preserved verbatim; historical Trace payloads themselves are compacted by the 2.1.4 legacy fold and privacy-redacted in public trace exports under the documented rules (see REPORT_LINKS_AND_EVIDENCE, section 7).

## Home & Settings terminology

The Home wording review covers all ten supported interface languages: Chinese, English, French, Russian, Spanish, Japanese, Portuguese, German, Serbian and Korean. It clarifies the six settings categories, task controls, stage-source permissions, Maybe availability, reusable exclusion reasons, evidence requirements, notes display, display-preference storage, training-label counts and the feature overview.

- **Screening settings** contains controls for both title-and-abstract and full-text screening; individual stage labels name the stage explicitly.
- **Maybe option settings** controls availability of new Maybe decisions, rather than deciding or changing a member's saved results. Historical Maybe remains distinct from undecided work.
- **Permitted sources by stage** concerns sources explicitly assigned at import. They may be databases or other origins; filenames alone do not establish source identity. Paused means read-only.
- **Coding dimensions requiring evidence** requires notes or an image for completion; it does not make the dimension's coding value mandatory.
- **Appearance & storage** includes browser display preferences and desktop storage-location controls. Data lives on the ReviewFlow host, which can be a local computer or team server. Separate personal decisions do not constitute a claim of a double-blind research design.
- **Save current task workflow settings** saves stage-source rules, Maybe availability and evidence requirements. Libraries and Machine Learning settings retain their separate Save actions.
- Training-label counts refer to saved Include/Exclude decisions; saved Maybe decisions are not training labels.

Interface strings and placeholders are checked structurally across languages. These revisions do not constitute an independent native-speaker review of each translation. User-authored source names, reasons and notes remain verbatim in the app; historical Trace payloads are compacted by the 2.1.4 legacy fold and privacy-redacted in public trace exports, while in-app Trace display applies no extra redaction.

- **Hide Machine Learning scores** describes a display preference. It hides scores and badges while retaining the selected ranking order; it does not promise a double-blind study design.
- Model presets use neutral configuration names and descriptions. Benchmark performance is not presented as a universal guarantee. The four profile identifiers and their parameters remain unchanged.

## Queue eligibility messages (UX approval list F49)

Title-and-abstract and full-text queue eligibility notices name the screened unit **Record** in English ("This record is not eligible for this stage.", "This record is outside the permitted sources.", "This record is in a repeat screening round. …", "This record is not in the current queue; reload screening and try again.", "This saved record is outside the selected queue. Return to the queue to continue."), so one screen uses one noun. The previous "This article …" sentences are retained as dictionary aliases: the reason codes emitted by `workflow_controls.js` and any cached control that still carries the old English sentence keep resolving. This is a wording-only change; eligibility checks, reason codes and saved decisions are untouched.

Scope limit of this pass: sentences that legitimately say *article* outside the screening queue (for example the Machine-Learning score explanation "… estimate for this article") are unchanged, and no non-screening module was swept. This is the approval-list "随版缓解" half; a full terminology sweep is a separate item.

## Deleted tasks (UX approval list F50)

Deleting a task moves its directory into the quarantine area. The user-facing destination is 「隔离区」 everywhere: the confirmation, the success notice and the help page. The internal folder name `.trash` (`coscreen/tasks.py` `TRASH_DIRNAME`) is not shown in the interface. Range notation in the UI uses an en dash (2–9 decision CSVs).

## Confirmation dialogs (UX approval list F52)

Destructive confirmations use the in-app `RFConfirm` dialog instead of the native `confirm()`: the destructive action is the red primary button, the safe action (取消/Cancel) takes initial focus, Tab cycles inside the dialog, Escape cancels, and focus returns to the control that opened it. The message text is the existing `T()` string, unchanged, so the dialog follows the selected interface language. `RFConfirm` is the shared shape for later confirmations (F24, F33). Its title and both button labels are dictionary entries (`请确认` / `取消` / `确定`), not browser-localised strings; `finish()` closes and removes the dialog synchronously so a second confirmation cannot stack a duplicate `#rf-confirm-dialog`.

## Login and registration constraints (UX approval list F51)

The username and password placeholders state the constraint *before* the user submits: 「3–64 位，可用字母、数字、. _ @ - 与汉字」 and 「8–128 位」. They restate `custom_backend/auth.py` (`_USERNAME_RE` = `^[0-9A-Za-z._@\-\u4e00-\u9fff]{3,64}$`, `_MIN_PASSWORD_LEN = 8`, `_MAX_PASSWORD_LEN = 128`); changing that regex or those bounds requires updating the placeholder copy and its ten dictionary slots. The separate 72-byte bcrypt ceiling stays an error-path message, not a placeholder.
