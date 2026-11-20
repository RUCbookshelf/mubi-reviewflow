# -*- coding: utf-8 -*-
"""Package B, Gap 2 (P24 R-a closeout): workspace_undo_api's three remaining
entrypoints switch to main.py ``_member_mutate_notified``.

P24 修轮 D1 landed values-batch; the same file's evidence uploads /
submission-evidence / restore kept the bare ``member_mutate`` (unknown
envelope swallowed as null-200, no SSE, no S3 envelope headers — disclosed in
the P24 REPORT as a follow-up).  This package switches all three and pins,
per entrypoint, over real HTTP:

- success -> 200 + envelope headers (X-Request-Id / X-Receipt-Id) + one
  postcommit ``task-workflow-changed`` SSE per active-roster participant;
- unknown mutate envelope (stubbed) -> 409 ``team_flow_unknown_outcome``
  (never a null 200) + zero SSE events.

Real team (P16/P18 recipe, zero mocking of guards/producers; only
``runtime.mutate`` is stubbed for the unknown probes and ``BUS.publish`` is
spied for event accounting).
"""
from __future__ import annotations

import importlib
import json
import sqlite3
import struct
import unittest
import uuid
import zlib
from contextlib import closing
from unittest import mock


def _tiny_png() -> bytes:
    """1x1 white PNG (real, decodable — upload validation decodes for real)."""
    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    idat = zlib.compress(b"\x00\xff\xff\xff")
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", idat) + chunk(b"IEND", b""))

_UPSERT_LIBRARY = (
    "INSERT INTO team_vote_revision_meta (singleton, format_version,"
    " library_id, seeded_stage1, seeded_stage2, advances)"
    " VALUES (1, 2, ?, 0, 0, 0) ON CONFLICT(singleton) DO UPDATE SET"
    " library_id=excluded.library_id")
_INSERT_ARTICLE = (
    "INSERT INTO articles (zotero_key, title, is_duplicate_of, import_order)"
    " VALUES (?, ?, NULL, ?)")
_LATCH = "UPDATE workflow_state SET team_latched=1 WHERE singleton=1"

ITEM = "PBGMB"    # chain-open key (stage1+stage2 completed by both members)
RST1 = "PBGMR"    # restore probe key (pkg2s stage1 vote, not completed)

PNG = _tiny_png()


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


def _unknown_envelope(probe: str) -> dict:
    return {"outcome": "unknown", "idempotent": False,
            "request_id": probe, "result": None, "receipt": None,
            "seals": (), "cleanup_errors": (),
            "error": "probe: window exit failed"}


class NotifiedEntrypointTests(unittest.TestCase):
    """evidence uploads / submission-evidence / restore: envelope-aware."""

    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        from custom_backend import auth as auth_mod
        from custom_backend import main as main_mod

        cls.client = TestClient(main_mod.app)
        cls.client.__enter__()
        cls.main_mod = main_mod
        cls.users = {}
        for name in ("pkg2o", "pkg2s", "pkg2c"):
            user = auth_mod.create_user(name, "PkgB-Gap2-2026")
            cls.users[name] = {
                "user_id": user["id"], "username": user["username"],
                "token": auth_mod.create_access_token(
                    user["id"], user["username"])}
        cls._build_team()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    @classmethod
    def _auth(cls, name):
        return {"Authorization": "Bearer " + cls.users[name]["token"]}

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
        coding = importlib.import_module("coscreen.coding")

        owner = cls.users["pkg2o"]
        owner_uid = owner["user_id"]
        cls.task_id = cls.client.post(
            "/api/tasks",
            json={"name": "pkgb-gap2-" + uuid.uuid4().hex[:8]},
            headers=cls._auth("pkg2o")).json()["task_id"]
        info = tasks.find_task(cls.task_id, cls.main_mod.DATA_DIR)
        task_dir = info.dir
        cls.collab_path = collab.shared_store_path(task_dir)
        if not cls.collab_path.is_file():
            collab.initialize_task_store(
                task_dir, task_id=cls.task_id, owner_user_id=owner_uid,
                owner_username=owner["username"])
        collab.set_business_role(
            cls.collab_path, actor_user_id=owner_uid, user_id=owner_uid,
            business_role="reconciler", task_id=cls.task_id)
        slug = tasks.slugify_screener
        cls.user_db = {"pkg2o": task_dir / f"{slug(owner['username'])}.db"}
        cls.user_db.update({
            name: task_dir / f"{slug(cls.users[name]['username'])}.db"
            for name in ("pkg2s", "pkg2c")})
        for name in ("pkg2s", "pkg2c"):
            uid = cls.users[name]["user_id"]
            collab.grant_role(cls.collab_path, actor_user_id=owner_uid,
                              user_id=uid, role="coder", task_id=cls.task_id)
            collab.set_business_role(
                cls.collab_path, actor_user_id=owner_uid, user_id=uid,
                business_role="coder", task_id=cls.task_id)
        articles = [(k, f"PKG2 {k}", i)
                    for i, k in enumerate((ITEM, RST1))]
        for name, path in cls.user_db.items():
            cdb.init_db(path)
            wf.migrate(path)
            tf.prepare_team_terminal_facts(path)
            receipts.prepare_team_flow_receipts(path)
            coding_evidence.ensure_fields(path)
            conn = sqlite3.connect(str(path), timeout=15)
            try:
                conn.execute("PRAGMA foreign_keys=ON")
                uid = cls.users[name]["user_id"]
                if name != "pkg2o":
                    conn.execute(_UPSERT_LIBRARY, ("%032x" % uid,))
                conn.executemany(_INSERT_ARTICLE, articles)
                if name == "pkg2o":
                    conn.execute(_LATCH)
                conn.commit()
            finally:
                conn.close()
        cls.dim_id = None
        # the dimension must exist in every library: the actor's (upload
        # target) AND the owner's (canonical_only validates against the
        # owner's canonical coding scheme).
        dim_ids = set()
        for name in ("pkg2o", "pkg2s", "pkg2c"):
            dim_ids.add(coding.add_dimension(
                cls.user_db[name], "PKG2 证据维度", "text", []))
        assert len(dim_ids) == 1, f"dimension ids diverged: {dim_ids}"
        cls.dim_id = dim_ids.pop()
        tr.prepare_team_terminal_registry(cls.collab_path)
        with closing(sqlite3.connect(str(cls.collab_path))) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT task_instance_id FROM main.team_task_protocol"
                " LIMIT 1").fetchone()
        assert row is not None, "create_task must register the protocol row"
        cls.instance_id = row["task_instance_id"]
        tf.prepare_team_terminal_facts(cls.collab_path)
        receipts.prepare_team_flow_receipts(cls.collab_path)
        for name in ("pkg2s", "pkg2c"):
            uid = cls.users[name]["user_id"]
            library = "%032x" % uid
            token = tsl.make_source_identity(
                task_dir=task_dir, source_path=cls.user_db[name],
                source_user_id=uid, source_library_id=library)
            writer = _borrowed_writer(cls.collab_path)
            try:
                tr.register_source_on(
                    writer, task_instance_id=cls.instance_id,
                    source_user_id=uid, source_library_id=library,
                    source_path_identity=token,
                    registration_id=f"reg-pkg2-{name}")
                writer.commit()
            finally:
                _close_quietly(writer)
        # open the coding chain on ITEM (stage1+stage2 completed by both)
        for stage_route in ("decisions", "stage2/decisions"):
            for name in ("pkg2s", "pkg2c"):
                seeded = cls.client.post(
                    f"/api/tasks/{cls.task_id}/{stage_route}",
                    json={"zotero_key": ITEM, "decision": "include",
                          "reason": "", "notes": "", "tags": None},
                    headers=cls._auth(name))
                assert seeded.status_code == 200, seeded.text

    # -- helpers -----------------------------------------------------------

    def _publish_spy(self, sink: list):
        from custom_backend.events import BUS
        real_publish = BUS.publish

        def publish_spy(user_id, event):
            sink.append({"user_id": user_id, "event": dict(event)})
            return real_publish(user_id, event)
        return mock.patch.object(self.main_mod.BUS, "publish", publish_spy)

    def _workflow_events(self, sink):
        return [s["event"] for s in sink
                if s["event"].get("type") == "task-workflow-changed"]

    def _assert_success_envelope(self, resp, stage, key):
        self.assertEqual(resp.status_code, 200, resp.text[:400])
        self.assertIn("X-Request-Id", resp.headers)
        self.assertIn("X-Receipt-Id", resp.headers)
        return resp

    def _assert_unknown(self, resp, sink_before, sink_now):
        self.assertEqual(resp.status_code, 409, resp.text[:400])
        self.assertEqual(resp.json()["detail"]["code"],
                         "team_flow_unknown_outcome")
        self.assertEqual(
            len(self._workflow_events(sink_now))
            - len(self._workflow_events(sink_before)), 0)

    # -- 1. evidence uploads ------------------------------------------------

    def test_upload_evidence_envelope_and_unknown(self):
        sink: list = []
        with self._publish_spy(sink):
            before = len(self._workflow_events(sink))
            resp = self.client.post(
                f"/api/tasks/{self.task_id}/coding/evidence/uploads",
                data={"zotero_key": ITEM, "dim_id": str(self.dim_id)},
                files={"file": ("pkgb.png", PNG, "image/png")},
                headers=self._auth("pkg2s"))
            self._assert_success_envelope(resp, "coding", ITEM)
            events = self._workflow_events(sink)[before:]
            self.assertEqual(len(events), 3)   # narrow active roster: 3
            self.assertTrue(all(e["stage"] == "coding" for e in events))
            self.assertTrue(all(e["item_keys"] == [ITEM] for e in events))
            self.assertTrue(all(e["change_kind"] == "vote" for e in events))
            # unknown envelope: 409 team_flow_unknown_outcome, zero events.
            runtime = self.main_mod.app.state.review_workflow
            unknown_before = len(self._workflow_events(sink))
            with mock.patch.object(
                    runtime, "mutate", create=True,
                    return_value=_unknown_envelope("pkgb-upl-unknown")):
                resp409 = self.client.post(
                    f"/api/tasks/{self.task_id}/coding/evidence/uploads",
                    data={"zotero_key": ITEM, "dim_id": str(self.dim_id)},
                    files={"file": ("pkgb2.png", PNG, "image/png")},
                    headers=self._auth("pkg2s"))
            self.assertEqual(resp409.status_code, 409, resp409.text[:400])
            self.assertEqual(resp409.json()["detail"]["code"],
                             "team_flow_unknown_outcome")
            self.assertEqual(
                len(self._workflow_events(sink)) - unknown_before, 0)

    # -- 2. submission-evidence uploads (canonical_only upload route) -------

    def test_submission_upload_envelope_and_unknown(self):
        sink: list = []
        with self._publish_spy(sink):
            before = len(self._workflow_events(sink))
            resp = self.client.post(
                f"/api/tasks/{self.task_id}/coding/submission-evidence/uploads",
                data={"zotero_key": ITEM, "dim_id": str(self.dim_id),
                      "expected_policy_version": None},
                files={"file": ("pkgbsub.png", PNG, "image/png")},
                headers=self._auth("pkg2s"))
            self._assert_success_envelope(resp, "coding", ITEM)
            self.assertEqual(
                len(self._workflow_events(sink)) - before, 3)
            runtime = self.main_mod.app.state.review_workflow
            unknown_before = len(self._workflow_events(sink))
            with mock.patch.object(
                    runtime, "mutate", create=True,
                    return_value=_unknown_envelope("pkgb-sub-upl-unknown")):
                resp409 = self.client.post(
                    f"/api/tasks/{self.task_id}/coding/"
                    f"submission-evidence/uploads",
                    data={"zotero_key": ITEM, "dim_id": str(self.dim_id),
                          "expected_policy_version": None},
                    files={"file": ("pkgbsub2.png", PNG, "image/png")},
                    headers=self._auth("pkg2s"))
            self.assertEqual(resp409.status_code, 409, resp409.text[:400])
            self.assertEqual(resp409.json()["detail"]["code"],
                             "team_flow_unknown_outcome")
            self.assertEqual(
                len(self._workflow_events(sink)) - unknown_before, 0)

    # -- 3. submission-evidence save ----------------------------------------

    def test_submission_evidence_envelope_and_unknown(self):
        sink: list = []
        with self._publish_spy(sink):
            before = len(self._workflow_events(sink))
            resp = self.client.post(
                f"/api/tasks/{self.task_id}/coding/submission-evidence",
                json={"zotero_key": ITEM,
                      "notes": {str(self.dim_id): "pkgb submission note"},
                      "evidence": None,
                      "draft": False, "expected_policy_version": None},
                headers=self._auth("pkg2s"))
            self._assert_success_envelope(resp, "coding", ITEM)
            self.assertEqual(
                len(self._workflow_events(sink)) - before, 3)
            runtime = self.main_mod.app.state.review_workflow
            unknown_before = len(self._workflow_events(sink))
            with mock.patch.object(
                    runtime, "mutate", create=True,
                    return_value=_unknown_envelope("pkgb-sub-unknown")):
                resp409 = self.client.post(
                    f"/api/tasks/{self.task_id}/coding/submission-evidence",
                    json={"zotero_key": ITEM,
                          "notes": {str(self.dim_id): "pkgb unknown probe"},
                          "evidence": None,
                          "draft": False, "expected_policy_version": None},
                    headers=self._auth("pkg2s"))
            self.assertEqual(resp409.status_code, 409, resp409.text[:400])
            self.assertEqual(resp409.json()["detail"]["code"],
                             "team_flow_unknown_outcome")
            self.assertEqual(
                len(self._workflow_events(sink)) - unknown_before, 0)

    # -- 4. restore (member_undo) -------------------------------------------

    def test_restore_envelope_and_unknown(self):
        workspace_undo = importlib.import_module("coscreen.workspace_undo")
        db = self.user_db["pkg2s"]
        actor = str(self.users["pkg2s"]["user_id"])
        # seed a real undo journal entry for pkg2s's RST1 stage1 vote:
        # before = pre-vote snapshot, after = post-vote snapshot.
        with closing(sqlite3.connect(str(db))) as conn:
            before = workspace_undo.snapshot(conn, "screen", RST1)
        seeded = self.client.post(
            f"/api/tasks/{self.task_id}/decisions",
            json={"zotero_key": RST1, "decision": "include",
                  "reason": "", "notes": "", "tags": None},
            headers=self._auth("pkg2s"))
        self.assertEqual(seeded.status_code, 200, seeded.text[:400])
        with closing(sqlite3.connect(str(db))) as conn:
            workspace_undo.begin_save(conn)
            after = workspace_undo.snapshot(conn, "screen", RST1)
            cur = conn.execute(
                "INSERT INTO workspace_undo "
                "(task_id, actor_id, stage, zotero_key, before_json,"
                " after_json, undone) VALUES (?,?,?,?,?,?,0)",
                (self.task_id, actor, "screen", RST1,
                 json.dumps(before), json.dumps(after)))
            entry_id = cur.lastrowid
            conn.commit()
        sink: list = []
        with self._publish_spy(sink):
            events_before = len(self._workflow_events(sink))
            resp = self.client.post(
                f"/api/tasks/{self.task_id}/undo/screen",
                json={"entry_id": entry_id, "expected_policy_version": None},
                headers=self._auth("pkg2s"))
            self._assert_success_envelope(resp, "screen", RST1)
            events = self._workflow_events(sink)[events_before:]
            self.assertEqual(len(events), 3)
            self.assertTrue(all(e["stage"] == "screen" for e in events))
            self.assertTrue(all(e["change_kind"] == "vote" for e in events))
            # the vote really was restored (undo took effect)
            with closing(sqlite3.connect(str(db))) as conn:
                self.assertIsNone(conn.execute(
                    "SELECT decision FROM decisions WHERE zotero_key=?",
                    (RST1,)).fetchone())
            # unknown envelope: 409 + zero events (journal row re-seeded).
            with closing(sqlite3.connect(str(db))) as conn:
                workspace_undo.begin_save(conn)
                after2 = workspace_undo.snapshot(conn, "screen", RST1)
                cur = conn.execute(
                    "INSERT INTO workspace_undo "
                    "(task_id, actor_id, stage, zotero_key, before_json,"
                    " after_json, undone) VALUES (?,?,?,?,?,?,0)",
                    (self.task_id, actor, "screen", RST1,
                     json.dumps(before), json.dumps(after2)))
                entry_id2 = cur.lastrowid
                conn.commit()
            runtime = self.main_mod.app.state.review_workflow
            unknown_before = len(self._workflow_events(sink))
            with mock.patch.object(
                    runtime, "mutate", create=True,
                    return_value=_unknown_envelope("pkgb-rst-unknown")):
                resp409 = self.client.post(
                    f"/api/tasks/{self.task_id}/undo/screen",
                    json={"entry_id": entry_id2,
                          "expected_policy_version": None},
                    headers=self._auth("pkg2s"))
            self.assertEqual(resp409.status_code, 409, resp409.text[:400])
            self.assertEqual(resp409.json()["detail"]["code"],
                             "team_flow_unknown_outcome")
            self.assertEqual(
                len(self._workflow_events(sink)) - unknown_before, 0)
