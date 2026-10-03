"""Convert single-group summaries to estimates and standard errors."""

from __future__ import annotations

import math

_FIELDS = {
    "single_mean_sd_n": {"n", "mean", "sd"},
    "single_proportion_events_n": {"events", "total"},
    "single_rate_events_time": {"events", "person_time"},
}


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


def _count(value: object, name: str, minimum: int) -> int:
    if type(value) is not int or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")
    return value


def _result(format_name: str, data: dict, measure: str, estimate: float,
            se: float, se_scale: str, variance: float,
            warnings: list[str]) -> dict:
    if not math.isfinite(estimate):
        raise ValueError("the resulting estimate must be finite")
    if not math.isfinite(se) or se <= 0:
        raise ValueError("the resulting standard error must be finite and positive")
    if not math.isfinite(variance) or variance <= 0:
        raise ValueError("the resulting variance must be finite and positive")
    return {"measure": measure, "estimate": estimate, "se": se,
            "se_scale": se_scale, "input_data": data,
            "entry_method": format_name, "warnings": warnings}


def calculate_single_group_effect(format_name: str, values: dict) -> dict:
    """Convert one supported single-group format to an estimate and SE."""
    if not isinstance(format_name, str) or format_name not in _FIELDS:
        raise ValueError("unsupported single-group format")
    data = _fields(values, _FIELDS[format_name])

    if format_name == "single_mean_sd_n":
        n = _count(data["n"], "n", 2)
        mean, sd = _number(data["mean"], "mean"), _number(data["sd"], "sd")
        try:
            se = sd / math.sqrt(n)
        except OverflowError:
            se = math.exp(math.log(sd) - 0.5 * math.log(n)) if sd > 0 else 0.0
        return _result(format_name, data, "MEAN", mean, se, "natural", se * se, [])

    if format_name == "single_proportion_events_n":
        events = _count(data["events"], "events", 0)
        total = _count(data["total"], "total", 0)
        if events == 0 or events == total:
            raise ValueError("boundary proportions (events=0 or events=total) are unsupported; no continuity correction is applied")
        if events > total:
            raise ValueError("events must be less than total")
        non_events = total - events
        estimate = math.log(events) - math.log(non_events)
        variance = math.fsum((1 / events, 1 / non_events))
        se = math.sqrt(variance)
        return _result(format_name, data, "LOGIT_PROP", estimate, se, "logit", variance, [
            "A normal inverse-variance approximation is used on the logit scale.",
            "Proportion boundary values (zero or all events) are unsupported without a continuity correction.",
        ])

    events = _count(data["events"], "events", 0)
    if events == 0:
        raise ValueError("zero-event single-group rates are unsupported; the log rate and standard error are undefined")
    person_time = _number(data["person_time"], "person_time")
    if person_time <= 0:
        raise ValueError("person_time must be positive")
    estimate = math.log(events) - math.log(person_time)
    variance = 1 / events
    se = math.sqrt(variance)
    return _result(format_name, data, "LOG_RATE", estimate, se, "log", variance, [
        "A normal inverse-variance approximation is used on the log rate scale.",
        "Zero events are an unsupported boundary case for this log-rate calculation.",
    ])
