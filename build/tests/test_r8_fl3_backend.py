"""r8 FL3 后端组回归钉子（专属新文件，撤修复必红）。第 2 轮追加 5 条。

第 2 轮评审后追加：R3-02 延迟上界（不可写介质零等待）＋真竞争仍收敛；
MM10-19 的 R2 边角（sqlite3.Row 行对象不得被拒）＋协变量值非映射 → ValueError；
MM11-08 的 ProgrammingError 必须继续抛出。

R8 收尾评审残留（负缓存不可失效 + docstring 与实现相反）追加
``R3_02_NegativeCacheInvalidation`` 一类 7 条：写入成功 / 读到持久钥 / 文件
指纹改变（先坏后好再坏）时清除 ``_replace_blocked_until``，冷却期内仍做一次
零等待重读接住并发创建者，并钉住 docstring 的失效契约。

逐条对应 inventory_缓修池72条.md 的 FL3 条目；每条钉子在**基线（a080fde）
上实测为红**（撤掉对应修复即回到该红态），修复后为绿：

- MM9-05（pool_compatibility.py）：拒绝明细只按存量口径回报，真实冲突
  （现值口径 approx-J vs exact-gamma）不可见；修复后 methods 条目对被分类
  器升级的存量组加注 ``current_classifier_views``，判定门控/reason 集合不变。
- MM9-16（dta 三视图）：except 缺 TypeError/AttributeError、prediction 视图
  边界门控在 try 外；修复后边界形态一律降级 ``available=False``。
- MM9-26（db.init_db）：迁移失败裸抛 → 每个筛选端点 500 重放；修复后
  记 WARNING 日志并降级跳过（库保持未迁移口径可用），建表失败仍抛。
- MM10-14（living_review_plugin.py）：模块 docstring 承诺 check-now 延迟
  导入并调用本模块，与实现（固定 409 protected_import_required）相反；
  修复后 docstring 如实描述「当前未接线、W3 复活」，模块不删。
- MM10-18*（heterogeneity_view.py）：except 漏 KeyError/TypeError（基线
  KeyError/TypeError 逃逸已独立复现）；修复后降级 ``available=False``。
- MM10-19*（robust_regression_view.py）：外部字典无守卫下标（基线
  KeyError/TypeError 逃逸已独立复现）；修复后缺字段抛带说明的 ValueError
  （端点 4xx），合法输入（键齐全）行为不变。
- MM11-08（screening_arbitration.py）：只捕 OperationalError，局部页损坏
  冒泡 500；修复后捕父类 DatabaseError 降级 None（= 无已保存仲裁）。
- B08-N3（research_trace.clear_history）：清空后不重建全局文献/协议基线；
  修复后仍 recording/paused 时按录制启动同款重建。
- B08-N6（research_trace_export.py）：actor_id:null 产出幻影演员 'None'；
  修复后真值回退到 alias。
- R3-06（research_trace.update_settings）：基线事件不受阶段选择门控——
  按复审判定以**文档化契约**收口（导入预览依赖 baseline_item 的 DOI），
  钉子钉住该契约与文档自述，防未来被误门控。
- D-05-01（export.py）：编码备注导出以 5 实参调 4 形参函数（必 TypeError）；
  修复后按实际签名调用并批量取回图片索引。
- D-05-02（db.record_ranking）：SELECT MAX 与批量 INSERT 无跨进程写锁，
  并发重排共用同一 created_at（latest_ranking 混批）；修复后读-判-写包在
  BEGIN IMMEDIATE 内，注入的竞争批被写锁挡下。
- L-09-01（task_collaboration.py）：revoke_role/list_role_grants 缺 task_id
  必传守卫；修复后与 grant_role 同款 ValueError。
- R3-02（auth.py）：并发首初始化时一个 worker 返回的密钥 ≠ 持久密钥；
  修复后首建者写后重读持久值、替换失败方有界等待重读（双进程实测收敛）。
"""
import contextlib
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from contextlib import closing
from collections.abc import Mapping
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from coscreen import db as db_mod
from coscreen import export as export_mod
from coscreen import coding as coding_mod
from coscreen import research_trace as research_trace_mod
from coscreen import research_trace_export as exporter
from coscreen import screening_arbitration as arbitration_mod
from coscreen import task_collaboration as collaboration_mod
from coscreen.models import Article
from coscreen.pool_compatibility import PoolCompatibilityError, require_compatible_effects

REPO_ROOT = Path(__file__).resolve().parents[2]


def _tmp_db(prefix: str) -> Path:
    directory = tempfile.mkdtemp(prefix=prefix)
    return Path(directory) / "task.db"


#: 本机环境警示：C 盘曾满，探针/临时产物一律落 E: 盘。新钉子统一走此根；
#: 该盘不可用时才退回系统临时目录（老用例沿用 ``tempfile`` 不动）。
_TMP_ROOT = Path("E:/rf214tmp/fl3")


@contextlib.contextmanager
def _e_tmp_dir(prefix: str):
    """E: 盘临时目录（存在则用，不可用退回 ``TemporaryDirectory``）。"""
    try:
        _TMP_ROOT.mkdir(parents=True, exist_ok=True)
        directory = Path(tempfile.mkdtemp(prefix=prefix, dir=str(_TMP_ROOT)))
    except OSError:  # E: 不可用（CI 等）——退回系统临时目录
        with tempfile.TemporaryDirectory(prefix=prefix) as fallback:
            yield fallback
        return
    try:
        yield str(directory)
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def _import_auth(testcase, data_dir: str):
    """取得一份**干净**的 custom_backend.auth 状态，并在用例结束时完整还原。

    auth 的应急钥/告警标志是模块级全局，DATA_DIR 走进程环境变量；直接改动
    会污染同进程的其他测试文件（实测会把 Mm03EmptyJwtSecret 的两个用例带红）。
    故此处登记 addCleanup：还原 env 与三个模块全局。
    """
    from custom_backend import auth as auth_module
    previous_env = os.environ.get("COBOOKSHELF_DATA_DIR")
    previous_state = (auth_module._EMERGENCY_SECRET,
                      auth_module._EMERGENCY_WRITE_LOGGED)
    # R8 收尾：负缓存按路径记账，跨用例残留会让后续用例命中别的数据目录的
    # 冷却（路径唯一，实际不会误命中，但快照/还原更干净且能断言其清空）。
    previous_cache = dict(auth_module._replace_blocked_until)

    def restore():
        if previous_env is None:
            os.environ.pop("COBOOKSHELF_DATA_DIR", None)
        else:
            os.environ["COBOOKSHELF_DATA_DIR"] = previous_env
        (auth_module._EMERGENCY_SECRET,
         auth_module._EMERGENCY_WRITE_LOGGED) = previous_state
        auth_module._replace_blocked_until.clear()
        auth_module._replace_blocked_until.update(previous_cache)

    testcase.addCleanup(restore)
    os.environ["COBOOKSHELF_DATA_DIR"] = data_dir
    auth_module._EMERGENCY_SECRET = None
    auth_module._EMERGENCY_WRITE_LOGGED = False
    auth_module._replace_blocked_until.clear()
    return auth_module


def _smd_row(study_id: str, estimator_metadata: dict | None, entry_method: str) -> dict:
    return {
        "study_id": study_id,
        "measure": "SMD",
        "estimate": 0.2,
        "se": 0.1,
        "entry_method": entry_method,
        "input_data": ({"estimator_metadata": estimator_metadata}
                       if estimator_metadata is not None else {}),
    }


_LEGACY_UNCLASSIFIED = {
    "estimator": "unclassified_smd", "standardizer": None,
    "small_sample_correction": None, "variance_method": "legacy_unknown",
}
_EXACT_GAMMA = {
    "estimator": "hedges_g", "standardizer": "pooled_within_group_sd",
    "small_sample_correction": "exact_gamma_ratio", "variance_method": "metafor_vtype_LS2",
}
_APPROX_J = {
    "estimator": "hedges_g", "standardizer": "pooled_within_group_sd",
    "small_sample_correction": "approximate_1_minus_3_over_4df_minus_1",
    "variance_method": "LS2_formula_with_approximate_J",
}


class MM9_05_RejectionDetailShowsCurrentClassifierView(unittest.TestCase):
    """MM9-05：拒绝明细的真实冲突可见（存量原样 + 现值加注）。"""

    def test_rejected_pool_annotates_legacy_upgrade(self):
        rows = [
            _smd_row("S1", _LEGACY_UNCLASSIFIED, "extraction:rct_continuous"),
            _smd_row("S3", _EXACT_GAMMA, "arm_level"),
        ]
        with self.assertRaises(PoolCompatibilityError) as caught:
            require_compatible_effects(rows)
        conflict = next(reason for reason in caught.exception.reasons
                        if reason["type"] == "estimator_conflict")
        methods = {(method["estimator"], method["small_sample_correction"]): method
                   for method in conflict["methods"]}
        legacy = methods[("unclassified_smd", None)]
        # 基线：存量条目原样保留（unclassified/null/null/legacy_unknown）。
        self.assertEqual(legacy["studies"], ["S1"])
        self.assertIsNone(legacy["standardizer"])
        # 修复：S1 的现值视图（真实冲突方 approx-J）必须在明细中可见。
        views = legacy.get("current_classifier_views")
        self.assertIsNotNone(views)
        self.assertEqual(views[0]["small_sample_correction"],
                         "approximate_1_minus_3_over_4df_minus_1")
        self.assertEqual(views[0]["studies"], ["S1"])
        # 未升级的组不得带注记。
        self.assertNotIn("current_classifier_views", methods[("hedges_g", "exact_gamma_ratio")])
        # detail 文案（基线 reason 集）不变。
        self.assertIn("undocumented estimators", conflict["detail"])

    def test_decision_gate_unchanged_compatible_pool_still_accepted(self):
        # R2 钉子：现值口径同签名（S1 升级为 approx-J + 显式 approx-J 行）
        # 仍可同池——加注只改明细，不改判定。
        rows = [
            _smd_row("S1", _LEGACY_UNCLASSIFIED, "extraction:rct_continuous"),
            _smd_row("S3", _APPROX_J, "arm_level"),
        ]
        require_compatible_effects(rows)  # 不抛即通过


class MM9_16_DtaViewDegradation(unittest.TestCase):
    """MM9-16：三个 DTA 视图对边界 fit 降级，不逃逸异常。"""

    def test_prediction_view_gate_inside_try(self):
        from custom_backend.dta_prediction_view import prediction_region_view
        # 基线：门控在 try 外，warnings 非 str → AttributeError 逃逸。
        result = prediction_region_view({"warnings": [1, 2]})
        self.assertFalse(result["available"])
        self.assertIn("reason", result)
        # 基线：fit 非 dict → AttributeError 逃逸。
        result = prediction_region_view(["not", "a", "dict"])
        self.assertFalse(result["available"])

    def test_likelihood_and_hsroc_views_catch_type_errors(self):
        from custom_backend.dta_likelihood_view import likelihood_ratio_view
        from custom_backend.dta_views import hsroc_view
        for view in (likelihood_ratio_view, hsroc_view):
            with self.subTest(view=view.__name__):
                # 基线：fit 非 dict → fit.get 触发 AttributeError 逃逸。
                result = view(None)
                self.assertFalse(result["available"])
                self.assertIn("reason", result)
                result = view(42)
                self.assertFalse(result["available"])

    def test_valid_fit_still_degrades_only_on_import_boundary(self):
        # 控制组：缺键的 dict 此前就已被 KeyError 分支降级（非本条修复面）。
        from custom_backend.dta_views import hsroc_view
        result = hsroc_view({"warnings": []})
        self.assertFalse(result["available"])
        self.assertIn("HSROC presentation unavailable", result["reason"])


class MM10_18_HeterogeneityViewExceptTuple(unittest.TestCase):
    """MM10-18*：heterogeneity_intervals 的 except 覆盖 KeyError/TypeError。"""

    def test_missing_key_degrades_instead_of_keyerror(self):
        from custom_backend.heterogeneity_view import heterogeneity_intervals
        result = heterogeneity_intervals({"n_studies": 5})  # 基线：KeyError 'q' 逃逸
        self.assertFalse(result["available"])
        self.assertIn("reason", result)
        result = heterogeneity_intervals({"q": 1.5})  # 基线：KeyError 'n_studies' 逃逸
        self.assertFalse(result["available"])

    def test_non_mapping_degrades_instead_of_typeerror(self):
        from custom_backend.heterogeneity_view import heterogeneity_intervals
        for payload in (None, [1, 2, 3], 42):
            with self.subTest(payload=payload):
                result = heterogeneity_intervals(payload)  # 基线：TypeError 逃逸
                self.assertFalse(result["available"])

    def test_valid_result_still_available(self):
        from custom_backend.heterogeneity_view import heterogeneity_intervals
        result = heterogeneity_intervals({"q": 8.0, "n_studies": 5})
        self.assertTrue(result["available"])


class MM10_19_RobustRegressionViewGuards(unittest.TestCase):
    """MM10-19*：fit_robust_view 缺字段抛 ValueError（→ 端点 400）而非 KeyError。"""

    @staticmethod
    def _rows(n: int = 2) -> list[dict]:
        return [{"study_id": f"S{i}", "estimate": 0.2 + 0.01 * i, "se": 0.1,
                 "source_key": f"k{i}", "source_locator": f"l{i}", "result_id": i}
                for i in range(1, n + 1)]

    def _call(self, rows, covariates, spec, references):
        from custom_backend.robust_regression_view import fit_robust_view
        return fit_robust_view(rows, covariates, spec, references, [],
                               measure="SMD", model="reml", vcov_type="CR1", level=0.95)

    def test_missing_reference_level_raises_value_error(self):
        rows, cov = self._rows(), {"S1": {"d1": 1.0}, "S2": {"d1": 2.0}}
        with self.assertRaises(ValueError) as caught:  # 基线：KeyError 'd1' 逃逸
            self._call(rows, cov, {"d1": "categorical"}, {})
        self.assertIn("reference", str(caught.exception))

    def test_missing_covariate_study_raises_value_error(self):
        with self.assertRaises(ValueError) as caught:  # 基线：KeyError 'S2' 逃逸
            self._call(self._rows(), {"S1": {"d1": 1.0}}, {"d1": "continuous"}, {})
        self.assertIn("S2", str(caught.exception))

    def test_missing_row_fields_raise_value_error(self):
        cov = {row["study_id"]: {"d1": 1.0} for row in self._rows()}
        for key in ("study_id", "estimate", "se"):
            with self.subTest(key=key):
                rows = [{k: v for k, v in row.items() if k != key} for row in self._rows()]
                with self.assertRaises(ValueError) as caught:  # 基线：KeyError 逃逸
                    self._call(rows, cov, {"d1": "continuous"}, {})
                self.assertIn(key, str(caught.exception))
        rows = [["S1", 0.2, 0.1]] + self._rows()[1:]
        with self.assertRaises(ValueError):  # 基线：TypeError 逃逸
            self._call(rows, cov, {"d1": "continuous"}, {})

    def test_valid_input_unchanged(self):
        # R2 钉子：键齐全的合法输入修复前后行为一致（成功拟合）。
        rows = self._rows(12)
        cov = {row["study_id"]: {"d1": float(i)} for i, row in enumerate(rows, start=1)}
        result = self._call(rows, cov, {"d1": "continuous"}, {})
        self.assertEqual(len(result["sources"]), 12)
        self.assertEqual(result["sources"][0]["study_id"], "S1")

    def test_sqlite_row_mapping_rows_still_accepted(self):
        """R2：sqlite3.Row 等 DB-API 行对象（非 dict、非 Mapping 子类）基线可
        用——守卫不得收窄到 dict 而拒绝它们。"""
        real = sqlite3.connect(":memory:")
        real.row_factory = sqlite3.Row
        real.execute("CREATE TABLE effects(study_id TEXT, estimate REAL, se REAL,"
                     " source_key TEXT, source_locator TEXT, result_id INT)")
        for i in range(1, 13):
            real.execute("INSERT INTO effects VALUES(?,?,?,?,?,?)",
                         (f"S{i}", 0.2 + 0.01 * i, 0.1, f"k{i}", f"l{i}", i))
        rows = real.execute("SELECT * FROM effects ORDER BY result_id").fetchall()
        self.assertNotIsInstance(rows[0], dict)
        self.assertNotIsInstance(rows[0], Mapping)
        cov = {f"S{i}": {"d1": float(i)} for i in range(1, 13)}
        try:
            result = self._call(rows, cov, {"d1": "continuous"}, {})
        finally:
            real.close()
        self.assertEqual(len(result["sources"]), 12)

    def test_covariate_value_not_a_mapping_is_value_error(self):
        """族 H 收口：值存在但非键值容器（None）→ ValueError（端点 400），
        而非基线在 ** 展开处的 TypeError → 500。"""
        rows = self._rows()
        with self.assertRaises(ValueError) as caught:
            self._call(rows, {"S1": {"d1": 1.0}, "S2": None}, {"d1": "continuous"}, {})
        self.assertIn("S2", str(caught.exception))


class MM11_08_ArbitrationReadCorruption(unittest.TestCase):
    """MM11-08：仲裁表局部页损坏降级为 None（= 无已保存仲裁），不再冒泡。"""

    def _build_store(self, path: Path) -> None:
        with closing(sqlite3.connect(path)) as conn:
            conn.executescript(arbitration_mod._ARBITRATION_SCHEMA)
            conn.execute(
                "INSERT INTO screening_arbitrations"
                "(stage,merge_sha256,result_sha256,supersedes_id,arbitrator_id,records_json,created_at) "
                "VALUES('stage1',?,?,?,?, '[]', '2026-01-01T00:00:00Z')",
                ("a" * 64, "b" * 64, None, "u1"),
            )
            conn.commit()
            self.root_page = int(conn.execute(
                "SELECT rootpage FROM sqlite_master WHERE name='screening_arbitrations'"
            ).fetchone()[0])
            self.page_size = int(conn.execute("PRAGMA page_size").fetchone()[0])

    def test_corrupt_table_page_reads_as_no_record(self):
        path = _tmp_db("rf_r8_mm1108_")  # 非任务库：任意路径皆可
        self._build_store(path)
        # 控制组：完好库两条读路径都命中记录。
        self.assertIsNotNone(arbitration_mod.get_decision_arbitration(path, 1))
        self.assertIsNotNone(arbitration_mod.latest_decision_arbitration(path, "stage1"))
        with open(path, "r+b") as fh:  # 只损坏该表的 b-tree 根页（属主/库头仍完好）
            fh.seek((self.root_page - 1) * self.page_size)
            fh.write(b"\x7f" + b"\x00" * 15)
        # 基线：DatabaseError（非 OperationalError）冒泡；修复：降级 None。
        self.assertIsNone(arbitration_mod.get_decision_arbitration(path, 1))
        self.assertIsNone(arbitration_mod.latest_decision_arbitration(path, "stage1"))

    def test_missing_file_still_none(self):
        self.assertIsNone(
            arbitration_mod.get_decision_arbitration("no/such/store.db", 1))

    def test_programming_error_is_not_swallowed(self):
        """第 2 轮评审：捕父类 DatabaseError 会把调用写法错一并吞掉；参数绑定
        失败（ProgrammingError）必须继续抛出，不得冒充「无已保存仲裁」。"""
        path = _tmp_db("rf_r8_mm1108_prog_")
        self._build_store(path)
        with self.assertRaises(sqlite3.ProgrammingError):
            arbitration_mod.get_decision_arbitration(path, [9999])


class B08_N3_ClearHistoryRebuildsGlobalBaseline(unittest.TestCase):
    """B08-N3：clear_history 后重建全局文献/协议基线（导入预览依赖其 DOI）。"""

    def _recorded_store(self, path: Path):
        db_mod.init_db(path)
        db_mod.upsert_articles(
            path, [Article(zotero_key="K1", title="Study One", doi="10.1/x")], [])
        context = research_trace_mod.TraceContext("T1", 1)
        with closing(db_mod._connect(path)) as conn, conn:
            research_trace_mod.update_settings(
                conn, context, stages=["fulltext"], status="recording",
                operation_id="op-start", expected_version=0)
        return context

    @staticmethod
    def _event_counts(path: Path) -> dict[tuple[str, str], int]:
        with closing(db_mod._connect(path)) as conn:
            return {(row[0], row[1]): int(row[2]) for row in conn.execute(
                "SELECT stage,action,COUNT(*) FROM trace_events GROUP BY stage,action")}

    def test_clear_history_keeps_recording_rebuilds_item_and_protocol_baseline(self):
        path = _tmp_db("rf_r8_b08n3_")
        context = self._recorded_store(path)
        before = self._event_counts(path)
        self.assertEqual(before[("screen", "baseline_item")], 1)
        self.assertEqual(before[("screen", "baseline_protocol")], 1)
        with closing(db_mod._connect(path)) as conn, conn:
            result = research_trace_mod.clear_history(
                conn, context, operation_id="op-clear", expected_version=1)
        self.assertGreaterEqual(result["deleted_event_count"], 3)
        after = self._event_counts(path)
        # 基线：clear 后二者为 0（真实缺陷）；修复：按录制启动同款重建。
        self.assertEqual(after.get(("screen", "baseline_item")), 1)
        self.assertEqual(after.get(("screen", "baseline_protocol")), 1)
        # DOI 口径原样保留（导入预览消费方）。
        with closing(db_mod._connect(path)) as conn:
            row = conn.execute(
                "SELECT after_json FROM trace_events WHERE action='baseline_item' "
                "AND stage='screen'").fetchone()
        self.assertIn("10.1/x", row[0])

    def test_clear_history_while_off_rebuilds_nothing(self):
        # 控制组：off 状态清空不重建（与「未在录制」语义一致）。
        path = _tmp_db("rf_r8_b08n3_off_")
        context = self._recorded_store(path)
        with closing(db_mod._connect(path)) as conn, conn:
            research_trace_mod.update_settings(
                conn, context, stages=["fulltext"], status="off",
                operation_id="op-stop", expected_version=1)
            research_trace_mod.clear_history(
                conn, context, operation_id="op-clear", expected_version=2)
        after = self._event_counts(path)
        self.assertNotIn(("screen", "baseline_item"), after)
        self.assertNotIn(("screen", "baseline_protocol"), after)


class R3_06_GlobalBaselineContract(unittest.TestCase):
    """R3-06：基线事件不受阶段选择门控——按复审判定收口为文档化契约。"""

    def test_baseline_written_even_when_screen_unselected(self):
        # 契约钉子：只录 fulltext 仍写 stage='screen' 的全局基线（绿钉，
        # 防未来被误门控而破坏 research_trace_api._import_metadata 的 DOI 匹配）。
        path = _tmp_db("rf_r8_r306_")
        db_mod.init_db(path)
        db_mod.upsert_articles(
            path, [Article(zotero_key="K1", title="Study One", doi="10.1/x")], [])
        context = research_trace_mod.TraceContext("T1", 1)
        with closing(db_mod._connect(path)) as conn, conn:
            research_trace_mod.update_settings(
                conn, context, stages=["fulltext"], status="recording",
                operation_id="op-start", expected_version=0)
        with closing(db_mod._connect(path)) as conn:
            rows = conn.execute(
                "SELECT stage,action FROM trace_events "
                "WHERE action IN ('baseline_item','baseline_protocol')").fetchall()
        self.assertIn(("screen", "baseline_item"), [tuple(row) for row in rows])
        self.assertIn(("screen", "baseline_protocol"), [tuple(row) for row in rows])

    def test_contract_documented_on_helper(self):
        # 文档钉子：契约（全局、不受阶段选择门控、stage 标签仅归类）必须写明。
        doc = research_trace_mod._snapshot_articles_and_protocol.__doc__
        self.assertIn("全局", doc)
        self.assertIn("不受 settings.stages", doc)


class B08_N6_OriginActorsNullFallback(unittest.TestCase):
    """B08-N6：actor_id:null 回退 alias，不再产出幻影演员 'None'。"""

    def test_null_actor_id_falls_back_to_alias(self):
        event = {"event_id": str(uuid.uuid4()), "store_id": "st-1", "sequence": 1,
                 "task_id": "T1", "actor_id": None, "actor_alias": "X",
                 "stage": "coding", "action": "coding_value_changed",
                 "subject_id": "S0", "before": None,
                 "after": {"field": "f0", "value": "v"},
                 "source": "manual", "recorded_at": "2026-01-01T00:00:00Z"}
        snapshot = {"stores": [{"partition": "personal", "store_id": "st-1",
                                "settings": {"status": "recording", "stages": ["coding"]},
                                "summary": {}, "events": [event]}]}
        parsed = exporter.validate_import_package(
            exporter.build_export_zip(snapshot, public=False))
        # 基线：['None']；修复：['X']。
        self.assertEqual(parsed["origin_actors"], ["X"])

    def test_plain_actor_id_unchanged(self):
        event = {"event_id": str(uuid.uuid4()), "store_id": "st-1", "sequence": 1,
                 "task_id": "T1", "actor_id": "42", "stage": "coding",
                 "action": "coding_value_changed", "subject_id": "S0",
                 "before": None, "after": {"field": "f0", "value": "v"},
                 "source": "manual", "recorded_at": "2026-01-01T00:00:00Z"}
        snapshot = {"stores": [{"partition": "personal", "store_id": "st-1",
                                "settings": {"status": "recording", "stages": ["coding"]},
                                "summary": {}, "events": [event]}]}
        parsed = exporter.validate_import_package(
            exporter.build_export_zip(snapshot, public=False))
        self.assertEqual(parsed["origin_actors"], ["42"])


class L_09_01_TaskIdRequiredGuards(unittest.TestCase):
    """L-09-01：revoke_role/list_role_grants 补 task_id 必传守卫。"""

    def setUp(self):
        self.directory = tempfile.mkdtemp(prefix="rf_r8_l0901_")
        self.store = collaboration_mod.shared_store_path(self.directory)
        collaboration_mod.initialize_task_store(
            self.directory, task_id="T1", owner_user_id=1, owner_username="owner")
        collaboration_mod.grant_role(
            self.store, actor_user_id=1, user_id=2, role="coder", task_id="T1")

    def test_list_role_grants_requires_task_id(self):
        with self.assertRaisesRegex(ValueError, "task_id is required"):
            # 基线：不抛，返回属主视角的角色列表（属主绑定校验被跳过）。
            collaboration_mod.list_role_grants(self.store, actor_user_id=1)

    def test_revoke_role_requires_task_id(self):
        with self.assertRaisesRegex(ValueError, "task_id is required"):
            # 基线：不抛，返回 True（属主绑定校验被跳过）。
            collaboration_mod.revoke_role(
                self.store, actor_user_id=1, user_id=2, role="coder")

    def test_with_task_id_behaviour_unchanged(self):
        grants = collaboration_mod.list_role_grants(
            self.store, actor_user_id=1, task_id="T1")
        self.assertEqual([grant["user_id"] for grant in grants], [2])
        self.assertTrue(collaboration_mod.revoke_role(
            self.store, actor_user_id=1, user_id=2, role="coder", task_id="T1"))
        self.assertEqual(
            collaboration_mod.list_role_grants(
                self.store, actor_user_id=1, task_id="T1"), [])


class D_05_01_CodingNotesMarkdownExport(unittest.TestCase):
    """D-05-01：编码备注导出按 _coding_notes_and_images 实际签名调用。"""

    def test_export_notes_writes_markdown(self):
        path = _tmp_db("rf_r8_d0501_")
        db_mod.init_db(path)
        db_mod.upsert_articles(
            path, [Article(zotero_key="K1", title="Study One")], [])
        dim = coding_mod.add_dimension(path, "质量评价", "text", [])
        coding_mod.save_coding_note(path, dim, "K1", "关键备注内容 R8")
        out = path.parent / "notes.md"
        # 基线：TypeError: takes 4 positional arguments but 5 were given。
        result = export_mod.export_coding_notes_markdown(path, out)
        self.assertEqual(result, out)
        text = out.read_text(encoding="utf-8")
        self.assertIn("关键备注内容 R8", text)
        self.assertIn("Study One", text)

    def test_export_notes_without_notes_writes_empty_skeleton(self):
        path = _tmp_db("rf_r8_d0501_empty_")
        db_mod.init_db(path)
        db_mod.upsert_articles(
            path, [Article(zotero_key="K1", title="Study One")], [])
        coding_mod.add_dimension(path, "质量评价", "text", [])
        out = path.parent / "notes-empty.md"
        result = export_mod.export_coding_notes_markdown(path, out)
        self.assertEqual(result, out)
        self.assertIn("文献数：0", out.read_text(encoding="utf-8"))


class D_05_02_RecordRankingWriteTransaction(unittest.TestCase):
    """D-05-02：SELECT MAX 与批量 INSERT 须在同一写事务内（并发批次不混）。"""

    class _InterleavingConnection:
        """代理连接：在 SELECT MAX 返回前注入一个「并发进程」的竞争批次。

        修复后主连接在 SELECT 前已持 BEGIN IMMEDIATE 写锁，注入连接拿不到
        写锁（OperationalError → 跳过）——这正是要钉住的串行化语义。
        """

        def __init__(self, real, path, inject):
            self._real, self._path, self._inject = real, path, inject

        def execute(self, sql, *args):
            if sql.strip().startswith("SELECT MAX(created_at)"):
                row = self._real.execute(sql, *args)
                self._inject(self._path)
                return row
            return self._real.execute(sql, *args)

        def __enter__(self):
            self._real.__enter__()
            return self

        def __exit__(self, *exc_info):
            return self._real.__exit__(*exc_info)

        def __getattr__(self, name):
            return getattr(self._real, name)

    @staticmethod
    def _inject_competing_batch(path) -> None:
        try:
            with closing(sqlite3.connect(path, timeout=0.5)) as conn:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    db_mod._INSERT_RANKING,
                    ("2026-01-01T00:00:00", "injected", 0, 0, "{}", "C1", 0, 0.0))
                conn.commit()
        except sqlite3.OperationalError:
            pass  # 写锁被修复后的主事务持有：无竞争批，即修复语义。

    def test_concurrent_writer_cannot_share_created_at(self):
        from coscreen.al.ranker import RankingResult

        path = _tmp_db("rf_r8_d0502_")
        real_connect = db_mod._connect
        inject = self._inject_competing_batch
        with mock.patch.object(db_mod, "_connect", lambda p: self._InterleavingConnection(
                real_connect(p), p, inject)), \
                mock.patch.object(db_mod, "_now", lambda: "2026-01-01T00:00:00"):
            batch_id = db_mod.record_ranking(
                path, RankingResult(
                    strategy="test", seed=0, n_labeled=0, model_meta={},
                    order=["K1", "K2"], scores={"K1": 1.0, "K2": 0.5}))
        self.assertGreater(batch_id, 0)
        latest = db_mod.latest_ranking(path)
        # 基线：注入批与本批共用 created_at → order 混批（含 'C1'）；修复：
        # 注入被写锁挡下，最近批次就是且仅是本批。
        self.assertEqual(latest["order"], ["K1", "K2"])
        with closing(sqlite3.connect(path)) as conn:
            shared = conn.execute(
                "SELECT COUNT(*) FROM rankings WHERE created_at='2026-01-01T00:00:00'"
            ).fetchone()[0]
        self.assertEqual(shared, 2)  # 基线为 3（K1/K2/C1 同秒）


class MM9_26_InitDbMigrationDegradation(unittest.TestCase):
    """MM9-26：迁移失败降级为日志+跳过，不再让每个筛选端点 500 重放。"""

    def _poison(self, path: Path) -> str:
        db_mod.init_db(path)
        overlong = "K" * 300 + "-abcdef123456"
        with closing(sqlite3.connect(path)) as conn:
            conn.execute("INSERT INTO articles(zotero_key,title) VALUES(?,?)",
                         (overlong, "poison"))
            # 引用表列名不在迁移白名单：重命名文章键后产生**新增**外键违例。
            conn.execute(
                "CREATE TABLE ext_ref(id INTEGER PRIMARY KEY, "
                "zkey TEXT REFERENCES articles(zotero_key))")
            conn.execute("INSERT INTO ext_ref(zkey) VALUES(?)", (overlong,))
            conn.commit()
        return overlong

    def test_migration_failure_degrades_with_warning(self):
        path = _tmp_db("rf_r8_mm926_")
        overlong = self._poison(path)
        # 基线：ValueError「超长键迁移引入新的外键违例」直接冒泡（端点 500 重放）。
        with self.assertLogs("coscreen.db", level="WARNING") as logs:
            db_mod.init_db(path)  # 修复后：不抛
        self.assertTrue(any("降级" in message for message in logs.output))
        with closing(sqlite3.connect(path)) as conn:
            length = conn.execute(
                "SELECT length(zotero_key) FROM articles").fetchone()[0]
        self.assertEqual(length, len(overlong))  # 库按未迁移口径继续可用
        # 降级不掩埋建表失败：schema 本身完好可写。
        with closing(db_mod._connect(path)) as conn, conn:
            conn.execute("INSERT INTO articles(zotero_key) VALUES('normal-key')")
        # 重试仍降级不抛（每请求重试语义）。
        db_mod.init_db(path)

    def test_healthy_db_migration_still_runs(self):
        # 控制组：无超长键的正常库零写入、无告警。
        path = _tmp_db("rf_r8_mm926_ok_")
        db_mod.init_db(path)
        with closing(sqlite3.connect(path)) as conn:
            conn.execute("INSERT INTO articles(zotero_key,title) VALUES(?,?)",
                         ("K" * 300 + "-abcdef123456", "migrate-me"))
            conn.commit()
        db_mod.init_db(path)
        with closing(sqlite3.connect(path)) as conn:
            length = conn.execute(
                "SELECT length(zotero_key) FROM articles").fetchone()[0]
        self.assertLessEqual(length, 300)  # 正常迁移照常发生（未降级路径）
        self.assertNotEqual(length, 313)


class MM10_14_LivingReviewPluginContractDoc(unittest.TestCase):
    """MM10-14：模块 docstring 与 check-now 的 409 实现对齐（仅文档，不删模块）。"""

    def test_docstring_states_unwired_contract(self):
        import custom_backend.living_review_plugin as plugin
        doc = plugin.__doc__
        # 基线：docstring 声称「check-now 端点才延迟导入并调用它」——与实现相反。
        self.assertIn("当前未接线", doc)
        self.assertIn("409", doc)
        self.assertIn("W3", doc)
        self.assertNotIn("才延迟导入并调用它", doc)

    def test_module_functions_kept_for_w3_revival(self):
        import custom_backend.living_review_plugin as plugin
        self.assertTrue(callable(plugin.fetch_pubmed_updates))
        self.assertTrue(callable(plugin.parse_medline_records))
        records = plugin.parse_medline_records(
            "PMID- 1\nTI  - Title.\n\n")
        self.assertEqual(records[0]["zotero_key"], "PMID-1")


class R3_02_ConcurrentFirstInitConvergence(unittest.TestCase):
    """R3-02：双进程并发首初始化后，各 worker 返回同一把持久密钥。"""

    _CHILD_A = """
import os, sys, time
sys.path.insert(0, sys.argv[1])
import custom_backend.auth as auth
auth.secrets.token_hex = lambda n: "A" * (2 * n)
real_fdopen = os.fdopen
def pausing_fdopen(fd, *a, **k):
    # 竞态窗口：.jwt_secret 已 O_EXCL 创建、未首写。取值须落在真实创建者的
    # 首写窗口量级内（本机实测上限约 16ms）；超长的悬停不再是「并发首初始化」
    # 而是「创建者被暂停」，那种场景由负缓存钉另行覆盖。
    time.sleep(float(sys.argv[3]))
    return real_fdopen(fd, *a, **k)
os.fdopen = pausing_fdopen
sys.stdout.write(auth._secret())
"""
    _CHILD_B = """
import os, sys, time
sys.path.insert(0, sys.argv[1])
import custom_backend.auth as auth
auth.secrets.token_hex = lambda n: "B" * (2 * n)
secret_path = os.path.join(os.environ["COBOOKSHELF_DATA_DIR"], ".jwt_secret")
for _ in range(500):
    if os.path.exists(secret_path):
        break
    time.sleep(0.01)
sys.stdout.write(auth._secret())
"""

    def _run_child(self, script: str, data_dir: str, *args: str) -> str:
        env = dict(os.environ, COBOOKSHELF_DATA_DIR=data_dir,
                   PYTHONDONTWRITEBYTECODE="1")
        completed = subprocess.run(
            [sys.executable, "-c", script, str(REPO_ROOT), data_dir, *args],
            capture_output=True, text=True, timeout=60, env=env)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return completed.stdout.strip()

    def test_both_workers_return_the_persisted_key(self):
        with tempfile.TemporaryDirectory(prefix="rf_r8_r302_") as data_dir:
            process_a = subprocess.Popen(
                [sys.executable, "-c", self._CHILD_A, str(REPO_ROOT), data_dir, "0.05"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                env=dict(os.environ, COBOOKSHELF_DATA_DIR=data_dir,
                         PYTHONDONTWRITEBYTECODE="1"))
            try:
                returned_b = self._run_child(self._CHILD_B, data_dir)
                returned_a = process_a.communicate(timeout=60)[0].strip()
            finally:
                if process_a.poll() is None:  # pragma: no cover - 清理兜底
                    process_a.kill()
            self.assertEqual(process_a.returncode, 0)
            persisted = (Path(data_dir) / ".jwt_secret").read_text(
                encoding="utf-8").strip()
            # 基线：A 返回 A、B 返回 B（Windows 镜像下 B 走应急钥）→ 两者不等；
            # 修复：首建者写后重读 + 替换失败方有界等待重读 → 全部等于持久钥。
            self.assertEqual(returned_a, persisted)
            self.assertEqual(returned_b, persisted)
            self.assertEqual(returned_a, returned_b)
            self.assertEqual(len(persisted), 64)

    def test_write_failure_path_does_not_block_auth(self):
        """第 2 轮评审：**不可写介质**（无并发写者）上每个鉴权请求不得叠加
        1s 阻塞。钉住上界：连续 3 次 ``_secret()`` 总耗时远低于旧实现的
        3.0s（旧实现无差别等满 1.0s/次）。

        用目录占住临时文件路径 → 临时文件创建即失败（= 只读/无权限同族，
        归因 ``_UNWRITABLE``），修复后必须零等待。
        """
        import time
        with tempfile.TemporaryDirectory(prefix="rf_r8_r302b_") as data_dir:
            auth_module = _import_auth(self, data_dir)
            secret_path = auth_module.data_dir() / ".jwt_secret"
            secret_path.write_text("", encoding="utf-8")
            blocker = secret_path.with_name(f".jwt_secret.{os.getpid()}.tmp")
            blocker.mkdir()
            try:
                started = time.monotonic()
                first = auth_module._secret()
                middle = auth_module._secret()
                last = auth_module._secret()
                elapsed = time.monotonic() - started
            finally:
                blocker.rmdir()
            self.assertLess(elapsed, 0.2, f"不可写介质路径耗时 {elapsed:.3f}s")
            # 应急口径仍是「进程内稳定同钥」（R3-03 语义不变）。
            self.assertEqual(first, middle)
            self.assertEqual(middle, last)

    def test_permanently_exclusive_file_does_not_block_every_auth(self):
        """第 3 轮评审：``_REPLACE_BLOCKED`` 必须区分「瞬时竞争」与「文件被
        永久独占」。

        用真实 Win32 句柄以 ``GENERIC_READ|WRITE`` + ``FILE_SHARE_READ|
        FILE_SHARE_WRITE``（**不含** share-delete）持有 ``.jwt_secret``——
        杀软/索引/备份的常见打开方式，无任何 mock。此时 ``os.replace`` 恒失败。
        钉住：首个请求至多付一次窗口（≤120ms 量级），其后靠负缓存回到零等待，
        4 线程总墙钟远低于「每请求一个窗口」（旧实现 4×0.5s≈2s）。
        """
        import ctypes
        import time
        from ctypes import wintypes

        if sys.platform != "win32":  # pragma: no cover - 该独占语义由 Win32 提供
            self.skipTest("Win32 share-deny-delete 语义仅在 Windows 上可复现")
        GENERIC_READ, GENERIC_WRITE = 0x80000000, 0x40000000
        FILE_SHARE_READ, FILE_SHARE_WRITE = 0x1, 0x2
        OPEN_EXISTING = 3
        invalid = ctypes.c_void_p(-1).value
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateFileW.restype = wintypes.HANDLE
        k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                    ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
                                    wintypes.HANDLE]

        with tempfile.TemporaryDirectory(prefix="rf_r8_r302d_") as data_dir:
            auth_module = _import_auth(self, data_dir)
            secret_path = auth_module.data_dir() / ".jwt_secret"
            secret_path.write_text("", encoding="utf-8")
            handle = k32.CreateFileW(
                str(secret_path), GENERIC_READ | GENERIC_WRITE,
                FILE_SHARE_READ | FILE_SHARE_WRITE, None, OPEN_EXISTING, 0, None)
            self.assertNotEqual(handle, invalid, "无法获取独占句柄（前置不成立）")
            try:
                first_started = time.monotonic()
                auth_module._secret()
                first_elapsed = time.monotonic() - first_started
                started = time.monotonic()
                for _ in range(3):
                    auth_module._secret()
                steady = time.monotonic() - started
            finally:
                k32.CloseHandle(handle)
            # 首个请求：至多一个收敛窗口（120ms），不能是旧的 0.5s/1.0s。
            self.assertLess(first_elapsed, 0.4, f"首个请求耗时 {first_elapsed:.3f}s")
            # 稳态：负缓存生效后不再等待（3 次合计应远小于 3×窗口）。
            self.assertLess(steady, 0.2, f"稳态 3 次合计 {steady:.3f}s")
            # 文件恢复可用后自愈：下一次调用写回并返回持久钥。
            healed = auth_module._secret()
            self.assertEqual(secret_path.read_text(encoding="utf-8").strip(), healed)

    def test_contended_replace_still_waits_and_converges(self):
        """真正的并发窗口（临时文件已落盘、仅 os.replace 被持有者挡住）仍
        必须等待并采用对方的持久钥——这是 R3-02 的修复本体，钉住它不被
        上一条的「零等待」优化误伤。"""
        with tempfile.TemporaryDirectory(prefix="rf_r8_r302c_") as data_dir:
            auth_module = _import_auth(self, data_dir)
            secret_path = auth_module.data_dir() / ".jwt_secret"
            secret_path.write_text("", encoding="utf-8")
            competing = "c" * 64
            real_replace = os.replace

            def fake_replace(src, dst):
                if str(dst) == str(secret_path) and not fake_replace.settled:
                    fake_replace.settled = True
                    # 模拟「并发创建者」在我们之前完成了原子写
                    real_replace(src, dst)  # 先让本方临时文件落位（随即将被覆盖）
                    secret_path.write_text(competing, encoding="utf-8")
                    raise PermissionError(13, "sharing violation")
                return real_replace(src, dst)

            fake_replace.settled = False
            with mock.patch.object(auth_module.os, "replace", fake_replace):
                result = auth_module._secret()
            # 采用并发创建者的持久钥，而不是本方 secret / 应急钥
            self.assertEqual(result, competing)
            self.assertEqual(secret_path.read_text(encoding="utf-8").strip(), competing)


class R3_02_NegativeCacheInvalidation(unittest.TestCase):
    """R8 收尾评审残留：``_REPLACE_BLOCKED`` 负缓存必须可失效。

    第 3 轮把「永久独占文件」从每请求付窗口降到「首请求一次 + 稳态零代价」，
    但负缓存装上后**永不撤销**：写入成功 / 文件恢复后仍在 30s 冷却期内，期间
    真实并发创建者被静默跳过收敛（返回应急钥而磁盘是对方的钥），R3-02 原问题
    在小窗口内复发。且 ``_secret`` docstring 当时声称「读取与写重试照旧…
    下一次调用即自愈」，与实现相反。

    修复：成功写回 / 读到有效持久钥 / 目标文件指纹改变（先坏后好再坏）即清除
    该路径的负缓存；冷却期内仍做一次零等待重读，接住刚写完的并发创建者。
    逐条在基线（1db6d5e 的 auth.py）上实测为红，修复后为绿。
    """

    def _blocked_replace(self, auth_module, secret_path):
        """让 ``os.replace`` 仅对目标密钥文件失败（其余路径原样放行）。"""
        real_replace = os.replace

        def replace_or_block(src, dst, *args, **kwargs):
            if str(dst) == str(secret_path):
                raise PermissionError(13, "sharing violation")
            return real_replace(src, dst, *args, **kwargs)

        patcher = mock.patch.object(auth_module.os, "replace", replace_or_block)
        self.addCleanup(patcher.stop)
        patcher.start()
        return patcher

    def _install_cooldown(self, auth_module, secret_path) -> None:
        """走真实路径装上一次负缓存：空文件 + 替换恒被挡住 → 一次窗口后记账。"""
        ignore = self._blocked_replace(auth_module, secret_path)
        try:
            first = auth_module._secret()
        finally:
            ignore.stop()
        self.assertTrue(auth_module._replace_blocked_until,
                        "前置不成立：负缓存未被安装（首位请求未付窗口？）")
        self.assertIsInstance(first, str)
        self.assertEqual(len(first), 64, "挡住期间走应急口径（64 位）")

    # ---- 失效口径 --------------------------------------------------------

    def test_successful_atomic_write_clears_cooldown(self):
        """写入成功（替换恢复可用）→ 负缓存清除并以磁盘钥为准。"""
        with _e_tmp_dir("rf_r8_r302e_") as data_dir:
            auth_module = _import_auth(self, data_dir)
            secret_path = auth_module.data_dir() / ".jwt_secret"
            secret_path.write_text("", encoding="utf-8")
            self._install_cooldown(auth_module, secret_path)
            key = str(secret_path)
            self.assertIn(key, auth_module._replace_blocked_until)
            healed = auth_module._secret()  # 替换已恢复：原子写成功
            # 基线：成功后不清除，冷却项残留到 30s 后（本断言为红）。
            self.assertNotIn(key, auth_module._replace_blocked_until)
            self.assertEqual(secret_path.read_text(encoding="utf-8").strip(), healed)
            self.assertEqual(len(healed), 64)

    def test_recovered_secret_file_clears_cooldown(self):
        """文件恢复（已有有效持久钥）→ 早退路径也必须清除负缓存。"""
        with _e_tmp_dir("rf_r8_r302f_") as data_dir:
            auth_module = _import_auth(self, data_dir)
            secret_path = auth_module.data_dir() / ".jwt_secret"
            secret_path.write_text("", encoding="utf-8")
            self._install_cooldown(auth_module, secret_path)
            recovered = "f" * 64
            secret_path.write_text(recovered, encoding="utf-8")
            # 基线：早退返回正确值但负缓存原样保留（本断言为红）。
            self.assertEqual(auth_module._secret(), recovered)
            self.assertEqual(auth_module._replace_blocked_until, {})

    def test_cooldown_read_adopts_just_written_concurrent_secret(self):
        """冷却期内仍做零等待重读：并发创建者刚写完的钥必须被采用，而不是
        退回应急钥（这是「静默跳过收敛」的最小复现形态）。

        注入口径：目标文件的前三次读（入口 existing、锁内双检、FileExistsError
        分支的 winner 重读）返回空串，仅**第四次**（冷却分支的零等待重读）返回
        并发创建者的钥；文件本身不动，故 stat 指纹不变——确保走到的是「冷却期
        零等待重读」分支而不是「指纹变化重开窗口」分支，也排除基线在前三处
        重读碰巧接住的情况。
        """
        with _e_tmp_dir("rf_r8_r302g_") as data_dir:
            auth_module = _import_auth(self, data_dir)
            secret_path = auth_module.data_dir() / ".jwt_secret"
            secret_path.write_text("", encoding="utf-8")
            self._install_cooldown(auth_module, secret_path)
            competing = "d" * 64
            self._blocked_replace(auth_module, secret_path)

            real_read = Path.read_text
            budget = {"empty_reads": 3}

            def read_with_late_write(self_path, *args, **kwargs):
                text = real_read(self_path, *args, **kwargs)
                if str(self_path) != str(secret_path):
                    return text
                if budget["empty_reads"] > 0:
                    budget["empty_reads"] -= 1
                    return ""
                return competing

            with mock.patch.object(Path, "read_text", read_with_late_write):
                result = auth_module._secret()
            self.assertEqual(budget["empty_reads"], 0, "前置不成立：未耗尽入口读预算")
            # 基线：冷却期内不再重读 → 返回应急钥（≠ competing）→ 红。
            self.assertEqual(result, competing)

    def test_break_heal_break_then_concurrent_creator_converges(self):
        """完整残留场景（先坏后好再坏 + 真实并发创建者）：冷却期内不得静默
        跳过收敛。基线：首次坏装缓存 → 恢复后缓存不清 → 再坏时跳过等待直接
        返回旧应急钥，与磁盘钥分叉。"""
        with _e_tmp_dir("rf_r8_r302h_") as data_dir:
            auth_module = _import_auth(self, data_dir)
            secret_path = auth_module.data_dir() / ".jwt_secret"
            secret_path.write_text("", encoding="utf-8")
            self._install_cooldown(auth_module, secret_path)      # 坏
            healed = "e" * 64
            secret_path.write_text(healed, encoding="utf-8")      # 好
            self.assertEqual(auth_module._secret(), healed)       # 缓存应被清除
            secret_path.write_text("", encoding="utf-8")          # 再坏
            self._blocked_replace(auth_module, secret_path)

            competing = "d" * 64
            calls = []

            def fake_converge(path):
                calls.append(str(path))
                path.write_text(competing, encoding="utf-8")
                return competing

            with mock.patch.object(auth_module, "_await_concurrent_creator",
                                   fake_converge):
                result = auth_module._secret()
            # 基线：缓存残留 → 零次收敛等待 → 返回旧应急钥（红）。
            self.assertEqual(calls, [str(secret_path)])
            self.assertEqual(result, competing)
            self.assertEqual(secret_path.read_text(encoding="utf-8").strip(), competing)

    def test_fingerprint_change_reopens_the_window(self):
        """文件指纹变化（= 「先坏后好再坏」的判据）必须让冷却失效、重新付窗口。"""
        with _e_tmp_dir("rf_r8_r302i_") as data_dir:
            auth_module = _import_auth(self, data_dir)
            secret_path = auth_module.data_dir() / ".jwt_secret"
            secret_path.write_text("", encoding="utf-8")
            self._install_cooldown(auth_module, secret_path)
            self._blocked_replace(auth_module, secret_path)
            # 目标文件被换过（mtime/size 变），但仍为空：旧判断失效。
            secret_path.write_text("", encoding="utf-8")
            os.utime(secret_path, ns=(time.time_ns(), time.time_ns()))
            calls = []
            with mock.patch.object(
                    auth_module, "_await_concurrent_creator",
                    lambda path: calls.append(str(path)) or None):
                auth_module._secret()
            # 基线：命中残留冷却 → 零次等待（红）；修复：指纹不符 → 重新等待一次。
            self.assertEqual(calls, [str(secret_path)])

    # ---- 稳态不回归 ------------------------------------------------------

    def test_permanent_block_still_reaches_zero_wait_after_clear(self):
        """清除后再次被永久挡住：重新付一次窗口，随后回到零等待（性能不回归）。"""
        with _e_tmp_dir("rf_r8_r302j_") as data_dir:
            auth_module = _import_auth(self, data_dir)
            secret_path = auth_module.data_dir() / ".jwt_secret"
            secret_path.write_text("", encoding="utf-8")
            self._install_cooldown(auth_module, secret_path)
            auth_module._secret()  # 恢复 → 成功写回并清除缓存
            secret_path.write_text("", encoding="utf-8")  # 再次坏
            self._blocked_replace(auth_module, secret_path)
            started = time.monotonic()
            first = auth_module._secret()   # 重新付一次窗口
            first_elapsed = time.monotonic() - started
            started = time.monotonic()
            for _ in range(3):
                self.assertEqual(auth_module._secret(), first)
            steady = time.monotonic() - started
            self.assertLess(first_elapsed, 0.4, f"首个请求耗时 {first_elapsed:.3f}s")
            self.assertLess(steady, 0.2, f"稳态 3 次合计 {steady:.3f}s")

    # ---- 文档与实现一致 --------------------------------------------------

    def test_docstring_states_invalidation_contract(self):
        """文档钉子：docstring 必须如实写明负缓存可失效（写入成功/读到持久钥/
        指纹改变），删掉实现里的清除或改回旧文案即红。"""
        with _e_tmp_dir("rf_r8_r302k_") as data_dir:
            doc = _import_auth(self, data_dir)._secret.__doc__
        self.assertIn("负缓存", doc)
        self.assertIn("立即失效", doc)
        self.assertIn("指纹", doc)
        self.assertIn("零等待重读", doc)
        # 旧文案（与实现相反）不得复活。
        self.assertNotIn("期间不再等待，读取与写重试照旧", doc)


if __name__ == "__main__":
    unittest.main()
