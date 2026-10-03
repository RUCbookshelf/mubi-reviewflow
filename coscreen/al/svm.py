"""线性 SVM 分类器（封装 ``sklearn.svm.LinearSVC``）。

参数与接口约定来自 AL 模型升级规格书（2026-09-30）§2：

- ``C=0.11``、``loss="squared_hinge"``——ASReview v3 Optuna 在 SYNERGY
  全集的调优结果（ELAS u4 配置；验证批宏 WSS@95=+0.580）；
- 接口与手写 :class:`~coscreen.al.nb.MultinomialNB` 流水线兼容：
  :meth:`SVMClassifier.fit` 接受 ``sample_weight=None``（供类别平衡器
  ``BalancedWeights`` 逐样本加权），:meth:`SVMClassifier.predict_proba`
  输出 ``P(include|x)`` 风格的 [0,1] 分数。``X`` 既接受 sklearn 原生
  输入（``numpy.ndarray`` / ``scipy.sparse`` 矩阵，原样透传，结果与
  sklearn 直调逐位一致），也接受本项目
  :class:`~coscreen.al.features.TfidfVectorizer` 输出的稀疏行
  ``list[dict[str, float]]``——fit 时按词项字典序确定稠密列序（确定
  可复现），predict 时 fit 未见的词项忽略（与 NB 语义一致）；
- ``LinearSVC`` 没有 ``predict_proba``——用 ``decision_function`` 分数
  ``s`` 经 sigmoid ``1/(1+exp(-s))`` 归一到 [0,1]（:func:`_sigmoid`
  为数值稳定实现，与 ``scipy.special.expit`` 逐位一致）。

延迟导入不变量（规格书 §10 不变量 4）：``import coscreen.al.svm``
只加载标准库——sklearn 在 :class:`SVMClassifier` 构造时、numpy 在
首次数值计算时才导入；本模块刻意不加入 ``coscreen/al/__init__.py``
（仅 SVM 预设选用时才被导入，不用时不加载 sklearn）。导入零副作用：
不连数据库、不起服务、不打开网络套接字。
"""

from __future__ import annotations

__all__ = ["SVMClassifier"]


def _sigmoid(scores) -> "numpy.ndarray":  # noqa: F821 - 延迟导入,仅注解
    """数值稳定的 sigmoid ``1/(1+exp(-s))``（与 ``scipy.special.expit`` 逐位一致）。

    直接计算 ``np.exp(-s)`` 在 ``s < -709`` 时上溢出 ``inf``（结果仍正确
    但触发 RuntimeWarning）；按符号分段计算避免该警告。
    """
    import numpy as np

    s = np.asarray(scores, dtype=np.float64)
    out = np.empty_like(s)
    pos = s >= 0
    out[pos] = 1.0 / (1.0 + np.exp(-s[pos]))
    exp_s = np.exp(s[~pos])
    out[~pos] = exp_s / (1.0 + exp_s)
    return out


def _is_dict_rows(X) -> bool:
    """``X`` 是否为本项目 TfidfVectorizer 输出的稀疏行 ``list[dict]``。

    空列表歧义（既无 dict 也非 sklearn 矩阵）——按非 dict 输入透传，
    由 sklearn 自行校验/报错。
    """
    return (
        isinstance(X, (list, tuple))
        and len(X) > 0
        and all(isinstance(row, dict) for row in X)
    )


def _rows_to_dense(rows: list[dict[str, float]], columns: list[str]):
    """稀疏 dict 行 → 稠密矩阵（列序 = ``columns``；缺失词项取 0）。

    与 ``tests/test_al_nb.py`` 的基准协议一致（稠密列 = 排序后的词项表）。
    """
    import numpy as np

    return np.array(
        [[row.get(term, 0.0) for term in columns] for row in rows],
        dtype=np.float64,
    )


class SVMClassifier:
    """LinearSVC 分类器（``C=0.11``、``loss="squared_hinge"``）。

    参数
    ----
    C:
        正则化强度的倒数（默认 ``0.11``，ASReview Optuna 调优值）。
    random_state:
        透传 ``LinearSVC`` 的同名校验/种子参数，默认 ``None``（sklearn
        默认，不改变行为）。liblinear 对偶坐标下降含随机坐标顺序，
        需要逐位可复现的调用方（如 AL 排序器）应显式传种子——镜像
        ``research/sim/replay.py`` 的可复现性决策。

    未 fit 先调用 ``predict``/``predict_proba``：数组输入由 sklearn 抛
    ``NotFittedError``；dict 输入由本类抛 ``RuntimeError``（与
    :class:`~coscreen.al.nb.MultinomialNB` 的报错风格一致）。fit 与
    predict 的输入形态（dict 行 vs 数组）不得混用。
    """

    def __init__(self, C: float = 0.11, random_state: int | None = None) -> None:
        # 延迟导入：构造分类器才加载 sklearn（§10 不变量 4）。
        from sklearn.svm import LinearSVC

        self.C = float(C)
        self.random_state = random_state
        self._model = LinearSVC(C=self.C, loss="squared_hinge",
                                random_state=random_state)
        # dict 输入模式：fit 时固定列序，predict 沿用（数组输入恒为 None）。
        self._columns: list[str] | None = None
        self._dict_mode: bool | None = None

    # ------------------------------------------------------------------
    # NB 兼容接口
    # ------------------------------------------------------------------

    def fit(self, X, y, sample_weight=None):
        """拟合 LinearSVC；``sample_weight`` 逐样本透传（平衡器入口）。

        ``X`` 为 dict 行时按词项字典序建立稠密列（确定可复现），为
        ``numpy.ndarray``/``scipy.sparse`` 时原样透传（与 sklearn 直调
        等价）。返回 ``self``（支持链式调用）。
        """
        if _is_dict_rows(X):
            terms: set[str] = set()
            for row in X:
                terms.update(row)
            self._columns = sorted(terms)
            self._dict_mode = True
            X = _rows_to_dense(list(X), self._columns)
        else:
            self._columns = None
            self._dict_mode = False
        self._model.fit(X, y, sample_weight=sample_weight)
        return self

    def predict(self, X) -> list[int]:
        """返回每条样本的预测标签（0/1，与 ``LinearSVC.predict`` 一致）。"""
        return [int(label) for label in self._model.predict(self._prepare(X))]

    def predict_proba(self, X) -> list[float]:
        """decision_function 经 sigmoid 归一到 [0,1]（无原生概率）。

        分数仅作排序/展示用——单调变换不改变 ``max`` 策略的排序，但
        不具备校准概率语义。
        """
        scores = self._model.decision_function(self._prepare(X))
        return _sigmoid(scores).tolist()

    # ------------------------------------------------------------------
    # 内部：dict 行 → 稠密矩阵（列序沿用 fit 时固定的词表）
    # ------------------------------------------------------------------

    def _prepare(self, X):
        """predict 侧输入整形：dict 行按 fit 词表稠密化，数组透传。"""
        if not _is_dict_rows(X):
            if self._dict_mode:
                raise ValueError(
                    "fit 接受的是稀疏 dict 行输入，predict 必须使用同一形态"
                    "（list[dict[str, float]]）"
                )
            return X
        if self._dict_mode is None:
            raise RuntimeError("尚未调用 fit，无法预测")
        if self._dict_mode is False:
            raise ValueError(
                "fit 接受的是数组输入，predict 必须使用同一形态"
                "（numpy.ndarray / scipy.sparse）"
            )
        # fit 未见的词项被忽略（row.get 缺省 0）——与 NB 语义一致。
        return _rows_to_dense(list(X), self._columns)
