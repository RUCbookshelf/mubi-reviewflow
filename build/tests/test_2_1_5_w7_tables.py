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
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

fixture = tempfile.TemporaryDirectory(prefix="rf_w7tab_")
os.environ["COBOOKSHELF_DATA_DIR"] = fixture.name
os.environ["REVIEWFLOW_MODE"] = "server"

from fastapi import HTTPException  # noqa: E402
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
from custom_backend.tables_api import _consensus_gate  # noqa: E402

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
        # 终审 #01（旧→新）：风险档位改用专门的风险量表（低/严重/非常严重），
        # 不再用降级级别标签表索引——旧期望是 assertIn("严重(-1)")（降级语义
        # 文案恰好同名），新期望为风险语义「严重（1/2 项研究）」打头。
        self.assertTrue(row["研究局限性"].startswith("严重（1/2 项研究）"),
                        row["研究局限性"])
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


# ---------------------------------------------------------------------------
# 5. I-19 / I-20：带 rob_tool 的 SoF 权限 = grade-input 同一判定 + 工具校验
# ---------------------------------------------------------------------------

class ConsensusPermissionPins(W7TablesBase):
    """I-19（权限漏洞）：研究局限性列跨全员个人库聚合评价档位，因此带
    ``rob_tool`` 的 SoF 必须与 ``GET /appraisal/grade-input`` **同一判定**：
    属主或带 ``consensus:write`` 的裁决员。普通成员带 ``rob_tool`` → 结构化
    403 ``tables.rob_requires_consensus``，响应中不得出现任何档位/计数。

    不带 ``rob_tool`` 的 SoF 与 Table 1 仍对可见成员开放，且不含任何评价数据。
    """

    PREFIX = "w7cons"

    def _member_library(self, article="K1"):
        path = self.info.dir / (tasks_mod.slugify_screener(self.member["username"])
                                + ".db")
        db_mod.init_db(path)
        db_mod.upsert_articles(path, [
            Article(zotero_key=article, title="Member study", authors="",
                    year=2020)], [])
        analysis_mod.save_study(path, "S1", "Study 1", [article])
        analysis_mod.save_effect(path, "S1", "C", "O", "T", "MD", 0.5, 0.2, article)
        return path

    def _owner_rating(self):
        self._seed()
        self._seed_effects()
        self._put_appraisal("K1", "high")   # high → GRADE serious

    def _grant_reconciler(self):
        response = self.client.post(
            f"/api/tasks/{self.task}/collaboration/roles",
            json={"user_id": self.member["id"], "role": "reconciler"},
            headers=self.auth)
        self.assertEqual(response.status_code, 201, response.text)

    def _member_sof(self, *, detail=0, **body):
        payload = {"format": "csv"}
        payload.update(body)
        return self.client.post(
            self.sof, json=payload,
            params={"screener": self.member["username"], "detail": detail},
            headers=self.member_auth)

    def _rob_cell(self, rows):
        header = rows[0]
        for row in rows[1:]:
            if row and row[0] != "脚注":
                return dict(zip(header, row))["研究局限性"]
        self.fail(f"未找到数据行：{rows!r}")

    def test_member_with_rob_tool_is_403_and_leaks_no_levels(self):
        self._owner_rating()
        self._member_library()
        for detail in (0, 1):
            response = self._member_sof(detail=detail, rob_tool=TOOL)
            self.assertEqual(response.status_code, 403, response.text)
            body = response.json()["detail"]
            self.assertEqual(body["error_code"], "tables_rob_requires_consensus")
            self.assertEqual(body["message_key"], "tables.rob_requires_consensus")
            # 响应体不得出现任何评价档位/计数（含 params 之外的一切）
            for forbidden in ("levels", "counts", "risk_of_bias", "serious",
                              "very_serious", "judgement"):
                self.assertNotIn(forbidden, response.text, forbidden)

    def test_owner_with_rob_tool_gets_levels(self):
        self._owner_rating()
        response = self._sof(rob_tool=TOOL)
        self.assertEqual(response.status_code, 200, response.text)
        cell = self._rob_cell(_read_csv(response.content))
        # 终审 #01（旧→新）：风险档位打头标签由降级语义「严重(-1)」改为
        # 风险语义「严重（1/2 项研究）」（K1 已评价 serious、K2 未评价）。
        self.assertTrue(cell.startswith("严重（1/2 项研究）"), cell)
        # 与 grade-input 同源：档位聚合计数 = {serious: 1}
        detail = self.client.post(
            self.sof, json={"format": "csv", "rob_tool": TOOL},
            params={"detail": 1}, headers=self.auth)
        self.assertEqual(detail.status_code, 200, detail.text)
        rob = detail.json()["rows"][0]["detail"]["rob"]
        self.assertEqual(rob["counts"], {"serious": 1})
        self.assertEqual(rob["tool"], TOOL)

    def test_adjudicator_with_consensus_write_gets_levels(self):
        """裁决员（consensus:write）与属主同权：可读跨成员聚合档位。"""
        self._owner_rating()
        self._member_library()
        self._grant_reconciler()
        response = self._member_sof(rob_tool=TOOL)
        self.assertEqual(response.status_code, 200, response.text)
        cell = self._rob_cell(_read_csv(response.content))
        # 档位来自属主库的 appraisal（跨成员聚合），证明判定通过且聚合正常。
        # 终审 #01（旧→新）：风险语义「严重（1/1 项研究）」打头（裁决员读
        # 自己的分析库 = 1 项研究，其中 1 项有跨库聚合档位）。
        self.assertTrue(cell.startswith("严重（1/1 项研究）"), cell)

    def test_member_without_rob_tool_still_reads_and_has_no_appraisal_data(self):
        self._owner_rating()
        self._member_library()
        response = self._member_sof(detail=1)
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertTrue(body["rows"], body)
        row = body["rows"][0]
        self.assertEqual(row["cells"]["rob"], "")
        self.assertEqual(row["detail"]["rob"]["counts"], {})
        self.assertNotIn("levels", json.dumps(row["detail"]["rob"]))
        self.assertIn("未选择评价工具", "\n".join(body["footnotes"]))
        # 成员仍可打开 Table 1（不含任何评价数据）
        table = self.client.post(self.table1, json={"format": "csv"},
                                 params={"screener": self.member["username"]},
                                 headers=self.member_auth)
        self.assertEqual(table.status_code, 200, table.text)
        self.assertNotIn("serious", table.text)

    def test_unknown_rob_tool_is_structured_400(self):
        self._seed()
        response = self._sof(rob_tool="no-such-tool")
        self.assertEqual(response.status_code, 400, response.text)
        detail = response.json()["detail"]
        self.assertEqual(detail["error_code"], "tables_rob_tool_unknown")
        self.assertEqual(detail["message_key"], "tables.rob_tool_unknown")
        self.assertEqual(detail["params"]["tool"], "no-such-tool")

    def test_link_only_rob_tool_is_structured_400(self):
        """link_only 工具没有条目模板（评价 §0），不能作为研究局限性来源。"""
        link_only = appraisal_core.LINK_ONLY[0]["id"]
        self._seed()
        response = self._sof(rob_tool=link_only)
        self.assertEqual(response.status_code, 400, response.text)
        detail = response.json()["detail"]
        self.assertEqual(detail["error_code"], "tables_rob_tool_link_only")
        self.assertEqual(detail["message_key"], "tables.rob_tool_link_only")
        self.assertEqual(detail["params"]["tool"], link_only)


class ConsensusGateInjectionPins(W7TablesBase):
    """2.1.5 收尾清债 C1：``_consensus_gate`` 必须用**注册期注入**的宿主判定
    函数（``register_tables_routes(..., arbitration_store=...)``），请求期
    ``from custom_backend.main import _arbitration_store`` 仅作为未注入
    （``None``）时的兜底。

    背景：旧实现只能在请求期从宿主模块取函数对象（注册区当时冻结）；现在
    注册区已解冻，注入是显式依赖、可被测试替换，兜底 import 只服务独立
    注册（未传参）的调用方。若实现退回「永远请求期导入」，钉 1 必红；
    若注入参数被移除（兜底 import 也删掉），钉 2/钉 3 必红。
    """

    PREFIX = "w7inj"

    @staticmethod
    def _boom(task_id, user):
        raise AssertionError("请求期导入路径被使用（应走注册期注入）")

    def test_sof_gate_uses_registration_time_injection_not_request_time_import(self):
        """钉 1（先红）：端点侧必须吃注册期注入的函数对象。

        打桩 ``custom_backend.main._arbitration_store``：若 tables 仍在请求期
        ``import`` 取该属性，成员的 rob_tool SoF 会撞桩（AssertionError →
        500）；注入路径捕获的是注册时的原函数对象，桩不可见 → 正常结构化
        403 ``tables_rob_requires_consensus``。
        """
        self._seed()  # 权限闸在校验工具/读库之前：无成员库也能到闸
        with mock.patch("custom_backend.main._arbitration_store", self._boom):
            response = self.client.post(
                self.sof, json={"format": "csv", "rob_tool": TOOL},
                headers=self.member_auth)
        self.assertEqual(response.status_code, 403, response.text)
        detail = response.json()["detail"]
        self.assertEqual(detail["error_code"], "tables_rob_requires_consensus")

    def test_injected_sentinel_is_called_and_403_is_mapped(self):
        """钉 2：注入函数被调用（参数原样），其 403 映射为 tables 词条，
        且全程不触发宿主模块属性（请求期兜底不启用）。"""
        calls = []

        def sentinel(task_id, user):
            calls.append((task_id, user))
            raise HTTPException(403, "blocked-by-injected-sentinel")

        with mock.patch("custom_backend.main._arbitration_store", self._boom):
            with self.assertRaises(HTTPException) as caught:
                _consensus_gate(self.task, self.member, sentinel)
        self.assertEqual(calls, [(self.task, self.member)])
        self.assertEqual(caught.exception.status_code, 403)
        body = caught.exception.detail
        self.assertEqual(body["error_code"], "tables_rob_requires_consensus")
        self.assertEqual(body["message_key"], "tables.rob_requires_consensus")

    def test_none_argument_falls_back_to_request_time_import(self):
        """钉 3：``arbitration_store=None``（未注入）保留请求期导入兜底。"""
        calls = []

        def recorder(task_id, user):
            calls.append((task_id, user))
            return Path("sentinel-store.db")

        with mock.patch("custom_backend.main._arbitration_store", recorder):
            _consensus_gate(self.task, self.member)  # 不注入
        self.assertEqual(calls, [(self.task, self.member)])


# ---------------------------------------------------------------------------
# 6. 终审 2.1.5 第 2 波「SoF/GRADE 计算正确性」红钉（#01/#46/#55/#08）
# ---------------------------------------------------------------------------

class SofGradeCorrectnessPins(W7TablesBase):
    """sweep_2.1.5_report.md §二 #01/#46/#55/#08：直接影响研究产出数字
    （SoF 导出与 grade-input）的四类计算错误，逐条带「修复前必红」的数值锚。"""

    PREFIX = "w7sec3"

    def _sof_row(self, rows, outcome="疼痛"):
        header = rows[0]
        for row in rows[1:]:
            if row and row[0].startswith(outcome):
                return dict(zip(header, row))
        self.fail(f"未找到结局行：{outcome}；实际 {rows!r}")

    def _detail(self, **body):
        payload = {"format": "csv"}
        payload.update(body)
        response = self.client.post(self.sof, json=payload,
                                    params={"detail": 1}, headers=self.auth)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_all_low_risk_band_renders_three_outlets(self):
        """#01：全低风险数据（worst='low'）三出口全 200。

        旧：``grade.DOWNGRADE_LABELS['low']`` KeyError → csv/docx/detail=1
        全 500。新（旧→新）：风险档位改用专门量表——all-low 格 =
        「低（2/2 项研究）｜无降级｜rule=no_high_risk_studies…」；此前
        serious/very_serious 恰好同名时显示降级语义文案「严重(-1)（x/y 项
        研究）」，一并校正为风险语义。
        """
        self._seed()
        self._seed_effects()
        self._put_appraisal("K1", "low")
        self._put_appraisal("K2", "some_concerns")
        for fmt in ("csv", "docx"):
            response = self._sof(format=fmt, rob_tool=TOOL)
            self.assertEqual(response.status_code, 200,
                             f"{fmt} 出口不得 500：{response.text[:200]}")
        payload = self._detail(rob_tool=TOOL)
        row = payload["rows"][0]
        rob_detail = row["detail"]["rob"]
        self.assertEqual(rob_detail["worst"], "low")
        self.assertEqual(rob_detail["counts"], {"low": 2})
        self.assertTrue(row["cells"]["rob"].startswith("低（2/2 项研究）"),
                        row["cells"]["rob"])
        self.assertIn("rule=no_high_risk_studies", row["cells"]["rob"])
        # 风险档位格不得再以降级级别文案打头（两套量表分家）
        self.assertFalse(row["cells"]["rob"].startswith(("严重", "非常严重")))
        # 数值锚：全构件无降级（rob=low 不降级；CI [0.116, 0.728] 不含 0 且
        # 宽度比 6.3 < 10；n=2 不判发表偏倚）→ RCT 起点 → 高 ⊕⊕⊕⊕。
        self.assertEqual(row["cells"]["certainty"], "高 ⊕⊕⊕⊕")
        csv_row = self._sof_row(_read_csv(self._sof(rob_tool=TOOL).content))
        self.assertTrue(csv_row["研究局限性"].startswith("低（2/2 项研究）"),
                        csv_row["研究局限性"])

    def test_imprecision_ratio_measures_use_display_scale(self):
        """#46：不精确性「同号上下限比>10」必须用**展示尺度** CI（与
        ``/analysis/grade/auto-suggest`` 同口径）。比值类（OR）的分析尺度是
        log 值，下限逼近 0 时比值急剧放大，不是尺度不变的量。

        方向一（旧→新）：OR 1.449（95%CI ≈ 1.05–2.00，log 尺度比 ≈ 14）
        曾误判 ci_too_wide、置信度「中」→ 现无降级、「高 ⊕⊕⊕⊕」；
        方向二（旧→新）：OR 展示尺度 CI ≈ 20–400（log 尺度比 ≈ 2）曾漏判
        ci_too_wide → 现降级、「中 ⊕⊕⊕○」。两方向与 auto-suggest 一致。
        """
        self._seed()
        analysis_mod.save_study(self.db, "S1", "Study 1", ["K1"])
        analysis_mod.save_study(self.db, "S2", "Study 2", ["K2"])
        for outcome, (estimate, se) in {"O_narrow": (1.449, 0.2324),
                                        "O_wide": (89.4427, 1.0805)}.items():
            for sid, key in (("S1", "K1"), ("S2", "K2")):
                analysis_mod.save_effect(self.db, sid, "C", outcome, "T",
                                         "OR", estimate, se, key)

        payload = self._detail()
        rows = {row["outcome"]: row for row in payload["rows"]}
        expectations = {"O_narrow": (False, "高 ⊕⊕⊕⊕"),
                        "O_wide": (True, "中 ⊕⊕⊕○")}
        for outcome, (expect_wide, expect_certainty) in expectations.items():
            row = rows[outcome]
            result = analysis_mod.synthesize(self.db, "C", outcome, "T", "OR")
            display_ratio = result["ci_high"] / result["ci_low"]
            signal = row["detail"]["grade"]["suggestions"]["imprecision"]["signal"]
            # 数值锚：signal 的宽度比必须是展示尺度比值（不是 log 尺度）
            self.assertAlmostEqual(signal["ci_width_ratio"], display_ratio,
                                   places=6, msg=outcome)
            self.assertIs("ci_too_wide" in str(signal.get("rule")), expect_wide,
                          f"{outcome}: {signal}")
            self.assertEqual(row["cells"]["certainty"], expect_certainty, outcome)
            # 同分层两出口（SoF vs auto-suggest）结论一致
            auto = self.client.get(
                f"/api/tasks/{self.task}/analysis/grade/auto-suggest",
                params={"comparison": "C", "outcome": outcome, "timepoint": "T",
                        "initial_design": "rct", "model": "fixed"},
                headers=self.auth)
            self.assertEqual(auto.status_code, 200, auto.text)
            suggestion = auto.json()
            self.assertIn(suggestion["final_quality_label"] + " ",
                          row["cells"]["certainty"], outcome)
            self.assertEqual(
                suggestion["signal_sources"]["imprecision"].get("rule"),
                signal.get("rule"), outcome)

    def test_unselected_variant_studies_stay_in_denominator(self):
        """#55：分层下仅有未选变体（selected=0，整包导入可达）的研究不得从
        研究数/研究局限性分母消失。

        旧→新：S3（双变体全未选）+ S4（已选）曾导出「纳入研究数 1」、
        「严重(-1)（1/1 项研究）」→ 现为 2、「严重（1/2 项研究）」，
        counts={low:1, serious:1}（S3 的评价记录参与汇总）。
        """
        from contextlib import closing

        self._seed()
        analysis_mod.save_study(self.db, "S3", "Study 3", ["K3"])
        analysis_mod.save_study(self.db, "S4", "Study 4", ["K4"])
        analysis_mod.save_effect(self.db, "S3", "干预 vs 对照", "疼痛", "12 周",
                                 "MD", 0.2, 0.1, "K3")
        analysis_mod.save_effect(self.db, "S3", "干预 vs 对照", "疼痛", "12 周",
                                 "MD", 0.4, 0.2, "K3", append=True)
        with closing(db_mod._connect(self.db)) as conn, conn:
            conn.execute("UPDATE review_effects SET selected=0 WHERE study_id='S3'")
        analysis_mod.save_effect(self.db, "S4", "干预 vs 对照", "疼痛", "12 周",
                                 "MD", 0.6, 0.25, "K4")
        self._put_appraisal("K3", "low")
        self._put_appraisal("K4", "high")

        payload = self._detail(rob_tool=TOOL)
        row = payload["rows"][0]
        # 数值锚：分母 = 本分层全部研究（S3+S4 = 2），两研究评价都计入
        self.assertEqual(row["n_studies"], 2)
        rob_detail = row["detail"]["rob"]
        self.assertEqual(rob_detail["counts"], {"low": 1, "serious": 1})
        self.assertEqual(rob_detail["n_studies"], 2)
        self.assertEqual(rob_detail["n_rated"], 2)
        self.assertEqual(rob_detail["worst"], "serious")
        self.assertTrue(row["cells"]["rob"].startswith("严重（1/2 项研究）"),
                        row["cells"]["rob"])
        # 选择异常仍如实披露：效应格留空 + 脚注，绝不冒充合并结果
        self.assertEqual(row["cells"]["effect"], "")
        self.assertIn("效应选择异常", "\n".join(row["footnotes"]))
        csv_row = self._sof_row(_read_csv(self._sof(rob_tool=TOOL).content))
        self.assertEqual(csv_row["纳入研究数"], "2")
        self.assertTrue(csv_row["研究局限性"].startswith("严重（1/2 项研究）"))

    def test_purely_unselected_stratum_row_is_kept(self):
        """#55（同根因更强形态）：整层只有未选变体时结局行不得被整行吞掉、
        全局脚注不得误写「还没有已保存的分析条目」。"""
        from contextlib import closing

        self._seed()
        analysis_mod.save_study(self.db, "S5", "Study 5", ["K4"])
        analysis_mod.save_effect(self.db, "S5", "c", "o", "t", "MD", 0.2, 0.1, "K4")
        analysis_mod.save_effect(self.db, "S5", "c", "o", "t", "MD", 0.4, 0.2,
                                 "K4", append=True)
        with closing(db_mod._connect(self.db)) as conn, conn:
            conn.execute("UPDATE review_effects SET selected=0 WHERE study_id='S5'")

        payload = self._detail()
        self.assertEqual(len(payload["rows"]), 1, payload["rows"])
        row = payload["rows"][0]
        self.assertEqual(row["outcome"], "o")
        self.assertEqual(row["n_studies"], 1)
        self.assertEqual(row["cells"]["effect"], "")
        self.assertTrue(any("效应选择异常" in note or "未选定" in note
                            for note in row["footnotes"]), row["footnotes"])
        self.assertNotIn("还没有已保存的分析条目", "\n".join(payload["footnotes"]))

    def test_rob_tool_whitespace_aggregates_as_canonical_id(self):
        """#08：rob_tool 带首尾空白必须与规范 id 聚合结果逐字段相等。

        旧→新：'rob2 ' 曾通过校验（get_tool 内部 strip）但聚合按未规范化
        原串命中 0 条 → 研究局限性空、总体置信度被静默上调为「高 ⊕⊕⊕⊕」；
        现端点使用注册表规范 id，带空白请求的响应与 'rob2' 逐字段相等。
        """
        self._seed()
        self._seed_effects()
        self._put_appraisal("K1", "high")
        self._put_appraisal("K2", "high")

        baseline = self._detail(rob_tool=TOOL)
        base_row = baseline["rows"][0]
        # 规范 id 基线（数值锚）：counts={serious:2}、置信度 低 ⊕⊕○○
        self.assertEqual(base_row["detail"]["rob"]["counts"], {"serious": 2})
        self.assertEqual(base_row["cells"]["certainty"], "低 ⊕⊕○○")
        self.assertEqual(baseline["params"]["rob_tool"], TOOL)

        for tool in ("rob2 ", " rob2", "rob2\t"):
            payload = self._detail(rob_tool=tool)
            self.assertEqual(payload["params"]["rob_tool"], TOOL, tool)
            self.assertEqual(payload["rows"], baseline["rows"],
                             f"rob_tool={tool!r} 与规范 id 逐字段不等")
            self.assertNotEqual(payload["rows"][0]["cells"]["rob"], "")

        # CSV 出口同口径：带空白的总体置信度不得被上调
        spaced = self._sof_row(_read_csv(self._sof(rob_tool="rob2 ").content))
        canonical = self._sof_row(_read_csv(self._sof(rob_tool=TOOL).content))
        self.assertEqual(spaced["总体置信度"], canonical["总体置信度"])
        self.assertEqual(spaced["研究局限性"], canonical["研究局限性"])
        self.assertNotEqual(spaced["总体置信度"], "高 ⊕⊕⊕⊕")


# ---------------------------------------------------------------------------
# 终审 #09：Unicode 数字串 fields（'²'.isdigit()=True 但 int() 抛 ValueError）
# ---------------------------------------------------------------------------
def _fullwidth_digits(value: int) -> str:
    """123 → '１２３'（int() 可解析的全角数字串）。"""
    return str(value).translate(
        {ord(d): chr(ord(d) + 0xFEE0) for d in "0123456789"})


class UnicodeDigitFieldPins(W7TablesBase):
    """上标/全角数字串不再 ValueError → 500；未命中一律 400 tables_field_unknown。"""

    PREFIX = "w7uni"

    def test_superscript_digit_string_is_structured_400(self):
        self._seed()
        # '²'.isdigit() 为 True、int('²') 抛 ValueError——修复前直接 500。
        response = self._table1(fields=["²"])
        self.assertEqual(response.status_code, 400, response.text)
        detail = response.json()["detail"]
        self.assertEqual(detail["error_code"], "tables_field_unknown")
        self.assertEqual(detail["message_key"], "tables.field_unknown")
        self.assertEqual(detail["params"]["field"], "²")
        # 混合串 '2²' 同形状（修复前同样 500）。
        response = self._table1(fields=["2²"])
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(response.json()["detail"]["error_code"],
                         "tables_field_unknown")

    def test_fullwidth_digits_parse_as_id_when_present_else_400(self):
        d1, d2, d3 = self._seed()
        # 全角数字可被 int() 解析 → 按值查 id：存在即 200（语义保持，含在
        # 列输出里），不存在 → 400 tables_field_unknown（不 500）。
        response = self._table1(fields=[_fullwidth_digits(int(d2))])
        self.assertEqual(response.status_code, 200, response.text)
        rows = _read_csv(response.content)
        self.assertTrue(any("样本" in cell for cell in rows[0]),
                        f"全角 id 应选中「样本量」维度：{rows[0]}")
        missing = _fullwidth_digits(max(int(d1), int(d2), int(d3)) + 1)
        response = self._table1(fields=[missing])
        self.assertEqual(response.status_code, 400, response.text)
        detail = response.json()["detail"]
        self.assertEqual(detail["error_code"], "tables_field_unknown")
        self.assertEqual(detail["params"]["field"], missing)
        # detail=1 出口同口径（同走 _resolve_fields）。
        response = self.client.post(
            f"{self.table1}?detail=1", json={"fields": ["²"], "inclusion": "matrix"},
            headers=self.auth)
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(response.json()["detail"]["error_code"],
                         "tables_field_unknown")


# ---------------------------------------------------------------------------
# 终审 #05：个人库 sqlite 故障（外部持锁/损坏）→ 503 既有词条，不落全局 500
# ---------------------------------------------------------------------------
class LibraryLockDegradationPins(W7TablesBase):
    """Table 1 / SoF / export·matrix 三读端点的库故障统一 503（含顺带修）。"""

    PREFIX = "w7lock"

    def test_sqlite_failure_maps_to_503_library_unavailable(self):
        from custom_backend import main as main_mod
        from custom_backend import tables_api

        self._seed()
        self._seed_effects()
        message = main_mod.SCREENER_LIBRARY_UNAVAILABLE_MESSAGE
        locked = sqlite3.OperationalError("database is locked")

        with mock.patch.object(tables_api, "_table1_payload", side_effect=locked):
            response = self._table1()
            self.assertEqual(response.status_code, 503, response.text)
            self.assertEqual(response.json()["detail"], message)
            detail_response = self.client.post(
                f"{self.table1}?detail=1",
                json={"format": "csv", "inclusion": "stage1_stage2"},
                headers=self.auth)
            self.assertEqual(detail_response.status_code, 503, detail_response.text)
            self.assertEqual(detail_response.json()["detail"], message)

        with mock.patch.object(tables_api, "_sof_rows", side_effect=locked):
            response = self._sof()
            self.assertEqual(response.status_code, 503, response.text)
            self.assertEqual(response.json()["detail"], message)

        # 同源既有 500（报告标注 preexisting，顺带修）：/export/matrix。
        with mock.patch.object(main_mod, "export_coding_matrix", side_effect=locked):
            response = self.client.get(
                f"/api/tasks/{self.task}/export/matrix", headers=self.auth)
            self.assertEqual(response.status_code, 503, response.text)
            self.assertEqual(response.json()["detail"], message)

        # 库恢复后同请求回到 200（故障源移除即恢复，无残留状态）。
        self.assertEqual(self._table1().status_code, 200)
        self.assertEqual(self._sof().status_code, 200)
        self.assertEqual(
            self.client.get(f"/api/tasks/{self.task}/export/matrix",
                            headers=self.auth).status_code, 200)

    def test_corrupt_database_error_also_structured_503(self):
        self._seed()
        from custom_backend import tables_api

        with mock.patch.object(
                tables_api, "_table1_payload",
                side_effect=sqlite3.DatabaseError("file is not a database")):
            response = self._table1()
            self.assertEqual(response.status_code, 503, response.text)

    def test_programming_error_is_never_swallowed_as_503(self):
        """复审 r2（#05 收窄）：ProgrammingError 是调用写法缺陷，不得吞成
        503「库不可用」——先例 coscreen/screening_arbitration.py:498/:520
        （``except sqlite3.ProgrammingError: raise``）。缺陷照常上抛可见。
        """
        from custom_backend import main as main_mod
        from custom_backend import tables_api

        self._seed()
        self._seed_effects()
        message = main_mod.SCREENER_LIBRARY_UNAVAILABLE_MESSAGE
        defect = sqlite3.ProgrammingError("Cannot operate on a closed database")
        with mock.patch.object(tables_api, "_table1_payload", side_effect=defect):
            table1 = self._table1()
        with mock.patch.object(tables_api, "_sof_rows", side_effect=defect):
            sof = self._sof()
        with mock.patch.object(main_mod, "export_coding_matrix", side_effect=defect):
            matrix = self.client.get(
                f"/api/tasks/{self.task}/export/matrix", headers=self.auth)
        for label, response in (("Table 1", table1), ("SoF", sof),
                                ("export/matrix", matrix)):
            with self.subTest(endpoint=label):
                self.assertEqual(response.status_code, 500, response.text)
                self.assertNotEqual(response.json().get("detail"), message,
                                    "ProgrammingError 不得被映射成库不可用 503")


def tearDownModule():
    """整个模块跑完再清理临时数据目录（混跑时 DATA_DIR 可能由本模块设置）。"""
    fixture.cleanup()


if __name__ == "__main__":
    unittest.main()
