"""Explicit effect-estimator metadata and independent-group Hedges' g.

The existing ``SMD`` API code remains unchanged. This module provides an
explicit, versioned calculation path so an estimator and variance formula are
never inferred from the broad ``SMD`` measure code alone.
"""

from __future__ import annotations

import copy
import math
from collections.abc import Mapping

from scipy.special import poch

CONTRACT_VERSION = "reviewflow.smd.v1"
_EFFECT_DIRECTIONS = {"first_vs_second", "second_vs_first"}
_SUMMARY_FIELDS = {"n_t", "mean_t", "sd_t", "n_c", "mean_c", "sd_c"}
_TEST_FORMATS = {
    "independent_t_n", "independent_d_n", "independent_p_n", "independent_f_n",
}
_LEGACY_APPROXIMATE_SUMMARY_FORMATS = {
    "independent_summary_stats", "analysis_calculate_smd", "continuous arms",
    "estimated_summary_arms", "multi-arm combination", "ipd_two_stage",
}
SMD_ESTIMATORS = (
    {
        "measure": "SMD", "estimator": "hedges_g", "status": "implemented",
        "calculation_version": CONTRACT_VERSION,
        "input_formats": ["independent_summary_stats", "independent_t_n", "independent_d_n",
                          "independent_p_n", "independent_f_n", "independent_g_n"],
        "standardizer": "pooled_within_group_sd",
        "small_sample_corrections": ["exact_gamma_ratio", "reported_g_no_recorrection"],
        "variance_methods": ["metafor_vtype_LS2", "metafor_vtype_LS"],
    },
    {
        "measure": "SMD", "estimator": "glass_delta", "status": "implemented",
        "implementation": "coscreen.glass_delta_effect.calculate_glass_delta_effect",
        "standardizer": "control_arm_sd", "small_sample_correction": "none",
        "variance_method": "metafor_vtype_LS_SMD1",
    },
    {
        "measure": "SMD", "estimator": "cohen_d", "status": "conversion_input",
        "input_format": "independent_d_n",
        "note": "Converted to Hedges g for pooled independent-group analysis.",
    },
    {
        "measure": "SMD", "estimator": "unclassified_smd", "status": "legacy_user_reported",
        "note": "Estimator metadata is unknown; do not infer it from measure=SMD.",
    },
)


def _number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number")
    try:
        number = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if not math.isfinite(number):
        raise ValueError(f"{name} must be a finite number")
    return number


def _count(value: object, name: str) -> int:
    if type(value) is not int or value < 2:
        raise ValueError(f"{name} must be an integer >= 2")
    try:
        if not math.isfinite(float(value)):
            raise ValueError(f"{name} must be a finite integer")
    except (OverflowError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite integer") from exc
    return value


def _exact_j(df: float) -> float:
    if not math.isfinite(df) or df <= 1:
        raise ValueError("pooled-SD degrees of freedom must be > 1")
    # J = Gamma(df/2) / (sqrt(df/2) * Gamma((df-1)/2)); poch is
    # algebraically identical and remains stable for large, finite df.
    j = float(poch((df - 1) / 2, 0.5) / math.sqrt(df / 2))
    if not math.isfinite(j) or not 0 < j <= 1:
        raise ValueError("the Hedges correction factor is not finite")
    return j


def _validate_metadata(metadata: Mapping[str, object]) -> dict:
    if not isinstance(metadata, Mapping):
        raise ValueError("estimator_metadata must be an object")
    result = dict(metadata)
    estimator = result.get("estimator")
    standardizer = result.get("standardizer")
    correction = result.get("small_sample_correction")
    variance_method = result.get("variance_method")

    if not isinstance(estimator, str):
        raise ValueError("estimator must be a string")
    if standardizer is not None and not isinstance(standardizer, str):
        raise ValueError("standardizer must be a string or null")
    if correction is not None and not isinstance(correction, str):
        raise ValueError("small_sample_correction must be a string or null")
    if not isinstance(variance_method, str) or not variance_method.strip():
        raise ValueError("SMD metadata needs an explicit variance_method")
    if result.get("effect_class") != "standardized_mean_difference":
        raise ValueError("SMD metadata needs effect_class='standardized_mean_difference'")
    if estimator == "hedges_g":
        if standardizer != "pooled_within_group_sd":
            raise ValueError("Hedges g requires the pooled within-group SD standardizer")
        if correction not in {"exact_gamma_ratio", "approximate_1_minus_3_over_4df_minus_1",
                              "reported_g_no_recorrection"}:
            raise ValueError("Hedges g needs an explicit small-sample correction status")
        allowed_variances = {
            "exact_gamma_ratio": {"metafor_vtype_LS", "metafor_vtype_LS2"},
            "approximate_1_minus_3_over_4df_minus_1": {"LS2_formula_with_approximate_J"},
            "reported_g_no_recorrection": {"metafor_vtype_LS", "user_reported_se"},
        }
        if variance_method not in allowed_variances[correction]:
            raise ValueError("variance_method conflicts with the Hedges g correction status")
    elif estimator == "cohen_d":
        if standardizer != "pooled_within_group_sd" or correction != "none":
            raise ValueError("Cohen d requires pooled SD and no small-sample correction")
        if variance_method != "user_reported_se":
            raise ValueError("Cohen d requires an explicitly reported variance method")
    elif estimator == "glass_delta":
        if standardizer != "control_arm_sd" or correction != "none":
            raise ValueError("Glass delta requires control-arm SD and no small-sample correction")
        if variance_method not in {"metafor_vtype_LS_SMD1", "user_reported_se"}:
            raise ValueError("variance_method conflicts with Glass delta")
    elif estimator == "unclassified_smd":
        if standardizer not in {None, "pooled_within_group_sd", "control_arm_sd"}:
            raise ValueError("unclassified SMD has an unsupported standardizer")
        if variance_method not in {"user_reported_se", "legacy_unknown"}:
            raise ValueError("unclassified SMD must retain an unknown or user-reported variance method")
    else:
        raise ValueError("unsupported SMD estimator")

    if result.get("calculation_version") is not None and not isinstance(
            result["calculation_version"], str):
        raise ValueError("calculation_version must be a string or null")
    df = result.get("standardizer_df")
    df_number = None
    if df is not None:
        try:
            df_number = _number(df, "standardizer_df")
        except ValueError as exc:
            raise ValueError("standardizer_df must be finite and greater than 1") from exc
        if df_number <= 1:
            raise ValueError("standardizer_df must be finite and greater than 1")
    factor = result.get("correction_factor")
    if factor is not None:
        factor_number = _number(factor, "correction_factor")
        if not 0 < factor_number <= 1:
            raise ValueError("correction_factor must be in (0, 1]")
        if estimator == "hedges_g" and df_number is not None:
            expected = (_exact_j(df_number) if correction == "exact_gamma_ratio"
                        else 1 - 3 / (4 * df_number - 1)
                        if correction == "approximate_1_minus_3_over_4df_minus_1"
                        else None)
            if expected is not None and not math.isclose(
                    factor_number, expected, rel_tol=1e-12, abs_tol=0):
                raise ValueError("correction_factor does not match its declared method and degrees of freedom")
    return result


def validate_smd_effect(estimate: object, se: object, metadata: Mapping[str, object],
                        variance: object | None = None) -> dict:
    """Validate an SMD estimate, positive SE, optional vi, and its metadata."""
    yi = _number(estimate, "estimate")
    standard_error = _number(se, "se")
    if standard_error <= 0:
        raise ValueError("se must be positive")
    vi = standard_error * standard_error
    if not math.isfinite(vi) or vi <= 0:
        raise ValueError("sampling variance must be positive and finite")
    if variance is not None:
        supplied_vi = _number(variance, "variance")
        if supplied_vi <= 0 or not math.isclose(supplied_vi, vi, rel_tol=1e-12, abs_tol=0):
            raise ValueError("variance must equal se squared")
    return {"estimate": yi, "se": standard_error, "variance": vi,
            "estimator_metadata": _validate_metadata(metadata)}


def _result(estimate: float, variance: float, cohen_d: float | None, df: float,
            correction: str, correction_factor: float | None,
            variance_method: str, raw_input_scale: str, entry_method: str, values: dict,
            effect_direction: str, source_locator: str,
            assumptions: list[str]) -> dict:
    finite_values = (estimate, variance) if cohen_d is None else (estimate, variance, cohen_d)
    if not all(math.isfinite(x) for x in finite_values) or variance <= 0:
        raise ValueError("the resulting estimate and sampling variance must be finite and variance positive")
    se = math.sqrt(variance)
    metadata = {
        "effect_class": "standardized_mean_difference",
        "estimator": "hedges_g",
        "standardizer": "pooled_within_group_sd",
        "standardizer_df": df,
        "small_sample_correction": correction,
        "correction_factor": correction_factor,
        "variance_method": variance_method,
        "calculation_version": CONTRACT_VERSION,
        "reference_implementation": {
            "name": "metafor::escalc",
            "version": "5.2-1",
            "measure": "SMD",
            "vtype": variance_method.rsplit("_", 1)[-1],
        },
    }
    payload = copy.deepcopy(values)
    payload.update({
        "raw_input_scale": raw_input_scale,
        "analysis_scale": "standardized_mean_difference",
        "effect_direction": effect_direction,
        "source_locator": source_locator,
        "assumptions": assumptions,
        "estimator_metadata": metadata,
    })
    checked = validate_smd_effect(estimate, se, metadata, variance)
    return {
        "measure": "SMD",
        "effect_class": "standardized_mean_difference",
        "estimator": "hedges_g",
        "estimate": checked["estimate"],
        "cohen_d": cohen_d,
        "hedges_g": checked["estimate"],
        "variance": checked["variance"],
        "vi": checked["variance"],
        "se": checked["se"],
        "se_scale": "natural",
        "effect_direction": effect_direction,
        "source_locator": source_locator,
        "estimator_metadata": checked["estimator_metadata"],
        "input_data": payload,
        "entry_method": entry_method,
    }


def calculate_hedges_g(format_name: str, values: dict, *,
                       effect_direction: str, source_locator: str = "") -> dict:
    """Calculate independent-group Hedges' g with a named metafor variance.

    Supported formats are ``independent_summary_stats`` (exact gamma J and
    LS2), existing test-statistic formats (exact gamma J and LS2), and
    ``independent_g_n`` for an already-corrected reported g (no second
    correction; LS variance). The latter explicitly preserves the supplied g.
    """
    if not isinstance(format_name, str):
        raise ValueError("format_name must be a string")
    if not isinstance(effect_direction, str) or effect_direction not in _EFFECT_DIRECTIONS:
        raise ValueError("effect_direction must be first_vs_second or second_vs_first")
    if not isinstance(source_locator, str):
        raise ValueError("source_locator must be a string")
    if not isinstance(values, dict):
        raise ValueError("values must be an object")

    if format_name == "independent_summary_stats":
        if set(values) != _SUMMARY_FIELDS:
            raise ValueError("values must contain exactly: " + ", ".join(sorted(_SUMMARY_FIELDS)))
        nt, nc = _count(values["n_t"], "n_t"), _count(values["n_c"], "n_c")
        mt, mc = _number(values["mean_t"], "mean_t"), _number(values["mean_c"], "mean_c")
        sdt, sdc = _number(values["sd_t"], "sd_t"), _number(values["sd_c"], "sd_c")
        if min(sdt, sdc) < 0:
            raise ValueError("standard deviations must be nonnegative")
        total = nt + nc
        try:
            df = float(total - 2)
        except OverflowError as exc:
            raise ValueError("combined group size must be finite") from exc
        scale = max(sdt, sdc)
        if scale == 0:
            raise ValueError("pooled within-group SD must be positive")
        pooled_sd = scale * math.sqrt(((nt - 1) * (sdt / scale) ** 2
                                      + (nc - 1) * (sdc / scale) ** 2) / df)
        difference = mt - mc
        if not math.isfinite(difference) or not math.isfinite(pooled_sd) or pooled_sd <= 0:
            raise ValueError("the pooled SD and mean difference must be finite")
        d = difference / pooled_sd
        j = _exact_j(df)
        g = j * d
        # metafor escalc(measure="SMD", correct=TRUE, vtype="LS2").
        variance = j * j * (1 / nt + 1 / nc + d * d / (2 * total))
        if not math.isfinite(d) or not math.isfinite(g) or not math.isfinite(variance):
            raise ValueError("the resulting estimate and sampling variance must be finite")
        return _result(g, variance, d, df, "exact_gamma_ratio", j,
                       "metafor_vtype_LS2", "independent_group_summary_stats",
                       format_name, values, effect_direction,
                       source_locator,
                       ["two independent groups", "pooled within-group SD",
                        "exact gamma-ratio Hedges correction"])

    if format_name in _TEST_FORMATS:
        from coscreen.test_statistic_effect_formats import calculate_test_statistic_effect

        raw = calculate_test_statistic_effect(format_name, values)
        nt, nc = values["n_t"], values["n_c"]
        try:
            df = float(nt + nc - 2)
        except OverflowError as exc:
            raise ValueError("combined group size must be finite") from exc
        j = _exact_j(df)
        raw_input_scale = {
            "independent_t_n": "signed_independent_samples_t",
            "independent_d_n": "reported_cohen_d",
            "independent_p_n": "exact_two_sided_p_and_direction",
            "independent_f_n": "F_df1_1_df2_pooled_t_direction",
        }[format_name]
        assumptions = ["two independent groups", "pooled-variance independent-groups t test",
                       "exact gamma-ratio Hedges correction", "metafor LS2 sampling variance"]
        if format_name == "independent_p_n":
            assumptions.append("p is an exact two-sided p value; threshold-only p values are unsupported")
        if format_name == "independent_f_n":
            assumptions.append("F has numerator df 1 and denominator df n_t+n_c-2")
        return _result(raw["hedges_g"], raw["se"] ** 2, raw["cohen_d"], df,
                       "exact_gamma_ratio", j, "metafor_vtype_LS2", raw_input_scale,
                       format_name, values, effect_direction, source_locator, assumptions)

    if format_name == "independent_g_n":
        if set(values) != {"g", "n_t", "n_c"}:
            raise ValueError("values must contain exactly: g, n_t, n_c")
        g = _number(values["g"], "g")
        nt, nc = _count(values["n_t"], "n_t"), _count(values["n_c"], "n_c")
        total = nt + nc
        try:
            df = float(total - 2)
        except OverflowError as exc:
            raise ValueError("combined group size must be finite") from exc
        # LS is written directly in terms of the already-corrected reported g.
        variance = 1 / nt + 1 / nc + g * g / (2 * total)
        if not math.isfinite(variance) or variance <= 0:
            raise ValueError("the resulting sampling variance must be finite and positive")
        _exact_j(df)  # Validate that the study sizes support this estimator.
        return _result(g, variance, None, df, "reported_g_no_recorrection", None,
                       "metafor_vtype_LS", "reported_hedges_g", format_name, values, effect_direction,
                       source_locator,
                       ["input is already Hedges' g and is not corrected again",
                        "two independent groups", "metafor LS sampling variance"])

    raise ValueError("unsupported independent-groups Hedges g input format")


def estimator_registry_snapshot() -> list[dict]:
    """Return a JSON-ready copy of SMD estimators and their limits."""
    return copy.deepcopy(list(SMD_ESTIMATORS))


def legacy_smd_metadata(entry_method: str, input_data: Mapping[str, object] | None = None) -> dict:
    """Describe old SMD paths; unknown formats remain explicitly unclassified."""
    data = input_data if isinstance(input_data, Mapping) else {}
    if entry_method == "glass_delta_two_arm":
        return {
            "effect_class": "standardized_mean_difference", "estimator": "glass_delta",
            "standardizer": "control_arm_sd", "small_sample_correction": "none",
            "variance_method": "metafor_vtype_LS_SMD1",
            "calculation_version": "legacy_glass_delta_v1",
        }
    if entry_method in _TEST_FORMATS:
        return {
            "effect_class": "standardized_mean_difference", "estimator": "hedges_g",
            "standardizer": "pooled_within_group_sd", "small_sample_correction": "exact_gamma_ratio",
            "variance_method": "metafor_vtype_LS2",
            "calculation_version": "legacy_test_statistic_v1",
        }
    if entry_method in _LEGACY_APPROXIMATE_SUMMARY_FORMATS:
        return {
            "effect_class": "standardized_mean_difference", "estimator": "hedges_g",
            "standardizer": "pooled_within_group_sd",
            "small_sample_correction": "approximate_1_minus_3_over_4df_minus_1",
            "variance_method": "LS2_formula_with_approximate_J",
            "calculation_version": "legacy_summary_smd_v1",
        }
    if data.get("standardizer") == "control_arm_sd" and data.get("bias_correction") == "none":
        return {
            "effect_class": "standardized_mean_difference", "estimator": "glass_delta",
            "standardizer": "control_arm_sd", "small_sample_correction": "none",
            "variance_method": "user_reported_se", "calculation_version": "legacy_manual_v1",
        }
    if data.get("standardizer") == "pooled_within_group_sd" and data.get("bias_correction") == "hedges":
        return {
            "effect_class": "standardized_mean_difference", "estimator": "hedges_g",
            "standardizer": "pooled_within_group_sd",
            "small_sample_correction": "reported_g_no_recorrection",
            "variance_method": "user_reported_se", "calculation_version": "legacy_manual_v1",
        }
    return {
        "effect_class": "standardized_mean_difference", "estimator": "unclassified_smd",
        "standardizer": data.get("standardizer"), "small_sample_correction": None,
        "variance_method": (
            "user_reported_se" if entry_method == "manual" else "legacy_unknown"
        ),
        "calculation_version": "legacy_unclassified_v1",
    }
