"""GRADE 证据质量评估（纯计算模块，零副作用）。

按 GRADE 框架（GRADE Handbook, Schünemann et al.）从 ReviewFlow 已有计算结果
（合成 I²/τ²/CI、RoB overall、发表偏倚检验 p 值）自动预填五个降级因子，
用户确认或覆盖后计算最终证据质量（高→中→低→极低）。

本模块只做算术与字典推导：
- 导入时不连数据库、不起服务、不读写文件（零副作用约定，SPEC §0.4）；
- 不调用任何外部 API（离线承诺，SPEC §0.1）；
- 持久化由调用方（custom_backend/main.py 的 Batch W7 段）经 coscreen/db.py
  的 _connect 自愈机制完成，本模块不感知 SQLite。
"""

from __future__ import annotations

import math

# ---------------------------------------------------------------------------
# 常量（SPEC §3.2）
# ---------------------------------------------------------------------------

#: 四级证据质量及中文标签。
GRADE_LEVELS = {
    "high": "高",
    "moderate": "中",
    "low": "低",
    "very_low": "极低",
}

#: 降级级别 → 中文标签。数值档位：none=0、serious=-1、very_serious=-2、
#: very_very_serious=-3。
DOWNGRADE_LABELS = {
    "none": "无降级",
    "serious": "严重(-1)",
    "very_serious": "非常严重(-2)",
    "very_very_serious": "极其严重(-3)",
}

#: 升级级别（仅观察性研究可用）→ 中文标签。
UPGRADE_LABELS = {
    "none": "无升级",
    "plus_one": "升级(+1)",
    "plus_two": "升级(+2)",
}

#: 质量等级的序数分（最终质量 = 初始 - 总降级 + 总升级，夹在 [1, 4]）。
_LEVEL_SCORE = {"very_low": 1, "low": 2, "moderate": 3, "high": 4}

#: 研究设计 → 初始质量。诊断准确性证据（QUADAS-2 体系）按 GRADE DTA 章
#  （Schünemann et al. 2008）从"高"起步；观察性研究从"低"起步。
INITIAL_DESIGNS = {"rct": "high", "observational": "low", "diagnostic": "high"}

#: 五个降级因子（评估结果 dict 的键）。
DOWNGRADE_FACTORS = ("rob", "inconsistency", "indirectness", "imprecision", "publication_bias")
#: 三个升级因子（仅 initial_design == "observational" 时可用）。
UPGRADE_FACTORS = ("large_effect", "dose_response", "plausible_confounding")

#: I² 阈值：≥50% 开始降级（保守），>75% 视为显著异质。
I2_SERIOUS_MIN = 50.0
I2_VERY_SERIOUS_MIN = 75.0
#: 发表偏倚检验的最小研究数：不足时不自动降级（检验功效不足）。
PUB_BIAS_MIN_STUDIES = 10
#: 发表偏倚检验 p 值阈值。
PUB_BIAS_P_THRESHOLD = 0.05
#: CI 上/下限比超过该值视为"极宽"。
CI_WIDTH_RATIO_MAX = 10.0
#: 最优信息量（OIS）代理：二分类结局事件总数阈值。
OIS_TOTAL_EVENTS_MIN = 300

#: RoB overall 判定中属于"高风险带"的取值（RoB 2 的 high、ROBINS-I 的
#: serious/critical、QUADAS-2 的 high）。
HIGH_RISK_JUDGEMENTS = frozenset({"high", "serious", "critical"})

#: RoB overall 单条判定的全部合法取值（RoB 2 ∪ ROBINS-I ∪ QUADAS-2）。
ROB_OVERALL_JUDGEMENTS = frozenset(
    {"low", "some concerns", "high", "moderate", "serious", "critical",
     "no information", "unclear"}
)

#: GRADE SoF 表的标准列（GDT 无臂风险变体）。
SOF_COLUMNS = (
    "Outcomes",
    "№ of participants (studies)",
    "Risk of bias",
    "Inconsistency",
    "Indirectness",
    "Imprecision",
    "Publication bias",
    "Overall certainty",
    "Comments",
)

#: 质量等级对应的 SoF 圆点符号。
CERTAINTY_SYMBOLS = {
    "high": "⊕⊕⊕⊕",
    "moderate": "⊕⊕⊕○",
    "low": "⊕⊕○○",
    "very_low": "⊕○○○",
}

_DOWNGRADE_STEPS = {"none": 0, "serious": 1, "very_serious": 2, "very_very_serious": 3}
_UPGRADE_STEPS = {"none": 0, "plus_one": 1, "plus_two": 2}


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------

def _finite_number(value: object, name: str) -> float | None:
    """宽松数值提取：非有限/非数值返回 None（自动建议缺信号时按"无降级"处理）。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        result = float(value)
    except (OverflowError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _require_dict(value: object, name: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{name} 必须是 dict 或 None")
    return value


def _validate_level(value: object, catalog: dict, name: str) -> str:
    if value not in catalog:
        raise ValueError(f"{name} 必须是 {sorted(catalog)} 之一")
    return value


def _ci_width_ratio(ci_low: float, ci_high: float) -> float | None:
    """CI 上/下限比。同号时计算比值；跨零（含无效线 0）时比值无定义返回 None。"""
    if ci_low > 0:
        return ci_high / ci_low if ci_low != 0 else None
    if ci_high < 0:
        return ci_low / ci_high
    return None


def _level_from_score(score: int) -> str:
    for level, bound in _LEVEL_SCORE.items():
        if score == bound:
            return level
    raise ValueError(f"非法质量分: {score}")


# ---------------------------------------------------------------------------
# 单因子自动建议
# ---------------------------------------------------------------------------

def suggest_rob_downgrade(rob_assessment: dict | None) -> tuple[str, dict]:
    """偏倚风险降级建议（SPEC §3.2 规则 1）。

    输入两种形态之一：
    - ``{"overall": "<判定>"}``：单条 RoB 评估（RoB 2/ROBINS-I/QUADAS-2 的
      overall 字段）。critical → very_serious；high / ROBINS-I serious →
      serious；其余（low/moderate/some concerns/unclear/no information）→ none。
    - ``{"overall_counts": {"<判定>": <研究数>, ...}}``：跨研究聚合。多数
      （≥50%）研究属高风险带 → very_serious；少数研究高风险 → serious；
      无 → none。
    - None 或无信号 → none（用户手动评估）。

    返回 ``(降级级别, 推导依据)``；未知判定值抛 ValueError（拒绝路径）。
    """
    empty = {"available": False, "reason": "无 RoB 数据，需用户手动评估"}
    if rob_assessment is None:
        return "none", empty
    source = _require_dict(rob_assessment, "rob_assessment")
    counts = source.get("overall_counts")
    if counts is not None:
        if not isinstance(counts, dict) or not counts:
            raise ValueError("overall_counts 必须是非空 dict（判定 → 研究数）")
        total = 0
        high_risk = 0
        for judgement, count in counts.items():
            if judgement not in ROB_OVERALL_JUDGEMENTS:
                raise ValueError(f"未知 RoB overall 判定: {judgement!r}")
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                raise ValueError("overall_counts 的研究数必须是非负整数")
            total += count
            if judgement in HIGH_RISK_JUDGEMENTS:
                high_risk += count
        if total == 0:
            return "none", empty
        ratio = high_risk / total
        if ratio >= 0.5:
            return "very_serious", {"available": True, "rule": "majority_high_risk",
                                    "high_risk_studies": high_risk, "n_studies": total}
        if high_risk > 0:
            return "serious", {"available": True, "rule": "some_high_risk_studies",
                               "high_risk_studies": high_risk, "n_studies": total}
        return "none", {"available": True, "rule": "no_high_risk_studies",
                        "high_risk_studies": 0, "n_studies": total}
    overall = source.get("overall")
    if overall is None:
        return "none", empty
    if overall not in ROB_OVERALL_JUDGEMENTS:
        raise ValueError(f"未知 RoB overall 判定: {overall!r}")
    if overall == "critical":
        return "very_serious", {"available": True, "rule": "rob_overall_critical", "overall": overall}
    if overall in ("high", "serious"):
        return "serious", {"available": True, "rule": "rob_overall_high", "overall": overall}
    return "none", {"available": True, "rule": "rob_overall_low_risk", "overall": overall}


def suggest_inconsistency_downgrade(heterogeneity: dict | None) -> tuple[str, dict]:
    """不一致性降级建议（SPEC §3.2 规则 2）。

    - I² > 75%（τ² 检验显著时另行标注）或 I² ∈ [50%, 75%] → serious（保守）；
    - I² < 50% 或无数据 → none。

    异质性显著性从 ``tau2_p`` 或 ``q_p``（< 0.05）读取；缺失时仅作信号标注，
    不影响档位（两个 I² 频段都落在 serious 档）。
    """
    empty = {"available": False, "reason": "无合成结果，需用户手动评估"}
    if heterogeneity is None:
        return "none", empty
    source = _require_dict(heterogeneity, "heterogeneity")
    i2 = _finite_number(source.get("i2_percent"), "i2_percent")
    if i2 is None:
        return "none", empty
    signal: dict = {"available": True, "i2_percent": i2}
    if i2 < I2_SERIOUS_MIN:
        signal["rule"] = "i2_below_50"
        return "none", signal
    p_value = next(
        (_finite_number(source.get(key), key) for key in ("tau2_p", "q_p")
         if _finite_number(source.get(key), key) is not None),
        None,
    )
    if p_value is not None:
        signal["heterogeneity_p_value"] = p_value
        signal["tau2_significant"] = p_value < 0.05
    signal["rule"] = "i2_above_75" if i2 > I2_VERY_SERIOUS_MIN else "i2_50_75_conservative"
    return "serious", signal


def suggest_imprecision_downgrade(imprecision: dict | None) -> tuple[str, dict]:
    """不精确性降级建议（SPEC §3.2 规则 4）。

    三条独立规则，任一命中即 serious：
    1. CI 包含无效线（null_value）且样本量小——事件总数未知同样视为
       OIS 未证实（保守，无法排除小样本）；
    2. CI 极宽：同号上下限之比 > 10；
    3. 事件总数 < 300（OIS 未达到）。

    无 CI/事件数据 → none（用户手动评估）。
    """
    empty = {"available": False, "reason": "无置信区间/事件数据，需用户手动评估"}
    if imprecision is None:
        return "none", empty
    source = _require_dict(imprecision, "imprecision")
    ci_low = _finite_number(source.get("ci_low"), "ci_low")
    ci_high = _finite_number(source.get("ci_high"), "ci_high")
    null_value = _finite_number(source.get("null_value"), "null_value")
    if null_value is None:
        null_value = 0.0
    raw_events = source.get("total_events")
    if raw_events is None:
        total_events = None
    elif isinstance(raw_events, bool) or not isinstance(raw_events, int) or raw_events < 0:
        raise ValueError("total_events 必须是非负整数")
    else:
        total_events = raw_events

    if ci_low is None or ci_high is None:
        signal = dict(empty)
        if total_events is not None:
            # 仅有事件数据：规则 3 仍可判定。
            if total_events < OIS_TOTAL_EVENTS_MIN:
                return ("serious", {"available": True, "rule": "events_below_ois",
                                    "total_events": total_events,
                                    "ois_threshold": OIS_TOTAL_EVENTS_MIN})
            return ("none", {"available": True, "rule": "events_meet_ois",
                             "total_events": total_events,
                             "ois_threshold": OIS_TOTAL_EVENTS_MIN})
        return "none", signal

    signal = {"available": True, "ci_low": ci_low, "ci_high": ci_high, "null_value": null_value}
    reasons: list[str] = []
    width_ratio = _ci_width_ratio(ci_low, ci_high)
    if width_ratio is not None:
        signal["ci_width_ratio"] = width_ratio
        if width_ratio > CI_WIDTH_RATIO_MAX:
            reasons.append("ci_too_wide")
    includes_null = ci_low <= null_value <= ci_high
    signal["ci_includes_null"] = includes_null
    ois_unmet = total_events is None or total_events < OIS_TOTAL_EVENTS_MIN
    if total_events is not None:
        signal["total_events"] = total_events
        signal["ois_threshold"] = OIS_TOTAL_EVENTS_MIN
        if total_events < OIS_TOTAL_EVENTS_MIN:
            reasons.append("events_below_ois")
    if includes_null and ois_unmet:
        reasons.append("ci_includes_null_and_ois_unmet")
    if reasons:
        signal["rule"] = reasons
        return "serious", signal
    signal["rule"] = "no_imprecision_signal"
    return "none", signal


def suggest_publication_bias_downgrade(publication_bias: dict | None) -> tuple[str, dict]:
    """发表偏倚降级建议（SPEC §3.2 规则 5）。

    - 研究数 < 10 → none（检验功效不足，不自动降级）；
    - Egger/Begg 检验 p < 0.05 → serious；
    - 无数据 → none。
    """
    empty = {"available": False, "reason": "无发表偏倚检验结果，需用户手动评估"}
    if publication_bias is None:
        return "none", empty
    source = _require_dict(publication_bias, "publication_bias")
    p_value = _finite_number(source.get("p_value"), "p_value")
    n_studies = source.get("n_studies")
    if n_studies is not None and (isinstance(n_studies, bool) or not isinstance(n_studies, int)):
        raise ValueError("n_studies 必须是整数")
    if n_studies is not None and n_studies < PUB_BIAS_MIN_STUDIES:
        return ("none", {"available": True, "rule": "insufficient_studies_no_auto_downgrade",
                         "n_studies": n_studies, "minimum_studies": PUB_BIAS_MIN_STUDIES,
                         "p_value": p_value})
    if p_value is not None and p_value < PUB_BIAS_P_THRESHOLD:
        return ("serious", {"available": True, "rule": "publication_bias_test_significant",
                            "p_value": p_value, "p_threshold": PUB_BIAS_P_THRESHOLD,
                            "n_studies": n_studies})
    if p_value is None:
        return "none", empty
    return ("none", {"available": True, "rule": "publication_bias_test_not_significant",
                     "p_value": p_value, "p_threshold": PUB_BIAS_P_THRESHOLD,
                     "n_studies": n_studies})


# ---------------------------------------------------------------------------
# 质量计算
# ---------------------------------------------------------------------------

def final_quality(initial_design: str, downgrades: dict, upgrades: dict | None = None) -> dict:
    """最终质量 = 初始质量 − 总降级数 + 总升级数（仅观察性研究可升级）。

    分值夹在 [1, 4]（极低…高），越界时返回 warnings 而不是报错。
    """
    if initial_design not in INITIAL_DESIGNS:
        raise ValueError(f"initial_design 必须是 {sorted(INITIAL_DESIGNS)} 之一")
    upgrade_allowed = initial_design == "observational"
    resolved_upgrades = dict.fromkeys(UPGRADE_FACTORS, "none")
    if upgrades:
        for factor, level in upgrades.items():
            if factor not in UPGRADE_FACTORS:
                raise ValueError(f"未知升级因子: {factor!r}")
            if not upgrade_allowed and level != "none":
                raise ValueError("升级因子仅观察性研究可用")
            resolved_upgrades[factor] = _validate_level(level, _UPGRADE_STEPS, factor)
    resolved_downgrades = {}
    for factor in DOWNGRADE_FACTORS:
        if factor not in downgrades:
            raise ValueError(f"缺少降级因子: {factor}")
        resolved_downgrades[factor] = _validate_level(
            downgrades[factor], _DOWNGRADE_STEPS, factor)
    total_downgrade = sum(_DOWNGRADE_STEPS[level] for level in resolved_downgrades.values())
    total_upgrade = sum(_UPGRADE_STEPS[level] for level in resolved_upgrades.values())
    initial_level = INITIAL_DESIGNS[initial_design]
    raw_score = _LEVEL_SCORE[initial_level] - total_downgrade + total_upgrade
    warnings: list[str] = []
    score = min(4, max(1, raw_score))
    if raw_score < 1:
        warnings.append(f"降级幅度超过下限，最终质量按极低处理（原始分 {raw_score}）")
    if raw_score > 4:
        warnings.append(f"升级幅度超过上限，最终质量按高处理（原始分 {raw_score}）")
    level = _level_from_score(score)
    return {"initial_quality": initial_level, "total_downgrade_steps": total_downgrade,
            "total_upgrade_steps": total_upgrade, "final_quality": level,
            "final_quality_label": GRADE_LEVELS[level], "warnings": warnings,
            "upgrade_allowed": upgrade_allowed}


def assess_grade(
    initial_design: str,
    rob_assessment: dict | None = None,
    heterogeneity: dict | None = None,
    imprecision: dict | None = None,
    publication_bias: dict | None = None,
    indirectness: str = "none",
    user_overrides: dict | None = None,
) -> dict:
    """GRADE 评估主入口（方法学锚：GRADE Handbook, Schünemann et al.）。

    自动预填五个降级因子（间接性除外——GRADE 中唯一无法从数据推导的维度，
    完全由用户选择）；``user_overrides`` 覆盖自动建议值并记录覆盖对照。

    返回 dict 含：初始/最终质量（键与中文标签）、各因子最终值与自动建议值、
    总降级/升级步数、每个因子的推导依据（signal_sources）、覆盖对照
    （overrides_applied）与 warnings。
    """
    if initial_design not in INITIAL_DESIGNS:
        raise ValueError(f"initial_design 必须是 {sorted(INITIAL_DESIGNS)} 之一")
    indirectness = _validate_level(indirectness, _DOWNGRADE_STEPS, "indirectness")
    if user_overrides is not None:
        user_overrides = _require_dict(user_overrides, "user_overrides")

    suggested = {
        "rob": suggest_rob_downgrade(rob_assessment),
        "inconsistency": suggest_inconsistency_downgrade(heterogeneity),
        "imprecision": suggest_imprecision_downgrade(imprecision),
        "publication_bias": suggest_publication_bias_downgrade(publication_bias),
    }
    signal_sources = {factor: signal for factor, (_, signal) in suggested.items()}
    signal_sources["indirectness"] = {
        "available": False,
        "reason": "间接性无法从数据自动推导，完全依赖用户判断",
        "user_selected": indirectness,
    }

    factors = {factor: level for factor, (level, _) in suggested.items()}
    factors["indirectness"] = indirectness
    upgrades = dict.fromkeys(UPGRADE_FACTORS, "none")

    overrides_applied: dict[str, dict] = {}
    if user_overrides:
        for factor, level in user_overrides.items():
            if factor in DOWNGRADE_FACTORS:
                factors[factor] = _validate_level(level, _DOWNGRADE_STEPS, factor)
            elif factor in UPGRADE_FACTORS:
                if initial_design != "observational" and level != "none":
                    raise ValueError("升级因子仅观察性研究可用")
                upgrades[factor] = _validate_level(level, _UPGRADE_STEPS, factor)
            else:
                raise ValueError(
                    f"未知覆盖因子: {factor!r}，必须属于 {DOWNGRADE_FACTORS + UPGRADE_FACTORS}")
            overrides_applied[factor] = {"auto": suggested[factor][0] if factor in suggested
                                         else "none",
                                         "user": level}

    outcome = final_quality(initial_design, factors, upgrades)
    return {
        "initial_design": initial_design,
        "initial_quality": outcome["initial_quality"],
        "initial_quality_label": GRADE_LEVELS[outcome["initial_quality"]],
        "downgrades": factors,
        "upgrades": upgrades,
        "auto_suggested": {factor: (level if factor != "indirectness" else "none")
                           for factor, (level, _) in suggested.items()},
        "signal_sources": signal_sources,
        "overrides_applied": overrides_applied,
        "total_downgrade_steps": outcome["total_downgrade_steps"],
        "total_upgrade_steps": outcome["total_upgrade_steps"],
        "upgrade_allowed": outcome["upgrade_allowed"],
        "final_quality": outcome["final_quality"],
        "final_quality_label": outcome["final_quality_label"],
        "warnings": outcome["warnings"],
    }


# ---------------------------------------------------------------------------
# Summary of Findings（SoF）表
# ---------------------------------------------------------------------------

def build_sof_table(assessments: list[dict], *, title: str = "GRADE 证据概要（Summary of Findings）") -> dict:
    """把 GRADE 评估结果列表组装成标准 SoF 表结构（纯数据，供前端/导出渲染）。

    每个元素接受 assess_grade 的返回值（含 downgrades/upgrades/final_quality），
    或至少含 outcome、downgrades、final_quality 的扁平行；可选 n_participants、
    n_studies、comparison、timepoint、override_notes（作为 Comments 列）。
    """
    if not isinstance(assessments, list):
        raise ValueError("assessments 必须是列表")
    rows = []
    for item in assessments:
        if not isinstance(item, dict):
            raise ValueError("每个评估必须是 dict")
        downgrades = item.get("downgrades")
        if not isinstance(downgrades, dict):
            downgrades = {factor: item.get(factor, "none") for factor in DOWNGRADE_FACTORS}
        for factor in DOWNGRADE_FACTORS:
            _validate_level(downgrades.get(factor, "none"), _DOWNGRADE_STEPS, factor)
        level = item.get("final_quality")
        if level not in GRADE_LEVELS:
            # 未给最终质量时按因子现算（升级因子仅观察性可用）。
            computed = final_quality(item.get("initial_design", "rct"), downgrades,
                                     item.get("upgrades") or None)
            level = computed["final_quality"]
        label_parts = [str(item.get("outcome", "")).strip() or "（未命名结局）"]
        timepoint = str(item.get("timepoint", "")).strip()
        if timepoint:
            label_parts.append(f"（{timepoint}）")
        participants = item.get("n_participants")
        n_studies = item.get("n_studies")
        cells = []
        if participants is not None:
            cells.append(str(participants))
        if n_studies is not None:
            cells.append(f"（{n_studies} 项研究）" if participants is not None else f"{n_studies} 项研究")
        rows.append([
            "".join(label_parts),
            " ".join(cells) if cells else "—",
            DOWNGRADE_LABELS[downgrades.get("rob", "none")],
            DOWNGRADE_LABELS[downgrades.get("inconsistency", "none")],
            DOWNGRADE_LABELS[downgrades.get("indirectness", "none")],
            DOWNGRADE_LABELS[downgrades.get("imprecision", "none")],
            DOWNGRADE_LABELS[downgrades.get("publication_bias", "none")],
            f"{GRADE_LEVELS[level]} {CERTAINTY_SYMBOLS[level]}",
            str(item.get("override_notes", "") or item.get("comments", "") or "").strip(),
        ])
    return {
        "table_type": "grade_sof_table",
        "title": title,
        "columns": list(SOF_COLUMNS),
        "rows": rows,
        "certainty_symbols": dict(CERTAINTY_SYMBOLS),
        "downgrade_labels": dict(DOWNGRADE_LABELS),
        "upgrade_labels": dict(UPGRADE_LABELS),
        "legend": [
            "GRADE Working Group 证据质量：高 = 进一步研究不太可能改变置信度；"
            "中 = 进一步研究可能对置信度有重要影响；低 = 进一步研究很可能对置信度有重要影响；"
            "极低 = 任何估计都很不确定。",
            "降级因子：无降级 / 严重(-1) / 非常严重(-2) / 极其严重(-3)；"
            "升级因子（仅观察性证据）：大效应、剂量反应、合理混杂方向，各 +1 或 +2。",
            "最终质量 = 初始质量 − 总降级步数 + 总升级步数（RCT 与诊断准确性证据从高起步，"
            "观察性研究从低起步）。",
        ],
    }
