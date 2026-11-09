"""文献评价工具注册表 + 汇总算法 + 按设计推荐（2.1.5 Wave B2）。

本模块是文献评价后端核心的**唯一权威源**，供 ``custom_backend/appraisal_api.py``
直接复用：

1. :data:`REGISTRY` —— **仅收录可嵌入许可**的六类工具：
   CASP 全系 11 份清单（CC BY-NC-SA 4.0）、RoB 2（CC BY-NC-ND 4.0）、
   MMAT 2018（版权登记 #1148552，作者明示可免费使用）、AXIS（CC BY-NC 4.0）、
   AMSTAR 2（CC BY 4.0）、CERQual（系列论文 CC BY 4.0）。
   每个工具的条目原文、应答选项、聚合规则、官方引用串、许可结论均逐字取自
   ``E:/mubi-reviewflow/out/appraisal_tools/dossiers/`` 下的查证档案
   （casp-*.md / RoB2.md / MMAT.md / AXIS.md / AMSTAR2.md / CERQual.md）。

2. :data:`LINK_ONLY` —— **可用但未内置**清单：JBI 全系（15 份）、NOS、ROBIS、
   QUADAS-2、PROBAST、EPHPP。这些工具需要授权或仅允许链接，故注册表只登记
   展示元数据（官方链接 + 许可原因键），**不收录任何条目文本**。

3. 聚合算法均为**纯函数**：
   - RoB 2：按官方各域算法表逐域判定，再按 Table 3 汇总；CASP 官方不打总分，
     RoB 2 也不输出数值分数；
   - AMSTAR 2：按 Box 1 关键域 + Box 2 四级置信度规则；
   - CERQual：按"默认高置信 + 逐构件降级"的指导性规则给出**建议**值；
   - CASP / MMAT / AXIS：官方明确不打总分，输出逐条报告结构（无任何分数字段）。

4. :func:`recommend` —— 复用 ``coscreen.al.study_design`` 的研究设计类别面
   （2.1.5 泳道 D3 起为**十一类**：临床七类 + 质性/混合方法/调查研究/话语分析），
   在其未识别（``other``）时用本模块的补充关键词识别经济学评价/预测规则/
   诊断补充措辞/患病率等；映射表 :data:`RECOMMENDATION_MAP` 可配置，
   返回值带理由键。质性/混合方法/话语分析/调查研究自 D3 起由共享分类器
   直接判定（``design.appraisal_source == "study_design"``），本模块的补充
   规则只保留给共享分类器不覆盖的类别——避免同一设计在两处各有一套口径。

治理约束：
- 本模块不联网、不读写磁盘、零副作用；存储函数只接受调用方给出的
  ``sqlite3.Connection``，SQL 全部为常量字面量 + 参数绑定。
- 新增表 ``appraisal_records`` 可加、幂等；旧版程序不查询该表即忽略。
- 失败以 :class:`AppraisalError` 上抛（带 ``message_key``），由 API 层映射
  为 4xx；模块内部不产生 5xx。
"""

from __future__ import annotations

import copy
import json
import re
import sqlite3
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

__all__ = [
    "GRADE_ROB_MAP",
    "GRADE_RISK_LEVELS",
    "LINK_ONLY",
    "REGISTRY",
    "RECOMMENDATION_MAP",
    "SUPPLEMENTARY_DESIGN_RULES",
    "AppraisalError",
    "aggregate",
    "ensure_appraisal_storage",
    "get_tool",
    "iter_items",
    "load_record",
    "load_records_for_tool",
    "normalize_responses",
    "recommend",
    "save_record",
    "study_rating_for_grade",
    "tool_summaries",
    "validate_article_key",
]

#: 注册表规则版本：条目/算法/映射变化时 bump（前端与文档按此对齐）。
#: 2 = 2.1.5 泳道 D3：推荐映射新增 survey / discourse 两个设计类别。
RULE_VERSION: int = 2

#: 单条评价记录允许的最大应答条目数（防御性上限）。
MAX_RESPONSE_ITEMS: int = 300

#: 评价说明最大长度（字符）。
MAX_NOTE_CHARS: int = 20_000

#: 文献键最大长度（与 coscreen.dedup.KEY_MAX_LEN 同量级，但不导入以避免环）。
MAX_ARTICLE_KEY_CHARS: int = 255

_CASP_OPTIONS = ["Yes", "No", "Can't Tell"]
_MMAT_OPTIONS = ["Yes", "No", "Can't tell"]
_AXIS_OPTIONS = ["Yes", "No", "Do not know"]
_AMSTAR_OPTIONS_BASIC = ["Yes", "No"]
_AMSTAR_OPTIONS_PARTIAL = ["Yes", "Partial Yes", "No"]
_AMSTAR_OPTIONS_META = ["Yes", "No", "No meta-analysis conducted"]
_ROB2_OPTIONS = ["Y", "PY", "PN", "N", "NA", "NI"]
_ROB2_OPTIONS_NO_NI = ["NA", "Y", "PY", "PN", "N"]
_ROB2_JUDGEMENTS = ["Low risk of bias", "Some concerns", "High risk of bias"]
_ROB2_DIRECTION = ["NA", "Favours experimental", "Favours comparator",
                   "Towards null", "Away from null", "Unpredictable"]

_CERQUAL_LEVELS = ["No or very minor concerns", "Minor concerns",
                   "Moderate concerns", "Serious concerns"]

_LICENSE_CASP = {
    "id": "CC-BY-NC-SA-4.0",
    "name": "Creative Commons Attribution-NonCommercial-ShareAlike 4.0 International",
    "url": "https://creativecommons.org/licenses/by-nc-sa/4.0/",
    "embed": "allowed_noncommercial",
    "commercial": "permission_required",
    "no_derivatives": False,
    "notice": ("©CASP this work is licensed under the Creative Commons Attribution – "
               "Non-Commercial- Share A like. To view a copy of this licence, visit "
               "https://creativecommons.org/licenses/by-nc-sa/4.0/"),
}

_LICENSE_ROB2 = {
    "id": "CC-BY-NC-ND-4.0",
    "name": "Creative Commons Attribution-NonCommercial-NoDerivatives 4.0 International",
    "url": "https://creativecommons.org/licenses/by-nc-nd/4.0/legalcode",
    "embed": "allowed_noncommercial",
    "commercial": "permission_required",
    "no_derivatives": True,
    "notice": ("© 2025 by the authors. RoB 2, ROBINS-I, ROBINS-E and ROB ME are licensed "
               "under Creative Commons Attribution-NonCommercial-NoDerivatives 4.0 "
               "International License."),
}

_LICENSE_MMAT = {
    "id": "MMAT-COPYRIGHT-1148552",
    "name": "Registration of Copyright (#1148552), Canadian Intellectual Property Office",
    "url": "http://mixedmethodsappraisaltoolpublic.pbworks.com/",
    "embed": "allowed_noncommercial",
    "commercial": "permission_required",
    "no_derivatives": False,
    "notice": ("Mixed Methods Appraisal Tool (MMAT), version 2018. Registration of "
               "Copyright (#1148552), Canadian Intellectual Property Office, Industry "
               "Canada. The checklist and user guide can be downloaded and used free "
               "of charge."),
}

_LICENSE_AXIS = {
    "id": "CC-BY-NC-4.0",
    "name": "Creative Commons Attribution-NonCommercial 4.0 International",
    "url": "https://creativecommons.org/licenses/by-nc/4.0/",
    "embed": "allowed_noncommercial",
    "commercial": "permission_required",
    "no_derivatives": False,
    "notice": ("This is an Open Access article distributed in accordance with the "
               "Creative Commons Attribution Non Commercial (CC BY-NC 4.0) license, "
               "which permits others to distribute, remix, adapt, build upon this work "
               "non-commercially, and license their derivative works on different "
               "terms, provided the original work is properly cited and the use is "
               "non-commercial."),
}

_LICENSE_AMSTAR2 = {
    "id": "CC-BY-4.0",
    "name": "Creative Commons Attribution 4.0 International",
    "url": "https://creativecommons.org/licenses/by/4.0/",
    "embed": "allowed",
    "commercial": "allowed_with_attribution",
    "no_derivatives": False,
    "notice": ("This is an Open Access article distributed in accordance with the "
               "terms of the Creative Commons Attribution (CC BY 4.0) license, which "
               "permits others to distribute, remix, adapt and build upon this work, "
               "for commercial use, provided the original work is properly cited."),
}

_LICENSE_CERQUAL = {
    "id": "CC-BY-4.0",
    "name": "Creative Commons Attribution 4.0 International",
    "url": "https://creativecommons.org/licenses/by/4.0/",
    "embed": "allowed",
    "commercial": "allowed_with_attribution",
    "no_derivatives": False,
    "notice": ("Open Access This article is distributed under the terms of the "
               "Creative Commons Attribution 4.0 International License "
               "(http://creativecommons.org/licenses/by/4.0/)."),
}


class AppraisalError(ValueError):
    """评价请求的结构化错误（API 层据此映射 4xx，绝不 500）。"""

    def __init__(self, code: str, message_key: str, *, params: Mapping | None = None,
                 status: int = 400) -> None:
        super().__init__(code)
        self.code = code
        self.message_key = message_key
        self.params = dict(params or {})
        self.status = status

    def to_detail(self) -> dict:
        return {
            "error_code": self.code,
            "message_key": self.message_key,
            "params": self.params,
        }


def _item(item_id: str, text: str, section: str, *,
          options: Iterable[str] | None = None, **extra: Any) -> dict:
    item = {
        "id": item_id,
        "number": item_id,
        "section": section,
        "text": text,
        "response_options": list(options) if options is not None else None,
    }
    item.update(extra)
    return item


# ---------------------------------------------------------------------------
# CASP 全系 11 份清单（2024 系列；case-control 为 2026-10 更新版）
# ---------------------------------------------------------------------------

_CASP_QUALITATIVE = {
    "id": "casp-qualitative",
    "family": "CASP",
    "title": "CASP checklist: for qualitative research",
    "version": "2024",
    "year": 2024,
    "scope": "primary_study",
    "url": "https://casp-uk.net/casp-checklists/CASP-checklist-qualitative-2024.pdf",
    "citation": ("Critical Appraisal Skills Programme (2024) CASP checklist: for "
                 "qualitative research. Available at: "
                 "https://casp-uk.net/casp-checklists/CASP-checklist-qualitative-2024.pdf"),
    "license": _LICENSE_CASP,
    "response_options": _CASP_OPTIONS,
    "overall_judgement_options": ["high", "moderate", "low"],
    "aggregation": "no_score",
    "aggregation_notes": [
        "As these tools were designed as pedagogic tools – for use in learning how "
        "to appraise in small groups, we do not assign a scoring system.",
        "We would advise that if you can't answer 'yes' to the first 2/3 questions, "
        "then the evidence may be of poor quality.",
        "Classifying quality (or risk of bias) as high, moderate or low, based on the "
        "CASP tool would be perfectly acceptable.",
    ],
    "sections": [
        {"id": "A", "title": "Section A: Are the results valid?"},
        {"id": "B", "title": "Section B: What are the results?"},
        {"id": "C", "title": "Section C: Will the results help locally?"},
    ],
    "items": [
        _item("1", "Was there a clear statement of the aims of the research?", "A"),
        _item("2", "Is a qualitative methodology appropriate?", "A"),
        _item("3", "Was the research design appropriate to address the aims of the "
                   "research?", "A"),
        _item("4", "Was the recruitment strategy appropriate to the aims of the "
                   "research?", "A"),
        _item("5", "Was the data collected in a way that addressed the research "
                   "issue?", "A"),
        _item("6", "Has the relationship between researcher and participants been "
                   "adequately considered?", "A"),
        _item("7", "Have ethical issues been taken into consideration?", "B"),
        _item("8", "Was the data analysis sufficiently rigorous?", "B"),
        _item("9", "Is there a clear statement of findings?", "B"),
        _item("10", "How valuable is the research?", "C"),
    ],
}

_CASP_RCT = {
    "id": "casp-rct",
    "family": "CASP",
    "title": "CASP checklist: for randomised controlled trials (RCTs)",
    "version": "2024",
    "year": 2024,
    "scope": "primary_study",
    "url": ("https://casp-uk.net/casp-checklists/"
            "CASP-checklist-randomised-controlled-trials-RCT-2024.pdf"),
    "citation": ("Critical Appraisal Skills Programme (2024) CASP checklist: for "
                 "randomised controlled trials (RCTs). Available at: "
                 "https://casp-uk.net/casp-checklists/"
                 "CASP-checklist-randomised-controlled-trials-RCT-2024.pdf"),
    "license": _LICENSE_CASP,
    "response_options": _CASP_OPTIONS,
    "overall_judgement_options": ["high", "moderate", "low"],
    "aggregation": "no_score",
    "aggregation_notes": ["CASP does not assign a score; report each item."],
    "sections": [
        {"id": "A", "title": "Section A: Is the basic study design valid for a "
                             "randomised controlled trial?"},
        {"id": "B", "title": "Section B: Was the study methodologically sound?"},
        {"id": "C", "title": "Section C: What are the results?"},
        {"id": "D", "title": "Section D: Will the results help locally?"},
    ],
    "items": [
        _item("1", "Did the study address a clearly formulated research question?", "A"),
        _item("2", "Was the assignment of participants to interventions randomised?", "A"),
        _item("3", "Were all participants who entered the study accounted for at its "
                   "conclusion?", "A"),
        _item("4a", "Were the participants 'blind' to intervention they were given?", "B"),
        _item("4b", "Were the investigators 'blind' to the intervention they were "
                    "giving to participants?", "B"),
        _item("4c", "Were the people assessing/analysing outcome/s 'blinded'?", "B"),
        _item("5", "Were the study groups similar at the start of the randomised "
                   "controlled trial?", "B"),
        _item("6", "Apart from the experimental intervention, did each study group "
                   "receive the same level of care (that is, were they treated "
                   "equally)?", "B"),
        _item("7", "Were the effects of intervention reported comprehensively?", "C"),
        _item("8", "Was the precision of the estimate of the intervention or treatment "
                   "effect reported?", "C"),
        _item("9", "Do the benefits of the experimental intervention outweigh the harms "
                   "and costs?", "C"),
        _item("10", "Can the results be applied to your local population/in your "
                    "context?", "D"),
        _item("11", "Would the experimental intervention provide greater value to the "
                    "people in your care than any of the existing interventions?", "D"),
    ],
}

_CASP_SYSTEMATIC_REVIEW = {
    "id": "casp-systematic-review",
    "family": "CASP",
    "title": "CASP checklist: for systematic reviews",
    "version": "2024",
    "year": 2024,
    "scope": "review",
    "url": ("https://casp-uk.net/casp-checklists/"
            "CASP-checklist-systematic-reviews-checklist-2024.pdf"),
    "citation": ("Critical Appraisal Skills Programme (2024) CASP checklist: for "
                 "systematic reviews. Available at: "
                 "https://casp-uk.net/casp-checklists/"
                 "CASP-checklist-systematic-reviews-checklist-2024.pdf"),
    "license": _LICENSE_CASP,
    "response_options": _CASP_OPTIONS,
    "overall_judgement_options": ["high", "moderate", "low"],
    "aggregation": "no_score",
    "aggregation_notes": [
        "CASP does not assign a score; the tool's collation table lists responses "
        "item by item only.",
    ],
    "sections": [
        {"id": "A", "title": "Section A: Are the results of the review valid?"},
        {"id": "B", "title": "Section B: What are the results?"},
        {"id": "C", "title": "Section C: Will the results help locally?"},
    ],
    "items": [
        _item("1", "Did the review address a clearly focused question?", "A"),
        _item("2", "Did the authors look for the right type of papers?", "A"),
        _item("3", "Do you think all the important, relevant studies were included?", "A"),
        _item("4", "Did the review's authors do enough to assess quality of the "
                   "included studies?", "A"),
        _item("5", "If the results of the review have been combined, was it reasonable "
                   "to do so?", "A"),
        _item("6", "What are the overall results of the review?", "B"),
        _item("7", "How precise are the results?", "B"),
        _item("8", "Can the results be applied to the local population?", "C"),
        _item("9", "Were all important outcomes considered?", "C"),
        _item("10", "Are the benefits worth the harms and costs?", "C"),
    ],
}

_CASP_CASE_CONTROL = {
    "id": "casp-case-control",
    "family": "CASP",
    "title": "CASP checklist: for case control studies",
    "version": "October 2026",
    "year": 2026,
    "scope": "primary_study",
    "url": ("https://casp-uk.net/casp-checklists/"
            "CASP-checklist-case-control-study-October%202026.pdf"),
    "citation": ("Critical Appraisal Skills Programme (2026) CASP checklist: for case "
                 "control studies. Available at: "
                 "https://casp-uk.net/casp-checklists/"
                 "CASP-checklist-case-control-study-October%202026.pdf"),
    "license": _LICENSE_CASP,
    "response_options": _CASP_OPTIONS,
    "overall_judgement_options": ["high", "moderate", "low"],
    "aggregation": "no_score",
    "aggregation_notes": ["CASP does not assign a score; report each item."],
    "sections": [
        {"id": "A", "title": "Section A: Are the results of the study valid?"},
        {"id": "B", "title": "Section B: What are the results?"},
        {"id": "C", "title": "Section C: Will the results help locally?"},
    ],
    "items": [
        _item("1", "Did the study address a clearly focused issue?", "A"),
        _item("2", "Did the authors use an appropriate method to answer their "
                   "question?", "A"),
        _item("3", "Were the cases recruited in an acceptable way?", "A"),
        _item("4", "Were the controls selected in an acceptable way?", "A"),
        _item("5", "Was the exposure accurately measured to minimise bias?", "A"),
        _item("6a", "Have the authors identified the important potential confounding "
                    "factors?", "A"),
        _item("6b", "Have the authors taken account of the potential confounding "
                    "factors in the design and/or in their analysis?", "A"),
        _item("7", "Was the exposure effect large?", "B"),
        _item("8", "Was the estimate of the exposure effect precise?", "B"),
        _item("9", "Do you believe the results?", "B"),
        _item("10", "Can the results be applied to your patients/the population of "
                    "interest?", "C"),
        _item("11", "Do the results of this study fit with other available evidence?", "C"),
    ],
}

_CASP_CLINICAL_PREDICTION_RULE = {
    "id": "casp-clinical-prediction-rule",
    "family": "CASP",
    "title": "CASP checklist: for clinical prediction rule",
    "version": "2024",
    "year": 2024,
    "scope": "primary_study",
    "url": ("https://casp-uk.net/casp-checklists/"
            "CASP-checklist-clinical-prediction-rule-2024.pdf"),
    "citation": ("Critical Appraisal Skills Programme (2024) CASP checklist: for "
                 "clinical prediction rule. Available at: "
                 "https://casp-uk.net/casp-checklists/"
                 "CASP-checklist-clinical-prediction-rule-2024.pdf"),
    "license": _LICENSE_CASP,
    "response_options": _CASP_OPTIONS,
    "overall_judgement_options": ["high", "moderate", "low"],
    "aggregation": "no_score",
    "aggregation_notes": ["CASP does not assign a score; report each item."],
    "sections": [
        {"id": "A", "title": "Section A: Are the results of the study valid?"},
        {"id": "B", "title": "Section B: What are the results?"},
        {"id": "C", "title": "Section C: Will the results help locally?"},
    ],
    "items": [
        _item("1", "Is the Clinical Prediction Rule (CPR) clearly defined?", "A"),
        _item("2", "Did the population from which the rule was derived include an "
                   "appropriate spectrum of patients?", "A"),
        _item("3", "Was the rule validated in a different group of patients?", "A"),
        _item("4", "Were the predictor variables and the outcome evaluated in a "
                   "blinded fashion?", "A"),
        _item("5", "Were the predictor variables and the outcome evaluates in the whole "
                   "sample selected initially?", "A",
              note="Official PDF typo 'evaluates' is reproduced verbatim."),
        _item("6", "Are the statistical methods used to construct and validate the rule "
                   "clearly described?", "A"),
        _item("7", "Can the performance of the rule be calculated?", "B"),
        _item("8", "How precise was the estimate of the treatment effect? (did they try "
                   "to refine the rule with other variables to see whether the precision "
                   "could be improved or the rule simplified?)", "B"),
        _item("9", "Would the prediction rule be reliable and the results "
                   "interpretable if used for your patient?", "C"),
        _item("10", "Is the rule acceptable in your case?", "C"),
        _item("11", "Would the results of the rule modify your decision about the "
                    "management of the patient, or the information you can give to "
                    "him/her?", "C"),
    ],
}

_CASP_COHORT = {
    "id": "casp-cohort",
    "family": "CASP",
    "title": "CASP checklist: for cohort studies",
    "version": "2024",
    "year": 2024,
    "scope": "primary_study",
    "url": "https://casp-uk.net/casp-checklists/CASP-checklist-cohort-study-2024.pdf",
    "citation": ("Critical Appraisal Skills Programme (2024) CASP checklist: for cohort "
                 "studies. Available at: "
                 "https://casp-uk.net/casp-checklists/CASP-checklist-cohort-study-2024.pdf"),
    "license": _LICENSE_CASP,
    "response_options": _CASP_OPTIONS,
    "overall_judgement_options": ["high", "moderate", "low"],
    "aggregation": "no_score",
    "aggregation_notes": ["CASP does not assign a score; report each item."],
    "sections": [
        {"id": "A", "title": "Section A: Are the results valid?"},
        {"id": "B", "title": "Section B: What are the results?"},
        {"id": "C", "title": "Section C: Will the results help locally?"},
    ],
    "items": [
        _item("1", "Did the study address a clearly focused issue?", "A"),
        _item("2", "Was the cohort recruited in an acceptable way?", "A"),
        _item("3", "Was the exposure accurately measured to minimise bias?", "A"),
        _item("4", "Was the outcome accurately measured to minimise bias?", "A"),
        _item("5a", "Have the authors identified all important confounding factors?", "A"),
        _item("5b", "Have they taken account of the confounding factors in the design "
                    "and/or analysis?", "A"),
        _item("6a", "Was the follow up of subjects complete enough?", "A"),
        _item("6b", "Was the follow up of subjects long enough?", "A"),
        _item("7", "What are the results of this study?", "B"),
        _item("8", "How precise are the results?", "B"),
        _item("9", "Do you believe the results?", "B"),
        _item("10", "Can the results be applied to the local population?", "C"),
        _item("11", "Do the results of this study fit with other available evidence?", "C"),
        _item("12", "What are the implications of this study for practice?", "C"),
    ],
}

_CASP_CROSS_SECTIONAL = {
    "id": "casp-cross-sectional",
    "family": "CASP",
    "title": "CASP checklist: for descriptive/cross-sectional studies",
    "version": "2024",
    "year": 2024,
    "scope": "primary_study",
    "url": ("https://casp-uk.net/casp-checklists/"
            "CASP-checklist-cross-sectional-study-2024.pdf"),
    "citation": ("Critical Appraisal Skills Programme (2024) CASP checklist: for "
                 "descriptive/cross-sectional studies. Available at: "
                 "https://casp-uk.net/casp-checklists/"
                 "CASP-checklist-cross-sectional-study-2024.pdf"),
    "license": _LICENSE_CASP,
    "response_options": _CASP_OPTIONS,
    "overall_judgement_options": ["high", "moderate", "low"],
    "aggregation": "no_score",
    "aggregation_notes": [
        "CASP does not assign a score; report each item.",
        "The 2024 PDF lists all 11 items under a single Section A heading.",
    ],
    "sections": [
        {"id": "A", "title": "Section A: Are the results valid?"},
    ],
    "items": [
        _item("1", "Did the study address a clearly focused issue?", "A"),
        _item("2", "Did the authors use an appropriate method to answer their "
                   "question?", "A"),
        _item("3", "Were the subjects recruited in an acceptable way?", "A"),
        _item("4", "Were the measures accurately measured to reduce bias?", "A"),
        _item("5", "Were the data collected in a way that addressed the research "
                   "issue?", "A"),
        _item("6", "Did the study have enough participants to minimise the play of "
                   "chance?", "A"),
        _item("7", "How are the results presented and what is the main result?", "A"),
        _item("8", "Was the data analysis sufficiently rigorous?", "A"),
        _item("9", "Is there a clear statement of findings?", "A"),
        _item("10", "Can the results be applied to the local population?", "A"),
        _item("11", "How valuable is the research?", "A"),
    ],
}

_CASP_DIAGNOSTIC = {
    "id": "casp-diagnostic",
    "family": "CASP",
    "title": "CASP checklist: for diagnostic test studies",
    "version": "2024",
    "year": 2024,
    "scope": "primary_study",
    "url": "https://casp-uk.net/casp-checklists/CASP-checklist-diagnostic-test-2024.pdf",
    "citation": ("Critical Appraisal Skills Programme (2024) CASP checklist: for "
                 "diagnostic test studies. Available at: "
                 "https://casp-uk.net/casp-checklists/CASP-checklist-diagnostic-test-2024.pdf"),
    "license": _LICENSE_CASP,
    "response_options": _CASP_OPTIONS,
    "overall_judgement_options": ["high", "moderate", "low"],
    "aggregation": "no_score",
    "aggregation_notes": ["CASP does not assign a score; report each item."],
    "sections": [
        {"id": "A", "title": "Section A: Are the results of the study valid?"},
        {"id": "B", "title": "Section B: What are the results?"},
        {"id": "C", "title": "Section C: Will the results help locally?"},
    ],
    "items": [
        _item("1", "Did the study address a clearly formulated research question?", "A"),
        _item("2", "Was there a comparison with an appropriate reference standard?", "A"),
        _item("3", "Did all patients get the diagnostic test and reference standard?", "A"),
        _item("4", "Could the results of the test have been influenced by the results of "
                   "the reference standard?", "A"),
        _item("5", "Is the disease status of the tested population clearly described?", "A"),
        _item("6", "Were the methods for performing the test described in sufficient "
                   "detail?", "A"),
        _item("7", "What are the results?", "B"),
        _item("8", "How sure are we about the results?", "B"),
        _item("9", "Can the results be applied to your patients/the population of "
                   "interest?", "C"),
        _item("10", "Can the test be applied to your patient or population of "
                    "interest?", "C"),
        _item("11", "Were all outcomes important to the individual or population "
                    "considered?", "C"),
        _item("12", "What would be the impact of using this test on your "
                    "patients/population?", "C"),
    ],
}

_CASP_ECONOMIC = {
    "id": "casp-economic-evaluation",
    "family": "CASP",
    "title": "CASP checklist: for economic evaluations",
    "version": "2024",
    "year": 2024,
    "scope": "primary_study",
    "url": ("https://casp-uk.net/casp-checklists/"
            "CASP-checklist-economic-evaluation-2024.pdf"),
    "citation": ("Critical Appraisal Skills Programme (2024) CASP checklist: for "
                 "economic evaluations. Available at: "
                 "https://casp-uk.net/casp-checklists/"
                 "CASP-checklist-economic-evaluation-2024.pdf"),
    "license": _LICENSE_CASP,
    "response_options": _CASP_OPTIONS,
    "overall_judgement_options": ["high", "moderate", "low"],
    "aggregation": "no_score",
    "aggregation_notes": ["CASP does not assign a score; report each item."],
    "sections": [
        {"id": "A", "title": "Section A: Is the economic evaluation valid?"},
        {"id": "B", "title": "Section B: How were consequences and costs assessed and "
                             "compared?"},
        {"id": "C", "title": "Section C: Will the results help in purchasing for local "
                             "people?"},
    ],
    "items": [
        _item("1", "Was a well-defined question posed?", "A"),
        _item("2", "Was a comprehensive description of the competing alternatives "
                   "given?", "A"),
        _item("3", "Does the paper provide evidence that the programme would be "
                   "effective? (i.e. would the programme do more good than harm?)", "A"),
        _item("4", "Were the effects of the intervention identified, measured and "
                   "valued appropriately?", "A"),
        _item("5", "Were all important and relevant resources required, and health "
                   "outcome costs for each alternative identified, measured in "
                   "appropriate units and valued credibly?", "B"),
        _item("6", "Were costs and consequences adjusted for different times at which "
                   "they occurred (discounting)?", "B"),
        _item("7", "What were the results of the evaluation?", "B"),
        _item("8", "Was an incremental analysis of the consequences and cost of "
                   "alternatives performed?", "B"),
        _item("9", "Was an adequate sensitivity analysis performed?", "B"),
        _item("10", "Is the programme likely to be equally effective in your context or "
                    "setting?", "C"),
        _item("11", "Are the costs translatable to your setting?", "C"),
        _item("12", "Is it worth doing in your setting?", "C"),
    ],
}

_CASP_SR_META_RCT = {
    "id": "casp-systematic-review-meta-rct",
    "family": "CASP",
    "title": ("CASP checklist: for systematic reviews with meta-analysis of randomised "
              "controlled trials (RCTs)"),
    "version": "2024",
    "year": 2024,
    "scope": "review",
    "url": ("https://casp-uk.net/casp-checklists/"
            "CASP-checklist-systematic-reviews-meta-analysis-rct-checklist-2024.pdf"),
    "citation": ("Critical Appraisal Skills Programme (2024) CASP checklist: for "
                 "systematic reviews with meta-analysis of randomised controlled trials "
                 "(RCTs). Available at: "
                 "https://casp-uk.net/casp-checklists/"
                 "CASP-checklist-systematic-reviews-meta-analysis-rct-checklist-2024.pdf"),
    "license": _LICENSE_CASP,
    "response_options": _CASP_OPTIONS,
    "overall_judgement_options": ["high", "moderate", "low"],
    "aggregation": "no_score",
    "aggregation_notes": [
        "CASP does not assign a score; report each item and sub-item.",
        "Section A carries the official guidance: if both questions are answered 'No', "
        "consider whether it would be useful to continue the critical appraisal.",
    ],
    "sections": [
        {"id": "A", "title": "Section A: Is the basic study design valid for a "
                             "systematic review?"},
        {"id": "B", "title": "Section B: Is the systematic review methodologically "
                             "sound?"},
        {"id": "C", "title": "Section C: Are the results of the systematic review "
                             "trustworthy?"},
        {"id": "D", "title": "Section D: Are the results of the systematic review "
                             "relevant locally?"},
        {"id": "E", "title": "Section E: Will the implementation of the results "
                             "represent greater value for your service users or "
                             "population?"},
    ],
    "items": [
        _item("1", "Did the systematic review address a clearly formulated research "
                   "question?", "A"),
        _item("2", "Did the researchers search for appropriate study design(s) to "
                   "answer the research question?", "A"),
        _item("3a", "Searching for primary research studies", "B", number="3a",
              parent_text="Were all the relevant primary research studies likely to "
                          "have been included in the systematic review?"),
        _item("3b", "Screening primary research studies from the search", "B",
              number="3b",
              parent_text="Were all the relevant primary research studies likely to "
                          "have been included in the systematic review?"),
        _item("3c", "Selecting primary research studies to include in the systematic "
                    "review", "B", number="3c",
              parent_text="Were all the relevant primary research studies likely to "
                          "have been included in the systematic review?"),
        _item("4", "Did the researchers assess the validity or methodological rigour of "
                   "the primary research studies included in the systematic review?", "B"),
        _item("5a", "Extraction of data", "B", number="5a",
              parent_text="Did the researchers extract, and present information on the "
                          "individual primary research studies appropriately and "
                          "transparently?"),
        _item("5b", "Presentation of data", "B", number="5b",
              parent_text="Did the researchers extract, and present information on the "
                          "individual primary research studies appropriately and "
                          "transparently?"),
        _item("6.1", "Subgroup analysis", "C", number="6.1",
              parent_text="Did the researchers analyse the pooled results of the "
                          "individual primary research studies appropriately?"),
        _item("6.2", "Meta-regression", "C", number="6.2",
              parent_text="Did the researchers analyse the pooled results of the "
                          "individual primary research studies appropriately?"),
        _item("7.1", "Subgroup-analysis", "C", number="7.1",
              parent_text="Did the researchers report any limitations of the "
                          "systematic review and, if so, do the limitations discussed "
                          "cover all the issues in your critical appraisal?"),
        _item("7.2", "Meta-regression", "C", number="7.2",
              parent_text="Did the researchers report any limitations of the "
                          "systematic review and, if so, do the limitations discussed "
                          "cover all the issues in your critical appraisal?"),
        _item("8", "Would the benefits of intervention outweigh any potential "
                   "disadvantages, harms and/or additional demand for resources "
                   "associated with acting on the results?", "C"),
        _item("9", "Can the results of the systematic review be applied to your local "
                   "population/in your local setting or context?", "D"),
        _item("10", "If actioned, would the findings from the systematic review "
                    "represent greater or additional value for the individuals or "
                    "populations for whom you are responsible?", "E"),
    ],
}

_CASP_SR_META_OBS = {
    "id": "casp-systematic-review-meta-observational",
    "family": "CASP",
    "title": ("CASP checklist: for systematic reviews with meta-analysis of "
              "observational studies"),
    "version": "2024",
    "year": 2024,
    "scope": "review",
    "url": ("https://casp-uk.net/casp-checklists/"
            "CASP-checklist-systematic-reviews-observational-studies-checklist-2024.pdf"),
    "citation": ("Critical Appraisal Skills Programme (2024) CASP checklist: for "
                 "systematic reviews with meta-analysis of observational studies. "
                 "Available at: https://casp-uk.net/casp-checklists/"
                 "CASP-checklist-systematic-reviews-observational-studies-checklist-2024.pdf"),
    "license": _LICENSE_CASP,
    "response_options": _CASP_OPTIONS,
    "overall_judgement_options": ["high", "moderate", "low"],
    "aggregation": "no_score",
    "aggregation_notes": ["CASP does not assign a score; report each item and sub-item."],
    "sections": [
        {"id": "A", "title": "Section A: Is the basic study design valid for a "
                             "systematic review?"},
        {"id": "B", "title": "Section B: Is the systematic review methodologically "
                             "sound?"},
        {"id": "C", "title": "Section C: Are the results of the systematic review "
                             "trustworthy?"},
        {"id": "D", "title": "Section D: Are the results of the systematic review "
                             "relevant locally?"},
        {"id": "E", "title": "Section E: Will the implementation of the results "
                             "represent greater value for your service users or "
                             "population?"},
    ],
    "items": [
        _item("1", "Did the systematic review address a clearly formulated research "
                   "question?", "A"),
        _item("2", "Did the researchers search for appropriate study designs to answer "
                   "the research question?", "A"),
        _item("3a", "Searching for primary research studies", "B", number="3a",
              parent_text="Were all the relevant primary research studies likely to "
                          "have been included in the systematic review?"),
        _item("3b", "Screening primary research studies from the search", "B",
              number="3b",
              parent_text="Were all the relevant primary research studies likely to "
                          "have been included in the systematic review?"),
        _item("3c", "Selecting primary research studies to include in the systematic "
                    "review", "B", number="3c",
              parent_text="Were all the relevant primary research studies likely to "
                          "have been included in the systematic review?"),
        _item("4", "Did the researchers assess the validity or methodological rigour of "
                   "the primary research studies included in the systematic review?", "B"),
        _item("5a", "Extraction of data", "B", number="5a",
              parent_text="Did the researchers extract, and present information on the "
                          "individual primary research studies appropriately and "
                          "transparently?"),
        _item("5b", "Presentation of data", "B", number="5b",
              parent_text="Did the researchers extract, and present information on the "
                          "individual primary research studies appropriately and "
                          "transparently?"),
        _item("6.1", "Subgroup analysis", "C", number="6.1",
              parent_text="Did the researchers analyse the results of the individual "
                          "primary research studies appropriately?"),
        _item("6.2", "Meta-regression", "C", number="6.2",
              parent_text="Did the researchers analyse the results of the individual "
                          "primary research studies appropriately?"),
        _item("7.1", "Subgroup analysis", "C", number="7.1",
              parent_text="Did the researchers report any limitations of the "
                          "systematic review and, if so, do the limitations discussed "
                          "cover all the issues in your critical appraisal?"),
        _item("7.2", "Meta-regression", "C", number="7.2",
              parent_text="Did the researchers report any limitations of the "
                          "systematic review and, if so, do the limitations discussed "
                          "cover all the issues in your critical appraisal?"),
        _item("8", "Would the benefits of acting upon the results outweigh any "
                   "potential disadvantages, harms and/or additional demand for "
                   "resources associated with acting on the results?", "C"),
        _item("9", "Can the results of the systematic review be applied to your local "
                   "population/in your local setting or context?", "D"),
        _item("10", "If actioned, would the findings from the systematic review "
                    "represent greater or additional value for the individuals or "
                    "populations for whom you are responsible?", "E"),
    ],
}


# ---------------------------------------------------------------------------
# RoB 2（2019-08-22 版；CC BY-NC-ND，条目与算法逐字，禁改写）
# ---------------------------------------------------------------------------

_ROB2_D1 = {
    "id": "D1",
    "title": "Risk of bias arising from the randomization process",
    "algorithm_text": {
        "low": ("(i) The allocation sequence was adequately concealed AND (ii.1) Any "
                "baseline differences observed between intervention groups appear to "
                "be compatible with chance OR (ii.2) There is no information about "
                "baseline imbalances AND (iii.1) The allocation sequence was random OR "
                "(iii.2) There is no information about whether the allocation sequence "
                "was random"),
        "some_concerns": ("(i.1) The allocation sequence was adequately concealed AND "
                          "(i.2.1) The allocation sequence was not random OR (i.2.2) "
                          "Baseline differences between intervention groups suggest a "
                          "problem with the randomization process OR (ii.1) There is no "
                          "information about concealment of the allocation sequence AND "
                          "(ii.2) Any baseline differences observed between intervention "
                          "groups appear to be compatible with chance OR (iii) There is "
                          "no information to answer any of the signalling questions"),
        "high": ("(i) The allocation sequence was not adequately concealed OR (ii.1) "
                 "There is no information about concealment of the allocation sequence "
                 "AND (ii.2) Baseline differences between intervention groups suggest a "
                 "problem with the randomization process"),
    },
    "items": [
        _item("1.1", "Was the allocation sequence random?", "D1",
              options=["Y", "PY", "PN", "N", "NI"]),
        _item("1.2", "Was the allocation sequence concealed until participants were "
                     "enrolled and assigned to interventions?", "D1",
              options=["Y", "PY", "PN", "N", "NI"]),
        _item("1.3", "Did baseline differences between intervention groups suggest a "
                     "problem with the randomization process?", "D1",
              options=["Y", "PY", "PN", "N", "NI"]),
    ],
}

_ROB2_D2_ASSIGNMENT = [
    _item("2.1", "Were participants aware of their assigned intervention during the "
                 "trial?", "D2", options=["Y", "PY", "PN", "N", "NI"]),
    _item("2.2", "Were carers and people delivering the interventions aware of "
                 "participants' assigned intervention during the trial?", "D2",
          options=["Y", "PY", "PN", "N", "NI"]),
    _item("2.3", "If Y/PY/NI to 2.1 or 2.2: Were there deviations from the intended "
                 "intervention that arose because of the trial context?", "D2",
          options=_ROB2_OPTIONS),
    _item("2.4", "If Y/PY/NI to 2.3: Were these deviations likely to have affected the "
                 "outcome?", "D2", options=_ROB2_OPTIONS),
    _item("2.5", "If Y/PY to 2.4: Were these deviations from intended intervention "
                 "balanced between groups?", "D2", options=_ROB2_OPTIONS),
    _item("2.6", "Was an appropriate analysis used to estimate the effect of assignment "
                 "to intervention?", "D2", options=["Y", "PY", "PN", "N", "NI"]),
    _item("2.7", "If N/PN/NI to 2.6: Was there potential for a substantial impact (on "
                 "the result) of the failure to analyse participants in the group to "
                 "which they were randomized?", "D2", options=_ROB2_OPTIONS),
]

_ROB2_D2_ADHERING = [
    _item("2.1", "Were participants aware of their assigned intervention during the "
                 "trial?", "D2", options=["Y", "PY", "PN", "N", "NI"]),
    _item("2.2", "Were carers and people delivering the interventions aware of "
                 "participants' assigned intervention during the trial?", "D2",
          options=["Y", "PY", "PN", "N", "NI"]),
    _item("2.3", "[If applicable:] If Y/PY/NI to 2.1 or 2.2: Were important "
                 "non-protocol interventions balanced across intervention groups?",
          "D2", options=_ROB2_OPTIONS),
    _item("2.4", "[If applicable:] Were there failures in implementing the intervention "
                 "that could have affected the outcome?", "D2", options=_ROB2_OPTIONS),
    _item("2.5", "[If applicable:] Was there non-adherence to the assigned intervention "
                 "regimen that could have affected participants' outcomes?", "D2",
          options=_ROB2_OPTIONS),
    _item("2.6", "If N/PN/NI to 2.3, or Y/PY/NI to 2.4 or 2.5: Was an appropriate "
                 "analysis used to estimate the effect of adhering to the "
                 "intervention?", "D2", options=_ROB2_OPTIONS),
]

_ROB2_DOMAIN2 = {
    "id": "D2",
    "title": "Risk of bias due to deviations from the intended interventions",
    "default_variant": "assignment",
    "variants": {
        "assignment": {
            "label": "(effect of assignment to intervention)",
            "algorithm_text": {
                "low": {
                    "part_1": ("(i) Participants, carers and people delivering the "
                               "interventions were unaware of intervention groups during "
                               "the trial OR (ii.1) Participants, carers or people "
                               "delivering the interventions were aware of intervention "
                               "groups during the trial AND (ii.2) No deviations from "
                               "intended intervention arose because of the trial "
                               "context."),
                    "part_2": ("An appropriate analysis was used to estimate the effect "
                               "of assignment to intervention"),
                    "domain": ("'Low' risk of bias for Part 1 AND 'Low' risk of bias "
                               "for Part 2"),
                },
                "some_concerns": {
                    "part_1": ("(i) Participants, carers or people delivering the "
                               "interventions were aware of intervention groups during "
                               "the trial AND (ii.1) There is no information on whether "
                               "there were deviations from intended intervention because "
                               "of the trial context OR (ii.1.1) There were deviations "
                               "from intended interventions that arose because of the "
                               "trial context AND (ii.1.1.1) These deviations were not "
                               "likely to have affected the outcome OR (ii.1.1.2) These "
                               "deviations were balanced between the intervention "
                               "groups"),
                    "part_2": ("(i) An appropriate analysis was not used to estimate the "
                               "effect of assignment to intervention AND (ii) The "
                               "potential impact (on the estimated effect of "
                               "intervention) of the failure to analyse participants in "
                               "the group to which they were randomized was not "
                               "substantial"),
                    "domain": ("'Some concerns' for Part 1 OR 'Some concerns' for Part 2 "
                               "AND Part 1 not 'High' risk of bias AND Part 2 not "
                               "'High' risk of bias"),
                },
                "high": {
                    "part_1": ("(i) Participants, carers or people delivering the "
                               "interventions were aware of intervention groups during "
                               "the trial AND (ii) There were deviations from intended "
                               "interventions that arose because of the trial context "
                               "AND (iii) These deviations were likely to have affected "
                               "the outcome AND (iv) These deviations were unbalanced "
                               "between the intervention groups"),
                    "part_2": ("(i) An appropriate analysis was not used to estimate the "
                               "effect of assignment to intervention AND (ii) The "
                               "potential impact (on the estimated effect of "
                               "intervention) of the failure to analyse participants in "
                               "the group to which they were randomized was "
                               "substantial"),
                    "domain": ("'High' risk of bias for Part 1 OR 'High' risk of bias "
                               "for Part 2"),
                },
            },
            "items": _ROB2_D2_ASSIGNMENT,
        },
        "adhering": {
            "label": "(effect of adhering to intervention)",
            "algorithm_text": {
                "low": ("(i.1) Participants, carers and people delivering the "
                        "interventions were unaware of intervention groups during the "
                        "trial OR (i.2.1) Participants, carers or people delivering the "
                        "interventions were aware of intervention groups AND (i.2.2) [If "
                        "applicable] The important non-protocol interventions were "
                        "balanced across intervention groups AND (ii) [If applicable] "
                        "Failures in implementing the intervention could not have "
                        "affected the outcome AND (iii) [If applicable] Study "
                        "participants adhered to the assigned intervention regimen"),
                "some_concerns": ("(i.1.1) Participants, carers and people delivering "
                                  "the interventions were unaware of intervention groups "
                                  "during the trial AND (i.1.2.1) [If applicable] "
                                  "Failures in implementing the intervention could have "
                                  "affected the outcome OR (i.1.2.2) [If applicable] "
                                  "Study participants did not adhere to the assigned "
                                  "intervention regimen OR (i.2.1) Participants, carers "
                                  "or people delivering the interventions were aware of "
                                  "intervention groups AND (i.2.2) [If applicable] The "
                                  "important non-protocol interventions were balanced "
                                  "across intervention groups AND (i.2.3.1) [If "
                                  "applicable] Failures in implementing the intervention "
                                  "could have affected the outcome OR (i.2.3.2) [If "
                                  "applicable] Study participants did not adhere to the "
                                  "assigned intervention regimen OR (i.3.1) "
                                  "Participants, carers or people delivering the "
                                  "interventions were aware of intervention groups AND "
                                  "(i.3.2) [If applicable] The important non-protocol "
                                  "interventions were not balanced across intervention "
                                  "groups AND (ii) An appropriate analysis was used to "
                                  "estimate the effect of adhering to the intervention"),
                "high": ("(i.1.1) Participants, carers and people delivering the "
                         "interventions were unaware of intervention groups during the "
                         "trial AND (i.1.2.1) [If applicable] Failures in implementing "
                         "the intervention could have affected the outcome OR (i.1.2.2) "
                         "[If applicable] Study participants did not adhere to the "
                         "assigned intervention regimen OR (i.2.1) Participants, carers "
                         "or people delivering the interventions were aware of "
                         "intervention groups AND (i.2.2) [If applicable] The important "
                         "non-protocol interventions were balanced across intervention "
                         "groups AND (i.2.3.1) [If applicable] Failures in implementing "
                         "the intervention could have affected the outcome OR "
                         "(i.2.3.2) [If applicable] Study participants did not adhere "
                         "to the assigned intervention regimen OR (i.3.1) Participants, "
                         "carers or people delivering the interventions were aware of "
                         "intervention groups AND (i.3.2) [If applicable] The important "
                         "non-protocol interventions were not balanced across "
                         "intervention groups AND (ii) An appropriate analysis was not "
                         "used to estimate the effect of adhering to the intervention"),
            },
            "items": _ROB2_D2_ADHERING,
        },
    },
}

_ROB2_D3 = {
    "id": "D3",
    "title": "Risk of bias due to missing outcome data",
    "algorithm_text": {
        "low": ("(i) Outcome data were available for all, or nearly all, randomized "
                "participants OR (ii) There is evidence that the result was not biased "
                "by missing outcome data OR (iii) Missingness in the outcome could not "
                "depend on its true value"),
        "some_concerns": ("(i) Outcome data were not available for all, or nearly all, "
                          "randomized participants AND (ii) There is not evidence that "
                          "the result was not biased by missing outcome data AND (iii) "
                          "Missingness in the outcome could depend on its true value AND "
                          "(iv) It is not likely that missingness in the outcome "
                          "depended on its true value"),
        "high": ("(i) Outcome data were not available for all, or nearly all, randomized "
                 "participants AND (ii) There is not evidence that the result was not "
                 "biased by missing outcome data AND (iii) Missingness in the outcome "
                 "could depend on its true value AND (iv) It is likely that missingness "
                 "in the outcome depended on its true value."),
    },
    "items": [
        _item("3.1", "Were data for this outcome available for all, or nearly all, "
                     "participants randomized?", "D3",
              options=["Y", "PY", "PN", "N", "NI"]),
        _item("3.2", "If N/PN/NI to 3.1: Is there evidence that the result was not "
                     "biased by missing outcome data?", "D3",
              options=_ROB2_OPTIONS_NO_NI),
        _item("3.3", "If N/PN to 3.2: Could missingness in the outcome depend on its "
                     "true value?", "D3", options=_ROB2_OPTIONS),
        _item("3.4", "If Y/PY/NI to 3.3: Is it likely that missingness in the outcome "
                     "depended on its true value?", "D3", options=_ROB2_OPTIONS),
    ],
}

_ROB2_D4 = {
    "id": "D4",
    "title": "Risk of bias in measurement of the outcome",
    "algorithm_text": {
        "low": ("(i) The method of measuring the outcome was not inappropriate AND (ii) "
                "The measurement or ascertainment of the outcome did not differ between "
                "intervention groups AND (iii.1) The outcome assessors were unaware of "
                "the intervention received by study participants OR (iii.2) The "
                "assessment of the outcome could not have been influenced by knowledge "
                "of the intervention received"),
        "some_concerns": ("(i.1) The method of measuring the outcome was not "
                          "inappropriate AND (i.2) The measurement or ascertainment of "
                          "the outcome did not differ between intervention groups AND "
                          "(i.3) The assessment of the outcome could have been "
                          "influenced by knowledge of the intervention received AND "
                          "(i.4) It is unlikely that assessment of the outcome was "
                          "influenced by knowledge of intervention received OR (ii.1) "
                          "The method of measuring the outcome was not inappropriate "
                          "AND (ii.2) There is no information on whether the measurement "
                          "or ascertainment of the outcome could have differed between "
                          "intervention groups AND (ii.3.1) The outcome assessors were "
                          "unaware of the intervention received by study participants OR "
                          "(ii.3.2) The assessment of the outcome could not have been "
                          "influenced by knowledge of the intervention received"),
        "high": ("(i) The method of measuring the outcome was inappropriate OR (ii) The "
                 "measurement or ascertainment of the outcome could have differed "
                 "between intervention groups OR (iii) It is likely that assessment of "
                 "the outcome was influenced by knowledge of the intervention received"),
    },
    "items": [
        _item("4.1", "Was the method of measuring the outcome inappropriate?", "D4",
              options=["Y", "PY", "PN", "N", "NI"]),
        _item("4.2", "Could measurement or ascertainment of the outcome have differed "
                     "between intervention groups?", "D4",
              options=["Y", "PY", "PN", "N", "NI"]),
        _item("4.3", "If N/PN/NI to 4.1 and 4.2: Were outcome assessors aware of the "
                     "intervention received by study participants?", "D4",
              options=["Y", "PY", "PN", "N", "NI"]),
        _item("4.4", "If Y/PY/NI to 4.3: Could assessment of the outcome have been "
                     "influenced by knowledge of intervention received?", "D4",
              options=_ROB2_OPTIONS),
        _item("4.5", "If Y/PY/NI to 4.4: Is it likely that assessment of the outcome "
                     "was influenced by knowledge of intervention received?", "D4",
              options=_ROB2_OPTIONS),
    ],
}

_ROB2_D5 = {
    "id": "D5",
    "title": "Risk of bias in selection of the reported result",
    "lead_in": ("Is the numerical result being assessed likely to have been selected, "
                "on the basis of the results, from..."),
    "algorithm_text": {
        "low": ("(i) The data were analysed in accordance with a pre-specified plan that "
                "was finalized before unblinded outcome data were available for analysis "
                "AND (ii) The result being assessed is unlikely to have been selected, "
                "on the basis of the results, from multiple outcome measurements (e.g. "
                "scales, definitions, time points) within the outcome domain AND (iii) "
                "Reported outcome data are unlikely to have been selected, on the basis "
                "of the results, from multiple analyses of the data"),
        "some_concerns": ("(i.1) The data were not analysed in accordance with a "
                          "pre-specified plan that was finalized before unblinded outcome "
                          "data were available for analysis AND (i.2) The result being "
                          "assessed is unlikely to have been selected, on the basis of "
                          "the results, from multiple outcome measurements (e.g. scales, "
                          "definitions, time points) within the outcome domain AND (i.3) "
                          "The result being assessed is unlikely to have been selected, "
                          "on the basis of the results, from multiple analyses of the "
                          "data OR (ii)There is no information on whether the result "
                          "being assessed is likely to have been selected, on the basis "
                          "of the results, from multiple outcome measurements (e.g. "
                          "scales, definitions, time points) within the outcome domain "
                          "AND from multiple analyses of the data"),
        "high": ("(i) The result being assessed is likely to have been selected, on the "
                 "basis of the results, from multiple outcome measurements (e.g. "
                 "scales, definitions, time points) within the outcome domain OR (ii) "
                 "The result being assessed is likely to have been selected, on the "
                 "basis of the results, from multiple analyses of the data"),
    },
    "items": [
        _item("5.1", "Were the data that produced this result analysed in accordance "
                     "with a pre-specified analysis plan that was finalized before "
                     "unblinded outcome data were available for analysis?", "D5",
              options=["Y", "PY", "PN", "N", "NI"]),
        _item("5.2", "... multiple eligible outcome measurements (e.g. scales, "
                     "definitions, time points) within the outcome domain?", "D5",
              options=["Y", "PY", "PN", "N", "NI"]),
        _item("5.3", "... multiple eligible analyses of the data?", "D5",
              options=["Y", "PY", "PN", "N", "NI"]),
    ],
}

_ROB2 = {
    "id": "rob2",
    "family": "RoB 2",
    "title": ("RoB 2: a revised tool for assessing risk of bias in randomised trials "
              "(individually-randomized, parallel-group trials)"),
    "version": "22 August 2019",
    "year": 2019,
    "scope": "primary_study",
    "url": "https://www.riskofbias.info/welcome/rob-2-0-tool/current-version-of-rob-2",
    "citation": ("Sterne JAC, Savović J, Page MJ, Elbers RG, Blencowe NS, Boutron I, "
                 "Cates CJ, Cheng HY, Corbett MS, Eldridge SM, Emberson JR, Hernán MA, "
                 "Hopewell S, Hróbjartsson A, Junqueira DR, Jüni P, Kirkham JJ, "
                 "Lasserson T, Li T, McAleenan A, Reeves BC, Shepperd S, Shrier I, "
                 "Stewart LA, Tilling K, White IR, Whiting PF, Higgins JPT. RoB 2: a "
                 "revised tool for assessing risk of bias in randomised trials. BMJ. "
                 "2019;366:l4898. doi:10.1136/bmj.l4898"),
    "license": _LICENSE_ROB2,
    "response_options": _ROB2_OPTIONS,
    "response_option_labels": {
        "Y": "yes", "PY": "probably yes", "PN": "probably no",
        "N": "no", "NA": "not applicable", "NI": "no information",
    },
    "judgement_options": ["low", "some_concerns", "high"],
    "judgement_labels": {
        "low": "Low risk of bias",
        "some_concerns": "Some concerns",
        "high": "High risk of bias",
    },
    "direction_options": _ROB2_DIRECTION,
    "default_variant": "assignment",
    "overall_algorithm_text": {
        "low": "The study is judged to be at low risk of bias for all domains for this result",
        "some_concerns": ("The study is judged to raise some concerns in at least one "
                          "domain for this result, but not to be at high risk of bias "
                          "for any domain"),
        "high": ("The study is judged to be at high risk of bias in at least one domain "
                 "for this result, or the study is judged to have some concerns for "
                 "multiple domains in a way that substantially lowers confidence in the "
                 "result"),
        "source": ("Approach to reaching an overall risk-of-bias judgment for a "
                   "specific result"),
        "source_note": "BMJ 2019 Table 3",
    },
    "aggregation": "rob2_domains",
    "aggregation_notes": [
        "Domain judgments follow the official algorithms; the overall judgment "
        "generally corresponds to the worst domain.",
        "High overall also applies when multiple domains have some concerns in a way "
        "that substantially lowers confidence: official guidance gives no threshold, "
        "so this requires the appraiser's judgement (manual upgrade with a reason).",
        "Algorithms give a proposed judgment; appraisers may overturn it.",
    ],
    "domains": [_ROB2_D1, _ROB2_DOMAIN2, _ROB2_D3, _ROB2_D4, _ROB2_D5],
}


# ---------------------------------------------------------------------------
# MMAT 2018（2 个筛选问 + 5 类 × 5 条；官方不打总分）
# ---------------------------------------------------------------------------

_MMAT = {
    "id": "mmat",
    "family": "MMAT",
    "title": "Mixed Methods Appraisal Tool (MMAT), version 2018",
    "version": "2018 (Last update: August 1st, 2018)",
    "year": 2018,
    "scope": "primary_study",
    "url": "http://mixedmethodsappraisaltoolpublic.pbworks.com/",
    "citation": ("Hong QN, Pluye P, Fàbregues S, Bartlett G, Boardman F, Cargo M, "
                 "Dagenais P, Gagnon M-P, Griffiths F, Nicolau B, O'Cathain A, "
                 "Rousseau M-C, Vedel I. Mixed Methods Appraisal Tool (MMAT), version "
                 "2018. Registration of Copyright (#1148552), Canadian Intellectual "
                 "Property Office, Industry Canada."),
    "license": _LICENSE_MMAT,
    "response_options": _MMAT_OPTIONS,
    "overall_judgement_options": [],
    "aggregation": "no_score",
    "aggregation_notes": [
        "It is discouraged to calculate an overall score from the ratings of each "
        "criterion; provide a detailed presentation of the ratings of each criterion.",
        "Use the screening questions first: further appraisal may not be feasible or "
        "appropriate when the answer is 'No' or 'Can't tell' to one or both.",
        "Mixed methods studies use the qualitative category + one quantitative "
        "category + the mixed methods category.",
        "Do not use the MMAT for economic or diagnostic accuracy studies.",
    ],
    "sections": [
        {"id": "S", "title": "Screening questions"},
        {"id": "1", "title": "1. Qualitative"},
        {"id": "2", "title": "2. Quantitative randomized controlled trials"},
        {"id": "3", "title": "3. Quantitative non-randomized"},
        {"id": "4", "title": "4. Quantitative descriptive"},
        {"id": "5", "title": "5. Mixed methods"},
    ],
    "items": [
        _item("S1", "Are there clear research questions?", "S"),
        _item("S2", "Do the collected data allow to address the research questions?", "S"),
        _item("1.1", "Is the qualitative approach appropriate to answer the research "
                     "question?", "1"),
        _item("1.2", "Are the qualitative data collection methods adequate to address "
                     "the research question?", "1"),
        _item("1.3", "Are the findings adequately derived from the data?", "1"),
        _item("1.4", "Is the interpretation of results sufficiently substantiated by "
                     "data?", "1"),
        _item("1.5", "Is there coherence between qualitative data sources, collection, "
                     "analysis and interpretation?", "1"),
        _item("2.1", "Is randomization appropriately performed?", "2"),
        _item("2.2", "Are the groups comparable at baseline?", "2"),
        _item("2.3", "Are there complete outcome data?", "2"),
        _item("2.4", "Are outcome assessors blinded to the intervention provided?", "2"),
        _item("2.5", "Did the participants adhere to the assigned intervention?", "2"),
        _item("3.1", "Are the participants representative of the target population?", "3"),
        _item("3.2", "Are measurements appropriate regarding both the outcome and "
                     "intervention (or exposure)?", "3"),
        _item("3.3", "Are there complete outcome data?", "3"),
        _item("3.4", "Are the confounders accounted for in the design and analysis?", "3"),
        _item("3.5", "During the study period, is the intervention administered (or "
                     "exposure occurred) as intended?", "3"),
        _item("4.1", "Is the sampling strategy relevant to address the research "
                     "question?", "4"),
        _item("4.2", "Is the sample representative of the target population?", "4"),
        _item("4.3", "Are the measurements appropriate?", "4"),
        _item("4.4", "Is the risk of nonresponse bias low?", "4"),
        _item("4.5", "Is the statistical analysis appropriate to answer the research "
                     "question?", "4"),
        _item("5.1", "Is there an adequate rationale for using a mixed methods design "
                     "to address the research question?", "5"),
        _item("5.2", "Are the different components of the study effectively integrated "
                     "to answer the research question?", "5"),
        _item("5.3", "Are the outputs of the integration of qualitative and quantitative "
                     "components adequately interpreted?", "5"),
        _item("5.4", "Are divergences and inconsistencies between quantitative and "
                     "qualitative results adequately addressed?", "5"),
        _item("5.5", "Do the different components of the study adhere to the quality "
                     "criteria of each tradition of the methods involved?", "5"),
    ],
}


# ---------------------------------------------------------------------------
# AXIS（20 条；官方无数值刻度）
# ---------------------------------------------------------------------------

_AXIS = {
    "id": "axis",
    "family": "AXIS",
    "title": "AXIS tool (Appraisal tool for Cross-Sectional Studies)",
    "version": "2016",
    "year": 2016,
    "scope": "primary_study",
    "url": "https://bmjopen.bmj.com/content/6/12/e011458",
    "citation": ("Downes MJ, Brennan ML, Williams HC, Dean RS. Development of a "
                 "critical appraisal tool to assess the quality of cross-sectional "
                 "studies (AXIS). BMJ Open. 2016;6(12):e011458. "
                 "doi:10.1136/bmjopen-2016-011458"),
    "license": _LICENSE_AXIS,
    "response_options": _AXIS_OPTIONS,
    "response_option_source": "Yes | No | Do not know/ comment",
    "comments_supported": True,
    "overall_judgement_options": ["high", "moderate", "low"],
    "aggregation": "no_score",
    "aggregation_notes": [
        "As the tool does not provide a numerical scale for assessing the quality of "
        "the study, a degree of subjective assessment is required.",
        "A numerical scale to reflect quality was not included in the final tool.",
        "Item 19 is reverse-direction: answering Yes means a possible funding or "
        "conflict-of-interest concern.",
        "Items 14 and 20 carry 'if appropriate / attained' semantics.",
    ],
    "sections": [
        {"id": "Introduction", "title": "Introduction"},
        {"id": "Methods", "title": "Methods"},
        {"id": "Results", "title": "Results"},
        {"id": "Discussion", "title": "Discussion"},
        {"id": "Other", "title": "Other"},
    ],
    "items": [
        _item("1", "Were the aims/objectives of the study clear?", "Introduction"),
        _item("2", "Was the study design appropriate for the stated aim(s)?", "Methods"),
        _item("3", "Was the sample size justified?", "Methods"),
        _item("4", "Was the target/reference population clearly defined? (Is it clear "
                   "who the research was about?)", "Methods"),
        _item("5", "Was the sample frame taken from an appropriate population base so "
                   "that it closely represented the target/reference population under "
                   "investigation?", "Methods"),
        _item("6", "Was the selection process likely to select subjects/participants "
                   "that were representative of the target/reference population under "
                   "investigation?", "Methods"),
        _item("7", "Were measures undertaken to address and categorise non-responders?",
              "Methods"),
        _item("8", "Were the risk factor and outcome variables measured appropriate to "
                   "the aims of the study?", "Methods"),
        _item("9", "Were the risk factor and outcome variables measured correctly using "
                   "instruments/measurements that had been trialled, piloted or "
                   "published previously?", "Methods"),
        _item("10", "Is it clear what was used to determined statistical significance "
                    "and/or precision estimates? (eg, p values, CIs)", "Methods",
              note="Official PDF typo 'determined' is reproduced verbatim."),
        _item("11", "Were the methods (including statistical methods) sufficiently "
                    "described to enable them to be repeated?", "Methods"),
        _item("12", "Were the basic data adequately described?", "Results"),
        _item("13", "Does the response rate raise concerns about non-response bias?",
              "Results"),
        _item("14", "If appropriate, was information about non-responders described?",
              "Results"),
        _item("15", "Were the results internally consistent?", "Results"),
        _item("16", "Were the results for the analyses described in the methods, "
                    "presented?", "Results"),
        _item("17", "Were the authors' discussions and conclusions justified by the "
                    "results?", "Discussion"),
        _item("18", "Were the limitations of the study discussed?", "Discussion"),
        _item("19", "Were there any funding sources or conflicts of interest that may "
                    "affect the authors' interpretation of the results?", "Other",
              reverse_direction=True),
        _item("20", "Was ethical approval or consent of participants attained?", "Other"),
    ],
}


# ---------------------------------------------------------------------------
# AMSTAR 2（16 条 + 7 关键域 + Box 2 四级置信度）
# ---------------------------------------------------------------------------

_AMSTAR2_CRITICAL_ITEMS = ("2", "4", "7", "9", "11", "13", "15")

_AMSTAR2 = {
    "id": "amstar2",
    "family": "AMSTAR 2",
    "title": "AMSTAR 2: a critical appraisal tool for systematic reviews",
    "version": "2017",
    "year": 2017,
    "scope": "review",
    "url": "https://www.bmj.com/content/358/bmj.j4008",
    "citation": ("Shea BJ, Reeves BC, Wells G, Thuku M, Hamel C, Moran J, Moher D, "
                 "Tugwell P, Welch V, Kristjansson E, Henry DA. AMSTAR 2: a critical "
                 "appraisal tool for systematic reviews that include randomised or "
                 "non-randomised studies of healthcare interventions, or both. BMJ. "
                 "2017;358:j4008. doi:10.1136/bmj.j4008"),
    "license": _LICENSE_AMSTAR2,
    "response_options": _AMSTAR_OPTIONS_PARTIAL,
    "aggregation": "amstar2_confidence",
    "critical_items": list(_AMSTAR2_CRITICAL_ITEMS),
    "box1_text": [
        "Protocol registered before commencement of the review (item 2)",
        "Adequacy of the literature search (item 4)",
        "Justification for excluding individual studies (item 7)",
        "Risk of bias from individual studies being included in the review (item 9)",
        "Appropriateness of meta-analytical methods (item 11)",
        "Consideration of risk of bias when interpreting the results of the review "
        "(item 13)",
        "Assessment of presence and likely impact of publication bias (item 15)",
    ],
    "box2_text": {
        "high": {
            "criteria": "No or one non-critical weakness",
            "meaning": ("the systematic review provides an accurate and comprehensive "
                        "summary of the results of the available studies that address "
                        "the question of interest"),
        },
        "moderate": {
            "criteria": "More than one non-critical weakness*",
            "meaning": ("the systematic review has more than one weakness but no "
                        "critical flaws. It may provide an accurate summary of the "
                        "results of the available studies that were included in the "
                        "review"),
        },
        "low": {
            "criteria": "One critical flaw with or without non-critical weaknesses",
            "meaning": ("the review has a critical flaw and may not provide an accurate "
                        "and comprehensive summary of the available studies that address "
                        "the question of interest"),
        },
        "critically_low": {
            "criteria": ("More than one critical flaw with or without non-critical "
                         "weaknesses"),
            "meaning": ("the review has more than one critical flaw and should not be "
                        "relied on to provide an accurate and comprehensive summary of "
                        "the available studies"),
        },
        "footnote": ("*Multiple non-critical weaknesses may diminish confidence in the "
                     "review and it may be appropriate to move the overall appraisal "
                     "down from moderate to low confidence"),
    },
    "judgement_options": ["high", "moderate", "low", "critically_low"],
    "judgement_labels": {
        "high": "High",
        "moderate": "Moderate",
        "low": "Low",
        "critically_low": "Critically low",
    },
    "aggregation_notes": [
        "AMSTAR 2 is not designed to generate an overall 'score'.",
        "Box 1 critical domains: items 2, 4, 7, 9, 11, 13, 15.",
        "A critical flaw is a 'No' on a critical item; item 11/15 answered 'No "
        "meta-analysis conducted' is not applicable and is not a flaw.",
        "Box 2: High = no or one non-critical weakness; Moderate = more than one "
        "non-critical weakness; Low = one critical flaw with or without non-critical "
        "weaknesses; Critically low = more than one critical flaw with or without "
        "non-critical weaknesses.",
        "Multiple non-critical weaknesses may warrant a manual move from moderate to "
        "low confidence.",
        "Appraisers may add or substitute critical domains; this is a suggestion.",
    ],
    "items": [
        _item("1", "Did the research questions and inclusion criteria for the review "
                   "include the components of PICO?", "items",
              options=_AMSTAR_OPTIONS_BASIC),
        _item("2", "Did the report of the review contain an explicit statement that the "
                   "review methods were established prior to the conduct of the review "
                   "and did the report justify any significant deviations from the "
                   "protocol?", "items", options=_AMSTAR_OPTIONS_PARTIAL, critical=True),
        _item("3", "Did the review authors explain their selection of the study designs "
                   "for inclusion in the review?", "items",
              options=_AMSTAR_OPTIONS_BASIC),
        _item("4", "Did the review authors use a comprehensive literature search "
                   "strategy?", "items", options=_AMSTAR_OPTIONS_PARTIAL, critical=True),
        _item("5", "Did the review authors perform study selection in duplicate?",
              "items", options=_AMSTAR_OPTIONS_BASIC),
        _item("6", "Did the review authors perform data extraction in duplicate?",
              "items", options=_AMSTAR_OPTIONS_BASIC),
        _item("7", "Did the review authors provide a list of excluded studies and "
                   "justify the exclusions?", "items",
              options=_AMSTAR_OPTIONS_PARTIAL, critical=True),
        _item("8", "Did the review authors describe the included studies in adequate "
                   "detail?", "items", options=_AMSTAR_OPTIONS_PARTIAL),
        _item("9", "Did the review authors use a satisfactory technique for assessing "
                   "the risk of bias (RoB) in individual studies that were included in "
                   "the review?", "items", options=_AMSTAR_OPTIONS_PARTIAL,
              critical=True,
              columns=["RCTs", "NRSI"],
              column_markers=["Includes only NRSI", "Includes only RCTs"],
              note=("The official instrument has separate RCT and NRSI columns plus "
                    "'Includes only NRSI' / 'Includes only RCTs' markers; record the "
                    "answer for the applicable column.")),
        _item("10", "Did the review authors report on the sources of funding for the "
                    "studies included in the review?", "items",
              options=_AMSTAR_OPTIONS_BASIC),
        _item("11", "If meta-analysis was performed did the review authors use "
                    "appropriate methods for statistical combination of results?",
              "items", options=_AMSTAR_OPTIONS_META, critical=True,
              columns=["RCTs", "NRSI"]),
        _item("12", "If meta-analysis was performed, did the review authors assess the "
                    "potential impact of RoB in individual studies on the results of "
                    "the meta-analysis or other evidence synthesis?", "items",
              options=_AMSTAR_OPTIONS_META),
        _item("13", "Did the review authors account for RoB in individual studies when "
                    "interpreting/discussing the results of the review?", "items",
              options=_AMSTAR_OPTIONS_BASIC, critical=True),
        _item("14", "Did the review authors provide a satisfactory explanation for, and "
                    "discussion of, any heterogeneity observed in the results of the "
                    "review?", "items", options=_AMSTAR_OPTIONS_BASIC),
        _item("15", "If they performed quantitative synthesis did the review authors "
                    "carry out an adequate investigation of publication bias (small "
                    "study bias) and discuss its likely impact on the results of the "
                    "review?", "items", options=_AMSTAR_OPTIONS_META, critical=True),
        _item("16", "Did the review authors report any potential sources of conflict of "
                    "interest, including any funding they received for conducting the "
                    "review?", "items", options=_AMSTAR_OPTIONS_BASIC),
    ],
}


# ---------------------------------------------------------------------------
# CERQual（四构件 + 降级规则；评估对象是单条 review finding）
# ---------------------------------------------------------------------------

_CERQUAL_COMPONENTS = (
    ("methodological_limitations", "Methodological limitations"),
    ("coherence", "Coherence"),
    ("adequacy", "Adequacy of data"),
    ("relevance", "Relevance"),
)

_CERQUAL = {
    "id": "cerqual",
    "family": "GRADE-CERQual",
    "title": "GRADE-CERQual: confidence in the evidence from reviews of qualitative research",
    "version": "2018",
    "year": 2018,
    "scope": "review_finding",
    "url": "https://www.cerqual.org",
    "citation": ("Lewin S, Booth A, Glenton C, Munthe-Kaas H, Rashidian A, Wainwright M, "
                 "Bohren MA, Tunçalp Ö, Colvin CJ, Garside R, Carlsen B, Langlois EV, "
                 "Noyes J. Applying GRADE-CERQual to qualitative evidence synthesis "
                 "findings: introduction to the series. Implement Sci. 2018;13(Suppl "
                 "1):2. doi:10.1186/s13012-017-0688-3"),
    "license": _LICENSE_CERQUAL,
    "response_options": _CERQUAL_LEVELS,
    "response_option_templates": {
        "No or very minor concerns": ("No or very minor concerns regarding {component} "
                                      "that are unlikely to reduce confidence in the "
                                      "review finding"),
        "Minor concerns": ("Minor concerns regarding {component} that may reduce "
                           "confidence in the review finding"),
        "Moderate concerns": ("Moderate concerns regarding {component} that will "
                              "probably reduce confidence in the review finding"),
        "Serious concerns": ("Serious concerns regarding {component} that are very "
                             "likely to reduce confidence in the review finding"),
    },
    "instruction": ("Confidence is an assessment of the extent to which a review "
                    "finding is a reasonable representation of the phenomenon of "
                    "interest."),
    "official_overall_text": ("Four levels are used to describe the overall assessment "
                              "of confidence in a review finding—high, moderate, low or "
                              "very low... All review findings start off by default as "
                              "'high confidence' and are then 'rated down' by one or "
                              "more levels (for example, from high to moderate "
                              "confidence) if there are concerns regarding any of the "
                              "CERQual components. This starting point of 'high "
                              "confidence' reflects a view that each review finding "
                              "should be seen as a reasonable representation of the "
                              "phenomenon of interest unless there are factors that "
                              "would weaken this assumption."),
    "rating_down_guidance": ("Typically, the overall assessment of confidence should be "
                             "rated down by at least one level for each component for "
                             "which you have identified serious concerns. Where concerns "
                             "in relation to a component are minor or moderate, it may "
                             "not be necessary to rate down. However, if there are a "
                             "number of such concerns, it may be appropriate to rate "
                             "down by one level to represent two or more of these "
                             "concerns."),
    "judgement_options": ["high", "moderate", "low", "very_low"],
    "judgement_labels": {
        "high": "High confidence",
        "moderate": "Moderate confidence",
        "low": "Low confidence",
        "very_low": "Very low confidence",
    },
    "aggregation": "cerqual_confidence",
    "aggregation_notes": [
        "All review findings start off by default as 'high confidence' and are then "
        "rated down by one or more levels if there are concerns.",
        "Typically, rate down by at least one level for each component with serious "
        "concerns; minor or moderate concerns may not require rating down, but a "
        "number of such concerns may warrant one level.",
        "Do not numerically score assessments.",
        "Avoid downgrading twice for the same concern across components.",
        "Assessments should be made through discussion between at least two reviewers.",
    ],
    "sections": [{"id": "components", "title": "CERQual components"}],
    "items": [
        _item("methodological_limitations", "Methodological limitations", "components",
              kind="component", source_text="methodological limitations"),
        _item("coherence", "Coherence", "components", kind="component",
              source_text="coherence"),
        _item("adequacy", "Adequacy of data", "components", kind="component",
              source_text="adequacy of data"),
        _item("relevance", "Relevance", "components", kind="component",
              source_text="relevance"),
    ],
}


REGISTRY: dict[str, dict] = {
    tool["id"]: tool
    for tool in (
        _CASP_QUALITATIVE,
        _CASP_RCT,
        _CASP_SYSTEMATIC_REVIEW,
        _CASP_CASE_CONTROL,
        _CASP_CLINICAL_PREDICTION_RULE,
        _CASP_COHORT,
        _CASP_CROSS_SECTIONAL,
        _CASP_DIAGNOSTIC,
        _CASP_ECONOMIC,
        _CASP_SR_META_RCT,
        _CASP_SR_META_OBS,
        _ROB2,
        _MMAT,
        _AXIS,
        _AMSTAR2,
        _CERQUAL,
    )
}

# 官方清单形态支持逐条理由/评论：CASP 要求「record the reasons for your
# answers in the space provided」、MMAT 表头含 Comments 列、AXIS 选项含
# comment。API 对所有工具都接受 comments，此处仅供前端决定默认展开评论框。
for _tool in REGISTRY.values():
    if _tool["family"] in {"CASP", "MMAT", "AXIS"}:
        _tool["comments_supported"] = True

#: 官方 BibTeX（逐字取自各查证档案 §4，压成一行；前端/导出直接使用）。
_BIBTEX: dict[str, str] = {
    "casp-qualitative": (
        "@misc{casp2024qualitative, author={{Critical Appraisal Skills Programme}}, "
        "title={{CASP checklist: for qualitative research}}, year={2024}, "
        "url={https://casp-uk.net/casp-checklists/CASP-checklist-qualitative-2024.pdf}, "
        "urldate={2026-11-07}, note={CC BY-NC-SA 4.0}}"),
    "casp-rct": (
        "@misc{casp2024rct, author={{Critical Appraisal Skills Programme}}, "
        "title={{CASP checklist: for randomised controlled trials (RCTs)}}, year={2024}, "
        "url={https://casp-uk.net/casp-checklists/"
        "CASP-checklist-randomised-controlled-trials-RCT-2024.pdf}, "
        "urldate={2026-11-07}, note={CC BY-NC-SA 4.0}}"),
    "casp-systematic-review": (
        "@misc{casp2024sr, author={{Critical Appraisal Skills Programme}}, "
        "title={{CASP checklist: for systematic reviews}}, year={2024}, "
        "url={https://casp-uk.net/casp-checklists/"
        "CASP-checklist-systematic-reviews-checklist-2024.pdf}, "
        "urldate={2026-11-07}, note={CC BY-NC-SA 4.0}}"),
    "casp-case-control": (
        "@misc{casp2026casecontrol, author={{Critical Appraisal Skills Programme}}, "
        "title={{CASP checklist: for case control studies}}, year={2026}, "
        "url={https://casp-uk.net/casp-checklists/"
        "CASP-checklist-case-control-study-October%202026.pdf}, "
        "urldate={2026-11-07}, note={October 2026 update; CC BY-NC-SA 4.0}}"),
    "casp-clinical-prediction-rule": (
        "@misc{casp2024cpr, author={{Critical Appraisal Skills Programme}}, "
        "title={{CASP checklist: for clinical prediction rule}}, year={2024}, "
        "url={https://casp-uk.net/casp-checklists/"
        "CASP-checklist-clinical-prediction-rule-2024.pdf}, "
        "urldate={2026-11-07}, note={CC BY-NC-SA 4.0}}"),
    "casp-cohort": (
        "@misc{casp2024cohort, author={{Critical Appraisal Skills Programme}}, "
        "title={{CASP checklist: for cohort studies}}, year={2024}, "
        "url={https://casp-uk.net/casp-checklists/CASP-checklist-cohort-study-2024.pdf}, "
        "urldate={2026-11-07}, note={CC BY-NC-SA 4.0}}"),
    "casp-cross-sectional": (
        "@misc{casp2024crosssectional, author={{Critical Appraisal Skills Programme}}, "
        "title={{CASP checklist: for descriptive/cross-sectional studies}}, year={2024}, "
        "url={https://casp-uk.net/casp-checklists/"
        "CASP-checklist-cross-sectional-study-2024.pdf}, "
        "urldate={2026-11-07}, note={CC BY-NC-SA 4.0}}"),
    "casp-diagnostic": (
        "@misc{casp2024diagnostic, author={{Critical Appraisal Skills Programme}}, "
        "title={{CASP checklist: for diagnostic test studies}}, year={2024}, "
        "url={https://casp-uk.net/casp-checklists/"
        "CASP-checklist-diagnostic-test-2024.pdf}, "
        "urldate={2026-11-07}, note={CC BY-NC-SA 4.0}}"),
    "casp-economic-evaluation": (
        "@misc{casp2024economic, author={{Critical Appraisal Skills Programme}}, "
        "title={{CASP checklist: for economic evaluations}}, year={2024}, "
        "url={https://casp-uk.net/casp-checklists/"
        "CASP-checklist-economic-evaluation-2024.pdf}, "
        "urldate={2026-11-07}, note={CC BY-NC-SA 4.0}}"),
    "casp-systematic-review-meta-rct": (
        "@misc{casp2024srmarct, author={{Critical Appraisal Skills Programme}}, "
        "title={{CASP checklist: for systematic reviews with meta-analysis of "
        "randomised controlled trials (RCTs)}}, year={2024}, "
        "url={https://casp-uk.net/casp-checklists/"
        "CASP-checklist-systematic-reviews-meta-analysis-rct-checklist-2024.pdf}, "
        "urldate={2026-11-07}, note={CC BY-NC-SA 4.0}}"),
    "casp-systematic-review-meta-observational": (
        "@misc{casp2024srmaobs, author={{Critical Appraisal Skills Programme}}, "
        "title={{CASP checklist: for systematic reviews with meta-analysis of "
        "observational studies}}, year={2024}, "
        "url={https://casp-uk.net/casp-checklists/"
        "CASP-checklist-systematic-reviews-observational-studies-checklist-2024.pdf}, "
        "urldate={2026-11-07}, note={CC BY-NC-SA 4.0}}"),
    "rob2": (
        "@article{Sterne2019RoB2, author={Sterne, Jonathan A. C. and Savovi{\\'c}, "
        "Jelena and Page, Matthew J. and Elbers, Roy G. and Blencowe, Natalie S. and "
        "Boutron, Isabelle and Cates, Christopher J. and Cheng, Hung-Yuan and Corbett, "
        "Mark S. and Eldridge, Sandra M. and Emberson, Jonathan R. and Hern{\\'a}n, "
        "Miguel A. and Hopewell, Sally and Hr{\\'o}bjartsson, Asbj{\\o}rn and Junqueira, "
        "Daniela R. and J{\\\"u}ni, Peter and Kirkham, Jamie J. and Lasserson, Toby and "
        "Li, Tianjing and McAleenan, Alexandra and Reeves, Barnaby C. and Shepperd, "
        "Sasha and Shrier, Ian and Stewart, Lesley A. and Tilling, Kate and White, Ian "
        "R. and Whiting, Penny F. and Higgins, Julian P. T.}, title={{RoB 2}: a revised "
        "tool for assessing risk of bias in randomised trials}, journal={BMJ}, "
        "year={2019}, volume={366}, pages={l4898}, doi={10.1136/bmj.l4898}}"),
    "mmat": (
        "@article{hong2018mmat, author={Hong, Quan Nha and F{\\`a}bregues, Sergi and "
        "Bartlett, Gillian and Boardman, Felicity and Cargo, Margaret and Dagenais, "
        "Pierre and Gagnon, Marie-Pierre and Griffiths, Frances and Nicolau, Belinda and "
        "O'Cathain, Alicia and Rousseau, Marie-Claude and Vedel, Isabelle and Pluye, "
        "Pierre}, title={The {Mixed Methods Appraisal Tool} ({MMAT}) version 2018 for "
        "information professionals and researchers}, journal={Education for "
        "Information}, year={2018}, volume={34}, number={4}, pages={285--291}, "
        "doi={10.3233/EFI-180221}}"),
    "axis": (
        "@article{downes2016axis, author={Downes, Martin J. and Brennan, Marnie L. and "
        "Williams, Hywel C. and Dean, Rachel S.}, title={Development of a critical "
        "appraisal tool to assess the quality of cross-sectional studies ({AXIS})}, "
        "journal={BMJ Open}, year={2016}, volume={6}, number={12}, pages={e011458}, "
        "doi={10.1136/bmjopen-2016-011458}}"),
    "amstar2": (
        "@article{Shea2017AMSTAR2, author={Shea, Beverley J. and Reeves, Barnaby C. and "
        "Wells, George and Thuku, Micere and Hamel, Candyce and Moran, Julian and Moher, "
        "David and Tugwell, Peter and Welch, Vivian and Kristjansson, Elizabeth and "
        "Henry, David A.}, title={{AMSTAR 2}: a critical appraisal tool for systematic "
        "reviews that include randomised or non-randomised studies of healthcare "
        "interventions, or both}, journal={BMJ}, year={2017}, volume={358}, "
        "pages={j4008}, doi={10.1136/bmj.j4008}}"),
    "cerqual": (
        "@article{lewin2018cerqualintro, author={Lewin, Simon and Booth, Andrew and "
        "Glenton, Claire and Munthe-Kaas, Heather and Rashidian, Arash and Wainwright, "
        "Megan and Bohren, Meghan A. and Tun{\\c{c}}alp, {\\\"O}zge and Colvin, "
        "Christopher J. and Garside, Ruth and Carlsen, Benedicte and Langlois, Etienne "
        "V. and Noyes, Jane}, title={Applying {GRADE-CERQual} to qualitative evidence "
        "synthesis findings: introduction to the series}, journal={Implementation "
        "Science}, year={2018}, volume={13}, number={Suppl 1}, pages={2}, "
        "doi={10.1186/s13012-017-0688-3}}"),
}

for _tool_id, _bibtex in _BIBTEX.items():
    REGISTRY[_tool_id]["bibtex"] = _bibtex


#: 可用但未内置（需授权或仅允许链接）——**不收录任何条目文本**。
LINK_ONLY: tuple[dict, ...] = tuple(
    {
        "id": tool_id,
        "family": family,
        "title": title,
        "provider": provider,
        "url": url,
        "reason_key": "appraisal.link_only.permission_required",
        "license_note": note,
    }
    for tool_id, family, title, provider, url, note in (
        ("jbi-case-control", "JBI", "JBI Checklist for Case Control Studies",
         "Joanna Briggs Institute", "https://jbi.global/critical-appraisal-tools",
         "JBI grants use of these tools for research purposes only; redistribution "
         "requires written permission."),
        ("jbi-case-report", "JBI", "JBI Checklist for Case Reports",
         "Joanna Briggs Institute", "https://jbi.global/critical-appraisal-tools",
         "JBI grants use of these tools for research purposes only; redistribution "
         "requires written permission."),
        ("jbi-case-series", "JBI", "JBI Checklist for Case Series",
         "Joanna Briggs Institute", "https://jbi.global/critical-appraisal-tools",
         "JBI grants use of these tools for research purposes only; redistribution "
         "requires written permission."),
        ("jbi-cohort", "JBI", "JBI Checklist for Cohort Studies (2025 revised)",
         "Joanna Briggs Institute", "https://jbi.global/critical-appraisal-tools",
         "JBI grants use of these tools for research purposes only; redistribution "
         "requires written permission."),
        ("jbi-cross-sectional", "JBI",
         "JBI Assessment of Risk of Bias for Analytical Cross-sectional Studies (2026 "
         "revised)", "Joanna Briggs Institute",
         "https://jbi.global/critical-appraisal-tools",
         "JBI grants use of these tools for research purposes only; redistribution "
         "requires written permission."),
        ("jbi-diagnostic-test-accuracy", "JBI",
         "JBI Checklist for Diagnostic Test Accuracy Studies",
         "Joanna Briggs Institute", "https://jbi.global/critical-appraisal-tools",
         "JBI grants use of these tools for research purposes only; redistribution "
         "requires written permission."),
        ("jbi-economic-evaluation", "JBI", "JBI Checklist for Economic Evaluations",
         "Joanna Briggs Institute", "https://jbi.global/critical-appraisal-tools",
         "JBI grants use of these tools for research purposes only; redistribution "
         "requires written permission."),
        ("jbi-prevalence", "JBI", "JBI Checklist for Studies Reporting Prevalence Data",
         "Joanna Briggs Institute", "https://jbi.global/critical-appraisal-tools",
         "JBI grants use of these tools for research purposes only; redistribution "
         "requires written permission."),
        ("jbi-qualitative", "JBI", "JBI Checklist for Qualitative Research (2024)",
         "Joanna Briggs Institute", "https://jbi.global/critical-appraisal-tools",
         "JBI grants use of these tools for research purposes only; redistribution "
         "requires written permission."),
        ("jbi-quasi-experimental", "JBI",
         "JBI Checklist for Quasi-Experimental Studies (2024 revised)",
         "Joanna Briggs Institute", "https://jbi.global/critical-appraisal-tools",
         "JBI grants use of these tools for research purposes only; redistribution "
         "requires written permission."),
        ("jbi-rct", "JBI",
         "JBI Critical Appraisal Tool for Randomized Controlled Trials (2023 revised)",
         "Joanna Briggs Institute", "https://jbi.global/critical-appraisal-tools",
         "JBI grants use of these tools for research purposes only; redistribution "
         "requires written permission."),
        ("jbi-systematic-review", "JBI",
         "JBI Checklist for Systematic Reviews and Research Syntheses",
         "Joanna Briggs Institute", "https://jbi.global/critical-appraisal-tools",
         "JBI grants use of these tools for research purposes only; redistribution "
         "requires written permission."),
        ("jbi-textual-narrative", "JBI", "JBI Checklist for Textual Evidence: Narrative",
         "Joanna Briggs Institute", "https://jbi.global/critical-appraisal-tools",
         "JBI grants use of these tools for research purposes only; redistribution "
         "requires written permission."),
        ("jbi-textual-opinion", "JBI",
         "JBI Checklist for Textual Evidence: Expert Opinion",
         "Joanna Briggs Institute", "https://jbi.global/critical-appraisal-tools",
         "JBI grants use of these tools for research purposes only; redistribution "
         "requires written permission."),
        ("jbi-textual-policy", "JBI", "JBI Checklist for Textual Evidence: Policy",
         "Joanna Briggs Institute", "https://jbi.global/critical-appraisal-tools",
         "JBI grants use of these tools for research purposes only; redistribution "
         "requires written permission."),
        ("nos", "NOS", "Newcastle-Ottawa Scale (NOS) for assessing the quality of "
                       "nonrandomised studies in meta-analyses",
         "Ottawa Hospital Research Institute",
         "https://ohri.ca/en/who-we-are/core-facilities-and-platforms/"
         "ottawa-methods-centre/newcastle-ottawa-scale",
         "OHRI terms allow personal, non-commercial use only and prohibit "
         "reproduction/republishing without written permission."),
        ("robis", "ROBIS", "ROBIS: a tool for assessing risk of bias in systematic "
                            "reviews",
         "University of Bristol",
         "https://www.bristol.ac.uk/population-health-sciences/projects/robis/robis-tool/",
         "The tool and guidance PDFs carry no licence; University of Bristol site "
         "terms require written permission beyond personal, non-commercial use."),
        ("quadas-2", "QUADAS-2", "QUADAS-2: Quality Assessment of Diagnostic Accuracy "
                                  "Studies",
         "University of Bristol",
         "https://www.bristol.ac.uk/population-health-sciences/projects/quadas/",
         "The official tool PDF has no licence statement; embed only after permission."),
        ("probast", "PROBAST", "PROBAST: Prediction model Risk Of Bias ASsessment Tool",
         "PROBAST group", "https://www.probast.org",
         "The official tool PDF has no licence statement; embed only after permission."),
        ("ephpp", "EPHPP", "Quality Assessment Tool for Quantitative Studies (EPHPP)",
         "McMaster Evidence Review and Synthesis Team (MERST)",
         "https://merst.healthsci.mcmaster.ca/ephpp/",
         "No licence statement found; verbatim embedding requires written permission."),
    )
)


# ---------------------------------------------------------------------------
# 工具查询 / 条目遍历
# ---------------------------------------------------------------------------

def get_tool(tool_id: object) -> dict | None:
    """按 id 取注册表工具（返回副本，调用方不可污染注册表）。"""
    if not isinstance(tool_id, str):
        return None
    tool = REGISTRY.get(tool_id.strip())
    return copy.deepcopy(tool) if tool is not None else None


def _resolve_variant(tool: Mapping[str, Any], variant: str | None) -> str | None:
    if "domains" not in tool:
        return None
    default = tool.get("default_variant", "assignment")
    chosen = variant or default
    domain2 = next((d for d in tool["domains"] if "variants" in d), None)
    if domain2 is None:
        return None
    if chosen not in domain2["variants"]:
        raise AppraisalError(
            "appraisal_variant_invalid",
            "appraisal.variant_invalid",
            params={"variant": chosen,
                    "allowed": sorted(domain2["variants"])},
        )
    return chosen


def iter_items(tool: Mapping[str, Any], variant: str | None = None) -> Iterable[dict]:
    """遍历工具的应答条目（RoB 2 按所选变体展开）。"""
    if "domains" in tool:
        chosen = _resolve_variant(tool, variant)
        for domain in tool["domains"]:
            if "variants" in domain:
                yield from domain["variants"][chosen]["items"]
            else:
                yield from domain["items"]
        return
    yield from tool["items"]


def _item_options(tool: Mapping[str, Any], item: Mapping[str, Any]) -> list[str]:
    return list(item.get("response_options") or tool.get("response_options") or [])


def _question_key(item: Mapping[str, Any]) -> str:
    """条目所属的编号问题：``4a``/``6b`` 归入 4/6；带 parent_text 的
    ``6.1``/``7.2`` 归入 6/7；其余（含 MMAT ``1.1``）保持原编号。"""
    if item.get("question"):
        return str(item["question"])
    number = str(item.get("number") or item["id"])
    match = re.fullmatch(r"(\d+)[a-z]", number)
    if match:
        return match.group(1)
    if item.get("parent_text") and "." in number:
        return number.split(".", 1)[0]
    return number


def _question_numbers(tool: Mapping[str, Any]) -> list[str]:
    seen: list[str] = []
    for item in iter_items(tool):
        number = _question_key(item)
        if number not in seen:
            seen.append(number)
    return seen


def tool_summaries() -> list[dict]:
    """注册表摘要（条目数/版本/引用/许可），供 ``GET .../appraisal/tools``。

    摘要**不**包含条目正文，避免把整份工具塞进列表响应；前端选工具后再经
    ``GET .../appraisal/{article_key}?tool=`` 取完整条目。
    """
    summaries = []
    for tool in REGISTRY.values():
        items = list(iter_items(tool))
        summaries.append({
            "id": tool["id"],
            "family": tool["family"],
            "title": tool["title"],
            "version": tool["version"],
            "year": tool["year"],
            "scope": tool["scope"],
            "url": tool["url"],
            "citation": tool["citation"],
            "bibtex": tool.get("bibtex", ""),
            "license": copy.deepcopy(tool["license"]),
            "aggregation": tool["aggregation"],
            "item_count": len(items),
            "question_count": len(_question_numbers(tool)),
            "overall_judgement_options": list(tool.get("overall_judgement_options", [])),
            "domain_count": len(tool.get("domains", [])),
            "comments_supported": bool(tool.get("comments_supported", False)),
        })
    return summaries


# ---------------------------------------------------------------------------
# 校验 / 归一化
# ---------------------------------------------------------------------------

def validate_article_key(article_key: object) -> str:
    """文献键形状校验（非空、有界、无控制字符）。"""
    if not isinstance(article_key, str):
        raise AppraisalError("appraisal_article_key_invalid",
                             "appraisal.article_key_invalid")
    key = article_key.strip()
    if not key or len(key) > MAX_ARTICLE_KEY_CHARS or any(ord(ch) < 32 for ch in key):
        raise AppraisalError("appraisal_article_key_invalid",
                             "appraisal.article_key_invalid",
                             params={"max_chars": MAX_ARTICLE_KEY_CHARS})
    return key


def _normalize_option(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.strip().split()).casefold()


def normalize_responses(tool: Mapping[str, Any], responses: object,
                        *, variant: str | None = None) -> tuple[dict[str, str], str | None]:
    """把 ``{item_id: option}`` 归一化为官方选项串；未知条目/选项抛 400。

    返回 ``(归一化应答, 所选变体)``。选项比较大小写与多余空白不敏感，但落库
    的是注册表里的**官方写法**。
    """
    if responses is None:
        responses = {}
    if not isinstance(responses, Mapping):
        raise AppraisalError("appraisal_responses_invalid",
                             "appraisal.responses_invalid")
    if len(responses) > MAX_RESPONSE_ITEMS:
        raise AppraisalError("appraisal_responses_too_large",
                             "appraisal.responses_too_large",
                             params={"max_items": MAX_RESPONSE_ITEMS})

    chosen = _resolve_variant(tool, variant)
    index: dict[str, dict] = {}
    for item in iter_items(tool, chosen):
        index[str(item["id"])] = item

    normalized: dict[str, str] = {}
    unknown: list[str] = []
    for raw_id, raw_option in responses.items():
        item_id = str(raw_id)
        item = index.get(item_id)
        if item is None:
            unknown.append(item_id)
            continue
        options = _item_options(tool, item)
        by_norm = {_normalize_option(option): option for option in options}
        match = by_norm.get(_normalize_option(raw_option))
        if match is None:
            raise AppraisalError(
                "appraisal_option_invalid",
                "appraisal.option_invalid",
                params={"item_id": item_id, "option": raw_option,
                        "allowed": options},
            )
        normalized[item_id] = match
    if unknown:
        raise AppraisalError(
            "appraisal_item_unknown",
            "appraisal.item_unknown",
            params={"item_ids": sorted(unknown)},
        )
    return normalized, chosen


# ---------------------------------------------------------------------------
# 聚合：RoB 2（官方域算法 + Table 3）
# ---------------------------------------------------------------------------

def _in(answer: str | None, *codes: str) -> bool:
    return answer in codes


def _rob2_d1(a: Mapping[str, str]) -> str:
    q1, q2, q3 = a.get("1.1"), a.get("1.2"), a.get("1.3")
    concealed = _in(q2, "Y", "PY")
    not_concealed = _in(q2, "N", "PN")
    no_info_conceal = q2 == "NI"
    random_ok = _in(q1, "Y", "PY")
    not_random = _in(q1, "N", "PN")
    no_info_random = q1 == "NI"
    baseline_ok = _in(q3, "N", "PN")
    baseline_bad = _in(q3, "Y", "PY")
    no_info_baseline = q3 == "NI"

    if not_concealed or (no_info_conceal and baseline_bad):
        return "high"
    if concealed and (baseline_ok or no_info_baseline) and (
            random_ok or no_info_random):
        return "low"
    return "some_concerns"


def _rob2_d2_assignment(a: Mapping[str, str]) -> dict:
    # 官方决策表（条目路由文本：『If Y/PY/NI to 2.1 or 2.2: (answer 2.3)』）：
    # 只有 2.1 与 2.2 **都是 N/PN** 才算「不知情→直接 Low」；NI（无信息）与
    # Y/PY 同路进入 2.3 分支（终审 #47：旧实现把 NI 折进 not aware，与 N/PN
    # 同路直接判 Low，偏倚风险被静默低估——与其他域把 NI 归 some_concerns 的
    # 实现也不一致）。
    aware = (_in(a.get("2.1"), "Y", "PY", "NI")
             or _in(a.get("2.2"), "Y", "PY", "NI"))
    deviations = _in(a.get("2.3"), "Y", "PY")
    no_deviations = _in(a.get("2.3"), "N", "PN")
    affected = _in(a.get("2.4"), "Y", "PY")
    balanced = _in(a.get("2.5"), "Y", "PY")
    unbalanced = _in(a.get("2.5"), "N", "PN")
    appropriate = _in(a.get("2.6"), "Y", "PY")
    # 官方 Part 2 的 High 条件是「An appropriate analysis was **not** used」。
    # 2.6 = NI 只表示「无从判断」，不等于「未使用适当分析」，因此只把 N/PN
    # 当作未使用；NI 归入 Some concerns（保守判定，避免凭 NI 直接升 High）。
    not_appropriate = _in(a.get("2.6"), "N", "PN")
    substantial = _in(a.get("2.7"), "Y", "PY")

    if not aware or no_deviations:
        part1 = "low"
    elif deviations and affected and unbalanced:
        part1 = "high"
    else:
        part1 = "some_concerns"

    if appropriate:
        part2 = "low"
    elif not_appropriate and substantial:
        part2 = "high"
    else:
        part2 = "some_concerns"

    if "high" in (part1, part2):
        judgement = "high"
    elif part1 == "low" and part2 == "low":
        judgement = "low"
    else:
        judgement = "some_concerns"
    return {"part_1": part1, "part_2": part2, "judgement": judgement}


def _rob2_d2_adhering(a: Mapping[str, str]) -> str:
    # 与 assignment 变体同一口径：NI 与 Y/PY 同路进入 2.3 分支（官方
    # 『If Y/PY/NI to 2.1 or 2.2』），只有两者都是 N/PN 才算不知情（终审 #47）。
    aware = (_in(a.get("2.1"), "Y", "PY", "NI")
             or _in(a.get("2.2"), "Y", "PY", "NI"))
    balanced = _in(a.get("2.3"), "Y", "PY", "NA")
    not_balanced = _in(a.get("2.3"), "N", "PN")
    failures = _in(a.get("2.4"), "Y", "PY")
    no_failures = _in(a.get("2.4"), "N", "PN", "NA")
    nonadherence = _in(a.get("2.5"), "Y", "PY")
    adherence = _in(a.get("2.5"), "N", "PN", "NA")
    not_appropriate = _in(a.get("2.6"), "N", "PN")

    if not aware:
        # 官方 Low 文本以「(i.1) ... unaware」单列，但 Some concerns/High 文本
        # 又把「unaware AND 失败/不依从」列为条件，原文自相矛盾。此处取保守
        # 口径：unaware 且无实施失败/不依从 → Low；有失败/不依从 → Some
        # concerns；不凭 unaware 直接升 High（与 Low 文本保持一致）。
        return "some_concerns" if (failures or nonadherence) else "low"
    if balanced and no_failures and adherence:
        return "low"
    # High 的官方条件：aware 且（非协议干预未平衡 OR 实施失败 OR 不依从）
    # 且未使用适当分析。旧实现只覆盖「未平衡」一支，漏掉 balanced + 实施
    # 失败/不依从 + 2.6 = N/PN 的 High 组合。
    if not_appropriate and (not_balanced or failures or nonadherence):
        return "high"
    return "some_concerns"


def _rob2_d3(a: Mapping[str, str]) -> str:
    available = _in(a.get("3.1"), "Y", "PY")
    evidence_ok = _in(a.get("3.2"), "Y", "PY")
    could_not_depend = _in(a.get("3.3"), "N", "PN")
    # 官方 High 条件要求「Missingness **could** depend on its true value」。
    # 3.3 = NI 是「无从判断」，不构成 High 的充分条件；归入 Some concerns。
    could_depend = _in(a.get("3.3"), "Y", "PY")
    likely_depended = _in(a.get("3.4"), "Y", "PY")

    if available or evidence_ok or could_not_depend:
        return "low"
    if could_depend and likely_depended:
        return "high"
    return "some_concerns"


def _rob2_d4(a: Mapping[str, str]) -> str:
    method_ok = _in(a.get("4.1"), "N", "PN")
    method_bad = _in(a.get("4.1"), "Y", "PY")
    measure_same = _in(a.get("4.2"), "N", "PN")
    measure_differs = _in(a.get("4.2"), "Y", "PY")
    assessors_unaware = _in(a.get("4.3"), "N", "PN")
    could_not_influence = _in(a.get("4.4"), "N", "PN")
    likely_influenced = _in(a.get("4.5"), "Y", "PY")

    if method_bad or measure_differs or likely_influenced:
        return "high"
    if method_ok and measure_same and (assessors_unaware or could_not_influence):
        return "low"
    return "some_concerns"


def _rob2_d5(a: Mapping[str, str]) -> str:
    prespecified = _in(a.get("5.1"), "Y", "PY")
    unlikely_measure = _in(a.get("5.2"), "N", "PN")
    unlikely_analysis = _in(a.get("5.3"), "N", "PN")

    if _in(a.get("5.2"), "Y", "PY") or _in(a.get("5.3"), "Y", "PY"):
        return "high"
    if prespecified and unlikely_measure and unlikely_analysis:
        return "low"
    return "some_concerns"


_OVERALL_ORDER = {"low": 0, "some_concerns": 1, "high": 2}


def _aggregate_rob2(tool: Mapping[str, Any], responses: Mapping[str, str],
                    variant: str | None, override: Mapping | None) -> dict:
    chosen = _resolve_variant(tool, variant)
    items = list(iter_items(tool, chosen))
    missing = [item["id"] for item in items if item["id"] not in responses]
    complete = not missing

    domains: dict[str, dict] = {}
    if complete:
        judgements = {
            "D1": _rob2_d1(responses),
        }
        d2 = (_rob2_d2_assignment(responses) if chosen == "assignment"
              else {"judgement": _rob2_d2_adhering(responses)})
        judgements["D2"] = d2["judgement"]
        judgements["D3"] = _rob2_d3(responses)
        judgements["D4"] = _rob2_d4(responses)
        judgements["D5"] = _rob2_d5(responses)
        for domain in tool["domains"]:
            entry = {
                "judgement": judgements[domain["id"]],
                "label": tool["judgement_labels"][judgements[domain["id"]]],
            }
            if domain["id"] == "D2":
                entry["variant"] = chosen
                entry["part_1"] = d2.get("part_1")
                entry["part_2"] = d2.get("part_2")
            domains[domain["id"]] = entry

    suggested = None
    if complete:
        worst = max(domains.values(),
                    key=lambda entry: _OVERALL_ORDER[entry["judgement"]])
        suggested = worst["judgement"]
    some_concerns_domains = [key for key, entry in domains.items()
                             if entry["judgement"] == "some_concerns"]

    manual = None
    if override is not None:
        manual = _validate_rob2_override(tool, override)

    judgement = manual["judgement"] if manual else suggested
    return {
        "mode": "domains",
        "variant": chosen,
        "complete": complete,
        "missing_items": missing,
        "domains": domains,
        "suggested_overall": suggested,
        "suggested_overall_label": (tool["judgement_labels"][suggested]
                                    if suggested else None),
        "multiple_some_concerns": len(some_concerns_domains) > 1,
        "manual_upgrade_recommended": len(some_concerns_domains) > 1,
        "manual_upgrade_reason_key": ("appraisal.rob2.multiple_some_concerns"
                                      if len(some_concerns_domains) > 1 else None),
        "judgement_required": True,
        "manual_judgement_required_for_upgrade": True,
        "manual_judgement": manual,
        "judgement": judgement,
        "judgement_label": (tool["judgement_labels"][judgement]
                            if judgement else None),
        "judgement_source": "manual" if manual else (
            "algorithm" if suggested else None),
    }


def _validate_rob2_override(tool: Mapping[str, Any], override: object) -> dict:
    if not isinstance(override, Mapping):
        raise AppraisalError("appraisal_overall_invalid", "appraisal.overall_invalid")
    judgement = _normalize_option(override.get("judgement"))
    allowed = {"low": "low", "some concerns": "some_concerns",
               "some_concerns": "some_concerns", "high": "high"}
    code = allowed.get(judgement)
    reason = override.get("reason")
    if code is None or not isinstance(reason, str) or not reason.strip():
        raise AppraisalError(
            "appraisal_overall_invalid",
            "appraisal.overall_invalid",
            params={"allowed": list(tool["judgement_options"]),
                    "reason_required": True},
        )
    return {"judgement": code, "reason": reason.strip()[:2000]}


# ---------------------------------------------------------------------------
# 聚合：AMSTAR 2（Box 1 + Box 2）
# ---------------------------------------------------------------------------

def _aggregate_amstar2(tool: Mapping[str, Any], responses: Mapping[str, str],
                       override: Mapping | None) -> dict:
    items = list(tool["items"])
    missing = [item["id"] for item in items if item["id"] not in responses]
    complete = not missing

    critical_ids = set(tool["critical_items"])
    ignored = {"No meta-analysis conducted"}
    flaws: list[dict] = []
    weaknesses: list[dict] = []
    for item in items:
        item_id = item["id"]
        answer = responses.get(item_id)
        if answer is None or answer in ignored:
            continue
        if item_id in critical_ids and answer == "No":
            flaws.append({"item": item_id, "answer": answer})
        elif answer in {"No", "Partial Yes"}:
            weaknesses.append({"item": item_id, "answer": answer})

    suggested = None
    if complete:
        if not flaws:
            suggested = "high" if len(weaknesses) <= 1 else "moderate"
        elif len(flaws) == 1:
            suggested = "low"
        else:
            suggested = "critically_low"

    manual = None
    if override is not None:
        manual = _validate_confidence_override(tool, override)

    confidence = manual["confidence"] if manual else suggested
    return {
        "mode": "critical_domains",
        "complete": complete,
        "missing_items": missing,
        "critical_items": list(tool["critical_items"]),
        "critical_flaws": flaws,
        "noncritical_weaknesses": weaknesses,
        "suggested_confidence": suggested,
        "suggested_confidence_label": (tool["judgement_labels"][suggested]
                                       if suggested else None),
        "multiple_noncritical_weaknesses": len(weaknesses) > 1,
        "manual_downgrade_recommended": len(weaknesses) > 1 and not flaws,
        "manual_downgrade_reason_key": ("appraisal.amstar2.multiple_weaknesses"
                                        if len(weaknesses) > 1 and not flaws else None),
        "judgement_required": True,
        "manual_confidence": manual,
        "confidence": confidence,
        "confidence_label": (tool["judgement_labels"][confidence]
                             if confidence else None),
        "confidence_source": "manual" if manual else (
            "algorithm" if suggested else None),
    }


def _validate_confidence_override(tool: Mapping[str, Any], override: object) -> dict:
    if not isinstance(override, Mapping):
        raise AppraisalError("appraisal_overall_invalid", "appraisal.overall_invalid")
    raw = _normalize_option(override.get("confidence") or override.get("judgement"))
    aliases = {
        "high": "high",
        "moderate": "moderate",
        "low": "low",
        "critically low": "critically_low",
        "critically_low": "critically_low",
        "very low": "very_low",
        "very_low": "very_low",
    }
    code = aliases.get(raw)
    reason = override.get("reason")
    if code not in tool["judgement_options"] or not isinstance(reason, str) or \
            not reason.strip():
        raise AppraisalError(
            "appraisal_overall_invalid",
            "appraisal.overall_invalid",
            params={"allowed": list(tool["judgement_options"]),
                    "reason_required": True},
        )
    return {"confidence": code, "reason": reason.strip()[:2000]}


# ---------------------------------------------------------------------------
# 聚合：CERQual（默认高置信 + 降级）
# ---------------------------------------------------------------------------

_CERQUAL_DOWNGRADE = {
    "No or very minor concerns": 0,
    "Minor concerns": 0,
    "Moderate concerns": 0,
    "Serious concerns": 1,
}
_CERQUAL_SCALE = ["high", "moderate", "low", "very_low"]


def _aggregate_cerqual(tool: Mapping[str, Any], responses: Mapping[str, str],
                       override: Mapping | None) -> dict:
    items = list(tool["items"])
    missing = [item["id"] for item in items if item["id"] not in responses]
    complete = not missing

    components: dict[str, dict] = {}
    serious = 0
    minor_or_moderate = 0
    for item in items:
        answer = responses.get(item["id"])
        if answer is None:
            continue
        downgrade = _CERQUAL_DOWNGRADE.get(answer, 0)
        if answer == "Serious concerns":
            serious += 1
        elif answer in {"Minor concerns", "Moderate concerns"}:
            minor_or_moderate += 1
        components[item["id"]] = {
            "component": item["text"],
            "concern": answer,
            "downgrade_levels": downgrade,
        }

    suggested = None
    downgrade_levels = 0
    if complete:
        downgrade_levels = serious
        notes: list[str] = []
        if minor_or_moderate >= 2 and serious == 0:
            downgrade_levels = 1
            notes.append("appraisal.cerqual.multiple_minor_moderate")
        downgrade_levels = min(downgrade_levels, len(_CERQUAL_SCALE) - 1)
        suggested = _CERQUAL_SCALE[downgrade_levels]
    else:
        notes = []

    manual = None
    if override is not None:
        manual = _validate_confidence_override(tool, override)

    confidence = manual["confidence"] if manual else suggested
    return {
        "mode": "downgrade",
        "complete": complete,
        "missing_items": missing,
        "components": components,
        "serious_count": serious,
        "minor_or_moderate_count": minor_or_moderate,
        "downgrade_levels": downgrade_levels,
        "suggested_confidence": suggested,
        "suggested_confidence_label": (tool["judgement_labels"][suggested]
                                       if suggested else None),
        "judgement_required": True,
        "manual_confidence": manual,
        "confidence": confidence,
        "confidence_label": (tool["judgement_labels"][confidence]
                             if confidence else None),
        "confidence_source": "manual" if manual else (
            "algorithm" if suggested else None),
        "notes": notes,
    }


# ---------------------------------------------------------------------------
# 聚合：无分工具（CASP / MMAT / AXIS）
# ---------------------------------------------------------------------------

def _aggregate_no_score(tool: Mapping[str, Any], responses: Mapping[str, str],
                        comments: Mapping[str, str],
                        override: Mapping | None) -> dict:
    items = list(tool["items"])
    answered_items = []
    unanswered = []
    distribution: dict[str, int] = {}
    for item in items:
        answer = responses.get(item["id"])
        entry = {
            "id": item["id"],
            "number": item.get("number", item["id"]),
            "section": item.get("section"),
            "response": answer,
        }
        if "parent_text" in item:
            entry["parent_text"] = item["parent_text"]
        comment = comments.get(item["id"])
        if comment:
            entry["comment"] = comment
        if answer is None:
            unanswered.append(item["id"])
        else:
            distribution[answer] = distribution.get(answer, 0) + 1
        answered_items.append(entry)

    options = list(tool.get("overall_judgement_options") or [])
    manual = None
    if override is not None:
        manual = _validate_generic_judgement(options, override)

    return {
        "mode": "no_score",
        "complete": not unanswered,
        "answered": len(items) - len(unanswered),
        "item_count": len(items),
        "unanswered": unanswered,
        "answer_distribution": distribution,
        "items": answered_items,
        "overall_judgement_options": options,
        "overall_judgement": manual["judgement"] if manual else None,
        "judgement_source": "manual" if manual else None,
        # MMAT 官方不设总体评级（options 为空）→ 不要求人工总体判定。
        "judgement_required": bool(options),
        "manual_judgement": manual,
        "notes": list(tool.get("aggregation_notes", [])),
    }


def _validate_generic_judgement(options: list[str], override: object) -> dict:
    if not isinstance(override, Mapping):
        raise AppraisalError("appraisal_overall_invalid", "appraisal.overall_invalid")
    raw = _normalize_option(override.get("judgement"))
    code = raw if raw in options else None
    reason = override.get("reason")
    if code is None or not isinstance(reason, str) or not reason.strip():
        raise AppraisalError(
            "appraisal_overall_invalid",
            "appraisal.overall_invalid",
            params={"allowed": options, "reason_required": True},
        )
    return {"judgement": code, "reason": reason.strip()[:2000]}


# ---------------------------------------------------------------------------
# 聚合入口
# ---------------------------------------------------------------------------

def aggregate(tool_id: object, responses: object, *,
              variant: str | None = None,
              comments: object = None,
              overall: Mapping | None = None) -> dict:
    """纯函数：应答 → 总体结果（按工具官方规则）。

    - ``responses``：``{item_id: option}``（可缺项＝草稿）；
    - ``comments``：``{item_id: comment}``（可选，AXIS/MMAT 的评论列）；
    - ``overall``：人工总体判定（``{"judgement"/"confidence": ..., "reason": ...}``）。
      官方算法只是"建议判定"，评审者可推翻，故人工值需带理由。

    Raises:
        AppraisalError: 工具不在册、条目/选项不合法、人工判定不合法。
    """
    tool = REGISTRY.get(tool_id) if isinstance(tool_id, str) else None
    if tool is None:
        raise AppraisalError("appraisal_tool_unknown", "appraisal.tool_unknown",
                             params={"tool": tool_id})
    normalized, chosen = normalize_responses(tool, responses, variant=variant)

    if comments is None:
        comments = {}
    if not isinstance(comments, Mapping):
        raise AppraisalError("appraisal_comments_invalid", "appraisal.comments_invalid")
    known_ids = {item["id"] for item in iter_items(tool, chosen)}
    clean_comments: dict[str, str] = {}
    for item_id, comment in comments.items():
        if str(item_id) not in known_ids:
            raise AppraisalError("appraisal_item_unknown", "appraisal.item_unknown",
                                 params={"item_ids": [str(item_id)]})
        if comment is None:
            continue
        if not isinstance(comment, str) or len(comment) > MAX_NOTE_CHARS:
            raise AppraisalError("appraisal_comment_invalid",
                                 "appraisal.comment_invalid",
                                 params={"item_id": str(item_id)})
        if comment.strip():
            clean_comments[str(item_id)] = comment.strip()

    mode = tool["aggregation"]
    if mode == "rob2_domains":
        return _aggregate_rob2(tool, normalized, variant, overall)
    if mode == "amstar2_confidence":
        return _aggregate_amstar2(tool, normalized, overall)
    if mode == "cerqual_confidence":
        return _aggregate_cerqual(tool, normalized, overall)
    return _aggregate_no_score(tool, normalized, clean_comments, overall)


# ---------------------------------------------------------------------------
# GRADE 衔接：每条文献的总体评价 → 研究局限性（risk of bias）档位
# ---------------------------------------------------------------------------

#: GRADE「研究局限性」三档（越靠后越严重）。
GRADE_RISK_LEVELS = ("low", "serious", "very_serious")

#: 工具总体判定 → GRADE 研究局限性档位（纯映射表，改表即改衔接，无需改端点）。
#:
#: 依据（``coscreen/grade_assessor.py`` 的 ``suggest_rob_downgrade`` 是既有
#: 权威输入形状：它接受 ``{"overall": "<判定>"}``，把 ``high``/``serious``
#: 降为 ``serious``、``critical`` 降为 ``very_serious``，其余不降级）：
#:
#: - **RoB 2**：注册表总体判定为 ``low`` / ``some_concerns`` / ``high``。
#:   ``high`` → serious，``critical``（ROBINS-I 的同类判定，本表保留以兼容
#:   直接传入的 overall 字符串）→ very_serious；``low`` 与 ``some_concerns``
#:   → low（与 ``suggest_rob_downgrade`` 的"不降级"口径一致；多个域
#:   some concerns 需人工上调时，记录里的人工 judgement 已是 ``high``，
#:   自然落入 serious）。
#: - **AMSTAR 2**：Box 2 置信度 high/moderate/low/critically_low 按严重度
#:   递增 → low/serious/very_serious（低与极低都归 very_serious）。
#: - **CASP / AXIS**：清单自带的总体质量 high/moderate/low 依次 →
#:   low/serious/very_serious；没有人工总体判定时，按逐条**正向**占比分档
#:   （见 ``_PER_ITEM_QUALITY_BANDS``）——普通条目答 Yes 记正向，
#:   ``reverse_direction`` 条目（AXIS 第 19 条利益冲突）答 **No** 记正向
#:   （终审 #06）。
#: - **CERQual**：它评价的是"综述发现的可信度"而非原始研究偏倚；本表仅作
#:   GRADE 起点锚（high/moderate/low/very_low → low/serious/serious/
#:   very_serious），不修改 CERQual 记录本身。
#: - **MMAT**：官方明确不建议合成总分，故只按逐条答案的 "Yes" 占比分档，
#:   再套用同一质量档映射。
GRADE_ROB_MAP: dict[str, dict[str, str]] = {
    "rob2": {
        "low": "low",
        "some_concerns": "low",
        "some concerns": "low",
        "high": "serious",
        "serious": "serious",
        "critical": "very_serious",
    },
    "amstar2": {
        "high": "low",
        "moderate": "serious",
        "low": "very_serious",
        "critically_low": "very_serious",
        "critically low": "very_serious",
    },
    "casp": {"high": "low", "moderate": "serious", "low": "very_serious"},
    "axis": {"high": "low", "moderate": "serious", "low": "very_serious"},
    "cerqual": {
        "high": "low",
        "moderate": "serious",
        "low": "serious",
        "very_low": "very_serious",
        "very low": "very_serious",
    },
    "mmat": {"high": "low", "moderate": "serious", "low": "very_serious"},
}

#: 无算法总体判定时（CASP/AXIS/MMAT），逐条 "Yes" 占比 → 质量档（降序阈值）。
_PER_ITEM_QUALITY_BANDS: tuple[tuple[float, str], ...] = (
    (0.8, "high"), (0.5, "moderate"), (0.0, "low"))


def _grade_tool_key(tool: Mapping[str, Any]) -> str:
    """工具 → ``GRADE_ROB_MAP`` 的键（CASP 全系共用一个档表）。"""
    tool_id = str(tool.get("id") or "")
    if tool_id in GRADE_ROB_MAP:
        return tool_id
    family = str(tool.get("family") or "")
    if family == "CASP":
        return "casp"
    if family == "GRADE-CERQual":
        return "cerqual"
    return tool_id


def _tally_responses(aggregate: Mapping[str, Any]) -> dict[str, int]:
    """从 no_score 聚合结果取应答计数（``answer_distribution`` 或逐条 items）。"""
    tally = aggregate.get("answer_distribution")
    if isinstance(tally, Mapping):
        return {str(key): int(value) for key, value in tally.items()
                if isinstance(value, int) and not isinstance(value, bool)}
    counts: dict[str, int] = {}
    items = aggregate.get("items")
    if isinstance(items, list):
        for entry in items:
            if isinstance(entry, Mapping):
                answer = entry.get("response")
                if isinstance(answer, str) and answer:
                    counts[answer] = counts.get(answer, 0) + 1
    return counts


def _reverse_item_ids(tool: Mapping[str, Any] | None) -> frozenset[str]:
    """工具注册表中标记 ``reverse_direction`` 的条目号（AXIS 第 19 条利益冲突）。"""
    if not isinstance(tool, Mapping):
        return frozenset()
    items = tool.get("items")
    if not isinstance(items, list):
        return frozenset()
    return frozenset(str(item.get("id")) for item in items
                     if isinstance(item, Mapping) and item.get("reverse_direction"))


def _per_item_quality(aggregate: Mapping[str, Any],
                      tool: Mapping[str, Any] | None = None) -> str | None:
    """逐条报告归纳质量档：正向占比 ≥0.8 → high、≥0.5 → moderate、其余 low。

    正向口径：普通条目答 ``Yes`` 记正向；``reverse_direction`` 条目（AXIS 第
    19 条「是否存在可能影响解释的资助/利益冲突」）答 **No** 记正向——答 Yes
    表示存在顾虑，计分方向相反（终审 #06：旧实现按正向题数 Yes，答「有利益
    冲突顾虑」反而抬高质量档、答「无顾虑」反而压低）。聚合缺逐条 ``items``
    （只有 ``answer_distribution``，不含条目号）时退回 Yes 占比口径——该形态
    无法识别反向题（现网仅 AXIS 有反向条目，且其聚合恒带 items）。
    """
    reverse = _reverse_item_ids(tool)
    items = aggregate.get("items")
    if reverse and isinstance(items, list):
        positive = 0
        total = 0
        for entry in items:
            if not isinstance(entry, Mapping):
                continue
            answer = entry.get("response")
            if not isinstance(answer, str) or not answer:
                continue
            total += 1
            item_id = str(entry.get("id") or "")
            is_positive = (answer == "No") if item_id in reverse else (answer == "Yes")
            if is_positive:
                positive += 1
        if total <= 0:
            return None
        share = positive / total
    else:
        tally = _tally_responses(aggregate)
        total = sum(tally.values())
        if total <= 0:
            return None
        share = tally.get("Yes", 0) / total
    for threshold, band in _PER_ITEM_QUALITY_BANDS:
        if share >= threshold:
            return band
    return "low"


def study_rating_for_grade(tool: object, overall: object) -> dict:
    """把一条文献的总体评价折算成 GRADE「研究局限性」档位（纯函数、零副作用）。

    输入 ``overall`` 接受两种形状（端点从 ``appraisal_records.overall_json``
    读出后直接传入）：

    - :func:`aggregate` 的返回 dict（含 ``mode`` 与工具特定字段）；
    - 存储包装 ``{"aggregate": <上述 dict>, "override": ..., "variant": ...}``
      —— 与 :func:`save_record` 落库的形状一致；
    - 直接传入总体判定字符串（如 ``"high"``）。

    返回 ``{"tool", "available", "risk_of_bias", "source_value", "source",
    "rule"}``：

    - ``risk_of_bias`` ∈ :data:`GRADE_RISK_LEVELS`，或 ``None``（缺总体判定）；
    - ``source``：``algorithm`` / ``manual`` / ``per_item_report``；
    - ``rule``：命中的映射键（``"<family>:<value>"``），便于审计。

    映射依据见 :data:`GRADE_ROB_MAP`：与 ``coscreen/grade_assessor.py`` 的
    ``suggest_rob_downgrade`` 同口径（high→serious、critical→very_serious、
    low/some concerns 不降级）。本函数只读，不写、也不引用任何 GRADE 草稿。
    """
    tool_id = tool.strip() if isinstance(tool, str) else ""
    tool_def = REGISTRY.get(tool_id) if tool_id else None
    if tool_def is None:
        raise ValueError(f"未注册的评价工具: {tool!r}")

    aggregate = overall
    if isinstance(overall, str):
        # 直接传入总体判定字符串（如 ``"high"``）时按工具无关的 overall 处理。
        aggregate = {"overall": overall}
    elif isinstance(overall, Mapping) and isinstance(overall.get("aggregate"), Mapping):
        aggregate = overall["aggregate"]
    if not isinstance(aggregate, Mapping):
        return {"tool": tool_id, "available": False, "risk_of_bias": None,
                "source_value": None, "source": "none", "rule": "no_overall"}

    family = _grade_tool_key(tool_def)
    table = GRADE_ROB_MAP.get(family)
    if table is None:  # 未登记映射的工具：如实报不可用，不猜档位
        return {"tool": tool_id, "available": False, "risk_of_bias": None,
                "source_value": None, "source": "none", "rule": "unsupported_tool"}

    if family == "rob2":
        value = aggregate.get("judgement")
        if value is None:
            value = aggregate.get("overall")
        source = ("manual" if aggregate.get("judgement_source") == "manual"
                  else "algorithm")
    elif family in {"amstar2", "cerqual"}:
        value = aggregate.get("confidence")
        if value is None:
            value = aggregate.get("overall")
        source = ("manual" if aggregate.get("confidence_source") == "manual"
                  else "algorithm")
    else:  # CASP / AXIS / MMAT：优先人工总体判定，否则逐条报告归纳
        value = aggregate.get("overall_judgement")
        if value is None:
            value = aggregate.get("overall")
        source = "manual" if isinstance(value, str) else "per_item_report"
        if value is None:
            # 带工具上下文：reverse_direction 条目（AXIS 第 19 条）按 No=正向
            # 计入（终审 #06），与注册表 aggregation_notes 的自述一致。
            value = _per_item_quality(aggregate, tool_def)

    key = _normalize_option(value)
    level = table.get(key) if key else None
    if level is None:
        return {"tool": tool_id, "available": False, "risk_of_bias": None,
                "source_value": value if isinstance(value, str) else None,
                "source": "none", "rule": "no_overall"}
    return {"tool": tool_id, "available": True, "risk_of_bias": level,
            "source_value": value, "source": source,
            "rule": f"{family}:{key}"}


# ---------------------------------------------------------------------------
# 按设计推荐（复用 study_design 类别面 + 补充关键词）
# ---------------------------------------------------------------------------

#: 研究设计标签 → 推荐工具。可配置：改表即改推荐，无需改端点。
RECOMMENDATION_MAP: dict[str, dict] = {
    "meta_analysis": {
        "primary": "amstar2",
        # 具体 meta 清单按摘要里的原研究类型在 recommend() 中选择
        # （RCT 版 / 观察性版），故不写死在这一格。
        "alternatives": ["casp-systematic-review"],
        "reason_key": "appraisal.recommend.meta_analysis",
    },
    "systematic_review": {
        "primary": "amstar2",
        "alternatives": ["casp-systematic-review"],
        "reason_key": "appraisal.recommend.systematic_review",
    },
    "rct": {
        "primary": "rob2",
        "alternatives": ["casp-rct", "mmat"],
        "reason_key": "appraisal.recommend.rct",
    },
    "cohort": {
        "primary": "casp-cohort",
        "alternatives": ["mmat"],
        "reason_key": "appraisal.recommend.cohort",
    },
    "case_control": {
        "primary": "casp-case-control",
        "alternatives": ["mmat"],
        "reason_key": "appraisal.recommend.case_control",
    },
    "cross_sectional": {
        "primary": "casp-cross-sectional",
        "alternatives": ["axis"],
        "reason_key": "appraisal.recommend.cross_sectional",
    },
    "prevalence": {
        # prevalence / incidence 研究通常以横断面调查实施（补充识别规则）。
        "primary": "casp-cross-sectional",
        "alternatives": ["axis"],
        "reason_key": "appraisal.recommend.prevalence",
    },
    "diagnostic": {
        "primary": "casp-diagnostic",
        "alternatives": [],
        "reason_key": "appraisal.recommend.diagnostic",
    },
    "qualitative": {
        "primary": "casp-qualitative",
        "alternatives": ["mmat"],
        "reason_key": "appraisal.recommend.qualitative",
    },
    "discourse": {
        # 话语/语料分析是质性研究的方法学分支（2.1.5 泳道 D3 新类别）：
        # 主选仍为 CASP 质性清单，MMAT 质性分量表作备选。
        "primary": "casp-qualitative",
        "alternatives": ["mmat"],
        "reason_key": "appraisal.recommend.discourse",
    },
    "survey": {
        # 调查研究（问卷/调查，2.1.5 泳道 D3 新类别）：横断面清单为主选，
        # AXIS 为横断面研究的专用备选。
        "primary": "casp-cross-sectional",
        "alternatives": ["axis"],
        "reason_key": "appraisal.recommend.survey",
    },
    "mixed_methods": {
        "primary": "mmat",
        "alternatives": ["casp-qualitative"],
        "reason_key": "appraisal.recommend.mixed_methods",
    },
    "economic_evaluation": {
        "primary": "casp-economic-evaluation",
        "alternatives": [],
        "reason_key": "appraisal.recommend.economic_evaluation",
    },
    "prediction_rule": {
        "primary": "casp-clinical-prediction-rule",
        "alternatives": [],
        "reason_key": "appraisal.recommend.prediction_rule",
    },
}

#: study_design 未识别（other）时的补充识别规则（顺序即优先级）。
SUPPLEMENTARY_DESIGN_RULES: tuple[tuple[str, tuple[re.Pattern[str], ...]], ...] = (
    ("mixed_methods", (
        re.compile(r"\bmixed[-\s]?methods?\b", re.IGNORECASE),
        re.compile(r"\bmixed\s+methods?\s+(study|design|research)\b", re.IGNORECASE),
    )),
    ("economic_evaluation", (
        re.compile(r"\bcost[-\s](effectiveness|benefit|utility|minimi[sz]ation)\b",
                   re.IGNORECASE),
        re.compile(r"\beconomic\s+evaluation\b", re.IGNORECASE),
    )),
    ("prediction_rule", (
        re.compile(r"\bclinical\s+prediction\s+rule\b", re.IGNORECASE),
        re.compile(r"\b(prediction|prognostic)\s+(rule|model|score)\b", re.IGNORECASE),
        re.compile(r"\brisk\s+(score|model)\b", re.IGNORECASE),
    )),
    ("diagnostic", (
        # study_design 的主规则只认 "diagnostic accuracy" / "sensitivity and
        # specificity"；诊断试验研究的常见措辞（diagnostic test study）在此补齐。
        re.compile(r"\bdiagnostic\s+(test|study|tests|studies)\b", re.IGNORECASE),
        re.compile(r"\bscreening\s+test\b", re.IGNORECASE),
    )),
    ("qualitative", (
        re.compile(r"\bqualitative\b", re.IGNORECASE),
        re.compile(r"\b(interviews?|focus\s+groups?|ethnograph\w*|grounded\s+theory|"
                   r"thematic\s+analysis|phenomenolog\w*)\b", re.IGNORECASE),
    )),
    ("prevalence", (
        re.compile(r"\bprevalence\b", re.IGNORECASE),
        re.compile(r"\bincidence\b", re.IGNORECASE),
    )),
)


def _design_text(article: Mapping[str, Any]) -> str:
    return f"{article.get('title') or ''} {article.get('abstract') or ''}"


_OBSERVATIONAL_RE = re.compile(
    r"\b(observational|cohorts?|cross[-\s]?sectional|case[-\s]?control|survey)\b",
    re.IGNORECASE,
)


def _meta_analysis_alternative(article: Mapping[str, Any]) -> str:
    """含 meta 分析的综述：按原研究设计选 CASP 的 RCT 版或观察性版。"""
    if _OBSERVATIONAL_RE.search(_design_text(article)):
        return "casp-systematic-review-meta-observational"
    return "casp-systematic-review-meta-rct"


def _supplementary_label(article: Mapping[str, Any]) -> tuple[str | None, str | None]:
    text = _design_text(article)
    for label, patterns in SUPPLEMENTARY_DESIGN_RULES:
        for pattern in patterns:
            match = pattern.search(text)
            if match:
                return label, match.group(0)
    return None, None


def _tool_card(tool_id: str, reason_key: str) -> dict:
    tool = REGISTRY[tool_id]
    return {
        "tool_id": tool_id,
        "title": tool["title"],
        "version": tool["version"],
        "citation": tool["citation"],
        "bibtex": tool.get("bibtex", ""),
        "license": copy.deepcopy(tool["license"]),
        "reason_key": reason_key,
    }


def recommend(article: object) -> dict:
    """按研究设计推荐评价工具（返回主选 + 备选 + 理由键）。

    ``article`` 为题录 dict（``title``/``abstract``；可选 ``raw``/``raw_json``
    供 study_design 的出版类型兜底）。纯函数，不读库、不联网。
    """
    if not isinstance(article, Mapping):
        raise AppraisalError("appraisal_article_invalid", "appraisal.article_invalid")

    design: dict = {"label": "other", "recognized": False, "source": None,
                    "matched_keyword": None}
    try:
        from coscreen.al.study_design import classify_article
        design = classify_article(article)
    except Exception:  # noqa: BLE001 —— 分类器不可用时降级为 other（端点仍 200）
        design = {"label": "other", "recognized": False, "source": None,
                  "matched_keyword": None, "degraded": True}

    label = str(design.get("label") or "other")
    matched = design.get("matched_keyword")
    source = "study_design"
    if label == "other":
        supplementary, keyword = _supplementary_label(article)
        if supplementary:
            label = supplementary
            matched = keyword
            source = "supplementary"

    rule = RECOMMENDATION_MAP.get(label)
    if rule is None:
        return {
            "design": {**design, "appraisal_label": label,
                       "appraisal_source": source},
            "primary": None,
            "alternatives": [],
            "reason_key": "appraisal.recommend.unrecognized",
            "available": True,
            "rule_version": RULE_VERSION,
        }

    alternatives = list(rule["alternatives"])
    if label == "meta_analysis":
        alternatives.append(_meta_analysis_alternative(article))

    return {
        "design": {**design, "appraisal_label": label,
                   "appraisal_source": source,
                   "matched_keyword": matched},
        "primary": _tool_card(rule["primary"], rule["reason_key"]),
        "alternatives": [_tool_card(tool_id, "appraisal.recommend.alternative")
                         for tool_id in alternatives],
        "reason_key": rule["reason_key"],
        "available": True,
        "rule_version": RULE_VERSION,
    }


# ---------------------------------------------------------------------------
# 存储：个人库 {slug}.db，与决策同库；表可加、幂等，旧版忽略
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS appraisal_records (
  article_key TEXT NOT NULL,
  tool_id     TEXT NOT NULL,
  responses_json TEXT NOT NULL,
  overall_json   TEXT NOT NULL,
  note           TEXT NOT NULL DEFAULT '',
  updated_at     TEXT NOT NULL,
  PRIMARY KEY (article_key, tool_id)
);
"""


def ensure_appraisal_storage(conn: sqlite3.Connection) -> None:
    """幂等建表（旧库无需迁移；旧版程序不查询该表即忽略）。"""
    conn.executescript(_SCHEMA)


def _record_from_row(row: tuple) -> dict:
    """``appraisal_records`` 单行 → 记录 dict（JSON 损坏一律降级为空对象）。"""
    try:
        payload = json.loads(row[2]) if row[2] else {}
    except ValueError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    try:
        stored_overall = json.loads(row[3]) if row[3] else {}
    except ValueError:
        stored_overall = {}
    if not isinstance(stored_overall, dict):
        stored_overall = {}
    return {
        "article_key": row[0],
        "tool_id": row[1],
        "responses": payload.get("responses") if isinstance(
            payload.get("responses"), dict) else {},
        "comments": payload.get("comments") if isinstance(
            payload.get("comments"), dict) else {},
        "variant": payload.get("variant"),
        "overall": stored_overall,
        "note": row[4],
        "updated_at": row[5],
    }


def load_record(conn: sqlite3.Connection, article_key: str, tool_id: str) -> dict | None:
    """读取一条评价记录（不存在返回 ``None``）。"""
    row = conn.execute(
        "SELECT article_key, tool_id, responses_json, overall_json, note, updated_at "
        "FROM appraisal_records WHERE article_key=? AND tool_id=?",
        (article_key, tool_id),
    ).fetchone()
    if row is None:
        return None
    return _record_from_row(row)


def load_records_for_tool(conn: sqlite3.Connection, tool_id: str) -> list[dict]:
    """读取某工具在单个个人库内的全部评价记录（按 article_key 排序）。

    供双评一致性/导出/GRADE 衔接端点只读扫描全员库使用：SQL 为常量字面量
    + ``?`` 绑定；空库/旧库无 ``appraisal_records`` 表时由调用方按
    ``OperationalError('no such table')`` 视为「无记录」。
    """
    rows = conn.execute(
        "SELECT article_key, tool_id, responses_json, overall_json, note, updated_at "
        "FROM appraisal_records WHERE tool_id=? ORDER BY article_key",
        (tool_id,),
    ).fetchall()
    return [_record_from_row(row) for row in rows]


def save_record(conn: sqlite3.Connection, article_key: str, tool_id: str, *,
                responses: Mapping[str, str], comments: Mapping[str, str],
                variant: str | None, overall: Mapping[str, Any],
                note: str = "") -> dict:
    """插入或覆盖一条评价记录（同一 article_key+tool_id 幂等）。"""
    payload = json.dumps({"responses": dict(responses), "comments": dict(comments),
                          "variant": variant}, ensure_ascii=False, sort_keys=True)
    overall_json = json.dumps(dict(overall), ensure_ascii=False, sort_keys=True,
                              allow_nan=False)
    updated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn.execute(
        "INSERT INTO appraisal_records "
        "(article_key, tool_id, responses_json, overall_json, note, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(article_key, tool_id) DO UPDATE SET "
        "responses_json=excluded.responses_json, "
        "overall_json=excluded.overall_json, "
        "note=excluded.note, updated_at=excluded.updated_at",
        (article_key, tool_id, payload, overall_json, note, updated_at),
    )
    return {
        "article_key": article_key,
        "tool_id": tool_id,
        "responses": dict(responses),
        "comments": dict(comments),
        "variant": variant,
        "overall": dict(overall),
        "note": note,
        "updated_at": updated_at,
    }
