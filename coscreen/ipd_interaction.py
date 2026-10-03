"""One-stage logistic treatment-covariate interaction from in-memory IPD."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from numbers import Real

import numpy as np
from scipy.optimize import linprog
from scipy.special import expit, log_expit
from scipy.stats import norm


def _number(value: object, label: str) -> float:
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, Real):
        raise ValueError(f"{label} must be a finite number")
    try:
        result = float(value)
    except (OverflowError, TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a finite number") from exc
    if not math.isfinite(result):
        raise ValueError(f"{label} must be a finite number")
    return result


def _separated(design: np.ndarray, outcome: np.ndarray) -> bool:
    """Check for a weak separating direction with a small linear program."""
    signed = design * (2 * outcome - 1)[:, None]
    columns = design.shape[1]
    constraints = np.column_stack((-signed, signed))
    constraints = np.vstack((constraints, np.ones((1, 2 * columns))))
    limits = np.r_[np.zeros(len(outcome)), 1.0]
    objective = np.r_[-signed.sum(axis=0), signed.sum(axis=0)]
    fit = linprog(
        objective,
        A_ub=constraints,
        b_ub=limits,
        bounds=(0, None),
        method="highs",
        options={"primal_feasibility_tolerance": 1e-9,
                 "dual_feasibility_tolerance": 1e-9},
    )
    if not fit.success:
        raise ValueError("could not assess logistic separation")
    return -float(fit.fun) / len(outcome) > 1e-9


def _exp_or_none(value: float) -> float | None:
    try:
        result = math.exp(value)
    except OverflowError:
        return None
    return result or None


def fit_ipd_interaction(
    rows: Sequence[Mapping[str, object]],
    study_sources: Mapping[str, str],
    *,
    covariate_center: float,
) -> dict:
    """Fit one-stage fixed-study-intercept logistic regression to participant rows.

    The model separates a participant's covariate deviation from their study
    mean and the study mean's deviation from ``covariate_center``. Separate
    treatment interactions let the within-study participant interaction be
    distinguished from the across-study association. Rows are used in memory
    only and are neither retained nor returned.
    """
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)) or not rows:
        raise ValueError("rows must be a non-empty sequence of participant mappings")
    if not isinstance(study_sources, Mapping):
        raise ValueError("study source mapping is required")
    center = _number(covariate_center, "covariate_center")

    parsed: list[tuple[str, int, int, float]] = []
    by_study: dict[str, list[tuple[int, int, float]]] = {}
    for row in rows:
        if not isinstance(row, Mapping) or set(row) != {
            "study_id", "treatment", "outcome", "covariate"
        }:
            raise ValueError("each row needs study_id, treatment, outcome, and covariate")
        study_id = row["study_id"]
        if not isinstance(study_id, str) or not study_id.strip():
            raise ValueError("study_id must be a non-empty string")
        treatment = row["treatment"]
        outcome = row["outcome"]
        if isinstance(treatment, (bool, np.bool_)) or not isinstance(treatment, Real) or treatment not in (0, 1):
            raise ValueError("treatment must be 0 or 1")
        if isinstance(outcome, (bool, np.bool_)) or not isinstance(outcome, Real) or outcome not in (0, 1):
            raise ValueError("outcome must be 0 or 1")
        covariate = _number(row["covariate"], f"covariate for study {study_id}")
        treatment, outcome = int(treatment), int(outcome)
        parsed.append((study_id, treatment, outcome, covariate))
        by_study.setdefault(study_id, []).append((treatment, outcome, covariate))

    study_ids = list(by_study)
    if len(study_ids) < 2:
        raise ValueError("at least two studies are required")
    if set(study_sources) != set(study_ids) or any(
        not isinstance(source, str) or not source.strip() for source in study_sources.values()
    ):
        raise ValueError("study source mapping must map every study ID to a non-empty source key")

    means: dict[str, float] = {}
    for study_id, observations in by_study.items():
        arms = {arm: [x for treatment, _, x in observations if treatment == arm]
                for arm in (0, 1)}
        outcomes = {outcome for _, outcome, _ in observations}
        if not arms[0] or not arms[1]:
            raise ValueError(f"study {study_id} must include both treatment groups")
        if outcomes != {0, 1}:
            raise ValueError(f"study {study_id} must include both outcome values")
        # ponytail: range overlap is a cheap support screen; use distributional overlap diagnostics when positivity decisions matter.
        overlap_low = max(min(arms[0]), min(arms[1]))
        overlap_high = min(max(arms[0]), max(arms[1]))
        if overlap_low >= overlap_high:
            raise ValueError(f"study {study_id} has insufficient within-study covariate overlap")
        means[study_id] = math.fsum(x / len(observations) for _, _, x in observations)

    study_index = {study_id: index for index, study_id in enumerate(study_ids)}
    y = np.asarray([outcome for _, _, outcome, _ in parsed], dtype=float)
    treatment = np.asarray([arm for _, arm, _, _ in parsed], dtype=float)
    study_mean = np.asarray([means[study_id] for study_id, _, _, _ in parsed], dtype=float)
    covariate = np.asarray([x for _, _, _, x in parsed], dtype=float)
    within = covariate - study_mean
    between = study_mean - center
    if not np.isfinite(within).all() or not np.isfinite(between).all():
        raise ValueError("centered covariate values must be finite")

    intercepts = np.asarray(
        [[float(study_id == current) for current in study_ids] for study_id, _, _, _ in parsed],
        dtype=float,
    )
    design = np.column_stack((
        intercepts,
        treatment,
        within,
        treatment * within,
        treatment * between,
    ))
    names = (
        [f"study_intercept[{study_id}]" for study_id in study_ids]
        + ["treatment", "covariate_within_study", "treatment:covariate_within_study",
           "treatment:covariate_between_study"]
    )
    scales = np.max(np.abs(design), axis=0)
    if np.any(scales == 0):
        raise ValueError("design matrix is singular")
    scaled_design = design / scales
    if len(rows) <= design.shape[1] or np.linalg.matrix_rank(scaled_design) != design.shape[1]:
        raise ValueError("design matrix is singular or insufficiently identified")
    if _separated(scaled_design, y):
        raise ValueError("logistic outcome is separated; finite maximum likelihood estimates do not exist")

    def log_likelihood(coefficients: np.ndarray) -> float:
        eta = scaled_design @ coefficients
        return float(np.sum(y * log_expit(eta) + (1 - y) * log_expit(-eta)))

    coefficients_scaled = np.zeros(design.shape[1], dtype=float)
    converged = False
    for _ in range(100):
        eta = scaled_design @ coefficients_scaled
        probability = expit(eta)
        score = scaled_design.T @ (y - probability)
        if np.max(np.abs(score)) <= 1e-11 * max(1, len(rows)):
            converged = True
            break
        weights = probability * (1 - probability)
        information = scaled_design.T @ (weights[:, None] * scaled_design)
        try:
            step = np.linalg.solve(information, score)
        except np.linalg.LinAlgError as exc:
            raise ValueError("logistic Hessian is singular") from exc
        direction = float(score @ step)
        if not math.isfinite(direction) or direction <= 0:
            raise ValueError("logistic MLE failed to converge")
        old_likelihood = log_likelihood(coefficients_scaled)
        fraction = 1.0
        while fraction >= 2**-30:
            candidate = coefficients_scaled + fraction * step
            candidate_likelihood = log_likelihood(candidate)
            if candidate_likelihood >= old_likelihood + 1e-4 * fraction * direction:
                coefficients_scaled = candidate
                break
            fraction *= 0.5
        else:
            raise ValueError("logistic MLE failed to converge")
    if not converged:
        eta = scaled_design @ coefficients_scaled
        score = scaled_design.T @ (y - expit(eta))
        converged = np.max(np.abs(score)) <= 1e-11 * max(1, len(rows))
    if not converged:
        raise ValueError("logistic MLE did not converge; separation or weak identification is possible")

    eta = scaled_design @ coefficients_scaled
    if not np.isfinite(eta).all() or np.max(np.abs(eta)) >= 30:
        raise ValueError("logistic MLE is extreme or numerically separated")
    probability = expit(eta)
    information = scaled_design.T @ ((probability * (1 - probability))[:, None] * scaled_design)
    try:
        condition = float(np.linalg.cond(information))
        sign, logdet = np.linalg.slogdet(information)
        if sign <= 0 or not math.isfinite(logdet) or not math.isfinite(condition) or condition > 1e12:
            raise ValueError("logistic Hessian is singular or poorly conditioned")
        covariance_scaled = np.linalg.inv(information)
    except np.linalg.LinAlgError as exc:
        raise ValueError("logistic Hessian is singular") from exc

    try:
        with np.errstate(over="raise", divide="raise", invalid="raise"):
            estimates = coefficients_scaled / scales
            covariance = covariance_scaled / scales[:, None] / scales[None, :]
    except FloatingPointError as exc:
        raise ValueError("logistic coefficient or covariance is outside numeric range") from exc
    if not np.isfinite(estimates).all() or not np.isfinite(covariance).all():
        raise ValueError("logistic coefficient or covariance is outside numeric range")
    standard_errors = np.sqrt(np.diag(covariance))
    if not np.isfinite(standard_errors).all() or np.any(standard_errors <= 0):
        raise ValueError("logistic Hessian does not yield finite standard errors")
    critical = float(norm.ppf(0.975))
    coefficients = {
        name: {
            "estimate": float(estimate),
            "se": float(se),
            "ci95": [float(estimate - critical * se), float(estimate + critical * se)],
        }
        for name, estimate, se in zip(names, estimates, standard_errors)
    }

    def odds_ratio(index: int) -> dict:
        estimate = float(estimates[index])
        se = float(standard_errors[index])
        return {
            "or": _exp_or_none(estimate),
            "ci95": [_exp_or_none(estimate - critical * se),
                     _exp_or_none(estimate + critical * se)],
        }

    treatment_index = len(study_ids)
    within_interaction_index = treatment_index + 2
    between_interaction_index = treatment_index + 3
    event_counts = [sum(outcome for _, arm, outcome, _ in parsed if arm == treatment_value)
                    for treatment_value in (0, 1)]
    treatment_reference = odds_ratio(treatment_index)
    treatment_reference["reference"] = (
        "within-study covariate deviation 0 and study mean equal to covariate_center; "
        "this reference may be hypothetical"
    )
    return {
        "method": "one-stage logistic regression with study-specific fixed intercepts",
        "model": "logit P(outcome=1) = study intercept + treatment + within-study covariate "
                 "+ treatment×within-study covariate + treatment×study-mean covariate",
        "covariate_center": center,
        "coefficients": coefficients,
        "covariance_names": names,
        "covariance": covariance.tolist(),
        "treatment_or_at_reference": treatment_reference,
        "within_study_interaction_or_per_unit": odds_ratio(within_interaction_index),
        "between_study_interaction_or_per_unit": odds_ratio(between_interaction_index),
        "n": len(parsed),
        "events": sum(event_counts),
        "events_by_treatment": {0: event_counts[0], 1: event_counts[1]},
        "studies": len(study_ids),
        "study_sources": dict(study_sources),
        "confidence_level": 0.95,
        "assumptions": [
            "independent binary outcomes conditional on the model terms",
            "linear covariate effects and treatment interactions on the log-odds scale",
            "common treatment, within-study interaction, and between-study interaction coefficients",
            "study-specific fixed intercepts with no random effects",
            "Wald confidence intervals use the inverse observed logistic Hessian and a normal reference",
            "complete participant rows with no missing fields",
        ],
    }
