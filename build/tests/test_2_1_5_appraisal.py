# -*- coding: utf-8 -*-
"""2.1.5 Wave B2 · 文献评价后端核心钉子（专属新文件，撤实现必红）。

覆盖：
1. 注册表完整性：六类工具逐档案对数（条目数 / 编号问题数），条目原文
   **逐字**出现在权威查证档案中；许可结论与 link_only 清单。
2. RoB 2 官方算法：至少 6 个组合用例（含跨域升级与人工上调用例）。
3. AMSTAR 2 Box 2 四级判定 + 未做 meta 分析豁免。
4. CASP / MMAT 不出分的负钉：聚合输出不含任何分数字段。
5. 推荐映射：研究设计 → 工具（含补充识别的质性/混合方法）。
6. 存储往返 + 幂等迁移（个人库 {slug}.db 的新表可加、旧版忽略）。
7. PUT 未在册 tool / item / option → 400；link_only 工具不可写。

档案路径：E:/mubi-reviewflow/out/appraisal_tools/dossiers/（逐字抄录的
唯一权威来源）。
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
fixture = tempfile.TemporaryDirectory(prefix="rf_215b_appraisal_")
os.environ["COBOOKSHELF_DATA_DIR"] = fixture.name
os.environ["REVIEWFLOW_MODE"] = "server"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from coscreen import appraisal as core  # noqa: E402
from coscreen import db as db_mod  # noqa: E402
from coscreen.models import Article  # noqa: E402
from custom_backend import auth as auth_mod  # noqa: E402
from custom_backend.main import app  # noqa: E402

DOSSIERS = Path("E:/mubi-reviewflow/out/appraisal_tools/dossiers")


# ---------------------------------------------------------------------------
# 1. 注册表完整性（逐档案对数 + 逐字抄录）
# ---------------------------------------------------------------------------

#: (工具 id, 档案文件, 应答条目数, 编号问题数)
#: 条目数按档案中**可独立作答的答题框**计（子问 a/b/c 各自作答）；
#: 编号问题数按档案的编号题计（4a/4b/4c 归入第 4 题）。
DOSSIER_COUNTS = [
    # casp-qualitative.md §1：Section A 1–6、B 7–9、C 10 → 10 条。
    ("casp-qualitative", "casp-qualitative.md", 10, 10),
    # casp-rct.md §1：11 题，第 4 题有 (a)(b)(c) 三个独立作答框 → 13。
    ("casp-rct", "casp-rct.md", 13, 11),
    # casp-systematic-review.md §1：A 1–5、B 6–7、C 8–10 → 10 条。
    ("casp-systematic-review", "casp-systematic-review.md", 10, 10),
    # casp-case-control.md §1：11 题，第 6 题 a/b 两个子问 → 12。
    ("casp-case-control", "casp-case-control.md", 12, 11),
    # casp-clinical-prediction-rule.md §1：A 1–6、B 7–8、C 9–11 → 11 条。
    ("casp-clinical-prediction-rule", "casp-clinical-prediction-rule.md", 11, 11),
    # casp-cohort.md §1：12 题，第 5 题 a/b、第 6 题 a/b → 14。
    ("casp-cohort", "casp-cohort.md", 14, 12),
    # casp-cross-sectional.md §1：单一 Section A 下 11 条。
    ("casp-cross-sectional", "casp-cross-sectional.md", 11, 11),
    # casp-diagnostic.md §1：A 1–6、B 7–8、C 9–12 → 12 条。
    ("casp-diagnostic", "casp-diagnostic.md", 12, 12),
    # casp-economic-evaluation.md §1：A 1–4、B 5–9、C 10–12 → 12 条。
    ("casp-economic-evaluation", "casp-economic-evaluation.md", 12, 12),
    # casp-systematic-review-meta-rct.md §1：10 题，3a/3b/3c、5a/5b、
    # 6.1/6.2、7.1/7.2 为子条目 → 15 个作答框。
    ("casp-systematic-review-meta-rct", "casp-systematic-review-meta-rct.md", 15, 10),
    ("casp-systematic-review-meta-observational",
     "casp-systematic-review-meta-observational.md", 15, 10),
    # RoB2.md §1.3–1.7：Domain 1（3）+ Domain 2 变体 A（7）+ Domain 3（4）
    # + Domain 4（5）+ Domain 5（3）= 22 个信号问题（变体 B 的 6 条另存）。
    ("rob2", "RoB2.md", 22, 22),
    # MMAT.md §1：S1/S2 + 5 类 × 5 = 27。
    ("mmat", "MMAT.md", 27, 27),
    # AXIS.md §1：Table 2 的 20 条。
    ("axis", "AXIS.md", 20, 20),
    # AMSTAR2.md §1.3：16 条。
    ("amstar2", "AMSTAR2.md", 16, 16),
    # CERQual.md §1：四个构件。
    ("cerqual", "CERQual.md", 4, 4),
]


class TestRegistry:
    """混入类：注册表完整性（不依赖 TestClient）。"""

    def test_six_families_and_tool_count(self):
        assert len(core.REGISTRY) == 16
        families: dict[str, int] = {}
        for tool in core.REGISTRY.values():
            families[tool["family"]] = families.get(tool["family"], 0) + 1
        assert families["CASP"] == 11, "CASP 全系 11 份清单"
        assert families["RoB 2"] == 1
        assert families["MMAT"] == 1
        assert families["AXIS"] == 1
        assert families["AMSTAR 2"] == 1
        assert families["GRADE-CERQual"] == 1

    @pytest.mark.parametrize("tool_id,filename,items,questions", DOSSIER_COUNTS)
    def test_item_counts_match_dossier(self, tool_id, filename, items, questions):
        tool = core.REGISTRY[tool_id]
        assert len(list(core.iter_items(tool))) == items, tool_id
        summary = next(row for row in core.tool_summaries() if row["id"] == tool_id)
        assert summary["item_count"] == items
        assert summary["question_count"] == questions

    @pytest.mark.parametrize("tool_id,filename,items,questions", DOSSIER_COUNTS)
    def test_item_text_is_verbatim_from_dossier(self, tool_id, filename, items, questions):
        """逐字抄录钉：每条条目原文必须是档案原文的子串。"""
        dossier = DOSSIERS / filename
        assert dossier.is_file(), f"权威档案缺失：{dossier}"
        text = dossier.read_text(encoding="utf-8")
        for item in core.iter_items(core.REGISTRY[tool_id]):
            source = item.get("source_text") or item["text"]
            assert source in text, (
                f"{tool_id} 条目 {item['id']} 未逐字出现在 {filename}：{source!r}")
            parent = item.get("parent_text")
            if parent:
                assert parent in text, (
                    f"{tool_id} 条目 {item['id']} 的父问题未逐字出现在 {filename}")

    def test_rob2_algorithm_text_is_verbatim(self):
        """RoB 2 为 CC BY-NC-ND：算法表必须逐字，禁改写。"""
        text = (DOSSIERS / "RoB2.md").read_text(encoding="utf-8")
        tool = core.REGISTRY["rob2"]
        for domain in tool["domains"]:
            for value in domain.get("algorithm_text", {}).values():
                for phrase in _strings(value):
                    assert phrase in text, (domain["id"], phrase[:80])
            for variant in domain.get("variants", {}).values():
                for value in variant["algorithm_text"].values():
                    for phrase in _strings(value):
                        assert phrase in text, (domain["id"], phrase[:80])
        for key in ("low", "some_concerns", "high", "source"):
            assert tool["overall_algorithm_text"][key] in text, key

    def test_amstar2_box_text_is_verbatim(self):
        text = (DOSSIERS / "AMSTAR2.md").read_text(encoding="utf-8")
        tool = core.REGISTRY["amstar2"]
        for phrase in tool["box1_text"]:
            assert phrase in text, phrase
        for value in tool["box2_text"].values():
            for phrase in _strings(value):
                assert phrase in text, phrase[:80]

    def test_casp_notes_are_verbatim(self):
        """CASP 的「不打总分」FAQ 说明按原文展示，不改写。"""
        text = (DOSSIERS / "casp-qualitative.md").read_text(encoding="utf-8")
        for note in core.REGISTRY["casp-qualitative"]["aggregation_notes"]:
            assert note in text, note

    def test_cerqual_official_text_is_verbatim(self):
        text = (DOSSIERS / "CERQual.md").read_text(encoding="utf-8")
        tool = core.REGISTRY["cerqual"]
        assert tool["official_overall_text"] in text
        assert tool["rating_down_guidance"] in text
        for template in tool["response_option_templates"].values():
            prefix = template.split("{component}")[0].strip()
            assert prefix in text, prefix

    def test_licenses_are_embeddable(self):
        expected = {
            "casp-qualitative": "CC-BY-NC-SA-4.0",
            "casp-rct": "CC-BY-NC-SA-4.0",
            "rob2": "CC-BY-NC-ND-4.0",
            "mmat": "MMAT-COPYRIGHT-1148552",
            "axis": "CC-BY-NC-4.0",
            "amstar2": "CC-BY-4.0",
            "cerqual": "CC-BY-4.0",
        }
        for tool_id, license_id in expected.items():
            license_ = core.REGISTRY[tool_id]["license"]
            assert license_["id"] == license_id
            assert license_["embed"].startswith("allowed")
            assert license_["notice"].strip()
        # RoB 2 为 ND：不得改写条目（注册表只存官方原文与算法说明）。
        assert core.REGISTRY["rob2"]["license"]["no_derivatives"] is True
        # 每个工具都带官方引用串与 BibTeX（契约要求随界面显示）。
        for tool in core.REGISTRY.values():
            assert tool["citation"].strip()
            assert tool["version"].strip()
            assert tool["bibtex"].startswith("@")
            assert "{" in tool["bibtex"] and tool["bibtex"].endswith("}")

    def test_link_only_tools_are_not_embedded(self):
        ids = {entry["id"] for entry in core.LINK_ONLY}
        for required in ("jbi-qualitative", "jbi-rct", "jbi-cross-sectional",
                         "nos", "robis", "quadas-2", "probast", "ephpp"):
            assert required in ids, required
        assert not (ids & set(core.REGISTRY)), "link_only 不得混入注册表"
        for entry in core.LINK_ONLY:
            assert entry["url"].startswith("https://")
            assert entry["reason_key"]
            assert "items" not in entry and "text" not in entry

    def test_aggregation_modes(self):
        modes = {tool["aggregation"] for tool in core.REGISTRY.values()}
        assert modes == {"no_score", "rob2_domains", "amstar2_confidence",
                         "cerqual_confidence"}

    def test_comments_supported_matches_official_instrument(self):
        """CASP 要求记录理由、MMAT 有 Comments 列、AXIS 选项含 comment。"""
        by_id = {row["id"]: row for row in core.tool_summaries()}
        for tool_id in ("casp-qualitative", "casp-rct", "mmat", "axis"):
            assert by_id[tool_id]["comments_supported"] is True, tool_id
        assert by_id["rob2"]["comments_supported"] is False


# ---------------------------------------------------------------------------
# 2. RoB 2 官方算法钉（≥6 组合，含跨域升级）
# ---------------------------------------------------------------------------

ROB2_LOW = {
    "1.1": "Y", "1.2": "Y", "1.3": "N",
    "2.1": "N", "2.2": "N", "2.3": "NA", "2.4": "NA", "2.5": "NA",
    "2.6": "Y", "2.7": "NA",
    "3.1": "Y", "3.2": "NA", "3.3": "N", "3.4": "N",
    "4.1": "N", "4.2": "N", "4.3": "N", "4.4": "N", "4.5": "N",
    "5.1": "Y", "5.2": "N", "5.3": "N",
}


def _rob2(**overrides):
    responses = dict(ROB2_LOW)
    responses.update(overrides)
    return core.aggregate("rob2", responses)


class TestRob2Algorithm:
    def test_all_low_is_low(self):
        result = _rob2()
        assert result["complete"] is True
        assert {d["judgement"] for d in result["domains"].values()} == {"low"}
        assert result["suggested_overall"] == "low"
        assert result["judgement_source"] == "algorithm"

    def test_domain1_not_concealed_is_high(self):
        result = _rob2(**{"1.2": "N"})
        assert result["domains"]["D1"]["judgement"] == "high"
        assert result["suggested_overall"] == "high"

    def test_domain1_concealed_with_baseline_problem_is_some_concerns(self):
        result = _rob2(**{"1.3": "Y"})
        assert result["domains"]["D1"]["judgement"] == "some_concerns"

    def test_domain2_assignment_some_concerns(self):
        # 2.1 知道分组、2.3 存在因试验情境产生的偏离、2.4 不太可能影响结局
        # → Part 1 = Some concerns；2.6 适当分析 → Part 2 = Low → 域 = Some。
        result = _rob2(**{"2.1": "Y", "2.3": "Y", "2.4": "N"})
        assert result["domains"]["D2"]["judgement"] == "some_concerns"
        assert result["domains"]["D2"]["part_1"] == "some_concerns"
        assert result["domains"]["D2"]["part_2"] == "low"

    def test_domain2_assignment_high(self):
        # 偏离、影响结局、组间不平衡 → Part 1 = High → 域 = High。
        result = _rob2(**{"2.1": "Y", "2.3": "Y", "2.4": "Y", "2.5": "N"})
        assert result["domains"]["D2"]["judgement"] == "high"

    def test_domain2_adhering_variant_high(self):
        responses = {key: value for key, value in ROB2_LOW.items()
                     if key not in {"2.7"}}
        responses.update({"2.1": "Y", "2.3": "N", "2.6": "N"})
        result = core.aggregate("rob2", responses, variant="adhering")
        assert result["variant"] == "adhering"
        assert result["domains"]["D2"]["judgement"] == "high"

    def test_domain2_adhering_high_with_failures_and_no_analysis(self):
        """官方 High：aware 且（重心在实施失败/不依从）且 2.6 = N/PN。

        旧实现只覆盖「非协议干预未平衡」一支，会漏掉 balanced + 实施失败
        + 未使用适当分析的 High 组合。
        """
        responses = {key: value for key, value in ROB2_LOW.items()
                     if key not in {"2.7"}}
        responses.update({"2.1": "Y", "2.3": "Y", "2.4": "Y", "2.5": "N",
                          "2.6": "N"})
        result = core.aggregate("rob2", responses, variant="adhering")
        assert result["domains"]["D2"]["judgement"] == "high"

    def test_domain2_ni_analysis_is_not_high(self):
        """2.6 = NI 不等于「未使用适当分析」，不得据此升 High。"""
        result = _rob2(**{"2.6": "NI", "2.7": "Y"})
        assert result["domains"]["D2"]["part_2"] == "some_concerns"
        assert result["domains"]["D2"]["judgement"] == "some_concerns"

    def test_domain2_ni_awareness_follows_official_routing(self):
        """终审 #47：2.1/2.2 答 NI（无信息）必须与 Y/PY 同路进入 2.3 分支
        （官方决策表『If Y/PY/NI to 2.1 or 2.2: (answer 2.3)』），只有两者
        都是 N/PN 才算「不知情 → 直接 Low」。

        旧→新：NI/NI + 2.3Y/2.4Y/2.5N 曾与 N/PN 同路直接判 Low（域 Low、
        suggested_overall low、GRADE 不降级，偏倚风险被静默低估）→ 现与
        2.1=Y 同组合完全同路（High）；NI + 2.3=NI 至少 Some concerns；
        不知情对照（N/N）保持 Low 不受影响。
        """
        ni = _rob2(**{"2.1": "NI", "2.2": "NI", "2.3": "Y", "2.4": "Y",
                      "2.5": "N"})
        aware = _rob2(**{"2.1": "Y", "2.2": "Y", "2.3": "Y", "2.4": "Y",
                         "2.5": "N"})
        assert ni["domains"]["D2"] == aware["domains"]["D2"]
        assert ni["domains"]["D2"]["part_1"] == "high"
        assert ni["domains"]["D2"]["judgement"] == "high"
        assert ni["suggested_overall"] == "high"
        # GRADE 衔接同步：rob2 high → serious（不再是 low 的不降级）
        assert core.study_rating_for_grade(
            "rob2", ni)["risk_of_bias"] == "serious"

        no_info_deviation = _rob2(**{"2.1": "NI", "2.2": "N", "2.3": "NI"})
        assert no_info_deviation["domains"]["D2"]["judgement"] == "some_concerns"

        unaware = _rob2()  # 2.1/2.2 = N/N → 不知情 → Low（既有行为）
        assert unaware["domains"]["D2"]["judgement"] == "low"

    def test_domain2_adhering_ni_awareness_follows_official_routing(self):
        """终审 #47（adhering 变体同口径）：2.1/2.2=NI 不得折叠进「不知情」
        分支在无实施失败/不依从时产出 Low；NI 与 Y/PY 同路。"""

        def adhering(**overrides):
            responses = {key: value for key, value in ROB2_LOW.items()
                         if key not in {"2.7"}}
            responses.update(overrides)
            return core.aggregate("rob2", responses, variant="adhering")

        ni = adhering(**{"2.1": "NI", "2.2": "NI", "2.3": "N", "2.4": "Y",
                         "2.5": "Y", "2.6": "N"})
        aware = adhering(**{"2.1": "Y", "2.2": "Y", "2.3": "N", "2.4": "Y",
                            "2.5": "Y", "2.6": "N"})
        assert ni["domains"]["D2"]["judgement"] == \
            aware["domains"]["D2"]["judgement"] == "high"
        # 不知情对照：无实施失败/不依从（2.4/2.5=NA）→ 保守 Low 不变
        assert adhering()["domains"]["D2"]["judgement"] == "low"

    def test_domain3_unclear_dependence_is_some_concerns(self):
        """3.3 = NI 不构成 High 的「could depend」，保守归 Some concerns。"""
        result = _rob2(**{"3.1": "N", "3.2": "N", "3.3": "NI", "3.4": "Y"})
        assert result["domains"]["D3"]["judgement"] == "some_concerns"

    def test_domain3_missing_data_high(self):
        result = _rob2(**{"3.1": "N", "3.2": "N", "3.3": "Y", "3.4": "Y"})
        assert result["domains"]["D3"]["judgement"] == "high"

    def test_domain4_measurement_high(self):
        result = _rob2(**{"4.3": "Y", "4.4": "Y", "4.5": "Y"})
        assert result["domains"]["D4"]["judgement"] == "high"

    def test_domain5_selection_high(self):
        result = _rob2(**{"5.2": "Y"})
        assert result["domains"]["D5"]["judgement"] == "high"

    def test_cross_domain_upgrade_requires_manual_judgement(self):
        """跨域升级：两个域 Some concerns → 总体建议 Some，但官方允许因
        “多个域 Some concerns 实质降低信心”人工上调为 High（必须给理由）。"""
        responses = dict(ROB2_LOW)
        responses.update({"1.3": "Y", "2.1": "Y", "2.3": "Y", "2.4": "N"})
        result = core.aggregate("rob2", responses)
        assert result["domains"]["D1"]["judgement"] == "some_concerns"
        assert result["domains"]["D2"]["judgement"] == "some_concerns"
        assert result["suggested_overall"] == "some_concerns"
        assert result["multiple_some_concerns"] is True
        assert result["manual_upgrade_recommended"] is True
        assert result["manual_upgrade_reason_key"] == \
            "appraisal.rob2.multiple_some_concerns"

        upgraded = core.aggregate(
            "rob2", responses,
            overall={"judgement": "high", "reason": "multiple domains with concerns"})
        assert upgraded["judgement"] == "high"
        assert upgraded["judgement_source"] == "manual"
        assert upgraded["suggested_overall"] == "some_concerns"

    def test_manual_judgement_requires_reason(self):
        with pytest.raises(core.AppraisalError) as ctx:
            core.aggregate("rob2", ROB2_LOW, overall={"judgement": "high"})
        assert ctx.value.message_key == "appraisal.overall_invalid"

    def test_partial_responses_are_not_scored(self):
        partial = dict(ROB2_LOW)
        partial.pop("4.1")
        result = core.aggregate("rob2", partial)
        assert result["complete"] is False
        assert result["suggested_overall"] is None
        assert result["missing_items"] == ["4.1"]


# ---------------------------------------------------------------------------
# 3. AMSTAR 2 Box 2 四级判定钉
# ---------------------------------------------------------------------------


def _amstar(**overrides):
    responses = {str(number): "Yes" for number in range(1, 17)}
    responses.update(overrides)
    return core.aggregate("amstar2", responses)


class TestAmstar2Box2:
    def test_high_no_weakness(self):
        result = _amstar()
        assert result["complete"] is True
        assert result["suggested_confidence"] == "high"
        assert result["critical_flaws"] == []

    def test_high_one_noncritical_weakness(self):
        result = _amstar(**{"8": "Partial Yes"})
        assert result["suggested_confidence"] == "high"
        assert len(result["noncritical_weaknesses"]) == 1

    def test_moderate_more_than_one_noncritical_weakness(self):
        result = _amstar(**{"8": "Partial Yes", "10": "No"})
        assert result["suggested_confidence"] == "moderate"
        assert result["manual_downgrade_recommended"] is True
        assert result["manual_downgrade_reason_key"] == \
            "appraisal.amstar2.multiple_weaknesses"

    def test_low_one_critical_flaw(self):
        result = _amstar(**{"4": "No"})
        assert result["suggested_confidence"] == "low"
        assert result["critical_flaws"] == [{"item": "4", "answer": "No"}]

    def test_critically_low_multiple_critical_flaws(self):
        result = _amstar(**{"4": "No", "7": "No"})
        assert result["suggested_confidence"] == "critically_low"
        assert len(result["critical_flaws"]) == 2

    def test_no_meta_analysis_is_not_a_flaw(self):
        result = _amstar(**{"11": "No meta-analysis conducted",
                            "15": "No meta-analysis conducted"})
        assert result["suggested_confidence"] == "high"
        assert result["critical_flaws"] == []
        assert result["noncritical_weaknesses"] == []

    def test_critical_items_are_box1(self):
        assert list(core.REGISTRY["amstar2"]["critical_items"]) == [
            "2", "4", "7", "9", "11", "13", "15"]

    def test_manual_confidence_override_needs_reason(self):
        result = core.aggregate(
            "amstar2", {str(number): "Yes" for number in range(1, 17)},
            overall={"confidence": "low", "reason": "context-specific concerns"})
        assert result["confidence"] == "low"
        assert result["confidence_source"] == "manual"
        assert result["suggested_confidence"] == "high"


# ---------------------------------------------------------------------------
# 4. CASP / MMAT / AXIS 不出分的负钉
# ---------------------------------------------------------------------------

def _all_answered(tool_id: str, variant: str | None = None) -> dict:
    tool = core.REGISTRY[tool_id]
    responses = {}
    for item in core.iter_items(tool, variant):
        options = item.get("response_options") or tool["response_options"]
        responses[item["id"]] = options[0]
    return responses


def _walk_keys(value, prefix=""):
    if isinstance(value, dict):
        for key, child in value.items():
            yield f"{prefix}.{key}" if prefix else str(key)
            yield from _walk_keys(child, f"{prefix}.{key}" if prefix else str(key))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk_keys(child, f"{prefix}[{index}]")


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from _strings(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _strings(child)


class TestNoScoreNegative:
    @pytest.mark.parametrize("tool_id", ["casp-qualitative", "casp-rct",
                                         "casp-cross-sectional", "mmat", "axis"])
    def test_aggregation_has_no_score_field(self, tool_id):
        result = core.aggregate(tool_id, _all_answered(tool_id))
        assert result["mode"] == "no_score"
        assert result["complete"] is True
        assert result["overall_judgement"] is None
        for key in _walk_keys(result):
            lowered = key.lower()
            for banned in ("score", "percent", "weighted", "total", "points"):
                assert banned not in lowered, f"{tool_id} 聚合输出含分数字段：{key}"
        # 逐条报告结构：每条应答都带 id / response。
        assert len(result["items"]) == len(list(core.iter_items(
            core.REGISTRY[tool_id])))
        assert all("response" in entry for entry in result["items"])

    def test_casp_manual_overall_is_allowed_options_only(self):
        result = core.aggregate(
            "casp-qualitative", _all_answered("casp-qualitative"),
            overall={"judgement": "moderate", "reason": "two items unclear"})
        assert result["overall_judgement"] == "moderate"
        assert result["judgement_source"] == "manual"
        assert result["overall_judgement_options"] == ["high", "moderate", "low"]
        with pytest.raises(core.AppraisalError):
            core.aggregate("casp-qualitative", _all_answered("casp-qualitative"),
                           overall={"judgement": "excellent", "reason": "x"})

    def test_mmat_has_no_overall_score_option(self):
        assert core.REGISTRY["mmat"]["overall_judgement_options"] == []
        result = core.aggregate("mmat", _all_answered("mmat"))
        assert result["overall_judgement_options"] == []
        assert result["judgement_required"] is False
        casp = core.aggregate("casp-qualitative",
                              _all_answered("casp-qualitative"))
        assert casp["judgement_required"] is True

    def test_partial_casp_reports_unanswered(self):
        result = core.aggregate("casp-qualitative", {"1": "Yes", "2": "No"})
        assert result["complete"] is False
        assert result["answered"] == 2
        assert len(result["unanswered"]) == 8


class TestAxisReverseItemScoring:
    """终审 #06：AXIS 第 19 条（资助/利益冲突）是 ``reverse_direction`` 反向
    条目，无人工总体判定时逐条归纳必须按「答 No=正向」计分。

    旧→新（修复前按正向题数 Yes）：19=No 曾得 moderate（risk_of_bias
    serious）、19=Yes 曾得 high（risk_of_bias low）——答「有利益冲突顾虑」
    反而抬高质量档、答「无顾虑」反而压低，方向完全颠倒。
    """

    @staticmethod
    def _axis_responses(item19, yes_base=15):
        responses = {str(i): ("Yes" if i <= yes_base else "No")
                     for i in range(1, 21)}
        responses["19"] = item19
        return responses

    def test_item19_no_counts_positive_low_risk(self):
        # 数值锚：正向 16/20 = 0.8 → high → risk_of_bias low
        aggregate = core.aggregate("axis", self._axis_responses("No"),
                                   comments={}, overall=None)
        assert core._per_item_quality(aggregate, core.REGISTRY["axis"]) == "high"
        rating = core.study_rating_for_grade("axis", aggregate)
        assert rating["risk_of_bias"] == "low"
        assert rating["source"] == "per_item_report"
        assert rating["rule"] == "axis:high"
        # 存储包装形状（save_record 落库形状）同源
        wrapped = core.study_rating_for_grade(
            "axis", {"aggregate": aggregate, "override": None, "variant": None})
        assert wrapped["risk_of_bias"] == "low"

    def test_item19_yes_downgrades_one_tier(self):
        # 数值锚：正向 15/20 = 0.75 → moderate → risk_of_bias serious（恰升一档）
        aggregate = core.aggregate("axis", self._axis_responses("Yes"),
                                   comments={}, overall=None)
        assert core._per_item_quality(aggregate, core.REGISTRY["axis"]) == "moderate"
        rating = core.study_rating_for_grade("axis", aggregate)
        assert rating["risk_of_bias"] == "serious"
        assert rating["source"] == "per_item_report"
        assert rating["rule"] == "axis:moderate"

    def test_manual_overall_still_takes_priority(self):
        """人工总体判定优先：逐条计分口径变化不影响（回归钉）。"""
        aggregate = core.aggregate("axis", self._axis_responses("Yes"),
                                   comments={},
                                   overall={"judgement": "high",
                                            "reason": "reviewer judged overall"})
        rating = core.study_rating_for_grade("axis", aggregate)
        assert rating["risk_of_bias"] == "low"
        assert rating["source"] == "manual"


# ---------------------------------------------------------------------------
# 5. 推荐映射钉
# ---------------------------------------------------------------------------


class TestRecommendation:
    @pytest.mark.parametrize("title,label,primary", [
        ("A randomised controlled trial of peer tutoring", "rct", "rob2"),
        ("A systematic review of formative assessment", "systematic_review",
         "amstar2"),
        ("A meta-analysis of homework effects", "meta_analysis", "amstar2"),
        ("Cross-sectional survey of student wellbeing", "cross_sectional",
         "casp-cross-sectional"),
        ("Prevalence of burnout among students", "prevalence",
         "casp-cross-sectional"),
        ("Diagnostic test study of a screening tool", "diagnostic",
         "casp-diagnostic"),
        ("A qualitative study of teacher beliefs", "qualitative",
         "casp-qualitative"),
        ("A mixed methods study of classroom talk", "mixed_methods", "mmat"),
        ("A cost-effectiveness analysis of tutoring", "economic_evaluation",
         "casp-economic-evaluation"),
    ])
    def test_design_mapping(self, title, label, primary):
        result = core.recommend({"title": title})
        assert result["design"]["appraisal_label"] == label
        assert result["primary"]["tool_id"] == primary
        assert result["reason_key"].startswith("appraisal.recommend.")
        assert result["primary"]["citation"].strip()
        assert result["primary"]["license"]["id"]

    def test_unrecognized_returns_no_primary(self):
        result = core.recommend({"title": "Notes on a curriculum meeting"})
        assert result["primary"] is None
        assert result["alternatives"] == []
        assert result["reason_key"] == "appraisal.recommend.unrecognized"

    def test_mapping_table_only_references_registry(self):
        for label, rule in core.RECOMMENDATION_MAP.items():
            assert rule["primary"] in core.REGISTRY, label
            for alternative in rule["alternatives"]:
                assert alternative in core.REGISTRY, (label, alternative)
            assert rule["reason_key"].startswith("appraisal.recommend.")

    def test_every_registry_tool_is_reachable(self):
        reachable = set()
        for rule in core.RECOMMENDATION_MAP.values():
            reachable.add(rule["primary"])
            reachable.update(rule["alternatives"])
        # CERQual 的评估对象是单条 review finding（不是原始研究/整篇综述），
        # 不在按设计推荐面里；两个 meta 清单按摘要内容二选一（见下一用例）。
        expected = set(core.REGISTRY) - {
            "cerqual", "casp-systematic-review-meta-rct",
            "casp-systematic-review-meta-observational"}
        assert expected <= reachable, sorted(expected - reachable)

    def test_meta_analysis_variant_selected_by_content(self):
        rct_meta = core.recommend(
            {"title": "A meta-analysis of randomised controlled trials"})
        obs_meta = core.recommend(
            {"title": "A meta-analysis of observational cohort studies"})
        assert "casp-systematic-review-meta-rct" in {
            card["tool_id"] for card in rct_meta["alternatives"]}
        assert "casp-systematic-review-meta-observational" in {
            card["tool_id"] for card in obs_meta["alternatives"]}


# ---------------------------------------------------------------------------
# 6. 存储往返 + 幂等迁移
# ---------------------------------------------------------------------------


class TestStorage:
    def test_ensure_is_idempotent_and_additive(self, tmp_path):
        path = tmp_path / "alice.db"
        with closing(sqlite3.connect(path)) as conn, conn:
            conn.execute("CREATE TABLE decisions (zotero_key TEXT PRIMARY KEY, "
                         "decision TEXT)")
            conn.execute("INSERT INTO decisions VALUES ('K1','include')")
            core.ensure_appraisal_storage(conn)
            core.ensure_appraisal_storage(conn)
            core.save_record(
                conn, "K1", "rob2",
                responses={"1.1": "Y"}, comments={}, variant="assignment",
                overall={"aggregate": {"judgement": "low"}}, note="first")
            core.save_record(
                conn, "K1", "rob2",
                responses={"1.1": "N"}, comments={"1.1": "checked protocol"},
                variant="assignment",
                overall={"aggregate": {"judgement": "high"}}, note="second")
            count = conn.execute(
                "SELECT COUNT(*) FROM appraisal_records").fetchone()[0]
            decisions = conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        assert count == 1, "同一 article_key+tool_id 必须幂等覆盖"
        assert decisions == 1, "新表不得影响既有 decisions"
        assert integrity == "ok"

    def test_round_trip(self, tmp_path):
        path = tmp_path / "bob.db"
        with closing(sqlite3.connect(path)) as conn, conn:
            core.ensure_appraisal_storage(conn)
            saved = core.save_record(
                conn, "K2", "amstar2",
                responses={"1": "Yes"}, comments={}, variant=None,
                overall={"aggregate": {"confidence": "high"}}, note="note")
            loaded = core.load_record(conn, "K2", "amstar2")
        assert loaded["responses"] == {"1": "Yes"}
        assert loaded["note"] == "note"
        assert loaded["overall"] == {"aggregate": {"confidence": "high"}}
        assert loaded["updated_at"] == saved["updated_at"]

    def test_load_missing_returns_none(self, tmp_path):
        path = tmp_path / "carol.db"
        with closing(sqlite3.connect(path)) as conn, conn:
            core.ensure_appraisal_storage(conn)
            assert core.load_record(conn, "missing", "rob2") is None


# ---------------------------------------------------------------------------
# 7. HTTP 端点（注册表 / 推荐 / 存取 / 400）
# ---------------------------------------------------------------------------


class TestAppraisalEndpoints(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app, raise_server_exceptions=False)
        cls.client.__enter__()
        user = auth_mod.create_user("r215b-appraisal@example.test",
                                    "R215B-Appraisal-2026!")
        cls.token = auth_mod.create_access_token(user["id"], user["username"])
        cls.auth = {"Authorization": "Bearer " + cls.token}
        cls.user = {"user_id": user["id"], "username": user["username"]}
        peer = auth_mod.create_user("r215b-appraisal-peer@example.test",
                                    "R215B-Appraisal-Peer-2026!")
        cls.peer_token = auth_mod.create_access_token(peer["id"], peer["username"])
        cls.peer_auth = {"Authorization": "Bearer " + cls.peer_token}
        cls.peer = {"user_id": peer["id"], "username": peer["username"]}

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        fixture.cleanup()

    def setUp(self):
        response = self.client.post(
            "/api/tasks", json={"name": f"appraisal-{self._testMethodName}"},
            headers=self.auth)
        assert response.status_code in (200, 201), response.text
        self.task = response.json()["task_id"]
        self.base = f"/api/tasks/{self.task}/appraisal"
        ctx = app.state.review_workflow.context(self.task, self.user)
        db_mod.upsert_articles(ctx.owner_db, [
            Article(zotero_key="K-RCT", title="A randomised controlled trial"),
            Article(zotero_key="K-QUAL", title="A qualitative interview study"),
        ], [])

    def test_tools_summary_includes_citation_and_license(self):
        response = self.client.get(f"{self.base}/tools", headers=self.auth)
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["available"] is True
        assert payload["count"] == 16
        assert payload["link_only_count"] == 20
        for tool in payload["tools"]:
            assert tool["citation"].strip()
            assert tool["license"]["id"]
            assert tool["license"]["notice"].strip()
            assert tool["item_count"] > 0
        assert any(entry["id"] == "jbi-qualitative"
                   for entry in payload["link_only"])

    def test_recommendation_uses_study_design(self):
        response = self.client.get(f"{self.base}/recommendation", headers=self.auth,
                                   params={"article_key": "K-RCT"})
        assert response.status_code == 200, response.text
        assert response.json()["primary"]["tool_id"] == "rob2"
        response = self.client.get(f"{self.base}/recommendation", headers=self.auth,
                                   params={"article_key": "K-QUAL"})
        assert response.json()["primary"]["tool_id"] == "casp-qualitative"
        response = self.client.get(f"{self.base}/recommendation", headers=self.auth,
                                   params={"article_key": "MISSING"})
        assert response.status_code == 404
        assert response.json()["detail"]["message_key"] == \
            "appraisal.article_not_found"

    def test_recommendation_reads_owner_articles_for_teammate(self):
        """题录在属主库；协作者的个人库里没有题录也必须能拿到推荐。"""
        invite = self.client.post(
            f"/api/tasks/{self.task}/collaboration/invite",
            json={"username": self.peer["username"], "role": "coder"},
            headers=self.auth)
        assert invite.status_code == 201, invite.text
        response = self.client.get(f"{self.base}/recommendation",
                                   params={"article_key": "K-RCT"},
                                   headers=self.peer_auth)
        assert response.status_code == 200, response.text
        assert response.json()["primary"]["tool_id"] == "rob2"

    def test_put_get_round_trip(self):
        put = self.client.put(f"{self.base}/K-RCT", headers=self.auth,
                              json={"tool": "rob2", "responses": ROB2_LOW,
                                    "note": "draft"})
        assert put.status_code == 200, put.text
        assert put.json()["overall"]["judgement"] == "low"

        get = self.client.get(f"{self.base}/K-RCT", headers=self.auth,
                              params={"tool": "rob2"})
        assert get.status_code == 200, get.text
        body = get.json()
        assert body["saved"] is True
        assert body["note"] == "draft"
        assert body["responses"]["1.1"] == "Y"
        assert body["tool"]["id"] == "rob2"
        assert len(body["tool"]["domains"]) == 5
        assert body["overall"]["judgement"] == "low"

    def test_put_rejects_unknown_tool_item_option(self):
        cases = [
            ({"tool": "not-a-tool", "responses": {}}, "appraisal.tool_unknown"),
            ({"tool": "rob2", "responses": {"9.9": "Y"}},
             "appraisal.item_unknown"),
            ({"tool": "rob2", "responses": {"1.1": "MAYBE"}},
             "appraisal.option_invalid"),
            ({"tool": "nos", "responses": {}}, "appraisal.tool_link_only"),
            ({"responses": {}}, "appraisal.tool_required"),
        ]
        for body, message_key in cases:
            response = self.client.put(f"{self.base}/K-RCT", headers=self.auth,
                                       json=body)
            assert response.status_code == 400, (body, response.text)
            assert response.json()["detail"]["message_key"] == message_key

    def test_put_rejects_non_object_body(self):
        response = self.client.put(f"{self.base}/K-RCT", headers=self.auth,
                                   json=["rob2"])
        assert response.status_code == 400, response.text
        assert response.json()["detail"]["message_key"] == "appraisal.body_invalid"

    def test_put_returns_full_tool_definition(self):
        put = self.client.put(f"{self.base}/K-RCT", headers=self.auth,
                              json={"tool": "rob2", "responses": ROB2_LOW})
        assert put.status_code == 200, put.text
        assert put.json()["tool"]["id"] == "rob2"
        assert len(put.json()["tool"]["domains"]) == 5

    def test_get_requires_registered_tool(self):
        response = self.client.get(f"{self.base}/K-RCT", headers=self.auth)
        assert response.status_code == 400
        assert response.json()["detail"]["message_key"] == "appraisal.tool_required"
        response = self.client.get(f"{self.base}/K-RCT", headers=self.auth,
                                   params={"tool": "quadas-2"})
        assert response.status_code == 400
        assert response.json()["detail"]["message_key"] == "appraisal.tool_link_only"

    def test_put_manual_upgrade_round_trip(self):
        responses = dict(ROB2_LOW)
        responses.update({"1.3": "Y", "2.1": "Y", "2.3": "Y", "2.4": "N"})
        put = self.client.put(
            f"{self.base}/K-RCT", headers=self.auth,
            json={"tool": "rob2", "responses": responses,
                  "overall": {"judgement": "high", "reason": "multiple concerns"}})
        assert put.status_code == 200, put.text
        assert put.json()["overall"]["judgement"] == "high"
        get = self.client.get(f"{self.base}/K-RCT", headers=self.auth,
                              params={"tool": "rob2"})
        assert get.json()["overall"]["judgement"] == "high"
        assert get.json()["overall"]["judgement_source"] == "manual"

    def test_casp_put_never_returns_scores(self):
        responses = _all_answered("casp-qualitative")
        put = self.client.put(f"{self.base}/K-QUAL", headers=self.auth,
                              json={"tool": "casp-qualitative",
                                    "responses": responses})
        assert put.status_code == 200, put.text
        overall = put.json()["overall"]
        assert overall["mode"] == "no_score"
        for key in _walk_keys(overall):
            assert "score" not in key.lower()

    # ---- 失败降级：绝不 500 ------------------------------------------------

    def test_tools_degrades_when_registry_raises(self):
        with mock.patch.object(core, "tool_summaries",
                               side_effect=RuntimeError("boom")):
            response = self.client.get(f"{self.base}/tools", headers=self.auth)
        assert response.status_code == 200, response.text
        assert response.json()["available"] is False
        assert response.json()["message_key"] == "appraisal.registry_unavailable"

    def test_get_degrades_when_storage_unreadable(self):
        with mock.patch.object(core, "load_record",
                               side_effect=RuntimeError("boom")):
            response = self.client.get(f"{self.base}/K-RCT", headers=self.auth,
                                       params={"tool": "rob2"})
        assert response.status_code == 200, response.text
        assert response.json()["available"] is False
        assert response.json()["message_key"] == "appraisal.storage_unavailable"

    def test_put_degrades_to_409_when_storage_fails(self):
        with mock.patch.object(core, "save_record",
                               side_effect=sqlite3.OperationalError("boom")):
            response = self.client.put(
                f"{self.base}/K-RCT", headers=self.auth,
                json={"tool": "rob2", "responses": ROB2_LOW})
        assert response.status_code == 409, response.text
        assert response.json()["detail"]["message_key"] == \
            "appraisal.storage_unavailable"

    def test_recommendation_degrades_when_classifier_raises(self):
        with mock.patch.object(core, "recommend",
                               side_effect=RuntimeError("boom")):
            response = self.client.get(f"{self.base}/recommendation",
                                       headers=self.auth,
                                       params={"article_key": "K-RCT"})
        assert response.status_code == 200, response.text
        assert response.json()["available"] is False
        assert response.json()["message_key"] == "appraisal.unavailable"
