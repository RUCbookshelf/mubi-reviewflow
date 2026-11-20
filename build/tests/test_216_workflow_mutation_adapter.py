"""P15 workflow mutation adapter tests (C12 author side; 2.1.6).

Four P15-named tests for ``custom_backend.review_workflow_api.WorkflowRuntime
.mutate``.  Product imports and SQLite fixture work are deferred to unittest
execution; this file must stay loadable under ``python -I -B``.

The team fixture reuses the proven P13 S2 harness shape (real collaboration
roles, real vote ledger via ``coscreen.db.init_db``, B1-reserved native
connections, TSR borrowed scope) on top of the genuine B1 reservation
manager (``retained_write_connections``).  The P10 window entry
(``retained_team_write_inputs``) is patched at the product module seam
(``custom_backend.team_flow_commit``) with a window that yields a **real
``WorkflowContext``** (the dataclass the adapter binds), because the
adapter under test is the real ``WorkflowRuntime.mutate``; the solo fixture
uses a second real runtime over a legacy task directory.
"""
from __future__ import annotations

import importlib
import sqlite3
import tempfile
import unittest
from contextlib import closing, contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

TASK_ID = "adapter-task-1"
SOLO_ID = "solo-task-1"
INSTANCE_ID = "adapter-instance-1"
OWNER, ALICE, BOB = 1, 2, 3
USER_NAMES = {OWNER: "owner", ALICE: "alice", BOB: "bob"}
ITEM_KEYS = ("K1", "K2")
_SEED = "2026-11-15T00:00:00+00:00"
_LIB = "a" * 32
_SHA = "b" * 64


class WorkflowMutationAdapterTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="p15-adapter-")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

        self.rwa = importlib.import_module("custom_backend.review_workflow_api")
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

        self.task_dir = self.root / "task"
        self.task_dir.mkdir()
        self.info = SimpleNamespace(
            task_id=TASK_ID, dir=self.task_dir, degraded=False)
        self.accounts_path = self.root / "users.db"
        self.collab_path = self.collab.shared_store_path(self.task_dir)
        slug = self.tasks.slugify_screener
        self.user_paths = {
            uid: self.task_dir / f"{slug(name)}.db" for uid, name in USER_NAMES.items()}
        self.owner_path = self.user_paths[OWNER]
        self.actor_path = self.user_paths[ALICE]
        self.runtime = self.rwa.WorkflowRuntime(
            self._team_lookup, self._team_screener_db, self.root, self._visible)
        self.window_ctx = None
        self.window_inputs = None
        self.writer_calls: list[int] = []
        self._prepare_databases()

    # ------------------------------------------------------------------
    # real-runtime plumbing (team + solo)
    # ------------------------------------------------------------------
    def _team_lookup(self, task_id):
        return self.info

    def _team_screener_db(self, info, username):
        return self.task_dir / f"{self.tasks.slugify_screener(username)}.db"

    def _visible(self, info, user):
        return True

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
                conn.commit()
        # durable F1/F2/receipts storage for every member target and collab
        for path in self.user_paths.values():
            self.facts.prepare_team_terminal_facts(path)
            self.receipts.prepare_team_flow_receipts(path)
        self.registry.prepare_team_terminal_registry(self.collab_path)
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
            self.window_ctx = ctx
            self.window_inputs = inputs
            yield inputs

    def _mutate(self, *, user=None, name="member_vote", stage="screen",
                keys=ITEM_KEYS[:1], request_id="req-1", request_sha256=_SHA,
                writer=None, expected_version=None, window=None):
        operation = self.flow_types.TeamOperation(
            name=name, stage=stage, expected_version=expected_version)
        if writer is None:
            writer = self._vote_writer(stage)
        active_window = window if window is not None else self._window
        with mock.patch.object(self.commit, "retained_team_write_inputs",
                               active_window):
            return self.runtime.mutate(
                TASK_ID, user or {"user_id": ALICE, "username": "alice"},
                operation=operation, item_keys=keys,
                request_id=request_id, request_sha256=request_sha256,
                writer=writer)

    def _vote_writer(self, stage: str, decision: str = "include",
                     key: str = "K1"):
        table = {"screen": "decisions", "fulltext": "stage2_decisions"}[stage]
        protocol = self

        def writer(ctx):
            protocol.writer_calls.append(1)
            inputs = ctx.write_inputs
            assert inputs is not None, "team writer must see bound inputs"
            inputs.target_conn.execute(
                f"INSERT INTO {table}(zotero_key,decision,exclusion_reason,notes,tags,"
                "screened_at,updated_at) VALUES(?,?,'','', '', ?, ?)",
                (key, decision, _SEED, _SEED))
            return {"saved": [key]}
        return writer

    def _vote_row(self, uid: int, stage: str, key: str):
        table = {"screen": "decisions", "fulltext": "stage2_decisions"}[stage]
        with closing(sqlite3.connect(self.user_paths[uid])) as conn:
            row = conn.execute(
                f"SELECT decision FROM {table} WHERE zotero_key=?", (key,)).fetchone()
        return row[0] if row else None

    def _receipt_count(self):
        with closing(sqlite3.connect(self.actor_path)) as conn:
            return conn.execute("SELECT COUNT(*) FROM team_flow_receipts").fetchone()[0]

    # ------------------------------------------------------------------
    # solo fixture: a legacy task directory (no collaboration store)
    # ------------------------------------------------------------------
    def _solo_setup(self):
        solo_dir = self.root / "solo"
        solo_dir.mkdir()
        self.solo_info = SimpleNamespace(
            task_id=SOLO_ID, dir=solo_dir, degraded=False)
        self.solo_user = {"user_id": 42, "username": "solo-user"}
        slug = self.tasks.slugify_screener
        self.solo_db = solo_dir / f"{slug('solo-user')}.db"
        self.db.init_db(self.solo_db)
        self.workflow.migrate(self.solo_db)
        with closing(sqlite3.connect(self.solo_db)) as conn:
            conn.execute(
                "INSERT INTO articles(zotero_key,title,is_duplicate_of,import_order)"
                " VALUES('K1','Solo K1',NULL,0)")
            conn.commit()
        self.solo_runtime = self.rwa.WorkflowRuntime(
            lambda task_id: self.solo_info,
            lambda info, username: info.dir / f"{slug(username)}.db",
            self.root, lambda info, user: True)

    def _solo_mutate(self, *, writer=None, keys=("K1",), expected_version=None):
        operation = self.flow_types.TeamOperation(
            name="member_vote", stage="screen", expected_version=expected_version)
        captured = {}

        def default_writer(ctx):
            captured["ctx"] = ctx
            captured["write_inputs"] = ctx.write_inputs
            return {"saved": list(keys)}

        return (self.solo_runtime.mutate(
            SOLO_ID, self.solo_user, operation=operation, item_keys=keys,
            request_id="solo-req-1", request_sha256=_SHA,
            writer=writer or default_writer), captured)

    # ------------------------------------------------------------------
    # 1. ctx.write_inputs restored on every exit (success/reject/exception)
    # ------------------------------------------------------------------
    def test_ctx_binding_restored_on_all_exits(self):
        seen = {}

        def writer(ctx):
            self.writer_calls.append(1)
            seen["bound"] = ctx.write_inputs
            return {"saved": ["K1"]}

        # (a) success: bound during the writer, restored after the request
        result = self._mutate(request_id="req-bind-1", writer=writer)
        self.assertEqual(result["outcome"], "committed")
        self.assertIsNotNone(seen["bound"])
        self.assertIs(seen["bound"], self.window_inputs)
        self.assertIsNone(self.window_ctx.write_inputs,
                          "write_inputs must be None after a successful request")

        # (b) rejection (policy version conflict, writer never runs)
        with self.assertRaises(self.rwa.HTTPException) as caught:
            self._mutate(request_id="req-bind-2", expected_version=999)
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(caught.exception.detail["code"],
                         "workflow_version_conflict")
        self.assertEqual(self.writer_calls, [1])
        self.assertIsNone(self.window_ctx.write_inputs,
                          "write_inputs must be None after a rejected request")

        # (c) writer exception: binding restored before the error escapes
        def failing(ctx):
            self.writer_calls.append(1)
            self.assertIsNotNone(ctx.write_inputs)
            raise RuntimeError("writer boom")

        with self.assertRaisesRegex(RuntimeError, "writer boom"):
            self._mutate(request_id="req-bind-3", writer=failing)
        self.assertIsNone(self.window_ctx.write_inputs,
                          "write_inputs must be None after a writer exception")

        # (d) a second request on the same runtime rebinds cleanly (no leak)
        result2 = self._mutate(request_id="req-bind-4", writer=writer)
        self.assertEqual(result2["outcome"], "committed")
        self.assertEqual(len(self.writer_calls), 3)
        self.assertIsNone(self.window_ctx.write_inputs)

    # ------------------------------------------------------------------
    # 2. team writer gets the native WorkflowContext + retained target
    # ------------------------------------------------------------------
    def test_team_writer_gets_native_target(self):
        seen = {}

        def writer(ctx):
            self.writer_calls.append(1)
            inputs = ctx.write_inputs
            seen["ctx_is_window_ctx"] = ctx is self.window_ctx
            seen["inputs"] = inputs
            seen["ctx_type"] = type(ctx).__name__
            seen["target_native"] = isinstance(inputs.target_conn,
                                               sqlite3.Connection)
            seen["target_in_txn"] = inputs.target_conn.in_transaction
            seen["target_row_factory"] = inputs.target_conn.row_factory
            seen["target_is_actor"] = inputs.target_conn is inputs.actor_conn
            # Requirement 2: dependency reads stay on retained connections —
            # poison every path connect and prove settings still resolves,
            # and that store.settings received the retained owner_conn.
            real_settings = self.workflow.settings
            with mock.patch.object(self.workflow, "settings",
                                   wraps=real_settings) as spy, \
                    mock.patch.object(self.workflow, "connect",
                                      side_effect=AssertionError(
                                          "path-only connect inside the "
                                          "team write window")):
                seen["settings"] = self.runtime.settings(ctx)
                seen["settings_conn"] = spy.call_args.kwargs.get("conn")
                seen["can_submit"] = self.runtime.can_submit_coding(ctx)
            # evidence contract keeps working through the retained channel
            seen["evidence_state"] = self.runtime.evidence_state(ctx, "K1")
            inputs.target_conn.execute(
                "INSERT INTO decisions(zotero_key,decision,exclusion_reason,notes,"
                "tags,screened_at,updated_at) VALUES(?,?,'','', '', ?, ?)",
                ("K1", "include", _SEED, _SEED))
            return {"saved": ["K1"]}

        result = self._mutate(request_id="req-native-1", writer=writer)
        self.assertEqual(result["outcome"], "committed")
        self.assertTrue(seen["ctx_is_window_ctx"])
        self.assertEqual(seen["ctx_type"], "WorkflowContext")
        self.assertIs(seen["inputs"], self.window_inputs)
        self.assertTrue(seen["target_native"])
        self.assertTrue(seen["target_in_txn"])
        self.assertIs(seen["target_row_factory"], sqlite3.Row)
        self.assertTrue(seen["target_is_actor"])
        self.assertIsNotNone(seen["settings"])
        self.assertIs(seen["settings_conn"], self.window_inputs.owner_conn,
                      "runtime.settings must read via the retained owner_conn")
        self.assertTrue(seen["can_submit"], "alice is the configured coder")
        self.assertIn("evidence_complete", seen["evidence_state"])
        # durable single-target commit really happened
        self.assertEqual(self._vote_row(ALICE, "screen", "K1"), "include")
        self.assertIsNone(self.window_ctx.write_inputs)

    # ------------------------------------------------------------------
    # 3. solo defaults unchanged (original write_guard, write_inputs None)
    # ------------------------------------------------------------------
    def test_solo_defaults_unchanged(self):
        self._solo_setup()

        # solo must never reach the team commit protocol
        with mock.patch.object(self.commit, "run_team_mutation",
                               side_effect=AssertionError(
                                   "solo path must not run_team_mutation")):
            with mock.patch.object(self.solo_runtime, "write_guard",
                                   wraps=self.solo_runtime.write_guard) as guard:
                result, captured = self._solo_mutate()
        self.assertEqual(guard.call_count, 1)
        self.assertEqual(
            guard.call_args.args,
            (SOLO_ID, self.solo_user, "screen", "K1", None),
            "solo path must keep the original write_guard call shape")
        self.assertEqual(captured["ctx"].task_kind, "legacy")
        self.assertIsNone(captured["write_inputs"],
                          "solo write_guard context never binds write_inputs")
        self.assertIsNone(captured["ctx"].write_inputs)
        # envelope: committed + no team receipt + receipt headers present
        self.assertEqual(result["outcome"], "committed")
        self.assertFalse(result["idempotent"])
        self.assertIsNone(result["receipt"])
        self.assertEqual(result["seals"], ())
        self.assertEqual(result["headers"]["X-Request-Id"], "solo-req-1")
        self.assertEqual(result["headers"]["X-Idempotent-Replay"], "false")

        # solo multi-key stays explicit 400 (no silent semantic change)
        with self.assertRaises(self.rwa.HTTPException) as caught:
            self._solo_mutate(keys=("K1", "K2"))
        self.assertEqual(caught.exception.status_code, 400)

        # solo version conflicts keep the original write_guard 409 payload
        with self.assertRaises(self.rwa.HTTPException) as caught:
            self._solo_mutate(expected_version=999)
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(caught.exception.detail["code"],
                         "workflow_version_conflict")
        self.assertIn("workflow_settings", caught.exception.detail)

    # ------------------------------------------------------------------
    # 4. API error mapping + receipt headers
    # ------------------------------------------------------------------
    def test_api_error_mapping_and_receipt_headers(self):
        # (a) success: receipt headers for the route layer (P24 wiring)
        first = self._mutate(request_id="req-map-1")
        self.assertEqual(first["outcome"], "committed")
        self.assertEqual(first["headers"]["X-Request-Id"], "req-map-1")
        self.assertEqual(first["headers"]["X-Idempotent-Replay"], "false")
        self.assertEqual(first["headers"]["X-Receipt-Id"],
                         first["receipt"]["receipt_id"])

        # (b) idempotent replay: same receipt, replay header flips
        replay = self._mutate(request_id="req-map-1")
        self.assertTrue(replay["idempotent"])
        self.assertEqual(replay["headers"]["X-Idempotent-Replay"], "true")
        self.assertEqual(replay["headers"]["X-Receipt-Id"],
                         first["headers"]["X-Receipt-Id"])
        self.assertEqual(self._receipt_count(), 1)

        # (c) same request id, different content: typed 409 conflict
        with self.assertRaises(self.rwa.HTTPException) as caught:
            self._mutate(request_id="req-map-1", request_sha256="c" * 64)
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(caught.exception.detail["code"],
                         "request_receipt_conflict")

        # (d) request-shape violations map to 400
        with self.assertRaises(self.rwa.HTTPException) as caught:
            self._mutate(request_id="bad\nrequest")
        self.assertEqual(caught.exception.status_code, 400)
        self.assertEqual(caught.exception.detail["code"], "invalid_request_id")

        # (e) a client-forged operation object never routes anywhere
        with self.assertRaises(TypeError):
            self.runtime.mutate(
                TASK_ID, {"user_id": ALICE, "username": "alice"},
                operation="member_vote:screen",  # client-shaped string
                item_keys=("K1",), request_id="req-map-2",
                request_sha256=_SHA, writer=self._vote_writer("screen"))
        self.assertEqual(len(self.writer_calls), 1, "(a) ran the writer once")

        # (f) policy rejection keeps the legacy payload shape
        with self.assertRaises(self.rwa.HTTPException) as caught:
            self._mutate(request_id="req-map-3", expected_version=999)
        detail = caught.exception.detail
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(detail["code"], "workflow_version_conflict")
        self.assertIsInstance(detail["version"], int)
        self.assertIn("workflow_settings", detail)
        self.assertIn("stage", detail)

        # (g) role rejection (reconciler owner voting): structured 403
        with self.assertRaises(self.rwa.HTTPException) as caught:
            self._mutate(request_id="req-map-4",
                         user={"user_id": OWNER, "username": "owner"})
        self.assertEqual(caught.exception.status_code, 403)
        self.assertEqual(caught.exception.detail["code"], "screening_not_enabled")
        self.assertEqual(caught.exception.detail["message_key"],
                         "team.screening_not_enabled")

        # (h) window-level "not a team task after all": 409 retry, no writer
        @contextmanager
        def drifting_window(runtime, info, user, *, target_kind, operation,
                            item_keys):
            raise ValueError(
                "retained_team_write_inputs requires a team task; got 'legacy'")
            yield  # pragma: no cover

        with self.assertRaises(self.rwa.HTTPException) as caught:
            self._mutate(request_id="req-map-5", window=drifting_window)
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(caught.exception.detail["code"], "task_mode_changed")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
