"""主动学习模拟基准（SPEC.md §13；指标定义与 ASReview Insights 一致）。

在**完整标注**的黄金标准集上"回放"主动学习逐条筛选过程，回答
"要达到 95% 召回需要人工筛多少条？"（systematic review 的核心效率指标）。

回放规则（与 SPEC §13 及 FUNCTIONAL_CHECKLIST "Simulation benchmark" 一致）：

1. **Warm-start（模型尚不存在）**：初始顺序 = :func:`query_order` 作用在
   **空分数字典**上——``max``/``uncertainty`` 缺失分数按 0.0 参与排序且同分
   稳定，故退化为**输入顺序**；``mixed`` 空分数退化为**种子化随机序**；
   ``uncertainty`` 无分数无意义，同样按输入顺序走（约定如此）。
2. **逐条筛选**：取当前队首视为"已筛"，其黄金标签（include=1 / exclude=0）
   立即加入训练集；文本为 ``" ".join(title, abstract, authors, journal)``
   （与 ActiveLearningRanker 的训练文本组装一致）。
3. **重训与重排（预热节奏，重点）**：前 ``WARMUP``（=5）条筛完之前**不训练**，
   队列保持初始顺序；自第 5 条筛完起，**每筛 1 条**都用"迄今全部已筛标签"
   重新 ``fit`` TF-IDF（整表 refit）+ MultinomialNB(alpha=1.0)，并对**剩余
   未筛**文献重新 :func:`query_order`。重排的键序基准为**当前队列顺序**——
   同分保持既有先后，全程逐位确定、可复现。
4. **提前终止**：全部相关记录均已发现（或队列走空）即停止，不再重训。

指标（n_relevant == 0 时全部为 0，见 :meth:`replay`）：

- ``n_to_95``：达到召回目标 :data:`coscreen.config.SIM_RECALL_TARGET`（0.95）
  所需的**最小**筛选条数——即第 k 小的相关发现位次，其中 k 为满足
  ``k / n_relevant >= 0.95`` 的最小计数（位次之间召回不变，最小 n 必为某条
  相关记录的发现位次）；
- ``wss95 = (N - n_to_95) / N - (1 - 0.95)``，标准 WSS@95，截断到 ``[-1, 1]``
  （可 Save Money：与"随机筛到 95% 召回需 N×0.95 条"相比节省的比例）；
- ``r95``：筛到 ``n_to_95`` 条时的实际召回（定义量，自检应 >= 0.95；必须
  筛完全集才达标时为 1.0）；
- ``ttd_mean``：每条相关记录**首次被筛到的位次**（time to discovery）均值。

规模上限：``len(articles) > MAX_RECORDS``（2000）抛 :class:`ValueError`——
逐条 refit 的总代价约 O(n²·v)，本模块面向测试与中小规模基准，不面向
生产级全量回放（需分块抽样）。

运行时零第三方依赖（仅标准库）；库代码不打印，呈现由 CLI 负责。
"""

from __future__ import annotations

from dataclasses import dataclass

from coscreen import config
from coscreen.al.features import TfidfVectorizer
from coscreen.al.nb import MultinomialNB
from coscreen.al.strategies import query_order
from coscreen.models import Article

__all__ = ["MAX_RECORDS", "WARMUP", "SimResult", "replay", "simulate_random_baseline"]

#: 预热期条数：筛满 WARMUP 条之前不训练，之后每筛 1 条重训重排一次。
WARMUP: int = 5

#: 单次回放的最大文献数（超过抛 ValueError，见模块 docstring）。
MAX_RECORDS: int = 2000

# 合法黄金标签（与决策语义一致；maybe 不是黄金标签）
_GOLD_LABELS = ("include", "exclude")


@dataclass
class SimResult:
    """一次回放的完整指标（字段含义见模块 docstring 与 SPEC.md §13）。"""

    strategy: str  # "max" | "uncertainty" | "mixed"
    seed: int  # 随机种子（mixed 与随机基线生效；其余透传记录）
    n_records: int  # 参与模拟的文献总数
    n_relevant: int  # 相关（include）文献数
    n_to_95: int  # 达到 95% 召回所需筛选条数
    wss95: float  # (N - n_to_95)/N - (1 - 0.95)，截断到 [-1, 1]
    r95: float  # 筛到 n_to_95 条时的实际召回（自检量）
    ttd_mean: float  # 相关记录首次发现位次的均值（time to discovery）


def _article_text(article: Article) -> str:
    """训练/打分文本：title + abstract + authors + journal 空格拼接。"""
    return " ".join(
        (article.title or "", article.abstract or "",
         article.authors or "", article.journal or "")
    )


def _validated_gold(articles: list[Article], gold: dict[str, str]) -> dict[str, str]:
    """校验黄金标准：覆盖每条文献、标签合法；返回规范化后的标签映射。

    - 文献键在 ``gold`` 中缺失 → ``KeyError``（中文消息，列出前几个缺失键）；
    - 标签（去首尾空白并小写后）不是 include/exclude → ``ValueError``；
    - ``gold`` 中多出的键（不在 ``articles`` 里）忽略，不做校验。
    """
    keys = [a.zotero_key for a in articles]
    missing = [k for k in keys if k not in gold]
    if missing:
        sample = ", ".join(repr(k) for k in missing[:5])
        raise KeyError(f"黄金标准缺少 {len(missing)} 条文献的标签，例如: {sample}")
    labels: dict[str, str] = {}
    for k in keys:
        label = str(gold[k]).strip().lower()
        if label not in _GOLD_LABELS:
            raise ValueError(
                f"黄金标签必须是 include/exclude，键 {k!r} 得到 {gold[k]!r}"
            )
        labels[k] = label
    return labels


def _score_remaining(
    labeled: list[str], labels: dict[str, str], texts: dict[str, str],
    remaining: list[str],
) -> dict[str, float]:
    """在"迄今全部已筛标签"上 refit TF-IDF + MultinomialNB，给剩余文献打分。

    训练顺序 = 筛选先后顺序（``labeled`` 顺序固定 → 浮点累加顺序固定 →
    结果逐位可复现）；``labeled`` 至少含 ``WARMUP`` 条，满足 NB 非空训练集要求
    （全部为 exclude 时 NB 单类退化，得分恒 0.0，排序退化为既有顺序——
    这是有意的诚实行为）。
    """
    vec = TfidfVectorizer()
    x_train = vec.fit_transform([texts[k] for k in labeled])
    nb = MultinomialNB(alpha=1.0)
    nb.fit(x_train, [1 if labels[k] == "include" else 0 for k in labeled])
    x_cand = vec.transform([texts[k] for k in remaining])
    return dict(zip(remaining, nb.predict_scores(x_cand)))


def _metrics(
    strategy: str, seed: int, n_records: int, n_relevant: int, ttd: list[int],
) -> SimResult:
    """由相关记录的发现位次列表计算 SimResult（SPEC §13 指标定义）。"""
    if n_relevant == 0 or n_records == 0:
        return SimResult(strategy, seed, n_records, 0, 0, 0.0, 0.0, 0.0)
    target = config.SIM_RECALL_TARGET
    sorted_ttd = sorted(ttd)
    # n_to_95 = 满足 k/n_relevant >= target 的最小计数 k 对应的发现位次；
    # 位次之间召回恒定，最小 n 必落在某个发现位次上。1e-12 容差吸收
    # 浮点表示误差（如 19/20 与 0.95 的比较）。
    n_to_95 = n_records  # 兜底（target<=1 时必在 ttd 内命中，不会用到）
    r95 = 1.0
    for count, position in enumerate(sorted_ttd, start=1):
        if count / n_relevant >= target - 1e-12:
            n_to_95 = position
            r95 = count / n_relevant
            break
    wss95 = (n_records - n_to_95) / n_records - (1.0 - target)
    wss95 = max(-1.0, min(1.0, wss95))
    ttd_mean = sum(sorted_ttd) / len(sorted_ttd)
    return SimResult(
        strategy=strategy, seed=seed, n_records=n_records,
        n_relevant=n_relevant, n_to_95=n_to_95, wss95=wss95,
        r95=r95, ttd_mean=ttd_mean,
    )


def _simulate(
    articles: list[Article], gold: dict[str, str], strategy: str, seed: int,
    learn: bool,
) -> SimResult:
    """回放公共实现；``learn=False`` 时只按初始顺序走（随机基线）。"""
    if len(articles) > MAX_RECORDS:
        raise ValueError(
            f"模拟规模过大：{len(articles)} 条超过上限 {MAX_RECORDS}；"
            "请抽样后重试（逐条重训的开销随规模平方增长）"
        )
    labels = _validated_gold(articles, gold)
    keys = [a.zotero_key for a in articles]
    texts = {a.zotero_key: _article_text(a) for a in articles}
    relevant = {k for k in keys if labels[k] == "include"}

    n_relevant = len(relevant)
    if n_relevant == 0:
        return _metrics(strategy, seed, len(keys), 0, [])

    # Warm-start：空分数下的初始顺序（max/uncertainty=输入序，mixed=种子化随机序）
    order = query_order(keys, {}, strategy, seed)
    labeled: list[str] = []  # 已筛键，按筛选先后排列（= 训练顺序）
    ttd: list[int] = []  # 每条相关记录的发现位次（1 起）
    position = 0
    while order:
        key = order.pop(0)
        position += 1
        labeled.append(key)
        if key in relevant:
            ttd.append(position)
            if len(ttd) == n_relevant:
                break  # 全部相关已发现，提前终止
        # 重训节奏：自第 WARMUP 条起每筛 1 条重训并重排剩余队列
        if learn and len(labeled) >= WARMUP and order:
            scores = _score_remaining(labeled, labels, texts, order)
            order = query_order(order, scores, strategy, seed)
    return _metrics(strategy, seed, len(keys), n_relevant, ttd)


def replay(
    articles: list[Article], gold: dict[str, str],
    strategy: str = config.AL_DEFAULT_STRATEGY, seed: int = config.AL_SEED,
) -> SimResult:
    """在完整标注的黄金标准集上回放主动学习逐条筛选（SPEC §13）。

    参数
    ----
    articles:
        全部（非重复）文献，按基准顺序（如导入顺序）传入；键须唯一。
    gold:
        ``zotero_key -> "include" | "exclude"`` 的完整标注。文献键缺失 →
        ``KeyError``；标签非法 → ``ValueError``（均含中文消息）；
        多出的键忽略。
    strategy:
        查询策略 ``max`` / ``uncertainty`` / ``mixed``；未知 → ``ValueError``
        （由 :func:`query_order` 抛出）。
    seed:
        随机种子（``mixed`` 策略生效；默认取 :data:`config.AL_SEED` = 42）。

    返回
    ----
    SimResult
        逐位确定：同输入 + 同种子 → 完全一致的结果。
    """
    return _simulate(articles, gold, strategy, seed, learn=True)


def simulate_random_baseline(
    articles: list[Article], gold: dict[str, str], seed: int = config.AL_SEED,
) -> SimResult:
    """纯随机顺序基线：``mixed`` 策略 + 永远为空的分数字典（不学习）。

    即一次性用种子把全部文献随机洗牌后按该顺序筛完（mixed 空分数的约定
    行为），给出"无模型时 WSS@95/发现位次"的对照点。参数与异常语义与
    :func:`replay` 完全一致。
    """
    return _simulate(articles, gold, "mixed", seed, learn=False)
