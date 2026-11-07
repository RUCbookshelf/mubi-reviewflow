"""Approximate effects for unadjusted parallel cluster trials with variable sizes."""

from __future__ import annotations

import math

from scipy.stats import t as student_t

from .cluster_trial_effects import (
    _copy_input,
    _exp,
    _finite_fsum,
    _integer,
    _number,
    _reciprocal_gap,
    _sanitize_provenance,
    calculate_cluster_trial_effect,
)

#: few-cluster t 分位数求值自由度的上界：scipy 的 ``stdtrit`` 只接受
#: int64/float 支撑域内的 df，``df > 2**63-1`` 时抛 TypeError（非
#: ValueError）→ 端点 500（F2-86）。超过该界时按 ``float(df)`` 求值——
#: stdtrit 内部本来就把 df 当双精度处理，实测 df 从 1 到 2**63-1 的随机
#: 抽样中 ``ppf(0.975, df)`` 与 ``ppf(0.975, float(df))`` 逐位相同，故这不是
#: 近似，而是同一数值（不拒绝输入、不改 df 回显）。
_MAX_T_QUANTILE_DF = 2 ** 63 - 1


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
        # F2-86：簇数本身只过了 `_integer(…, 1)`（无上界），两臂各 1e19 时
        # df≈2e19 超出 int64 的 stdtrit 支撑域，scipy 抛 TypeError（非
        # ValueError）→ 端点 500。df 仍在 int64 内时原样求值（逐位不变），
        # 超出时按 float(df) 求值（见 _MAX_T_QUANTILE_DF 的实测依据）。
        df_for_quantile = df if df <= _MAX_T_QUANTILE_DF else float(df)

    standard_values = {key: value for key, value in values.items()
                       if key not in {"icc_source", "few_cluster_ci"}}
    standard_values["arms"] = {name: {key: value for key, value in arm.items() if key != "cv"}
                               for name, arm in values["arms"].items()}
    if "source_provenance" in standard_values:
        # F2-80（deepcopy 深度族）/ F2-84（零校验族）：结构化浅拷贝替代
        # 递归 deepcopy——深嵌套 provenance 在 deepcopy 上抛 RecursionError
        # （非 ValueError）→ 500；provenance 先走族内统一的有界清洗入口。
        standard_values["source_provenance"] = _copy_input(
            {"source_provenance": standard_values["source_provenance"]})["source_provenance"]
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
            variance = _finite_fsum((
                de_t / events_t, de_t / (n_t - events_t),
                de_c / events_c, de_c / (n_c - events_c),
            ))
        elif values["measure"] == "RR":
            # F-04: same cancellation guard as the base calculator (the
            # duplicate expression here would otherwise re-cancel to zero on
            # the huge-count inputs the base call now accepts).
            variance = _finite_fsum((
                de_t * _reciprocal_gap(events_t, n_t),
                de_c * _reciprocal_gap(events_c, n_c),
            ))
        else:
            risk_t, risk_c = events_t / n_t, events_c / n_c
            variance = _finite_fsum((
                de_t * risk_t * (1 - risk_t) / n_t,
                de_c * risk_c * (1 - risk_c) / n_c,
            ))
        se = math.sqrt(variance)

    if not math.isfinite(se) or se <= 0:
        raise ValueError("inputs must imply a positive finite SE")
    result.update({
        "se": se,
        # F2-84（source_provenance 零校验族）：input_data 的 provenance 走
        # 族内统一的有界清洗入口，裸 NaN/深嵌套不会原样落库。
        "input_data": _copy_input(values),
        "source_provenance": _sanitize_provenance(values.get("source_provenance")),
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
        t_critical = float(student_t.ppf(0.975, df_for_quantile))
        ci_analysis_low = result["analysis_estimate"] - t_critical * se
        ci_analysis_high = result["analysis_estimate"] + t_critical * se
        # F2-88：natural 标度下 estimate/t·se 本身可以是有限大数（如 1e307），
        # 但二者相加溢出成 ±inf；旧实现把 inf 直接塞进响应，端点
        # JSONResponse(allow_nan=False) 渲染期 ValueError → 500。区间边界
        # 不可表示说明该研究级 t 区间在本数据上不存在，按值域拒绝（4xx）。
        if not math.isfinite(ci_analysis_low) or not math.isfinite(ci_analysis_high):
            raise ValueError(
                "the study-level t interval exceeds the representable float range; "
                "the effect and its SE are too large for a finite confidence bound")
        if result["se_scale"] == "log":
            transform = lambda value: _exp(value, "confidence bound")  # noqa: E731
        else:
            transform = float
        result.update({
            "ci_method": "t_clusters_minus_arms",
            "df": df,
            # df 超出 stdtrit 支撑域时按 _MAX_T_QUANTILE_DF 求值（t 分位数在
            # df≫1e15 已与正态不可区分），t_critical 即实际使用的值；df 回显
            # 保持输入真值（不因求值需要而改写）。
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
