"""PRISMA 流程数据：从合并结果推导流程计数并生成 Graphviz DOT（SPEC §7）。

prisma_from_merge 依赖合并表（merge_decisions 输出）统计筛选/纳入/排除数，
去重报告通过鸭子类型读取（不 import coscreen.dedup，避免运行时耦合）；
prisma_dot 输出带中文标签的 DOT 字符串，可直接用于 st.graphviz_chart。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import pandas as pd

if TYPE_CHECKING:  # 仅类型检查使用，运行时不导入他方模块
    from coscreen.dedup import DedupReport

__all__ = [
    "PrismaFlow",
    "prisma_dot",
    "prisma_from_merge",
    "prisma_from_merge_multi",
    "prisma_two_stage",
]


@dataclass
class PrismaFlow:
    """PRISMA 流程各节点计数。included + excluded <= screened 恒成立。"""

    identified: int
    duplicates_removed: int
    screened: int
    excluded: int
    included: int

    def to_dict(self) -> dict[str, int]:
        """按固定键顺序导出（同一输入多次调用结果完全一致）。"""
        return {
            "identified": self.identified,
            "duplicates_removed": self.duplicates_removed,
            "screened": self.screened,
            "excluded": self.excluded,
            "included": self.included,
        }


def _count_duplicates(report: Any | None) -> int:
    """鸭子类型读取去重数：n_duplicates 可为方法（DedupReport）或整数属性。"""
    if report is None:
        return 0
    value = getattr(report, "n_duplicates", 0)
    if callable(value):
        value = value()
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _count_identified(report: Any | None, duplicates_removed: int, screened: int) -> int:
    """优先取 report.total，其次 unique + 去重数，均缺失时以 screened 兜底。"""
    if report is None:
        return screened
    total = getattr(report, "total", None)
    if total is not None:
        return int(total)
    unique = getattr(report, "unique", None)
    if unique is not None:
        return int(unique) + duplicates_removed
    return screened + duplicates_removed


def prisma_from_merge(
    merged: pd.DataFrame,
    report: "DedupReport | None" = None,
    resolved: pd.DataFrame | None = None,
) -> PrismaFlow:
    """由合并结果构造 PRISMA 流程计数。

    screened：至少被一名筛选员筛选的文献数（agree+conflict+A_only+B_only）。
    included / excluded：
      - 给出 resolved（仲裁后的 zotero_key/final_decision 表）时按其统计；
      - 否则仅统计 agree 条目的共同决策，conflict/单边条目未决，不计入。
    """
    status = merged["status"]
    n_agree = int((status == "agree").sum())
    n_conflict = int((status == "conflict").sum())
    n_a_only = int((status == "A_only").sum())
    n_b_only = int((status == "B_only").sum())
    screened = n_agree + n_conflict + n_a_only + n_b_only

    if resolved is not None:
        final = resolved["final_decision"].astype(str).str.strip().str.lower()
        included = int((final == "include").sum())
        excluded = int((final == "exclude").sum())
    else:
        agree = merged[status == "agree"]
        included = int((agree["decision_A"] == "include").sum())
        excluded = int((agree["decision_A"] == "exclude").sum())

    duplicates_removed = _count_duplicates(report)
    identified = _count_identified(report, duplicates_removed, screened)
    return PrismaFlow(
        identified=identified,
        duplicates_removed=duplicates_removed,
        screened=screened,
        excluded=excluded,
        included=included,
    )


def prisma_from_merge_multi(
    merged_multi: pd.DataFrame,
    resolved: pd.DataFrame | None = None,
) -> PrismaFlow:
    """由多人合并结果（merge_decisions_multi 输出）构造 PRISMA 流程计数。

    screened = 唯一 zotero_key 数；included/excluded：
      - 给出 resolved（仲裁后的 zotero_key/final_decision 表）时仅按其统计，
        final_decision 大小写/首尾空白不敏感（与 prisma_from_merge 相同口径）；
      - 否则仅统计 agree 条目的共同决策：include -> included、exclude -> excluded、
        maybe 及其他不计入；conflict/incomplete 条目未决，不计入。
    identified/duplicates_removed 以 report=None 的兜底口径：去重数 0、
    identified = screened。
    """
    if "status" not in merged_multi.columns:
        raise ValueError("prisma_from_merge_multi 需要 merge_decisions_multi 的输出（缺少 status 列）")
    names = [col[len("decision_"):] for col in merged_multi.columns if col.startswith("decision_")]
    if not names:
        raise ValueError("prisma_from_merge_multi 需要 merge_decisions_multi 的输出（缺少 decision_<name> 列）")

    screened = int(merged_multi["zotero_key"].nunique())
    if resolved is not None:
        final = resolved["final_decision"].astype(str).str.strip().str.lower()
        included = int((final == "include").sum())
        excluded = int((final == "exclude").sum())
    else:
        agree = merged_multi[merged_multi["status"] == "agree"]
        # agree 行所有已筛决策全同，取按列顺序首个非空值即为共同决策
        shared = pd.Series([""] * len(agree), index=agree.index, dtype=object)
        for name in names:
            col = agree[f"decision_{name}"].fillna("")
            shared = shared.where(shared != "", col)
        included = int((shared == "include").sum())
        excluded = int((shared == "exclude").sum())

    return PrismaFlow(
        identified=screened,
        duplicates_removed=0,
        screened=screened,
        excluded=excluded,
        included=included,
    )


def prisma_two_stage(
    stage1_merged: pd.DataFrame,
    stage2_merged: pd.DataFrame,
    resolved2: pd.DataFrame | None = None,
) -> PrismaFlow:
    """两阶段（初筛 -> 全文复筛）PRISMA 流程计数（SPEC §14）。

    两个入参均为 :func:`prisma_from_merge` 接受的合并表（merge_decisions 输出，
    双筛选员 decisions / decisions2 CSV 合并结果）：

    - identified / duplicates_removed / screened：沿用初筛合并表口径
      （无 DedupReport 时 identified = screened、duplicates_removed = 0）；
    - excluded：初筛排除数 + 复筛排除数（两个阶段的排除累计）；
    - included：复筛合并表的最终纳入数——默认只计 stage2 中 agree 且共同
      decision == "include" 的条目；给出 resolved2（zotero_key/final_decision
      仲裁表）时按其统计（口径与 prisma_from_merge 的 resolved 相同）。

    恒满足 included + excluded <= screened + 复筛排除（复筛在初筛纳入子集上
    进行，其排除对象已被初筛排除计数覆盖，故不相加两次）。
    """
    stage1 = prisma_from_merge(stage1_merged)
    stage2 = prisma_from_merge(stage2_merged, resolved=resolved2)
    return PrismaFlow(
        identified=stage1.identified,
        duplicates_removed=stage1.duplicates_removed,
        screened=stage1.screened,
        excluded=stage1.excluded + stage2.excluded,
        included=stage2.included,
    )


def prisma_dot(flow: PrismaFlow) -> str:
    """生成 PRISMA 流程图的 Graphviz DOT 字符串（中文标签，含计数）。"""
    lines = [
        "digraph PRISMA {",
        "    rankdir=TB;",
        '    node [shape=box, style="rounded,filled", fillcolor="#E3F2F0", color="#2F6F6A"];',
        f'    identified [label="检索识别\\n{flow.identified} 篇"];',
        f'    screened [label="去重后进入筛选\\n{flow.screened} 篇"];',
        f'    excluded [label="排除\\n{flow.excluded} 篇"];',
        f'    included [label="最终纳入\\n{flow.included} 篇"];',
        f'    identified -> screened [label="去除重复 {flow.duplicates_removed} 篇"];',
        "    screened -> excluded;",
        "    screened -> included;",
        "}",
    ]
    return "\n".join(lines)
