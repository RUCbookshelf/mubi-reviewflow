"""r9 泳道 L6d2 钉子：L6 统计合成Ⅱ 的 coding_agreement / grade_assessor（13 条）。

对应 `out/release_plan/inventory_夜扫286确认分组.md` §3.6 的本泳道条目：
b01·F3-1、b04·F3-3、b11·F3-2、b12·F3-4/F3-5/F3-6、b06·F4-58/F4-59、
b15·F4-60/F4-61/F4-62/F4-63/F4-64。

破坏即红（R4）：每个 test 的第一个断言钉住修复后的行为，把对应修复行回退
（工作树基线 bfd07ae 的两个模块版本）即变红；标「常规对照」的断言逐位钉死
修复前的合法输入输出（R1/R2：不新增拒绝合法输入、常规结果逐位不变）。

修法口径（模块层单域，R5）：
- F3-1/F3-3 数值族：派生量（逐项差、均值）超出 JSON/IEEE-754 double 可表示
  范围时按库内既有 display()/F2-84 降级口径置 None（端点 200，不拒绝合法有限
  输入）；可表示的差值/均值逐位不变，均值在求和中途溢出但真值可表示时用精确
  有理数补算（两个 1.7e308 差值 → 1.7e308）。
- F3-2/F3-4/F3-5/F3-6：记录形状统一在 ``_field_key`` 入口做映射/可哈希校验，
  rater_id 不再 ``str()`` 静默归一，缺 ``field_key`` 键按独立字段参与单字段门。
- F4-58/F4-60/F4-64：``build_sof_table`` 的宽松校验与 ``final_quality`` 严格
  校验用同一份补全后的 downgrades；upgrades 非 dict 转 ValueError；None 结局/
  时间点按空值折叠到「（未命名结局）」/无时间点。
- F4-59/F4-62：倒置 CI、越界 p 值与负研究数转 ValueError（同文件既有拒绝口径）。
- F4-61：成员判断封装 ``_member``，不可哈希值按「不属于」处理 → ValueError。
- F4-63：``user_overrides`` 覆盖 indirectness 后回填 signal_sources.user_selected。
"""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from coscreen.coding_agreement import summarize_agreement
from coscreen.grade_assessor import (
    DOWNGRADE_FACTORS, assess_grade, build_sof_table, final_quality,
    suggest_imprecision_downgrade, suggest_publication_bias_downgrade, suggest_rob_downgrade)


def _numeric(records, tolerance=1.0):
    return summarize_agreement(records, ("a", "b"), "numeric", absolute_tolerance=tolerance)


def _text(records, raters=("a", "b")):
    return summarize_agreement(records, raters, "text")


def _pair(left, right, item="i", raters=("a", "b")):
    return [{"item_key": item, "rater_id": raters[0], "value": left},
            {"item_key": item, "rater_id": raters[1], "value": right}]


FULL = {factor: "none" for factor in DOWNGRADE_FACTORS}


def _minimal_app():
    """与夜扫探针同形：最小 FastAPI（默认 JSONResponse allow_nan=False）。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()

    @app.get("/agreement")
    def agreement():
        return _numeric(_pair(EXTREME_INT_A, EXTREME_INT_B))

    @app.get("/agreement-float")
    def agreement_float():
        return _numeric(_pair(EXTREME_FLOAT_A, EXTREME_FLOAT_B))

    return TestClient(app, raise_server_exceptions=False)


EXTREME_INT_A, EXTREME_INT_B = 10 ** 308, -(10 ** 308)
EXTREME_FLOAT_A, EXTREME_FLOAT_B = 1.7e308, -1.7e308


class CodingAgreementExtremeNumbersTests(unittest.TestCase):
    """b01·F3-1 / b04·F3-3：极端有限数对的派生量不得 500。"""

    def test_f3_1_integer_extreme_pair_degrades_instead_of_overflowing(self):
        # 修前：sum([2000…])/1 → OverflowError（非 ValueError）穿透端点 → 500。
        result = _numeric(_pair(EXTREME_INT_A, EXTREME_INT_B))
        self.assertIsNone(result["item_results"][0]["absolute_difference"])
        self.assertFalse(result["item_results"][0]["within_tolerance"])
        self.assertIsNone(result["mean_absolute_difference"])
        self.assertEqual(result["agreement_rate"], 0.0)
        json.dumps(result, allow_nan=False)  # 渲染期不再抛 ValueError

    def test_f3_1_integer_extreme_pair_served_as_200(self):
        # 夜扫探针形态：最小 FastAPI + TestClient，修前渲染/端点 500。
        response = _minimal_app().get("/agreement")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertIsNone(body["mean_absolute_difference"])
        self.assertIsNone(body["item_results"][0]["absolute_difference"])

    def test_f3_3_float_extreme_pair_degrades_instead_of_inf(self):
        # 修前：abs(1.7e308 - -1.7e308) = inf 进入响应体，渲染期 ValueError → 500。
        result = _numeric(_pair(EXTREME_FLOAT_A, EXTREME_FLOAT_B))
        self.assertIsNone(result["item_results"][0]["absolute_difference"])
        self.assertIsNone(result["mean_absolute_difference"])
        json.dumps(result, allow_nan=False)

    def test_f3_3_float_extreme_pair_served_as_200(self):
        response = _minimal_app().get("/agreement-float")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIsNone(response.json()["mean_absolute_difference"])

    def test_f3_1_mean_recomputes_when_sum_overflows_but_mean_representable(self):
        # 两个 1e308 差值：逐项可表示，逐项和溢出 inf，均值仍可表示 → 补算 1e308。
        records = [{"item_key": "i", "rater_id": "a", "value": 1e308},
                   {"item_key": "i", "rater_id": "b", "value": 0.0},
                   {"item_key": "j", "rater_id": "a", "value": -1e308},
                   {"item_key": "j", "rater_id": "b", "value": 0.0}]
        result = _numeric(records)
        self.assertEqual(result["mean_absolute_difference"], 1e308)
        json.dumps(result, allow_nan=False)

    def test_control_numeric_results_bit_identical(self):
        # 常规对照：1e300 对（可表示）、普通整数/浮点对，逐位不变。
        big = _numeric(_pair(1e300, -1e300))
        self.assertEqual(big["item_results"][0]["absolute_difference"], 2e300)
        self.assertEqual(big["mean_absolute_difference"], 2e300)
        self.assertEqual(big["agreement_rate"], 0.0)
        small = _numeric([{"item_key": "i1", "rater_id": "a", "value": 1},
                          {"item_key": "i1", "rater_id": "b", "value": 1.25},
                          {"item_key": "i2", "rater_id": "a", "value": 3},
                          {"item_key": "i2", "rater_id": "b", "value": 1}], tolerance=0.5)
        self.assertEqual(small, {
            "field_type": "numeric", "rater_ids": ["a", "b"], "n_items": 2, "n_paired": 2,
            "missing_by_rater": {"a": [], "b": []},
            "item_results": [
                {"item_key": "i1", "within_tolerance": True, "absolute_difference": 0.25},
                {"item_key": "i2", "within_tolerance": False, "absolute_difference": 2}],
            "agreement_rate": 0.5, "absolute_tolerance": 0.5,
            "mean_absolute_difference": 1.125})


class CodingAgreementRecordShapeTests(unittest.TestCase):
    """b11·F3-2 / b12·F3-4/F3-5/F3-6：记录形状错误一律 ValueError。"""

    def test_f3_2_unhashable_field_key_is_value_error(self):
        records = [{"item_key": "i", "rater_id": "a", "field_key": ["F"], "value": "x"},
                   {"item_key": "i", "rater_id": "b", "field_key": ["F"], "value": "y"}]
        with self.assertRaises(ValueError):
            _text(records)

    def test_f3_2_non_mapping_record_is_value_error(self):
        for record in ("not-a-dict", None, 5, ["x"]):
            with self.assertRaises(ValueError):
                _text([record])
            with self.assertRaises(ValueError):
                _numeric([record])

    def test_f3_4_integer_rater_id_not_silently_coerced(self):
        # 修前：str(1) == '1' 静默匹配，n_items=1 且 missing_by_rater 掩盖问题。
        records = [{"item_key": "i", "rater_id": 1, "value": "x"},
                   {"item_key": "i", "rater_id": "2", "value": "x"}]
        with self.assertRaises(ValueError):
            _text(records, raters=("1", "2"))
        # 常规对照：两侧均为字符串照常配对。
        control = _text([{"item_key": "i", "rater_id": "1", "value": "x"},
                         {"item_key": "i", "rater_id": "2", "value": "x"}], raters=("1", "2"))
        self.assertEqual(control["n_paired"], 1)
        self.assertEqual(control["agreement_rate"], 1.0)

    def test_f3_5_annotated_and_unannotated_records_cannot_mix(self):
        # 修前：缺键记录不参与去重计数，len==1 放行 → 混比 ok=true。
        records = [{"item_key": "i", "rater_id": "a", "field_key": "F", "value": "x"},
                   {"item_key": "j", "rater_id": "b", "value": "y"}]
        with self.assertRaises(ValueError):
            _text(records)
        # 两条都缺键：同属"未标注"字段，仍可比（不新增拒绝）。
        both_missing = _text([{"item_key": "i", "rater_id": "a", "value": "x"},
                              {"item_key": "i", "rater_id": "b", "value": "x"}])
        self.assertEqual(both_missing["n_paired"], 1)
        # 两条同键：常规对照不回归。
        same_key = _text([{"item_key": "i", "rater_id": "a", "field_key": "F", "value": "x"},
                          {"item_key": "i", "rater_id": "b", "field_key": "F", "value": "x"}])
        self.assertEqual(same_key["n_paired"], 1)

    def test_f3_6_non_dict_records_raise_value_error_not_attribute_error(self):
        for record in ("not-a-dict", None):
            with self.assertRaises(ValueError):
                _text([record])

    def test_control_text_multiselect_categorical_bit_identical(self):
        text = _text([{"item_key": "i1", "rater_id": "a", "value": "x"},
                      {"item_key": "i1", "rater_id": "b", "value": "x"},
                      {"item_key": "i2", "rater_id": "a", "value": "y"},
                      {"item_key": "i2", "rater_id": "b", "value": "z"}])
        self.assertEqual(text, {
            "field_type": "text", "rater_ids": ["a", "b"], "n_items": 2, "n_paired": 2,
            "missing_by_rater": {"a": [], "b": []},
            "item_results": [{"item_key": "i1", "exact_match": True},
                             {"item_key": "i2", "exact_match": False}],
            "agreement_rate": 0.5})
        multi = summarize_agreement(
            [{"item_key": "i1", "rater_id": "a", "value": ["x", "y"]},
             {"item_key": "i1", "rater_id": "b", "value": ["y", "z"]},
             {"item_key": "i2", "rater_id": "a", "value": ["q"]},
             {"item_key": "i2", "rater_id": "b", "value": ["q"]}],
            ("a", "b"), "multiselect")
        self.assertEqual(multi["item_results"][0]["jaccard"], 1 / 3)
        self.assertEqual(multi["agreement_rate"], 0.5)
        kappa = summarize_agreement(
            [{"item_key": "i1", "rater_id": "a", "value": "x"},
             {"item_key": "i1", "rater_id": "b", "value": "x"},
             {"item_key": "i2", "rater_id": "a", "value": "y"},
             {"item_key": "i2", "rater_id": "b", "value": "y"},
             {"item_key": "i3", "rater_id": "a", "value": "y"},
             {"item_key": "i3", "rater_id": "b", "value": "z"}],
            ("a", "b"), "categorical")
        self.assertEqual(kappa["cohen_kappa"], 0.49999999999999994)
        self.assertEqual(kappa["kappa_status"], "available")


class GradeSofTests(unittest.TestCase):
    """b06·F4-58/59、b15·F4-60/61/62/63/64。"""

    def test_f4_58_missing_downgrades_computed_with_same_defaults(self):
        # 修前：宽松校验按 none 放行后，final_quality 严格校验抛「缺少降级因子」。
        row = build_sof_table([{"outcome": "O", "downgrades": {"rob": "none"}}])["rows"][0]
        self.assertEqual(row[0], "O")
        self.assertEqual(row[7], "高 ⊕⊕⊕⊕")
        # 对照：完整 downgrades 逐位不变。
        control = build_sof_table([{"outcome": "O", "downgrades": dict(FULL)}])["rows"][0]
        self.assertEqual(control, row)

    def test_f4_58_upgrades_still_require_observational_design(self):
        # 自相矛盾的另一半（设计不允许升级）仍按严格口径拒绝，不被宽松化吞掉。
        with self.assertRaises(ValueError):
            build_sof_table([{"outcome": "O", "downgrades": dict(FULL),
                              "upgrades": {"large_effect": "plus_one"}}])

    def test_f4_59_inverted_ci_rejected(self):
        for low, high in ((5.0, 2.0), (5.0, -2.0)):
            with self.assertRaises(ValueError):
                suggest_imprecision_downgrade(
                    {"ci_low": low, "ci_high": high, "null_value": 0.0, "total_events": 500})
        # 常规对照逐位不变：有序宽 CI → serious(ci_too_wide)，有序窄 CI → none。
        wide = suggest_imprecision_downgrade(
            {"ci_low": 1.0, "ci_high": 20.0, "null_value": 1.0, "total_events": 500})
        self.assertEqual(wide, ("serious", {
            "available": True, "ci_low": 1.0, "ci_high": 20.0, "null_value": 1.0,
            "ci_width_ratio": 20.0, "ci_includes_null": True, "total_events": 500,
            "ois_threshold": 300, "rule": ["ci_too_wide"]}))
        narrow = suggest_imprecision_downgrade(
            {"ci_low": 1.0, "ci_high": 2.0, "null_value": 1.0, "total_events": 500})
        self.assertEqual(narrow[0], "none")
        self.assertEqual(narrow[1]["rule"], "no_imprecision_signal")

    def test_f4_60_non_dict_upgrades_is_value_error(self):
        for upgrades in ("plus_one", ["plus_one"], 1):
            with self.assertRaises(ValueError):
                build_sof_table([{"outcome": "O", "downgrades": dict(FULL), "upgrades": upgrades}])
        # 对照：dict 升级（观察性）与 None 逐位不变。
        upgraded = build_sof_table([{"outcome": "O", "initial_design": "observational",
                                     "downgrades": dict(FULL),
                                     "upgrades": {"large_effect": "plus_one"}}])["rows"][0]
        self.assertEqual(upgraded[7], "中 ⊕⊕⊕○")
        plain = build_sof_table([{"outcome": "O", "downgrades": dict(FULL),
                                  "upgrades": None}])["rows"][0]
        self.assertEqual(plain[7], "高 ⊕⊕⊕⊕")

    def test_f4_61_unhashable_levels_are_value_error(self):
        cases = [
            lambda: assess_grade("rct", user_overrides={"rob": ["serious"]}),
            lambda: assess_grade("rct", indirectness=["serious"]),
            lambda: assess_grade(["rct"]),
            lambda: suggest_rob_downgrade({"overall": ["high"]}),
            lambda: final_quality("rct", dict(FULL, rob=["serious"])),
        ]
        for case in cases:
            with self.assertRaises(ValueError):
                case()
        # 对照：tuple 键（可哈希）仍走既有的"未知判定" ValueError。
        with self.assertRaises(ValueError):
            suggest_rob_downgrade({"overall_counts": {("high",): 1}})
        # 对照：合法覆盖逐位不变。
        overridden = assess_grade("rct", user_overrides={"rob": "serious"})
        self.assertEqual(overridden["downgrades"]["rob"], "serious")
        self.assertEqual(overridden["overrides_applied"],
                         {"rob": {"auto": "none", "user": "serious"}})

    def test_f4_62_p_value_and_study_count_ranges(self):
        for payload in ({"p_value": 1.5, "n_studies": 20},
                        {"p_value": -0.2, "n_studies": 20},
                        {"p_value": 0.01, "n_studies": -5}):
            with self.assertRaises(ValueError):
                suggest_publication_bias_downgrade(payload)
        # 常规对照逐位不变。
        self.assertEqual(suggest_publication_bias_downgrade({"p_value": 0.01, "n_studies": 20}),
                         ("serious", {"available": True,
                                      "rule": "publication_bias_test_significant",
                                      "p_value": 0.01, "p_threshold": 0.05, "n_studies": 20}))
        self.assertEqual(suggest_publication_bias_downgrade({"p_value": 0.01, "n_studies": 5}),
                         ("none", {"available": True,
                                   "rule": "insufficient_studies_no_auto_downgrade",
                                   "n_studies": 5, "minimum_studies": 10, "p_value": 0.01}))
        # 边界值 0 与 1 是合法 p 值，不得拒绝（R2）。
        for edge in (0.0, 1.0):
            level, _ = suggest_publication_bias_downgrade({"p_value": edge, "n_studies": 20})
            self.assertIn(level, ("none", "serious"))

    def test_f4_63_indirectness_override_backfills_signal_sources(self):
        result = assess_grade("rct", indirectness="none",
                              user_overrides={"indirectness": "very_serious"})
        self.assertEqual(result["downgrades"]["indirectness"], "very_serious")
        self.assertEqual(result["signal_sources"]["indirectness"]["user_selected"],
                         "very_serious")
        self.assertEqual(result["overrides_applied"],
                         {"indirectness": {"auto": "none", "user": "very_serious"}})
        # 对照：不覆盖时 user_selected 保持构造参数。
        plain = assess_grade("rct", indirectness="serious")
        self.assertEqual(plain["signal_sources"]["indirectness"]["user_selected"], "serious")

    def test_f4_64_none_outcome_falls_back_to_unnamed(self):
        row = build_sof_table([{"outcome": None, "timepoint": None, "downgrades": dict(FULL),
                                "final_quality": "high"}])["rows"][0]
        self.assertEqual(row[0], "（未命名结局）")
        # 对照逐位不变：空串回退、命名结局带时间点。
        empty = build_sof_table([{"outcome": "", "downgrades": dict(FULL),
                                  "final_quality": "high"}])["rows"][0]
        self.assertEqual(empty[0], "（未命名结局）")
        named = build_sof_table([{"outcome": "Mortality", "timepoint": "12m",
                                  "downgrades": dict(FULL),
                                  "final_quality": "high"}])["rows"][0]
        self.assertEqual(named[0], "Mortality（12m）")
        self.assertEqual(named[7], "高 ⊕⊕⊕⊕")

    def test_control_assess_grade_and_sof_bit_identical(self):
        # 常规对照（R1）：完整评估与 SoF 行逐位不变。
        full = assess_grade(
            "rct",
            rob_assessment={"overall": "high"},
            heterogeneity={"i2_percent": 80.0, "tau2_p": 0.01},
            imprecision={"ci_low": 1.5, "ci_high": 60.0, "null_value": 1.0,
                         "total_events": 500},
            publication_bias={"p_value": 0.3, "n_studies": 15},
            indirectness="serious")
        self.assertEqual(full["downgrades"], {"rob": "serious", "inconsistency": "serious",
                                              "imprecision": "serious",
                                              "publication_bias": "none",
                                              "indirectness": "serious"})
        self.assertEqual(full["final_quality"], "very_low")
        self.assertEqual(full["signal_sources"]["indirectness"],
                         {"available": False,
                          "reason": "间接性无法从数据自动推导，完全依赖用户判断",
                          "user_selected": "serious"})
        observational = assess_grade(
            "observational", {"i2_percent": 60.0, "q_p": 0.01},
            imprecision={"ci_low": 0.5, "ci_high": 3.0, "null_value": 1.0,
                         "total_events": 120},
            publication_bias={"p_value": 0.02, "n_studies": 12})
        self.assertEqual(observational["downgrades"],
                         {"rob": "none", "inconsistency": "none", "imprecision": "serious",
                          "publication_bias": "serious", "indirectness": "none"})
        self.assertEqual(observational["warnings"],
                         ["降级幅度超过下限，最终质量按极低处理（原始分 0）"])
        row = build_sof_table([{
            "outcome": "Mortality", "timepoint": "12m",
            "downgrades": {"rob": "serious", "inconsistency": "none",
                           "indirectness": "none", "imprecision": "very_serious",
                           "publication_bias": "none"},
            "final_quality": "low", "n_participants": 500, "n_studies": 4,
            "override_notes": "note"}])
        self.assertEqual(row["rows"], [["Mortality（12m）", "500 （4 项研究）", "严重(-1)",
                                        "无降级", "无降级", "非常严重(-2)", "无降级",
                                        "低 ⊕⊕○○", "note"]])


if __name__ == "__main__":
    unittest.main()
