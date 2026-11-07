"""r7-lastfix 数值内核钉子测试（MM11-03 / B08-N2 / F-04-01 / F-04-04）。

每条用例对应一个已确认缺陷的修复；把对应修复回退，该用例必须变红：

- MM11-03（ipd_cox_extended.py:92/:96 集合全等）：修前 two-stage 随机斜率
  固定协变量空元组，任何带额外列（如 ``age``）的 IPD 行整批 ValueError →
  HTTP 400，而同一批行 multi-adjusted 正常 200（mm11_l4_verify_mm.md §MM11-03
  实测：two-stage WITH age → 400 "and the named covariates "，WITHOUT → 200）。
  修复把 ``set(row) != fields`` 放宽为 ``fields <= set(row)``（按需子集校验，
  忽略未声明的额外列）。钉子：同批含协变量行 two-stage 库级 + HTTP 均 200，
  且数值与无协变量列的同批行、与函数级直调逐位一致；声明协变量缺失仍拒。
- B08-N2（sensitivity_view.py 无退化 SE 筛查）：修前 ``v = se*se`` 上溢 inf
  （se=1e200）/ 下溢 0（se=1e-170）破坏 ``except ValueError → 4xx`` 契约，
  且按池构成分两种失败模式（基线 ebd78f3 实测）：下溢带与全退化溢出池
  （w 全 0 → total_w=0）在 ``_model_fit`` 抛 ZeroDivisionError（非
  ValueError 子类）→ ``POST /analysis/sensitivity-view`` 真实 500
  （glm_verify_v1.md §2.3 实测：SVUN/SVOV → 500，两研究全退化）；混合
  溢出池（一正常一 1e200）则泄漏为下游 REML 前置 ValueError("REML needs
  matching finite effects and positive sampling variances")，口径外文案。
  修复按 ``review_analysis._screened_inverse_variance_weights``（MM10-01）的
  既有口径在 ``v`` 计算后筛查非有限/非正方差转 SensitivityViewError → 400。钉子：
  se=1e-170/1e200 库级 ValueError + HTTP 4xx；正常对照组逐位钉死基线 ebd78f3
  的采样值（数值在基线树模块上对拍，json 级相等，仅 ``seconds`` 计时字段除外）。
- F-04-01（stop_certificate.py:126 硬编码 confidence=0.95）：修前只要量足且
  0 新相关就以 ``1-alpha`` 发证；真实超几何上界 P(0 新相关 | 剩余恰为 k_t+1)
  在 k_t=0..8 时为 0.7000/0.4879/0.3385/0.2338/0.1608/0.1100/0.0749/0.0507/
  0.0342（glm_verify_v1.md §3.3 数值表），即仅 k_t≥8（found≥152）置信度才
  ≥95%。修复实现该上界，置信度 = 1 − P 随 k_t/抽验量如实计算，并同步
  main.py:6254-6256 与 al_stop_panel.js:237 文案（不许虚标 ≥95%）。
  钉子：found=10（k_t=0, 池 100, 抽 30）置信度 = 1−0.70 = 0.30（数表对拍），
  抽 70 条时 = 0.70；found=152（k_t=8）= 0.9658… ≥ 0.95；三处文案不再含
  "以 ≥95% 置信度"。全部撤修复（回到硬编码 0.95 / 原文案）必红。
- F-04-04（balancer.py:34 类 docstring 方向写反）：修前类 docstring 写
  ``ratio = N_irrelevant_weight / N_relevant_weight``，与模块 docstring
  （纳入:排除 = ratio:1）和实现（weight_0 = n_relevant/(ratio*n_irrelevant)）
  相反（glm_verify_v2.md §4 实测：y=[1,1,0]、ratio=9.8 → 总权重比 9.8:1
  纳入:排除）。钉子：docstring 方向字符串 + 数值总权重比 9.8 双重钉死。
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
fixture = tempfile.TemporaryDirectory(prefix="rf_r7_lastfix_kernel_")
os.environ["COBOOKSHELF_DATA_DIR"] = fixture.name
os.environ["REVIEWFLOW_MODE"] = "server"

from fastapi.testclient import TestClient

from coscreen import db as db_mod
from coscreen import tasks as tasks_mod
from coscreen.models import Article
from coscreen.review_analysis import save_effect, save_study
from custom_backend import auth as auth_mod
from custom_backend.main import app, DATA_DIR

REPO = Path(__file__).resolve().parents[2]

# 退化 SE 值：glm_verify_v1.md §2.3 实测口径（se=1e200 → se² 上溢 inf；
# se=1e-170 → se² 下溢 0）。基线 ebd78f3 实测（r2 评审更正后复核）：下溢带
# 与全退化溢出池在 _model_fit 处 ZeroDivisionError → HTTP 500；混合溢出池
# （一正常一 1e200）泄漏为下游 REML 前置 ValueError（口径外文案）。
SE_OVERFLOW, SE_UNDERFLOW = 1e200, 1e-170

# F-04-01 复审数值表（glm_verify_v1.md §3.3，pool=100、m=30）：
# k_t -> P(0 新相关 | K=k_t+1) = 错误发证概率上界；置信度 = 1 − 该值。
F0401_TABLE = {
    0: 0.7000, 1: 0.4879, 2: 0.3385, 3: 0.2338, 4: 0.1608,
    5: 0.1100, 6: 0.0749, 7: 0.0507, 8: 0.0342, 9: 0.0229,
}

# 手工确定性 IPD 批（无随机数）：两研究、双臂各有事件，S2 轻度反转。
# (study_id, duration, event, treatment)
_IPD_BASE = [
    ("S1", 9.0, 1, 1), ("S1", 8.5, 0, 1), ("S1", 4.0, 1, 0), ("S1", 3.5, 1, 1),
    ("S1", 7.0, 0, 0), ("S1", 2.5, 1, 1), ("S1", 8.0, 1, 0), ("S1", 6.5, 0, 0),
    ("S1", 3.0, 1, 1), ("S1", 5.5, 0, 1), ("S1", 7.5, 0, 0), ("S1", 2.0, 1, 0),
    ("S2", 6.0, 1, 0), ("S2", 5.5, 1, 1), ("S2", 2.0, 1, 0), ("S2", 4.5, 0, 1),
    ("S2", 7.5, 0, 0), ("S2", 1.5, 1, 1), ("S2", 8.0, 1, 0), ("S2", 6.0, 0, 0),
    ("S2", 2.5, 1, 0), ("S2", 3.5, 0, 1), ("S2", 5.0, 0, 0), ("S2", 1.0, 1, 1),
]


def _ipd_rows(with_age: bool) -> list[dict]:
    rows = []
    for index, (study_id, duration, event, treatment) in enumerate(_IPD_BASE):
        row = {"study_id": study_id, "duration": duration,
               "event": event, "treatment": treatment}
        if with_age:
            row["age"] = 40.0 + 2.5 * (index % 13)
        rows.append(row)
    return rows


class Mm11LibraryTests(unittest.TestCase):
    """MM11-03 库级钉子：修复回退（恢复集合全等）时这里先红。"""

    def test_mm11_03_two_stage_accepts_rows_with_named_covariates(self):
        """修前：带 age 列的行在 two-stage 下 ValueError 整批拒收（复审
        实测 400 "and the named covariates "）；修后必须接受，且数值与
        去掉 age 列的同批行、与函数级语义完全一致（额外列被忽略）。"""
        from coscreen.ipd_cox_extended import fit_two_stage_random_slopes

        sources = {"S1": "K1", "S2": "K2"}
        with_age = fit_two_stage_random_slopes(_ipd_rows(True), sources, tau2_method="DL")
        without_age = fit_two_stage_random_slopes(_ipd_rows(False), sources, tau2_method="DL")
        # 数值逐位一致：额外列不影响两阶段估计（同一解析路径，仅忽略键）
        self.assertEqual(without_age["pooled_log_hr"], with_age["pooled_log_hr"])
        self.assertEqual(without_age["se"], with_age["se"])
        self.assertEqual(without_age["tau2"], with_age["tau2"])
        self.assertEqual(without_age["q"], with_age["q"])
        # 复审口径的具体数值（确定性夹具，基线采样）
        self.assertEqual(with_age["pooled_log_hr"], 0.513116806120782)
        self.assertEqual(with_age["se"], 0.6183281682188588)

    def test_mm11_03_declared_covariate_missing_is_still_rejected(self):
        """放宽只针对"额外列"：multi-adjusted 声明的协变量缺失仍必须拒
        （按需子集校验，不是无校验）。"""
        from coscreen.ipd_cox_extended import fit_adjusted_stratified_cox_multi

        with self.assertRaises(ValueError) as caught:
            fit_adjusted_stratified_cox_multi(
                _ipd_rows(False), {"S1": "K1", "S2": "K2"},
                covariate_centers={"age": 55.0}, covariate_scales={"age": 10.0})
        self.assertIn("named covariates", str(caught.exception))


class B08LibraryTests(unittest.TestCase):
    """B08-N2 库级钉子：修复回退（去掉 v 筛查）时这里先红。"""

    _NORMAL = [
        {"study_id": "S1", "measure": "MD", "estimate": 0.5, "se": 0.3},
        {"study_id": "S2", "measure": "MD", "estimate": -0.25, "se": 0.35},
    ]

    def _rows(self, se):
        return [dict(self._NORMAL[0]), {**self._NORMAL[1], "se": se}]

    def test_b08_n2_degenerate_se_becomes_value_error_not_zero_division(self):
        """修前（基线 ebd78f3 实测，混合池=一正常行+一退化行）：se=1e-170
        （se² 下溢 0）→ ``_model_fit`` ZeroDivisionError（非 ValueError 子类）
        → 端点 500；se=1e200（se² 上溢 inf）→ 泄漏为下游 REML 前置
        ValueError("REML needs matching finite effects and positive sampling
        variances")，口径外文案。修后两带必须统一按 MM10-01 同口径给
        ValueError（SensitivityViewError + supported numeric range 文案）。"""
        from coscreen.sensitivity_view import SensitivityViewError, sensitivity_view

        for se in (SE_OVERFLOW, SE_UNDERFLOW):
            with self.assertRaises(ValueError) as caught:
                sensitivity_view(self._rows(se))
            self.assertIsInstance(caught.exception, SensitivityViewError)
            self.assertIn("supported numeric range", str(caught.exception))

    def test_b08_n2_normal_path_digit_identical_to_baseline(self):
        """正常对照组逐位钉死基线 ebd78f3 的采样值（基线树模块对拍通过，
        json 级相等；此处把关键浮点写死，任何漂移即红）。"""
        from coscreen.sensitivity_view import sensitivity_view

        result = sensitivity_view([dict(row) for row in self._NORMAL])
        self.assertEqual(result["q"], 2.647058823529412)
        self.assertEqual(result["c_constant"], 9.411764705882353)
        self.assertEqual(result["tau2_point_estimates"]["fixed"], 0.0)
        self.assertEqual(result["tau2_point_estimates"]["dl"], 0.17500000000000004)
        self.assertEqual(result["tau2_point_estimates"]["reml"], 0.1749999932561155)
        self.assertEqual(result["i2"]["point_percent"], 62.22222222222222)
        cells = {cell["key"]: cell for cell in result["cells"]}
        self.assertEqual(cells["fixed_wald"]["estimate"], 0.18235294117647055)
        self.assertEqual(cells["fixed_wald"]["ci"], [-0.2640817375215985, 0.6287876198745397])
        self.assertEqual(cells["dl_wald"]["estimate"], 0.14666666666666667)
        self.assertEqual(cells["dl_wald"]["ci"], [-0.5870920097100931, 0.8804253430434263])
        self.assertEqual(cells["reml_wald"]["estimate"], 0.14666666718619556)
        self.assertEqual(cells["reml_hksj"]["ci"], [-4.61020031699299, 4.9035336513653816])
        self.assertIsNone(cells["fixed_prediction"]["ci"])  # k=2 无预测区间


class F0401LibraryTests(unittest.TestCase):
    """F-04-01 库级钉子：修复回退（confidence 回到硬编码 1-alpha）时这里先红。"""

    def test_f04_01_hypergeometric_bound_matches_review_table(self):
        """certified_confidence 必须逐点复现复审数值表（pool=100、m=30）：
        置信度 = 1 − P(0 新相关 | K=k_t+1)，k_t=0..9 十行全部对拍。"""
        from coscreen.al.stop_certificate import RecallCertificate

        cert = RecallCertificate()
        for k_t, p_cert in F0401_TABLE.items():
            self.assertAlmostEqual(
                1.0 - p_cert, cert.certified_confidence(100, k_t, 30),
                delta=5e-5, msg=f"k_t={k_t}")
        # 阈值口径：found=152 → k_t=8（置信度 ≥0.95 的最小值），151 → 7
        self.assertEqual(cert.threshold(152), 8)
        self.assertEqual(cert.threshold(151), 7)

    def test_f04_01_found_10_confidence_is_honest_not_95(self):
        """found=10（k_t=0）、池 100、抽验 30（=ceil(100×0.30)）：
        真实置信度 = 1 − 0.70 = 0.30（数表 k_t=0 行）；修前硬编码 0.95。
        抽验 70 条（≥required，更充分抽样）时置信度 = 1 − (100−70)/100 = 0.70。"""
        from coscreen.al.stop_certificate import RecallCertificate

        cert = RecallCertificate()
        result = cert.should_stop(total_records=200, screened_count=100,
                                  found_relevant=10, sample_new_relevant=0, sample_size=30)
        self.assertTrue(result["stop"])
        self.assertAlmostEqual(result["confidence"], 0.30, delta=1e-9)
        self.assertNotEqual(result["confidence"], 0.95)
        self.assertLess(result["confidence"], 0.95)
        self.assertIn("30.0%", result["message"])
        self.assertNotIn("≥95% 置信度", result["message"])
        # 抽验量提高 → 置信度如实提高（0.70，复审同式）
        bigger = cert.should_stop(total_records=200, screened_count=100,
                                  found_relevant=10, sample_new_relevant=0, sample_size=70)
        self.assertTrue(bigger["stop"])
        self.assertAlmostEqual(bigger["confidence"], 0.70, delta=1e-12)

    def test_f04_01_found_152_confidence_reaches_95(self):
        """found=152（k_t=8）、池 100、抽验 30：置信度 = 1 − 0.0342 =
        0.9658…≥ 0.95（复审："仅 k_t≥8（found≥152）才 ≤0.05"）。修前硬编码
        0.95，与真实值差 0.0158 → 本钉子的紧公差下必红。"""
        from coscreen.al.stop_certificate import RecallCertificate

        cert = RecallCertificate()
        result = cert.should_stop(total_records=200, screened_count=100,
                                  found_relevant=152, sample_new_relevant=0, sample_size=30)
        self.assertTrue(result["stop"])
        self.assertAlmostEqual(result["confidence"], 0.9658119855462302, delta=1e-9)
        self.assertGreaterEqual(result["confidence"], 0.95)
        # found=151（k_t=7）尚未到 95%：1 − 0.050730602… = 0.949269398
        below = cert.should_stop(total_records=200, screened_count=100,
                                 found_relevant=151, sample_new_relevant=0, sample_size=30)
        self.assertTrue(below["stop"])
        self.assertAlmostEqual(below["confidence"], 0.9492693979073092, delta=1e-9)
        self.assertLess(below["confidence"], 0.95)

    def test_f04_01_copy_no_unconditional_95_confidence_claim(self):
        """三处用户可见文案不许再虚标 "以 ≥95% 置信度"（撤文案修复即红）：
        stop_certificate.py 发证 message 模板、main.py stop-certificate/check
        端点 message、al_stop_panel.js 通过判定。stop_registry.py:29 的
        description 属其他组文件，不在本组范围。"""
        stop_src = (REPO / "coscreen" / "al" / "stop_certificate.py").read_text(encoding="utf-8")
        main_src = (REPO / "custom_backend" / "main.py").read_text(encoding="utf-8")
        panel_src = (REPO / "custom_frontend" / "al_stop_panel.js").read_text(encoding="utf-8")
        # 硬编码置信度回退形态
        self.assertNotIn('"confidence": 1 - self.alpha', stop_src)
        self.assertNotIn("以 ≥95% 置信度", stop_src)
        self.assertNotIn("以 ≥95% 置信度", main_src)
        self.assertNotIn("以 ≥95% 置信度", panel_src)
        # 修复后的文案必须在场（防"删文案"式假修）
        self.assertIn("certified_confidence", stop_src)
        # r2 评审：端点响应 certificate 只含 recall_target/alpha/
        # sampling_fraction/look_fractions（main.py check_stop_certificate），
        # 无任何 confidence 字段，文案不得指向不存在的"证书返回的置信度"。
        self.assertIn("可能远低于 95%", main_src)
        self.assertNotIn("实际置信度为准", main_src)
        self.assertIn("并非固定 95%", panel_src)
        # r2 评审：模块头承诺"不再虚标固定的 1−α"，α 参数行必须同步限定，
        # 不得再写"显著性水平: 置信度 ≥ 95%"（certified_confidence 不用 α，
        # k_t=0 时置信度 0.30）。
        self.assertNotIn("显著性水平: 置信度 ≥ 95%", stop_src)
        self.assertIn("名义显著性水平", stop_src)


class F0404BalancerDocstringTests(unittest.TestCase):
    """F-04-04 钉子：docstring 方向回退（N_irrelevant/N_relevant）时这里红。"""

    def test_f04_04_class_docstring_ratio_direction_matches_implementation(self):
        """类 docstring 必须与模块 docstring/实现同向：总权重比 纳入:排除 =
        ratio:1；数值对照 y=[1,1,0]、ratio=9.8 → 总权重比 9.8（glm_verify_v2
        §4 实测 [1.3611,1.3611,0.2778]）。r2 评审补：MM11-05 更正
        （mm11_l4_verify_mm.md MM11-05）要求 :37 的逐样本断言（"排除类样本
        的权重约为纳入类的 1/9.8"，仅两类等量时成立，50/4950 实测
        1/970.2）改为"排除类**总**权重约为纳入类的 1/9.8"。"""
        from coscreen.al.balancer import BalancedWeights

        doc = BalancedWeights.__doc__
        self.assertIn("ratio = N_relevant_weight / N_irrelevant_weight", doc)
        self.assertNotIn("ratio = N_irrelevant_weight / N_relevant_weight", doc)
        # MM11-05 更正：逐样本断言必须删除，换成总权重表述
        self.assertIn("排除类**总**权重约为纳入类的 1/9.8", doc)
        self.assertNotIn("排除类样本的权重约为纳入类的 1/9.8", doc)
        # r3 评审：逐样本公式与所配数值必须同向——排除/纳入逐样本权重比
        # = n_relevant/(ratio × n_irrelevant)（50/4950 → 1/970.2，等量 → 1/9.8）。
        # r2 版句子把纳入/排除方向的公式 "= ratio × n_irrelevant / n_relevant"
        # 与排除/纳入方向的数值（1/9.8、1/970.2）配在一起，方向自相矛盾，
        # 不得再现（新 docstring 中该纳入/排除公式仅以"倒数"身份出现，无 "="）。
        self.assertIn("n_relevant/(ratio × n_irrelevant)", doc)
        self.assertIn("1/970.2", doc)
        self.assertNotIn("= ratio × n_irrelevant / n_relevant", doc)
        init_doc = BalancedWeights.__init__.__doc__
        self.assertIn("纳入类与排除类的总权重之比", init_doc)
        self.assertNotIn("排除类与纳入类的总权重之比", init_doc)
        # 行为对照（本修复不动行为）：总权重比 = ratio（纳入:排除）；
        # 50/4950 逐样本排除/纳入 = 1/970.2（MM11-05 实测，非 1/9.8）。
        import numpy as np

        weights = BalancedWeights(9.8).compute(np.asarray([1, 1, 0]))
        self.assertEqual(float(weights.sum()), 3.0)  # 归一化：总和 == n_samples
        ratio = float(weights[:2].sum() / weights[2])
        self.assertAlmostEqual(ratio, 9.8, delta=1e-9)
        self.assertAlmostEqual(float(weights[0]), 1.3611111111111112, delta=1e-12)
        self.assertAlmostEqual(float(weights[2]), 0.2777777777777778, delta=1e-12)
        y = np.asarray([1] * 50 + [0] * 4950)
        w = BalancedWeights(9.8).compute(y)
        total_ratio = float(w[y == 1].sum() / w[y == 0].sum())
        per_sample = float(w[y == 0][0] / w[y == 1][0])
        self.assertAlmostEqual(total_ratio, 9.8, delta=1e-9)
        self.assertAlmostEqual(per_sample, 0.0010307153, delta=1e-9)  # = 1/970.2
        # r3：双向数值对照（MM11-05 实测）——排除/纳入 = 1/970.2，
        # 其倒数纳入/排除 = 970.2；docstring 选用的排除/纳入方向与 1/970.2 配对。
        self.assertAlmostEqual(1.0 / per_sample, 970.2, delta=1e-6)
        # 等量对照：排除/纳入逐样本 = 1/9.8（docstring "仅两类等量时才是 1/9.8"）
        w_eq = BalancedWeights(9.8).compute(np.asarray([1, 0]))
        self.assertAlmostEqual(float(w_eq[1] / w_eq[0]), 1.0 / 9.8, delta=1e-9)


class EndpointGuardTests(unittest.TestCase):
    """端点级钉子：真实 app + TestClient（不重抛服务端异常，才能观察到 500）。"""

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app, raise_server_exceptions=False)
        cls.client.__enter__()
        # 不走 HTTP 注册（多模块共享限流桶会撞 429，同 test_r7_final_numerical 手法）
        cls.user = auth_mod.create_user("r7lastfix.kernel@example.test", "R7LastfixKernel-2026")
        cls.token = auth_mod.create_token(cls.user["id"], cls.user["username"]) \
            if hasattr(auth_mod, "create_token") else \
            auth_mod.create_access_token(cls.user["id"], cls.user["username"])
        cls.task = cls.client.post(
            "/api/tasks", json={"name": "r7-lastfix-kernel"},
            headers=cls.auth_header()).json()["task_id"]
        info = tasks_mod.find_task(cls.task, DATA_DIR)
        cls.db = info.dir / (tasks_mod.slugify_screener(cls.user["username"]) + ".db")
        db_mod.init_db(cls.db)
        db_mod.upsert_articles(
            cls.db, [Article(zotero_key=f"K{i}", title=f"T{i}") for i in range(1, 4)], [])
        save_study(cls.db, "S1", "Study 1", ["K1"])
        save_study(cls.db, "S2", "Study 2", ["K2"])
        # 敏感性矩阵夹具（comparison SV）：正常 / 溢出 / 下溢。
        for outcome, se in (("SVOK", 0.3), ("SVOV", SE_OVERFLOW), ("SVUN", SE_UNDERFLOW)):
            save_effect(cls.db, "S1", "SV", outcome, "TN", "MD", 0.5, se, "K1")
            save_effect(cls.db, "S2", "SV", outcome, "TN", "MD", -0.25,
                        se if se != 0.3 else 0.35, "K2")

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        fixture.cleanup()

    @classmethod
    def auth_header(cls):
        return {"Authorization": "Bearer " + cls.token}

    def _sensitivity(self, outcome):
        return self.client.post(
            f"/api/tasks/{self.task}/analysis/sensitivity-view",
            json={"comparison": "SV", "outcome": outcome, "timepoint": "TN",
                  "measure": "MD"},
            headers=self.auth_header())

    def test_b08_n2_degenerate_se_endpoint_is_4xx_not_500(self):
        """修前：se=1e200 / se=1e-170 → POST /analysis/sensitivity-view 真实
        500（glm_verify_v1.md §2.3：SVOV/SVUN → 500 "服务暂时无法完成请求"）；
        修后必须 400 且 detail 明确。"""
        for outcome in ("SVOV", "SVUN"):
            response = self._sensitivity(outcome)
            self.assertEqual(
                400, response.status_code,
                f"outcome={outcome}: 修前此处为 500；现在必须是明确 4xx："
                f"{response.status_code} {response.text[:200]}")
            self.assertEqual(
                response.json()["detail"],
                "study standard errors are outside the supported numeric range "
                "for inverse-variance weighting")

    def test_b08_n2_normal_outcome_still_200_with_baseline_digits(self):
        """正常 SE 对照：必须照常 200 且关键数值一位不许变（基线 ebd78f3
        采样值；与库级钉子同口径）。"""
        response = self._sensitivity("SVOK")
        self.assertEqual(200, response.status_code, response.text[:300])
        body = response.json()
        self.assertEqual(body["q"], 2.647058823529412)
        self.assertEqual(body["c_constant"], 9.411764705882353)
        self.assertEqual(body["tau2_point_estimates"]["dl"], 0.17500000000000004)
        self.assertEqual(body["i2"]["point_percent"], 62.22222222222222)
        cells = {cell["key"]: cell for cell in body["cells"]}
        self.assertEqual(cells["fixed_wald"]["ci"], [-0.2640817375215985, 0.6287876198745397])

    def test_mm11_03_two_stage_endpoint_accepts_covariate_rows(self):
        """修前：同批带 age 列的行 → HTTP 400 "each row needs … and the named
        covariates "（mm11_l4_verify_mm.md §MM11-03 HTTP 实测）；修后 200 且
        数值与函数级直调、与无 age 列批次逐位一致。"""
        from coscreen.ipd_cox_extended import fit_two_stage_random_slopes

        body = {
            "participants": _ipd_rows(True),
            "study_sources": {"S1": "K1", "S2": "K2"},
            "ties": "efron",
            "tau2_method": "DL",
        }
        response = self.client.post(
            f"/api/tasks/{self.task}/analysis/ipd/survival/two-stage-random-slopes",
            json=body, headers=self.auth_header())
        self.assertEqual(
            200, response.status_code,
            f"修前此处为 400（集合全等校验拒收带协变量行）："
            f"{response.status_code} {response.text[:200]}")
        result = response.json()
        expected = fit_two_stage_random_slopes(
            _ipd_rows(True), {"S1": "K1", "S2": "K2"}, ties="efron", tau2_method="DL")
        self.assertEqual(result["pooled_log_hr"], expected["pooled_log_hr"])
        self.assertEqual(result["se"], expected["se"])
        self.assertEqual(result["tau2"], expected["tau2"])
        self.assertEqual(result["pooled_log_hr"], 0.513116806120782)
        # 同批无 age 列的请求照常 200 且数值一致
        plain_body = dict(body, participants=_ipd_rows(False))
        plain = self.client.post(
            f"/api/tasks/{self.task}/analysis/ipd/survival/two-stage-random-slopes",
            json=plain_body, headers=self.auth_header())
        self.assertEqual(200, plain.status_code, plain.text[:200])
        self.assertEqual(plain.json()["pooled_log_hr"], result["pooled_log_hr"])
        self.assertEqual(plain.json()["se"], result["se"])
