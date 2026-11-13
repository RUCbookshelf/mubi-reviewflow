"""r9 LX 跨域泳道钉子 —— §5.1 全部 7 条（b15·F4-43 含 F4-47、b11·F3-24、
b15·F4-77、b16·F4-82、b14·F4-100、b05·F4-28、b14·F4-26）。

每条用例按 `out/release_plan/inventory_夜扫286确认分组.md` §5.1 的探针形态
复现；把对应修复回退，该用例必红（对照组逐位不变）：

- **b15·F4-43（含 F4-47）** apply 端点两层口径分裂：``map_to_effect`` 用
  ``_coerce_number`` 接受数字字符串并算出 DOR，保存层的 ``_dta_count`` 只认
  ``int/float`` 实例 → schema 合法的 ``tp='3'`` 得 400「TP 必须是非负整数」。
  修法是把保存层守卫放宽到 map 层同一合法域（R2），非整数/负值/越界仍拒绝。
- **b11·F3-24** ``config.DEFAULT_EXCLUSION_REASONS`` / ``CUSTOM_REASON_PLACEHOLDER``
  是死配置且与实际界面分叉（config 6 条、初筛 8 条、全文另一组 6 条）。修法
  在展示层与 config 两侧对齐，并由本钉子逐项比对，界面再分叉即红。
- **b15·F4-77** canonical 缺失时批次详情 GET 整页 500。首个抛点在
  ``review_imports_api.py`` 的 ``store.batch_summary``（``except KeyError``
  漏 ValueError），不是 ``available()``。
- **b16·F4-82** 同族：``available()`` 读路径裸抛 ValueError（直调 + GET 500），
  写路径 ``restore()`` 被译 409，读写口径分裂。修法是读路径降级
  ``available: False`` + ``reason``，写路径继续 409 且文案一致。
- **b14·F4-100【高】** 合法有限 ±1.5e308 参与者值 → ``stdev`` 得 1.73e308，
  ``continuous_effect`` 的 ``sd_t**2`` 抛 OverflowError → HTTP 500。
- **b05·F4-28** numpy/scikit-learn 请求期懒导入但后端部署清单未声明；
  按该清单自建部署时 /evidence-map 每次 ImportError 500。
- **b14·F4-26** ``required_fields`` 类型不统一（6 条路径为整句 str），前端把
  非列表值整体丢弃换通用提示。

环境：所有临时数据落 E: 盘（C 盘曾 100% 满，不用 %TEMP%）。
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

_TMP_ROOT = Path(os.environ.get("RF_R9_TMP", os.environ.get("RF_R9_TMP_ROOT","E:/rf214tmp")+"/r9/lx"))
_TMP_ROOT.mkdir(parents=True, exist_ok=True)
FIXTURE = tempfile.mkdtemp(prefix="rf_r9lx_nail_", dir=str(_TMP_ROOT))
# 只占住数据目录（未设时才设，避免覆盖先导入模块的隔离目录）。
# **不设 REVIEWFLOW_MODE**：main._server_mode() 每次调用现读环境，默认即
# desktop——在这里 setdefault('desktop') 会把同进程内后续模块的
# setdefault('server') 变成 no-op，把 test_w4_study_design 的协作端点钉成
# 404（实测：本文件排在它前面时该用例红）。本文件的用例都是 desktop 语义，
# 不依赖该变量被写死。
os.environ.setdefault("COBOOKSHELF_DATA_DIR", FIXTURE)

INDEX = REPO / "custom_frontend" / "index.html"


# ---------------------------------------------------------------------------
# b15·F4-43（含 F4-47）：apply 端点 map 层 / 保存层同一口径
# ---------------------------------------------------------------------------

class _AppFixture:
    """真实 app + TestClient（模块级单例，供 F4-43 / F4-77 / F4-82 / F4-100 共用）。

    只 boot 一次：``auth.create_user`` 对同名账号第二次调用会抛 AuthError
    （筛选员数据路径冲突），故不在每个 TestCase 里重注册。
    """

    client = None
    headers = None
    user = None

    @classmethod
    def boot(cls):
        if _AppFixture.client is not None:
            return cls
        from fastapi.testclient import TestClient

        from custom_backend.main import app
        from custom_backend import auth as auth_mod

        _AppFixture.client = TestClient(app, raise_server_exceptions=False)
        _AppFixture.client.__enter__()
        _AppFixture.user = auth_mod.create_user("r9lx-nail@example.test", "R9-LX-Nail-2026")
        _AppFixture.headers = {"Authorization": "Bearer " + auth_mod.create_access_token(
            _AppFixture.user["id"], _AppFixture.user["username"])}
        return cls

    @classmethod
    def shutdown(cls):
        """保留给模块级 tearDownModule（类级调用是 no-op）。"""
        if _AppFixture.client is not None:
            _AppFixture.client.__exit__(None, None, None)
            _AppFixture.client = None

    def new_task(self, name):
        response = self.client.post("/api/tasks", json={"name": name}, headers=self.headers)
        assert response.status_code in (200, 201), response.text
        return response.json()["task_id"]

    def owner_db(self, task_id):
        from custom_backend.main import DATA_DIR
        from coscreen import tasks as tasks_mod

        info = tasks_mod.find_task(task_id, DATA_DIR)
        return info.dir / (tasks_mod.slugify_screener(self.user["username"]) + ".db")


def tearDownModule():
    _AppFixture.shutdown()


class DtaCountTwoLayerCaliberTests(unittest.TestCase, _AppFixture):
    """b15·F4-43：同一 apply 请求里 map 层接受、保存层拒绝 = 误导性 400。"""

    @classmethod
    def setUpClass(cls):
        cls.boot()

    def _apply(self, task_id, values):
        return self.client.post(
            f"/api/tasks/{task_id}/extraction/K1/apply",
            json={"template_id": "diagnostic_2x2", "study_id": "S1",
                  "index_test": "IT", "target_condition": "TC",
                  "threshold": "TH", "reference_standard": "RS", "values": values},
            headers=self.headers)

    def test_数字字符串计数与数字计数同得_200(self):
        from coscreen import db as articles_db, review_analysis
        from coscreen.models import Article

        task = self.new_task("r9lx-f43")
        db = self.owner_db(task)
        articles_db.init_db(db)
        articles_db.upsert_articles(db, [Article(zotero_key="K1", title="Study K1")], [])
        review_analysis.save_study(db, "S1", "Study 1", ["K1"])
        cells = {"fp": 1, "fn": 2, "tn": 10}
        # 修前：tp='3' → 400「TP 必须是非负整数」；tp=3 / 3.0 → 200
        results = {}
        for raw in ("3", " 3 ", "3.0", 3, 3.0):
            response = self._apply(task, {**cells, "tp": raw})
            results[raw] = response
            self.assertEqual(response.status_code, 200,
                             f"tp={raw!r} 是 schema 合法计数（map 层已接受），保存层不得拒绝：{response.text}")
        # 逐位一致：三种数字形态落库的 DOR 与 SE 相同
        payloads = {raw: r.json() for raw, r in results.items()}
        for raw in (" 3 ", "3.0", 3.0):
            self.assertEqual(payloads[raw]["estimate"], payloads[3]["estimate"])
            self.assertEqual(payloads[raw]["se"], payloads[3]["se"])
        self.assertEqual(payloads[3]["estimate"], 15.0)

    def test_非整数与负数与越界仍拒绝(self):
        from coscreen import db as articles_db, review_analysis
        from coscreen.models import Article

        task = self.new_task("r9lx-f43b")
        db = self.owner_db(task)
        articles_db.init_db(db)
        articles_db.upsert_articles(db, [Article(zotero_key="K1", title="Study K1")], [])
        review_analysis.save_study(db, "S1", "Study 1", ["K1"])
        cells = {"fp": 1, "fn": 2, "tn": 10}
        for raw in ("4.5", "abc", "", None, True, -1, "2**63", 2**63):
            response = self._apply(task, {**cells, "tp": raw})
            self.assertEqual(response.status_code, 400,
                             f"tp={raw!r} 必须在 400 口径拒绝，实际 {response.status_code}：{response.text}")

    def test_模块层计数口径与端点一致(self):
        """同一守卫的库级入口（对照：合法整数与 2**63-1 边界逐位不变）。"""
        from custom_backend import main as backend_main

        self.assertEqual(backend_main._dta_count({"tp": "3"}, "tp"), 3)
        self.assertEqual(backend_main._dta_count({"tp": 2**63 - 1}, "tp"), 2**63 - 1)
        self.assertEqual(backend_main._dta_count({"tp": 5.0}, "tp"), 5)
        with self.assertRaises(ValueError):
            backend_main._dta_count({"tp": 2**63}, "tp")
        with self.assertRaises(ValueError):
            backend_main._dta_count({"tp": float(2**63)}, "tp")
        with self.assertRaises(ValueError):
            backend_main._dta_count({"tp": -1}, "tp")


# ---------------------------------------------------------------------------
# b11·F3-24：排除理由词表 config ↔ 界面一致
# ---------------------------------------------------------------------------

def _select_options(html, select_id):
    """取 ``<select id=..>`` 块内 ``<option value=..>`` 的取值（按出现顺序）。"""
    block = re.search(r'<select[^>]*id="%s"[^>]*>(.*?)</select>' % re.escape(select_id), html, re.S)
    assert block, f"index.html 未找到 #{select_id}"
    return re.findall(r'<option value="([^"]*)"', block.group(1))


class ExclusionReasonVocabularyTests(unittest.TestCase):
    """b11·F3-24：config 词表是唯一文档化口径，且与两个界面下拉逐项对齐。"""

    @classmethod
    def setUpClass(cls):
        cls.html = INDEX.read_text(encoding="utf-8")
        from coscreen import config

        cls.config = config

    def test_全文下拉等于全文词表加自定义入口(self):
        options = _select_options(self.html, "ftReason")
        # 首项是占位（空 value），末项是「其他（自定义）」
        self.assertEqual(options[0], "")
        self.assertEqual(options[-1], "__custom__")
        self.assertEqual(options[1:-1], self.config.FULLTEXT_EXCLUSION_REASONS)

    def test_初筛下拉等于默认词表加自定义入口(self):
        # 初筛卡的下拉由 el('option', ...) 动态构建（#excReason）
        block = re.search(r"id: 'excReason'.*?el\('option', \{ value: '__custom__'", self.html, re.S)
        self.assertIsNotNone(block, "index.html 未找到初筛排除理由下拉的构建代码")
        inline_list = re.search(r"\[('人群不符'.*?)\]\s*\)?\s*\.map", block.group(0), re.S)
        self.assertIsNotNone(inline_list, "初筛下拉的硬编码词表未找到")
        rendered = re.findall(r"'([^']+)'", inline_list.group(1))
        self.assertEqual(rendered, self.config.DEFAULT_EXCLUSION_REASONS)

    def test_自定义占位文案与界面同字(self):
        self.assertEqual(self.config.CUSTOM_REASON_PLACEHOLDER, "请输入自定义排除理由")
        self.assertIn('placeholder="请输入自定义排除理由"', self.html)
        self.assertIn("placeholder: T('请输入自定义排除理由')", self.html)

    def test_两个下拉的公共理由集合一致(self):
        """初筛与全文的交集即默认词表；全文只多出「全文无法获取」。"""
        full = _select_options(self.html, "ftReason")[1:-1]
        self.assertEqual(set(full) - set(self.config.DEFAULT_EXCLUSION_REASONS),
                         {"全文无法获取"})
        self.assertEqual(set(full) & set(self.config.DEFAULT_EXCLUSION_REASONS),
                         set(self.config.DEFAULT_EXCLUSION_REASONS))

    def test_词表不含重复项(self):
        reasons = self.config.FULLTEXT_EXCLUSION_REASONS
        self.assertEqual(len(reasons), len(set(reasons)), "排除理由词表出现重复项")


# ---------------------------------------------------------------------------
# b15·F4-77 / b16·F4-82：撤销读路径降级、写路径 409
# ---------------------------------------------------------------------------

def _seed_canonical_missing_batch(db, actor_id, batch_id="b1", record_id="rec1"):
    """按 §5.1 探针形态造库：批次/源记录/元数据提议 + 可用撤销条目，但 canonical 文章缺失。"""
    from coscreen import import_review_undo as review_undo
    from coscreen import review_imports as store, review_workflow as wf

    store.migrate(db)
    review_undo.migrate(db)
    record = {"zotero_key": record_id, "title": "Incoming", "doi": "10.x/1"}
    with closing(wf.connect(db)) as conn, conn:
        conn.execute("INSERT INTO workflow_sources VALUES(?,?,?,?,?)",
                     ("s", "f.ris", "f.ris", 1, "now"))
        conn.execute("INSERT INTO workflow_import_batches VALUES(?,?,?,?,?,?,?)",
                     (batch_id, str(actor_id), "h", "batch", 1, "staged", "now"))
        conn.execute("INSERT INTO workflow_source_records VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (record_id, "s", record_id, 0, "f.ris", json.dumps(record), json.dumps(record),
                      record_id, record_id, "missing-key", None, "new", "staged", "now"))
        conn.execute("INSERT INTO workflow_import_members VALUES(?,?,?)",
                     (batch_id, record_id, 0))
        conn.execute("INSERT INTO workflow_import_metadata(record_id,canonical_key,before_json,"
                     "incoming_json) VALUES(?,?,?,?)",
                     (record_id, "missing-key", json.dumps(record), json.dumps(record)))
        version = conn.execute(
            "SELECT version FROM workflow_state WHERE singleton=1").fetchone()[0]
        conn.execute("INSERT INTO workflow_import_undo(batch_id,actor_id,kind,subject_id,"
                     "before_json,after_json,policy_version,state,created_at) "
                     "VALUES(?,?,?,?,?,?,?,?,?)",
                     (batch_id, str(actor_id), "metadata", record_id,
                      json.dumps({"legacy_seq": 0}),
                      json.dumps({"legacy_seq": 0, "candidates": [], "records": [],
                                  "proposals": [], "articles": [], "acceptances": []}),
                      version, "available", "now"))
    return version


class UndoCorruptCanonicalReadWriteTests(unittest.TestCase, _AppFixture):
    """b15·F4-77 + b16·F4-82：同一坏数据，读路径不得 500，写路径仍是 409。"""

    @classmethod
    def setUpClass(cls):
        cls.boot()

    def test_available_在_canonical_缺失时降级而不裸抛(self):
        from coscreen import db as articles_db
        from coscreen import import_review_undo as review_undo, review_workflow as wf

        task = self.new_task("r9lx-f82")
        db = self.owner_db(task)
        articles_db.init_db(db)
        actor = self.user["id"]
        _seed_canonical_missing_batch(db, actor)
        # 修前：ValueError: Canonical article no longer exists...
        with closing(wf.connect(db, read_only=True)) as conn:
            state = review_undo.available(conn, "b1", str(actor))
        self.assertFalse(state["available"], "库外改写状态下撤销必须判为不可用")
        self.assertIsNone(state["entry_id"])
        self.assertIn("Canonical article", state.get("reason", ""))

    def test_restore_仍以_ValueError_拒绝同一状态(self):
        """写路径口径不变（端点 409），且文案与读路径降级原因一致。"""
        from coscreen import db as articles_db
        from coscreen import import_review_undo as review_undo, review_workflow as wf

        task = self.new_task("r9lx-f82b")
        db = self.owner_db(task)
        articles_db.init_db(db)
        actor = self.user["id"]
        _seed_canonical_missing_batch(db, actor)
        with closing(wf.connect(db)) as conn, conn:
            with self.assertRaises(ValueError) as ctx:
                review_undo.restore(conn, "b1", str(actor))
        self.assertIn("Canonical article", str(ctx.exception))

    def test_批次详情_GET_不再_500(self):
        """b15·F4-77：首个抛点在 batch_summary（except KeyError 漏 ValueError）。"""
        from coscreen import db as articles_db

        task = self.new_task("r9lx-f77")
        db = self.owner_db(task)
        articles_db.init_db(db)
        actor = self.user["id"]
        version = _seed_canonical_missing_batch(db, actor)
        base = f"/api/tasks/{task}/import-batches/b1"
        detail = self.client.get(base, headers=self.headers)
        # 修前：500 {"detail":"服务暂时无法完成请求，请稍后重试。"}
        self.assertNotEqual(detail.status_code, 500,
                            f"批次详情读路径不得整页 500：{detail.text}")
        self.assertEqual(detail.status_code, 409, detail.text)
        self.assertIn("Canonical article", detail.text)
        # 写路径同状态：409（口径一致）
        undo = self.client.post(base + "/undo",
                                json={"expected_policy_version": version},
                                headers=self.headers)
        self.assertEqual(undo.status_code, 409, undo.text)
        # 对照：批次不存在仍是 404（KeyError 分支不变）
        missing = self.client.get(f"/api/tasks/{task}/import-batches/nope", headers=self.headers)
        self.assertEqual(missing.status_code, 404, missing.text)

    def test_补回_canonical_后详情恢复_200_且撤销不可用(self):
        from coscreen import db as articles_db
        from coscreen import review_workflow as wf

        task = self.new_task("r9lx-f77c")
        db = self.owner_db(task)
        articles_db.init_db(db)
        actor = self.user["id"]
        _seed_canonical_missing_batch(db, actor)
        with closing(wf.connect(db)) as conn, conn:
            conn.execute("INSERT INTO articles(zotero_key,title) VALUES('missing-key','X')")
        detail = self.client.get(f"/api/tasks/{task}/import-batches/b1", headers=self.headers)
        self.assertEqual(detail.status_code, 200, detail.text)
        self.assertFalse(detail.json()["undo"]["available"])


# ---------------------------------------------------------------------------
# b14·F4-100：IPD 极端有限值不得 500
# ---------------------------------------------------------------------------

class IpdExtremeFiniteValueTests(unittest.TestCase, _AppFixture):
    """b14·F4-100：合法有限 ±1.5e308 → stdev 1.73e308 → sd_t**2 OverflowError 500。"""

    @classmethod
    def setUpClass(cls):
        cls.boot()

    def _task_with_link(self, name):
        from coscreen import db as articles_db, review_analysis
        from coscreen.models import Article

        task = self.new_task(name)
        db = self.owner_db(task)
        articles_db.init_db(db)
        articles_db.upsert_articles(db, [Article(zotero_key="K1", title="Study K1")], [])
        review_analysis.save_study(db, "S1", "Study 1", ["K1"])
        return task

    @staticmethod
    def _body(measure, values):
        return {"study_id": "S1", "comparison": "c", "outcome": "o", "timepoint": "t",
                "measure": measure, "effect_direction": "first_vs_second",
                "source_key": "K1",
                "participants": [{"arm": "treatment", "value": v} for v in values]
                                + [{"arm": "control", "value": v} for v in values]}

    def test_极端有限值收敛为_400_而不是_500(self):
        task = self._task_with_link("r9lx-f100")
        extreme = (1.5e308, -1.5e308, 1.5e308, -1.5e308)
        for measure in ("MD", "SMD"):
            response = self.client.post(f"/api/tasks/{task}/analysis/ipd/study",
                                        json=self._body(measure, extreme),
                                        headers=self.headers)
            self.assertEqual(response.status_code, 400,
                             f"{measure} 极端有限值必须以 400 拒绝，实际 {response.status_code}：{response.text}")

    def test_普通参与者值对照逐位不变(self):
        task = self._task_with_link("r9lx-f100b")
        response = self.client.post(f"/api/tasks/{task}/analysis/ipd/study",
                                    json=self._body("MD", (1.0, 2.0, 3.0, 4.0)),
                                    headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["summary"]["sd_t"], 1.2909944487358056)
        self.assertEqual(payload["estimate"], 0.0)
        self.assertEqual(payload["se"], 0.9128709291752768)

    def test_模块层常规输入逐位不变(self):
        from coscreen.review_analysis import continuous_effect

        self.assertEqual(continuous_effect(4, 10.0, 2.0, 4, 11.5, 2.5, "MD"),
                         {"measure": "MD", "estimate": -1.5,
                          "se": 1.6007810593582121, "se_scale": "natural"})
        self.assertEqual(continuous_effect(10, 10.0, 2.0, 10, 11.0, 2.0, "SMD"),
                         {"measure": "SMD", "estimate": -0.47887323943661975,
                          "se": 0.4349582198844284, "se_scale": "natural"})
        self.assertEqual(continuous_effect(5, 1e150, 1e150, 7, -1e150, 1e150, "MD"),
                         {"measure": "MD", "estimate": 2e150,
                          "se": 5.8554004376911985e149, "se_scale": "natural"})

    def test_零方差与非法输入的_ValueError_契约不变(self):
        from coscreen.review_analysis import continuous_effect

        with self.assertRaisesRegex(ValueError, "zero sampling variance"):
            continuous_effect(2, 0.0, 1e-300, 2, 0.0, 1e-300, "MD")
        with self.assertRaisesRegex(ValueError, "MD or SMD"):
            continuous_effect(4, 1.0, 1.0, 4, 1.0, 1.0, "RR")
        with self.assertRaisesRegex(ValueError, "n >= 2"):
            continuous_effect(1, 1.0, 1.0, 4, 1.0, 1.0, "MD")


# ---------------------------------------------------------------------------
# b05·F4-28：部署清单声明 numpy/scipy/scikit-learn；缺失依赖翻译为 ValueError
# ---------------------------------------------------------------------------

class EvidenceMapperDependencyTests(unittest.TestCase):
    """b05·F4-28：请求期懒导入的依赖必须被后端清单声明，且缺失时可诊断。"""

    def test_后端清单声明与打包清单同版本口径(self):
        backend = (REPO / "custom_backend" / "requirements.txt").read_text(encoding="utf-8")
        packed = (REPO / "build" / "requirements-app.txt").read_text(encoding="utf-8")

        def pins(text):
            return {name: spec for name, spec in re.findall(
                r"^(numpy|scipy|scikit-learn)([<>=!~][^\s#]*)", text, re.M)}

        declared = pins(backend)
        expected = pins(packed)
        self.assertEqual(set(declared), {"numpy", "scipy", "scikit-learn"},
                         "后端部署清单必须声明三个请求期依赖")
        for name in declared:
            self.assertEqual(declared[name], expected[name],
                             f"{name} 的版本区间与 build/requirements-app.txt 不一致")

    def test_缺失依赖翻译为带说明的_ValueError(self):
        """阻断 sklearn/numpy 的导入（§5.1 探针同款手法），不真的卸依赖。

        进程里 numpy/scipy/sklearn 可能已被别的用例导入并缓存，故先临时摘掉
        sys.modules 里的这几棵子树，再挂 meta_path 阻断器；结束后原样恢复。
        """
        from coscreen import evidence_mapper

        class _Blocker:
            def find_spec(self, fullname, path=None, target=None):
                if fullname.split(".")[0] in {"sklearn", "numpy"}:
                    raise ImportError("No module named '%s' (simulated missing deployment dependency)" % fullname)
                return None

        articles = [{"title": "diabetes randomized trial", "abstract": "patients with diabetes"},
                    {"title": "hypertension cohort", "abstract": "adults with hypertension"}]
        cached = {name: module for name, module in list(sys.modules.items())
                  if name.split(".")[0] in {"numpy", "sklearn", "scipy"}}
        for name in cached:
            del sys.modules[name]
        blocker = _Blocker()
        sys.meta_path.insert(0, blocker)
        try:
            with self.assertRaises(ValueError) as ctx:
                evidence_mapper.generate_evidence_map(articles, "year", "journal", cluster_count=1)
        finally:
            sys.meta_path.remove(blocker)
            sys.modules.update(cached)
        message = str(ctx.exception)
        self.assertIn("numpy", message)
        self.assertIn("scikit-learn", message)


# ---------------------------------------------------------------------------
# b14·F4-26：required_fields 恒为 list[str]
# ---------------------------------------------------------------------------

class EntryGuidanceRequiredFieldsTests(unittest.TestCase):
    """b14·F4-26：6 条路径的 required_fields 曾是整句 str，前端整体丢弃。"""

    @classmethod
    def setUpClass(cls):
        from coscreen import entry_guidance

        cls.guidance = entry_guidance
        cls.payload = entry_guidance.recommend_entry_routes(
            list(entry_guidance.DATA_SHAPES))

    def test_每条候选的_required_fields_都是字符串列表(self):
        self.assertTrue(self.payload["candidates"])
        for candidate in self.payload["candidates"]:
            self.assertIsInstance(candidate["required_fields"], list,
                                  f"{candidate['shape']} 的 required_fields 不是列表")
            self.assertTrue(candidate["required_fields"],
                            f"{candidate['shape']} 的 required_fields 为空列表")
            for field in candidate["required_fields"]:
                self.assertIsInstance(field, str,
                                      f"{candidate['shape']} 的 required_fields 含非字符串项")

    def test_整句描述的路径不被拆成逐字符(self):
        """修前 API 消费方迭代该字段得到逐字符输出——列表长度必须远小于句长。"""
        for candidate in self.payload["candidates"]:
            if candidate["entry_module"] == "coscreen.survival_summary_conversion":
                self.assertEqual(len(candidate["required_fields"]), 1)
                self.assertGreater(len(candidate["required_fields"][0]), 5)

    def test_集合路径的排序与内容逐位不变(self):
        """对照组：原本就是 list 的路径，内容与顺序不得因收口而变。"""
        by_shape = {c["shape"]: c for c in self.payload["candidates"]}
        self.assertEqual(by_shape["dta_2x2"]["required_fields"],
                         ["fn", "fp", "index_test", "reference_standard",
                          "target_condition", "threshold", "tn", "tp"])
        self.assertEqual(by_shape["correlation_r_n"]["required_fields"], ["n", "r"])

    def test_静态表本身已归一(self):
        for shape, paths in self.guidance._SHAPE_PATHS.items():
            for path in paths:
                self.assertIsInstance(path["required_fields"], list, f"{shape} 的静态表未归一")


if __name__ == "__main__":
    unittest.main(verbosity=2)
