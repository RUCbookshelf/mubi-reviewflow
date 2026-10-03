"""Parametric bootstrap intervals for the pooled effect of a synthesis.

The module is a third interval family alongside the Wald / modified HKSJ
intervals of ``coscreen.review_analysis`` and the likelihood-based tau-squared
intervals of ``coscreen.tau_profile_interval`` / ``coscreen.tau_q_profile_interval``.
It is a pure calculation: no data files are read.

For each of ``n_boot`` replicates every study effect is redrawn from its own
sampling distribution, ``y*_i ~ N(y_i, se_i)``, and the selected model
(``fixed`` / ``dl`` / ``reml``) is refitted on the replicate; the interval for
the pooled effect is read off the empirical distribution of the ``n_boot``
refitted pooled estimates. This is the *first-level* (within-study) parametric
bootstrap of Van den Noortgate & Onghena (2005): the resampling model is the
per-study sampling law, not the marginal random-effects law
``N(mu_hat, tau2_hat + v_i)`` used by the marginal-design variant (metafor's
bootstrapping tutorial). Consequences, documented and warned about:

- For ``model="fixed"`` the design is the textbook parametric bootstrap of the
  inverse-variance weighted mean: its first two bootstrap moments equal the
  Wald mean and standard error exactly, so the percentile interval converges
  to the Wald interval as ``n_boot`` grows.
- For random-effects models tau-squared is re-estimated in every replicate
  (DerSimonian-Laird or profile REML, truncated at zero exactly like
  ``coscreen.review_analysis``), so the bootstrap spread includes the noise of
  heterogeneity estimation.
- Because the observed study effects are frozen as the resampling centres, the
  design does *not* regenerate the between-study spread of the true effects.
  Under heterogeneity its intervals are therefore narrower than the marginal
  bootstrap, HKSJ or profile-likelihood intervals, and they target the
  conditional (studies-fixed) sampling distribution. Report them as a
  sensitivity analysis next to those intervals, not as a replacement.

A non-parametric (resample-the-studies) bootstrap was deliberately not
implemented: with one effect per study and a fixed small k it duplicates
studies, draws about 37 percent of studies zero times ((1-1/k)^k), treats the
sampling variances as attributes that travel with the resampled rows, and
produces a discrete tau-squared estimator law -- all of which misrepresent the
known sampling model that a meta-analysis provides. Only ``percentile`` and
``basic`` (pivotal / reverse-percentile, Davison & Hinkley 1997 equation 5.6)
intervals are implemented; the BCa interval is not (see the methodology
document for the reasons).
"""

from __future__ import annotations

import copy
import math
import time
from collections.abc import Sequence
from numbers import Integral, Real

import numpy as np

MODELS = ("fixed", "dl", "reml")
INTERVAL_TYPES = ("percentile", "basic")
METHODS = {
    "percentile": "parametric_bootstrap_percentile",
    "basic": "parametric_bootstrap_basic",
}
MODEL_LABELS = {
    "fixed": "fixed-effect inverse variance",
    "dl": "DerSimonian-Laird random effects",
    "reml": "restricted maximum likelihood random effects",
}

#: Hard upper bound on ``n_boot``; protects memory and run time. Replicates are
#: processed in chunks, so peak memory stays near this bound for any k.
MAX_N_BOOT = 200_000

# Bootstrap rows are processed in chunks of at most ~2e6 cells so the
# (chunk, k) intermediates stay a few tens of megabytes for any k.
_MAX_CHUNK_CELLS = 2_000_000

_INVPHI = (math.sqrt(5.0) - 1.0) / 2.0  # 1 / golden ratio, golden-section step
_REML_XATOL = 1e-12  # same scaled tolerance as coscreen.meta_regression_model
_REML_BOUNDARY = 1e-6  # same "fit at bracket edge" test as the house REML
_REML_MAX_ROUNDS = 40  # same bracket-doubling budget as the house REML


def _finite_floats(values: Sequence[float], label: str) -> list[float]:
    try:
        converted = list(values)
    except TypeError as exc:
        raise ValueError(f"{label} must be a sequence of finite numbers") from exc
    result = []
    for value in converted:
        if isinstance(value, (bool, np.bool_)) or not isinstance(value, Real):
            raise ValueError(f"{label} must be a sequence of finite numbers")
        try:
            number = float(value)
        except (OverflowError, TypeError, ValueError) as exc:
            raise ValueError(f"{label} must be a sequence of finite numbers") from exc
        if not math.isfinite(number):
            raise ValueError(f"{label} must be a sequence of finite numbers")
        result.append(number)
    return result


def _reml_objective_rows(y: np.ndarray, v: np.ndarray, tau2: np.ndarray) -> np.ndarray:
    """-2 profile restricted log likelihood (constants omitted), intercept only.

    Same objective as ``coscreen.meta_regression_model._profile_objective`` with
    a single-column design, re-implemented for row batches: ``tau2`` has one
    entry per replicate, so the objective is evaluated for all replicates of a
    chunk at once. Non-finite values are mapped to +inf, matching the house
    convention of rejecting candidates that overflow.
    """
    variance = v + tau2[:, None]
    with np.errstate(over="ignore", divide="ignore", invalid="ignore"):
        weight = 1.0 / variance
        total_w = weight.sum(axis=1)
        beta = (weight * y).sum(axis=1) / total_w
        residual = y - beta[:, None]
        value = np.log(variance).sum(axis=1) + np.log(total_w) \
            + (weight * residual**2).sum(axis=1)
    return np.where(np.isfinite(value), value, np.inf)


def _reml_tau2_rows(y: np.ndarray, v: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Profile-REML tau-squared for a batch of intercept-only replications.

    The objective, the variance rescaling ``max(var(y), mean(v), max(v))``,
    the bracket doubling and the tau2 = 0 boundary test replicate
    ``coscreen.meta_regression_model._reml_tau2`` formula by formula; the
    scalar Brent search is replaced by a vectorized golden-section search with
    the same scaled tolerance, so every replicate of a chunk is optimized
    simultaneously. Returns the tau-squared estimates and a boolean validity
    mask (a replicate is invalid only if its optimization failed numerically).
    """
    n_rows = y.shape[0]
    scale = np.maximum(
        y.var(axis=1), max(float(np.mean(v)), float(np.max(v)))
    )
    if not np.all(np.isfinite(scale)) or np.any(scale <= 0):
        return np.zeros(n_rows), np.zeros(n_rows, dtype=bool)

    lo = np.zeros(n_rows)
    hi = np.ones(n_rows)
    active = np.arange(n_rows)
    for _ in range(_REML_MAX_ROUNDS):
        if active.size == 0:
            break
        a = lo[active].copy()
        b = hi[active].copy()
        x1 = b - _INVPHI * (b - a)
        x2 = a + _INVPHI * (b - a)
        f1 = _reml_objective_rows(y[active], v, x1 * scale[active])
        f2 = _reml_objective_rows(y[active], v, x2 * scale[active])
        for _ in range(200):
            if float(np.max(b - a)) < _REML_XATOL:
                break
            left = f1 <= f2
            a_new = np.where(left, a, x1)
            b_new = np.where(left, x2, b)
            x1_new = b_new - _INVPHI * (b_new - a_new)
            x2_new = a_new + _INVPHI * (b_new - a_new)
            # Golden-section bookkeeping: one interior point is always carried
            # over (with its objective value), the mirrored one is new.
            f1_new = np.where(
                left, _reml_objective_rows(y[active], v, x1_new * scale[active]), f2
            )
            f2_new = np.where(left, f1, _reml_objective_rows(y[active], v, x2_new * scale[active]))
            a, b, x1, x2, f1, f2 = a_new, b_new, x1_new, x2_new, f1_new, f2_new
        x_hat = (a + b) / 2
        at_edge = x_hat >= hi[active] * (1.0 - _REML_BOUNDARY)
        keep = ~at_edge
        lo[active[keep]], hi[active[keep]] = a[keep], b[keep]
        if not at_edge.any():
            break
        expand = active[at_edge]
        hi[expand] *= 10.0
        active = expand

    tau2 = (lo + hi) / 2 * scale
    f_min = _reml_objective_rows(y, v, tau2)
    f_zero = _reml_objective_rows(y, v, np.zeros(n_rows))
    tau2 = np.where(f_zero <= f_min, 0.0, tau2)
    valid = np.isfinite(tau2) & np.isfinite(f_min)
    return np.where(valid, tau2, 0.0), valid


def _fit_pooled(y: np.ndarray, v: np.ndarray, model: str) -> dict:
    """Fit the selected model on one dataset (the original data).

    The formulas follow ``coscreen.review_analysis._synthesize_rows``:
    inverse-variance pooling; DerSimonian-Laird tau-squared
    ``max(0, (Q - df) / C)`` with ``C = sum(w) - sum(w^2)/sum(w)``; profile
    REML tau-squared for ``model="reml"``; pooled weights ``1/(v + tau2)``
    for both random-effects models.
    """
    w = 1.0 / v
    total_w = float(w.sum())
    pooled = float(w @ y / total_w)
    q = float(w @ (y - pooled) ** 2)
    df = y.size - 1
    c = total_w - float((w**2).sum()) / total_w
    tau2 = 0.0
    if model == "dl":
        if not math.isfinite(c) or c <= 0:
            raise ValueError("sampling variances give a non-positive DerSimonian-Laird constant C")
        tau2 = max(0.0, (q - df) / c)
    elif model == "reml":
        tau2_rows, valid_rows = _reml_tau2_rows(y[None, :], v)
        if not bool(valid_rows[0]):
            raise ValueError("REML profile optimization failed on the input data")
        tau2 = float(tau2_rows[0])
    weights = w if model == "fixed" else 1.0 / (v + tau2)
    total = float(weights.sum())
    pooled = float(weights @ y / total)
    pooled_se = math.sqrt(1.0 / total)
    return {
        "pooled": pooled,
        "pooled_se": pooled_se,
        "tau2": tau2,
        "q": q,
        "df": df,
        "c": c,
    }


def _bootstrap_pooled(
    rng: np.random.Generator,
    y: np.ndarray,
    se: np.ndarray,
    v: np.ndarray,
    model: str,
    n_boot: int,
    fit: dict,
) -> tuple[np.ndarray, np.ndarray | None, int]:
    """Draw the replicates and refit the model on each; return pooled draws.

    Rows whose refit failed numerically are excluded (counted, warned about)
    rather than silently forced into the distribution.
    """
    chunk = max(1, min(n_boot, _MAX_CHUNK_CELLS // y.size))
    keep = np.ones(n_boot, dtype=bool)
    pooled_boot = np.empty(n_boot)
    tau2_boot = np.empty(n_boot) if model != "fixed" else None
    w = 1.0 / v
    total_w = float(w.sum())
    for start in range(0, n_boot, chunk):
        rows = min(chunk, n_boot - start)
        stop = start + rows
        y_star = y + rng.standard_normal((rows, y.size)) * se
        if model == "fixed":
            pooled = y_star @ w / total_w
            ok = np.isfinite(pooled)
            pooled_boot[start:stop] = pooled
        else:
            if model == "dl":
                fixed_star = y_star @ w / total_w
                q_star = ((y_star - fixed_star[:, None]) ** 2 * w).sum(axis=1)
                tau2_star = np.maximum(0.0, (q_star - fit["df"]) / fit["c"])
                ok = np.isfinite(tau2_star)
            else:
                tau2_star, ok = _reml_tau2_rows(y_star, v)
            weights = 1.0 / (v + tau2_star[:, None])
            pooled = (weights * y_star).sum(axis=1) / weights.sum(axis=1)
            ok &= np.isfinite(pooled)
            pooled_boot[start:stop] = pooled
            tau2_boot[start:stop] = tau2_star
        keep[start:stop] = ok
    invalid = int(keep.size - np.count_nonzero(keep))
    if invalid:
        pooled_boot = pooled_boot[keep]
        if tau2_boot is not None:
            tau2_boot = tau2_boot[keep]
    return pooled_boot, tau2_boot, invalid


def _batch_mc_se(pooled_boot: np.ndarray, theta_hat: float, alpha: float,
                 interval_type: str) -> dict[str, float | None]:
    """Batch-means Monte Carlo standard errors of the two interval endpoints.

    The replicate stream is split into ten contiguous batches; the endpoint is
    recomputed inside every batch and the reported standard error is the
    batch-to-batch standard deviation divided by sqrt(number of batches).
    """
    n_batches = 10
    if pooled_boot.size < n_batches * 20:
        return {"low": None, "high": None}
    lo_quantile, hi_quantile = alpha / 2, 1.0 - alpha / 2
    lows = []
    highs = []
    for batch in np.array_split(pooled_boot, n_batches):
        q_low, q_high = np.quantile(batch, [lo_quantile, hi_quantile])
        if interval_type == "percentile":
            lows.append(q_low)
            highs.append(q_high)
        else:
            lows.append(2.0 * theta_hat - q_high)
            highs.append(2.0 * theta_hat - q_low)
    se_low = float(np.std(lows, ddof=1) / math.sqrt(n_batches))
    se_high = float(np.std(highs, ddof=1) / math.sqrt(n_batches))
    return {"low": se_low, "high": se_high}


def bootstrap_pooled_interval(
    effects: Sequence[float],
    ses: Sequence[float],
    *,
    model: str,
    interval_type: str,
    n_boot: int = 5000,
    seed: int,
    level: float = 0.95,
) -> dict:
    """Return a parametric bootstrap interval for the pooled effect.

    ``effects`` are independent study effect estimates and ``ses`` their
    standard errors on one common analysis scale (log scale for ratio
    measures); at least two studies with positive, finite SEs are required.
    ``model`` selects the refitted synthesis model, ``"fixed"``, ``"dl"`` or
    ``"reml"``; ``interval_type`` selects ``"percentile"`` (quantiles of the
    bootstrap distribution) or ``"basic"`` (pivotal reflection around the
    original-data point estimate). ``n_boot`` (1 to 200000), ``seed`` and
    ``level`` in (0, 1) are validated; invalid inputs raise ``ValueError``.

    Every replicate redraws each study effect from ``N(y_i, se_i)`` and the
    selected model is refitted exactly as ``coscreen.review_analysis`` fits it
    (same pooling formulas, same DerSimonian-Laird constant, same profile REML
    objective, tau-squared truncated at zero). The returned dict carries the
    original-data fit (``point_estimate``, ``pooled_se``, ``tau2``, ``q``,
    ``q_df``, ``c_constant``), the interval (``interval`` tuple, also
    ``ci_low``/``ci_high``), the bootstrap distribution summary (``boot_summary``
    with mean, SD, extrema, median and the alpha/2, quartile and 1-alpha/2
    quantiles, so the interval can be recomputed and audited), the batch-means
    Monte Carlo standard errors of both endpoints (``mc_se``), the fraction of
    replicates whose tau-squared hit the zero boundary
    (``tau2_boundary_fraction``, random-effects models), the number of dropped
    replicates (``n_invalid_boot``), the elapsed seconds, the ``model`` and
    ``method`` identifiers, a deep copy of the inputs (``input_data``) and
    ``warnings``.
    """
    if not isinstance(model, str) or model not in MODELS:
        raise ValueError("model must be one of: fixed, dl, reml")
    if not isinstance(interval_type, str) or interval_type not in INTERVAL_TYPES:
        raise ValueError("interval_type must be one of: percentile, basic")
    if isinstance(level, bool) or not isinstance(level, Real):
        raise ValueError("level must be a number strictly between 0 and 1")
    level = float(level)
    if not math.isfinite(level) or not 0 < level < 1:
        raise ValueError("level must be a number strictly between 0 and 1")
    if isinstance(n_boot, bool) or not isinstance(n_boot, Integral):
        raise ValueError(f"n_boot must be an integer between 1 and {MAX_N_BOOT}")
    n_boot = int(n_boot)
    if not 1 <= n_boot <= MAX_N_BOOT:
        raise ValueError(f"n_boot must be an integer between 1 and {MAX_N_BOOT}")
    if isinstance(seed, bool) or not isinstance(seed, Integral):
        raise ValueError("seed must be a non-negative integer")
    seed = int(seed)
    if seed < 0:
        raise ValueError("seed must be a non-negative integer")

    y_list = _finite_floats(effects, "effects")
    se_list = _finite_floats(ses, "ses")
    if len(y_list) != len(se_list):
        raise ValueError("effects and ses must have the same length")
    k = len(y_list)
    if k < 2:
        raise ValueError("bootstrap_pooled_interval needs at least two studies")
    if any(value <= 0 for value in se_list):
        raise ValueError("ses must be positive")

    started = time.perf_counter()
    y = np.asarray(y_list, dtype=float)
    se = np.asarray(se_list, dtype=float)
    with np.errstate(over="ignore", invalid="ignore"):
        v = se**2
    if not np.isfinite(v).all() or np.any(v <= 0):
        raise ValueError("squared SE values must be finite and positive")

    fit = _fit_pooled(y, v, model)
    rng = np.random.default_rng(seed)
    pooled_boot, tau2_boot, n_invalid = _bootstrap_pooled(
        rng, y, se, v, model, n_boot, fit
    )
    if pooled_boot.size < 1:
        raise RuntimeError("every bootstrap replicate failed numerically")

    alpha = 1.0 - level
    q_low, q_high = (float(value) for value in np.quantile(
        pooled_boot, [alpha / 2, 1 - alpha / 2], method="linear"))
    if interval_type == "percentile":
        low, high = q_low, q_high
    else:
        low, high = 2 * fit["pooled"] - q_high, 2 * fit["pooled"] - q_low

    mc_se = _batch_mc_se(pooled_boot, fit["pooled"], alpha, interval_type)
    quantile_grid = [alpha / 2, 0.25, 0.5, 0.75, 1 - alpha / 2]
    boot_summary = {
        "n_valid": int(pooled_boot.size),
        "mean": float(pooled_boot.mean()),
        "sd": float(pooled_boot.std(ddof=1)) if pooled_boot.size > 1 else 0.0,
        "minimum": float(pooled_boot.min()),
        "maximum": float(pooled_boot.max()),
        "median": float(np.quantile(pooled_boot, 0.5)),
        "quantiles": {f"{p:g}": float(value) for p, value in zip(quantile_grid, np.quantile(pooled_boot, quantile_grid))},
        "quantile_method": "linear",
    }

    warnings: list[str] = []
    if k < 5:
        warnings.append(
            f"Only k={k} studies: percentile bootstrap intervals are known to "
            "undercover at this k and the random-effects fitting is dominated "
            "by sampling noise; compare with the modified HKSJ interval."
        )
    elif k < 10:
        warnings.append(
            f"k={k} studies is small for bootstrap inference; treat the "
            "interval as a sensitivity analysis next to HKSJ or "
            "profile-likelihood intervals."
        )
    if mc_se["low"] is not None:
        warnings.append(
            "Monte Carlo uncertainty: with n_boot="
            f"{n_boot} the endpoints carry a batch-estimated standard error of "
            f"about {mc_se['low']:.4g} (low) and {mc_se['high']:.4g} (high); "
            "it shrinks proportionally to 1/sqrt(n_boot)."
        )
    else:
        warnings.append(
            f"n_boot={n_boot} is too small to estimate the Monte Carlo "
            "standard error of the endpoints (ten batches of 20 replicates "
            "are needed); increase n_boot before reporting the interval."
        )
    warnings.append(
        "Resampling is from N(estimate_i, SE_i) per study (first-level "
        "parametric bootstrap): the observed effects are frozen as resampling "
        "centres, so the between-study spread of true effects is not "
        "regenerated and the interval can be narrower than Wald, HKSJ or "
        "profile-likelihood intervals under heterogeneity; report it "
        "alongside those intervals."
    )
    tau2_boundary_fraction = None
    if model != "fixed" and tau2_boot is not None and tau2_boot.size:
        tau2_boundary_fraction = float(np.mean(tau2_boot == 0.0))
        estimator = "DerSimonian-Laird" if model == "dl" else "profile REML"
        warnings.append(
            f"tau-squared is re-estimated by {estimator} in every bootstrap "
            f"sample and truncated at 0 (boundary in "
            f"{100 * tau2_boundary_fraction:.1f}% of samples); with k={k} the "
            "per-replicate tau-squared estimate has k-1 degrees of freedom "
            "and is unstable."
        )
    if n_invalid:
        warnings.append(
            f"{n_invalid} of {n_boot} replicates failed numerically and were "
            "dropped from the bootstrap distribution."
        )

    elapsed = time.perf_counter() - started
    return {
        "n_studies": k,
        "model": model,
        "model_label": MODEL_LABELS[model],
        "method": METHODS[interval_type],
        "interval_type": interval_type,
        "level": level,
        "n_boot": n_boot,
        "seed": seed,
        "point_estimate": fit["pooled"],
        "pooled_se": fit["pooled_se"],
        "tau2": fit["tau2"] if model != "fixed" else None,
        "q": fit["q"],
        "q_df": fit["df"],
        "c_constant": fit["c"],
        "interval": (low, high),
        "ci_low": low,
        "ci_high": high,
        "mc_se": mc_se,
        "boot_summary": boot_summary,
        "tau2_boundary_fraction": tau2_boundary_fraction,
        "n_invalid_boot": n_invalid,
        "seconds": elapsed,
        "input_data": copy.deepcopy({
            "effects": y_list,
            "ses": se_list,
            "model": model,
            "interval_type": interval_type,
            "n_boot": n_boot,
            "seed": seed,
            "level": level,
        }),
        "warnings": warnings,
    }
