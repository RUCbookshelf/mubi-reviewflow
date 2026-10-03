"""Extended IPD Cox models: multi-covariate adjustment and two-stage random slopes."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from math import exp, fsum, isfinite, log, sqrt
from numbers import Real

import numpy as np
from scipy.optimize import minimize_scalar
from scipy.stats import chi2, norm, t


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


def _check_ties(ties: str) -> None:
    if ties not in {"efron", "breslow"}:
        raise ValueError("ties must be 'efron' or 'breslow'")


def _check_participants(participants: object) -> None:
    if (
        not isinstance(participants, Sequence)
        or isinstance(participants, (str, bytes))
        or not participants
    ):
        raise ValueError("participants must be a non-empty sequence of rows")


def _check_study_sources(study_sources: Mapping[str, str], study_ids: set[str]) -> None:
    if not isinstance(study_sources, Mapping):
        raise ValueError("study source mapping is required")
    if set(study_sources) != study_ids or any(
        not isinstance(source, str) or not source.strip() for source in study_sources.values()
    ):
        raise ValueError("study source mapping must map every study ID to a non-empty source key")


def _covariate_spec(
    covariate_centers: Mapping[str, float], covariate_scales: Mapping[str, float]
) -> tuple[list[str], dict[str, float], dict[str, float]]:
    if not isinstance(covariate_centers, Mapping) or not isinstance(covariate_scales, Mapping):
        raise ValueError("covariate center and scale mappings are required")
    keys = set(covariate_centers)
    if not keys:
        raise ValueError("at least one covariate center and scale is required")
    if set(covariate_scales) != keys:
        raise ValueError("covariate center and scale mappings must name the same covariates")
    if any(not isinstance(name, str) or not name.strip() for name in keys):
        raise ValueError("covariate names must be non-empty strings")
    names = sorted(keys)
    centers = {
        name: _number(covariate_centers[name], f"covariate center for {name}") for name in names
    }
    scales = {
        name: _number(covariate_scales[name], f"covariate scale for {name}") for name in names
    }
    if any(scale <= 0 for scale in scales.values()):
        raise ValueError("covariate scales must be greater than zero")
    return names, centers, scales


def _prepare_rows(
    participants: Sequence[Mapping[str, object]],
    covariate_names: Sequence[str],
    centers: Mapping[str, float],
    scales: Mapping[str, float],
) -> tuple[dict[str, list[tuple[float, int, np.ndarray]]], list[int], int]:
    """Parse participant rows into study-grouped (duration, event, x) tuples.

    The design vector x is ``[treatment, scaled covariates...]``. Rows are used
    in memory only and are never retained by or returned from the fit result.
    """
    fields = {"study_id", "duration", "event", "treatment", *covariate_names}
    rows_by_study: dict[str, list[tuple[float, int, np.ndarray]]] = {}
    event_counts = [0, 0]
    for row in participants:
        if not isinstance(row, Mapping) or set(row) != fields:
            raise ValueError(
                "each row needs study_id, duration, event, treatment, and the named covariates "
                + ", ".join(covariate_names)
            )
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
        design = [float(int(treatment))]
        for name in covariate_names:
            value = _number(row[name], f"{name} for study {study_id}")
            try:
                scaled = (value - centers[name]) / scales[name]
            except (OverflowError, ZeroDivisionError):
                raise ValueError(f"centered {name} must be finite") from None
            if not isfinite(scaled):
                raise ValueError(f"centered {name} must be finite")
            design.append(scaled)
        event, treatment = int(event), int(treatment)
        rows_by_study.setdefault(study_id, []).append(
            (duration, event, np.asarray(design, dtype=float))
        )
        event_counts[treatment] += event
    return rows_by_study, event_counts, len(participants)


def _event_groups(rows_by_study: Mapping[str, list[tuple[float, int, np.ndarray]]]) -> dict:
    """Group tied event rows per study from a descending-time sweep."""
    for rows in rows_by_study.values():
        rows.sort(key=lambda row: row[0], reverse=True)
    groups: dict[str, dict[float, list[tuple[float, int, np.ndarray]]]] = {}
    for study_id, rows in rows_by_study.items():
        study_groups: dict[float, list[tuple[float, int, np.ndarray]]] = {}
        for row in rows:
            if row[1]:
                study_groups.setdefault(row[0], []).append(row)
        groups[study_id] = study_groups
    return groups


def _evaluate(
    beta: np.ndarray,
    rows_by_study: Mapping[str, list[tuple[float, int, np.ndarray]]],
    event_groups: Mapping[str, Mapping[float, list[tuple[float, int, np.ndarray]]]],
    ties: str,
    width: int,
) -> tuple[float, np.ndarray, np.ndarray]:
    """Return the partial log likelihood, score, and observed information."""
    likelihood = 0.0
    score = np.zeros(width)
    information = np.zeros((width, width))
    for study_id, rows in rows_by_study.items():
        cursor = 0
        maximum = None
        total_weight = 0.0
        weighted_x = np.zeros(width)
        weighted_xx = np.zeros((width, width))
        for event_time, tied_rows in event_groups[study_id].items():
            while cursor < len(rows) and rows[cursor][0] >= event_time:
                x = rows[cursor][2]
                try:
                    eta = fsum(float(beta[index]) * x[index] for index in range(width))
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

            tied_x = np.zeros(width)
            tied_weight = 0.0
            tied_weighted_x = np.zeros(width)
            tied_weighted_xx = np.zeros((width, width))
            relative_event_eta = []
            for _, _, x in tied_rows:
                try:
                    eta = fsum(float(beta[index]) * x[index] for index in range(width))
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


def _fit_cox(
    rows_by_study: Mapping[str, list[tuple[float, int, np.ndarray]]],
    event_total: int,
    ties: str,
    width: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Newton-fit the stratified partial likelihood; return beta and covariance."""
    event_groups = _event_groups(rows_by_study)

    def evaluate(beta: np.ndarray) -> tuple[float, np.ndarray, np.ndarray]:
        return _evaluate(beta, rows_by_study, event_groups, ties, width)

    beta = np.zeros(width)
    tolerance = 1e-10 * max(1, event_total)
    converged = False
    for iteration in range(100):
        likelihood, score, information = evaluate(beta)
        if iteration == 0:
            # A degenerate design (for example a constant covariate) is singular from
            # the start; report identifiability instead of a convergence failure.
            try:
                smallest = float(np.linalg.eigvalsh(information)[0])
            except np.linalg.LinAlgError:
                smallest = float("nan")
            if not isfinite(smallest) or smallest <= 0:
                raise ValueError(
                    "treatment or covariate effect is not identifiable with a finite Hessian"
                )
        if np.max(np.abs(score)) <= tolerance:
            converged = True
            break
        try:
            step = np.linalg.solve(information, score)
        except np.linalg.LinAlgError:
            raise ValueError("treatment or covariate effect is not identifiable") from None
        direction = float(score @ step)
        if not isfinite(direction) or direction <= 0:
            raise ValueError("Cox partial-likelihood fit failed to converge")
        fraction = 1.0
        while fraction >= 2**-30:
            candidate = beta + fraction * step
            try:
                candidate_likelihood, candidate_score, _ = evaluate(candidate)
            except ValueError:
                candidate_likelihood, candidate_score = float("-inf"), None
            if candidate_likelihood >= likelihood + 1e-4 * fraction * direction:
                beta = candidate
                break
            if fraction == 1.0 and candidate_score is not None:
                # Near the optimum the partial likelihood is flat to float rounding,
                # so the full Newton step can fail the likelihood-increase test while
                # already solving the score. Accept it when its score meets the same
                # tolerance; otherwise halve as usual.
                if np.max(np.abs(candidate_score)) <= tolerance:
                    beta = candidate
                    break
            fraction *= 0.5
        else:
            raise ValueError(
                "Cox partial-likelihood fit failed to converge; separation or weak identification is possible"
            )
    if not converged:
        _, score, _ = evaluate(beta)
        converged = np.max(np.abs(score)) <= tolerance
    if not converged:
        raise ValueError(
            "Cox partial-likelihood fit did not converge; separation or weak identification is possible"
        )

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
        raise ValueError("Cox Hessian does not yield finite estimates")
    standard_errors = np.sqrt(np.diag(covariance))
    if not np.isfinite(standard_errors).all() or np.any(standard_errors <= 0):
        raise ValueError("Cox Hessian does not yield finite standard errors")
    return beta, covariance


def fit_adjusted_stratified_cox_multi(
    participants: Sequence[Mapping[str, object]],
    study_sources: Mapping[str, str],
    *,
    covariate_centers: Mapping[str, float],
    covariate_scales: Mapping[str, float],
    ties: str = "efron",
) -> dict:
    """Fit a common treatment HR adjusted for one or more numeric baseline covariates.

    Treatment is coded 0=control and 1=treatment. Each named covariate is
    centered at its entry in ``covariate_centers`` and divided by the positive
    entry in ``covariate_scales``; its coefficient is per one scaled unit.
    Covariate term order is the sorted covariate name order. Rows are used in
    memory only and are never returned.
    """
    _check_ties(ties)
    _check_participants(participants)
    names, centers, scales = _covariate_spec(covariate_centers, covariate_scales)

    rows_by_study, event_counts, _ = _prepare_rows(participants, names, centers, scales)
    study_ids = set(rows_by_study)
    _check_study_sources(study_sources, study_ids)
    if not all(event_counts):
        raise ValueError("at least one event in both treatment arms is required")

    event_total = sum(event_counts)
    width = len(names) + 1
    beta, covariance = _fit_cox(rows_by_study, event_total, ties, width)
    standard_errors = np.sqrt(np.diag(covariance))

    critical = float(norm.ppf(0.975))
    treatment_low = float(beta[0] - critical * standard_errors[0])
    treatment_high = float(beta[0] + critical * standard_errors[0])
    covariate_effects = {}
    for index, name in enumerate(names, start=1):
        estimate = float(beta[index])
        standard_error = float(standard_errors[index])
        low = estimate - critical * standard_error
        high = estimate + critical * standard_error
        covariate_effects[name] = {
            "log_hr": estimate,
            "se": standard_error,
            "log_hr_ci_low": float(low),
            "log_hr_ci_high": float(high),
            "hazard_ratio": _exp_or_none(estimate),
            "hr_ci_low": _exp_or_none(low),
            "hr_ci_high": _exp_or_none(high),
        }
    return {
        "method": (
            "study-stratified Cox proportional hazards model adjusted for "
            f"{len(names)} numeric baseline covariates"
        ),
        "model": (
            "h_is(t) = h_0s(t) exp(beta_treatment * treatment"
            + "".join(f" + beta_{name} * ({name} - center) / scale" for name in names)
            + ")"
        ),
        "treatment_coding": "0=control, 1=treatment",
        "log_hr": float(beta[0]),
        "se": float(standard_errors[0]),
        "log_hr_ci_low": treatment_low,
        "log_hr_ci_high": treatment_high,
        "hazard_ratio": _exp_or_none(float(beta[0])),
        "hr_ci_low": _exp_or_none(treatment_low),
        "hr_ci_high": _exp_or_none(treatment_high),
        "covariate_names": names,
        "covariate_effects": covariate_effects,
        "covariate_centers": dict(centers),
        "covariate_scales": dict(scales),
        "covariance_names": ["treatment", *names],
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
            "independent right censoring conditional on treatment and covariates",
            "complete participant rows with fixed numeric baseline covariates",
            "no treatment-by-covariate interaction or time-varying effects",
        ],
    }


def _dl_tau2(y: np.ndarray, variance: np.ndarray) -> float:
    """DerSimonian-Laird tau2, consistent with the review-analysis formulas."""
    weights = 1.0 / variance
    total = float(fsum(weights.tolist()))
    fixed = float(fsum((w * value for w, value in zip(weights.tolist(), y.tolist())))) / total
    q = float(fsum((w * (value - fixed) ** 2 for w, value in zip(weights.tolist(), y.tolist()))))
    df = len(y) - 1
    c = total - float(fsum((w * w for w in weights.tolist()))) / total
    return max(0.0, (q - df) / c) if c > 0 else 0.0


def _reml_objective(y: np.ndarray, variance: np.ndarray, tau2: float) -> float:
    """Return -2 profile restricted log likelihood, omitting constants."""
    try:
        with np.errstate(over="raise", divide="raise", invalid="raise"):
            total_variance = variance + tau2
            weights = 1.0 / total_variance
            total_weight = float(np.sum(weights))
            if total_weight <= 0 or not isfinite(total_weight):
                return float("inf")
            pooled = float(np.sum(weights * y)) / total_weight
            value = float(
                np.log(total_variance).sum()
                + log(total_weight)
                + np.sum(weights * (y - pooled) ** 2)
            )
    except (FloatingPointError, OverflowError, ValueError):
        return float("inf")
    return value if isfinite(value) else float("inf")


def _reml_tau2(y: np.ndarray, variance: np.ndarray) -> float:
    """Minimize the profile REML objective, including its tau2=0 boundary."""
    with np.errstate(over="ignore", invalid="ignore"):
        scale = max(float(np.var(y)), float(np.mean(variance)), float(np.max(variance)))
    if not isfinite(scale) or scale <= 0:
        raise ValueError("REML variance scale is not finite and positive")

    def objective(scaled_tau2: float) -> float:
        return _reml_objective(y, variance, scaled_tau2 * scale)

    upper = 1.0
    for _ in range(40):
        try:
            fit = minimize_scalar(objective, bounds=(0.0, upper), method="bounded",
                                  options={"xatol": 1e-12, "maxiter": 1000})
        except (FloatingPointError, OverflowError, ValueError) as exc:
            raise ValueError("REML profile optimization failed") from exc
        if not fit.success or not isfinite(float(fit.fun)):
            raise ValueError("REML profile optimization failed")
        if fit.x >= upper * (1.0 - 1e-6):
            upper *= 10.0
            if not isfinite(upper * scale):
                raise ValueError("REML profile did not find a finite interior estimate")
            continue
        at_zero = _reml_objective(y, variance, 0.0)
        return 0.0 if at_zero <= float(fit.fun) else float(fit.x * scale)
    raise ValueError("REML profile optimization failed to bracket its minimum")


def fit_two_stage_random_slopes(
    participants: Sequence[Mapping[str, object]],
    study_sources: Mapping[str, str],
    *,
    ties: str = "efron",
    tau2_method: str = "DL",
) -> dict:
    """Meta-analyze within-study Cox treatment log-HRs with a random-effects model.

    Stage one fits ``Cox(Surv(duration, event) ~ treatment)`` separately in every
    study. Stage two pools the log-HRs with inverse-variance weights under
    DerSimonian-Laird (``tau2_method="DL"``) or profile REML
    (``tau2_method="REML"``) between-study variance. Rows are used in memory
    only and are never returned; only aggregate study estimates are reported.
    """
    _check_ties(ties)
    _check_participants(participants)
    if tau2_method not in {"DL", "REML"}:
        raise ValueError("tau2_method must be 'DL' or 'REML'")

    rows_by_study, _, _ = _prepare_rows(participants, (), {}, {})
    study_ids = list(rows_by_study)
    if len(study_ids) < 2:
        raise ValueError("at least two studies are required")
    _check_study_sources(study_sources, set(study_ids))

    estimates = []
    events_total = 0
    rows_total = 0
    for study_id in study_ids:
        rows = rows_by_study[study_id]
        arm_events = [0, 0]
        arm_sizes = [0, 0]
        for _, event, x in rows:
            arm = int(x[0])
            arm_events[arm] += event
            arm_sizes[arm] += 1
        if not all(arm_sizes):
            raise ValueError(f"study {study_id} must include both treatment groups")
        if not all(arm_events):
            raise ValueError(f"study {study_id} needs at least one event in both treatment arms")
        events = int(sum(event for _, event, _ in rows))
        try:
            beta, covariance = _fit_cox({study_id: rows}, events, ties, 1)
        except ValueError as exc:
            raise ValueError(f"study {study_id}: {exc}") from None
        standard_error = float(sqrt(covariance[0, 0]))
        events_total += events
        rows_total += len(rows)
        estimates.append({
            "study_id": study_id,
            "source": study_sources[study_id],
            "log_hr": float(beta[0]),
            "se": standard_error,
            "n": len(rows),
            "events": events,
        })

    y = np.asarray([estimate["log_hr"] for estimate in estimates], dtype=float)
    variance = np.asarray([estimate["se"] ** 2 for estimate in estimates], dtype=float)
    if tau2_method == "DL":
        tau2 = _dl_tau2(y, variance)
    else:
        tau2 = _reml_tau2(y, variance)

    weights = 1.0 / (variance + tau2)
    total_weight = float(fsum(weights.tolist()))
    pooled = float(fsum((w * value for w, value in zip(weights.tolist(), y.tolist())))) / total_weight
    pooled_se = sqrt(1.0 / total_weight)
    # Heterogeneity Q and I2 use the fixed-effect weights, as in the review-analysis
    # formulas and metafor's QE statistic.
    fixed_weights = 1.0 / variance
    fixed_total = float(fsum(fixed_weights.tolist()))
    fixed_pooled = (
        float(fsum((w * value for w, value in zip(fixed_weights.tolist(), y.tolist()))))
        / fixed_total
    )
    q = float(
        fsum((w * (value - fixed_pooled) ** 2 for w, value in zip(fixed_weights.tolist(), y.tolist())))
    )
    df = len(y) - 1
    i2 = max(0.0, (q - df) / q) * 100.0 if q > 0 else 0.0
    critical = float(norm.ppf(0.975))
    low, high = pooled - critical * pooled_se, pooled + critical * pooled_se
    prediction = None
    if len(y) >= 3:
        spread = float(t.ppf(0.975, len(y) - 2)) * sqrt(tau2 + pooled_se**2)
        prediction = [pooled - spread, pooled + spread]
    for estimate, weight in zip(estimates, weights.tolist()):
        estimate["weight_share"] = float(weight / total_weight)

    return {
        "method": "two-stage IPD meta-analysis of within-study Cox treatment log hazard ratios",
        "stage_one_model": "coxph(Surv(duration, event) ~ treatment) fitted separately per study",
        "stage_two_model": (
            f"inverse-variance random-effects pooling with tau2_method={tau2_method}"
        ),
        "treatment_coding": "0=control, 1=treatment",
        "tau2_method": tau2_method,
        "pooled_log_hr": pooled,
        "se": pooled_se,
        "log_hr_ci_low": float(low),
        "log_hr_ci_high": float(high),
        "pooled_hazard_ratio": _exp_or_none(pooled),
        "hr_ci_low": _exp_or_none(low),
        "hr_ci_high": _exp_or_none(high),
        "tau2": tau2,
        "q": q,
        "q_df": df,
        "q_p": float(chi2.sf(q, df)),
        "i2_percent": i2,
        "prediction_interval_log_hr_95": None if prediction is None else [float(prediction[0]), float(prediction[1])],
        "prediction_interval_hazard_ratio_95": (
            None if prediction is None
            else [_exp_or_none(prediction[0]), _exp_or_none(prediction[1])]
        ),
        "study_estimates": estimates,
        "n": rows_total,
        "events": events_total,
        "studies": len(study_ids),
        "study_sources": dict(study_sources),
        "ties": ties,
        "confidence_level": 0.95,
        "assumptions": [
            "within-study treatment log hazard ratios are approximately normal with the reported sampling variances",
            "between-study heterogeneity is normal on the log-HR scale with variance tau2",
            "per-study proportional hazards and independent right censoring",
            "one effect estimate per study; the two-stage unadjusted slope differs from an adjusted stratified one-stage fit",
            "prediction intervals use a t reference with studies-2 degrees of freedom and require at least three studies",
        ],
    }
