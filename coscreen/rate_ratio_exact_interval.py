"""Research-level conditional exact and mid-P intervals for the rate ratio.

Descriptive presentation intervals for the incidence-rate ratio
``IRR = (events_t/person_time_t)/(events_c/person_time_c)`` of two
independent Poisson counts, computed from one comparison at a time. The
interval conditions on the total number of events ``E = events_t + events_c``
and inverts binomial tails for ``e_t ~ Binomial(E, p)`` with
``p = T_t*theta/(T_t*theta + T_c)``, the same construction the R
``stats::poisson.test`` documentation describes for the two-sample case
("Confidence intervals are computed similarly to those of binom.test in the
one-sample case, and using binom.test in the two sample case") and that the
Stata ``[R] epitab`` manual publishes under "Methods and formulas".

Unlike the pooled likelihood path these intervals are NOT exported with a
variance or standard error on the log scale and none may be back-derived
from the interval limits for pooling; meta-analytic combination keeps using
the existing RATE_RATIO+SE pipeline (``effect_size_formats``
``rate_ratio_events_time`` and downstream).
"""

from __future__ import annotations

import copy
import math
from numbers import Integral, Real

from scipy.optimize import brentq
from scipy.stats import beta, binom

_INTEGER_MAX = 2**53
_METHODS = ("exact", "mid_p")
_METHOD_LABELS = {
    "exact": "Conditional exact (Clopper-Pearson) interval for the incidence-rate ratio",
    "mid_p": "Conditional mid-P adjusted interval for the incidence-rate ratio",
}
_DIRECTION = (
    "IRR is the rate in the treatment arm divided by the rate in the control "
    "arm, (events_t/person_time_t)/(events_c/person_time_c); an IRR above 1 "
    "means the treatment arm has the higher event rate."
)
_NO_SE_WARNING = (
    "This is a research-level presentation interval for one study: the module "
    "exports no standard error or variance and none may be back-derived from "
    "the interval limits for pooling. Meta-analytic combination must keep "
    "using the existing RATE_RATIO+SE path (effect_size_formats "
    "rate_ratio_events_time)."
)
_MID_P_NON_CONSERVATIVE_WARNING = (
    "The mid-P interval removes half the observed count's point probability "
    "from the exact conditional binomial tail equations, so the limits are "
    "narrower than the exact limits but the interval is not conservative: "
    "its coverage can fall below the nominal level."
)
_ZERO_TREATMENT_EVENTS_WARNING = (
    "The treatment arm has zero events: the lower limit is exactly 0 by the "
    "definition of the conditional binomial reversal and the log-scale "
    "reference values are degenerate (-inf); the interval is one-sided by "
    "construction, not an overflow."
)
_ZERO_CONTROL_EVENTS_WARNING = (
    "The control arm has zero events: the point estimate and the upper limit "
    "are infinite (positive infinity, expressed explicitly rather than "
    "overflowed) and the log-scale reference values are degenerate (+inf); "
    "the lower limit is finite."
)


def _count(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, Integral):
        raise ValueError(f"{name} must be an integer")
    if value < 0:
        raise ValueError(f"{name} must be nonnegative")
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


def _odds_to_irr(probability: float, person_time_ratio: float) -> float:
    """Convert a conditional-binomial p limit to the theta scale.

    ``p = T_t*theta/(T_t*theta + T_c)`` inverts to
    ``theta = p/(1-p) * T_c/T_t``; ``p = 1`` maps to an infinite limit.
    """
    if probability >= 1.0:
        return math.inf
    return probability / (1.0 - probability) * person_time_ratio


def _exact_p_limits(events_t: int, total: int, alpha: float) -> tuple[float, float]:
    """Clopper-Pearson limits for p in the conditional binomial."""
    lower = 0.0 if events_t == 0 else float(beta.ppf(alpha / 2, events_t, total - events_t + 1))
    upper = 1.0 if events_t == total else float(beta.ppf(1 - alpha / 2, events_t + 1, total - events_t))
    return lower, upper


def _mid_p_tail_gt(events: int, total: int, probability: float) -> float:
    """mid-P upper tail: P(X >= events) minus half of P(X = events)."""
    return float(binom.sf(events - 1, total, probability)) - 0.5 * float(binom.pmf(events, total, probability))


def _mid_p_tail_lt(events: int, total: int, probability: float) -> float:
    """mid-P lower tail: P(X <= events) minus half of P(X = events)."""
    return float(binom.cdf(events, total, probability)) - 0.5 * float(binom.pmf(events, total, probability))


def _mid_p_p_limits(events_t: int, total: int, alpha: float) -> tuple[float, float]:
    """mid-P limits for p, mirroring single_group_exact_ci._mid_p."""
    if events_t == 0:
        return 0.0, 1.0 - alpha ** (1.0 / total)
    if events_t == total:
        return alpha ** (1.0 / total), 1.0
    lower = float(brentq(lambda p: _mid_p_tail_gt(events_t, total, p) - alpha / 2,
                         0.0, 1.0, xtol=1e-14, rtol=8.9e-16, maxiter=200))
    upper = float(brentq(lambda p: _mid_p_tail_lt(events_t, total, p) - alpha / 2,
                         0.0, 1.0, xtol=1e-14, rtol=8.9e-16, maxiter=200))
    return lower, upper


def _log(value: float) -> float:
    """Natural log with explicit degenerate values instead of domain errors."""
    if value == 0.0:
        return float("-inf")
    if math.isinf(value):
        return float("inf")
    return math.log(value)


def rate_ratio_interval(events_t, person_time_t, events_c, person_time_c, *, method,
                        level=0.95, source_provenance=None) -> dict:
    """Return a research-level confidence interval for the incidence-rate ratio.

    ``events_t``/``person_time_t`` and ``events_c``/``person_time_c`` are the
    event counts and total person-times of the treatment and control arms
    (independent Poisson counts with known, caller-interpreted exposure time).
    ``method`` is mandatory and one of ``"exact"`` (conditional
    Clopper-Pearson limits, conservative, matching the two-sample confidence
    interval of R ``stats::poisson.test`` and Stata ``iri``'s exact interval)
    or ``"mid_p"`` (the mid-P adjusted conditional interval, narrower but not
    conservative). ``irr`` is the natural-scale point estimate and ``ci`` its
    interval ``[lower, upper]``; ``log_irr`` and ``log_ci`` are log-scale
    reference values only (no SE is derived from them). Boundary arms are
    expressed explicitly: ``e_t = 0`` gives ``lower = 0`` and
    ``log_irr = -inf``; ``e_c = 0`` gives ``irr = upper = inf`` and
    ``log_irr = +inf``. Both arms with zero events raise ``ValueError``
    (the conditional distribution carries no information). No variance or
    standard error is returned and none may be derived from the limits for
    pooling: meta-analytic combination keeps using the existing RATE_RATIO+SE
    pipeline. The conditional binomial audit quantities (``events_total``,
    ``p_hat``, solved ``p_lower``/``p_upper``) are returned for inspection.
    """
    if not isinstance(method, str) or method not in _METHODS:
        raise ValueError(f"method must be one of: {', '.join(_METHODS)}")
    if source_provenance is not None and not isinstance(source_provenance, dict):
        raise ValueError("source_provenance must be an object")
    count_t = _count(events_t, "events_t")
    exposure_t = _positive_number(person_time_t, "person_time_t")
    count_c = _count(events_c, "events_c")
    exposure_c = _positive_number(person_time_c, "person_time_c")
    confidence = _level(level)
    total = count_t + count_c
    if total == 0:
        raise ValueError(
            "both arms have zero events (events_t = events_c = 0): the "
            "conditional binomial carries no information about the rate ratio"
        )

    alpha = 1 - confidence
    person_time_ratio = exposure_c / exposure_t
    rate_t = count_t / exposure_t
    rate_c = count_c / exposure_c
    # With a zero-event control arm the ratio of rates is infinite by
    # definition; float division must not manufacture this silently.
    if count_c == 0:
        irr = math.inf
    else:
        irr = rate_t / rate_c
    # Nominal conditional success probability; algebraically identical to
    # T_t*irr/(T_t*irr + T_c) = count_t/total for finite irr.
    p_hat = count_t / total
    if method == "exact":
        p_lower, p_upper = _exact_p_limits(count_t, total, alpha)
    else:
        p_lower, p_upper = _mid_p_p_limits(count_t, total, alpha)
    lower = _odds_to_irr(p_lower, person_time_ratio)
    upper = _odds_to_irr(p_upper, person_time_ratio)

    warnings: list[str] = [_NO_SE_WARNING]
    if method == "mid_p":
        warnings.append(_MID_P_NON_CONSERVATIVE_WARNING)
    if count_t == 0:
        warnings.append(_ZERO_TREATMENT_EVENTS_WARNING)
    if count_c == 0:
        warnings.append(_ZERO_CONTROL_EVENTS_WARNING)

    return {
        "measure": "IRR",
        "direction": "treatment_over_control",
        "method": method,
        "method_label": _METHOD_LABELS[method],
        "level": confidence,
        "irr": irr,
        "ci": [lower, upper],
        "log_irr": _log(irr),
        "log_ci": [_log(lower), _log(upper)],
        "rate_t": rate_t,
        "rate_c": rate_c,
        "conditional_binomial": {
            "events_total": total,
            "p_hat": p_hat,
            "p_lower": p_lower,
            "p_upper": p_upper,
        },
        "conservative": method == "exact",
        "input_data": copy.deepcopy({
            "events_t": events_t,
            "person_time_t": person_time_t,
            "events_c": events_c,
            "person_time_c": person_time_c,
            "method": method,
            "level": level,
            "source_provenance": source_provenance,
        }),
        "entry_method": "rate_ratio_exact_interval",
        "direction_note": _DIRECTION,
        "warnings": warnings,
    }
