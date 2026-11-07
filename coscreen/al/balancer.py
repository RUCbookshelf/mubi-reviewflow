"""类别平衡策略（解决文献筛选的极不平衡问题）。

典型场景： 5000 篇文献中仅 50 篇相关（1% 患病率）。
不做平衡 → NB 几乎全判为"不相关" → 排序无信号。

ASReview ELAS u4 默认 ratio=9.8。

权重设计（ASReview Balanced 策略同构；参数出处见
``docs/methodology/results/al_upgrade_verification/REPORT.md`` §2 与
``docs/methodology/sim/README.md`` §1.2）：

- 类权重 ``relevant = 1.0``、``irrelevant = n_relevant / (ratio × n_irrelevant)``；
- 归一化使 ``sum(weights)`` 等于样本数 ``n_samples``（训练集的"有效样本量"
  不变，与 sample_weight=None 的量纲可比）；**浮点舍入下最后一个 ulp
  上的偏差是正常的**（如 ``y=[1,0]``、ratio=9.8 实测
  ``2.0000000000000004``），调用方不得按精确相等断言（b07·F1-124）；
- 由此两类的**总**权重之比恒为 ``ratio : 1``（纳入类 : 排除类），
  即 ratio=9.8 时排除类总权重约为纳入类的 1/9.8——这是 ratio 的语义；
  单个样本的权重比则为 ``ratio × n_irrelevant / n_relevant``；
- ratio=1.0 时两类总权重相等（每样本权重 ``n / (2·n_c)``，与 sklearn
  ``compute_class_weight("balanced")`` 一致）；标签本身已均衡时权重
  恰为全 1，等价于不做平衡。

纯 numpy 计算，import 零副作用（不连库、不起服务、不加载 sklearn）。
"""

from __future__ import annotations

import math

import numpy as np

__all__ = ["BalancedWeights"]


class BalancedWeights:
    """ASReview Balanced 策略：给少数类（相关）更高权重。

    ratio = N_relevant_weight / N_irrelevant_weight
    （纳入类 : 排除类的**总**权重之比，与模块 docstring/实现一致；
    F-04-04 修正：此前公式方向写反。）
    ratio=9.8 意味着排除类**总**权重约为纳入类的 1/9.8；逐样本的
    排除/纳入权重比则为 n_relevant/(ratio × n_irrelevant)，随两类样本量
    变化，仅两类等量时才是 1/9.8（MM11-05 修正：50/4950 时为 1/970.2；
    纳入/排除方向是其倒数 ratio × n_irrelevant / n_relevant，见模块说明）。
    """

    def __init__(self, ratio: float = 9.8) -> None:
        """``ratio`` 为纳入类与排除类的总权重之比（须为有限正数）。

        ASReview ELAS u4 在 SYNERGY 全集 Optuna 调优得 9.8；
        ``ratio=1.0`` 表示两类等总权重（标签均衡时等价于无平衡）。

        ``inf`` / ``nan`` 在构造时以 ``ValueError`` 拒绝（b07·F1-121）：
        ``inf`` 会让排除类总权重恒为 0（NB 的排除类先验变成 ``-inf``，
        模型静默退化为「全判相关」），``nan`` 更早就在 ``ratio > 0`` 处
        被拒；只有有限正数才有可解释的语义。极小正数（如 ``1e-320``）
        不在构造期拒绝——它仍是合法比例，数值溢出在 :meth:`compute`
        内以尺度等价的公式兜住（见该方法说明）。
        """
        if not ratio > 0:
            raise ValueError(f"ratio 必须为正数，得到 {ratio!r}")
        if not math.isfinite(ratio):
            raise ValueError(f"ratio 必须为有限正数，得到 {ratio!r}")
        self.ratio = float(ratio)

    def compute(self, y) -> np.ndarray:
        """计算每个样本的权重（长度与 ``y`` 相同，总和等于样本数）。

        参数
        ----
        y:
            一维二分类标签数组（``y ∈ {0, 1}``，1 = include/相关），
            ndarray 或可转 ndarray 的序列。二维/零维输入 →
            ``ValueError``（b07·F1-120：二维输入此前静默返回二维权重，
            两分支形状口径互斥，把失败推迟到下游 ``fit`` 的形状报错）。

        返回
        ----
        np.ndarray
            float64 权重向量：

            - 两类并存：relevant 样本权重 1.0、irrelevant 样本权重
              ``n_relevant / (ratio × n_irrelevant)``，再整体缩放使
              ``sum ≈ len(y)``（两类总权重之比恒等于 ``ratio``）；
              **浮点舍入下总和可在最后一个 ulp 上与 ``len(y)`` 不等**
              （b07·F1-124：本模块不承诺按位精确相等，如 ``y=[1,0]``、
              ratio=9.8 实得 ``2.0000000000000004``）；
            - 单类退化（全 0、全 1 或空数组）：均匀权重 ``np.ones``，
              即不做任何平衡；
            - 标签非 0/1 → ``ValueError``。

        数值兜底（b07·F1-121）：``ratio`` 极小（如 ``1e-320``）时
        ``n_relevant / (ratio × n_irrelevant)`` 会溢出为 ``inf``，旧实现
        随后在 ``inf × 0`` 上产出整列 NaN。此时改用尺度等价、不会溢出的
        形式（两类权重分别为 ``n·ratio / (n_rel·(1+ratio))`` 与
        ``n / (n_ir·(1+ratio))``），结果仍是同一比例的有限权重；正常
        ``ratio`` 一律走原路径，逐位不变。
        """
        y = np.asarray(y)
        if y.ndim != 1:
            raise ValueError(f"标签必须是一维数组，得到 {y.ndim} 维（shape {y.shape}）")
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
        total = weights.sum()
        if not math.isfinite(float(total)):
            # 仅在 ratio 极小、weight_0 上溢时可达：用同一比例的
            # 尺度等价形式重建（两类总权重比仍为 ratio，总和仍为 n）。
            # 乘法形式避免 1/ratio 自身溢出，1e-320 的权重仍可表示为
            # 次正规数而非被静默截成 0。
            denom = 1.0 + self.ratio
            weight_1 = (n * self.ratio) / (n_relevant * denom)
            weight_0 = n / (n_irrelevant * denom)
            weights = np.where(y == 1, weight_1, weight_0)
            total = weights.sum()
        return weights * (n / total)
