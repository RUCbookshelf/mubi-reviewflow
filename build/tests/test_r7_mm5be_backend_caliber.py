"""r7 修复泳道「后端口径与文档」钉子测试（MM5-04/06/08/09/12/13）。

每条用例对应一个已确认缺陷的修复；把对应修复回退，该用例必须变红：
- MM5-04：提取模板入口与臂级入口的同数值 SMD 必须携带一致 estimator
  元数据并可混池合成（修前混池抛 PoolCompatibilityError）。
- MM5-06：/analysis/advanced/cumulative 的 model 参数白名单
  {fixed,random,random_pm,random_reml}（修前非法值 KeyError→500）。
- MM5-08/09：meta_regression / small_study_effects 的 effect_scale 复用
  review_analysis 合成口径（修前 FISHER_Z 标 natural、漏斗点无尺度字段）。
- MM5-12：提取落库 values 用强制转换后的计算输入，原始提交另存 raw_values
  （修前落库原始串 "3e1"，与计算用 30 不一致）。
- MM5-13：highlights rects 每项恰 4 个浮点分量（修前 3/5 元素 200 静默丢弃）。
- MM5-15/16：README 四语种不得引用仓库不存在的 requirements-dev.txt /
  run_pathB.bat，开发安装命令必须指向真实文件（修前各 4 处引用不存在文件）。
"""
import os
import re
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
fixture = tempfile.TemporaryDirectory(prefix="rf_r7_mm5be_")
os.environ["COBOOKSHELF_DATA_DIR"] = fixture.name
os.environ["REVIEWFLOW_MODE"] = "server"

from fastapi.testclient import TestClient

from coscreen import db as db_mod
from coscreen import tasks as tasks_mod
from coscreen.advanced_analysis import meta_regression, small_study_effects
from coscreen.extraction_templates import TEMPLATES, map_to_effect
from coscreen.models import Article
from coscreen.pool_compatibility import PoolCompatibilityError
from coscreen.review_analysis import (
    continuous_effect,
    list_effects,
    save_arm_effect,
    save_effect,
    save_study,
    synthesize,
)
from custom_backend import auth as auth_mod
from custom_backend.main import app, DATA_DIR

_NOW = "2026-01-01T00:00:00"


class Mm5LibraryCaliberTests(unittest.TestCase):
    """库级钉子：MM5-04 / MM5-08 / MM5-09 / MM5-12（独立临时库，不起服务）。"""

    @classmethod
    def setUpClass(cls):
        cls.dir = Path(tempfile.mkdtemp(prefix="mm5be_lib_"))
        cls.db = cls.dir / "review.db"
        db_mod.init_db(cls.db)
        db_mod.upsert_articles(
            cls.db, [Article(zotero_key=f"K{i:02d}", title=f"T{i:02d}") for i in range(1, 13)], [])
        for i in range(1, 13):
            save_study(cls.db, f"S{i:02d}", f"Study {i:02d}", [f"K{i:02d}"])

    def test_mm5_04_mixed_entry_smd_pool_synthesizes(self):
        """同一臂数据经臂级入口与提取模板入口入库后必须能混池合成。

        修前：模板入口 entry_method="extraction:rct_continuous" 不在
        smd_contract 白名单 → estimator_metadata=unclassified_smd，与臂级
        hedges_g 签名互斥 → synthesize 抛 PoolCompatibilityError。"""
        arms = {"n_t": 10, "mean_t": 10.0, "sd_t": 2.0,
                "n_c": 10, "mean_c": 11.0, "sd_c": 2.0}
        save_arm_effect(self.db, "S01", "C", "O", "T", "SMD", "K01", dict(arms))
        template_values = {"mean_t": 10.0, "sd_t": 2.0, "n_t": 10,
                           "mean_c": 11.0, "sd_c": 2.0, "n_c": 10,
                           "unit": "mmHg", "measure": "SMD"}
        effect = map_to_effect(TEMPLATES["rct_continuous"], template_values, "S02", "K02")
        expected = continuous_effect(10, 10.0, 2.0, 10, 11.0, 2.0, "SMD")
        self.assertAlmostEqual(effect["estimate"], expected["estimate"])
        self.assertAlmostEqual(effect["se"], expected["se"])
        save_effect(self.db, "S02", "C", "O", "T", "SMD", effect["estimate"], effect["se"],
                    "K02", effect["input_data"], effect["entry_method"])
        rows = [r for r in list_effects(self.db) if r["measure"] == "SMD"]
        self.assertEqual(len(rows), 2)
        for row in rows:  # 两条入口的估计量签名必须一致（修前模板行是 unclassified_smd）
            metadata = row["input_data"]["estimator_metadata"]
            self.assertEqual(metadata["estimator"], "hedges_g")
            self.assertEqual(metadata["variance_method"], "LS2_formula_with_approximate_J")
        result = synthesize(self.db, "C", "O", "T", "SMD")  # 修前：PoolCompatibilityError
        self.assertEqual(result["n_studies"], 2)

    def test_mm5_12_extraction_stores_coerced_values_with_raw_backup(self):
        """提取入库 values 必须是计算实际使用的强制转换值，原始提交另存 raw_values。

        修前：input_data["values"] 落库原始串 "3e1"/"1e2"，而计算用 30/100。"""
        raw = {"events_t": "3e1", "n_t": "1e2", "events_c": 5, "n_c": 100, "measure": "RR"}
        effect = map_to_effect(TEMPLATES["rct_binary"], raw, "S03", "K03")
        self.assertAlmostEqual(effect["estimate"], 6.0)  # (30/100)/(5/100)
        values = effect["input_data"]["values"]
        self.assertEqual(values["events_t"], 30)
        self.assertEqual(values["n_t"], 100)
        self.assertEqual(effect["input_data"]["raw_values"], raw)


class Mm5AdvancedScaleLabelTests(unittest.TestCase):
    """MM5-08 / MM5-09：meta_regression 与 small_study_effects 的效应尺度口径。"""

    @classmethod
    def setUpClass(cls):
        cls.dir = Path(tempfile.mkdtemp(prefix="mm5be_scale_"))
        cls.db = cls.dir / "review.db"
        db_mod.init_db(cls.db)
        db_mod.upsert_articles(
            cls.db, [Article(zotero_key=f"K{i:02d}", title=f"T{i:02d}") for i in range(1, 13)], [])
        for i in range(1, 13):
            save_study(cls.db, f"S{i:02d}", f"Study {i:02d}", [f"K{i:02d}"])
        # FISHER_Z 层（MM5-08）与 OR 层（MM5-09）共用研究与编码维度 1..12
        for i in range(1, 13):
            save_effect(cls.db, f"S{i:02d}", "CF", "OF", "TF", "FISHER_Z",
                        0.1 + 0.01 * i, 0.2 + 0.01 * i, f"K{i:02d}")
            save_effect(cls.db, f"S{i:02d}", "CO", "OO", "TO", "OR",
                        1.10 + 0.05 * i, 0.10 + 0.01 * i, f"K{i:02d}")
        with closing(db_mod._connect(cls.db)) as conn, conn:
            cursor = conn.execute(
                "INSERT INTO coding_dimensions(name,dtype,options_json,multi_select,unit,"
                "description,section,position,created_at) VALUES('mm5be_dose','numeric','[]',"
                "0,'','','',0,?)", (_NOW,))
            cls.dimension_id = cursor.lastrowid
            for i in range(1, 13):
                conn.execute(
                    "INSERT INTO coding_values(dimension_id,zotero_key,value,updated_at) "
                    "VALUES(?,?,?,?)", (cls.dimension_id, f"K{i:02d}", str(i), _NOW))

    def test_mm5_08_meta_regression_fisher_z_scale_matches_synthesis(self):
        """FISHER_Z 的 meta_regression 尺度必须与 synthesize 同口径。

        修前：meta_regression 把 FISHER_Z 标成 "natural"。"""
        regression = meta_regression(self.db, "CF", "OF", "TF", "FISHER_Z", self.dimension_id)
        synthesis = synthesize(self.db, "CF", "OF", "TF", "FISHER_Z")
        self.assertEqual(synthesis["effect_scale"], "Fisher z; results shown as r")
        self.assertEqual(regression["effect_scale"], synthesis["effect_scale"])

    def test_mm5_09_small_study_effects_declares_effect_scale(self):
        """small_study_effects 必须声明 points[].effect 的分析尺度。

        修前：返回 dict 无 effect_scale，OR 层点值实为 log(estimate)。"""
        funnel = small_study_effects(self.db, "CO", "OO", "TO", "OR")
        self.assertIn("effect_scale", funnel)
        self.assertEqual(funnel["effect_scale"], "log")
        regression = meta_regression(self.db, "CO", "OO", "TO", "OR", self.dimension_id)
        self.assertEqual(regression["effect_scale"], "log")


class Mm5EndpointCaliberTests(unittest.TestCase):
    """端点级钉子：MM5-06（cumulative model 白名单）/ MM5-13（rects 长度）。"""

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.client.__enter__()
        # 不走 HTTP 注册（多模块共享限流桶会撞 429，同 test_collaboration_hardening 手法）
        cls.user = auth_mod.create_user("mm5be@example.test", "Mm5be-Caliber-2026")
        cls.token = auth_mod.create_access_token(cls.user["id"], cls.user["username"])
        cls.task = cls.client.post(
            "/api/tasks", json={"name": "mm5be-backend-caliber"},
            headers=cls.auth_header()).json()["task_id"]
        info = tasks_mod.find_task(cls.task, DATA_DIR)
        cls.db = info.dir / (tasks_mod.slugify_screener(cls.user["username"]) + ".db")
        db_mod.init_db(cls.db)
        db_mod.upsert_articles(
            cls.db, [Article(zotero_key="K1", title="T1"), Article(zotero_key="K2", title="T2")], [])
        save_study(cls.db, "S1", "Study 1", ["K1"])
        save_study(cls.db, "S2", "Study 2", ["K2"])
        save_effect(cls.db, "S1", "C", "O", "T", "MD", -0.1, 0.1, "K1")
        save_effect(cls.db, "S2", "C", "O", "T", "MD", -0.2, 0.1, "K2")
        with closing(db_mod._connect(cls.db)) as conn, conn:
            cursor = conn.execute(
                "INSERT INTO coding_dimensions(name,dtype,options_json,multi_select,unit,"
                "description,section,position,created_at) VALUES('mm5be_year','numeric','[]',"
                "0,'','','',0,?)", (_NOW,))
            cls.dimension_id = cursor.lastrowid
            conn.execute("INSERT INTO coding_values(dimension_id,zotero_key,value,updated_at) "
                         "VALUES(?,?,?,?)", (cls.dimension_id, "K1", "2019", _NOW))
            conn.execute("INSERT INTO coding_values(dimension_id,zotero_key,value,updated_at) "
                         "VALUES(?,?,?,?)", (cls.dimension_id, "K2", "2021", _NOW))

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        fixture.cleanup()

    @classmethod
    def auth_header(cls):
        return {"Authorization": "Bearer " + cls.token}

    def test_mm5_06_cumulative_model_whitelist(self):
        """非法 model 必须 4xx（修前 KeyError→500）；合法四值必须 200 对照。"""
        base = f"/api/tasks/{self.task}/analysis/advanced/cumulative"
        params = {"comparison": "C", "outcome": "O", "timepoint": "T",
                  "measure": "MD", "dimension_id": self.dimension_id}
        for bad in ("bogus", "fixed ", ""):
            response = self.client.get(base, params={**params, "model": bad},
                                       headers=self.auth_header())
            self.assertEqual(response.status_code, 400,
                             f"model={bad!r}: {response.status_code} {response.text}")
        for good in ("fixed", "random", "random_pm", "random_reml"):
            response = self.client.get(base, params={**params, "model": good},
                                       headers=self.auth_header())
            self.assertEqual(response.status_code, 200,
                             f"model={good!r}: {response.status_code} {response.text}")
            self.assertTrue(response.json()["steps"])

    def test_mm5_13_rects_must_have_exactly_four_components(self):
        """rects 每项必须恰 4 个浮点分量（修前 3/5 元素 200 且读回被静默丢弃）。"""
        url = f"/api/tasks/{self.task}/highlights"
        control = {"zotero_key": "K1", "events": [
            {"kind": "highlight", "page": 1, "text": "ctrl", "rects": [[0.1, 0.2, 0.3, 0.4]]}]}
        response = self.client.post(url, json=control, headers=self.auth_header())
        self.assertEqual(response.status_code, 200, response.text)  # 4 元素对照：必须照常可用
        self.assertEqual(response.json()["highlights"][0]["rects"], [[0.1, 0.2, 0.3, 0.4]])
        for bad_rects in ([[1.0, 2.0, 3.0]], [[1.0, 2.0, 3.0, 4.0, 5.0]]):
            body = {"zotero_key": "K1", "events": [
                {"kind": "highlight", "page": 2, "text": "bad", "rects": bad_rects}]}
            response = self.client.post(url, json=body, headers=self.auth_header())
            self.assertEqual(response.status_code, 422,
                             f"rects={bad_rects}: {response.status_code} {response.text}")


class Mm5ReadmeReferenceTests(unittest.TestCase):
    """MM5-15/16：README 开发章节引用的文件与命令必须真实存在（静态守护）。"""

    REPO_ROOT = Path(__file__).resolve().parents[2]

    def test_mm5_15_16_readme_stale_file_references_removed(self):
        readme = (self.REPO_ROOT / "README.md").read_text(encoding="utf-8")
        # 修前：四语种各 1 处 `pip install -r requirements-dev.txt`（文件不存在）
        self.assertNotIn("requirements-dev.txt", readme)
        # 修前：四语种各 1 处把 run_pathB.bat 描述为开发入口（文件从未入库）
        self.assertNotIn("run_pathB.bat", readme)

    def test_mm5_15_readme_dev_commands_reference_real_paths(self):
        readme = (self.REPO_ROOT / "README.md").read_text(encoding="utf-8")
        referenced = set(re.findall(r"-r (\S+\.txt)", readme))
        self.assertTrue(referenced)
        for path in referenced:
            self.assertTrue((self.REPO_ROOT / path).is_file(), path)
        self.assertIn("python -m pytest build/tests -q", readme)
        self.assertTrue((self.REPO_ROOT / "build" / "tests").is_dir())


if __name__ == "__main__":
    unittest.main()
