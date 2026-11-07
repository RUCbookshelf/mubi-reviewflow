"""r7-finalfix 数值域钉子测试（MM10-01 / MM10-02）。

每条用例对应一个已确认缺陷的修复；把对应修复回退，该用例必须变红：
- MM10-01：退化 SE 的逆方差权重。修前 se=1e200 在 ``1/se**2`` 处抛
  OverflowError、se=1e-170 因 se² 下溢为 0 抛 ZeroDivisionError，二者都
  不是 ValueError，绕过 ``network_analysis``/``review_analysis`` 读端点的
  ``except ValueError → 4xx`` 契约，GET /analysis/network 与
  /analysis/synthesis 均真实 500。修复在计算入口（共享核
  ``_synthesize_rows`` 与 network 权重行）按 publication_bias 既有
  "supported numeric range" 口径转 ValueError → 400。
  对照组逐位钉死基线 5cb6314 的返回值（基线采样见
  out/external_reviews/final_verify_v1.md §1 的同型实测）：se 正常值、
  se=361.7（大但合法，MM10-03 阈内）、se=1e-160（次正规带 → 1/se²=inf →
  下游 NaN→null 的已知 MM5-05 行为）都必须照常 200 且数值不变。
  写入侧（POST /analysis/effects / save_effect）继续接受 se=1e200：
  按 R2 纪律不在写入侧新增值域上界，退化值在读侧计算入口给 4xx。
- MM10-02：rank_correlation n=2。修前 ``kendalltau(method="asymptotic")``
  的渐近方差在 ``size-2==0`` 处除零（scipy _stats_py ZeroDivisionError，
  非 ValueError 子类）→ 500。n=2 两研究是常见输入，修复按库内小样本拒绝
  先例（如 dose_nonlinear "requires at least three studies"）明确 400；
  n=3 与 rosenthal n=2 的 200 行为与数值逐位不变。
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
fixture = tempfile.TemporaryDirectory(prefix="rf_r7_finalnum_")
os.environ["COBOOKSHELF_DATA_DIR"] = fixture.name
os.environ["REVIEWFLOW_MODE"] = "server"

from fastapi.testclient import TestClient

from coscreen import db as db_mod
from coscreen import tasks as tasks_mod
from coscreen.models import Article
from coscreen.network_analysis import synthesize as network_synthesize
from coscreen.publication_bias import diagnose_publication_bias
from coscreen.review_analysis import _synthesize_rows, save_effect, save_study
from custom_backend import auth as auth_mod
from custom_backend.main import app, DATA_DIR

# 退化 SE 值：final_verify_v1.md §1.3 实测口径（se=1e200 溢出 / se=1e-170 下溢）。
SE_OVERFLOW, SE_UNDERFLOW, SE_SUBNORMAL, SE_LARGE_LEGAL = 1e200, 1e-170, 1e-160, 361.7

_ROWS_N2 = [  # publication-bias n=2 夹具（方差与标准化效应均有变异）
    {"study_id": "S1", "measure": "MD", "estimate": 0.5, "se": 0.2, "input_data": {}},
    {"study_id": "S2", "measure": "MD", "estimate": -0.4, "se": 0.35, "input_data": {}},
]
_ROWS_N3 = _ROWS_N2 + [
    {"study_id": "S3", "measure": "MD", "estimate": 0.15, "se": 0.28, "input_data": {}},
]


class Mm10LibraryGuardTests(unittest.TestCase):
    """库级钉子：直接调用三个载体函数，修复回退时这里先红。"""

    @classmethod
    def setUpClass(cls):
        cls.dir = Path(tempfile.mkdtemp(prefix="r7_finalnum_lib_"))
        cls.db = cls.dir / "review.db"
        db_mod.init_db(cls.db)
        db_mod.upsert_articles(
            cls.db, [Article(zotero_key=f"K{i}", title=f"T{i}") for i in range(1, 6)], [])
        for i in range(1, 6):
            save_study(cls.db, f"S{i}", f"Study {i}", [f"K{i}"])
        # 网络夹具：3 处理 3 对照连通网（参考=B），退化与正常各一层 outcome。
        for outcome, se in (("NETOV", SE_OVERFLOW), ("NETUN", SE_UNDERFLOW)):
            save_effect(cls.db, "S1", "A vs B", outcome, "TN", "RR", 1.8, se, "K1")
            save_effect(cls.db, "S2", "B vs C", outcome, "TN", "RR", 0.9, se, "K2")
            save_effect(cls.db, "S3", "A vs C", outcome, "TN", "RR", 2.2, se, "K3")

    def test_mm10_01_review_kernel_converts_degenerate_se_to_value_error(self):
        """修前：se=1e200 → OverflowError、se=1e-170 → ZeroDivisionError（均非
        ValueError 子类）直接逃出合成核；修后必须按既有校验模式给 ValueError。"""
        rows = [{"study_id": "S1", "measure": "MD", "estimate": 0.5, "se": SE_OVERFLOW,
                 "input_data": {}},
                {"study_id": "S2", "measure": "MD", "estimate": -0.25, "se": SE_OVERFLOW,
                 "input_data": {}}]
        with self.assertRaises(ValueError) as caught:
            _synthesize_rows(rows, "MD", "fixed", "normal")
        self.assertIn("supported numeric range", str(caught.exception))
        for row in rows:
            row["se"] = SE_UNDERFLOW
        with self.assertRaises(ValueError) as caught:
            _synthesize_rows(rows, "MD", "fixed", "normal")
        self.assertIn("supported numeric range", str(caught.exception))

    def test_mm10_01_network_module_converts_degenerate_se_to_value_error(self):
        """修前：network_analysis.synthesize 在 1/se² 行抛 OverflowError /
        ZeroDivisionError → 端点 500；修后必须 ValueError → 4xx。"""
        for outcome in ("NETOV", "NETUN"):
            with self.assertRaises(ValueError) as caught:
                network_synthesize(self.db, outcome, "TN", "RR", "B")
            self.assertIn("supported numeric range", str(caught.exception))

    def test_mm10_01_subnormal_band_keeps_mm5_05_null_behavior(self):
        """次正规带（se=1e-160 → 1/se²=inf）不在拒绝之列：合成照常返回，
        展示值降级为 None（已知 MM5-05 行为），不得变成 4xx/异常。"""
        rows = [{"study_id": "S1", "measure": "MD", "estimate": 0.5, "se": SE_SUBNORMAL,
                 "input_data": {}},
                {"study_id": "S2", "measure": "MD", "estimate": -0.25, "se": SE_SUBNORMAL,
                 "input_data": {}}]
        result = _synthesize_rows(rows, "MD", "fixed", "normal")
        self.assertIsNone(result["pooled"])
        self.assertIsNone(result["ci_low"])
        self.assertIsNone(result["ci_high"])
        self.assertTrue(any("representable display range" in w for w in result["warnings"]))

    def test_mm10_02_rank_correlation_rejects_two_studies_with_value_error(self):
        """修前：n=2 → scipy 渐近方差除零 ZeroDivisionError（非 ValueError）→ 500；
        修后按库内小样本先例明确 ValueError。rosenthal n=2 与 n=3 必须照常可用。"""
        with self.assertRaises(ValueError) as caught:
            diagnose_publication_bias(_ROWS_N2, "MD", "rank_correlation")
        self.assertIn("at least three studies", str(caught.exception))
        # 对照：n=3 rank_correlation 与 n=2 rosenthal 的 200 路径数值逐位钉死
        rank3 = diagnose_publication_bias(_ROWS_N3, "MD", "rank_correlation")
        self.assertEqual(rank3["n_studies"], 3)
        self.assertEqual(rank3["statistic"], -1.0)
        self.assertEqual(rank3["p_value"], 0.11718508719813801)
        rosenthal2 = diagnose_publication_bias(_ROWS_N2, "MD", "rosenthal_fail_safe_n")
        self.assertEqual(rosenthal2["n_studies"], 2)
        self.assertEqual(rosenthal2["sum_z"], 1.357142857142857)
        self.assertEqual(rosenthal2["fail_safe_n"], 0)
        self.assertEqual(rosenthal2["combined_p_value"], 0.3372339544842745)


class Mm10EndpointGuardTests(unittest.TestCase):
    """端点级钉子：真实 app + TestClient（不重抛服务端异常，才能观察到 500）。"""

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app, raise_server_exceptions=False)
        cls.client.__enter__()
        # 不走 HTTP 注册（多模块共享限流桶会撞 429，同 test_r7_mm5be_backend_caliber 手法）
        cls.user = auth_mod.create_user("r7finalnum@example.test", "R7FinalNum-2026")
        cls.token = auth_mod.create_access_token(cls.user["id"], cls.user["username"])
        cls.task = cls.client.post(
            "/api/tasks", json={"name": "r7-final-numerical"},
            headers=cls.auth_header()).json()["task_id"]
        info = tasks_mod.find_task(cls.task, DATA_DIR)
        cls.db = info.dir / (tasks_mod.slugify_screener(cls.user["username"]) + ".db")
        db_mod.init_db(cls.db)
        db_mod.upsert_articles(
            cls.db, [Article(zotero_key=f"K{i}", title=f"T{i}") for i in range(1, 6)], [])
        for i in range(1, 6):
            save_study(cls.db, f"S{i}", f"Study {i}", [f"K{i}"])

        def eff(study, comparison, outcome, measure, estimate, se):
            save_effect(cls.db, study, comparison, outcome, "TN", measure, estimate, se,
                        f"K{study[1:]}")

        # 合成夹具（comparison CN）：正常 / 溢出 / 下溢 / 次正规 / 大但合法（RR）。
        eff("S1", "CN", "ON", "MD", 0.5, 0.3)
        eff("S2", "CN", "ON", "MD", -0.25, 0.35)
        eff("S1", "CN", "OOV", "MD", 0.5, SE_OVERFLOW)
        eff("S2", "CN", "OOV", "MD", -0.25, SE_OVERFLOW)
        eff("S1", "CN", "OUN", "MD", 0.5, SE_UNDERFLOW)
        eff("S2", "CN", "OUN", "MD", -0.25, SE_UNDERFLOW)
        eff("S1", "CN", "ONAN", "MD", 0.5, SE_SUBNORMAL)
        eff("S2", "CN", "ONAN", "MD", -0.25, SE_SUBNORMAL)
        eff("S1", "CN", "OBIG", "RR", 2.0, SE_LARGE_LEGAL)
        eff("S2", "CN", "OBIG", "RR", 0.9, SE_LARGE_LEGAL)
        # 网络夹具（RR，参考 B）：正常 / 溢出 / 下溢。
        eff("S1", "A vs B", "NET", "RR", 1.8, 0.25)
        eff("S2", "B vs C", "NET", "RR", 0.9, 0.30)
        eff("S3", "A vs C", "NET", "RR", 2.2, 0.20)
        eff("S1", "A vs B", "NETOV", "RR", 1.8, SE_OVERFLOW)
        eff("S2", "B vs C", "NETOV", "RR", 0.9, SE_OVERFLOW)
        eff("S3", "A vs C", "NETOV", "RR", 2.2, SE_OVERFLOW)
        eff("S1", "A vs B", "NETUN", "RR", 1.8, SE_UNDERFLOW)
        eff("S2", "B vs C", "NETUN", "RR", 0.9, SE_UNDERFLOW)
        eff("S3", "A vs C", "NETUN", "RR", 2.2, SE_UNDERFLOW)
        # 发表偏倚夹具（comparison "C vs P"，MD）：n=2 与 n=3 两层。
        eff("S1", "C vs P", "PB2", "MD", 0.5, 0.2)
        eff("S2", "C vs P", "PB2", "MD", -0.4, 0.35)
        eff("S1", "C vs P", "PB3", "MD", 0.5, 0.2)
        eff("S2", "C vs P", "PB3", "MD", -0.4, 0.35)
        eff("S3", "C vs P", "PB3", "MD", 0.15, 0.28)

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        fixture.cleanup()

    @classmethod
    def auth_header(cls):
        return {"Authorization": "Bearer " + cls.token}

    def _synthesis(self, outcome, measure="MD"):
        return self.client.get(
            f"/api/tasks/{self.task}/analysis/synthesis",
            params={"comparison": "CN", "outcome": outcome, "timepoint": "TN",
                    "measure": measure, "model": "fixed", "ci_method": "normal"},
            headers=self.auth_header())

    def _network(self, outcome):
        return self.client.get(
            f"/api/tasks/{self.task}/analysis/network",
            params={"outcome": outcome, "timepoint": "TN", "measure": "RR",
                    "reference": "B"},
            headers=self.auth_header())

    def _pb(self, outcome, method):
        return self.client.get(
            f"/api/tasks/{self.task}/analysis/publication-bias",
            params={"comparison": "C vs P", "outcome": outcome, "timepoint": "TN",
                    "measure": "MD", "method": method},
            headers=self.auth_header())

    def test_mm10_01_synthesis_degenerate_se_is_4xx_not_500(self):
        """修前：se=1e200 / 1e-170 → GET /analysis/synthesis 真实 500；修后 400。"""
        for outcome in ("OOV", "OUN"):
            response = self._synthesis(outcome)
            self.assertEqual(
                400, response.status_code,
                f"outcome={outcome}: 修前此处为 500；现在必须是明确 4xx："
                f"{response.status_code} {response.text[:200]}")
            self.assertEqual(response.json()["detail"],
                             "study standard errors are outside the supported numeric range "
                             "for inverse-variance weighting")

    def test_mm10_01_network_degenerate_se_is_4xx_not_500(self):
        """修前：se=1e200 / 1e-170 → GET /analysis/network 真实 500；修后 400。"""
        for outcome in ("NETOV", "NETUN"):
            response = self._network(outcome)
            self.assertEqual(
                400, response.status_code,
                f"outcome={outcome}: 修前此处为 500；现在必须是明确 4xx："
                f"{response.status_code} {response.text[:200]}")
            self.assertEqual(response.json()["detail"],
                             "study standard errors are outside the supported numeric range "
                             "for inverse-variance weighting")

    def test_mm10_02_rank_correlation_n2_is_4xx_not_500(self):
        """修前：n=2 rank_correlation → ZeroDivisionError → 500；修后 400。"""
        response = self._pb("PB2", "rank_correlation")
        self.assertEqual(
            400, response.status_code,
            f"修前此处为 500；现在必须是明确 4xx：{response.status_code} {response.text[:200]}")
        self.assertEqual(response.json()["detail"],
                         "rank correlation needs at least three studies for the asymptotic variance")

    def test_controls_digit_identical_to_baseline(self):
        """对照组逐位钉死基线 5cb6314（同夹具在基线树上的采样值）：
        正常 SE、大但合法的 se=361.7（MM10-03 阈内）、次正规 se=1e-160、
        n=3 rank_correlation、rosenthal n=2——全部 200 且数值一位不许变。"""
        normal = self._synthesis("ON")
        self.assertEqual(200, normal.status_code, normal.text[:300])
        body = normal.json()
        self.assertEqual(body["pooled"], 0.18235294117647055)
        self.assertEqual(body["ci_low"], -0.26408994101432387)
        self.assertEqual(body["ci_high"], 0.628795823367265)
        self.assertEqual(body["q"], 2.647058823529412)
        self.assertEqual(body["q_p"], 0.10374167823654236)
        self.assertEqual(body["i2_percent"], 62.22222222222222)

        # se=361.7（log 尺度 CI 高界 ~e^709.6，仍在可表示范围）：必须照常 200。
        big = self._synthesis("OBIG", "RR")
        self.assertEqual(200, big.status_code, big.text[:300])
        big_body = big.json()
        self.assertEqual(big_body["pooled"], 1.3416407864998738)
        self.assertEqual(big_body["pooled_analysis"], 0.29389333245105953)
        self.assertEqual(big_body["ci_low"], 2.629566503431453e-218)
        self.assertEqual(big_body["ci_high"], 6.845234747442209e+217)

        # 次正规带 se=1e-160：已知 MM5-05 行为（200 + 展示值 null）不得移动。
        subnormal = self._synthesis("ONAN")
        self.assertEqual(200, subnormal.status_code, subnormal.text[:300])
        sub_body = subnormal.json()
        self.assertIsNone(sub_body["pooled"])
        self.assertIsNone(sub_body["ci_low"])
        self.assertIsNone(sub_body["ci_high"])

        network = self._network("NET")
        self.assertEqual(200, network.status_code, network.text[:300])
        net_body = network.json()
        self.assertEqual(net_body["treatments"], ["A", "B", "C"])
        self.assertEqual(net_body["n_studies"], 3)
        self.assertEqual(net_body["residual_q"], 0.486520011322391)
        self.assertEqual(net_body["residual_df"], 1)
        self.assertEqual(net_body["residual_p"], 0.4854837680741242)
        expected_estimates = {
            ("A", "B"): (1.988036459258131, 0.20544535090332955,
                         1.3290635014449028, 2.973739749111191),
            ("A", "C"): (2.0644553928475893, 0.17801211107200457,
                         1.4563928408388032, 2.926391801406293),
            ("B", "C"): (1.0384394024735217, 0.21891111877215813,
                         0.6761456407995334, 1.5948581600473273),
        }
        seen = {(e["treatment"], e["comparator"]): e for e in net_body["estimates"]}
        self.assertEqual(set(seen), set(expected_estimates))
        for pair, (estimate, se, ci_low, ci_high) in expected_estimates.items():
            self.assertEqual(seen[pair]["estimate"], estimate, pair)
            self.assertEqual(seen[pair]["se"], se, pair)
            self.assertEqual(seen[pair]["ci_low"], ci_low, pair)
            self.assertEqual(seen[pair]["ci_high"], ci_high, pair)

        rank3 = self._pb("PB3", "rank_correlation")
        self.assertEqual(200, rank3.status_code, rank3.text[:300])
        rank3_body = rank3.json()
        self.assertEqual(rank3_body["n_studies"], 3)
        self.assertEqual(rank3_body["statistic"], -1.0)
        self.assertEqual(rank3_body["p_value"], 0.11718508719813801)

        rosenthal2 = self._pb("PB2", "rosenthal_fail_safe_n")
        self.assertEqual(200, rosenthal2.status_code, rosenthal2.text[:300])
        rosenthal2_body = rosenthal2.json()
        self.assertEqual(rosenthal2_body["n_studies"], 2)
        self.assertEqual(rosenthal2_body["sum_z"], 1.357142857142857)
        self.assertEqual(rosenthal2_body["fail_safe_n"], 0)
        self.assertEqual(rosenthal2_body["combined_p_value"], 0.3372339544842745)

    def test_write_side_still_accepts_degenerate_se(self):
        """R2 纪律钉子：修复在计算入口而非写入侧——POST /analysis/effects
        对 se=1e200 必须照常 200（不新增拒绝合法输入的 Field 上界），
        随后的读端点负责给明确 4xx。"""
        response = self.client.post(
            f"/api/tasks/{self.task}/analysis/effects",
            json={"study_id": "S5", "comparison": "CN", "outcome": "OWRITE",
                  "timepoint": "TN", "measure": "MD", "estimate": 0.5,
                  "se": SE_OVERFLOW, "source_key": "K5",
                  "effect_direction": "first_vs_second"},
            headers=self.auth_header())
        self.assertEqual(200, response.status_code, response.text[:300])
        saved = [e for e in response.json()["effects"]
                 if e["outcome"] == "OWRITE" and e["se"] == SE_OVERFLOW]
        self.assertEqual(len(saved), 1)
        followup = self._synthesis("OWRITE")
        self.assertEqual(400, followup.status_code, followup.text[:300])


if __name__ == "__main__":
    unittest.main()
