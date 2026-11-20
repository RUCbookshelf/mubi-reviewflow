"""S2-B trace-prepared seam contract tests.

Contract: .superpowers/sdd/2026-11-14-2.1.6-maintenance/
s2b-trace-prepared-brief.md. Product import and all SQLite work are deferred to
unittest execution; this file does not alter environment or repository paths.
"""
from __future__ import annotations

import importlib
import sqlite3
import tempfile
import unittest
from contextlib import closing, contextmanager
from pathlib import Path
from unittest import mock


TRACE_TABLES = (
    "trace_store",
    "trace_settings",
    "trace_operations",
    "trace_clear_operations",
    "trace_write_operations",
    "trace_events",
    "trace_field_lifecycles",
)


class _InjectedWriteFailure(Exception):
    pass


class _NativeConnectionControl:
    """Observe/fault-inject through actual sqlite3.Connection instances."""

    def __init__(self):
        self.raw_connect = sqlite3.connect
        self.connections: list[sqlite3.Connection] = []
        self.connect_calls = 0
        self.failure_at_dml: int | None = None
        self.failure: BaseException | None = None
        self._connect = None
        self.connection_type = None

    @contextmanager
    def installed(self, module):
        control = self

        class ObservedConnection(sqlite3.Connection):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.statements: list[str] = []
                self.executescript_calls = 0
                self.backup_calls = 0
                self.commit_calls = 0
                self.rollback_calls = 0
                self.close_calls = 0
                self.dml_attempts = 0

            def execute(self, sql, *args, **kwargs):
                statement = str(sql)
                self.statements.append(statement)
                head = statement.lstrip().split(None, 1)[0].upper() if statement.strip() else ""
                if head in {"INSERT", "UPDATE", "DELETE", "REPLACE"}:
                    self.dml_attempts += 1
                    if (control.failure_at_dml is not None
                            and self.dml_attempts == control.failure_at_dml):
                        raise control.failure
                return super().execute(sql, *args, **kwargs)

            def executemany(self, sql, *args, **kwargs):
                self.statements.append(str(sql))
                return super().executemany(sql, *args, **kwargs)

            def executescript(self, script):
                self.executescript_calls += 1
                self.statements.append("executescript: " + str(script))
                return super().executescript(script)

            def backup(self, *args, **kwargs):
                self.backup_calls += 1
                return super().backup(*args, **kwargs)

            def commit(self):
                self.commit_calls += 1
                return super().commit()

            def rollback(self):
                self.rollback_calls += 1
                return super().rollback()

            def close(self):
                self.close_calls += 1
                return super().close()

        def controlled_connect(*args, **kwargs):
            control.connect_calls += 1
            kwargs["factory"] = ObservedConnection
            connection = control.raw_connect(*args, **kwargs)
            control.connections.append(connection)
            return connection

        self._connect = controlled_connect
        self.connection_type = ObservedConnection
        with mock.patch.object(module.sqlite3, "connect", controlled_connect):
            yield

    def open(self, path: Path) -> sqlite3.Connection:
        if self._connect is None:
            raise AssertionError("install the connection monitor before opening a tracked connection")
        return self._connect(path)

    @staticmethod
    def reset(connection):
        connection.statements.clear()
        connection.executescript_calls = 0
        connection.backup_calls = 0
        connection.commit_calls = 0
        connection.rollback_calls = 0
        connection.close_calls = 0
        connection.dml_attempts = 0


def _transaction_or_ddl_statements(connection: sqlite3.Connection) -> list[str]:
    forbidden = {
        "BEGIN", "COMMIT", "END", "ROLLBACK", "SAVEPOINT", "RELEASE",
        "CREATE", "ALTER", "DROP", "ATTACH", "DETACH", "VACUUM", "REINDEX",
    }
    found = []
    for statement in connection.statements:
        parts = statement.strip().split(None, 1)
        head = parts[0].upper() if parts else ""
        if head in forbidden:
            found.append(statement)
        elif head == "PRAGMA" and len(parts) > 1 and parts[1].upper().startswith("JOURNAL_MODE"):
            found.append(statement)
    return found


class TracePreparedTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="s2-trace-prepared-")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.api = importlib.import_module("coscreen.research_trace")
        self.raw_connect = sqlite3.connect

    def context(self, name: str, *, request_id: str | None = None):
        return self.api.TraceContext(
            task_id="task-" + name,
            actor_id=431,
            source="manual",
            request_id=request_id,
        )

    def _trace_counts(self, connection: sqlite3.Connection) -> dict[str, int]:
        return {
            table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in TRACE_TABLES
        }

    def _database(self, name: str, *, seed_business: bool = True) -> Path:
        """Prepare structure_only first; create small real source tables for baseline tests."""
        path = self.root / name
        with closing(self.raw_connect(path)) as connection:
            self.api.ensure_schema(connection, structure_only=True)
            if seed_business:
                connection.executescript(
                    "CREATE TABLE articles (zotero_key TEXT PRIMARY KEY, doi TEXT, "
                    "item_type TEXT, import_order INTEGER);"
                    "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);"
                    "CREATE TABLE decisions (zotero_key TEXT PRIMARY KEY, decision TEXT, "
                    "exclusion_reason TEXT, notes TEXT, tags TEXT, screened_at TEXT);"
                    "CREATE TABLE stage2_decisions (zotero_key TEXT PRIMARY KEY, decision TEXT, "
                    "exclusion_reason TEXT, notes TEXT, tags TEXT, screened_at TEXT);"
                    "CREATE TABLE business_probe (value INTEGER NOT NULL);"
                    "CREATE TABLE workspace_undo (id INTEGER PRIMARY KEY, value TEXT NOT NULL);"
                )
                connection.execute(
                    "INSERT INTO articles VALUES(?,?,?,?)", ("K1", "10.1000/example", "journalArticle", 1))
                connection.execute(
                    "INSERT INTO meta VALUES(?,?)", ("protocol_version", "protocol-v1"))
                connection.execute(
                    "INSERT INTO decisions VALUES(?,?,?,?,?,?)",
                    ("K1", "include", "", "screen note", "tag-a", "2026-11-15T00:00:00Z"))
                connection.execute(
                    "INSERT INTO stage2_decisions VALUES(?,?,?,?,?,?)",
                    ("K1", "maybe", "", "fulltext note", "tag-b", "2026-11-15T00:01:00Z"))
                connection.execute("INSERT INTO business_probe VALUES(0)")
            connection.commit()
        return path

    def _table_names(self, connection: sqlite3.Connection) -> set[str]:
        return {
            str(row[0]) for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'trace_%'")
        }

    def _schema_names(self, connection: sqlite3.Connection) -> tuple[tuple[str, str, str | None], ...]:
        return tuple(connection.execute(
            "SELECT type,name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' "
            "ORDER BY type,name").fetchall())

    def _assert_prepared_runtime_is_borrowed(
        self, control: _NativeConnectionControl, connection: sqlite3.Connection,
        initial_row_factory, open_count: int, directory_entries: set[Path],
    ):
        self.assertTrue(connection.in_transaction, "prepared API must leave caller transaction active")
        self.assertEqual(connection.commit_calls, 0)
        self.assertEqual(connection.rollback_calls, 0)
        self.assertEqual(connection.close_calls, 0)
        self.assertEqual(connection.executescript_calls, 0)
        self.assertEqual(connection.backup_calls, 0)
        self.assertEqual(control.connect_calls, open_count, "prepared API must not open another connection")
        self.assertIs(connection.row_factory, initial_row_factory)
        self.assertEqual(_transaction_or_ddl_statements(connection), [])
        self.assertEqual(set(self.root.iterdir()), directory_entries,
                         "prepared runtime must not create a backup or other file")

    def _exercise_prepared_default_and_settings(self, connection, context):
        api = self.api
        api.ensure_default_recording(connection, context, prepared=True)
        initial = api.get_settings(
            connection, context.task_id, context.actor_id, prepared=True)
        self.assertEqual(initial["status"], "recording")
        self.assertEqual(set(initial["stages"]), set(api.STAGES))
        self.assertEqual(initial["version"], 1)
        self.assertTrue(initial["store_id"])

        data_before_repeat = self._trace_counts(connection)
        settings_before_repeat = api.get_settings(
            connection, context.task_id, context.actor_id, prepared=True)
        api.ensure_default_recording(connection, context, prepared=True)
        self.assertEqual(self._trace_counts(connection), data_before_repeat,
                         "repeated default initialization must add no events or receipts")
        self.assertEqual(api.get_settings(
            connection, context.task_id, context.actor_id, prepared=True), settings_before_repeat)

        self.assertTrue(api.begin_capture_transaction(connection, context, "screen", prepared=True),
                        "A1 prepared reader must observe the just-created recording setting")
        replay, original_result = api.begin_write_request(
            connection, context, stage="screen", operation="save_decision",
            subject_id="K1", payload={"decision": "include"})
        self.assertFalse(replay)
        self.assertIsNone(original_result)
        api.capture_event(
            connection, context, stage="screen", action="decision_saved", subject_id="K1",
            before={"decision": "undecided"}, after={"decision": "include"})
        api.finish_write_request(connection, context, {"saved": True})

        paused = api.update_settings(
            connection, context, stages=["screen"], status="paused",
            operation_id="pause-once", expected_version=initial["version"], prepared=True)
        self.assertEqual(paused["status"], "paused")
        self.assertEqual(paused["version"], initial["version"] + 1)
        counts_after_update = self._trace_counts(connection)
        replayed = api.update_settings(
            connection, context, stages=["screen"], status="paused",
            operation_id="pause-once", expected_version=initial["version"], prepared=True)
        self.assertEqual(replayed, paused, "same operation ID and payload replays the stored result")
        self.assertEqual(self._trace_counts(connection), counts_after_update,
                         "idempotent replay must not duplicate events or operations")
        with self.assertRaises(api.TraceVersionConflict):
            api.update_settings(
                connection, context, stages=["screen"], status="off",
                operation_id="stale-version", expected_version=initial["version"], prepared=True)
        self.assertEqual(self._trace_counts(connection), counts_after_update)

        api.ensure_default_recording(connection, context, prepared=True)
        current = api.get_settings(
            connection, context.task_id, context.actor_id, prepared=True)
        self.assertEqual(current, paused, "default init must preserve an existing paused preference")
        self.assertEqual(self._trace_counts(connection), counts_after_update)
        actions = {str(row[0]) for row in connection.execute("SELECT action FROM trace_events")}
        self.assertTrue({"baseline_item", "baseline_protocol", "baseline_decision"} <= actions)
        self.assertIn("decision_saved", actions)
        self.assertEqual(self._trace_counts(connection)["trace_write_operations"], 1)
        return current

    def test_structure_only_preparation_has_no_trace_rows_and_default_store_id_is_stable(self):
        path = self.root / "structure-only.db"
        with closing(self.raw_connect(path)) as connection:
            self.api.ensure_schema(connection, structure_only=True)
            self.assertTrue(set(TRACE_TABLES) <= self._table_names(connection))
            self.assertEqual(self._trace_counts(connection), {table: 0 for table in TRACE_TABLES})
            self.assertEqual(connection.execute("SELECT store_id FROM trace_store").fetchall(), [])

            # A gate-rejected caller may have structural metadata available, but without
            # the post-gate seam no trace data is initialized; its target write can roll back.
            connection.executescript(
                "CREATE TABLE business_probe (value INTEGER NOT NULL);"
                "CREATE TABLE workspace_undo (id INTEGER PRIMARY KEY, value TEXT NOT NULL);"
                "INSERT INTO business_probe VALUES(0);"
            )
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("UPDATE business_probe SET value=1")
            connection.execute("INSERT INTO workspace_undo(value) VALUES('staged')")
            connection.rollback()
            self.assertEqual(self._trace_counts(connection), {table: 0 for table in TRACE_TABLES})
            self.assertEqual(connection.execute("SELECT value FROM business_probe").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM workspace_undo").fetchone()[0], 0)

            self.api.ensure_schema(connection)  # standalone default behavior still creates store identity
            store_id = connection.execute("SELECT store_id FROM trace_store WHERE singleton=1").fetchone()[0]
            self.assertTrue(store_id)
            self.api.ensure_schema(connection)
            self.assertEqual(
                connection.execute("SELECT store_id FROM trace_store WHERE singleton=1").fetchone()[0],
                store_id,
            )
            self.assertEqual(self._trace_counts(connection), {
                **{table: 0 for table in TRACE_TABLES}, "trace_store": 1,
            })

    def test_prepared_default_is_atomic_with_target_and_supports_caller_writer(self):
        for finish in ("rollback", "commit"):
            with self.subTest(finish=finish):
                path = self._database(f"prepared-{finish}.db")
                context = self.context(finish, request_id=f"request-{finish}")
                control = _NativeConnectionControl()
                with control.installed(self.api):
                    connection = control.open(path)
                    connection.row_factory = sqlite3.Row
                    connection.execute("BEGIN IMMEDIATE")
                    connection.execute("UPDATE business_probe SET value=1")
                    connection.execute("INSERT INTO workspace_undo(value) VALUES(?)", ("same-target",))
                    control.reset(connection)
                    initial_row_factory = connection.row_factory
                    open_count = control.connect_calls
                    directory_entries = set(self.root.iterdir())

                    setting = self._exercise_prepared_default_and_settings(connection, context)
                    before_finish = self._trace_counts(connection)
                    self.assertGreater(before_finish["trace_events"], 0)
                    self.assertEqual(before_finish["trace_store"], 1)
                    self.assertEqual(before_finish["trace_settings"], 1)
                    self.assertGreaterEqual(before_finish["trace_operations"], 2)
                    self.assertEqual(setting["status"], "paused")
                    self._assert_prepared_runtime_is_borrowed(
                        control, connection, initial_row_factory, open_count, directory_entries)

                    if finish == "rollback":
                        connection.rollback()  # only the caller ends the transaction
                    else:
                        connection.commit()  # one target commit persists the whole prepared window
                    connection.close()

                with closing(self.raw_connect(path)) as observer:
                    if finish == "rollback":
                        self.assertEqual(self._trace_counts(observer), {
                            table: 0 for table in TRACE_TABLES})
                        self.assertEqual(observer.execute("SELECT value FROM business_probe").fetchone()[0], 0)
                        self.assertEqual(observer.execute("SELECT COUNT(*) FROM workspace_undo").fetchone()[0], 0)
                    else:
                        self.assertEqual(self._trace_counts(observer)["trace_store"], 1)
                        self.assertEqual(self._trace_counts(observer)["trace_settings"], 1)
                        self.assertGreater(self._trace_counts(observer)["trace_events"], 0)
                        self.assertEqual(self._trace_counts(observer)["trace_write_operations"], 1)
                        self.assertEqual(observer.execute("SELECT value FROM business_probe").fetchone()[0], 1)
                        self.assertEqual(observer.execute("SELECT value FROM workspace_undo").fetchone()[0],
                                         "same-target")
                        final = self.api.get_settings(
                            observer, context.task_id, context.actor_id)
                        self.assertEqual((final["status"], final["version"]), ("paused", 2))

    def test_prepared_default_mid_initialization_failure_stays_with_caller_rollback(self):
        path = self._database("prepared-injected-failure.db")
        context = self.context("injected-failure")
        injected = _InjectedWriteFailure("controlled mid-initialization write failure")
        control = _NativeConnectionControl()
        directory_entries = set(self.root.iterdir())
        with control.installed(self.api):
            connection = control.open(path)
            try:
                connection.row_factory = sqlite3.Row
                connection.execute("BEGIN IMMEDIATE")
                control.reset(connection)
                control.failure_at_dml = 3
                control.failure = injected
                open_count = control.connect_calls

                with self.assertRaises(_InjectedWriteFailure) as caught:
                    self.api.ensure_default_recording(connection, context, prepared=True)
                self.assertIs(caught.exception, injected, "the original initialization error must propagate")
                self.assertTrue(connection.in_transaction, "only the caller may roll back the target")
                self.assertEqual(connection.dml_attempts, 3, "the injected failure occurs after partial work")
                self.assertEqual(connection.commit_calls, 0)
                self.assertEqual(connection.rollback_calls, 0)
                self.assertEqual(connection.close_calls, 0)
                self.assertEqual(connection.executescript_calls, 0)
                self.assertEqual(connection.backup_calls, 0)
                self.assertEqual(control.connect_calls, open_count)
                self.assertEqual(_transaction_or_ddl_statements(connection), [])
            finally:
                try:
                    connection.rollback()
                finally:
                    connection.close()

        # SQLite may create its transaction journal until the caller rolls back.
        self.assertEqual(set(self.root.iterdir()), directory_entries)
        with closing(self.raw_connect(path)) as observer:
            self.assertEqual(self._trace_counts(observer), {table: 0 for table in TRACE_TABLES})
            self.assertEqual(observer.execute("SELECT value FROM business_probe").fetchone()[0], 0)
            self.assertEqual(observer.execute("SELECT COUNT(*) FROM workspace_undo").fetchone()[0], 0)

    def test_existing_off_paused_and_recording_preferences_survive_prepared_default(self):
        for status in ("off", "paused", "recording"):
            with self.subTest(status=status):
                path = self._database(f"existing-{status}.db")
                context = self.context("existing-" + status)
                with closing(self.raw_connect(path)) as seed:
                    initial = self.api.update_settings(
                        seed, context, stages=["fulltext"], status=status,
                        operation_id="seed-" + status, expected_version=0)
                    self.assertFalse(seed.in_transaction, "standalone update retains its own commit behavior")
                    before = {
                        "settings": tuple(tuple(row) for row in seed.execute(
                            "SELECT task_id,actor_id,stages_json,status,operation_id,version,updated_at "
                            "FROM trace_settings")),
                        "counts": self._trace_counts(seed),
                        "store": seed.execute("SELECT store_id FROM trace_store WHERE singleton=1").fetchone()[0],
                        "events": tuple(tuple(row) for row in seed.execute(
                            "SELECT action,subject_id FROM trace_events ORDER BY sequence")),
                        "operations": tuple(tuple(row) for row in seed.execute(
                            "SELECT operation_id,payload_sha256,result_json FROM trace_operations ORDER BY operation_id")),
                    }
                    self.assertEqual(initial["status"], status)

                control = _NativeConnectionControl()
                with control.installed(self.api):
                    connection = control.open(path)
                    connection.row_factory = sqlite3.Row
                    connection.execute("BEGIN IMMEDIATE")
                    control.reset(connection)
                    changes_before = connection.total_changes
                    row_factory_before = connection.row_factory
                    open_count = control.connect_calls
                    directory_entries = set(self.root.iterdir())
                    self.api.ensure_default_recording(connection, context, prepared=True)
                    actual = self.api.get_settings(
                        connection, context.task_id, context.actor_id, prepared=True)
                    self.assertEqual(actual["status"], status)
                    self.assertEqual(actual["version"], initial["version"])
                    self.assertEqual(connection.total_changes, changes_before,
                                     "existing preference path must be read-only")
                    self._assert_prepared_runtime_is_borrowed(
                        control, connection, row_factory_before, open_count, directory_entries)
                    self.assertEqual(tuple(tuple(row) for row in connection.execute(
                        "SELECT task_id,actor_id,stages_json,status,operation_id,version,updated_at "
                        "FROM trace_settings")), before["settings"])
                    self.assertEqual(self._trace_counts(connection), before["counts"])
                    self.assertEqual(
                        connection.execute("SELECT store_id FROM trace_store WHERE singleton=1").fetchone()[0],
                        before["store"],
                    )
                    self.assertEqual(tuple(tuple(row) for row in connection.execute(
                        "SELECT action,subject_id FROM trace_events ORDER BY sequence")), before["events"])
                    self.assertEqual(tuple(tuple(row) for row in connection.execute(
                        "SELECT operation_id,payload_sha256,result_json FROM trace_operations ORDER BY operation_id")),
                        before["operations"])
                    connection.rollback()
                    connection.close()

    def test_prepared_apis_require_native_active_transaction_before_sql(self):
        path = self._database("native-gate.db")
        context = self.context("native-gate")
        calls = (
            lambda connection: self.api.ensure_default_recording(
                connection, context, prepared=True),
            lambda connection: self.api.get_settings(
                connection, context.task_id, context.actor_id, prepared=True),
            lambda connection: self.api.update_settings(
                connection, context, stages=["screen"], status="paused",
                operation_id="must-not-start", expected_version=0, prepared=True),
        )
        for call in calls:
            with self.assertRaises(RuntimeError):
                call(object())

        control = _NativeConnectionControl()
        with control.installed(self.api):
            for call in calls:
                connection = control.open(path)
                connection.row_factory = sqlite3.Row
                before_factory = connection.row_factory
                before_opens = control.connect_calls
                with self.assertRaises(RuntimeError):
                    call(connection)  # native, but no caller-owned transaction
                self.assertFalse(connection.in_transaction)
                self.assertEqual(connection.statements, [], "inactive prepared call must reject before SQL")
                self.assertEqual(connection.commit_calls, 0)
                self.assertEqual(connection.rollback_calls, 0)
                self.assertEqual(connection.close_calls, 0)
                self.assertIs(connection.row_factory, before_factory)
                self.assertEqual(control.connect_calls, before_opens)
                connection.close()

    def test_prepared_default_fails_closed_on_missing_table_or_column_without_partial_sql(self):
        incomplete_paths = {}
        for kind in ("table", "column"):
            path = self._database(f"incomplete-{kind}.db")
            with closing(self.raw_connect(path)) as setup:
                if kind == "table":
                    setup.execute("DROP TABLE trace_write_operations")
                else:
                    setup.execute("ALTER TABLE trace_settings RENAME TO trace_settings_full")
                    setup.execute(
                        "CREATE TABLE trace_settings (task_id TEXT, actor_id TEXT, status TEXT)")
                setup.commit()
            incomplete_paths[kind] = path

        for kind, path in incomplete_paths.items():
            with self.subTest(kind=kind):
                control = _NativeConnectionControl()
                context = self.context("incomplete-" + kind)
                with control.installed(self.api):
                    connection = control.open(path)
                    connection.row_factory = sqlite3.Row
                    connection.execute("BEGIN IMMEDIATE")
                    control.reset(connection)
                    for call in (
                        lambda: self.api.ensure_default_recording(
                            connection, context, prepared=True),
                        lambda: self.api.get_settings(
                            connection, context.task_id, context.actor_id, prepared=True),
                        lambda: self.api.update_settings(
                            connection, context, stages=["screen"], status="paused",
                            operation_id="incomplete", expected_version=0, prepared=True),
                    ):
                        with self.assertRaises(sqlite3.OperationalError):
                            call()
                        self.assertTrue(connection.in_transaction)
                        self.assertEqual(connection.dml_attempts, 0,
                                         "schema validation must precede data initialization")
                        self.assertEqual(connection.commit_calls, 0)
                        self.assertEqual(connection.rollback_calls, 0)
                        self.assertEqual(connection.close_calls, 0)
                        self.assertEqual(connection.executescript_calls, 0)
                        self.assertEqual(connection.backup_calls, 0)
                        self.assertEqual(_transaction_or_ddl_statements(connection), [])
                    connection.rollback()
                    connection.close()

                with closing(self.raw_connect(path)) as observer:
                    names = self._table_names(observer)
                    if kind == "table":
                        self.assertNotIn("trace_write_operations", names)
                    else:
                        columns = {str(row[1]) for row in observer.execute(
                            "PRAGMA table_info(trace_settings)")}
                        self.assertEqual(columns, {"task_id", "actor_id", "status"})
                    self.assertEqual(observer.execute(
                        "SELECT COUNT(*) FROM trace_store").fetchone()[0], 0)

    def test_orphan_default_recording_receipt_rejects_without_inner_commit(self):
        path = self._database("orphan-default-receipt.db")
        context = self.context("orphan-receipt")
        with closing(self.raw_connect(path)) as setup:
            setup.execute(
                "INSERT INTO trace_operations(task_id,actor_id,operation_id,payload_sha256,result_json) "
                "VALUES(?,?,?,?,?)",
                (context.task_id, str(context.actor_id), "default-recording", "orphaned", "{}"),
            )
            setup.commit()

        control = _NativeConnectionControl()
        with control.installed(self.api):
            connection = control.open(path)
            connection.row_factory = sqlite3.Row
            connection.execute("BEGIN IMMEDIATE")
            control.reset(connection)
            with self.assertRaises((sqlite3.OperationalError, ValueError, RuntimeError)):
                self.api.ensure_default_recording(connection, context, prepared=True)
            self.assertTrue(connection.in_transaction)
            self.assertEqual(connection.commit_calls, 0)
            self.assertEqual(connection.rollback_calls, 0)
            self.assertEqual(connection.close_calls, 0)
            self.assertEqual(connection.executescript_calls, 0)
            self.assertEqual(connection.backup_calls, 0)
            self.assertEqual(_transaction_or_ddl_statements(connection), [])
            connection.rollback()
            connection.close()

        with closing(self.raw_connect(path)) as observer:
            self.assertEqual(observer.execute("SELECT COUNT(*) FROM trace_store").fetchone()[0], 0)
            self.assertEqual(observer.execute("SELECT COUNT(*) FROM trace_settings").fetchone()[0], 0)
            self.assertEqual(observer.execute(
                "SELECT payload_sha256,result_json FROM trace_operations "
                "WHERE task_id=? AND actor_id=? AND operation_id='default-recording'",
                (context.task_id, str(context.actor_id)),
            ).fetchall(), [("orphaned", "{}")])

    def test_standalone_default_and_settings_keep_legacy_idempotency_and_versioning(self):
        path = self.root / "standalone-default.db"
        context = self.context("standalone")
        with closing(self.raw_connect(path)) as connection:
            self.api.ensure_default_recording(connection, context)
            self.assertFalse(connection.in_transaction)
            first = self.api.get_settings(connection, context.task_id, context.actor_id)
            before = (self._trace_counts(connection), tuple(connection.execute(
                "SELECT action,subject_id FROM trace_events ORDER BY sequence")),
                tuple(connection.execute("SELECT operation_id,payload_sha256,result_json "
                                         "FROM trace_operations ORDER BY operation_id")))
            self.api.ensure_default_recording(connection, context)
            self.assertFalse(connection.in_transaction)
            self.assertEqual(self.api.get_settings(
                connection, context.task_id, context.actor_id), first)
            self.assertEqual((self._trace_counts(connection), tuple(connection.execute(
                "SELECT action,subject_id FROM trace_events ORDER BY sequence")),
                tuple(connection.execute("SELECT operation_id,payload_sha256,result_json "
                                         "FROM trace_operations ORDER BY operation_id"))), before)

            paused = self.api.update_settings(
                connection, context, stages=["screen"], status="paused",
                operation_id="standalone-pause", expected_version=first["version"])
            replay = self.api.update_settings(
                connection, context, stages=["screen"], status="paused",
                operation_id="standalone-pause", expected_version=first["version"])
            self.assertEqual(replay, paused)
            self.assertFalse(connection.in_transaction)
            self.assertEqual(paused["version"], first["version"] + 1)
            with self.assertRaises(self.api.TraceVersionConflict):
                self.api.update_settings(
                    connection, context, stages=["screen"], status="off",
                    operation_id="standalone-stale", expected_version=first["version"])
            self.assertFalse(connection.in_transaction)
            current = self.api.get_settings(connection, context.task_id, context.actor_id)
            self.assertEqual((current["status"], current["version"]), ("paused", paused["version"]))


if __name__ == "__main__":  # pragma: no cover - unittest entry point
    unittest.main()
