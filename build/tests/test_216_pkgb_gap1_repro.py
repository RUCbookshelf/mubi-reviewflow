# -*- coding: utf-8 -*-
"""Package B, Gap 1 (HIGH#2 follow-up #2): post-removal reduced-roster vote.

Repro of the HIGH#1 rerun observation R8.1 (m01 owner_vote step): after a
seal-holding member is successfully removed (200 + library renamed away +
seals archived into collab, HIGH#2 landed semantics), further votes by the
SHRUNKEN roster answered a typed 503 connection_contract
"missing_members/borrowed 映射不完布".  Root cause (this package): the P16
ruling-A write-window retention (team_screening_write.py) still reserves
member libraries for required_screener participants whose source has been
RETIRED by the removal — a retired holder's library was renamed away, so the
reservation cannot honor it.

Expected after the fix: the retention set aligns with the current effective
roster (retired/removed members are not retained; their history stays
discoverable through the collab archive copies, HIGH#1 B5 semantics), and the
reduced roster can keep voting (200).

Real-HTTP test (TestClient over the real app), real team building (P16/P18
recipe, zero mocking), real removal through the management coordinator.
Imports are deferred to setUpClass so the file loads under -I -B.
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

KEY_SEAL = "PBGK1"   # removed member's completed key (archive survival)
KEY_OWN = "PBGK2"    # owner (business-role coder) votes after removal
KEY_MEM = "PBGK3"    # remaining coder votes after removal


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


class PostRemovalVoteTests(unittest.TestCase):
    """Reduced roster keeps voting after a seal-holding member is removed."""

    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        from custom_backend import auth as auth_mod
        from custom_backend import main as main_mod

        cls.client = TestClient(main_mod.app)
        cls.client.__enter__()
        cls.main_mod = main_mod
        cls.users = {}
        for name in ("pkgbo", "pkgbc", "pkgbd"):
            user = auth_mod.create_user(name, "PkgB-Gap1-2026")
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

    def _server_mode(self):
        saved = os.environ.get("REVIEWFLOW_MODE")

        def restore():
            if saved is None:
                os.environ.pop("REVIEWFLOW_MODE", None)
            else:
                os.environ["REVIEWFLOW_MODE"] = saved
        return _EnvGuard("server", restore)

    def _vote(self, name, key, decision, stage="screen", expect=200):
        route = "decisions" if stage == "screen" else "stage2/decisions"
        resp = self.client.post(
            f"/api/tasks/{self.task_id}/{route}",
            json={"zotero_key": key, "decision": decision,
                  "reason": "", "notes": "", "tags": None},
            headers=self._auth(name))
        self.assertEqual(resp.status_code, expect,
                         (name, key, resp.status_code, resp.text[:400]))
        return resp

    def _seals(self, stage=None, key=None):
        out = []
        for path in [self.collab_path, *self.user_db.values()]:
            try:
                with closing(sqlite3.connect(str(path))) as conn:
                    rows = [json.loads(r[0]) for r in conn.execute(
                        "SELECT payload_json FROM team_terminal_seals")]
            except sqlite3.Error:
                continue
            for payload in rows:
                if payload.get("kind") not in ("unanimous", "adjudicated"):
                    continue
                if stage is not None and payload.get("stage") != stage:
                    continue
                if key is not None and payload.get("item_key") != key:
                    continue
                out.append(payload)
        return out

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

        owner = cls.users["pkgbo"]
        owner_uid = owner["user_id"]
        cls.task_id = cls.client.post(
            "/api/tasks",
            json={"name": "pkgb-gap1-" + uuid.uuid4().hex[:8]},
            headers=cls._auth("pkgbo")).json()["task_id"]
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
        cls.user_db = {"pkgbo": task_dir / f"{slug(owner['username'])}.db"}
        cls.user_db.update({
            name: task_dir / f"{slug(cls.users[name]['username'])}.db"
            for name in ("pkgbc", "pkgbd")})
        for name in ("pkgbc", "pkgbd"):
            uid = cls.users[name]["user_id"]
            collab.grant_role(cls.collab_path, actor_user_id=owner_uid,
                              user_id=uid, role="coder", task_id=cls.task_id)
            collab.set_business_role(
                cls.collab_path, actor_user_id=owner_uid, user_id=uid,
                business_role="coder", task_id=cls.task_id)
        articles = [(k, f"PKG {k}", i) for i, k in enumerate(
            (KEY_SEAL, KEY_OWN, KEY_MEM))]
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
                if name != "pkgbo":
                    conn.execute(_UPSERT_LIBRARY, ("%032x" % uid,))
                conn.executemany(_INSERT_ARTICLE, articles)
                if name == "pkgbo":
                    conn.execute(_LATCH)
                conn.commit()
            finally:
                conn.close()
        tr.prepare_team_terminal_registry(cls.collab_path)
        importlib.import_module(
            "coscreen.screening_arbitration"
        ).prepare_arbitration_attestation_storage(cls.collab_path)
        importlib.import_module(
            "coscreen.arbitration_session"
        ).prepare_team_session_storage(str(cls.collab_path))
        with closing(sqlite3.connect(str(cls.collab_path))) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT task_instance_id FROM main.team_task_protocol"
                " LIMIT 1").fetchone()
        assert row is not None, "create_task must register the protocol row"
        cls.instance_id = row["task_instance_id"]
        tf.prepare_team_terminal_facts(cls.collab_path)
        receipts.prepare_team_flow_receipts(cls.collab_path)
        # ONLY pkgbc holds a source registration (m01 shape: the removed
        # member is the roster's last ACTIVE source; after the removal
        # retires it the task has zero active sources left).
        for name in ("pkgbc",):
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
                    registration_id=f"reg-pkgb-{name}")
                writer.commit()
            finally:
                _close_quietly(writer)

    def _keyed_view(self, stage, keys, name):
        resp = self.client.post(
            f"/api/tasks/{self.task_id}/work-items/{stage}/keyed",
            json={"zotero_keys": list(keys)}, headers=self._auth(name))
        self.assertEqual(resp.status_code, 200, resp.text[:400])
        return resp.json()

    def test_reduced_roster_votes_after_removal(self):
        uid = self.users
        # 1. KEY_SEAL stage1 completes unanimously with pkgbc as the LAST
        #    voter -> the seal lands in pkgbc's library (pkgbc thereby
        #    becomes a seal-holding member).
        self._vote("pkgbd", KEY_SEAL, "include")
        self._vote("pkgbc", KEY_SEAL, "include")
        self.assertEqual(len(self._seals(stage="stage1", key=KEY_SEAL)), 1)

        # 2. owner removes the seal-holding member through the real
        #    coordinator (HIGH#2 landed semantics: 200 + renamed + archived).
        with self._server_mode():
            removed = self.client.delete(
                f"/api/tasks/{self.task_id}/collaboration/roles/"
                f"{uid['pkgbc']['user_id']}/coder",
                headers=self._auth("pkgbo"))
        self.assertEqual(removed.status_code, 200, removed.text[:400])
        body = removed.json()
        self.assertTrue(body.get("membership_committed"), body)
        self.assertTrue(
            body.get("file_cleanup", {}).get("member_db_renamed"), body)

        # 3. HIGH#1 B5/archive survival: the removed member's completed key
        #    stays persisted/unanimous through the collab archive copy.
        view = self._keyed_view("screen", [KEY_SEAL], "pkgbo")
        completion = view["items_by_key"][KEY_SEAL]["completion"]
        self.assertEqual(completion["kind"], "unanimous", completion)
        self.assertTrue(completion["persisted"], completion)

        # 4. GAP 1 REPRO: the reduced roster keeps voting — owner switches to
        #    coder and votes a new key (the m01 owner_vote step that answered
        #    the typed 503 connection_contract [missing_members/borrowed]).
        with self._server_mode():
            switch = self.client.put(
                f"/api/tasks/{self.task_id}/collaboration/business-role/"
                f"{uid['pkgbo']['user_id']}",
                json={"business_role": "coder"},
                headers=self._auth("pkgbo"))
        self.assertEqual(switch.status_code, 200, switch.text[:400])
        owner_vote = self.client.post(
            f"/api/tasks/{self.task_id}/decisions",
            json={"zotero_key": KEY_OWN, "decision": "include",
                  "reason": "", "notes": "", "tags": None},
            headers=self._auth("pkgbo"))
        self.assertEqual(owner_vote.status_code, 200, owner_vote.text[:400])

        # 5. the remaining member of the reduced roster votes as well.
        self._vote("pkgbd", KEY_MEM, "include")
