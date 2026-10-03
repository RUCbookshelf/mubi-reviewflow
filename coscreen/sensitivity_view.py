"""One-call side-by-side sensitivity matrix for a synthesis-ready row set.

This module composes existing, already-validated calculations into a single
table for the front end. It is a pure calculation: no files are read or
written.

Input ``rows`` are one-per-study effect rows in the shape produced by
``coscreen.review_analysis.selected_effects`` -- each row carries at least
``study_id``, ``measure``, ``estimate`` and ``se``, where ``estimate`` is on
the natural scale for ratio measures and ``se`` is on the analysis scale
(log scale for ratios), exactly as stored by the house effect entry. The rows
must share one measure; mixed pools are rejected with a structured error.

The returned matrix crosses three synthesis models (fixed, DerSimonian-Laird,
REML) with three interval methods (Wald, modified HKSJ, Higgins prediction
interval). All pooling and interval formulas follow
``coscreen.review_analysis._synthesize_rows`` exactly (verified by test
against that function); the only intentional difference is that ``level`` is
an explicit parameter instead of the house's hardcoded 95% (whose Wald normal
quantile is rounded to 1.96 for fixed/DL models).

Alongside the matrix it returns an ML profile-likelihood interval for the
pooled effect, four tau-squared intervals (REML profile likelihood,
Viechtbauer Q-profile, Higgins-Thompson transform, noncentral inversion), an I-squared
point estimate with the Higgins-Thompson interval, and -- only when
``bootstrap_n`` is explicitly given, with a mandatory ``seed`` -- parametric
bootstrap intervals per model from ``coscreen.bootstrap_interval``.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from numbers import Integral, Real

from coscreen.heterogeneity_interval import METHOD as _HT_METHOD
from coscreen.heterogeneity_interval import i2_h_interval
from coscreen.measure_registry import MEASURE_REGISTRY
from coscreen.measure_registry import SYNTHESIS_READY_CODES, check_pool_compatibility
from coscreen.meta_regression_model import profile_reml_tau2
from coscreen.pooled_effect_profile import profile_likelihood_pooled_interval
from coscreen.review_analysis import RATIOS, SINGLE_GROUP
from coscreen.tau_profile_interval import profile_likelihood_tau2_interval
from coscreen.tau_q_profile_interval import q_profile_tau2_interval

__all__ = ["SensitivityViewError", "MEASURE_INFO", "MODELS", "INTERVAL_METHODS", "sensitivity_view"]

MODELS = ("fixed", "dl", "reml")
INTERVAL_METHODS = ("wald", "hksj", "prediction")

MODEL_LABELS = {
    "fixed": "fixed-effect inverse variance",
    "dl": "DerSimonian-Laird random effects",
    "reml": "restricted maximum likelihood random effects",
}
INTERVAL_LABELS = {
    "wald": "Wald normal interval",
    "hksj": "modified Hartung-Knapp-Sidik-Jonkman t interval",
    "prediction": "Higgins-type prediction interval",
}

#: Per-measure metadata for the codes this view can compute on. Family and
#: analysis scale are read from the batch 11A ``coscreen.measure_registry``
#: -- the repo-wide single source of truth for measure codes -- and the
#: accepted codes are exactly its ``SYNTHESIS_READY_CODES`` (what
#: ``review_analysis`` pooling accepts), so the mixed-pool check below
#: delegates to the registry's ``check_pool_compatibility`` instead of an
#: inlined rule. Only the display null lives here, a presentation field the
#: registry deliberately does not carry. A code flipped away from
#: ``synthesis_ready`` (or a new ready code without a null) fails at import,
#: which keeps this table and the registry from drifting.
_NULL_DISPLAY: dict[str, float | None] = {
    "RR": 1.0,
    "OR": 1.0,
    "HR": 1.0,
    "RATE_RATIO": 1.0,
    "LOG_RATE": 1.0,
    "LOG_ROM": 1.0,
    "PAIRED_OR": 1.0,
    "PAIRED_RD": 0.0,
    "PHI": 0.0,
    "R_EQUIV_APPROX": 0.0,
    "RD": 0.0,
    "MD": 0.0,
    "SMD": 0.0,
    "SMCR": 0.0,
    "SMCC": 0.0,
    "FISHER_Z": 0.0,
    "LOGIT_PROP": 0.5,
    "MEAN": None,
}
MEASURE_INFO: dict[str, dict[str, float | str | None]] = {
    code: {
        "family": MEASURE_REGISTRY[code]["family"],
        "analysis_scale": MEASURE_REGISTRY[code]["scale"],
        "null_display": _NULL_DISPLAY[code],
    }
    for code in SYNTHESIS_READY_CODES
}

_WALD_SOURCE = "coscreen.sensitivity_view (formulas of coscreen.review_analysis._synthesize_rows)"
_TAU2_REML_PROFILE_SOURCE = "coscreen.tau_profile_interval.profile_likelihood_tau2_interval"
_TAU2_Q_PROFILE_SOURCE = "coscreen.tau_q_profile_interval.q_profile_tau2_interval"
_TAU2_HT_SOURCE = "coscreen.heterogeneity_interval.i2_h_interval"
_BOOTSTRAP_SOURCE = "coscreen.bootstrap_interval.bootstrap_pooled_interval"

_FIXED_HKSJ_CAUTION = (
    "The modified HKSJ interval is designed for random-effects models; "
    "coscreen.review_analysis.synthesize rejects fixed + HKSJ for primary "
    "synthesis. Shown here for side-by-side sensitivity comparison only."
)
_FIXED_PREDICTION_CAUTION = (
    "The fixed-effect model sets tau-squared to 0, so this prediction interval "
    "covers only the sampling variation of a new study under homogeneity; "
    "random-effect prediction intervals are the reportable ones."
)


class SensitivityViewError(ValueError):
    """Structured rejection; ``reason`` is a stable code for the UI, and the
    message is human-readable. Subclasses ``ValueError`` so existing route
    error handling (``_bad``) keeps working unchanged."""

    def __init__(self, reason: str, message: str, **details):
        super().__init__(message)
        self.reason = reason
        self.details = details


def _number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise SensitivityViewError(f"{name}_invalid", f"{name} must be a finite number")
    try:
        number = float(value)
    except (OverflowError, TypeError, ValueError) as exc:
        raise SensitivityViewError(f"{name}_invalid", f"{name} must be a finite number") from exc
    if not math.isfinite(number):
        raise SensitivityViewError(f"{name}_invalid", f"{name} must be a finite number")
    return number


def _display_transform(measure: str):
    """Natural-scale display transform, identical to ``review_analysis``."""
    from scipy.special import expit

    if measure in RATIOS | {"LOG_RATE", "LOG_ROM", "PAIRED_OR"}:
        return math.exp
    if measure == "LOGIT_PROP":
        return lambda value: float(expit(value))
    if measure == "FISHER_Z":
        return math.tanh
    return float


def _display_or_none(transform, value: float) -> float | None:
    """Analysis→display transform that degrades to None instead of raising.

    Huge-SE log-scale limits can overflow ``math.exp``; the cell/bootstrap
    entry then reports the analysis-scale numbers with a reason instead of
    failing the whole view.
    """
    try:
        out = transform(value)
    except (ValueError, RuntimeError, OverflowError):
        return None
    return out if math.isfinite(out) else None


def _model_fit(y: list[float], v: list[float], model: str) -> dict:
    """One synthesis fit; formulas identical to ``review_analysis._synthesize_rows``."""
    k = len(y)
    df = k - 1
    w = [1.0 / value for value in v]
    total_w = math.fsum(w)
    fixed_pooled = math.fsum(wi * yi for wi, yi in zip(w, y)) / total_w
    q = math.fsum(wi * (yi - fixed_pooled) ** 2 for wi, yi in zip(w, y))
    c = total_w - math.fsum(wi * wi for wi in w) / total_w
    tau2 = 0.0
    if model == "dl":
        tau2 = max(0.0, (q - df) / c) if c > 0 else 0.0
    elif model == "reml":
        tau2, _ = profile_reml_tau2(y, v)
    weights = w if model == "fixed" else [1.0 / (value + tau2) for value in v]
    total = math.fsum(weights)
    pooled = math.fsum(wi * yi for wi, yi in zip(weights, y)) / total
    return {
        "tau2": tau2,
        "q": q,
        "df": df,
        "c": c,
        "weights": weights,
        "pooled": pooled,
        "pooled_se": math.sqrt(1.0 / total),
    }


def _interval_half_width(method: str, fit: dict, y: list[float], level: float) -> float | None:
    """Half width of one interval method for a fitted model (analysis scale).

    Returns ``None`` when the method is not defined for this k (prediction
    intervals need k >= 3 so the t quantile has k - 2 degrees of freedom).
    """
    from scipy.stats import norm, t

    alpha_half = (1.0 - level) / 2.0
    if method == "wald":
        return float(norm.ppf(1.0 - alpha_half)) * fit["pooled_se"]
    if method == "hksj":
        df = fit["df"]
        s2 = math.fsum(wi * (yi - fit["pooled"]) ** 2 for wi, yi in zip(fit["weights"], y)) / df
        scale = math.sqrt(max(1.0, s2))
        return float(t.ppf(1.0 - alpha_half, df)) * fit["pooled_se"] * scale
    # prediction
    k = len(y)
    if k < 3:
        return None
    spread = math.sqrt(fit["tau2"] + fit["pooled_se"] ** 2)
    return float(t.ppf(1.0 - alpha_half, k - 2)) * spread


def sensitivity_view(
    rows: Sequence[dict],
    *,
    level: float = 0.95,
    bootstrap_n: int | None = None,
    bootstrap_interval_type: str = "percentile",
    seed: int | None = None,
) -> dict:
    """Build the model x interval-method sensitivity matrix for ``rows``.

    ``rows``: one dict per study with at least ``study_id`` (non-empty string),
    ``measure`` (a code that is registered and synthesis-ready in
    ``coscreen.measure_registry``, identical across rows),
    ``estimate`` and ``se`` (finite; ``se`` positive; ratio estimates
    positive). Extra keys (``source_key``, ``result_id``, ...) are ignored, so
    the rows of ``review_analysis.selected_effects`` can be passed unchanged.

    ``level`` is the common confidence level of every interval (default and
    house level 0.95). ``bootstrap_n`` (>= 1) switches on parametric
    bootstrap intervals for all three models and requires ``seed`` (>= 0);
    passing either one without the other is rejected, so no bootstrap and no
    randomness ever happens implicitly. The REML profile tau-squared interval
    is implemented at 95% only; at other levels its cell is reported as not
    applicable instead of silently degrading.

    Invalid inputs raise ``SensitivityViewError`` (a ``ValueError``) with a
    stable ``reason`` code: unknown measure, mixed-measure pool, k < 2,
    non-positive SE, duplicate study ids, bootstrap/seed mismatches.
    """
    if isinstance(level, bool) or not isinstance(level, Real):
        raise SensitivityViewError("level_invalid", "level must be a number strictly between 0 and 1")
    level = float(level)
    if not math.isfinite(level) or not 0 < level < 1:
        raise SensitivityViewError("level_invalid", "level must be a number strictly between 0 and 1")

    if bootstrap_interval_type not in {"percentile", "basic"}:
        raise SensitivityViewError("bootstrap_interval_type_invalid", "Choose percentile or basic bootstrap intervals")
    if bootstrap_n is not None:
        if isinstance(bootstrap_n, bool) or not isinstance(bootstrap_n, Integral) or bootstrap_n < 1:
            raise SensitivityViewError("bootstrap_n_invalid", "bootstrap_n must be an integer >= 1")
        if seed is None:
            raise SensitivityViewError(
                "seed_required",
                "seed is required when bootstrap_n is given; bootstrap intervals are never run implicitly",
            )
        if isinstance(seed, bool) or not isinstance(seed, Integral) or seed < 0:
            raise SensitivityViewError("seed_invalid", "seed must be a non-negative integer")
    elif seed is not None:
        raise SensitivityViewError(
            "seed_only_with_bootstrap",
            "seed is only meaningful together with an explicit bootstrap_n",
        )

    if not isinstance(rows, (list, tuple)) or not rows:
        raise SensitivityViewError("rows_invalid", "rows must be a non-empty list of per-study effect dicts")

    cleaned: list[tuple[str, str, float, float]] = []
    seen_ids: set[str] = set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or not {"study_id", "measure", "estimate", "se"} <= set(row):
            raise SensitivityViewError(
                "row_invalid",
                f"row {index} must be a dict with study_id, measure, estimate and se",
                index=index,
            )
        study_id = row["study_id"]
        if not isinstance(study_id, str) or not study_id.strip():
            raise SensitivityViewError("study_id_invalid", f"row {index} needs a non-empty study_id", index=index)
        if study_id in seen_ids:
            raise SensitivityViewError(
                "duplicate_study_id",
                f"study {study_id} appears more than once; one selected effect per study is required",
                study_id=study_id,
            )
        seen_ids.add(study_id)
        measure = row["measure"]
        if not isinstance(measure, str) or measure not in MEASURE_INFO:
            raise SensitivityViewError(
                "measure_unknown",
                f"measure {measure!r} is not a synthesis-ready registered measure "
                f"(coscreen.measure_registry); accepted measures: {', '.join(MEASURE_INFO)}",
                measure=measure,
            )
        estimate = _number(row["estimate"], "estimate")
        se = _number(row["se"], "se")
        if se <= 0:
            raise SensitivityViewError("se_nonpositive", f"study {study_id}: se must be positive", study_id=study_id)
        if measure in RATIOS and estimate <= 0:
            raise SensitivityViewError(
                "estimate_nonpositive", f"study {study_id}: ratio estimates must be positive", study_id=study_id
            )
        cleaned.append((study_id, measure, estimate, se))

    k = len(cleaned)
    if k < 2:
        raise SensitivityViewError("k_too_small", "at least two independent studies are required")
    measures = sorted({measure for _, measure, _, _ in cleaned})
    # Mixed-pool decision delegated to the batch 11A registry (single source of
    # truth: same family or explicitly declared poolable_families). Among the
    # synthesis-ready codes every family is distinct and poolable_families is
    # empty, so this is exactly the house "one identical measure" rule enforced
    # by review_analysis.selected_effects.
    compatibility = check_pool_compatibility(measures)
    if not compatibility["compatible"]:
        families = sorted({MEASURE_REGISTRY[measure]["family"] for measure in measures})
        raise SensitivityViewError(
            "measure_mixed_pool",
            f"mixed-effect pool is not poolable: rows carry measures {', '.join(measures)} "
            f"(families {', '.join(families)}); one identical measure is required "
            "(coscreen.measure_registry.check_pool_compatibility)",
            measures=measures,
            families=families,
            reasons=compatibility["reasons"],
        )
    measure = cleaned[0][1]
    info = MEASURE_INFO[measure]

    y = [math.log(estimate) if measure in RATIOS else estimate for _, _, estimate, _ in cleaned]
    v = [se * se for _, _, _, se in cleaned]
    transform = _display_transform(measure)
    has_null = info["null_display"] is not None

    fits = {model: _model_fit(y, v, model) for model in MODELS}
    try:
        pooled_profile = profile_likelihood_pooled_interval(y, v, level=level)
        profile_low, profile_high = pooled_profile["ci"]
        pooled_profile_cell = {
            "key": "random_ml_profile", "model": "random_ml",
            "model_label": "maximum-likelihood random effects",
            "interval_method": "profile_likelihood",
            "interval_label": "pooled-effect ML profile likelihood interval",
            "estimate": pooled_profile["estimate"], "ci": pooled_profile["ci"],
            "estimate_display": transform(pooled_profile["estimate"]),
            "ci_display": [transform(profile_low), transform(profile_high)],
            "width": profile_high - profile_low,
            "side_of_null": ("negative" if profile_high < 0 else "positive" if profile_low > 0 else "crosses")
                            if has_null else None,
            "tau2_ml": pooled_profile["tau2_ml"], "level": level,
            "method": pooled_profile["method"],
            "method_source": "coscreen.pooled_effect_profile.profile_likelihood_pooled_interval",
            "citation": pooled_profile["citation"], "caution": pooled_profile["warning"],
            "applicable": True,
        }
    except (ValueError, RuntimeError, OverflowError) as exc:
        pooled_profile_cell = {
            "key": "random_ml_profile", "model": "random_ml",
            "interval_method": "profile_likelihood", "applicable": False,
            "reason": str(exc), "estimate": None, "ci": None,
            "estimate_display": None, "ci_display": None, "width": None,
            "method_source": "coscreen.pooled_effect_profile.profile_likelihood_pooled_interval",
        }

    cells: list[dict] = []
    for model in MODELS:
        fit = fits[model]
        for method in INTERVAL_METHODS:
            half = _interval_half_width(method, fit, y, level)
            key = f"{model}_{method}"
            if half is None:
                cells.append({
                    "key": key, "model": model, "interval_method": method,
                    "estimate": fit["pooled"], "ci": None,
                    "estimate_display": transform(fit["pooled"]), "ci_display": None,
                    "width": None, "side_of_null": None,
                    "method": INTERVAL_LABELS[method], "method_source": _WALD_SOURCE,
                    "applicable": False,
                    "reason": "prediction intervals need at least three studies (t quantile with k - 2 df)",
                })
                continue
            low, high = fit["pooled"] - half, fit["pooled"] + half
            if has_null:
                side = "negative" if high < 0 else "positive" if low > 0 else "crosses"
            else:
                side = None
            estimate_display = _display_or_none(transform, fit["pooled"])
            ci_display = [_display_or_none(transform, low), _display_or_none(transform, high)]
            display_overflow = (estimate_display is None
                                or any(value is None for value in ci_display))
            cell = {
                "key": key, "model": model, "interval_method": method,
                "model_label": MODEL_LABELS[model], "interval_label": INTERVAL_LABELS[method],
                "estimate": fit["pooled"], "ci": [low, high],
                "estimate_display": estimate_display,
                "ci_display": ci_display,
                "width": high - low, "side_of_null": side,
                "level": level,
                "method": INTERVAL_LABELS[method], "method_source": _WALD_SOURCE,
                "applicable": not display_overflow,
            }
            if display_overflow:
                cell["reason"] = ("pooled estimate or interval limits are outside the "
                                  "representable display range for this measure")
            if model == "fixed" and method == "hksj":
                cell["caution"] = _FIXED_HKSJ_CAUTION
            elif model == "fixed" and method == "prediction":
                cell["caution"] = _FIXED_PREDICTION_CAUTION
            cells.append(cell)

    q = fits["fixed"]["q"]
    df = fits["fixed"]["df"]
    c_constant = fits["fixed"]["c"]

    tau2_intervals: list[dict] = []
    # REML profile likelihood (k >= 3; implemented at 95% only).
    if k >= 3 and math.isclose(level, 0.95, rel_tol=0.0, abs_tol=1e-12):
        profile = profile_likelihood_tau2_interval(y, v)
        tau2_intervals.append({
            "key": "tau2_reml_profile",
            "estimate": profile["estimate"], "ci": [profile["lower"], profile["upper"]],
            "level": profile["confidence_level"], "converged": profile["converged"],
            "upper_unbounded": profile["upper_unbounded"],
            "method": "REML profile likelihood interval for tau-squared",
            "method_source": _TAU2_REML_PROFILE_SOURCE, "applicable": True,
        })
    else:
        tau2_intervals.append({
            "key": "tau2_reml_profile", "estimate": fits["reml"]["tau2"], "ci": None,
            "method": "REML profile likelihood interval for tau-squared",
            "method_source": _TAU2_REML_PROFILE_SOURCE, "applicable": False,
            "reason": "needs at least three studies" if k < 3 else
                      "profile REML tau-squared interval is implemented at level 0.95 only",
        })
    # Same fixed-IV Q and C; this approximation is distinct from Q-profile.
    from coscreen.tau_q_profile_interval import tau_q_profile_interval
    noncentral = tau_q_profile_interval(q, k, level=level, c_constant=c_constant)
    tau2_intervals.append({
        "key": "tau2_noncentral", "estimate": noncentral["tau2"],
        "ci": list(noncentral["tau2_ci"]), "level": level,
        "converged": noncentral["converged"], "upper_unbounded": noncentral["upper_unbounded"],
        "method": "Noncentral chi-square inversion of fixed-IV Q (approximate; DL point estimate)",
        "method_source": "coscreen.tau_q_profile_interval.tau_q_profile_interval",
        "citation": "Higgins & Thompson (2002), Statistics in Medicine 21:1539–1558, DOI 10.1002/sim.1186. Verify the noncentral inversion variant against the original method.",
        "applicable": True, "warnings": list(noncentral["warnings"]),
        "input_data": noncentral["input_data"],
    })
    # Viechtbauer (2007) Q-profile (k >= 2).
    q_profile = q_profile_tau2_interval(y, v, level=level)
    tau2_intervals.append({
        "key": "tau2_q_profile",
        "estimate": q_profile["estimate"], "ci": [q_profile["lower"], q_profile["upper"]],
        "level": level, "converged": q_profile["converged"],
        "upper_unbounded": q_profile["upper_unbounded"],
        "method": "Viechtbauer (2007) Q-profile interval for tau-squared",
        "method_source": _TAU2_Q_PROFILE_SOURCE, "applicable": True,
        "warnings": list(q_profile["warnings"]),
    })
    # Higgins & Thompson (2002) transform, mapped to tau-squared through the
    # typical sampling variance v = (k-1)/C (k >= 3).
    if k >= 3:
        ht = i2_h_interval(q, k, level=level)
        typical = df / c_constant
        tau2_intervals.append({
            "key": "tau2_ht_transform",
            "estimate": max(0.0, (q - df) / c_constant),
            "ci": [typical * (ht["h_ci"][0] ** 2 - 1.0), typical * (ht["h_ci"][1] ** 2 - 1.0)],
            "level": level, "converged": True, "upper_unbounded": False,
            "method": "Higgins & Thompson (2002) transformed interval for tau-squared "
                      "(H limits mapped through the typical variance (k-1)/C)",
            "method_source": _TAU2_HT_SOURCE, "applicable": True,
            "warnings": list(ht["warnings"]),
        })
    else:
        tau2_intervals.append({
            "key": "tau2_ht_transform", "estimate": None, "ci": None,
            "method": "Higgins & Thompson (2002) transformed interval for tau-squared",
            "method_source": _TAU2_HT_SOURCE, "applicable": False,
            "reason": "needs at least three studies",
        })

    i2_point = max(0.0, (q - df) / q) * 100 if q > 0 else 0.0
    i2_section: dict = {
        "point_percent": i2_point,
        "point_formula": "max(0, (Q - df) / Q) * 100 (house formula of coscreen.review_analysis)",
        "interval_percent": None,
        "method": "Higgins & Thompson (2002) transformed interval",
        "method_source": _TAU2_HT_SOURCE,
        "applicable": k >= 3,
    }
    if k >= 3:
        i2_section["interval_percent"] = list(ht["i2_ci_percent"])
        i2_section["warnings"] = list(ht["warnings"])
        i2_section["h"] = ht["h"]
        i2_section["h_ci"] = list(ht["h_ci"])
    else:
        i2_section["reason"] = "H and I-squared transformed intervals need at least three studies"

    bootstrap_section: list[dict] | None = None
    if bootstrap_n is not None:
        from coscreen.bootstrap_interval import bootstrap_pooled_interval

        bootstrap_section = []
        for model in MODELS:
            boot = bootstrap_pooled_interval(
                y, [math.sqrt(value) for value in v],
                model=model, interval_type=bootstrap_interval_type, n_boot=int(bootstrap_n),
                seed=int(seed), level=level,
            )
            low, high = boot["ci_low"], boot["ci_high"]
            ci_display = [_display_or_none(transform, low), _display_or_none(transform, high)]
            bootstrap_section.append({
                "model": model, "model_label": MODEL_LABELS[model],
                "estimate": fits[model]["pooled"], "ci": [low, high],
                "estimate_display": _display_or_none(transform, fits[model]["pooled"]),
                "ci_display": ci_display,
                "width": high - low,
                "side_of_null": ("negative" if high < 0 else "positive" if low > 0 else "crosses")
                                if has_null else None,
                "n_boot": boot["n_boot"], "seed": boot["seed"], "interval_type": boot["interval_type"],
                "mc_se": boot["mc_se"], "seconds": boot["seconds"],
                "method": f"first-level parametric bootstrap {bootstrap_interval_type} interval",
                "method_source": _BOOTSTRAP_SOURCE,
                "warnings": list(boot["warnings"]) + (
                    ["bootstrap limits are outside the representable display range "
                     "for this measure"] if any(value is None for value in ci_display) else []),
            })

    applicable_cells = [cell for cell in cells if cell["applicable"]]
    if applicable_cells:
        widths = {cell["key"]: cell["width"] for cell in applicable_cells}
        widest_key = max(widths, key=widths.get)
        narrowest_key = min(widths, key=widths.get)
        width_ratio = widths[widest_key] / widths[narrowest_key]
    else:
        # 展示范围整体溢出（极端 SE）：无可用 cell，汇总退化为空而不崩。
        widths, widest_key, narrowest_key, width_ratio = {}, None, None, None
    sides = {cell["key"]: cell["side_of_null"] for cell in applicable_cells}
    positive = [key for key, side in sides.items() if side == "positive"]
    negative = [key for key, side in sides.items() if side == "negative"]
    crossing = [key for key, side in sides.items() if side == "crosses"]
    model_estimates = [fits[model]["pooled"] for model in MODELS]
    signs = {"positive" if value > 0 else "negative" if value < 0 else "zero" for value in model_estimates}
    summary = {
        "interval_widths_analysis_scale": widths,
        "widest_cell": widest_key,
        "narrowest_cell": narrowest_key,
        "width_ratio_widest_to_narrowest": width_ratio,
        "side_of_null": sides,
        "n_crossing_null": len(crossing),
        "crossing_null_cells": crossing,
        "direction_flip": bool(positive and negative),
        "direction_flip_cells": {"positive": positive, "negative": negative},
        "mixed_conclusion": len({side for side in sides.values() if side is not None}) > 1
                            if has_null else False,
        "estimate_sign_consistent_across_models": len(signs - {"zero"}) <= 1,
    }

    return {
        "level": level,
        "k": k,
        "study_ids": [study_id for study_id, _, _, _ in cleaned],
        "measure": measure,
        "measure_family": info["family"],
        "analysis_scale": info["analysis_scale"],
        "null_display": info["null_display"],
        "q": q,
        "q_df": df,
        "c_constant": c_constant,
        "tau2_point_estimates": {
            "fixed": 0.0,
            "dl": fits["dl"]["tau2"],
            "reml": fits["reml"]["tau2"],
        },
        "i2": i2_section,
        "cells": cells,
        "pooled_effect_profile": pooled_profile_cell,
        "tau2_intervals": tau2_intervals,
        "bootstrap": bootstrap_section,
        "summary": summary,
        "notes": [
            "All pooling and interval formulas follow coscreen.review_analysis._synthesize_rows; "
            "unlike the house function, level is an explicit parameter, so Wald limits use the exact "
            "normal quantile instead of the rounded 1.96.",
            "The fixed_hksj and fixed_prediction cells are shown for side-by-side sensitivity "
            "comparison; coscreen.review_analysis.synthesize rejects fixed + HKSJ for primary "
            "synthesis, and the fixed prediction interval assumes tau-squared = 0.",
            "Cells and widths are on the analysis scale (log for ratios/rates, logit for proportions, "
            "Fisher z for correlations); the _display fields carry the back-transformed values.",
            "Bootstrap intervals are first-level parametric percentile or basic intervals "
            "(coscreen.bootstrap_interval); they run only when bootstrap_n and seed are both given.",
        ],
    }
