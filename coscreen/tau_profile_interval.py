"""95% REML profile-likelihood interval for one inverse-variance tau².

Inputs are independent study effects and their known sampling variances on
the same analysis scale. The returned interval is on the variance scale.
"""

from __future__ import annotations

import math
import sys
from collections.abc import Sequence

import numpy as np
from scipy.optimize import brentq
from scipy.stats import chi2

from coscreen.meta_regression_model import _profile_objective, profile_reml_tau2


def profile_likelihood_tau2_interval(
    effects: Sequence[float], sampling_variances: Sequence[float]
) -> dict[str, float | bool | None]:
    """Return a 95% profile-REML likelihood-ratio interval for tau².

    Requires at least three independent studies and positive, known sampling
    variances. ``lower`` is constrained to zero. ``upper`` is ``None`` only
    when the LR cutoff is not bracketed before floating-point range is
    exhausted; in that case ``converged`` is false and ``upper_unbounded`` is
    true. Numerical root-finding failures raise ``RuntimeError``.
    """
    try:
        y = np.asarray(effects, dtype=float)
        vi = np.asarray(sampling_variances, dtype=float)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("effects and sampling variances must be finite numbers") from exc
    if y.ndim != 1 or vi.ndim != 1 or y.size < 3 or vi.size != y.size:
        raise ValueError("profile interval needs at least three matching study effects and variances")
    if not np.isfinite(y).all() or not np.isfinite(vi).all() or np.any(vi <= 0):
        raise ValueError("effects must be finite and sampling variances finite and positive")

    try:
        tau2_hat, min_objective = profile_reml_tau2(y, vi)
    except ValueError as exc:
        raise RuntimeError("REML profile optimization failed") from exc
    scale = max(float(np.var(y)), float(np.mean(vi)), float(np.max(vi)))
    if not math.isfinite(scale) or scale <= 0:
        raise ValueError("REML variance scale is not finite and positive")

    design = np.ones((y.size, 1), dtype=float)
    cutoff = float(chi2.ppf(0.95, 1))
    threshold = min_objective + cutoff

    def delta(scaled_tau2: float) -> float:
        tau2 = scaled_tau2 * scale
        if not math.isfinite(tau2):
            return math.inf
        objective = _profile_objective(y, design, vi, tau2)
        return objective - threshold if math.isfinite(objective) else math.inf

    tau2_hat_scaled = tau2_hat / scale
    try:
        at_zero = delta(0.0)
        lower = 0.0 if at_zero <= 0 else brentq(
            delta, 0.0, tau2_hat_scaled, xtol=1e-12,
            rtol=4 * sys.float_info.epsilon, maxiter=1000,
        ) * scale

        max_scaled_tau2 = sys.float_info.max / max(scale, 1.0)
        right = min(max_scaled_tau2, max(1.0, 2.0 * tau2_hat_scaled))
        while delta(right) < 0 and right < max_scaled_tau2:
            next_right = min(max_scaled_tau2, 2.0 * right)
            if next_right <= right:
                break
            right = next_right
        if delta(right) < 0:
            return {
                "estimate": tau2_hat,
                "lower": lower,
                "upper": None,
                "confidence_level": 0.95,
                "converged": False,
                "upper_unbounded": True,
            }
        upper = brentq(
            delta, tau2_hat_scaled, right, xtol=1e-12,
            rtol=4 * sys.float_info.epsilon, maxiter=1000,
        ) * scale
    except (ValueError, RuntimeError, OverflowError, FloatingPointError) as exc:
        raise RuntimeError("profile-likelihood interval root-finding failed") from exc

    if not math.isfinite(lower) or not math.isfinite(upper):
        raise RuntimeError("profile-likelihood interval produced a non-finite bound")
    return {
        "estimate": tau2_hat,
        "lower": lower,
        "upper": upper,
        "confidence_level": 0.95,
        "converged": True,
        "upper_unbounded": False,
    }
