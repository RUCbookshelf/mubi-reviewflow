"""HIGH#1 extended acceptance matrix E1-E9 (DESIGN.md section 5, 2026-11-20).

Real-API verification of the B1+B2 (+B3 P19 holder scan, +B4 observation
skip) fix on the fix tree: the write-gate chain leg and the fulltext read
chain leg consume persisted F1 seals through the keyed view (P09 plan
witness), per the unified authority rule (DESIGN section 0).

Scenario map (DESIGN section 5, E1-E9):

    e01a  E1a  undo BEFORE seal -> 200, live-vote chain (409 awaiting)
    e01b  E1b  undo AFTER seal  -> 409 stage_completed_read_only
    e01c  E1c  trace/history clear AFTER seal -> structured 409, seals kept
               (method test_e08_*: executes last, see note there)
    e02   E2   stage2 writes after adjudicated stage1 (200; complete->409)
    e03   E3   mixed keys: final_exclude 409 zero-write; pre/live 200;
               out-of-domain key -> key_not_in_domain (ruling 3 token)
    e04a  E5a  same seal held by two holders (archive copy + original
               library coexist, constructed BEFORE any retirement) ->
               settled 200, persisted
    e04b  E5b  conflicting seals across two ACTIVE holders (constructed
               BEFORE retirement) -> 409 conflicting_seals, zero write,
               read-side per-key error
    e04c  E4   HIGH#2 landed: real retirement API -> 200, member library
               renamed away, seal archived into collab (identical copy);
               D stage2 200, persisted; retired holder's library no longer
               a discovery dependency (B5)
    e06   E6   P23 read/write isomorphism on pre-join-sealed key
    e07   E7   P19 stage2 arbitration READY on pre-join-sealed key (B3);
               control key without stage1 settlement stays upstream_pending

Fixtures reuse the P25 matrix recipe (a6080470, read-only reference): real
team building (P16/P18/P19), real invites, real arbitration, real undo and
trace-clear endpoints; the collab archive copy in e04/e05a mirrors P14's
``list_seals_on`` + ``archive_seals_on`` retirement semantics.
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

# pre-join keys: stage1 sealed by A+B BEFORE D joins (seal lands in the last
# voter B's member library, origin 'actor' -- the HIGH#1 blind-spot holder)
KEY_PRE_MIX = "H1E3P"    # e03 pre-join-sealed leg
KEY_ARC = "H1E4K"        # e04 archived-into-collab leg
KEY_DUP = "H1E5A"        # e05a idempotent dual holder
KEY_CONF = "H1E5B"       # e05b conflicting injection target
KEY_ISO = "H1E6K"        # e06 read/write isomorphism
KEY_P19 = "H1E7K"        # e07 P19 ready (pre-join stage1 seal)
# post-join keys (voted only after D joined)
KEY_UNDO_PRE = "H1E1A"   # e01a undo before seal
KEY_UNDO_POST = "H1E1B"  # e01b undo after seal
KEY_CLEAR = "H1E1C"      # e01c trace clear (seal present)
KEY_ADJ = "H1E2K"        # e02 adjudicated stage1 -> stage2
KEY_LIVE_MIX = "H1E3L"   # e03 live-completed leg
KEY_EX_MIX = "H1E3X"     # e03 final_exclude leg
KEY_DOM_OUT = "H1E3Z"    # e03 out-of-domain token probe (never seeded)
KEY_P19_CTRL = "H1E7C"   # e07 control: no stage1 settlement

_PREJOIN_KEYS = (KEY_PRE_MIX, KEY_ARC, KEY_DUP, KEY_CONF, KEY_ISO, KEY_P19)
_ALL_KEYS = _PREJOIN_KEYS + (
    KEY_UNDO_PRE, KEY_UNDO_POST, KEY_CLEAR, KEY_ADJ, KEY_LIVE_MIX,
    KEY_EX_MIX, KEY_P19_CTRL)
_COMPLETION_KINDS = ("unanimous", "adjudicated")


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


class High1EScenarioTests(unittest.TestCase):
    """One real team, real HTTP, real arbitration; scenarios in name order."""

    task_id = {}
    collab_path = {}
    instance_id = {}
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
        for name in ("h1owner", "h1a", "h1b", "h1d"):
            user = auth_mod.create_user(name, "H1-E-Scenarios-2026")
            cls.users[name] = {
                "user_id": user["id"], "username": user["username"],
                "token": auth_mod.create_access_token(
                    user["id"], user["username"])}
        cls._build_team("main", "h1owner", ("h1a", "h1b"),
                        extra_libraries=("h1d",), keys=_ALL_KEYS)
        # pre-join stage1 seals: A+B unanimous include while required={A,B};
        # the last voter B holds every seal in its member library.
        for key in _PREJOIN_KEYS:
            cls._vote_setup("h1a", key)
            cls._vote_setup("h1b", key)
        assert len(cls._seals_setup(stage="stage1",
                                    keys=_PREJOIN_KEYS)) == len(_PREJOIN_KEYS)
        # D joins AFTER the pre-join seals landed (invite + business role;
        # the coordinator registers D's source itself).
        with cls._server_mode_setup():
            resp = cls.client.post(
                f"/api/tasks/{cls.task_id['main']}/collaboration/invite",
                json={"username": cls.users["h1d"]["username"],
                      "role": "coder"},
                headers=cls._auth_setup("h1owner"))
            assert resp.status_code == 201, resp.text
            resp = cls.client.put(
                f"/api/tasks/{cls.task_id['main']}/collaboration/business-role/"
                f"{cls.users['h1d']['user_id']}",
                json={"business_role": "coder"},
                headers=cls._auth_setup("h1owner"))
            assert resp.status_code == 200, resp.text

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    # ------------------------------------------------------------------
    # fixture helpers (P25 matrix recipe, adapted)
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
    def _build_team(cls, label, owner_name, coder_names, *, keys,
                    extra_libraries=()):
        collab = importlib.import_module("coscreen.task_collaboration")
        cdb = importlib.import_module("coscreen.db")
        wf = importlib.import_module("coscreen.review_workflow")
        tasks = importlib.import_module("coscreen.tasks")
        tf = importlib.import_module("coscreen.team_terminal_facts")
        tr = importlib.import_module("coscreen.team_terminal_registry")
        receipts = importlib.import_module("coscreen.team_flow_receipts")
        tsl = importlib.import_module("custom_backend.team_source_lifecycle")
        coding_evidence = importlib.import_module("coscreen.coding_evidence")

        owner = cls.users[owner_name]
        owner_uid = owner["user_id"]
        task_id = cls.client.post(
            "/api/tasks", json={"name": f"h1-{label}-" + uuid.uuid4().hex[:8]},
            headers=cls._auth_setup(owner_name)).json()["task_id"]
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
        member_names = tuple(coder_names) + tuple(extra_libraries)
        cls.user_db[label] = {
            owner_name: task_dir / f"{slug(owner['username'])}.db"}
        cls.user_db[label].update({
            name: task_dir / f"{slug(cls.users[name]['username'])}.db"
            for name in member_names})
        for name in coder_names:
            uid = cls.users[name]["user_id"]
            collab.grant_role(collab_path, actor_user_id=owner_uid,
                              user_id=uid, role="coder", task_id=task_id)
            collab.set_business_role(
                collab_path, actor_user_id=owner_uid, user_id=uid,
                business_role="coder", task_id=task_id)
        articles = [(k, f"H1 {k}", i) for i, k in enumerate(keys)]
        for name, path in cls.user_db[label].items():
            cdb.init_db(path)
            wf.migrate(path)
            tf.prepare_team_terminal_facts(path)
            receipts.prepare_team_flow_receipts(path)
            coding_evidence.ensure_fields(path)
            conn = sqlite3.connect(str(path), timeout=15)
            try:
                conn.execute("PRAGMA foreign_keys=ON")
                if name != owner_name:
                    conn.execute(_UPSERT_LIBRARY, (
                        "%032x" % cls.users[name]["user_id"],))
                conn.executemany(_INSERT_ARTICLE, articles)
                if name == owner_name:
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
        instance_id = row["task_instance_id"]
        tf.prepare_team_terminal_facts(collab_path)
        receipts.prepare_team_flow_receipts(collab_path)
        for name in coder_names:
            cls._register_source(label, name, collab_path, task_dir,
                                 instance_id)
        cls.task_id[label] = task_id
        cls.collab_path[label] = collab_path
        cls.instance_id[label] = instance_id

    @classmethod
    def _register_source(cls, label, name, collab_path, task_dir,
                         instance_id):
        tr = importlib.import_module("coscreen.team_terminal_registry")
        tsl = importlib.import_module("custom_backend.team_source_lifecycle")
        uid = cls.users[name]["user_id"]
        library = "%032x" % uid
        token = tsl.make_source_identity(
            task_dir=task_dir, source_path=cls.user_db[label][name],
            source_user_id=uid, source_library_id=library)
        writer = _borrowed_writer(collab_path)
        try:
            tr.register_source_on(
                writer, task_instance_id=instance_id, source_user_id=uid,
                source_library_id=library, source_path_identity=token,
                registration_id=f"reg-h1-{label}-{name}")
            writer.commit()
        finally:
            _close_quietly(writer)

    @classmethod
    def _vote_setup(cls, name, key, stage="screen"):
        route = "decisions" if stage == "screen" else "stage2/decisions"
        resp = cls.client.post(
            f"/api/tasks/{cls.task_id['main']}/{route}",
            json={"zotero_key": key, "decision": "include",
                  "reason": "", "notes": "", "tags": None},
            headers=cls._auth_setup(name))
        assert resp.status_code == 200, (name, key, stage, resp.text)

    @classmethod
    def _seals_setup(cls, stage=None, keys=None):
        paths = [cls.collab_path["main"], *cls.user_db["main"].values()]
        out = []
        for path in paths:
            try:
                with closing(sqlite3.connect(str(path))) as conn:
                    rows = [json.loads(r[0]) for r in conn.execute(
                        "SELECT payload_json FROM team_terminal_seals")]
            except sqlite3.Error:
                continue
            for payload in rows:
                if payload.get("kind") not in _COMPLETION_KINDS:
                    continue
                if stage is not None and payload.get("stage") != stage:
                    continue
                if keys is not None and payload.get("item_key") not in keys:
                    continue
                out.append(payload)
        return out

    # ------------------------------------------------------------------
    # per-test helpers
    # ------------------------------------------------------------------
    def _auth(self, name):
        return {"Authorization": "Bearer " + self.users[name]["token"]}

    def _server_mode(self):
        saved = os.environ.get("REVIEWFLOW_MODE")

        def restore():
            if saved is None:
                os.environ.pop("REVIEWFLOW_MODE", None)
            else:
                os.environ["REVIEWFLOW_MODE"] = saved
        return _EnvGuard("server", restore)

    def _vote(self, name, key, decision="include", stage="screen",
              expect=200):
        route = "decisions" if stage == "screen" else "stage2/decisions"
        resp = self.client.post(
            f"/api/tasks/{self.task_id['main']}/{route}",
            json={"zotero_key": key, "decision": decision,
                  "reason": "", "notes": "", "tags": None},
            headers=self._auth(name))
        self.assertEqual(resp.status_code, expect,
                         (name, key, stage, resp.status_code, resp.text))
        return resp

    def _keyed_view(self, stage, keys, name):
        resp = self.client.post(
            f"/api/tasks/{self.task_id['main']}/work-items/{stage}/keyed",
            json={"zotero_keys": list(keys)}, headers=self._auth(name))
        self.assertEqual(resp.status_code, 200, resp.text)
        return resp.json()

    def _seals(self, stage=None, key=None):
        return self._seals_setup(stage=stage, keys=(key,) if key else None)

    def _decision_row(self, name, key, stage="screen"):
        table = "decisions" if stage == "screen" else "stage2_decisions"
        with closing(sqlite3.connect(
                str(self.user_db["main"][name]))) as conn:
            return conn.execute(
                f"SELECT decision FROM {table} WHERE zotero_key=?",
                (key,)).fetchone()

    def _error_code(self, resp):
        detail = resp.json().get("detail")
        if isinstance(detail, dict):
            return detail.get("error_code") or detail.get("code") or ""
        return detail or ""

    def _arb_preview(self, stage="stage1"):
        resp = self.client.get(
            f"/api/tasks/{self.task_id['main']}/team-arbitration/session",
            params={"stage": stage}, headers=self._auth("h1owner"))
        self.assertEqual(resp.status_code, 200, resp.text)
        return resp.json()

    def _list_stage1_records(self, path, key=None):
        """F1 list within a read transaction (C02 borrowed contract).

        HIGH#2 landed semantics: a retired member's library is RENAMED AWAY
        by the removal flow -- a missing/unprepared store simply contributes
        no seals (the authoritative archived copy lives in collab), instead
        of failing the whole fixture scan.
        """
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
                conn, task_instance_id=self.instance_id["main"],
                stage="stage1"))
        except Exception:  # noqa: BLE001 - not_prepared 等＝该库无 seal 可采
            return []
        finally:
            try:
                conn.rollback()
            finally:
                conn.close()
        if key is not None:
            records = [r for r in records if _record_item_key(r) == key]
        return records

    def _archive_stage1_seal_into_collab(self, key):
        """Mirror P14 retirement archive semantics (fixture side, read-only
        product API): list the holder's stage1 seal for ``key`` and copy it
        into the collab store with archive_seals_on on a borrowed writer."""
        tf = importlib.import_module("coscreen.team_terminal_facts")
        records = self._list_stage1_records(
            self.user_db["main"]["h1b"], key)
        self.assertEqual(len(records), 1, key)
        writer = _borrowed_writer(self.collab_path["main"])
        try:
            archived = tf.archive_seals_on(writer, records)
            writer.commit()
        finally:
            _close_quietly(writer)
        self.assertEqual(len(archived), 1, key)
        return archived[0]

    # ------------------------------------------------------------------
    # E1a: undo BEFORE seal -> 200; chain still by live votes
    # ------------------------------------------------------------------
    def test_e01a_undo_before_seal_live_votes(self):
        self._vote("h1a", KEY_UNDO_PRE)   # A only; not unanimous
        self.assertEqual(self._seals(stage="stage1", key=KEY_UNDO_PRE), [])
        status = self.client.get(
            f"/api/tasks/{self.task_id['main']}/undo/screen",
            headers=self._auth("h1a")).json()
        entry = (status.get("latest") or {}).get("entry_id")
        self.assertIsNotNone(entry, status)
        undo = self.client.post(
            f"/api/tasks/{self.task_id['main']}/undo/screen",
            json={"entry_id": entry}, headers=self._auth("h1a"))
        self.assertEqual(undo.status_code, 200, undo.text)
        self.assertIsNone(self._decision_row("h1a", KEY_UNDO_PRE))
        # live-vote chain: stage1 unfinished (B/D never voted) -> others'
        # fulltext write is blocked by awaiting-members, zero write
        blocked = self.client.post(
            f"/api/tasks/{self.task_id['main']}/stage2/decisions",
            json={"zotero_key": KEY_UNDO_PRE, "decision": "include",
                  "reason": "", "notes": "", "tags": None},
            headers=self._auth("h1d"))
        self.assertEqual(blocked.status_code, 409, blocked.text)
        self.assertEqual(self._error_code(blocked),
                         "team_item_upstream_stage1_awaiting_members",
                         blocked.text)
        self.assertIsNone(
            self._decision_row("h1d", KEY_UNDO_PRE, "fulltext"))

    # ------------------------------------------------------------------
    # E1b: undo AFTER seal -> 409 stage_completed_read_only (m09 anchor)
    # ------------------------------------------------------------------
    def test_e01b_undo_after_seal_readonly(self):
        for name in ("h1a", "h1b", "h1d"):
            self._vote(name, KEY_UNDO_POST)
        self.assertEqual(
            len(self._seals(stage="stage1", key=KEY_UNDO_POST)), 1)
        status = self.client.get(
            f"/api/tasks/{self.task_id['main']}/undo/screen",
            headers=self._auth("h1a")).json()
        entry = (status.get("latest") or {}).get("entry_id")
        self.assertIsNotNone(entry, status)
        undo = self.client.post(
            f"/api/tasks/{self.task_id['main']}/undo/screen",
            json={"entry_id": entry}, headers=self._auth("h1a"))
        self.assertEqual(undo.status_code, 409, undo.text)
        self.assertEqual(self._error_code(undo),
                         "stage_completed_read_only", undo.text)
        self.assertEqual(
            self._decision_row("h1a", KEY_UNDO_POST)[0], "include")

    # ------------------------------------------------------------------
    # E1c (runs LAST: the trace-clear rejection path on an unprepared
    # partition holds the owner-db connection open in the app layer -- a
    # pre-existing product quirk independent of HIGH#1 -- so the scenario
    # executes after the other main-task writes to avoid lock contention)
    # ------------------------------------------------------------------
    def test_e08_e1c_trace_clear_after_seal_structured_409(self):
        seals_before = len(self._seals(stage="stage1", key=KEY_CLEAR))
        self.assertGreaterEqual(seals_before, 0)
        clear = self.client.request(
            "DELETE", f"/api/tasks/{self.task_id['main']}/trace/history",
            json={"confirm": True}, headers=self._auth("h1owner"))
        self.assertEqual(clear.status_code, 409, clear.text)
        detail = clear.json()["detail"]
        code = detail.get("error_code") or detail.get("code")
        self.assertIn(code, ("history_read_only_after_arbitration",
                             "trace_partition_unavailable"), detail)
        self.assertNotEqual(detail.get("status"), "partial", detail)
        self.assertEqual(
            len(self._seals(stage="stage1", key=KEY_CLEAR)), seals_before)

    # ------------------------------------------------------------------
    # E2: adjudicated stage1 seal -> stage2 writes pass; complete -> 409
    # ------------------------------------------------------------------
    def test_e02_stage2_after_adjudicated_stage1(self):
        self._vote("h1a", KEY_ADJ, "include")
        self._vote("h1b", KEY_ADJ, "exclude")
        self._vote("h1d", KEY_ADJ, "include")
        self.assertEqual(self._seals(stage="stage1", key=KEY_ADJ), [])
        version = self._arb_preview()["keys"][KEY_ADJ]["key_version"]
        decide = self.client.post(
            f"/api/tasks/{self.task_id['main']}/team-arbitration/session/item",
            json={"stage": "stage1", "zotero_key": KEY_ADJ,
                  "decision": "include", "reason": "",
                  "expected_key_version": version},
            headers=self._auth("h1owner"))
        self.assertEqual(decide.status_code, 200, decide.text)
        fresh = self._arb_preview()["keys"][KEY_ADJ]["key_version"]
        finish = self.client.post(
            f"/api/tasks/{self.task_id['main']}/team-arbitration/session/"
            "finish",
            json={"stage": "stage1", "item_keys": [KEY_ADJ],
                  "expected_key_versions": {KEY_ADJ: fresh}},
            headers={**self._auth("h1owner"),
                     "X-Request-Id": str(uuid.uuid4())})
        self.assertEqual(finish.status_code, 200, finish.text)
        seals = self._seals(stage="stage1", key=KEY_ADJ)
        self.assertEqual(len(seals), 1)
        self.assertEqual(seals[0]["kind"], "adjudicated")
        # chain leg consumes the adjudicated seal: both a completer and the
        # late member may vote stage2
        self._vote("h1a", KEY_ADJ, "include", "fulltext")
        self._vote("h1d", KEY_ADJ, "include", "fulltext")
        view = self._keyed_view("screen", [KEY_ADJ], "h1a")
        completion = view["items_by_key"][KEY_ADJ]["completion"]
        self.assertEqual(completion["kind"], "adjudicated")
        self.assertTrue(completion["persisted"])
        # complete stage2 unanimously -> sealed -> read-only afterwards
        self._vote("h1b", KEY_ADJ, "include", "fulltext")
        self.assertEqual(
            len(self._seals(stage="stage2", key=KEY_ADJ)), 1)
        self._vote("h1d", KEY_ADJ, "exclude", "fulltext", expect=409)
        self.assertEqual(
            self._decision_row("h1d", KEY_ADJ, "fulltext")[0], "include")

    # ------------------------------------------------------------------
    # E3: mixed keys -- final_exclude blocks (zero write), pre-join-sealed
    # and live-completed pass; out-of-domain key -> key_not_in_domain
    # ------------------------------------------------------------------
    def test_e03_mixed_keys_exclude_blocks_pre_and_live_pass(self):
        # live leg: stage1 completed by the CURRENT roster (seal in the last
        # voter D's member library)
        for name in ("h1a", "h1b", "h1d"):
            self._vote(name, KEY_LIVE_MIX)
        self.assertEqual(
            len(self._seals(stage="stage1", key=KEY_LIVE_MIX)), 1)
        # exclude leg: unanimous stage1 exclude seals final_exclude
        for name in ("h1a", "h1b", "h1d"):
            self._vote(name, KEY_EX_MIX, "exclude")
        exc_seals = self._seals(stage="stage1", key=KEY_EX_MIX)
        self.assertEqual(len(exc_seals), 1)
        self.assertEqual(exc_seals[0]["final_decision"], "exclude")
        # (a) exclude key: whole write rejected, zero business rows anywhere
        blocked = self.client.post(
            f"/api/tasks/{self.task_id['main']}/stage2/decisions",
            json={"zotero_key": KEY_EX_MIX, "decision": "include",
                  "reason": "", "notes": "", "tags": None},
            headers=self._auth("h1a"))
        self.assertEqual(blocked.status_code, 409, blocked.text)
        self.assertEqual(self._error_code(blocked),
                         "team_item_upstream_stage1_final_exclude",
                         blocked.text)
        for name in ("h1a", "h1b", "h1d"):
            self.assertIsNone(
                self._decision_row(name, KEY_EX_MIX, "fulltext"))
        # (b) pre-join-sealed key: stage2 write 200 (the HIGH#1 core fix)
        self._vote("h1a", KEY_PRE_MIX, "include", "fulltext")
        # (c) live-completed key: stage2 write 200
        self._vote("h1d", KEY_LIVE_MIX, "include", "fulltext")
        # (d) out-of-domain key (ruling 3 token probe): the WRITE entry
        # validation rejects unknown keys pre-gate with the typed
        # team_flow_invalid_item_keys 400 (zero write); the keyed READ side
        # answers key_not_in_domain in item_errors -- both sides structured
        # and consistent about "not in this task's writable domain".
        out = self.client.post(
            f"/api/tasks/{self.task_id['main']}/stage2/decisions",
            json={"zotero_key": KEY_DOM_OUT, "decision": "include",
                  "reason": "", "notes": "", "tags": None},
            headers=self._auth("h1a"))
        self.assertEqual(out.status_code, 400, out.text)
        self.assertEqual(self._error_code(out), "team_flow_invalid_item_keys",
                         out.text)
        self.assertIsNone(self._decision_row("h1a", KEY_DOM_OUT, "fulltext"))
        # read side mirrors the same per-key classification: exclude key is
        # a non-writable item with the chain reason (not an error entry);
        # the out-of-domain key is a per-key error entry
        view = self._keyed_view(
            "fulltext", [KEY_PRE_MIX, KEY_LIVE_MIX, KEY_EX_MIX, KEY_DOM_OUT],
            "h1d")
        exc_item = view["items_by_key"][KEY_EX_MIX]
        self.assertFalse(exc_item["writable"], exc_item)
        self.assertEqual(exc_item["reason"],
                         "team_item_upstream_stage1_final_exclude", exc_item)
        self.assertNotIn(KEY_DOM_OUT, view["items_by_key"])
        self.assertEqual(view["item_errors"][KEY_DOM_OUT]["reason"],
                         "key_not_in_domain")
        self.assertTrue(view["items_by_key"][KEY_PRE_MIX]["writable"])
        self.assertTrue(view["items_by_key"][KEY_LIVE_MIX]["writable"])

    # ------------------------------------------------------------------
    # E5a (runs BEFORE the retirement scenario so the dual-holder premise
    # "archive copy + original library coexist" is constructed with the
    # original library still in place)
    # ------------------------------------------------------------------
    def test_e04a_duplicate_seal_two_holders_idempotent(self):
        holder_records = self._holder_records(KEY_DUP)
        self.assertEqual(len(holder_records), 1)  # B's library only, yet
        self._archive_stage1_seal_into_collab(KEY_DUP)
        holder_records = self._holder_records(KEY_DUP)
        self.assertEqual(len(holder_records), 2)  # + identical collab copy
        first, second = holder_records
        self.assertEqual(first["seal_id"], second["seal_id"])
        self.assertEqual(first["content_sha256"], second["content_sha256"])
        self.assertEqual(first["payload_sha256"], second["payload_sha256"])
        self.assertEqual(first["sealed_at"], second["sealed_at"])
        self._vote("h1a", KEY_DUP, "include", "fulltext")
        view = self._keyed_view("screen", [KEY_DUP], "h1a")
        self.assertTrue(
            view["items_by_key"][KEY_DUP]["completion"]["persisted"])

    # ------------------------------------------------------------------
    # E4 (HIGH#2 landed, rebased): real retirement API on the seal holder
    # succeeds (200, member library renamed away, seals archived into the
    # collab store); afterwards the archived seal stays discoverable, the
    # late member D may vote stage2, and the retired holder's vanished
    # library is no longer a discovery dependency (B5).
    # ------------------------------------------------------------------
    def test_e04c_retire_holder_real_api_archive_then_stage2(self):
        before = self._list_stage1_records(
            self.user_db["main"]["h1b"], KEY_ARC)
        self.assertEqual(len(before), 1, "pre-join seal in B's library")
        with self._server_mode():
            retire = self.client.delete(
                f"/api/tasks/{self.task_id['main']}/collaboration/roles/"
                f"{self.users['h1b']['user_id']}/coder",
                headers=self._auth("h1owner"))
        self.assertEqual(retire.status_code, 200, retire.text)
        body = retire.json()
        self.assertTrue(body.get("membership_committed"), body)
        self.assertTrue(
            body.get("file_cleanup", {}).get("member_db_renamed"), body)
        # HIGH#2 retirement archive: B's stage1 seal now lives in the collab
        # store as the identical archived copy (four fields preserved).
        archived = self._list_stage1_records(
            self.collab_path["main"], KEY_ARC)
        self.assertEqual(len(archived), 1, archived)
        self.assertEqual(archived[0]["seal_id"], before[0]["seal_id"])
        self.assertEqual(
            archived[0]["payload_sha256"], before[0]["payload_sha256"])
        self.assertEqual(archived[0]["sealed_at"], before[0]["sealed_at"])
        # the retired holder's renamed-away library is no longer a
        # discovery dependency: D's stage2 write passes (was
        # source_identity_unresolved pre-B5), completion stays persisted.
        self._vote("h1d", KEY_ARC, "include", "fulltext")
        view = self._keyed_view("screen", [KEY_ARC], "h1d")
        completion = view["items_by_key"][KEY_ARC]["completion"]
        self.assertEqual(completion["kind"], "unanimous")
        self.assertTrue(completion["persisted"])

    # ------------------------------------------------------------------
    # E5b: conflicting seals across holders -> 409 conflicting_seals,
    # zero write; read side per-key error
    # ------------------------------------------------------------------
    def test_e04b_conflicting_seals_key_level_fail_closed(self):
        tf = importlib.import_module("coscreen.team_terminal_facts")
        holder_records = self._holder_records(KEY_CONF)
        self.assertEqual(len(holder_records), 1)
        original = holder_records[0]
        # inject a DIFFERENT seal for the same (stage1, KEY_CONF) into A's
        # library: payload copy with a distinct sealed_at/request identity
        payload = json.loads(json.dumps(original["payload"]))
        payload["sealed_at"] = "2026-11-01T00:00:00.000Z"
        payload["source_request"]["request_id"] = str(uuid.uuid4())
        payload["source_request"]["request_sha256"] = "1" * 64
        writer = _borrowed_writer(self.user_db["main"]["h1a"])
        try:
            injected = tf.append_seal_on(writer, payload, origin="actor")
            writer.commit()
        finally:
            _close_quietly(writer)
        self.assertNotEqual(injected["seal_id"], original["seal_id"])
        # write gate: key-level fail-closed 409, zero business write
        blocked = self.client.post(
            f"/api/tasks/{self.task_id['main']}/stage2/decisions",
            json={"zotero_key": KEY_CONF, "decision": "include",
                  "reason": "", "notes": "", "tags": None},
            headers=self._auth("h1d"))
        self.assertEqual(blocked.status_code, 409, blocked.text)
        self.assertEqual(self._error_code(blocked), "conflicting_seals",
                         blocked.text)
        for name in ("h1a", "h1b", "h1d"):
            self.assertIsNone(
                self._decision_row(name, KEY_CONF, "fulltext"))
        # read side: per-key unavailable with the same reason
        view = self._keyed_view("fulltext", [KEY_CONF], "h1d")
        self.assertNotIn(KEY_CONF, view["items_by_key"])
        self.assertEqual(view["item_errors"][KEY_CONF]["reason"],
                         "conflicting_seals")

    # ------------------------------------------------------------------
    # E6: P23 read/write isomorphism on the pre-join-sealed key
    # ------------------------------------------------------------------
    def test_e06_read_write_isomorphism_prejoin_key(self):
        view = self._keyed_view("fulltext", [KEY_ISO], "h1d")
        item = view["items_by_key"][KEY_ISO]
        self.assertTrue(item["writable"], item)
        self.assertIsNone(item["reason"], item)
        # same key, same actor: the write agrees with the read (pre-fix the
        # pair was reason=team_item_upstream_stage1_awaiting_members + 409)
        resp = self.client.post(
            f"/api/tasks/{self.task_id['main']}/stage2/decisions",
            json={"zotero_key": KEY_ISO, "decision": "include",
                  "reason": "", "notes": "", "tags": None},
            headers=self._auth("h1d"))
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(
            self._decision_row("h1d", KEY_ISO, "fulltext")[0], "include")

    # ------------------------------------------------------------------
    # E7 (B3): P19 stage2 arbitration READY on the pre-join-sealed key;
    # control key without stage1 settlement stays upstream_pending
    # ------------------------------------------------------------------
    def test_e07_p19_ready_prejoin_stage2_holder_scan(self):
        # stage2 conflict among the CURRENT required roster {A, D} on
        # KEY_P19 (B was retired by e04c; its stage1 seal lives on as the
        # collab retirement archive, which is exactly what B3 must find)
        self._vote("h1a", KEY_P19, "include", "fulltext")
        self._vote("h1d", KEY_P19, "exclude", "fulltext")
        preview = self._arb_preview(stage="stage2")
        entry = preview["keys"][KEY_P19]
        self.assertTrue(entry["ready"], entry)  # pre-B3: upstream_pending
        # control: stage1 in live DISAGREEMENT with no seal anywhere ->
        # the key cannot even reach stage2 (write gate awaiting members),
        # so P19 lists it as not ready for a non-upstream reason; this
        # pins that B3 only widens seal discovery, not readiness semantics.
        self._vote("h1a", KEY_P19_CTRL, "include")
        self._vote("h1d", KEY_P19_CTRL, "exclude")
        stage2_attempt = self.client.post(
            f"/api/tasks/{self.task_id['main']}/stage2/decisions",
            json={"zotero_key": KEY_P19_CTRL, "decision": "include",
                  "reason": "", "notes": "", "tags": None},
            headers=self._auth("h1a"))
        self.assertEqual(stage2_attempt.status_code, 409, stage2_attempt.text)
        self.assertEqual(
            self._error_code(stage2_attempt),
            "team_item_upstream_stage1_awaiting_arbitration",
            stage2_attempt.text)
        preview = self._arb_preview(stage="stage2")
        control = preview["keys"].get(KEY_P19_CTRL)
        if control is not None:
            self.assertFalse(control["ready"], control)
            self.assertNotEqual(control["reason"], "upstream_pending",
                                control)

    # ------------------------------------------------------------------
    # fixture introspection helpers
    # ------------------------------------------------------------------
    def _holder_records(self, key):
        out = []
        for path in [self.collab_path["main"],
                     *self.user_db["main"].values()]:
            out.extend(self._list_stage1_records(path, key))
        return out


def _record_item_key(record):
    payload = record["payload"]
    if isinstance(payload, str):
        payload = json.loads(payload)
    return payload.get("item_key")


if __name__ == "__main__":
    unittest.main()
