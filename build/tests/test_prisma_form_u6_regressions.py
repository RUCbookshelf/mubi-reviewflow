"""PRISMA 表单（coscreen/stats/prisma_form.py）r6 修复回归钉。

覆盖（每条均按首审/复审复现手法原样回放修复前的失败输入）：
- U6-P1/R6-F1：v2 其他方法列 R4 勾稽（sought − not retrieved = assessed）
  为 error；合法 other 链 DOT 呈现 notret_other 灰框；
- r5#49：R5 单列余量——某列 Σ排除理由不得超该列评估数（负余量不得由
  另一列抵消）；
- U6-P2/R6-F4：R14 纳入研究数 ≤ 纳入报告数（> 为 error、0 研究配正报告
  为 warning）；
- U6-P6/P10：STEP_FIELDS 覆盖 v2 其他列与脚注**字段，validate_step 可见；
  R10 拆分错误按 R6-F6 方案 B 降级为 warning；
- U6-P9：官方模板（题摘排除仅报总数）ok=True（R13 warning）；
- U6-P12：v2 DOT rank=same 组去重（同一节点不再出现在多条 rank 语句）。
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from coscreen.stats.prisma_form import (
    IdentifiedRow,
    PrismaFormData,
    ReasonRow,
    prisma_form_dot,
    validate,
    validate_step,
)


def _legal_v1_form(**overrides) -> PrismaFormData:
    """合法 v1（new）基线表单：db 链 100→20 移除→80 筛→60 排→20 寻求→
    18 评估→10 理由→8 报告、6 研究（全部勾稽通过）。"""
    defaults = dict(
        identified_rows_db=[IdentifiedRow("database", "WOS", 100)],
        duplicates_removed=20,
        records_screened=80,
        records_excluded_screening=60,
        screening_exclusion_reasons=[ReasonRow("scope", 60)],
        reports_sought_db=20,
        reports_not_retrieved_db=2,
        reports_assessed_db=18,
        exclusion_reasons_db=[ReasonRow("design", 10)],
        studies_included=6,
        reports_of_included_studies=8,
    )
    defaults.update(overrides)
    return PrismaFormData(**defaults)


def _legal_v2_form(**overrides) -> PrismaFormData:
    """合法 v2（new）基线表单：db 链同上；other 链 10 寻求→0 未获取→
    10 评估→2 理由；末框 16 报告 / 10 研究。"""
    defaults = dict(
        sources_scope="db_reg_other",
        identified_rows_db=[IdentifiedRow("database", "WOS", 100)],
        identified_rows_other=[IdentifiedRow("website", "GS", 10)],
        duplicates_removed=20,
        records_screened=80,
        records_excluded_screening=60,
        screening_exclusion_reasons=[ReasonRow("scope", 60)],
        reports_sought_db=20,
        reports_not_retrieved_db=2,
        reports_assessed_db=18,
        exclusion_reasons_db=[ReasonRow("design", 10)],
        reports_sought_other=10,
        reports_not_retrieved_other=0,
        reports_assessed_other=10,
        exclusion_reasons_other=[ReasonRow("setting", 2)],
        studies_included=10,
        reports_of_included_studies=16,
    )
    defaults.update(overrides)
    return PrismaFormData(**defaults)


# ---------------------------------------------------------------------------
# U6-P1 / R6-F1：other 列 R4 勾稽
# ---------------------------------------------------------------------------

class OtherColumnR4Tests(unittest.TestCase):
    def test_v2_other_column_r4_mismatch_is_error(self):
        # 首审 U6-P1 同法：other sought=10/not_retrieved=0/assessed=5/理由=2，
        # 修复前 ok=True、errors=[]，缺口被 R6/R12 掩盖。
        form = _legal_v2_form(
            reports_assessed_other=5,
            reports_of_included_studies=11,  # R5 总额仍对齐：(18-10)+(5-2)=11
        )
        result = validate(form)
        self.assertFalse(result.ok)
        self.assertEqual([i.rule for i in result.errors], ["R4"])
        self.assertEqual(result.errors[0].field, "reports_assessed_other")
        self.assertEqual(result.errors[0].expected, 10)
        self.assertEqual(result.errors[0].actual, 5)

    def test_v2_valid_other_chain_renders_notret_other(self):
        # 合法 other 链（未获取 >0）时 DOT 呈现完整 other 链含 notret_other 灰框。
        form = _legal_v2_form(
            reports_not_retrieved_other=3,
            reports_assessed_other=7,
            reports_of_included_studies=13,  # (18-10)+(7-2)=13
        )
        result = validate(form)
        self.assertTrue(result.ok, [i.text for i in result.errors])
        dot = prisma_form_dot(form)
        self.assertIn("notret_other", dot)
        self.assertIn("未获取到的报告（n = 3）", dot)


# ---------------------------------------------------------------------------
# r5#49：R5 单列余量
# ---------------------------------------------------------------------------

class PerColumnReasonCapTests(unittest.TestCase):
    def test_single_column_negative_margin_not_offset_by_other_column(self):
        # r5 复审 #49 同法：db 18/10 + other 5/10 + reports=3——总额勾稽通过
        # （(18-10)+(5-10)=3），other 列负余量修复前被 db 列抵消。
        form = _legal_v2_form(
            exclusion_reasons_other=[ReasonRow("setting", 10)],
            reports_assessed_other=5,
            reports_of_included_studies=3,
        )
        result = validate(form)
        self.assertFalse(result.ok)
        self.assertIn("R5", [i.rule for i in result.errors])
        r5 = [i for i in result.errors if i.rule == "R5"][0]
        self.assertEqual(r5.field, "exclusion_reasons_other")

    def test_db_column_reason_cap(self):
        form = _legal_v1_form(
            exclusion_reasons_db=[ReasonRow("design", 19)],
            reports_of_included_studies=8,
        )
        result = validate(form)
        self.assertFalse(result.ok)
        r5_fields = [i.field for i in result.errors if i.rule == "R5"]
        self.assertIn("exclusion_reasons_db", r5_fields)

    def test_balanced_columns_still_ok(self):
        form = _legal_v2_form()
        self.assertTrue(validate(form).ok)


# ---------------------------------------------------------------------------
# U6-P2 / R6-F4：R14 研究数 ≤ 报告数
# ---------------------------------------------------------------------------

class StudyReportConsistencyTests(unittest.TestCase):
    def test_new_mode_studies_exceed_reports_is_error(self):
        # 首审 U6-P2 同法：assessed=18/理由=18/studies=5/reports=0，
        # 修复前 ok=True、DOT final_box「研究 n=5 / 报告 n=0」。
        form = _legal_v1_form(
            exclusion_reasons_db=[ReasonRow("design", 18)],
            studies_included=5,
            reports_of_included_studies=0,
        )
        result = validate(form)
        self.assertFalse(result.ok)
        self.assertIn("R14", [i.rule for i in result.errors])
        self.assertEqual(
            [i for i in result.errors if i.rule == "R14"][0].field, "studies_included"
        )

    def test_updated_new_zero_studies_with_reports_warns_only(self):
        # 首审 U6-P2 同法：previous 4/5、new 0/8、total 4/13——修复前无任何
        # 提示；现为 warning（可能是填写口径问题，不阻断）。
        form = _legal_v1_form(
            review_mode="updated",
            exclusion_reasons_db=[ReasonRow("design", 10)],
            reports_of_included_studies=13,
            studies_included=4,
            previous_studies=4,
            previous_reports=5,
            new_studies=0,
            new_reports=8,
            total_studies=4,
            total_reports=13,
        )
        result = validate(form)
        self.assertTrue(result.ok, [i.text for i in result.errors])
        self.assertIn("R14", [i.rule for i in result.warnings])

    def test_equal_studies_reports_ok(self):
        form = _legal_v1_form(studies_included=8, reports_of_included_studies=8)
        self.assertTrue(validate(form).ok)


# ---------------------------------------------------------------------------
# U6-P6 / U6-P10：STEP_FIELDS 覆盖 + R10 降级（R6-F6 方案 B）
# ---------------------------------------------------------------------------

class StepFieldCoverageTests(unittest.TestCase):
    def test_v2_other_row_error_visible_in_s1(self):
        # 首审 U6-P6 探针 1：other 空名称 → R1(field=identified_rows_other)，
        # 修复前 s1–s4 全 ok=True、issues=[]。
        form = _legal_v2_form(
            identified_rows_other=[IdentifiedRow("website", "", 5)],
        )
        result = validate(form)
        self.assertIn("R1", [i.rule for i in result.errors])
        self.assertEqual(
            [i for i in result.errors if i.rule == "R1"][0].field, "identified_rows_other"
        )
        step = validate_step(form, "s1")
        self.assertFalse(step.ok)
        self.assertIn("identified_rows_other", [i.field for i in step.issues])

    def test_negative_human_count_visible_in_s2(self):
        # 首审 U6-P6 探针 3：human_excluded_n=-1 → R9(field=human_excluded_n)。
        form = _legal_v1_form(human_excluded_n=-1, automation_excluded_n=61)
        step = validate_step(form, "s2")
        self.assertFalse(step.ok)
        self.assertIn("human_excluded_n", [i.field for i in step.issues])

    def test_other_column_fields_visible_in_s3(self):
        form = _legal_v2_form(reports_not_retrieved_other=-2)
        step = validate_step(form, "s3")
        self.assertFalse(step.ok)
        self.assertIn("reports_not_retrieved_other", [i.field for i in step.issues])
        # R4 other 列勾稽错误同样落在 s3
        form2 = _legal_v2_form(reports_assessed_other=5, reports_of_included_studies=11)
        step2 = validate_step(form2, "s3")
        self.assertIn("reports_assessed_other", [i.field for i in step2.issues])

    def test_r10_split_mismatch_is_warning_not_error(self):
        # R6-F6 方案 B：产品页无人工/自动化拆分输入，R10 不再阻断。
        form = _legal_v1_form(human_excluded_n=50, automation_excluded_n=5)
        result = validate(form)
        self.assertTrue(result.ok, [i.text for i in result.errors])
        self.assertIn("R10", [i.rule for i in result.warnings])

    def test_r10_half_filled_is_warning(self):
        form = _legal_v1_form(human_excluded_n=None, automation_excluded_n=5)
        result = validate(form)
        self.assertTrue(result.ok, [i.text for i in result.errors])
        self.assertIn("R10", [i.rule for i in result.warnings])


# ---------------------------------------------------------------------------
# U6-P9：官方模板合规（R13 仅报总数 → warning）
# ---------------------------------------------------------------------------

class OfficialTemplateTests(unittest.TestCase):
    @staticmethod
    def official_template_form() -> PrismaFormData:
        # r5 复审 #51 / 首审 U6-P9 同法：识别 120+30（两行数据库）、
        # 筛前移除 20+10、进入筛选 120、题摘排除 30 仅报总数、
        # 全文 90→80→50 全对齐——官方 2020 模板式表单。
        return PrismaFormData(
            identified_rows_db=[
                IdentifiedRow("database", "Medline", 120),
                IdentifiedRow("database", "Embase", 30),
            ],
            duplicates_removed=20,
            removed_other=10,
            records_screened=120,
            records_excluded_screening=30,
            reports_sought_db=90,
            reports_not_retrieved_db=10,
            reports_assessed_db=80,
            exclusion_reasons_db=[ReasonRow("design", 30)],
            studies_included=40,
            reports_of_included_studies=50,
        )

    def test_official_template_total_only_passes(self):
        # 修复前唯一 error 为 R13（Σ理由 0 ≠ 排除 30），ok=False。
        result = validate(self.official_template_form())
        self.assertTrue(result.ok, [i.text for i in result.errors])
        self.assertIn("R13", [i.rule for i in result.warnings])

    def test_itemized_mismatch_still_errors(self):
        form = self.official_template_form()
        form.screening_exclusion_reasons = [ReasonRow("scope", 20)]
        result = validate(form)
        self.assertFalse(result.ok)
        self.assertIn("R13", [i.rule for i in result.errors])


# ---------------------------------------------------------------------------
# U6-P12：rank=same 组去重
# ---------------------------------------------------------------------------

class DotRankDedupTests(unittest.TestCase):
    @staticmethod
    def _rank_groups(dot: str) -> list[list[str]]:
        groups = []
        for match in re.finditer(r"\{\s*rank\s*=\s*same;\s*([^}]*)\}", dot):
            groups.append([name.strip() for name in match.group(1).split(";") if name.strip()])
        return groups

    def test_v2_nodes_appear_in_single_rank_group(self):
        # 首审 U6-P12 同法：v2 输出 9 条 rank=same，ident_db/removed_db/
        # sought_db/assessed_db/excl_ft_db 各出现 2 次。
        form = _legal_v2_form(
            reports_not_retrieved_other=3,
            reports_assessed_other=7,
            reports_of_included_studies=13,
        )
        self.assertTrue(validate(form).ok)
        groups = self._rank_groups(prisma_form_dot(form))
        occurrences: dict[str, int] = {}
        for group in groups:
            for name in group:
                occurrences[name] = occurrences.get(name, 0) + 1
        self.assertEqual(
            {name: n for name, n in occurrences.items() if n > 1}, {},
            f"重复出现在多条 rank=same 的节点: {occurrences}",
        )
        # 合并后语义不变：ident_db/removed_db/ident_other 同组。
        merged_group = [g for g in groups if "ident_db" in g][0]
        self.assertIn("removed_db", merged_group)
        self.assertIn("ident_other", merged_group)

    def test_dot_output_deterministic(self):
        form = _legal_v2_form()
        self.assertEqual(prisma_form_dot(form), prisma_form_dot(form))


if __name__ == "__main__":
    unittest.main()
