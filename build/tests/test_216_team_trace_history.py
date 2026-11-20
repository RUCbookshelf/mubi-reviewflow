"""P21 team trace history & multi-partition clear tests (C12 author side).

Six P21-named tests over real HTTP (TestClient) with the real team-building
recipe ported from test_216_annotation_routes.py (P16/P18/P19/P20 lineage:
single business role, P04 ledger libs, F1 facts, F2 registry + true source
tokens, create_task protocol hook) — zero business mocks; the only patch is a
narrow commit-fault injection for ``research_trace.clear_history`` in
``test_commit_partial_and_explicit_retry`` (per-DB fault that cannot be
produced naturally), restored immediately.

Three real team tasks:

- T1 (arb): adjudicated stage1 attestation seeded into the real collab
  attestation tables (``prepare_arbitration_attestation_storage`` + direct
  rows consumed by the real ``adjudicated_history_keys_on`` reader);
- T2 (seal): stage1+stage2 completed on KS1 via two required screeners'
  real member votes (F1 seals, no arbitration);
- T3 (plain): no protection facts — successful clears, owner dedup,
  hardlink alias denial, off/rebuild and partial/retry live here.

Assertions anchor: a protected rejection never reports ``status='partial'``
and performs zero writes on *both* physical partitions (the first-acquired
owner/shared store included).
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
from unittest.mock import patch

_UPSERT_LIBRARY = (
    "INSERT INTO team_vote_revision_meta (singleton, format_version,"
    " library_id, seeded_stage1, seeded_stage2, advances)"
    " VALUES (1, 2, ?, 0, 0, 0) ON CONFLICT(singleton) DO UPDATE SET"
    " library_id=excluded.library_id")
_INSERT_ARTICLE = (
    "INSERT INTO articles (zotero_key, title, is_duplicate_of, import_order)"
    " VALUES (?, ?, NULL, ?)")
_LATCH = "UPDATE workflow_state SET team_latched=1 WHERE singleton=1"

#: T1: KA adjudicated (stage1), KD open.  T2: KS1 fully completed, KS2 open.
KEY_ARB = "P21A"
KEY_OPEN1 = "P21D"
KEY_SEAL = "P21S1"
KEY_SEAL2 = "P21S2"
#: T3 plain keys.
KEY_P1 = "P21P1"
KEY_P2 = "P21P2"


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


class TeamTraceHistoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        from custom_backend import auth as auth_mod
        from custom_backend import main as main_mod

        cls.client = TestClient(main_mod.app)
        cls.client.__enter__()
        cls.main_mod = main_mod
        cls.users = {}
        for name in ("p21owner", "p21s1", "p21s2", "p21m3", "p21m4"):
            user = auth_mod.create_user(name, "P21-Trace-Clear-2026")
            cls.users[name] = {
                "user_id": user["id"], "username": user["username"],
                "token": auth_mod.create_access_token(user["id"], user["username"])}
        cls._team_task("arb", ("p21owner", "p21s1", "p21s2"),
                       (KEY_ARB, KEY_OPEN1), completed=())
        cls.info_arb = cls.info
        cls._team_task("seal", ("p21owner", "p21s1", "p21s2"),
                       (KEY_SEAL, KEY_SEAL2), completed=())
        cls.info_seal = cls.info
        cls._team_task("plain", ("p21owner", "p21m3", "p21m4"),
                       (KEY_P1, KEY_P2), completed=())
        cls.info_plain = cls.info
        cls._prepare_recordings()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    @classmethod
    def _auth(cls, name):
        return {"Authorization": "Bearer " + cls.users[name]["token"]}

    # ------------------------------------------------------------------
    # real team building (P16 recipe port)
    # ------------------------------------------------------------------
    @classmethod
    def _team_task(cls, label, members, keys, *, completed):
        collab = importlib.import_module("coscreen.task_collaboration")
        cdb = importlib.import_module("coscreen.db")
        wf = importlib.import_module("coscreen.review_workflow")
        tasks = importlib.import_module("coscreen.tasks")
        tf = importlib.import_module("coscreen.team_terminal_facts")
        tr = importlib.import_module("coscreen.team_terminal_registry")
        receipts = importlib.import_module("coscreen.team_flow_receipts")
        tsl = importlib.import_module("custom_backend.team_source_lifecycle")
        coding_evidence = importlib.import_module("coscreen.coding_evidence")

        owner = cls.users["p21owner"]
        owner_uid = owner["user_id"]
        cls.task_id = cls.client.post(
            "/api/tasks", json={"name": f"p21-{label}-" + uuid.uuid4().hex[:8]},
            headers=cls._auth("p21owner")).json()["task_id"]
        cls.info = tasks.find_task(cls.task_id, cls.main_mod.DATA_DIR)
        task_dir = cls.info.dir
        collab_path = collab.shared_store_path(task_dir)
        if not collab_path.is_file():
            collab.initialize_task_store(
                task_dir, task_id=cls.task_id, owner_user_id=owner_uid,
                owner_username=owner["username"])
        collab.set_business_role(
            collab_path, actor_user_id=owner_uid, user_id=owner_uid,
            business_role="reconciler", task_id=cls.task_id)
        slug = tasks.slugify_screener
        cls.user_db = {name: task_dir / f"{slug(u['username'])}.db"
                       for name, u in cls.users.items()}
        for name in members:
            if name == "p21owner":
                continue
            uid = cls.users[name]["user_id"]
            collab.grant_role(collab_path, actor_user_id=owner_uid,
                              user_id=uid, role="coder", task_id=cls.task_id)
            collab.set_business_role(
                collab_path, actor_user_id=owner_uid, user_id=uid,
                business_role="coder", task_id=cls.task_id)
        articles = [(k, f"P21 {k}", i) for i, k in enumerate(keys)]
        for name in members:
            path = cls.user_db[name]
            cdb.init_db(path)
            wf.migrate(path)
            tf.prepare_team_terminal_facts(path)
            receipts.prepare_team_flow_receipts(path)
            conn = sqlite3.connect(str(path), timeout=15)
            try:
                conn.execute("PRAGMA foreign_keys=ON")
                uid = cls.users[name]["user_id"]
                if name != "p21owner":
                    conn.execute(_UPSERT_LIBRARY, ("%032x" % uid,))
                conn.executemany(_INSERT_ARTICLE, articles)
                if name == "p21owner":
                    conn.execute(_LATCH)
                conn.commit()
            finally:
                conn.close()
            coding_evidence.ensure_fields(path)
        tr.prepare_team_terminal_registry(collab_path)
        with closing(sqlite3.connect(str(collab_path))) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT task_instance_id FROM main.team_task_protocol"
                " LIMIT 1").fetchone()
        assert row is not None, "create_task must register the protocol row"
        instance = row["task_instance_id"]
        tf.prepare_team_terminal_facts(collab_path)
        for name in members:
            if name == "p21owner":
                continue
            uid = cls.users[name]["user_id"]
            library = "%032x" % uid
            token = tsl.make_source_identity(
                task_dir=task_dir, source_path=cls.user_db[name],
                source_user_id=uid, source_library_id=library)
            writer = _borrowed_writer(collab_path)
            try:
                tr.register_source_on(
                    writer, task_instance_id=instance,
                    source_user_id=uid, source_library_id=library,
                    source_path_identity=token,
                    registration_id=f"reg-p21-{label}-{name}")
                writer.commit()
            finally:
                _close_quietly(writer)
        # one solo stage1 vote by the first screener on every key: real
        # decisions rows, no stage completion yet (single required vote).
        for key in keys:
            resp = cls.client.post(
                f"/api/tasks/{cls.task_id}/decisions",
                json={"zotero_key": key, "decision": "include",
                      "reason": "", "notes": "", "tags": None},
                headers=cls._auth(members[1]))
            assert resp.status_code == 200, resp.text
        # completed keys: both required screeners finish stage1 then stage2.
        for key in completed:
            for stage_route in ("decisions", "stage2/decisions"):
                for name in members[1:]:
                    resp = cls.client.post(
                        f"/api/tasks/{cls.task_id}/{stage_route}",
                        json={"zotero_key": key, "decision": "include",
                              "reason": "", "notes": "", "tags": None},
                        headers=cls._auth(name))
                    assert resp.status_code == 200, (resp.status_code, resp.text)
        cls.collab_path = collab_path
        cls.instance_id = instance
        cls._remember(label)

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    @classmethod
    def _put_settings(cls, task_id, name, body):
        return cls.client.put(
            f"/api/tasks/{task_id}/trace/settings", json=body,
            headers=cls._auth(name))

    @classmethod
    def _get_settings(cls, task_id, name):
        resp = cls.client.get(f"/api/tasks/{task_id}/trace/settings",
                              headers=cls._auth(name))
        assert resp.status_code == 200, resp.text
        return resp.json()

    @classmethod
    def _clear(cls, task_id, name, *, expected_version=None, operation_id=None):
        body = {"confirm": True}
        if expected_version is not None:
            body["expected_version"] = expected_version
        if operation_id is not None:
            body["operation_id"] = operation_id
        return cls.client.request("DELETE",
                                  f"/api/tasks/{task_id}/trace/history",
                                  json=body, headers=cls._auth(name))

    @classmethod
    def _seed_adjudicated(cls, collab_path, task_id, key):
        """Seed one real adjudicated stage1 attestation row pair (T1)."""
        arbitration = importlib.import_module("coscreen.screening_arbitration")
        arbitration.prepare_arbitration_attestation_storage(collab_path)
        records = json.dumps([
            {"zotero_key": key, "arbitration_status": "adjudicated",
             "final_decision": "include"}])
        proof = json.dumps({key: {"decision": "include"}})
        conn = _borrowed_writer(collab_path)
        try:
            conn.execute(
                "INSERT INTO screening_arbitrations"
                " (stage,merge_sha256,result_sha256,supersedes_id,arbitrator_id,"
                " records_json,created_at) VALUES ('stage1',?,?,NULL,?,?,?)",
                ("m" * 64, "r" * 64, 1, records, "2026-11-19T00:00:00Z"))
            record_id = conn.execute(
                "SELECT last_insert_rowid()").fetchone()[0]
            conn.execute(
                "INSERT INTO screening_arbitration_attestations"
                " (record_id,task_id,round_id,stage,arbitrator_user_id,"
                " arbitrator_role,required_names_json,input_proof_json,"
                " resolutions_digest,attest_sha256,attested_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (record_id, task_id, 1, "stage1", 1, "owner",
                 json.dumps(["p21s1", "p21s2"]), proof, "d" * 64,
                 "a" * 64, "2026-11-19T00:00:01Z"))
            conn.commit()
        finally:
            _close_quietly(conn)

    def _row(self, path, sql, args=()):
        with closing(sqlite3.connect(str(path))) as conn:
            return conn.execute(sql, args).fetchone()

    def _events(self, path, task_id, actor_id):
        return self._row(
            path,
            "SELECT COUNT(*) FROM trace_events WHERE task_id=? AND actor_id=?",
            (task_id, str(actor_id)))[0]

    def _receipt_count(self, path, task_id, actor_id):
        return self._row(
            path,
            "SELECT COUNT(*) FROM trace_clear_operations"
            " WHERE task_id=? AND actor_id=?",
            (task_id, str(actor_id)))[0]

    def _settings_version(self, path, task_id, actor_id):
        return self._row(
            path,
            "SELECT version FROM trace_settings WHERE task_id=? AND actor_id=?",
            (task_id, str(actor_id)))[0]

    def _protected(self, resp, code):
        assert resp.status_code == 409, resp.text
        detail = resp.json()["detail"]
        assert detail["code"] == code, detail
        # P21 assertion anchor: a protective rejection is never a partial.
        assert detail.get("status") != "partial", detail
        return detail

    # ------------------------------------------------------------------
    # fixture: T1 arbitration fact + actor recordings
    # ------------------------------------------------------------------
    @classmethod
    def _prepare_recordings(cls):
        cls.task_arb = cls._last_task_ids["arb"]
        cls.task_seal = cls._last_task_ids["seal"]
        cls.task_plain = cls._last_task_ids["plain"]
        # 1) enable recording first (protection facts are seeded afterwards —
        #    the same-footprint guard correctly blocks later recording PUTs).
        for task_id, name in ((cls.task_arb, "p21s1"), (cls.task_seal, "p21s2"),
                              (cls.task_plain, "p21m3"), (cls.task_plain, "p21owner")):
            version = cls._get_settings(task_id, name)["version"]
            resp = cls._put_settings(task_id, name, {
                "status": "recording", "stages": ["screen", "fulltext"],
                "expected_version": version,
                "operation_id": f"rec-{name}-{task_id[:8]}"})
            assert resp.status_code == 200, (name, task_id, resp.text)
        # 2) T2 seal facts: finish stage1 (s2 completes the pair) then stage2.
        for stage_route in ("decisions", "stage2/decisions"):
            for name in ("p21s1", "p21s2"):
                resp = cls.client.post(
                    f"/api/tasks/{cls.task_seal}/{stage_route}",
                    json={"zotero_key": KEY_SEAL, "decision": "include",
                          "reason": "", "notes": "", "tags": None},
                    headers=cls._auth(name))
                assert resp.status_code == 200, (name, stage_route, resp.text)
        # 3) T1 arbitration fact: adjudicated stage1 attestation on KEY_ARB.
        cls._seed_adjudicated(cls.collab_arb, cls.task_arb, KEY_ARB)

    # setUpClass bookkeeping for the three tasks
    @classmethod
    def _remember(cls, label):
        cls._last_task_ids = getattr(cls, "_last_task_ids", {})
        cls._last_task_ids[label] = cls.task_id
        if label == "arb":
            cls.collab_arb = cls.collab_path
        elif label == "seal":
            cls.collab_seal = cls.collab_path
        else:
            cls.collab_plain = cls.collab_path
            cls.plain_db = dict(cls.user_db)

    # ------------------------------------------------------------------
    # D1 regression pin: recording-entry domain + legal off
    # ------------------------------------------------------------------
    def test_d1_recording_entry_domain_and_legal_off(self):
        task_id = self.task_arb
        s2_db = self.member_db("arb", "p21s2")
        s2 = self.users["p21s2"]["user_id"]
        # (1) legal off: the off transition's real write surface is control
        # events only — must pass even with an adjudicated key present
        # (previously over-blocked). Uses the fresh actor's current version.
        version = self._get_settings(task_id, "p21s2")["version"]
        resp = self._put_settings(task_id, "p21s2", {
            "status": "off", "stages": ["screen"],
            "expected_version": version,
            "operation_id": "d1-off-" + uuid.uuid4().hex[:8]})
        self.assertEqual(resp.status_code == 200, True, resp.text)
        version_after_off = self._settings_version(s2_db, task_id, s2)
        # (2) entering recording with stages=["fulltext"] (no screen!):
        # update_settings unconditionally transcribes ALL articles+protocol
        # baselines (R3-06, screen label) — the protection domain must include
        # them regardless of body.stages; the adjudicated article blocks.
        for stages in (["fulltext"], ["coding"]):
            resp = self._put_settings(task_id, "p21s2", {
                "status": "recording", "stages": stages,
                "expected_version": version_after_off,
                "operation_id": "d1-rec-" + uuid.uuid4().hex[:8]})
            detail = self._protected(
                resp, "history_read_only_after_arbitration")
            self.assertIn(KEY_ARB, detail["item_keys"], detail)
        # zero writes from the rejected puts: version unchanged, still off.
        self.assertEqual(self._settings_version(s2_db, task_id, s2),
                         version_after_off)
        # (3) off+clear-domain consistency: the clear path's deletion domain
        # (history events) still contains the adjudicated key even though the
        # actor is now off — the same arbitration lock rejects the clear.
        detail = self._protected(self._clear(task_id, "p21s2"),
                                 "history_read_only_after_arbitration")
        self.assertIn(KEY_ARB, detail["item_keys"], detail)

    # ------------------------------------------------------------------
    # 1. second-partition protection leaves the first physical store intact
    # ------------------------------------------------------------------
    def test_second_partition_protected_first_unchanged(self):
        task_id = self.task_arb
        s1 = self.users["p21s1"]["user_id"]
        owner_db = self.plain_owner_db("arb")
        s1_db = self.member_db("arb", "p21s1")
        owner_before = (self._events(owner_db, task_id, s1),
                        self._receipt_count(owner_db, task_id, s1),
                        self._settings_version(owner_db, task_id, s1))
        personal_before = self._events(s1_db, task_id, s1)
        resp = self._clear(task_id, "p21s1")
        detail = self._protected(resp, "history_read_only_after_arbitration")
        self.assertEqual(detail["item_keys"], [KEY_ARB], detail)
        self.assertEqual(detail["stage"], "screen")
        # first-acquired (owner/shared) physical store: zero writes.
        self.assertEqual(
            (self._events(owner_db, task_id, s1),
             self._receipt_count(owner_db, task_id, s1),
             self._settings_version(owner_db, task_id, s1)), owner_before)
        # second partition (personal) untouched as well.
        self.assertEqual(self._events(s1_db, task_id, s1), personal_before)

    # ------------------------------------------------------------------
    # 2. arbitration vs completion-seal barrier
    # ------------------------------------------------------------------
    def test_arbitration_or_seal_vs_clear_barrier(self):
        # (a) raw adjudicated arbitration blocks the clear (T1).
        detail = self._protected(
            self._clear(self.task_arb, "p21s1"),
            "history_read_only_after_arbitration")
        self.assertEqual(detail["item_keys"], [KEY_ARB])
        self.assertEqual(detail["message_key"],
                         "team.history.read_only_after_arbitration")
        # (b) F1 completion seal blocks the clear (T2, no arbitration facts).
        s2 = self.users["p21s2"]["user_id"]
        owner_db = self.plain_owner_db("seal")
        before = (self._events(owner_db, self.task_seal, s2),
                  self._receipt_count(owner_db, self.task_seal, s2))
        detail = self._protected(self._clear(self.task_seal, "p21s2"),
                                 "stage_completed_read_only")
        self.assertIn(KEY_SEAL, detail["item_keys"], detail)
        self.assertEqual(
            (self._events(owner_db, self.task_seal, s2),
             self._receipt_count(owner_db, self.task_seal, s2)), before)

    # ------------------------------------------------------------------
    # 3. same-physical-store dedup + cross-uid alias denial
    # ------------------------------------------------------------------
    def test_same_physical_store_dedup_and_alias_denial(self):
        task_id = self.task_plain
        owner = self.users["p21owner"]["user_id"]
        owner_db = self.plain_owner_db("plain")
        resp = self._clear(task_id, "p21owner")
        self.assertEqual(resp.status_code, 200, resp.text)
        payload = resp.json()
        self.assertEqual(payload["status"], "cleared", payload)
        # both logical names resolved, one physical execution: exactly one
        # receipt row for this operation on the single physical store.
        self.assertIn("personal", payload["partitions"])
        self.assertIn("shared", payload["partitions"])
        self.assertEqual(
            self._row(owner_db,
                      "SELECT COUNT(*) FROM trace_clear_operations"
                      " WHERE task_id=? AND actor_id=? AND operation_id=?",
                      (task_id, str(owner), payload["operation_id"]))[0], 1)
        # cross-uid hardlink alias is denied with zero writes.
        m4_db = self.member_db("plain", "p21m4")
        if m4_db.exists():
            m4_db.unlink()
        os.link(owner_db, m4_db)
        try:
            resp = self._clear(task_id, "p21m4")
            detail = self._protected(resp, "trace_store_alias_denied")
            self.assertEqual(self._receipt_count(owner_db, task_id, owner), 1)
        finally:
            m4_db.unlink()

    # ------------------------------------------------------------------
    # 4. off clears everything without rebuild; recording rebuilds baseline
    # ------------------------------------------------------------------
    def test_off_and_rebuilt_baseline_footprint(self):
        task_id = self.task_plain
        m3 = self.users["p21m3"]["user_id"]
        m3_db = self.member_db("plain", "p21m3")
        version = self._get_settings(task_id, "p21m3")["version"]
        resp = self._put_settings(task_id, "p21m3", {
            "status": "off", "stages": ["screen", "fulltext"],
            "expected_version": version})
        self.assertEqual(resp.status_code, 200, resp.text)
        resp = self._clear(task_id, "p21m3")
        self.assertEqual(resp.status_code, 200, resp.text)
        # off: all stages deleted, NO baseline rebuild — only the boundary.
        rows = self._row(
            m3_db,
            "SELECT COUNT(*) FROM trace_events WHERE task_id=? AND actor_id=?"
            " AND action != 'history_cleared'", (task_id, str(m3)))[0]
        self.assertEqual(rows, 0)
        receipts = self._receipt_count(m3_db, task_id, m3)
        # recording: clear rebuilds the global article baseline (screen tag).
        version = self._get_settings(task_id, "p21m3")["version"]
        self.assertEqual(self._put_settings(task_id, "p21m3", {
            "status": "recording", "stages": ["screen", "fulltext"],
            "expected_version": version,
            "operation_id": "rec-again-" + uuid.uuid4().hex[:8]}).status_code, 200)
        resp = self._clear(task_id, "p21m3")
        self.assertEqual(resp.status_code, 200, resp.text)
        rebuilt = self._row(
            m3_db,
            "SELECT COUNT(*) FROM trace_events WHERE task_id=? AND actor_id=?"
            " AND action='baseline_item'", (task_id, str(m3)))[0]
        self.assertGreaterEqual(rebuilt, 2)
        # clear deletes prior receipts for this actor and writes exactly one
        # row for the current operation: the newest operation id is present.
        second_op = resp.json()["operation_id"]
        self.assertEqual(
            self._row(m3_db,
                      "SELECT COUNT(*) FROM trace_clear_operations"
                      " WHERE task_id=? AND actor_id=? AND operation_id=?",
                      (task_id, str(m3), second_op))[0], 1)

    # ------------------------------------------------------------------
    # 5. per-DB commit failure keeps honest partial; explicit retry finishes
    # ------------------------------------------------------------------
    def test_commit_partial_and_explicit_retry(self):
        task_id = self.task_plain
        m3 = self.users["p21m3"]["user_id"]
        owner_db = self.plain_owner_db("plain")
        m3_db = self.member_db("plain", "p21m3")
        research_trace = importlib.import_module("coscreen.research_trace")
        real_clear = research_trace.clear_history
        calls = {"n": 0}

        def flaky(conn, *args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 2:  # second physical target (personal) fails
                raise sqlite3.OperationalError("injected commit-path fault")
            return real_clear(conn, *args, **kwargs)

        operation_id = "p21-partial-" + uuid.uuid4().hex[:8]
        with patch("coscreen.research_trace.clear_history", side_effect=flaky):
            resp = self._clear(task_id, "p21m3", operation_id=operation_id)
        self.assertEqual(resp.status_code, 200, resp.text)
        payload = resp.json()
        self.assertEqual(payload["status"], "partial", payload)
        self.assertIn("personal", payload["failures"])
        self.assertIn("shared", payload["partitions"])
        # first (owner/shared) store committed and keeps its facts; personal intact.
        self.assertEqual(self._receipt_count(owner_db, task_id, m3), 1)
        self.assertEqual(self._receipt_count(m3_db, task_id, m3), 0)
        # explicit retry with the same operation id finishes the batch.
        resp = self._clear(task_id, "p21m3", operation_id=operation_id)
        self.assertEqual(resp.status_code, 200, resp.text)
        payload = resp.json()
        self.assertEqual(payload["status"], "cleared", payload)
        self.assertEqual(self._receipt_count(owner_db, task_id, m3), 1)
        self.assertEqual(self._receipt_count(m3_db, task_id, m3), 1)
        # the patched window saw exactly the two per-target calls; the retry
        # ran unpatched (idempotent replay + fresh clear on the personal db).
        self.assertEqual(calls["n"], 2)

    # ------------------------------------------------------------------
    # 6. trace import cannot restore completed votes
    # ------------------------------------------------------------------
    def test_trace_import_cannot_restore_completed_votes(self):
        task_id = self.task_seal
        s2_db = self.member_db("seal", "p21s2")
        s2 = self.users["p21s2"]["user_id"]
        # export the actor's own recorded package (contains KS1/KS2 baselines).
        export = self.client.post(
            f"/api/tasks/{task_id}/trace/exports",
            json={"preset": "complete"}, headers=self._auth("p21s2"))
        self.assertIn(export.status_code, (200, 201))
        package = export.content
        preview = self.client.post(
            f"/api/tasks/{task_id}/trace/imports/preview",
            files={"package": ("p21.zip", package, "application/zip")},
            headers=self._auth("p21s2"))
        assert preview.status_code == 200, preview.text
        meta = preview.json()
        digest = meta["package_sha256"]
        aliases = {alias: alias for alias in meta.get("source_aliases", [])}
        before = self._events(s2_db, task_id, s2)
        commit = self.client.post(
            f"/api/tasks/{task_id}/trace/imports/commit",
            data={"preview_sha256": digest,
                  "actor_aliases": json.dumps(aliases),
                  "subject_mappings": "{}", "confirm_unmapped": "true"},
            files={"package": ("p21.zip", package, "application/zip")},
            headers=self._auth("p21s2"))
        detail = self._protected(commit, "stage_completed_read_only")
        self.assertIn(KEY_SEAL, detail["item_keys"], detail)
        # zero business writes: no imported rows landed.
        self.assertEqual(self._events(s2_db, task_id, s2), before)

    # ------------------------------------------------------------------
    def plain_owner_db(self, label):
        task = {"arb": self.task_arb, "seal": self.task_seal,
                "plain": self.task_plain}[label]
        info = importlib.import_module("coscreen.tasks").find_task(
            task, self.main_mod.DATA_DIR)
        collab = importlib.import_module("coscreen.task_collaboration")
        owner = collab.get_owner(collab.shared_store_path(info.dir), task_id=task)
        slug = importlib.import_module("coscreen.tasks").slugify_screener
        return info.dir / f"{slug(owner['owner_username'])}.db"

    def member_db(self, label, name):
        task = {"arb": self.task_arb, "seal": self.task_seal,
                "plain": self.task_plain}[label]
        info = importlib.import_module("coscreen.tasks").find_task(
            task, self.main_mod.DATA_DIR)
        slug = importlib.import_module("coscreen.tasks").slugify_screener
        return info.dir / f"{slug(self.users[name]['username'])}.db"


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
