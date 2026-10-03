"""Approximate effects for unadjusted parallel cluster trials with variable sizes."""

from __future__ import annotations

import copy
import math

from scipy.stats import t as student_t

from .cluster_trial_effects import _exp, _integer, _number, calculate_cluster_trial_effect


def calculate_cluster_trial_variable_size_effect(values: dict) -> dict:
    """Apply an arm-specific CV design effect to unadjusted two-arm summaries.

    Each arm must supply ``clusters``, ``participants``, ``cv``, and the same
    outcome fields accepted by :func:`calculate_cluster_trial_effect`. The
    input also requires an ``icc_source`` citation string.

    Setting the optional flag ``few_cluster_ci`` to true additionally reports
    a study-level 95% interval using a t quantile with
    ``df = treatment clusters + control clusters - 2``. The SE is never
    changed: pooling still receives the uncorrected normal-approximation SE.
    """
    if not isinstance(values, dict):
        raise ValueError("values must be an object")
    required = {"outcome_type", "measure", "icc", "icc_source", "analysis_status", "arms"}
    optional = {"source_provenance", "few_cluster_ci"}
    if not required <= values.keys() or set(values) - required - optional:
        raise ValueError("values must contain outcome_type, measure, icc, icc_source, analysis_status and arms")
    if not isinstance(values["icc_source"], str) or not values["icc_source"].strip():
        raise ValueError("icc_source must be a non-empty citation string")
    few_cluster_ci = values.get("few_cluster_ci", False)
    if not isinstance(few_cluster_ci, bool):
        raise ValueError("few_cluster_ci must be a boolean")

    arms = values["arms"]
    if not isinstance(arms, dict) or set(arms) != {"treatment", "control"}:
        raise ValueError("arms must contain exactly treatment and control")
    cvs = {}
    cluster_counts = {}
    for name, arm in arms.items():
        if not isinstance(arm, dict) or "cv" not in arm:
            raise ValueError(f"{name} arm must contain cv")
        if "participants" not in arm:
            raise ValueError(f"{name} arm must contain participants")
        cv = _number(arm["cv"], f"{name} cv")
        if cv < 0:
            raise ValueError(f"{name} cv must be nonnegative")
        cvs[name] = cv
        if few_cluster_ci:
            if "clusters" not in arm:
                raise ValueError(f"few_cluster_ci requires clusters in the {name} arm")
            cluster_counts[name] = _integer(arm["clusters"], f"{name} clusters", 1)

    if few_cluster_ci:
        df = cluster_counts["treatment"] + cluster_counts["control"] - 2
        if df < 1:
            raise ValueError(
                "few_cluster_ci requires df = treatment clusters + control clusters - 2 of at least 1; "
                f"the supplied arms give df = {df}, so no study-level t interval exists"
            )

    standard_values = copy.deepcopy(values)
    standard_values.pop("icc_source")
    standard_values.pop("few_cluster_ci", None)
    for arm in standard_values["arms"].values():
        arm.pop("cv")
    result = calculate_cluster_trial_effect(standard_values)
    icc = result["cluster_adjustment"]["icc"]
    adjustment_arms = {}

    for name, arm in result["cluster_adjustment"]["arms"].items():
        cv = cvs[name]
        mean_size = arm["mean_cluster_size"]
        if arm["participants"] == arm["clusters"] and cv != 0:
            raise ValueError(f"{name} cv must be 0 when mean cluster size is 1")
        design_effect = (
            1.0 if icc == 0
            else 1 + (((1 + cv * cv) * mean_size) - 1) * icc
        )
        effective_n = arm["participants"] / design_effect
        if (not math.isfinite(design_effect) or design_effect < 1
                or not math.isfinite(effective_n) or not 0 < effective_n <= arm["participants"]):
            raise ValueError(f"{name} design effect must imply a positive finite effective sample size")
        adjusted = {
            "clusters": arm["clusters"],
            "participants": arm["participants"],
            "mean_cluster_size": mean_size,
            "cv": cv,
            "design_effect": design_effect,
            "effective_sample_size": effective_n,
        }
        if values["outcome_type"] == "binary":
            events = standard_values["arms"][name]["events"]
            adjusted["effective_events"] = events / design_effect
            adjusted["effective_non_events"] = (arm["participants"] - events) / design_effect
        adjustment_arms[name] = adjusted

    if values["outcome_type"] == "continuous":
        treatment = standard_values["arms"]["treatment"]
        control = standard_values["arms"]["control"]
        se = math.hypot(
            treatment["sd"] / math.sqrt(adjustment_arms["treatment"]["effective_sample_size"]),
            control["sd"] / math.sqrt(adjustment_arms["control"]["effective_sample_size"]),
        )
    else:
        treatment = standard_values["arms"]["treatment"]
        control = standard_values["arms"]["control"]
        n_t, n_c = treatment["participants"], control["participants"]
        events_t, events_c = treatment["events"], control["events"]
        de_t = adjustment_arms["treatment"]["design_effect"]
        de_c = adjustment_arms["control"]["design_effect"]
        if values["measure"] == "OR":
            variance = math.fsum((
                de_t / events_t, de_t / (n_t - events_t),
                de_c / events_c, de_c / (n_c - events_c),
            ))
        elif values["measure"] == "RR":
            variance = math.fsum((
                de_t * (1 / events_t - 1 / n_t),
                de_c * (1 / events_c - 1 / n_c),
            ))
        else:
            risk_t, risk_c = events_t / n_t, events_c / n_c
            variance = math.fsum((
                de_t * risk_t * (1 - risk_t) / n_t,
                de_c * risk_c * (1 - risk_c) / n_c,
            ))
        se = math.sqrt(variance)

    if not math.isfinite(se) or se <= 0:
        raise ValueError("inputs must imply a positive finite SE")
    result.update({
        "se": se,
        "input_data": copy.deepcopy(values),
        "source_provenance": copy.deepcopy(values.get("source_provenance")),
        "entry_method": "cluster_trial_variable_size_design_effect",
        "cluster_adjustment": {
            "method": "variable_size_design_effect_effective_sample_size",
            "icc": icc,
            "icc_source": values["icc_source"],
            "arms": adjustment_arms,
        },
        "assumptions": [
            "The source summary is unadjusted for clustering.",
            "One outcome ICC is shared across both arms; mean size and CV are arm-specific.",
            "The design effect uses the published CV correction for variable cluster sizes.",
            "The CV correction is applied to arm-level effective sample sizes and does not reproduce a fitted cluster-weighted model.",
            "The normal-approximation SE does not adjust for few-cluster degrees of freedom or other design features.",
        ],
    })
    if few_cluster_ci:
        t_critical = float(student_t.ppf(0.975, df))
        ci_analysis_low = result["analysis_estimate"] - t_critical * se
        ci_analysis_high = result["analysis_estimate"] + t_critical * se
        if result["se_scale"] == "log":
            transform = lambda value: _exp(value, "confidence bound")  # noqa: E731
        else:
            transform = float
        result.update({
            "ci_method": "t_clusters_minus_arms",
            "df": df,
            "t_critical": t_critical,
            "ci_analysis_low": ci_analysis_low,
            "ci_analysis_high": ci_analysis_high,
            "ci_low": transform(ci_analysis_low),
            "ci_high": transform(ci_analysis_high),
            "warnings": [
                "The 95% interval uses a t distribution with df = total clusters - number of arms and is a "
                "study-level description only; pooling still uses the uncorrected normal-approximation SE, so "
                "few-cluster variance instability will propagate into any synthesis, and this interval does not "
                "change the SE entered into pooling.",
            ],
        })
    return result
