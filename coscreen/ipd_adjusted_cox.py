"""Study-stratified Cox treatment effect adjusted for one baseline covariate."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from math import exp, fsum, isfinite, log, sqrt
from numbers import Real

import numpy as np
from scipy.stats import norm


def _number(value: object, label: str) -> float:
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, Real):
        raise ValueError(f"{label} must be a finite number")
    try:
        result = float(value)
    except (OverflowError, TypeError, ValueError):
        raise ValueError(f"{label} must be a finite number") from None
    if not isfinite(result):
        raise ValueError(f"{label} must be a finite number")
    return result


def _exp_or_none(value: float) -> float | None:
    try:
        result = exp(value)
    except OverflowError:
        return None
    return result or None


def fit_adjusted_stratified_cox(
    participants: Sequence[Mapping[str, object]],
    study_sources: Mapping[str, str],
    *,
    covariate_center: float,
    covariate_scale: float,
    ties: str = "efron",
) -> dict:
    """Fit a common treatment HR with a separate baseline hazard per study.

    Treatment is coded 0=control and 1=treatment. The prespecified numeric
    covariate is centered at ``covariate_center`` and divided by the positive
    ``covariate_scale``; its coefficient is per one scaled unit. Rows are used
    in memory only and are never returned.
    """
    if ties not in {"efron", "breslow"}:
        raise ValueError("ties must be 'efron' or 'breslow'")
    if (
        not isinstance(participants, Sequence)
        or isinstance(participants, (str, bytes))
        or not participants
    ):
        raise ValueError("participants must be a non-empty sequence of rows")
    if not isinstance(study_sources, Mapping):
        raise ValueError("study source mapping is required")
    center = _number(covariate_center, "covariate_center")
    scale = _number(covariate_scale, "covariate_scale")
    if scale <= 0:
        raise ValueError("covariate_scale must be greater than zero")

    rows_by_study: dict[str, list[tuple[float, int, int, float]]] = {}
    event_counts = [0, 0]
    for row in participants:
        if not isinstance(row, Mapping) or set(row) != {
            "study_id", "duration", "event", "treatment", "covariate"
        }:
            raise ValueError("each row needs study_id, duration, event, treatment, and covariate")
        study_id = row["study_id"]
        if not isinstance(study_id, str) or not study_id.strip():
            raise ValueError("study_id must be a non-empty string")
        duration = _number(row["duration"], "duration")
        if duration <= 0:
            raise ValueError("duration must be greater than zero")
        event = row["event"]
        treatment = row["treatment"]
        if isinstance(event, (bool, np.bool_)) or not isinstance(event, Real) or event not in (0, 1):
            raise ValueError("event must be 0 or 1")
        if isinstance(treatment, (bool, np.bool_)) or not isinstance(treatment, Real) or treatment not in (0, 1):
            raise ValueError("treatment must be 0 or 1")
        covariate = _number(row["covariate"], f"covariate for study {study_id}")
        try:
            scaled_covariate = (covariate - center) / scale
        except (OverflowError, ZeroDivisionError):
            raise ValueError("centered covariate must be finite") from None
        if not isfinite(scaled_covariate):
            raise ValueError("centered covariate must be finite")
        event, treatment = int(event), int(treatment)
        rows_by_study.setdefault(study_id, []).append(
            (duration, event, treatment, scaled_covariate)
        )
        event_counts[treatment] += event

    study_ids = set(rows_by_study)
    if set(study_sources) != study_ids or any(
        not isinstance(source, str) or not source.strip() for source in study_sources.values()
    ):
        raise ValueError("study source mapping must map every study ID to a non-empty source key")
    if not all(event_counts):
        raise ValueError("at least one event in both treatment arms is required")

    # Descending sweeps add each participant to a study-specific risk set once per fit.
    event_groups = {}
    for rows in rows_by_study.values():
        rows.sort(key=lambda row: row[0], reverse=True)
    for study_id, rows in rows_by_study.items():
        groups = {}
        for row in rows:
            if row[1]:
                groups.setdefault(row[0], []).append(row)
        event_groups[study_id] = groups
    event_total = sum(event_counts)

    def evaluate(beta: np.ndarray) -> tuple[float, np.ndarray, np.ndarray]:
        likelihood = 0.0
        score = np.zeros(2)
        information = np.zeros((2, 2))
        for study_id, rows in rows_by_study.items():
            cursor = 0
            maximum = None
            total_weight = 0.0
            weighted_x = np.zeros(2)
            weighted_xx = np.zeros((2, 2))
            for event_time, tied_rows in event_groups[study_id].items():
                while cursor < len(rows) and rows[cursor][0] >= event_time:
                    _, _, treatment, covariate = rows[cursor]
                    x = np.asarray((treatment, covariate), dtype=float)
                    try:
                        eta = fsum((float(beta[0]) * treatment, float(beta[1]) * covariate))
                    except (OverflowError, ValueError):
                        raise ValueError("linear predictor is outside numeric range") from None
                    if not isfinite(eta):
                        raise ValueError("linear predictor is outside numeric range")
                    if maximum is None:
                        maximum = eta
                    elif eta > maximum:
                        factor = exp(maximum - eta)
                        total_weight *= factor
                        weighted_x *= factor
                        weighted_xx *= factor
                        maximum = eta
                    weight = exp(eta - maximum)
                    with np.errstate(over="ignore", invalid="ignore"):
                        total_weight += weight
                        weighted_x += weight * x
                        weighted_xx += weight * np.outer(x, x)
                    cursor += 1
                if maximum is None or total_weight <= 0 or not isfinite(total_weight):
                    raise ValueError("risk set is not finite or identifiable")

                tied_x = np.zeros(2)
                tied_weight = 0.0
                tied_weighted_x = np.zeros(2)
                tied_weighted_xx = np.zeros((2, 2))
                relative_event_eta = []
                for _, _, treatment, covariate in tied_rows:
                    x = np.asarray((treatment, covariate), dtype=float)
                    try:
                        eta = fsum((float(beta[0]) * treatment, float(beta[1]) * covariate))
                    except (OverflowError, ValueError):
                        raise ValueError("linear predictor is outside numeric range") from None
                    if not isfinite(eta):
                        raise ValueError("linear predictor is outside numeric range")
                    weight = exp(eta - maximum)
                    tied_x += x
                    tied_weight += weight
                    with np.errstate(over="ignore", invalid="ignore"):
                        tied_weighted_x += weight * x
                        tied_weighted_xx += weight * np.outer(x, x)
                    relative_event_eta.append(eta - maximum)

                deaths = len(tied_rows)
                likelihood += fsum(relative_event_eta)
                score += tied_x
                tie_steps = range(deaths) if ties == "efron" else (0,)
                for tied_index in tie_steps:
                    fraction = tied_index / deaths if ties == "efron" else 0.0
                    denominator = total_weight - fraction * tied_weight
                    if denominator <= 0 or not isfinite(denominator):
                        raise ValueError("risk set is not finite or identifiable")
                    risk_x = weighted_x - fraction * tied_weighted_x
                    risk_xx = weighted_xx - fraction * tied_weighted_xx
                    mean = risk_x / denominator
                    second_moment = risk_xx / denominator
                    multiplicity = 1 if ties == "efron" else deaths
                    likelihood -= multiplicity * log(denominator)
                    score -= multiplicity * mean
                    information += multiplicity * (second_moment - np.outer(mean, mean))
                if not np.isfinite(score).all() or not np.isfinite(information).all():
                    raise ValueError("risk-set information is outside numeric range")
        return likelihood, score, information

    beta = np.zeros(2)
    tolerance = 1e-10 * max(1, event_total)
    converged = False
    for _ in range(100):
        likelihood, score, information = evaluate(beta)
        if np.max(np.abs(score)) <= tolerance:
            converged = True
            break
        try:
            step = np.linalg.solve(information, score)
        except np.linalg.LinAlgError:
            raise ValueError("treatment or covariate effect is not identifiable") from None
        direction = float(score @ step)
        if not isfinite(direction) or direction <= 0:
            raise ValueError("adjusted Cox fit failed to converge")
        fraction = 1.0
        while fraction >= 2**-30:
            candidate = beta + fraction * step
            try:
                candidate_likelihood = evaluate(candidate)[0]
            except ValueError:
                candidate_likelihood = float("-inf")
            if candidate_likelihood >= likelihood + 1e-4 * fraction * direction:
                beta = candidate
                break
            fraction *= 0.5
        else:
            raise ValueError("adjusted Cox fit failed to converge; separation or weak identification is possible")
    if not converged:
        _, score, _ = evaluate(beta)
        converged = np.max(np.abs(score)) <= tolerance
    if not converged:
        raise ValueError("adjusted Cox fit did not converge; separation or weak identification is possible")

    _, score, information = evaluate(beta)
    try:
        condition = float(np.linalg.cond(information))
        # ponytail: 1e10 condition ceiling; use covariate_scale to resolve unit-driven ill-conditioning.
        if (
            not np.isfinite(beta).all()
            or abs(float(beta[0])) >= 30
            or not np.isfinite(information).all()
            or not isfinite(condition)
            or condition > 1e10
            or np.linalg.eigvalsh(information)[0] <= 0
        ):
            raise ValueError("treatment or covariate effect is not identifiable with a finite Hessian")
        covariance = np.linalg.inv(information)
    except np.linalg.LinAlgError:
        raise ValueError("treatment or covariate effect is not identifiable with a finite Hessian") from None
    if not np.isfinite(covariance).all() or not np.isfinite(score).all():
        raise ValueError("adjusted Cox Hessian does not yield finite estimates")
    standard_errors = np.sqrt(np.diag(covariance))
    if not np.isfinite(standard_errors).all() or np.any(standard_errors <= 0):
        raise ValueError("adjusted Cox Hessian does not yield finite standard errors")

    critical = float(norm.ppf(0.975))
    treatment_low, treatment_high = beta[0] - critical * standard_errors[0], beta[0] + critical * standard_errors[0]
    covariate_low, covariate_high = beta[1] - critical * standard_errors[1], beta[1] + critical * standard_errors[1]
    return {
        "method": "study-stratified Cox proportional hazards model adjusted for one numeric baseline covariate",
        "model": "h_is(t) = h_0s(t) exp(beta_treatment * treatment + beta_covariate * (covariate - center) / scale)",
        "treatment_coding": "0=control, 1=treatment",
        "log_hr": float(beta[0]),
        "se": float(standard_errors[0]),
        "log_hr_ci_low": float(treatment_low),
        "log_hr_ci_high": float(treatment_high),
        "hazard_ratio": _exp_or_none(float(beta[0])),
        "hr_ci_low": _exp_or_none(float(treatment_low)),
        "hr_ci_high": _exp_or_none(float(treatment_high)),
        "covariate_log_hr": float(beta[1]),
        "covariate_se": float(standard_errors[1]),
        "covariate_log_hr_ci_low": float(covariate_low),
        "covariate_log_hr_ci_high": float(covariate_high),
        "covariate_hazard_ratio": _exp_or_none(float(beta[1])),
        "covariate_hr_ci_low": _exp_or_none(float(covariate_low)),
        "covariate_hr_ci_high": _exp_or_none(float(covariate_high)),
        "covariate_center": center,
        "covariate_scale": scale,
        "covariance_names": ["treatment", "covariate"],
        "covariance": covariance.tolist(),
        "confidence_level": 0.95,
        "n": len(participants),
        "events": event_total,
        "events_by_treatment": {0: event_counts[0], 1: event_counts[1]},
        "studies": len(study_ids),
        "study_sources": dict(study_sources),
        "ties": ties,
        "assumptions": [
            "common treatment and covariate log hazard ratios across studies",
            "study-specific baseline hazards and proportional hazards within each study",
            "independent right censoring conditional on treatment and covariate",
            "complete participant rows with one fixed baseline covariate",
            "no treatment-by-covariate interaction or time-varying effects",
        ],
    }
