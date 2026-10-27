"""PRISMA 流程数据：从合并结果推导流程计数并生成 Graphviz DOT（SPEC §7）。

prisma_from_merge 依赖合并表（merge_decisions 输出）统计筛选/纳入/排除数，
去重报告通过鸭子类型读取（不 import coscreen.dedup，避免运行时耦合）；
prisma_dot 输出带中文标签的 DOT 字符串，可直接用于 st.graphviz_chart。

计数口径（U6-P3/P7 修复）：
- screened 只计「至少一名筛选员给出实际决策（非空串）」的记录：双方决策
  均为空的行（merge_decisions 现输出 status="unscreened"）与多人路径
  n_screened=0 的 incomplete 行不计入，PRISMA 分母不再虚高；
- included/excluded 未给 resolved 时仅统计双方一致的实际决策
  （include/exclude）；一致 maybe 等其他决策属未决，与 conflict/单边行
  一样只体现在 screened − included − excluded 的「未决」差值中
  （prisma_dot 渲染为未决节点，U6-P5/R6-F7）；
- resolved / prisma_two_stage 的复筛决策只统计落在分母键域内的仲裁键：
  键域外（幽灵/未筛）键与重复键被忽略并记入 PrismaFlow.warnings
  （U6-P11/R6-F5），保证 included + excluded <= screened 恒成立。

与任务页 /prisma-metrics 的口径边界（R6-F9，r6 评审反馈 2 修正）：
- reviewer 模式与 CLI/手动 merge 一致——records_screened = len(db_keys &
  set(first))，first 为决策表内记录（只含已保存的实际决策），与本模块
  「至少一名筛选员有实际决策」同口径（见 test_prisma_stats_u6_regressions
  的 ScreenedCaliberAlignmentTests）；
- team 模式**未对齐**：custom_backend/prisma_task_metrics.py 的 outcomes
  在 team 模式只返回 agreed/adjudicated（已决）记录，pending/部分已筛
  行不计入 records_screened——比本模块口径更严。对齐需修改该文件，
  其不在本泳道允许改动清单（custom_backend/prisma_task_metrics.py），
  Task 端到端亦未运行；如需统一以最严（已决）口径或放宽 team 口径，
  须由后续轮次在产品侧裁决。

PrismaFlow.warnings 的呈现边界（r6 评审反馈 4）：勾稽提示仅随 to_dict
（CLI stats.json 与 Python 调用方）导出；prisma_dot 输出的 DOT 图与 CLI
stdout **不呈现**这些提示（保持标准流程图形状，非标准警示行不入图）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
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
    """PRISMA 流程各节点计数。included + excluded <= screened 恒成立。

    warnings 为勾稽提示（U6-P11/R6-F5）：如 identified − duplicates_removed
    ≠ screened 的未完成筛选提示、resolved/stage2 键域外键忽略提示；
    不改变计数本身，随 to_dict 一并导出供 CLI stats.json 呈现。
    """

    identified: int
    duplicates_removed: int
    screened: int
    excluded: int
    included: int
    warnings: tuple[str, ...] = field(default=())

    def to_dict(self) -> dict[str, Any]:
        """按固定键顺序导出（同一输入多次调用结果完全一致）。"""
        return {
            "identified": self.identified,
            "duplicates_removed": self.duplicates_removed,
            "screened": self.screened,
            "excluded": self.excluded,
            "included": self.included,
            "warnings": list(self.warnings),
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


def _decision_series(merged: pd.DataFrame, col: str) -> pd.Series:
    """决策列规整为小写、无首尾空白的字符串；缺列时回退为全空串。"""
    if col in merged.columns:
        return merged[col].fillna("").astype(str).str.strip().str.lower()
    return pd.Series([""] * len(merged), index=merged.index, dtype=object)


def _screened_mask(merged: pd.DataFrame, columns: tuple[str, ...]) -> pd.Series:
    """至少一名筛选员给出实际决策（非空串）的行掩码（U6-P3/P7）。

    按决策列实际值判断，兼容旧版合并表（双空决策曾被标 status="agree"
    的行不再计入 screened）；两列均缺时回退按 status 排除 unscreened。
    """
    present = [col for col in columns if col in merged.columns]
    if not present:
        status = merged["status"].astype(str).str.strip().str.lower()
        return status.isin(("agree", "conflict", "a_only", "b_only"))
    mask = pd.Series(False, index=merged.index)
    for col in present:
        mask = mask | (_decision_series(merged, col) != "")
    return mask


def _agree_counts_two(merged: pd.DataFrame) -> tuple[int, int]:
    """双筛选员一致决策计数：(included, excluded)。空决策不算一致（U6-P7）。"""
    a = _decision_series(merged, "decision_A")
    b = _decision_series(merged, "decision_B")
    if "decision_B" in merged.columns:
        included = int(((a == "include") & (b == "include")).sum())
        excluded = int(((a == "exclude") & (b == "exclude")).sum())
    else:  # 兼容仅 decision_A + status 的最小表
        agree = merged["status"].astype(str).str.strip().str.lower() == "agree"
        included = int((agree & (a == "include")).sum())
        excluded = int((agree & (a == "exclude")).sum())
    return included, excluded


def _agree_decision_keys(merged: pd.DataFrame, decision: str) -> set[str]:
    """双方一致为 decision 的 zotero_key 集合（空决策不算一致）。"""
    if "zotero_key" not in merged.columns:
        raise ValueError("合并表缺少 zotero_key 列（需要 merge_decisions 的输出）")
    a = _decision_series(merged, "decision_A")
    b = _decision_series(merged, "decision_B")
    keys = merged["zotero_key"].fillna("").astype(str)
    if "decision_B" in merged.columns:
        mask = (a == decision) & (b == decision)
    else:  # 兼容仅 decision_A + status 的最小表
        agree = merged["status"].astype(str).str.strip().str.lower() == "agree"
        mask = agree & (a == decision)
    return set(keys[mask])


def _resolved_decisions(
    resolved: pd.DataFrame, allowed_keys: set[str]
) -> tuple[dict[str, str], list[str]]:
    """仲裁表清洗（U6-P11/R6-F5）。

    按 zotero_key 去重（重复仲裁键保留首条，只计一次），只保留落在
    allowed_keys（分母键域）内的键；键域外（幽灵/未筛）键忽略并如实
    返回清单。返回 (键 -> 终决策小写, 被忽略键按首现顺序)。
    """
    if not {"zotero_key", "final_decision"} <= set(resolved.columns):
        raise ValueError("resolved 需要 zotero_key/final_decision 两列（仲裁后的终决策表）")
    decisions: dict[str, str] = {}
    ignored: list[str] = []
    keys = resolved["zotero_key"].fillna("").astype(str)
    values = resolved["final_decision"].fillna("").astype(str).str.strip().str.lower()
    for key, value in zip(keys, values):
        if key in allowed_keys:
            if key not in decisions:  # 重复仲裁键去重，避免同键双计
                decisions[key] = value
        elif key not in ignored:
            ignored.append(key)
    return decisions, ignored


def _resolved_warning(ignored: list[str], source: str) -> str:
    """键域外忽略提示文案（键名最多列 5 个，保持确定性）。"""
    shown = ", ".join(ignored[:5]) + ("…" if len(ignored) > 5 else "")
    return f"{source} 含 {len(ignored)} 个键域外（幽灵/未筛）键，已忽略不计入: {shown}"


def prisma_from_merge(
    merged: pd.DataFrame,
    report: "DedupReport | None" = None,
    resolved: pd.DataFrame | None = None,
) -> PrismaFlow:
    """由合并结果构造 PRISMA 流程计数。

    screened：至少被一名筛选员给出实际决策（非空串）的文献数；双方决策
    均为空的行（unscreened）不计入——即使旧表仍标 status="agree"，也按
    决策列实际值判断（U6-P3/P7/R6-F9）。
    included / excluded：
      - 给出 resolved（仲裁后的 zotero_key/final_decision 表）时按其统计；
        重复键去重、只计已筛键域内的键（键域外忽略并记入 warnings，
        U6-P11/R6-F5）；
      - 否则仅统计双方一致的实际决策 include/exclude；一致 maybe 等其他
        决策未决不计入（R6-F7 口径，DOT 以「未决」节点呈现差值）。
    给出去重报告且 identified − duplicates_removed ≠ screened 时记入
    warnings（筛选未完成或口径不一致，U6-P11 report 侧勾稽提示）。
    """
    if "status" not in merged.columns:
        raise ValueError("prisma_from_merge 需要 merge_decisions 的输出（缺少 status 列）")
    mask = _screened_mask(merged, ("decision_A", "decision_B"))
    screened = int(mask.sum())
    warns: list[str] = []

    if resolved is not None:
        if "zotero_key" not in merged.columns:
            raise ValueError("给出 resolved 时 merged 需含 zotero_key 列")
        screened_keys = set(merged.loc[mask, "zotero_key"].fillna("").astype(str))
        decisions, ignored = _resolved_decisions(resolved, screened_keys)
        included = sum(1 for value in decisions.values() if value == "include")
        excluded = sum(1 for value in decisions.values() if value == "exclude")
        if ignored:
            warns.append(_resolved_warning(ignored, "resolved"))
    else:
        included, excluded = _agree_counts_two(merged)

    duplicates_removed = _count_duplicates(report)
    identified = _count_identified(report, duplicates_removed, screened)
    if report is not None and identified - duplicates_removed != screened:
        warns.append(
            f"identified({identified}) − duplicates_removed({duplicates_removed}) "
            f"≠ screened({screened})：筛选尚未完成，或去重报告与决策表口径不一致"
            "（U6-P11 勾稽提示）"
        )
    return PrismaFlow(
        identified=identified,
        duplicates_removed=duplicates_removed,
        screened=screened,
        excluded=excluded,
        included=included,
        warnings=tuple(warns),
    )


def prisma_from_merge_multi(
    merged_multi: pd.DataFrame,
    resolved: pd.DataFrame | None = None,
) -> PrismaFlow:
    """由多人合并结果（merge_decisions_multi 输出）构造 PRISMA 流程计数。

    screened = 至少一名筛选员给出实际决策的唯一 zotero_key 数
    （n_screened=0 的 incomplete 行不计入，U6-P3/R6-F9）；included/excluded：
      - 给出 resolved（仲裁后的 zotero_key/final_decision 表）时仅按其统计，
        final_decision 大小写/首尾空白不敏感；重复键去重、只计已筛键域内
        的键（键域外忽略并记入 warnings，U6-P11/R6-F5）；
      - 否则仅统计 agree 条目的共同决策：include -> included、exclude ->
        excluded、maybe 及其他不计入；conflict/incomplete 条目未决，不计入。
    identified/duplicates_removed 以 report=None 的兜底口径：去重数 0、
    identified = screened。
    """
    if "status" not in merged_multi.columns:
        raise ValueError("prisma_from_merge_multi 需要 merge_decisions_multi 的输出（缺少 status 列）")
    names = [col[len("decision_"):] for col in merged_multi.columns if col.startswith("decision_")]
    if not names:
        raise ValueError("prisma_from_merge_multi 需要 merge_decisions_multi 的输出（缺少 decision_<name> 列）")

    mask = _screened_mask(merged_multi, tuple(f"decision_{name}" for name in names))
    keys = merged_multi["zotero_key"].fillna("").astype(str)
    screened = int(keys[mask].nunique())
    warns: list[str] = []

    if resolved is not None:
        screened_keys = set(keys[mask])
        decisions, ignored = _resolved_decisions(resolved, screened_keys)
        included = sum(1 for value in decisions.values() if value == "include")
        excluded = sum(1 for value in decisions.values() if value == "exclude")
        if ignored:
            warns.append(_resolved_warning(ignored, "resolved"))
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
        warnings=tuple(warns),
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
    - 复筛决策只统计落在初筛纳入键域（stage1 双方一致 include 的键）内的
      条目：键域外与重复键忽略并记入 warnings（R6-F5），保证
      included + excluded <= screened 恒成立（复筛在初筛纳入子集上进行，
      其排除对象已被初筛排除计数覆盖，故不相加两次）。
    """
    stage1 = prisma_from_merge(stage1_merged)
    include_keys = _agree_decision_keys(stage1_merged, "include")
    if "zotero_key" not in stage2_merged.columns:
        raise ValueError("stage2_merged 需含 zotero_key 列（需要 merge_decisions 的输出）")
    warns: list[str] = list(stage1.warnings)

    keys2 = stage2_merged["zotero_key"].fillna("").astype(str)
    in_domain = keys2.isin(include_keys)
    if resolved2 is not None:
        decisions, ignored = _resolved_decisions(resolved2, include_keys)
        included = sum(1 for value in decisions.values() if value == "include")
        excluded2 = sum(1 for value in decisions.values() if value == "exclude")
        if ignored:
            warns.append(_resolved_warning(ignored, "resolved2"))
    else:
        a2 = _decision_series(stage2_merged, "decision_A")
        b2 = _decision_series(stage2_merged, "decision_B")
        if "decision_B" in stage2_merged.columns:
            inc_mask = (a2 == "include") & (b2 == "include")
            exc_mask = (a2 == "exclude") & (b2 == "exclude")
        else:  # 兼容仅 decision_A + status 的最小表
            agree2 = stage2_merged["status"].astype(str).str.strip().str.lower() == "agree"
            inc_mask = agree2 & (a2 == "include")
            exc_mask = agree2 & (a2 == "exclude")
        included = int((inc_mask & in_domain).sum())
        excluded2 = int((exc_mask & in_domain).sum())
        n_out = int(((inc_mask | exc_mask) & ~in_domain).sum())
        if n_out:
            warns.append(
                f"stage2 有 {n_out} 条决策落在初筛纳入键域外，已忽略不计入"
                "（复筛应在初筛纳入子集上进行，R6-F5）"
            )

    return PrismaFlow(
        identified=stage1.identified,
        duplicates_removed=stage1.duplicates_removed,
        screened=stage1.screened,
        excluded=stage1.excluded + excluded2,
        included=included,
        warnings=tuple(warns),
    )


def prisma_dot(flow: PrismaFlow) -> str:
    """生成 PRISMA 流程图的 Graphviz DOT 字符串（中文标签，含计数）。

    included + excluded < screened 时追加「未决」节点（U6-P5/R6-F7）：
    conflict/单边与一致 maybe 等未决记录不再从图中静默消失，分支之和
    与 screened 保持勾稽。
    """
    pending = flow.screened - flow.included - flow.excluded
    node_lines = [
        f'    identified [label="检索识别\\n{flow.identified} 篇"];',
        f'    screened [label="去重后进入筛选\\n{flow.screened} 篇"];',
        f'    excluded [label="排除\\n{flow.excluded} 篇"];',
        f'    included [label="最终纳入\\n{flow.included} 篇"];',
    ]
    edge_lines = [
        f'    identified -> screened [label="去除重复 {flow.duplicates_removed} 篇"];',
        "    screened -> excluded;",
        "    screened -> included;",
    ]
    if pending > 0:
        node_lines.append(f'    pending [label="未决（分歧/待仲裁）\\n{pending} 篇"];')
        edge_lines.append("    screened -> pending;")
    lines = [
        "digraph PRISMA {",
        "    rankdir=TB;",
        '    node [shape=box, style="rounded,filled", fillcolor="#E3F2F0", color="#2F6F6A"];',
        *node_lines,
        *edge_lines,
        "}",
    ]
    return "\n".join(lines)
