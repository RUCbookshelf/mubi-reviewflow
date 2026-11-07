"""r9 L6c 泳道端点钉子：真实 app + TestClient（不重抛服务端异常，才能看到 500）。

与 ``test_r9_l6c_ipd_family.py`` 的模块级钉子配对，逐条把复审判定里的
「HTTP 500」形态钉成 4xx，并钉住常规请求的 200 与逐位数值：

- b06·F4-96  ``POST /analysis/ipd/study`` 401 位整数结局 → 修前 500，修后 400；
- b06·F4-98  同行 ``arm=[...]`` → 修前 500（unhashable），修后 400；
- b06·F4-94  ``POST /analysis/ipd/survival/adjusted-stratified-cox`` 完全分离
  → 修前 200 + ``hr_ci_low/high = null``，修后 400 且说明分离；
- b05·F4-101 ``POST .../survival/multi-adjusted-stratified-cox`` 协变量名 ``event``
  → 修前 200 且 ``covariate_effects['event']`` 为「校正结局」的伪估计，修后 400；
- b05·F4-104 ``POST .../survival/two-stage-random-slopes`` 两研究反向完全分离
  → 修前 200 + ``pooled_hazard_ratio≈1``、``tau2=0``，修后 400；
- b16·F4-95  同上端点（adjusted）在 :174 fsum 溢出 → 修前 500，修后 400。
"""
import math
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

#: HTTP 面的数据目录必须在导入 custom_backend 之前定好（同 test_r9_l6a 手法）。
_HTTP_FIXTURE = tempfile.TemporaryDirectory(prefix="rf_r9_l6c_http_",
                                            ignore_cleanup_errors=True)
os.environ["COBOOKSHELF_DATA_DIR"] = _HTTP_FIXTURE.name
os.environ["REVIEWFLOW_MODE"] = "server"

from coscreen import ipd_adjusted_cox, ipd_cox_extended  # noqa: E402

_ADJUSTED_CONTROL = [
    {"study_id": "S1", "duration": 9.0, "event": 1, "treatment": 1, "covariate": 0.4},
    {"study_id": "S1", "duration": 8.5, "event": 0, "treatment": 1, "covariate": 0.1},
    {"study_id": "S1", "duration": 4.0, "event": 1, "treatment": 0, "covariate": -0.3},
    {"study_id": "S1", "duration": 3.5, "event": 1, "treatment": 1, "covariate": -0.2},
    {"study_id": "S1", "duration": 7.0, "event": 0, "treatment": 0, "covariate": 0.7},
    {"study_id": "S1", "duration": 2.5, "event": 1, "treatment": 1, "covariate": -0.6},
    {"study_id": "S1", "duration": 8.0, "event": 1, "treatment": 0, "covariate": 0.9},
    {"study_id": "S1", "duration": 6.5, "event": 0, "treatment": 0, "covariate": 0.2},
    {"study_id": "S1", "duration": 3.0, "event": 1, "treatment": 1, "covariate": 0.05},
    {"study_id": "S1", "duration": 5.5, "event": 0, "treatment": 1, "covariate": 0.33},
    {"study_id": "S1", "duration": 7.5, "event": 0, "treatment": 0, "covariate": -0.8},
    {"study_id": "S1", "duration": 2.0, "event": 1, "treatment": 0, "covariate": 0.6},
]

_SEPARATED_ROWS = [
    {"study_id": "S1", "duration": float(index), "event": 1,
     "treatment": 1 if index % 2 else 0, "covariate": float(10 - index)}
    for index in range(1, 11)
]

_TWO_STAGE_BASE = [
    ("S1", 9.0, 1, 1), ("S1", 8.5, 0, 1), ("S1", 4.0, 1, 0), ("S1", 3.5, 1, 1),
    ("S1", 7.0, 0, 0), ("S1", 2.5, 1, 1), ("S1", 8.0, 1, 0), ("S1", 6.5, 0, 0),
    ("S1", 3.0, 1, 1), ("S1", 5.5, 0, 1), ("S1", 7.5, 0, 0), ("S1", 2.0, 1, 0),
    ("S2", 6.0, 1, 0), ("S2", 5.5, 1, 1), ("S2", 2.0, 1, 0), ("S2", 4.5, 0, 1),
    ("S2", 7.5, 0, 0), ("S2", 1.5, 1, 1), ("S2", 8.0, 1, 0), ("S2", 6.0, 0, 0),
    ("S2", 2.5, 1, 0), ("S2", 3.5, 0, 1), ("S2", 5.0, 0, 0), ("S2", 1.0, 1, 1),
]


def _multi_rows(studies=("S1", "S2"), per_study=20):
    rows = []
    for sid in studies:
        for index in range(per_study):
            rows.append({"study_id": sid, "duration": float(per_study - index),
                         "event": 1 if index % 3 else 0,
                         "treatment": 1 if index % 2 else 0,
                         "age": 40.0 + (index % 9)})
    return rows


def _two_stage_separated(per_arm=15):
    rows = []
    for sid, early_arm in (("S1", 1), ("S2", 0)):
        for arm in (0, 1):
            start = 1 if arm == early_arm else per_arm + 1
            for index in range(per_arm):
                rows.append({"study_id": sid, "duration": float(start + index),
                             "event": 1, "treatment": arm})
    return rows


class _IpdEndpointBase(unittest.TestCase):
    """真实 app + TestClient（``raise_server_exceptions=False`` 才看得到 500）。"""

    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient

        from coscreen import tasks as tasks_mod
        from custom_backend import auth as auth_mod
        from custom_backend.main import app, DATA_DIR

        cls.client = TestClient(app, raise_server_exceptions=False)
        cls.client.__enter__()
        user = auth_mod.create_user(f"r9l6c-{cls.__name__.lower()}@example.test",
                                    "R9L6c-Pin-2026")
        cls.headers = {"Authorization": "Bearer " + auth_mod.create_access_token(
            user["id"], user["username"])}
        response = cls.client.post("/api/tasks",
                                   json={"name": f"r9-l6c-{cls.__name__.lower()}"[:60]},
                                   headers=cls.headers)
        assert response.status_code in {200, 201}, response.text[:300]
        cls.task = response.json()["task_id"]
        info = tasks_mod.find_task(cls.task, DATA_DIR)
        cls.db = info.dir / (tasks_mod.slugify_screener(user["username"]) + ".db")
        from coscreen.db import init_db
        init_db(cls.db)

        from coscreen import db as db_mod
        from coscreen.models import Article
        from coscreen.review_analysis import save_study

        db_mod.upsert_articles(cls.db, [Article(zotero_key=f"K{i}", title=f"T{i}")
                                        for i in range(1, 4)], [])
        save_study(cls.db, "S1", "Study 1", ["K1"])
        save_study(cls.db, "S2", "Study 2", ["K2"])
        save_study(cls.db, "S3", "Study 3", ["K3"])

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    def _base(self):
        return f"/api/tasks/{self.task}"


class HttpF496F498IpdStudy(_IpdEndpointBase):
    """b06·F4-96 / F4-98：同入口两种非法值都必须 400，且常规值照常 200。"""

    def _post(self, participants):
        return self.client.post(
            self._base() + "/analysis/ipd/study",
            json={"study_id": "S1", "comparison": "A vs B", "outcome": "Y",
                  "timepoint": "T", "measure": "MD", "effect_direction": "first_vs_second",
                  "source_key": "K1", "participants": participants},
            headers=self.headers)

    def _normal(self):
        return [{"arm": "treatment", "value": 1}, {"arm": "treatment", "value": 2},
                {"arm": "control", "value": 3}, {"arm": "control", "value": 4}]

    def test_huge_integer_outcome_is_400(self):
        participants = self._normal()
        participants[0]["value"] = 10 ** 400
        response = self._post(participants)
        self.assertEqual(400, response.status_code,
                         f"修前此处为 500（OverflowError）：{response.text[:300]}")
        self.assertIn("finite numbers", response.json()["detail"])

    def test_unhashable_arm_is_400(self):
        participants = self._normal()
        participants[0]["arm"] = ["treatment"]
        response = self._post(participants)
        self.assertEqual(400, response.status_code,
                         f"修前此处为 500（TypeError unhashable）：{response.text[:300]}")
        self.assertIn("arm", response.json()["detail"])

    def test_normal_study_is_200_with_baseline_digits(self):
        response = self._post(self._normal())
        self.assertEqual(200, response.status_code, response.text[:300])
        body = response.json()
        self.assertEqual(-2.0, body["estimate"])
        self.assertEqual(0.7071067811865476, body["se"])
        self.assertEqual({"n_t": 2, "mean_t": 1.5, "sd_t": 0.7071067811865476,
                          "n_c": 2, "mean_c": 3.5, "sd_c": 0.7071067811865476},
                         body["summary"])


class HttpF494AdjustedSeparation(_IpdEndpointBase):
    """b06·F4-94：完全分离不得再以 200 + null CI 的形式「成功」。"""

    def test_separation_is_400(self):
        response = self.client.post(
            self._base() + "/analysis/ipd/survival/adjusted-stratified-cox",
            json={"participants": _SEPARATED_ROWS, "study_sources": {"S1": "K1"},
                  "ties": "efron", "covariate_center": 0.0, "covariate_scale": 1.0},
            headers=self.headers)
        self.assertEqual(400, response.status_code,
                         f"修前此处为 200（hr_ci=null 的假阳性）：{response.text[:300]}")
        self.assertIn("separate the groups", response.json()["detail"])

    def test_normal_adjusted_fit_is_200_with_baseline_digits(self):
        response = self.client.post(
            self._base() + "/analysis/ipd/survival/adjusted-stratified-cox",
            json={"participants": _ADJUSTED_CONTROL, "study_sources": {"S1": "K1"},
                  "ties": "efron", "covariate_center": 0.0, "covariate_scale": 1.0},
            headers=self.headers)
        self.assertEqual(200, response.status_code, response.text[:300])
        body = response.json()
        self.assertEqual(-0.052537670619041, body["log_hr"])
        self.assertEqual(0.8410243360302041, body["se"])
        self.assertEqual(0.9488185777993869, body["hazard_ratio"])
        self.assertEqual(0.18251643059401804, body["hr_ci_low"])
        self.assertEqual(4.93246931603514, body["hr_ci_high"])


class HttpF495AdjustedFsumOverflow(_IpdEndpointBase):
    """b16·F4-95：:174 fsum 溢出必须 400，而不是逃逸成 500。"""

    def test_forced_event_term_overflow_is_400(self):
        real = ipd_adjusted_cox.fsum

        def raiser(values):
            if isinstance(values, list):
                raise OverflowError("intermediate overflow in fsum")
            return real(values)

        ipd_adjusted_cox.fsum = raiser
        try:
            response = self.client.post(
                self._base() + "/analysis/ipd/survival/adjusted-stratified-cox",
                json={"participants": _ADJUSTED_CONTROL, "study_sources": {"S1": "K1"},
                      "ties": "efron", "covariate_center": 0.0, "covariate_scale": 1.0},
                headers=self.headers)
        finally:
            ipd_adjusted_cox.fsum = real
        self.assertEqual(400, response.status_code,
                         f"修前此处为 500（OverflowError 未转 ValueError）："
                         f"{response.text[:300]}")
        self.assertIn("outside numeric range", response.json()["detail"])


class HttpF4101ReservedCovariate(_IpdEndpointBase):
    """b05·F4-101：协变量名 event/duration 必须 400（守护科学解释）。"""

    def test_reserved_name_is_400(self):
        for name in ("event", "duration"):
            response = self.client.post(
                self._base() + "/analysis/ipd/survival/multi-adjusted-stratified-cox",
                json={"participants": _multi_rows(), "study_sources": {"S1": "K1", "S2": "K2"},
                      "ties": "efron", "covariate_centers": {name: 0.5},
                      "covariate_scales": {name: 1.0}},
                headers=self.headers)
            self.assertEqual(400, response.status_code,
                             f"修前此处为 200（把 {name} 当协变量校正）：{response.text[:300]}")
            self.assertIn("reserved row fields", response.json()["detail"])

    def test_age_covariate_is_200_with_baseline_digits(self):
        response = self.client.post(
            self._base() + "/analysis/ipd/survival/multi-adjusted-stratified-cox",
            json={"participants": _multi_rows(), "study_sources": {"S1": "K1", "S2": "K2"},
                  "ties": "efron", "covariate_centers": {"age": 55.0},
                  "covariate_scales": {"age": 10.0}},
            headers=self.headers)
        self.assertEqual(200, response.status_code, response.text[:300])
        body = response.json()
        self.assertEqual(0.3121481487917157, body["log_hr"])
        self.assertEqual(0.398270555787086, body["se"])
        self.assertEqual(2.7750914170503966, body["covariate_effects"]["age"]["log_hr"])


class HttpF4102CovariateCap(_IpdEndpointBase):
    """b05·F4-102：协变量上限在模块层，端点翻译成 400（view 层不动）。"""

    def test_oversized_covariate_set_is_400(self):
        count = ipd_cox_extended._MAX_COVARIATES + 1
        rows = _multi_rows()
        centers = {f"c{index}": 0.0 for index in range(count)}
        scales = {f"c{index}": 1.0 for index in range(count)}
        for row in rows:
            for index in range(count):
                row[f"c{index}"] = 0.0
        response = self.client.post(
            self._base() + "/analysis/ipd/survival/multi-adjusted-stratified-cox",
            json={"participants": rows, "study_sources": {"S1": "K1", "S2": "K2"},
                  "ties": "efron", "covariate_centers": centers, "covariate_scales": scales},
            headers=self.headers)
        self.assertEqual(400, response.status_code,
                         f"修前此处会进入 width^2 分配：{response.text[:300]}")
        self.assertIn(str(ipd_cox_extended._MAX_COVARIATES), response.json()["detail"])


class HttpF4104TwoStageSeparation(_IpdEndpointBase):
    """b05·F4-104：两阶段完全分离必须 400，不得返回 HR≈1、tau2=0 的假阳性。"""

    def test_two_stage_separation_is_400(self):
        response = self.client.post(
            self._base() + "/analysis/ipd/survival/two-stage-random-slopes",
            json={"participants": _two_stage_separated(),
                  "study_sources": {"S1": "K1", "S2": "K2"},
                  "ties": "efron", "tau2_method": "DL"},
            headers=self.headers)
        self.assertEqual(400, response.status_code,
                         f"修前此处为 200（HR≈1、tau2=0 的误导结果）：{response.text[:300]}")
        self.assertIn("separate the groups", response.json()["detail"])

    def test_two_stage_control_is_200_with_baseline_digits(self):
        rows = [{"study_id": s, "duration": d, "event": e, "treatment": t}
                for s, d, e, t in _TWO_STAGE_BASE]
        response = self.client.post(
            self._base() + "/analysis/ipd/survival/two-stage-random-slopes",
            json={"participants": rows, "study_sources": {"S1": "K1", "S2": "K2"},
                  "ties": "efron", "tau2_method": "DL"},
            headers=self.headers)
        self.assertEqual(200, response.status_code, response.text[:300])
        body = response.json()
        self.assertEqual(0.513116806120782, body["pooled_log_hr"])
        self.assertEqual(0.6183281682188588, body["se"])
        self.assertEqual(0.0, body["tau2"])
        self.assertEqual(0.9330580960776236, body["q"])


class HttpF4105F4106Interaction(_IpdEndpointBase):
    """b05·F4-105 / F4-106：研究数上限与均值聚合溢出都必须 400。"""

    def _rows(self, studies):
        rows = []
        for index in range(studies):
            for offset in range(4):
                rows.append({"study_id": f"S{index}", "treatment": offset % 2,
                             "outcome": (offset // 2) % 2,
                             "covariate": 0.5 + 0.5 * offset})
        return rows

    def test_unlinked_study_count_is_400_before_the_module_cap(self):
        """HTTP 面的更外层护栏先命中：每个 study 必须与本任务的研究关联。

        k 上限本身在模块层（``test_r9_l6c_ipd_family`` 的
        ``F4105InteractionStudyCap``）；端点对未链接研究先 400，故 HTTP 面
        要到「任务内真有 501 个研究」才走得到模块上限——这里钉住这个次序，
        说明新增的模块上限不会改变既有的 400 文案。
        """
        studies = 501
        response = self.client.post(
            self._base() + "/analysis/ipd/interaction/logistic",
            json={"rows": self._rows(studies),
                  "study_sources": {f"S{i}": "K1" for i in range(studies)},
                  "covariate_center": 1.0},
            headers=self.headers)
        self.assertEqual(400, response.status_code, response.text[:300])
        self.assertEqual("each study source must be linked to its study in this task",
                         response.json()["detail"])

    def test_mean_overflow_is_400(self):
        largest = sys.float_info.max
        rows = []
        for study in ("S1", "S2"):
            for arm in (0, 1):
                for _ in range(14):
                    rows.append({"study_id": study, "treatment": arm,
                                 "outcome": 1, "covariate": largest})
                rows.append({"study_id": study, "treatment": arm, "outcome": 0,
                             "covariate": math.nextafter(largest, 0.0)})
        response = self.client.post(
            self._base() + "/analysis/ipd/interaction/logistic",
            json={"rows": rows, "study_sources": {"S1": "K1", "S2": "K2"},
                  "covariate_center": 1.0},
            headers=self.headers)
        self.assertEqual(400, response.status_code, response.text[:300])
        self.assertIn("representable numeric range", response.json()["detail"])

    def test_interaction_control_is_200_with_baseline_digits(self):
        rows = []
        for study, shift in (("S1", 0.0), ("S2", 0.4), ("S3", -0.3)):
            for index in range(30):
                rows.append({"study_id": study, "treatment": index % 2,
                             "outcome": 1 if (index * 7 + int(shift * 10)) % 3 else 0,
                             "covariate": round(shift + (index % 11) * 0.21 - 1.0, 4)})
        response = self.client.post(
            self._base() + "/analysis/ipd/interaction/logistic",
            json={"rows": rows, "study_sources": {"S1": "K1", "S2": "K2", "S3": "K3"},
                  "covariate_center": 0.0},
            headers=self.headers)
        self.assertEqual(200, response.status_code, response.text[:300])
        body = response.json()
        self.assertEqual(1.0060283118084776, body["treatment_or_at_reference"]["or"])
        self.assertEqual(0.3648758125490206,
                         body["within_study_interaction_or_per_unit"]["or"])


if __name__ == "__main__":
    unittest.main()
