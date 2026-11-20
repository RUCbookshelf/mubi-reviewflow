"""HIGH#2 regression tests: removing a member who holds completed seals.

Product semantics (2026-11-15 ruling anchor, P14 spec): removal of a member
holding >= 1 completed seal must SUCCEED — the old-set seals/receipts are
archived into the collab store (byte-faithful copies), the completed-stage
conclusions stay untouched, roster revision advances and the same-commit
management receipt lands.  Pre-fix reality: ``_archive_retiring_source`` read
``seal["stage"]``/``seal["item_key"]`` at record top level, but
``_record_from_row``/``_build_record`` (coscreen/team_terminal_facts.py) put
those fields inside ``payload`` — removal crashed with ``KeyError('stage')``
(surfacing as 503 ``team_management_uncommitted`` leaking internal details).
The fallback requirement: a truly unexpected record shape must fail as a
typed, structured rejection without leaking internal key names.

Three lanes:
1. coordinator lane (fixture recipe of test_216_team_lifecycle.py=3f6ee9ed):
   seal-holding removal succeeds end to end; no-seal control stays green;
   bad-shape record -> typed rejection, no internal leak;
2. HTTP lane (fixture recipe of test_216_management_routes.py): the real
   DELETE role endpoint over TestClient returns 200 for a seal-holding
   member (the exact reported 503 symptom).

Product imports are deferred to test execution; this file must stay loadable
under ``python -I -B``.  Env whitelist: ``COBOOKSHELF_DATA_DIR`` +
``REVIEWFLOW_MODE=server`` only (pinned by conftest).
"""
from __future__ import annotations

import hashlib
import importlib
import json
import sqlite3
import tempfile
import unittest
import uuid
from contextlib import closing, contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

TASK_ID = "high2-task-1"
INSTANCE_ID = "high2-instance-1"
OWNER, ALICE, BOB, CAROL = 1, 2, 3, 4
ACCOUNT_NAMES = {OWNER: "owner", ALICE: "alice", BOB: "bob", CAROL: "carol"}
DB_NAMES = {OWNER: "owner", ALICE: "alice", BOB: "bob", CAROL: "carol"}
ITEM_KEYS = ("K1",)
_SEED = "2026-11-15T00:00:00+00:00"
_SEALED_AT = "2026-11-18T08:00:00.000Z"
_LIB = "a" * 32


def _canonical_json(value) -> str:
    """Canonical JSON mirror of the F1 contract serialization parameters."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _observation_payload(*, task_instance_id, item_key, stage, decision,
                         owner_uid, required_uids, roster_usernames,
                         observed_by_user_id, sealed_at=_SEALED_AT):
    """Self-consistent v2 observation seal payload (P08 recipe, condensed).

    The payload is internally consistent only (append_seal_on does not
    cross-check the F2 registry), so a minimal roster covering one owner and
    the required coders suffices.
    """
    facts = importlib.import_module("coscreen.team_terminal_facts")
    roster = [{
        "uid": owner_uid, "username": roster_usernames[owner_uid],
        "role": "reconciler", "is_owner": True,
        "screening_enabled": False, "required_screener": False,
        "source": "owner-source", "config": "ok",
    }]
    for uid in sorted(required_uids):
        roster.append({
            "uid": uid, "username": roster_usernames[uid], "role": "coder",
            "is_owner": False, "screening_enabled": None,
            "required_screener": True, "source": "grant-source",
            "config": "ok",
        })
    opinions = []
    for uid in sorted(required_uids):
        source_row = {"tags": f"tag-{uid}",
                      "screened_at": "2026-01-02 03:04:05",
                      "updated_at": None}
        content = {"decision": decision, "exclusion_reason": "", "notes": ""}
        opinions.append({
            "uid": uid, "decision": decision, "exclusion_reason": "",
            "notes": "", "library_id": "%032x" % uid, "revision": uid + 10,
            "source_row": source_row,
            "opinion_sha256": hashlib.sha256(
                _canonical_json(content).encode("utf-8")).hexdigest(),
            "source_row_sha256": hashlib.sha256(
                _canonical_json(source_row).encode("utf-8")).hexdigest(),
        })
    bindings = [
        {"source_user_id": uid, "registration_id": "fixture-reg-%d" % uid,
         "generation": 1, "source_library_id": "%032x" % uid}
        for uid in sorted(set(required_uids) | {observed_by_user_id})
    ]
    return {
        "schema": facts.OBSERVATION_PAYLOAD_SCHEMA,
        "task": {"task_id": TASK_ID, "task_instance_id": task_instance_id,
                 "owner_user_id": owner_uid,
                 "owner_username": roster_usernames[owner_uid],
                 "owner_library_id": "%032x" % owner_uid},
        "stage": stage, "item_key": item_key, "round_id": None,
        "kind": "unanimous", "trigger": "current_observation",
        "final_decision": decision, "roster": roster,
        "required_uids": sorted(required_uids), "opinions": opinions,
        "arbitration": None,
        "protocol": {"source_registration_id": "fixture-reg-%d"
                     % observed_by_user_id, "roster_revision": 1},
        "source_request": {"operation": "completion_observe",
                           "request_id": "request-high2-1",
                           "request_sha256": "b" * 64},
        "sealed_at": sealed_at,
        "observation": {"host_operation": "completion_observe",
                        "host_stage": "screen", "host_item_keys": [item_key],
                        "observed_by_user_id": observed_by_user_id,
                        "basis": "current_snapshot"},
        "source_bindings": bindings,
    }


def _add_actor_seal(db_path, payload):
    """Append one real actor seal into a member library (borrowed writer)."""
    facts = importlib.import_module("coscreen.team_terminal_facts")
    conn = sqlite3.connect(str(db_path), timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("BEGIN IMMEDIATE")
        record = facts.append_seal_on(conn, payload, origin="actor")
        conn.commit()
        return record
    finally:
        try:
            conn.close()
        except sqlite3.Error:
            pass


def _seal_rows(db_path):
    with closing(sqlite3.connect(str(db_path))) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(
            "SELECT seal_id, stage, item_key, origin, payload_sha256,"
            " payload_json FROM team_terminal_seals")]


class High2SealRemovalTests(unittest.TestCase):
    """Coordinator lane: run_team_management('remove') with/without seals."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="high2-remove-")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

        self.lifecycle = importlib.import_module(
            "custom_backend.team_source_lifecycle")
        self.inputs_api = importlib.import_module(
            "custom_backend.team_screening_write")
        self.tsr = importlib.import_module(
            "custom_backend.team_screening_runtime")
        self.resource = importlib.import_module(
            "coscreen.team_write_reservation")
        self.collab = importlib.import_module("coscreen.task_collaboration")
        self.db = importlib.import_module("coscreen.db")
        self.workflow = importlib.import_module("coscreen.review_workflow")
        self.ledger = importlib.import_module("coscreen.team_vote_ledger")
        self.facts = importlib.import_module("coscreen.team_terminal_facts")
        self.registry = importlib.import_module(
            "coscreen.team_terminal_registry")
        self.receipts = importlib.import_module(
            "coscreen.team_flow_receipts")
        self.tasks = importlib.import_module("coscreen.tasks")

        self._build_store()

    # ------------------------------------------------------------------
    # fixture (recipe of test_216_team_lifecycle.py=3f6ee9ed)
    # ------------------------------------------------------------------
    def _build_store(self):
        self.task_dir = self.root / "task"
        self.task_dir.mkdir()
        self.info = SimpleNamespace(
            task_id=TASK_ID, dir=self.task_dir, degraded=False)
        self.accounts_path = self.root / "users.db"
        self.collab_path = self.collab.shared_store_path(self.task_dir)
        slug = self.tasks.slugify_screener
        self.user_paths = {
            uid: self.task_dir / f"{slug(name)}.db"
            for uid, name in DB_NAMES.items()}
        self.owner_path = self.user_paths[OWNER]

        with closing(sqlite3.connect(self.accounts_path)) as conn:
            conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, "
                         "username TEXT NOT NULL UNIQUE)")
            conn.executemany("INSERT INTO users(id,username) VALUES(?,?)",
                             [(uid, name) for uid, name in
                              ACCOUNT_NAMES.items()])
            conn.commit()
        self.collab.initialize_task_store(
            self.task_dir, task_id=TASK_ID, owner_user_id=OWNER,
            owner_username="owner")
        name_to_uid = {v: k for k, v in ACCOUNT_NAMES.items()}
        for name, role in (("alice", "coder"), ("bob", "coder"),
                           ("carol", "reconciler"), ("owner", "reconciler")):
            self.collab.grant_role(
                self.collab_path, actor_user_id=OWNER,
                user_id=name_to_uid[name], role=role, task_id=TASK_ID)
        for uid, path in self.user_paths.items():
            self.db.init_db(path)
            if uid == OWNER:
                self.workflow.migrate(path)
            with closing(sqlite3.connect(path)) as conn:
                conn.execute("PRAGMA foreign_keys=ON")
                conn.executemany(
                    "INSERT INTO articles(zotero_key,title,is_duplicate_of,"
                    "import_order) VALUES(?,?,NULL,?)",
                    [(key, f"Title {key}", i)
                     for i, key in enumerate(ITEM_KEYS)])
                conn.commit()
            self.facts.prepare_team_terminal_facts(path)
            self.receipts.prepare_team_flow_receipts(path)
        self.lifecycle.prepare_team_protocol_storage(
            collab_path=self.collab_path, source_paths=())
        with closing(sqlite3.connect(self.collab_path)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("BEGIN IMMEDIATE")
            self.registry.register_protocol_on(
                conn, task_id=TASK_ID, task_instance_id=INSTANCE_ID,
                owner_user_id=OWNER, owner_library_id=_LIB,
                creation_path="trusted_new_task")
            for uid, path in self.user_paths.items():
                with closing(sqlite3.connect(path)) as own:
                    library, _rows = self.ledger.vote_snapshot(own, "stage1")
                self.registry.register_source_on(
                    conn, task_instance_id=INSTANCE_ID, source_user_id=uid,
                    source_library_id=library,
                    source_path_identity=path.name)
            conn.commit()
        self.runtime = SimpleNamespace(host="isolated-test")

    # ------------------------------------------------------------------
    # window seam (recipe of test_216_team_lifecycle.py=3f6ee9ed)
    # ------------------------------------------------------------------
    @contextmanager
    def _window(self, runtime, info, user, *, target_kind, operation,
                item_keys):
        assert target_kind == "collab", "management must borrow the collab"
        paths = list(dict.fromkeys(
            (self.owner_path, self.accounts_path,
             *self.user_paths.values())))
        with self.resource.retained_write_connections(self.collab_path) \
                as reserved:
            reserved.reserve(
                self.owner_path, [p for p in paths if p != self.owner_path])
            all_paths = list(dict.fromkeys(
                (self.collab_path, *paths)))
            scope = self.tsr.borrowed_team_read_scope(
                self.info, accounts_path=self.accounts_path,
                collab_path=self.collab_path, owner_path=self.owner_path,
                member_paths=dict(self.user_paths),
                connections={p: reserved.connection(p) for p in all_paths},
                missing_members=frozenset())
            reserved.select_target(self.collab_path)
            ctx = SimpleNamespace(
                info=self.info, task_id=TASK_ID, user=dict(user),
                owner={"owner_user_id": OWNER}, owner_db=self.owner_path,
                actor_db=self.owner_path,
                collaboration_db=self.collab_path,
                task_kind="team", can_manage=user["user_id"] == OWNER)
            role_state = scope.role_state()
            yield self.inputs_api.TeamWriteInputs(
                ctx=ctx, scope=scope, collab_conn=reserved.collab_conn,
                owner_conn=reserved.connection(self.owner_path),
                actor_conn=reserved.connection(self.owner_path),
                target_conn=reserved.target_conn,
                connections=reserved.connections,
                roster=tuple(dict(e) for e in role_state["participants"]),
                operation=operation, item_keys=tuple(item_keys),
                plan=self._plan(operation, item_keys),
                _reserved=reserved)

    def _plan(self, operation, item_keys):
        flow_types = importlib.import_module("coscreen.team_flow_types")
        return flow_types.KeyedReadPlan(
            binding=flow_types.TaskBinding(
                task_id=TASK_ID, task_instance_id=INSTANCE_ID,
                owner_user_id=OWNER, owner_library_id=_LIB),
            operation=operation, item_keys=tuple(item_keys),
            witnesses={}, live_dependencies={}, issues={},
            selected_paths=(self.owner_path, self.collab_path))

    def _manage(self, *, action, target, payload=None, request_id=None):
        request_id = request_id or f"req-{action}-{target}"
        with mock.patch.object(self.inputs_api,
                               "retained_team_write_inputs", self._window):
            return self.lifecycle.run_team_management(
                self.runtime, self.info,
                {"user_id": OWNER, "username": "owner"},
                action=action, target_user_id=target,
                payload=payload, request_id=request_id,
                request_sha256="b" * 64)

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _seed_actor_receipt(self, uid: int, request_id: str):
        path = self.user_paths[uid]
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("BEGIN")
            self.receipts.append_receipt_on(conn, {
                "schema": "team_flow_receipt.v1",
                "task_instance_id": INSTANCE_ID,
                "actor_user_id": uid,
                "operation": "member_vote:screen",
                "request_id": request_id,
                "request_sha256": "d" * 64,
                "result": {"request_id": request_id, "saved": ["K1"]},
                "internal_evidence": {"target_kind": "actor"},
                "recorded_at": "2026-11-19T00:00:00.000Z",
                "source_registration_id": "fixture-reg",
                "source_generation": 1,
            })
            conn.commit()
        finally:
            conn.close()

    def _seed_actor_seal(self, uid: int):
        return _add_actor_seal(
            self.user_paths[uid],
            _observation_payload(
                task_instance_id=INSTANCE_ID, item_key="K1",
                stage="stage1", decision="include", owner_uid=OWNER,
                required_uids=(ALICE, BOB),
                roster_usernames={OWNER: "owner", ALICE: "alice",
                                  BOB: "bob"},
                observed_by_user_id=uid))

    def _management_receipt_payloads(self):
        with closing(sqlite3.connect(self.collab_path)) as conn:
            return [json.loads(r[0]) for r in conn.execute(
                "SELECT payload_json FROM team_flow_receipts "
                "WHERE origin='collab'")]

    def _archived_receipt_payloads(self):
        with closing(sqlite3.connect(self.collab_path)) as conn:
            return [json.loads(r[0]) for r in conn.execute(
                "SELECT payload_json FROM team_flow_receipts "
                "WHERE origin='actor_archive'")]

    def _protocol(self):
        with closing(sqlite3.connect(self.collab_path)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("BEGIN")
            record = self.registry.read_protocol_on(
                conn, task_instance_id=INSTANCE_ID)
            conn.rollback()
            return record

    def _grant_count(self, uid: int, role: str) -> int:
        with closing(sqlite3.connect(self.collab_path)) as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM collaboration_role_grants "
                "WHERE user_id=? AND role=?", (uid, role)).fetchone()
            return int(row[0])

    def _source(self, uid: int):
        with closing(sqlite3.connect(self.collab_path)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("BEGIN")
            record = self.registry.active_source_on(
                conn, task_instance_id=INSTANCE_ID, source_user_id=uid)
            conn.rollback()
            return record

    # ------------------------------------------------------------------
    # 1. main acceptance: removing a seal-holding member succeeds
    # ------------------------------------------------------------------
    def test_remove_member_with_completed_seal_succeeds(self):
        seeded = self._seed_actor_seal(BOB)
        seal_id = seeded["seal_id"]
        member_rows_before = _seal_rows(self.user_paths[BOB])
        self.assertEqual(len(member_rows_before), 1)
        self._seed_actor_receipt(BOB, "bob-actor-receipt-high2")
        previous_revision = int(self._protocol()["roster_revision"])

        removed = self._manage(
            action="remove", target=BOB, payload={"role": "coder"},
            request_id="req-high2-remove-seal")

        # 200-equivalent: the coordinator committed the whole change.
        self.assertEqual(removed["outcome"], "committed")
        self.assertTrue(removed["result"]["writer"]["revoked"])
        self.assertEqual(self._grant_count(BOB, "coder"), 0)
        # revision advanced and is reported on the outcome
        self.assertEqual(removed["roster_revision"], previous_revision + 1)
        self.assertEqual(int(self._protocol()["roster_revision"]),
                         previous_revision + 1)
        self.assertIsNone(self._source(BOB), "retired source must close")

        # archive evidence landed inside the same-commit receipt, with
        # stage/item_key read from the payload (the pre-fix KeyError point).
        evidence = self._management_receipt_payloads()[-1][
            "internal_evidence"]["archive_evidence"]
        self.assertEqual(evidence["seal_count"], 1)
        self.assertEqual(evidence["seals"][0]["seal_id"], seal_id)
        self.assertEqual(evidence["seals"][0]["stage"], "stage1")
        self.assertEqual(evidence["seals"][0]["item_key"], "K1")
        self.assertTrue(evidence["verified_per_copy"])

        # byte-faithful archive copy lives in the collab store
        collab_rows = _seal_rows(self.collab_path)
        archived = [r for r in collab_rows if r["origin"] == "actor_archive"]
        self.assertEqual(len(archived), 1)
        self.assertEqual(archived[0]["seal_id"], seal_id)
        self.assertEqual(archived[0]["stage"], "stage1")
        self.assertEqual(archived[0]["item_key"], "K1")
        self.assertEqual(archived[0]["payload_sha256"],
                         member_rows_before[0]["payload_sha256"])
        self.assertEqual(archived[0]["payload_json"],
                         member_rows_before[0]["payload_json"])

        # completed-stage conclusions untouched: the member library keeps
        # its original seal row byte-identical (no physical cleanup), and
        # the actor receipt is archived (kept in the collab store).
        member_rows_after = _seal_rows(self.user_paths[BOB])
        self.assertEqual(member_rows_after, member_rows_before)
        archived_requests = {p["request_id"]
                             for p in self._archived_receipt_payloads()}
        self.assertEqual(archived_requests, {"bob-actor-receipt-high2"})

        # idempotent replay stays a clean committed no-op
        replay = self._manage(
            action="remove", target=BOB, payload={"role": "coder"},
            request_id="req-high2-remove-seal")
        self.assertTrue(replay["idempotent"])
        self.assertEqual(replay["outcome"], "committed")

    # ------------------------------------------------------------------
    # 2. control: removal without seals still succeeds (was already green)
    # ------------------------------------------------------------------
    def test_remove_member_without_seal_still_succeeds(self):
        self._seed_actor_receipt(BOB, "bob-actor-receipt-plain")
        previous_revision = int(self._protocol()["roster_revision"])

        removed = self._manage(
            action="remove", target=BOB, payload={"role": "coder"},
            request_id="req-high2-remove-noseal")

        self.assertEqual(removed["outcome"], "committed")
        self.assertEqual(removed["roster_revision"], previous_revision + 1)
        evidence = self._management_receipt_payloads()[-1][
            "internal_evidence"]["archive_evidence"]
        self.assertEqual(evidence["seal_count"], 0)
        self.assertEqual(self._grant_count(BOB, "coder"), 0)

    # ------------------------------------------------------------------
    # 3. fallback: truly unexpected record shape -> typed rejection,
    #    no internal details leaked (safety net, not the main behavior)
    # ------------------------------------------------------------------
    def test_unexpected_seal_shape_is_typed_rejection(self):
        bad_shape = {"seal_id": "x" * 64, "stage": "stage1",
                     "item_key": "K1", "payload_sha256": "y" * 64,
                     "sealed_at": _SEALED_AT}  # legacy/foreign shape: no
        # "payload" mapping at all — outside the F1 read-path contract.
        with mock.patch.object(
                self.lifecycle, "list_seals_on",
                lambda conn, **kw: [dict(bad_shape)]), \
             mock.patch.object(
                self.lifecycle, "archive_seals_on",
                lambda conn, seals, **kw: [dict(s) for s in seals]):
            with self.assertRaises(self.lifecycle.TeamManagementError) \
                    as caught:
                self._manage(
                    action="remove", target=BOB, payload={"role": "coder"},
                    request_id="req-high2-badshape")
        self.assertEqual(caught.exception.code, "retirement_source_unreadable")
        leaked = str(caught.exception)
        self.assertNotIn("KeyError", leaked)
        self.assertNotIn("'stage'", leaked)


class High2SealRemovalHttpTests(unittest.TestCase):
    """HTTP lane: the real DELETE role endpoint, seal-holding member.

    The reported symptom was exactly this endpoint answering 503
    ``team_management_uncommitted`` (details leaking ``KeyError('stage')``)
    whenever the removed member held a completed seal.  Post-fix the same
    request must answer 200 with the roster committed.
    """

    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        from custom_backend import auth as auth_mod
        from custom_backend import main as main_mod

        cls.client = TestClient(main_mod.app)
        cls.client.__enter__()
        cls.main_mod = main_mod
        cls.users = {}
        for name in ("high2owner", "high2membera", "high2memberb"):
            user = auth_mod.create_user(name, "High2-Remove-2026")
            cls.users[name] = {
                "user_id": user["id"], "username": user["username"],
                "token": auth_mod.create_access_token(
                    user["id"], user["username"])}
        cls._team_task()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    @classmethod
    def _auth(cls, name):
        return {"Authorization": "Bearer " + cls.users[name]["token"]}

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

        owner = cls.users["high2owner"]
        owner_uid = owner["user_id"]
        cls.task_id = cls.client.post(
            "/api/tasks", json={"name": "high2-http-" + uuid.uuid4().hex[:8]},
            headers=cls._auth("high2owner")).json()["task_id"]
        cls.info = tasks.find_task(cls.task_id, cls.main_mod.DATA_DIR)
        task_dir = cls.info.dir
        cls.collab_path = collab.shared_store_path(task_dir)
        if not cls.collab_path.is_file():
            collab.initialize_task_store(
                task_dir, task_id=cls.task_id, owner_user_id=owner_uid,
                owner_username=owner["username"])
        with closing(sqlite3.connect(str(cls.collab_path))) as conn:
            conn.row_factory = sqlite3.Row
            cls.instance_id = conn.execute(
                "SELECT task_instance_id FROM main.team_task_protocol "
                "LIMIT 1").fetchone()["task_instance_id"]
        collab.set_business_role(
            cls.collab_path, actor_user_id=owner_uid, user_id=owner_uid,
            business_role="reconciler", task_id=cls.task_id)
        slug = tasks.slugify_screener
        cls.user_db = {}
        for name in ("high2owner", "high2membera", "high2memberb"):
            uid = cls.users[name]["user_id"]
            if name != "high2owner":
                collab.grant_role(
                    cls.collab_path, actor_user_id=owner_uid, user_id=uid,
                    role="coder", task_id=cls.task_id)
                collab.set_business_role(
                    cls.collab_path, actor_user_id=owner_uid, user_id=uid,
                    business_role="coder", task_id=cls.task_id)
            cls.user_db[name] = task_dir / f"{slug(name)}.db"
        articles = [(k, f"HIGH2 {k}", i) for i, k in enumerate(("K1",))]
        for name, path in cls.user_db.items():
            cdb.init_db(path)
            if name == "high2owner":
                wf.migrate(path)
            tf.prepare_team_terminal_facts(path)
            receipts.prepare_team_flow_receipts(path)
            conn = sqlite3.connect(str(path), timeout=15)
            try:
                conn.execute("PRAGMA foreign_keys=ON")
                uid = cls.users[name]["user_id"]
                if name != "high2owner":
                    conn.execute(
                        "INSERT INTO team_vote_revision_meta (singleton,"
                        " format_version, library_id, seeded_stage1,"
                        " seeded_stage2, advances)"
                        " VALUES (1, 2, ?, 0, 0, 0)"
                        " ON CONFLICT(singleton) DO UPDATE SET"
                        " library_id=excluded.library_id", ("%032x" % uid,))
                conn.executemany(
                    "INSERT INTO articles (zotero_key, title,"
                    " is_duplicate_of, import_order)"
                    " VALUES (?, ?, NULL, ?)", articles)
                if name == "high2owner":
                    conn.execute(
                        "UPDATE workflow_state SET team_latched=1"
                        " WHERE singleton=1")
                conn.commit()
            finally:
                conn.close()
        tsl.prepare_team_protocol_storage(
            collab_path=cls.collab_path, source_paths=())
        for name in ("high2membera", "high2memberb"):
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
                    source_path_identity=token)
                writer.commit()
            finally:
                try:
                    writer.close()
                except sqlite3.Error:
                    pass

    def test_revoke_seal_holding_member_returns_200(self):
        member_a = self.users["high2membera"]
        member_b = self.users["high2memberb"]
        payload = _observation_payload(
            task_instance_id=self.instance_id, item_key="K1",
            stage="stage1", decision="include",
            owner_uid=self.users["high2owner"]["user_id"],
            required_uids=(member_a["user_id"], member_b["user_id"]),
            roster_usernames={
                self.users["high2owner"]["user_id"]: "high2owner",
                member_a["user_id"]: "high2membera",
                member_b["user_id"]: "high2memberb"},
            observed_by_user_id=member_a["user_id"])
        seeded = _add_actor_seal(self.user_db["high2membera"], payload)
        seal_id = seeded["seal_id"]

        with closing(sqlite3.connect(str(self.collab_path))) as conn:
            before_revision = conn.execute(
                "SELECT roster_revision FROM team_task_protocol"
                " WHERE task_instance_id=?",
                (self.instance_id,)).fetchone()[0]

        response = self.client.delete(
            f"/api/tasks/{self.task_id}/collaboration/roles/"
            f"{member_a['user_id']}/coder",
            headers=self._auth("high2owner"))

        # the exact reported symptom (503 team_management_uncommitted with
        # KeyError('stage') details) must be gone: removal commits.
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertTrue(body["revoked"])
        self.assertTrue(body["membership_committed"])
        self.assertIsNotNone(body["roster_revision"])

        # durable archive copy landed in the collab store
        with closing(sqlite3.connect(str(self.collab_path))) as conn:
            row = conn.execute(
                "SELECT stage, item_key, payload_sha256 FROM"
                " team_terminal_seals WHERE seal_id=?", (seal_id,)).fetchone()
            after_revision = conn.execute(
                "SELECT roster_revision FROM team_task_protocol"
                " WHERE task_instance_id=?",
                (self.instance_id,)).fetchone()[0]
        self.assertIsNotNone(row, "archive copy must live in collab store")
        self.assertEqual(row[0], "stage1")
        self.assertEqual(row[1], "K1")
        self.assertGreaterEqual(after_revision, before_revision + 1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
