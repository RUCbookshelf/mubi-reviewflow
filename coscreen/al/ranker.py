"""主动学习排序器（ActiveLearningRanker，见 SPEC.md §12）。

把 coscreen.al 的三个组件串成一条完整流水线：

1. **训练文本**：``" ".join(title, abstract, authors, journal)``（题录四字段
   拼接；缺省字段为空串，不引入魔法词）；
2. **向量化**：:class:`~coscreen.al.features.TfidfVectorizer` 在带标签语料上
   ``fit_transform``，候选语料 ``transform``（词表外 term 忽略）；
3. **打分**：:class:`~coscreen.al.nb.MultinomialNB`（alpha=1.0 Laplace 平滑）
   输出后验 ``P(include | x) ∈ [0, 1]``；
4. **排序**：:func:`~coscreen.al.strategies.query_order` 按
   ``max`` / ``uncertainty`` / ``mixed`` 策略给出确定性队列。

标签约定（SPEC §12）：

- ``decisions`` 为 ``zotero_key -> "include" | "exclude" | "maybe"``；
- 只有 ``include`` / ``exclude`` 参与训练（``include=1``、``exclude=0``）；
- ``maybe`` **不参与训练**，且视为"尚未筛过"——保留在待筛队列里继续排序；
- 可用标签（include+exclude）少于 ``min_labeled`` 时返回 ``None``，
  调用方回退导入顺序（FUNCTIONAL_CHECKLIST "Active learning ranking" 条目 3）。

确定性：给定相同的 ``(articles, decisions, strategy, seed)``，输出
（含 :class:`RankingResult` 的每个字段）完全一致——训练集按 ``articles``
传入顺序构造，``TfidfVectorizer`` 词表按字典序，``mixed`` 策略由
``random.Random(seed)`` 种子化；SVM 预设下 ``LinearSVC`` 的
``random_state`` 亦固定为 ``seed``（镜像 ``research/sim/replay.py`` 的
可复现性决策——liblinear 的随机性只影响坐标顺序，固定种子后逐位可复现）。

模型预设（2026-09-30 AL 模型升级，规格书 §4.2）：``__init__`` 增加可选
``profile`` 参数，传入 :data:`~coscreen.al.profiles.PROFILES` 中的预设名
（legacy/tuned/cjk/svm）时按预设装配组件——特征
:class:`~coscreen.al.features.TfidfVectorizer`(**feature_params)、分类器
（``"svm"`` 延迟导入 :class:`~coscreen.al.svm.SVMClassifier`；``"nb"`` 用
:class:`~coscreen.al.nb.MultinomialNB`(**classifier_params)）、平衡器
（``"balanced"`` 延迟导入 :class:`~coscreen.al.balancer.BalancedWeights`，
其权重经 ``fit(sample_weight=...)`` 同时作用于 NB 的类先验与类内特征和，
即 ASReview v3 Balanced 语义）；``profile=None``（默认）时行为与旧版
**完全一致**（规格书 §10 不变量 1：不装配任何组件，``rank`` 走原有
代码路径，不加载 sklearn/numpy）。

运行时零第三方依赖（仅标准库）；sklearn 仅在 SVM 预设选用时加载
（延迟导入，规格书 §10 不变量 4）、在测试中作基准。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from coscreen import config
from coscreen.al.features import TfidfVectorizer
from coscreen.al.nb import MultinomialNB
from coscreen.al.strategies import query_order
from coscreen.models import Article

__all__ = ["ActiveLearningRanker", "RankingResult"]

#: 参与训练的决策值（maybe 不训练，视为未筛）
_TRAIN_LABELS = ("include", "exclude")


@dataclass
class RankingResult:
    """一次重排的完整结果（含审计所需元数据，见 SPEC.md §12）。"""

    order: list[str]  # 全部待筛键按查询策略排序（候选键的排列）
    scores: dict[str, float]  # zotero_key -> P(include)
    strategy: str  # "max" | "uncertainty" | "mixed"
    seed: int  # 随机种子（mixed 生效；其余策略仅透传记录）
    n_labeled: int  # 参与训练的标签数（include + exclude）
    model_meta: dict = field(default_factory=dict)  # 特征数/alpha/训练分布等


class ActiveLearningRanker:
    """TF-IDF + MultinomialNB + 查询策略的主动学习排序器。

    参数与 :meth:`rank` 的契约见 SPEC.md §12；默认值取自
    :mod:`coscreen.config` 的 AL_* 常量。

    ``profile``（可选，规格书 §4.2）：模型预设名（见
    :data:`~coscreen.al.profiles.PROFILES`）。``None``（默认）= 旧版行为
    （§10 不变量 1）；传入预设名时按预设装配特征/分类器/平衡器——
    ``"svm"``/``"cjk"`` 预设会在构造时加载 sklearn（延迟导入不变量：
    不选用不加载），且 ``LinearSVC`` 的 ``random_state`` 固定为 ``seed``
    （排序逐位可复现）。未知预设名 → ``KeyError``（预设只能显式选择，
    不做静默回退——§0.4 第 4 条）。
    """

    def __init__(
        self,
        strategy: str = config.AL_DEFAULT_STRATEGY,
        seed: int = config.AL_SEED,
        min_labeled: int = config.AL_MIN_LABELED,
        profile: str | None = None,
    ) -> None:
        self.strategy = strategy
        self.seed = int(seed)
        self.min_labeled = int(min_labeled)
        self.profile = profile
        # profile=None 时不装配任何组件（rank 走原有代码路径，行为与旧版
        # 完全一致）；传入预设名时按 §4.2 装配（svm/balancer 延迟导入）。
        self._profile_conf = None
        self._vectorizer = None
        self._classifier = None
        self._balancer = None
        self._classifier_kind: str | None = None
        if profile is not None:
            from coscreen.al.profiles import PROFILES

            p = PROFILES[profile]
            self._profile_conf = p
            self._vectorizer = TfidfVectorizer(**p.feature_params)
            if p.classifier == "svm":
                # 延迟导入：仅 SVM 预设选用时才加载 sklearn（§10 不变量 4）。
                from coscreen.al.svm import SVMClassifier

                # random_state 固定为 seed：liblinear 对偶坐标下降含随机
                # 坐标顺序，固定种子后排序逐位可复现（镜像 replay.py 决策）。
                self._classifier = SVMClassifier(
                    random_state=self.seed, **p.classifier_params
                )
            else:
                self._classifier = MultinomialNB(**p.classifier_params)
            if p.balancer == "balanced":
                # 延迟导入：平衡器仅在其预设选用时加载（numpy 依赖）。
                from coscreen.al.balancer import BalancedWeights

                self._balancer = BalancedWeights(**p.balancer_params)
            else:
                self._balancer = None
            self._classifier_kind = p.classifier

    # ------------------------------------------------------------------
    # 内部：文本组装（题录四字段拼接）
    # ------------------------------------------------------------------

    @staticmethod
    def _text(article: Article) -> str:
        """训练/预测文本：title + abstract + authors + journal 空格拼接。"""
        return " ".join(
            (article.title or "", article.abstract or "",
             article.authors or "", article.journal or "")
        )

    def rank(
        self, articles: list[Article], decisions: dict[str, str]
    ) -> RankingResult | None:
        """对未筛文献排序；可用标签不足 ``min_labeled`` 时返回 ``None``。

        参数
        ----
        articles:
            全部（非重复）文献，按基准顺序（导入顺序）传入；键重复时后者
            覆盖前者（与数据库主键语义一致）。
        decisions:
            ``zotero_key -> "include" | "exclude" | "maybe"``；``maybe``
            不参与训练，仍留在候选队列；键不在 ``articles`` 中时忽略
            （防御：数据库外键下不应发生）。

        返回
        ----
        RankingResult | None
            ``order`` 恰为候选键（未筛 + maybe）在导入顺序基准上的一个排列；
            候选为空时 ``order == []``、``scores == {}``（全筛完的合法终态）。
            未知 ``strategy`` 经 :func:`query_order` 抛 ``ValueError``。
        """
        by_key: dict[str, Article] = {a.zotero_key: a for a in articles}

        # 带标签训练集：按 articles 传入顺序选取，保证训练顺序确定
        # （浮点累加顺序固定 => 结果逐位可复现）。
        train_keys = [
            a.zotero_key for a in articles
            if decisions.get(a.zotero_key) in _TRAIN_LABELS
        ]
        if len(train_keys) < self.min_labeled:
            return None

        # 候选 = 未筛（无决策行）或 maybe（视为尚未筛过，留在队列）
        cand_keys = [
            a.zotero_key for a in articles
            if decisions.get(a.zotero_key) not in _TRAIN_LABELS
        ]

        train_texts = [self._text(by_key[k]) for k in train_keys]
        cand_texts = [self._text(by_key[k]) for k in cand_keys]
        y = [1 if decisions[k] == "include" else 0 for k in train_keys]

        if self._profile_conf is None:
            # 旧版路径（profile=None，§10 不变量 1）：逐行与升级前一致。
            vec = TfidfVectorizer()
            x_train = vec.fit_transform(train_texts)
            nb = MultinomialNB(alpha=1.0)
            nb.fit(x_train, y)
            x_cand = vec.transform(cand_texts)
            scores_list = nb.predict_scores(x_cand)
        else:
            vec = self._vectorizer
            x_train = vec.fit_transform(train_texts)
            weights = (
                self._balancer.compute(y) if self._balancer is not None else None
            )
            # NB 与 SVM 的 fit 均接受 sample_weight=None（无平衡器时直通）；
            # 有平衡器时权重同时作用于 NB 的类先验与类内特征和（sklearn 语义，
            # 即验证批 research/sim/replay.py 的 _nb_fit_score 基准）。
            self._classifier.fit(x_train, y, sample_weight=weights)
            x_cand = vec.transform(cand_texts)
            if self._classifier_kind == "svm":
                scores_list = self._classifier.predict_proba(x_cand)
            else:
                scores_list = self._classifier.predict_scores(x_cand)
        scores = dict(zip(cand_keys, scores_list))

        n_include = sum(1 for label in y if label == 1)
        n_exclude = len(y) - n_include
        if self._profile_conf is None:
            model_meta = {
                "n_features": len(vec.vocab),
                "n_include": n_include,
                "n_exclude": n_exclude,
                "alpha": 1.0,
            }
        else:
            p = self._profile_conf
            model_meta = {
                "n_features": len(vec.vocab),
                "n_include": n_include,
                "n_exclude": n_exclude,
                "profile": p.name,
                "classifier": p.classifier,
                **p.classifier_params,
                "balancer": p.balancer,
                "balance_ratio": (
                    p.balancer_params.get("ratio") if p.balancer else None
                ),
            }

        order = query_order(cand_keys, scores, self.strategy, self.seed)
        return RankingResult(
            order=order,
            scores=scores,
            strategy=self.strategy,
            seed=self.seed,
            n_labeled=len(train_keys),
            model_meta=model_meta,
        )
