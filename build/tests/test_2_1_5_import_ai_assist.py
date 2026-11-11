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
from custom_backend import import_ai_assist_api  # noqa: E402
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
        # 本地探针端点（127.0.0.1）= 本地模型语义：ai_keys 的回环例外按
        # desktop 模式放行（sec29 F-5 起 server 模式拦截回环/私网 base_url）；
        # 只钉 ai_keys 的模式判定，其余 server 行为不受影响。
        _mode = mock.patch.object(ai_keys, "_server_mode", lambda: False)
        _mode.start()
        self.addCleanup(_mode.stop)
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


class StorageFailurePins(unittest.TestCase):
    """终审 #32：三端点 sqlite 故障 → 结构化 503 storage_unavailable（不 500）。

    场景 A（workflow.context）/ B（store.migrate，出站前）/ C（出站后审计写）
    分别钉；A/B 保持零出站；payload 解析阶段的锁不再误映射 404。
    """

    @classmethod
    def _auth(cls, index=0):
        return {"Authorization": "Bearer " + cls.users[index]["token"]}

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.client.__enter__()
        cls.users = []
        for i in range(2):
            user = auth_mod.create_user(
                f"r215sec8ai{i}@example.test", "R215Sec8-AIStorage-2026")
            cls.users.append({"user_id": user["id"], "username": user["username"],
                              "token": auth_mod.create_access_token(
                                  user["id"], user["username"])})
        # 与 ImportAIAssistApiTests 同款模板库（来源/批次/文件静态种子）。
        cls._slug = tasks_mod.slugify_screener(cls.users[0]["username"])
        template = cls.client.post("/api/tasks", json={"name": "import-ai-sec8-tpl"},
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

    def setUp(self):
        os.environ.pop("REVIEWFLOW_AI_KEY", None)
        # 本地探针端点（127.0.0.1）= 本地模型语义：ai_keys 的回环例外按
        # desktop 模式放行（sec29 F-5 起 server 模式拦截回环/私网 base_url）；
        # 只钉 ai_keys 的模式判定，其余 server 行为不受影响。
        _mode = mock.patch.object(ai_keys, "_server_mode", lambda: False)
        _mode.start()
        self.addCleanup(_mode.stop)
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

    # -- 夹具（与 ImportAIAssistApiTests 同款） ----------------------------

    def seed(self, *, metadata: bool = True) -> None:
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

    def save_key(self, base_url: str) -> None:
        response = self.client.put(
            "/api/ai/key",
            json={"provider": "openai", "key": FAKE_KEY, "model": "probe-model",
                  "base_url": base_url},
            headers=self._auth(0))
        self.assertEqual(response.status_code, 200, response.text)

    def enable(self) -> None:
        settings = self.client.get(f"{self.base}/settings", headers=self._auth(0)).json()
        response = self.client.put(
            f"{self.base}/settings",
            json={"expected_version": settings["version"], "enabled": True},
            headers=self._auth(0))
        self.assertEqual(response.status_code, 200, response.text)

    @staticmethod
    def duplicate_item(item_id: str = "c1") -> dict:
        return {"batch_id": "b", "kind": "duplicate_group", "item_id": item_id}

    def ask(self, items):
        return self.client.post(f"{self.base}", json={"items": items},
                                headers=self._auth(0))

    def audit_rows(self) -> list[dict]:
        with closing(wf.connect(self.db, read_only=True)) as conn:
            tables = {row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            if "import_ai_suggestions" not in tables:
                return []
            return [dict(row) for row in conn.execute(
                "SELECT * FROM import_ai_suggestions ORDER BY id")]

    def _assert_503(self, response):
        self.assertEqual(response.status_code, 503, response.text)
        detail = response.json()["detail"]
        self.assertEqual(detail["code"], "import_ai_assist_storage_unavailable")
        self.assertEqual(detail["message_key"], "import_ai_assist.storage_unavailable")

    def _arm_zero_outbound_guard(self):
        guard = mock.patch.object(
            ai_suggest_api, "_provider_generate",
            side_effect=AssertionError("存储故障阶段不得出站"))
        guard.start()
        self.addCleanup(guard.stop)

    def test_context_failure_503_on_all_three_endpoints_zero_outbound(self):
        self.seed()
        _server, base = self.probe()
        self.save_key(base)
        self.enable()
        self._arm_zero_outbound_guard()
        with mock.patch.object(
                app.state.review_workflow, "context",
                side_effect=sqlite3.OperationalError("database is locked")):
            self._assert_503(self.client.get(f"{self.base}/settings",
                                             headers=self._auth(0)))
            self._assert_503(self.client.put(
                f"{self.base}/settings",
                json={"expected_version": 0, "enabled": True},
                headers=self._auth(0)))
            self._assert_503(self.ask([self.duplicate_item()]))
        self.assertEqual(_ModelProbe.received, [])

    def test_migrate_failure_503_zero_outbound(self):
        self.seed()
        _server, base = self.probe()
        self.save_key(base)
        self.enable()
        self._arm_zero_outbound_guard()
        with mock.patch.object(
                import_ai_assist_api.store, "migrate",
                side_effect=sqlite3.OperationalError("database is locked")):
            self._assert_503(self.ask([self.duplicate_item()]))
        self.assertEqual(_ModelProbe.received, [])

    def test_payload_read_lock_is_503_not_404(self):
        self.seed()
        _server, base = self.probe()
        self.save_key(base)
        self.enable()
        self._arm_zero_outbound_guard()
        with mock.patch.object(
                import_ai_assist_api, "load_item_payload",
                side_effect=sqlite3.OperationalError("database is locked")):
            # 修复前该阶段误映射 404 item_not_found（把存储故障说成条目不存在）
            self._assert_503(self.ask([self.duplicate_item()]))
        self.assertEqual(_ModelProbe.received, [])

    def test_audit_write_failure_after_outbound_is_503_never_fake_200(self):
        self.seed()
        _server, base = self.probe()
        self.save_key(base)
        self.enable()
        broken_upsert = ("INSERT INTO import_ai_suggestions_no_such VALUES ("
                         + ",".join(["?"] * 16) + ")")
        with mock.patch.object(import_ai_assist_api, "_AUDIT_UPSERT", broken_upsert):
            response = self.ask([self.duplicate_item()])
        self._assert_503(response)
        # 出站已真实发生（该次建议的额度已消耗）——503 如实告知，绝不假 200。
        self.assertGreaterEqual(len(_ModelProbe.received), 1)
        # 审计零行落库（唯一写入点失败即整批丢弃，不半写）。
        self.assertEqual(self.audit_rows(), [])


# ===========================================================================
# 2.1.5 aiux G · 双源交叉验证（cross_check）红钉（2026-11-12；只在文件末尾
# 追加新类，不改上方既有用例——「缺省关=字节级行为不变」本身是第一钉）。
# ===========================================================================

#: 第二源专用哨兵钥匙（与主源 FAKE_KEY 区分；并钉其绝不进任何响应/错误体）。
SHARED_FAKE_KEY = "sk-shared-cross-987654321"

REPLY_SECOND_SAME = json.dumps({
    "verdict": "same", "confidence": 0.8,
    "rationale_zh": "第二源：题名、作者与 DOI 一致，应为重复。",
}, ensure_ascii=False)

REPLY_SECOND_SAME_MID = json.dumps({
    "verdict": "same", "confidence": 0.7,
    "rationale_zh": "第二源：证据基本一致。",
}, ensure_ascii=False)

REPLY_SECOND_DIFFERENT = json.dumps({
    "verdict": "different", "confidence": 0.85,
    "rationale_zh": "第二源：期刊与年份不符，不像同一篇。",
}, ensure_ascii=False)

REPLY_SAME_MID = json.dumps({
    "verdict": "same", "confidence": 0.75,
    "rationale_zh": "题录大体一致。",
}, ensure_ascii=False)

#: Jev(systemone) 同形响应（decision 钥匙用例）：duplicate 概率 0.91、
#: confidence 0.7 → 映射置信度取小 = 0.7。
JEV_CROSS_SAME = {"model": "jev-latest",
                  "answers": {"duplicate_verdict": {
                      "type": "choice", "choice": "duplicate",
                      "probabilities": {"duplicate": 0.91, "include": 0.06,
                                         "unsure": 0.03},
                      "confidence": 0.7}},
                  "usage": {"input_tokens": 90, "output_tokens": 9}}


class _SecondChatProbe(BaseHTTPRequestHandler):
    """第二独立源探针（OpenAI 兼容形状）。

    类属性与 _ModelProbe 分离——两探针的 received/replies 互不混流，才能分别
    钉「主源出站了几次 / 第二源出站了几次」。``status`` 非 200 时直接回该
    状态码（第二源 5xx 用例；经 _call_provider 的瞬时重试档，一次合成调用
    会打到两次）。"""

    replies: list[str] = []
    received: list[dict] = []
    status: int = 200

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
        if type(self).status != 200:
            self.send_response(type(self).status)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        reply = type(self).replies.pop(0) if type(self).replies else REPLY_SECOND_SAME
        payload = json.dumps({
            "model": body.get("model") or "probe-model-2",
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


class _JevCrossProbe(BaseHTTPRequestHandler):
    """decision 钥匙用例的本地 Jev(systemone) 端点探针。"""

    replies: list[dict] = []
    received: list[dict] = []

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        type(self).received.append({
            "path": self.path,
            "json": json.loads(raw.decode("utf-8", "replace") or "{}"),
        })
        reply = type(self).replies.pop(0) if type(self).replies else JEV_CROSS_SAME
        payload = json.dumps(reply, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):  # noqa: A003  静默探针日志
        return


class ImportAiCrossCheckTests(unittest.TestCase):
    """aiux G：cross_check 双源交叉验证七钉。

    ① 缺省关（字段缺省/显式 False）：双源都在也只调主源；② 双源一致 same →
    verdict 不变、confidence=max(c1,c2)+0.1（上限 1.0）；③ 双源不一致 →
    unsure+degraded+cross_check_disagree；④ 共享 llm 槽缺 → cross_check_
    unavailable 不降级；⑤ 共享槽与个人同 provider+base_url → 视同无第二源；
    ⑥ 第二源 5xx → 照主源结果+cross_check_second_failed，且不触发主源连败
    停机；⑦ decision 钥匙单源体系：cross_check=true 与 false 行为全等。
    """

    @classmethod
    def _auth(cls, index=0):
        return {"Authorization": "Bearer " + cls.users[index]["token"]}

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.client.__enter__()
        cls.users = []
        for i in range(3):
            user = auth_mod.create_user(f"r215gcross{i}@example.test",
                                        "R215G-Cross-2026")
            cls.users.append({"user_id": user["id"], "username": user["username"],
                              "token": auth_mod.create_access_token(
                                  user["id"], user["username"])})
        # 与 ImportAIAssistApiTests 同款模板库（来源/批次/文件静态种子）。
        cls._slug = tasks_mod.slugify_screener(cls.users[0]["username"])
        template = cls.client.post("/api/tasks", json={"name": "import-ai-cross-tpl"},
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
        # 注意：模块级 fixture.cleanup() 由文件里第一个类负责（同 StorageFailurePins）。
        cls.client.__exit__(None, None, None)

    def setUp(self):
        os.environ.pop("REVIEWFLOW_AI_KEY", None)
        os.environ.pop("REVIEWFLOW_AI_MODEL_KIND", None)
        # 本地探针端点（127.0.0.1）= 本地模型语义：ai_keys 的回环例外按 desktop
        # 模式放行（sec29 F-5 起 server 模式拦截回环/私网 base_url）；只钉
        # ai_keys 的模式判定，其余 server 行为不受影响。
        _mode = mock.patch.object(ai_keys, "_server_mode", lambda: False)
        _mode.start()
        self.addCleanup(_mode.stop)
        for user in self.users:
            ai_keys.delete_personal_key(user["user_id"], DATA_ROOT)
        _ModelProbe.received = []
        _ModelProbe.replies = []
        _SecondChatProbe.received = []
        _SecondChatProbe.replies = []
        _SecondChatProbe.status = 200
        _JevCrossProbe.received = []
        _JevCrossProbe.replies = []
        self.task = self.client.post("/api/tasks", json={"name": self._testMethodName},
                                     headers=self._auth(0)).json()["task_id"]
        info = tasks_mod.find_task(self.task, DATA_DIR)
        self.db = info.dir / (self._slug + ".db")
        Path(self.db).write_bytes(self._template_db_bytes)
        self.base = f"/api/tasks/{self.task}/import-review/ai-assist"

    # -- 种子与夹具（与既有类同款） -----------------------------------------

    def seed(self, *, metadata: bool = False) -> None:
        """一组待审重复候选 c1（可选拖一条元数据差异提案 m1）。"""
        with closing(wf.connect(self.db)) as conn, conn:
            for key in ("r1", "r2"):
                article = _article(key, title=f"Synthetic {key} therapy trial")
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

    def seed_second_duplicate_group(self) -> None:
        """第二组重复候选 c2（第二源连败不触发主源停机用例需要两组）。"""
        with closing(wf.connect(self.db)) as conn, conn:
            for key in ("r3", "r4"):
                article = _article(key, title=f"Synthetic {key} therapy trial")
                conn.execute(
                    "INSERT INTO workflow_source_records VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (key, "s", key, 1, "01-Wiley.ris", json.dumps(article), json.dumps(article),
                     key, key, None, None, "new", "staged", "now"))
                conn.execute("INSERT INTO workflow_import_members VALUES(?,?,?)", ("b", key, 0))
            conn.execute(
                "INSERT INTO workflow_import_candidates(id,signature,kind,status,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?)", ("c2", "sig-c2", "title", "pending", "now", "now"))
            for key in ("r3", "r4"):
                conn.execute("INSERT INTO workflow_import_candidate_members VALUES(?,?,?)",
                             ("c2", "record", key))

    @staticmethod
    def _spawn(handler) -> str:
        server = HTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        # 由测试方法自行挂 cleanup（本类不依赖实例探针顺序）。
        return server

    def probe(self) -> str:
        server = self._spawn(_ModelProbe)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f"http://127.0.0.1:{server.server_port}/v1"

    def second_probe(self) -> str:
        server = self._spawn(_SecondChatProbe)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f"http://127.0.0.1:{server.server_port}/v1"

    def jev_probe(self) -> str:
        server = self._spawn(_JevCrossProbe)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f"http://127.0.0.1:{server.server_port}"

    def save_key(self, base_url: str) -> None:
        """主源：个人 llm 钥匙（openai 兼容探针端点）。"""
        response = self.client.put(
            "/api/ai/key",
            json={"provider": "openai", "key": FAKE_KEY, "model": "probe-model",
                  "base_url": base_url},
            headers=self._auth(0))
        self.assertEqual(response.status_code, 200, response.text)

    def save_decision_key(self, base_url: str) -> None:
        """decision 钥匙（systemone 协议；⑦ 单源体系用例）。"""
        response = self.client.put(
            "/api/ai/key",
            json={"provider": "typesafe", "key": FAKE_KEY, "kind": "decision",
                  "model": "jev-latest", "base_url": base_url},
            headers=self._auth(0))
        self.assertEqual(response.status_code, 200, response.text)

    def save_shared_llm(self, base_url: str, *, provider: str = "probe-second") -> None:
        """第二源：任务共享 llm 槽钥匙（直写保险柜，与主源不同端点）。"""
        ai_keys.save_shared_key(self.task, provider=provider, key=SHARED_FAKE_KEY,
                                base_url=base_url, model="probe-model-2",
                                kind="llm", data_root=DATA_ROOT)

    def enable(self) -> None:
        settings = self.client.get(f"{self.base}/settings", headers=self._auth(0)).json()
        response = self.client.put(
            f"{self.base}/settings",
            json={"expected_version": settings["version"], "enabled": True},
            headers=self._auth(0))
        self.assertEqual(response.status_code, 200, response.text)

    @staticmethod
    def duplicate_item(item_id: str = "c1") -> dict:
        return {"batch_id": "b", "kind": "duplicate_group", "item_id": item_id}

    @staticmethod
    def metadata_item(item_id: str = "m1") -> dict:
        return {"batch_id": "b", "kind": "metadata_diff", "item_id": item_id}

    def ask(self, items, *, cross_check: bool | None = None):
        body = {"items": items}
        if cross_check is not None:
            body["cross_check"] = cross_check
        return self.client.post(f"{self.base}", json=body, headers=self._auth(0))

    def audit_rows(self) -> list[dict]:
        with closing(wf.connect(self.db, read_only=True)) as conn:
            tables = {row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            if "import_ai_suggestions" not in tables:
                return []
            return [dict(row) for row in conn.execute(
                "SELECT * FROM import_ai_suggestions ORDER BY id")]

    def _both_sources(self) -> tuple[str, str]:
        """主源（个人 llm）+ 第二源（共享 llm 槽，异 provider 异端点）就位。"""
        primary = self.probe()
        second = self.second_probe()
        self.save_key(primary)
        self.save_shared_llm(second)
        self.enable()
        return primary, second

    # -- ① 缺省关：只调主源 -------------------------------------------------

    def test_cross_check_off_calls_primary_only(self):
        self.seed()
        self._both_sources()
        # 字段缺省（老客户端载荷形状）与显式 False 都必须字节级等价：零第二源
        # 出站、结果不带任何 cross_* 痕迹。
        first = self.ask([self.duplicate_item()])
        self.assertEqual(first.status_code, 200, first.text)
        second = self.ask([self.duplicate_item()], cross_check=False)
        self.assertEqual(second.status_code, 200, second.text)
        for response in (first, second):
            row = response.json()["suggestions"][0]
            self.assertEqual(row["verdict"], "same")
            self.assertEqual(row["confidence"], 0.92)
            self.assertFalse(row["degraded"])
            self.assertNotIn("cross_check_unavailable", row["warnings"])
        self.assertEqual(first.json()["suggestions"][0],
                         second.json()["suggestions"][0])
        self.assertEqual(len(_ModelProbe.received), 2)  # 每次请求恰好 1 次主源
        self.assertEqual(_SecondChatProbe.received, [])  # 第二源零出站
        self.assertEqual({row["status"] for row in self.audit_rows()}, {"ok"})

    # -- ② 双源一致：confidence 提升 max+0.1 上限 1.0 ------------------------

    def test_cross_check_agree_same_boosts_confidence_capped(self):
        self.seed()
        self._both_sources()
        # 0.92 vs 0.8 → min(1.0, max(0.92, 0.8)+0.1) = 1.0（触顶）。
        _ModelProbe.replies = [REPLY_SAME]
        _SecondChatProbe.replies = [REPLY_SECOND_SAME]
        response = self.ask([self.duplicate_item()], cross_check=True)
        self.assertEqual(response.status_code, 200, response.text)
        row = response.json()["suggestions"][0]
        self.assertEqual(row["verdict"], "same")
        self.assertEqual(row["confidence"], 1.0)
        self.assertFalse(row["degraded"])
        self.assertEqual(row["warnings"], [])
        # 未触顶档：0.75 vs 0.7 → 0.75+0.1 = 0.85。
        _ModelProbe.replies = [REPLY_SAME_MID]
        _SecondChatProbe.replies = [REPLY_SECOND_SAME_MID]
        again = self.ask([self.duplicate_item()], cross_check=True)
        self.assertEqual(again.status_code, 200, again.text)
        mid = again.json()["suggestions"][0]
        self.assertEqual(mid["verdict"], "same")
        self.assertAlmostEqual(mid["confidence"], 0.85, places=6)
        self.assertFalse(mid["degraded"])
        self.assertEqual(len(_ModelProbe.received), 2)
        self.assertEqual(len(_SecondChatProbe.received), 2)
        self.assertEqual({r["status"] for r in self.audit_rows()}, {"ok"})

    # -- ③ 双源不一致：unsure + degraded + warning ---------------------------

    def test_cross_check_disagree_degrades_to_unsure(self):
        self.seed()
        self._both_sources()
        _ModelProbe.replies = [REPLY_SAME]
        _SecondChatProbe.replies = [REPLY_SECOND_DIFFERENT]
        response = self.ask([self.duplicate_item()], cross_check=True)
        self.assertEqual(response.status_code, 200, response.text)
        row = response.json()["suggestions"][0]
        self.assertEqual(row["verdict"], "unsure")
        self.assertTrue(row["degraded"])
        self.assertIn("cross_check_disagree", row["warnings"])
        self.assertNotIn(SHARED_FAKE_KEY, response.text)
        rows = self.audit_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["verdict"], "unsure")
        self.assertEqual(rows[0]["status"], "degraded")
        self.assertIn("cross_check_disagree",
                      json.loads(rows[0]["warnings_json"]))
        # 审计 model 字段保持主源（第二源只参与合成，不抢署名）。
        self.assertEqual(rows[0]["model"], "probe-model")
        self.assertEqual(rows[0]["provider"], "openai")
        self.assertEqual(len(_ModelProbe.received), 1)
        self.assertEqual(len(_SecondChatProbe.received), 1)

    # -- ④ 共享 llm 槽缺：unavailable 不降级 ---------------------------------

    def test_cross_check_unavailable_without_shared_llm_slot(self):
        self.seed(metadata=True)
        primary = self.probe()
        self.save_key(primary)  # 只有个人 llm 钥匙，任务共享 llm 槽未配
        self.enable()
        _ModelProbe.replies = [REPLY_SAME, REPLY_FIELD_PICKS]
        response = self.ask([self.duplicate_item(), self.metadata_item()],
                            cross_check=True)
        self.assertEqual(response.status_code, 200, response.text)
        suggestions = {row["kind"]: row for row in response.json()["suggestions"]}
        # 重复组：照常出结果，仅追加机器可读提示；不降级。
        duplicate = suggestions["duplicate_group"]
        self.assertEqual(duplicate["verdict"], "same")
        self.assertEqual(duplicate["confidence"], 0.92)
        self.assertFalse(duplicate["degraded"])
        self.assertIn("cross_check_unavailable", duplicate["warnings"])
        # metadata_diff 不在交叉范围：零 cross_* 痕迹。
        metadata = suggestions["metadata_diff"]
        self.assertNotIn("cross_check_unavailable", metadata["warnings"])
        self.assertFalse(any(w.startswith("cross_check")
                             for w in metadata["warnings"]))
        self.assertEqual(len(_ModelProbe.received), 2)
        self.assertEqual(_SecondChatProbe.received, [])
        self.assertEqual({row["status"] for row in self.audit_rows()}, {"ok"})

    # -- ⑤ 共享槽与个人同 provider+base_url：视同无第二源 ---------------------

    def test_cross_check_same_endpoint_is_unavailable(self):
        self.seed()
        primary = self.probe()
        self.save_key(primary)
        # 共享 llm 槽指向与个人钥匙完全相同的 provider+base_url（同一探针）——
        # 若误把同端点当第二源，会对同一端点翻倍出站且“交叉”毫无独立性。
        self.save_shared_llm(primary, provider="openai")
        self.enable()
        response = self.ask([self.duplicate_item()], cross_check=True)
        self.assertEqual(response.status_code, 200, response.text)
        row = response.json()["suggestions"][0]
        self.assertEqual(row["verdict"], "same")
        self.assertEqual(row["confidence"], 0.92)  # 无合成：原置信度原样
        self.assertFalse(row["degraded"])
        self.assertIn("cross_check_unavailable", row["warnings"])
        self.assertEqual(len(_ModelProbe.received), 1)  # 只有主源那一次
        self.assertEqual(_SecondChatProbe.received, [])

    # -- ⑥ 第二源 5xx：照主源结果 + 不触发主源停机 ---------------------------

    def test_cross_check_second_failure_keeps_primary_and_no_breaker(self):
        self.seed()
        self.seed_second_duplicate_group()
        self._both_sources()
        _SecondChatProbe.status = 500
        response = self.ask([self.duplicate_item("c1"), self.duplicate_item("c2")],
                            cross_check=True)
        self.assertEqual(response.status_code, 200, response.text)
        for row in response.json()["suggestions"]:
            self.assertEqual(row["verdict"], "same")
            self.assertEqual(row["confidence"], 0.92)
            self.assertFalse(row["degraded"])
            self.assertIn("cross_check_second_failed", row["warnings"])
        self.assertNotIn(SHARED_FAKE_KEY, response.text)
        # 第二源失败绝不计入主钥匙连败停机：两组条目主源都真实出站
        # （若误计入，第 2 条会被 provider_unavailable 跳过 → 只剩 1 次）。
        self.assertEqual(len(_ModelProbe.received), 2)
        # 第二源 500 属瞬时档：_call_provider 初次+重试 1 次 → 每条 2 次。
        self.assertEqual(len(_SecondChatProbe.received), 4)
        rows = self.audit_rows()
        self.assertEqual(len(rows), 2)
        self.assertEqual({row["status"] for row in rows}, {"ok"})

    # -- ⑦ decision 钥匙：开关被忽略，行为与 false 全等 -----------------------

    def test_cross_check_ignored_for_decision_key(self):
        self.seed()
        jev_base = self.jev_probe()
        second = self.second_probe()
        self.save_decision_key(jev_base)
        # 共享 llm 槽在场也必须被无视（decision 单源体系）。
        self.save_shared_llm(second)
        self.enable()
        enabled = self.ask([self.duplicate_item()], cross_check=True)
        self.assertEqual(enabled.status_code, 200, enabled.text)
        disabled = self.ask([self.duplicate_item()], cross_check=False)
        self.assertEqual(disabled.status_code, 200, disabled.text)
        # 全等：verdict/confidence/warnings/degraded 连同幂等 audit_id。
        self.assertEqual(enabled.json()["suggestions"][0],
                         disabled.json()["suggestions"][0])
        row = enabled.json()["suggestions"][0]
        self.assertEqual(row["verdict"], "same")
        self.assertAlmostEqual(row["confidence"], 0.7, places=6)
        self.assertFalse(row["degraded"])
        self.assertFalse(any(w.startswith("cross_check") for w in row["warnings"]))
        self.assertEqual(len(_JevCrossProbe.received), 2)  # 每次恰好 1 次 systemone
        self.assertEqual({call["path"] for call in _JevCrossProbe.received},
                         {"/v1/systemone"})
        self.assertEqual(_SecondChatProbe.received, [])    # 共享 llm 槽零出站
        self.assertEqual(_ModelProbe.received, [])
        self.assertEqual(len(self.audit_rows()), 1)        # 同摘要幂等一行
        self.assertEqual(self.audit_rows()[0]["model_kind"], "decision")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
