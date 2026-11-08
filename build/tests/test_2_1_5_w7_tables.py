# -*- coding: utf-8 -*-
"""2.1.5 Wave G4 · W7 表生成（Table 1 / SoF）红钉（专属新文件，撤实现必红）。

权威设计：``out/release_plan/drafts/design_2.1.5_tables.md`` v1.0 §三。

按任务书 W7 红钉清单成文，先于实现写成（端点缺席时本文件全红）：

1. **同口径一致性钉**：同一任务数据下 ``GET /export/matrix``（编码矩阵 CSV）与
   ``POST /tables/characteristics``（inclusion="matrix"，CSV）逐行逐格相等；
2. **docx 可打开且表头分组正确**：python-docx 读回断言「分区表头行 + 列名行 +
   数据行」三段结构、合并单元格跨度、表注含「自动生成于」；
3. **SoF 空数据列 = 空 + 脚注（非 0、非臆造）**：无评价数据时「研究局限性」格
   为空串且脚注写明「未评价」；任何空数据格都不得写成 0；
4. **降级建议列 = grade_assessor 输出原样**：列文本必须含 suggest_* 返回的
   signal 数值的 ``str()`` 原样（不四舍五入）与 rule 码；
5. **纳入过滤口径钉**：默认（初筛+复筛均纳入）排除复筛已排除者；stage1 口径
   保留初筛纳入者；matrix 口径 = 编码矩阵全量；
6. **CSV 公式注入防护**：``=``/``+``/``-``/``@``/Tab/CR 开头的值加 ``'`` 前缀
   （沿既有导出模式，见 ``custom_backend/main.py`` MM-05）；
7. **权限**：非任务成员 403；未知任务 404。

另钉 SoF 的研究局限性列与既有 ``GET /appraisal/grade-input`` 聚合一致（取最严重
档、口径同源），以及 ``?detail=1`` 装配明细 JSON 的形状。

运行：
    python -m pytest build/tests/test_2_1_5_w7_tables.py -q
"""
from __future__ import annotations

import csv
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

fixture = tempfile.TemporaryDirectory(prefix="rf_w7tab_")
os.environ["COBOOKSHELF_DATA_DIR"] = fixture.name
os.environ["REVIEWFLOW_MODE"] = "server"

from fastapi.testclient import TestClient  # noqa: E402

from coscreen import appraisal as appraisal_core  # noqa: E402
from coscreen import coding as coding_mod  # noqa: E402
from coscreen import db as db_mod  # noqa: E402
from coscreen import fulltext as fulltext_mod  # noqa: E402
from coscreen import grade_assessor as grade  # noqa: E402
from coscreen import review_analysis as analysis_mod  # noqa: E402
from coscreen import tasks as tasks_mod  # noqa: E402
from coscreen.models import Article  # noqa: E402
from custom_backend import auth as auth_mod  # noqa: E402
from custom_backend.main import DATA_DIR, app  # noqa: E402

USERS = ("w7owner@example.test", "w7member@example.test", "w7outsider@example.test")
TOOL = "rob2"


def _make_user(prefix: str):
    username = f"{prefix}@example.test"
    user = auth_mod.create_user(username, f"{prefix}-Pass-2026!")
    return user, {"Authorization": "Bearer " +
                  auth_mod.create_access_token(user["id"], user["username"])}


def _read_csv(payload: bytes) -> list[list[str]]:
    text = payload.decode("utf-8-sig")
    return [row for row in csv.reader(io.StringIO(text, newline=""))]


class W7TablesBase(unittest.TestCase):
    _seq = 0
    #: 每个测试类用独立的账号前缀：混跑（同进程多测试文件、DATA_DIR 可能复用）
    #: 时 class 之间不会撞用户名（重名注册会抛 AuthError）。
    PREFIX = "w7base"

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app, raise_server_exceptions=False)
        cls.client.__enter__()
        cls.owner, cls.auth = _make_user(f"{cls.PREFIX}owner")
        cls.member, cls.member_auth = _make_user(f"{cls.PREFIX}member")
        cls.outsider, cls.outsider_auth = _make_user(f"{cls.PREFIX}out")

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        # 临时目录在整个模块结束时才删（见文件末尾 tearDownModule）：类之间删
        # DATA_DIR 会让混跑时后一个类拿不到任务目录/用户库。

    def setUp(self):
        type(self)._seq += 1
        # 任务名带类前缀：任务名在数据目录内全局唯一，混跑/同类复用序号时
        # 不能与其它测试类撞名（否则 409 已有同名任务）。
        response = self.client.post(
            "/api/tasks", json={"name": f"{self.PREFIX}-{type(self)._seq}"},
            headers=self.auth)
        self.assertIn(response.status_code, (200, 201), response.text)
        self.task = response.json()["task_id"]
        invite = self.client.post(
            f"/api/tasks/{self.task}/collaboration/invite",
            json={"username": self.member["username"], "role": "coder"},
            headers=self.auth)
        self.assertEqual(invite.status_code, 201, invite.text)
        self.info = tasks_mod.find_task(self.task, DATA_DIR)
        self.db = self.info.dir / (tasks_mod.slugify_screener(self.owner["username"]) + ".db")
        self.table1 = f"/api/tasks/{self.task}/tables/characteristics"
        self.sof = f"/api/tasks/{self.task}/tables/sof"

    # ---- 夹具 ---------------------------------------------------------
    def _seed(self):
        """三篇文献：K1/K2 初筛+复筛均纳入且已编码；K3 初筛排除；K4 初筛纳入
        但复筛排除（却有编码值——用来钉纳入过滤口径）。"""
        db_mod.init_db(self.db)
        db_mod.upsert_articles(self.db, [
            Article(zotero_key="K1", title="Alpha RCT", authors="Smith J; Doe A", year=2019),
            Article(zotero_key="K2", title="Beta cohort", authors="Lee K", year=2021),
            Article(zotero_key="K3", title="Gamma excluded", authors="Wong M", year=2020),
            Article(zotero_key="K4", title="Delta stage2 excluded", authors="Park H", year=2018),
        ], [])
        db_mod.save_decision(self.db, "K1", "include")
        db_mod.save_decision(self.db, "K2", "include")
        db_mod.save_decision(self.db, "K3", "exclude", "wrong population")
        db_mod.save_decision(self.db, "K4", "include")
        fulltext_mod.save_stage2(self.db, "K1", "include")
        fulltext_mod.save_stage2(self.db, "K2", "include")
        fulltext_mod.save_stage2(self.db, "K4", "exclude", "no full text")
        d1 = coding_mod.add_dimension(self.db, "研究设计", "choice", ["RCT", "队列"],
                                      section="研究特征")
        d2 = coding_mod.add_dimension(self.db, "样本量", "numeric", [], section="研究特征")
        d3 = coding_mod.add_dimension(self.db, "地区", "text", [], section="研究背景")
        coding_mod.save_coding_value(self.db, d1, "K1", "RCT")
        coding_mod.save_coding_value(self.db, d2, "K1", "120")
        coding_mod.save_coding_value(self.db, d3, "K1", "北京")
        coding_mod.save_coding_value(self.db, d1, "K2", "队列")
        coding_mod.save_coding_value(self.db, d3, "K4", "上海")
        self.dims = {"设计": d1, "样本": d2, "地区": d3}
        return d1, d2, d3

    def _seed_effects(self, *, comparison="干预 vs 对照", outcome="疼痛", timepoint="12 周"):
        analysis_mod.save_study(self.db, "S1", "Study 1", ["K1"])
        analysis_mod.save_study(self.db, "S2", "Study 2", ["K2"])
        analysis_mod.save_effect(self.db, "S1", comparison, outcome, timepoint,
                                 "MD", 0.5, 0.2, "K1")
        analysis_mod.save_effect(self.db, "S2", comparison, outcome, timepoint,
                                 "MD", 0.3, 0.25, "K2")

    def _put_appraisal(self, article: str, judgement: str) -> None:
        """按存储形状写入评价记录（overall.judgement → GRADE 档位映射源）。"""
        conn = db_mod._connect(self.db)
        try:
            appraisal_core.ensure_appraisal_storage(conn)
            appraisal_core.save_record(
                conn, article, TOOL, responses={}, comments={}, variant=None,
                overall={"mode": "rob2_domains", "judgement": judgement})
            conn.commit()
        finally:
            conn.close()

    def _table1(self, **body):
        payload = {"format": "csv", "inclusion": "stage1_stage2"}
        payload.update(body)
        return self.client.post(self.table1, json=payload, headers=self.auth)

    def _sof(self, **body):
        payload = {"format": "csv"}
        payload.update(body)
        return self.client.post(self.sof, json=payload, headers=self.auth)


# ---------------------------------------------------------------------------
# 1. Table 1：与编码矩阵同口径一致 + 纳入过滤 + 字段选择
# ---------------------------------------------------------------------------

class Table1Pins(W7TablesBase):
    PREFIX = "w7t1"
    def test_csv_matches_coding_matrix_row_for_row(self):
        """同数据两出口（编码矩阵 CSV 与 Table 1 CSV）必须逐格一致。

        含**负数**（评审 r1 Core 项：公式注入防护曾把 -0.5 改写成 "'-0.5"，
        两个出口的数字因此不同）。
        """
        d1, d2, d3 = self._seed()
        coding_mod.save_coding_value(self.db, d2, "K1", "-0.5")
        coding_mod.save_coding_value(self.db, d2, "K2", "+0.5")
        matrix = self.client.get(f"/api/tasks/{self.task}/export/matrix", headers=self.auth)
        self.assertEqual(matrix.status_code, 200, matrix.text)
        response = self._table1(inclusion="matrix")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["content-type"].split(";")[0], "text/csv")
        matrix_rows = _read_csv(matrix.content)
        table_rows = _read_csv(response.content)
        self.assertEqual(table_rows, matrix_rows)
        self.assertEqual(table_rows[0], ["zotero_key", "title", "研究设计", "样本量", "地区"])
        # 负数是数值字面量：两个出口都必须是 -0.5，不得被加上 ' 前缀
        k1 = {row[0]: row for row in table_rows[1:]}["K1"]
        self.assertEqual(k1[3], "-0.5")
        # 正号数值（变化分常见 +2.3 形态）同样不得被转义（评审 r2 项）
        k2 = {row[0]: row for row in table_rows[1:]}["K2"]
        self.assertEqual(k2[3], "+0.5")

    def test_inclusion_filter_modes(self):
        """默认 = 初筛+复筛均纳入（K4 被复筛排除 → 缺行）；stage1 保留 K4；
        matrix 口径 = 编码矩阵全量（含 K4）。"""
        self._seed()
        default_rows = _read_csv(self._table1().content)
        self.assertEqual([r[0] for r in default_rows[1:]], ["K1", "K2"])

        stage1_rows = _read_csv(self._table1(inclusion="stage1").content)
        self.assertEqual([r[0] for r in stage1_rows[1:]], ["K1", "K2", "K4"])

        matrix_rows = _read_csv(self._table1(inclusion="matrix").content)
        self.assertEqual([r[0] for r in matrix_rows[1:]], ["K1", "K2", "K4"])

        # 复筛排除者的编码值绝不能借默认口径回流
        self.assertNotIn("上海", [cell for row in default_rows for cell in row])

    def test_field_selection_controls_columns_and_order(self):
        d1, d2, d3 = self._seed()
        response = self._table1(fields=[d3, "研究设计"])
        self.assertEqual(response.status_code, 200, response.text)
        rows = _read_csv(response.content)
        self.assertEqual(rows[0], ["zotero_key", "title", "地区", "研究设计"])
        # 未选字段不出现；行序仍为 import_order
        self.assertEqual(rows[1][:4], ["K1", "Alpha RCT", "北京", "RCT"])

    def test_unknown_field_is_structured_400(self):
        self._seed()
        response = self._table1(fields=["不存在的字段"])
        self.assertEqual(response.status_code, 400, response.text)
        detail = response.json()["detail"]
        self.assertEqual(detail["error_code"], "tables_field_unknown")
        self.assertEqual(detail["message_key"], "tables.field_unknown")

    def test_unknown_field_without_screener_db_is_400(self):
        """任务里还没有该筛选员的库时，任何字段名都必然是未知字段 → 400，不是
        200（评审 r1 项：原实现在无库分支提前返回，未知字段被静默接受）。"""
        peer_db = self.info.dir / (tasks_mod.slugify_screener(self.member["username"])
                                   + ".db")
        self.assertFalse(peer_db.exists(), "该用例要求该筛选员无库")
        # 成员读自己的库（自己的 screener 名是允许的；库尚未创建）
        response = self.client.post(self.table1, json={"format": "csv", "fields": ["不存在"]},
                                    params={"screener": self.member["username"]},
                                    headers=self.member_auth)
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(response.json()["detail"]["error_code"], "tables_field_unknown")
        # 同一请求不带 fields（=全部维度）时仍是合法的空表
        empty = self.client.post(self.table1, json={"format": "csv"},
                                 params={"screener": self.member["username"]},
                                 headers=self.member_auth)
        self.assertEqual(empty.status_code, 200, empty.text)
        self.assertEqual(_read_csv(empty.content), [["zotero_key", "title"]])
        self.assertFalse(peer_db.exists(), "读端点不得懒建库")

    def test_table1_detail_json_shape(self):
        """Table 1 `?detail=1` 预览形状（契约 §4.1 的实测样例同源钉）。"""
        self._seed()
        response = self.client.post(self.table1, json={"format": "csv"},
                                    params={"detail": 1}, headers=self.auth)
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(set(body), {"table", "generated_at", "inclusion", "sort",
                                     "columns", "rows", "n_rows", "notes"})
        self.assertEqual(body["table"], "characteristics")
        self.assertEqual(body["n_rows"], 2)
        self.assertEqual(set(body["columns"][0]), {"id", "name", "column", "section"})
        self.assertEqual(set(body["rows"][0]),
                         {"zotero_key", "title", "authors_year", "values"})
        self.assertEqual(body["rows"][0]["authors_year"], "Smith et al. (2019)")
        self.assertEqual(body["rows"][0]["values"]["研究设计"], "RCT")
        self.assertTrue(body["notes"][0].startswith("自动生成于"))

    def test_author_year_surname_rules(self):
        """作者-年份规范要覆盖真实存储形态（``"姓, 名"`` 分号连接）：
        单作者不得加 et al.、多作者不得留下多余逗号（评审 r1 项）。"""
        db_mod.init_db(self.db)
        db_mod.upsert_articles(self.db, [
            Article(zotero_key="A1", title="t1", authors="Smith, John; Doe, Jane", year=2020),
            Article(zotero_key="A2", title="t2", authors="Lee, Kim", year=2019),
            Article(zotero_key="A3", title="t3", authors="王小明", year=2018),
            Article(zotero_key="A4", title="Fallback title", authors="", year=2017),
            Article(zotero_key="A5", title="t5", authors="Smith J; Doe A", year=2016),
        ], [])
        for key in ("A1", "A2", "A3", "A4", "A5"):
            db_mod.save_decision(self.db, key, "include")
        response = self.client.post(self.table1, json={"format": "csv"},
                                    params={"detail": 1}, headers=self.auth)
        self.assertEqual(response.status_code, 200, response.text)
        labels = {row["zotero_key"]: row["authors_year"] for row in response.json()["rows"]}
        self.assertEqual(labels, {
            "A1": "Smith et al. (2020)",
            "A2": "Lee (2019)",
            "A3": "王小明 (2018)",
            "A4": "Fallback title (2017)",
            "A5": "Smith et al. (2016)",
        })

    def test_author_year_sort_and_format(self):
        self._seed()
        response = self._table1(sort="author_year")
        self.assertEqual(response.status_code, 200, response.text)
        rows = _read_csv(response.content)
        # Lee (2021) < Smith et al. (2019) 按姓名字典序
        self.assertEqual([r[0] for r in rows[1:]], ["K2", "K1"])

    def test_csv_formula_injection_guard(self):
        d1, d2, d3 = self._seed()
        for index, value in enumerate(("=HYPERLINK(\"http://x\")", "+1+1", "-2+3", "@SUM(A1)")):
            article = ("K1", "K2", "K4")[index % 3]
            coding_mod.save_coding_value(self.db, d3, article, value)
        rows = _read_csv(self._table1(inclusion="matrix").content)
        header = rows[0]
        column = header.index("地区")
        guarded = [row[column] for row in rows[1:] if row[column]]
        self.assertTrue(guarded, guarded)
        for value in guarded:
            self.assertTrue(value.startswith("'"), value)
            self.assertIn(value[1:2], ("=", "+", "-", "@"))


# ---------------------------------------------------------------------------
# 2. Table 1 docx：可打开、表头分组正确、表注
# ---------------------------------------------------------------------------

class Table1DocxPins(W7TablesBase):
    PREFIX = "w7t1d"
    def test_docx_opens_with_grouped_header_rows(self):
        self._seed()
        response = self._table1(format="docx")
        self.assertEqual(response.status_code, 200, response.text)
        media = response.headers["content-type"].split(";")[0]
        self.assertEqual(
            media,
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document")

        from docx import Document

        document = Document(io.BytesIO(response.content))
        self.assertTrue(document.tables, "docx 内没有表格")
        table = document.tables[0]

        # 第 0 行 = 分区表头（首列「文献」+ 研究特征 ×2 合并 + 研究背景）；
        # 第 1 行 = 列名；第 2 行起 = 数据（仅 K1/K2 纳入）。
        group = table.rows[0].cells
        self.assertEqual([cell.text for cell in group],
                         ["文献", "研究特征", "研究特征", "研究背景"])
        self.assertIs(group[1]._tc, group[2]._tc, "同分区表头单元格未合并")
        self.assertIsNot(group[0]._tc, group[1]._tc, "首列被错误并入分区")
        self.assertIsNot(group[2]._tc, group[3]._tc, "不同分区被错误合并")

        header = [cell.text for cell in table.rows[1].cells]
        self.assertEqual(header, ["作者-年份", "研究设计", "样本量", "地区"])

        self.assertEqual(len(table.rows), 4, "数据行应为 2 篇纳入文献")
        first = [cell.text for cell in table.rows[2].cells]
        self.assertEqual(first, ["Smith et al. (2019)", "RCT", "120", "北京"])
        second = [cell.text for cell in table.rows[3].cells]
        self.assertEqual(second, ["Lee (2021)", "队列", "", ""])

        text = "\n".join(paragraph.text for paragraph in document.paragraphs)
        self.assertIn("自动生成于", text)


# ---------------------------------------------------------------------------
# 3. SoF：空数据留空 + 脚注、效应口径、降级建议原样、Rob 与既有端点同源
# ---------------------------------------------------------------------------

class SofPins(W7TablesBase):
    PREFIX = "w7sof"
    def _sof_row(self, rows, outcome="疼痛"):
        header = rows[0]
        for row in rows[1:]:
            if row and row[0].startswith(outcome):
                return dict(zip(header, row))
        self.fail(f"未找到结局行：{outcome}；实际 {rows!r}")

    def test_empty_cells_are_blank_with_footnote_not_zero(self):
        self._seed()
        self._seed_effects()
        response = self._sof()
        self.assertEqual(response.status_code, 200, response.text)
        rows = _read_csv(response.content)
        header = rows[0]
        row = self._sof_row(rows)
        # 无评价数据 → 研究局限性必须为空串（不是 0，也不是臆造档位）
        self.assertEqual(row["研究局限性"], "")
        # 发表偏倚：2 项研究 → 检验功效不足，不得给出结论
        self.assertEqual(row["发表偏倚"], "")
        for column in ("研究局限性", "发表偏倚"):
            self.assertNotIn("0", row[column])
        tail = "\n".join(",".join(r) for r in rows)
        self.assertIn("未评价", tail)
        self.assertIn("研究局限性", tail)
        self.assertIn("发表偏倚", tail)
        self.assertIn(header[0], ("结局（时间点）",))

    def test_effect_matches_synthesis_report_convention(self):
        """效应格口径 = synthesize + synthesis_report 的 _display/_number 展示。"""
        self._seed()
        self._seed_effects()
        from custom_backend.synthesis_report import _number

        result = analysis_mod.synthesize(self.db, "干预 vs 对照", "疼痛", "12 周", "MD")
        expected = (f"MD {_number(result['pooled'])} "
                    f"(95% CI {_number(result['ci_low'])} to {_number(result['ci_high'])})")
        rows = _read_csv(self._sof().content)
        row = self._sof_row(rows)
        self.assertEqual(row["效应估计 (95% CI)"], expected)
        self.assertEqual(row["纳入研究数"], "2")

    def test_grade_suggestion_columns_are_verbatim_library_output(self):
        self._seed()
        self._seed_effects()
        db_rows = _read_csv(self._sof().content)
        row = self._sof_row(db_rows)
        result = analysis_mod.synthesize(self.db, "干预 vs 对照", "疼痛", "12 周", "MD")

        level, signal = grade.suggest_inconsistency_downgrade(
            {"i2_percent": result["i2_percent"], "q_p": result["q_p"]})
        self.assertIn(grade.DOWNGRADE_LABELS[level], row["不一致性"])
        self.assertIn(signal["rule"], row["不一致性"])
        self.assertIn(str(result["i2_percent"]), row["不一致性"], "数值被四舍五入")

        level, signal = grade.suggest_imprecision_downgrade(
            {"ci_low": result["ci_analysis_low"],
             "ci_high": result["ci_analysis_high"], "null_value": 0.0})
        self.assertIn(grade.DOWNGRADE_LABELS[level], row["不精确性"])
        self.assertIn(signal["rule"], row["不精确性"])

    def test_rob_column_matches_grade_input_endpoint(self):
        """研究局限性列必须与既有 GRADE 输入端点的聚合同源（最严重档）。"""
        self._seed()
        self._seed_effects()
        self._put_appraisal("K1", "high")
        self._put_appraisal("K2", "low")

        source = self.client.get(f"/api/tasks/{self.task}/appraisal/grade-input",
                                 params={"tool": TOOL}, headers=self.auth)
        self.assertEqual(source.status_code, 200, source.text)
        payload = source.json()
        self.assertTrue(payload["available"], payload)
        levels = {study["article_key"]: study["risk_of_bias"] for study in payload["studies"]}
        self.assertEqual(levels, {"K1": "serious", "K2": "low"})

        rows = _read_csv(self._sof(rob_tool=TOOL).content)
        row = self._sof_row(rows)
        self.assertIn(grade.DOWNGRADE_LABELS["serious"], row["研究局限性"])
        self.assertIn("1/2", row["研究局限性"])

        expected_level, expected_signal = grade.suggest_rob_downgrade(
            {"overall_counts": {"high": 1, "low": 1}})
        self.assertIn(grade.DOWNGRADE_LABELS[expected_level], row["研究局限性"])
        self.assertIn(expected_signal["rule"], row["研究局限性"])

    def test_overall_certainty_uses_final_quality(self):
        self._seed()
        self._seed_effects()
        result = analysis_mod.synthesize(self.db, "干预 vs 对照", "疼痛", "12 周", "MD")
        rob_level, _ = grade.suggest_rob_downgrade(None)
        inc_level, _ = grade.suggest_inconsistency_downgrade(
            {"i2_percent": result["i2_percent"], "q_p": result["q_p"]})
        imp_level, _ = grade.suggest_imprecision_downgrade(
            {"ci_low": result["ci_analysis_low"],
             "ci_high": result["ci_analysis_high"], "null_value": 0.0})
        pub_level, _ = grade.suggest_publication_bias_downgrade(None)
        expected = grade.final_quality("rct", {
            "rob": rob_level, "inconsistency": inc_level, "indirectness": "none",
            "imprecision": imp_level, "publication_bias": pub_level})
        rows = _read_csv(self._sof().content)
        row = self._sof_row(rows)
        self.assertIn(grade.GRADE_LEVELS[expected["final_quality"]], row["总体置信度"])
        self.assertIn(grade.CERTAINTY_SYMBOLS[expected["final_quality"]],
                      row["总体置信度"])

    def test_outcome_filter_and_unknown_outcome(self):
        self._seed()
        self._seed_effects()
        self._seed_effects(comparison="干预 vs 安慰剂", outcome="生活质量", timepoint="24 周")
        only = self._sof(outcomes=["疼痛"])
        self.assertEqual(only.status_code, 200, only.text)
        rows = _read_csv(only.content)
        data_rows = [row for row in rows[1:] if row and row[0] != "脚注"]
        self.assertEqual(len(data_rows), 1)
        self.assertTrue(data_rows[0][0].startswith("疼痛"))

        unknown = self._sof(outcomes=["不存在的结局"])
        self.assertEqual(unknown.status_code, 400, unknown.text)
        self.assertEqual(unknown.json()["detail"]["error_code"], "tables_outcome_unknown")

    def test_detail_json_shape(self):
        self._seed()
        self._seed_effects()
        response = self.client.post(self.sof, json={"format": "csv"},
                                    params={"detail": 1}, headers=self.auth)
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["table"], "sof")
        self.assertIn("footnotes", body)
        self.assertEqual(len(body["rows"]), 1)
        row = body["rows"][0]
        self.assertEqual(row["outcome"], "疼痛")
        self.assertEqual(row["n_studies"], 2)
        self.assertEqual(row["empty_cells"], ["rob", "publication_bias"])
        self.assertIn("grade", row["detail"])
        self.assertEqual(set(row["detail"]["grade"]["suggestions"]),
                         {"rob", "inconsistency", "imprecision", "publication_bias"})

    def test_single_study_row_leaves_certainty_blank(self):
        """单项研究行：效应格有值（该研究自身估计），但没有合成 → I²/CI/发表偏倚
        都无从评估，总体置信度必须留空 + 脚注，不得按「全构件无降级」给「高」
        （评审 r2 阻断项，探针 Q3）。"""
        self._seed()
        analysis_mod.save_study(self.db, "S1", "Study 1", ["K1"])
        analysis_mod.save_effect(self.db, "S1", "C", "O", "T", "MD", 0.5, 0.2, "K1")
        response = self._sof()
        self.assertEqual(response.status_code, 200, response.text)
        rows = _read_csv(response.content)
        row = self._sof_row(rows, outcome="O")
        self.assertTrue(row["效应估计 (95% CI)"].startswith("MD 0.5"))
        self.assertEqual(row["总体置信度"], "")
        self.assertEqual(row["研究局限性"], "")
        tail = "\n".join(",".join(r) for r in rows)
        self.assertIn("未评价（仅 1 项研究，未做 GRADE 合成评价）", tail)

        detail = self.client.post(self.sof, json={"format": "csv"},
                                  params={"detail": 1}, headers=self.auth).json()
        entry = detail["rows"][0]
        self.assertEqual(entry["n_studies"], 1)
        for key in ("certainty", "inconsistency", "imprecision",
                    "publication_bias", "rob"):
            self.assertIn(key, entry["empty_cells"])
        self.assertEqual(entry["detail"]["grade"]["quality"],
                         {"skipped": "no_synthesis"})

    def test_no_effect_means_no_invented_certainty(self):
        """没有有效应数据时，不得用「全构件按无降级」凑出一个「高」置信度
        （评审 r1 项，探针 P4/P5：效应为空但总体置信度曾显示 高 ⊕⊕⊕⊕）。"""
        self._seed()
        self._seed_effects()
        response = self._sof(ci_method="hksj")  # fixed + hksj 是既有层拒绝的组合
        self.assertEqual(response.status_code, 200, response.text)
        rows = _read_csv(response.content)
        row = self._sof_row(rows)
        self.assertEqual(row["效应估计 (95% CI)"], "")
        self.assertEqual(row["总体置信度"], "")
        tail = "\n".join(",".join(r) for r in rows)
        self.assertIn("总体置信度：未评价（无效应数据）", tail)
        # 有数据的研究数不能被写成 0
        self.assertEqual(row["纳入研究数"], "2")

    def test_rob_denominator_covers_unrated_studies(self):
        """分层有 2 项研究、只 1 项有评价：分母必须是**全部研究**（1/2）并脚注
        说明未评价者，不能写成 1/1 让读者误以为全覆盖（评审 r1 项）。"""
        self._seed()
        self._seed_effects()
        self._put_appraisal("K1", "high")
        response = self._sof(rob_tool=TOOL)
        self.assertEqual(response.status_code, 200, response.text)
        rows = _read_csv(response.content)
        row = self._sof_row(rows)
        self.assertIn("1/2 项研究", row["研究局限性"])
        tail = "\n".join(",".join(r) for r in rows)
        self.assertIn("1/2 项研究无评价记录", tail)

    def test_publication_bias_small_sample_signal_is_documented(self):
        """n<10 时既有建议器仍返回「无降级」+ ``insufficient_studies_no_auto_downgrade``
        （不自动降级）；契约 §3.2 必须照此描述，而不是「空 + 脚注」。"""
        from custom_backend.tables_api import _publication_bias_signal

        rows = [{"study_id": f"S{index}", "estimate": 0.1 * (index + 1),
                 "se": 0.1 * (index + 1)} for index in range(4)]
        level, signal = _publication_bias_signal(rows, "MD", 4)
        self.assertEqual(level, "none")
        self.assertTrue(signal["available"])
        self.assertEqual(signal["rule"], "insufficient_studies_no_auto_downgrade")

    def test_selection_conflict_degrades_to_blank_not_500(self):
        """两项已选结果被登记为同一 cluster/sample（样本重叠）→ 既有合成层拒绝
        合并；表必须留空 + 脚注，绝不 500、绝不挑其中一份冒充合并结果。"""
        from contextlib import closing

        from coscreen.analysis_storage import set_effect_design

        self._seed()
        self._seed_effects()
        with closing(db_mod._connect(self.db)) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            ids = {effect["study_id"]: effect["result_id"]
                   for effect in analysis_mod.list_effects(self.db)}
            for result_id in ids.values():
                set_effect_design(conn, result_id, "CL-1", "SAMPLE-1", "同一原始样本")

        response = self._sof()
        self.assertEqual(response.status_code, 200, response.text)
        rows = _read_csv(response.content)
        row = self._sof_row(rows, outcome="疼痛")
        self.assertEqual(row["效应估计 (95% CI)"], "")
        # 样本重叠时既不能合并、也不能编一个总体置信度
        self.assertEqual(row["总体置信度"], "")
        self.assertEqual(row["纳入研究数"], "2")
        tail = "\n".join(",".join(r) for r in rows)
        self.assertIn("效应选择异常", tail)

    def test_sof_docx_opens_with_footnotes(self):
        self._seed()
        self._seed_effects()
        response = self._sof(format="docx")
        self.assertEqual(response.status_code, 200, response.text)
        from docx import Document

        document = Document(io.BytesIO(response.content))
        self.assertTrue(document.tables)
        table = document.tables[0]
        # 第 0 行三段列组：结局与效应 ×3 / GRADE 降级构件 ×4 / 总体置信度 ×1
        group = table.rows[0].cells
        self.assertEqual([cell.text for cell in group],
                         ["结局与效应"] * 3 + ["GRADE 降级构件"] * 4 + ["总体置信度"])
        self.assertIs(group[0]._tc, group[2]._tc)
        self.assertIs(group[3]._tc, group[6]._tc)
        self.assertIsNot(group[2]._tc, group[3]._tc)
        self.assertIsNot(group[6]._tc, group[7]._tc)
        header = [cell.text for cell in table.rows[1].cells]
        self.assertEqual(header[0], "结局（时间点）")
        self.assertEqual(len(header), 8)
        text = "\n".join(paragraph.text for paragraph in document.paragraphs)
        self.assertIn("自动生成于", text)


# ---------------------------------------------------------------------------
# 4. 权限
# ---------------------------------------------------------------------------

class PermissionPins(W7TablesBase):
    PREFIX = "w7perm"
    def test_non_member_is_403(self):
        self._seed()
        for path, body in ((self.table1, {"format": "csv"}),
                           (self.sof, {"format": "csv"})):
            response = self.client.post(path, json=body, headers=self.outsider_auth)
            self.assertEqual(response.status_code, 403, response.text)

    def test_unknown_task_is_404(self):
        response = self.client.post("/api/tasks/do-not-exist/tables/characteristics",
                                    json={"format": "csv"}, headers=self.auth)
        self.assertEqual(response.status_code, 404, response.text)


def tearDownModule():
    """整个模块跑完再清理临时数据目录（混跑时 DATA_DIR 可能由本模块设置）。"""
    fixture.cleanup()


if __name__ == "__main__":
    unittest.main()
