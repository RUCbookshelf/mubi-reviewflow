"""Likelihood pooling for single-group event counts."""

from __future__ import annotations

import math
from numbers import Integral, Real
from typing import Mapping, Sequence

import numpy as np
from numpy.polynomial.hermite import hermgauss
from scipy.integrate import quad
from scipy.optimize import minimize
from scipy.special import expit, gammaln, log_expit, logsumexp
from scipy.stats import beta, chi2


def _count(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, Integral) or value < 0:
        raise ValueError(f"{name} must be a nonnegative integer")
    if value > 2**53:
        raise ValueError(f"{name} exceeds the exact integer range of likelihood calculations")
    return int(value)


def _finite_number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{name} must be a positive finite number")
    try:
        result = float(value)
    except (OverflowError, TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a positive finite number") from exc
    if not math.isfinite(result) or result <= 0:
        raise ValueError(f"{name} must be a positive finite number")
    return result


def _prepare(rows: Sequence[Mapping], kind: str) -> tuple[list[dict], list[str], str, np.ndarray, np.ndarray]:
    if not isinstance(kind, str) or kind not in {"proportion", "rate"}:
        raise ValueError("kind must be 'proportion' or 'rate'")
    if not isinstance(rows, (list, tuple)) or not rows:
        raise ValueError("rows must be a non-empty list")

    source, study_ids, events, denominators, units = [], [], [], [], []
    for row in rows:
        if not isinstance(row, Mapping):
            raise ValueError("each row must be a mapping")
        study_id = row.get("study_id")
        if not isinstance(study_id, str) or not study_id.strip():
            raise ValueError("each row needs a non-empty study_id")
        study_ids.append(study_id.strip())
        source.append(dict(row))
        event = _count(row.get("events"), f"events for study {study_id}")
        events.append(event)
        if kind == "proportion":
            total = _count(row.get("total"), f"total for study {study_id}")
            if total == 0:
                raise ValueError(f"total for study {study_id} must be positive")
            if event > total:
                raise ValueError(f"events cannot exceed total for study {study_id}")
            denominators.append(total)
        else:
            denominators.append(_finite_number(row.get("person_time"),
                                               f"person_time for study {study_id}"))
            unit = row.get("time_unit")
            if not isinstance(unit, str) or not unit.strip():
                raise ValueError(f"time_unit for study {study_id} must be a non-empty string")
            units.append(unit.strip())

    if len(set(study_ids)) != len(study_ids):
        raise ValueError("duplicate study_id; each study must appear once")
    if sum(events) > 2**53 or (kind == "proportion" and sum(denominators) > 2**53):
        raise ValueError("pooled counts exceed the exact integer range of likelihood calculations")
    unit = ""
    if kind == "rate":
        if len(set(units)) != 1:
            raise ValueError("all rate rows must use the same time_unit")
        unit = units[0]

    try:
        events_array = np.asarray(events, dtype=float)
        denominator_array = np.asarray(denominators, dtype=float)
    except (OverflowError, ValueError) as exc:
        raise ValueError("counts and denominators must be representable for likelihood calculations") from exc
    if not np.isfinite(events_array).all() or not np.isfinite(denominator_array).all():
        raise ValueError("counts and denominators must be representable for likelihood calculations")
    return source, study_ids, unit, events_array, denominator_array


def _fixed_result(kind: str, events: np.ndarray, denominators: np.ndarray, unit: str) -> dict:
    count = sum(int(value) for value in events)
    alpha = 0.05
    if kind == "proportion":
        total = sum(int(value) for value in denominators)
        estimate = count / total
        ci = [0.0 if count == 0 else float(beta.ppf(alpha / 2, count, total - count + 1)),
              1.0 if count == total else float(beta.ppf(1 - alpha / 2, count + 1, total - count))]
        return {"method": "pooled binomial maximum likelihood",
                "ci_method": "Clopper-Pearson exact 95% interval",
                "estimate": estimate, "ci_95": ci, "events": count, "total": total}

    try:
        exposure = math.fsum(float(value) for value in denominators)
        estimate = count / exposure
    except (OverflowError, ZeroDivisionError) as exc:
        raise ValueError("total person_time is outside the representable range") from exc
    if not math.isfinite(exposure) or not math.isfinite(estimate) or (count and estimate == 0):
        raise ValueError("pooled rate is outside the representable range")
    ci = [0.0 if count == 0 else float(chi2.ppf(alpha / 2, 2 * count) / (2 * exposure)),
          float(chi2.ppf(1 - alpha / 2, 2 * (count + 1)) / (2 * exposure))]
    return {"method": "pooled Poisson maximum likelihood",
            "ci_method": "Garwood exact 95% interval",
            "estimate": estimate, "ci_95": ci, "events": count,
            "person_time": exposure, "time_unit": unit}


def _fit_random(kind: str, events: np.ndarray, denominators: np.ndarray) -> tuple[dict, float | None, list[str]]:
    n_studies = len(events)
    warning_list = []
    total_events = float(events.sum())
    if kind == "proportion":
        total = float(denominators.sum())
        if total_events == 0 or total_events == total:
            state = "all studies have zero events" if total_events == 0 else "all studies have all events"
            warning_list.append(f"Random-effects GLMM is not estimable because {state}.")
            return {"model": "binomial-logit-normal GLMM", "status": "not_estimable",
                    "estimate": None, "ci_95": None, "link_intercept": None,
                    "marginal_mean": None, "log_likelihood": None, "tau2": None}, None, warning_list
        mu_start = math.log(total_events) - math.log(total - total_events)
        offsets = None
    else:
        if total_events == 0:
            warning_list.append("Random-effects GLMM is not estimable because all studies have zero events.")
            return {"model": "Poisson-log-normal GLMM", "status": "not_estimable",
                    "estimate": None, "ci_95": None, "link_intercept": None,
                    "marginal_mean": None, "log_likelihood": None, "tau2": None}, None, warning_list
        offsets = np.log(denominators)
        mu_start = math.log(total_events) - math.log(math.fsum(float(value) for value in denominators))

    if n_studies < 2:
        warning_list.append("A random-intercept variance cannot be identified from one study.")
        return {"model": "binomial-logit-normal GLMM" if kind == "proportion" else
                "Poisson-log-normal GLMM", "status": "not_identifiable", "estimate": None,
                "ci_95": None, "link_intercept": None, "marginal_mean": None,
                "log_likelihood": None, "tau2": None}, None, warning_list

    if kind == "proportion":
        trials = denominators
        log_choose = gammaln(trials + 1) - gammaln(events + 1) - gammaln(trials - events + 1)
    else:
        log_choose = -gammaln(events + 1)
    nodes, weights = hermgauss(61)
    log_weights = np.log(weights)

    def negative_log_likelihood(params: np.ndarray) -> float:
        mu, sd = float(params[0]), abs(float(params[1]))
        if sd < 1e-8:
            if kind == "proportion":
                value = events * log_expit(mu) + (trials - events) * log_expit(-mu) + log_choose
            else:
                log_mean = offsets + mu
                with np.errstate(over="ignore", invalid="ignore"):
                    value = events * log_mean - np.exp(np.minimum(log_mean, 700)) + log_choose
                value = np.where(log_mean > 700, -np.inf, value)
            total_ll = float(np.sum(value))
            return -total_ll if math.isfinite(total_ll) else math.inf

        variance = sd * sd
        if kind == "proportion":
            # This is only a finite posterior-mode starting value; the likelihood
            # below always uses the original, unadjusted event and total counts.
            fitted = np.clip((events + .5) / (trials + 1), 1e-12, 1 - 1e-12)
            data_mode = np.log(fitted / (1 - fitted)) - mu
            information = trials * fitted * (1 - fitted)
            mode = data_mode * (variance * information) / (1 + variance * information)
        else:
            log_mean0 = offsets + mu
            positive = events > 0
            mode = np.empty_like(events)
            mode[positive] = ((np.log(events[positive]) - log_mean0[positive]) *
                              (variance * events[positive]) /
                              (1 + variance * events[positive]))
            log_vmean = math.log(variance) + log_mean0[~positive]
            w_approx = np.empty_like(log_vmean)
            large = log_vmean > 1
            w_approx[large] = log_vmean[large] - np.log(log_vmean[large])
            w_approx[~large] = np.exp(np.minimum(log_vmean[~large], 700))
            mode[~positive] = -w_approx

        # Center and scale GH nodes at each study's posterior mode; fixed nodes
        # miss the narrow likelihood mass when the fitted random-effect SD is large.
        mode_converged = False
        for _ in range(100):
            if kind == "proportion":
                probability = expit(mu + mode)
                score = events - trials * probability - mode / variance
                precision = trials * probability * (1 - probability) + 1 / variance
            else:
                log_mean_mode = offsets + mu + mode
                with np.errstate(over="ignore", invalid="ignore"):
                    mean_mode = np.exp(np.minimum(log_mean_mode, 700))
                score = events - mean_mode - mode / variance
                precision = mean_mode + 1 / variance
            step = score / precision
            mode += step
            if float(np.max(np.abs(step))) < 1e-10:
                mode_converged = True
                break
        if not mode_converged:
            return math.inf

        if kind == "proportion":
            probability = expit(mu + mode)
            mode_log_likelihood = (log_choose + events * log_expit(mu + mode) +
                                   (trials - events) * log_expit(-mu - mode))
            curvature = trials * probability * (1 - probability) + 1 / variance
        else:
            log_mean_mode = offsets + mu + mode
            with np.errstate(over="ignore", invalid="ignore"):
                mean_mode = np.exp(np.minimum(log_mean_mode, 700))
                mode_log_likelihood = events * log_mean_mode - mean_mode + log_choose
            mode_log_likelihood = np.where(log_mean_mode > 700, -np.inf, mode_log_likelihood)
            curvature = mean_mode + 1 / variance
        mode_log_integrand = mode_log_likelihood - mode * mode / (2 * variance)
        scale = 1 / np.sqrt(curvature)
        u = mode[:, None] + math.sqrt(2) * scale[:, None] * nodes[None, :]
        if kind == "proportion":
            log_integrand = (log_choose[:, None] + events[:, None] * log_expit(mu + u) +
                             (trials - events)[:, None] * log_expit(-mu - u) -
                             u * u / (2 * variance))
        else:
            log_mean = offsets[:, None] + mu + u
            with np.errstate(over="ignore", invalid="ignore"):
                mean = np.exp(np.minimum(log_mean, 700))
                log_integrand = events[:, None] * log_mean - mean + log_choose[:, None]
            log_integrand = np.where(log_mean > 700, -np.inf, log_integrand)
            log_integrand -= u * u / (2 * variance)
        log_likelihoods = (mode_log_integrand + np.log(scale / sd) - .5 * math.log(math.pi) +
                           logsumexp(log_weights[None, :] + log_integrand -
                                     mode_log_integrand[:, None] + nodes[None, :] ** 2, axis=1))
        value = float(np.sum(log_likelihoods))
        return -value if math.isfinite(value) else math.inf

    # ponytail: random-effect SD capped at 8; expand if a fit lands on this bound.
    bounds = [(mu_start - 40.0, mu_start + 40.0), (0.0, 8.0)]
    candidates = []
    boundary_ll = -negative_log_likelihood(np.asarray([mu_start, 0.0]))
    if math.isfinite(boundary_ll):
        candidates.append((boundary_ll, np.asarray([mu_start, 0.0]), True))
    converged_fits = 0
    for initial_mu in (mu_start - 1.5, mu_start, mu_start + 1.5):
        for initial_sd in (0.2, 1.0, 3.0):
            fit = minimize(negative_log_likelihood, [initial_mu, initial_sd],
                           method="L-BFGS-B", bounds=bounds,
                           options={"maxiter": 1000, "ftol": 1e-12, "gtol": 1e-7})
            if fit.success and math.isfinite(float(fit.fun)):
                converged_fits += 1
                candidates.append((-float(fit.fun), np.asarray(fit.x), False))
    if not converged_fits:
        warning_list.append("Random-effects GLMM optimization did not converge; no random estimate is reported.")
        return {"model": "binomial-logit-normal GLMM" if kind == "proportion" else
                "Poisson-log-normal GLMM", "status": "non_converged", "estimate": None,
                "ci_95": None, "link_intercept": None, "marginal_mean": None,
                "log_likelihood": None, "tau2": None}, None, warning_list

    log_likelihood, params, _ = max(candidates, key=lambda candidate: candidate[0])
    mu, sd = map(float, params)
    if sd < 1e-8:
        sd = 0.0
        params = np.asarray([mu, sd])
    tau2 = sd * sd
    estimate = float(expit(mu)) if kind == "proportion" else math.exp(mu)
    marginal_mean = (float(quad(lambda z: math.exp(-z * z / 2) * float(expit(mu + sd * z)) /
                               math.sqrt(2 * math.pi), -10, 10, epsabs=1e-12)[0])
                     if kind == "proportion" else math.exp(mu + tau2 / 2))
    if sd == 0:
        warning_list.append("The estimated random-intercept variance is at its zero boundary.")
    if sd >= 7.99:
        warning_list.append("The variance estimate is at the optimizer's upper bound; inspect model fit.")
    if abs(mu - mu_start) >= 39.99:
        warning_list.append("The intercept estimate is at the optimizer's numeric bound; inspect model fit.")

    steps = np.asarray([1e-4 * max(1.0, abs(mu)), 5e-3 * max(1.0, sd)])
    center = negative_log_likelihood(params)
    hessian = np.zeros((2, 2))
    for i in range(2):
        shift = np.zeros(2)
        shift[i] = steps[i]
        hessian[i, i] = (negative_log_likelihood(params + shift) - 2 * center +
                         negative_log_likelihood(params - shift)) / steps[i] ** 2
        for j in range(i):
            left = np.zeros(2)
            right = np.zeros(2)
            left[i], right[j] = steps[i], steps[j]
            hessian[i, j] = hessian[j, i] = (
                negative_log_likelihood(params + left + right) -
                negative_log_likelihood(params + left - right) -
                negative_log_likelihood(params - left + right) +
                negative_log_likelihood(params - left - right)) / (4 * steps[i] * steps[j])
    ci = None
    try:
        if np.linalg.eigvalsh(hessian).min() <= 1e-7:
            raise np.linalg.LinAlgError("observed information is singular")
        se_mu = math.sqrt(float(np.linalg.inv(hessian)[0, 0]))
        z = 1.959963984540054
        link_ci = [mu - z * se_mu, mu + z * se_mu]
        ci = ([float(expit(value)) for value in link_ci] if kind == "proportion" else
              [math.exp(value) for value in link_ci])
    except (np.linalg.LinAlgError, OverflowError, ValueError):
        link_ci = None
        warning_list.append("Observed information is singular; random-effects confidence interval is unavailable.")

    model = "binomial-logit-normal GLMM" if kind == "proportion" else "Poisson-log-normal GLMM"
    result = {"model": model, "status": "fitted",
              "estimate": estimate, "ci_95": ci,
              "estimand": "conditional median/reference probability or rate at random intercept 0; marginal_mean integrates over random effects",
              "ci_method": "observed-information Wald interval on link scale" if ci else None,
              "link_intercept": mu, "marginal_mean": marginal_mean,
              "log_likelihood": log_likelihood, "tau2": tau2,
              "quadrature_points": len(nodes), "quadrature": "adaptive Gauss-Hermite"}
    return result, tau2, warning_list


def pool_single_group_counts(rows: list[dict], kind: str) -> dict:
    """Pool one independent event count per study with fixed and random likelihoods.

    Random effects use a normal random intercept and 61-point Gauss-Hermite
    quadrature. Fixed intervals are exact; random-effects intervals use the
    observed-information Wald approximation on the link scale.
    """
    source, study_ids, unit, events, denominators = _prepare(rows, kind)
    fixed = _fixed_result(kind, events, denominators, unit)
    random, tau2, warnings = _fit_random(kind, events, denominators)
    warnings.insert(0, "Fixed-effect pooling assumes one common event probability/rate across studies.")
    return {"kind": kind, "fixed": fixed, "random": random, "tau2": tau2,
            "n_studies": len(source), "study_ids": study_ids,
            "time_unit": unit, "warnings": warnings, "source_data": source}
