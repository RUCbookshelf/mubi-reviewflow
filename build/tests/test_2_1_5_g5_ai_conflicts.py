# -*- coding: utf-8 -*-
"""2.1.5 G5 第二段「AI 冲突建议」（merge_conflict）红钉（专属新文件，撤实现必红）。

权威设计：``out/release_plan/drafts/design_2.1.5_dedup_merge_quality.md``
（§七「执行决议」优先）+ 本波指令（B6 去掉决策模型、预填语义、I-6）。
第一段「合并规则与撤销」的冻结接口见
``frontend_contract_2.1.5-g5-merge-rules.md``（conflict_picks / ai_suggestion_id）。

覆盖（每条在实现前按现状为红）：

1. 确定性预判层四规则（零出站）：DOI 规范化后相等→取规范书写的一侧；
   题名忽略大小写相等→取非全大写；作者一侧 "et al." 且第一作者一致→取全列；
   摘要一侧是另一侧前缀且以省略号/半句截断→取完整者。
2. 零出站：全部冲突都被规则层判定时，模型通道一次都不调用。
3. 出站载荷负钉：只含六字段白名单里**处于冲突**的字段（每项仅
   ``field/left/right``）；url、item_type、全文、笔记、筛选决定、非冲突字段值
   一个字符都不外发。
4. 非法返回降级：值不在 {left,right,unsure} 的字段丢弃按 unsure
   （warnings 含 ``field_picks_invalid``）。
5. 低置信（<0.6）→ AI 层全部 unsure；规则层判定不受模型置信影响。
6. unsure 回落保留值：``prefill=null`` + ``fallback="default"``，且不进入
   ``suggested_conflict_picks``（执行时缺省=保留条目值）。
7. "是不是重复"仍只高亮不预选（负钉）：``duplicate_group`` 建议不带
   ``field_picks`` / 预填 / ``suggested_conflict_picks``。
8. 本端点只读：生成建议前后候选状态、保留条目题录、留痕表都不变。
9. 审计三栏：审计导出逐字段给 ``rule_pick`` / ``ai_pick`` / ``human_final``
   （``human_final`` 由第一段留痕表经 ``ai_suggestion_id`` 关联得出）。
10. I-6：本模块每类结构化错误都带 ``message_key`` 与 ``params``
    （``code`` / ``detail`` / ``guidance`` 兼容保留）。
11. 存储可加幂等：既有 ``import_ai_suggestions``（旧 CHECK 约束）升级后保留
    旧行并可写入 ``merge_conflict`` 行。
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import threading
import unittest
from contextlib import closing
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

fixture = tempfile.TemporaryDirectory(prefix="rf_215g5_ai_")
os.environ["COBOOKSHELF_DATA_DIR"] = fixture.name
os.environ["REVIEWFLOW_MODE"] = "server"

from fastapi.testclient import TestClient  # noqa: E402

from coscreen import db as db_mod  # noqa: E402
from coscreen import review_imports as store  # noqa: E402
from coscreen import review_workflow as wf  # noqa: E402
from coscreen import task_collaboration  # noqa: E402
from coscreen import tasks as tasks_mod  # noqa: E402
from custom_backend import ai_keys  # noqa: E402
from custom_backend import ai_suggest_api  # noqa: E402
from custom_backend import auth as auth_mod  # noqa: E402
from custom_backend import import_ai_assist_api as assist  # noqa: E402
from custom_backend.main import DATA_DIR, app  # noqa: E402

FAKE_KEY = "sk-import-conflicts-abcdef123456"
DATA_ROOT = Path(DATA_DIR)

#: 埋在 raw/url 里、绝不许随出站载荷离开服务端的哨兵串。
SECRET_NOTES = "SECRET-NOTES-DELTA"
SECRET_FULLTEXT = "SECRET-FULLTEXT-EPSILON"
SECRET_DECISION = "SECRET-SCREEN-DECISION-ZETA"

ALLOWED_KEYS = {"title", "authors", "year", "journal", "doi", "abstract"}


class _ModelProbe(BaseHTTPRequestHandler):
    """本地 OpenAI 兼容模型端点探针（不打真网）。"""

    replies: list[str] = []
    received: list[dict] = []

    def do_POST(self):  # noqa: N802  BaseHTTPRequestHandler 约定
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        body = json.loads(raw.decode("utf-8", "replace") or "{}")
        type(self).received.append({
            "json": body,
            "raw": raw.decode("utf-8", "replace"),
            "content": [m.get("content") for m in body.get("messages") or []],
        })
        reply = type(self).replies.pop(0) if type(self).replies else "{}"
        payload = json.dumps({
            "model": body.get("model") or "probe-model",
            "choices": [{"message": {"content": reply}}],
        }, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):  # noqa: A003  静默探针日志
        return


def _reply(picks: dict, confidence=0.9) -> str:
    return json.dumps({"field_picks": picks, "confidence": confidence,
                       "rationale_zh": "按信息完整度选择。"}, ensure_ascii=False)


def _article(**kw) -> dict:
    base = {"zotero_key": "", "item_type": "", "title": "", "authors": "",
            "journal": "", "year": "", "doi": "", "abstract": "", "url": "",
            "source_format": "ris", "raw_json": json.dumps({
                "notes": SECRET_NOTES, "fulltext": SECRET_FULLTEXT,
                "screening_decision": SECRET_DECISION}),
            "content_hash": ""}
    base.update(kw)
    return base


# ---------------------------------------------------------------------------
# 1. 确定性预判层（纯函数，不依赖数据库）
# ---------------------------------------------------------------------------

class RuleLayerTests(unittest.TestCase):
    def fields(self, keeper, *merged):
        members = [{"record_id": rid, "article": art} for rid, art in merged]
        return {row["field"]: row
                for row in assist.merge_conflict_fields(keeper, members)}

    def test_doi_normalized_rule_takes_canonical_side(self):
        rows = self.fields(
            {"doi": "https://doi.org/10.1000/ABC"},
            ("R1", {"doi": "10.1000/abc"}))
        self.assertEqual(rows["doi"]["conflict_kind"], "two_sided")
        self.assertEqual(rows["doi"]["rule_pick"], "right",
                         "DOI 规范化后相等 → 取规范书写的一侧（此处为非 URL 形式）")
        # 反向：保留条目书写规范 → 取保留条目。
        rows = self.fields({"doi": "10.1000/abc"}, ("R1", {"doi": "doi:10.1000/ABC"}))
        self.assertEqual(rows["doi"]["rule_pick"], "left")

    def test_title_case_rule_takes_non_all_caps(self):
        rows = self.fields({"title": "TEACHER IDENTITY STUDY"},
                           ("R1", {"title": "Teacher Identity Study"}))
        self.assertEqual(rows["title"]["rule_pick"], "right")
        rows = self.fields({"title": "Teacher Identity Study"},
                           ("R1", {"title": "TEACHER IDENTITY STUDY"}))
        self.assertEqual(rows["title"]["rule_pick"], "left")
        # 大小写不同且都是正常大小写 → 不是本规则能判的（留给 AI）。
        rows = self.fields({"title": "A Study Of Teachers"},
                           ("R1", {"title": "A study of teachers"}))
        self.assertIsNone(rows["title"]["rule_pick"])

    def test_authors_et_al_rule_takes_full_list(self):
        rows = self.fields(
            {"authors": "Rivera, Ana; Chen, Mei; Park, Jiyoung"},
            ("R1", {"authors": "Rivera, Ana et al."}))
        self.assertEqual(rows["authors"]["rule_pick"], "left")
        # 第一作者不一致 → 不适用（留给 AI）。
        rows = self.fields({"authors": "Rivera, Ana; Chen, Mei"},
                           ("R1", {"authors": "Chen, Mei et al."}))
        self.assertIsNone(rows["authors"]["rule_pick"])
        # 两侧都是 et al. → 不适用。
        rows = self.fields({"authors": "Rivera, Ana et al."},
                           ("R1", {"authors": "Rivera, Ana et al."}))
        self.assertNotIn("authors", rows)

    def test_abstract_prefix_truncation_rule_takes_complete(self):
        rows = self.fields(
            {"abstract": "Background: the trial compared X..."},
            ("R1", {"abstract": "Background: the trial compared X and Y in 2020. "
                                "Results showed Z."}))
        self.assertEqual(rows["abstract"]["rule_pick"], "right")
        # 半句截断（无收尾标点）同样按截断处理。
        rows = self.fields(
            {"abstract": "Background: the trial compared X and Y in"},
            ("R1", {"abstract": "Background: the trial compared X and Y in 2020."}))
        self.assertEqual(rows["abstract"]["rule_pick"], "right")
        # 不是前缀关系 → 留给 AI。
        rows = self.fields({"abstract": "Completely different abstract A."},
                           ("R1", {"abstract": "Completely different abstract B."}))
        self.assertIsNone(rows["abstract"]["rule_pick"])

    def test_identical_values_are_not_conflicts(self):
        rows = self.fields({"title": "Same title", "year": 2020},
                           ("R1", {"title": "Same title", "year": 2020}))
        self.assertEqual(rows, {})

    def test_member_conflict_uses_completeness_then_import_order(self):
        rows = self.fields(
            {"abstract": None},
            ("R1", {"abstract": "First abstract", "journal": None}),
            ("R2", {"abstract": "Second abstract", "journal": "J Two"}))
        self.assertEqual(rows["abstract"]["conflict_kind"], "member_conflict")
        # 完整度降序：R2 多一个期刊 → 先取 R2。
        self.assertEqual(rows["abstract"]["right_record_id"], "R2")
        self.assertEqual(rows["abstract"]["rule_pick"], "right")

    def test_url_and_item_type_are_never_rule_decided(self):
        rows = self.fields(
            {"url": "http://example.test/keeper", "item_type": "journalArticle"},
            ("R1", {"url": "http://example.test/merged", "item_type": "report"}))
        self.assertIsNone(rows["url"]["rule_pick"])
        self.assertIsNone(rows["item_type"]["rule_pick"])


# ---------------------------------------------------------------------------
# API 夹具（模块级共享：DATA_DIR 在 import main 时绑定，只建一次）
# ---------------------------------------------------------------------------

_ENV: dict = {}


def setUpModule():
    client = TestClient(app)
    client.__enter__()
    users = []
    for i in range(3):
        user = auth_mod.create_user(f"r215g5ai{i}@example.test",
                                    "R215G5-AI-Conflicts-2026")
        users.append({"user_id": user["id"], "username": user["username"],
                      "token": auth_mod.create_access_token(
                          user["id"], user["username"])})
    slug = tasks_mod.slugify_screener(users[0]["username"])
    template = client.post("/api/tasks", json={"name": "g5-ai-template"},
                           headers={"Authorization": "Bearer " + users[0]["token"]}
                           ).json()["task_id"]
    ctx0 = app.state.review_workflow.context(template, users[0])
    db_mod.init_db(ctx0.owner_db)
    store.migrate(ctx0.owner_db)
    with closing(wf.connect(ctx0.owner_db)) as conn, conn:
        conn.execute("INSERT INTO workflow_sources VALUES(?,?,?,?,?)",
                     ("s", "01-Wiley.ris", "01-wiley.ris", 1, "now"))
        conn.execute("INSERT INTO workflow_import_batches VALUES(?,?,?,?,?,?,?)",
                     ("b", str(users[0]["user_id"]), "h", "batch", 1,
                      "staged", "now"))
        conn.execute("INSERT INTO workflow_import_files VALUES(?,?,?,?,?)",
                     ("b", 0, "01-Wiley.ris", "d", "s"))
    _ENV.update(client=client, users=users, slug=slug,
                template_bytes=Path(ctx0.owner_db).read_bytes())


def tearDownModule():
    _ENV["client"].__exit__(None, None, None)
    fixture.cleanup()


class _ApiCase(unittest.TestCase):
    @classmethod
    def _auth(cls, index=0):
        return {"Authorization": "Bearer " + cls.users[index]["token"]}

    @classmethod
    def setUpClass(cls):
        cls.client = _ENV["client"]
        cls.users = _ENV["users"]
        cls._slug = _ENV["slug"]
        cls._template_db_bytes = _ENV["template_bytes"]

    def setUp(self):
        os.environ.pop("REVIEWFLOW_AI_KEY", None)
        for user in self.users:
            ai_keys.delete_personal_key(user["user_id"], DATA_ROOT)
        _ModelProbe.received = []
        _ModelProbe.replies = []
        self.task = self.client.post("/api/tasks", json={"name": self._testMethodName},
                                     headers=self._auth(0)).json()["task_id"]
        info = tasks_mod.find_task(self.task, DATA_DIR)
        self.info = info
        self.db = info.dir / (self._slug + ".db")
        Path(self.db).write_bytes(self._template_db_bytes)
        self.base = f"/api/tasks/{self.task}/import-review/ai-assist"
        self.imports = f"/api/tasks/{self.task}/import-batches/b"

    # -- 种子 -------------------------------------------------------------

    def add_active(self, key, **fields):
        article = _article(zotero_key=key, **fields)
        with closing(wf.connect(self.db)) as conn, conn:
            conn.execute(
                "INSERT INTO articles(zotero_key,item_type,title,authors,journal,year,"
                "doi,abstract,url,import_order) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (key, article["item_type"], article["title"], article["authors"],
                 article["journal"] or None, article["year"] or None,
                 article["doi"] or None, article["abstract"] or None,
                 article["url"], 1))

    def add_record(self, rid, row_index=1, **fields):
        article = _article(zotero_key=rid, **fields)
        with closing(wf.connect(self.db)) as conn, conn:
            conn.execute(
                "INSERT INTO workflow_source_records VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (rid, "s", rid, row_index, "01-Wiley.ris", json.dumps(article),
                 json.dumps(article), rid, rid, None, None, "new", "staged",
                 "2026-01-01T00:00:0%d+00:00" % row_index))
            conn.execute("INSERT INTO workflow_import_members VALUES(?,?,?)",
                         ("b", rid, 0))

    def add_group(self, cid, refs, kind="title", status="pending"):
        with closing(wf.connect(self.db)) as conn, conn:
            conn.execute(
                "INSERT INTO workflow_import_candidates(id,signature,kind,status,"
                "created_at,updated_at) VALUES(?,?,?,?,?,?)",
                (cid, cid, kind, status, "now", "now"))
            for ref_type, ref_id in refs:
                conn.execute("INSERT INTO workflow_import_candidate_members VALUES(?,?,?)",
                             (cid, ref_type, ref_id))

    def seed_two_sided(self, cid="c1"):
        """保留者（已入任务）+ 一条被合并记录：题录冲突覆盖规则层与 AI 层。"""
        self.add_active("K1", title="Keeper concise title",
                        authors="Rivera, Ana; Chen, Mei",
                        journal="Journal of Testing", year=2023,
                        doi="https://doi.org/10.1000/ABC",
                        abstract="Same abstract text.",
                        url="http://example.test/keeper",
                        item_type="journalArticle")
        self.add_record("R1", title="Merged fuller title about teachers",
                        authors="Rivera, Ana; Chen, Mei; Park, J",
                        journal="TESTING QUARTERLY", year=2024,
                        doi="10.1000/abc", abstract="Same abstract text.",
                        url="http://example.test/merged", item_type="report")
        self.add_group(cid, [("active", "K1"), ("record", "R1")])

    def seed_rule_only(self, cid="r1"):
        """所有冲突都能被规则层判定（DOI / 题名大小写 / 作者 et al. / 摘要截断）。"""
        self.add_active("K1", title="TEACHER IDENTITY STUDY",
                        authors="Rivera, Ana; Chen, Mei; Park, Jiyoung",
                        doi="https://doi.org/10.1000/ABC",
                        abstract="Background: the trial compared X...")
        self.add_record("R1", title="Teacher Identity Study",
                        authors="Rivera, Ana et al.", doi="10.1000/abc",
                        abstract="Background: the trial compared X and Y in 2020. "
                                 "Results showed Z.")
        self.add_group(cid, [("active", "K1"), ("record", "R1")])

    # -- 工具 -------------------------------------------------------------

    def probe(self) -> str:
        server = HTTPServer(("127.0.0.1", 0), _ModelProbe)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f"http://127.0.0.1:{server.server_port}/v1"

    def save_key(self, base_url=None, user_index=0):
        body = {"provider": "openai", "key": FAKE_KEY, "model": "probe-model"}
        if base_url:
            body["base_url"] = base_url
        response = self.client.put("/api/ai/key", json=body,
                                   headers=self._auth(user_index))
        self.assertEqual(response.status_code, 200, response.text)

    def enable(self, user_index=0):
        settings = self.client.get(f"{self.base}/settings",
                                   headers=self._auth(user_index)).json()
        response = self.client.put(
            f"{self.base}/settings",
            json={"expected_version": settings["version"], "enabled": True},
            headers=self._auth(user_index))
        self.assertEqual(response.status_code, 200, response.text)

    def ready(self, base_url=None):
        if base_url:
            self.save_key(base_url)
        else:
            self.save_key()
        self.enable()

    def ask(self, items, user_index=0):
        return self.client.post(self.base, json={"items": items},
                                headers=self._auth(user_index))

    @staticmethod
    def item(item_id="c1", kind="merge_conflict", **extra):
        return {"batch_id": "b", "kind": kind, "item_id": item_id, **extra}

    def suggestion(self, response, index=0):
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["suggestions"][index]

    def candidate_status(self, cid="c1"):
        with closing(wf.connect(self.db, read_only=True)) as conn:
            return conn.execute("SELECT status FROM workflow_import_candidates "
                                "WHERE id=?", (cid,)).fetchone()[0]

    def audit_rows(self):
        with closing(wf.connect(self.db, read_only=True)) as conn:
            return [dict(row) for row in conn.execute(
                "SELECT * FROM import_ai_suggestions ORDER BY id")]

    def trail_rows(self):
        with closing(wf.connect(self.db, read_only=True)) as conn:
            return [dict(row) for row in conn.execute(
                "SELECT * FROM workflow_import_field_merges ORDER BY id")]

    def policy_version(self):
        with closing(wf.connect(self.db, read_only=True)) as conn:
            return conn.execute("SELECT version FROM workflow_state WHERE singleton=1"
                                ).fetchone()[0]


# ---------------------------------------------------------------------------
# 2. 零出站 + 规则层 source
# ---------------------------------------------------------------------------

class ZeroOutboundRuleTests(_ApiCase):
    def test_rule_only_conflicts_never_call_provider(self):
        self.seed_rule_only()
        guard = mock.patch.object(ai_suggest_api, "_provider_generate",
                                  side_effect=AssertionError("规则层判得出，不得出站"))
        guard.start()
        self.addCleanup(guard.stop)
        self.ready()
        body = self.suggestion(self.ask([self.item("r1")]))
        self.assertEqual(_ModelProbe.received, [])
        self.assertFalse(body["degraded"])
        fields = {row["field"]: row for row in body["fields"]}
        self.assertEqual(set(fields), {"doi", "title", "authors", "abstract"})
        for name, row in fields.items():
            self.assertEqual(row["source"], "rule", name)
            self.assertIsNotNone(row["prefill"], name)
            self.assertIsNone(row["ai_pick"], name)
            self.assertIsNotNone(row["rationale"], name)
            self.assertTrue(row["rationale_key"].startswith("import_merge.ai.rule."))
        self.assertEqual(fields["doi"]["prefill"], "right")
        self.assertEqual(fields["title"]["prefill"], "right")
        self.assertEqual(fields["authors"]["prefill"], "left")
        self.assertEqual(fields["abstract"]["prefill"], "right")
        self.assertEqual(body["suggested_conflict_picks"],
                         {"doi": "R1", "title": "R1", "authors": "keeper",
                          "abstract": "R1"})
        self.assertEqual(self.candidate_status("r1"), "pending")
        self.assertEqual(self.trail_rows(), [])
        # 审计行也落了（规则层判定同样留痕，审计可查）。
        rows = self.audit_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["item_kind"], "merge_conflict")
        self.assertEqual(rows[0]["status"], "ok")


# ---------------------------------------------------------------------------
# 3. 出站载荷负钉
# ---------------------------------------------------------------------------

class OutboundPayloadTests(_ApiCase):
    def test_only_six_field_whitelist_conflicts_are_sent(self):
        self.seed_two_sided()
        base_url = self.probe()
        _ModelProbe.replies = [_reply({"title": "left", "authors": "right",
                                       "journal": "left", "year": "unsure"})]
        self.ready(base_url)
        body = self.suggestion(self.ask([self.item("c1")]))
        self.assertEqual(len(_ModelProbe.received), 1)
        call = _ModelProbe.received[0]
        raw = call["raw"]
        # 负钉：非白名单字段/非冲突字段的值与外发禁区的哨兵串一字不外发。
        for forbidden in ("example.test/keeper", "example.test/merged",
                          "journalArticle", "report", "Same abstract text.",
                          SECRET_NOTES, SECRET_FULLTEXT, SECRET_DECISION,
                          "notes", "fulltext", "screening_decision",
                          "zotero_key", "raw_json"):
            self.assertNotIn(forbidden, raw, f"出站载荷泄漏了 {forbidden}")
        payload = json.loads(call["content"][-1])
        self.assertEqual(payload["kind"], "merge_conflict")
        sent = {row["field"]: row for row in payload["conflicts"]}
        # doi 由规则层判定、abstract 两侧相同 → 都不该出站。
        self.assertEqual(set(sent), {"title", "authors", "journal", "year"})
        for row in sent.values():
            self.assertEqual(set(row), {"field", "left", "right"})
        self.assertEqual(sent["title"]["left"], "Keeper concise title")
        self.assertEqual(sent["title"]["right"],
                         "Merged fuller title about teachers")
        # 系统提示词是设计 §三 的原文（只选不写、不得编造）。
        system = call["content"][0]
        self.assertIn("不得改写、合并、补写或编造任何值", system)
        self.assertIn("unsure", system)
        # 响应：规则层与 AI 层共存。
        fields = {row["field"]: row for row in body["fields"]}
        self.assertEqual(fields["doi"]["source"], "rule")
        self.assertEqual(fields["title"]["prefill"], "left")
        self.assertEqual(fields["title"]["source"], "ai")
        self.assertEqual(fields["authors"]["prefill"], "right")
        self.assertEqual(fields["year"]["prefill"], None)
        self.assertEqual(fields["year"]["fallback"], "default")
        # url / item_type 永不出站 = 保留值。
        for name in ("url", "item_type"):
            self.assertIsNone(fields[name]["prefill"], name)
            self.assertIsNone(fields[name]["source"], name)
            self.assertEqual(fields[name]["fallback"], "default", name)
            self.assertEqual(fields[name]["rationale_key"],
                             "import_merge.ai.field_not_sent", name)
        self.assertEqual(body["suggested_conflict_picks"],
                         {"doi": "R1", "title": "keeper", "authors": "R1",
                          "journal": "keeper"})


# ---------------------------------------------------------------------------
# 4/5/6. 非法返回 / 低置信 / unsure 回落
# ---------------------------------------------------------------------------

class DegradeTests(_ApiCase):
    def test_invalid_pick_values_degrade_to_unsure(self):
        self.seed_two_sided()
        base_url = self.probe()
        _ModelProbe.replies = [_reply({"title": "middle", "journal": "LEFT",
                                       "bogus": "right"}, confidence=0.9)]
        self.ready(base_url)
        body = self.suggestion(self.ask([self.item("c1")]))
        fields = {row["field"]: row for row in body["fields"]}
        self.assertEqual(fields["title"]["prefill"], None)
        self.assertEqual(fields["title"]["fallback"], "default")
        self.assertIn("field_picks_invalid", body["warnings"])
        # 大小写规范化后合法（"LEFT"）。未知字段丢弃，不影响其余字段。
        self.assertEqual(fields["journal"]["prefill"], "left")
        self.assertEqual(fields["journal"]["source"], "ai")
        self.assertTrue(body["degraded"])

    def test_low_confidence_makes_ai_layer_all_unsure(self):
        self.seed_two_sided()
        base_url = self.probe()
        _ModelProbe.replies = [_reply({"title": "left", "authors": "right",
                                       "journal": "left", "year": "left"},
                                      confidence=0.3)]
        self.ready(base_url)
        body = self.suggestion(self.ask([self.item("c1")]))
        self.assertIn("low_confidence", body["warnings"])
        self.assertTrue(body["degraded"])
        fields = {row["field"]: row for row in body["fields"]}
        for name in ("title", "authors", "journal", "year"):
            self.assertEqual(fields[name]["prefill"], None, name)
            self.assertEqual(fields[name]["source"], None, name)
            self.assertEqual(fields[name]["fallback"], "default", name)
        # 规则层判定与模型置信无关。
        self.assertEqual(fields["doi"]["prefill"], "right")
        self.assertEqual(fields["doi"]["source"], "rule")
        self.assertIsNone(fields["title"]["ai_pick"])

    def test_unsure_falls_back_to_keeper_value(self):
        self.seed_two_sided()
        base_url = self.probe()
        _ModelProbe.replies = [_reply({"title": "unsure", "authors": "right",
                                       "journal": "left", "year": "unsure"})]
        self.ready(base_url)
        body = self.suggestion(self.ask([self.item("c1")]))
        fields = {row["field"]: row for row in body["fields"]}
        self.assertEqual(fields["title"]["prefill"], None)
        self.assertEqual(fields["title"]["fallback"], "default")
        self.assertIn("保留条目值", fields["title"]["rationale"])
        self.assertNotIn("title", body["suggested_conflict_picks"])
        self.assertEqual(body["field_picks"]["title"], "unsure")
        self.assertEqual(body["field_picks"]["authors"], "right")

    def test_provider_failure_degrades_not_500(self):
        self.seed_two_sided()
        self.ready()
        with mock.patch.object(ai_suggest_api, "_provider_generate",
                               side_effect=ai_suggest_api.ProviderError("超时")):
            response = self.ask([self.item("c1")])
        body = self.suggestion(response)
        self.assertTrue(body["degraded"])
        self.assertIn("provider_error", body["warnings"])
        fields = {row["field"]: row for row in body["fields"]}
        self.assertEqual(fields["title"]["fallback"], "default")
        self.assertEqual(fields["doi"]["source"], "rule")
        self.assertEqual(self.audit_rows()[0]["status"], "degraded")


# ---------------------------------------------------------------------------
# 7/8. "是不是重复"不预选 + 只读
# ---------------------------------------------------------------------------

class ReadOnlyDisciplineTests(_ApiCase):
    def test_duplicate_group_is_never_preselected(self):
        self.seed_two_sided()
        base_url = self.probe()
        _ModelProbe.replies = [json.dumps({
            "verdict": "same", "confidence": 0.93,
            "rationale_zh": "三条身份证据一致。",
            "field_picks": {"title": "left"}}, ensure_ascii=False)]
        self.ready(base_url)
        body = self.suggestion(self.ask([self.item("c1", kind="duplicate_group")]))
        self.assertEqual(body["verdict"], "same")
        self.assertIsNone(body["field_picks"],
                          "duplicate_group 仍只高亮：给 field_picks 就等于预选")
        self.assertNotIn("suggested_conflict_picks", body)
        self.assertNotIn("fields", body)
        self.assertNotIn("prefill", body)
        self.assertEqual(self.candidate_status("c1"), "pending")

    def test_merge_conflict_suggestion_writes_nothing(self):
        self.seed_two_sided()
        before = self._keeper_snapshot()
        base_url = self.probe()
        _ModelProbe.replies = [_reply({"title": "right", "authors": "right",
                                       "journal": "left", "year": "left"})]
        self.ready(base_url)
        response = self.ask([self.item("c1")])
        body = self.suggestion(response)
        self.assertTrue(response_read_only(response))
        self.assertEqual(body["keeper"]["keeper_key"], "K1")
        self.assertEqual(self._keeper_snapshot(), before,
                         "本端点不写任何题录")
        self.assertEqual(self.candidate_status("c1"), "pending")
        self.assertEqual(self.trail_rows(), [])

    def _keeper_snapshot(self):
        with closing(wf.connect(self.db, read_only=True)) as conn:
            row = conn.execute("SELECT title,authors,journal,year,doi,abstract,url,"
                               "item_type FROM articles WHERE zotero_key='K1'").fetchone()
        return dict(row)


def response_read_only(response) -> bool:
    return bool(response.json().get("read_only"))


# ---------------------------------------------------------------------------
# 9. 审计三栏：rule_pick / ai_pick / human_final
# ---------------------------------------------------------------------------

class AuditTrailTests(_ApiCase):
    def confirm(self, body):
        return self.client.post(
            f"{self.imports}/candidates/c1",
            json={"expected_policy_version": self.policy_version(), **body},
            headers=self._auth(0))

    def test_audit_export_joins_rule_ai_and_human_final(self):
        self.seed_two_sided()
        base_url = self.probe()
        _ModelProbe.replies = [_reply({"title": "left", "authors": "right",
                                       "journal": "right", "year": "left"})]
        self.ready(base_url)
        suggestion = self.suggestion(self.ask([self.item("c1")]))
        audit_id = suggestion["audit_id"]
        # 人执行：题名改用被合并记录（与 AI 的 left 相反）、期刊改回保留值。
        response = self.confirm({"status": "duplicate", "keeper_key": "K1",
                                 "conflict_picks": {"title": "R1", "journal": "keeper"},
                                 "ai_suggestion_id": str(audit_id)})
        self.assertEqual(response.status_code, 200, response.text)
        exported = self.client.get(f"{self.base}/audit/{audit_id}",
                                   headers=self._auth(0))
        self.assertEqual(exported.status_code, 200, exported.text)
        body = exported.json()
        self.assertTrue(body["executed"])
        self.assertEqual(body["candidate_status"], "duplicate")
        fields = {row["field"]: row for row in body["fields"]}
        # 规则层：AI 从未参与，人也没改 → 最终=保留值（保持不动）。
        self.assertEqual(fields["doi"]["rule_pick"], "right")
        self.assertIsNone(fields["doi"]["ai_pick"])
        self.assertEqual(fields["doi"]["human_final"], "keeper")
        # AI 层：AI 建议 left，人改为 R1。
        self.assertEqual(fields["title"]["ai_pick"], "left")
        self.assertIsNone(fields["title"]["rule_pick"])
        self.assertEqual(fields["title"]["human_final"], "R1")
        self.assertEqual(fields["title"]["human_final_value"],
                         "Merged fuller title about teachers")
        # AI 建议 right，人选择保留值 → keeper（留痕无行即“未写”）。
        self.assertEqual(fields["journal"]["ai_pick"], "right")
        self.assertEqual(fields["journal"]["human_final"], "keeper")
        self.assertFalse(fields["journal"]["undone"])
        # 未出站字段：三栏里只有 human_final。
        self.assertIsNone(fields["url"]["ai_pick"])
        self.assertEqual(fields["url"]["human_final"], "keeper")
        # 留痕行确实按 ai_suggestion_id 关联。
        trails = self.trail_rows()
        self.assertTrue(trails)
        self.assertEqual({row["ai_suggestion_id"] for row in trails}, {str(audit_id)})

    def test_audit_export_before_execution_reports_not_executed(self):
        self.seed_two_sided()
        self.ready(self.probe())
        _ModelProbe.replies = [_reply({"title": "left", "authors": "right",
                                       "journal": "left", "year": "left"})]
        suggestion = self.suggestion(self.ask([self.item("c1")]))
        body = self.client.get(f"{self.base}/audit/{suggestion['audit_id']}",
                               headers=self._auth(0)).json()
        self.assertFalse(body["executed"])
        self.assertTrue(all(row["human_final"] is None for row in body["fields"]))

    def test_audit_export_for_d1_kinds_keeps_flat_field_picks(self):
        """非 merge_conflict 的审计导出沿用 D1 形状（fields=[]，扁平 field_picks）。"""
        incoming = _article(zotero_key="m1", title="Synthetic m1 therapy trial",
                            journal="J New", doi="10.test/a")
        before = _article(zotero_key="EXIST", title="Synthetic m1 therapy trial",
                          journal="J Old", doi="10.test/a")
        with closing(wf.connect(self.db)) as conn, conn:
            conn.execute(
                "INSERT INTO workflow_source_records VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("m1", "s", "m1", 2, "01-Wiley.ris", json.dumps(incoming),
                 json.dumps(incoming), "m1", "m1", "EXIST", None, "new", "staged", "now"))
            conn.execute("INSERT INTO workflow_import_members VALUES(?,?,?)", ("b", "m1", 0))
            conn.execute(
                "INSERT INTO workflow_import_metadata(record_id,canonical_key,before_json,"
                "incoming_json) VALUES(?,?,?,?)",
                ("m1", "EXIST", json.dumps(before), json.dumps(incoming)))
        self.ready(self.probe())
        _ModelProbe.replies = [json.dumps({
            "verdict": "different", "confidence": 0.8,
            "rationale_zh": "两侧期刊不同。",
            "field_picks": {"journal": "right"}}, ensure_ascii=False)]
        suggestion = self.suggestion(self.ask([self.item("m1", kind="metadata_diff")]))
        body = self.client.get(f"{self.base}/audit/{suggestion['audit_id']}",
                               headers=self._auth(0)).json()
        self.assertEqual(body["kind"], "metadata_diff")
        self.assertEqual(body["fields"], [])
        self.assertEqual(body["field_picks"], {"journal": "right"})
        self.assertFalse(body["executed"])
        self.assertEqual(body["verdict"], "different")

    def test_audit_export_unknown_id_is_404_structured(self):
        self.seed_two_sided()
        response = self.client.get(f"{self.base}/audit/9999", headers=self._auth(0))
        self.assertEqual(response.status_code, 404, response.text)
        detail = response.json()["detail"]
        self.assertTrue(detail.get("message_key"))


# ---------------------------------------------------------------------------
# 10. 保留者选择 + 参数校验
# ---------------------------------------------------------------------------

class KeeperTests(_ApiCase):
    def test_keeper_defaults_to_suggested_keeper(self):
        self.add_record("R1", row_index=1, title="First record title",
                        doi="10.x/r1", journal="J One")
        self.add_record("R2", row_index=2, title="Different second title",
                        doi="10.x/r2")
        self.add_group("c1", [("record", "R1"), ("record", "R2")])
        self.ready(self.probe())
        _ModelProbe.replies = [_reply({"title": "left", "journal": "left"})]
        body = self.suggestion(self.ask([self.item("c1")]))
        self.assertEqual(body["keeper"]["keeper_record_id"], "R1")
        self.assertEqual(body["keeper"]["source"], "suggested")
        self.assertIn("has_journal", body["keeper"]["reasons"])

    def test_explicit_keeper_record_id_and_key(self):
        self.seed_two_sided()
        self.ready(self.probe())
        _ModelProbe.replies = [_reply({"title": "left", "authors": "right",
                                       "journal": "left", "year": "left"})]
        body = self.suggestion(self.ask([self.item("c1", keeper_key="K1")]))
        self.assertEqual(body["keeper"]["keeper_key"], "K1")
        self.assertEqual(body["keeper"]["source"], "request")
        # 已入任务成员必须当保留者：显式指定被合并记录 → 400。
        response = self.ask([self.item("c1", keeper_record_id="R1")])
        self.assertEqual(response.status_code, 400, response.text)
        detail = response.json()["detail"]
        self.assertEqual(detail["message_key"], "import_ai_assist.keeper_invalid")
        self.assertEqual(detail["params"]["reason"], "active_member_required")

    def test_invalid_keeper_shapes_are_structured(self):
        self.seed_two_sided()
        self.ready(self.probe())
        cases = [
            ({"keeper_key": "K1", "keeper_record_id": "R1"}, "both"),
            ({"keeper_key": "NOPE"}, "not_member"),
            ({"keeper_record_id": "NOPE"}, "not_member"),
        ]
        for extra, reason in cases:
            response = self.ask([self.item("c1", **extra)])
            self.assertEqual(response.status_code, 400, response.text)
            detail = response.json()["detail"]
            self.assertEqual(detail["message_key"], "import_ai_assist.keeper_invalid")
            self.assertEqual(detail["params"]["reason"], reason)

    def test_keeper_on_other_kinds_is_rejected(self):
        self.seed_two_sided()
        self.ready(self.probe())
        response = self.ask([self.item("c1", kind="duplicate_group", keeper_key="K1")])
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(response.json()["detail"]["message_key"],
                         "import_ai_assist.item_invalid")


# ---------------------------------------------------------------------------
# 11. I-6：每类结构化错误都带 message_key + params
# ---------------------------------------------------------------------------

class ErrorShapeTests(_ApiCase):
    def assert_structured(self, response, status=None):
        if status is not None:
            self.assertEqual(response.status_code, status, response.text)
        detail = response.json()["detail"]
        self.assertIsInstance(detail, dict, detail)
        self.assertTrue(detail.get("code"), detail)
        self.assertTrue(detail.get("message_key"), detail)
        self.assertIsInstance(detail.get("params"), dict, detail)
        self.assertTrue(detail.get("detail"), detail)
        return detail

    def test_every_error_class_carries_message_key(self):
        self.seed_two_sided()
        # 开关未开
        detail = self.assert_structured(self.ask([self.item("c1")]), 404)
        self.assertEqual(detail["message_key"], "import_ai_assist.disabled")
        # 无钥匙
        self.enable()
        detail = self.assert_structured(self.ask([self.item("c1")]), 404)
        self.assertEqual(detail["message_key"], "import_ai_assist.key_missing")
        self.save_key()
        # 超限
        detail = self.assert_structured(
            self.ask([self.item("c%02d" % i) for i in range(21)]), 400)
        self.assertEqual(detail["message_key"], "import_ai_assist.too_many_items")
        self.assertEqual(detail["params"]["max_items"], 20)
        # 重复条目
        detail = self.assert_structured(
            self.ask([self.item("c1"), self.item("c1")]), 400)
        self.assertEqual(detail["message_key"], "import_ai_assist.duplicate_item")
        # 条目不存在
        detail = self.assert_structured(self.ask([self.item("nope")]), 404)
        self.assertEqual(detail["message_key"], "import_ai_assist.item_not_found")
        # 条目不可用（组内两条已入任务文献 → 不能合并）
        self.add_active("A1", title="Active one")
        self.add_active("A2", title="Active two")
        self.add_record("R9", title="Staged nine")
        self.add_group("busy", [("active", "A1"), ("active", "A2"), ("record", "R9")])
        detail = self.assert_structured(self.ask([self.item("busy")]), 409)
        self.assertEqual(detail["message_key"], "import_ai_assist.item_unavailable")
        self.assertEqual(detail["params"]["reason"], "active_conflict")
        # 保留者非法
        detail = self.assert_structured(
            self.ask([self.item("c1", keeper_key="NOPE")]), 400)
        self.assertEqual(detail["message_key"], "import_ai_assist.keeper_invalid")
        # 开关乐观锁 / 非负责人 / 无属主
        settings = self.client.get(f"{self.base}/settings", headers=self._auth(0)).json()
        detail = self.assert_structured(self.client.put(
            f"{self.base}/settings",
            json={"expected_version": settings["version"] + 5, "enabled": True},
            headers=self._auth(0)), 409)
        self.assertEqual(detail["message_key"],
                         "import_ai_assist.settings_version_conflict")
        self.assertIn("version", detail["params"])
        # 非负责人：先把 user1 邀进任务（否则是全局 403 而非 owner_only）。
        invite = self.client.post(
            f"/api/tasks/{self.task}/collaboration/invite",
            json={"username": self.users[1]["username"], "role": "coder"},
            headers=self._auth(0))
        self.assertEqual(invite.status_code, 201, invite.text)
        detail = self.assert_structured(self.client.put(
            f"{self.base}/settings", json={"expected_version": 0, "enabled": True},
            headers=self._auth(1)), 403)
        self.assertEqual(detail["message_key"], "import_ai_assist.settings_owner_only")
        # legacy（无属主）：server 模式的更前置守卫（任务查找）先拒绝，仍是结构化
        # 错误且带 message_key；路由内的 import_ai_assist.owner_required 分支由
        # 静态声明钉（见 ErrorDeclarationTests）覆盖，不靠这里触发。
        collab = task_collaboration.shared_store_path(self.info.dir)
        with closing(sqlite3.connect(collab)) as conn, conn:
            conn.execute("DELETE FROM collaboration_task")
        legacy = self.client.get(f"{self.base}/settings", headers=self._auth(0))
        self.assertEqual(legacy.status_code, 409, legacy.text)
        detail = legacy.json()["detail"]
        self.assertTrue(detail.get("message_key"), detail)
        self.assertIsInstance(detail.get("params"), dict, detail)

    def test_key_shape_error_is_structured(self):
        self.seed_two_sided()
        self.ready()
        with mock.patch.object(ai_keys, "base_url_of",
                               side_effect=ai_keys.KeyShapeError("base_url 不是合法 URL。")):
            response = self.ask([self.item("c1")])
        self.assertEqual(response.status_code, 422, response.text)
        detail = response.json()["detail"]
        self.assertIsInstance(detail, dict, detail)
        self.assertEqual(detail["message_key"], "import_ai_assist.key_invalid")
        self.assertIsInstance(detail["params"], dict)


class ErrorDeclarationTests(unittest.TestCase):
    """I-6 静态钉：本文件每个结构化错误点都声明 message_key + params。

    部分分支（如 legacy 任务的 owner_required）在 server 模式下被更前置的
    守卫拦住、运行时不可达；静态检查覆盖全部错误点，不依赖可达性。
    """

    MODULE = Path(__file__).resolve().parents[2] / "custom_backend" / "import_ai_assist_api.py"

    def test_every_error_site_declares_message_key_and_params(self):
        import ast
        tree = ast.parse(self.MODULE.read_text(encoding="utf-8"))
        calls = [node for node in ast.walk(tree)
                 if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                 and node.func.id == "_error"]
        self.assertGreaterEqual(len(calls), 10, "结构化错误点少于预期（漏改？）")
        for call in calls:
            self.assertGreaterEqual(len(call.args), 4, ast.dump(call)[:160])
            key = call.args[2]
            self.assertIsInstance(key, ast.Constant, ast.dump(call)[:160])
            self.assertTrue(str(key.value).startswith("import_ai_assist."),
                            key.value)
            self.assertIsInstance(call.args[3], (ast.Dict, ast.Name),
                                  "params 必须显式给出（dict 字面量或包装器的 params "
                                  "形参）：%s" % ast.dump(call)[:160])

    def test_no_bare_http_exception_outside_error_helper(self):
        import ast
        tree = ast.parse(self.MODULE.read_text(encoding="utf-8"))
        raises = [node for node in ast.walk(tree)
                  if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call)
                  and isinstance(node.exc.func, ast.Name)
                  and node.exc.func.id == "HTTPException"]
        self.assertEqual(raises, [], "新增结构化错误一律走 _error()（带 message_key）")


# ---------------------------------------------------------------------------
# 12. 存储：审计表 CHECK 升级幂等
# ---------------------------------------------------------------------------

class AuditSchemaUpgradeTests(unittest.TestCase):
    OLD = """
    CREATE TABLE import_ai_suggestions(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      task_id TEXT NOT NULL, batch_id TEXT NOT NULL,
      item_kind TEXT NOT NULL CHECK(item_kind IN ('duplicate_group','metadata_diff')),
      item_id TEXT NOT NULL, provider TEXT NOT NULL, model TEXT NOT NULL,
      prompt_version TEXT NOT NULL, request_digest TEXT NOT NULL,
      verdict TEXT NOT NULL CHECK(verdict IN ('same','different','unsure')),
      confidence REAL, rationale_zh TEXT NOT NULL, field_picks_json TEXT,
      warnings_json TEXT NOT NULL DEFAULT '[]',
      status TEXT NOT NULL CHECK(status IN ('ok','degraded')),
      created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
    CREATE UNIQUE INDEX idx_import_ai_suggestions_request
      ON import_ai_suggestions(task_id,batch_id,item_kind,item_id,request_digest);
    INSERT INTO import_ai_suggestions(task_id,batch_id,item_kind,item_id,provider,model,
      prompt_version,request_digest,verdict,confidence,rationale_zh,warnings_json,status,
      created_at,updated_at)
      VALUES('t','b','duplicate_group','c1','p','m','v1','d','same',0.9,'r','[]','ok',
             'now','now');
    """

    def test_old_check_constraint_is_upgraded_and_data_preserved(self):
        with closing(sqlite3.connect(":memory:")) as conn, conn:
            conn.executescript(self.OLD)
            assist._ensure_audit_schema(conn)
            assist._ensure_audit_schema(conn)  # 幂等
            conn.execute(
                "INSERT INTO import_ai_suggestions(task_id,batch_id,item_kind,item_id,"
                "provider,model,prompt_version,request_digest,verdict,confidence,"
                "rationale_zh,warnings_json,status,created_at,updated_at) "
                "VALUES('t','b','merge_conflict','c1','p','m','mv','d2','n/a',0.9,'r',"
                "'[]','ok','now','now')")
            kinds = [row[0] for row in conn.execute(
                "SELECT item_kind FROM import_ai_suggestions ORDER BY id")]
            self.assertEqual(kinds, ["duplicate_group", "merge_conflict"])
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='index' AND "
                "name='idx_import_ai_suggestions_request'").fetchone()[0], 1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
