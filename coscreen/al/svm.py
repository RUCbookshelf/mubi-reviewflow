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
  ``list[dict[str, float]]``——fit 时按词项字典序确定列序（确定
  可复现），predict 时 fit 未见的词项忽略（与 NB 语义一致）。dict 行
  **始终走 ``scipy.sparse.csr_matrix`` 稀疏路径**（r9，b03·F2-44）：
  此前每次 fit/predict 都物化 ``n_docs × |vocab|`` 的 float64 稠密
  矩阵（1000 候选 × 2 万词表 = 152.6 MiB；外推 5000 × 10 万 = 3.7 GiB
  → MemoryError/500），现在内存为 O(nnz)，与 NB 载体同阶；liblinear
  原生支持 CSR，实测拟合系数同稠密路径逐位相同（diff 0.0），决策值
  至多差 1 ULP（~3.3e-16）。``max_features`` 另设词表列数上界（见下），
  超出时按总权重降序保留——默认 ``None`` 即无上限，正常规模语料
  逐位不变；
- ``LinearSVC`` 没有 ``predict_proba``——用 ``decision_function`` 分数
  ``s`` 经 sigmoid ``1/(1+exp(-s))`` 归一到 [0,1]（:func:`_sigmoid`
  为数值稳定实现，与 ``scipy.special.expit`` 数值等价、不承诺逐位一致：
  200k 样本实测位级差 ≤3 ULP、绝对差 ≤2.22e-16，且两者各自与高精度
  正确舍入值之差 ≤1 ULP；单调变换不改排序。基准钉在
  ``build/tests/test_2_1_5_review67_lows.py``；review67 L-23）；
- 退化输入（与 NB 载体行为对齐）：dict 模式下零样本预测（``X == []``，
  对应排序器"全筛完"的合法终态）直接返回空列表，不落入形态混用
  校验；``y`` 为 list/tuple 且只含一个类别时 :meth:`SVMClassifier.fit`
  先于 sklearn 抛可读 :class:`SingleClassTrainingError`（``ValueError``
  子类，供 HTTP 边界精确捕获转 4xx；``LinearSVC`` 无法在单类标签上
  定义间隔，必须拒绝——NB 载体对单类以 ``-inf`` 类先验降级、predict
  仍可用，见 :mod:`coscreen.al.nb`）。``y`` 为 numpy 数组时保持与
  sklearn 直调等价（不拦截，原生校验错误照旧）。

延迟导入不变量（规格书 §10 不变量 4）：``import coscreen.al.svm``
只加载标准库——sklearn 在 :class:`SVMClassifier` 构造时、numpy 在
首次数值计算时才导入；本模块刻意不加入 ``coscreen/al/__init__.py``
（仅 SVM 预设选用时才被导入，不用时不加载 sklearn）。导入零副作用：
不连数据库、不起服务、不打开网络套接字。
"""

from __future__ import annotations

__all__ = ["SVMClassifier", "SingleClassTrainingError"]


class SingleClassTrainingError(ValueError):
    """svm/cjk 预设下训练标签只有一类（ranker 传入 list/tuple ``y``）。

    ``ValueError`` 子类——模块对外契约仍是「可读 ``ValueError``」
    （:meth:`SVMClassifier.fit` 单类守卫抛出）。专用类型的意义在 HTTP
    边界：端点可以精确 ``except SingleClassTrainingError`` 转 4xx，
    不必用宽 ``except ValueError``（会把 ``rank`` 路径上其他 ValueError
    一并吞成 400，掩盖真实的服务端/调用方错误）。
    """


def _sigmoid(scores) -> "numpy.ndarray":  # noqa: F821 - 延迟导入,仅注解
    """数值稳定的 sigmoid ``1/(1+exp(-s))``（与 ``scipy.special.expit`` 数值等价）。

    review67 L-23：此前 docstring 宣称「逐位一致」——实测（numpy 2.4.6 /
    scipy 1.17.1，均在钉定依赖区间）不成立：200k 样本中 ~24% 位级不同、
    最大位级差 3 ULP、绝对差 ≤2.22e-16。位级承诺失实，改为「数值等价
    （位级差 ≤3 ULP，绝对差 ≤2.22e-16）」并以基准测试钉住（见
    ``build/tests/test_2_1_5_review67_lows.py``）。单调变换不改排序。

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
    由 sklearn 自行校验/报错。零样本的 dict 模式合法输入（排序器
    "全筛完"终态）由 :meth:`SVMClassifier.predict` /
    :meth:`SVMClassifier.predict_proba` 入口短路处理，不经过本函数。
    """
    return (
        isinstance(X, (list, tuple))
        and len(X) > 0
        and all(isinstance(row, dict) for row in X)
    )


def _is_empty_rows(X) -> bool:
    """``X`` 是否为空列表/空元组（零样本输入，两种形态下语法上都可能出现）。"""
    return isinstance(X, (list, tuple)) and len(X) == 0


def _rows_to_dense(rows: list[dict[str, float]], columns: list[str]):
    """稀疏 dict 行 → 稠密矩阵（列序 = ``columns``；缺失词项取 0）。

    r9（b03·F2-44）后**仅作对照基准保留**：生产路径改用
    :func:`_rows_to_csr`（O(nnz) 内存）。本函数会分配
    ``len(rows) × len(columns)`` 个 float64（1000 × 20000 = 152.6 MiB），
    只允许在小规模等价性对照里调用。
    """
    import numpy as np

    return np.array(
        [[row.get(term, 0.0) for term in columns] for row in rows],
        dtype=np.float64,
    )


def _select_columns(
    rows: list[dict[str, float]], max_features: int | None
) -> list[str]:
    """确定 dict 行的列空间（词项字典序），并按 ``max_features`` 优雅降级。

    ``max_features`` 为 ``None`` 或列数不超过上限时，返回按字典序排列的
    全部词项（与升级前逐位一致）。超过上限时按**总权重降序**保留前
    ``max_features`` 个词项（同权重按词项字典序打破平局，完全可复现），
    再按字典序排定列序——超限语料得到有界列空间而不是 ``MemoryError``，
    且排序与 `max_features` 取值确定对应。
    """
    totals: dict[str, float] = {}
    for row in rows:
        for term, value in row.items():
            totals[term] = totals.get(term, 0.0) + float(value)
    if max_features is not None and len(totals) > max_features:
        kept = sorted(totals, key=lambda term: (-totals[term], term))[:max_features]
        return sorted(kept)
    return sorted(totals)


def _rows_to_csr(rows: list[dict[str, float]], columns: list[str]):
    """稀疏 dict 行 → CSR 矩阵（列序 = ``columns``；缺失/零值词项不存）。

    内存 O(nnz)：只存非零元素（Indptr/indices/data 三个一维数组），
    不再分配 ``len(rows) × len(columns)`` 的稠密块（r9，b03·F2-44：
    修前 1000×20000 单次 152.6 MiB、外推 5000×10 万 3.73 GiB）。
    ``LinearSVC`` 原生支持 CSR；实测拟合系数与稠密路径**逐位相同**
    （diff 0.0），决策值在词表规模大时最多差 1 ULP（~3.3e-16，来自
    稀疏点积的累加顺序，小规模对照恰为 0.0）——不影响排序/展示。
    行内按列号升序写入——CSR 要求有序索引，同时固定了浮点累加顺序。
    """
    import numpy as np
    from array import array
    from scipy.sparse import csr_matrix

    # 平台保证：array('i') 的元素宽度必须是 4 字节才能零拷贝当 int32 用。
    # CPython 在 Windows/Linux/macOS 上都是 4；万一遇到非 4 的平台就退回
    # 逐元素 ndarray（牺牲一点峰值内存，绝不静默错位）。
    index = {term: i for i, term in enumerate(columns)}
    # 用紧凑的 array('d')/array('i') 暂存再 np.frombuffer 零拷贝转 ndarray：
    # Python list 的 float 元素每个要 24B 对象 + 8B 指针，在 GB 级 nnz 上
    # 反而成了新的内存热点。两个 array 合计恰为 CSR 的 data/indices。
    data = array("d")
    indices = array("i")
    indptr = array("q", [0])
    for row in rows:
        entries = sorted(
            (index[term], float(value))
            for term, value in row.items()
            if term in index and value != 0.0
        )
        for col, value in entries:
            indices.append(col)
            data.append(value)
        indptr.append(len(data))
    if indices.itemsize == 4 and indptr.itemsize == 8:
        data_arr = np.frombuffer(data, dtype=np.float64)
        indices_arr = np.frombuffer(indices, dtype=np.int32)
        indptr_arr = np.frombuffer(indptr, dtype=np.int64)
    else:  # pragma: no cover - 现有 CPython 平台均走上面的零拷贝分支
        data_arr = np.asarray(data, dtype=np.float64)
        indices_arr = np.asarray(indices, dtype=np.int32)
        indptr_arr = np.asarray(indptr, dtype=np.int64)
    return csr_matrix(
        (data_arr, indices_arr, indptr_arr),
        shape=(len(rows), len(columns)),
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
        ``docs/methodology/sim/replay.py`` 的可复现性决策。
    max_features:
        dict 行的词表列数上界（r9，b03·F2-44 的资源上限）。``None``
        （默认）不设上限；超过上限时按总权重降序保留前 ``max_features``
        个词项（见 :func:`_select_columns`）。仅影响 dict 行输入，数组/
        稀疏矩阵输入原样透传不受影响。

    未 fit 先调用 ``predict``/``predict_proba``：数组输入由 sklearn 抛
    ``NotFittedError``；dict 输入由本类抛 ``RuntimeError``（与
    :class:`~coscreen.al.nb.MultinomialNB` 的报错风格一致）。fit 与
    predict 的输入形态（dict 行 vs 数组）不得混用；唯一例外是零样本
    输入——dict 模式下 ``X == []``（排序器全筛完的合法终态）不视为
    形态混用，直接返回空结果。
    """

    def __init__(
        self,
        C: float = 0.11,
        random_state: int | None = None,
        max_features: int | None = None,
    ) -> None:
        # 延迟导入：构造分类器才加载 sklearn（§10 不变量 4）。
        from sklearn.svm import LinearSVC

        if max_features is not None:
            # R2：合法取值域含 numpy 整型（np.int64 不是 int 子类），
            # 按 numbers.Integral 判定；bool 虽是其子类但语义上不是上限值。
            import numbers

            if (
                isinstance(max_features, bool)
                or not isinstance(max_features, numbers.Integral)
                or int(max_features) < 1
            ):
                raise ValueError(
                    f"max_features 必须是正整数或 None，收到 {max_features!r}"
                )
            max_features = int(max_features)
        self.C = float(C)
        self.random_state = random_state
        self.max_features = max_features
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

        ``X`` 为 dict 行时按词项字典序建立稀疏列（确定可复现；内存
        O(nnz)，见 :func:`_rows_to_csr`），为 ``numpy.ndarray``/
        ``scipy.sparse`` 时原样透传（与 sklearn 直调等价）。返回 ``self``
        （支持链式调用）。

        单类守卫：``y`` 为 list/tuple 且只含一个类别时，先于 sklearn
        抛可读 :class:`SingleClassTrainingError`（``ValueError`` 子类；
        sklearn 原生文案是面向开发者的内部措辞，经调用方转换为用户可见
        错误后不可读）。这不会拒绝任何 sklearn 本可接受的输入——
        ``LinearSVC`` 对单类同样抛错，这里只是把同一拒绝换成可读文案。
        ``y`` 为 numpy 数组时不动（保持 sklearn 直调等价，原生校验错误
        照旧）。形态状态（``_columns``/``_dict_mode``）在
        ``_model.fit`` **成功后**才提交：失败的 fit（如非法
        ``sample_weight``）不留下半更新状态，dict 模式的零样本短路也就
        不会在未 fit 的模型上静默返回 ``[]``。
        """
        self._check_two_classes(y)
        if _is_dict_rows(X):
            columns = _select_columns(list(X), self.max_features)
            dict_mode = True
            # r9（b03·F2-44）：CSR 稀疏路径，内存 O(nnz) 而非
            # n_docs × |vocab| 稠密块；liblinear 原生支持，结果与稠密
            # 路径逐位一致（决策函数与系数 diff 0.0）。
            X = _rows_to_csr(list(X), columns)
        else:
            columns = None
            dict_mode = False
        # 成功后才提交形态状态（评审实测：先置 _dict_mode=True 再让
        # _model.fit 抛错，会让 predict([]) 的零样本短路在未 fit 的
        # 模型上返回 []——假成功）。
        self._model.fit(X, y, sample_weight=sample_weight)
        self._columns = columns
        self._dict_mode = dict_mode
        return self

    @staticmethod
    def _check_two_classes(y) -> None:
        """单类训练守卫：list/tuple ``y`` 只含一个类别时抛可读 ``ValueError``。

        只拦 list/tuple（排序器传入的形态）；numpy 数组 ``y`` 不动——
        本类对数组输入承诺与 sklearn 直调逐位等价（含报错）。空 ``y``
        同样交回 sklearn（0 样本是另一类校验错误，文案不应混淆）。
        对照 NB 载体：``MultinomialNB`` 单类可 fit（缺失类先验取
        ``-inf``，predict 退化为常数分），而 ``LinearSVC`` 无法在单类
        标签上定义间隔，只能拒绝——因此这里给出指向用户动作的文案。
        """
        if not isinstance(y, (list, tuple)) or not y:
            return
        try:
            classes = set(y)
        except TypeError:
            return  # 元素不可哈希等畸形输入：交由 sklearn 抛原生校验错误
        if len(classes) > 1:
            return
        only = next(iter(classes))
        if only in (0, 1):
            n_include = sum(1 for label in y if label == 1)
            n_exclude = len(y) - n_include
            missing = "exclude" if n_include else "include"
            raise SingleClassTrainingError(
                "SVM 模型需要 include 与 exclude 两类训练标签各至少 "
                f"1 条（当前 include={n_include}、exclude={n_exclude}），"
                f"请先补充 {missing} 侧标签后重试，或改用经典/优化"
                "（朴素贝叶斯）预设——后者对单类标签可降级打分。"
            )
        raise SingleClassTrainingError(
            f"LinearSVC 训练需要至少两个类别的标签，当前只有 {only!r} 一类。"
        )

    def predict(self, X) -> list[int]:
        """返回每条样本的预测标签（0/1，与 ``LinearSVC.predict`` 一致）。

        dict 模式下的零样本输入（``X == []``）直接返回 ``[]``：空列表
        对 :func:`_is_dict_rows` 有形态歧义，但它是排序器"全筛完"的
        合法终态，不得误报形态混用。数组模式的空输入仍透传 sklearn。
        """
        if self._dict_mode and _is_empty_rows(X):
            return []
        return [int(label) for label in self._model.predict(self._prepare(X))]

    def predict_proba(self, X) -> list[float]:
        """decision_function 经 sigmoid 归一到 [0,1]（无原生概率）。

        分数仅作排序/展示用——单调变换不改变 ``max`` 策略的排序，但
        不具备校准概率语义。dict 模式下的零样本输入（``X == []``，
        排序器全筛完的合法终态）返回 ``[]``，与 NB 载体对空候选的
        行为一致。
        """
        if self._dict_mode and _is_empty_rows(X):
            return []
        scores = self._model.decision_function(self._prepare(X))
        return _sigmoid(scores).tolist()

    # ------------------------------------------------------------------
    # 内部：dict 行 → CSR 稀疏矩阵（列序沿用 fit 时固定的词表）
    # ------------------------------------------------------------------

    def _prepare(self, X):
        """predict 侧输入整形：dict 行按 fit 词表转 CSR，数组透传。"""
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
        # fit 未见的词项被忽略（不在列空间内）——与 NB 语义一致。
        # r9（b03·F2-44）：预测侧同样只分配 O(nnz)，不再每次
        # n_candidates × |vocab| 整块稠密化。
        return _rows_to_csr(list(X), self._columns)
