# -*- coding: utf-8 -*-
"""包 H — 2③ 窄缝隙（team_keyed_inputs Phase 1/2 键域断裂）红绿证明。

缝隙（C 复审定因，e04b 同法可复现）：
    Phase 1 命中 collab F1 的键不入 pending → Phase 2 holder 扫描只覆盖
    pending。若某成员库对同一 (stage, key) 持**异四元组** seal（协议外直
    写 DB 才能造：同库异内容被 TerminalFactsConflict 拒、归档写入 collab
    的是同四元组幂等副本、B4 堵死观察伪造），发现层不报冲突，该键按
    collab 权威域直接 FINAL 放行——读写两侧都不 fail-closed。

修法（C 复审建议的最小修）：
    Phase 2 扫描目标集 `pending` → `pending ∪ collab 已见证目标`。四字段同
    seal＝幂等（归档副本），异四元组＝conflicting_seals 按键 fail-closed，
    与既有 P22 读侧"冲突即拒"口径同。

红绿判据（同一测试文件、同一夹具，只换被测模块字节）：
    - RED（pristine f19a4ead）：plan 级 —— collab 见证在场、无 issue
      （FINAL 放行）；HTTP 读侧 item 进 items_by_key 且 completion.persisted
      True；HTTP 写侧 stage2 写 200 落库。
    - GREEN（修后）：plan 级 conflicting_seals issue、无 witness；读侧
      item_errors[KEY_GAP].reason == conflicting_seals；写侧 409 + 零业务行。
    - 幂等对照键 KEY_IDEM：collab 副本 + holder 同四元组 seal 恒为见证
      （修复不得误伤归档/双持有）。

夹具（零 mock，e04a/e04b/e04c 同配方）：真实建队 + 真实 HTTP，pre-join
stage1 由 A+B 全票完成（seal 落最后投票人 B 的成员库），B 的 seal 以
archive_seals_on 归档进 collab（= e04c 退休归档语义），再以 e04b 手法向
A 的成员库直写异四元组 stage1 seal。
"""
from __future__ import annotations

import importlib
import json
import os
import sqlite3
import unittest
import uuid
from contextlib import closing
from pathlib import Path

_UPSERT_LIBRARY = (
    "INSERT INTO team_vote_revision_meta (singleton, format_version,"
    " library_id, seeded_stage1, seeded_stage2, advances)"
    " VALUES (1, 2, ?, 0, 0, 0) ON CONFLICT(singleton) DO UPDATE SET"
    " library_id=excluded.library_id")
_INSERT_ARTICLE = (
    "INSERT INTO articles (zotero_key, title, is_duplicate_of, import_order)"
    " VALUES (?, ?, NULL, ?)")
_LATCH = "UPDATE workflow_state SET team_latched=1 WHERE singleton=1"

KEY_GAP = "H2GAP1"       # 冲突目标：collab 副本 + A 库异四元组 seal
KEY_IDEM = "H2IDM1"      # 幂等对照：collab 副本 + A/B 库同四元组 seal


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


class CollabWitnessVsHolderSealTests(unittest.TestCase):
    """一个真实团队；用例按名称序执行（先 plan，后读侧，最后写侧）。"""

    task_id = None
    collab_path = None
    instance_id = None
    user_db = {}

    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        from custom_backend import auth as auth_mod
        from custom_backend import main as main_mod

        cls.client = TestClient(main_mod.app)
        cls.client.__enter__()
        cls.main_mod = main_mod
        cls.users = {}
        for name in ("h2owner", "h2a", "h2b", "h2d"):
            user = auth_mod.create_user(name, "H2-Gap-2026")
            cls.users[name] = {
                "user_id": user["id"], "username": user["username"],
                "token": auth_mod.create_access_token(
                    user["id"], user["username"])}
        cls._build_team()
        # pre-join stage1：A+B 全票 include（seal 落最后投票人 B 的库）
        for key in (KEY_GAP, KEY_IDEM):
            cls._vote_setup("h2a", key)
            cls._vote_setup("h2b", key)
        # 归档 B 的两枚 stage1 seal 进 collab（四字段保留＝e04c 语义）
        for key in (KEY_GAP, KEY_IDEM):
            cls._archive_stage1_seal_into_collab(key)
        # e04b 手法：向 A 的成员库直写异四元组 stage1 seal（仅 KEY_GAP）
        cls._inject_conflicting_holder_seal(KEY_GAP)
        # D 在 pre-join seal 之后加入（invite + business-role）
        with cls._server_mode_setup():
            resp = cls.client.post(
                f"/api/tasks/{cls.task_id}/collaboration/invite",
                json={"username": cls.users["h2d"]["username"],
                      "role": "coder"},
                headers=cls._auth_setup("h2owner"))
            assert resp.status_code == 201, resp.text
            resp = cls.client.put(
                f"/api/tasks/{cls.task_id}/collaboration/business-role/"
                f"{cls.users['h2d']['user_id']}",
                json={"business_role": "coder"},
                headers=cls._auth_setup("h2owner"))
            assert resp.status_code == 200, resp.text

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    # ------------------------------------------------------------------
    # fixture helpers（high1 E 场景 / P25 矩阵配方）
    # ------------------------------------------------------------------
    @classmethod
    def _auth_setup(cls, name):
        return {"Authorization": "Bearer " + cls.users[name]["token"]}

    @classmethod
    def _server_mode_setup(cls):
        saved = os.environ.get("REVIEWFLOW_MODE")

        def restore():
            if saved is None:
                os.environ.pop("REVIEWFLOW_MODE", None)
            else:
                os.environ["REVIEWFLOW_MODE"] = saved
        return _EnvGuard("server", restore)

    @classmethod
    def _build_team(cls):
        collab = importlib.import_module("coscreen.task_collaboration")
        cdb = importlib.import_module("coscreen.db")
        wf = importlib.import_module("coscreen.review_workflow")
        tasks = importlib.import_module("coscreen.tasks")
        tf = importlib.import_module("coscreen.team_terminal_facts")
        tr = importlib.import_module("coscreen.team_terminal_registry")
        receipts = importlib.import_module("coscreen.team_flow_receipts")
        tsl = importlib.import_module("custom_backend.team_source_lifecycle")
        coding_evidence = importlib.import_module("coscreen.coding_evidence")

        owner = cls.users["h2owner"]
        owner_uid = owner["user_id"]
        cls.task_id = cls.client.post(
            "/api/tasks", json={"name": "h2-gap-" + uuid.uuid4().hex[:8]},
            headers=cls._auth_setup("h2owner")).json()["task_id"]
        info = tasks.find_task(cls.task_id, cls.main_mod.DATA_DIR)
        task_dir = info.dir
        collab_path = collab.shared_store_path(task_dir)
        if not collab_path.is_file():
            collab.initialize_task_store(
                task_dir, task_id=cls.task_id, owner_user_id=owner_uid,
                owner_username=owner["username"])
        collab.set_business_role(
            collab_path, actor_user_id=owner_uid, user_id=owner_uid,
            business_role="reconciler", task_id=cls.task_id)
        slug = tasks.slugify_screener
        member_names = ("h2a", "h2b", "h2d")
        cls.user_db = {
            "h2owner": task_dir / f"{slug(owner['username'])}.db"}
        cls.user_db.update({
            name: task_dir / f"{slug(cls.users[name]['username'])}.db"
            for name in member_names})
        for name in ("h2a", "h2b"):
            uid = cls.users[name]["user_id"]
            collab.grant_role(collab_path, actor_user_id=owner_uid,
                              user_id=uid, role="coder", task_id=cls.task_id)
            collab.set_business_role(
                collab_path, actor_user_id=owner_uid, user_id=uid,
                business_role="coder", task_id=cls.task_id)
        articles = [(k, f"H2 {k}", i) for i, k in enumerate(
            (KEY_GAP, KEY_IDEM))]
        for name, path in cls.user_db.items():
            cdb.init_db(path)
            wf.migrate(path)
            tf.prepare_team_terminal_facts(path)
            receipts.prepare_team_flow_receipts(path)
            coding_evidence.ensure_fields(path)
            conn = sqlite3.connect(str(path), timeout=15)
            try:
                conn.execute("PRAGMA foreign_keys=ON")
                if name != "h2owner":
                    conn.execute(_UPSERT_LIBRARY, (
                        "%032x" % cls.users[name]["user_id"],))
                conn.executemany(_INSERT_ARTICLE, articles)
                if name == "h2owner":
                    conn.execute(_LATCH)
                conn.commit()
            finally:
                conn.close()
        tr.prepare_team_terminal_registry(collab_path)
        importlib.import_module(
            "coscreen.screening_arbitration"
        ).prepare_arbitration_attestation_storage(collab_path)
        importlib.import_module(
            "coscreen.arbitration_session"
        ).prepare_team_session_storage(str(collab_path))
        with closing(sqlite3.connect(str(collab_path))) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT task_instance_id FROM main.team_task_protocol"
                " LIMIT 1").fetchone()
        assert row is not None, "team protocol row missing (P18)"
        cls.instance_id = row["task_instance_id"]
        tf.prepare_team_terminal_facts(collab_path)
        receipts.prepare_team_flow_receipts(collab_path)
        for name in ("h2a", "h2b"):
            uid = cls.users[name]["user_id"]
            library = "%032x" % uid
            token = tsl.make_source_identity(
                task_dir=task_dir, source_path=cls.user_db[name],
                source_user_id=uid, source_library_id=library)
            writer = _borrowed_writer(collab_path)
            try:
                tr.register_source_on(
                    writer, task_instance_id=cls.instance_id,
                    source_user_id=uid, source_library_id=library,
                    source_path_identity=token,
                    registration_id=f"reg-h2-{name}")
                writer.commit()
            finally:
                _close_quietly(writer)
        cls.collab_path = collab_path

    @classmethod
    def _vote_setup(cls, name, key):
        resp = cls.client.post(
            f"/api/tasks/{cls.task_id}/decisions",
            json={"zotero_key": key, "decision": "include",
                  "reason": "", "notes": "", "tags": None},
            headers=cls._auth_setup(name))
        assert resp.status_code == 200, (name, key, resp.text)

    @classmethod
    def _stage1_records(cls, path, key):
        tf = importlib.import_module("coscreen.team_terminal_facts")
        if not Path(path).is_file():
            return []
        conn = sqlite3.connect(str(path), timeout=15)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("BEGIN")
        except sqlite3.Error:
            conn.close()
            return []
        try:
            records = list(tf.list_seals_on(
                conn, task_instance_id=cls.instance_id, stage="stage1"))
        finally:
            try:
                conn.rollback()
            finally:
                conn.close()
        out = []
        for record in records:
            payload = record["payload"]
            if isinstance(payload, str):
                payload = json.loads(payload)
            if payload.get("item_key") == key:
                out.append(record)
        return out

    @classmethod
    def _archive_stage1_seal_into_collab(cls, key):
        """B 库 stage1 seal → collab（archive_seals_on，四字段保留）。"""
        tf = importlib.import_module("coscreen.team_terminal_facts")
        records = cls._stage1_records(cls.user_db["h2b"], key)
        assert len(records) == 1, (key, records)
        writer = _borrowed_writer(cls.collab_path)
        try:
            archived = tf.archive_seals_on(writer, records)
            writer.commit()
        finally:
            _close_quietly(writer)
        assert len(archived) == 1, (key, archived)
        return archived[0]

    @classmethod
    def _inject_conflicting_holder_seal(cls, key):
        """e04b 手法：向 A 的成员库直写异四元组 stage1 seal。

        异四元组来源＝不同 sealed_at + 不同 source_request 身份（同库同键
        第二枚在协议内会被 TerminalFactsConflict 拒，故必须直写 DB）。
        """
        tf = importlib.import_module("coscreen.team_terminal_facts")
        records = cls._stage1_records(cls.user_db["h2b"], key)
        assert len(records) == 1
        payload = json.loads(json.dumps(records[0]["payload"]))
        payload["sealed_at"] = "2026-11-01T00:00:00.000Z"
        payload["source_request"]["request_id"] = str(uuid.uuid4())
        payload["source_request"]["request_sha256"] = "1" * 64
        writer = _borrowed_writer(cls.user_db["h2a"])
        try:
            injected = tf.append_seal_on(writer, payload, origin="actor")
            writer.commit()
        finally:
            _close_quietly(writer)
        assert injected["seal_id"] != records[0]["seal_id"]
        return injected

    # ------------------------------------------------------------------
    # per-test helpers
    # ------------------------------------------------------------------
    def _auth(self, name):
        return {"Authorization": "Bearer " + self.users[name]["token"]}

    def _keyed_view(self, stage, keys, name):
        resp = self.client.post(
            f"/api/tasks/{self.task_id}/work-items/{stage}/keyed",
            json={"zotero_keys": list(keys)}, headers=self._auth(name))
        self.assertEqual(resp.status_code, 200, resp.text)
        return resp.json()

    def _decision_row(self, name, key, stage="fulltext"):
        table = "decisions" if stage == "screen" else "stage2_decisions"
        with closing(sqlite3.connect(str(self.user_db[name]))) as conn:
            return conn.execute(
                f"SELECT decision FROM {table} WHERE zotero_key=?",
                (key,)).fetchone()

    def _error_code(self, resp):
        detail = resp.json().get("detail")
        if isinstance(detail, dict):
            return detail.get("error_code") or detail.get("code") or ""
        return detail or ""

    # ------------------------------------------------------------------
    # H1 plan 级：collab 已见证键 + 异四元组 holder seal
    # ------------------------------------------------------------------
    def test_h1_plan_collab_witness_holder_conflict(self):
        from coscreen.team_flow_types import TeamOperation
        from custom_backend.team_keyed_inputs import build_keyed_read_plan
        ctx = self.main_mod.app.state.review_workflow.readonly_context(
            self.task_id, self.users["h2a"])
        self.assertEqual(ctx.info.task_id, self.task_id)
        conn = _borrowed_writer(self.collab_path)
        try:
            plan = build_keyed_read_plan(
                ctx, collab_conn=conn,
                operation=TeamOperation(name="h2-gap", stage="fulltext"),
                item_keys=[KEY_GAP, KEY_IDEM])
        finally:
            _close_quietly(conn)
        # 红侧诊断锚（-s 可见）：修复前 issues 不含关键、witnesses 含 collab
        # 见证（FINAL 放行）；修后相反。
        print("H2-PLAN witnesses=%r issues=%r" % (
            sorted((s, k, w.seal_id[:8]) for (s, k), w in
                   plan.witnesses.items()),
            sorted((t, i.reason) for t, i in plan.issues.items())))
        # 冲突键：collab 见证被撤销、按键 conflicting_seals fail-closed
        issue = plan.issues.get(("stage1", KEY_GAP))
        self.assertIsNotNone(
            issue,
            "H/2③: collab 已见证键上的异四元组 holder seal 必须呈报 issue"
            "（修复前此处为 None＝FINAL 放行）")
        self.assertEqual(issue.reason, "conflicting_seals")
        self.assertEqual(issue.source_role, "actor")
        self.assertFalse(issue.retryable)
        self.assertNotIn(("stage1", KEY_GAP), plan.witnesses,
                         "H/2③: 冲突键不得保留 collab 见证")
        # 幂等对照键：collab 副本 + holder 同四元组 seal → 见证保留
        witness_idem = plan.witnesses.get(("stage1", KEY_IDEM))
        self.assertIsNotNone(witness_idem,
                             "归档副本（同四字段）不得被误判冲突")
        self.assertIsNone(witness_idem.source_path,
                          "幂等键保留 collab 权威域归属")
        self.assertNotIn(("stage1", KEY_IDEM), plan.issues)
        # stage2 仍为干净负发现（冲突只锁冲突键本身）
        self.assertNotIn(("stage2", KEY_GAP), plan.issues)
        self.assertNotIn(("stage2", KEY_GAP), plan.witnesses)

    # ------------------------------------------------------------------
    # H2 读侧：keyed view 按键 error（冲突不连坐其他键）
    # ------------------------------------------------------------------
    def test_h2_read_side_conflict_is_key_level_error(self):
        view = self._keyed_view("fulltext", [KEY_GAP, KEY_IDEM], "h2d")
        self.assertNotIn(
            KEY_GAP, view["items_by_key"],
            "修复前：冲突键按 collab 权威域进 items_by_key（FINAL 放行）")
        error = view["item_errors"].get(KEY_GAP)
        self.assertIsNotNone(error, view)
        self.assertEqual(error["reason"], "conflicting_seals", view)
        # 幂等对照键照常为正常 item（归档副本不受影响；stage2 尚未投票故
        # chain completion 投影可为 None——本用例只钉"不被误判为冲突"）
        item = view["items_by_key"].get(KEY_IDEM)
        self.assertIsNotNone(item, view)
        self.assertNotIn(KEY_IDEM, view["item_errors"], view)
        self.assertEqual(item["reason"], None, item)

    # ------------------------------------------------------------------
    # H3 写侧：stage2 写门按键 409，零业务行
    # ------------------------------------------------------------------
    def test_h3_write_gate_conflict_409_zero_write(self):
        blocked = self.client.post(
            f"/api/tasks/{self.task_id}/stage2/decisions",
            json={"zotero_key": KEY_GAP, "decision": "include",
                  "reason": "", "notes": "", "tags": None},
            headers=self._auth("h2d"))
        self.assertEqual(
            blocked.status_code, 409,
            "修复前：collab 权威域见证放行 stage2 写（200）\n"
            + blocked.text)
        self.assertEqual(self._error_code(blocked), "conflicting_seals",
                         blocked.text)
        for name in ("h2a", "h2b", "h2d"):
            self.assertIsNone(self._decision_row(name, KEY_GAP), name)


if __name__ == "__main__":
    unittest.main()
