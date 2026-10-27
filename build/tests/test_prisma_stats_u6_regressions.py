"""PRISMA 流程统计（coscreen/stats/prisma.py + merge.py）r6 修复回归钉。

覆盖（每条均按首审/复审复现手法原样回放修复前的失败输入）：
- U6-P3/P7/R6-F2/R6-F9：双空决策归 unscreened 不计 agree/screened；
  prisma_from_merge(_multi) 只计至少一名筛选员有实际决策的记录（兼容
  旧表 status 误标）；
- U6-P5/R6-F7：prisma_dot 补「未决」节点，分支之和与 screened 勾稽
  （conflict/单边/一致 maybe 不再静默消失）；
- U6-P11/R6-F5：resolved/two_stage 键域钳制 + 去重，included+excluded
  <= screened 恒成立；report 侧 identified−duplicates==screened 勾稽告警；
- U6-P4（n_duplicates 记录口径）复核确认仍在场（R6-F4 要求先复核）。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pandas as pd

from coscreen.stats.merge import merge_decisions, merge_decisions_multi, summarize
from coscreen.stats.prisma import (
    prisma_dot,
    prisma_from_merge,
    prisma_from_merge_multi,
    prisma_two_stage,
)


def _decisions_df(keys_decisions: list[tuple[str, str]]) -> pd.DataFrame:
    return pd.DataFrame(
        {"zotero_key": [k for k, _ in keys_decisions], "decision": [d for _, d in keys_decisions]}
    )


# ---------------------------------------------------------------------------
# U6-P3 / U6-P7 / R6-F2 / R6-F9：空决策不入 screened
# ---------------------------------------------------------------------------

class BlankDecisionTests(unittest.TestCase):
    @staticmethod
    def _blank_probe_dfs() -> tuple[pd.DataFrame, pd.DataFrame]:
        # 首审 U6-P3 同法：k1 双方 decision=''、k2 双方 'include'。
        return (
            _decisions_df([("k1", ""), ("k2", "include")]),
            _decisions_df([("k1", ""), ("k2", "include")]),
        )

    def test_both_blank_is_unscreened_not_agree(self):
        df_a, df_b = self._blank_probe_dfs()
        merged = merge_decisions(df_a, df_b)
        self.assertEqual(merged["status"].tolist(), ["unscreened", "agree"])

    def test_prisma_from_merge_screened_excludes_never_screened(self):
        df_a, df_b = self._blank_probe_dfs()
        merged = merge_decisions(df_a, df_b)
        flow = prisma_from_merge(merged)
        # R6-F2 验收：screened=1（仅 k2），included=1；report=None 的兜底
        # 口径下 identified = screened（无去重报告时不臆造检索总数）。
        self.assertEqual(
            (flow.identified, flow.duplicates_removed, flow.screened, flow.excluded, flow.included),
            (1, 0, 1, 0, 1),
        )

    def test_legacy_table_with_mislabeled_agree_not_screened(self):
        # 兼容旧版合并表：双空行曾被标 status='agree'（CSV 往返产物），
        # 修复后按决策列实际值判断，仍不计入 screened。
        merged = pd.DataFrame(
            {
                "zotero_key": ["k1", "k2"],
                "decision_A": ["", "include"],
                "decision_B": ["", "include"],
                "status": ["agree", "agree"],
            }
        )
        flow = prisma_from_merge(merged)
        self.assertEqual(flow.screened, 1)
        self.assertEqual(flow.included, 1)

    def test_multi_incomplete_zero_screened_not_counted(self):
        # 首审 U6-P3 同法：multi 路径 k1 n_screened=0/incomplete 仍计
        # screened=2；修复后只计有实际决策的键。
        df = _decisions_df([("k1", ""), ("k2", "include")])
        merged = merge_decisions_multi({"R1": df, "R2": df.copy()})
        self.assertEqual(merged["status"].tolist(), ["incomplete", "agree"])
        flow = prisma_from_merge_multi(merged)
        self.assertEqual(flow.screened, 1)
        self.assertEqual(flow.included, 1)

    def test_summarize_ignores_unscreened_rows(self):
        df_a, df_b = self._blank_probe_dfs()
        merged = merge_decisions(df_a, df_b)
        summary = summarize(merged)
        self.assertEqual((summary.n_total, summary.n_agree, summary.n_conflict), (2, 1, 0))


# ---------------------------------------------------------------------------
# U6-P5 / R6-F7：未决节点
# ---------------------------------------------------------------------------

class PendingNodeTests(unittest.TestCase):
    @staticmethod
    def _four_status_merged() -> pd.DataFrame:
        # 首审 U6-P5 同法：agree include / agree exclude / conflict / A_only。
        return pd.DataFrame(
            {
                "zotero_key": ["k1", "k2", "k3", "k4"],
                "decision_A": ["include", "exclude", "include", "include"],
                "decision_B": ["include", "exclude", "exclude", ""],
                "status": ["agree", "agree", "conflict", "A_only"],
            }
        )

    def test_flow_counts_undecided_as_gap(self):
        flow = prisma_from_merge(self._four_status_merged())
        self.assertEqual(
            (flow.identified, flow.screened, flow.excluded, flow.included), (4, 4, 1, 1)
        )

    def test_prisma_dot_renders_pending_node(self):
        # 修复前 DOT 只有 identified/screened/excluded/included 四节点，
        # conflict/A_only 的 2 条记录在图中静默消失。
        flow = prisma_from_merge(self._four_status_merged())
        dot = prisma_dot(flow)
        self.assertIn("pending", dot)
        self.assertIn("未决", dot)
        self.assertIn("screened -> pending", dot)

    def test_agree_maybe_is_pending_not_dropped(self):
        # R6-F7 同法：k1 双方 maybe、k2 双方 include——maybe 一致不计入
        # included/excluded，属未决。
        df = _decisions_df([("k1", "maybe"), ("k2", "include")])
        merged = merge_decisions(df, df.copy())
        flow = prisma_from_merge(merged)
        self.assertEqual((flow.screened, flow.included, flow.excluded), (2, 1, 0))
        dot = prisma_dot(flow)
        self.assertIn("未决（分歧/待仲裁）\\n1 篇", dot)

    def test_fully_resolved_flow_has_no_pending_node(self):
        df = _decisions_df([("k1", "include"), ("k2", "exclude")])
        flow = prisma_from_merge(merge_decisions(df, df.copy()))
        self.assertEqual(flow.screened, flow.included + flow.excluded)
        self.assertNotIn("pending", prisma_dot(flow))


# ---------------------------------------------------------------------------
# U6-P11 / R6-F5：键域钳制、去重与 report 勾稽告警；U6-P4 复核
# ---------------------------------------------------------------------------

class ResolvedDomainTests(unittest.TestCase):
    def test_resolved_ghost_keys_ignored(self):
        # 首审 U6-P11 同法：merged 仅 1 行 agree-exclude，resolved 含 3 键
        # （exclude/include/include）→ 修复前 included=2+excluded=1=3 > screened=1。
        merged = pd.DataFrame(
            {
                "zotero_key": ["k1"],
                "decision_A": ["exclude"],
                "decision_B": ["exclude"],
                "status": ["agree"],
            }
        )
        resolved = pd.DataFrame(
            {
                "zotero_key": ["k1", "k2", "k3"],
                "final_decision": ["exclude", "include", "include"],
            }
        )
        flow = prisma_from_merge(merged, resolved=resolved)
        self.assertEqual((flow.screened, flow.included, flow.excluded), (1, 0, 1))
        self.assertLessEqual(flow.included + flow.excluded, flow.screened)
        self.assertTrue(any("键域外" in w and "k2" in w for w in flow.warnings))

    def test_multi_resolved_duplicate_and_ghost_keys_counted_once(self):
        # R6-F5 同法：resolved={k1,k1,k9} 均 include → 修复前 included=3 > screened=2。
        df = _decisions_df([("k1", "include"), ("k2", "")])
        merged = merge_decisions_multi({"R1": df, "R2": df.copy()})
        resolved = pd.DataFrame(
            {
                "zotero_key": ["k1", "k1", "k9"],
                "final_decision": ["include", "include", "include"],
            }
        )
        flow = prisma_from_merge_multi(merged, resolved=resolved)
        self.assertEqual(flow.screened, 1)
        self.assertEqual(flow.included, 1)
        self.assertLessEqual(flow.included + flow.excluded, flow.screened)
        self.assertTrue(any("k9" in w for w in flow.warnings))

    def test_two_stage_stage2_outside_stage1_includes_ignored(self):
        # R6-F5 同法：初筛纳入 1 条，复筛 5 条 exclude → 修复前 excluded=5 > screened=1。
        stage1 = pd.DataFrame(
            {
                "zotero_key": ["k1"],
                "decision_A": ["include"],
                "decision_B": ["include"],
                "status": ["agree"],
            }
        )
        stage2 = pd.DataFrame(
            {
                "zotero_key": ["k1", "k2", "k3", "k4", "k5"],
                "decision_A": ["exclude"] * 5,
                "decision_B": ["exclude"] * 5,
                "status": ["agree"] * 5,
            }
        )
        flow = prisma_two_stage(stage1, stage2)
        self.assertEqual(flow.screened, 1)
        self.assertEqual(flow.excluded, 1)
        self.assertEqual(flow.included, 0)
        self.assertLessEqual(flow.included + flow.excluded, flow.screened)
        self.assertTrue(any("stage2" in w for w in flow.warnings))

    def test_report_reconciliation_mismatch_warned(self):
        # 首审 U6-P11 E11b 同法：DedupReport(total=10, unique=8) + 5 行已筛
        # → identified=10、duplicates_removed=2、screened=5（8≠5），
        # 修复前无任何警示。
        class _Report:  # 鸭子类型去重报告（prisma_from_merge 不 import dedup）
            total = 10
            unique = 8

            def n_duplicates(self) -> int:
                return self.total - self.unique

        merged = merge_decisions(
            _decisions_df([(f"k{i}", "include") for i in range(5)]),
            _decisions_df([(f"k{i}", "include") for i in range(5)]),
        )
        flow = prisma_from_merge(merged, report=_Report())
        self.assertEqual((flow.identified, flow.duplicates_removed, flow.screened), (10, 2, 5))
        self.assertTrue(any("identified" in w for w in flow.warnings))
        self.assertIn("warnings", flow.to_dict())
        self.assertTrue(flow.to_dict()["warnings"])

    def test_u6p4_record_caliber_duplicate_count_confirm(self):
        # U6-P4 复核（R6-F4：先复核确认）：两条同 DOI+同键记录的
        # n_duplicates 按记录口径（total−unique），identified−duplicates==screened。
        from coscreen.dedup import find_duplicates
        from coscreen.models import Article

        articles = [
            Article(zotero_key="K1", title="T", doi="10.1/x"),
            Article(zotero_key="K1", title="T", doi="10.1/x"),
        ]
        _, report = find_duplicates(articles)
        self.assertEqual(report.n_duplicates(), 1)
        merged = pd.DataFrame(
            {
                "zotero_key": ["K1"],
                "decision_A": ["include"],
                "decision_B": ["include"],
                "status": ["agree"],
            }
        )
        flow = prisma_from_merge(merged, report=report)
        self.assertEqual(flow.identified - flow.duplicates_removed, flow.screened)
        self.assertEqual(flow.warnings, ())


# ---------------------------------------------------------------------------
# R6-F9（r6 评审反馈 2 修正）：screened 口径与任务页 /prisma-metrics 的对齐边界
# ---------------------------------------------------------------------------

class ScreenedCaliberAlignmentTests(unittest.TestCase):
    """reviewer 口径对齐钉住；team 口径未对齐（如实限定）。

    - reviewer 模式：任务页 records_screened = len(db_keys & set(first))，
      first 为决策表内记录（只含已保存的实际决策）——与本模块「至少一名
      筛选员有实际决策」同口径，本类钉住；
    - team 模式未对齐：custom_backend/prisma_task_metrics.py 的 outcomes
      只返回 agreed/adjudicated（已决）记录，pending/部分已筛行不计入，
      比本模块口径更严；对齐需修改该文件（不在本泳道允许改动清单），
      Task 端到端未运行——边界声明见 coscreen/stats/prisma.py 模块 docstring。
    """

    def test_screened_matches_saved_decision_union(self):
        # 两名筛选员的决策表（任务页 first 的数据形状：键 -> 已保存决策）；
        # 导出 CSV 额外含一条双方都未筛的空行 k9（不在任何决策表内）。
        df_a = _decisions_df([("k1", "include"), ("k2", "exclude"), ("k9", "")])
        df_b = _decisions_df([("k1", "include"), ("k3", "include"), ("k9", "")])
        merged = merge_decisions(df_a, df_b)
        flow = prisma_from_merge(merged)
        saved_decision_keys = {"k1", "k2", "k3"}  # 两名筛选员决策表的键并集
        self.assertEqual(flow.screened, len(saved_decision_keys))
        self.assertEqual(
            merged.loc[merged["zotero_key"] == "k9", "status"].iloc[0], "unscreened"
        )


# ---------------------------------------------------------------------------
# r6 评审反馈 4：PrismaFlow.warnings 的呈现边界（JSON-only）
# ---------------------------------------------------------------------------

class WarningSurfaceBoundaryTests(unittest.TestCase):
    def test_reconciliation_warnings_are_json_only(self):
        # 勾稽提示仅随 to_dict（CLI stats.json / Python 调用方）导出；
        # prisma_dot 的 DOT 图不呈现警示行（保持标准流程图形状），
        # CLI stdout 亦不打印（coscreen/cli.py 不在本泳道允许改动清单）。
        class _Report:
            total = 10
            unique = 8

            def n_duplicates(self) -> int:
                return self.total - self.unique

        merged = merge_decisions(
            _decisions_df([(f"k{i}", "include") for i in range(5)]),
            _decisions_df([(f"k{i}", "include") for i in range(5)]),
        )
        flow = prisma_from_merge(merged, report=_Report())
        self.assertTrue(flow.warnings)
        self.assertTrue(flow.to_dict()["warnings"])
        dot = prisma_dot(flow)
        self.assertNotIn("筛选尚未完成", dot)
        self.assertNotIn("≠ screened", dot)


if __name__ == "__main__":
    unittest.main()
