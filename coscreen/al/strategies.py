"""查询策略（query strategies）：max / uncertainty / mixed。

对应 SPEC.md §12 "coscreen/al/strategies.py" 与 FUNCTIONAL_CHECKLIST.md
"Active learning ranking" 条目 2（查询策略确定性）。纯标准库实现；
随机性仅用于 mixed 策略，且用 ``random.Random(rng_seed)`` 种子化——
给定相同的 ``(keys, scores, rng_seed)``，输出完全确定、可复现。

本模块只做"排序"，不训练模型、不读数据库；分数字典由调用方
（ranker，他人实现）传入。
"""

from __future__ import annotations

import random

from coscreen.config import AL_MIXED_RANDOM_RATIO

__all__ = ["query_order"]

# 合法查询策略（与 SPEC.md §12 一致）
_VALID_STRATEGIES = ("max", "uncertainty", "mixed")


def _max_order(keys: list[str], scores: dict[str, float]) -> list[str]:
    """max 策略内部实现：分数降序，同分保持 keys 原有先后（稳定排序）。

    缺失分数的键按 0.0 参与排序。CPython 的 ``sorted`` 是稳定排序，
    且 ``reverse=True`` 不破坏稳定性（相等元素保持原相对顺序）。
    """
    return sorted(keys, key=lambda k: scores.get(k, 0.0), reverse=True)


def query_order(
    keys: list[str],
    scores: dict[str, float],
    strategy: str,
    rng_seed: int,
) -> list[str]:
    """按查询策略返回待筛文献键的排序（始终是 ``keys`` 的一个排列）。

    参数
    ----
    keys:
        全部待筛文献键（zotero_key），按调用方的基准顺序（如导入顺序）给出。
        输出与输入逐位置保持多重集合相等——不新增、不丢弃、不去重。
    scores:
        ``zotero_key -> 模型分数 P(include)``（约 0-1）。
        **键在字典中缺失分数时一律按 0.0 处理**（三种策略通用）。
    strategy:
        - ``"max"``：分数降序；同分按键在 ``keys`` 中的先后顺序稳定保持。
        - ``"uncertainty"``：``|score - 0.5|`` 升序（最不确定者优先）；
          同分（含等距）稳定规则与 max 相同。
        - ``"mixed"``：95% max + 5% 随机，精确算法定义见下文。
    rng_seed:
        mixed 策略的随机种子；其余策略忽略该参数。

    返回
    ----
    list[str]
        ``keys`` 的一个排列。空 ``keys`` 返回 ``[]``；
        未知 ``strategy`` 抛 :class:`ValueError`（参数校验先于空列表处理：
        未知策略即使 ``keys`` 为空也抛错）。

    mixed 的精确定义（给定输入与种子后完全确定）
    --------------------------------------------
    1. ``rng = random.Random(rng_seed)``；
    2. ``shuffled = list(keys)``，然后 ``rng.shuffle(shuffled)``
       （洗牌先消耗随机流）；``shuffled`` 定义"随机候选键"的先后顺序；
    3. 若 ``scores`` 为空字典：直接返回 ``shuffled``
       （无任何模型分数可排时，mixed 退化为纯随机序基线；
       此时 max 因同分为 0.0 而退化为 keys 原序）；
    4. 否则取 max 策略的最终降序列表 ``order``（见 ``_max_order``），
       游标 ``pos = 0`` 指向 ``shuffled`` 中首个"未使用"槽位；
       对 ``i = 0 .. len(order) - 1`` 依次执行：
       恰好调用一次 ``rng.random()``，若
       ``rng.random() < AL_MIXED_RANDOM_RATIO``（默认 0.05），则令
       ``k = shuffled[pos]``（首个未使用的随机候选键），在 ``order`` 内
       找到 ``k`` 当前所在下标 ``j``（``j = order.index(k)``），交换
       ``order[i]`` 与 ``order[j]``（候选键 k 上浮到位置 i，原占据者
       换到 k 原来的位置），随后 ``pos += 1``（该候选槽位视为已使用）。
       交换发生在 ``order`` 内部，因此 ``order`` 在每一步都严格保持为
       ``keys`` 的排列（不重复、不丢弃）；由于每个位置 i 至多消耗一个
       候选槽位，第 k 次交换发生时 ``pos == k <= i < len(shuffled)`` 恒
       成立，游标不会越界（实现中仍保留防御性边界判断，不改变确定性）；
    5. 所有 i 处理完毕后返回 ``order``。

    每个位置恰好消耗一次随机数、交换只发生于命中的位置，因此算法
    对 ``(keys, scores, rng_seed)`` 完全确定，且与逐元素遍历顺序无关。
    效果：约 ``AL_MIXED_RANDOM_RATIO`` 比例的位置由随机候选键占据，
    其余位置维持 max 序——即"95% max + 5% random"。
    """
    if strategy not in _VALID_STRATEGIES:
        raise ValueError(
            f"未知查询策略: {strategy!r}；可选值: {', '.join(_VALID_STRATEGIES)}"
        )
    if not keys:
        return []

    if strategy == "max":
        return _max_order(keys, scores)

    if strategy == "uncertainty":
        # |score - 0.5| 升序；缺失分数按 0.0（距离 0.5）。稳定排序保证同距时保持 keys 原序。
        return sorted(keys, key=lambda k: abs(scores.get(k, 0.0) - 0.5))

    # ---- mixed：见 docstring 的精确定义 ----
    rng = random.Random(rng_seed)
    shuffled = list(keys)
    rng.shuffle(shuffled)

    # 空分数字典：无模型可用，退化为纯随机序（SPEC 约定）。
    if not scores:
        return shuffled

    order = _max_order(keys, scores)
    pos = 0  # shuffled 中首个"未使用"槽位
    n = len(order)
    for i in range(n):
        if rng.random() < AL_MIXED_RANDOM_RATIO and pos < len(shuffled):
            # 将首个未使用的随机候选键换到位置 i（order 内部交换，保持排列不变）。
            key = shuffled[pos]
            j = order.index(key)
            order[i], order[j] = order[j], order[i]
            pos += 1
    return order
