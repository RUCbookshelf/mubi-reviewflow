"""Approximate effect estimates for unadjusted parallel cluster trials."""

from __future__ import annotations

import copy
import math

#: 审计载荷（``input_data``/``source_provenance``）的嵌套深度上限。
#: deepcopy 深度族的族内统一常量（与 ``glass_delta_effect`` /
#: ``binary_correlation_conversion`` 同值同形，三载体须同步修改）。
_MAX_PROVENANCE_DEPTH = 32
_DEEP_PROVENANCE_PLACEHOLDER = "<nesting too deep>"


def _sanitize_provenance(value, _depth: int = 0):  # noqa: ANN001
    """source_provenance 有界拷贝（deepcopy 族 F2-80 + 零校验族 F2-84 的统一入口）。

    客户端可控的 ``source_provenance`` 此前由 ``copy.deepcopy`` 整树复制：
    嵌套约 500 层即抛 ``RecursionError``（非 ValueError），穿出只捕
    ValueError 的端点 → 500。此处以深度上限 + 有限性清洗替代递归 deepcopy：
    - 容器递归重建成新的 dict/list（不再走 deepcopy 的递归实现）；
    - 深度达到 :data:`_MAX_PROVENANCE_DEPTH` 的子树整体替换为占位字符串——
      不拒绝输入（R2：客户端可能携带任意嵌套的来源信息，拒绝即新增严格性）；
    - 非有限浮点（nan/±inf）替换为占位字符串：裸 NaN 落库后 GET 回读 null，
      并在渲染层触发 ``allow_nan=False`` 500（同族 F2-84 口径）。
    """
    if isinstance(value, float) and not math.isfinite(value):
        return f"<non-finite {value!r}>"
    if isinstance(value, (dict, list, tuple)):
        if _depth >= _MAX_PROVENANCE_DEPTH:
            return _DEEP_PROVENANCE_PLACEHOLDER
        if isinstance(value, dict):
            return {key: _sanitize_provenance(item, _depth + 1)
                    for key, item in value.items()}
        cleaned = [_sanitize_provenance(item, _depth + 1) for item in value]
        return tuple(cleaned) if isinstance(value, tuple) else cleaned
    return value


def _copy_input(values: dict) -> dict:
    """有界结构化拷贝（替代递归 ``copy.deepcopy``，深/宽结构一律新容器）。

    provenance 走同一清洗器，故 ``source_provenance`` 的深度封顶与非有限
    清洗与其余字段口径一致（F2-80 / F2-84 同族同入口）。容器全部重建，
    返回结果不与调用方输入共享可变对象，与旧 deepcopy 的可变隔离语义等价。
    """
    return _sanitize_provenance(values)


def _finite_fsum(terms) -> float:  # noqa: ANN001
    """``math.fsum`` 的溢出家族统一入口：中间溢出转 ValueError（4xx 契约）。

    fsum 对「各项都有限、部分和却超出 float 范围」的输入抛
    ``OverflowError``（fsum 溢出族：F2-87/F4-106/F4-95 同型），端点只捕
    ValueError，于是溢出变成 500。常规输入仍走同一条 fsum，结果逐位不变。
    """
    try:
        return math.fsum(terms)
    except OverflowError as exc:
        raise ValueError("inputs imply a variance outside the representable range") from exc


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


def _integer(value: object, name: str, minimum: int) -> int:
    if type(value) is not int or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")
    try:
        finite = math.isfinite(float(value))
    except (OverflowError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite integer") from exc
    if not finite:
        raise ValueError(f"{name} must be a finite integer")
    return value


def _reciprocal_gap(events: int, participants: int) -> float:
    """``1/events - 1/participants`` with the F-04 cancellation guard.

    The float difference of reciprocals cancels catastrophically once the
    counts reach ~2**53: at ``participants = 2**63-1, events = 2**63-2`` it
    evaluates to 0.0 (exact value 1.1754943508222875e-38, so a positive
    variance is falsely rejected) and at 2**53 it doubles the true gap,
    silently inflating the SE by ~41%. The exact integer form
    ``(participants - events) / (events * participants)`` replaces it
    whenever the two disagree by more than 1e-6 relative — i.e. only in the
    cancellation regime (physically plausible trial sizes are orders of
    magnitude below it); regular trials keep the historical float path
    bit-for-bit. ``int / int`` is correctly rounded even for bignums.
    """
    naive = 1 / events - 1 / participants
    exact = (participants - events) / (events * participants)
    if naive > 0 and abs(naive - exact) <= 1e-6 * exact:
        return naive
    return exact


def _arm(values: object, outcome_type: str, name: str) -> dict:
    if not isinstance(values, dict):
        raise ValueError(f"{name} arm must be an object")
    outcome_fields = {"mean", "sd"} if outcome_type == "continuous" else {"events"}
    size_fields = {"participants", "mean_cluster_size"} & values.keys()
    required = {"clusters"} | outcome_fields
    if len(size_fields) != 1 or not required <= values.keys() or set(values) != required | size_fields:
        raise ValueError(
            f"{name} arm must contain clusters, exactly one of participants or mean_cluster_size, "
            f"and {', '.join(sorted(outcome_fields))}"
        )

    clusters = _integer(values["clusters"], f"{name} clusters", 1)
    if clusters < 2:
        raise ValueError(f"{name} arm requires at least 2 clusters")
    if "participants" in values:
        participants = _integer(values["participants"], f"{name} participants", 1)
        if participants < clusters:
            raise ValueError(f"{name} participants cannot be fewer than clusters")
    else:
        mean_cluster_size = _number(values["mean_cluster_size"], f"{name} mean_cluster_size")
        if mean_cluster_size < 1:
            raise ValueError(f"{name} mean_cluster_size must be at least 1")
        participants = clusters * mean_cluster_size
        if not math.isfinite(participants):
            raise ValueError(f"{name} participants must be finite")

    mean_cluster_size = participants / clusters
    if outcome_type == "continuous":
        mean = _number(values["mean"], f"{name} mean")
        sd = _number(values["sd"], f"{name} sd")
        if sd < 0:
            raise ValueError(f"{name} sd must be nonnegative")
        return {"clusters": clusters, "participants": participants,
                "mean_cluster_size": mean_cluster_size, "mean": mean, "sd": sd}

    events = _integer(values["events"], f"{name} events", 0)
    if events > participants:
        raise ValueError(f"{name} events cannot exceed participants")
    return {"clusters": clusters, "participants": participants,
            "mean_cluster_size": mean_cluster_size, "events": events}


def _exp(value: float, name: str) -> float:
    try:
        result = math.exp(value)
    except OverflowError as exc:
        raise ValueError(f"{name} is outside the finite range") from exc
    if result == 0 or not math.isfinite(result):
        raise ValueError(f"{name} is outside the finite positive range")
    return result


def calculate_cluster_trial_effect(values: dict) -> dict:
    """Calculate a design-effect-adjusted estimate from unadjusted two-arm summaries.

    ``values`` contains ``outcome_type`` (``continuous`` or ``binary``),
    ``measure`` (``MD`` or ``OR``/``RR``/``RD``), one shared ``icc``,
    ``analysis_status='unadjusted'``, and treatment/control arm summaries.
    Each arm supplies ``clusters`` and either ``participants`` or
    ``mean_cluster_size``. A continuous arm also supplies ``mean`` and ``sd``;
    a binary arm supplies ``events``. Optional source metadata belongs in
    ``source_provenance``.
    """
    if not isinstance(values, dict):
        raise ValueError("values must be an object")
    required = {"outcome_type", "measure", "icc", "analysis_status", "arms"}
    optional = {"source_provenance"}
    if not required <= values.keys() or set(values) - required - optional:
        raise ValueError("values must contain outcome_type, measure, icc, analysis_status and arms")
    if values["analysis_status"] != "unadjusted":
        raise ValueError("source analysis is already cluster-adjusted or not declared unadjusted")

    outcome_type, measure = values["outcome_type"], values["measure"]
    if not isinstance(outcome_type, str) or outcome_type not in {"continuous", "binary"}:
        raise ValueError("outcome_type must be 'continuous' or 'binary'")
    allowed = {"MD"} if outcome_type == "continuous" else {"OR", "RR", "RD"}
    if not isinstance(measure, str) or measure not in allowed:
        raise ValueError(f"measure must be one of {', '.join(sorted(allowed))}")
    icc = _number(values["icc"], "icc")
    if not 0 <= icc <= 1:
        raise ValueError("icc must be between 0 and 1 inclusive")

    arms = values["arms"]
    if not isinstance(arms, dict) or set(arms) != {"treatment", "control"}:
        raise ValueError("arms must contain exactly treatment and control")
    if "source_provenance" in values and not isinstance(values["source_provenance"], dict):
        raise ValueError("source_provenance must be an object")
    # F2-80（deepcopy 深度族）/ F2-84（source_provenance 零校验族）统一入口：
    # 有界拷贝在 deepcopy 之前完成深度封顶与非有限清洗。
    data = _copy_input(values)
    treatment = _arm(arms["treatment"], outcome_type, "treatment")
    control = _arm(arms["control"], outcome_type, "control")
    parsed_arms = {"treatment": treatment, "control": control}

    adjustment_arms = {}
    for name, arm in parsed_arms.items():
        design_effect = 1 + (arm["mean_cluster_size"] - 1) * icc
        effective_n = arm["participants"] / design_effect
        if (not math.isfinite(design_effect) or design_effect < 1
                or not math.isfinite(effective_n) or not 0 < effective_n <= arm["participants"]):
            raise ValueError(f"{name} design effect must imply a positive finite effective sample size")
        adjusted = {
            "clusters": arm["clusters"], "participants": arm["participants"],
            "mean_cluster_size": arm["mean_cluster_size"],
            "design_effect": design_effect, "effective_sample_size": effective_n,
        }
        if outcome_type == "binary":
            adjusted["effective_events"] = arm["events"] / design_effect
            adjusted["effective_non_events"] = (arm["participants"] - arm["events"]) / design_effect
        adjustment_arms[name] = adjusted

    if outcome_type == "continuous":
        estimate = treatment["mean"] - control["mean"]
        se = math.hypot(
            treatment["sd"] / math.sqrt(adjustment_arms["treatment"]["effective_sample_size"]),
            control["sd"] / math.sqrt(adjustment_arms["control"]["effective_sample_size"]),
        )
        se_scale = "natural"
        analysis_estimate = estimate
    else:
        risk_t = treatment["events"] / treatment["participants"]
        risk_c = control["events"] / control["participants"]
        if measure == "OR":
            if min(treatment["events"], control["events"]) == 0:
                raise ValueError("OR requires positive event counts in both arms")
            non_events_t = treatment["participants"] - treatment["events"]
            non_events_c = control["participants"] - control["events"]
            if non_events_t <= 0 or non_events_c <= 0:
                raise ValueError("OR requires positive event and non-event counts in both arms")
            analysis_estimate = (
                math.log(treatment["events"]) - math.log(non_events_t)
                - math.log(control["events"]) + math.log(non_events_c)
            )
            estimate = _exp(analysis_estimate, "OR estimate")
            variance = _finite_fsum((
                adjustment_arms["treatment"]["design_effect"] / treatment["events"],
                adjustment_arms["treatment"]["design_effect"] / non_events_t,
                adjustment_arms["control"]["design_effect"] / control["events"],
                adjustment_arms["control"]["design_effect"] / non_events_c,
            ))
            se_scale = "log"
        elif measure == "RR":
            if treatment["events"] == 0 or control["events"] == 0:
                raise ValueError("RR requires positive event counts in both arms")
            analysis_estimate = math.log(risk_t) - math.log(risk_c)
            estimate = _exp(analysis_estimate, "RR estimate")
            variance = _finite_fsum((
                adjustment_arms["treatment"]["design_effect"]
                * _reciprocal_gap(treatment["events"], treatment["participants"]),
                adjustment_arms["control"]["design_effect"]
                * _reciprocal_gap(control["events"], control["participants"]),
            ))
            se_scale = "log"
        else:
            estimate = risk_t - risk_c
            analysis_estimate = estimate
            variance = _finite_fsum((
                adjustment_arms["treatment"]["design_effect"] * risk_t * (1 - risk_t)
                / treatment["participants"],
                adjustment_arms["control"]["design_effect"] * risk_c * (1 - risk_c)
                / control["participants"],
            ))
            se_scale = "natural"
        se = math.sqrt(variance)

    if not math.isfinite(estimate) or not math.isfinite(analysis_estimate):
        raise ValueError("effect estimate must be finite")
    if not math.isfinite(se) or se <= 0:
        raise ValueError("inputs must imply a positive finite SE")

    return {
        "measure": measure,
        "estimate": estimate,
        "analysis_estimate": analysis_estimate,
        "se": se,
        "se_scale": se_scale,
        # data 已经过 _copy_input：全部容器重建、深度已封顶、非有限值已清洗，
        # 因此不再递归 deepcopy（那正是 F2-80 的崩溃点），也不会与被调用方
        # 输入共享可变对象。provenance 再单独拷一份，保持两个响应字段互不
        # 别名（深度已 ≤ _MAX_PROVENANCE_DEPTH，此处的 deepcopy 安全）。
        "input_data": data,
        "source_provenance": copy.deepcopy(data.get("source_provenance")),
        "entry_method": "cluster_trial_design_effect",
        "cluster_adjustment": {
            "method": "design_effect_effective_sample_size",
            "icc": icc,
            "arms": adjustment_arms,
        },
        "assumptions": [
            "The source summary is unadjusted for clustering.",
            "One ICC is shared across both arms; mean cluster size and design effect are calculated per arm.",
            "The design-effect formula treats clusters as equally sized within each arm.",
            "The normal-approximation SE does not adjust for few-cluster degrees of freedom or other design features.",
        ],
    }
