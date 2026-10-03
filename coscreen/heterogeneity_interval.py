"""Higgins-Thompson (2002) transformed 95% intervals for I-squared and H.

The intervals act directly on the Pearson chi-square type heterogeneity
statistic ``Q`` and its degrees of freedom ``k - 1``. ``Q`` must already have
been computed with the effect sizes and weights of the analysis at hand; this
module contains no data access and is a pure calculation.
"""

from __future__ import annotations

import copy
import math
from numbers import Integral, Real

from scipy.stats import norm

METHOD = "higgins_thompson_2002_nonparametric"


def i2_h_interval(q: float, k: int, level: float = 0.95) -> dict:
    """Return Higgins-Thompson (2002) intervals for I-squared (%) and H.

    ``q`` is the heterogeneity statistic Q (Cochran's Q, a Pearson chi-square
    type statistic) computed from ``k`` independent studies, so its degrees of
    freedom are ``k - 1``. Requires ``k`` to be an integer of at least three,
    ``q`` finite and non-negative, and ``level`` strictly between zero and
    one; otherwise ``ValueError`` is raised.

    The point estimates follow ``coscreen.review_analysis``: I-squared is
    ``max(0, (Q - df) / Q) * 100`` (zero when ``Q = 0``) and H is
    ``sqrt(Q / df)``, set to 1.0 with a warning when ``Q < df``. The interval
    is the non-parametric transformed method of Higgins & Thompson (2002): it
    assumes ``ln H`` is normal, centres on ``ln max(H, 1)`` with the
    large-sample standard error transcribed from that paper, and maps the
    resulting H limits through ``I-squared = (H^2 - 1) / H^2``. The H lower
    limit cannot fall below 1 and I-squared limits are truncated to
    ``[0, 100]`` percent. The result is descriptive; ``input_data`` echoes the
    inputs as a deep copy and ``warnings`` lists convention notes.
    """
    if isinstance(k, bool) or not isinstance(k, Integral):
        raise ValueError("k must be an integer number of studies")
    k = int(k)
    if k < 3:
        raise ValueError("i2_h_interval needs at least three studies so df = k - 1 is at least 2")
    if isinstance(q, bool) or not isinstance(q, Real):
        raise ValueError("q must be a finite non-negative number")
    try:
        q = float(q)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("q must be a finite non-negative number") from exc
    if not math.isfinite(q) or q < 0:
        raise ValueError("q must be finite and non-negative")
    if isinstance(level, bool) or not isinstance(level, Real):
        raise ValueError("level must be a number strictly between 0 and 1")
    level = float(level)
    if not math.isfinite(level) or not 0 < level < 1:
        raise ValueError("level must be a number strictly between 0 and 1")

    df = k - 1
    warnings: list[str] = []

    # Standard error of ln H (Higgins & Thompson 2002, non-parametric method,
    # as transcribed verbatim by Stata heterogi, R meta::calcH, R heterometa
    # and Borenstein 2021 equations 16.20-16.21): log-transformed branch when
    # Q exceeds df + 1, central chi-square branch otherwise.
    if q > df + 1:
        se_lnh = 0.5 * (math.log(q) - math.log(df)) / (math.sqrt(2 * q) - math.sqrt(2 * df - 1))
    else:
        se_lnh = math.sqrt((1 / (2 * (df - 1))) * (1 - 1 / (3 * (df - 1) ** 2)))

    h_squared = q / df
    if h_squared < 1:
        if q < df:
            warnings.append(
                f"q={q} is below its degrees of freedom df={df}; per Higgins & Thompson (2002) "
                "H is reported as 1.0 and I-squared as 0, so the H interval is effectively one-sided."
            )
        h = 1.0
    else:
        h = math.sqrt(h_squared)

    z = float(norm.ppf(1 - (1 - level) / 2))
    centre = math.log(h)
    h_lower = max(1.0, math.exp(centre - z * se_lnh))
    h_upper = math.exp(centre + z * se_lnh)

    def to_i2_percent(h_value: float) -> float:
        proportion = (h_value * h_value - 1) / (h_value * h_value)
        return min(100.0, max(0.0, 100 * proportion))

    i2_percent = max(0.0, (q - df) / q) * 100 if q > 0 else 0.0
    return {
        "i2_percent": i2_percent,
        "i2_ci_percent": (to_i2_percent(h_lower), to_i2_percent(h_upper)),
        "h": h,
        "h_ci": (h_lower, h_upper),
        "level": level,
        "method": METHOD,
        "input_data": copy.deepcopy({"q": q, "k": k, "level": level}),
        "warnings": warnings,
    }
