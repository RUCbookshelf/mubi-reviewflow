"""W4 预置分类器评估夹具抓取脚本（一次性取数，产物入库；重跑生成**同构**清单）。

复现口径（如实声明）：重跑**不能**逐字重现同一批 PMID——PubMed esearch 的
结果集随时间漂移（新一轮重取时旧夹具的 PMID-42957174/42957497 已不在结果
集中）。可复现的是**构造过程**：同样的 8 个类别检索式、同样的每类 6 篇、
同样的跨类 PMID 去重与 400 字符截断。数值结论由
``build/tests/test_w4_study_design.py`` 锁定在**已入库的这份夹具**上，
因此文档数字稳定；换了夹具就必须同步重算（钉子会变红）。

从 PubMed E-utilities（公开端点）按类别各取少量真实公开题录，落盘为
``build/tests/study_design_fixture.json``，供
``build/tests/test_w4_study_design.py`` 的 precision/recall 评估钉子与
``docs/methodology/study_design_classifier.md`` 的诚实披露引用。

治理口径（与产品出站先例一致）：stdlib urllib、显式 UA、短超时、
读取上限、**拒绝跟随 3xx 重定向**（``_NoRedirectHandler``——同
``custom_backend/ai_suggest_api.py`` / ``pdf_fetch_api.py`` /
``living_review_plugin.py`` 先例）；超时用**局部** ``urlopen(timeout=)``，
不用 ``socket.setdefaulttimeout``（不改进程级全局状态）。只读公开
元数据/摘要；**摘要截断至 400 字符**入库（关键词分类只需题名+摘要开头，
降低全文再分发面）；题录原文版权归原作者，仅作测试夹具随库分发（见
docs/methodology/study_design_classifier.md §3）。

**跨类去重**：同一 PMID 可能同时命中两个类别的检索式（本夹具实测有 2 篇
cohort 与 cross_sectional 的双重命中）。脚本按「先到先得」全局去重——
每类先多取 ``PER_CLASS + OVERFETCH`` 条候选，去重后取前 ``PER_CLASS`` 条，
因此夹具是**独立文献**（48 条 = 8 类 × 6 篇互不重复），docs §4/§5 的
逐记录指标与 zotero_key 键一一对应，不再出现「46 键 / 48 条」的口径分歧。

用法（在仓库根目录）::

    python build/scripts/fetch_study_design_fixture.py
"""

from __future__ import annotations

import json
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

#: 每类最终入库条数。
PER_CLASS = 6

#: 每类多取的候选条数（跨类去重后仍有足够余量补齐 PER_CLASS）。
OVERFETCH = 6

#: 摘要入库截断长度（字符）。
ABSTRACT_LIMIT = 400

#: 单响应读取上限（字节）——与产品出站先例同口径的防御上限。
READ_LIMIT = 2_000_000

#: 单请求超时（秒）；局部传入 urlopen，不用 socket.setdefaulttimeout。
TIMEOUT_SECONDS = 20.0

#: 类别 → E-classes 检索式。金标 = 检索类别本身（选择偏差在文档中如实
#: 披露：类别词与研究设计关键词同源，precision/recall 度量的是「规则对
#: 典型表述的覆盖」，不是真实筛选流的判别力；负类取病例报告提供少量
#: 反例信号）。
QUERIES: dict[str, str] = {
    "rct": "randomized controlled trial[Publication Type]",
    "systematic_review": "systematic review[Publication Type] NOT meta-analysis[Publication Type]",
    "meta_analysis": "meta-analysis[Publication Type]",
    "cohort": "cohort[Title/Abstract]",
    "case_control": "case-control[Title/Abstract]",
    "cross_sectional": "cross-sectional[Title/Abstract]",
    "diagnostic": "(diagnostic accuracy[Title/Abstract]) OR (sensitivity and specificity[Title/Abstract])",
    "other": "case reports[Publication Type]",
}

BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
HEADERS = {"User-Agent": "ReviewFlow-study-design-fixture/2.1.4 (test data fetch; contact: repo)"}


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """拒绝跟随 3xx（出站红线；同 ai_suggest_api._NoRedirectHandler 先例）。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        return None


_OPENER = urllib.request.build_opener(_NoRedirectHandler)


def _get(url: str) -> bytes:
    request = urllib.request.Request(url, headers=HEADERS)
    with _OPENER.open(request, timeout=TIMEOUT_SECONDS) as response:  # noqa: S310 - 固定 https 白名单端点
        return response.read(READ_LIMIT)


def _text_of(node: ET.Element | None) -> str:
    return " ".join("".join(node.itertext()).split()) if node is not None else ""


def fetch_candidates(label: str, query: str) -> list[dict]:
    """取该类的候选记录（顺序 = PubMed 相关性序，去重要保留先到先得）。"""
    search = json.loads(_get(
        f"{BASE}/esearch.fcgi?db=pubmed&retmode=json&retmax={PER_CLASS + OVERFETCH}&term="
        + urllib.parse.quote(query)))
    ids = search["esearchresult"]["idlist"]
    if not ids:
        raise RuntimeError(f"{label}: esearch returned no ids for {query!r}")
    xml_text = _get(f"{BASE}/efetch.fcgi?db=pubmed&retmode=xml&id=" + ",".join(ids))
    root = ET.fromstring(xml_text)
    records: list[dict] = []
    for article in root.iter("PubmedArticle"):
        citation = article.find("MedlineCitation")
        if citation is None:
            continue
        pmid = _text_of(citation.find("PMID"))
        base = citation.find("Article")
        if base is None or not pmid:
            continue
        title = _text_of(base.find("ArticleTitle"))
        abstract_parts = [
            _text_of(node) for node in (base.find("Abstract") or ET.Element("x")).findall("AbstractText")
        ]
        abstract = " ".join(part for part in abstract_parts if part)[:ABSTRACT_LIMIT]
        pub_types = [
            _text_of(node) for node in
            (base.find("PublicationTypeList") or ET.Element("x")).findall("PublicationType")
            if _text_of(node)
        ]
        year_node = base.find("./Journal/JournalIssue/PubDate/Year")
        records.append({
            "zotero_key": f"PMID-{pmid}",
            "title": title,
            "abstract": abstract,
            # raw 镜像 PubMed RIS 导入形态：PT 标签列表（rispy 未知标签原样保留）。
            "raw": {"PT": pub_types},
            "gold": label,
            "pubmed_publication_types": pub_types,
            "year": int(_text_of(year_node)) if _text_of(year_node).isdigit() else None,
            "abstract_truncated_to": ABSTRACT_LIMIT,
        })
    if not records:
        raise RuntimeError(f"{label}: efetch returned no parsable records")
    return records


def main() -> int:
    items: list[dict] = []
    seen: set[str] = set()
    skipped_cross_class = 0
    for label, query in QUERIES.items():
        accepted = 0
        for record in fetch_candidates(label, query):
            key = record["zotero_key"]
            if key in seen:                     # 跨类 / 类内重复：先到先得
                skipped_cross_class += 1
                continue
            seen.add(key)
            items.append(record)
            accepted += 1
            if accepted == PER_CLASS:
                break
        if accepted < PER_CLASS:
            raise RuntimeError(f"{label}: only {accepted}/{PER_CLASS} unique records after dedupe")
        print(f"{label}: {accepted} unique records")
    out = {
        "meta": {
            "source": "PubMed E-utilities (https://eutils.ncbi.nlm.nih.gov), esearch+efetch",
            "fetched_on": "2026-10-30",
            "per_class": PER_CLASS,
            "queries": QUERIES,
            "dedupe": "同一 PMID 跨类/类内重复按先到先得保留一次；夹具为独立文献",
            "duplicates_skipped": skipped_cross_class,
            "gold": "检索类别即金标（选择偏差与方法学限制见 docs/methodology/study_design_classifier.md）",
            "abstract_truncated_to": ABSTRACT_LIMIT,
            "license_note": "题录与摘要片段版权归原作者/PubMed；仅作测试夹具随库分发，不作商业用途。",
        },
        "items": items,
    }
    path = Path(__file__).resolve().parents[1] / "tests" / "study_design_fixture.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"wrote {path} ({len(items)} unique records, {skipped_cross_class} duplicates skipped)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
