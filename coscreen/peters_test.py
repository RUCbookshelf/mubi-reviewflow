"""Peters regression test for funnel plot asymmetry in proportion meta-analyses.

Implements the single-proportion variant of Peters et al. (2006), *JAMA*
295(6):676-680, doi:10.1001/jama.295.6.676, exactly as R meta 8.5-0 computes
``metabias(metaprop(event, n), method.bias = "Peters")``: a weighted linear
regression of the logit proportion (log(e/(n-e))) on the inverse of the total
sample size (1/n) with weights equal to the inverse Wald variance of the logit
proportion, w = 1/(1/e + 1/(n-e)) = e(n-e)/n.  The test statistic is the
Student t ratio of the slope to its standard error with residual degrees of
freedom k - 2.  Studies at a proportion boundary (events = 0 or events =
total) have zero weight under this construction and are excluded from the fit
and from the degrees of freedom, matching the behaviour of the
``lm(..., weights = 1/vi)`` fit that meta performs internally.
"""

from __future__ import annotations

import copy
import math

import numpy as np
from scipy.stats import t as student_t

_METHOD = "peters_test"
_METHOD_NAME = "Peters linear regression test of funnel plot asymmetry (single-proportion variant)"
_SOURCE = (
    "Peters JL, Sutton AJ, Jones DR, Abrams KR, Rushton L (2006) JAMA 295(6):676-680, "
    "doi:10.1001/jama.295.6.676; single-proportion variant as implemented in "
    "R meta::metabias(method.bias='Peters') for metaprop objects"
)
_LIMITATION = (
    "Funnel plot asymmetry can reflect publication bias, reporting bias, chance, heterogeneity, or "
    "genuine small-study effects; a Peters test result does not prove publication bias and does not "
    "explain why studies are missing."
)
_BOUNDARY_NOTE = (
    "Boundary studies (events=0 or events=total) carry zero weight because the Peters weights "
    "e(n-e)/n are zero there, and are excluded from the regression and its degrees of freedom; "
    "this mirrors meta 8.5-0, whose zero-cell continuity correction changes only the excluded "
    "study's displayed logit estimate and not the test."
)


def _finite_number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number")
    try:
        number = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if not math.isfinite(number):
        raise ValueError(f"{name} must be a finite number")
    return number


def _integer_count(value: object, name: str) -> float:
    number = _finite_number(value, name)
    if not float(number).is_integer():
        raise ValueError(f"{name} must be a whole number of participants/events, not a non-integer count")
    return number


def _validated_rows(rows: list[dict]) -> tuple[list[str], list[float], list[float]]:
    if not isinstance(rows, list) or len(rows) < 3:
        raise ValueError("at least three independent study rows are required")

    study_ids: list[str] = []
    events: list[float] = []
    totals: list[float] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(f"row at index {index} must be a dictionary")
        study_id = row.get("study_id")
        if not isinstance(study_id, str) or not study_id.strip():
            raise ValueError(f"row at index {index} needs a nonempty study_id")
        if study_id in study_ids:
            raise ValueError("rows must have a unique study_id per independent study")
        study_ids.append(study_id)
        try:
            total = _integer_count(row["total"], "total")
            event = _integer_count(row["events"], "events")
        except KeyError as exc:
            raise ValueError("each row needs events and total counts") from exc
        if total <= 0:
            raise ValueError("total must be a positive integer count")
        if event < 0:
            raise ValueError("events must not be negative")
        if event > total:
            raise ValueError("events must not exceed total")
        events.append(event)
        totals.append(total)
    return study_ids, events, totals


def _included(study_ids: list[str], events: list[float], totals: list[float]):
    """Split rows into the weighted-fit sample and zero-weight boundary rows.

    Returns (kept index list, excluded descriptor list) mirroring meta 8.5-0,
    where the weighted lm fit drops rows whose Peters weight e(n-e)/n is zero.
    """
    kept: list[int] = []
    excluded: list[dict] = []
    for index, (event, total) in enumerate(zip(events, totals)):
        if event == 0 or event == total:
            excluded.append({
                "study_id": study_ids[index],
                "events": int(event),
                "total": int(total),
                "weight": 0.0,
                "reason": (
                    "boundary proportion: all events"
                    if event == total else "boundary proportion: zero events"
                ),
            })
        else:
            kept.append(index)
    return kept, excluded


def _peters_regression(y: np.ndarray, x: np.ndarray, w: np.ndarray) -> dict:
    """Weighted least squares of y on [1, x] with the slope tested by Student t."""
    with np.errstate(over="raise", invalid="raise"):
        design = np.column_stack((np.ones(len(y)), x))
        if np.linalg.matrix_rank(design) < 2:
            raise ValueError(
                "Peters regression is rank deficient because the predictor 1/total has no variation"
            )
        try:
            information = design.T @ (w[:, None] * design)
            information_inverse = np.linalg.inv(information)
            beta = information_inverse @ (design.T @ (w * y))
            residuals = y - design @ beta
            residual_q = float(np.sum(w * residuals * residuals))
            df = len(y) - 2
            sigma_squared = residual_q / df
            standard_errors = np.sqrt(information_inverse.diagonal() * sigma_squared)
        except (FloatingPointError, np.linalg.LinAlgError, OverflowError) as exc:
            raise ValueError("Peters regression is numerically unstable for the supplied counts") from exc
    if not np.isfinite(beta).all() or not np.isfinite(standard_errors).all():
        raise ValueError("Peters regression produced non-finite statistics")
    return {
        "design": design,
        "beta": beta,
        "standard_errors": standard_errors,
        "residual_sigma": math.sqrt(sigma_squared),
        "residual_df": int(df),
    }


def _coefficient(estimate: float, standard_error: float, df: int) -> dict:
    critical = float(student_t.ppf(.975, df))
    return {
        "estimate": estimate,
        "se": standard_error,
        "ci_low": estimate - critical * standard_error,
        "ci_high": estimate + critical * standard_error,
        "ci_method": f"Student t 95% with df = {df}",
    }


def peters_test(rows: list[dict], *, source_provenance: dict | None = None) -> dict:
    """Return the Peters regression test for one proportion per study.

    Each row holds one independent study as ``events`` and ``total`` integer
    counts for a single proportion (the module is not applicable to combined
    effect-size rows, which lack the raw counts the weights require).  The
    response is the logit proportion log(events/(total-events)), the predictor
    is 1/total, and the weight is events*(total-events)/total, so boundary
    studies carry zero weight and are excluded and reported.  The asymmetry
    test is the Student t test of the slope on 1/total with residual degrees
    of freedom (included studies - 2): a positive slope means smaller studies
    report larger proportions, a negative slope the reverse.
    """
    if source_provenance is not None and not isinstance(source_provenance, dict):
        raise ValueError("source_provenance must be an object")

    study_ids, events, totals = _validated_rows(rows)
    kept, excluded = _included(study_ids, events, totals)
    if len(kept) < 3:
        raise ValueError(
            "Peters regression needs at least three non-boundary studies; boundary studies "
            "(events=0 or events=total) carry zero weight and are excluded"
        )

    try:
        with np.errstate(over="raise", invalid="raise"):
            y = np.array([math.log(events[i] / (totals[i] - events[i])) for i in kept])
            x = np.array([1 / totals[i] for i in kept])
            w = np.array([events[i] * (totals[i] - events[i]) / totals[i] for i in kept])
    except (FloatingPointError, OverflowError) as exc:
        raise ValueError("study counts are outside the supported numeric range") from exc

    fit = _peters_regression(y, x, w)
    df = fit["residual_df"]
    slope, intercept = float(fit["beta"][1]), float(fit["beta"][0])
    slope_se, intercept_se = float(fit["standard_errors"][1]), float(fit["standard_errors"][0])
    statistic = slope / slope_se
    p_value = float(2 * student_t.sf(abs(statistic), df))

    n_studies, n_included = len(study_ids), len(kept)
    small_study_warning = (
        "Fewer than 10 studies entered the regression: interpret cautiously because power and "
        "calibration may be limited; 10 is the rule-of-thumb recommendation of Sterne et al. (2011) "
        "and meta's default k.min, not a validity cutoff."
        if n_included < 10 else None
    )
    warnings = [
        _BOUNDARY_NOTE,
        "The test targets the slope on 1/total in a fixed-effects weighted regression; it assumes "
        "independent studies of one proportion on one outcome and time point.",
    ]
    if small_study_warning is not None:
        warnings.insert(0, small_study_warning)

    result = {
        "method": _METHOD,
        "method_name": _METHOD_NAME,
        "source": _SOURCE,
        "outcome_type": "single-arm proportion (events/total counts)",
        "response_scale": "logit(proportion)",
        "predictor": "inverse of total sample size (1/total)",
        "weights": "events * (total - events) / total, the inverse Wald variance of the logit proportion",
        "n_studies": n_studies,
        "n_included": n_included,
        "excluded_studies": excluded,
        "boundary_handling": _BOUNDARY_NOTE,
        "coefficients": {
            "intercept": _coefficient(intercept, intercept_se, df),
            "slope_on_inverse_total": _coefficient(slope, slope_se, df),
        },
        "statistic_name": "t (slope / se.slope on 1/total)",
        "statistic": statistic,
        "df": df,
        "p_value": p_value,
        "p_value_method": f"two-sided Student t with residual df = included studies - 2 = {df}",
        "residual_sigma": fit["residual_sigma"],
        "direction_interpretation": (
            "positive slope: smaller studies report larger proportions; negative slope: smaller "
            "studies report smaller proportions; neither direction proves publication bias"
        ),
        "small_study_warning": small_study_warning,
        "warnings": warnings,
        "assumptions": [
            "Each row is one independent study of a single proportion; the same comparison, outcome, "
            "and time point apply to all rows.",
            "The regression reproduces meta 8.5-0 metabias(metaprop(...), method.bias='Peters'): "
            "logit proportion regressed on 1/total with weights e(total-e)/total (Peters et al. 2006, "
            "a Macaskill et al. 2001 FPV variant using the inverse sample size as covariate).",
            "Asymmetry is assessed by the slope t test with df = included studies - 2; meta conducts "
            "the test by default only for k >= 10 (k.min), with a minimum of three studies.",
        ],
        "limitation": _LIMITATION,
        "input_data": copy.deepcopy(rows),
        "source_provenance": copy.deepcopy(source_provenance) if source_provenance is not None else None,
    }
    return result
