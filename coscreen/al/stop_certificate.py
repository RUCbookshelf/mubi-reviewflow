"""召回证书停止规则——S1 全量分析推荐的替代方案。

原理: 从未筛池中随机抽样 m 条,全部人工判读。
如果抽验量足且未发现新的相关文献,则声明"剩余相关文献 ≤ k_t";
该声明的置信度按超几何上界 1 - P(抽验 0 新相关 | 剩余恰为 k_t+1)
如实计算,随已发现相关数与抽验量变化(F-04-01:未筛池 100、抽验 30 时
k_t=0 → 置信度仅 0.30,k_t≥8 即已发现 ≥152 条相关时才 ≥95%),
不再虚标固定的 1−α。

参数(来自 S1 全量优化):
  - R = 0.95  (召回目标: 总召回 ≥ 95%)
  - α = 0.05  (名义显著性水平;证书置信度由 certified_confidence 按
    超几何上界如实计算,不直接使用 α,仅当 k_t 足够大时才 ≥ 1−α)
  - φ = 0.30  (抽样比例: 抽样量 = 30% × 未筛池大小)
  - look = [0.50, 0.70, 0.85, 0.95]  (在已筛进度达到这些百分比时检查)

数学基础: 超几何检验 + Pocock 序贯监测(S1 理论推导,见
``research/notes/stopping_rules.md`` §3;数值自检 ``check_d2_subsample.py``)。

S1 全量结果: 650/650 场模拟中召回保证 100% 达成;
在 3/26 数据集上触发提前停止(节省 11-34%),其余安全退化为全筛。

纯函数模块(SPEC 2026-09-30 §10 不变量 3):
  - ``should_stop()`` 只做计数判断,不执行抽样(抽样是用户手动操作);
  - 不依赖模型分数/排序(S1 发现 AL 排序使 streak 类规则更不安全,
    故本规则刻意与任何模型输出解耦);
  - 仅导入标准库 ``math``,import 时零副作用(不连库、不起服务、不开网络)。
"""

from __future__ import annotations

import math

__all__ = ["RecallCertificate"]


class RecallCertificate:
    """召回证书停止规则。

    参数均为 S1 全量分析的推荐值;构造函数只做赋值,无任何 I/O。
    ``look_fractions`` 记录建议检查进度点(50%/70%/85%/95%),
    是否到达 look 点由调用方(API/前端)根据已筛进度判断,
    本类不负责进度追踪。
    """

    def __init__(
        self,
        recall_target: float = 0.95,     # R
        alpha: float = 0.05,             # α
        sampling_fraction: float = 0.30, # φ
        look_fractions: tuple[float, ...] = (0.50, 0.70, 0.85, 0.95),
    ):
        # MM10-11: 构造器此前无域校验——recall_target=0 使 threshold() 除零、
        # recall_target=1.5 发出"负的可容忍剩余数"证书、alpha=2 /
        # sampling_fraction≤0 或 >1 也能照常签发。这里按参数各自的数学
        # 定义域拒绝（默认参数不受影响）。
        if not 0 < recall_target <= 1:
            raise ValueError(f"recall_target 必须在 (0, 1] 内，得到 {recall_target!r}")
        if not 0 < alpha < 1:
            raise ValueError(f"alpha 必须在 (0, 1) 内，得到 {alpha!r}")
        if not 0 < sampling_fraction <= 1:
            raise ValueError(
                f"sampling_fraction 必须在 (0, 1] 内，得到 {sampling_fraction!r}"
            )
        self.recall_target = recall_target
        self.alpha = alpha
        self.sampling_fraction = sampling_fraction
        self.look_fractions = look_fractions

    def threshold(self, found_relevant: int) -> int:
        """k_t = floor(found × (1-R) / R): 允许的最大剩余相关文献数。

        已发现 ``found_relevant`` 条相关文献时,若剩余未发现的相关
        文献不超过 k_t,则总召回仍 ≥ R(研究实现 ``research/sim/
        stopping_rules.py`` 的 ``k_target(k_mode="recall")`` 同式)。
        ``found_relevant <= 0`` 时返回 0(尚无相关发现,不容忍剩余)。
        """
        if found_relevant <= 0:
            return 0
        return math.floor(found_relevant * (1 - self.recall_target) / self.recall_target)

    def required_sample_size(self, pool_size: int, k_t: int) -> int:
        """m = ceil(pool_size × φ): 需要抽样的最小条数。

        ``k_t`` 为签名兼容保留(抽样量只由池大小与抽样比例决定,
        不随阈值变化)。``pool_size <= 0`` 时返回 0(池空则无需抽样)。
        MM10-11: 结果夹到非负——修前负池给出负抽样量,使
        ``sample_size < required`` 守卫恒成立、证书被直接发放。
        """
        return max(0, math.ceil(pool_size * self.sampling_fraction))

    def certified_confidence(self, pool_size: int, k_t: int, sample_size: int) -> float:
        """证书通过时的真实置信度 = 1 − P(X=0 | K=k_t+1)(超几何上界)。

        X ~ Hypergeometric(N=pool_size, K=k_t+1, n=sample_size):未筛池中
        恰好还剩 ``k_t + 1`` 条相关(声明"剩余 ≤ k_t"的最小违例)时,
        抽验 ``sample_size`` 条却发现 0 条新相关的概率。该违例最难被
        抽验发现,故 P(错误发证) ≤ P(X=0 | K=k_t+1),置信度 = 1 − 该上界,
        随已发现相关数(``k_t``)与实际抽验量变化——并非固定 1−α
        (F-04-01 数值表:pool=100、m=30 时 k_t=0..8 的上界为
        0.7000/0.4879/0.3385/0.2338/0.1608/0.1100/0.0749/0.0507/0.0342,
        即 k_t≥8、已发现 ≥152 条相关时置信度才 ≥0.95)。

        池非正、抽验覆盖全池、或违例在池中放不下(``k_t + 1 > 池 − 抽验``)
        时,0 新相关不可能出自违例池,返回 1.0。
        """
        remaining = k_t + 1
        if pool_size <= 0 or sample_size >= pool_size or remaining > pool_size - sample_size:
            return 1.0
        log_p = math.fsum(
            math.log1p(-sample_size / (pool_size - j)) for j in range(remaining)
        )
        return 1.0 - math.exp(log_p)

    def should_stop(
        self,
        total_records: int,
        screened_count: int,
        found_relevant: int,
        sample_new_relevant: int,  # 抽样中发现的新的相关文献数
        sample_size: int,          # 实际抽样条数
        ) -> dict:
        """判断是否可以停止(纯函数,多次调用同参数结果一致)。

        参数
        ----
        total_records:
            语料总记录数 N。
        screened_count:
            已人工判读的条数。
        found_relevant:
            已发现的相关文献数(用于计算 k_t 阈值)。
        sample_new_relevant:
            本次随机抽验中发现的新的相关文献数(用户人工判读的结果,
            非模型预测)。
        sample_size:
            实际完成判读的抽验条数。

        返回
        ----
        dict
            {
                "stop": bool,
                "confidence": float | None,   # 置信度(超几何上界,随 k_t/抽验量变化;未通过时 None)
                "k_t": int,                   # 允许的剩余相关数
                "message": str,               # 给用户的信息
            }

        语义(S1 推导的三条路径,按序短路):
          1. 抽样量不足(``sample_size < m``)→ 不停止;
          2. 量足且 ``sample_new_relevant == 0`` → 证书通过
             (置信度 = ``certified_confidence`` 如实计算,剩余相关 ≤ k_t
             时总召回 ≥ R;该置信度随 k_t 与抽验量变化,仅当 k_t 足够大
             (如池 100、抽验 30 时 k_t≥8)时才 ≥ 1−α,不虚标);
          3. 否则(量足但抽验发现新相关)→ 不停止,继续筛选。
        """
        k_t = self.threshold(found_relevant)
        pool_size = total_records - screened_count
        required = self.required_sample_size(pool_size, k_t)

        if sample_size < required:
            return {"stop": False, "confidence": None, "k_t": k_t,
                    "message": f"抽样量不足(需 {required},实际 {sample_size})"}

        # 超几何上界: P(剩余相关 > k_t | 抽验仅发现 sample_new_relevant 条)
        # ≤ P(X=0 | K=k_t+1)(最小违例最难被抽验发现),置信度 = 1 − 该上界。
        if sample_new_relevant == 0:
            confidence = self.certified_confidence(pool_size, k_t, sample_size)
            return {"stop": True,
                    "confidence": confidence,
                    "k_t": k_t,
                    "message": (f"证书通过:抽验 {sample_size} 条未见新相关,"
                                f"超几何置信度 {confidence:.1%},"
                                f"总召回 ≥{int(self.recall_target*100)}%(剩余相关 ≤ {k_t} 条)")}

        return {"stop": False,
                "confidence": None,
                "k_t": k_t,
                "message": f"抽样中发现 {sample_new_relevant} 条新的相关文献,不满足停止条件"}
