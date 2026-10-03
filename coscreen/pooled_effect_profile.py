"""ML profile-likelihood interval for the random-effects pooled mean.

For independent effects y_i with known sampling variances v_i, maximize the
normal likelihood over tau² >= 0 both freely and with mu fixed. Invert the
one-degree-of-freedom likelihood-ratio test for mu. This is distinct from a
REML profile interval for tau² and from a Wald interval around a REML mean.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np
from scipy.optimize import brentq, minimize_scalar
from scipy.stats import chi2


def profile_likelihood_pooled_interval(
    effects: Sequence[float], sampling_variances: Sequence[float], *, level: float = .95
) -> dict:
    try:
        y = np.asarray(effects, dtype=float)
        v = np.asarray(sampling_variances, dtype=float)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("effects and sampling variances must be finite numbers") from exc
    if y.ndim != 1 or v.ndim != 1 or len(y) < 2 or len(y) != len(v):
        raise ValueError("at least two matching independent effects and variances are required")
    if not np.isfinite(y).all() or not np.isfinite(v).all() or np.any(v <= 0):
        raise ValueError("effects must be finite and sampling variances finite and positive")
    if not isinstance(level, (float, int)) or isinstance(level, bool) or not math.isfinite(level) or not 0 < level < 1:
        raise ValueError("level must be strictly between 0 and 1")

    center = float(np.mean(y))
    scale = max(float(np.sqrt(v.max())), float(np.std(y)))
    z, variances = (y - center) / scale, v / scale**2

    def fitted(mu: float | None) -> tuple[float, float, float]:
        residual = z - (float(np.mean(z)) if mu is None else mu)
        upper = max(10.0, 100.0 * float(np.mean(residual**2)), 100.0 * float(variances.max()))

        def objective(tau2: float) -> float:
            total_variance = variances + tau2
            weights = 1.0 / total_variance
            mean = float(np.dot(weights, z) / weights.sum()) if mu is None else mu
            return .5 * float(np.log(total_variance).sum() + np.dot(weights, (z - mean)**2))

        optimum = minimize_scalar(objective, bounds=(0.0, upper), method="bounded",
                                  options={"xatol": 1e-12})
        if not optimum.success or not math.isfinite(optimum.fun):
            raise RuntimeError("pooled-effect profile optimization failed")
        tau2 = float(optimum.x) if optimum.fun < objective(0.0) else 0.0
        weights = 1.0 / (variances + tau2)
        mean = float(np.dot(weights, z) / weights.sum()) if mu is None else mu
        return objective(tau2), tau2, mean

    minimum, tau2_ml, mu_ml = fitted(None)
    cutoff = float(chi2.ppf(level, 1))

    def difference(mu: float) -> float:
        return 2.0 * (fitted(mu)[0] - minimum) - cutoff

    step = max(float(np.sqrt(1.0 / (1.0 / (variances + tau2_ml)).sum())), .01)
    bounds = []
    for direction in (-1, 1):
        distance = step
        for _ in range(100):
            endpoint = mu_ml + direction * distance
            if difference(endpoint) >= 0:
                break
            distance *= 2
        else:
            raise RuntimeError("pooled-effect profile interval could not bracket a limit")
        bounds.append(float(brentq(difference, min(mu_ml, endpoint), max(mu_ml, endpoint),
                                   xtol=1e-10, maxiter=1000)))

    return {
        "estimate": center + scale * mu_ml,
        "ci": [center + scale * bound for bound in bounds],
        "tau2_ml": tau2_ml * scale**2,
        "level": float(level),
        "method": "ML profile likelihood for pooled random-effects mean (chi-square LR inversion)",
        "citation": "Hardy & Thompson (1996), Statistics in Medicine 15:619–629, DOI 10.1002/(SICI)1097-0258(19960330)15:6<619::AID-SIM188>3.0.CO;2-A",
        "warning": "Asymptotic likelihood-ratio limits can be inaccurate with few studies or a boundary tau-squared estimate.",
    }
