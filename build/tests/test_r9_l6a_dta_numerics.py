"""r9 L6a 泳道钉子：DTA 家族（dta_analysis / dta_hsroc / dta_likelihood_ratios /
dta_prediction_region / dta_threshold_model）。

覆盖清单 §3.6 的本泳道条目（E:/mubi-reviewflow/out/release_plan/inventory_夜扫286确认分组.md，
行号按开工时 bfd07ae 读取）。每条在基线 bfd07ae 上实测为红（修前形态见各类首注释），
修复后为绿；标「逐位对照」的用例钉住基线返回值，修复后必须仍逐位相等（R1/R2：
常规输入结果不变、不新增对合法输入的拒绝）。

- b05·F3-92  save_result 用未 strip 的 study_id/source_key 做链接检查与 INSERT，
  带空白的合法 ID 被误导性拒成 "source report must be linked"；
- b13·F3-94  synthesize 静默过滤不存在的 threshold/reference_standard，真因被
  「至少五研究」掩盖；
- b13·F3-96  bivariate_from_hsroc 对 |beta|>=1420 抛 OverflowError（1419 为 ValueError）；
- b13·F3-97  派生均值溢出 inf 后误报 RuntimeError "numerically unstable"；
- b14·F3-99  逆向只查方差为正，可产出回喂正向即被拒的参数（域不对称）；
- b11·F3-100 round-trip 绝对容差 1e-8 把合法大数（1e9）判成「数值失稳」；
- b05·F4-2   逐研究方差下溢 0.0 → 1/variance ZeroDivisionError → 端点 500；
- b14·F4-3   计数无上界，10**400 直传 float() 抛 OverflowError（非 ValueError）；
- b14·F4-4   n_samples 只查正整数无上限，1e9 触发约 15 GiB 采样分配；
- b06·F4-7   n_samples>0 且无协方差时 validated=None 解包抛 TypeError（非 ValueError）；
- b15·F4-5   正定性判据 vs*vc-cov² 在 1e308 量级得 inf-inf=nan，nan<=0 为 False 放行；
- b14·F4-10  tau 无上界，1e154 返回饱和退化区域且无任何 warning；
- b14·F4-11  _finite_float 静默接受数值字符串，类型错误与非有限同一文案；
- b14·F4-13  闭合多边形复制首点使 len(points)=121 ≠ 120；
- b14·F4-14  简并 2×2 行（tp+fn=0）在共享读表函数里抛裸 ZeroDivisionError → 500；
- b14·F4-16  threshold_by_study 键不剥离空白，存在的研究被误判 unknown；
- b14·F4-17  同一研究两条 threshold 文本解析为同一数值时不选/照做均 400（无出口）。

同族修法形态（清单 §6.3）：本泳道只有退化 SE 族的载体（F4-2）——统一入口
``_positive_variances`` 在逆方差权重前做有限/正性筛查，与
``review_analysis._screened_inverse_variance_weights`` 同形（同一「supported
numeric range」拒绝口径）。其余条目不属于四个指定族。
"""
import math
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

#: HTTP 面的数据目录必须在导入 custom_backend 之前定好（同 test_r9_l6d1 手法），
#: 否则端点会写到仓库 data/ 并与其他测试文件的用户名撞车。
_HTTP_FIXTURE = tempfile.TemporaryDirectory(prefix="rf_r9_l6a_http_",
                                            ignore_cleanup_errors=True)
os.environ["COBOOKSHELF_DATA_DIR"] = _HTTP_FIXTURE.name
os.environ["REVIEWFLOW_MODE"] = "server"

from _numeric_assertions import assert_numeric_baseline
from coscreen import dta_analysis  # noqa: E402
from coscreen import dta_hsroc  # noqa: E402
from coscreen import dta_likelihood_ratios as lr_mod  # noqa: E402
from coscreen import dta_prediction_region  # noqa: E402
from coscreen import dta_threshold_model  # noqa: E402
from coscreen import review_analysis  # noqa: E402
from coscreen.db import _SCHEMA, _connect  # noqa: E402
from coscreen.models import Article  # noqa: E402

_TMP = tempfile.TemporaryDirectory(prefix="rf_r9_l6a_", ignore_cleanup_errors=True)

_NORMAL_HSROC = {"lambda": 1.0, "theta": 0.5, "beta": 0.4054651081081642,
                 "variance_alpha": 0.312, "variance_theta": 0.084}
_NORMAL_BIVARIATE = {"logit_sensitivity_mean": 1.0, "logit_specificity_mean": 0.5,
                     "tau_sensitivity": 0.4, "tau_specificity": 0.6,
                     "random_effect_correlation": 0.3}
_POOL_TABLES = [{"study_id": "A", "tp": 20, "fp": 10, "fn": 5, "tn": 30},
                {"study_id": "B", "tp": 15, "fp": 8, "fn": 6, "tn": 28},
                {"study_id": "C", "tp": 18, "fp": 12, "fn": 4, "tn": 26}]


def _fresh_db(name: str) -> str:
    """新建独立任务库。

    ``sqlite3.Connection`` 的上下文管理器只提交、不关闭；``with _connect(...)``
    会留下未关连接，Windows 上随后删/重建同名库文件直接 WinError 32，故用
    ``closing`` 显式关闭（与 ``coscreen.db`` 内部用法一致）。
    """
    path = os.path.join(_TMP.name, name + ".db")
    for suffix in ("", "-wal", "-shm"):
        try:
            os.remove(path + suffix)
        except FileNotFoundError:
            pass
    with closing(_connect(path)) as conn, conn:
        conn.executescript(_SCHEMA)
    return path


def _per_test_db(test: unittest.TestCase, prefix: str) -> str:
    """每个用例独立库文件（同名库被复用时会读到上一用例的旧句柄）。"""
    return _fresh_db(prefix + "_" + test.id().rsplit(".", 1)[-1])


def _seed(db_path: str, studies) -> None:
    """studies: [(study_id, [(threshold, tp, fp, fn, tn), ...]), ...]"""
    from coscreen import db as db_mod
    db_mod.upsert_articles(db_path, [Article(zotero_key=f"R_{study_id}", title="Ref " + study_id)
                                     for study_id, _ in studies], [])
    for study_id, rows in studies:
        review_analysis.save_study(db_path, study_id, "Study " + study_id, [f"R_{study_id}"])
    for study_id, rows in studies:
        for threshold, tp, fp, fn, tn in rows:
            dta_analysis.save_result(db_path, study_id, "IT", "TC", threshold, "RS",
                                     tp, fp, fn, tn, f"R_{study_id}")


def _insert_raw(db_path: str, row) -> None:
    """库外直写（模拟部分拷贝/自建库），绕过 save_result 的守卫。

    ``with sqlite3.connect(...)`` 只提交事务、不关连接；Windows 上未关的连接
    会锁住文件，故用 ``closing`` 显式关闭。
    """
    with closing(sqlite3.connect(db_path)) as conn, conn:
        conn.execute("INSERT INTO review_dta_results (study_id,index_test,target_condition,"
                     "threshold,reference_standard,tp,fp,fn,tn,source_key,source_locator) "
                     "VALUES (?,?,?,?,?,?,?,?,?,?,?)", row)


class F392WhitespaceIdentifiers(unittest.TestCase):
    """b05·F3-92：链接检查/入库必须用与校验同一份剥离后的 ID。

    修前实测：`" S1 "` 与 `" R1 "` 都抛
    ``ValueError: source report must be linked to this study``（研究其实已链接）。
    """

    def setUp(self):
        self.db = _fresh_db("f392")
        from coscreen import db as db_mod
        db_mod.upsert_articles(self.db, [Article(zotero_key="R1", title="Ref one")], [])
        review_analysis.save_study(self.db, "S1", "Study 1", ["R1"])

    def test_padded_study_id_and_source_key_are_saved(self):
        dta_analysis.save_result(self.db, " S1 ", "IT", "TC", " TH ", "RS", 1, 1, 1, 1, " R1 ")
        rows = dta_analysis.list_results(self.db)
        self.assertEqual(1, len(rows))
        self.assertEqual("S1", rows[0]["study_id"])
        self.assertEqual("TH", rows[0]["threshold"])
        self.assertEqual("R1", rows[0]["source_key"])

    def test_unstripped_input_lands_on_same_row_as_stripped(self):
        """逐位对照：写成 "S1" 后再以 " S1 " 保存同一键 → 覆盖同一行。"""
        dta_analysis.save_result(self.db, "S1", "IT", "TC", "TH", "RS", 1, 1, 1, 1, "R1")
        dta_analysis.save_result(self.db, " S1 ", "IT", "TC", "TH", "RS", 9, 9, 9, 9, " R1 ")
        rows = dta_analysis.list_results(self.db)
        self.assertEqual(1, len(rows), "剥离后应命中同一主键，而不是并存第二行")
        self.assertEqual((9, 9, 9, 9), (rows[0]["tp"], rows[0]["fp"],
                                        rows[0]["fn"], rows[0]["tn"]))


class F394UnknownThresholdDiagnosis(unittest.TestCase):
    """b13·F3-94：不存在的 threshold/reference_standard 必须给出真因与候选值。

    修前实测：6 条 '5 mm' 数据 + 请求 '5mm' → ``bivariate synthesis requires at
    least five independent studies``（正确阈值同数据 200、n_studies=6）。
    """

    @classmethod
    def setUpClass(cls):
        cls.db = _fresh_db("f394")
        _seed(cls.db, [(f"SD{i}", [("5 mm", 10 + i, 5, 2 + (i % 3), 20)]) for i in range(1, 7)])

    def test_unknown_threshold_names_the_recorded_values(self):
        with self.assertRaises(ValueError) as caught:
            dta_analysis.synthesize(self.db, "IT", "TC", "5mm", "RS")
        message = str(caught.exception)
        self.assertNotIn("at least five", message)
        self.assertIn("'5mm'", message)
        self.assertIn("'5 mm'", message)

    def test_unknown_reference_standard_names_the_recorded_values(self):
        with self.assertRaises(ValueError) as caught:
            dta_analysis.synthesize(self.db, "IT", "TC", "5 mm", "Histology")
        message = str(caught.exception)
        self.assertIn("'Histology'", message)
        self.assertIn("'RS'", message)

    def test_recorded_threshold_matches_implicit_selection_and_baseline(self):
        """A recorded threshold uses the same observations and fitted model."""
        result = dta_analysis.synthesize(self.db, "IT", "TC", "5 mm", "RS")
        self.assertEqual(result, dta_analysis.synthesize(self.db, "IT", "TC"))
        self.assertEqual(6, result["n_studies"])
        self.assertEqual(["SD1", "SD2", "SD3", "SD4", "SD5", "SD6"], result["study_ids"])
        self.assertEqual(("5 mm", "RS"), (result["threshold"], result["reference_standard"]))
        # L-BFGS-B can stop at slightly different points near this variance boundary.
        assert_numeric_baseline(result["sensitivity"], 0.8181818702492092, abs_tol=1e-7)
        assert_numeric_baseline(result["specificity"], 0.8000000226100881, abs_tol=1e-7)
        assert_numeric_baseline(result["log_likelihood"], -18.844392216536914, abs_tol=1e-7)
        assert_numeric_baseline(result["tau_sensitivity"], 0.0009118819655545162)
        assert_numeric_baseline(result["tau_specificity"], 0.0009118819655545162)
        # At near-zero between-study variance the correlation is weakly identified.
        self.assertLessEqual(abs(result["random_effect_correlation"]), 1.0)
        self.assertIsNone(result["pooled_ci_95"])
        self.assertIsNone(result["mean_covariance"])


class F396BetaOverflowContract(unittest.TestCase):
    """b13·F3-96：非法 beta 一律 ValueError，不得抛 OverflowError。

    修前实测：beta=1419 → ValueError；1420/2000 → ``OverflowError: math range error``。
    """

    def test_large_beta_is_value_error_not_overflow(self):
        for beta in (1420.0, 2000.0, -2000.0):
            with self.subTest(beta=beta):
                payload = {"lambda": 1.0, "theta": 0.5, "beta": beta,
                           "variance_alpha": 1.0, "variance_theta": 1.0}
                with self.assertRaises(ValueError) as caught:
                    dta_hsroc.bivariate_from_hsroc(payload)
                self.assertIn("beta", str(caught.exception))

    def test_1419_still_value_error(self):
        payload = {"lambda": 1.0, "theta": 0.5, "beta": 1419.0,
                   "variance_alpha": 1.0, "variance_theta": 1.0}
        with self.assertRaises(ValueError):
            dta_hsroc.bivariate_from_hsroc(payload)

    def test_normal_parameters_unchanged_bit_for_bit(self):
        result = dta_hsroc.bivariate_from_hsroc(_NORMAL_HSROC)
        self.assertEqual(0.8164965809277261, result["logit_sensitivity_mean"])
        self.assertEqual(0.0, result["logit_specificity_mean"])
        self.assertEqual(0.32863353450309973, result["tau_sensitivity"])
        self.assertEqual(0.4929503017546495, result["tau_specificity"])
        self.assertEqual(-0.03703703703703706, result["random_effect_correlation"])
        self.assertEqual(1.1102230246251565e-16, result["roundtrip_check"])


class F397DerivedMeanFiniteness(unittest.TestCase):
    """b13·F3-97：派生均值溢出必须是输入校验错误（ValueError），不是 RuntimeError。

    修前实测：lambda=theta=1e308, beta=-100 →
    ``RuntimeError: round-trip check failed; transformation is numerically unstable``。
    """

    def test_derived_mean_overflow_is_value_error(self):
        payload = {"lambda": 1e308, "theta": 1e308, "beta": -100.0,
                   "variance_alpha": 1.0, "variance_theta": 1.0}
        with self.assertRaises(ValueError) as caught:
            dta_hsroc.bivariate_from_hsroc(payload)
        self.assertIn("finite", str(caught.exception))

    def test_forward_path_extreme_input_is_value_error(self):
        """同口径对照：正向对同类极端输入一直抛 ValueError。"""
        params = {"logit_sensitivity_mean": 1e308, "logit_specificity_mean": 1e308,
                  "tau_sensitivity": 0.4, "tau_specificity": 0.6,
                  "random_effect_correlation": 0.3}
        with self.assertRaises(ValueError):
            dta_hsroc.hsroc_from_bivariate(params)


class F399InverseDomainSymmetry(unittest.TestCase):
    """b14·F3-99：逆向输出的双变量参数必须落在正向的有效域内。

    修前实测：variance=1e-10 → 接受并返回 tau=1.118e-5；回喂正向 →
    ``ValueError: HSROC transformation unavailable for a near-boundary between-study SD``。
    """

    def test_below_boundary_variance_is_rejected(self):
        payload = {"lambda": 1.0, "theta": 0.5, "beta": 0.0,
                   "variance_alpha": 1e-10, "variance_theta": 1e-10}
        with self.assertRaises(ValueError) as caught:
            dta_hsroc.bivariate_from_hsroc(payload)
        self.assertIn("near-boundary between-study SD", str(caught.exception))

    def test_near_boundary_correlation_is_rejected(self):
        payload = {"lambda": 1.0, "theta": 0.5, "beta": 0.0,
                   "variance_alpha": 1.0, "variance_theta": 1e-4}
        with self.assertRaises(ValueError) as caught:
            dta_hsroc.bivariate_from_hsroc(payload)
        self.assertIn("near-boundary", str(caught.exception))

    def test_domain_round_trip_succeeds_both_ways(self):
        """逐位对照：正常参数两向往返仍成立（接受域未被收窄）。"""
        forward = dta_hsroc.hsroc_from_bivariate(_NORMAL_BIVARIATE)
        back = dta_hsroc.bivariate_from_hsroc(
            {key: forward[key] for key in dta_hsroc._HSROC_KEYS})
        self.assertEqual(1.632993161855452, forward["lambda"])
        self.assertEqual(0.40824829046386296, forward["theta"])
        self.assertAlmostEqual(_NORMAL_BIVARIATE["tau_sensitivity"], back["tau_sensitivity"],
                               places=15)
        self.assertAlmostEqual(_NORMAL_BIVARIATE["tau_specificity"], back["tau_specificity"],
                               places=15)


class F3100RoundtripMagnitude(unittest.TestCase):
    """b11·F3-100：往返容差按变换量级缩放，合法大数不再误判「数值失稳」。

    修前实测：logit_sens_mean=1e9 → ``RuntimeError: round-trip check failed``；
    常规（1.0）roundtrip=5.55e-17。
    """

    def test_large_but_legal_mean_is_accepted(self):
        params = {"logit_sensitivity_mean": 1e9, "logit_specificity_mean": 0.5,
                  "tau_sensitivity": 0.4, "tau_specificity": 0.6,
                  "random_effect_correlation": 0.3}
        result = dta_hsroc.hsroc_from_bivariate(params)
        self.assertTrue(math.isfinite(result["lambda"]))
        self.assertLessEqual(result["roundtrip_check"], 1e-8 * abs(result["lambda"]))

    def test_large_inverse_lambda_is_accepted(self):
        """逆向量级大且 θ 与 λ 相差多个数量级（加减抵消放大舍入）。

        基线实测：``lambda=1e9+0.123, theta=0.4`` 与 ``lambda=1e9, theta=0.123``
        都抛 ``RuntimeError: round-trip check failed``。
        """
        for lambda_, theta in ((1e9 + 0.123, 0.4), (1e9, 0.123), (1e12 + 0.123, 0.4)):
            with self.subTest(lambda_=lambda_, theta=theta):
                payload = {"lambda": lambda_, "theta": theta,
                           "beta": 0.4054651081081642,
                           "variance_alpha": 0.312, "variance_theta": 0.084}
                result = dta_hsroc.bivariate_from_hsroc(payload)
                self.assertTrue(math.isfinite(result["logit_sensitivity_mean"]))

    def test_normal_parameters_unchanged_bit_for_bit(self):
        result = dta_hsroc.hsroc_from_bivariate(_NORMAL_BIVARIATE)
        self.assertEqual(1.632993161855452, result["lambda"])
        self.assertEqual(0.40824829046386296, result["theta"])
        self.assertEqual(0.4054651081081642, result["beta"])
        self.assertEqual(0.624, result["variance_alpha"])
        self.assertEqual(0.08399999999999999, result["variance_theta"])
        self.assertEqual(5.551115123125783e-17, result["roundtrip_check"])
        self.assertEqual(1.3333333333333333, result["summary_sroc_curve"]["logit_intercept"])
        self.assertEqual(0.6666666666666666, result["summary_sroc_curve"]["logit_slope_fpr"])


class F402ZeroVariancePool(unittest.TestCase):
    """b05·F4-2：零（下溢）方差必须按 ValueError 拒绝，不得 ZeroDivisionError。

    修前实测：两条 tp=fp=10**17, fn=tn=1 的表 → ``ZeroDivisionError: float
    division by zero``（端点只捕 ValueError → 500）。属退化 SE 族，修法统一走
    ``_positive_variances`` 入口，与 review_analysis 的筛查同形。
    """

    def test_underflowed_variance_is_value_error(self):
        tables = [{"study_id": "a", "tp": 10**17, "fp": 10**17, "fn": 1, "tn": 1},
                  {"study_id": "b", "tp": 10**17, "fp": 10**17, "fn": 1, "tn": 1}]
        with self.assertRaises(ValueError) as caught:
            lr_mod.pool_likelihood_ratios(tables)
        self.assertIn("variance", str(caught.exception))

    def test_normal_pool_unchanged_bit_for_bit(self):
        result = lr_mod.pool_likelihood_ratios(_POOL_TABLES)
        self.assertEqual(2.9300833871098058, result["lr_positive"])
        self.assertEqual(0.3055104275177129, result["lr_negative"])
        self.assertEqual(1.0750308823884434, result["positive"]["ln_lr"])
        self.assertEqual(0.1683959958228989, result["positive"]["se_ln_lr"])
        self.assertEqual(-1.1857713683254538, result["negative"]["ln_lr"])
        self.assertEqual(0.23293350362445076, result["negative"]["se_ln_lr"])
        self.assertEqual([2.1064009820772993, 4.075856747346413],
                         result["positive"]["ci_lr"])
        self.assertEqual([0.1935319534141583, 0.4822801592991488],
                         result["negative"]["ci_lr"])

    def test_random_effects_normal_pool_unchanged(self):
        """随机效应分支同样过同一入口；常规输入结果与原实现一致。"""
        result = lr_mod.pool_likelihood_ratios(_POOL_TABLES, random_effects=True)
        self.assertGreaterEqual(result["positive"]["heterogeneity"]["tau2"], 0.0)
        self.assertEqual(result["lr_positive"], math.exp(result["positive"]["ln_lr"]))


class F403HugeCounts(unittest.TestCase):
    """b14·F4-3：无法转成有限浮点的计数按 ValueError 拒绝；可表示的计数照旧放行。"""

    def test_unrepresentable_count_is_value_error(self):
        tables = [{"study_id": "a", "tp": 10**400, "fp": 5, "fn": 2, "tn": 20},
                  {"study_id": "b", "tp": 10, "fp": 5, "fn": 2, "tn": 20}]
        with self.assertRaises(ValueError) as caught:
            lr_mod.pool_likelihood_ratios(tables)
        self.assertIn("too large", str(caught.exception))

    def test_float_representable_large_counts_still_accepted(self):
        """R2 对照：10**300（float 可表示，虽然精度已损失）必须继续放行。

        基线与本修复都必须返回同一个合并值——不因「新上界」被拒。
        """
        tables = [{"study_id": "a", "tp": 10**300, "fp": 10**300,
                   "fn": 10**300, "tn": 10**300},
                  {"study_id": "b", "tp": 10**300, "fp": 10**300,
                   "fn": 10**300, "tn": 10**300}]
        result = lr_mod.pool_likelihood_ratios(tables)
        self.assertEqual(2, result["n_used"])
        self.assertEqual(1.0, result["lr_positive"])
        self.assertEqual(1.0, result["lr_negative"])

    def test_closed_form_points_are_reachable_before_boundary_gate(self):
        """R2 对照：原采样条目不能被「新上界」连带拒绝。

        10**300 计数保持原样放行（F4-3 只拒绝 float 不可表示者）；此处钉住
        「计数本身不下发任何新上界」这一边界，与 F4-3 的上界判据区分。
        """
        tables = [{"study_id": "a", "tp": 10**300, "fp": 5, "fn": 2, "tn": 20},
                  {"study_id": "b", "tp": 10, "fp": 5, "fn": 2, "tn": 20}]
        result = lr_mod.pool_likelihood_ratios(tables)
        self.assertEqual(2, result["n_used"])

    def test_sqlite_range_counts_unchanged(self):
        """逐位对照：SQLite INTEGER 上界内的计数与基线一致。"""
        result = lr_mod.pool_likelihood_ratios(
            [{"study_id": "a", "tp": 2**63 - 1, "fp": 5, "fn": 2, "tn": 20},
             {"study_id": "b", "tp": 10, "fp": 5, "fn": 2, "tn": 20}])
        self.assertEqual(2, result["n_used"])
        self.assertTrue(math.isfinite(result["lr_positive"]))


class F404MonteCarloBudget(unittest.TestCase):
    """b14·F4-4：n_samples 有硬上界，越界在采样前拒绝；常规值结果不变。"""

    def test_huge_n_samples_rejected_before_sampling(self):
        calls = []
        original = lr_mod._sampling_cross_check

        def spy(*args, **kwargs):
            calls.append(args)
            return {"spied": True}

        lr_mod._sampling_cross_check = spy
        try:
            with self.assertRaises(ValueError) as caught:
                lr_mod.summary_likelihood_ratios(
                    0.5, 0.5, [[0.1, 0.02], [0.02, 0.1]], n_samples=10**9)
        finally:
            lr_mod._sampling_cross_check = original
        self.assertEqual([], calls, "越界后不得进入采样函数（否则 15 GiB 已分配）")
        self.assertIn(str(lr_mod.MAX_N_SAMPLES), str(caught.exception))

    def test_boundary_of_budget_is_accepted(self):
        result = lr_mod.summary_likelihood_ratios(
            0.5, 0.5, [[0.1, 0.02], [0.02, 0.1]], n_samples=lr_mod.MAX_N_SAMPLES)
        self.assertEqual(lr_mod.MAX_N_SAMPLES, result["sampling_cross_check"]["n_samples"])
        self.assertTrue(math.isfinite(result["lr_positive"]))

    def test_normal_n_samples_match_baseline(self):
        result = lr_mod.summary_likelihood_ratios(
            0.5, 0.5, [[0.1, 0.02], [0.02, 0.1]], n_samples=1000)
        assert_numeric_baseline(result["lr_positive"], 1.6487212707001282)
        assert_numeric_baseline(result["lr_negative"], 0.6065306597126334)
        assert_numeric_baseline(result["se_ln_lr_positive"], 0.24979873107713113)
        assert_numeric_baseline(
            result["sampling_cross_check"]["ci_lr_positive"], [1.0309636512605491, 2.732706503063804]
        )


class F407SamplingWithoutCovariance(unittest.TestCase):
    """b06·F4-7：n_samples>0 且无协方差时必须 ValueError，不得 TypeError。

    修前实测（当前树与基线导出逐字相同）：
    ``summary_likelihood_ratios(0.5, 0.5, None, n_samples=100)`` →
    ``TypeError: cannot unpack non-iterable NoneType object``
    （validated=None 被无条件传入 _sampling_cross_check 并在解包处崩）。
    TypeError 不是 ValueError，端点只捕 ValueError；HTTP 面今日不可达
    （视图写死 n_samples=0），属潜伏契约缺口。
    """

    def test_n_samples_without_covariance_is_value_error(self):
        with self.assertRaises(ValueError) as caught:
            lr_mod.summary_likelihood_ratios(0.5, 0.5, None, n_samples=100)
        message = str(caught.exception)
        self.assertIn("mean_covariance", message)
        self.assertNotIsInstance(caught.exception, TypeError)

    def test_sampling_is_never_reached_without_covariance(self):
        calls = []
        original = lr_mod._sampling_cross_check

        def spy(*args, **kwargs):
            calls.append(args)
            return {"spied": True}

        lr_mod._sampling_cross_check = spy
        try:
            with self.assertRaises(ValueError):
                lr_mod.summary_likelihood_ratios(0.5, 0.5, None, n_samples=100)
        finally:
            lr_mod._sampling_cross_check = original
        self.assertEqual([], calls, "无协方差时不得进入采样函数")

    def test_no_covariance_without_sampling_unchanged_bit_for_bit(self):
        """逐位对照：不请求交叉检验时，无协方差路径仍返回点估计 + 无区间。"""
        result = lr_mod.summary_likelihood_ratios(0.5, 0.5, None)
        self.assertEqual(1.6487212707001282, result["lr_positive"])
        self.assertEqual(0.6065306597126334, result["lr_negative"])
        self.assertIsNone(result["ci_lr_positive"])
        self.assertIsNone(result["ci_method"])
        self.assertNotIn("sampling_cross_check", result)

    def test_with_covariance_still_samples(self):
        """R2 对照：给了协方差时 n_samples 照常生效（未因新守卫被连带拒绝）。"""
        result = lr_mod.summary_likelihood_ratios(
            0.5, 0.5, [[0.1, 0.02], [0.02, 0.1]], n_samples=100)
        self.assertEqual(100, result["sampling_cross_check"]["n_samples"])
        sampled = lr_mod.summary_likelihood_ratios(
            0.5, 0.5, [[0.1, 0.02], [0.02, 0.1]], n_samples=1000)
        assert_numeric_baseline(
            sampled["sampling_cross_check"]["ci_lr_positive"], [1.0309636512605491, 2.732706503063804]
        )


class F405CovariancePositiveDefinite(unittest.TestCase):
    """b15·F4-5：正定性判据不得被 inf-inf=nan 绕过。

    修前实测：``_validated_covariance([[1e308,1e308],[1e308,1e308]])`` 返回矩阵；
    随后 summary 在 math.exp 处以 OverflowError 早退。
    """

    def test_overflowing_entries_are_rejected(self):
        with self.assertRaises(ValueError) as caught:
            lr_mod._validated_covariance([[1e308, 1e308], [1e308, 1e308]])
        self.assertIn("positive definite", str(caught.exception))

    def test_singular_matrix_still_rejected(self):
        with self.assertRaises(ValueError):
            lr_mod._validated_covariance([[1.0, 2.0], [2.0, 1.0]])
        with self.assertRaises(ValueError):
            lr_mod._validated_covariance([[1.0, 1.0], [1.0, 1.0]])

    def test_normal_covariance_unchanged_bit_for_bit(self):
        self.assertEqual((0.1, 0.1, 0.02),
                         lr_mod._validated_covariance([[0.1, 0.02], [0.02, 0.1]]))


class F410SaturatedPredictionRegion(unittest.TestCase):
    """b14·F4-10：饱和退化区域必须显式标记，不静默返回。

    修前实测：tau=1e154 → 121 点、不同 (sens,spec) 只有 4 个、返回字典无
    warning 字段。修法不拒绝合法输入（R2）：仍返回潜变量区域的 120 点，
    附带饱和 warning 与不同概率点计数。
    """

    def test_saturated_region_carries_warning(self):
        region = dta_prediction_region.prediction_region(0.0, 0.0, 1e154, 1e154, 0.5)
        self.assertIn("warnings", region)
        self.assertTrue(region["warnings"])
        self.assertIn("saturate", " ".join(region["warnings"]).lower())
        self.assertEqual(120, len(region["points"]))
        self.assertLess(region["distinct_probability_points"], 120)
        self.assertEqual(4, region["distinct_probability_points"])

    def test_latent_coordinates_remain_complete(self):
        """饱和时潜变量（logit）坐标仍逐点不同——诊断可据此指出饱和只在展示尺度。"""
        region = dta_prediction_region.prediction_region(0.0, 0.0, 1e154, 1e154, 0.5)
        latent = {(point["logit_sensitivity"], point["logit_specificity"])
                  for point in region["points"]}
        self.assertEqual(120, len(latent))

    def test_normal_region_unchanged_and_unwarned(self):
        """逐位对照：常规 tau=0.5 区域仍为 120 点、无饱和 warning。"""
        region = dta_prediction_region.prediction_region(0.3, -0.2, 0.5, 0.4, 0.35)
        self.assertEqual(120, len(region["points"]))
        self.assertEqual(120, region["distinct_probability_points"])
        self.assertEqual([], region["warnings"])
        self.assertEqual({"logit_sensitivity": 1.523873415340408,
                          "logit_specificity": 0.14268455629531424,
                          "sensitivity": 0.8211081526830603,
                          "specificity": 0.5356107433187913}, region["points"][0])
        self.assertEqual({"logit_sensitivity": 1.522196139359883,
                          "logit_specificity": 0.19021591121864734,
                          "sensitivity": 0.8208616456541871,
                          "specificity": 0.5474111111669856}, region["points"][1])


class F411PredictionRegionInputTyping(unittest.TestCase):
    """b14·F4-11：数值字符串不得被静默接受；类型错误与非有限文案可辨。"""

    def test_numeric_string_is_rejected(self):
        with self.assertRaises(ValueError) as caught:
            dta_prediction_region.prediction_region("0.5", -0.3, 0.8, 0.6, 0.35)
        self.assertIn("got str", str(caught.exception))

    def test_bool_none_and_text_have_distinct_messages(self):
        messages = {}
        for bad in (True, None, "abc"):
            with self.assertRaises(ValueError) as caught:
                dta_prediction_region.prediction_region(bad, -0.3, 0.8, 0.6, 0.35)
            messages[repr(bad)] = str(caught.exception)
        self.assertIn("got bool", messages["True"])
        self.assertIn("got NoneType", messages["None"])
        self.assertIn("got str", messages["'abc'"])
        self.assertNotEqual(messages["True"], messages["None"])

    def test_non_finite_keeps_its_own_message(self):
        with self.assertRaises(ValueError) as caught:
            dta_prediction_region.prediction_region(float("inf"), -0.3, 0.8, 0.6, 0.35)
        self.assertIn("must be finite", str(caught.exception))
        self.assertNotIn("got", str(caught.exception))

    def test_int_and_numeric_types_still_accepted(self):
        """R2 对照：int 与 numpy 标量（数值）继续接受，结果与 float 入参逐位一致。"""
        baseline = dta_prediction_region.prediction_region(0, -0.2, 0.5, 0.4, 0.35)
        as_float = dta_prediction_region.prediction_region(0.0, -0.2, 0.5, 0.4, 0.35)
        self.assertEqual(as_float["points"], baseline["points"])
        try:
            import numpy as np
        except ImportError:  # pragma: no cover - numpy is a runtime dependency
            return
        numpy_input = dta_prediction_region.prediction_region(
            np.float64(0.3), np.float64(-0.2), np.float64(0.5), np.float64(0.4),
            np.float64(0.35))
        reference = dta_prediction_region.prediction_region(0.3, -0.2, 0.5, 0.4, 0.35)
        self.assertEqual(reference["points"], numpy_input["points"])


class F413BoundaryPointCount(unittest.TestCase):
    """b14·F4-13：返回 120 个边界点，不复制首点闭合。

    修前实测：len(points)=121、points[0]==points[-1]。审计表 "Boundary points"
    列原样填 len(points)，因此导出数字偏大 1。
    """

    def test_points_are_not_closed(self):
        region = dta_prediction_region.prediction_region(0.3, -0.2, 0.5, 0.4, 0.35)
        self.assertEqual(120, len(region["points"]))
        self.assertNotEqual(region["points"][0], region["points"][-1])

    def test_remaining_points_unchanged_bit_for_bit(self):
        """逐位对照：前 120 个点与基线逐位一致（只删掉复制的第 121 点）。"""
        points = dta_prediction_region.prediction_region(
            0.3, -0.2, 0.5, 0.4, 0.35)["points"]
        self.assertEqual(1.523873415340408, points[0]["logit_sensitivity"])
        self.assertEqual(1.522196139359883, points[1]["logit_sensitivity"])
        self.assertEqual(1.522196139359883, points[-1]["logit_sensitivity"])
        self.assertEqual(0.09421392682288658, points[-1]["logit_specificity"])
        self.assertEqual(120, len({(point["sensitivity"], point["specificity"])
                                   for point in points}))


class F414DegenerateTableRow(unittest.TestCase):
    """b14·F4-14：简并 2×2 行不得让共享读表函数抛 ZeroDivisionError。

    修前实测：库外插入 (0,5,0,5) 行后 ``list_results`` 与 threshold synthesize
    均抛 ``ZeroDivisionError: division by zero``（端点只捕 ValueError → 500）。
    """

    def setUp(self):
        self.db = _fresh_db("f414")
        _seed(self.db, [("S1", [("0.5", 10, 5, 2, 20)])])
        _insert_raw(self.db, ("BAD", "OTHER", "OTHER", "0.5", "RS", 0, 5, 0, 5, "R_S1", ""))

    def test_list_results_reports_degenerate_row(self):
        rows = {row["study_id"]: row for row in dta_analysis.list_results(self.db)}
        self.assertEqual(2, len(rows))
        self.assertIsNone(rows["BAD"]["sensitivity"])
        self.assertEqual(0.5, rows["BAD"]["specificity"])
        self.assertEqual((0, 5, 0, 5),
                         (rows["BAD"]["tp"], rows["BAD"]["fp"],
                          rows["BAD"]["fn"], rows["BAD"]["tn"]))

    def test_normal_row_match_baseline(self):
        rows = {row["study_id"]: row for row in dta_analysis.list_results(self.db)}
        good = rows["S1"]
        assert_numeric_baseline(good["sensitivity"], 0.8333333333333334)
        assert_numeric_baseline(good["specificity"], 0.8)
        assert_numeric_baseline(good["sensitivity_ci"], (0.5158622513140327, 0.9791374745399076))
        assert_numeric_baseline(good["specificity_ci"], (0.5929625677213227, 0.931688535987516))

    def test_unrelated_degenerate_row_does_not_break_synthesis(self):
        """不同 index_test/condition 的坏行只影响自身回显，不阻断本选择。"""
        _seed(self.db, [(f"S{i}", [("0.5", 10 + i, 5, 2 + (i % 3), 20)]) for i in range(2, 7)])
        result = dta_analysis.synthesize(self.db, "IT", "TC", "0.5", "RS")
        self.assertEqual(6, result["n_studies"])
        self.assertTrue(math.isfinite(result["sensitivity"]))

    def test_degenerate_selected_row_is_value_error(self):
        """同选择集合内的简并行 → 可读 ValueError（而不是 500）。"""
        _insert_raw(self.db, ("S3", "IT", "TC", "0.5", "RS", 0, 5, 0, 5, "R_S1", ""))
        with self.assertRaises(ValueError) as caught:
            dta_analysis.synthesize(self.db, "IT", "TC", "0.5", "RS")
        self.assertIn("diseased and non-diseased", str(caught.exception))

    def test_threshold_model_survives_degenerate_row(self):
        _seed(self.db, [(f"S{i}", [(str(round(0.1 * i, 1)), 10 + i, 5, 2 + (i % 4), 20 + i)])
                        for i in range(2, 12)])
        result = dta_threshold_model.synthesize(self.db, "IT", "TC", "RS")
        self.assertEqual(11, result["n_studies"])


class F416WhitespaceThresholdKeys(unittest.TestCase):
    """b14·F4-16：threshold_by_study 的键先剥离空白再与库内 study_id 比对。

    修前实测：``{" S1 ": 0.5}`` → ``threshold_by_study contains unknown study IDs``；
    ``{"S1": 0.5}`` 越过该检查。
    """

    @classmethod
    def setUpClass(cls):
        cls.db = _fresh_db("f416")
        _seed(cls.db, [(f"S{i}", [(str(round(0.1 * i, 1)), 10 + i, 5, 2 + (i % 4), 20 + i)])
                       for i in range(1, 12)])

    def test_padded_key_selects_the_study(self):
        padded = dta_threshold_model.synthesize(self.db, "IT", "TC", "RS", {" S1 ": 0.1})
        exact = dta_threshold_model.synthesize(self.db, "IT", "TC", "RS", {"S1": 0.1})
        self.assertEqual(11, padded["n_studies"])
        self.assertEqual(exact["sensitivity_at_mean_threshold"],
                         padded["sensitivity_at_mean_threshold"])
        self.assertEqual(exact["studies"], padded["studies"])

    def test_truly_unknown_key_still_rejected(self):
        with self.assertRaises(ValueError) as caught:
            dta_threshold_model.synthesize(self.db, "IT", "TC", "RS", {"S99": 0.1})
        self.assertIn("unknown study IDs", str(caught.exception))

    def test_conflicting_normalised_keys_rejected(self):
        with self.assertRaises(ValueError) as caught:
            dta_threshold_model.synthesize(self.db, "IT", "TC", "RS",
                                           {"S1": 0.1, " S1 ": 0.2})
        self.assertIn("conflicting", str(caught.exception))


class F417DuplicateNumericThresholds(unittest.TestCase):
    """b14·F4-17：两条 threshold 文本解析为同一数值时给出确定性行为。

    修前实测：无选择 → ``study S1 has multiple thresholds``；传 0.5 →
    ``must match exactly one result``——两条路径互斥且无出口，只能删改库。
    修法（R2：不得拒绝合法输入）按存储文本排序取第一条，并在 warnings 说明；
    threshold 数值确实不同的多阈值仍要求用户选择。
    """

    @classmethod
    def setUpClass(cls):
        cls.db = _fresh_db("f417")
        studies = [(f"S{i}", [(str(round(0.1 * i, 1)), 10 + i, 5, 2 + (i % 4), 20 + i)])
                   for i in range(1, 12)]
        studies.append(("S1", [("0.10", 12, 5, 3, 21)]))
        _seed(cls.db, studies)

    def test_automatic_selection_is_deterministic(self):
        result = dta_threshold_model.synthesize(self.db, "IT", "TC", "RS")
        chosen = [row for row in result["studies"] if row["study_id"] == "S1"]
        self.assertEqual(1, len(chosen))
        self.assertEqual("0.1", chosen[0]["threshold"])
        self.assertEqual(11, result["n_studies"])
        self.assertTrue(any("parse to the same numeric value" in warning
                            for warning in result["warnings"]))

    def test_explicit_selection_uses_the_same_row(self):
        automatic = dta_threshold_model.synthesize(self.db, "IT", "TC", "RS")
        explicit = dta_threshold_model.synthesize(self.db, "IT", "TC", "RS", {"S1": 0.1})
        self.assertEqual(automatic["studies"], explicit["studies"])
        self.assertEqual(automatic["sensitivity_at_mean_threshold"],
                         explicit["sensitivity_at_mean_threshold"])
        self.assertEqual(automatic["threshold_center"], explicit["threshold_center"])

    def test_genuine_multi_threshold_still_requires_selection(self):
        """R2/R1 对照：数值确实不同的多阈值仍必须选择（不静默挑一个）。"""
        db = _fresh_db("f417b")
        studies = [(f"S{i}", [(str(round(0.1 * i, 1)), 10 + i, 5, 2 + (i % 4), 20 + i)])
                   for i in range(1, 12)]
        studies.append(("S1", [("0.9", 12, 5, 3, 21)]))
        _seed(db, studies)
        with self.assertRaises(ValueError) as caught:
            dta_threshold_model.synthesize(db, "IT", "TC", "RS")
        self.assertIn("multiple thresholds", str(caught.exception))


class _DtaEndpointBase(unittest.TestCase):
    """真实 app + TestClient（不重抛服务端异常，才能观察到 500）。"""

    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient

        from coscreen import tasks as tasks_mod
        from custom_backend import auth as auth_mod
        from custom_backend.main import app, DATA_DIR

        cls.client = TestClient(app, raise_server_exceptions=False)
        cls.client.__enter__()
        user = auth_mod.create_user(f"r9l6a-{cls.__name__.lower()}@example.test",
                                    "R9L6a-Pin-2026")
        cls.headers = {"Authorization":
                       "Bearer " + auth_mod.create_access_token(user["id"], user["username"])}
        response = cls.client.post("/api/tasks",
                                   json={"name": f"r9-l6a-{cls.__name__.lower()}"[:60]},
                                   headers=cls.headers)
        assert response.status_code in {200, 201}, response.text[:300]
        cls.task = response.json()["task_id"]
        info = tasks_mod.find_task(cls.task, DATA_DIR)
        cls.db = info.dir / (tasks_mod.slugify_screener(user["username"]) + ".db")
        from coscreen.db import init_db
        init_db(cls.db)

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    def _base(self):
        return f"/api/tasks/{self.task}"


class HttpF402ZeroVariance(_DtaEndpointBase):
    """b05·F4-2（HTTP）：池化端点在零方差下必须 400，不能 500。"""

    def test_zero_variance_is_400_not_500(self):
        from coscreen import db as db_mod
        db_mod.upsert_articles(self.db, [Article(zotero_key=f"LR{i}", title="t")
                                         for i in range(2)], [])
        review_analysis.save_study(self.db, "L1", "Study L1", ["LR0"])
        review_analysis.save_study(self.db, "L2", "Study L2", ["LR1"])
        for study_id, key in (("L1", "LR0"), ("L2", "LR1")):
            dta_analysis.save_result(self.db, study_id, "IT", "TC", "TH", "RS",
                                     10**17, 10**17, 1, 1, key)
        response = self.client.post(
            self._base() + "/analysis/dta/exploratory-likelihood-ratios",
            json={"index_test": "IT", "target_condition": "TC", "threshold": "TH",
                  "reference_standard": "RS", "model": "fixed", "correction": "none",
                  "confidence": 0.95},
            headers=self.headers)
        self.assertEqual(400, response.status_code,
                         f"修前此处为 500（ZeroDivisionError）：{response.text[:300]}")
        self.assertIn("variance", response.json()["detail"])


class HttpF394UnknownThreshold(_DtaEndpointBase):
    """b13·F3-94（HTTP）：错误阈值必须报真因，而不是「不足五研究」。"""

    def test_wrong_threshold_reports_candidates(self):
        _seed(self.db, [(f"SD{i}", [("5 mm", 10 + i, 5, 2 + (i % 3), 20)]) for i in range(1, 7)])
        response = self.client.get(
            self._base() + "/analysis/dta/synthesis",
            params={"index_test": "IT", "target_condition": "TC", "threshold": "5mm",
                    "reference_standard": "RS"},
            headers=self.headers)
        self.assertEqual(400, response.status_code)
        detail = response.json()["detail"]
        self.assertIn("'5 mm'", detail)
        self.assertNotIn("at least five", detail)


class HttpF414DegenerateRow(_DtaEndpointBase):
    """b14·F4-14（HTTP）：坏行不得让 GET results / threshold-meta 变 500。"""

    def test_get_results_survives_degenerate_row(self):
        _seed(self.db, [("S1", [("0.5", 10, 5, 2, 20)])])
        _insert_raw(self.db, ("BAD", "OTHER", "OTHER", "0.5", "RS", 0, 5, 0, 5, "R_S1", ""))
        response = self.client.get(self._base() + "/analysis/dta/results",
                                   headers=self.headers)
        self.assertEqual(200, response.status_code,
                         f"修前此处为 500（ZeroDivisionError）：{response.text[:300]}")
        self.assertEqual(2, len(response.json()["results"]))


class HttpF417DuplicateThresholds(_DtaEndpointBase):
    """b14·F4-17（HTTP）：重复数值阈值不再是无出口的 400。"""

    def test_threshold_regression_reaches_the_fit(self):
        studies = [(f"S{i}", [(str(round(0.1 * i, 1)), 10 + i, 5, 2 + (i % 4), 20 + i)])
                   for i in range(1, 12)]
        studies.append(("S1", [("0.10", 12, 5, 3, 21)]))
        _seed(self.db, studies)
        response = self.client.post(
            self._base() + "/analysis/dta/threshold-meta-regression",
            json={"index_test": "IT", "target_condition": "TC", "reference_standard": "RS",
                  "threshold_by_study": {"S1": 0.1}},
            headers=self.headers)
        self.assertEqual(200, response.status_code,
                         f"修前此处为 400 must match exactly one result：{response.text[:300]}")
        body = response.json()
        self.assertEqual(11, body["n_studies"])
        self.assertEqual("0.1", [row for row in body["studies"]
                                 if row["study_id"] == "S1"][0]["threshold"])


if __name__ == "__main__":
    unittest.main()
