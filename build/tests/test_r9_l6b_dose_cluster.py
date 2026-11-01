"""r9 L6b 泳道钉子：剂量反应 / 整群试验（14 条，清单 §3.6）。

覆盖清单条目（E:/mubi-reviewflow/out/release_plan/inventory_夜扫286确认分组.md §3.6）：

- b05·F3-75  dose_analysis.synthesize 的 ``1/information`` 在次正规剂量间距
  上信息量下溢为 0.0 → ZeroDivisionError（非 ValueError）→ 端点 500；
- b13·F3-76  （高危）list_curves 三列 ``json.loads`` 无防护：一行坏数据 brick
  全部剂量端点（GET 500、POST 500 且新行已持久化、synthesize 400）；
- b13·F3-79  ``_validate`` 用 ``float()`` 校验却按原值存储：字符串剂量进库后
  在 synthesize 的减法处 TypeError；list 行/非 dict 行在键检查后 TypeError；
- b05·F3-82  dose_arm_covariance 的 MD 分支 ``sd ** 2`` 溢出未捕获
  （sd=1e200 通过有限性校验）→ OverflowError → 500；
- b13·F3-80  RR 分支 ``"person_time" in arms[0]`` 先于 Mapping 检查，None 臂
  → TypeError（OR 同输入是 ValueError）；
- b13·F3-84  不可哈希 measure（``['RR']``）抛 TypeError 而非 ValueError；
- b05·F3-85  （高危）入库契约（≥2 对比、不排序）与拟合契约（≥3 且严格递增）
  脱节：3 臂最小合法输入进不了非线性端点；
- b05·F3-88  docstring 宣称 log-ratio 尺度、代码按自然尺度二次取 log
  （负 log RR 被误拒、log 尺度输入系数被二次取对数）；
- b13·F3-89  studies[].contrasts（自然尺度）与 sampling_covariance（log 尺度）
  并列且后者无标注；
- b04·F2-80  深嵌套 source_provenance 的 ``copy.deepcopy`` 抛 RecursionError
  → 500（deepcopy 深度族）；
- b01·F2-84  嵌套 NaN 穿透校验、input_json 落库裸 NaN，GET effects 回显 null
  （source_provenance 零校验族）；
- b04·F2-86  每臂 clusters=1e19 → df 超 int64 → student_t.ppf TypeError → 500；
- b11·F2-87  CV 修正使 design_effect≈1.3e308，OR 方差 ``math.fsum`` 抛
  OverflowError → 500（fsum 溢出族）；
- b11·F2-88  few_cluster_ci 无有限性门控，natural 标度返回 ci=±inf → 渲染期
  ValueError → 500。

破坏即红（R4）：每个 ``test_*`` 的首个断言都是修复后的行为，在基线 bfd07ae 上
必红（各类首注释记录本工作树的修前实测）。标「逐位对照」的用例钉住基线返回值，
修复后必须仍逐位相等（R1/R2：常规输入结果不变、不新增对合法输入的拒绝）。

同族修法形态（清单 §6.3）：
- deepcopy 深度族（F2-80）与 source_provenance 零校验族（F2-84）共用
  ``cluster_trial_effects._sanitize_provenance`` / ``_copy_input``：
  ``_MAX_PROVENANCE_DEPTH = 32``、超限子树替换 ``<nesting too deep>``、
  非有限浮点替换 ``<non-finite nan>``——与 ``glass_delta_effect`` /
  ``binary_correlation_conversion`` 的既有族口径逐字同形（不拒绝合法输入）；
- fsum 溢出族（F2-87，同 F4-106/F4-95）共用 ``_finite_fsum``：OverflowError
  → ValueError（4xx 契约），常规输入仍走同一条 fsum 逐位不变。
"""
import copy
import json
import math
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

#: HTTP 面的数据目录必须在导入 custom_backend 之前定好（同
#: test_r7_final_numerical / test_r9_l6d1_glass_binary 手法）。
_HTTP_FIXTURE = tempfile.TemporaryDirectory(prefix="rf_r9_l6b_http_",
                                            ignore_cleanup_errors=True)
os.environ["COBOOKSHELF_DATA_DIR"] = _HTTP_FIXTURE.name
os.environ["REVIEWFLOW_MODE"] = "server"

from coscreen import cluster_trial_effects as cte_mod
from coscreen import cluster_trial_variable_size as ctvs_mod
from coscreen import dose_analysis as dose_mod
from coscreen import dose_arm_covariance as dac_mod
from coscreen import dose_nonlinear as nl_mod
from coscreen.db import _connect, init_db
from coscreen.review_analysis import save_study


# --------------------------------------------------------------------------- #
# 夹具
# --------------------------------------------------------------------------- #
def _nested(depth, leaf="leaf"):
    value = {"leaf": leaf}
    for _ in range(depth):
        value = {"k": value}
    return value


def _make_db(prefix: str, studies=(("S1", "R1"), ("S2", "R2"), ("S3", "R3"))) -> str:
    """建任务库：三篇文章 + 每篇一个研究并回链（save_curve/synthesize 的入口契约）。"""
    path = Path(tempfile.mkdtemp(prefix=prefix, dir=r"E:/rf214tmp/r9/l6b")) / "task.db"
    init_db(path)
    conn = sqlite3.connect(path)
    try:
        for _, key in studies:
            conn.execute("INSERT INTO articles(zotero_key,title) VALUES(?,?)", (key, key))
        conn.commit()
    finally:
        conn.close()
    for study_id, key in studies:
        save_study(path, study_id, study_id, [key])
    return str(path)


def _save_simple_curve(db, study_id, key, *, outcome="CVD", timepoint="5y", measure="RR",
                       contrasts=None, covariance=None, input_data=None):
    dose_mod.save_curve(
        db, study_id, outcome, timepoint, measure, 0.0, "g/day",
        contrasts if contrasts is not None else [{"dose": 1.0, "estimate": 1.1},
                                                 {"dose": 2.0, "estimate": 1.2}],
        covariance if covariance is not None else [[0.01, 0.0], [0.0, 0.01]],
        key, input_data=input_data)


def _curve(study_id, doses, estimates, *, measure="RR", covariance=None):
    size = len(doses)
    return {
        "study_id": study_id, "outcome": "CVD", "timepoint": "5y", "measure": measure,
        "reference_dose": 0.0, "dose_unit": "g/day",
        "contrasts": [{"dose": d, "estimate": e} for d, e in zip(doses, estimates)],
        "covariance": covariance if covariance is not None else [
            [0.01 * (i == j) + 0.001 for j in range(size)] for i in range(size)],
        "source_key": "R1", "input_data": {"source_locator": "p1"},
    }


def _vs_values(**over):
    values = {"outcome_type": "binary", "measure": "OR", "icc": 0.05,
              "icc_source": "citation", "analysis_status": "unadjusted",
              "arms": {"treatment": {"clusters": 10, "participants": 200, "events": 40, "cv": 0.3},
                       "control": {"clusters": 10, "participants": 200, "events": 30, "cv": 0.3}}}
    values.update(over)
    return values


def _cluster_md(**over):
    values = {"outcome_type": "continuous", "measure": "MD", "icc": 0.05,
              "analysis_status": "unadjusted",
              "arms": {"treatment": {"clusters": 4, "participants": 100, "mean": 1.0, "sd": 1.0},
                       "control": {"clusters": 4, "participants": 100, "mean": 0.9, "sd": 1.0}}}
    values.update(over)
    return values


# --------------------------------------------------------------------------- #
# 剂量反应：读路径 / 写路径 / 拟合
# --------------------------------------------------------------------------- #
class F375DoseInformationUnderflowTests(unittest.TestCase):
    """b05·F3-75：剂量信息量下溢为 0 → ZeroDivisionError（500）。

    修前实测（本工作树）：两研究 ``dose=[1e-200, 2e-200]`` 落库后
    ``synthesize`` 在 dose_analysis.py:87 ``float(inverse_x @ y / information)``
    抛 ZeroDivisionError（HTTP 500）。
    """

    def setUp(self):
        self.db = _make_db("rf_r9_l6b_f375_")
        for study_id, key in (("S1", "R1"), ("S2", "R2")):
            _save_simple_curve(self.db, study_id, key,
                               contrasts=[{"dose": 1e-200, "estimate": 1.1},
                                          {"dose": 2e-200, "estimate": 1.2}])

    def test_subnormal_dose_spacing_is_value_error_not_zero_division(self):
        with self.assertRaises(ValueError) as caught:
            dose_mod.synthesize(self.db, "CVD", "5y", "RR")
        self.assertIn("no usable information", str(caught.exception))

    def test_http_synthesis_is_400_not_500(self):
        """端点级：修前 ZeroDivisionError 穿过 except ValueError → 500。"""
        from fastapi.testclient import TestClient

        from custom_backend.main import app
        _client, headers, db, task = _http_task("r9l6b-f375")
        for study_id, key in (("S1", "R1"), ("S2", "R2")):
            response = _client.post(f"/api/tasks/{task}/analysis/dose/curves", headers=headers,
                                    json={"study_id": study_id, "outcome": "CVD",
                                          "timepoint": "5y", "measure": "RR",
                                          "reference_dose": 0.0, "dose_unit": "g/day",
                                          "contrasts": [{"dose": 1e-200, "estimate": 1.1},
                                                        {"dose": 2e-200, "estimate": 1.2}],
                                          "covariance": [[0.01, 0.0], [0.0, 0.01]],
                                          "source_key": key})
            self.assertEqual(200, response.status_code, response.text[:300])
        response = _client.get(f"/api/tasks/{task}/analysis/dose/synthesis",
                               headers=headers,
                               params={"outcome": "CVD", "timepoint": "5y",
                                       "measure": "RR", "model": "fixed"})
        self.assertEqual(400, response.status_code, response.text[:300])

    def test_regular_curves_bit_identical(self):
        """逐位对照：常规曲线的斜率/SE/合并值逐位不变。"""
        db = _make_db("rf_r9_l6b_f375ctl_")
        for study_id, key, est in (("S1", "R1", 1.10), ("S2", "R2", 1.15), ("S3", "R3", 1.05)):
            _save_simple_curve(db, study_id, key,
                               contrasts=[{"dose": 1.0, "estimate": est},
                                          {"dose": 2.0, "estimate": est + 0.05}],
                               input_data={"source_locator": "p1"})
        fixed = dose_mod.synthesize(db, "CVD", "5y", "RR", "fixed")
        self.assertEqual([0.07496681291092851, 0.10088101119261356, 0.047882104755616384],
                         [row["estimate"] for row in fixed["study_slopes"]])
        self.assertEqual(0.07457664295305282, fixed["pooled_slope"])
        self.assertEqual(0.023969660562609234, fixed["ci_low"])
        self.assertEqual(0.1251836253434964, fixed["ci_high"])
        self.assertEqual(0.044721359549995794, fixed["study_slopes"][0]["se"])


class F376CorruptRowsDoNotBrickTests(unittest.TestCase):
    """b13·F3-76（高危）：一行坏 JSON brick 全部剂量端点。

    修前实测（本工作树）：``contrasts_json='{bad'`` → GET /dose/curves 抛
    JSONDecodeError（500）；POST 在写入之后回读列表，于是「新行已持久化 +
    500」；synthesize 同样崩在 list_curves。
    """

    def setUp(self):
        self.db = _make_db("rf_r9_l6b_f376_")
        for study_id, key in (("S1", "R1"), ("S2", "R2"), ("S3", "R3")):
            _save_simple_curve(self.db, study_id, key)
        with _connect(self.db) as conn:
            conn.execute("UPDATE review_dose_curves SET contrasts_json='{bad' WHERE study_id='S1'")

    def test_corrupt_row_degrades_instead_of_raising(self):
        curves = dose_mod.list_curves(self.db)
        by_study = {row["study_id"]: row for row in curves}
        self.assertIsNone(by_study["S1"]["contrasts"])
        self.assertIn("unreadable", by_study["S1"])
        self.assertNotIn("unreadable", by_study["S2"])
        self.assertEqual([{"dose": 1.0, "estimate": 1.1}, {"dose": 2.0, "estimate": 1.2}],
                         by_study["S2"]["contrasts"])

    def test_synthesis_skips_corrupt_row_and_warns(self):
        """修前：synthesize 在 list_curves 的 json.loads 处直接崩（500）。"""
        result = dose_mod.synthesize(self.db, "CVD", "5y", "RR", "fixed")
        self.assertEqual(["S2", "S3"], [row["study_id"] for row in result["study_slopes"]])
        self.assertEqual(["S1"], result["unreadable_studies"])
        self.assertIn("unreadable", result["warning"])

    def test_synthesis_with_only_corrupt_rows_is_actionable_400(self):
        with _connect(self.db) as conn:
            conn.execute("UPDATE review_dose_curves SET contrasts_json='{bad'")
        with self.assertRaises(ValueError) as caught:
            dose_mod.synthesize(self.db, "CVD", "5y", "RR", "fixed")
        self.assertIn("S1, S2, S3", str(caught.exception))
        self.assertIn("re-enter", str(caught.exception))

    def test_synthesis_failure_does_not_persist_a_new_row(self):
        """写路径防护（F3-76 的第二半）：坏数据下 POST 失败不得先落库。"""
        with _connect(self.db) as conn:
            before = conn.execute("SELECT COUNT(*) FROM review_dose_curves").fetchone()[0]
        with self.assertRaises(ValueError):
            _save_simple_curve(self.db, "S2", "R2", input_data={"bad": {1, 2}})
        with _connect(self.db) as conn:
            after = conn.execute("SELECT COUNT(*) FROM review_dose_curves").fetchone()[0]
        self.assertEqual(before, after)
        self.assertEqual([{"dose": 1.0, "estimate": 1.1}, {"dose": 2.0, "estimate": 1.2}],
                         {row["study_id"]: row for row in dose_mod.list_curves(self.db)}
                         ["S2"]["contrasts"], "坏输入的保存失败不得污染既有好行")

    def test_nonlinear_skips_corrupt_row_too(self):
        """同一条坏行也不得 brick 非线性端点（与线性合成同一降级口径）。"""
        db = _make_db("rf_r9_l6b_f376nl_",
                      studies=(("S1", "R1"), ("S2", "R2"), ("S3", "R3"), ("S4", "R4")))
        for study_id, key in (("S1", "R1"), ("S2", "R2"), ("S3", "R3"), ("S4", "R4")):
            _save_simple_curve(db, study_id, key)
        with _connect(db) as conn:
            conn.execute("UPDATE review_dose_curves SET contrasts_json='{bad' WHERE study_id='S1'")
        result = nl_mod.fit_nonlinear_dose_response(dose_mod.list_curves(db), [0.0, 1.0, 2.0])
        self.assertEqual(["S1"], result["unreadable_studies"])
        self.assertEqual(["S2", "S3", "S4"], [row["study_id"] for row in result["studies"]])

    def test_nonlinear_with_insufficient_readable_curves_is_actionable(self):
        with _connect(self.db) as conn:
            conn.execute("UPDATE review_dose_curves SET contrasts_json='{bad'")
        with self.assertRaises(ValueError) as caught:
            nl_mod.fit_nonlinear_dose_response(dose_mod.list_curves(self.db), [0.0, 1.0, 2.0])
        self.assertIn("S1, S2, S3", str(caught.exception))

    def test_http_get_and_post_survive_corrupt_row(self):
        """端点级：GET 200（可见坏行）、POST 200 且返回列表照常。"""
        client, headers, db_path, task = _http_task("r9l6b-f376")
        for study_id, key in (("S1", "R1"), ("S2", "R2")):
            response = client.post(f"/api/tasks/{task}/analysis/dose/curves", headers=headers,
                                   json={"study_id": study_id, "outcome": "CVD",
                                         "timepoint": "5y", "measure": "RR",
                                         "reference_dose": 0.0, "dose_unit": "g/day",
                                         "contrasts": [{"dose": 1.0, "estimate": 1.1},
                                                       {"dose": 2.0, "estimate": 1.2}],
                                         "covariance": [[0.01, 0.0], [0.0, 0.01]],
                                         "source_key": key})
            self.assertEqual(200, response.status_code, response.text[:300])
        conn = sqlite3.connect(db_path)

    def test_nonlinear_skips_corrupt_row_too(self):
        """同一条坏行也不得 brick 非线性端点（与线性合成同一降级口径）。"""
        db = _make_db("rf_r9_l6b_f376nl_",
                      studies=(("S1", "R1"), ("S2", "R2"), ("S3", "R3"), ("S4", "R4")))
        for study_id, key in (("S1", "R1"), ("S2", "R2"), ("S3", "R3"), ("S4", "R4")):
            _save_simple_curve(db, study_id, key)
        with _connect(db) as conn:
            conn.execute("UPDATE review_dose_curves SET contrasts_json='{bad' WHERE study_id='S1'")
        result = nl_mod.fit_nonlinear_dose_response(dose_mod.list_curves(db), [0.0, 1.0, 2.0])
        self.assertEqual(["S1"], result["unreadable_studies"])
        self.assertEqual(["S2", "S3", "S4"], [row["study_id"] for row in result["studies"]])

    def test_nonlinear_with_insufficient_readable_curves_is_actionable(self):
        with _connect(self.db) as conn:
            conn.execute("UPDATE review_dose_curves SET contrasts_json='{bad'")
        with self.assertRaises(ValueError) as caught:
            nl_mod.fit_nonlinear_dose_response(dose_mod.list_curves(self.db), [0.0, 1.0, 2.0])
        self.assertIn("S1, S2, S3", str(caught.exception))

    def test_http_get_and_post_survive_corrupt_row(self):
        """端点级：GET 200（可见坏行）、POST 200 且返回列表照常。"""
        client, headers, db_path, task = _http_task("r9l6b-f376")
        for study_id, key in (("S1", "R1"), ("S2", "R2")):
            response = client.post(f"/api/tasks/{task}/analysis/dose/curves", headers=headers,
                                   json={"study_id": study_id, "outcome": "CVD",
                                         "timepoint": "5y", "measure": "RR",
                                         "reference_dose": 0.0, "dose_unit": "g/day",
                                         "contrasts": [{"dose": 1.0, "estimate": 1.1},
                                                       {"dose": 2.0, "estimate": 1.2}],
                                         "covariance": [[0.01, 0.0], [0.0, 0.01]],
                                         "source_key": key})
            self.assertEqual(200, response.status_code, response.text[:300])
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("UPDATE review_dose_curves SET contrasts_json='{bad'")
            conn.commit()
        finally:
            conn.close()
        response = client.get(f"/api/tasks/{task}/analysis/dose/curves", headers=headers)
        self.assertEqual(200, response.status_code, response.text[:300])
        self.assertTrue(response.json()["curves"])
        response = client.post(f"/api/tasks/{task}/analysis/dose/curves", headers=headers,
                               json={"study_id": "S2", "outcome": "CVD",
                                     "timepoint": "5y", "measure": "RR",
                                     "reference_dose": 0.0, "dose_unit": "g/day",
                                     "contrasts": [{"dose": 1.0, "estimate": 1.2},
                                                   {"dose": 2.0, "estimate": 1.3}],
                                     "covariance": [[0.01, 0.0], [0.0, 0.01]],
                                     "source_key": "R2"})
        self.assertEqual(200, response.status_code, response.text[:300])


class F379StoredValuesAreNormalisedTests(unittest.TestCase):
    """b13·F3-79：校验用 float() 但按原值存储。

    修前实测（本工作树）：``dose="1.0"`` 落库仍是字符串，``synthesize`` 在
    ``dose_analysis.py:80`` 的减法处 TypeError；``[["dose","estimate"],…]`` 行
    通过 ``set(row)`` 键检查后在 ``row["dose"]`` 处 TypeError。
    """

    def setUp(self):
        self.db = _make_db("rf_r9_l6b_f379_")

    def test_string_doses_are_stored_as_numbers(self):
        for study_id, key in (("S1", "R1"), ("S2", "R2")):
            _save_simple_curve(self.db, study_id, key,
                               contrasts=[{"dose": "1.0", "estimate": 1.1},
                                          {"dose": "2.0", "estimate": 1.2}])
        stored = dose_mod.list_curves(self.db)[0]["contrasts"]
        self.assertEqual([{"dose": 1.0, "estimate": 1.1}, {"dose": 2.0, "estimate": 1.2}], stored)
        self.assertEqual(1.0, stored[0]["dose"])
        result = dose_mod.synthesize(self.db, "CVD", "5y", "RR", "fixed")
        self.assertEqual(2, len(result["study_slopes"]))

    def test_list_rows_are_value_error(self):
        with self.assertRaises(ValueError):
            _save_simple_curve(self.db, "S1", "R1",
                               contrasts=[["dose", "estimate"], ["dose", "estimate"]])

    def test_non_dict_rows_are_value_error(self):
        """对照（基线已绿，修复后必须仍绿）：非 dict 行的既有 ValueError 契约不变。"""
        for rows in (["x", "y"], {"a": 1}, [{"dose": 1.0, "estimate": 1.1, "extra": 0},
                                           {"dose": 2.0, "estimate": 1.2}]):
            with self.subTest(rows=rows):
                with self.assertRaises(ValueError):
                    _save_simple_curve(self.db, "S1", "R1", contrasts=rows)

    def test_non_numeric_estimate_is_value_error(self):
        """对照（基线已绿）：数值转换失败已是 ValueError，修复不得回退成 TypeError。"""
        with self.assertRaises(ValueError):
            _save_simple_curve(self.db, "S1", "R1",
                               contrasts=[{"dose": 1.0, "estimate": "abc"},
                                          {"dose": 2.0, "estimate": 1.2}])

    def test_non_json_input_data_is_value_error(self):
        """F3-76 写路径防护：不可序列化的审计载荷必须是 4xx 而不是 500。"""
        with self.assertRaises(ValueError):
            _save_simple_curve(self.db, "S1", "R1", input_data={"bad": {1, 2}})

    def test_regular_curve_storage_bit_identical(self):
        """逐位对照：常规曲线落库后的 JSON 文本与基线的规范化形式一致。"""
        _save_simple_curve(self.db, "S1", "R1", input_data={"source_locator": "p1"})
        with _connect(self.db) as conn:
            row = conn.execute("SELECT contrasts_json,covariance_json,input_json "
                               "FROM review_dose_curves").fetchone()
        self.assertEqual('[{"dose": 1.0, "estimate": 1.1}, {"dose": 2.0, "estimate": 1.2}]', row[0])
        self.assertEqual("[[0.01, 0.0], [0.0, 0.01]]", row[1])
        self.assertEqual('{"source_locator": "p1"}', row[2])


class F385NonlinearContractTests(unittest.TestCase):
    """b05·F3-85（高危）：入库契约（≥2 对比、不排序）vs 拟合契约（≥3、严格递增）。

    修前实测（本工作树）：三臂研究（参照 + 2 对比）能 ``save_curve`` 落库，
    但 ``fit_nonlinear_dose_response`` 在 dose_nonlinear.py:100 以
    「each study needs at least three dose contrasts」拒绝；倒序曲线能落库，
    拟合处以「dose contrasts must be strictly increasing」拒绝。
    """

    def test_three_arm_study_can_enter_nonlinear_fit(self):
        db = _make_db("rf_r9_l6b_f385_")
        for study_id, key in (("S1", "R1"), ("S2", "R2"), ("S3", "R3")):
            _save_simple_curve(db, study_id, key)  # 参照 0.0 + 两个对比：合法最小输入
        curves = dose_mod.list_curves(db)
        result = nl_mod.fit_nonlinear_dose_response(curves, [0.0, 1.0, 2.0])
        self.assertEqual(3, result["n_studies"])
        self.assertEqual(51, len(result["curve"]))
        self.assertEqual([0.0, 0.04, 0.08], [row["dose"] for row in result["curve"][:3]])
        self.assertTrue(all(math.isfinite(row["estimate"]) for row in result["curve"]))

    def test_arm_builder_three_arm_study_reaches_nonlinear_endpoint(self):
        """端到端两侧契约：arm_summaries_to_dose_curve → save_curve → 非线性拟合。

        这是清单条目要求的「3 臂最小合法输入可用」全链路（修前三臂曲线能落库
        但拟合必拒）。
        """
        db = _make_db("rf_r9_l6b_f385e2e_")
        for study_id, key in (("S1", "R1"), ("S2", "R2"), ("S3", "R3")):
            built = dac_mod.arm_summaries_to_dose_curve(study_id, [
                {"dose": 0.0, "events": 10, "total": 100},
                {"dose": 1.0, "events": 12, "total": 100},
                {"dose": 2.0, "events": 15, "total": 100},
            ], "RR", "g/day")
            dose_mod.save_curve(db, study_id, "CVD", "5y", "RR", built["reference_dose"],
                                "g/day", built["contrasts"], built["covariance"], key,
                                input_data={"source_locator": "p1"})
        result = nl_mod.fit_nonlinear_dose_response(dose_mod.list_curves(db), [0.0, 1.0, 2.0])
        self.assertEqual(3, result["n_studies"])
        self.assertEqual([], result["unreadable_studies"])
        self.assertEqual([1.0, 2.0], result["studies"][0]["contrast_doses"])

    def test_unsorted_contrasts_are_sorted_not_rejected(self):
        result = nl_mod.fit_nonlinear_dose_response(
            [_curve(s, [3.0, 1.0, 2.0], [1.3, 1.1, 1.2]) for s in ("S1", "S2", "S3")],
            [0.0, 1.5, 3.0])
        self.assertEqual([1.0, 2.0, 3.0], result["studies"][0]["contrast_doses"])
        self.assertEqual([1.1, 1.2, 1.3],
                         [row["estimate"] for row in result["studies"][0]["contrasts"]])
        # 排序后与显式升序输入逐位同结果（置换不改变拟合）
        ascending = nl_mod.fit_nonlinear_dose_response(
            [_curve(s, [1.0, 2.0, 3.0], [1.1, 1.2, 1.3]) for s in ("S1", "S2", "S3")],
            [0.0, 1.5, 3.0])
        self.assertEqual([row["estimate"] for row in ascending["pooled_coefficients"]],
                         [row["estimate"] for row in result["pooled_coefficients"]])

    def test_duplicate_contrast_doses_still_rejected(self):
        with self.assertRaises(ValueError):
            nl_mod.fit_nonlinear_dose_response(
                [_curve(s, [1.0, 1.0, 2.0], [1.1, 1.2, 1.3]) for s in ("S1", "S2", "S3")],
                [0.0, 1.5, 3.0])

    def test_single_contrast_still_rejected(self):
        with self.assertRaises(ValueError):
            nl_mod.fit_nonlinear_dose_response(
                [_curve(s, [1.0], [1.1]) for s in ("S1", "S2", "S3")], [0.0, 1.5, 3.0])


class F388AnalysisScaleTests(unittest.TestCase):
    """b05·F3-88：docstring 宣称 log-ratio 尺度，代码按自然尺度二次取 log。

    修前实测（本工作树）：按 docstring（log 尺度）喂 0.2/0.4/0.6 得到系数
    [-4.12455134749736, 5.017204551850578]——那是 log 0.2 = -1.609 的样条，
    即**二次取对数**；按入库契约（自然尺度，save_curve 存 1.1 这类比值）喂
    同数据才得到 [0.5561084134914263, -0.11538625674227268]。

    修法：代码口径（自然尺度估计 + log 尺度协方差，log 只取一次）才是与
    ``save_curve``/``arm_summaries_to_dose_curve`` 一致的真契约，故修正
    docstring 与拒绝文案，并把两侧尺度显式回显（与 F3-89 同一处）。
    """

    def test_docstring_declares_the_natural_scale_contract(self):
        doc = nl_mod.fit_nonlinear_dose_response.__doc__
        self.assertIn("natural-scale", doc)
        self.assertIn("log-ratio", doc)
        self.assertNotIn("Ratio measures require estimates and covariance on the log-ratio scale", doc)

    def test_log_is_applied_exactly_once(self):
        """对照 + 契约钉子：log 只取一次（基线已取一次，此处钉住不被二次取）。"""
        """自然尺度 exp(x) 与 identity 尺度的 x 必须给出同一套系数。"""
        ratio = nl_mod.fit_nonlinear_dose_response(
            [_curve(s, [1.0, 2.0, 3.0], [math.exp(0.1), math.exp(0.2), math.exp(0.3)])
             for s in ("S1", "S2", "S3")], [0.0, 1.5, 3.0])
        identity = nl_mod.fit_nonlinear_dose_response(
            [_curve(s, [1.0, 2.0, 3.0], [0.1, 0.2, 0.3], measure="MD")
             for s in ("S1", "S2", "S3")], [0.0, 1.5, 3.0])
        for left, right in zip(ratio["pooled_coefficients"], identity["pooled_coefficients"]):
            self.assertAlmostEqual(left["estimate"], right["estimate"], places=12)
        self.assertEqual("log", ratio["analysis_scale"])
        self.assertEqual("identity", identity["analysis_scale"])

    def test_non_positive_ratio_message_names_the_contract(self):
        with self.assertRaises(ValueError) as caught:
            nl_mod.fit_nonlinear_dose_response(
                [_curve(s, [1.0, 2.0, 3.0], [-0.1, 0.0, 0.1]) for s in ("S1", "S2", "S3")],
                [0.0, 1.5, 3.0])
        self.assertIn("natural-scale", str(caught.exception))

    def test_regular_fit_bit_identical(self):
        """逐位对照：常规三对比输入的合并系数与曲线点逐位不变（基线值）。"""
        result = nl_mod.fit_nonlinear_dose_response(
            [_curve(s, [1.0, 2.0, 3.0], [1.1, 1.2, 1.35]) for s in ("S1", "S2", "S3")],
            [0.0, 1.5, 3.0])
        self.assertEqual([0.2615244831882349, 0.04843997063012734],
                         [row["estimate"] for row in result["pooled_coefficients"]])
        self.assertEqual([0.1584317036359531, 0.23702548089480685],
                         [row["se"] for row in result["pooled_coefficients"]])
        self.assertEqual([0.0, 0.01143777279132276, 0.02285679521998979],
                         [row["ci_high"] for row in result["curve"][:3]])
        self.assertEqual(7.247786041604718e-32, result["q"]["statistic"])


class F389ScaleLabellingTests(unittest.TestCase):
    """b13·F3-89：contrasts（自然尺度）与 sampling_covariance（log 尺度）无标注。

    修前实测（本工作树）：studies[0] 只有 ``contrasts`` 与 ``sampling_covariance``
    两个键，无 ``contrasts_scale`` / ``sampling_covariance_scale``。
    """

    def test_scales_are_labelled_per_study(self):
        result = nl_mod.fit_nonlinear_dose_response(
            [_curve(s, [1.0, 2.0, 3.0], [1.2, 1.4, 1.6]) for s in ("S1", "S2", "S3")],
            [0.0, 1.5, 3.0])
        study = result["studies"][0]
        self.assertEqual("natural", study["contrasts_scale"])
        self.assertEqual("log", study["sampling_covariance_scale"])
        self.assertEqual("log", study["coefficient_covariance"]["scale"])

    def test_identity_measure_labels_covariance_identity(self):
        result = nl_mod.fit_nonlinear_dose_response(
            [_curve(s, [1.0, 2.0, 3.0], [0.2, 0.4, 0.6], measure="MD")
             for s in ("S1", "S2", "S3")], [0.0, 1.5, 3.0])
        self.assertEqual("identity", result["studies"][0]["sampling_covariance_scale"])


# --------------------------------------------------------------------------- #
# 臂级剂量协方差
# --------------------------------------------------------------------------- #
class F382DoseArmOverflowTests(unittest.TestCase):
    """b05·F3-82：MD 分支 sd² 溢出未捕获（sd=1e200 通过有限性校验）。

    修前实测（本工作树）：dose_arm_covariance.py:167 ``float(sd) ** 2``
    抛 OverflowError('Result too large')。
    """

    ARMS = [{"dose": 0.0, "n": 10, "mean": 1.0, "sd": 1e200},
            {"dose": 1.0, "n": 10, "mean": 1.0, "sd": 1.0},
            {"dose": 2.0, "n": 10, "mean": 1.0, "sd": 1.0}]

    def test_reference_sd_overflow_is_value_error(self):
        with self.assertRaises(ValueError) as caught:
            dac_mod.arm_summaries_to_dose_curve("S1", self.ARMS, "MD", "g/day")
        self.assertIn("overflows", str(caught.exception))

    def test_arm_sd_overflow_is_value_error(self):
        arms = [{"dose": 0.0, "n": 10, "mean": 1.0, "sd": 1.0},
                {"dose": 1.0, "n": 10, "mean": 1.0, "sd": 1e200},
                {"dose": 2.0, "n": 10, "mean": 1.0, "sd": 1.0}]
        with self.assertRaises(ValueError):
            dac_mod.arm_summaries_to_dose_curve("S1", arms, "MD", "g/day")

    def test_regular_md_curve_bit_identical(self):
        """逐位对照：常规 MD 曲线的对比/协方差逐位不变。"""
        result = dac_mod.arm_summaries_to_dose_curve("S2", [
            {"dose": 0.0, "n": 50, "mean": 1.0, "sd": 1.0},
            {"dose": 1.0, "n": 50, "mean": 1.2, "sd": 1.1},
            {"dose": 2.0, "n": 50, "mean": 1.4, "sd": 1.2},
        ], "MD", "g/day")
        self.assertEqual([{"dose": 1.0, "estimate": 0.19999999999999996},
                          {"dose": 2.0, "estimate": 0.3999999999999999}],
                         result["contrasts"])
        self.assertEqual([[0.0442, 0.02], [0.02, 0.048799999999999996]],
                         result["covariance"])


class F380NonMappingArmTests(unittest.TestCase):
    """b13·F3-80：RR 分支 ``"person_time" in arms[0]`` 先于 Mapping 检查。

    修前实测（本工作树）：``arms=[None, None, None]`` + measure="RR" →
    dose_arm_covariance.py:126 TypeError（同输入 OR 是 ValueError）。
    """

    def test_none_arms_are_value_error_for_every_measure(self):
        for measure in ("RR", "OR", "MD"):
            with self.subTest(measure=measure):
                with self.assertRaises(ValueError):
                    dac_mod.arm_summaries_to_dose_curve("S1", [None, None, None],
                                                        measure, "g/day")

    def test_non_mapping_arm_in_rr_is_value_error(self):
        with self.assertRaises(ValueError):
            dac_mod.arm_summaries_to_dose_curve(
                "S1", [{"dose": 0.0, "events": 5, "total": 100}, ["x"], None], "RR", "g/day")

    def test_regular_rr_curve_bit_identical(self):
        """逐位对照：常规 RR 曲线逐位不变。"""
        result = dac_mod.arm_summaries_to_dose_curve("S1", [
            {"dose": 0.0, "events": 10, "total": 100},
            {"dose": 1.0, "events": 12, "total": 100},
            {"dose": 2.0, "events": 15, "total": 100},
        ], "RR", "g/day")
        self.assertEqual([{"dose": 1.0, "estimate": 1.2},
                          {"dose": 2.0, "estimate": 1.4999999999999998}], result["contrasts"])
        self.assertEqual([[0.16333333333333333, 0.09000000000000001],
                          [0.09000000000000001, 0.14666666666666667]], result["covariance"])


class F384NonHashableMeasureTests(unittest.TestCase):
    """b13·F3-84：不可哈希 measure 抛 TypeError 而非 :118 的 ValueError。"""

    ARMS = [{"dose": 0.0, "events": 5, "total": 100},
            {"dose": 1.0, "events": 6, "total": 100},
            {"dose": 2.0, "events": 7, "total": 100}]

    def test_list_measure_is_value_error(self):
        with self.assertRaises(ValueError):
            dac_mod.arm_summaries_to_dose_curve("S1", self.ARMS, ["RR"], "g/day")

    def test_dict_measure_is_value_error(self):
        with self.assertRaises(ValueError):
            dac_mod.arm_summaries_to_dose_curve("S1", self.ARMS, {"m": "RR"}, "g/day")

    def test_unknown_string_measure_still_rejected(self):
        with self.assertRaises(ValueError):
            dac_mod.arm_summaries_to_dose_curve("S1", self.ARMS, "SMD", "g/day")


# --------------------------------------------------------------------------- #
# 整群试验
# --------------------------------------------------------------------------- #
class F280DeepProvenanceTests(unittest.TestCase):
    """b04·F2-80：深嵌套 source_provenance 的 deepcopy 抛 RecursionError → 500。

    修前实测（本工作树）：depth=600 → ``copy.py:229 RecursionError``；
    depth≤400 正常（recursionlimit=1000）。
    """

    def test_deep_provenance_does_not_recurse(self):
        for depth in (600, 994):
            with self.subTest(depth=depth):
                result = cte_mod.calculate_cluster_trial_effect(
                    _cluster_md(source_provenance=_nested(depth)))
                self.assertAlmostEqual(0.09999999999999998, result["estimate"], places=15)
                self.assertAlmostEqual(0.2097617696340303, result["se"], places=15)

    def test_deep_provenance_is_bounded_not_rejected(self):
        limit = cte_mod._MAX_PROVENANCE_DEPTH
        self.assertEqual(32, limit, "deepcopy 深度族的上限必须与 glass/bcc 同值")
        self.assertEqual("<nesting too deep>", cte_mod._DEEP_PROVENANCE_PLACEHOLDER,
                         "deepcopy 深度族的占位符必须与 glass/bcc 同形")
        result = cte_mod.calculate_cluster_trial_effect(
            _cluster_md(source_provenance=_nested(limit + 5)))
        item = result["source_provenance"]
        seen = 0
        while isinstance(item, dict):
            item = item["k"]
            seen += 1
            self.assertLessEqual(seen, limit)
        self.assertEqual(cte_mod._DEEP_PROVENANCE_PLACEHOLDER, item)

    def test_variable_size_carrier_does_not_recurse(self):
        """同族另一载体：cluster_trial_variable_size 的结构化拷贝。"""
        result = ctvs_mod.calculate_cluster_trial_variable_size_effect(
            _vs_values(source_provenance=_nested(600)))
        self.assertTrue(math.isfinite(result["se"]))

    def test_family_form_matches_glass_and_bcc(self):
        """族内形态一致：三个载体同一深度上限、同一占位符。"""
        from coscreen import binary_correlation_conversion as bcc_mod
        from coscreen import glass_delta_effect as glass_mod

        self.assertEqual(glass_mod._MAX_PROVENANCE_DEPTH, cte_mod._MAX_PROVENANCE_DEPTH)
        self.assertEqual(bcc_mod._MAX_PROVENANCE_DEPTH, cte_mod._MAX_PROVENANCE_DEPTH)
        self.assertEqual(glass_mod._DEEP_PROVENANCE_PLACEHOLDER,
                         cte_mod._DEEP_PROVENANCE_PLACEHOLDER)
        self.assertEqual(bcc_mod._DEEP_PROVENANCE_PLACEHOLDER,
                         cte_mod._DEEP_PROVENANCE_PLACEHOLDER)


class F284NonFiniteProvenanceTests(unittest.TestCase):
    """b01·F2-84：嵌套 NaN 穿透校验、input_json 落库裸 NaN（GET 回显 null）。

    修前实测（本工作树）：``source_provenance={"nested": [nan]}`` 原样进
    ``input_data``，``json.dumps(...)`` 产出裸 ``NaN``。
    """

    def test_non_finite_provenance_is_sanitized(self):
        result = ctvs_mod.calculate_cluster_trial_variable_size_effect(
            _vs_values(source_provenance={"nested": [float("nan"), {"w": float("inf"),
                                                                   "v": float("-inf")}]}))
        dumped = json.dumps(result["input_data"], allow_nan=False)
        self.assertNotIn("NaN", dumped)
        self.assertNotIn("Infinity", dumped)
        self.assertEqual({"nested": ["<non-finite nan>",
                                     {"w": "<non-finite inf>", "v": "<non-finite -inf>"}]},
                         result["input_data"]["source_provenance"])
        self.assertEqual(result["input_data"]["source_provenance"],
                         result["source_provenance"])

    def test_base_carrier_sanitizes_too(self):
        result = cte_mod.calculate_cluster_trial_effect(
            _cluster_md(source_provenance={"nested": [float("nan")]}))
        json.dumps(result["input_data"], allow_nan=False)
        self.assertEqual({"nested": ["<non-finite nan>"]}, result["source_provenance"])

    def test_legal_provenance_is_preserved_verbatim(self):
        """不新增 schema 拒绝：合法浅层来源信息逐字保留（R2）。"""
        provenance = {"level1": {"level2": [1, {"a": 2}], "s": "x", "b": True, "n": None}}
        result = cte_mod.calculate_cluster_trial_effect(_cluster_md(source_provenance=provenance))
        self.assertEqual(provenance, result["source_provenance"])
        self.assertEqual(provenance, result["input_data"]["source_provenance"])

    def test_regular_cluster_controls_bit_identical(self):
        """逐位对照：常规整群 MD/OR/RR 结果与 adjust 结构逐位不变。"""
        md = cte_mod.calculate_cluster_trial_effect(_cluster_md())
        self.assertEqual(0.09999999999999998, md["estimate"])
        self.assertEqual(0.2097617696340303, md["se"])
        self.assertEqual([[25.0, 100, 2.2, 45.45454545454545],
                          [25.0, 100, 2.2, 45.45454545454545]],
                         [[arm["mean_cluster_size"], arm["participants"],
                           arm["design_effect"], arm["effective_sample_size"]]
                          for arm in md["cluster_adjustment"]["arms"].values()])
        binary = cte_mod.calculate_cluster_trial_effect({
            "outcome_type": "binary", "measure": "OR", "icc": 0.1,
            "analysis_status": "unadjusted",
            "arms": {"treatment": {"clusters": 5, "participants": 200, "events": 40},
                     "control": {"clusters": 5, "participants": 200, "events": 30}}})
        self.assertEqual(1.4166666666666674, binary["estimate"])
        self.assertEqual(0.34830669426821625, binary["analysis_estimate"])
        self.assertEqual(0.587606894739245, binary["se"])


class F286HugeClusterDfTests(unittest.TestCase):
    """b04·F2-86：每臂 clusters=1e19 → df 超 int64 → student_t.ppf TypeError → 500。

    修前实测（本工作树）：df=2e19 → ``ufunc 'stdtrit' not supported``；
    df≤2**63-1 正常（实测 int df 与 float(df) 的分位数逐位相同）。
    """

    def _values(self, clusters):
        return {"outcome_type": "binary", "measure": "OR", "icc": 0.05,
                "icc_source": "citation", "analysis_status": "unadjusted",
                "few_cluster_ci": True,
                "arms": {"treatment": {"clusters": clusters, "participants": 10 ** 21,
                                       "events": 1000, "cv": 0.3},
                         "control": {"clusters": clusters, "participants": 10 ** 21,
                                     "events": 900, "cv": 0.3}}}

    def test_huge_cluster_counts_yield_a_finite_interval(self):
        result = ctvs_mod.calculate_cluster_trial_variable_size_effect(self._values(10 ** 19))
        self.assertEqual(2 * 10 ** 19 - 2, result["df"])
        self.assertTrue(math.isfinite(result["t_critical"]))
        self.assertAlmostEqual(1.959963984540054, result["t_critical"], places=12)
        self.assertTrue(math.isfinite(result["ci_analysis_low"]))
        self.assertTrue(math.isfinite(result["ci_analysis_high"]))

    def test_int64_boundary_bit_identical(self):
        """df=2**63-2 仍在 int64 内：分位数与基线同值（逐位对照）。"""
        result = ctvs_mod.calculate_cluster_trial_variable_size_effect(self._values(2 ** 62))
        self.assertEqual(2 ** 63 - 2, result["df"])
        self.assertEqual(1.9599639845400536, result["t_critical"])

    def test_regular_few_cluster_controls_bit_identical(self):
        """逐位对照：常规 few-cluster 区间的 df/t/CI 逐位不变。"""
        result = ctvs_mod.calculate_cluster_trial_variable_size_effect({
            "outcome_type": "continuous", "measure": "MD", "icc": 0.05,
            "icc_source": "citation", "analysis_status": "unadjusted", "few_cluster_ci": True,
            "arms": {"treatment": {"clusters": 12, "participants": 240, "mean": 1.2,
                                   "sd": 1.1, "cv": 0.4},
                     "control": {"clusters": 12, "participants": 240, "mean": 1.0,
                                 "sd": 1.0, "cv": 0.4}}})
        self.assertEqual(22, result["df"])
        self.assertEqual(2.073873067904025, result["t_critical"])
        self.assertEqual(0.19999999999999996, result["estimate"])
        self.assertEqual(0.13939004029461122, result["se"])
        self.assertEqual(-0.08907725050105109, result["ci_analysis_low"])
        self.assertEqual(0.489077250501051, result["ci_high"])


class F287FsumOverflowTests(unittest.TestCase):
    """b11·F2-87：CV 修正使 design_effect 极大，OR 方差 fsum 抛 OverflowError。

    修前实测（本工作树）：cv=9.31e153 且 icc=0.5 →
    ``OverflowError: intermediate overflow in fsum``（端点 500）。
    """

    def _values(self, cv, icc=0.5):
        return {"outcome_type": "binary", "measure": "OR", "icc": icc,
                "icc_source": "citation", "analysis_status": "unadjusted",
                "arms": {"treatment": {"clusters": 10, "participants": 20, "events": 1, "cv": cv},
                         "control": {"clusters": 10, "participants": 20, "events": 1, "cv": cv}}}

    def test_overflowing_variance_is_value_error(self):
        """OR 分支在 design_effect≈1e308 时 fsum 中间溢出（清单探针原值）。"""
        with self.assertRaises(ValueError):
            ctvs_mod.calculate_cluster_trial_variable_size_effect(self._values(9.31e153))

    def test_family_helper_contract(self):
        """fsum 溢出族的族内入口：OverflowError → ValueError（两载体共用）。"""
        with self.assertRaises(ValueError):
            cte_mod._finite_fsum([1e308, 1e308])
        self.assertEqual(3.0, cte_mod._finite_fsum([1.0, 2.0]))
        self.assertIs(ctvs_mod._finite_fsum, cte_mod._finite_fsum,
                      "fsum 溢出族必须同用族内唯一入口")

    def test_large_but_representable_cv_still_accepted(self):
        """不新增拒绝：3e153/0.05 的合法大 CV 仍返回有限 SE。"""
        result = ctvs_mod.calculate_cluster_trial_variable_size_effect(self._values(3e153, 0.05))
        self.assertEqual(1.3764944032233708e153, result["se"])


class F288NonFiniteFewClusterCiTests(unittest.TestCase):
    """b11·F2-88：few_cluster_ci 无有限性门控，natural 标度返回 ci=±inf。

    修前实测（本工作树）：mean=1e307 / sd=9e307（icc=0）→
    ci_analysis_low=-inf、ci_high=+inf，``json.dumps(allow_nan=False)``
    ValueError（端点渲染期 500）。
    """

    def _values(self, sd, icc=0.0):
        return {"outcome_type": "continuous", "measure": "MD", "icc": icc,
                "icc_source": "citation", "analysis_status": "unadjusted", "few_cluster_ci": True,
                "arms": {"treatment": {"clusters": 2, "participants": 2, "mean": 1e307,
                                       "sd": sd, "cv": 0.0},
                         "control": {"clusters": 2, "participants": 2, "mean": 0.0,
                                     "sd": 1.0, "cv": 0.0}}}

    def test_overflowing_natural_interval_is_value_error(self):
        for sd in (9e307, 1e308, 1.5e308):
            with self.subTest(sd=sd):
                with self.assertRaises(ValueError) as caught:
                    ctvs_mod.calculate_cluster_trial_variable_size_effect(self._values(sd))
                self.assertIn("representable float range", str(caught.exception))

    def test_accepted_results_never_carry_non_finite_bounds(self):
        """只要不抛错，区间边界必须全部有限且可 JSON 序列化。"""
        for sd in (1e150, 1e155, 1e160):
            with self.subTest(sd=sd):
                try:
                    result = ctvs_mod.calculate_cluster_trial_variable_size_effect(
                        self._values(sd))
                except ValueError:
                    continue
                json.dumps(result, allow_nan=False)
                self.assertTrue(math.isfinite(result["ci_analysis_low"]))
                self.assertTrue(math.isfinite(result["ci_high"]))


# --------------------------------------------------------------------------- #
# HTTP 夹具（F3-75 / F3-76 的端点级钉子）
# --------------------------------------------------------------------------- #
_HTTP_STATE = {}


def _http_task(label: str):
    """真实 app + TestClient（不重抛服务端异常，才能观察到 500），并备好研究库。

    任务库内灌入三篇文章 + 三个已回链研究（``save_curve`` 的 ``_linked`` 前置
    契约），与清单探针夹具同形。
    """
    if "client" not in _HTTP_STATE:
        from fastapi.testclient import TestClient

        from custom_backend import auth as auth_mod
        from custom_backend.main import app

        client = TestClient(app, raise_server_exceptions=False)
        client.__enter__()
        user = auth_mod.create_user(f"r9l6b-{label}@example.test", "R9L6b-Pin-2026")
        _HTTP_STATE["client"] = client
        _HTTP_STATE["headers"] = {
            "Authorization": "Bearer " + auth_mod.create_access_token(user["id"], user["username"])}
        _HTTP_STATE["username"] = user["username"]
    client = _HTTP_STATE["client"]
    response = client.post("/api/tasks", json={"name": f"r9-l6b-{label}"[:60]},
                           headers=_HTTP_STATE["headers"])
    assert response.status_code in {200, 201}, response.text[:300]
    task = response.json()["task_id"]
    from coscreen import tasks as tasks_mod
    from custom_backend.main import DATA_DIR

    info = tasks_mod.find_task(task, DATA_DIR)
    db_path = info.dir / (tasks_mod.slugify_screener(_HTTP_STATE["username"]) + ".db")
    init_db(db_path)
    conn = sqlite3.connect(db_path)
    try:
        for key in ("R1", "R2"):
            conn.execute("INSERT OR IGNORE INTO articles(zotero_key,title) VALUES(?,?)", (key, key))
        conn.commit()
    finally:
        conn.close()
    for study_id, key in (("S1", "R1"), ("S2", "R2")):
        save_study(db_path, study_id, study_id, [key])
    return client, _HTTP_STATE["headers"], db_path, task


if __name__ == "__main__":
    unittest.main()
