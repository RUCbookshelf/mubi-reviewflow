"""Persist raw one-group event counts for binomial and Poisson models."""

from __future__ import annotations

import math
from contextlib import closing
from pathlib import Path

from coscreen.db import _connect
from coscreen.review_analysis import _linked

DbPath = str | Path
_KINDS = {"proportion", "rate"}
_SQLITE_INT_MAX = 2**63 - 1
_COLUMNS = (
    "result_id", "study_id", "comparison", "outcome", "timepoint", "kind", "events",
    "total", "person_time", "time_unit", "source_key", "source_locator", "selected",
)


def _required_text(value, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} is required")
    return value.strip()


def _finite_number(value) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def save_single_group_count(
    db: DbPath,
    study_id: str,
    comparison: str,
    outcome: str,
    timepoint: str,
    kind: str,
    events: int,
    source_key: str,
    *,
    total: int | None = None,
    person_time: float | None = None,
    time_unit: str = "",
    source_locator: str = "",
    append: bool = False,
) -> int:
    study_id, comparison, outcome, timepoint, source_key = (
        _required_text(value, name)
        for value, name in (
            (study_id, "study"), (comparison, "comparison"), (outcome, "outcome"),
            (timepoint, "timepoint"), (source_key, "source report"),
        )
    )
    if not isinstance(kind, str) or kind not in _KINDS:
        raise ValueError("kind must be proportion or rate")
    if type(events) is not int or not 0 <= events <= _SQLITE_INT_MAX:
        raise ValueError("events must be a nonnegative SQLite integer")
    if not isinstance(time_unit, str):
        raise ValueError("time unit must be text")
    time_unit = time_unit.strip()
    if kind == "proportion":
        if type(total) is not int or not 0 < total <= _SQLITE_INT_MAX or events > total:
            raise ValueError("proportion total must be a positive SQLite integer at least as large as events")
        if person_time is not None or time_unit != "":
            raise ValueError("proportions require total and cannot have person time or a time unit")
    else:
        if total is not None or not _finite_number(person_time) or person_time <= 0 or not time_unit:
            raise ValueError("rates require positive finite person time, a time unit and no total")
        person_time = float(person_time)
    if not isinstance(source_locator, str):
        raise ValueError("source locator must be text")
    source_locator = source_locator.strip()

    with closing(_connect(db)) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        _linked(conn, study_id, source_key)
        stratum = (study_id, comparison, outcome, timepoint, kind)
        saved = conn.execute(
            "SELECT result_id,selected FROM review_single_group_counts "
            "WHERE study_id=? AND comparison=? AND outcome=? AND timepoint=? AND kind=?",
            stratum,
        ).fetchall()
        if not append and saved:
            selected_ids = [result_id for result_id, selected in saved if selected]
            if len(selected_ids) > 1:
                raise ValueError("multiple selected single-group counts exist in this stratum")
            if not selected_ids:
                raise ValueError("saved count variants have no selected count; select one before updating")
            result_id = selected_ids[0]
            conn.execute(
                "UPDATE review_single_group_counts SET events=?,total=?,person_time=?,time_unit=?,"
                "source_key=?,source_locator=COALESCE(NULLIF(?,''),source_locator) WHERE result_id=?",
                (events, total, person_time, time_unit, source_key, source_locator, result_id),
            )
            return result_id

        selected = int(not saved)
        cursor = conn.execute(
            "INSERT INTO review_single_group_counts "
            "(study_id,comparison,outcome,timepoint,kind,events,total,person_time,time_unit,"
            "source_key,source_locator,selected) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (*stratum, events, total, person_time, time_unit, source_key, source_locator, selected),
        )
        return cursor.lastrowid


def list_single_group_counts(db: DbPath) -> list[dict]:
    with closing(_connect(db)) as conn:
        rows = conn.execute(
            "SELECT " + ",".join(_COLUMNS) + " FROM review_single_group_counts "
            "ORDER BY study_id,comparison,outcome,timepoint,kind,result_id"
        ).fetchall()
    return [dict(zip(_COLUMNS, row)) for row in rows]


def select_single_group_count(db: DbPath, result_id: int) -> int:
    with closing(_connect(db)) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        if type(result_id) is not int or not 0 < result_id <= _SQLITE_INT_MAX:
            raise ValueError("single-group count does not exist")
        target = conn.execute(
            "SELECT study_id,comparison,outcome,timepoint,kind FROM review_single_group_counts "
            "WHERE result_id=?",
            (result_id,),
        ).fetchone()
        if target is None:
            raise ValueError("single-group count does not exist")
        conn.execute(
            "UPDATE review_single_group_counts SET selected=0 "
            "WHERE study_id=? AND comparison=? AND outcome=? AND timepoint=? AND kind=?",
            target,
        )
        conn.execute(
            "UPDATE review_single_group_counts SET selected=1 WHERE result_id=?", (result_id,)
        )
    return result_id


def selected_single_group_counts(
    db: DbPath, comparison: str, outcome: str, timepoint: str, kind: str
) -> list[dict]:
    comparison = _required_text(comparison, "comparison")
    outcome = _required_text(outcome, "outcome")
    timepoint = _required_text(timepoint, "timepoint")
    if not isinstance(kind, str) or kind not in _KINDS:
        raise ValueError("kind must be proportion or rate")
    stratum = (comparison, outcome, timepoint, kind)
    with closing(_connect(db)) as conn:
        groups = conn.execute(
            "SELECT study_id,COUNT(*),SUM(selected) FROM review_single_group_counts "
            "WHERE comparison=? AND outcome=? AND timepoint=? AND kind=? "
            "GROUP BY study_id ORDER BY study_id",
            stratum,
        ).fetchall()
        for study_id, _, n_selected in groups:
            if n_selected == 0:
                raise ValueError(f"study {study_id} has count variants but no selected count")
            if n_selected != 1:
                raise ValueError(f"study {study_id} has multiple selected counts in this stratum")
        rows = conn.execute(
            "SELECT " + ",".join(_COLUMNS) + " FROM review_single_group_counts "
            "WHERE comparison=? AND outcome=? AND timepoint=? AND kind=? AND selected=1 "
            "ORDER BY study_id",
            stratum,
        ).fetchall()
    return [dict(zip(_COLUMNS, row)) for row in rows]
