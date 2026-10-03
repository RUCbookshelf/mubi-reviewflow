"""Convert a treatment-arm log-rank O-E and variance to an HR and SE."""

from __future__ import annotations

import copy
import math

_FORMAT = "survival_logrank_oe_v"
_FIELDS = {"oe", "variance", "oe_definition", "direction"}


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


def calculate_survival_logrank_effect(values: dict) -> dict:
    """Convert treatment-minus-control log-rank O-E/V to an approximate HR.

    ``oe`` must be treatment-arm observed minus expected log-rank events;
    ``variance`` must be the corresponding log-rank variance. The explicit
    definition and direction fields prevent silent sign reversals.
    """
    if not isinstance(values, dict) or set(values) - (_FIELDS | {"source_provenance"}) \
            or not _FIELDS <= values.keys():
        raise ValueError(
            "values must contain oe, variance, oe_definition and direction; "
            "source_provenance is optional"
        )
    data = copy.deepcopy(values)
    if data["oe_definition"] != "observed_minus_expected_treatment":
        raise ValueError("oe_definition must be 'observed_minus_expected_treatment'")
    if data["direction"] != "treatment_vs_control":
        raise ValueError("direction must be 'treatment_vs_control'")
    if "source_provenance" in data and not isinstance(data["source_provenance"], dict):
        raise ValueError("source_provenance must be an object")

    oe = _number(data["oe"], "oe")
    variance = _number(data["variance"], "variance")
    if variance <= 0:
        raise ValueError("variance must be positive")

    log_hr = oe / variance
    se = 1 / math.sqrt(variance)
    if not math.isfinite(log_hr) or not math.isfinite(se) or se <= 0:
        raise ValueError("O-E and variance must imply a finite log-HR and positive SE")
    try:
        hr = math.exp(log_hr)
    except OverflowError as exc:
        raise ValueError("O-E and variance imply an unrepresentable hazard ratio") from exc
    if not math.isfinite(hr) or hr <= 0:
        raise ValueError("O-E and variance imply an unrepresentable hazard ratio")

    return {
        "measure": "HR",
        "estimate": hr,
        "se": se,
        "se_scale": "log",
        "input_data": data,
        "entry_method": _FORMAT,
        "assumptions": [
            "O-E is the log-rank observed minus expected event count for the treatment arm; V is its corresponding variance.",
            "The treatment-versus-control log hazard ratio is approximated by (O-E)/V, with SE 1/sqrt(V).",
            "Interpretation as a common hazard ratio assumes proportional hazards for the analyzed event and censoring definitions.",
        ],
    }
