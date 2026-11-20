# -*- coding: utf-8 -*-
"""2.1.6 Task3（ROB-01）· 旧 RoB 保存路径错误契约钉子（专属新文件，撤实现必红）。

旧 ``PUT /api/tasks/{task_id}/analysis/risk-of-bias``（main.py 的
``analysis_save_rob``）此前把 ``save_rob`` 的 8 个 ValueError 经全站 ``_bad``
返回**裸英文**文案，前端只能做英文文本匹配。本钉子钉住改造后的契约：

- 8 个失败分支一律 400 + ``{error_code, message_key, params, details, message}``；
  ``message_key`` 固定 ``analysis.rob.legacy_save.<error_code>``（**独立命名
  空间**：v2 的 ``analysis.rob.<code>`` 已注册十语言，复用会让 errOf 命中词典
  并改写 message，令旧编辑器英文匹配失效）；
- ``message``/``details`` 与 ``save_rob`` 原文**逐字节相等**（含 ``": {域名}"``
  后缀）——这是旧客户端兼容红线，故意写成字面量而不是 import 常量：``save_rob``
  的文案一旦被改动，本文件必须红；
- 同义错误复用 v2 词表（``invalid_overall``/``source_unlinked``/
  ``invalid_judgement``/``unsupported_framework``），其余按真实校验语义新码；
- 未知 ValueError 保持安全旧行为（裸字符串 400）；401/403/404/409/422 语义不变；
- 所有失败分支断言 **不写入无效数据**（行数不变）。

播种全部走真实 API（建用户→建任务→建研究并联报告），只有 articles 两行直插
（FK 父表；跑完整文献导入流程对本钉子无意义）。
注意：``RobIn`` 对 overall_rationale/study_id/outcome/timepoint/source_key 有
``min_length=1``，故「必填缺失」必须用**纯空白串**才能落到 ``save_rob`` 的 400
分支；用空串会先被 pydantic 挡成 422（该路径本单不动）。
"""
from __future__ import annotations

import gc
import json
import os
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

#: 数据根隔离只在 setUpClass 的作用域内做（见 RobSaveErrorContractTests）。
#: **不在模块导入期写 os.environ**：合跑时 main 早已被别的模块导入缓存，
#: 导入期设 env 既改不动 main.DATA_DIR（import 时快照），又会把临时根永久
#: 泄漏给同进程后续模块，属 2.1.6 Task2 正在修的同一类病。
from fastapi.testclient import TestClient  # noqa: E402

from coscreen import db as db_mod  # noqa: E402
from coscreen import review_analysis as analysis_mod  # noqa: E402
from custom_backend import auth as auth_mod  # noqa: E402
from custom_backend import main as main_mod  # noqa: E402
from custom_backend.main import _screener_db, _task_or_404, app  # noqa: E402

PASSWORD = "R216-Rob-Save!!"
ROUTE = "/api/tasks/{task}/analysis/risk-of-bias"

_DOMAINS = {
    "randomization process": {"judgement": "low", "rationale": "randomised centrally"},
    "deviations from intended interventions": {"judgement": "low", "rationale": "blinded"},
    "missing outcome data": {"judgement": "low", "rationale": "complete follow-up"},
    "measurement of outcome": {"judgement": "low", "rationale": "objective"},
    "selection of reported result": {"judgement": "low", "rationale": "protocol available"},
}

#: 分支 -> (error_code, params, 原文)。原文为字面量：兼容红线不允许从
#: review_analysis 导入，否则文案被改也不会红。
CASES = {
    "framework": ("unsupported_framework", {},
                  "unsupported risk-of-bias framework"),
    "fields": ("fields_required", {},
               "all result, reviewer, judgement and source fields are required"),
    "overall": ("invalid_overall", {},
                "invalid overall judgement"),
    "overall_rationale": ("overall_rationale_required", {},
                          "overall rationale is required"),
    "domain_judgement": ("invalid_judgement", {"domain": "missing outcome data"},
                         "invalid domain judgement: missing outcome data"),
    "domain_rationale": ("domain_rationale_required", {"domain": "missing outcome data"},
                         "domain rationale is required: missing outcome data"),
    "domains_incomplete": ("domains_incomplete", {},
                           "all framework domains are required for a completed assessment"),
    "source_unlinked": ("source_unlinked", {},
                        "source report must be linked to this study"),
}


def _domains(**override):
    payload = json.loads(json.dumps(_DOMAINS))
    payload.update(override)
    return payload


class RobSaveErrorContractTests(unittest.TestCase):
    """真实路由 + 真实存储：逐分支断言结构化错误与「不写库」。

    数据根隔离（2.1.6 Task2 同类病防复发）：``main.DATA_DIR`` 是**导入时快照**
    的模块常量，而 ``auth.data_dir()`` 每次调用读 ``COBOOKSHELF_DATA_DIR``。
    合跑时 ``main`` 往往已被别的模块先导入，故「导入前设 env」既改不动
    ``DATA_DIR``、又会把临时根永久泄漏给同进程后续模块。本类改为：
    **作用域内**同时 patch 两处（``mock.patch.object(main_mod, "DATA_DIR")``
    + env），先关 ``TestClient`` 生命周期、再停 patch、最后只清理本类自建的
    临时目录并还原 env——绝不删别的模块可能共享的 root、不在导入期写 env。

    资源收回（SafetyR2）：每建立一项资源立即 ``addClassCleanup``（LIFO）。
    unittest 在 ``setUpClass`` 中途抛错时不调用 ``tearDownClass``，但**会**
    执行已登记的类级清理——清理顺序固定为 关 Client → 停 patch → 恢复
    env → 清自有目录，setup 任何一步失败同样依次收回，不再泄漏打开的
    Client / 悬空的 DATA_DIR patch / 改写的环境 / 自建临时目录。
    """

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory(prefix="rf_216_rob_save_")
        cls._root = Path(cls._tmp.name)

        def _cleanup_tmp_dir():
            # Windows 句柄竞态硬化（SafetyR2 箱内实证：同序清理 2/9 次在删目录
            # 时 WinError32——请求机件的瞬态 sqlite 句柄晚一步释放）。先
            # gc.collect() 释放不可达连接再删；若句柄仍被真实引用，照常响亮
            # 失败，绝不吞错。
            gc.collect()
            cls._tmp.cleanup()

        cls.addClassCleanup(_cleanup_tmp_dir)
        # 只为还原而读旧值；下面的 env 写入与 patch 在同一作用域内成对收回。
        cls._saved_data_env = os.environ.get("COBOOKSHELF_DATA_DIR")
        os.environ["COBOOKSHELF_DATA_DIR"] = cls._tmp.name

        def _restore_data_env():
            if cls._saved_data_env is None:
                os.environ.pop("COBOOKSHELF_DATA_DIR", None)
            else:
                os.environ["COBOOKSHELF_DATA_DIR"] = cls._saved_data_env

        cls.addClassCleanup(_restore_data_env)
        cls._patch = mock.patch.object(main_mod, "DATA_DIR", cls._root)
        cls._patch.start()
        cls.addClassCleanup(cls._patch.stop)
        cls.client = TestClient(app)
        cls.client.__enter__()
        cls.addClassCleanup(cls.client.__exit__, None, None, None)
        # 硬门：两处数据根必须都指向本类临时目录，否则立刻失败退出。
        # （这是「默认 data 回落」的直接探测，不再依赖导入顺序。）
        assert Path(main_mod.DATA_DIR).resolve() == cls._root.resolve(), main_mod.DATA_DIR
        assert Path(auth_mod.data_dir()).resolve() == cls._root.resolve(), auth_mod.data_dir()
        user = auth_mod.create_user("rob-save@example.test", PASSWORD)
        cls.user = user
        cls.headers = {"Authorization":
                       "Bearer " + auth_mod.create_access_token(user["id"], user["username"])}
        cls.task = cls.client.post("/api/tasks", json={"name": "rob save errors"},
                                   headers=cls.headers).json()["task_id"]
        # articles 是 review_reports 的 FK 父表；Z1 将被链到 S1，Z2 保持未链接。
        db = _screener_db(_task_or_404(cls.task), user["username"])
        # SafetyR3 F5：sqlite 连接的 ``with conn`` 只管事务提交/回滚，**不关
        # 连接**——测试自有连接必须显式关闭（closing 在事务 with 之外包一层），
        # 文件句柄释放不落给 gc/进程退出；setUpClass 中途抛错时本块已完整走
        # 完，连接确定关闭。下方类清理里的 gc.collect() 硬化保留，是兜底
        # 而非显式关闭的替代。
        with closing(db_mod._connect(db)) as conn:
            with conn:
                conn.execute("INSERT OR IGNORE INTO articles(zotero_key,title) VALUES ('Z1','Report One')")
                conn.execute("INSERT OR IGNORE INTO articles(zotero_key,title) VALUES ('Z2','Report Two')")
        created = cls.client.post(f"/api/tasks/{cls.task}/analysis/studies",
                                  json={"id": "S1", "label": "Study One", "reports": ["Z1"]},
                                  headers=cls.headers)
        assert created.status_code == 200, created.text

    # -- helpers ---------------------------------------------------------
    def _body(self, **over):
        """每个测试默认落在**独立 outcome** 作用域。

        旧路径的 409 ``ambiguous_legacy_scope`` 以
        (study, outcome, timepoint, framework, reviewer) 为判定域，且缺
        comparison 时只要存在同域记录就拒。若各测试共用同一 outcome，
        前序测试写入的合法行会让后续「本该 400」的分支先撞 409。
        故按测试名派生 outcome，测试之间互不干扰。
        """
        payload = {"study_id": "S1", "comparison": "",
                   "outcome": "Pain · " + self._testMethodName,
                   "timepoint": "Week 12", "framework": "RoB 2", "domains": _domains(),
                   "overall": "low", "overall_rationale": "outcome assessors blinded",
                   "source_key": "Z1", "source_locator": "p. 3"}
        payload.update(over)
        return payload

    def _put(self, payload, headers=None):
        return self.client.put(ROUTE.format(task=self.task), json=payload,
                               headers=self.headers if headers is None else headers)

    def _rows(self):
        resp = self.client.get(ROUTE.format(task=self.task), headers=self.headers)
        self.assertEqual(resp.status_code, 200, resp.text)
        return resp.json()["assessments"]

    def _assert_structured(self, resp, key, *, params=None, text=None):
        """status/五键/error_code/message_key/params/原文逐字节。

        ``params``/``text`` 可覆盖（同一码在不同域名下的原文差异用）。
        """
        code, default_params, default_text = CASES[key]
        expected_params = default_params if params is None else params
        expected_text = default_text if text is None else text
        self.assertEqual(resp.status_code, 400, resp.text)
        detail = resp.json()["detail"]
        self.assertIsInstance(detail, dict, detail)
        self.assertEqual(detail["error_code"], code)
        self.assertEqual(detail["message_key"], "analysis.rob.legacy_save." + code)
        self.assertEqual(detail["params"], expected_params)
        self.assertEqual(detail["message"], expected_text)
        self.assertEqual(detail["details"], expected_text)

    # -- 有效保存 ---------------------------------------------------------
    def test_data_root_isolation_is_scoped(self):
        """数据根隔离自证：两处根都在本类临时目录，且全部写入落在其中。

        这条同时钉住「合跑时 main 已被别的模块导入缓存」的场景——若隔离只靠
        导入前设 env，此处 DATA_DIR 会指向别处而变红。
        """
        self.assertEqual(Path(main_mod.DATA_DIR).resolve(), self._root.resolve())
        self.assertEqual(Path(auth_mod.data_dir()).resolve(), self._root.resolve())
        self.assertTrue((self._root / "users.db").is_file(),
                        "users.db 必须落在本次临时数据根")
        self.assertTrue(any(self._root.rglob("*.db")), "任务库必须落在本次临时数据根")

    def test_valid_save_persists(self):
        before = len(self._rows())
        resp = self._put(self._body(comparison="valid"))
        self.assertEqual(resp.status_code, 200, resp.text)
        stored = [row for row in self._rows() if row["comparison"] == "valid"]
        self.assertEqual(len(stored), 1, stored)
        self.assertEqual(stored[0]["overall"], "low")
        self.assertEqual(stored[0]["reviewer"], self.user["username"])
        self.assertEqual(stored[0]["source_key"], "Z1")
        self.assertEqual(len(self._rows()), before + 1)

    # -- 8 个失败分支 -----------------------------------------------------
    def test_framework_unsupported(self):
        self._assert_branch("framework", self._body(framework="RoB 9"))

    def test_fields_required(self):
        # 纯空白串：空串会被 RobIn 的 min_length=1 提前挡成 422。
        self._assert_branch("fields", self._body(outcome="   "))

    def test_overall_invalid(self):
        self._assert_branch("overall", self._body(overall="banana"))

    def test_overall_rationale_required(self):
        self._assert_branch("overall_rationale", self._body(overall_rationale="   "))

    def test_domain_judgement_invalid(self):
        self._assert_branch("domain_judgement",
                            self._body(domains=_domains(**{"missing outcome data":
                                                     {"judgement": "banana", "rationale": "x"}})))

    def test_domain_judgement_invalid_unknown_domain(self):
        """未知域名同样走 invalid_judgement，params.domain 为原文域名。"""
        payload = self._body(domains=_domains(**{"bogus domain": {"judgement": "low", "rationale": "x"}}))
        self._assert_branch("domain_judgement", payload, domain="bogus domain")

    def test_domain_rationale_required(self):
        self._assert_branch("domain_rationale",
                            self._body(domains=_domains(**{"missing outcome data":
                                                     {"judgement": "low", "rationale": "  "}})))

    def test_domains_incomplete(self):
        partial = {k: v for k, v in _domains().items() if k != "measurement of outcome"}
        self._assert_branch("domains_incomplete", self._body(domains=partial))

    def test_source_unlinked(self):
        self._assert_branch("source_unlinked", self._body(source_key="Z2"))

    def test_quadas_domain_with_colon_is_not_truncated(self):
        """QUADAS-2 applicability 域名自身含 ": "：只切首个分隔，params 保留全名。"""
        resp = self._put(self._body(framework="QUADAS-2",
                                    domains={"patient selection": {"judgement": "low",
                                                                   "rationale": "x"},
                                             "applicability: patient selection": {
                                                 "judgement": "banana", "rationale": "x"}}))
        self.assertEqual(resp.status_code, 400, resp.text)
        detail = resp.json()["detail"]
        self.assertEqual(detail["error_code"], "invalid_judgement")
        self.assertEqual(detail["params"], {"domain": "applicability: patient selection"})
        self.assertEqual(detail["message"],
                         "invalid domain judgement: applicability: patient selection")
        self.assertEqual(detail["details"], detail["message"])

    def _assert_branch(self, key, payload, *, domain=None):
        """分支断言 + 失败不写库（行数不变）。

        ``domain`` 非空时按「消息前缀 + 该域名」重算期望原文与 params。
        """
        before = self._rows()
        resp = self._put(payload)
        if domain is None:
            self._assert_structured(resp, key)
        else:
            prefix = CASES[key][2].split(": ", 1)[0] + ": "
            self._assert_structured(resp, key, params={"domain": domain},
                                    text=prefix + domain)
        self.assertEqual(self._rows(), before, "失败请求不得写入无效数据")

    # -- 未知 ValueError 保持旧行为 ----------------------------------------
    def test_unknown_value_error_keeps_legacy_shape(self):
        before = self._rows()
        with mock.patch.object(analysis_mod, "save_rob", side_effect=ValueError("boom")):
            resp = self._put(self._body(comparison="unknown"))
        self.assertEqual(resp.status_code, 400, resp.text)
        self.assertEqual(resp.json()["detail"], "boom")  # 裸字符串，非五键结构
        self.assertEqual(self._rows(), before)

    def test_mapping_does_not_swallow_unrelated_text(self):
        """仅完整前缀/整串命中；同前缀的其它文本不得被结构化。"""
        from custom_backend.main import _legacy_rob_save_error
        self.assertIsInstance(_legacy_rob_save_error(ValueError("invalid domain judgement:  ")),
                              type(_legacy_rob_save_error(ValueError("unsupported risk-of-bias framework"))))
        # 未命中 -> _bad 路径（HTTPException 的 detail 为字符串）
        self.assertEqual(_legacy_rob_save_error(ValueError("domain rationale is required")).detail,
                         "domain rationale is required")

    # -- 既有语义不变 -----------------------------------------------------
    def test_existing_409_ambiguous_scope_unchanged(self):
        for comparison in ("A vs B", "C vs D"):
            saved = self._put(self._body(comparison=comparison))
            self.assertEqual(saved.status_code, 200, saved.text)
        resp = self._put(self._body(comparison=""))
        self.assertEqual(resp.status_code, 409, resp.text)
        detail = resp.json()["detail"]
        self.assertEqual(detail["error_code"], "ambiguous_legacy_scope")
        # 旧键硬编码保留（本单不动 409 分支）
        self.assertEqual(detail["message_key"], "analysis.rob_ambiguous_scope")
        self.assertEqual(detail["params"], {"candidate_count": 2})

    def test_missing_task_404(self):
        resp = self.client.put("/api/tasks/does-not-exist/analysis/risk-of-bias",
                               json=self._body(), headers=self.headers)
        self.assertEqual(resp.status_code, 404, resp.text)
        self.assertNotIsInstance(resp.json()["detail"], dict)

    def test_non_participant_403(self):
        other = auth_mod.create_user("rob-outsider@example.test", PASSWORD)
        headers = {"Authorization":
                   "Bearer " + auth_mod.create_access_token(other["id"], other["username"])}
        resp = self._put(self._body(), headers=headers)
        self.assertEqual(resp.status_code, 403, resp.text)
        self.assertNotIsInstance(resp.json()["detail"], dict)

    def test_anonymous_401(self):
        resp = self._put(self._body(), headers={})
        self.assertEqual(resp.status_code, 401, resp.text)

    def test_pydantic_422_unchanged(self):
        """空串仍在 pydantic 层被挡（422 数组），不被本改造影响。"""
        resp = self._put(self._body(outcome=""))
        self.assertEqual(resp.status_code, 422, resp.text)
        self.assertIsInstance(resp.json()["detail"], list)

    def test_success_body_unchanged(self):
        """成功响应仍是 {"assessments": [...]}，无新增键。"""
        resp = self._put(self._body(comparison="shape"))
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(set(resp.json()), {"assessments"})
        self.assertIsInstance(resp.json()["assessments"], list)


if __name__ == "__main__":
    unittest.main()
