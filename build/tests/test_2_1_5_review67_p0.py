# -*- coding: utf-8 -*-
"""review67 W1 · P0 四件靶向钉（评审 2026-11-12 verified 项）。

- M-01：PUT /api/tasks description 后 task.json 扩展键（protocol/
  living_review_auto）保留——修前 _json_payload 整盘重写静默丢弃。
- M-02：description 写路径走 _mutate_task 锁定合并骨架（spy 钉）；并发
  register_screener 交错不丢（线程钉）。
- M-11：四导出 CSV 公式注入转义 + decisions.csv 导出→回导对称无损。
- M-15：配置端点（Unpaywall/Crossref）出站钉扎表（_pinned_base_addresses）
  且三调用方把钉扎表传进 _get_json（堵 DNS 重绑定缝）。
- M-16：_crossref_year 对畸形上游 JSON 形状防御（不 500）。
"""
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

fixture = tempfile.TemporaryDirectory(prefix="rf_r67_p0_")
os.environ["COBOOKSHELF_DATA_DIR"] = fixture.name
os.environ["REVIEWFLOW_MODE"] = "server"

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fastapi.testclient import TestClient  # noqa: E402

from coscreen import export as export_mod  # noqa: E402
from coscreen import tasks as tasks_mod  # noqa: E402
from custom_backend import auth as auth_mod  # noqa: E402
from custom_backend import pdf_fetch_api as pdf_mod  # noqa: E402
from custom_backend.main import app  # noqa: E402

# review67 round2（deepseek 门禁观察）：本文件自建 env 推 DATA_ROOT 在
# 整目录收集时必红——main.DATA_DIR 先被其他模块导入钉死，读 task.json
# FileNotFoundError。改与 test_ai_suggest 同口径：以 main.DATA_DIR 为准。
from custom_backend.main import DATA_DIR  # noqa: E402
DATA_ROOT = Path(DATA_DIR)
PW = "R67-P0-2026!!"


class UpdateTaskDescriptionPins(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.client.__enter__()
        user = auth_mod.create_user("r67-p0@example.test", PW)
        cls.headers = {"Authorization":
                       "Bearer " + auth_mod.create_access_token(user["id"], user["username"])}
        cls.task = cls.client.post("/api/tasks", json={"name": "r67-p0"},
                                   headers=cls.headers).json()["task_id"]

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    def _task_json(self):
        return json.loads((tasks_mod.tasks_root(DATA_ROOT) / self.task / "task.json")
                          .read_text(encoding="utf-8"))

    def test_m01_extension_keys_preserved(self):
        # 修前（评审 probe）：description 分支整盘重写 → protocol/
        # living_review_auto 确定性丢失；修后随 _mutate_task 合并透传保留。
        raw = self._task_json()
        raw["protocol"] = {"version": 1}
        raw["living_review_auto"] = True
        (tasks_mod.tasks_root(DATA_ROOT) / self.task / "task.json") \
            .write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        resp = self.client.put(f"/api/tasks/{self.task}",
                               json={"description": "review67 后的新描述"},
                               headers=self.headers)
        self.assertEqual(resp.status_code, 200, resp.text)
        after = self._task_json()
        self.assertEqual(after.get("protocol"), {"version": 1})
        self.assertIs(after.get("living_review_auto"), True)
        self.assertEqual(after.get("description"), "review67 后的新描述")

    def test_m02_writes_via_locked_mutate_spy(self):
        with mock.patch.object(tasks_mod, "_mutate_task",
                               wraps=tasks_mod._mutate_task) as spy:
            resp = self.client.put(f"/api/tasks/{self.task}",
                                   json={"description": "spy 路径"},
                                   headers=self.headers)
        self.assertEqual(resp.status_code, 200, resp.text)
        spy.assert_called_once()

    def test_m02_interleave_register_screener_no_loss(self):
        # 并发交错钉：description 写与 register_screener 同时发起；修前无锁
        # 整盘覆盖可丢筛选员（评审 probe bob lost），修后锁内重读合并必双存。
        barrier = threading.Barrier(2)
        results = {}

        def put_description():
            barrier.wait()
            results["desc"] = self.client.put(
                f"/api/tasks/{self.task}", json={"description": "交错描述"},
                headers=self.headers).status_code

        def register():
            barrier.wait()
            results["reg"] = tasks_mod.register_screener(self.task, "r67bob@example.test",
                                                         DATA_ROOT)

        worker = threading.Thread(target=put_description)
        worker.start()
        register()
        worker.join()
        self.assertEqual(results["desc"], 200)
        after = self._task_json()
        self.assertEqual(after.get("description"), "交错描述")
        self.assertIn("r67bob@example.test", after.get("screeners") or [])


class CsvInjectionPins(unittest.TestCase):
    """M-11：四导出转义 + decisions.csv 对称往返。"""

    def test_cell_escape_unescape_symmetry(self):
        cases = {"=HYPERLINK(\"http://e\",\"x\")": "'=HYPERLINK(\"http://e\",\"x\")",
                 "+cmd|'/C calc'!A0": "'+cmd|'/C calc'!A0",
                 "-2+1": "'-2+1", "@sum(a)": "'@sum(a)",
                 "\tab": "'\tab", "\r\nx": "'\r\nx",
                 # 数值字面量原样（tables_api._csv_cell 同款口径，集成修订）
                 "-0.5": "-0.5", "+0.5": "+0.5", "-2": "-2", "-1e-3": "-1e-3",
                 "'已有前导撇": "''已有前导撇", "普通文本": "普通文本", "123": "123",
                 "'Tis the season": "''Tis the season"}
        for raw, escaped in cases.items():
            self.assertEqual(export_mod._csv_escape_cell(raw), escaped, raw)
            self.assertEqual(export_mod._csv_unescape_cell(escaped), raw, raw)
        # round2（deepseek M-11）：外部 CSV 的**单**前导撇（非本产品双写
        # 转义形态）不得被无条件剥掉——「'Tis the season」原样保留。
        self.assertEqual(export_mod._csv_unescape_cell("'Tis the season"),
                         "'Tis the season")
        self.assertEqual(export_mod._csv_unescape_cell("'普通外部标题"),
                         "'普通外部标题")

    def test_all_four_export_sites_escape(self):
        # 四处 to_csv 前必须统一过 _df_csv_safe（matrix/long 无往返约束，
        # 行为级由 decisions/stage2 覆盖，此处源级钉四站齐全）。
        source = Path(export_mod.__file__).read_text(encoding="utf-8")
        self.assertEqual(
            source.count("df = _df_csv_safe(df)  # review67 M-11"), 4,
            "四导出（decisions/stage2/matrix/long）都必须经转义。")

    def test_decisions_export_and_roundtrip(self):
        import pandas as pd
        rows = [{"zotero_key": "K1", "title": "=HYPERLINK(\"http://evil\",\"c\")",
                 "decision": "included", "exclusion_reason": "",
                 "notes": "+cmd|'/C calc'!A0", "tags": "-2", "screened_at": "2026-01-01"},
                {"zotero_key": "K2", "title": "'真实撇号开头", "decision": "excluded",
                 "exclusion_reason": "@理由", "notes": "正常", "tags": "",
                 "screened_at": "2026-01-02"}]
        df = pd.DataFrame(rows, columns=export_mod.DECISION_COLUMNS)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "decisions.csv"
            safe = export_mod._df_csv_safe(df)
            safe.to_csv(out, index=False, encoding="utf-8-sig")
            text = out.read_text(encoding="utf-8-sig")
            self.assertIn("'=HYPERLINK", text)      # 公式面已转义
            self.assertNotIn("\n=HYPERLINK", text)  # 无未转义裸公式
            # 对称往返：load_decisions_csv 剥离转义前缀 → 与原值逐格相等。
            loaded = export_mod.load_decisions_csv(out)
        for field in ("title", "notes", "tags", "exclusion_reason"):
            self.assertEqual(list(loaded[field]), list(df[field]), field)


class CrossrefYearPins(unittest.TestCase):
    """M-16：畸形上游 JSON 形状防御。"""

    def test_malformed_shapes_degrade_to_none(self):
        self.assertIsNone(pdf_mod._crossref_year({"issued": [1, 2]}))       # list（修前 AttributeError）
        self.assertIsNone(pdf_mod._crossref_year({"issued": "2020"}))       # str
        self.assertIsNone(pdf_mod._crossref_year(                            # date-parts=数字（修前 TypeError）
            {"issued": {"date-parts": 5}}))
        self.assertIsNone(pdf_mod._crossref_year({"issued": {"date-parts": []}}))
        self.assertIsNone(pdf_mod._crossref_year({"issued": {"date-parts": [[]]}}))
        self.assertIsNone(pdf_mod._crossref_year(None))

    def test_valid_shapes_still_work(self):
        self.assertEqual(pdf_mod._crossref_year(
            {"issued": {"date-parts": [[2020, 5]]}}), 2020)
        self.assertEqual(pdf_mod._crossref_year(
            {"published-print": {"date-parts": [[2019]]}}), 2019)


class PinnedBasePins(unittest.TestCase):
    """M-15：配置端点出站钉扎表 + 三调用方透传。"""

    def test_public_base_returns_pins_and_blocked_raises(self):
        with mock.patch.object(pdf_mod, "_resolve_host",
                               return_value=["93.184.216.34"]):
            self.assertEqual(pdf_mod._pinned_base_addresses("https://api.example.com/v2"),
                             ["93.184.216.34"])
        # 解析到被拦地址 → 失败关闭（unsafe_url），绝不带未钉扎域名出站。
        with mock.patch.object(pdf_mod, "_resolve_host",
                               return_value=["169.254.169.254"]):
            with self.assertRaises(pdf_mod.OutboundError):
                pdf_mod._pinned_base_addresses("https://api.example.com/v2")

    def test_unpaywall_and_crossref_pass_pins_to_get_json(self):
        # review67 修复注：假 DOI 必须满足 _DOI_RE（^10\.\d{4,9}/\S+$）——
        # 旧假 DOI "10.1/x" 前缀仅 1 位数字，normalize_doi 归一为 None 后
        # _crossref_message 走空标题分支直接返回 (None,"")，从不调 _get_json，
        # captured 无 "pins" → KeyError。统一改用合法形状 10.12345/r67-test。
        captured = {}

        # review67 修复注：产品侧 _get_json 在配置端点路径额外传
        # allow_private=True（限定自托管回环放行的入口标记），假实现必须
        # 同步收下，否则 TypeError 先于 captured 写入 → KeyError: 'pins'。
        def fake_get_json(url, *, timeout=pdf_mod.API_TIMEOUT_SECONDS,
                          pinned_address=None, allow_private=False):
            captured["url"] = url
            self.assertTrue(allow_private, "配置端点路径应带 allow_private=True")
            captured["pins"] = pinned_address
            return {"is_oa": False}

        settings = {"unpaywall_base_url": "https://api.example.com",
                    "email": "a@example.test"}
        with mock.patch.object(pdf_mod, "_resolve_host", return_value=["93.184.216.34"]), \
                mock.patch.object(pdf_mod, "_get_json", side_effect=fake_get_json):
            pdf_mod._unpaywall_pdf_candidates("10.12345/r67-test", settings)
        self.assertEqual(captured["pins"], ["93.184.216.34"])
        # Crossref DOI 路径同口径。
        captured.clear()

        def fake_get_json2(url, *, timeout=pdf_mod.API_TIMEOUT_SECONDS,
                           pinned_address=None, allow_private=False):
            captured["pins"] = pinned_address
            return {"message": {"issued": {"date-parts": [[2020]]}}}

        settings2 = {"crossref_base_url": "https://api.example.com"}
        with mock.patch.object(pdf_mod, "_resolve_host", return_value=["93.184.216.34"]), \
                mock.patch.object(pdf_mod, "_get_json", side_effect=fake_get_json2):
            pdf_mod._crossref_message({"doi": "10.12345/r67-test", "title": ""},
                                      settings2)
        self.assertEqual(captured["pins"], ["93.184.216.34"])

    def test_retraction_watch_pinned_too(self):
        # review67 修复注：DOI 需满足 _DOI_RE（同上）——旧 "10.1/x" 会被
        # crossref_works_url 判为 invalid_doi 直接抛 OutboundError，根本
        # 走不到（被 mock 的）_get_json。改用合法形状 10.12345/r67-test。
        captured = {}

        # 同上：fetch_crossref_work 的出站缝同样带 allow_private=True。
        def fake_get_json(url, *, timeout=pdf_mod.API_TIMEOUT_SECONDS,
                          pinned_address=None, allow_private=False):
            captured["pins"] = pinned_address
            return {"message": {}}

        with mock.patch.object(pdf_mod, "_resolve_host", return_value=["93.184.216.34"]), \
                mock.patch.object(pdf_mod, "_get_json", side_effect=fake_get_json):
            pdf_mod.fetch_crossref_work("https://api.example.com",
                                        "10.12345/r67-test")
        self.assertEqual(captured["pins"], ["93.184.216.34"])


if __name__ == "__main__":
    unittest.main()
