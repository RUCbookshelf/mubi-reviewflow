"""Persist multi-contrast network study data with supplied covariance."""

from __future__ import annotations

import json
import math
from contextlib import closing
from pathlib import Path

from coscreen.db import _connect
from coscreen.review_analysis import _linked

DbPath = str | Path
_RATIOS = {"RR", "OR"}
_MEASURES = _RATIOS | {"RD", "MD", "SMD"}
_TABLE = """
CREATE TABLE IF NOT EXISTS review_network_studies (
  study_id TEXT NOT NULL REFERENCES review_studies(id),
  outcome TEXT NOT NULL,
  timepoint TEXT NOT NULL,
  measure TEXT NOT NULL,
  contrasts_json TEXT NOT NULL,
  covariance_json TEXT NOT NULL,
  covariance_scale TEXT NOT NULL,
  source_key TEXT NOT NULL REFERENCES articles(zotero_key),
  input_arms_json TEXT,
  source_locator TEXT NOT NULL DEFAULT '',
  PRIMARY KEY(study_id,outcome,timepoint,measure)
)
"""


def _ensure_table(conn) -> None:
    conn.execute(_TABLE)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(review_network_studies)")}
    if "input_arms_json" not in columns:
        conn.execute("ALTER TABLE review_network_studies ADD COLUMN input_arms_json TEXT")
    if "source_locator" not in columns:
        conn.execute("ALTER TABLE review_network_studies ADD COLUMN source_locator TEXT NOT NULL DEFAULT ''")


def _finite_number(value) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _validate(contrasts, covariance, measure: str, covariance_scale: str) -> tuple[list[dict], list[list[float]]]:
    if measure not in _MEASURES:
        raise ValueError("measure must be RR, OR, RD, MD or SMD")
    expected_scale = "log" if measure in _RATIOS else "natural"
    if covariance_scale != expected_scale:
        raise ValueError(f"{measure} covariance must use the {expected_scale} scale")
    if not isinstance(contrasts, list) or not contrasts:
        raise ValueError("at least one contrast is required")

    clean_contrasts = []
    for contrast in contrasts:
        if not isinstance(contrast, dict) or set(contrast) != {"treatment", "comparator", "estimate"}:
            raise ValueError("each contrast must contain treatment, comparator and estimate")
        treatment, comparator = contrast["treatment"], contrast["comparator"]
        if (not isinstance(treatment, str) or not treatment.strip() or
                not isinstance(comparator, str) or not comparator.strip() or
                treatment.strip() == comparator.strip()):
            raise ValueError("contrast treatment and comparator must be distinct nonempty strings")
        estimate = contrast["estimate"]
        if not _finite_number(estimate) or (measure in _RATIOS and estimate <= 0):
            raise ValueError("contrast estimates must be finite and valid for the measure")
        clean_contrasts.append({"treatment": treatment.strip(), "comparator": comparator.strip(),
                                "estimate": float(estimate)})

    n = len(clean_contrasts)
    if (not isinstance(covariance, list) or len(covariance) != n or
            any(not isinstance(row, list) or len(row) != n for row in covariance)):
        raise ValueError("covariance matrix must be square and match the contrasts")
    clean_covariance = []
    for row in covariance:
        if any(not _finite_number(value) for value in row):
            raise ValueError("covariance values must be finite numbers")
        clean_covariance.append([float(value) for value in row])
    if any(clean_covariance[i][i] <= 0 for i in range(n)):
        raise ValueError("covariance diagonal values must be positive")
    return clean_contrasts, clean_covariance


def save_network_study(db: DbPath, study_id: str, outcome: str, timepoint: str,
                       measure: str, contrasts: list[dict], covariance: list[list[float]],
                       covariance_scale: str, source_key: str,
                       input_arms: list[dict] | None = None, source_locator: str = "") -> None:
    required = (study_id, outcome, timepoint, measure, covariance_scale, source_key)
    if any(not isinstance(value, str) or not value.strip() for value in required):
        raise ValueError("study, outcome, timepoint, measure, covariance scale and source are required")
    if not isinstance(source_locator, str) or len(source_locator) > 500:
        raise ValueError("source locator must be text of at most 500 characters")
    contrasts, covariance = _validate(contrasts, covariance, measure, covariance_scale)
    study_id, outcome, timepoint, source_key = (
        value.strip() for value in (study_id, outcome, timepoint, source_key))
    contrasts_json = json.dumps(contrasts, allow_nan=False, separators=(",", ":"))
    covariance_json = json.dumps(covariance, allow_nan=False, separators=(",", ":"))
    if input_arms is not None and (not isinstance(input_arms, list) or
                                   any(not isinstance(arm, dict) for arm in input_arms)):
        raise ValueError("input arms must be a list of arm objects")
    input_arms_json = json.dumps(input_arms, allow_nan=False, separators=(",", ":")) if input_arms is not None else None

    with closing(_connect(db)) as conn, conn:
        _ensure_table(conn)
        _linked(conn, study_id, source_key)
        conn.execute(
            "INSERT INTO review_network_studies "
            "(study_id,outcome,timepoint,measure,contrasts_json,covariance_json,covariance_scale,source_key,input_arms_json,source_locator) "
            "VALUES (?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(study_id,outcome,timepoint,measure) DO UPDATE SET "
            "contrasts_json=excluded.contrasts_json,covariance_json=excluded.covariance_json,"
            "covariance_scale=excluded.covariance_scale,source_key=excluded.source_key,"
            "input_arms_json=excluded.input_arms_json,source_locator=excluded.source_locator",
            (study_id, outcome, timepoint, measure, contrasts_json, covariance_json,
             covariance_scale, source_key, input_arms_json, source_locator.strip()),
        )


def list_network_studies(db: DbPath, outcome: str | None = None,
                         timepoint: str | None = None, measure: str | None = None) -> list[dict]:
    filters = (("outcome", outcome), ("timepoint", timepoint), ("measure", measure))
    clauses, values = [], []
    for column, value in filters:
        if value is not None:
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{column} filter must be a nonempty string")
            clauses.append(f"{column}=?")
            values.append(value.strip())
    query = ("SELECT study_id,outcome,timepoint,measure,contrasts_json,covariance_json,"
             "covariance_scale,source_key,input_arms_json,source_locator FROM review_network_studies")
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY study_id,outcome,timepoint,measure"
    with closing(_connect(db)) as conn, conn:
        _ensure_table(conn)
        rows = conn.execute(query, values).fetchall()
    keys = ("study_id", "outcome", "timepoint", "measure", "contrasts", "covariance",
            "covariance_scale", "source_key")
    return [{**dict(zip(keys, (*row[:4], json.loads(row[4]), json.loads(row[5]), row[6], row[7]))),
             **({"input_arms": json.loads(row[8])} if row[8] is not None else {}),
             "source_locator": row[9]}
            for row in rows]
