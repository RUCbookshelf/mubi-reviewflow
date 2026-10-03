"""Persist raw binary arm counts for sparse-data analyses."""

from collections.abc import Mapping
from contextlib import closing
from pathlib import Path

from coscreen.db import _add_column_if_missing, _connect
from coscreen.review_analysis import _linked

DbPath = str | Path
_FIELDS = ("events_t", "total_t", "events_c", "total_c")
_TABLE = """
CREATE TABLE IF NOT EXISTS review_binary_arms (
  study_id TEXT NOT NULL REFERENCES review_studies(id),
  comparison TEXT NOT NULL,
  outcome TEXT NOT NULL,
  timepoint TEXT NOT NULL,
  events_t INTEGER NOT NULL CHECK(typeof(events_t)='integer' AND events_t>=0),
  total_t INTEGER NOT NULL CHECK(typeof(total_t)='integer' AND total_t>0),
  events_c INTEGER NOT NULL CHECK(typeof(events_c)='integer' AND events_c>=0),
  total_c INTEGER NOT NULL CHECK(typeof(total_c)='integer' AND total_c>0),
  source_key TEXT NOT NULL REFERENCES articles(zotero_key),
  source_locator TEXT NOT NULL DEFAULT '',
  CHECK(events_t<=total_t),
  CHECK(events_c<=total_c),
  PRIMARY KEY(study_id,comparison,outcome,timepoint)
)
"""


def _ensure_table(conn) -> None:
    conn.execute(_TABLE)
    _add_column_if_missing(conn, "review_binary_arms", "source_locator",
                           "ALTER TABLE review_binary_arms ADD COLUMN source_locator TEXT NOT NULL DEFAULT ''")


def save_binary_arms(db: DbPath, study_id: str, comparison: str, outcome: str,
                     timepoint: str, source_key: str, arms: Mapping,
                     source_locator: str = "") -> None:
    if any(not isinstance(value, str) or not value.strip()
           for value in (study_id, comparison, outcome, timepoint, source_key)):
        raise ValueError("study, comparison, outcome, timepoint and source report are required")
    if not isinstance(arms, Mapping) or set(arms) != set(_FIELDS):
        raise ValueError("arm inputs must contain events_t, total_t, events_c and total_c")
    counts = [arms[field] for field in _FIELDS]
    if any(type(value) is not int or value < 0 or value > 2**63 - 1 for value in counts):
        raise ValueError("binary arm counts must be nonnegative SQLite integers")
    events_t, total_t, events_c, total_c = counts
    if total_t == 0 or total_c == 0 or events_t > total_t or events_c > total_c:
        raise ValueError("arm totals must be positive and events cannot exceed totals")

    study_id, comparison, outcome, timepoint, source_key = (
        value.strip() for value in (study_id, comparison, outcome, timepoint, source_key))
    with closing(_connect(db)) as conn, conn:
        _ensure_table(conn)
        _linked(conn, study_id, source_key)
        conn.execute(
            "INSERT INTO review_binary_arms "
            "(study_id,comparison,outcome,timepoint,events_t,total_t,events_c,total_c,source_key,source_locator) "
            "VALUES (?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(study_id,comparison,outcome,timepoint) DO UPDATE SET "
            "events_t=excluded.events_t,total_t=excluded.total_t,events_c=excluded.events_c,"
            "total_c=excluded.total_c,source_locator=CASE WHEN source_key=excluded.source_key "
            "THEN COALESCE(NULLIF(excluded.source_locator,''),source_locator) ELSE excluded.source_locator END,"
            "source_key=excluded.source_key",
            (study_id, comparison, outcome, timepoint, *counts, source_key, source_locator.strip()),
        )


def list_binary_arms(db: DbPath) -> list[dict]:
    with closing(_connect(db)) as conn, conn:
        _ensure_table(conn)
        rows = conn.execute(
            "SELECT study_id,comparison,outcome,timepoint,source_key,source_locator,events_t,total_t,events_c,total_c "
            "FROM review_binary_arms ORDER BY study_id,comparison,outcome,timepoint"
        ).fetchall()
    return [{"study_id": row[0], "comparison": row[1], "outcome": row[2], "timepoint": row[3],
             "source_key": row[4], "source_locator": row[5],
             "arms": dict(zip(_FIELDS, row[6:]))} for row in rows]
