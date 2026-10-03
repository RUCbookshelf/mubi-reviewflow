"""PET and PEESE inverse-variance WLS sensitivity fits for independent studies."""

from __future__ import annotations

import math
from numbers import Real
from statistics import NormalDist

import numpy as np

_RATIOS = {"RR", "OR", "HR", "RATE_RATIO"}
_MEASURES = _RATIOS | {"RD", "MD", "SMD", "FISHER_Z"}
_Z_975 = NormalDist().inv_cdf(.975)


def _number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{name} must be a finite number")
    try:
        result = float(value)
    except (OverflowError, TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name} must be a finite number")
    return result


def _validated_rows(rows: list[dict], measure: str) -> tuple[list[str], np.ndarray, np.ndarray]:
    if not isinstance(measure, str) or measure not in _MEASURES:
        raise ValueError(f"measure must be one of {', '.join(sorted(_MEASURES))}")
    if not isinstance(rows, list) or len(rows) < 3:
        raise ValueError("at least three independent studies are required for positive residual degrees of freedom")

    study_ids: list[str] = []
    effects: list[float] = []
    standard_errors: list[float] = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("each row must be a dictionary")
        study_id = row.get("study_id")
        if not isinstance(study_id, str) or not study_id.strip():
            raise ValueError("each row needs a nonempty study_id")
        study_ids.append(study_id)
        if row.get("measure") != measure:
            raise ValueError("all row measures must match the requested measure")
        try:
            effect = _number(row["estimate"], "estimate")
            se = _number(row["se"], "se")
        except KeyError as exc:
            raise ValueError("each row needs estimate and se") from exc
        if se <= 0:
            raise ValueError("standard errors must be positive")
        if measure in _RATIOS:
            if effect <= 0:
                raise ValueError("ratio estimates must be positive")
            effect = math.log(effect)
        variance = se * se
        if not math.isfinite(variance) or variance <= 0:
            raise ValueError("standard errors must imply a finite positive variance")
        try:
            weight = 1 / variance
        except OverflowError as exc:
            raise ValueError("study precisions are outside the supported numeric range") from exc
        if not math.isfinite(weight) or weight <= 0:
            raise ValueError("study precisions are outside the supported numeric range")
        effects.append(effect)
        standard_errors.append(se)

    if len(study_ids) != len(set(study_ids)):
        raise ValueError("rows must have a unique study_id per independent study")
    return study_ids, np.asarray(effects), np.asarray(standard_errors)


def _coefficient(estimate: float, standard_error: float) -> dict:
    low, high = estimate - _Z_975 * standard_error, estimate + _Z_975 * standard_error
    if not all(math.isfinite(value) for value in (estimate, standard_error, low, high)):
        raise ValueError("PET/PEESE coefficient statistics are outside the supported numeric range")
    return {"estimate": estimate, "se": standard_error, "ci_low": low, "ci_high": high}


def _fit(effects: np.ndarray, ses: np.ndarray, predictor: np.ndarray, method: str, model: str) -> dict:
    try:
        with np.errstate(over="raise", divide="raise", invalid="raise"):
            variances = ses * ses
            weights = 1 / variances
            design = np.column_stack((np.ones(len(effects)), predictor))
            if np.linalg.matrix_rank(design) < 2:
                raise ValueError(f"{method} regression is rank deficient because its predictor has no variation")
            information = design.T @ (weights[:, None] * design)
            covariance = np.linalg.solve(information, np.eye(2))
            beta = np.linalg.solve(information, design.T @ (weights * effects))
            residuals = effects - design @ beta
            residual_q = float(np.sum(weights * residuals * residuals))
    except (FloatingPointError, np.linalg.LinAlgError) as exc:
        raise ValueError(f"{method} regression is numerically unstable for the supplied estimates") from exc
    standard_errors = np.sqrt(np.diag(covariance))
    if (not np.isfinite(beta).all() or not np.isfinite(standard_errors).all()
            or np.any(standard_errors <= 0) or not math.isfinite(residual_q)):
        raise ValueError("PET/PEESE regression produced non-finite statistics")
    return {
        "model": model,
        "weights": "1 / se^2",
        "coefficients": {
            "intercept": _coefficient(float(beta[0]), float(standard_errors[0])),
            "slope": _coefficient(float(beta[1]), float(standard_errors[1])),
        },
        "residual_q": residual_q,
        "residual_df": len(effects) - 2,
    }


def diagnose_pet_peese(rows: list[dict], measure: str) -> dict:
    """Fit PET (effect ~ SE) and PEESE (effect ~ sampling variance).

    Rows follow ``selected_effects``: one selected, independent study result
    per row with ``study_id``, ``measure``, ``estimate``, and ``se``. Ratio
    estimates are logged; their supplied standard errors must already be on
    the log scale. Both fits use inverse-variance WLS and normal 95% intervals.
    """
    study_ids, effects, ses = _validated_rows(rows, measure)
    with np.errstate(over="raise", invalid="raise"):
        variances = ses * ses
        pet = _fit(effects, ses, ses, "PET", "effect ~ se")
        peese = _fit(effects, ses, variances, "PEESE", "effect ~ variance")
    n = len(study_ids)
    return {
        "method": "PET/PEESE inverse-variance WLS sensitivity",
        "measure": measure,
        "effect_scale": "log" if measure in _RATIOS else "fisher_z" if measure == "FISHER_Z" else "natural",
        "n_studies": n,
        "study_ids": study_ids,
        "ci_method": "normal 95% Wald; known-sampling-variance WLS",
        "small_study_warning": (
            "Fewer than 10 studies: interpret cautiously because regression power and calibration may be limited; "
            "10 is a rule of thumb, not a validity cutoff."
            if n < 10 else None
        ),
        "conditional_selection": None,
        "fits": {"PET": pet, "PEESE": peese},
        "assumptions": [
            "Each row is one independent study effect for the same comparison, outcome, time point, and measure.",
            "PET regresses effect on SE; PEESE regresses effect on SE squared; both use weights 1/SE squared.",
            "The intercept is the fitted effect extrapolated to SE=0, and intervals use the known sampling variances.",
            "Ratio estimates are analyzed on the log scale, with standard errors already on that scale; FISHER_Z remains on the Fisher z scale.",
            "PET and PEESE are reported separately; no conditional estimate-selection rule is applied.",
        ],
        "limitation": (
            "Small-study patterns can reflect heterogeneity, chance, or study-design differences; these fits do not establish "
            "publication or outcome-reporting bias and do not provide a causal explanation."
        ),
    }
