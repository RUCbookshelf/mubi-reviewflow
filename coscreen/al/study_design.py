"""预置研究设计分类器（W4 · B3 空白格泳道；2.1.4-r8 · 2.1.5 泳道 D3 扩类）。

对单篇题录做**研究设计**的精确优先启发式分类，供初筛卡片的分类徽标与
排除理由预填（预填不代存）使用。**纯本地、零依赖、零副作用**：不连库、
不联网、不落盘；除标准库 ``json`` 外零导入（临床七类规则直接复用
:mod:`coscreen.evidence_mapper` 的 ``_STUDY_DESIGN_RULES``，不复制粘贴）。

信号与加权（两路信号，先主后辅）：

1. **主信号 = 标题 + 摘要**：先逐条复用
   ``evidence_mapper._STUDY_DESIGN_RULES`` 的有序正则
   （meta→sysrev→rct→cohort→cc→cross→diagnostic，先命中先归类，
   特异性优先——证据合成类 > 试验类 > 观察类），**全部未命中后**再跑本模块
   的 :data:`_EXTENDED_DESIGN_RULES`（教育/社科四类：mixed_methods→
   discourse→qualitative→survey）。新类别整体排在临床七类之后，因此临床
   语料判定**逐位不变**（W4 的 48 篇 PubMed 夹具实测零变化），扩类只接管
   原本归 ``other`` 的题录。
2. **辅信号 = raw_json 的出版类型类字段**（RIS 的 ``unknown_tag['PT'/'TY']``、
   rispy 的 ``type_of_reference``、CSV 的 ``publication_type(s)``/``pubtype(s)``
   列等）：仅当主信号未命中（``other``）时才扫描，命中即加权采纳（
   ``source="raw_json"``）。主信号永远优先——题名里写了 meta-analysis 的
   记录不会被 PT 里的 "Randomized Controlled Trial" 抢走（PubMed 会给
   纳入 RCT 的 Meta 分析同时挂两个 PT，正是这种记录需要主信号压轴）。

都未命中 → ``other``（宁缺毋滥，不臆测——与 evidence_mapper 的
``_rule_label`` 语义一致）。

返回值是纯 JSON 可序列化 dict（API 层直接回显；前端徽标/预填只读
``label`` / ``recognized`` / ``suggest_exclusion_reason``）::

    {
      "zotero_key": "PMID-123",     # 入参带回（缺失时空串）
      "label": "rct",               # 规则稳定代码（12 取值 = 十一类设计 + other）
      "recognized": True,           # label != "other"
      "is_rct": True,               # 等价于 label == "rct"
      "source": "title_abstract",   # 主信号 | "raw_json" | None（未识别）
      "matched_keyword": "randomised controlled trial",  # 命中的原文片段
      "suggest_exclusion_reason": null,  # 识别为非 RCT 设计时为
                                  #   "研究设计不符"（与初筛下拉选项值同串），
                                  #   供前端**预填不代存**；其余为 None
      "rule_version": 2
    }

精确优先的边界（诚实披露，详见 ``docs/methodology/study_design_classifier.md``）：
只认明确关键词组合；摘要未提设计词的记录依赖 raw 出版类型兜底，二者皆无则
归 ``other``；对 ``other`` **不**给出排除建议（未识别 ≠ 不符合）。
评估夹具（PubMed 公开样例 48 条）与实测 precision/recall 见方法学文档。

与 :mod:`coscreen.evidence_mapper` 的**显式边界**（2.1.5 泳道 D3）：
证据地图的 ``study_design`` 维度服务 GRADE 与研究缺口分析，保持**临床七类**
口径（``_STUDY_DESIGN_RULES`` 一字未改）；教育/社科四类只在本模块内追加，
用于初筛徽标与文献评价的「按设计推荐」。两处因此**不再逐字同一套规则**：
临床七类的规则对象仍共用（同一个 tuple，无镜像实现），新增部分是本模块私有的
``_EXTENDED_DESIGN_RULES``。改动任一处的临床规则仍会同时影响两边；扩类不会
影响证据地图——这一边界由 ``build/tests/test_2_1_5_study_design_ext.py`` 的
``ClinicalMapperBoundaryNails`` 钉死。
"""

from __future__ import annotations

import json
import re
from typing import Any, Mapping

from coscreen.evidence_mapper import OTHER_LABEL, _STUDY_DESIGN_RULES

__all__ = [
    "CLINICAL_LABELS",
    "DESIGN_LABELS",
    "EXTENDED_LABELS",
    "RCT_LABEL",
    "RULE_VERSION",
    "SUGGESTED_EXCLUSION_REASON",
    "classify_article",
    "classify_corpus",
    "label_is_rct",
    "suggest_exclusion_reason",
]

#: 规则版本：判定因子（规则本体/加权策略）变化时必须 bump，
#: 前端徽标与文档披露按此对齐。2 = 七类 → 十一类（2.1.5 泳道 D3 扩类；
#: 任务书写作「十类」，按列出的四类新增与既有七类相加实为十一类）。
RULE_VERSION: int = 2

#: 随机对照试验的稳定代码（与 evidence_mapper 标签族一致）。
RCT_LABEL: str = "rct"

#: 初筛排除理由下拉中「研究设计不符」的选项**值**（index.html 初筛卡固定
#: 选项表）。识别为非 RCT 设计时作为建议值返回；前端只预填控件、绝不代存。
SUGGESTED_EXCLUSION_REASON: str = "研究设计不符"

#: 教育/社科口径的研究设计类别（2.1.5 泳道 D3）。**整体排在临床七类之后**
#: ——临床语料判定逐位不变（W4 的 48 篇 PubMed 夹具实测零变化），新类别只
#: 接管原本归 ``other`` 的题录。规则风格与 ``_STUDY_DESIGN_RULES`` 一致：
#: 只认明确的设计词/方法词，词组内部用 ``[-\s]?`` 容忍连字符与空格变体，
#: 词界 ``\b`` 阻断裂变（``qualitatively``、``surveyed`` 不算命中）。
#:
#: 组内顺序 = 特异性：混合方法（同时会提到 qualitative/survey）>
#: 话语/语料分析（方法名比「质性」更具体）> 质性 > 调查（最泛的名词）。
_EXTENDED_DESIGN_RULES: tuple[tuple[str, tuple[re.Pattern[str], ...]], ...] = (
    ("mixed_methods", (
        re.compile(r"\bmixed[-\s]?methods?\b", re.IGNORECASE),
    )),
    ("discourse", (
        re.compile(r"\bdiscourse\s+analys(?:is|es)\b", re.IGNORECASE),
        re.compile(r"\bconversation\s+analys(?:is|es)\b", re.IGNORECASE),
        re.compile(r"\bcorpus[-\s]?based\b", re.IGNORECASE),
    )),
    ("qualitative", (
        re.compile(r"\bqualitative\b", re.IGNORECASE),
        re.compile(r"\bnarrative\s+inquiry\b", re.IGNORECASE),
        re.compile(r"\bnarrative\s+analysis\b", re.IGNORECASE),
        re.compile(r"\bethnograph\w*\b", re.IGNORECASE),
        re.compile(r"\bphenomenolog\w*\b", re.IGNORECASE),
        re.compile(r"\bcase\s+stud(?:y|ies)\b", re.IGNORECASE),
        re.compile(r"\bgrounded\s+theory\b", re.IGNORECASE),
        re.compile(r"\binterview\s+stud(?:y|ies)\b", re.IGNORECASE),
    )),
    ("survey", (
        # 「survey of the literature/evidence/trials」是叙述性综述的习惯说法，
        # 不是调查研究——负向环视把它挡在门外（宁缺毋滥）。
        re.compile(
            r"\bsurveys?\b(?!\s+(?:of\s+)?(?:the\s+)?(?:literature|evidence|trials)\b)",
            re.IGNORECASE),
        re.compile(r"\bquestionnaires?\b", re.IGNORECASE),
    )),
)

#: 主信号/兜底信号的完整规则序列：临床七类（与 evidence_mapper 共用同一
#: tuple 对象）→ 本模块的教育/社科四类。顺序即优先级，先命中先归类。
_CLASSIFICATION_RULES: tuple[tuple[str, tuple[re.Pattern[str], ...]], ...] = (
    _STUDY_DESIGN_RULES + _EXTENDED_DESIGN_RULES
)

#: 临床七类的稳定代码（顺序与 evidence_mapper 一致）。
CLINICAL_LABELS: tuple[str, ...] = tuple(label for label, _ in _STUDY_DESIGN_RULES)

#: 2.1.5 新增的教育/社科四类稳定代码（顺序即优先级）。
EXTENDED_LABELS: tuple[str, ...] = tuple(label for label, _ in _EXTENDED_DESIGN_RULES)

#: 十一类设计 + 未识别的全部稳定代码（前端徽标/i18n 与契约文档按此对齐）。
DESIGN_LABELS: tuple[str, ...] = CLINICAL_LABELS + EXTENDED_LABELS + (OTHER_LABEL,)

#: raw 里视作「出版类型类」的字段名（小写精确匹配）。rispy 0.10 把 TY 映射为
#: ``type_of_reference``、把未知标签（如 PubMed RIS 的 PT）收进 ``unknown_tag``
#: （{标签: [值, ...]}）；CSV 导入按列名直存。三种形态都收。
_RAW_TYPE_KEYS: frozenset[str] = frozenset({
    "pt", "ty",
    "publication_type", "publication_types",
    "pubtype", "pubtypes",
    "type_of_reference",
})

#: ``unknown_tag`` 里视作类型标签的 RIS 标签（大写精确匹配）。
_RAW_TYPE_TAGS: frozenset[str] = frozenset({"PT", "TY"})


def _rule_hit(text: str) -> tuple[str, str] | None:
    """按 :data:`_CLASSIFICATION_RULES` 优先级在一段文本中找首个命中。

    临床七类在前、教育/社科四类在后（临床语料上判定不变），返回
    ``(label, 命中的原文片段)``；都不中返回 ``None``（不臆测）。
    """
    for label, patterns in _CLASSIFICATION_RULES:
        for pattern in patterns:
            match = pattern.search(text)
            if match:
                return label, match.group(0)
    return None


def _scalar_texts(value: Any) -> list[str]:
    """把 raw 字段值（str/list/dict/标量）摊平为字符串列表。"""
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, Mapping):
        return [text for item in value.values() for text in _scalar_texts(item)]
    if isinstance(value, (list, tuple, set)):
        return [text for item in value for text in _scalar_texts(item)]
    return [str(value)]


def _raw_type_texts(raw: object) -> list[str]:
    """raw_json 中的出版类型类文本（rispy unknown_tag['PT'/'TY'] + 具名字段）。"""
    if not isinstance(raw, Mapping):
        return []
    texts: list[str] = []
    unknown = raw.get("unknown_tag")
    if isinstance(unknown, Mapping):
        for tag, value in unknown.items():
            if str(tag).upper() in _RAW_TYPE_TAGS:
                texts.extend(_scalar_texts(value))
    for key, value in raw.items():
        if str(key).lower() in _RAW_TYPE_KEYS:
            texts.extend(_scalar_texts(value))
    return texts


def label_is_rct(label: object) -> bool:
    """稳定代码是否为随机对照试验（容错任意输入）。"""
    return label == RCT_LABEL


def suggest_exclusion_reason(label: str) -> str | None:
    """「识别为非 RCT 的具体设计」→ 建议排除理由；RCT 与未识别 → None。

    未识别（``other``）**不**给建议：宁缺毋滥，未识别 ≠ 研究设计不符。
    """
    if label != OTHER_LABEL and not label_is_rct(label):
        return SUGGESTED_EXCLUSION_REASON
    return None


def classify_article(article: Mapping[str, Any]) -> dict:
    """单篇题录 → 研究设计分类结果（纯函数，见模块 docstring 的返回形状）。

    参数：题录 dict（``title``/``abstract``/``raw``（dict）或 ``raw_json``
    （JSON 字符串），键缺失或形态异常一律按空值降级，绝不抛错；
    ``zotero_key`` 仅原样带回）。

    Raises:
        ValueError: ``article`` 不是 dict/Mapping（调用方契约错误，
            与 ``evidence_mapper.generate_evidence_map`` 对非 dict 输入同口径）。
    """
    if not isinstance(article, Mapping):
        raise ValueError(f"article 必须是 dict（title/abstract/raw...），收到 {type(article).__name__}")

    title = str(article.get("title") or "")
    abstract = str(article.get("abstract") or "")
    primary = _rule_hit(f"{title} {abstract}")

    label: str = OTHER_LABEL
    source: str | None = None
    matched: str | None = None
    if primary:
        label, matched = primary
        source = "title_abstract"
    else:
        raw = article.get("raw")
        if not isinstance(raw, Mapping):
            raw_json = article.get("raw_json")
            if isinstance(raw_json, str) and raw_json.strip():
                try:
                    parsed = json.loads(raw_json)
                except ValueError:
                    parsed = None
                raw = parsed if isinstance(parsed, Mapping) else {}
        fallback = _rule_hit(" ; ".join(_raw_type_texts(raw)))
        if fallback:
            label, matched = fallback
            source = "raw_json"

    return {
        "zotero_key": str(article.get("zotero_key") or ""),
        "label": label,
        "recognized": label != OTHER_LABEL,
        "is_rct": label_is_rct(label),
        "source": source,
        "matched_keyword": matched,
        "suggest_exclusion_reason": suggest_exclusion_reason(label),
        "rule_version": RULE_VERSION,
    }


def classify_corpus(articles: list[Mapping[str, Any]]) -> dict[str, dict]:
    """批量分类 → ``{zotero_key: 结果}``（重复键后写者胜；键为空串的跳过）。

    纯本地循环，无全局状态；API 端点与评估夹具共用本入口，保证线上判定
    与文档披露的 precision/recall 用的是同一条代码路径。
    """
    items: dict[str, dict] = {}
    for article in articles:
        result = classify_article(article)
        if result["zotero_key"]:
            items[result["zotero_key"]] = result
    return items
