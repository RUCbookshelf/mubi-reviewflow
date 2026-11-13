"""r9 L5c 钉子：依赖效应 REML 族 + dependent_runs 入口（清单 §3.5 18 条）。

覆盖（inventory_夜扫286确认分组.md §3.5，泳道 L5c）：
  b12·F3-50 / F3-51 / F3-52 / F3-53        coscreen/dependent_random.py
  b04·F3-54【高】· F3-58【高】              资源预算（模块层 wall-clock 收口）
  b12·F3-55 / F3-56 / F3-57 / F3-59 / F3-60  coscreen/dependent_random_fiveplus.py / _many.py
  b05·F3-62【高】                            coscreen/dependent_random_three.py
  b05·F3-63 / b13·F3-64 / F3-65 / F3-66 / F3-68 / F3-69  coscreen/dependent_runs.py

破坏即红（R4）：每个条目的第一个断言钉修复后的行为，在基线 bfd07ae 上必红
（修前实测见各 test 注释）；标「常规对照」的断言是逐位不变钉子（基线绿、修复后
必须仍绿），钉住 R1/R2：无格式变更、不新增拒绝合法输入的严格性。

同族口径（清单 §6.3）：退化 SE 族（F3-50/F3-60 系数协方差负对角、F3-68 采样方差
溢出、F3-64 非有限输入）统一走同一有限性清洗入口（`_coefficient_standard_errors`
/ `_sampling_variance` / `_clean_non_finite`，均为「命名域错误或降到 null，绝不裸
math/sqrt/json 异常逃逸」）；资源上限族（F3-54/F3-58）统一走 dependent_random 的
`_EvaluationBudget`（同一墙钟上限 600s、同一 +inf 优雅降级形态，各载体共享）。
"""
import json
import math
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np

from _numeric_assertions import assert_numeric_baseline
from coscreen import db as db_mod
from coscreen import dependent_random
from coscreen import dependent_random_fiveplus as fiveplus
from coscreen import dependent_random_many as many
from coscreen import dependent_random_three as three
from coscreen.db import _SCHEMA, _connect
from coscreen.dependent_runs import create_dependent_gls_run
from coscreen.models import Article
from coscreen.review_analysis import save_effect
from coscreen.analysis_storage import ensure_analysis_storage, set_effect_design


def _fixed_result(n: int) -> dict:
    return {"analysis_scale": "identity",
            "estimates": [{"outcome": f"o{index}"} for index in range(n)],
            "q": {"statistic": 1.0, "df": 1, "p_value": 0.5}}


def _outcome_blocks(labels, studies: int, *, tau: float = 0.2, variance: float = 0.01,
                    seed: int = 3):
    """平衡的 n 结局 × studies 研究夹具（独立采样的真值）。"""
    rng = np.random.default_rng(seed)
    rows_by_block, matrices = [], []
    for _ in range(studies):
        theta = tau * rng.standard_normal(len(labels))
        rows_by_block.append([{"outcome": label, "estimate": float(value)}
                              for label, value in zip(labels, theta)])
        matrices.append(np.eye(len(labels)) * variance)
    return rows_by_block, matrices


def _two_outcome_blocks(studies: int = 14):
    rows_by_block, matrices = [], []
    for index in range(studies):
        rows_by_block.append([{"outcome": "x", "estimate": 0.10 + 0.03 * index},
                              {"outcome": "y", "estimate": 0.20 + 0.02 * index}])
        matrices.append(np.array([[0.01, 0.002], [0.002, 0.012]]))
    return rows_by_block, matrices


# --------------------------------------------------------------------------
# b12·F3-50：dependent_random.py:238-246 系数协方差对角线开方前无正性守卫
# --------------------------------------------------------------------------
# 修前实测（基线 bfd07ae，本工作树独立复现）：注入负对角后
#   `escaped=true, ValueError: math domain error`（裸 math.sqrt）；
#   真实基线 converged=true se=[0.046595, 0.045711]。
class F3_50CoefficientVarianceGuardTests(unittest.TestCase):
    """F3-50：负对角必须以命名域错误收口，不能是裸 math domain error。"""

    def test_negative_diagonal_is_a_named_domain_error(self):
        with self.assertRaises(ValueError) as caught:
            dependent_random._coefficient_standard_errors(np.array([[-1e-18, 0.0],
                                                                   [0.0, 0.01]]))
        self.assertIn("coefficient covariance", str(caught.exception))
        self.assertNotIn("math domain", str(caught.exception))

    def test_non_finite_diagonal_is_a_named_domain_error(self):
        with self.assertRaises(ValueError) as caught:
            dependent_random._coefficient_standard_errors(
                np.array([[float("nan"), 0.0], [0.0, 0.01]]))
        self.assertIn("coefficient covariance", str(caught.exception))

    def test_carrier_uses_the_shared_guard(self):
        """真实链路：2 结局载体的 SE 必须经同一守卫产出（负对角 → 命名域错误）。"""
        rows_by_block, matrices = _two_outcome_blocks()
        captured = {}
        original = dependent_random._coefficient_standard_errors

        def spy(covariance):
            captured["diagonal"] = np.diag(covariance).copy()
            return original(covariance)

        dependent_random._coefficient_standard_errors = spy
        try:
            result = dependent_random.synthesize_random_reml_un(
                _fixed_result(2), rows_by_block, matrices, ["x", "y"], "MD")
            self.assertTrue(result["fit"]["converged"])
            self.assertTrue((captured["diagonal"] > 0).all())
            # 同一守卫收到负对角时必须是命名域错误
            with self.assertRaises(ValueError) as caught:
                dependent_random._coefficient_standard_errors(
                    np.array([[-1e-18, 0.0], [0.0, 0.01]]))
            self.assertIn("coefficient covariance", str(caught.exception))
        finally:
            dependent_random._coefficient_standard_errors = original

    def test_regular_fit_unchanged(self):
        """常规对照（基线绿，修复后必须仍绿）：收敛拟合的 SE 逐位不变。"""
        rows_by_block, matrices = _two_outcome_blocks()
        result = dependent_random.synthesize_random_reml_un(
            _fixed_result(2), rows_by_block, matrices, ["x", "y"], "MD")
        self.assertEqual([round(row["se"], 6) for row in result["estimates"]],
                         [0.035184, 0.033013])


# --------------------------------------------------------------------------
# b12·F3-51：dependent_random.py:183-186 可辨识性检查每次分配 4 个 n×n 稠密阵
# --------------------------------------------------------------------------
# 修前实测：`4n²×8: 2000→128 MB、20000→12.8 GB`；dose/nonlinear 随库内曲线数增长。
class F3_51IdentifiabilityMemoryTests(unittest.TestCase):
    """F3-51：块对角结构下按块累加，内存随最大研究块而非总效应数增长。"""

    def test_blockwise_matches_dense_eigenvalues(self):
        """同构块与异质块都必须与稠密路径一致。

        r13 终扫 stats-b-0：全同构块（所有研究方差相同）会让被丢弃的跨块项
        精确相消，掩盖分块路径只累加块对角项的缺陷；这里同时比较异质块夹具
        （方差/缺失结局随研究变化，相对差 ≤1e-10）。完整回归钉见
        build/tests/test_r13_stats_blockwise_information.py。
        """
        rng = np.random.default_rng(11)
        tau = np.array([[0.02, 0.004], [0.004, 0.03]])
        rows_by_block, matrices = _two_outcome_blocks(studies=12)
        homogeneous = ([[0, 1]] * len(rows_by_block),
                       [matrix / 10.0 for matrix in matrices])
        heterogeneous_outcomes, heterogeneous_scaled = [], []
        for index in range(12):
            outcomes_local = [0, 1] if index % 4 else [index % 2]
            size = len(outcomes_local)
            base = rng.random((size, size)) * 0.02 + np.eye(size) * 0.01
            heterogeneous_outcomes.append(outcomes_local)
            heterogeneous_scaled.append(base @ base.T + np.eye(size) * 0.001)
        for label, (outcomes, scaled) in (
            ("homogeneous", homogeneous),
            ("heterogeneous", (heterogeneous_outcomes, heterogeneous_scaled)),
        ):
            with self.subTest(fixture=label):
                blockwise = dependent_random._blockwise_information_eigenvalues(
                    outcomes, scaled, tau)
                self.assertEqual(blockwise.shape, (3,))
                self.assertTrue(np.isfinite(blockwise).all())
                # 与历史稠密实现对比（同一数量级、同一符号）
                total = sum(len(outcomes_local) for outcomes_local in outcomes)
                design = np.zeros((total, 2))
                marginal = np.zeros((total, total))
                derivatives = [np.zeros((total, total)) for _ in range(3)]
                offset = 0
                for outcomes_local, sampling in zip(outcomes, scaled):
                    count = len(outcomes_local)
                    rows = np.arange(offset, offset + count)
                    design[rows, outcomes_local] = 1.0
                    marginal[np.ix_(rows, rows)] = (
                        sampling + tau[np.ix_(outcomes_local, outcomes_local)])
                    for local, outcome in enumerate(outcomes_local):
                        derivatives[outcome][offset + local, offset + local] = 1.0
                    if count == 2:
                        derivatives[2][offset, offset + 1] = 1.0
                        derivatives[2][offset + 1, offset] = 1.0
                    offset += count
                weight = np.linalg.inv(marginal)
                fixed_information = design.T @ weight @ design
                weighted_design = weight @ design
                projection = weight - weighted_design @ np.linalg.solve(
                    fixed_information, weighted_design.T)
                fisher = np.asarray([[0.5 * np.trace(projection @ left @ projection @ right)
                                      for right in derivatives] for left in derivatives])
                scale = np.sqrt(np.diag(fisher))
                dense = np.linalg.eigvalsh(fisher / np.outer(scale, scale))
                np.testing.assert_allclose(blockwise, dense, rtol=1e-10, atol=1e-12,
                                           err_msg=label)

    def test_large_input_never_allocates_dense_matrices(self):
        """2400 效应走上块累加分支，堆峰值远低于 4n²×8=184 MB。"""
        self.assertLessEqual(dependent_random._DENSE_IDENTIFIABILITY_MAX_EFFECTS, 2000)
        source = Path(dependent_random.__file__).read_text(encoding="utf-8")
        self.assertIn("_blockwise_information_eigenvalues", source)
        self.assertIn("total <= _DENSE_IDENTIFIABILITY_MAX_EFFECTS", source)
        import tracemalloc
        blocks = 1200
        outcomes = [[0, 1]] * blocks
        scaled = [np.array([[0.001, 0.0001], [0.0001, 0.002]]) for _ in range(blocks)]
        tau = np.array([[0.02, 0.004], [0.004, 0.03]])
        tracemalloc.start()
        try:
            eigenvalues = dependent_random._blockwise_information_eigenvalues(
                outcomes, scaled, tau)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertTrue(np.isfinite(eigenvalues).all())
        self.assertLess(peak, 50e6, f"块累加的堆峰值应远低于稠密的 184 MB，实测 {peak/1e6:.1f} MB")

    def test_end_to_end_large_input_uses_blockwise_path(self):
        """超阈值输入的真实调用必须走块累加（不进行稠密分配）。"""
        rows, matrices = [], []
        for index in range(1200):
            rows.append([{"outcome": "x", "estimate": 0.10 + 0.0001 * index},
                         {"outcome": "y", "estimate": 0.20 + 0.0002 * index}])
            matrices.append(np.array([[0.01, 0.002], [0.002, 0.012]]))
        original = dependent_random._blockwise_information_eigenvalues
        calls = []

        def spy(*args, **kwargs):
            calls.append(len(args[0]))
            return original(*args, **kwargs)

        dependent_random._blockwise_information_eigenvalues = spy
        try:
            result = dependent_random.synthesize_random_reml_un(
                _fixed_result(2), rows, matrices, ["x", "y"], "MD")
        finally:
            dependent_random._blockwise_information_eigenvalues = original
        self.assertEqual(calls, [1200])
        self.assertTrue(result["fit"]["converged"])
        self.assertTrue(math.isfinite(
            result["fit"]["covariance_information_condition"]))


# --------------------------------------------------------------------------
# b12·F3-52：dependent_random.py:149-154 paired_residuals 忽略块内 outcome 顺序
# --------------------------------------------------------------------------
# 修前实测：规范序 rho 初值路径 vs 混序 → 0.32563633 / 0.30351315；
# 最终估计重算受影响（本工作树实测 [0.275, 0.34] vs [0.27395, 0.34076]）。
class F3_52PairedResidualOrderTests(unittest.TestCase):
    """F3-52：块内行序不影响 rho 初值 → 同数据的估计与 SE 逐位一致。"""

    @staticmethod
    def _heteroscedastic_blocks(studies: int = 12):
        """方差不齐且两结局量级差大：rho 初值对行序敏感的可触发夹具。"""
        rows, matrices = [], []
        for index in range(studies):
            rows.append([{"outcome": "x", "estimate": 0.05 * index},
                         {"outcome": "y", "estimate": 0.3 + 0.02 * index}])
            matrices.append(np.array([[0.01 + 0.002 * index, 0.001], [0.001, 0.02]]))
        return rows, matrices

    def _fits(self, rows, matrices, *, swaps):
        """规范序与「若干块反序 + 协方差同步置换」的同一份数据。"""
        permutation = [1, 0]
        mixed_rows = [list(reversed(block)) if (index + 1) in swaps else block
                      for index, block in enumerate(rows)]
        mixed_matrices = [matrix[np.ix_(permutation, permutation)]
                          if (index + 1) in swaps else matrix
                          for index, matrix in enumerate(matrices)]
        starts = []
        original_minimize = dependent_random.minimize

        def spy(objective, start, **kwargs):
            starts.append(np.asarray(start, dtype=float).copy())
            return original_minimize(objective, start, **kwargs)

        dependent_random.minimize = spy
        try:
            forward = dependent_random.synthesize_random_reml_un(
                _fixed_result(2), rows, matrices, ["x", "y"], "MD")
            backward = dependent_random.synthesize_random_reml_un(
                _fixed_result(2), mixed_rows, mixed_matrices, ["x", "y"], "MD")
        finally:
            dependent_random.minimize = original_minimize
        return starts, forward, backward

    def test_rho_initial_ignores_block_row_order(self):
        """修前实测：规范序 rho=0.9 vs 混序 0.3524083499846103（同一份数据）。"""
        rows, matrices = self._heteroscedastic_blocks()
        starts, forward, backward = self._fits(rows, matrices, swaps=(2, 4, 6))
        self.assertEqual(starts[0][2], starts[1][2],
                         "块内行序不得改变 rho 初值")
        self.assertEqual(starts[0][2], 0.9)

    def test_row_order_does_not_change_the_fit(self):
        rows, matrices = self._heteroscedastic_blocks()
        _, forward, backward = self._fits(rows, matrices, swaps=(2, 4, 6))
        np.testing.assert_allclose(
            [row["estimate"] for row in forward["estimates"]],
            [row["estimate"] for row in backward["estimates"]], rtol=0.0, atol=1e-9)
        np.testing.assert_allclose(
            [row["se"] for row in forward["estimates"]],
            [row["se"] for row in backward["estimates"]], rtol=1e-8, atol=0.0)

    def test_canonical_order_unchanged(self):
        """常规对照（规范序输入逐位不变）。"""
        rows_by_block, matrices = _two_outcome_blocks()
        result = dependent_random.synthesize_random_reml_un(
            _fixed_result(2), rows_by_block, matrices, ["x", "y"], "MD")
        self.assertEqual([round(row["estimate"], 9) for row in result["estimates"]],
                         [0.295, 0.33])


# --------------------------------------------------------------------------
# b12·F3-53：dependent_random.py:32,42,71 重复 outcome 标签 → 裸 math domain error
# --------------------------------------------------------------------------
# 修前实测：`outcome_order=["a","a"] -> ValueError: math domain error`。
class F3_53DuplicateOutcomeLabelTests(unittest.TestCase):
    """F3-53：重复 outcome 标签必须是域错误，不是裸 math domain error。"""

    def test_duplicate_outcomes_rejected_by_name(self):
        rows = [[{"outcome": "a", "estimate": 1.0},
                 {"outcome": "a", "estimate": 2.0}]] * 6
        with self.assertRaises(ValueError) as caught:
            dependent_random.synthesize_random_reml_un(
                _fixed_result(2), rows, [np.eye(2) * 0.01] * 6, ["a", "a"], "MD")
        self.assertIn("distinct outcomes", str(caught.exception))

    def test_single_outcome_still_rejected(self):
        with self.assertRaises(ValueError):
            dependent_random.synthesize_random_reml_un(
                _fixed_result(1), [[{"outcome": "a", "estimate": 1.0}]],
                [np.eye(1) * 0.01], ["a"], "MD")


# --------------------------------------------------------------------------
# b12·F3-55：dependent_random_fiveplus.py:140-142,180-182,425
#            rows_by_block 与 matrices 等长从不校验 → zip 静默截断
# --------------------------------------------------------------------------
# 修前实测：`20 blocks/19 matrices → converged=true, n_paired=20`。
class F3_55BlockMatrixLengthTests(unittest.TestCase):
    """F3-55：块与协方差矩阵不等长必须报域错误，不得静默截断仍报收敛。"""

    def test_short_matrix_list_is_rejected(self):
        rows, matrices = _outcome_blocks(list("abcde"), 20)
        with self.assertRaises(ValueError) as caught:
            fiveplus.synthesize_random_reml_un_fiveplus(
                _fixed_result(5), rows, matrices[:-1], list("abcde"), "MD")
        self.assertIn("covariance matrix", str(caught.exception))

    def test_mismatched_matrix_dimension_is_rejected(self):
        rows, matrices = _outcome_blocks(list("abcde"), 20)
        broken = list(matrices)
        broken[0] = np.eye(4) * 0.01
        with self.assertRaises(ValueError):
            fiveplus.synthesize_random_reml_un_fiveplus(
                _fixed_result(5), rows, broken, list("abcde"), "MD")

    def test_matched_lengths_still_fit(self):
        """常规对照（基线绿，修复后必须仍绿）：等长输入照常收敛。"""
        rows, matrices = _outcome_blocks(list("abcde"), 20)
        result = fiveplus.synthesize_random_reml_un_fiveplus(
            _fixed_result(5), rows, matrices, list("abcde"), "MD")
        self.assertTrue(result["fit"]["converged"])
        self.assertEqual(len(result["estimates"]), 5)


# --------------------------------------------------------------------------
# b12·F3-56：dependent_random_fiveplus.py:357-360 「|」拼接键非单射
# --------------------------------------------------------------------------
# 修前实测：`n_pairs=10, n_keys=9, duplicate_key="X|Y|Z"`。
class F3_56OverlapKeyTests(unittest.TestCase):
    """F3-56：标签含「|」时重叠计数不得被覆盖。"""

    def test_labels_containing_the_separator_do_not_collide(self):
        labels = ["X|Y", "Z", "P", "Q", "R"]
        rows, matrices = _outcome_blocks(labels, 20)
        result = fiveplus.synthesize_random_reml_un_fiveplus(
            _fixed_result(5), rows, matrices, labels, "MD")
        overlaps = result["fit"]["pairwise_overlap_studies"]
        self.assertEqual(len(overlaps), 10)
        self.assertEqual(len({json.dumps(entry["outcomes"]) for entry in overlaps}), 10)
        self.assertIn({"outcomes": ["X|Y", "Z"], "n_studies": 20}, overlaps)

    def test_batch_fixture_pair_values_are_not_overwritten(self):
        """清单原探针：对 (X, Y|Z)=19 被对 (X|Y, Z)=20 覆盖 → 修复后两者并存。"""
        pair_counts = {("X", "Y|Z"): 19, ("X|Y", "Z"): 20, ("X", "W"): 20}
        overlaps = dependent_random._pairwise_overlap_counts(
            pair_counts, ["X", "Y|Z", "W", "X|Y", "Z"])
        self.assertEqual(len(overlaps), 3)
        self.assertEqual(len({json.dumps(entry["outcomes"]) for entry in overlaps}), 3)
        self.assertIn({"outcomes": ["X", "Y|Z"], "n_studies": 19}, overlaps)
        self.assertIn({"outcomes": ["X|Y", "Z"], "n_studies": 20}, overlaps)

    def test_plain_labels_report_all_pairs(self):
        """常规对照：普通标签仍报满 10 对且计数正确。"""
        labels = list("abcde")
        rows, matrices = _outcome_blocks(labels, 20)
        result = fiveplus.synthesize_random_reml_un_fiveplus(
            _fixed_result(5), rows, matrices, labels, "MD")
        overlaps = result["fit"]["pairwise_overlap_studies"]
        self.assertEqual(len(overlaps), 10)
        self.assertTrue(all(entry["n_studies"] == 20 for entry in overlaps))


# --------------------------------------------------------------------------
# b12·F3-57：dependent_random_fiveplus.py:75-79 n_outcomes≤1 无守卫
# --------------------------------------------------------------------------
# 修前实测：`outcome_order=["a"] -> ValueError: min() arg is an empty sequence`。
class F3_57CoreDimensionTests(unittest.TestCase):
    """F3-57：通用 n-outcome 核对 ≤1 结局报域错误，不抛 min() 空序列。"""

    def test_single_outcome_core_rejected_by_name(self):
        with self.assertRaises(ValueError) as caught:
            fiveplus._synthesize_random_reml_un(
                _fixed_result(1), [[{"outcome": "a", "estimate": 1.0}]],
                [np.eye(1) * 0.01], ["a"], "MD")
        self.assertIn("at least two outcomes", str(caught.exception))
        self.assertNotIn("min()", str(caught.exception))

    def test_empty_core_rejected_by_name(self):
        with self.assertRaises(ValueError) as caught:
            fiveplus._synthesize_random_reml_un(_fixed_result(1), [], [], [], "MD")
        self.assertIn("at least two outcomes", str(caught.exception))


# --------------------------------------------------------------------------
# b12·F3-60：dependent_random_many.py:275-281 系数协方差对角线开方前无正性守卫
# --------------------------------------------------------------------------
# 修前实测：`注入负对角 → escaped=true, ValueError: math domain error`。
class F3_60FourOutcomeVarianceGuardTests(unittest.TestCase):
    """F3-60：4 结局载体与 F3-50 同型，走同一守卫。"""

    def test_carrier_calls_the_shared_guard(self):
        rows, matrices = _outcome_blocks(list("abcd"), 20)
        original = many._coefficient_standard_errors
        seen = []

        def spy(covariance):
            seen.append(np.diag(covariance).copy())
            return original(covariance)

        many._coefficient_standard_errors = spy
        try:
            result = many.synthesize_random_reml_un_four(
                _fixed_result(4), rows, matrices, list("abcd"), "MD")
        finally:
            many._coefficient_standard_errors = original
        self.assertTrue(result["fit"]["converged"])
        self.assertEqual(len(seen), 1)
        self.assertTrue((seen[0] > 0).all())

    def test_four_outcome_baseline_converges(self):
        """常规对照（基线 converged=true）。"""
        rows, matrices = _outcome_blocks(list("abcd"), 20)
        result = many.synthesize_random_reml_un_four(
            _fixed_result(4), rows, matrices, list("abcd"), "MD")
        self.assertTrue(result["fit"]["converged"])
        self.assertEqual(len(result["estimates"]), 4)


# --------------------------------------------------------------------------
# b05·F3-62【高】：dependent_random_three.py:218 梯度绝对阈值无法吸收比值测度平移
# --------------------------------------------------------------------------
# 修前实测（batch05 夹具：60 块、τ 真值 diag(0.2,0.1,0.3)、抽样方差 0.01）：
#   shift=0/ln2.7 OK（loglik 逐位相同 69.9061281445）；
#   shift≥ln10 → `projected gradient norm 0.000384` 三起点全拒。
class F3_62RatioShiftStationarityTests(unittest.TestCase):
    """F3-62：比值测度整体平移不得让完全可拟合的数据全部起点被拒。"""

    @staticmethod
    def _shifted(shift: float, studies: int = 60, seed: int = 11):
        rng = np.random.default_rng(seed)
        rows_by_block, matrices = [], []
        for _ in range(studies):
            theta = np.array([0.2, 0.1, 0.3]) * rng.standard_normal(3)
            values = np.exp(theta + shift)
            rows_by_block.append([{"outcome": label, "estimate": float(value)}
                                  for label, value in zip("abc", values)])
            matrices.append(np.eye(3) * 0.01)
        return rows_by_block, matrices

    def test_large_ratio_shift_still_fits(self):
        for shift in (math.log(10), math.log(100), math.log(300)):
            with self.subTest(shift=round(shift, 4)):
                rows, matrices = self._shifted(shift)
                result = three.synthesize_random_reml_un_three(
                    _fixed_result(3), rows, matrices, list("abc"), "RR")
                self.assertGreaterEqual(result["fit"]["valid_starts"], 1)

    def test_shift_is_absorbed_by_the_means(self):
        """关键对照：平移后 loglik 与 τ 对角线在容差内不变（目标函数平移恒等）。"""
        shifted_rows, shifted_matrices = self._shifted(math.log(300))
        base_rows, base_matrices = self._shifted(0.0)
        shifted = three.synthesize_random_reml_un_three(
            _fixed_result(3), shifted_rows, shifted_matrices, list("abc"), "RR")
        base = three.synthesize_random_reml_un_three(
            _fixed_result(3), base_rows, base_matrices, list("abc"), "RR")
        self.assertAlmostEqual(shifted["fit"]["reml_log_likelihood"],
                               base["fit"]["reml_log_likelihood"], places=5)
        for index in range(3):
            self.assertAlmostEqual(
                shifted["between_study_covariance"]["matrix"][index][index],
                base["between_study_covariance"]["matrix"][index][index], places=6)

    def test_ordinary_scale_fit_unchanged(self):
        """Check the REML fit on the original ratio scale."""
        rows, matrices = self._shifted(0.0)
        result = three.synthesize_random_reml_un_three(
            _fixed_result(3), rows, matrices, list("abc"), "RR")
        assert_numeric_baseline(result["fit"]["reml_log_likelihood"], 69.90612814454083)
        self.assertEqual(
            [round(result["between_study_covariance"]["matrix"][i][i], 6) for i in range(3)],
            [0.022008, 0.001096, 0.045199])


# --------------------------------------------------------------------------
# b04·F3-54【高】/ b04·F3-58【高】：模块层 wall-clock 预算优雅收敛
# --------------------------------------------------------------------------
# 修前实测：`8 结局×100 研究 47.66s/12629 evals`、`4 结局×100 研究 8.62s/2898 evals`；
# 外推最坏 114.7 min / 25.9 min；端点同步 def 无超时。
class F3_54_58BudgetTests(unittest.TestCase):
    """F3-54/F3-58：同一墙钟预算；超预算优雅收敛（不把可拟合数据变拒绝）。"""

    @unittest.skipIf(os.environ.get("CI") == "true", "性能/预算断言按本机速度校准")
    def test_budget_is_shared_by_every_carrier(self):
        self.assertTrue(hasattr(dependent_random, "_EvaluationBudget"))
        self.assertGreater(dependent_random._BUDGET_SECONDS, 0)
        for module in (three, many, fiveplus):
            source = Path(module.__file__).read_text(encoding="utf-8")
            self.assertIn("_EvaluationBudget()", source, module.__name__)
            self.assertIn("budget.guard", source, module.__name__)
            self.assertIn("_optimizer_options()", source, module.__name__)

    @unittest.skipIf(os.environ.get("CI") == "true", "性能/预算断言按本机速度校准")
    def test_exhausted_budget_returns_inf_not_an_exception(self):
        budget = dependent_random._EvaluationBudget(seconds=0.0)
        self.assertTrue(budget.expired())
        self.assertEqual(budget.guard(1.0), math.inf)
        self.assertTrue(budget.exhausted)

    @unittest.skipIf(os.environ.get("CI") == "true", "性能/预算断言按本机速度校准")
    def test_budget_exhaustion_is_a_named_domain_error(self):
        """预算耗尽且无合格拟合 → 明确的域错误；默认预算下同输入照常收敛。"""
        original = dependent_random._BUDGET_SECONDS
        dependent_random._BUDGET_SECONDS = 0.0
        rows, matrices = _outcome_blocks(list("abcd"), 20)
        try:
            with self.assertRaises(ValueError) as caught:
                many.synthesize_random_reml_un_four(
                    _fixed_result(4), rows, matrices, list("abcd"), "MD")
            self.assertIn("computation budget", str(caught.exception))
        finally:
            dependent_random._BUDGET_SECONDS = original

    @unittest.skipIf(os.environ.get("CI") == "true", "性能/预算断言按本机速度校准")
    def test_default_budget_keeps_legal_inputs_fitting(self):
        """R2 钉子：默认预算下 4 结局 × 20 研究照常收敛，SE 逐位不变。"""
        rows, matrices = _outcome_blocks(list("abcd"), 20)
        result = many.synthesize_random_reml_un_four(
            _fixed_result(4), rows, matrices, list("abcd"), "MD")
        self.assertTrue(result["fit"]["converged"])
        self.assertEqual([round(row["se"], 6) for row in result["estimates"]],
                         [0.056533, 0.059696, 0.041829, 0.035211])


# --------------------------------------------------------------------------
# b12·F3-59：dependent_random_many.py 成功路径与异常路径零测试覆盖（测试空洞）
# --------------------------------------------------------------------------
class F3_59ModuleCoverageTests(unittest.TestCase):
    """F3-59：本文件即为 dependent_random_many 的覆盖载体（成功 + 异常路径）。"""

    def test_success_and_failure_paths_are_covered_here(self):
        rows, matrices = _outcome_blocks(list("abcd"), 20)
        self.assertTrue(many.synthesize_random_reml_un_four(
            _fixed_result(4), rows, matrices, list("abcd"), "MD")["fit"]["converged"])
        with self.assertRaises(ValueError):
            many.synthesize_random_reml_un_four(
                _fixed_result(4), rows, matrices, list("abcd"), "RR")
        # 边界：少于三对重叠
        sparse = [[{"outcome": label, "estimate": float(index)}] for index, label in
                  enumerate(["a", "b", "a", "c"])]
        with self.assertRaises(ValueError):
            many.synthesize_random_reml_un_four(
                _fixed_result(4), sparse, [np.eye(1)] * 4, list("abcd"), "MD")

    def test_wrong_outcome_count_is_rejected(self):
        rows, matrices = _outcome_blocks(list("abcde"), 20)
        with self.assertRaises(ValueError):
            many.synthesize_random_reml_un_four(
                _fixed_result(5), rows, matrices, list("abcde"), "MD")


# --------------------------------------------------------------------------
# dependent_runs 夹具：真实 SQLite 任务库 + 已存版本 + 已设 design
# --------------------------------------------------------------------------
def _task_db(prefix: str) -> str:
    path = os.path.join(tempfile.mkdtemp(prefix=prefix), "task.db")
    with _connect(path) as conn:
        conn.executescript(_SCHEMA)
    db_mod.upsert_articles(path, [Article(zotero_key="R1", title="Ref one"),
                                  Article(zotero_key="R2", title="Ref two")], [])
    with _connect(path) as conn:
        conn.execute("INSERT INTO review_studies (id,label) VALUES ('S1','Study 1')")
        conn.execute("INSERT INTO review_studies (id,label) VALUES ('S2','Study 2')")
        conn.execute("INSERT INTO review_reports (zotero_key,study_id) VALUES ('R1','S1')")
        conn.execute("INSERT INTO review_reports (zotero_key,study_id) VALUES ('R2','S2')")
    return path


def _seed_effects(path: str, *, se: float = 0.2, input_data: dict | None = None):
    ids = []
    for index in range(2):
        result_id = save_effect(path, f"S{index + 1}", "A vs B", "pain", "t1", "MD",
                                0.5 + 0.1 * index, se, f"R{index + 1}",
                                input_data=input_data)
        with _connect(path) as conn, conn:
            ensure_analysis_storage(conn)
            set_effect_design(conn, result_id, f"c{index + 1}", f"p{index + 1}", "note")
        ids.append(result_id)
    return ids


def _versions(path: str, ids) -> dict:
    with _connect(path) as conn:
        return {result_id: conn.execute(
            "SELECT effect_version_id FROM review_effect_versions WHERE result_id=? "
            "ORDER BY revision DESC LIMIT 1", (result_id,)).fetchone()[0]
            for result_id in ids}


def _request(path: str, ids, **extra) -> dict:
    versions = _versions(path, ids)
    body = {"construct_confirmed": True, "measure": "MD",
            "effect_refs": [{"result_id": result_id,
                             "effect_version_id": versions[result_id]} for result_id in ids],
            "design_effect_ids": [str(result_id) for result_id in ids],
            "design_matrix": [[1.0, 0.0], [0.0, 1.0]],
            "design_columns": ["intercept", "grp"]}
    body.update(extra)
    return body


# --------------------------------------------------------------------------
# b05·F3-63：dependent_runs.py:132-133,136-137 effect_ids 非可迭代 → TypeError → 500
# --------------------------------------------------------------------------
# 修前实测：`effect_ids=5 -> TypeError: 'int' object is not iterable`。
class F3_63CovarianceEffectIdsTests(unittest.TestCase):
    """F3-63：effect_ids 非列表必须是 ValueError，不是 TypeError 逃逸。"""

    def test_non_iterable_effect_ids_is_a_domain_error(self):
        path = _task_db("f363")
        ids = _seed_effects(path)
        body = _request(path, ids, working_covariance={
            "kind": "block_correlation", "rho": 0.3, "source_note": "analyst",
            "effect_ids": 5})
        with self.assertRaises(ValueError) as caught:
            create_dependent_gls_run(path, "T1", body)
        self.assertIn("effect_ids", str(caught.exception))

    def test_null_effect_ids_keeps_the_absent_default(self):
        """JSON null 等同「未提供」（修前 TypeError: 'NoneType' is not iterable）。"""
        path = _task_db("f363b")
        ids = _seed_effects(path)
        body = _request(path, ids, additive_covariance={
            "source_note": "analyst", "effect_ids": None})
        result = create_dependent_gls_run(path, "T1", body)
        self.assertIn("run_id", result)

    def test_scalar_effect_ids_is_a_domain_error(self):
        path = _task_db("f363d")
        ids = _seed_effects(path)
        body = _request(path, ids, additive_covariance={
            "source_note": "analyst", "effect_ids": 5})
        with self.assertRaises(ValueError) as caught:
            create_dependent_gls_run(path, "T1", body)
        self.assertIn("effect_ids", str(caught.exception))

    def test_control_without_effect_ids_still_runs(self):
        """常规对照（基线绿）：不传 effect_ids 的请求照常成功。"""
        path = _task_db("f363c")
        ids = _seed_effects(path)
        result = create_dependent_gls_run(path, "T1", _request(
            path, ids, working_covariance={
                "kind": "block_correlation", "rho": 0.3, "source_note": "analyst"}))
        self.assertIn("run_id", result)


# --------------------------------------------------------------------------
# b13·F3-64：dependent_runs.py:176-177 快照哈希 allow_nan=False，落库 allow_nan=True
# --------------------------------------------------------------------------
# 修前实测：`1e999 → input_json 落 Infinity`；`run -> 400 Out of range float values`。
class F3_64SnapshotFiniteTests(unittest.TestCase):
    """F3-64：已存 input_json 的非有限值必须被清洗并指名，而非整次 400。"""

    def test_infinite_stored_input_no_longer_blocks_the_run(self):
        path = _task_db("f364")
        ids = _seed_effects(path, input_data={"raw": 1e999, "ok": 2.0})
        result = create_dependent_gls_run(path, "T1", _request(path, ids))
        self.assertIn("run_id", result)
        cleaned = result["input_snapshot"]["effects"][0]["input_data"]
        self.assertIsNone(cleaned["raw"])
        self.assertEqual(cleaned["ok"], 2.0)
        warnings = result["result"].get("warnings", [])
        self.assertTrue(any("input_snapshot.effects[0].input_data.raw" in text
                            for text in warnings), warnings)
        json.dumps(result["input_snapshot"], allow_nan=False)

    def test_regular_snapshot_hash_unchanged(self):
        """常规对照（无 NaN 输入时快照逐位不变、哈希可复算）。"""
        path = _task_db("f364b")
        ids = _seed_effects(path)
        result = create_dependent_gls_run(path, "T1", _request(path, ids))
        snapshot = dict(result["input_snapshot"])
        digest = snapshot.pop("data_sha256")
        import hashlib
        expected = hashlib.sha256(json.dumps(
            snapshot, ensure_ascii=False, allow_nan=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        self.assertEqual(digest, expected)


# --------------------------------------------------------------------------
# b13·F3-65：dependent_runs.py:178-181 {**request} 整体存入不可删表
# --------------------------------------------------------------------------
# 修前实测：`client_junk blob 200KB → specification_json 200,517 B`，GET 逐字回显。
class F3_65SpecificationWhitelistTests(unittest.TestCase):
    """F3-65：不可删的运行记录只保留拟合实际消费的键。"""

    def test_unknown_request_keys_are_not_persisted(self):
        path = _task_db("f365")
        ids = _seed_effects(path)
        blob = "x" * 200000
        result = create_dependent_gls_run(path, "T1", _request(
            path, ids, client_junk={"blob": blob}))
        self.assertNotIn("client_junk", result["specification"])
        self.assertLess(len(json.dumps(result["specification"], ensure_ascii=False)), 4096)
        with _connect(path) as conn:
            stored = conn.execute(
                "SELECT length(specification_json) FROM analysis_runs WHERE run_id=?",
                (result["run_id"],)).fetchone()[0]
        self.assertLess(stored, 4096)

    def test_consumed_keys_are_preserved(self):
        """R2 钉子：白名单保留全部拟合输入，审计信息不丢。"""
        path = _task_db("f365b")
        ids = _seed_effects(path)
        result = create_dependent_gls_run(path, "T1", _request(
            path, ids, inference={"vcov_type": "cr2", "level": 0.95}))
        specification = result["specification"]
        for key in ("effect_refs", "measure", "design_effect_ids", "design_matrix",
                    "design_columns", "construct_confirmed"):
            self.assertIn(key, specification)
        self.assertEqual(specification["inference"], {"vcov_type": "cr2", "level": 0.95})
        self.assertEqual(specification["project_id"], "T1")
        self.assertEqual(specification["algorithm_version"], "fixed_gls_cluster_robust_v1")


# --------------------------------------------------------------------------
# b13·F3-66：dependent_runs.py:26-27 effect_refs 裸 int() 强转（int(1.9)=1）
# --------------------------------------------------------------------------
# 修前实测：`spec effect_refs ids: [1.9, 2] / snapshot result ids: [1, 2]`。
class F3_66ExactIntegerIdTests(unittest.TestCase):
    """F3-66：小数 ID 不得被静默截断成另一个既有结果。"""

    def test_fractional_id_is_rejected(self):
        path = _task_db("f366")
        ids = _seed_effects(path)
        versions = _versions(path, ids)
        body = _request(path, ids, effect_refs=[
            {"result_id": float(ids[0]) + 0.9, "effect_version_id": versions[ids[0]]},
            {"result_id": ids[1], "effect_version_id": versions[ids[1]]}])
        with self.assertRaises(ValueError) as caught:
            create_dependent_gls_run(path, "T1", body)
        self.assertIn("result_id", str(caught.exception))

    def test_boolean_id_is_rejected(self):
        path = _task_db("f366b")
        ids = _seed_effects(path)
        versions = _versions(path, ids)
        body = _request(path, ids, effect_refs=[
            {"result_id": True, "effect_version_id": versions[ids[0]]},
            {"result_id": ids[1], "effect_version_id": versions[ids[1]]}])
        with self.assertRaises(ValueError):
            create_dependent_gls_run(path, "T1", body)

    def test_integral_and_string_forms_still_accepted(self):
        """R2 钉子：整数值浮点与数字字符串不损失数字，照常接受。"""
        path = _task_db("f366c")
        ids = _seed_effects(path)
        versions = _versions(path, ids)
        result = create_dependent_gls_run(path, "T1", _request(path, ids, effect_refs=[
            {"result_id": float(ids[0]), "effect_version_id": str(versions[ids[0]])},
            {"result_id": ids[1], "effect_version_id": versions[ids[1]]}]))
        self.assertEqual(result["input_snapshot"]["design_effect_ids"], ids)
        self.assertEqual([ref["result_id"] for ref in result["specification"]["effect_refs"]],
                         [ids[0], ids[1]])


# --------------------------------------------------------------------------
# b13·F3-68：dependent_runs.py:113 合法 se=1e200 求幂 OverflowError → 500
# --------------------------------------------------------------------------
# 修前实测：`se=1e200 run -> 500`；栈顶 `OverflowError: (34,'Result too large')`。
class F3_68SamplingVarianceRangeTests(unittest.TestCase):
    """F3-68：写入侧合法接受的极大 SE 必须是域错误，不是 OverflowError 逃逸。"""

    def test_huge_se_is_a_domain_error(self):
        path = _task_db("f368")
        ids = _seed_effects(path, se=1e200)
        with self.assertRaises(ValueError) as caught:
            create_dependent_gls_run(path, "T1", _request(path, ids))
        self.assertIn("sampling variance", str(caught.exception))

    def test_large_but_fittable_se_still_runs(self):
        """R2 钉子：1e150 的 SE 方差仍有限，照常拟合。"""
        path = _task_db("f368b")
        ids = _seed_effects(path, se=1e150)
        result = create_dependent_gls_run(path, "T1", _request(path, ids))
        self.assertIn("run_id", result)

    def test_regular_se_unchanged(self):
        """常规对照：se=0.2 的方差为 0.04，拟合成功。"""
        path = _task_db("f368c")
        ids = _seed_effects(path)
        result = create_dependent_gls_run(path, "T1", _request(path, ids))
        self.assertIn("run_id", result)


# --------------------------------------------------------------------------
# b13·F3-69：dependent_runs.py:115,127,145 三个 `or {}` 吞掉假值非对象
# --------------------------------------------------------------------------
# 修前实测：`12 种假值输入全部 200`；`'bogus' 才 400`。
class F3_69FalsyObjectTests(unittest.TestCase):
    """F3-69：假值非对象必须与真值非对象同口径拒绝。"""

    def test_falsy_non_objects_are_rejected(self):
        path = _task_db("f369")
        ids = _seed_effects(path)
        for field in ("working_covariance", "inference", "diagnostics"):
            for value in ([], 0, "", False):
                with self.subTest(field=field, value=repr(value)):
                    with self.assertRaises(ValueError) as caught:
                        create_dependent_gls_run(path, "T1", _request(
                            path, ids, **{field: value}))
                    self.assertIn(f"{field} must be an object", str(caught.exception))

    def test_absent_keys_keep_the_default(self):
        """R2 钉子：未提供该键时仍按默认空对象处理（文档口径不变）。"""
        path = _task_db("f369b")
        ids = _seed_effects(path)
        body = _request(path, ids)
        for key in ("working_covariance", "inference", "diagnostics"):
            body.pop(key, None)
        self.assertIn("run_id", create_dependent_gls_run(path, "T1", body))

    def test_null_keys_keep_the_default(self):
        """R2 钉子：显式 JSON null 与「未提供」等价（修前也被接受）。"""
        path = _task_db("f369d")
        ids = _seed_effects(path)
        result = create_dependent_gls_run(path, "T1", _request(
            path, ids, working_covariance=None, inference=None, diagnostics=None))
        self.assertIn("run_id", result)

    def test_empty_object_is_still_accepted(self):
        path = _task_db("f369c")
        ids = _seed_effects(path)
        result = create_dependent_gls_run(path, "T1", _request(
            path, ids, working_covariance={}, inference={}, diagnostics={}))
        self.assertIn("run_id", result)


if __name__ == "__main__":
    unittest.main()
