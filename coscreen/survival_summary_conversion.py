"""Convert reported median time-to-event summaries to an HR or a per-person-time rate.

Two conversions are provided:

- ``calculate_hr_from_median_survival``: from the two arms' Kaplan-Meier median
  times to event plus the numbers at risk (and optionally the reported event
  counts) to a treatment-versus-control hazard ratio with an implied O-E/V pair,
  using the same ``(O-E)/V`` conversion as directly reported log-rank statistics.
- ``calculate_rate_from_median``: from one arm's median time to event to the
  instantaneous event rate ``ln(2)/median`` per person-time unit.
"""

from __future__ import annotations

import copy
import math

_MEDIAN_DEFINITION = "km_median_time_to_event"
_Z_95 = 1.959963984540054
_HR_FORMAT = "survival_median_hr"
_RATE_FORMAT = "survival_median_rate"

_HR_FIELDS = {"median_treatment", "median_control", "n_treatment", "n_control",
              "oe_definition", "direction"}
_HR_OPTIONAL_FIELDS = {"events_treatment", "events_control", "time_unit", "source_provenance"}
_RATE_FIELDS = {"median", "median_definition", "time_unit"}
_RATE_OPTIONAL_FIELDS = {"n", "events", "source_provenance"}


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


def _positive(value: object, name: str) -> float:
    result = _number(value, name)
    if result <= 0:
        raise ValueError(f"{name} must be positive")
    return result


def _check_provenance(data: dict) -> None:
    if "source_provenance" in data and not isinstance(data["source_provenance"], dict):
        raise ValueError("source_provenance must be an object")


def _check_time_unit(data: dict) -> str:
    unit = data.get("time_unit")
    if not isinstance(unit, str) or not unit.strip():
        raise ValueError("time_unit must be a nonempty string naming the median's time unit")
    return unit


def _events_or_half_n(data: dict, arm: str, warnings: list[str]) -> float:
    """Reported event count for one arm, or n/2 under exponential follow-up."""
    events_key, n_key = f"events_{arm}", f"n_{arm}"
    number_at_risk = _positive(data[n_key], n_key)
    if events_key in data:
        events = _positive(data[events_key], events_key)
        if events > number_at_risk:
            warnings.append(
                f"Reported {events_key} exceeds {n_key}; check the source before pooling."
            )
        return events
    warnings.append(
        f"{arm.replace('_', ' ')} event count was not reported and was approximated as "
        "half the number at risk under exponential follow-up to the median; supply the "
        "reported event count when available."
    )
    return number_at_risk / 2


def calculate_hr_from_median_survival(values: dict) -> dict:
    """Convert two arms' median times to event into an approximate HR.

    Each arm is assumed exponential with hazard ``ln(2)/median``, so the
    treatment-versus-control log hazard ratio is
    ``ln(median_control/median_treatment)``. Its variance is
    ``variance_log_hr = 1/events_treatment + 1/events_control`` (events
    approximated by half the number at risk when event counts are not
    reported), so ``se = sqrt(variance_log_hr)``. For reuse with the log-rank
    O-E/V converter, the log-rank-style implied quantities
    ``implied_logrank_variance = 1/variance_log_hr`` and
    ``implied_logrank_oe = implied_logrank_variance * log(median_control/
    median_treatment)`` satisfy the same ``(O-E)/V`` conversion as a directly
    reported log-rank statistic. The explicit definition and direction fields
    prevent silent arm reversals; a missing median (e.g. "not reached") is
    rejected.
    """
    if not isinstance(values, dict) or set(values) - (_HR_FIELDS | _HR_OPTIONAL_FIELDS) \
            or not _HR_FIELDS <= values.keys():
        raise ValueError(
            "values must contain median_treatment, median_control, n_treatment, "
            "n_control, oe_definition and direction; events_treatment, events_control, "
            "time_unit and source_provenance are optional"
        )
    data = copy.deepcopy(values)
    if data["oe_definition"] != "implied_observed_minus_expected_treatment":
        raise ValueError("oe_definition must be 'implied_observed_minus_expected_treatment'")
    if data["direction"] != "treatment_vs_control":
        raise ValueError("direction must be 'treatment_vs_control'")
    for name in ("median_treatment", "median_control"):
        if data[name] is None:
            raise ValueError(
                f"{name} is None or the median was not reached; a study that does not "
                "report a median time to event cannot be converted, record it as not "
                "convertible instead of imputing"
            )
    _check_provenance(data)
    if "time_unit" in data:
        _check_time_unit(data)

    median_treatment = _positive(data["median_treatment"], "median_treatment")
    median_control = _positive(data["median_control"], "median_control")

    warnings = [
        "The median-ratio hazard ratio is exact only under exponential (constant-hazard) "
        "survival; with other shapes the ratio of medians is not the hazard ratio.",
        "The conversion ignores censoring patterns: arms with different censoring or "
        "follow-up bias the implied HR; prefer a log-rank O-E/V or reconstructed-curve "
        "conversion when those summaries are available.",
    ]
    events_treatment = _events_or_half_n(data, "treatment", warnings)
    events_control = _events_or_half_n(data, "control", warnings)

    variance_log_hr = 1 / events_treatment + 1 / events_control
    log_hr = math.log(median_control) - math.log(median_treatment)
    se = math.sqrt(variance_log_hr)
    if not math.isfinite(log_hr) or not math.isfinite(variance_log_hr) \
            or not math.isfinite(se) or se <= 0:
        raise ValueError("medians and event counts must imply a finite log-HR and positive SE")
    try:
        hr = math.exp(log_hr)
    except OverflowError as exc:
        raise ValueError("the medians imply an unrepresentable hazard ratio") from exc
    if not math.isfinite(hr) or hr <= 0:
        raise ValueError("the medians imply an unrepresentable hazard ratio")
    hazard_treatment = math.log(2) / median_treatment
    hazard_control = math.log(2) / median_control
    if not math.isfinite(hazard_treatment) or not math.isfinite(hazard_control):
        raise ValueError("the medians imply an unrepresentable per-arm hazard rate")
    try:
        implied_logrank_variance = 1 / variance_log_hr
        implied_logrank_oe = implied_logrank_variance * log_hr
    except OverflowError as exc:
        raise ValueError(
            "the medians and event counts imply unrepresentable implied log-rank statistics"
        ) from exc
    if not math.isfinite(implied_logrank_variance) or not math.isfinite(implied_logrank_oe):
        raise ValueError(
            "the medians and event counts imply unrepresentable implied log-rank statistics"
        )

    return {
        "measure": "HR",
        "estimate": hr,
        "se": se,
        "se_scale": "log",
        "variance_log_hr": variance_log_hr,
        "implied_logrank_oe": implied_logrank_oe,
        "implied_logrank_variance": implied_logrank_variance,
        "hazard_rate_treatment": hazard_treatment,
        "hazard_rate_control": hazard_control,
        "input_data": data,
        "entry_method": _HR_FORMAT,
        "assumptions": [
            "Each arm's survival is assumed exponential, so its hazard is ln(2)/median and "
            "the treatment-versus-control log hazard ratio is ln(median_control/median_treatment).",
            "The returned variance_log_hr is the variance of the log hazard ratio, "
            "1/events_treatment + 1/events_control, so SE(log HR) = sqrt(variance_log_hr).",
            "The variance uses the Poisson approximation Var(log hazard) = 1/events; when an "
            "arm's event count is not reported it is approximated by half the arm's number at "
            "risk, as half of an exponential population has had the event by the median.",
            "The returned implied_logrank_oe and implied_logrank_variance are reconstructed "
            "log-rank-style quantities (V = 1/variance_log_hr and O-E = V * "
            "ln(median_control/median_treatment)), so the log-rank conversion HR = exp((O-E)/V) "
            "with SE(log HR) = 1/sqrt(V) reproduces the same HR and SE.",
            "Interpretation as a common hazard ratio assumes proportional hazards and comparable "
            "censoring for the analyzed event.",
        ],
        "warnings": warnings,
    }


def calculate_rate_from_median(values: dict) -> dict:
    """Convert one arm's median time to event into a per-person-time rate.

    Under the exponential assumption the median solves ``0.5 = exp(-rate * median)``,
    so the instantaneous event rate is ``ln(2)/median`` in the declared time unit.
    The log-rate standard error uses the Poisson approximation
    ``SE(log rate) = 1/sqrt(events)``, with events approximated by half the
    number at risk when only ``n`` is reported; without either, no interval is
    returned. A missing median (e.g. "not reached") is rejected.
    """
    if not isinstance(values, dict) or set(values) - (_RATE_FIELDS | _RATE_OPTIONAL_FIELDS) \
            or not _RATE_FIELDS <= values.keys():
        raise ValueError(
            "values must contain median, median_definition and time_unit; "
            "n, events and source_provenance are optional"
        )
    data = copy.deepcopy(values)
    if data["median_definition"] != _MEDIAN_DEFINITION:
        raise ValueError(f"median_definition must be '{_MEDIAN_DEFINITION}'")
    time_unit = _check_time_unit(data)
    if data["median"] is None:
        raise ValueError(
            "median is None or the median was not reached; a study that does not report "
            "a median time to event cannot be converted, record it as not convertible "
            "instead of imputing"
        )
    _check_provenance(data)

    median = _positive(data["median"], "median")

    warnings = [
        "The rate ln(2)/median is the true instantaneous hazard only under exponential "
        "(constant-hazard) survival; with other shapes it is only an average rate.",
        "The conversion ignores censoring patterns: differential censoring or follow-up "
        "biases the reported median and therefore the rate.",
    ]
    number_at_risk = None
    if "n" in data:
        number_at_risk = _positive(data["n"], "n")
    if "events" in data:
        events = _positive(data["events"], "events")
        if number_at_risk is not None and events > number_at_risk:
            warnings.append("Reported events exceeds n; check the source before pooling.")
    elif number_at_risk is not None:
        events = number_at_risk / 2
        warnings.append(
            "Event count was not reported and was approximated as half the number at risk "
            "under exponential follow-up to the median; supply the reported event count "
            "when available."
        )
    else:
        events = None
        warnings.append(
            "Neither events nor n was reported, so no SE or interval can be computed; "
            "the rate is returned without an interval."
        )

    rate = math.log(2) / median
    if not math.isfinite(rate):
        raise ValueError("the median implies an unrepresentable event rate")
    if events is None:
        se = None
        ci = None
    else:
        se = 1 / math.sqrt(events)
        try:
            ci = [math.exp(math.log(rate) - _Z_95 * se), math.exp(math.log(rate) + _Z_95 * se)]
        except OverflowError as exc:
            raise ValueError("median and event count imply an unrepresentable interval") from exc
        if not all(math.isfinite(value) for value in ci):
            raise ValueError("median and event count imply an unrepresentable interval")

    return {
        "measure": "rate",
        "estimate": rate,
        "rate_unit": f"events_per_person_{time_unit.strip()}",
        "se": se,
        "se_scale": "log",
        "ci_95": ci,
        "events_assumed": events,
        "input_data": data,
        "entry_method": _RATE_FORMAT,
        "assumptions": [
            "The instantaneous hazard rate is ln(2)/median under the exponential assumption: "
            "the median solves S(median) = 0.5 = exp(-rate * median).",
            "The rate is expressed per person-time in the declared time_unit of the median.",
            "The interval uses the Poisson approximation SE(log rate) = 1/sqrt(events), with "
            "events approximated by half the number at risk when only n is reported.",
            "A single constant rate summarizes an assumed homogeneous exponential process; "
            "heterogeneity between patients is not represented.",
        ],
        "warnings": warnings,
    }
