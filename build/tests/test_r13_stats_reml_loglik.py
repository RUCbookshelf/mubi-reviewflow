"""r13 终扫 stats-a-0 钉子：network_random 的 REML 对数似然口径（中危）。

发现（终扫 stats-a-0）：
提交 1db6d5e（r8 FL1 MM10-09*）认为 ``-objective - residual_df*log(scale)``
等于"真 REML"再加上 ``0.5*logdet(X'X)``，于是又从上报值里多减了一次
``0.5*logdet(X'X)``。该判断的参考式（本文件取代的旧钉
build/tests/test_r8_fl1_numerical.py 原 686-700 行）漏掉了标准 REML 的
``-log|X'X|`` 项——那正是误差对照密度（orthonormal error contrasts）与
Harville 剖面式自带的归一化项。结果：1db6d5e 之后上报值偏低
``0.5*log|X'X|``，且偏移随网络设计（|X'X|）变化，与同库四个相依随机姊妹
模块（dependent_random / _three / _fiveplus / _many，均上报
``-objective - df*log(scale)``）不再同口径。

数学（本文件独立复算，与模块实现无关）：
  ℓ_ec = -0.5 * (df*log(2π) + log|V| + log|X'V⁻¹X| - log|X'X| + Q)
       = -0.5 * (df*log(2π) + log|B'VB| + u'(B'VB)⁻¹u),  B 正交补基 (B'X=0)
即"误差对照密度"，也是 Harville 剖面式含 ``-log|X'X|`` 的形式。
模块内部 objective = 0.5*(df*log2π + log|V| + log|X'V⁻¹X| - log|X'X| + Q)，
故 ``-objective - df*log(scale)`` 正是 ℓ_ec；再减 0.5*logdet 即为 1db6d5e 的
缺陷（= 平坦先验边际似然，缺 ``-log|X'X|`` 归一化）。

旧钉 → 新钉对照（fixture C，3 研究 2 参数，logdet(X'X)=log 3）：
  旧钉（去掉 ``-log|X'X|`` 的参考式）预期 -0.725791352644717
      （= 模块 1db6d5e 后的上报值；比新钉目标低 0.549306144 = 0.5*log 3）
  新钉（含 ``-log|X'X|`` 的误差对照密度）预期 -0.1764852083106725
两者之差恰为 0.5*logdet(X'X)=0.5493061443340548，与终扫报告的偏移一致。

破坏即红（R4）：修复前（基线 1d23f22）前两个测试必红，实测上报值与误差对照
密度恒差 -0.5*logdet(X'X)（fixture C -0.549306，fixture D -1.039721）。
复现材料：E:/rf214tmp/dwave/confirm-stats-a-0/reml_confirm.py
（ground truth 1 = QR 补空间，ground truth 2 = Harville 含项式，均与旧式一致）。
"""
import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np

from _numeric_assertions import assert_numeric_baseline
from coscreen import dependent_random, network_random


# 旧钉 build/tests/test_r8_fl1_numerical.py:686-700 的夹具（参照 C）。
FIXTURE_C = [
    {"study_id": "T1", "covariance_scale": "natural",
     "contrasts": [{"treatment": "A", "comparator": "B", "estimate": 2.0}],
     "covariance": [[0.04]]},
    {"study_id": "T2", "covariance_scale": "natural",
     "contrasts": [{"treatment": "B", "comparator": "C", "estimate": -1.0}],
     "covariance": [[0.09]]},
    {"study_id": "T3", "covariance_scale": "natural",
     "contrasts": [{"treatment": "A", "comparator": "C", "estimate": 0.5}],
     "covariance": [[0.06]]},
]

# 非平衡设计（含一个双臂多对比研究）：X'X = [[3,-1],[-1,2]]，
# 0.5*logdet(X'X) = 0.8047189562 ≠ 0.5493061443，证明偏移随设计变化。
FIXTURE_D = [
    {"study_id": "S1", "covariance_scale": "natural",
     "contrasts": [{"treatment": "A", "comparator": "C", "estimate": 0.5},
                   {"treatment": "B", "comparator": "C", "estimate": -0.2}],
     "covariance": [[0.04, 0.01], [0.01, 0.06]]},
    {"study_id": "S2", "covariance_scale": "natural",
     "contrasts": [{"treatment": "A", "comparator": "B", "estimate": 0.8}],
     "covariance": [[0.05]]},
    {"study_id": "S3", "covariance_scale": "natural",
     "contrasts": [{"treatment": "A", "comparator": "C", "estimate": 0.4}],
     "covariance": [[0.03]]},
    {"study_id": "S4", "covariance_scale": "natural",
     "contrasts": [{"treatment": "B", "comparator": "C", "estimate": -0.3}],
     "covariance": [[0.07]]},
]


def _blocks_and_design(studies, columns, tau2, measure="MD"):
    """按 network_random 的公共契约重建 X、y 与 V 的块对角（参考实现，只读）。"""
    designs, values, blocks = [], [], []
    for study in studies:
        contrasts = [{**row, "treatment": str(row["treatment"]).strip(),
                      "comparator": str(row["comparator"]).strip()}
                     for row in study["contrasts"]]
        design = np.asarray([
            [int(row["treatment"] == node) - int(row["comparator"] == node)
             for node in columns]
            for row in contrasts
        ], dtype=float)
        value = np.asarray([
            math.log(float(row["estimate"])) if measure in ("RR", "OR", "HR")
            else float(row["estimate"])
            for row in contrasts
        ], dtype=float)
        arms = sorted({arm for row in contrasts
                       for arm in (row["treatment"], row["comparator"])})
        incidence = np.asarray([
            [int(row["treatment"] == arm) - int(row["comparator"] == arm)
             for arm in arms]
            for row in contrasts
        ], dtype=float)
        designs.append(design)
        values.append(value)
        blocks.append(np.asarray(study["covariance"], dtype=float)
                      + tau2 * (.5 * incidence @ incidence.T))
    return designs, values, blocks


def _error_contrast_density(designs, values, blocks):
    """正交误差对照密度：B 为 X 列空间补空间的正交基（B'B=I, B'X=0）。

    与模块内部任何算式无关；同时返回 0.5*logdet(X'X) 供偏移核对。
    """
    design = np.vstack(designs)
    y = np.concatenate(values)
    n, p = design.shape
    residual_df = n - p
    covariance = np.zeros((n, n), dtype=float)
    offset = 0
    for block in blocks:
        size = block.shape[0]
        covariance[offset:offset + size, offset:offset + size] = block
        offset += size
    basis = np.linalg.qr(design, mode="complete")[0][:, p:]
    contrast_covariance = basis.T @ covariance @ basis
    contrast_values = basis.T @ y
    value = -0.5 * (
        residual_df * math.log(2 * math.pi)
        + float(np.linalg.slogdet(contrast_covariance)[1])
        + float(contrast_values @ np.linalg.solve(contrast_covariance, contrast_values))
    )
    return value, 0.5 * float(np.linalg.slogdet(design.T @ design)[1])


def _flat_prior_reference(designs, values, blocks):
    """漏掉 -log|X'X| 的"平坦先验边际似然"（旧钉参考式），独立稠密算式。"""
    design = np.vstack(designs)
    y = np.concatenate(values)
    n, p = design.shape
    covariance = np.zeros((n, n), dtype=float)
    offset = 0
    for block in blocks:
        size = block.shape[0]
        covariance[offset:offset + size, offset:offset + size] = block
        offset += size
    inverse = np.linalg.inv(covariance)
    information = design.T @ inverse @ design
    coefficients = np.linalg.solve(information, design.T @ inverse @ y)
    residual = y - design @ coefficients
    quadratic = float(residual @ inverse @ residual)
    return -0.5 * (
        (n - p) * math.log(2 * math.pi)
        + float(np.linalg.slogdet(covariance)[1])
        + float(np.linalg.slogdet(information)[1])
        + quadratic
    )


def _reported(studies, reference):
    result = network_random.synthesize_network_random(studies, "MD", reference=reference)
    columns = [node for node in result["treatments"] if node != reference]
    tau2 = float(result["random_effects"]["tau2"])
    designs, values, blocks = _blocks_and_design(studies, columns, tau2)
    return result, _error_contrast_density(designs, values, blocks)


class StatsA0RemlLogLikelihoodTests(unittest.TestCase):
    """stats-a-0：上报值必须是误差对照密度（含 -log|X'X|），不再多减半项。"""

    def test_reported_equals_error_contrast_density_balanced_design(self):
        """平衡设计（旧钉夹具）：QR 补空间直接算出的密度必须等于上报值。"""
        result, (density, half_logdet) = _reported(FIXTURE_C, "C")
        reported = float(result["fit"]["reml_log_likelihood"])
        self.assertLess(abs(reported - density), 1e-9,
                        msg="上报值必须等于误差对照密度；修复前恒差 "
                            f"-0.5*logdet(X'X)=-{half_logdet:.12f}，"
                            f"reported={reported!r} density={density!r}")

    def test_reported_equals_error_contrast_density_unbalanced_design(self):
        """非平衡设计：同一个偏移必须随 X'X 变化而消失（排除巧合常数）。"""
        result, (density, half_logdet) = _reported(FIXTURE_D, "C")
        reported = float(result["fit"]["reml_log_likelihood"])
        self.assertLess(abs(reported - density), 1e-9,
                        msg="非平衡设计同样必须等于误差对照密度；修复前恒差 "
                            f"-0.5*logdet(X'X)=-{half_logdet:.12f}")
        self.assertGreater(abs(half_logdet - 0.5 * math.log(3.0)), 0.1,
                           msg="夹具必须让 logdet(X'X) 与旧钉夹具明显不同")

    def test_pinned_value_matches_harville_profile_reference(self):
        """等强度钉新值（旧钉→新钉）：Harville 剖面式含 -log|X'X| 的形式。

        旧钉参考式（漏 -log|X'X|）在该夹具上给出 -0.725791352644717，
        恰为 1db6d5e 之后模块上报的值；新钉给出 -0.1764852083106725。
        """
        result, _ = _reported(FIXTURE_C, "C")
        reported = float(result["fit"]["reml_log_likelihood"])
        tau2 = float(result["random_effects"]["tau2"])
        design = np.array([[1.0, -1.0], [0.0, 1.0], [1.0, 0.0]])  # 列 (A,B)，参照 C
        values = np.array([2.0, -1.0, 0.5])
        covariance = np.diag([0.04 + tau2, 0.09 + tau2, 0.06 + tau2])
        inverse = np.linalg.inv(covariance)
        information = design.T @ inverse @ design
        coefficients = np.linalg.solve(information, design.T @ inverse @ values)
        residual = values - design @ coefficients
        quadratic = float(residual @ inverse @ residual)
        reml_ll = -0.5 * (math.log(2 * math.pi) + math.log(np.linalg.det(covariance))
                          + math.log(np.linalg.det(information))
                          - math.log(np.linalg.det(design.T @ design)) + quadratic)
        self.assertLess(abs(reported - reml_ll), 1e-9,
                        msg=f"上报值必须等于含 -log|X'X| 的参考式；旧钉目标 "
                            f"-0.725791352644717，新钉目标 {reml_ll!r}")
        self.assertAlmostEqual(reported, -0.1764852083106725, places=9)
        self.assertAlmostEqual(reml_ll, -0.1764852083106725, places=9)

    def test_deviation_from_flat_prior_is_half_logdet(self):
        """口径对照：本口径比"平坦先验边际似然"（漏 -log|X'X|）恰高 0.5*logdet。

        1db6d5e 的缺陷正是把上报值降到了平坦先验值；该值随 X'X 变化，
        因此跨网络不可比。两种夹具都验证：reported - flat = +0.5*logdet。
        """
        for studies, reference in ((FIXTURE_C, "C"), (FIXTURE_D, "C")):
            with self.subTest(reference=reference, studies=studies[0]["study_id"]):
                result, (density, half_logdet) = _reported(studies, reference)
                reported = float(result["fit"]["reml_log_likelihood"])
                columns = [node for node in result["treatments"] if node != reference]
                designs, values, blocks = _blocks_and_design(
                    studies, columns, float(result["random_effects"]["tau2"]))
                flat = _flat_prior_reference(designs, values, blocks)
                self.assertLess(abs(reported - density), 1e-9)
                self.assertAlmostEqual(reported - flat, half_logdet, places=9)
                design_matrix = np.vstack(designs)
                self.assertAlmostEqual(
                    half_logdet,
                    0.5 * float(np.linalg.slogdet(design_matrix.T @ design_matrix)[1]),
                    places=9)


class StatsA0RegularControlTests(unittest.TestCase):
    """常规对照（基线绿，修复后必须仍绿）：点估计/tau2 不因诊断字段修正而变。"""

    def test_estimates_and_tau2_match_baseline(self):
        result = network_random.synthesize_network_random(FIXTURE_C, "MD", reference="C")
        assert_numeric_baseline(result["random_effects"]["tau2"], 0.019999999778362258, abs_tol=1e-8)
        by_pair = {(e["treatment"], e["comparator"]): (e["estimate"], e["se"])
                   for e in result["estimates"]}
        assert_numeric_baseline(
            by_pair[("A", "B")], (1.8800000001241177, 0.2135415646810929), abs_tol=1e-8
        )
        assert_numeric_baseline(
            by_pair[("A", "C")], (0.6599999999822692, 0.23323807547680395), abs_tol=1e-8
        )
        assert_numeric_baseline(
            by_pair[("B", "C")], (-1.2200000001418485, 0.24819347260690858), abs_tol=1e-8
        )


class StatsA0SiblingConventionTests(unittest.TestCase):
    """同库相依随机姊妹模块上报的是同一（误差对照密度）口径。"""

    def test_dependent_random_reports_error_contrast_density(self):
        rng = np.random.default_rng(3)
        rows_by_block, matrices = [], []
        for _ in range(4):
            theta = 0.2 * rng.standard_normal(2)
            rows_by_block.append([{"outcome": label, "estimate": float(value)}
                                  for label, value in zip(("x", "y"), theta)])
            matrices.append(np.eye(2) * 0.01)
        fixed = {"analysis_scale": "identity",
                 "estimates": [{"outcome": "x"}, {"outcome": "y"}],
                 "q": {"statistic": 1.0, "df": 1, "p_value": 0.5}}
        result = dependent_random.synthesize_random_reml_un(
            fixed, rows_by_block, matrices, ["x", "y"], "MD")
        tau = np.asarray(result["between_study_covariance"]["matrix"], dtype=float)
        designs, values, blocks = [], [], []
        for rows, matrix in zip(rows_by_block, matrices):
            index = [{"x": 0, "y": 1}[row["outcome"]] for row in rows]
            design = np.zeros((len(rows), 2), dtype=float)
            design[np.arange(len(rows)), index] = 1.0
            designs.append(design)
            values.append(np.asarray([row["estimate"] for row in rows], dtype=float))
            blocks.append(matrix + tau[np.ix_(index, index)])
        density, _ = _error_contrast_density(designs, values, blocks)
        reported = float(result["fit"]["reml_log_likelihood"])
        self.assertLess(abs(reported - density), 1e-9,
                        msg="姊妹模块本已上报误差对照密度；network_random 必须同口径")
