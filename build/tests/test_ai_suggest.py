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
        self.assertEqual(body["masked"], "sk-***3456")
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
        self.assertEqual(ai_keys.mask_key("sk-medium-key1"), "sk-***ey1")  # 9~16 位只露尾 3
        self.assertEqual(ai_keys.mask_key("sk-test-abcdef123456"), "sk-***3456")  # 长钥匙露尾 4

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
        self.assertEqual(suggestion["pages"], [1])  # quote 定位到摘要（无页码文本时为空，这里模型给了 [1]）
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


if __name__ == "__main__":
    unittest.main()
