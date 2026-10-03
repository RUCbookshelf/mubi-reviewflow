"""Exploratory publication-bias diagnostics for independent study effects."""

from __future__ import annotations

import math

from scipy.stats import kendalltau, norm

_RATIO_MEASURES = {"RR", "OR", "HR", "RATE_RATIO"}
_NATURAL_MEASURES = {"RD", "MD", "SMD"}
_MEASURES = _RATIO_MEASURES | _NATURAL_MEASURES | {"FISHER_Z"}
_LIMITATION = (
    "Begg's conventional asymptotic p-value can be miscalibrated; asymmetry or a fail-safe N "
    "does not prove publication bias or identify why studies are missing."
)


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


def _validated_rows(rows: list[dict], measure: str) -> tuple[list[float], list[float]]:
    if not isinstance(rows, list) or len(rows) < 2:
        raise ValueError("at least two independent study rows are required")

    study_ids: set[str] = set()
    effects, standard_errors = [], []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("each row must be a dictionary")
        study_id = row.get("study_id")
        if not isinstance(study_id, str) or not study_id.strip():
            raise ValueError("each row needs a nonempty study_id")
        if study_id in study_ids:
            raise ValueError("rows must have a unique study_id per independent study")
        study_ids.add(study_id)
        if "measure" in row and row["measure"] != measure:
            raise ValueError("all row measures must match the requested measure")
        try:
            estimate = _number(row["estimate"], "estimate")
            se = _number(row["se"], "se")
        except KeyError as exc:
            raise ValueError("each row needs estimate and se") from exc
        if se <= 0:
            raise ValueError("standard errors must be positive")
        effect = _analysis_effect(estimate, measure)
        if not math.isfinite(se * se) or se * se <= 0:
            raise ValueError("standard errors must imply a finite positive variance")
        effects.append(effect)
        standard_errors.append(se)
    return effects, standard_errors


def _base_result(method: str, measure: str, n: int) -> dict:
    effect_scale = "log" if measure in _RATIO_MEASURES else (
        "fisher_z" if measure == "FISHER_Z" else "natural"
    )
    return {
        "method": method,
        "measure": measure,
        "n_studies": n,
        "effect_scale": effect_scale,
        "small_study_warning": (
            "Fewer than 10 studies: interpret cautiously because power and calibration may be limited; "
            "10 is a rule-of-thumb warning, not a validity cutoff."
            if n < 10 else None
        ),
        "limitation": _LIMITATION,
    }


def diagnose_publication_bias(
    rows: list[dict],
    measure: str,
    method: str,
    *,
    null_effect: float = 0.0,
    target_effect: float | None = None,
) -> dict:
    """Run Begg–Mazumdar rank correlation or Rosenthal's fail-safe N.

    Ratios are analyzed as log estimates, Fisher correlations are supplied as
    Fisher z, and all standard errors must already be on that analysis scale.
    The caller must select one compatible result per independent study.
    """
    if method == "orwin_fail_safe_n":
        raise ValueError(
            "orwin_fail_safe_n is not implemented: the API cannot distinguish an explicitly "
            "specified unpublished-study mean from the default null_effect"
        )
    if method not in {"rank_correlation", "rosenthal_fail_safe_n"}:
        raise ValueError("method must be rank_correlation or rosenthal_fail_safe_n")
    if not isinstance(measure, str) or measure not in _MEASURES:
        raise ValueError(f"measure must be one of {', '.join(sorted(_MEASURES))}")
    null_effect = _number(null_effect, "null_effect")
    if target_effect is not None:
        _number(target_effect, "target_effect")
        raise ValueError("target_effect is only used by the unsupported orwin_fail_safe_n method")
    if method == "rank_correlation" and null_effect != 0:
        raise ValueError("null_effect is only used by rosenthal_fail_safe_n")

    effects, ses = _validated_rows(rows, measure)
    n = len(effects)
    result = _base_result(method, measure, n)

    if method == "rank_correlation":
        variances = [se * se for se in ses]
        if len(set(variances)) < 2:
            raise ValueError("rank correlation needs variation in sampling variances")
        weights = [1 / variance for variance in variances]
        try:
            total_weight = math.fsum(weights)
            pooled_effect = math.fsum(effect * weight for effect, weight in zip(effects, weights)) / total_weight
        except (OverflowError, ZeroDivisionError) as exc:
            raise ValueError("study precisions are outside the supported numeric range") from exc
        if not math.isfinite(total_weight) or total_weight <= 0 or not math.isfinite(pooled_effect):
            raise ValueError("study precisions are outside the supported numeric range")

        adjusted_variances = [variance - 1 / total_weight for variance in variances]
        if any(not math.isfinite(v) or v <= 0 for v in adjusted_variances):
            raise ValueError("sampling variances must be finite and positive after adjustment")
        standardized_effects = [
            (effect - pooled_effect) / math.sqrt(adjusted_variance)
            for effect, adjusted_variance in zip(effects, adjusted_variances)
        ]
        if not all(math.isfinite(value) for value in standardized_effects):
            raise ValueError("standardized effects are outside the supported numeric range")
        if len(set(standardized_effects)) < 2:
            raise ValueError("rank correlation needs variation in standardized effects")

        test = kendalltau(standardized_effects, variances, variant="b", method="asymptotic")
        tau, p_value = float(test.statistic), float(test.pvalue)
        if not math.isfinite(tau) or not math.isfinite(p_value):
            raise ValueError("rank correlation is undefined when either ranked variable has no variation")
        result.update({
            "method_name": "Begg–Mazumdar rank correlation",
            "statistic_name": "kendall_tau_b",
            "statistic": tau,
            "p_value": p_value,
            "p_value_method": "two-sided asymptotic Kendall tau-b with tie adjustment",
            "assumptions": [
                "Effects share one comparison, outcome, time point, and measure; each row is an independent study.",
                "Standard errors are on the analyzed effect scale and give the sampling variances.",
                "Effects are standardized around their inverse-variance weighted mean using adjusted variances; the test uses an asymptotic null approximation.",
            ],
        })
        return result

    z_scores = [(effect - null_effect) / se for effect, se in zip(effects, ses)]
    try:
        sum_z = math.fsum(z_scores)
    except OverflowError as exc:
        raise ValueError("study Z scores are outside the supported numeric range") from exc
    critical_z = float(norm.isf(.025))
    combined_z = sum_z / math.sqrt(n)
    combined_p = float(2 * norm.sf(abs(combined_z)))
    try:
        formula_value = (sum_z / critical_z) ** 2 - n
    except OverflowError as exc:
        raise ValueError("combined Z scores are outside the supported numeric range") from exc
    if not all(math.isfinite(value) for value in (sum_z, combined_z, combined_p, formula_value)):
        raise ValueError("combined Z scores are outside the supported numeric range")

    result.update({
        "method_name": "Rosenthal fail-safe N",
        "null_effect": null_effect,
        "sum_z": sum_z,
        "combined_z": combined_z,
        "combined_p_value": combined_p,
        "alpha_two_sided": .05,
        "critical_z": critical_z,
        "formula_value": formula_value,
        "fail_safe_n": max(0, math.ceil(formula_value)),
        "assumed_unpublished_z": 0.0,
        "assumptions": [
            "The observed study Z scores are combined with Stouffer's unweighted sum under independence.",
            "Each missing study is assumed to have exactly Z=0; significance uses a two-sided alpha of 0.05.",
            "null_effect is on the analyzed scale (log for ratios, Fisher z for FISHER_Z, natural otherwise); standard errors are on that same scale.",
        ],
    })
    return result
