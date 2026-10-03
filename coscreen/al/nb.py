"""多项式朴素贝叶斯（Multinomial Naive Bayes，纯 Python 实现）。

与 ``sklearn.naive_bayes.MultinomialNB`` 数值等价（同输入下
``predict_scores`` 与 ``predict_proba(...)[:, 1]`` 误差 < 1e-9，
见 ``tests/test_al_nb.py`` 基准测试）：

- 特征对数概率 ``feature_log_prob_[c][t] =
  log((count[c][t] + alpha) / (total_count[c] + alpha * V))``，
  其中 ``count[c][t]`` 为类 c 内特征 t 的取值总和（TF-IDF 加权值亦可），
  ``total_count[c]`` 为类 c 全部特征值之和，``V`` 为词表大小
  （fit 时 X 中出现过的不同词项数）。
- 类先验 ``class_log_prior_[c] = log(n_c / n)``；某类样本数为 0 时其先验
  为 ``-inf``——该类在对数空间归一化后后验恒为 0，predict 永不选中
  （例如训练集只含 0 类时得分恒 ``0.0``，只含 1 类时恒 ``1.0``）。
- :meth:`MultinomialNB.predict_scores` 返回后验 ``P(y=1|x)``：先在对数
  空间算联合对数似然，再用 logsumexp 归一化后取指数；文档不含任何
  已知词项时退化为纯先验得分 ``n_1 / n``；fit 时未见的词项一律忽略。
- :meth:`MultinomialNB.fit` 接受可选 ``sample_weight``（2026-09-30 AL
  模型升级，配合 :class:`~coscreen.al.balancer.BalancedWeights`）：权重
  同时作用于**类先验**（``class_count[c] = Σ w_i``）与**类内特征和**
  （``feat_sum[c][t] = Σ w_i·x_it``），与 sklearn
  ``MultinomialNB.fit(sample_weight=...)``（即 ASReview v3 Balanced 的
  NB 语义）逐项对应——参考基准 ``research/sim/replay.py`` 的
  ``_nb_fit_score``。``sample_weight=None``（默认）时数学与代码路径
  均与升级前逐位一致（权重全取 1.0，纯 stdlib）。

运行时零第三方依赖（仅标准库 ``math``）；sklearn 仅在测试中作基准。
"""

from __future__ import annotations

import math

__all__ = ["MultinomialNB"]


def _logsumexp2(a: float, b: float) -> float:
    """两个对数值的 logsumexp（允许 ``-inf``；两者同为 ``-inf`` 时返回 ``-inf``）。"""
    hi = a if a >= b else b
    if hi == -math.inf:
        return hi
    return hi + math.log(math.exp(a - hi) + math.exp(b - hi))


class MultinomialNB:
    """二分类多项式朴素贝叶斯（``y ∈ {0, 1}``，1 = include）。

    属性（fit 后可用，命名与 sklearn 对齐）：
        class_log_prior_: ``{0: float, 1: float}``，类先验对数；
        feature_log_prob_: ``{c: {term: 对数概率}}``；
        n_features_: 词表大小（fit 时 X 中不同词项数）。
    """

    def __init__(self, alpha: float = 1.0) -> None:
        """``alpha`` 为 Laplace/Lidstone 平滑强度（须为正，与 sklearn 一致）。"""
        if not alpha > 0:
            raise ValueError(f"alpha 必须为正数，得到 {alpha!r}")
        self.alpha = float(alpha)
        self.class_log_prior_: dict[int, float] = {}
        self.feature_log_prob_: dict[int, dict[str, float]] = {}
        self.n_features_: int = 0

    def fit(self, X: list[dict[str, float]], y: list[int],
            sample_weight=None) -> None:
        """在稀疏向量 ``X`` 与二分类标签 ``y`` 上拟合（Laplace 平滑）。

        空训练集 → ``ValueError``；标签非 0/1 或长度不一致 → ``ValueError``；
        特征取值为负 → ``ValueError``（多项式分布要求非负，TF-IDF 天然满足）。

        ``sample_weight``（可选，长度与 ``y`` 一致的可迭代数值）逐样本加权，
        语义与 sklearn ``MultinomialNB.fit(sample_weight=...)`` 一致：权重
        作用于类先验（各类的权重和）与类内特征和（``w_i · x_it`` 累加）；
        权重须非负。``None``（默认）= 全 1.0，与不加权逐位一致。
        """
        if len(X) != len(y):
            raise ValueError(f"X 与 y 长度不一致：{len(X)} != {len(y)}")
        if not X:
            raise ValueError("训练集为空：MultinomialNB.fit 至少需要一条带标签样本")
        if sample_weight is None:
            weights = [1.0] * len(y)
        else:
            weights = [float(w) for w in sample_weight]
            if len(weights) != len(y):
                raise ValueError(
                    f"sample_weight 与 y 长度不一致：{len(weights)} != {len(y)}"
                )
            if any(w < 0.0 for w in weights):
                raise ValueError("sample_weight 必须非负")
        class_count: dict[int, float] = {0: 0.0, 1: 0.0}
        feat_sum: dict[int, dict[str, float]] = {0: {}, 1: {}}
        terms: set[str] = set()
        for row, label, w in zip(X, y, weights):
            if label not in (0, 1):
                raise ValueError(f"标签必须是 0 或 1，得到 {label!r}")
            class_count[label] += w
            acc = feat_sum[label]
            for term, value in row.items():
                if value < 0:
                    raise ValueError(
                        f"特征取值必须非负（多项式分布要求）：{term!r}={value!r}"
                    )
                terms.add(term)
                acc[term] = acc.get(term, 0.0) + w * value
        vocab_size = len(terms)
        self.n_features_ = vocab_size
        total_weight = class_count[0] + class_count[1]
        if total_weight <= 0.0:
            raise ValueError("sample_weight 全为零，无法拟合")
        self.class_log_prior_ = {
            c: math.log(class_count[c] / total_weight) if class_count[c] else -math.inf
            for c in (0, 1)
        }
        self.feature_log_prob_ = {}
        for c in (0, 1):
            denom = sum(feat_sum[c].values()) + self.alpha * vocab_size
            self.feature_log_prob_[c] = {
                term: math.log(feat_sum[c].get(term, 0.0) + self.alpha)
                - math.log(denom)
                for term in sorted(terms)
            }

    def predict_scores(self, X: list[dict[str, float]]) -> list[float]:
        """返回每条文档的后验 ``P(y=1|x)``（未 fit 先调用 → ``RuntimeError``）。

        fit 时未见的词项忽略；文档无任何已知词项时得分为纯先验 ``n_1/n``。
        """
        if not self.feature_log_prob_:
            raise RuntimeError("尚未调用 fit，无法预测")
        prior0 = self.class_log_prior_[0]
        prior1 = self.class_log_prior_[1]
        flp0 = self.feature_log_prob_[0]
        flp1 = self.feature_log_prob_[1]
        scores: list[float] = []
        for row in X:
            jll0 = prior0
            jll1 = prior1
            for term, value in row.items():
                lp0 = flp0.get(term)
                if lp0 is not None:  # 两类词表恒一致：同在/同不在
                    jll0 += value * lp0
                    jll1 += value * flp1[term]
            scores.append(math.exp(jll1 - _logsumexp2(jll0, jll1)))
        return scores
