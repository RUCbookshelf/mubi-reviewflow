"""review67 low 泳道钉子·后端批（专属新文件，撤对应修复必红）。

覆盖（编号与 out/code_review_2026-11-12/REPORT.md 一致）：
- L-01：_trace_context 的 ImportError 兜底集合过滤 None——legacy（无属主）任务
  _coding_owner_db 返回 None 时 sqlite3.connect(None) 抛 TypeError（非
  sqlite3.Error）穿透 503 兜底落全局 500；修复后普通保存路径保留，
  recording 状态仍 503。
- L-02：W13 遗留死三函数 _lr_validate_measure/_lr_history_entry/
  _lr_ranker_for_task 删除（全仓无调用方）；ActiveLearningRanker 导入保留。
- L-03：set_al_profile docstring 改写为真实行为（个人 al_settings_{slug}.json，
  非 task.json 写入）。
- L-04：三处「异常文案判状态码」改为按库内状态判定（404 存在性复查；
  409 修订号预检），core 文案改写不再静默改变状态码。
- L-13：r12 CSV 保存与 server-save 对协作库锁忙 OperationalError 补 503
  （对齐 arbitration_session 口径），不再落全局 500。
- L-42：bayesian_robust_view 死 replace 与 no-op 重赋值删除——FISHER_Z 标签
  由 meta 层原生产出（fisher_z / fisher_z_sd / 概览不含 natural）。
- L-43：dta_exploratory_lr 过滤字段 strip + 未知值/零行显式诊断
  （对齐 F3-94），不再被「at least two studies」掩盖真因。
- L-45：trace 导入 commit 别名查找键改真值回退链（actor_id 为 null 时不再
  产出幻影键 'None' → KeyError → 500）。
- L-46：_trace_validate_subject 对 legacy（无属主）任务 None 守卫 → 结构化
  400（对齐 validate_subject 缺位分支文案），不再 TypeError → 500。
- L-47：prisma_task_metrics.decisions 锁忙/IO → 503、永久损坏 → 跳过该成员
  （对齐 screening_arbitration 同库读口径）。
- L-48：个人撤销对 previous 快照引用的报告下线/并入重复组给专用 409
  （对齐 undo_shared），不再通用 422 误导。
"""
import json
import os
import sqlite3
import sys
import tempfile
import unittest
import uuid
from contextlib import closing
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
# 前缀/用户名/任务名保持短：Windows MAX_PATH≈260，trace 迁移备份会在
# 任务目录里生成「.库名.research-trace-*-pre-migration-时间戳-哈希.bak.随机.tmp」
# 形态的长文件名，长用例名会把路径顶破 260 触发 FileNotFoundError。
fixture = tempfile.TemporaryDirectory(prefix="rf_r67b_")
os.environ["COBOOKSHELF_DATA_DIR"] = fixture.name
os.environ["REVIEWFLOW_MODE"] = "server"

from fastapi import HTTPException
from fastapi.testclient import TestClient

from custom_backend import main as backend_main
from custom_backend.main import app
from custom_backend import auth as auth_mod
from custom_backend import prisma_task_metrics as ptm
from custom_backend.report_evidence_api import DEFAULT
from coscreen import db as db_mod
from coscreen import review_analysis, dta_analysis, tasks as tasks_mod
from coscreen import research_trace, research_trace_export as exporter
from coscreen import screening_arbitration as sa_mod
from coscreen.models import Article


def _make_account(tag: str) -> dict:
    """直接建真实用户签票（不走 /api/auth/register 的共享限流桶，
    与 test_r7_lastfix_api.py 同款口径）。用户名刻意短（见模块头 MAX_PATH 说明）。"""
    user = auth_mod.create_user(f"r67b{tag}@e.dev", "R67-Lows-2026")
    return {"user_id": user["id"], "username": user["username"],
            "auth": {"Authorization": "Bearer " +
                     auth_mod.create_access_token(user["id"], user["username"])}}


class _ApiBase(unittest.TestCase):
    """真实 app + TestClient（raise_server_exceptions=False：撤修复时未捕获
    异常以 500 响应形态现身使钉子变红，而不是在测试内抛出）。"""

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app, raise_server_exceptions=False)
        cls.client.__enter__()
        # 短标签（如 "04dir"）→ 短用户名 → 短库文件名（MAX_PATH 说明见模块头）；
        # 取 5 字符保证三类 L04（direction/moderator/design）互不撞名。
        cls.account = _make_account(cls.__name__[1:6].lower())
        cls.auth = cls.account["auth"]

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    def new_task(self) -> tuple[str, object]:
        task = self.client.post(
            "/api/tasks",
            json={"name": f"{type(self).__name__[1:4]}-{self._testMethodName[-24:]}"},
            headers=self.auth).json()["task_id"]
        return task, tasks_mod.find_task(task, backend_main.DATA_DIR)

    # 注：setUpClass 里取类名用 cls.__name__（type(cls) 是元类，恒为 "type"）

    def screener_db(self, info) -> Path:
        return backend_main._screener_db(info, self.account["username"])


# ---------------------------------------------------------------------------
# L-01 / L-46：main.py 的 legacy（无属主）None 守卫
# ---------------------------------------------------------------------------
class L01TraceContextLegacyGuardTests(_ApiBase):

    def test_legacy_none_owner_keeps_plain_save_path(self):
        """撤修复必红：修复前 sqlite3.connect(None) 抛 TypeError（非 sqlite3.Error，
        穿透 503 兜底），普通保存路径被炸断。"""
        _, info = self.new_task()
        plain = Path(self.enterContext(tempfile.TemporaryDirectory())) / "screener.db"
        sqlite3.connect(plain).close()  # 无 trace_settings 的普通空库
        with mock.patch.object(backend_main, "_screener_db", return_value=plain), \
                mock.patch.object(backend_main, "_coding_owner_db", return_value=None), \
                mock.patch.dict(sys.modules, {"coscreen.research_trace": None}):
            result = backend_main._trace_context(
                info, {"username": "u", "user_id": 7, "_trace_request_id": None})
        self.assertIsNone(result)  # 模块失败 + legacy：普通保存路径保留

    def test_recording_status_still_503_with_legacy_owner(self):
        """契约对照：可选模块失败但筛选员处于 recording 时仍 503（既有语义
        不因 None 过滤丢失）。trace_settings 用真实 update_settings 产品路径
        建表写入。"""
        _, info = self.new_task()
        recording = Path(self.enterContext(tempfile.TemporaryDirectory())) / "screener.db"
        trace_ctx = research_trace.TraceContext(
            task_id=info.task_id, actor_id=7, source="manual",
            request_id=str(uuid.uuid4()))
        with closing(sqlite3.connect(recording)) as conn:
            research_trace.update_settings(
                conn, trace_ctx, stages=("coding",), status="recording",
                operation_id=str(uuid.uuid4()), expected_version=0)
        with mock.patch.object(backend_main, "_screener_db", return_value=recording), \
                mock.patch.object(backend_main, "_coding_owner_db", return_value=None), \
                mock.patch.dict(sys.modules, {"coscreen.research_trace": None}):
            with self.assertRaises(HTTPException) as ctx:
                backend_main._trace_context(
                    info, {"username": "u", "user_id": 7, "_trace_request_id": None})
        self.assertEqual(ctx.exception.status_code, 503)


class L46TraceValidateSubjectGuardTests(_ApiBase):

    def test_legacy_owner_is_structured_400_not_500(self):
        """撤修复必红：修复前 get_article(None,…) → os.path.exists(None) 抛
        TypeError → 500；修复后与 validate_subject 缺位分支同口径 400。"""
        _, info = self.new_task()
        with mock.patch.object(backend_main, "_coding_owner_db", return_value=None):
            with self.assertRaises(HTTPException) as ctx:
                backend_main._trace_validate_subject(info, "coding", "R1")
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("keep it unlinked", str(ctx.exception.detail))

    def test_real_owner_db_lookup_unchanged(self):
        """对照：有属主库时按文章存在性返回布尔（既有行为不变）。"""
        _, info = self.new_task()
        db = self.screener_db(info)
        db_mod.upsert_articles(db, [Article(zotero_key="R1", title="Report 1")], [])
        with mock.patch.object(backend_main, "_coding_owner_db", return_value=db):
            self.assertIs(backend_main._trace_validate_subject(info, "coding", "R1"), True)
            self.assertIs(backend_main._trace_validate_subject(info, "coding", "R9"), False)


# ---------------------------------------------------------------------------
# L-02 / L-03：死代码删除与 docstring 失真改写
# ---------------------------------------------------------------------------
class L02DeadLrHelpersRemovedTests(unittest.TestCase):

    def test_dead_helpers_removed_and_live_imports_kept(self):
        for name in ("_lr_validate_measure", "_lr_history_entry", "_lr_ranker_for_task"):
            self.assertFalse(hasattr(backend_main, name), f"{name} 应已删除（review67 L-02）")
        # 删除不得伤及仍在使用的导入（过度删除守卫）
        self.assertTrue(hasattr(backend_main, "ActiveLearningRanker"))
        self.assertTrue(hasattr(backend_main, "analysis_mod"))


class L03AlProfileDocstringTests(unittest.TestCase):

    def test_docstring_describes_per_user_file(self):
        doc = backend_main.set_al_profile.__doc__ or ""
        self.assertIn("al_settings_{slug}.json", doc)  # 真实落点：个人设置文件
        self.assertNotIn("round-trip", doc)  # 失真的 task.json round-trip 声明移除


# ---------------------------------------------------------------------------
# L-04：状态码按库内状态判定，不再钉死 core 异常文案
# ---------------------------------------------------------------------------
class L04DirectionStatusByStateTests(_ApiBase):

    def _seed(self):
        task, info = self.new_task()
        db = self.screener_db(info)
        db_mod.upsert_articles(db, [Article(zotero_key="R1", title="Report 1")], [])
        review_analysis.save_study(db, "S1", "Study 1", ["R1"])
        result_id = review_analysis.save_effect(db, "S1", "cmp", "out", "tp", "MD", 0.5, 0.1, "R1")
        return task, db, result_id

    def test_missing_effect_is_404_and_present_is_200(self):
        task, db, result_id = self._seed()
        base = f"/api/tasks/{task}/analysis/effects"
        r = self.client.post(f"{base}/999/direction",
                             json={"effect_direction": "first_vs_second", "source_locator": "p1"},
                             headers=self.auth)
        self.assertEqual(r.status_code, 404, r.text[:300])
        r = self.client.post(f"{base}/{result_id}/direction",
                             json={"effect_direction": "first_vs_second", "source_locator": "p1"},
                             headers=self.auth)
        self.assertEqual(r.status_code, 200, r.text[:300])

    def test_404_survives_core_message_rewrite(self):
        """撤修复必红：core 文案改写后（此处以改写消息模拟），旧的全等文案
        判定会把 404 静默降级成 400；状态判定不受影响。"""
        task, db, result_id = self._seed()
        with mock.patch.object(backend_main.analysis_mod, "confirm_effect_direction",
                               side_effect=ValueError("gone (rewritten message)")):
            r = self.client.post(f"/api/tasks/{task}/analysis/effects/999/direction",
                                 json={"effect_direction": "first_vs_second", "source_locator": "p1"},
                                 headers=self.auth)
        self.assertEqual(r.status_code, 404, r.text[:300])
        # 对照：效应存在时改写消息仍是普通 400（契约错误）
        with mock.patch.object(backend_main.analysis_mod, "confirm_effect_direction",
                               side_effect=ValueError("gone (rewritten message)")):
            r = self.client.post(f"/api/tasks/{task}/analysis/effects/{result_id}/direction",
                                 json={"effect_direction": "first_vs_second", "source_locator": "p1"},
                                 headers=self.auth)
        self.assertEqual(r.status_code, 400, r.text[:300])


class L04ModeratorRevisionConflictTests(_ApiBase):

    def setUp(self):
        super().setUp()
        self.task, info = self.new_task()
        db = self.screener_db(info)
        db_mod.upsert_articles(db, [Article(zotero_key="R1", title="Report 1")], [])
        review_analysis.save_study(db, "S1", "Study 1", ["R1"])
        self.result_id = review_analysis.save_effect(db, "S1", "cmp", "out", "tp", "MD", 0.5, 0.1, "R1")
        self.url = f"/api/tasks/{self.task}/analysis/effects/{self.result_id}/moderators"

    def _put(self, **overrides):
        body = {"name": "age", "value": 1, "value_type": "continuous",
                "source_key": "R1", "source_locator": "p1", "expected_revision": 0}
        body.update(overrides)
        return self.client.put(self.url, json=body, headers=self.auth)

    def test_revision_conflict_409_then_contract_error_400(self):
        r = self._put()
        self.assertEqual(r.status_code, 200, r.text[:300])
        self.assertEqual(r.json()["revision"], 1)
        r = self._put()  # 修订号过期（库内已是 1）
        self.assertEqual(r.status_code, 409, r.text[:300])
        self.assertEqual(r.json()["detail"]["error_code"], "result_moderator_conflict")
        r = self._put(expected_revision=1, source_key="R9")  # 契约错误：来源未链接
        self.assertEqual(r.status_code, 400, r.text[:300])
        self.assertEqual(r.json()["detail"]["error_code"], "result_moderator_invalid")

    def test_conflict_409_fires_without_core_text(self):
        """撤修复必红：core 不抛（stub 直接成功）且修订号过期时，旧代码无异常
        可判 → 200；状态预检在调用前给出 409。"""
        with mock.patch("coscreen.analysis_storage.save_result_moderator",
                        return_value={"stub": True}) as stub:
            r = self._put(expected_revision=7)  # 库内当前 0 ≠ 7
            self.assertEqual(r.status_code, 409, r.text[:300])
            self.assertEqual(r.json()["detail"]["error_code"], "result_moderator_conflict")
            stub.assert_not_called()  # 预检先行，未写入
            r = self._put()  # 修订号新鲜 → 正常走 core
            self.assertEqual(r.status_code, 200, r.text[:300])
            self.assertEqual(r.json(), {"stub": True})
            stub.assert_called_once()


class L04DesignRevisionConflictTests(_ApiBase):

    def setUp(self):
        super().setUp()
        self.task, info = self.new_task()
        db = self.screener_db(info)
        db_mod.upsert_articles(db, [Article(zotero_key="R1", title="Report 1")], [])
        review_analysis.save_study(db, "S1", "Study 1", ["R1"])
        self.result_id = review_analysis.save_effect(db, "S1", "cmp", "out", "tp", "MD", 0.5, 0.1, "R1")
        self.url = f"/api/tasks/{self.task}/analysis/effects/{self.result_id}/design"

    def _put(self, expected_revision):
        return self.client.put(self.url, json={
            "cluster_id": "C1", "sample_id": "P1",
            "structure_note": "same cohort", "expected_revision": expected_revision},
            headers=self.auth)

    def test_revision_progression_200_409_200(self):
        r = self._put(0)
        self.assertEqual(r.status_code, 200, r.text[:300])
        self.assertEqual(r.json()["revision"], 1)
        r = self._put(0)  # 过期修订号
        self.assertEqual(r.status_code, 409, r.text[:300])
        self.assertEqual(r.json()["detail"]["error_code"], "effect_design_conflict")
        r = self._put(1)
        self.assertEqual(r.status_code, 200, r.text[:300])
        self.assertEqual(r.json()["revision"], 2)

    def test_unknown_result_stays_400_not_409(self):
        """效应不存在时不抢答 409，仍按 core 的 unknown result ID → 400。"""
        url = f"/api/tasks/{self.task}/analysis/effects/999/design"
        r = self.client.put(url, json={
            "cluster_id": "C1", "sample_id": "P1",
            "structure_note": "same cohort", "expected_revision": 5},
            headers=self.auth)
        self.assertEqual(r.status_code, 400, r.text[:300])


# ---------------------------------------------------------------------------
# L-13：仲裁保存链对协作库锁忙 OperationalError 的 503
# ---------------------------------------------------------------------------
class L13ArbitrationSaveLocked503Tests(_ApiBase):

    def test_csv_upload_locked_store_is_503(self):
        """撤修复必红：修复前 append_decision_arbitration 的 OperationalError
        落全局 500。"""
        task, _ = self.new_task()
        csv_bytes = b"zotero_key,decision\nK1,include\n"
        with mock.patch.object(sa_mod, "merge_decision_packages", return_value=object()), \
                mock.patch.object(sa_mod, "append_decision_arbitration",
                                   side_effect=sqlite3.OperationalError("database is locked")):
            r = self.client.post(
                f"/api/tasks/{task}/screening-arbitrations",
                files=[("files", ("a.csv", csv_bytes, "text/csv")),
                       ("files", ("b.csv", csv_bytes, "text/csv"))],
                data={"stage": "stage1", "resolutions": "{}"},
                headers=self.auth)
        self.assertEqual(r.status_code, 503, r.text[:300])
        self.assertEqual(r.json()["detail"]["error_code"], "arbitration_session_store_unavailable")

    def test_server_save_locked_store_is_503(self):
        task, _ = self.new_task()
        with mock.patch.object(sa_mod, "load_screener_decision_packages", return_value={}), \
                mock.patch.object(sa_mod, "merge_decision_packages", return_value=object()), \
                mock.patch.object(sa_mod, "append_decision_arbitration",
                                   side_effect=sqlite3.OperationalError("database is locked")):
            r = self.client.post(
                f"/api/tasks/{task}/screening-arbitrations/server-save",
                json={"server_source": True, "stage": "stage1", "resolutions": []},
                headers=self.auth)
        self.assertEqual(r.status_code, 503, r.text[:300])
        self.assertEqual(r.json()["detail"]["error_code"], "arbitration_session_store_unavailable")


# ---------------------------------------------------------------------------
# L-42：FISHER_Z 标签由 meta 层原生产出（被删视图块确系 no-op）
# ---------------------------------------------------------------------------
class L42FisherZNativeLabelsTests(_ApiBase):

    def test_prior_grid_fisher_z_labels_without_view_rewrite(self):
        task, info = self.new_task()
        db = self.screener_db(info)
        db_mod.upsert_articles(db, [Article(zotero_key="R1", title="Report 1"),
                                    Article(zotero_key="R2", title="Report 2")], [])
        review_analysis.save_study(db, "S1", "Study 1", ["R1"])
        review_analysis.save_study(db, "S2", "Study 2", ["R2"])
        rid1 = review_analysis.save_effect(db, "S1", "cmp", "out", "tp", "FISHER_Z", 0.2, 0.05, "R1")
        rid2 = review_analysis.save_effect(db, "S2", "cmp", "out", "tp", "FISHER_Z", 0.4, 0.06, "R2")
        review_analysis.select_effect(db, rid1)
        review_analysis.select_effect(db, rid2)
        priors = [
            {"mu_prior_family": "normal", "mu_prior_mean": 0.0, "mu_prior_scale": 1.0,
             "mu_prior_df": None, "tau_prior_family": "half_normal",
             "tau_prior_scale": 0.5, "tau_prior_df": None},
            {"mu_prior_family": "normal", "mu_prior_mean": 0.2, "mu_prior_scale": 2.0,
             "mu_prior_df": None, "tau_prior_family": "half_normal",
             "tau_prior_scale": 0.3, "tau_prior_df": None},
        ]
        r = self.client.post(f"/api/tasks/{task}/analysis/bayesian/prior-grid",
                             json={"comparison": "cmp", "outcome": "out", "timepoint": "tp",
                                   "measure": "FISHER_Z", "priors": priors},
                             headers=self.auth)
        self.assertEqual(r.status_code, 200, r.text[:400])
        body = r.json()
        self.assertEqual(body["analysis_scale"], "fisher_z")
        self.assertEqual(body["standard_error_scale"], "fisher_z")
        for cell in body["cells"]:
            self.assertEqual(cell["mu"]["scale"], "fisher_z")
            self.assertEqual(cell["tau"]["scale"], "fisher_z_sd")
        # 概览由 _analysis_scale('FISHER_Z') 拼出，不含被删 replace 的目标串
        self.assertNotIn("natural", body["influence_overview"])
        self.assertIn("fisher_z", body["influence_overview"])


# ---------------------------------------------------------------------------
# L-43：dta_exploratory_lr 过滤 strip + 显式诊断（对齐 F3-94）
# ---------------------------------------------------------------------------
class L43ExploratoryLrFilterDiagnosticsTests(_ApiBase):

    def setUp(self):
        super().setUp()
        self.task, info = self.new_task()
        db = self.screener_db(info)
        db_mod.upsert_articles(db, [Article(zotero_key=f"R{i}", title=f"Report {i}")
                                    for i in range(1, 4)], [])
        for i, key in ((1, "R1"), (2, "R2")):
            review_analysis.save_study(db, f"S{i}", f"Study {i}", [key])
            dta_analysis.save_result(db, f"S{i}", "IT", "TC", "5 mm", "RS",
                                     10 + i, 2, 3, 9, key)
        # 组合空场景：另一检验下记录不同阈值
        review_analysis.save_study(db, "S3", "Study 3", ["R3"])
        dta_analysis.save_result(db, "S3", "IT2", "TC", "6 mm", "RS", 5, 1, 1, 5, "R3")
        self.url = f"/api/tasks/{self.task}/analysis/dta/exploratory-likelihood-ratios"

    def _post(self, **overrides):
        body = {"index_test": "IT", "target_condition": "TC", "threshold": "5 mm",
                "reference_standard": "RS", "model": "fixed", "correction": "none",
                "confidence": 0.95}
        body.update(overrides)
        return self.client.post(self.url, json=body, headers=self.auth)

    def test_mismatched_threshold_reports_candidates(self):
        """撤修复必红：修复前 '5mm' 静默零行 → 「at least two studies」掩盖。"""
        r = self._post(threshold="5mm")
        self.assertEqual(r.status_code, 400, r.text[:300])
        self.assertIn("'5 mm'", r.json()["detail"])
        self.assertNotIn("at least two studies", r.json()["detail"])

    def test_unstripped_request_value_still_matches(self):
        """strip 对齐：入库值已剥离，带首尾空白的请求值照常命中。"""
        r = self._post(threshold=" 5 mm ", index_test=" IT ")
        self.assertEqual(r.status_code, 200, r.text[:300])
        self.assertIn("lr_positive", r.json())

    def test_empty_combination_reports_true_cause(self):
        """四值各自在库、组合命中零行：显式报组合无结果，而非研究数不足。"""
        r = self._post(threshold="6 mm")  # '6 mm' 记录在 IT2 下，(IT,·,'6 mm',·) 无行
        self.assertEqual(r.status_code, 400, r.text[:300])
        self.assertIn("no diagnostic accuracy results for this combination",
                      r.json()["detail"])
        self.assertNotIn("at least two studies", r.json()["detail"])


# ---------------------------------------------------------------------------
# L-45：别名查找键真值回退链（actor_id 为 null 不再产出幻影键）
# ---------------------------------------------------------------------------
class L45NullActorAliasFallbackTests(_ApiBase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # 单事件包：actor_id 键存在但为 null（_validate_event 放行），alias 兜底。
        # 走真实 build_export_zip / validate_import_package 两条产品路径。
        event = {"event_id": str(uuid.uuid4()), "store_id": "st-1", "sequence": 1,
                 "task_id": "T1", "actor_id": None, "actor_alias": "Reviewer B",
                 "stage": "coding", "action": "coding_value_changed", "subject_id": "S0",
                 "before": None, "after": {"field": "f0", "value": "v"},
                 "source": "manual", "recorded_at": "2026-01-01T00:00:00Z"}
        snapshot = {"stores": [{"partition": "personal", "store_id": "st-1",
                                "settings": {"status": "recording", "stages": ["coding"]},
                                "summary": {}, "events": [event]}]}
        cls.package = exporter.build_export_zip(snapshot, public=False)
        cls.parsed = exporter.validate_import_package(cls.package)

    def test_origin_actors_use_truthy_chain(self):
        self.assertEqual(self.parsed["origin_actors"], ["Reviewer B"])

    def test_commit_with_null_actor_id_is_200_not_500(self):
        """撤修复必红：修复前 str(None) 产出幻影键 'None' → aliases['None']
        KeyError → 500。"""
        task, _ = self.new_task()
        r = self.client.post(
            f"/api/tasks/{task}/trace/imports/commit",
            files={"package": ("trace.zip", self.package, "application/zip")},
            data={"preview_sha256": self.parsed["package_sha256"],
                  "actor_aliases": json.dumps({"Reviewer B": "RB"}),
                  "subject_mappings": "{}", "confirm_unmapped": "true"},
            headers=self.auth)
        self.assertEqual(r.status_code, 200, r.text[:300])
        self.assertEqual(r.json()["status"], "committed")
        self.assertEqual(r.json()["imported"], 1)


# ---------------------------------------------------------------------------
# L-47：prisma 指标读成员库的锁忙/IO→503、永久损坏→跳过
# ---------------------------------------------------------------------------
class L47PrismaMemberLibraryReadTests(_ApiBase):

    def test_missing_file_returns_empty(self):
        absent = Path(self.enterContext(tempfile.TemporaryDirectory())) / "absent.db"
        self.assertEqual(ptm.decisions(absent, "decisions"), {})

    def test_valid_library_reads_votes(self):
        """真实筛选员库（产品路径建库+保存决策）照常读到票。"""
        _, info = self.new_task()
        db = self.screener_db(info)
        db_mod.upsert_articles(db, [Article(zotero_key="K1", title="Report 1")], [])
        db_mod.save_decision(db, "K1", "include")
        votes = ptm.decisions(db, "decisions")
        self.assertEqual(set(votes), {"K1"})
        self.assertEqual(votes["K1"]["decision"], "include")

    def test_corrupt_library_skips_member(self):
        """永久损坏（无法解析的库）→ 跳过该成员（返回空），不 500。"""
        path = Path(self.enterContext(tempfile.TemporaryDirectory())) / "corrupt.db"
        path.write_bytes(b"this is not a sqlite database at all" * 8)
        self.assertEqual(ptm.decisions(path, "decisions"), {})

    def test_locked_library_is_503(self):
        """锁忙超时 OperationalError → 503（不再让 GET /prisma-metrics 整页
        500）。在首个读语句（has_table 的 sqlite_master 查询）处以同型异常
        模拟 15s busy_timeout 到期的库锁。"""
        member = Path(self.enterContext(tempfile.TemporaryDirectory())) / "member.db"
        sqlite3.connect(member).close()  # 真实可打开的库文件
        with mock.patch.object(ptm.workflow, "has_table",
                               side_effect=sqlite3.OperationalError("database is locked")):
            with self.assertRaises(HTTPException) as ctx:
                ptm.decisions(member, "decisions")
        self.assertEqual(ctx.exception.status_code, 503)

    def test_io_failure_is_503(self):
        member = Path(self.enterContext(tempfile.TemporaryDirectory())) / "member.db"
        sqlite3.connect(member).close()
        with mock.patch.object(ptm.workflow, "connect",
                               side_effect=OSError("disk I/O error")):
            with self.assertRaises(HTTPException) as ctx:
                ptm.decisions(member, "decisions")
        self.assertEqual(ctx.exception.status_code, 503)


# ---------------------------------------------------------------------------
# L-48：个人撤销对 previous 快照的专用 409
# ---------------------------------------------------------------------------
class L48PersonalUndoStaleReportTests(_ApiBase):

    def setUp(self):
        super().setUp()
        self.task, _ = self.new_task()
        ctx = app.state.review_workflow.context(self.task, self.account)
        db_mod.upsert_articles(
            ctx.owner_db,
            [Article(zotero_key="R1", title="First", doi="10.test/1"),
             Article(zotero_key="R2", title="Second", doi="10.test/2")], [])
        self.owner_db = ctx.owner_db
        self.base = f"/api/tasks/{self.task}/report-evidence"

    def _state(self):
        return json.loads(json.dumps(DEFAULT))

    def _link(self, state):
        state["links"] = [{"id": "L1", "a": "R1", "b": "R2", "type": "study", "same": True,
                           "evidence": "Report explicitly states shared interviews",
                           "locator": "p.4", "status": "mine"}]
        return state

    def _save(self, state, revision):
        return self.client.put(self.base, json={
            "expected_revision": revision, "operation_id": str(uuid.uuid4()),
            "state": state, "label": "Save evidence", "stage": "coding"},
            headers=self.auth)

    def _two_saves(self):
        """save#1 带链接（previous=DEFAULT）；save#2 清空（previous=带链接快照）。"""
        r = self._save(self._link(self._state()), 0)
        self.assertEqual(r.status_code, 200, r.text[:300])
        r = self._save(self._state(), 1)
        self.assertEqual(r.status_code, 200, r.text[:300])

    def _retire_r2(self):
        """把 R2 并入 R1 的重复组（参数绑定），使其退出现役报告键集——
        与标题查重判定 duplicate 的落库终态同源（decide_title_duplicate_review
        的 UPDATE 路径，见 db.py:1774）。"""
        with closing(sqlite3.connect(self.owner_db)) as conn:
            conn.execute("UPDATE articles SET is_duplicate_of=? WHERE zotero_key=?",
                         ("R1", "R2"))
            conn.commit()

    def test_undo_stale_report_is_dedicated_409(self):
        """撤修复必红：修复前快照链接引用已下线报告 → validate 通用 422
        「Choose two different active reports...」且此后撤销永久不可用。"""
        self._two_saves()
        self._retire_r2()
        r = self.client.post(self.base + "/undo", json={"expected_revision": 2},
                             headers=self.auth)
        self.assertEqual(r.status_code, 409, r.text[:300])
        self.assertIn("no longer active", r.json()["detail"])
        self.assertNotIn("Choose two different", r.json()["detail"])

    def test_undo_healthy_snapshot_still_restores(self):
        """对照：报告仍现役时撤销照常 200（既有语义不变）。"""
        self._two_saves()
        r = self.client.post(self.base + "/undo", json={"expected_revision": 2},
                             headers=self.auth)
        self.assertEqual(r.status_code, 200, r.text[:300])
        self.assertEqual(len(r.json()["state"]["links"]), 1)


if __name__ == "__main__":
    unittest.main()
