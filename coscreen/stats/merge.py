"""合并与汇总：双盲决策表外连接、一致性状态判定、排除理由分布（SPEC §7）。

merge_decisions 将两名筛选员导出的决策 DataFrame 按 zotero_key 外连接，
为每条文献标注 status ∈ {"agree", "conflict", "A_only", "B_only"}；
summarize 输出各类计数与一致率；exclusion_reason_distribution 汇总双方
exclude 记录的排除理由频次。

多筛选员扩展（N>=2）：merge_decisions_multi 接受有序映射
{"<列名后缀>": df}，输出 decision_<name>/exclusion_reason_<name>/notes_<name>
列与 n_screened，status ∈ {"agree", "conflict", "incomplete"}；
summarize_multi 输出计数、参与度与两两一致率；
exclusion_reason_distribution_multi 汇总全部筛选员的排除理由频次。
所有输出按 zotero_key / 键名排序，保证确定性。
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

import numpy as np
import pandas as pd

from coscreen.config import DECISIONS

#: 合并后每条文献的一致性状态（与 SPEC §7 一致）
MERGE_STATUS = ("agree", "conflict", "A_only", "B_only")

#: 合并输出的列顺序（固定，保证下游 CSV/JSON 确定性）
MERGE_COLUMNS = (
    "zotero_key",
    "title",
    "decision_A",
    "decision_B",
    "exclusion_reason_A",
    "exclusion_reason_B",
    "notes_A",
    "notes_B",
    "status",
)

_BASE_COLUMNS = ("zotero_key", "title", "decision", "exclusion_reason", "notes")
_REQUIRED_COLUMNS = ("zotero_key", "decision")


@dataclass
class MergeSummary:
    """合并结果的计数摘要。

    agreement_rate = n_agree / (n_agree + n_conflict)，
    仅统计双方共有条目；分母为 0 时取 0.0。
    """

    n_total: int
    n_agree: int
    n_conflict: int
    n_A_only: int
    n_B_only: int
    agreement_rate: float


def _normalize_side(df: pd.DataFrame, label: str = "") -> pd.DataFrame:
    """规范化单方决策表：补齐缺失列、空值填充为 ""、统一为字符串。

    zotero_key 重复即 ValueError：外连接遇到单侧重复键会产生笛卡尔积行，
    一致性统计被静默夸大（实测 1 agree → 2 agree + 1 conflict）。
    """
    missing = [c for c in _REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"决策表缺少必需列: {missing}")
    keys = df["zotero_key"].fillna("").astype(str)
    invalid_keys = keys.str.strip().eq("") | keys.str.len().gt(300) | keys.ne(keys.str.strip())
    if invalid_keys.any():
        raise ValueError(f"{label} 侧 zotero_key 必须为 1 到 300 个无首尾空格的字符")
    duplicated = sorted(set(keys[keys != ""][keys[keys != ""].duplicated()]))
    if duplicated:
        raise ValueError(
            f"{label} 侧 zotero_key 存在重复（合并将产生笛卡尔积行、一致性统计失真）: "
            f"{duplicated[:10]}{'…' if len(duplicated) > 10 else ''}"
        )
    keep = [c for c in _BASE_COLUMNS if c in df.columns]
    out = df.loc[:, keep].copy()
    for col in _BASE_COLUMNS:
        if col not in out.columns:
            out[col] = ""
        out[col] = out[col].fillna("").astype(str)
    out["decision"] = out["decision"].str.strip().str.lower()
    invalid_decisions = sorted(set(out["decision"]) - {"", *DECISIONS})
    if invalid_decisions:
        raise ValueError(
            f"{label} 侧 decision 含非法值: {invalid_decisions[:5]}"
        )
    return out.reset_index(drop=True)


def merge_decisions(df_a: pd.DataFrame, df_b: pd.DataFrame) -> pd.DataFrame:
    """按 zotero_key 外连接两份决策表，输出固定 9 列并按 zotero_key 排序。

    状态判定：双方均有决策且相等 -> agree；均有但不等 -> conflict；
    仅 A 有 -> A_only；仅 B 有 -> B_only。缺失决策以 "" 表示。
    """
    a = _normalize_side(df_a, "A")
    b = _normalize_side(df_b, "B")
    joined = a.merge(b, on="zotero_key", how="outer", suffixes=("_A", "_B"), indicator=True)

    in_a = joined["_merge"].isin(("left_only", "both"))
    in_b = joined["_merge"].isin(("right_only", "both"))
    both = in_a & in_b
    equal = joined["decision_A"] == joined["decision_B"]
    status = np.select(
        condlist=[both & equal, both & ~equal, in_a & ~in_b, in_b & ~in_a],
        choicelist=list(MERGE_STATUS),
        default="",
    )

    title_a = joined["title_A"].fillna("")
    title_b = joined["title_B"].fillna("")
    merged = pd.DataFrame(
        {
            "zotero_key": joined["zotero_key"].fillna(""),
            "title": title_a.where(title_a != "", title_b),
            "decision_A": joined["decision_A"].fillna(""),
            "decision_B": joined["decision_B"].fillna(""),
            "exclusion_reason_A": joined["exclusion_reason_A"].fillna(""),
            "exclusion_reason_B": joined["exclusion_reason_B"].fillna(""),
            "notes_A": joined["notes_A"].fillna(""),
            "notes_B": joined["notes_B"].fillna(""),
            "status": status,
        },
        columns=list(MERGE_COLUMNS),
    )
    return merged.sort_values("zotero_key", kind="stable").reset_index(drop=True)


def summarize(merged: pd.DataFrame) -> MergeSummary:
    """统计合并表中各状态条数与一致率（仅双方共有条目参与一致率）。"""
    if "status" not in merged.columns:
        raise ValueError("summarize 需要 merge_decisions 的输出（缺少 status 列）")
    status = merged["status"]
    n_agree = int((status == "agree").sum())
    n_conflict = int((status == "conflict").sum())
    n_a_only = int((status == "A_only").sum())
    n_b_only = int((status == "B_only").sum())
    denominator = n_agree + n_conflict
    agreement_rate = (n_agree / denominator) if denominator > 0 else 0.0
    return MergeSummary(
        n_total=int(len(merged)),
        n_agree=n_agree,
        n_conflict=n_conflict,
        n_A_only=n_a_only,
        n_B_only=n_b_only,
        agreement_rate=agreement_rate,
    )


def exclusion_reason_distribution(merged: pd.DataFrame) -> dict[str, int]:
    """汇总双方全部 exclude 记录的 exclusion_reason 频次。

    空理由计入 "" 键；返回字典按频次降序、同频次按键名升序排列，
    同一输入多次调用返回内容与顺序完全一致。
    """
    counts: Counter[str] = Counter()
    for side in ("A", "B"):
        mask = merged[f"decision_{side}"] == "exclude"
        reasons = merged.loc[mask, f"exclusion_reason_{side}"].fillna("")
        for reason in reasons:
            counts[str(reason)] += 1
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


# ---------------------------------------------------------------------------
# 多筛选员扩展（N>=2，SPEC §7）
# ---------------------------------------------------------------------------

#: 多人合并后每条文献的一致性状态
MULTI_MERGE_STATUS = ("agree", "conflict", "incomplete")

_DECISION_FIELD = "decision"
_REASON_FIELD = "exclusion_reason"
_NOTES_FIELD = "notes"


@dataclass
class MultiMergeSummary:
    """多人合并结果的计数摘要。

    agreement_rate = n_agree / (n_agree + n_conflict)，分母为 0 时取 0.0；
    participation 为每位筛选员已筛（决策非空）条数；
    pairwise_agreement 为两两共有条目（双方决策均非空）的决策一致率，
    某一对无共有条目时取 0.0（与 agreement_rate 的分母 0 约定一致）。
    """

    n_total: int
    n_agree: int
    n_conflict: int
    n_incomplete: int
    agreement_rate: float
    participation: dict[str, int]
    pairwise_agreement: dict[tuple[str, str], float]


def _multi_names(merged: pd.DataFrame) -> list[str]:
    """从多人合并表中按列顺序提取筛选员名（decision_<name> 列后缀）。"""
    names = [col[len(_DECISION_FIELD) + 1:] for col in merged.columns
             if col.startswith(f"{_DECISION_FIELD}_")]
    if not names:
        raise ValueError("输入不是 merge_decisions_multi 的输出（缺少 decision_<name> 列）")
    return names


def merge_decisions_multi(named_dfs: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """按 zotero_key 外连接 N>=2 份决策表（SPEC §7 多筛选员扩展）。

    named_dfs 为有序映射 {"<列名后缀>": df, ...}，列名后缀即筛选员名，
    少于 2 份时抛 ValueError。输出列固定为：
    zotero_key, title, decision_<name>...(N 列), exclusion_reason_<name>...(N 列),
    notes_<name>...(N 列), n_screened, status，并按 zotero_key 排序。

    某筛选员未筛该条时其 decision_<name> 为 ""（exclusion_reason/notes 同）；
    n_screened 为非空决策的筛选员数；status 判定：
    agree = n_screened>=2 且决策全同；conflict = n_screened>=2 且不全同；
    incomplete = 不足 2 人筛选（仅单人筛或无人筛）。
    title 取按 named_dfs 顺序首个非空值。

    N=2 时与 merge_decisions 的对应关系：agree/conflict 逐一相同，
    A_only 与 B_only 均对应 incomplete（决策非空时完全等价）。
    """
    names = list(named_dfs)
    if len(names) < 2:
        raise ValueError(
            f"merge_decisions_multi 至少需要 2 位筛选员的决策表，实际收到 {len(names)} 份"
        )
    normalized = {name: _normalize_side(df, name) for name, df in named_dfs.items()}

    joined: pd.DataFrame | None = None
    for name, part in normalized.items():
        renamed = part.rename(
            columns={
                "title": f"title_{name}",
                _DECISION_FIELD: f"{_DECISION_FIELD}_{name}",
                _REASON_FIELD: f"{_REASON_FIELD}_{name}",
                _NOTES_FIELD: f"{_NOTES_FIELD}_{name}",
            }
        )
        joined = renamed if joined is None else joined.merge(renamed, on="zotero_key", how="outer")

    decision_cols = [f"{_DECISION_FIELD}_{name}" for name in names]
    decisions = joined[decision_cols].fillna("")
    nonempty = decisions != ""
    n_screened = nonempty.sum(axis=1)
    # 双方均非空的决策中不同取值的个数（0 表示无人筛、1 表示全同）
    n_distinct = decisions.where(nonempty).nunique(axis=1)
    status = np.select(
        condlist=[(n_screened >= 2) & (n_distinct <= 1), (n_screened >= 2) & (n_distinct > 1)],
        choicelist=("agree", "conflict"),
        default="incomplete",
    )

    title = joined[f"title_{names[0]}"].fillna("")
    for name in names[1:]:
        title = title.where(title != "", joined[f"title_{name}"].fillna(""))

    data: dict[str, pd.Series | np.ndarray] = {
        "zotero_key": joined["zotero_key"].fillna(""),
        "title": title,
    }
    for field in (_DECISION_FIELD, _REASON_FIELD, _NOTES_FIELD):
        for name in names:
            data[f"{field}_{name}"] = joined[f"{field}_{name}"].fillna("")
    data["n_screened"] = n_screened
    data["status"] = status

    columns = [
        "zotero_key",
        "title",
        *[f"{_DECISION_FIELD}_{name}" for name in names],
        *[f"{_REASON_FIELD}_{name}" for name in names],
        *[f"{_NOTES_FIELD}_{name}" for name in names],
        "n_screened",
        "status",
    ]
    merged = pd.DataFrame(data, columns=columns)
    return merged.sort_values("zotero_key", kind="stable").reset_index(drop=True)


def summarize_multi(merged: pd.DataFrame) -> MultiMergeSummary:
    """统计多人合并表的各类计数、一致率、参与度与两两一致率。

    一致率仅统计 n_screened>=2 的条目（agree + conflict）；
    participation 按输出列顺序给出每位筛选员已筛条数；
    pairwise_agreement 键为 (name_i, name_j) 二元组（按列顺序 i<j），
    仅统计双方均非空的条目，无共有条目时取 0.0。
    """
    if "status" not in merged.columns:
        raise ValueError("summarize_multi 需要 merge_decisions_multi 的输出（缺少 status 列）")
    names = _multi_names(merged)

    status = merged["status"]
    n_agree = int((status == "agree").sum())
    n_conflict = int((status == "conflict").sum())
    n_incomplete = int((status == "incomplete").sum())
    denominator = n_agree + n_conflict
    agreement_rate = (n_agree / denominator) if denominator > 0 else 0.0

    participation = {
        name: int((merged[f"{_DECISION_FIELD}_{name}"].fillna("") != "").sum())
        for name in names
    }

    pairwise_agreement: dict[tuple[str, str], float] = {}
    for i, left in enumerate(names):
        left_dec = merged[f"{_DECISION_FIELD}_{left}"].fillna("")
        for right in names[i + 1:]:
            right_dec = merged[f"{_DECISION_FIELD}_{right}"].fillna("")
            mask = (left_dec != "") & (right_dec != "")
            shared = int(mask.sum())
            rate = float((left_dec[mask] == right_dec[mask]).sum() / shared) if shared > 0 else 0.0
            pairwise_agreement[(left, right)] = rate

    return MultiMergeSummary(
        n_total=int(len(merged)),
        n_agree=n_agree,
        n_conflict=n_conflict,
        n_incomplete=n_incomplete,
        agreement_rate=agreement_rate,
        participation=participation,
        pairwise_agreement=pairwise_agreement,
    )


def exclusion_reason_distribution_multi(merged: pd.DataFrame) -> dict[str, int]:
    """汇总全部筛选员 exclude 记录的 exclusion_reason 频次（多人扩展）。

    每位筛选员的每条 exclude 记录各计 1 次（同一文献被多人排除计多次）；
    空理由计入 "" 键；按频次降序、同频次按键名升序排列，与单人版一致。
    """
    counts: Counter[str] = Counter()
    for name in _multi_names(merged):
        mask = merged[f"{_DECISION_FIELD}_{name}"] == "exclude"
        reasons = merged.loc[mask, f"{_REASON_FIELD}_{name}"].fillna("")
        for reason in reasons:
            counts[str(reason)] += 1
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))
