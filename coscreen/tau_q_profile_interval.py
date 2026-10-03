"""Intervals for the between-study variance tau-squared from the Q statistic.

Two functions live here, and they are different statistical methods that must
not be conflated:

``tau_q_profile_interval(q, k, ...)``
    The contract entry point of this module. It takes only the heterogeneity
    statistic ``Q``, the number of studies ``k``, and the DerSimonian-Laird
    constant ``C = sum(w) - sum(w^2)/sum(w)`` (or the inverse-variance
    ``weights`` to form it) and inverts the noncentral chi-square distribution
    ``Q ~ chi-squared(k-1, lambda = tau^2 * C)``: the confidence limits are the
    noncentrality values whose 2.5%/97.5% quantiles land on the observed ``Q``,
    rescaled by ``tau^2 = lambda / C``. This is the test-based noncentral
    inversion in the Higgins & Thompson (2002) family (Stata ``heterogi``,
    option ``ncchi2``). It is an approximation, and -- despite the module name,
    which follows the development tasking -- it is *not* the Q-profile method
    of Viechtbauer (2007); see ``docs/methodology/tau_q_profile_interval.md``.

``q_profile_tau2_interval(effects, sampling_variances)``
    The Q-profile method itself. For each candidate ``tau^2`` it recomputes
    ``Q(tau^2)`` with weights ``1 / (v_i + tau^2)``; when ``tau^2`` is the true
    value this statistic follows a central chi-squared distribution with
    ``k - 1`` degrees of freedom exactly (under normality), so the interval is
    obtained by inverting the central quantiles. This reproduces
    ``metafor::confint.rma.uni`` (default ``type = "QP"``) to machine
    precision and is exact under the normal random-effects model.

Neither function reads data files; both are pure calculations.
"""

from __future__ import annotations

import copy
import math
import sys
from collections.abc import Sequence
from numbers import Integral, Real

from scipy.optimize import brentq
from scipy.stats import chi2, ncx2

NONCENTRAL_METHOD = "noncentral_chi_square_inversion"
Q_PROFILE_METHOD = "viechtbauer_2007_q_profile"

_MAX_SEARCH = sys.float_info.max / 4


def tau_q_profile_interval(
    q: float,
    k: int,
    *,
    level: float = 0.95,
    c_constant: float | None = None,
    weights: Sequence[float] | None = None,
) -> dict:
    """Return the noncentral chi-square inversion interval for tau^2.

    ``q`` is the heterogeneity statistic Q (Cochran's Q, a Pearson chi-square
    type statistic) computed with inverse-variance weights from ``k``
    independent studies, so its degrees of freedom are ``k - 1``. Exactly one
    of ``c_constant`` (the DerSimonian-Laird constant ``C = sum(w) -
    sum(w^2)/sum(w)`` of the same synthesis) and ``weights`` (the
    inverse-variance weights ``w = 1/v`` used for ``Q``, from which ``C`` is
    formed) must be supplied; passing neither or both raises ``TypeError`` so
    no constant is silently assumed. ``k`` must be an integer of at least two,
    ``q`` finite and non-negative, ``level`` strictly between zero and one,
    ``c_constant`` finite and positive, and ``weights`` finite, positive and
    of length ``k``; otherwise ``ValueError`` is raised.

    Under the working approximation ``Q ~ chi-squared(k - 1, lambda = tau^2 *
    C)`` the limits solve, for the noncentrality ``lambda``,

    ```text
    P(chi-squared(k-1, lambda_lower) <= q) = 1 - (1 - level)/2
    P(chi-squared(k-1, lambda_upper) <= q) = (1 - level)/2
    ```

    and are mapped to the variance scale by ``tau^2 = lambda / C``. The lower
    limit is truncated at zero. When even ``lambda = 0`` puts the observed
    ``q`` below the ``(1 - level)/2`` quantile -- less dispersion than
    homogeneity predicts -- no non-negative ``lambda`` is consistent and the
    interval collapses to ``[0, 0]`` with a warning. The distribution functions
    come from ``scipy.stats.ncx2``. The point estimate is the DerSimonian-Laird
    ``max(0, (q - (k-1))/C)``; ``i2``/``h2`` and their intervals are the
    monotone transformations through the typical variance ``v = (k-1)/C``
    (``I^2 = tau^2/(tau^2 + v)``, ``H^2 = 1 + tau^2/v``), with ``i2`` in
    percent truncated to ``[0, 100]``.

    The result carries ``method`` = ``"noncentral_chi_square_inversion"``, a
    deep copy of the inputs, and ``warnings``. ``tau2_ci[1]`` is ``None`` only
    when the upper search exhausts floating-point range without bracketing, in
    which case ``converged`` is false and ``upper_unbounded`` is true. The
    interval is approximate: it shares the first moment of ``Q`` but not its
    exact (weighted chi-square) law, so it is narrower or wider than the exact
    Q-profile interval depending on the weight configuration.
    """
    if (c_constant is None) == (weights is None):
        raise TypeError(
            "pass exactly one of c_constant or weights; the DerSimonian-Laird "
            "constant is never assumed"
        )
    if isinstance(k, bool) or not isinstance(k, Integral):
        raise ValueError("k must be an integer number of studies")
    k = int(k)
    if k < 2:
        raise ValueError("tau_q_profile_interval needs at least two studies so df = k - 1 is at least 1")
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

    if c_constant is not None:
        if isinstance(c_constant, bool) or not isinstance(c_constant, Real):
            raise ValueError("c_constant must be a finite positive number")
        try:
            c = float(c_constant)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("c_constant must be a finite positive number") from exc
        if not math.isfinite(c) or c <= 0:
            raise ValueError("c_constant must be finite and positive")
        input_data: dict = {"q": q, "k": k, "level": level, "c_constant": c}
    else:
        weights = list(weights)  # type: ignore[arg-type]
        if len(weights) != k:
            raise ValueError("weights must hold exactly k studies, matching q")
        if any(isinstance(value, bool) or not isinstance(value, Real) for value in weights):
            raise ValueError("weights must be finite positive numbers")
        try:
            w = [float(value) for value in weights]
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("weights must be finite positive numbers") from exc
        if not all(math.isfinite(value) and value > 0 for value in w):
            raise ValueError("weights must be finite positive numbers")
        c = sum(w) - sum(value * value for value in w) / sum(w)
        if not math.isfinite(c) or c <= 0:
            raise ValueError("weights give a non-positive DerSimonian-Laird constant C")
        input_data = {"q": q, "k": k, "level": level, "weights": [copy.deepcopy(value) for value in w]}

    df = k - 1
    alpha = 1.0 - level
    warnings: list[str] = []
    if q <= df:
        warnings.append(
            f"q={q} is at or below its degrees of freedom df={df}; the DerSimonian-Laird "
            "point estimate is truncated at 0 and the interval starts at 0."
        )

    # ncx2.cdf(q, df, nc) is continuous and strictly decreasing in nc.
    def noncentrality_cdf(nc: float, target: float) -> float:
        return float(ncx2.cdf(q, df, nc)) - target

    def solve_noncentrality(target: float) -> float | None:
        """Solve cdf(q; nc) = target for nc > 0; None when the root is <= 0."""
        if noncentrality_cdf(0.0, target) <= 0:
            return None
        right = max(1.0, q)
        while noncentrality_cdf(right, target) > 0:
            if right >= _MAX_SEARCH:
                return None
            right = min(_MAX_SEARCH, 2.0 * right)
        return brentq(
            lambda nc: noncentrality_cdf(nc, target), 0.0, right,
            xtol=1e-12, rtol=4 * sys.float_info.epsilon, maxiter=1000,
        )

    lambda_lower = solve_noncentrality(1.0 - alpha / 2)
    lower = 0.0 if lambda_lower is None else lambda_lower / c

    lambda_upper = solve_noncentrality(alpha / 2)
    if lambda_upper is None and noncentrality_cdf(0.0, alpha / 2) <= 0:
        # Even tau^2 = 0 puts q below the lower quantile: the acceptance set is
        # empty and the interval is reported as the single point [0, 0].
        upper: float | None = 0.0
        unbounded = False
        converged = True
        warnings.append(
            f"q={q} is at or below the {(1 - level) / 2:.3g} quantile of the central "
            f"chi-squared with df={df}, so no non-negative noncentrality is consistent "
            "with it; the interval is reported as the single point [0, 0]."
        )
    elif lambda_upper is None:
        upper = None
        unbounded = True
        converged = False
    else:
        upper = lambda_upper / c
        unbounded = False
        converged = True

    tau2 = max(0.0, (q - df) / c)
    typical = df / c

    def to_i2_percent(value: float) -> float:
        return min(100.0, max(0.0, 100.0 * value / (typical + value)))

    i2 = to_i2_percent(tau2)
    i2_lower = to_i2_percent(lower)
    i2_upper = None if upper is None else to_i2_percent(upper)
    h2 = 1.0 + tau2 / typical
    h2_lower = 1.0 + lower / typical
    h2_upper = None if upper is None else 1.0 + upper / typical

    return {
        "tau2": tau2,
        "tau2_ci": (lower, upper),
        "i2": i2,
        "i2_ci": (i2_lower, i2_upper),
        "h2": h2,
        "h2_ci": (h2_lower, h2_upper),
        "level": level,
        "method": NONCENTRAL_METHOD,
        "converged": converged,
        "upper_unbounded": unbounded,
        "input_data": copy.deepcopy(input_data),
        "warnings": warnings,
    }


def _q_at_tau2(effects, sampling_variances, tau2: float) -> float:
    """The Q statistic recomputed with weights 1 / (v_i + tau^2).

    At ``tau2 = 0`` this is Cochran's Q with inverse-variance weights. When
    ``tau2`` is the true between-study variance, this statistic follows a
    central chi-squared distribution with ``k - 1`` degrees of freedom exactly
    (normal random-effects model), which is what makes the Q-profile interval
    exact; the matrix proof is ``P (D + tau^2 I) P = P`` for the weighted
    projector ``P``, so the quadratic form is chi-squared with the rank of
    ``P``, namely ``k - 1``.
    """
    if tau2 == 0.0:
        w = [1.0 / v for v in sampling_variances]
    else:
        w = [1.0 / (v + tau2) for v in sampling_variances]
    total_w = math.fsum(w)
    mean = math.fsum(wi * y for wi, y in zip(w, effects)) / total_w
    return math.fsum(wi * (y - mean) ** 2 for wi, y in zip(w, effects))


def q_profile_tau2_interval(
    effects: Sequence[float], sampling_variances: Sequence[float], *, level: float = 0.95
) -> dict:
    """Return the Viechtbauer (2007) Q-profile interval for tau^2.

    Inputs are independent study effects and their known, positive sampling
    variances on one common analysis scale; at least two studies are required.
    The limits invert the profiled statistic ``Q(tau^2)`` (weights
    ``1/(v_i + tau^2)``) against the central chi-squared quantiles with
    ``k - 1`` degrees of freedom:

    ```text
    lower: Q(tau^2) = chi2_{1-(1-level)/2}(k-1)   (else 0)
    upper: Q(tau^2) = chi2_{(1-level)/2}(k-1)
    ```

    ``Q(tau^2)`` is decreasing in ``tau^2``, so each equation has at most one
    non-negative root. When the observed ``Q(0)`` already sits below the
    lower quantile the interval collapses to ``[0, 0]`` (metafor's ``ci.null``);
    when it sits at or below the upper quantile the lower limit is 0. The point
    estimate reported alongside is the DerSimonian-Laird
    ``max(0, (Q - (k-1))/C)`` with ``C = sum(1/v) - sum(1/v^2)/sum(1/v)``.

    ``upper`` is ``None`` only when the search exhausts floating-point range
    without bracketing; then ``converged`` is false and ``upper_unbounded`` is
    true. Invalid inputs raise ``ValueError``. This is the computation behind
    ``metafor::confint.rma.uni`` with its default ``type = "QP"``.
    """
    try:
        if any(isinstance(value, bool) or not isinstance(value, Real) for value in effects) or \
                any(isinstance(value, bool) or not isinstance(value, Real) for value in sampling_variances):
            raise ValueError("effects and sampling variances must be finite numbers")
        y = [float(value) for value in effects]
        v = [float(value) for value in sampling_variances]
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("effects and sampling variances must be finite numbers") from exc
    if len(y) != len(v):
        raise ValueError("effects and sampling variances must have the same length")
    k = len(y)
    if k < 2:
        raise ValueError("q_profile_tau2_interval needs at least two studies")
    if not all(math.isfinite(value) for value in y) or not all(math.isfinite(value) for value in v):
        raise ValueError("effects and sampling variances must be finite")
    if any(value <= 0 for value in v):
        raise ValueError("sampling variances must be positive")
    if isinstance(level, bool) or not isinstance(level, Real):
        raise ValueError("level must be a number strictly between 0 and 1")
    level = float(level)
    if not math.isfinite(level) or not 0 < level < 1:
        raise ValueError("level must be a number strictly between 0 and 1")

    df = k - 1
    alpha = 1.0 - level
    q_obs = _q_at_tau2(y, v, 0.0)
    crit_upper = float(chi2.ppf(1.0 - alpha / 2, df))
    crit_lower = float(chi2.ppf(alpha / 2, df))
    warnings: list[str] = []

    def delta(tau2: float, critical: float) -> float:
        return _q_at_tau2(y, v, tau2) - critical

    def solve(critical: float) -> float | None:
        if delta(0.0, critical) <= 0:
            return None
        right = max(1.0, q_obs)
        while delta(right, critical) > 0:
            if right >= _MAX_SEARCH:
                return None
            right = min(_MAX_SEARCH, 2.0 * right)
        return brentq(
            lambda tau2: delta(tau2, critical), 0.0, right,
            xtol=1e-12, rtol=4 * sys.float_info.epsilon, maxiter=1000,
        )

    lower_root = solve(crit_upper)
    lower = 0.0 if lower_root is None else lower_root

    upper_root = solve(crit_lower)
    if upper_root is None and delta(0.0, crit_lower) <= 0:
        upper: float | None = 0.0
        unbounded = False
        converged = True
        if q_obs > 0.0:
            warnings.append(
                f"Q={q_obs} lies at or below the {(1 - level) / 2:.3g} quantile of the "
                f"chi-squared with df={df}; the Q-profile acceptance set is empty and the "
                "interval is reported as the single point [0, 0]."
            )
    elif upper_root is None:
        upper = None
        unbounded = True
        converged = False
    else:
        upper = upper_root
        unbounded = False
        converged = True

    w_inv = [1.0 / value for value in v]
    c = math.fsum(w_inv) - math.fsum(value * value for value in w_inv) / math.fsum(w_inv)
    estimate = max(0.0, (q_obs - df) / c) if c > 0 else 0.0
    if q_obs <= df:
        warnings.append(
            f"Q={q_obs} is at or below its degrees of freedom df={df}; the "
            "DerSimonian-Laird point estimate is truncated at 0."
        )

    return {
        "estimate": estimate,
        "lower": lower,
        "upper": upper,
        "confidence_level": level,
        "converged": converged,
        "upper_unbounded": unbounded,
        "method": Q_PROFILE_METHOD,
        "input_data": copy.deepcopy({"effects": y, "sampling_variances": v, "level": level}),
        "warnings": warnings,
    }
