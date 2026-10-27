"""文献去重 —— 两层去重（精确 DOI/Key + 模糊标题/作者候选）与传递闭包（见 SPEC.md §4）。

第 1 层 精确（自动合并仅保留可机器核验的证据，U1-P1/U1-P3，与 Web 导入的
    受保护核验/人工候选哲学对齐）：
    a) 规范化 DOI 非空且相等；或
    b) zotero_key 相等且 DOI/标题不冲突（双方 DOI 均非空时以 DOI 裁决；
       否则要求标题规范化后**完全相等**且首作者兼容，R6-D1——一词之差
       如 women/men、phase2/phase3 不再凭 token_set_ratio>=90 自动合并）。
    键相等但 DOI 不等、标题不完全相等或首作者不兼容 → key_conflict_pairs
    候选（不合并，由 stabilize_conflicting_keys 消歧键后各自入库）。
第 2 层 模糊（U1-P1/U1-P10）：仅对精确层存活且双方 DOI 均为空的条目做
    O(n^2) 两两扫描，标题相似度（rapidfuzz.fuzz.token_set_ratio，小写 +
    空白折叠形式）>= 90 且第一作者门（first_author_surname 相似度 >= 80，
    任一侧作者缺失则门控不通过）达标。模糊证据只报告 fuzzy_pairs 候选，
    不再自动合并——由调用方进入人工确认（Web/活体综述候选清单、CLI 打印）。
    候选边经并查集生成树去重（每连通分量至多 n-1 条边）并有总量上限
    FUZZY_PAIR_LIMIT，n 条同模板标题不再产生 O(n^2) 条驻留内存的配对。
传递闭包：A~B、B~C 时 B、C 均指向最终保留的 A（并查集实现，根即保留条目）；
    仅精确层参与并查集。

比较用规范化统一委托 coscreen.normalize（DOI 走 normalize_doi，与解析层
存储形态同一口径，U1-P5/U1-P8/U1-P9）；标题/作者的比较形式在 normalize
中同样是纯函数，行为保持确定、可复现。

复杂度说明：模糊层仍需比较所有可能匹配的候选对，但通过 RapidFuzz 原生批量
计算和分块控制内存；候选配对本身经生成树去重后至多 n-1 条（复审 #4/#10）；
文献数量不再作为跳过模糊去重的理由。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from collections.abc import Callable, Sequence
import unicodedata

from rapidfuzz import fuzz, process

from coscreen import config
from coscreen.models import Article
from coscreen.normalize import (
    first_author_given_names,
    first_author_surname,
    given_names_compatible,
    normalize_doi,
    years_compatible,
)

#: 每次交给 RapidFuzz 的标题查询分块行数；分块限制临时矩阵内存，不限制总量。
FUZZY_CHUNK_SIZE = 64

#: 模糊候选对总量上限（复审 #4/#10：n 条同模板标题曾产生 n(n-1)/2 条
#: DuplicatePair 全量驻留内存，n=2000 实测 1,999,000 对/386MB，n=10000
#: 外推约 5 千万对/数 GB 可 OOM）。候选边经并查集生成树去重后至多 n-1 条，
#: 本上限是对极端输入的最终防线：超出即截断并在报告 note 中显式警告。
FUZZY_PAIR_LIMIT = 50000

#: DuplicatePair.method 的合法取值
METHODS = ("exact_doi", "exact_key", "fuzzy", "exact_key_conflict")


@dataclass
class DuplicatePair:
    """一条重复/候选关系：dup_key 指向最终保留条目 kept_key。

    kept_index/dup_index 为 find_duplicates 输入列表中的下标（-1 表示未提供，
    如外部手工构造的配对），供 stabilize_conflicting_keys 重建合并组。
    method 为 "exact_doi" | "exact_key" | "fuzzy" | "exact_key_conflict"；
    fuzzy 与 exact_key_conflict 是待人工确认的候选，不是已合并证据。
    """

    kept_key: str
    dup_key: str
    method: str
    kept_index: int = -1
    dup_index: int = -1


@dataclass
class DedupReport:
    """去重报告：总数、保留数、精确合并清单与候选清单。

    exact_pairs 为已自动合并的证据事件；fuzzy_pairs（标题相似）与
    key_conflict_pairs（键相等但 DOI/标题冲突）为待人工确认的候选，
    不参与自动合并。note 为兼容报告保留的扩展字段。
    """

    total: int = 0
    unique: int = 0
    exact_pairs: list[DuplicatePair] = field(default_factory=list)
    fuzzy_pairs: list[DuplicatePair] = field(default_factory=list)
    key_conflict_pairs: list[DuplicatePair] = field(default_factory=list)
    note: str = ""

    def n_duplicates(self) -> int:
        """按记录口径返回重复数（total − unique）。

        事件清单可能对同一条重复双计（同 DOI 且同键的行同时产生
        exact_doi 与 exact_key 两条事件，U1-P4/U1-P12）；此处以记录数
        为准，保证 identified − duplicates_removed = screened 可勾稽。
        """
        return max(0, self.total - self.unique)


# ---------------------------------------------------------------------------
# 比较用规范化（委托 coscreen.normalize，保持确定性）
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
    """DOI 比较形式：统一走 normalize.normalize_doi（U1-P5/U1-P8/U1-P9）。

    与解析层存储形态同一口径（剥前缀/删不可见字符/NFKC 折叠全角/去尾部
    标点），未规范化的 DOI 不再同时关闭精确与模糊两条路径。
    """
    return normalize_doi(doi or "")


def _first_author_token(authors: str) -> str:
    """第一作者比较形式：委托 normalize.first_author_surname（U1-P2/U1-P11）。

    识别 'Last, First' 与 'First Last' 两种顺序、剥离 van/de/der 等助词、
    机构作者按整串比；空作者返回 ""，由模糊层门控视为不通过。
    """
    return first_author_surname(authors)


def _first_authors_compatible(a: Article, b: Article) -> bool:
    """首作者是否兼容（R6-D1 + 评审 R1 返修·FB2 + R6-D3/D4 名的区分度）。

    - 双方姓氏均在：相似度达标才兼容（Smith vs Jones 不兼容）；
    - **姓氏相同时名的区分度（R6-D4）**：双侧均含全名词且不一致则不兼容
      （"Smith, John" vs "Smith, Jane" 不再坍缩为同一文献）；一侧只有缩写
      或缺名时按首字母/元数据补全口径兼容；
    - **单侧缺作者视为兼容（元数据补全口径）**：旧版本库（2.1.3 的 CSV
      "Author" 列不映射，U2-P1）行 authors 为空，同键 + 标题完全相等的
      重导不得因作者缺失被判为不同文献（基线 4a06688 的 _key_merge_confirmed
      同样为 True——标题 token>=100 即合并，作者不参与裁决）。库行缺作者
      而本批带作者时由 stabilize 发"元数据补全"警告，不改变合并行为；
    - 双方均无作者视为兼容——无作者元数据记录重导的常见形态。
    """
    surname_a = _first_author_token(a.authors)
    surname_b = _first_author_token(b.authors)
    if not surname_a or not surname_b:
        # 任一侧缺作者：不能证明"作者不同"，按元数据补全处理（不阻断合并）
        return True
    if fuzz.ratio(surname_a, surname_b) < config.FUZZY_AUTHOR_THRESHOLD:
        return False
    return given_names_compatible(
        first_author_given_names(a.authors), first_author_given_names(b.authors))


def _key_merge_confirmed(a: Article, b: Article) -> bool:
    """键相等时是否允许自动合并（U1-P3，R6-D1 收紧）。

    - 双方规范化 DOI 均非空时以 DOI 裁决（相等才合并）；
    - 否则要求**标题规范化后完全相等**且**首作者兼容**才允许自动合并。
      此前"标题 token_set_ratio >= 90 即合并"的口径对一词之差几乎不敏感
      （women/men=98.41、phase2/3=93.18、retraction 前缀=100、词序反转=100），
      同键 + 双方 DOI 为空时不同文献被静默合并并覆盖；现一律改报
      exact_key_conflict 候选（两条都进筛选，不覆盖）。
    - 与 Web 导入路径（review_imports）对 identity_conflict 的判定共用本
      谓词，两处口径不分叉。
    """
    doi_a, doi_b = _norm_doi(a.doi), _norm_doi(b.doi)
    if doi_a and doi_b:
        return doi_a == doi_b
    title_a, title_b = _norm_title(a.title), _norm_title(b.title)
    if not title_a or not title_b:
        return False
    if title_a != title_b:
        return False
    return _first_authors_compatible(a, b)


# ---------------------------------------------------------------------------
# 并查集：根节点（最小索引）即该组最终保留的条目 → 天然实现传递闭包
# ---------------------------------------------------------------------------

class _UnionFind:
    def __init__(self, n: int) -> None:
        self._parent = list(range(n))
        self._size = [1] * n

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
        # 较小索引者为根 → 输入顺序靠前的条目保留；同侧以按大小合并
        # 防止长链退化（复审门禁墙钟：模糊层 4M 次 find 曾占 ~0.6s+
        # find_duplicates 主循环放大），路径压缩后均摊近 O(1)
        if ra < rb:
            self._parent[rb] = ra
            self._size[ra] += self._size[rb]
        else:
            self._parent[ra] = rb
            self._size[rb] += self._size[ra]


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def find_duplicates(
    articles: list[Article], *, include_fuzzy: bool = True,
    progress_callback: Callable[[str, int, int], None] | None = None,
) -> tuple[list[Article], DedupReport]:
    """返回 (去重后保留的 Article 列表, 报告)。不修改输入。

    - 自动合并仅凭两类可机器核验的证据：规范化 DOI 非空且相等；zotero_key
      相等且 DOI/标题不冲突。生成的 auto-* 内部键不视为来源标识。
    - 模糊层证据只进 fuzzy_pairs 候选，不自动合并（U1-P1/U1-P10）；键冲突
      进 key_conflict_pairs 候选（U1-P3）。二者均需人工确认。
    - 保留条目按输入顺序排列；每组重复只保留输入顺序靠前的首条。
    - include_fuzzy=False 时跳过模糊扫描，供精确核验流程使用。
    - progress_callback(stage, completed, total) 用于报告分块匹配进度。
    - DuplicatePair.kept_key 始终指向最终保留条目（含传递闭包情形）；
      候选对（fuzzy/exact_key_conflict）指向配对中输入顺序靠前的一方。
    """
    n = len(articles)
    uf = _UnionFind(n)
    exact_events: list[tuple[int, int, str]] = []  # (组内代表索引, 重复索引, method)
    conflict_events: list[tuple[int, int, str]] = []
    fuzzy_events: list[tuple[int, int, str]] = []

    # ---- 第 1 层：精确去重（规范化 DOI 非空相等，或键相等且不冲突）----
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
            elif _key_merge_confirmed(articles[leader], art):
                uf.union(leader, i)
                exact_events.append((leader, i, "exact_key"))
            else:
                # 键相等但 DOI 不等或标题相似度不足：候选待确认，不合并
                conflict_events.append((leader, i, "exact_key_conflict"))

    if progress_callback:
        progress_callback("exact", n, n)

    # ---- 第 2 层：模糊扫描（仅存活条目、双方 DOI 均为空；只出候选）----
    # 候选边经并查集生成树去重（复审 #4/#10）：同一连通分量只保留至多
    # n-1 条边（每合并两个分量记一条），消费方（候选复核组/标题审阅组）
    # 只依赖连通性，生成树与完全图等价；内存从 O(n^2) 条配对降为 O(n)。
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
        candidate_uf = _UnionFind(n)  # 候选分组并查集（与精确层 uf 相互独立）
        truncated = False
        stop = False
        for start in range(0, total_candidates, FUZZY_CHUNK_SIZE):
            if stop:
                break
            end = min(start + FUZZY_CHUNK_SIZE, total_candidates)
            scores = process.cdist(
                titles[start:end], titles[start:],
                scorer=fuzz.token_set_ratio,
                score_cutoff=config.FUZZY_TITLE_THRESHOLD,
                workers=-1,
            )
            for local_i, row in enumerate(scores):
                if stop:
                    break
                i_pos = start + local_i
                i = fuzzy_candidates[i_pos]
                # cdist zeroes scores below score_cutoff. Only visit title candidates.
                for local_j in row.nonzero()[0]:
                    j_pos = start + int(local_j)
                    if j_pos <= i_pos:
                        continue
                    j = fuzzy_candidates[j_pos]
                    # 作者门（U1-P2/U1-P11）：任一侧第一作者缺失/不可解析
                    # 即不通过——缺作者不等于作者相同，双空也不再放行。
                    if not first_authors[i_pos] or not first_authors[j_pos]:
                        continue
                    if fuzz.ratio(first_authors[i_pos], first_authors[j_pos]) < config.FUZZY_AUTHOR_THRESHOLD:
                        continue
                    if candidate_uf.find(i) == candidate_uf.find(j):
                        continue  # 同组冗余边：连通性已具备，不重复入清单
                    candidate_uf.union(i, j)
                    # 候选待确认（不 union 精确层）：写入 fuzzy_events 供人工清单使用
                    fuzzy_events.append((i, j, "fuzzy"))
                    if len(fuzzy_events) >= FUZZY_PAIR_LIMIT:
                        truncated = True
                        stop = True
                        break
            if progress_callback:
                progress_callback("fuzzy", end, total_candidates)
        if truncated:
            note = (
                f"模糊候选对数达到 {FUZZY_PAIR_LIMIT} 上限，已截断：仅保留最先"
                f"确认的 {len(fuzzy_events)} 条候选边，其余相似标题组未全部展开，"
                "请分批导入或人工核对"
            )
    elif progress_callback:
        progress_callback("fuzzy", total_candidates, total_candidates)

    # ---- 汇总：根节点为保留条目（仅精确层合并）----
    kept = [articles[i] for i in range(n) if uf.find(i) == i]
    report = DedupReport(total=n, unique=len(kept), note=note)

    def to_pair(leader: int, dup: int, method: str) -> DuplicatePair:
        return DuplicatePair(
            kept_key=articles[uf.find(leader)].zotero_key,
            dup_key=articles[dup].zotero_key,
            method=method,
            kept_index=leader,
            dup_index=dup,
        )

    report.exact_pairs = [to_pair(leader, dup, m) for leader, dup, m in exact_events]
    report.fuzzy_pairs = [to_pair(leader, dup, m) for leader, dup, m in fuzzy_events]
    report.key_conflict_pairs = [to_pair(leader, dup, m) for leader, dup, m in conflict_events]
    return kept, report


# ---------------------------------------------------------------------------
# 键消歧（U1-P3/U1-P6）：复用后端 _stabilize_generated_import_keys 的思路
# ---------------------------------------------------------------------------

def merged_group_roots(count: int, pairs: Sequence[DuplicatePair]) -> list[int]:
    """返回 0..count-1 每个下标所在精确合并组的根下标（根=组内最小下标）。

    仅 method 为 exact_doi/exact_key 的事件参与组重建（fuzzy 与
    exact_key_conflict 是候选，不是合并证据）；下标越界或未提供（-1）
    的配对忽略。供调用方按"组根"做记录级统计（U1-P4 的 total-unique
    口径在 living_review 等按行消费场景的落地）。
    """
    uf = _UnionFind(count)
    for pair in pairs:
        if pair.method in ("exact_doi", "exact_key") and 0 <= pair.kept_index < count and 0 <= pair.dup_index < count:
            uf.union(pair.kept_index, pair.dup_index)
    return [uf.find(i) for i in range(count)]


def _stable_content_id(art: Article) -> str:
    """记录级稳定内容标识（U2-P5/#18：_record_id 消费）：优先解析器写入的
    raw["_record_id"]（内容稳定、含同文件 #N occurrence，不随文件字节漂移），
    其次 raw["_fallback_id"]（v2.1.3 逐字一致形态，旧库行回退），否则
    DOI|标题|作者|年份。同一文件/同内容重导得到同一标识。"""
    raw = art.raw if isinstance(art.raw, dict) else {}
    base_id = str(raw.get("_record_id") or "").strip()
    if not base_id:
        base_id = str(raw.get("_fallback_id") or "").strip()
    if not base_id:
        base_id = f"{art.doi}|{art.title}|{art.authors}|{art.year}"
    return base_id


def _disambiguation_key(
    key: str, art: Article, occupied: set[str],
    known_by_key: dict[str, Article],
) -> str:
    """为冲突键生成确定性消歧键：键 + "-" + sha256(键|稳定内容标识)[:12]。

    后缀若命中**库内同一文献的既往消歧行**（_key_merge_confirmed 成立）则
    直接沿用——重导同一文件时后缀与首导一致，走 upsert 更新而非再插新行
    （重导幂等；复审 #2/#9 场景的跨批次稳定性）。仍冲突时按 serial 递增。
    """
    suffix = hashlib.sha256(
        f"{key}|{_stable_content_id(art)}".encode("utf-8")
    ).hexdigest()[:12]
    candidate = f"{key}-{suffix}"
    db_art = known_by_key.get(candidate)
    if db_art is not None and _key_merge_confirmed(db_art, art):
        return candidate  # 同一文献首导时的消歧键：沿用走更新路径
    serial = 2
    while candidate in occupied:
        candidate = f"{key}-{suffix}-{serial}"
        serial += 1
    return candidate


def _is_generated_key_article(art: Article) -> bool:
    """是否为无来源 Key 的内部生成键（auto-*）记录。

    解析器对无 Key 行写入 raw["_generated_key"]=True；库内旧行以
    "auto-" 前缀识别（2.1.3 及更早的行没有该 raw 标记）。
    """
    raw = art.raw if isinstance(art.raw, dict) else {}
    return bool(raw.get("_generated_key")) or art.zotero_key.startswith("auto-")


def _content_match_key(art: Article) -> tuple[str, str, str, object]:
    """内容匹配键（R6-D2 方案2 + R6-D4 名的区分度）：标题+姓氏+名+年份。"""
    year = art.year
    if year is not None:
        try:
            year = int(year)
        except (TypeError, ValueError):
            year = str(year).strip()
    return (_norm_title(art.title), _first_author_token(art.authors),
            first_author_given_names(art.authors), year)


def stabilize_conflicting_keys(
    articles: list[Article],
    pairs: list[DuplicatePair],
    *,
    known: list[Article] | None = None,
    warnings: list[str] | None = None,
) -> list[tuple[str, str]]:
    """为"键相同但未合并"的条目就地生成消歧键，返回 (旧键, 新键) 列表。

    - 从 pairs 中 method 为 exact_doi/exact_key 的事件按下标重建合并组
      （fuzzy/exact_key_conflict 候选对不参与组重建，但键会同步刷新）；
    - **组保留键全集进占用集**（复审 #2/#9）：每个精确组按组根顺序决策，
      组代表保留原键，组内每个成员的字面键只要不与其他组/库内冲突都
      计入占用集——两个不同组各有一成员持同一字面键时，后到组的该成员
      加确定性后缀，绝不互相覆盖；
    - **成员消歧加后缀而非改名为组键**（复审 #9）：被其他组占用或与库内
      同键条目冲突的成员，后缀自己的键成为独立行（由 is_duplicate_of
      标记），不再坍缩到组代表的键上覆盖代表行内容；
    - **生成键身份连续**（R6-D2 方案2，R1 铁律；评审 R1 返修·FB1/FB3）：
      无来源 Key 的记录入库前在库内既有行中匹配（不看键形），优先级为
      raw["_fallback_id"]（字节相同文件的同一行）→ content_hash（2.1.3
      起即持久化的 sha1(doi|title)，不受 authors 缺失/年份抽取口径漂移
      影响）→（规范化标题+首作者姓氏+年份）。命中沿用库内键走 UPDATE
      路径；与库内行同组合并的生成键成员同样沿用库内键。既有库主键
      永不重写。
    - ``warnings``（可选出参）：收集需向用户显式提示的事件——多条本批
      记录内容匹配到同一库内行（后写覆盖前写，R3 显式警告不改变行为）、
      库内行缺作者而本批补全元数据（FB2/FB5）。
    """
    n = len(articles)
    uf = _UnionFind(n)
    for pair in pairs:
        if pair.method in ("exact_doi", "exact_key") and 0 <= pair.kept_index < n and 0 <= pair.dup_index < n:
            uf.union(pair.kept_index, pair.dup_index)

    def _warn(message: str) -> None:
        # 去重：同一事件经不同路径（预扫描/组循环）只提示一次
        if warnings is not None and message not in warnings:
            warnings.append(message)

    def _metadata_completion_note(key: str, db_art: Article, art: Article) -> None:
        """库内行缺作者而本批记录带作者：按元数据补全处理并显式警告（FB2）。"""
        if not _first_author_token(db_art.authors) and _first_author_token(art.authors):
            _warn(
                f"库内行 {key} 缺作者，本批导入补全了该行元数据"
                "（同一文献，保留键走更新路径）"
            )

    known_by_key: dict[str, Article] = {}
    if known:
        for art in known:
            known_by_key.setdefault(art.zotero_key, art)
    known_ids = {id(art) for art in known or ()}

    # ---- 生成键身份匹配：沿用库内键，保持跨公式版本的身份连续 ----
    # 2.1.3 旧行的 authors 可能为空（CSV "Author" 列不映射，U2-P1）、年份
    # 取值口径不同（extract_year 括号/句末优先），故（标题+姓氏+年份）只是
    # 最后一级；content_hash 与 _fallback_id 不受两者影响（FB1/FB3）。
    fallback_index: dict[str, str] = {}   # raw["_fallback_id"] -> 库内键
    record_id_index: dict[str, str] = {}  # raw["_record_id"] -> 库内键（U2-P5）
    hash_index: dict[str, str] = {}       # content_hash -> 库内键
    content_index: dict[tuple[str, str, str, object], str] = {}
    for key_text, art in known_by_key.items():
        raw = art.raw if isinstance(art.raw, dict) else {}
        record_id = str(raw.get("_record_id") or "").strip()
        if record_id:
            record_id_index.setdefault(record_id, key_text)
        fallback_id = str(raw.get("_fallback_id") or "").strip()
        if fallback_id:
            fallback_index.setdefault(fallback_id, key_text)
        if art.content_hash:
            hash_index.setdefault(art.content_hash, key_text)
        match_key = _content_match_key(art)
        if match_key[0]:  # 空标题不参与内容匹配（空标题无区分度）
            content_index.setdefault(match_key, key_text)
    key_positions: dict[str, int] = {}
    for i, art in enumerate(articles):
        key_positions.setdefault(art.zotero_key, i)

    def _match_legacy_key(art: Article) -> str | None:
        """按分层在库内既有行中匹配旧身份，返回库内目标键；无命中返回 None。

        层级：raw["_record_id"]（内容稳定、含 #N occurrence，不随文件字节
        漂移——U2-P5/#18 的重导出零新增）→ raw["_fallback_id"]（v2.1.3
        逐字一致形态，字节相同文件的同一行）→ content_hash（2.1.3 起持久
        化，不受 authors 缺失/年份口径漂移影响；复审 D2 回归修复：哈希命中
        **必须叠加首作者兼容门**——content_hash 只含 doi|title，同题不同
        作者会命中，直接沿用会把 2.1.3 的同题坍缩重新引入；不兼容时显式
        警告并落后续层，绝不静默沿用。年份不参与裁决：与
        _key_merge_confirmed 同口径（标题相等+作者兼容即为同一文献），
        2.1.3↔2.1.4 的年份抽取口径漂移（FB1/FB3 验收）仍可沿用）
        →（规范化标题+首作者姓氏+名+年份）。
        """
        raw = art.raw if isinstance(art.raw, dict) else {}
        record_id = str(raw.get("_record_id") or "").strip()
        if record_id and record_id in record_id_index:
            return record_id_index[record_id]
        fallback_id = str(raw.get("_fallback_id") or "").strip()
        if fallback_id and fallback_id in fallback_index:
            return fallback_index[fallback_id]
        if art.content_hash and art.content_hash in hash_index:
            key_text = hash_index[art.content_hash]
            db_art = known_by_key.get(key_text)
            if db_art is None or _first_authors_compatible(art, db_art):
                return key_text
            _warn(
                f"记录与库内行 {key_text} 的内容哈希相同（标题一致），但首作者"
                "不兼容，未沿用库内键（视为不同文献，各自成行）"
            )
        match_key = _content_match_key(art)
        if match_key[0] and match_key in content_index:
            return content_index[match_key]
        return None

    adopted_targets: dict[str, list[int]] = {}  # 库内键 -> 命中的本批下标
    for i, art in enumerate(articles):
        if not _is_generated_key_article(art):
            continue
        if id(art) in known_ids:
            # 评审 R2 返修·FB-A/FB-C：库内旧行（auto-* 同样命中
            # _is_generated_key_article）不参与本批身份匹配——它们经
            # hash/content 索引与自身或彼此互相命中时，会被计入
            # adopted_targets（把库内行当"本批记录"误报坍缩警告：1 旧 +
            # 1 新报"本批 2 条"、库内两条同哈希行 + 无关导入也告警），
            # 且形成与 exact_pairs 无关的阴影合并组。仅本批（非库内）
            # 生成键记录需要沿用库内键。
            continue
        target = _match_legacy_key(art)
        if target is None:
            continue
        db_art = known_by_key.get(target)
        if db_art is not None:
            doi_known, doi_new = _norm_doi(db_art.doi), _norm_doi(art.doi)
            if doi_known and doi_new and doi_known != doi_new:
                continue  # DOI 冲突：不同文献，不沿用库内键
            # 评审 R2 返修·FB-B：content/_fallback 分层命中沿用库内键且库行
            # 缺作者时，同样发"元数据补全"警告（与来源键路径同一口径，
            # 不静默改写库行 authors）。
            _metadata_completion_note(target, db_art, art)
        # 记账所有命中（含 _fallback_id 层）：不同分层（同文件行 vs 哈希/
        # 内容）仍可能命中同一库内行，坍缩警告按目标键计数（FB5）——
        # 只统计本批命中（库内行已在上面跳过）。
        adopted_targets.setdefault(target, []).append(i)
        j = key_positions.get(target)
        if j is not None and j != i:
            # 库内行同在本列表（combined 调用形态）：并组，走更新路径
            uf.union(j, i)
        elif j is None:
            # 分离传参形态（known 不在 articles 内）：直接沿用库内键
            art.zotero_key = target
    for target, hits in adopted_targets.items():
        if len(hits) > 1:
            # 多条本批记录匹配到同一库内行：保留 2.1.3 的坍缩语义（不新增
            # 行，满足零新增行验收），但必须显式警告后写覆盖前写（R3）。
            _warn(
                f"本批 {len(hits)} 条记录内容匹配到同一库内行 {target}："
                "后写将覆盖前写（沿用旧版坍缩语义，零新增行）；"
                "若非同一文献请人工核对"
            )

    # ---- 组键决策：保留键全集进占用集 + 确定性后缀 ----
    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(uf.find(i), []).append(i)

    occupied: dict[str, int] = {}  # 最终保留键（含成员保留键全集）-> 组根下标
    final_key: dict[int, str] = {}  # 组根下标 -> 最终键
    renames: list[tuple[str, str]] = []
    for root in sorted(groups):
        rep = articles[root]
        key = rep.zotero_key
        db_art = known_by_key.get(key)
        if (db_art is not None and not _key_merge_confirmed(db_art, rep)) or key in occupied:
            # 与库内同键条目冲突，或被本批先到组占用：加确定性后缀
            chosen = _disambiguation_key(key, rep, set(occupied), known_by_key)
            renames.append((key, chosen))
            rep.zotero_key = chosen
        else:
            chosen = key
            if db_art is not None:
                # 与库内同键条目兼容（同一文献）保留原键：库行缺作者时
                # 本批元数据补全，显式警告（FB2）
                _metadata_completion_note(key, db_art, rep)
        occupied[chosen] = root
        final_key[root] = chosen

        for i in groups[root]:
            if i == root:
                continue
            art = articles[i]
            if (_is_generated_key_article(art) and id(articles[root]) in known_ids
                    and id(art) not in known_ids):
                # 生成键成员并入**库内行**为根的组（DOI/内容匹配）：沿用
                # 库内键走更新路径（身份连续，不计入消歧清单）。库内行自身
                # 绝不改键（R1：既有库主键永不重写）。
                if final_key[root] != art.zotero_key:
                    art.zotero_key = final_key[root]
                continue
            # 以下两种情况必须改名，否则 upsert 主键会覆盖别的行：
            # 1) 本条目的字面键被批内其他合并组占用为保留键；
            # 2) 本条目的字面键在库内（known）对应的是"另一篇文献"（DOI/
            #    标题/作者冲突）。库内同键若与本条目是同一文献，则保留成员
            #    原键：upsert 走既有行的更新路径，重复关系由 is_duplicate_of
            #    标记表达。改名一律加后缀成为独立行，绝不改名为组键。
            keeper = occupied.get(art.zotero_key)
            db_art = known_by_key.get(art.zotero_key)
            occupied_elsewhere = (
                (keeper is not None and keeper != root)
                or (db_art is not None and not _key_merge_confirmed(db_art, art))
            )
            if occupied_elsewhere:
                new_key = _disambiguation_key(
                    art.zotero_key, art, set(occupied), known_by_key)
                renames.append((art.zotero_key, new_key))
                art.zotero_key = new_key
                occupied[new_key] = root
            else:
                # 保留自己的键：重复成员单独成行，由 is_duplicate_of 标记；
                # 该键计入本组保留键全集，阻止后续组再占用。
                if db_art is not None:
                    # 与库内同键条目兼容保留原键（更新路径）：库行缺作者时
                    # 本批元数据补全，显式警告（FB2）
                    _metadata_completion_note(art.zotero_key, db_art, art)
                occupied.setdefault(art.zotero_key, root)

    # 同步全部配对的键（含 fuzzy/exact_key_conflict 候选对）。改名后自指的
    # 配对（双方归一到同一键，如同键同 DOI 的自然坍缩/生成键沿用库内键）
    # 从清单剔除：它们不产生标记，留下只会让"精确重复 N"与"重复标记 N"
    # 两条口径失真（复审 #9：自指配对曾使重复标记被静默跳过且无迹可查）。
    refreshed: list[DuplicatePair] = []
    for pair in pairs:
        if 0 <= pair.kept_index < n:
            pair.kept_key = articles[uf.find(pair.kept_index)].zotero_key
        if 0 <= pair.dup_index < n:
            pair.dup_key = articles[pair.dup_index].zotero_key
        both_indexed = 0 <= pair.kept_index < n and 0 <= pair.dup_index < n
        if not (both_indexed and pair.kept_key == pair.dup_key):
            refreshed.append(pair)
    pairs[:] = refreshed
    return renames
