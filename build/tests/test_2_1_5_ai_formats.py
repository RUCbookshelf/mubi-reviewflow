# -*- coding: utf-8 -*-
"""2.1.5 aiux 批1 · AI 双协议格式 / 双槽钥匙 / 厂商目录 红钉（专属新文件，不改产品代码）。

被测对象：
- custom_backend/ai_keys.py：KEY_FORMATS（llm=chat/responses/anthropic，decision=systemone）、
  个人/共享钥匙双槽、record_outcome(ok,usage) 记账、outcome_path_for；
- custom_backend/ai_suggest_api.py：_wire_request/_wire_parse 三 wire、
  GET /api/ai/providers、PUT/GET/DELETE /api/ai/key、POST /api/ai/key/test?kind=、
  POST /api/tasks/{task_id}/ai-suggest/shared-key/test（owner-only）；
- custom_backend/ai_providers.py：13 条目静态目录 + snapshot 两级深拷贝。

钉的组织（测试类 → 用例）：
- WireTests：三格式各起一个本地 http.server 假端点（照
  test_2_1_5_import_ai_assist._ModelProbe 骨架），经 POST /api/ai/key/test
  逐格式断言出站路径/鉴权头（responses→{base}/responses+Bearer；
  anthropic→{base}/v1/messages+x-api-key 且无 Bearer+anthropic-version）。
- FormatShapeTests：非法 format×kind 组合 422；缺省 format 按 kind 落
  （llm→openai_chat、decision→systemone）；format 持久化读回；
  _wire_parse 三格式坏形状 → ProviderError（不 500）。
- DualSlotHealthTests：GET /api/ai/key 双槽视图与健康/用量字段；
  /test 成功记账（call_count+1、连败清零、total_tokens 累计探针 usage）；
  /test 失败（探针回 500）连败+1；DELETE ?kind=decision 单槽删。
- SharedKeyTestEndpoint：owner 200；非 owner 成员 403；无钥匙 409。
- CatalogTests：快照 13 条、id 唯一、全 base_url 公网 http(s)（唯一例外
  local_ollama：回环+desktop_only）、format ∈ KEY_FORMATS 展平集、
  GET /api/ai/providers 的 supported_formats 四真 + version 非空、
  snapshot 返回值改坏不影响再次 snapshot。

运行模式组合（照 test_2_1_5_import_ai_assist 先例）：模块整体跑 server 模式
（协作邀请——成员 403 用例——只在 server 模式开放）；SSRF 回环闸按
ai_keys._server_mode 判定，setUp 里以函数级 mock 钉成桌面口径——探针
base_url 用 http://127.0.0.1:<port>/v1 照样过闸（桌面=本地模型语义）。

已发现产品疑似缺陷（本文件如实报告、不改产品代码，见
SharedKeyTestEndpointTests 注释）：ai_suggest_api.test_shared_key 调
ai_keys.outcome_path_for 漏传必填关键字参数 user_id，owner 成功路径
TypeError→500。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
fixture = tempfile.TemporaryDirectory(prefix="rf_215_ai_formats_")
os.environ["COBOOKSHELF_DATA_DIR"] = fixture.name
# 服务器模式（同 test_ai_suggest / test_2_1_5_import_ai_assist 先例）：协作邀请
# 端点只在 server 模式开放（成员 403 用例需要）；SSRF 回环闸走
# ai_keys._server_mode，setUp 以函数级 mock 钉成桌面口径放行回环探针。
os.environ["REVIEWFLOW_MODE"] = "server"

from fastapi.testclient import TestClient  # noqa: E402

from custom_backend import ai_keys  # noqa: E402
from custom_backend import ai_providers  # noqa: E402
from custom_backend import ai_suggest_api  # noqa: E402
from custom_backend import auth as auth_mod  # noqa: E402
from custom_backend.main import DATA_DIR, app  # noqa: E402

#: 钥匙保险柜根：与 main.DATA_DIR 同一绑定（全套 pytest 下其它模块会在收集期
#: 改写环境变量，绝不能用 fixture.name 推断保险柜位置——先例 test_ai_suggest）。
DATA_ROOT = Path(DATA_DIR)

#: 假钥匙（仅测试内使用；断言失败消息绝不回显其值）。
FAKE_KEY = "sk-ai-formats-abcdef123456"

#: 字面公网 IP（TEST-NET-3 文档段：非 RFC1918/CGNAT/回环，免 DNS 即可过闸），
#: 供不出站的用例当 base_url 占位。
PUBLIC_LITERAL_BASE = "https://203.0.113.10/v1"

#: openai_responses wire 的固定应答（含 usage.total_tokens 供记账断言）。
RESPONSES_REPLY = {
    "output": [{"type": "message",
                "content": [{"type": "output_text", "text": "{\"ping\":true}"}]}],
    "usage": {"total_tokens": 5}, "model": "m",
}

#: anthropic wire 的固定应答。
ANTHROPIC_REPLY = {"content": [{"type": "text", "text": "ok"}],
                   "usage": {}, "model": "m"}

#: openai_chat wire 的固定应答。
CHAT_REPLY = {"choices": [{"message": {"content": "{\"ping\":true}"}}],
              "usage": {"total_tokens": 5}, "model": "m"}


class _ProbeHandler(BaseHTTPRequestHandler):
    """本地假模型端点（照 test_2_1_5_import_ai_assist._ModelProbe 骨架）：
    线程起 http.server，捕获请求头/体，回固定 JSON / 固定状态码。"""

    reply: dict = {}
    status: int = 200
    received: list[dict] = []

    def do_POST(self):  # noqa: N802  BaseHTTPRequestHandler 约定
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        type(self).received.append({
            "path": self.path,
            "headers": {name.lower(): value for name, value in self.headers.items()},
            "body": raw.decode("utf-8", "replace"),
        })
        payload = json.dumps(type(self).reply, ensure_ascii=False).encode("utf-8")
        self.send_response(type(self).status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):  # 静默探针日志
        return


class _AiFormatsTestBase(unittest.TestCase):
    """公共夹具：TestClient + 双用户（owner/member）+ 每用例清钥匙/环境覆盖
    + 桌面模式钉（回环探针 base_url 可过 SSRF 闸）。"""

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.client.__enter__()
        cls.users = []
        for index, name in enumerate(("owner", "member")):
            # 不走 HTTP /api/auth/register：全套 pytest 里多模块注册共享同一
            # 限流桶会撞 429（先例 test_ai_suggest 注）；直接建用户签发令牌。
            user = auth_mod.create_user(f"ai-formats-{name}{index}@example.test",
                                        "AI-Formats-2026")
            cls.users.append({
                "user_id": user["id"], "username": user["username"],
                "token": auth_mod.create_access_token(user["id"], user["username"]),
            })

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        fixture.cleanup()

    def setUp(self):
        os.environ.pop("REVIEWFLOW_AI_KEY", None)
        # 桌面模式钉：只钉 ai_keys 的模式判定，回环探针可放行（先例
        # test_2_1_5_import_ai_assist.setUp），免疫环境变量被其它模块改写。
        mode = mock.patch.object(ai_keys, "_server_mode", lambda: False)
        mode.start()
        self.addCleanup(mode.stop)
        for user in self.users:
            ai_keys.delete_personal_key(user["user_id"], DATA_ROOT)  # 全删（含双槽）

    def auth(self, index=0):
        return {"Authorization": "Bearer " + self.users[index]["token"]}

    def new_task(self) -> str:
        return self.client.post("/api/tasks", json={"name": self._testMethodName},
                                headers=self.auth(0)).json()["task_id"]

    def put_key(self, *, base_url=PUBLIC_LITERAL_BASE, kind=ai_keys.KIND_LLM,
                format=None, path="/api/ai/key", user_index=0):
        """PUT 个人/共享钥匙（format=None 表示请求里不带该字段）。"""
        payload = {"provider": "probe", "key": FAKE_KEY, "model": "probe-model",
                   "base_url": base_url, "kind": kind}
        if format is not None:
            payload["format"] = format
        return self.client.put(path, json=payload, headers=self.auth(user_index))

    def get_slot(self, kind=ai_keys.KIND_LLM, user_index=0) -> dict:
        response = self.client.get("/api/ai/key", headers=self.auth(user_index))
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()[kind]

    def post_test(self, kind=ai_keys.KIND_LLM, user_index=0):
        return self.client.post("/api/ai/key/test", params={"kind": kind},
                                headers=self.auth(user_index))

    def start_probe(self, reply, status=200):
        """起一个一次性本地探针服务器；返回 (server, received 列表)。"""
        handler = type("Probe", (_ProbeHandler,),
                       {"reply": reply, "status": status, "received": []})
        server = HTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def _stop():
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()
        self.addCleanup(_stop)
        return server, handler.received

    @staticmethod
    def probe_base(server, suffix="/v1") -> str:
        return f"http://127.0.0.1:{server.server_address[1]}{suffix}"


class WireTests(_AiFormatsTestBase):
    """三 wire 格式经真实 HTTP 出站到本地探针（不打真网）。"""

    def _roundtrip(self, *, reply, format, base_suffix="/v1"):
        """PUT 带 format 指向探针 → POST /api/ai/key/test?kind=llm，返回 (响应体, 探针捕获)。"""
        server, received = self.start_probe(reply)
        response = self.put_key(base_url=self.probe_base(server, base_suffix),
                                format=format)
        self.assertEqual(response.status_code, 200, response.text)
        response = self.post_test(kind=ai_keys.KIND_LLM)
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        # 通用钉：ok=true + latency_ms≥0 + 正确 kind/format（aiux ①/D）。
        self.assertTrue(body["ok"], "连通测试应成功")
        self.assertEqual(body["kind"], ai_keys.KIND_LLM)
        self.assertEqual(body["format"], format)
        self.assertGreaterEqual(body["latency_ms"], 0)
        self.assertTrue(FAKE_KEY not in response.text, "响应不得包含钥匙明文")
        self.assertEqual(len(received), 1, "成功路径只应出站一次（不重试）")
        return body, received[0]

    def test_openai_responses_wire(self):
        """responses：POST {base}/responses + Bearer；解析 output[].message→output_text。"""
        body, request = self._roundtrip(reply=RESPONSES_REPLY,
                                        format=ai_keys.FORMAT_OPENAI_RESPONSES)
        self.assertEqual(body["model"], "m")
        self.assertEqual(body["usage"].get("total_tokens"), 5)
        self.assertEqual(request["path"], "/v1/responses",
                         "responses wire 必须打到 {base}/responses")
        # 钥匙值绝不写进断言失败消息——用 assertTrue 固定文案比较。
        self.assertTrue(request["headers"].get("authorization") == f"Bearer {FAKE_KEY}",
                        "responses wire 必须携带 Authorization Bearer 钥匙（失败时不回显值）")

    def test_anthropic_wire(self):
        """anthropic：POST {base}/v1/messages + x-api-key（无 Bearer）+ anthropic-version。"""
        body, request = self._roundtrip(reply=ANTHROPIC_REPLY,
                                        format=ai_keys.FORMAT_ANTHROPIC,
                                        base_suffix="")
        self.assertEqual(body["usage"], {})
        self.assertEqual(body["model"], "m")
        headers = request["headers"]
        self.assertEqual(request["path"], "/v1/messages",
                         "anthropic wire 必须打到 {base}/v1/messages")
        self.assertTrue(headers.get("x-api-key") == FAKE_KEY,
                        "anthropic 钥匙必须走 x-api-key 头（失败时不回显值）")
        self.assertEqual(headers.get("anthropic-version"), "2023-06-01")
        self.assertNotIn("authorization", headers,
                         "anthropic wire 不得携带 Authorization Bearer 头")

    def test_openai_chat_wire(self):
        """chat（缺省 wire）：POST {base}/chat/completions + Bearer。"""
        body, request = self._roundtrip(reply=CHAT_REPLY,
                                        format=ai_keys.FORMAT_OPENAI_CHAT)
        self.assertEqual(body["usage"].get("total_tokens"), 5)
        self.assertEqual(request["path"], "/v1/chat/completions",
                         "chat wire 必须打到 {base}/chat/completions")
        self.assertTrue(request["headers"].get("authorization") == f"Bearer {FAKE_KEY}",
                        "chat wire 必须携带 Authorization Bearer 钥匙（失败时不回显值）")


class FormatShapeTests(_AiFormatsTestBase):
    """format 字段的校验/缺省/持久化 + _wire_parse 坏形状映射。"""

    def test_put_decision_with_anthropic_format_rejected(self):
        """decision 槽仅允许 systemone：PUT format=anthropic+kind=decision → 422。"""
        response = self.put_key(kind=ai_keys.KIND_DECISION,
                                format=ai_keys.FORMAT_ANTHROPIC, base_url=None)
        self.assertEqual(response.status_code, 422,
                         "decision 钥匙的 format 只能是 systemone（非法组合须 422）")
        self.assertFalse(self.get_slot(ai_keys.KIND_DECISION)["configured"],
                         "422 后不得落盘 decision 槽")

    def test_default_format_per_kind(self):
        """PUT 不带 format：llm 槽读回 openai_chat，decision 槽读回 systemone。"""
        response = self.put_key()
        self.assertEqual(response.status_code, 200, response.text)
        response = self.put_key(kind=ai_keys.KIND_DECISION, base_url=None)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.get_slot(ai_keys.KIND_LLM)["format"],
                         ai_keys.FORMAT_OPENAI_CHAT)
        self.assertEqual(self.get_slot(ai_keys.KIND_DECISION)["format"],
                         ai_keys.FORMAT_SYSTEMONE)

    def test_format_persisted_and_read_back(self):
        """显式 format 落盘后经 GET 读回（每次 GET 都从盘上重读=持久化钉）。"""
        self.put_key(format=ai_keys.FORMAT_ANTHROPIC,
                     base_url=PUBLIC_LITERAL_BASE.rsplit("/", 1)[0])
        self.assertEqual(self.get_slot(ai_keys.KIND_LLM)["format"],
                         ai_keys.FORMAT_ANTHROPIC)
        self.put_key(format=ai_keys.FORMAT_OPENAI_RESPONSES)
        self.assertEqual(self.get_slot(ai_keys.KIND_LLM)["format"],
                         ai_keys.FORMAT_OPENAI_RESPONSES)

    def test_wire_parse_bad_shapes_raise_provider_error(self):
        """三 wire 的坏响应形状（缺 output/缺 content/缺 choices）各映射
        ProviderError（端点层 502/不 500 的前提），绝不冒泡裸异常。"""
        with self.assertRaises(ai_suggest_api.ProviderError):
            ai_suggest_api._wire_parse(ai_keys.FORMAT_OPENAI_RESPONSES,
                                       {"model": "m"}, "m")  # 缺 output
        with self.assertRaises(ai_suggest_api.ProviderError):
            ai_suggest_api._wire_parse(ai_keys.FORMAT_ANTHROPIC,
                                       {"model": "m"}, "m")  # 缺 content
        with self.assertRaises(ai_suggest_api.ProviderError):
            ai_suggest_api._wire_parse(ai_keys.FORMAT_OPENAI_CHAT,
                                       {"model": "m"}, "m")  # 缺 choices


class DualSlotHealthTests(_AiFormatsTestBase):
    """双槽并存视图 + /test 出站记账（D/H）+ 单槽删除。"""

    def test_dual_slot_view_shape(self):
        """llm 与 decision 双槽并存；每槽带 format/健康/用量字段。"""
        self.put_key()
        self.put_key(kind=ai_keys.KIND_DECISION, base_url=None)
        response = self.client.get("/api/ai/key", headers=self.auth(0))
        self.assertEqual(response.status_code, 200, response.text)
        slots = response.json()
        self.assertEqual(set(slots), {ai_keys.KIND_LLM, ai_keys.KIND_DECISION},
                         "GET /api/ai/key 必须是 llm+decision 双槽视图")
        for kind, slot in slots.items():
            self.assertTrue(slot["configured"], f"{kind} 槽应已配置")
            for field in ("format", "kind", "provider", "masked", "base_url",
                          "consecutive_failures", "call_count", "total_tokens",
                          "last_used_at"):
                self.assertIn(field, slot, f"{kind} 槽缺元数据字段 {field}")
        self.assertEqual(slots[ai_keys.KIND_LLM]["kind"], ai_keys.KIND_LLM)
        self.assertEqual(slots[ai_keys.KIND_DECISION]["kind"], ai_keys.KIND_DECISION)
        self.assertEqual(slots[ai_keys.KIND_DECISION]["format"],
                         ai_keys.FORMAT_SYSTEMONE)

    def test_success_ledger_counts_and_resets_streak(self):
        """/test 失败一次（连败=1）→ 成功两次：连败清零、call_count+1/次、
        total_tokens 按探针 usage.total_tokens 累计。

        注：中途换 base_url 覆盖保存时，无论产品采「计数随记录延续」还是
        「换钥匙清零」，成功后的断言值都一致（清零与延续殊途同归）。"""
        fail_server, _ = self.start_probe({"error": "boom"}, status=500)
        self.put_key(base_url=self.probe_base(fail_server))
        response = self.post_test()
        self.assertEqual(response.status_code, 502, "探针回 500 应映射 502（不 500）")
        slot = self.get_slot(ai_keys.KIND_LLM)
        self.assertEqual(slot["consecutive_failures"], 1, "失败后连败应为 1")
        self.assertEqual(slot["call_count"], 0)

        ok_server, _ = self.start_probe(CHAT_REPLY)
        self.put_key(base_url=self.probe_base(ok_server))
        response = self.post_test()
        self.assertEqual(response.status_code, 200, response.text)
        slot = self.get_slot(ai_keys.KIND_LLM)
        self.assertEqual(slot["consecutive_failures"], 0, "成功后连败必须清零")
        self.assertEqual(slot["call_count"], 1)
        self.assertEqual(slot["total_tokens"], 5, "探针 usage.total_tokens 须累计")
        self.assertIsNotNone(slot["last_used_at"], "成功后须刷新 last_used_at")

        response = self.post_test()
        self.assertEqual(response.status_code, 200, response.text)
        slot = self.get_slot(ai_keys.KIND_LLM)
        self.assertEqual(slot["call_count"], 2)
        self.assertEqual(slot["total_tokens"], 10, "再次成功用量继续累计")

    def test_failure_ledger_increments_streak(self):
        """/test 失败（探针回 500）→ consecutive_failures+1，计数/用量不动。"""
        server, received = self.start_probe({"error": "boom"}, status=500)
        self.put_key(base_url=self.probe_base(server))
        response = self.post_test()
        self.assertEqual(response.status_code, 502, response.text)
        self.assertTrue(FAKE_KEY not in response.text, "错误响应不得包含钥匙明文")
        self.assertGreaterEqual(len(received), 1, "探针应收到出站请求")
        slot = self.get_slot(ai_keys.KIND_LLM)
        self.assertEqual(slot["consecutive_failures"], 1)
        self.assertEqual(slot["call_count"], 0)
        self.assertEqual(slot["total_tokens"], 0)

    def test_delete_kind_removes_only_that_slot(self):
        """DELETE ?kind=decision 只删 decision 槽；llm 槽（文件）保留。"""
        self.put_key()
        self.put_key(kind=ai_keys.KIND_DECISION, base_url=None)
        response = self.client.delete("/api/ai/key",
                                      params={"kind": ai_keys.KIND_DECISION},
                                      headers=self.auth(0))
        self.assertEqual(response.status_code, 200, response.text)
        slots = response.json()
        self.assertFalse(slots[ai_keys.KIND_DECISION]["configured"],
                         "只应删 decision 槽")
        self.assertTrue(slots[ai_keys.KIND_LLM]["configured"], "llm 槽必须保留")
        user_id = self.users[0]["user_id"]
        self.assertFalse(
            ai_keys.personal_slot_path(user_id, ai_keys.KIND_DECISION, DATA_ROOT).exists(),
            "decision 槽文件应已删除")
        self.assertTrue(
            ai_keys.personal_slot_path(user_id, ai_keys.KIND_LLM, DATA_ROOT).is_file(),
            "llm 槽文件必须保留")


class SharedKeyTestEndpointTests(_AiFormatsTestBase):
    """POST /api/tasks/{task_id}/ai-suggest/shared-key/test（E：owner-only）。"""

    def _invite_member(self, task_id):
        response = self.client.post(
            f"/api/tasks/{task_id}/collaboration/invite",
            json={"username": self.users[1]["username"], "role": "coder"},
            headers=self.auth(0))
        self.assertEqual(response.status_code, 201, response.text)

    def test_owner_gets_200_and_ledger(self):
        """owner 测共享钥匙：200 + ok + kind/format/latency；记账落共享槽文件。

        （原产品缺陷 outcome_path_for 漏传 user_id → owner 500 已在 aiux
        批2 集成时修复，测试内垫片与缺陷红钉随修复移除。）"""
        task_id = self.new_task()
        server, received = self.start_probe(CHAT_REPLY)
        response = self.put_key(
            path=f"/api/tasks/{task_id}/ai-suggest/shared-key",
            base_url=self.probe_base(server))
        self.assertEqual(response.status_code, 200, response.text)

        response = self.client.post(
            f"/api/tasks/{task_id}/ai-suggest/shared-key/test",
            params={"kind": ai_keys.KIND_LLM}, headers=self.auth(0))
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["kind"], ai_keys.KIND_LLM)
        self.assertEqual(body["format"], ai_keys.FORMAT_OPENAI_CHAT)
        self.assertGreaterEqual(body["latency_ms"], 0)
        self.assertTrue(FAKE_KEY not in response.text, "响应不得包含钥匙明文")
        # D 记账也覆盖共享槽：成功 → call_count+1、total_tokens 累计。
        shared_slot = ai_keys.shared_slot_path(task_id, ai_keys.KIND_LLM, DATA_ROOT)
        record = json.loads(shared_slot.read_text(encoding="utf-8"))
        self.assertEqual(record["call_count"], 1)
        self.assertEqual(record["total_tokens"], 5)

    def test_non_owner_member_forbidden(self):
        """受邀成员（非 owner）调用共享钥匙测试 → 403（owner-only）。"""
        task_id = self.new_task()
        self._invite_member(task_id)
        response = self.put_key(
            path=f"/api/tasks/{task_id}/ai-suggest/shared-key",
            base_url=PUBLIC_LITERAL_BASE)
        self.assertEqual(response.status_code, 200, response.text)
        response = self.client.post(
            f"/api/tasks/{task_id}/ai-suggest/shared-key/test",
            params={"kind": ai_keys.KIND_LLM}, headers=self.auth(1))
        self.assertEqual(response.status_code, 403,
                         "共享钥匙连通测试必须 owner-only")

    def test_missing_key_conflict(self):
        """未配置共享钥匙 → 409 结构化 ai_key_missing。"""
        task_id = self.new_task()
        response = self.client.post(
            f"/api/tasks/{task_id}/ai-suggest/shared-key/test",
            params={"kind": ai_keys.KIND_LLM}, headers=self.auth(0))
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"]["code"], "ai_key_missing")


class CatalogTests(_AiFormatsTestBase):
    """14 条目厂商目录静态完整性 + GET /api/ai/providers 端点。

    目检反馈④（2026-11-12）：+TypeSafe（Jev 官方，systemone 同线）→ 14 条。
    """

    def test_unique_ids_and_entry_count(self):
        snapshot = ai_providers.snapshot()
        self.assertEqual(len(snapshot), 14, "目录必须恰好 14 条")
        ids = [entry["id"] for entry in snapshot]
        self.assertEqual(len(set(ids)), len(ids), "目录 id 必须唯一")

    def test_base_urls_public_except_local_ollama(self):
        """全 base_url 公网 http(s)（hostname 解析后非私网/回环）；唯一例外
        local_ollama：回环端点且必须标 desktop_only（server 模式可拦）。"""
        with mock.patch.object(ai_keys, "_server_mode", lambda: True):
            snapshot = ai_providers.snapshot()
            by_id = {entry["id"]: entry for entry in snapshot}
            self.assertIn("local_ollama", by_id)
            ollama = by_id["local_ollama"]
            self.assertTrue(ollama.get("desktop_only"),
                            "local_ollama 回环端点必须标 desktop_only")
            ollama_host = urlsplit(ollama["formats"][0]["base_url"]).hostname
            self.assertEqual(ai_keys._address_block_reason(ollama_host), "loopback",
                             "server 模式下 local_ollama 回环端点必须被 SSRF 闸拦下")
            for entry in snapshot:
                self.assertIn(entry.get("type"), ("official", "relay"),
                              f"{entry['id']}: type 取值非法")
                if entry["id"] == "local_ollama":
                    continue
                for fmt in entry["formats"]:
                    self._assert_public_base_url(fmt["base_url"], entry["id"])

    def _assert_public_base_url(self, base_url, entry_id):
        parts = urlsplit(base_url)
        self.assertIn(parts.scheme, ("http", "https"),
                      f"{entry_id}: base_url 必须 http(s)")
        host = (parts.hostname or "").lower()
        literal = ai_keys._parse_ip_literal(host)
        if literal is not None:
            self.assertIsNone(ai_keys._address_block_reason(str(literal)),
                              f"{entry_id}: 字面 IP 必须是公网地址（非私网/回环）")
            return
        try:
            addresses = ai_keys._resolve_host(host, parts.port or 443)
        except (OSError, ValueError, UnicodeError):
            addresses = []
        if not addresses:
            # 离线兜底：域名解析不可用时至少排除明显的内网主机名形态。
            self.assertNotEqual(host, "localhost", f"{entry_id}: 不得指向 localhost")
            self.assertFalse(host.endswith((".internal", ".local", ".lan")),
                             f"{entry_id}: 不得指向内网域名后缀")
            return
        for address in addresses:
            self.assertIsNone(ai_keys._address_block_reason(address),
                              f"{entry_id}: 解析出的地址必须非私网/回环")

    def test_formats_within_key_formats(self):
        """每条 format 取值都在 KEY_FORMATS 展平集内。"""
        flattened = {fmt for formats in ai_keys.KEY_FORMATS.values() for fmt in formats}
        self.assertEqual(flattened, {ai_keys.FORMAT_OPENAI_CHAT,
                                     ai_keys.FORMAT_OPENAI_RESPONSES,
                                     ai_keys.FORMAT_ANTHROPIC,
                                     ai_keys.FORMAT_SYSTEMONE})
        for entry in ai_providers.snapshot():
            for fmt in entry["formats"]:
                self.assertIn(fmt["format"], flattened,
                              f"{entry['id']}: format 取值必须合法")
                self.assertTrue(fmt["models"], f"{entry['id']}: models 建议清单非空")

    def test_providers_endpoint_shape(self):
        response = self.client.get("/api/ai/providers", headers=self.auth(0))
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(len(body["providers"]), 14)
        self.assertEqual(body["providers"], ai_providers.snapshot())
        self.assertTrue(str(body["version"] or "").strip(), "目录版本号必须非空")
        self.assertEqual(body["supported_formats"], {
            ai_keys.FORMAT_OPENAI_CHAT: True,
            ai_keys.FORMAT_OPENAI_RESPONSES: True,
            ai_keys.FORMAT_ANTHROPIC: True,
            ai_keys.FORMAT_SYSTEMONE: True,
        }, "supported_formats 必须四键全真")

    def test_snapshot_two_level_deep_copy(self):
        """改坏 snapshot 返回值（两级：条目/格式对象 + 内层列表）不影响再次 snapshot。"""
        pristine = ai_providers.snapshot()
        mutated = ai_providers.snapshot()
        mutated[0]["id"] = "hacked"
        mutated[0]["kinds"].append("decision")
        mutated[0]["formats"][0]["models"].append("evil-model")
        mutated[0]["formats"][0]["format"] = "bogus"
        self.assertEqual(ai_providers.snapshot(), pristine,
                         "snapshot 必须两级深拷贝：改坏返回值不得污染目录")


if __name__ == "__main__":
    unittest.main()
