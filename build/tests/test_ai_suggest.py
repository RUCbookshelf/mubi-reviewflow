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
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        fixture.cleanup()

    def auth(self, i=0):
        return {"Authorization": "Bearer " + self.users[i]["token"]}

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
        # 门禁墙钟（类级模板库，同 test_report_evidence 手法）：原 setUp 每用例
        # 「POST + 首建迁移链 + 种入 A1」约 200ms；类级建真实模板任务并快照
        # 库字节，每用例复制（~3ms），隔离与内容与逐用例自建同源。
        from coscreen import tasks as tasks_mod
        cls._slug = tasks_mod.slugify_screener(cls.users[0]["username"])
        template_task = cls.client.post(
            "/api/tasks", json={"name": "ai-suggest-template"},
            headers={"Authorization": "Bearer " + cls.users[0]["token"]}).json()["task_id"]
        ctx0 = app.state.review_workflow.context(template_task, cls.users[0])
        db.upsert_articles(ctx0.owner_db, [Article(
            zotero_key="A1", title="Trial of Example Therapy",
            authors="Doe J; Smith A", journal="J Example", year=2024, doi="10.test/ai",
            abstract="Background: example. Methods: a randomised controlled trial was conducted in 6 centres.")], [])
        cls._template_db_bytes = Path(ctx0.owner_db).read_bytes()

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
        from coscreen import tasks as tasks_mod
        info = tasks_mod.find_task(self.task, str(DATA_ROOT))
        (info.dir / (self._slug + ".db")).write_bytes(self._template_db_bytes)
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
                # 先读完请求体再响应，否则 Windows 下连接在客户端发送途中被重置，
                # 客户端拿到 ConnectionAbortedError 而非 302（flaky 根因）。
                length = int(self.headers.get("Content-Length") or 0)
                if length:
                    self.rfile.read(length)
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
        # 本地回环端点（本地模型语义）= desktop 模式放行（sec29b W4① 起
        # _provider_generate 自带出站闸，server 模式回环在连接层也被拦——
        # 同 test_mm_06_* 的模式钉法）。
        with mock.patch.dict(os.environ, {"REVIEWFLOW_MODE": "desktop"}):
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
        # loopback 仅 desktop 模式保留（本地模型卖点；sec29 F-5 起 server 拦截）
        with mock.patch.dict(os.environ, {"REVIEWFLOW_MODE": "desktop"}):
            self.assertEqual(ai_keys.base_url_of({"base_url": "http://127.0.0.1:11434/v1"}),
                             "http://127.0.0.1:11434/v1")
        with self.assertRaises(ai_keys.KeyShapeError):
            ai_keys.base_url_of({"base_url": "http://127.0.0.1:11434/v1"})  # server

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


    # ---- B9 R 轮（pass_with_notes）修复回归 -----------------------------------
    def test_b8r2_obs1_verified_persists_for_history_reload(self):
        """B8 v2 观察项：verified（部分核验）入库，历史重载角标不丢。"""
        # 单元层：partial 判定成立（>80 字真前缀+假尾段）
        pages = [("real prefix " * 9).strip() + " and more real content continues here"]
        fabricated = "real prefix " * 9 + " THEN A COMPLETELY FABRICATED TAIL NOT IN SOURCE"
        content = json.dumps({"study_design": {"value": "X", "quote": fabricated, "pages": []}}, ensure_ascii=False)
        kept, warnings, _ = ai_suggest_api._parse_provider_content(content, ["study_design"], pages, "")
        self.assertEqual(kept[0]["verified"], "partial")
        self.assertIn("partial_quote:study_design", warnings)
        # API 层（无 PDF 走摘要，full 路径）：verified 随建议入库并可在历史接口回读
        api_content = json.dumps({"study_design": {
            "value": "随机对照试验", "quote": "a randomised controlled trial was conducted", "pages": [3]}}, ensure_ascii=False)
        self.enable_task(key_mode="personal", field_ids=["study_design"])
        self.put_personal_key()
        with mock.patch.object(ai_suggest_api, "_provider_generate", fake_generate(api_content)):
            body = self.suggestions(field_ids=["study_design"]).json()
        self.assertEqual(body["suggestions"][0]["verified"], "full")
        listed = self.client.get(
            f"{self.base}/coding/ai-suggestions/A1", headers=self.auth(0)).json()["suggestions"]
        self.assertEqual([x["verified"] for x in listed], ["full"])
        # 旧库兼容：手工删列场景由 ALTER 守卫覆盖；此处断言 schema 含列
        ctx = app.state.review_workflow.context(self.task, self.users[0])
        with closing(sqlite3.connect(str(ctx.actor_db))) as conn:
            cols = [r[1] for r in conn.execute("PRAGMA table_info(ai_suggestions)").fetchall()]
        self.assertIn("verified", cols)



    def test_b9_r1_prefix_fabricated_tail_is_partial(self):
        """B9 R-1：真前缀+编造尾段 -> 保留但标注 partial；不再冒充完全核验。"""
        pages = [("real prefix " * 9).strip() + " and more real content continues here", "other page"]
        fabricated = "real prefix " * 9 + " THEN A COMPLETELY FABRICATED TAIL THAT IS NOT IN THE SOURCE"
        content = json.dumps({"study_design": {"value": "X", "quote": fabricated, "pages": []}}, ensure_ascii=False)
        kept, warnings, dropped = ai_suggest_api._parse_provider_content(content, ["study_design"], pages, "")
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["verified"], "partial")
        self.assertIn("partial_quote:study_design", warnings)
        self.assertEqual(dropped, 0)

    def test_b9_r1_short_quote_exact_match_allowed(self):
        """B9 R-1：>=3 字符的短引文走完整精确匹配（不被 8 字门槛误杀）。"""
        pages = ["mentions 6 centres and other things"]
        content = json.dumps({"study_design": {"value": "X", "quote": "6 centres", "pages": [3]}}, ensure_ascii=False)
        kept, _warnings, dropped = ai_suggest_api._parse_provider_content(content, ["study_design"], pages, "")
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["verified"], "full")
        self.assertEqual(kept[0]["pages"], [1])   # 服务端定位，模型自报 [3] 不采信
        self.assertEqual(dropped, 0)
        # 原文中不存在的短引文仍被丢弃
        content2 = json.dumps({"study_design": {"value": "X", "quote": "zz9 plural", "pages": []}}, ensure_ascii=False)
        kept2, warnings2, _ = ai_suggest_api._parse_provider_content(content2, ["study_design"], pages, "")
        self.assertEqual(kept2, [])
        self.assertIn("unverified_quote:study_design", warnings2)

    def test_b9_r10_warnings_separate_no_quote_and_unverified(self):
        """B9 R-10：dropped_no_quote 只计无引文；unverified 独立成 warning。"""
        pages = ["source text about centres exists here"]
        content = json.dumps({
            "study_design": {"value": "X", "quote": "NOT IN SOURCE AT ALL", "pages": []},
            "funding_source": {"value": "Y", "quote": "", "pages": []},
        }, ensure_ascii=False)
        kept, warnings, dropped = ai_suggest_api._parse_provider_content(
            content, ["study_design", "funding_source"], pages, "")
        self.assertEqual(kept, [])
        self.assertEqual(dropped, 1)                       # 仅 funding_source（无引文）
        self.assertIn("unverified_quote:study_design", warnings)

    def test_b9_r6_metadata_bypass_literals_blocked(self):
        """B9 R-6：IPv6 ULA/映射、十进制 IPv4、尾点域名等字面变体全部拦截；
        sec29 F-5 起 RFC1918/CGNAT 私网段同样拦截（两种运行模式一致）。"""
        for bad in ("http://[fc00::1]/v1", "http://[fd12::1]/v1",
                    "http://[::ffff:169.254.169.254]/v1",
                    "http://[::ffff:10.0.0.5]/v1",
                    "http://metadata.google.internal.:8080/v1",
                    "http://2852039166/v1", "http://169.254.169.254/v1",
                    "http://10.0.0.5/v1", "http://192.168.1.10/v1",
                    "http://172.16.0.9/v1", "http://100.100.100.200/v1"):
            with self.assertRaises(ai_keys.KeyShapeError, msg=bad):
                ai_keys.base_url_of({"base_url": bad})
        # 回环保留仅限 desktop（本地模型）；两种模式私网段都拦
        with mock.patch.dict(os.environ, {"REVIEWFLOW_MODE": "desktop"}):
            self.assertTrue(ai_keys.base_url_of({"base_url": "http://127.0.0.1:11434/v1"}))
        with self.assertRaises(ai_keys.KeyShapeError):
            ai_keys.base_url_of({"base_url": "http://10.0.0.5/v1"})  # desktop 也拦

    def test_b9_r10_methods_report_lists_models(self):
        self.enable_task()
        self.put_personal_key()
        with mock.patch.object(ai_suggest_api, "_provider_generate", fake_generate()):
            sid = self.suggestions().json()["suggestions"][0]["id"]
        self.client.post(f"{self.base}/coding/ai-suggestions/{sid}/decision",
                         json={"status": "adopted"}, headers=self.auth(0))
        body = self.client.get(f"{self.base}/ai-suggestions/methods-report", headers=self.auth(0)).json()
        self.assertIn("gpt-test", body["report_zh"])       # 模型清单（B9 R-10）
        self.assertIn("gpt-test", body["report_en"])

    def test_b9_r10_migration_backup_never_touches_vault(self):
        """B9 R-10/B5a-3：迁移/删除走显式文件清单，永不触碰 .ai_keys。"""
        tasks_src = (Path(__file__).resolve().parents[2] / "coscreen" / "tasks.py").read_text(encoding="utf-8")
        self.assertNotIn(".ai_keys", tasks_src)
        self.assertIn("def _backup_zip(files", tasks_src)   # 显式清单打包

    # ---- r7 MM2-01/MM2-04/MM-06/MM-09 修复回归（破坏即红钉子） ------------------

    def test_mm2_01_short_quote_below_threshold_never_full(self):
        """MM2-01/MM4-03 下界钉子：短于 MIN_FULL_QUOTE_CHARS 的引文即使逐字命中原文
        也不得判 verified='full'（原漏洞：3 字符常用词给捏造值发 full）；
        恰达门槛的引文仍 full（上界不收紧过头，B9 R-1 的 9 字符裁决不受影响）。
        门槛常量进测试：删除/调低门槛（回 3）或删长度判断，本用例必须变红。"""
        threshold = ai_suggest_api.MIN_FULL_QUOTE_CHARS
        self.assertGreaterEqual(threshold, 8, "full 核验门槛不得低于 8 字符")
        self.assertLessEqual(threshold, 12, "full 核验门槛不得高于 12 字符")
        source_token = "abcdefghijkl"                      # 12 字符，均在原文中
        pages = [f"context words {source_token} tail here"]
        below = source_token[:threshold - 1]               # 恰低于门槛（≥7 字符）
        at = source_token[:threshold]                      # 恰达门槛
        content = json.dumps({"sample_size": {
            "value": "9999999", "quote": below, "pages": [9]}}, ensure_ascii=False)
        kept, warnings, _dropped = ai_suggest_api._parse_provider_content(
            content, ["sample_size"], pages, "")
        self.assertEqual(len(kept), 1, "短引文命中仍保留（不新增丢弃面）")
        self.assertNotEqual(kept[0]["verified"], "full",
                            f"{threshold - 1} 字符引文不得判 full")
        self.assertEqual(kept[0]["verified"], "partial")
        self.assertIn("partial_quote:sample_size", warnings)
        content2 = json.dumps({"sample_size": {
            "value": "X", "quote": at, "pages": []}}, ensure_ascii=False)
        kept2, _w, _d = ai_suggest_api._parse_provider_content(content2, ["sample_size"], pages, "")
        self.assertEqual(kept2[0]["verified"], "full", "恰达门槛的完整命中仍应 full")

    def test_mm2_01_fabricated_value_short_quote_persists_partial(self):
        """MM2-01 端到端（原复现路径）：真实存在的 3 字符引文（'tri' 是 'trial' 的
        子串）不再能把捏造值标 verified='full' 入库——降为 partial，历史回读一致。"""
        self.enable_task()
        self.put_personal_key()
        content = json.dumps({"sample_size": {
            "value": "9999999", "quote": "tri", "pages": [2]}}, ensure_ascii=False)
        with mock.patch.object(ai_suggest_api, "_provider_generate", fake_generate(content)):
            body = self.suggestions(field_ids=["sample_size"]).json()
        self.assertEqual(body["suggestions"][0]["verified"], "partial")
        self.assertEqual(body["suggestions"][0]["pages"], [])   # 命中摘要：无页可跳
        self.assertIn("partial_quote:sample_size", body["warnings"])
        listed = self.client.get(
            f"{self.base}/coding/ai-suggestions/A1", headers=self.auth(0)).json()["suggestions"]
        self.assertEqual([x["verified"] for x in listed], ["partial"])

    def test_mm2_01_verified_column_never_defaults_full(self):
        """MM2-01 入库缺省钉子：verified 列 DEFAULT 不得为 'full'（新表与旧库补列
        两条路径都查）——缺省 full 会让任何漏给 verified 的写入自动获得最强标记。"""
        with tempfile.TemporaryDirectory() as tmp:
            fresh = Path(tmp) / "fresh.db"
            with closing(ai_suggest_api._connect(fresh)) as conn:
                cols = conn.execute("PRAGMA table_info(ai_suggestions)").fetchall()
            dflt = [c[4] for c in cols if c[1] == "verified"]
            self.assertEqual(len(dflt), 1)
            self.assertNotEqual((dflt[0] or "").strip("'"), "full",
                                "新建表 verified DEFAULT 不得为 full")
            legacy = Path(tmp) / "legacy.db"
            with closing(sqlite3.connect(str(legacy))) as conn:
                # 旧库形状：除 verified 外的完整列（建表 SQL 的索引需要这些列）。
                conn.execute(
                    "CREATE TABLE ai_suggestions ("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, "
                    "actor_id TEXT NOT NULL, zotero_key TEXT NOT NULL, field_id TEXT NOT NULL, "
                    "value TEXT NOT NULL, quote TEXT NOT NULL, pages_json TEXT NOT NULL, "
                    "provider TEXT NOT NULL, model TEXT NOT NULL, prompt_version TEXT NOT NULL, "
                    "final_value TEXT, status TEXT NOT NULL, created_at TEXT NOT NULL, "
                    "decided_at TEXT)")
            with closing(ai_suggest_api._connect(legacy)) as conn:
                cols = conn.execute("PRAGMA table_info(ai_suggestions)").fetchall()
            dflt = [c[4] for c in cols if c[1] == "verified"]
            self.assertEqual(len(dflt), 1)
            self.assertNotEqual((dflt[0] or "").strip("'"), "full",
                                "旧库补列 verified DEFAULT 不得为 full")

    def test_mm2_01_04_methods_report_quote_semantics(self):
        """MM2-01/MM2-04 Methods 文案钉子：不再声称"每条建议均附页码"（摘要命中
        pages=[]）；改为 正文来源附页码、摘要来源标注摘要；并明示核验只证明引文
        存在于原文、不代表核验了建议值。"""
        self.enable_task()
        self.put_personal_key()
        with mock.patch.object(ai_suggest_api, "_provider_generate", fake_generate()):
            self.suggestions()
        body = self.client.get(f"{self.base}/ai-suggestions/methods-report",
                               headers=self.auth(0)).json()
        zh, en = body["report_zh"], body["report_en"]
        self.assertNotIn("每条建议均附原文引文与页码", zh)
        self.assertNotIn("Every suggestion carried a source quote and page numbers", en)
        self.assertIn("仅取自摘要的建议标注为摘要来源", zh)
        self.assertIn("引文取自全文正文的建议附服务端定位的页码", zh)
        self.assertIn("不代表核验了建议值本身", zh)
        self.assertIn("labelled as abstract-sourced", en)
        self.assertIn("did not validate the suggested value itself", en)

    def test_mm_06_provider_http_error_body_never_echoed(self):
        """MM-06 钉子：provider 非 2xx 只回状态码＋固定文案——响应体内容（原先
        回显前 200 字节）不得出现在任何错误消息里（verify=true/生成/测试连接共用
        该出口；回显构成内网 SSRF 读预言机）。"""
        import http.server
        import threading

        secret = "INTERNAL-SECRET-CONTENT-xyz789"

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                if length:
                    self.rfile.read(length)
                payload = json.dumps({"error": {"message": secret}}).encode("utf-8")
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.guard.stop()      # 本用例直连真实 _provider_generate（仅 127.0.0.1）
        # 本文件默认 server 模式；本地回环端点（本地模型语义）= desktop 模式放行
        # （sec29 F-5：server 模式回环一律拦）。
        with mock.patch.dict(os.environ, {"REVIEWFLOW_MODE": "desktop"}):
            try:
                # 单元层：ProviderError 消息含状态码、绝不含响应体内容。
                with self.assertRaises(ai_suggest_api.ProviderError) as ctx:
                    ai_suggest_api._provider_generate(
                        "s", "u", api_key="sk-echo-test-123456",
                        base_url=f"http://127.0.0.1:{srv.server_address[1]}/v1",
                        model="m", timeout=5)
                self.assertEqual(ctx.exception.status, 500)
                self.assertIn("500", str(ctx.exception))
                self.assertNotIn(secret, str(ctx.exception))
                # API 层（MM-06 原复现路径）：verify=true 出站失败 -> 502 只回状态码。
                r = self.client.put("/api/ai/key", json={
                    "provider": "openai", "key": FAKE_KEY,
                    "base_url": f"http://127.0.0.1:{srv.server_address[1]}/v1",
                    "verify": True}, headers=self.auth(0))
                self.assertEqual(r.status_code, 502, r.text)
                self.assertIn("500", r.text)
                self.assertNotIn(secret, r.text)
            finally:
                self.guard.start()
                srv.shutdown()
                srv.server_close()

    def test_mm_09_base_url_control_characters_rejected(self):
        """MM-09 钉子：base_url 含 C0/C1 控制字符（含 \\r\\n 头注入尝试）在入库前
        被拒（422）——不再放行到出站才抛 http.client.InvalidURL 冒泡 500。"""
        for bad in ("https://evil.example/v1\r\nX-Injected: 1",
                    "https://evil.example/v1\x00x",
                    "https://evil.example/v1\x7f",
                    "https://evil.example/v\x85i"):
            # 注：\x85 放中间——收尾的 Unicode 空白（含 NEL）会被 .strip() 清洗掉，
            # 中间残留的控制字符才是必须显式拒绝的路径。
            with self.assertRaises(ai_keys.KeyShapeError, msg=repr(bad)):
                ai_keys.base_url_of({"base_url": bad})
        # API 层：verify=true + 控制字符 base_url -> 422（校验层拦截，无出站）。
        r = self.client.put("/api/ai/key", json={
            "provider": "openai", "key": FAKE_KEY,
            "base_url": "https://evil.example/v1\r\nX-Injected: 1", "verify": True},
            headers=self.auth(0))
        self.assertEqual(r.status_code, 422, r.text)
        # 入库前拒绝（verify=false 的保存路径同样 422，文件绝不落盘）。
        r = self.client.put("/api/ai/key", json={
            "provider": "openai", "key": FAKE_KEY,
            "base_url": "https://evil.example/v1\r\nX-Injected: 1"},
            headers=self.auth(0))
        self.assertEqual(r.status_code, 422, r.text)
        self.assertFalse(
            (DATA_ROOT / ".ai_keys" / f"personal_{self.users[0]['user_id']}.json").exists())

    def test_mm_09_invalid_url_maps_to_provider_error_not_500(self):
        """MM-09 纵深钉子：即使控制字符 URL 漏过校验层（如历史存量记录），
        http.client.InvalidURL 也必须映射为 ProviderError（502）而非冒泡 500。
        用回环地址保证零真实出网（InvalidURL 在连接建立前抛出）。"""
        self.guard.stop()
        try:
            with self.assertRaises(ai_suggest_api.ProviderError):
                ai_suggest_api._provider_generate(
                    "s", "u", api_key="sk-url-test-123456",
                    base_url="http://127.0.0.1:1/v1\r\nX-Injected: 1",
                    model="m", timeout=5)
        finally:
            self.guard.start()

    # ---- r7 复审第 1 轮补钉（存量读取路径 / README / 前端文案 / 存量库缺省） ----

    def test_r7_review_get_key_never_500_on_legacy_invalid_base_url(self):
        """复审#1：存量个人钥匙记录（base_url 含 \\r\\n，83ae738 接受保存）在
        GET /api/ai/key 不得从 200 变 500——元数据读取绝不抛 KeyShapeError；
        出站类调用（key/test）映射 422。"""
        vault = DATA_ROOT / ".ai_keys" / f"personal_{self.users[0]['user_id']}.json"
        vault.parent.mkdir(parents=True, exist_ok=True)
        vault.write_text(json.dumps({
            "provider": "openai", "key": FAKE_KEY,
            "base_url": "https://legacy.example/v1\r\nX-Injected: 1",
            "model": "gpt-test", "added_at": "2026-01-01T00:00:00",
            "last_used_at": None}, ensure_ascii=False), encoding="utf-8")
        r = self.client.get("/api/ai/key", headers=self.auth(0))
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertTrue(body["configured"])
        # 回传原始串（本人的配置）便于排查重存，而非 500/静默丢配置。
        self.assertEqual(body["base_url"], "https://legacy.example/v1\r\nX-Injected: 1")
        self.assertNotIn(FAKE_KEY, r.text)
        # 出站类端点对同一存量记录映射 422（重存可修），而非 500。
        r = self.client.post("/api/ai/key/test", headers=self.auth(0))
        self.assertEqual(r.status_code, 422, r.text)

    def test_r7_review_env_base_url_invalid_metadata_ok_and_calls_mapped_422(self):
        """复审#1：REVIEWFLOW_AI_BASE_URL 含控制字符时，env_key() 不得在 GET/生成
        等读取路径抛 KeyShapeError（500）；非法值由出站前的 base_url_of 校验并
        映射 422。"""
        os.environ["REVIEWFLOW_AI_KEY"] = FAKE_KEY
        os.environ["REVIEWFLOW_AI_BASE_URL"] = "https://env.example/v1\r\nX-Injected: 1"
        self.addCleanup(os.environ.pop, "REVIEWFLOW_AI_KEY", None)
        self.addCleanup(os.environ.pop, "REVIEWFLOW_AI_BASE_URL", None)
        r = self.client.get("/api/ai/key", headers=self.auth(0))
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertTrue(body["configured"])
        self.assertEqual(body["source"], "env")
        self.assertNotIn(FAKE_KEY, r.text)
        r = self.client.post("/api/ai/key/test", headers=self.auth(0))
        self.assertEqual(r.status_code, 422, r.text)

    def test_r7_review_legacy_default_full_db_migrated_on_connect(self):
        """复审#4：已含 verified 列且 DEFAULT 'full' 的存量 2.1.4 库，经 _connect
        定向迁移为非 full 缺省；既有行数据（id 与 verified 取值）逐字保留；
        迁移幂等（二次打开不再重建）。"""
        legacy_schema = (
            "CREATE TABLE ai_suggestions("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, "
            "actor_id TEXT NOT NULL, zotero_key TEXT NOT NULL, field_id TEXT NOT NULL, "
            "value TEXT NOT NULL, quote TEXT NOT NULL, pages_json TEXT NOT NULL, "
            "provider TEXT NOT NULL, model TEXT NOT NULL, prompt_version TEXT NOT NULL, "
            "verified TEXT NOT NULL DEFAULT 'full' CHECK(verified IN ('full','partial')), "
            "final_value TEXT, status TEXT NOT NULL "
            "CHECK(status IN ('pending','adopted','ignored','edited')), "
            "created_at TEXT NOT NULL, decided_at TEXT)")
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "legacy214.db"
            with closing(sqlite3.connect(str(db_path))) as conn:
                conn.executescript(legacy_schema + ";"
                                   "INSERT INTO ai_suggestions(task_id,actor_id,zotero_key,"
                                   "field_id,value,quote,pages_json,provider,model,"
                                   "prompt_version,verified,status,created_at) VALUES("
                                   "'t','a','K','sample_size','120','in 6 centres','[2]',"
                                   "'openai','gpt','v1','full','pending','2026-01-01');")
            with closing(ai_suggest_api._connect(db_path)) as conn:
                dflt = conn.execute("SELECT dflt_value FROM pragma_table_info('ai_suggestions') "
                                    "WHERE name='verified'").fetchone()[0]
                self.assertNotEqual((dflt or "").strip("'"), "full",
                                    "存量 DEFAULT 'full' 库必须被迁移为非 full 缺省")
                rows = conn.execute("SELECT id,task_id,field_id,value,verified,status "
                                    "FROM ai_suggestions").fetchall()
            self.assertEqual([tuple(r) for r in rows],
                             [(1, "t", "sample_size", "120", "full", "pending")],
                             "迁移只改缺省，不改写存量行（含 verified 取值）")
            with closing(ai_suggest_api._connect(db_path)) as conn:  # 幂等
                count = conn.execute("SELECT COUNT(*) FROM ai_suggestions").fetchone()[0]
                self.assertEqual(count, 1)

    # ---- r7 复审第 2 轮补钉（urlsplit 裸 ValueError / env model 清洗） ----------

    def test_r2_review_malformed_ipv6_base_url_mapped_422(self):
        """复审#1：畸形 URL（'http://[::1'，urlsplit 抛裸 ValueError 而非
        KeyShapeError）在 PUT /api/ai/key（verify=false/true）必须 422 而非 500。"""
        with self.assertRaises(ai_keys.KeyShapeError):
            ai_keys.base_url_of({"base_url": "http://[::1"})
        for verify in (False, True):
            r = self.client.put("/api/ai/key", json={
                "provider": "openai", "key": FAKE_KEY,
                "base_url": "http://[::1", "verify": verify}, headers=self.auth(0))
            self.assertEqual(r.status_code, 422, r.text)
        # 共享钥匙路径同一校验函数（owner 写）
        r = self.client.put(f"{self.base}/ai-suggest/shared-key", json={
            "provider": "openai", "key": FAKE_KEY, "base_url": "http://[::1"},
            headers=self.auth(0))
        self.assertEqual(r.status_code, 422, r.text)

    def test_r2_review_malformed_base_url_metadata_never_500(self):
        """复审#1：存量记录与环境变量的 base_url 为畸形 URL（裸 ValueError）时，
        GET /api/ai/key 元数据读取仍 200（回传原始串），key/test 映射 422。"""
        vault = DATA_ROOT / ".ai_keys" / f"personal_{self.users[0]['user_id']}.json"
        vault.parent.mkdir(parents=True, exist_ok=True)
        vault.write_text(json.dumps({
            "provider": "openai", "key": FAKE_KEY, "base_url": "http://[::1",
            "model": "gpt-test", "added_at": "2026-01-01T00:00:00",
            "last_used_at": None}, ensure_ascii=False), encoding="utf-8")
        r = self.client.get("/api/ai/key", headers=self.auth(0))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["configured"])
        self.assertEqual(r.json()["base_url"], "http://[::1")
        r = self.client.post("/api/ai/key/test", headers=self.auth(0))
        self.assertEqual(r.status_code, 422, r.text)
        # env 变体：读取 200；出站类调用 422。
        os.environ["REVIEWFLOW_AI_KEY"] = FAKE_KEY
        os.environ["REVIEWFLOW_AI_BASE_URL"] = "http://[::1"
        self.addCleanup(os.environ.pop, "REVIEWFLOW_AI_KEY", None)
        self.addCleanup(os.environ.pop, "REVIEWFLOW_AI_BASE_URL", None)
        r = self.client.get("/api/ai/key", headers=self.auth(0))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["configured"])
        self.assertEqual(r.json()["source"], "env")
        r = self.client.post("/api/ai/key/test", headers=self.auth(0))
        self.assertEqual(r.status_code, 422, r.text)

    def test_r2_review_env_model_overlong_metadata_ok(self):
        """复审#2：REVIEWFLOW_AI_MODEL 超 100 字符时 env_key() 不得抛
        KeyShapeError——GET /api/ai/key 仍 200（透传原始值，出站由 provider 拒）。"""
        os.environ["REVIEWFLOW_AI_KEY"] = FAKE_KEY
        os.environ["REVIEWFLOW_AI_MODEL"] = "m" * 150
        self.addCleanup(os.environ.pop, "REVIEWFLOW_AI_KEY", None)
        self.addCleanup(os.environ.pop, "REVIEWFLOW_AI_MODEL", None)
        r = self.client.get("/api/ai/key", headers=self.auth(0))
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertTrue(body["configured"])
        self.assertEqual(body["source"], "env")
        self.assertEqual(body["model"], "m" * 150)
        self.assertNotIn(FAKE_KEY, r.text)


    def test_r3_pool_path_fulltext_not_sent_to_provider(self):
        """终审 #39 r3：fulltexts 行指向共享平铺池 → 该文件文本绝不进 provider。

        断言两层：① provider 收到的 user 消息不含池内文件的文本标记；
        ② 该 key 仍按「无全文」处理（摘要仍可用，端点不 500）。
        """
        import hashlib as _hashlib

        self.enable_task(key_mode="personal")
        self.put_personal_key()
        from coscreen import fulltext as fulltext_mod
        from coscreen import tasks as tasks_mod

        info = tasks_mod.find_task(self.task, str(DATA_ROOT))
        owner_db = info.dir / (self._slug + ".db")
        marker = "R3-POOL-FULLTEXT-SECRET-MARKER"
        pool = Path(DATA_ROOT) / "pdfs"
        pool.mkdir(parents=True, exist_ok=True)
        victim = pool / ("legacy_r3_" + self._testMethodName + ".pdf")
        # 可解析的真实 PDF：正文页里放不可误认的标记文本
        from pypdf import PdfWriter
        writer = PdfWriter()
        writer.add_blank_page(width=200, height=200)
        writer.add_metadata({"/Title": marker})
        buffer = io.BytesIO()
        writer.write(buffer)
        victim.write_bytes(buffer.getvalue())
        self.addCleanup(lambda: victim.unlink(missing_ok=True))
        with closing(sqlite3.connect(owner_db)) as conn, conn:
            conn.execute(
                "INSERT INTO fulltexts (zotero_key, filename, path, sha256, "
                "n_pages, added_at) VALUES ('A1', 'A1.pdf', ?, ?, 1, '2026-01-01')",
                (str(victim), _hashlib.sha256(buffer.getvalue()).hexdigest()))
        captured = {}

        def spy_generate(system, user, *, api_key, base_url, model, timeout=60.0):
            captured["user"] = user
            return {"content": json.dumps({}), "usage": {}, "model": model}

        read_calls = []

        def spy_pdf_text(path):
            read_calls.append(str(path))
            return ["page text " + marker]

        with mock.patch.object(ai_suggest_api.fulltext_mod, "pdf_text", spy_pdf_text),              mock.patch.object(ai_suggest_api, "_provider_generate", spy_generate):
            response = self.suggestions()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(read_calls, [], "池内文件绝不被读取（包含性校验先于读取）")
        self.assertNotIn(str(victim), captured.get("user", ""),
                         "池内文件路径不得出现在 provider 消息中")
        self.assertNotIn(marker, captured.get("user", ""),
                         "池内文件内容标记不得进入 provider 消息")

    def test_r3_mutation_containment_removed_pin_goes_red(self):
        """破坏性变异：包含性恒 True（防护删掉）→ 池内文本真实进入 provider。"""
        import hashlib as _hashlib

        self.enable_task(key_mode="personal")
        self.put_personal_key()
        from coscreen import tasks as tasks_mod

        info = tasks_mod.find_task(self.task, str(DATA_ROOT))
        owner_db = info.dir / (self._slug + ".db")
        marker = "R3-POOL-MUT-MARKER"
        pool = Path(DATA_ROOT) / "pdfs"
        pool.mkdir(parents=True, exist_ok=True)
        victim = pool / ("legacy_r3mut_" + self._testMethodName + ".pdf")
        from pypdf import PdfWriter
        writer = PdfWriter()
        writer.add_blank_page(width=200, height=200)
        writer.add_metadata({"/Title": marker})
        buffer = io.BytesIO()
        writer.write(buffer)
        victim.write_bytes(buffer.getvalue())
        self.addCleanup(lambda: victim.unlink(missing_ok=True))
        with closing(sqlite3.connect(owner_db)) as conn, conn:
            conn.execute(
                "INSERT INTO fulltexts (zotero_key, filename, path, sha256, "
                "n_pages, added_at) VALUES ('A1', 'A1.pdf', ?, ?, 1, '2026-01-01')",
                (str(victim), _hashlib.sha256(buffer.getvalue()).hexdigest()))
        captured = {}

        def spy_generate(system, user, *, api_key, base_url, model, timeout=60.0):
            captured["user"] = user
            return {"content": json.dumps({}), "usage": {}, "model": model}

        def spy_pdf_text(path):
            return ["page text " + marker]

        with mock.patch.object(ai_suggest_api, "_fulltext_path_allowed",
                               lambda path, db: True),              mock.patch.object(ai_suggest_api.fulltext_mod, "pdf_text", spy_pdf_text),              mock.patch.object(ai_suggest_api, "_provider_generate", spy_generate):
            response = self.suggestions()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn(marker, captured.get("user", ""),
                      "无防护时池内文件文本真实进入 provider（钉必红）")


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

    def test_lang_register_entries_all_iterable(self):
        """B8 v2 回归发现（feat/ai-extract @ 22a8f3a 前端整体失效）：词条数组漏逗号
        会被 JS 解析成 A[B] 成员访问（语法合法、node --check 通过），RFLang.register
        遍历到 undefined 词条抛 TypeError 且被模块末尾吞异常——整个 AI 前端静默失效
        （无设置卡/无建议按钮/零判定请求）。锁死：register 实参必须是数组字面量的
        列表，且中文源串互不重复（重复键会让后注册词条覆盖先注册词条）。"""
        source = (Path(__file__).resolve().parents[2] / "custom_frontend" / "ai_suggest.js").read_text(
            encoding="utf-8")
        match = re.search(r"RFLang\.register\(\[(.*?)\]\);", source, re.S)
        self.assertTrue(match, "ai_suggest.js 未找到 RFLang.register 调用")
        inner = re.sub(r",\s*$", "", match.group(1).rstrip())
        entries = json.loads("[" + inner + "]")   # 漏逗号/多余逗号以外的问题在这里抛
        self.assertTrue(entries, "词条数组不得为空")
        for entry in entries:
            self.assertIsInstance(entry, list, f"词条必须是数组（发现不可迭代项）：{entry!r}")
            self.assertTrue(entry, "词条不得为空数组")
        zh_keys = [entry[0] for entry in entries]
        duplicates = {key for key in zh_keys if zh_keys.count(key) > 1}
        self.assertEqual(len(zh_keys), len(set(zh_keys)),
                         f"中文源串重复（后注册覆盖先注册）：{sorted(duplicates)}")

    def test_mm2_01_frontend_full_badge_and_abstract_source_label(self):
        """MM2-01/MM2-04 前端钉子：verified='full' 也必须有显式角标（'引文核验'——
        明示核验的是引文存在而非值本身），不能像修复前那样对 full 静默不标；
        pages 为空（摘要来源）的建议卡显式标注摘要来源，不再什么都不显示。"""
        source = (Path(__file__).resolve().parents[2] / "custom_frontend" / "ai_suggest.js").read_text(
            encoding="utf-8")
        self.assertIn("item.verified === 'full' ? T('引文核验')", source,
                      "full 必须映射显式角标文案（引文核验），不得对 full 静默")
        self.assertIn("item.verified === 'partial' ? T('部分核验')", source,
                      "partial 角标（B9 R-1）必须保留")
        self.assertIn("T('来源：摘要（无页码）')", source,
                      "pages 为空的建议必须标注摘要来源（MM2-04）")

    def test_r7_review_partial_quote_warning_covers_short_quotes(self):
        """复审#3：partial_quote 警告文案必须覆盖 MM2-01 新门槛——<8 字符的逐字
        命中同样发 partial_quote，只写『按可核验前缀放行』会与实际不符。"""
        source = (Path(__file__).resolve().parents[2] / "custom_frontend" / "ai_suggest.js").read_text(
            encoding="utf-8")
        self.assertIn("有建议因引文过短或按可核验前缀放行（部分核验）", source,
                      "警告文案必须同时覆盖短引文与前缀两条 partial 路径")
        self.assertNotIn("有建议按可核验前缀放行（部分核验）", source,
                         "旧文案未覆盖短引文路径，不得残留")

    def test_r7_review_readme_quote_page_claims_aligned(self):
        """复审#2：README 中英特性表不得再宣称『每条建议附页码』——摘要来源
        pages=[]，须与 MM2-04 后的 Methods/docs 口径一致。"""
        readme = (Path(__file__).resolve().parents[2] / "README.md").read_text(encoding="utf-8")
        self.assertNotIn("建议附原文引文与页码可溯源", readme)
        self.assertNotIn("every suggestion carries a source quote and page numbers", readme)
        self.assertIn("正文来源附页码", readme)
        self.assertIn("abstract-sourced quotes are labelled as such", readme)


if __name__ == "__main__":
    unittest.main()