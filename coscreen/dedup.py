"""文献去重 —— 两层去重（精确 DOI/Key + 模糊标题/作者）与传递闭包（见 SPEC.md §4）。

第 1 层 精确：规范化 DOI 非空且相等，或 zotero_key 相等（保留输入顺序靠前者）。
第 2 层 模糊：仅对精确层存活且双方 DOI 均为空的条目做 O(n^2) 两两扫描，
    标题相似度（rapidfuzz.fuzz.token_set_ratio，小写 + 空白折叠形式）>= 90
    且第一作者（authors 按 ";" 取首段、再按逗号/空白取第一词）fuzz.ratio >= 80。
传递闭包：A~B、B~C 时 B、C 均指向最终保留的 A（并查集实现，根即保留条目）。

比较用规范化在模块内本地实现（小写 + 空白折叠），不依赖 coscreen.normalize，
以保证无论解析层清洗策略如何变化，去重行为始终确定、可复现。

复杂度说明：模糊层仍需比较所有可能匹配的候选对，但通过 RapidFuzz 原生批量
计算和分块控制内存；文献数量不再作为跳过模糊去重的理由。
"""

from __future__ import annotations

from dataclasses import dataclass, field
import unicodedata
from collections.abc import Callable

from rapidfuzz import fuzz, process

from coscreen import config
from coscreen.models import Article

#: 每次交给 RapidFuzz 的标题查询分块行数；分块限制临时矩阵内存，不限制总量。
FUZZY_CHUNK_SIZE = 64

#: DuplicatePair.method 的合法取值
METHODS = ("exact_doi", "exact_key", "fuzzy")


@dataclass
class DuplicatePair:
    """一条重复关系：dup_key 指向最终保留条目 kept_key。"""

    kept_key: str
    dup_key: str
    method: str  # "exact_doi" | "exact_key" | "fuzzy"


@dataclass
class DedupReport:
    """去重报告：总数、保留数、两层配对清单。

    note 为兼容报告保留的扩展字段；当前不因输入规模跳过模糊去重。
    """

    total: int = 0
    unique: int = 0
    exact_pairs: list[DuplicatePair] = field(default_factory=list)
    fuzzy_pairs: list[DuplicatePair] = field(default_factory=list)
    note: str = ""

    def n_duplicates(self) -> int:
        return len(self.exact_pairs) + len(self.fuzzy_pairs)


# ---------------------------------------------------------------------------
# 比较用规范化（本地实现，保持确定性）
# ---------------------------------------------------------------------------

def _norm_title(title: str) -> str:
    """标题比较形式：转小写 + 连续空白折叠为单个空格 + 去首尾空白。"""
    return " ".join(title.lower().split())


def normalize_title_candidate(title: str) -> str:
    """Canonical title key for human-review candidates (not an automatic match)."""
    normalized = unicodedata.normalize("NFKC", title or "").casefold()
    parts: list[str] = []
    for char in normalized:
        category = unicodedata.category(char)
        if category.startswith(("P", "S")):
            parts.append(" ")
        else:
            parts.append(char)
    return " ".join("".join(parts).split())


def find_title_candidate_groups(articles: list[Article]) -> list[list[Article]]:
    """Group identical normalized titles for user review in linear time."""
    by_title: dict[str, list[Article]] = {}
    keys_by_title: dict[str, set[str]] = {}
    for article in articles:
        title_key = normalize_title_candidate(article.title)
        if title_key:
            seen = keys_by_title.setdefault(title_key, set())
            if article.zotero_key not in seen:
                by_title.setdefault(title_key, []).append(article)
                seen.add(article.zotero_key)
    return [group for group in by_title.values() if len(group) > 1]


def _norm_doi(doi: str) -> str:
    """DOI 比较形式：去首尾空白 + 转小写（解析层已做前缀/空白清洗，此处兜底）。"""
    return doi.strip().lower()


def _first_author_token(authors: str) -> str:
    """第一作者比较形式：按 ";" 取首段，再按逗号/空白取第一词，转小写。

    例："Smith, John; Lee, Anna" -> "smith"；"Smith J" -> "smith"；"" -> ""。
    """
    if not authors:
        return ""
    head = authors.split(";")[0]
    for token in head.replace(",", " ").split():
        return token.lower()
    return ""


# ---------------------------------------------------------------------------
# 并查集：根节点（最小索引）即该组最终保留的条目 → 天然实现传递闭包
# ---------------------------------------------------------------------------

class _UnionFind:
    def __init__(self, n: int) -> None:
        self._parent = list(range(n))

    def find(self, i: int) -> int:
        parent = self._parent
        root = i
        while parent[root] != root:
            root = parent[root]
        while parent[i] != root:  # 路径压缩
            parent[i], i = root, parent[i]
        return root

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return
        # 较小索引者为根 → 输入顺序靠前的条目保留
        if ra < rb:
            self._parent[rb] = ra
        else:
            self._parent[ra] = rb


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def find_duplicates(
    articles: list[Article], *, include_fuzzy: bool = True,
    progress_callback: Callable[[str, int, int], None] | None = None,
) -> tuple[list[Article], DedupReport]:
    """返回 (去重后保留的 Article 列表, 报告)。不修改输入。

    - DOI 与真实数据库 Key 用于精确匹配；生成的 auto-* 内部键不视为来源标识。
    - 保留条目按输入顺序排列；每组重复只保留输入顺序靠前的首条。
    - include_fuzzy=False 时只做 DOI/Key 匹配，供标题候选人工核验流程使用。
    - progress_callback(stage, completed, total) 用于报告分块匹配进度。
    - DuplicatePair.kept_key 始终指向最终保留条目（含传递闭包情形）。
    """
    n = len(articles)
    uf = _UnionFind(n)
    exact_events: list[tuple[int, int, str]] = []  # (组内代表索引, 重复索引, method)
    fuzzy_events: list[tuple[int, int, str]] = []

    # ---- 第 1 层：精确去重（规范化 DOI 非空相等，或 zotero_key 相等）----
    doi_first: dict[str, int] = {}
    key_first: dict[str, int] = {}
    for i, art in enumerate(articles):
        doi = _norm_doi(art.doi)
        if doi:
            leader = doi_first.get(doi)
            if leader is None:
                doi_first[doi] = i
            else:
                uf.union(leader, i)
                exact_events.append((leader, i, "exact_doi"))
        key = art.zotero_key
        # auto-* 是缺少来源 Key 时创建的内部主键，不能作为自动去重证据。
        generated_key = isinstance(art.raw, dict) and bool(art.raw.get("_generated_key"))
        if not generated_key and not key.startswith("auto-"):
            leader = key_first.get(key)
            if leader is None:
                key_first[key] = i
            else:
                uf.union(leader, i)
                exact_events.append((leader, i, "exact_key"))

    if progress_callback:
        progress_callback("exact", n, n)

    # ---- 第 2 层：模糊去重（仅存活条目、双方 DOI 均为空）----
    note = ""
    survivors = [i for i in range(n) if uf.find(i) == i]
    fuzzy_candidates = [
        i for i in survivors
        if not _norm_doi(articles[i].doi) and _norm_title(articles[i].title)
    ] if include_fuzzy else []
    total_candidates = len(fuzzy_candidates)
    if total_candidates >= 2:
        titles = [_norm_title(articles[i].title) for i in fuzzy_candidates]
        first_authors = [_first_author_token(articles[i].authors) for i in fuzzy_candidates]
        for start in range(0, total_candidates, FUZZY_CHUNK_SIZE):
            end = min(start + FUZZY_CHUNK_SIZE, total_candidates)
            scores = process.cdist(
                titles[start:end], titles[start:],
                scorer=fuzz.token_set_ratio,
                score_cutoff=config.FUZZY_TITLE_THRESHOLD,
                workers=-1,
            )
            for local_i, row in enumerate(scores):
                i_pos = start + local_i
                i = fuzzy_candidates[i_pos]
                # cdist zeroes scores below score_cutoff. Only visit title candidates,
                # then retain the original input-order union semantics.
                for local_j in row.nonzero()[0]:
                    j_pos = start + int(local_j)
                    if j_pos <= i_pos:
                        continue
                    j = fuzzy_candidates[j_pos]
                    if uf.find(i) == uf.find(j):
                        continue
                    if fuzz.ratio(first_authors[i_pos], first_authors[j_pos]) < config.FUZZY_AUTHOR_THRESHOLD:
                        continue
                    uf.union(i, j)
                    fuzzy_events.append((i, j, "fuzzy"))
            if progress_callback:
                progress_callback("fuzzy", end, total_candidates)
    elif progress_callback:
        progress_callback("fuzzy", total_candidates, total_candidates)

    # ---- 汇总：根节点为保留条目 ----
    kept = [articles[i] for i in range(n) if uf.find(i) == i]
    report = DedupReport(total=n, unique=len(kept), note=note)

    def to_pair(leader: int, dup: int, method: str) -> DuplicatePair:
        return DuplicatePair(
            kept_key=articles[uf.find(leader)].zotero_key,
            dup_key=articles[dup].zotero_key,
            method=method,
        )

    report.exact_pairs = [to_pair(leader, dup, m) for leader, dup, m in exact_events]
    report.fuzzy_pairs = [to_pair(leader, dup, m) for leader, dup, m in fuzzy_events]
    return kept, report
