"""r13 终扫 stats-b-0 钉子：dependent_random 分块 Fisher 信息的跨块项（低危）。

发现（终扫 stats-b-0）：
``_blockwise_information_eigenvalues``（n_effects>1000 时启用，阈值见
_DENSE_IDENTIFIABILITY_MAX_EFFECTS）只累加块对角项
``trace(P_b D_i,b P_b D_j,b)``，其中 ``P_b = W_b - W_b X_b F X_b' W_b``，
而稠密路径用的是全局投影 ``P = W - W X F X'W``（F=(X'WX)⁻¹）。P 的跨块块
``-W_b X_b F X_c' W_c`` 被丢掉了，两条路径不是同一个量，docstring
「eigenvalues unchanged」不成立。

实测（本工作树，40 个异质研究块：方差/相关/缺失结局混合）：
report 材料 E:/rf214tmp/dwave/confirm-stats-b-0/probe_fidelity.py
  dense eig [0.54240621 0.93561567 1.52197812]
  block eig [0.54339368 0.93596217 1.52064415]
  条件数相对差 2.69e-3（远超 1e-9）
同构块夹具（build/tests/test_r9_l5c_dependent_reml_runs.py:134 的
``_two_outcome_blocks``）恰好让跨块项精确相消，因此旧测试掩盖了缺陷——
本文件补充异质块夹具。

修法（保持内存优势）：用低秩展开精确累加
  trace(P D_i P D_j) = trace(W D_i W D_j)
                       - trace(F (C_ij + C_ji))
                       + trace(F G_j F G_i)
其中 G_k = X' W D_k W X，C_ij = X' W D_j W D_i W X，全部经每块 count×count
小矩阵累加成 3×3 / 2×2 的 p×p 小量，不构造全局 n×n 矩阵。

破坏即红（R4）：修复前异质块比较必红（相对差 ~1e-3），修复后应 ≤1e-9。
"""
import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np

from coscreen import dependent_random


def _heterogeneous_blocks(studies: int = 40, seed: int = 11):
    """异质研究块：方差、相关、缺失结局三者都随研究变化。"""
    rng = np.random.default_rng(seed)
    outcomes_by_block, scaled = [], []
    for _ in range(studies):
        outcomes = [0, 1] if rng.random() < 0.65 else [int(rng.choice([0, 1]))]
        size = len(outcomes)
        base = rng.random((size, size)) * 0.05 + np.eye(size) * 0.1
        outcomes_by_block.append(sorted(outcomes))
        scaled.append(base @ base.T + np.eye(size) * 0.05)
    return outcomes_by_block, scaled


def _dense_eigenvalues(outcomes_by_block, scaled_matrices, tau):
    """历史稠密实现（全局投影 P = W - WX F X'W）——测试内独立参考。"""
    total = sum(len(outcomes) for outcomes in outcomes_by_block)
    design = np.zeros((total, 2), dtype=float)
    marginal = np.zeros((total, total), dtype=float)
    derivatives = [np.zeros((total, total), dtype=float) for _ in range(3)]
    offset = 0
    for outcomes, sampling in zip(outcomes_by_block, scaled_matrices):
        count = len(outcomes)
        rows = np.arange(offset, offset + count)
        design[rows, outcomes] = 1.0
        marginal[np.ix_(rows, rows)] = sampling + tau[np.ix_(outcomes, outcomes)]
        for local_index, outcome in enumerate(outcomes):
            derivatives[outcome][offset + local_index, offset + local_index] = 1.0
        if count == 2:
            derivatives[2][offset, offset + 1] = 1.0
            derivatives[2][offset + 1, offset] = 1.0
        offset += count
    weight = np.linalg.inv(marginal)
    fixed_information = design.T @ weight @ design
    weighted_design = weight @ design
    projection = weight - weighted_design @ np.linalg.solve(fixed_information,
                                                            weighted_design.T)
    fisher = np.asarray([[0.5 * np.trace(projection @ left @ projection @ right)
                          for right in derivatives] for left in derivatives])
    scale = np.sqrt(np.diag(fisher))
    return np.linalg.eigvalsh(fisher / np.outer(scale, scale))


class StatsB0BlockwiseInformationTests(unittest.TestCase):
    """stats-b-0：分块路径必须与稠密路径是同一个量（异质块）。"""

    def test_heterogeneous_blocks_match_dense_eigenvalues(self):
        outcomes_by_block, scaled = _heterogeneous_blocks()
        tau = np.array([[0.15, 0.05], [0.05, 0.12]])
        dense = _dense_eigenvalues(outcomes_by_block, scaled, tau)
        blockwise = dependent_random._blockwise_information_eigenvalues(
            outcomes_by_block, scaled, tau)
        relative = np.abs(blockwise - dense) / np.abs(dense)
        self.assertLessEqual(float(relative.max()), 1e-9,
                             msg="异质块分块特征值与稠密路径的相对差必须 ≤1e-9；"
                                 f"修复前实测 ~2.7e-3，blockwise={blockwise!r}, "
                                 f"dense={dense!r}")

    def test_heterogeneous_condition_ratio_matches_dense(self):
        """条件数（门禁实际使用的量）同样必须一致。"""
        outcomes_by_block, scaled = _heterogeneous_blocks()
        tau = np.array([[0.15, 0.05], [0.05, 0.12]])
        dense = _dense_eigenvalues(outcomes_by_block, scaled, tau)
        blockwise = dependent_random._blockwise_information_eigenvalues(
            outcomes_by_block, scaled, tau)
        dense_condition = float(dense[-1] / dense[0])
        blockwise_condition = float(blockwise[-1] / blockwise[0])
        self.assertLessEqual(abs(blockwise_condition - dense_condition) / dense_condition,
                             1e-9,
                             msg="条件数相对差必须 ≤1e-9；修复前实测 2.69e-3")

    def test_single_outcome_blocks_match_dense(self):
        """单行块（缺失结局）占多数时跨块项同样不可丢。

        需要少量双行块，否则相关系数方向的导数恒为零、Fisher 矩阵本身不可辨
        （两条路径都会以 LinAlgError/NaN 收口），无法比较特征值。
        """
        rng = np.random.default_rng(29)
        outcomes_by_block, scaled = [], []
        for index in range(24):
            if index % 5 == 0:
                size, outcomes = 2, [0, 1]
                base = rng.random((size, size)) * 0.02 + np.eye(size) * 0.05
                scaled.append(base @ base.T + np.eye(size) * 0.02)
            else:
                outcomes = [index % 2]
                scaled.append(np.asarray([[0.04 + 0.2 * rng.random()]]))
            outcomes_by_block.append(outcomes)
        tau = np.array([[0.08, 0.02], [0.02, 0.09]])
        dense = _dense_eigenvalues(outcomes_by_block, scaled, tau)
        blockwise = dependent_random._blockwise_information_eigenvalues(
            outcomes_by_block, scaled, tau)
        self.assertLessEqual(float(np.max(np.abs(blockwise - dense) / np.abs(dense))),
                             1e-9, msg=f"blockwise={blockwise!r}, dense={dense!r}")

    def test_end_to_end_forced_blockwise_matches_dense(self):
        """临时调低阈值强制走分块路径，端到端条件数与稠密路径一致。"""
        rng = np.random.default_rng(7)
        rows_by_block, matrices = [], []
        for index in range(12):
            outcomes = ("x", "y") if index % 3 else (("x",) if index % 2 else ("y",))
            rows_by_block.append([
                {"outcome": outcome,
                 "estimate": float(rng.normal(0.1, 0.2))}
                for outcome in outcomes
            ])
            size = len(outcomes)
            base = rng.random((size, size)) * 0.02 + np.eye(size) * 0.01
            matrices.append(base @ base.T + np.eye(size) * 0.005)
        fixed = {"analysis_scale": "identity",
                 "estimates": [{"outcome": "x"}, {"outcome": "y"}],
                 "q": {"statistic": 1.0, "df": 1, "p_value": 0.5}}
        original = dependent_random._DENSE_IDENTIFIABILITY_MAX_EFFECTS
        try:
            dependent_random._DENSE_IDENTIFIABILITY_MAX_EFFECTS = 0
            blockwise = dependent_random.synthesize_random_reml_un(
                fixed, rows_by_block, matrices, ["x", "y"], "MD")
            dependent_random._DENSE_IDENTIFIABILITY_MAX_EFFECTS = 10 ** 9
            dense = dependent_random.synthesize_random_reml_un(
                fixed, rows_by_block, matrices, ["x", "y"], "MD")
        finally:
            dependent_random._DENSE_IDENTIFIABILITY_MAX_EFFECTS = original
        block_condition = float(blockwise["fit"]["covariance_information_condition"])
        dense_condition = float(dense["fit"]["covariance_information_condition"])
        self.assertLessEqual(abs(block_condition - dense_condition) / dense_condition, 1e-9,
                             msg=f"强制分块 {block_condition!r} vs 稠密 {dense_condition!r}")
        # 估计不因可辨识性检查路径而变（逐位）
        self.assertEqual([row["se"] for row in blockwise["estimates"]],
                         [row["se"] for row in dense["estimates"]])
        self.assertEqual(math.isfinite(block_condition), True)
