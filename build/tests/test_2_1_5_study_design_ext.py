"""泳道 D3（2.1.5）：研究设计类别由七类扩至十一类——教育/社科口径的钉子。

口径说明：任务书写「七类扩为十类」，但同一段明确列出**四**个新类别
（质性研究/混合方法/调查研究/话语、语料分析），七加四为十一。本钉子按
**列出的类别**实现并以十一类为口径（不为了凑数删掉任一被点名的类别）。

背景（2026-11-07 用户验证）：既有七类全为**临床口径**（meta/系统综述/RCT/
队列/病例对照/横断面/诊断准确性），教育与社会科学的语料大多落入「未识别」。
本组钉子锁定 2.1.5 的扩类裁定：

1. **四个新类别的正边界**：质性研究（``qualitative``）、混合方法
   （``mixed_methods``）、调查研究（``survey``）、话语/语料分析
   （``discourse``）。每类 ≥4 条**真实标题风格**的正例（含
   ``narrative inquiry of an underprivileged student`` 这类用户语料）。
2. **优先级不倒退**：新类别整体排在临床七类**之后**——RCT / Meta / 系统综述
   里出现 ``survey`` / ``qualitative`` / ``interviews`` 等干扰词时仍按临床
   类别判定（宁缺毋滥口径不变）；``cross-sectional survey`` 仍判
   ``cross_sectional``（既有 P/R 评估与 2.1.5 推荐映射钉都建立在这一判定上）。
3. **未识别输出不变**：``other`` 的返回形状逐键不变（前端徽标/预填只读
   ``label``/``recognized``/``suggest_exclusion_reason``）。
4. **与文献评价推荐共用判定源**：``coscreen.appraisal.recommend`` 对新类别
   直接给出主选/备选工具（qualitative→casp-qualitative、discourse→
   casp-qualitative、survey→casp-cross-sectional/AXIS、mixed_methods→MMAT），
   且 ``design.appraisal_source == "study_design"``（不再依赖 appraisal 的
   补充规则——判定口径单一来源）。
5. **临床证据映射不被扩类**（谨慎边界）：``evidence_mapper`` 的
   ``study_design`` 维度服务 GRADE、保持临床七类；本文件用 import 断言 +
   夹具回归断言把「两处独立」钉死，避免口径分叉却无人察觉。
6. **评估材料**：W4 的 48 篇 PubMed 夹具原样不动（每类 6 条、摘要截断
   400 字符、§5 机器块逐值互锁），新类别另立合成样本
   ``build/tests/study_design_ext_sample.json``（16 条 = 4 类 × 4，计数与
   逐条判定钉在本文件；不作 P/R 承诺）。

规则版本：``study_design.RULE_VERSION`` 由 **1 → 2**（判定因子变化必须 bump，
与 ``docs/methodology/study_design_classifier.md`` §5 机器块同步）。
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from coscreen import appraisal as appraisal_core  # noqa: E402
from coscreen import evidence_mapper  # noqa: E402
from coscreen.al import study_design as sd  # noqa: E402

FIXTURE = REPO / "build" / "tests" / "study_design_fixture.json"
SAMPLE = REPO / "build" / "tests" / "study_design_ext_sample.json"

CLINICAL_LABELS = ("meta_analysis", "systematic_review", "rct", "cohort",
                   "case_control", "cross_sectional", "diagnostic")
NEW_LABELS = ("qualitative", "mixed_methods", "survey", "discourse")


def _classify(title, abstract=""):
    return sd.classify_article({"title": title, "abstract": abstract})


class ExtendedCategoryPositiveNails(unittest.TestCase):
    """每类 ≥4 条正例（题名风格取自教育/社科真实语料；宁缺毋滥）。"""

    POSITIVES = {
        "qualitative": (
            "A narrative inquiry of an underprivileged student's mathematics identity",
            "Understanding teacher burnout: a qualitative study of secondary teachers",
            "Narrative analysis of student teachers' reflective journals",
            "An ethnographic study of classroom interaction in rural schools",
            "Lived experiences of first-generation students: a phenomenological study",
            "A case study of curriculum reform in a vocational college",
            "Grounded theory of teacher collaboration in professional learning communities",
            "An interview study of parental school choice in urban China",
        ),
        "mixed_methods": (
            "A mixed methods study of classroom talk and student agency",
            "Mixed-methods evaluation of a reading intervention",
            "Using mixed methods to examine teacher feedback practices",
            "A mixed-method design for evaluating peer tutoring",
        ),
        "survey": (
            "A national survey of school principals' leadership practices",
            "Questionnaire-based study of exam anxiety among high school students",
            "Student perceptions of online learning: a survey study",
            "A questionnaire study of teachers' assessment literacy",
        ),
        "discourse": (
            "Discourse analysis of classroom questioning sequences",
            "A conversation analysis of peer feedback turns in group work",
            "Corpus-based analysis of textbook vocabulary in primary English",
            "Discourse analysis of inclusive education policy documents",
        ),
    }

    def test_each_new_category_has_at_least_four_positives(self):
        for label, titles in self.POSITIVES.items():
            self.assertGreaterEqual(len(titles), 4, f"{label} 正例不足 4 条")
            for title in titles:
                result = _classify(title)
                self.assertEqual(result["label"], label, title)
                self.assertTrue(result["recognized"], title)
                self.assertEqual(result["source"], "title_abstract", title)
                self.assertTrue(result["matched_keyword"], title)
                # 识别为非 RCT 的具体设计 → 预填建议（预填不代存）
                self.assertEqual(result["suggest_exclusion_reason"], "研究设计不符", title)

    def test_abstract_signal_also_classifies(self):
        cases = {
            "qualitative": "We conducted a qualitative study with 24 teachers.",
            "mixed_methods": "This mixed-methods design combined surveys and interviews.",
            "survey": "An online questionnaire was distributed to 400 students.",
            "discourse": "Recordings were analysed using conversation analysis.",
        }
        for label, abstract in cases.items():
            result = _classify("", abstract)
            self.assertEqual(result["label"], label, abstract)
            self.assertEqual(result["source"], "title_abstract", abstract)

    def test_classify_corpus_covers_all_extended_categories_plus_other(self):
        items = [{"zotero_key": f"P{i}", "title": title}
                 for i, titles in enumerate(self.POSITIVES.values())
                 for title in titles]
        corpus = sd.classify_corpus(items)
        labels = {result["label"] for result in corpus.values()}
        self.assertTrue(set(NEW_LABELS) <= labels, sorted(labels))
        self.assertEqual(set(sd.DESIGN_LABELS), set(CLINICAL_LABELS) | set(NEW_LABELS) | {"other"})

    def test_extended_sample_fixture_counts_and_labels(self):
        """结构性评估样本：合成 16 条（每类 4 条）→ 期望计数 4/类、16 条。

        旧→新：W4 的 48 篇 PubMed 夹具**不追加**新类别样本行（每类 6 条、
        摘要统一截断 400 字符、§5 机器块与文档逐值互锁；混入异构样本会同时
        破坏取数溯源和这些锁）。新类别另立合成样本，只钉规则覆盖与计数，
        **不报 P/R**（见 JSON 的 honesty_note）。"""
        data = json.loads(SAMPLE.read_text(encoding="utf-8"))
        items = data["items"]
        self.assertEqual(len(items), 16, "合成评估样本条数固定为 16（4 类 × 4）")
        per_class = {}
        for item in items:
            per_class[item["gold"]] = per_class.get(item["gold"], 0) + 1
        self.assertEqual(per_class, {label: 4 for label in NEW_LABELS})
        self.assertEqual(data["meta"]["per_class"], 4)
        self.assertEqual(len({item["zotero_key"] for item in items}), 16,
                         "样本键必须互异")
        for item in items:
            result = _classify(item["title"])
            self.assertEqual(result["label"], item["gold"], item["title"])
            self.assertEqual(result["source"], "title_abstract", item["title"])
        corpus = sd.classify_corpus(items)
        self.assertEqual(len(corpus), 16)
        self.assertTrue(all(r["recognized"] for r in corpus.values()))


class PrecedenceNails(unittest.TestCase):
    """新类别排在临床七类之后：干扰词不得抢走临床判定（宁缺毋滥不变）。"""

    def test_rct_wins_over_new_category_words(self):
        for title in (
            "A randomized controlled trial of a survey feedback intervention",
            "Randomised controlled trial with qualitative interviews of teachers",
            "A randomised trial using mixed methods process evaluation",
            "Participants were randomly assigned to a discourse-based curriculum",
        ):
            self.assertEqual(_classify(title)["label"], "rct", title)

    def test_synthesis_beats_new_category_words(self):
        self.assertEqual(_classify("A systematic review of qualitative studies")["label"],
                         "systematic_review")
        self.assertEqual(_classify("A meta-analysis of survey studies")["label"],
                         "meta_analysis")

    def test_observational_wins_over_new_category_words(self):
        self.assertEqual(_classify("A prospective cohort study with questionnaire follow-up")["label"],
                         "cohort")
        self.assertEqual(_classify("A case-control study of survey respondents")["label"],
                         "case_control")
        # 既有判定不动：cross-sectional survey 仍归横断面（2.1.5 评估/推荐钉依赖它）
        self.assertEqual(_classify("Cross-sectional survey of student wellbeing")["label"],
                         "cross_sectional")
        self.assertEqual(_classify("A cross-sectional survey")["label"], "cross_sectional")

    def test_new_category_negatives_stay_unrecognized(self):
        for title in (
            "A narrative review of formative assessment",
            "The results differed qualitatively between groups",
            "A case report of a student with dyscalculia",
            "We surveyed 200 teachers about workload",
            "Notes on a curriculum meeting",
            "A survey of the literature on formative assessment",
            "cohortectomy outcomes",
            "Plain title with no design words",
        ):
            result = _classify(title)
            self.assertEqual(result["label"], "other", title)
            self.assertFalse(result["recognized"], title)


class UnrecognizedShapeNails(unittest.TestCase):
    """未识别输出逐键不变（前端只读 label/recognized/suggest_exclusion_reason）。"""

    def test_other_result_shape_is_unchanged(self):
        result = _classify("Notes on a curriculum meeting")
        self.assertEqual(sorted(result), sorted([
            "zotero_key", "label", "recognized", "is_rct", "source",
            "matched_keyword", "suggest_exclusion_reason", "rule_version"]))
        self.assertEqual(result["label"], "other")
        self.assertIsNone(result["source"])
        self.assertIsNone(result["matched_keyword"])
        self.assertIsNone(result["suggest_exclusion_reason"])
        self.assertEqual(result["rule_version"], 2)

    def test_rule_version_bumped_for_extended_categories(self):
        # 旧值 1（七类）→ 新值 2（十一类）：判定因子变化必须 bump
        self.assertEqual(sd.RULE_VERSION, 2)

    def test_api_degraded_rule_version_matches_rule_version(self):
        """导入失败降级响应里的兜底版本号必须与真实常量同值（防漂移）。"""
        from custom_backend import study_design_api  # noqa: WPS433
        self.assertEqual(study_design_api._RULE_VERSION_FALLBACK, sd.RULE_VERSION)

    def test_raw_publication_type_fallback_uses_extended_categories(self):
        result = sd.classify_article({
            "title": "Plain title", "abstract": "Plain abstract.",
            "raw": {"publication_type": ["Qualitative Research"]}})
        self.assertEqual((result["label"], result["source"]),
                         ("qualitative", "raw_json"))


class AppraisalRecommendationNails(unittest.TestCase):
    """文献评价推荐接入新类别（与初筛徽标共用同一判定源）。"""

    CASES = (
        ("A narrative inquiry of an underprivileged student's mathematics identity",
         "qualitative", "casp-qualitative"),
        ("Grounded theory of teacher collaboration", "qualitative", "casp-qualitative"),
        ("A mixed methods study of classroom talk", "mixed_methods", "mmat"),
        ("A national survey of school principals' leadership practices",
         "survey", "casp-cross-sectional"),
        ("Questionnaire-based study of exam anxiety", "survey", "casp-cross-sectional"),
        ("Discourse analysis of classroom questioning sequences",
         "discourse", "casp-qualitative"),
        ("Corpus-based analysis of textbook vocabulary", "discourse", "casp-qualitative"),
    )

    def test_new_categories_recommend_expected_tools(self):
        for title, label, tool in self.CASES:
            result = appraisal_core.recommend({"title": title})
            self.assertEqual(result["design"]["appraisal_label"], label, title)
            self.assertEqual(result["primary"]["tool_id"], tool, title)
            self.assertEqual(result["reason_key"], f"appraisal.recommend.{label}", title)
            self.assertTrue(result["primary"]["citation"].strip(), title)
            self.assertTrue(result["primary"]["license"]["id"], title)

    def test_new_categories_inherit_the_shared_classifier(self):
        """来源必须是 study_design（而非 appraisal 的补充规则）——单一判定源。"""
        for title, _, _ in self.CASES:
            result = appraisal_core.recommend({"title": title})
            self.assertEqual(result["design"]["appraisal_source"], "study_design", title)

    def test_survey_offers_axis_as_alternative(self):
        result = appraisal_core.recommend(
            {"title": "A national survey of school principals' leadership practices"})
        self.assertIn("axis", {card["tool_id"] for card in result["alternatives"]})

    def test_clinical_recommendations_unchanged(self):
        self.assertEqual(
            appraisal_core.recommend({"title": "A randomised controlled trial"})["primary"]["tool_id"],
            "rob2")
        self.assertEqual(
            appraisal_core.recommend({"title": "Cross-sectional survey of wellbeing"})["primary"]["tool_id"],
            "casp-cross-sectional")

    def test_mapping_table_only_references_registry(self):
        for label, rule in appraisal_core.RECOMMENDATION_MAP.items():
            self.assertIn(rule["primary"], appraisal_core.REGISTRY, label)
            for alternative in rule["alternatives"]:
                self.assertIn(alternative, appraisal_core.REGISTRY, (label, alternative))
            self.assertTrue(rule["reason_key"].startswith("appraisal.recommend."), label)


class ClinicalMapperBoundaryNails(unittest.TestCase):
    """证据映射保持临床七类（服务 GRADE），扩类只发生在初筛分类器。"""

    def test_evidence_mapper_rules_stay_clinical_seven(self):
        labels = tuple(label for label, _ in evidence_mapper._STUDY_DESIGN_RULES)
        self.assertEqual(labels, CLINICAL_LABELS,
                         "evidence_mapper 的 study_design 维度不得扩入教育/社科类别")

    def test_clinical_fixture_never_lands_in_new_categories(self):
        data = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.assertEqual(len(data["items"]), 48)
        for item in data["items"]:
            label = sd.classify_article(item)["label"]
            self.assertNotIn(label, NEW_LABELS, item["zotero_key"])
            self.assertIn(label, set(CLINICAL_LABELS) | {"other"}, item["zotero_key"])

    def test_evidence_map_dimension_outputs_stay_clinical(self):
        articles = [{"title": f"Paper {i}", "abstract": "Plain abstract.",
                     "journal": "J", "year": 2020} for i in range(3)]
        articles.append({"title": "A narrative inquiry of an underprivileged student",
                         "abstract": "", "journal": "J", "year": 2020})
        result = evidence_mapper.generate_evidence_map(articles, "study_design", "year")
        for label in result["x_labels"]:
            self.assertNotIn(label, NEW_LABELS)


if __name__ == "__main__":
    unittest.main()
