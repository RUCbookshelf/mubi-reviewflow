"""P14 team lifecycle tests (C05/C10 author side; 2.1.6).

Seven P14-named tests for the ``custom_backend.team_source_lifecycle`` P14
extension (``prepare_team_protocol_storage`` / ``adopt_team_protocol_on`` /
``run_team_management``).  Product imports and SQLite fixture work are
deferred to unittest execution; this file must stay loadable under
``python -I -B``.

The fixture reuses the proven P13 harness shape (real collaboration roles,
real vote ledger via ``coscreen.db.init_db``, B1-reserved native connections,
TSR borrowed scope) on top of the genuine B1 reservation manager.  The P10
window entry is patched at its defining module (``team_screening_write``)
because the P14 product resolves it with a deferred import (P09 already
imports P06 symbols from the same module; a module-level import would cycle).
The management window always selects the collaboration DB as the single
commit target (C10: one collab-target coordinator).
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

TASK_ID = "lifecycle-task-1"
INSTANCE_ID = "lifecycle-instance-1"
OWNER, ALICE, BOB, CAROL, DAVE = 1, 2, 3, 4, 5
#: accounts rows (DAVE has an account but, until test 5 grants him, no role).
ACCOUNT_NAMES = {OWNER: "owner", ALICE: "alice", BOB: "bob", CAROL: "carol",
                 DAVE: "dave"}
#: members with physical libraries in the base fixture.
DB_NAMES = {OWNER: "owner", ALICE: "alice", BOB: "bob", CAROL: "carol"}
ITEM_KEYS = ("K1",)
_SEED = "2026-11-15T00:00:00+00:00"
_LIB = "a" * 32


class TeamLifecycleTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="p14-lifecycle-")
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
        self.registry = importlib.import_module("coscreen.team_terminal_registry")
        self.receipts = importlib.import_module("coscreen.team_flow_receipts")
        self.tasks = importlib.import_module("coscreen.tasks")

        self._build_store(self.root, register_protocol=True)

    # ------------------------------------------------------------------
    # fixture
    # ------------------------------------------------------------------
    def _build_store(self, base: Path, *, register_protocol: bool):
        self.task_dir = base / "task"
        self.task_dir.mkdir()
        self.info = SimpleNamespace(
            task_id=TASK_ID, dir=self.task_dir, degraded=False)
        self.accounts_path = base / "users.db"
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
                             [(uid, name) for uid, name in ACCOUNT_NAMES.items()])
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
            self.db.init_db(path)  # real vote ledger + triggers
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
        # R1/D-1：collab 侧准备（F2 registry + receipts + F1 facts）一律走
        # 产品显式准备入口——夹具不再手工补齐 collab facts（该手工补齐曾
        # 掩盖 prepare 入口漏装 F1 的缺口）。
        self.lifecycle.prepare_team_protocol_storage(
            collab_path=self.collab_path, source_paths=())
        if register_protocol:
            with closing(sqlite3.connect(self.collab_path)) as conn:
                conn.row_factory = sqlite3.Row
                conn.execute("BEGIN IMMEDIATE")
                self.registry.register_protocol_on(
                    conn, task_id=TASK_ID, task_instance_id=INSTANCE_ID,
                    owner_user_id=OWNER, owner_library_id=_LIB,
                    creation_path="trusted_new_task")
                for uid, path in self.user_paths.items():
                    with closing(sqlite3.connect(path)) as own:
                        library, _ = self.ledger.vote_snapshot(own, "stage1")
                    self.registry.register_source_on(
                        conn, task_instance_id=INSTANCE_ID, source_user_id=uid,
                        source_library_id=library,
                        source_path_identity=path.name)
                conn.commit()
        self.runtime = SimpleNamespace(host="isolated-test")

    def _seed_vote(self, uid: int, key: str, stage: str, decision: str):
        table = {"screen": "decisions", "fulltext": "stage2_decisions"}[stage]
        with closing(sqlite3.connect(self.user_paths[uid])) as conn:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute(
                f"INSERT INTO {table}(zotero_key,decision,exclusion_reason,"
                "notes,tags,screened_at,updated_at) "
                "VALUES(?,?, '', '', '', ?, ?)",
                (key, decision, _SEED, _SEED))
            conn.commit()

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

    # ------------------------------------------------------------------
    # window seam: genuine B1 reservation machinery, collab target
    # ------------------------------------------------------------------
    @contextmanager
    def _window(self, runtime, info, user, *, target_kind, operation,
                item_keys):
        assert target_kind == "collab", "management must borrow the collab"
        paths = list(dict.fromkeys(
            (self.owner_path, self.accounts_path, *self.user_paths.values())))
        with self.resource.retained_write_connections(self.collab_path) \
                as reserved:
            reserved.reserve(
                self.owner_path, [p for p in paths if p != self.owner_path])
            all_paths = list(dict.fromkeys((self.collab_path, *paths)))
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

    # ------------------------------------------------------------------
    # management helpers
    # ------------------------------------------------------------------
    def _manage(self, *, action, target, payload=None,
                request_id=None, request_sha256="b" * 64):
        request_id = request_id or f"req-{action}-{target}"
        with mock.patch.object(self.inputs_api,
                               "retained_team_write_inputs", self._window):
            return self.lifecycle.run_team_management(
                self.runtime, self.info,
                {"user_id": OWNER, "username": "owner"},
                action=action, target_user_id=target,
                payload=payload, request_id=request_id,
                request_sha256=request_sha256)

    def _adopt(self, *, request_id="req-adopt-1"):
        with self._window(
                self.runtime, self.info,
                {"user_id": OWNER, "username": "owner"},
                target_kind="collab",
                operation=self.lifecycle.TeamOperation(
                    name="team_management", stage="screen"),
                item_keys=("__team_management__",)) as inputs:
            return self.lifecycle.adopt_team_protocol_on(
                inputs, request_id=request_id, request_sha256="b" * 64)

    def _observation_seals(self):
        with closing(sqlite3.connect(self.collab_path)) as conn:
            conn.row_factory = sqlite3.Row
            rows = []
            for r in conn.execute(
                    "SELECT stage, item_key, origin, payload_json "
                    "FROM team_terminal_seals"):
                row = dict(r)
                payload = json.loads(row.pop("payload_json"))
                row["roster_revision"] = payload["protocol"]["roster_revision"]
                row["roster"] = payload["roster"]
                row["required_uids"] = payload["required_uids"]
                rows.append(row)
            return rows

    def _archived_receipt_payloads(self):
        with closing(sqlite3.connect(self.collab_path)) as conn:
            return [json.loads(r[0]) for r in conn.execute(
                "SELECT payload_json FROM team_flow_receipts "
                "WHERE origin='actor_archive'")]

    def _management_receipt_payloads(self):
        with closing(sqlite3.connect(self.collab_path)) as conn:
            return [json.loads(r[0]) for r in conn.execute(
                "SELECT payload_json FROM team_flow_receipts "
                "WHERE origin='collab'")]

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
    # 0. explicit schema preparation (D-1): collab gets F1 facts in place
    # ------------------------------------------------------------------
    def test_prepare_team_protocol_storage(self):
        self._tmp3 = tempfile.TemporaryDirectory(prefix="p14-prepare-")
        self.addCleanup(self._tmp3.cleanup)
        base = Path(self._tmp3.name)
        collab = base / "collab.db"
        member = base / "member.db"
        for path in (collab, member):
            sqlite3.connect(path).close()  # empty SQLite carriers

        self.lifecycle.prepare_team_protocol_storage(
            collab_path=collab, source_paths=(member,))

        def tables(path):
            with closing(sqlite3.connect(path)) as conn:
                return {row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")}

        collab_tables = tables(collab)
        # D-1 anchor: collab F1 facts are in place after prepare (the
        # observation/archive target of run_team_management).
        self.assertIn("team_terminal_seals", collab_tables)
        self.assertIn("team_terminal_facts_meta", collab_tables)
        self.assertIn("team_task_protocol", collab_tables)      # F2 registry
        self.assertIn("team_source_registry", collab_tables)
        self.assertIn("team_flow_receipts", collab_tables)      # P07
        member_tables = tables(member)
        self.assertIn("team_terminal_seals", member_tables)
        self.assertIn("team_flow_receipts", member_tables)
        # idempotent re-preparation is harmless
        self.lifecycle.prepare_team_protocol_storage(
            collab_path=collab, source_paths=(member,))
        # parameter shape misuse stays typed
        with self.assertRaises(TypeError):
            self.lifecycle.prepare_team_protocol_storage(
                collab_path="not-a-path", source_paths=())
        with self.assertRaises(ValueError):
            self.lifecycle.prepare_team_protocol_storage(
                collab_path=Path("relative.db"), source_paths=())

    # ------------------------------------------------------------------
    # 1. add/remove/toggle keep the completed-stage roster (user anchor)
    # ------------------------------------------------------------------
    def test_add_remove_toggle_keep_completed_roster(self):
        self._seed_vote(ALICE, "K1", "screen", "include")
        self._seed_vote(BOB, "K1", "screen", "include")
        previous_revision = self._protocol()["roster_revision"]

        removed = self._manage(action="remove", target=BOB,
                               payload={"role": "coder"},
                               request_id="req-anchor-remove")
        self.assertEqual(removed["outcome"], "committed")
        seals = self._observation_seals()
        self.assertEqual(len(seals), 1)
        completed = seals[0]
        completed_uids = sorted(p["uid"] for p in completed["roster"])
        self.assertIn(ALICE, completed_uids)
        self.assertIn(BOB, completed_uids)
        # anchor: observation inside the management transaction used the OLD
        # revision
        self.assertEqual(completed["roster_revision"], previous_revision)

        added = self._manage(action="add", target=BOB,
                             payload={"role": "coder"},
                             request_id="req-anchor-add")
        self.assertEqual(added["outcome"], "committed")
        toggled = self._manage(action="toggle", target=OWNER,
                               payload={"enabled": True},
                               request_id="req-anchor-toggle")
        self.assertEqual(toggled["outcome"], "committed")

        # completed stage keeps the roster/opinions/conclusion of its time:
        # the persisted seal is untouched by later roster changes.
        after = self._observation_seals()
        self.assertEqual(len(after), 1, "no re-seal after roster changes")
        self.assertEqual(after[0]["roster"], completed["roster"])
        self.assertEqual(after[0]["roster_revision"], previous_revision)
        self.assertGreater(self._protocol()["roster_revision"],
                           previous_revision)
        self.assertEqual(self._grant_count(BOB, "coder"), 1,
                         "re-added member keeps exactly one grant")

    # ------------------------------------------------------------------
    # 2. observation happens before the new member joins (old revision)
    # ------------------------------------------------------------------
    def test_observe_old_before_new_member(self):
        self._seed_vote(ALICE, "K1", "screen", "include")
        self._seed_vote(BOB, "K1", "screen", "include")
        previous_revision = self._protocol()["roster_revision"]

        # DAVE has an account but no grant and no member library yet: the
        # pre-change roster cannot contain him.
        result = self._manage(action="add", target=DAVE,
                              payload={"role": "coder"},
                              request_id="req-observe-add")
        self.assertEqual(result["outcome"], "committed")
        seals = self._observation_seals()
        self.assertEqual(len(seals), 1)
        roster_uids = sorted(p["uid"] for p in seals[0]["roster"])
        self.assertIn(ALICE, roster_uids)
        self.assertIn(BOB, roster_uids)
        self.assertNotIn(DAVE, roster_uids,
                         "observation must reflect the pre-change roster")
        # anchor: the in-transaction observation carries the previous revision
        self.assertEqual(seals[0]["roster_revision"], previous_revision)
        self.assertEqual(result["roster_revision"], previous_revision + 1)

    # ------------------------------------------------------------------
    # 3. removal forms a new consensus under the new roster/revision
    # ------------------------------------------------------------------
    def test_remove_forms_new_consensus_with_roster_change(self):
        self._seed_vote(ALICE, "K1", "screen", "include")
        self._seed_vote(BOB, "K1", "screen", "exclude")
        previous_revision = self._protocol()["roster_revision"]

        result = self._manage(action="remove", target=BOB,
                              payload={"role": "coder"},
                              request_id="req-consensus-remove")
        self.assertEqual(result["outcome"], "committed")
        seals = self._observation_seals()
        # old set disagreed: no old-revision seal; the new set (alice only)
        # is unanimous for the first time at the NEW revision.
        self.assertEqual(len(seals), 1)
        new_roster_uids = [p["uid"] for p in seals[0]["roster"]]
        self.assertNotIn(BOB, new_roster_uids,
                         "removed member must leave the new-consensus roster")
        self.assertIn(ALICE, new_roster_uids)
        self.assertEqual(seals[0]["required_uids"], [ALICE])
        self.assertEqual(seals[0]["roster_revision"], previous_revision + 1)
        self.assertEqual(result["roster_revision"], previous_revision + 1)
        self.assertEqual(self._grant_count(BOB, "coder"), 0)

    # ------------------------------------------------------------------
    # 4. a legal observer source (not required) is archived on retirement
    # ------------------------------------------------------------------
    def test_observer_not_required_is_archived(self):
        self._seed_actor_receipt(CAROL, "carol-actor-receipt-1")
        carol_source = self._source(CAROL)
        self.assertIsNotNone(carol_source,
                             "observer must be a registered source")

        result = self._manage(action="remove", target=CAROL,
                              payload={"role": "reconciler"},
                              request_id="req-observer-remove")
        self.assertEqual(result["outcome"], "committed")
        archived = self._archived_receipt_payloads()
        self.assertEqual(len(archived), 1,
                         "observer's actor receipts must be archived")
        self.assertEqual(archived[0]["request_id"], "carol-actor-receipt-1")
        evidence = self._management_receipt_payloads()[-1][
            "internal_evidence"]["archive_evidence"]
        self.assertEqual(evidence["registration_id"],
                         carol_source["registration_id"])
        self.assertTrue(evidence["verified_per_copy"])
        self.assertIsNone(self._source(CAROL), "observer source retired")

    # ------------------------------------------------------------------
    # 5. adoption is forward-only, pending stays pending (no single-key
    #    auto-verified)
    # ------------------------------------------------------------------
    def test_adoption_forward_only_and_pending(self):
        self._tmp2 = tempfile.TemporaryDirectory(prefix="p14-adopt-")
        self.addCleanup(self._tmp2.cleanup)
        self._build_store(Path(self._tmp2.name), register_protocol=False)
        # DAVE: granted coder with an account but NO member library — the
        # roster holds a necessary source that cannot be verified.
        self.collab.grant_role(
            self.collab_path, actor_user_id=OWNER, user_id=DAVE,
            role="coder", task_id=TASK_ID)

        # (a) unverifiable necessary source -> adoption stays pending and
        #     records the identity issue; management stays configurable.
        pending = self._adopt(request_id="req-adopt-pending")
        self.assertEqual(pending["coverage_state"],
                         "legacy_pending_verification")
        self.assertTrue(pending["pending_issues"])
        self.assertIsNone(pending["receipt"])
        # single-key current consistency must not flip the pending state
        self._seed_vote(ALICE, "K1", "screen", "include")
        self._seed_vote(BOB, "K1", "screen", "include")
        toggle = self._manage(action="toggle", target=OWNER,
                              payload={"enabled": True},
                              request_id="req-adopt-toggle")
        self.assertEqual(toggle["outcome"], "committed",
                         "management stays configurable while pending")
        self.assertEqual(self._observation_seals(), [],
                         "dave's absent opinion blocks unanimity")
        self.assertEqual(self._protocol()["coverage_state"],
                         "legacy_pending_verification",
                         "single-key consistency must not auto-verify")

        # (b) remove the unverifiable member -> explicit adoption verifies
        #     with prior_history_unknown=True and a forward coverage start.
        removed = self._manage(action="remove", target=DAVE,
                               payload={"role": "coder"},
                               request_id="req-adopt-remove-dave")
        self.assertEqual(removed["outcome"], "committed")
        adopted = self._adopt(request_id="req-adopt-verified")
        self.assertEqual(adopted["coverage_state"], "legacy_verified")
        self.assertIsNotNone(adopted["receipt"])
        self.assertIsNotNone(adopted["forward_coverage_start"])
        record = self._protocol()
        self.assertEqual(record["coverage_state"], "legacy_verified")
        evidence = json.loads(record["adoption_evidence_json"])
        self.assertTrue(evidence["prior_history_unknown"])
        self.assertTrue(evidence["forward_coverage_start"])
        self.assertGreaterEqual(record["roster_revision"], 1)
        # idempotent replay returns the original result
        replay = self._adopt(request_id="req-adopt-verified")
        self.assertTrue(replay["idempotent"])

    # ------------------------------------------------------------------
    # 6. archive failure keeps the old role (whole-change rollback)
    # ------------------------------------------------------------------
    def test_archive_failure_keeps_old_role(self):
        self._seed_actor_receipt(BOB, "bob-actor-receipt-1")
        previous_revision = self._protocol()["roster_revision"]

        def failing_archive(*args, **kwargs):
            raise RuntimeError("archive boom")

        with mock.patch.object(self.lifecycle, "archive_seals_on",
                               failing_archive):
            with self.assertRaisesRegex(RuntimeError, "archive boom"):
                self._manage(action="remove", target=BOB,
                             payload={"role": "coder"},
                             request_id="req-archive-fail")
        # whole role change rolled back: grant kept, no receipt, no revision
        self.assertEqual(self._grant_count(BOB, "coder"), 1)
        self.assertEqual(self._management_receipt_payloads(), [])
        self.assertEqual(self._observation_seals(), [])
        self.assertEqual(self._protocol()["roster_revision"],
                         previous_revision)
        self.assertIsNotNone(self._source(BOB), "source not retired")

    # ------------------------------------------------------------------
    # 7. rejoin uses a new generation; retry archive succeeds
    # ------------------------------------------------------------------
    def test_rejoin_new_generation_and_retry_archive(self):
        self._seed_actor_receipt(BOB, "bob-actor-receipt-1")
        first = self._manage(action="remove", target=BOB,
                             payload={"role": "coder"},
                             request_id="req-rejoin-remove-1")
        self.assertEqual(first["outcome"], "committed")
        first_generation = self._management_receipt_payloads()[-1][
            "internal_evidence"]["archive_evidence"]["generation"]

        rejoined = self._manage(action="add", target=BOB,
                                payload={"role": "coder"},
                                request_id="req-rejoin-add")
        self.assertEqual(rejoined["outcome"], "committed")
        active = self._source(BOB)
        self.assertIsNotNone(active, "rejoin registers a new active source")
        self.assertGreater(int(active["generation"]), first_generation,
                           "rejoin must use a new registration generation")

        retried = self._manage(action="remove", target=BOB,
                               payload={"role": "coder"},
                               request_id="req-rejoin-remove-2")
        self.assertEqual(retried["outcome"], "committed")
        self.assertIsNone(self._source(BOB), "second retirement sticks")
        # retry archive kept byte-faithful copies of the actor receipt
        archived_requests = {payload["request_id"]
                             for payload in self._archived_receipt_payloads()}
        self.assertEqual(archived_requests, {"bob-actor-receipt-1"})


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
