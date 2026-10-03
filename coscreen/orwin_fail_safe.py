"""Orwin's effect-size fail-safe N for independent study estimates."""

from __future__ import annotations

import math

_RATIO_MEASURES = {"RR", "OR", "HR", "RATE_RATIO"}
_NATURAL_MEASURES = {"RD", "MD", "SMD"}
_MEASURES = _RATIO_MEASURES | _NATURAL_MEASURES | {"FISHER_Z"}


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


def calculate_orwin_fail_safe(
    rows: list[dict],
    measure: str,
    target_effect: float,
    missing_effect: float,
) -> dict:
    """Return Orwin's unweighted arithmetic-mean fail-safe N.

    ``estimate`` is supplied on the reported scale (ratios are converted to
    logs); ``target_effect`` and ``missing_effect`` are already on the analyzed
    scale. Standard errors are validated but do not enter Orwin's formula.
    """
    if not isinstance(measure, str) or measure not in _MEASURES:
        raise ValueError(f"measure must be one of {', '.join(sorted(_MEASURES))}")
    target = _finite_number(target_effect, "target_effect")
    missing = _finite_number(missing_effect, "missing_effect")
    if not isinstance(rows, list) or not rows:
        raise ValueError("at least one independent study row is required")

    seen_ids: set[str] = set()
    effects = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(f"row at index {index} must be a dictionary")
        required = ("study_id", "measure", "estimate", "se")
        absent = [key for key in required if key not in row]
        if absent:
            raise ValueError(f"row at index {index} is missing: {', '.join(absent)}")

        study_id = row["study_id"]
        if not isinstance(study_id, str) or not study_id.strip():
            raise ValueError(f"row at index {index} needs a nonempty study_id")
        study_id = study_id.strip()
        if study_id in seen_ids:
            raise ValueError("rows must have a unique study_id per independent study")
        seen_ids.add(study_id)
        if row["measure"] != measure:
            raise ValueError("all row measures must match the requested measure")

        estimate = _finite_number(row["estimate"], f"estimate for study {study_id}")
        se = _finite_number(row["se"], f"se for study {study_id}")
        if se <= 0:
            raise ValueError("standard errors must be positive")
        if measure in _RATIO_MEASURES:
            if estimate <= 0:
                raise ValueError("ratio estimates must be positive")
            estimate = math.log(estimate)
        effects.append(estimate)

    try:
        observed_mean = math.fsum(effects) / len(effects)
    except OverflowError as exc:
        raise ValueError("observed effects are outside the supported numeric range") from exc
    if not math.isfinite(observed_mean):
        raise ValueError("observed effects are outside the supported numeric range")

    if missing == target:
        if observed_mean != target:
            raise ValueError("target is unattainable when missing_effect equals target_effect")
        formula_value = 0.0
    elif (missing < target and observed_mean <= target) or (
        missing > target and observed_mean >= target
    ):
        formula_value = 0.0
    else:
        numerator = observed_mean - target
        denominator = target - missing
        if not math.isfinite(numerator) or not math.isfinite(denominator):
            raise ValueError("Orwin parameters are outside the supported numeric range")
        formula_value = len(effects) * (numerator / denominator)
        if not math.isfinite(formula_value) or formula_value < 0:
            raise ValueError("Orwin parameters are unattainable or outside the supported numeric range")

    effect_scale = "log" if measure in _RATIO_MEASURES else (
        "fisher_z" if measure == "FISHER_Z" else "natural"
    )
    return {
        "method_name": "Orwin fail-safe N",
        "measure": measure,
        "n_studies": len(effects),
        "effect_scale": effect_scale,
        "observed_mean": observed_mean,
        "target_effect": target,
        "missing_effect": missing,
        "formula_value": formula_value,
        "fail_safe_n": math.ceil(formula_value),
        "assumptions": [
            "Each row is one independent study and contributes equally to the arithmetic observed mean.",
            "Every missing study has the explicitly supplied missing_effect; target_effect and missing_effect are on the analyzed scale.",
            "Ratio estimates are log transformed, FISHER_Z estimates are already Fisher z, and other measures stay on their natural scale.",
            "Standard errors are checked for finite positive values but are not used in this formula.",
        ],
        "limitations": [
            "This sensitivity count does not estimate how many unpublished studies exist and does not prove publication bias.",
            "The arithmetic-mean calculation does not model study precision, heterogeneity, or dependencies beyond one selected effect per independent study.",
            "Card's worked example uses raw r; FISHER_Z analyses must use Fisher z targets and missing-effect values, so its raw-r thresholds do not transfer directly.",
        ],
    }
