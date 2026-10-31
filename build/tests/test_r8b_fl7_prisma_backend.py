"""r8b FL7 PRISMA 后端回归钉（r6 #49/#54）。

破坏即红（修复前 a080fde 上必失败），探针形态与 r6 复审一致
（E:/mubi-reviewflow/out/external_reviews/r6_verify_prisma.md）：

- #49：同键两行分属 agree-include / agree-exclude 的手工/历史合并表，
  included+excluded<=screened 不变量修复前被打破（screened=1、
  included=1、excluded=1）且零告警——修复后跨桶互斥折叠 + 告警，
  agree/multi/two_stage 三路径同口径。
- #54：R14「研究数 > 报告数」在报告数 ≥ 1 时降为 warning（官方模板
  multi-study report 合法形态，修复前唯一 error 阻断导出）；报告数=0
  仍 error。
"""
from __future__ import annotations

import re
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from coscreen.stats.prisma import (
    prisma_dot,
    prisma_from_merge,
    prisma_from_merge_multi,
    prisma_two_stage,
)
from coscreen.stats.prisma_form import (
    IdentifiedRow,
    PrismaFormData,
    ReasonRow,
    validate,
)


def _v1_form(**overrides) -> PrismaFormData:
    """全勾稽 v1 基线（r6 #54 探针 A1 同款数字）。"""
    defaults = dict(
        identified_rows_db=[IdentifiedRow("database", "WOS", 100)],
        duplicates_removed=20,
        records_screened=80,
        records_excluded_screening=60,
        screening_exclusion_reasons=[ReasonRow("scope", 60)],
        reports_sought_db=20,
        reports_not_retrieved_db=2,
        reports_assessed_db=18,
        exclusion_reasons_db=[ReasonRow("design", 14)],
        studies_included=6,
        reports_of_included_studies=4,
    )
    defaults.update(overrides)
    return PrismaFormData(**defaults)


def _conflict_table() -> pd.DataFrame:
    """r6 #49 探针同款：同键 k1 两行（agree include / agree exclude）。"""
    with tempfile.TemporaryDirectory(prefix="rf_r8b_fl7_prisma_") as tmp:
        path = Path(tmp) / "probe49.sqlite"
        conn = sqlite3.connect(path)
        conn.execute(
            "CREATE TABLE merged_agree (zotero_key TEXT, decision_A TEXT,"
            " decision_B TEXT, status TEXT)")
        conn.executemany(
            "INSERT INTO merged_agree VALUES (?,?,?,?)",
            [("k1", "include", "include", "agree"),
             ("k1", "exclude", "exclude", "agree")],
        )
        conn.commit()
        df = pd.read_sql_query("SELECT * FROM merged_agree", conn)
        conn.close()
        return df


def _conflict_table_multi() -> pd.DataFrame:
    """multi 形态同款冲突表（两名筛选员列 + status）。"""
    with tempfile.TemporaryDirectory(prefix="rf_r8b_fl7_prisma_") as tmp:
        path = Path(tmp) / "probe49m.sqlite"
        conn = sqlite3.connect(path)
        conn.execute(
            "CREATE TABLE merged_multi (zotero_key TEXT, decision_alice TEXT,"
            " decision_bob TEXT, status TEXT)")
        conn.executemany(
            "INSERT INTO merged_multi VALUES (?,?,?,?)",
            [("k1", "include", "include", "agree"),
             ("k1", "exclude", "exclude", "agree")],
        )
        conn.commit()
        df = pd.read_sql_query("SELECT * FROM merged_multi", conn)
        conn.close()
        return df


class CrossBucketExclusionTests(unittest.TestCase):
    """#49：跨桶互斥折叠 + 告警（三路径）。"""

    def test_agree_path_invariant_and_warning(self):
        merged = _conflict_table()
        flow = prisma_from_merge(merged)
        # 修复前：screened=1 included=1 excluded=1（和 2 > 1），warnings 空
        self.assertEqual(flow.screened, 1)
        self.assertLessEqual(
            flow.included + flow.excluded, flow.screened,
            "included+excluded 不得突破 screened（r6 #49）")
        self.assertEqual(flow.included + flow.excluded, 1)
        self.assertTrue(
            any("跨桶" in w and "k1" in w for w in flow.warnings),
            flow.warnings)
        # 每键只计一次：首现行（agree include）归属
        self.assertEqual((flow.included, flow.excluded), (1, 0))

    def test_multi_path_invariant_and_warning(self):
        flow = prisma_from_merge_multi(_conflict_table_multi())
        self.assertEqual(flow.screened, 1)
        self.assertLessEqual(flow.included + flow.excluded, flow.screened)
        self.assertTrue(any("跨桶" in w for w in flow.warnings), flow.warnings)

    def test_two_stage_invariant_and_warning(self):
        merged = _conflict_table()
        flow = prisma_two_stage(merged, merged)
        self.assertEqual(flow.screened, 1)
        self.assertLessEqual(flow.included + flow.excluded, flow.screened)
        self.assertTrue(any("跨桶" in w for w in flow.warnings), flow.warnings)

    def test_dot_has_no_negative_pending_and_carries_warning(self):
        merged = _conflict_table()
        flow = prisma_from_merge(merged)
        dot = prisma_dot(flow)
        # 修复前 pending=-1：不画未决节点也无告警节点
        self.assertNotIn("未决", dot)
        self.assertIn("勾稽告警", dot)
        self.assertIn("跨桶", dot)

    def test_two_stage_invariant_is_row_order_independent(self):
        """r6 #49 二轮阻断项：同键 agree include + agree exclude 两种行序，
        two_stage 都须 included+excluded<=screened。

        修复前 stage2 键域取「全部 agree include 键」，exclude 行在前时
        stage2 的排除落进域内 → stage1.excluded + excluded2 = 0+2 > screened=1。
        """
        for name, rows in (
            ("include_first", [("k1", "include", "include", "agree"),
                               ("k1", "exclude", "exclude", "agree")]),
            ("exclude_first", [("k1", "exclude", "exclude", "agree"),
                               ("k1", "include", "include", "agree")]),
        ):
            with self.subTest(order=name):
                merged = pd.DataFrame(
                    rows, columns=["zotero_key", "decision_A", "decision_B", "status"])
                flow = prisma_two_stage(merged, merged)
                self.assertLessEqual(
                    flow.included + flow.excluded, flow.screened,
                    f"{name}: {flow.included}+{flow.excluded} > screened {flow.screened}")
                # 真正可失败的断言：DOT 里 excluded 节点数值 == flow.excluded
                dot = prisma_dot(flow)
                m_exc = re.search(r'excluded \[label="排除\\n(\d+) 篇"\]', dot)
                self.assertIsNotNone(m_exc, dot)
                self.assertEqual(int(m_exc.group(1)), flow.excluded)

    def test_two_stage_dot_has_no_contradictory_counts(self):
        """r6 #49 二轮：exclude 行在前时 DOT 不得出现「排除 2 篇 / 筛选 1 篇」。"""
        rows = [("k1", "exclude", "exclude", "agree"),
                ("k1", "include", "include", "agree")]
        merged = pd.DataFrame(
            rows, columns=["zotero_key", "decision_A", "decision_B", "status"])
        flow = prisma_two_stage(merged, merged)
        dot = prisma_dot(flow)
        # 解析 DOT 中的 excluded 节点数值（旧写法 needle 含真实换行，恒真）
        m_exc = re.search(r'excluded \[label="排除\\n(\d+) 篇"\]', dot)
        self.assertIsNotNone(m_exc, dot)
        self.assertEqual(int(m_exc.group(1)), flow.excluded)
        self.assertLessEqual(flow.included + flow.excluded, flow.screened)
        self.assertNotIn("排除\\n2 篇", dot)  # 反斜杠+n 形态（可失败）

    def test_normal_single_bucket_input_unchanged(self):
        """对照组（r6 探针同款）：单行 k1 include → 1/0、无告警。"""
        with tempfile.TemporaryDirectory(prefix="rf_r8b_fl7_prisma_") as tmp:
            path = Path(tmp) / "normal.sqlite"
            conn = sqlite3.connect(path)
            conn.execute(
                "CREATE TABLE merged_agree (zotero_key TEXT, decision_A TEXT,"
                " decision_B TEXT, status TEXT)")
            conn.execute(
                "INSERT INTO merged_agree VALUES ('k1','include','include','agree')")
            conn.commit()
            df = pd.read_sql_query("SELECT * FROM merged_agree", conn)
            conn.close()
        flow = prisma_from_merge(df)
        self.assertEqual((flow.included, flow.excluded), (1, 0))
        self.assertFalse(any("跨桶" in w for w in flow.warnings))


class ResolvedPathWarningTests(unittest.TestCase):
    """r6 #49 三轮：resolved / resolved2 两条仲裁路径的告警语义。"""

    def test_resolved_path_reports_cross_bucket_overlap(self):
        """非 resolved 分支已有的跨桶告警，resolved 分支也不得静默。"""
        merged = _conflict_table()
        resolved = pd.DataFrame({"zotero_key": ["k1"], "final_decision": ["include"]})
        flow = prisma_from_merge(merged, resolved=resolved)
        self.assertTrue(any("跨桶" in w for w in flow.warnings), flow.warnings)

    def test_two_stage_resolved2_conflict_is_not_called_ghost(self):
        """stage1 判 exclude 的键出现在 resolved2（判 include）时要报
        「与初筛归属冲突」，而不是误导性的「键域外（幽灵/未筛）」。"""
        # exclude 行在前 ⇒ stage1 折叠后该键归属 exclude（不在 include 域）
        merged = pd.DataFrame(
            [("k1", "exclude", "exclude", "agree"),
             ("k1", "include", "include", "agree")],
            columns=["zotero_key", "decision_A", "decision_B", "status"])
        resolved2 = pd.DataFrame(
            {"zotero_key": ["k1"], "final_decision": ["include"]})
        flow = prisma_two_stage(merged, merged, resolved2=resolved2)
        joined = " | ".join(flow.warnings)
        self.assertIn("与初筛归属冲突", joined)
        self.assertNotIn("幽灵", joined)
        self.assertLessEqual(flow.included + flow.excluded, flow.screened)


class R14MultiStudyReportTests(unittest.TestCase):
    """#54：studies > reports 且 reports ≥ 1 → warning（R14W）不阻断。"""

    def test_new_mode_studies_exceed_reports_with_reports_ok(self):
        """r6 探针 A1：研究 6 / 报告 4，其余全勾稽 → 修复前 ok=False 唯一
        error= R14；现 ok=True 且 R14W 提示可在备注中说明。"""
        result = validate(_v1_form())
        self.assertTrue(result.ok, [i.text for i in result.errors])
        r14w = [i for i in result.warnings if i.rule == "R14W"]
        self.assertEqual(len(r14w), 1)
        self.assertIn("multi-study report", r14w[0].zh)
        self.assertIn("可在备注中说明", r14w[0].zh)

    def test_new_mode_studies_exceed_zero_reports_still_error(self):
        """报告数=0 且研究数>0 仍 error（无任何报告却纳入研究）。"""
        result = validate(_v1_form(
            exclusion_reasons_db=[ReasonRow("design", 18)],
            reports_of_included_studies=0))
        self.assertFalse(result.ok)
        r14 = [i for i in result.errors if i.rule == "R14"]
        self.assertEqual(len(r14), 1)
        self.assertEqual(r14[0].field, "studies_included")

    def test_updated_previous_studies_exceed_reports_ok(self):
        """r6 探针：updated previous 6/4（new 2/4、total 8/8 其余对齐）→
        修复前唯一 error= R14（updated 连上一版合法数字都无法导入）。"""
        result = validate(_v1_form(
            review_mode="updated",
            exclusion_reasons_db=[ReasonRow("design", 14)],
            reports_of_included_studies=8,
            studies_included=8,
            previous_studies=6,
            previous_reports=4,
            new_studies=2,
            new_reports=4,
            total_studies=8,
            total_reports=8,
        ))
        self.assertTrue(result.ok, [i.text for i in result.errors])
        self.assertIn("R14W", [i.rule for i in result.warnings])

    def test_control_equal_reports_still_clean(self):
        """对照（r6 探针变体）：研究 4 / 报告 4（Σ理由 14 → 报告=4 勾稽）→
        ok 且无 R14/R14W。"""
        result = validate(_v1_form(studies_included=4,
                                   reports_of_included_studies=4))
        self.assertTrue(result.ok, [i.text for i in result.errors])
        self.assertNotIn("R14W", [i.rule for i in result.warnings])


if __name__ == "__main__":
    unittest.main()
