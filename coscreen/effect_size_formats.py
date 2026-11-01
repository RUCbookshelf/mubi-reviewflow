"""Convert supported reported effect formats to an estimate and standard error."""

from __future__ import annotations

import math

from scipy.stats import norm

_RATIO_MEASURES = {"HR", "RATE_RATIO", "RR", "OR"}
_ADDITIVE_MEASURES = {"RD", "MD", "SMD"}
_GENERIC_MEASURES = _RATIO_MEASURES | _ADDITIVE_MEASURES | {"FISHER_Z"}


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


def _positive_integer(value: object, name: str, minimum: int = 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")
    try:
        finite = math.isfinite(float(value))
    except (OverflowError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite integer") from exc
    if not finite:
        raise ValueError(f"{name} must be a finite integer")
    return value


def _critical_value(confidence_level: object) -> float:
    level = _number(confidence_level, "confidence_level")
    if not 0 < level < 1:
        raise ValueError("confidence_level must be strictly between 0 and 1")
    critical = float(norm.ppf((1 + level) / 2))
    if not math.isfinite(critical) or critical <= 0:
        raise ValueError("confidence_level does not produce a finite normal quantile")
    return critical


def _check_rd_range(measure: str, *values: float) -> None:
    if measure == "RD" and any(value < -1 or value > 1 for value in values):
        raise ValueError("risk differences and confidence limits must be between -1 and 1")


def _reported_half_unit(value: float) -> float:
    """The half unit of the last reported decimal place of ``value``.

    Reported effect estimates and confidence limits are rounded before
    publication ("HR 1.5 (1.2–2.0)"), so the midpoint they imply can differ
    from the reported estimate by up to that rounding. ``repr`` yields the
    shortest round-tripping decimal string, i.e. the precision the author
    actually wrote (``1.5`` -> 0.05, ``0.611`` -> 0.0005, full-precision
    floats -> their last ULP). This is the width of the reporting grain, not
    a numerical tolerance.
    """
    text = repr(abs(float(value)))
    mantissa, _, exponent = text.lower().partition("e")
    decimals = len(mantissa.partition(".")[2])
    place = int(exponent) if exponent else 0
    return 0.5 * 10.0 ** (place - decimals)


def _interval_asymmetry_tolerance(estimate: float, low: float, high: float,
                                  reported_scale: str) -> float:
    """Allowed asymmetry of a reported interval around its estimate.

    The numbers are taken as reported, so the allowance is the publication
    grain of the decimals actually written. ``reported_scale`` names what the
    calculator had to do with them before testing symmetry:

    ``"identity"``    the reported numbers already are the analysis scale
                      (natural additive effects, Fisher z, or a CI already
                      reported on the log scale), so a rounding of ``h``
                      moves the value by ``h``;
    ``"log_of_reported"`` the reported natural-scale ratio is log-transformed,
                      so a rounding of ``h`` in ``x`` moves ``log(x)`` by
                      about ``h/x``.

    The estimate and both limits each contribute. The log budget is capped at
    0.25 so a coarsely reported small ratio cannot admit an interval whose
    midpoint is unrelated to the estimate.
    """
    if reported_scale == "log_of_reported":
        grain = (2 * _reported_half_unit(estimate) / abs(estimate)
                 + _reported_half_unit(low) / abs(low)
                 + _reported_half_unit(high) / abs(high))
        return min(grain, 0.25)
    return (2 * _reported_half_unit(estimate)
            + _reported_half_unit(low) + _reported_half_unit(high))


def _ci_result(measure: str, estimate: float, ci_low: float, ci_high: float,
               confidence_level: object, scale: str, input_data: dict,
               entry_method: str) -> dict:
    if input_data.get("ci_method") != "wald_normal":
        raise ValueError("CI-to-SE conversion requires a reported normal/Wald interval on the analysis scale; exact, score, profile and t intervals are unsupported")
    critical = _critical_value(confidence_level)
    if not ci_low < ci_high or not ci_low <= estimate <= ci_high:
        raise ValueError("estimate must lie inside an ordered confidence interval")

    if measure in _RATIO_MEASURES:
        if scale == "natural":
            if min(estimate, ci_low, ci_high) <= 0:
                raise ValueError("ratio estimates and confidence limits must be positive")
            log_estimate, log_low, log_high = map(math.log, (estimate, ci_low, ci_high))
            output_estimate = estimate
        elif scale == "log":
            log_estimate, log_low, log_high = estimate, ci_low, ci_high
            try:
                output_estimate = math.exp(log_estimate)
            except OverflowError as exc:
                raise ValueError("log estimate is outside the representable ratio range") from exc
            if output_estimate == 0 or not math.isfinite(output_estimate):
                raise ValueError("log estimate is outside the representable ratio range")
        else:
            raise ValueError("ratio measure scale must be 'natural' or 'log'")
        analysis_estimate, analysis_low, analysis_high = log_estimate, log_low, log_high
        se_scale = "log"
    elif measure in _ADDITIVE_MEASURES:
        if scale != "natural":
            raise ValueError("RD, MD and SMD require scale='natural'")
        _check_rd_range(measure, estimate, ci_low, ci_high)
        output_estimate = estimate
        analysis_estimate, analysis_low, analysis_high = estimate, ci_low, ci_high
        se_scale = "natural"
    elif measure == "FISHER_Z":
        if scale != "fisher_z":
            raise ValueError("FISHER_Z requires scale='fisher_z'")
        output_estimate = estimate
        analysis_estimate, analysis_low, analysis_high = estimate, ci_low, ci_high
        se_scale = "fisher_z"
    else:
        raise ValueError("unsupported generic measure")

    # F4-20: a CI re-derived from an exact SE is symmetric to round-off, but a
    # published CI is rounded to the reported precision (HR 1.5 (1.2-2.0)).
    # The gate therefore allows the publication grain of the reported numbers,
    # so it still rejects an interval whose midpoint is not the reported
    # estimate.
    reported_scale = ("log_of_reported"
                      if measure in _RATIO_MEASURES and scale == "natural"
                      else "identity")
    lower_arm = analysis_estimate - analysis_low
    upper_arm = analysis_high - analysis_estimate
    tolerance = max(
        _interval_asymmetry_tolerance(estimate, ci_low, ci_high, reported_scale),
        1e-7 * max(abs(lower_arm), abs(upper_arm)),
        1e-12,
    )
    if not abs(lower_arm - upper_arm) <= tolerance:
        raise ValueError("confidence interval must be symmetric around estimate on the analysis scale")
    se = (analysis_high - analysis_low) / (2 * critical)
    if not math.isfinite(se) or se <= 0:
        raise ValueError("confidence interval must imply a positive finite SE")
    return {"measure": measure, "estimate": output_estimate, "se": se,
            "se_scale": se_scale, "input_data": input_data, "entry_method": entry_method}


def calculate_effect(format_name: str, values: dict) -> dict:
    """Convert one supported effect format to its analysis estimate and SE.

    Ratio confidence intervals are treated as normal on the log scale. A
    correlation uses Fisher's z with SE ``1 / sqrt(n - 3)``; its returned
    estimate is z (back-transform with ``tanh`` for display). Event-rate SEs
    assume independent Poisson counts and known person-time, with no zero cells.
    """
    if format_name == "correlation_r_n":
        data = _fields(values, {"r", "n"})
        r = _number(data["r"], "r")
        n = _positive_integer(data["n"], "n", 4)
        if not -1 < r < 1:
            raise ValueError("r must be strictly between -1 and 1 for finite Fisher z")
        return {"measure": "FISHER_Z", "estimate": math.atanh(r),
                "se": 1 / math.sqrt(n - 3), "se_scale": "fisher_z",
                "input_data": data, "entry_method": format_name}

    if format_name == "hazard_ratio_ci":
        data = _fields(values, {"estimate", "ci_low", "ci_high", "confidence_level", "ci_method"})
        estimate, low, high = (_number(data[key], key) for key in ("estimate", "ci_low", "ci_high"))
        if min(estimate, low, high) <= 0:
            raise ValueError("hazard ratio and confidence limits must be positive")
        return _ci_result("HR", estimate, low, high, data["confidence_level"], "natural", data, format_name)

    if format_name == "rate_ratio_events_time":
        data = _fields(values, {"events_t", "time_t", "events_c", "time_c"})
        events_t = _positive_integer(data["events_t"], "events_t")
        events_c = _positive_integer(data["events_c"], "events_c")
        time_t, time_c = _number(data["time_t"], "time_t"), _number(data["time_c"], "time_c")
        if time_t <= 0 or time_c <= 0:
            raise ValueError("person-time must be positive")
        log_estimate = math.log(events_t) - math.log(time_t) - math.log(events_c) + math.log(time_c)
        try:
            estimate = math.exp(log_estimate)
        except OverflowError as exc:
            raise ValueError("event rates must imply a finite positive rate ratio") from exc
        se = math.sqrt(1 / events_t + 1 / events_c)
        if not math.isfinite(estimate) or estimate <= 0 or not math.isfinite(se) or se <= 0:
            raise ValueError("event rates must imply a finite positive rate ratio")
        return {"measure": "RATE_RATIO", "estimate": estimate, "se": se,
                "se_scale": "log", "input_data": data, "entry_method": format_name}

    if format_name == "generic_ci":
        data = _fields(values, {"measure", "estimate", "ci_low", "ci_high",
                                "confidence_level", "scale", "ci_method"})
        measure, scale = data["measure"], data["scale"]
        if not isinstance(measure, str) or measure not in _GENERIC_MEASURES:
            raise ValueError(f"measure must be one of {', '.join(sorted(_GENERIC_MEASURES))}")
        if not isinstance(scale, str):
            raise ValueError("scale must be a string")
        estimate, low, high = (_number(data[key], key) for key in ("estimate", "ci_low", "ci_high"))
        return _ci_result(measure, estimate, low, high, data["confidence_level"], scale, data, format_name)

    if format_name == "generic_wald_p":
        data = _fields(values, {"measure", "estimate", "p", "scale"})
        measure, scale = data["measure"], data["scale"]
        if not isinstance(measure, str) or measure not in _GENERIC_MEASURES:
            raise ValueError(f"measure must be one of {', '.join(sorted(_GENERIC_MEASURES))}")
        if not isinstance(scale, str):
            raise ValueError("scale must be a string")
        estimate, p = _number(data["estimate"], "estimate"), _number(data["p"], "p")
        _check_rd_range(measure, estimate)
        if not 0 < p < 1:
            raise ValueError("p must be an exact two-sided value strictly between 0 and 1")
        z = float(norm.isf(p / 2))
        if not math.isfinite(z) or z <= 0:
            raise ValueError("p does not produce a finite normal quantile")

        if measure in _RATIO_MEASURES:
            if scale == "natural":
                if estimate <= 0:
                    raise ValueError("ratio estimate must be positive")
                theta, output_estimate = math.log(estimate), estimate
            elif scale == "log":
                theta = estimate
                try:
                    output_estimate = math.exp(theta)
                except OverflowError as exc:
                    raise ValueError("log estimate is outside the representable ratio range") from exc
                if output_estimate == 0 or not math.isfinite(output_estimate):
                    raise ValueError("log estimate is outside the representable ratio range")
            else:
                raise ValueError("ratio measure scale must be 'natural' or 'log'")
            se_scale = "log"
        elif measure in _ADDITIVE_MEASURES:
            if scale != "natural":
                raise ValueError("RD, MD and SMD require scale='natural'")
            theta, output_estimate, se_scale = estimate, estimate, "natural"
        else:
            if scale != "fisher_z":
                raise ValueError("FISHER_Z requires scale='fisher_z'")
            theta, output_estimate, se_scale = estimate, estimate, "fisher_z"

        if theta == 0:
            raise ValueError("estimate must differ from the null value to recover an SE")
        se = abs(theta) / z
        if not math.isfinite(output_estimate) or not math.isfinite(se) or se <= 0:
            raise ValueError("estimate and p must imply a finite positive SE")
        return {"measure": measure, "estimate": output_estimate, "se": se,
                "se_scale": se_scale, "input_data": data, "entry_method": format_name}

    raise ValueError("unsupported effect format")
