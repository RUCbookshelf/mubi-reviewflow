"""Calculate raw mean differences from paired or change-score summaries."""

from __future__ import annotations

import math

_FIELDS = {
    "paired_md_sd_diff": {"n", "mean_a", "mean_b", "sd_diff", "design"},
    "paired_md_correlation": {
        "n", "mean_a", "mean_b", "sd_a", "sd_b", "correlation",
        "correlation_source", "correlation_source_note", "design",
    },
    "parallel_change_sd": {
        "n_t", "baseline_mean_t", "final_mean_t", "sd_change_t",
        "n_c", "baseline_mean_c", "final_mean_c", "sd_change_c",
    },
    "parallel_change_correlations": {
        "n_t", "baseline_mean_t", "final_mean_t", "sd_baseline_t", "sd_final_t",
        "correlation_t", "correlation_source_t", "correlation_source_note_t",
        "n_c", "baseline_mean_c", "final_mean_c", "sd_baseline_c", "sd_final_c",
        "correlation_c", "correlation_source_c", "correlation_source_note_c",
    },
}
_DESIGNS = {"paired_conditions", "pre_post", "crossover"}
_CORRELATION_SOURCES = {"reported", "derived", "assumed"}


def _fields(values: dict, required: set[str]) -> dict:
    if not isinstance(values, dict) or set(values) != required:
        raise ValueError(f"values must contain exactly: {', '.join(sorted(required))}")
    return dict(values)


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


def _sd(value: object, name: str) -> float:
    result = _number(value, name)
    if result < 0:
        raise ValueError(f"{name} must be >= 0")
    return result


def _design(value: object) -> str:
    if not isinstance(value, str) or value not in _DESIGNS:
        raise ValueError("design must be paired_conditions, pre_post or crossover")
    return value


def _correlation(data: dict, key: str, source_key: str, note_key: str,
                 description: str, warnings: list[str]) -> float:
    value = _number(data[key], key)
    if not -1 <= value <= 1:
        raise ValueError(f"{key} must be between -1 and 1")
    source = data[source_key]
    if not isinstance(source, str) or source not in _CORRELATION_SOURCES:
        raise ValueError(f"{source_key} must be reported, derived or assumed")
    note = data[note_key]
    if not isinstance(note, str) or not note.strip():
        raise ValueError(f"{note_key} must be a nonempty provenance note")
    if source == "assumed":
        warnings.append(
            f"An assumed {description} correlation was used; examine plausible values in a sensitivity analysis."
        )
    return value


def _difference_standard_error(sd_a: float, sd_b: float, correlation: float, n: int) -> float:
    scale = max(sd_a, sd_b)
    if scale == 0:
        return 0.0
    a, b = sd_a / scale, sd_b / scale
    # Scale before squaring to keep valid large SDs within floating-point range.
    scaled_variance = math.fsum(((a - b) ** 2, 2 * a * b * (1 - correlation)))
    result = scale * (math.sqrt(scaled_variance) / math.sqrt(n))
    if not math.isfinite(result):
        raise ValueError("the resulting standard error must be finite")
    return result


def _standard_error(sd: float, n: int) -> float:
    return sd / math.sqrt(n)


def _result(format_name: str, data: dict, estimate: float, se: float,
            warnings: list[str]) -> dict:
    if not math.isfinite(estimate):
        raise ValueError("the resulting estimate must be finite")
    if not math.isfinite(se) or se <= 0:
        raise ValueError("the resulting standard error must be finite and positive")
    return {"measure": "MD", "estimate": estimate, "se": se,
            "se_scale": "natural", "input_data": data,
            "entry_method": format_name, "warnings": warnings}


def calculate_dependent_effect(format_name: str, values: dict) -> dict:
    """Return a raw mean difference and natural-scale SE for one exact input format."""
    if not isinstance(format_name, str) or format_name not in _FIELDS:
        raise ValueError("unsupported dependent effect format")
    data = _fields(values, _FIELDS[format_name])
    warnings: list[str] = []

    if format_name in {"paired_md_sd_diff", "paired_md_correlation"}:
        n = _count(data["n"], "n")
        mean_a, mean_b = _number(data["mean_a"], "mean_a"), _number(data["mean_b"], "mean_b")
        try:
            estimate = math.fsum((mean_a, -mean_b))
        except OverflowError as exc:
            raise ValueError("the resulting estimate must be finite") from exc
        design = _design(data["design"])
        if design == "pre_post":
            warnings.append("A single-group pre/post change is not a controlled treatment effect.")
        elif design == "crossover":
            warnings.append("Crossover carryover and period effects are not adjusted by this calculation.")

        if format_name == "paired_md_sd_diff":
            se = _standard_error(_sd(data["sd_diff"], "sd_diff"), n)
        else:
            sd_a, sd_b = _sd(data["sd_a"], "sd_a"), _sd(data["sd_b"], "sd_b")
            correlation = _correlation(
                data, "correlation", "correlation_source", "correlation_source_note",
                "within-pair", warnings,
            )
            se = _difference_standard_error(sd_a, sd_b, correlation, n)
        return _result(format_name, data, estimate, se, warnings)

    n_t = _count(data["n_t"], "n_t")
    n_c = _count(data["n_c"], "n_c")
    means = [_number(data[key], key) for key in (
        "final_mean_t", "baseline_mean_t", "final_mean_c", "baseline_mean_c",
    )]
    try:
        estimate = math.fsum((means[0], -means[1], -means[2], means[3]))
    except OverflowError as exc:
        raise ValueError("the resulting estimate must be finite") from exc

    if format_name == "parallel_change_sd":
        sd_t = _sd(data["sd_change_t"], "sd_change_t")
        sd_c = _sd(data["sd_change_c"], "sd_change_c")
    else:
        sd_baseline_t = _sd(data["sd_baseline_t"], "sd_baseline_t")
        sd_final_t = _sd(data["sd_final_t"], "sd_final_t")
        sd_baseline_c = _sd(data["sd_baseline_c"], "sd_baseline_c")
        sd_final_c = _sd(data["sd_final_c"], "sd_final_c")
        correlation_t = _correlation(
            data, "correlation_t", "correlation_source_t", "correlation_source_note_t",
            "treatment-arm baseline-to-final", warnings,
        )
        correlation_c = _correlation(
            data, "correlation_c", "correlation_source_c", "correlation_source_note_c",
            "comparator-arm baseline-to-final", warnings,
        )
        se_t = _difference_standard_error(sd_baseline_t, sd_final_t, correlation_t, n_t)
        se_c = _difference_standard_error(sd_baseline_c, sd_final_c, correlation_c, n_c)
        return _result(format_name, data, estimate, math.hypot(se_t, se_c), warnings)

    se = math.hypot(_standard_error(sd_t, n_t), _standard_error(sd_c, n_c))
    return _result(format_name, data, estimate, se, warnings)
