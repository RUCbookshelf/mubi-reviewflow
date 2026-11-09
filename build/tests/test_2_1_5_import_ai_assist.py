# -*- coding: utf-8 -*-
"""2.1.5 Wave D1 · 导入复核 AI 助手（BYOK）红钉（专属新文件，撤实现必红）。

2026-11-07 用户设计裁定：逐条建议 + 人工确认、批量模式、审计留痕、
低置信明说无法判断、默认关显式触发、无钥匙隐藏入口、复用 ai_suggest 钥匙柜。

覆盖（每条在实现前按现状为红）：

1. 无钥匙 → 404 结构化 ``import_ai_assist_key_missing``（零出站）。
2. 任务级开关默认关 → 404 ``import_ai_assist_disabled``；开关 owner-only、
   ``expected_version`` 乐观锁；成员可读 settings（入口显隐依据）。
3. 出站载荷只含题录字段（负钉）：本地 http.server 充当模型端点，断言
   发往模型的两侧题录**逐键**只有 title/authors/year/journal/doi/abstract，
   且埋在 ``raw`` 里的 notes/全文/筛选决定与 url 等字段 **一个字符都不外发**。
4. 低置信（<0.6）与解析失败 → ``verdict=unsure``（绝不硬给建议）；恰好
   0.6 的边界不算低置信；``field_picks`` 仅 metadata_diff 且只保留合法键值。
5. 审计行落库：新表 ``import_ai_suggestions``（batch/条目/模型名/请求摘要
   哈希/结果/时间），同请求重复调用幂等（行数不变、audit_id 稳定）。
6. 建议只读不代存：调用前后导入复核既有状态（候选状态/元数据动作）不变。
7. 单次请求条目上限 20 → 第 21 条返回 400 ``import_ai_assist_too_many_items``
   （先判条目数，再解析条目，零出站）。
8. 权限：非任务成员 403。
9. 出站失败（超时/网络）逐条优雅降级为 unsure，绝不 500。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import unittest
from contextlib import closing
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

fixture = tempfile.TemporaryDirectory(prefix="rf_215d_import_ai_")
os.environ["COBOOKSHELF_DATA_DIR"] = fixture.name
os.environ["REVIEWFLOW_MODE"] = "server"

from fastapi.testclient import TestClient  # noqa: E402

from coscreen import db as db_mod  # noqa: E402
from coscreen import review_imports as store  # noqa: E402
from coscreen import review_workflow as wf  # noqa: E402
from coscreen import tasks as tasks_mod  # noqa: E402
from coscreen.models import Article  # noqa: E402
from custom_backend import ai_keys  # noqa: E402
from custom_backend import ai_suggest_api  # noqa: E402
from custom_backend import auth as auth_mod  # noqa: E402
from custom_backend.main import DATA_DIR, app  # noqa: E402

FAKE_KEY = "sk-import-assist-abcdef123456"
DATA_ROOT = Path(DATA_DIR)

#: 埋在 raw/url 里、绝不许随出站载荷离开服务端的哨兵串。
SECRET_NOTES = "SECRET-NOTES-ALPHA"
SECRET_FULLTEXT = "SECRET-FULLTEXT-BETA"
SECRET_DECISION = "SECRET-SCREEN-DECISION-GAMMA"

#: 出站题录的逐键白名单（设计裁定：只发题录/摘要级数据）。
ALLOWED_KEYS = {"title", "authors", "year", "journal", "doi", "abstract"}

REPLY_SAME = json.dumps({
    "verdict": "same", "confidence": 0.92,
    "rationale_zh": "题名、作者、年份与 DOI 一致，指向同一篇文献。",
}, ensure_ascii=False)

REPLY_FIELD_PICKS = json.dumps({
    "verdict": "different", "confidence": 0.8,
    "rationale_zh": "两侧期刊不同，可能不是同一篇记录。",
    "field_picks": {"journal": "right", "title": "left", "bogus": "left"},
}, ensure_ascii=False)

REPLY_LOW_CONFIDENCE = json.dumps({
    "verdict": "same", "confidence": 0.3,
    "rationale_zh": "题名相似但证据不足。",
}, ensure_ascii=False)

REPLY_BOUNDARY_CONFIDENCE = json.dumps({
    "verdict": "same", "confidence": 0.6,
    "rationale_zh": "题录基本一致。",
}, ensure_ascii=False)

REPLY_NOT_JSON = "这不是 JSON，模型跑题了。"


class _ModelProbe(BaseHTTPRequestHandler):
    """本地 OpenAI 兼容模型端点探针（不打真网）。"""

    replies: list[str] = []
    received: list[dict] = []

    def do_POST(self):  # noqa: N802  BaseHTTPRequestHandler 约定
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        body = json.loads(raw.decode("utf-8", "replace") or "{}")
        type(self).received.append({
            "path": self.path,
            "headers": {k.lower(): v for k, v in self.headers.items()},
            "json": body,
            "raw": raw.decode("utf-8", "replace"),
        })
        reply = type(self).replies.pop(0) if type(self).replies else REPLY_SAME
        payload = json.dumps({
            "model": body.get("model") or "probe-model",
            "choices": [{"message": {"content": reply}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):  # noqa: A003  静默探针日志
        return


def _article(key: str, *, title: str, journal: str = "J Test", doi: str = "10.test/a",
             year: int = 2024) -> dict:
    return Article(
        zotero_key=key, title=title, authors="Doe J; Smith A", journal=journal,
        year=year, doi=doi, abstract="Background: example trial of therapy X.",
        url="http://example.test/landing",
        raw={"notes": SECRET_NOTES, "fulltext": SECRET_FULLTEXT,
             "screening_decision": SECRET_DECISION, "tags": ["internal"]},
    ).to_dict()


class ImportAIAssistApiTests(unittest.TestCase):
    @classmethod
    def _auth(cls, index=0):
        return {"Authorization": "Bearer " + cls.users[index]["token"]}

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.client.__enter__()
        cls.users = []
        for i in range(3):
            user = auth_mod.create_user(f"r215d{i}@example.test", "R215D-ImportAI-2026")
            cls.users.append({"user_id": user["id"], "username": user["username"],
                              "token": auth_mod.create_access_token(user["id"], user["username"])})
        # 门禁墙钟（类级模板库，同 test_import_review_actions 手法）：模板任务
        # 只含来源/批次/文件三类静态种子；每条用例复制后按需补条目。
        cls._slug = tasks_mod.slugify_screener(cls.users[0]["username"])
        template = cls.client.post("/api/tasks", json={"name": "import-ai-template"},
                                   headers=cls._auth(0)).json()["task_id"]
        ctx0 = app.state.review_workflow.context(template, cls.users[0])
        db_mod.init_db(ctx0.owner_db)
        store.migrate(ctx0.owner_db)
        with closing(wf.connect(ctx0.owner_db)) as conn, conn:
            conn.execute("INSERT INTO workflow_sources VALUES(?,?,?,?,?)",
                         ("s", "01-Wiley.ris", "01-wiley.ris", 1, "now"))
            conn.execute("INSERT INTO workflow_import_batches VALUES(?,?,?,?,?,?,?)",
                         ("b", str(cls.users[0]["user_id"]), "h", "batch", 1, "staged", "now"))
            conn.execute("INSERT INTO workflow_import_files VALUES(?,?,?,?,?)",
                         ("b", 0, "01-Wiley.ris", "d", "s"))
        cls._template_db_bytes = Path(ctx0.owner_db).read_bytes()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        fixture.cleanup()

    def setUp(self):
        os.environ.pop("REVIEWFLOW_AI_KEY", None)
        for user in self.users:
            ai_keys.delete_personal_key(user["user_id"], DATA_ROOT)
        _ModelProbe.received = []
        _ModelProbe.replies = []
        self.task = self.client.post("/api/tasks", json={"name": self._testMethodName},
                                     headers=self._auth(0)).json()["task_id"]
        info = tasks_mod.find_task(self.task, DATA_DIR)
        self.db = info.dir / (self._slug + ".db")
        Path(self.db).write_bytes(self._template_db_bytes)
        self.base = f"/api/tasks/{self.task}/import-review/ai-assist"

    # -- 种子 -------------------------------------------------------------

    def seed(self, *, metadata: bool = True) -> None:
        """种一组重复候选 + 一条元数据差异提案（两侧题录都埋哨兵）。"""
        with closing(wf.connect(self.db)) as conn, conn:
            for key, journal in (("r1", "J Test"), ("r2", "J Test")):
                article = _article(key, title=f"Synthetic {key} therapy trial", journal=journal)
                conn.execute(
                    "INSERT INTO workflow_source_records VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (key, "s", key, 1, "01-Wiley.ris", json.dumps(article), json.dumps(article),
                     key, key, None, None, "new", "staged", "now"))
                conn.execute("INSERT INTO workflow_import_members VALUES(?,?,?)", ("b", key, 0))
            conn.execute(
                "INSERT INTO workflow_import_candidates(id,signature,kind,status,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?)", ("c1", "sig-c1", "title", "pending", "now", "now"))
            for key in ("r1", "r2"):
                conn.execute("INSERT INTO workflow_import_candidate_members VALUES(?,?,?)",
                             ("c1", "record", key))
            if metadata:
                incoming = _article("m1", title="Synthetic m1 therapy trial",
                                    journal="J New", doi="10.test/a")
                before = _article("EXIST", title="Synthetic m1 therapy trial",
                                  journal="J Old", doi="10.test/a")
                conn.execute(
                    "INSERT INTO workflow_source_records VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    ("m1", "s", "m1", 2, "01-Wiley.ris", json.dumps(incoming),
                     json.dumps(incoming), "m1", "m1", "EXIST", None, "new", "staged", "now"))
                conn.execute("INSERT INTO workflow_import_members VALUES(?,?,?)", ("b", "m1", 0))
                conn.execute(
                    "INSERT INTO workflow_import_metadata(record_id,canonical_key,before_json,incoming_json) "
                    "VALUES(?,?,?,?)", ("m1", "EXIST", json.dumps(before), json.dumps(incoming)))

    def probe(self) -> tuple[HTTPServer, str]:
        server = HTTPServer(("127.0.0.1", 0), _ModelProbe)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server, f"http://127.0.0.1:{server.server_port}/v1"

    def save_key(self, base_url: str | None = None, user_index: int = 0) -> None:
        body = {"provider": "openai", "key": FAKE_KEY, "model": "probe-model"}
        if base_url:
            body["base_url"] = base_url
        response = self.client.put("/api/ai/key", json=body, headers=self._auth(user_index))
        self.assertEqual(response.status_code, 200, response.text)

    def enable(self, user_index: int = 0) -> None:
        settings = self.client.get(f"{self.base}/settings", headers=self._auth(user_index)).json()
        response = self.client.put(
            f"{self.base}/settings",
            json={"expected_version": settings["version"], "enabled": True},
            headers=self._auth(user_index))
        self.assertEqual(response.status_code, 200, response.text)

    def invite_member(self) -> None:
        response = self.client.post(
            f"/api/tasks/{self.task}/collaboration/invite",
            json={"username": self.users[1]["username"], "role": "coder"},
            headers=self._auth(0))
        self.assertEqual(response.status_code, 201, response.text)

    def ask(self, items, user_index: int = 0):
        return self.client.post(f"{self.base}", json={"items": items}, headers=self._auth(user_index))

    @staticmethod
    def duplicate_item(item_id: str = "c1") -> dict:
        return {"batch_id": "b", "kind": "duplicate_group", "item_id": item_id}

    @staticmethod
    def metadata_item(item_id: str = "m1") -> dict:
        return {"batch_id": "b", "kind": "metadata_diff", "item_id": item_id}

    def audit_rows(self) -> list[dict]:
        with closing(wf.connect(self.db, read_only=True)) as conn:
            tables = {row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            if "import_ai_suggestions" not in tables:
                return []
            return [dict(row) for row in conn.execute(
                "SELECT * FROM import_ai_suggestions ORDER BY id")]

    # -- 1/2. 无钥匙 / 开关 ------------------------------------------------

    def test_key_missing_is_404_and_zero_outbound(self):
        self.seed()
        guard = mock.patch.object(ai_suggest_api, "_provider_generate",
                                  side_effect=AssertionError("无钥匙不得出站"))
        guard.start()
        self.addCleanup(guard.stop)
        self.enable()
        response = self.ask([self.duplicate_item()])
        self.assertEqual(response.status_code, 404, response.text)
        detail = response.json()["detail"]
        self.assertEqual(detail["code"], "import_ai_assist_key_missing")
        self.assertTrue(detail["guidance"])
        self.assertNotIn(FAKE_KEY, response.text)
        self.assertEqual(self.audit_rows(), [])

    def test_toggle_default_off_and_owner_only(self):
        self.seed()
        self.invite_member()
        guard = mock.patch.object(ai_suggest_api, "_provider_generate",
                                  side_effect=AssertionError("开关未开不得出站"))
        guard.start()
        self.addCleanup(guard.stop)
        # 默认关：读到的 enabled=False，调用 404 disabled。
        settings = self.client.get(f"{self.base}/settings", headers=self._auth(0))
        self.assertEqual(settings.status_code, 200, settings.text)
        self.assertFalse(settings.json()["enabled"])
        self.save_key()
        response = self.ask([self.duplicate_item()])
        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(response.json()["detail"]["code"], "import_ai_assist_disabled")
        # 成员可读（入口显隐依据），不可写。
        self.assertEqual(self.client.get(
            f"{self.base}/settings", headers=self._auth(1)).status_code, 200)
        self.assertEqual(self.client.put(
            f"{self.base}/settings", json={"expected_version": 0, "enabled": True},
            headers=self._auth(1)).status_code, 403)
        # 乐观锁：错误版本 409，正确版本 200。
        self.assertEqual(self.client.put(
            f"{self.base}/settings", json={"expected_version": 99, "enabled": True},
            headers=self._auth(0)).status_code, 409)
        enabled = self.client.put(
            f"{self.base}/settings", json={"expected_version": 0, "enabled": True},
            headers=self._auth(0))
        self.assertEqual(enabled.status_code, 200, enabled.text)
        self.assertTrue(enabled.json()["enabled"])
        self.assertEqual(self.audit_rows(), [])

    # -- 3. 出站载荷负钉 ----------------------------------------------------

    def test_outbound_payload_carries_bibliographic_fields_only(self):
        self.seed()
        server, base_url = self.probe()
        _ModelProbe.replies = [REPLY_SAME, REPLY_FIELD_PICKS]
        self.save_key(base_url)
        self.enable()
        response = self.ask([self.duplicate_item(), self.metadata_item()])
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(_ModelProbe.received), 2)
        self.assertNotIn(SECRET_NOTES, response.text)
        self.assertNotIn(SECRET_FULLTEXT, response.text)
        self.assertNotIn(SECRET_DECISION, response.text)
        for call in _ModelProbe.received:
            raw = call["raw"]
            # 负钉：埋在 raw / url 里的内容一个字符都不外发。
            for secret in (SECRET_NOTES, SECRET_FULLTEXT, SECRET_DECISION,
                           "example.test/landing"):
                self.assertNotIn(secret, raw, f"出站载荷泄漏了 {secret}")
            for forbidden in ("notes", "fulltext", "screening_decision",
                              "raw_json", "content_hash", "zotero_key"):
                self.assertNotIn(forbidden, raw, f"出站载荷出现了非题录键 {forbidden}")
            # 正钉：每条消息只带白名单题录键，且两侧题录都在。
            contents = [m["content"] for m in call["json"]["messages"]]
            payload = json.loads(contents[-1])
            sides = payload["members"] if payload["kind"] == "duplicate_group" else [payload["left"], payload["right"]]
            self.assertEqual(payload["kind"] in ("duplicate_group", "metadata_diff"), True)
            for side in sides:
                self.assertEqual(set(side), ALLOWED_KEYS, side)
                self.assertTrue(side["title"])
            if payload["kind"] == "duplicate_group":
                self.assertIn("Synthetic r1 therapy trial", contents[-1])

    # -- 4. 低置信 / 解析失败 ----------------------------------------------

    def test_low_confidence_and_parse_failure_degrade_to_unsure(self):
        self.seed()
        _, base_url = self.probe()
        _ModelProbe.replies = [REPLY_LOW_CONFIDENCE, REPLY_NOT_JSON,
                               REPLY_BOUNDARY_CONFIDENCE]
        self.save_key(base_url)
        self.enable()
        response = self.ask([self.duplicate_item()])
        self.assertEqual(response.status_code, 200, response.text)
        first = response.json()["suggestions"][0]
        self.assertEqual(first["verdict"], "unsure")
        self.assertTrue(first["degraded"])
        self.assertIn("low_confidence", first["warnings"])
        self.assertIn("无法判断", first["rationale_zh"])

        # 解析失败同样 unsure，绝不硬给建议。
        response = self.ask([self.metadata_item()])
        self.assertEqual(response.status_code, 200, response.text)
        second = response.json()["suggestions"][0]
        self.assertEqual(second["verdict"], "unsure")
        self.assertTrue(second["degraded"])
        self.assertIn("provider_response_not_json", second["warnings"])

        # 边界：恰好 0.6 不属于低置信。
        response = self.ask([self.duplicate_item()])
        self.assertEqual(response.status_code, 200, response.text)
        third = response.json()["suggestions"][0]
        self.assertEqual(third["verdict"], "same")
        self.assertFalse(third["degraded"])

    def test_field_picks_only_for_metadata_diff_and_filtered(self):
        self.seed()
        _, base_url = self.probe()
        _ModelProbe.replies = [REPLY_FIELD_PICKS, REPLY_FIELD_PICKS]
        self.save_key(base_url)
        self.enable()
        response = self.ask([self.metadata_item(), self.duplicate_item()])
        self.assertEqual(response.status_code, 200, response.text)
        suggestions = {row["kind"]: row for row in response.json()["suggestions"]}
        picks = suggestions["metadata_diff"]["field_picks"]
        self.assertEqual(picks, {"journal": "right", "title": "left"})
        self.assertIsNone(suggestions["duplicate_group"]["field_picks"])

    # -- 5/6. 审计与只读 ----------------------------------------------------

    def test_audit_rows_persisted_and_idempotent_and_state_untouched(self):
        self.seed()
        _, base_url = self.probe()
        _ModelProbe.replies = [REPLY_SAME, REPLY_FIELD_PICKS]
        self.save_key(base_url)
        self.enable()
        items = [self.duplicate_item(), self.metadata_item()]
        response = self.ask(items)
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertTrue(body["read_only"])
        first_ids = [row["audit_id"] for row in body["suggestions"]]
        rows = self.audit_rows()
        self.assertEqual(len(rows), 2)
        by_item = {(row["item_kind"], row["item_id"]): row for row in rows}
        duplicate = by_item[("duplicate_group", "c1")]
        self.assertEqual(duplicate["batch_id"], "b")
        self.assertEqual(duplicate["model"], "probe-model")
        self.assertEqual(duplicate["verdict"], "same")
        self.assertEqual(duplicate["prompt_version"], "import-ai-assist/v1")
        self.assertEqual(len(duplicate["request_digest"]), 64)
        self.assertTrue(duplicate["created_at"])
        self.assertEqual(by_item[("metadata_diff", "m1")]["verdict"], "different")
        # 建议只读不代存：导入复核既有状态不变。
        with closing(wf.connect(self.db, read_only=True)) as conn:
            self.assertEqual(conn.execute(
                "SELECT status FROM workflow_import_candidates WHERE id='c1'").fetchone()[0],
                "pending")
            self.assertIsNone(conn.execute(
                "SELECT action FROM workflow_import_metadata WHERE record_id='m1'").fetchone()[0])
        # 幂等：同一 batch/条目/模型/请求摘要重复请求 → 行数不变、audit_id 稳定。
        _ModelProbe.replies = [REPLY_SAME, REPLY_FIELD_PICKS]
        again = self.ask(items)
        self.assertEqual(again.status_code, 200, again.text)
        self.assertEqual([row["audit_id"] for row in again.json()["suggestions"]], first_ids)
        self.assertEqual(len(self.audit_rows()), 2)

    # -- 7/8. 条目上限与权限 -----------------------------------------------

    def test_already_decided_items_are_not_suggested(self):
        """只对待处理项出建议：已处理的候选组/元数据提案 → 404 item_not_found。"""
        self.seed()
        guard = mock.patch.object(ai_suggest_api, "_provider_generate",
                                  side_effect=AssertionError("已处理条目不得出站"))
        guard.start()
        self.addCleanup(guard.stop)
        with closing(wf.connect(self.db)) as conn, conn:
            conn.execute("UPDATE workflow_import_candidates SET status='duplicate' WHERE id='c1'")
            conn.execute("UPDATE workflow_import_metadata SET action='keep_existing' WHERE record_id='m1'")
        self.save_key()
        self.enable()
        for item in (self.duplicate_item(), self.metadata_item()):
            response = self.ask([item])
            self.assertEqual(response.status_code, 404, response.text)
            self.assertEqual(response.json()["detail"]["code"],
                             "import_ai_assist_item_not_found")
        self.assertEqual(self.audit_rows(), [])

    def test_item_limit_is_400_before_resolution_and_outbound(self):
        self.seed()
        guard = mock.patch.object(ai_suggest_api, "_provider_generate",
                                  side_effect=AssertionError("超限不得出站"))
        guard.start()
        self.addCleanup(guard.stop)
        self.save_key()
        self.enable()
        response = self.ask([self.duplicate_item(f"c{i}") for i in range(21)])
        self.assertEqual(response.status_code, 400, response.text)
        detail = response.json()["detail"]
        self.assertEqual(detail["code"], "import_ai_assist_too_many_items")
        self.assertEqual(detail["max_items"], 20)
        self.assertEqual(self.audit_rows(), [])

    def test_non_member_is_403(self):
        self.seed()
        self.save_key(user_index=2)
        response = self.ask([self.duplicate_item()], user_index=2)
        self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual(self.client.get(
            f"{self.base}/settings", headers=self._auth(2)).status_code, 403)
        self.assertEqual(self.audit_rows(), [])

    # -- 9. 出站失败优雅降级 -----------------------------------------------

    def test_provider_failure_degrades_to_unsure_not_500(self):
        self.seed()
        self.save_key()
        self.enable()
        with mock.patch.object(ai_suggest_api, "_provider_generate",
                               side_effect=ai_suggest_api.ProviderError(
                                   "provider 超时（10s）: TimeoutError")):
            response = self.ask([self.duplicate_item(), self.metadata_item()])
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual([row["verdict"] for row in body["suggestions"]], ["unsure", "unsure"])
        self.assertTrue(all(row["degraded"] for row in body["suggestions"]))
        self.assertIn("provider_error", body["suggestions"][0]["warnings"])
        rows = self.audit_rows()
        self.assertEqual(len(rows), 2)
        self.assertEqual({row["status"] for row in rows}, {"degraded"})




class OpenerProxyPins(unittest.TestCase):
    """终审 #31：导入 AI 的 provider opener 必须忽略 HTTP(S)_PROXY
    （沿 test_pdf_fetch_api.py:892 钉型——否则代理环境下 API Key 与六字段
    载荷整包发往代理主机，本地 Ollama 回环端点也功能性失效）。"""

    def test_opener_ignores_proxy_environment(self):
        import urllib.request as _urllib

        def proxy_routing(opener):
            return sorted(name for name, _func in opener.handle_open.items()
                          for _h in opener.handle_open[name]
                          if type(_h).__name__ == "ProxyHandler")

        with mock.patch.dict(os.environ, {"HTTP_PROXY": "http://127.0.0.1:9",
                                          "HTTPS_PROXY": "http://127.0.0.1:9",
                                          "http_proxy": "http://127.0.0.1:9"}):
            default_opener = _urllib.build_opener()
            self.assertTrue(proxy_routing(default_opener),
                            "前提：默认 opener 在代理环境下会走代理（漏洞形态）")
            our_opener = ai_suggest_api._build_opener()
            self.assertEqual(proxy_routing(our_opener), [],
                             "provider opener 必须忽略 HTTP(S)_PROXY（空 ProxyHandler）")
        self.assertTrue(any(type(h).__name__ == "_NoRedirectHandler"
                            for h in our_opener.handlers),
                        "拒绝重定向 handler 仍需保留")


class FulltextPathContainmentPins(unittest.TestCase):
    """终审 #39 r3：ai_suggest 全文读取的包含性判定直测（端到端钉与变异钉
    在 build/tests/test_ai_suggest.py::AISuggestTests 的 r3 用例）。"""

    def test_pool_path_rejected_and_task_dir_allowed(self):
        import shutil as _shutil

        base = Path(tempfile.mkdtemp(prefix="sec2-ai-contain-"))
        self.addCleanup(lambda: _shutil.rmtree(base, ignore_errors=True))
        task_dir = base / "tasks" / "t1"
        (task_dir / "pdfs").mkdir(parents=True)
        pool = base / "pdfs"
        pool.mkdir()
        inside = task_dir / "pdfs" / "ok.pdf"
        inside.write_bytes(b"%PDF-1.4 ok")
        outside = pool / "legacy.pdf"
        outside.write_bytes(b"%PDF-1.4 secret")
        owner_db = task_dir / "owner.db"
        self.assertTrue(ai_suggest_api._fulltext_path_allowed(inside, owner_db))
        self.assertFalse(ai_suggest_api._fulltext_path_allowed(outside, owner_db),
                         "共享平铺池路径必须被拒")
        self.assertFalse(
            ai_suggest_api._fulltext_path_allowed(task_dir / ".." / ".." / "pdfs"
                                                  / "legacy.pdf", owner_db),
            "上跳写法解析后同样在任务目录外")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
