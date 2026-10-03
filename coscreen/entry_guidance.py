"""Route a reported-data shape to ranked effect-entry paths.

A pure rule engine, zero IO: the caller describes what a study report
actually contains (arm-level counts, medians of survival, an HR with CI, ...)
and receives every supported entry path for that shape, ordered by the
Tierney et al. (2007) precision ladder so the front end can walk the user
through the best available option first (the P4 guided-entry flow).

The mapping is static and derived from the entry formats that exist in this
codebase (the ``analysis/effects`` manual route, ``analysis/arm-effects``,
``analysis/format-effects`` and the DTA / dose-curve save routes). Nothing
here calls those modules; every path only records where the data would go.
"""

from __future__ import annotations

import copy

__all__ = ["DATA_SHAPES", "SHAPE_LABELS", "PRECISION_LADDER", "recommend_entry_routes"]

# Tierney et al. (2007) ladder, condensed to five operational ranks:
# 1  directly reported per-arm / per-table summaries (counts, means, SDs, n)
# 2  directly reported contrast summaries (O-E/V, effect estimate + SE/CI)
# 3  reported effect estimate with only a p value, or test statistics
# 4  summaries that require design adjustments (design effects, correlations
#    recovered from paired tables)
# 5  summaries that require re-deriving the effect from indirect quantities
#    (median survival, baseline risk assumptions)
PRECISION_LADDER = {
    1: "directly reported arm/table summaries",
    2: "directly reported contrast summaries (estimate+CI/SE, O-E/V)",
    3: "test statistics or p-derived estimates",
    4: "design-adjusted summaries (design effect, converted correlation)",
    5: "indirectly derived estimates (median survival, assumed distributions)",
}

_L = "coscreen.review_analysis"
_BIN_COUNTS = {"events_t", "total_t", "events_c", "total_c"}
_CONT_ARMS = {"n_t", "mean_t", "sd_t", "n_c", "mean_c", "sd_c"}

#: Every shape code the guided-entry flow may offer, with a UI label.
SHAPE_LABELS: dict[str, str] = {
    "arm_events_totals": "两臂事件数/人数（二分类臂级）",
    "arm_mean_sd_n": "两臂均值/SD/n（连续臂级）",
    "independent_2x2": "独立两组 2×2 表",
    "paired_2x2": "配对 2×2 表（同一受试者两次测量）",
    "paired_mean_sd": "配对均值/变化分数（含前后测、交叉）",
    "crossover_2x2_md": "2×2 交叉设计均差（两序列）",
    "single_mean_sd_n": "单组均值/SD/n",
    "single_proportion_events_n": "单组比例（事件数/总数）",
    "single_rate_events_n": "单组率（事件数/人时）",
    "single_direct_interval": "单组估计±SE/CI（直录）",
    "test_statistic": "检验统计量（t/F/p/d + n）",
    "correlation_r_n": "相关系数 r 与 n",
    "effect_ci": "效应估计±CI（通用）",
    "effect_p": "效应估计±p 值（通用）",
    "effect_se": "效应估计+SE（直录）",
    "hazard_ratio_ci": "HR±CI（生存）",
    "median_survival": "中位生存时间（推导 HR）",
    "median_summary": "Median with range or IQR (estimate arm mean/SD)",
    "oe_v": "log-rank O−E 与 V",
    "logrank_p": "log-rank p 值",
    "rate_ratio_events_time": "两臂事件数/人时（发病密度）",
    "zero_cell_binary": "含零格的两臂计数",
    "cluster_design_effect": "整群随机设计（设计效应/ICC 调整）",
    "dta_2x2": "诊断准确性 2×2（TP/FP/FN/TN）",
    "dose_arms": "剂量-反应对比（含协方差）",
}
DATA_SHAPES = tuple(SHAPE_LABELS)


def _path(shape: str, module: str, function: str, required: set[str] | str,
          rank: int, measure: str, cautions: list[str]) -> dict:
    return {
        "shape": shape,
        "entry_module": module,
        "entry_function": function,
        "required_fields": sorted(required) if isinstance(required, set) else required,
        "precision_rank": rank,
        "precision_label": PRECISION_LADDER[rank],
        "produces_measure": measure,
        "cautions": cautions,
    }


#: shape code -> candidate entry paths, best rank first within each shape.
_SHAPE_PATHS: dict[str, list[dict]] = {
    "median_summary": [
        _path("median_summary", "coscreen.summary_stats_estimation",
              "estimate_from_median_range / estimate_from_median_iqr",
              "Per arm: n, median and minimum/maximum or Q1/Q3; explicit method; source table/page and units",
              5, "MD / SMD (estimated summaries)",
              ["Formula-estimated means and SDs are not observed data; reconstruction uncertainty is omitted from ordinary effect SEs.",
               "Choose Hozo, Wan or Luo explicitly; Luo mean uses Wan SD. Compare analyses with and without estimated studies and cite each method used."]),
    ],
    "arm_events_totals": [
        _path("arm_events_totals", _L, "save_arm_effect (binary arms via binary_effect)",
              _BIN_COUNTS, 1, "RR / OR / RD",
              ["零格需显式选择连续性校正或改走 zero_cell_binary 路径",
               "RR/OR 的 SE 在对数尺度上合成"]),
        _path("arm_events_totals", "coscreen.two_group_rd_interval",
              "(RD alternative interval from counts)", _BIN_COUNTS, 1, "RD",
              ["RD 的区间方法与本库逆方差主路径不同，录入前核对一致口径"]),
    ],
    "independent_2x2": [
        _path("independent_2x2", _L, "save_arm_effect (binary arms via binary_effect)",
              _BIN_COUNTS, 1, "RR / OR / RD",
              ["与 arm_events_totals 等价：4 个计数即两臂事件/人数"]),
        _path("independent_2x2", "coscreen.binary_correlation_conversion",
              "calculate_binary_correlation_conversion (phi / r-equivalent)",
              _BIN_COUNTS, 4, "PHI / R_EQUIV_APPROX",
              ["把计数转换为相关系数仅用于与相关型结局合并，不能当作效应量合成的替代"]),
    ],
    "paired_2x2": [
        _path("paired_2x2", "coscreen.paired_binary_effect",
              "paired_binary_effect (matched-pair OR / paired RD)",
              {"a", "b", "c", "d", "direction"}, 1, "PAIRED_OR / PAIRED_RD",
              ["使用不一致对 (b, c)；某侧零不一致对时必须显式选择校正",
               "配对效应量与独立两臂效应量属于不同族，禁止混池"]),
    ],
    "arm_mean_sd_n": [
        _path("arm_mean_sd_n", _L, "save_arm_effect (continuous arms via continuous_effect)",
              _CONT_ARMS, 1, "MD / SMD",
              ["MD 要求两臂同一测量工具；跨工具差异考虑 SMD",
               "SMD 采用 Hedges g（含小样本校正）"]),
        _path("arm_mean_sd_n", "coscreen.ratio_of_means_effect",
              "calculate_ratio_of_means", _CONT_ARMS | {"direction"}, 1, "LOG_ROM",
              ["均值的对数比适合正偏态或比率型结局；当前为录入格式，合成接线由注册表标注"]),
        _path("arm_mean_sd_n", "coscreen.glass_delta_effect",
              "calculate_glass_delta_effect",
              {"mean_t", "mean_c", "sd_c", "n_t", "n_c", "direction"}, 1, "SMD (Glass delta)",
              ["用对照组 SD 标准化，适合两臂 SD 差异悬殊（如天花板/地板效应）"]),
    ],
    "paired_mean_sd": [
        _path("paired_mean_sd", "coscreen.dependent_effect_formats",
              "calculate_dependent_effect (paired_md_sd_diff / paired_md_correlation / "
              "parallel_change_sd / parallel_change_correlations)",
              "按具体 format_name 而定（n、两时点/两处理均值、SD 或相关系数及来源）", 1, "MD",
              ["相关系数来源必须显式登记（correlation_source + note）",
               "配对 MD 与独立两臂 MD 混合需要协方差/多臂模块，不能直接混池"]),
    ],
    "crossover_2x2_md": [
        _path("crossover_2x2_md", "coscreen.crossover_effects",
              "calculate_crossover_effect (format crossover_2x2_md)",
              {"n_ab", "mean_diff_ab", "sd_diff_ab", "n_ba", "mean_diff_ba", "sd_diff_ba",
               "design_note"}, 1, "MD",
              ["不调整期效应/残留效应；design_note 必须记录序列信息"]),
    ],
    "single_mean_sd_n": [
        _path("single_mean_sd_n", "coscreen.single_group_formats",
              "calculate_single_group_effect (single_mean_sd_n)",
              {"n", "mean", "sd"}, 1, "MEAN",
              ["单组均值没有自然零假设，敏感性视图的方向标记对该 measure 关闭"]),
    ],
    "single_proportion_events_n": [
        _path("single_proportion_events_n", "coscreen.single_group_formats",
              "calculate_single_group_effect (single_proportion_events_n)",
              {"events", "total"}, 1, "LOGIT_PROP",
              ["0 或全部事件时按模块内置校正处理，结果以 logit 尺度合成"]),
    ],
    "single_rate_events_n": [
        _path("single_rate_events_n", "coscreen.single_group_formats",
              "calculate_single_group_effect (single_rate_events_time)",
              {"events", "person_time"}, 1, "LOG_RATE",
              ["假设 Poisson 计数独立、人时已知且无零事件"]),
    ],
    "single_direct_interval": [
        _path("single_direct_interval", "coscreen.single_group_direct",
              "calculate_single_group_direct_effect (single_mean_direct / "
              "single_proportion_direct / single_rate_direct)",
              {"estimate", "precision_scale", "SE 或 CI（按格式）"}, 1,
              "MEAN / LOGIT_PROP / LOG_RATE",
              ["precision_scale 必须显式声明 SE/CI 所在尺度，避免双重变换"]),
    ],
    "test_statistic": [
        _path("test_statistic", "coscreen.test_statistic_effect_formats",
              "calculate_test_statistic_effect (independent_t_n / independent_d_n / "
              "independent_p_n / independent_f_n)",
              "按 format_name 而定（t/d/p/F + n_t, n_c 等）", 3, "SMD (Hedges g)",
              ["由检验统计量反推效应是 Tierney 阶梯的低精度档",
               "p 值路径需要方向（direction）防止符号错误"]),
    ],
    "correlation_r_n": [
        _path("correlation_r_n", "coscreen.effect_size_formats",
              "calculate_effect (correlation_r_n)", {"r", "n"}, 1, "FISHER_Z",
              ["以 Fisher z 合成，展示时 tanh 反变换回 r"]),
    ],
    "effect_ci": [
        _path("effect_ci", "coscreen.effect_size_formats",
              "calculate_effect (generic_ci)",
              {"measure", "estimate", "ci_low", "ci_high", "confidence_level", "scale"}, 2,
              "RR / OR / HR / RATE_RATIO / RD / MD / SMD / FISHER_Z",
              ["比值型 CI 一律按对数尺度正态近似反推 SE；scale 必须显式"]),
    ],
    "effect_p": [
        _path("effect_p", "coscreen.effect_size_formats",
              "calculate_effect (generic_wald_p)",
              {"measure", "estimate", "p", "scale"}, 3,
              "RR / OR / HR / RATE_RATIO / RD / MD / SMD / FISHER_Z",
              ["由精确 p 值反推 SE 假设 Wald z 分布；估计等于零时不可识别"]),
    ],
    "effect_se": [
        _path("effect_se", _L, "save_effect (manual estimate + se)",
              {"measure", "estimate", "se"}, 2,
              "RR / OR / RD / MD / SMD / HR / RATE_RATIO / FISHER_Z / MEAN / LOGIT_PROP / LOG_RATE",
              ["直录 SE 绕过任何换算，录入者需对尺度负责（比值型 SE 在对数尺度）"]),
    ],
    "hazard_ratio_ci": [
        _path("hazard_ratio_ci", "coscreen.effect_size_formats",
              "calculate_effect (hazard_ratio_ci)",
              {"estimate", "ci_low", "ci_high", "confidence_level"}, 2, "HR",
              ["CI 按对数尺度正态近似反推 SE"]),
    ],
    "median_survival": [
        _path("median_survival", "coscreen.survival_summary_conversion",
              "calculate_hr_from_median_survival",
              "两臂中位生存与随访/时间单位及来源溯源字段", 5, "HR",
              ["中位生存推导 HR 依赖指数假设与删失模式，是阶梯最低档；仅在无 HR±CI / O−E/V 时使用",
               "必须记录时间单位与来源溯源，防止单位错配"]),
    ],
    "oe_v": [
        _path("oe_v", "coscreen.survival_logrank_effect",
              "calculate_survival_logrank_effect (survival_logrank_oe_v)",
              {"oe", "variance", "oe_definition", "direction"}, 1, "HR",
              ["oe_definition 与 direction 必须逐字匹配约定，防止符号反转",
               "O−E/V 是试验内已汇总的精确对比，属可直接合成的高精度档"]),
    ],
    "logrank_p": [],
    "rate_ratio_events_time": [
        _path("rate_ratio_events_time", "coscreen.effect_size_formats",
              "calculate_effect (rate_ratio_events_time)",
              {"events_t", "time_t", "events_c", "time_c"}, 1, "RATE_RATIO",
              ["要求两臂事件数均为正（零事件不适用）"]),
        _path("rate_ratio_events_time", "coscreen.rate_ratio_exact_interval",
              "rate_ratio_interval (exact / mid-p)",
              {"events_t", "person_time_t", "events_c", "person_time_c", "method"}, 1,
              "RATE_RATIO (exact CI)",
              ["method（exact / mid-p）必须显式；精确区间路径用于极小样本，录入前核对 CI 口径"]),
    ],
    "zero_cell_binary": [
        _path("zero_cell_binary", "coscreen.zero_cell_effects",
              "calculate_zero_cell_effect (format zero_cell_binary)",
              {"events_t", "total_t", "events_c", "total_c", "measure", "correction"}, 2,
              "RR / OR / RD",
              ["连续性校正必须显式选择；双零/全事件研究对相对效应无信息，应保存原始计数",
               "校正后的 SE 已非纯计数似然，比直接臂级计数低一档"]),
    ],
    "cluster_design_effect": [
        _path("cluster_design_effect", "coscreen.cluster_trial_effects",
              "calculate_cluster_trial_effect (cluster_trial_design_effect)",
              "clusters、icc(+source)、analysis_status、arm 级 mean/sd 或 events", 4,
              "按 outcome_type：MD / SMD / RR / OR / RD",
              ["设计效应调整降低精度（阶梯第 4 档）；ICC 来源必须登记",
               "analysis_status 必须显式，防止重复调整"]),
        _path("cluster_design_effect", "coscreen.cluster_trial_variable_size",
              "calculate_cluster_trial_variable_size_effect (cluster_trial_variable_size_design_effect)",
              "outcome_type、measure、icc(+source)、analysis_status、arms", 4,
              "按 outcome_type：MD / SMD / RR / OR / RD",
              ["变大小整群用精确权重；同样不得与未调整臂级数据混池"]),
    ],
    "dta_2x2": [
        _path("dta_2x2", "coscreen.dta_analysis",
              "save_result (binomial bivariate model, separate DTA flow)",
              {"index_test", "target_condition", "threshold", "reference_standard",
               "tp", "fp", "fn", "tn"}, 1, "敏感度/特异度/DOR（DTA 专用流程）",
              ["DTA 走独立的 binomial-normal 流程，不进入逆方差效应表；不要把 DOR 存入 RR/OR 族"]),
    ],
    "dose_arms": [
        _path("dose_arms", "coscreen.dose_analysis",
              "save_curve (dose-response contrasts + covariance)",
              {"measure", "reference_dose", "dose_unit", "contrasts", "covariance"}, 1,
              "剂量-反应对比（专用曲线流程）",
              ["各剂量对比需完整协方差矩阵；合成走专用剂量流程，不入通用效应表"]),
    ],
}

#: advice emitted when several selected shapes interact.
_COMBINATION_RULES: list[dict] = [
    {
        "if": {"arm_mean_sd_n"},
        "shapes": ["arm_mean_sd_n"],
        "note": "臂级连续数据有多条并列路径：MD（同工具）/ SMD（跨工具，Hedges g）/ LOG_ROM（比率假设）/ "
                "Glass Δ（对照 SD 悬殊）——按尺度假设逐研究选定一种，并在 protocol 记录选择规则。",
    },
    {
        "if": {"arm_events_totals", "independent_2x2"},
        "shapes": ["arm_events_totals", "independent_2x2"],
        "note": "两臂计数可产生 RR / OR / RD 多条并列路径：事件少见优先 OR/RR（对数尺度），"
                "绝对差优先 RD；零格研究走 zero_cell_binary 路径并登记校正。",
    },
    {
        "if": {"paired_mean_sd", "arm_mean_sd_n"},
        "shapes": ["paired_mean_sd", "arm_mean_sd_n"],
        "note": "配对/变化分数 MD 与独立两臂 MD 不能直接混入同一逆方差合成（相关性未建模）；"
                "如需合并，使用 dependent_synthesis 的多臂/相关流程。",
    },
    {
        "if": {"paired_2x2", "arm_events_totals"},
        "shapes": ["paired_2x2", "arm_events_totals"],
        "note": "配对 2×2（PAIRED_OR）与独立两臂（RR/OR）属不同效应族，注册表会拦截混池；"
                "分别合成后再做叙述性比较。",
    },
    {
        "if": {"hazard_ratio_ci", "oe_v"},
        "shapes": ["hazard_ratio_ci", "oe_v"],
        "note": "同一 HR 族内可混录入方法（注册表按 measure 校验），但逐研究选精度最高的可用形状，"
                "并在录入字段中保留 entry_method 以便审计。",
    },
    {
        "if": {"median_survival", "hazard_ratio_ci"},
        "shapes": ["median_survival", "hazard_ratio_ci"],
        "note": "同一研究同时报告 HR±CI 与中位生存时，用 HR±CI（阶梯第 2 档）而非中位生存推导（第 5 档）。",
    },
    {
        "if": {"logrank_p"},
        "shapes": ["logrank_p"],
        "note": "log-rank p 不能用于仅接受 Wald p 的 SE 换算。请查找同一研究的 HR±CI 或 O−E/V；"
                "仅有 log-rank p 时无法据此录入可合并效应。",
    },
    {
        "if": {"cluster_design_effect", "arm_mean_sd_n"},
        "shapes": ["cluster_design_effect", "arm_mean_sd_n"],
        "note": "整群研究必须走设计效应路径，不能与未调整的臂级数据混池（有效样本量被高估）。",
    },
    {
        "if": {"zero_cell_binary", "arm_events_totals"},
        "shapes": ["zero_cell_binary", "arm_events_totals"],
        "note": "含零格的研究走 zero_cell_binary 显式校正路径，其余研究保持原始计数路径；"
                "两者 measure 相同可混池，但 entry_method 会留在结果里供敏感性核查。",
    },
]


def recommend_entry_routes(available: list[str]) -> dict:
    """Rank every supported entry path for the shapes the user says they have.

    ``available`` is a list of shape codes from ``DATA_SHAPES`` (the guided
    entry checklist: arm-level counts, arm means/SD/n, median survival,
    HR+CI, O-E/V, log-rank p, effect+SE, paired 2x2, single proportions,
    single rates, dose-level contrasts, independent 2x2, correlations,
    diagnostic 2x2, ...). Unknown codes raise ``ValueError`` so typos surface
    instead of silently dropping a shape.

    Returns a payload shaped for the P4 question flow: the echoed selection,
    a shape catalogue for the checkboxes, all candidate paths sorted by
    precision rank (best first), combination advice for interacting shapes,
    and the precision ladder legend. Pure static mapping and sorting; no
    module in the mapping is called.
    """
    if not isinstance(available, (list, tuple)) or not available:
        raise ValueError("available must be a non-empty list of data-shape codes")
    unknown = [code for code in available if not isinstance(code, str) or code not in SHAPE_LABELS]
    if unknown:
        raise ValueError(
            f"unknown data shapes: {', '.join(sorted({str(code) for code in unknown}))}; "
            f"valid shapes: {', '.join(DATA_SHAPES)}"
        )
    chosen = list(dict.fromkeys(available))  # deduplicate, keep order

    candidates: list[dict] = []
    for shape in chosen:
        candidates.extend(_SHAPE_PATHS[shape])
    # Stable order: precision rank first, then the curated per-shape order.
    # Sort on positions only and deep-copy the results, so the shared static
    # table is never mutated and callers cannot corrupt it.
    positions = {id(path): index for index, path in enumerate(candidates)}
    candidates.sort(key=lambda path: (path["precision_rank"], positions[id(path)]))
    candidates = [copy.deepcopy(path) for path in candidates]

    chosen_set = set(chosen)
    combinations = [rule for rule in _COMBINATION_RULES if rule["if"] <= chosen_set]

    return {
        "available": chosen,
        "shape_catalog": [{"code": code, "label": label} for code, label in SHAPE_LABELS.items()],
        "candidates": candidates,
        "combinations": combinations,
        "precision_ladder": [{"rank": rank, "label": label} for rank, label in PRECISION_LADDER.items()],
        "notes": [
            "排序遵循 Tierney et al. (2007) 的精度阶梯思想：直接报告的臂级/表格摘要优先，"
            "统计量或 p 值反推次之，间接推导（如中位生存）最后。",
            "候选路径只描述录入去向与所需字段，不做任何计算或存储；实际录入仍走各自的端点。",
            "混池校验在合成入口按 measure 注册表执行；此处仅在组合建议中提示族群冲突风险。",
        ],
    }
