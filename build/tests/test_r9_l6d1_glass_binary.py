"""r9 L6d1 泳道钉子：binary_arm_data / binary_correlation_conversion / glass_delta_effect。

覆盖清单 §3.6 的 7 条（E:/mubi-reviewflow/out/release_plan/inventory_夜扫286确认分组.md）：

- b03·F2-63  binary_arm_data 的 `_linked` 在写事务外（TOCTOU）→ 并发删链接后留下
  孤儿 arms 行，且此后无法经 API 更新；
- b10·F2-64  （高危）`list_binary_arms` 读路径无条件 `_ensure_table` → `BEGIN
  IMMEDIATE` 抢写锁：长写事务并发读时阻塞到锁超时抛 OperationalError → 500；
- b10·F2-65  `source_locator` 是六个字符串参数里唯一无类型校验的（None/123/[...]
  → AttributeError 穿出只捕 ValueError 的端点）；
- b03·F2-66  深嵌套 `source_provenance` 的 `copy.deepcopy` 抛 RecursionError
  （非 ValueError）→ 500（deepcopy 深度族）；
- b10·F2-67  `source_provenance` 仅 isinstance(dict) 一道检查，任意嵌套/非有限值
  原样落库回显（source_provenance 零校验族）；
- b10·F2-68  phi/rho 的 ±1 守卫用浮点商判定，合法大表被舍入成 1.0 误报
  「完全分离」（工具箱 R2 违例：拒绝合法输入）；
- b06·F4-56  （高危）glass_delta 同一 deepcopy 缺陷的另一载体（deepcopy 深度族，
  4.5KB 请求即 500）。

破坏即红（R4）：每个 `test_*` 的修复断言在基线 bfd07ae 上必红（各自类首注释
记录了修前实测值）；标「逐位对照」的用例钉住基线返回值，修复后必须仍逐位相等
（R1/R2：常规输入结果不变、不新增对合法输入的拒绝）。

同族修法形态（清单 §6.3）：
- deepcopy 深度族（F2-66 / F4-56）：两载体同一套 `_MAX_PROVENANCE_DEPTH = 32`
  + `_sanitize_provenance` + `_copy_input`，超限子树整体替换占位字符串（不拒绝）、
  非有限浮点替换占位字符串；两模块的常量与函数逐字同形；
- source_provenance 零校验族（F2-67）：同一入口做深度收敛与非有限清洗，
  合法浅层来源信息逐字保留（不新增 schema 拒绝）。
"""
import json
import math
import os
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

#: 临时目录落 E: 盘（环境警示：C 盘曾满，临时输出一律不用 %TEMP%）；
#: E: 不可写时回退系统临时目录，测试不因环境差异而不可运行。
_TMP_ROOT = Path(os.environ.get("RF_R9_L6D1_TMP", os.environ.get("RF_R9_TMP_ROOT","E:/rf214tmp")+"/r9/l6d1"))
try:
    _TMP_ROOT.mkdir(parents=True, exist_ok=True)
except OSError:  # pragma: no cover - 无 E: 盘的机器
    _TMP_ROOT = None


def _tmpdir(prefix):
    return tempfile.TemporaryDirectory(prefix=prefix, dir=None if _TMP_ROOT is None else str(_TMP_ROOT),
                                       ignore_cleanup_errors=True)


#: HTTP 面的数据目录必须在导入 custom_backend 之前定好（同
#: test_r7_final_numerical / test_r8b_fl6_collab_lane 手法）。
_HTTP_FIXTURE = _tmpdir("rf_r9_l6d1_http_")
os.environ["COBOOKSHELF_DATA_DIR"] = _HTTP_FIXTURE.name
os.environ["REVIEWFLOW_MODE"] = "server"

from coscreen import binary_arm_data as arm_mod
from coscreen import binary_correlation_conversion as bcc_mod
from coscreen import glass_delta_effect as glass_mod
from coscreen.db import init_db
from coscreen.review_analysis import save_study

FIELDS = {"events_t": 10, "total_t": 20, "events_c": 8, "total_c": 20}

#: 清单 F2-68 探针原值：a=d=45016960957365413, b=c=1。
#: 数学上 phi = 1 − 2/(a+1) ≈ 1 − 4.4e-17（严格 < 1），float 商却恰为 1.0。
F268_TABLE = {"events_t": 45016960957365413, "total_t": 45016960957365414,
              "events_c": 1, "total_c": 45016960957365414}


def _nested(depth, leaf="leaf"):
    value = {"leaf": leaf}
    for _ in range(depth):
        value = {"k": value}
    return value


def _make_db(path: Path) -> Path:
    """建库并灌入两篇文章/一个研究/两条来源链接（与清单探针夹具同形）。"""
    init_db(path)
    conn = sqlite3.connect(path)
    try:
        conn.execute("INSERT INTO articles(zotero_key,title) VALUES(?,?)", ("k1", "r1"))
        conn.execute("INSERT INTO articles(zotero_key,title) VALUES(?,?)", ("k2", "r2"))
        conn.commit()
    finally:
        conn.close()
    save_study(path, "S1", "Study 1", ["k1", "k2"])
    return path


class BinaryArmDataToctouTests(unittest.TestCase):
    """b03·F2-63：链接校验与 INSERT 必须同处一个写事务。

    修前实测（本工作树 bfd07ae）：闸门中另一连接 `DELETE FROM review_reports`
    在 autocommit 下直接提交 → `save_binary_arms` 随后 INSERT 成功 →
    `orphan: [('S1','k1')]`，且之后同一研究再保存被 `_linked` 拒绝。
    """

    def setUp(self):
        self.tmp = _tmpdir("rf_r9_l6d1_arm_")
        self.db = _make_db(Path(self.tmp.name) / "arm.db")

    def tearDown(self):
        self.tmp.cleanup()

    def test_linked_check_and_insert_share_one_write_transaction(self):
        """并发删链接发生在 `_linked` 之后、INSERT 之前时不得留下孤儿行。"""
        original = arm_mod._linked
        gate = {"outcome": None}

        def gated(conn, study_id, source_key):
            original(conn, study_id, source_key)
            other = sqlite3.connect(self.db, timeout=0)
            try:
                other.execute("PRAGMA foreign_keys=ON")
                other.execute("DELETE FROM review_reports WHERE zotero_key=?", (source_key,))
                other.commit()
                gate["outcome"] = "deleted"
            except sqlite3.OperationalError as exc:
                gate["outcome"] = f"blocked: {exc}"
            finally:
                other.close()

        arm_mod._linked = gated
        try:
            try:
                arm_mod.save_binary_arms(self.db, "S1", "cmp", "out", "tp", "k1",
                                         {"events_t": 1, "total_t": 2,
                                          "events_c": 1, "total_c": 2})
                saved = True
            except ValueError:
                saved = False
        finally:
            arm_mod._linked = original

        conn = sqlite3.connect(self.db)
        try:
            arms = conn.execute("SELECT study_id,source_key FROM review_binary_arms").fetchall()
            linked = conn.execute("SELECT 1 FROM review_reports WHERE study_id=? AND zotero_key=?",
                                  ("S1", "k1")).fetchone() is not None
        finally:
            conn.close()

        # 不变量（与实现无关）：要么保存被拒且无行，要么行存在且其来源链接仍在。
        self.assertFalse(arms and not linked,
                         f"留下孤儿 arms 行：arms={arms} k1 已不链接；闸门={gate['outcome']}")
        if saved:
            self.assertTrue(linked)
        else:
            self.assertEqual([], arms)

    def test_save_study_cannot_orphan_arms_row(self):
        """修前：孤儿行存在时 `save_study` 仍可删链接（占用守卫看不到该行外的删链接）。

        修后：写事务串行化后，arms 行与链接同生同灭——先保存 arms 再试图删其
        来源，`save_study` 以「used by an analysis result」拒绝。
        """
        arm_mod.save_binary_arms(self.db, "S1", "cmp", "out", "tp", "k1",
                                 {"events_t": 1, "total_t": 2, "events_c": 1, "total_c": 2})
        with self.assertRaises(ValueError) as ctx:
            save_study(self.db, "S1", "Study 1", ["k2"])
        self.assertIn("used by an analysis result", str(ctx.exception))
        conn = sqlite3.connect(self.db)
        try:
            orphan = conn.execute(
                "SELECT a.study_id,a.source_key FROM review_binary_arms a WHERE NOT EXISTS "
                "(SELECT 1 FROM review_reports r WHERE r.study_id=a.study_id AND r.zotero_key=a.source_key)"
            ).fetchall()
        finally:
            conn.close()
        self.assertEqual([], orphan)


class BinaryArmDataReadLockTests(unittest.TestCase):
    """b10·F2-64（高危）：读路径不得抢写锁。

    修前实测：另一连接持 `BEGIN IMMEDIATE` 时，普通 SELECT 0.000s 通过，而
    `list_binary_arms` 阻塞 5.589s 后抛 `OperationalError: database is locked`。
    """

    def setUp(self):
        self.tmp = _tmpdir("rf_r9_l6d1_read_")
        self.db = _make_db(Path(self.tmp.name) / "read.db")

    def tearDown(self):
        self.tmp.cleanup()

    def test_list_binary_arms_does_not_take_write_lock(self):
        arm_mod.save_binary_arms(self.db, "S1", "cmp", "out", "tp", "k1",
                                 {"events_t": 1, "total_t": 2, "events_c": 1, "total_c": 2})
        holder = sqlite3.connect(self.db)
        try:
            holder.execute("PRAGMA journal_mode=WAL")
            holder.commit()
            holder.execute("BEGIN IMMEDIATE")
            started = time.perf_counter()
            try:
                rows = arm_mod.list_binary_arms(self.db)
            except sqlite3.OperationalError as exc:  # 修前经由此处
                self.fail(f"读路径抢写锁：{exc}（{time.perf_counter() - started:.2f}s）")
            elapsed = time.perf_counter() - started
            self.assertEqual("S1", rows[0]["study_id"])
            self.assertLess(elapsed, 1.0, f"读路径被写锁阻塞 {elapsed:.2f}s")
        finally:
            holder.rollback()
            holder.close()

    def test_list_binary_arms_without_table_returns_empty_without_ddl(self):
        """读路径不建表：空库读出 []，且不产生 review_binary_arms 表（无 DDL 写）。"""
        empty = Path(self.tmp.name) / "empty.db"
        init_db(empty)
        self.assertEqual([], arm_mod.list_binary_arms(empty))
        conn = sqlite3.connect(empty)
        try:
            present = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='review_binary_arms'"
            ).fetchone()
        finally:
            conn.close()
        self.assertIsNone(present)

    def test_list_binary_arms_legacy_schema_without_locator(self):
        """旧库缺 source_locator 列：读路径按空定位符返回，不再 ALTER 抢锁。"""
        legacy = Path(self.tmp.name) / "legacy.db"
        init_db(legacy)
        conn = sqlite3.connect(legacy)
        try:
            conn.execute("CREATE TABLE review_binary_arms (study_id TEXT NOT NULL, comparison TEXT NOT NULL,"
                         " outcome TEXT NOT NULL, timepoint TEXT NOT NULL, events_t INTEGER NOT NULL,"
                         " total_t INTEGER NOT NULL, events_c INTEGER NOT NULL, total_c INTEGER NOT NULL,"
                         " source_key TEXT NOT NULL, PRIMARY KEY(study_id,comparison,outcome,timepoint))")
            conn.execute("INSERT INTO articles(zotero_key,title) VALUES('k1','r1')")
            conn.commit()
            conn.execute("INSERT INTO review_binary_arms VALUES('S9','c','o','t',1,2,1,2,'k1')")
            conn.commit()
        finally:
            conn.close()
        rows = arm_mod.list_binary_arms(legacy)
        self.assertEqual(1, len(rows))
        self.assertEqual("", rows[0]["source_locator"])
        self.assertEqual({"events_t": 1, "total_t": 2, "events_c": 1, "total_c": 2}, rows[0]["arms"])

    def test_save_path_still_creates_and_migrates_schema(self):
        """写路径保留自愈：新库首次保存建表；旧库缺列保存时补列。"""
        fresh = Path(self.tmp.name) / "fresh.db"
        init_db(fresh)
        conn = sqlite3.connect(fresh)
        try:
            conn.execute("INSERT INTO articles(zotero_key,title) VALUES('k1','r1')")
            conn.commit()
        finally:
            conn.close()
        save_study(fresh, "S1", "Study 1", ["k1"])
        arm_mod.save_binary_arms(fresh, "S1", "c", "o", "t", "k1",
                                 {"events_t": 1, "total_t": 2, "events_c": 1, "total_c": 2},
                                 source_locator="Table 2")
        rows = arm_mod.list_binary_arms(fresh)
        self.assertEqual("Table 2", rows[0]["source_locator"])


class BinaryArmDataLocatorTests(unittest.TestCase):
    """b10·F2-65：source_locator 也要以 ValueError 报告类型错误。

    修前实测：None/123/['x'] → `AttributeError: ... has no attribute 'strip'`；
    五字段空串对照 → ValueError。
    """

    def setUp(self):
        self.tmp = _tmpdir("rf_r9_l6d1_loc_")
        self.db = _make_db(Path(self.tmp.name) / "loc.db")

    def tearDown(self):
        self.tmp.cleanup()

    def _save(self, **kwargs):
        base = {"study_id": "S1", "comparison": "cmp", "outcome": "out", "timepoint": "tp",
                "source_key": "k1", "arms": {"events_t": 1, "total_t": 2,
                                             "events_c": 1, "total_c": 2}}
        base.update(kwargs)
        arm_mod.save_binary_arms(self.db, **base)

    def test_non_string_locator_is_value_error(self):
        for locator in (None, 123, ["x"], {"a": 1}):
            with self.assertRaises(ValueError, msg=f"source_locator={locator!r}"):
                self._save(source_locator=locator)

    def test_empty_locator_still_accepted(self):
        """逐位对照：空串是合法默认值，修复不得改成必填（R2）。"""
        self._save(source_locator="")
        self.assertEqual("", arm_mod.list_binary_arms(self.db)[0]["source_locator"])

    def test_blank_required_field_control(self):
        """逐位对照：必填字段空串的既有 ValueError 口径不变。"""
        with self.assertRaises(ValueError) as ctx:
            self._save(study_id="")
        self.assertIn("study, comparison, outcome, timepoint and source report are required",
                      str(ctx.exception))


class ProvenanceDeepcopyFamilyTests(unittest.TestCase):
    """b03·F2-66 / b06·F4-56（deepcopy 深度族）与 b10·F2-67（零校验族）。

    修前实测：两模块 `copy.deepcopy(values)` 在 depth=600 抛
    `RecursionError: maximum recursion depth exceeded`（栈顶 copy.py:229）；
    depth=300 正常。F2-67 修前：任意嵌套 provenance 逐字保留，NaN 亦然。
    """

    @staticmethod
    def _glass(depth):
        return glass_mod.calculate_glass_delta_effect({
            "mean_t": 1.0, "mean_c": 0.5, "sd_c": 1.0, "n_t": 10, "n_c": 10,
            "direction": "treatment_minus_control", "source_provenance": _nested(depth)})

    @staticmethod
    def _bcc(depth):
        return bcc_mod.calculate_binary_correlation_conversion({
            **FIELDS, "transform": "phi", "source_provenance": _nested(depth)})

    def test_glass_deep_provenance_no_recursion_error(self):
        for depth in (600, 994):
            result = self._glass(depth)
            self.assertAlmostEqual(0.5, result["estimate"], places=15)
            self.assertAlmostEqual(0.21250000000000002, result["variance"], places=18)

    def test_binary_correlation_deep_provenance_no_recursion_error(self):
        for depth in (600, 994):
            result = self._bcc(depth)
            self.assertAlmostEqual(0.10050378152592121, result["estimate"], places=15)

    def test_deep_provenance_is_bounded_but_not_rejected(self):
        """超限子树整体替换为占位字符串（不抛错、不返回深树）。"""
        limit = bcc_mod._MAX_PROVENANCE_DEPTH
        self.assertEqual(limit, glass_mod._MAX_PROVENANCE_DEPTH,
                         "deepcopy 族两载体的深度上限必须同值")
        self.assertEqual(bcc_mod._DEEP_PROVENANCE_PLACEHOLDER,
                         glass_mod._DEEP_PROVENANCE_PLACEHOLDER,
                         "deepcopy 族两载体的占位符必须同形")
        for result in (self._glass(limit + 5), self._bcc(limit + 5)):
            item = result["input_data"]["source_provenance"]
            seen = 0
            while isinstance(item, dict) and not isinstance(item, str):
                item = item["k"]
                seen += 1
                self.assertLessEqual(seen, limit)
            self.assertEqual(bcc_mod._DEEP_PROVENANCE_PLACEHOLDER, item)

    def test_non_finite_provenance_is_sanitized_before_echo(self):
        """F2-67：裸 NaN/inf 不得进入 input_data（落库后 GET 回显 null 的同族口径）。"""
        for module_result in (
            bcc_mod.calculate_binary_correlation_conversion(
                {**FIELDS, "transform": "phi",
                 "source_provenance": {"note": "x", "value": float("nan"),
                                       "nested": [float("inf"), {"w": float("-inf")}]}}),
            glass_mod.calculate_glass_delta_effect(
                {"mean_t": 1.0, "mean_c": 0.5, "sd_c": 1.0, "n_t": 10, "n_c": 10,
                 "direction": "treatment_minus_control",
                 "source_provenance": {"value": float("nan")}}),
        ):
            payload = json.dumps(module_result["input_data"], ensure_ascii=False, allow_nan=False)
            self.assertIsInstance(payload, str)

    def test_shallow_provenance_preserved_bit_for_bit(self):
        """逐位对照：合法浅层来源信息逐字保留（不新增 schema 拒绝）。"""
        provenance = {"page": 3, "table": "Table 2", "extractor": "manual",
                      "rows": [{"cell": "1.2", "unit": "mg"}], "flag": True, "score": 0.5}
        bcc_result = bcc_mod.calculate_binary_correlation_conversion(
            {**FIELDS, "transform": "phi", "source_provenance": provenance})
        self.assertEqual(provenance, bcc_result["input_data"]["source_provenance"])
        glass_result = glass_mod.calculate_glass_delta_effect(
            {"mean_t": 1.0, "mean_c": 0.5, "sd_c": 1.0, "n_t": 10, "n_c": 10,
             "direction": "treatment_minus_control", "source_provenance": provenance})
        self.assertEqual(provenance, glass_result["input_data"]["source_provenance"])
        # 输入不被原地改动（原 deepcopy 的隔离语义保持）。
        self.assertEqual({"page": 3, "table": "Table 2", "extractor": "manual",
                          "rows": [{"cell": "1.2", "unit": "mg"}], "flag": True, "score": 0.5},
                         provenance)

    def test_non_dict_provenance_still_rejected(self):
        """逐位对照：非 dict 的既有 ValueError 口径不变。"""
        with self.assertRaises(ValueError) as ctx:
            bcc_mod.calculate_binary_correlation_conversion(
                {**FIELDS, "transform": "phi", "source_provenance": "str"})
        self.assertIn("source_provenance must be an object", str(ctx.exception))


class PhiRhoRoundingGuardTests(unittest.TestCase):
    """b10·F2-68：±1 守卫不得把舍入当分离（R2：拒绝合法输入）。

    修前实测（清单探针原值）：phi 与 r_equiv_approx 均
    `ValueError: ... complete separation`，而精确有理数 |phi| < 1。
    """

    def test_legal_large_table_not_rejected(self):
        a, b, c, d = (F268_TABLE["events_t"], F268_TABLE["total_t"] - F268_TABLE["events_t"],
                      F268_TABLE["events_c"], F268_TABLE["total_c"] - F268_TABLE["events_c"])
        exact_numerator = a * d - b * c
        exact_denominator_sq = (a + b) * (c + d) * (a + c) * (b + d)
        self.assertLess(exact_numerator * exact_numerator, exact_denominator_sq,
                        "夹具前提：精确 |phi| < 1")
        self.assertEqual(1.0, exact_numerator / math.sqrt(exact_denominator_sq),
                         "夹具前提：float 商会舍入到 1.0")
        for transform in ("phi", "r_equiv_approx"):
            result = bcc_mod.calculate_binary_correlation_conversion(
                {**F268_TABLE, "transform": transform})
            self.assertLess(abs(result["estimate"]), 1.0)
            self.assertGreater(result["estimate"], 0.999999999999)
            self.assertTrue(math.isfinite(result["variance"]))
            self.assertGreater(result["variance"], 0.0)
            self.assertTrue(math.isfinite(result["se"]))

    def test_true_degenerate_table_still_rejected(self):
        """逐位对照：真分离表（20/20 vs 0/20）仍按原口径拒绝。"""
        with self.assertRaises(ValueError) as ctx:
            bcc_mod.calculate_binary_correlation_conversion(
                {"events_t": 20, "total_t": 20, "events_c": 0, "total_c": 20,
                 "transform": "phi"})
        self.assertIn("complete separation", str(ctx.exception))

    def test_regular_table_unchanged_bit_for_bit(self):
        """逐位对照（基线 bfd07ae 实测值）。"""
        phi = bcc_mod.calculate_binary_correlation_conversion({**FIELDS, "transform": "phi"})
        self.assertEqual(0.10050378152592121, phi["estimate"])
        self.assertEqual(0.024739822467095197, phi["variance"])
        self.assertEqual(0.1572889775766096, phi["se"])
        rho = bcc_mod.calculate_binary_correlation_conversion({**FIELDS, "transform": "r_equiv_approx"})
        self.assertEqual(0.14865919171740052, rho["estimate"])
        self.assertEqual(0.056131695794111945, rho["variance"])
        self.assertEqual(0.23692128607221416, rho["se"])

    def test_zero_cell_table_r_equiv_unchanged(self):
        """逐位对照：含零格表的 r_equiv_approx（20/20 vs 0/20 → 0.9972）。"""
        rho = bcc_mod.calculate_binary_correlation_conversion(
            {"events_t": 20, "total_t": 20, "events_c": 0, "total_c": 20,
             "transform": "r_equiv_approx"})
        self.assertEqual(0.9972037971811801, rho["estimate"])
        self.assertEqual(3.0501892293253467e-05, rho["variance"])


class _EndpointBase(unittest.TestCase):
    """真实 app + TestClient（不重抛服务端异常，才能观察到 500）。"""

    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient

        from coscreen import tasks as tasks_mod
        from custom_backend import auth as auth_mod
        from custom_backend.main import app, DATA_DIR

        cls.client = TestClient(app, raise_server_exceptions=False)
        cls.client.__enter__()
        user = auth_mod.create_user(f"r9l6d1-{cls.__name__.lower()}@example.test",
                                    "R9L6d1-Pin-2026")
        cls.headers = {"Authorization": "Bearer " + auth_mod.create_access_token(user["id"], user["username"])}
        response = cls.client.post("/api/tasks", json={"name": f"r9-l6d1-{cls.__name__.lower()}"[:60]},
                                   headers=cls.headers)
        assert response.status_code in {200, 201}, response.text[:300]
        cls.task = response.json()["task_id"]
        info = tasks_mod.find_task(cls.task, DATA_DIR)
        cls.db = info.dir / (tasks_mod.slugify_screener(user["username"]) + ".db")
        init_db(cls.db)
        conn = sqlite3.connect(cls.db)
        try:
            conn.execute("INSERT INTO articles(zotero_key,title) VALUES('K1','Report 1')")
            conn.commit()
        finally:
            conn.close()
        save_study(cls.db, "S1", "Study 1", ["K1"])

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    def _post(self, format_name, values, effect_direction="unset"):
        if effect_direction == "unset":
            # PHI / R_EQUIV_APPROX 属 NON_DIRECTIONAL_MEASURES，SMD(glass) 需方向。
            effect_direction = ("not_applicable" if format_name == "binary_correlation_conversion"
                                else "first_vs_second")
        return self.client.post(
            f"/api/tasks/{self.task}/analysis/format-effects",
            json={"study_id": "S1", "comparison": "cmp", "outcome": "out", "timepoint": "tp",
                  "source_key": "K1", "format_name": format_name, "values": values,
                  "effect_direction": effect_direction},
            headers=self.headers)

    @staticmethod
    def _glass_values(provenance=None):
        values = {"mean_t": 1.0, "mean_c": 0.5, "sd_c": 1.0, "n_t": 10, "n_c": 10,
                  "direction": "treatment_minus_control"}
        if provenance is not None:
            values["source_provenance"] = provenance
        return values


class GlassDeltaEndpointTests(_EndpointBase):
    """b06·F4-56（高危）：修前 `[format glass depth600] 500`，常规 200。"""

    def test_regular_request_still_200(self):
        response = self._post("glass_delta_two_arm", self._glass_values())
        self.assertEqual(200, response.status_code, response.text[:300])
        self.assertAlmostEqual(0.5, response.json()["effect"]["estimate"], places=15)

    def test_deep_provenance_is_not_500(self):
        response = self._post("glass_delta_two_arm", self._glass_values(_nested(600)))
        self.assertEqual(200, response.status_code,
                         f"修前此处为 500（deepcopy RecursionError）：{response.text[:300]}")
        self.assertAlmostEqual(0.5, response.json()["effect"]["estimate"], places=15)


class BinaryCorrelationEndpointTests(_EndpointBase):
    """b03·F2-66：修前同一端点 depth=600 500、depth=300 正常。"""

    def test_regular_request_still_200(self):
        response = self._post("binary_correlation_conversion",
                              {**FIELDS, "transform": "phi"})
        self.assertEqual(200, response.status_code, response.text[:300])
        self.assertAlmostEqual(0.10050378152592121, response.json()["effect"]["estimate"], places=15)

    def test_deep_provenance_is_not_500(self):
        response = self._post("binary_correlation_conversion",
                              {**FIELDS, "transform": "phi",
                               "source_provenance": _nested(600)})
        self.assertEqual(200, response.status_code,
                         f"修前此处为 500（deepcopy RecursionError）：{response.text[:300]}")

    def test_rounding_guard_not_rejecting_legal_table(self):
        response = self._post("binary_correlation_conversion",
                              {**F268_TABLE, "transform": "phi"})
        self.assertEqual(200, response.status_code, response.text[:300])
        self.assertLess(abs(response.json()["effect"]["estimate"]), 1.0)


class BinaryArmsEndpointTests(_EndpointBase):
    """b10·F2-64（高危）/ F2-65 的 HTTP 面。"""

    def _save(self, **overrides):
        body = {"study_id": "S1", "comparison": "cmp", "outcome": "out", "timepoint": "tp",
                "source_key": "K1", "arms": {"events_t": 1, "total_t": 2,
                                             "events_c": 1, "total_c": 2}}
        body.update(overrides)
        return self.client.post(f"/api/tasks/{self.task}/analysis/binary-arms",
                                json=body, headers=self.headers)

    def test_get_survives_held_write_lock(self):
        self.assertEqual(200, self._save().status_code)
        holder = sqlite3.connect(self.db)
        try:
            holder.execute("PRAGMA journal_mode=WAL")
            holder.commit()
            holder.execute("BEGIN IMMEDIATE")
            started = time.perf_counter()
            response = self.client.get(f"/api/tasks/{self.task}/analysis/binary-arms",
                                       headers=self.headers)
            elapsed = time.perf_counter() - started
        finally:
            holder.rollback()
            holder.close()
        self.assertEqual(200, response.status_code,
                         f"修前此处 500（database is locked）：{response.text[:300]}")
        self.assertLess(elapsed, 2.0, f"读端点被写锁阻塞 {elapsed:.2f}s")

    def test_blank_locator_still_200(self):
        """逐位对照：省略/空串定位符走默认值，不得变成 422/400（R2）。"""
        body = {"study_id": "S1", "comparison": "cmp2", "outcome": "out", "timepoint": "tp",
                "source_key": "K1", "arms": {"events_t": 1, "total_t": 2,
                                             "events_c": 1, "total_c": 2},
                "source_locator": ""}
        response = self.client.post(f"/api/tasks/{self.task}/analysis/binary-arms",
                                    json=body, headers=self.headers)
        self.assertEqual(200, response.status_code, response.text[:300])


if __name__ == "__main__":
    unittest.main()
