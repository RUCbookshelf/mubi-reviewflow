# Screening rounds and filename sources — 2.0.5.2 replacement

> **2.1.5 update — repeat rounds and Not Sure Now have been removed from formal screening.**
> The “start another round” mechanism described below is **no longer available** in 2.1.5
> (the `POST /api/tasks/{id}/screening-rounds` route is gone and round fields are no longer
> returned by queue/settings endpoints). Both initial and full-text screening are now strictly
> binary (include/exclude); saving `maybe` returns a structured 409 (`maybe_disabled`,
> `message_key=workflow.maybe_disabled`). Not Sure Now remains only in pilot screening (W2).
> Existing `workflow_screen_rounds` tables and historical snapshots are **preserved unchanged**
> for rollback/read compatibility; no new rounds are created. On first upgrade, existing
> `maybe` decisions in initial and full-text stage decisions are reset to unscreened once,
> idempotently, with a `.pre-binary-reset.bak` backup per affected reviewer database; coding,
> extraction and evidence data are retained (dormant until a record qualifies again).
> The historical description below documents the pre-2.1.5 behaviour only.

## Scope

The decision strip remains one horizontal row in initial and full-text screening, including the optional uncertainty button, custom reason and reusable-reason save. The full-text page's old four-column grid was the cause of wrapping.

“Needs review” is displayed as “Not Sure Now” with corresponding wording in all ten languages. The stored `maybe` value and adjudication/Kappa behavior are unchanged.

Each reviewer can start another round once their current scoped queue is complete. Only remaining uncertain articles without a saved full-text decision are snapshotted. The second, third, fourth and further rounds are supported without a fixed limit. Choosing to proceed releases remaining uncertain articles to full text without converting their decisions. Existing full-text and coding work is preserved. Rounds belong to the reviewer, not to the team collectively. Starting a round pins its selected source scope while current owner policy continues to restrict access.

Since 2.1.4, **Not Sure Now is available only in the first round.** From the second round on, the round UI asks the reviewer to include or exclude each snapshotted record. Once a record has entered any of that reviewer's round snapshots (whether the round is active, closed or released), that reviewer can no longer save Not Sure Now for it; the backend rejects such saves with `maybe_first_round_only` and leaves the existing decision untouched. Records imported later that have never entered a round still count as first round and keep the button. Full-text screening remains a single pass and its Not Sure Now behaviour is unchanged; an unfinished second-round record still proceeds to full text with its original decision when the reviewer chooses to continue. The task-level `show_maybe` switch keeps its meaning: the option is shown only when that switch is on and the record is still first round for this reviewer. Undo that restores a previous Not Sure Now decision is a restoration, not a new decision, and is not blocked.

Saved-decision fingerprints determine review progress. A new save advances the round; guarded Undo restores the prior fingerprint and makes that article pending again. Snapshots and source policy are checked by the backend. The backend also rejects any new Not Sure Now save for a record that has entered one of the reviewer's round snapshots. Full-text save endpoints reject an uncertain article still held by an active repeat round. A per-reviewer settings marker and SSE event support other open pages; dirty forms remain protected.

Sources use the complete uploaded filename rather than RIS DB/DP metadata. Filename identity follows the existing case-insensitive, Unicode-normalized source-name uniqueness rule. Same filename reuses its source; different filenames preserve separate provenance even for identical file content. Names cannot be edited into guessed database names. Owners retain source-type confirmation and activation controls; unknown types are not fabricated for PRISMA.

## Migration and rollback

Numbered migrations v5 and v6 are idempotent and back up their SQLite database before first upgrade (`.pre-filename-v5.bak`, `.pre-rounds-v6.bak`). V5 re-associates reliable filename provenance and carries selected-source policies and paused sources forward. Original records, candidate IDs, article identities, metadata, decisions and coding are not replaced. Ambiguous legacy collisions retain originals rather than deleting records. Missing filename evidence remains explicitly legacy/unrecorded.

V6 adds only reviewer round snapshots. Code rollback leaves additional storage harmless and saved research intact. It does not restore earlier source labels or active-round holds. Do not restore a pre-upgrade database over newer research without preserving the newer records first.

## Validation

- Changed Python and JavaScript syntax, all locale files and inline scripts checked.
- Disposable API checks cover wrong RIS database metadata, filename identity, reimports, rounds 2–5, release to full text, direct-save bypass rejection, stale policy, Undo, reviewer independence, migration reruns, saved decisions and source pause preservation.
- Updated existing team/source/PRISMA regression scenarios retain manual generation, selective task metrics, updated reviews, other-source branch, DTA, independent study counts and access controls.
- Actual browser rendering checks cover Not Sure Now wording, start/release navigation and full-text controls at 1440, 1024, 768 and 420 pixels. Screenshot: `fulltext-single-row.png` in the local preview artifact directory.

No real research data or installed E:/ReviewFlow files were used or changed. Preview uses a disposable database at port 18744.
