"""AL 模型预设配置（用户可选，不静默切换）。

每个预设定义： 分类器类型+参数、平衡器+参数、特征提取参数。
预设通过 API 传递 profile 名称，由 ranker 加载对应配置。

参数出处（可复核；r9 b09·F2-17 修正——旧注释引用的 handoff 规格书与
``research/`` 下的验证报告在仓库与全部 git 历史中都不存在，已全部替换）：

- 90 场验证批报告：``docs/methodology/results/al_upgrade_verification/REPORT.md``
  （宏 WSS@95：svm +0.580 / tuned +0.495 / legacy +0.148；6 集中 5 集
  svm 最优，合计筛查量 −71%——见该报告 §1、§3.1）；
- 产品算法参数与行号表：``docs/methodology/sim/README.md`` §1.1–§1.5；
  99 场（90 + 20 场停止规则回归）原始结果与复现脚本同目录。

设计约束：

- **纯数据模块**：只依赖标准库 ``dataclasses``/``types``，不导入任何实现
  模块（features/nb/svm/balancer 均由 :class:`~coscreen.al.ranker.ActiveLearningRanker`
  在选用预设时延迟导入）；import 本模块零副作用——不连库、不起服务、
  不加载 sklearn/numpy；
- **无静默默认变更**：:data:`DEFAULT_PROFILE` = ``"legacy"``
  ——不传 profile 时 :class:`~coscreen.al.ranker.ActiveLearningRanker`
  行为与旧版完全一致；切换到其他预设必须由用户显式操作
  （前端弹确认框、API 显式 PUT）；
- **参数不可变**（r9 b09·F2-14）：``frozen=True`` 只冻结属性重绑定，
  三个 dict 字段在进程内是可变的单例——任何调用方就地修改
  （``PROFILES["tuned"].classifier_params["alpha"] = 999``）都会跨任务
  污染所有后续排序。构造时统一转为 :class:`types.MappingProxyType`
  （只读映射；``**`` 解包与 ``.get`` 照常工作，对下游零改动）；
- **值域在加载期校验**（r9 b09·F2-15）：``classifier`` 只能是
  ``"nb"``/``"svm"``、``balancer`` 只能是 ``None``/``"balanced"``。
  旧实现只在注释里写值域，ranker 用 ``if/else`` 兜底：未知 classifier
  静默落 NB、未知 balancer 静默降级为不平衡——二者都是"静默默认变更"，
  与上面第二条约束直接冲突。现在非法值在构造 :class:`ALProfile` 时即
  ``ValueError``（模块导入时就会触发，不会等到用户选中才炸）；
- **CJK 优势**：所有预设的 ``feature_params`` 都不关闭 ``cjk_bigram``
  （legacy 空参数 = 特征提取器默认 ``True``；cjk 预设显式声明
  ``"cjk_bigram": True``）。注意 ``svm`` 与 ``cjk`` 因此**运行时行为
  完全相同**（r9 b09·F2-16：cjk 只是把默认值又写了一遍）；两条预设的
  description 已如实写明这一点，不再把字符二元组当 cjk 独有卖点。
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType

__all__ = ["ALProfile", "DEFAULT_PROFILE", "PROFILES"]

#: 允许的分类器载体（与 :mod:`coscreen.al.ranker` 的装配分支一一对应）。
_CLASSIFIERS: tuple[str, ...] = ("nb", "svm")

#: 允许的平衡器载体（``None`` = 不做平衡）。
_BALANCERS: tuple[str, ...] = ("balanced",)


def _frozen(params: dict) -> MappingProxyType:
    """把参数字典转成只读映射（b09·F2-14：frozen dataclass 的浅冻结漏洞）。"""
    return MappingProxyType(dict(params))


@dataclass(frozen=True)
class ALProfile:
    """一个 AL 模型预设。

    字段：

    - ``name``：预设标识（``"legacy" | "tuned" | "cjk" | "svm"``），
      API 传递用；
    - ``label``：显示名（如 "经典模式"）；
    - ``classifier``：``"nb"``（手写 MultinomialNB）或 ``"svm"``
      （sklearn LinearSVC 封装，选用时才延迟导入）；
    - ``classifier_params``：``{"alpha": ...}`` 或 ``{"C": ...}``，
      逐关键字传给分类器构造函数（只读映射）；
    - ``balancer``：``None``（不平衡）或 ``"balanced"``
      （:class:`~coscreen.al.balancer.BalancedWeights`）；
    - ``balancer_params``：``{}`` 或 ``{"ratio": ...}``（只读映射）；
    - ``feature_params``：逐关键字传给
      :class:`~coscreen.al.features.TfidfVectorizer` 的参数（只读映射）；
    - ``description``：给用户的一句解释（UI/方法学文档展示）。

    构造即校验（b09·F2-15）：``classifier``/``balancer`` 越界 →
    :class:`ValueError`；三个 dict 字段一律转只读映射（b09·F2-14）。
    """

    name: str  # "legacy" | "tuned" | "cjk" | "svm"
    label: str  # 显示名("经典模式" / "优化模式" / "中文优化" / "SVM 模式")
    classifier: str  # "nb" | "svm"
    classifier_params: dict  # {"alpha": 1.0} 或 {"C": 0.11}
    balancer: str | None  # None | "balanced"
    balancer_params: dict  # {} 或 {"ratio": 9.8}
    feature_params: dict  # {"ngram_range": (1,2), "sublinear_tf": True, ...}
    description: str  # 给用户的一句解释

    def __post_init__(self) -> None:
        if self.classifier not in _CLASSIFIERS:
            raise ValueError(
                f"预设 {self.name!r} 的 classifier 必须是 "
                f"{' / '.join(_CLASSIFIERS)}，得到 {self.classifier!r}"
            )
        if self.balancer is not None and self.balancer not in _BALANCERS:
            raise ValueError(
                f"预设 {self.name!r} 的 balancer 必须是 None 或 "
                f"{' / '.join(_BALANCERS)}，得到 {self.balancer!r}"
            )
        # frozen=True 下普通赋值会抛 FrozenInstanceError，用 object.__setattr__
        # 把 dict 换成只读映射（同一份数据，下游 ``**`` 解包与 ``.get`` 不受影响）。
        object.__setattr__(self, "classifier_params", _frozen(self.classifier_params))
        object.__setattr__(self, "balancer_params", _frozen(self.balancer_params))
        object.__setattr__(self, "feature_params", _frozen(self.feature_params))


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
        # b09·F2-16：cjk_bigram 的默认值就是 True，所以本预设与 svm 的运行时
        # 行为完全相同（同样的向量器四属性、同样的分类器与平衡器）。旧文案
        # 「SVM + CJK 二元组,适合中英混合文献」把默认能力写成独有卖点，已改。
        description="与 SVM 模式运行时完全相同（字符二元组本就是所有预设的默认特征），"
                    "保留该名称用于兼容已选择它的任务。"
    ),
}

# 验证批推荐: svm 配置宏 WSS +0.580,5/6 集胜出
# tuned_nb(+0.495)是无 sklearn 依赖的备选
# legacy 仅保留向后兼容,不推荐新项目使用
DEFAULT_PROFILE = "legacy"  # 不改变现有行为;新建任务时前端可推荐 "svm"
