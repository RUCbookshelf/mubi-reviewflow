"""Period-adjusted continuous effects from a 2-sequence, 2-period crossover."""

from __future__ import annotations

import math

_FORMAT = "crossover_2x2_md"
_FIELDS = {
    "n_ab", "mean_diff_ab", "sd_diff_ab",
    "n_ba", "mean_diff_ba", "sd_diff_ba", "design_note",
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


def _count(value: object, name: str) -> int:
    if type(value) is not int or value < 2:
        raise ValueError(f"{name} must be an integer >= 2")
    return value


def calculate_crossover_effect(format_name: str, values: dict) -> dict:
    """Calculate an equal-sequence-weighted MD and SE from complete-pair data."""
    if format_name != _FORMAT:
        raise ValueError("unsupported crossover effect format")
    if not isinstance(values, dict) or set(values) != _FIELDS:
        raise ValueError(f"values must contain exactly: {', '.join(sorted(_FIELDS))}")

    data = dict(values)
    n_ab = _count(data["n_ab"], "n_ab")
    n_ba = _count(data["n_ba"], "n_ba")
    means = (_number(data["mean_diff_ab"], "mean_diff_ab"),
             _number(data["mean_diff_ba"], "mean_diff_ba"))
    sds = (_number(data["sd_diff_ab"], "sd_diff_ab"),
           _number(data["sd_diff_ba"], "sd_diff_ba"))
    for name, sd in zip(("sd_diff_ab", "sd_diff_ba"), sds):
        if sd <= 0:
            raise ValueError(f"{name} must be > 0")
    note = data["design_note"]
    if not isinstance(note, str) or not note.strip():
        raise ValueError("design_note must be a nonempty source and design-assumption note")

    try:
        # Halve first so valid averages and contrasts do not overflow on addition.
        estimate = math.fsum((means[0] / 2, means[1] / 2))
        period_contrast = math.fsum((means[0] / 2, -means[1] / 2))
        se = math.hypot(
            sds[0] / math.sqrt(n_ab) / 2,
            sds[1] / math.sqrt(n_ba) / 2,
        )
    except OverflowError as exc:
        raise ValueError("the resulting estimate and standard error must be finite") from exc
    if not all(math.isfinite(value) for value in (estimate, period_contrast, se)) or se <= 0:
        raise ValueError("the resulting estimate and standard error must be finite and positive")

    return {
        "measure": "MD",
        "estimate": estimate,
        "se": se,
        "se_scale": "natural",
        "period_contrast_p1_minus_p2": period_contrast,
        "input_data": data,
        "entry_method": _FORMAT,
        "source_note": note,
        "warnings": [
            "The (AB minus BA)/2 period contrast is descriptive, not a carryover estimate. "
            "Differential carryover, sequence-specific period effects and missing-pair bias "
            "are not corrected; use a published adjusted estimate when available."
        ],
    }
