"""效应量（measure）代码注册表 —— 全库唯一清单（P9）。

纯数据 + 纯函数，零副作用：不 import 任何 coscreen 模块、不做 I/O，可被
``coscreen.db``（CHECK 约束清单与旧库重建判定）与前端护栏（P2 混池拦截）
安全复用而不引入循环依赖。

职责边界（设计决定）：

- 本模块是 measure 代码的**唯一权威清单**：``review_effects.measure`` 的
  CHECK IN 列表由 :data:`MEASURE_CODES` 生成；``review_analysis.MEASURES``
  仍由其原负责人维护，两者的一致性由 ``tests/test_measure_registry.py``
  的反射式对账测试强制（发现遗漏即失败）。
- ``synthesis_ready=True`` 当且仅当代码同时被 db CHECK 与
  ``review_analysis.MEASURES`` 接受（即可经 ``save_effect`` 写入并进入合
  成）；``False`` 表示计算模块已产出该代码但尚未接线（保存/合成入口仍拒绝），
  详见模块尾部的“待接线”注释与 docs/methodology/measure_registry.md。
- 混池约定：同 family 或互为 poolable_families 才可进入同一合成池。比值族
  （RR/OR/HR/RATE_RATIO/LOG_ROM 等）family 各自独立、互不混池——除非文献
  明确支持，否则不得把两个比值代码放进同一池（设计决定记录于文档）。

``MEASURE_CODES`` 的先后顺序即 CHECK IN 列表的字面量顺序：既有 11 个代码
保持历史顺序作为前缀（旧库模拟测试依赖该前缀做字面量替换），注册表新增
代码一律追加在尾部，不得重排。
"""

from __future__ import annotations

from itertools import combinations

__all__ = [
    "MEASURE_REGISTRY",
    "MEASURE_CODES",
    "SYNTHESIS_READY_CODES",
    "CAUTIONS",
    "get_measure",
    "is_registered",
    "check_pool_compatibility",
    "registry_snapshot",
]

# 条目结构（MEASURE_REGISTRY 值的字段是冻结契约，缺一不可、不加不减）：
#   family             —— 方法学家族；同族才视为可合成（混池检测第一判据）
#   scale              —— 分析尺度：'log' | 'natural' | 'fisher_z' | 'logit'
#   poolable_families  —— 显式声明可与本代码混池的其他 family（当前全部为空：
#                         比值族互不混池，跨族合并需文献支持并成对声明）
#   synthesis_ready    —— 是否已被 db CHECK 与 review_analysis.MEASURES 同时
#                         接受（即可经 save_effect 入库并进入合成）
#   source_module      —— 该代码的主要产出模块（接线/排障入口）
#   caution_key        —— CAUTIONS 中的警示键；None 表示无专项警示
MEASURE_REGISTRY: dict[str, dict] = {
    # ---- 既有 11 个代码（顺序 = 历史 CHECK 字面量顺序，不得重排）----
    "RR": {
        "family": "risk_ratio", "scale": "log", "poolable_families": (),
        "synthesis_ready": True, "source_module": "coscreen.review_analysis",
        "caution_key": None,
    },
    "OR": {
        "family": "odds_ratio", "scale": "log", "poolable_families": (),
        "synthesis_ready": True, "source_module": "coscreen.review_analysis",
        "caution_key": None,
    },
    "RD": {
        "family": "risk_difference", "scale": "natural", "poolable_families": (),
        "synthesis_ready": True, "source_module": "coscreen.review_analysis",
        "caution_key": None,
    },
    "MD": {
        "family": "mean_difference", "scale": "natural", "poolable_families": (),
        "synthesis_ready": True, "source_module": "coscreen.review_analysis",
        "caution_key": None,
    },
    "SMD": {
        "family": "std_mean_difference", "scale": "natural", "poolable_families": (),
        "synthesis_ready": True, "source_module": "coscreen.review_analysis",
        "caution_key": None,
    },
    "HR": {
        "family": "hazard_ratio", "scale": "log", "poolable_families": (),
        "synthesis_ready": True, "source_module": "coscreen.review_analysis",
        "caution_key": None,
    },
    "RATE_RATIO": {
        "family": "rate_ratio", "scale": "log", "poolable_families": (),
        "synthesis_ready": True, "source_module": "coscreen.review_analysis",
        "caution_key": None,
    },
    "FISHER_Z": {
        "family": "correlation_pearson", "scale": "fisher_z", "poolable_families": (),
        "synthesis_ready": True, "source_module": "coscreen.review_analysis",
        "caution_key": None,
    },
    "MEAN": {
        "family": "mean", "scale": "natural", "poolable_families": (),
        "synthesis_ready": True, "source_module": "coscreen.review_analysis",
        "caution_key": None,
    },
    "LOGIT_PROP": {
        "family": "logit_proportion", "scale": "logit", "poolable_families": (),
        "synthesis_ready": True, "source_module": "coscreen.review_analysis",
        "caution_key": None,
    },
    "LOG_RATE": {
        "family": "log_rate", "scale": "log", "poolable_families": (),
        "synthesis_ready": True, "source_module": "coscreen.review_analysis",
        "caution_key": None,
    },
    # ---- P9 新增（已产出、待接线：synthesis_ready=False）----
    "PAIRED_OR": {
        "family": "odds_ratio_paired", "scale": "log", "poolable_families": (),
        "synthesis_ready": True, "source_module": "coscreen.paired_binary_effect",
        "caution_key": "paired_discordant_cells",
    },
    # （PAIRED_RD 另一产出模块：coscreen.paired_rd_interval；source_module
    #   保持单一主模块，保证机器可解析。）
    "PAIRED_RD": {
        "family": "risk_difference_paired", "scale": "natural", "poolable_families": (),
        "synthesis_ready": True,
        "source_module": "coscreen.paired_binary_effect",
        "caution_key": "paired_discordant_cells",
    },
    "SMCR": {
        "family": "std_mean_change_pretest", "scale": "natural", "poolable_families": (),
        "synthesis_ready": True,
        "source_module": "coscreen.single_group_standardized_change",
        "caution_key": "smcr_correlation_or_change_sd",
    },
    "SMCC": {
        "family": "std_mean_change_score", "scale": "natural", "poolable_families": (),
        "synthesis_ready": True,
        "source_module": "coscreen.single_group_standardized_change",
        "caution_key": None,
    },
    "LOG_ROM": {
        "family": "ratio_of_means", "scale": "log", "poolable_families": (),
        "synthesis_ready": True, "source_module": "coscreen.ratio_of_means_effect",
        "caution_key": "rom_not_risk_ratio",
    },
    "PHI": {
        "family": "correlation_binary", "scale": "natural", "poolable_families": (),
        "synthesis_ready": True, "source_module": "coscreen.binary_correlation_conversion",
        "caution_key": "marginal_dependent_correlation",
    },
    "R_EQUIV_APPROX": {
        "family": "correlation_tetrachoric_approx", "scale": "natural",
        "poolable_families": (),
        "synthesis_ready": True, "source_module": "coscreen.binary_correlation_conversion",
        "caution_key": "marginal_dependent_correlation",
    },
    "IRR": {
        "family": "rate_ratio_descriptive", "scale": "log", "poolable_families": (),
        "synthesis_ready": False, "source_module": "coscreen.rate_ratio_exact_interval",
        "caution_key": "no_se_presentation_only",
    },
    "PROP": {
        "family": "proportion_descriptive", "scale": "natural", "poolable_families": (),
        "synthesis_ready": False, "source_module": "coscreen.single_group_exact_ci",
        "caution_key": "no_se_presentation_only",
    },
    "RATE": {
        "family": "rate_descriptive", "scale": "natural", "poolable_families": (),
        "synthesis_ready": False, "source_module": "coscreen.single_group_exact_ci",
        "caution_key": "no_se_presentation_only",
    },
    "NNT": {
        "family": "nnt", "scale": "natural", "poolable_families": (),
        "synthesis_ready": False, "source_module": "coscreen.nnt_conversion",
        "caution_key": "nnt_depends_on_cer",
    },
}

# CHECK IN 列表顺序 = 既有 11 码历史顺序前缀 + 追加的新码（见模块 docstring）。
MEASURE_CODES: tuple[str, ...] = tuple(MEASURE_REGISTRY)

# 可直接进入 save_effect / synthesize 的代码（= 与 review_analysis.MEASURES
# 的交集；一致性由 tests/test_measure_registry.py 强制）。
SYNTHESIS_READY_CODES: tuple[str, ...] = tuple(
    code for code in MEASURE_CODES if MEASURE_REGISTRY[code]["synthesis_ready"]
)

# caution_key 目录：UI 护栏按键查取警示文案；文案随代码演进在此维护。
CAUTIONS: dict[str, str] = {
    "paired_discordant_cells": (
        "Paired-design effect estimated from discordant pairs only; never pool "
        "with independent-group effects of the same measure family."
    ),
    "smcr_correlation_or_change_sd": (
        "SMCR standardizes by the pretest SD and its variance needs either the "
        "pre/post correlation (with a cited source) or the observed change SD; "
        "document which one was used."
    ),
    "rom_not_risk_ratio": (
        "The log ratio of means is a continuous-outcome effect; it must never "
        "be pooled or compared with the binary risk ratio (RR)."
    ),
    "marginal_dependent_correlation": (
        "Correlation coefficient depends on the binary marginal split; it must "
        "not be pooled with Fisher-z correlations from continuous outcomes."
    ),
    "no_se_presentation_only": (
        "Descriptive presentation interval with no exported standard error; "
        "never enter inverse-variance pooling and never back-derive an SE "
        "from the interval limits."
    ),
    "nnt_depends_on_cer": (
        "NNT depends on the control event rate of the target population; "
        "recompute with the actual CER before use."
    ),
}

_ENTRY_FIELDS = frozenset(
    {"family", "scale", "poolable_families", "synthesis_ready",
     "source_module", "caution_key"}
)
_SCALES = frozenset({"log", "natural", "fisher_z", "logit"})


def _validate_registry() -> None:
    """纯数据一致性自检（无 I/O；注册表内部结构错误在 import 即失败）。"""
    families = {entry["family"] for entry in MEASURE_REGISTRY.values()}
    for code, entry in MEASURE_REGISTRY.items():
        if set(entry) != _ENTRY_FIELDS:
            raise ValueError(f"注册表条目字段不完整: {code!r}")
        if entry["scale"] not in _SCALES:
            raise ValueError(f"注册表条目尺度非法: {code!r} -> {entry['scale']!r}")
        unknown = set(entry["poolable_families"]) - families
        if unknown:
            raise ValueError(f"注册表条目声明了未知 family: {code!r} -> {sorted(unknown)}")
        caution = entry["caution_key"]
        if caution is not None and caution not in CAUTIONS:
            raise ValueError(f"注册表条目引用了未定义的警示键: {code!r} -> {caution!r}")


_validate_registry()


def is_registered(code: str) -> bool:
    """代码是否已登记（任意输入安全，不抛错）。"""
    return code in MEASURE_REGISTRY


def get_measure(code: str) -> dict:
    """取单个条目（含 code 字段的防御性副本）；未知代码抛 ValueError。"""
    entry = MEASURE_REGISTRY.get(code)
    if entry is None:
        raise ValueError(
            f"未知 measure 代码: {code!r}，必须为注册表已登记代码之一 "
            f"（共 {len(MEASURE_CODES)} 个）"
        )
    return {"code": code, **entry, "poolable_families": tuple(entry["poolable_families"])}


def check_pool_compatibility(codes) -> dict:
    """混池检测（P2 前端护栏的后端依据）。

    规则：两两代码**同 family** 或**互为 poolable_families** 才可进入同一
    合成池；否则在 reasons 中返回结构化拦截原因。重复代码视为同码（天然
    兼容）；单代码/空池直接兼容。未知代码抛 ValueError（与 get_measure
    一致，由调用方决定如何呈现）。
    """
    code_list = list(codes)
    for code in code_list:
        if not is_registered(code):
            raise ValueError(
                f"未知 measure 代码: {code!r}，必须为注册表已登记代码之一 "
                f"（共 {len(MEASURE_CODES)} 个）"
            )
    reasons: list[dict] = []
    for a, b in combinations(sorted(set(code_list)), 2):
        entry_a, entry_b = MEASURE_REGISTRY[a], MEASURE_REGISTRY[b]
        family_a, family_b = entry_a["family"], entry_b["family"]
        poolable = (
            family_a in entry_b["poolable_families"]
            or family_b in entry_a["poolable_families"]
        )
        if family_a == family_b or poolable:
            continue
        reasons.append({
            "type": "family_conflict",
            "codes": [a, b],
            "families": [family_a, family_b],
            "detail": (
                f"{a} (family {family_a!r}) and {b} (family {family_b!r}) "
                "estimate different effect scales and must not share a pool; "
                "cross-family pooling requires explicit literature support "
                "declared in poolable_families."
            ),
        })
    return {"compatible": not reasons, "reasons": reasons}


def registry_snapshot() -> list[dict]:
    """导出全表（供 UI 下拉/护栏展示）；每次返回全新副本，注册表不可被外部污染。"""
    return [get_measure(code) for code in MEASURE_CODES]
