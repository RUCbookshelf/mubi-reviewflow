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
  （U6-P11/R6-F5），保证 included + excluded <= screened 恒成立；
- identified − duplicates_removed ≠ screened 的勾稽告警无条件执行
  （MM-08，修复前被 report is not None 门控倒置）；unscreened 按键级
  口径计（唯一键集合 − screened 键集合：同一键既有已筛行又有未筛行时
  按已筛计，不双计），勾稽不变式为「决策表唯一键数 =
  screened + unscreened」（MM-10，修复前 unscreened 按行数计）；
- identified 在 report=None 的兜底口径下同样按**唯一键数**计
  （MM6-02，修复前 agree 路径按决策表行数、multi 路径恒 =screened 且
  从不设置 unscreened——同一份数据两条 CLI 路径得出不同 PRISMA 数字）。
  行数与唯一键数的取舍：行数（重复键折叠行的物理行数）不再单独导出——
  该差值已由 MM-08 勾稽告警文案解释，且 agree/multi/two_stage 的
  report=None 分支上「identified = screened + unscreened = 唯一键数」
  恒成立（two_stage 经 stage1 透传；report 非 None 时 identified 取去重
  报告口径，不适用该恒等式，MM-08 告警负责勾稽）。

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

PrismaFlow.warnings 的呈现边界（r6 评审反馈 4 → MM6-02 修订）：勾稽提示
随 to_dict（CLI stats.json 与 Python 调用方）导出，**并经 prisma_dot 渲染
为「勾稽告警」note 节点**（MM6-02：修复前 DOT 只画四核心节点 + 未决，
「识别 2 → 筛选 1（去除重复 0）」的自相矛盾数字在图上无任何解释）；
CLI stdout 仍不打印这些提示（保持 stdout 输出契约不变）。
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
    """PRISMA 流程各节点计数。included + excluded <= screened 恒成立
    （r6 #49：include/exclude 跨桶互斥——同一键分落两桶的重复键行按首
    现行归属只计一次并告警；正常入口 merge_decisions 拒绝单侧重复键，
    该形态只来自手工/历史合并表）。

    warnings 为勾稽提示（U6-P11/R6-F5）：如 identified − duplicates_removed
    ≠ screened 的未完成筛选提示、resolved/stage2 键域外键忽略提示、
    跨桶重复键折叠提示（r6 #49）；不改变计数本身，随 to_dict 一并导出
    供 CLI stats.json 呈现。
    """

    identified: int
    duplicates_removed: int
    screened: int
    excluded: int
    included: int
    warnings: tuple[str, ...] = field(default=())
    #: 无任何已筛行的唯一键数（键级口径＝唯一键集合 − screened 键集合，
    #: MM-10：同一键既有已筛行又有未筛行时按已筛计，不双计）——复审勾稽
    #: 桶，与 screened 同按唯一键计：决策表唯一键数 = screened +
    #: unscreened 恒成立；行数口径在重复键行下更大，差值由 identified
    #: 勾稽告警解释，导出供对账
    unscreened: int = 0

    def to_dict(self) -> dict[str, Any]:
        """按固定键顺序导出（同一输入多次调用结果完全一致）。"""
        return {
            "identified": self.identified,
            "duplicates_removed": self.duplicates_removed,
            "screened": self.screened,
            "excluded": self.excluded,
            "included": self.included,
            "unscreened": self.unscreened,
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


def _count_identified(
    report: Any | None, duplicates_removed: int, screened: int, decision_table_total: int | None = None,
) -> int:
    """优先取 report.total，其次 unique + 去重数；report=None 时以**决策表
    唯一键数**兜底（MM6-02：检索识别数=决策表唯一键数——与 screened/
    unscreened 的键级口径对齐，「唯一键数 = screened + unscreened =
    identified」恒成立；此前按决策表**行数**兜底，重复键行输入下与 multi
    路径（恒 =screened）两条 CLI 路径对同一数据得出不同数字）。
    decision_table_total 由调用方传唯一键数（无 zotero_key 列的最小表回退
    行数）。"""
    if report is None:
        return screened if decision_table_total is None else int(decision_table_total)
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


def _cross_bucket_warning(overlap: list[str], source: str) -> str:
    """r6 #49 跨桶互斥告警文案（键名最多列 5 个，保持确定性）。"""
    shown = ", ".join(overlap[:5]) + ("…" if len(overlap) > 5 else "")
    return (
        f"{source} 有 {len(overlap)} 个键同时落入 include 与 exclude 桶"
        f"（跨桶重复键，按首现行归属只计一次）: {shown}"
    )


def _split_exclusive(
    merged: pd.DataFrame, inc_mask: pd.Series, exc_mask: pd.Series
) -> tuple[set[str], set[str], list[str]]:
    """把 include/exclude 两桶拆成**跨桶互斥**的键集合（r6 #49）。

    逐桶 ``nunique`` 只保证桶内去重：同一键既有一致 include 行又有一致
    exclude 行时会双双计数（r6 实测同键两行 agree include/agree exclude →
    screened=1、included=1、excluded=1，included+excluded=2 > screened=1，
    docstring 不变量被打破且无任何告警）。跨桶重复键按**首现行**归属只
    保留在一侧并返回重复键清单；正常入口 merge_decisions 对单侧重复键抛
    ValueError，该形态只来自手工/历史合并表。

    无 zotero_key 列的最小表无法按键折叠（screened 同为行口径），按原样
    返回行掩码的键集合（空键集合 → 调用方退回行计数）。
    返回 (included_keys, excluded_keys, 跨桶重叠键清单)。
    """
    if "zotero_key" not in merged.columns:
        return set(), set(), []
    keys = merged["zotero_key"].fillna("").astype(str)
    inc_keys = set(keys[inc_mask.fillna(False)])
    exc_keys = set(keys[exc_mask.fillna(False)])
    overlap = sorted(inc_keys & exc_keys)
    if not overlap:
        return inc_keys, exc_keys, []
    overlap_set = set(overlap)
    first_is_include: dict[str, bool] = {}
    inc_filled = inc_mask.fillna(False).to_numpy()
    exc_filled = exc_mask.fillna(False).to_numpy()
    keys_array = keys.to_numpy()
    for pos in range(len(merged)):
        key = keys_array[pos]
        if key in first_is_include or key not in overlap_set:
            continue
        if inc_filled[pos]:
            first_is_include[key] = True
        elif exc_filled[pos]:
            first_is_include[key] = False
    inc_kept = (inc_keys - overlap_set) | {k for k, v in first_is_include.items() if v}
    exc_kept = (exc_keys - overlap_set) | {k for k, v in first_is_include.items() if not v}
    return inc_kept, exc_kept, overlap


def _bucket_counts_exclusive(
    merged: pd.DataFrame, inc_mask: pd.Series, exc_mask: pd.Series, source: str
) -> tuple[int, int, list[str]]:
    """include/exclude 两桶互斥后的计数 (included, excluded, warnings)。"""
    if "zotero_key" not in merged.columns:
        return int(inc_mask.sum()), int(exc_mask.sum()), []
    inc_kept, exc_kept, overlap = _split_exclusive(merged, inc_mask, exc_mask)
    warns = [_cross_bucket_warning(overlap, source)] if overlap else []
    return len(inc_kept), len(exc_kept), warns


def _agree_counts_two(merged: pd.DataFrame) -> tuple[int, int, list[str]]:
    """双筛选员一致决策计数：(included, excluded, warnings)。

    空决策不算一致（U6-P7）。R6-F5（复审）：含 zotero_key 列时按**唯一键**
    计数；r6 #49 进一步保证 include/exclude **跨桶互斥**（同一键分落两桶
    时按首现行归属只计一次并告警）。
    """
    return _bucket_counts_exclusive(merged, *_agree_masks(merged), "合并表")


def _agree_masks(merged: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """双筛选员「一致 include / 一致 exclude」行掩码。"""
    a = _decision_series(merged, "decision_A")
    b = _decision_series(merged, "decision_B")
    if "decision_B" in merged.columns:
        return (a == "include") & (b == "include"), (a == "exclude") & (b == "exclude")
    # 兼容仅 decision_A + status 的最小表
    agree = merged["status"].astype(str).str.strip().str.lower() == "agree"
    return agree & (a == "include"), agree & (a == "exclude")


def _agree_decision_keys(merged: pd.DataFrame, decision: str) -> set[str]:
    """双方一致为 decision、且跨桶互斥折叠后仍归属该侧的 zotero_key 集合。

    two_stage 以本集合为 stage2 键域：若直接用「全部 agree include 键」，
    同键另有 agree exclude 行时会落进 stage2 域内，stage1 与 stage2 各计
    一次（r6 #49 二轮实测 excluded=2 > screened=1）。按与
    :func:`_bucket_counts_exclusive` 相同的首现行折叠口径取键，保证域与
    计数一致、且结果与行序无关的最终归属互补。
    """
    if decision not in ("include", "exclude"):
        raise ValueError(f"unsupported decision: {decision!r}")
    if "zotero_key" not in merged.columns:
        raise ValueError("合并表缺少 zotero_key 列（需要 merge_decisions 的输出）")
    inc_kept, exc_kept, _ = _split_exclusive(merged, *_agree_masks(merged))
    return inc_kept if decision == "include" else exc_kept


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


def _reconciliation_warning(identified: int, duplicates_removed: int, screened: int) -> str:
    """MM-08 勾稽告警文案（agree/multi 两路径共用同一措辞，保证同一份
    数据两条 CLI 路径的 stats.json warnings 完全一致，MM6-02）。"""
    return (
        f"identified({identified}) − duplicates_removed({duplicates_removed}) "
        f"≠ screened({screened})：筛选尚未完成（含未筛行）、决策表存在"
        "重复键行（按唯一键折叠计），或去重报告与决策表口径不一致"
        "（U6-P11 勾稽提示）"
    )


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
    identified − duplicates_removed ≠ screened 时**无条件**记入 warnings
    （MM-08：report=None 的决策表兜底口径同样勾稽；差值可能来自
    未筛行、重复键行折叠或去重报告口径不一致）；identified 与 unscreened
    均按键级口径计（identified=决策表唯一键数、unscreened=唯一键集合 −
    screened 键集合，MM-10/MM6-02），「决策表唯一键数 =
    screened + unscreened = identified(report=None)」恒成立。
    """
    if "status" not in merged.columns:
        raise ValueError("prisma_from_merge 需要 merge_decisions 的输出（缺少 status 列）")
    mask = _screened_mask(merged, ("decision_A", "decision_B"))
    # R6-F5（复审）：screened 同样按唯一键计（与 multi 路径 nunique 口径
    # 一致）——重复 zotero_key 行不放大分母，included+excluded<=screened
    # 的不变量在 agree 路径的任何输入下成立。
    if "zotero_key" in merged.columns:
        all_keys = merged["zotero_key"].fillna("").astype(str)
        screened = int(all_keys[mask.fillna(False)].nunique())
    else:
        all_keys = None
        screened = int(mask.sum())
    warns: list[str] = []

    if resolved is not None:
        if "zotero_key" not in merged.columns:
            raise ValueError("给出 resolved 时 merged 需含 zotero_key 列")
        screened_keys = set(merged.loc[mask, "zotero_key"].fillna("").astype(str))
        # r6 #49（三轮）：resolved 决定归属时，非 resolved 分支的跨桶告警不再
        # 触发——但「同一键既有一致 include 又有一致 exclude 行」这一事实仍
        # 存在（正常入口不可能产生，只来自手工/历史合并表），须照报。
        _, _, cross_overlap = _split_exclusive(merged, *_agree_masks(merged))
        if cross_overlap:
            warns.append(_cross_bucket_warning(cross_overlap, "合并表（resolved 口径）"))
        decisions, ignored = _resolved_decisions(resolved, screened_keys)
        included = sum(1 for value in decisions.values() if value == "include")
        excluded = sum(1 for value in decisions.values() if value == "exclude")
        if ignored:
            warns.append(_resolved_warning(ignored, "resolved"))
    else:
        included, excluded, cross_warns = _agree_counts_two(merged)
        warns.extend(cross_warns)

    duplicates_removed = _count_duplicates(report)
    # MM6-02：report=None 的兜底口径统一为**键级**（决策表唯一键数），
    # 与 multi 路径及 unscreened 的口径对齐——修复前按行数兜底，重复键
    # 行输入下 identified ≠ 唯一键数，且与 multi 路径（恒 =screened）对
    # 同一数据得出不同数字。无 zotero_key 列的最小表回退行数（screened
    # 同为行数口径，行数不变式相应成立）。
    if all_keys is not None:
        decision_table_total = int(all_keys.nunique())
    else:
        decision_table_total = int(len(merged))
    identified = _count_identified(
        report, duplicates_removed, screened, decision_table_total)
    # MM-08（r7-prisma）：勾稽检查无条件执行。修复前被「report is not
    # None」门控倒置——report=None 时 identified 恰恰走决策表兜底
    # （含未筛行/重复键行），是最可能 ≠ screened 的一支，却被静默跳过，
    # 流程图画出「识别 3 → 去重 0 → 筛选 1」的不自洽数字且无任何提示。
    if identified - duplicates_removed != screened:
        warns.append(_reconciliation_warning(identified, duplicates_removed, screened))
    # MM-10（r7-prisma）：unscreened 按「唯一键集合 − screened 键集合」计
    # （键级口径，r7 复审第 1 轮修正：同一键既有已筛行又有未筛行时按已筛
    # 计，不双计——该键所有行均无决策才算未筛），与 screened 的唯一键
    # 口径互补，勾稽不变式「决策表唯一键数 = screened + unscreened」恒
    # 成立；行数与唯一键数的差（重复键折叠行/已筛键的未筛行）由上方
    # identified 勾稽告警解释。无 zotero_key 列的最小表回退行数（兼容，
    # screened 同为行数口径，行数不变式相应成立）。
    if all_keys is not None:
        unscreened = int(all_keys.nunique() - screened)
    else:
        unscreened = int(len(merged) - mask.sum())
    return PrismaFlow(
        identified=identified,
        duplicates_removed=duplicates_removed,
        screened=screened,
        excluded=excluded,
        included=included,
        warnings=tuple(warns),
        unscreened=unscreened,
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
    identified = 决策表唯一键数（MM6-02：修复前恒 =screened 且从不设置
    unscreened，与 agree 路径对同一数据得出不同数字）；unscreened =
    唯一键数 − screened，MM-08 勾稽告警与 agree 路径同一措辞无条件执行。
    """
    if "status" not in merged_multi.columns:
        raise ValueError("prisma_from_merge_multi 需要 merge_decisions_multi 的输出（缺少 status 列）")
    names = [col[len("decision_"):] for col in merged_multi.columns if col.startswith("decision_")]
    if not names:
        raise ValueError("prisma_from_merge_multi 需要 merge_decisions_multi 的输出（缺少 decision_<name> 列）")

    mask = _screened_mask(merged_multi, tuple(f"decision_{name}" for name in names))
    keys = merged_multi["zotero_key"].fillna("").astype(str)
    screened = int(keys[mask].nunique())
    # MM6-02：identified/unscreened 与 agree 路径同口径（键级），勾稽
    # 不变式「唯一键数 = screened + unscreened = identified」恒成立。
    unique_total = int(keys.nunique())
    unscreened = unique_total - screened
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
        # R6-F5（复审）：按唯一键计数；r6 #49：跨桶互斥（同键分落 include/
        # exclude 两桶按首现行归属只计一次并告警，included+excluded<=
        # screened 恒成立）。
        inc_mask = (shared == "include").reindex(merged_multi.index, fill_value=False)
        exc_mask = (shared == "exclude").reindex(merged_multi.index, fill_value=False)
        included, excluded, cross_warns = _bucket_counts_exclusive(
            merged_multi, inc_mask, exc_mask, "合并表")
        warns.extend(cross_warns)

    # MM-08（MM6-02 补齐 multi 路径）：勾稽检查与 agree 路径同一措辞、
    # 同样无条件执行——修复前 multi 路径 identified 恒 =screened 倒是恒
    # 勾稽，但 unscreened 键在 stats.json 里被静默吞成默认 0。
    identified = unique_total
    if identified - 0 != screened:
        warns.append(_reconciliation_warning(identified, 0, screened))
    return PrismaFlow(
        identified=identified,
        duplicates_removed=0,
        screened=screened,
        excluded=excluded,
        included=included,
        warnings=tuple(warns),
        unscreened=unscreened,
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
      （无 DedupReport 时 identified = 决策表唯一键数、duplicates_removed = 0，
      MM6-02 键级口径）；
    - excluded：初筛排除数 + 复筛排除数（两个阶段的排除累计）；
    - included：复筛合并表的最终纳入数——默认只计 stage2 中 agree 且共同
      decision == "include" 的条目；给出 resolved2（zotero_key/final_decision
      仲裁表）时按其统计（口径与 prisma_from_merge 的 resolved 相同）；
      unscreened 自 stage1 透传（r1 评审：identified 沿用 stage1 键级口径
      而不透传 unscreened 会让「identified = screened + unscreened」在
      two_stage 上破）。
    - 复筛决策只统计落在初筛纳入键域内的条目：键域外与重复键忽略并记入
      warnings（R6-F5）。**键域与 stage1 计数同口径**（r6 #49：跨桶重复键
      按首现行归属折叠后的 include 侧），因此 included 键与 stage2 排除键
      互不重叠，included + excluded <= screened 对任意行序成立。
    - 键域口径如实声明（复审）：stage1 **经仲裁纳入**的键（resolved1 形态）
      目前不在本函数键域内——尚无 resolved1 入参，传入前需先把仲裁结果
      并入 stage1_merged 的决策列；越域条目按 warning 显式忽略，不静默
      也不计入（扩展 resolved1 属后续轮次，见集成指示文档）。
    """
    stage1 = prisma_from_merge(stage1_merged)
    # r6 #49（二轮阻断项）：stage2 键域必须与 stage1 的跨桶互斥口径一致。
    # 旧实现取「全部 agree include 键」为域——同键（agree include +
    # agree exclude）既算 stage1 include 又落进 stage2 排除侧，
    # `stage1.excluded + excluded2` 就让 included+excluded 突破 screened
    # （实测 exclude 行在前时 1/0/2，且与行序相关）。折叠后的 include 侧
    # 键与 exclude 侧键互斥 ⇒ 两侧不再重叠，不变量与行序无关。
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
            # r6 #49（三轮）：键域收窄后，stage1 判定为 exclude 的键也会落在
            # 「域外」。它并非幽灵/未筛键，而是**与初筛归属冲突**——两者
            # 语义不同，混报「域外」会误导用户去查筛选覆盖。分开计数与措辞。
            stage1_exclude = _agree_decision_keys(stage1_merged, "exclude")
            conflict = [k for k in ignored if k in stage1_exclude]
            ghost = [k for k in ignored if k not in stage1_exclude]
            if conflict:
                shown = ", ".join(conflict[:5]) + ("…" if len(conflict) > 5 else "")
                warns.append(
                    f"resolved2 有 {len(conflict)} 个键与初筛归属冲突"
                    f"（stage1 判为排除、复筛仲裁判为纳入/排除），已按初筛口径忽略: {shown}"
                )
            if ghost:
                warns.append(_resolved_warning(ghost, "resolved2"))
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
        included, excluded2, cross_warns = _bucket_counts_exclusive(
            stage2_merged, inc_mask & in_domain, exc_mask & in_domain, "stage2")
        warns.extend(cross_warns)
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
        unscreened=stage1.unscreened,
    )


def prisma_dot(flow: PrismaFlow) -> str:
    """生成 PRISMA 流程图的 Graphviz DOT 字符串（中文标签，含计数）。

    included + excluded < screened 时追加「未决」节点（U6-P5/R6-F7）：
    conflict/单边与一致 maybe 等未决记录不再从图中静默消失，分支之和
    与 screened 保持勾稽。

    warnings 非空时追加「勾稽告警」note 节点（MM6-02）：identified −
    duplicates_removed ≠ screened 等勾稽提示不再只在 stats.json 里出现——
    图上的数字（如「识别 2 → 筛选 1、去除重复 0」）自相矛盾时，解释
    就在图内，不与图分离。无告警的流程保持标准形状（不出现该节点）。
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
    if flow.warnings:
        # 告警正文转义后按行拼进 note 节点标签（琥珀色便签，虚线边框
        # 区分于流程节点；不连边——告警不是流程的一站）。
        body = "\\n".join(
            w.replace("\\", "\\\\").replace('"', '\\"') for w in flow.warnings
        )
        node_lines.append(
            f'    warnings [label="勾稽告警\\n{body}", shape=note, '
            f'style="rounded,filled", fillcolor="#FFF8E1", color="#B26A00"];'
        )
    lines = [
        "digraph PRISMA {",
        "    rankdir=TB;",
        '    node [shape=box, style="rounded,filled", fillcolor="#E3F2F0", color="#2F6F6A"];',
        *node_lines,
        *edge_lines,
        "}",
    ]
    return "\n".join(lines)
