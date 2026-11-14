"""r9 L6d3 效应格式/提取模板泳道钉子（7 条修复 + 1 条升级登记）。

清单：``out/release_plan/inventory_夜扫286确认分组.md`` §3.6（本泳道 9 条）。
证据原文：``out/external_reviews/night_verify_batch_05.md``（F4-20、F4-40）、
``night_verify_batch_14.md``（F4-18、F4-19）、``night_verify_batch_06.md``
（F4-41、F4-44、F4-45）、``night_verify_batch_15.md``（F4-42、F4-48）。

条目 ↔ 测试类 ↔ 处置：

| 条目 | 载体 | 测试类 | 处置 |
|---|---|---|---|
| b05·F4-20 | effect_size_formats.py CI 对称性门槛 | ``F420ReportedRoundedCiNails`` | 修妥（放宽到出版物精度域） |
| b14·F4-18 | 引导注册表漏列 ci_method | （无绿钉子，见文末 SKIP） | **需升级**（entry_guidance.py 属 L2 域） |
| b14·F4-19 | 模块/5 格式/端点零测试引用 | ``F419CoverageNails`` + ``FormatEffectsAndExtractionEndpointTests`` | 修妥（本文件即防线） |
| b05·F4-40 / b06·F4-45 | DOR 整数除法先溢出 | ``F440F445DtaOverflowNails`` | 修妥（同族：溢出守卫前移） |
| b06·F4-41 | 模板数值无幅值预算 | ``F441AmplitudeNails`` | 修妥（同族：溢出守卫前移） |
| b06·F4-44 | 超大整数 float() 溢出 | ``F444HugeIntegerNails`` | 修妥（同族：溢出守卫前移） |
| b15·F4-42 | 生存模板事件数无下界 | ``F442SurvivalEventsNails`` | 修妥 |
| b15·F4-48 | 落库 values 不含实际参与计算的附加参数 | ``F448ValuesMatchInputsNails`` | 修妥 |

设计口径（与 R1/R2/R4 对齐）：

- 每条钉子都断言「修后行为」，撤掉修复即红；各类都带 ``*_control_*``
  对照组，钉住合法输入的逐位输出（R1：不改格式；R2：不新增对合法输入的
  拒绝）。对照组在当前树（bfd07ae）上修复前后都为绿。
- 修前实测（本轮在当前树复现，命令见交付 summary）：
  ``HR 1.5 (1.2-2.0) -> ValueError must be symmetric``；
  ``sd_t=1.5e308 -> OverflowError integer division result too large``；
  ``n_t=10**400 -> OverflowError int too large to convert to float``；
  ``tp=tn=10**200 -> OverflowError integer division result too large``；
  ``events_t=-5 -> OK（落库 -5）``；
  ``confidence_level=0.90 -> values 不含该键（se 却随它变化）``。
"""

from __future__ import annotations

import math
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scipy.stats import norm  # noqa: E402

from coscreen.effect_size_formats import calculate_effect  # noqa: E402
from coscreen.extraction_templates import (  # noqa: E402
    TEMPLATES,
    _coerce_number,
    calculate_dta_2x2,
    map_to_effect,
)

Z95 = float(norm.ppf(0.975))


def _hr(**overrides):
    values = {"estimate": 1.5, "ci_low": 1.2, "ci_high": 2.0,
              "confidence_level": 0.95, "ci_method": "wald_normal"}
    values.update(overrides)
    return values


# ======================================================================
# b05·F4-20 —— 出版物舍入 CI（HR 1.5 (1.2-2.0)）必须可录入
# ======================================================================
class F420ReportedRoundedCiNails(unittest.TestCase):
    """修前实测（当前树，``calculate_effect`` 真实调用）：

    .. code-block:: text

        HR 1.5 (1.2-2.0), wald_normal -> ValueError: confidence interval
            must be symmetric around estimate on the analysis scale
        OR 0.77 (0.611-0.972), wald_normal -> ValueError: 同上
        exact-SE control (se=0.15) -> 0.14999999999999997  （通过）

    即门槛 rel_tol=1e-7 只放行「先知道 SE 再反构出来的 CI」。修复后门槛按
    三个已报告数字的出版物精度（最后一位小数的半个单位）计量，仍拒绝中点
    明显偏离所述估计值的区间。
    """

    def test_reported_rounded_hr_ci_is_accepted(self):
        """HR 1.5 (1.2-2.0)：SE 由区间宽度决定，estimate 保持报告值。"""
        result = calculate_effect("hazard_ratio_ci", _hr())
        self.assertEqual(result["measure"], "HR")
        self.assertEqual(result["estimate"], 1.5)
        self.assertEqual(result["se_scale"], "log")
        expected = (math.log(2.0) - math.log(1.2)) / (2 * Z95)
        self.assertAlmostEqual(result["se"], expected, places=12)

    def test_reported_rounded_or_ci_is_accepted(self):
        """OR 0.77 (0.611-0.972)（三位小数的常见发表形态）。"""
        result = calculate_effect("hazard_ratio_ci", _hr(
            estimate=0.77, ci_low=0.611, ci_high=0.972))
        self.assertEqual(result["estimate"], 0.77)
        expected = (math.log(0.972) - math.log(0.611)) / (2 * Z95)
        self.assertAlmostEqual(result["se"], expected, places=12)

    def test_rounded_generic_ci_md_is_accepted(self):
        """自然尺度同样受益：MD 2.0 (1.0-3.0) 的舍入区间本来就是对称的，
        此前因 rel_tol 只作用于差值相对量而偶发通过；修复后逐位不变。"""
        result = calculate_effect("generic_ci", {
            "measure": "MD", "estimate": 2.0, "ci_low": 1.0, "ci_high": 3.0,
            "confidence_level": 0.95, "scale": "natural", "ci_method": "wald_normal"})
        self.assertEqual(result["estimate"], 2.0)
        self.assertAlmostEqual(result["se"], (3.0 - 1.0) / (2 * Z95), places=12)

    def test_exact_se_reconstruction_control_is_unchanged(self):
        """对照组（R1/R2）：已知 SE 反构的 CI 必须照旧通过且 se 逐位不变。"""
        se = 0.15
        low = math.exp(math.log(1.5) - Z95 * se)
        high = math.exp(math.log(1.5) + Z95 * se)
        result = calculate_effect("hazard_ratio_ci", _hr(ci_low=low, ci_high=high))
        self.assertEqual(result["se"], 0.14999999999999997)

    def test_log_scale_report_identity_grain_is_unchanged(self):
        """已报告在对数尺度的 CI：数字本身就是分析尺度，舍入粒度按原值计。"""
        result = calculate_effect("generic_ci", {
            "measure": "HR", "estimate": 0.405, "ci_low": 0.182, "ci_high": 0.628,
            "confidence_level": 0.95, "scale": "log", "ci_method": "wald_normal"})
        self.assertAlmostEqual(result["se"], (0.628 - 0.182) / (2 * Z95), places=12)

    def test_interval_not_centred_on_reported_estimate_is_still_rejected(self):
        """放宽不等于不再校验：HR 1.5 (1.2-3.0) 的中点（约 1.9）与所述
        估计值 1.5 相差远超发表精度，必须继续 400。"""
        with self.assertRaises(ValueError) as caught:
            calculate_effect("hazard_ratio_ci", _hr(ci_high=3.0))
        self.assertIn("symmetric around estimate", str(caught.exception))

    def test_natural_scale_off_centre_interval_is_still_rejected(self):
        """自然尺度同一口径：MD 2.0 (0.5-3.0) 中点 1.75 ≠ 2.0。"""
        with self.assertRaises(ValueError) as caught:
            calculate_effect("generic_ci", {
                "measure": "MD", "estimate": 2.0, "ci_low": 0.5, "ci_high": 3.0,
                "confidence_level": 0.95, "scale": "natural", "ci_method": "wald_normal"})
        self.assertIn("symmetric around estimate", str(caught.exception))


# ======================================================================
# b14·F4-19 —— 模块、5 个格式与端点的回归防线
# ======================================================================
class F419CoverageNails(unittest.TestCase):
    """修前实测（静态核证）：

    .. code-block:: text

        grep -rln "effect_size_formats|generic_wald_p|generic_ci|
                    hazard_ratio_ci|format-effects" build/tests/ -> 空
        ls build/tests/*.py | wc -l -> 113（原报告 55，零引用结论不变）

    本文件把五个格式与 format-effects 端点钉进套件（端点级见下一个类），
    并复跑批 14 的 7 组边界输入，确认它们全部收敛为 ValueError 或合法结果。
    """

    def test_module_referenced_by_test_suite(self):
        """防线自证：build/tests 下至少有一个文件真实引用本模块与端点。"""
        pattern = re.compile(r"effect_size_formats|generic_ci|generic_wald_p|"
                             r"hazard_ratio_ci|format-effects")
        hits = [path for path in (REPO / "build" / "tests").glob("*.py")
                if pattern.search(path.read_text(encoding="utf-8", errors="ignore"))]
        self.assertTrue(hits, "effect_size_formats 在测试套件中重新成为零引用")

    def test_five_formats_produce_standard_results(self):
        """五个格式各跑一条正常输入：返回标准 dict 且 estimate/se 有限。"""
        cases = {
            "correlation_r_n": {"r": 0.5, "n": 20},
            "hazard_ratio_ci": _hr(),
            "rate_ratio_events_time": {"events_t": 30, "time_t": 100,
                                       "events_c": 15, "time_c": 100},
            "generic_ci": {"measure": "MD", "estimate": 2.0, "ci_low": 1.0,
                           "ci_high": 3.0, "confidence_level": 0.95,
                           "scale": "natural", "ci_method": "wald_normal"},
            "generic_wald_p": {"measure": "MD", "estimate": 0.5, "p": 0.05,
                               "scale": "natural"},
        }
        for format_name, values in cases.items():
            with self.subTest(format_name=format_name):
                result = calculate_effect(format_name, values)
                self.assertIn("measure", result)
                self.assertTrue(math.isfinite(result["estimate"]))
                self.assertTrue(math.isfinite(result["se"]))
                self.assertGreater(result["se"], 0)

    def test_boundary_inputs_stay_inside_value_error_contract(self):
        """批 14 的 7 组边界输入：不允许任何非 ValueError 异常逃出
        （调用方 ``except ValueError -> 400`` 是唯一契约）。"""
        cases = {
            "r_at_boundary": ("correlation_r_n", {"r": 1.0, "n": 10}),
            "n_huge": ("correlation_r_n", {"r": 0.5, "n": 10 ** 400}),
            "confidence_next_to_one": ("hazard_ratio_ci", _hr(
                estimate=1.5, ci_low=1.4, ci_high=1.6,
                confidence_level=1 - 2 ** -53)),
            "bool_estimate": ("generic_ci", {
                "measure": "MD", "estimate": True, "ci_low": 1, "ci_high": 2,
                "confidence_level": 0.95, "scale": "natural",
                "ci_method": "wald_normal"}),
            "p_path_zero_estimate": ("generic_wald_p", {
                "measure": "MD", "estimate": 0.0, "p": 0.05, "scale": "natural"}),
            "events_huge": ("rate_ratio_events_time", {
                "events_t": 10 ** 400, "time_t": 100, "events_c": 15,
                "time_c": 100}),
            "ci_method_exact": ("hazard_ratio_ci", _hr(ci_method="exact")),
        }
        for label, (format_name, values) in cases.items():
            with self.subTest(case=label):
                try:
                    result = calculate_effect(format_name, values)
                except ValueError:
                    continue
                except Exception as exc:  # noqa: BLE001 - 这就是要钉的契约
                    self.fail(f"{label} 逃出了 ValueError 契约：{type(exc).__name__}: {exc}")
                self.assertTrue(math.isfinite(result["se"]))


# ======================================================================
# b05·F4-40 / b06·F4-45 —— DOR 整数除法先于 isfinite 守卫溢出（同族）
# ======================================================================
class F440F445DtaOverflowNails(unittest.TestCase):
    """修前实测（当前树）：

    .. code-block:: text

        calculate_dta_2x2(tp=tn=1e200, fp=fn=1) -> OverflowError:
            integer division result too large for a float
        map_to_effect("diagnostic_2x2", tp=tn="1e200") -> OverflowError
        control (10,2,3,15) -> 25.0 / se 1.0

    修复（同族「溢出守卫前移」）：``_coerce_number`` 统一幅值预算 +
    ``calculate_dta_2x2`` 内除法/开方 OverflowError 转 ValueError。
    """

    def test_dor_division_overflow_is_value_error(self):
        # tp*tn = 10**400 而 fp*fn = 1：整数真除法本身溢出（fp=fn=1）
        with self.assertRaises(ValueError) as caught:
            calculate_dta_2x2(tp=10 ** 200, fp=1, fn=1, tn=10 ** 200)
        self.assertNotIsInstance(caught.exception, OverflowError)
        # 分子分母同为 10**400 时商恰为 1.0（可表示），不得被误拒（R2）
        balanced = calculate_dta_2x2(10 ** 200, 10 ** 200, 10 ** 200, 10 ** 200)
        self.assertEqual(balanced["estimate"], 1.0)

    def test_template_path_converts_overflow_to_value_error(self):
        with self.assertRaises(ValueError):
            map_to_effect(TEMPLATES["diagnostic_2x2"],
                          {"tp": "1e200", "fp": "1e200",
                           "fn": "1e200", "tn": "1e200"}, "S1", "K1")

    def test_control_dor_digits_unchanged(self):
        """对照组（R1）：常规 2×2 的 DOR 与 log SE 逐位不变。"""
        result = calculate_dta_2x2(10, 2, 3, 15)
        self.assertEqual(result["measure"], "DTA")
        self.assertEqual(result["estimate"], 25.0)
        self.assertEqual(result["se"], 1.0)
        self.assertEqual(calculate_dta_2x2(5, 1, 1, 10)["estimate"], 50.0)

    def test_control_zero_cell_path_unchanged(self):
        """对照组：零格仍走「DOR 无定义但计数可入库」分支。"""
        result = calculate_dta_2x2(10, 0, 2, 15)
        self.assertIsNone(result["estimate"])
        self.assertTrue(result["dor_unavailable"])


# ======================================================================
# b06·F4-41 —— 模板数值字段无幅值预算（1.5e308 级 SD）
# ======================================================================
class F441AmplitudeNails(unittest.TestCase):
    """修前实测（当前树）：

    .. code-block:: text

        map_to_effect(rct_continuous, sd_t=1.5e308, MD)
          -> OverflowError: integer division result too large for a float
        map_to_effect(..., sd_t="1e200", MD) -> 同上
        control（mean 1.0/1.5, sd 1.0, n 10）-> -0.5 / 0.4472135954999579

    ``_coerce_number`` 把 1.5e308 转成 309 位整数，``sd_t**2`` 再除以 n
    在整数真除法处溢出。修复在数值校验阶段统一设 1e150 幅值预算。
    """

    VALUES = {"mean_t": 1.0, "sd_t": 1.0, "n_t": 10,
              "mean_c": 1.5, "sd_c": 1.0, "n_c": 10, "unit": "x"}

    def _values(self, **overrides):
        values = dict(self.VALUES)
        values.update(overrides)
        return values

    def test_1e308_sd_is_value_error_not_overflow(self):
        with self.assertRaises(ValueError) as caught:
            map_to_effect(TEMPLATES["rct_continuous"],
                          self._values(sd_t=1.5e308), "S1", "K1")
        self.assertNotIsInstance(caught.exception, OverflowError)
        self.assertIn("超出可计算的数值范围", str(caught.exception))

    def test_1e200_sd_string_is_value_error(self):
        with self.assertRaises(ValueError):
            map_to_effect(TEMPLATES["rct_continuous"],
                          self._values(sd_t="1e200"), "S1", "K1")

    def test_control_normal_effect_digits_unchanged(self):
        """对照组（R1）：常规连续臂输出逐位不变。"""
        result = map_to_effect(TEMPLATES["rct_continuous"],
                               self._values(), "S1", "K1")
        self.assertEqual(result["estimate"], -0.5)
        self.assertEqual(result["se"], 0.4472135954999579)
        self.assertEqual(result["entry_method"], "extraction:rct_continuous")

    def test_large_but_computable_values_still_accepted(self):
        """R2：1e100 量级（下游平方仍可表示）必须照常计算，不得被预算误拒。"""
        result = map_to_effect(TEMPLATES["rct_continuous"],
                               self._values(sd_t=1e100, sd_c=1e100), "S1", "K1")
        self.assertEqual(result["measure"], "MD")
        self.assertTrue(math.isfinite(result["se"]))


# ======================================================================
# b06·F4-44 —— 超大整数在 float() 处抛 OverflowError
# ======================================================================
class F444HugeIntegerNails(unittest.TestCase):
    """修前实测（当前树）：

    .. code-block:: text

        float(10**400) -> OverflowError: int too large to convert to float
        _coerce_number(n_t 字段, 10**400) -> OverflowError
        map_to_effect(rct_continuous, n_t=10**400) -> OverflowError

    原实现只把字符串路径包进 ``try``；int/float 直传的 OverflowError 越过
    端点的 ``except ValueError`` 变 500。修复把两条路径统一包进
    ``except (OverflowError, ValueError)``。
    """

    VALUES = {"mean_t": 1.0, "sd_t": 1.0, "n_t": 10,
              "mean_c": 1.5, "sd_c": 1.0, "n_c": 10, "unit": "x"}

    def test_coerce_number_converts_big_int_overflow(self):
        field = TEMPLATES["rct_continuous"].fields[2]  # n_t
        with self.assertRaises(ValueError) as caught:
            _coerce_number(field, 10 ** 400)
        self.assertNotIsInstance(caught.exception, OverflowError)

    def test_map_to_effect_big_int_is_value_error(self):
        values = dict(self.VALUES, n_t=10 ** 400)
        with self.assertRaises(ValueError) as caught:
            map_to_effect(TEMPLATES["rct_continuous"], values, "S1", "K1")
        self.assertNotIsInstance(caught.exception, OverflowError)

    def test_control_integer_counts_still_accepted(self):
        """对照组（R2）：SQLite 可存范围内的整数计数仍照常接受。"""
        values = dict(self.VALUES, n_t=10 ** 9, n_c=10 ** 9 - 1)
        result = map_to_effect(TEMPLATES["rct_continuous"], values, "S1", "K1")
        self.assertEqual(result["input_data"]["values"]["n_t"], 10 ** 9)
        self.assertEqual(result["input_data"]["values"]["n_c"], 10 ** 9 - 1)

    def test_control_coerced_strings_digit_identical(self):
        """对照组（R1）：MM5-12 的强转语义不变——"3e1" 落库为 30。"""
        result = map_to_effect(TEMPLATES["rct_binary"],
                               {"events_t": "3e1", "n_t": "1e2",
                                "events_c": 5, "n_c": 100, "measure": "RR"},
                               "S1", "K1")
        self.assertAlmostEqual(result["estimate"], 6.0, places=12)
        self.assertEqual(result["input_data"]["values"]["events_t"], 30)
        self.assertEqual(result["input_data"]["values"]["n_t"], 100)


# ======================================================================
# b15·F4-42 —— survival_hr 的事件数无下界（负数经 UI 直接落库）
# ======================================================================
class F442SurvivalEventsNails(unittest.TestCase):
    """修前实测（当前树）：

    .. code-block:: text

        map_to_effect(survival_hr, events_t=-5, events_c=-3)
          -> OK，input_data.values 含 -5 / -3
        map_to_effect(survival_hr, events_t=1e300) -> OK，values 含 301 位整数
        control events 30/25 -> se=0.11385084270646685（与不传事件数一致）

    事件数只进 ``input_data`` 不参与换算，但会随 ``input_json`` 永久落库，
    形成记录型污染。修复给两个可选字段加 ``min: 0``（负数不是合法事件数），
    幅值同样受 1e150 预算约束。
    """

    VALUES = {"hr": 0.5, "ci_low": 0.4, "ci_high": 0.625}

    def test_negative_events_are_value_error(self):
        for key in ("events_t", "events_c"):
            with self.subTest(field=key):
                values = dict(self.VALUES, **{key: -5})
                with self.assertRaises(ValueError) as caught:
                    map_to_effect(TEMPLATES["survival_hr"], values, "S1", "K1")
                self.assertIn("不能小于 0", str(caught.exception))

    def test_1e300_events_are_value_error(self):
        values = dict(self.VALUES, events_t=1e300, events_c=25)
        with self.assertRaises(ValueError) as caught:
            map_to_effect(TEMPLATES["survival_hr"], values, "S1", "K1")
        self.assertNotIsInstance(caught.exception, OverflowError)

    def test_control_nonnegative_events_unchanged(self):
        """对照组（R1/R2）：零事件与常规事件数照常接受，se 逐位不变。"""
        plain = map_to_effect(TEMPLATES["survival_hr"], dict(self.VALUES), "S1", "K1")
        with_events = map_to_effect(TEMPLATES["survival_hr"],
                                    dict(self.VALUES, events_t=30, events_c=25),
                                    "S1", "K1")
        self.assertEqual(plain["se"], 0.11385084270646685)
        self.assertEqual(with_events["se"], plain["se"])
        self.assertEqual(with_events["input_data"]["values"]["events_t"], 30)
        zero = map_to_effect(TEMPLATES["survival_hr"],
                             dict(self.VALUES, events_t=0, events_c=0), "S1", "K1")
        self.assertEqual(zero["se"], plain["se"])


# ======================================================================
# b15·F4-48 —— 落库 values 必须与计算实际使用的输入一致
# ======================================================================
class F448ValuesMatchInputsNails(unittest.TestCase):
    """修前实测（当前树）：

    .. code-block:: text

        confidence_level=0.90 -> se=0.13566164651852822
            values={'hr':0.5,'ci_low':0.4,'ci_high':0.625}   ← 不含 0.90
        confidence_level=0.95 -> se=0.11385084270646685
            values 同上（不含该键）
        measure 覆盖 "SMD" -> values 无 measure 键

    MM5-12 的注释断言「values 与计算实际使用的输入一致」，但计算函数签名
    里的附加参数（measure 覆盖、confidence_level）只出现在 raw_values。
    修复把这些实参（含默认值）一并并入 values。
    """

    def _survival(self, **overrides):
        values = {"hr": 0.5, "ci_low": 0.4, "ci_high": 0.625}
        values.update(overrides)
        return map_to_effect(TEMPLATES["survival_hr"], values, "S1", "K1")

    def test_confidence_level_used_by_calculator_is_recorded(self):
        effect = self._survival(confidence_level=0.90)
        stored = effect["input_data"]["values"]
        self.assertEqual(stored["confidence_level"], 0.90)
        # 审计复算：只凭 values 就能复现 se
        recomputed = calculate_effect("hazard_ratio_ci", {
            "estimate": stored["hr"], "ci_low": stored["ci_low"],
            "ci_high": stored["ci_high"],
            "confidence_level": stored["confidence_level"],
            "ci_method": "wald_normal"})
        self.assertEqual(recomputed["se"], effect["se"])
        # The frozen SciPy result can differ by one ULP across platforms.
        self.assertAlmostEqual(effect["se"], 0.13566164651852822, delta=1e-15)

    def test_default_confidence_level_is_recorded(self):
        effect = self._survival()
        stored = effect["input_data"]["values"]
        self.assertEqual(stored["confidence_level"], 0.95)
        self.assertEqual(effect["se"], 0.11385084270646685)

    def test_measure_override_is_recorded(self):
        effect = map_to_effect(TEMPLATES["rct_continuous"],
                               {"mean_t": 10.0, "sd_t": 2.0, "n_t": 10,
                                "mean_c": 11.0, "sd_c": 2.0, "n_c": 10,
                                "unit": "mmHg", "measure": "SMD"}, "S1", "K1")
        self.assertEqual(effect["measure"], "SMD")
        self.assertEqual(effect["input_data"]["values"]["measure"], "SMD")

    def test_template_default_measure_is_recorded(self):
        effect = map_to_effect(TEMPLATES["rct_binary"],
                               {"events_t": 10, "n_t": 100,
                                "events_c": 5, "n_c": 100}, "S1", "K1")
        self.assertEqual(effect["measure"], "RR")
        self.assertEqual(effect["input_data"]["values"]["measure"], "RR")

    def test_control_values_keep_every_field_key(self):
        """对照组（R1）：模板字段键一个不少，raw_values 仍是原始提交。"""
        raw = {"hr": 0.5, "ci_low": 0.4, "ci_high": 0.625, "events_t": 30}
        effect = map_to_effect(TEMPLATES["survival_hr"], raw, "S1", "K1")
        stored = effect["input_data"]["values"]
        for key in ("hr", "ci_low", "ci_high", "events_t"):
            self.assertIn(key, stored)
        self.assertEqual(stored["events_t"], 30)
        self.assertEqual(effect["input_data"]["raw_values"], raw)


# ======================================================================
# 端点级钉子（F4-19 端点引用 + 三条 500→400 的用户可见后果）
# ======================================================================
_BASE = Path(os.environ.get("RF_R9_TMP", os.environ.get("RF_R9_TMP_ROOT","E:/rf214tmp")+"/r9/l6d3")).resolve()
_BASE.mkdir(parents=True, exist_ok=True)
_fixture = tempfile.TemporaryDirectory(prefix="rf_r9_l6d3_", dir=str(_BASE))
os.environ["COBOOKSHELF_DATA_DIR"] = _fixture.name
os.environ.setdefault("REVIEWFLOW_MODE", "server")

from fastapi.testclient import TestClient  # noqa: E402

from coscreen import db as db_mod  # noqa: E402
from coscreen import tasks as tasks_mod  # noqa: E402
from coscreen.models import Article  # noqa: E402
from coscreen.review_analysis import save_study  # noqa: E402
from custom_backend import auth as auth_mod  # noqa: E402
from custom_backend.main import DATA_DIR, app  # noqa: E402


class FormatEffectsAndExtractionEndpointTests(unittest.TestCase):
    """真实 app + TestClient（``raise_server_exceptions=False``：撤修复时
    以 500 响应形态现身，而不是在测试内抛出）。接口路径取自
    ``custom_backend/main.py:3950``（format-effects）与 ``:6457``（apply）。"""

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app, raise_server_exceptions=False)
        cls.client.__enter__()
        user = auth_mod.create_user("r9l6d3@example.test", "R9L6d3-Lane-2026")
        cls.auth = {"Authorization": "Bearer " +
                    auth_mod.create_access_token(user["id"], user["username"])}
        cls.task = cls.client.post("/api/tasks", json={"name": "r9-l6d3"},
                                   headers=cls.auth).json()["task_id"]
        info = tasks_mod.find_task(cls.task, DATA_DIR)
        cls.db = info.dir / (tasks_mod.slugify_screener(user["username"]) + ".db")
        db_mod.init_db(cls.db)
        db_mod.upsert_articles(cls.db, [Article(zotero_key="K1", title="T1")], [])
        save_study(cls.db, "S1", "Study 1", ["K1"])

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        _fixture.cleanup()

    def _format_effect(self, values, format_name="hazard_ratio_ci"):
        return self.client.post(
            f"/api/tasks/{self.task}/analysis/format-effects",
            json={"study_id": "S1", "comparison": "C", "outcome": "O",
                  "timepoint": "T", "source_key": "K1",
                  "format_name": format_name, "values": values,
                  "effect_direction": "first_vs_second"},
            headers=self.auth)

    def _apply(self, template_id, values, **extra):
        body = {"template_id": template_id, "study_id": "S1", "values": values,
                "comparison": "C", "outcome": "O", "timepoint": "T",
                "effect_direction": "first_vs_second"}
        body.update(extra)
        return self.client.post(
            f"/api/tasks/{self.task}/extraction/K1/apply", json=body,
            headers=self.auth)

    def test_f4_20_endpoint_accepts_reported_rounded_ci(self):
        """修前：POST /analysis/format-effects → 400 must be symmetric。"""
        response = self._format_effect(_hr())
        self.assertEqual(200, response.status_code, response.text[:300])
        body = response.json()["effect"]
        self.assertEqual(body["estimate"], 1.5)
        self.assertAlmostEqual(
            body["se"], (math.log(2.0) - math.log(1.2)) / (2 * Z95), places=12)

    def test_f4_20_endpoint_still_rejects_off_centre_ci(self):
        response = self._format_effect(_hr(ci_high=3.0))
        self.assertEqual(400, response.status_code, response.text[:300])

    def test_f4_41_endpoint_amplitude_is_400_not_500(self):
        """修前：apply rct_continuous sd=1.5e308 → 500。"""
        response = self._apply("rct_continuous", {
            "mean_t": 1.0, "sd_t": 1.5e308, "n_t": 10,
            "mean_c": 1.5, "sd_c": 1.0, "n_c": 10, "unit": "x"})
        self.assertEqual(400, response.status_code, response.text[:300])
        self.assertIn("超出可计算的数值范围", response.text)

    def test_f4_44_endpoint_big_integer_is_400_not_500(self):
        response = self._apply("rct_continuous", {
            "mean_t": 1.0, "sd_t": 1.0, "n_t": 10 ** 400,
            "mean_c": 1.5, "sd_c": 1.0, "n_c": 10, "unit": "x"})
        self.assertEqual(400, response.status_code, response.text[:300])

    def test_f4_40_endpoint_dta_overflow_is_400_not_500(self):
        """修前：tp=tn="1e200"（fp=fn=1）在 :179 整数真除法处 OverflowError → 500。
        四格同量级（10**200 对 10**200）商恰为 1、本就可表示，另见库级对照组。"""
        response = self._apply(
            "diagnostic_2x2", {"tp": "1e200", "fp": "1",
                               "fn": "1", "tn": "1e200"},
            effect_direction="not_applicable", index_test="IT",
            target_condition="TC", threshold="TH", reference_standard="RS")
        self.assertEqual(400, response.status_code, response.text[:300])

    def test_control_endpoints_normal_paths_unchanged(self):
        """对照组（R1/R2）：常规连续臂与常规 2×2 仍 200 且数值不变。"""
        normal = self._apply("rct_continuous", {
            "mean_t": 1.0, "sd_t": 1.0, "n_t": 10,
            "mean_c": 1.5, "sd_c": 1.0, "n_c": 10, "unit": "x"})
        self.assertEqual(200, normal.status_code, normal.text[:300])
        body = normal.json()
        self.assertEqual(body["estimate"], -0.5)
        self.assertEqual(body["se"], 0.4472135954999579)


# ======================================================================
# b14·F4-18 —— 引导注册表漏列 ci_method（r9 由 L2 落点收口）
# ======================================================================
class F418GuidanceContractNails(unittest.TestCase):
    """F4-18 已落地：entry_guidance.py 的 effect_ci 与 hazard_ratio_ci 两条
    ``_path()`` 的 required_fields 补了 ``"ci_method"``（计算器
    ``effect_size_formats._fields`` 要求键集精确相等，且 ``_ci_result`` 只
    接受 ``wald_normal``）。修复前本类两条断言为红（skip 已在 L2 落地后解除）。
    """

    def test_guidance_advertises_every_required_calculator_field(self):
        from coscreen.entry_guidance import _SHAPE_PATHS

        advertised = set(_SHAPE_PATHS["hazard_ratio_ci"][0]["required_fields"])
        # 计算器 hazard_ratio_ci 的键集（effect_size_formats.calculate_effect:131）
        self.assertLessEqual({"estimate", "ci_low", "ci_high",
                              "confidence_level", "ci_method"}, advertised)
        advertised_ci = set(_SHAPE_PATHS["effect_ci"][0]["required_fields"])
        self.assertLessEqual({"measure", "estimate", "ci_low", "ci_high",
                              "confidence_level", "scale", "ci_method"},
                             advertised_ci)


if __name__ == "__main__":
    unittest.main()
