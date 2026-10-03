"""方案设计器（Protocol Designer）：PICO 结构化与检索式生成（衍生产品规格 §1）。

纯计算模块，零副作用：不连数据库、不起服务、不做任何网络请求。
URL 生成只是字符串拼接（``generate_pubmed_url`` 等），点击链接后的检索
由用户浏览器完成——本模块永不联网（产品哲学 §0.1 离线优先）。

方法学锚：Cochrane Handbook for Systematic Reviews of Interventions,
ch.4（Searching for and selecting studies）, Section 4.4：
- 每个概念组（PICO 要素）内同义词用 OR 连接；
- 概念组之间用 AND 连接；
- 主题词（MeSH/EMTREE）与自由词（title/abstract）分别限定字段。

输入约定：调用方（API 层）负责剥除首尾空白并过滤空白词条；本模块只做
格式化，不做词表清洗（garbage in, garbage out 的纯函数语义）。
"""

from __future__ import annotations

import urllib.parse
from dataclasses import asdict, dataclass, field

__all__ = [
    "PICOElement",
    "SearchStrategy",
    "ProtocolDocument",
    "PUBMED_URL_PREFIX",
    "EMBASE_URL_PREFIX",
    "COCHRANE_URL_PREFIX",
    "generate_pubmed_query",
    "generate_pubmed_url",
    "generate_embase_query",
    "generate_cochrane_query",
    "generate_generic_query",
    "build_search_strategies",
    "protocol_to_dict",
    "protocol_from_dict",
    "protocol_to_markdown",
]


# ---------------------------------------------------------------------------
# 数据模型（规格 §1.2）
# ---------------------------------------------------------------------------


@dataclass
class PICOElement:
    """一个 PICO 要素（如 Population = "adults with type 2 diabetes"）。

    key   "population" | "intervention" | "comparator" | "outcome"
          （SPIDER 等扩展框架的自定义 key 同样被接受——生成器只看词条）
    label 显示名（"人群"）；缺省回退 key
    value 用户输入的自然语言描述（要素主词条）
    synonyms 用户提供的同义词/变体（如 ["T2DM", "NIDDM", "type 2 diabetes"]）
    mesh_terms MeSH 主题词（用户从建议列表选择）
    """

    key: str
    label: str = ""
    value: str = ""
    synonyms: list[str] = field(default_factory=list)
    mesh_terms: list[str] = field(default_factory=list)


@dataclass
class SearchStrategy:
    """一个数据库的检索式。"""

    database: str  # "pubmed" | "embase" | "cochrane" | "wos" | "generic"
    query_string: str  # 生成的检索式
    url: str  # 可点击的检索链接（仅 PubMed/Embase/Cochrane 有公开 URL 格式）
    line_count: int  # 检索式行数（用于报告）


@dataclass
class ProtocolDocument:
    """完整方案文档。"""

    title: str
    research_question: str  # 结构化研究问题
    pico: list[PICOElement]
    inclusion_criteria: list[str] = field(default_factory=list)  # 纳入标准（与 PICO 对齐）
    exclusion_criteria: list[str] = field(default_factory=list)  # 排除标准
    search_strategies: list[SearchStrategy] = field(default_factory=list)
    created_at: str = ""


# 可点击检索链接前缀（拼接即得，不代表本模块会访问它们）
PUBMED_URL_PREFIX = "https://pubmed.ncbi.nlm.nih.gov/?term="
EMBASE_URL_PREFIX = "https://www.embase.com/search#?q="
COCHRANE_URL_PREFIX = "https://www.cochranelibrary.com/search?q="


# ---------------------------------------------------------------------------
# 检索式生成（规格 §1.3）
# ---------------------------------------------------------------------------


def generate_pubmed_query(pico: list[PICOElement]) -> str:
    """PubMed 检索式生成（方法学锚：Cochrane Handbook ch.4, Section 4.4）。

    规则：
    - 每个 PICO 要素生成一个括号组：(term1[tiab] OR term2[tiab] OR "MeSH term"[Mesh])
    - 组间用 AND 连接
    - 同义词在组内用 OR 连接
    - MeSH 词用 [Mesh] 限定，自由词用 [tiab] 限定
    - 短语加双引号
    - 空（无 value/同义词/MeSH）的要素跳过——如没有 Comparator 的观察性研究
    """
    groups: list[str] = []
    for element in pico:
        if not element.value and not element.synonyms and not element.mesh_terms:
            continue  # 跳过空要素（如没有 Comparator 的观察性研究）
        terms: list[str] = []
        for term in [element.value] + (element.synonyms or []):
            if term:
                if " " in term:
                    terms.append(f'"{term}"[tiab]')
                else:
                    terms.append(f"{term}[tiab]")
        for mesh in element.mesh_terms or []:
            if mesh:
                terms.append(f'"{mesh}"[Mesh]')
        if terms:
            groups.append(f"({' OR '.join(terms)})")
    return " AND ".join(groups)


def generate_embase_query(pico: list[PICOElement]) -> str:
    """Embase 检索式（Embase 语法：``'term'/exp OR 'term':ti,ab``）。

    与 PubMed 同构的概念组结构：自由词（value+synonyms）→ ``'term':ti,ab``，
    主题词（mesh_terms，视作 EMTREE 扩展词）→ ``'term'/exp``；组内 OR、组间
    AND、空要素跳过。词条去重（保序）。
    """
    groups: list[str] = []
    for element in pico:
        if not element.value and not element.synonyms and not element.mesh_terms:
            continue
        seen: set[str] = set()
        terms: list[str] = []
        for term in [element.value] + (element.synonyms or []):
            if term and term not in seen:
                seen.add(term)
                terms.append(f"'{term}':ti,ab")
        for mesh in element.mesh_terms or []:
            if mesh and mesh not in seen:
                seen.add(mesh)
                terms.append(f"'{mesh}'/exp")
        if terms:
            groups.append(f"({' OR '.join(terms)})")
    return " AND ".join(groups)


def generate_cochrane_query(pico: list[PICOElement]) -> str:
    """Cochrane Library 检索式（与 PubMed 类似但无 [Mesh]）。

    自由词与主题词同池：短语加双引号，单词裸写，不加字段限定
    （Cochrane Library 检索框默认全字段检索，MeSH 映射由其内部完成）。
    组内 OR、组间 AND、空要素跳过、词条去重（保序）。
    """
    groups: list[str] = []
    for element in pico:
        if not element.value and not element.synonyms and not element.mesh_terms:
            continue
        seen: set[str] = set()
        terms: list[str] = []
        for term in [element.value] + (element.synonyms or []) + (element.mesh_terms or []):
            if not term or term in seen:
                continue
            seen.add(term)
            terms.append(f'"{term}"' if " " in term else term)
        if terms:
            groups.append(f"({' OR '.join(terms)})")
    return " AND ".join(groups)


def generate_generic_query(pico: list[PICOElement]) -> str:
    """通用格式检索式：不限定数据库的纯文本描述（用户手动适配）。

    每个概念组一行（``标签: 词条 OR 词条``），组间以单独一行的 AND 分隔，
    便于逐行复制到任意数据库的检索构建器。
    """
    lines: list[str] = []
    for element in pico:
        if not element.value and not element.synonyms and not element.mesh_terms:
            continue
        seen: set[str] = set()
        terms: list[str] = []
        for term in [element.value] + (element.synonyms or []) + (element.mesh_terms or []):
            if not term or term in seen:
                continue
            seen.add(term)
            terms.append(f'"{term}"' if " " in term else term)
        if terms:
            label = element.label or element.key
            lines.append(f"{label}: {' OR '.join(terms)}")
    return "\nAND\n".join(lines)


def generate_pubmed_url(query: str, date_from: str = "", date_to: str = "") -> str:
    """生成可点击的 PubMed URL（不需要 API，只是拼 URL）。

    ``date_from``/``date_to`` 为 YYYY-MM-DD；只给 ``date_from`` 时
    ``date_to`` 以空串拼入（PubMed 将其解释为开放式日期过滤）。
    """
    base = PUBMED_URL_PREFIX + urllib.parse.quote(query)
    if date_from:
        base += f"&filter=dates.{date_from}-{date_to}"
    return base


def _line_count(query: str) -> int:
    """检索式行数：换行数 + 1（单行检索式为 1）。"""
    return query.count("\n") + 1 if query else 0


def _url(prefix: str, query: str) -> str:
    """非空检索式才拼 URL；空检索式没有可点击的检索意义。"""
    return prefix + urllib.parse.quote(query) if query else ""


def build_search_strategies(
    pico: list[PICOElement], date_from: str = "", date_to: str = ""
) -> list[SearchStrategy]:
    """按四个数据库（PubMed/Embase/Cochrane/通用）生成完整检索式集合。

    Embase URL 为导航链接（``#?q=`` 片段，仅定位到检索页，不能直接执行）；
    通用格式无公开 URL 模板，``url`` 为空串。
    """
    pubmed = generate_pubmed_query(pico)
    embase = generate_embase_query(pico)
    cochrane = generate_cochrane_query(pico)
    generic = generate_generic_query(pico)
    return [
        SearchStrategy(
            database="pubmed",
            query_string=pubmed,
            url=generate_pubmed_url(pubmed, date_from, date_to) if pubmed else "",
            line_count=_line_count(pubmed),
        ),
        SearchStrategy(
            database="embase",
            query_string=embase,
            url=_url(EMBASE_URL_PREFIX, embase),
            line_count=_line_count(embase),
        ),
        SearchStrategy(
            database="cochrane",
            query_string=cochrane,
            url=_url(COCHRANE_URL_PREFIX, cochrane),
            line_count=_line_count(cochrane),
        ),
        SearchStrategy(
            database="generic",
            query_string=generic,
            url="",
            line_count=_line_count(generic),
        ),
    ]


# ---------------------------------------------------------------------------
# 序列化（task.json 存储 / API 返回 / 导出）
# ---------------------------------------------------------------------------


def protocol_to_dict(protocol: ProtocolDocument) -> dict:
    """ProtocolDocument -> 可 JSON 化的 dict（深转换，dataclass 不残留）。"""
    return asdict(protocol)


def protocol_from_dict(data: dict) -> ProtocolDocument:
    """存储的 dict -> ProtocolDocument（round-trip；形状非法抛 ValueError）。"""
    if not isinstance(data, dict):
        raise ValueError("方案数据不是对象。")
    title = data.get("title")
    if not isinstance(title, str) or not title.strip():
        raise ValueError("方案缺少标题。")
    raw_pico = data.get("pico")
    if not isinstance(raw_pico, list):
        raise ValueError("方案缺少 PICO 要素列表。")
    pico: list[PICOElement] = []
    for item in raw_pico:
        if not isinstance(item, dict):
            raise ValueError("PICO 要素格式非法。")
        key = item.get("key")
        if not isinstance(key, str) or not key:
            raise ValueError("PICO 要素缺少 key。")
        pico.append(PICOElement(
            key=key,
            label=str(item.get("label") or ""),
            value=str(item.get("value") or ""),
            synonyms=[str(s) for s in item.get("synonyms") or []],
            mesh_terms=[str(m) for m in item.get("mesh_terms") or []],
        ))
    strategies: list[SearchStrategy] = []
    for item in data.get("search_strategies") or []:
        if not isinstance(item, dict):
            raise ValueError("检索策略格式非法。")
        strategies.append(SearchStrategy(
            database=str(item.get("database") or ""),
            query_string=str(item.get("query_string") or ""),
            url=str(item.get("url") or ""),
            line_count=int(item.get("line_count") or 0),
        ))
    return ProtocolDocument(
        title=title,
        research_question=str(data.get("research_question") or ""),
        pico=pico,
        inclusion_criteria=[str(c) for c in data.get("inclusion_criteria") or []],
        exclusion_criteria=[str(c) for c in data.get("exclusion_criteria") or []],
        search_strategies=strategies,
        created_at=str(data.get("created_at") or ""),
    )


def protocol_to_markdown(protocol: ProtocolDocument | dict) -> str:
    """方案 -> Markdown（可直接粘贴到 PROSPERO 注册表单）。

    接受 ProtocolDocument 或已存储的 dict（导出端点直接读 task.json）。
    """
    doc = protocol if isinstance(protocol, ProtocolDocument) else protocol_from_dict(protocol)
    lines: list[str] = [f"# {doc.title}", ""]
    if doc.research_question:
        lines += ["## 研究问题", "", doc.research_question, ""]
    if doc.pico:
        lines += ["## PICO 要素", "",
                  "| 要素 | 描述 | 同义词 | MeSH 主题词 |",
                  "| --- | --- | --- | --- |"]
        for element in doc.pico:
            label = f"{element.label or element.key}（{element.key}）"
            lines.append("| {} | {} | {} | {} |".format(
                label.replace("|", "\\|"),
                element.value.replace("|", "\\|"),
                "; ".join(s.replace("|", "\\|") for s in element.synonyms),
                "; ".join(m.replace("|", "\\|") for m in element.mesh_terms),
            ))
        lines.append("")
    if doc.search_strategies:
        lines += ["## 检索策略", ""]
        for strategy in doc.search_strategies:
            name = strategy.database.capitalize() if strategy.database else "未知数据库"
            lines += [f"### {name}", "", "```text", strategy.query_string, "```", ""]
            if strategy.url:
                lines += [f"[在 {name} 中打开]({strategy.url})", ""]
    if doc.inclusion_criteria:
        lines += ["## 纳入标准", ""] + [f"- {c}" for c in doc.inclusion_criteria] + [""]
    if doc.exclusion_criteria:
        lines += ["## 排除标准", ""] + [f"- {c}" for c in doc.exclusion_criteria] + [""]
    if doc.created_at:
        lines += ["---", f"创建时间：{doc.created_at}", ""]
    return "\n".join(lines).strip() + "\n"
