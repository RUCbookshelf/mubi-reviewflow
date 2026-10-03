"""Fixed-effect Duval–Tweedie trim-and-fill for independent study effects."""

from __future__ import annotations

import math
from statistics import NormalDist

_RATIO_MEASURES = {"RR", "OR", "HR", "RATE_RATIO"}
_NATURAL_MEASURES = {"RD", "MD", "SMD"}
_MEASURES = _RATIO_MEASURES | _NATURAL_MEASURES | {"FISHER_Z"}
_MAX_ITER = 100


def _number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number")
    try:
        result = float(value)
    except OverflowError as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name} must be a finite number")
    return result


def _analysis_effect(estimate: float, measure: str) -> float:
    if measure in _RATIO_MEASURES:
        if estimate <= 0:
            raise ValueError("ratio estimates must be positive")
        return math.log(estimate)
    return estimate


def _validated_rows(rows: list[dict], measure: str) -> tuple[list[dict], list[float], list[float]]:
    if not isinstance(rows, list) or len(rows) < 2:
        raise ValueError("at least two independent study rows are required")
    if not isinstance(measure, str) or measure not in _MEASURES:
        raise ValueError(f"measure must be one of {', '.join(sorted(_MEASURES))}")

    ids: set[str] = set()
    effects, ses = [], []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("each row must be a dictionary")
        study_id = row.get("study_id")
        if not isinstance(study_id, str) or not study_id.strip():
            raise ValueError("each row needs a nonempty study_id")
        if study_id in ids:
            raise ValueError("rows must have a unique study_id per independent study")
        ids.add(study_id)
        if row.get("measure") != measure:
            raise ValueError("all row measures must match the requested measure")
        try:
            estimate = _number(row["estimate"], "estimate")
            se = _number(row["se"], "se")
        except KeyError as exc:
            raise ValueError("each row needs estimate and se") from exc
        if se <= 0:
            raise ValueError("standard errors must be positive")
        effect = _analysis_effect(estimate, measure)
        variance = se * se
        if not math.isfinite(variance) or variance <= 0:
            raise ValueError("standard errors must imply a finite positive variance")
        if not math.isfinite(1 / variance):
            raise ValueError("study precisions are outside the supported numeric range")
        effects.append(effect)
        ses.append(se)
    return rows, effects, ses


def _pool(effects: list[float], ses: list[float]) -> tuple[float, float]:
    try:
        weights = [1 / (se * se) for se in ses]
        total_weight = math.fsum(weights)
        pooled = math.fsum(effect * weight for effect, weight in zip(effects, weights)) / total_weight
        pooled_se = math.sqrt(1 / total_weight)
    except (OverflowError, ZeroDivisionError) as exc:
        raise ValueError("study precisions are outside the supported numeric range") from exc
    if not all(math.isfinite(value) for value in (total_weight, pooled, pooled_se)) or total_weight <= 0:
        raise ValueError("study precisions are outside the supported numeric range")
    return pooled, pooled_se


def _l0_count(centered: list[float]) -> int:
    # metafor ranks absolute residual ties in sorted-effect order (ties.method="first").
    ranks = [0] * len(centered)
    for rank, index in enumerate(sorted(range(len(centered)), key=lambda i: abs(centered[i])), 1):
        ranks[index] = rank
    positive_rank_sum = sum(rank for rank, residual in zip(ranks, centered) if residual > 0)
    k = len(centered)
    estimate = (4 * positive_rank_sum - k * (k + 1)) / (2 * k - 1)
    return max(0, round(estimate))


def _reported_effect(effect: float, measure: str) -> float:
    if measure not in _RATIO_MEASURES:
        return effect
    try:
        estimate = math.exp(effect)
    except OverflowError as exc:
        raise ValueError("log estimate is outside the representable ratio range") from exc
    if not math.isfinite(estimate) or estimate <= 0:
        raise ValueError("log estimate is outside the representable ratio range")
    return estimate


def _pooled_result(effect: float, se: float, measure: str, critical: float) -> dict:
    try:
        low, high = effect - critical * se, effect + critical * se
    except OverflowError as exc:
        raise ValueError("confidence interval is outside the supported numeric range") from exc
    if not all(math.isfinite(value) for value in (low, high)):
        raise ValueError("confidence interval is outside the supported numeric range")
    return {
        "pooled": _reported_effect(effect, measure),
        "se": se,
        "ci_low": _reported_effect(low, measure),
        "ci_high": _reported_effect(high, measure),
    }


def trim_and_fill(
    rows: list[dict],
    measure: str,
    side: str,
    *,
    confidence_level: float = 0.95,
) -> dict:
    """Estimate and impute missing studies using the L0 trim-and-fill estimator.

    Ratio estimates are analyzed on the log scale; their input SEs must already
    be log-scale SEs. FISHER_Z estimates and SEs remain on the Fisher z scale.
    Returned ratio pooled estimates, CIs, and imputed estimates are exponentiated;
    their reported SEs remain on the analysis scale, identified by ``se_scale``.
    """
    if not isinstance(side, str) or side not in {"left", "right"}:
        raise ValueError("side must be 'left' or 'right'")
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
    previous_missing = -1
    for iterations in range(1, _MAX_ITER + 1):
        trimmed_center, _ = _pool(sorted_effects[:n - missing], sorted_ses[:n - missing])
        centered = [effect - trimmed_center for effect in sorted_effects]
        if not all(math.isfinite(value) for value in centered):
            raise ValueError("centered study effects are outside the supported numeric range")
        updated_missing = _l0_count(centered)
        if updated_missing == previous_missing:
            missing = updated_missing
            break
        previous_missing = updated_missing
        missing = updated_missing
    else:
        raise RuntimeError(f"trim-and-fill did not converge within {_MAX_ITER} iterations")

    observed, observed_se = _pool(oriented, ses)
    imputed = []
    imputed_effects, imputed_ses = [], []
    used_ids = {row["study_id"] for row in source_rows}
    for index in range(n - missing, n):
        source = source_rows[order[index]]
        mirrored = 2 * trimmed_center - sorted_effects[index]
        if not math.isfinite(mirrored):
            raise ValueError("imputed study effects are outside the supported numeric range")
        imputed_effects.append(mirrored)
        imputed_ses.append(ses[order[index]])
        if side == "right":
            mirrored = -mirrored
        filled_estimate = _reported_effect(mirrored, measure)
        filled_id = f"filled_{len(imputed) + 1}"
        while filled_id in used_ids:
            filled_id += "_"
        used_ids.add(filled_id)
        imputed.append({
            "study_id": filled_id,
            "source_study_id": source["study_id"],
            "measure": measure,
            "estimate": filled_estimate,
            "se": source["se"],
        })

    adjusted, adjusted_se = _pool(sorted_effects + imputed_effects, sorted_ses + imputed_ses)
    if side == "right":
        observed, adjusted = -observed, -adjusted

    analysis_scale = "log" if measure in _RATIO_MEASURES else (
        "fisher_z" if measure == "FISHER_Z" else "natural"
    )
    return {
        "method": "Duval–Tweedie trim-and-fill",
        "measure": measure,
        "side": side,
        "estimator": "L0",
        "pooling_method": "FE",
        "observed_tau2": 0.0,
        "adjusted_tau2": 0.0,
        "n_studies": n,
        "n_missing": missing,
        "confidence_level": confidence_level,
        "estimate_scale": "ratio" if measure in _RATIO_MEASURES else analysis_scale,
        "analysis_scale": analysis_scale,
        "se_scale": analysis_scale,
        "observed": _pooled_result(observed, observed_se, measure, critical),
        "adjusted": _pooled_result(adjusted, adjusted_se, measure, critical),
        "imputed_studies": imputed,
        "iterations": iterations,
        "assumptions": [
            "Each row is one independent study estimate for a common outcome and comparison.",
            "A fixed-effect inverse-variance model assumes all studies share one true effect; SEs are on the analysis scale.",
            "Missingness is one-sided on the requested funnel-plot side; L0 estimates the number from signed ranks of centered effects.",
            "Imputed effects mirror the most extreme opposite-side studies around the final trimmed pool and reuse their SEs.",
        ],
        "warnings": [
            "Funnel asymmetry does not prove publication bias; heterogeneity, chance, and other mechanisms can also create asymmetry.",
            "The adjusted estimate is a sensitivity analysis and is not guaranteed to be unbiased.",
        ],
    }
