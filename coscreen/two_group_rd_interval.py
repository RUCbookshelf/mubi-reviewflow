"""Research-level confidence intervals for the two-group risk difference.

Descriptive presentation intervals for ``RD = risk_treatment - risk_control``
computed from one 2x2 table at a time (Wald and Newcombe hybrid score). Unlike
the pooled synthesis path these intervals are NOT exported with a variance or
standard error and must not be converted into one for pooling; meta-analytic
combination keeps using the existing RD+SE pipeline (``binary_arm_data`` and
downstream).
"""

from __future__ import annotations

import copy
import math
from numbers import Integral, Real
from statistics import NormalDist

_INTEGER_MAX = 2**53
_METHODS = ("wald", "newcombe_hybrid_score")
_METHOD_LABELS = {
    "wald": "Wald (unpooled) interval for the risk difference",
    "newcombe_hybrid_score": (
        "Newcombe (1998) hybrid score interval, method 10, without continuity "
        "correction"
    ),
}
_NO_SE_WARNING = (
    "This is a research-level presentation interval for one study: the module "
    "exports no standard error and none may be back-derived from the interval "
    "limits for pooling. Meta-analytic combination must keep using the existing "
    "RD+SE pipeline."
)
_NON_CONSERVATIVE_WARNING = (
    "The Newcombe hybrid score interval is an asymptotic score approximation, "
    "not an exact-coverage method: its realized coverage can depart from the "
    "nominal level, and the limits are generally asymmetric about the point "
    "estimate."
)
_WALD_BOUNDARY_ARM_WARNING = (
    "An arm with all events or no events contributes zero variance to the Wald "
    "standard error, so the interval understates uncertainty; the Newcombe "
    "hybrid score interval handles such zero cells more reliably."
)
_TRUNCATED_WARNING = (
    "A Wald confidence limit left the achievable risk-difference range [-1, 1] "
    "and was truncated; this is a known defect of the Wald interval at extreme "
    "rates."
)
_DOUBLE_ZERO_NEWCOMBE_WARNING = (
    "Both arms have zero events: the hybrid score formula reduces literally to "
    "the symmetric interval [-u_c, u_t], where u is the Wilson score upper limit "
    "for 0/n events; this is the method's own definition at double-zero cells, "
    "not a degenerate or full-range interval."
)
_DIRECTION = (
    "RD is the risk in the treatment arm minus the risk in the control arm; a "
    "positive RD means the treatment increases the event probability."
)


def _count(value: object, name: str, *, positive: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, Integral):
        raise ValueError(f"{name} must be an integer")
    minimum = 1 if positive else 0
    if value < minimum:
        raise ValueError(f"{name} must be {'positive' if positive else 'nonnegative'}")
    if value > _INTEGER_MAX:
        raise ValueError(f"{name} exceeds the exact integer range of interval calculations")
    return int(value)


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


def _wilson(events: int, total: int, z: float) -> tuple[float, float]:
    """Wilson score limits for one proportion, matching single_group_exact_ci."""
    n = float(total)
    center = (events + z * z / 2) / (n + z * z)
    half = z / (n + z * z) * math.sqrt(events * (n - events) / n + z * z / 4)
    lower, upper = max(0.0, center - half), min(1.0, center + half)
    # Boundary counts collapse analytically; drop floating-point residue so the
    # limits are exactly 0 and 1 as in the one-proportion module.
    if events == 0:
        lower = 0.0
    if events == total:
        upper = 1.0
    return lower, upper


def _wald_limits(p_t: float, p_c: float, n_t: float, n_c: float, z: float,
                 warnings: list[str]) -> list[float]:
    se = math.sqrt(p_t * (1.0 - p_t) / n_t + p_c * (1.0 - p_c) / n_c)
    if se == 0.0:
        # Both arms are all-event or all-none: the Wald interval degenerates to
        # the point estimate (flagged as an aberration in published method
        # comparisons, e.g. the 10/10 vs 0/20 column of Newcombe's Table II).
        raise ValueError(
            "the Wald variance is degenerate because both arms have p in {0, 1} "
            "(all events or no events in both arms); the Wald interval would be "
            "a zero-width interval at the point estimate, which is meaningless"
        )
    if p_t in (0.0, 1.0) or p_c in (0.0, 1.0):
        warnings.append(_WALD_BOUNDARY_ARM_WARNING)
    rd = p_t - p_c
    lower, upper = rd - z * se, rd + z * se
    if lower < -1.0 or upper > 1.0:
        warnings.append(_TRUNCATED_WARNING)
    lower, upper = max(-1.0, lower), min(1.0, upper)
    return [lower, upper]


def _newcombe_limits(events_t: int, n_t: int, events_c: int, n_c: int,
                     z: float) -> tuple[float, float, dict]:
    l1, u1 = _wilson(events_t, n_t, z)
    l2, u2 = _wilson(events_c, n_c, z)
    p_t, p_c = events_t / n_t, events_c / n_c
    rd = p_t - p_c
    lower = rd - math.sqrt((p_t - l1) ** 2 + (u2 - p_c) ** 2)
    upper = rd + math.sqrt((u1 - p_t) ** 2 + (p_c - l2) ** 2)
    # sqrt(a^2+b^2) <= a+b keeps the raw limits inside [l_t - u_c, u_t - l_c]
    # and hence inside [-1, 1]; the clamp only removes float residue.
    if lower < -1.0 or upper > 1.0:
        lower, upper = max(-1.0, lower), min(1.0, upper)
    components = {"wilson_t": {"l1": l1, "u1": u1}, "wilson_c": {"l2": l2, "u2": u2}}
    return lower, upper, components


def risk_difference_interval(events_t, n_t, events_c, n_c, *, method, level=0.95,
                             source_provenance=None) -> dict:
    """Return a research-level confidence interval for the risk difference.

    ``events_t``/``n_t`` and ``events_c``/``n_c`` are the event counts and group
    sizes of the treatment and control arms; ``method`` is mandatory and one of
    ``"wald"`` (unpooled Wald interval, rejected when the variance degenerates
    because both arms have p in {0, 1}) or ``"newcombe_hybrid_score"`` (Newcombe
    1998 method 10: the square-root combination of the two Wilson score
    intervals, without continuity correction). ``rd`` is ``p_t - p_c`` and ``ci``
    its interval on the natural scale; ``level`` is the two-sided confidence
    level. No variance or standard error is returned and none may be derived
    from the limits for pooling: meta-analytic combination keeps using the
    existing RD+SE pipeline. The Newcombe path also returns the two per-arm
    Wilson components (``l1, u1, l2, u2``) for audit.
    """
    if not isinstance(method, str) or method not in _METHODS:
        raise ValueError(f"method must be one of: {', '.join(_METHODS)}")
    if source_provenance is not None and not isinstance(source_provenance, dict):
        raise ValueError("source_provenance must be an object")
    count_t = _count(events_t, "events_t")
    size_t = _count(n_t, "n_t", positive=True)
    count_c = _count(events_c, "events_c")
    size_c = _count(n_c, "n_c", positive=True)
    if count_t > size_t:
        raise ValueError("events_t cannot exceed n_t")
    if count_c > size_c:
        raise ValueError("events_c cannot exceed n_c")
    confidence = _level(level)

    p_t, p_c = count_t / size_t, count_c / size_c
    rd = p_t - p_c
    warnings: list[str] = [_NO_SE_WARNING]
    if method == "wald":
        z = _z_quantile(confidence)
        ci = _wald_limits(p_t, p_c, float(size_t), float(size_c), z, warnings)
        wilson_t = wilson_c = None
    else:
        warnings.append(_NON_CONSERVATIVE_WARNING)
        z = _z_quantile(confidence)
        lower, upper, components = _newcombe_limits(count_t, size_t, count_c, size_c, z)
        ci = [lower, upper]
        wilson_t, wilson_c = components["wilson_t"], components["wilson_c"]
        if count_t == 0 and count_c == 0:
            warnings.append(_DOUBLE_ZERO_NEWCOMBE_WARNING)

    return {
        "measure": "RD",
        "direction": "treatment_minus_control",
        "method": method,
        "method_label": _METHOD_LABELS[method],
        "level": confidence,
        "rd": rd,
        "ci": [ci[0], ci[1]],
        "p_t": p_t,
        "p_c": p_c,
        "wilson_t": wilson_t,
        "wilson_c": wilson_c,
        "input_data": copy.deepcopy({
            "events_t": events_t,
            "n_t": n_t,
            "events_c": events_c,
            "n_c": n_c,
            "method": method,
            "level": level,
            "source_provenance": source_provenance,
        }),
        "entry_method": "two_group_rd_interval",
        "direction_note": _DIRECTION,
        "warnings": warnings,
    }
