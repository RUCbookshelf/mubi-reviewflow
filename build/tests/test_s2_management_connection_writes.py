"""S2 management-writer borrowed-connection contract tests.

Test-only file.  It is authored statically: the author does not import the
product, execute SQL, or run any test.  Execution is left to an independent
runner after re-impact and review.

Scope: the four existing management writers in ``coscreen.task_collaboration``
(``grant_role``, ``set_member_screening``, ``set_business_role``,
``revoke_role``) and their keyword-only ``conn`` seam.

- ``conn`` omitted / ``None`` keeps the legacy path semantics, including its
  own DDL, migrations, commit/rollback and exception mapping.
- A borrowed conn must be a native ``sqlite3.Connection`` with
  ``row_factory is sqlite3.Row`` and an active caller transaction.  The writer
  must not touch the store path, open connections, run DDL/PRAGMA, start or
  end transactions, close the connection, or mutate ``row_factory``.
- Schema/trigger/backfill preflight happens before any real business DML.
  ``EXPLAIN`` compilation may be used as a preflight; it is not business DML
  and its cursor must be fully fetched.
- Caller owns commit/rollback.  Native SQLite errors propagate unchanged.

No private implementation is imported and no private helper is called directly.
"""

from __future__ import annotations

import importlib
import re
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path


TASK_ID = "s2-mgmt-conn-task"
OWNER_ID = 1
CODER_ID = 2
RECON_ID = 3
NEW_UID = 4
OTHER_UID = 5

S2MRW_TABLES = (
    "collaboration_task",
    "collaboration_role_grants",
    "collaboration_settings",
    "collaboration_member_settings",
    "collaboration_business_role",
    "collaboration_business_role_events",
)


class _S2MRWProbeCursor:
    """Cursor proxy that records fetches and forwards ``close`` unchanged."""

    def __init__(self, cursor, sql, registry=None):
        self._cursor = cursor
        self.sql = sql
        self.registry = registry
        self.rows_returned = 0
        self.fetchall_called = False
        self.iteration_exhausted = False
        self.saw_empty = False
        self.closed = False
        if registry is not None:
            registry.append(self)

    def execute(self, sql, parameters=()):
        # sqlite3.Cursor.execute returns the cursor itself; keep that shape so
        # product code that binds a cursor once and later fetches from it is
        # still tracked here.
        self.sql = sql
        self._cursor.execute(sql, parameters)
        return self

    def executemany(self, sql, seq_of_parameters):
        self.sql = sql
        self._cursor.executemany(sql, seq_of_parameters)
        return self

    def fetchone(self):
        row = self._cursor.fetchone()
        if row is None:
            self.saw_empty = True
        else:
            self.rows_returned += 1
        return row

    def fetchall(self):
        rows = self._cursor.fetchall()
        self.fetchall_called = True
        self.rows_returned += len(rows)
        return rows

    def fetchmany(self, size=None):
        rows = self._cursor.fetchmany(size) if size is not None else self._cursor.fetchmany()
        self.rows_returned += len(rows)
        if not rows:
            self.saw_empty = True
        return rows

    def __iter__(self):
        for row in self._cursor:
            self.rows_returned += 1
            yield row
        self.iteration_exhausted = True

    def close(self):
        if self.closed:
            return
        # Forward failures; never swallow or replace them.
        self._cursor.close()
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.close()
        return False

    def fully_fetched(self):
        return self.fetchall_called or self.iteration_exhausted or self.saw_empty

    @property
    def rowcount(self):
        return self._cursor.rowcount

    @property
    def description(self):
        return self._cursor.description

    @property
    def lastrowid(self):
        return self._cursor.lastrowid

    def __getattr__(self, name):
        return getattr(self._cursor, name)


class _S2MRWProbeConnection(sqlite3.Connection):
    """Native connection subclass recording SQL and session-control calls."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.statements = []
        self.cursors = []
        self.statement_offset = 0
        self.cursor_offset = 0
        self.commit_calls = 0
        self.rollback_calls = 0
        self.close_calls = 0
        self.executescript_calls = 0
        self._s2mrw_closed = False
        self.set_trace_callback(self.statements.append)

    def execute(self, sql, parameters=()):
        raw = super().execute(sql, parameters)
        return _S2MRWProbeCursor(raw, sql, self.cursors)

    def executemany(self, sql, seq_of_parameters):
        raw = super().executemany(sql, seq_of_parameters)
        return _S2MRWProbeCursor(raw, sql, self.cursors)

    def cursor(self, factory=None):
        raw = super().cursor(factory) if factory is not None else super().cursor()
        return _S2MRWProbeCursor(raw, "", self.cursors)

    def executescript(self, script):
        self.executescript_calls += 1
        return super().executescript(script)

    def commit(self):
        self.commit_calls += 1
        return super().commit()

    def rollback(self):
        self.rollback_calls += 1
        return super().rollback()

    def close(self):
        if self._s2mrw_closed:
            return
        super().close()
        self._s2mrw_closed = True
        self.close_calls += 1


class _S2MRWPoisonStore:
    """Store sentinel: any path/FS/attribute access in borrowed mode fails."""

    def __getattr__(self, name):
        raise AssertionError(f"borrowed mode touched store attribute: {name}")

    def __fspath__(self):
        raise AssertionError("borrowed mode resolved the store path")

    def __str__(self):
        raise AssertionError("borrowed mode stringified the store")

    def __repr__(self):
        raise AssertionError("borrowed mode repr()'d the store")


class _S2MRWDelegatingConnection:
    """Non-native wrapper used to prove the native-type check is real."""

    def __init__(self, conn):
        self._conn = conn

    def __getattr__(self, name):
        return getattr(self._conn, name)


class S2ManagementConnectionWritesTests(unittest.TestCase):
    """22 focused tests for the conn=None / borrowed management seam."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="s2-mgmt-conn-")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.task_dir = self.root / "task"
        self.task_dir.mkdir()
        # Product import is deferred to test execution; authoring is static.
        self.collab = importlib.import_module("coscreen.task_collaboration")
        self._s2mrw_prepare_public_store()

    # ------------------------------------------------------------------
    # Fixtures and helpers
    # ------------------------------------------------------------------

    def _s2mrw_prepare_public_store(self):
        self.collab.initialize_task_store(
            self.task_dir,
            task_id=TASK_ID,
            owner_user_id=OWNER_ID,
            owner_username="owner",
        )
        self.store = self.collab.shared_store_path(self.task_dir)
        for user_id, role in ((CODER_ID, "coder"), (RECON_ID, "reconciler")):
            self.collab.grant_role(
                self.store,
                actor_user_id=OWNER_ID,
                user_id=user_id,
                role=role,
                task_id=TASK_ID,
            )
        # Business schema/triggers are prepared through the public default
        # path, before any borrowed transaction exists.
        self._s2mrw_install_business_schema()

    def _s2mrw_install_business_schema(self):
        # Public default path; also proves the schema can be prepared before
        # any borrowed transaction is opened.
        self.collab.set_business_role(
            self.store,
            actor_user_id=OWNER_ID,
            user_id=RECON_ID,
            business_role="reconciler",
            task_id=TASK_ID,
        )

    def _s2mrw_close_probe(self, probe):
        # Close any cursor this test created before the connection; never
        # rely on GC.  A forwarding close failure propagates to cleanup.
        if getattr(probe, "_s2mrw_closed", False):
            return
        for cursor in list(getattr(probe, "cursors", ())):
            cursor.close()
        probe.close()

    def _s2mrw_register_probe(self, probe):
        # Registered immediately after connect and before any BEGIN.
        self.addCleanup(self._s2mrw_close_probe, probe)

    def _s2mrw_minimal_schema(self, base_store, name, statements):
        dest = self.root / "variants" / name / "collaboration.db"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(Path(base_store).read_bytes())
        conn = sqlite3.connect(str(dest), timeout=15, factory=_S2MRWProbeConnection)
        self._s2mrw_register_probe(conn)
        try:
            for statement in statements:
                with closing(conn.execute(statement)):
                    pass
            conn.commit()
        finally:
            self._s2mrw_close_probe(conn)
        return dest

    def _s2mrw_open_probe(self, path, *, begin=True, read_only=False):
        path = Path(path)
        if read_only:
            uri = path.resolve().as_uri() + "?mode=ro"
            conn = sqlite3.connect(
                uri, uri=True, timeout=15, factory=_S2MRWProbeConnection
            )
        else:
            conn = sqlite3.connect(
                str(path), timeout=15, factory=_S2MRWProbeConnection
            )
        # Registered immediately after connect, before any BEGIN.
        self._s2mrw_register_probe(conn)
        conn.row_factory = sqlite3.Row
        if begin:
            with closing(conn.execute("BEGIN" if read_only else "BEGIN IMMEDIATE")):
                pass
        return conn

    def _s2mrw_collaboration_row_counts(self, path):
        conn = sqlite3.connect(str(path), timeout=15, factory=_S2MRWProbeConnection)
        self._s2mrw_register_probe(conn)
        conn.row_factory = sqlite3.Row
        counts = {}
        try:
            for table in S2MRW_TABLES:
                try:
                    with closing(
                        conn.execute("SELECT COUNT(*) FROM main." + table)
                    ) as cur:
                        counts[table] = int(cur.fetchone()[0])
                except sqlite3.OperationalError as exc:
                    if "no such table" in str(exc).lower():
                        counts[table] = None
                    else:
                        raise
        finally:
            self._s2mrw_close_probe(conn)
        return counts

    def _s2mrw_assert_counts_unchanged(self, path, before):
        self.assertEqual(self._s2mrw_collaboration_row_counts(path), before)

    def _s2mrw_reset_probe(self, probe):
        probe.statement_offset = len(probe.statements)
        probe.cursor_offset = len(probe.cursors)
        probe.commit_calls = 0
        probe.rollback_calls = 0
        probe.close_calls = 0
        probe.executescript_calls = 0

    @staticmethod
    def _s2mrw_classify_trace(statements):
        """Classify statements by their first real keyword.

        Leading SQL comments are stripped so a comment that merely contains
        "INSERT" can never count as evidence for or against business DML.
        """
        records = []
        for sql in statements:
            if not isinstance(sql, str):
                continue
            text = sql.lstrip()
            while text:
                if text.startswith("--"):
                    newline = text.find("\n")
                    if newline < 0:
                        text = ""
                        break
                    text = text[newline + 1:].lstrip()
                    continue
                if text.startswith("/*"):
                    end = text.find("*/")
                    if end < 0:
                        text = ""
                        break
                    text = text[end + 2:].lstrip()
                    continue
                break
            if not text:
                continue
            match = re.match(r"[A-Za-z]+", text)
            if match is None:
                continue
            keyword = match.group(0).upper()
            table = None
            if keyword == "EXPLAIN":
                kind = "preflight"
            elif keyword in ("INSERT", "REPLACE"):
                kind = "dml"
                dml = re.match(
                    r"(?:INSERT(?:\s+OR\s+\w+)?|REPLACE)\s+INTO\s+"
                    r"(?:main\s*\.\s*)?([A-Za-z_][A-Za-z0-9_]*)",
                    text,
                    re.IGNORECASE,
                )
                table = dml.group(1).lower() if dml is not None else None
            elif keyword == "UPDATE":
                kind = "dml"
                dml = re.match(
                    r"UPDATE(?:\s+OR\s+\w+)?\s+"
                    r"(?:main\s*\.\s*)?([A-Za-z_][A-Za-z0-9_]*)",
                    text,
                    re.IGNORECASE,
                )
                table = dml.group(1).lower() if dml is not None else None
            elif keyword == "DELETE":
                kind = "dml"
                dml = re.match(
                    r"DELETE\s+FROM\s+"
                    r"(?:main\s*\.\s*)?([A-Za-z_][A-Za-z0-9_]*)",
                    text,
                    re.IGNORECASE,
                )
                table = dml.group(1).lower() if dml is not None else None
            elif keyword == "SELECT":
                kind = "select"
            elif keyword in ("CREATE", "ALTER", "DROP"):
                kind = "ddl"
            elif keyword == "PRAGMA":
                kind = "pragma"
            elif keyword in (
                "BEGIN", "COMMIT", "END", "ROLLBACK", "SAVEPOINT", "RELEASE"
            ):
                kind = "txn"
            elif keyword in ("ATTACH", "DETACH"):
                kind = "attach"
            else:
                kind = "other"
            records.append({"sql": sql, "kind": kind, "table": table})
        return records

    def _s2mrw_assert_no_business_dml(self, probe, *, require_active=True):
        records = self._s2mrw_classify_trace(
            probe.statements[probe.statement_offset:]
        )
        for record in records:
            self.assertNotIn(
                record["kind"],
                {"dml", "ddl", "pragma", "txn", "attach", "other"},
                "borrowed preflight issued prohibited SQL: " + record["sql"],
            )
        for cursor in probe.cursors[probe.cursor_offset:]:
            cursor_records = self._s2mrw_classify_trace([cursor.sql])
            if cursor_records and cursor_records[0]["kind"] == "preflight":
                self.assertTrue(
                    cursor.fully_fetched(),
                    "EXPLAIN cursor was not fully fetched: " + cursor.sql,
                )
                self.assertGreater(
                    cursor.rows_returned,
                    0,
                    "EXPLAIN cursor returned no rows: " + cursor.sql,
                )
        self.assertEqual(probe.commit_calls, 0)
        self.assertEqual(probe.rollback_calls, 0)
        self.assertEqual(probe.close_calls, 0)
        self.assertEqual(probe.executescript_calls, 0)
        if require_active:
            self.assertTrue(probe.in_transaction)

    def _s2mrw_assert_borrowed_success(self, probe):
        records = self._s2mrw_classify_trace(
            probe.statements[probe.statement_offset:]
        )
        for record in records:
            self.assertNotIn(
                record["kind"],
                {"ddl", "pragma", "txn", "attach", "other"},
                "borrowed writer issued prohibited SQL: " + record["sql"],
            )
        dml = [record for record in records if record["kind"] == "dml"]
        self.assertTrue(dml, "borrowed writer issued no real collaboration DML")
        for record in dml:
            self.assertIsNotNone(record["table"], "unclassified real DML")
            self.assertTrue(
                record["table"].startswith("collaboration_"),
                "real DML escaped collaboration tables: " + record["sql"],
            )
            tokens = (
                record["sql"].lower()
                .replace("(", " ").replace(")", " ")
                .replace(",", " ").split()
            )
            for position, token in enumerate(tokens[:-1]):
                if token == "where":
                    self.assertNotEqual(
                        tokens[position + 1],
                        "0",
                        "zero-row DML cannot stand in for a read-only "
                        "preflight: " + record["sql"],
                    )
        explain_positions = [
            index
            for index, record in enumerate(records)
            if record["kind"] == "preflight"
        ]
        dml_positions = [
            index
            for index, record in enumerate(records)
            if record["kind"] == "dml"
        ]
        if explain_positions:
            self.assertLess(
                max(explain_positions),
                min(dml_positions),
                "EXPLAIN preflight was not completed before real DML",
            )
        for cursor in probe.cursors[probe.cursor_offset:]:
            cursor_records = self._s2mrw_classify_trace([cursor.sql])
            if cursor_records and cursor_records[0]["kind"] == "preflight":
                self.assertTrue(
                    cursor.fully_fetched(),
                    "EXPLAIN cursor was not fully fetched: " + cursor.sql,
                )
                self.assertGreater(
                    cursor.rows_returned,
                    0,
                    "EXPLAIN cursor returned no rows: " + cursor.sql,
                )
        self.assertEqual(probe.commit_calls, 0)
        self.assertEqual(probe.rollback_calls, 0)
        self.assertEqual(probe.close_calls, 0)
        self.assertEqual(probe.executescript_calls, 0)
        self.assertTrue(probe.in_transaction)
        self.assertIs(probe.row_factory, sqlite3.Row)

    # ------------------------------------------------------------------
    # Default path: conn omitted / None keeps legacy behavior
    # ------------------------------------------------------------------

    def test_01_default_grant_role_return_permission_persistence(self):
        result = self.collab.grant_role(
            self.store,
            actor_user_id=OWNER_ID,
            user_id=NEW_UID,
            role="reconciler",
            task_id=TASK_ID,
        )
        self.assertEqual(
            set(result), {"user_id", "role", "granted_by", "granted_at"}
        )
        self.assertEqual(result["user_id"], NEW_UID)
        self.assertEqual(result["role"], "reconciler")
        self.assertEqual(result["granted_by"], OWNER_ID)
        self.assertIsInstance(result["granted_at"], str)
        self.assertTrue(result["granted_at"])

        with self.assertRaises(PermissionError):
            self.collab.grant_role(
                self.store,
                actor_user_id=CODER_ID,
                user_id=OTHER_UID,
                role="coder",
                task_id=TASK_ID,
            )

        conn = self._s2mrw_open_probe(self.store, begin=False)
        try:
            with closing(
                conn.execute(
                    "SELECT user_id, role FROM main.collaboration_role_grants "
                    "WHERE user_id=?",
                    (NEW_UID,),
                )
            ) as cur:
                row = cur.fetchone()
            self.assertIsNotNone(row)
            self.assertEqual((row["user_id"], row["role"]), (NEW_UID, "reconciler"))
            with closing(
                conn.execute(
                    "SELECT COUNT(*) FROM main.collaboration_role_grants "
                    "WHERE user_id=?",
                    (OTHER_UID,),
                )
            ) as cur:
                self.assertEqual(cur.fetchone()[0], 0)
        finally:
            self._s2mrw_close_probe(conn)

    def test_02_default_member_screening_shape_gate_persistence(self):
        result = self.collab.set_member_screening(
            self.store,
            actor_user_id=OWNER_ID,
            user_id=RECON_ID,
            enabled=True,
            task_id=TASK_ID,
        )
        self.assertEqual(
            set(result),
            {"user_id", "screening_enabled", "updated_by", "updated_at"},
        )
        self.assertEqual(result["user_id"], RECON_ID)
        self.assertIs(result["screening_enabled"], True)
        self.assertEqual(result["updated_by"], OWNER_ID)
        self.assertIsInstance(result["updated_at"], str)

        with self.assertRaises(PermissionError):
            self.collab.set_member_screening(
                self.store,
                actor_user_id=CODER_ID,
                user_id=RECON_ID,
                enabled=False,
                task_id=TASK_ID,
            )
        with self.assertRaises(ValueError):
            self.collab.set_member_screening(
                self.store,
                actor_user_id=OWNER_ID,
                user_id=CODER_ID,
                enabled=False,
                task_id=TASK_ID,
            )

        conn = self._s2mrw_open_probe(self.store, begin=False)
        try:
            with closing(
                conn.execute(
                    "SELECT screening_enabled FROM main.collaboration_member_settings "
                    "WHERE user_id=?",
                    (RECON_ID,),
                )
            ) as cur:
                row = cur.fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row["screening_enabled"], 1)
        finally:
            self._s2mrw_close_probe(conn)

    def test_03_default_revoke_role_bool_permission_persistence(self):
        self.collab.grant_role(
            self.store,
            actor_user_id=OWNER_ID,
            user_id=OTHER_UID,
            role="coder",
            task_id=TASK_ID,
        )
        with self.assertRaises(PermissionError):
            self.collab.revoke_role(
                self.store,
                actor_user_id=RECON_ID,
                user_id=OTHER_UID,
                role="coder",
                task_id=TASK_ID,
            )
        self.assertIs(
            self.collab.revoke_role(
                self.store,
                actor_user_id=OWNER_ID,
                user_id=OTHER_UID,
                role="coder",
                task_id=TASK_ID,
            ),
            True,
        )
        self.assertIs(
            self.collab.revoke_role(
                self.store,
                actor_user_id=OWNER_ID,
                user_id=OTHER_UID,
                role="coder",
                task_id=TASK_ID,
            ),
            False,
        )
        self.assertIs(
            self.collab.revoke_role(
                self.store,
                actor_user_id=OWNER_ID,
                user_id=RECON_ID,
                role="reconciler",
                task_id=TASK_ID,
            ),
            True,
        )

        conn = self._s2mrw_open_probe(self.store, begin=False)
        try:
            with closing(
                conn.execute(
                    "SELECT COUNT(*) FROM main.collaboration_role_grants "
                    "WHERE user_id=?",
                    (OTHER_UID,),
                )
            ) as cur:
                self.assertEqual(cur.fetchone()[0], 0)
            with closing(
                conn.execute(
                    "SELECT COUNT(*) FROM main.collaboration_role_grants "
                    "WHERE user_id=? AND role='reconciler'",
                    (RECON_ID,),
                )
            ) as cur:
                self.assertEqual(cur.fetchone()[0], 0)
            with closing(
                conn.execute(
                    "SELECT COUNT(*) FROM main.collaboration_member_settings "
                    "WHERE user_id=?",
                    (RECON_ID,),
                )
            ) as cur:
                self.assertEqual(cur.fetchone()[0], 0)
        finally:
            self._s2mrw_close_probe(conn)

    def test_04_default_business_role_return_schema_audit_choice(self):
        result = self.collab.set_business_role(
            self.store,
            actor_user_id=OWNER_ID,
            user_id=CODER_ID,
            business_role="reconciler",
            task_id=TASK_ID,
        )
        self.assertEqual(
            set(result),
            {
                "user_id",
                "business_role",
                "previous_role",
                "previous_config",
                "is_owner",
                "set_by",
                "changed_at",
            },
        )
        self.assertEqual(result["user_id"], CODER_ID)
        self.assertEqual(result["business_role"], "reconciler")
        self.assertEqual(result["previous_role"], "coder")
        self.assertEqual(result["previous_config"], "ok")
        self.assertIs(result["is_owner"], False)
        self.assertEqual(result["set_by"], OWNER_ID)
        self.assertIsInstance(result["changed_at"], str)

        conn = self._s2mrw_open_probe(self.store, begin=False)
        try:
            with closing(
                conn.execute(
                    "SELECT business_role FROM main.collaboration_business_role "
                    "WHERE user_id=?",
                    (CODER_ID,),
                )
            ) as cur:
                rows = cur.fetchall()
            self.assertEqual([row["business_role"] for row in rows], ["reconciler"])
            with closing(
                conn.execute(
                    "SELECT old_role, new_role "
                    "FROM main.collaboration_business_role_events "
                    "WHERE user_id=? ORDER BY id",
                    (CODER_ID,),
                )
            ) as cur:
                events = cur.fetchall()
            self.assertEqual(
                [(row["old_role"], row["new_role"]) for row in events],
                [("coder", "reconciler")],
            )
            with closing(
                conn.execute(
                    "SELECT role FROM main.collaboration_role_grants "
                    "WHERE user_id=? ORDER BY role",
                    (CODER_ID,),
                )
            ) as cur:
                grants = [row["role"] for row in cur.fetchall()]
            self.assertEqual(grants, ["coder", "reconciler"])
            with closing(
                conn.execute(
                    "SELECT screening_enabled FROM main.collaboration_member_settings "
                    "WHERE user_id=?",
                    (CODER_ID,),
                )
            ) as cur:
                settings = cur.fetchall()
            self.assertEqual([row["screening_enabled"] for row in settings], [0])
            with closing(
                conn.execute(
                    "SELECT name FROM main.sqlite_master WHERE type='trigger' "
                    "AND name IN (?,?) ORDER BY name",
                    (
                        "collaboration_business_role_events_no_update",
                        "collaboration_business_role_events_no_delete",
                    ),
                )
            ) as cur:
                triggers = {row["name"] for row in cur.fetchall()}
            self.assertEqual(
                triggers,
                {
                    "collaboration_business_role_events_no_update",
                    "collaboration_business_role_events_no_delete",
                },
            )
            with self.assertRaises(sqlite3.IntegrityError):
                with closing(
                    conn.execute(
                        "UPDATE main.collaboration_business_role_events "
                        "SET new_role='coder' WHERE user_id=?",
                        (CODER_ID,),
                    )
                ):
                    pass
            conn.rollback()
        finally:
            self._s2mrw_close_probe(conn)

    def test_05_default_store_errors_raw_vs_mapped(self):
        garbage_dir = self.root / "garbage-store"
        garbage_dir.mkdir()
        garbage = garbage_dir / "collaboration.db"
        garbage.write_bytes(b"this is not a sqlite database")

        with self.assertRaises(sqlite3.DatabaseError) as raw:
            self.collab.grant_role(
                garbage,
                actor_user_id=OWNER_ID,
                user_id=NEW_UID,
                role="coder",
                task_id=TASK_ID,
            )
        self.assertNotIsInstance(raw.exception, self.collab.OwnerUnavailableError)

        with self.assertRaises(self.collab.CollaborationStoreCorruptError):
            self.collab.set_member_screening(
                garbage,
                actor_user_id=OWNER_ID,
                user_id=RECON_ID,
                enabled=False,
                task_id=TASK_ID,
            )

        missing = self.root / "missing-store" / "collaboration.db"
        with self.assertRaises(ValueError):
            self.collab.grant_role(
                missing,
                actor_user_id=OWNER_ID,
                user_id=NEW_UID,
                role="coder",
                task_id=TASK_ID,
            )
        with self.assertRaises(ValueError):
            self.collab.set_member_screening(
                missing,
                actor_user_id=OWNER_ID,
                user_id=RECON_ID,
                enabled=False,
                task_id=TASK_ID,
            )

    # ------------------------------------------------------------------
    # Borrowed positive / same-connection visibility
    # ------------------------------------------------------------------

    def test_06_borrowed_grant_same_conn_rollback_commit(self):
        conn = self._s2mrw_open_probe(self.store)
        try:
            self._s2mrw_reset_probe(conn)
            result = self.collab.grant_role(
                self.store,
                actor_user_id=OWNER_ID,
                user_id=NEW_UID,
                role="coder",
                task_id=TASK_ID,
                conn=conn,
            )
            self.assertEqual(
                set(result), {"user_id", "role", "granted_by", "granted_at"}
            )
            self.assertEqual(result["user_id"], NEW_UID)
            self.assertEqual(result["role"], "coder")
            self.assertEqual(result["granted_by"], OWNER_ID)
            self._s2mrw_assert_borrowed_success(conn)

            with closing(
                conn.execute(
                    "SELECT COUNT(*) FROM main.collaboration_role_grants "
                    "WHERE user_id=? AND role='coder'",
                    (NEW_UID,),
                )
            ) as cur:
                self.assertEqual(cur.fetchone()[0], 1)

            self._s2mrw_reset_probe(conn)
            with self.assertRaises(PermissionError):
                self.collab.grant_role(
                    self.store,
                    actor_user_id=CODER_ID,
                    user_id=OTHER_UID,
                    role="coder",
                    task_id=TASK_ID,
                    conn=conn,
                )
            self._s2mrw_assert_no_business_dml(conn)
            conn.rollback()
        finally:
            self._s2mrw_close_probe(conn)

        reader = self._s2mrw_open_probe(self.store, begin=False)
        try:
            with closing(
                reader.execute(
                    "SELECT COUNT(*) FROM main.collaboration_role_grants "
                    "WHERE user_id=?",
                    (NEW_UID,),
                )
            ) as cur:
                self.assertEqual(cur.fetchone()[0], 0)
        finally:
            self._s2mrw_close_probe(reader)

        conn = self._s2mrw_open_probe(self.store)
        try:
            self._s2mrw_reset_probe(conn)
            self.collab.grant_role(
                self.store,
                actor_user_id=OWNER_ID,
                user_id=NEW_UID,
                role="coder",
                task_id=TASK_ID,
                conn=conn,
            )
            self._s2mrw_assert_borrowed_success(conn)
            conn.commit()
        finally:
            self._s2mrw_close_probe(conn)

        reader = self._s2mrw_open_probe(self.store, begin=False)
        try:
            with closing(
                reader.execute(
                    "SELECT COUNT(*) FROM main.collaboration_role_grants "
                    "WHERE user_id=? AND role='coder'",
                    (NEW_UID,),
                )
            ) as cur:
                self.assertEqual(cur.fetchone()[0], 1)
        finally:
            self._s2mrw_close_probe(reader)

    def test_07_borrowed_screening_same_conn_rollback(self):
        # Public default bootstrap; must finish before the borrowed BEGIN.
        self.collab.set_member_screening(
            self.store,
            actor_user_id=OWNER_ID,
            user_id=RECON_ID,
            enabled=True,
            task_id=TASK_ID,
        )

        conn = self._s2mrw_open_probe(self.store)
        try:
            self._s2mrw_reset_probe(conn)
            result = self.collab.set_member_screening(
                self.store,
                actor_user_id=OWNER_ID,
                user_id=RECON_ID,
                enabled=False,
                task_id=TASK_ID,
                conn=conn,
            )
            self.assertEqual(
                set(result),
                {"user_id", "screening_enabled", "updated_by", "updated_at"},
            )
            self.assertIs(result["screening_enabled"], False)
            self._s2mrw_assert_borrowed_success(conn)
            with closing(
                conn.execute(
                    "SELECT screening_enabled FROM main.collaboration_member_settings "
                    "WHERE user_id=?",
                    (RECON_ID,),
                )
            ) as cur:
                self.assertEqual(cur.fetchone()[0], 0)

            self._s2mrw_reset_probe(conn)
            with self.assertRaises(PermissionError):
                self.collab.set_member_screening(
                    self.store,
                    actor_user_id=CODER_ID,
                    user_id=RECON_ID,
                    enabled=True,
                    task_id=TASK_ID,
                    conn=conn,
                )
            self._s2mrw_assert_no_business_dml(conn)
            conn.rollback()
        finally:
            self._s2mrw_close_probe(conn)

        reader = self._s2mrw_open_probe(self.store, begin=False)
        try:
            with closing(
                reader.execute(
                    "SELECT screening_enabled FROM main.collaboration_member_settings "
                    "WHERE user_id=?",
                    (RECON_ID,),
                )
            ) as cur:
                self.assertEqual(cur.fetchone()[0], 1)
        finally:
            self._s2mrw_close_probe(reader)

    def test_08_borrowed_revoke_same_conn_rollback_commit(self):
        conn = self._s2mrw_open_probe(self.store)
        try:
            self._s2mrw_reset_probe(conn)
            self.assertIs(
                self.collab.revoke_role(
                    self.store,
                    actor_user_id=OWNER_ID,
                    user_id=CODER_ID,
                    role="coder",
                    task_id=TASK_ID,
                    conn=conn,
                ),
                True,
            )
            self._s2mrw_assert_borrowed_success(conn)
            with closing(
                conn.execute(
                    "SELECT COUNT(*) FROM main.collaboration_role_grants "
                    "WHERE user_id=? AND role='coder'",
                    (CODER_ID,),
                )
            ) as cur:
                self.assertEqual(cur.fetchone()[0], 0)

            self._s2mrw_reset_probe(conn)
            with self.assertRaises(PermissionError):
                self.collab.revoke_role(
                    self.store,
                    actor_user_id=RECON_ID,
                    user_id=CODER_ID,
                    role="coder",
                    task_id=TASK_ID,
                    conn=conn,
                )
            self._s2mrw_assert_no_business_dml(conn)
            conn.rollback()
        finally:
            self._s2mrw_close_probe(conn)

        reader = self._s2mrw_open_probe(self.store, begin=False)
        try:
            with closing(
                reader.execute(
                    "SELECT COUNT(*) FROM main.collaboration_role_grants "
                    "WHERE user_id=? AND role='coder'",
                    (CODER_ID,),
                )
            ) as cur:
                self.assertEqual(cur.fetchone()[0], 1)
        finally:
            self._s2mrw_close_probe(reader)

        conn = self._s2mrw_open_probe(self.store)
        try:
            self._s2mrw_reset_probe(conn)
            self.assertIs(
                self.collab.revoke_role(
                    self.store,
                    actor_user_id=OWNER_ID,
                    user_id=CODER_ID,
                    role="coder",
                    task_id=TASK_ID,
                    conn=conn,
                ),
                True,
            )
            self._s2mrw_assert_borrowed_success(conn)
            self._s2mrw_reset_probe(conn)
            self.assertIs(
                self.collab.revoke_role(
                    self.store,
                    actor_user_id=OWNER_ID,
                    user_id=CODER_ID,
                    role="coder",
                    task_id=TASK_ID,
                    conn=conn,
                ),
                False,
            )
            self._s2mrw_assert_borrowed_success(conn)
            conn.commit()
        finally:
            self._s2mrw_close_probe(conn)

        reader = self._s2mrw_open_probe(self.store, begin=False)
        try:
            with closing(
                reader.execute(
                    "SELECT COUNT(*) FROM main.collaboration_role_grants "
                    "WHERE user_id=? AND role='coder'",
                    (CODER_ID,),
                )
            ) as cur:
                self.assertEqual(cur.fetchone()[0], 0)
        finally:
            self._s2mrw_close_probe(reader)

    def test_09_borrowed_business_choice_event_grant_commit(self):

        conn = self._s2mrw_open_probe(self.store)
        try:
            self._s2mrw_reset_probe(conn)
            result = self.collab.set_business_role(
                self.store,
                actor_user_id=OWNER_ID,
                user_id=CODER_ID,
                business_role="reconciler",
                task_id=TASK_ID,
                conn=conn,
            )
            self.assertEqual(
                set(result),
                {
                    "user_id",
                    "business_role",
                    "previous_role",
                    "previous_config",
                    "is_owner",
                    "set_by",
                    "changed_at",
                },
            )
            self.assertEqual(result["business_role"], "reconciler")
            self.assertEqual(result["previous_role"], "coder")
            self.assertEqual(result["previous_config"], "ok")
            self.assertIs(result["is_owner"], False)
            self._s2mrw_assert_borrowed_success(conn)

            with closing(
                conn.execute(
                    "SELECT business_role FROM main.collaboration_business_role "
                    "WHERE user_id=?",
                    (CODER_ID,),
                )
            ) as cur:
                self.assertEqual(
                    [row["business_role"] for row in cur.fetchall()],
                    ["reconciler"],
                )
            with closing(
                conn.execute(
                    "SELECT old_role, new_role "
                    "FROM main.collaboration_business_role_events "
                    "WHERE user_id=? ORDER BY id",
                    (CODER_ID,),
                )
            ) as cur:
                self.assertEqual(
                    [(row["old_role"], row["new_role"]) for row in cur.fetchall()],
                    [("coder", "reconciler")],
                )
            with closing(
                conn.execute(
                    "SELECT role FROM main.collaboration_role_grants "
                    "WHERE user_id=? ORDER BY role",
                    (CODER_ID,),
                )
            ) as cur:
                self.assertEqual(
                    [row["role"] for row in cur.fetchall()],
                    ["coder", "reconciler"],
                )
            with closing(
                conn.execute(
                    "SELECT screening_enabled FROM main.collaboration_member_settings "
                    "WHERE user_id=?",
                    (CODER_ID,),
                )
            ) as cur:
                self.assertEqual(
                    [row["screening_enabled"] for row in cur.fetchall()], [0]
                )
            conn.commit()
        finally:
            self._s2mrw_close_probe(conn)

        reader = self._s2mrw_open_probe(self.store, begin=False)
        try:
            with closing(
                reader.execute(
                    "SELECT business_role FROM main.collaboration_business_role "
                    "WHERE user_id=?",
                    (CODER_ID,),
                )
            ) as cur:
                self.assertEqual(cur.fetchone()["business_role"], "reconciler")
            with closing(
                reader.execute(
                    "SELECT COUNT(*) FROM main.collaboration_business_role_events "
                    "WHERE user_id=?",
                    (CODER_ID,),
                )
            ) as cur:
                self.assertEqual(cur.fetchone()[0], 1)
        finally:
            self._s2mrw_close_probe(reader)

        resolution = self.collab.resolve_business_role(
            self.store, user_id=CODER_ID, task_id=TASK_ID
        )
        self.assertEqual(resolution["role"], "reconciler")
        self.assertEqual(resolution["config"], "ok")

    def test_10_borrowed_business_event_append_only(self):

        conn = self._s2mrw_open_probe(self.store)
        try:
            self._s2mrw_reset_probe(conn)
            self.collab.set_business_role(
                self.store,
                actor_user_id=OWNER_ID,
                user_id=CODER_ID,
                business_role="reconciler",
                task_id=TASK_ID,
                conn=conn,
            )
            self._s2mrw_assert_borrowed_success(conn)

            with self.assertRaises(sqlite3.IntegrityError) as update_error:
                with closing(
                    conn.execute(
                        "UPDATE main.collaboration_business_role_events "
                        "SET new_role='coder' WHERE user_id=?",
                        (CODER_ID,),
                    )
                ):
                    pass
            self.assertIn("append-only", str(update_error.exception).lower())
            self.assertTrue(conn.in_transaction)

            with self.assertRaises(sqlite3.IntegrityError) as delete_error:
                with closing(
                    conn.execute(
                        "DELETE FROM main.collaboration_business_role_events "
                        "WHERE user_id=?",
                        (CODER_ID,),
                    )
                ):
                    pass
            self.assertIn("append-only", str(delete_error.exception).lower())
            self.assertTrue(conn.in_transaction)

            with closing(
                conn.execute(
                    "SELECT COUNT(*), MIN(old_role), MIN(new_role) "
                    "FROM main.collaboration_business_role_events WHERE user_id=?",
                    (CODER_ID,),
                )
            ) as cur:
                row = cur.fetchone()
            self.assertEqual(tuple(row), (1, "coder", "reconciler"))
            conn.rollback()
        finally:
            self._s2mrw_close_probe(conn)

        reader = self._s2mrw_open_probe(self.store, begin=False)
        try:
            with closing(
                reader.execute(
                    "SELECT COUNT(*) FROM main.collaboration_business_role_events "
                    "WHERE user_id=?",
                    (CODER_ID,),
                )
            ) as cur:
                self.assertEqual(cur.fetchone()[0], 0)
            with closing(
                reader.execute(
                    "SELECT COUNT(*) FROM main.collaboration_business_role "
                    "WHERE user_id=?",
                    (CODER_ID,),
                )
            ) as cur:
                self.assertEqual(cur.fetchone()[0], 0)
        finally:
            self._s2mrw_close_probe(reader)

    # ------------------------------------------------------------------
    # Borrowed negative / contract tests
    # ------------------------------------------------------------------

    def test_11_borrowed_closed_and_readonly_native_errors(self):
        closed = self._s2mrw_open_probe(self.store, begin=False)
        self._s2mrw_close_probe(closed)
        closed_calls = (
            (
                "grant_role",
                lambda: self.collab.grant_role(
                    self.store,
                    actor_user_id=OWNER_ID,
                    user_id=NEW_UID,
                    role="coder",
                    task_id=TASK_ID,
                    conn=closed,
                ),
            ),
            (
                "set_member_screening",
                lambda: self.collab.set_member_screening(
                    self.store,
                    actor_user_id=OWNER_ID,
                    user_id=RECON_ID,
                    enabled=False,
                    task_id=TASK_ID,
                    conn=closed,
                ),
            ),
            (
                "set_business_role",
                lambda: self.collab.set_business_role(
                    self.store,
                    actor_user_id=OWNER_ID,
                    user_id=CODER_ID,
                    business_role="reconciler",
                    task_id=TASK_ID,
                    conn=closed,
                ),
            ),
            (
                "revoke_role",
                lambda: self.collab.revoke_role(
                    self.store,
                    actor_user_id=OWNER_ID,
                    user_id=CODER_ID,
                    role="coder",
                    task_id=TASK_ID,
                    conn=closed,
                ),
            ),
        )
        for name, call in closed_calls:
            with self.subTest(writer=name):
                with self.assertRaises(sqlite3.ProgrammingError):
                    call()

        before = self._s2mrw_collaboration_row_counts(self.store)
        for name, call in (
            (
                "grant_role",
                lambda conn: self.collab.grant_role(
                    self.store,
                    actor_user_id=OWNER_ID,
                    user_id=NEW_UID,
                    role="coder",
                    task_id=TASK_ID,
                    conn=conn,
                ),
            ),
            (
                "set_member_screening",
                lambda conn: self.collab.set_member_screening(
                    self.store,
                    actor_user_id=OWNER_ID,
                    user_id=RECON_ID,
                    enabled=False,
                    task_id=TASK_ID,
                    conn=conn,
                ),
            ),
        ):
            with self.subTest(readonly_writer=name):
                conn = self._s2mrw_open_probe(self.store, read_only=True)
                try:
                    self._s2mrw_reset_probe(conn)
                    with self.assertRaises(sqlite3.OperationalError) as exc:
                        call(conn)
                    self.assertNotIsInstance(
                        exc.exception, self.collab.OwnerUnavailableError
                    )
                    self.assertNotIsInstance(
                        exc.exception, self.collab.CollaborationStoreCorruptError
                    )
                    self.assertEqual(conn.commit_calls, 0)
                    self.assertEqual(conn.rollback_calls, 0)
                    self.assertEqual(conn.close_calls, 0)
                    conn.rollback()
                finally:
                    self._s2mrw_close_probe(conn)
        self._s2mrw_assert_counts_unchanged(self.store, before)

    def test_12_borrowed_wrong_type_row_factory_inactive_txn(self):
        conn = self._s2mrw_open_probe(self.store)
        try:
            wrapper = _S2MRWDelegatingConnection(conn)
            self._s2mrw_reset_probe(conn)
            with self.assertRaises(TypeError):
                self.collab.grant_role(
                    self.store,
                    actor_user_id=OWNER_ID,
                    user_id=NEW_UID,
                    role="coder",
                    task_id=TASK_ID,
                    conn=wrapper,
                )
            self._s2mrw_assert_no_business_dml(conn)
            conn.rollback()
        finally:
            self._s2mrw_close_probe(conn)

        conn = self._s2mrw_open_probe(self.store)
        try:
            conn.row_factory = tuple
            self._s2mrw_reset_probe(conn)
            with self.assertRaises((TypeError, RuntimeError)):
                self.collab.grant_role(
                    self.store,
                    actor_user_id=OWNER_ID,
                    user_id=NEW_UID,
                    role="coder",
                    task_id=TASK_ID,
                    conn=conn,
                )
            self._s2mrw_assert_no_business_dml(conn)
            conn.rollback()
        finally:
            self._s2mrw_close_probe(conn)

        conn = self._s2mrw_open_probe(self.store, begin=False)
        try:
            self._s2mrw_reset_probe(conn)
            with self.assertRaises((TypeError, RuntimeError)):
                self.collab.grant_role(
                    self.store,
                    actor_user_id=OWNER_ID,
                    user_id=NEW_UID,
                    role="coder",
                    task_id=TASK_ID,
                    conn=conn,
                )
            self._s2mrw_assert_no_business_dml(conn, require_active=False)
        finally:
            self._s2mrw_close_probe(conn)

        class _CloseBoom:
            def close(self):
                raise RuntimeError("close boom")

        proxy = _S2MRWProbeCursor(_CloseBoom(), "SELECT 1")
        with self.assertRaises(RuntimeError):
            proxy.close()
        self.assertFalse(proxy.closed)

    def test_13_borrowed_missing_table_column_conflict_unique_preflight(self):
        # Normal unique structure positive controls.
        conn = self._s2mrw_open_probe(self.store)
        try:
            self._s2mrw_reset_probe(conn)
            self.collab.grant_role(
                self.store,
                actor_user_id=OWNER_ID,
                user_id=NEW_UID,
                role="reconciler",
                task_id=TASK_ID,
                conn=conn,
            )
            self._s2mrw_assert_borrowed_success(conn)
            conn.commit()
        finally:
            self._s2mrw_close_probe(conn)
        conn = self._s2mrw_open_probe(self.store)
        try:
            self._s2mrw_reset_probe(conn)
            self.collab.set_business_role(
                self.store,
                actor_user_id=OWNER_ID,
                user_id=CODER_ID,
                business_role="reconciler",
                task_id=TASK_ID,
                conn=conn,
            )
            self._s2mrw_assert_borrowed_success(conn)
            conn.rollback()
        finally:
            self._s2mrw_close_probe(conn)

        variants = (
            (
                "missing-member-table",
                ("DROP TABLE collaboration_member_settings",),
                lambda conn: self.collab.grant_role(
                    self.store,
                    actor_user_id=OWNER_ID,
                    user_id=OTHER_UID,
                    role="reconciler",
                    task_id=TASK_ID,
                    conn=conn,
                ),
            ),
            (
                "missing-member-column",
                (
                    "ALTER TABLE collaboration_member_settings "
                    "RENAME TO _s2mrw_member_settings_old",
                    "CREATE TABLE collaboration_member_settings ("
                    "user_id INTEGER PRIMARY KEY CHECK(user_id > 0),"
                    "updated_by INTEGER NOT NULL CHECK(updated_by > 0),"
                    "updated_at TEXT NOT NULL)",
                    "INSERT INTO collaboration_member_settings"
                    "(user_id, updated_by, updated_at) "
                    "SELECT user_id, updated_by, updated_at "
                    "FROM _s2mrw_member_settings_old",
                    "DROP TABLE _s2mrw_member_settings_old",
                ),
                lambda conn: self.collab.grant_role(
                    self.store,
                    actor_user_id=OWNER_ID,
                    user_id=OTHER_UID,
                    role="reconciler",
                    task_id=TASK_ID,
                    conn=conn,
                ),
            ),
            (
                "missing-member-unique",
                (
                    "ALTER TABLE collaboration_member_settings "
                    "RENAME TO _s2mrw_member_settings_old",
                    "CREATE TABLE collaboration_member_settings ("
                    "user_id INTEGER NOT NULL CHECK(user_id > 0),"
                    "screening_enabled INTEGER NOT NULL "
                    "CHECK(screening_enabled IN (0, 1)),"
                    "updated_by INTEGER NOT NULL CHECK(updated_by > 0),"
                    "updated_at TEXT NOT NULL)",
                    "INSERT INTO collaboration_member_settings"
                    "(user_id, screening_enabled, updated_by, updated_at) "
                    "SELECT user_id, screening_enabled, updated_by, updated_at "
                    "FROM _s2mrw_member_settings_old",
                    "DROP TABLE _s2mrw_member_settings_old",
                ),
                lambda conn: self.collab.grant_role(
                    self.store,
                    actor_user_id=OWNER_ID,
                    user_id=OTHER_UID,
                    role="reconciler",
                    task_id=TASK_ID,
                    conn=conn,
                ),
            ),
            (
                "missing-business-unique",
                (
                    "ALTER TABLE collaboration_business_role "
                    "RENAME TO _s2mrw_business_role_old",
                    "CREATE TABLE collaboration_business_role ("
                    "user_id INTEGER NOT NULL CHECK(user_id > 0),"
                    "business_role TEXT NOT NULL "
                    "CHECK(business_role IN ('coder', 'reconciler')),"
                    "set_by INTEGER NOT NULL CHECK(set_by > 0),"
                    "set_at TEXT NOT NULL)",
                    "INSERT INTO collaboration_business_role"
                    "(user_id, business_role, set_by, set_at) "
                    "SELECT user_id, business_role, set_by, set_at "
                    "FROM _s2mrw_business_role_old",
                    "DROP TABLE _s2mrw_business_role_old",
                ),
                lambda conn: self.collab.set_business_role(
                    self.store,
                    actor_user_id=OWNER_ID,
                    user_id=CODER_ID,
                    business_role="reconciler",
                    task_id=TASK_ID,
                    conn=conn,
                ),
            ),
        )
        for name, statements, call in variants:
            with self.subTest(variant=name):
                variant = self._s2mrw_minimal_schema(self.store, name, statements)
                before = self._s2mrw_collaboration_row_counts(variant)
                conn = self._s2mrw_open_probe(variant)
                try:
                    self._s2mrw_reset_probe(conn)
                    with self.assertRaises(RuntimeError):
                        call(conn)
                    self._s2mrw_assert_no_business_dml(conn)
                    self._s2mrw_assert_borrowed_cursors_closed(conn)
                    self._s2mrw_assert_counts_unchanged(variant, before)
                    # Coder positive control on the same missing layout: the
                    # coder profile stays narrow (the member table and its
                    # columns are not coder preflight inputs), so this grant
                    # succeeds inside the caller transaction with the same
                    # authorized owner actor.  Tracking is reset first so the
                    # control's DML can never pollute the rejection evidence
                    # above, and the caller rollback below erases both.
                    self._s2mrw_reset_probe(conn)
                    self.collab.grant_role(
                        self.store,
                        actor_user_id=OWNER_ID,
                        user_id=OTHER_UID,
                        role="coder",
                        task_id=TASK_ID,
                        conn=conn,
                    )
                    self._s2mrw_assert_borrowed_success(conn)
                    self._s2mrw_assert_borrowed_cursors_closed(conn)
                    conn.rollback()
                    self._s2mrw_assert_counts_unchanged(variant, before)
                finally:
                    self._s2mrw_close_probe(conn)

    def test_14_borrowed_fake_trigger_protection_rejected(self):

        # Normal definitions on the public-initialized store are accepted.
        conn = self._s2mrw_open_probe(self.store)
        try:
            self._s2mrw_reset_probe(conn)
            self.collab.set_business_role(
                self.store,
                actor_user_id=OWNER_ID,
                user_id=CODER_ID,
                business_role="reconciler",
                task_id=TASK_ID,
                conn=conn,
            )
            self._s2mrw_assert_borrowed_success(conn)
            conn.rollback()
        finally:
            self._s2mrw_close_probe(conn)

        variants = (
            (
                "missing-required-trigger",
                ("DROP TRIGGER collaboration_business_role_events_no_delete",),
            ),
            (
                "fake-select-one",
                (
                    "DROP TRIGGER collaboration_business_role_events_no_update",
                    "CREATE TRIGGER collaboration_business_role_events_no_update "
                    "BEFORE UPDATE ON collaboration_business_role_events "
                    "BEGIN SELECT 1; END",
                ),
            ),
            (
                "fake-abort-string",
                (
                    "DROP TRIGGER collaboration_business_role_events_no_update",
                    "CREATE TRIGGER collaboration_business_role_events_no_update "
                    "BEFORE UPDATE ON collaboration_business_role_events "
                    "BEGIN SELECT 'abort'; END",
                ),
            ),
            (
                "wrong-event",
                (
                    "DROP TRIGGER collaboration_business_role_events_no_update",
                    "CREATE TRIGGER collaboration_business_role_events_no_update "
                    "BEFORE INSERT ON collaboration_business_role_events "
                    "BEGIN SELECT RAISE(ABORT, 'wrong event'); END",
                ),
            ),
            (
                "wrong-table",
                (
                    "DROP TRIGGER collaboration_business_role_events_no_update",
                    "CREATE TRIGGER collaboration_business_role_events_no_update "
                    "BEFORE UPDATE ON collaboration_business_role "
                    "BEGIN SELECT RAISE(ABORT, 'wrong table'); END",
                ),
            ),
            (
                "after-instead-of-before",
                (
                    "DROP TRIGGER collaboration_business_role_events_no_update",
                    "CREATE TRIGGER collaboration_business_role_events_no_update "
                    "AFTER UPDATE ON collaboration_business_role_events "
                    "BEGIN SELECT RAISE(ABORT, 'after'); END",
                ),
            ),
        )
        for name, statements in variants:
            with self.subTest(variant=name):
                variant = self._s2mrw_minimal_schema(self.store, name, statements)
                before = self._s2mrw_collaboration_row_counts(variant)
                conn = self._s2mrw_open_probe(variant)
                try:
                    self._s2mrw_reset_probe(conn)
                    with self.assertRaises(RuntimeError):
                        self.collab.set_business_role(
                            self.store,
                            actor_user_id=OWNER_ID,
                            user_id=CODER_ID,
                            business_role="reconciler",
                            task_id=TASK_ID,
                            conn=conn,
                        )
                    self._s2mrw_assert_no_business_dml(conn)
                    self._s2mrw_assert_counts_unchanged(variant, before)
                    conn.rollback()
                finally:
                    self._s2mrw_close_probe(conn)

    def test_15_borrowed_backfill_marker_value_and_scope(self):
        marker = self.collab.SCREENING_BACKFILL_KEY

        deletion = self._s2mrw_minimal_schema(
            self.store,
            "marker-deleted",
            (
                "DELETE FROM collaboration_settings WHERE key='" + marker + "'",
            ),
        )
        before = self._s2mrw_collaboration_row_counts(deletion)
        conn = self._s2mrw_open_probe(deletion)
        try:
            self._s2mrw_reset_probe(conn)
            # Coder grant positive control: the coder profile never requires
            # the backfill marker, so this succeeds and the same caller
            # transaction is kept for what follows.
            self.collab.grant_role(
                self.store,
                actor_user_id=OWNER_ID,
                user_id=NEW_UID,
                role="coder",
                task_id=TASK_ID,
                conn=conn,
            )
            self._s2mrw_assert_borrowed_success(conn)
            self._s2mrw_assert_borrowed_cursors_closed(conn)
            # Records are reset before the reconciler revoke so the positive
            # control's DML cannot pollute the zero-DML evidence below.
            self._s2mrw_reset_probe(conn)
            with self.assertRaises(RuntimeError):
                self.collab.revoke_role(
                    self.store,
                    actor_user_id=OWNER_ID,
                    user_id=RECON_ID,
                    role="reconciler",
                    task_id=TASK_ID,
                    conn=conn,
                )
            self._s2mrw_assert_no_business_dml(conn)
            self._s2mrw_assert_borrowed_cursors_closed(conn)
            # Coder revoke contrast on a correctly existing user: the coder
            # profile needs neither the marker nor the member settings, on
            # the same caller transaction.
            self._s2mrw_reset_probe(conn)
            self.assertIs(
                self.collab.revoke_role(
                    self.store,
                    actor_user_id=OWNER_ID,
                    user_id=CODER_ID,
                    role="coder",
                    task_id=TASK_ID,
                    conn=conn,
                ),
                True,
            )
            self._s2mrw_assert_borrowed_success(conn)
            self._s2mrw_assert_borrowed_cursors_closed(conn)
            conn.rollback()
            self._s2mrw_assert_counts_unchanged(deletion, before)
        finally:
            self._s2mrw_close_probe(conn)

        for name, value in (
            ("marker-date-only", "2026-11-16"),
            ("marker-empty", ""),
            ("marker-garbage", "not-a-timestamp"),
        ):
            with self.subTest(variant=name):
                variant = self._s2mrw_minimal_schema(
                    self.store,
                    name,
                    (
                        "UPDATE collaboration_settings SET value='"
                        + value
                        + "' WHERE key='"
                        + marker
                        + "'",
                    ),
                )
                before = self._s2mrw_collaboration_row_counts(variant)
                conn = self._s2mrw_open_probe(variant)
                try:
                    self.assertTrue(
                        conn.in_transaction,
                        "bad-marker negative control requires the caller transaction",
                    )
                    with closing(
                        conn.execute(
                            "SELECT COUNT(*) FROM main.collaboration_role_grants "
                            "WHERE user_id=?",
                            (NEW_UID,),
                        )
                    ) as cur:
                        self.assertEqual(
                            cur.fetchone()[0],
                            0,
                            "bad-marker target must have no existing authorization",
                        )
                    self._s2mrw_reset_probe(conn)
                    with self.assertRaises(RuntimeError):
                        self.collab.grant_role(
                            self.store,
                            actor_user_id=OWNER_ID,
                            user_id=NEW_UID,
                            role="reconciler",
                            task_id=TASK_ID,
                            conn=conn,
                        )
                    self._s2mrw_assert_no_business_dml(conn)
                    self._s2mrw_assert_borrowed_cursors_closed(conn)
                    self._s2mrw_assert_counts_unchanged(variant, before)
                    conn.rollback()
                finally:
                    self._s2mrw_close_probe(conn)

        # Full UTC ISO marker produced by public initialization is accepted.
        # A reconciler grant is required here: only that role's profile walks
        # the marker preflight, so a coder grant could not prove acceptance.
        conn = self._s2mrw_open_probe(self.store)
        try:
            self._s2mrw_reset_probe(conn)
            self.collab.grant_role(
                self.store,
                actor_user_id=OWNER_ID,
                user_id=NEW_UID,
                role="reconciler",
                task_id=TASK_ID,
                conn=conn,
            )
            self._s2mrw_assert_borrowed_success(conn)
            self._s2mrw_assert_borrowed_cursors_closed(conn)
            conn.rollback()
        finally:
            self._s2mrw_close_probe(conn)

    def test_16_borrowed_nocase_temp_and_owner_resolver_main_only(self):
        # Normal public initialization positive control.
        conn = self._s2mrw_open_probe(self.store)
        try:
            self._s2mrw_reset_probe(conn)
            self.collab.grant_role(
                self.store,
                actor_user_id=OWNER_ID,
                user_id=NEW_UID,
                role="coder",
                task_id=TASK_ID,
                conn=conn,
            )
            self._s2mrw_assert_borrowed_success(conn)
            conn.rollback()
        finally:
            self._s2mrw_close_probe(conn)

        before = self._s2mrw_collaboration_row_counts(self.store)
        conn = self._s2mrw_open_probe(self.store)
        try:
            with closing(
                conn.execute(
                    "CREATE TEMP TABLE COLLABORATION_ROLE_GRANTS("
                    "user_id INTEGER, role TEXT, granted_by INTEGER, granted_at TEXT)"
                )
            ):
                pass
            self._s2mrw_reset_probe(conn)
            with self.assertRaises(RuntimeError):
                self.collab.grant_role(
                    self.store,
                    actor_user_id=OWNER_ID,
                    user_id=NEW_UID,
                    role="coder",
                    task_id=TASK_ID,
                    conn=conn,
                )
            self._s2mrw_assert_no_business_dml(conn)
            self._s2mrw_assert_counts_unchanged(self.store, before)
            conn.rollback()
        finally:
            self._s2mrw_close_probe(conn)
        conn = self._s2mrw_open_probe(self.store)
        try:
            with closing(
                conn.execute(
                    "CREATE TEMP TABLE s2mrw_temp_trigger_target(value INTEGER)"
                )
            ):
                pass
            with closing(
                conn.execute(
                    "CREATE TEMP TRIGGER "
                    "COLLABORATION_BUSINESS_ROLE_EVENTS_NO_UPDATE "
                    "BEFORE UPDATE ON s2mrw_temp_trigger_target "
                    "BEGIN SELECT RAISE(ABORT, 'temp shadow'); END"
                )
            ):
                pass
            self._s2mrw_reset_probe(conn)
            with self.assertRaises(RuntimeError):
                self.collab.set_business_role(
                    self.store,
                    actor_user_id=OWNER_ID,
                    user_id=CODER_ID,
                    business_role="reconciler",
                    task_id=TASK_ID,
                    conn=conn,
                )
            self._s2mrw_assert_no_business_dml(conn)
            conn.rollback()
        finally:
            self._s2mrw_close_probe(conn)

        # Owner-target screening must resolve the role from main, not from a
        # same-name TEMP table.  Main has no owner choice; TEMP claims one.
        owner_variant = self._s2mrw_minimal_schema(
            self.store, "owner-resolver-temp", ()
        )
        before = self._s2mrw_collaboration_row_counts(owner_variant)
        conn = self._s2mrw_open_probe(owner_variant)
        try:
            with closing(
                conn.execute(
                    "CREATE TEMP TABLE collaboration_business_role("
                    "user_id INTEGER PRIMARY KEY, business_role TEXT NOT NULL, "
                    "set_by INTEGER NOT NULL, set_at TEXT NOT NULL)"
                )
            ):
                pass
            with closing(
                conn.execute(
                    "INSERT INTO temp.collaboration_business_role"
                    "(user_id, business_role, set_by, set_at) VALUES(?,?,?,?)",
                    (OWNER_ID, "reconciler", OWNER_ID, "2026-11-16T00:00:00Z"),
                )
            ):
                pass
            self._s2mrw_reset_probe(conn)
            # Main has a business table but no owner choice; TEMP claims one.
            # Fail-closed via contract error or the normal "no reconciler
            # role" ValueError is acceptable; a successful call would prove
            # the resolver read TEMP.
            with self.assertRaises((RuntimeError, ValueError)):
                self.collab.set_member_screening(
                    owner_variant,
                    actor_user_id=OWNER_ID,
                    user_id=OWNER_ID,
                    enabled=False,
                    task_id=TASK_ID,
                    conn=conn,
                )
            self._s2mrw_assert_no_business_dml(conn)
            self._s2mrw_assert_counts_unchanged(owner_variant, before)
            conn.rollback()
        finally:
            self._s2mrw_close_probe(conn)

        # Main business table absent: preflight must reject, never fall back
        # to the TEMP table that carries a usable-looking owner choice.
        missing_variant = self._s2mrw_minimal_schema(
            self.store,
            "owner-resolver-main-missing",
            ("DROP TABLE collaboration_business_role",),
        )
        before = self._s2mrw_collaboration_row_counts(missing_variant)
        conn = self._s2mrw_open_probe(missing_variant)
        try:
            with closing(
                conn.execute(
                    "CREATE TEMP TABLE collaboration_business_role("
                    "user_id INTEGER PRIMARY KEY, business_role TEXT NOT NULL, "
                    "set_by INTEGER NOT NULL, set_at TEXT NOT NULL)"
                )
            ):
                pass
            with closing(
                conn.execute(
                    "INSERT INTO temp.collaboration_business_role"
                    "(user_id, business_role, set_by, set_at) VALUES(?,?,?,?)",
                    (OWNER_ID, "reconciler", OWNER_ID, "2026-11-16T00:00:00Z"),
                )
            ):
                pass
            self._s2mrw_reset_probe(conn)
            # Main business table absent; a TEMP table claims an owner choice.
            # Any fail-closed outcome is acceptable (contract error or native
            # missing-table error) as long as TEMP is never used and no
            # business DML occurs.
            with self.assertRaises(
                (RuntimeError, ValueError, sqlite3.OperationalError)
            ):
                self.collab.set_member_screening(
                    missing_variant,
                    actor_user_id=OWNER_ID,
                    user_id=OWNER_ID,
                    enabled=False,
                    task_id=TASK_ID,
                    conn=conn,
                )
            self._s2mrw_assert_no_business_dml(conn)
            self._s2mrw_assert_counts_unchanged(missing_variant, before)
            conn.rollback()
        finally:
            self._s2mrw_close_probe(conn)

        # An unrelated TEMP object does not block the borrowed path.
        conn = self._s2mrw_open_probe(self.store)
        try:
            with closing(
                conn.execute("CREATE TEMP TABLE s2mrw_unrelated(value INTEGER)")
            ):
                pass
            self._s2mrw_reset_probe(conn)
            self.collab.grant_role(
                self.store,
                actor_user_id=OWNER_ID,
                user_id=NEW_UID,
                role="coder",
                task_id=TASK_ID,
                conn=conn,
            )
            self._s2mrw_assert_borrowed_success(conn)
            conn.rollback()
        finally:
            self._s2mrw_close_probe(conn)

    def test_17_borrowed_poison_store_no_fs_no_session_control_all_four(self):
        from unittest.mock import patch

        poison = _S2MRWPoisonStore()
        marker = "s2mrw-caller-owned-transaction-marker"
        before = self._s2mrw_observer_rows(
            self.store,
            "SELECT user_id,role,granted_by,granted_at FROM "
            "main.collaboration_role_grants ORDER BY user_id,role",
        )
        settings_before = self._s2mrw_observer_rows(
            self.store,
            "SELECT user_id,screening_enabled,updated_by,updated_at FROM "
            "main.collaboration_member_settings ORDER BY user_id",
        )
        choices_before = self._s2mrw_observer_rows(
            self.store,
            "SELECT user_id,business_role,set_by,set_at FROM "
            "main.collaboration_business_role ORDER BY user_id",
        )
        events_before = self._s2mrw_observer_rows(
            self.store,
            "SELECT id,user_id,old_role,new_role,set_by,set_at FROM "
            "main.collaboration_business_role_events ORDER BY id",
        )
        conn = self._s2mrw_open_recording_probe(self.store)
        original_conn = conn
        try:
            with closing(conn.execute(
                "INSERT INTO main.collaboration_settings(key,value) VALUES(?,?)",
                (marker, "caller-only-uncommitted"),
            )) as cur:
                cur.fetchall()
            # The caller cursor predates every reset/ownership window.  Keep
            # it partially consumed until all four real writers have returned.
            with closing(conn.execute(
                "SELECT 101 UNION ALL SELECT 102 UNION ALL SELECT 103 "
                "UNION ALL SELECT 104 UNION ALL SELECT 105"
            )) as caller_cursor:
                self.assertEqual(caller_cursor.fetchone()[0], 101)
                for writer in (
                    "grant_role", "set_member_screening",
                    "set_business_role", "revoke_role",
                ):
                    with self.subTest(writer=writer):
                        self._s2mrw_reset_recording_probe(conn)
                        self.assertNotIn(
                            caller_cursor, conn.cursors[conn.cursor_offset:]
                        )
                        # This is an explicit reconnect guard, not a native
                        # SQLite fault or an observation manufactured by a helper.
                        with patch.object(
                            self.collab.sqlite3, "connect",
                            side_effect=AssertionError("borrowed writer reconnected"),
                        ) as reconnect:
                            if writer == "grant_role":
                                self.collab.grant_role(
                                    poison, actor_user_id=OWNER_ID,
                                    user_id=NEW_UID, role="coder",
                                    task_id=TASK_ID, conn=conn,
                                )
                                next_value = 102
                            elif writer == "set_member_screening":
                                self.collab.set_member_screening(
                                    poison, actor_user_id=OWNER_ID,
                                    user_id=RECON_ID, enabled=False,
                                    task_id=TASK_ID, conn=conn,
                                )
                                next_value = 103
                            elif writer == "set_business_role":
                                self.collab.set_business_role(
                                    poison, actor_user_id=OWNER_ID,
                                    user_id=CODER_ID, business_role="reconciler",
                                    task_id=TASK_ID, conn=conn,
                                )
                                next_value = 104
                            else:
                                self.assertIs(self.collab.revoke_role(
                                    poison, actor_user_id=OWNER_ID,
                                    user_id=CODER_ID, role="coder",
                                    task_id=TASK_ID, conn=conn,
                                ), True)
                                next_value = 105
                            reconnect.assert_not_called()
                        self._s2mrw_assert_borrowed_success(conn)
                        self._s2mrw_assert_borrowed_cursors_closed(conn)
                        for attempt in self._s2mrw_borrowed_attempts(conn):
                            for record in self._s2mrw_classify_trace([attempt["sql"]]):
                                self.assertNotIn(record["kind"], {
                                    "ddl", "pragma", "txn", "attach", "other",
                                })
                        self.assertIs(conn, original_conn)
                        self.assertIs(conn.row_factory, sqlite3.Row)
                        self.assertTrue(conn.in_transaction)
                        self.assertFalse(caller_cursor.closed)
                        self.assertEqual(caller_cursor.fetchone()[0], next_value)
                        with closing(conn.execute(
                            "SELECT value FROM main.collaboration_settings WHERE key=?",
                            (marker,),
                        )) as cur:
                            self.assertEqual(cur.fetchone()[0], "caller-only-uncommitted")
            # Only the caller now ends its transaction, after all ownership
            # and external-cursor observations have already been asserted.
            conn.rollback()
        finally:
            self._s2mrw_close_probe(conn)
        self.assertEqual(self._s2mrw_observer_rows(
            self.store, "SELECT value FROM main.collaboration_settings WHERE key=?",
            (marker,),
        ), [])
        for sql, expected in (
            ("SELECT user_id,role,granted_by,granted_at FROM "
             "main.collaboration_role_grants ORDER BY user_id,role", before),
            ("SELECT user_id,screening_enabled,updated_by,updated_at FROM "
             "main.collaboration_member_settings ORDER BY user_id", settings_before),
            ("SELECT user_id,business_role,set_by,set_at FROM "
             "main.collaboration_business_role ORDER BY user_id", choices_before),
            ("SELECT id,user_id,old_role,new_role,set_by,set_at FROM "
             "main.collaboration_business_role_events ORDER BY id", events_before),
        ):
            self.assertEqual(self._s2mrw_observer_rows(self.store, sql), expected)

        # Preserve the existing comment-only classifier control.
        self.assertEqual(self._s2mrw_classify_trace((
            "-- INSERT INTO collaboration_role_grants VALUES(1)",
            "/* UPDATE collaboration_role_grants SET role='coder' */", "   ",
        )), [])

    def test_18_borrowed_owner_management_not_business_bypass(self):
        before = self.collab.resolve_business_role(
            self.store, user_id=OWNER_ID, task_id=TASK_ID
        )
        self.assertIs(before["is_owner"], True)
        self.assertIs(before["participant"], True)
        self.assertIsNone(before["role"])
        self.assertEqual(before["config"], "unconfigured")
        with self.assertRaises(self.collab.TeamRoleUnconfigured):
            self.collab.authorize_team_action(
                self.store, OWNER_ID, "coding:submit", task_id=TASK_ID
            )

        conn = self._s2mrw_open_probe(self.store)
        try:
            self._s2mrw_reset_probe(conn)
            self.collab.grant_role(
                self.store,
                actor_user_id=OWNER_ID,
                user_id=OWNER_ID,
                role="coder",
                task_id=TASK_ID,
                conn=conn,
            )
            self._s2mrw_assert_borrowed_success(conn)
            conn.commit()
        finally:
            self._s2mrw_close_probe(conn)

        after = self.collab.resolve_business_role(
            self.store, user_id=OWNER_ID, task_id=TASK_ID
        )
        self.assertIs(after["is_owner"], True)
        self.assertEqual(after["role"], "coder")
        self.assertEqual(after["config"], "ok")
        self.assertIs(after["required_screener"], True)
        self.assertIs(
            self.collab.authorize_team_action(
                self.store, OWNER_ID, "coding:submit", task_id=TASK_ID
            ),
            True,
        )
        with self.assertRaises(self.collab.TeamRoleDenied):
            self.collab.authorize_team_action(
                self.store, OWNER_ID, "consensus:write", task_id=TASK_ID
            )

    # ------------------------------------------------------------------
    # Increment fixtures: attempts journal, fault injection, observers
    # ------------------------------------------------------------------

    def _s2mrw_open_recording_probe(self, path, *, begin=True, read_only=False):
        # Same construction contract as _s2mrw_open_probe, with the
        # recording connection that keeps the attempts journal.  The frozen
        # _s2mrw_open_probe stays untouched; only the factory differs.
        path = Path(path)
        if read_only:
            uri = path.resolve().as_uri() + "?mode=ro"
            conn = sqlite3.connect(
                uri, uri=True, timeout=15, factory=_S2MRWRecordingConnection
            )
        else:
            conn = sqlite3.connect(
                str(path), timeout=15, factory=_S2MRWRecordingConnection
            )
        self._s2mrw_register_probe(conn)
        conn.row_factory = sqlite3.Row
        if begin:
            with closing(conn.execute("BEGIN" if read_only else "BEGIN IMMEDIATE")):
                pass
        return conn

    def _s2mrw_open_fault_probe(self, path, *, begin=True):
        # Recording probe that additionally supports the single-point
        # authorizer fault used by the grant-postcondition case.
        conn = sqlite3.connect(
            str(Path(path)), timeout=15, factory=_S2MRWFaultInjectionConnection
        )
        self._s2mrw_register_probe(conn)
        conn.row_factory = sqlite3.Row
        if begin:
            with closing(conn.execute("BEGIN IMMEDIATE")):
                pass
        return conn

    def _s2mrw_reset_recording_probe(self, probe):
        # The frozen _s2mrw_reset_probe cannot learn about the journal, so
        # this wrapper aligns the attempts offset first and then delegates.
        probe.attempt_offset = len(probe.attempts)
        self._s2mrw_reset_probe(probe)

    def _s2mrw_reset_fault_probe(self, probe):
        self._s2mrw_reset_recording_probe(probe)

    def _s2mrw_borrowed_attempts(self, probe):
        # Statements the writer actually tried since the last reset, with
        # bound parameters and per-statement outcome.  Unlike the trace
        # callback this includes statements that failed before running.
        return list(probe.attempts[probe.attempt_offset:])

    def _s2mrw_borrowed_explain_records(self, probe):
        # Diagnostic extraction of EXPLAIN-keyword attempts, when any exist.
        # The contract does not make EXPLAIN the required preflight
        # mechanism, so nothing derived here is load-bearing for pass/fail.
        records = []
        for attempt in self._s2mrw_borrowed_attempts(probe):
            classified = self._s2mrw_classify_trace([attempt["sql"]])
            if classified and classified[0]["kind"] == "preflight":
                records.append(dict(attempt))
        return records

    def _s2mrw_assert_attempts_no_business_dml(self, probe):
        # Attempts-side twin of _s2mrw_assert_no_business_dml.  A statement
        # that fails before running never reaches the trace callback, so
        # zero-DML evidence must also cover the journal.  Compile-failed
        # EXPLAIN attempts and metadata reads are allowed; a real business
        # DML attempted before the rejection is a contract violation.
        attempts = self._s2mrw_borrowed_attempts(probe)
        for attempt in attempts:
            self.assertIsNotNone(
                attempt["outcome"],
                "attempt journal left an unresolved record: " + attempt["sql"],
            )
            classified = self._s2mrw_classify_trace([attempt["sql"]])
            if classified:
                self.assertNotIn(
                    classified[0]["kind"],
                    {"dml", "ddl", "pragma", "txn", "attach", "other"},
                    "borrowed preflight attempted prohibited SQL: " + attempt["sql"],
                )
        return attempts

    def _s2mrw_assert_borrowed_cursors_closed(self, probe):
        # F-01: cursors the writer API actually received must be closed
        # before the caller commits, rolls back, or cleans up.  Registry
        # membership is the ownership proof; a statement whose execute fails
        # before the wrapper exists has no registered cursor and is
        # structurally outside this scope, not exempt from the contract.
        for cursor in probe.cursors[probe.cursor_offset:]:
            label = cursor.sql if cursor.sql else "<cursor()>"
            self.assertTrue(
                cursor.closed, "API-owned cursor left open: " + label
            )

    def _s2mrw_assert_writer_no_session_control(self, probe, *, expect_dml_tables):
        # F-03 discipline for mid-sequence failures.  The preceding business
        # writes are intentional real DML, so zero-DML assertions do not
        # apply.  What must hold instead: the writer performed no session
        # control (no commit/rollback/close/executescript, no DDL, no
        # PRAGMA, no SQL-level transaction or attach statements), the
        # transaction is still owned by the caller, the caller's connection
        # state (row_factory) is untouched, and the trace proves real DML
        # happened on every expected table before the failure.
        self.assertEqual(probe.commit_calls, 0)
        self.assertEqual(probe.rollback_calls, 0)
        self.assertEqual(probe.close_calls, 0)
        self.assertEqual(probe.executescript_calls, 0)
        self.assertTrue(probe.in_transaction)
        records = self._s2mrw_classify_trace(
            probe.statements[probe.statement_offset:]
        )
        for record in records:
            self.assertNotIn(
                record["kind"],
                {"ddl", "pragma", "txn", "attach", "other"},
                "borrowed writer issued prohibited SQL under failure: "
                + record["sql"],
            )
        seen = {}
        for record in records:
            if record["kind"] == "dml" and record["table"]:
                seen.setdefault(record["table"], record["sql"])
        for table in expect_dml_tables:
            self.assertIn(
                table,
                seen,
                "expected prior real DML on " + table + " before the failure",
            )
        # C02: the caller's connection state must survive the failure
        # untouched; asserted here so it is proven before the caller
        # rollback runs.
        self.assertIs(probe.row_factory, sqlite3.Row)

    def _s2mrw_temp_master_row(self, conn, name):
        # Empirical read-back of what SQLite actually stored for a TEMP
        # object; decides which NOCASE claim the evidence supports.
        with closing(
            conn.execute(
                "SELECT tbl_name FROM temp.sqlite_temp_master WHERE name = ?",
                (name,),
            )
        ) as cur:
            row = cur.fetchone()
        return None if row is None else row[0]

    def _s2mrw_observer_rows(self, path, sql, parameters=()):
        # Independent reader on its own connection; used after the caller
        # rollback to prove zero partial persistence on disk.
        with closing(sqlite3.connect(str(path), timeout=15)) as conn:
            with closing(conn.execute(sql, parameters)) as cur:
                return cur.fetchall()

    # ------------------------------------------------------------------
    # Increment: layout metadata boundary
    # ------------------------------------------------------------------

    def test_required_columns_and_safe_extensions(self):
        # FIX-R2 metadata boundary: safe extra columns and unrelated indexes
        # must stay compatible; a required extra NOT NULL column without a
        # default, a non-rowid-alias event key, and WITHOUT ROWID event
        # layouts must be rejected before any business DML.  Behavior and
        # boundaries are asserted; no statement text or mechanism is pinned.
        capability = sqlite3.connect(":memory:")
        try:
            capability.execute(
                "CREATE TABLE s2mrw_capability(a INTEGER, "
                "b TEXT GENERATED ALWAYS AS (a) VIRTUAL)"
            )
            generated_ok = True
        except sqlite3.OperationalError:
            generated_ok = False
        finally:
            capability.close()

        safe_variants = (
            (
                "safe-nullable-extra",
                ("ALTER TABLE collaboration_role_grants ADD COLUMN note TEXT",),
            ),
            (
                "safe-default-extra",
                (
                    "ALTER TABLE collaboration_role_grants "
                    "ADD COLUMN source TEXT DEFAULT 'import'",
                ),
            ),
            (
                "safe-default-null-extra",
                (
                    "ALTER TABLE collaboration_role_grants "
                    "ADD COLUMN note TEXT DEFAULT NULL",
                ),
            ),
            (
                "safe-unrelated-index",
                (
                    "CREATE INDEX idx_s2mrw_granted_at "
                    "ON collaboration_role_grants(granted_at)",
                ),
            ),
        )
        for name, statements in safe_variants:
            with self.subTest(variant=name):
                variant = self._s2mrw_minimal_schema(self.store, name, statements)
                conn = self._s2mrw_open_recording_probe(variant)
                try:
                    self._s2mrw_reset_recording_probe(conn)
                    result = self.collab.grant_role(
                        self.store,
                        actor_user_id=OWNER_ID,
                        user_id=NEW_UID,
                        role="coder",
                        task_id=TASK_ID,
                        conn=conn,
                    )
                    self.assertEqual(
                        set(result),
                        {"user_id", "role", "granted_by", "granted_at"},
                    )
                    self._s2mrw_assert_borrowed_success(conn)
                    self._s2mrw_assert_borrowed_cursors_closed(conn)
                    conn.rollback()
                finally:
                    self._s2mrw_close_probe(conn)

        if generated_ok:
            with self.subTest(variant="safe-generated-extra"):
                variant = self._s2mrw_minimal_schema(
                    self.store,
                    "safe-generated-extra",
                    (
                        "ALTER TABLE collaboration_role_grants ADD COLUMN "
                        "role_norm TEXT GENERATED ALWAYS AS (role) VIRTUAL",
                    ),
                )
                conn = self._s2mrw_open_recording_probe(variant)
                try:
                    self._s2mrw_reset_recording_probe(conn)
                    self.collab.grant_role(
                        self.store,
                        actor_user_id=OWNER_ID,
                        user_id=NEW_UID,
                        role="coder",
                        task_id=TASK_ID,
                        conn=conn,
                    )
                    self._s2mrw_assert_borrowed_success(conn)
                    self._s2mrw_assert_borrowed_cursors_closed(conn)
                    conn.rollback()
                finally:
                    self._s2mrw_close_probe(conn)
        else:
            # Conservative classification: the runtime cannot even build the
            # generated fixture, so there is no testable generated layout.
            # The pass condition is that every constructable safe layout is
            # accepted; nothing is asserted and nothing is skipped about
            # generated layouts, and no healthy layout may be required to be
            # rejected.
            with self.subTest(variant="generated-capability-unavailable"):
                combo = self._s2mrw_minimal_schema(
                    self.store,
                    "safe-combo-low-capability",
                    (
                        "ALTER TABLE collaboration_role_grants ADD COLUMN note TEXT",
                        "CREATE INDEX idx_s2mrw_combo "
                        "ON collaboration_role_grants(role)",
                    ),
                )
                conn = self._s2mrw_open_recording_probe(combo)
                try:
                    self._s2mrw_reset_recording_probe(conn)
                    self.collab.grant_role(
                        self.store,
                        actor_user_id=OWNER_ID,
                        user_id=NEW_UID,
                        role="coder",
                        task_id=TASK_ID,
                        conn=conn,
                    )
                    self._s2mrw_assert_borrowed_success(conn)
                    self._s2mrw_assert_borrowed_cursors_closed(conn)
                    conn.rollback()
                finally:
                    self._s2mrw_close_probe(conn)

        # Rebuild variants of the append-only events table must recreate the
        # two triggers verbatim, otherwise preflight rejects the layout for
        # the wrong reason and the metadata judgment is never reached.
        events_triggers = (
            "CREATE TRIGGER IF NOT EXISTS "
            "collaboration_business_role_events_no_update "
            "BEFORE UPDATE ON collaboration_business_role_events "
            "BEGIN SELECT RAISE(ABORT, "
            "'business role events are append-only'); END",
            "CREATE TRIGGER IF NOT EXISTS "
            "collaboration_business_role_events_no_delete "
            "BEFORE DELETE ON collaboration_business_role_events "
            "BEGIN SELECT RAISE(ABORT, "
            "'business role events are append-only'); END",
        )

        # Positive control: a table-constraint PRIMARY KEY on the events id
        # stays a rowid alias, so omitted ids are still auto-generated.
        with self.subTest(variant="events-table-constraint-pk"):
            variant = self._s2mrw_minimal_schema(
                self.store,
                "events-table-constraint-pk",
                (
                    "ALTER TABLE collaboration_business_role_events "
                    "RENAME TO _s2mrw_events_old",
                    "CREATE TABLE collaboration_business_role_events ("
                    "id INTEGER, "
                    "user_id INTEGER NOT NULL, "
                    "old_role TEXT NOT NULL, "
                    "new_role TEXT NOT NULL, "
                    "set_by INTEGER NOT NULL, "
                    "set_at TEXT NOT NULL, "
                    "PRIMARY KEY(id DESC))",
                    "INSERT INTO collaboration_business_role_events"
                    "(id, user_id, old_role, new_role, set_by, set_at) "
                    "SELECT id, user_id, old_role, new_role, set_by, set_at "
                    "FROM _s2mrw_events_old",
                    "DROP TABLE _s2mrw_events_old",
                )
                + events_triggers,
            )
            conn = self._s2mrw_open_recording_probe(variant)
            try:
                # The old table owned the two reserved trigger names until it
                # was dropped, so the CREATE TRIGGER IF NOT EXISTS statements
                # above would have been silent no-ops had they run earlier;
                # verify in sqlite_master that both triggers now truly exist
                # and are attached to the rebuilt table with the unweakened
                # append-only contract.
                with closing(
                    conn.execute(
                        "SELECT name, tbl_name, sql FROM main.sqlite_master "
                        "WHERE type='trigger' AND name IN (?,?)",
                        (
                            "collaboration_business_role_events_no_update",
                            "collaboration_business_role_events_no_delete",
                        ),
                    )
                ) as cur:
                    attached = cur.fetchall()
                self.assertEqual(len(attached), 2)
                self.assertEqual(
                    {row["name"] for row in attached},
                    {
                        "collaboration_business_role_events_no_update",
                        "collaboration_business_role_events_no_delete",
                    },
                )
                for row in attached:
                    self.assertEqual(
                        row["tbl_name"],
                        "collaboration_business_role_events",
                    )
                    self.assertIn(
                        "business role events are append-only", row["sql"]
                    )
                    if row["name"] == (
                        "collaboration_business_role_events_no_update"
                    ):
                        self.assertIn("BEFORE UPDATE ON", row["sql"])
                    else:
                        self.assertIn("BEFORE DELETE ON", row["sql"])
                self._s2mrw_reset_recording_probe(conn)
                self.collab.set_business_role(
                    self.store,
                    actor_user_id=OWNER_ID,
                    user_id=CODER_ID,
                    business_role="reconciler",
                    task_id=TASK_ID,
                    conn=conn,
                )
                self._s2mrw_assert_borrowed_success(conn)
                self._s2mrw_assert_borrowed_cursors_closed(conn)
                with closing(
                    conn.execute(
                        "SELECT id FROM main.collaboration_business_role_events "
                        "WHERE user_id=?",
                        (CODER_ID,),
                    )
                ) as cur:
                    new_event_ids = [row[0] for row in cur.fetchall()]
                self.assertEqual(len(new_event_ids), 1)
                self.assertIsNotNone(new_event_ids[0])
                conn.rollback()
            finally:
                self._s2mrw_close_probe(conn)

        reject_variants = (
            (
                "extra-notnull-no-default",
                (
                    "ALTER TABLE collaboration_role_grants "
                    "RENAME TO _s2mrw_grants_old",
                    "CREATE TABLE collaboration_role_grants ("
                    "user_id INTEGER NOT NULL CHECK(user_id > 0), "
                    "role TEXT NOT NULL CHECK(role IN ('coder', 'reconciler')), "
                    "granted_by INTEGER NOT NULL CHECK(granted_by > 0), "
                    "granted_at TEXT NOT NULL, "
                    "audit_by INTEGER NOT NULL, "
                    "PRIMARY KEY(user_id, role))",
                    "INSERT INTO collaboration_role_grants"
                    "(user_id, role, granted_by, granted_at, audit_by) "
                    "SELECT user_id, role, granted_by, granted_at, granted_by "
                    "FROM _s2mrw_grants_old",
                    "DROP TABLE _s2mrw_grants_old",
                ),
                lambda conn: self.collab.grant_role(
                    self.store,
                    actor_user_id=OWNER_ID,
                    user_id=NEW_UID,
                    role="coder",
                    task_id=TASK_ID,
                    conn=conn,
                ),
            ),
            (
                "events-inline-pk-desc",
                (
                    "ALTER TABLE collaboration_business_role_events "
                    "RENAME TO _s2mrw_events_old",
                    "CREATE TABLE collaboration_business_role_events ("
                    "id INTEGER PRIMARY KEY DESC, "
                    "user_id INTEGER NOT NULL, "
                    "old_role TEXT NOT NULL, "
                    "new_role TEXT NOT NULL, "
                    "set_by INTEGER NOT NULL, "
                    "set_at TEXT NOT NULL)",
                    "INSERT INTO collaboration_business_role_events"
                    "(id, user_id, old_role, new_role, set_by, set_at) "
                    "SELECT id, user_id, old_role, new_role, set_by, set_at "
                    "FROM _s2mrw_events_old",
                )
                + events_triggers
                + ("DROP TABLE _s2mrw_events_old",),
                lambda conn: self.collab.set_business_role(
                    self.store,
                    actor_user_id=OWNER_ID,
                    user_id=CODER_ID,
                    business_role="reconciler",
                    task_id=TASK_ID,
                    conn=conn,
                ),
            ),
            (
                "events-without-rowid",
                (
                    "ALTER TABLE collaboration_business_role_events "
                    "RENAME TO _s2mrw_events_old",
                    "CREATE TABLE collaboration_business_role_events ("
                    "id INTEGER NOT NULL, "
                    "user_id INTEGER NOT NULL, "
                    "old_role TEXT NOT NULL, "
                    "new_role TEXT NOT NULL, "
                    "set_by INTEGER NOT NULL, "
                    "set_at TEXT NOT NULL, "
                    "PRIMARY KEY(id)) WITHOUT ROWID",
                    "INSERT INTO collaboration_business_role_events"
                    "(id, user_id, old_role, new_role, set_by, set_at) "
                    "SELECT id, user_id, old_role, new_role, set_by, set_at "
                    "FROM _s2mrw_events_old",
                )
                + events_triggers
                + ("DROP TABLE _s2mrw_events_old",),
                lambda conn: self.collab.set_business_role(
                    self.store,
                    actor_user_id=OWNER_ID,
                    user_id=CODER_ID,
                    business_role="reconciler",
                    task_id=TASK_ID,
                    conn=conn,
                ),
            ),
        )
        for name, statements, call in reject_variants:
            with self.subTest(variant=name):
                variant = self._s2mrw_minimal_schema(self.store, name, statements)
                before = self._s2mrw_collaboration_row_counts(variant)
                conn = self._s2mrw_open_recording_probe(variant)
                try:
                    self._s2mrw_reset_recording_probe(conn)
                    with self.assertRaises(RuntimeError):
                        call(conn)
                    self._s2mrw_assert_attempts_no_business_dml(conn)
                    self._s2mrw_assert_no_business_dml(conn)
                    self._s2mrw_assert_borrowed_cursors_closed(conn)
                    self._s2mrw_assert_counts_unchanged(variant, before)
                    conn.rollback()
                finally:
                    self._s2mrw_close_probe(conn)

    # ------------------------------------------------------------------
    # Increment: writer profiles and default-path compatibility
    # ------------------------------------------------------------------

    def test_operation_profiles_and_default_compatibility(self):
        # Profiles stay narrow: a missing unrelated table never blocks coder
        # revoke, and DELETE never inherits INSERT-only column rules.  TEMP
        # shadowing covers VIEW and attachment-name shapes with an honest
        # storage-shape record.  Owner screening configuration gets its
        # positive control.  The default path keeps legacy self-heal,
        # migration timing, and exception mapping and proves it does not run
        # borrowed preflight.
        member_dropped = self._s2mrw_minimal_schema(
            self.store,
            "profile-missing-member",
            ("DROP TABLE collaboration_member_settings",),
        )
        before = self._s2mrw_collaboration_row_counts(member_dropped)

        # A1a: coder revoke must not require the unrelated member table.
        with self.subTest(case="A1a-coder-revoke-narrow-profile"):
            conn = self._s2mrw_open_recording_probe(member_dropped)
            try:
                self._s2mrw_reset_recording_probe(conn)
                self.assertIs(
                    self.collab.revoke_role(
                        self.store,
                        actor_user_id=OWNER_ID,
                        user_id=CODER_ID,
                        role="coder",
                        task_id=TASK_ID,
                        conn=conn,
                    ),
                    True,
                )
                self._s2mrw_assert_borrowed_success(conn)
                self._s2mrw_assert_borrowed_cursors_closed(conn)
                conn.rollback()
            finally:
                self._s2mrw_close_probe(conn)

        # A1b: reconciler revoke legitimately needs member settings.
        with self.subTest(case="A1b-reconciler-revoke-needs-member"):
            conn = self._s2mrw_open_recording_probe(member_dropped)
            try:
                self._s2mrw_reset_recording_probe(conn)
                with self.assertRaises(RuntimeError):
                    self.collab.revoke_role(
                        self.store,
                        actor_user_id=OWNER_ID,
                        user_id=RECON_ID,
                        role="reconciler",
                        task_id=TASK_ID,
                        conn=conn,
                    )
                self._s2mrw_assert_attempts_no_business_dml(conn)
                self._s2mrw_assert_no_business_dml(conn)
                self._s2mrw_assert_borrowed_cursors_closed(conn)
                self._s2mrw_assert_counts_unchanged(member_dropped, before)
                conn.rollback()
            finally:
                self._s2mrw_close_probe(conn)

        # A1c: dropping the business tables too still leaves coder revoke
        # working; unrelated missing objects never widen the profile.
        wider = self._s2mrw_minimal_schema(
            member_dropped,
            "profile-missing-member-and-business",
            (
                "DROP TABLE collaboration_business_role",
                "DROP TABLE collaboration_business_role_events",
            ),
        )
        before = self._s2mrw_collaboration_row_counts(wider)
        with self.subTest(case="A1c-coder-revoke-ignores-business"):
            conn = self._s2mrw_open_recording_probe(wider)
            try:
                self._s2mrw_reset_recording_probe(conn)
                self.assertIs(
                    self.collab.revoke_role(
                        self.store,
                        actor_user_id=OWNER_ID,
                        user_id=CODER_ID,
                        role="coder",
                        task_id=TASK_ID,
                        conn=conn,
                    ),
                    True,
                )
                self._s2mrw_assert_borrowed_success(conn)
                self._s2mrw_assert_borrowed_cursors_closed(conn)
                self._s2mrw_assert_counts_unchanged(wider, before)
                conn.rollback()
            finally:
                self._s2mrw_close_probe(conn)

        # A2: the same extra-NOT-NULL layout rejects INSERT but not DELETE.
        insert_only = self._s2mrw_minimal_schema(
            self.store,
            "profile-delete-vs-insert",
            (
                "ALTER TABLE collaboration_role_grants "
                "RENAME TO _s2mrw_grants_old",
                "CREATE TABLE collaboration_role_grants ("
                "user_id INTEGER NOT NULL CHECK(user_id > 0), "
                "role TEXT NOT NULL CHECK(role IN ('coder', 'reconciler')), "
                "granted_by INTEGER NOT NULL CHECK(granted_by > 0), "
                "granted_at TEXT NOT NULL, "
                "audit_by INTEGER NOT NULL, "
                "PRIMARY KEY(user_id, role))",
                "INSERT INTO collaboration_role_grants"
                "(user_id, role, granted_by, granted_at, audit_by) "
                "SELECT user_id, role, granted_by, granted_at, granted_by "
                "FROM _s2mrw_grants_old",
                "DROP TABLE _s2mrw_grants_old",
            ),
        )
        before = self._s2mrw_collaboration_row_counts(insert_only)
        with self.subTest(case="A2-delete-not-insert-rules"):
            conn = self._s2mrw_open_recording_probe(insert_only)
            try:
                self._s2mrw_reset_recording_probe(conn)
                with self.assertRaises(RuntimeError):
                    self.collab.grant_role(
                        self.store,
                        actor_user_id=OWNER_ID,
                        user_id=NEW_UID,
                        role="coder",
                        task_id=TASK_ID,
                        conn=conn,
                    )
                self._s2mrw_assert_attempts_no_business_dml(conn)
                self._s2mrw_assert_no_business_dml(conn)
                self._s2mrw_assert_borrowed_cursors_closed(conn)
                self._s2mrw_assert_counts_unchanged(insert_only, before)
                self._s2mrw_reset_recording_probe(conn)
                self.assertIs(
                    self.collab.revoke_role(
                        self.store,
                        actor_user_id=OWNER_ID,
                        user_id=CODER_ID,
                        role="coder",
                        task_id=TASK_ID,
                        conn=conn,
                    ),
                    True,
                )
                self._s2mrw_assert_borrowed_success(conn)
                self._s2mrw_assert_borrowed_cursors_closed(conn)
                conn.rollback()
            finally:
                self._s2mrw_close_probe(conn)

        # A3: a TEMP VIEW carrying a reserved name is shadowed as well.
        with self.subTest(case="A3-temp-view-reserved-name"):
            conn = self._s2mrw_open_recording_probe(self.store)
            try:
                with closing(
                    conn.execute(
                        "CREATE TEMP VIEW COLLABORATION_MEMBER_SETTINGS "
                        "AS SELECT 1"
                    )
                ):
                    pass
                self._s2mrw_reset_recording_probe(conn)
                with self.assertRaises(RuntimeError):
                    self.collab.set_member_screening(
                        self.store,
                        actor_user_id=OWNER_ID,
                        user_id=RECON_ID,
                        enabled=False,
                        task_id=TASK_ID,
                        conn=conn,
                    )
                self._s2mrw_assert_attempts_no_business_dml(conn)
                self._s2mrw_assert_no_business_dml(conn)
                self._s2mrw_assert_borrowed_cursors_closed(conn)
                conn.rollback()
            finally:
                self._s2mrw_close_probe(conn)

        # A7: owner screening configuration positive control.  The owner's
        # main business choice is legitimate without any owner grant, and a
        # "always reject owner targets" regression must fail here.
        owner_choice = self.collab.set_business_role(
            self.store,
            actor_user_id=OWNER_ID,
            user_id=OWNER_ID,
            business_role="reconciler",
            task_id=TASK_ID,
        )
        self.assertIs(owner_choice["is_owner"], True)
        owner_grants = self._s2mrw_observer_rows(
            self.store,
            "SELECT COUNT(*) FROM main.collaboration_role_grants "
            "WHERE user_id=?",
            (OWNER_ID,),
        )
        self.assertEqual(owner_grants[0][0], 0, "owner must not receive a grant")
        with self.subTest(case="A7-owner-screening-borrowed"):
            conn = self._s2mrw_open_recording_probe(self.store)
            try:
                self._s2mrw_reset_recording_probe(conn)
                result = self.collab.set_member_screening(
                    self.store,
                    actor_user_id=OWNER_ID,
                    user_id=OWNER_ID,
                    enabled=False,
                    task_id=TASK_ID,
                    conn=conn,
                )
                self.assertEqual(
                    set(result),
                    {
                        "user_id",
                        "screening_enabled",
                        "updated_by",
                        "updated_at",
                    },
                )
                self.assertIs(result["screening_enabled"], False)
                self._s2mrw_assert_borrowed_success(conn)
                self._s2mrw_assert_borrowed_cursors_closed(conn)
                with closing(
                    conn.execute(
                        "SELECT screening_enabled "
                        "FROM main.collaboration_member_settings "
                        "WHERE user_id=?",
                        (OWNER_ID,),
                    )
                ) as cur:
                    self.assertEqual(
                        [row[0] for row in cur.fetchall()], [0]
                    )
                conn.commit()
            finally:
                self._s2mrw_close_probe(conn)
        persisted = self._s2mrw_observer_rows(
            self.store,
            "SELECT screening_enabled "
            "FROM main.collaboration_member_settings WHERE user_id=?",
            (OWNER_ID,),
        )
        self.assertEqual([row[0] for row in persisted], [0])
        default_owner = self.collab.set_member_screening(
            self.store,
            actor_user_id=OWNER_ID,
            user_id=OWNER_ID,
            enabled=True,
            task_id=TASK_ID,
        )
        self.assertIs(default_owner["screening_enabled"], True)
        persisted = self._s2mrw_observer_rows(
            self.store,
            "SELECT screening_enabled "
            "FROM main.collaboration_member_settings WHERE user_id=?",
            (OWNER_ID,),
        )
        self.assertEqual([row[0] for row in persisted], [1])

        # A8: attachment-name shadowing.  The stored tbl_name is read back
        # BEFORE the writer call; NOCASE branch-kill claims are made only in
        # the storage shapes the read-back actually proves, otherwise the
        # evidence limitation is recorded instead of a claim.
        for name, target in (
            ("temp-tblname-bare", "COLLABORATION_BUSINESS_ROLE_EVENTS"),
            ("temp-tblname-qualified", "main.COLLABORATION_BUSINESS_ROLE_EVENTS"),
        ):
            with self.subTest(variant=name):
                before = self._s2mrw_collaboration_row_counts(self.store)
                conn = self._s2mrw_open_recording_probe(self.store)
                try:
                    with closing(
                        conn.execute(
                            "CREATE TEMP TRIGGER s2mrw_side_trg BEFORE INSERT ON "
                            + target
                            + " BEGIN SELECT RAISE(ABORT, 'tbl_name shadow'); END"
                        )
                    ):
                        pass
                    stored = self._s2mrw_temp_master_row(conn, "s2mrw_side_trg")
                    reserved = "collaboration_business_role_events"
                    if stored is None:
                        claim = (
                            "evidence-limitation: trigger row not found in "
                            "temp.sqlite_temp_master; NOCASE branch kill "
                            "not claimed"
                        )
                    else:
                        qualified = stored.lower().startswith("main.")
                        base = stored[5:] if qualified else stored
                        if base.lower() != reserved:
                            claim = (
                                "evidence-limitation: stored tbl_name differs "
                                "beyond ASCII case: " + stored
                            )
                        elif base != reserved and qualified:
                            claim = (
                                "storage shape: main.-qualified "
                                "ASCII-case-variant tbl_name; prefix-aware "
                                "tbl_name NOCASE kill claimed"
                            )
                        elif base != reserved:
                            claim = (
                                "storage shape: ASCII-case-variant tbl_name; "
                                "tbl_name NOCASE kill claimed"
                            )
                        else:
                            claim = (
                                "storage shape: normalized tbl_name identical "
                                "to reserved name"
                                + (" (main.-qualified)" if qualified else "")
                                + "; guard regression only, "
                                "NOCASE branch kill NOT claimed"
                            )
                    self._s2mrw_reset_recording_probe(conn)
                    with self.subTest(tbl_name_storage=claim):
                        with self.assertRaises(RuntimeError):
                            self.collab.set_business_role(
                                self.store,
                                actor_user_id=OWNER_ID,
                                user_id=CODER_ID,
                                business_role="reconciler",
                                task_id=TASK_ID,
                                conn=conn,
                            )
                        self._s2mrw_assert_attempts_no_business_dml(conn)
                        self._s2mrw_assert_no_business_dml(conn)
                        self._s2mrw_assert_borrowed_cursors_closed(conn)
                        self._s2mrw_assert_counts_unchanged(self.store, before)
                        conn.rollback()
                finally:
                    self._s2mrw_close_probe(conn)

        # A9: a trigger attached to an unrelated TEMP object never blocks
        # the borrowed path; the guard must not over-reach.
        with self.subTest(case="A9-unrelated-temp-attachment"):
            conn = self._s2mrw_open_recording_probe(self.store)
            try:
                with closing(
                    conn.execute("CREATE TEMP TABLE s2mrw_stage(value INTEGER)")
                ):
                    pass
                with closing(
                    conn.execute(
                        "CREATE TEMP TRIGGER s2mrw_stage_trg "
                        "BEFORE INSERT ON s2mrw_stage "
                        "BEGIN SELECT RAISE(ABORT, 'stage'); END"
                    )
                ):
                    pass
                self._s2mrw_reset_recording_probe(conn)
                self.collab.grant_role(
                    self.store,
                    actor_user_id=OWNER_ID,
                    user_id=NEW_UID,
                    role="coder",
                    task_id=TASK_ID,
                    conn=conn,
                )
                self._s2mrw_assert_borrowed_success(conn)
                self._s2mrw_assert_borrowed_cursors_closed(conn)
                conn.rollback()
            finally:
                self._s2mrw_close_probe(conn)

        # A4: default grant keeps legacy migration timing; a deleted marker
        # is re-created as a full UTC timestamp.  Three input states are
        # prepared and self-evidenced before the call so the backfill
        # expectations each get their own positive evidence: an existing
        # reconciler with a settings row at 0 that the INSERT OR IGNORE
        # backfill must preserve, an old reconciler grant whose settings row
        # is missing and that the backfill must set to 1, and a user with no
        # grant at all.  The default write itself is a fresh reconciler
        # grant, which must really be created and land at the post-upgrade
        # default 0.
        marker = self.collab.SCREENING_BACKFILL_KEY
        self.collab.grant_role(
            self.store,
            actor_user_id=OWNER_ID,
            user_id=OTHER_UID,
            role="reconciler",
            task_id=TASK_ID,
        )
        deletion = self._s2mrw_minimal_schema(
            self.store,
            "default-migration-marker-deleted",
            (
                "DELETE FROM collaboration_settings WHERE key='" + marker + "'",
                "DELETE FROM collaboration_member_settings "
                "WHERE user_id=" + str(OTHER_UID),
                "CREATE TRIGGER s2mrw_a4_marker_order_guard "
                "BEFORE INSERT ON collaboration_role_grants "
                "WHEN NEW.user_id=" + str(NEW_UID)
                + " AND NEW.role='reconciler' "
                + "AND NOT EXISTS (SELECT 1 FROM collaboration_settings "
                + "WHERE key='" + marker + "') "
                + "BEGIN SELECT RAISE(ABORT, "
                + "'A4 marker must exist before new reconciler grant'); END",
            ),
        )
        with self.subTest(case="A4-default-migration-timing"):
            # Input states, measured not assumed: existing 0 row present,
            # old grant present with its settings row missing, no rows for
            # the future grant target, and the marker row truly gone.
            recon_before = self._s2mrw_observer_rows(
                deletion,
                "SELECT screening_enabled "
                "FROM main.collaboration_member_settings WHERE user_id=?",
                (RECON_ID,),
            )
            self.assertEqual([row[0] for row in recon_before], [0])
            other_grants = self._s2mrw_observer_rows(
                deletion,
                "SELECT COUNT(*) FROM main.collaboration_role_grants "
                "WHERE user_id=? AND role='reconciler'",
                (OTHER_UID,),
            )
            self.assertEqual(other_grants[0][0], 1)
            other_settings = self._s2mrw_observer_rows(
                deletion,
                "SELECT COUNT(*) "
                "FROM main.collaboration_member_settings WHERE user_id=?",
                (OTHER_UID,),
            )
            self.assertEqual(other_settings[0][0], 0)
            new_grants_before = self._s2mrw_observer_rows(
                deletion,
                "SELECT COUNT(*) FROM main.collaboration_role_grants "
                "WHERE user_id=?",
                (NEW_UID,),
            )
            self.assertEqual(new_grants_before[0][0], 0)
            marker_before = self._s2mrw_observer_rows(
                deletion,
                "SELECT value FROM main.collaboration_settings WHERE key=?",
                (marker,),
            )
            self.assertEqual(marker_before, [])
            marker_order_guard = self._s2mrw_observer_rows(
                deletion,
                "SELECT tbl_name, sql FROM main.sqlite_master "
                "WHERE type='trigger' AND name=?",
                ("s2mrw_a4_marker_order_guard",),
            )
            self.assertEqual(len(marker_order_guard), 1)
            self.assertEqual(
                marker_order_guard[0][0], "collaboration_role_grants"
            )
            guard_sql = marker_order_guard[0][1]
            for guard_fragment in (
                "BEFORE INSERT ON collaboration_role_grants",
                "NEW.user_id=" + str(NEW_UID),
                "NEW.role='reconciler'",
                "NOT EXISTS (SELECT 1 FROM collaboration_settings",
                "WHERE key='" + marker + "'",
                "RAISE(ABORT",
            ):
                self.assertIn(guard_fragment, guard_sql)
            result = self.collab.grant_role(
                deletion,
                actor_user_id=OWNER_ID,
                user_id=NEW_UID,
                role="reconciler",
                task_id=TASK_ID,
            )
            self.assertEqual(
                set(result), {"user_id", "role", "granted_by", "granted_at"}
            )
            self.assertEqual(result["role"], "reconciler")
            marker_value = self._s2mrw_observer_rows(
                deletion,
                "SELECT value FROM main.collaboration_settings WHERE key=?",
                (marker,),
            )
            self.assertEqual(len(marker_value), 1)
            self.assertRegex(
                marker_value[0][0],
                r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}"
                r"(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$",
            )
            # Expected three-state outcome: the existing 0 stays 0, the old
            # grant with the missing row is backfilled to 1, and the brand
            # new reconciler grant exists with the post-upgrade default 0.
            recon_settings = self._s2mrw_observer_rows(
                deletion,
                "SELECT screening_enabled "
                "FROM main.collaboration_member_settings WHERE user_id=?",
                (RECON_ID,),
            )
            self.assertEqual([row[0] for row in recon_settings], [0])
            other_settings = self._s2mrw_observer_rows(
                deletion,
                "SELECT screening_enabled "
                "FROM main.collaboration_member_settings WHERE user_id=?",
                (OTHER_UID,),
            )
            self.assertEqual([row[0] for row in other_settings], [1])
            new_settings = self._s2mrw_observer_rows(
                deletion,
                "SELECT screening_enabled "
                "FROM main.collaboration_member_settings WHERE user_id=?",
                (NEW_UID,),
            )
            self.assertEqual([row[0] for row in new_settings], [0])
            new_grants_after = self._s2mrw_observer_rows(
                deletion,
                "SELECT COUNT(*) FROM main.collaboration_role_grants "
                "WHERE user_id=? AND role='reconciler'",
                (NEW_UID,),
            )
            self.assertEqual(new_grants_after[0][0], 1)

        # A5: default repeat grant is idempotent and never resets the switch.
        self.collab.set_member_screening(
            self.store,
            actor_user_id=OWNER_ID,
            user_id=RECON_ID,
            enabled=True,
            task_id=TASK_ID,
        )
        original = self._s2mrw_observer_rows(
            self.store,
            "SELECT granted_at FROM main.collaboration_role_grants "
            "WHERE user_id=? AND role='reconciler'",
            (RECON_ID,),
        )
        self.assertEqual(len(original), 1)
        with self.subTest(case="A5-default-repeat-grant-no-reset"):
            result = self.collab.grant_role(
                self.store,
                actor_user_id=OWNER_ID,
                user_id=RECON_ID,
                role="reconciler",
                task_id=TASK_ID,
            )
            self.assertEqual(
                set(result), {"user_id", "role", "granted_by", "granted_at"}
            )
            self.assertEqual(result["granted_at"], original[0][0])
            recon_settings = self._s2mrw_observer_rows(
                self.store,
                "SELECT screening_enabled "
                "FROM main.collaboration_member_settings WHERE user_id=?",
                (RECON_ID,),
            )
            self.assertEqual([row[0] for row in recon_settings], [1])
            grants_count = self._s2mrw_observer_rows(
                self.store,
                "SELECT COUNT(*) FROM main.collaboration_role_grants "
                "WHERE user_id=? AND role='reconciler'",
                (RECON_ID,),
            )
            self.assertEqual(grants_count[0][0], 1)

        # A6/D3: dropped business tables self-heal on the default path while
        # the borrowed path fail-closes.  Default and borrowed each get an
        # independent missing-table copy so neither call's outcome can mask
        # the other; absence before the call and repair after it are both
        # measured, never assumed.
        dropped_default = self._s2mrw_minimal_schema(
            self.store,
            "default-self-heal-business-dropped",
            (
                "DROP TABLE collaboration_business_role",
                "DROP TABLE collaboration_business_role_events",
            ),
        )
        dropped_borrowed = self._s2mrw_minimal_schema(
            self.store,
            "borrowed-business-dropped",
            (
                "DROP TABLE collaboration_business_role",
                "DROP TABLE collaboration_business_role_events",
            ),
        )
        with self.subTest(case="A6-default-self-heal-vs-borrowed-closed"):
            pre_default = self._s2mrw_collaboration_row_counts(dropped_default)
            self.assertIsNone(pre_default["collaboration_business_role"])
            self.assertIsNone(pre_default["collaboration_business_role_events"])
            result = self.collab.set_business_role(
                dropped_default,
                actor_user_id=OWNER_ID,
                user_id=RECON_ID,
                business_role="reconciler",
                task_id=TASK_ID,
            )
            self.assertEqual(
                set(result),
                {
                    "user_id",
                    "business_role",
                    "previous_role",
                    "previous_config",
                    "is_owner",
                    "set_by",
                    "changed_at",
                },
            )
            healed = self._s2mrw_collaboration_row_counts(dropped_default)
            self.assertIsNotNone(healed["collaboration_business_role"])
            self.assertIsNotNone(healed["collaboration_business_role_events"])
            healed_choice = self._s2mrw_observer_rows(
                dropped_default,
                "SELECT business_role FROM main.collaboration_business_role "
                "WHERE user_id=?",
                (RECON_ID,),
            )
            self.assertEqual([row[0] for row in healed_choice], ["reconciler"])
            self.assertIs(
                self.collab.revoke_role(
                    dropped_default,
                    actor_user_id=OWNER_ID,
                    user_id=CODER_ID,
                    role="coder",
                    task_id=TASK_ID,
                ),
                True,
            )
            before = self._s2mrw_collaboration_row_counts(dropped_borrowed)
            self.assertIsNone(before["collaboration_business_role"])
            self.assertIsNone(before["collaboration_business_role_events"])
            conn = self._s2mrw_open_recording_probe(dropped_borrowed)
            try:
                self._s2mrw_reset_recording_probe(conn)
                with self.assertRaises(RuntimeError):
                    self.collab.set_business_role(
                        dropped_borrowed,
                        actor_user_id=OWNER_ID,
                        user_id=CODER_ID,
                        business_role="reconciler",
                        task_id=TASK_ID,
                        conn=conn,
                    )
                self._s2mrw_assert_attempts_no_business_dml(conn)
                self._s2mrw_assert_no_business_dml(conn)
                self._s2mrw_assert_borrowed_cursors_closed(conn)
                self._s2mrw_assert_counts_unchanged(dropped_borrowed, before)
                conn.rollback()
            finally:
                self._s2mrw_close_probe(conn)

        # D1/D2: default-path store-level exception boundaries for the two
        # writers the original suite did not cover.
        garbage_dir = self.root / "profile-garbage-store"
        garbage_dir.mkdir()
        garbage = garbage_dir / "collaboration.db"
        garbage.write_bytes(b"this is not a sqlite database")
        for writer, call in (
            (
                "set_business_role",
                lambda: self.collab.set_business_role(
                    garbage,
                    actor_user_id=OWNER_ID,
                    user_id=CODER_ID,
                    business_role="reconciler",
                    task_id=TASK_ID,
                ),
            ),
            (
                "revoke_role",
                lambda: self.collab.revoke_role(
                    garbage,
                    actor_user_id=OWNER_ID,
                    user_id=CODER_ID,
                    role="coder",
                    task_id=TASK_ID,
                ),
            ),
        ):
            with self.subTest(garbage_writer=writer):
                with self.assertRaises(sqlite3.DatabaseError) as exc:
                    call()
                self.assertNotIsInstance(
                    exc.exception, self.collab.OwnerUnavailableError
                )
        missing = self.root / "profile-missing-store" / "collaboration.db"
        for writer, call in (
            (
                "set_business_role",
                lambda: self.collab.set_business_role(
                    missing,
                    actor_user_id=OWNER_ID,
                    user_id=CODER_ID,
                    business_role="reconciler",
                    task_id=TASK_ID,
                ),
            ),
            (
                "revoke_role",
                lambda: self.collab.revoke_role(
                    missing,
                    actor_user_id=OWNER_ID,
                    user_id=CODER_ID,
                    role="coder",
                    task_id=TASK_ID,
                ),
            ),
        ):
            with self.subTest(missing_writer=writer):
                with self.assertRaises(ValueError):
                    call()

    # ------------------------------------------------------------------
    # Increment: grant conflict target and postcondition
    # ------------------------------------------------------------------

    def test_targeted_conflict_and_grant_postcondition(self):
        # Repeat grants are idempotent (no duplicate row, no switch reset),
        # a missing conflict target is rejected before any business DML by
        # whatever reliable read-only preflight the implementation uses
        # (metadata or compilation; EXPLAIN is never required to appear and
        # no statement text is pinned), non-conflict constraint errors
        # propagate natively, and a grant that silently materialized zero
        # rows must never be reported as success.
        self.collab.set_member_screening(
            self.store,
            actor_user_id=OWNER_ID,
            user_id=RECON_ID,
            enabled=True,
            task_id=TASK_ID,
        )
        original = self._s2mrw_observer_rows(
            self.store,
            "SELECT granted_at FROM main.collaboration_role_grants "
            "WHERE user_id=? AND role='reconciler'",
            (RECON_ID,),
        )
        self.assertEqual(len(original), 1)

        # B1: borrowed repeat grant keeps the original row and the switch.
        with self.subTest(case="B1-repeat-grant-idempotent"):
            conn = self._s2mrw_open_recording_probe(self.store)
            try:
                self._s2mrw_reset_recording_probe(conn)
                result = self.collab.grant_role(
                    self.store,
                    actor_user_id=OWNER_ID,
                    user_id=RECON_ID,
                    role="reconciler",
                    task_id=TASK_ID,
                    conn=conn,
                )
                self.assertEqual(
                    set(result),
                    {"user_id", "role", "granted_by", "granted_at"},
                )
                self.assertEqual(result["granted_at"], original[0][0])
                self._s2mrw_assert_borrowed_success(conn)
                self._s2mrw_assert_borrowed_cursors_closed(conn)
                with closing(
                    conn.execute(
                        "SELECT COUNT(*) FROM main.collaboration_role_grants "
                        "WHERE user_id=? AND role='reconciler'",
                        (RECON_ID,),
                    )
                ) as cur:
                    self.assertEqual(cur.fetchone()[0], 1)
                with closing(
                    conn.execute(
                        "SELECT screening_enabled "
                        "FROM main.collaboration_member_settings "
                        "WHERE user_id=?",
                        (RECON_ID,),
                    )
                ) as cur:
                    self.assertEqual(
                        [row[0] for row in cur.fetchall()], [1]
                    )
                conn.commit()
            finally:
                self._s2mrw_close_probe(conn)
            persisted = self._s2mrw_observer_rows(
                self.store,
                "SELECT COUNT(*) FROM main.collaboration_role_grants "
                "WHERE user_id=? AND role='reconciler'",
                (RECON_ID,),
            )
            self.assertEqual(persisted[0][0], 1)
            persisted = self._s2mrw_observer_rows(
                self.store,
                "SELECT screening_enabled "
                "FROM main.collaboration_member_settings WHERE user_id=?",
                (RECON_ID,),
            )
            self.assertEqual([row[0] for row in persisted], [1])

        # F5a: without the (user_id, role) unique target the write must be
        # rejected before any business DML, with the journal covering
        # statements that failed before running.  The trailing diagnostic
        # records how many EXPLAIN attempts occurred but is intentionally
        # not asserted: the rejection mechanism is not pinned.
        missing_target = self._s2mrw_minimal_schema(
            self.store,
            "grants-no-unique-target",
            (
                "ALTER TABLE collaboration_role_grants "
                "RENAME TO _s2mrw_grants_old",
                "CREATE TABLE collaboration_role_grants ("
                "user_id INTEGER NOT NULL CHECK(user_id > 0), "
                "role TEXT NOT NULL CHECK(role IN ('coder', 'reconciler')), "
                "granted_by INTEGER NOT NULL CHECK(granted_by > 0), "
                "granted_at TEXT NOT NULL)",
                "INSERT INTO collaboration_role_grants"
                "(user_id, role, granted_by, granted_at) "
                "SELECT user_id, role, granted_by, granted_at "
                "FROM _s2mrw_grants_old",
                "DROP TABLE _s2mrw_grants_old",
            ),
        )
        before = self._s2mrw_collaboration_row_counts(missing_target)
        with self.subTest(case="F5a-missing-conflict-target"):
            conn = self._s2mrw_open_recording_probe(missing_target)
            try:
                self._s2mrw_reset_recording_probe(conn)
                with self.assertRaises(RuntimeError):
                    self.collab.grant_role(
                        self.store,
                        actor_user_id=OWNER_ID,
                        user_id=NEW_UID,
                        role="coder",
                        task_id=TASK_ID,
                        conn=conn,
                    )
                self._s2mrw_assert_attempts_no_business_dml(conn)
                self._s2mrw_assert_no_business_dml(conn)
                self._s2mrw_assert_borrowed_cursors_closed(conn)
                self._s2mrw_assert_counts_unchanged(missing_target, before)
                conn.rollback()
                # The caller reopens its own transaction for the control: the
                # rolled-back connection has no active transaction, and the
                # borrowed writer requires one.  The BEGIN cursor is closed
                # deterministically and the probe is reset only after the
                # BEGIN, so the BEGIN statement never enters the writer
                # evidence window.
                with closing(conn.execute("BEGIN IMMEDIATE")):
                    pass
                self._s2mrw_reset_recording_probe(conn)
                # Narrow-profile control: DELETE keeps working on the same
                # layout; a missing INSERT conflict target never blocks it.
                self.assertIs(
                    self.collab.revoke_role(
                        self.store,
                        actor_user_id=OWNER_ID,
                        user_id=CODER_ID,
                        role="coder",
                        task_id=TASK_ID,
                        conn=conn,
                    ),
                    True,
                )
                self._s2mrw_assert_borrowed_success(conn)
                self._s2mrw_assert_borrowed_cursors_closed(conn)
                conn.rollback()
            finally:
                self._s2mrw_close_probe(conn)

        # B2: a CHECK violation is not a conflict-target hit and must
        # propagate natively instead of being swallowed as idempotent
        # success; the failing statement's own cursor is structurally
        # outside the registry, and every registered cursor is closed.
        check_variant = self._s2mrw_minimal_schema(
            self.store,
            "grants-check-not-conflict",
            (
                "ALTER TABLE collaboration_role_grants "
                "RENAME TO _s2mrw_grants_old",
                "CREATE TABLE collaboration_role_grants ("
                "user_id INTEGER NOT NULL CHECK(user_id > 0), "
                "role TEXT NOT NULL CHECK(role IN ('coder', 'reconciler')), "
                "granted_by INTEGER NOT NULL CHECK(granted_by > 0), "
                "granted_at TEXT NOT NULL, "
                "CHECK(user_id <> granted_by), "
                "PRIMARY KEY(user_id, role))",
                "INSERT INTO collaboration_role_grants"
                "(user_id, role, granted_by, granted_at) "
                "SELECT user_id, role, granted_by, granted_at "
                "FROM _s2mrw_grants_old",
                "DROP TABLE _s2mrw_grants_old",
            ),
        )
        before = self._s2mrw_collaboration_row_counts(check_variant)
        with self.subTest(case="B2-check-not-suppressed"):
            conn = self._s2mrw_open_recording_probe(check_variant)
            try:
                self._s2mrw_reset_recording_probe(conn)
                with self.assertRaises(sqlite3.IntegrityError):
                    self.collab.grant_role(
                        self.store,
                        actor_user_id=OWNER_ID,
                        user_id=OWNER_ID,
                        role="coder",
                        task_id=TASK_ID,
                        conn=conn,
                    )
                self._s2mrw_assert_borrowed_cursors_closed(conn)
                self._s2mrw_assert_counts_unchanged(check_variant, before)
                conn.rollback()
            finally:
                self._s2mrw_close_probe(conn)

        # B3: fixture-only single-point fault.  SQLite's native authorizer
        # silently disables exactly one targeted INSERT so structure and
        # preflight inputs are untouched while the grant truly materializes
        # zero rows.  The mechanism is verified in-test on a scratch TEMP
        # table first and the injection is transparently disclosed; if the
        # runtime does not honor it, the trial is reported as a fixture
        # limitation, never as a product failure.  The contract requirement
        # under test is independent of the injection: the writer must verify
        # the grant row actually exists instead of returning success.
        with self.subTest(case="B3-silent-zero-row-grant"):
            conn = self._s2mrw_open_fault_probe(self.store)
            try:
                with closing(
                    conn.execute(
                        "CREATE TEMP TABLE s2mrw_fault_selfcheck(value INTEGER)"
                    )
                ):
                    pass
                conn.fault_table = "s2mrw_fault_selfcheck"
                conn.fault_armed = True
                with closing(
                    conn.execute(
                        "INSERT INTO s2mrw_fault_selfcheck(value) VALUES(1)"
                    )
                ):
                    pass
                insert_outcome = conn.attempts[-1]["outcome"]
                with closing(
                    conn.execute(
                        "SELECT COUNT(*) FROM temp.s2mrw_fault_selfcheck"
                    )
                ) as cur:
                    leftover = cur.fetchone()[0]
                # The scratch table must truly exist before the drop and be
                # truly gone after it; otherwise the self-check premise is
                # void and the trial must not continue.
                with closing(
                    conn.execute(
                        "SELECT name FROM temp.sqlite_temp_master "
                        "WHERE name = 's2mrw_fault_selfcheck'"
                    )
                ) as cur:
                    self.assertIsNotNone(
                        cur.fetchone(),
                        "B3 fixture-invalid: scratch table missing before "
                        "the drop",
                    )
                with closing(
                    conn.execute("DROP TABLE temp.s2mrw_fault_selfcheck")
                ):
                    pass
                with closing(
                    conn.execute(
                        "SELECT name FROM temp.sqlite_temp_master "
                        "WHERE name = 's2mrw_fault_selfcheck'"
                    )
                ) as cur:
                    self.assertIsNone(
                        cur.fetchone(),
                        "B3 fixture-invalid: scratch table survived the drop",
                    )
                if leftover != 0 or insert_outcome != "ok":
                    self.fail(
                        "B3 fixture mechanism unavailable: authorizer "
                        "SQLITE_IGNORE did not silently disable the scratch "
                        "INSERT (outcome=%s, leftover_rows=%s); trial void - "
                        "fixture fix required, not a product failure"
                        % (insert_outcome, leftover)
                    )
                self._s2mrw_reset_fault_probe(conn)
                injections_before = len(conn.injections)
                before = self._s2mrw_collaboration_row_counts(self.store)
                conn.fault_table = "collaboration_role_grants"
                conn.fault_armed = True
                writer_error = None
                try:
                    self.collab.grant_role(
                        self.store,
                        actor_user_id=OWNER_ID,
                        user_id=NEW_UID,
                        role="coder",
                        task_id=TASK_ID,
                        conn=conn,
                    )
                except Exception as exc:
                    writer_error = exc
                # Fixture triage first: whether the injection fired, where it
                # landed and whether it silenced the INSERT are fixture facts
                # that must be settled before the writer's outcome is
                # interpreted.  Any miss is fixture-invalid - never a product
                # failure and never a silent pass; the injections log keeps
                # the exact statement text so forms the matcher could not
                # recognize stay recorded as unverified fixture coverage.
                if len(conn.injections) != injections_before + 1:
                    self.fail(
                        "B3 fixture-invalid: the authorizer injection did "
                        "not fire exactly once in this window (window "
                        "count=%d); trial void - fixture issue, not a "
                        "product failure"
                        % (len(conn.injections) - injections_before)
                    )
                injected = conn.injections[-1]
                injected_kind = self._s2mrw_classify_trace([injected["sql"]])
                if not (
                    injected_kind
                    and injected_kind[0]["kind"] == "dml"
                    and injected_kind[0]["table"] == "collaboration_role_grants"
                ):
                    self.fail(
                        "B3 fixture-invalid: the injection did not land on "
                        "a collaboration_role_grants INSERT (statement text "
                        "kept in the injections log); trial void - fixture "
                        "issue, not a product failure"
                    )
                grant_attempts = []
                for attempt in self._s2mrw_borrowed_attempts(conn):
                    classified = self._s2mrw_classify_trace([attempt["sql"]])
                    if (
                        classified
                        and classified[0]["kind"] == "dml"
                        and classified[0]["table"] == "collaboration_role_grants"
                    ):
                        grant_attempts.append(attempt)
                if (
                    len(grant_attempts) != 1
                    or grant_attempts[0]["outcome"] != "ok"
                ):
                    self.fail(
                        "B3 fixture-invalid: the targeted INSERT was not "
                        "silently attempted exactly once (grants attempts=%d, "
                        "outcome=%s); trial void - fixture issue, not a "
                        "product failure"
                        % (
                            len(grant_attempts),
                            grant_attempts[0]["outcome"]
                            if grant_attempts else "missing",
                        )
                    )
                with closing(
                    conn.execute(
                        "SELECT COUNT(*) FROM main.collaboration_role_grants "
                        "WHERE user_id=?",
                        (NEW_UID,),
                    )
                ) as cur:
                    leftover = cur.fetchone()[0]
                if leftover != 0:
                    self.fail(
                        "B3 fixture-invalid: grant row exists despite the "
                        "injection; trial void - fixture issue, not a "
                        "product failure"
                    )
                # Premises held: only now is the writer outcome read as
                # product behavior.
                if writer_error is None:
                    self.fail(
                        "B3: writer returned success while the grant row is "
                        "absent - product reported a grant without a real "
                        "row (product defect)"
                    )
                self.assertIsInstance(
                    writer_error,
                    RuntimeError,
                    "product must raise the contract's explicit missing-row "
                    "RuntimeError; got %s: %s"
                    % (type(writer_error).__name__, writer_error),
                )
                self._s2mrw_assert_borrowed_cursors_closed(conn)
                self._s2mrw_assert_counts_unchanged(self.store, before)
                conn.rollback()
            finally:
                self._s2mrw_close_probe(conn)
            persisted = self._s2mrw_observer_rows(
                self.store,
                "SELECT COUNT(*) FROM main.collaboration_role_grants "
                "WHERE user_id=?",
                (NEW_UID,),
            )
            self.assertEqual(persisted[0][0], 0)

        # B4: the owner branch must not backfill a grant; the postcondition
        # only attaches to real backfill branches.
        grants_before = self._s2mrw_observer_rows(
            self.store,
            "SELECT COUNT(*) FROM main.collaboration_role_grants",
        )[0][0]
        with self.subTest(case="B4-owner-no-backfill"):
            conn = self._s2mrw_open_recording_probe(self.store)
            try:
                self._s2mrw_reset_recording_probe(conn)
                result = self.collab.set_business_role(
                    self.store,
                    actor_user_id=OWNER_ID,
                    user_id=OWNER_ID,
                    business_role="reconciler",
                    task_id=TASK_ID,
                    conn=conn,
                )
                self.assertIs(result["is_owner"], True)
                self._s2mrw_assert_borrowed_success(conn)
                self._s2mrw_assert_borrowed_cursors_closed(conn)
                for attempt in self._s2mrw_borrowed_attempts(conn):
                    classified = self._s2mrw_classify_trace([attempt["sql"]])
                    if (
                        classified
                        and classified[0]["kind"] == "dml"
                        and classified[0]["table"] == "collaboration_role_grants"
                    ):
                        self.fail(
                            "owner branch backfilled a grant: " + attempt["sql"]
                        )
                with closing(
                    conn.execute(
                        "SELECT COUNT(*) FROM main.collaboration_role_grants"
                    )
                ) as cur:
                    self.assertEqual(cur.fetchone()[0], grants_before)
                conn.rollback()
            finally:
                self._s2mrw_close_probe(conn)

        # F3a: a mid-sequence native failure must leave zero partial state.
        # The preceding choice/event/backfill-grant writes are intentional
        # real DML, so the writer is asserted to hold no session control
        # while real DML provably happened; the caller rolls back and an
        # independent observer verifies every affected table row-by-row.
        padded_timestamp = "x" * 120
        member_check = self._s2mrw_minimal_schema(
            self.store,
            "member-settings-check-fails-late",
            (
                "ALTER TABLE collaboration_member_settings "
                "RENAME TO _s2mrw_settings_old",
                "CREATE TABLE collaboration_member_settings ("
                "user_id INTEGER PRIMARY KEY CHECK(user_id > 0), "
                "screening_enabled INTEGER NOT NULL "
                "CHECK(screening_enabled IN (0, 1)), "
                "updated_by INTEGER NOT NULL CHECK(updated_by > 0), "
                "updated_at TEXT NOT NULL "
                "CHECK(length(updated_at) > 100))",
                "INSERT INTO collaboration_member_settings"
                "(user_id, screening_enabled, updated_by, updated_at) "
                "SELECT user_id, screening_enabled, updated_by, '"
                + padded_timestamp
                + "' FROM _s2mrw_settings_old",
                "DROP TABLE _s2mrw_settings_old",
            ),
        )
        events_before = self._s2mrw_observer_rows(
            member_check,
            "SELECT COUNT(*) FROM main.collaboration_business_role_events",
        )[0][0]
        grants_before = self._s2mrw_observer_rows(
            member_check,
            "SELECT COUNT(*) FROM main.collaboration_role_grants "
            "WHERE user_id=?",
            (CODER_ID,),
        )[0][0]
        settings_before = self._s2mrw_observer_rows(
            member_check,
            "SELECT COUNT(*) FROM main.collaboration_member_settings",
        )[0][0]
        with self.subTest(case="F3a-business-sequence-mid-failure"):
            conn = self._s2mrw_open_recording_probe(member_check)
            try:
                self._s2mrw_reset_recording_probe(conn)
                with self.assertRaises(sqlite3.IntegrityError):
                    self.collab.set_business_role(
                        self.store,
                        actor_user_id=OWNER_ID,
                        user_id=CODER_ID,
                        business_role="reconciler",
                        task_id=TASK_ID,
                        conn=conn,
                    )
                self._s2mrw_assert_writer_no_session_control(
                    conn,
                    expect_dml_tables=(
                        "collaboration_business_role",
                        "collaboration_business_role_events",
                        "collaboration_role_grants",
                    ),
                )
                self._s2mrw_assert_borrowed_cursors_closed(conn)
                conn.rollback()
            finally:
                self._s2mrw_close_probe(conn)
            persisted = self._s2mrw_observer_rows(
                member_check,
                "SELECT COUNT(*) FROM main.collaboration_business_role "
                "WHERE user_id=?",
                (CODER_ID,),
            )
            self.assertEqual(persisted[0][0], 0)
            persisted = self._s2mrw_observer_rows(
                member_check,
                "SELECT COUNT(*) "
                "FROM main.collaboration_business_role_events",
            )
            self.assertEqual(persisted[0][0], events_before)
            persisted = self._s2mrw_observer_rows(
                member_check,
                "SELECT COUNT(*) FROM main.collaboration_role_grants "
                "WHERE user_id=? AND role='reconciler'",
                (CODER_ID,),
            )
            self.assertEqual(persisted[0][0], 0)
            persisted = self._s2mrw_observer_rows(
                member_check,
                "SELECT COUNT(*) FROM main.collaboration_role_grants "
                "WHERE user_id=?",
                (CODER_ID,),
            )
            self.assertEqual(persisted[0][0], grants_before)
            persisted = self._s2mrw_observer_rows(
                member_check,
                "SELECT COUNT(*) FROM main.collaboration_member_settings",
            )
            self.assertEqual(persisted[0][0], settings_before)
            persisted = self._s2mrw_observer_rows(
                member_check,
                "SELECT COUNT(*) FROM main.collaboration_member_settings "
                "WHERE user_id=?",
                (CODER_ID,),
            )
            self.assertEqual(persisted[0][0], 0)

    # ------------------------------------------------------------------
    # Increment: count_changes environment
    # ------------------------------------------------------------------

    def test_count_changes_reconciler_initialization(self):
        # With count_changes=ON, INSERT/UPDATE/DELETE return a data row and
        # cursor.rowcount no longer reflects sqlite3_changes; the writer
        # must still produce correct semantics without touching the caller's
        # PRAGMA, row_factory, or transaction, must fully drain its own
        # cursors and close them, and a mid-sequence failure under ON must
        # still leave zero partial persistence after the caller rollback.
        # rowcount values are never used as a pass condition here.
        with self.subTest(case="C1-count-changes-on-initialization"):
            conn = self._s2mrw_open_recording_probe(self.store)
            try:
                with closing(
                    conn.execute("PRAGMA count_changes = ON")
                ) as cur:
                    cur.fetchall()
                with closing(
                    conn.execute("PRAGMA count_changes")
                ) as cur:
                    self.assertEqual(cur.fetchone()[0], 1)
                self._s2mrw_reset_recording_probe(conn)
                result = self.collab.grant_role(
                    self.store,
                    actor_user_id=OWNER_ID,
                    user_id=NEW_UID,
                    role="reconciler",
                    task_id=TASK_ID,
                    conn=conn,
                )
                self.assertEqual(
                    set(result),
                    {"user_id", "role", "granted_by", "granted_at"},
                )
                self._s2mrw_assert_borrowed_success(conn)
                self._s2mrw_assert_borrowed_cursors_closed(conn)
                records = self._s2mrw_classify_trace(
                    conn.statements[conn.statement_offset:]
                )
                for record in records:
                    self.assertNotEqual(
                        record["kind"],
                        "pragma",
                        "borrowed writer touched caller PRAGMA: "
                        + record["sql"],
                    )
                with closing(
                    conn.execute(
                        "SELECT screening_enabled, updated_by "
                        "FROM main.collaboration_member_settings "
                        "WHERE user_id=?",
                        (NEW_UID,),
                    )
                ) as cur:
                    self.assertEqual(
                        [tuple(row) for row in cur.fetchall()],
                        [(0, OWNER_ID)],
                    )
                with closing(
                    conn.execute("PRAGMA count_changes")
                ) as cur:
                    self.assertEqual(cur.fetchone()[0], 1)
                self.assertIs(conn.row_factory, sqlite3.Row)
                conn.commit()
            finally:
                self._s2mrw_close_probe(conn)
            persisted = self._s2mrw_observer_rows(
                self.store,
                "SELECT screening_enabled "
                "FROM main.collaboration_member_settings WHERE user_id=?",
                (NEW_UID,),
            )
            self.assertEqual([row[0] for row in persisted], [0])

        # C2: under ON a repeat grant stays idempotent and never resets.
        self.collab.set_member_screening(
            self.store,
            actor_user_id=OWNER_ID,
            user_id=RECON_ID,
            enabled=True,
            task_id=TASK_ID,
        )
        original = self._s2mrw_observer_rows(
            self.store,
            "SELECT granted_at FROM main.collaboration_role_grants "
            "WHERE user_id=? AND role='reconciler'",
            (RECON_ID,),
        )
        self.assertEqual(len(original), 1)
        with self.subTest(case="C2-count-changes-on-repeat-grant"):
            conn = self._s2mrw_open_recording_probe(self.store)
            try:
                with closing(
                    conn.execute("PRAGMA count_changes = ON")
                ) as cur:
                    cur.fetchall()
                self._s2mrw_reset_recording_probe(conn)
                result = self.collab.grant_role(
                    self.store,
                    actor_user_id=OWNER_ID,
                    user_id=RECON_ID,
                    role="reconciler",
                    task_id=TASK_ID,
                    conn=conn,
                )
                self.assertEqual(
                    set(result),
                    {"user_id", "role", "granted_by", "granted_at"},
                )
                self.assertEqual(result["granted_at"], original[0][0])
                self._s2mrw_assert_borrowed_success(conn)
                self._s2mrw_assert_borrowed_cursors_closed(conn)
                with closing(
                    conn.execute(
                        "SELECT screening_enabled "
                        "FROM main.collaboration_member_settings "
                        "WHERE user_id=?",
                        (RECON_ID,),
                    )
                ) as cur:
                    self.assertEqual(
                        [row[0] for row in cur.fetchall()], [1]
                    )
                with closing(
                    conn.execute(
                        "SELECT COUNT(*) FROM main.collaboration_role_grants "
                        "WHERE user_id=? AND role='reconciler'",
                        (RECON_ID,),
                    )
                ) as cur:
                    self.assertEqual(cur.fetchone()[0], 1)
                with closing(
                    conn.execute("PRAGMA count_changes")
                ) as cur:
                    self.assertEqual(cur.fetchone()[0], 1)
                conn.rollback()
            finally:
                self._s2mrw_close_probe(conn)

        # C3: OFF control for reconciler revoke keeps the established
        # semantics: the grant row and the member settings row both go.
        with self.subTest(case="C3-count-changes-off-revoke"):
            conn = self._s2mrw_open_recording_probe(self.store)
            try:
                with closing(
                    conn.execute("PRAGMA count_changes")
                ) as cur:
                    self.assertEqual(cur.fetchone()[0], 0)
                self._s2mrw_reset_recording_probe(conn)
                self.assertIs(
                    self.collab.revoke_role(
                        self.store,
                        actor_user_id=OWNER_ID,
                        user_id=RECON_ID,
                        role="reconciler",
                        task_id=TASK_ID,
                        conn=conn,
                    ),
                    True,
                )
                self._s2mrw_assert_borrowed_success(conn)
                self._s2mrw_assert_borrowed_cursors_closed(conn)
                with closing(
                    conn.execute(
                        "SELECT COUNT(*) "
                        "FROM main.collaboration_member_settings "
                        "WHERE user_id=?",
                        (RECON_ID,),
                    )
                ) as cur:
                    self.assertEqual(cur.fetchone()[0], 0)
                conn.commit()
            finally:
                self._s2mrw_close_probe(conn)
            persisted = self._s2mrw_observer_rows(
                self.store,
                "SELECT COUNT(*) FROM main.collaboration_role_grants "
                "WHERE user_id=? AND role='reconciler'",
                (RECON_ID,),
            )
            self.assertEqual(persisted[0][0], 0)

        # F3b: ON environment with a mid-sequence native failure.  The
        # grants write succeeds, the settings write fails the runtime CHECK,
        # the native error propagates, the writer holds no session control,
        # and the caller rollback erases every partial write.
        padded_timestamp = "x" * 120
        member_check = self._s2mrw_minimal_schema(
            self.store,
            "member-settings-check-fails-on",
            (
                "ALTER TABLE collaboration_member_settings "
                "RENAME TO _s2mrw_settings_old",
                "CREATE TABLE collaboration_member_settings ("
                "user_id INTEGER PRIMARY KEY CHECK(user_id > 0), "
                "screening_enabled INTEGER NOT NULL "
                "CHECK(screening_enabled IN (0, 1)), "
                "updated_by INTEGER NOT NULL CHECK(updated_by > 0), "
                "updated_at TEXT NOT NULL "
                "CHECK(length(updated_at) > 100))",
                "INSERT INTO collaboration_member_settings"
                "(user_id, screening_enabled, updated_by, updated_at) "
                "SELECT user_id, screening_enabled, updated_by, '"
                + padded_timestamp
                + "' FROM _s2mrw_settings_old",
                "DROP TABLE _s2mrw_settings_old",
            ),
        )
        with self.subTest(case="F3b-grant-sequence-mid-failure-on"):
            conn = self._s2mrw_open_recording_probe(member_check)
            try:
                with closing(
                    conn.execute("PRAGMA count_changes = ON")
                ) as cur:
                    cur.fetchall()
                # C1 already committed a NEW_UID reconciler grant, which would
                # make a repeat grant land rowcount=0 and skip the settings
                # write entirely.  OTHER_UID is unused elsewhere in this
                # method, so the call below is a genuinely fresh reconciler
                # grant.  Premise is measured, not assumed: neither table
                # knows this user before the call.
                with closing(
                    conn.execute(
                        "SELECT COUNT(*) FROM main.collaboration_role_grants "
                        "WHERE user_id=?",
                        (OTHER_UID,),
                    )
                ) as cur:
                    self.assertEqual(cur.fetchone()[0], 0)
                with closing(
                    conn.execute(
                        "SELECT COUNT(*) "
                        "FROM main.collaboration_member_settings "
                        "WHERE user_id=?",
                        (OTHER_UID,),
                    )
                ) as cur:
                    self.assertEqual(cur.fetchone()[0], 0)
                self._s2mrw_reset_recording_probe(conn)
                with self.assertRaises(sqlite3.IntegrityError):
                    self.collab.grant_role(
                        self.store,
                        actor_user_id=OWNER_ID,
                        user_id=OTHER_UID,
                        role="reconciler",
                        task_id=TASK_ID,
                        conn=conn,
                    )
                self._s2mrw_assert_writer_no_session_control(
                    conn,
                    expect_dml_tables=("collaboration_role_grants",),
                )
                self._s2mrw_assert_borrowed_cursors_closed(conn)
                conn.rollback()
            finally:
                self._s2mrw_close_probe(conn)
            persisted = self._s2mrw_observer_rows(
                member_check,
                "SELECT COUNT(*) FROM main.collaboration_role_grants "
                "WHERE user_id=?",
                (OTHER_UID,),
            )
            self.assertEqual(persisted[0][0], 0)
            persisted = self._s2mrw_observer_rows(
                member_check,
                "SELECT COUNT(*) FROM main.collaboration_member_settings "
                "WHERE user_id=?",
                (OTHER_UID,),
            )
            self.assertEqual(persisted[0][0], 0)

    def test_borrowed_reader_fault_stages_preserve_errors(self):
        # These are disclosed proxy injections on cursors obtained by the
        # real public writer.  They are not naturally occurring SQLite faults.
        self.collab.set_business_role(
            self.store, actor_user_id=OWNER_ID, user_id=OWNER_ID,
            business_role="reconciler", task_id=TASK_ID,
        )
        for target in ("owner", "optional-choice"):
            for mode in ("execute-missing", "fetch-missing", "double", "close-only"):
                with self.subTest(target=target, injected_mode=mode):
                    conn = self._s2mrw_open_recording_probe(self.store)
                    token = "INJECTED-S2MRW-" + target + "-" + mode
                    primary = sqlite3.OperationalError("no such table: " + token + "-primary")
                    cleanup = RuntimeError(token + "-cleanup")
                    plan = {
                        "target": target, "mode": mode,
                        "primary": primary, "cleanup": cleanup,
                        "events": [], "cursor": None,
                    }
                    conn._s2mrw_reader_fault = plan
                    try:
                        self._s2mrw_reset_recording_probe(conn)
                        with self.assertRaises(Exception) as caught:
                            if target == "owner":
                                self.collab.grant_role(
                                    self.store, actor_user_id=OWNER_ID,
                                    user_id=NEW_UID, role="coder",
                                    task_id=TASK_ID, conn=conn,
                                )
                            else:
                                self.collab.set_member_screening(
                                    self.store, actor_user_id=OWNER_ID,
                                    user_id=OWNER_ID, enabled=False,
                                    task_id=TASK_ID, conn=conn,
                                )
                        if target == "owner":
                            # Assert the actual journaled data read in every
                            # injected mode, not merely a selector-generated hit.
                            # This exact SQL/binding is from _management_owner;
                            # metadata, WHERE 0 and EXPLAIN cannot satisfy it.
                            owner_sql = (
                                "select task_id, owner_user_id, owner_username, created_at "
                                "from main.collaboration_task where singleton=1"
                            )
                            owner_reads = []
                            for attempt in self._s2mrw_borrowed_attempts(conn):
                                if " ".join(attempt["sql"].split()).casefold() == owner_sql:
                                    self.assertEqual(tuple(attempt["parameters"]), ())
                                    owner_reads.append(attempt)
                                elif (
                                    re.search(r"\bFROM\s+main\s*\.\s*collaboration_task\b",
                                              attempt["sql"], re.IGNORECASE) is not None
                                    and re.search(r"\bWHERE\s+0\b", attempt["sql"], re.IGNORECASE) is not None
                                ):
                                    self.assertEqual(attempt["outcome"], "ok")
                            self.assertEqual(len(owner_reads), 1)
                            self.assertEqual(
                                owner_reads[0]["outcome"],
                                "OperationalError" if mode == "execute-missing" else "ok",
                            )
                            if mode != "execute-missing":
                                self.assertIsNotNone(plan["cursor"])
                                self.assertEqual(
                                    " ".join(plan["cursor"].sql.split()).casefold(), owner_sql,
                                )
                                self.assertEqual(tuple(plan["cursor"].parameters), ())
                        # The journal/phase events are appended only inside
                        # the execute/fetchall/close calls made by the product.
                        expected = ["execute"] if mode == "execute-missing" else [
                            "execute", "fetchall", "close",
                        ]
                        self.assertEqual([e["phase"] for e in plan["events"]], expected)
                        for event in plan["events"]:
                            self.assertEqual(event["target"], target)
                        if mode == "execute-missing":
                            self.assertIsNone(plan["cursor"])
                            if target == "optional-choice":
                                # Optional execute missing-table is the one
                                # intentional absence fallback, then the public
                                # screening writer rejects the unconfigured owner.
                                self.assertIsInstance(caught.exception, ValueError)
                                self.assertIn("no reconciler role", str(caught.exception))
                            else:
                                self.assertIs(caught.exception, primary)
                        else:
                            cursor = plan["cursor"]
                            self.assertIsNotNone(cursor)
                            self.assertIn(cursor, conn.cursors[conn.cursor_offset:])
                            self.assertIs(cursor._s2mrw_owner, conn)
                            if mode == "fetch-missing":
                                self.assertIs(caught.exception, primary)
                                self.assertTrue(cursor.closed)
                            else:
                                # A failed close must not be disguised as a
                                # successful closure; cleanup has not run yet.
                                self.assertFalse(cursor.closed)
                            if mode == "close-only":
                                self.assertTrue(cursor.fetchall_called)
                                self.assertIs(caught.exception, cleanup)
                            elif mode == "double":
                                # C02 permits notes, cause/context and groups.
                                # Inspect the actual propagated exception graph,
                                # without clearing any traceback or pinning its top.
                                pending = [caught.exception]
                                seen = set()
                                visible = []
                                while pending:
                                    exc = pending.pop()
                                    if id(exc) in seen:
                                        continue
                                    seen.add(id(exc))
                                    visible.append(str(exc))
                                    visible.extend(getattr(exc, "__notes__", ()))
                                    if exc.__cause__ is not None:
                                        pending.append(exc.__cause__)
                                    if exc.__context__ is not None:
                                        pending.append(exc.__context__)
                                    if isinstance(exc, BaseExceptionGroup):
                                        pending.extend(exc.exceptions)
                                text = "\n".join(visible)
                                self.assertIn(token + "-primary", text)
                                self.assertIn(token + "-cleanup", text)
                        self._s2mrw_assert_no_business_dml(conn)
                        self._s2mrw_assert_attempts_no_business_dml(conn)
                        self.assertIs(conn.row_factory, sqlite3.Row)
                        self.assertTrue(conn.in_transaction)
                        # Before disarming, all other cursors must already
                        # have been closed by the product.  The target may
                        # remain open only in the injected close-failure cases.
                        for cursor in conn.cursors[conn.cursor_offset:]:
                            if cursor is plan["cursor"] and mode in ("double", "close-only"):
                                continue
                            self.assertTrue(cursor.closed, cursor.sql)
                    finally:
                        # No fixture closes a cursor before the assertions.
                        conn._s2mrw_reader_fault = None
                        self._s2mrw_close_probe(conn)

        # P01 regression: a single injected OperationalError("database is
        # locked") on the schema preflight's own WHERE 0 column probe must
        # escape the writer unchanged, exactly as every other borrowed
        # preflight error does.  Until the bare raise exists, the except in
        # _management_conn swallows this single lock error and the writer
        # walks on into its business flow, so the assertRaises below fails.
        # This is a disclosed proxy injection, not a natural lock.
        with self.subTest(target="preflight-where0", injected_mode="execute-missing"):
            conn = self._s2mrw_open_recording_probe(self.store)
            token = "INJECTED-S2MRW-preflight-where0-execute-missing"
            primary = sqlite3.OperationalError("database is locked")
            cleanup = RuntimeError(token + "-cleanup")
            plan = {
                "target": "preflight-where0", "mode": "execute-missing",
                "primary": primary, "cleanup": cleanup,
                "events": [], "cursor": None,
            }
            conn._s2mrw_reader_fault = plan
            try:
                self._s2mrw_reset_recording_probe(conn)
                with self.assertRaises(Exception) as caught:
                    self.collab.grant_role(
                        self.store, actor_user_id=OWNER_ID,
                        user_id=NEW_UID, role="coder",
                        task_id=TASK_ID, conn=conn,
                    )
                # C02: the exact injected object escapes; no re-wrap, no
                # swallow-and-continue, no traceback clearing.
                self.assertIs(caught.exception, primary)
                # The injection hit the preflight statement itself, once, at
                # execute: no cursor was ever created for it (execute raised
                # before the raw statement ran, so there is nothing whose
                # closure needs observing beyond the registry sweep below).
                self.assertEqual(
                    [e["phase"] for e in plan["events"]], ["execute"])
                for event in plan["events"]:
                    self.assertEqual(event["target"], "preflight-where0")
                self.assertIsNone(plan["cursor"])
                # Journal proof: exactly one WHERE 0 column probe against
                # main.collaboration_task was attempted with empty bindings,
                # and it is the statement that died with the injected error;
                # the owner's real singleton row read (WHERE singleton=1)
                # never matches the selector and never ran.
                where0_reads = []
                for attempt in self._s2mrw_borrowed_attempts(conn):
                    if (
                        re.search(r"\bFROM\s+main\s*\.\s*collaboration_task\b",
                                  attempt["sql"], re.IGNORECASE) is not None
                        and re.search(r"\bWHERE\s+0\b", attempt["sql"],
                                      re.IGNORECASE) is not None
                    ):
                        self.assertEqual(tuple(attempt["parameters"]), ())
                        where0_reads.append(attempt)
                self.assertEqual(len(where0_reads), 1)
                self.assertEqual(where0_reads[0]["outcome"], "OperationalError")
                # Preflight aborted before any business DML; the caller's
                # connection identity, row_factory and open transaction are
                # untouched, and the writer neither committed, rolled back
                # nor closed anything.
                self._s2mrw_assert_no_business_dml(conn)
                self._s2mrw_assert_attempts_no_business_dml(conn)
                self.assertIs(conn.row_factory, sqlite3.Row)
                self.assertTrue(conn.in_transaction)
                self.assertEqual(conn.commit_calls, 0)
                self.assertEqual(conn.rollback_calls, 0)
                self.assertEqual(conn.close_calls, 0)
                for cursor in conn.cursors[conn.cursor_offset:]:
                    self.assertTrue(cursor.closed, cursor.sql)
            finally:
                # No fixture closes a cursor before the assertions; the
                # caller, not the writer, ends its still-open transaction.
                conn._s2mrw_reader_fault = None
                conn.rollback()
                self._s2mrw_close_probe(conn)

    def test_borrowed_optional_choice_missing_table_execute_only(self):
        variant = self._s2mrw_minimal_schema(
            self.store, "natural-optional-choice-missing",
            ("DROP TABLE collaboration_business_role",),
        )
        self.assertEqual(self._s2mrw_observer_rows(
            variant, "SELECT name FROM main.sqlite_master WHERE type='table' AND name=?",
            ("collaboration_business_role",),
        ), [])
        self.assertEqual(self._s2mrw_observer_rows(
            variant, "SELECT role FROM main.collaboration_role_grants WHERE user_id=?",
            (OWNER_ID,),
        ), [])
        conn = self._s2mrw_open_recording_probe(variant)
        try:
            self._s2mrw_reset_recording_probe(conn)
            with self.assertRaises(ValueError) as caught:
                self.collab.set_member_screening(
                    variant, actor_user_id=OWNER_ID, user_id=OWNER_ID,
                    enabled=False, task_id=TASK_ID, conn=conn,
                )
            self.assertIn("no reconciler role", str(caught.exception))
            missing_attempts = []
            for attempt in self._s2mrw_borrowed_attempts(conn):
                if re.search(
                    r"\bfrom\s+main\s*\.\s*collaboration_business_role\b",
                    attempt["sql"], re.IGNORECASE,
                ):
                    missing_attempts.append(attempt)
            self.assertEqual(len(missing_attempts), 1)
            self.assertEqual(missing_attempts[0]["outcome"], "OperationalError")
            self.assertFalse(any(
                re.search(r"\bfrom\s+main\s*\.\s*collaboration_business_role\b",
                          cursor.sql, re.IGNORECASE)
                for cursor in conn.cursors[conn.cursor_offset:]
            ), "failed execute cannot supply a fetchable cursor")
            self._s2mrw_assert_no_business_dml(conn)
            self._s2mrw_assert_attempts_no_business_dml(conn)
            self._s2mrw_assert_borrowed_cursors_closed(conn)
            conn.rollback()
        finally:
            self._s2mrw_close_probe(conn)
        self.assertEqual(self._s2mrw_observer_rows(
            variant, "SELECT screening_enabled FROM main.collaboration_member_settings WHERE user_id=?",
            (OWNER_ID,),
        ), [])

        # This natural required-table negative reaches only preflight.
        # It is deliberately not reported as a _management_read_rows trial.
        required = self._s2mrw_minimal_schema(
            self.store, "natural-required-owner-table-missing",
            ("DROP TABLE collaboration_task",),
        )
        conn = self._s2mrw_open_recording_probe(required)
        try:
            self._s2mrw_reset_recording_probe(conn)
            with self.assertRaises(RuntimeError):
                self.collab.grant_role(
                    required, actor_user_id=OWNER_ID, user_id=NEW_UID,
                    role="coder", task_id=TASK_ID, conn=conn,
                )
            self.assertFalse(any(
                re.search(r"\bfrom\s+main\s*\.\s*collaboration_task\b",
                          attempt["sql"], re.IGNORECASE)
                for attempt in self._s2mrw_borrowed_attempts(conn)
            ))
            self._s2mrw_assert_no_business_dml(conn)
            self._s2mrw_assert_attempts_no_business_dml(conn)
            self._s2mrw_assert_borrowed_cursors_closed(conn)
            conn.rollback()
        finally:
            self._s2mrw_close_probe(conn)

    def test_borrowed_owner_resolver_business_parity(self):
        from datetime import datetime

        for case, target, expected_role, expected_config, expected_source in (
            ("owner-unconfigured", OWNER_ID, None, "unconfigured", "none"),
            ("owner-coder-choice", OWNER_ID, "coder", "ok", "choice"),
            ("owner-reconciler-choice", OWNER_ID, "reconciler", "ok", "choice"),
            ("member-coder-grant", CODER_ID, "coder", "ok", "grant"),
            ("member-reconciler-grant", NEW_UID, "reconciler", "ok", "grant"),
            ("member-dual-conflict", CODER_ID, None, "conflict", "none"),
            ("member-stale-choice", CODER_ID, None, "unconfigured", "stale_choice"),
            ("removed-choice", CODER_ID, None, "unconfigured", "stale_choice"),
        ):
            with self.subTest(case=case):
                seed = self._s2mrw_minimal_schema(self.store, case + "-seed", ())
                # Build every business state through public writers.  No
                # expected resolver/writer response is manufactured by a helper.
                if case in ("owner-coder-choice", "owner-reconciler-choice"):
                    self.collab.set_business_role(
                        seed, actor_user_id=OWNER_ID, user_id=OWNER_ID,
                        business_role=("coder" if case == "owner-coder-choice" else "reconciler"),
                        task_id=TASK_ID,
                    )
                elif case == "member-reconciler-grant":
                    self.collab.grant_role(
                        seed, actor_user_id=OWNER_ID, user_id=NEW_UID,
                        role="reconciler", task_id=TASK_ID,
                    )
                elif case == "member-dual-conflict":
                    self.collab.grant_role(
                        seed, actor_user_id=OWNER_ID, user_id=CODER_ID,
                        role="reconciler", task_id=TASK_ID,
                    )
                elif case in ("member-stale-choice", "removed-choice"):
                    self.collab.set_business_role(
                        seed, actor_user_id=OWNER_ID, user_id=CODER_ID,
                        business_role="reconciler", task_id=TASK_ID,
                    )
                    self.collab.revoke_role(
                        seed, actor_user_id=OWNER_ID, user_id=CODER_ID,
                        role="reconciler", task_id=TASK_ID,
                    )
                    if case == "removed-choice":
                        self.collab.revoke_role(
                            seed, actor_user_id=OWNER_ID, user_id=CODER_ID,
                            role="coder", task_id=TASK_ID,
                        )
                default_path = self._s2mrw_minimal_schema(seed, case + "-default", ())
                borrowed_path = self._s2mrw_minimal_schema(seed, case + "-borrowed", ())
                snapshots = []
                responses = []
                for mode, path in (("default", default_path), ("borrowed", borrowed_path)):
                    with self.subTest(mode=mode):
                        prior = self.collab.resolve_business_role(
                            path, user_id=target, task_id=TASK_ID,
                        )
                        self.assertEqual(prior["role"], expected_role)
                        self.assertEqual(prior["config"], expected_config)
                        self.assertEqual(prior["source"], expected_source)
                        self.assertIs(prior["is_owner"], target == OWNER_ID)
                        self.assertIs(prior["participant"], case != "removed-choice")
                        self.assertIs(prior["conflict"], case == "member-dual-conflict")
                        events_before = self._s2mrw_observer_rows(
                            path, "SELECT id,user_id,old_role,new_role,set_by,set_at "
                            "FROM main.collaboration_business_role_events ORDER BY id",
                        )
                        before = []
                        for sql in (
                            "SELECT user_id,role,granted_by,granted_at FROM main.collaboration_role_grants ORDER BY user_id,role",
                            "SELECT user_id,screening_enabled,updated_by,updated_at FROM main.collaboration_member_settings ORDER BY user_id",
                            "SELECT user_id,business_role,set_by,set_at FROM main.collaboration_business_role ORDER BY user_id",
                            "SELECT id,user_id,old_role,new_role,set_by,set_at FROM main.collaboration_business_role_events ORDER BY id",
                        ):
                            before.append(self._s2mrw_observer_rows(path, sql))
                        conn = self._s2mrw_open_recording_probe(path) if mode == "borrowed" else None
                        try:
                            if conn is not None:
                                self._s2mrw_reset_recording_probe(conn)
                            if case == "removed-choice":
                                with self.assertRaises(ValueError) as denied:
                                    self.collab.set_business_role(
                                        path, actor_user_id=OWNER_ID, user_id=target,
                                        business_role="coder", task_id=TASK_ID,
                                        conn=conn,
                                    )
                                self.assertIn("not a current task participant", str(denied.exception))
                                responses.append(type(denied.exception))
                                if conn is not None:
                                    self._s2mrw_assert_no_business_dml(conn)
                                    self._s2mrw_assert_attempts_no_business_dml(conn)
                            else:
                                result = self.collab.set_business_role(
                                    path, actor_user_id=OWNER_ID, user_id=target,
                                    business_role="coder", task_id=TASK_ID, conn=conn,
                                )
                                self.assertEqual(set(result), {
                                    "user_id", "business_role", "previous_role", "previous_config",
                                    "is_owner", "set_by", "changed_at",
                                })
                                self.assertEqual(result["previous_role"], expected_role)
                                self.assertEqual(result["previous_config"], expected_config)
                                self.assertEqual(result["business_role"], "coder")
                                self.assertEqual(result["set_by"], OWNER_ID)
                                self.assertIs(result["is_owner"], target == OWNER_ID)
                                stamp = datetime.fromisoformat(result["changed_at"].replace("Z", "+00:00"))
                                self.assertIsNotNone(stamp.tzinfo)
                                self.assertEqual(stamp.utcoffset().total_seconds(), 0)
                                responses.append({
                                    key: value for key, value in result.items() if key != "changed_at"
                                })
                                if conn is not None:
                                    self._s2mrw_assert_borrowed_success(conn)
                            if conn is not None:
                                self._s2mrw_assert_borrowed_cursors_closed(conn)
                                conn.commit()
                        finally:
                            if conn is not None:
                                self._s2mrw_close_probe(conn)
                        after = []
                        for sql in (
                            "SELECT user_id,role,granted_by,granted_at FROM main.collaboration_role_grants ORDER BY user_id,role",
                            "SELECT user_id,screening_enabled,updated_by,updated_at FROM main.collaboration_member_settings ORDER BY user_id",
                            "SELECT user_id,business_role,set_by,set_at FROM main.collaboration_business_role ORDER BY user_id",
                            "SELECT id,user_id,old_role,new_role,set_by,set_at FROM main.collaboration_business_role_events ORDER BY id",
                        ):
                            after.append(self._s2mrw_observer_rows(path, sql))
                        if case == "removed-choice":
                            self.assertEqual(after, before)
                        else:
                            events = after[3]
                            self.assertEqual(events[:-1], events_before)
                            self.assertEqual(len(events), len(events_before) + 1)
                            self.assertEqual(events[-1][1:5], (target, expected_role, "coder", OWNER_ID))
                            self.assertEqual(events[-1][-1], result["changed_at"])
                            resolved = self.collab.resolve_business_role(
                                path, user_id=target, task_id=TASK_ID,
                            )
                            self.assertEqual(resolved["role"], "coder")
                            self.assertEqual(resolved["config"], "ok")
                            self.assertEqual(resolved["source"], "choice")
                            self.assertIs(resolved["required_screener"], True)
                        normalized = []
                        for rows in after:
                            normalized_rows = []
                            for row in rows:
                                # Each selected table's final column is an
                                # authentic persisted timestamp, not a fixture token.
                                stamp = datetime.fromisoformat(row[-1].replace("Z", "+00:00"))
                                self.assertIsNotNone(stamp.tzinfo)
                                self.assertEqual(stamp.utcoffset().total_seconds(), 0)
                                self.assertEqual(row[-2], OWNER_ID)
                                normalized_rows.append(row[:-1])
                            normalized.append(normalized_rows)
                        snapshots.append(normalized)
                        if target == OWNER_ID:
                            self.assertEqual(self._s2mrw_observer_rows(
                                path, "SELECT role FROM main.collaboration_role_grants WHERE user_id=?",
                                (OWNER_ID,),
                            ), [])
                self.assertEqual(responses[0], responses[1])
                self.assertEqual(snapshots[0], snapshots[1])



class _S2MRWRecordingCursor(_S2MRWProbeCursor):
    """Probe cursor that journals every execute with its bound parameters.

    Recording is evidence only: the contract under test never pins statement
    text, parameter shapes, or how often a mechanism runs.  ``owner`` is the
    recording connection that built this cursor; a fault-injection owner may
    open its authorizer window around a cursor-path execute exactly as it
    does around a connection-path execute.
    """

    def __init__(self, cursor, sql, registry=None, journal=None,
                 parameters=(), owner=None):
        super().__init__(cursor, sql, registry)
        self.parameters = parameters
        self._s2mrw_journal = journal if journal is not None else []
        self._s2mrw_owner = owner

    def execute(self, sql, parameters=()):
        # Journal before forwarding so a statement that fails before running
        # is still recorded; the outcome is filled in and the exception is
        # re-raised unchanged, without swallowing or clearing the traceback.
        # A fault owner arms its authorizer around this single statement the
        # same way it does on the connection path; the window decision is
        # made exactly once per statement execution, here or in the
        # connection override, never twice and never inside the journal.
        record = {"sql": sql, "parameters": parameters, "outcome": None}
        self._s2mrw_journal.append(record)
        owner = self._s2mrw_owner
        fault_window = (
            owner is not None
            and owner._s2mrw_fault_should_arm(sql, parameters)
        )
        if fault_window:
            owner.set_authorizer(owner._s2mrw_fault_authorizer)
        try:
            self._cursor.execute(sql, parameters)
        except BaseException as exc:
            record["outcome"] = type(exc).__name__
            raise
        finally:
            if fault_window:
                owner.set_authorizer(None)
        record["outcome"] = "ok"
        self.sql = sql
        self.parameters = parameters
        return self

    def executemany(self, sql, seq_of_parameters):
        record = {
            "sql": sql,
            "parameters": seq_of_parameters,
            "outcome": None,
        }
        self._s2mrw_journal.append(record)
        try:
            self._cursor.executemany(sql, seq_of_parameters)
        except BaseException as exc:
            record["outcome"] = type(exc).__name__
            raise
        record["outcome"] = "ok"
        self.sql = sql
        self.parameters = seq_of_parameters
        return self

    def fetchall(self):
        owner = self._s2mrw_owner
        plan = getattr(owner, "_s2mrw_reader_fault", None)
        if (plan is not None and plan.get("cursor") is self
                and owner._s2mrw_reader_fault_matches(self.sql, self.parameters)):
            plan["events"].append({"phase": "fetchall", "target": plan["target"]})
            if plan["mode"] in ("fetch-missing", "double"):
                # Deliberate proxy injection, not an error from raw.fetchall.
                raise plan["primary"]
        return super().fetchall()

    def close(self):
        owner = self._s2mrw_owner
        plan = getattr(owner, "_s2mrw_reader_fault", None)
        if (plan is not None and plan.get("cursor") is self
                and owner._s2mrw_reader_fault_matches(self.sql, self.parameters)):
            plan["events"].append({"phase": "close", "target": plan["target"]})
            if plan["mode"] in ("double", "close-only"):
                # Do not pre-close the real cursor or falsify closed=True.
                # Tests disarm only after observing the product's close attempt.
                raise plan["cleanup"]
        return super().close()



class _S2MRWRecordingConnection(_S2MRWProbeConnection):
    """Probe connection that keeps the attempts journal for every statement.

    The journal and the trace callback are complementary: the trace only
    sees statements that actually started running, while the journal also
    records statements that failed before running.  Neither is required to
    contain any particular statement.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.attempts = []
        self.attempt_offset = 0

    def execute(self, sql, parameters=()):
        record = {"sql": sql, "parameters": parameters, "outcome": None}
        self.attempts.append(record)
        plan = getattr(self, "_s2mrw_reader_fault", None)
        matched = self._s2mrw_reader_fault_matches(sql, parameters)
        try:
            if matched:
                plan["events"].append({"phase": "execute", "target": plan["target"]})
                if plan["mode"] == "execute-missing":
                    # Disclosed stage-specific proxy fault.  SQLite was not
                    # asked to execute this targeted statement in this trial.
                    raise plan["primary"]
            raw = sqlite3.Connection.execute(self, sql, parameters)
        except BaseException as exc:
            record["outcome"] = type(exc).__name__
            raise
        record["outcome"] = "ok"
        cursor = _S2MRWRecordingCursor(
            raw, sql, self.cursors, self.attempts, parameters, owner=self
        )
        if matched:
            plan["cursor"] = cursor
        return cursor

    def executemany(self, sql, seq_of_parameters):
        record = {
            "sql": sql,
            "parameters": seq_of_parameters,
            "outcome": None,
        }
        self.attempts.append(record)
        try:
            raw = sqlite3.Connection.executemany(self, sql, seq_of_parameters)
        except BaseException as exc:
            record["outcome"] = type(exc).__name__
            raise
        record["outcome"] = "ok"
        return _S2MRWRecordingCursor(
            raw, sql, self.cursors, self.attempts, seq_of_parameters, owner=self
        )

    def cursor(self, factory=None):
        # Mirrors _S2MRWProbeConnection.cursor exactly but wraps in the
        # recording cursor, so the registry still holds exactly one proxy
        # per cursor the API received.
        raw = (
            sqlite3.Connection.cursor(self, factory)
            if factory is not None
            else sqlite3.Connection.cursor(self)
        )
        return _S2MRWRecordingCursor(
            raw, "", self.cursors, self.attempts, owner=self
        )

    def _s2mrw_fault_should_arm(self, sql, parameters=None):
        # Base no-op: only a fault-injection owner ever opens an authorizer
        # window; non-fault recording probes keep their exact old behavior.
        return False

    def _s2mrw_reader_fault_matches(self, sql, parameters=()):
        plan = getattr(self, "_s2mrw_reader_fault", None)
        if plan is None:
            return False
        # Only actual SELECTs with the named source/fixture binding qualify.
        # Metadata and EXPLAIN preflight cannot stand in for reader coverage.
        if re.match(r"\s*SELECT\b", sql, re.IGNORECASE) is None:
            return False
        if plan["target"] == "owner":
            # _management_conn also reads these columns with WHERE 0.
            # Inject only into _management_owner's actual singleton row read,
            # using the SQL and empty binding verified in the product candidate.
            if re.search(r"\bWHERE\s+0\b", sql, re.IGNORECASE) is not None:
                return False
            return (
                tuple(parameters) == ()
                and " ".join(sql.split()).casefold() == (
                    "select task_id, owner_user_id, owner_username, created_at "
                    "from main.collaboration_task where singleton=1"
                )
            )
        if plan["target"] == "optional-choice":
            return (
                tuple(parameters) == (OWNER_ID,)
                and re.search(r"\bFROM\s+main\s*\.\s*collaboration_business_role\b", sql, re.IGNORECASE) is not None
            )
        if plan["target"] == "preflight-where0":
            # P01 regression target: _management_conn's required-table column
            # preflight itself, the SELECT ... FROM main.<table> WHERE 0 probe
            # with empty bindings.  The owner's real singleton row read
            # (WHERE singleton=1), the sqlite_master presence query, metadata
            # pragma queries and any EXPLAIN probe never satisfy this.
            return (
                tuple(parameters) == ()
                and re.search(r"\bFROM\s+main\s*\.\s*collaboration_task\b",
                              sql, re.IGNORECASE) is not None
                and re.search(r"\bWHERE\s+0\b", sql, re.IGNORECASE) is not None
            )
        return False



class _S2MRWFaultInjectionConnection(_S2MRWRecordingConnection):
    """Recording probe with a transparent single-point INSERT fault.

    Fixture mechanism: SQLite's native authorizer returns SQLITE_IGNORE for
    one armed INSERT so the statement completes silently with zero rows
    while preflight-visible structure stays untouched.  The activation is
    armed per trial, fires at most once on whichever path (connection or
    cursor) actually executes the statement, is removed in a finally block,
    and is disclosed in ``injections``.  The runtime effect of
    SQLITE_IGNORE on INSERT is verified in-test before use; if unavailable,
    the trial is reported as a fixture limitation and never as a product
    failure.
    """

    # stdlib exposes these constants on modern CPython; the integer
    # fallbacks are the documented SQLite authorizer action codes.
    SQLITE_INSERT = getattr(sqlite3, "SQLITE_INSERT", 18)
    SQLITE_OK = getattr(sqlite3, "SQLITE_OK", 0)
    SQLITE_IGNORE = getattr(sqlite3, "SQLITE_IGNORE", 2)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fault_table = None
        self.fault_armed = False
        self.injections = []

    def _s2mrw_fault_targets(self, sql):
        # True when the statement carries an INSERT INTO the fault table and
        # is not an EXPLAIN preflight probe.  Matching is a bounded
        # word-boundary containment check after comment stripping - wide
        # enough for equivalent INSERT forms (OR variants, main.-qualified,
        # CTE-wrapped), deliberately not a general SQL parser.  Statements
        # the matcher cannot recognize simply do not arm; the B3 triage
        # records that as fixture-invalid instead of a product failure, and
        # the injections log keeps the exact statement text as unverified
        # fixture coverage.
        text = sql.lstrip()
        while text:
            if text.startswith("--"):
                newline = text.find("\n")
                text = "" if newline < 0 else text[newline + 1:].lstrip()
                continue
            if text.startswith("/*"):
                end = text.find("*/")
                text = "" if end < 0 else text[end + 2:].lstrip()
                continue
            break
        if not text:
            return False
        first = re.match(r"[A-Za-z]+", text)
        if first is not None and first.group(0).upper() == "EXPLAIN":
            return False
        match = re.search(
            r"\binsert\s+(?:or\s+\w+\s+)?into\s+"
            r"(?:main\s*\.\s*)?([A-Za-z_][A-Za-z0-9_]*)\b",
            text,
            re.IGNORECASE,
        )
        return (
            match is not None
            and match.group(1).lower() == self.fault_table
        )

    def _s2mrw_fault_should_arm(self, sql, parameters=None):
        # Single point: one armed trial injects at most one statement, on
        # whichever path (connection or cursor) actually executes it; the
        # activation is disclosed before the statement is forwarded.
        if not (
            self.fault_armed
            and self.fault_table
            and self._s2mrw_fault_targets(sql)
        ):
            return False
        self.fault_armed = False
        self.injections.append(
            {
                "sql": sql,
                "parameters": parameters,
                "mode": "authorizer-ignore",
            }
        )
        return True

    def execute(self, sql, parameters=()):
        if self._s2mrw_fault_should_arm(sql, parameters):
            self.set_authorizer(self._s2mrw_fault_authorizer)
            try:
                return super().execute(sql, parameters)
            finally:
                self.set_authorizer(None)
        return super().execute(sql, parameters)

    def _s2mrw_fault_authorizer(self, action, arg1, arg2, database, trigger):
        if action == self.SQLITE_INSERT and arg1 == self.fault_table:
            return self.SQLITE_IGNORE
        return self.SQLITE_OK


if __name__ == "__main__":
    unittest.main()
