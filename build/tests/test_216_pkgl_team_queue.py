"""包 I+L 验收：坏库 503 站点抽验 + 旧 GET /work-queue 团队口径换接 keyed。

L 红绿（差分）：同一真实团队夹具上，**未修底版**（包 G 交付字节
``out/pkgIL_before/review_workflow_api.before.py``，sha256 514bb645…）与
**本包修后模块**分别对同一 team 任务求 ``queue()``：

    pre-join-sealed 键（A+B 在 D 加入前已完成 stage1+stage2 双 include，
    seal 落最后投票人库）——
      旧口径（path eligible()，按当前名册数 D 自己的票）→ fulltext/coding
      队列**漏掉**这些键（D 无票）；
      新口径（P23 keyed 通道：完成看 seal）→ 键在队列中且 completed=True。

solo 零回归：同一 solo 任务上两版 ``queue()`` 载荷逐字相等（含 total/
pending/scope_progress/queue 全部字段）。

I 抽验（3+1 站点，坏库→503 且词条=SCREENER_LIBRARY_UNAVAILABLE_MESSAGE）：
    article_rows（team 队列全域域读）、reason-library GET（原因库坏字节）、
    workflow-settings PUT（方案读/写路径）、skip_order（actor 库坏字节）。

产品导入全部延迟到 setUpClass（python -I -B 可装载）；真实建队配方沿用
P25/PkgC（零 mock 建队、真实 HTTP 投票、真实 F2 登记）。
"""
from __future__ import annotations

import importlib
import importlib.util
import json
import os
import sqlite3
import unittest
import uuid
from contextlib import closing
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
BEFORE = REPO_ROOT / "out" / "pkgIL_before" / "review_workflow_api.before.py"

#: 包 G 交付底版（sha256 前 16 位）——差分基线必须是未修字节。
BEFORE_SHA256_PREFIX = "514bb645ed29cf1f"

KEY_SEALED = "PKGL-SEALED"
KEY_OPEN = "PKGL-OPEN"
KEY_SOLO = "PKGL-SOLO"

_UPSERT_LIBRARY = (
    "INSERT INTO team_vote_revision_meta (singleton, format_version,"
    " library_id, seeded_stage1, seeded_stage2, advances)"
    " VALUES (1, 2, ?, 0, 0, 0) ON CONFLICT(singleton) DO UPDATE SET"
    " library_id=excluded.library_id")
_INSERT_ARTICLE = (
    "INSERT INTO articles (zotero_key, title, is_duplicate_of, import_order)"
    " VALUES (?, ?, NULL, ?)")
_LATCH = "UPDATE workflow_state SET team_latched=1 WHERE singleton=1"


def _close_quietly(conn):
    try:
        conn.close()
    except Exception:
        pass


def _borrowed_writer(path):
    conn = sqlite3.connect(str(path), timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("BEGIN IMMEDIATE")
    return conn


class _EnvGuard:
    def __init__(self, value, restore):
        self._value = value
        self._restore = restore

    def __enter__(self):
        os.environ["REVIEWFLOW_MODE"] = self._value
        return self

    def __exit__(self, *exc_info):
        self._restore()
        return False


class PkgITeamQueueTests(unittest.TestCase):
    """One real team with pre-join seals + one solo task; name order."""

    task_id = {}
    user_db = {}
    collab_path = {}

    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        from custom_backend import auth as auth_mod
        from custom_backend import main as main_mod

        cls.client = TestClient(main_mod.app)
        cls.client.__enter__()
        cls.main_mod = main_mod
        cls.rwa = importlib.import_module("custom_backend.review_workflow_api")
        cls.sha_before = cls._sha(BEFORE)
        assert cls.sha_before.startswith(BEFORE_SHA256_PREFIX), cls.sha_before
        spec = importlib.util.spec_from_file_location(
            "custom_backend.review_workflow_api_pkgI_base", BEFORE)
        cls.base = importlib.util.module_from_spec(spec)
        # 先注册再 exec：@dataclass + ``from __future__ import annotations``
        # 的字段解析要求 cls.__module__ 已在 sys.modules（P03 装载协议同法）。
        import sys
        sys.modules[spec.name] = cls.base
        spec.loader.exec_module(cls.base)
        # F1 红侧锚：包 I 中间态（notify 已误移出 if changed:，15 站 503 已修）。
        buggy_path = (REPO_ROOT / "out" / "pkgIL_before"
                      / "review_workflow_api.buggyI.py")
        assert cls._sha(buggy_path).startswith("5031490b762d5057"),             cls._sha(buggy_path)
        buggy_spec = importlib.util.spec_from_file_location(
            "custom_backend.review_workflow_api_pkgI_buggy", buggy_path)
        buggy = importlib.util.module_from_spec(buggy_spec)
        sys.modules[buggy_spec.name] = buggy
        buggy_spec.loader.exec_module(buggy)
        cls.users = {}
        for name in ("pkglowner", "pkgla", "pkgb", "pkgld", "pkgscolo"):
            user = auth_mod.create_user(name, "PkgIL-Queue-2026")
            cls.users[name] = {
                "user_id": user["id"], "username": user["username"],
                "token": auth_mod.create_access_token(
                    user["id"], user["username"])}
        cls.uid = {name: value["user_id"] for name, value in cls.users.items()}
        cls._build_team()
        cls._build_solo()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    # ------------------------------------------------------------------
    # fixture helpers（P25/PkgC 真实建队配方）
    # ------------------------------------------------------------------
    @staticmethod
    def _sha(path):
        import hashlib
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()

    @classmethod
    def _auth_setup(cls, name):
        return {"Authorization": "Bearer " + cls.users[name]["token"]}

    @classmethod
    def _server_mode(cls):
        saved = os.environ.get("REVIEWFLOW_MODE")

        def restore():
            if saved is None:
                os.environ.pop("REVIEWFLOW_MODE", None)
            else:
                os.environ["REVIEWFLOW_MODE"] = saved
        return _EnvGuard("server", restore)

    @classmethod
    def _runtime_for(cls, module):
        """同一宿主接线下构造给定模块版本的 WorkflowRuntime。"""
        from custom_backend import main as main_mod
        return module.WorkflowRuntime(
            main_mod._task_or_404, main_mod._screener_db,
            main_mod.DATA_DIR, lambda info, user: True)

    @classmethod
    def _build_team(cls):
        collab = importlib.import_module("coscreen.task_collaboration")
        cdb = importlib.import_module("coscreen.db")
        wf = importlib.import_module("coscreen.review_workflow")
        tasks = importlib.import_module("coscreen.tasks")
        tf = importlib.import_module("coscreen.team_terminal_facts")
        tr = importlib.import_module("coscreen.team_terminal_registry")
        receipts = importlib.import_module("coscreen.team_flow_receipts")
        coding_evidence = importlib.import_module("coscreen.coding_evidence")
        tsl = importlib.import_module("custom_backend.team_source_lifecycle")

        owner = cls.users["pkglowner"]
        owner_uid = owner["user_id"]
        task_id = cls.client.post(
            "/api/tasks", json={"name": "pkgl-" + uuid.uuid4().hex[:8]},
            headers=cls._auth_setup("pkglowner")).json()["task_id"]
        info = tasks.find_task(task_id, cls.main_mod.DATA_DIR)
        task_dir = info.dir
        collab_path = collab.shared_store_path(task_dir)
        if not collab_path.is_file():
            collab.initialize_task_store(
                task_dir, task_id=task_id, owner_user_id=owner_uid,
                owner_username=owner["username"])
        collab.set_business_role(
            collab_path, actor_user_id=owner_uid, user_id=owner_uid,
            business_role="reconciler", task_id=task_id)
        slug = tasks.slugify_screener
        names = ("pkglowner", "pkgla", "pkgb", "pkgld")
        cls.user_db[task_id] = {
            name: task_dir / f"{slug(cls.users[name]['username'])}.db"
            for name in names}
        for name in ("pkgla", "pkgb"):   # D 在 seal 之后才 invite（pre-join）
            uid = cls.uid[name]
            collab.grant_role(collab_path, actor_user_id=owner_uid,
                              user_id=uid, role="coder", task_id=task_id)
            collab.set_business_role(
                collab_path, actor_user_id=owner_uid, user_id=uid,
                business_role="coder", task_id=task_id)
        articles = [(KEY_SEALED, "PkgL sealed", 0),
                    (KEY_OPEN, "PkgL open", 1)]
        for name, path in cls.user_db[task_id].items():
            cdb.init_db(path)
            wf.migrate(path)
            tf.prepare_team_terminal_facts(path)
            receipts.prepare_team_flow_receipts(path)
            coding_evidence.ensure_fields(path)
            conn = sqlite3.connect(str(path), timeout=15)
            try:
                conn.execute("PRAGMA foreign_keys=ON")
                if name != "pkglowner":
                    conn.execute(_UPSERT_LIBRARY, ("%032x" % cls.uid[name],))
                conn.executemany(_INSERT_ARTICLE, articles)
                if name == "pkglowner":
                    conn.execute(_LATCH)
                conn.commit()
            finally:
                conn.close()
        tr.prepare_team_terminal_registry(collab_path)
        importlib.import_module(
            "coscreen.screening_arbitration"
        ).prepare_arbitration_attestation_storage(collab_path)
        with closing(sqlite3.connect(str(collab_path))) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT task_instance_id FROM main.team_task_protocol"
                " LIMIT 1").fetchone()
        assert row is not None, "team protocol row missing (P18)"
        instance_id = row["task_instance_id"]
        tf.prepare_team_terminal_facts(collab_path)
        receipts.prepare_team_flow_receipts(collab_path)
        for name in ("pkgla", "pkgb"):
            uid = cls.uid[name]
            library = "%032x" % uid
            token = tsl.make_source_identity(
                task_dir=task_dir, source_path=cls.user_db[task_id][name],
                source_user_id=uid, source_library_id=library)
            writer = _borrowed_writer(collab_path)
            try:
                tr.register_source_on(
                    writer, task_instance_id=instance_id, source_user_id=uid,
                    source_library_id=library, source_path_identity=token,
                    registration_id=f"reg-pkgl-{name}")
                writer.commit()
            finally:
                _close_quietly(writer)
        cls.task_id["team"] = task_id
        cls.collab_path[task_id] = collab_path
        # pre-join seals：A+B（D 加入前的全部 required screeners）完成
        # screen+fulltext 双 include——seal 落最后投票人 B 的成员库。
        # 顺序：先两票 screen 封印 stage1，再两票 fulltext（站内链门）。
        for stage, route in (("screen", "decisions"),
                             ("fulltext", "stage2/decisions")):
            for name in ("pkgla", "pkgb"):
                resp = cls.client.post(
                    f"/api/tasks/{task_id}/{route}",
                    json={"zotero_key": KEY_SEALED, "decision": "include",
                          "reason": "", "notes": "", "tags": None},
                    headers=cls._auth_setup(name))
                assert resp.status_code == 200, (name, stage, resp.text)
        # D 在 seal 之后加入（真实 invite + business role；不改 A 的票）。
        with cls._server_mode():
            resp = cls.client.post(
                f"/api/tasks/{task_id}/collaboration/invite",
                json={"username": cls.users["pkgld"]["username"],
                      "role": "coder"},
                headers=cls._auth_setup("pkglowner"))
            assert resp.status_code == 201, resp.text
            resp = cls.client.put(
                f"/api/tasks/{task_id}/collaboration/business-role/"
                f"{cls.uid['pkgld']}",
                json={"business_role": "coder"},
                headers=cls._auth_setup("pkglowner"))
            assert resp.status_code == 200, resp.text

    @classmethod
    def _build_solo(cls):
        tasks = importlib.import_module("coscreen.tasks")
        wf = importlib.import_module("coscreen.review_workflow")
        task_id = cls.client.post(
            "/api/tasks", json={"name": "pkgl-solo-" + uuid.uuid4().hex[:8]},
            headers=cls._auth_setup("pkgscolo")).json()["task_id"]
        info = tasks.find_task(task_id, cls.main_mod.DATA_DIR)
        db = info.dir / (
            tasks.slugify_screener(cls.users["pkgscolo"]["username"]) + ".db")
        wf.migrate(db)
        stamp = "2026-11-20T00:00:00+00:00"
        with closing(sqlite3.connect(str(db))) as conn:
            conn.execute(_INSERT_ARTICLE, (KEY_SOLO, "PkgL solo", 0))
            for table in ("decisions", "stage2_decisions"):
                conn.execute(
                    f"INSERT OR REPLACE INTO {table}"
                    "(zotero_key,decision,exclusion_reason,notes,tags,"
                    "screened_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                    (KEY_SOLO, "include", "", "", "", stamp, stamp))
            conn.commit()
        cls.task_id["solo"] = task_id
        cls.user_db[task_id] = {"pkgscolo": db}

    def _user(self, name):
        return {"user_id": self.uid[name],
                "username": self.users[name]["username"]}

    def _get(self, task_id, path, name):
        return self.client.get(f"/api/tasks/{task_id}{path}",
                               headers=self._auth_setup(name))

    # ------------------------------------------------------------------
    # L1 红绿：pre-join-sealed 键在 team fulltext/coding 队列
    # ------------------------------------------------------------------
    def test_l1_team_fulltext_queue_prejoin_seal_red_green(self):
        task = self.task_id["team"]
        d_user = self._user("pkgld")
        # 绿：本包修后口径（真实 HTTP）。
        green = self._get(task, "/work-queue/fulltext", "pkgld")
        self.assertEqual(green.status_code, 200, green.text)
        body = green.json()
        keys = [row["zotero_key"] for row in body["queue"]]
        self.assertIn(KEY_SEALED, keys, body)
        self.assertNotIn(KEY_OPEN, keys, body)
        sealed = next(row for row in body["queue"]
                      if row["zotero_key"] == KEY_SEALED)
        self.assertTrue(sealed["completed"], sealed)
        # 两阶段 seal 均已终局：本阶段完成→只读（保存门同码），不是"可写"。
        self.assertFalse(sealed["writable"], sealed)
        self.assertEqual(sealed["reason"], "stage_completed_read_only", sealed)
        self.assertEqual(body["total"], 1, body)
        self.assertEqual(body["scope_progress"],
                         {"total": 1, "completed": 1, "pending": 0}, body)
        # 红：未修底版（before 514bb645，path 口径）在同一任务、同一用户上
        # 的 eligible()（D 无票 → 漏键）。
        base_runtime = self._runtime_for(self.base)
        red = base_runtime.queue(task, d_user, "fulltext")
        self.assertEqual(red["total"], 0, red)
        self.assertEqual([row["zotero_key"] for row in red["queue"]], [], red)
        self.assertNotEqual(body["queue"], red["queue"])

    def test_l2_team_coding_queue_prejoin_seal_red_green(self):
        task = self.task_id["team"]
        d_user = self._user("pkgld")
        green = self._get(task, "/work-queue/coding", "pkgld")
        self.assertEqual(green.status_code, 200, green.text)
        keys = [row["zotero_key"] for row in green.json()["queue"]]
        self.assertEqual(keys, [KEY_SEALED], green.text)
        red = self._runtime_for(self.base).queue(task, d_user, "coding")
        self.assertEqual(red["total"], 0, red)

    def test_l2b_team_screen_queue_keeps_full_domain(self):
        """screen 无链：全部 canonical 键入队（不因 D 无票而漏），坏库不 500。"""
        task = self.task_id["team"]
        green = self._get(task, "/work-queue/screen", "pkgld")
        self.assertEqual(green.status_code, 200, green.text)
        body = green.json()
        self.assertEqual([row["zotero_key"] for row in body["queue"]],
                         [KEY_SEALED, KEY_OPEN], body)
        self.assertEqual(body["scope_progress"],
                         {"total": 2, "completed": 1, "pending": 1}, body)
        # random 序与 shuffle 路径同通道可用（只换序不换集）。
        shuffled = self._get(task, "/work-queue/screen?order=random", "pkgld")
        self.assertEqual(shuffled.status_code, 200, shuffled.text)
        self.assertEqual({row["zotero_key"] for row in shuffled.json()["queue"]},
                         {KEY_SEALED, KEY_OPEN})
        resp = self.client.post(
            f"/api/tasks/{task}/work-queue/screen/shuffle",
            headers=self._auth_setup("pkgld"))
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual({row["zotero_key"] for row in resp.json()["queue"]},
                         {KEY_SEALED, KEY_OPEN})

    # ------------------------------------------------------------------
    # L3 solo 零回归：两版 queue() 载荷逐字相等
    # ------------------------------------------------------------------
    def test_l3_solo_queue_payload_bytes_identical(self):
        task = self.task_id["solo"]
        user = self._user("pkgscolo")
        patched = self.rwa.WorkflowRuntime(
            self.main_mod._task_or_404, self.main_mod._screener_db,
            self.main_mod.DATA_DIR, lambda info, u: True)
        base = self._runtime_for(self.base)
        for stage in ("screen", "fulltext", "coding"):
            new = patched.queue(task, user, stage, order="import")
            old = base.queue(task, user, stage, order="import")
            self.assertEqual(new, old, stage)
        # HTTP 面同样是同形载荷（solo 任务：owner 即 actor）。
        http = self._get(task, "/work-queue/screen", "pkgscolo")
        self.assertEqual(http.status_code, 200, http.text)
        self.assertEqual(http.json(), patched.queue(
            task, user, "screen", order="import"))

    # ------------------------------------------------------------------
    # L4 口令口径：team 队列 reason/writable 与 keyed_work_items 一致
    # ------------------------------------------------------------------
    def test_l4_team_queue_reason_agrees_with_keyed_items(self):
        task = self.task_id["team"]
        queue = self._get(task, "/work-queue/fulltext", "pkgld").json()
        keyed = self.client.post(
            f"/api/tasks/{task}/work-items/fulltext/keyed",
            json={"zotero_keys": [KEY_SEALED, KEY_OPEN]},
            headers=self._auth_setup("pkgld"))
        self.assertEqual(keyed.status_code, 200, keyed.text)
        items = keyed.json()["items_by_key"]
        for row in queue["queue"]:
            self.assertEqual(row["reason"], items[row["key"]]["reason"])
            self.assertEqual(row["writable"], items[row["key"]]["writable"])

    # ------------------------------------------------------------------
    # I1 站点抽验：article_rows（team 队列域读）坏库 → 503
    # ------------------------------------------------------------------
    def test_i1_article_rows_corrupt_db_is_503(self):
        from fastapi import HTTPException
        from custom_backend import main as main_mod
        bad = self.main_mod.DATA_DIR / f"pkgil-bad-{uuid.uuid4().hex[:8]}.db"
        bad.write_bytes(b"this is not a sqlite database at all........")
        self.addCleanup(bad.unlink, True)
        with self.assertRaises(HTTPException) as raised:
            self.rwa.WorkflowRuntime.article_rows(bad)
        self.assertEqual(raised.exception.status_code, 503)
        self.assertEqual(raised.exception.detail,
                         main_mod.SCREENER_LIBRARY_UNAVAILABLE_MESSAGE)

    # ------------------------------------------------------------------
    # I2 站点抽验：reason-library GET 坏库 → 503
    # ------------------------------------------------------------------
    def test_i2_reason_library_corrupt_db_is_503(self):
        from custom_backend import main as main_mod
        task = self.task_id["solo"]
        user_id = self.uid["pkgscolo"]
        reason_db = (self.main_mod.DATA_DIR / "workflow_accounts"
                     / f"{user_id}.sqlite3")
        reason_db.parent.mkdir(parents=True, exist_ok=True)
        self.addCleanup(reason_db.unlink, True)
        reason_db.write_bytes(b"this is not a sqlite database at all........")
        resp = self._get(task, "/reason-library?stage=screen", "pkgscolo")
        self.assertEqual(resp.status_code, 503, resp.text)
        self.assertEqual(resp.json()["detail"],
                         main_mod.SCREENER_LIBRARY_UNAVAILABLE_MESSAGE)

    # ------------------------------------------------------------------
    # I3 站点抽验：workflow-settings PUT 方案读坏库 → 503
    # ------------------------------------------------------------------
    def test_i3_settings_put_corrupt_owner_db_is_503(self):
        from custom_backend import main as main_mod
        task = self.task_id["solo"]
        settings = self._get(task, "/workflow-settings", "pkgscolo").json()
        stages = {name: {"mode": value["mode"],
                         "source_ids": value["source_ids"]}
                  for name, value in settings["stages"].items()}
        body = {"expected_version": settings["version"], "stages": stages,
                "evidence_required_field_ids":
                    settings["evidence_required_field_ids"]}
        with mock.patch.object(
                self.rwa.coding_evidence, "field_rows",
                side_effect=sqlite3.DatabaseError("file is not a database")):
            resp = self.client.put(
                f"/api/tasks/{task}/workflow-settings", json=body,
                headers=self._auth_setup("pkgscolo"))
        self.assertEqual(resp.status_code, 503, resp.text)
        self.assertEqual(resp.json()["detail"],
                         main_mod.SCREENER_LIBRARY_UNAVAILABLE_MESSAGE)

    # ------------------------------------------------------------------
    # I4 站点抽验：skip_order actor 库坏字节 → 503（直调内核）
    # ------------------------------------------------------------------
    def test_i4_skip_order_corrupt_actor_db_is_503(self):
        from fastapi import HTTPException
        from custom_backend import main as main_mod
        task = self.task_id["solo"]
        runtime = self.rwa.WorkflowRuntime(
            self.main_mod._task_or_404, self.main_mod._screener_db,
            self.main_mod.DATA_DIR, lambda info, u: True)
        ctx = runtime.context(task, self._user("pkgscolo"))
        bad = self.main_mod.DATA_DIR / f"pkgil-skip-{uuid.uuid4().hex[:8]}.db"
        bad.write_bytes(b"this is not a sqlite database at all........")
        self.addCleanup(bad.unlink, True)
        ctx.actor_db = bad
        with self.assertRaises(HTTPException) as raised:
            runtime.skip_order(ctx, ctx.info.task_id, ctx.user, "screen")
        self.assertEqual(raised.exception.status_code, 503)
        self.assertEqual(raised.exception.detail,
                         main_mod.SCREENER_LIBRARY_UNAVAILABLE_MESSAGE)

    # ------------------------------------------------------------------
    # F1（复审）：schema_guard 的 notify 必须仍在 if changed: 内
    # ------------------------------------------------------------------
    def test_f1_unchanged_scheme_save_emits_no_notify(self):
        """方案未变更的保存 → 零 notify（SSE 零广播）；变更 → 恰一次。

        红侧：底版模块（before 514bb645）在包 I 重构中把 notify 移出了
        ``if changed:``——同一 no-change 保存会广播一次 coding-scheme。
        """
        tasks = importlib.import_module("coscreen.tasks")
        wf = importlib.import_module("coscreen.review_workflow")
        coding = importlib.import_module("coscreen.coding")
        task = self.client.post(
            "/api/tasks", json={"name": "pkgl-f1-" + uuid.uuid4().hex[:8]},
            headers=self._auth_setup("pkgscolo")).json()["task_id"]
        info = tasks.find_task(task, self.main_mod.DATA_DIR)
        db = info.dir / (
            tasks.slugify_screener(self.users["pkgscolo"]["username"]) + ".db")
        wf.migrate(db)
        user = self._user("pkgscolo")
        runtime = self.rwa.WorkflowRuntime(
            self.main_mod._task_or_404, self.main_mod._screener_db,
            self.main_mod.DATA_DIR, lambda info, u: True)
        # 绿：no-change 保存零广播。
        with mock.patch.object(runtime, "notify") as notify_spy:
            with runtime.schema_guard(task, user, db):
                pass  # 未改方案
            self.assertEqual(notify_spy.call_count, 0,
                             "方案未变更的保存不得广播 coding-scheme（F1）")
        # 正控：真实变更后恰一次广播（证明 spy 与 changed 判定都活着）。
        with mock.patch.object(runtime, "notify") as notify_spy:
            with runtime.schema_guard(task, user, db):
                coding.add_dimension(db, "PkgI F1 维度", "text", [])
            self.assertEqual(notify_spy.call_count, 1,
                             "方案变更必须广播一次 coding-scheme（正控）")
            self.assertEqual(notify_spy.call_args.args[1], "coding-scheme")
        # 红：包 I 中间态（notify 被移出 if changed:，sha256 5031490b）同一
        # no-change 保存 → 无条件 notify 一次（F1 回归实证）。
        buggy = importlib.import_module(
            "custom_backend.review_workflow_api_pkgI_buggy")
        base_runtime = self._runtime_for(buggy)
        with mock.patch.object(base_runtime, "notify") as base_spy:
            with base_runtime.schema_guard(task, user, db):
                pass
            self.assertEqual(base_spy.call_count, 1,
                             "底版应复现 F1（notify 被移出 if changed:）")


if __name__ == "__main__":
    unittest.main()
