"""Calculate a paired pre/post standardized mean change for one group."""

from __future__ import annotations

import math


_REQUIRED = {"n", "mean_pre", "mean_post", "sd_pre"}
_ALLOWED = _REQUIRED | {"sd_change", "correlation", "correlation_source"}
_STANDARDIZERS = ("change_sd", "pretest_sd")


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


def calculate_single_group_standardized_change(values: dict, *, standardizer: str) -> dict:
    """Return a Hedges-corrected standardized mean change for one group.

    Change is defined as ``mean_post - mean_pre``. The required keyword
    ``standardizer`` selects the denominator, matching ``metafor::escalc``
    change measures:

    - ``"pretest_sd"`` standardizes by ``sd_pre`` (SMCR/SMCRH): supply either
      the pre/post ``correlation`` with a non-empty ``correlation_source``, or
      the observed SD of paired differences ``sd_change``.
    - ``"change_sd"`` standardizes by ``sd_change`` (SMCC): ``sd_change`` is
      required and ``correlation`` is not accepted, because the change-score
      SD is already the denominator and no dependence information is needed.
    """
    if not isinstance(values, dict):
        raise ValueError("values must be a dict")
    data = dict(values)
    if any(not isinstance(key, str) for key in data):
        raise ValueError("field names must be strings")
    if not _REQUIRED <= data.keys():
        raise ValueError(f"values must include: {', '.join(sorted(_REQUIRED))}")
    if data.keys() - _ALLOWED:
        raise ValueError(f"unsupported fields: {', '.join(sorted(data.keys() - _ALLOWED))}")
    if standardizer not in _STANDARDIZERS:
        raise ValueError(f"standardizer must be one of: {', '.join(_STANDARDIZERS)}")

    has_change_sd = "sd_change" in data
    has_correlation = "correlation" in data
    if standardizer == "change_sd":
        if has_correlation:
            raise ValueError(
                "correlation is not valid with standardizer='change_sd'; "
                "the change-score SD sd_change is the required denominator"
            )
        if "correlation_source" in data:
            raise ValueError("correlation_source is only valid with correlation")
        if not has_change_sd:
            raise ValueError(
                "standardizer='change_sd' requires sd_change "
                "(the observed SD of the paired differences)"
            )
    else:
        if has_change_sd == has_correlation:
            raise ValueError("provide exactly one of sd_change or correlation")
        if has_correlation:
            source = data.get("correlation_source")
            if not isinstance(source, str) or not source.strip():
                raise ValueError("correlation_source must cite the source or rationale for correlation")
        elif "correlation_source" in data:
            raise ValueError("correlation_source is only valid with correlation")

    n = data["n"]
    if isinstance(n, bool) or not isinstance(n, int) or n < 3:
        raise ValueError("n must be an integer >= 3 complete pairs")
    try:
        if not math.isfinite(float(n)):
            raise ValueError("n must be a finite integer")
    except (OverflowError, ValueError) as exc:
        raise ValueError("n must be a finite integer") from exc

    mean_pre = _number(data["mean_pre"], "mean_pre")
    mean_post = _number(data["mean_post"], "mean_post")
    sd_pre = _number(data["sd_pre"], "sd_pre")
    if sd_pre <= 0:
        raise ValueError("sd_pre must be positive")

    difference = mean_post - mean_pre
    if standardizer == "change_sd":
        denominator = _number(data["sd_change"], "sd_change")
        if denominator < 0:
            raise ValueError("sd_change must be non-negative")
        if denominator == 0:
            raise ValueError("sd_change must be positive when standardizer='change_sd'")
    else:
        denominator = sd_pre
    d = difference / denominator
    if not math.isfinite(d):
        raise ValueError("the uncorrected standardized change must be finite")

    df = n - 1
    try:
        correction = math.exp(
            math.lgamma(df / 2) - math.log(math.sqrt(df / 2)) - math.lgamma((df - 1) / 2)
        )
    except (OverflowError, ValueError) as exc:
        raise ValueError("the Hedges correction must be finite") from exc
    estimate = correction * d

    try:
        if standardizer == "change_sd":
            variance = 1 / n + estimate**2 / (2 * n)
            variance_method = "SMCC_LS_Gibbons_1993"
        elif has_correlation:
            correlation = _number(data["correlation"], "correlation")
            if not -1 <= correlation <= 1:
                raise ValueError("correlation must be between -1 and 1")
            variance = 2 * (1 - correlation) / n + estimate**2 / (2 * n)
            variance_method = "SMCR_LS_Becker_1988"
        else:
            sd_change = _number(data["sd_change"], "sd_change")
            if sd_change < 0:
                raise ValueError("sd_change must be non-negative")
            variance = sd_change**2 / (sd_pre**2 * df) + estimate**2 / (2 * df)
            variance_method = "SMCRH_LS_Bonett_2008"
    except OverflowError as exc:
        raise ValueError("the variance calculation must be finite") from exc

    if not math.isfinite(estimate) or not math.isfinite(variance) or variance <= 0:
        raise ValueError("the calculation must produce a finite estimate and positive variance")

    measure = "SMCC" if standardizer == "change_sd" else "SMCR"
    entry_method = "single_group_smcc" if standardizer == "change_sd" else "single_group_smcr"
    return {
        "measure": measure,
        "estimate": estimate,
        "se": math.sqrt(variance),
        "se_scale": "natural",
        "input_data": data,
        "entry_method": entry_method,
        "raw_estimate": d,
        "hedges_correction": correction,
        "variance": variance,
        "variance_method": variance_method,
    }
