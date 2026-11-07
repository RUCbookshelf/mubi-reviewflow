"""r8 FL1 数值缓修池钉子：族A 极端数值守卫（15 条）。

覆盖编号（inventory_缓修池72条.md 逐条表，证据：out/external_reviews/
luna2_verify_num.md、luna2_verify_api.md、final_verify_v1.md §4、
mm11_l4_verify_mm.md §MM11-07、glm_verify_v2 §10、minimax_full_audit_10.md
§MM10-08..13）：F-02、F-03、F-04、F-05、F-06、F-07、MM10-04、MM11-07、
B08-N4、MM10-08*、MM10-09*、MM10-10*、MM10-11*、MM10-12*、MM10-13*。
带 * 的六条无独立复审判定——本轮修复前已先在本工作树逐条独立复现
（见各 test 内的"修前实测"注释；数值与原始证据一致）。

破坏即红（R4）：每个测试类的第一个测试断言修复后的行为，基线 a080fde
上必红；标"常规对照"的测试是逐位不变钉子（基线绿、修复后必须仍绿），
钉住 R2/R1：不新增拒绝合法输入、常规输入结果逐位不变。

修法（族A 一把刀）：
- 输入有限性/退化域守卫（参照 review_analysis._screened_inverse_variance_weights
  模式）：F-05（次正规 c/方差）、F-06（SQLite INTEGER 上限）、MM10-10*（NaN
  权重）、MM10-11*（构造器域）、MM10-13*（sd_pre² 下溢）、MM11-07（df 上界
  对齐数值能力）；
- 相消处整数恒等式改写（保留原浮点表达式，仅当相消误差 > 1e-6 相对时切换到
  精确整数形式，常规输入路径逐位不变）：F-03（n(b+c)-(b-c)^2 = (a+d)(b+c)+4bc）、
  F-04（1/e-1/n = (n-e)/(e·n)）；
- except 扩容转 4xx：F-05（brentq 的 RuntimeError/OverflowError）、F-06
  （SQLite OverflowError → 域校验 ValueError / pydantic 422）、MM10-13*
  （ZeroDivisionError 分母预检）；
- 其余：F-02（isclose abs_tol=0）、F-07（max(0,·)）、MM10-04（噪声底行搜索
  回退 + 文案）、B08-N4（bool 计数拒绝）、MM10-08*（CR0/CR1 反保守性量化
  warning）、MM10-09*（reml_log_likelihood 口径；r13 终扫 stats-a-0 更正参考式
  后重钉，旧钉→新钉见该测试注释）、
  MM10-12*（反向重复对比 warning——不用 ValueError：反向串+取反估计是合法
  输入，拒绝即违反 R2，见 test 内说明）。
"""
import math
import os
import sys
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from coscreen import (bayesian_robust_meta, cluster_trial_effects, cluster_trial_variable_size,
                      dependent_synthesis, meta_regression_robust, network_analysis,
                      network_gls, network_random, paired_binary_effect, paired_rd_interval,
                      review_analysis, single_group_standardized_change, tau_q_profile_interval)
from coscreen import dta_analysis
from coscreen.al import nb as al_nb
from coscreen.al import stop_certificate

_SQLITE_MAX = 2**63 - 1
_MIN_NORMAL = 2.2250738585072014e-308  # sys.float_info.min


def _task_db_with_articles(prefix: str) -> str:
    """带 articles/review_* 表与一篇文献（R1）的任务库路径。"""
    from coscreen import db as db_mod
    from coscreen.db import _SCHEMA, _connect
    from coscreen.models import Article

    path = os.path.join(tempfile.mkdtemp(prefix=prefix), "task.db")
    with _connect(path) as conn:
        conn.executescript(_SCHEMA)
    db_mod.upsert_articles(path, [Article(zotero_key="R1", title="Ref one")], [])
    return path


def _dependent_fixture(se: float, diagonal: float):
    """两研究 × 两结局（MD）的 dependent_synthesis 输入。"""
    effects, blocks, rid = [], [], 0
    for sid, ex, ey in (("S1", 0.0, 1.0), ("S2", 0.2, 1.1)):
        ids = []
        for outcome, est in (("x", ex), ("y", ey)):
            rid += 1
            ids.append(rid)
            effects.append({"result_id": rid, "study_id": sid, "comparison": "A vs B",
                            "outcome": outcome, "timepoint": "t1", "measure": "MD",
                            "estimate": est, "se": se, "selected": 1,
                            "source_key": "R1", "source_locator": "p1"})
        blocks.append({"study_id": sid, "result_ids": ids, "covariance_source_note": "analyst",
                       "covariance": [[diagonal, 0.0], [0.0, diagonal]]})
    return effects, blocks


def _cluster_rr(participants: int, events: int, icc: float = 0.5) -> dict:
    return {"outcome_type": "binary", "measure": "RR", "icc": icc,
            "analysis_status": "unadjusted",
            "arms": {"treatment": {"clusters": 2, "participants": participants, "events": events},
                     "control": {"clusters": 2, "participants": participants, "events": events}}}


# 修前实测（基线 a080fde，本工作树独立复现）：diag=1e-14 vs var=1e-20 被接受，
# 返回 SE 7.071067811865476e-08 = 声明值隐含 SE 的 1000.0000000000001 倍。
class F02DependentSynthesisToleranceTests(unittest.TestCase):
    """F-02：固定 abs_tol=1e-14 在微小方差下吞掉相对失配（SE 可偏 1000 倍）。"""

    def test_tiny_variance_mismatch_is_rejected(self):
        effects, blocks = _dependent_fixture(se=1e-10, diagonal=1e-14)
        with self.assertRaises(ValueError, msg="diag 1e-14 vs var 1e-20（相对失配 1e6 倍）不得再被接受"):
            dependent_synthesis.synthesize_dependent_effects(effects, blocks)

    def test_regular_variance_contract_unchanged(self):
        """常规对照（基线绿，修复后必须仍绿）：匹配接受、真实失配照常拒绝、值逐位不变。"""
        effects, blocks = _dependent_fixture(se=0.1, diagonal=0.1 ** 2)
        result = dependent_synthesis.synthesize_dependent_effects(effects, blocks)
        self.assertEqual([e["se"] for e in result["estimates"]],
                         [0.07071067811865477, 0.07071067811865477])
        effects2, blocks2 = _dependent_fixture(se=0.1, diagonal=0.02)
        with self.assertRaises(ValueError):
            dependent_synthesis.synthesize_dependent_effects(effects2, blocks2)
        effects3, blocks3 = _dependent_fixture(se=0.1, diagonal=0.011)
        with self.assertRaises(ValueError):
            dependent_synthesis.synthesize_dependent_effects(effects3, blocks3)


# 修前实测：{a:1,b:2**63-3,c:1,d:0} 被"方差为零"误拒（fsum 项 0.0，精确值 5.0）；
# paired_rd_interval {a:1,b:2**53-1,c:2,d:0}（路由上限内）Wald 方差低估 11.1%。
class F03PairedRdIntegerIdentityTests(unittest.TestCase):
    """F-03：配对 RD Wald 方差在大计数浮点相消——整数恒等式改写。"""

    def test_2p63_table_no_longer_falsely_rejected(self):
        a, b, c, d = 1, 2**63 - 3, 1, 0
        result = paired_binary_effect.paired_binary_effect(
            {"a": a, "b": b, "c": c, "d": d}, direction="treatment_minus_control")
        n = a + b + c + d
        exact_var = float(Fraction((a + d) * (b + c) + 4 * b * c, 1) / n**3)
        self.assertLess(abs(result["rd_se"] - math.sqrt(exact_var)) / math.sqrt(exact_var), 1e-12,
                        msg="rd_se 必须等于恒等式 (a+d)(b+c)+4bc 精确方差的开方")

    def test_in_cap_variance_no_longer_underestimated(self):
        """路由上限内的等价形态（b=2**53-1, c=2；修前 fsum 项 8.0 vs 精确
        8.999999999999996，方差低估 11.1%）：paired_binary_effect 导出的
        rd_se 必须精确。（paired_rd_interval 的 CI 界在 ~1.0 处按 ULP 量化，
        该修正不移动其任何输出数字——其 _wald_limits 的同型修复由
        test_variance_term_helper_is_shared 钉住。）"""
        a, b, c, d = 1, 2**53 - 1, 2, 0
        n = a + b + c + d
        exact_var = float(Fraction((a + d) * (b + c) + 4 * b * c, 1) / n**3)
        result = paired_binary_effect.paired_binary_effect(
            {"a": a, "b": b, "c": c, "d": d}, direction="treatment_minus_control")
        self.assertLess(abs(result["rd_se"] - math.sqrt(exact_var)) / math.sqrt(exact_var),
                        1e-12, msg=f"rd_se {result['rd_se']!r} 必须匹配精确方差（修前低估 5.7% 的 SE）")

    def test_variance_term_helper_is_shared(self):
        """两个模块的方差项助手都必须存在并给出恒等式精确值/常规逐位值。"""
        for module in (paired_binary_effect, paired_rd_interval):
            helper = getattr(module, "_rd_variance_term", None)
            self.assertIsNotNone(helper, msg=f"{module.__name__} 缺少 _rd_variance_term")
            # 大计数：恒等式精确项（修前浮点式此处为 0.0/8.0）
            a, b, c, d = 1, 2**63 - 3, 1, 0
            n = a + b + c + d
            exact_term = float(Fraction((a + d) * (b + c) + 4 * b * c, 1) / n)
            self.assertLess(abs(helper(a, b, c, d, n) - exact_term) / exact_term, 1e-12)
            # 常规表：必须逐位等于历史浮点表达式（R2 逐位不变钉子）
            self.assertEqual(helper(10, 15, 5, 20, 50),
                             math.fsum((20.0, -(10 ** 2) / 50)))
            self.assertEqual(helper(0, 40, 2, 8, 50),
                             math.fsum((42.0, -(38 ** 2) / 50)))

    def test_regular_tables_bit_identical(self):
        """常规对照（基线绿，修复后必须仍绿）：常规四格表 rd_se 逐位不变。"""
        for cells, expected in (({"a": 10, "b": 15, "c": 5, "d": 20}, 0.08485281374238571),
                                ({"a": 0, "b": 40, "c": 2, "d": 8}, 0.07244308110509934)):
            result = paired_binary_effect.paired_binary_effect(
                cells, direction="treatment_minus_control")
            self.assertEqual(result["rd_se"], expected, msg=str(cells))
        ci = paired_rd_interval.paired_rd_interval(10, 15, 5, 20, method="wald")
        self.assertEqual(ci["ci"], [0.033691541078038706, 0.3663084589219613])


# 修前实测：participants=events=两臂 2**63-1/2**63-2（RR、icc=0.5）→
# "inputs must imply a positive finite SE"（浮点倒数差相消为 0，精确值
# 1.1754943508222875e-38）；participants=2**53 → SE 静默偏大 41.4%。
class F04ClusterReciprocalIdentityTests(unittest.TestCase):
    """F-04：整群 RR 方差倒数差相消——(n-e)/(e·n) 整数恒等式改写。"""

    def test_2p63_positive_variance_accepted(self):
        p, e = 2**63 - 1, 2**63 - 2
        result = cluster_trial_effects.calculate_cluster_trial_effect(_cluster_rr(p, e))
        de = 1 + (p / 2 - 1) * 0.5
        exact_se = math.sqrt(de * 2 * ((p - e) / (e * p)))
        self.assertLess(abs(result["se"] - exact_se) / exact_se, 1e-12)

    def test_2p53_silent_se_inflation_gone(self):
        p, e = 2**53, 2**53 - 1
        result = cluster_trial_effects.calculate_cluster_trial_effect(_cluster_rr(p, e))
        de = 1 + (p / 2 - 1) * 0.5
        exact_se = math.sqrt(de * 2 * ((p - e) / (e * p)))
        self.assertLess(abs(result["se"] - exact_se) / exact_se, 1e-6,
                        msg="修前此处 SE 静默偏大 41.4%（浮点倒数差是精确差的 2 倍）")

    def test_variable_size_entry_fixed_too(self):
        """cluster_trial_variable_size 的同型重复表达式（:121-123）一并修复。"""
        values = _cluster_rr(2**63 - 1, 2**63 - 2)
        values["icc_source"] = "citation"
        for arm in values["arms"].values():
            arm["cv"] = 0.3
        result = cluster_trial_variable_size.calculate_cluster_trial_variable_size_effect(values)
        self.assertTrue(math.isfinite(result["se"]) and result["se"] > 0)

    def test_regular_cluster_trials_bit_identical(self):
        """常规对照（基线绿，修复后必须仍绿）：常规整群试验 SE 逐位不变。"""
        for p, e, icc, expected in ((2000, 1900, 0.05, 0.051783964191324895),
                                    (200, 100, 0.1, 0.3301514803843836),
                                    (40, 20, 0.02, 0.2626785107312739)):
            result = cluster_trial_effects.calculate_cluster_trial_effect(_cluster_rr(p, e, icc))
            self.assertEqual(result["se"], expected, msg=f"p={p} e={e}")
        exact = cluster_trial_effects.calculate_cluster_trial_effect(_cluster_rr(2**52, 2**52 - 1))
        self.assertEqual(exact["se"], 1.053671212772351e-08)


# 修前实测：tau_q_profile_interval(3.0, 2, c_constant=5e-324) 返回
# {tau2: inf, h2: nan, converged: True, upper_unbounded: False}；
# q_profile_tau2_interval：v=1e-300/1e-307 → RuntimeError（绕过 except
# ValueError）、v=1e-308 → OverflowError、v=5e-324 → NaN solver ValueError。
class F05SubnormalTauQGuardTests(unittest.TestCase):
    """F-05：次正规 tau/Q 输入的退化域守卫 + except 扩容转 4xx。"""

    def test_subnormal_c_constant_rejected(self):
        with self.assertRaises(ValueError, msg="次正规 c_constant 不得再返回 tau2=inf/converged=True"):
            tau_q_profile_interval.tau_q_profile_interval(3.0, 2, c_constant=5e-324)

    def test_q_profile_non_valueerror_escapes_become_valueerror(self):
        for variances in ([1e-300, 1e-300], [1e-307, 1e-307], [1e-308, 1e-308]):
            with self.subTest(v=variances[0]):
                with self.assertRaises(ValueError,
                                       msg="RuntimeError/OverflowError 必须转为 ValueError（4xx 契约）"):
                    tau_q_profile_interval.q_profile_tau2_interval([0.0, 1.0], variances)

    def test_subnormal_variance_rejected_upfront(self):
        with self.assertRaises(ValueError) as ctx:
            tau_q_profile_interval.q_profile_tau2_interval([0.0, 1.0], [5e-324, 5e-324])
        self.assertIn("supported numeric range", str(ctx.exception),
                      msg="修前是迟到且误导的 NaN solver 消息；必须在入口以上界拒绝")

    def test_regular_and_small_normal_inputs_unchanged(self):
        """常规对照（基线绿，修复后必须仍绿）：c=1.0 与 c=1e-307 结果逐位不变。"""
        result = tau_q_profile_interval.tau_q_profile_interval(3.0, 2, c_constant=1.0)
        self.assertEqual(result["tau2"], 2.0)
        self.assertEqual(result["i2"], 66.66666666666667)
        self.assertEqual(result["h2"], 3.0)
        self.assertEqual(result["tau2_ci"], (0.0, 13.630969544914821))
        tiny = tau_q_profile_interval.tau_q_profile_interval(3.0, 2, c_constant=1e-307)
        self.assertTrue(math.isfinite(tiny["tau2"]) and math.isfinite(tiny["h2"]))
        qp = tau_q_profile_interval.q_profile_tau2_interval([0, 1], [0.01, 0.01])
        self.assertEqual(qp["upper"], 509.1191348598337)
        self.assertTrue(qp["converged"])


# 修前实测：域层 save_result(tp=2**63) → sqlite3 OverflowError（裸 500）；
# 提取入口 _dta_count({'tp': 2**63}) 放行 → INSERT 500；2**63-1 提取口径
# 因 float(2**63-1) != 2**63-1 被误判"非整数"（400），与主路径 200 不一致。
class F06DtaSqliteIntegerBoundTests(unittest.TestCase):
    """F-06：DTA 计数 0..2**63-1 双入口边界（域层 + DtaResultIn + _dta_count）。"""

    def test_domain_rejects_above_sqlite_integer_max(self):
        db = _task_db_with_articles("rf_r8_f06_")
        review_analysis.save_study(db, "S1", "Study 1", ["R1"])
        with self.assertRaises(ValueError, msg="tp=2**63 必须以 ValueError（4xx）拒绝，而非 OverflowError 500"):
            dta_analysis.save_result(db, "S1", "IT", "TC", "TH", "RS", 2**63, 1, 1, 1, "R1")

    def test_domain_accepts_sqlite_integer_max(self):
        db = _task_db_with_articles("rf_r8_f06b_")
        review_analysis.save_study(db, "S1", "Study 1", ["R1"])
        dta_analysis.save_result(db, "S1", "IT", "TC", "TH", "RS",
                                 _SQLITE_MAX, 1, 1, 1, "R1")  # 修前亦通过（常规对照）

    def test_dta_count_extraction_caliber_unified(self):
        """提取入口 _dta_count：上界拒绝 + 整数 2**63-1 放行（修前被 float 舍入误拒）。"""
        from custom_backend import main as backend_main

        with self.assertRaises(ValueError):
            backend_main._dta_count({"tp": 2**63}, "tp")
        with self.assertRaises(ValueError):
            backend_main._dta_count({"tp": float(2**63)}, "tp")  # 恰为 2**63 的 float 也不得穿过
        self.assertEqual(backend_main._dta_count({"tp": _SQLITE_MAX}, "tp"), _SQLITE_MAX)
        self.assertEqual(backend_main._dta_count({"tp": 5.0}, "tp"), 5)  # 整数值 float 维持原语义

    def test_dta_count_negative_and_overflow_messages_distinct(self):
        """负值与越上界给不同文案（都仍是 ValueError → 400，契约不变）。"""
        from custom_backend import main as backend_main

        with self.assertRaises(ValueError) as negative:
            backend_main._dta_count({"tp": -1}, "tp")
        with self.assertRaises(ValueError) as overflow:
            backend_main._dta_count({"tp": 2**63}, "tp")
        self.assertIn("非负整数", str(negative.exception))
        self.assertIn("上限", str(overflow.exception))
        self.assertNotEqual(str(negative.exception), str(overflow.exception))


# 修前实测：{a:0,b:1,c:1,d:0} → continuity 0.5（应 0）；{a:5,b:3,c:3,d:5} →
# 0.1666…（应 0）。触发域为 |b-c|=0（复审更正：|b-c|=1 时 (1-1)^2=0 本就正确）。
class F07McNemarContinuityFloorTests(unittest.TestCase):
    """F-07：连续性校正 McNemar 在 b=c 时统计量必须为 0——max(0,·)。"""

    def test_b_equals_c_continuity_is_zero(self):
        for cells in ({"a": 0, "b": 1, "c": 1, "d": 0}, {"a": 5, "b": 3, "c": 3, "d": 5},
                      {"a": 0, "b": 30, "c": 30, "d": 0}):
            result = paired_binary_effect.paired_binary_effect(
                cells, direction="treatment_minus_control")
            self.assertEqual(result["mcnemar_chi2_continuity_descriptive_only"], 0.0, msg=str(cells))

    def test_other_deltas_bit_identical(self):
        """常规对照（基线绿，修复后必须仍绿）：|b-c|≥1 的取值逐位不变。"""
        r1 = paired_binary_effect.paired_binary_effect(
            {"a": 10, "b": 20, "c": 5, "d": 10}, direction="treatment_minus_control")
        self.assertEqual(r1["mcnemar_chi2_descriptive_only"], 9.0)
        self.assertEqual(r1["mcnemar_chi2_continuity_descriptive_only"], 7.84)
        r2 = paired_binary_effect.paired_binary_effect(
            {"a": 10, "b": 5, "c": 10, "d": 10}, direction="treatment_minus_control")
        self.assertEqual(r2["mcnemar_chi2_continuity_descriptive_only"], 1.0666666666666667)


# 修前实测（同 final_verify_v1 §4.3(b) hit-1 口径重建）：center=0.0 →
# ValueError "…did not converge; separation or weak identification…"，
# center=1.0/1.5/2.0/2.5 → log_hr=0.614788460993484{2..8}（相互一致到 ~4e-16）。
class MM1004IpdCoxNoiseFloorTests(unittest.TestCase):
    """MM10-04：绝对容差伪不收敛——行搜索在似然自身舍入噪声下停滞。"""

    ROWS = [
        {"study_id": "A", "duration": 2, "event": 1, "treatment": 1, "covariate": 2.5},
        {"study_id": "A", "duration": 3, "event": 1, "treatment": 0, "covariate": 1.5},
        {"study_id": "A", "duration": 3, "event": 1, "treatment": 0, "covariate": 2.0},
        {"study_id": "A", "duration": 4, "event": 1, "treatment": 1, "covariate": 0.5},
        {"study_id": "A", "duration": 5, "event": 1, "treatment": 0, "covariate": 1.0},
        {"study_id": "B", "duration": 5, "event": 0, "treatment": 1, "covariate": 1.0},
        {"study_id": "B", "duration": 6, "event": 1, "treatment": 0, "covariate": 2.0},
        {"study_id": "B", "duration": 6, "event": 0, "treatment": 0, "covariate": 0.5},
        {"study_id": "B", "duration": 7, "event": 1, "treatment": 1, "covariate": 3.0},
        {"study_id": "B", "duration": 8, "event": 0, "treatment": 1, "covariate": 1.5},
        {"study_id": "B", "duration": 9, "event": 0, "treatment": 0, "covariate": 2.5},
    ]

    def _fit(self, center):
        from coscreen import ipd_adjusted_cox

        return ipd_adjusted_cox.fit_adjusted_stratified_cox(
            self.ROWS, {"A": "R1", "B": "R2"}, covariate_center=center, covariate_scale=1.0)

    def test_center_zero_converges_to_same_fit(self):
        zero = self._fit(0.0)
        same = self._fit(1.5)
        self.assertLess(abs(zero["log_hr"] - same["log_hr"]), 1e-9,
                        msg="center=0 与 center=1.5 是同一拟合的不同参数化，log_hr 必须一致")
        self.assertLess(abs(zero["se"] - same["se"]), 1e-9)

    def test_regular_centers_bit_identical(self):
        """常规对照（基线绿，修复后必须仍绿）：center=1.0..2.5 结果逐位不变。

        修复采用"null 步不算接受 + 噪声底全 Newton 步回退"，仅在基线本就
        全部试探步被拒/停滞的输入上改变路径；400 组随机夹具上 377/377 个
        基线可解夹具逐位一致（见修复说明）。
        """
        for center, log_hr, se in ((1.0, 0.6147884609934847, 0.9839101341284368),
                                   (1.5, 0.6147884609934842, 0.9839101341284366),
                                   (2.0, 0.6147884609934844, 0.9839101341284366),
                                   (2.5, 0.6147884609934848, 0.9839101341284368)):
            result = self._fit(center)
            self.assertEqual((result["log_hr"], result["se"]), (log_hr, se), msg=f"center={center}")


# 修前实测：本夹具 df=3..3e9 全部成功（5e8/1e9/2e9/3e9 的 mu.mean 与
# normal 家族极限 0.35928725061352246 相差 ≤3e-9），df=5e9 起 "grid
# refinement did not converge"；df=1e12+1 触发 "too large" 入口拒绝。
# 即：失败点是数据相关的（证据夹具 1e12 才败），上界不该用来假装精确边界。
class MM1107BayesianDfCapTests(unittest.TestCase):
    """MM11-07：df 接近上界时的失败必须可行动，且不得做错误归因。

    三轮的教训都写在这里（第三轮钉的是作用域）：
    - 第一轮把 _MAX_PRIOR_DF 降到 1e8 = R2 违规（基线在本夹具上 df=5e8…3e9
      全部成功且 mu.mean 与 normal 家族极限相差 ≤3e-9，被 1e8 全部误拒）；
    - 第二轮把文案绑成「the Student-t prior is numerically equivalent to a
      normal prior … use the normal family」，在 k=2 反例（y=[-0.57,-0.54]、
      se=[0.24,0.14]）上被证伪：df=1e8 成功、df=1e6/1e7/1e9/1e12 都失败，
      normal 家族在同一数据上也失败；
    - 第三轮把建议的作用域放大到所有 student_t 失败（无 df 下限），于是
      普通用户会选的 df=3/5/10/30/100 上也发出「试更小的 df / 用 normal
      家族」，而这批输入下两个旋钮都无效——那是新增误导（medium）。
    现行修法：df < _DF_DIAGNOSTIC_FLOOR 的失败一律给中性诊断且不付探针
    成本；df 达阈值时才在同一批 rows 上实测候选（更小 df、normal 家族），
    只有实测收敛的候选才会写进文案。因此「建议」必然是「在同一数据上
    成立的设置」，而不只是「看起来相关的旋钮」。
    """

    ROWS = [{"study_id": f"S{i+1}", "measure": "MD", "estimate": 0.3 + 0.02 * i,
             "se": 0.2 + 0.01 * i} for i in range(8)]

    def _fit(self, df, rows=None):
        return bayesian_robust_meta.fit_bayesian_robust_meta(
            rows or self.ROWS, mu_prior_mean=0.0, mu_prior_family="student_t",
            mu_prior_scale=1.0, mu_prior_df=df, tau_prior_family="half_normal",
            tau_prior_scale=0.5, tau_prior_df=None)

    def test_refinement_failure_is_actionable_and_unattributed(self):
        """df=1e12（上界内、本夹具必败）：文案给出 df/家族两个旋钮，不做归因。"""
        with self.assertRaises(ValueError) as ctx:
            self._fit(1e12)
        message = str(ctx.exception)
        self.assertIn("grid refinement did not converge", message)
        self.assertIn("df=1e+12", message, msg="必须点出失败的先验参数")
        self.assertIn("smaller df", message, msg="必须给出可行动建议")
        self.assertIn("normal family", message)
        self.assertNotIn("equivalent to a normal prior", message,
                         msg="第二轮的错误归因已被反例证伪，不得回归")
        self.assertIn("not by itself evidence", message,
                      msg="必须说明这不等于分离/弱识别，避免用户去删数据")

    def test_regular_df_failures_get_neutral_diagnostic(self):
        """第三轮钉子：df=3/5/10/30/100 的网格失败不得发出 df/家族建议。

        评审实测：在 k=2 反例上这批 df 全部失败，更小 df 全失败、normal
        家族也失败，两个旋钮都无效。文案必须中性，且不许出现「smaller
        df」「normal family」这类在数据上站不住的引导。
        """
        rows = [{"study_id": "S1", "measure": "MD", "estimate": -0.57, "se": 0.24},
                {"study_id": "S2", "measure": "MD", "estimate": -0.54, "se": 0.14}]
        for df in (3.0, 5.0, 10.0, 30.0, 100.0):
            with self.subTest(df=df):
                with self.assertRaises(ValueError) as ctx:
                    self._fit(df, rows=rows)
                message = str(ctx.exception)
                self.assertIn("grid refinement did not converge", message)
                self.assertIn(f"df={df:g}", message, msg="失败仍须点名先验参数")
                self.assertNotIn("smaller df", message,
                                 msg=f"df={df:g} 上更小 df 实测无效，不得建议")
                self.assertNotIn("normal family", message,
                                 msg=f"df={df:g} 上 normal 家族实测同样失败，不得建议")
                self.assertIn("not attributed", message,
                              msg="普通 df 的失败必须明确不做 df/家族归因")
                self.assertIn("not by itself evidence", message,
                              msg="中性的同时仍须说明这不等于分离/弱识别")

    def test_diagnostic_does_not_contradict_its_own_advice(self):
        """评审要求的自洽钉子：文案里被推荐的替代方案必须在同一数据上成立。

        反例夹具上 df=1e6/1e7/1e9/1e12 失败（只有 df=1e8 因数值巧合成功），
        因此这里任何「use the normal family」式断言都站不住。第三轮之后的
        契约更强：不仅不得做矛盾归因，而且在这批 rows 上实测不到收敛候选时，
        文案根本不给「smaller df」建议——只陈述「没试出更小的 df 能收敛」。

        r9/l5a 更新（F2-61，升级授权 dwfq 同批更新先例）：本测试原本还断言
        「夹具前提：normal 家族在该数据上失败」。该前提本身是缺陷产物——
        HEAD 上 df=1e6/1e12 的失败来自 ``axis_panels`` 把越界尾 panel 以
        a>b 反向追加（负面积因子），normal 家族在这批 rows 上同样被同一反向
        panel 拖死。修掉反向 panel 后 normal 家族收敛，1-D 稠密参考积分
        （u 步长 0.005）给出 mu=-0.5153297930394495，模块值
        -0.5153297930393791，相对差 1.4e-13，即新值是正确值、旧前提是缺陷
        造成的假失败。因此这里的「自洽」改为钉新行为：normal 家族必须收敛
        到参考核验值，且 df 失败文案仍不得建议一个实测无效的旋钮。
        """
        rows = [{"study_id": "S1", "measure": "MD", "estimate": -0.57, "se": 0.24},
                {"study_id": "S2", "measure": "MD", "estimate": -0.54, "se": 0.14}]
        # r9/l5a：normal 家族在此 rows 上必须收敛，且与稠密参考一致
        # （参考值见上；容差按参考积分的有效位取 1e-9，远严于任何真回归）。
        normal = bayesian_robust_meta.fit_bayesian_robust_meta(
            rows, mu_prior_mean=0.0, mu_prior_family="normal", mu_prior_scale=1.0,
            mu_prior_df=None, tau_prior_family="half_normal", tau_prior_scale=0.5,
            tau_prior_df=None)
        self.assertTrue(normal["integration"]["converged"])
        self.assertLess(abs(normal["mu"]["mean"] - (-0.5153297930394495)), 1e-9,
                        msg="normal 家族在该数据上收敛，且必须与稠密参考积分一致")
        with self.assertRaises(ValueError) as ctx:
            self._fit(1e6, rows=rows)
        message = str(ctx.exception)
        self.assertNotIn("normal family", message,
                         msg="df=1e6 的失败没有阈值结构，不得引入家族建议")
        self.assertNotIn("smaller df", message,
                         msg="实测没有更小的 df 收敛，不得给出「更小 df」建议")
        self.assertIn("not shown to be the cause", message,
                      msg="必须明确说明 df 未被证明是原因")
        self.assertNotIn("equivalent to a normal prior", message)

    def test_advice_names_a_df_proven_to_settle(self):
        """建议里点名的 df 必须在本夹具上实测收敛——不能只是「更小就推荐」。

        df=1e12 在本夹具上失败，文案点名 df=1e8。这里独立复跑该 df，确认它
        确实收敛（第三轮之前文案只是"试更小的 df"，不保证点名值可用）。
        """
        import re

        with self.assertRaises(ValueError) as ctx:
            self._fit(1e12)
        named = re.search(r"does settle on these rows \(df=([0-9.e+\-]+)\)",
                          str(ctx.exception))
        self.assertIsNotNone(named, msg=f"文案未点名任何实测收敛的 df：{ctx.exception}")
        self.assertLess(float(named.group(1)), 1e12)
        self._fit(float(named.group(1)))  # 点名值必须真的收敛，否则抛错即红

    def test_lucky_df_between_failures_is_not_recommended(self):
        """第三轮的核心钉子：孤立收敛点不得成为建议。

        k=2 反例上 df=1e8 成功，但 df=1e7 与 df=1e9 都失败——该夹具没有
        「df 越大越难算」的阈值结构，因此即使 1e8 能算，也不许建议。文案
        必须中性，且不得点名 1e8。
        """
        rows = [{"study_id": "S1", "measure": "MD", "estimate": -0.57, "se": 0.24},
                {"study_id": "S2", "measure": "MD", "estimate": -0.54, "se": 0.14}]
        # 前提复核：1e7 失败、1e8 成功、1e9 失败（非阈值结构）
        for df, should_settle in ((1e7, False), (1e8, True), (1e9, False)):
            with self.subTest(df=df):
                if should_settle:
                    self._fit(df, rows=rows)
                else:
                    with self.assertRaises(ValueError):
                        self._fit(df, rows=rows)
        with self.assertRaises(ValueError) as ctx:
            self._fit(1e9, rows=rows)
        message = str(ctx.exception)
        self.assertNotIn("df=1e+08", message,
                         msg="1e8 是夹在两次失败之间的孤立成功，不得被推荐")
        self.assertNotIn("smaller df", message)
        self.assertIn("not shown to be the cause", message)

    def test_isolated_success_gets_its_own_honest_wording(self):
        """孤立成功点存在时，中性诊断也不能说「没有任何 df 收敛」（那是假话）。

        夹具（k=3，实测）：df=1e10 失败，而 1e2 步长梯子上出现「失败→成功→
        失败」的孤立成功，因而既不能推荐也不能声称全失败。文案必须落到
        「没有一致收敛」这一支，且不得点名任何 df。
        """
        rows = [{"study_id": "S0", "measure": "MD",
                 "estimate": -0.500826731375809, "se": 0.17232803715009687},
                {"study_id": "S1", "measure": "MD",
                 "estimate": -0.5437122878609378, "se": 0.20759857813849897},
                {"study_id": "S2", "measure": "MD",
                 "estimate": -0.506466670394568, "se": 0.18155852458647231}]
        with self.assertRaises(ValueError) as ctx:
            self._fit(1e10, rows=rows)
        message = str(ctx.exception)
        self.assertIn("did not settle consistently", message,
                      msg=f"孤立成功必须用「不一致」措辞，而非「一个都没收敛」：{message}")
        self.assertNotIn("No alternative df value", message)
        self.assertNotIn("does settle on these rows", message)

    def test_probe_failure_never_masks_the_original_error(self):
        """探针只做诊断：探针里抛出的非 ValueError 不得替换掉原本的值错误。

        用包装器模拟「探针路径炸出 RuntimeError」：外层调用仍必须是
        ValueError（4xx 契约），而不是把诊断时的意外异常漏给用户。
        """
        from unittest import mock

        original = bayesian_robust_meta.fit_bayesian_robust_meta

        def explode_in_probe(*args, **kwargs):
            if kwargs.get("_diagnostic_probe") is False:
                raise RuntimeError("probe blew up")
            return original(*args, **kwargs)

        with mock.patch.object(bayesian_robust_meta, "fit_bayesian_robust_meta",
                               side_effect=explode_in_probe):
            with self.assertRaises(ValueError) as ctx:
                self._fit(1e12)
        self.assertIn("grid refinement did not converge", str(ctx.exception))

    def test_cap_itself_unchanged(self):
        """上界仍是 1e12：1e12+1 以上才以 df 上界拒绝（基线同口径）。"""
        with self.assertRaises(ValueError) as ctx:
            self._fit(1e12 + 1)
        self.assertIn("too large", str(ctx.exception))

    def test_previously_accepted_dfs_stay_accepted(self):
        """R2 钉子（第一轮的违规点）：基线可解的 df 区间必须继续可解且取值不变。

        基线 a080fde 在本夹具上 df=5e8…3e9 全部返回，mu.mean 与 normal 家族
        极限相差 ≤3e-9，故这些是合法输入；第一轮的 1e8 上界把它们全部误拒。

        r9/l5a 更新（F2-61，升级授权同批更新先例）：df=5e8 与 3e9 的期望值
        从 0.3592872505561766 / 0.359287250619282 改为
        0.35928725055617655 / 0.3592872506192819。旧值是 ``axis_panels`` 把
        越界尾 panel 以 a>b 反向追加后得到的：负面积因子改变了加权结果
        （旧值相对新值分别偏 +1.5e-16 与 +3.1e-16）。反向 panel 修掉后这两个
        df 的取值回落到无负权重的正确权重集上；其余三个 df（1e6/1e8/1e9）
        与原值逐位相同，可作同一修正未产生漂移的对照。
        """
        for df, expected in ((1e6, 0.35928724811260593),
                             (1e8, 0.3592872505883232),
                             (5e8, 0.35928725055617655),
                             (1e9, 0.35928725061162303),
                             (3e9, 0.3592872506192819)):
            with self.subTest(df=df):
                self.assertEqual(self._fit(df)["mu"]["mean"], expected,
                                 msg=f"df={df:g} 在基线上是可解的合法输入，不得改判为拒绝")

    def test_small_df_unchanged(self):
        """常规对照（基线绿，修复后必须仍绿）：df=3 取值不变。

        r9/l5a 更新（F2-61，升级授权同批更新先例）：期望值从
        0.3584942038278878 改为 0.35849420382788（相对差 2.2e-14）。旧值同样
        含反向 panel 的负面积：本夹具在 HEAD 上确实追加了
        ``(a=7.0740, b=2.8108)``、面积因子 -2.131631 的 panel；修复后无任何
        负面积 panel，2-D 稠密参考积分（u 步长 0.02、w 步长 0.02）给出
        0.3584942051620264，与修复值相差 1.3e-9、与旧值相差 3.7e-9——两者
        都远在参考积分有效位之内，但只有修复值不再带负权重。
        """
        self.assertEqual(self._fit(3.0)["mu"]["mean"], 0.35849420382788)


# 修前实测：binary_effect(True, 2, 1, 2, 'RR') 正常返回
# {'estimate': 1.0, 'se': 1.0}（True 被当作 1 个事件）；连续路径 n=True 已被
# n<2 拒（复审范围更正：样本量半句不成立）。
class B08N4BoolCountTests(unittest.TestCase):
    """B08-N4：bool 被 isinstance(int) 放行为计数。"""

    def test_bool_binary_counts_rejected(self):
        for args in ((True, 2, 1, 2), (10, 20, True, 20)):
            with self.subTest(args=args):
                with self.assertRaises(ValueError, msg="bool 不是合法计数"):
                    review_analysis.binary_effect(*args, "RR")

    def test_int_counts_unchanged(self):
        """常规对照（基线绿，修复后必须仍绿）：整型计数结果逐位不变。"""
        result = review_analysis.binary_effect(10, 20, 5, 20, "RR")
        self.assertEqual(result, {"measure": "RR", "estimate": 2.0, "se": 0.4472135954999579,
                                  "se_scale": "log"})


# 修前实测（*项独立复现）：cr0/cr1/cr2 的 warnings 均为 4 条，无任何一条量化
# CR0/CR1 的 I 类错误放大；同设计 1500 次蒙特卡洛（k=8、tau2=0.05、真斜率 0）
# 拒绝率 cr0=0.0787 > cr1=0.0640 > cr2=0.0387（名义 0.05）——与 minimax_10
# §MM10-08 的 0.1105/0.0921/0.0559 同向。
class MM1008CrAntiConservatismWarningTests(unittest.TestCase):
    """MM10-08*：CR0/CR1 反保守性必须在返回载荷中量化披露。"""

    ROWS = [{"study_id": f"S{i+1}", "effect": 0.1 * ((i % 5) - 2), "se": 0.15 + 0.02 * i,
             "dose": 0.5 * i} for i in range(8)]
    MODS = {"dose": "continuous"}

    def _fit(self, vcov):
        return meta_regression_robust.robust_meta_regression(
            self.ROWS, self.MODS, model="reml", vcov_type=vcov)

    def test_cr0_and_cr1_quantify_anti_conservatism(self):
        for vcov in ("cr0", "cr1"):
            with self.subTest(vcov=vcov):
                warnings = self._fit(vcov)["warnings"]
                self.assertTrue(
                    any("anti-conservative" in w.lower() and any(ch.isdigit() for ch in w)
                        for w in warnings),
                    msg=f"{vcov} 必须披露量化的反保守性（I 类错误可达名义值约 2 倍）")

    def test_cr2_and_point_estimates_unchanged(self):
        """常规对照（基线绿，修复后必须仍绿）：cr2 无新增告警；点估计跨 vcov 不变。"""
        self.assertFalse(any("anti-conservative" in w.lower()
                             for w in self._fit("cr2")["warnings"]))
        estimates = {vc: [(c["name"], c["estimate"]) for c in self._fit(vc)["coefficients"]]
                     for vc in ("cr0", "cr1", "cr2")}
        self.assertEqual(estimates["cr0"], estimates["cr1"])
        self.assertEqual(estimates["cr0"], estimates["cr2"])

    def test_quantified_multiplier_only_in_few_study_regime(self):
        """2.2x/1.8x 是 k=8 设计的实测值：k 变大后幅度不再成立，只报方向。

        每个大-k 夹具在此仍能拟合（cr0/cr1 照常可算），告警不得引用
        它支撑不了的倍数。
        """
        rows = [{"study_id": f"S{i+1}", "effect": 0.1 * ((i % 7) - 3),
                 "se": 0.2 + 0.001 * i, "dose": 0.25 * i} for i in range(60)]
        result = meta_regression_robust.robust_meta_regression(
            rows, {"dose": "continuous"}, model="reml", vcov_type="cr0")
        quantified = [w for w in result["warnings"] if "anti-conservative" in w.lower()]
        self.assertTrue(quantified, "大 k 仍须披露反保守性方向")
        self.assertFalse(any("2.2x" in w or "1.8x" in w for w in quantified),
                         msg="k=60 时不得沿用 k=8 的倍数")


# r13 终扫 stats-a-0 重钉（旧钉→新钉对照）：
# 旧钉（本类原参考式，漏 -log|X'X|）：
#   reml_ll_old = -.5(df·log2π + log|V| + log|X'V⁻¹X| + Q) = -0.725791352645
#   它把"平坦先验边际似然"当成真值，促成了 1db6d5e 的多减；
#   模块当时上报值 = reml_ll_old，与本参考式恒差 0（旧钉绿）。
# 新钉（误差对照密度 / Harville 含项式）：
#   reml_ll_new = reml_ll_old + .5·log|X'X| = -0.176485208311
#   （logdet(X'X)=log 3，0.5·log3 = 0.549306144334）。
# 旧钉目标比新钉目标低 0.5·logdet(X'X)=0.5493061443340548；1db6d5e 之后、
# stats-a-0 修复之前的上报值等于旧钉目标（比标准值低恰该量）。
# 独立交叉验证（QR 补空间误差对照密度）见
# build/tests/test_r13_stats_reml_loglik.py。
class MM1009RemlLogLikelihoodTests(unittest.TestCase):
    """MM10-09*：network_random 的 reml_log_likelihood 必须是含 -log|X'X| 的标准 REML。"""

    STUDIES = [
        {"study_id": "T1", "covariance_scale": "natural",
         "contrasts": [{"treatment": "A", "comparator": "B", "estimate": 2.0}],
         "covariance": [[0.04]]},
        {"study_id": "T2", "covariance_scale": "natural",
         "contrasts": [{"treatment": "B", "comparator": "C", "estimate": -1.0}],
         "covariance": [[0.09]]},
        {"study_id": "T3", "covariance_scale": "natural",
         "contrasts": [{"treatment": "A", "comparator": "C", "estimate": 0.5}],
         "covariance": [[0.06]]},
    ]

    def test_reported_value_is_true_reml_log_likelihood(self):
        import numpy as np

        result = network_random.synthesize_network_random(self.STUDIES, "MD", reference="C")
        reported = result["fit"]["reml_log_likelihood"]
        tau2 = result["random_effects"]["tau2"]
        design = np.array([[1.0, -1.0], [0.0, 1.0], [1.0, 0.0]])  # 列 (A,B)，参照 C
        values = np.array([2.0, -1.0, 0.5])
        V = np.diag([0.04 + tau2, 0.09 + tau2, 0.06 + tau2])
        Vinv = np.linalg.inv(V)
        XtVinvX = design.T @ Vinv @ design
        beta = np.linalg.solve(XtVinvX, design.T @ Vinv @ values)
        Q = float((values - design @ beta) @ Vinv @ (values - design @ beta))
        reml_ll = -0.5 * (math.log(2 * math.pi) + math.log(np.linalg.det(V))
                          + math.log(np.linalg.det(XtVinvX))
                          - math.log(np.linalg.det(design.T @ design)) + Q)
        # 旧钉目标（漏 -log|X'X|，1db6d5e 后的上报值）比新钉低 0.5·logdet。
        reml_ll_old = reml_ll - 0.5 * math.log(3.0)
        self.assertEqual(round(reml_ll_old, 9), -0.725791353)
        self.assertEqual(round(reml_ll, 9), -0.176485208)
        self.assertLess(abs(reported - reml_ll), 1e-9,
                        msg=f"上报值必须含 -log|X'X|；旧钉目标 {reml_ll_old!r}，"
                            f"新钉目标 {reml_ll!r}，实测 {reported!r}")

    def test_estimates_and_tau2_bit_identical(self):
        """常规对照（基线绿，修复后必须仍绿）：合并估计/tau2 不因诊断字段修正而变。"""
        result = network_random.synthesize_network_random(self.STUDIES, "MD", reference="C")
        self.assertEqual(result["random_effects"]["tau2"], 0.019999999778362258)
        by_pair = {(e["treatment"], e["comparator"]): (e["estimate"], e["se"])
                   for e in result["estimates"]}
        self.assertEqual(by_pair[("A", "B")], (1.8800000001241177, 0.2135415646810929))
        self.assertEqual(by_pair[("A", "C")], (0.6599999999822692, 0.23323807547680395))
        self.assertEqual(by_pair[("B", "C")], (-1.2200000001418485, 0.24819347260690858))


# 修前实测（*项独立复现）：NaN 权重通过 `any(w < 0.0)` 校验 → class_log_prior_
# 全 NaN → predict_scores=[nan]；inf 权重 → ValueError: math domain error（误导）。
class MM10010NbFiniteWeightTests(unittest.TestCase):
    """MM10-10*：al/nb NaN sample_weight 绕过非负校验污染排序。"""

    X = [{"a": 1.0, "b": 2.0}, {"a": 3.0, "b": 1.0}]
    Y = [1, 0]

    def test_non_finite_weights_rejected(self):
        for name, weights in (("nan", [float("nan"), 1.0]), ("inf", [float("inf"), 1.0])):
            with self.subTest(weight=name):
                clf = al_nb.MultinomialNB(alpha=1.0)
                with self.assertRaises(ValueError, msg=f"{name} 权重不得穿过校验"):
                    clf.fit(self.X, self.Y, sample_weight=weights)

    def test_finite_weights_bit_identical(self):
        """常规对照（基线绿，修复后必须仍绿）：无权/合法加权结果逐位不变。"""
        plain = al_nb.MultinomialNB(alpha=1.0)
        plain.fit(self.X, self.Y)
        self.assertEqual(plain.predict_scores([{"a": 1.0, "b": 1.0}]), [0.5192307692307692])
        weighted = al_nb.MultinomialNB(alpha=1.0)
        weighted.fit(self.X, self.Y, sample_weight=[1.0, 2.0])
        self.assertEqual(weighted.predict_scores([{"a": 1.0, "b": 1.0}]), [0.3636363636363638])
        zero_one = al_nb.MultinomialNB(alpha=1.0)
        zero_one.fit(self.X, self.Y, sample_weight=[0.0, 1.0])  # 合法零权重仍可用
        self.assertTrue(all(math.isfinite(v)
                            for v in zero_one.predict_scores([{"a": 1.0, "b": 1.0}])))


# 修前实测（*项独立复现）：recall_target=0 → threshold ZeroDivisionError；
# recall_target=1.5 → threshold(10)=-4 且 should_stop 仍发证（stop=True）；
# alpha=2 / sampling_fraction=-1 / 0 → 证书照样签发（confidence=0.30）。
class MM10011StopCertificateValidationTests(unittest.TestCase):
    """MM10-11*：RecallCertificate 构造器无域校验。"""

    def test_invalid_constructor_params_rejected(self):
        for kwargs in ({"recall_target": 0.0}, {"recall_target": 1.5}, {"recall_target": -0.1},
                       {"alpha": 0.0}, {"alpha": 1.0}, {"alpha": 2.0},
                       {"sampling_fraction": 0.0}, {"sampling_fraction": -1.0},
                       {"sampling_fraction": 1.5}):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError, msg="越域参数必须在构造时拒绝"):
                    stop_certificate.RecallCertificate(**kwargs)

    def test_required_sample_size_clamped_nonnegative(self):
        cert = stop_certificate.RecallCertificate()
        self.assertEqual(cert.required_sample_size(-10, 0), 0,
                         msg="负池不得给出负抽样量（修前 -3 使守卫恒成立、证书直接发放）")

    def test_defaults_and_honest_confidence_unchanged(self):
        """常规对照（基线绿，修复后必须仍绿）：默认参数行为逐位不变（r7 已有钉子）。"""
        cert = stop_certificate.RecallCertificate()
        self.assertEqual(cert.threshold(152), 8)
        out = cert.should_stop(total_records=200, screened_count=100, found_relevant=10,
                               sample_new_relevant=0, sample_size=30)
        self.assertTrue(out["stop"])
        self.assertEqual(out["confidence"], 0.30000000000000004)


def _network_db_rows(rows_spec):
    """rows_spec: list[(study_id, comparison, estimate, se_key)] → 库路径。

    每个 study_id 各自绑定一份独立的文献（save_effect 要求 source_key 已
    linked 到该 study），因此研究数由调用方给的行决定。
    """
    db = _task_db_with_articles("rf_r8_mm12_")
    from coscreen import db as db_mod
    from coscreen.models import Article

    study_keys = {}
    for sid, _comp, _est, _key in rows_spec:
        if sid not in study_keys:
            key = f"R{len(study_keys) + 1}"
            study_keys[sid] = key
    db_mod.upsert_articles(db, [Article(zotero_key=k, title=f"Ref {k}")
                                for k in study_keys.values()], [])
    for sid, key in study_keys.items():
        review_analysis.save_study(db, sid, sid, [key])
    for sid, comp, est, _key in rows_spec:
        review_analysis.save_effect(db, sid, comp, "O1", "T1", "MD", est, 1.0, study_keys[sid])
    return db


# 修前实测（*项独立复现）：{A vs B: 2.0, B vs A: 2.0, A vs C: 4.0} →
# network_analysis A-vs-B 估计 = 0.0（两条源研究都说 2.0）、residual_q=8.0、
# 无任何方向性告警；network_gls 同型夹具估计 0.0、residual_q=200、无告警。
#
# 修法用 warning 而非 minimax_10 建议的 ValueError：反向串 + 取反估计
# （"B vs A": -2.0 表示 A-B=+2）是数学上自洽的合法输入，GLS 会正确合并；
# 一刀切拒绝违反 R2（不新增拒绝合法输入的严格性）。告警消除"静默"。
class MM10012ReversedContrastWarningTests(unittest.TestCase):
    """MM10-12*：同一无序治疗对的两种方向同时出现必须被显式标记。"""

    GLS_STUDIES = [
        {"study_id": "T1", "covariance_scale": "natural",
         "contrasts": [{"treatment": "A", "comparator": "B", "estimate": 2.0}],
         "covariance": [[0.04]]},
        {"study_id": "T2", "covariance_scale": "natural",
         "contrasts": [{"treatment": "B", "comparator": "A", "estimate": 2.0}],
         "covariance": [[0.04]]},
        {"study_id": "T3", "covariance_scale": "natural",
         "contrasts": [{"treatment": "A", "comparator": "C", "estimate": 4.0}],
         "covariance": [[0.06]]},
    ]

    @staticmethod
    def _has_direction_warning(result) -> bool:
        """network_analysis 用单个 'warning' 字符串、network_gls 用 'warnings' 列表。"""
        texts = ([result["warning"]] if isinstance(result.get("warning"), str)
                 else list(result.get("warnings", [])))
        return any("direction" in t.lower() for t in texts)

    def test_network_analysis_flags_reversed_pair(self):
        db = _network_db_rows([("S1", "A vs B", 2.0, "R1"), ("S2", "B vs A", 2.0, "R2"),
                               ("S3", "A vs C", 4.0, "R3")])
        result = network_analysis.synthesize(db, "O1", "T1", "MD", reference="C")
        self.assertTrue(self._has_direction_warning(result),
                        msg="A vs B 与 B vs A 并存时不得再静默平均")

    def test_network_gls_flags_reversed_pair(self):
        result = network_gls.synthesize_network(self.GLS_STUDIES, "MD", reference="C")
        self.assertTrue(self._has_direction_warning(result))

    def test_consistent_reversed_pair_still_allowed(self):
        """R2 钉子：反向串 + 取反估计是合法输入，必须继续可算（只告警不拒绝）。"""
        studies = [dict(s) for s in self.GLS_STUDIES]
        studies[1] = {"study_id": "T2", "covariance_scale": "natural",
                      "contrasts": [{"treatment": "B", "comparator": "A", "estimate": -2.0}],
                      "covariance": [[0.04]]}
        result = network_gls.synthesize_network(studies, "MD", reference="C")
        ab = [e for e in result["estimates"]
              if {e["treatment"], e["comparator"]} == {"A", "B"}][0]
        self.assertLess(abs(ab["estimate"] - 2.0), 1e-9,
                        msg="B vs A: -2.0 与 A vs B: 2.0 是同一对比的合法双报，必须照常合并")
        self.assertTrue(self._has_direction_warning(result))

    def test_forward_duplicate_pair_not_flagged(self):
        """常规对照：同方向重复（正常复现研究）不得误报告警。"""
        studies = [
            {"study_id": "T1", "covariance_scale": "natural",
             "contrasts": [{"treatment": "A", "comparator": "B", "estimate": 2.0}],
             "covariance": [[0.04]]},
            {"study_id": "T2", "covariance_scale": "natural",
             "contrasts": [{"treatment": "A", "comparator": "B", "estimate": 2.4}],
             "covariance": [[0.04]]},
            {"study_id": "T3", "covariance_scale": "natural",
             "contrasts": [{"treatment": "A", "comparator": "C", "estimate": 4.0}],
             "covariance": [[0.06]]},
        ]
        result = network_gls.synthesize_network(studies, "MD", reference="C")
        self.assertFalse(self._has_direction_warning(result))

    def test_reversed_pair_named_once_in_message(self):
        """同一无序对被两个研究反向时，文案只点名一次（不打印重复项）。"""
        db = _network_db_rows([("S1", "A vs B", 2.0, "R1"), ("S2", "B vs A", 2.0, "R2"),
                               ("S3", "B vs A", 2.2, "R3"), ("S4", "A vs C", 4.0, "R4")])
        result = network_analysis.synthesize(db, "O1", "T1", "MD", reference="C")
        named = result["warning"].count("'B vs A'") + result["warning"].count("'A vs B'")
        self.assertEqual(named, 1, msg=f"重复点名：{result['warning'][-200:]!r}")


# 修前实测（*项独立复现）：sd_pre=1e-200/1e-300 → ZeroDivisionError（:127
# sd_pre**2 下溢为 0，:129 只捕 OverflowError）；sd_pre=1e-160/2.2e-162 →
# OverflowError 被正确转为 ValueError；sd_pre=1 正常。
class MM10013SdPreUnderflowTests(unittest.TestCase):
    """MM10-13*：sd_pre 下溢除零必须转 ValueError（4xx）。"""

    def _calc(self, sd_pre):
        return single_group_standardized_change.calculate_single_group_standardized_change(
            {"n": 20, "mean_pre": 1.0, "mean_post": 0.5, "sd_pre": sd_pre, "sd_change": 0.8},
            standardizer="pretest_sd")

    def test_underflow_sd_pre_is_valueerror(self):
        for sd_pre in (1e-200, 1e-300):
            with self.subTest(sd_pre=sd_pre):
                with self.assertRaises(ValueError, msg="下溢分母必须以 ValueError 拒绝而非裸 ZeroDivisionError"):
                    self._calc(sd_pre)

    def test_regular_and_overflow_paths_unchanged(self):
        """常规对照（基线绿，修复后必须仍绿）：正常值逐位不变、近下溢仍走既有
        OverflowError→ValueError 路径。"""
        result = self._calc(1.0)
        self.assertEqual((result["estimate"], result["se"]), (-0.47995517646212743, 0.1993645808307241))
        with self.assertRaises(ValueError):
            self._calc(1e-160)


class F06RouteLevelTests(unittest.TestCase):
    """F-06 路由层：DtaResultIn 四计数字段加 le=2**63-1（422 取代 500）。"""

    _saved_env: dict[str, str | None] = {}

    @classmethod
    def setUpClass(cls):
        # 环境变量在运行期覆盖、tearDownClass 原样恢复——本文件按字母序先于
        # test_trace_*/test_upload_* 等文件运行，残留覆盖或删除其指向的临时
        # 目录会污染同进程后续测试（临时目录对象保留到进程结束，不 cleanup，
        # 与 test_r7_mm9_api_fixes 的模块级 fixture 同一约定）。
        for key in ("COBOOKSHELF_DATA_DIR", "REVIEWFLOW_MODE"):
            cls._saved_env[key] = os.environ.get(key)
        fixture = tempfile.TemporaryDirectory(prefix="rf_r8_f06route_")
        os.environ["COBOOKSHELF_DATA_DIR"] = fixture.name
        os.environ["REVIEWFLOW_MODE"] = "server"
        from fastapi.testclient import TestClient

        from custom_backend import main as backend_main
        from custom_backend.main import app

        app.dependency_overrides[backend_main.require_user] = lambda: {
            "user_id": 1, "username": "r8-fl1-probe"}
        cls.client = TestClient(app, raise_server_exceptions=False)
        cls.client.__enter__()
        cls._backend = backend_main
        cls._fixture = fixture

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        from custom_backend.main import app

        # 键是函数对象 backend_main.require_user，不是字符串——按字符串 pop
        # 会让鉴权旁路泄漏到同进程后续测试（upload 安全回归里未登录请求
        # 期望 401 却拿到 200）。
        app.dependency_overrides.pop(cls._backend.require_user, None)
        for key, value in cls._saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def _body(self, tp):
        return {"study_id": "S1", "index_test": "IT", "target_condition": "TC",
                "threshold": "TH", "reference_standard": "RS", "tp": tp, "fp": 1,
                "fn": 1, "tn": 1, "source_key": "R1"}

    def test_tp_above_sqlite_max_is_422_not_500(self):
        response = self.client.post("/api/tasks/T1/analysis/dta/results", json=self._body(2**63))
        self.assertEqual(response.status_code, 422,
                         msg=f"修前：校验放行后落到 handler（404/500）；修复后必须在请求层 422。status={response.status_code}")

    def test_tp_at_sqlite_max_passes_validation(self):
        """常规对照：2**63-1 必须继续通过请求校验（不会因新上界被误拒）。"""
        from custom_backend.main import DtaResultIn

        model = DtaResultIn(**self._body(_SQLITE_MAX))
        self.assertEqual(model.tp, _SQLITE_MAX)


if __name__ == "__main__":
    unittest.main()
