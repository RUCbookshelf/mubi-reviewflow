"""预置研究设计分类器（W4 · B3 空白格泳道；2.1.4-r8）。

对单篇题录做**研究设计**的精确优先启发式分类，供初筛卡片的分类徽标与
排除理由预填（预填不代存）使用。**纯本地、零依赖、零副作用**：不连库、
不联网、不落盘；除标准库 ``json`` 外零导入（规则本体复用
:mod:`coscreen.evidence_mapper`，不复制粘贴）。

信号与加权（两路信号，先主后辅）：

1. **主信号 = 标题 + 摘要**：逐条复用 ``evidence_mapper._STUDY_DESIGN_RULES``
   的有序正则（meta→sysrev→rct→cohort→cc→cross→diagnostic，先命中先归类，
   特异性优先——证据合成类 > 试验类 > 观察类）。与证据地图的 study_design
   维度**完全同一套规则**，两处判定永不漂移。
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
      "label": "rct",               # 规则稳定代码（8 取值）
      "recognized": True,           # label != "other"
      "is_rct": True,               # 等价于 label == "rct"
      "source": "title_abstract",   # 主信号 | "raw_json" | None（未识别）
      "matched_keyword": "randomised controlled trial",  # 命中的原文片段
      "suggest_exclusion_reason": null,  # 识别为非 RCT 设计时为
                                  #   "研究设计不符"（与初筛下拉选项值同串），
                                  #   供前端**预填不代存**；其余为 None
      "rule_version": 1
    }

精确优先的边界（诚实披露，详见 ``docs/methodology/study_design_classifier.md``）：
只认明确关键词组合；摘要未提设计词的记录依赖 raw 出版类型兜底，二者皆无则
归 ``other``；对 ``other`` **不**给出排除建议（未识别 ≠ 不符合）。
评估夹具（PubMed 公开样例 48 条）与实测 precision/recall 见方法学文档。
"""

from __future__ import annotations

import json
from typing import Any, Mapping

from coscreen.evidence_mapper import OTHER_LABEL, _STUDY_DESIGN_RULES

__all__ = [
    "RCT_LABEL",
    "RULE_VERSION",
    "SUGGESTED_EXCLUSION_REASON",
    "classify_article",
    "classify_corpus",
    "label_is_rct",
    "suggest_exclusion_reason",
]

#: 规则版本：判定因子（规则本体/加权策略）变化时必须 bump，
#: 前端徽标与文档披露按此对齐。
RULE_VERSION: int = 1

#: 随机对照试验的稳定代码（与 evidence_mapper 标签族一致）。
RCT_LABEL: str = "rct"

#: 初筛排除理由下拉中「研究设计不符」的选项**值**（index.html 初筛卡固定
#: 选项表）。识别为非 RCT 设计时作为建议值返回；前端只预填控件、绝不代存。
SUGGESTED_EXCLUSION_REASON: str = "研究设计不符"

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
    """按 ``_STUDY_DESIGN_RULES`` 优先级在一段文本中找首个命中。

    返回 ``(label, 命中的原文片段)``；都不中返回 ``None``（不臆测）。
    """
    for label, patterns in _STUDY_DESIGN_RULES:
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
