"""P24 entrypoint coverage tests (C12 author side; 2.1.6 team contracts P24).

Four P24-named tests + the bundled P13 end-to-end seam completion, over the
**real** app: real HTTP via TestClient, real team building (P16 fixture
recipe from ``test_216_member_write_routes.py``: business-role single choice /
P04 ledger libraries / F1 facts / F2 registry + real source tokens — zero
mock, zero window patch), real P10 window / P13 commit protocol, real
business writers.  The only wrapping is **passthrough recorders** (the real
function still runs and merely gets its calls ledgered) around
``WorkflowRuntime.mutate`` / ``WorkflowRuntime.write_guard`` /
``custom_backend.main.BUS.publish`` — an observation account (调用账), never
a stub, so a comment-only "wiring" cannot fake green: the route must really
call through, really commit a receipt, and really publish after commit.

Product imports are deferred to ``setUpClass`` (loadable under ``python -I -B``).
"""
from __future__ import annotations

import importlib
import os
import sqlite3
import unittest
import uuid
from contextlib import closing
from pathlib import Path
from unittest import mock

ITEM = "KEY1"      # seeded complete through both stages by the fixture
ITEM2 = "KEY2"     # matrix/bypass probes (screen + annotations)
ITEM3 = "KEY3"     # SSE probe key (kept unvoted until that test)
ITEM4 = "KEY4"     # P13 end-to-end probe key
_OWNER_LIBRARY = "3c" * 16
_UPSERT_LIBRARY = (
    "INSERT INTO team_vote_revision_meta (singleton, format_version,"
    " library_id, seeded_stage1, seeded_stage2, advances)"
    " VALUES (1, 2, ?, 0, 0, 0) ON CONFLICT(singleton) DO UPDATE SET"
    " library_id=excluded.library_id")
_INSERT_ARTICLE = (
    "INSERT INTO articles (zotero_key, title, is_duplicate_of, import_order)"
    " VALUES (?, ?, NULL, ?)")
_LATCH = "UPDATE workflow_state SET team_latched=1 WHERE singleton=1"


def _borrowed_writer(path):
    conn = sqlite3.connect(str(path), timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("BEGIN IMMEDIATE")
    return conn


def _close_quietly(conn):
    try:
        conn.close()
    except Exception:
        pass


class EntrypointCoverageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        from custom_backend import auth as auth_mod
        from custom_backend import main as main_mod

        cls.client = TestClient(main_mod.app)
        cls.client.__enter__()
        cls.main_mod = main_mod
        cls.users = {}
        for name in ("p24owner", "p24screener", "p24coder"):
            user = auth_mod.create_user(name, "P24-Entry-Close-2026")
            cls.users[name] = {
                "user_id": user["id"], "username": user["username"],
                "token": auth_mod.create_access_token(user["id"], user["username"])}
        cls._team_task()
        cls._start_ledgers()

    @classmethod
    def tearDownClass(cls):
        for patcher in getattr(cls, "_ledger_patchers", ()):
            try:
                patcher.stop()
            except Exception:
                pass
        cls.client.__exit__(None, None, None)

    @classmethod
    def _auth(cls, name):
        return {"Authorization": "Bearer " + cls.users[name]["token"]}

    # ------------------------------------------------------------------
    # 真实建队（P16 test_216_member_write_routes 配方整体移植，零 mock）
    # ------------------------------------------------------------------
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

        owner = cls.users["p24owner"]
        owner_uid = owner["user_id"]
        cls.task_id = cls.client.post(
            "/api/tasks", json={"name": "p24-entry-" + uuid.uuid4().hex[:8]},
            headers=cls._auth("p24owner")).json()["task_id"]
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
        for name in ("p24screener", "p24coder"):
            uid = cls.users[name]["user_id"]
            collab.grant_role(cls.collab_path, actor_user_id=owner_uid,
                              user_id=uid, role="coder", task_id=cls.task_id)
            collab.set_business_role(
                cls.collab_path, actor_user_id=owner_uid, user_id=uid,
                business_role="coder", task_id=cls.task_id)
        articles = [(k, f"P24 {k}", i)
                    for i, k in enumerate((ITEM, ITEM2, ITEM3, ITEM4))]
        for name, path in cls.user_db.items():
            cdb.init_db(path)
            wf.migrate(path)
            tf.prepare_team_terminal_facts(path)
            receipts.prepare_team_flow_receipts(path)
            conn = sqlite3.connect(str(path), timeout=15)
            try:
                conn.execute("PRAGMA foreign_keys=ON")
                uid = cls.users[name]["user_id"]
                conn.execute(_UPSERT_LIBRARY, ("%032x" % uid,))
                conn.executemany(_INSERT_ARTICLE, articles)
                if name == "p24owner":
                    conn.execute(_LATCH)
                conn.commit()
            finally:
                conn.close()
            coding_evidence.ensure_fields(path)
        coding = importlib.import_module("coscreen.coding")
        cls.dim_id = None
        for name in ("p24screener", "p24coder"):
            dim_id = coding.add_dimension(
                cls.user_db[name], "P24 证据维度", "text", [])
            if cls.dim_id is None:
                cls.dim_id = dim_id
        tr.prepare_team_terminal_registry(cls.collab_path)
        writer = _borrowed_writer(cls.collab_path)
        cls.instance_id = "p24-inst-" + uuid.uuid4().hex[:12]
        try:
            bound = writer.execute(
                "SELECT task_instance_id FROM team_task_protocol"
                " WHERE task_id=?", (cls.task_id,)).fetchone()
            if bound is not None:
                # 当前主仓（P17-P23 落地版）协作库初始化即绑定任务实例：
                # 复用真实绑定，绝不二次注册冲突。
                cls.instance_id = bound["task_instance_id"]
            else:
                tr.register_protocol_on(
                    writer, task_id=cls.task_id, task_instance_id=cls.instance_id,
                    owner_user_id=owner_uid, owner_library_id=_OWNER_LIBRARY,
                    creation_path="trusted_new_task")
            writer.commit()
        finally:
            _close_quietly(writer)
        tf.prepare_team_terminal_facts(cls.collab_path)
        for name in ("p24screener", "p24coder"):
            uid = cls.users[name]["user_id"]
            library = "%032x" % uid
            token = tsl.make_source_identity(
                task_dir=task_dir, source_path=cls.user_db[name],
                source_user_id=uid, source_library_id=library)
            writer = _borrowed_writer(cls.collab_path)
            try:
                tr.register_source_on(
                    writer, task_instance_id=cls.instance_id, source_user_id=uid,
                    source_library_id=library, source_path_identity=token,
                    registration_id=f"reg-p24-{name}")
                writer.commit()
            finally:
                _close_quietly(writer)
        # P24 roster discriminator：三源登记里加一个**无协作授权**的历史
        # 筛选员——历史名单（screeners）保留、窄活动投影（active_screeners）
        # 不包含（活动名单窄投影 vs 历史身份不全局删）。
        tasks.register_screener(cls.task_id, "p24ghost", cls.main_mod.DATA_DIR)
        # stage1+stage2 上游完成（ITEM）——编码链开闸；ITEM2/3/4 保持可写。
        for name in ("p24screener", "p24coder"):
            seeded = cls.client.post(
                f"/api/tasks/{cls.task_id}/decisions",
                json={"zotero_key": ITEM, "decision": "include",
                      "reason": "", "notes": "", "tags": None},
                headers=cls._auth(name))
            assert seeded.status_code == 200, seeded.text
        for name in ("p24screener", "p24coder"):
            seeded = cls.client.post(
                f"/api/tasks/{cls.task_id}/stage2/decisions",
                json={"zotero_key": ITEM, "decision": "include",
                      "reason": "", "notes": "", "tags": None},
                headers=cls._auth(name))
            assert seeded.status_code == 200, seeded.text

    # ------------------------------------------------------------------
    # passthrough 调用账（真实函数照跑；只记账）
    # ------------------------------------------------------------------
    @classmethod
    def _start_ledgers(cls):
        from custom_backend.events import BUS

        runtime = cls.main_mod.app.state.review_workflow
        cls.mutate_calls: list[dict] = []
        cls.guard_calls: list[tuple] = []
        cls.published: list[dict] = []
        real_mutate = type(runtime).mutate
        real_guard = type(runtime).write_guard

        def mutate_spy(self, task_id, user, **kwargs):
            cls.mutate_calls.append(
                {"task_id": task_id,
                 "operation": getattr(kwargs.get("operation"), "name", None),
                 "stage": getattr(kwargs.get("operation"), "stage", None),
                 "keys": list(kwargs.get("item_keys") or ())})
            return real_mutate(self, task_id, user, **kwargs)

        def guard_spy(self, task_id, user, stage, key=None,
                      expected_version=None, **kwargs):
            cls.guard_calls.append((task_id, stage, key))
            return real_guard(self, task_id, user, stage, key,
                              expected_version, **kwargs)

        real_publish = BUS.publish

        def publish_spy(user_id, event):
            # 发布时刻的持久化回执计数（postcommit 排序的观察锚）。
            try:
                count = cls._receipt_count("p24screener")
            except Exception:
                count = -1
            cls.published.append({"user_id": user_id, "event": dict(event),
                                  "receipts_at_publish": count})
            return real_publish(user_id, event)

        # BUS 在 main.py 是 from-import 的实例绑定：patch 实例的 publish。
        cls._ledger_patchers = [
            mock.patch.object(type(runtime), "mutate", mutate_spy),
            mock.patch.object(type(runtime), "write_guard", guard_spy),
            mock.patch.object(cls.main_mod.BUS, "publish", publish_spy),
        ]
        for patcher in cls._ledger_patchers:
            patcher.start()

    def _row(self, name: str, sql: str, args=()):
        with closing(sqlite3.connect(self.user_db[name])) as conn:
            return conn.execute(sql, args).fetchone()

    @classmethod
    def _receipt_count(cls, name: str) -> int:
        with closing(sqlite3.connect(cls.user_db[name])) as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM team_flow_receipts").fetchone()[0]

    # ------------------------------------------------------------------
    # 1. 真实注册 route 清单×分类矩阵（行为+调用账，非源码子串）
    # ------------------------------------------------------------------
    def test_all_real_mutation_routes_classified(self):
        app = self.main_mod.app
        mutating = set()
        for route in app.routes:
            methods = getattr(route, "methods", None) or set()
            for method in methods - {"GET", "HEAD", "OPTIONS"}:
                mutating.add((method, getattr(route, "path", "?")))
        # 分类器：真实注册的每个 mutation route 必须落进且只落进一族
        # （TEAM_PROTOCOL / MANAGEMENT / SOLO_CONFIG / LEGACY_NON_TEAM /
        # READ_ONLY_SHAPE）——新注册未分类 mutation route 立即失败。
        def classify(method: str, path: str) -> str:
            team_business = (
                ("/decisions", path.endswith("/decisions")),
                ("/coding/values", path.endswith("/coding/values")
                 or path.endswith("/coding/values-batch")),
                ("/highlights", "/highlights" in path),
                ("/appraisal/{article_key}", path.endswith("/appraisal/{article_key}")),
                ("/coding/records", path.endswith("/coding/records")),
                ("/coding/consensus", path.endswith("/coding/consensus")),
                ("/coding/freezes", path.endswith("/coding/freezes")),
            )
            if any(hit and path.startswith("/api/tasks/{task_id}") for _, hit in team_business):
                return "TEAM_PROTOCOL"
            if "/collaboration/" in path:
                return "MANAGEMENT"          # P18 五管理（含 roster 通知义务）
            if path.startswith("/api/tasks/{task_id}") and any(
                    path.endswith(suffix) for suffix in (
                        "/settings", "/sources", "/reasons", "/coding/scheme",
                        "/coding/dimensions")):
                return "SOLO_CONFIG"         # 非筛选配置：write_guard solo 口径
            if "/binary-reset" in path or "/merge" in path or "/compute" in path:
                return "READ_ONLY_SHAPE"     # binary_reset 仅 GET；merge/compute 无业务写
            if path.startswith("/api/tasks/{task_id}"):
                return "LEGACY_NON_TEAM"     # 导入/undo/追踪等既有族（team 侧各有门）
            return "GLOBAL_OR_TASKS"         # /api/tasks、账号等任务域外写
        matrix = {}
        for method, path in sorted(mutating):
            family = classify(method, path)
            self.assertTrue(family, (method, path))
            matrix[(method, path)] = family
        # binary_reset 只允许 GET 形状：mutation 清单里不得出现它的写方法。
        self.assertFalse(any("binary-reset" in p and m != "GET"
                             for m, p in mutating))
        # 矩阵核心行确实存在（防清单被掏空成空集假绿）。
        for required in (
                ("POST", "/api/tasks/{task_id}/decisions"),
                ("POST", "/api/tasks/{task_id}/stage2/decisions"),
                ("POST", "/api/tasks/{task_id}/coding/values"),
                ("POST", "/api/tasks/{task_id}/highlights"),
                ("PUT", "/api/tasks/{task_id}/appraisal/{article_key}"),
                ("POST", "/api/tasks/{task_id}/collaboration/roles")):
            self.assertIn(required, matrix, matrix.get(required))
        # 行为证据（真实 HTTP）：TEAM_PROTOCOL 行走 mutate；SOLO_CONFIG 行走
        # write_guard 且不走 mutate（调用账区分）。
        before_mutate = list(self.mutate_calls)
        before_guard = list(self.guard_calls)
        vote = self.client.post(
            f"/api/tasks/{self.task_id}/decisions",
            json={"zotero_key": ITEM2, "decision": "include",
                  "reason": "", "notes": "", "tags": None},
            headers=self._auth("p24screener"))
        self.assertEqual(vote.status_code, 200, vote.text)
        self.assertEqual(len(self.mutate_calls) - len(before_mutate), 1)
        self.assertEqual(self.mutate_calls[-1]["operation"], "member_vote")
        self.assertEqual(self.mutate_calls[-1]["stage"], "screen")
        # P15-D1 行为锚：坏键（域外）→ 400 structured 客户端错误（不再是
        # 409 task_mode_changed 的徒劳重试），且零业务写零回执。
        bad = self.client.post(
            f"/api/tasks/{self.task_id}/decisions",
            json={"zotero_key": "NOT-IN-DOMAIN", "decision": "include",
                  "reason": "", "notes": "", "tags": None},
            headers=self._auth("p24screener"))
        self.assertEqual(bad.status_code, 400, bad.text)
        self.assertEqual(bad.json()["detail"]["code"],
                         "team_flow_invalid_item_keys")
        # workflow-settings（SOLO_CONFIG）走 write_guard，不走 mutate（调用账
        # 区分两类入口；写本身幂等回写同值，业务状态不变）。
        guard_before = len(self.guard_calls)
        mutate_before = len(self.mutate_calls)
        settings = self.client.get(
            f"/api/tasks/{self.task_id}/workflow-settings",
            headers=self._auth("p24owner")).json()
        put = self.client.put(
            f"/api/tasks/{self.task_id}/workflow-settings",
            json={"expected_version": settings["version"],
                  "stages": settings["stages"],
                  "evidence_required_field_ids":
                      settings.get("evidence_required_field_ids", [])},
            headers=self._auth("p24owner"))
        self.assertIn(put.status_code, (200, 409), put.text)
        self.assertGreaterEqual(len(self.guard_calls) - guard_before, 1)
        self.assertEqual(len(self.mutate_calls) - mutate_before, 0)

    # ------------------------------------------------------------------
    # 2. team 无漏网：全部真实业务写都经 P13 提交协议（调用账+回执账）
    # ------------------------------------------------------------------
    def test_no_missing_operation_bypass_in_team(self):
        headers = self._auth("p24screener")
        # 链门事实：fulltext 阶段操作（含批注）要求 stage1 完成——第二位
        # 必需筛选人补票后 ITEM2 的 stage1 完成而 fulltext 未完成（批注可写）。
        second = self.client.post(
            f"/api/tasks/{self.task_id}/decisions",
            json={"zotero_key": ITEM2, "decision": "include",
                  "reason": "", "notes": "", "tags": None},
            headers=self._auth("p24coder"))
        self.assertEqual(second.status_code, 200, second.text)
        probes: list[tuple[str, object, int]] = []
        # (label, callable, expected receipts delta)——期望 0 的探测=结构化
        # 拒绝（链/历史门），仍必须先经 team 协议检查，绝不静默直写。
        def probe(label, method, url, **kwargs):
            before = self._receipt_count("p24screener")
            mutate_before = len(self.mutate_calls)
            resp = method(url, headers=headers, **kwargs)
            after = self._receipt_count("p24screener")
            probes.append({"label": label, "status": resp.status_code,
                           "receipts_delta": after - before,
                           "mutate_delta":
                               len(self.mutate_calls) - mutate_before})
            return resp

        # screen vote（新键 ITEM4 的第一票）→ committed +1 回执。
        resp = probe("screen-vote", self.client.post,
                     f"/api/tasks/{self.task_id}/decisions",
                     json={"zotero_key": ITEM4, "decision": "include",
                           "reason": "", "notes": "", "tags": None})
        self.assertEqual(resp.status_code, 200, resp.text)
        # stage2 在上游未完成键上 → 409 链门，零回执，但已进协议（mutate 记账）。
        probe("stage2-chain-blocked", self.client.post,
              f"/api/tasks/{self.task_id}/stage2/decisions",
              json={"zotero_key": ITEM4, "decision": "include",
                    "reason": "", "notes": "", "tags": None})
        # coding（链已开的 ITEM）→ committed。
        resp = probe("coding-value", self.client.post,
                     f"/api/tasks/{self.task_id}/coding/values",
                     json={"zotero_key": ITEM, "dim_id": self.dim_id,
                           "value": "p24-cohort"})
        self.assertEqual(resp.status_code, 200, resp.text)
        # annotations 三态：add → committed；note 编辑→committed；delete→committed。
        resp = probe("highlight-add", self.client.post,
                     f"/api/tasks/{self.task_id}/highlights",
                     json={"zotero_key": ITEM2,
                           "events": [{"kind": "highlight", "page": 1,
                                       "text": "P24", "color": "jade",
                                       "note": "", "rects": [[0.1, 0.1, 0.4, 0.4]]}]})
        self.assertEqual(resp.status_code, 200, resp.text)
        highlight_id = resp.json()["highlights"][0]["id"]
        resp = probe("highlight-note-update", self.client.patch,
                     f"/api/tasks/{self.task_id}/highlights/{highlight_id}",
                     json={"zotero_key": ITEM2, "note": "P24 note",
                           "expected_note": ""})
        self.assertEqual(resp.status_code, 200, resp.text)
        resp = probe("highlight-delete", self.client.delete,
                     f"/api/tasks/{self.task_id}/highlights/{highlight_id}")
        # DELETE 无请求体：保护键来自存储行（写窗内 P11 artifact 权威复核）。
        for row in probes:
            self.assertGreaterEqual(
                row["mutate_delta"], 1,
                f"{row['label']} 绕过 team 协议（无 mutate 调用账）")
            if 200 <= row["status"] < 300:
                self.assertEqual(row["receipts_delta"], 1, row)
            else:
                self.assertEqual(row["receipts_delta"], 0, row)
                self.assertIn(row["status"], (400, 403, 404, 409), row)
        # 旧 solo 族在 team 任务上不得成为无门旁路：成员直接打 solo 形状
        # （screener 伪装参数）必须 403，零回执零写。
        spoof = self.client.post(
            f"/api/tasks/{self.task_id}/decisions?screener=p24owner",
            json={"zotero_key": ITEM4, "decision": "exclude",
                  "reason": "", "notes": "", "tags": None},
            headers=headers)
        self.assertEqual(spoof.status_code, 403, spoof.text)

    # ------------------------------------------------------------------
    # 3. SSE postcommit：task_id 规范化、change_kind、锁后顺序、无票面
    # ------------------------------------------------------------------
    def test_sse_commit_order_task_id_and_no_votes(self):
        headers = self._auth("p24screener")
        before_events = len(self.published)
        resp = self.client.post(
            f"/api/tasks/{self.task_id}/decisions",
            json={"zotero_key": ITEM3, "decision": "include",
                  "reason": "", "notes": "", "tags": None},
            headers=headers)
        self.assertEqual(resp.status_code, 200, resp.text)
        # S3：真实 HTTP 响应携带 P15 信封头。
        self.assertIn("X-Request-Id", resp.headers)
        self.assertIn("X-Idempotent-Replay", resp.headers)
        self.assertIn("X-Receipt-Id", resp.headers)
        publish_rows = [p for p in self.published[before_events:]
                        if p["event"].get("type") == "task-workflow-changed"]
        events = [p["event"] for p in publish_rows]
        # 窄名单广播：恰好三位当前参与者（owner+两位必需筛选人）各一份同形事件。
        self.assertEqual(len(events), 3, self.published[before_events:])
        notification = events[0]
        self.assertTrue(all(e == notification for e in events))
        canonical_task_id = self.task_id  # fixture 建队返回的规范 id
        self.assertEqual(notification["task_id"], canonical_task_id)
        self.assertEqual(notification["stage"], "screen")
        self.assertEqual(notification["item_keys"], [ITEM3])
        self.assertIn(notification["change_kind"],
                      {"vote", "completion", "roster", "arbitration"})
        self.assertEqual(notification["change_kind"], "vote")
        # 事件不含个人票（无 decision/votes/value 字段）。
        for banned in ("decision", "votes", "value", "rationale", "records"):
            self.assertNotIn(banned, notification)
        # postcommit 顺序锚：发布时刻同 actor 回执已持久化（≥1）。
        self.assertGreaterEqual(publish_rows[0]["receipts_at_publish"], 1)
        # 失败请求绝不发成功事件：坏键 400（P15-D1）后无新增事件。
        fail_before = len(self.published)
        bad = self.client.post(
            f"/api/tasks/{self.task_id}/decisions",
            json={"zotero_key": "GHOST-KEY-404", "decision": "include",
                  "reason": "", "notes": "", "tags": None},
            headers=headers)
        self.assertEqual(bad.status_code, 400, bad.text)
        self.assertEqual(len(self.published) - fail_before, 0)
        # unknown 信封（P24 前置 d）：路由必须 409 team_flow_unknown_outcome，
        # 绝不 saved:True/null，且不发成功事件。
        runtime = self.main_mod.app.state.review_workflow
        unknown_before = len(self.published)
        with mock.patch.object(
                runtime, "mutate", create=True,
                return_value={"outcome": "unknown", "idempotent": False,
                              "request_id": "p24-unknown-probe",
                              "result": None, "receipt": None,
                              "seals": (), "cleanup_errors": (),
                              "error": "probe: window exit failed"}):
            resp409 = self.client.post(
                f"/api/tasks/{self.task_id}/decisions",
                json={"zotero_key": ITEM4, "decision": "include",
                      "reason": "", "notes": "", "tags": None},
                headers=headers)
        self.assertEqual(resp409.status_code, 409, resp409.text)
        self.assertEqual(resp409.json()["detail"]["code"],
                         "team_flow_unknown_outcome")
        self.assertEqual(len(self.published) - unknown_before, 0)

    # ------------------------------------------------------------------
    # 4. 活动名单窄投影 vs 历史三源名单（不全局删）
    # ------------------------------------------------------------------
    def test_active_roster_vs_historical_options(self):
        # 任务卡投影：窄活动名单（协作名册）+ 历史三源 screeners 并存。
        listing = self.client.get("/api/tasks",
                                  headers=self._auth("p24owner")).json()
        card = next(t for t in listing["tasks"] if t["task_id"] == self.task_id)
        self.assertEqual(card.get("active_screeners_status"), "available")
        active_ids = {entry["user_id"] for entry in card["active_screeners"]}
        roster_ids = {self.users[name]["user_id"]
                      for name in ("p24owner", "p24screener", "p24coder")}
        self.assertEqual(active_ids, roster_ids)
        self.assertNotIn("p24ghost",
                          {e.get("username") for e in card["active_screeners"]})
        self.assertIn("p24ghost", card["screeners"])  # 历史身份不全局删
        # team-progress 当前补充：窄投影键随行，历史 members/grants 视图保留。
        saved_mode = os.environ.get("REVIEWFLOW_MODE")
        os.environ["REVIEWFLOW_MODE"] = "server"
        try:
            progress = self.client.get(
                f"/api/tasks/{self.task_id}/team-progress",
                headers=self._auth("p24owner"))
        finally:
            if saved_mode is None:
                os.environ.pop("REVIEWFLOW_MODE", None)
            else:
                os.environ["REVIEWFLOW_MODE"] = saved_mode
        self.assertEqual(progress.status_code, 200, progress.text)
        body = progress.json()
        self.assertEqual(body.get("active_screeners_status"), "available")
        progress_active = {e["user_id"] for e in body["active_screeners"]}
        self.assertEqual(progress_active, roster_ids)
        ghost = [m for m in body["members"] if m["username"] == "p24ghost"]
        self.assertEqual(ghost, [])  # 名册窄投影：无授权者不进"当前"
        self.assertTrue(body["members"], "历史成员视图保留")
        self.assertIn("grants", body)
        self.assertIn("can_manage", body)

    # ------------------------------------------------------------------
    # 5. P13 端到端补测（前置 a）：真实 WorkflowRuntime 完整链直调
    # ------------------------------------------------------------------
    def test_p13_run_team_mutation_end_to_end_real_runtime(self):
        from coscreen.team_flow_types import TeamOperation
        runtime = self.main_mod.app.state.review_workflow
        user = dict(self.users["p24coder"])
        request_id = "p24-e2e-" + uuid.uuid4().hex[:12]
        payload = {"task_id": self.task_id, "route": "e2e-probe",
                   "zotero_key": ITEM4, "decision": "include"}
        from custom_backend.workspace_undo_api import member_request_fingerprint
        payload_id, payload_sha = member_request_fingerprint(request_id, payload)

        def writer(ctx):
            conn = ctx.write_inputs.actor_conn
            conn.execute(
                "INSERT INTO decisions (zotero_key, decision, exclusion_reason,"
                " notes, tags, screened_at, updated_at)"
                " VALUES (?, 'include', 'p24-e2e', '', '',"
                " strftime('%Y-%m-%dT%H:%M:%fZ','now'),"
                " strftime('%Y-%m-%dT%H:%M:%fZ','now'))"
                " ON CONFLICT(zotero_key) DO UPDATE SET decision='include',"
                " exclusion_reason='p24-e2e',"
                " updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')",
                (ITEM4,))
            return {"saved": True}

        envelope = runtime.mutate(
            self.task_id, user,
            operation=TeamOperation(name="member_vote", stage="screen"),
            item_keys=(ITEM4,), request_id=payload_id,
            request_sha256=payload_sha, writer=writer)
        self.assertEqual(envelope["outcome"], "committed")
        self.assertFalse(envelope["idempotent"])
        self.assertIn("X-Request-Id", envelope["headers"])
        self.assertIn("X-Receipt-Id", envelope["headers"])
        self.assertEqual(envelope["headers"]["X-Idempotent-Replay"], "false")
        receipt_id = envelope["receipt"]["receipt_id"]
        # 同 request_id 同内容重试 → 幂等命中，不重放 writer。
        replay = runtime.mutate(
            self.task_id, user,
            operation=TeamOperation(name="member_vote", stage="screen"),
            item_keys=(ITEM4,), request_id=payload_id,
            request_sha256=payload_sha, writer=writer)
        self.assertEqual(replay["outcome"], "committed")
        self.assertTrue(replay["idempotent"])
        self.assertEqual(replay["receipt"]["receipt_id"], receipt_id)
        self.assertEqual(
            self._row("p24coder",
                      "SELECT exclusion_reason FROM decisions"
                      " WHERE zotero_key=?", (ITEM4,))[0], "p24-e2e")


    # ------------------------------------------------------------------
    # 修轮 D1：values-batch（workspace_undo_api 第 4 入口）同款接线——
    # 成功=S3 头+SSE 事件；unknown=409 惯例、零事件（真实 HTTP）。
    # ------------------------------------------------------------------
    def test_values_batch_envelope_sse_and_unknown(self):
        headers = self._auth("p24screener")
        before = len(self.published)
        resp = self.client.post(
            f"/api/tasks/{self.task_id}/coding/values-batch",
            json={"zotero_key": ITEM, "values": {str(self.dim_id): "batch-v1"},
                  "notes": None, "evidence": None, "draft": False},
            headers=headers)
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertIn("X-Request-Id", resp.headers)
        self.assertIn("X-Receipt-Id", resp.headers)   # S3 头透传
        events = [p_["event"] for p_ in self.published[before:]
                  if p_["event"].get("type") == "task-workflow-changed"]
        self.assertEqual(len(events), 3)              # 窄名单三参与者同形
        self.assertEqual(events[0]["stage"], "coding")
        self.assertEqual(events[0]["item_keys"], [ITEM])
        self.assertEqual(events[0]["change_kind"], "vote")
        # 幂等重放（同 X-Request-Id 同内容）不重复广播（note1 修）。
        replay_before = len(self.published)
        replay = self.client.post(
            f"/api/tasks/{self.task_id}/coding/values-batch",
            json={"zotero_key": ITEM, "values": {str(self.dim_id): "batch-v1"},
                  "notes": None, "evidence": None, "draft": False},
            headers={**headers, "X-Request-Id": resp.headers["X-Request-Id"]})
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertEqual(replay.headers.get("X-Idempotent-Replay"), "true")
        self.assertEqual(
            len([p_ for p_ in self.published[replay_before:]
                 if p_["event"].get("type") == "task-workflow-changed"]), 0)
        # unknown 信封：409 team_flow_unknown_outcome、绝不 200/null、零事件。
        runtime = self.main_mod.app.state.review_workflow
        unknown_before = len(self.published)
        with mock.patch.object(
                runtime, "mutate", create=True,
                return_value={"outcome": "unknown", "idempotent": False,
                              "request_id": "p24-vb-unknown",
                              "result": None, "receipt": None,
                              "seals": (), "cleanup_errors": (),
                              "error": "probe: window exit failed"}):
            resp409 = self.client.post(
                f"/api/tasks/{self.task_id}/coding/values-batch",
                json={"zotero_key": ITEM,
                      "values": {str(self.dim_id): "batch-x"},
                      "notes": None, "evidence": None, "draft": False},
                headers=headers)
        self.assertEqual(resp409.status_code, 409, resp409.text)
        self.assertEqual(resp409.json()["detail"]["code"],
                         "team_flow_unknown_outcome")
        self.assertEqual(len(self.published) - unknown_before, 0)

    # ------------------------------------------------------------------
    # 修轮 D2：P15-D1 收窄的 409 分支——开窗模式漂移字面信号真实映射。
    # ------------------------------------------------------------------
    def test_p15_d1_drift_signal_maps_409_task_mode_changed(self):
        commit = importlib.import_module("custom_backend.team_flow_commit")
        headers = self._auth("p24screener")
        drift = ValueError(
            "retained_team_write_inputs requires a team task, got 'personal'")
        with mock.patch.object(
                commit, "retained_team_write_inputs", side_effect=drift):
            resp = self.client.post(
                f"/api/tasks/{self.task_id}/decisions",
                json={"zotero_key": ITEM3, "decision": "include",
                      "reason": "", "notes": "", "tags": None},
                headers=headers)
        self.assertEqual(resp.status_code, 409, resp.text)
        detail = resp.json()["detail"]
        self.assertEqual(detail["code"], "task_mode_changed")
        self.assertEqual(detail["message_key"], "team.flow.task_mode_changed")
        self.assertIn("requires a team task", detail["details"])

    # ------------------------------------------------------------------
    # 修轮 note3：P17 四操作 S3 头的真实 HTTP 断言（appraisal PUT）。
    # ------------------------------------------------------------------
    def test_p17_appraisal_s3_header_real_http(self):
        appraisal_api = importlib.import_module("custom_backend.appraisal_api")
        # 宿主同款准备（main._prepare_invited_member_storage 的显式 prepare）。
        appraisal_api.prepare_appraisal_storage_for(
            self.info, "p24coder", self.main_mod._screener_db)
        resp = self.client.put(
            f"/api/tasks/{self.task_id}/appraisal/{ITEM}",
            json={"tool": "casp-qualitative",
                  "responses": {"1": "Yes"}, "comments": {}, "note": ""},
            headers=self._auth("p24coder"))
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertIn("X-Request-Id", resp.headers)
        self.assertIn("X-Receipt-Id", resp.headers)
        self.assertIn(resp.headers.get("X-Idempotent-Replay"),
                      ("true", "false"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
