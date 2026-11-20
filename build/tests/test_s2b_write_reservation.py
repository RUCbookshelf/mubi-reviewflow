"""S2-B1 retained SQLite write reservation contract tests.

Contract source: .superpowers/sdd/2026-11-14-2.1.6-maintenance/
s2b-resource-brief.md. This module imports the product resource lazily and creates
all databases only inside test execution in a fresh temporary directory.
"""
from __future__ import annotations

import importlib
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing, contextmanager
from pathlib import Path
from unittest import mock


class _InjectedFailure(Exception):
    """Distinct marker used to verify the exact injected failure propagates."""


class _ConnectionControl:
    """Install real sqlite3.Connection subclasses with narrowly controlled faults."""

    def __init__(self):
        self.connections: list[sqlite3.Connection] = []
        self.close_order: list[sqlite3.Connection] = []
        self.begin_attempts = 0
        self.fail_begin_at: int | None = None
        self.begin_failure: BaseException | None = None
        self.commit_failure: BaseException | None = None
        self.close_failure_connection: sqlite3.Connection | None = None
        self.close_failure: BaseException | None = None
        self.close_failure_raised = False
        self.raw_connect = None
        self.forbidden_operations: list[str] = []
        self.before_commit = None

    @contextmanager
    def installed(self, resource_module):
        control = self
        original_connect = sqlite3.connect
        control.raw_connect = original_connect

        class ControlledConnection(sqlite3.Connection):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.commit_calls = 0
                self.rollback_calls = 0
                self.close_calls = 0
                self._injected_close_raised = False

            def execute(self, sql, *args, **kwargs):
                statement = str(sql).strip()
                upper = statement.upper()
                head = upper.split(None, 1)[0] if upper else ""
                if head in {"CREATE", "ALTER", "DROP", "ATTACH", "DETACH", "VACUUM", "REINDEX"}:
                    control.forbidden_operations.append(statement)
                if upper.startswith("PRAGMA JOURNAL_MODE"):
                    control.forbidden_operations.append(statement)
                if upper == "BEGIN IMMEDIATE":
                    control.begin_attempts += 1
                    if control.begin_attempts == control.fail_begin_at:
                        raise control.begin_failure
                return super().execute(sql, *args, **kwargs)

            def executescript(self, script):
                control.forbidden_operations.append("executescript: " + str(script))
                return super().executescript(script)

            def commit(self):
                self.commit_calls += 1
                if control.before_commit is not None:
                    control.before_commit(self)
                if control.commit_failure is not None:
                    raise control.commit_failure
                return super().commit()

            def rollback(self):
                self.rollback_calls += 1
                return super().rollback()

            def close(self):
                self.close_calls += 1
                control.close_order.append(self)
                result = super().close()
                if (self is control.close_failure_connection
                        and not self._injected_close_raised):
                    self._injected_close_raised = True
                    control.close_failure_raised = True
                    raise control.close_failure
                return result

        def controlled_connect(*args, **kwargs):
            # Keep the underlying connection native; factory only adds observation
            # and explicit faults to the real SQLite connection implementation.
            kwargs["factory"] = ControlledConnection
            connection = original_connect(*args, **kwargs)
            control.connections.append(connection)
            return connection

        with mock.patch.object(resource_module.sqlite3, "connect", controlled_connect):
            yield


class RetainedWriteReservationTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="s2b-wr-")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.api = importlib.import_module("coscreen.team_write_reservation")

    def make_db(self, name: str) -> Path:
        path = self.root / name
        with closing(sqlite3.connect(path)) as conn, conn:
            conn.execute("CREATE TABLE probe (value INTEGER NOT NULL)")
            conn.execute("INSERT INTO probe(value) VALUES (0)")
        return path

    @staticmethod
    def read_value(path: Path) -> int:
        with closing(sqlite3.connect(path)) as conn:
            return conn.execute("SELECT value FROM probe").fetchone()[0]

    @staticmethod
    def write_value(path: Path, value: int) -> None:
        with closing(sqlite3.connect(path, timeout=0)) as conn, conn:
            conn.execute("UPDATE probe SET value=?", (value,))

    @staticmethod
    def schema(path: Path):
        with closing(sqlite3.connect(path)) as conn:
            return conn.execute(
                "SELECT type,name,sql FROM sqlite_master "
                "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
            ).fetchall()

    @staticmethod
    def assert_closed(test: unittest.TestCase, connection: sqlite3.Connection) -> None:
        with test.assertRaises(sqlite3.ProgrammingError):
            connection.execute("SELECT 1")

    def test_missing_collaboration_or_reserved_input_never_creates_database(self):
        missing_collab = self.root / "absent-dir" / "collaboration.db"
        self.assertFalse(missing_collab.parent.exists())
        yielded = False
        with self.assertRaises(Exception):
            with self.api.retained_write_connections(missing_collab):
                yielded = True
        self.assertFalse(yielded, "missing mode=rw collaboration database must not yield")
        self.assertFalse(missing_collab.exists())
        self.assertFalse(missing_collab.parent.exists())

        collab = self.make_db("collaboration.db")
        missing_input = self.root / "absent-input.db"
        control = _ConnectionControl()
        reserve_attempted = False
        reserve_returned = False
        reserve_error = None
        with control.installed(self.api):
            try:
                with self.api.retained_write_connections(collab) as reserved:
                    reserve_attempted = True
                    reserved.reserve(missing_input, ())
                    reserve_returned = True
            except Exception as exc:
                if not reserve_attempted or reserve_returned:
                    raise
                reserve_error = exc
            self.assertTrue(reserve_attempted)
            self.assertFalse(reserve_returned)
            self.assertIsNotNone(reserve_error, "missing reserved input must reject reserve")
            self.assertEqual(len(control.connections), 1)
            self.assertEqual([connection.commit_calls for connection in control.connections], [0])
            self.assertEqual([connection.rollback_calls for connection in control.connections], [1])
            self.assertEqual(control.close_order, list(reversed(control.connections)))
            self.assert_closed(self, control.connections[0])
        self.assertFalse(missing_input.exists())
        self.write_value(collab, 1)
        self.assertEqual(self.read_value(collab), 1, "failed reserve releases its collaboration lock")

        owner = self.make_db("owner.db")
        missing_other = self.root / "absent-other.db"
        control_other = _ConnectionControl()
        other_attempted = False
        other_returned = False
        other_error = None
        with control_other.installed(self.api):
            try:
                with self.api.retained_write_connections(collab) as reserved:
                    other_attempted = True
                    reserved.reserve(owner, (missing_other,))
                    other_returned = True
            except Exception as exc:
                if not other_attempted or other_returned:
                    raise
                other_error = exc
            self.assertTrue(other_attempted)
            self.assertFalse(other_returned)
            self.assertIsNotNone(other_error, "a missing non-owner input must reject reserve")
            self.assertGreaterEqual(len(control_other.connections), 1)
            self.assertEqual([connection.commit_calls for connection in control_other.connections],
                             [0] * len(control_other.connections))
            self.assertEqual([connection.rollback_calls for connection in control_other.connections],
                             [1] * len(control_other.connections))
            self.assertEqual(control_other.close_order, list(reversed(control_other.connections)))
            for connection in control_other.connections:
                self.assert_closed(self, connection)
        self.assertFalse(missing_other.exists())
        self.write_value(collab, 2)
        self.write_value(owner, 2)
        self.assertEqual(self.read_value(collab), 2)
        self.assertEqual(self.read_value(owner), 2)

    def test_unregistered_connection_and_target_switch_are_rejected(self):
        collab = self.make_db("collaboration.db")
        owner = self.make_db("owner.db")
        other = self.make_db("other.db")
        unregistered = self.make_db("unregistered.db")
        unregistered_owner_alias = self.root / "unregistered-owner-hardlink.db"
        os.link(owner, unregistered_owner_alias)

        control = _ConnectionControl()
        with control.installed(self.api):
            with self.api.retained_write_connections(collab) as reserved:
                with self.assertRaises(Exception):
                    reserved.select_target(owner)
                self.assertIsNone(reserved.target_conn)
                self.assertEqual(len(control.connections), 1,
                                 "target cannot be selected before reserve opens any inputs")
                reserved.reserve(owner, (other,))
                opened_after_reserve = len(control.connections)
                with self.assertRaises(Exception):
                    reserved.reserve(owner, ())
                self.assertEqual(len(control.connections), opened_after_reserve,
                                 "a rejected second reserve must not open more connections")
                owner_conn = reserved.connection(owner)
                opened_before_unregistered_lookup = len(control.connections)
                with self.assertRaises(Exception):
                    reserved.connection(unregistered)
                self.assertEqual(len(control.connections), opened_before_unregistered_lookup,
                                 "unregistered lookup must not lazily open a database")
                reserved.select_target(owner)
                reserved.select_target(owner)  # the same registered target is idempotent
                registered_target = reserved.target_conn
                opened_before_alias_lookup = len(control.connections)
                with self.assertRaises(Exception):
                    reserved.connection(unregistered_owner_alias)
                with self.assertRaises(Exception):
                    reserved.select_target(unregistered_owner_alias)
                self.assertEqual(len(control.connections), opened_before_alias_lookup,
                                 "unregistered hard-link aliases must not open another connection")
                self.assertIs(reserved.target_conn, registered_target,
                              "rejected alias selection must preserve the original target")
                with self.assertRaises(Exception):
                    reserved.select_target(other)
                self.assertIs(reserved.target_conn, owner_conn)
                owner_conn.execute("UPDATE probe SET value=11")

            self.assertEqual([connection.commit_calls for connection in control.connections],
                             [0, 1, 0])
            self.assertEqual([connection.rollback_calls for connection in control.connections],
                             [1, 0, 1])
            self.assertEqual(control.close_order, list(reversed(control.connections)))
            for connection in control.connections:
                self.assert_closed(self, connection)

        self.assertEqual(self.read_value(owner), 11)
        self.assertEqual(self.read_value(other), 0)

    def test_owner_first_fixed_sort_row_factory_and_aliases_share_connections(self):
        collab = self.make_db("collaboration.db")
        owner = self.make_db("owner.db")
        alpha = self.make_db("alpha.db")
        beta = self.make_db("beta.db")
        zeta = self.make_db("zeta.db")
        schemas_before = {path: self.schema(path) for path in (collab, owner, alpha, beta, zeta)}
        alpha_alias = self.root / "alpha-hardlink.db"
        collab_alias = self.root / "collaboration-hardlink.db"
        actor_alias = self.root / "owner-actor-hardlink.db"
        os.link(alpha, alpha_alias)
        os.link(collab, collab_alias)
        os.link(owner, actor_alias)

        # owner also serves as the actor path; all three databases have hard-link aliases.
        actor = owner
        sources = (zeta, alpha_alias, collab_alias, actor, actor_alias, beta, alpha)
        with self.api.retained_write_connections(collab) as reserved:
            reserved.reserve(owner, sources)
            owner_conn = reserved.connection(owner)
            self.assertIs(reserved.collab_conn, reserved.connection(collab_alias))
            self.assertIs(owner_conn, reserved.connection(owner))
            self.assertIs(owner_conn, reserved.connection(actor_alias))
            self.assertIs(reserved.connection(alpha), reserved.connection(alpha_alias))
            reserved.select_target(owner)
            reserved.select_target(actor_alias)
            self.assertIs(reserved.target_conn, owner_conn)
            expected = [reserved.collab_conn, owner_conn]
            remaining = [zeta, alpha_alias, beta, alpha]
            unique = []
            for path in sorted(remaining, key=lambda item: str(item.resolve())):
                if not any(os.path.samefile(path, prior) for prior in (collab, owner, *unique)):
                    unique.append(path)
            expected.extend(reserved.connection(path) for path in unique)
            self.assertEqual(reserved.connections, tuple(expected))
            self.assertEqual(len(reserved.connections), 5)
            for attribute in ("collab_conn", "connections", "target_conn", "cleanup_errors"):
                with self.assertRaises(AttributeError):
                    setattr(reserved, attribute, None)
            for connection in reserved.connections:
                self.assertIs(connection.row_factory, sqlite3.Row)
                row = connection.execute("SELECT value FROM probe").fetchone()
                self.assertIsInstance(row, sqlite3.Row)
                self.assertEqual(row["value"], 0)
            self.assertIsInstance(reserved.cleanup_errors, tuple)

        self.assertEqual({path: self.schema(path) for path in schemas_before}, schemas_before,
                         "resource reservation must not migrate or otherwise change schemas")

    def test_target_commit_occurs_while_every_input_still_blocks_external_writes(self):
        collab = self.make_db("collaboration.db")
        owner = self.make_db("owner.db")
        input_a = self.make_db("input-a.db")
        input_b = self.make_db("input-b.db")
        control = _ConnectionControl()
        observed: list[tuple[Path, str]] = []

        def probe_all_inputs(_target_connection):
            for path in (collab, owner, input_a, input_b):
                external = control.raw_connect(path, timeout=0)
                try:
                    try:
                        external.execute("UPDATE probe SET value=value+100")
                    except sqlite3.OperationalError:
                        observed.append((path, "locked"))
                    else:
                        external.commit()
                        observed.append((path, "writable"))
                finally:
                    external.close()

        control.before_commit = probe_all_inputs
        with control.installed(self.api):
            with self.api.retained_write_connections(collab, timeout=0.2) as reserved:
                reserved.reserve(owner, (input_b, input_a))
                for connection in reserved.connections:
                    self.assertEqual(connection.execute("PRAGMA busy_timeout").fetchone()[0], 200)
                reserved.connection(collab).execute("UPDATE probe SET value=1")
                reserved.connection(owner).execute("UPDATE probe SET value=2")
                reserved.connection(input_a).execute("UPDATE probe SET value=3")
                reserved.connection(input_b).execute("UPDATE probe SET value=4")
                reserved.select_target(owner)
            self.assertEqual(reserved.target_conn.commit_calls, 1)
            self.assertEqual([connection.commit_calls for connection in control.connections],
                             [0, 1, 0, 0])
            self.assertEqual([connection.rollback_calls for connection in control.connections],
                             [1, 0, 1, 1])
            self.assertEqual(control.close_order, list(reversed(control.connections)))
            self.assertEqual(len(control.connections), 4)
            self.assertEqual(len(observed), 4)
            self.assertEqual([state for _, state in observed], ["locked"] * 4)
            self.assertEqual(control.forbidden_operations, [],
                             "reservation must not run DDL, executescript, or journal_mode PRAGMA")
            self.assertEqual(reserved.cleanup_errors, ())

        self.assertEqual(self.read_value(collab), 0)
        self.assertEqual(self.read_value(owner), 2)
        self.assertEqual(self.read_value(input_a), 0)
        self.assertEqual(self.read_value(input_b), 0)
        for connection in control.connections:
            self.assert_closed(self, connection)
        for path in (collab, input_a, input_b):
            self.write_value(path, 9)
            self.assertEqual(self.read_value(path), 9, "input locks release after target commit cleanup")

    def test_normal_exit_without_target_rejects_and_rolls_back_every_database(self):
        collab = self.make_db("collaboration.db")
        owner = self.make_db("owner.db")
        other = self.make_db("other.db")
        control = _ConnectionControl()
        body_complete = False
        exit_error = None
        with control.installed(self.api):
            try:
                with self.api.retained_write_connections(collab) as reserved:
                    reserved.reserve(owner, (other,))
                    reserved.connection(collab).execute("UPDATE probe SET value=1")
                    reserved.connection(owner).execute("UPDATE probe SET value=2")
                    reserved.connection(other).execute("UPDATE probe SET value=3")
                    for connection, value in zip(reserved.connections, (1, 2, 3)):
                        self.assertEqual(connection.execute("SELECT value FROM probe").fetchone()[0], value)
                    body_complete = True
            except Exception as exc:
                if not body_complete:
                    raise
                exit_error = exc

            self.assertTrue(body_complete, "all three real writes must finish before context exit")
            self.assertIsNotNone(exit_error, "normal exit without target must reject")
            self.assertEqual([connection.commit_calls for connection in control.connections], [0, 0, 0])
            self.assertEqual([connection.rollback_calls for connection in control.connections], [1, 1, 1])
            self.assertEqual(control.close_order, list(reversed(control.connections)))
            for connection in control.connections:
                self.assert_closed(self, connection)

        self.assertEqual([self.read_value(path) for path in (collab, owner, other)], [0, 0, 0])

    def test_writer_exception_propagates_unchanged_and_rolls_back_all_inputs(self):
        collab = self.make_db("collaboration.db")
        owner = self.make_db("owner.db")
        other = self.make_db("other.db")
        injected = _InjectedFailure("writer failed")
        control = _ConnectionControl()
        control.close_failure = _InjectedFailure("input close failed during writer exception cleanup")
        with control.installed(self.api):
            with self.assertRaises(_InjectedFailure) as caught:
                with self.api.retained_write_connections(collab) as reserved:
                    reserved.reserve(owner, (other,))
                    control.close_failure_connection = reserved.connection(other)
                    reserved.connection(collab).execute("UPDATE probe SET value=1")
                    reserved.connection(owner).execute("UPDATE probe SET value=2")
                    reserved.connection(other).execute("UPDATE probe SET value=3")
                    reserved.select_target(owner)
                    raise injected
            self.assertIs(caught.exception, injected)
            self.assertTrue(control.close_failure_raised)
        self.assertEqual([self.read_value(path) for path in (collab, owner, other)], [0, 0, 0])
        self.assertEqual([connection.commit_calls for connection in control.connections], [0, 0, 0])
        self.assertEqual([connection.rollback_calls for connection in control.connections], [1, 1, 1])
        for connection in control.connections:
            self.assert_closed(self, connection)

    def test_commit_failure_propagates_once_and_cleanup_failure_does_not_mask_it(self):
        collab = self.make_db("collaboration.db")
        owner = self.make_db("owner.db")
        other = self.make_db("other.db")
        commit_error = _InjectedFailure("target commit failed")
        control = _ConnectionControl()
        control.commit_failure = commit_error
        control.close_failure = _InjectedFailure("close failed during failed commit cleanup")
        with control.installed(self.api):
            with self.assertRaises(_InjectedFailure) as caught:
                with self.api.retained_write_connections(collab) as reserved:
                    reserved.reserve(owner, (other,))
                    control.close_failure_connection = reserved.connection(owner)
                    reserved.connection(owner).execute("UPDATE probe SET value=8")
                    reserved.connection(other).execute("UPDATE probe SET value=9")
                    reserved.select_target(owner)
            self.assertIs(caught.exception, commit_error)
            self.assertEqual(reserved.target_conn.commit_calls, 1, "failed commit is never retried")
            self.assertEqual([connection.rollback_calls for connection in control.connections],
                             [1, 1, 1])
            self.assertTrue(control.close_failure_raised,
                            "the injected cleanup failure must occur after failed commit")
        self.assertEqual(self.read_value(owner), 0)
        self.assertEqual(self.read_value(other), 0)
        self.write_value(collab, 1)
        self.write_value(owner, 1)
        self.write_value(other, 1)

    def test_begin_failure_during_partial_reserve_closes_every_opened_connection(self):
        collab = self.make_db("collaboration.db")
        owner = self.make_db("owner.db")
        input_a = self.make_db("input-a.db")
        input_b = self.make_db("input-b.db")
        control = _ConnectionControl()
        control.fail_begin_at = 3  # collab and owner opened; first sorted input begins and fails
        failure = _InjectedFailure("BEGIN IMMEDIATE failed")
        control.begin_failure = failure

        with control.installed(self.api):
            with self.assertRaises(_InjectedFailure) as caught:
                with self.api.retained_write_connections(collab) as reserved:
                    reserved.reserve(owner, (input_b, input_a))
            self.assertIs(caught.exception, failure)

        self.assertEqual(len(control.connections), 3)
        self.assertEqual(control.close_order, list(reversed(control.connections)))
        for connection in control.connections:
            self.assertGreaterEqual(connection.close_calls, 1)
            self.assert_closed(self, connection)
        for path in (collab, owner, input_a, input_b):
            self.write_value(path, 5)
            self.assertEqual(self.read_value(path), 5, "partial reserve failure releases every lock")

    def test_successful_target_commit_survives_input_cleanup_error(self):
        collab = self.make_db("collaboration.db")
        owner = self.make_db("owner.db")
        other = self.make_db("other.db")
        control = _ConnectionControl()
        control.close_failure = _InjectedFailure("non-target close failed")

        with control.installed(self.api):
            with self.api.retained_write_connections(collab) as reserved:
                reserved.reserve(owner, (other,))
                control.close_failure_connection = reserved.connection(other)
                reserved.connection(owner).execute("UPDATE probe SET value=17")
                reserved.connection(other).execute("UPDATE probe SET value=23")
                reserved.select_target(owner)
            self.assertEqual(reserved.target_conn.commit_calls, 1)
            self.assertTrue(reserved.cleanup_errors)
            self.assertTrue(all(isinstance(item, str) and item for item in reserved.cleanup_errors))
            self.assertTrue(control.close_failure_raised)
            self.assertEqual([connection.close_calls for connection in control.connections],
                             [1, 1, 1], "cleanup continues after the injected close failure")
            self.assertEqual(control.close_order, list(reversed(control.connections)))

        self.assertEqual(self.read_value(owner), 17, "post-commit close failure cannot report rollback")
        self.assertEqual(self.read_value(other), 0)
        for path in (collab, owner, other):
            self.write_value(path, 6)
            self.assertEqual(self.read_value(path), 6)

    def test_reserved_connections_enable_foreign_keys_and_enforce_integrity(self):
        collab = self.make_db("fk-collaboration.db")
        owner = self.make_db("fk-owner.db")
        other = self.make_db("fk-other.db")
        for path in (collab, owner, other):
            with closing(sqlite3.connect(path)) as connection, connection:
                connection.execute("CREATE TABLE fk_parent (id INTEGER PRIMARY KEY)")
                connection.execute(
                    "CREATE TABLE fk_child (id INTEGER PRIMARY KEY, "
                    "parent_id INTEGER NOT NULL REFERENCES fk_parent(id))")
                connection.execute("INSERT INTO fk_parent(id) VALUES (1)")

        failed = _ConnectionControl()
        with failed.installed(self.api):
            with self.assertRaises(sqlite3.IntegrityError):
                with self.api.retained_write_connections(collab) as reserved:
                    reserved.reserve(owner, (other,))
                    for connection in reserved.connections:
                        self.assertEqual(
                            connection.execute("PRAGMA foreign_keys").fetchone()[0], 1,
                            "every reserved SQLite connection must enforce foreign keys")
                    reserved.select_target(owner)
                    reserved.connection(owner).execute(
                        "INSERT INTO fk_child(id,parent_id) VALUES(1,999)")
            self.assertEqual([connection.commit_calls for connection in failed.connections],
                             [0, 0, 0])
            self.assertEqual([connection.rollback_calls for connection in failed.connections],
                             [1, 1, 1])
            self.assertEqual(failed.close_order, list(reversed(failed.connections)))

        with closing(sqlite3.connect(owner)) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM fk_child").fetchone()[0], 0,
                             "invalid reference and all caller work must roll back without an orphan")

        committed = _ConnectionControl()
        with committed.installed(self.api):
            with self.api.retained_write_connections(collab) as reserved:
                reserved.reserve(owner, (other,))
                for connection in reserved.connections:
                    self.assertEqual(connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)
                reserved.connection(owner).execute(
                    "INSERT INTO fk_child(id,parent_id) VALUES(2,1)")
                reserved.select_target(owner)
            self.assertEqual([connection.commit_calls for connection in committed.connections],
                             [0, 1, 0])
            self.assertEqual([connection.rollback_calls for connection in committed.connections],
                             [1, 0, 1])
            self.assertEqual(committed.close_order, list(reversed(committed.connections)))

        with closing(sqlite3.connect(owner)) as connection:
            self.assertEqual(connection.execute(
                "SELECT parent_id FROM fk_child WHERE id=2").fetchone()[0], 1,
                "a valid FK child persists through the sole target commit")

    def test_reserve_begin_failure_records_exact_failed_path_and_preserves_exception(self):
        for failed_input in ("owner", "other"):
            with self.subTest(failed_input=failed_input):
                collab = self.make_db(f"failed-path-{failed_input}-collab.db")
                owner = self.make_db(f"failed-path-{failed_input}-owner.db")
                other = self.make_db(f"failed-path-{failed_input}-other.db")
                expected_path = owner if failed_input == "owner" else other
                control = _ConnectionControl()
                control.fail_begin_at = 2 if failed_input == "owner" else 3
                injected = _InjectedFailure(f"BEGIN failed for {failed_input}")
                control.begin_failure = injected
                reserved = None

                with control.installed(self.api):
                    with self.assertRaises(_InjectedFailure) as caught:
                        with self.api.retained_write_connections(collab) as reserved:
                            reserved.reserve(owner, (other,))
                    self.assertIs(caught.exception, injected,
                                  "failed_path attribution must not wrap the original exception")
                    self.assertIsNotNone(reserved)
                    self.assertIsInstance(reserved.failed_path, Path)
                    self.assertEqual(reserved.failed_path, expected_path)
                    self.assertEqual([connection.commit_calls for connection in control.connections],
                                     [0] * len(control.connections))
                    self.assertEqual(control.close_order, list(reversed(control.connections)))
                    for connection in control.connections:
                        self.assertGreaterEqual(connection.close_calls, 1)
                        self.assert_closed(self, connection)

                for path in (collab, owner, other):
                    self.write_value(path, 12)
                    self.assertEqual(self.read_value(path), 12,
                                     "failed owner/input acquisition must release every acquired lock")


if __name__ == "__main__":  # pragma: no cover - unittest entry point
    unittest.main()
