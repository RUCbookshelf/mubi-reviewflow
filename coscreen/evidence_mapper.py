"""证据地图（Evidence Mapper，衍生产品规格 2026-09-30 §6）。

对已导入的文献集合做 TF-IDF 主题聚类与 x×y 交叉计数热图，识别研究缺口。
**纯计算模块**：不连库、不起服务、不联网、不落盘；scikit-learn/numpy 在
聚类函数体内懒导入，模块 import 零副作用。

管线（:func:`generate_evidence_map`）：

1. **TF-IDF 向量化**：复用 ``coscreen/al/features.py`` 的 ``TfidfVectorizer``
   （确定性实现：英文 unigram+bigram、CJK 字符 bigram、L2 归一化）。
   语料 = 每篇文献的 ``标题 + 摘要`` 拼接；使用默认参数，``cjk_bigram=True``
   与 AL 特征完全一致——CJK bigram 优势保留（产品哲学 §0 约束 5）。
2. **K-Means 聚类**（scikit-learn ``KMeans``，``random_state=0``、
   ``n_init=10``，可复现）：每篇文献分配一个主题簇；簇代表词 = 质心
   （成员向量均值）权重最高的前 5 个词。``cluster_count`` 大于文献数时
   自动收敛到文献数；全空文本（词表为空）时全体入簇 0、代表词为空。
3. **维度取值**（标签是语言中立的稳定代码，前端经 i18n 字典映射显示名）：

   ===============  =====================================================
   维度             取值规则
   ===============  =====================================================
   ``year``         题录 ``year`` 中第一个 1800–2099 的四位数字；缺失 →
                     ``unknown``
   ``journal``      期刊名压缩空白；缺失 → ``unknown``；按文献数降序取
                     前 20 个（并列按名称升序），其余并入 ``other``
   ``study_design`` 标题+摘要关键词规则（精确优先，见
                     ``_STUDY_DESIGN_RULES``；都不中 → ``other``）
   ``population``   标题+摘要关键词规则（精确优先，见
                     ``_POPULATION_RULES``；都不中 → ``other``）
   ``cluster``      第 2 步主题簇，标签 ``cluster {id}``
   ===============  =====================================================

4. **交叉计数矩阵**：``matrix[i][j]`` = x 取 ``x_labels[i]`` 且 y 取
   ``y_labels[j]`` 的文献数（行 = x 维度，列 = y 维度）。
5. **研究缺口**：``matrix`` 中计数为 0 的单元格，每条
   ``{"x": x_label, "y": y_label, "note": "无研究覆盖"}``。

规则推断（study_design/population）是**精确优先的启发式**：只认明确的
关键词组合，宁缺毋滥（匹配不到归入 ``other``，不臆测）。方法学出处、
公式与限制见 ``docs/methodology/evidence_mapper.md``。
"""

from __future__ import annotations

import re
from collections import Counter

from coscreen.al.features import TfidfVectorizer

__all__ = [
    "DIMENSIONS",
    "GAP_NOTE",
    "MAX_CATEGORY_LABELS",
    "OTHER_LABEL",
    "TOP_TERMS_PER_CLUSTER",
    "UNKNOWN_LABEL",
    "generate_evidence_map",
]

#: 可用的 x/y 维度（"cluster" 为第 2 步主题簇；规格 §6.2 四个题录维度 + 簇）。
DIMENSIONS: tuple[str, ...] = ("year", "journal", "study_design", "population", "cluster")

#: 研究缺口单元格的固定备注（与规格 §6.3 示例一致；后端文案服务端使用）。
GAP_NOTE: str = "无研究覆盖"

#: 缺失取值的统一标签（year 缺失 / journal 缺失）。
UNKNOWN_LABEL: str = "unknown"

#: journal 维度截断后剩余取值的归并标签。
OTHER_LABEL: str = "other"

#: journal 维度保留的标签上限（其余并入 ``other``，保证热图可读）。
MAX_CATEGORY_LABELS: int = 20

#: 每个主题簇输出的代表词个数（规格 §6.4 前端展示前 5 个关键词）。
TOP_TERMS_PER_CLUSTER: int = 5

# 研究设计规则：按顺序匹配（先命中先归类），大小写不敏感。
# 优先级 = 特异性：证据合成类（meta/SR）> 试验类（RCT）> 观察类。
_STUDY_DESIGN_RULES: tuple[tuple[str, tuple[re.Pattern[str], ...]], ...] = (
    ("meta_analysis", (re.compile(r"meta-?analys", re.IGNORECASE),)),
    ("systematic_review", (re.compile(r"systematic\s+review", re.IGNORECASE),)),
    ("rct", (
        re.compile(r"randomi[sz]ed\s+(controlled\s+)?trial", re.IGNORECASE),
        re.compile(r"randomly\s+(assigned|allocated)", re.IGNORECASE),
    )),
    ("cohort", (re.compile(r"\bcohorts?\b", re.IGNORECASE),)),
    ("case_control", (re.compile(r"case-?\s?control", re.IGNORECASE),)),
    ("cross_sectional", (re.compile(r"cross-?\s?sectional", re.IGNORECASE),)),
    ("diagnostic", (
        re.compile(r"diagnostic\s+accuracy", re.IGNORECASE),
        re.compile(r"sensitivity\s+and\s+specificity", re.IGNORECASE),
    )),
)

# 人群规则：按顺序匹配（先命中先归类）。特定人群（儿童/老年）先于动物，
# 动物先于泛化人群词（动物研究的摘要也可能出现 "patients"）。
_POPULATION_RULES: tuple[tuple[str, tuple[re.Pattern[str], ...]], ...] = (
    ("children", (re.compile(
        r"\b(child(ren|hood)?|infants?|adolescen\w*|p[ae]diatric\w*|neonat\w*|boys|girls)\b",
        re.IGNORECASE),)),
    ("older_adults", (re.compile(
        r"\b(older\s+adults?|elderly|geriatric\w*|nursing\s+home|postmenopausal)\b",
        re.IGNORECASE),)),
    ("animals", (re.compile(
        r"\b(mice|mouse|murine|rats?|rabbits?|guinea\s+pigs?|dogs?|cats?|swine|pigs?|piglets?|"
        r"sheep|goats?|hamsters?|zebrafish|cattle|cows?|horses?|monkeys|primates|in\s+vivo|animals?)\b",
        re.IGNORECASE),)),
    ("patients", (re.compile(r"\bpatients?\b", re.IGNORECASE),)),
    ("adults", (re.compile(r"\badults?\b", re.IGNORECASE),)),
)

#: 题录 year 字段中可识别的年份范围（避免把页码/编号当年份）。
_YEAR_RE = re.compile(r"\b(1[89]\d{2}|20\d{2})\b")


def _year_label(year: object) -> str:
    """从题录 ``year``（int/str/None，如 ``2020``、``"2020-05"``）提取四位年份。"""
    if year is None:
        return UNKNOWN_LABEL
    match = _YEAR_RE.search(str(year))
    return match.group(1) if match else UNKNOWN_LABEL


def _rule_label(text: str, rules: tuple[tuple[str, tuple[re.Pattern[str], ...]], ...]) -> str:
    """按优先级匹配关键词规则；都不中 → ``other``（精确优先，不臆测）。"""
    for label, patterns in rules:
        if any(pattern.search(text) for pattern in patterns):
            return label
    return OTHER_LABEL


def _dimension_value(article: dict, dimension: str, cluster_id: int) -> str:
    """单篇文献在指定维度上的取值（稳定代码标签）。"""
    if dimension == "cluster":
        return f"cluster {cluster_id}"
    if dimension == "year":
        return _year_label(article.get("year"))
    if dimension == "journal":
        journal = " ".join(str(article.get("journal") or "").split())
        return journal or UNKNOWN_LABEL
    text = f"{article.get('title') or ''} {article.get('abstract') or ''}"
    if dimension == "study_design":
        return _rule_label(text, _STUDY_DESIGN_RULES)
    return _rule_label(text, _POPULATION_RULES)


def _journal_labels(values: list[str]) -> tuple[list[str], list[str]]:
    """journal 维度标签：按文献数降序（并列按名称升序）取前
    ``MAX_CATEGORY_LABELS`` 个，其余并入 ``other``。

    返回 ``(标签列表, 重映射后的取值列表)``——热图行列数有界，长尾期刊
    不逐列展开（设计决策与限制见方法学文档）。
    """
    counts = Counter(values)
    ranked = sorted(counts, key=lambda label: (-counts[label], label))
    if len(ranked) <= MAX_CATEGORY_LABELS:
        return ranked, values
    kept = set(ranked[:MAX_CATEGORY_LABELS])
    return ranked[:MAX_CATEGORY_LABELS] + [OTHER_LABEL], [
        value if value in kept else OTHER_LABEL for value in values
    ]


def _ordered_labels(values: list[str], dimension: str) -> tuple[list[str], list[str]]:
    """维度取值 → 确定性排序的标签列表；返回 ``(标签, 原样取值)``。"""
    if dimension == "journal":
        return _journal_labels(values)
    counts = Counter(values)
    if dimension == "year":
        labels = sorted(
            counts, key=lambda label: (label == UNKNOWN_LABEL, int(label) if label != UNKNOWN_LABEL else 0)
        )
    elif dimension in ("study_design", "population"):
        rules = _STUDY_DESIGN_RULES if dimension == "study_design" else _POPULATION_RULES
        canonical = [label for label, _ in rules] + [OTHER_LABEL]
        labels = [label for label in canonical if label in counts]
    else:  # cluster
        labels = sorted(counts, key=lambda label: int(label[len("cluster "):]))
    return labels, values


def _cluster_articles(
    texts: list[str], cluster_count: int
) -> tuple[list[int], dict[int, list[str]]]:
    """TF-IDF + K-Means 主题聚类。

    返回 ``(每篇文献的簇 id 列表, {簇 id: 代表词列表})``。scikit-learn/numpy
    在此懒导入（模块 import 保持零副作用）。``random_state=0``、
    ``n_init=10`` 固定 → 同一输入可复现；空文本语料（词表为空）全体入簇 0。
    """
    vectorizer = TfidfVectorizer()  # 默认参数：cjk_bigram=True（CJK 优势保留）
    vectors = vectorizer.fit_transform(texts)
    if not vectorizer.vocab:  # 全部文本分词为空（如空标题+空摘要）
        return [0] * len(texts), {0: []}
    import numpy as np
    from sklearn.cluster import KMeans

    index = {idx: term for term, idx in vectorizer.vocab.items()}
    matrix = np.zeros((len(texts), len(vectorizer.vocab)), dtype=float)
    for row, vector in enumerate(vectors):
        for term, value in vector.items():
            matrix[row, vectorizer.vocab[term]] = value
    k = max(1, min(int(cluster_count), len(texts)))
    model = KMeans(n_clusters=k, random_state=0, n_init=10)
    labels = [int(label) for label in model.fit_predict(matrix)]
    top_terms: dict[int, list[str]] = {}
    for cluster_id in sorted(set(labels)):
        centroid = model.cluster_centers_[cluster_id]  # 质心 = 成员向量均值
        order = np.argsort(-centroid, kind="stable")[:TOP_TERMS_PER_CLUSTER]
        top_terms[cluster_id] = [
            index[int(pos)] for pos in order if centroid[pos] > 0.0
        ]
    return labels, top_terms


def generate_evidence_map(
    articles: list[dict],
    x_dimension: str,
    y_dimension: str,
    cluster_count: int = 8,
) -> dict:
    """生成证据地图数据（规格 §6.2；不含可视化，由前端渲染）。

    参数：
        articles: 文献 dict 列表（``title``/``abstract``/``authors``/
            ``journal``/``year``，键缺失按空值处理）。
        x_dimension: ``"year" | "journal" | "study_design" | "population" | "cluster"``
        y_dimension: 同上（必须不同于 x）。
        cluster_count: TF-IDF 聚类数（≥1，默认 8；大于文献数时收敛到文献数）。

    返回（全部为 JSON 可序列化的 Python 内置类型）::

        {
          "matrix":     [[count, ...], ...],  # matrix[i][j] = (x_labels[i], y_labels[j]) 的文献数
          "cell_members": [[[zotero_key, ...], ...], ...],  # 与 matrix 同形，列出每格成员
          "x_labels":   [...],                # 行标签（确定性排序，见模块 docstring）
          "y_labels":   [...],                # 列标签
          "clusters":   [{"id": 0, "top_terms": ["diabetes", ...], "count": 45}, ...],
          "gaps":       [{"x": "...", "y": "...", "note": "无研究覆盖"}, ...],
        }

    空文献集返回全空结构（不报错）；维度未知 / x==y / cluster_count 非法
    抛 :class:`ValueError`（API 层映射 400/422）。
    """
    for dimension in (x_dimension, y_dimension):
        if dimension not in DIMENSIONS:
            raise ValueError(
                f"未知维度：{dimension!r}（可选：{'、'.join(DIMENSIONS)}）"
            )
    if x_dimension == y_dimension:
        raise ValueError("x 与 y 维度不能相同。")
    if (
        isinstance(cluster_count, bool)
        or not isinstance(cluster_count, int)
        or cluster_count < 1
    ):
        raise ValueError(f"clusters 必须是 ≥1 的整数，收到 {cluster_count!r}")

    checked: list[dict] = []
    for position, article in enumerate(articles):
        if not isinstance(article, dict):
            raise ValueError(
                f"articles[{position}] 必须是 dict（title/abstract/journal/year...）"
            )
        checked.append(article)
    if not checked:
        return {"matrix": [], "cell_members": [], "x_labels": [], "y_labels": [],
                "clusters": [], "gaps": []}

    texts = [f"{a.get('title') or ''} {a.get('abstract') or ''}" for a in checked]
    cluster_ids, top_terms = _cluster_articles(texts, cluster_count)

    x_values = [
        _dimension_value(article, x_dimension, cluster_id)
        for article, cluster_id in zip(checked, cluster_ids)
    ]
    y_values = [
        _dimension_value(article, y_dimension, cluster_id)
        for article, cluster_id in zip(checked, cluster_ids)
    ]
    x_labels, x_values = _ordered_labels(x_values, x_dimension)
    y_labels, y_values = _ordered_labels(y_values, y_dimension)

    x_index = {label: row for row, label in enumerate(x_labels)}
    y_index = {label: col for col, label in enumerate(y_labels)}
    matrix = [[0] * len(y_labels) for _ in x_labels]
    cell_members = [[[] for _ in y_labels] for _ in x_labels]
    for article, x_value, y_value in zip(checked, x_values, y_values):
        if x_value in x_index and y_value in y_index:
            row, col = x_index[x_value], y_index[y_value]
            matrix[row][col] += 1
            zotero_key = article.get("zotero_key")
            if isinstance(zotero_key, str) and zotero_key:
                cell_members[row][col].append(zotero_key)

    gaps = [
        {"x": x_labels[row], "y": y_labels[col], "note": GAP_NOTE}
        for row in range(len(x_labels))
        for col in range(len(y_labels))
        if matrix[row][col] == 0
    ]
    cluster_sizes = Counter(cluster_ids)
    clusters = [
        {"id": cluster_id, "top_terms": top_terms.get(cluster_id, []),
         "count": cluster_sizes[cluster_id]}
        for cluster_id in sorted(cluster_sizes)
    ]
    return {
        "matrix": matrix,
        "cell_members": cell_members,
        "x_labels": x_labels,
        "y_labels": y_labels,
        "clusters": clusters,
        "gaps": gaps,
    }
