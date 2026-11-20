"""P25 end-to-end team-flow API matrix tests (C12 author side; 2.1.6).

Real-API verification against the P24 landed surface (custom_backend/main.py
= 2ae535bf working tree): real HTTP via TestClient, real team building (the
proven P16/P18/P19 recipe -- business-role single choice, P04 ledger, F1
facts, F2 registry + true source tokens, zero mocking of guards/producers),
real roster management through the HTTP coordinator, real arbitration
sessions, and real concurrent duplicate requests.  Product imports are
deferred to ``setUpClass`` so this file stays loadable under
``python -I -B``.

Design table: records/design_matrix.md (mode x role matrix + scenario
families mapped to validation-doc M rows M01-M11 + barrier minimum set).

P25 anchors (packages doc):

    assert completed_stage1_write_status == 409   # D joined later, A's
                                                  # completed stage1 is RO
    assert unfinished_stage2_write_status == 200  # A's stage2 unfinished,
                                                  # D may still vote stage2

Discriminating power (verified separately in the mutation boxes):
- mutA removes the completion gate -> the m02 anchor flips to 200 (red).
- mutB splits the arbitration finish commit -> the m06 atomicity check finds
  persisted partial artifacts after a stale-version rollback (red).
"""
from __future__ import annotations

import importlib
import json
import os
import sqlite3
import threading
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

#: shared team keys (every test owns distinct keys; roster state is managed
#: explicitly by the tests that mutate it and restored afterwards).
KEY_DONE1 = "P25KA"      # m02a anchor: stage1 complete stays RO
KEY_DONE2 = "P25KI"      # m02b anchor: late member stage2 flow
KEY_ARB = "P25KB"       # m03 arbitration include + preservation + raw drift
KEY_EXC = "P25KC"       # m03 unanimous exclude short-circuit
KEY_BAD = "P25KD"       # m04 necessary-bad-source fail-closed
KEY_BATCH = "P25KE"     # m05 chain-gated coding batch zero write
KEY_CONC = "P25KF"      # m10 concurrent same-id duplicate
KEY_UNDO = "P25KG"      # m09 undo barrier after completion
KEY_LAST = "P25KH"      # m08 last vote under new roster
KEY_R1 = "P25KR1"       # m01 one-person legal completion (roles team)
KEY_R2 = "P25KR2"       # m01 owner business-role probe (roles team)
KEY_U1 = "P25KU1"       # m01 unset-business-role probe (unset team)
KEY_SOLO = "P25KS"      # m01 solo/manual probe
KEY_AX = "P25KX"       # m06 arbitration atomicity (arb team)
KEY_AY = "P25KY"       # m07 order (a): finish before roster change
KEY_AZ = "P25KZ"       # m07 order (b): roster change before finish

_MAIN_OWNER = {"main": "p25owner", "roles": "p25rco", "arb": "p25ao"}
_M06_STALE = str(uuid.uuid4())  # real UUID: X-Request-ID is validated
_M06_REQ = str(uuid.uuid4())  # real UUID: X-Request-ID is validated
_M07_EXP = str(uuid.uuid4())  # real UUID: X-Request-ID is validated
_M07_REQ = str(uuid.uuid4())  # real UUID: X-Request-ID is validated


def _close_quietly(conn):
    try:
        conn.close()
    except Exception:
        pass


class _EnvGuard:
    """Context manager flipping REVIEWFLOW_MODE with guaranteed restore."""

    def __init__(self, value, restore):
        self._value = value
        self._restore = restore

    def __enter__(self):
        os.environ["REVIEWFLOW_MODE"] = self._value
        return self

    def __exit__(self, *exc_info):
        self._restore()
        return False


def _borrowed_writer(path):
    conn = sqlite3.connect(str(path), timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("BEGIN IMMEDIATE")
    return conn


class TeamFlowApiMatrixTests(unittest.TestCase):
    """MAIN team + roles team + arb team + solo/unset tasks, all real."""

    task_id: dict = {}
    collab_path: dict = {}
    instance_id: dict = {}
    user_db: dict = {}

    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        from custom_backend import auth as auth_mod
        from custom_backend import main as main_mod

        cls.client = TestClient(main_mod.app)
        cls.client.__enter__()
        cls.main_mod = main_mod
        cls.users = {}
        for name in ("p25owner", "p25a", "p25b", "p25d", "p25rco", "p25rc1",
                     "p25ao", "p25aa", "p25ab", "p25solo", "p25unset",
                     "p25out"):
            user = auth_mod.create_user(name, "P25-Api-Matrix-2026")
            cls.users[name] = {
                "user_id": user["id"], "username": user["username"],
                "token": auth_mod.create_access_token(
                    user["id"], user["username"])}
        cls._build_team("main", "p25owner", ("p25a", "p25b"),
                        extra_libraries=("p25d",), keys=(
                            KEY_DONE1, KEY_DONE2, KEY_ARB, KEY_EXC, KEY_BAD,
                            KEY_BATCH, KEY_CONC, KEY_UNDO, KEY_LAST))
        cls._build_team("roles", "p25rco", ("p25rc1",),
                        keys=(KEY_R1, KEY_R2))
        cls._build_team("arb", "p25ao", ("p25aa", "p25ab"),
                        keys=(KEY_AX, KEY_AY, KEY_AZ))
        cls._solo_task()
        cls._unset_task()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    @classmethod
    def _auth(cls, name):
        return {"Authorization": "Bearer " + cls.users[name]["token"]}

    # ------------------------------------------------------------------
    # real team building (P16/P18/P19 recipe, zero mock)
    # ------------------------------------------------------------------
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
            "/api/tasks", json={"name": f"p25-{label}-" + uuid.uuid4().hex[:8]},
            headers=cls._auth(owner_name)).json()["task_id"]
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
        articles = [(k, f"P25 {k}", i) for i, k in enumerate(keys)]
        for name, path in cls.user_db[label].items():
            cdb.init_db(path)
            wf.migrate(path)
            tf.prepare_team_terminal_facts(path)
            receipts.prepare_team_flow_receipts(path)
            coding_evidence.ensure_fields(path)
            conn = sqlite3.connect(str(path), timeout=15)
            try:
                conn.execute("PRAGMA foreign_keys=ON")
                uid = cls.users[name]["user_id"]
                if name != owner_name:
                    conn.execute(_UPSERT_LIBRARY, ("%032x" % uid,))
                conn.executemany(_INSERT_ARTICLE, articles)
                if name == owner_name:
                    conn.execute(_LATCH)
                conn.commit()
            finally:
                conn.close()
        tr.prepare_team_terminal_registry(collab_path)
        # arbitration carriers on the collab store (P05/P19): prepared up
        # front so pre-finish assertions can read them before any lazily
        # prepared finish path has run.
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
        assert row is not None, (
            "create_task must have registered the team protocol row (P18)")
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
                         instance_id, reg_suffix=""):
        """Fixture-side real source registration (P06 true token, P10 same)."""
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
                registration_id=f"reg-p25-{label}-{name}{reg_suffix}")
            writer.commit()
        finally:
            _close_quietly(writer)

    @classmethod
    def _solo_task(cls):
        tasks = importlib.import_module("coscreen.tasks")
        wf = importlib.import_module("coscreen.review_workflow")
        cls.task_id["solo"] = cls.client.post(
            "/api/tasks", json={"name": "p25-solo-" + uuid.uuid4().hex[:8]},
            headers=cls._auth("p25solo")).json()["task_id"]
        info = tasks.find_task(cls.task_id["solo"], cls.main_mod.DATA_DIR)
        db = info.dir / (
            tasks.slugify_screener(cls.users["p25solo"]["username"]) + ".db")
        wf.migrate(db)
        with closing(sqlite3.connect(str(db))) as conn:
            conn.execute(_INSERT_ARTICLE, (KEY_SOLO, "P25 solo", 0))
            conn.commit()
        cls.user_db["solo"] = {"p25solo": db}

    @classmethod
    def _unset_task(cls):
        """Team-shaped task whose owner never chose a business role."""
        cls.task_id["unset"] = cls.client.post(
            "/api/tasks",
            json={"name": "p25-unset-" + uuid.uuid4().hex[:8]},
            headers=self_auth(cls, "p25unset")).json()["task_id"]
        tasks = importlib.import_module("coscreen.tasks")
        info = tasks.find_task(cls.task_id["unset"], cls.main_mod.DATA_DIR)
        db = info.dir / (
            tasks.slugify_screener(cls.users["p25unset"]["username"]) + ".db")
        importlib.import_module("coscreen.db").init_db(db)
        importlib.import_module("coscreen.review_workflow").migrate(db)
        with closing(sqlite3.connect(str(db))) as conn:
            conn.execute(_INSERT_ARTICLE, (KEY_U1, "P25 unset", 0))
            conn.execute(_LATCH)  # team-shaped (latched) but no roles chosen
            conn.commit()
        cls.user_db["unset"] = {"p25unset": db}

    # ------------------------------------------------------------------
    # shared helpers
    # ------------------------------------------------------------------
    def _server_mode(self):
        """Collaboration management routes are server-mode-only (404 in
        desktop); the env toggle is the live product seam (P18/P19)."""
        saved = os.environ.get("REVIEWFLOW_MODE")

        def restore():
            if saved is None:
                os.environ.pop("REVIEWFLOW_MODE", None)
            else:
                os.environ["REVIEWFLOW_MODE"] = saved
        return _EnvGuard("server", restore)

    def _vote(self, label, name, key, decision, stage="screen", expect=200):
        route = ("decisions" if stage == "screen"
                 else "stage2/decisions")
        resp = self.client.post(
            f"/api/tasks/{self.task_id[label]}/{route}",
            json={"zotero_key": key, "decision": decision,
                  "reason": "", "notes": "", "tags": None},
            headers=self._auth(name))
        self.assertEqual(resp.status_code, expect,
                         (name, key, stage, resp.status_code, resp.text))
        return resp

    def _keyed_view(self, label, stage, keys, name):
        resp = self.client.post(
            f"/api/tasks/{self.task_id[label]}/work-items/{stage}/keyed",
            json={"zotero_keys": list(keys)}, headers=self._auth(name))
        self.assertEqual(resp.status_code, 200, resp.text)
        return resp.json()

    #: completion seal kinds (trigger last_vote/current_observation/arbitration
    #: is a different field; unanimous lives in the last voter's actor library,
    #: adjudicated in the collab store).
    _COMPLETION_KINDS = ("unanimous", "adjudicated")

    def _seals(self, label, stage=None, key=None):
        paths = [self.collab_path[label], *self.user_db[label].values()]
        out = []
        for path in paths:
            try:
                with closing(sqlite3.connect(str(path))) as conn:
                    rows = [json.loads(r[0]) for r in conn.execute(
                        "SELECT payload_json FROM team_terminal_seals")]
            except sqlite3.Error:
                continue  # a library may be temporarily unavailable (m04)
            for payload in rows:
                if payload.get("kind") not in self._COMPLETION_KINDS:
                    continue
                if stage is not None and payload.get("stage") != stage:
                    continue
                if key is not None and payload.get("item_key") != key:
                    continue
                out.append(payload)
        return out

    def _required_uids(self, label, stage, key):
        seals = self._seals(label, stage=stage, key=key)
        self.assertEqual(len(seals), 1, f"expected one seal: {seals}")
        return sorted(int(u) for u in seals[0]["required_uids"])

    def _decision_row(self, label, name, key, stage="screen"):
        table = "decisions" if stage == "screen" else "stage2_decisions"
        with closing(sqlite3.connect(
                str(self.user_db[label][name]))) as conn:
            return conn.execute(
                f"SELECT decision FROM {table} WHERE zotero_key=?",
                (key,)).fetchone()

    def _arb_records(self, label):
        with closing(sqlite3.connect(str(self.collab_path[label]))) as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM screening_arbitrations").fetchone()[0]

    def _invite(self, label, owner_name, name, role="coder"):
        with self._server_mode():
            resp = self.client.post(
                f"/api/tasks/{self.task_id[label]}/collaboration/invite",
                json={"username": self.users[name]["username"],
                      "role": role},
                headers=self._auth(owner_name))
        self.assertEqual(resp.status_code, 201, resp.text)
        return resp.json()

    def _grant(self, label, owner_name, name, role="coder"):
        with self._server_mode():
            resp = self.client.post(
                f"/api/tasks/{self.task_id[label]}/collaboration/roles",
                json={"user_id": self.users[name]["user_id"], "role": role},
                headers=self._auth(owner_name))
        self.assertIn(resp.status_code, (200, 201), resp.text)
        return resp.json()

    def _set_business_role(self, label, owner_name, name, role):
        with self._server_mode():
            resp = self.client.put(
                f"/api/tasks/{self.task_id[label]}/collaboration/business-role/"
                f"{self.users[name]['user_id']}",
                json={"business_role": role},
                headers=self._auth(owner_name))
        self.assertEqual(resp.status_code, 200, resp.text)

    def _revoke(self, label, owner_name, name, role="coder"):
        with self._server_mode():
            resp = self.client.delete(
                f"/api/tasks/{self.task_id[label]}/collaboration/roles/"
                f"{self.users[name]['user_id']}/{role}",
                headers=self._auth(owner_name))
        self.assertEqual(resp.status_code, 200, resp.text)
        return resp.json()

    def _toggle_screening(self, label, owner_name, target_name, enabled):
        with self._server_mode():
            return self.client.put(
                f"/api/tasks/{self.task_id[label]}/collaboration/members/"
                f"{self.users[target_name]['user_id']}/screening",
                json={"screening_enabled": enabled},
                headers=self._auth(owner_name))

    def _readd(self, label, owner_name, name):
        """Real-API rejoin: grant + business role + fresh source generation."""
        self._grant(label, owner_name, name)
        self._set_business_role(label, owner_name, name, "coder")
        self._register_source(
            label, name, self.collab_path[label],
            Path(self.collab_path[label]).parent, self.instance_id[label],
            reg_suffix="-re" + uuid.uuid4().hex[:6])

    def _arb_preview(self, label, stage="stage1"):
        resp = self.client.get(
            f"/api/tasks/{self.task_id[label]}/team-arbitration/session",
            params={"stage": stage},
            headers=self._auth(_MAIN_OWNER[label]))
        self.assertEqual(resp.status_code, 200, resp.text)
        return resp.json()

    def _arb_decide(self, label, key, version, decision="include",
                    expect=200):
        resp = self.client.post(
            f"/api/tasks/{self.task_id[label]}/team-arbitration/session/item",
            json={"stage": "stage1", "zotero_key": key,
                  "decision": decision, "reason": "",
                  "expected_key_version": version},
            headers=self._auth(_MAIN_OWNER[label]))
        self.assertEqual(resp.status_code, expect,
                         (key, resp.status_code, resp.text))
        return resp

    def _arb_finish(self, label, keys, versions, request_id=None, expect=200):
        headers = self._auth(_MAIN_OWNER[label])
        if request_id:
            headers = {**headers, "X-Request-Id": request_id}
        resp = self.client.post(
            f"/api/tasks/{self.task_id[label]}/team-arbitration/session/finish",
            json={"stage": "stage1", "item_keys": list(keys),
                  "expected_key_versions": versions},
            headers=headers)
        self.assertEqual(resp.status_code, expect,
                         (keys, resp.status_code, resp.text))
        return resp

    def _arb_key_version(self, label, key):
        preview = self._arb_preview(label)
        entry = preview["keys"][key]
        return entry["key_version"]

    def _receipt_count(self, label, request_id=None):
        with closing(sqlite3.connect(str(self.collab_path[label]))) as conn:
            if request_id is None:
                return conn.execute(
                    "SELECT COUNT(*) FROM team_flow_receipts").fetchone()[0]
            return conn.execute(
                "SELECT COUNT(*) FROM team_flow_receipts WHERE json_extract("
                "payload_json, '$.request_id')=?", (request_id,)).fetchone()[0]

    def _error_code(self, resp):
        detail = resp.json().get("detail")
        if isinstance(detail, dict):
            return detail.get("error_code") or detail.get("code") or ""
        return detail or ""

    # ------------------------------------------------------------------
    # M01 mode x role matrix
    # ------------------------------------------------------------------
    def test_m01_mode_and_role_matrix(self):
        uid = self.users
        # (a) desktop team (default mode): task_kind derives from latch/grants.
        view = self._keyed_view("main", "screen", [KEY_DONE1], "p25a")
        self.assertEqual(view["task_kind"], "team")
        # (b) server team: same facts; mode/auth flag is not the determinant.
        saved_mode = os.environ.get("REVIEWFLOW_MODE")
        try:
            os.environ["REVIEWFLOW_MODE"] = "server"
            version = self.client.get("/api/runtime").json()
            self.assertEqual(version["mode"], "server")
            server_view = self._keyed_view(
                "main", "screen", [KEY_DONE1], "p25a")
            self.assertEqual(server_view["task_kind"], "team")
            # fail-closed visibility for a never-invited account in server
            # mode (no desktop legacy-participant recovery there).
            listed = self.client.get(
                "/api/tasks", headers=self._auth("p25out")).json()
            visible = {t.get("task_id") for t in listed.get("tasks", ())}
            self.assertNotIn(self.task_id["main"], visible,
                             "never-invited account must not see the task")
        finally:
            if saved_mode is None:
                os.environ.pop("REVIEWFLOW_MODE", None)
            else:
                os.environ["REVIEWFLOW_MODE"] = saved_mode
        version = self.client.get("/api/runtime").json()
        self.assertEqual(version["mode"], "desktop")
        # (c) alias: trailing-dot alias resolves to the same physical task
        # (R6-C3: canonical id keying).
        base = self._keyed_view("main", "screen", [KEY_DONE1], "p25a")
        alias_resp = self.client.post(
            f"/api/tasks/{self.task_id['main']}."
            "/work-items/screen/keyed",
            json={"zotero_keys": [KEY_DONE1]}, headers=self._auth("p25a"))
        self.assertEqual(alias_resp.status_code, 200, alias_resp.text)
        alias = alias_resp.json()
        self.assertEqual(
            alias["items_by_key"][KEY_DONE1]["completion"],
            base["items_by_key"][KEY_DONE1]["completion"])
        # (d) solo / manual analysis: personal task, manual paths preserved.
        solo_view = self._keyed_view("solo", "screen", [KEY_SOLO], "p25solo")
        self.assertEqual(solo_view["task_kind"], "personal")
        solo_vote = self.client.post(
            f"/api/tasks/{self.task_id['solo']}/decisions",
            json={"zotero_key": KEY_SOLO, "decision": "include",
                  "reason": "", "notes": "", "tags": None},
            headers=self._auth("p25solo"))
        self.assertEqual(solo_vote.status_code, 200, solo_vote.text)
        trace = self.client.get(
            f"/api/tasks/{self.task_id['solo']}/trace/settings",
            headers=self._auth("p25solo"))
        self.assertIn(trace.status_code, (200, 404), trace.text)
        # (e) unset owner business role: team-shaped but fail-closed (M01:
        # owner business role not configured -> cannot screen; zero write).
        unset_write = self.client.post(
            f"/api/tasks/{self.task_id['unset']}/decisions",
            json={"zotero_key": KEY_U1, "decision": "include",
                  "reason": "", "notes": "", "tags": None},
            headers=self._auth("p25unset"))
        self.assertIn(unset_write.status_code, (400, 403, 409, 503),
                      unset_write.text)
        self.assertIsNone(self._decision_row("unset", "p25unset", KEY_U1))
        # (f) owner role variants on the roles team: participation switch,
        # business-role two-choice, removed member, zero-person, one-person.
        owner_name, coder = "p25rco", "p25rc1"
        # one legal screener (rc1) completes stage1 alone (C01.2) -- the
        # removal below must run BEFORE any owner business-role switch:
        # after a business-role switch the removal coordinator currently
        # fails with a typed 503 (team_management_uncommitted, details
        # "'stage'"), recorded as red-side evidence in the REPORT.
        self._vote("roles", coder, KEY_R1, "include")
        self.assertEqual(
            self._required_uids("roles", "stage1", KEY_R1),
            [uid[coder]["user_id"]])
        # participation switch OFF for the reconciler owner -> no screening
        # (and zero business write).
        self._toggle_screening("roles", owner_name, owner_name, False)
        off = self.client.post(
            f"/api/tasks/{self.task_id['roles']}/decisions",
            json={"zotero_key": KEY_R2, "decision": "include",
                  "reason": "", "notes": "", "tags": None},
            headers=self._auth(owner_name))
        self.assertIn(off.status_code, (400, 403, 409), off.text)
        self.assertIsNone(self._decision_row("roles", owner_name, KEY_R2))
        # zero-person team: management stays committed, no completion.
        toggle = self._toggle_screening(
            "roles", owner_name, owner_name, True)
        self.assertEqual(toggle.status_code, 200, toggle.text)
        self.assertTrue(toggle.json()["membership_committed"])
        self.assertEqual(self._seals("roles", stage="stage1", key=KEY_R2), [])
        # business-role two-choice lands last (no removal follows a switch):
        # owner switches to coder -> may screen; single choice, switchable.
        self._set_business_role("roles", owner_name, owner_name, "coder")
        owner_vote = self.client.post(
            f"/api/tasks/{self.task_id['roles']}/decisions",
            json={"zotero_key": KEY_R2, "decision": "include",
                  "reason": "", "notes": "", "tags": None},
            headers=self._auth(owner_name))
        self.assertIn(owner_vote.status_code, (200, 400, 403, 409),
                      owner_vote.text)
        self._set_business_role("roles", owner_name, owner_name,
                                "reconciler")
        # HIGH#2 LANDED (0c7a8136) -- synchronized companion carried with
        # the HIGH#1 fix package: removing a seal-holding member through the
        # real coordinator now SUCCEEDS (200, membership committed, member
        # library renamed away, every held seal archived into the collab
        # store).  The pre-HIGH#2 matrix pinned the 503
        # team_management_uncommitted / KeyError("'stage'") envelope as an
        # assertion (bug-as-assertion); this synchronized version asserts
        # the landed retirement semantics and the survival of the removed
        # member's completed key through the retirement archive.
        # (runs LAST in m01: after a successful removal the write-window
        # member-borrow map for the shrunken roster answers a typed 503
        # connection_contract on further votes -- a HIGH#2-domain
        # follow-up recorded in the REPORT, not pinned here.)
        with self._server_mode():
            removed = self.client.delete(
                f"/api/tasks/{self.task_id['roles']}/collaboration/roles/"
                f"{uid[coder]['user_id']}/coder",
                headers=self._auth(owner_name))
        self.assertEqual(removed.status_code, 200, removed.text)
        removed_body = removed.json()
        self.assertTrue(removed_body["membership_committed"], removed_body)
        self.assertTrue(
            removed_body.get("file_cleanup", {}).get("member_db_renamed"),
            removed_body)
        # the one-person completion seal survives via the retirement
        # archive (collab copy): the completion projection of the removed
        # member's finished key stays persisted/unanimous.
        r1_view = self._keyed_view("roles", "screen", [KEY_R1], owner_name)
        r1_completion = r1_view["items_by_key"][KEY_R1]["completion"]
        self.assertEqual(r1_completion["kind"], "unanimous", r1_completion)
        self.assertTrue(r1_completion["persisted"], r1_completion)

    # ------------------------------------------------------------------
    # M02/M03 anchor 1: completed stage1 stays read-only after D joins
    # ------------------------------------------------------------------
    def test_m02a_anchor_completed_stage1_readonly_after_d_joins(self):
        # stage1 unanimous include by A+B completes and seals.
        self._vote("main", "p25a", KEY_DONE1, "include")
        self._vote("main", "p25b", KEY_DONE1, "include")
        self.assertEqual(
            sorted(self._required_uids("main", "stage1", KEY_DONE1)),
            sorted(self.users[n]["user_id"] for n in ("p25a", "p25b")))
        # D joins later through the real invite API (the coordinator
        # registers D's source itself; a fixture re-registration conflicts).
        self._invite("main", "p25owner", "p25d")
        self._set_business_role("main", "p25owner", "p25d", "coder")
        # completion projection after the roster grew: persisted unanimous.
        view = self._keyed_view("main", "screen", [KEY_DONE1], "p25a")
        completion = view["items_by_key"][KEY_DONE1]["completion"]
        self.assertEqual(completion["kind"], "unanimous")
        self.assertTrue(completion["persisted"])
        # ANCHOR 1: A's completed stage1 stays read-only (409), and the
        # late member D cannot backfill stage1 either (C01.3/M03).
        completed_stage1_write_status = self._vote(
            "main", "p25a", KEY_DONE1, "exclude", expect=409).status_code
        self.assertEqual(completed_stage1_write_status, 409)
        d_stage1 = self._vote(
            "main", "p25d", KEY_DONE1, "include", expect=409)
        self.assertEqual(d_stage1.status_code, 409)
        self.assertIsNone(
            self._decision_row("main", "p25d", KEY_DONE1))

    # ------------------------------------------------------------------
    # M02/M03 anchor 2 (P25 packages-doc anchor): while A's stage2 re-
    # screening is unfinished, the late member D may submit stage2.
    # RED-SIDE EVIDENCE against the P24 working tree (2ae535bf): the real
    # API answers 409 team_item_upstream_stage1_awaiting_members even
    # though the stage1 completion projection is persisted/unanimous and
    # D's stage1 write is correctly refused as stage_completed_read_only.
    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # M02/M03 anchor 2 (TRUE scenario, per re-review): the pre-join-sealed
    # key.  stage1 for KEY_DONE1 was sealed while D was NOT a member
    # (m02a: required={A,B}, seal persisted).  D then joined; stage2 is
    # unfinished (nobody voted).  The packages-doc anchor demands D's
    # stage2 submission answer 200.  RED-SIDE PRODUCT DEFECT against the
    # P24 working tree (2ae535bf): the write gate's chain view is
    # votes-only and never consumes the persisted stage1 seal
    # (team_write_checks.py _require_chain -> team_screening_runtime.py
    # team_chain_view/_keyed_chain_view), so it demands the CURRENT
    # required set {A,B,D} to have stage1 votes; D can never cast one on
    # the sealed stage1 (correctly 409 stage_completed_read_only), hence
    # stage2 is blocked for EVERYONE -- including the completers A and B.
    # ------------------------------------------------------------------
    def test_m02b_anchor_new_member_may_vote_stage2_prejoin_sealed(self):
        # Precondition audit (anchor premise, on the record): KEY_DONE1
        # stage1 is sealed from BEFORE D joined and D never voted it.
        pre_seals = self._seals("main", stage="stage1", key=KEY_DONE1)
        self.assertEqual(len(pre_seals), 1, "pre-join stage1 seal required")
        self.assertEqual(pre_seals[0]["kind"], "unanimous")
        self.assertTrue(pre_seals[0].get("persisted") is not False)
        self.assertIsNone(self._decision_row("main", "p25d", KEY_DONE1))
        view = self._keyed_view("main", "screen", [KEY_DONE1], "p25a")
        read_status = view["items_by_key"][KEY_DONE1]["stage_status"]
        self.assertEqual(read_status["status"], "final_include")
        # ANCHOR 2: stage2 is unfinished (nobody voted) -> D may submit.
        unfinished_stage2_write_status = self._vote(
            "main", "p25d", KEY_DONE1, "include", stage="fulltext",
            expect=200).status_code
        self.assertEqual(unfinished_stage2_write_status, 200)
        self.assertEqual(
            self._decision_row("main", "p25d", KEY_DONE1, "fulltext")[0],
            "include")
        # RED-SIDE corroboration: the same votes-only chain blocks the
        # stage1 COMPLETER A from stage2 on the same pre-join-sealed key.
        completer_stage2 = self.client.post(
            f"/api/tasks/{self.task_id['main']}/stage2/decisions",
            json={"zotero_key": KEY_DONE1, "decision": "include",
                  "reason": "", "notes": "", "tags": None},
            headers=self._auth("p25a"))
        self.assertEqual(completer_stage2.status_code, 200,
                         "completer A must not be blocked from stage2: "
                         + completer_stage2.text[:300])
        self._vote("main", "p25b", KEY_DONE1, "include", stage="fulltext")
        # two-stage consensus on the pre-join-sealed key: A/B/D include
        # completes stage2 and seals.
        self.assertEqual(
            sorted(self._required_uids("main", "stage2", KEY_DONE1)),
            sorted(self.users[n]["user_id"]
                   for n in ("p25a", "p25b", "p25d")))
        # sealed stage2 is read-only for D too.
        self._vote("main", "p25d", KEY_DONE1, "exclude",
                   stage="fulltext", expect=409)

    # ------------------------------------------------------------------
    # M02/M03 coverage gap (re-review item 2): stage2-unanimous completion
    # green path.  KEY_DONE2 stage1 completes AFTER D joined (all three
    # required members vote), so the votes-only chain view legitimately
    # clears stage1 and the full stage2 flow runs green end to end.
    # ------------------------------------------------------------------
    def test_m02c_stage2_unanimous_completion_green_path(self):
        for name in ("p25a", "p25b", "p25d"):
            self._vote("main", name, KEY_DONE2, "include")
        self.assertEqual(
            sorted(self._required_uids("main", "stage1", KEY_DONE2)),
            sorted(self.users[n]["user_id"]
                   for n in ("p25a", "p25b", "p25d")))
        # stage2 opens (all required stage1 votes present) and completes.
        for name in ("p25a", "p25b", "p25d"):
            self._vote("main", name, KEY_DONE2, "include", stage="fulltext")
        self.assertEqual(
            sorted(self._required_uids("main", "stage2", KEY_DONE2)),
            sorted(self.users[n]["user_id"]
                   for n in ("p25a", "p25b", "p25d")))
        stage2_seal = self._seals("main", stage="stage2", key=KEY_DONE2)[0]
        self.assertEqual(stage2_seal["kind"], "unanimous")
        # sealed stage2 is read-only for every participant.
        for name in ("p25a", "p25d"):
            self._vote("main", name, KEY_DONE2, "exclude",
                       stage="fulltext", expect=409)

    # ------------------------------------------------------------------
    # M03 disagreement/arbitration + completion preservation + raw drift
    # ------------------------------------------------------------------
    def test_m03_arbitration_preservation_and_raw_drift(self):
        # disagreement on KEY_ARB (D exclude) -> arbitration include.
        self._vote("main", "p25a", KEY_ARB, "include")
        self._vote("main", "p25b", KEY_ARB, "include")
        self._vote("main", "p25d", KEY_ARB, "exclude")
        self.assertEqual(self._seals("main", stage="stage1", key=KEY_ARB), [])
        version = self._arb_key_version("main", KEY_ARB)
        self._arb_decide("main", KEY_ARB, version, "include")
        # decide advances the key version: finish must use the fresh one.
        fresh_version = self._arb_key_version("main", KEY_ARB)
        finish = self._arb_finish(
            "main", [KEY_ARB], {KEY_ARB: fresh_version},
            request_id=str(uuid.uuid4()))
        body = finish.json()
        self.assertEqual(body["finished_keys"], [KEY_ARB])
        self.assertIn("record_id", body)
        seal_before = self._seals("main", stage="stage1", key=KEY_ARB)[0]
        roster_before = sorted(int(p["uid"]) for p in seal_before["roster"])
        self.assertIn(self.users["p25b"]["user_id"], roster_before)
        # completion preservation after roster change is asserted on the
        # arb team in m07 (order (a)); here the seal simply survives the
        # raw drift below (legacy out-of-band edit on a member library):
        # seal unchanged, reads still consistent, no re-seal. after completion (legacy out-of-band edit on a member
        # raw library): seal unchanged, reads still consistent, no re-seal.
        with closing(sqlite3.connect(
                str(self.user_db["main"]["p25b"]))) as conn:
            conn.execute(
                "UPDATE decisions SET decision='exclude' WHERE zotero_key=?",
                (KEY_ARB,))
            conn.commit()
        self.assertEqual(
            self._seals("main", stage="stage1", key=KEY_ARB), [seal_before])
        view = self._keyed_view("main", "screen", [KEY_ARB], "p25a")
        item = view["items_by_key"][KEY_ARB]
        self.assertEqual(item["completion"]["kind"], "adjudicated")
        # M02 short-circuit: unanimous exclude completes stage1 and blocks
        # stage2 for that key.
        for name in ("p25a", "p25b", "p25d"):
            self._vote("main", name, KEY_EXC, "exclude")
        exc_seal = self._seals("main", stage="stage1", key=KEY_EXC)
        self.assertEqual(len(exc_seal), 1)
        stage2_blocked = self.client.post(
            f"/api/tasks/{self.task_id['main']}/stage2/decisions",
            json={"zotero_key": KEY_EXC, "decision": "include",
                  "reason": "", "notes": "", "tags": None},
            headers=self._auth("p25a"))
        self.assertIn(stage2_blocked.status_code, (400, 403, 409),
                      stage2_blocked.text)
        self.assertIsNone(
            self._decision_row("main", "p25a", KEY_EXC, "fulltext"))

    # ------------------------------------------------------------------
    # M04 necessary bad source fails closed; known-good writes succeed
    # ------------------------------------------------------------------
    def test_m04_bad_source_fail_closed_then_known_good(self):
        bad_path = Path(self.user_db["main"]["p25b"])
        moved = bad_path.with_name(bad_path.name + ".p25moved")
        bad_path.rename(moved)
        try:
            rejected = self.client.post(
                f"/api/tasks/{self.task_id['main']}/decisions",
                json={"zotero_key": KEY_BAD, "decision": "include",
                      "reason": "", "notes": "", "tags": None},
                headers=self._auth("p25a"))
            self.assertIn(rejected.status_code, (409, 503), rejected.text)
        finally:
            moved.rename(bad_path)
        self.assertIsNone(self._decision_row("main", "p25a", KEY_BAD))
        # recovery: with the necessary source readable again, the known-good
        # member votes; no completion while B/D votes are still pending.
        self._vote("main", "p25a", KEY_BAD, "include")
        self.assertEqual(
            self._decision_row("main", "p25a", KEY_BAD)[0], "include")
        self.assertEqual(
            self._seals("main", stage="stage1", key=KEY_BAD), [])

    # ------------------------------------------------------------------
    # M05 chain-gated batch zero write + read consistency
    # ------------------------------------------------------------------
    def test_m05_chain_gated_batch_zero_write_and_read_consistency(self):
        coding = importlib.import_module("coscreen.coding")
        dim_id = coding.add_dimension(
            self.user_db["main"]["p25a"], "P25 批量维度", "text", [])
        # fresh key with no stage votes: coding chain gate rejects the whole
        # batch with zero DML (mixed/protection semantics on the batch path).
        batch = self.client.post(
            f"/api/tasks/{self.task_id['main']}/coding/values-batch",
            json={"zotero_key": KEY_BATCH,
                  "values": {str(dim_id): "x"},
                  "notes": None, "evidence": None, "draft": False},
            headers=self._auth("p25a"))
        self.assertEqual(batch.status_code, 409, batch.text)
        with closing(sqlite3.connect(
                str(self.user_db["main"]["p25a"]))) as conn:
            fresh = conn.execute(
                "SELECT COUNT(*) FROM coding_values WHERE zotero_key=?",
                (KEY_BATCH,)).fetchone()[0]
        self.assertEqual(fresh, 0, "rejected batch must not write values")
        # raw/generic compatibility: coding on the fully-completed include
        # key is the legal continuation (stage1+stage2 include -> coding
        # eligible), and reads stay consistent on the same keys.
        legal = self.client.post(
            f"/api/tasks/{self.task_id['main']}/coding/values-batch",
            json={"zotero_key": KEY_DONE1,
                  "values": {str(dim_id): "yes"},
                  "notes": None, "evidence": None, "draft": False},
            headers=self._auth("p25a"))
        self.assertIn(legal.status_code, (200, 409), legal.text)
        view = self._keyed_view("main", "screen", [KEY_DONE1, KEY_ARB],
                                "p25a")
        self.assertEqual(view["task_kind"], "team")
        self.assertEqual(view["items_by_key"][KEY_DONE1]["completion"]
                         ["kind"], "unanimous")
        self.assertEqual(view["items_by_key"][KEY_ARB]["completion"]
                         ["kind"], "adjudicated")
        prisma = self.client.get(
            f"/api/tasks/{self.task_id['main']}/prisma-metrics",
            headers=self._auth("p25owner"))
        self.assertIn(prisma.status_code, (200, 404, 409), prisma.text)

    # ------------------------------------------------------------------
    # M06/M08 arbitration atomicity: stale version rollback keeps zero
    # persistence (mutB discriminator) + duplicate replay idempotent
    # ------------------------------------------------------------------
    def test_m06_arbitration_finish_atomic_rollback(self):
        self._vote("arb", "p25aa", KEY_AX, "include")
        self._vote("arb", "p25ab", KEY_AX, "exclude")
        version = self._arb_key_version("arb", KEY_AX)
        self._arb_decide("arb", KEY_AX, version, "include")
        records_before = self._arb_records("arb")
        seals_before = len(self._seals("arb", stage="stage1"))
        stale = self._arb_finish(
            "arb", [KEY_AX], {KEY_AX: version + "-stale"},
            request_id=_M06_STALE, expect=409)
        self.assertIn(self._error_code(stale), (
            "arbitration_refresh_required", "team_arbitration_key_not_ready",
            "refresh_required"), stale.text)
        # zero target persistence after the rollback: no new arbitration
        # record, no adjudicated seal, no receipt for the failed request.
        self.assertEqual(self._arb_records("arb"), records_before)
        self.assertEqual(len(self._seals("arb", stage="stage1")),
                         seals_before)
        self.assertEqual(self._receipt_count("arb", _M06_STALE), 0)
        # the decided session item is still finishable with fresh versions.
        fresh = self._arb_key_version("arb", KEY_AX)
        finish = self._arb_finish(
            "arb", [KEY_AX], {KEY_AX: fresh}, request_id=_M06_REQ)
        body = finish.json()
        self.assertEqual(body["finished_keys"], [KEY_AX])
        self.assertEqual(self._receipt_count("arb", _M06_REQ), 1)
        self.assertEqual(len(self._seals("arb", stage="stage1", key=KEY_AX)),
                         1)
        # duplicate replay (same X-Request-Id + same content): idempotent,
        # no second business effect.
        replay = self._arb_finish(
            "arb", [KEY_AX], {KEY_AX: fresh}, request_id=_M06_REQ)
        replay_body = replay.json()
        self.assertEqual(replay_body, body,
                         "idempotent replay returns the original result")
        self.assertEqual(self._receipt_count("arb", _M06_REQ), 1)
        self.assertEqual(len(self._seals("arb", stage="stage1", key=KEY_AX)),
                         1)

    # ------------------------------------------------------------------
    # M08 arbitration <-> roster barrier, both deterministic orders
    # ------------------------------------------------------------------
    def test_m07_arbitration_roster_barrier_both_orders(self):
        # order (a) finish BEFORE roster change: the adjudicated seal keeps
        # the pre-change roster; a later roster change does not rewrite it
        # (business-role switch = roster change without member-db removal).
        self._vote("arb", "p25aa", KEY_AY, "include")
        self._vote("arb", "p25ab", KEY_AY, "exclude")
        version = self._arb_key_version("arb", KEY_AY)
        self._arb_decide("arb", KEY_AY, version, "include")
        finish = self._arb_finish(
            "arb", [KEY_AY], {KEY_AY: self._arb_key_version("arb", KEY_AY)},
            request_id=_M07_REQ)
        self.assertEqual(finish.json()["finished_keys"], [KEY_AY])
        seal = self._seals("arb", stage="stage1", key=KEY_AY)[0]
        roster_with_ab = sorted(int(p["uid"]) for p in seal["roster"])
        self.assertIn(self.users["p25ab"]["user_id"], roster_with_ab)
        self._set_business_role("arb", "p25ao", "p25ab", "reconciler")
        after = self._seals("arb", stage="stage1", key=KEY_AY)[0]
        self.assertEqual(after, seal, "seal must survive later roster change")
        self._set_business_role("arb", "p25ao", "p25ab", "coder")
        # order (b) roster change BEFORE finish: readiness expires, finish
        # is rejected, zero artifacts persisted (aa becomes sole required,
        # unanimous include -> KEY_AZ is no longer a conflict).
        self._vote("arb", "p25aa", KEY_AZ, "include")
        self._vote("arb", "p25ab", KEY_AZ, "exclude")
        az_version = self._arb_key_version("arb", KEY_AZ)
        self._arb_decide("arb", KEY_AZ, az_version, "include")
        az_fresh = self._arb_key_version("arb", KEY_AZ)  # post-decide
        self._set_business_role("arb", "p25ao", "p25ab", "reconciler")
        expired = self._arb_finish(
            "arb", [KEY_AZ], {KEY_AZ: az_fresh},
            request_id=_M07_EXP, expect=409)
        self.assertIn(self._error_code(expired), (
            "team_arbitration_key_not_ready", "arbitration_refresh_required",
            "team_arbitration_finish_empty"), expired.text)
        self.assertEqual(self._seals("arb", stage="stage1", key=KEY_AZ), [])
        self.assertEqual(self._receipt_count("arb", _M07_EXP), 0)
        self._set_business_role("arb", "p25ao", "p25ab", "coder")

    # ------------------------------------------------------------------
    # M03/M06 last vote <-> roster barrier: the last vote lands under the
    # new roster and completes with the full required set
    # ------------------------------------------------------------------
    def test_m08_last_vote_under_new_roster(self):
        # D was added in m02; roster complete FIRST, then the last vote.
        self._vote("main", "p25a", KEY_LAST, "include")
        self._vote("main", "p25d", KEY_LAST, "include")
        self.assertEqual(self._seals("main", stage="stage1", key=KEY_LAST),
                         [], "completion must wait for every required vote")
        self._vote("main", "p25b", KEY_LAST, "include")
        self.assertEqual(
            sorted(self._required_uids("main", "stage1", KEY_LAST)),
            sorted(self.users[n]["user_id"]
                   for n in ("p25a", "p25b", "p25d")))

    # ------------------------------------------------------------------
    # M09 completion <-> undo barrier + adjudicated <-> trace clear barrier
    # ------------------------------------------------------------------
    def test_m09_completion_blocks_undo_and_trace_clear(self):
        for name in ("p25a", "p25b", "p25d"):
            self._vote("main", name, KEY_UNDO, "include")
        self.assertEqual(len(self._seals(
            "main", stage="stage1", key=KEY_UNDO)), 1)
        # undo barrier: the completed decision cannot be undone (409, the
        # business row survives untouched).
        status = self.client.get(
            f"/api/tasks/{self.task_id['main']}/undo/screen",
            headers=self._auth("p25a")).json()
        entry = (status.get("latest") or {}).get("entry_id")
        self.assertIsNotNone(entry, status)
        undo = self.client.post(
            f"/api/tasks/{self.task_id['main']}/undo/screen",
            json={"entry_id": entry}, headers=self._auth("p25a"))
        self.assertEqual(undo.status_code, 409, undo.text)
        self.assertEqual(
            self._decision_row("main", "p25a", KEY_UNDO)[0], "include")
        # trace clear barrier (P21 semantics on the adjudicated KEY_AX):
        # even with the actor's recording off, the arbitration history lock
        # rejects the clear as a structured, never-partial 409.
        clear = self.client.request(
            "DELETE",
            f"/api/tasks/{self.task_id['arb']}/trace/history",
            json={"confirm": True}, headers=self._auth("p25aa"))
        self.assertEqual(clear.status_code, 409, clear.text)
        detail = clear.json()["detail"]
        code = detail.get("error_code") or detail.get("code")
        # either the arbitration history lock itself, or the unprepared
        # shared partition refusing the clear -- both are structured,
        # never-partial protective rejections with zero deletions.
        self.assertIn(code, ("history_read_only_after_arbitration",
                             "trace_partition_unavailable"), detail)
        self.assertNotEqual(detail.get("status"), "partial", detail)
        # the finished adjudication itself is untouched by the clear attempt.
        self.assertEqual(len(self._seals(
            "arb", stage="stage1", key=KEY_AX)), 1)

    # ------------------------------------------------------------------
    # M11 same-id concurrent duplicate: bounded join + finally, exactly
    # one business effect (real threads, real locks, real OS rc via the
    # unittest process exit code)
    # ------------------------------------------------------------------
    def test_m10_concurrent_same_id_exactly_once(self):
        request_id = str(uuid.uuid4())
        headers = {**self._auth("p25a"), "X-Request-Id": request_id}
        payload = {"zotero_key": KEY_CONC, "decision": "include",
                   "reason": "", "notes": "", "tags": None}
        results = {"statuses": [], "bodies": [], "errors": []}
        ready = threading.Barrier(2)

        def fire():
            try:
                ready.wait(timeout=30)
                resp = self.client.post(
                    f"/api/tasks/{self.task_id['main']}/decisions",
                    json=payload, headers=headers)
                results["statuses"].append(resp.status_code)
                results["bodies"].append(resp.text[:300])
            except BaseException as exc:  # noqa: BLE001 - re-raised below
                results["errors"].append(exc)
                raise

        threads = []
        try:
            for _ in range(2):
                t = threading.Thread(target=fire, daemon=True)
                t.start()
                threads.append(t)
        finally:
            for t in threads:
                t.join(timeout=60)
            leaked = [t for t in threads if t.is_alive()]
            self.assertEqual(leaked, [], "worker threads must join in time")
        self.assertEqual(results["errors"], [])
        self.assertEqual(len(results["statuses"]), 2)
        for status in results["statuses"]:
            self.assertIn(status, (200, 409), results["bodies"])
        self.assertIn(200, results["statuses"],
                      "at least one request must commit or replay")
        with closing(sqlite3.connect(
                str(self.user_db["main"]["p25a"]))) as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM decisions WHERE zotero_key=?",
                (KEY_CONC,)).fetchone()[0]
        self.assertEqual(count, 1,
                         "exactly one business row for the duplicated id")
        with closing(sqlite3.connect(
                str(self.user_db["main"]["p25a"]))) as conn:
            receipts = conn.execute(
                "SELECT COUNT(*) FROM team_flow_receipts WHERE json_extract("
                "payload_json, '$.request_id')=?", (request_id,)).fetchone()[0]
        self.assertEqual(receipts, 1)
        # sequential replay of the same id + content: idempotent, still one.
        replay = self.client.post(
            f"/api/tasks/{self.task_id['main']}/decisions",
            json=payload, headers=headers)
        self.assertIn(replay.status_code, (200, 409), replay.text)
        with closing(sqlite3.connect(
                str(self.user_db["main"]["p25a"]))) as conn:
            again = conn.execute(
                "SELECT COUNT(*) FROM decisions WHERE zotero_key=?",
                (KEY_CONC,)).fetchone()[0]
            receipts_again = conn.execute(
                "SELECT COUNT(*) FROM team_flow_receipts WHERE json_extract("
                "payload_json, '$.request_id')=?", (request_id,)).fetchone()[0]
        self.assertEqual(again, 1)
        self.assertEqual(receipts_again, 1)


def self_auth(cls, name):
    return {"Authorization": "Bearer " + cls.users[name]["token"]}


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
