"""review67 统计正确性修复钉子（处置泳道：数值与契约缺陷）。

覆盖编号（out/code_review_2026-11-12/REPORT.md）：
- M-03（stat-core-0）：create_synthesis_run 前置复用 selected_effects 的
  「已登记 cluster/sample 互异」独立性守卫，cluster_assumption/n_clusters
  按实际登记情况落库；
- M-09（stats-net-0）：synthesize_network_random 的 warnings 与 fixed_result
  合并而非整体覆盖（保留 MM10-12 反向配对方向告警）；
- M-10（stats-net-1）：synthesize_network 的 information 矩阵加 cond≤1e12
  护栏（对齐 network_random 同阈值），病态输入结构化拒绝而非静默垃圾解；
- L-05（stat-core-1）：DL 的 c=Σw−Σw²/Σw 在 Σw² 溢出（se<~2.7e-78 荒谬域）
  时按成对恒等式重算，PM 同域改用 brentq 解同一估计方程，均不再静默 τ²=0；
- L-24（stats-net-2）：network_gls/network_random 的 95% CI z 乘数统一为
  精确 norm.ppf(0.975)（唯一定义点 network_gls.Z_975）。

破坏即红：每个测试类第一个测试断言修复后的行为（修前基线 main@2226cad
必红）；标「常规对照」的测试是逐位/语义不变钉子（修前绿、修复后必须仍绿）。
"""
import math
import os
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from coscreen import network_gls, network_random, review_analysis  # noqa: E402
from coscreen.analysis_storage import create_synthesis_run, set_effect_design  # noqa: E402
from coscreen.db import _connect, init_db  # noqa: E402

_DIRECTION_PREFIX = "Effect direction check needed:"


def _direction_warnings(result: dict) -> list[str]:
    return [w for w in result["warnings"] if w.startswith(_DIRECTION_PREFIX)]


# ---------------------------------------------------------------------------
# M-03：合成运行持久化路径的独立性守卫 + cluster_assumption 按实落库
# ---------------------------------------------------------------------------
class M03SynthesisRunGuardTests(unittest.TestCase):
    """读写一致性：同一选层 GET（synthesize）与 POST（create_synthesis_run）同判定。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="rf_r67_m03_")
        self.db = os.path.join(self._tmp.name, "t.db")
        init_db(self.db)
        with closing(_connect(self.db)) as conn, conn:
            for i in (1, 2, 3):
                conn.execute("INSERT INTO review_studies(id,label) VALUES (?,?)",
                             (f"s{i}", f"S{i}"))
                conn.execute("INSERT INTO articles(zotero_key,title) VALUES (?,?)",
                             (f"k{i}", f"T{i}"))
                conn.execute("INSERT INTO review_reports(zotero_key,study_id) VALUES (?,?)",
                             (f"k{i}", f"s{i}"))
        self.rids = [review_analysis.save_effect(
            self.db, f"s{i}", "c", "o", "t1", "MD", 1.0 + 0.5 * i, 0.2, f"k{i}",
            {"effect_direction": "first_vs_second"}) for i in (1, 2, 3)]
        self.spec = {"comparison": "c", "outcome": "o", "timepoint": "t1",
                     "measure": "MD", "model": "fixed", "ci_method": "normal"}

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _register(self, designs: list[tuple[str, str, str] | None]) -> None:
        with closing(_connect(self.db)) as conn, conn:
            for rid, design in zip(self.rids, designs):
                if design is not None:
                    set_effect_design(conn, rid, *design)

    def test_shared_recorded_cluster_rejected_by_both_paths(self):
        """修前实测（探针 probe3_storage/repro）：synthesize 400 而 POST 落库成功。

        修复后同一选层两条路径必须抛出同一 ValueError（同一守卫函数）。
        """
        self._register([("clusterA", "sampleA", "note"),
                        ("clusterA", "sampleA", "same study duplicated"),
                        ("clusterB", "sampleB", "note")])
        with self.assertRaises(ValueError) as read_ctx:
            review_analysis.synthesize(self.db, "c", "o", "t1", "MD", "fixed")
        with self.assertRaises(ValueError) as run_ctx:
            create_synthesis_run(self.db, self.spec, "task1")
        self.assertEqual(str(read_ctx.exception), str(run_ctx.exception))
        self.assertIn("selected effects share a recorded cluster or sample",
                      str(run_ctx.exception))

    def test_shared_recorded_sample_only_also_rejected(self):
        """cluster 互异但 sample 共享（同一样本复用）同样拒绝。

        set_effect_design 的一对一约束会先拒绝这种登记对，故此处直接 SQL
        插入设计行（模拟绕过 API 的历史/手工数据），验证守卫对 sample 列
        同样逐列检查（与 selected_effects 守卫同语义）。
        """
        with closing(_connect(self.db)) as conn, conn:
            for rid, cluster in zip(self.rids, ("clusterA", "clusterB", "clusterC")):
                conn.execute(
                    "INSERT INTO review_effect_design VALUES (?,?,?,?,?,?)",
                    (rid, cluster, "sampleA", "reused sample", 1, "2026-01-01"))
        with self.assertRaises(ValueError) as ctx:
            create_synthesis_run(self.db, self.spec, "task1")
        self.assertIn("selected effects share a recorded cluster or sample",
                      str(ctx.exception))

    def test_no_design_run_keeps_legacy_assumption_literal(self):
        """常规对照：无任何登记时快照字面/n_clusters 与修复前一致。"""
        info = create_synthesis_run(self.db, self.spec, "task1")
        snapshot = info["input_snapshot"]
        self.assertEqual(snapshot["cluster_assumption"],
                         "one independent study per selected effect")
        self.assertEqual(snapshot["n_clusters"], 3)
        self.assertEqual(snapshot["n_studies"], 3)
        # 与实时 synthesize 同选层同数（读写一致的数值面）
        live = review_analysis.synthesize(self.db, "c", "o", "t1", "MD", "fixed")
        self.assertEqual(info["result"]["pooled_analysis"], live["pooled_analysis"])
        self.assertEqual(info["result"]["study_ids"], live["study_ids"])
        # 快照指纹确定性不因修复破坏
        again = create_synthesis_run(self.db, self.spec, "task1")
        self.assertEqual(snapshot["data_sha256"], again["input_snapshot"]["data_sha256"])

    def test_fully_recorded_distinct_designs_persist_verified_assumption(self):
        """全部登记且互异：声明按实（pairwise distinct），簇数=登记数。"""
        self._register([("clusterA", "sampleA", "note"),
                        ("clusterB", "sampleB", "note"),
                        ("clusterC", "sampleC", "note")])
        info = create_synthesis_run(self.db, self.spec, "task1")
        snapshot = info["input_snapshot"]
        self.assertEqual(snapshot["cluster_assumption"],
                         "recorded cluster and sample designs are pairwise distinct")
        self.assertEqual(snapshot["n_clusters"], 3)

    def test_partially_recorded_designs_persist_mixed_assumption(self):
        """部分登记：声明如实写出登记数与剩余假定，n_clusters 按研究数。"""
        self._register([("clusterA", "sampleA", "note"), None, None])
        info = create_synthesis_run(self.db, self.spec, "task1")
        snapshot = info["input_snapshot"]
        self.assertEqual(
            snapshot["cluster_assumption"],
            "1 of 3 selected effects carry recorded, pairwise-distinct "
            "cluster/sample designs; the rest are assumed one independent study "
            "per selected effect")
        self.assertEqual(snapshot["n_clusters"], 3)


# ---------------------------------------------------------------------------
# M-09：random 路径合并（而非覆盖）fixed 路径的方向告警
# ---------------------------------------------------------------------------
class M09RandomWarningsMergeTests(unittest.TestCase):
    STUDIES = [
        {"study_id": "S1", "covariance_scale": "natural",
         "contrasts": [{"treatment": "A", "comparator": "B", "estimate": 2.0}],
         "covariance": [[0.25]]},
        {"study_id": "S2", "covariance_scale": "natural",
         "contrasts": [{"treatment": "B", "comparator": "A", "estimate": 2.0}],
         "covariance": [[0.25]]},
        {"study_id": "S3", "covariance_scale": "natural",
         "contrasts": [{"treatment": "A", "comparator": "C", "estimate": 4.0}],
         "covariance": [[0.25]]},
    ]

    def test_random_keeps_fixed_direction_warnings(self):
        """修前实测：fixed 方向告警 4 条/random 5 条无方向（静态文案整体覆盖）。

        修复后 random 必须携带与 fixed 相同的方向告警。
        """
        fixed = network_gls.synthesize_network(self.STUDIES, "MD", reference="A")
        rand = network_random.synthesize_network_random(self.STUDIES, "MD", reference="A")
        self.assertEqual(_direction_warnings(rand), _direction_warnings(fixed))
        self.assertTrue(_direction_warnings(rand))

    def test_random_static_warnings_still_present(self):
        """常规对照：random 专属的 5 条静态文案一条不丢（合并不删随机语义）。"""
        rand = network_random.synthesize_network_random(self.STUDIES, "MD", reference="A")
        for literal in (
            "Requires transitivity and comparable effect modifiers",
            "Residual Q is a random-effects lack-of-fit diagnostic",
            "Uses independent arm-level study deviations",
            "Within-study covariance matrices must be provided",
            "Normal Wald intervals condition on the fitted heterogeneity variance",
        ):
            self.assertTrue(any(literal in w for w in rand["warnings"]), literal)

    def test_forward_duplicate_pair_not_flagged_in_random(self):
        """常规对照：同方向重复（正常复现研究）在 random 路径同样不得误报。"""
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
        rand = network_random.synthesize_network_random(studies, "MD", reference="C")
        self.assertEqual(_direction_warnings(rand), [])


# ---------------------------------------------------------------------------
# M-10：information 矩阵条件数护栏
# ---------------------------------------------------------------------------
class M10ConditionNumberGuardTests(unittest.TestCase):
    """病态 information（方差跨度 ≥~16 个数量级）结构化拒绝而非静默垃圾解。"""

    # 探针 probe_stats_net6 的最坏静默损坏用例：float64 下 information=[[2.2e144,
    # -2.2e144],[-2.2e144,2.2e144]] cond=inf，solve 不抛，修前输出 A-B
    # est=0.149±5.37e-65（Fraction 精确解 -3.535±2.9e+60，SE 错 125 个数量级）。
    BAD = [
        ("S0", "B-A", -3.535162345626397, 8.668554128119483e+120),
        ("S1", "C-A", 4.094001721635509, 1.2157133846199437e+259),
        ("S2", "B-C", -2.1494280348081194, 4.5352376971222124e-145),
        ("S3", "B-A", -1.9612052003049163, 2.5933104109916952e+269),
        ("S4", "C-A", 2.0708552055060387, 6.337745024629615e+181),
    ]

    @staticmethod
    def _payload(studies):
        return [{"study_id": sid, "covariance_scale": "natural",
                 "contrasts": [{"treatment": pair.split("-")[0],
                                "comparator": pair.split("-")[1], "estimate": est}],
                 "covariance": [[var]]}
                for sid, pair, est, var in studies]

    def test_ill_conditioned_information_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            network_gls.synthesize_network(self._payload(self.BAD), "MD", reference="A")
        self.assertIn("network information matrix is numerically ill-conditioned",
                      str(ctx.exception))

    def test_random_path_also_rejects_via_shared_fixed_fit(self):
        """random 先跑 fixed 拟合，同一病态输入必须同样拒绝。"""
        with self.assertRaises(ValueError):
            network_random.synthesize_network_random(self._payload(self.BAD), "MD",
                                                     reference="A")

    def test_well_conditioned_input_still_solves(self):
        """常规对照：正常量级输入照常出全部两两对比。"""
        studies = [
            ("S1", "A-B", 2.0, 0.04),
            ("S2", "B-C", -1.0, 0.09),
            ("S3", "A-C", 0.5, 0.06),
        ]
        result = network_gls.synthesize_network(self._payload(studies), "MD", reference="C")
        self.assertEqual(len(result["estimates"]), 3)
        ab = next(e for e in result["estimates"]
                  if {e["treatment"], e["comparator"]} == {"A", "B"})
        # fixed-effect GLS 合并值（修前基线即如此，与 r8 夹具同数）
        self.assertAlmostEqual(ab["estimate"], 1.8947368421052633, places=9)


# ---------------------------------------------------------------------------
# L-05：DL/PM 的 c 项溢出域（se<~2.7e-78）不再静默 τ²=0
# ---------------------------------------------------------------------------
class L05OverflowTau2Tests(unittest.TestCase):
    ROWS = [{"estimate": 1.0, "se": 1e-80, "study_id": "S1", "input_data": {}},
            {"estimate": 2.0, "se": 1e-80, "study_id": "S2", "input_data": {}}]

    def test_dl_overflow_domain_not_collapsed(self):
        """修前实测：tau2=0、ci=(1.5,1.5)、i2=100 自相矛盾；DL 真值 0.5。

        手算（Fraction 域）：q=5e159、c=1e160 → τ²=(q−df)/c=0.5；修复后
        random_reml 一直返回 0.5000000069（修前即正确），DL/PM 必须与之一致。
        """
        out = review_analysis._synthesize_rows(self.ROWS, "MD", "random", "normal")
        self.assertAlmostEqual(out["tau2"], 0.5, delta=1e-9)
        self.assertGreater(out["ci_analysis_high"] - out["ci_analysis_low"], 1.0)
        self.assertEqual(out["i2_percent"], 100.0)
        # 与 REML（修前已正确）同域对照
        reml = review_analysis._synthesize_rows(self.ROWS, "MD", "random_reml", "normal")
        self.assertAlmostEqual(out["tau2"], reml["tau2"], delta=1e-6)

    def test_pm_overflow_domain_matches_dl(self):
        """PM 同域不得静默 0：brentq 解同一 PM 方程，与 DL 同量级一致。"""
        pm = review_analysis._synthesize_rows(self.ROWS, "MD", "random_pm", "normal")
        dl = review_analysis._synthesize_rows(self.ROWS, "MD", "random", "normal")
        self.assertGreater(pm["tau2"], 0.0)
        self.assertAlmostEqual(pm["tau2"], dl["tau2"], delta=1e-9)
        self.assertGreater(pm["ci_analysis_high"] - pm["ci_analysis_low"], 1.0)

    def test_heteroscedastic_overflow_domain(self):
        """非均衡荒谬域（se 相差 10 倍）同样给出非零 τ²。"""
        rows = [{"estimate": 1.0, "se": 1e-80, "study_id": "S1", "input_data": {}},
                {"estimate": 2.0, "se": 1e-79, "study_id": "S2", "input_data": {}}]
        for model in ("random", "random_pm"):
            out = review_analysis._synthesize_rows(rows, "MD", model, "normal")
            self.assertGreater(out["tau2"], 0.0, model)
            self.assertAlmostEqual(out["tau2"], 0.5, delta=1e-9, msg=model)

    def test_dl_normal_domain_bit_identical(self):
        """常规对照：正常域仍走字面式 c=Σw−Σw²/Σw，τ² 与手算逐位一致。"""
        rows = [{"estimate": 1.0, "se": 0.2, "study_id": "S1", "input_data": {}},
                {"estimate": 2.0, "se": 0.3, "study_id": "S2", "input_data": {}},
                {"estimate": 1.4, "se": 0.25, "study_id": "S3", "input_data": {}}]
        out = review_analysis._synthesize_rows(rows, "MD", "random", "normal")
        w = [1 / r["se"] ** 2 for r in rows]
        y = [r["estimate"] for r in rows]
        fixed = sum(a * b for a, b in zip(w, y)) / sum(w)
        q = sum(a * (b - fixed) ** 2 for a, b in zip(w, y))
        c = sum(w) - sum(a * a for a in w) / sum(w)
        self.assertEqual(out["q"], q)
        self.assertEqual(out["tau2"], max(0.0, (q - 2) / c))

    def test_pm_normal_domain_bit_identical(self):
        """常规对照：正常域仍逐位调用 statsmodels combine_effects("iterated")。"""
        import numpy as np
        from statsmodels.stats.meta_analysis import combine_effects
        rows = [{"estimate": 1.0, "se": 0.2, "study_id": "S1", "input_data": {}},
                {"estimate": 2.0, "se": 0.3, "study_id": "S2", "input_data": {}},
                {"estimate": 1.4, "se": 0.25, "study_id": "S3", "input_data": {}}]
        out = review_analysis._synthesize_rows(rows, "MD", "random_pm", "normal")
        direct = float(combine_effects(
            np.asarray([1.0, 2.0, 1.4]),
            np.asarray([r["se"] ** 2 for r in rows]),
            method_re="iterated").tau2)
        self.assertEqual(out["tau2"], direct)


# ---------------------------------------------------------------------------
# L-24：z 乘数统一为精确 norm.ppf(0.975)
# ---------------------------------------------------------------------------
class L24ExactZMultiplierTests(unittest.TestCase):
    STUDIES = [
        {"study_id": "S1", "covariance_scale": "natural",
         "contrasts": [{"treatment": "A", "comparator": "B", "estimate": 2.0}],
         "covariance": [[0.04]]},
        {"study_id": "S2", "covariance_scale": "natural",
         "contrasts": [{"treatment": "B", "comparator": "C", "estimate": -1.0}],
         "covariance": [[0.09]]},
        {"study_id": "S3", "covariance_scale": "natural",
         "contrasts": [{"treatment": "A", "comparator": "C", "estimate": 0.5}],
         "covariance": [[0.06]]},
    ]

    def test_z_multiplier_is_exact_ppf(self):
        """修前实测：两处硬编码 1.96（相对差 1.8376e-5，probe_stats_net probe2）。"""
        from scipy.stats import norm
        exact = float(norm.ppf(0.975))
        self.assertEqual(network_gls.Z_975, exact)
        self.assertEqual(exact, 1.959963984540054)
        self.assertNotEqual(exact, 1.96)

    def test_gls_ci_uses_exact_multiplier(self):
        result = network_gls.synthesize_network(self.STUDIES, "MD", reference="C")
        for est in result["estimates"]:
            self.assertEqual(est["ci_low"], est["estimate"] - network_gls.Z_975 * est["se"])
            self.assertEqual(est["ci_high"], est["estimate"] + network_gls.Z_975 * est["se"])

    def test_random_ci_uses_same_multiplier(self):
        """random 复用同一常量（单一定义点），不是各自的 1.96。"""
        result = network_random.synthesize_network_random(self.STUDIES, "MD", reference="C")
        for est in result["estimates"]:
            self.assertEqual(est["ci_low"], est["estimate"] - network_gls.Z_975 * est["se"])
            self.assertEqual(est["ci_high"], est["estimate"] + network_gls.Z_975 * est["se"])


if __name__ == "__main__":
    unittest.main()
