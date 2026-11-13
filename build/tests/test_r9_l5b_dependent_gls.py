"""r9 L5b 钉子：依赖效应格式/协方差核/固定效应合成（清单 §3.5 11 条）。

覆盖（inventory_夜扫286确认分组.md §3.5，泳道 L5b）：
  b04·F3-38  coscreen/dependent_effect_formats.py  计数无上界 → OverflowError 500
  b04·F3-41  coscreen/dependent_effects.py         尺寸校验前整体物化矩阵 / ID 长度无上限
  b04·F3-44  coscreen/dependent_effects.py         失败 rho 回填 inf → 保存期 400
  b04·F3-45  coscreen/dependent_effects.py         additive_covariance_effect_ids 报源顺序错配
  b12·F3-42  coscreen/dependent_effects.py         HTZ 裸 inv 无有限性门 → statistic=Infinity
  b12·F3-43  coscreen/dependent_effects.py         rho_sensitivity 失败分支原样回显 NaN/Infinity
  b12·F3-46  coscreen/dependent_effects.py         LOCO/rho_sensitivity 畸形输入裸 KeyError/TypeError
  b12·F3-47  coscreen/dependent_effects.py         0 列设计被接受并「成功」拟合
  b13·F3-71  coscreen/dependent_synthesis.py       result_ids 不可哈希/None → 裸 TypeError
  b13·F3-72  coscreen/dependent_synthesis.py       9 结局在整条 GLS 后才拒，真因被数值文案掩盖
  b13·F3-74  coscreen/dependent_synthesis.py       study_id 单侧 strip，带空白 study_id 永不匹配

破坏即红（R4）：每个条目的第一个断言钉修复后的行为，在基线 bfd07ae 上必红
（修前实测数值见各 test 注释）；标「常规对照」的断言是逐位不变钉子（基线绿、
修复后必须仍绿），钉住 R1/R2：无格式变更、不新增拒绝合法输入的严格性。

同族口径（清单 §6.3）：非有限值清洗族（F3-42 HTZ statistic、F3-43/F3-44 失败 run 的
rho 回显）统一走同一「有限性清洗入口」——拟合结果只发布可序列化的有限值，
拒绝原因走 reason_code/reason；F3-38 与 F3-50/F3-60 同型（发布前有限性门）。
"""
import json
import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np

from _numeric_assertions import assert_numeric_baseline
from coscreen import dependent_effects as de
from coscreen import dependent_synthesis as ds
from coscreen.dependent_effect_formats import calculate_dependent_effect


def _cluster_effects(variances=(0.01, 0.01, 0.01, 0.01, 0.02, 0.02), estimates=(0.1, 0.2, 0.15, 0.25, 0.12, 0.32)):
    clusters = ("c1", "c1", "c2", "c2", "c3", "c3")
    return [
        {"effect_id": f"e{index + 1}", "cluster_id": clusters[index],
         "study_id": f"S{index // 2 + 1}" if index // 2 < 2 else "S3",
         "estimate": estimates[index], "variance": variances[index]}
        for index in range(len(estimates))
    ]


_DESIGN = [[1.0, 0.0], [1.0, 1.0], [1.0, 0.0], [1.0, 1.0], [1.0, 0.5], [1.0, 1.5]]

# 基线 bfd07ae 逐位实测（本工作树，tools 见各 test 注释）：常规 3 簇 6 效应。
_BASELINE_COEFFICIENTS = [
    {"name": "a", "estimate": 0.11448275862068966, "model_based_se": 0.06695340634119862},
    {"name": "b", "estimate": 0.11586206896551725, "model_based_se": 0.08304547985373997},
]
_BASELINE_ROBUST_COVARIANCE = [
    [0.0007177275101400103, -0.00041283013608428673],
    [-0.00041283013608428673, 0.0003604354972674296],
]
_BASELINE_JOINT = {"statistic": 286.75329512352374, "f_statistic": 62.51625676179383,
                   "df_denominator": 0.7731381971235605, "satterthwaite_nu": 1.7731381971235605,
                   "p_value": 0.1396768604092907}


class _CountingMatrix:
    """300×300 惰性矩阵：只记录真正被读取的单元数。

    F3-41 的复现形态：修复前 ``np.asarray`` 把整张 300×300 读满（90000 单元）
    才报 dimension mismatch；修复后按声明的 len/shape 先判尺寸。
    """

    def __init__(self, size: int = 300, value: float = 1.0):
        self.size = size
        self.value = value
        self.reads = 0

    def __len__(self):
        return self.size

    def __getitem__(self, row):
        if isinstance(row, slice):
            return [self[index] for index in range(*row.indices(self.size))]
        if not 0 <= row < self.size:
            raise IndexError(row)
        parent = self

        class _Row:
            def __init__(self, index):
                self.index = index

            def __len__(self):
                return parent.size

            def __getitem__(self, column):
                if not 0 <= column < parent.size:
                    raise IndexError(column)
                parent.reads += 1
                return parent.value if self.index == column else 0.0

        return _Row(row)


class F338CountBoundTests(unittest.TestCase):
    """F3-38：n=10**400 使 math.sqrt 抛 OverflowError → HTTP 500。"""

    def test_unrepresentable_count_is_a_domain_error(self):
        values = {"n": 10 ** 400, "mean_a": 1.0, "mean_b": 0.0, "sd_diff": 1.0, "design": "pre_post"}
        with self.assertRaises(ValueError, msg="10**400 计数不得再以 OverflowError 冒泡"):
            calculate_dependent_effect("paired_md_sd_diff", values)

    def test_all_four_formats_close_the_same_gap(self):
        with self.assertRaises(ValueError):
            calculate_dependent_effect(
                "paired_md_correlation",
                {"n": 10 ** 400, "mean_a": 1.0, "mean_b": 0.0, "sd_a": 1.0, "sd_b": 1.0,
                 "correlation": 0.5, "correlation_source": "assumed",
                 "correlation_source_note": "n", "design": "pre_post"})
        with self.assertRaises(ValueError):
            calculate_dependent_effect(
                "parallel_change_sd",
                {"n_t": 10 ** 400, "baseline_mean_t": 1.0, "final_mean_t": 2.0, "sd_change_t": 1.0,
                 "n_c": 10 ** 400, "baseline_mean_c": 1.0, "final_mean_c": 2.0, "sd_change_c": 1.0})

    def test_regular_counts_and_large_but_representable_controls_unchanged(self):
        """常规对照（R2）：10**100、2**53 与常规 n 仍接受，SE 逐位不变。"""
        base = {"mean_a": 1.0, "mean_b": 0.0, "sd_diff": 1.0, "design": "pre_post"}
        self.assertEqual(calculate_dependent_effect("paired_md_sd_diff", {"n": 10 ** 100, **base})["se"],
                         9.999999999999999e-51)
        self.assertEqual(calculate_dependent_effect("paired_md_sd_diff", {"n": 2 ** 53, **base})["se"],
                         1.0536712127723507e-08)
        self.assertEqual(calculate_dependent_effect("paired_md_sd_diff", {"n": 40, **base})["se"],
                         0.15811388300841897)


class F341BoundedMaterializationTests(unittest.TestCase):
    """F3-41：尺寸校验前整体物化 8N² 字节；ID 长度无上限。"""

    def test_mismatched_user_covariance_is_rejected_before_reading_all_cells(self):
        matrix = _CountingMatrix()
        with self.assertRaises(de.KernelError) as caught:
            de.build_working_covariance(["a"], [1.0], ["c"], kind="user", matrix=matrix,
                                        covariance_effect_ids=["a"])
        self.assertEqual(caught.exception.code, "covariance_dimension_mismatch")
        self.assertLess(matrix.reads, 5000,
                        "300×300 与 1 个 ID 不匹配时不得读满 90000 个单元")

    def test_mismatched_additive_covariance_is_rejected_before_materialising(self):
        matrix = _CountingMatrix()
        effects = [
            {"effect_id": "a", "cluster_id": "c", "study_id": "S", "estimate": 0.1, "variance": 1.0},
            {"effect_id": "b", "cluster_id": "d", "study_id": "T", "estimate": 0.2, "variance": 1.0},
        ]
        with self.assertRaises(de.KernelError) as caught:
            de.fit_dependent_gls(effects, [[1.0], [1.0]], ["x"], additive_covariance=matrix,
                                 additive_covariance_effect_ids=["a"])
        self.assertEqual(caught.exception.code, "covariance_dimension_mismatch")
        self.assertLess(matrix.reads, 5000)

    def test_htz_constraints_with_wrong_column_count_skip_materialisation(self):
        matrix = _CountingMatrix()
        effects2 = [
            {"effect_id": "a", "cluster_id": "c", "study_id": "S", "estimate": 0.1, "variance": 1.0},
            {"effect_id": "b", "cluster_id": "d", "study_id": "T", "estimate": 0.2, "variance": 1.0},
        ]
        result = de.fit_dependent_gls(effects2, [[1.0], [1.0]], ["x"], constraints=matrix)
        joint = result["robust_inference"]["joint_test"]
        self.assertEqual(joint["status"], "unavailable")
        self.assertEqual(joint["reason_code"], "invalid_constraints")
        self.assertLess(matrix.reads, 5000)

    def test_overlong_effect_id_is_reported(self):
        with self.assertRaises(de.KernelError) as caught:
            de.build_working_covariance(["i" * 200000], [1.0], ["c"])
        self.assertEqual(caught.exception.code, "invalid_ids")

    def test_regular_user_covariance_control_unchanged(self):
        built = de.build_working_covariance(["u1", "u2"], [1.0, 4.0], ["c1", "c1"], kind="user",
                                            matrix=[[1.0, 0.5], [0.5, 4.0]],
                                            covariance_effect_ids=["u1", "u2"])
        self.assertEqual(built["matrix"], [[1.0, 0.5], [0.5, 4.0]])
        self.assertEqual(built["source_covariance_effect_ids"], ["u1", "u2"])


class F347EmptyDesignTests(unittest.TestCase):
    """F3-47：0 列设计被接受并「成功」拟合（coefficients=[]，status=available）。"""

    def test_zero_column_design_is_refused(self):
        effects = [
            {"effect_id": "e1", "cluster_id": "c1", "study_id": "S1", "estimate": 0.1, "variance": 0.01},
            {"effect_id": "e2", "cluster_id": "c2", "study_id": "S2", "estimate": 0.2, "variance": 0.01},
        ]
        with self.assertRaises(de.KernelError) as caught:
            de.fit_dependent_gls(effects, [[], []], [])
        self.assertEqual(caught.exception.code, "empty_design_matrix")

    def test_regular_design_control_match_baseline(self):
        result = de.fit_dependent_gls(_cluster_effects(), _DESIGN, ["a", "b"])
        assert_numeric_baseline(result["coefficients"], _BASELINE_COEFFICIENTS)


class F342HtzFinitenessTests(unittest.TestCase):
    """F3-42：近奇异约束协方差经裸 inv 发布 statistic=Infinity（保存期 400）。"""

    def test_subnormal_constraint_covariance_is_not_published_as_infinity(self):
        beta = np.array([1.0, 1.0])
        robust = np.array([[1e-320, 0.0], [0.0, 1e-320]])
        maps = [np.eye(2) * 0.1, np.eye(2) * 0.1]
        joint = de._htz_test(beta, maps, np.eye(2) * 0.01, robust, [[1.0, 1.0]], ["a", "b"], None, None)
        self.assertEqual(joint["status"], "unavailable")
        self.assertEqual(joint["reason_code"], "invalid_htz_statistic")
        json.dumps(joint, allow_nan=False)

    def test_public_fit_with_subnormal_residual_is_serialisable(self):
        """同族口径：拟合结果发布前统一有限性清洗，拒绝原因走 reason_code。"""
        effects = [
            {"effect_id": "e1", "cluster_id": "c1", "study_id": "S1", "estimate": 1e-155, "variance": 1.0},
            {"effect_id": "e2", "cluster_id": "c1", "study_id": "S1", "estimate": 2e-155, "variance": 1.0},
            {"effect_id": "e3", "cluster_id": "c2", "study_id": "S2", "estimate": 1.5e-155, "variance": 1.0},
            {"effect_id": "e4", "cluster_id": "c2", "study_id": "S2", "estimate": 3.7e-155, "variance": 1.0},
        ]
        design = [[1.0, 0.0], [1.0, 1.0], [1.0, 0.0], [1.0, 1.0]]
        result = de.fit_dependent_gls(effects, design, ["a", "b"], constraints=[[1.0, 1.0]])
        self.assertEqual(result["robust_inference"]["status"], "available")
        self.assertEqual(result["robust_inference"]["joint_test"]["status"], "unavailable")
        json.dumps(result, allow_nan=False)

    def test_regular_htz_control_match_baseline(self):
        result = de.fit_dependent_gls(_cluster_effects(), _DESIGN, ["a", "b"],
                                      constraints=[[1.0, 1.0], [1.0, -1.0]],
                                      constraint_names=["sum", "diff"])
        joint = result["robust_inference"]["joint_test"]
        self.assertEqual(joint["status"], "available")
        for key, value in _BASELINE_JOINT.items():
            assert_numeric_baseline(joint[key], value, path=str(key))
        assert_numeric_baseline(result["robust_inference"]["covariance"], _BASELINE_ROBUST_COVARIANCE)


class F343F344RhoEchoTests(unittest.TestCase):
    """F3-44/F3-43：失败 run 回填 inf/NaN，整体 run 因 allow_nan=False 无法保存。"""

    def test_non_finite_rho_is_not_echoed_by_failed_run(self):
        result = de.rho_sensitivity(_cluster_effects(), _DESIGN, ["a", "b"], [0.3, float("inf")])
        failed = result["runs"][1]
        self.assertEqual(failed["status"], "failed")
        self.assertIsNone(failed["rho"])
        self.assertEqual(failed["reason_code"], "invalid_number")
        json.dumps(result, allow_nan=False)

    def test_nan_rho_is_not_echoed_either(self):
        result = de.rho_sensitivity(_cluster_effects(), _DESIGN, ["a", "b"], [float("nan"), 0.3])
        self.assertIsNone(result["runs"][0]["rho"])
        self.assertEqual(result["runs"][0]["status"], "failed")
        self.assertEqual(result["runs"][1]["rho"], 0.3)
        self.assertEqual(result["runs"][1]["status"], "available")
        json.dumps(result, allow_nan=False)

    def test_finite_rho_control_match_baseline(self):
        result = de.rho_sensitivity(_cluster_effects(), _DESIGN, ["a", "b"], [0.25, 0.5])
        self.assertEqual([run["rho"] for run in result["runs"]], [0.25, 0.5])
        assert_numeric_baseline(
            [(run["status"], run["result"]["coefficients"]) for run in result["runs"]],
            [("available",
              [{"name": "a", "estimate": 0.1135766423357664, "model_based_se": 0.06686406299132613},
               {"name": "b", "estimate": 0.11737226277372267, "model_based_se": 0.07398954355004853}]),
             ("available",
              [{"name": "a", "estimate": 0.11291139240506325, "model_based_se": 0.06608395121052414},
               {"name": "b", "estimate": 0.11848101265822783, "model_based_se": 0.06162360225133207}])],
        )


class F346MalformedInputTests(unittest.TestCase):
    """F3-46：LOCO/rho_sensitivity 对畸形输入抛裸 KeyError/TypeError（同输入 fit 转 KernelError）。"""

    def test_loco_missing_cluster_is_a_kernel_error(self):
        with self.assertRaises(de.KernelError) as caught:
            de.leave_one_cluster_out(
                [{"effect_id": "e1", "study_id": "S1", "estimate": 0.1, "variance": 0.01}],
                [[1.0]], ["a"])
        self.assertEqual(caught.exception.code, "missing_cluster_id")

    def test_loco_non_mapping_row_is_a_kernel_error(self):
        with self.assertRaises(de.KernelError):
            de.leave_one_cluster_out([5, 6], [[1.0], [1.0]], ["a"])

    def test_rho_sensitivity_duplicate_rho_keyword_is_a_kernel_error(self):
        with self.assertRaises(de.KernelError) as caught:
            de.rho_sensitivity(_cluster_effects(), _DESIGN, ["a", "b"], [0.2], rho=0.5)
        self.assertEqual(caught.exception.code, "unused_covariance_option")

    def test_rho_values_must_be_a_sequence(self):
        with self.assertRaises(de.KernelError) as caught:
            de.rho_sensitivity(_cluster_effects(), _DESIGN, ["a", "b"], 0.2)
        self.assertEqual(caught.exception.code, "invalid_rho_values")
        with self.assertRaises(de.KernelError):
            de.rho_sensitivity(_cluster_effects(), _DESIGN, ["a", "b"], "0.2")

    def test_non_finite_design_is_a_kernel_error(self):
        with self.assertRaises(de.KernelError) as caught:
            de.leave_one_cluster_out(_cluster_effects(), [[1e400]] * 6, ["a"])
        self.assertEqual(caught.exception.code, "invalid_design_matrix")

    def test_regular_loco_control_match_baseline(self):
        result = de.leave_one_cluster_out(_cluster_effects(), _DESIGN, ["a", "b"])
        assert_numeric_baseline(
            [(run["removed_cluster_id"], run["status"], run["coefficients"]) for run in result["runs"]],
            [("c1", "available",
              [{"name": "a", "estimate": 0.12909090909090912, "model_based_se": 0.0904534033733291},
               {"name": "b", "estimate": 0.1163636363636363, "model_based_se": 0.1044465935734187}]),
             ("c2", "available",
              [{"name": "a", "estimate": 0.08363636363636368, "model_based_se": 0.0904534033733291},
               {"name": "b", "estimate": 0.1345454545454545, "model_based_se": 0.1044465935734187}]),
             ("c3", "inference_unavailable",
              [{"name": "a", "estimate": 0.125, "model_based_se": 0.07071067811865475},
               {"name": "b", "estimate": 0.1, "model_based_se": 0.1}])],
        )


class F345AdditiveOrderTests(unittest.TestCase):
    """F3-45：matrix 按 effect_ids 重排，报的 ids 却是源顺序 → 存档工件错配。"""

    def _fit(self, ids):
        effects = [
            {"effect_id": "a1", "cluster_id": "c1", "study_id": "S1", "estimate": 0.1, "variance": 1.0},
            {"effect_id": "a2", "cluster_id": "c2", "study_id": "S2", "estimate": 0.2, "variance": 1.0},
        ]
        matrix = [[7.0, 0.0], [0.0, 5.0]] if ids == ["a2", "a1"] else [[5.0, 0.0], [0.0, 7.0]]
        return de.fit_dependent_gls(effects, [[1.0], [1.0]], ["b"], additive_covariance=matrix,
                                    additive_covariance_effect_ids=ids)["working_covariance"]

    def test_reported_ids_follow_the_stored_matrix_order(self):
        built = self._fit(["a2", "a1"])
        self.assertEqual(built["additive_covariance_effect_ids"], ["a1", "a2"])
        # a1 的加性方差是 5.0（源矩阵中位于 a2 之后），行序必须与报出的 ids 一致
        self.assertEqual(built["additive_covariance"], [[5.0, 0.0], [0.0, 7.0]])

    def test_same_order_control_bitwise_unchanged(self):
        built = self._fit(["a1", "a2"])
        self.assertEqual(built["additive_covariance_effect_ids"], ["a1", "a2"])
        self.assertEqual(built["additive_covariance"], [[5.0, 0.0], [0.0, 7.0]])


def _synth_fixture(*, study_ids=("S1", "S1", "S2", "S2"), estimates=(0.1, 0.2, 0.3, 0.4)):
    effects = [
        {"result_id": 1, "study_id": study_ids[0], "comparison": "A", "outcome": "x", "timepoint": "t",
         "measure": "MD", "estimate": estimates[0], "se": 0.5, "selected": 1,
         "source_key": "R1", "source_locator": "p1"},
        {"result_id": 2, "study_id": study_ids[1], "comparison": "A", "outcome": "y", "timepoint": "t",
         "measure": "MD", "estimate": estimates[1], "se": 0.5, "selected": 1,
         "source_key": "R1", "source_locator": "p1"},
        {"result_id": 3, "study_id": study_ids[2], "comparison": "A", "outcome": "x", "timepoint": "t",
         "measure": "MD", "estimate": estimates[2], "se": 0.5, "selected": 1,
         "source_key": "R1", "source_locator": "p1"},
        {"result_id": 4, "study_id": study_ids[3], "comparison": "A", "outcome": "y", "timepoint": "t",
         "measure": "MD", "estimate": estimates[3], "se": 0.5, "selected": 1,
         "source_key": "R1", "source_locator": "p1"},
    ]
    blocks = [
        {"study_id": "S1", "result_ids": [1, 2], "covariance_source_note": "n",
         "covariance": [[0.25, 0.0], [0.0, 0.25]]},
        {"study_id": "S2", "result_ids": [3, 4], "covariance_source_note": "n",
         "covariance": [[0.25, 0.0], [0.0, 0.25]]},
    ]
    return effects, blocks


def _synth_n_outcomes(count, *, base=0.1, spread=0.05):
    effects, blocks, rid = [], [], 0
    for study_index, study_id in enumerate(("S1", "S2")):
        ids = []
        for index in range(count):
            rid += 1
            ids.append(rid)
            estimate = base * (index + 1) + (spread if study_index else 0.0)
            effects.append({"result_id": rid, "study_id": study_id, "comparison": "A",
                            "outcome": f"o{index}", "timepoint": "t", "measure": "MD",
                            "estimate": estimate, "se": 0.5, "selected": 1,
                            "source_key": "R1", "source_locator": "p1"})
        blocks.append({"study_id": study_id, "result_ids": ids, "covariance_source_note": "n",
                       "covariance": [[0.25 if i == j else 0.0 for j in range(count)]
                                      for i in range(count)]})
    return effects, blocks


class F371ResultIdTests(unittest.TestCase):
    """F3-71：result_ids 含不可哈希/None 时抛裸 TypeError。"""

    def test_unhashable_result_id_is_a_value_error(self):
        effects, blocks = _synth_fixture()
        blocks[0]["result_ids"] = [[1], [2]]
        with self.assertRaises(ValueError) as caught:
            ds.synthesize_dependent_effects(effects, blocks)
        self.assertIsInstance(caught.exception, ValueError)
        self.assertNotIn("unhashable", str(caught.exception))

    def test_none_result_id_is_a_value_error(self):
        effects, blocks = _synth_fixture()
        blocks[0]["result_ids"] = [None]
        with self.assertRaises(ValueError) as caught:
            ds.synthesize_dependent_effects(effects, blocks)
        self.assertNotIn("NoneType", str(caught.exception))

    def test_effect_row_result_id_is_validated(self):
        effects, blocks = _synth_fixture()
        effects[0] = {**effects[0], "result_id": None}
        with self.assertRaises(ValueError):
            ds.synthesize_dependent_effects(effects, blocks)

    def test_regular_fixed_synthesis_control_match_baseline(self):
        result = ds.synthesize_dependent_effects(*_synth_fixture())
        assert_numeric_baseline(
            result["q"],
            {"statistic": 0.16, "df": 2, "p_value": 0.9231163463866358, "scope": "joint_gls_residual"},
        )
        assert_numeric_baseline(
            [row["estimate"] for row in result["estimates"]], [0.2, 0.30000000000000004]
        )


class F372OutcomeCountTests(unittest.TestCase):
    """F3-72：9 结局在完成整条 fixed GLS 后才被拒，数值先失败时真因被掩盖。"""

    def test_nine_outcomes_are_refused_for_the_real_reason(self):
        effects, blocks = _synth_n_outcomes(9, base=1e160, spread=5e158)
        with self.assertRaises(ValueError) as caught:
            ds.synthesize_dependent_effects(effects, blocks, model="random_reml_un")
        self.assertIn("two to eight outcomes", str(caught.exception))
        self.assertNotIn("numerically", str(caught.exception))

    def test_nine_outcomes_refused_before_any_fit_even_with_bad_numbers(self):
        effects, blocks = _synth_n_outcomes(9)
        effects[0] = {**effects[0], "se": float("nan")}
        with self.assertRaises(ValueError) as caught:
            ds.synthesize_dependent_effects(effects, blocks, model="random_reml_un")
        # se=NaN 是被支持性判定之前的输入错误；9 结局的专项文案不得被它掩盖
        self.assertIn("finite estimate and positive SE", str(caught.exception))

    def test_fixed_synthesis_of_nine_outcomes_still_supported(self):
        """R2 对照：fixed 模型本就支持任意结局数，不得被新守卫波及。"""
        result = ds.synthesize_dependent_effects(*_synth_n_outcomes(9))
        self.assertEqual(result["model"], "fixed_equal_effects_gls")
        self.assertEqual(len(result["estimates"]), 9)


class F374StudyIdWhitespaceTests(unittest.TestCase):
    """F3-74：block study_id 单侧 strip，带空白 study_id 永远无法匹配。"""

    def test_trailing_space_study_id_matches_its_block(self):
        effects, blocks = _synth_fixture(study_ids=("S1 ", "S1 ", "S2", "S2"))
        result = ds.synthesize_dependent_effects(effects, blocks)
        self.assertEqual([row["estimate"] for row in result["estimates"]], [0.2, 0.30000000000000004])

    def test_genuinely_different_study_is_still_rejected(self):
        effects, blocks = _synth_fixture(study_ids=("S1", "S1", "S2", "S2"))
        effects[0] = {**effects[0], "study_id": "S9"}
        with self.assertRaises(ValueError) as caught:
            ds.synthesize_dependent_effects(effects, blocks)
        self.assertIn("does not belong to study", str(caught.exception))

    def test_regular_study_ids_control_bitwise_unchanged(self):
        effects, blocks = _synth_fixture()
        result = ds.synthesize_dependent_effects(effects, blocks)
        self.assertEqual(result["estimates"],
                         [{"outcome": "x", "n_studies": 2, "estimate": 0.2,
                           "se": 0.3535533905932738, "se_scale": "identity",
                           "ci_lower": -0.49295191217483897, "ci_upper": 0.892951912174839,
                           "q": {"statistic": 0.07999999999999999, "df": 1,
                                 "p_value": 0.7772974107895216, "scope": "marginal_univariate"}},
                          {"outcome": "y", "n_studies": 2, "estimate": 0.30000000000000004,
                           "se": 0.3535533905932738, "se_scale": "identity",
                           "ci_lower": -0.39295191217483894, "ci_upper": 0.992951912174839,
                           "q": {"statistic": 0.08000000000000002, "df": 1,
                                 "p_value": 0.7772974107895214, "scope": "marginal_univariate"}}])


if __name__ == "__main__":
    unittest.main()
