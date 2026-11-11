# -*- coding: utf-8 -*-
"""2.1.5 · 决策模型（Jev 类）钥匙接入红钉（专属新文件，撤实现必红）。

2026-11-10 用户裁定：Jev 决策模型提前到 2.1.5，只用于导入复核 AI 助手的
**重复判断 duplicate_group** 与**合并字段冲突 merge_conflict**；绝不进入
筛选/编码/其他 AI 功能（范围铁律 + 越界硬闸）。

API 形状（2026-11-10 官方文档核实，存证 E:/rf214tmp/jev/）：
- `https://docs.typesafe.ai/api/system-one.md`（内嵌 OpenAPI 3.1 全量规范；
  `https://api.typesafe.ai/openapi.json` 直连在本网络返回 451，文档页为同源
  官方发布）。请求 ``POST /v1/systemone``：``{model, state, questions}``，
  Choice 问题 ``{"type":"choice","instructions":…,"criteria":{名:描述}}``；
  响应 ``{model, answers, usage}``，Choice 答案
  ``{"type":"choice","choice":名,"confidence":0~1,"probabilities":{名:p}}``。
- 交叉验证：Cloudflare Clef（Jev 兼容）文档
  `https://developers.cloudflare.com/workers-ai/models/clef/` 同一词汇
  （state/questions/criteria/choice/probabilities/confidence）。

覆盖（每条在实现前按现状为红）：

1. 钥匙类型读写与存量兼容：PUT/GET ``kind``；缺省=llm；非法 kind 422；
   存量钥匙文件无 kind → 读作 llm；环境变量覆盖可给 decision。
2. duplicate_group / merge_conflict 走 decision 钥匙：请求体形状=已核实
   OpenAPI 模板（本地探针充当 Jev 端点，零真实出站），响应统一形状与
   LLM 适配器一致（verdict/field_picks/confidence/rationale）。
3. 概率→confidence 映射（probabilities[选中项] 优先，answer.confidence
   兜底）与低置信回落 unsure。
4. rationale 模板串（决策模型无理由文本）。
5. 硬闸负钉：decision 钥匙调编码建议（个人/共享/环境变量三条解析路径）
   → 结构化拒绝 + 零出站；钥匙测试端点按协议分派（绝不打 chat/completions）。
6. metadata_diff 走 decision 钥匙 → 不出站、逐条降级 unsure（范围铁律）。
7. 审计行记录 ``model_kind``（decision/llm）+ 决策版 prompt_version；
   既有审计表（无该列）幂等加列并回填 llm。
8. llm 钥匙行为零回归（同一端点、旧协议、旧行为）。

第 2 轮评审修复钉：

9. 【BLOCKER】decision 钥匙不填 base_url → 落盘/端点/env 三路径缺省都是
   ``https://api.typesafe.ai``（绝不写成 OpenAI 缺省把 TypeSafe 钥匙发往
   api.openai.com）；llm 缺省回归不变。
10. 【BLOCKER】瞬时错误（5xx/429）重试 1 次=共 2 次出站调用；非瞬时（4xx）
    不重试。
11. 【中】assist 顶层 ``prompt_version``/``prompt_versions`` 按钥匙 kind
    返回（decision 时 metadata_diff=null=该 kind 不出站）；llm 零回归。
12. 【低】decision 钥匙下 metadata_diff 零出站降级行审计 ``prompt_version``
    记 ``n/a``（从未出站、不属于任何版本域），digest 幂等。
13. 【观察采纳】置信映射=概率与 answer.confidence **两信号取小**（概率 0.65
    × confidence 0.475 → 0.475 → unsure）；单信号可用时用该方。
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

fixture = tempfile.TemporaryDirectory(prefix="rf_215jev_")
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
from custom_backend import decision_model_adapter as decision  # noqa: E402
from custom_backend import import_ai_assist_api  # noqa: E402
from custom_backend import auth as auth_mod  # noqa: E402
from custom_backend.main import DATA_DIR, app  # noqa: E402

FAKE_KEY = "sk-jev-decision-abcdef123456"
DATA_ROOT = Path(DATA_DIR)

#: 埋在 raw/url 里、绝不许随出站载荷离开服务端的哨兵串。
SECRET_NOTES = "SECRET-NOTES-JEV"
SECRET_FULLTEXT = "SECRET-FULLTEXT-JEV"
SECRET_DECISION = "SECRET-SCREEN-DECISION-JEV"

ALLOWED_KEYS = {"title", "authors", "year", "journal", "doi", "abstract"}

#: LLM 通道既有回复（零回归用例）。
REPLY_LLM_SAME = json.dumps({
    "verdict": "same", "confidence": 0.92,
    "rationale_zh": "题名、作者、年份与 DOI 一致，指向同一篇文献。",
}, ensure_ascii=False)


def _choice(choice: str, probabilities: dict | None, confidence=0.7) -> dict:
    """按已核实的 OpenAPI ChoiceAnswer 形状构造答案。"""
    answer: dict = {"type": "choice", "choice": choice}
    if probabilities is not None:
        answer["probabilities"] = probabilities
    answer["confidence"] = confidence
    return answer


def _jev_body(answers: dict) -> dict:
    return {"model": "jev-latest", "answers": answers,
            "usage": {"input_tokens": 90, "output_tokens": 9}}


#: duplicate_group：选中 duplicate，probabilities[duplicate]=0.91（映射置信度）。
JEV_DUPLICATE_SAME = _jev_body({"duplicate_verdict": _choice(
    "duplicate", {"duplicate": 0.91, "include": 0.06, "unsure": 0.03}, 0.7)})
#: 选中 include → 统一 verdict=different。
JEV_INCLUDE_DIFFERENT = _jev_body({"duplicate_verdict": _choice(
    "include", {"duplicate": 0.05, "include": 0.9, "unsure": 0.05}, 0.8)})
#: 模型自答 unsure。
JEV_UNSURE = _jev_body({"duplicate_verdict": _choice(
    "unsure", {"duplicate": 0.2, "include": 0.2, "unsure": 0.6}, 0.2)})
#: 选中 duplicate 但概率 0.45 < 0.6 → 低置信回落 unsure。
JEV_LOW_CONFIDENCE = _jev_body({"duplicate_verdict": _choice(
    "duplicate", {"duplicate": 0.45, "include": 0.4, "unsure": 0.15}, 0.1)})
#: probabilities 缺失 → 兜底 answer.confidence=0.8。
JEV_PROB_MISSING = _jev_body({"duplicate_verdict": _choice(
    "duplicate", None, 0.8)})
#: 候选项不合法（不在 criteria 里）→ invalid_verdict。
JEV_INVALID_CHOICE = _jev_body({"duplicate_verdict": _choice(
    "banana", {"duplicate": 0.3, "include": 0.3, "unsure": 0.4}, 0.0)})
#: 响应缺 answers → decision_response_invalid。
JEV_NO_ANSWERS = {"model": "jev-latest", "usage": {"input_tokens": 1, "output_tokens": 1}}

#: merge_conflict：四个出站字段各自作答。映射置信度=两信号取小：
#: 概率(0.8/0.85/0.9/0.75) 与 confidence(0.95) → min=0.8/0.85/0.9/0.75，
#: 整体再取各字段最小=0.75。
JEV_MERGE_PICKS = _jev_body({
    "title": _choice("left", {"left": 0.8, "right": 0.15, "unsure": 0.05}, 0.95),
    "authors": _choice("right", {"left": 0.1, "right": 0.85, "unsure": 0.05}, 0.95),
    "journal": _choice("right", {"left": 0.05, "right": 0.9, "unsure": 0.05}, 0.95),
    "year": _choice("unsure", {"left": 0.1, "right": 0.15, "unsure": 0.75}, 0.95),
})
#: title 字段映射置信度取小=min(0.5, 0.4)=0.4 < 0.6 → 整体 low_confidence 全 unsure。
JEV_MERGE_LOW = _jev_body({
    "title": _choice("left", {"left": 0.5, "right": 0.3, "unsure": 0.2}, 0.4),
    "authors": _choice("right", {"left": 0.1, "right": 0.9, "unsure": 0.0}, 0.8),
    "journal": _choice("right", {"left": 0.05, "right": 0.9, "unsure": 0.05}, 0.8),
    "year": _choice("unsure", {"left": 0.1, "right": 0.15, "unsure": 0.75}, 0.5),
})


class _JevProbe(BaseHTTPRequestHandler):
    """本地 Jev(systemone) 端点探针（不打真网）。"""

    replies: list[dict] = []
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
        reply = type(self).replies.pop(0) if type(self).replies else JEV_DUPLICATE_SAME
        payload = json.dumps(reply, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):  # noqa: A003  静默探针日志
        return


class _ChatProbe(BaseHTTPRequestHandler):
    """本地 OpenAI 兼容端点探针（llm 钥匙零回归用例）。"""

    replies: list[str] = []
    received: list[dict] = []

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        body = json.loads(raw.decode("utf-8", "replace") or "{}")
        type(self).received.append({
            "path": self.path,
            "json": body,
            "raw": raw.decode("utf-8", "replace"),
        })
        reply = type(self).replies.pop(0) if type(self).replies else REPLY_LLM_SAME
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

    def log_message(self, *args):  # noqa: A003
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


class DecisionModelTests(unittest.TestCase):
    @classmethod
    def _auth(cls, index=0):
        return {"Authorization": "Bearer " + cls.users[index]["token"]}

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.client.__enter__()
        cls.users = []
        for i in range(3):
            user = auth_mod.create_user(f"r215jev{i}@example.test", "R215Jev-2026")
            cls.users.append({"user_id": user["id"], "username": user["username"],
                              "token": auth_mod.create_access_token(user["id"], user["username"])})
        # 门禁墙钟（类级模板库，同 test_2_1_5_import_ai_assist 手法）。
        cls._slug = tasks_mod.slugify_screener(cls.users[0]["username"])
        template = cls.client.post("/api/tasks", json={"name": "jev-template"},
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
        # 编码建议端点的文献种子（硬闸负钉需要一篇有摘要的文献；在连接关闭后
        # 再开第二个连接写，避免同文件双连接锁冲突——同 test_ai_suggest 手法）。
        db_mod.upsert_articles(ctx0.owner_db, [Article(
            zotero_key="A1", title="Trial of Example Therapy",
            authors="Doe J; Smith A", journal="J Example", year=2024, doi="10.test/ai",
            abstract="Background: example. Methods: a randomised controlled trial.")], [])
        cls._template_db_bytes = Path(ctx0.owner_db).read_bytes()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        fixture.cleanup()

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
        _JevProbe.received = []
        _JevProbe.replies = []
        _ChatProbe.received = []
        _ChatProbe.replies = []
        self.task = self.client.post("/api/tasks", json={"name": self._testMethodName},
                                     headers=self._auth(0)).json()["task_id"]
        info = tasks_mod.find_task(self.task, DATA_DIR)
        self.db = info.dir / (self._slug + ".db")
        Path(self.db).write_bytes(self._template_db_bytes)
        self.base = f"/api/tasks/{self.task}/import-review/ai-assist"
        self.suggest_base = f"/api/tasks/{self.task}/ai-suggest"
        self.coding = f"/api/tasks/{self.task}/coding/ai-suggestions"

    # -- 种子（SQL 全部字面量，同 test_2_1_5_import_ai_assist 惯例） --------

    def seed_duplicate_group(self):
        """一组待审重复候选 c1（两条待审记录）。"""
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
            conn.execute("INSERT INTO workflow_import_candidate_members VALUES(?,?,?)",
                         ("c1", "record", "r1"))
            conn.execute("INSERT INTO workflow_import_candidate_members VALUES(?,?,?)",
                         ("c1", "record", "r2"))

    def seed_second_duplicate_group(self):
        """第二组重复候选 c2（连续失败停出站用例需要两组条目）。"""
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
            conn.execute("INSERT INTO workflow_import_candidate_members VALUES(?,?,?)",
                         ("c2", "record", "r3"))
            conn.execute("INSERT INTO workflow_import_candidate_members VALUES(?,?,?)",
                         ("c2", "record", "r4"))

    def seed_merge_conflict(self):
        """保留者（已入任务 K1）+ 一条被合并记录 R1：title/authors/journal/year
        四个冲突留给模型层，DOI 被规则层判定、url/item_type 永不出站（同 g5 种子）。"""
        keeper = _article("K1", title="Keeper concise title", journal="Journal of Testing",
                          doi="https://doi.org/10.1000/ABC", year=2023)
        keeper["authors"] = "Rivera, Ana; Chen, Mei"
        merged = _article("R1", title="Merged fuller title about teachers",
                          journal="TESTING QUARTERLY", doi="10.1000/abc", year=2024)
        merged["authors"] = "Rivera, Ana; Chen, Mei; Park, J"
        with closing(wf.connect(self.db)) as conn, conn:
            conn.execute(
                "INSERT INTO articles(zotero_key,item_type,title,authors,journal,year,"
                "doi,abstract,url,import_order) VALUES(?,?,?,?,?,?,?,?,?,?)",
                ("K1", keeper["item_type"], keeper["title"], keeper["authors"],
                 keeper["journal"], keeper["year"], keeper["doi"], keeper["abstract"],
                 keeper["url"], 1))
            conn.execute(
                "INSERT INTO workflow_source_records VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("R1", "s", "R1", 1, "01-Wiley.ris", json.dumps(merged), json.dumps(merged),
                 "R1", "R1", None, None, "new", "staged", "now"))
            conn.execute("INSERT INTO workflow_import_members VALUES(?,?,?)", ("b", "R1", 0))
            conn.execute(
                "INSERT INTO workflow_import_candidates(id,signature,kind,status,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?)", ("c1", "sig-c1", "title", "pending", "now", "now"))
            conn.execute("INSERT INTO workflow_import_candidate_members VALUES(?,?,?)",
                         ("c1", "active", "K1"))
            conn.execute("INSERT INTO workflow_import_candidate_members VALUES(?,?,?)",
                         ("c1", "record", "R1"))

    def seed_metadata_diff(self):
        incoming = _article("m1", title="Synthetic m1 therapy trial", journal="J New")
        before = _article("EXIST", title="Synthetic m1 therapy trial", journal="J Old")
        with closing(wf.connect(self.db)) as conn, conn:
            conn.execute(
                "INSERT INTO workflow_source_records VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("m1", "s", "m1", 2, "01-Wiley.ris", json.dumps(incoming),
                 json.dumps(incoming), "m1", "m1", "EXIST", None, "new", "staged", "now"))
            conn.execute("INSERT INTO workflow_import_members VALUES(?,?,?)", ("b", "m1", 0))
            conn.execute(
                "INSERT INTO workflow_import_metadata(record_id,canonical_key,before_json,incoming_json) "
                "VALUES(?,?,?,?)", ("m1", "EXIST", json.dumps(before), json.dumps(incoming)))

    def jev_probe(self) -> str:
        server = HTTPServer(("127.0.0.1", 0), _JevProbe)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f"http://127.0.0.1:{server.server_port}"

    def chat_probe(self) -> str:
        server = HTTPServer(("127.0.0.1", 0), _ChatProbe)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f"http://127.0.0.1:{server.server_port}/v1"

    def save_decision_key(self, base_url: str | None = None, user_index: int = 0,
                          model: str = "jev-latest"):
        body = {"provider": "typesafe", "key": FAKE_KEY, "kind": "decision", "model": model}
        if base_url:
            body["base_url"] = base_url
        response = self.client.put("/api/ai/key", json=body, headers=self._auth(user_index))
        self.assertEqual(response.status_code, 200, response.text)
        return response

    def save_llm_key(self, base_url: str | None = None, user_index: int = 0):
        body = {"provider": "openai", "key": FAKE_KEY, "model": "probe-model"}
        if base_url:
            body["base_url"] = base_url
        response = self.client.put("/api/ai/key", json=body, headers=self._auth(user_index))
        self.assertEqual(response.status_code, 200, response.text)

    def enable(self):
        settings = self.client.get(f"{self.base}/settings", headers=self._auth(0)).json()
        response = self.client.put(
            f"{self.base}/settings",
            json={"expected_version": settings["version"], "enabled": True},
            headers=self._auth(0))
        self.assertEqual(response.status_code, 200, response.text)

    def ask(self, items, user_index=0):
        return self.client.post(self.base, json={"items": items},
                                headers=self._auth(user_index))

    @staticmethod
    def duplicate_item(item_id="c1"):
        return {"batch_id": "b", "kind": "duplicate_group", "item_id": item_id}

    @staticmethod
    def merge_item(item_id="c1"):
        return {"batch_id": "b", "kind": "merge_conflict", "item_id": item_id}

    @staticmethod
    def metadata_item(item_id="m1"):
        return {"batch_id": "b", "kind": "metadata_diff", "item_id": item_id}

    def audit_rows(self) -> list[dict]:
        with closing(wf.connect(self.db, read_only=True)) as conn:
            tables = {row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            if "import_ai_suggestions" not in tables:
                return []
            return [dict(row) for row in conn.execute(
                "SELECT * FROM import_ai_suggestions ORDER BY id")]

    def arm_zero_outbound(self):
        """双通道零出站哨兵：LLM 与决策模型出站函数一概禁用。"""
        for target, name in ((ai_suggest_api, "_provider_generate"),
                             (decision, "post_decision")):
            guard = mock.patch.object(target, name,
                                      side_effect=AssertionError(name + " 不得被调用"))
            guard.start()
            self.addCleanup(guard.stop)

    # ------------------------------------------------------------------
    # 1. 钥匙类型读写与存量兼容
    # ------------------------------------------------------------------

    def test_key_kind_roundtrip_default_and_validation(self):
        # 显式 decision：写入并回读。
        response = self.save_decision_key()
        self.assertEqual(response.json()["kind"], "decision")
        self.assertEqual(self.client.get("/api/ai/key", headers=self._auth(0)
                                         ).json()["kind"], "decision")
        # 不带 kind（旧客户端）→ 默认 llm，既有行为不变。
        self.client.put("/api/ai/key", json={"provider": "openai", "key": FAKE_KEY},
                        headers=self._auth(0))
        self.assertEqual(self.client.get("/api/ai/key", headers=self._auth(0)
                                         ).json()["kind"], "llm")
        # 非法 kind → 422（pydantic Literal），且不落盘。
        response = self.client.put("/api/ai/key", json={
            "provider": "openai", "key": FAKE_KEY, "kind": "bogus"},
            headers=self._auth(0))
        self.assertEqual(response.status_code, 422, response.text)

    def test_legacy_key_without_kind_reads_as_llm(self):
        # 存量钥匙文件（无 kind 字段）→ 读作 llm（兼容）。
        vault = ai_keys.personal_path(self.users[0]["user_id"], DATA_ROOT)
        vault.parent.mkdir(parents=True, exist_ok=True)
        vault.write_text(json.dumps({
            "provider": "openai", "key": FAKE_KEY, "base_url": None,
            "model": "probe-model", "added_at": "2026-01-01", "last_used_at": None,
        }), encoding="utf-8")
        self.assertEqual(ai_keys.kind_of(ai_keys.load_personal_key(
            self.users[0]["user_id"], DATA_ROOT)), "llm")
        self.assertEqual(self.client.get("/api/ai/key", headers=self._auth(0)
                                         ).json()["kind"], "llm")

    def test_env_override_can_be_decision_kind(self):
        os.environ["REVIEWFLOW_AI_KEY"] = FAKE_KEY
        os.environ["REVIEWFLOW_AI_MODEL_KIND"] = "decision"
        self.addCleanup(os.environ.pop, "REVIEWFLOW_AI_KEY", None)
        self.addCleanup(os.environ.pop, "REVIEWFLOW_AI_MODEL_KIND", None)
        self.assertEqual(self.client.get("/api/ai/key", headers=self._auth(0)
                                         ).json()["kind"], "decision")
        self.assertEqual(ai_keys.kind_of(ai_keys.env_key()), "decision")
        # 非法环境值 → 读作 llm（读路径绝不抛错）。
        os.environ["REVIEWFLOW_AI_MODEL_KIND"] = "not-a-kind"
        self.assertEqual(ai_keys.kind_of(ai_keys.env_key()), "llm")

    def test_settings_view_exposes_key_kind(self):
        self.seed_duplicate_group()
        base = self.jev_probe()
        self.save_decision_key(base)
        self.enable()
        view = self.client.get(f"{self.base}/settings", headers=self._auth(0)).json()
        self.assertEqual(view["key_kind"], "decision")
        self.assertEqual(view["model"], "jev-latest")

    # ------------------------------------------------------------------
    # 2. 请求体形状（已核实 OpenAPI 模板）+ 响应统一形状
    # ------------------------------------------------------------------

    def test_duplicate_group_decision_request_and_unified_response(self):
        self.seed_duplicate_group()
        base = self.jev_probe()
        self.save_decision_key(base)
        self.enable()
        response = self.ask([self.duplicate_item()])
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(_JevProbe.received), 1)
        call = _JevProbe.received[0]
        # 端点路径与协议头。
        self.assertEqual(call["path"], "/v1/systemone")
        self.assertEqual(call["headers"].get("authorization"), "Bearer " + FAKE_KEY)
        self.assertNotIn("typesafe-danger-zone-images", call["headers"])
        # 请求体 = 已核实形状：model + state + questions（Choice）。
        body = call["json"]
        self.assertEqual(set(body), {"model", "state", "questions"})
        self.assertEqual(body["model"], "jev-latest")
        self.assertEqual(body["state"]["kind"], "duplicate_group")
        question = body["questions"]["duplicate_verdict"]
        self.assertEqual(question["type"], "choice")
        self.assertEqual(set(question["criteria"]), {"duplicate", "include", "unsure"})
        self.assertTrue(question["instructions"])
        # 出站载荷负钉：state 只含白名单题录键，哨兵串一个字符都不外发。
        for member in body["state"]["members"]:
            self.assertEqual(set(member), ALLOWED_KEYS, member)
        for secret in (SECRET_NOTES, SECRET_FULLTEXT, SECRET_DECISION,
                       "example.test/landing"):
            self.assertNotIn(secret, call["raw"])
        # 响应统一形状（与 LLM 适配器一致）。置信度=两信号取小：min(0.91, 0.7)=0.7。
        suggestion = response.json()["suggestions"][0]
        self.assertEqual(suggestion["verdict"], "same")
        self.assertEqual(suggestion["confidence"], 0.7)
        self.assertIsNone(suggestion["field_picks"])
        self.assertFalse(suggestion["degraded"])
        self.assertEqual(suggestion["warnings"], [])
        self.assertEqual(response.json()["model_kind"], "decision")

    def test_duplicate_group_decision_choice_include_maps_to_different(self):
        self.seed_duplicate_group()
        base = self.jev_probe()
        _JevProbe.replies = [JEV_INCLUDE_DIFFERENT]
        self.save_decision_key(base)
        self.enable()
        suggestion = self.ask([self.duplicate_item()]).json()["suggestions"][0]
        self.assertEqual(suggestion["verdict"], "different")
        self.assertEqual(suggestion["confidence"], 0.8)  # min(0.9, 0.8)
        self.assertFalse(suggestion["degraded"])

    def test_merge_conflict_decision_request_and_unified_response(self):
        self.seed_merge_conflict()
        base = self.jev_probe()
        _JevProbe.replies = [JEV_MERGE_PICKS]
        self.save_decision_key(base)
        self.enable()
        response = self.ask([self.merge_item()])
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(_JevProbe.received), 1)
        call = _JevProbe.received[0]
        self.assertEqual(call["path"], "/v1/systemone")
        body = call["json"]
        # 出站冲突字段 = 规则层判不出的六字段白名单冲突（title/authors/journal/year）；
        # DOI 已被规则层判定、url/item_type 永不出站。
        asked = set(body["questions"])
        self.assertEqual(asked, {"title", "authors", "journal", "year"})
        for field, question in body["questions"].items():
            self.assertEqual(question["type"], "choice")
            self.assertEqual(set(question["criteria"]), {"left", "right", "unsure"})
        conflicts = {row["field"]: row for row in body["state"]["conflicts"]}
        self.assertEqual(set(conflicts), asked)
        for row in conflicts.values():
            self.assertEqual(set(row), {"field", "left", "right"})
        for secret in (SECRET_NOTES, SECRET_FULLTEXT, SECRET_DECISION,
                       "example.test/landing", "10.1000"):
            self.assertNotIn(secret, call["raw"])
        # 统一形状：picks + min 概率置信度；逐字段 prefill/source 与 LLM 一致。
        # field_picks 是全部冲突字段的扁平镜像：DOI 由规则层判定（prefill=right）
        # 也出现在镜像里（契约：规则层判定不受模型影响）。
        suggestion = response.json()["suggestions"][0]
        self.assertIsNone(suggestion["verdict"])
        self.assertEqual(suggestion["confidence"], 0.75)
        self.assertEqual(suggestion["field_picks"],
                         {"title": "left", "authors": "right", "journal": "right",
                          "year": "unsure", "doi": "right"})
        by_field = {row["field"]: row for row in suggestion["fields"]}
        self.assertEqual(by_field["title"]["prefill"], "left")
        self.assertEqual(by_field["title"]["source"], "ai")
        self.assertEqual(by_field["year"]["fallback"], "default")
        # DOI 由规则层判定（零出站），不受模型影响。
        doi_row = next(row for row in suggestion["fields"] if row["field"] == "doi")
        self.assertEqual(doi_row["source"], "rule")
        self.assertFalse(suggestion["degraded"])

    def test_merge_conflict_rule_only_never_calls_decision_endpoint(self):
        self.seed_merge_conflict()
        # 只留 DOI 冲突（规则层可判）→ 决策端点一次都不调用。
        with closing(wf.connect(self.db)) as conn, conn:
            row = conn.execute(
                "SELECT article_json FROM workflow_source_records WHERE id='R1'").fetchone()
            article = json.loads(row[0])
            article.update({"title": "Keeper concise title", "authors": "Rivera, Ana; Chen, Mei",
                            "journal": "Journal of Testing", "year": 2023})
            conn.execute("UPDATE workflow_source_records SET article_json=? WHERE id='R1'",
                         (json.dumps(article),))
        self.arm_zero_outbound()
        self.save_decision_key()
        self.enable()
        response = self.ask([self.merge_item()])
        self.assertEqual(response.status_code, 200, response.text)
        suggestion = response.json()["suggestions"][0]
        self.assertEqual(_JevProbe.received, [])
        doi_row = next(row for row in suggestion["fields"] if row["field"] == "doi")
        self.assertEqual(doi_row["source"], "rule")

    # ------------------------------------------------------------------
    # 3. 概率→confidence 映射与低置信回落
    # ------------------------------------------------------------------

    def test_probability_maps_to_confidence_and_low_confidence_falls_back(self):
        self.seed_duplicate_group()
        base = self.jev_probe()
        _JevProbe.replies = [JEV_LOW_CONFIDENCE, JEV_PROB_MISSING,
                             JEV_INVALID_CHOICE, JEV_NO_ANSWERS]
        self.save_decision_key(base)
        self.enable()
        # 概率 0.45 < 0.6 → unsure + low_confidence。
        first = self.ask([self.duplicate_item()]).json()["suggestions"][0]
        self.assertEqual(first["verdict"], "unsure")
        self.assertTrue(first["degraded"])
        self.assertIn("low_confidence", first["warnings"])
        self.assertIn("无法判断", first["rationale_zh"])
        # probabilities 缺失 → 兜底 answer.confidence=0.8，结论保留。
        second = self.ask([self.duplicate_item()]).json()["suggestions"][0]
        self.assertEqual(second["verdict"], "same")
        self.assertEqual(second["confidence"], 0.8)
        self.assertFalse(second["degraded"])
        # 候选项不合法 → invalid_verdict unsure。
        third = self.ask([self.duplicate_item()]).json()["suggestions"][0]
        self.assertEqual(third["verdict"], "unsure")
        self.assertIn("invalid_verdict", third["warnings"])
        # 响应缺 answers → decision_response_invalid unsure。
        fourth = self.ask([self.duplicate_item()]).json()["suggestions"][0]
        self.assertEqual(fourth["verdict"], "unsure")
        self.assertIn("decision_response_invalid", fourth["warnings"])

    def test_merge_lowest_field_probability_degrades_whole_ai_layer(self):
        self.seed_merge_conflict()
        base = self.jev_probe()
        _JevProbe.replies = [JEV_MERGE_LOW]
        self.save_decision_key(base)
        self.enable()
        suggestion = self.ask([self.merge_item()]).json()["suggestions"][0]
        self.assertTrue(suggestion["degraded"])
        self.assertIn("low_confidence", suggestion["warnings"])
        self.assertEqual(suggestion["confidence"], 0.4)  # min(各字段 min(概率,confidence))
        # 任一字段低置信 → 整个 AI 层 unsure：ai_pick 不落、prefill 不给。
        for row in suggestion["fields"]:
            if row["field"] in ("title", "authors", "journal", "year"):
                self.assertIsNone(row["ai_pick"])
                self.assertIsNone(row["prefill"])
                self.assertEqual(row["fallback"], "default")
        # 规则层判定不受模型置信影响：DOI 仍给出预填（right → 被合并记录 R1）。
        self.assertEqual(suggestion["suggested_conflict_picks"], {"doi": "R1"})

    def test_provider_failure_degrades_to_unsure_and_stops_outbound(self):
        self.seed_duplicate_group()
        self.seed_second_duplicate_group()
        # 连续失败达到上限（2）后剩余条目不再出站 → 需要第三组条目。
        with closing(wf.connect(self.db)) as conn, conn:
            for key in ("r5", "r6"):
                article = _article(key, title=f"Synthetic {key} therapy trial")
                conn.execute(
                    "INSERT INTO workflow_source_records VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (key, "s", key, 1, "01-Wiley.ris", json.dumps(article), json.dumps(article),
                     key, key, None, None, "new", "staged", "now"))
                conn.execute("INSERT INTO workflow_import_members VALUES(?,?,?)", ("b", key, 0))
            conn.execute(
                "INSERT INTO workflow_import_candidates(id,signature,kind,status,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?)", ("c3", "sig-c3", "title", "pending", "now", "now"))
            conn.execute("INSERT INTO workflow_import_candidate_members VALUES(?,?,?)",
                         ("c3", "record", "r5"))
            conn.execute("INSERT INTO workflow_import_candidate_members VALUES(?,?,?)",
                         ("c3", "record", "r6"))
        self.save_decision_key()
        self.enable()
        with mock.patch.object(decision, "post_decision",
                               side_effect=ai_suggest_api.ProviderError(
                                   "决策模型端点 HTTP 503（详情不回显）", status=503)):
            response = self.ask([self.duplicate_item(), self.duplicate_item("c2"),
                                 self.duplicate_item("c3")])
        self.assertEqual(response.status_code, 200, response.text)
        suggestions = response.json()["suggestions"]
        self.assertEqual([row["verdict"] for row in suggestions],
                         ["unsure", "unsure", "unsure"])
        self.assertTrue(all(row["degraded"] for row in suggestions))
        self.assertIn("provider_error", suggestions[0]["warnings"])
        self.assertIn("provider_error", suggestions[1]["warnings"])
        # 连续失败达到上限后剩余条目不再出站。
        self.assertIn("provider_unavailable", suggestions[2]["warnings"])

    # ------------------------------------------------------------------
    # 4. rationale 模板串（决策模型无理由文本）
    # ------------------------------------------------------------------

    def test_rationale_is_template_string(self):
        self.seed_duplicate_group()
        base = self.jev_probe()
        _JevProbe.replies = [JEV_DUPLICATE_SAME, JEV_UNSURE, JEV_LOW_CONFIDENCE]
        self.save_decision_key(base)
        self.enable()
        confident = self.ask([self.duplicate_item()]).json()["suggestions"][0]
        self.assertEqual(confident["rationale_zh"], "决策模型判定，置信 0.70。")
        model_unsure = self.ask([self.duplicate_item()]).json()["suggestions"][0]
        self.assertEqual(model_unsure["verdict"], "unsure")
        self.assertFalse(model_unsure["degraded"])
        self.assertEqual(model_unsure["rationale_zh"], "决策模型判定无法判断，置信 0.20。")
        low = self.ask([self.duplicate_item()]).json()["suggestions"][0]
        self.assertIn("无法判断", low["rationale_zh"])
        self.assertIn("0.10", low["rationale_zh"])

    # ------------------------------------------------------------------
    # 5. 越界硬闸负钉（范围铁律）
    # ------------------------------------------------------------------

    def _enable_ai_suggest(self, key_mode="personal"):
        settings = self.client.get(f"{self.suggest_base}/settings",
                                   headers=self._auth(0)).json()
        response = self.client.put(f"{self.suggest_base}/settings", json={
            "expected_version": settings["version"], "enabled": True,
            "key_mode": key_mode,
            "allowed_field_ids": ["study_design"],
            "context_prompt": ""}, headers=self._auth(0))
        self.assertEqual(response.status_code, 200, response.text)

    def test_decision_key_rejected_on_coding_suggestions_personal(self):
        self._enable_ai_suggest()
        self.save_decision_key()
        self.arm_zero_outbound()
        response = self.client.post(self.coding, json={
            "zotero_key": "A1", "field_ids": ["study_design"]},
            headers=self._auth(0))
        self.assertEqual(response.status_code, 409, response.text)
        detail = response.json()["detail"]
        self.assertEqual(detail["code"], "ai_decision_key_not_allowed")
        self.assertEqual(detail["message_key"], "ai.decision_key_not_allowed")
        self.assertEqual(detail["params"]["endpoint"], "coding/ai-suggestions")
        self.assertNotIn(FAKE_KEY, response.text)

    def test_decision_key_rejected_on_coding_suggestions_shared_and_env(self):
        self._enable_ai_suggest()
        # 共享 decision 钥匙（owner 配置；本人无个人钥匙 → 解析到共享）。
        response = self.client.put(f"{self.suggest_base}/shared-key", json={
            "provider": "typesafe", "key": FAKE_KEY, "kind": "decision",
            "model": "jev-latest"}, headers=self._auth(0))
        self.assertEqual(response.status_code, 200, response.text)
        self._enable_ai_suggest(key_mode="shared")
        self.arm_zero_outbound()
        response = self.client.post(self.coding, json={
            "zotero_key": "A1", "field_ids": ["study_design"]},
            headers=self._auth(0))
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["detail"]["code"], "ai_decision_key_not_allowed")
        # 环境变量 decision 钥匙同样被拒。
        self.client.delete(f"{self.suggest_base}/shared-key", headers=self._auth(0))
        os.environ["REVIEWFLOW_AI_KEY"] = FAKE_KEY
        os.environ["REVIEWFLOW_AI_MODEL_KIND"] = "decision"
        self.addCleanup(os.environ.pop, "REVIEWFLOW_AI_KEY", None)
        self.addCleanup(os.environ.pop, "REVIEWFLOW_AI_MODEL_KIND", None)
        response = self.client.post(self.coding, json={
            "zotero_key": "A1", "field_ids": ["study_design"]},
            headers=self._auth(0))
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["detail"]["code"], "ai_decision_key_not_allowed")

    def test_key_test_endpoint_dispatches_by_kind_and_never_chats(self):
        base = self.jev_probe()
        self.save_decision_key(base)
        with mock.patch.object(ai_suggest_api, "_provider_generate",
                               side_effect=AssertionError("decision 钥匙不得走 chat/completions")):
            response = self.client.post("/api/ai/key/test", headers=self._auth(0))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["ok"])
        self.assertEqual(response.json()["model"], "jev-latest")
        self.assertEqual(len(_JevProbe.received), 1)
        self.assertEqual(_JevProbe.received[0]["path"], "/v1/systemone")
        self.assertEqual(_JevProbe.received[0]["json"]["questions"]["ping"]["type"],
                         "choice")

    def test_metadata_diff_with_decision_key_never_outbounds(self):
        self.seed_metadata_diff()
        base = self.jev_probe()
        self.save_decision_key(base)
        self.enable()
        self.arm_zero_outbound()
        response = self.ask([self.metadata_item()])
        self.assertEqual(response.status_code, 200, response.text)
        suggestion = response.json()["suggestions"][0]
        self.assertEqual(suggestion["verdict"], "unsure")
        self.assertTrue(suggestion["degraded"])
        self.assertIn("decision_kind_not_supported", suggestion["warnings"])
        self.assertIn("无法判断", suggestion["rationale_zh"])
        self.assertEqual(_JevProbe.received, [])

    # ------------------------------------------------------------------
    # 6. 审计行 model_kind 与迁移
    # ------------------------------------------------------------------

    def test_audit_rows_record_model_kind_and_decision_prompt_version(self):
        self.seed_duplicate_group()
        self.seed_metadata_diff()
        base = self.jev_probe()
        _JevProbe.replies = [JEV_DUPLICATE_SAME]
        self.save_decision_key(base)
        self.enable()
        response = self.ask([self.duplicate_item()])
        self.assertEqual(response.status_code, 200, response.text)
        audit_id = response.json()["suggestions"][0]["audit_id"]
        rows = self.audit_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["model_kind"], "decision")
        self.assertEqual(rows[0]["prompt_version"], "import-ai-assist-decision/v1")
        self.assertEqual(rows[0]["verdict"], "same")
        view = self.client.get(f"{self.base}/audit/{audit_id}",
                               headers=self._auth(0)).json()
        self.assertEqual(view["model_kind"], "decision")
        # llm 钥匙同一端点 → model_kind=llm、旧 prompt_version（零回归）。
        self.save_llm_key(self.chat_probe())
        _ChatProbe.replies = [REPLY_LLM_SAME]
        response = self.ask([self.duplicate_item()])
        self.assertEqual(response.status_code, 200, response.text)
        rows = self.audit_rows()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1]["model_kind"], "llm")
        self.assertEqual(rows[1]["prompt_version"], "import-ai-assist/v1")
        self.assertEqual(rows[1]["verdict"], "same")

    def test_audit_table_legacy_columns_backfilled_as_llm(self):
        """既有审计表（无 model_kind 列）→ 幂等加列，旧行回填 llm，新行可写。"""
        self.seed_duplicate_group()
        with closing(wf.connect(self.db)) as conn, conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS import_ai_suggestions("
                "id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "task_id TEXT NOT NULL,"
                "batch_id TEXT NOT NULL,"
                "item_kind TEXT NOT NULL CHECK(item_kind IN ('duplicate_group','metadata_diff','merge_conflict')),"
                "item_id TEXT NOT NULL,"
                "provider TEXT NOT NULL,"
                "model TEXT NOT NULL,"
                "prompt_version TEXT NOT NULL,"
                "request_digest TEXT NOT NULL,"
                "verdict TEXT NOT NULL CHECK(verdict IN ('same','different','unsure','n/a')),"
                "confidence REAL,"
                "rationale_zh TEXT NOT NULL,"
                "field_picks_json TEXT,"
                "warnings_json TEXT NOT NULL DEFAULT '[]',"
                "status TEXT NOT NULL CHECK(status IN ('ok','degraded')),"
                "created_at TEXT NOT NULL,"
                "updated_at TEXT NOT NULL)")
            conn.execute(
                "INSERT INTO import_ai_suggestions(task_id,batch_id,item_kind,item_id,"
                "provider,model,prompt_version,request_digest,verdict,confidence,"
                "rationale_zh,field_picks_json,warnings_json,status,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("t", "b", "duplicate_group", "old", "openai", "gpt", "v1", "digest",
                 "same", 0.9, "旧理由", None, "[]", "ok", "now", "now"))
        base = self.jev_probe()
        self.save_decision_key(base)
        self.enable()
        response = self.ask([self.duplicate_item()])
        self.assertEqual(response.status_code, 200, response.text)
        rows = self.audit_rows()
        self.assertEqual(len(rows), 2)
        by_item = {row["item_id"]: row for row in rows}
        self.assertEqual(by_item["old"]["model_kind"], "llm")
        self.assertEqual(by_item["c1"]["model_kind"], "decision")

    # ------------------------------------------------------------------
    # 7. llm 钥匙零回归（协议与行为照旧）
    # ------------------------------------------------------------------

    def test_llm_key_unchanged_on_import_assist(self):
        self.seed_duplicate_group()
        base = self.chat_probe()
        self.save_llm_key(base)
        self.enable()
        response = self.ask([self.duplicate_item()])
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(_ChatProbe.received), 1)
        call = _ChatProbe.received[0]
        self.assertEqual(call["path"], "/v1/chat/completions")
        suggestion = response.json()["suggestions"][0]
        self.assertEqual(suggestion["verdict"], "same")
        self.assertEqual(suggestion["confidence"], 0.92)
        self.assertEqual(response.json()["model_kind"], "llm")
        self.assertNotIn("决策模型", suggestion["rationale_zh"])

    # ------------------------------------------------------------------
    # 8. base_url 解析（纯函数）与缺省落盘（评审 BLOCKER 1 钉）
    # ------------------------------------------------------------------

    def test_decision_base_url_resolution(self):
        # 缺省 → 官方 hosted Jev 端点。
        self.assertEqual(decision.decision_base_url_of({}), "https://api.typesafe.ai")
        # 用户自配（hosted 或自部署 JEV-9B/Kev）：根地址 + /v1/systemone。
        self.assertEqual(decision.systemone_url("https://jev.internal"),
                         "https://jev.internal/v1/systemone")
        # 尾部 /v1（OpenAI 习惯）自动剥离，不产生 /v1/v1。
        self.assertEqual(decision.systemone_url("https://jev.internal/v1/"),
                         "https://jev.internal/v1/systemone")
        self.assertEqual(decision.systemone_url("https://api.typesafe.ai"),
                         "https://api.typesafe.ai/v1/systemone")

    def test_decision_key_without_base_url_defaults_to_typesafe(self):
        """decision 钥匙不填 base_url → 落盘/env 缺省都是 hosted Jev 端点，
        绝不写成 OpenAI 缺省把 TypeSafe 钥匙发往 api.openai.com（评审 BLOCKER）。"""
        # a) 保险柜直存（个人钥匙）：存储值与出站解析都指向 typesafe。
        record = ai_keys.save_personal_key(
            self.users[0]["user_id"], provider="typesafe", key=FAKE_KEY,
            base_url=None, model="jev-latest", kind="decision", data_root=DATA_ROOT)
        self.assertEqual(record["base_url"], "https://api.typesafe.ai")
        self.assertEqual(decision.decision_base_url_of(record), "https://api.typesafe.ai")
        self.assertEqual(decision.systemone_url(decision.decision_base_url_of(record)),
                         "https://api.typesafe.ai/v1/systemone")
        # b) HTTP 端点（PUT 不带 base_url → GET 元数据回读）。
        self.save_decision_key()  # base_url=None
        meta = self.client.get("/api/ai/key", headers=self._auth(0)).json()
        self.assertEqual(meta["kind"], "decision")
        self.assertEqual(meta["base_url"], "https://api.typesafe.ai")
        # d) llm 钥匙缺省回归：不填 base_url 仍是 OpenAI 兼容缺省（既有行为）。
        #    （在 env 覆盖之前做——GET /api/ai/key 的 env 记录优先于文件。）
        self.client.put("/api/ai/key", json={"provider": "openai", "key": FAKE_KEY},
                        headers=self._auth(0))
        self.assertEqual(self.client.get("/api/ai/key", headers=self._auth(0)
                                         ).json()["base_url"], "https://api.openai.com/v1")
        # c) 环境变量路径：REVIEWFLOW_AI_KEY + KIND=decision（无 BASE_URL）。
        os.environ["REVIEWFLOW_AI_KEY"] = FAKE_KEY
        os.environ["REVIEWFLOW_AI_MODEL_KIND"] = "decision"
        self.addCleanup(os.environ.pop, "REVIEWFLOW_AI_KEY", None)
        self.addCleanup(os.environ.pop, "REVIEWFLOW_AI_MODEL_KIND", None)
        env = ai_keys.env_key()
        self.assertEqual(env["base_url"], "https://api.typesafe.ai")
        self.assertEqual(decision.decision_base_url_of(env), "https://api.typesafe.ai")

    # ------------------------------------------------------------------
    # 9. 瞬时错误重试（评审 BLOCKER 2 钉：此前重试永不发生）
    # ------------------------------------------------------------------

    def test_decision_transient_error_retries_once_and_nontransient_does_not(self):
        body = decision.connectivity_body(model="jev-latest")

        def transient(*args, **kwargs):
            raise ai_suggest_api.ProviderError("决策模型端点 HTTP 503（详情不回显）",
                                               status=503)

        with mock.patch.object(decision, "post_decision", side_effect=transient) as probe:
            with self.assertRaises(ai_suggest_api.ProviderError):
                decision.call_decision(body, api_key=FAKE_KEY,
                                       base_url="https://api.typesafe.ai",
                                       model="jev-latest", timeout=1.0)
        self.assertEqual(probe.call_count, 2, "瞬时错误（503）必须重试 1 次=共 2 次调用")

        def permanent(*args, **kwargs):
            raise ai_suggest_api.ProviderError("决策模型端点 HTTP 400（详情不回显）",
                                               status=400)

        with mock.patch.object(decision, "post_decision", side_effect=permanent) as probe:
            with self.assertRaises(ai_suggest_api.ProviderError):
                decision.call_decision(body, api_key=FAKE_KEY,
                                       base_url="https://api.typesafe.ai",
                                       model="jev-latest", timeout=1.0)
        self.assertEqual(probe.call_count, 1, "非瞬时错误（400）不得重试")

    # ------------------------------------------------------------------
    # 10. assist 顶层 prompt_version(s) 按钥匙 kind（评审中危项钉）
    # ------------------------------------------------------------------

    def test_assist_top_level_prompt_versions_follow_key_kind(self):
        self.seed_duplicate_group()
        self.seed_metadata_diff()
        jev_base = self.jev_probe()
        self.save_decision_key(jev_base)
        self.enable()
        body = self.ask([self.duplicate_item()]).json()
        self.assertEqual(body["model_kind"], "decision")
        self.assertEqual(body["prompt_version"], "import-ai-assist-decision/v1")
        self.assertEqual(body["prompt_versions"], {
            "duplicate_group": "import-ai-assist-decision/v1",
            "metadata_diff": None,
            "merge_conflict": "import-ai-assist-merge-decision/v1"})
        # llm 钥匙：顶层与行级回到 LLM 版本（零回归）。
        self.save_llm_key(self.chat_probe())
        body = self.ask([self.duplicate_item()]).json()
        self.assertEqual(body["model_kind"], "llm")
        self.assertEqual(body["prompt_version"], "import-ai-assist/v1")
        self.assertEqual(body["prompt_versions"], {
            "duplicate_group": "import-ai-assist/v1",
            "metadata_diff": "import-ai-assist/v1",
            "merge_conflict": "import-ai-assist-merge/v1"})

    # ------------------------------------------------------------------
    # 11. metadata_diff 零出站降级行的审计标记（评审低危项钉）
    # ------------------------------------------------------------------

    def test_metadata_diff_decision_audit_row_marked_not_sent(self):
        self.seed_metadata_diff()
        base = self.jev_probe()
        self.save_decision_key(base)
        self.enable()
        first = self.ask([self.metadata_item()])
        self.assertEqual(first.status_code, 200, first.text)
        audit_id = first.json()["suggestions"][0]["audit_id"]
        rows = self.audit_rows()
        self.assertEqual(len(rows), 1)
        # 该行从未出站：prompt_version 记 n/a（不伪装成任何 LLM/决策版本域）。
        self.assertEqual(rows[0]["prompt_version"], "n/a")
        self.assertEqual(rows[0]["model_kind"], "decision")
        self.assertEqual(rows[0]["verdict"], "unsure")
        self.assertEqual(rows[0]["status"], "degraded")
        self.assertEqual(len(rows[0]["request_digest"]), 64)
        # 幂等：重复请求 → 行数不变、audit_id 稳定。
        again = self.ask([self.metadata_item()])
        self.assertEqual(again.status_code, 200, again.text)
        self.assertEqual(again.json()["suggestions"][0]["audit_id"], audit_id)
        self.assertEqual(len(self.audit_rows()), 1)

    # ------------------------------------------------------------------
    # 12. 置信映射取小（评审观察项钉：概率 0.65 × confidence 0.475）
    # ------------------------------------------------------------------

    def test_confidence_takes_min_of_probability_and_answer_confidence(self):
        body = _jev_body({"duplicate_verdict": _choice(
            "duplicate", {"duplicate": 0.65, "include": 0.25, "unsure": 0.1}, 0.475)})
        parsed = decision.parse_duplicate_answer(body, min_confidence=0.6)
        self.assertEqual(parsed["confidence"], 0.475)
        self.assertEqual(parsed["verdict"], "unsure",
                         "取小后低于阈值必须回落 unsure，绝不按 0.65 放行")
        self.assertIn("low_confidence", parsed["warnings"])
        # 只有一方可用时用该方。
        only_prob = _jev_body({"duplicate_verdict": {
            "type": "choice", "choice": "duplicate",
            "probabilities": {"duplicate": 0.9, "include": 0.05, "unsure": 0.05}}})
        self.assertEqual(decision.parse_duplicate_answer(
            only_prob, min_confidence=0.6)["confidence"], 0.9)
        only_conf = _jev_body({"duplicate_verdict": _choice("duplicate", None, 0.8)})
        self.assertEqual(decision.parse_duplicate_answer(
            only_conf, min_confidence=0.6)["confidence"], 0.8)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
