"""Cohen's kappa 一致性系数：纯 Python 实现，禁止依赖 scikit-learn（SPEC §7）。

cohen_kappa 将两列决策按位置配对，仅保留双方均非空且属于 labels 的配对，
计算 po（实际一致率）、pe（随机期望一致率）与 kappa = (po - pe) / (1 - pe)。
结果与 sklearn.metrics.cohen_kappa_score(a, b, labels=labels) 数学完全一致
（sklearn 仅在测试中作基准，见 tests/test_kappa.py）。
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Sequence

#: 三分类决策标签（与 config.DECISIONS 对应，但本模块不依赖 config 以保持独立）
DEFAULT_LABELS: tuple[str, ...] = ("include", "exclude", "maybe")

__all__ = [
    "DEFAULT_LABELS",
    "KappaResult",
    "cohen_kappa",
    "interpret_kappa",
    "light_kappa",
    "pairwise_kappa",
]


@dataclass
class KappaResult:
    """kappa 计算结果：系数、实际/期望一致率、样本量、中文解读与混淆矩阵。

    confusion 的键为 (label_a, label_b) 二元组，覆盖 labels 的全部组合
    （未出现的组合计 0），按 labels 顺序排列。
    """

    kappa: float
    po: float
    pe: float
    n: int
    interpretation: str
    confusion: dict[tuple[str, str], int] = field(default_factory=dict)


def interpret_kappa(k: float) -> str:
    """按 Landis & Koch 分级返回中文解读。"""
    if k < 0:
        return "一致性低于随机水平"
    if k <= 0.20:
        return "一致性极低"
    if k <= 0.40:
        return "一致性一般"
    if k <= 0.60:
        return "一致性中等"
    if k <= 0.80:
        return "一致性较高"
    return "几乎完全一致"


def _clean(value: object) -> str:
    """None/缺失 -> ""，其余转字符串（与 merge 的缺失语义一致）。"""
    return "" if value is None else str(value)


def cohen_kappa(
    labels_a: Sequence[object],
    labels_b: Sequence[object],
    labels: Sequence[str] = DEFAULT_LABELS,
) -> KappaResult:
    """计算 Cohen's kappa，仅统计双方均有决策的配对。

    配对规则：按位置 zip，丢弃任一方为空（""/None）或不在 labels 内的组合
    （与 sklearn 传入 labels 时忽略表外类别、以保留样本为分母的口径一致）。

    边界行为：n == 0 时返回 kappa=0.0、n=0（sklearn 此场景会抛异常）；
    pe == 1 时（单一类别完全共线）若 po == 1 取 kappa=1.0，否则取 0.0
    （sklearn >= 1.9 该场景默认返回 nan，可用 replace_undefined_by 复现本约定）。
    """
    label_set = set(labels)
    confusion: dict[tuple[str, str], int] = {
        (la, lb): 0 for la in labels for lb in labels
    }
    pairs: list[tuple[str, str]] = [
        (a, b)
        for a, b in zip((_clean(v) for v in labels_a), (_clean(v) for v in labels_b))
        if a and b and a in label_set and b in label_set
    ]
    n = len(pairs)
    if n == 0:
        return KappaResult(
            kappa=0.0,
            po=0.0,
            pe=0.0,
            n=0,
            interpretation=interpret_kappa(0.0),
            confusion=confusion,
        )

    observed: Counter[tuple[str, str]] = Counter(pairs)
    confusion.update(observed)
    marginal_a: Counter[str] = Counter(a for a, _ in pairs)
    marginal_b: Counter[str] = Counter(b for _, b in pairs)

    agree = sum(cnt for (a, b), cnt in observed.items() if a == b)
    po = agree / n
    # 用整数计数求和后一次除法，避免逐项浮点乘除累积误差
    pe = sum(marginal_a[label] * marginal_b[label] for label in labels) / (n * n)

    if pe == 1.0:
        kappa = 1.0 if po == 1.0 else 0.0
    else:
        kappa = (po - pe) / (1.0 - pe)
    return KappaResult(
        kappa=kappa,
        po=po,
        pe=pe,
        n=n,
        interpretation=interpret_kappa(kappa),
        confusion=confusion,
    )


# ---------------------------------------------------------------------------
# 多筛选员扩展（N>=2，SPEC §7）
# ---------------------------------------------------------------------------

def pairwise_kappa(
    named_labels: dict[str, Sequence[object]],
) -> dict[tuple[str, str], KappaResult]:
    """两两计算 Cohen's kappa（多人扩展，SPEC §7）。

    named_labels 为 {"<筛选员名>": 决策标签序列}，各序列按同一顺序对齐
    （如按 zotero_key 排序后的合并表逐行取值）。对每对 (i, j)（按字典插入
    顺序 i<j）按位配对，由 cohen_kappa 内部丢弃任一方为空的位置——
    因此每一对的结果恰等于在"双方均非空的共享子序列"上直接调用 cohen_kappa。

    某一对没有共同标签时，其 KappaResult 为 n=0（kappa=po=pe=0.0，
    与 cohen_kappa 的 n==0 约定一致）。返回字典按插入顺序排列，确定性。
    """
    names = list(named_labels)
    results: dict[tuple[str, str], KappaResult] = {}
    for i, left in enumerate(names):
        for right in names[i + 1:]:
            results[(left, right)] = cohen_kappa(named_labels[left], named_labels[right])
    return results


def light_kappa(named_labels: dict[str, Sequence[object]]) -> float | None:
    """Light's kappa：两两 Cohen's kappa 的算术平均（Light 1971，多人扩展）。

    口径说明：n==0（无共同标签）的配对不参与平均；若所有配对的 n 均为 0
    （或不足两方），则没有任何可计算的配对，返回 None。
    返回值为 float 时按配对数等权平均，确定性。
    """
    values = [result.kappa for result in pairwise_kappa(named_labels).values() if result.n > 0]
    if not values:
        return None
    return sum(values) / len(values)
