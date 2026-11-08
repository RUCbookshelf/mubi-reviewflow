"""近邻聚类排序（Topic-Cluster Order）：2.1.5 W6 第四类排序方式。

与主动学习排序（``coscreen.al``）共用**同一文本特征表示**——这里不新造
分词/向量化逻辑，直接复用 :class:`coscreen.al.features.TfidfVectorizer`
的默认参数（词级 TF-IDF + 中文二字组 + 内置英文停用词、L2 归一化），因此
余弦相似度就是归一化向量的内积，与 ML 排序“同一套特征”的方法学口径一致。

模块边界（硬约束）：

- **纯函数**：不读数据库、不读文件、不联网、不加载任何模型文件、不做缓存；
  每次重排现算（避免缓存失效后“同数据不同序”的一致性坑）；
- **数值内核 = 既有运行时依赖**：相似度与贪心遍历用 ``numpy`` + ``scipy.sparse``
  （``build/requirements-app.txt`` 已钉版本，统计核心模块同样顶层硬导入）。
  2026-11-08 用户裁决（B'）：只保留这一条路径、不做纯标准库兜底——避免两条
  路径浮点累加次序不同带来的“同数据不同机器不同序”。设计 §三.1 禁的是
  新组件/模型文件/新第三方依赖/联网；numpy/scipy 属既有依赖，不违约。
- **确定性**：给定相同的 ``(vectors, decided_keys, seed, base_order)``，
  输出逐位相同。加固手段：CSR 按 (行, 列) 规范排序后再做稀疏乘（行内累加
  顺序固定）；比较前把余弦 **量化到 12 位小数**（``np.round``），量化后相等
  的并列由 ``base_order`` 中的先后决定（``np.argmax`` 取首个最大值 = 最靠前
  的未决条目）；遍历起点由 ``random.Random(seed)`` 决定。
- **只改顺序**：输出恒为 ``base_order`` 的一个排列（不新增、不丢弃、不去重）。

排序语义（设计 §三.2）：

1. **已决键冻结**：``decided_keys`` 命中的键保持在 ``base_order`` 中的原
   位置（“筛过的位置不动”，未决段重排）；
2. **贪心近邻遍历**：从种子决定的未决起点出发，每步取与当前文献余弦
   相似度最高的未决文献（同分取 ``base_order`` 中靠前者），填回未决位置；
3. **零向量排尾部**：无标题无摘要（或全部为停用词）的未决条目向量为空，
   不参与遍历，按原相对顺序排在未决段尾部；全为零向量时整体保持原序。

复杂度：构建 CSR O(Σ 非零) + 每步一次稀疏矩阵-向量乘 O(当前文献各词的
文档频率之和)，合计 O(Σ_t df(t)²)（与全对点积同量级），但内核在 C 层完成；
1784 条量级遍历段为亚秒级，见 ``build/tests/test_2_1_5_w6_topic_cluster.py``
的 ``PerformanceNails`` 与 ``E:/rf214tmp/g3/w6_perf_probe.txt``。

本模块不产生任何用户可见文案；产品披露语见 ``docs/TOPIC_CLUSTER_ORDER.md``。
"""

from __future__ import annotations

import random
from collections.abc import Iterable, Mapping, Sequence

import numpy as np
from scipy.sparse import csr_matrix

from coscreen.al.features import TfidfVectorizer

__all__ = ["cluster_order", "topic_text", "vectorize"]

#: 余弦量化小数位。稀疏乘的末位浮点噪声（≈1e-16）远小于该量级，量化后
#: 相等即视为精确并列，由 base_order 先后稳定打破——这是“同数据同 seed
#: 完全同序”在实际运行中的加固点（2026-11-08 裁决 B' 第 3 条）。
_COSINE_DECIMALS = 12

#: 一次性稠密相似度矩阵的上限（行数）。1784² × 8B ≈ 25MB；5000 行约 200MB。
#: 超过该规模的队列改用逐行稀疏乘（同样只走 numpy/scipy，只是按行算，
#: 内存 O(m) 而非 O(m²)），保证大库不会被一次重排打爆内存。
_DENSE_SIMILARITY_LIMIT = 5000


def topic_text(
    title: str | None,
    abstract: str | None = None,
    keywords: str | None = None,
) -> str:
    """题录文本：标题 + 摘要（+ 关键词若有）空格拼接，缺省字段为空串。

    只做拼接，不做任何清洗——分词/停用词/二字组由
    :class:`~coscreen.al.features.TfidfVectorizer` 负责（与 ML 同源）。
    非字符串（含 ``None``）一律视作空字段。
    """
    parts: list[str] = []
    for value in (title, abstract, keywords):
        text = value.strip() if isinstance(value, str) else ""
        if text:
            parts.append(text)
    return " ".join(parts)


def vectorize(texts: Sequence[str | None]) -> list[dict[str, float]]:
    """把题录文本转为稀疏 TF-IDF 向量（与 ML 排序同一特征表示）。

    直接复用 :class:`coscreen.al.features.TfidfVectorizer` 的默认参数
    （``ngram_range=(1, 1)`` / ``sublinear_tf=False`` / ``max_df=1.0`` /
    ``cjk_bigram=True``）——即 ``ActiveLearningRanker`` 未选预设时的同一条
    特征路径：词级 TF-IDF + 中文二字组 + 内置英文停用词，逐文档 L2 归一化。
    空文本（或全停用词文本）得到空字典（零向量）。

    ``None`` 与非字符串条目按空文本处理；空语料不报错（返回 ``[]``）。
    """
    return TfidfVectorizer().fit_transform(
        [text if isinstance(text, str) else "" for text in texts]
    )


def _cosine_matrix(vectors: Sequence[Mapping[str, float]]) -> csr_matrix:
    """把稀疏字典列表转成行 L2 归一化的 CSR（行序 = ``vectors`` 顺序）。

    列号按词首次出现顺序分配（确定性），行内按列升序排列后再做稀疏乘——
    CSR 行内累加顺序固定，同一输入在任何机器上都走同一累加次序。
    全零向量保留为空行（norm=0，不做除法）。防御性归一化：即使调用方传入
    未归一化向量，也能得到真正的余弦相似度。
    """
    vocabulary: dict[str, int] = {}
    rows: list[int] = []
    cols: list[int] = []
    data: list[float] = []
    for row, vector in enumerate(vectors):
        for term, weight in vector.items():
            column = vocabulary.get(term)
            if column is None:
                column = vocabulary[term] = len(vocabulary)
            rows.append(row)
            cols.append(column)
            data.append(float(weight))
    matrix = csr_matrix(
        (np.asarray(data, dtype=np.float64),
         (np.asarray(rows, dtype=np.int64), np.asarray(cols, dtype=np.int64))),
        shape=(len(vectors), max(1, len(vocabulary))),
        dtype=np.float64,
    )
    matrix.sum_duplicates()   # 同一 (行, 列) 的重复项按既定顺序合并
    matrix.sort_indices()     # 行内列号升序 → 累加顺序固定
    norms = np.sqrt(np.asarray(matrix.multiply(matrix).sum(axis=1)).ravel())
    nonzero = norms > 0.0
    if nonzero.any():
        scale = np.zeros(len(vectors), dtype=np.float64)
        scale[nonzero] = 1.0 / norms[nonzero]
        matrix = csr_matrix(
            (matrix.data * np.repeat(scale, np.diff(matrix.indptr)),
             matrix.indices, matrix.indptr),
            shape=matrix.shape, dtype=np.float64,
        )
    return matrix


def cluster_order(
    vectors: Mapping[str, Mapping[str, float]],
    decided_keys: Iterable[str] | None,
    seed: int,
    base_order: Sequence[str],
) -> list[str]:
    """按近邻聚类重排 ``base_order``，返回其一个排列。

    参数
    ----
    vectors:
        ``zotero_key -> 稀疏向量``（:func:`vectorize` 的输出按键配对成字典）。
        键缺失、或向量为空字典 → 视为零向量（无文本特征）。向量不必预先
        归一化（内部会按行做 L2 归一化后再算余弦）。
    decided_keys:
        已决键集合（``include``/``exclude``）：这些键**冻结在 ``base_order``
        的原位置**，不参与遍历。``None`` 等价于空集合。
    seed:
        遍历起点种子——``random.Random(seed).randrange(未决非零条目数)``
        决定从哪一条开始贪心遍历。同种子同数据 → 同顺序（可复现，写
        方法学时能交代）。不同种子只改变起点，不改变“同主题相邻”的性质。
    base_order:
        基准顺序（通常是导入顺序），输出恒为其一个排列。

    返回
    ----
    list[str]
        ``base_order`` 的排列：已决键原位；未决非零向量键按贪心近邻遍历
        填入其余位置；未决零向量键按原相对顺序排在未决段尾部。

    边界：``len(base_order) <= 1``、未决条目 <= 1、或未决条目全为零向量时
    直接返回基准序的副本（仍是合法排列）。``base_order`` 中键重复时按
    位置分别处理（同一键的多个位置各自独立），输出仍是多重集合相等的排列。
    """
    base = list(base_order)
    if len(base) <= 1:
        return base
    decided = set(decided_keys or ())
    movable = [index for index, key in enumerate(base) if key not in decided]
    if len(movable) <= 1:
        return base

    vectors_by_position = [vectors.get(base[index]) or {} for index in movable]
    nonzero_rows = [
        row for row, vector in enumerate(vectors_by_position) if vector
    ]
    zero_rows = [
        row for row, vector in enumerate(vectors_by_position) if not vector
    ]
    if not nonzero_rows:
        # 全缺文本：整段按原序（设计 §三.2“零向量按原序排尾部”的退化情形）。
        return base

    matrix = _cosine_matrix(vectors_by_position)
    alive = np.zeros(len(movable), dtype=bool)
    alive[nonzero_rows] = True
    rng = random.Random(int(seed))
    current = nonzero_rows[rng.randrange(len(nonzero_rows))]
    alive[current] = False
    traversal = [current]

    # 一次性算出整张相似度矩阵（C 层稀疏乘，代价 Σdf²，结果量化后逐位可复现）；
    # 队列过大时退化为逐行稀疏乘（内存 O(m)），两条分支都只走 numpy/scipy。
    dense_similarity = None
    if len(movable) <= _DENSE_SIMILARITY_LIMIT:
        dense_similarity = np.round(
            (matrix @ matrix.T).toarray(), _COSINE_DECIMALS)

    for _ in range(len(nonzero_rows) - 1):
        if dense_similarity is not None:
            similarities = dense_similarity[current].copy()
        else:
            similarities = np.round(
                (matrix @ matrix.getrow(current).T).toarray().ravel(),
                _COSINE_DECIMALS,
            )
        similarities[~alive] = -np.inf
        best = int(np.argmax(similarities))  # 首个最大值 = base_order 靠前者
        traversal.append(best)
        alive[best] = False
        current = best

    result = list(base)
    slots = iter([movable[row] for row in traversal + zero_rows])
    for index in movable:
        result[index] = base[next(slots)]
    return result
