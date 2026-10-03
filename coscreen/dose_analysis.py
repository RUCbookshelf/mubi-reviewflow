"""Two-stage linear dose-response meta-analysis with study-specific covariance."""

from __future__ import annotations

import json
import math
from contextlib import closing
from pathlib import Path

from coscreen.db import _connect
from coscreen.review_analysis import RATIOS, _linked, _synthesize_rows

DOSE_MEASURES = {"RR", "OR", "RD", "MD", "SMD", "RATE_RATIO"}


def _validate(contrasts: list[dict], covariance: list[list[float]], reference_dose: float,
              measure: str) -> None:
    import numpy as np
    if measure not in DOSE_MEASURES or not math.isfinite(reference_dose):
        raise ValueError("valid measure and finite reference dose are required")
    if len(contrasts) < 2 or any(set(row) != {"dose", "estimate"} for row in contrasts):
        raise ValueError("at least two dose contrasts with dose and estimate are required")
    doses = [float(row["dose"]) for row in contrasts]
    estimates = [float(row["estimate"]) for row in contrasts]
    if (not all(math.isfinite(x) for x in doses + estimates) or len(set(doses)) != len(doses)
            or reference_dose in doses or (measure in RATIOS and min(estimates) <= 0)):
        raise ValueError("dose and effect values must be finite, distinct and valid")
    matrix = np.asarray(covariance, dtype=float)
    if matrix.shape != (len(doses), len(doses)) or not np.isfinite(matrix).all():
        raise ValueError("covariance matrix must match the contrasts and be finite")
    if not np.allclose(matrix, matrix.T, rtol=0, atol=1e-10) or np.linalg.eigvalsh(matrix).min() <= 0:
        raise ValueError("covariance matrix must be symmetric positive definite")


def save_curve(db: str | Path, study_id: str, outcome: str, timepoint: str,
               measure: str, reference_dose: float, dose_unit: str,
               contrasts: list[dict], covariance: list[list[float]], source_key: str, *,
               input_data: dict | None = None) -> None:
    if not all(str(v).strip() for v in (study_id, outcome, timepoint, dose_unit, source_key)):
        raise ValueError("study, outcome, timepoint, dose unit and source are required")
    _validate(contrasts, covariance, reference_dose, measure)
    if input_data is not None and not isinstance(input_data, dict):
        raise ValueError("dose-curve input_data must be an object")
    audit_json = json.dumps(input_data or {}, allow_nan=False)
    with closing(_connect(db)) as conn, conn:
        _linked(conn, study_id, source_key)
        conn.execute("INSERT INTO review_dose_curves (study_id,outcome,timepoint,measure,reference_dose,dose_unit,contrasts_json,covariance_json,source_key,input_json) VALUES (?,?,?,?,?,?,?,?,?,?) "
                     "ON CONFLICT(study_id,outcome,timepoint,measure) DO UPDATE SET "
                     "reference_dose=excluded.reference_dose,dose_unit=excluded.dose_unit,"
                     "contrasts_json=excluded.contrasts_json,covariance_json=excluded.covariance_json,"
                     "source_key=excluded.source_key,input_json=excluded.input_json",
                     (study_id, outcome.strip(), timepoint.strip(), measure, reference_dose,
                      dose_unit.strip(), json.dumps(contrasts), json.dumps(covariance), source_key, audit_json))


def list_curves(db: str | Path) -> list[dict]:
    with closing(_connect(db)) as conn:
        rows = conn.execute("SELECT study_id,outcome,timepoint,measure,reference_dose,dose_unit,"
                            "contrasts_json,covariance_json,source_key,input_json FROM review_dose_curves "
                            "ORDER BY study_id,outcome,timepoint").fetchall()
    keys = ("study_id", "outcome", "timepoint", "measure", "reference_dose", "dose_unit",
            "contrasts", "covariance", "source_key", "input_data")
    return [dict(zip(keys, (*row[:6], json.loads(row[6]), json.loads(row[7]), row[8], json.loads(row[9])))) for row in rows]


def synthesize(db: str | Path, outcome: str, timepoint: str, measure: str,
               model: str = "random_pm") -> dict:
    import numpy as np
    if model not in {"fixed", "random", "random_pm"}:
        raise ValueError("invalid dose-response model")
    curves = [row for row in list_curves(db) if (row["outcome"], row["timepoint"], row["measure"]) ==
              (outcome, timepoint, measure)]
    if len(curves) < 2:
        raise ValueError("dose-response synthesis requires at least two independent studies")
    if len({row["dose_unit"] for row in curves}) != 1:
        raise ValueError("all studies need the same dose unit")
    slopes = []
    for row in curves:
        _validate(row["contrasts"], row["covariance"], row["reference_dose"], measure)
        x = np.asarray([r["dose"] - row["reference_dose"] for r in row["contrasts"]])
        y = np.asarray([math.log(r["estimate"]) if measure in RATIOS else r["estimate"]
                        for r in row["contrasts"]])
        inverse_x = np.linalg.solve(np.asarray(row["covariance"]), x)
        information = float(x @ inverse_x)
        slope = float(inverse_x @ y / information)
        slopes.append({"study_id": row["study_id"], "estimate": slope,
                       "se": math.sqrt(1/information), "source_key": row["source_key"],
                       "source_locator": row["input_data"].get("source_locator", "")})
    pooled = _synthesize_rows(slopes, "MD", model, "hksj" if model != "fixed" else "normal")
    return {"method": "two-stage linear dose-response GLS with supplied within-study covariance",
            "effect_scale": "log ratio per dose unit" if measure in RATIOS else "difference per dose unit",
            "dose_unit": curves[0]["dose_unit"], "study_slopes": slopes,
            "pooled_slope": pooled["pooled"], "ci_low": pooled["ci_low"],
            "ci_high": pooled["ci_high"], "tau2": pooled["tau2"],
            "warning": "Assumes a linear response and correctly specified within-study covariance; inspect dose shape before interpretation."}
