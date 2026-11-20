"""P13 team commit protocol tests (C08 author side; 2.1.6).

Six P13-named tests for ``custom_backend/team_flow_commit.run_team_mutation``.
Product imports and SQLite fixture work are deferred to unittest execution;
this file must stay loadable under ``python -I -B``.

The fixture reuses the proven P12 S2 harness shape (real collaboration roles,
real vote ledger via ``coscreen.db.init_db``, B1-reserved native connections,
TSR borrowed scope) on top of the genuine B1 reservation manager
(``retained_write_connections``).  The P10 window entry
(``retained_team_write_inputs``) is patched at the product module seam with a
window built from the same reservation machinery, because the isolated test
tree has no full ``WorkflowRuntime``/catalog host; production still calls the
real P10 CM (see REPORT left-behind note).
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

TASK_ID = "commit-task-1"
INSTANCE_ID = "commit-instance-1"
OWNER, ALICE, BOB = 1, 2, 3
USER_NAMES = {OWNER: "owner", ALICE: "alice", BOB: "bob"}
ITEM_KEYS = ("K1", "K2")
_SEED = "2026-11-15T00:00:00+00:00"
_LIB = "a" * 32


class TeamCommitProtocolTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="p13-commit-")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

        self.commit = importlib.import_module("custom_backend.team_flow_commit")
        self.checks = importlib.import_module("custom_backend.team_write_checks")
        self.flow_types = importlib.import_module("coscreen.team_flow_types")
        self.inputs_api = importlib.import_module("custom_backend.team_screening_write")
        self.tsr = importlib.import_module("custom_backend.team_screening_runtime")
        self.resource = importlib.import_module("coscreen.team_write_reservation")
        self.collab = importlib.import_module("coscreen.task_collaboration")
        self.db = importlib.import_module("coscreen.db")
        self.workflow = importlib.import_module("coscreen.review_workflow")
        self.ledger = importlib.import_module("coscreen.team_vote_ledger")
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
        self.runtime = SimpleNamespace(host="isolated-test")
        self._prepare_databases()
        self.writer_calls: list[int] = []

    # ------------------------------------------------------------------
    # fixture
    # ------------------------------------------------------------------
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
        # durable F1/F2/receipts storage for the actor target and collab
        self.facts.prepare_team_terminal_facts(self.actor_path)
        self.receipts.prepare_team_flow_receipts(self.actor_path)
        self.registry.prepare_team_terminal_registry(self.collab_path)
        with closing(sqlite3.connect(self.collab_path)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("BEGIN IMMEDIATE")
            self.registry.register_protocol_on(
                conn, task_id=TASK_ID, task_instance_id=INSTANCE_ID,
                owner_user_id=OWNER, owner_library_id=_LIB,
                creation_path="trusted_new_task")
            for uid, path in ((ALICE, self.actor_path), (BOB, self.user_paths[BOB])):
                with closing(sqlite3.connect(path)) as own:
                    library, _ = self.ledger.vote_snapshot(own, "stage1")
                self.registry.register_source_on(
                    conn, task_instance_id=INSTANCE_ID, source_user_id=uid,
                    source_library_id=library, source_path_identity=path.name)
            conn.commit()

    def _seed_vote(self, uid: int, key: str, stage: str, decision: str):
        table = {"screen": "decisions", "fulltext": "stage2_decisions"}[stage]
        with closing(sqlite3.connect(self.user_paths[uid])) as conn:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute(
                f"INSERT INTO {table}(zotero_key,decision,exclusion_reason,notes,tags,"
                "screened_at,updated_at) VALUES(?,?, '', '', '', ?, ?)",
                (key, decision, _SEED, _SEED))
            conn.commit()

    # ------------------------------------------------------------------
    # window seam: the genuine B1 reservation machinery + real TeamWriteInputs
    # ------------------------------------------------------------------
    @contextmanager
    def _window(self, runtime, info, user, *, target_kind, operation, item_keys):
        member_paths = dict(self.user_paths)
        paths = list(dict.fromkeys(
            (self.owner_path, self.accounts_path, *self.user_paths.values())))
        with self.resource.retained_write_connections(self.collab_path) as reserved:
            reserved.reserve(
                self.owner_path, [p for p in paths if p != self.owner_path])
            all_paths = list(dict.fromkeys((self.collab_path, *paths)))
            scope = self.tsr.borrowed_team_read_scope(
                self.info, accounts_path=self.accounts_path,
                collab_path=self.collab_path, owner_path=self.owner_path,
                member_paths=member_paths,
                connections={p: reserved.connection(p) for p in all_paths},
                missing_members=frozenset())
            target_path = (self.actor_path if target_kind == "actor"
                           else self.collab_path)
            reserved.select_target(target_path)
            actor_uid = user["user_id"]
            ctx = SimpleNamespace(
                info=self.info, task_id=TASK_ID, user=dict(user),
                owner={"owner_user_id": OWNER}, owner_db=self.owner_path,
                actor_db=self.actor_path, collaboration_db=self.collab_path,
                task_kind="team", can_manage=actor_uid == OWNER)
            role_state = scope.role_state()
            yield self.inputs_api.TeamWriteInputs(
                ctx=ctx, scope=scope, collab_conn=reserved.collab_conn,
                owner_conn=reserved.connection(self.owner_path),
                actor_conn=reserved.connection(self.actor_path),
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
                    selected_paths=(self.owner_path, self.actor_path)),
                _reserved=reserved)

    def _run(self, *, name="member_vote", stage="screen", keys=ITEM_KEYS[:1],
             request_id="req-stable-1", request_sha256="b" * 64,
             writer=None, expected_version=None, user=None):
        operation = self.flow_types.TeamOperation(
            name=name, stage=stage, expected_version=expected_version)
        if writer is None:
            writer = self._vote_writer(stage)
        with mock.patch.object(self.commit, "retained_team_write_inputs",
                               self._window):
            return self.commit.run_team_mutation(
                self.runtime, self.info,
                user or {"user_id": ALICE},
                operation=operation, item_keys=keys,
                request_id=request_id, request_sha256=request_sha256,
                writer=writer)

    def _vote_writer(self, stage: str, decision: str = "include", body=None,
                     key: str = "K1"):
        table = {"screen": "decisions", "fulltext": "stage2_decisions"}[stage]
        protocol = self

        def writer(inputs):
            protocol.writer_calls.append(1)
            inputs.target_conn.execute(
                f"INSERT INTO {table}(zotero_key,decision,exclusion_reason,notes,tags,"
                "screened_at,updated_at) VALUES(?,?,'','', '', ?, ?)",
                (key, decision, _SEED, _SEED))
            return body if body is not None else {"saved": [key]}
        return writer

    def _seal_rows(self, trigger=None):
        import json
        with closing(sqlite3.connect(self.actor_path)) as conn:
            conn.row_factory = sqlite3.Row
            rows = []
            for r in conn.execute(
                    "SELECT stage, item_key, kind, origin, payload_json "
                    "FROM team_terminal_seals"):
                row = dict(r)
                row["trigger"] = json.loads(row.pop("payload_json"))["trigger"]
                row["schema"] = "v2" if row["trigger"] == "current_observation" else "v1"
                rows.append(row)
        return [r for r in rows if trigger is None or r["trigger"] == trigger]

    def _receipt_rows(self):
        import json
        with closing(sqlite3.connect(self.actor_path)) as conn:
            conn.row_factory = sqlite3.Row
            rows = []
            for r in conn.execute("SELECT * FROM team_flow_receipts"):
                row = dict(r)
                payload = json.loads(row["payload_json"])
                row["recorded_at"] = payload["recorded_at"]
                row["request_sha256"] = payload["request_sha256"]
                rows.append(row)
            return rows

    def _vote_row(self, uid: int, stage: str, key: str):
        table = {"screen": "decisions", "fulltext": "stage2_decisions"}[stage]
        with closing(sqlite3.connect(self.user_paths[uid])) as conn:
            row = conn.execute(
                f"SELECT decision FROM {table} WHERE zotero_key=?", (key,)).fetchone()
        return row[0] if row else None

    # ------------------------------------------------------------------
    # 1. last vote + seal, same single actor commit
    # ------------------------------------------------------------------
    def test_last_vote_and_seal_same_actor_commit(self):
        self._seed_vote(BOB, "K1", "screen", "include")
        result = self._run(request_id="req-anchor-1")

        self.assertEqual(result["outcome"], "committed")
        self.assertFalse(result["idempotent"])
        # anchor: receipt result carries the stable request id
        self.assertEqual(result["receipt"]["request_id"], "req-anchor-1")
        self.assertEqual(result["result"]["request_id"], "req-anchor-1")
        # durable business row (single target commit really happened)
        self.assertEqual(self._vote_row(ALICE, "screen", "K1"), "include")
        # last_vote unanimous seal persisted for the actual deciding vote
        seals = self._seal_rows("last_vote")
        self.assertEqual(len(seals), 1)
        self.assertEqual(seals[0]["stage"], "stage1")
        self.assertEqual(seals[0]["item_key"], "K1")
        self.assertEqual(seals[0]["kind"], "unanimous")
        self.assertIn({"kind": "unanimous", "trigger": "last_vote",
                       "stage": "stage1", "item_key": "K1",
                       "seal_id": result["seals"][0]["seal_id"]},
                      [dict(s) for s in result["seals"]])
        self.assertEqual(len(self._receipt_rows()), 1)
        self.assertEqual(result["cleanup_errors"], ())

    # ------------------------------------------------------------------
    # 2. upstream observation before a stage2 save
    # ------------------------------------------------------------------
    def test_upstream_observation_with_stage2_save(self):
        # stage1 live-unanimous (no persisted seal); stage2 in conflict.
        self._seed_vote(ALICE, "K1", "screen", "include")
        self._seed_vote(BOB, "K1", "screen", "include")
        self._seed_vote(BOB, "K1", "fulltext", "exclude")
        seen = {}

        def writer(inputs):
            self.writer_calls.append(1)
            # the upstream observation must already exist when the borrowed
            # writer runs (C08 order: observation -> writer)
            seen["seal"] = self.facts.read_seal_on(
                inputs.target_conn, task_instance_id=INSTANCE_ID,
                stage="stage1", item_key="K1")
            inputs.target_conn.execute(
                "INSERT INTO stage2_decisions(zotero_key,decision,exclusion_reason,"
                "notes,tags,screened_at,updated_at) VALUES(?,?,'','', '', ?, ?)",
                ("K1", "include", _SEED, _SEED))
            return {"saved": ["K1"]}

        result = self._run(stage="fulltext", request_id="req-obs-1", writer=writer)
        self.assertEqual(result["outcome"], "committed")
        self.assertIsNotNone(seen["seal"], "observation must precede the writer")
        self.assertEqual(seen["seal"]["payload"]["stage"], "stage1")
        self.assertEqual(seen["seal"]["payload"]["trigger"],
                         "current_observation")
        # stage2 itself is still conflicted: no stage2 last_vote seal
        self.assertEqual(self._seal_rows("last_vote"), [])
        obs = self._seal_rows("current_observation")
        self.assertEqual(len(obs), 1)
        self.assertEqual(obs[0]["schema"], "v2")
        self.assertEqual(self._vote_row(ALICE, "fulltext", "K1"), "include")

    # ------------------------------------------------------------------
    # 3. rejected batch and writer failure leave no seals
    # ------------------------------------------------------------------
    def test_rejected_batch_and_writer_failure_no_seals(self):
        self._seed_vote(BOB, "K1", "screen", "include")
        # (a) pure full-batch rejection (policy version conflict): no writer,
        # no seals, no receipt, typed P12 exception passes through.
        with self.assertRaises(self.checks.TeamWriteBlocked) as caught:
            self._run(request_id="req-rej-1", expected_version=999)
        self.assertEqual(caught.exception.code, "workflow_version_conflict")
        self.assertEqual(self.writer_calls, [])
        self.assertEqual(self._seal_rows(), [])
        self.assertEqual(self._receipt_rows(), [])
        self.assertIsNone(self._vote_row(ALICE, "screen", "K1"))

        # (b) writer failure inside the window: same-target rollback.
        def failing(inputs):
            self.writer_calls.append(1)
            inputs.target_conn.execute(
                "INSERT INTO decisions(zotero_key,decision,exclusion_reason,notes,"
                "tags,screened_at,updated_at) VALUES(?,?,'','', '', ?, ?)",
                ("K1", "include", _SEED, _SEED))
            raise RuntimeError("writer boom")

        with self.assertRaisesRegex(RuntimeError, "writer boom"):
            self._run(request_id="req-rej-2", writer=failing)
        self.assertEqual(self.writer_calls, [1])
        self.assertEqual(self._seal_rows(), [], "rollback must remove staged seals")
        self.assertEqual(self._receipt_rows(), [])
        self.assertIsNone(self._vote_row(ALICE, "screen", "K1"),
                          "same-target rollback must remove the business row")

    # ------------------------------------------------------------------
    # 4. successful retry never re-observes
    # ------------------------------------------------------------------
    def test_success_retry_no_reobserve(self):
        self._seed_vote(ALICE, "K1", "screen", "include")
        self._seed_vote(BOB, "K1", "screen", "include")
        self._seed_vote(BOB, "K1", "fulltext", "exclude")

        first = self._run(stage="fulltext", request_id="req-retry-1")
        self.assertEqual(first["outcome"], "committed")
        self.assertFalse(first["idempotent"])
        seals_after_first = self._seal_rows()
        receipt_first = self._receipt_rows()[0]
        writer_after_first = len(self.writer_calls)

        second = self._run(stage="fulltext", request_id="req-retry-1")
        self.assertEqual(second["outcome"], "committed")
        self.assertTrue(second["idempotent"])
        self.assertEqual(second["result"], first["result"])
        self.assertEqual(len(self.writer_calls), writer_after_first,
                         "retry must not replay the writer")
        self.assertEqual(self._seal_rows(), seals_after_first,
                         "retry must not re-observe or re-seal")
        receipt_second = self._receipt_rows()
        self.assertEqual(len(receipt_second), 1)
        self.assertEqual(receipt_second[0]["recorded_at"],
                         receipt_first["recorded_at"],
                         "recorded_at must be the original, never regenerated")

        # different content under the same id is a conflict, not a replay
        with self.assertRaises(self.receipts.TeamFlowReceiptConflict):
            self._run(stage="fulltext", request_id="req-retry-1",
                      request_sha256="c" * 64)

    # ------------------------------------------------------------------
    # 5. post-write verdict is fresh, never cached, never from request body
    # ------------------------------------------------------------------
    def test_postwrite_fresh_not_cached(self):
        self._seed_vote(BOB, "K1", "screen", "include")
        self._seed_vote(BOB, "K2", "screen", "include")

        def writer(inputs):
            self.writer_calls.append(1)
            # poison the borrowed scope's vote cache with PRE-write state
            inputs.scope.member_vote_snapshot(ALICE, "alice", "alice", "stage1")
            inputs.target_conn.execute(
                "INSERT INTO decisions(zotero_key,decision,exclusion_reason,notes,"
                "tags,screened_at,updated_at) VALUES(?,?,'','', '', ?, ?)",
                ("K1", "include", _SEED, _SEED))
            # request body lies in both directions; the protocol must ignore it
            return {"unanimous": False, "keys": ["K1", "K2"]}

        result = self._run(request_id="req-fresh-1", writer=writer,
                           keys=("K1", "K2"))
        self.assertEqual(result["outcome"], "committed")
        seals = {s["item_key"] for s in self._seal_rows("last_vote")}
        self.assertEqual(seals, {"K1"},
                         "K1 truly unanimous via fresh read (cache stale); "
                         "K2 has no actor vote despite the body claim")

    # ------------------------------------------------------------------
    # 6. commit/cleanup truth
    # ------------------------------------------------------------------
    def test_commit_or_cleanup_truth(self):
        self._seed_vote(BOB, "K1", "screen", "include")
        self._seed_vote(BOB, "K2", "screen", "include")

        # (a) cleanup failure after a successful commit stays a success with
        # diagnostics — never reported as rollback/unknown.
        cls = self.resource.ReservedWriteConnections
        original_cleanup = cls._cleanup

        def cleanup_with_failure(self, *, committed_target=False):
            self._cleanup_errors.append(
                "close <synthetic-conn>: simulated cleanup failure")
            return original_cleanup(self, committed_target=committed_target)

        with mock.patch.object(self.commit, "retained_team_write_inputs",
                               self._window), \
                mock.patch.object(cls, "_cleanup", cleanup_with_failure):
            result = self.commit.run_team_mutation(
                self.runtime, self.info, {"user_id": ALICE},
                operation=self.flow_types.TeamOperation(
                    name="member_vote", stage="screen"),
                item_keys=("K1",), request_id="req-clean-1",
                request_sha256="b" * 64, writer=self._vote_writer("screen"))
        self.assertEqual(result["outcome"], "committed")
        self.assertTrue(any("simulated cleanup failure" in e
                            for e in result["cleanup_errors"]),
                        result["cleanup_errors"])
        self.assertEqual(self._vote_row(ALICE, "screen", "K1"), "include",
                         "committed business row stays durable")
        self.assertEqual(len(self._seal_rows("last_vote")), 1)

        # (b) commit outcome undecidable after the body succeeded: report
        # unknown, never replay the writer.
        def failing_commit(self):
            raise sqlite3.OperationalError("disk I/O error")

        with mock.patch.object(self.commit, "retained_team_write_inputs",
                               self._window), \
                mock.patch.object(cls, "_commit_target", failing_commit):
            unknown = self.commit.run_team_mutation(
                self.runtime, self.info, {"user_id": ALICE},
                operation=self.flow_types.TeamOperation(
                    name="member_vote", stage="screen"),
                item_keys=("K2",), request_id="req-unknown-1",
                request_sha256="b" * 64,
                writer=self._vote_writer("screen", key="K2"))
        self.assertEqual(unknown["outcome"], "unknown")
        self.assertIn("disk I/O error", unknown["error"])
        self.assertEqual(len(self.writer_calls), 2,
                         "writer ran once per distinct request, never replayed")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
