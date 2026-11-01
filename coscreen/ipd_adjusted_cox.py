"""Study-stratified Cox treatment effect adjusted for one baseline covariate."""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from math import exp, fsum, isfinite, log, sqrt, ulp
from numbers import Real

import numpy as np
from scipy.stats import norm

# F4-93: one fit is bounded by a wall-clock budget -- the same guard shape and
# the same 120 s ceiling the random-effects carriers use (_EvaluationBudget in
# dependent_random). The review measured a legitimate 3000-row request holding a
# synchronous worker for 82 s through Newton/Armijo retries, extrapolating to
# tens of minutes at the 100,000-row model limit. The worst legitimate fits
# measured while sizing this limit: 100,000 rows with one covariate 13.1 s,
# 100,000 rows with the 50-covariate cap 44.6 s -- so 120 s never truncates a
# fit that would have succeeded, and only a runaway search stops early, with a
# 400 instead of unbounded CPU. ipd_cox_extended shares the value and shape.
_BUDGET_SECONDS = 120.0

# F4-94/F4-104: complete separation makes the partial likelihood flat, so the
# observed information collapses relative to the null model. 716 random accepted
# fits across three sweeps measured ratios from 0.03 to 1.3; fully separated fits
# measure about 4e-10 (one-stage) and 9e-10 (separated per-study fits). The gate
# sits five orders of magnitude below the lowest legitimate observation, and
# ipd_cox_extended uses the same threshold (same family, same guard shape).
_MIN_INFORMATION_RATIO = 1e-6


class _BudgetExceeded(ValueError):
    """Raised inside evaluate() when the fit's wall-clock budget is spent."""


class _EvaluationBudget:
    """Wall-clock budget shared by the evaluations of one Cox fit (F4-93)."""

    def __init__(self, seconds: float | None = None) -> None:
        self.seconds = _BUDGET_SECONDS if seconds is None else seconds
        self.started = time.monotonic()
        self.exhausted = False

    def expired(self) -> bool:
        if not self.exhausted and time.monotonic() - self.started >= self.seconds:
            self.exhausted = True
        return self.exhausted

    def check(self) -> None:
        if self.expired():
            raise _BudgetExceeded(
                "Cox fit exceeded its computation budget before convergence; "
                "reduce the number of participant rows or covariates per run"
            )


def _finite_fsum(terms, message: str) -> float:  # noqa: ANN001
    """``math.fsum`` 的溢出家族统一入口：中间溢出转 ValueError（4xx 契约）。

    fsum 对「各项都有限、部分和却超出 float 范围」的输入抛
    ``OverflowError``（fsum 溢出族：F2-87/F4-106/F4-95 同型），端点只捕
    ValueError，于是溢出变成 500。常规输入仍走同一条 fsum，结果逐位不变。
    """
    try:
        return fsum(terms)
    except OverflowError as exc:
        raise ValueError(message) from exc


def _information_collapse_ratio(evaluate, beta: np.ndarray) -> float | None:  # noqa: ANN001
    """Smallest-eigenvalue ratio of observed information: fitted fit vs null model.

    F4-94/F4-104 同族判据：完全分离时风险集权重被单个观测支配，拟合点的
    observed information 相对 beta=0 的零模型坍缩数个数量级。返回 None 表示
    无法评估（零模型信息非正/求值失败），调用方此时不得新增拒绝（R2）。
    """
    try:
        null_information = evaluate(np.zeros_like(beta))[2]
    except _BudgetExceeded:
        raise
    except ValueError:
        return None
    try:
        null_smallest = float(np.linalg.eigvalsh(null_information)[0])
        fitted_smallest = float(np.linalg.eigvalsh(evaluate(beta)[2])[0])
    except np.linalg.LinAlgError:
        return None
    if not isfinite(null_smallest) or null_smallest <= 0:
        return None
    return fitted_smallest / null_smallest


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
    """exp() as a reportable ratio: None only when the exponent overflows.

    F4-107: ``result or None`` also folded a correctly rounded underflow (0.0)
    into None, so an audit table showed a real near-zero bound as a missing
    value. Keep every finite result, including 0.0.
    """
    try:
        result = exp(value)
    except OverflowError:
        return None
    return result if isfinite(result) else None


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

    budget = _EvaluationBudget()

    def evaluate(beta: np.ndarray) -> tuple[float, np.ndarray, np.ndarray]:
        # F4-93: charge every full evaluation, so Newton steps and each Armijo
        # trial are covered; a runaway search stops with a 400 instead of
        # occupying the worker thread indefinitely.
        budget.check()
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
                    eta = _finite_fsum(
                        (float(beta[0]) * treatment, float(beta[1]) * covariate),
                        "linear predictor is outside numeric range")
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
                    eta = _finite_fsum(
                        (float(beta[0]) * treatment, float(beta[1]) * covariate),
                        "linear predictor is outside numeric range")
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
                # F4-95: the event-term sum was the only fsum without the family guard.
                likelihood += _finite_fsum(relative_event_eta,
                                           "linear predictor is outside numeric range")
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
            if not np.array_equal(candidate, beta):
                try:
                    candidate_likelihood = evaluate(candidate)[0]
                except _BudgetExceeded:
                    raise
                except ValueError:
                    candidate_likelihood = float("-inf")
                if candidate_likelihood >= likelihood + 1e-4 * fraction * direction:
                    beta = candidate
                    break
            fraction *= 0.5
        else:
            # MM10-04: once the predicted improvement drops below the likelihood
            # value's own rounding noise, every trial step measures as a 1-2 ULP
            # decrease and the search stalls a representable distance from the
            # optimum -- the fit then spent its iterations on null steps
            # (candidate == beta) and reported "separation or weak
            # identification" for a well-conditioned problem. When the full
            # Newton step's predicted improvement is below that noise floor and
            # its measured change is within it, take the step: the score and
            # information it is computed from are far above their own noise.
            noise = 8.0 * ulp(likelihood)
            full_step = beta + step
            try:
                full_likelihood = evaluate(full_step)[0]
            except _BudgetExceeded:
                raise
            except ValueError:
                full_likelihood = float("-inf")
            if (1e-4 * direction <= noise
                    and full_likelihood - likelihood >= -noise
                    and np.any(full_step != beta)):
                beta = full_step
            else:
                raise ValueError("adjusted Cox fit failed to converge; try a different "
                                 "covariate_center before assuming separation or weak "
                                 "identification")
    if not converged:
        _, score, _ = evaluate(beta)
        converged = np.max(np.abs(score)) <= tolerance
    if not converged:
        raise ValueError("adjusted Cox fit did not converge; try a different "
                         "covariate_center before assuming separation or weak "
                         "identification")

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
    # F4-94: complete separation converges on a flat likelihood (score ~ 0 while
    # the coefficient runs to the boundary). The Hessian then still passes the
    # magnitude/condition checks above but its information has collapsed against
    # the null model, and the reported se (~3e4) with hr_ci (None, None) was a
    # silently wrong "success". Reject exactly that collapse; the ratio is
    # evaluated against the same design, so scale and centering cancel.
    collapse = _information_collapse_ratio(evaluate, beta)
    if collapse is not None and collapse < _MIN_INFORMATION_RATIO:
        raise ValueError("treatment or covariate effect is not identifiable: the data appear "
                         "to separate the groups, so a finite maximum-likelihood estimate "
                         "does not exist")
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
