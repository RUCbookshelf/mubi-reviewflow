"""结构化数据提取模板（Data Extraction Templates，衍生产品规格 2026-09-30 §2）。

把"按研究设计定制的提取表单"一键映射为标准效应量 dict，消除手动换算。

纯计算模块，零副作用（产品哲学 §0.1 / 新模块规则 1）：import 时不连数据库、
不起服务、不联网、不加载 scipy —— 重量级方法学依赖在 :func:`map_to_effect`
内部按需动态导入（模板的 ``effect_function`` 本身也是动态解析的）。

设计决定（与规格书 §2.2 示意的差异）：
- 规格书示例的 ``effect_function`` 指向 ``coscreen.review_analysis.calculate_continuous_effect``
  等函数名，代码库中不存在这些名字；实际存在的是
  ``review_analysis.continuous_effect`` / ``review_analysis.binary_effect`` /
  ``effect_size_formats.calculate_effect("hazard_ratio_ci", ...)``。本模块提供
  同名适配函数（``calculate_continuous_effect`` 等，字段键即参数名），内部转调
  上述既有实现——模板声明的计算语义不变，复用的也是同一套经过测试的方法学代码。
- ``diagnostic_2x2`` 模板的 ``effect_measure="DTA"``：``DTA`` 不是
  ``coscreen.measure_registry`` 已登记代码（注册表是 measure 代码唯一权威清单），
  因此诊断 2×2 的保存路径是既有的 ``review_dta_results`` 表
  （``coscreen.dta_analysis.save_result``，bivariate 合成的输入），而非
  ``review_effects``。:func:`calculate_dta_2x2` 额外给出单研究诊断比值比 DOR
  （natural 尺度估计 + log 尺度 SE）供核对展示，仅信息性、绝不进入合成池；
  含零单元格的表 DOR 无定义（本项目的零单元格策略要求显式决定，不做静默连续性
  校正），返回 ``estimate=None`` 并附结构化原因，原始计数仍可正常入库。

方法学锚：
- 连续 / 二分类臂级计算：``coscreen.review_analysis.continuous_effect`` /
  ``binary_effect``（Cochrane Handbook ch.6 的 MD/SMD(Hedges g) 与 RR/OR/RD）。
- 生存 HR 的 CI→SE 换算：``coscreen.effect_size_formats.calculate_effect``
  的 ``hazard_ratio_ci`` 路径（比值类 CI 视为 log 尺度正态）。
- DOR：Moses-Littenberg 传统单数值指标，ln DOR 方差 = 1/TP+1/FP+1/FN+1/TN
  （Cochrane DTA Handbook ch.10；现代合成用 bivariate 模型，DOR 仅作单研究展示）。
"""

from __future__ import annotations

import importlib
import inspect
import math
from dataclasses import dataclass, field
from typing import Any, Callable

__all__ = [
    "ExtractionField",
    "ExtractionTemplate",
    "TEMPLATES",
    "map_to_effect",
    "DTA_ROUTING_CODE",
    "calculate_continuous_effect",
    "calculate_binary_arm_effect",
    "calculate_hr_with_ci",
    "calculate_dta_2x2",
]

# diagnostic_2x2 模板的路由标签（规格 §2.2 的 effect_measure="DTA"）。
# 注意：DTA **不是** coscreen.measure_registry 已登记的 measure 代码，也**不应**
# 注册——诊断精度没有单一合成指标（bivariate 模型同时建模 Se/Sp），注册表登记
# 会让 "DTA" 出现在 review_effects 的 CHECK 白名单里，反而打开混池旁门。该值
# 只用于 API 层把诊断 2×2 路由到既有 review_dta_results 存储；它从不进入
# review_effects/合成（check_pool_compatibility 遇未登记代码会抛错，是兜底）。
# 经由本常量（而非 "measure": "DTA" 字面量）表达，tests/test_measure_registry.py
# 的反射扫描据此正确地不把它视作待登记的合成指标代码。
DTA_ROUTING_CODE = "DTA"


# ---------------------------------------------------------------------------
# 数据模型（字段与校验按规格 §2.2）
# ---------------------------------------------------------------------------

@dataclass
class ExtractionField:
    """表单中的单个字段。"""

    key: str                # "mean_t" / "sd_t" / "n_t" / ...
    label: str              # "治疗组均值"
    input_type: str         # "number" | "text" | "select" | "boolean"
    unit: str = ""          # 显示单位（如 "mmHg"）或空
    required: bool = True
    validation: dict = field(default_factory=dict)  # {"min": 0, "max": None} 或 {"options": [...]}
    condition: str | None = None  # 条件显示（如 "study_type == 'rct'"）；内置模板未使用

    def to_dict(self) -> dict:
        return {"key": self.key, "label": self.label, "input_type": self.input_type,
                "unit": self.unit, "required": self.required,
                "validation": dict(self.validation), "condition": self.condition}


@dataclass
class ExtractionTemplate:
    """按研究设计的提取模板（模板是代码，不是数据——硬编码，不存数据库）。"""

    template_id: str        # "rct_continuous" / "rct_binary" / "diagnostic_2x2" / "survival_hr"
    study_type: str         # "rct" / "cohort" / "diagnostic" / "survival" / "cross_over"
    outcome_type: str       # "continuous" / "binary" / "diagnostic" / "survival"
    fields: list[ExtractionField]
    effect_measure: str     # 映射到的 measure 代码："MD"/"SMD"/"RR"/"OR"/"DTA"/"HR"
    effect_function: str    # 映射函数完整路径（map_to_effect 动态导入）

    def to_dict(self) -> dict:
        return {"template_id": self.template_id, "study_type": self.study_type,
                "outcome_type": self.outcome_type,
                "fields": [f.to_dict() for f in self.fields],
                "effect_measure": self.effect_measure, "effect_function": self.effect_function}

    def field_keys(self) -> set[str]:
        return {f.key for f in self.fields}


# ---------------------------------------------------------------------------
# 效应量映射函数（模板声明的 effect_function 目标；内部转调既有实现）
# ---------------------------------------------------------------------------

def calculate_continuous_effect(mean_t: float, sd_t: float, n_t: int,
                                mean_c: float, sd_c: float, n_c: int,
                                unit: str = "", measure: str = "MD") -> dict:
    """两独立臂连续结局 → MD/SMD（转调 ``review_analysis.continuous_effect``）。

    ``measure`` 可由用户显式选择 MD 或 SMD（模板默认 MD）；``unit`` 仅作
    提取记录，不参与计算。
    """
    from coscreen.review_analysis import continuous_effect  # 动态导入，保持模块零副作用

    return continuous_effect(n_t, mean_t, sd_t, n_c, mean_c, sd_c, measure)


def calculate_binary_arm_effect(events_t: int, n_t: int, events_c: int, n_c: int,
                                measure: str = "RR") -> dict:
    """两独立臂二分类结局 → RR/OR/RD（转调 ``review_analysis.binary_effect``）。

    ``measure`` 可由用户显式选择 RR、OR 或 RD（模板默认 RR）；比值类指标遇
    零单元格按既有策略显式拒绝（"choose and document a continuity correction"）。
    """
    from coscreen.review_analysis import binary_effect  # 动态导入，保持模块零副作用

    return binary_effect(events_t, n_t, events_c, n_c, measure)


def calculate_hr_with_ci(hr: float, ci_low: float, ci_high: float,
                         events_t: float | None = None, events_c: float | None = None,
                         confidence_level: float = 0.95) -> dict:
    """报告的 HR 与 CI → 估计 + log 尺度 SE（转调 ``effect_size_formats`` 的
    ``hazard_ratio_ci`` 路径）。

    要求 CI 是 log 尺度对称的 Wald 区间；``events_t``/``events_c`` 为可选的
    事件数记录（供后续不精确性判断，不参与换算）；``confidence_level`` 与
    报告 CI 的置信水平一致（默认 0.95）。
    """
    from coscreen.effect_size_formats import calculate_effect  # 动态导入，保持模块零副作用

    result = calculate_effect("hazard_ratio_ci", {
        "estimate": hr, "ci_low": ci_low, "ci_high": ci_high,
        "confidence_level": confidence_level, "ci_method": "wald_normal"})
    if events_t is not None or events_c is not None:
        result["input_data"] = {**result["input_data"],
                                "events_t": events_t, "events_c": events_c}
    return result


def calculate_dta_2x2(tp: int, fp: int, fn: int, tn: int) -> dict:
    """诊断 2×2 → 单研究 DOR（natural 尺度估计 + log 尺度 SE），信息性展示。

    ln DOR 的方差 = 1/TP + 1/FP + 1/FN + 1/TN（Moses-Littenberg 传统指标）。
    任一单元格为零时 DOR 无定义：本项目零单元格策略要求显式决定（不静默加
    0.5 校正），故返回 ``estimate=None``/``se=None`` 并附 ``dor_unavailable``
    结构化原因；原始计数本身仍可经 ``dta_analysis.save_result`` 入库参与
    bivariate 合成（binomial 似然天然容纳零单元格）。
    """
    cells = {"tp": tp, "fp": fp, "fn": fn, "tn": tn}
    for name, value in cells.items():
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{name.upper()} 必须是非负整数")
    if min(tp, fp, fn, tn) == 0:
        return {"measure": DTA_ROUTING_CODE, "estimate": None, "se": None, "se_scale": "log",
                "dor_unavailable": True,
                "dor_unavailable_reason": "zero cell: DOR is undefined; the 2x2 counts "
                                          "remain storable for bivariate synthesis",
                "input_data": dict(cells), "entry_method": "diagnostic_2x2"}
    if tp + fn <= 0 or tn + fp <= 0:  # 防御：四格均 >0 时不可达
        raise ValueError("患病组与非患病组都必须有受试者")
    dor = (tp * tn) / (fp * fn)
    se = math.sqrt(1 / tp + 1 / fp + 1 / fn + 1 / tn)
    if not math.isfinite(dor) or not math.isfinite(se) or se <= 0:
        raise ValueError("诊断比值比超出可表示范围")
    return {"measure": DTA_ROUTING_CODE, "estimate": dor, "se": se, "se_scale": "log",
            "input_data": dict(cells), "entry_method": "diagnostic_2x2"}


# ---------------------------------------------------------------------------
# 内置模板（规格 §2.2；硬编码，不存数据库）
# ---------------------------------------------------------------------------

TEMPLATES: dict[str, ExtractionTemplate] = {
    "rct_continuous": ExtractionTemplate(
        template_id="rct_continuous",
        study_type="rct", outcome_type="continuous",
        fields=[
            ExtractionField(key="mean_t", label="治疗组均值", input_type="number",
                            required=True, validation={"min": None}),
            ExtractionField(key="sd_t", label="治疗组SD", input_type="number",
                            required=True, validation={"min": 0.01}),
            ExtractionField(key="n_t", label="治疗组样本量", input_type="number",
                            required=True, validation={"min": 2}),
            ExtractionField(key="mean_c", label="对照组均值", input_type="number",
                            required=True),
            ExtractionField(key="sd_c", label="对照组SD", input_type="number",
                            required=True, validation={"min": 0.01}),
            ExtractionField(key="n_c", label="对照组样本量", input_type="number",
                            required=True, validation={"min": 2}),
            ExtractionField(key="unit", label="结局单位", input_type="text",
                            required=True),
        ],
        effect_measure="MD",  # 或 SMD，由用户在模板上方的下拉框显式选择
        effect_function="coscreen.extraction_templates.calculate_continuous_effect",
    ),
    "rct_binary": ExtractionTemplate(
        template_id="rct_binary",
        study_type="rct", outcome_type="binary",
        fields=[
            ExtractionField(key="events_t", label="治疗组事件数", input_type="number",
                            required=True, validation={"min": 0}),
            ExtractionField(key="n_t", label="治疗组总数", input_type="number",
                            required=True, validation={"min": 1}),
            ExtractionField(key="events_c", label="对照组事件数", input_type="number",
                            required=True, validation={"min": 0}),
            ExtractionField(key="n_c", label="对照组总数", input_type="number",
                            required=True, validation={"min": 1}),
        ],
        effect_measure="RR",  # 或 OR/RD，由用户在模板上方的下拉框显式选择
        effect_function="coscreen.extraction_templates.calculate_binary_arm_effect",
    ),
    "diagnostic_2x2": ExtractionTemplate(
        template_id="diagnostic_2x2",
        study_type="diagnostic", outcome_type="diagnostic",
        fields=[
            ExtractionField(key="tp", label="真阳性", input_type="number",
                            required=True, validation={"min": 0}),
            ExtractionField(key="fp", label="假阳性", input_type="number",
                            required=True, validation={"min": 0}),
            ExtractionField(key="fn", label="假阴性", input_type="number",
                            required=True, validation={"min": 0}),
            ExtractionField(key="tn", label="真阴性", input_type="number",
                            required=True, validation={"min": 0}),
        ],
        effect_measure="DTA",
        effect_function="coscreen.extraction_templates.calculate_dta_2x2",
    ),
    "survival_hr": ExtractionTemplate(
        template_id="survival_hr",
        study_type="survival", outcome_type="survival",
        fields=[
            ExtractionField(key="hr", label="风险比 HR", input_type="number",
                            required=True, validation={"min": 0.01}),
            ExtractionField(key="ci_low", label="CI 下限", input_type="number",
                            required=True),
            ExtractionField(key="ci_high", label="CI 上限", input_type="number",
                            required=True),
            ExtractionField(key="events_t", label="治疗组事件数", input_type="number",
                            required=False),
            ExtractionField(key="events_c", label="对照组事件数", input_type="number",
                            required=False),
        ],
        effect_measure="HR",
        effect_function="coscreen.extraction_templates.calculate_hr_with_ci",
    ),
}


# ---------------------------------------------------------------------------
# 校验 + 映射（规格 §2.3）
# ---------------------------------------------------------------------------

def _blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _coerce_number(field: ExtractionField, value: Any) -> int | float:
    """数字字段 → int/float；整数值的浮点输入转为 int（匹配臂级计算器的整数参数）。"""
    if isinstance(value, bool):
        raise ValueError(f"{field.label} 必须是数字")
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        try:
            number = float(str(value).strip())
        except ValueError as exc:
            raise ValueError(f"{field.label} 必须是数字") from exc
    if not math.isfinite(number):
        raise ValueError(f"{field.label} 必须是有限数字")
    return int(number) if number.is_integer() else number


def _validate_and_coerce(template: ExtractionTemplate, values: dict[str, Any]) -> dict[str, Any]:
    """必填存在性 + 数值 min/max + 类型校验；返回字段键 → 强制转换后的值。"""
    if not isinstance(values, dict):
        raise ValueError("提取表单数据必须是对象")
    checked: dict[str, Any] = {}
    for field_def in template.fields:
        present = field_def.key in values
        raw = values.get(field_def.key)
        if field_def.required and (not present or _blank(raw)):
            raise ValueError(f"缺少必填字段: {field_def.label}")
        if not present or _blank(raw):
            continue  # 可选字段缺省/留空 -> 不传给计算函数
        if field_def.input_type == "number":
            number = _coerce_number(field_def, raw)
            minimum = field_def.validation.get("min")
            maximum = field_def.validation.get("max")
            if minimum is not None and number < minimum:
                raise ValueError(f"{field_def.label} 不能小于 {minimum}")
            if maximum is not None and number > maximum:
                raise ValueError(f"{field_def.label} 不能大于 {maximum}")
            checked[field_def.key] = number
        elif field_def.input_type == "select":
            options = field_def.validation.get("options")
            if options is not None and raw not in options:
                raise ValueError(f"{field_def.label} 必须是以下选项之一: {', '.join(map(str, options))}")
            checked[field_def.key] = raw
        elif field_def.input_type == "boolean":
            if not isinstance(raw, bool):
                raise ValueError(f"{field_def.label} 必须是布尔值")
            checked[field_def.key] = raw
        else:  # text
            if not isinstance(raw, str):
                raise ValueError(f"{field_def.label} 必须是文本")
            checked[field_def.key] = raw.strip()
    return checked


def _resolve_effect_function(template: ExtractionTemplate) -> Callable:
    """动态导入模板声明的 effect_function（不在模块级导入——保持零副作用）。"""
    module_path, _, func_name = template.effect_function.rpartition(".")
    try:
        module = importlib.import_module(module_path)
        func = getattr(module, func_name)
    except (ImportError, AttributeError) as exc:
        raise ValueError(f"模板 {template.template_id} 的效应量计算函数不可用: "
                         f"{template.effect_function}") from exc
    if not callable(func):
        raise ValueError(f"模板 {template.template_id} 的 effect_function 不是可调用对象")
    return func


def map_to_effect(template: ExtractionTemplate, values: dict[str, Any],
                  study_id: str, source_key: str) -> dict:
    """把提取表单数据映射为标准效应量 dict（规格 §2.3）。

    流程：校验（必填存在 + min/max/类型）→ 动态导入并调用 ``effect_function``
    （只传字段键与函数显式声明的附加参数，如 ``measure``/``confidence_level``；
    未知键一律拒绝，不做静默丢弃）→ 返回含 ``measure``/``estimate``/``se``/
    ``study_id``/``source_key``/``input_data`` 的标准 dict，可直接交给
    ``review_analysis.save_effect()`` 入库（``DTA`` 除外，见模块 docstring）。

    ``values["measure"]`` 可显式覆盖模板默认指标（如 rct_binary 的 RR→OR），
    仅对声明了 ``measure`` 参数的模板生效；对固定指标的模板传入即拒绝。
    """
    checked = _validate_and_coerce(template, values)
    func = _resolve_effect_function(template)
    signature_params = set(inspect.signature(func).parameters)
    field_keys = template.field_keys()

    # 未知键拒绝（"measure" 与计算函数声明的附加参数除外），防拼写错误静默丢失
    allowed_extra = ({"measure"} | signature_params) - field_keys
    unknown = set(values) - field_keys - allowed_extra
    if unknown:
        raise ValueError(f"未知字段: {', '.join(sorted(unknown))}")

    kwargs = dict(checked)
    if "measure" in values:
        if "measure" not in signature_params:
            raise ValueError(f"模板 {template.template_id} 的效应指标固定为 "
                             f"{template.effect_measure}，不支持覆盖")
        override = values["measure"]
        if not isinstance(override, str) or not override.strip():
            raise ValueError("measure 覆盖必须是非空字符串")
        kwargs["measure"] = override.strip()
    for param in sorted(allowed_extra - {"measure"}):
        if param in values and not _blank(values[param]):
            kwargs[param] = values[param]

    result = func(**kwargs)
    if not isinstance(result, dict) or not all(key in result for key in ("measure", "estimate", "se")):
        raise ValueError(f"模板 {template.template_id} 的计算函数未返回标准效应量")
    result = dict(result)
    result["study_id"] = study_id
    result["source_key"] = source_key
    calculator_entry_method = result.get("entry_method")  # 计算器自报的格式名（如 hazard_ratio_ci）
    result["entry_method"] = "extraction:" + template.template_id
    input_data = {"template_id": template.template_id, "values": dict(values)}
    if calculator_entry_method:
        input_data["calculator_entry_method"] = calculator_entry_method
    result["input_data"] = input_data
    return result
