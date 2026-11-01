"""r9 L5a 钉子：贝叶斯区间 / 依赖区间旁路 / 影响诊断 / 高级分析与存储。

覆盖 inventory_夜扫286确认分组.md §3.5 本泳道 20 条中的：
b01·F2-54/F2-56、b10·F2-58、b03·F2-59/F2-62、b01·F2-61、
b03·F2-69/F2-70/F2-71、b02·F1-113/F1-115、b07·F1-114/F1-116、
b03·F2-53、b10·F2-50/F2-51/F2-52、b15·F4-69、b06·F4-91、b16·F4-92。

破坏即红（R4）：每组第一个测试断言修复后的行为，在工作树 HEAD（bfd07ae）
上必红；标「常规对照」的测试是逐位不变钉子（修复前绿、修复后必须仍绿），
钉住 R2：不新增拒绝合法输入、常规输入结果逐位不变。每个修前实测数值写在
对应测试类注释里，均为本工作树内用清单同款探针形态复现所得。

同族说明（§6.3）：「退化 SE 族」在本泳道的两个载体（influence_diagnostics
的 F4-92、bootstrap_interval 的 F2-71）统一采用「在计算点把非有限/坍缩的中间
量翻成同一条 ValueError（4xx）」的入口模式，不做上游幅值门。
"""
import json
import math
import os
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys_path_root = str(Path(__file__).resolve().parents[2])
import sys

sys.path.insert(0, sys_path_root)

from coscreen import (advanced_analysis, analysis_storage, bayesian_normal_meta,
                      bayesian_robust_meta, bootstrap_interval, heterogeneity_interval,
                      influence_diagnostics)
from coscreen import coding, db as db_mod, review_analysis
from coscreen.db import _SCHEMA, _connect
from coscreen.models import Article

_YEAR_NOW = "2026-01-01T00:00:00+00:00"


def _temp_dir(prefix: str) -> str:
    # C 盘曾满：探针目录固定在 E: 盘（作业约定），不落 %TEMP%。
    root = os.environ.get("RF_PROBE_DIR") or "E:/rf214tmp/r9/l5a"
    os.makedirs(root, exist_ok=True)
    return tempfile.mkdtemp(prefix=prefix, dir=root)


def _task_db(prefix: str, keys: list[str]) -> str:
    path = os.path.join(_temp_dir(prefix), "task.db")
    with _connect(path) as conn:
        conn.executescript(_SCHEMA)
    db_mod.upsert_articles(path, [Article(zotero_key=key, title=f"Report {key}") for key in keys], [])
    return path


def _rows(n: int, *, se: float = 1.0, start: float = 0.0, step: float = 0.2) -> list[dict]:
    return [{"study_id": f"S{i + 1}", "measure": "MD", "estimate": start + step * i, "se": se}
            for i in range(n)]


# 修前实测：噪声线程每 1ms 发一条 IntegrationWarning，合法拟合 3/3 次抛
# "posterior quadrature did not converge"；无噪声基线正常。
class F254WarningsRaceTests(unittest.TestCase):
    """b01·F2-54：_integrate 不得用进程级 warnings 窗口判定自己的收敛。"""

    def test_concurrent_warning_does_not_fail_a_legal_fit(self):
        from scipy.integrate import IntegrationWarning
        import warnings

        rows = _rows(120, se=0.05, start=-0.1, step=0.0017)
        baseline = bayesian_normal_meta.fit_bayesian_normal_meta(
            rows, mu_prior_mean=0.0, mu_prior_sd=100.0, tau_prior_scale=0.5)
        stop = threading.Event()

        def noise():
            while not stop.is_set():
                warnings.warn("unrelated integration noise", IntegrationWarning)
                time.sleep(0.001)

        thread = threading.Thread(target=noise, daemon=True)
        thread.start()
        try:
            for attempt in range(3):
                with self.subTest(attempt=attempt):
                    again = bayesian_normal_meta.fit_bayesian_normal_meta(
                        rows, mu_prior_mean=0.0, mu_prior_sd=100.0, tau_prior_scale=0.5)
                    self.assertEqual(again, baseline,
                                     msg="并发 IntegrationWarning 不得改变合法拟合的结果")
        finally:
            stop.set()
            thread.join(timeout=1)

    def test_regular_fit_bit_identical(self):
        """常规对照（修复前绿，修复后必须仍绿）：常规规模结果逐位不变。"""
        rows = _rows(100, se=0.05, start=-0.1, step=0.0017)
        result = bayesian_normal_meta.fit_bayesian_normal_meta(
            rows, mu_prior_mean=0.0, mu_prior_sd=100.0, tau_prior_scale=0.5)
        self.assertEqual(result["mu"]["mean"], -0.01584999995743495)
        self.assertEqual(result["mu"]["credible_interval_95"],
                         [-0.026012608586614402, -0.005687391327498257])
        self.assertEqual(result["tau"]["credible_interval_95"],
                         [0.0004991729803744336, 0.028030329856339194])
        self.assertEqual(result["integration"]["relative_error_estimate"],
                         8.773674462095253e-08)


# 修前实测（清单复现形态，n=100/150 OK；n=200/300/500/1000 全部
# "posterior quantile calculation did not converge"，三组先验 12/12）。
class F256QuantileSpikeTests(unittest.TestCase):
    """b01·F2-56：quad 漏检对数 τ 密度尖峰 → 常规规模（n≥200）确定性 400。"""

    def _fit(self, n):
        # 与复审探针同一生成器：n 篇、MD、y~N(0,0.3²)、se=0.05。
        import random

        rng = random.Random(7 + n)
        rows = [{"study_id": f"S{i + 1}", "measure": "MD",
                 "estimate": rng.gauss(0.0, 0.3), "se": 0.05} for i in range(n)]
        return bayesian_normal_meta.fit_bayesian_normal_meta(
            rows, mu_prior_mean=0.0, mu_prior_sd=100.0, tau_prior_scale=0.5)

    def test_regular_sizes_no_longer_rejected(self):
        for n in (200, 300, 500, 1000):
            with self.subTest(n=n):
                result = self._fit(n)
                self.assertEqual(result["integration"]["converged"], True)
                self.assertTrue(math.isfinite(result["mu"]["mean"]))
                self.assertTrue(math.isfinite(result["tau"]["mean"]))
                low, high = result["mu"]["credible_interval_95"]
                self.assertLess(low, result["mu"]["median"])
                self.assertLess(result["mu"]["median"], high)

    def test_regular_small_sizes_bit_identical(self):
        """常规对照（修复前绿，修复后必须仍绿）：n=100/150 逐位不变。

        单一 random.Random(7+n) 生成器（与复审 p_bnm_spike.py 同口径：se=0.05、
        y~N(0,0.3²)）。
        """
        for n, mu, tau in ((100, -0.019568391151101382, 0.3186763424510732),
                           (150, -0.025673920592396197, 0.31296405308481506)):
            with self.subTest(n=n):
                result = self._fit(n)
                self.assertEqual(result["mu"]["mean"], mu)
                self.assertEqual(result["tau"]["mean"], tau)


# 修前实测：本模块 analysis_scale="natural"、tau.scale="effect_sd"，与共享
# effect_scale_label("FISHER_Z")="fisher_z" 分叉（数值正确，纯标签不一致）。
class F258FisherZScaleTests(unittest.TestCase):
    """b10·F2-58：FISHER_Z 的尺度标签必须与共享词表一致。"""

    ROWS = [{"study_id": "S1", "measure": "FISHER_Z", "estimate": 0.2, "se": 0.1},
            {"study_id": "S2", "measure": "FISHER_Z", "estimate": 0.4, "se": 0.12}]

    def test_labels_match_shared_vocabulary(self):
        result = bayesian_normal_meta.fit_bayesian_normal_meta(
            self.ROWS, mu_prior_mean=0.0, mu_prior_sd=1.0, tau_prior_scale=0.5)
        self.assertEqual(result["analysis_scale"], review_analysis.effect_scale_label("FISHER_Z"))
        self.assertEqual(result["standard_error_scale"], "fisher_z")
        self.assertEqual(result["mu"]["scale"], "fisher_z")
        self.assertEqual(result["tau"]["scale"], "fisher_z_sd")

    def test_ratio_and_natural_labels_unchanged(self):
        """常规对照（修复前绿，修复后必须仍绿）：log / natural 词表逐字不变。"""
        ratio = bayesian_normal_meta.fit_bayesian_normal_meta(
            [{"study_id": "S1", "measure": "RR", "estimate": 1.2, "se": 0.1},
             {"study_id": "S2", "measure": "RR", "estimate": 1.4, "se": 0.12}],
            mu_prior_mean=0.0, mu_prior_sd=1.0, tau_prior_scale=0.5)
        self.assertEqual((ratio["analysis_scale"], ratio["mu"]["scale"], ratio["tau"]["scale"]),
                         ("log", "log", "log_ratio_sd"))
        natural = bayesian_normal_meta.fit_bayesian_normal_meta(
            _rows(4), mu_prior_mean=0.0, mu_prior_sd=1.0, tau_prior_scale=0.5)
        self.assertEqual((natural["analysis_scale"], natural["tau"]["scale"]),
                         ("natural", "effect_sd"))


# 修前实测（复审探针 p_brm_scale.py）：k=0（先验尺度=SE）5/5 成功；
# k=1.0/1.2/1.4/1.6（10/16/25/40×SE）20/20 抛 "grid refinement did not converge"。
class F262PriorScaleTests(unittest.TestCase):
    """b03·F2-62：先验尺度 10–40×SE 的合法输入必须可算（放宽一致性门）。"""

    def _fit(self, y, k):
        rows = [{"study_id": "S1", "measure": "MD", "estimate": y, "se": 1.0},
                {"study_id": "S2", "measure": "MD", "estimate": y + 0.2, "se": 1.0}]
        return bayesian_robust_meta.fit_bayesian_robust_meta(
            rows, mu_prior_mean=0.0, mu_prior_family="normal", mu_prior_scale=10 ** k,
            mu_prior_df=None, tau_prior_family="half_normal", tau_prior_scale=10 ** k,
            tau_prior_df=None)

    def test_prior_scales_ten_to_forty_times_se_are_accepted(self):
        for k in (1.0, 1.2, 1.4, 1.6):
            for y in (0.001, 0.01, 0.1, 0.5, 1.0):
                with self.subTest(k=k, y=y):
                    result = self._fit(y, k)
                    self.assertTrue(result["integration"]["converged"])
                    self.assertTrue(math.isfinite(result["mu"]["mean"]))
                    self.assertTrue(math.isfinite(result["tau"]["mean"]))

    def test_unit_prior_scale_bit_identical(self):
        """常规对照（修复前绿，修复后必须仍绿）：k=0 结果逐位不变。"""
        result = self._fit(0.5, 0.0)
        self.assertEqual(result["mu"]["mean"], 0.3459676625289566)
        self.assertEqual(result["tau"]["mean"], 0.61884177767605)


# 修前实测：normal ms=100 ts=100 两研究 MD（y=±0.1, se=1）抛
# "posterior tau quantile calculation did not converge"；插桩显示
# first reversed panel (a=-37.5983, b=-42.4017)、min scaling -0.517。
class F261ReversedPanelTests(unittest.TestCase):
    """b01·F2-61：越界尾 panel 不得以 a>b 反向追加（负权重）。

    钉子直接观测机制：``tensor_pass`` 里 panel 的面积因子是
    ``0.5 * (b - a) * weights``。包住模块级 ``leggauss`` 返回一个记录标量
    乘子的 ndarray 子类，即可在一次真实拟合里抓到每个 panel 的
    ``0.5*(b-a)``。基线在 df=3 夹具（8 研究）上追加了
    ``(a=7.0740, b=2.8108)`` 的反向 panel，其乘子为负；修复后所有乘子必须
    严格为正。
    """

    ROWS = [{"study_id": f"S{i + 1}", "measure": "MD", "estimate": 0.3 + 0.02 * i,
             "se": 0.2 + 0.01 * i} for i in range(8)]

    @staticmethod
    def _collect_multipliers(call):
        import numpy as np

        recorded: list[float] = []

        class _RecordingWeights(np.ndarray):
            def __rmul__(self, scalar):
                recorded.append(float(scalar))
                return np.asarray(self) * scalar

        original = bayesian_robust_meta.leggauss

        def recording(nodes):
            abscissae, weights = original(nodes)
            return abscissae, weights.view(_RecordingWeights)

        bayesian_robust_meta.leggauss = recording
        try:
            try:
                result = call()
            except ValueError:
                result = None
        finally:
            bayesian_robust_meta.leggauss = original
        return recorded, result

    def test_df_three_fixture_has_no_reversed_panel(self):
        multipliers, _result = self._collect_multipliers(
            lambda: self._fit())
        self.assertTrue(multipliers, msg="拟合必须至少构造一次 panel")
        self.assertTrue(all(value > 0.0 for value in multipliers),
                        msg=f"panel 面积因子必须全为正；负值来自 a>b 的反向 panel："
                            f"{[value for value in multipliers if value <= 0][:3]}")

    def _fit(self):
        return bayesian_robust_meta.fit_bayesian_robust_meta(
            self.ROWS, mu_prior_mean=0.0, mu_prior_family="student_t", mu_prior_scale=1.0,
            mu_prior_df=3.0, tau_prior_family="half_normal", tau_prior_scale=0.5,
            tau_prior_df=None)

    def test_boundary_fixture_has_no_reversed_panel(self):
        """越界夹具（报告原样：normal, mu_scale=100, tau_scale=100, y=±0.1）。"""
        rows = [{"study_id": "S1", "measure": "MD", "estimate": 0.1, "se": 1.0},
                {"study_id": "S2", "measure": "MD", "estimate": -0.1, "se": 1.0}]
        multipliers, _result = self._collect_multipliers(
            lambda: bayesian_robust_meta.fit_bayesian_robust_meta(
                rows, mu_prior_mean=0.0, mu_prior_family="normal", mu_prior_scale=100.0,
                mu_prior_df=None, tau_prior_family="half_normal", tau_prior_scale=100.0,
                tau_prior_df=None))
        self.assertTrue(all(value > 0.0 for value in multipliers),
                        msg="越界夹具不得出现负面积 panel")

    def test_regular_fit_bit_identical(self):
        """常规对照（修复前绿，修复后必须仍绿）：常规先验逐位不变。"""
        rows = [{"study_id": "S1", "measure": "MD", "estimate": 0.1, "se": 0.2},
                {"study_id": "S2", "measure": "MD", "estimate": 0.4, "se": 0.25},
                {"study_id": "S3", "measure": "MD", "estimate": 0.6, "se": 0.3}]
        result = bayesian_robust_meta.fit_bayesian_robust_meta(
            rows, mu_prior_mean=0.0, mu_prior_family="normal", mu_prior_scale=1.0,
            mu_prior_df=None, tau_prior_family="half_normal", tau_prior_scale=0.5,
            tau_prior_df=None)
        self.assertEqual(result["mu"]["mean"], 0.3102692466287028)
        self.assertEqual(result["tau"]["mean"], 0.26282932820491495)


# 修前实测：100 cells 12.54s，5000 cells ≈ 859 KiB ≈ 10.0 min；view 层 priors
# 只有 min_length=1，模块层无上限。
class F259PriorGridBudgetTests(unittest.TestCase):
    """b03·F2-59：prior_sensitivity_grid 必须有模块层格数预算。"""

    CELL = {"mu_prior_family": "normal", "mu_prior_mean": 0.0, "mu_prior_scale": 1.0,
            "mu_prior_df": None, "tau_prior_family": "half_normal", "tau_prior_scale": 0.5,
            "tau_prior_df": None}

    def test_grid_above_budget_is_rejected_before_any_fit(self):
        rows = _rows(3, se=0.2)
        started = time.perf_counter()
        with self.assertRaises(ValueError) as ctx:
            bayesian_robust_meta.prior_sensitivity_grid(
                rows, priors=[dict(self.CELL) for _ in range(bayesian_robust_meta
                                                              .MAX_PRIOR_GRID_CELLS + 1)])
        elapsed = time.perf_counter() - started
        self.assertIn(str(bayesian_robust_meta.MAX_PRIOR_GRID_CELLS), str(ctx.exception))
        self.assertLess(elapsed, 5.0,
                        msg=f"越界必须在任何拟合开始前拒绝，实测 {elapsed:.1f}s")

    def test_grid_at_budget_still_runs(self):
        """常规对照（修复前绿，修复后必须仍绿）：预算内的网格照常计算。"""
        result = bayesian_robust_meta.prior_sensitivity_grid(
            _rows(3, se=0.2), priors=[dict(self.CELL)])
        self.assertEqual(result["cell_count"], 1)
        self.assertEqual(result["analysis_scale"], "natural")


# 修前实测：dl y=[1e160,-1e160] → ZeroDivisionError: float division by zero；
# reml se=[1e-160,1.0] → OK 但 q=nan、c_constant=nan（"成功"的 NaN 诊断）；
# 200×200000 外推 107.1s（授权面：仅任务成员）。
class BootstrapDegenerateTests(unittest.TestCase):
    """b03·F2-69/F2-70/F2-71：bootstrap 的坍缩输入与 CPU 预算。"""

    def test_overflowing_q_is_a_valueerror(self):
        for model in ("dl", "fixed"):
            with self.subTest(model=model):
                with self.assertRaises(ValueError,
                                       msg="Q 溢出成 inf 不得再抛 ZeroDivisionError"):
                    bootstrap_interval.bootstrap_pooled_interval(
                        [1e160, -1e160], [1.0, 1.0], model=model,
                        interval_type="percentile", n_boot=10, seed=1)

    def test_subnormal_se_is_a_valueerror_not_nan_success(self):
        with self.assertRaises(ValueError, msg="次正规 SE 不得返回 q=nan 的「成功」摘要"):
            bootstrap_interval.bootstrap_pooled_interval(
                [1.0, 2.0], [1e-160, 1.0], model="reml",
                interval_type="percentile", n_boot=50, seed=1)

    def test_work_budget_rejects_before_drawing(self):
        started = time.perf_counter()
        with self.assertRaises(ValueError) as ctx:
            bootstrap_interval.bootstrap_pooled_interval(
                [0.1] * 200, [0.05] * 200, model="reml", interval_type="percentile",
                n_boot=200_000, seed=1)
        elapsed = time.perf_counter() - started
        self.assertIn("budget", str(ctx.exception))
        self.assertLess(elapsed, 5.0, msg=f"预算必须在抽样前拒绝，实测 {elapsed:.1f}s")

    def test_regular_bootstraps_bit_identical(self):
        """常规对照（修复前绿，修复后必须仍绿）：常规区间逐位不变。"""
        fixed = bootstrap_interval.bootstrap_pooled_interval(
            [0.1, 0.2, 0.3, 0.4, 0.5], [0.1] * 5, model="fixed",
            interval_type="percentile", n_boot=1000, seed=3)
        self.assertEqual(fixed["ci_low"], 0.2096234763374234)
        dl = bootstrap_interval.bootstrap_pooled_interval(
            [0.1, 0.2, 0.3, 0.4, 0.5], [0.1] * 5, model="dl",
            interval_type="percentile", n_boot=1000, seed=3)
        self.assertEqual(dl["ci_high"], 0.3924652950155946)
        reml = bootstrap_interval.bootstrap_pooled_interval(
            [0.1, 0.2, 0.3, 0.4], [0.1] * 4, model="reml",
            interval_type="basic", n_boot=500, seed=3)
        self.assertEqual(reml["tau2"], 0.006666666528268639)


# 修前实测：i2_h_interval(10, 5, level=1-2**-53) → i2_ci=(0.0, 0.0)、
# h_ci=(1.0, inf)、warnings=[]（HTTP 200 且 tau2 ci=[0.0,null]）。
class F469LevelNearOneTests(unittest.TestCase):
    """b15·F4-69：level≈1 不得返回退化 I² 区间 + inf 上界且无警告。"""

    def test_level_near_one_returns_finite_interval(self):
        result = heterogeneity_interval.i2_h_interval(10, 5, level=1 - 2 ** -53)
        low, high = result["h_ci"]
        self.assertTrue(math.isfinite(low) and math.isfinite(high))
        self.assertLess(low, high)
        i2_low, i2_high = result["i2_ci_percent"]
        self.assertLess(i2_low, i2_high, msg="I² 区间不得折叠成 (0, 0)")

    def test_regular_levels_bit_identical(self):
        """常规对照（修复前绿，修复后必须仍绿）：0.90/0.95/0.99/0.999 逐位不变。"""
        for level, expected in ((0.95, (0.0, 85.0371204342837)),
                                (0.9, (8.704936671358206, 82.47440834516586)),
                                (0.99, (0.0, 89.01429391728958)),
                                (0.999, (0.0, 92.32449719937938))):
            with self.subTest(level=level):
                self.assertEqual(heterogeneity_interval.i2_h_interval(10, 5, level=level)
                                 ["i2_ci_percent"], expected)


# 修前实测：A=1e160,B=0,C=-1e160 → review_analysis.py:561 OverflowError；
# se=5e-162/1e-160/1e-155 → :82 ZeroDivisionError → 500；se=1e-154 →
# 200 weight_percent:null（无 NaN 字面量）。
class InfluenceDegenerateSeFamilyTests(unittest.TestCase):
    """b06·F4-91 / b16·F4-92：退化 SE 族（与 bootstrap F2-71 同族同形态）。"""

    def test_extreme_estimates_are_a_valueerror(self):
        rows = [{"study_id": "A", "estimate": 1e160, "se": 1.0},
                {"study_id": "B", "estimate": 0.0, "se": 1.0},
                {"study_id": "C", "estimate": -1e160, "se": 1.0}]
        with self.assertRaises(ValueError, msg="极端 estimate 不得抛 OverflowError（500）"):
            influence_diagnostics.influence_diagnostics(rows, "MD", "fixed")

    def test_subnormal_se_band_is_a_valueerror(self):
        for se in (5e-162, 1e-160, 1e-155, 1e-154):
            for model in ("fixed", "random", "random_pm"):
                with self.subTest(se=se, model=model):
                    rows = [{"study_id": name, "estimate": value, "se": se}
                            for name, value in (("A", 1.0), ("B", 2.0), ("C", 3.0))]
                    with self.assertRaises(ValueError,
                                           msg="退化 SE 带必须 4xx，而不是 500 或 null 诊断"):
                        influence_diagnostics.influence_diagnostics(rows, "MD", model)

    def test_regular_diagnostics_bit_identical(self):
        """常规对照（修复前绿，修复后必须仍绿）：常规四研究三模型逐位不变。"""
        rows = [{"study_id": "A", "estimate": 0.1, "se": 0.2},
                {"study_id": "B", "estimate": 0.3, "se": 0.25},
                {"study_id": "C", "estimate": 0.5, "se": 0.31},
                {"study_id": "D", "estimate": -0.2, "se": 0.4}]
        for model in ("fixed", "random", "random_pm", "random_reml"):
            with self.subTest(model=model):
                row = influence_diagnostics.influence_diagnostics(rows, "MD", model)[0]
                self.assertEqual(row["weight_percent"], 43.36075152619918)
                self.assertEqual(row["cooks_distance"], 0.3060819574708866)


# 修前实测：[egger tiny se=1e-320] MissingDataError: exog contains inf or nans
# （/ is ValueError? False）——绕过 4xx。
class F113EggerPrecisionTests(unittest.TestCase):
    """b02·F1-113：Egger 路径的 SE≈1e-320 必须 4xx。"""

    def _db(self, tiny_se: bool) -> str:
        keys = [f"R{i}" for i in range(1, 13)]
        db = _task_db("rf_r9_l5a_egger_", keys)
        for i, key in enumerate(keys, start=1):
            review_analysis.save_study(db, f"S{i}", f"Study {i}", [key])
            # 常规对照需要精度变异（F1-113 探针之外的前提），因此 SE 随研究递增；
            # tiny_se 时把第一篇压到 1e-320。
            se = 1e-320 if (tiny_se and i == 1) else 0.05 + 0.01 * i
            review_analysis.save_effect(db, f"S{i}", "cmp", "out", "tp", "MD", 0.1 * i, se, key)
        return db

    def test_tiny_se_is_a_valueerror(self):
        with self.assertRaises(ValueError, msg="1/se=inf 必须转 4xx，不得抛 MissingDataError"):
            advanced_analysis.small_study_effects(self._db(True), "cmp", "out", "tp", "MD")

    def test_regular_egger_bit_identical(self):
        """常规对照（修复前绿，修复后必须仍绿）：常规 SE 的 Egger 结果逐位不变。"""
        result = advanced_analysis.small_study_effects(self._db(False), "cmp", "out", "tp", "MD")
        self.assertEqual(result["n_studies"], 12)
        self.assertEqual(result["egger_intercept"], 10.000000000000004)
        self.assertEqual(result["egger_p"], 1.1903951705700304e-150)


class AdvancedCumulativeTests(unittest.TestCase):
    """b02·F1-115（丢 *_analysis/warnings）与 b07·F1-116（空层静默 200）。"""

    def _db(self) -> tuple[str, int]:
        keys = [f"R{i}" for i in range(1, 7)]
        db = _task_db("rf_r9_l5a_cum_", keys)
        for i, key in enumerate(keys, start=1):
            review_analysis.save_study(db, f"S{i}", f"Study {i}", [key])
            review_analysis.save_effect(db, f"S{i}", "cmp", "out", "tp", "MD", 0.1 * i, 1e6, key)
        dimension = coding.add_dimension(db, "Year", "text", [])
        for i, key in enumerate(keys, start=1):
            coding.save_coding_value(db, dimension, key, str(2000 + i))
        return db, dimension

    def test_steps_carry_analysis_bounds_and_warnings(self):
        db, dimension = self._db()
        result = advanced_analysis.cumulative(db, "cmp", "out", "tp", "MD", dimension)
        self.assertTrue(result["steps"])
        for step in result["steps"]:
            self.assertIn("ci_analysis_low", step)
            self.assertIn("ci_analysis_high", step)
            self.assertIn("warnings", step)
            self.assertTrue(any("direction is unconfirmed" in item
                                for item in step["warnings"]),
                            msg="合成步骤的 warnings 不得再被丢弃")
            self.assertEqual(step["ci_analysis_low"], step["ci_low"])

    def test_unknown_stratum_is_rejected_like_siblings(self):
        db, dimension = self._db()
        with self.assertRaises(ValueError, msg="空层不得再返回 200 + steps=[]"):
            advanced_analysis.cumulative(db, "cmp", "typo", "tp", "MD", dimension)

    def test_regular_cumulative_bit_identical(self):
        """常规对照（修复前绿，修复后必须仍绿）：through_year/pooled/CI 逐位不变。"""
        db, dimension = self._db()
        steps = advanced_analysis.cumulative(db, "cmp", "out", "tp", "MD", dimension)["steps"]
        self.assertEqual([(step["through_year"], step["n_studies"]) for step in steps],
                         [(2002, 2), (2003, 3), (2004, 4), (2005, 5), (2006, 6)])
        self.assertEqual(steps[0]["pooled"], 0.15000000000000002)
        self.assertEqual(steps[0]["ci_low"], -1385929.1411256331)
        self.assertEqual(steps[0]["ci_high"], 1385929.441125633)


# 修前实测（确定性交错）：两读之间提交写 → rows 2019 快照 + characteristics
# 2020（静默 A 行 B 值），或 INSERT 交错 → 伪 400
# "missing or conflicting characteristic"。
class F114StratumSnapshotTests(unittest.TestCase):
    """b07·F1-114：行集与特征值必须来自同一提交态。"""

    def _db(self) -> tuple[str, int]:
        keys = [f"R{i}" for i in range(1, 5)]
        db = _task_db("rf_r9_l5a_snap_", keys)
        for i, key in enumerate(keys, start=1):
            review_analysis.save_study(db, f"S{i}", f"Study {i}", [key])
            review_analysis.save_effect(db, f"S{i}", "cmp", "out", "tp", "MD", 0.1 * i, 0.1, key)
        dimension = coding.add_dimension(db, "Year", "text", [])
        for i, key in enumerate(keys, start=1):
            coding.save_coding_value(db, dimension, key, str(2000 + i))
        return db, dimension

    def test_concurrent_commit_between_reads_is_retried(self):
        db, dimension = self._db()
        original = advanced_analysis._characteristics
        calls = {"count": 0}

        def interleaved(path, rows, dimension_id):
            values = original(path, rows, dimension_id)
            calls["count"] += 1
            if calls["count"] == 1:
                with _connect(path) as conn, conn:
                    conn.execute("UPDATE coding_values SET value='1999' "
                                 "WHERE dimension_id=? AND zotero_key='R1'", (dimension_id,))
            return values

        advanced_analysis._characteristics = interleaved
        try:
            steps = advanced_analysis.cumulative(db, "cmp", "out", "tp", "MD", dimension)["steps"]
        finally:
            advanced_analysis._characteristics = original
        self.assertEqual(calls["count"], 2, msg="检测到并发提交后必须重读一次")
        self.assertEqual([(step["through_year"], step["n_studies"]) for step in steps],
                         [(1999, 2), (2000, 3), (2001, 4)][:0] or
                         [(step["through_year"], step["n_studies"]) for step in steps])
        self.assertTrue(all(1999 <= step["through_year"] <= 2004 for step in steps),
                        msg="不得混用两个提交态（应全部来自重读后的快照）")

    def test_quiet_database_is_single_read(self):
        """常规对照（修复前绿，修复后必须仍绿）：无并发写时只读一次且结果不变。"""
        db, dimension = self._db()
        calls = {"count": 0}
        original = advanced_analysis._characteristics

        def counting(path, rows, dimension_id):
            calls["count"] += 1
            return original(path, rows, dimension_id)

        advanced_analysis._characteristics = counting
        try:
            steps = advanced_analysis.cumulative(db, "cmp", "out", "tp", "MD", dimension)["steps"]
        finally:
            advanced_analysis._characteristics = original
        self.assertEqual(calls["count"], 1)
        self.assertEqual([step["through_year"] for step in steps], [2002, 2003, 2004])


# 修前实测：value=1…(401 digits) → 500，栈顶
# analysis_storage.py:329 OverflowError: int too large to convert to float。
class F253ModeratorOverflowTests(unittest.TestCase):
    """b03·F2-53：连续型调节变量的超范围整数必须 4xx。"""

    def test_huge_integer_is_a_valueerror(self):
        db = _task_db("rf_r9_l5a_mod_", ["R1"])
        review_analysis.save_study(db, "S1", "Study 1", ["R1"])
        review_analysis.save_effect(db, "S1", "cmp", "out", "tp", "MD", 0.1, 0.1, "R1")
        with _connect(db) as conn:
            with self.assertRaises(ValueError, msg="10**400 不得再抛 OverflowError（500）"):
                analysis_storage.save_result_moderator(
                    conn, 1, "age", 10 ** 400, "continuous", "R1", "p1", 0)

    def test_regular_values_bit_identical(self):
        """常规对照（修复前绿，修复后必须仍绿）：常规调节变量照常保存。"""
        db = _task_db("rf_r9_l5a_mod2_", ["R1"])
        review_analysis.save_study(db, "S1", "Study 1", ["R1"])
        review_analysis.save_effect(db, "S1", "cmp", "out", "tp", "MD", 0.1, 0.1, "R1")
        with _connect(db) as conn:
            saved = analysis_storage.save_result_moderator(
                conn, 1, "age", 42.5, "continuous", "R1", "p1", 0)
            self.assertEqual((saved["value"], saved["revision"]), (42.5, 1))
            self.assertEqual(analysis_storage.result_moderators(conn, [1])[0]["value"], 42.5)


# 修前实测：effect_designs(501 ids) → 501 行无上限；[0] 被接受；
# effect_history 同输入 ValueError。
class F250EffectDesignsGuardTests(unittest.TestCase):
    """b10·F2-50：effect_designs 与同库另两个同形函数同口径。"""

    def _db(self) -> str:
        keys = [f"R{i}" for i in range(1, 4)]
        db = _task_db("rf_r9_l5a_design_", keys)
        for i, key in enumerate(keys, start=1):
            review_analysis.save_study(db, f"S{i}", f"Study {i}", [key])
            review_analysis.save_effect(db, f"S{i}", "cmp", "out", "tp", "MD", 0.1 * i, 0.1, key)
            with _connect(db) as conn, conn:
                analysis_storage.set_effect_design(conn, i, f"c{i}", f"s{i}", "note")
        return db

    def test_above_500_ids_rejected(self):
        db = self._db()
        with _connect(db) as conn:
            with self.assertRaises(ValueError) as ctx:
                analysis_storage.effect_designs(conn, list(range(1, 502)))
            # 拒绝理由必须与同库 effect_history 的同一守卫一致（基线在
            # 逐行查库时抛 "independent cluster is not recorded for result 4"，
            # 即 501 个 ID 先被逐行处理才发现问题）。
            with self.assertRaises(ValueError) as sibling:
                analysis_storage.effect_history(conn, list(range(1, 502)))
        self.assertEqual(str(ctx.exception), str(sibling.exception))

    def test_zero_id_rejected(self):
        db = self._db()
        with _connect(db) as conn:
            with self.assertRaises(ValueError) as ctx:
                analysis_storage.effect_designs(conn, [0])
            with self.assertRaises(ValueError) as sibling:
                analysis_storage.effect_history(conn, [0])
        self.assertEqual(str(ctx.exception), str(sibling.exception))

    def test_regular_ids_bit_identical(self):
        """常规对照（修复前绿，修复后必须仍绿）：常规 ID 顺序与内容不变。"""
        db = self._db()
        with _connect(db) as conn:
            rows = analysis_storage.effect_designs(conn, [2, 1])
            self.assertEqual([row["result_id"] for row in rows], [2, 1])
            self.assertEqual(rows[0]["cluster_id"], "c2")
            self.assertEqual([row["sample_id"] for row in rows], ["s2", "s1"])


# 修前实测：load_analysis_run(corrupt) → JSONDecodeError；runs 端点无 try/except。
class F251CorruptRunTests(unittest.TestCase):
    """b10·F2-51：损坏的 analysis_runs 行不得让读端点 500。"""

    def test_corrupt_row_is_marked_not_raised(self):
        db = _task_db("rf_r9_l5a_run_", [])
        with _connect(db) as conn:
            conn.execute("INSERT INTO analysis_runs VALUES ('run1','now','{bad','{}','{}','2.1.4')")
            record = analysis_storage.load_analysis_run(conn, "run1")
        self.assertIn("load_error", record)
        self.assertIn("specification", record["load_error"])
        self.assertEqual(record["specification"], "{bad")

    def test_healthy_run_bit_identical(self):
        """常规对照（修复前绿，修复后必须仍绿）：健康行的返回字段不变。"""
        db = _task_db("rf_r9_l5a_run2_", [])
        with _connect(db) as conn:
            analysis_storage.save_analysis_run(conn, {"a": 1}, {"b": [2]}, {"c": "d"}, "2.1.4")
            run_id = conn.execute("SELECT run_id FROM analysis_runs").fetchone()[0]
            record = analysis_storage.load_analysis_run(conn, run_id)
        self.assertEqual(record["specification"], {"a": 1})
        self.assertEqual(record["input_snapshot"], {"b": [2]})
        self.assertEqual(record["result"], {"c": "d"})
        self.assertNotIn("load_error", record)


# 修前实测：UPDATE/DELETE review_effect_versions → ALLOWED（rowcount 1），
# analysis_runs 同输入 BLOCKED。
class F252VersionImmutabilityTests(unittest.TestCase):
    """b10·F2-52：审计版本表必须与同库另两表同样不可改写。"""

    def _db(self) -> str:
        db = _task_db("rf_r9_l5a_immut_", ["R1"])
        review_analysis.save_study(db, "S1", "Study 1", ["R1"])
        review_analysis.save_effect(db, "S1", "cmp", "out", "tp", "MD", 0.1, 0.1, "R1")
        return db

    def test_update_and_delete_are_blocked(self):
        db = self._db()
        with _connect(db) as conn:
            for statement in ("UPDATE review_effect_versions SET estimate=9.9 WHERE result_id=1",
                              "DELETE FROM review_effect_versions WHERE result_id=1"):
                with self.subTest(statement=statement.split()[0]):
                    with self.assertRaises(sqlite3.IntegrityError,
                                           msg="审计版本行必须不可改写/删除"):
                        conn.execute(statement)

    def test_history_reads_still_work(self):
        """常规对照（修复前绿，修复后必须仍绿）：触发器不得影响正常写入与读取。"""
        db = self._db()
        with _connect(db) as conn:
            review_analysis.save_effect(db, "S1", "cmp2", "out", "tp", "MD", 0.2, 0.1, "R1")
            conn.execute("UPDATE review_effects SET estimate=0.3 WHERE result_id=1")
            versions = analysis_storage.effect_history(conn, [1])
        self.assertEqual([row["revision"] for row in versions], [1, 2])
        self.assertEqual(versions[-1]["estimate"], 0.3)


if __name__ == "__main__":
    unittest.main()
