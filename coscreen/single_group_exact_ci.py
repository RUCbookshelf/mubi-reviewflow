"""Exact and scored confidence intervals for one-group proportions and rates.

Descriptive study-level intervals only. Unlike the pooled likelihood intervals
in ``single_group_glmm`` these are computed from one count at a time and are
not exported with a variance or SE for pooling.
"""

from __future__ import annotations

import copy
import math
from numbers import Integral, Real
from statistics import NormalDist

from scipy.optimize import brentq
from scipy.stats import beta, binom, chi2

_INTEGER_MAX = 2**53
_PROPORTION_METHODS = ("clopper_pearson", "wilson", "mid_p", "jeffreys")
_RATE_METHODS = ("exact", "byar")
_PROPORTION_LABELS = {
    "clopper_pearson": "Clopper-Pearson exact interval",
    "wilson": "Wilson score interval without continuity correction",
    "mid_p": "mid-P adjusted binomial exact interval",
    "jeffreys": "Jeffreys equal-tailed interval (Beta(1/2,1/2) posterior)",
}
_RATE_LABELS = {
    "exact": "Garwood exact Poisson interval",
    "byar": "Byar approximation of the Garwood exact Poisson interval",
}
_NON_CONSERVATIVE_NOTES = {
    "wilson": "The Wilson score interval is not conservative: its coverage can fall below the nominal level for p near 0 or 1.",
    "mid_p": "The mid-P interval is not conservative: it removes half the boundary point probability from the exact Clopper-Pearson equations, so tails are shorter and coverage can fall below the nominal level.",
    "jeffreys": "The Jeffreys interval is a Beta(1/2,1/2)-posterior credible interval, not a conservative frequentist exact interval; coverage can fall below the nominal level.",
    "byar": "The Byar limits approximate the exact Garwood Poisson interval with the Wilson-Hilferty chi-square transformation; they are not conservative and lose accuracy for very small event counts.",
}


def _count(value: object, name: str, *, positive: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, Integral):
        raise ValueError(f"{name} must be an integer")
    minimum = 1 if positive else 0
    if value < minimum:
        raise ValueError(f"{name} must be {'positive' if positive else 'nonnegative'}")
    if value > _INTEGER_MAX:
        raise ValueError(f"{name} exceeds the exact integer range of interval calculations")
    return int(value)


def _positive_number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{name} must be a positive finite number")
    try:
        result = float(value)
    except (OverflowError, TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a positive finite number") from exc
    if not math.isfinite(result) or result <= 0:
        raise ValueError(f"{name} must be a positive finite number")
    return result


def _level(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError("level must be a number strictly between 0 and 1")
    try:
        result = float(value)
    except (OverflowError, TypeError, ValueError) as exc:
        raise ValueError("level must be a number strictly between 0 and 1") from exc
    if not math.isfinite(result) or not 0 < result < 1:
        raise ValueError("level must be a number strictly between 0 and 1")
    return result


def _z_quantile(level: float) -> float:
    z = NormalDist().inv_cdf((1 + level) / 2)
    if not math.isfinite(z) or z <= 0:
        raise ValueError("level must produce a finite normal quantile")
    return z


def _clopper_pearson(events: int, total: int, alpha: float) -> tuple[float, float]:
    lower = 0.0 if events == 0 else float(beta.ppf(alpha / 2, events, total - events + 1))
    upper = 1.0 if events == total else float(beta.ppf(1 - alpha / 2, events + 1, total - events))
    return lower, upper


def _wilson(events: int, total: int, z: float) -> tuple[float, float]:
    n = float(total)
    center = (events + z * z / 2) / (n + z * z)
    half = z / (n + z * z) * math.sqrt(events * (n - events) / n + z * z / 4)
    lower, upper = max(0.0, center - half), min(1.0, center + half)
    # The score limits collapse to the boundary analytically when x = 0 or x = n;
    # floating point evaluation leaves rounding residue on the wrong side.
    if events == 0:
        lower = 0.0
    if events == total:
        upper = 1.0
    return lower, upper


def _mid_p_tail_gt(events: int, total: int, probability: float) -> float:
    """mid-P upper tail: P(X >= events) minus half of P(X = events)."""
    return float(binom.sf(events - 1, total, probability)) - 0.5 * float(binom.pmf(events, total, probability))


def _mid_p_tail_lt(events: int, total: int, probability: float) -> float:
    """mid-P lower tail: P(X <= events) minus half of P(X = events)."""
    return float(binom.cdf(events, total, probability)) - 0.5 * float(binom.pmf(events, total, probability))


def _mid_p(events: int, total: int, alpha: float) -> tuple[float, float]:
    if events == 0:
        return 0.0, 1.0 - alpha ** (1.0 / total)
    if events == total:
        return alpha ** (1.0 / total), 1.0
    lower = float(brentq(lambda p: _mid_p_tail_gt(events, total, p) - alpha / 2,
                         0.0, 1.0, xtol=1e-14, rtol=8.9e-16, maxiter=200))
    upper = float(brentq(lambda p: _mid_p_tail_lt(events, total, p) - alpha / 2,
                         0.0, 1.0, xtol=1e-14, rtol=8.9e-16, maxiter=200))
    return lower, upper


def _jeffreys(events: int, total: int, alpha: float) -> tuple[float, float]:
    posterior_a = events + 0.5
    posterior_b = total - events + 0.5
    return float(beta.ppf(alpha / 2, posterior_a, posterior_b)), \
        float(beta.ppf(1 - alpha / 2, posterior_a, posterior_b))


def _rate_exact(events: int, person_time: float, alpha: float) -> tuple[float, float]:
    lower = 0.0 if events == 0 else float(chi2.ppf(alpha / 2, 2 * events)) / (2 * person_time)
    upper = float(chi2.ppf(1 - alpha / 2, 2 * (events + 1))) / (2 * person_time)
    return lower, upper


def _rate_byar(events: int, person_time: float, z: float, warnings: list[str]) -> tuple[float, float]:
    lower = 0.0
    if events > 0:
        base = 1 - 1 / (9 * events) - z / (3 * math.sqrt(events))
        if base <= 0:
            warnings.append("The Byar approximation is invalid at this confidence level for so few events; "
                            "the lower limit is clamped to 0. Use the exact interval instead.")
        else:
            lower = events * base**3 / person_time
    shifted = events + 1
    upper = shifted * (1 - 1 / (9 * shifted) + z / (3 * math.sqrt(shifted))) ** 3 / person_time
    return lower, upper


def proportion_ci(events, total, method, level=0.95) -> dict:
    """Return a descriptive confidence interval for one binomial proportion.

    ``events`` and ``total`` are the observed event count and group size;
    ``method`` is one of ``clopper_pearson``, ``wilson``, ``mid_p`` or
    ``jeffreys`` and ``level`` the two-sided confidence level. The interval
    bounds are returned on the natural (probability) scale. Only
    ``clopper_pearson`` is conservative; the other methods carry an explicit
    non-conservative warning. No SE or variance is returned.
    """
    if not isinstance(method, str) or method not in _PROPORTION_METHODS:
        raise ValueError(f"method must be one of: {', '.join(_PROPORTION_METHODS)}")
    count = _count(events, "events")
    size = _count(total, "total", positive=True)
    if count > size:
        raise ValueError("events cannot exceed total")
    confidence = _level(level)
    alpha = 1 - confidence
    warnings: list[str] = []
    if method == "clopper_pearson":
        lower, upper = _clopper_pearson(count, size, alpha)
    elif method == "wilson":
        lower, upper = _wilson(count, size, _z_quantile(confidence))
    elif method == "mid_p":
        lower, upper = _mid_p(count, size, alpha)
    else:
        lower, upper = _jeffreys(count, size, alpha)
    if method != "clopper_pearson":
        warnings.append(_NON_CONSERVATIVE_NOTES[method])
    return {
        "kind": "proportion",
        "measure": "PROP",
        "method": method,
        "method_label": _PROPORTION_LABELS[method],
        "conservative": method == "clopper_pearson",
        "level": confidence,
        "estimate": count / size,
        "lower": lower,
        "upper": upper,
        "events": count,
        "total": size,
        "input_data": copy.deepcopy({"events": events, "total": total, "method": method, "level": level}),
        "warnings": warnings,
    }


def rate_ci(events, person_time, method, level=0.95) -> dict:
    """Return a descriptive confidence interval for one incidence rate.

    ``events`` is the observed count and ``person_time`` the total exposure;
    the interval bounds are returned in events per person-time unit.
    ``method`` is ``exact`` (Garwood chi-square limits, matching
    ``stats::poisson.test`` in R) or ``byar`` (Wilson-Hilferty approximation,
    non-conservative). No SE or variance is returned.
    """
    if not isinstance(method, str) or method not in _RATE_METHODS:
        raise ValueError(f"method must be one of: {', '.join(_RATE_METHODS)}")
    count = _count(events, "events")
    exposure = _positive_number(person_time, "person_time")
    confidence = _level(level)
    alpha = 1 - confidence
    warnings: list[str] = []
    if method == "exact":
        lower, upper = _rate_exact(count, exposure, alpha)
    else:
        lower, upper = _rate_byar(count, exposure, _z_quantile(confidence), warnings)
    if method != "exact":
        warnings.append(_NON_CONSERVATIVE_NOTES[method])
    return {
        "kind": "rate",
        "measure": "RATE",
        "method": method,
        "method_label": _RATE_LABELS[method],
        "conservative": method == "exact",
        "level": confidence,
        "estimate": count / exposure,
        "lower": lower,
        "upper": upper,
        "events": count,
        "person_time": exposure,
        "input_data": copy.deepcopy({"events": events, "person_time": person_time,
                                     "method": method, "level": level}),
        "warnings": warnings,
    }
