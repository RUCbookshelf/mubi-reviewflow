"""类别平衡策略（解决文献筛选的极不平衡问题）。

典型场景： 5000 篇文献中仅 50 篇相关（1% 患病率）。
不做平衡 → NB 几乎全判为"不相关" → 排序无信号。

ASReview ELAS u4 默认 ratio=9.8。

权重设计（规格书 2026-09-30-al-model-upgrade-spec.md §3.1，与
ASReview Balanced 策略同构）：

- 类权重 ``relevant = 1.0``、``irrelevant = n_relevant / (ratio × n_irrelevant)``；
- 归一化使 ``sum(weights) == n_samples``（训练集的"有效样本量"不变，
  与 sample_weight=None 的量纲可比）；
- 由此两类的**总**权重之比恒为 ``ratio : 1``（纳入类 : 排除类），
  即 ratio=9.8 时排除类总权重约为纳入类的 1/9.8——这是 ratio 的语义；
  单个样本的权重比则为 ``ratio × n_irrelevant / n_relevant``；
- ratio=1.0 时两类总权重相等（每样本权重 ``n / (2·n_c)``，与 sklearn
  ``compute_class_weight("balanced")`` 一致）；标签本身已均衡时权重
  恰为全 1，等价于不做平衡。

纯 numpy 计算，import 零副作用（不连库、不起服务、不加载 sklearn）。
"""

from __future__ import annotations

import numpy as np

__all__ = ["BalancedWeights"]


class BalancedWeights:
    """ASReview Balanced 策略：给少数类（相关）更高权重。

    ratio = N_irrelevant_weight / N_relevant_weight
    ratio=9.8 意味着排除类样本的权重约为纳入类的 1/9.8。
    """

    def __init__(self, ratio: float = 9.8) -> None:
        """``ratio`` 为排除类与纳入类的总权重之比（须为正）。

        ASReview ELAS u4 在 SYNERGY 全集 Optuna 调优得 9.8；
        ``ratio=1.0`` 表示两类等总权重（标签均衡时等价于无平衡）。
        """
        if not ratio > 0:
            raise ValueError(f"ratio 必须为正数，得到 {ratio!r}")
        self.ratio = float(ratio)

    def compute(self, y) -> np.ndarray:
        """计算每个样本的权重（长度与 ``y`` 相同，总和恰为样本数）。

        参数
        ----
        y:
            二分类标签数组（``y ∈ {0, 1}``，1 = include/相关），
            ndarray 或可转 ndarray 的序列。

        返回
        ----
        np.ndarray
            float64 权重向量：

            - 两类并存：relevant 样本权重 1.0、irrelevant 样本权重
              ``n_relevant / (ratio × n_irrelevant)``，再整体缩放使
              ``sum == len(y)``（两类总权重之比恒等于 ``ratio``）；
            - 单类退化（全 0、全 1 或空数组）：均匀权重 ``np.ones``，
              即不做任何平衡；
            - 标签非 0/1 → ``ValueError``。
        """
        y = np.asarray(y)
        n = len(y)
        if n and not np.isin(y, (0, 1)).all():
            raise ValueError("标签必须是 0 或 1")
        n_relevant = int(np.count_nonzero(y == 1))
        n_irrelevant = int(np.count_nonzero(y == 0))
        if n_relevant == 0 or n_irrelevant == 0:
            return np.ones(n, dtype=float)  # 单一类时不加权

        # 类权重: relevant=1.0, irrelevant = n_rel / (ratio * n_ir)
        weight_1 = 1.0
        weight_0 = n_relevant / (self.ratio * n_irrelevant)

        weights = np.where(y == 1, weight_1, weight_0)
        # 归一化使总和等于样本数
        return weights * (n / weights.sum())
