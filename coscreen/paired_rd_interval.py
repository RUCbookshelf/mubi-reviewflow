"""Research-level confidence intervals for the paired risk difference.

Descriptive presentation intervals for the paired risk difference
``RD = (b - c) / n`` computed from one paired 2x2 table at a time, with cell
semantics identical to :mod:`coscreen.paired_binary_effect` (``a`` = both
measurements positive, ``b`` = only the treatment/target measurement positive,
``c`` = only the control/baseline measurement positive, ``d`` = both negative;
direction is treatment minus control).

Implemented methods follow the paired 2x2 recommendations of Fagerland,
Lydersen & Laake ("Recommended tests and confidence intervals for paired
binomial proportions", Statistics in Medicine 33(16):2850-2875, 2014, and the
expanded Chapter 8 of their 2017 book "Statistical Analysis of Contingency
Tables", Table 8.15):

- ``wald_bonett_price``: Wald interval with Bonett-Price adjustment (Bonett &
  Price 2012), the first-choice recommendation for all sample sizes,
- ``newcombe_square_and_add``: Newcombe (1998) square-and-add interval (MOVER
  Wilson score), recommended for small/medium sample sizes,
- ``wald``: the plain paired Wald interval, kept only as a contrast method that
  matches ``paired_binary_effect`` for cross-auditing.

Like :mod:`coscreen.two_group_rd_interval` these are single-table presentation
intervals: the module exports NO standard error or variance and none may be
back-derived from the interval limits for pooling. Meta-analytic combination of
paired designs keeps using the existing RD+SE pipeline.
"""

from __future__ import annotations

import copy
import math
from numbers import Integral, Real
from statistics import NormalDist

_INTEGER_MAX = 2**53
_DIRECTION = "treatment_minus_control"
_METHODS = ("wald_bonett_price", "newcombe_square_and_add", "wald")
_METHOD_LABELS = {
    "wald_bonett_price": (
        "Wald interval with Bonett-Price adjustment for the paired risk "
        "difference (Bonett & Price 2012); first-choice recommendation for all "
        "sample sizes in Fagerland, Lydersen & Laake (2014/2017)"
    ),
    "newcombe_square_and_add": (
        "Newcombe (1998) square-and-add interval (MOVER Wilson score) for the "
        "paired risk difference, without continuity correction"
    ),
    "wald": (
        "Wald interval for the paired risk difference (contrast method; "
        "matches coscreen.paired_binary_effect)"
    ),
}
_NO_SE_WARNING = (
    "This is a research-level presentation interval for one paired table: the "
    "module exports no standard error and none may be back-derived from the "
    "interval limits for pooling. Meta-analytic combination must keep using "
    "the existing RD+SE pipeline."
)
_NON_EXACT_WARNING = (
    "The selected interval is an asymptotic approximation, not an exact-"
    "coverage method: its realized coverage can depart from the nominal level, "
    "and the limits are generally not centred on the raw point estimate."
)
_TRUNCATED_WARNING = (
    "A confidence limit left the achievable risk-difference range [-1, 1] and "
    "was truncated to the boundary."
)
_PSI_ZERO_WARNING = (
    "At least one marginal count is zero, so the Newcombe correlation "
    "coefficient estimate psi is set to 0 by the method's own definition."
)
_ZERO_WIDTH_WARNING = (
    "The interval is zero-width at the point estimate because at least one "
    "marginal count is 0/n or n/n and the corresponding Wilson score interval "
    "collapses; this is the method's own behaviour at fully determined margins."
)
_WALD_ZERO_DISCORDANT_ARM_WARNING = (
    "One discordant count is zero, so the paired Wald variance rests on very "
    "little discordant information; the recommended Bonett-Price and Newcombe "
    "intervals handle such tables more reliably."
)
_DIRECTION_NOTE = (
    "RD is the probability that the treatment/target measurement is positive "
    "minus the probability that the control/baseline measurement is positive, "
    "computed as (b - c)/n on the paired 2x2 table (a = both positive, "
    "b = only treatment positive, c = only control positive, d = both "
    "negative); a positive RD means the treatment increases the event "
    "probability."
)
_NO_DISCORDANT_MESSAGE = (
    "b = c = 0 gives no discordant pairs; the paired risk difference is 0 but "
    "the table carries no discordant information for any interval method "
    "(the paired Wald interval degenerates to the zero-width interval (0, 0)), "
    "so the computation is refused, consistent with coscreen.paired_binary_effect"
)


def _cell_count(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, Integral):
        raise ValueError(f"cell {name} must be an integer")
    if value < 0:
        raise ValueError(f"cell {name} must be nonnegative")
    if value > _INTEGER_MAX:
        raise ValueError(f"cell {name} exceeds the exact integer range of interval calculations")
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
    """Wilson score limits for one binomial proportion.

    Closed form of equations (8.11)/(8.12) in Fagerland, Lydersen & Laake
    (2017), Chapter 8, identical to the Wilson interval used by
    ``two_group_rd_interval`` and ``single_group_exact_ci``.
    """
    n = float(total)
    center = (events + z * z / 2) / (n + z * z)
    half = z / (n + z * z) * math.sqrt(events * (n - events) / n + z * z / 4)
    lower, upper = max(0.0, center - half), min(1.0, center + half)
    # Boundary counts collapse analytically; drop floating-point residue so the
    # limits are exactly 0 and 1.
    if events == 0:
        lower = 0.0
    if events == total:
        upper = 1.0
    return lower, upper


def _clamp(name: str, value: float, warnings: list[str]) -> float:
    if value < -1.0 or value > 1.0:
        warnings.append(_TRUNCATED_WARNING)
        return min(1.0, max(-1.0, value))
    return value


def _wald_limits(a: int, b: int, c: int, d: int, n: int, rd: float, z: float,
                 warnings: list[str]) -> list[float]:
    # Verified form of the paired Wald SE, bitwise identical to
    # coscreen.paired_binary_effect: sqrt((b + c - (b - c)^2/n) / n^2)
    # with the same fsum term order, so the contrast path cross-audits
    # exactly (that variance form was Monte Carlo-checked in B3-3).
    var_term = math.fsum((float(b + c), -((b - c) ** 2) / n))
    if not math.isfinite(var_term) or var_term <= 0:
        # Happens when a = d = 0 and one discordant count is zero: the paired
        # Wald variance is exactly zero and the interval would degenerate to a
        # zero-width interval at +-1, which is meaningless.
        raise ValueError(
            "the paired Wald variance is degenerate (all pairs are concordant "
            "or discordant in one direction with a zero discordant cell); the "
            "Wald interval would be a zero-width interval at the point "
            "estimate, which is meaningless"
        )
    if b == 0 or c == 0:
        warnings.append(_WALD_ZERO_DISCORDANT_ARM_WARNING)
    se = math.sqrt(var_term / n**2)
    lower, upper = rd - z * se, rd + z * se
    return [_clamp("lower", lower, warnings), _clamp("upper", upper, warnings)]


def _bonett_price_limits(b: int, c: int, n: int, z: float,
                         warnings: list[str]) -> tuple[list[float], dict]:
    # Bonett & Price (2012), as reproduced in Fagerland, Lydersen & Laake
    # (2017), Chapter 8, Section 8.6.2: Laplace estimates on the discordant
    # cells, Wald form on the shifted denominator n + 2. The adjusted variance
    # is strictly positive for all admissible tables (pi12_tilde can never be
    # 0 or 1 because 1 is added to b), so this path never degenerates.
    pi12_tilde = (b + 1) / (n + 2)
    pi21_tilde = (c + 1) / (n + 2)
    delta_tilde = pi12_tilde - pi21_tilde
    var_term = pi12_tilde + pi21_tilde - delta_tilde**2
    if not math.isfinite(var_term) or var_term <= 0:  # pragma: no cover - unreachable guard
        raise ValueError("the Bonett-Price adjusted variance is unrepresentable")
    half_width = z * math.sqrt(var_term / (n + 2))
    lower, upper = delta_tilde - half_width, delta_tilde + half_width
    components = {
        "pi12_tilde": pi12_tilde,
        "pi21_tilde": pi21_tilde,
        "delta_tilde": delta_tilde,
    }
    return [_clamp("lower", lower, warnings), _clamp("upper", upper, warnings)], components


def _newcombe_limits(a: int, b: int, c: int, d: int, n: int, rd: float,
                     p_t: float, p_c: float, z: float,
                     warnings: list[str]) -> tuple[list[float], dict]:
    # Newcombe (1998) square-and-add (MOVER Wilson score) interval for paired
    # data, equations (8.9)-(8.14) in Fagerland, Lydersen & Laake (2017),
    # Chapter 8, including their psi rule for the estimated correlation.
    l1, u1 = _wilson(a + b, n, z)
    l2, u2 = _wilson(a + c, n, z)

    marginal_1p, marginal_2p = a + b, c + d
    marginal_p1, marginal_p2 = a + c, b + d
    if min(marginal_1p, marginal_2p, marginal_p1, marginal_p2) == 0:
        psi = 0.0
        warnings.append(_PSI_ZERO_WARNING)
    else:
        a_stat = a * d - b * c
        nprod = float(marginal_1p * marginal_2p * marginal_p1 * marginal_p2)
        if a_stat > n / 2:
            psi = (a_stat - n / 2) / math.sqrt(nprod)
        elif a_stat >= 0:
            psi = 0.0
        else:
            psi = a_stat / math.sqrt(nprod)

    radicand_lower = (
        (p_t - l1) ** 2 + (u2 - p_c) ** 2 - 2 * psi * (p_t - l1) * (u2 - p_c)
    )
    radicand_upper = (
        (p_c - l2) ** 2 + (u1 - p_t) ** 2 - 2 * psi * (p_c - l2) * (u1 - p_t)
    )
    # psi in [-1, 1] keeps both radicands >= (x - y)^2 >= 0 mathematically;
    # clamp tiny negative float residue to zero.
    lower = rd - math.sqrt(max(0.0, radicand_lower))
    upper = rd + math.sqrt(max(0.0, radicand_upper))
    if lower == upper:
        warnings.append(_ZERO_WIDTH_WARNING)
    components = {
        "wilson_t": {"l1": l1, "u1": u1},
        "wilson_c": {"l2": l2, "u2": u2},
        "psi": psi,
    }
    return [_clamp("lower", lower, warnings), _clamp("upper", upper, warnings)], components


def paired_rd_interval(a, b, c, d, *, method, level=0.95, source_provenance=None) -> dict:
    """Return a research-level confidence interval for the paired risk difference.

    ``a``/``b``/``c``/``d`` are the paired 2x2 counts with the exact semantics
    of :mod:`coscreen.paired_binary_effect`: ``a`` = both measurements
    positive, ``b`` = only the treatment/target measurement positive, ``c`` =
    only the control/baseline measurement positive, ``d`` = both measurements
    negative; ``rd = (b - c) / (a + b + c + d)`` with the treatment-minus-control
    sign convention. ``method`` is mandatory and one of ``"wald_bonett_price"``
    (recommended first choice for all sample sizes), ``"newcombe_square_and_add"``
    (recommended for small/medium sample sizes) or ``"wald"`` (contrast method,
    numerically identical to the paired RD interval of
    ``paired_binary_effect``). ``level`` is the two-sided confidence level.

    No variance or standard error is returned and none may be derived from the
    interval limits for pooling: meta-analytic combination keeps using the
    existing RD+SE pipeline. The Bonett-Price and Newcombe paths return their
    intermediate quantities (Laplace-adjusted estimates, Wilson components,
    psi) for audit.
    """
    if not isinstance(method, str) or method not in _METHODS:
        raise ValueError(f"method must be one of: {', '.join(_METHODS)}")
    if source_provenance is not None and not isinstance(source_provenance, dict):
        raise ValueError("source_provenance must be an object")
    count_a = _cell_count(a, "a")
    count_b = _cell_count(b, "b")
    count_c = _cell_count(c, "c")
    count_d = _cell_count(d, "d")
    n = count_a + count_b + count_c + count_d
    if n < 2:
        raise ValueError("the paired table must contain at least n = 2 pairs")
    if count_b == 0 and count_c == 0:
        raise ValueError(_NO_DISCORDANT_MESSAGE)
    confidence = _level(level)
    z = _z_quantile(confidence)

    p_t = (count_a + count_b) / n
    p_c = (count_a + count_c) / n
    rd = (count_b - count_c) / n
    warnings: list[str] = [_NO_SE_WARNING]

    bonett_price = newcombe = None
    if method == "wald":
        ci = _wald_limits(count_a, count_b, count_c, count_d, n, rd, z, warnings)
    elif method == "wald_bonett_price":
        warnings.append(_NON_EXACT_WARNING)
        ci, bonett_price = _bonett_price_limits(count_b, count_c, n, z, warnings)
    else:
        warnings.append(_NON_EXACT_WARNING)
        ci, newcombe = _newcombe_limits(
            count_a, count_b, count_c, count_d, n, rd, p_t, p_c, z, warnings
        )

    return {
        "measure": "PAIRED_RD",
        "direction": _DIRECTION,
        "method": method,
        "method_label": _METHOD_LABELS[method],
        "level": confidence,
        "rd": rd,
        "ci": [ci[0], ci[1]],
        "p_t": p_t,
        "p_c": p_c,
        "n_pairs": n,
        "discordant_total": count_b + count_c,
        "bonett_price": bonett_price,
        "wilson_t": newcombe["wilson_t"] if newcombe else None,
        "wilson_c": newcombe["wilson_c"] if newcombe else None,
        "psi": newcombe["psi"] if newcombe else None,
        "input_data": copy.deepcopy({
            "a": a,
            "b": b,
            "c": c,
            "d": d,
            "method": method,
            "level": level,
            "source_provenance": source_provenance,
        }),
        "entry_method": "paired_rd_interval",
        "direction_note": _DIRECTION_NOTE,
        "source_provenance": copy.deepcopy(source_provenance) if source_provenance is not None else None,
        "warnings": warnings,
    }
