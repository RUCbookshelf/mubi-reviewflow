"""2.1.6 Task 4 (NUM-01) bootstrap 数值边界钉子。

只补 ``test_r9_l5a_bayesian_intervals.py``（本轮只读）未决定的数值行为，
全部为纯计算（不读任何真实数据）。修前参考值来自
``E:/rf216-scratch/bootstrap/task4_prefix_evidence.json``（release venv =
validation venv：同 Python 3.11.15 / numpy 2.4.6，钉前已交叉验证逐字节一致）。

钉住的行为（主控裁定，2026-11-14）：
- NUM-01 成功输出中的每个 float 统计量必须有限（既有合法 null 不动），
  ``json.dumps(..., allow_nan=False)`` 必须成功。修前 se≈1e153 的合法样本
  返回 ``boot_summary.sd=inf``（错误输出），修后由缩放的稳定归约给出有限值。
- 接受域不收窄：9.48e153 / 1e150 合法效应边界与 1e153..1.34e154 大 SE 域
  的区间、n_invalid_boot 逐字节保持修前值；不改随机数消耗（正常样本区间
  与修前证据逐字节一致即直接证明）。
- 四族拒绝消息原文不变，且拒绝路径零 RuntimeWarning 逃逸（旧代码在
  bootstrap_interval.py:210-228/_methods.py 在同一样本上泄漏溢出警告）。
- 仍不可表示的极端结果由返回点守卫按模块 ValueError（4xx 契约）拒绝。

跨平台口径（2.1.6 可移植性收尾，2026-11-14）：同环境修前修后的逐字节证明
保留在 ``task_bootstrap_report.md`` / ``task4_prefix_evidence.json``，本文件
不再把它当作跨平台逐位保证——正常路径的统计量钉值改用 house
``assert_numeric_baseline``（与 test_r9_l5a 同款：直接算术值默认
rel_tol=1e-11，REML 优化器 tau2 沿用 l5a:361 的 abs_tol=1e-8）；确定性改由
「同环境同 seed 两次运行逐字节相等」覆盖（RNG 消耗顺序不变钉）。字符串、
计数、离散计数比（tau2_boundary_fraction = n/N 的正确舍入）与结构性精确值
（9.48e153/1e150 边界的 ±a 精确相消、同项加倍、ulp≫噪声）仍 assertEqual。
"""
import json
import math
import sys
import time
import unittest
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from _numeric_assertions import assert_numeric_baseline
from coscreen import bootstrap_interval

_RANGE_MESSAGE = "study standard errors or effect scales are outside the supported numeric range"
_DL_C_MESSAGE = "sampling variances give a non-positive DerSimonian-Laird constant C"
_REML_MESSAGE = "REML profile optimization failed on the input data"
_V_MESSAGE = "squared SE values must be finite and positive"


def _nonfinite_paths(node, path="result"):
    """Paths of every non-finite float leaf (None/int/str slots are legal)."""
    bad = []
    if isinstance(node, dict):
        for key, value in node.items():
            bad.extend(_nonfinite_paths(value, f"{path}.{key}"))
    elif isinstance(node, (list, tuple)):
        for index, value in enumerate(node):
            bad.extend(_nonfinite_paths(value, f"{path}[{index}]"))
    elif isinstance(node, float) and not math.isfinite(node):
        bad.append(path)
    return bad


class ExtremeRejectionWarningZeroEscapeTests(unittest.TestCase):
    """四族拒绝：消息原文逐字不变 + 全程零 RuntimeWarning 逃逸。

    修前实测（task4_prefix_evidence.json §rej_*）：同一样本除 ValueError 外
    还逃逸 1-3 条 RuntimeWarning（bootstrap_interval.py:210-228、
    numpy _methods.py）。过滤 RuntimeWarning 提升为错误后，任何逃逸都会让
    assertRaises(ValueError) 失败。
    """

    CASES = (
        # name, effects, ses, model, n_boot, seed, expected message
        ("q_overflow_fixed", [1e160, -1e160], [1.0, 1.0], "fixed", 10, 1, _RANGE_MESSAGE),
        ("q_overflow_dl", [1e160, -1e160], [1.0, 1.0], "dl", 10, 1, _RANGE_MESSAGE),
        ("q_overflow_reml", [1e160, -1e160], [1.0, 1.0], "reml", 10, 1, _REML_MESSAGE),
        ("subnormal_se_fixed", [1.0, 2.0], [1e-160, 1.0], "fixed", 50, 1, _RANGE_MESSAGE),
        ("subnormal_se_dl", [1.0, 2.0], [1e-160, 1.0], "dl", 50, 1, _DL_C_MESSAGE),
        ("subnormal_se_reml", [1.0, 2.0], [1e-160, 1.0], "reml", 50, 1, _RANGE_MESSAGE),
        ("subnormal_v", [1.0, 2.0], [1e-200, 1.0], "fixed", 50, 1, _V_MESSAGE),
    )

    def test_rejections_exact_message_no_runtime_warning(self):
        for name, effects, ses, model, n_boot, seed, message in self.CASES:
            with self.subTest(case=name):
                with warnings.catch_warnings():
                    warnings.simplefilter("error", RuntimeWarning)
                    started = time.perf_counter()
                    with self.assertRaises(ValueError) as ctx:
                        bootstrap_interval.bootstrap_pooled_interval(
                            effects, ses, model=model, interval_type="percentile",
                            n_boot=n_boot, seed=seed)
                    elapsed = time.perf_counter() - started
                self.assertEqual(str(ctx.exception), message)
                self.assertLess(elapsed, 5.0, msg=f"拒绝必须快速返回，实测 {elapsed:.2f}s")


class NormalBaselineByteIdentityTests(unittest.TestCase):
    """固定 seed 正常样本：同环境同 seed 两次运行逐字节相等（确定性钉），
    统计量对修前证据取跨平台基线。

    errstate/稳定归约都不消耗随机数；「同环境同 seed 两次运行全载荷逐字节
    相等（剔除墙钟 seconds）」即 RNG 消耗顺序不变的直接证明。修前值 =
    release venv 证据（钉前已在 validation venv 复现、同环境修前修后逐字节
    一致，见 task_bootstrap_report.md）；这里以 house
    ``assert_numeric_baseline`` 钉跨平台基线（BLAS/SIMD 归约次序与 REML
    括号搜索的 ulp 级平台差异被默认 rel_tol=1e-11 吸收；REML tau2 用
    l5a:361 先例 abs_tol=1e-8）。
    """

    NORMAL_Y = [0.32, 0.45, 0.51, 0.62, 0.71]
    NORMAL_SE = [0.12, 0.15, 0.11, 0.18, 0.14]

    def _boot(self, model, n_boot, seed):
        return bootstrap_interval.bootstrap_pooled_interval(
            self.NORMAL_Y, self.NORMAL_SE, model=model,
            interval_type="percentile", n_boot=n_boot, seed=seed)

    def _assert_same_run_determinism(self, result, replay):
        """同环境同 seed 两次运行：除墙钟 seconds 外逐字节相等。"""
        result.pop("seconds")
        replay.pop("seconds")
        self.assertEqual(result, replay,
                         msg="同环境同 seed 的两次运行不一致（RNG 消耗顺序漂移）")

    def test_normal_fixed_byte_identical(self):
        result = self._boot("fixed", 2000, 7)
        self._assert_same_run_determinism(result, self._boot("fixed", 2000, 7))
        assert_numeric_baseline(result["interval"],
                                (0.3844334580100919, 0.6209004353225857))
        assert_numeric_baseline(result["boot_summary"], {
            "n_valid": 2000,
            "mean": 0.5005472725704931,
            "sd": 0.0604888416752374,
            "minimum": 0.3014528866628554,
            "maximum": 0.7097227704905106,
            "median": 0.4993065694203871,
            "quantiles": {
                "0.025": 0.3844334580100919, "0.25": 0.4597173122222726,
                "0.5": 0.4993065694203871, "0.75": 0.5407978598947825,
                "0.975": 0.6209004353225857},
            "quantile_method": "linear",
        })
        assert_numeric_baseline(result["mc_se"], {"low": 0.002462504740580697,
                                                 "high": 0.003628580834346207})
        self.assertEqual((result["n_invalid_boot"], result["boot_summary"]["n_valid"]),
                         (0, 2000))
        self.assertIsNone(result["tau2"])

    def test_normal_dl_byte_identical(self):
        result = self._boot("dl", 2000, 7)
        self._assert_same_run_determinism(result, self._boot("dl", 2000, 7))
        assert_numeric_baseline(result["interval"],
                                (0.39155723933412845, 0.6288691815071996))
        assert_numeric_baseline(result["tau2"], 0.004904680422205072)
        assert_numeric_baseline(result["boot_summary"]["sd"], 0.06186605197256703)
        # 离散计数比（n/N 的正确舍入）：跨平台精确，保持 assertEqual
        self.assertEqual(result["tau2_boundary_fraction"], 0.178)
        self.assertEqual(result["n_invalid_boot"], 0)

    def test_normal_reml_byte_identical(self):
        result = self._boot("reml", 2000, 7)
        self._assert_same_run_determinism(result, self._boot("reml", 2000, 7))
        assert_numeric_baseline(result["interval"],
                                (0.3916795774066085, 0.6290193778632597))
        # REML 轮廓优化的 tau2：l5a:361 同款 abs_tol=1e-8（house 先例口径）
        assert_numeric_baseline(result["tau2"], 0.005782448619884239, abs_tol=1e-8)
        assert_numeric_baseline(result["boot_summary"]["sd"], 0.061904849528943186)
        self.assertEqual(result["n_invalid_boot"], 0)

    def test_suite_pins_still_byte_identical(self):
        """test_r9_l5a:353/:357/:361 的钉值按同容差口径在本文件复现。"""
        fixed = bootstrap_interval.bootstrap_pooled_interval(
            [0.1, 0.2, 0.3, 0.4, 0.5], [0.1] * 5, model="fixed",
            interval_type="percentile", n_boot=1000, seed=3)
        assert_numeric_baseline(fixed["ci_low"], 0.2096234763374234)
        dl = bootstrap_interval.bootstrap_pooled_interval(
            [0.1, 0.2, 0.3, 0.4, 0.5], [0.1] * 5, model="dl",
            interval_type="percentile", n_boot=1000, seed=3)
        assert_numeric_baseline(dl["ci_high"], 0.3924652950155946)
        reml = bootstrap_interval.bootstrap_pooled_interval(
            [0.1, 0.2, 0.3, 0.4], [0.1] * 4, model="reml",
            interval_type="basic", n_boot=500, seed=3)
        assert_numeric_baseline(reml["tau2"], 0.006666666528268639, abs_tol=1e-8)


class LegalLargeEffectBoundaryTests(unittest.TestCase):
    """合法大效应边界不得收窄：9.48e153 / 1e150 照常受理且逐字节一致。

    这里的逐字节是结构性的、跨平台成立的，不是同环境巧合：±a 中 |z|~O(1)
    远小于 ulp(a)≈4.9e138 使每个 replicate 的加权和精确归零（IEEE 精确相消
    a+(-a)=+0）；q 的两项相同、加倍无舍入；q−1/tau2−1 被 ulp≈2e292 吸回；
    pooled_se=sqrt(1/2) 输入精确。故全部断言保持 assertEqual，不用容差。
    """

    def test_948e153_boundary_fixed(self):
        with warnings.catch_warnings():
            warnings.simplefilter("error", RuntimeWarning)
            result = bootstrap_interval.bootstrap_pooled_interval(
                [9.48e153, -9.48e153], [1.0, 1.0], model="fixed",
                interval_type="percentile", n_boot=2000, seed=1)
        self.assertEqual(result["interval"], (0.0, 0.0))
        self.assertEqual(result["boot_summary"]["sd"], 0.0)
        self.assertEqual(result["q"], 1.797408e308)
        self.assertEqual(result["pooled_se"], 0.7071067811865476)
        self.assertEqual(result["n_invalid_boot"], 0)
        self.assertEqual(_nonfinite_paths(result), [])
        json.dumps(result, allow_nan=False)

    def test_948e153_boundary_dl(self):
        with warnings.catch_warnings():
            warnings.simplefilter("error", RuntimeWarning)
            result = bootstrap_interval.bootstrap_pooled_interval(
                [9.48e153, -9.48e153], [1.0, 1.0], model="dl",
                interval_type="percentile", n_boot=2000, seed=1)
        self.assertEqual(result["interval"], (0.0, 0.0))
        self.assertEqual(result["tau2"], 1.797408e308)
        self.assertEqual(result["boot_summary"]["sd"], 0.0)
        self.assertEqual(result["tau2_boundary_fraction"], 0.0)
        self.assertEqual(result["n_invalid_boot"], 0)
        self.assertEqual(_nonfinite_paths(result), [])
        json.dumps(result, allow_nan=False)

    def test_1e150_boundary_fixed(self):
        with warnings.catch_warnings():
            warnings.simplefilter("error", RuntimeWarning)
            result = bootstrap_interval.bootstrap_pooled_interval(
                [1e150, -1e150], [1.0, 1.0], model="fixed",
                interval_type="percentile", n_boot=2000, seed=1)
        self.assertEqual(result["interval"], (0.0, 0.0))
        self.assertEqual(result["q"], 1.9999999999999998e300)
        self.assertEqual(result["n_invalid_boot"], 0)
        self.assertEqual(_nonfinite_paths(result), [])
        json.dumps(result, allow_nan=False)


class LargeSeSuccessStrictJsonTests(unittest.TestCase):
    """原「成功但泄漏警告 + boot_summary.sd=inf」域：全部统计量有限且
    strict JSON 可序列化；区间/sd 对修前同环境证据取跨平台基线（house
    helper 默认 rel_tol=1e-11——大 SE 区间是直接算术，无优化器；inf 绝不
    钉为基线，钉的是修后的有限稳定 sd 值）。n_invalid/n_valid 为离散计数、
    tau2_boundary_fraction 为离散计数比，跨平台精确，保持 assertEqual。
    """

    def _ok(self, effects, ses, model, n_boot, seed):
        with warnings.catch_warnings():
            warnings.simplefilter("error", RuntimeWarning)
            result = bootstrap_interval.bootstrap_pooled_interval(
                effects, ses, model=model, interval_type="percentile",
                n_boot=n_boot, seed=seed)
        self.assertEqual(_nonfinite_paths(result), [])
        json.dumps(result, allow_nan=False)  # strict JSON must succeed
        return result

    def test_dl_se_1e153_sd_now_finite(self):
        result = self._ok([1.0, 2.0], [1e153, 1e153], "dl", 500, 1)
        # interval = 修前同环境证据的跨平台基线（接受域不变）
        assert_numeric_baseline(result["interval"],
                                (-1.3454088947258069e+153, 1.1408333451860476e+153))
        self.assertEqual(result["n_invalid_boot"], 0)
        # 修前 boot_summary.sd=inf（strict JSON 失败）；修后稳定归约有限值
        assert_numeric_baseline(result["boot_summary"]["sd"], 6.787812303548636e+152)

    def test_fixed_se_1e153_sd_now_finite(self):
        result = self._ok([1.0, 2.0], [1e153, 1e153], "fixed", 500, 1)
        assert_numeric_baseline(result["interval"],
                                (-1.345408894725807e+153, 1.1408333451860476e+153))
        self.assertEqual(result["n_invalid_boot"], 0)
        assert_numeric_baseline(result["boot_summary"]["sd"], 6.787812303548635e+152)

    def test_dl_se_13e154_dropped_replicates_preserved(self):
        result = self._ok([1.0, 2.0], [1.3e154, 1.3e154], "dl", 500, 1)
        assert_numeric_baseline(result["interval"],
                                (-1.8162945703875735e+154, 1.4779402199534908e+154))
        self.assertEqual((result["n_invalid_boot"], result["boot_summary"]["n_valid"]),
                         (146, 354))
        self.assertEqual(result["tau2_boundary_fraction"], 0.943502824858757)
        assert_numeric_baseline(result["boot_summary"]["sd"], 8.680618380186742e+153)

    def test_fixed_se_13e154_sd_now_finite(self):
        result = self._ok([1.0, 2.0], [1.3e154, 1.3e154], "fixed", 500, 1)
        assert_numeric_baseline(result["interval"],
                                (-1.7490315631435487e+154, 1.483083348741862e+154))
        self.assertEqual(result["n_invalid_boot"], 0)
        assert_numeric_baseline(result["boot_summary"]["sd"], 8.824155994613226e+153)

    def test_reml_se_13e154_rejection_unchanged(self):
        with warnings.catch_warnings():
            warnings.simplefilter("error", RuntimeWarning)
            with self.assertRaises(ValueError) as ctx:
                bootstrap_interval.bootstrap_pooled_interval(
                    [1.0, 2.0], [1.3e154, 1.3e154], model="reml",
                    interval_type="percentile", n_boot=500, seed=1)
        self.assertEqual(str(ctx.exception), _REML_MESSAGE)

    def test_acceptance_sweep_counts_and_intervals_unchanged(self):
        """dl 扫点（修前证据 sweep_min_se_dl）：n_invalid 精确一致，区间取
        修前证据的跨平台基线。"""
        sweep = (
            (1e153, (-1.3454088947258069e+153, 1.1408333451860476e+153), 0),
            (5e153, (-6.740251728722021e+153, 5.704273248605243e+153), 1),
            (9e153, (-1.1966041697528013e+154, 1.0208664715945397e+154), 34),
            (1.1e154, (-1.5383290873344126e+154, 1.2512665730349302e+154), 72),
            (1.2e154, (-1.6765796034346834e+154, 1.3642525107262994e+154), 106),
            (1.34e154, (-1.8781271151763364e+154, 1.521138118197124e+154), 166),
        )
        for se, interval, invalid in sweep:
            with self.subTest(se=se):
                result = self._ok([1.0, 2.0], [se, se], "dl", 500, 1)
                assert_numeric_baseline(result["interval"], interval)
                self.assertEqual(result["n_invalid_boot"], invalid)
                self.assertTrue(math.isfinite(result["boot_summary"]["sd"]))

    def test_stable_sd_matches_independent_reduction(self):
        """稳定 sd 与独立 fsum 归约一致（数值正确，不只是有限）。"""
        import numpy as np

        for ses, model, expected_sd in (([1e153, 1e153], "dl", 6.787812303548636e+152),
                                        ([1.3e154, 1.3e154], "fixed", 8.824155994613226e+153)):
            with self.subTest(ses=ses, model=model):
                y = np.asarray([1.0, 2.0], dtype=float)
                se = np.asarray(ses, dtype=float)
                v = se ** 2
                fit = bootstrap_interval._fit_pooled(y, v, model)
                rng = np.random.default_rng(1)
                pooled_boot, _tau2, _invalid = bootstrap_interval._bootstrap_pooled(
                    rng, y, se, v, model, 500, fit)
                with np.errstate(over="ignore"):  # test-local: the checked precondition itself overflows
                    self.assertFalse(math.isfinite(float(pooled_boot.std(ddof=1))),
                                     msg="前提：朴素归约在这组向量上仍溢出")
                scale = max(abs(value) for value in pooled_boot.tolist())
                scaled = [value / scale for value in pooled_boot.tolist()]
                mean = math.fsum(scaled) / len(scaled)
                independent = scale * math.sqrt(
                    math.fsum((value - mean) ** 2 for value in scaled)
                    / (len(scaled) - 1))
                self.assertLess(abs(expected_sd - independent) / independent, 1e-12)


class FinitePayloadGuardUnitTests(unittest.TestCase):
    """返回点守卫的直接单元行为（合法 null 放行，非有限 float 拒绝）。"""

    def test_legal_null_slots_pass(self):
        payload = {"tau2": None, "tau2_boundary_fraction": None,
                   "mc_se": {"low": None, "high": None},
                   "interval": (0.0, 0.0), "n": 3, "text": "ok"}
        bootstrap_interval._ensure_finite_payload(payload)  # must not raise

    def test_nonfinite_float_is_rejected_with_range_message(self):
        for bad in (float("inf"), float("-inf"), float("nan"),
                    (1.0, float("inf")), {"a": {"b": [float("nan")]}}):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError) as ctx:
                    bootstrap_interval._ensure_finite_payload({"x": bad})
                self.assertEqual(str(ctx.exception), _RANGE_MESSAGE)


if __name__ == "__main__":
    unittest.main()
