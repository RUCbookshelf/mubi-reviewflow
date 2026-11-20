"""P16 member write routes tests (C12 author side; 2.1.6 team contracts P16).

Five P16-named tests over the **real** app routes: real HTTP via TestClient,
real team building (移植 test_216_keyed_write_window.py=6b8239fd 的真实建队
配方：业务角色单选/holder 库 P04 ledger/F1 facts/F2 registry+真 source
token——零 mock、零窗口 patch), real P10 window/P13 commit protocol, and
real business writers (``db.save_decision``/``save_stage2``/
``coding_evidence.save_form``/``stage_upload``/``workspace_undo.undo`` all
execute).  The only wrapping is a **passthrough recorder** around this
package's own seam (``workspace_undo_api.borrowed_target_conn``) and the
module writers: the recorder delegates to the real function unchanged and
merely records arguments, so the P16 anchor

    assert writer_connection is guarded_target_connection

is observed on the genuine request path, not a stub.

Product imports are deferred to ``setUpClass`` (loadable under ``python -I -B``).
"""
from __future__ import annotations

import importlib
import sqlite3
import unittest
import uuid
from contextlib import closing
from pathlib import Path
from unittest import mock

_REQ_ID = "7f16a1b2-3c4d-4e5f-8a90-bcdef0123456"  # app 全局 X-Request-ID 约定：UUID
ITEM = "KEY1"
ITEM2 = "KEY2"
ITEM3 = "KEY3"
_OWNER_LIBRARY = "3c" * 16  # P18 同步版：仅历史参考；protocol 行现由
# create_task 自动登记（owner_library=确定性 uid-hex），夹具不再使用本值。
_UPSERT_LIBRARY = (
    "INSERT INTO team_vote_revision_meta (singleton, format_version,"
    " library_id, seeded_stage1, seeded_stage2, advances)"
    " VALUES (1, 2, ?, 0, 0, 0) ON CONFLICT(singleton) DO UPDATE SET"
    " library_id=excluded.library_id")
_INSERT_ARTICLE = (
    "INSERT INTO articles (zotero_key, title, is_duplicate_of, import_order)"
    " VALUES (?, ?, NULL, ?)")
_LATCH = "UPDATE workflow_state SET team_latched=1 WHERE singleton=1"
def _make_png() -> bytes:
    """Real valid 1x1 grayscale PNG (chunk CRCs computed; stage_upload checks)."""
    import struct as _struct
    import zlib as _zlib

    def _chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return (_struct.pack(">I", len(data)) + body
                + _struct.pack(">I", _zlib.crc32(body) & 0xFFFFFFFF))

    signature = bytes.fromhex("89504e470d0a1a0a")
    ihdr = _struct.pack(">IIBBBBB", 1, 1, 8, 0, 0, 0, 0)
    scanline = bytes((0, 0))
    return (signature + _chunk(b"IHDR", ihdr)
            + _chunk(b"IDAT", _zlib.compress(scanline))
            + _chunk(b"IEND", b""))


_PNG = _make_png()


def _borrowed_writer(path):
    """真实产品写连接形态（F2/F1 append 入口要求）：Row 工厂＋IMMEDIATE。"""
    conn = sqlite3.connect(str(path), timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("BEGIN IMMEDIATE")
    return conn


def _close_quietly(conn):
    try:
        conn.close()
    except Exception:
        pass


class MemberWriteRoutesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        from custom_backend import auth as auth_mod
        from custom_backend import main as main_mod
        from custom_backend import workspace_undo_api as undo_api

        cls.client = TestClient(main_mod.app)
        cls.client.__enter__()
        cls.main_mod = main_mod
        cls.undo_api = undo_api
        cls.users = {}
        for name in ("p16owner", "p16screener", "p16coder"):
            user = auth_mod.create_user(name, "P16-Member-Routes-2026")
            cls.users[name] = {
                "user_id": user["id"], "username": user["username"],
                "token": auth_mod.create_access_token(user["id"], user["username"])}
        cls._team_task()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    @classmethod
    def _auth(cls, name):
        return {"Authorization": "Bearer " + cls.users[name]["token"]}

    # ------------------------------------------------------------------
    # 真实建队（移植 P10 keyed_write_window 基座配方，零 mock）
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

        owner = cls.users["p16owner"]
        owner_uid = owner["user_id"]
        cls.task_id = cls.client.post(
            "/api/tasks", json={"name": "p16-member-" + uuid.uuid4().hex[:8]},
            headers=cls._auth("p16owner")).json()["task_id"]
        cls.info = tasks.find_task(cls.task_id, cls.main_mod.DATA_DIR)
        task_dir = cls.info.dir
        cls.collab_path = collab.shared_store_path(task_dir)
        if not cls.collab_path.is_file():
            collab.initialize_task_store(
                task_dir, task_id=cls.task_id, owner_user_id=owner_uid,
                owner_username=owner["username"])
        # 属主业务角色单选（required_screener 语义来自 set_business_role）。
        collab.set_business_role(
            cls.collab_path, actor_user_id=owner_uid, user_id=owner_uid,
            business_role="reconciler", task_id=cls.task_id)
        slug = tasks.slugify_screener
        cls.user_db = {name: task_dir / f"{slug(u['username'])}.db"
                       for name, u in cls.users.items()}
        for name in ("p16screener", "p16coder"):
            uid = cls.users[name]["user_id"]
            collab.grant_role(cls.collab_path, actor_user_id=owner_uid,
                              user_id=uid, role="coder", task_id=cls.task_id)
            collab.set_business_role(
                cls.collab_path, actor_user_id=owner_uid, user_id=uid,
                business_role="coder", task_id=cls.task_id)
        articles = [(k, f"P16 {k}", i) for i, k in enumerate((ITEM, ITEM2, ITEM3))]
        # 每库：init_db（P04 ledger 真实 meta DDL）+ migrate + F1 facts + P07
        # receipts + 确定性 library + articles（owner 另 latch——P10 配方）。
        for name, path in cls.user_db.items():
            cdb.init_db(path)
            wf.migrate(path)
            tf.prepare_team_terminal_facts(path)
            receipts.prepare_team_flow_receipts(path)
            conn = sqlite3.connect(str(path), timeout=15)
            try:
                conn.execute("PRAGMA foreign_keys=ON")
                uid = cls.users[name]["user_id"]
                if name != "p16owner":
                    # P18 S-3 同步：owner 库保留 create_task hook 安装的真实
                    # 账本身份（协议行登记的就是它）；成员库仍用确定性
                    # uid-hex 并与 source 登记一致。
                    conn.execute(_UPSERT_LIBRARY, ("%032x" % uid,))
                conn.executemany(_INSERT_ARTICLE, articles)
                if name == "p16owner":
                    conn.execute(_LATCH)
                conn.commit()
            finally:
                conn.close()
            coding_evidence.ensure_fields(path)
        # 真实编码维度（coding.add_dimension——成员私有方案；两成员库同建）。
        coding = importlib.import_module("coscreen.coding")
        cls.dim_id = None
        for name in ("p16screener", "p16coder"):
            dim_id = coding.add_dimension(
                cls.user_db[name], "P16 证据维度", "text", [])
            if cls.dim_id is None:
                cls.dim_id = dim_id
        # collab 组合库：F2 registry＋protocol 行＋F1 facts（P10 _prepare_collab）。
        # P18 同步版（main=d4d05542）：create_task 现已显式准备并登记 protocol
        # 行（trusted_new_task；owner_library=确定性 uid-hex）——夹具改为直接
        # 依赖自动登记，删手工登记（原手工行 owner_library=_OWNER_LIBRARY 与
        # 自动行绑定不同 → TerminalRegistryConflict；r3 红根因）。缺行=产品
        # hook 未跑，assertion 如实红。
        tr.prepare_team_terminal_registry(cls.collab_path)
        with closing(sqlite3.connect(str(cls.collab_path))) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT task_instance_id FROM main.team_task_protocol"
                " LIMIT 1").fetchone()
        assert row is not None, (
            "create_task must have registered the team protocol row "
            "(P18 explicit registration hook)")
        cls.instance_id = row["task_instance_id"]
        tf.prepare_team_terminal_facts(cls.collab_path)
        # 真实 source 登记（P06 真 token——P10 _register_source 同款）。
        for name in ("p16screener", "p16coder"):
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
                    registration_id=f"reg-p16-{name}")
                writer.commit()
            finally:
                _close_quietly(writer)

        # stage1 上游完成：两位必需筛选人各以真实 member_vote/screen 写入
        # ITEM 的 include（第二票触发协议补上游观察 seal——编码链由此开闸；
        # ITEM2 刻意不播，专供 undo 测试的 screen 冲突场景）。
        for name in ("p16screener", "p16coder"):
            seeded = cls.client.post(
                f"/api/tasks/{cls.task_id}/decisions",
                json={"zotero_key": ITEM, "decision": "include",
                      "reason": "", "notes": "", "tags": None},
                headers=cls._auth(name))
            assert seeded.status_code == 200, seeded.text
        # stage2 上游完成：两位必需筛选人各以真实 member_vote/fulltext 写入
        # ITEM 的 include（链：screen 完成 → fulltext 完成 → coding 开闸）。
        # 完成即封版（stage_completed_read_only）——此后测试不得再写
        # ITEM 的 screen/fulltext；可变 screen 场景一律用未播的 ITEM2。
        for name in ("p16screener", "p16coder"):
            seeded = cls.client.post(
                f"/api/tasks/{cls.task_id}/stage2/decisions",
                json={"zotero_key": ITEM, "decision": "include",
                      "reason": "", "notes": "", "tags": None},
                headers=cls._auth(name))
            assert seeded.status_code == 200, seeded.text

    # ------------------------------------------------------------------
    # shared observation: passthrough recorders (real functions still run)
    # ------------------------------------------------------------------
    @classmethod
    def _recorder(cls):
        """Wrap borrowed_target_conn + module writers with passthrough spies."""
        real_borrow = cls.undo_api.borrowed_target_conn
        seen: dict[str, object] = {}

        def borrowed_spy(ctx):
            conn = real_borrow(ctx)  # real anchor check still runs
            inputs = getattr(ctx, "write_inputs", None)
            seen["writer_connection"] = conn
            seen["guarded_target_connection"] = getattr(inputs, "target_conn", None)
            seen["inputs"] = inputs
            return conn

        # main.py 在模块导入期直接绑定 borrowed_target_conn（from-import），
        # 补丁需同时覆盖 workspace_undo_api 模块属性与 main 的绑定名。
        patchers = [mock.patch.object(cls.undo_api, "borrowed_target_conn",
                                      borrowed_spy),
                    mock.patch.object(cls.main_mod, "borrowed_target_conn",
                                      borrowed_spy)]
        return seen, patchers

    def _row(self, name: str, sql: str, args=()):
        with closing(sqlite3.connect(self.user_db[name])) as conn:
            return conn.execute(sql, args).fetchone()

    def _receipt_count(self, name: str) -> int:
        return self._row(name, "SELECT COUNT(*) FROM team_flow_receipts")[0]

    # ------------------------------------------------------------------
    # 1. direct + batch member write routes go through mutate
    # ------------------------------------------------------------------
    def test_direct_and_batch_screen_routes(self):
        headers = self._auth("p16screener")
        # direct screen decision (member_vote/screen through mutate)
        resp = self.client.post(
            f"/api/tasks/{self.task_id}/decisions",
            json={"zotero_key": ITEM2, "decision": "include",
                  "reason": "", "notes": "", "tags": None},
            headers=headers)
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertTrue(body["saved"])
        self.assertIn("progress", body)
        self.assertEqual(
            self._row("p16screener",
                      "SELECT decision FROM decisions WHERE zotero_key=?",
                      (ITEM2,))[0], "include")
        self.assertGreaterEqual(self._receipt_count("p16screener"), 1)
        # fulltext (member_vote/fulltext)——上游 stage1 需双必需筛选人完成：
        # p16coder 补第二票（真实 member_vote/screen）后 fulltext 开闸。
        second = self.client.post(
            f"/api/tasks/{self.task_id}/decisions",
            json={"zotero_key": ITEM2, "decision": "include",
                  "reason": "", "notes": "", "tags": None},
            headers=self._auth("p16coder"))
        self.assertEqual(second.status_code, 200, second.text)
        resp2 = self.client.post(
            f"/api/tasks/{self.task_id}/stage2/decisions",
            json={"zotero_key": ITEM2, "decision": "include",
                  "reason": "", "notes": "", "tags": None},
            headers=headers)
        self.assertEqual(resp2.status_code, 200, resp2.text)
        self.assertEqual(
            self._row("p16screener",
                      "SELECT decision FROM stage2_decisions WHERE zotero_key=?",
                      (ITEM2,))[0], "include")
        # batch coding save (member_coding/coding through mutate)
        resp3 = self.client.post(
            f"/api/tasks/{self.task_id}/coding/values-batch",
            json={"zotero_key": ITEM, "values": {str(self.dim_id): "yes"}, "notes": None,
                  "evidence": None, "draft": False},
            headers=self._auth("p16coder"))
        self.assertEqual(resp3.status_code, 200, resp3.text)
        self.assertEqual(
            self._row("p16coder",
                      "SELECT value FROM coding_values WHERE zotero_key=?"
                      " AND dimension_id=?", (ITEM, self.dim_id))[0], "yes")

    # ------------------------------------------------------------------
    # 2. coding/evidence writers use the retained target connection
    # ------------------------------------------------------------------
    def test_coding_and_evidence_use_retained_connections(self):
        seen, patchers = self._recorder()
        coding_evidence = importlib.import_module("coscreen.coding_evidence")
        real_save = coding_evidence.save_form
        real_upload = coding_evidence.stage_upload

        def save_spy(db, *a, **kw):
            seen["save_form_conn"] = kw.get("conn")
            return real_save(db, *a, **kw)  # real writer runs unchanged

        def upload_spy(db, *a, **kw):
            seen["stage_upload_conn"] = kw.get("conn")
            return real_upload(db, *a, **kw)

        patchers.append(mock.patch.object(coding_evidence, "save_form", save_spy))
        patchers.append(mock.patch.object(coding_evidence, "stage_upload", upload_spy))
        for p in patchers:
            p.start()
        try:
            resp = self.client.post(
                f"/api/tasks/{self.task_id}/coding/values",
                json={"zotero_key": ITEM, "dim_id": self.dim_id, "value": "cohort"},
                headers=self._auth("p16coder"))
            self.assertEqual(resp.status_code, 200, resp.text)
            # P16 anchor: the real save writer's connection IS the guarded
            # target connection of the same window.
            writer_connection = seen["save_form_conn"]
            guarded_target_connection = seen["guarded_target_connection"]
            self.assertIsNotNone(writer_connection)
            self.assertIs(writer_connection, guarded_target_connection)
            self.assertIs(writer_connection, seen["writer_connection"])
            upload = self.client.post(
                f"/api/tasks/{self.task_id}/coding/evidence/uploads",
                data={"zotero_key": ITEM, "dim_id": str(self.dim_id)},
                files={"file": ("ev.png", _PNG, "image/png")},
                headers=self._auth("p16coder"))
            self.assertEqual(upload.status_code, 200, upload.text)
            self.assertIs(seen["stage_upload_conn"],
                          seen["guarded_target_connection"])
        finally:
            for p in patchers:
                p.stop()

    # ------------------------------------------------------------------
    # 3. undo late completion conflict (409, journal untouched)
    # ------------------------------------------------------------------
    def test_undo_late_completion_conflict(self):
        headers = self._auth("p16screener")
        saved = self.client.post(
            f"/api/tasks/{self.task_id}/decisions",
            json={"zotero_key": ITEM3, "decision": "exclude",
                  "reason": "", "notes": "", "tags": None},
            headers=headers)
        self.assertEqual(saved.status_code, 200, saved.text)
        status = self.client.get(f"/api/tasks/{self.task_id}/undo/screen",
                                 headers=headers).json()
        entry_id = (status.get("latest") or {}).get("entry_id")
        self.assertIsNotNone(entry_id, status)
        # Late completion: downstream work changes after the journal snapshot —
        # a later save on the same key supersedes the entry.
        again = self.client.post(
            f"/api/tasks/{self.task_id}/decisions",
            json={"zotero_key": ITEM3, "decision": "include",
                  "reason": "", "notes": "", "tags": None},
            headers=headers)
        self.assertEqual(again.status_code, 200, again.text)
        undo = self.client.post(f"/api/tasks/{self.task_id}/undo/screen",
                                json={"entry_id": entry_id}, headers=headers)
        self.assertEqual(undo.status_code, 409, undo.text)

    # ------------------------------------------------------------------
    # 4. removed actor + screener spoof
    # ------------------------------------------------------------------
    def test_removed_actor_and_screener_spoof(self):
        # (a) screener spoof: member targets another workspace by name.
        spoof = self.client.post(
            f"/api/tasks/{self.task_id}/coding/values?screener=p16owner",
            json={"zotero_key": ITEM, "dim_id": self.dim_id, "value": "spoof"},
            headers=self._auth("p16coder"))
        self.assertEqual(spoof.status_code, 403, spoof.text)
        # (b) removed actor: revoke the coder grant; the real team window must
        # refuse (structured 403/409/503) — never a silent personal write.
        collab = importlib.import_module("coscreen.task_collaboration")
        owner_uid = self.users["p16owner"]["user_id"]
        coder_uid = self.users["p16coder"]["user_id"]
        removed = self.client.post(
            f"/api/tasks/{self.task_id}/coding/values-batch",
            json={"zotero_key": ITEM, "values": {"1": "x"}, "notes": None,
                  "evidence": None, "draft": False},
            headers=self._auth("p16coder"))
        self.assertEqual(removed.status_code, 200, removed.text)
        collab.revoke_role(self.collab_path, actor_user_id=owner_uid,
                           user_id=coder_uid, role="coder",
                           task_id=self.task_id)
        try:
            after = self.client.post(
                f"/api/tasks/{self.task_id}/coding/values-batch",
                json={"zotero_key": ITEM, "values": {str(self.dim_id): "y"},
                      "notes": None, "evidence": None, "draft": False},
                headers=self._auth("p16coder"))
            self.assertIn(after.status_code, (403, 404, 409, 503), after.text)
            row = self._row("p16coder",
                            "SELECT value FROM coding_values WHERE zotero_key=?"
                            " AND dimension_id=?", (ITEM, self.dim_id))
            self.assertEqual(row[0] if row else None, "x",
                             "removed actor write must not change saved state")
        finally:
            collab.grant_role(self.collab_path, actor_user_id=owner_uid,
                              user_id=coder_uid, role="coder",
                              task_id=self.task_id)
            collab.set_business_role(
                self.collab_path, actor_user_id=owner_uid, user_id=coder_uid,
                business_role="coder", task_id=self.task_id)

    # ------------------------------------------------------------------
    # 5. client retry (idempotent replay) + payload conflict (409)
    # ------------------------------------------------------------------
    def test_client_retry_and_payload_conflict(self):
        headers = {**self._auth("p16coder"), "X-Request-Id": _REQ_ID}
        payload = {"zotero_key": ITEM, "values": {str(self.dim_id): "retry-a"},
                   "notes": None, "evidence": None, "draft": False}
        first = self.client.post(f"/api/tasks/{self.task_id}/coding/values-batch",
                                 json=payload, headers=headers)
        self.assertEqual(first.status_code, 200, first.text)
        before = self._receipt_count("p16coder")
        # same request id + same payload: idempotent replay, no second receipt
        second = self.client.post(f"/api/tasks/{self.task_id}/coding/values-batch",
                                  json=payload, headers=headers)
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(first.json(), second.json())
        self.assertEqual(self._receipt_count("p16coder"), before)
        # same request id + different payload: receipt conflict 409
        conflict = self.client.post(
            f"/api/tasks/{self.task_id}/coding/values-batch",
            json={**payload, "values": {str(self.dim_id): "retry-b"}}, headers=headers)
        self.assertEqual(conflict.status_code, 409, conflict.text)
        self.assertEqual(self._row("p16coder",
                                   "SELECT value FROM coding_values WHERE"
                                   " zotero_key=? AND dimension_id=?",
                                   (ITEM, self.dim_id))[0], "retry-a")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
