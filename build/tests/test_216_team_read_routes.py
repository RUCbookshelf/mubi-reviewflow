"""P23 team read routes tests (C12 author side; 2.1.6 team contracts P23).

Five P23-named tests over the **real** app routes: real HTTP via TestClient,
real team building (移植 test_216_member_write_routes.py=1993c128 的真实建队
配方：业务角色单选/P04 ledger/F1 facts/F2 registry+真 source token/协议行
create_task hook——零 mock), real member_vote seeding through the P15/P16
mutate routes. The product under test is the box candidate
custom_backend/review_workflow_api.py (P15 基线 62e203fd + P23 keyed 读服务),
loaded via sys.modules pre-registration (P03 candidate4 装载协议) before
custom_backend.main imports it.

工单锚（逐字）：assert set(payload['items_by_key']) | set(payload['item_errors'])
== set(requested_keys)。

Product imports are deferred to setUpClass (loadable under python -I -B).
"""
from __future__ import annotations

import importlib
import json
import sqlite3
import sys
import unittest
import uuid
from contextlib import closing
from pathlib import Path

K_DONE = "P23DONE"      # 双必需筛选人 screen+fulltext 双 include → 编码开闸
K_OPEN = "P23OPEN"      # 双必需筛选人 screen include（stage1 完成）、fulltext 开放
K_VOTE = "P23VOTE"      # 仅 p23screener 投 screen exclude（带私密理由）→ awaiting
K_SOLO = "P23SOLO1"     # solo 任务键（test_solo_manual_context_unchanged）
PEER_SECRET = "P23PEER-SECRET-REASON-9f3a"  # 双盲票面泄露探针（绝不可回传）
_OUT_OF_DOMAIN = "P23-NOT-IN-DOMAIN-XYZ"

_UPSERT_LIBRARY = (
    "INSERT INTO team_vote_revision_meta (singleton, format_version,"
    " library_id, seeded_stage1, seeded_stage2, advances)"
    " VALUES (1, 2, ?, 0, 0, 0) ON CONFLICT(singleton) DO UPDATE"
    " SET library_id=excluded.library_id")
_INSERT_ARTICLE = (
    "INSERT INTO articles (zotero_key, title, is_duplicate_of, import_order)"
    " VALUES (?, ?, NULL, ?)")
_LATCH = "UPDATE workflow_state SET team_latched=1 WHERE singleton=1"


def _close_quietly(conn):
    try:
        conn.close()
    except Exception:
        pass


class TeamReadRoutesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # 装载协议（P03 candidate4）：候选 review_workflow_api 先注册进
        # sys.modules，再 import custom_backend.main——主模块 from-import 绑定
        # 到候选字节（主仓其余消费面按集成版解析）。
        import custom_backend  # noqa: F401  (package init first)
        cand = Path(__file__).resolve().parents[2] / "candidate" / \
            "custom_backend" / "review_workflow_api.py"
        cls._cand_path = cand
        spec = importlib.util.spec_from_file_location(
            "custom_backend.review_workflow_api", cand)
        module = importlib.util.module_from_spec(spec)
        sys.modules["custom_backend.review_workflow_api"] = module
        spec.loader.exec_module(module)
        cls.rwf = module

        from fastapi.testclient import TestClient
        from custom_backend import auth as auth_mod
        from custom_backend import main as main_mod

        cls.client = TestClient(main_mod.app)
        cls.client.__enter__()
        cls.main_mod = main_mod
        cls.users = {}
        for name in ("p23owner", "p23screener", "p23coder"):
            user = auth_mod.create_user(name, "P23-Team-Reads-2026")
            cls.users[name] = {
                "user_id": user["id"], "username": user["username"],
                "token": auth_mod.create_access_token(
                    user["id"], user["username"])}
        cls._team_task()
        cls._solo_task()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    @classmethod
    def _auth(cls, name):
        return {"Authorization": "Bearer " + cls.users[name]["token"]}

    # ------------------------------------------------------------------
    # 真实建队（移植 P16=1993c128 配方，零 mock）
    # ------------------------------------------------------------------
    @classmethod
    def _seed_votes(cls, key: str, stage: str, names, *, reason: str = ""):
        path = {"/decisions", "/stage2/decisions"}
        route = "/decisions" if stage == "screen" else "/stage2/decisions"
        assert route in path
        for name in names:
            seeded = cls.client.post(
                f"/api/tasks/{cls.task_id}{route}",
                json={"zotero_key": key, "decision": "include",
                      "reason": reason, "notes": "", "tags": None},
                headers=cls._auth(name))
            assert seeded.status_code == 200, seeded.text

    @classmethod
    def _team_task(cls):
        collab = importlib.import_module("coscreen.task_collaboration")
        cdb = importlib.import_module("coscreen.db")
        wf = importlib.import_module("coscreen.review_workflow")
        tasks = importlib.import_module("coscreen.tasks")
        tf = importlib.import_module("coscreen.team_terminal_facts")
        tr = importlib.import_module("coscreen.team_terminal_registry")
        receipts = importlib.import_module("coscreen.team_flow_receipts")
        tsl = importlib.import_module("custom_backend.team_source_lifecycle")
        coding_evidence = importlib.import_module("coscreen.coding_evidence")

        owner = cls.users["p23owner"]
        owner_uid = owner["user_id"]
        cls.task_id = cls.client.post(
            "/api/tasks", json={"name": "p23-reads-" + uuid.uuid4().hex[:8]},
            headers=cls._auth("p23owner")).json()["task_id"]
        cls.info = tasks.find_task(cls.task_id, cls.main_mod.DATA_DIR)
        task_dir = cls.info.dir
        cls.collab_path = collab.shared_store_path(task_dir)
        if not cls.collab_path.is_file():
            collab.initialize_task_store(
                task_dir, task_id=cls.task_id, owner_user_id=owner_uid,
                owner_username=owner["username"])
        collab.set_business_role(
            cls.collab_path, actor_user_id=owner_uid, user_id=owner_uid,
            business_role="reconciler", task_id=cls.task_id)
        slug = tasks.slugify_screener
        cls.user_db = {name: task_dir / f"{slug(u['username'])}.db"
                       for name, u in cls.users.items()}
        for name in ("p23screener", "p23coder"):
            uid = cls.users[name]["user_id"]
            collab.grant_role(cls.collab_path, actor_user_id=owner_uid,
                              user_id=uid, role="coder", task_id=cls.task_id)
            collab.set_business_role(
                cls.collab_path, actor_user_id=owner_uid, user_id=uid,
                business_role="coder", task_id=cls.task_id)
        articles = [(k, f"P23 {k}", i) for i, k in enumerate(
            (K_DONE, K_OPEN, K_VOTE))]
        for name, path in cls.user_db.items():
            cdb.init_db(path)
            wf.migrate(path)
            tf.prepare_team_terminal_facts(path)
            receipts.prepare_team_flow_receipts(path)
            conn = sqlite3.connect(str(path), timeout=15)
            try:
                conn.execute("PRAGMA foreign_keys=ON")
                if name != "p23owner":
                    conn.execute(_UPSERT_LIBRARY,
                                 ("%032x" % cls.users[name]["user_id"],))
                conn.executemany(_INSERT_ARTICLE, articles)
                if name == "p23owner":
                    conn.execute(_LATCH)
                conn.commit()
            finally:
                conn.close()
            coding_evidence.ensure_fields(path)
        coding = importlib.import_module("coscreen.coding")
        cls.dim_id = coding.add_dimension(
            cls.user_db["p23coder"], "P23 证据维度", "text", [])
        tr.prepare_team_terminal_registry(cls.collab_path)
        with closing(sqlite3.connect(str(cls.collab_path))) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT task_instance_id FROM main.team_task_protocol"
                " LIMIT 1").fetchone()
        assert row is not None, "create_task must register the protocol row"
        cls.instance_id = row["task_instance_id"]
        tf.prepare_team_terminal_facts(cls.collab_path)
        for name in ("p23screener", "p23coder"):
            uid = cls.users[name]["user_id"]
            library = "%032x" % uid
            token = tsl.make_source_identity(
                task_dir=task_dir, source_path=cls.user_db[name],
                source_user_id=uid, source_library_id=library)
            writer = sqlite3.connect(str(cls.collab_path), timeout=15)
            writer.row_factory = sqlite3.Row
            try:
                writer.execute("BEGIN IMMEDIATE")
                tr.register_source_on(
                    writer, task_instance_id=cls.instance_id,
                    source_user_id=uid, source_library_id=library,
                    source_path_identity=token,
                    registration_id=f"reg-p23-{name}")
                writer.commit()
            finally:
                _close_quietly(writer)
        # 播种：K_DONE 双必需筛选人 screen include（stage1 完成、单一 seal
        # 落第二票人 p23coder 库；刻意不播 fulltext——双成员 fulltext 播种
        # 会让第一票人在自己库补 stage1 current_observation seal，与 coder
        # 库的 last_vote seal 构成跨 holder 互斥 seal，P09 计划按
        # conflicting_seals fail-closed（合同行为，非本包缺陷））；
        # K_OPEN 零票（screen 开放）；K_VOTE 仅 p23screener 一票 screen
        # exclude（带私密理由）。
        cls._seed_votes(K_DONE, "screen", ("p23screener", "p23coder"))
        seeded = cls.client.post(
            f"/api/tasks/{cls.task_id}/decisions",
            json={"zotero_key": K_VOTE, "decision": "exclude",
                  "reason": PEER_SECRET, "notes": "", "tags": None},
            headers=cls._auth("p23screener"))
        assert seeded.status_code == 200, seeded.text

    @classmethod
    def _solo_task(cls):
        tasks = importlib.import_module("coscreen.tasks")
        wf = importlib.import_module("coscreen.review_workflow")
        cls.solo_task_id = cls.client.post(
            "/api/tasks", json={"name": "p23-solo-" + uuid.uuid4().hex[:8]},
            headers=cls._auth("p23owner")).json()["task_id"]
        info = tasks.find_task(cls.solo_task_id, cls.main_mod.DATA_DIR)
        db = info.dir / (
            tasks.slugify_screener(cls.users["p23owner"]["username"]) + ".db")
        wf.migrate(db)
        with closing(sqlite3.connect(str(db))) as conn:
            conn.execute(
                "INSERT INTO articles (zotero_key, title, is_duplicate_of,"
                " import_order) VALUES (?, ?, NULL, 0)", (K_SOLO, "P23 solo"))
            conn.commit()
        cls.solo_db = db

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _keyed(self, task_id, stage, keys, name="p23screener"):
        resp = self.client.post(
            f"/api/tasks/{task_id}/work-items/{stage}/keyed",
            json={"zotero_keys": list(keys)}, headers=self._auth(name))
        self.assertEqual(resp.status_code, 200, resp.text)
        return resp.json()

    # ------------------------------------------------------------------
    # 1. queue/detail/save reason agree（保存门同构）
    # ------------------------------------------------------------------
    def test_queue_detail_save_reason_agree(self):
        # 队列/详情读到的 reason 与真实保存 POST 的 409 code 逐字一致
        #（工单「eligible/writable_reason 同保存门」锚），开放键则两侧都放行。
        scr = self._keyed(self.task_id, "screen", (K_DONE, K_OPEN))
        self.assertEqual(
            set(scr["items_by_key"]) | set(scr["item_errors"]),
            {K_DONE, K_OPEN})
        done = scr["items_by_key"][K_DONE]
        open_item = scr["items_by_key"][K_OPEN]
        # K_DONE stage1 已完成（双票一致）→ 详情 reason=stage_completed_read_only。
        self.assertFalse(done["writable"], done)
        self.assertEqual(done["reason"], "stage_completed_read_only")
        self.assertTrue(done["completed"])
        # 真实保存门同码拒绝（补第三票 screen）。
        save = self.client.post(
            f"/api/tasks/{self.task_id}/decisions",
            json={"zotero_key": K_DONE, "decision": "include",
                  "reason": "", "notes": "", "tags": None},
            headers=self._auth("p23screener"))
        self.assertEqual(save.status_code, 409, save.text)
        save_body = save.json()
        self.assertEqual(
            save_body.get("code")
            or save_body.get("detail", {}).get("code"),
            "stage_completed_read_only", save.text)
        # K_OPEN 无人投 → 详情可写 reason=None；真实保存 200（同一 screener
        # 的 screen 票——不完成 stage1，后续 coding 键仍开放）。
        self.assertTrue(open_item["writable"], open_item)
        self.assertIsNone(open_item["reason"])
        saved = self.client.post(
            f"/api/tasks/{self.task_id}/decisions",
            json={"zotero_key": K_OPEN, "decision": "include",
                  "reason": "", "notes": "", "tags": None},
            headers=self._auth("p23screener"))
        self.assertEqual(saved.status_code, 200, saved.text)
        # coding 侧：K_DONE 的 stage2 无人投 → 链未清除（upstream 令牌）；
        # 真实编码保存 409 且 code == 详情 reason。
        code_payload = self._keyed(self.task_id, "coding", (K_DONE,),
                                   name="p23coder")
        code_done = code_payload["items_by_key"][K_DONE]
        self.assertFalse(code_done["writable"], code_done)
        self.assertTrue(str(code_done["reason"]).startswith(
            "team_item_upstream_stage2"), code_done["reason"])
        blocked = self.client.post(
            f"/api/tasks/{self.task_id}/coding/values-batch",
            json={"zotero_key": K_DONE,
                  "values": {str(self.dim_id): "x"}, "notes": None,
                  "evidence": None, "draft": False},
            headers=self._auth("p23coder"))
        self.assertEqual(blocked.status_code, 409, blocked.text)
        blocked_body = blocked.json()
        self.assertEqual(
            blocked_body.get("code")
            or blocked_body.get("detail", {}).get("code"),
            code_done["reason"], blocked.text)

    # ------------------------------------------------------------------
    # 2. A 可用 / B 域外 → HTTP 200 + items_by_key{A} + item_errors{B}
    # ------------------------------------------------------------------
    def test_a_success_b_issue_http_200(self):
        requested = [K_DONE, _OUT_OF_DOMAIN]
        resp = self.client.post(
            f"/api/tasks/{self.task_id}/work-items/screen/keyed",
            json={"zotero_keys": requested}, headers=self._auth("p23screener"))
        self.assertEqual(resp.status_code, 200, resp.text)
        payload = resp.json()
        # 工单断言锚（逐字语义）：两键集互斥且并集 == 请求键集。
        self.assertEqual(
            set(payload["items_by_key"]) | set(payload["item_errors"]),
            set(requested))
        self.assertEqual(set(payload["items_by_key"]), {K_DONE})
        self.assertEqual(set(payload["item_errors"]), {_OUT_OF_DOMAIN})
        issue = payload["item_errors"][_OUT_OF_DOMAIN]
        self.assertEqual(issue["reason"], "key_not_in_domain")
        self.assertFalse(issue["retryable"])
        # 域外键不连坐：K_DONE 的事实完整呈现。
        self.assertIn(K_DONE, payload["items_by_key"])

    # ------------------------------------------------------------------
    # 3. counts unknown not zero（known_total 可 None，不把未知当零）
    # ------------------------------------------------------------------
    def test_counts_unknown_not_zero(self):
        mixed = self._keyed(self.task_id, "screen", (K_DONE, _OUT_OF_DOMAIN))
        counts = mixed["counts"]
        self.assertEqual(counts["unknown"], 1)   # 未知不被折为零
        self.assertIsNone(counts["known_total"])  # 键域有未知 → 总数不可断言
        self.assertEqual(counts["requested"], 2)
        self.assertEqual(
            counts["writable"] + counts["blocked"],
            len(mixed["items_by_key"]))
        self.assertEqual(counts["completed"], 1)  # K_DONE stage1 完成
        # 全可判定请求：known_total 为具体整数（不为 0 伪报）。
        clean = self._keyed(self.task_id, "screen", (K_DONE, K_OPEN, K_VOTE))
        cc = clean["counts"]
        self.assertEqual(cc["unknown"], 0)
        self.assertIsInstance(cc["known_total"], int)
        self.assertEqual(cc["known_total"], 3)
        self.assertEqual(
            cc["writable"] + cc["blocked"] + cc["unknown"], cc["requested"])

    # ------------------------------------------------------------------
    # 3b. D-1 钉：degraded 团队任务的队列/详情 reason 与真实保存 409 同令牌
    #     （task_read_only）——真实降级态（task.json 暂移，find_task 降级
    #     TaskInfo），finally 恢复；非 mock。
    # ------------------------------------------------------------------
    def test_degraded_team_reason_matches_save_gate(self):
        # 真实降级态＝task.json **损坏**（_read_task_json → {} →
        # _degraded_info；缺失文件是 404 而非 degraded）。原字节备份、
        # 写坏、finally 逐字节恢复——非 mock。
        task_json = self.info.dir / "task.json"
        original = task_json.read_bytes()
        self.assertTrue(original)
        try:
            task_json.write_bytes(b'{"p23-corrupt": not-json')
            payload = self._keyed(self.task_id, "screen", (K_OPEN,))
            item = payload["items_by_key"][K_OPEN]
            self.assertFalse(item["writable"], item)
            self.assertEqual(item["reason"], "task_read_only")
            self.assertFalse(item["completed"])
            # 同保存门：degraded 下真实 member_vote 保存 409 同令牌。
            save = self.client.post(
                f"/api/tasks/{self.task_id}/decisions",
                json={"zotero_key": K_OPEN, "decision": "include",
                      "reason": "", "notes": "", "tags": None},
                headers=self._auth("p23screener"))
            self.assertEqual(save.status_code, 409, save.text)
            body = save.json()
            self.assertEqual(
                body.get("code") or body.get("detail", {}).get("code"),
                "task_read_only", save.text)
        finally:
            task_json.write_bytes(original)
        # 恢复后健康同前（降级不留痕）。
        healthy = self._keyed(self.task_id, "screen", (K_OPEN,))
        self.assertTrue(healthy["items_by_key"][K_OPEN]["writable"])

    # ------------------------------------------------------------------
    # 4. completion/issue 字段无私有 source 泄露（双盲票面不进详情）
    # ------------------------------------------------------------------
    def test_no_blind_peer_votes_in_completion_details(self):
        # actor=p23coder（未投 K_VOTE）；peer=p23screener 已投 exclude 且
        # 票面带私密理由——详情/完成字段只给结论与名单诊断，票面永不回传。
        payload = self._keyed(self.task_id, "screen", (K_VOTE,),
                              name="p23coder")
        self.assertNotIn(PEER_SECRET, json.dumps(payload, ensure_ascii=False))
        item = payload["items_by_key"][K_VOTE]
        # actor（p23coder）尚未投 K_VOTE → 可写（可投出自己的票）；但 peer
        # 的票面/私密理由绝不随详情回传。
        self.assertTrue(item["writable"], item)
        self.assertIsNone(item["reason"])
        # payload 无 votes/opinions/ballots/私有 source 绑定结构。
        dumped = json.dumps(payload, ensure_ascii=False)
        for banned in ('"votes"', '"opinions"', '"ballots"', '"source_path"',
                       '"registration_id"', '"library_id"'):
            self.assertNotIn(banned, dumped)
        # 完成投影字段白名单（CompletionProjection 六字段，无 source 绑定）。
        for entry in payload["items_by_key"].values():
            self.assertIn("completion", entry)
            if entry["completion"] is not None:
                self.assertLessEqual(
                    set(entry["completion"]),
                    {"seal_id", "kind", "trigger", "sealed_at",
                     "final_decision", "persisted"})
        # 状态摘要允许 pending 名单（完成度诊断，P22 同形）但不含票面。
        status = payload["items_by_key"][K_VOTE]["stage_status"]
        self.assertIsNotNone(status)
        self.assertEqual(status["status"], "awaiting_members")
        self.assertLessEqual(
            set(status),
            {"status", "decided_by", "applied_arbitration", "pending_members",
             "pending_reasons", "stale_arbitrations"})
        # 本人票摘要只含 actor 自己的 decision（此处未投 → saved=None）。
        self.assertIsNone(payload["items_by_key"][K_VOTE]["saved"])

    # ------------------------------------------------------------------
    # 5. solo 手工路径零变化（scope 恒 None、行为同旧）
    # ------------------------------------------------------------------
    def test_solo_manual_context_unchanged(self):
        runtime = self.main_mod.app.state.review_workflow
        user = self.users["p23owner"]
        ctx = runtime.context(self.solo_task_id, user)
        # 旧位置参数构造保持；P23 新字段对 solo 恒 None（不串请求）。
        self.assertIsNone(ctx.write_inputs)
        self.assertIsNone(ctx.team_read_scope)
        self.assertIn(ctx.task_kind, ("legacy", "personal"))
        payload = self._keyed(self.solo_task_id, "screen",
                              (K_SOLO, "P23SOLO-MISSING"), name="p23owner")
        self.assertEqual(
            set(payload["items_by_key"]) | set(payload["item_errors"]),
            {K_SOLO, "P23SOLO-MISSING"})
        item = payload["items_by_key"][K_SOLO]
        self.assertTrue(item["writable"], item)
        self.assertIsNone(item["reason"])
        self.assertIsNone(item["saved"])
        self.assertEqual(
            payload["item_errors"]["P23SOLO-MISSING"]["reason"],
            "article_not_found")
        # solo 同一计数语义：存在不可判定键 → unknown=1、known_total=None
        #（不把未知当零）；writable+blocked 覆盖全部已判定键。
        self.assertEqual(payload["counts"]["unknown"], 1)
        self.assertIsNone(payload["counts"]["known_total"])
        self.assertEqual(payload["counts"]["writable"]
                         + payload["counts"]["blocked"], 1)
        # keyed 服务不得给 solo 请求留下任何 scope 绑定（新 ctx 每请求新建，
        # 但同一 runtime 的下一次 context() 读到的仍是无 scope 世界）。
        ctx2 = runtime.context(self.solo_task_id, user)
        self.assertIsNone(ctx2.write_inputs)
        self.assertIsNone(ctx2.team_read_scope)
        # solo 可写判定与真实保存一致（include 保存 200）。
        saved = self.client.post(
            f"/api/tasks/{self.solo_task_id}/decisions",
            json={"zotero_key": K_SOLO, "decision": "include",
                  "reason": "", "notes": "", "tags": None},
            headers=self._auth("p23owner"))
        self.assertEqual(saved.status_code, 200, saved.text)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
