"""S2-B2b pure team write-check contract tests (P12 revision).

P12 (2026-11-15 ruling; C08) revised this suite in place.  Still-valid
assertions are kept; the NOT_ADOPTED first-vote expectations are reversed
into rejections, each carrying a ``P12-MAP:`` comment describing the old
expectation.  The harness now follows the current P10 ``TeamWriteInputs``
contract (operation/item_keys/plan) and maps every participant DB into the
borrowed scope.  Reversal summary:

- first-vote/new-slot append on an adjudicated key: ALLOWED -> blocked
  ``stage_completed_read_only`` (empty actor row is no exception);
- same-stage raw history lock: ``TeamHistoryReadOnlyError`` -> blocked
  ``stage_completed_read_only`` (409, key-level item_keys);
- re-vote/undo/annotation on a currently-consistent (unanimous) key:
  ALLOWED -> blocked ``stage_completed_read_only``;
- tombstone/undo-journal/old-row guards existed only to fence the removed
  first-vote exception: those keys without completion facts are now plain
  writable current state (writer-side ledger rules still apply there).

Product imports and all SQLite fixture work are deferred to unittest execution.
The fixture uses real collaboration roles, vote ledgers, B1-reserved native
connections, and a TSR borrowed scope; only the host context is a SimpleNamespace.
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


TASK_ID = "checks-task-1"
USER_IDS = {"owner": 1, "alice": 2, "bob": 3, "carol": 4}
USER_NAMES = {value: key for key, value in USER_IDS.items()}
ITEM_KEYS = ("K1", "K2")
_SEEDED_AT = "2026-11-15T00:00:00+00:00"


class TeamWriteChecksTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="s2-team-write-checks-")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

        # Product code is deliberately imported only when a test executes.
        self.checks = importlib.import_module("custom_backend.team_write_checks")
        self.inputs_api = importlib.import_module("custom_backend.team_screening_write")
        self.tsr = importlib.import_module("custom_backend.team_screening_runtime")
        self.resource = importlib.import_module("coscreen.team_write_reservation")
        self.collab = importlib.import_module("coscreen.task_collaboration")
        self.history = importlib.import_module("coscreen.team_history_guard")
        self.db = importlib.import_module("coscreen.db")
        self.ledger = importlib.import_module("coscreen.team_vote_ledger")
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
        self.member_paths = {
            uid: self.user_paths[uid] for uid in (USER_IDS["alice"], USER_IDS["bob"])
        }
        self._prepare_real_databases()

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
                # Seed parent articles before FK vote rows; do not depend on
                # SQLite's default foreign_keys=OFF connection setting.
                connection.execute("PRAGMA foreign_keys=ON")
                connection.executemany(
                    "INSERT INTO articles(zotero_key,title,is_duplicate_of,import_order) "
                    "VALUES(?,?,NULL,?)",
                    [(key, f"Title {key}", index) for index, key in enumerate(ITEM_KEYS)],
                )
                if uid in self.member_paths:
                    for key in ("K1",):
                        connection.execute(
                            "INSERT INTO decisions(zotero_key,decision,exclusion_reason,notes,tags,"
                            "screened_at,updated_at) VALUES(?,?, '', '', '', ?, ?)",
                            (key, "include", _SEEDED_AT, _SEEDED_AT),
                        )
                        connection.execute(
                            "INSERT INTO stage2_decisions(zotero_key,decision,exclusion_reason,notes,tags,"
                            "screened_at,updated_at) VALUES(?,?, '', '', '', ?, ?)",
                            (key, "include", _SEEDED_AT, _SEEDED_AT),
                        )
                connection.commit()

        self.arbitration.prepare_arbitration_attestation_storage(self.collab_path)

    def _add_extra_articles(self, keys):
        for path in (self.owner_path, *self.user_paths.values()):
            with closing(sqlite3.connect(path)) as connection:
                connection.execute("PRAGMA foreign_keys=ON")
                connection.executemany(
                    "INSERT OR IGNORE INTO articles(zotero_key,title,is_duplicate_of,import_order) "
                    "VALUES(?,?,NULL,?)",
                    [(key, f"Title {key}", 100 + index) for index, key in enumerate(keys)],
                )
                connection.commit()

    def _add_new_required_coder(self, *, keys=ITEM_KEYS):
        uid, name = max(USER_IDS.values()) + 1, "dave"
        path = self.task_dir / (importlib.import_module("coscreen.tasks").slugify_screener(name) + ".db")
        with closing(sqlite3.connect(self.accounts_path)) as connection:
            connection.execute("INSERT INTO users(id,username) VALUES(?,?)", (uid, name))
            connection.commit()
        self.db.init_db(path)
        with closing(sqlite3.connect(path)) as connection:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.executemany(
                "INSERT INTO articles(zotero_key,title,is_duplicate_of,import_order) "
                "VALUES(?,?,NULL,?)",
                [(key, f"Title {key}", index) for index, key in enumerate(keys)],
            )
            connection.commit()
        self.collab.grant_role(
            self.collab_path, actor_user_id=USER_IDS["owner"],
            user_id=uid, role="coder", task_id=TASK_ID)
        return uid, path

    def _write_vote(self, path: Path, key: str, stage: str, decision: str):
        table = {"screen": "decisions", "fulltext": "stage2_decisions"}[stage]
        with closing(sqlite3.connect(path)) as connection:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute(
                f"INSERT INTO {table}(zotero_key,decision,exclusion_reason,notes,tags,screened_at,updated_at) "
                "VALUES(?,?, '', '', '', ?, ?)",
                (key, decision, _SEEDED_AT, _SEEDED_AT),
            )
            connection.commit()

    def _append_committed_attestation(
        self, *, stage: str, keys, required_names=("alice", "bob"),
        extra_user_paths=None, extra_proof=None,
    ):
        table, ledger_stage = {
            "screen": ("decisions", "stage1"),
            "fulltext": ("stage2_decisions", "stage2"),
        }[stage]
        paths_by_name = {USER_NAMES[uid]: path for uid, path in self.user_paths.items()}
        paths_by_name.update(extra_user_paths or {})
        uids_by_name = {name: uid for uid, name in USER_NAMES.items()}
        # RUN1-FIX(#2): the original dict-comprehension referenced `name`
        # without binding it inside the comprehension (UnboundLocalError
        # whenever extra_user_paths was non-empty).  Extra members carry the
        # sequential uids _add_new_required_coder assigns above max(USER_IDS).
        next_uid = max(USER_IDS.values()) + 1
        for extra_name in (extra_user_paths or {}):
            uids_by_name[extra_name] = next_uid
            next_uid += 1
        proof, merged_rows = {}, []
        for key in keys:
            votes = {}
            proof[key] = {}
            for name in required_names:
                uid = uids_by_name[name]
                path = paths_by_name[name]
                with closing(sqlite3.connect(path)) as connection:
                    row = connection.execute(
                        f"SELECT decision FROM {table} WHERE zotero_key=?", (key,)).fetchone()
                    self.assertIsNotNone(row, f"missing seeded {stage} vote {name}/{key}")
                    library_id, snapshot = self.ledger.vote_snapshot(connection, ledger_stage)
                votes[name] = row[0]
                proof[key][str(uid)] = {
                    "library_id": library_id,
                    "revision": int(snapshot[key]["revision"]),
                }
            if extra_proof and key in extra_proof:
                proof[key].update(extra_proof[key])
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
                connection, stage="stage1" if stage == "screen" else "stage2",
                merged=pd.DataFrame(merged_rows),
                resolutions=[{"zotero_key": key, "decision": "include", "rationale": "eligible"}
                             for key in keys],
                arbitrator_id=USER_IDS["carol"],
                attestation_meta={
                    "task_id": TASK_ID, "round": None,
                    "required_names": list(required_names),
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

    @staticmethod
    def _attestation_snapshot(connection):
        generic = tuple(tuple(row) for row in connection.execute(
            "SELECT * FROM screening_arbitrations ORDER BY id"))
        attested = tuple(tuple(row) for row in connection.execute(
            "SELECT * FROM screening_arbitration_attestations ORDER BY id"))
        return generic, attested

    def _set_owner_policy(self, stage: str, *, mode: str, source_ids=()):
        with closing(sqlite3.connect(self.owner_path)) as connection:
            row = connection.execute(
                "SELECT stages_json FROM workflow_state WHERE singleton=1").fetchone()
            stages = json.loads(row[0])
            ids = list(source_ids)
            if mode == "selected" and not ids:
                # Selected mode needs a real active source; K1 remains linked
                # only to Unassigned, so this source creates a concrete mismatch.
                ids = ["checks-selected-source"]
                connection.execute(
                    "INSERT OR IGNORE INTO workflow_sources"
                    "(id,name,normalized_name,active,created_at) VALUES(?,?,?,?,?)",
                    (ids[0], "Selected source", "selected source", 1, _SEEDED_AT),
                )
            stages[stage] = {"mode": mode, "source_ids": ids}
            connection.execute(
                "UPDATE workflow_state SET stages_json=?, version=version+1 WHERE singleton=1",
                (json.dumps(stages, sort_keys=True),),
            )
            connection.commit()

    def _append_stage1_include_attestation_after_alice_excludes(self, inputs):
        # Stage the vote and attestation on the exact already-reserved native
        # connections; TSR must consume both facts from this uncommitted window.
        inputs.actor_conn.execute(
            "UPDATE decisions SET decision='exclude', updated_at=?, screened_at=? "
            "WHERE zotero_key='K1'", (_SEEDED_AT, _SEEDED_AT))

        proof = {}
        for name in ("alice", "bob"):
            path = self.user_paths[USER_IDS[name]]
            connection = inputs._reserved.connection(path)
            library_id, rows = self.ledger.vote_snapshot(connection, "stage1")
            proof.setdefault("K1", {})[str(USER_IDS[name])] = {
                "library_id": library_id,
                "revision": int(rows["K1"]["revision"]),
            }

        import pandas as pd

        merged = pd.DataFrame([{
            "zotero_key": "K1", "status": "conflict", "n_screened": 2,
            "decision_alice": "exclude", "decision_bob": "include",
        }])
        self.arbitration.append_decision_arbitration_attested(
            inputs.collab_conn, stage="stage1", merged=merged,
            resolutions=[{"zotero_key": "K1", "decision": "include", "rationale": "eligible"}],
            arbitrator_id=USER_IDS["carol"],
            attestation_meta={
                "task_id": TASK_ID, "round": None, "required_names": ["alice", "bob"],
                "arbitrator_user_id": USER_IDS["carol"],
                "arbitrator_role": "reconciler", "input_proof": proof,
            },
        )

    @contextmanager
    def _inputs(self, *, actor_uid=USER_IDS["alice"], target_kind="actor",
                actor_path_override=None, additional_member_paths=None):
        # P12: every participant DB must be mapped into the borrowed scope
        # (the current P10 keyed stage reads any required screener's ledger
        # through this map) and TeamWriteInputs now carries the P10
        # operation/item_keys/plan contract.
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
            flow_types = importlib.import_module("coscreen.team_flow_types")
            probe_operation = flow_types.TeamOperation(name="member_vote", stage="screen")
            yield self.inputs_api.TeamWriteInputs(
                ctx=ctx, scope=scope, collab_conn=reserved.collab_conn,
                owner_conn=reserved.connection(self.owner_path),
                actor_conn=reserved.connection(actor_path),
                target_conn=reserved.target_conn, connections=reserved.connections,
                roster=tuple(dict(entry) for entry in role_state["participants"]),
                operation=probe_operation,
                item_keys=tuple(ITEM_KEYS[:1]),
                plan=flow_types.KeyedReadPlan(
                    binding=flow_types.TaskBinding(
                        task_id=TASK_ID, task_instance_id=TASK_ID,
                        owner_user_id=USER_IDS["owner"], owner_library_id="s2-lib"),
                    operation=probe_operation,
                    item_keys=tuple(ITEM_KEYS[:1]), witnesses={}, live_dependencies={},
                    issues={}, selected_paths=(self.owner_path, actor_path)),
                _reserved=reserved,
            )

    def _require(self, inputs, name: str, stage: str, keys=ITEM_KEYS[:1], version=None):
        operation = self.checks.TeamWriteOp(name=name, stage=stage, expected_version=version)
        return self.checks.require_team_write(
            inputs, operation=operation, item_keys=keys)

    @staticmethod
    def _connection_state(inputs):
        return tuple((connection, connection.total_changes) for connection in inputs.connections)

    def _assert_borrowed_and_read_only(self, inputs, before):
        self.assertEqual(len(inputs.connections), len(before))
        for (connection, changes_before) in before:
            self.assertTrue(connection.in_transaction, "checks must leave every caller transaction active")
            self.assertIs(connection.row_factory, sqlite3.Row)
            self.assertEqual(connection.total_changes, changes_before, "checks must not write rows")
            self.assertEqual(connection.execute("SELECT 1").fetchone()[0], 1,
                             "checks must leave every borrowed connection open")

    def _call_without_hidden_connect(self, inputs, name, stage, keys=ITEM_KEYS[:1], version=None):
        before = self._connection_state(inputs)
        with mock.patch.object(sqlite3, "connect", side_effect=AssertionError("hidden connection open")):
            result = self._require(inputs, name, stage, keys=keys, version=version)
        self.assertIsNone(result)
        self._assert_borrowed_and_read_only(inputs, before)

    def test_fixed_operation_table_rejects_unknown_name_stage_pairs(self):
        # P12-MAP: was test_fixed_nine_operation_table_rejects_unknown_name_
        # stage_pairs — the frozen table now carries ten names, so the
        # "nine" in the legacy name no longer holds.
        table = (
            ("member_vote", "screen", "actor"),
            ("member_vote", "fulltext", "actor"),
            ("member_undo", "screen", "actor"),
            ("member_undo", "fulltext", "actor"),
            ("member_undo", "coding", "actor"),
            ("member_coding", "coding", "actor"),
            ("member_coding_note", "coding", "actor"),
            ("member_appraisal", "coding", "actor"),
            ("member_annotation", "fulltext", "actor"),
            ("member_annotation_note", "fulltext", "actor"),
            ("coding_rater_submit", "coding", "collab"),
            ("coding_consensus_append", "coding", "collab"),
            ("coding_consensus_freeze", "coding", "collab"),
        )
        # P12-MAP: the table gained member_coding_note (coding_notes belong
        # to coding; P11 footprint family).  Nine legacy names plus
        # member_coding_note; multi-stage names account for multiple legal
        # name/stage rows above.
        self.assertEqual({name for name, _, _ in table}, {
            "member_vote", "member_undo", "member_coding", "member_coding_note",
            "member_appraisal", "member_annotation", "member_annotation_note",
            "coding_rater_submit", "coding_consensus_append",
            "coding_consensus_freeze",
        })
        for name, stage, target in table:
            with self.subTest(name=name, stage=stage):
                operation = self.checks.TeamWriteOp(name=name, stage=stage)
                self.assertEqual(self.checks.team_operation_target(operation), target)
        for name, stage in (("member_vote", "coding"), ("member_coding", "screen"),
                            ("member_annotation", "screen"), ("unknown", "screen")):
            with self.subTest(invalid=(name, stage)):
                with self.assertRaises(ValueError):
                    self.checks.team_operation_target(
                        self.checks.TeamWriteOp(name=name, stage=stage))

    def test_first_screen_vote_is_allowed_and_owner_management_does_not_bypass_role(self):
        # K2 is a canonical article with no stage1 vote; its first screen vote
        # has no upstream chain requirement.
        with self._inputs(actor_uid=USER_IDS["alice"], target_kind="actor") as inputs:
            self._call_without_hidden_connect(inputs, "member_vote", "screen", keys=("K2",))

        with self._inputs(actor_uid=USER_IDS["owner"], target_kind="actor") as inputs:
            before = self._connection_state(inputs)
            with mock.patch.object(sqlite3, "connect", side_effect=AssertionError("hidden connection open")):
                with self.assertRaises(self.collab.TeamRoleUnconfigured) as caught:
                    self._require(inputs, "member_vote", "screen", keys=("K2",))
            self.assertEqual(caught.exception.params.get("participant"), USER_IDS["owner"])
            self._assert_borrowed_and_read_only(inputs, before)

    def test_disabled_reconciler_is_not_a_screening_participant(self):
        with self._inputs(actor_uid=USER_IDS["carol"], target_kind="actor") as inputs:
            before = self._connection_state(inputs)
            with mock.patch.object(sqlite3, "connect", side_effect=AssertionError("hidden connection open")):
                with self.assertRaises(self.checks.TeamWriteBlocked) as caught:
                    self._require(inputs, "member_vote", "screen", keys=("K2",))
            self.assertEqual(caught.exception.status, 403)
            self.assertEqual(caught.exception.code, "screening_not_enabled")
            self.assertEqual(caught.exception.message_key, "team.screening_not_enabled")
            self._assert_borrowed_and_read_only(inputs, before)

    def test_fulltext_uses_current_stage1_chain_attestation_and_same_stage_history_lock(self):
        # P12: a fully-configured roster is required for the two-stage chain
        # (an unconfigured owner poisons configuration_status); the owner
        # takes the reconciler role so it never becomes a required screener.
        self.collab.grant_role(
            self.collab_path, actor_user_id=USER_IDS["owner"],
            user_id=USER_IDS["owner"], role="reconciler", task_id=TASK_ID)
        with self._inputs(actor_uid=USER_IDS["alice"], target_kind="actor") as inputs:
            self._append_stage1_include_attestation_after_alice_excludes(inputs)
            # Alice's vote and its proof-bearing attestation are uncommitted on
            # these exact borrowed connections; the current scope sees both.
            self._call_without_hidden_connect(inputs, "member_coding", "coding")
            self._call_without_hidden_connect(inputs, "member_undo", "coding")

            before = self._connection_state(inputs)
            with mock.patch.object(sqlite3, "connect", side_effect=AssertionError("hidden connection open")):
                # P12-MAP: previously ALLOWED — alice re-voting fulltext on
                # K1 where alice/bob are unanimously include is now a
                # trusted current-consistency completion: rejected 409.
                with self.assertRaises(self.checks.TeamWriteBlocked) as caught:
                    self._require(inputs, "member_vote", "fulltext")
            self.assertEqual(caught.exception.code, "stage_completed_read_only")
            self.assertEqual(caught.exception.stage, "fulltext")
            self.assertEqual(caught.exception.item_keys, ("K1",))
            self._assert_borrowed_and_read_only(inputs, before)

            before = self._connection_state(inputs)
            with mock.patch.object(sqlite3, "connect", side_effect=AssertionError("hidden connection open")):
                # P12-MAP: previously TeamHistoryReadOnlyError — the raw
                # same-stage lock is now the structured 409 payload.
                with self.assertRaises(self.checks.TeamWriteBlocked) as caught:
                    self._require(inputs, "member_vote", "screen")
            self.assertEqual(caught.exception.code, "stage_completed_read_only")
            self.assertEqual(caught.exception.stage, "screen")
            self.assertEqual(caught.exception.item_keys, ("K1",))
            self._assert_borrowed_and_read_only(inputs, before)

    def test_coding_and_appraisal_require_the_live_two_stage_chain(self):
        # Seeded K1 is final_include in both stages. K2 has no votes and must not
        # be promoted by a different key's complete history.
        # P12: configured roster required for the two-stage chain (owner takes
        # the reconciler role; it is not a required screener).
        self.collab.grant_role(
            self.collab_path, actor_user_id=USER_IDS["owner"],
            user_id=USER_IDS["owner"], role="reconciler", task_id=TASK_ID)
        with self._inputs(actor_uid=USER_IDS["alice"], target_kind="actor") as inputs:
            self._call_without_hidden_connect(inputs, "member_coding", "coding", keys=("K1",))
            self._call_without_hidden_connect(inputs, "member_appraisal", "coding", keys=("K1",))
            before = self._connection_state(inputs)
            with mock.patch.object(sqlite3, "connect", side_effect=AssertionError("hidden connection open")):
                with self.assertRaises(self.checks.TeamWriteBlocked) as caught:
                    self._require(inputs, "member_appraisal", "coding", keys=("K2",))
            # P12-MAP: previously "article_not_eligible" under the pre-P10
            # canonical projection; K2 is canonical now, so the rejection is
            # the live two-stage chain gate (upstream stage1 not final).
            self.assertTrue(caught.exception.code.startswith("team_item_upstream_stage1"))
            self.assertEqual(caught.exception.item_keys, ("K2",))
            self._assert_borrowed_and_read_only(inputs, before)

    def test_policy_version_pause_and_source_scope_apply_only_to_policy_operations(self):
        current_version = self.workflow.settings(self.owner_path)["version"]
        with self._inputs(actor_uid=USER_IDS["alice"], target_kind="actor") as inputs:
            before = self._connection_state(inputs)
            with mock.patch.object(sqlite3, "connect", side_effect=AssertionError("hidden connection open")):
                with self.assertRaises(self.checks.TeamWriteBlocked) as caught:
                    self._require(inputs, "member_vote", "screen", version=current_version - 1)
            self.assertEqual(caught.exception.code, "workflow_version_conflict")
            self.assertEqual(caught.exception.version, current_version)
            self._assert_borrowed_and_read_only(inputs, before)

        with self._inputs(actor_uid=USER_IDS["alice"], target_kind="actor") as inputs:
            # Update policy on the caller's locked owner connection without
            # committing; the check must read the same uncommitted snapshot.
            current = self.workflow.settings(self.owner_path, conn=inputs.owner_conn)
            stages = current["stages"]
            stages["screen"] = {"mode": "paused", "source_ids": []}
            inputs.owner_conn.execute(
                "UPDATE workflow_state SET stages_json=?, version=version+1 WHERE singleton=1",
                (json.dumps(stages, sort_keys=True),),
            )
            paused_version = current["version"] + 1
            before = self._connection_state(inputs)
            with mock.patch.object(sqlite3, "connect", side_effect=AssertionError("hidden connection open")):
                with self.assertRaises(self.checks.TeamWriteBlocked) as caught:
                    self._require(inputs, "member_vote", "screen", version=paused_version)
            self.assertEqual(caught.exception.code, "stage_paused")
            self._assert_borrowed_and_read_only(inputs, before)

        self._set_owner_policy("screen", mode="selected", source_ids=())
        with self._inputs(actor_uid=USER_IDS["alice"], target_kind="actor") as inputs:
            before = self._connection_state(inputs)
            with mock.patch.object(sqlite3, "connect", side_effect=AssertionError("hidden connection open")):
                with self.assertRaises(self.checks.TeamWriteBlocked) as caught:
                    self._require(inputs, "member_vote", "screen")
            self.assertEqual(caught.exception.code, "article_outside_scope")
            self._assert_borrowed_and_read_only(inputs, before)

    def test_nonpolicy_appraisal_and_annotation_ignore_paused_source_policy(self):
        self._set_owner_policy("fulltext", mode="paused")
        self._set_owner_policy("coding", mode="selected", source_ids=())
        # P12: configured roster required for the two-stage chain.
        self.collab.grant_role(
            self.collab_path, actor_user_id=USER_IDS["owner"],
            user_id=USER_IDS["owner"], role="reconciler", task_id=TASK_ID)
        with self._inputs(actor_uid=USER_IDS["alice"], target_kind="actor") as inputs:
            # member_appraisal has no policy gate; role and chain still run.
            self._call_without_hidden_connect(inputs, "member_appraisal", "coding")

            before = self._connection_state(inputs)
            with mock.patch.object(sqlite3, "connect", side_effect=AssertionError("hidden connection open")):
                # P12-MAP: previously ALLOWED — annotation add on K1 where
                # alice/bob are unanimously include on fulltext is now a
                # current-consistency completion: rejected 409 (the A-rule
                # addendum keeps member_annotation non-policy, and the
                # completion lock is not a personal policy).
                with self.assertRaises(self.checks.TeamWriteBlocked) as caught:
                    self._require(inputs, "member_annotation", "fulltext")
            self.assertEqual(caught.exception.code, "stage_completed_read_only")
            self._assert_borrowed_and_read_only(inputs, before)

            before = self._connection_state(inputs)
            with mock.patch.object(sqlite3, "connect", side_effect=AssertionError("hidden connection open")):
                with self.assertRaises(self.checks.TeamWriteBlocked) as caught:
                    self._require(inputs, "member_annotation_note", "fulltext")
            self.assertEqual(caught.exception.code, "stage_paused")
            self._assert_borrowed_and_read_only(inputs, before)

            with self.assertRaises(ValueError):
                self.checks.require_team_write(
                    inputs,
                    operation=self.checks.TeamWriteOp(
                        name="member_appraisal", stage="coding", expected_version=1),
                    item_keys=("K1",),
                )

        with self._inputs(actor_uid=USER_IDS["carol"], target_kind="collab") as inputs:
            # Consensus append is also outside the added settings/source policy
            # gates, while retaining its reconciler role and chain requirements.
            self._call_without_hidden_connect(inputs, "coding_consensus_append", "coding")

    def test_coder_submit_and_reconciler_consensus_roles_remain_distinct(self):
        # P12: configured roster required for the coding chain.
        self.collab.grant_role(
            self.collab_path, actor_user_id=USER_IDS["owner"],
            user_id=USER_IDS["owner"], role="reconciler", task_id=TASK_ID)
        with self._inputs(actor_uid=USER_IDS["alice"], target_kind="collab") as inputs:
            self._call_without_hidden_connect(inputs, "coding_rater_submit", "coding")
            before = self._connection_state(inputs)
            with mock.patch.object(sqlite3, "connect", side_effect=AssertionError("hidden connection open")):
                with self.assertRaises(self.collab.TeamRoleDenied):
                    self._require(inputs, "coding_consensus_append", "coding")
            self._assert_borrowed_and_read_only(inputs, before)

        with self._inputs(actor_uid=USER_IDS["carol"], target_kind="collab") as inputs:
            self._call_without_hidden_connect(inputs, "coding_consensus_append", "coding")
            self._call_without_hidden_connect(inputs, "coding_consensus_freeze", "coding")
            before = self._connection_state(inputs)
            with mock.patch.object(sqlite3, "connect", side_effect=AssertionError("hidden connection open")):
                with self.assertRaises(self.collab.TeamRoleDenied):
                    self._require(inputs, "coding_rater_submit", "coding")
            self._assert_borrowed_and_read_only(inputs, before)

    def test_batch_ineligible_key_rejects_whole_request_and_target_is_object_identity(self):
        with self._inputs(actor_uid=USER_IDS["alice"], target_kind="actor") as inputs:
            before = self._connection_state(inputs)
            with mock.patch.object(sqlite3, "connect", side_effect=AssertionError("hidden connection open")):
                with self.assertRaises(self.checks.TeamWriteBlocked) as caught:
                    self._require(inputs, "member_vote", "screen", keys=("K1", "NO-SUCH-ARTICLE"))
            self.assertEqual(caught.exception.code, "article_not_eligible")
            self.assertEqual(caught.exception.item_keys, ("NO-SUCH-ARTICLE",))
            self._assert_borrowed_and_read_only(inputs, before)

        # The service must reject a wrong retained target before role/eligibility
        # checks; a valid alias or ctx.can_manage cannot substitute for identity.
        with self._inputs(actor_uid=USER_IDS["alice"], target_kind="actor") as inputs:
            before = self._connection_state(inputs)
            with mock.patch.object(sqlite3, "connect", side_effect=AssertionError("hidden connection open")):
                with self.assertRaises(RuntimeError):
                    self._require(inputs, "coding_consensus_append", "coding")
            self._assert_borrowed_and_read_only(inputs, before)


    def test_new_required_uid_first_vote_is_rejected_on_completed_stages(self):
        # P12-MAP: was test_new_required_uid_first_vote_preserves_old_traces_
        # and_only_vote_gets_exception — the s2b first-current-vote addendum
        # is NOT_ADOPTED (2026-11-15 ruling): Dave's first vote on a
        # completed stage is now rejected; existing raw adjudication and
        # attestation rows survive untouched.
        # RUN1-FIX(#1): the alice window's fulltext ops cross the two-stage
        # chain, so the roster must be fully configured — an unconfigured
        # owner poisons configuration_status and the chain gate reports
        # team_item_upstream_stage1_awaiting_config before the completion
        # lock fires (the completion facts themselves — committed
        # attestations on both stages — were already correctly built; the
        # anchor case itself, Dave's screen vote, crosses no chain and
        # never needed the grant).  Same reconciler grant as every other
        # chain-crossing test in this suite.
        self.collab.grant_role(
            self.collab_path, actor_user_id=USER_IDS["owner"],
            user_id=USER_IDS["owner"], role="reconciler", task_id=TASK_ID)
        for stage, table in (("screen", "decisions"), ("fulltext", "stage2_decisions")):
            with closing(sqlite3.connect(self.user_paths[USER_IDS["alice"]])) as connection:
                connection.execute(
                    f"UPDATE {table} SET decision='exclude' WHERE zotero_key='K1'")
                connection.commit()
            self._append_committed_attestation(stage=stage, keys=("K1",))

        # Undo, annotation, and annotation-note stay subject to the
        # same-stage completion lock (P12-MAP: was TeamHistoryReadOnlyError).
        with self._inputs(actor_uid=USER_IDS["alice"], target_kind="actor") as inputs:
            for operation, stage in (("member_undo", "screen"),
                                     ("member_annotation", "fulltext"),
                                     ("member_annotation_note", "fulltext")):
                before = self._connection_state(inputs)
                with mock.patch.object(sqlite3, "connect", side_effect=AssertionError("hidden connection open")):
                    with self.assertRaises(self.checks.TeamWriteBlocked) as caught:
                        self._require(inputs, operation, stage, keys=("K1",))
                self.assertEqual(caught.exception.code, "stage_completed_read_only")
                self._assert_borrowed_and_read_only(inputs, before)

        dave_uid, dave_path = self._add_new_required_coder()
        with self._inputs(actor_uid=dave_uid, target_kind="actor",
                          actor_path_override=dave_path,
                          additional_member_paths={dave_uid: dave_path}) as inputs:
            self.assertIsNone(inputs.actor_conn.execute(
                "SELECT 1 FROM decisions WHERE zotero_key='K1'").fetchone())
            _, revisions = self.ledger.vote_snapshot(inputs.actor_conn, "stage1")
            self.assertNotIn("K1", revisions)
            trace_before = self._attestation_snapshot(inputs.collab_conn)

            # P12-MAP: was ALLOWED (first-vote exception) — now rejected.
            before = self._connection_state(inputs)
            with mock.patch.object(sqlite3, "connect", side_effect=AssertionError("hidden connection open")):
                with self.assertRaises(self.checks.TeamWriteBlocked) as caught:
                    self._require(inputs, "member_vote", "screen", keys=("K1",))
            self.assertEqual(caught.exception.code, "stage_completed_read_only")
            self._assert_borrowed_and_read_only(inputs, before)
            self.assertEqual(self._attestation_snapshot(inputs.collab_conn), trace_before)
            self.assertIsNone(inputs.actor_conn.execute(
                "SELECT 1 FROM decisions WHERE zotero_key='K1'").fetchone(),
                "the check itself must not append a vote")

            # Model the real writer on the same retained connection; even a
            # current row cannot reopen the completed stage.
            inputs.actor_conn.execute(
                "INSERT INTO decisions(zotero_key,decision,exclusion_reason,notes,tags,screened_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?)",
                ("K1", "include", "", "", "", _SEEDED_AT, _SEEDED_AT),
            )
            after_write = self._connection_state(inputs)
            with mock.patch.object(sqlite3, "connect", side_effect=AssertionError("hidden connection open")):
                with self.assertRaises(self.checks.TeamWriteBlocked) as caught:
                    self._require(inputs, "member_vote", "screen", keys=("K1",))
            self.assertEqual(caught.exception.code, "stage_completed_read_only")
            self._assert_borrowed_and_read_only(inputs, after_write)
            self.assertEqual(self._attestation_snapshot(inputs.collab_conn), trace_before)

    def test_previously_bound_uid_is_rejected_after_current_decision_row_is_deleted(self):
        with closing(sqlite3.connect(self.user_paths[USER_IDS["alice"]])) as connection:
            connection.execute("UPDATE decisions SET decision='exclude' WHERE zotero_key='K1'")
            connection.commit()
        dave_uid, dave_path = self._add_new_required_coder()
        self._write_vote(dave_path, "K1", "screen", "include")
        self._append_committed_attestation(
            stage="screen", keys=("K1",), required_names=("alice", "bob", "dave"),
            extra_user_paths={"dave": dave_path},
        )
        with closing(sqlite3.connect(dave_path)) as connection:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("DELETE FROM decisions WHERE zotero_key='K1'")
            connection.commit()

        with self._inputs(actor_uid=dave_uid, target_kind="actor",
                          actor_path_override=dave_path,
                          additional_member_paths={dave_uid: dave_path}) as inputs:
            self.assertIsNone(inputs.actor_conn.execute(
                "SELECT 1 FROM decisions WHERE zotero_key='K1'").fetchone())
            # RUN1-FIX(#3 family): vote_snapshot's mapping only covers live
            # decision rows (FROM decisions LEFT JOIN revisions); a durable
            # delete-side revision (tombstone) lives in team_vote_revisions
            # ("DELETE 后再 INSERT 不重置" per the ledger module contract).
            tombstone_keys = {row[0] for row in inputs.actor_conn.execute(
                "SELECT item_key FROM team_vote_revisions WHERE stage='stage1'")}
            self.assertIn("K1", tombstone_keys,
                          "the delete must leave a ledger tombstone")
            trace_before = self._attestation_snapshot(inputs.collab_conn)
            before = self._connection_state(inputs)
            with mock.patch.object(sqlite3, "connect", side_effect=AssertionError("hidden connection open")):
                # P12-MAP: was TeamHistoryReadOnlyError (a RuntimeError
                # subclass, code history_read_only_after_arbitration) from
                # the ledger-tombstone arm of the removed first-vote
                # exception — the durable raw adjudication now rejects with
                # the structured 409 payload.
                with self.assertRaises(self.checks.TeamWriteBlocked) as caught:
                    self._require(inputs, "member_vote", "screen", keys=("K1",))
            self.assertEqual(caught.exception.code, "stage_completed_read_only")
            self._assert_borrowed_and_read_only(inputs, before)
            self.assertEqual(self._attestation_snapshot(inputs.collab_conn), trace_before)

    def test_tombstone_journal_existing_row_and_mixed_batch_all_reject(self):
        self._add_extra_articles(("K3", "K4"))
        with closing(sqlite3.connect(self.user_paths[USER_IDS["alice"]])) as connection:
            connection.execute("UPDATE decisions SET decision='exclude' WHERE zotero_key='K1'")
            connection.commit()
        self._append_committed_attestation(stage="screen", keys=("K1",))
        dave_uid, dave_path = self._add_new_required_coder(keys=ITEM_KEYS + ("K3", "K4"))

        # K2 has a durable delete revision; K3 has an old undo journal even
        # though it is marked both undone and superseded; K4 retains a vote row.
        self._write_vote(dave_path, "K2", "screen", "include")
        with closing(sqlite3.connect(dave_path)) as connection:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("DELETE FROM decisions WHERE zotero_key='K2'")
            connection.commit()
        undo = importlib.import_module("coscreen.workspace_undo")
        with closing(sqlite3.connect(dave_path)) as connection:
            undo.begin_save(connection)
            connection.execute(
                "INSERT INTO workspace_undo(task_id,actor_id,stage,zotero_key,before_json,after_json,undone,superseded) "
                "VALUES(?,?,?,?,?,?,1,1)",
                (TASK_ID, str(dave_uid), "screen", "K3", "{}", "{}"),
            )
            connection.commit()
        # P12-MAP: was a non-binary "maybe" row.  "maybe" is a legal domain
        # value (_VOTE_DECISIONS includes it), so the row loaded fine; its
        # only purpose was exercising _actor_vote_row_exists' "any row,
        # including non-binary" counting — an old-trace arm that existed
        # solely to fence the removed first-vote exception.  With that arm
        # gone the shape nuance carries no authorizer semantics, so the
        # durable old row is modeled as a plain include instead.
        self._write_vote(dave_path, "K4", "screen", "include")

        with self._inputs(actor_uid=dave_uid, target_kind="actor",
                          actor_path_override=dave_path,
                          additional_member_paths={dave_uid: dave_path}) as inputs:
            # RUN1-FIX(#3): vote_snapshot's mapping only covers live
            # decision rows; K2's durable delete-side revision (tombstone)
            # lives in team_vote_revisions, so assert the tombstone there.
            tombstone_keys = {row[0] for row in inputs.actor_conn.execute(
                "SELECT item_key FROM team_vote_revisions WHERE stage='stage1'")}
            self.assertIn("K2", tombstone_keys)
            self.assertIsNone(inputs.actor_conn.execute(
                "SELECT 1 FROM decisions WHERE zotero_key='K2'").fetchone())
            journal = inputs.actor_conn.execute(
                "SELECT undone,superseded FROM workspace_undo "
                "WHERE task_id=? AND actor_id=? AND stage='screen' AND zotero_key='K3'",
                (TASK_ID, str(dave_uid)),
            ).fetchone()
            self.assertEqual(tuple(journal), (1, 1))
            self.assertIsNotNone(inputs.actor_conn.execute(
                "SELECT 1 FROM decisions WHERE zotero_key='K4'").fetchone())

            # P12-MAP: K2/K3/K4 previously raised RuntimeError (tombstone/
            # undo-journal/old-row arms of the removed first-vote exception).
            # Those keys carry no completion fact, so for the pure authorizer
            # they are ordinary writable current state; row-level ledger
            # rules remain the writer's concern.
            for key in ("K2", "K3", "K4"):
                before = self._connection_state(inputs)
                with mock.patch.object(sqlite3, "connect", side_effect=AssertionError("hidden connection open")):
                    self._require(inputs, "member_vote", "screen", keys=(key,))
                self._assert_borrowed_and_read_only(inputs, before)

            # K1 is completed; mixing it into a batch rejects the whole
            # request without creating a row for K2 or changing any source.
            before = self._connection_state(inputs)
            with mock.patch.object(sqlite3, "connect", side_effect=AssertionError("hidden connection open")):
                with self.assertRaises(self.checks.TeamWriteBlocked) as caught:
                    self._require(inputs, "member_vote", "screen", keys=("K1", "K2"))
            self.assertEqual(caught.exception.code, "stage_completed_read_only")
            self.assertEqual(caught.exception.item_keys, ("K1",))
            self._assert_borrowed_and_read_only(inputs, before)
            self.assertIsNone(inputs.actor_conn.execute(
                "SELECT 1 FROM decisions WHERE zotero_key='K1'").fetchone())

    def test_noncanonical_proof_uid_fails_closed_for_new_vote(self):
        with closing(sqlite3.connect(self.user_paths[USER_IDS["alice"]])) as connection:
            connection.execute("UPDATE decisions SET decision='exclude' WHERE zotero_key='K1'")
            library_id, revisions = self.ledger.vote_snapshot(connection, "stage1")
            connection.commit()
        malformed = {"K1": {"not-a-uid": {
            "library_id": library_id, "revision": int(revisions["K1"]["revision"]),
        }}}
        dave_uid, dave_path = self._add_new_required_coder()
        self._append_committed_attestation(
            stage="screen", keys=("K1",), extra_proof=malformed,
        )

        with self._inputs(actor_uid=dave_uid, target_kind="actor",
                          actor_path_override=dave_path,
                          additional_member_paths={dave_uid: dave_path}) as inputs:
            before = self._connection_state(inputs)
            with mock.patch.object(sqlite3, "connect", side_effect=AssertionError("hidden connection open")):
                # P12-MAP: was the typed fail-closed trio (the removed
                # exception parsed proof shapes).  The guard no longer
                # consults proof text at all; the durable raw adjudication
                # rejects fail-closed with the structured 409 payload, and
                # P0x storage-level typed errors still pass through
                # unchanged when the store itself is broken.
                with self.assertRaises(self.checks.TeamWriteBlocked) as caught:
                    self._require(inputs, "member_vote", "screen", keys=("K1",))
            self.assertEqual(caught.exception.code, "stage_completed_read_only")
            self._assert_borrowed_and_read_only(inputs, before)


if __name__ == "__main__":  # pragma: no cover - unittest entry point
    unittest.main()
