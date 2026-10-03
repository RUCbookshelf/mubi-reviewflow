"""Rosenberg's weighted fail-safe N for independent study estimates.

Implements the fixed-effects weighted fail-safe number of Rosenberg (2005),
*Evolution* 59(2):464-468: studies are inverse-variance weighted (w = 1/se^2,
equations 3-4), and the fail-safe count N of null-effect missing studies of
mean weight follows from equations (7)-(10). The critical value is two-sided
at ``1 - target_alpha``; the default uses the standard normal quantile (the
convention of metafor's ``fsn(type="Rosenberg")`` and of this package's
Rosenthal fail-safe N), while ``test="t"`` applies the Student-t critical
value with iterated degrees of freedom df = k + N - 1 that the paper
recommends for the multiple-missing-studies reading of N.
"""

from __future__ import annotations

import copy
import math

from scipy.stats import norm, t as student_t

_METHOD = "rosenberg_fail_safe_n"
_MAX_ITERATIONS = 200
_CONVERGENCE_TOLERANCE = 1e-12


def _finite_number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number")
    try:
        number = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if not math.isfinite(number):
        raise ValueError(f"{name} must be a finite number")
    return number


def _validated_rows(rows: list[dict]) -> tuple[list[float], list[float]]:
    if not isinstance(rows, list) or len(rows) < 2:
        raise ValueError("at least two independent study rows are required")

    study_ids: set[str] = set()
    effects, ses = [], []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(f"row at index {index} must be a dictionary")
        study_id = row.get("study_id")
        if study_id is not None:
            if not isinstance(study_id, str) or not study_id.strip():
                raise ValueError(f"row at index {index} needs a nonempty study_id")
            if study_id in study_ids:
                raise ValueError("rows must have a unique study_id per independent study")
            study_ids.add(study_id)
        try:
            effect = _finite_number(row["effect"], "effect")
            se = _finite_number(row["se"], "se")
        except KeyError as exc:
            raise ValueError("each row needs effect and se") from exc
        if se <= 0:
            raise ValueError("standard errors must be positive")
        variance = se * se
        if not math.isfinite(variance) or variance <= 0:
            raise ValueError("standard errors must imply a finite positive variance")
        weight = 1 / variance
        if not math.isfinite(weight) or not math.isfinite(weight * abs(effect)):
            raise ValueError("study weights are outside the supported numeric range")
        effects.append(effect)
        ses.append(se)
    return effects, ses


def _fail_safe_formula(sum_weighted_effects: float, sum_weights: float, k: int, critical: float) -> float:
    """Return equation (10) as N = ((sum w E / critical)^2 - sum w) * k / sum w."""
    ratio = sum_weighted_effects / critical
    try:
        squared = ratio * ratio
    except OverflowError as exc:
        raise ValueError("fail-safe N is outside the supported numeric range") from exc
    if math.isfinite(squared):
        try:
            return squared * (k / sum_weights) - k
        except OverflowError as exc:
            raise ValueError("fail-safe N is outside the supported numeric range") from exc
    # (sum w E)^2 overflows the float range: evaluate equation (10) in log space.
    log10_n = 2 * math.log10(abs(ratio)) + math.log10(k) - math.log10(sum_weights)
    if log10_n > 308:
        raise ValueError("fail-safe N is outside the supported numeric range")
    return math.pow(10.0, log10_n) - k


def _t_critical_converged(
    sum_weighted_effects: float, sum_weights: float, k: int, alpha: float
) -> tuple[float, float]:
    """Solve the Student-t version iteratively as suggested after equation (10).

    Degrees of freedom start at the observed df = k - 1 and are updated to
    df = k + N - 1 until the critical value stops moving.
    """
    df = float(k - 1)
    critical = float(student_t.isf(alpha / 2, df))
    formula_value = _fail_safe_formula(sum_weighted_effects, sum_weights, k, critical)
    for _ in range(_MAX_ITERATIONS):
        if formula_value <= 0:
            return critical, df
        df = k + formula_value - 1
        updated = float(student_t.isf(alpha / 2, df))
        if not math.isfinite(updated):
            raise ValueError("fail-safe N is outside the supported numeric range")
        converged = abs(updated - critical) <= _CONVERGENCE_TOLERANCE * critical
        critical = updated
        formula_value = _fail_safe_formula(sum_weighted_effects, sum_weights, k, critical)
        if converged:
            break
    return critical, df


def rosenberg_fail_safe(
    rows: list[dict],
    *,
    level: float = 0.95,
    target_alpha: float = 0.05,
    test: str = "z",
    source_provenance: dict | None = None,
) -> dict:
    """Return Rosenberg's weighted fail-safe N for one effect per study.

    Each row holds one independent study as ``effect`` (already on the analysis
    scale: log for ratios, Fisher z for correlations, natural otherwise) and
    ``se`` (standard error on that same scale). ``level`` and ``target_alpha``
    describe the same two-sided significance target and must satisfy
    ``target_alpha == 1 - level``. ``test`` selects the critical value: ``"z"``
    (default) is the two-sided standard normal quantile used by metafor and by
    this package's Rosenthal fail-safe N; ``"t"`` is Rosenberg's conservative
    Student-t version with df = k + N - 1 solved iteratively. Missing studies
    are assumed to have mean effect exactly zero and the mean observed weight.
    """
    level = _finite_number(level, "level")
    target_alpha = _finite_number(target_alpha, "target_alpha")
    if not 0 < level < 1:
        raise ValueError("level must be between 0 and 1")
    if not 0 < target_alpha < 1:
        raise ValueError("target_alpha must be between 0 and 1")
    if abs((1 - level) - target_alpha) > 1e-9:
        raise ValueError("level and target_alpha must describe the same target: target_alpha == 1 - level")
    if test not in {"z", "t"}:
        raise ValueError("test must be 'z' (normal critical value) or 't' (Student-t critical value)")
    if source_provenance is not None and not isinstance(source_provenance, dict):
        raise ValueError("source_provenance must be an object")

    effects, ses = _validated_rows(rows)
    k = len(effects)

    variances = [se * se for se in ses]
    weights = [1 / variance for variance in variances]
    try:
        sum_weights = math.fsum(weights)
        sum_weighted_effects = math.fsum(weight * effect for weight, effect in zip(weights, effects))
    except OverflowError as exc:
        raise ValueError("study weights are outside the supported numeric range") from exc
    if not math.isfinite(sum_weights) or sum_weights <= 0 or not math.isfinite(sum_weighted_effects):
        raise ValueError("study weights are outside the supported numeric range")

    pooled_effect = sum_weighted_effects / sum_weights
    pooled_se = math.sqrt(1 / sum_weights)
    weighted_z = sum_weighted_effects / math.sqrt(sum_weights)

    if test == "z":
        critical = float(norm.isf(target_alpha / 2))
        critical_df = None
        observed_p = float(2 * norm.sf(abs(weighted_z)))
        formula_value = _fail_safe_formula(sum_weighted_effects, sum_weights, k, critical)
        critical_distribution = "two-sided standard normal"
    else:
        critical, critical_df = _t_critical_converged(sum_weighted_effects, sum_weights, k, target_alpha)
        observed_p = float(2 * student_t.sf(abs(weighted_z), k - 1))
        formula_value = _fail_safe_formula(sum_weighted_effects, sum_weights, k, critical)
        critical_distribution = f"two-sided Student t with df = k + N - 1 (converged df {critical_df!r})"

    fail_safe_n = max(0, math.ceil(formula_value))

    small_study_warning = (
        "Fewer than 10 studies: interpret cautiously because power and calibration may be limited; "
        "10 is a rule-of-thumb warning, not a validity cutoff."
        if k < 10 else None
    )
    warnings = [
        "The count depends on all effects and standard errors sharing one consistent analysis scale "
        "(log for ratios, Fisher z for correlations) and on the zero-cell or continuity corrections "
        "applied upstream; changing the scale or corrections changes the fail-safe number.",
        "A fail-safe number is a sensitivity indicator: it does not prove publication bias, does not "
        "estimate how many studies are actually unpublished, and does not explain why studies are "
        "missing (Rosenberg 2005, p. 467).",
    ]
    if small_study_warning is not None:
        warnings.insert(0, small_study_warning)

    assumptions = [
        "Each row is one independent study weighted by the inverse of its sampling variance, w = 1/se^2 "
        "(Rosenberg 2005, equations 3-4).",
        "Missing studies are assumed to have mean effect exactly zero and the mean observed weight "
        "sum(w)/k (Rosenberg 2005, after equation 10); the count targets a two-sided alpha.",
        "The fixed-effects critical value is used: test='z' takes the two-sided standard normal quantile "
        "(metafor convention; identical to this package's Rosenthal fail-safe N), test='t' takes "
        "Rosenberg's Student-t quantile with df = k + N - 1 solved iteratively.",
    ]
    limitations = [
        "This sensitivity count does not prove publication bias, does not estimate how many unpublished "
        "studies exist, and is not a method for accounting for publication bias (Rosenberg 2005, p. 467).",
        "Only the fixed-effects version is implemented; the paper's random-effects variant (equations "
        "11-13) re-weights by 1/(se^2 + tau^2_pooled) iteratively and often collapses to the fixed-effects "
        "count.",
        "The classic robustness threshold N > 5k + 10 (Rosenthal 1991, cited in Rosenberg 2005, p. 466) "
        "is an arbitrary rule of thumb, not a validity criterion.",
    ]

    return {
        "method": _METHOD,
        "method_name": "Rosenberg weighted fail-safe N",
        "source_equations": (
            "Rosenberg (2005) Evolution 59(2):464-468, equations (3)-(4), (7)-(10); "
            "fixed-effects weights w = 1/se^2"
        ),
        "test": test,
        "level": level,
        "alpha": target_alpha,
        "k": k,
        "n_studies": k,
        "weighted_mean_effect": pooled_effect,
        "se_weighted_mean": pooled_se,
        "weighted_z": weighted_z,
        "statistic_name": "weighted_mean_z_fixed_effects",
        "observed_p_value": observed_p,
        "observed_p_method": (
            "two-sided Student t with k - 1 df" if test == "t" else "two-sided standard normal"
        ),
        "critical_value": critical,
        "critical_distribution": critical_distribution,
        "critical_df": critical_df,
        "sum_weights": sum_weights,
        "mean_weight": sum_weights / k,
        "harmonic_mean_variance": k / sum_weights,
        "assumed_missing_effect": 0.0,
        "assumed_missing_weight": "mean observed weight sum(w)/k",
        "formula_value": formula_value,
        "fail_safe_n": fail_safe_n,
        "n_fs": fail_safe_n,
        "input_data": copy.deepcopy(rows),
        "source_provenance": copy.deepcopy(source_provenance) if source_provenance is not None else None,
        "small_study_warning": small_study_warning,
        "warnings": warnings,
        "assumptions": assumptions,
        "limitations": limitations,
    }
