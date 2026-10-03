"""AL 模型预设配置（用户可选，不静默切换）。

每个预设定义： 分类器类型+参数、平衡器+参数、特征提取参数。
预设通过 API 传递 profile 名称，由 ranker 加载对应配置。

设计约束（规格书 ``docs/handoff/2026-09-30-al-model-upgrade-spec.md``
§0.4/§10）：

- **纯数据模块**：只依赖标准库 ``dataclasses``，不导入任何实现模块
  （features/nb/svm/balancer 均由 :class:`~coscreen.al.ranker.ActiveLearningRanker`
  在选用预设时延迟导入）；import 本模块零副作用——不连库、不起服务、
  不加载 sklearn/numpy（§10 不变量 4 的前提）；
- **无静默默认变更**（§0.4 第 4 条）：:data:`DEFAULT_PROFILE` = ``"legacy"``
  ——不传 profile 时 :class:`~coscreen.al.ranker.ActiveLearningRanker`
  行为与旧版完全一致（§10 不变量 1）；切换到其他预设必须由用户显式操作
  （前端弹确认框、API 显式 PUT）；
- **参数出处**：``tuned``/``svm`` 的 TF-IDF ``(1, 2)``-gram +
  ``sublinear_tf`` + ``max_df=0.95``、NB ``alpha=3.822``、SVM ``C=0.11``、
  平衡 ``ratio=9.8`` 均来自 ASReview v3 Optuna 在 SYNERGY 全集的调优
  结果（ELAS u3/u4 配置），并经本项目 90 场验证批复核（宏 WSS@95：
  svm +0.580 / tuned +0.495 / legacy +0.148，见
  ``research/results/al_upgrade_verification/REPORT.md``）；
- **CJK 优势保留**（§0.4 第 5 条 / §10 不变量 5）：所有预设的
  ``feature_params`` 都不关闭 ``cjk_bigram``（legacy 空参数 = 特征
  提取器默认 ``True``；cjk 预设显式声明 ``"cjk_bigram": True``）。
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["ALProfile", "DEFAULT_PROFILE", "PROFILES"]


@dataclass(frozen=True)
class ALProfile:
    """一个 AL 模型预设。

    字段（规格书 §4.1）：

    - ``name``：预设标识（``"legacy" | "tuned" | "cjk" | "svm"``），
      API 传递用；
    - ``label``：显示名（如 "经典模式"）；
    - ``classifier``：``"nb"``（手写 MultinomialNB）或 ``"svm"``
      （sklearn LinearSVC 封装，选用时才延迟导入）；
    - ``classifier_params``：``{"alpha": ...}`` 或 ``{"C": ...}``，
      逐关键字传给分类器构造函数；
    - ``balancer``：``None``（不平衡）或 ``"balanced"``
      （:class:`~coscreen.al.balancer.BalancedWeights`）；
    - ``balancer_params``：``{}`` 或 ``{"ratio": ...}``；
    - ``feature_params``：逐关键字传给
      :class:`~coscreen.al.features.TfidfVectorizer` 的参数；
    - ``description``：给用户的一句解释（UI/方法学文档展示）。
    """

    name: str  # "legacy" | "tuned" | "cjk" | "svm"
    label: str  # 显示名("经典模式" / "优化模式" / "中文优化" / "SVM 模式")
    classifier: str  # "nb" | "svm"
    classifier_params: dict  # {"alpha": 1.0} 或 {"C": 0.11}
    balancer: str | None  # None | "balanced"
    balancer_params: dict  # {} 或 {"ratio": 9.8}
    feature_params: dict  # {"ngram_range": (1,2), "sublinear_tf": True, ...}
    description: str  # 给用户的一句解释


PROFILES: dict[str, ALProfile] = {
    "legacy": ALProfile(
        name="legacy", label="经典模式",
        classifier="nb", classifier_params={"alpha": 1.0},
        balancer=None, balancer_params={},
        feature_params={},
        description="与旧版本行为完全一致。注意:低患病率(<1%)场景下性能较差(WSS 可能为负)。"
    ),
    "tuned": ALProfile(
        name="tuned", label="优化模式(推荐)",
        classifier="nb", classifier_params={"alpha": 3.822},
        balancer="balanced", balancer_params={"ratio": 9.8},
        feature_params={"ngram_range": (1, 2), "sublinear_tf": True, "max_df": 0.95},
        description="参数在 SYNERGY 基准优化,低患病率显著改善(WSS +0.148→+0.495)。"
    ),
    "svm": ALProfile(
        name="svm", label="SVM 模式(最优)",
        classifier="svm", classifier_params={"C": 0.11},
        balancer="balanced", balancer_params={"ratio": 9.8},
        feature_params={"ngram_range": (1, 2), "sublinear_tf": True, "max_df": 0.95},
        description="线性 SVM(ASReview v3 默认),验证批宏 WSS +0.580,6 集中 5 集最优,筛查量减少 71%。"
    ),
    "cjk": ALProfile(
        name="cjk", label="中文优化模式",
        classifier="svm", classifier_params={"C": 0.11},
        balancer="balanced", balancer_params={"ratio": 9.8},
        feature_params={"ngram_range": (1, 2), "sublinear_tf": True, "max_df": 0.95,
                       "cjk_bigram": True},
        description="SVM + CJK 二元组,适合中英混合文献。"
    ),
}

# 验证批推荐: svm 配置宏 WSS +0.580,5/6 集胜出
# tuned_nb(+0.495)是无 sklearn 依赖的备选
# legacy 仅保留向后兼容,不推荐新项目使用
DEFAULT_PROFILE = "legacy"  # 不改变现有行为;新建任务时前端可推荐 "svm"
