"""P17 appraisal & coding-consensus route tests (C12 author side; 2.1.6).

Four P17-named tests over the real route handlers
(``custom_backend.appraisal_api`` / ``custom_backend.coding_consensus_api``)
against the genuine P13/P15 stack: real ``WorkflowRuntime.mutate`` →
``run_team_mutation`` → ``require_team_write`` on B1-reserved native
connections (the P10 window entry is patched at the
``custom_backend.team_flow_commit`` seam, same shape as the integrated
``test_216_workflow_mutation_adapter`` harness).  No gate, writer, or store
function is mocked away; the only patches are capturing ``wraps=`` spies and
reconnect poison pills.

Requirement anchors (P17 spec):

    assert operation_target['coding_rater_submit'] == 'collab'
    assert operation_target['member_appraisal'] == 'actor'
"""
from __future__ import annotations

import importlib
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing, contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

TASK_ID = "p17-task-1"
INSTANCE_ID = "p17-instance-1"
OWNER, ALICE, BOB = 1, 2, 3
USER_NAMES = {OWNER: "owner", ALICE: "alice", BOB: "bob"}
ITEM_KEYS = ("K1", "K2", "K3")  # K3 never receives votes: chain-blocked key
_SEED = "2026-11-15T00:00:00+00:00"
_LIB = "a" * 32
_SHA = "b" * 64
_TOOL = "casp-qualitative"
_FIELD_A, _FIELD_B = "f-uuid-A", "f-uuid-B"


class _RouteApp:
    """Minimal route registrar: captures handlers, exposes app.state."""

    def __init__(self, runtime=None):
        self.state = SimpleNamespace(review_workflow=runtime)
        self.handlers = {}

    def _capture(self, method, path, **_kw):
        def decorator(fn):
            self.handlers[(method, path)] = fn
            return fn
        return decorator

    def get(self, path, **kw):
        return self._capture("GET", path, **kw)

    def post(self, path, **kw):
        return self._capture("POST", path, **kw)

    def put(self, path, **kw):
        return self._capture("PUT", path, **kw)


class AppraisalConsensusRouteTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="p17-routes-")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

        self.rwa = importlib.import_module("custom_backend.review_workflow_api")
        self.appraisal_api = importlib.import_module("custom_backend.appraisal_api")
        self.consensus_api = importlib.import_module(
            "custom_backend.coding_consensus_api")
        self.commit = importlib.import_module("custom_backend.team_flow_commit")
        self.checks = importlib.import_module("custom_backend.team_write_checks")
        self.flow_types = importlib.import_module("coscreen.team_flow_types")
        self.inputs_api = importlib.import_module("custom_backend.team_screening_write")
        self.tsr = importlib.import_module("custom_backend.team_screening_runtime")
        self.resource = importlib.import_module("coscreen.team_write_reservation")
        self.collab = importlib.import_module("coscreen.task_collaboration")
        self.db = importlib.import_module("coscreen.db")
        self.workflow = importlib.import_module("coscreen.review_workflow")
        self.facts = importlib.import_module("coscreen.team_terminal_facts")
        self.registry = importlib.import_module("coscreen.team_terminal_registry")
        self.receipts = importlib.import_module("coscreen.team_flow_receipts")
        self.tasks = importlib.import_module("coscreen.tasks")
        self.core = importlib.import_module("coscreen.appraisal")
        self.store = importlib.import_module("coscreen.coding_consensus")

        self.task_dir = self.root / "task"
        self.task_dir.mkdir()
        self.info = SimpleNamespace(task_id=TASK_ID, dir=self.task_dir, degraded=False)
        self.accounts_path = self.root / "users.db"
        self.collab_path = self.collab.shared_store_path(self.task_dir)
        slug = self.tasks.slugify_screener
        self.user_paths = {
            uid: self.task_dir / f"{slug(name)}.db" for uid, name in USER_NAMES.items()}
        self.owner_path = self.user_paths[OWNER]
        self.runtime = self.rwa.WorkflowRuntime(
            self._task_lookup, self._screener_db, self.root, self._visible)
        self.window_inputs = None
        self.gate_calls: list[tuple] = []
        self.mutate_calls: list[dict] = []
        self._prepare_databases()
        self._register_routes()
        # All team mutations run against the fixture window (genuine B1
        # reservation manager + real WorkflowContext), same seam as the
        # integrated P15 adapter tests.
        window_patcher = mock.patch.object(
            self.commit, "retained_team_write_inputs", self._window)
        window_patcher.start()
        self.addCleanup(window_patcher.stop)

    # ------------------------------------------------------------------
    # host plumbing
    # ------------------------------------------------------------------
    def _task_lookup(self, task_id):
        return self.info

    def _screener_db(self, info, username):
        return self.task_dir / f"{self.tasks.slugify_screener(username)}.db"

    def _visible(self, info, user):
        return True

    def _authorize(self, info, user, action):
        # Host-level participant authorization only: role separation between
        # coder (coding:submit) and reconciler (consensus:write/freeze) is
        # enforced by the real P12 gate inside the team window, not here.
        return user["user_id"] in USER_NAMES

    def _bad(self, message):
        return self.rwa.HTTPException(400, message)

    def _prepare_databases(self):
        with closing(sqlite3.connect(self.accounts_path)) as conn:
            conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, "
                         "username TEXT NOT NULL UNIQUE)")
            conn.executemany("INSERT INTO users(id,username) VALUES(?,?)",
                             [(uid, name) for uid, name in USER_NAMES.items()])
            conn.commit()
        self.collab.initialize_task_store(
            self.task_dir, task_id=TASK_ID, owner_user_id=OWNER,
            owner_username="owner")
        for name, role in (("alice", "coder"), ("bob", "coder"),
                           ("owner", "reconciler")):
            self.collab.grant_role(
                self.collab_path, actor_user_id=OWNER,
                user_id={v: k for k, v in USER_NAMES.items()}[name], role=role,
                task_id=TASK_ID)
        for uid, path in self.user_paths.items():
            self.db.init_db(path)  # real vote ledger + triggers
            if uid == OWNER:
                self.workflow.migrate(path)
            with closing(sqlite3.connect(path)) as conn:
                conn.execute("PRAGMA foreign_keys=ON")
                conn.executemany(
                    "INSERT INTO articles(zotero_key,title,is_duplicate_of,import_order)"
                    " VALUES(?,?,NULL,?)",
                    [(key, f"Title {key}", i) for i, key in enumerate(ITEM_KEYS)])
                if uid in (ALICE, BOB):
                    # both coders hold committed screen+fulltext "include" votes
                    # on K1/K2 -> live unanimity -> two-phase chain cleared there;
                    # K3 stays vote-free so its chain is blocked upstream.
                    for table in ("decisions", "stage2_decisions"):
                        conn.executemany(
                            f"INSERT INTO {table}(zotero_key,decision,exclusion_reason,"
                            "notes,tags,screened_at,updated_at)"
                            " VALUES(?,?,'','','', ?, ?)",
                            [(key, "include", _SEED, _SEED)
                             for key in ("K1", "K2")])
                conn.commit()
            self.facts.prepare_team_terminal_facts(path)
            self.receipts.prepare_team_flow_receipts(path)
        # collab-target operations (rater/consensus/freeze) write receipts and
        # seals through the retained collab connection too.
        self.facts.prepare_team_terminal_facts(self.collab_path)
        self.receipts.prepare_team_flow_receipts(self.collab_path)
        self.registry.prepare_team_terminal_registry(self.collab_path)
        self.store.prepare_coding_consensus_storage(self.collab_path)
        with closing(sqlite3.connect(self.collab_path)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("BEGIN IMMEDIATE")
            self.registry.register_protocol_on(
                conn, task_id=TASK_ID, task_instance_id=INSTANCE_ID,
                owner_user_id=OWNER, owner_library_id=_LIB,
                creation_path="trusted_new_task")
            for uid in (ALICE, BOB):
                path = self.user_paths[uid]
                with closing(sqlite3.connect(path)) as own:
                    library, _ = importlib.import_module(
                        "coscreen.team_vote_ledger").vote_snapshot(own, "stage1")
                self.registry.register_source_on(
                    conn, task_instance_id=INSTANCE_ID, source_user_id=uid,
                    source_library_id=library, source_path_identity=path.name)
            conn.commit()

    def _register_routes(self):
        self.appraisal_app = _RouteApp(self.runtime)
        self.appraisal_api.register_appraisal_routes(
            self.appraisal_app, lambda user: user, self._task_lookup,
            self._visible, self._screener_db,
            team_role_gate=self._record_gate)
        self.consensus_app = _RouteApp(self.runtime)
        self.consensus_api.register_coding_consensus_routes(
            self.consensus_app, lambda user: user, self._task_lookup,
            lambda info: str(self.collab_path), self._authorize,
            lambda info, item_key, field_key: item_key in ITEM_KEYS,
            self._bad, None, None, None)

    def _record_gate(self, info, user):
        self.gate_calls.append((info.task_id, user["user_id"]))

    @contextmanager
    def _window(self, runtime, info, user, *, target_kind, operation, item_keys):
        """P10 seam: genuine B1 reservation manager + real WorkflowContext."""
        actor_path = self.user_paths[user["user_id"]]
        paths = list(dict.fromkeys(
            (self.owner_path, self.accounts_path, *self.user_paths.values())))
        with self.resource.retained_write_connections(self.collab_path) as reserved:
            reserved.reserve(
                self.owner_path, [p for p in paths if p != self.owner_path])
            all_paths = list(dict.fromkeys((self.collab_path, *paths)))
            scope = self.tsr.borrowed_team_read_scope(
                self.info, accounts_path=self.accounts_path,
                collab_path=self.collab_path, owner_path=self.owner_path,
                member_paths=dict(self.user_paths),
                connections={p: reserved.connection(p) for p in all_paths},
                missing_members=frozenset())
            target_path = (actor_path if target_kind == "actor"
                           else self.collab_path)
            reserved.select_target(target_path)
            ctx = self.rwa.WorkflowContext(
                info=self.info, user=dict(user),
                owner={"owner_user_id": OWNER, "owner_username": "owner"},
                owner_db=self.owner_path, actor_db=actor_path,
                can_manage=user["user_id"] == OWNER,
                task_kind="team", collaboration_db=self.collab_path)
            role_state = scope.role_state()
            inputs = self.inputs_api.TeamWriteInputs(
                ctx=ctx, scope=scope, collab_conn=reserved.collab_conn,
                owner_conn=reserved.connection(self.owner_path),
                actor_conn=reserved.connection(actor_path),
                target_conn=reserved.target_conn,
                connections=reserved.connections,
                roster=tuple(dict(e) for e in role_state["participants"]),
                operation=operation, item_keys=tuple(item_keys),
                plan=self.flow_types.KeyedReadPlan(
                    binding=self.flow_types.TaskBinding(
                        task_id=TASK_ID, task_instance_id=INSTANCE_ID,
                        owner_user_id=OWNER, owner_library_id=_LIB),
                    operation=operation, item_keys=tuple(item_keys),
                    witnesses={}, live_dependencies={}, issues={},
                    selected_paths=(self.owner_path, actor_path)),
                _reserved=reserved)
            self.window_inputs = inputs
            yield inputs

    # -- route call helpers ---------------------------------------------------
    def _appraisal_put(self, user, key, responses=None):
        handler = self.appraisal_app.handlers[
            ("PUT", f"/api/tasks/{{task_id}}/appraisal/{{article_key}}")]
        return handler(TASK_ID, key,
                       {"tool": _TOOL, "responses": responses or {"1": "Yes"}},
                       dict(user))

    def _rater_post(self, user, key, field, value="code-a"):
        handler = self.consensus_app.handlers[
            ("POST", f"/api/tasks/{{task_id}}/coding/records")]
        body = self.consensus_api.RaterRecordIn(
            item_key=key, field_key=field, value=value,
            source={"origin": "test"}, rationale="r")
        return handler(TASK_ID, body, dict(user))

    def _consensus_post(self, user, key, field, rationale="consensus"):
        handler = self.consensus_app.handlers[
            ("POST", f"/api/tasks/{{task_id}}/coding/consensus")]
        body = self.consensus_api.ConsensusRecordIn(
            item_key=key, field_key=field, value="code-a",
            source={"origin": "test"}, rationale=rationale)
        return handler(TASK_ID, body, dict(user))

    def _freeze_post(self, user, ids, name="v1"):
        handler = self.consensus_app.handlers[
            ("POST", f"/api/tasks/{{task_id}}/coding/freezes")]
        body = self.consensus_api.FreezeIn(name=name, consensus_ids=list(ids))
        return handler(TASK_ID, body, dict(user))

    def _user(self, uid):
        return {"user_id": uid, "username": USER_NAMES[uid]}

    def _table_count(self, path, table):
        with closing(sqlite3.connect(path)) as conn:
            row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
        return row[0]

    def _pause_coding_stage(self):
        with closing(sqlite3.connect(self.owner_path)) as conn:
            row = conn.execute(
                "SELECT stages_json FROM workflow_state WHERE singleton=1"
            ).fetchone()
            stages = json.loads(row[0])
            stages["coding"] = {"mode": "paused", "source_ids": []}
            conn.execute("UPDATE workflow_state SET stages_json=? WHERE singleton=1",
                         (json.dumps(stages),))
            conn.commit()

    # ------------------------------------------------------------------
    # 1. appraisal: two-phase chain only, no extra paused policy; prepare
    #    lifecycle out of the PUT window
    # ------------------------------------------------------------------
    def test_appraisal_chain_without_extra_paused_policy(self):
        # spec anchor: member_appraisal is an actor-target operation
        operation_target = {
            name: self.checks.team_operation_target(
                self.flow_types.TeamOperation(name=name, stage="coding"))
            for name in ("member_appraisal", "coding_rater_submit")}
        self.assertEqual(operation_target["member_appraisal"], "actor")
        self.assertEqual(operation_target["coding_rater_submit"], "collab")

        # explicit prepare lifecycle (outside the write window) for alice only
        prepared = self.appraisal_api.prepare_appraisal_storage_for(
            self.info, "alice", self._screener_db)
        self.assertEqual(prepared, self.user_paths[ALICE])

        # coding stage paused: appraisal must NOT inherit the paused/source/
        # expected gates (C08 addendum: only the two-phase chain applies)
        self._pause_coding_stage()
        self.gate_calls.clear()
        result = self._appraisal_put(self._user(ALICE), "K1")
        self.assertTrue(result["saved"])
        self.assertEqual(result["article_key"], "K1")
        self.assertEqual(self.gate_calls, [],
                         "mutate path must not re-run the interim team_role_gate")
        with closing(sqlite3.connect(self.user_paths[ALICE])) as conn:
            row = conn.execute(
                "SELECT tool_id FROM appraisal_records "
                "WHERE article_key='K1' AND tool_id=?", (_TOOL,)).fetchone()
        self.assertIsNotNone(row, "the appraisal record must really be stored")

        # K3 has no screening votes: the two-phase chain still blocks it
        with self.assertRaises(self.rwa.HTTPException) as caught:
            self._appraisal_put(self._user(ALICE), "K3")
        self.assertEqual(caught.exception.status_code, 409)
        self.assertNotEqual(caught.exception.detail.get("code"), "stage_paused",
                            "paused must not be the blocking gate for appraisal")
        self.assertNotEqual(
            caught.exception.detail.get("code"), "article_outside_scope")

        # bob never ran the prepare lifecycle: the team window refuses with the
        # honest storage-unavailable 409 instead of creating tables in-window
        with self.assertRaises(self.rwa.HTTPException) as caught:
            self._appraisal_put(self._user(BOB), "K1")
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(
            caught.exception.detail["error_code"], "appraisal_storage_unavailable")
        with closing(sqlite3.connect(self.user_paths[BOB])) as conn:
            has_table = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND "
                "name='appraisal_records'").fetchone()
        self.assertIsNone(
            has_table, "the PUT window must never create appraisal storage")

    # ------------------------------------------------------------------
    # 2. coder rater vs reconciler consensus/freeze permissions and targets
    # ------------------------------------------------------------------
    def test_rater_vs_reconciler_targets(self):
        alice, owner = self._user(ALICE), self._user(OWNER)

        record = self._rater_post(alice, "K1", _FIELD_A)
        self.assertEqual(record["item_key"], "K1")
        self.assertEqual(record["rater_id"], str(ALICE))

        # reconciler (owner) is not a coder: the real P12 role gate denies
        # rater submit inside the window (host authorize passed on purpose)
        with self.assertRaises(self.rwa.HTTPException) as caught:
            self._rater_post(owner, "K1", _FIELD_A)
        self.assertEqual(caught.exception.status_code, 403)
        self.assertEqual(caught.exception.detail["params"]["action"],
                         "coding:submit")

        # reconciler appends consensus; the coder may not
        consensus = self._consensus_post(owner, "K1", _FIELD_A)
        self.assertEqual(consensus["item_key"], "K1")
        with self.assertRaises(self.rwa.HTTPException) as caught:
            self._consensus_post(alice, "K1", _FIELD_A)
        self.assertEqual(caught.exception.status_code, 403)
        self.assertEqual(caught.exception.detail["params"]["action"],
                         "consensus:write")

        # reconciler freezes; the coder may not
        freeze = self._freeze_post(owner, [consensus["id"]])
        self.assertEqual(freeze["name"], "v1")
        with self.assertRaises(self.rwa.HTTPException) as caught:
            self._freeze_post(alice, [consensus["id"]])
        self.assertEqual(caught.exception.status_code, 403)
        self.assertEqual(caught.exception.detail["params"]["action"],
                         "consensus:freeze")

        # S2: an unknown commit outcome is surfaced as a typed 409 for every
        # team operation (never 200+null, never swallowed by a generic body)
        unknown_env = {"outcome": "unknown", "idempotent": False,
                       "request_id": "req-unknown", "result": None,
                       "receipt": None, "seals": (), "cleanup_errors": (),
                       "headers": {}, "error": "RuntimeError: window exit failed"}
        with mock.patch.object(self.runtime, "mutate", return_value=unknown_env):
            for call in (lambda: self._appraisal_put(alice, "K1"),
                         lambda: self._rater_post(alice, "K1", _FIELD_A),
                         lambda: self._consensus_post(owner, "K1", _FIELD_B),
                         lambda: self._freeze_post(owner, [consensus["id"]],
                                                   name="v-unknown")):
                with self.assertRaises(self.rwa.HTTPException) as caught:
                    call()
                self.assertEqual(caught.exception.status_code, 409)
                self.assertEqual(caught.exception.detail["code"],
                                 "team_flow_unknown_outcome")
                self.assertTrue(caught.exception.detail["retryable"])
        self.assertEqual(caught.exception.detail["params"]["request_id"],
                         "req-unknown")

        # S4: only the two stable pre-yield drift literals still map to a
        # structured retryable 409 (P15-D1 narrow surface, package M); the
        # generic RuntimeError "collab store flipped" no longer qualifies.
        drift = ValueError(
            "retained_team_write_inputs requires a team task; got 'personal'")
        with mock.patch.object(self.runtime, "mutate", side_effect=drift):
            for call in (lambda: self._appraisal_put(alice, "K1"),
                         lambda: self._rater_post(alice, "K1", _FIELD_A),
                         lambda: self._consensus_post(owner, "K1", _FIELD_B),
                         lambda: self._freeze_post(owner, [consensus["id"]],
                                                   name="v-drift")):
                with self.assertRaises(self.rwa.HTTPException) as caught:
                    call()
                self.assertEqual(caught.exception.status_code, 409)
                self.assertEqual(caught.exception.detail["code"],
                                 "task_mode_changed")
                self.assertEqual(caught.exception.detail["message_key"],
                                 "team.flow.task_mode_changed")
                self.assertTrue(caught.exception.detail["retryable"])

        # both collab-target operations landed in the shared collaboration DB
        self.assertGreaterEqual(
            self._table_count(self.collab_path, "coding_rater_records"), 1)
        self.assertGreaterEqual(
            self._table_count(self.collab_path, "coding_consensus_records"), 1)
        self.assertGreaterEqual(self._table_count(self.collab_path, "coding_freezes"), 1)

    # ------------------------------------------------------------------
    # 2b. package M: deterministic typed failures stay non-retryable; a
    #     generic internal error is not disguised as a retryable mode drift
    # ------------------------------------------------------------------
    def test_deterministic_not_prepared_is_not_retryable(self):
        alice = self._user(ALICE)
        # appraisal storage itself is prepared, so the single deterministic
        # cause left is the receipts layout -> TeamFlowReceiptUnavailable
        # ("not_prepared") raised by the real store on the retained actor conn.
        self.appraisal_api.prepare_appraisal_storage_for(
            self.info, "alice", self._screener_db)
        with closing(sqlite3.connect(self.user_paths[ALICE])) as conn, conn:
            conn.execute("DROP TABLE IF EXISTS team_flow_receipts_meta")
            conn.execute("DROP TABLE IF EXISTS team_flow_receipts")

        with self.assertRaises(self.rwa.HTTPException) as caught:
            self._appraisal_put(alice, "K1")
        detail = caught.exception.detail
        # P17 storage-unprepared convention in this route family is a 409
        # (same family as appraisal_storage_unavailable), never a 503 that
        # gateways/clients read as "retry later".
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(detail["error_code"], "team_flow_receipt_unavailable")
        self.assertEqual(detail["message_key"], "team.flow.receipt_unavailable")
        self.assertIs(detail["retryable"], False,
                      "not_prepared is deterministic: retrying the same "
                      "request can never succeed")
        self.assertEqual(detail["params"]["reason"], "not_prepared")
        self.assertNotEqual(detail.get("code"), "task_mode_changed")
        # zero business write on the rejected path
        with closing(sqlite3.connect(self.user_paths[ALICE])) as conn:
            row = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND "
                "name='appraisal_records'").fetchone()
        self.assertIsNotNone(row, "the prepare lifecycle keeps its table")

        # same deterministic mapping for the coding-consensus team routes
        with closing(sqlite3.connect(self.collab_path)) as conn, conn:
            conn.execute("DROP TABLE IF EXISTS team_flow_receipts_meta")
            conn.execute("DROP TABLE IF EXISTS team_flow_receipts")
        with self.assertRaises(self.rwa.HTTPException) as caught:
            self._rater_post(alice, "K1", _FIELD_A)
        detail = caught.exception.detail
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(detail["error_code"], "team_flow_receipt_unavailable")
        self.assertIs(detail["retryable"], False)
        self.assertEqual(detail["params"]["reason"], "not_prepared")

        # typed transient unavailability keeps the P15/P17 503 payload
        transient = self.tsr.TeamFlowUnavailableError(
            "collab_unreadable", detail="probe")
        with mock.patch.object(self.runtime, "mutate", side_effect=transient):
            with self.assertRaises(self.rwa.HTTPException) as caught:
                self._consensus_post(self._user(OWNER), "K1", _FIELD_A)
        detail = caught.exception.detail
        self.assertEqual(caught.exception.status_code, 503)
        self.assertEqual(detail["error_code"], "team.flow.unavailable")
        self.assertEqual(detail["message_key"], "team.flow.unavailable")
        self.assertNotEqual(detail.get("code"), "task_mode_changed")
        self.assertNotIn("retryable", detail)

        # a borrowed-connection contract breach is a programming error: it is
        # never re-shaped into a business 409/503 payload
        contract = self.receipts.TeamFlowReceiptUnavailable(
            "connection_contract", "probe: caller must own a transaction")
        with mock.patch.object(self.runtime, "mutate", side_effect=contract):
            with self.assertRaises(self.receipts.TeamFlowReceiptUnavailable):
                self._rater_post(alice, "K1", _FIELD_A)

    def test_generic_exception_is_not_disguised_as_retryable_drift(self):
        alice = self._user(ALICE)
        self.appraisal_api.prepare_appraisal_storage_for(
            self.info, "alice", self._screener_db)
        with mock.patch.object(
                self.runtime, "mutate",
                side_effect=RuntimeError("collab store flipped mid-race")):
            with self.assertRaises(self.rwa.HTTPException) as caught:
                self._appraisal_put(alice, "K1")
        detail = caught.exception.detail
        # the appraisal route keeps its pre-existing outer degradation, but
        # the payload must not claim a retryable task-mode change
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(detail["error_code"], "appraisal_unavailable")
        self.assertNotEqual(detail.get("code"), "task_mode_changed")
        self.assertNotIn("retryable", detail)

        # the coding-consensus routes no longer fire a blanket 409 at all:
        # the real exception surfaces unchanged to the global handler
        with mock.patch.object(
                self.runtime, "mutate",
                side_effect=RuntimeError("collab store flipped mid-race")):
            with self.assertRaises(RuntimeError):
                self._rater_post(alice, "K1", _FIELD_A)

    # ------------------------------------------------------------------
    # 3. freeze resolves every ID on the collab connection; a mixed batch is
    #    rejected whole with zero writes; one window for the whole batch
    # ------------------------------------------------------------------
    def test_freeze_ids_mixed_batch_zero_write(self):
        owner = self._user(OWNER)
        first = self._consensus_post(owner, "K1", _FIELD_A)
        second = self._consensus_post(owner, "K2", _FIELD_B)
        self.assertEqual(self._table_count(self.collab_path, "coding_freezes"), 0)

        # mixed batch: one live ID + one that does not exist -> whole-batch
        # rejection (same store wording), zero freeze rows, no window opened
        run_spy = mock.patch.object(
            self.commit, "run_team_mutation",
            wraps=self.commit.run_team_mutation)
        with run_spy as spy:
            with self.assertRaises(self.rwa.HTTPException) as caught:
                self._freeze_post(owner, [first["id"], 999999])
        self.assertEqual(caught.exception.status_code, 400)
        self.assertIn("must exist", str(caught.exception.detail))
        self.assertEqual(spy.call_count, 0,
                         "an unresolvable batch must not even open a window")
        self.assertEqual(self._table_count(self.collab_path, "coding_freezes"), 0)

        # valid two-key batch: single mutate window carries every key and the
        # freeze row is written once for the whole batch
        with run_spy as spy:
            freeze = self._freeze_post(owner, [first["id"], second["id"]])
        self.assertEqual(spy.call_count, 1,
                         "the whole batch commits in one window, not per key")
        keys = spy.call_args.kwargs["item_keys"]
        self.assertEqual(sorted(keys), ["K1", "K2"])
        self.assertEqual(freeze["version"], 1)
        self.assertEqual(self._table_count(self.collab_path, "coding_freezes"), 1)

    # ------------------------------------------------------------------
    # 4. saves ride the retained guarded connection and cannot reconnect
    # ------------------------------------------------------------------
    def test_save_cannot_reconnect_under_guard(self):
        alice, owner = self._user(ALICE), self._user(OWNER)
        self.appraisal_api.prepare_appraisal_storage_for(
            self.info, "alice", self._screener_db)
        captured = {}

        real_save = self.core.save_record
        with mock.patch.object(
                self.core, "save_record",
                side_effect=lambda conn, *a, **kw: (
                    captured.__setitem__("conn", conn),
                    real_save(conn, *a, **kw))[1]), \
             mock.patch.object(
                 self.db, "_connect",
                 side_effect=AssertionError(
                     "path-only _connect inside the team write window")):
            result = self._appraisal_put(alice, "K1")
        self.assertTrue(result["saved"])
        self.assertIs(captured["conn"], self.window_inputs.actor_conn,
                      "appraisal save must write through the retained actor "
                      "connection (same object as the checked/committed target)")

        freeze_captured = {}
        real_freeze = self.store.freeze_consensus

        def _freeze_spy(*args, **kwargs):
            freeze_captured["conn"] = kwargs.get("conn")
            return real_freeze(*args, **kwargs)

        consensus = self._consensus_post(owner, "K1", _FIELD_A)
        with mock.patch.object(self.store, "freeze_consensus",
                               side_effect=_freeze_spy), \
             mock.patch.object(
                 self.store, "_connect",
                 side_effect=AssertionError(
                     "store _connect fallback inside the team write window")):
            freeze = self._freeze_post(owner, [consensus["id"]])
        self.assertEqual(freeze["version"], 1)
        self.assertIs(freeze_captured["conn"], self.window_inputs.collab_conn,
                      "freeze must write through the retained collab connection")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
