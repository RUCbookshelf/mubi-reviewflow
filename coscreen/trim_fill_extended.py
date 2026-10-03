"""R0/Q0 Duval–Tweedie trim-and-fill with FE, DL, or REML pooling; L0 with DL or REML.

L0 with fixed-effect pooling remains in :mod:`coscreen.trim_fill`; this module
rejects the L0 + FE combination and points callers there.
"""

from __future__ import annotations

import math
from statistics import NormalDist

from .trim_fill import (
    _RATIO_MEASURES,
    _number,
    _pooled_result,
    _reported_effect,
    _validated_rows,
)

_ESTIMATORS = {"R0", "Q0", "L0"}
_POOLING_METHODS = {"FE", "DL", "REML"}
_REML_MAX_BRACKET_STEPS = 256
_REML_MAX_ROOT_STEPS = 256


def _finite_sum(values: list[float], message: str) -> float:
    try:
        result = math.fsum(values)
    except (OverflowError, ValueError) as exc:
        raise ValueError(message) from exc
    if not math.isfinite(result):
        raise ValueError(message)
    return result


def _weighted_summary(effects: list[float], variances: list[float]) -> tuple[float, float]:
    try:
        weights = [1 / variance for variance in variances]
    except (OverflowError, ZeroDivisionError) as exc:
        raise ValueError("study precisions are outside the supported numeric range") from exc
    if not all(math.isfinite(weight) and weight > 0 for weight in weights):
        raise ValueError("study precisions are outside the supported numeric range")
    total_weight = _finite_sum(weights, "study precisions are outside the supported numeric range")
    weighted_effects = []
    try:
        weighted_effects = [effect * weight for effect, weight in zip(effects, weights)]
    except OverflowError as exc:
        raise ValueError("study estimates are outside the supported numeric range") from exc
    mean = _finite_sum(weighted_effects, "study estimates are outside the supported numeric range") / total_weight
    se = math.sqrt(1 / total_weight)
    if not all(math.isfinite(value) for value in (mean, se)) or se <= 0:
        raise ValueError("pooled estimate is outside the supported numeric range")
    return mean, se


def _reml_score(effects: list[float], variances: list[float], tau2: float) -> float:
    try:
        total_variances = [variance + tau2 for variance in variances]
        if any(not math.isfinite(variance) or variance <= 0 for variance in total_variances):
            raise ValueError("REML tau-squared is outside the supported numeric range")
        weights = [1 / variance for variance in total_variances]
    except (OverflowError, ZeroDivisionError) as exc:
        raise ValueError("REML tau-squared is outside the supported numeric range") from exc
    if not all(math.isfinite(weight) and weight > 0 for weight in weights):
        raise ValueError("REML weights are outside the supported numeric range")
    total_weight = _finite_sum(weights, "REML weights are outside the supported numeric range")
    sum_weight_squares = _finite_sum(
        [weight * weight for weight in weights], "REML weights are outside the supported numeric range"
    )
    mean, _ = _weighted_summary(effects, total_variances)
    try:
        residual_terms = [weight * weight * (effect - mean) ** 2 for effect, weight in zip(effects, weights)]
    except OverflowError as exc:
        raise ValueError("REML score is outside the supported numeric range") from exc
    residual_term = _finite_sum(residual_terms, "REML score is outside the supported numeric range")
    score = total_weight - sum_weight_squares / total_weight - residual_term
    if not math.isfinite(score):
        raise ValueError("REML score is outside the supported numeric range")
    return score


def _reml_tau2(effects: list[float], variances: list[float]) -> float:
    """Solve the intercept-only restricted-likelihood score on tau-squared."""
    if _reml_score(effects, variances, 0.0) >= 0:
        return 0.0

    upper = max(variances)
    if not math.isfinite(upper) or upper <= 0:
        raise ValueError("REML tau-squared is outside the supported numeric range")
    for _ in range(_REML_MAX_BRACKET_STEPS):
        score = _reml_score(effects, variances, upper)
        if score >= 0:
            break
        upper *= 2
        if not math.isfinite(upper):
            raise RuntimeError("REML tau-squared estimation did not converge")
    else:
        raise RuntimeError("REML tau-squared estimation did not converge")

    lower = 0.0
    scale = max(upper, max(variances), 1e-300)
    for _ in range(_REML_MAX_ROOT_STEPS):
        midpoint = lower + (upper - lower) / 2
        if midpoint == lower or midpoint == upper:
            break
        score = _reml_score(effects, variances, midpoint)
        if score >= 0:
            upper = midpoint
        else:
            lower = midpoint
        if upper - lower <= 1e-12 * max(scale, upper):
            break
    else:
        raise RuntimeError("REML tau-squared estimation did not converge")
    tau2 = lower + (upper - lower) / 2
    if not math.isfinite(tau2) or tau2 < 0:
        raise RuntimeError("REML tau-squared estimation did not converge")
    return tau2


def _tau2(effects: list[float], variances: list[float], pooling_method: str) -> float:
    if len(effects) < 2:
        raise ValueError("at least two studies must remain in each trimmed fit")
    if pooling_method == "FE":
        return 0.0
    if pooling_method == "REML":
        return _reml_tau2(effects, variances)

    fixed_mean, _ = _weighted_summary(effects, variances)
    weights = [1 / variance for variance in variances]
    total_weight = _finite_sum(weights, "study precisions are outside the supported numeric range")
    weight_squares = _finite_sum(
        [weight * weight for weight in weights], "study precisions are outside the supported numeric range"
    )
    c_value = total_weight - weight_squares / total_weight
    if not math.isfinite(c_value) or c_value <= 0:
        raise ValueError("DerSimonian–Laird heterogeneity estimate is outside the supported numeric range")
    try:
        q_terms = [weight * (effect - fixed_mean) ** 2 for effect, weight in zip(effects, weights)]
    except OverflowError as exc:
        raise ValueError("DerSimonian–Laird heterogeneity estimate is outside the supported numeric range") from exc
    q_value = _finite_sum(q_terms, "DerSimonian–Laird heterogeneity estimate is outside the supported numeric range")
    tau2 = max(0.0, (q_value - (len(effects) - 1)) / c_value)
    if not math.isfinite(tau2):
        raise ValueError("DerSimonian–Laird heterogeneity estimate is outside the supported numeric range")
    return tau2


def _pool(
    effects: list[float], ses: list[float], pooling_method: str
) -> tuple[float, float, float]:
    variances = [se * se for se in ses]
    tau2 = _tau2(effects, variances, pooling_method)
    total_variances = [variance + tau2 for variance in variances]
    mean, se = _weighted_summary(effects, total_variances)
    return mean, se, tau2


def _rank_sum_variance(k: int, raw_estimate: float) -> float:
    """Variance of the positive signed-rank sum shared by the L0 and Q0 estimators.

    metafor 5.2-1 evaluates this expression with the unrounded raw k0
    estimate (`trimfill.rma.uni`, `varSr <- 1/24 * (...)`).
    """
    return (
        k * (k + 1) * (2 * k + 1)
        + 10 * raw_estimate**3
        + 27 * raw_estimate**2
        + 17 * raw_estimate
        - 18 * k * raw_estimate**2
        - 18 * k * raw_estimate
        + 6 * k * k * raw_estimate
    ) / 24


def _estimate_missing(centered: list[float], estimator: str) -> tuple[float, float]:
    ranks = [0] * len(centered)
    order = sorted(range(len(centered)), key=lambda index: abs(centered[index]))
    for rank, index in enumerate(order, 1):
        ranks[index] = rank
    n = len(centered)

    if estimator == "R0":
        negative_ranks = [rank for rank, effect in zip(ranks, centered) if effect < 0]
        if not negative_ranks:
            raise ValueError("R0 estimator is undefined when no study is below the trim center")
        raw = n - max(negative_ranks) - 1
        se_missing = math.sqrt(2 * max(0.0, raw) + 2)
        return raw, se_missing

    positive_rank_sum = sum(rank for rank, effect in zip(ranks, centered) if effect > 0)
    if estimator == "L0":
        # metafor 5.2-1 trimfill.rma.uni: raw k0 = (4*Sr - k*(k+1)) / (2*k - 1),
        # then se.k0 = 4*sqrt(varSr) / (2*k - 1) with varSr evaluated at the
        # unrounded raw estimate; rounding and the zero clamp happen afterwards.
        raw = (4 * positive_rank_sum - n * (n + 1)) / (2 * n - 1)
        variance_sum = _rank_sum_variance(n, raw)
        if not math.isfinite(variance_sum) or variance_sum < 0:
            raise ValueError("L0 standard error is undefined for this signed-rank pattern")
        se_missing = 4 * math.sqrt(variance_sum) / (2 * n - 1)
        if not math.isfinite(raw) or not math.isfinite(se_missing):
            raise ValueError("L0 estimator is outside the supported numeric range")
        return raw, max(0.0, se_missing)

    radicand = 2 * n * n - 4 * positive_rank_sum + 0.25
    if not math.isfinite(radicand) or radicand < 0:
        raise ValueError("Q0 estimator is undefined for this signed-rank pattern")
    raw = n - 0.5 - math.sqrt(radicand)
    k0 = raw
    variance_sum = _rank_sum_variance(n, k0)
    se_denominator = (n - 0.5) ** 2 - k0 * (2 * n - k0 - 1)
    if not math.isfinite(variance_sum) or variance_sum < 0 or not math.isfinite(se_denominator) or se_denominator <= 0:
        raise ValueError("Q0 standard error is undefined for this signed-rank pattern")
    se_missing = 2 * math.sqrt(variance_sum) / math.sqrt(se_denominator)
    if not math.isfinite(raw) or not math.isfinite(se_missing):
        raise ValueError("Q0 estimator is outside the supported numeric range")
    return raw, max(0.0, se_missing)


def trim_and_fill_extended(
    rows: list[dict],
    measure: str,
    side: str,
    *,
    estimator: str = "R0",
    pooling_method: str = "REML",
    confidence_level: float = 0.95,
    max_iterations: int = 100,
) -> dict:
    """Run R0 or Q0 trim-and-fill with FE, DerSimonian–Laird, or REML pooling,
    or L0 with DerSimonian–Laird or REML pooling.

    The requested missing side is mandatory. L0 with fixed-effect pooling is
    the ``coscreen.trim_fill.trim_and_fill`` entry point and is rejected here.
    Ratio estimates are analyzed on the log scale and returned as ratios; their
    input SEs are already log-scale SEs. FISHER_Z stays on the Fisher z scale.
    """
    if not isinstance(side, str) or side not in {"left", "right"}:
        raise ValueError("side must be 'left' or 'right'")
    if not isinstance(estimator, str) or estimator not in _ESTIMATORS:
        raise ValueError("estimator must be 'R0', 'Q0', or 'L0'")
    if not isinstance(pooling_method, str) or pooling_method not in _POOLING_METHODS:
        raise ValueError("pooling_method must be 'FE', 'DL', or 'REML'")
    if estimator == "L0" and pooling_method == "FE":
        raise ValueError(
            "L0 with FE pooling is provided by coscreen.trim_fill.trim_and_fill; "
            "call that entry point, or select 'DL' or 'REML' pooling here"
        )
    if isinstance(max_iterations, bool) or not isinstance(max_iterations, int) or max_iterations < 1:
        raise ValueError("max_iterations must be a positive integer")

    confidence_level = _number(confidence_level, "confidence_level")
    if not 0 < confidence_level < 1:
        raise ValueError("confidence_level must be strictly between 0 and 1")
    critical = NormalDist().inv_cdf((1 + confidence_level) / 2)
    if not math.isfinite(critical) or critical <= 0:
        raise ValueError("confidence_level does not produce a finite normal quantile")

    source_rows, effects, ses = _validated_rows(rows, measure)
    oriented = effects if side == "left" else [-effect for effect in effects]
    order = sorted(range(len(oriented)), key=lambda index: oriented[index])
    sorted_effects = [oriented[index] for index in order]
    sorted_ses = [ses[index] for index in order]
    n = len(sorted_effects)

    missing = 0
    final_center = 0.0
    missing_se = 0.0
    for iterations in range(1, max_iterations + 1):
        final_center, _trimmed_se, _trimmed_tau2 = _pool(
            sorted_effects[: n - missing], sorted_ses[: n - missing], pooling_method
        )
        centered = [effect - final_center for effect in sorted_effects]
        if not all(math.isfinite(value) for value in centered):
            raise ValueError("centered study effects are outside the supported numeric range")
        raw_missing, missing_se = _estimate_missing(centered, estimator)
        updated_missing = max(0, round(raw_missing))
        if updated_missing >= n - 1:
            raise ValueError("trim-and-fill must leave at least two observed studies")
        if updated_missing == missing:
            missing = updated_missing
            break
        missing = updated_missing
    else:
        raise RuntimeError(f"trim-and-fill did not converge within {max_iterations} iterations")

    observed, observed_se, observed_tau2 = _pool(oriented, ses, pooling_method)
    imputed = []
    imputed_effects, imputed_ses = [], []
    used_ids = {row["study_id"] for row in source_rows}
    for index in range(n - missing, n):
        source = source_rows[order[index]]
        oriented_imputed = 2 * final_center - sorted_effects[index]
        if not math.isfinite(oriented_imputed):
            raise ValueError("imputed study effects are outside the supported numeric range")
        imputed_effects.append(oriented_imputed)
        imputed_ses.append(ses[order[index]])
        reported_imputed = -oriented_imputed if side == "right" else oriented_imputed
        filled_id = f"filled_{len(imputed) + 1}"
        while filled_id in used_ids:
            filled_id += "_"
        used_ids.add(filled_id)
        imputed.append({
            "study_id": filled_id,
            "source_study_id": source["study_id"],
            "measure": measure,
            "estimate": _reported_effect(reported_imputed, measure),
            "se": source["se"],
        })

    adjusted, adjusted_se, adjusted_tau2 = _pool(
        sorted_effects + imputed_effects,
        sorted_ses + imputed_ses,
        pooling_method,
    )
    if side == "right":
        observed, adjusted = -observed, -adjusted

    analysis_scale = "log" if measure in _RATIO_MEASURES else (
        "fisher_z" if measure == "FISHER_Z" else "natural"
    )
    return {
        "method": "Duval–Tweedie trim-and-fill",
        "measure": measure,
        "side": side,
        "estimator": estimator,
        "pooling_method": pooling_method,
        "n_studies": n,
        "n_missing": missing,
        "n_missing_se": missing_se,
        "n_missing_p_value": 0.5 ** (missing + 1) if estimator == "R0" else None,
        "confidence_level": confidence_level,
        "estimate_scale": "ratio" if measure in _RATIO_MEASURES else analysis_scale,
        "analysis_scale": analysis_scale,
        "se_scale": analysis_scale,
        "observed": _pooled_result(observed, observed_se, measure, critical),
        "adjusted": _pooled_result(adjusted, adjusted_se, measure, critical),
        "observed_tau2": observed_tau2,
        "adjusted_tau2": adjusted_tau2,
        "imputed_studies": imputed,
        "iterations": iterations,
        "assumptions": [
            "Each row is one independent study estimate for a common outcome and comparison.",
            "The requested funnel-plot side is selected in advance; it is not inferred from the data.",
            "R0, Q0, and L0 are signed-rank estimators; ties in absolute centered effects are ranked in input order after sorting effects.",
            "FE uses inverse sampling-variance weights; DL and REML re-estimate between-study variance in each trimmed fit and in the augmented fit.",
            "Imputed effects mirror the most extreme trimmed studies around the final trimmed pooled estimate and reuse their standard errors.",
        ],
        "warnings": [
            "Funnel asymmetry does not prove publication bias; heterogeneity, chance, and other mechanisms can also create asymmetry.",
            "The adjusted estimate is a sensitivity analysis and is not guaranteed to be unbiased.",
        ],
    }
