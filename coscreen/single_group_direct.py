"""Convert direct one-group estimates and reported uncertainty to an IV effect."""

from __future__ import annotations

import math
from statistics import NormalDist


_FORMATS = {
    "single_mean_direct": ("MEAN", "natural", {"raw"}),
    "single_proportion_direct": ("LOGIT_PROP", "logit", {"raw", "logit"}),
    "single_rate_direct": ("LOG_RATE", "log", {"raw", "log"}),
}


def _number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number")
    try:
        result = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name} must be a finite number")
    return result


def _logit(value: float) -> float:
    return math.log(value) - math.log1p(-value)


def calculate_single_group_direct_effect(format_name: str, values: dict) -> dict:
    """Return a one-group mean, logit proportion, or log rate and its SE.

    The input ``estimate`` is always on the raw scale. ``precision_scale``
    identifies the scale of ``se``, ``variance``, or CI limits; the returned
    estimate and SE use the measure's analysis scale.
    """
    if not isinstance(format_name, str) or format_name not in _FORMATS:
        raise ValueError("unsupported direct single-group format")
    if not isinstance(values, dict):
        raise ValueError("values must be a dict")

    measure, se_scale, allowed_scales = _FORMATS[format_name]
    data = dict(values)
    estimate = _number(data.get("estimate"), "estimate")
    precision_scale = data.get("precision_scale")
    if not isinstance(precision_scale, str) or precision_scale not in allowed_scales:
        raise ValueError(f"precision_scale must be one of: {', '.join(sorted(allowed_scales))}")

    if measure == "LOGIT_PROP":
        if not 0 < estimate < 1:
            raise ValueError("proportion estimate must be strictly between 0 and 1")
        analysis_estimate = _logit(estimate)
    elif measure == "LOG_RATE":
        if estimate <= 0:
            raise ValueError("rate estimate must be positive")
        analysis_estimate = math.log(estimate)
    else:
        analysis_estimate = estimate

    if measure == "LOG_RATE" and not -745 < analysis_estimate < 709:
        raise ValueError("log rate is outside the representable positive rate range")

    uncertainty_keys = set(data) - {"estimate", "precision_scale", "ci_method"}
    if "ci_method" in data and uncertainty_keys != {"ci_lower", "ci_upper", "confidence_level"}:
        raise ValueError("ci_method is only supported with confidence interval inputs")
    warnings = []
    if "se" in uncertainty_keys and len(uncertainty_keys) == 1:
        se = _number(data["se"], "se")
        if se <= 0:
            raise ValueError("se must be positive")
        if precision_scale == "raw":
            se /= estimate * (1 - estimate) if measure == "LOGIT_PROP" else estimate if measure == "LOG_RATE" else 1
            if measure != "MEAN":
                warnings.append("A raw-scale SE is converted to the analysis scale by the first-order delta method.")
    elif "variance" in uncertainty_keys and len(uncertainty_keys) == 1:
        variance = _number(data["variance"], "variance")
        if variance <= 0:
            raise ValueError("variance must be positive")
        se = math.sqrt(variance)
        if precision_scale == "raw":
            se /= estimate * (1 - estimate) if measure == "LOGIT_PROP" else estimate if measure == "LOG_RATE" else 1
            if measure != "MEAN":
                warnings.append("A raw-scale variance is converted to the analysis scale by the first-order delta method.")
    elif {"ci_lower", "ci_upper", "confidence_level"} == uncertainty_keys:
        if data.get("ci_method") != "wald_normal":
            raise ValueError("CI-to-SE conversion requires a reported normal/Wald interval on the selected analysis scale; exact, score, profile and t intervals are unsupported")
        lower = _number(data["ci_lower"], "ci_lower")
        upper = _number(data["ci_upper"], "ci_upper")
        confidence_level = _number(data["confidence_level"], "confidence_level")
        if not 0 < confidence_level < 1:
            raise ValueError("confidence_level must be between 0 and 1")
        if lower >= upper:
            raise ValueError("ci_lower must be less than ci_upper")
        if precision_scale == "raw":
            if measure == "LOGIT_PROP":
                if not 0 < lower < upper < 1:
                    raise ValueError("proportion CI bounds must be strictly between 0 and 1 on the raw scale")
                lower, upper = _logit(lower), _logit(upper)
            elif measure == "LOG_RATE":
                if lower <= 0:
                    raise ValueError("rate CI bounds must be positive on the raw scale")
                lower, upper = math.log(lower), math.log(upper)
        if not lower <= analysis_estimate <= upper:
            raise ValueError("estimate must lie within the confidence interval")
        try:
            z = NormalDist().inv_cdf((1 + confidence_level) / 2)
        except ValueError as exc:
            raise ValueError("confidence_level must produce a finite normal quantile") from exc
        if not math.isfinite(z) or z <= 0:
            raise ValueError("confidence_level must produce a finite normal quantile")
        se = (upper - lower) / (2 * z)
        warnings.append("CI width is converted using the confirmed symmetric two-sided normal/Wald method on the selected analysis scale. Verify the original report; this software cannot independently confirm its interval method.")
    else:
        raise ValueError("provide exactly one of se, variance, or ci_lower/ci_upper/confidence_level")

    if not math.isfinite(analysis_estimate):
        raise ValueError("the resulting estimate must be finite")
    if not math.isfinite(se) or se <= 0:
        raise ValueError("the resulting standard error must be finite and positive")
    return {
        "measure": measure,
        "estimate": analysis_estimate,
        "se": se,
        "se_scale": se_scale,
        "input_data": data,
        "entry_method": format_name,
        "warnings": warnings,
    }
