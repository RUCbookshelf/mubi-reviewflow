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

## Decisions and completion

- **Maybe** is an explicitly saved uncertainty decision. Decision-history counts use Maybe rather than Pending.
- **Undecided** means no screening decision has been saved for that item.
- **Pending** remains appropriate for work awaiting action, such as duplicate review or adjudication. It must not stand in for a saved Maybe decision in a decision breakdown.
- A message that every queue item has a saved decision does not imply all uncertainty is resolved, every report is included, or coding is complete.
- **Data extraction & coding** is the workspace name. Coding dimension/field refers to the defined variable or category; statistical uses of dimension and reference retain their technical meanings.

## Review scope

The October 2026 wording pass covered the full-text counter, screening history breakdown, stage labels, PDF placeholders, queue-save completion notices, the main merge/adjudication label and the login subtitle. It is a focused interface review, not a certification of every analysis/help/export string or a native-language linguistic review. Historical Trace content and user-authored titles, notes and reasons are preserved verbatim.

## Home & Settings terminology

The Home wording review covers all ten supported interface languages: Chinese, English, French, Russian, Spanish, Japanese, Portuguese, German, Serbian and Korean. It clarifies the six settings categories, task controls, stage-source permissions, Maybe availability, reusable exclusion reasons, evidence requirements, notes display, display-preference storage, training-label counts and the feature overview.

- **Screening settings** contains controls for both title-and-abstract and full-text screening; individual stage labels name the stage explicitly.
- **Maybe option settings** controls availability of new Maybe decisions, rather than deciding or changing a member's saved results. Historical Maybe remains distinct from undecided work.
- **Permitted sources by stage** concerns sources explicitly assigned at import. They may be databases or other origins; filenames alone do not establish source identity. Paused means read-only.
- **Coding dimensions requiring evidence** requires notes or an image for completion; it does not make the dimension's coding value mandatory.
- **Appearance & storage** includes browser display preferences and desktop storage-location controls. Data lives on the ReviewFlow host, which can be a local computer or team server. Separate personal decisions do not constitute a claim of a double-blind research design.
- **Save current task workflow settings** saves stage-source rules, Maybe availability and evidence requirements. Libraries and Machine Learning settings retain their separate Save actions.
- Training-label counts refer to saved Include/Exclude decisions; saved Maybe decisions are not training labels.

Interface strings and placeholders are checked structurally across languages. These revisions do not constitute an independent native-speaker review of each translation. User-authored source names, reasons, notes and historical Trace payloads remain verbatim.

- **Hide Machine Learning scores** describes a display preference. It hides scores and badges while retaining the selected ranking order; it does not promise a double-blind study design.
- Model presets use neutral configuration names and descriptions. Benchmark performance is not presented as a universal guarantee. The four profile identifiers and their parameters remain unchanged.
