"""查询策略（query strategies）：max / uncertainty / mixed。

参数表见 ``docs/methodology/sim/README.md`` §1.4（功能清单文件不在本
仓库，勿再引用）。纯标准库实现；随机性仅用于 mixed 策略，且用
``random.Random(rng_seed)`` 种子化——给定相同的
``(keys, scores, rng_seed)``，输出完全确定、可复现。

本模块只做"排序"，不训练模型、不读数据库；分数字典由调用方
（ranker，他人实现）传入。

r9 修复（b10·F2-39/F2-40/F2-41）：

- **值域校验**：``scores`` 的值必须是数值且不能是 ``NaN``（NaN 无大小
  关系，"分数降序"在它上面没有定义，旧实现会静默给出与契约不符的序）；
  字符串/``None`` 等非数值此前抛裸 ``TypeError``，现改为可读的
  ``ValueError``（与未知策略同一异常族，调用方一处处理即可）。
  ``±inf`` 仍被接受——它有确定的序，且是"某候选确定命中/确定排除"的
  合法表达。
- **``scores=None`` 语义统一**：三种策略一律按"无任何分数"处理
  （等价空字典）。此前 max/uncertainty 抛 ``AttributeError``、mixed 却
  静默退化为随机序，同一输入三种策略三种行为。
- **mixed 的复杂度**：用位置索引替代 ``order.index(key)``，把命中随机
  分支时的 O(n) 查找降为 O(1)，整体由 O(n²/20) 回到 O(n)。键互异（生产
  与 ranker 的实际输入）时逐位输出不变；键有重复时退回旧的
  ``list.index`` 语义，保证既有调用方拿到的序不受影响。
"""

from __future__ import annotations

import random

from coscreen.config import AL_MIXED_RANDOM_RATIO

__all__ = ["query_order", "validate_strategy"]

# 合法查询策略（与 ``docs/methodology/sim/README.md`` §1.4 一致）
_VALID_STRATEGIES = ("max", "uncertainty", "mixed")


def validate_strategy(strategy: str) -> None:
    """校验查询策略名；非法值抛 :class:`ValueError`（中文消息）。

    独立成函数（r9，b10·F2-28）：``simulation`` 的 ``n_relevant == 0``
    早退路径也必须先过这道校验，不能再靠 ``query_order`` 的调用顺带完成。
    """
    if strategy not in _VALID_STRATEGIES:
        raise ValueError(
            f"未知查询策略: {strategy!r}；可选值: {', '.join(_VALID_STRATEGIES)}"
        )


def _checked_scores(scores) -> dict:
    """``scores`` 的规范形式：``None`` → 空字典；值域校验后原样返回。

    - ``None`` 视为"无任何分数"（b10·F2-41，三策略统一）；
    - 值必须是数值（``int``/``float``，含 ``bool``——它是 ``int`` 的子类，
      旧实现照常当 0/1 用，R2：不放宽成拒绝），且不是 ``NaN``；``±inf``
      允许（有确定序）；
    - 超出双精度范围的 ``int``（如 ``10**400``）拒绝：排序比较时它会抛
      ``OverflowError``，与"非数值"同一处置（可读 ValueError）。
    """
    if scores is None:
        return {}
    for key, value in scores.items():
        if not isinstance(value, (int, float)):
            raise ValueError(
                f"分数必须是数值，键 {key!r} 得到 {value!r}"
            )
        # 自反不等价即 NaN；不用 math.isnan，避免超大 int 在转 float 时
        # 抛 OverflowError（那是合法数值，只是大）。
        if value != value:
            raise ValueError(
                f"分数不能是 NaN（无大小关系，无法定义排序），键 {key!r}"
            )
        if isinstance(value, int):
            try:
                float(value)
            except OverflowError as exc:
                raise ValueError(
                    f"分数必须是可表示的双精度数值，键 {key!r} 得到 {value!r}"
                ) from exc
    return scores


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

    实现注记（r9，b10·F2-39）：第 4 步的"在 ``order`` 内找到 ``k`` 当前
    所在下标"用**位置表**（键 → 下标，交换后同步更新）实现，不再调用
    ``order.index``——后者是 O(n) 查找，命中随机分支时整体退化为
    O(n²/20)（实测 8k→64k 每翻倍 ×3.7）。位置表给出与 ``list.index``
    完全相同的结果（键互异时；键重复时退回旧语义，见实现），
    因此输出逐位不变。
    """
    validate_strategy(strategy)
    scores = _checked_scores(scores)
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
    # 位置表只在键互异时可用（生产/ranker 的实际输入）；键有重复时
    # ``list.index`` 的语义（取首个）与位置表不同，退回旧实现。
    unique_keys = len(set(order)) == n
    positions = {key: index for index, key in enumerate(order)} if unique_keys else None
    for i in range(n):
        if rng.random() < AL_MIXED_RANDOM_RATIO and pos < len(shuffled):
            # 将首个未使用的随机候选键换到位置 i（order 内部交换，保持排列不变）。
            key = shuffled[pos]
            j = positions[key] if positions is not None else order.index(key)
            other = order[i]
            order[i], order[j] = key, other
            if positions is not None:
                # 交换后两键的下标互换（i 与 j）。
                positions[key] = i
                positions[other] = j
            pos += 1
    return order
