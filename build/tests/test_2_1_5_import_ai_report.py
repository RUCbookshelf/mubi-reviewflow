# -*- coding: utf-8 -*-
"""2.1.5 aiux 波 B/C · 导入复核 AI 助手审计导出 + 记分卡（专属新文件，撤实现必红）。

B ``GET /api/tasks/{task_id}/import-review/ai-assist/audit/export?format=csv|json``：
   CSV 固定列 + MM-05 公式注入防护（=+-@\\t\\r 开头补 '）+ attachment 头；
   JSON 完整行（含 rationale_zh/warnings）；format 非法 → 422 结构化；
   batch_id 可选过滤。
C ``GET /api/tasks/{task_id}/import-review/ai-assist/scorecard``：
   AI-人工一致率——same↔duplicate / different↔not_duplicate 记 agree、
   else disagree、AI unsure 单列不进分母、无样本 rate=null；
   metadata_diff 人工侧由提案 action 派生；merge_conflict 只做字段级一致
   （field_picks vs human_final，口径复用 _audit_view 留痕派生）。

覆盖（每条在实现前按现状为红）：

1. 空库 scorecard（null rate）与空导出（CSV 仅表头）。
2. 种子 3 条（同向 2 + 反向 1）一致率 2/3；unsure 不进分母；pending /
   undecided 候选不算人工最终判定。
3. metadata_diff 三种 action 的同/异侧映射与一致率。
4. merge_conflict 字段级一致（含 AI unsure 字段不进字段分母、未执行组不计）。
5. CSV 转义：值以 ``=`` / ``+`` / Tab 开头补 ``'``；列头与 executed 派生列。
6. 非任务成员 403；format 非法 422；未知任务 404。
7. 导出不含 API key 字样：钥匙永不入库，钉响应无钥匙材料 / 无 .ai_keys。
8. merge 行 undone 派生（留痕全撤销 → undone=true）。
"""

from __future__ import annotations

import csv as csv_mod
import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

fixture = tempfile.TemporaryDirectory(prefix="rf_215r_import_ai_report_")
os.environ["COBOOKSHELF_DATA_DIR"] = fixture.name
os.environ["REVIEWFLOW_MODE"] = "server"

from fastapi.testclient import TestClient  # noqa: E402

from coscreen import db as db_mod  # noqa: E402
from coscreen import review_imports as store  # noqa: E402
from coscreen import review_workflow as wf  # noqa: E402
from coscreen import tasks as tasks_mod  # noqa: E402
from custom_backend import ai_keys  # noqa: E402
from custom_backend import auth as auth_mod  # noqa: E402
from custom_backend import import_ai_assist_api  # noqa: E402
from custom_backend.main import DATA_DIR, app  # noqa: E402

FAKE_KEY = "sk-import-report-abcdef123456"
DATA_ROOT = Path(DATA_DIR)

#: merge_conflict 审计载荷（field_picks_json 存量形状：keeper + fields）。
MERGE_PAYLOAD = {
    "keeper": {"keeper_key": None, "keeper_record_id": "r1",
               "reasons": [], "source": "request"},
    "fields": [
        {"field": "journal", "left": "J Old", "right": "J New",
         "right_record_id": "r2", "prefill": "right", "source": "ai"},
        {"field": "title", "left": "Title A", "right": "Title B",
         "right_record_id": "r2", "prefill": "left", "source": "ai"},
        {"field": "abstract", "left": "Abs A", "right": "Abs B",
         "right_record_id": "r2", "prefill": None, "source": None},
    ],
}

CSV_HEADER = ["audit_id", "batch_id", "item_kind", "item_id", "verdict",
              "confidence", "model", "model_kind", "provider",
              "prompt_version", "status", "executed", "undone", "created_at"]


class ImportAIReportApiTests(unittest.TestCase):
    @classmethod
    def _auth(cls, index=0):
        return {"Authorization": "Bearer " + cls.users[index]["token"]}

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.client.__enter__()
        cls.users = []
        for i in range(3):
            user = auth_mod.create_user(f"r215r{i}@example.test", "R215R-Report-2026")
            cls.users.append({"user_id": user["id"], "username": user["username"],
                              "token": auth_mod.create_access_token(user["id"], user["username"])})
        # 门禁墙钟（类级模板库，同 test_2_1_5_import_ai_assist 手法）：模板任务
        # 只含来源/批次静态种子；每条用例复制后按需补条目。
        cls._slug = tasks_mod.slugify_screener(cls.users[0]["username"])
        template = cls.client.post("/api/tasks", json={"name": "import-ai-report-tpl"},
                                   headers=cls._auth(0)).json()["task_id"]
        ctx0 = app.state.review_workflow.context(template, cls.users[0])
        db_mod.init_db(ctx0.owner_db)
        store.migrate(ctx0.owner_db)
        with closing(wf.connect(ctx0.owner_db)) as conn, conn:
            conn.execute("INSERT INTO workflow_sources VALUES(?,?,?,?,?)",
                         ("s", "01-Wiley.ris", "01-wiley.ris", 1, "now"))
            conn.execute("INSERT INTO workflow_import_batches VALUES(?,?,?,?,?,?,?)",
                         ("b", str(cls.users[0]["user_id"]), "h", "batch", 1, "staged", "now"))
        cls._template_db_bytes = Path(ctx0.owner_db).read_bytes()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        fixture.cleanup()

    def setUp(self):
        os.environ.pop("REVIEWFLOW_AI_KEY", None)
        for user in self.users:
            ai_keys.delete_personal_key(user["user_id"], DATA_ROOT)
        self.task = self.client.post("/api/tasks", json={"name": self._testMethodName},
                                     headers=self._auth(0)).json()["task_id"]
        info = tasks_mod.find_task(self.task, DATA_DIR)
        self.db = info.dir / (self._slug + ".db")
        Path(self.db).write_bytes(self._template_db_bytes)
        self.base = f"/api/tasks/{self.task}/import-review/ai-assist"

    # -- 种子 -------------------------------------------------------------

    def seed_candidate(self, cid: str, status: str = "pending") -> None:
        with closing(wf.connect(self.db)) as conn, conn:
            conn.execute(
                "INSERT INTO workflow_import_candidates(id,signature,kind,status,"
                "created_at,updated_at) VALUES(?,?,?,?,?,?)",
                (cid, cid, "title", status, "now", "now"))

    def seed_metadata(self, rid: str, action: str | None) -> None:
        """来源记录 + 元数据提案（action=人工最终动作；None=待处理）。"""
        article = json.dumps({"title": f"Synthetic {rid}", "authors": "Doe J",
                              "year": 2024, "journal": "J Test"}, ensure_ascii=False)
        with closing(wf.connect(self.db)) as conn, conn:
            conn.execute(
                "INSERT INTO workflow_source_records VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (rid, "s", rid, 1, "01-Wiley.ris", article, article,
                 rid, rid, None, None, "new", "staged", "now"))
            conn.execute(
                "INSERT INTO workflow_import_metadata(record_id,canonical_key,"
                "before_json,incoming_json,action) VALUES(?,?,?,?,?)",
                (rid, "EXIST", article, article, action))

    def seed_audit(self, *, kind: str, item_id: str, verdict: str = "same",
                   batch_id: str = "b", model: str = "probe-model",
                   model_kind: str = "llm", status: str = "ok",
                   confidence: float = 0.9, rationale_zh: str = "题录一致。",
                   field_picks=None, warnings=None) -> int:
        """直接落一行审计（唯一索引键 request_digest 由种子参数派生）。"""
        digest = hashlib.sha256(
            f"{kind}:{item_id}:{verdict}:{model}:{batch_id}".encode("utf-8")).hexdigest()
        picks_json = (json.dumps(field_picks, ensure_ascii=False)
                      if field_picks is not None else None)
        warn_json = json.dumps(warnings or [], ensure_ascii=False)
        with closing(wf.connect(self.db)) as conn, conn:
            import_ai_assist_api._ensure_audit_schema(conn)
            conn.execute(
                "INSERT INTO import_ai_suggestions(task_id,batch_id,item_kind,item_id,"
                "provider,model,model_kind,prompt_version,request_digest,verdict,"
                "confidence,rationale_zh,field_picks_json,warnings_json,status,"
                "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (self.task, batch_id, kind, item_id, "openai", model, model_kind,
                 "import-ai-assist/v1", digest, verdict, confidence, rationale_zh,
                 picks_json, warn_json, status, "2026-11-12T00:00:00+00:00",
                 "2026-11-12T00:00:00+00:00"))
            return int(conn.execute(
                "SELECT id FROM import_ai_suggestions WHERE request_digest=?",
                (digest,)).fetchone()[0])

    def seed_trail(self, audit_id: int, *, field: str, source_record_id: str | None,
                   candidate_id: str = "c1", applied: int = 1, undone: int = 0) -> None:
        """merge_conflict 的逐字段留痕（ai_suggestion_id=审计行 id 字符串）。"""
        with closing(wf.connect(self.db)) as conn, conn:
            conn.execute(
                "INSERT INTO workflow_import_field_merges(batch_id,candidate_id,"
                "keeper_kind,keeper_ref,field,before_value,after_value,"
                "source_record_id,decision_source,applied,conflict,ai_suggestion_id,"
                "actor_id,created_at,undone) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("b", candidate_id, "record", "r1", field, "before", "after",
                 source_record_id, "human", applied, 1, str(audit_id), "u", "now",
                 undone))

    def export(self, *, fmt: str, batch_id: str | None = None, user_index: int = 0):
        params = {"format": fmt}
        if batch_id is not None:
            params["batch_id"] = batch_id
        return self.client.get(f"{self.base}/audit/export", params=params,
                               headers=self._auth(user_index))

    def scorecard(self, user_index: int = 0):
        return self.client.get(f"{self.base}/scorecard",
                               headers=self._auth(user_index))

    @staticmethod
    def csv_rows(response) -> list[list[str]]:
        return list(csv_mod.reader(io.StringIO(
            response.content.decode("utf-8-sig"))))

    # -- 1. 空库 -----------------------------------------------------------

    def test_empty_task_scorecard_is_null_rate_and_export_header_only(self):
        card = self.scorecard()
        self.assertEqual(card.status_code, 200, card.text)
        body = card.json()
        self.assertEqual(body["task_id"], self.task)
        self.assertEqual((body["total_compared"], body["agree"], body["disagree"],
                          body["ai_unsure"]), (0, 0, 0, 0))
        self.assertIsNone(body["agreement_rate"])
        for kind in ("duplicate_group", "metadata_diff"):
            self.assertIsNone(body["by_kind"][kind]["agreement_rate"])
            self.assertEqual(body["by_kind"][kind]["total_compared"], 0)
        self.assertEqual(body["by_kind"]["merge_conflict"],
                         {"fields_compared": 0, "fields_agree": 0,
                          "field_agreement_rate": None})
        exported = self.export(fmt="csv")
        self.assertEqual(exported.status_code, 200, exported.text)
        rows = self.csv_rows(exported)
        self.assertEqual(rows, [CSV_HEADER])

    # -- 2. duplicate_group 一致率 ------------------------------------------

    def test_scorecard_agreement_two_of_three_and_unsure_excluded(self):
        # 同向 2：same↔duplicate、different↔not_duplicate。
        self.seed_candidate("c1", "duplicate")
        self.seed_audit(kind="duplicate_group", item_id="c1", verdict="same")
        self.seed_candidate("c2", "not_duplicate")
        self.seed_audit(kind="duplicate_group", item_id="c2", verdict="different")
        # 反向 1：same 但人工判非重复。
        self.seed_candidate("c3", "duplicate")
        self.seed_audit(kind="duplicate_group", item_id="c3", verdict="different")
        # unsure：有人工最终判定但 AI 弃权 → 单列，不进分母。
        self.seed_candidate("c4", "duplicate")
        self.seed_audit(kind="duplicate_group", item_id="c4", verdict="unsure")
        # 无人工最终判定（pending / undecided）→ 不进样本。
        self.seed_candidate("c5", "pending")
        self.seed_audit(kind="duplicate_group", item_id="c5", verdict="same")
        self.seed_candidate("c6", "undecided")
        self.seed_audit(kind="duplicate_group", item_id="c6", verdict="same")

        body = self.scorecard().json()
        group = body["by_kind"]["duplicate_group"]
        self.assertEqual((group["total_compared"], group["agree"],
                          group["disagree"], group["ai_unsure"]), (3, 2, 1, 1))
        self.assertAlmostEqual(group["agreement_rate"], 2 / 3)
        self.assertEqual((body["total_compared"], body["agree"], body["disagree"],
                          body["ai_unsure"]), (3, 2, 1, 1))
        self.assertAlmostEqual(body["agreement_rate"], 2 / 3)
        self.assertEqual(body["by_kind"]["metadata_diff"]["total_compared"], 0)

    # -- 3. metadata_diff 人工侧映射 ----------------------------------------

    def test_scorecard_metadata_diff_action_mapping(self):
        self.seed_metadata("m1", "keep_existing")
        self.seed_audit(kind="metadata_diff", item_id="m1", verdict="different")
        self.seed_metadata("m2", "accept_incoming")
        self.seed_audit(kind="metadata_diff", item_id="m2", verdict="same")
        self.seed_metadata("m3", "field_picks")
        self.seed_audit(kind="metadata_diff", item_id="m3", verdict="same")
        self.seed_metadata("m4", "keep_existing")
        self.seed_audit(kind="metadata_diff", item_id="m4", verdict="same")
        self.seed_metadata("m5", None)  # 未判定 → 不进样本
        self.seed_audit(kind="metadata_diff", item_id="m5", verdict="same")

        body = self.scorecard().json()
        meta = body["by_kind"]["metadata_diff"]
        self.assertEqual((meta["total_compared"], meta["agree"],
                          meta["disagree"]), (4, 3, 1))
        self.assertAlmostEqual(meta["agreement_rate"], 0.75)
        self.assertEqual(body["by_kind"]["duplicate_group"]["total_compared"], 0)

    # -- 4. merge_conflict 字段级一致 ---------------------------------------

    def test_scorecard_merge_conflict_field_agreement(self):
        # 已执行组（候选判定 duplicate）：journal AI=right 人工=r2 → agree；
        # title AI=left 人工=r2 → disagree；abstract AI unsure → 不进字段分母。
        self.seed_candidate("c1", "duplicate")
        audit_id = self.seed_audit(kind="merge_conflict", item_id="c1",
                                   verdict="n/a", field_picks=MERGE_PAYLOAD)
        self.seed_trail(audit_id, field="journal", source_record_id="r2")
        self.seed_trail(audit_id, field="title", source_record_id="r2")
        # 未执行组（候选仍待处理）：留痕在也不计。
        self.seed_candidate("c2", "pending")
        pending_id = self.seed_audit(kind="merge_conflict", item_id="c2",
                                     verdict="n/a", field_picks=MERGE_PAYLOAD)
        self.seed_trail(pending_id, field="journal", source_record_id="r2",
                        candidate_id="c2")

        body = self.scorecard().json()
        merge = body["by_kind"]["merge_conflict"]
        self.assertEqual(merge, {"fields_compared": 2, "fields_agree": 1,
                                 "field_agreement_rate": 0.5})
        # merge 不进 verdict 级一致率：顶层仍无样本（null rate）。
        self.assertEqual(body["total_compared"], 0)
        self.assertIsNone(body["agreement_rate"])

    # -- 5. CSV 转义与列形 ---------------------------------------------------

    def test_export_csv_escapes_formula_cells(self):
        self.seed_candidate("c1", "duplicate")
        self.seed_audit(kind="duplicate_group", item_id="c1", verdict="same",
                        model="=SUM(A1)", rationale_zh="+1+1 注入尝试")
        self.seed_audit(kind="duplicate_group", item_id="=HYPERLINK(\"x\")",
                        verdict="same", batch_id="b2")
        self.seed_audit(kind="duplicate_group", item_id="c9", verdict="same",
                        batch_id="b3", model="+INJECT")
        exported = self.export(fmt="csv")
        self.assertEqual(exported.status_code, 200, exported.text)
        self.assertIn("attachment", exported.headers["content-disposition"])
        self.assertIn("import-ai-assist-audit.csv",
                      exported.headers["content-disposition"])
        self.assertTrue(exported.content.startswith(b"\xef\xbb\xbf"))  # BOM
        rows = self.csv_rows(exported)
        self.assertEqual(rows[0], CSV_HEADER)
        self.assertEqual(len(rows), 4)
        first = dict(zip(CSV_HEADER, rows[1]))
        self.assertEqual(first["model"], "'=SUM(A1)")
        self.assertEqual(first["executed"], "true")
        self.assertEqual(first["undone"], "false")
        # CSV 不含 rationale_zh 列（完整行在 JSON）：理由里的公式串不得外漏。
        self.assertNotIn("+1+1", exported.text)
        second = dict(zip(CSV_HEADER, rows[2]))
        self.assertEqual(second["item_id"], "'=HYPERLINK(\"x\")")
        self.assertEqual(second["executed"], "false")  # 无该候选 → 未执行
        third = dict(zip(CSV_HEADER, rows[3]))
        self.assertEqual(third["model"], "'+INJECT")

    def test_export_csv_tab_then_formula_is_escaped(self):
        # MM-05 同款判定是 lstrip 后看首字符："\t=公式"（Tab 后跟公式符）
        # 必须补 '；原始字节完整保留。
        self.seed_candidate("c1", "duplicate")
        self.seed_audit(kind="duplicate_group", item_id="c1", verdict="same",
                        model="\t=SUM(B1)")
        rows = self.csv_rows(self.export(fmt="csv"))
        first = dict(zip(CSV_HEADER, rows[1]))
        self.assertEqual(first["model"], "'\t=SUM(B1)")

    def test_export_merge_row_csv_verdict_empty_and_undone_flag(self):
        # 候选已判 duplicate + 留痕全部撤销 → executed=true / undone=true，
        # 且 merge 行 verdict 列为空（n/a 占位不出口，与 audit GET 同口径）。
        self.seed_candidate("c1", "duplicate")
        audit_id = self.seed_audit(kind="merge_conflict", item_id="c1",
                                   verdict="n/a", field_picks=MERGE_PAYLOAD)
        self.seed_trail(audit_id, field="journal", source_record_id="r2",
                        applied=1, undone=1)
        rows = self.csv_rows(self.export(fmt="csv"))
        first = dict(zip(CSV_HEADER, rows[1]))
        self.assertEqual(first["verdict"], "")
        self.assertEqual(first["executed"], "true")
        self.assertEqual(first["undone"], "true")

    # -- 6. JSON 完整行 + batch 过滤 ----------------------------------------

    def test_export_json_full_rows_and_batch_filter(self):
        self.seed_candidate("c1", "duplicate")
        self.seed_audit(kind="duplicate_group", item_id="c1", verdict="same",
                        batch_id="b1", rationale_zh="理由甲",
                        field_picks={"journal": "right"}, warnings=["low_confidence"])
        self.seed_metadata("m1", "keep_existing")
        self.seed_audit(kind="metadata_diff", item_id="m1", verdict="different",
                        batch_id="b2", rationale_zh="理由乙")
        exported = self.export(fmt="json", batch_id="b1")
        self.assertEqual(exported.status_code, 200, exported.text)
        self.assertIn("attachment", exported.headers["content-disposition"])
        self.assertIn("import-ai-assist-audit.json",
                      exported.headers["content-disposition"])
        body = exported.json()
        self.assertEqual(body["count"], 1)
        row = body["rows"][0]
        self.assertEqual(row["item_kind"], "duplicate_group")
        self.assertEqual(row["verdict"], "same")
        self.assertEqual(row["rationale_zh"], "理由甲")
        self.assertEqual(row["warnings"], ["low_confidence"])
        self.assertEqual(row["field_picks"], {"journal": "right"})
        self.assertTrue(row["executed"])
        self.assertFalse(row["undone"])
        self.assertEqual(row["candidate_status"], "duplicate")
        self.assertEqual(row["model_kind"], "llm")
        # 不过滤 → 两行；merge 行附逐字段视图键。
        full = self.export(fmt="json").json()
        self.assertEqual(full["count"], 2)
        # CSV 同过滤只剩一行数据。
        csv_rows = self.csv_rows(self.export(fmt="csv", batch_id="b1"))
        self.assertEqual(len(csv_rows), 2)

    def test_export_json_merge_row_carries_fields(self):
        self.seed_candidate("c1", "duplicate")
        audit_id = self.seed_audit(kind="merge_conflict", item_id="c1",
                                   verdict="n/a", field_picks=MERGE_PAYLOAD)
        self.seed_trail(audit_id, field="journal", source_record_id="r2")
        row = self.export(fmt="json").json()["rows"][0]
        self.assertIsNone(row["verdict"])
        self.assertTrue(row["executed"])
        fields = {item["field"]: item for item in row["fields"]}
        self.assertEqual(fields["journal"]["human_final"], "r2")

    # -- 7. 权限 / 参数错误 --------------------------------------------------

    def test_non_member_is_403_on_both_endpoints(self):
        self.seed_candidate("c1", "duplicate")
        self.seed_audit(kind="duplicate_group", item_id="c1", verdict="same")
        self.assertEqual(self.export(fmt="csv", user_index=2).status_code, 403)
        self.assertEqual(self.export(fmt="json", user_index=2).status_code, 403)
        self.assertEqual(self.scorecard(user_index=2).status_code, 403)

    def test_invalid_format_is_422_structured(self):
        response = self.export(fmt="xml")
        self.assertEqual(response.status_code, 422, response.text)
        detail = response.json()["detail"]
        self.assertEqual(detail["code"], "import_ai_report_format_invalid")
        self.assertEqual(detail["message_key"], "import_ai_report.format_invalid")
        self.assertEqual(detail["params"]["allowed"], ["csv", "json"])
        self.assertEqual(detail["params"]["format"], "xml")
        # 缺省 format 同样按非法处理。
        missing = self.client.get(f"{self.base}/audit/export",
                                  headers=self._auth(0))
        self.assertEqual(missing.status_code, 422, missing.text)
        self.assertEqual(missing.json()["detail"]["code"],
                         "import_ai_report_format_invalid")

    def test_unknown_task_is_404(self):
        self.assertEqual(
            self.client.get("/api/tasks/nope/import-review/ai-assist/scorecard",
                            headers=self._auth(0)).status_code, 404)
        self.assertEqual(
            self.client.get("/api/tasks/nope/import-review/ai-assist/audit/export",
                            params={"format": "csv"},
                            headers=self._auth(0)).status_code, 404)

    # -- 8. 钥匙永不泄露 ----------------------------------------------------

    def test_export_never_leaks_key_material(self):
        # 钥匙柜真实落盘后导出：审计表本就无钥匙列，响应亦不得出现钥匙
        # 材料或 .ai_keys 槽文件痕迹（任务名不含敏感词，避免自污染断言）。
        saved = self.client.put(
            "/api/ai/key",
            json={"provider": "openai", "key": FAKE_KEY, "model": "probe-model"},
            headers=self._auth(0))
        self.assertEqual(saved.status_code, 200, saved.text)
        self.seed_candidate("c1", "duplicate")
        self.seed_audit(kind="duplicate_group", item_id="c1", verdict="same")
        for fmt in ("csv", "json"):
            response = self.export(fmt=fmt)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertNotIn(FAKE_KEY, response.text)
            self.assertNotIn(".ai_keys", response.text)
            self.assertNotIn("api_key", response.text)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
