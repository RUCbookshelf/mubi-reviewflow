"""Contract tests for the F2 team terminal registry storage API.

These tests intentionally exercise only the public API and real, temporary SQLite
files. They do not infer implementation details from the registry module.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from coscreen import team_terminal_registry as registry


MAX_SQLITE_INT = (1 << 63) - 1
OWNER_LIBRARY = "a" * 32
SOURCE_LIBRARY = "b" * 32


class CleanupPropagationCursor(sqlite3.Cursor):
    def fetchall(self):
        rows = super().fetchall()
        if getattr(self, "_f2_target_source_insert", False):
            connection = self.connection
            connection.f2_insert_fetchall_count += 1
            connection.f2_insert_result_rows = rows
            connection.f2_rowcount_after_fetch = self.rowcount
            if connection.f2_fetch_exception is not None:
                error = connection.f2_fetch_exception
                connection.f2_fetch_exception = None
                connection.f2_fetch_exception_injected = True
                raise error
        return rows

    def close(self):
        target = getattr(self, "_f2_target_source_insert", False)
        connection = self.connection if target else None
        super().close()
        if target:
            connection.f2_real_cursor_close_count += 1
            if connection.f2_close_exception is not None:
                error = connection.f2_close_exception
                connection.f2_close_exception = None
                connection.f2_close_exception_injected = True
                raise error


class CleanupPropagationConnection(sqlite3.Connection):
    def execute(self, sql, parameters=(), /):
        normalized = sql.lstrip().upper()
        if normalized.startswith("INSERT") and "TEAM_SOURCE_REGISTRY" in normalized:
            self.f2_target_source_insert_count += 1
            cursor = super().cursor(factory=CleanupPropagationCursor)
            cursor._f2_target_source_insert = True
            return cursor.execute(sql, parameters)
        if (
            self.f2_insert_fetchall_count
            and normalized.startswith("SELECT")
            and "TEAM_SOURCE_REGISTRY" in normalized
        ):
            self.f2_recovery_source_select_count += 1
        return super().execute(sql, parameters)


class FixtureFactory:
    """Small file-backed fixtures; cleanup is registered before setup can fail."""

    @staticmethod
    def make_ready_database(test_case: unittest.TestCase, *, prepared: bool = True) -> Path:
        scratch = tempfile.TemporaryDirectory(prefix="f2-terminal-registry-")
        test_case.addCleanup(scratch.cleanup)
        path = Path(scratch.name) / "registry.sqlite3"
        connection = sqlite3.connect(path)
        connection.close()
        if prepared:
            registry.prepare_team_terminal_registry(path)
        return path

    @staticmethod
    def borrowed_connection(
        test_case: unittest.TestCase, path: Path, *, active: bool = True
    ) -> sqlite3.Connection:
        connection = sqlite3.connect(path, cached_statements=0)
        test_case.addCleanup(connection.close)
        connection.row_factory = sqlite3.Row
        if active:
            connection.execute("BEGIN")
        return connection


class RegistryAssertions:
    """Independent public-contract oracles and persisted-state snapshots."""

    @staticmethod
    def canonical_evidence_json(document: dict) -> str:
        return json.dumps(
            document,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )

    @staticmethod
    def expected_evidence_sha256(document: dict) -> str:
        canonical = RegistryAssertions.canonical_evidence_json(document)
        return hashlib.sha256(canonical.encode("utf-8", errors="strict")).hexdigest()

    @staticmethod
    def assert_registry_error(
        test_case: unittest.TestCase,
        operation,
        expected_type,
        *,
        reason: str | None = None,
    ) -> BaseException:
        with test_case.assertRaises(expected_type) as caught:
            operation()
        error = caught.exception
        if reason is not None:
            test_case.assertEqual(getattr(error, "reason", None), reason)
        return error

    @staticmethod
    def exception_diagnostics(root: BaseException) -> tuple[str, ...]:
        messages: list[str] = []
        pending: list[object] = [root]
        seen_exceptions: set[int] = set()
        seen_containers: set[int] = set()
        while pending:
            value = pending.pop()
            if isinstance(value, BaseException):
                identity = id(value)
                if identity in seen_exceptions:
                    continue
                seen_exceptions.add(identity)
                messages.append(str(value))
                pending.extend(getattr(value, "__notes__", ()))
                pending.extend(
                    linked for linked in
                    (getattr(value, "__cause__", None), getattr(value, "__context__", None))
                    if linked is not None
                )
                pending.extend(getattr(value, "exceptions", ()))
                pending.extend(getattr(value, "args", ()))
                try:
                    pending.extend(vars(value).values())
                except TypeError:
                    pass
            elif isinstance(value, str):
                messages.append(value)
            elif isinstance(value, dict):
                identity = id(value)
                if identity not in seen_containers:
                    seen_containers.add(identity)
                    pending.extend(value.values())
            elif isinstance(value, (tuple, list, set, frozenset)):
                identity = id(value)
                if identity not in seen_containers:
                    seen_containers.add(identity)
                    pending.extend(value)
        return tuple(messages)

    @staticmethod
    def capture_rows(connection: sqlite3.Connection, sql: str, parameters=()) -> list[tuple]:
        return [tuple(row) for row in connection.execute(sql, parameters).fetchall()]


class TestPrepareReadiness(unittest.TestCase):
    def test_missing_database_is_not_created(self):
        scratch = tempfile.TemporaryDirectory(prefix="f2-terminal-missing-")
        self.addCleanup(scratch.cleanup)
        missing = Path(scratch.name) / "absent.sqlite3"
        with self.assertRaises(FileNotFoundError):
            registry.prepare_team_terminal_registry(missing)
        self.assertFalse(missing.exists())

    def test_file_disappears_between_check_and_open_does_not_get_created(self):
        scratch = tempfile.TemporaryDirectory(prefix="f2-terminal-toctou-")
        self.addCleanup(scratch.cleanup)
        disappearing = Path(scratch.name) / "disappearing.sqlite3"
        realconnect = sqlite3.connect
        seed = realconnect(disappearing)
        self.addCleanup(seed.close)
        seed.close()
        self.assertTrue(disappearing.is_file())

        def unlink_before_real_open(*args, **kwargs):
            self.assertTrue(disappearing.is_file())
            disappearing.unlink()
            return realconnect(*args, **kwargs)

        with mock.patch.object(sqlite3, "connect", side_effect=unlink_before_real_open):
            error = RegistryAssertions.assert_registry_error(
                self, lambda: registry.prepare_team_terminal_registry(disappearing),
                registry.TerminalRegistryUnavailable, reason="unreadable",
            )
        self.assertIsInstance(getattr(error, "__cause__", None), sqlite3.Error)
        self.assertFalse(disappearing.exists())

    def test_check_constraint_literal_qualification_mismatch_is_not_repaired(self):
        normal_path = FixtureFactory.make_ready_database(self)
        normal = sqlite3.connect(normal_path)
        self.addCleanup(normal.close)
        captured = RegistryAssertions.capture_rows(
            normal,
            "SELECT type, name, sql FROM main.sqlite_master "
            "WHERE sql IS NOT NULL AND type IN ('table','index','trigger') "
            "ORDER BY CASE type WHEN 'table' THEN 0 WHEN 'index' THEN 1 "
            "WHEN 'trigger' THEN 2 ELSE 3 END, name",
        )
        normal.close()
        synthetic = FixtureFactory.make_ready_database(self, prepared=False)
        setup = sqlite3.connect(synthetic)
        self.addCleanup(setup.close)
        changed_protocol_check = False
        for object_type, name, ddl in captured:
            if object_type == "table" and name == "team_task_protocol":
                ddl, replacements = re.subn(
                    r"(\bIN\s*\()\s*'new_covered'",
                    r"\1'main.new_covered'",
                    ddl,
                    count=1,
                    flags=re.IGNORECASE,
                )
                self.assertEqual(replacements, 1)
                changed_protocol_check = True
            setup.execute(ddl)
        self.assertTrue(changed_protocol_check)
        setup.commit()
        before = RegistryAssertions.capture_rows(
            setup,
            "SELECT type, name, sql FROM main.sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name",
        )
        setup.close()
        RegistryAssertions.assert_registry_error(
            self, lambda: registry.prepare_team_terminal_registry(synthetic),
            registry.TerminalRegistryUnavailable, reason="schema_mismatch",
        )
        observer = sqlite3.connect(synthetic)
        self.addCleanup(observer.close)
        after = RegistryAssertions.capture_rows(
            observer,
            "SELECT type, name, sql FROM main.sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name",
        )
        self.assertEqual(after, before)

    def test_fresh_database_has_exact_object_layout(self):
        path = FixtureFactory.make_ready_database(self, prepared=False)
        registry.prepare_team_terminal_registry(path)
        connection = sqlite3.connect(path)
        self.addCleanup(connection.close)
        objects = RegistryAssertions.capture_rows(
            connection,
            "SELECT type, name FROM main.sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' AND type IN ('table','trigger','index') "
            "ORDER BY type, name",
        )
        self.assertEqual(
            objects,
            [
                ("index", "team_source_registry_one_active"),
                ("table", "team_source_registry"),
                ("table", "team_task_protocol"),
                ("trigger", "team_source_registry_guard_update"),
                ("trigger", "team_source_registry_no_delete"),
                ("trigger", "team_task_protocol_guard_update"),
                ("trigger", "team_task_protocol_no_delete"),
            ],
        )
        protocol_columns = RegistryAssertions.capture_rows(
            connection, "PRAGMA main.table_info('team_task_protocol')"
        )
        source_columns = RegistryAssertions.capture_rows(
            connection, "PRAGMA main.table_info('team_source_registry')"
        )
        self.assertEqual(
            [row[1] for row in protocol_columns],
            [
                "task_instance_id", "task_id", "owner_user_id", "owner_library_id",
                "protocol_version", "coverage_state", "roster_revision", "adopted_at",
                "adoption_evidence_ref", "adoption_evidence_json",
                "adoption_evidence_sha256", "created_at", "updated_at",
            ],
        )
        self.assertEqual(
            [row[1] for row in source_columns],
            [
                "registration_id", "task_instance_id", "source_user_id",
                "source_library_id", "source_path_identity", "generation", "status",
                "registered_at", "retired_at", "retired_reason", "archive_watermark",
            ],
        )
        self.assertEqual(protocol_columns[0][3], 1)  # TEXT primary key is explicitly NOT NULL.
        self.assertEqual(source_columns[0][3], 1)

    def test_exact_prepared_layout_is_idempotent(self):
        path = FixtureFactory.make_ready_database(self)
        connection = sqlite3.connect(path)
        self.addCleanup(connection.close)
        before = RegistryAssertions.capture_rows(
            connection,
            "SELECT type, name, sql FROM main.sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name",
        )
        connection.close()
        registry.prepare_team_terminal_registry(path)
        connection = sqlite3.connect(path)
        self.addCleanup(connection.close)
        after = RegistryAssertions.capture_rows(
            connection,
            "SELECT type, name, sql FROM main.sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name",
        )
        self.assertEqual(after, before)

    def test_partial_layout_is_rejected_without_repair(self):
        path = FixtureFactory.make_ready_database(self, prepared=False)
        connection = sqlite3.connect(path)
        self.addCleanup(connection.close)
        connection.execute("CREATE TABLE team_task_protocol (wrong TEXT)")
        connection.commit()
        before = RegistryAssertions.capture_rows(
            connection,
            "SELECT type, name, sql FROM main.sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name",
        )
        connection.close()
        error = RegistryAssertions.assert_registry_error(
            self, lambda: registry.prepare_team_terminal_registry(path),
            registry.TerminalRegistryUnavailable, reason="schema_mismatch",
        )
        self.assertIsNone(getattr(error, "__cause__", None))
        connection = sqlite3.connect(path)
        self.addCleanup(connection.close)
        after = RegistryAssertions.capture_rows(
            connection,
            "SELECT type, name, sql FROM main.sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name",
        )
        self.assertEqual(after, before)

        # ASCII case variants of a reserved main object are still reserved ownership.
        upper_path = FixtureFactory.make_ready_database(self, prepared=False)
        upper = sqlite3.connect(upper_path)
        self.addCleanup(upper.close)
        upper.execute('CREATE TABLE "TEAM_TASK_PROTOCOL" (wrong TEXT)')
        upper.commit()
        before_upper = RegistryAssertions.capture_rows(
            upper, "SELECT type, name, sql FROM main.sqlite_master ORDER BY type, name"
        )
        upper.close()
        RegistryAssertions.assert_registry_error(
            self, lambda: registry.prepare_team_terminal_registry(upper_path),
            registry.TerminalRegistryUnavailable, reason="schema_mismatch",
        )
        upper = sqlite3.connect(upper_path)
        self.addCleanup(upper.close)
        self.assertEqual(
            RegistryAssertions.capture_rows(
                upper, "SELECT type, name, sql FROM main.sqlite_master ORDER BY type, name"
            ),
            before_upper,
        )

        # A reserved table name owned by the wrong object type is also a mismatch.
        owned = FixtureFactory.make_ready_database(self, prepared=False)
        owner = sqlite3.connect(owned)
        self.addCleanup(owner.close)
        owner.execute("CREATE VIEW team_task_protocol AS SELECT 1 AS wrong_owner")
        owner.commit()
        before_owner = RegistryAssertions.capture_rows(
            owner, "SELECT type, name, sql FROM main.sqlite_master ORDER BY type, name"
        )
        owner.close()
        RegistryAssertions.assert_registry_error(
            self, lambda: registry.prepare_team_terminal_registry(owned),
            registry.TerminalRegistryUnavailable, reason="schema_mismatch",
        )
        owner = sqlite3.connect(owned)
        self.addCleanup(owner.close)
        self.assertEqual(
            RegistryAssertions.capture_rows(
                owner, "SELECT type, name, sql FROM main.sqlite_master ORDER BY type, name"
            ),
            before_owner,
        )

    def test_extra_trigger_or_index_is_rejected(self):
        for object_sql in (
            "CREATE TRIGGER extra_ignore BEFORE INSERT ON team_source_registry "
            "BEGIN SELECT RAISE(IGNORE); END",
            "CREATE INDEX extra_library_lookup ON team_source_registry(source_library_id)",
        ):
            with self.subTest(object_sql=object_sql):
                path = FixtureFactory.make_ready_database(self)
                connection = sqlite3.connect(path)
                self.addCleanup(connection.close)
                connection.execute(object_sql)
                connection.commit()
                before = RegistryAssertions.capture_rows(
                    connection,
                    "SELECT type, name, sql FROM main.sqlite_master "
                    "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name",
                )
                connection.close()
                RegistryAssertions.assert_registry_error(
                    self, lambda: registry.prepare_team_terminal_registry(path),
                    registry.TerminalRegistryUnavailable, reason="schema_mismatch",
                )
                connection = sqlite3.connect(path)
                self.addCleanup(connection.close)
                self.assertEqual(
                    RegistryAssertions.capture_rows(
                        connection,
                        "SELECT type, name, sql FROM main.sqlite_master "
                        "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name",
                    ),
                    before,
                )


class TestBorrowedConnection(unittest.TestCase):
    def test_missing_layout_is_unavailable_not_unknown(self):
        path = FixtureFactory.make_ready_database(self, prepared=False)
        connection = FixtureFactory.borrowed_connection(self, path)
        RegistryAssertions.assert_registry_error(
            self,
            lambda: registry.read_protocol_for_task_on(connection, task_id="absent"),
            registry.TerminalRegistryUnavailable,
            reason="not_prepared",
        )
        # A prepared database with no row is unknown, not unavailable.
        ready = FixtureFactory.make_ready_database(self)
        known_layout = FixtureFactory.borrowed_connection(self, ready)
        self.assertIsNone(
            registry.read_protocol_for_task_on(known_layout, task_id="absent")
        )
        self.assertEqual(
            registry.coverage_status_on(known_layout, task_instance_id="absent-instance"),
            "unknown",
        )

    def test_extra_schema_objects_fail_readiness(self):
        cases = (
            ("main-index", "CREATE INDEX extra_uid ON team_task_protocol(owner_user_id)"),
            (
                "temp-trigger-same-name",
                "CREATE TEMP TRIGGER team_task_protocol_no_delete BEFORE INSERT "
                "ON main.team_task_protocol BEGIN SELECT 1; END",
            ),
            (
                "temp-trigger-extra-name",
                "CREATE TEMP TRIGGER temp_unrelated BEFORE INSERT "
                "ON main.team_task_protocol BEGIN SELECT 1; END",
            ),
            (
                "temp-trigger-uppercase-reserved-name",
                'CREATE TEMP TRIGGER "TEAM_TASK_PROTOCOL_NO_DELETE" BEFORE INSERT '
                "ON main.team_task_protocol BEGIN SELECT 1; END",
            ),
        )
        for label, sql in cases:
            with self.subTest(label=label):
                path = FixtureFactory.make_ready_database(self)
                connection = FixtureFactory.borrowed_connection(self, path)
                connection.execute(sql)
                RegistryAssertions.assert_registry_error(
                    self,
                    lambda: registry.coverage_status_on(
                        connection, task_instance_id="unused"
                    ),
                    registry.TerminalRegistryUnavailable,
                    reason="schema_mismatch",
                )

        # A TEMP table shadowing a reserved main name is rejected by the readiness gate.
        shadow_path = FixtureFactory.make_ready_database(self)
        shadow = FixtureFactory.borrowed_connection(self, shadow_path)
        registry.register_protocol_on(
            shadow, task_id="task-temp-shadow", task_instance_id="instance-temp-shadow",
            owner_user_id=1, owner_library_id=OWNER_LIBRARY, creation_path="trusted_new_task",
        )
        shadow.execute("CREATE TEMP TABLE team_task_protocol (wrong TEXT)")
        RegistryAssertions.assert_registry_error(
            self,
            lambda: registry.coverage_status_on(
                shadow, task_instance_id="instance-temp-shadow"
            ),
            registry.TerminalRegistryUnavailable,
            reason="schema_mismatch",
        )

        # An unrelated TEMP table is legal and the main protocol row remains readable.
        path = FixtureFactory.make_ready_database(self)
        connection = FixtureFactory.borrowed_connection(self, path)
        registry.register_protocol_on(
            connection, task_id="task-temp-positive", task_instance_id="instance-temp-positive",
            owner_user_id=1, owner_library_id=OWNER_LIBRARY, creation_path="trusted_new_task",
        )
        connection.execute("CREATE TEMP TABLE unrelated_temp (x TEXT)")
        self.assertEqual(
            registry.coverage_status_on(connection, task_instance_id="instance-temp-positive"),
            "new_covered",
        )

    def test_requires_native_active_row_connection(self):
        path = FixtureFactory.make_ready_database(self)
        inactive = FixtureFactory.borrowed_connection(self, path, active=False)
        RegistryAssertions.assert_registry_error(
            self,
            lambda: registry.coverage_status_on(inactive, task_instance_id="i"),
            registry.TerminalRegistryUnavailable,
            reason="connection_contract",
        )
        path2 = FixtureFactory.make_ready_database(self)
        wrong_factory = FixtureFactory.borrowed_connection(self, path2)
        wrong_factory.row_factory = None
        RegistryAssertions.assert_registry_error(
            self,
            lambda: registry.coverage_status_on(wrong_factory, task_instance_id="i"),
            registry.TerminalRegistryUnavailable,
            reason="connection_contract",
        )
        path3 = FixtureFactory.make_ready_database(self)
        non_native = FixtureFactory.borrowed_connection(self, path3)
        self.assertTrue(non_native.in_transaction)
        self.assertIs(non_native.row_factory, sqlite3.Row)
        proxy = mock.Mock(spec=sqlite3.Connection, wraps=non_native)
        proxy.in_transaction = True
        proxy.row_factory = sqlite3.Row
        self.assertTrue(isinstance(proxy, sqlite3.Connection))
        self.assertFalse(issubclass(type(proxy), sqlite3.Connection))

        traced_statements = []
        non_native.set_trace_callback(traced_statements.append)
        try:
            RegistryAssertions.assert_registry_error(
                self,
                lambda: registry.coverage_status_on(proxy, task_instance_id="i"),
                registry.TerminalRegistryUnavailable,
                reason="connection_contract",
            )
        finally:
            non_native.set_trace_callback(None)
        self.assertEqual(traced_statements, [])
        # The same active native connection with sqlite3.Row remains a valid caller connection.
        self.assertEqual(
            registry.coverage_status_on(non_native, task_instance_id="i"),
            "unknown",
        )

    def test_borrowed_api_preserves_connection_ownership(self):
        path = FixtureFactory.make_ready_database(self)
        connection = FixtureFactory.borrowed_connection(self, path)
        original_factory = connection.row_factory
        registry.read_protocol_for_task_on(connection, task_id="not-yet")
        registry.register_protocol_on(
            connection, task_id="task-owned", task_instance_id="instance-owned",
            owner_user_id=1, owner_library_id=OWNER_LIBRARY, creation_path="trusted_new_task",
        )
        self.assertTrue(connection.in_transaction)
        self.assertIs(connection.row_factory, original_factory)
        self.assertIsNotNone(connection.execute("SELECT 1").fetchone())
        connection.rollback()
        observer = sqlite3.connect(path)
        self.addCleanup(observer.close)
        self.assertEqual(
            observer.execute(
                "SELECT count(*) FROM team_task_protocol WHERE task_instance_id=?",
                ("instance-owned",),
            ).fetchone()[0],
            0,
        )

    def test_dml_result_rows_completed_before_mutation_count(self):
        path = FixtureFactory.make_ready_database(self)
        connection = FixtureFactory.borrowed_connection(self, path, active=False)
        # Caller configures count_changes before opening the borrowed transaction.
        connection.execute("PRAGMA count_changes=ON")
        self.assertEqual(connection.execute("PRAGMA count_changes").fetchone()[0], 1)
        connection.execute("BEGIN")
        statements = []
        connection.set_trace_callback(statements.append)

        before = connection.total_changes
        protocol = registry.register_protocol_on(
            connection, task_id="count-changes", task_instance_id="count-changes-instance",
            owner_user_id=1, owner_library_id=OWNER_LIBRARY, creation_path="legacy_adoption",
        )
        self.assertEqual(connection.total_changes - before, 1)
        self.assertEqual(
            registry.read_protocol_on(connection, task_instance_id="count-changes-instance"),
            protocol,
        )

        before = connection.total_changes
        revised = registry.set_roster_revision_on(
            connection, task_instance_id="count-changes-instance", expected_revision=0,
            new_revision=1,
        )
        self.assertEqual(connection.total_changes - before, 1)
        self.assertEqual(
            registry.read_protocol_on(connection, task_instance_id="count-changes-instance"),
            revised,
        )

        before = connection.total_changes
        source = registry.register_source_on(
            connection, task_instance_id="count-changes-instance", source_user_id=31,
            source_library_id=SOURCE_LIBRARY, source_path_identity="count-changes-source",
            registration_id="count-changes-registration",
        )
        self.assertEqual(connection.total_changes - before, 1)
        self.assertEqual(
            registry.read_source_on(connection, registration_id="count-changes-registration"),
            source,
        )

        before = connection.total_changes
        retired = registry.retire_source_on(
            connection, registration_id="count-changes-registration",
            retired_reason="count changes retirement",
        )
        self.assertEqual(connection.total_changes - before, 1)
        self.assertEqual(
            registry.read_source_on(connection, registration_id="count-changes-registration"),
            retired,
        )

        evidence = {
            "sources": [],
            "verification_scope": "no active source after retirement",
            "verification_method": "reviewed roster boundary",
            "conflict_handling": "none",
            "prior_history_unknown": True,
        }
        before = connection.total_changes
        verified = registry.mark_legacy_verified_on(
            connection, task_instance_id="count-changes-instance",
            expected_roster_revision=1, adoption_evidence_ref="count-changes-evidence",
            adoption_evidence=evidence,
        )
        self.assertEqual(connection.total_changes - before, 1)
        self.assertEqual(
            registry.read_protocol_on(connection, task_instance_id="count-changes-instance"),
            verified,
        )

        connection.set_trace_callback(None)
        self.assertFalse(
            any(statement.lstrip().upper().startswith("PRAGMA") for statement in statements)
        )
        self.assertTrue(connection.in_transaction)
        self.assertEqual(connection.execute("PRAGMA count_changes").fetchone()[0], 1)
        connection.rollback()
        observer = sqlite3.connect(path)
        self.addCleanup(observer.close)
        self.assertEqual(
            observer.execute("SELECT count(*) FROM team_task_protocol").fetchone()[0], 0
        )
        self.assertEqual(
            observer.execute("SELECT count(*) FROM team_source_registry").fetchone()[0], 0
        )

    def test_closed_connection_preserves_programming_error(self):
        path = FixtureFactory.make_ready_database(self)
        connection = FixtureFactory.borrowed_connection(self, path)
        connection.close()
        with self.assertRaises(sqlite3.ProgrammingError):
            registry.coverage_status_on(connection, task_instance_id="closed")


class TestProtocolIdentity(unittest.TestCase):
    def test_task_id_follows_canonical_host_rule(self):
        path = FixtureFactory.make_ready_database(self)
        connection = FixtureFactory.borrowed_connection(self, path)
        row = registry.register_protocol_on(
            connection, task_id="  task-canonical  ", task_instance_id="instance-canonical",
            owner_user_id=1, owner_library_id=OWNER_LIBRARY, creation_path="trusted_new_task",
        )
        self.assertEqual(row["task_id"], "task-canonical")
        for invalid in ("", " " * 201, "a" * 201, "a/b", "a\\b", "a\x00b"):
            with self.subTest(task_id=repr(invalid)):
                RegistryAssertions.assert_registry_error(
                    self,
                    lambda invalid=invalid: registry.register_protocol_on(
                        connection, task_id=invalid, task_instance_id="bad-" + str(len(invalid)),
                        owner_user_id=1, owner_library_id=OWNER_LIBRARY,
                        creation_path="trusted_new_task",
                    ),
                    registry.TerminalRegistryPayloadInvalid,
                )

    def test_instance_and_paths_are_opaque_utf8_text(self):
        path = FixtureFactory.make_ready_database(self)
        connection = FixtureFactory.borrowed_connection(self, path)
        instance = "实例/" + ("é" * 5000)
        registry.register_protocol_on(
            connection, task_id="opaque", task_instance_id=instance,
            owner_user_id=1, owner_library_id=OWNER_LIBRARY, creation_path="legacy_adoption",
        )
        row = registry.register_source_on(
            connection, task_instance_id=instance, source_user_id=1,
            source_library_id=SOURCE_LIBRARY, source_path_identity="../not/resolved/" + ("λ" * 5000),
            registration_id="reg-" + ("x" * 5000),
        )
        self.assertEqual(row["source_path_identity"], "../not/resolved/" + ("λ" * 5000))
        self.assertEqual(row["generation"], 1)
        for invalid in ("", "bad\x00text", "bad\ud800text"):
            with self.subTest(value=repr(invalid)):
                RegistryAssertions.assert_registry_error(
                    self,
                    lambda invalid=invalid: registry.register_protocol_on(
                        connection, task_id="invalid-opaque", task_instance_id=invalid,
                        owner_user_id=1, owner_library_id=OWNER_LIBRARY,
                        creation_path="trusted_new_task",
                    ),
                    registry.TerminalRegistryPayloadInvalid,
                )

    def test_task_and_instance_selectors_are_independent(self):
        path = FixtureFactory.make_ready_database(self)
        connection = FixtureFactory.borrowed_connection(self, path)
        expected = registry.register_protocol_on(
            connection, task_id="task-select", task_instance_id="instance-select",
            owner_user_id=1, owner_library_id=OWNER_LIBRARY, creation_path="trusted_new_task",
        )
        self.assertEqual(
            registry.read_protocol_for_task_on(connection, task_id="task-select")["task_instance_id"],
            "instance-select",
        )
        self.assertIsNone(registry.read_protocol_for_task_on(connection, task_id="instance-select"))
        self.assertEqual(
            registry.read_protocol_on(connection, task_instance_id="instance-select")["task_id"],
            "task-select",
        )
        self.assertIsNone(registry.read_protocol_on(connection, task_instance_id="task-select"))
        self.assertEqual(expected["task_id"], "task-select")

    def test_binding_collisions_conflict(self):
        path = FixtureFactory.make_ready_database(self)
        connection = FixtureFactory.borrowed_connection(self, path)
        first = registry.register_protocol_on(
            connection, task_id="task-binding", task_instance_id="instance-binding",
            owner_user_id=7, owner_library_id=OWNER_LIBRARY, creation_path="legacy_adoption",
        )
        same = registry.register_protocol_on(
            connection, task_id="task-binding", task_instance_id="instance-binding",
            owner_user_id=7, owner_library_id=OWNER_LIBRARY, creation_path="legacy_adoption",
        )
        self.assertEqual(same, first)
        attempts = (
            dict(task_id="different-task", task_instance_id="instance-binding", owner_user_id=7,
                 owner_library_id=OWNER_LIBRARY, creation_path="legacy_adoption"),
            dict(task_id="task-binding", task_instance_id="instance-binding", owner_user_id=8,
                 owner_library_id=OWNER_LIBRARY, creation_path="legacy_adoption"),
            dict(task_id="task-binding", task_instance_id="instance-binding", owner_user_id=7,
                 owner_library_id="c" * 32, creation_path="legacy_adoption"),
            dict(task_id="task-binding", task_instance_id="another-instance", owner_user_id=7,
                 owner_library_id=OWNER_LIBRARY, creation_path="legacy_adoption"),
        )
        for kwargs in attempts:
            with self.subTest(kwargs=kwargs):
                RegistryAssertions.assert_registry_error(
                    self, lambda kwargs=kwargs: registry.register_protocol_on(connection, **kwargs),
                    registry.TerminalRegistryConflict,
                )

    def test_legacy_absence_registers_pending_not_covered(self):
        path = FixtureFactory.make_ready_database(self)
        connection = FixtureFactory.borrowed_connection(self, path)
        self.assertIsNone(registry.read_protocol_for_task_on(connection, task_id="legacy"))
        row = registry.register_protocol_on(
            connection, task_id="legacy", task_instance_id="legacy-instance",
            owner_user_id=2, owner_library_id=OWNER_LIBRARY, creation_path="legacy_adoption",
        )
        self.assertEqual(row["coverage_state"], "legacy_pending_verification")
        self.assertEqual(row["roster_revision"], 0)
        self.assertIsNone(row["adopted_at"])
        self.assertIsNone(row["adoption_evidence_ref"])
        self.assertEqual(
            registry.coverage_status_on(connection, task_instance_id="legacy-instance"),
            "pending_verification",
        )


class TestRosterRevision(unittest.TestCase):
    def test_revision_compare_and_set_and_maximum_boundary(self):
        path = FixtureFactory.make_ready_database(self)
        connection = FixtureFactory.borrowed_connection(self, path)
        registry.register_protocol_on(
            connection, task_id="cas-task", task_instance_id="cas-instance", owner_user_id=1,
            owner_library_id=OWNER_LIBRARY, creation_path="trusted_new_task",
        )
        updated = registry.set_roster_revision_on(
            connection, task_instance_id="cas-instance", expected_revision=0, new_revision=1,
        )
        self.assertEqual(updated["roster_revision"], 1)
        RegistryAssertions.assert_registry_error(
            self,
            lambda: registry.set_roster_revision_on(
                connection, task_instance_id="cas-instance", expected_revision=0,
            ),
            registry.TerminalRegistryConflict,
        )

        registry.register_protocol_on(
            connection, task_id="raw-trigger-task", task_instance_id="raw-trigger-instance",
            owner_user_id=1, owner_library_id=OWNER_LIBRARY, creation_path="trusted_new_task",
        )
        with self.assertRaises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE team_task_protocol SET roster_revision=roster_revision+2 "
                "WHERE task_instance_id=?",
                ("raw-trigger-instance",),
            )
        raw_increment = connection.execute(
            "UPDATE team_task_protocol SET roster_revision=roster_revision+1 "
            "WHERE task_instance_id=?",
            ("raw-trigger-instance",),
        )
        self.assertEqual(raw_increment.rowcount, 1)
        self.assertEqual(
            registry.read_protocol_on(connection, task_instance_id="raw-trigger-instance")["roster_revision"],
            1,
        )

        # A valid MAX pre-state is inserted natively; no illegal UPDATE jumps across the guard.
        max_path = FixtureFactory.make_ready_database(self)
        max_connection = FixtureFactory.borrowed_connection(self, max_path)
        max_connection.execute(
            "INSERT INTO team_task_protocol "
            "(task_instance_id, task_id, owner_user_id, owner_library_id, protocol_version, "
            "coverage_state, roster_revision, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 1, 'new_covered', ?, '2026-11-16T00:00:00.000Z', "
            "'2026-11-16T00:00:00.000Z')",
            ("max-instance", "max-task", 1, OWNER_LIBRARY, MAX_SQLITE_INT),
        )
        RegistryAssertions.assert_registry_error(
            self,
            lambda: registry.set_roster_revision_on(
                max_connection, task_instance_id="max-instance", expected_revision=MAX_SQLITE_INT,
            ),
            (registry.TerminalRegistryPayloadInvalid, registry.TerminalRegistryConflict),
        )
        self.assertEqual(
            registry.read_protocol_on(max_connection, task_instance_id="max-instance")["roster_revision"],
            MAX_SQLITE_INT,
        )

    def test_integer_inputs_reject_bool_and_out_of_range(self):
        path = FixtureFactory.make_ready_database(self)
        connection = FixtureFactory.borrowed_connection(self, path)
        registry.register_protocol_on(
            connection, task_id="numeric", task_instance_id="numeric-instance",
            owner_user_id=MAX_SQLITE_INT, owner_library_id=OWNER_LIBRARY,
            creation_path="trusted_new_task",
        )
        registry.register_source_on(
            connection, task_instance_id="numeric-instance", source_user_id=MAX_SQLITE_INT,
            source_library_id=SOURCE_LIBRARY, source_path_identity="max-user",
        )
        for value in (True, False, -1, MAX_SQLITE_INT + 1, 1.0, "1"):
            with self.subTest(value=repr(value)):
                RegistryAssertions.assert_registry_error(
                    self,
                    lambda value=value: registry.register_source_on(
                        connection, task_instance_id="numeric-instance", source_user_id=value,
                        source_library_id=SOURCE_LIBRARY, source_path_identity="bad-user",
                    ),
                    registry.TerminalRegistryPayloadInvalid,
                )
        RegistryAssertions.assert_registry_error(
            self,
            lambda: registry.register_protocol_on(
                connection, task_id="revision-init", task_instance_id="revision-init-instance",
                owner_user_id=1, owner_library_id=OWNER_LIBRARY,
                creation_path="trusted_new_task", roster_revision=MAX_SQLITE_INT,
            ),
            registry.TerminalRegistryPayloadInvalid,
        )
        # Generation overflow is tested from a legal native retired-row pre-state.
        connection.execute(
            "INSERT INTO team_source_registry "
            "(registration_id, task_instance_id, source_user_id, source_library_id, "
            "source_path_identity, generation, status, registered_at, retired_at, retired_reason) "
            "VALUES (?, ?, ?, ?, ?, ?, 'retired', ?, ?, ?)",
            ("max-generation", "numeric-instance", 99, SOURCE_LIBRARY, "history", MAX_SQLITE_INT,
             "2026-11-16T00:00:00.000Z", "2026-11-16T00:00:01.000Z", "fixture"),
        )
        RegistryAssertions.assert_registry_error(
            self,
            lambda: registry.register_source_on(
                connection, task_instance_id="numeric-instance", source_user_id=99,
                source_library_id=SOURCE_LIBRARY, source_path_identity="next-generation",
            ),
            (registry.TerminalRegistryPayloadInvalid, registry.TerminalRegistryConflict),
        )


class TestSourceLifecycle(unittest.TestCase):
    def test_active_registration_is_idempotent_without_timestamp_change(self):
        path = FixtureFactory.make_ready_database(self)
        connection = FixtureFactory.borrowed_connection(self, path)
        registry.register_protocol_on(
            connection, task_id="source-task", task_instance_id="source-instance", owner_user_id=1,
            owner_library_id=OWNER_LIBRARY, creation_path="legacy_adoption",
        )
        first = registry.register_source_on(
            connection, task_instance_id="source-instance", source_user_id=3,
            source_library_id=SOURCE_LIBRARY, source_path_identity="opaque/path",
            registration_id="registration-one",
        )
        same = registry.register_source_on(
            connection, task_instance_id="source-instance", source_user_id=3,
            source_library_id=SOURCE_LIBRARY, source_path_identity="opaque/path",
        )
        self.assertEqual(same, first)
        self.assertRegex(first["registered_at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$")

    def test_changed_active_identity_or_registration_conflicts(self):
        path = FixtureFactory.make_ready_database(self)
        connection = FixtureFactory.borrowed_connection(self, path)
        registry.register_protocol_on(
            connection, task_id="active-task", task_instance_id="active-instance", owner_user_id=1,
            owner_library_id=OWNER_LIBRARY, creation_path="legacy_adoption",
        )
        initial = registry.register_source_on(
            connection, task_instance_id="active-instance", source_user_id=4,
            source_library_id=SOURCE_LIBRARY, source_path_identity="path-one",
            registration_id="active-reg",
        )
        alternatives = (
            dict(source_library_id="c" * 32, source_path_identity="path-one", registration_id="active-reg"),
            dict(source_library_id=SOURCE_LIBRARY, source_path_identity="path-two", registration_id="active-reg"),
            dict(source_library_id=SOURCE_LIBRARY, source_path_identity="path-one", registration_id="different-reg"),
        )
        for change in alternatives:
            with self.subTest(change=change):
                RegistryAssertions.assert_registry_error(
                    self,
                    lambda change=change: registry.register_source_on(
                        connection, task_instance_id="active-instance", source_user_id=4, **change
                    ),
                    registry.TerminalRegistryConflict,
                )
        self.assertEqual(
            registry.read_source_on(connection, registration_id="active-reg"), initial
        )

    def test_same_library_is_allowed_for_distinct_users(self):
        path = FixtureFactory.make_ready_database(self)
        connection = FixtureFactory.borrowed_connection(self, path)
        registry.register_protocol_on(
            connection, task_id="shared-library", task_instance_id="shared-library-instance",
            owner_user_id=1, owner_library_id=OWNER_LIBRARY, creation_path="legacy_adoption",
        )
        rows = [
            registry.register_source_on(
                connection, task_instance_id="shared-library-instance", source_user_id=user,
                source_library_id=SOURCE_LIBRARY, source_path_identity=f"user-{user}",
            )
            for user in (11, 12)
        ]
        self.assertEqual([row["source_library_id"] for row in rows], [SOURCE_LIBRARY, SOURCE_LIBRARY])
        self.assertEqual(len(registry.list_sources_on(connection, task_instance_id="shared-library-instance")), 2)

    def test_retirement_and_rejoin_create_generation_with_empty_watermark(self):
        path = FixtureFactory.make_ready_database(self)
        connection = FixtureFactory.borrowed_connection(self, path)
        registry.register_protocol_on(
            connection, task_id="generation", task_instance_id="generation-instance", owner_user_id=1,
            owner_library_id=OWNER_LIBRARY, creation_path="legacy_adoption",
        )
        old = registry.register_source_on(
            connection, task_instance_id="generation-instance", source_user_id=8,
            source_library_id=SOURCE_LIBRARY, source_path_identity="source-path",
            registration_id="generation-one",
        )
        retired = registry.retire_source_on(
            connection, registration_id="generation-one", retired_reason="member left",
            archive_watermark="opaque-watermark",
        )
        same_retirement = registry.retire_source_on(
            connection, registration_id="generation-one", retired_reason="member left",
            archive_watermark="opaque-watermark",
        )
        self.assertEqual(same_retirement, retired)
        new = registry.register_source_on(
            connection, task_instance_id="generation-instance", source_user_id=8,
            source_library_id=SOURCE_LIBRARY, source_path_identity="source-path",
            registration_id="generation-two",
        )
        self.assertEqual(old["generation"], 1)
        self.assertEqual(retired["status"], "retired")
        self.assertEqual(retired["archive_watermark"], "opaque-watermark")
        self.assertEqual(new["generation"], 2)
        self.assertIsNone(new["archive_watermark"])
        self.assertEqual(len(registry.list_sources_on(connection, task_instance_id="generation-instance")), 2)

    def test_registration_id_reuse_conflicts(self):
        path = FixtureFactory.make_ready_database(self)
        connection = FixtureFactory.borrowed_connection(self, path)
        registry.register_protocol_on(
            connection, task_id="reuse", task_instance_id="reuse-instance", owner_user_id=1,
            owner_library_id=OWNER_LIBRARY, creation_path="legacy_adoption",
        )
        registry.register_source_on(
            connection, task_instance_id="reuse-instance", source_user_id=1,
            source_library_id=SOURCE_LIBRARY, source_path_identity="first", registration_id="used-id",
        )
        RegistryAssertions.assert_registry_error(
            self,
            lambda: registry.register_source_on(
                connection, task_instance_id="reuse-instance", source_user_id=2,
                source_library_id=SOURCE_LIBRARY, source_path_identity="second", registration_id="used-id",
            ),
            registry.TerminalRegistryConflict,
        )

    def test_source_status_distinguishes_unknown_from_retired(self):
        path = FixtureFactory.make_ready_database(self)
        connection = FixtureFactory.borrowed_connection(self, path)
        self.assertIsNone(
            registry.active_source_on(
                connection, task_instance_id="no-parent-no-source", source_user_id=77
            )
        )
        self.assertEqual(
            registry.source_status_on(
                connection, task_instance_id="no-parent-no-source", source_user_id=77
            ),
            "unknown",
        )
        registry.register_protocol_on(
            connection, task_id="status", task_instance_id="status-instance", owner_user_id=1,
            owner_library_id=OWNER_LIBRARY, creation_path="legacy_adoption",
        )
        self.assertEqual(
            registry.source_status_on(connection, task_instance_id="status-instance", source_user_id=77),
            "unknown",
        )
        source = registry.register_source_on(
            connection, task_instance_id="status-instance", source_user_id=77,
            source_library_id=SOURCE_LIBRARY, source_path_identity="status-path",
            registration_id="status-reg",
        )
        self.assertEqual(
            registry.source_status_on(connection, task_instance_id="status-instance", source_user_id=77),
            "active",
        )
        registry.retire_source_on(connection, registration_id=source["registration_id"], retired_reason="retired")
        self.assertEqual(
            registry.source_status_on(connection, task_instance_id="status-instance", source_user_id=77),
            "retired",
        )

    def test_list_unknown_task_differs_from_known_empty_registry(self):
        path = FixtureFactory.make_ready_database(self)
        connection = FixtureFactory.borrowed_connection(self, path)
        RegistryAssertions.assert_registry_error(
            self,
            lambda: registry.list_sources_on(connection, task_instance_id="no-protocol"),
            registry.TerminalRegistryStateError,
        )
        registry.register_protocol_on(
            connection, task_id="empty", task_instance_id="empty-instance", owner_user_id=1,
            owner_library_id=OWNER_LIBRARY, creation_path="legacy_adoption",
        )
        self.assertEqual(registry.list_sources_on(connection, task_instance_id="empty-instance"), [])


class TestNoImplicitAdoption(unittest.TestCase):
    def test_reads_and_source_writes_never_verify_pending_protocol(self):
        path = FixtureFactory.make_ready_database(self)
        connection = FixtureFactory.borrowed_connection(self, path)
        pending = registry.register_protocol_on(
            connection, task_id="pending", task_instance_id="pending-instance", owner_user_id=1,
            owner_library_id=OWNER_LIBRARY, creation_path="legacy_adoption",
        )
        self.assertEqual(registry.read_protocol_on(connection, task_instance_id="pending-instance"), pending)
        self.assertEqual(registry.coverage_status_on(connection, task_instance_id="pending-instance"), "pending_verification")
        registry.register_source_on(
            connection, task_instance_id="pending-instance", source_user_id=2,
            source_library_id=SOURCE_LIBRARY, source_path_identity="new-source",
        )
        self.assertEqual(registry.list_sources_on(connection, task_instance_id="pending-instance")[0]["status"], "active")
        current = registry.read_protocol_on(connection, task_instance_id="pending-instance")
        self.assertEqual(current["coverage_state"], "legacy_pending_verification")
        self.assertIsNone(current["adopted_at"])
        self.assertIsNone(current["adoption_evidence_ref"])
        self.assertIsNone(current["adoption_evidence_json"])
        self.assertIsNone(current["adoption_evidence_sha256"])


class TestAdoptionEvidence(unittest.TestCase):
    def test_verified_evidence_is_canonical_and_hash_bound(self):
        path = FixtureFactory.make_ready_database(self)
        connection = FixtureFactory.borrowed_connection(self, path)
        registry.register_protocol_on(
            connection, task_id="adopt", task_instance_id="adopt-instance", owner_user_id=5,
            owner_library_id=OWNER_LIBRARY, creation_path="legacy_adoption",
        )
        source = registry.register_source_on(
            connection, task_instance_id="adopt-instance", source_user_id=6,
            source_library_id=SOURCE_LIBRARY, source_path_identity="actor-path", registration_id="adopt-source",
        )
        caller_evidence = {
            "sources": [{
                "source_user_id": source["source_user_id"], "registration_id": source["registration_id"],
                "generation": source["generation"], "source_library_id": source["source_library_id"],
            }],
            "verification_scope": "scope verified by operator",
            "verification_method": "reviewed roster and source records",
            "conflict_handling": "resolved before adoption",
            "prior_history_unknown": True,
        }
        row = registry.mark_legacy_verified_on(
            connection, task_instance_id="adopt-instance", expected_roster_revision=0,
            adoption_evidence_ref="evidence/ref/one", adoption_evidence=caller_evidence,
        )
        stored = json.loads(row["adoption_evidence_json"])
        self.assertEqual(row["coverage_state"], "legacy_verified")
        self.assertEqual(row["adopted_at"], stored["forward_coverage_start"])
        self.assertEqual(stored["schema"], "team_terminal_legacy_adoption.v1")
        self.assertEqual(stored["task_instance_id"], "adopt-instance")
        self.assertEqual(stored["task_id"], "adopt")
        self.assertEqual(stored["owner_user_id"], 5)
        self.assertEqual(stored["owner_library_id"], OWNER_LIBRARY)
        self.assertEqual(stored["sources"], caller_evidence["sources"])
        self.assertEqual(row["adoption_evidence_json"], RegistryAssertions.canonical_evidence_json(stored))
        self.assertEqual(
            row["adoption_evidence_sha256"], RegistryAssertions.expected_evidence_sha256(stored)
        )
        self.assertEqual(row["adoption_evidence_ref"], "evidence/ref/one")
        self.assertRegex(row["adopted_at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$")

    def test_adoption_rejects_invalid_shapes_and_unknown_keys(self):
        invalid_evidence = (
            {"verification_scope": "s", "verification_method": "m", "conflict_handling": "c", "prior_history_unknown": True},
            {"sources": [], "verification_scope": "s", "verification_method": "m", "conflict_handling": "c", "prior_history_unknown": False},
            {"sources": [], "verification_scope": "s", "verification_method": "m", "conflict_handling": "c", "prior_history_unknown": 1},
            {"sources": [], "verification_scope": "s", "verification_method": "m", "conflict_handling": "c", "prior_history_unknown": True, "extra": 1},
            {"sources": [], "verification_scope": "s", "verification_method": "m", "conflict_handling": "c", "prior_history_unknown": True, "nested": float("nan")},
            {"sources": [], "verification_scope": "bad\x00scope", "verification_method": "m", "conflict_handling": "c", "prior_history_unknown": True},
        )
        for index, evidence in enumerate(invalid_evidence):
            with self.subTest(index=index):
                path = FixtureFactory.make_ready_database(self)
                connection = FixtureFactory.borrowed_connection(self, path)
                registry.register_protocol_on(
                    connection, task_id=f"invalid-{index}", task_instance_id=f"invalid-instance-{index}",
                    owner_user_id=1, owner_library_id=OWNER_LIBRARY, creation_path="legacy_adoption",
                )
                RegistryAssertions.assert_registry_error(
                    self,
                    lambda evidence=evidence, index=index: registry.mark_legacy_verified_on(
                        connection, task_instance_id=f"invalid-instance-{index}",
                        expected_roster_revision=0, adoption_evidence_ref="valid-ref",
                        adoption_evidence=evidence,
                    ),
                    registry.TerminalRegistryPayloadInvalid,
                )
                self.assertEqual(
                    registry.coverage_status_on(connection, task_instance_id=f"invalid-instance-{index}"),
                    "pending_verification",
                )

    def test_adoption_sources_bind_current_active_and_related_retired_rows(self):
        path = FixtureFactory.make_ready_database(self)
        connection = FixtureFactory.borrowed_connection(self, path)
        registry.register_protocol_on(
            connection, task_id="sources-evidence", task_instance_id="sources-evidence-instance",
            owner_user_id=1, owner_library_id=OWNER_LIBRARY, creation_path="legacy_adoption",
        )
        old = registry.register_source_on(
            connection, task_instance_id="sources-evidence-instance", source_user_id=10,
            source_library_id=SOURCE_LIBRARY, source_path_identity="old-path", registration_id="old-reg",
        )
        registry.retire_source_on(connection, registration_id="old-reg", retired_reason="rotated")
        active = registry.register_source_on(
            connection, task_instance_id="sources-evidence-instance", source_user_id=10,
            source_library_id="c" * 32, source_path_identity="new-path", registration_id="new-reg",
        )
        def evidence(sources):
            return {
                "sources": sources,
                "verification_scope": "old and active source records reviewed",
                "verification_method": "operator comparison",
                "conflict_handling": "resolved",
                "prior_history_unknown": True,
            }
        valid_items = [
            {"source_user_id": old["source_user_id"], "registration_id": old["registration_id"],
             "generation": old["generation"], "source_library_id": old["source_library_id"]},
            {"source_user_id": active["source_user_id"], "registration_id": active["registration_id"],
             "generation": active["generation"], "source_library_id": active["source_library_id"]},
        ]
        verified = registry.mark_legacy_verified_on(
            connection, task_instance_id="sources-evidence-instance", expected_roster_revision=0,
            adoption_evidence_ref="source-evidence", adoption_evidence=evidence(valid_items),
        )
        self.assertEqual(json.loads(verified["adoption_evidence_json"])["sources"], valid_items)

        # Missing the active registration is invalid and must leave an independent pending row pending.
        registry.register_protocol_on(
            connection, task_id="missing-active", task_instance_id="missing-active-instance",
            owner_user_id=1, owner_library_id=OWNER_LIBRARY, creation_path="legacy_adoption",
        )
        registry.register_source_on(
            connection, task_instance_id="missing-active-instance", source_user_id=10,
            source_library_id=SOURCE_LIBRARY, source_path_identity="required", registration_id="required-reg",
        )
        RegistryAssertions.assert_registry_error(
            self,
            lambda: registry.mark_legacy_verified_on(
                connection, task_instance_id="missing-active-instance", expected_roster_revision=0,
                adoption_evidence_ref="missing-active-ref", adoption_evidence=evidence([]),
            ),
            (registry.TerminalRegistryStateError, registry.TerminalRegistryUnavailable),
        )
        self.assertEqual(registry.coverage_status_on(connection, task_instance_id="missing-active-instance"), "pending_verification")

    def test_empty_adoption_sources_are_valid_only_for_empty_active_set(self):
        path = FixtureFactory.make_ready_database(self)
        connection = FixtureFactory.borrowed_connection(self, path)
        registry.register_protocol_on(
            connection, task_id="empty-evidence", task_instance_id="empty-evidence-instance",
            owner_user_id=1, owner_library_id=OWNER_LIBRARY, creation_path="legacy_adoption",
        )
        empty = {
            "sources": [], "verification_scope": "no active sources at adoption",
            "verification_method": "verified empty roster", "conflict_handling": "none",
            "prior_history_unknown": True,
        }
        verified = registry.mark_legacy_verified_on(
            connection, task_instance_id="empty-evidence-instance", expected_roster_revision=0,
            adoption_evidence_ref="empty-evidence-ref", adoption_evidence=empty,
        )
        self.assertEqual(json.loads(verified["adoption_evidence_json"])["sources"], [])

        pending_path = FixtureFactory.make_ready_database(self)
        pending_connection = FixtureFactory.borrowed_connection(self, pending_path)
        registry.register_protocol_on(
            pending_connection, task_id="not-empty", task_instance_id="not-empty-instance",
            owner_user_id=1, owner_library_id=OWNER_LIBRARY, creation_path="legacy_adoption",
        )
        registry.register_source_on(
            pending_connection, task_instance_id="not-empty-instance", source_user_id=1,
            source_library_id=SOURCE_LIBRARY, source_path_identity="active",
        )
        RegistryAssertions.assert_registry_error(
            self,
            lambda: registry.mark_legacy_verified_on(
                pending_connection, task_instance_id="not-empty-instance", expected_roster_revision=0,
                adoption_evidence_ref="bad-empty", adoption_evidence=empty,
            ),
            (registry.TerminalRegistryStateError, registry.TerminalRegistryUnavailable),
        )

    def test_same_evidence_retry_is_idempotent_and_changes_conflict(self):
        path = FixtureFactory.make_ready_database(self)
        connection = FixtureFactory.borrowed_connection(self, path)
        registry.register_protocol_on(
            connection, task_id="retry", task_instance_id="retry-instance", owner_user_id=1,
            owner_library_id=OWNER_LIBRARY, creation_path="legacy_adoption",
        )
        evidence = {
            "sources": [], "verification_scope": "empty roster", "verification_method": "review",
            "conflict_handling": "none", "prior_history_unknown": True,
        }
        first = registry.mark_legacy_verified_on(
            connection, task_instance_id="retry-instance", expected_roster_revision=0,
            adoption_evidence_ref="retry-ref", adoption_evidence=evidence,
        )
        retry = registry.mark_legacy_verified_on(
            connection, task_instance_id="retry-instance", expected_roster_revision=0,
            adoption_evidence_ref="retry-ref", adoption_evidence=evidence,
        )
        self.assertEqual(retry, first)
        changed = dict(evidence, verification_method="different method")
        RegistryAssertions.assert_registry_error(
            self,
            lambda: registry.mark_legacy_verified_on(
                connection, task_instance_id="retry-instance", expected_roster_revision=0,
                adoption_evidence_ref="retry-ref", adoption_evidence=changed,
            ),
            registry.TerminalRegistryConflict,
        )
        RegistryAssertions.assert_registry_error(
            self,
            lambda: registry.mark_legacy_verified_on(
                connection, task_instance_id="retry-instance", expected_roster_revision=1,
                adoption_evidence_ref="retry-ref", adoption_evidence=evidence,
            ),
            registry.TerminalRegistryConflict,
        )
        registry.register_protocol_on(
            connection, task_id="new-covered", task_instance_id="new-covered-instance", owner_user_id=1,
            owner_library_id=OWNER_LIBRARY, creation_path="trusted_new_task",
        )
        RegistryAssertions.assert_registry_error(
            self,
            lambda: registry.mark_legacy_verified_on(
                connection, task_instance_id="new-covered-instance", expected_roster_revision=0,
                adoption_evidence_ref="not-allowed", adoption_evidence=evidence,
            ),
            registry.TerminalRegistryStateError,
        )

    def test_verified_history_survives_later_roster_changes(self):
        path = FixtureFactory.make_ready_database(self)
        connection = FixtureFactory.borrowed_connection(self, path)
        registry.register_protocol_on(
            connection, task_id="historical", task_instance_id="historical-instance", owner_user_id=1,
            owner_library_id=OWNER_LIBRARY, creation_path="legacy_adoption",
        )
        original = registry.register_source_on(
            connection, task_instance_id="historical-instance", source_user_id=13,
            source_library_id=SOURCE_LIBRARY, source_path_identity="original", registration_id="original-reg",
        )
        evidence = {
            "sources": [{"source_user_id": 13, "registration_id": "original-reg",
                         "generation": 1, "source_library_id": SOURCE_LIBRARY}],
            "verification_scope": "original registration", "verification_method": "reviewed",
            "conflict_handling": "resolved", "prior_history_unknown": True,
        }
        verified = registry.mark_legacy_verified_on(
            connection, task_instance_id="historical-instance", expected_roster_revision=0,
            adoption_evidence_ref="historical-ref", adoption_evidence=evidence,
        )
        registry.retire_source_on(connection, registration_id="original-reg", retired_reason="replaced")
        registry.register_source_on(
            connection, task_instance_id="historical-instance", source_user_id=13,
            source_library_id="c" * 32, source_path_identity="new generation", registration_id="new-reg",
        )
        registry.set_roster_revision_on(
            connection, task_instance_id="historical-instance", expected_revision=0,
        )
        readback = registry.read_protocol_on(connection, task_instance_id="historical-instance")
        self.assertEqual(readback["coverage_state"], "legacy_verified")
        self.assertEqual(readback["adoption_evidence_json"], verified["adoption_evidence_json"])
        self.assertEqual(readback["adoption_evidence_sha256"], verified["adoption_evidence_sha256"])
        self.assertEqual(registry.coverage_status_on(connection, task_instance_id="historical-instance"), "legacy_forward_only")
        self.assertEqual(original["registration_id"], "original-reg")


class TestCorruptRowsAndStorageFailures(unittest.TestCase):
    def test_source_reads_detect_orphan_protocol_binding(self):
        path = FixtureFactory.make_ready_database(self)
        connection = FixtureFactory.borrowed_connection(self, path)
        connection.execute(
            "INSERT INTO team_source_registry "
            "(registration_id, task_instance_id, source_user_id, source_library_id, "
            "source_path_identity, generation, status, registered_at) "
            "VALUES ('orphan-reg', 'orphan-instance', 1, ?, 'path', 1, 'active', "
            "'2026-11-16T00:00:00.000Z')",
            (SOURCE_LIBRARY,),
        )
        for operation in (
            lambda: registry.read_source_on(connection, registration_id="orphan-reg"),
            lambda: registry.list_sources_on(connection, task_instance_id="orphan-instance"),
            lambda: registry.active_source_on(connection, task_instance_id="orphan-instance", source_user_id=1),
            lambda: registry.source_status_on(connection, task_instance_id="orphan-instance", source_user_id=1),
        ):
            RegistryAssertions.assert_registry_error(
                self, operation, registry.TerminalRegistryUnavailable, reason="corrupt"
            )

        empty = FixtureFactory.make_ready_database(self)
        empty_connection = FixtureFactory.borrowed_connection(self, empty)
        RegistryAssertions.assert_registry_error(
            self,
            lambda: registry.list_sources_on(empty_connection, task_instance_id="unknown-no-source"),
            registry.TerminalRegistryStateError,
        )

    def test_malformed_persisted_rows_are_typed_corrupt(self):
        path = FixtureFactory.make_ready_database(self)
        connection = FixtureFactory.borrowed_connection(self, path)
        registry.register_protocol_on(
            connection, task_id="bad-row", task_instance_id="bad-row-instance", owner_user_id=1,
            owner_library_id=OWNER_LIBRARY, creation_path="legacy_adoption",
        )
        connection.execute("PRAGMA ignore_check_constraints=ON")
        connection.execute(
            "INSERT INTO team_source_registry "
            "(registration_id, task_instance_id, source_user_id, source_library_id, "
            "source_path_identity, generation, status, registered_at) "
            "VALUES ('bad-status', 'bad-row-instance', 9, ?, 'path', 1, 'mystery', "
            "'2026-11-16T00:00:00.000Z')",
            (SOURCE_LIBRARY,),
        )
        RegistryAssertions.assert_registry_error(
            self,
            lambda: registry.read_source_on(connection, registration_id="bad-status"),
            registry.TerminalRegistryUnavailable,
            reason="corrupt",
        )
        RegistryAssertions.assert_registry_error(
            self,
            lambda: registry.list_sources_on(connection, task_instance_id="bad-row-instance"),
            registry.TerminalRegistryUnavailable,
            reason="corrupt",
        )
        # Native INSERT creates a legal state shape with invalid evidence bytes; no UPDATE
        # bypasses the immutability trigger. The read path must parse, canonicalize and hash-check.
        connection.execute(
            "INSERT INTO team_task_protocol "
            "(task_instance_id, task_id, owner_user_id, owner_library_id, protocol_version, "
            "coverage_state, roster_revision, adopted_at, adoption_evidence_ref, "
            "adoption_evidence_json, adoption_evidence_sha256, created_at, updated_at) "
            "VALUES ('bad-evidence-instance', 'bad-evidence-task', 1, ?, 1, "
            "'legacy_verified', 0, '2026-11-16T00:00:00.000Z', 'bad-evidence-ref', "
            "'{not-json', ?, '2026-11-16T00:00:00.000Z', '2026-11-16T00:00:00.000Z')",
            (OWNER_LIBRARY, "0" * 64),
        )
        RegistryAssertions.assert_registry_error(
            self,
            lambda: registry.read_protocol_on(
                connection, task_instance_id="bad-evidence-instance"
            ),
            registry.TerminalRegistryUnavailable,
            reason="corrupt",
        )

    def test_post_write_zero_rowcount_is_corrupt_and_caller_rolls_back(self):
        path = FixtureFactory.make_ready_database(self)
        seed = sqlite3.connect(path)
        self.addCleanup(seed.close)
        seed.row_factory = sqlite3.Row
        seed.execute("BEGIN")
        registry.register_protocol_on(
            seed, task_id="zero-write", task_instance_id="zero-write-instance",
            owner_user_id=1, owner_library_id=OWNER_LIBRARY, creation_path="trusted_new_task",
        )
        seed.commit()
        seed.close()

        # SIMULATED zero-write using native sqlite3 subclasses; no schema object is added.
        class ZeroWriteCursor(sqlite3.Cursor):
            def execute(self, sql, parameters=(), /):
                normalized = sql.lstrip().upper()
                if (
                    self.connection.inject_zero_update
                    and normalized.startswith("UPDATE")
                    and "TEAM_TASK_PROTOCOL" in normalized
                ):
                    self.connection.inject_zero_update = False
                    self.connection.zero_update_injected = True
                    return super().execute(
                        "UPDATE team_task_protocol "
                        "SET roster_revision=roster_revision WHERE 0"
                    )
                return super().execute(sql, parameters)

        class ZeroWriteConnection(sqlite3.Connection):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.inject_zero_update = False
                self.zero_update_injected = False

            def cursor(self, factory=ZeroWriteCursor):
                return super().cursor(factory=factory)

            def execute(self, sql, parameters=(), /):
                return self.cursor().execute(sql, parameters)

        connection = sqlite3.connect(
            path, factory=ZeroWriteConnection, cached_statements=0
        )
        self.addCleanup(connection.close)
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN")
        self.assertEqual(
            registry.read_protocol_on(connection, task_instance_id="zero-write-instance")["roster_revision"],
            0,
        )
        connection.inject_zero_update = True
        RegistryAssertions.assert_registry_error(
            self,
            lambda: registry.set_roster_revision_on(
                connection, task_instance_id="zero-write-instance", expected_revision=0,
                new_revision=1,
            ),
            registry.TerminalRegistryUnavailable,
            reason="corrupt",
        )
        self.assertTrue(connection.zero_update_injected)
        self.assertTrue(connection.in_transaction)
        connection.rollback()
        observer = sqlite3.connect(path)
        self.addCleanup(observer.close)
        self.assertEqual(
            observer.execute(
                "SELECT roster_revision FROM team_task_protocol "
                "WHERE task_instance_id='zero-write-instance'"
            ).fetchone()[0],
            0,
        )

    def test_register_source_cleanup_faults_propagate_only_when_close_fails(self):
        scenarios = (
            ("primary-and-close", True, True, False),
            ("primary-only-with-cleanup-word-note", True, False, True),
            ("close-only", False, True, False),
        )
        for label, inject_fetch_error, inject_close_error, add_cleanup_word_note in scenarios:
            with self.subTest(scenario=label):
                path = FixtureFactory.make_ready_database(self)
                task_id = "cleanup-" + label
                instance_id = "cleanup-instance-" + label
                registration_id = "cleanup-registration-" + label

                seed = sqlite3.connect(path, cached_statements=0)
                self.addCleanup(seed.close)
                seed.row_factory = sqlite3.Row
                seed.execute("BEGIN")
                registry.register_protocol_on(
                    seed, task_id=task_id, task_instance_id=instance_id, owner_user_id=41,
                    owner_library_id=OWNER_LIBRARY, creation_path="trusted_new_task",
                )
                seed.commit()
                seed.close()

                connection = sqlite3.connect(
                    path, factory=CleanupPropagationConnection, cached_statements=0
                )
                self.addCleanup(connection.close)
                connection.f2_target_source_insert_count = 0
                connection.f2_insert_fetchall_count = 0
                connection.f2_insert_result_rows = None
                connection.f2_rowcount_after_fetch = None
                connection.f2_fetch_exception = None
                connection.f2_fetch_exception_injected = False
                connection.f2_real_cursor_close_count = 0
                connection.f2_close_exception = None
                connection.f2_close_exception_injected = False
                connection.f2_recovery_source_select_count = 0
                connection.row_factory = sqlite3.Row
                # The caller sets count_changes before BEGIN; preparation already completed.
                connection.execute("PRAGMA count_changes=ON")
                self.assertEqual(
                    connection.execute("PRAGMA count_changes").fetchone()[0], 1
                )
                connection.execute("BEGIN")
                self.assertTrue(connection.in_transaction)
                self.assertIs(connection.row_factory, sqlite3.Row)
                self.assertIsNone(
                    registry.active_source_on(
                        connection, task_instance_id=instance_id, source_user_id=42
                    )
                )

                fetch_error = sqlite3.IntegrityError(
                    "simulated IntegrityError after source INSERT reached DONE"
                )
                if add_cleanup_word_note:
                    fetch_error.add_note("ordinary primary note contains cleanup wording")
                close_error = sqlite3.OperationalError(
                    "simulated cursor-close OperationalError after real close"
                )
                connection.f2_fetch_exception = fetch_error if inject_fetch_error else None
                connection.f2_close_exception = close_error if inject_close_error else None

                operation = lambda: registry.register_source_on(
                    connection, task_instance_id=instance_id, source_user_id=42,
                    source_library_id=SOURCE_LIBRARY,
                    source_path_identity="cleanup-source-path",
                    registration_id=registration_id,
                )
                if inject_fetch_error and inject_close_error:
                    error = RegistryAssertions.assert_registry_error(
                        self, operation, registry.TerminalRegistryUnavailable,
                        reason="unreadable",
                    )
                    self.assertIs(error.__cause__, fetch_error)
                    diagnostics = RegistryAssertions.exception_diagnostics(error)
                    self.assertTrue(
                        any("simulated cursor-close OperationalError" in item for item in diagnostics)
                    )
                    self.assertGreater(connection.f2_recovery_source_select_count, 0)
                elif inject_fetch_error:
                    returned = operation()
                    self.assertEqual(returned["registration_id"], registration_id)
                    self.assertEqual(returned["task_instance_id"], instance_id)
                    self.assertEqual(returned["source_user_id"], 42)
                    self.assertEqual(returned["source_library_id"], SOURCE_LIBRARY)
                    self.assertEqual(returned["source_path_identity"], "cleanup-source-path")
                    self.assertEqual(returned["generation"], 1)
                    self.assertEqual(returned["status"], "active")
                    self.assertGreater(connection.f2_recovery_source_select_count, 0)
                else:
                    error = RegistryAssertions.assert_registry_error(
                        self, operation, registry.TerminalRegistryUnavailable,
                        reason="unreadable",
                    )
                    self.assertIs(error.__cause__, close_error)
                    self.assertEqual(connection.f2_recovery_source_select_count, 0)

                self.assertEqual(connection.f2_target_source_insert_count, 1)
                self.assertEqual(connection.f2_insert_fetchall_count, 1)
                self.assertEqual(
                    [tuple(row) for row in connection.f2_insert_result_rows], [(1,)]
                )
                self.assertEqual(connection.f2_rowcount_after_fetch, 1)
                self.assertEqual(connection.f2_fetch_exception_injected, inject_fetch_error)
                self.assertEqual(connection.f2_real_cursor_close_count, 1)
                self.assertEqual(connection.f2_close_exception_injected, inject_close_error)
                self.assertTrue(connection.in_transaction)
                self.assertIs(connection.row_factory, sqlite3.Row)

                stored = registry.read_source_on(
                    connection, registration_id=registration_id
                )
                self.assertIsNotNone(stored)
                self.assertEqual(stored["task_instance_id"], instance_id)
                self.assertEqual(stored["source_user_id"], 42)
                self.assertEqual(stored["source_library_id"], SOURCE_LIBRARY)
                self.assertEqual(stored["source_path_identity"], "cleanup-source-path")
                self.assertEqual(stored["registration_id"], registration_id)
                self.assertEqual(stored["generation"], 1)
                self.assertEqual(stored["status"], "active")
                if inject_fetch_error and not inject_close_error:
                    self.assertEqual(stored, returned)

                # The caller, not the API, rolls back the completed but uncommitted INSERT.
                connection.rollback()
                observer = sqlite3.connect(path)
                self.addCleanup(observer.close)
                self.assertEqual(
                    observer.execute(
                        "SELECT count(*) FROM team_source_registry WHERE registration_id=?",
                        (registration_id,),
                    ).fetchone()[0],
                    0,
                )

    def test_sqlite_failures_keep_typed_cause_and_rollback_observable(self):
        # SIMULATED fetch fault: native SQLite connection, with text decoding made to fail
        # only when sqlite3.Row fetches returned text. This is not a disk/I/O failure test.
        path = FixtureFactory.make_ready_database(self)
        connection = FixtureFactory.borrowed_connection(self, path)
        def fail_during_fetch(_raw_text):
            raise sqlite3.OperationalError("simulated fetch-time decode failure")
        connection.text_factory = fail_during_fetch
        error = RegistryAssertions.assert_registry_error(
            self,
            lambda: registry.coverage_status_on(connection, task_instance_id="fetch-fault"),
            registry.TerminalRegistryUnavailable,
            reason="unreadable",
        )
        self.assertIsInstance(getattr(error, "__cause__", None), sqlite3.OperationalError)
        self.assertIn("simulated fetch-time decode failure", str(error.__cause__))

        # SIMULATED prepare cleanup faults through a proxy over a real SQLite connection.
        # This proves chaining/visibility behavior, not a physical storage cleanup failure.
        scratch = tempfile.TemporaryDirectory(prefix="f2-terminal-cleanup-injection-")
        self.addCleanup(scratch.cleanup)
        cleanup_path = Path(scratch.name) / "cleanup.sqlite3"
        backing = sqlite3.connect(cleanup_path)
        self.addCleanup(backing.close)
        cleanup_row_factory_at_execute = []
        cleanup_transaction_at_ddl = []

        class CleanupFaultProxy:
            @property
            def row_factory(self):
                return backing.row_factory

            @row_factory.setter
            def row_factory(self, value):
                backing.row_factory = value

            @property
            def in_transaction(self):
                return backing.in_transaction

            def execute(self, sql, *args):
                if not cleanup_row_factory_at_execute:
                    cleanup_row_factory_at_execute.append(backing.row_factory)
                if sql.lstrip().upper().startswith("CREATE TABLE"):
                    cleanup_transaction_at_ddl.append(self.in_transaction)
                    raise sqlite3.OperationalError("simulated primary DDL fault")
                return backing.execute(sql, *args)

            def commit(self):
                return backing.commit()

            def rollback(self):
                raise sqlite3.OperationalError("simulated rollback fault")

            def close(self):
                backing.close()
                raise sqlite3.OperationalError("simulated close fault")
        proxy = CleanupFaultProxy()
        self.assertIsNone(proxy.row_factory)
        self.assertFalse(proxy.in_transaction)
        with mock.patch.object(sqlite3, "connect", return_value=proxy):
            with self.assertRaises(Exception) as caught:
                registry.prepare_team_terminal_registry(cleanup_path)
        self.assertEqual(cleanup_row_factory_at_execute, [sqlite3.Row])
        self.assertEqual(cleanup_transaction_at_ddl, [True])
        rendered = "\n".join(
            RegistryAssertions.exception_diagnostics(caught.exception)
        )
        for message in (
            "simulated primary DDL fault", "simulated rollback fault", "simulated close fault"
        ):
            self.assertIn(message, rendered)

        # SIMULATED close failure after the real backing connection has committed.
        # Persisted objects prove commit is retained; the diagnostic must retain the close failure.
        committed_scratch = tempfile.TemporaryDirectory(prefix="f2-terminal-committed-close-")
        self.addCleanup(committed_scratch.cleanup)
        committed_path = Path(committed_scratch.name) / "committed.sqlite3"
        committed_backing = sqlite3.connect(committed_path)
        self.addCleanup(committed_backing.close)

        commit_statements = []
        commit_completed_states = []
        close_row_factory_states = []
        close_transaction_states = []
        real_close_completed = []
        committed_backing.set_trace_callback(commit_statements.append)

        class CloseAfterCommitProxy:
            @property
            def row_factory(self):
                return committed_backing.row_factory

            @row_factory.setter
            def row_factory(self, value):
                committed_backing.row_factory = value

            @property
            def in_transaction(self):
                return committed_backing.in_transaction

            def execute(self, sql, *args):
                return committed_backing.execute(sql, *args)

            def commit(self):
                result = committed_backing.commit()
                commit_completed_states.append(not committed_backing.in_transaction)
                return result

            def rollback(self):
                return committed_backing.rollback()

            def close(self):
                close_row_factory_states.append(self.row_factory is sqlite3.Row)
                close_transaction_states.append(self.in_transaction)
                committed_backing.close()
                real_close_completed.append(True)
                raise sqlite3.OperationalError("simulated close after successful commit")

        close_proxy = CloseAfterCommitProxy()
        self.assertIsNone(close_proxy.row_factory)
        self.assertFalse(close_proxy.in_transaction)
        with mock.patch.object(
            sqlite3, "connect", return_value=close_proxy
        ):
            with self.assertRaises(Exception) as caught_close:
                registry.prepare_team_terminal_registry(committed_path)
        self.assertIn(
            "simulated close after successful commit",
            "\n".join(RegistryAssertions.exception_diagnostics(caught_close.exception)),
        )
        self.assertEqual(commit_completed_states, [True])
        self.assertEqual(close_row_factory_states, [True])
        self.assertEqual(close_transaction_states, [False])
        self.assertEqual(real_close_completed, [True])
        self.assertTrue(
            any(statement.strip().rstrip(";").upper() == "COMMIT" for statement in commit_statements)
        )
        observer = sqlite3.connect(committed_path)
        self.addCleanup(observer.close)
        self.assertEqual(
            RegistryAssertions.capture_rows(
                observer,
                "SELECT name FROM main.sqlite_master "
                "WHERE type='table' AND name IN ('team_task_protocol','team_source_registry') "
                "ORDER BY name",
            ),
            [("team_source_registry",), ("team_task_protocol",)],
        )
