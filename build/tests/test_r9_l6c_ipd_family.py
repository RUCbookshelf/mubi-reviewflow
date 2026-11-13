"""r9 L6c 泳道钉子：IPD 家族（ipd_analysis / ipd_adjusted_cox / ipd_cox_extended /
ipd_interaction）。

覆盖清单 §3.6 的本泳道 11 条（E:/mubi-reviewflow/out/release_plan/
inventory_夜扫286确认分组.md，行号按开工时 bfd07ae 读取；探针口径逐字取自
夜间复审批次原文）。每条在基线 bfd07ae 上实测为红（修前形态见各类首注释），
修复后为绿；标「逐位对照」的用例钉住基线返回值，修复后必须仍逐位相等
（R1/R2：常规输入结果不变、不新增对合法输入的拒绝）。

- b06·F4-96  ipd_analysis 结局值为 401 位整数 → math.isfinite OverflowError → 500；
- b06·F4-98  ipd_analysis 行 arm 为 list/dict → `not in arms` unhashable TypeError → 500；
- b06·F4-93  ipd_adjusted_cox Newton×Armijo 无时间预算，3000 行合法请求 82.2s；
- b06·F4-94  ipd_adjusted_cox 完全分离被当成功拟合：se≈3.3e4、hr_ci=None；
- b16·F4-95  ipd_adjusted_cox:174 `fsum(relative_event_eta)` 的 OverflowError 未转 ValueError；
- b05·F4-101 ipd_cox_extended 协变量名 event/duration 与保留字段塌缩，静默「校正结局/时间」；
- b05·F4-102 ipd_cox_extended 协变量个数无上限（k=1e5 → 74.5 GiB）；
- b05·F4-104 ipd_cox_extended 两阶段完全分离 → HR≈1、tau2=0、I2=0 的误导性结果；
- b05·F4-105 ipd_interaction 研究数 k 无上限（k=33333 → intercepts ~99 GiB）；
- b05·F4-106 ipd_interaction:120 研究均值 `math.fsum` 可在全部输入合法时抛 OverflowError；
- b14·F4-107 ipd_interaction `_exp_or_none` 用 `or None` 把下溢 0.0 与溢出混为一谈。

同族修法形态（清单 §6.3）：本泳道涉及 fsum 溢出族的三个载体
（F4-95 / F4-106，以及 ipd_cox_extended 的池化求和），统一走同一入口
``_finite_fsum(terms, message)``：仍是同一条 ``math.fsum``，仅把
``OverflowError`` 转成本模块既有的 ``ValueError`` 4xx 契约；常规输入逐位不变。
"""
import math
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

REPO = Path(__file__).resolve().parents[2]

from _numeric_assertions import assert_numeric_baseline
from coscreen import ipd_adjusted_cox  # noqa: E402
from coscreen import ipd_analysis  # noqa: E402
from coscreen import ipd_cox_extended  # noqa: E402
from coscreen import ipd_interaction  # noqa: E402

_TMP = tempfile.TemporaryDirectory(prefix="rf_r9_l6c_", ignore_cleanup_errors=True)


def _ipd_rows(studies=("S1", "S2"), per_study=20, extra=None):
    """确定性多协变量 IPD 批（无随机数）：每研究轮流分配两臂、事件与年龄。"""
    rows = []
    for sid in studies:
        for index in range(per_study):
            row = {"study_id": sid, "duration": float(per_study - index),
                   "event": 1 if index % 3 else 0,
                   "treatment": 1 if index % 2 else 0,
                   "age": 40.0 + (index % 9)}
            if extra:
                row.update(extra)
            rows.append(row)
    return rows


#: 12 行、两臂都有事件、可识别的调整 Cox 批（与 MM10-04 的不同：单研究、法线附近）。
_ADJUSTED_CONTROL = [
    {"study_id": "S1", "duration": 9.0, "event": 1, "treatment": 1, "covariate": 0.4},
    {"study_id": "S1", "duration": 8.5, "event": 0, "treatment": 1, "covariate": 0.1},
    {"study_id": "S1", "duration": 4.0, "event": 1, "treatment": 0, "covariate": -0.3},
    {"study_id": "S1", "duration": 3.5, "event": 1, "treatment": 1, "covariate": -0.2},
    {"study_id": "S1", "duration": 7.0, "event": 0, "treatment": 0, "covariate": 0.7},
    {"study_id": "S1", "duration": 2.5, "event": 1, "treatment": 1, "covariate": -0.6},
    {"study_id": "S1", "duration": 8.0, "event": 1, "treatment": 0, "covariate": 0.9},
    {"study_id": "S1", "duration": 6.5, "event": 0, "treatment": 0, "covariate": 0.2},
    {"study_id": "S1", "duration": 3.0, "event": 1, "treatment": 1, "covariate": 0.05},
    {"study_id": "S1", "duration": 5.5, "event": 0, "treatment": 1, "covariate": 0.33},
    {"study_id": "S1", "duration": 7.5, "event": 0, "treatment": 0, "covariate": -0.8},
    {"study_id": "S1", "duration": 2.0, "event": 1, "treatment": 0, "covariate": 0.6},
]

#: b06·F4-94 复审同形夹具：单研究、全事件、协变量随 duration 递减 →
#: 每个风险集里事件行的协变量最大（完全分离）。
_SEPARATED_ROWS = [
    {"study_id": "S1", "duration": float(index), "event": 1,
     "treatment": 1 if index % 2 else 0, "covariate": float(10 - index)}
    for index in range(1, 11)
]
_SEPARATED_ROWS_2 = [
    dict(row, study_id=study) for study in ("S1", "S2") for row in _SEPARATED_ROWS
]

#: r7 内核批（两研究、双臂事件、可识别）：MM11-03 与两阶段数值的对照祖先。
_TWO_STAGE_BASE = [
    ("S1", 9.0, 1, 1), ("S1", 8.5, 0, 1), ("S1", 4.0, 1, 0), ("S1", 3.5, 1, 1),
    ("S1", 7.0, 0, 0), ("S1", 2.5, 1, 1), ("S1", 8.0, 1, 0), ("S1", 6.5, 0, 0),
    ("S1", 3.0, 1, 1), ("S1", 5.5, 0, 1), ("S1", 7.5, 0, 0), ("S1", 2.0, 1, 0),
    ("S2", 6.0, 1, 0), ("S2", 5.5, 1, 1), ("S2", 2.0, 1, 0), ("S2", 4.5, 0, 1),
    ("S2", 7.5, 0, 0), ("S2", 1.5, 1, 1), ("S2", 8.0, 1, 0), ("S2", 6.0, 0, 0),
    ("S2", 2.5, 1, 0), ("S2", 3.5, 0, 1), ("S2", 5.0, 0, 0), ("S2", 1.0, 1, 1),
]


def _two_stage_rows(with_age=False):
    rows = []
    for index, (sid, duration, event, treatment) in enumerate(_TWO_STAGE_BASE):
        row = {"study_id": sid, "duration": duration, "event": event, "treatment": treatment}
        if with_age:
            row["age"] = 40.0 + 2.5 * (index % 13)
        rows.append(row)
    return rows


def _two_stage_separated(per_arm=15):
    """两研究反向完全分离：S1 处理组早死、S2 对照组早死（复审判定夹具）。"""
    rows = []
    for sid, early_arm in (("S1", 1), ("S2", 0)):
        for arm in (0, 1):
            start = 1 if arm == early_arm else per_arm + 1
            for index in range(per_arm):
                rows.append({"study_id": sid, "duration": float(start + index),
                             "event": 1, "treatment": arm})
    return rows


#: r9 交互模型对照批：3 研究 × 30 行，两臂、两结局值、协变量重叠（F4-108 探针同形）。
_INTERACTION_CONTROL = []
for _study, _shift in (("S1", 0.0), ("S2", 0.4), ("S3", -0.3)):
    for _index in range(30):
        _INTERACTION_CONTROL.append({
            "study_id": _study, "treatment": _index % 2,
            "outcome": 1 if (_index * 7 + int(_shift * 10)) % 3 else 0,
            "covariate": round(_shift + (_index % 11) * 0.21 - 1.0, 4),
        })


def _study_rows(studies):
    """k 个研究 × 4 行（两臂、两结局值、协变量随行号变化）→ 交互模型输入。"""
    rows = []
    for index in range(studies):
        for offset in range(4):
            rows.append({"study_id": f"S{index}", "treatment": offset % 2,
                         "outcome": (offset // 2) % 2,
                         "covariate": 0.5 + 0.5 * offset})
    return rows


class F496HugeOutcomeInteger(unittest.TestCase):
    """b06·F4-96：结局 401 位整数 → math.isfinite OverflowError → 端点 500。

    修前实测（``ipd_analysis.import_study`` 直接复现）：``OverflowError: int too
    large to convert to float``（非 ValueError），端点只捕 ValueError。
    """

    def _rows(self, value):
        return [{"arm": "treatment", "value": 1}, {"arm": "treatment", "value": value},
                {"arm": "control", "value": 3}, {"arm": "control", "value": 4}]

    def test_huge_integer_outcome_is_a_value_error(self):
        with self.assertRaises(ValueError) as caught:
            ipd_analysis.import_study(
                os.path.join(_TMP.name, "unused.db"), "S1", "A vs B", "Y", "T",
                "MD", "K1", self._rows(10 ** 400), effect_direction="first_vs_second")
        self.assertEqual("participant outcomes must be finite numbers", str(caught.exception))
        self.assertNotIsInstance(caught.exception, OverflowError)

    def test_huge_integer_control_value_error_is_valueerror_subclass(self):
        """对照：同一入口的 float('nan')/inf 早已是 ValueError（契约锚点）。"""
        for bad in (float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                ipd_analysis.import_study(
                    os.path.join(_TMP.name, "unused.db"), "S1", "A vs B", "Y", "T",
                    "MD", "K1", self._rows(bad), effect_direction="first_vs_second")

    def test_normal_values_still_import(self):
        """逐位对照：常规 0/1 与连续值批照常入库，汇总与基线一致。"""
        from coscreen import db as db_mod
        from coscreen.db import _SCHEMA, _connect
        from coscreen.models import Article
        from coscreen.review_analysis import list_effects, save_study
        from contextlib import closing

        path = os.path.join(_TMP.name, "f496.db")
        with closing(_connect(path)) as conn, conn:
            conn.executescript(_SCHEMA)
        db_mod.upsert_articles(path, [Article(zotero_key="K1", title="Ref")], [])
        save_study(path, "S1", "Study 1", ["K1"])
        rows = [{"arm": "treatment", "value": 1.0}, {"arm": "treatment", "value": 2.0},
                {"arm": "control", "value": 3.0}, {"arm": "control", "value": 4.0}]
        result = ipd_analysis.import_study(
            path, "S1", "A vs B", "Y", "T", "MD", "K1", rows,
            effect_direction="first_vs_second")
        self.assertEqual({"n_t": 2, "mean_t": 1.5, "sd_t": 0.7071067811865476,
                          "n_c": 2, "mean_c": 3.5, "sd_c": 0.7071067811865476},
                         result["summary"])
        self.assertEqual(-2.0, result["estimate"])
        self.assertEqual(0.7071067811865476, result["se"])
        saved = list_effects(path)
        self.assertEqual(1, len(saved))
        self.assertEqual(-2.0, saved[0]["estimate"])


class F498UnhashableArm(unittest.TestCase):
    """b06·F4-98：行 arm 为 JSON list/dict → `arm not in arms` unhashable TypeError。

    修前实测：``TypeError: unhashable type: 'list'`` —— 端点只捕 ValueError，故 500。
    """

    def _rows(self):
        return [{"arm": ["treatment"], "value": 1}, {"arm": "treatment", "value": 2},
                {"arm": "control", "value": 3}, {"arm": "control", "value": 4}]

    def test_list_arm_is_a_value_error(self):
        with self.assertRaises(ValueError) as caught:
            ipd_analysis.import_study(
                os.path.join(_TMP.name, "unused.db"), "S1", "A vs B", "Y", "T",
                "MD", "K1", self._rows(), effect_direction="first_vs_second")
        self.assertEqual("each IPD row needs arm (treatment/control) and value",
                         str(caught.exception))

    def test_dict_and_int_arm_are_value_errors(self):
        for arm in ({"a": 1}, 0, None):
            rows = self._rows()
            rows[0]["arm"] = arm
            with self.assertRaises(ValueError):
                ipd_analysis.import_study(
                    os.path.join(_TMP.name, "unused.db"), "S1", "A vs B", "Y", "T",
                    "MD", "K1", rows, effect_direction="first_vs_second")

    def test_unknown_string_arm_message_unchanged(self):
        """对照（R2）：未知字符串臂原本就是同名 ValueError，文案不变。"""
        rows = self._rows()
        rows[0]["arm"] = "placebo"
        with self.assertRaises(ValueError) as caught:
            ipd_analysis.import_study(
                os.path.join(_TMP.name, "unused.db"), "S1", "A vs B", "Y", "T",
                "MD", "K1", rows, effect_direction="first_vs_second")
        self.assertEqual("each IPD row needs arm (treatment/control) and value",
                         str(caught.exception))


class F493ComputationBudget(unittest.TestCase):
    """b06·F4-93：Newton×Armijo 无预算，一个合法请求可占满同步 worker。

    修复按依赖 REML 载体的 ``_EvaluationBudget`` 同形加模块层墙钟预算：
    超出时 ``_BudgetExceeded``（ValueError 子类）→ 端点 400。
    """

    def test_budget_is_module_level_and_shared_shape(self):
        for module in (ipd_adjusted_cox, ipd_cox_extended):
            self.assertTrue(hasattr(module, "_EvaluationBudget"), module.__name__)
            self.assertGreater(module._BUDGET_SECONDS, 0, module.__name__)
            budget = module._EvaluationBudget()
            self.assertFalse(budget.expired())
            self.assertTrue(module._EvaluationBudget(seconds=0.0).expired())

    def test_expired_budget_aborts_the_fit_with_a_value_error(self):
        """破坏即红：把预算置 0 → 拟合必须 ValueError（400），不是跑满 100 轮。"""
        real = ipd_adjusted_cox._BUDGET_SECONDS
        ipd_adjusted_cox._BUDGET_SECONDS = 0.0
        try:
            with self.assertRaises(ValueError) as caught:
                ipd_adjusted_cox.fit_adjusted_stratified_cox(
                    _ADJUSTED_CONTROL, {"S1": "K1"},
                    covariate_center=0.0, covariate_scale=1.0)
        finally:
            ipd_adjusted_cox._BUDGET_SECONDS = real
        self.assertIn("computation budget", str(caught.exception))

    def test_budget_is_charged_per_evaluation_not_per_fit(self):
        """每轮 evaluate 计费：零预算下第一次求值即中止（不是 100 轮后）。"""
        budget = ipd_adjusted_cox._EvaluationBudget(seconds=0.0)
        with self.assertRaises(ipd_adjusted_cox._BudgetExceeded):
            budget.check()
        self.assertTrue(budget.exhausted)

    def test_pathological_batch_is_cut_off_by_the_budget(self):
        """破坏即红：复审同形态的大批在 1 s 预算下必须被预算中止。

        该批（3 研究 × 1500 行）在基线 bfd07ae 上跑满 34.1 s 才以「failed to
        converge」结束（本机实测）；若预算接线回退，本用例会跑满全部迭代而不是
        在 1 s 附近抛 ``computation budget``。
        """
        rows = []
        for study in range(3):
            for index in range(1500):
                treatment = index % 2
                rows.append({"study_id": f"S{study}",
                             "duration": float(1500 - index) + (index % 7) * 1e-4,
                             "event": 1 if (index * 3) % 5 else 0,
                             "treatment": treatment,
                             "covariate": float(treatment) * 10.0 + (index % 3) * 1e-3})
        real = ipd_adjusted_cox._BUDGET_SECONDS
        ipd_adjusted_cox._BUDGET_SECONDS = 1.0
        started = time.monotonic()
        try:
            with self.assertRaises(ValueError) as caught:
                ipd_adjusted_cox.fit_adjusted_stratified_cox(
                    rows, {"S0": "K1", "S1": "K2", "S2": "K3"},
                    covariate_center=0.0, covariate_scale=1.0)
        finally:
            ipd_adjusted_cox._BUDGET_SECONDS = real
        elapsed = time.monotonic() - started
        self.assertIn("computation budget", str(caught.exception))
        self.assertLess(elapsed, 20.0,
                        f"预算未生效：耗时 {elapsed:.1f}s（基线 34.1s，1 s 预算应立即中止）")

    def test_exhausted_budget_does_not_leak_into_candidate_search(self):
        """Armijo 回退不得把预算中止吞成 -inf（否则会继续跑满 31 次回退）。"""
        source = (REPO / "coscreen" / "ipd_adjusted_cox.py").read_text(encoding="utf-8")
        self.assertIn("except _BudgetExceeded:\n                    raise", source)
        self.assertIn("budget.check()\n        likelihood = 0.0", source)

    def test_normal_control_fit_is_bit_identical(self):
        """逐位对照：12 行可识别批的返回值与修复前完全一致。"""
        result = ipd_adjusted_cox.fit_adjusted_stratified_cox(
            _ADJUSTED_CONTROL, {"S1": "K1"}, covariate_center=0.0, covariate_scale=1.0)
        self.assertEqual(-0.052537670619041, result["log_hr"])
        self.assertEqual(0.8410243360302041, result["se"])
        self.assertEqual(-0.5001297609056868, result["covariate_log_hr"])
        self.assertEqual(0.8439035562719427, result["covariate_se"])
        self.assertEqual(0.9488185777993869, result["hazard_ratio"])
        self.assertEqual(0.18251643059401804, result["hr_ci_low"])
        self.assertEqual(4.93246931603514, result["hr_ci_high"])
        self.assertEqual(12, result["n"])
        self.assertEqual(7, result["events"])


class F494AdjustedCoxSeparation(unittest.TestCase):
    """b06·F4-94：完全分离被当「成功拟合」返回 se≈3.3e4、hr_ci=None。

    修前实测：单研究 10 行全事件、协变量随 duration 递减 →
    ``log_hr=0.07541 se=33359.82 covariate_log_hr=23.02 hr_ci=(None, None)``，200 返回。
    """

    def test_covariate_separation_is_rejected(self):
        with self.assertRaises(ValueError) as caught:
            ipd_adjusted_cox.fit_adjusted_stratified_cox(
                _SEPARATED_ROWS, {"S1": "K1"}, covariate_center=0.0, covariate_scale=1.0)
        message = str(caught.exception)
        self.assertIn("separate the groups", message)
        self.assertIn("does not exist", message)

    def test_two_study_separation_is_rejected(self):
        with self.assertRaises(ValueError) as caught:
            ipd_adjusted_cox.fit_adjusted_stratified_cox(
                _SEPARATED_ROWS_2, {"S1": "K1", "S2": "K2"},
                covariate_center=0.0, covariate_scale=1.0)
        self.assertIn("separate the groups", str(caught.exception))

    def test_gate_uses_information_collapse_ratio_not_magnitude(self):
        """门是「信息坍缩比」而非 |beta|：分离批的 se 从未超过 30，|beta[0]| 仅 0.075。"""
        self.assertTrue(hasattr(ipd_adjusted_cox, "_information_collapse_ratio"))
        self.assertTrue(hasattr(ipd_adjusted_cox, "_MIN_INFORMATION_RATIO"))
        self.assertLess(ipd_adjusted_cox._MIN_INFORMATION_RATIO, 1e-5)

    def test_mm1004_noise_floor_control_still_fits(self):
        """Check the Cox fit at the line search noise floor."""
        rows = [
            {"study_id": "A", "duration": 2, "event": 1, "treatment": 1, "covariate": 2.5},
            {"study_id": "A", "duration": 3, "event": 1, "treatment": 0, "covariate": 1.5},
            {"study_id": "A", "duration": 3, "event": 1, "treatment": 0, "covariate": 2.0},
            {"study_id": "A", "duration": 4, "event": 1, "treatment": 1, "covariate": 0.5},
            {"study_id": "A", "duration": 5, "event": 1, "treatment": 0, "covariate": 1.0},
            {"study_id": "B", "duration": 5, "event": 0, "treatment": 1, "covariate": 1.0},
            {"study_id": "B", "duration": 6, "event": 1, "treatment": 0, "covariate": 2.0},
            {"study_id": "B", "duration": 6, "event": 0, "treatment": 0, "covariate": 0.5},
            {"study_id": "B", "duration": 7, "event": 1, "treatment": 1, "covariate": 3.0},
            {"study_id": "B", "duration": 8, "event": 0, "treatment": 1, "covariate": 1.5},
            {"study_id": "B", "duration": 9, "event": 0, "treatment": 0, "covariate": 2.5},
        ]
        result = ipd_adjusted_cox.fit_adjusted_stratified_cox(
            rows, {"A": "R1", "B": "R2"}, covariate_center=1.9, covariate_scale=1.0)
        assert_numeric_baseline(result["log_hr"], 0.6147884612405432, abs_tol=1e-9)
        assert_numeric_baseline(result["se"], 0.9839101340666847, abs_tol=1e-9)
        assert_numeric_baseline(result["covariate_log_hr"], 1.4815006422579453, abs_tol=1e-9)


class F495FsumFamily(unittest.TestCase):
    """b16·F4-95：`:174` 的 fsum 溢出未按契约转 ValueError（fsum 溢出族）。

    修前实测（构造包装强制该行抛 OverflowError）：
    ``isinstance(exc, ValueError) = False`` → 端点 500。
    """

    def test_event_term_fsum_overflow_becomes_value_error(self):
        real = ipd_adjusted_cox.fsum

        def raiser(values):
            if isinstance(values, list):
                raise OverflowError("intermediate overflow in fsum")
            return real(values)

        ipd_adjusted_cox.fsum = raiser
        try:
            with self.assertRaises(ValueError) as caught:
                ipd_adjusted_cox.fit_adjusted_stratified_cox(
                    _ADJUSTED_CONTROL, {"S1": "K1"},
                    covariate_center=0.0, covariate_scale=1.0)
        finally:
            ipd_adjusted_cox.fsum = real
        self.assertNotIsInstance(caught.exception, OverflowError)
        self.assertIn("outside numeric range", str(caught.exception))

    def test_three_fsum_carriers_share_one_entry(self):
        """族内一致：三个载体的 fsum 全部经 ``_finite_fsum`` 入口。"""
        for module in (ipd_adjusted_cox, ipd_cox_extended, ipd_interaction):
            self.assertTrue(hasattr(module, "_finite_fsum"), module.__name__)

    def test_finite_fsum_is_bit_identical_and_maps_overflow(self):
        for module in (ipd_adjusted_cox, ipd_cox_extended, ipd_interaction):
            self.assertEqual(math.fsum([0.1, 0.2, 0.3]),
                             module._finite_fsum([0.1, 0.2, 0.3], "unused"))
            with self.assertRaises(ValueError) as caught:
                module._finite_fsum([-1.5e308, -1.5e308], "sum overflowed")
            self.assertEqual("sum overflowed", str(caught.exception))

    def test_no_bare_fsum_left_in_the_event_term(self):
        """钉子：事件项求和不得退回裸 fsum（恢复时这里红）。"""
        source = (REPO / "coscreen" / "ipd_adjusted_cox.py").read_text(encoding="utf-8")
        self.assertIn("likelihood += _finite_fsum(relative_event_eta,", source)
        self.assertNotIn("likelihood += fsum(relative_event_eta)", source)


class F4101ReservedCovariateNames(unittest.TestCase):
    """b05·F4-101：协变量名 event/duration 与保留字段塌缩，结局/时间被静默「校正」。

    修前实测（2 研究 × 20 行、4 键行 + covariate_centers={"event":0.5}）：
    ``covariate 'event' -> OK``，``covariate_effects {'event': log_hr 22.76, se 22708}``。
    这是正确性修复（守护科学解释），不属于 R2：保留名协变量的估计根本无法解释。
    """

    def _fit(self, name):
        return ipd_cox_extended.fit_adjusted_stratified_cox_multi(
            _ipd_rows(), {"S1": "K1", "S2": "K2"},
            covariate_centers={name: 0.5}, covariate_scales={name: 1.0})

    def test_reserved_covariate_names_are_refused_with_explanation(self):
        for name in ("event", "duration", "study_id", "treatment"):
            with self.assertRaises(ValueError) as caught:
                self._fit(name)
            message = str(caught.exception)
            self.assertIn("reserved row fields", message)
            self.assertIn(name, message)

    def test_legitimate_covariate_is_unaffected(self):
        """逐位对照：age 协变量照常拟合，数值与基线一致。"""
        result = ipd_cox_extended.fit_adjusted_stratified_cox_multi(
            _ipd_rows(), {"S1": "K1", "S2": "K2"},
            covariate_centers={"age": 55.0}, covariate_scales={"age": 10.0})
        self.assertEqual("age", result["covariate_names"][0])
        self.assertEqual(0.3121481487917157, result["log_hr"])
        self.assertEqual(0.398270555787086, result["se"])
        self.assertEqual(2.7750914170503966, result["covariate_effects"]["age"]["log_hr"])
        self.assertEqual(0.9717161137229171, result["covariate_effects"]["age"]["se"])

    def test_reserved_check_runs_before_row_scan(self):
        """守卫在 _covariate_spec 内，与行内容无关（缺失列也先报保留名）。"""
        with self.assertRaises(ValueError) as caught:
            ipd_cox_extended.fit_adjusted_stratified_cox_multi(
                _ipd_rows(), {"S1": "K1", "S2": "K2"},
                covariate_centers={"event": 0.5, "age": 50.0},
                covariate_scales={"event": 1.0, "age": 10.0})
        self.assertIn("reserved row fields", str(caught.exception))


class F4102CovariateCountCap(unittest.TestCase):
    """b05·F4-102：协变量个数无上限，width²×8 使单请求可达 74.5 GiB。

    修前实测：``_covariate_spec 100000 -> OK len(names)=100000``。
    """

    def _spec(self, count):
        return ipd_cox_extended._covariate_spec(
            {f"c{index}": 0.0 for index in range(count)},
            {f"c{index}": 1.0 for index in range(count)})

    def test_cap_is_enforced_at_the_module_boundary(self):
        with self.assertRaises(ValueError) as caught:
            self._spec(ipd_cox_extended._MAX_COVARIATES + 1)
        self.assertIn(str(ipd_cox_extended._MAX_COVARIATES), str(caught.exception))

    def test_cap_boundary_is_inclusive(self):
        names, centers, scales = self._spec(ipd_cox_extended._MAX_COVARIATES)
        self.assertEqual(ipd_cox_extended._MAX_COVARIATES, len(names))
        self.assertEqual(ipd_cox_extended._MAX_COVARIATES, len(centers))
        self.assertEqual(ipd_cox_extended._MAX_COVARIATES, len(scales))

    def test_legitimate_one_and_two_covariate_fits_unchanged(self):
        """逐位对照：单协变量拟合数值不变（cap 只在边界处新增拒绝）。"""
        result = ipd_cox_extended.fit_adjusted_stratified_cox_multi(
            _ipd_rows(), {"S1": "K1", "S2": "K2"},
            covariate_centers={"age": 55.0}, covariate_scales={"age": 10.0})
        self.assertEqual(0.3121481487917157, result["log_hr"])
        self.assertEqual(2, len(result["covariance_names"]))


class F4104TwoStageSeparation(unittest.TestCase):
    """b05·F4-104：两阶段完全分离 → HR≈1、tau2=0、I2=0 的误导性 meta 结果。

    修前实测：``study_estimates [('S1', 23.668, 19545.6), ('S2', -23.668, 19545.6)]``、
    ``pooled_log_hr=-6.32e-07 tau2=0.0 i2=0.0 q_p=0.9986 HR=0.9999994``。
    """

    def test_pooled_separation_is_rejected(self):
        with self.assertRaises(ValueError) as caught:
            ipd_cox_extended.fit_two_stage_random_slopes(
                _two_stage_separated(), {"S1": "K1", "S2": "K2"}, tau2_method="DL")
        message = str(caught.exception)
        self.assertIn("study S1", message)
        self.assertIn("separate the groups", message)

    def test_reml_path_shares_the_gate(self):
        with self.assertRaises(ValueError) as caught:
            ipd_cox_extended.fit_two_stage_random_slopes(
                _two_stage_separated(), {"S1": "K1", "S2": "K2"}, tau2_method="REML")
        self.assertIn("separate the groups", str(caught.exception))

    def test_two_stage_control_is_bit_identical(self):
        """逐位对照：r7 内核两研究批的池化值不变（含 MM11-03 的带 age 列批）。"""
        for with_age in (False, True):
            result = ipd_cox_extended.fit_two_stage_random_slopes(
                _two_stage_rows(with_age), {"S1": "K1", "S2": "K2"}, tau2_method="DL")
            self.assertEqual(0.513116806120782, result["pooled_log_hr"], with_age)
            self.assertEqual(0.6183281682188588, result["se"], with_age)
            self.assertEqual(0.0, result["tau2"], with_age)
            self.assertEqual(0.9330580960776236, result["q"], with_age)
            self.assertEqual([("S1", -0.021746804169711153, 0.8300201454640053),
                              ("S2", 1.1800828085272226, 0.9268702784402811)],
                             [(item["study_id"], item["log_hr"], item["se"])
                              for item in result["study_estimates"]])

    def test_one_stage_gate_and_two_stage_gate_share_the_threshold(self):
        self.assertEqual(ipd_adjusted_cox._MIN_INFORMATION_RATIO,
                         ipd_cox_extended._MIN_INFORMATION_RATIO)


class F4105InteractionStudyCap(unittest.TestCase):
    """b05·F4-105：研究数 k 无上限，intercepts ~ n·k 与 (k+4)² 协方差回显。

    修前实测：``IpdInteractionIn 接受 33333 study_sources``；k=10/20/40 的
    peak 0.3/0.9/3.0 MiB 呈 k² 增长。
    """

    def test_cap_is_enforced(self):
        studies = ipd_interaction._MAX_STUDIES + 1
        with self.assertRaises(ValueError) as caught:
            ipd_interaction.fit_ipd_interaction(
                _study_rows(studies), {f"S{i}": f"K{i}" for i in range(studies)},
                covariate_center=1.0)
        self.assertIn(str(ipd_interaction._MAX_STUDIES), str(caught.exception))

    def test_cap_boundary_allows_the_cap_itself(self):
        """对照：恰为上限时进入拟合（此处因设计退化而 400，但不是上限拒绝）。"""
        studies = ipd_interaction._MAX_STUDIES
        with self.assertRaises(ValueError) as caught:
            ipd_interaction.fit_ipd_interaction(
                _study_rows(studies), {f"S{i}": f"K{i}" for i in range(studies)},
                covariate_center=1.0)
        self.assertNotIn("at most", str(caught.exception))

    def test_control_fit_matches_baseline(self):
        """Check odds ratios and covariance for three studies with 30 rows each."""
        result = ipd_interaction.fit_ipd_interaction(
            _INTERACTION_CONTROL, {"S1": "K1", "S2": "K2", "S3": "K3"},
            covariate_center=0.0)
        assert_numeric_baseline(result["treatment_or_at_reference"]["or"], 1.0060283118084776)
        assert_numeric_baseline(result["within_study_interaction_or_per_unit"]["or"], 0.3648758125490206)
        assert_numeric_baseline(
            result["between_study_interaction_or_per_unit"]["or"], 0.9999999999999999
        )
        assert_numeric_baseline(
            result["covariance"][0],
            [0.2071140583086484, 0.045480758422598425, 0.05990147699039028,
             -0.104137878043648, 0.023784439376931664,
             -0.023784439376931678, 0.04132979534648222],
        )
        self.assertEqual(90, result["n"])
        self.assertEqual(3, result["studies"])


class F4106InteractionMeanOverflow(unittest.TestCase):
    """b05·F4-106：研究均值 math.fsum 在全部输入合法时抛 OverflowError → 500。

    修前实测：study A 每臂 14×maxfloat + 1×nextafter(maxfloat,0)（全部通过
    ``_number`` 与 overlap 检查）→ ``OverflowError: intermediate overflow in fsum``。
    """

    def _rows(self):
        largest = sys.float_info.max
        rows = []
        for study in ("A", "B"):
            for arm in (0, 1):
                for _ in range(14):
                    rows.append({"study_id": study, "treatment": arm,
                                 "outcome": 1, "covariate": largest})
                rows.append({"study_id": study, "treatment": arm, "outcome": 0,
                             "covariate": math.nextafter(largest, 0)})
        return rows

    def test_mean_overflow_becomes_a_value_error(self):
        with self.assertRaises(ValueError) as caught:
            ipd_interaction.fit_ipd_interaction(
                self._rows(), {"A": "K1", "B": "K2"}, covariate_center=1.0)
        self.assertIn("outside the representable numeric range", str(caught.exception))
        self.assertNotIsInstance(caught.exception, OverflowError)

    def test_control_means_unchanged(self):
        """逐位对照：常规协变量批的均值路径仍走同一条 fsum。"""
        result = ipd_interaction.fit_ipd_interaction(
            _INTERACTION_CONTROL, {"S1": "K1", "S2": "K2", "S3": "K3"},
            covariate_center=0.0)
        self.assertEqual(1.0060283118084776, result["treatment_or_at_reference"]["or"])


class F4107ExpUnderflow(unittest.TestCase):
    """b14·F4-107：``result or None`` 把下溢 0.0 与溢出混为一谈（审计表显示 null）。

    修前实测：``_exp_or_none(-800) -> None``、``math.exp(-800)=0.0``、
    ``(-700) -> 9.86e-305``。三个载体（adjusted / extended / interaction）同形。
    """

    def test_underflow_keeps_the_zero(self):
        for module in (ipd_interaction, ipd_adjusted_cox, ipd_cox_extended):
            self.assertEqual(0.0, module._exp_or_none(-800.0), module.__name__)
            self.assertEqual(0.0, module._exp_or_none(-745.5), module.__name__)

    def test_overflow_and_ordinary_values_unchanged(self):
        for module in (ipd_interaction, ipd_adjusted_cox, ipd_cox_extended):
            self.assertIsNone(module._exp_or_none(800.0), module.__name__)
            self.assertEqual(1.0, module._exp_or_none(0.0), module.__name__)
            self.assertEqual(9.85967654375977e-305, module._exp_or_none(-700.0),
                             module.__name__)

    def test_underflow_result_is_json_serialisable(self):
        import json

        self.assertEqual('{"v": 0.0}',
                         json.dumps({"v": ipd_interaction._exp_or_none(-800.0)}))


if __name__ == "__main__":
    unittest.main()
