"""P12 unified pure write-guard tests (C08 author side; 2.1.6).

Covers the 2026-11-15 ruling productized by P12: completed stages keep the
roster/opinions/conclusions of that time, new members only join unfinished
stages (no first-vote backfill), and owners have no business bypass
(team contract v2 section 9).

Product imports and all SQLite fixture work are deferred to unittest
execution.  The fixture reuses the proven S2 harness shape on top of the
current P10 ``TeamWriteInputs`` (operation/item_keys/plan) contract: real
collaboration roles, real attestation storage, B1-reserved native
connections, and a TSR borrowed scope; only the host context is a
SimpleNamespace.

Execution belongs to the later isolated test-author chain; this file must
stay loadable under ``python -I -B`` (no side-effect imports at module
scope).  Fixture shapes were validated against the live P10/P11 modules
(records/probe3.py, records/probe4.py in the P12 evidence box).
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


TASK_ID = "guards-task-1"
USER_IDS = {"owner": 1, "alice": 2, "bob": 3, "carol": 4}
USER_NAMES = {value: key for key, value in USER_IDS.items()}
DAVE_UID = 5
ITEM_KEYS = ("K1", "K2")
_SEEDED_AT = "2026-11-15T00:00:00+00:00"

#: P12 server-selected profile lock: name -> stage -> (target, policy,
#: history_stage).  ``member_vote``/``member_undo`` cover multiple stages;
#: the coding rows carry history_stage None (screening completion never
#: extends into a coding history lock).
EXPECTED_PROFILE = {
    "member_vote": {"screen": ("actor", True, "screen"),
                    "fulltext": ("actor", True, "fulltext")},
    "member_undo": {"screen": ("actor", True, "screen"),
                    "fulltext": ("actor", True, "fulltext"),
                    "coding": ("actor", True, None)},
    "member_coding": {"coding": ("actor", True, None)},
    "member_coding_note": {"coding": ("actor", False, None)},
    "member_appraisal": {"coding": ("actor", False, None)},
    "member_annotation": {"fulltext": ("actor", False, "fulltext")},
    "member_annotation_note": {"fulltext": ("actor", True, "fulltext")},
    "coding_rater_submit": {"coding": ("collab", True, None)},
    "coding_consensus_append": {"coding": ("collab", False, None)},
    "coding_consensus_freeze": {"coding": ("collab", False, None)},
}


class TeamWriteGuardsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="p216-team-write-guards-")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

        # Product code is deliberately imported only when a test executes.
        self.checks = importlib.import_module("custom_backend.team_write_checks")
        self.flow_types = importlib.import_module("coscreen.team_flow_types")
        self.inputs_api = importlib.import_module("custom_backend.team_screening_write")
        self.tsr = importlib.import_module("custom_backend.team_screening_runtime")
        self.footprints = importlib.import_module("custom_backend.team_write_footprints")
        self.resource = importlib.import_module("coscreen.team_write_reservation")
        self.collab = importlib.import_module("coscreen.task_collaboration")
        self.db = importlib.import_module("coscreen.db")
        self.workflow = importlib.import_module("coscreen.review_workflow")
        self.arbitration = importlib.import_module("coscreen.screening_arbitration")

        self.task_dir = self.root / "task"
        self.task_dir.mkdir()
        self.info = SimpleNamespace(task_id=TASK_ID, dir=self.task_dir, degraded=False)
        self.accounts_path = self.root / "users.db"
        self.collab_path = self.collab.shared_store_path(self.task_dir)
        self.user_paths = {
            uid: self.task_dir / (importlib.import_module("coscreen.tasks").slugify_screener(name) + ".db")
            for name, uid in USER_IDS.items()
        }
        self.owner_path = self.user_paths[USER_IDS["owner"]]
        self._prepare_real_databases()

    # ------------------------------------------------------------------
    # fixture helpers (S2 harness shape on the current P10 inputs contract)
    # ------------------------------------------------------------------
    def _prepare_real_databases(self):
        with closing(sqlite3.connect(self.accounts_path)) as connection:
            connection.execute(
                "CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE)")
            connection.executemany(
                "INSERT INTO users(id,username) VALUES(?,?)",
                [(uid, USER_NAMES[uid]) for uid in sorted(USER_NAMES)],
            )
            connection.commit()

        self.collab.initialize_task_store(
            self.task_dir, task_id=TASK_ID, owner_user_id=USER_IDS["owner"],
            owner_username="owner")
        for name, role in (("alice", "coder"), ("bob", "coder"), ("carol", "reconciler")):
            self.collab.grant_role(
                self.collab_path, actor_user_id=USER_IDS["owner"],
                user_id=USER_IDS[name], role=role, task_id=TASK_ID)

        for uid, path in self.user_paths.items():
            self.db.init_db(path)  # installs the real vote ledger and its triggers
            if uid == USER_IDS["owner"]:
                self.workflow.migrate(path)
            with closing(sqlite3.connect(path)) as connection:
                connection.execute("PRAGMA foreign_keys=ON")
                connection.executemany(
                    "INSERT INTO articles(zotero_key,title,is_duplicate_of,import_order) "
                    "VALUES(?,?,NULL,?)",
                    [(key, f"Title {key}", index) for index, key in enumerate(ITEM_KEYS)],
                )
                if uid in (USER_IDS["alice"], USER_IDS["bob"]):
                    # alice/bob hold committed screen+fulltext votes on K1;
                    # stage completion facts come only from unanimity or
                    # attestations, never from bare rows.
                    for table in ("decisions", "stage2_decisions"):
                        connection.execute(
                            f"INSERT INTO {table}(zotero_key,decision,exclusion_reason,notes,tags,"
                            "screened_at,updated_at) VALUES(?,?, '', '', '', ?, ?)",
                            ("K1", "include", _SEEDED_AT, _SEEDED_AT),
                        )
                connection.commit()

        self.arbitration.prepare_arbitration_attestation_storage(self.collab_path)

    def _set_vote(self, path: Path, key: str, stage: str, decision: str):
        table = {"screen": "decisions", "fulltext": "stage2_decisions"}[stage]
        with closing(sqlite3.connect(path)) as connection:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute(
                f"UPDATE {table} SET decision=? WHERE zotero_key=?", (decision, key))
            connection.commit()

    def _add_member(self, *, uid: int, name: str, role: str,
                    stage1_vote: str | None = None,
                    stage2_vote: str | None = None):
        """Create a real member DB (optionally seeding votes) and grant role."""
        path = self.task_dir / (importlib.import_module("coscreen.tasks").slugify_screener(name) + ".db")
        with closing(sqlite3.connect(self.accounts_path)) as connection:
            connection.execute(
                "INSERT INTO users(id,username) VALUES(?,?)", (uid, name))
            connection.commit()
        self.db.init_db(path)
        with closing(sqlite3.connect(path)) as connection:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.executemany(
                "INSERT INTO articles(zotero_key,title,is_duplicate_of,import_order) "
                "VALUES(?,?,NULL,?)",
                [(key, f"Title {key}", index) for index, key in enumerate(ITEM_KEYS)],
            )
            for table, vote in (("decisions", stage1_vote),
                                ("stage2_decisions", stage2_vote)):
                if vote is not None:
                    connection.execute(
                        f"INSERT INTO {table}(zotero_key,decision,exclusion_reason,notes,tags,"
                        "screened_at,updated_at) VALUES(?,?, '', '', '', ?, ?)",
                        ("K1", vote, _SEEDED_AT, _SEEDED_AT),
                    )
            connection.commit()
        self.collab.grant_role(
            self.collab_path, actor_user_id=USER_IDS["owner"],
            user_id=uid, role=role, task_id=TASK_ID)
        return path

    def _append_committed_attestation(self, *, stage: str, keys):
        """Persist an applied arbitration on the collab store (committed).

        After this the stage holds a durable completion fact for ``keys``:
        P10 projects it as ``completion`` while the roster matches, and the
        raw adjudication rows stay protected regardless of later roster
        growth (the durable arm of the P12 lock).
        """
        table, ledger_stage = {
            "screen": ("decisions", "stage1"),
            "fulltext": ("stage2_decisions", "stage2"),
        }[stage]
        ledger = importlib.import_module("coscreen.team_vote_ledger")
        proof, merged_rows = {}, []
        for key in keys:
            votes = {}
            proof[key] = {}
            for name in ("alice", "bob"):
                uid, path = USER_IDS[name], self.user_paths[USER_IDS[name]]
                with closing(sqlite3.connect(path)) as connection:
                    row = connection.execute(
                        f"SELECT decision FROM {table} WHERE zotero_key=?", (key,)).fetchone()
                    self.assertIsNotNone(row, f"missing seeded {stage} vote {name}/{key}")
                    library_id, snapshot = ledger.vote_snapshot(connection, ledger_stage)
                votes[name] = row[0]
                proof[key][str(uid)] = {
                    "library_id": library_id,
                    "revision": int(snapshot[key]["revision"]),
                }
            merged_rows.append({
                "zotero_key": key,
                "status": "agree" if len(set(votes.values())) == 1 else "conflict",
                "n_screened": len(votes),
                **{f"decision_{name}": vote for name, vote in votes.items()},
            })

        import pandas as pd

        connection = sqlite3.connect(self.collab_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            self.arbitration.append_decision_arbitration_attested(
                connection, stage=ledger_stage,
                merged=pd.DataFrame(merged_rows),
                resolutions=[{"zotero_key": key, "decision": "include", "rationale": "eligible"}
                             for key in keys],
                arbitrator_id=USER_IDS["carol"],
                attestation_meta={
                    "task_id": TASK_ID, "round": None,
                    "required_names": ["alice", "bob"],
                    "arbitrator_user_id": USER_IDS["carol"],
                    "arbitrator_role": "reconciler", "input_proof": proof,
                },
            )
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    @contextmanager
    def _inputs(self, *, actor_uid=USER_IDS["alice"], target_kind="actor",
                actor_path_override=None, additional_member_paths=None):
        # Every participant DB must be mapped: the borrowed scope reads any
        # required screener's ledger through this map (probe3 lesson).
        member_paths = dict(self.user_paths)
        member_paths.update(additional_member_paths or {})
        actor_path = actor_path_override or self.user_paths[actor_uid]
        paths = list(dict.fromkeys((self.owner_path, self.accounts_path,
                                    *self.user_paths.values(), actor_path,
                                    *(additional_member_paths or {}).values())))
        with self.resource.retained_write_connections(self.collab_path) as reserved:
            reserved.reserve(self.owner_path, [path for path in paths if path != self.owner_path])
            all_paths = list(dict.fromkeys((self.collab_path, *paths)))
            connections_by_path = {
                path.resolve(): reserved.connection(path) for path in all_paths
            }
            scope = self.tsr.borrowed_team_read_scope(
                self.info,
                accounts_path=self.accounts_path,
                collab_path=self.collab_path,
                owner_path=self.owner_path,
                member_paths=member_paths,
                connections=connections_by_path,
                missing_members=frozenset(),
            )
            target_path = actor_path if target_kind == "actor" else self.collab_path
            reserved.select_target(target_path)
            ctx = SimpleNamespace(
                info=self.info, task_id=TASK_ID,
                user={"user_id": actor_uid}, owner={"owner_user_id": USER_IDS["owner"]},
                owner_db=self.owner_path, actor_db=actor_path,
                collaboration_db=self.collab_path, task_kind="team",
                can_manage=actor_uid == USER_IDS["owner"],
            )
            role_state = scope.role_state()
            yield self.inputs_api.TeamWriteInputs(
                ctx=ctx, scope=scope, collab_conn=reserved.collab_conn,
                owner_conn=reserved.connection(self.owner_path),
                actor_conn=reserved.connection(actor_path),
                target_conn=reserved.target_conn, connections=reserved.connections,
                roster=tuple(dict(entry) for entry in role_state["participants"]),
                operation=self.flow_types.TeamOperation(name="member_vote", stage="screen"),
                item_keys=tuple(ITEM_KEYS[:1]),
                plan=self.flow_types.KeyedReadPlan(
                    binding=self.flow_types.TaskBinding(
                        task_id=TASK_ID, task_instance_id=TASK_ID,
                        owner_user_id=USER_IDS["owner"], owner_library_id="guards-lib"),
                    operation=self.flow_types.TeamOperation(name="member_vote", stage="screen"),
                    item_keys=tuple(ITEM_KEYS[:1]), witnesses={}, live_dependencies={},
                    issues={}, selected_paths=(self.owner_path, actor_path)),
                _reserved=reserved,
            )

    def _require(self, inputs, name: str, stage: str, keys=ITEM_KEYS[:1], version=None):
        operation = self.checks.TeamWriteOp(name=name, stage=stage, expected_version=version)
        return self.checks.require_team_write(
            inputs, operation=operation, item_keys=keys)

    # ------------------------------------------------------------------
    # zero-business-DML authorizer proof (stronger than observer checks)
    # ------------------------------------------------------------------
    @staticmethod
    def _connection_state(inputs):
        return tuple((connection, connection.total_changes) for connection in inputs.connections)

    @staticmethod
    def _business_rows_snapshot(inputs):
        def rows(conn, sql):
            return tuple(tuple(row) for row in conn.execute(sql).fetchall())

        return (
            {"decisions": rows(inputs.actor_conn,
                               "SELECT zotero_key,decision FROM decisions ORDER BY zotero_key"),
             "stage2_decisions": rows(inputs.actor_conn,
                                      "SELECT zotero_key,decision FROM stage2_decisions ORDER BY zotero_key")},
            {"arbitrations": rows(inputs.collab_conn,
                                  "SELECT * FROM screening_arbitrations ORDER BY id"),
             "attestations": rows(inputs.collab_conn,
                                  "SELECT * FROM screening_arbitration_attestations ORDER BY id")},
        )

    def _assert_zero_business_dml(self, inputs, before, snapshot_before):
        """Rejection must produce no business DML anywhere in the window.

        Three independent proofs: every reserved connection's total_changes
        is unchanged (no INSERT/UPDATE/DELETE on any table of any window
        database), the actor/collab business rows are byte-identical, and
        every borrowed connection is still open inside its caller
        transaction.  Combined with the patched sqlite3.connect guard this
        shows the *authorizer itself* performs no business DML, not merely
        that an observer sees no persistence.
        """
        self.assertEqual(len(inputs.connections), len(before))
        for (connection, changes_before) in before:
            self.assertTrue(connection.in_transaction,
                            "guards must leave every caller transaction active")
            self.assertIs(connection.row_factory, sqlite3.Row)
            self.assertEqual(connection.total_changes, changes_before,
                             "guards must not execute any business DML")
            self.assertEqual(connection.execute("SELECT 1").fetchone()[0], 1,
                             "guards must leave every borrowed connection open")
        self.assertEqual(self._business_rows_snapshot(inputs), snapshot_before)

    def _reject_with_zero_dml(self, inputs, name, stage, keys, *,
                              expected_code, expected_item_keys=None):
        before = self._connection_state(inputs)
        snapshot_before = self._business_rows_snapshot(inputs)
        with mock.patch.object(sqlite3, "connect", side_effect=AssertionError("hidden connection open")):
            with self.assertRaises(self.checks.TeamWriteBlocked) as caught:
                self._require(inputs, name, stage, keys=keys)
        blocked = caught.exception
        self.assertEqual(blocked.code, expected_code)
        self.assertEqual(blocked.status, 409)
        self.assertEqual(blocked.stage, stage)
        if expected_item_keys is not None:
            self.assertEqual(blocked.item_keys, expected_item_keys)
        self._assert_zero_business_dml(inputs, before, snapshot_before)
        return blocked

    def _allow_with_zero_dml(self, inputs, name, stage, keys):
        before = self._connection_state(inputs)
        snapshot_before = self._business_rows_snapshot(inputs)
        with mock.patch.object(sqlite3, "connect", side_effect=AssertionError("hidden connection open")):
            result = self._require(inputs, name, stage, keys=keys)
        self.assertIsNone(result)
        self._assert_zero_business_dml(inputs, before, snapshot_before)

    # ------------------------------------------------------------------
    # the five P12-named guards
    # ------------------------------------------------------------------
    def test_new_member_cannot_backfill_completed_stage(self):
        # Existing members conflicted on stage1 K1; the applied arbitration
        # is a durable completion fact of that stage (raw adjudication).
        self._set_vote(self.user_paths[USER_IDS["alice"]], "K1", "screen", "exclude")
        self._append_committed_attestation(stage="screen", keys=("K1",))

        dave_path = self._add_member(uid=DAVE_UID, name="dave", role="coder")
        with self._inputs(actor_uid=DAVE_UID, target_kind="actor",
                          actor_path_override=dave_path,
                          additional_member_paths={DAVE_UID: dave_path}) as inputs:
            # Empty actor row: no old vote, no ledger revision, no undo entry.
            self.assertIsNone(inputs.actor_conn.execute(
                "SELECT 1 FROM decisions WHERE zotero_key='K1'").fetchone())
            self.assertIsNone(inputs.actor_conn.execute(
                "SELECT 1 FROM stage2_decisions WHERE zotero_key='K1'").fetchone())

            blocked = self._reject_with_zero_dml(
                inputs, "member_vote", "screen", ("K1",),
                expected_code="stage_completed_read_only",
                expected_item_keys=("K1",))
            self.assertEqual(blocked.message_key, "team.stage_completed_read_only")

            # The empty actor row gained nothing: still no vote anywhere.
            self.assertIsNone(inputs.actor_conn.execute(
                "SELECT 1 FROM decisions WHERE zotero_key='K1'").fetchone())

    def test_new_member_can_enter_unfinished_stage2(self):
        # Fully configured roster (owner holds the reconciler role so the
        # stage configuration is complete without adding a required
        # screener); stage1 K1 is unanimous across every current required
        # member including dave, stage2 is in conflict and unresolved.
        self.collab.grant_role(
            self.collab_path, actor_user_id=USER_IDS["owner"],
            user_id=USER_IDS["owner"], role="reconciler", task_id=TASK_ID)
        self._set_vote(self.user_paths[USER_IDS["bob"]], "K1", "fulltext", "exclude")
        dave_path = self._add_member(uid=DAVE_UID, name="dave", role="coder",
                                     stage1_vote="include")
        with self._inputs(actor_uid=DAVE_UID, target_kind="actor",
                          actor_path_override=dave_path,
                          additional_member_paths={DAVE_UID: dave_path}) as inputs:
            self.assertIsNone(inputs.actor_conn.execute(
                "SELECT 1 FROM stage2_decisions WHERE zotero_key='K1'").fetchone(),
                "dave must enter stage2 without any stage2 trace")

            self._allow_with_zero_dml(inputs, "member_vote", "fulltext", ("K1",))

            # The finished stage stays read-only for the very same member in
            # the very same window: new members only join unfinished stages.
            self._reject_with_zero_dml(
                inputs, "member_vote", "screen", ("K1",),
                expected_code="stage_completed_read_only",
                expected_item_keys=("K1",))

    def test_mixed_batch_zero_dml(self):
        # K1 completed on stage1 (applied arbitration); K2 has no completion
        # fact. One completed key rejects the whole mixed batch with
        # key-level error and zero business DML — no partial write for K2.
        self._set_vote(self.user_paths[USER_IDS["alice"]], "K1", "screen", "exclude")
        self._append_committed_attestation(stage="screen", keys=("K1",))

        dave_path = self._add_member(uid=DAVE_UID, name="dave", role="coder")
        with self._inputs(actor_uid=DAVE_UID, target_kind="actor",
                          actor_path_override=dave_path,
                          additional_member_paths={DAVE_UID: dave_path}) as inputs:
            blocked = self._reject_with_zero_dml(
                inputs, "member_vote", "screen", ("K1", "K2"),
                expected_code="stage_completed_read_only",
                expected_item_keys=("K1",))
            # Only the completed key is reported; the whole batch failed.
            self.assertEqual(blocked.item_keys, ("K1",))
            for key in ("K1", "K2"):
                self.assertIsNone(inputs.actor_conn.execute(
                    "SELECT 1 FROM decisions WHERE zotero_key=?", (key,)).fetchone(),
                    f"rejected batch must not create a row for {key}")

            # The uncompleted key alone stays writable for the same member:
            # the rejection is scoped to completed facts, never a blanket.
            self._allow_with_zero_dml(inputs, "member_vote", "screen", ("K2",))

    def test_owner_no_business_bypass(self):
        # Team contract v2 section 9: the owner follows the same rules; the
        # management flag and ownership never bypass the completion lock.
        self._set_vote(self.user_paths[USER_IDS["alice"]], "K1", "screen", "exclude")
        self._append_committed_attestation(stage="screen", keys=("K1",))
        self.collab.grant_role(
            self.collab_path, actor_user_id=USER_IDS["owner"],
            user_id=USER_IDS["owner"], role="coder", task_id=TASK_ID)

        with self._inputs(actor_uid=USER_IDS["owner"], target_kind="actor") as inputs:
            self.assertTrue(inputs.ctx.can_manage, "fixture must model a manager flag")
            self._reject_with_zero_dml(
                inputs, "member_vote", "screen", ("K1",),
                expected_code="stage_completed_read_only",
                expected_item_keys=("K1",))

    def test_each_operation_profile(self):
        # The re-export contract: exactly one operation DTO, no second class.
        self.assertIs(self.checks.TeamWriteOp, self.flow_types.TeamOperation)

        # Server-selected profile lock: table rows match EXPECTED_PROFILE
        # exactly (no extra name/stage pairs silently added or removed).
        table = self.checks._OPERATIONS
        self.assertEqual(set(table), set(EXPECTED_PROFILE))
        for name, spec in table.items():
            self.assertEqual(set(spec.stages), set(EXPECTED_PROFILE[name]))
            for stage, rule in spec.stages.items():
                with self.subTest(name=name, stage=stage):
                    target, policy, history_stage = EXPECTED_PROFILE[name][stage]
                    self.assertEqual(spec.target, target)
                    self.assertEqual(spec.policy, policy)
                    self.assertEqual(rule.history_stage, history_stage)
                    if history_stage is not None:
                        self.assertIn(history_stage, self.checks._STAGE_VIEW_STAGES)
                    operation = self.checks.TeamWriteOp(name=name, stage=stage)
                    self.assertEqual(self.checks.team_operation_target(operation), target)

        # Unknown names/stages and non-policy expected_version stay
        # programming errors (ValueError), never silent acceptance.
        for name, stage in (("member_vote", "coding"), ("member_coding", "screen"),
                            ("team_trace_edit", "screen"), ("unknown", "screen")):
            with self.subTest(invalid=(name, stage)):
                with self.assertRaises(ValueError):
                    self.checks.team_operation_target(
                        self.checks.TeamWriteOp(name=name, stage=stage))
        with self.assertRaises(ValueError):
            self.checks.team_operation_target(
                self.checks.TeamWriteOp(name="member_appraisal", stage="coding",
                                        expected_version=1))

        # P11 alignment: every request-key-subject operation resolves a
        # complete footprint on a borrowed in-memory native connection.
        with closing(sqlite3.connect(":memory:")) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("BEGIN")
            for name in sorted(self.checks._REQUEST_KEY_FOOTPRINT_OPS):
                spec = table[name]
                for stage in spec.stages:
                    with self.subTest(footprint=(name, stage)):
                        operation = self.checks.TeamWriteOp(name=name, stage=stage)
                        footprint = self.footprints.mutation_footprint_on(
                            conn, operation=operation, artifact_id=None,
                            request_keys=("K1",))
                        self.assertTrue(footprint.complete)
                        self.assertEqual(
                            tuple((cell.stage, cell.item_key) for cell in footprint.cells),
                            ((stage, "K1"),))
            conn.rollback()


if __name__ == "__main__":  # pragma: no cover - unittest entry point
    unittest.main()
