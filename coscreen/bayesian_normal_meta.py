"""Conjugate normal-normal random-effects meta-analysis with explicit priors."""

from __future__ import annotations

import math
import warnings
from numbers import Real
from typing import Mapping, Sequence

import numpy as np
from scipy.integrate import IntegrationWarning, quad
from scipy.optimize import brentq, minimize_scalar
from scipy.special import ndtr

RATIOS = {"RR", "OR", "HR", "RATE_RATIO"}
MEASURES = RATIOS | {"RD", "MD", "SMD", "FISHER_Z"}


def _number(value: object, name: str) -> float:
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, Real):
        raise ValueError(f"{name} must be a finite number")
    try:
        value = float(value)
    except (OverflowError, TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    return value


def _integrate(function, lower=-math.inf, upper=math.inf, *, loose=False):
    epsabs, epsrel = (1e-8, 1e-7) if loose else (2e-11, 2e-10)
    try:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always", IntegrationWarning)
            value, error = quad(function, lower, upper, epsabs=epsabs, epsrel=epsrel,
                                limit=300)
    except (FloatingPointError, OverflowError, ValueError) as exc:
        raise ValueError("posterior quadrature failed") from exc
    error_limit = max(1e-7, abs(value) * 1e-6) if loose else max(1e-9, abs(value) * 1e-7)
    if caught or not math.isfinite(value) or not math.isfinite(error) or error > error_limit:
        raise ValueError("posterior quadrature did not converge")
    return value, error


def _parse_study_rows(rows: Sequence[Mapping]):
    """Validate independent study estimates shared by the Bayesian modules.

    Returns the study ids, estimates and SEs as arrays plus the single effect
    measure. Ratio estimates are logged internally and their supplied SEs must
    already be on the log scale.
    """
    if not isinstance(rows, (list, tuple)) or len(rows) < 2:
        raise ValueError("at least two independent study estimates are required")
    ids, effects, ses, measures = [], [], [], set()
    for row in rows:
        if not isinstance(row, Mapping):
            raise ValueError("each study estimate must be a mapping")
        study_id = row.get("study_id")
        if not isinstance(study_id, str) or not study_id.strip():
            raise ValueError("each study estimate needs a non-empty study_id")
        measure = row.get("measure")
        if not isinstance(measure, str) or measure not in MEASURES:
            raise ValueError("unsupported effect measure")
        estimate = _number(row.get("estimate"), f"estimate for study {study_id}")
        se = _number(row.get("se"), f"SE for study {study_id}")
        if se <= 0:
            raise ValueError("each SE must be positive")
        if measure in RATIOS:
            if estimate <= 0:
                raise ValueError("ratio effect estimates must be positive")
            estimate = math.log(estimate)
        ids.append(study_id)
        effects.append(estimate)
        ses.append(se)
        measures.add(measure)
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate study id; rows must contain independent studies once")
    if len(measures) != 1:
        raise ValueError("all rows must use the same effect measure")
    return ids, np.asarray(effects, dtype=float), np.asarray(ses, dtype=float), next(iter(measures))


def fit_bayesian_normal_meta(
    rows: Sequence[Mapping],
    *,
    mu_prior_mean: object,
    mu_prior_sd: object,
    tau_prior_scale: object,
) -> dict:
    """Summarize a normal-normal random-effects model by integrating over tau.

    Rows have ``study_id``, ``measure``, ``estimate`` and ``se``. Ratio
    estimates are logged internally and their supplied SEs must already be on
    the log scale. Priors are Normal(mu_prior_mean, mu_prior_sd) and
    Half-Normal(tau_prior_scale), both specified on the analysis scale.
    """
    mu0 = _number(mu_prior_mean, "mu prior mean")
    mu_sd = _number(mu_prior_sd, "mu prior SD")
    tau_sd = _number(tau_prior_scale, "tau prior scale")
    if mu_sd <= 0:
        raise ValueError("mu prior SD must be positive for a proper normal prior")
    if tau_sd <= 0:
        raise ValueError("tau prior scale must be positive for a proper half-normal prior")

    ids, y, se, measure = _parse_study_rows(rows)
    analysis_scale = "log" if measure in RATIOS else "natural"
    unit = max(float(np.max(np.abs(y))), float(np.max(se)), abs(mu0), mu_sd, tau_sd)
    y_raw = y
    y, se, mu0, mu_sd, tau_sd = y / unit, se / unit, mu0 / unit, mu_sd / unit, tau_sd / unit
    if np.any((y_raw != 0) & (y == 0)) or np.any(se == 0) or mu_sd == 0 or tau_sd == 0:
        raise ValueError("input scales exceed stable floating-point precision")
    se2, mu_var = se * se, mu_sd * mu_sd
    if not np.isfinite(se2).all() or np.any(se2 <= 0) or not math.isfinite(mu_var) or mu_var <= 0:
        raise ValueError("input scales exceed stable floating-point precision")
    mu_precision = 1.0 / mu_var
    if not math.isfinite(mu_precision):
        raise ValueError("input scales exceed stable floating-point precision")

    def conditional(tau):
        variance = se2 + tau * tau
        if not np.isfinite(variance).all() or np.any(variance <= 0):
            return math.nan, math.nan
        with np.errstate(over="ignore", divide="ignore", invalid="ignore"):
            weight = 1.0 / variance
            total_weight = float(np.sum(weight))
            posterior_variance = 1.0 / (mu_precision + total_weight)
            posterior_mean = posterior_variance * (mu_precision * mu0 + float(weight @ y))
        if not all(math.isfinite(v) and v > 0 for v in (total_weight, posterior_variance)) or not math.isfinite(posterior_mean):
            return math.nan, math.nan
        return posterior_mean, posterior_variance

    def log_density_u(u):
        if u > 350:
            return -math.inf
        try:
            tau = 0.0 if u < -745 else tau_sd * math.exp(u)
            ratio2 = math.exp(2 * u) if u < 350 else math.inf
        except OverflowError:
            return -math.inf
        mean, posterior_variance = conditional(tau)
        if not math.isfinite(mean) or not math.isfinite(posterior_variance):
            return -math.inf
        with np.errstate(over="ignore", divide="ignore", invalid="ignore"):
            variance = se2 + tau * tau
            weight = 1.0 / variance
            residual = y - mu0
            shift = mean - mu0
            quadratic = float(np.sum(weight * (residual - shift) ** 2)) + shift * shift / mu_var
            logdet = float(np.log(variance).sum()) + math.log1p(mu_var * float(np.sum(weight)))
        if not math.isfinite(quadratic) or not math.isfinite(logdet):
            return -math.inf
        log_likelihood = -0.5 * (len(y) * math.log(2 * math.pi) + logdet + quadratic)
        result = u - 0.5 * ratio2 + log_likelihood
        return result if math.isfinite(result) else -math.inf

    data_scale = max(float(np.max(se)), float(np.ptp(y)), mu_sd)
    log_ratio = math.log(data_scale) - math.log(tau_sd)
    low = max(-740.0, min(-40.0, log_ratio - 40.0))
    high = min(350.0, max(40.0, log_ratio + 40.0))
    grid = np.linspace(low, high, max(161, min(1001, int((high - low) / 2) + 1)))
    grid_values = np.asarray([log_density_u(float(u)) for u in grid])
    best = int(np.argmax(grid_values))
    if best in (0, len(grid) - 1) or not math.isfinite(float(grid_values[best])):
        raise ValueError("could not bracket the posterior density of tau")
    try:
        mode = minimize_scalar(lambda u: -log_density_u(u),
                               bounds=(float(grid[best - 1]), float(grid[best + 1])),
                               method="bounded", options={"xatol": 1e-10, "maxiter": 1000})
    except (FloatingPointError, OverflowError, ValueError) as exc:
        raise ValueError("could not locate the posterior density of tau") from exc
    if not mode.success or not math.isfinite(float(mode.fun)):
        raise ValueError("could not locate the posterior density of tau")
    peak = max(float(grid_values[best]), log_density_u(float(mode.x)))

    def density(u):
        delta = log_density_u(float(u)) - peak
        if delta < -745:
            return 0.0
        if delta > 50:
            raise ValueError("posterior density scaling was unstable")
        return math.exp(delta)

    errors = []

    def integral(function, lower=-math.inf, upper=math.inf, *, loose=False):
        def integrand(u):
            weight = density(float(u))
            if weight == 0:
                return 0.0
            value = function(float(u))
            if not math.isfinite(value):
                raise ValueError("posterior integrand is unstable")
            return weight * value

        value, error = _integrate(integrand, lower, upper, loose=loose)
        errors.append(error)
        return value

    z_loose = integral(lambda _u: 1.0, loose=True)
    z = integral(lambda _u: 1.0)
    if z <= 0 or abs(z - z_loose) > max(2e-8, z * 2e-7):
        raise ValueError("posterior quadrature did not converge")

    def tau_at(u):
        return 0.0 if u < -745 else tau_sd * math.exp(u)

    mu_numerator = integral(lambda u: conditional(tau_at(u))[0])
    tau_numerator = integral(tau_at)
    mu_mean_s, tau_mean_s = mu_numerator / z, tau_numerator / z
    for function, strict_value in (
        (lambda u: conditional(tau_at(u))[0], mu_numerator),
        (tau_at, tau_numerator),
    ):
        loose_value = integral(function, loose=True)
        if abs(strict_value - loose_value) > max(2e-8, abs(strict_value) * 2e-7):
            raise ValueError("posterior moment quadrature did not converge")

    def tau_cdf(u):
        if u <= -745:
            return 0.0
        if u >= 350:
            return 1.0
        return min(1.0, max(0.0, integral(lambda _x: 1.0, -math.inf, u) / z))

    def mu_cdf(value):
        def conditional_cdf(u):
            mean, variance = conditional(tau_at(u))
            return float(ndtr((value - mean) / math.sqrt(variance)))

        return min(1.0, max(0.0, integral(conditional_cdf) / z))

    def quantile(cdf, probability, center, *, initial_width=4.0, tau_domain=False):
        width = initial_width
        for _ in range(24):
            left, right = center - width, center + width
            if tau_domain:
                left = max(-745.0, left)
                right = min(350.0, right)
                c_left = 0.0 if left <= -745 else tau_cdf(left)
                c_right = 1.0 if right >= 350 else tau_cdf(right)
            else:
                c_left, c_right = mu_cdf(left), mu_cdf(right)
            if c_left <= probability <= c_right:
                break
            width *= 2
        else:
            raise ValueError("could not bracket a posterior quantile")
        if left == right:
            raise ValueError("posterior quantile is outside stable floating-point precision")
        try:
            root = brentq(lambda x: cdf(x) - probability, left, right,
                          xtol=1e-10 if tau_domain else max(5e-324, min(1e-11, width * 1e-10)),
                          rtol=4 * np.finfo(float).eps, maxiter=150)
        except (ValueError, RuntimeError, OverflowError) as exc:
            raise ValueError("posterior quantile calculation failed") from exc
        if abs(cdf(root) - probability) > 1e-6:
            raise ValueError("posterior quantile calculation did not converge")
        return root

    tau_quantiles_s = [tau_sd * math.exp(quantile(tau_cdf, p, float(mode.x), tau_domain=True))
                       for p in (0.025, 0.5, 0.975)]

    def mu_conditional_variance(u):
        mean, variance = conditional(tau_at(u))
        return variance + (mean - mu_mean_s) ** 2

    mu_second = integral(mu_conditional_variance) / z
    if not math.isfinite(mu_second) or mu_second <= 0:
        raise ValueError("posterior variance of mu is unstable")
    mu_sd_post_s = math.sqrt(mu_second)
    mu_quantiles_s = [quantile(mu_cdf, p, mu_mean_s, initial_width=8 * mu_sd_post_s)
                      for p in (0.025, 0.5, 0.975)]

    def mu_probability_gt_zero(u):
        mean, variance = conditional(tau_at(u))
        return float(ndtr(mean / math.sqrt(variance)))

    probability_gt_zero = integral(mu_probability_gt_zero) / z
    if not 0 <= probability_gt_zero <= 1:
        raise ValueError("posterior probability is unstable")

    def restore(value, name, *, positive=False):
        result = float(value) * unit
        if not math.isfinite(result) or (positive and result <= 0) or (value != 0 and result == 0):
            raise ValueError(f"{name} is outside the finite reporting range")
        return result

    relative_error = max(errors) / z
    if not math.isfinite(relative_error) or relative_error > 1e-6:
        raise ValueError("posterior quadrature did not converge")

    mu_mean = restore(mu_mean_s, "mu posterior mean")
    mu_quantiles = [restore(v, "mu posterior quantile") for v in mu_quantiles_s]
    tau_mean = restore(tau_mean_s, "tau posterior mean", positive=True)
    tau_quantiles = [restore(v, "tau posterior quantile", positive=True) for v in tau_quantiles_s]
    mu = {
        "scale": analysis_scale,
        "mean": mu_mean,
        "median": mu_quantiles[1],
        "credible_interval_95": [mu_quantiles[0], mu_quantiles[2]],
        "probability_gt_zero": float(probability_gt_zero),
    }
    tau = {
        "scale": "log_ratio_sd" if analysis_scale == "log" else "effect_sd",
        "mean": tau_mean,
        "median": tau_quantiles[1],
        "credible_interval_95": [tau_quantiles[0], tau_quantiles[2]],
    }
    result = {
        "model": "normal_normal_random_effects",
        "measure": measure,
        "analysis_scale": analysis_scale,
        "standard_error_scale": analysis_scale,
        "study_ids": ids,
        "study_count": len(ids),
        "priors": {
            "mu": {"distribution": "normal", "mean": float(mu_prior_mean), "sd": float(mu_prior_sd)},
            "tau": {"distribution": "half_normal", "scale": float(tau_prior_scale)},
        },
        "mu": mu,
        "tau": tau,
        "integration": {
            "method": "adaptive Gauss-Kronrod quadrature over log(tau)",
            "converged": True,
            "relative_error_estimate": relative_error,
        },
    }
    if analysis_scale == "log":
        try:
            ratio = [math.exp(value) for value in mu_quantiles]
        except OverflowError as exc:
            raise ValueError("exponentiated posterior interval is outside the finite range") from exc
        if not all(math.isfinite(value) and value > 0 for value in ratio):
            raise ValueError("exponentiated posterior interval is outside the finite range")
        result["ratio_summary"] = {
            "scale": "ratio",
            "median": ratio[1],
            "credible_interval_95": [ratio[0], ratio[2]],
            "probability_gt_one": float(probability_gt_zero),
        }
    return result
