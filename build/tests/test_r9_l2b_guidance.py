"""r9 L2b 泳道钉子 —— 引导目录（b05·F4-22/F4-23、b14·F4-24/F4-21）。

每条钉子按 `inventory_夜扫286确认分组.md` §3.2 的探针形态复现；把对应修复
回退，该用例必红：

- **F4-22【高】** ``single_proportion_events_n`` 的 caution 宣称「0/全部事件按
  内置校正处理」，而被指向的 ``calculate_single_group_effect`` 对边界比例直接
  ``ValueError('boundary proportions … no continuity correction is applied')``
  ——按引导构造必 400。修法：把**声称**收窄到实现（标签宣称 -> 放开实现是另
  一条路，但边界比例确实没有校正公式，不能凭空许诺）。
- **F4-23【高】** ``zero_cell_binary`` 的 ``produces_measure`` 宣称 RR/OR/RD，
  被指向的 ``calculate_zero_cell_effect`` 只收 OR/RR（``measure=RD`` → 400）。
  修法同上：声称收窄到实现。
- **F4-24【高】** ``cluster_design_effect`` 两条路径的标签都宣称 SMD，但
  ``calculate_cluster_trial_effect`` / ``..._variable_size_effect`` 对
  ``outcome_type=continuous`` 只收 ``MD``（``measure=SMD`` → 400），前端
  ``anClusterMeasure`` 也没有 SMD 选项。修法同上：标签改为实际的 MD/二分类三
  个测度。
- **F4-21** ``combinations`` 直接引用模块级静态表里的 dict，改返回负载即跨
  调用/跨 shape 污染（``combinations[0]['note']='POLLUTED'`` 后仍 POLLUTED）。
  修法：与 ``candidates`` 同口径 deepcopy。

说明（依据）：上述三处「收窄标签宣称」的选择理由是——同族条目要求 R2（不得
新增拒绝合法输入的严格性），而这三条的正确做法是把**声称与实现对齐**；实现端
（zero_cell_effects / single_group_formats / cluster_trial_effects）的测度域在
各自 docstring 与测试里已被固定为当前集合，新增 RD/SMD/连续性校正属于功能
扩张而非缺陷修复。故本泳道只改引导目录（展示层宣称），不动计算实现。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from coscreen import entry_guidance as guidance


def _paths_for(shape: str) -> list[dict]:
    return [p for p in guidance.recommend_entry_routes([shape])["candidates"]
            if p["shape"] == shape]


class SingleProportionCautionMatchesImplementation(unittest.TestCase):
    """F4-22：caution 不得宣称实现会做连续性校正。"""

    def test_caution_does_not_claim_continuity_correction(self):
        cautions = " ".join(_paths_for("single_proportion_events_n")[0]["cautions"])
        # 复现口径：旧文案「0 或全部事件时按模块内置校正处理」
        self.assertNotIn("按模块内置校正", cautions,
                         "caution 仍宣称边界比例会做内置校正（实现直接 400）")
        self.assertIn("不做连续性校正", cautions)
        self.assertIn("边界比例", cautions)

    def test_implementation_still_rejects_boundary_proportions(self):
        """对照：实现侧行为不变（不因改文案而放宽或收紧）。"""
        from coscreen.single_group_formats import calculate_single_group_effect

        with self.assertRaisesRegex(ValueError, "boundary proportions"):
            calculate_single_group_effect(
                "single_proportion_events_n", {"events": 0, "total": 10})
        with self.assertRaisesRegex(ValueError, "boundary proportions"):
            calculate_single_group_effect(
                "single_proportion_events_n", {"events": 10, "total": 10})
        ok = calculate_single_group_effect(
            "single_proportion_events_n", {"events": 3, "total": 10})
        self.assertEqual(ok["measure"], "LOGIT_PROP")

    def test_normal_proportion_hint_still_present(self):
        cautions = " ".join(_paths_for("single_proportion_events_n")[0]["cautions"])
        self.assertIn("logit", cautions)


class ZeroCellMeasureLabelMatchesImplementation(unittest.TestCase):
    """F4-23：produces_measure 不得宣称 RD。"""

    def test_label_lists_only_supported_measures(self):
        path = _paths_for("zero_cell_binary")[0]
        self.assertEqual(path["produces_measure"], "RR / OR")
        self.assertNotIn("RD", path["produces_measure"])

    def test_implementation_accepts_exactly_rr_and_or(self):
        """对照：实现侧测度域不变。"""
        from coscreen.zero_cell_effects import calculate_zero_cell_effect

        base = dict(events_t=1, total_t=10, events_c=2, total_c=10, correction="none")
        for measure in ("OR", "RR"):
            result = calculate_zero_cell_effect(**base, measure=measure)
            self.assertEqual(result["measure"], measure)
        with self.assertRaisesRegex(ValueError, "measure must be OR or RR"):
            calculate_zero_cell_effect(**base, measure="RD")


class ClusterDesignEffectMeasureLabel(unittest.TestCase):
    """F4-24：整群标签不得宣称 SMD。"""

    def test_labels_do_not_claim_smd(self):
        for path in _paths_for("cluster_design_effect"):
            self.assertNotIn("SMD", path["produces_measure"],
                             f"整群路径仍宣称 SMD: {path['entry_function']}")

    def test_labels_still_list_md_and_binary_measures(self):
        for path in _paths_for("cluster_design_effect"):
            for token in ("MD", "RR", "OR", "RD"):
                self.assertIn(token, path["produces_measure"])

    def test_implementation_matches_for_continuous_outcomes(self):
        """对照：连续结局只收 MD（SMD 被拒）——标签必须与之一致。"""
        from coscreen.cluster_trial_effects import calculate_cluster_trial_effect

        values = {
            "outcome_type": "continuous", "measure": "MD", "icc": 0.1,
            "analysis_status": "unadjusted",
            "arms": {
                "treatment": {"clusters": 10, "participants": 100, "mean": 5.0, "sd": 1.0},
                "control": {"clusters": 10, "participants": 100, "mean": 4.0, "sd": 1.0},
            },
        }
        self.assertEqual(calculate_cluster_trial_effect(values)["measure"], "MD")
        for bad in ("SMD", "OR"):
            with self.assertRaisesRegex(ValueError, "measure must be one of"):
                calculate_cluster_trial_effect({**values, "measure": bad})


class CombinationRulesAreDeepCopied(unittest.TestCase):
    """F4-21：返回的组合建议不得与模块级静态表共享可变对象。"""

    def test_mutating_returned_combination_does_not_pollute_later_calls(self):
        """复现口径：改 combinations[0]['note']='POLLUTED' 后再调仍 POLLUTED。"""
        first = guidance.recommend_entry_routes(["arm_mean_sd_n"])
        first["combinations"][0]["note"] = "POLLUTED"
        second = guidance.recommend_entry_routes(["arm_mean_sd_n"])
        self.assertNotEqual(second["combinations"][0]["note"], "POLLUTED")

    def test_mutating_returned_combination_does_not_touch_static_table(self):
        first = guidance.recommend_entry_routes(["arm_mean_sd_n"])
        first["combinations"][0]["note"] = "POLLUTED"
        self.assertNotEqual(guidance._COMBINATION_RULES[0]["note"], "POLLUTED")

    def test_shape_set_does_not_affect_other_shapes(self):
        first = guidance.recommend_entry_routes(["hazard_ratio_ci", "oe_v"])
        first["combinations"][0]["note"] = "POLLUTED"
        other = guidance.recommend_entry_routes(["median_survival", "hazard_ratio_ci"])
        self.assertNotEqual(other["combinations"][0]["note"], "POLLUTED")

    def test_candidates_deepcopy_still_holds(self):
        """对照：candidates 的既有 deepcopy 行为不变。"""
        first = guidance.recommend_entry_routes(["arm_mean_sd_n"])
        first["candidates"][0]["cautions"][0] = "CAND-POLLUTED"
        again = guidance.recommend_entry_routes(["arm_mean_sd_n"])
        self.assertNotEqual(again["candidates"][0]["cautions"][0], "CAND-POLLUTED")

    def test_combination_content_unchanged(self):
        """对照：组合建议的内容与非受影响字段逐字不变。"""
        payload = guidance.recommend_entry_routes(["arm_mean_sd_n", "hazard_ratio_ci"])
        rules = [(rule["if"], rule["shapes"], rule["note"])
                 for rule in payload["combinations"]]
        self.assertEqual(rules, [
            (rule["if"], rule["shapes"], rule["note"])
            for rule in guidance._COMBINATION_RULES
            if rule["if"] <= {"arm_mean_sd_n", "hazard_ratio_ci"}
        ])


if __name__ == "__main__":
    unittest.main()
