"""B6 后端骨架回归：AI 抽取建议（BYOK）。

覆盖（B4 v1.1 对应条款）：
- 钥匙保险柜：掩码回显、跨用户隔离（无读取他人钥匙路径）、owner-only 共享钥匙写、
  钥匙文件只在 {DATA_DIR}/.ai_keys/ 保险柜（B5a 隔离契约）；
- 任务 AI 设置：owner-only、expected_version 乐观锁、白名单/语境上限校验（§2/R2/R3）；
- 建议生成：注入 fake provider 零联网、无 quote 丢弃（P4）、白名单 403（P9）、
  轨迹 ai_suggestion_generated 落库（P6）、provider/model/prompt_version 随建议存储（P7）；
- 建议决定：adopted/ignored/edited 状态流转、adopt 不写编码值（P1）、
  轨迹 ai_suggestion_decided 落库；
- R1 无 Key 零影响：未配置钥匙时全部端点优雅降级、无异常、零出站；
- 导出包断言：轨迹导出 ZIP 与审计 CSV 内绝无 .ai_keys 路径与钥匙明文（B5a 第三条）。

出站客户端以 mock 替换 custom_backend.ai_suggest_api._provider_generate，不打真实网络。
"""
import io
import json
import os
import re
import sqlite3
import sys
import tempfile
import unittest
import zipfile
from contextlib import closing
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
fixture = tempfile.TemporaryDirectory(prefix="rf_ai_suggest_test_")
os.environ["COBOOKSHELF_DATA_DIR"] = fixture.name
os.environ["REVIEWFLOW_MODE"] = "server"

from fastapi.testclient import TestClient

from custom_backend import main as main_mod
from custom_backend.main import app
from custom_backend import auth as auth_mod
from custom_backend import ai_keys, ai_suggest_api
from coscreen import db
from coscreen.models import Article

#: 钥匙保险柜根：与 main.DATA_DIR 同一绑定（全套 pytest 下其它模块会在收集期
#: 改写环境变量，绝不能用 fixture.name 推断保险柜位置）。
DATA_ROOT = Path(main_mod.DATA_DIR)

FAKE_KEY = "sk-test-abcdef123456"

FAKE_CONTENT = json.dumps({
    "study_design": {"value": "随机对照试验", "quote": "A randomised controlled trial was conducted.", "pages": [1]},
    "sample_size": {"value": "120", "quote": "", "pages": []},
    "funding_source": {"value": None, "quote": None, "pages": []},
}, ensure_ascii=False)


def fake_generate(content=FAKE_CONTENT):
    """返回拟真的 OpenAI 兼容响应（零联网）。"""
    def _generate(system, user, *, api_key, base_url, model, timeout=60.0):
        assert api_key == FAKE_KEY, "出站必须携带保险柜里的钥匙"
        return {"content": content, "usage": {"prompt_tokens": 100, "completion_tokens": 20},
                "model": model}
    return _generate


class AISuggestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.client.__enter__()
        cls.users = []
        for i, name in enumerate(("owner", "member", "outsider")):
            # 不走 HTTP /api/auth/register：全套 pytest 里 TestClient 的 client.host
            # 固定，多模块注册共享同一限流桶会撞 429（先例：test_ux_frontend_review_fixes.py）；
            # 直接建用户并签发令牌，行为等价且不占限流额度。
            user = auth_mod.create_user(f"{name}{i}@example.test", "AI-Suggest-2026")
            token = auth_mod.create_access_token(user["id"], user["username"])
            cls.users.append({"user_id": user["id"], "username": user["username"],
                              "token": token})

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        fixture.cleanup()

    def auth(self, i=0):
        return {"Authorization": "Bearer " + self.users[i]["token"]}

    def setUp(self):
        # 每用例清场：环境变量覆盖、个人钥匙；任务为每用例新建（共享钥匙随任务 id 隔离）。
        os.environ.pop("REVIEWFLOW_AI_KEY", None)
        for user in self.users:
            ai_keys.delete_personal_key(user["user_id"], DATA_ROOT)
        self.guard = mock.patch.object(
            ai_suggest_api, "_provider_generate",
            side_effect=AssertionError("未配置钥匙时不得出站"))
        self.guard.start()
        self.addCleanup(self.guard.stop)
        self.task = self.client.post("/api/tasks", json={"name": self._testMethodName},
                                     headers=self.auth(0)).json()["task_id"]
        ctx = app.state.review_workflow.context(self.task, self.users[0])
        db.upsert_articles(ctx.owner_db, [Article(
            zotero_key="A1", title="Trial of Example Therapy",
            authors="Doe J; Smith A", journal="J Example", year=2024, doi="10.test/ai",
            abstract="Background: example. Methods: a randomised controlled trial was conducted in 6 centres.")], [])
        self.base = f"/api/tasks/{self.task}"

    def invite_member(self):
        r = self.client.post(f"{self.base}/collaboration/invite",
                             json={"username": self.users[1]["username"], "role": "coder"},
                             headers=self.auth(0))
        self.assertEqual(r.status_code, 201, r.text)

    def put_personal_key(self, user_index=0, verify=False):
        r = self.client.put("/api/ai/key", json={
            "provider": "openai", "key": FAKE_KEY, "model": "gpt-test",
            "verify": verify}, headers=self.auth(user_index))
        return r

    def enable_task(self, field_ids=None, key_mode="personal"):
        settings = self.client.get(f"{self.base}/ai-suggest/settings", headers=self.auth(0)).json()
        return self.client.put(f"{self.base}/ai-suggest/settings", json={
            "expected_version": settings["version"], "enabled": True, "key_mode": key_mode,
            "allowed_field_ids": field_ids or ["study_design", "sample_size", "funding_source"],
            "context_prompt": ""}, headers=self.auth(0))

    def suggestions(self, key="A1", field_ids=None, user_index=0):
        requested = ["study_design", "sample_size", "funding_source"] if field_ids is None else field_ids
        return self.client.post(f"{self.base}/coding/ai-suggestions", json={
            "zotero_key": key, "field_ids": requested},
            headers=self.auth(user_index))

    # -- 钥匙保险柜：掩码 / 隔离 / 位置 ---------------------------------------

    def test_personal_key_masked_and_isolated(self):
        r = self.put_personal_key()
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertTrue(body["configured"])
        self.assertEqual(body["masked"], "***3456")
        self.assertNotIn(FAKE_KEY, r.text)  # 永不回显明文
        r = self.client.get("/api/ai/key", headers=self.auth(0))
        self.assertNotIn(FAKE_KEY, r.text)
        self.assertEqual(r.json()["provider"], "openai")
        # 跨用户隔离：另一账号读不到（无读取他人钥匙路径）。
        r = self.client.get("/api/ai/key", headers=self.auth(1))
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["configured"])
        # 保险柜位置：{DATA_DIR}/.ai_keys/personal_{user_id}.json，绝不在任何任务目录。
        vault = DATA_ROOT / ".ai_keys"
        path = vault / f"personal_{self.users[0]['user_id']}.json"
        self.assertTrue(path.is_file())
        tasks_root = DATA_ROOT / "tasks"
        for task_dir in (tasks_root.iterdir() if tasks_root.is_dir() else []):
            self.assertFalse(list(task_dir.rglob(".ai_keys*")),
                             f"钥匙文件出现在任务目录 {task_dir}")
        # 删除后优雅降级。
        r = self.client.delete("/api/ai/key", headers=self.auth(0))
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["configured"])
        self.assertFalse(path.exists())

    def test_short_key_masked_fully(self):
        self.assertEqual(ai_keys.mask_key("short"), "***")           # 短钥匙整体打码
        self.assertEqual(ai_keys.mask_key("12345678"), "***")
        self.assertEqual(ai_keys.mask_key("sk-medium-key1"), "***key1")  # B9 F22：不露前缀，仅尾 4 位
        self.assertEqual(ai_keys.mask_key("sk-test-abcdef123456"), "***3456")  # 仅尾 4 位

    def test_shared_key_owner_only_and_metadata_only(self):
        self.invite_member()
        # 成员/外人不可写共享钥匙（owner-only）。
        r = self.client.put(f"{self.base}/ai-suggest/shared-key", json={
            "provider": "openai", "key": FAKE_KEY}, headers=self.auth(1))
        self.assertEqual(r.status_code, 403, r.text)
        r = self.client.put(f"{self.base}/ai-suggest/shared-key", json={
            "provider": "openai", "key": FAKE_KEY}, headers=self.auth(2))
        self.assertEqual(r.status_code, 403, r.text)
        # owner 写入后，所有成员只见 {enabled, provider_name} 元数据。
        r = self.client.put(f"{self.base}/ai-suggest/shared-key", json={
            "provider": "openai", "key": FAKE_KEY, "model": "gpt-test"}, headers=self.auth(0))
        self.assertEqual(r.status_code, 200, r.text)
        r = self.client.get(f"{self.base}/ai-suggest/shared-key", headers=self.auth(1))
        self.assertEqual(r.json(), {"enabled": True, "provider_name": "openai"})
        self.assertNotIn(FAKE_KEY, r.text)
        r = self.client.get(f"{self.base}/ai-suggest/shared-key", headers=self.auth(0))
        self.assertNotIn(FAKE_KEY, r.text)  # owner 也拿不到明文
        # 成员不可删除；owner 删除后元数据回落。
        self.assertEqual(self.client.delete(
            f"{self.base}/ai-suggest/shared-key", headers=self.auth(1)).status_code, 403)
        self.assertEqual(self.client.delete(
            f"{self.base}/ai-suggest/shared-key", headers=self.auth(0)).status_code, 200)
        self.assertFalse(self.client.get(
            f"{self.base}/ai-suggest/shared-key", headers=self.auth(1)).json()["enabled"])

    def test_shared_key_vault_location(self):
        r = self.client.put(f"{self.base}/ai-suggest/shared-key", json={
            "provider": "openai", "key": FAKE_KEY}, headers=self.auth(0))
        self.assertEqual(r.status_code, 200, r.text)
        path = DATA_ROOT / ".ai_keys" / f"shared_{self.task}.json"
        self.assertTrue(path.is_file())
        task_dir = app.state.review_workflow.context(self.task, self.users[0]).info.dir
        self.assertNotIn(".ai_keys", str(task_dir))

    # -- 任务设置 / 偏好 -------------------------------------------------------

    def test_status_participant_readable_personal_mode(self):
        """B7 审查修复：成员可读任务 AI 状态——personal 模式下成员不被误判为未启用。"""
        self.invite_member()
        r = self.client.get(f"{self.base}/ai-suggest/status", headers=self.auth(1))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertFalse(r.json()["enabled"])
        # owner 启用（personal 模式）后：成员读到 开关/模式/白名单子集/标签，settings 仍 owner-only。
        self.enable_task(key_mode="personal", field_ids=["study_design", "sample_size"])
        r = self.client.get(f"{self.base}/ai-suggest/status", headers=self.auth(1))
        body = r.json()
        self.assertTrue(body["enabled"])
        self.assertEqual(body["key_mode"], "personal")
        self.assertEqual(body["allowed_field_ids"], ["sample_size", "study_design"])
        self.assertIn("study_design", body["field_labels"])
        self.assertFalse(body["shared_available"])
        self.assertNotIn("key", body)  # 状态端点绝无钥匙材料
        self.assertEqual(
            self.client.get(f"{self.base}/ai-suggest/settings", headers=self.auth(1)).status_code, 403)
        # shared 模式：成员读到共享钥匙存在性与 provider 名（披露条款），仍无任何明文。
        self.client.put(f"{self.base}/ai-suggest/shared-key", json={
            "provider": "deepseek", "key": FAKE_KEY}, headers=self.auth(0))
        settings = self.client.get(f"{self.base}/ai-suggest/settings", headers=self.auth(0)).json()
        self.client.put(f"{self.base}/ai-suggest/settings", json={
            "expected_version": settings["version"], "enabled": True, "key_mode": "shared",
            "allowed_field_ids": ["study_design"], "context_prompt": ""}, headers=self.auth(0))
        body = self.client.get(f"{self.base}/ai-suggest/status", headers=self.auth(1)).json()
        self.assertTrue(body["shared_available"])
        self.assertEqual(body["shared_provider_name"], "deepseek")
        self.assertNotIn(FAKE_KEY, json.dumps(body))

    def test_settings_owner_only_and_optimistic_lock(self):
        self.invite_member()
        r = self.client.get(f"{self.base}/ai-suggest/settings", headers=self.auth(1))
        self.assertEqual(r.status_code, 403, r.text)
        r = self.client.get(f"{self.base}/ai-suggest/settings", headers=self.auth(0))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertFalse(r.json()["enabled"])  # P2 默认关
        self.assertEqual(r.json()["key_mode"], "personal")
        self.assertEqual(r.json()["allowed_field_ids"], [])
        # 乐观锁：错误 expected_version -> 409。
        r = self.client.put(f"{self.base}/ai-suggest/settings", json={
            "expected_version": 99, "enabled": True, "allowed_field_ids": ["study_design"]},
            headers=self.auth(0))
        self.assertEqual(r.status_code, 409, r.text)
        self.assertEqual(r.json()["detail"]["code"], "ai_settings_version_conflict")
        # 正确版本写入；白名单外字段 422；语境超限 422。
        r = self.enable_task()
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["enabled"])
        self.assertEqual(r.json()["version"], 1)
        self.assertTrue(r.json()["acknowledged_at"])
        r = self.client.put(f"{self.base}/ai-suggest/settings", json={
            "expected_version": 1, "enabled": True, "allowed_field_ids": ["outcome_risk"]},
            headers=self.auth(0))
        self.assertEqual(r.status_code, 422, r.text)
        r = self.client.put(f"{self.base}/ai-suggest/settings", json={
            "expected_version": 1, "enabled": True, "context_prompt": "x" * 2001},
            headers=self.auth(0))
        self.assertEqual(r.status_code, 422, r.text)

    def test_preferences_roundtrip(self):
        r = self.client.get(f"{self.base}/ai-suggest/preferences", headers=self.auth(0))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json(), {"use_personal_key": True, "trigger": "manual",
                                    "field_ids": [], "presentation": "card"})  # R3 全保守默认
        r = self.client.put(f"{self.base}/ai-suggest/preferences", json={
            "use_personal_key": False, "trigger": "auto",
            "field_ids": ["study_design"], "presentation": "badge"}, headers=self.auth(0))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.client.get(f"{self.base}/ai-suggest/preferences",
                                         headers=self.auth(0)).json()["presentation"], "badge")

    # -- R1 无 Key 零影响 ------------------------------------------------------

    def test_r1_no_key_degrades_gracefully(self):
        # 个人钥匙端点：未配置 -> 200 configured=false（无异常）。
        r = self.client.get("/api/ai/key", headers=self.auth(0))
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["configured"])
        self.assertEqual(self.client.delete("/api/ai/key", headers=self.auth(0)).status_code, 200)
        # 任务未启用 -> 403 code ai_suggest_disabled（B4 §3）。
        r = self.suggestions()
        self.assertEqual(r.status_code, 403, r.text)
        self.assertEqual(r.json()["detail"]["code"], "ai_suggest_disabled")
        # 已启用但无任何钥匙 -> 200 明确 disabled 状态（而非报错），零出站（guard 在位）。
        self.assertEqual(self.enable_task().status_code, 200)
        r = self.suggestions()
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["status"], "disabled")
        self.assertEqual(r.json()["code"], "ai_key_missing")
        self.assertEqual(r.json()["suggestions"], [])
        # 无源文本 -> 409 ai_no_source_text（优雅业务错误）。
        ctx = app.state.review_workflow.context(self.task, self.users[0])
        db.upsert_articles(ctx.owner_db, [Article(zotero_key="A2", title="No text")], [])
        self.put_personal_key()
        r = self.suggestions(key="A2")
        self.assertEqual(r.status_code, 409, r.text)
        self.assertEqual(r.json()["detail"]["code"], "ai_no_source_text")

    def test_env_override_personal_key(self):
        os.environ["REVIEWFLOW_AI_KEY"] = FAKE_KEY
        self.addCleanup(os.environ.pop, "REVIEWFLOW_AI_KEY", None)
        r = self.client.get("/api/ai/key", headers=self.auth(0))
        self.assertTrue(r.json()["configured"])
        self.assertEqual(r.json()["source"], "env")
        self.assertNotIn(FAKE_KEY, r.text)

    # -- 建议生成 ---------------------------------------------------------------

    def test_generate_drops_no_quote_and_stores_with_provenance(self):
        self.enable_task()
        self.put_personal_key()
        with mock.patch.object(ai_suggest_api, "_provider_generate", fake_generate()):
            r = self.suggestions()
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["key_source"], "personal")
        # P4：无 quote 的 sample_size 被丢弃；funding_source 为 null 属"无建议"。
        self.assertEqual([s["field_id"] for s in body["suggestions"]], ["study_design"])
        self.assertIn("dropped_no_quote:1", body["warnings"])
        suggestion = body["suggestions"][0]
        self.assertEqual(suggestion["value"], "随机对照试验")
        self.assertEqual(suggestion["quote"], "A randomised controlled trial was conducted.")
        self.assertEqual(suggestion["pages"], [])  # B9 F1：页码以服务端定位为准——命中摘要无页可跳，模型自报 [1] 不采信
        self.assertEqual(suggestion["provider"], "openai")
        self.assertEqual(suggestion["model"], "gpt-test")
        self.assertEqual(suggestion["prompt_version"], ai_suggest_api.PROMPT_VERSION)
        self.assertTrue(suggestion["generated_at"])
        self.assertTrue(suggestion["id"])
        # 建议 + 轨迹落在个人库；轨迹载荷不含钥匙明文。
        ctx = app.state.review_workflow.context(self.task, self.users[0])
        with closing(sqlite3.connect(str(ctx.actor_db))) as conn:
            rows = conn.execute("SELECT field_id, value, provider, model, prompt_version, status "
                                "FROM ai_suggestions").fetchall()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0][0], "study_design")
            self.assertEqual(rows[0][2], "openai")
            self.assertEqual(rows[0][3], "gpt-test")
            self.assertEqual(rows[0][4], ai_suggest_api.PROMPT_VERSION)
            self.assertEqual(rows[0][5], "pending")
            events = conn.execute(
                "SELECT action, stage, after_json FROM trace_events WHERE action LIKE 'ai_%' ORDER BY sequence"
            ).fetchall()
        self.assertEqual([e[0] for e in events], ["ai_suggestion_generated"])
        self.assertEqual(events[0][1], "coding")
        after = json.loads(events[0][2])
        self.assertEqual(after["provider"], "openai")
        self.assertEqual(after["prompt_version"], ai_suggest_api.PROMPT_VERSION)
        self.assertEqual(after["count"], 1)
        self.assertEqual(after["dropped_no_quote"], 1)
        self.assertNotIn(FAKE_KEY, events[0][2])

    def test_generate_field_whitelist_enforced(self):
        self.enable_task()
        self.put_personal_key()
        with mock.patch.object(ai_suggest_api, "_provider_generate", fake_generate()):
            r = self.suggestions(field_ids=["outcome_risk"])
        self.assertEqual(r.status_code, 403, r.text)
        self.assertEqual(r.json()["detail"]["code"], "ai_field_not_allowed")
        # 白名单内但任务未勾选 -> 403。
        self.client.put(f"{self.base}/ai-suggest/settings", json={
            "expected_version": 1, "enabled": True,
            "allowed_field_ids": ["study_design"]}, headers=self.auth(0))
        with mock.patch.object(ai_suggest_api, "_provider_generate", fake_generate()):
            r = self.suggestions(field_ids=["sample_size"])
        self.assertEqual(r.status_code, 403, r.text)
        # 空字段 -> 422。
        self.assertEqual(self.suggestions(field_ids=[]).status_code, 422)

    def test_generate_shared_key_mode_and_personal_preference(self):
        self.invite_member()
        self.enable_task(key_mode="shared")
        self.client.put(f"{self.base}/ai-suggest/shared-key", json={
            "provider": "deepseek", "key": FAKE_KEY, "model": "ds-test"}, headers=self.auth(0))
        # 成员无个人钥匙，任务共享钥匙自动可用（R2）。
        with mock.patch.object(ai_suggest_api, "_provider_generate", fake_generate()):
            r = self.suggestions(user_index=1)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["key_source"], "shared")
        self.assertEqual(r.json()["suggestions"][0]["provider"], "deepseek")
        # 成员声明不用个人钥匙 -> 即使配置了个人钥匙也走共享（优先级由偏好关闭个人）。
        self.put_personal_key(user_index=1)
        self.client.put(f"{self.base}/ai-suggest/preferences", json={
            "use_personal_key": False, "trigger": "manual", "field_ids": [],
            "presentation": "card"}, headers=self.auth(1))
        with mock.patch.object(ai_suggest_api, "_provider_generate", fake_generate()):
            r = self.suggestions(user_index=1)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["key_source"], "shared")

    def test_generate_personal_key_beats_shared(self):
        self.invite_member()
        self.enable_task(key_mode="shared")
        self.client.put(f"{self.base}/ai-suggest/shared-key", json={
            "provider": "deepseek", "key": FAKE_KEY}, headers=self.auth(0))
        self.put_personal_key(user_index=1)
        with mock.patch.object(ai_suggest_api, "_provider_generate", fake_generate()):
            r = self.suggestions(user_index=1)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["key_source"], "personal")  # 个人 > 任务共享
        self.assertEqual(r.json()["suggestions"][0]["provider"], "openai")

    def test_provider_error_maps_to_502_without_key(self):
        self.enable_task()
        self.put_personal_key()

        def exploding(system, user, *, api_key, base_url, model, timeout=60.0):
            raise ai_suggest_api.ProviderError(f"provider HTTP 401: invalid key {FAKE_KEY}", status=401)

        with mock.patch.object(ai_suggest_api, "_provider_generate", exploding):
            r = self.suggestions()
        self.assertEqual(r.status_code, 502, r.text)
        self.assertNotIn(FAKE_KEY, r.text)  # key 永不进错误信息
        self.assertIn("***", r.json()["detail"]["detail"])

    def test_verify_and_test_connection_endpoints(self):
        def exploding(*args, **kwargs):
            raise AssertionError("verify=false 时不得出站")

        # 默认 verify=false：保存离线可用，不触发出站。
        with mock.patch.object(ai_suggest_api, "_provider_generate", exploding):
            r = self.put_personal_key()
        self.assertEqual(r.status_code, 200, r.text)
        # 测试连接：成功路径。
        with mock.patch.object(ai_suggest_api, "_provider_generate", fake_generate()):
            r = self.client.post("/api/ai/key/test", headers=self.auth(0))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["ok"])
        self.assertGreaterEqual(r.json()["latency_ms"], 0)
        # 测试连接：未配置钥匙 -> 409 ai_key_missing。
        self.client.delete("/api/ai/key", headers=self.auth(0))
        r = self.client.post("/api/ai/key/test", headers=self.auth(0))
        self.assertEqual(r.status_code, 409, r.text)
        self.assertEqual(r.json()["detail"]["code"], "ai_key_missing")

    # -- 建议决定（P1/P5） -------------------------------------------------------

    def test_decision_flow_and_no_coding_write(self):
        self.enable_task()
        self.put_personal_key()
        with mock.patch.object(ai_suggest_api, "_provider_generate", fake_generate()):
            body = self.suggestions().json()
        sid = body["suggestions"][0]["id"]
        # adopt：只回传建议值，不写编码值（P1）。
        r = self.client.post(f"{self.base}/coding/ai-suggestions/{sid}/decision",
                             json={"status": "adopted"}, headers=self.auth(0))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["suggestion"]["status"], "adopted")
        self.assertTrue(r.json()["suggestion"]["decided_at"])
        self.assertEqual(r.json()["value"], "随机对照试验")
        ctx = app.state.review_workflow.context(self.task, self.users[0])
        with closing(sqlite3.connect(str(ctx.actor_db))) as conn:
            has_values = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE name='coding_values'").fetchone()
            if has_values:
                self.assertEqual(conn.execute(
                    "SELECT COUNT(*) FROM coding_values WHERE zotero_key='A1'").fetchone()[0], 0)
            events = conn.execute(
                "SELECT action, before_json, after_json FROM trace_events "
                "WHERE action LIKE 'ai_%' ORDER BY sequence").fetchall()
        self.assertEqual([e[0] for e in events],
                         ["ai_suggestion_generated", "ai_suggestion_decided"])
        before, after = json.loads(events[1][1]), json.loads(events[1][2])
        self.assertEqual(before["status"], "pending")
        self.assertEqual(before["field_id"], "study_design")
        self.assertEqual(after["status"], "adopted")
        # 已决定的建议再决定 -> 409。
        r = self.client.post(f"{self.base}/coding/ai-suggestions/{sid}/decision",
                             json={"status": "ignored"}, headers=self.auth(0))
        self.assertEqual(r.status_code, 409, r.text)
        # 列表端点回显最终状态。
        listing = self.client.get(f"{self.base}/coding/ai-suggestions/A1", headers=self.auth(0))
        self.assertEqual(listing.json()["suggestions"][0]["status"], "adopted")

    def test_decision_edited_requires_final_value(self):
        self.enable_task()
        self.put_personal_key()
        with mock.patch.object(ai_suggest_api, "_provider_generate", fake_generate()):
            sid = self.suggestions().json()["suggestions"][0]["id"]
        r = self.client.post(f"{self.base}/coding/ai-suggestions/{sid}/decision",
                             json={"status": "edited"}, headers=self.auth(0))
        self.assertEqual(r.status_code, 400, r.text)
        r = self.client.post(f"{self.base}/coding/ai-suggestions/{sid}/decision",
                             json={"status": "edited", "final_value": "RCT（三臂）"},
                             headers=self.auth(0))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["value"], "RCT（三臂）")
        self.assertEqual(r.json()["suggestion"]["final_value"], "RCT（三臂）")

    def test_decision_isolated_per_user(self):
        self.enable_task()
        self.put_personal_key(user_index=0)
        self.invite_member()
        with mock.patch.object(ai_suggest_api, "_provider_generate", fake_generate()):
            sid = self.suggestions().json()["suggestions"][0]["id"]
        # 另一成员看不到 owner 的建议 id（个人层 403/404 隔离）。
        r = self.client.post(f"{self.base}/coding/ai-suggestions/{sid}/decision",
                             json={"status": "adopted"}, headers=self.auth(1))
        self.assertEqual(r.status_code, 404, r.text)
        listing = self.client.get(f"{self.base}/coding/ai-suggestions/A1", headers=self.auth(1))
        self.assertEqual(listing.json()["total"], 0)

    # -- 审计导出 / Methods 报告 / 导出包钥匙隔离 -------------------------------

    def test_export_csv_bom_and_formula_guard(self):
        content = json.dumps({"study_design": {
            "value": "=SUM(A1:A9)", "quote": "A randomised controlled trial was conducted.",
            "pages": [2]}}, ensure_ascii=False)
        self.enable_task()
        self.put_personal_key()
        with mock.patch.object(ai_suggest_api, "_provider_generate", fake_generate(content)):
            body = self.suggestions().json()
        sid = body["suggestions"][0]["id"]
        self.client.post(f"{self.base}/coding/ai-suggestions/{sid}/decision",
                         json={"status": "adopted"}, headers=self.auth(0))
        r = self.client.get(f"{self.base}/ai-suggestions/export?format=csv", headers=self.auth(0))
        self.assertEqual(r.status_code, 200, r.text)
        raw = r.content
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))  # UTF-8-BOM
        text = raw.decode("utf-8-sig")
        self.assertIn("'=SUM(A1:A9)", text)  # 公式注入防护
        self.assertIn("study_design", text)
        self.assertNotIn(FAKE_KEY, text)
        r = self.client.get(f"{self.base}/ai-suggestions/export?format=json", headers=self.auth(0))
        self.assertEqual(r.json()["records"][0]["status"], "adopted")
        self.assertNotIn(FAKE_KEY, r.text)

    def test_methods_report(self):
        self.enable_task()
        self.put_personal_key()
        with mock.patch.object(ai_suggest_api, "_provider_generate", fake_generate()):
            sid = self.suggestions().json()["suggestions"][0]["id"]
        self.client.post(f"{self.base}/coding/ai-suggestions/{sid}/decision",
                         json={"status": "adopted"}, headers=self.auth(0))
        r = self.client.get(f"{self.base}/ai-suggestions/methods-report", headers=self.auth(0))
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["counts"]["adopted"], 1)
        self.assertIn(ai_suggest_api.PROMPT_VERSION, body["report_zh"])
        self.assertIn("BYOK", body["report_en"])
        self.assertNotIn(FAKE_KEY, r.text)

    def test_trace_export_zip_never_contains_vault_or_key(self):
        self.invite_member()
        self.enable_task(key_mode="shared")
        self.client.put(f"{self.base}/ai-suggest/shared-key", json={
            "provider": "openai", "key": FAKE_KEY}, headers=self.auth(0))
        self.put_personal_key()
        with mock.patch.object(ai_suggest_api, "_provider_generate", fake_generate()):
            body = self.suggestions().json()
        sid = body["suggestions"][0]["id"]
        self.client.post(f"{self.base}/coding/ai-suggestions/{sid}/decision",
                         json={"status": "adopted"}, headers=self.auth(0))
        # 轨迹导出 ZIP：B5a 隔离契约第三条——任何导出包不得出现 .ai_keys 路径与钥匙明文。
        r = self.client.post(f"{self.base}/trace/exports", json={"preset": "complete"},
                             headers=self.auth(0))
        self.assertEqual(r.status_code, 200, r.text)
        with zipfile.ZipFile(io.BytesIO(r.content)) as archive:
            names = archive.namelist()
            self.assertTrue(names)
            for name in names:
                self.assertNotIn(".ai_keys", name)
                self.assertNotIn("ai_keys", name)
            for name in names:
                self.assertNotIn(FAKE_KEY.encode(), archive.read(name))
        # 保险柜文件本身存在且在数据目录根下（不在任务目录，天然不进任务导出/备份包）。
        vault = DATA_ROOT / ".ai_keys"
        self.assertTrue((vault / f"personal_{self.users[0]['user_id']}.json").is_file())
        self.assertTrue((vault / f"shared_{self.task}.json").is_file())
        task_dir = app.state.review_workflow.context(self.task, self.users[0]).info.dir
        for hidden in vault.iterdir():
            self.assertFalse(str(hidden).startswith(str(task_dir)))

    def test_trace_export_contains_ai_events_without_key(self):
        """B8 补测：既有轨迹导出路径必须携带 ai_ 事件（P6/P7 随包审计），载荷含
        provider/model/prompt_version/字段与状态，且绝无钥匙明文。"""
        self.enable_task()
        self.put_personal_key()
        with mock.patch.object(ai_suggest_api, "_provider_generate", fake_generate()):
            body = self.suggestions().json()
        sid = body["suggestions"][0]["id"]
        self.client.post(f"{self.base}/coding/ai-suggestions/{sid}/decision",
                         json={"status": "edited", "final_value": "RCT（三臂）"},
                         headers=self.auth(0))
        r = self.client.post(f"{self.base}/trace/exports", json={"preset": "complete"},
                             headers=self.auth(0))
        self.assertEqual(r.status_code, 200, r.text)
        events: list[dict] = []
        with zipfile.ZipFile(io.BytesIO(r.content)) as archive:
            members = [n for n in archive.namelist()
                       if n == "events.jsonl" or re.match(r"events-part-\d+\.jsonl", n)]
            self.assertTrue(members, "导出包必须包含 events.jsonl（或分卷）")
            for name in members:
                for line in archive.read(name).decode("utf-8").splitlines():
                    if line.strip():
                        events.append(json.loads(line))
        by_action = {e.get("action"): e for e in events}
        self.assertIn("ai_suggestion_generated", by_action)
        self.assertIn("ai_suggestion_decided", by_action)
        generated = by_action["ai_suggestion_generated"]
        self.assertEqual(generated.get("stage"), "coding")
        after_gen = generated.get("after") or {}
        self.assertEqual(after_gen.get("provider"), "openai")
        self.assertEqual(after_gen.get("model"), "gpt-test")
        self.assertEqual(after_gen.get("prompt_version"), ai_suggest_api.PROMPT_VERSION)
        self.assertEqual(after_gen.get("count"), 1)
        self.assertEqual(after_gen.get("fields"), [{"field_id": "study_design", "value": "随机对照试验"}])
        decided = by_action["ai_suggestion_decided"]
        self.assertEqual(decided.get("stage"), "coding")
        self.assertEqual((decided.get("after") or {}).get("status"), "edited")
        self.assertEqual((decided.get("after") or {}).get("final_value"), "RCT（三臂）")
        # 钥匙明文红线：任何事件载荷（含 before/after）都不得携带。
        blob = json.dumps(events, ensure_ascii=False)
        self.assertNotIn(FAKE_KEY, blob)
        self.assertNotIn(".ai_keys", blob)


    # ---- B9 must-fix 修复回归 -------------------------------------------------

    def test_b9_f1_fake_quote_dropped_and_server_locates_pages(self):
        """B9 F1：引文核验——不在原文的建议丢弃；命中摘要保留且不标页码；模型 pages 不采信。"""
        self.enable_task(key_mode="personal")
        self.put_personal_key()
        real_quote = "a randomised controlled trial was conducted in 6 centres"
        fake_quote = "THIS SENTENCE NEVER APPEARED IN THE ARTICLE"
        content = json.dumps({
            "study_design": {"value": "随机对照试验", "quote": real_quote, "pages": [9]},
            "funding_source": {"value": "NIH", "quote": fake_quote, "pages": [1]},
        }, ensure_ascii=False)
        with mock.patch.object(ai_suggest_api, "_provider_generate", fake_generate(content)):
            r = self.suggestions()
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual([x["field_id"] for x in body["suggestions"]], ["study_design"])
        self.assertEqual(body["suggestions"][0]["pages"], [])  # 命中摘要：无页可跳；模型自报 [9] 不采信
        self.assertIn("unverified_quote:funding_source", body["warnings"])

    def test_b9_f1_page_location_with_fulltext_pages(self):
        """B9 F1：有全文时服务端把引文定位到真实页号。"""
        self.enable_task(key_mode="personal")
        self.put_personal_key()
        from coscreen import fulltext as fulltext_mod
        pdf = Path(__file__).resolve().parents[2] / "build" / "tests" / "data"
        pdf.mkdir(parents=True, exist_ok=True)
        # 构造两页文本的假 PDF 太重——直接单测 _parse_provider_content 的定位逻辑。
        pages = ["Page one talks about funding from the NIH institute only.",
                 "Page two is about something entirely different here."]
        content = json.dumps({
            "funding_source": {"value": "NIH", "quote": "funding from the NIH institute", "pages": [7]},
        }, ensure_ascii=False)
        kept, warnings, dropped = ai_suggest_api._parse_provider_content(content, ["funding_source"], pages, "")
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["pages"], [1])   # 服务端定位=第 1 页；模型自报 [7] 被忽略
        self.assertEqual(dropped, 0)

    def test_b9_f9_malformed_provider_payload_never_500(self):
        """B9 F9：provider 载荷形状非法（pages 非数组/值非标量）按无效丢弃，不冒泡 500。"""
        self.enable_task(key_mode="personal")
        self.put_personal_key()
        content = json.dumps({
            "study_design": {"value": "RCT", "quote": "a randomised controlled trial", "pages": 5},
            "sample_size": {"value": ["not", "scalar"], "quote": "in 6 centres", "pages": [1]},
        }, ensure_ascii=False)
        with mock.patch.object(ai_suggest_api, "_provider_generate", fake_generate(content)):
            r = self.suggestions()
        self.assertEqual(r.status_code, 200, r.text)

    def test_b9_f2_delete_failure_maps_503_and_key_remains(self):
        """B9 F2：删除失败绝不假报成功——503 + 钥匙仍在 + GET 仍 configured。"""
        self.put_personal_key()
        path = DATA_ROOT / ".ai_keys" / f"personal_{self.users[0]['user_id']}.json"
        self.assertTrue(path.is_file())
        with mock.patch.object(Path, "unlink", side_effect=PermissionError("locked")):
            r = self.client.delete("/api/ai/key", headers=self.auth(0))
        self.assertEqual(r.status_code, 503, r.text)
        self.assertEqual(r.json()["detail"]["code"], "ai_key_delete_failed")
        self.assertTrue(path.is_file())
        self.assertTrue(self.client.get("/api/ai/key", headers=self.auth(0)).json()["configured"])

    def test_b9_f8_prefs_field_subset_enforced_server_side(self):
        """B9 F8：三级白名单服务端强制——偏好子集外的字段 403。"""
        self.invite_member()
        prefs = self.client.put(f"{self.base}/ai-suggest/preferences", json={
            "use_personal_key": True, "trigger": "manual",
            "field_ids": ["study_design"], "presentation": "card"}, headers=self.auth(1))
        self.assertEqual(prefs.status_code, 200, prefs.text)
        self.put_personal_key(user_index=1)
        self.enable_task(key_mode="personal")
        r = self.suggestions(field_ids=["sample_size"], user_index=1)
        self.assertEqual(r.status_code, 403, r.text)
        self.assertIn("ai_field_not_allowed", r.text)
        with mock.patch.object(ai_suggest_api, "_provider_generate", fake_generate()):
            r2 = self.suggestions(field_ids=["study_design"], user_index=1)
        self.assertEqual(r2.status_code, 200, r2.text)

    def test_b9_f10_redirect_refused_key_not_forwarded(self):
        """B9 F10：3xx 重定向被拒绝，Authorization 钥匙绝不转发到重定向目标。"""
        import http.server
        import threading
        seen_headers = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                seen_headers.append(dict(self.headers))
                if self.path.endswith("/chat/completions"):
                    self.send_response(302)
                    self.send_header("Location",
                                     f"http://127.0.0.1:{self.server.server_address[1]}/v1/target")
                    self.end_headers()
                else:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"choices":[{"message":{"content":"{}"}}],"usage":{}}')

            def log_message(self, *args):
                pass

        srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=srv.serve_forever, daemon=True)
        thread.start()
        self.guard.stop()          # 本用例直连真实 _provider_generate（默认守卫会拦）
        try:
            port = srv.server_address[1]
            with self.assertRaises(ai_suggest_api.ProviderError) as ctx:
                ai_suggest_api._provider_generate(
                    "s", "u", api_key="sk-redirect-secret-123456",
                    base_url=f"http://127.0.0.1:{port}/v1", model="m", timeout=5)
            self.assertIn("重定向", str(ctx.exception))
            # 只收到最初的 /chat/completions 一次请求——目标路径从未被打到
            self.assertEqual(len(seen_headers), 1)
        finally:
            self.guard.start()
            srv.shutdown()
            srv.server_close()

    def test_b9_f12_metadata_base_url_blocked(self):
        """B9 F12 缓解：云元数据/链路本地 base_url 在校验层被拒。"""
        for bad in ("http://169.254.169.254/v1", "http://metadata.google.internal/v1",
                    "http://100.100.100.200/v1", "http://[fe80::1]/v1"):
            with self.assertRaises(ai_keys.KeyShapeError, msg=bad):
                ai_keys.base_url_of({"base_url": bad})
        # loopback 保留（本地模型卖点）
        self.assertEqual(ai_keys.base_url_of({"base_url": "http://127.0.0.1:11434/v1"}),
                         "http://127.0.0.1:11434/v1")

    def test_b9_f15_policy_version_conflict(self):
        """B9 F15：expected_policy_version 与设置版本不符 -> 409。"""
        self.put_personal_key()
        self.enable_task(key_mode="personal")
        r = self.client.post(f"{self.base}/coding/ai-suggestions", json={
            "zotero_key": "A1", "field_ids": ["study_design"],
            "expected_policy_version": 999}, headers=self.auth(0))
        self.assertEqual(r.status_code, 409, r.text)
        self.assertIn("ai_policy_version_conflict", r.text)

    def test_b9_f16_truncation_keeps_citation_and_abstract(self):
        """B9 F16：超预算截断先裁全文页，题录与摘要永不丢。"""
        pages = ["p" * 30000, "q" * 30000]
        prefix = "文献题录：{'title': 'Trial'}\n\n摘要：a randomised controlled trial"
        message, overflow = ai_suggest_api._truncate_user_message(prefix, pages)
        self.assertIn("文献题录", message)
        self.assertIn("摘要", message)
        self.assertGreater(overflow, 0)
        self.assertLessEqual(len(message), ai_suggest_api.MAX_CONTEXT_CHARS + len(prefix))


class FrontendStaticGuardTests(unittest.TestCase):
    """B8 浏览器回归发现的成员生成缺陷的源码钉子（不启动浏览器）。"""

    def test_request_fields_use_task_whitelist_for_both_roles(self):
        """requestFields 字段基不得按 owner/成员区分：成员基误用 FIELD_IDS（全 7 字段）
        时，任务白名单只勾部分字段的成员生成会被 ai_field_not_allowed 整单 403
        （B8 S3/S4 实测复现：状态行“以下字段未开放”，无任何建议卡）。"""
        source = (Path(__file__).resolve().parents[2] / "custom_frontend" / "ai_suggest.js").read_text(
            encoding="utf-8")
        match = re.search(r"function requestFields\(\)\s*\{(.+?)\n\s{4}\}", source, re.S)
        self.assertTrue(match, "ai_suggest.js 未找到 requestFields 源码")
        body = match.group(1)
        self.assertIn("detection.allowedFields || []", body,
                      "字段基必须统一取任务白名单（owner=settings、成员=status）")
        self.assertNotIn("detection.isOwner ? (detection.allowedFields", body,
                          "不得按角色区分字段基（成员全字段请求会 403）")


if __name__ == "__main__":
    unittest.main()