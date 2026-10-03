"""文献去重 —— 两层去重（精确 DOI/Key + 模糊标题/作者）与传递闭包（见 SPEC.md §4）。

第 1 层 精确：规范化 DOI 非空且相等，或 zotero_key 相等（保留输入顺序靠前者）。
第 2 层 模糊：仅对精确层存活且双方 DOI 均为空的条目做 O(n^2) 两两扫描，
    标题相似度（rapidfuzz.fuzz.token_set_ratio，小写 + 空白折叠形式）>= 90
    且第一作者（authors 按 ";" 取首段、再按逗号/空白取第一词）fuzz.ratio >= 80。
传递闭包：A~B、B~C 时 B、C 均指向最终保留的 A（并查集实现，根即保留条目）。

比较用规范化在模块内本地实现（小写 + 空白折叠），不依赖 coscreen.normalize，
以保证无论解析层清洗策略如何变化，去重行为始终确定、可复现。

复杂度说明：模糊层为 O(n^2)。当输入条数 >= FUZZY_SCAN_LIMIT 时跳过第 2 层
（只做精确去重），并在 DedupReport.note 中写明跳过原因。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from rapidfuzz import fuzz

from coscreen import config
from coscreen.models import Article

#: 输入达到该条数时跳过第二层模糊去重（O(n^2) 扫描代价过高），原因写入报告 note。
FUZZY_SCAN_LIMIT = 2000

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

    note 为实现层扩展字段（SPEC 未定义）：模糊去重被跳过时写入说明，其余为空串。
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

def find_duplicates(articles: list[Article]) -> tuple[list[Article], DedupReport]:
    """返回 (去重后保留的 Article 列表, 报告)。不修改输入。

    - 保留条目按输入顺序排列；每组重复只保留输入顺序靠前的首条。
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
        leader = key_first.get(key)
        if leader is None:
            key_first[key] = i
        else:
            uf.union(leader, i)
            exact_events.append((leader, i, "exact_key"))

    # ---- 第 2 层：模糊去重（仅存活条目、双方 DOI 均为空）----
    note = ""
    survivors = [i for i in range(n) if uf.find(i) == i]
    if n >= FUZZY_SCAN_LIMIT:
        note = (
            f"输入 {n} 条 >= {FUZZY_SCAN_LIMIT}，为控制 O(n^2) 复杂度已跳过"
            "第二层模糊去重（仅精确去重）；如需模糊去重请分批处理"
        )
    elif len(survivors) >= 2:
        titles = {i: _norm_title(articles[i].title) for i in survivors}
        first_authors = {i: _first_author_token(articles[i].authors) for i in survivors}
        has_doi = {i: bool(_norm_doi(articles[i].doi)) for i in survivors}
        for pos_i in range(len(survivors)):
            i = survivors[pos_i]
            if has_doi[i]:
                continue  # 模糊层仅作用于 DOI 均为空的条目
            for pos_j in range(pos_i + 1, len(survivors)):
                j = survivors[pos_j]
                if has_doi[j] or uf.find(i) == uf.find(j):
                    continue
                title_i, title_j = titles[i], titles[j]
                if not title_i or not title_j:
                    continue
                if fuzz.token_set_ratio(title_i, title_j) < config.FUZZY_TITLE_THRESHOLD:
                    continue
                if fuzz.ratio(first_authors[i], first_authors[j]) < config.FUZZY_AUTHOR_THRESHOLD:
                    continue
                uf.union(i, j)
                fuzzy_events.append((i, j, "fuzzy"))

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
