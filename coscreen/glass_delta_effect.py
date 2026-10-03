"""Calculate an independent-arm Glass's delta from summary statistics."""

from __future__ import annotations

import copy
import math

_FORMAT = "glass_delta_two_arm"
_FIELDS = {"mean_t", "mean_c", "sd_c", "n_t", "n_c", "direction"}
_OPTIONAL_FIELDS = {"source_provenance"}


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


def _count(value: object, name: str) -> int:
    if type(value) is not int or value < 2:
        raise ValueError(f"{name} must be an integer >= 2")
    try:
        if not math.isfinite(float(value)):
            raise ValueError(f"{name} must be a finite integer")
    except (OverflowError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite integer") from exc
    return value


def calculate_glass_delta_effect(values: dict) -> dict:
    """Calculate Glass's delta and its large-sample variance approximation.

    Treatment is group t, control is group c, and direction is treatment minus
    control. The control-arm sample SD is the standardizer.
    """
    if (not isinstance(values, dict) or set(values) - (_FIELDS | _OPTIONAL_FIELDS)
            or not _FIELDS <= values.keys()):
        raise ValueError(
            "values must contain mean_t, mean_c, sd_c, n_t, n_c and direction; "
            "source_provenance is optional"
        )

    data = copy.deepcopy(values)
    if data["direction"] != "treatment_minus_control":
        raise ValueError("direction must be 'treatment_minus_control'")
    if "source_provenance" in data and not isinstance(data["source_provenance"], dict):
        raise ValueError("source_provenance must be an object")

    mean_t = _number(data["mean_t"], "mean_t")
    mean_c = _number(data["mean_c"], "mean_c")
    sd_c = _number(data["sd_c"], "sd_c")
    n_t = _count(data["n_t"], "n_t")
    n_c = _count(data["n_c"], "n_c")
    if sd_c <= 0:
        raise ValueError("sd_c must be > 0")

    try:
        difference = mean_t - mean_c
        estimate = difference / sd_c
        mean_se = math.sqrt(math.fsum((1 / n_t, 1 / n_c)))
        denominator_se = estimate / math.sqrt(n_c) / math.sqrt(2)
        se = math.hypot(mean_se, denominator_se)
        variance = se * se
    except (OverflowError, ValueError) as exc:
        raise ValueError("the resulting effect and variance must be finite") from exc
    if not all(math.isfinite(value) for value in (difference, estimate, variance, se)):
        raise ValueError("the resulting effect and variance must be finite")
    if variance <= 0 or se <= 0:
        raise ValueError("the resulting variance and standard error must be positive")

    return {
        "measure": "SMD",
        "estimate": estimate,
        "variance": variance,
        "se": se,
        "se_scale": "natural",
        "standardizer": "control_arm_sd",
        "direction": "treatment_minus_control",
        "input_data": data,
        "entry_method": _FORMAT,
    }
