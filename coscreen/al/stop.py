"""停止规则（stop rule）：连续排除计数与进度提示（已降级，非安全保证）。

对应 SPEC.md §12 "coscreen/al/stop.py"。自 2026-09-30 升级规格
（§5.2）起，本模块的 streak 规则**降级为进度提示**：S1 全量分析
（650 场模拟轨迹，``research/notes/stopping_rules.md`` §1）证明该
规则不存在安全默认阈值；安全停止请使用
``coscreen.al.stop_certificate.RecallCertificate``（召回证书）。
默认阈值 ``AL_STOP_STREAK_EXCLUDE = 0``（关闭），仅当调用方显式
配置 >0 时才作为进度提示启用。
纯标准库、无副作用；阈值常量 ``AL_STOP_STREAK_EXCLUDE`` 由调用方
（UI/CLI，他人实现）从 ``coscreen.config`` 读取后传入。
"""

from __future__ import annotations

__all__ = ["current_streak", "suggest_stop"]


def current_streak(decisions_in_order: list[str]) -> int:
    """统计决策序列尾部（最近端）连续 ``"exclude"`` 的条数。

    参数
    ----
    decisions_in_order:
        按时间先后排列的决策值列表（最早在前、**最近在后**），
        取值为 ``"include"`` / ``"exclude"`` / ``"maybe"``（或其他调用方
        自定义字符串）。

    返回
    ----
    int
        尾部连续字面量 ``"exclude"`` 的个数：
        - 空列表 → ``0``；
        - 尾部（最近一条）不是 ``"exclude"`` → ``0``；
        - 全部为 ``"exclude"`` → ``len(decisions_in_order)``；
        - 仅严格相等的 ``"exclude"`` 延续计数，``"include"`` / ``"maybe"``
          以及任何其他值（含大小写不同的写法）都视为打断。
    """
    streak = 0
    for decision in reversed(decisions_in_order):
        if decision != "exclude":
            break
        streak += 1
    return streak


def suggest_stop(streak: int, threshold: int) -> bool:
    """进度提示（非安全保证）。S1 全量分析证明此规则无安全默认值。
    安全停止请使用 RecallCertificate。

    （升级规格 2026-09-30 §5.2：旧语义"是否建议暂停"降级为进度提示；
    函数签名与判断行为保持不变，向后兼容。）

    规则：``streak >= threshold`` 且 ``threshold > 0`` 时返回 ``True``；
    ``threshold <= 0``（含 0 与负数）表示关闭停止建议，恒返回 ``False``
    （``coscreen.config.AL_STOP_STREAK_EXCLUDE`` 默认即 0，streak 关闭）。
    返回 ``True`` 仅表示"已连续排除 N 条"这一进度信号，**不构成**
    "剩余文献可安全停止筛查"的任何保证；安全停止判断请使用
    :class:`coscreen.al.stop_certificate.RecallCertificate`。
    """
    return streak >= threshold and threshold > 0
