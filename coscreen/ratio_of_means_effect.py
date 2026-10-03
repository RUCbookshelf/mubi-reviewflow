"""Calculate the log ratio of means for two independent arms from summary statistics.

The ratio of means (ROM, also called the response ratio) is a continuous-outcome
effect size: the treatment-arm mean divided by the control-arm mean. It is a
different quantity from the risk ratio (RR) used for binary event counts. The
estimate is returned on the log scale (ln ROM) together with its large-sample
variance and standard error; synthesis should be performed on that log scale.
"""

from __future__ import annotations

import copy
import math

_FORMAT = "ratio_of_means_two_arm"
_FIELDS = {"mean_t", "mean_c", "sd_t", "sd_c", "n_t", "n_c", "direction"}
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


def calculate_ratio_of_means(values: dict) -> dict:
    """Calculate the log ratio of means and its delta-method variance.

    Treatment is arm t and control is arm c, with direction
    treatment_minus_control. The estimate is ln(mean_t / mean_c), so a positive
    estimate means the treatment mean exceeds the control mean. This is a
    continuous-outcome measure (ratio of arm means); it must not be confused
    with the risk ratio for binary event counts.
    """
    if (not isinstance(values, dict) or set(values) - (_FIELDS | _OPTIONAL_FIELDS)
            or not _FIELDS <= values.keys()):
        raise ValueError(
            "values must contain mean_t, mean_c, sd_t, sd_c, n_t, n_c and "
            "direction; source_provenance is optional"
        )

    data = copy.deepcopy(values)
    if data["direction"] != "treatment_minus_control":
        raise ValueError("direction must be 'treatment_minus_control'")
    if "source_provenance" in data and not isinstance(data["source_provenance"], dict):
        raise ValueError("source_provenance must be an object")

    mean_t = _number(data["mean_t"], "mean_t")
    mean_c = _number(data["mean_c"], "mean_c")
    sd_t = _number(data["sd_t"], "sd_t")
    sd_c = _number(data["sd_c"], "sd_c")
    n_t = _count(data["n_t"], "n_t")
    n_c = _count(data["n_c"], "n_c")
    if sd_t <= 0 or sd_c <= 0:
        raise ValueError("sd_t and sd_c must be > 0")
    if mean_t == 0 or mean_c == 0 or (mean_t > 0) != (mean_c > 0):
        raise ValueError(
            "mean_t and mean_c must be nonzero and share the same sign; the "
            "ratio of means is a ratio-scale measure and is undefined for "
            "zero or opposite-sign means"
        )

    try:
        ratio = mean_t / mean_c
        estimate = math.log(ratio)
        variance = math.fsum((
            sd_t * sd_t / (n_t * mean_t * mean_t),
            sd_c * sd_c / (n_c * mean_c * mean_c),
        ))
        se = math.sqrt(variance)
    except (OverflowError, ValueError, ZeroDivisionError) as exc:
        raise ValueError("the resulting effect and variance must be finite") from exc
    if not all(math.isfinite(value) for value in (ratio, estimate, variance, se)):
        raise ValueError("the resulting effect and variance must be finite")
    if variance <= 0 or se <= 0:
        raise ValueError("the resulting variance and standard error must be positive")

    return {
        "measure": "LOG_ROM",
        "estimate": estimate,
        "variance": variance,
        "se": se,
        "se_scale": "log",
        "ratio": ratio,
        "direction": "treatment_minus_control",
        "input_data": data,
        "entry_method": _FORMAT,
    }
