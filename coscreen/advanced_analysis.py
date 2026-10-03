"""Exploratory study-level analyses built on the validated pairwise effect table."""

from __future__ import annotations

import math
from collections import defaultdict
from contextlib import closing
from pathlib import Path

from coscreen.db import _connect
from coscreen.review_analysis import RATIOS, SINGLE_GROUP, _synthesize_rows, selected_effects

DbPath = str | Path


def _effects(db: DbPath, comparison: str, outcome: str, timepoint: str,
             measure: str, study_ids: list[str] | None = None) -> list[dict]:
    rows = selected_effects(db, comparison, outcome, timepoint, measure)
    if study_ids is not None:
        if len(set(study_ids)) != len(study_ids):
            raise ValueError("duplicate study id in selection")
        rows = [r for r in rows if r["study_id"] in set(study_ids)]
        if len(rows) != len(study_ids):
            raise ValueError("selected study has no compatible result")
    return rows


def _characteristics(db: DbPath, rows: list[dict], dimension_id: int) -> dict[str, str]:
    with closing(_connect(db)) as conn:
        if conn.execute("SELECT 1 FROM coding_dimensions WHERE id=?", (dimension_id,)).fetchone() is None:
            raise ValueError("coding dimension does not exist")
        data = conn.execute("SELECT rr.study_id,trim(cv.value) FROM review_reports rr "
                            "JOIN coding_values cv ON cv.zotero_key=rr.zotero_key "
                            "WHERE cv.dimension_id=? AND trim(cv.value)<>''", (dimension_id,)).fetchall()
    values: dict[str, set[str]] = defaultdict(set)
    for study_id, value in data:
        values[study_id].add(value)
    result = {}
    for row in rows:
        sid = row["study_id"]
        if len(values[sid]) != 1:
            raise ValueError(f"study {sid} has missing or conflicting characteristic")
        result[sid] = next(iter(values[sid]))
    return result


def subgroup(db: DbPath, comparison: str, outcome: str, timepoint: str,
             measure: str, dimension_id: int) -> dict:
    from scipy.stats import chi2
    rows = _effects(db, comparison, outcome, timepoint, measure)
    if len(rows) < 4:
        raise ValueError("subgroup analysis requires at least four studies")
    values = _characteristics(db, rows, dimension_id)
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[values[row["study_id"]]].append(row)
    if len(grouped) < 2 or any(len(group) < 2 for group in grouped.values()):
        raise ValueError("each of at least two subgroups needs two independent studies")
    pooled = {name: _synthesize_rows(group, measure, "fixed", "normal")
              for name, group in sorted(grouped.items())}
    overall = _synthesize_rows(rows, measure, "fixed", "normal")
    q_between = max(0.0, overall["q"] - sum(p["q"] for p in pooled.values()))
    return {"subgroups": pooled, "q_between": q_between, "df_between": len(pooled)-1,
            "p_interaction": float(chi2.sf(q_between, len(pooled)-1)),
            "method": "fixed-effect Q interaction test",
            "warning": "Exploratory study-level comparison; prespecify groups and avoid causal interpretation."}


def meta_regression(db: DbPath, comparison: str, outcome: str, timepoint: str,
                    measure: str, dimension_id: int) -> dict:
    import numpy as np
    from statsmodels.regression.linear_model import WLS
    rows = _effects(db, comparison, outcome, timepoint, measure)
    if len(rows) < 10:
        raise ValueError("meta-regression requires at least ten independent studies")
    values = _characteristics(db, rows, dimension_id)
    try:
        x = np.asarray([float(values[r["study_id"]]) for r in rows])
    except ValueError as exc:
        raise ValueError("meta-regression characteristic must be numeric") from exc
    if not np.isfinite(x).all() or len(set(x)) < 2:
        raise ValueError("meta-regression characteristic needs at least two finite values")
    y = np.asarray([math.log(r["estimate"]) if measure in RATIOS else r["estimate"] for r in rows])
    baseline = _synthesize_rows(rows, measure, "random_pm", "normal")
    tau2 = baseline["tau2"]
    weights = np.asarray([1/(r["se"]**2+tau2) for r in rows])
    fit = WLS(y, np.column_stack((np.ones(len(x)), x)), weights=weights).fit()
    return {"n_studies": len(rows), "slope": float(fit.params[1]),
            "slope_se": float(fit.bse[1]), "slope_ci": [float(v) for v in fit.conf_int()[1]],
            "p": float(fit.pvalues[1]), "intercept": float(fit.params[0]),
            "tau2_from_intercept_only_model": tau2,
            "effect_scale": "logit" if measure == "LOGIT_PROP" else
                            "log" if measure in RATIOS | {"LOG_RATE", "LOG_ROM", "PAIRED_OR"} else "natural",
            "method": "exploratory inverse-variance WLS with fixed intercept-only Paule-Mandel tau2",
            "warning": "Study-level association is not a participant-level interaction or causal effect."}


def small_study_effects(db: DbPath, comparison: str, outcome: str, timepoint: str,
                        measure: str, study_ids: list[str] | None = None) -> dict:
    import numpy as np
    from statsmodels.regression.linear_model import OLS
    if measure in SINGLE_GROUP:
        raise ValueError("Egger regression is not validated for these one-group estimates")
    rows = _effects(db, comparison, outcome, timepoint, measure, study_ids)
    if len(rows) < 10:
        raise ValueError("Egger test requires at least ten independent studies")
    se = np.asarray([r["se"] for r in rows])
    if len(set(se)) < 2:
        raise ValueError("Egger test needs variation in study precision")
    y = np.asarray([math.log(r["estimate"]) if measure in RATIOS else r["estimate"] for r in rows])
    fit = OLS(y/se, np.column_stack((np.ones(len(se)), 1/se))).fit()
    return {"n_studies": len(rows), "points": [{"study_id": r["study_id"],
            "result_id": r["result_id"], "source_key": r["source_key"],
            "source_locator": r["source_locator"],
            "effect": float(effect), "se": float(error)} for r, effect, error in zip(rows, y, se)],
            "egger_intercept": float(fit.params[0]), "egger_p": float(fit.pvalues[0]),
            "method": "Egger regression of standardized effect on precision",
            "method_sources": ["Egger et al. (1997), BMJ 315:629–634, doi:10.1136/bmj.315.7109.629"],
            "warning": "Asymmetry is a small-study effect, not proof of publication bias; effect-SE correlation may distort binary-effect tests."}


def cumulative(db: DbPath, comparison: str, outcome: str, timepoint: str,
               measure: str, year_dimension_id: int, model: str = "fixed") -> dict:
    rows = _effects(db, comparison, outcome, timepoint, measure)
    values = _characteristics(db, rows, year_dimension_id)
    groups: dict[int, list[dict]] = defaultdict(list)
    for row in rows:
        value = values[row["study_id"]]
        if not (len(value) == 4 and value.isascii() and value.isdecimal()):
            raise ValueError("study year must be four digits")
        groups[int(value)].append(row)
    included, steps = [], []
    for year in sorted(groups):
        included.extend(groups[year])
        if len(included) >= 2:
            result = _synthesize_rows(included, measure, model, "normal")
            steps.append({"through_year": year, "n_studies": len(included),
                          "pooled": result["pooled"], "ci_low": result["ci_low"],
                          "ci_high": result["ci_high"]})
    return {"steps": steps, "method": f"cumulative {model} synthesis by explicitly coded study year"}
