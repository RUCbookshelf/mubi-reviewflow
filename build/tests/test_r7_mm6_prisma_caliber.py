"""r7-MM6 修复泳道回归钉：MM6-02 两条 CLI 路径 PRISMA 口径一致 + DOT 呈现告警。

外部复审（minimax_findings_verification_6.md MM6-02，deepseek 复现 confirmed）：
同一份双筛选员数据（2 键：K1 双方 include、K2 双方空决策），
- ``python -m coscreen merge --a a.csv --b b.csv`` → identified=2（决策表
  行数兜底）、unscreened=1；
- ``python -m coscreen merge --files a.csv b.csv`` → identified=1（=screened）、
  unscreened=0（PrismaFlow 默认值，multi 路径从不设置）；
- 两条官方 CLI 路径对同一数据给出不同 PRISMA 数字；prisma_dot 不渲染
  warnings，agree 路径画出「识别 2 → 筛选 1（去除重复 0）」的自相矛盾
  数字且图上无任何解释。

钉子（破坏即红）：
- 复审 probe_prisma 场景原样回放（真实 CLI 两条路径）：stats.json 的
  prisma 计数与 warnings 两路径完全一致；恒等式「决策表唯一键数 =
  screened + unscreened = identified(report=None)」两路径均成立
  （修前 identified 2 vs 1、unscreened 1 vs 0 → 红）；
- prisma_dot 渲染 warnings（note 节点，含勾稽告警文案）——修前 DOT 无
  任何告警 token（两段 DOT 均不含 warning/≠/尚未完成）→ 红；
- 恒绿守护：全筛自洽输入（无未筛键）两路径均不产生勾稽告警、DOT 无
  告警节点——无条件勾稽不得对自洽输入制造噪音（MM-08 对照口径延续）。
"""

from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pandas as pd

from coscreen.stats.merge import merge_decisions, merge_decisions_multi
from coscreen.stats.prisma import prisma_dot, prisma_from_merge, prisma_from_merge_multi


def _decisions_df(pairs: list[tuple[str, str]]) -> pd.DataFrame:
    return pd.DataFrame({"zotero_key": [k for k, _ in pairs],
                         "decision": [d for _, d in pairs]})


def _probe_dfs() -> tuple[pd.DataFrame, pd.DataFrame]:
    """复审 probe_prisma 原样输入：K1 双方 include、K2 双方空决策。"""
    df = _decisions_df([("K1", "include"), ("K2", "")])
    return df, df.copy()


def _run_cli(args: list[str]) -> int:
    """同进程直调 CLI 主入口（与 test_dedup_key_len_g02 同一手法）。"""
    from coscreen import cli as cli_module

    buf = io.StringIO()
    with redirect_stdout(buf):
        try:
            code = cli_module.main(list(args))
        except SystemExit as exc:
            code = int(exc.code or 0)
    return int(code)


# ---------------------------------------------------------------------------
# 单元钉子：两条路径对 probe 场景的数字一致、恒等式成立、DOT 含告警
# ---------------------------------------------------------------------------
class TwoPathCaliberConsistencyTests(unittest.TestCase):
    def test_probe_scenario_two_paths_identical_and_identity_holds(self):
        df_a, df_b = _probe_dfs()
        agree = prisma_from_merge(merge_decisions(df_a, df_b))
        multi = prisma_from_merge_multi(merge_decisions_multi({"R1": df_a, "R2": df_b}))
        # 修前：agree identified=2/unscreened=1 vs multi identified=1/unscreened=0
        self.assertEqual(
            agree.to_dict(), multi.to_dict(),
            f"两条路径 PRISMA 数字不一致: agree={agree.to_dict()} multi={multi.to_dict()}",
        )
        # 复审 probe 实测数字（键级口径）：2 键、1 已筛、1 未筛
        for flow in (agree, multi):
            self.assertEqual(flow.identified, 2)
            self.assertEqual(flow.screened, 1)
            self.assertEqual(flow.unscreened, 1)  # 修前 multi 恒 0 → 红
            self.assertEqual(flow.included, 1)
            self.assertEqual(flow.excluded, 0)
            # 恒等式：决策表唯一键数 = screened + unscreened = identified
            self.assertEqual(flow.screened + flow.unscreened, 2)
            self.assertEqual(flow.identified, flow.screened + flow.unscreened)
            # 含未筛键 → 两路径都必须有勾稽告警（修前 multi warnings=() → 红）
            self.assertTrue(flow.warnings, f"expected reconciliation warning: {flow!r}")

    def test_dot_renders_warnings_for_both_paths(self):
        # 修前两段 DOT 均不含 warning/告警/≠/尚未完成 任一 token（复审实测）→ 红
        df_a, df_b = _probe_dfs()
        flows = {
            "agree": prisma_from_merge(merge_decisions(df_a, df_b)),
            "multi": prisma_from_merge_multi(merge_decisions_multi({"R1": df_a, "R2": df_b})),
        }
        for name, flow in flows.items():
            dot = prisma_dot(flow)
            self.assertIn("warnings", dot, f"[{name}] DOT 缺 warnings 节点")
            self.assertIn("勾稽告警", dot, f"[{name}] DOT 缺告警文案")
            self.assertIn("≠ screened", dot, f"[{name}] DOT 缺勾稽告警正文")

    def test_fully_screened_input_no_warning_noise(self):
        # 恒绿守护：自洽输入（全筛、无未筛键、无重复键行）两路径都不告警，
        # DOT 不出现告警节点——键级口径下 identified == screened 恒勾稽。
        df_a, df_b = _decisions_df([("K1", "include"), ("K2", "exclude")]), \
            _decisions_df([("K1", "include"), ("K2", "exclude")])
        agree = prisma_from_merge(merge_decisions(df_a, df_b))
        multi = prisma_from_merge_multi(merge_decisions_multi({"R1": df_a, "R2": df_b}))
        for flow in (agree, multi):
            self.assertEqual(flow.warnings, ())
            self.assertEqual(flow.unscreened, 0)
            self.assertNotIn("warnings", prisma_dot(flow))


# ---------------------------------------------------------------------------
# 端到端钉子：真实 CLI 两条路径（复审 probe_prisma 原样回放）
# ---------------------------------------------------------------------------
class CliTwoPathProbeTests(unittest.TestCase):
    def test_probe_prisma_cli_paths_agree(self):
        with tempfile.TemporaryDirectory(prefix="rf_mm6_prisma_") as tmp:
            tmp = Path(tmp)
            df_a, df_b = _probe_dfs()
            a_csv, b_csv = tmp / "a.csv", tmp / "b.csv"
            df_a.to_csv(a_csv, index=False, encoding="utf-8-sig")
            df_b.to_csv(b_csv, index=False, encoding="utf-8-sig")
            agree_dir, multi_dir = tmp / "agree", tmp / "multi"
            self.assertEqual(_run_cli(["merge", "--a", str(a_csv), "--b", str(b_csv),
                                       "--outdir", str(agree_dir)]), 0)
            self.assertEqual(_run_cli(["merge", "--files", str(a_csv), str(b_csv),
                                       "--outdir", str(multi_dir)]), 0)
            agree_stats = json.loads((agree_dir / "stats.json").read_text(encoding="utf-8"))
            multi_stats = json.loads((multi_dir / "stats.json").read_text(encoding="utf-8"))
            # 修前：identified 2 vs 1、unscreened 1 vs 0 → 红
            self.assertEqual(agree_stats["prisma"], multi_stats["prisma"],
                             f"CLI 两路径 stats.json prisma 不一致: "
                             f"agree={agree_stats['prisma']} multi={multi_stats['prisma']}")
            prisma = agree_stats["prisma"]
            self.assertEqual((prisma["identified"], prisma["screened"], prisma["unscreened"]),
                             (2, 1, 1))
            self.assertEqual(prisma["identified"], prisma["screened"] + prisma["unscreened"])
            self.assertTrue(prisma["warnings"])
            # DOT 含告警（修前两段 DOT 均无任何告警 token → 红）
            for outdir in (agree_dir, multi_dir):
                dot = (outdir / "prisma.dot").read_text(encoding="utf-8")
                self.assertIn("勾稽告警", dot, f"{outdir} 的 prisma.dot 缺告警")
                self.assertIn("≠ screened", dot)


if __name__ == "__main__":
    unittest.main()
