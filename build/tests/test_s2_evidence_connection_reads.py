# -*- coding: utf-8 -*-
"""S2 coding evidence borrowed reads (dual actor/owner conn) regression.

Contract source: dispatch brief for coding_evidence four reads. Only new test
symbols; no product/test changed. Import time creates no DB and runs no business.
Real temporary SQLite; existing coding_evidence.migrate path API for base schema;
caller connections are native sqlite3.Connection with sqlite3.Row.

Coverage:
  1. Path vs dual-conn parity for field_rows/mapping/evidence_state/complete_keys.
  2. Owner=actor same connection accepted.
  3. Borrowed reads see uncommitted actor values/notes and owner policy changes;
     caller rollback removes them.
  4. Probe connections: bogus db paths ignored; no FS/connect/PRAGMA/DDL/write/
     transaction-control/row_factory change; only SELECT statements.
  5. Partial pair, non-native, missing transaction and non-Row factory rejected.
  6. mapping JSON errors are not swallowed; field_rows still works.
  7. complete_keys path mapping-issues returns before opening the actor connection.
  8. sqlite3.ProgrammingError propagates in borrowed mode.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from coscreen import coding_evidence as ce  # noqa: E402


def _sha256_file(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _open_borrowed(store, *, factory=None) -> sqlite3.Connection:
    kwargs = {"timeout": 15}
    if factory is not None:
        kwargs["factory"] = factory
    conn = sqlite3.connect(str(store), **kwargs)
    conn.row_factory = sqlite3.Row
    conn.execute("BEGIN IMMEDIATE")
    return conn


def _make_store(path, *, prefix="D", required=("f1",)) -> Path:
    """Create owner/actor evidence DB fixture with two dimensions."""
    ce.migrate(path)
    with closing(sqlite3.connect(str(path), timeout=15)) as conn:
        conn.execute(
            "CREATE TABLE coding_dimensions("
            "id INTEGER PRIMARY KEY, name TEXT, dtype TEXT, options_json TEXT, "
            "multi_select INTEGER, unit TEXT, description TEXT, section TEXT, "
            "position INTEGER, created_at TEXT)")
        conn.execute(
            "CREATE TABLE coding_values("
            "dimension_id INTEGER, zotero_key TEXT, value TEXT, updated_at TEXT)")
        conn.execute(
            "CREATE TABLE coding_notes("
            "id INTEGER PRIMARY KEY, dimension_id INTEGER, zotero_key TEXT, text TEXT, "
            "created_at TEXT, updated_at TEXT)")
        conn.execute(
            "CREATE TABLE coding_note_images("
            "id INTEGER PRIMARY KEY, note_id INTEGER, path TEXT, sha256 TEXT, "
            "ocr_text TEXT, ocr_status TEXT, created_at TEXT)")
        conn.executemany(
            "INSERT INTO coding_dimensions VALUES(?,?,?,?,?,?,?,?,?,?)",
            [
                (1, f"{prefix}-A", "text", "[]", 0, "", "", "s", 1,
                 "2026-01-01T00:00:00Z"),
                (2, f"{prefix}-B", "text", "[]", 0, "", "", "s", 2,
                 "2026-01-01T00:00:00Z"),
            ],
        )
        conn.executemany(
            "INSERT OR REPLACE INTO workflow_coding_fields"
            "(dimension_id,field_id,definition_json) VALUES(?,?,?)",
            [(1, "f1", "{}"), (2, "f2", "{}")],
        )
        conn.commit()
        dims = ce.field_rows(path)
        for dim in dims:
            conn.execute(
                "UPDATE workflow_coding_fields SET definition_json=? "
                "WHERE dimension_id=?", (ce.definition(dim), dim["id"]))
        conn.execute(
            "UPDATE workflow_state SET evidence_json=? WHERE singleton=1",
            (json.dumps(list(required)),))
        by_field = {dim.get("field_id"): dim for dim in dims}
        for fid in required:
            conn.execute(
                "INSERT OR REPLACE INTO workflow_evidence_requirements"
                "(field_id,definition_json) VALUES(?,?)",
                (fid, ce.definition(by_field[fid])),
            )
        conn.executemany(
            "INSERT INTO coding_values VALUES(?,?,?,?)",
            [
                (1, "K1", "yes", "2026-01-01T00:00:00Z"),
                (2, "K1", "no", "2026-01-01T00:00:00Z"),
                (1, "K2", "yes", "2026-01-01T00:00:00Z"),
            ],
        )
        conn.executemany(
            "INSERT INTO coding_notes VALUES(?,?,?,?,?,?)",
            [
                (1, 1, "K1", "note", "2026-01-01T00:00:00Z",
                 "2026-01-01T00:00:00Z"),
                (2, 1, "K2", "", "2026-01-01T00:00:00Z",
                 "2026-01-01T00:00:00Z"),
            ],
        )
        conn.commit()
    return path


class _ProbeConnection(sqlite3.Connection):
    """Native connection recording SQL and explicit control calls."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.statements: list[str] = []
        self.commit_calls = 0
        self.rollback_calls = 0
        self.close_calls = 0
        self.executescript_calls = 0
        self.set_trace_callback(self.statements.append)

    def reset_probe(self) -> None:
        self.commit_calls = 0
        self.rollback_calls = 0
        self.close_calls = 0
        self.executescript_calls = 0
        self.statements.clear()

    def commit(self):
        self.commit_calls += 1
        return super().commit()

    def rollback(self):
        self.rollback_calls += 1
        return super().rollback()

    def close(self):
        self.close_calls += 1
        return super().close()

    def executescript(self, script):
        self.executescript_calls += 1
        return super().executescript(script)


class _ProgrammingErrorConnection(sqlite3.Connection):
    def execute(self, sql, parameters=()):
        if "FROM workflow_state" in sql:
            raise sqlite3.ProgrammingError("injected evidence read programming error")
        return super().execute(sql, parameters)


class EvidenceConnectionReadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory(prefix="rf216_evidence_reads_")
        cls.root = Path(cls._tmp.name)
        cls.addClassCleanup(cls._tmp.cleanup)
        print("S2_EVIDENCE_READ_TEST_SHA", _sha256_file(__file__))
        print("S2_EVIDENCE_READ_PRODUCT_SHA", _sha256_file(ce.__file__))
        print("S2_EVIDENCE_READ_ISOLATED", sys.flags.isolated)
        print("S2_EVIDENCE_READ_DONT_WRITE_BC", sys.flags.dont_write_bytecode)

    def setUp(self):
        self.base = self.root / self.id().rsplit(".", 1)[-1]
        self.base.mkdir(parents=True, exist_ok=True)

    def _pair(self, *, owner_prefix="D", actor_prefix=None):
        actor_prefix = owner_prefix if actor_prefix is None else actor_prefix
        owner = _make_store(self.base / "owner.db", prefix=owner_prefix)
        actor = _make_store(self.base / "actor.db", prefix=actor_prefix)
        return actor, owner

    def test_01_path_and_dual_conn_parity(self):
        actor, owner = self._pair()
        path_fields = ce.field_rows(actor)
        path_mapping = ce.mapping(actor, owner)
        path_state = ce.evidence_state(actor, "K1", owner, task_id="task-1")
        path_complete = ce.complete_keys(actor, owner, ["K1", "K2"])

        self.assertEqual(path_mapping[1], [])
        self.assertEqual(path_mapping[2], ["f1"])
        self.assertEqual(path_state["evidence_complete"], True)
        self.assertEqual(path_complete, {"K1"})

        owner_conn = _open_borrowed(owner)
        actor_conn = _open_borrowed(actor)
        try:
            self.assertEqual(ce.field_rows(actor, conn=actor_conn), path_fields)
            self.assertEqual(
                ce.mapping(actor, owner, actor_conn=actor_conn,
                           owner_conn=owner_conn),
                path_mapping,
            )
            self.assertEqual(
                ce.evidence_state(actor, "K1", owner, task_id="task-1",
                                  actor_conn=actor_conn, owner_conn=owner_conn),
                path_state,
            )
            self.assertEqual(
                ce.complete_keys(actor, owner, ["K1", "K2"],
                                 actor_conn=actor_conn, owner_conn=owner_conn),
                path_complete,
            )
            self.assertTrue(actor_conn.in_transaction)
            self.assertTrue(owner_conn.in_transaction)
        finally:
            actor_conn.rollback()
            actor_conn.close()
            owner_conn.rollback()
            owner_conn.close()

    def test_02_owner_equals_actor_same_connection(self):
        owner = _make_store(self.base / "same.db")
        conn = _open_borrowed(owner)
        try:
            self.assertEqual(
                ce.mapping(owner, owner, actor_conn=conn, owner_conn=conn),
                ce.mapping(owner, owner),
            )
            self.assertEqual(
                ce.evidence_state(owner, "K1", owner, task_id="task-1",
                                  actor_conn=conn, owner_conn=conn),
                ce.evidence_state(owner, "K1", owner, task_id="task-1"),
            )
            self.assertEqual(
                ce.complete_keys(owner, owner, ["K1", "K2"],
                                 actor_conn=conn, owner_conn=conn),
                ce.complete_keys(owner, owner, ["K1", "K2"]),
            )
        finally:
            conn.rollback()
            conn.close()

    def test_03_borrowed_sees_uncommitted_and_rollback_reverts(self):
        actor, owner = self._pair()
        old_mapping = ce.mapping(actor, owner)
        old_state = ce.evidence_state(actor, "K2", owner, task_id="task-1")
        old_complete = ce.complete_keys(actor, owner, ["K1", "K2"])

        actor_conn = _open_borrowed(actor)
        owner_conn = _open_borrowed(owner)
        try:
            actor_conn.execute(
                "INSERT INTO coding_values VALUES(2,'K2','no','2026-01-02T00:00:00Z')")
            actor_conn.execute(
                "UPDATE coding_notes SET text='new note' WHERE dimension_id=1 "
                "AND zotero_key='K2'")
            self.assertEqual(
                ce.evidence_state(actor, "K2", owner, task_id="task-1",
                                  actor_conn=actor_conn, owner_conn=owner_conn)
                ["evidence_complete"],
                True,
            )
            self.assertEqual(
                ce.complete_keys(actor, owner, ["K1", "K2"],
                                 actor_conn=actor_conn, owner_conn=owner_conn),
                {"K1", "K2"},
            )
            owner_conn.execute(
                "UPDATE workflow_state SET evidence_json=? WHERE singleton=1",
                (json.dumps(["f1", "f2"]),))
            self.assertEqual(
                ce.mapping(actor, owner, actor_conn=actor_conn,
                           owner_conn=owner_conn)[2],
                ["f1", "f2"],
            )
            self.assertTrue(actor_conn.in_transaction)
            self.assertTrue(owner_conn.in_transaction)
        finally:
            actor_conn.rollback()
            actor_conn.close()
            owner_conn.rollback()
            owner_conn.close()

        self.assertEqual(ce.mapping(actor, owner), old_mapping)
        self.assertEqual(
            ce.evidence_state(actor, "K2", owner, task_id="task-1"), old_state)
        self.assertEqual(ce.complete_keys(actor, owner, ["K1", "K2"]), old_complete)

    def test_04_probe_ignores_fs_and_never_controls_connection(self):
        actor, owner = self._pair()
        expected_fields = ce.field_rows(actor)
        expected_mapping = ce.mapping(actor, owner)
        expected_state = ce.evidence_state(actor, "K1", owner, task_id="task-1")
        expected_complete = ce.complete_keys(actor, owner, ["K1", "K2"])
        actor_missing = self.base / "missing-actor.db"
        owner_missing = self.base / "missing-owner.db"

        owner_conn = _open_borrowed(owner, factory=_ProbeConnection)
        actor_conn = _open_borrowed(actor, factory=_ProbeConnection)
        owner_conn.reset_probe()
        actor_conn.reset_probe()
        try:
            self.assertEqual(ce.field_rows(actor_missing, conn=actor_conn),
                             expected_fields)
            self.assertEqual(
                ce.mapping(actor_missing, owner_missing, actor_conn=actor_conn,
                           owner_conn=owner_conn),
                expected_mapping,
            )
            self.assertEqual(
                ce.evidence_state(actor_missing, "K1", owner_missing,
                                  task_id="task-1", actor_conn=actor_conn,
                                  owner_conn=owner_conn),
                expected_state,
            )
            self.assertEqual(
                ce.complete_keys(actor_missing, owner_missing, ["K1", "K2"],
                                 actor_conn=actor_conn, owner_conn=owner_conn),
                expected_complete,
            )
            for conn in (owner_conn, actor_conn):
                self.assertEqual(conn.commit_calls, 0)
                self.assertEqual(conn.rollback_calls, 0)
                self.assertEqual(conn.close_calls, 0)
                self.assertEqual(conn.executescript_calls, 0)
                self.assertTrue(conn.in_transaction)
                self.assertIs(conn.row_factory, sqlite3.Row)
                self.assertTrue(conn.statements)
                for statement in conn.statements:
                    self.assertTrue(
                        statement.lstrip().upper().startswith("SELECT"),
                        f"borrowed read executed non-SELECT SQL: {statement!r}",
                    )
            self.assertFalse(actor_missing.exists())
            self.assertFalse(owner_missing.exists())
        finally:
            actor_conn.rollback()
            actor_conn.close()
            owner_conn.rollback()
            owner_conn.close()

    def test_05_contract_rejects_partial_non_native_no_txn_non_row(self):
        actor, owner = self._pair()
        conn = _open_borrowed(actor)
        try:
            with self.assertRaises(ValueError):
                ce.mapping(actor, owner, actor_conn=conn)
            with self.assertRaises(ValueError):
                ce.mapping(actor, owner, owner_conn=conn)
            with self.assertRaises(ValueError):
                ce.evidence_state(actor, "K1", owner, actor_conn=conn)
            with self.assertRaises(ValueError):
                ce.complete_keys(actor, owner, ["K1"], owner_conn=conn)
            for call in (
                lambda: ce.field_rows(actor, conn=object()),
                lambda: ce.mapping(actor, owner, actor_conn=object(),
                                   owner_conn=object()),
                lambda: ce.evidence_state(actor, "K1", owner,
                                          actor_conn=object(), owner_conn=object()),
                lambda: ce.complete_keys(actor, owner, ["K1"],
                                         actor_conn=object(), owner_conn=object()),
            ):
                with self.assertRaises(RuntimeError):
                    call()
        finally:
            conn.rollback()
            conn.close()

        owner_conn = sqlite3.connect(str(owner), timeout=15)
        owner_conn.row_factory = sqlite3.Row
        actor_conn = sqlite3.connect(str(actor), timeout=15)
        actor_conn.row_factory = sqlite3.Row
        try:
            with self.assertRaises(RuntimeError):
                ce.field_rows(actor, conn=actor_conn)
            with self.assertRaises(RuntimeError):
                ce.mapping(actor, owner, actor_conn=actor_conn,
                           owner_conn=owner_conn)
            self.assertFalse(actor_conn.in_transaction)
            self.assertFalse(owner_conn.in_transaction)
        finally:
            actor_conn.close()
            owner_conn.close()

        owner_conn = sqlite3.connect(str(owner), timeout=15)
        actor_conn = sqlite3.connect(str(actor), timeout=15)
        owner_conn.execute("BEGIN IMMEDIATE")
        actor_conn.execute("BEGIN IMMEDIATE")
        try:
            with self.assertRaises(RuntimeError):
                ce.field_rows(actor, conn=actor_conn)
            with self.assertRaises(RuntimeError):
                ce.mapping(actor, owner, actor_conn=actor_conn,
                           owner_conn=owner_conn)
            self.assertTrue(actor_conn.in_transaction)
            self.assertTrue(owner_conn.in_transaction)
            self.assertIsNone(actor_conn.row_factory)
            self.assertIsNone(owner_conn.row_factory)
        finally:
            actor_conn.rollback()
            actor_conn.close()
            owner_conn.rollback()
            owner_conn.close()

    def test_06_mapping_json_error_not_swallowed(self):
        actor, owner = self._pair()
        with closing(sqlite3.connect(str(owner), timeout=15)) as conn:
            conn.execute(
                "UPDATE workflow_state SET stages_json='not-json' WHERE singleton=1")
            conn.commit()

        self.assertTrue(ce.field_rows(actor))
        for call in (
            lambda: ce.mapping(actor, owner),
            lambda: ce.evidence_state(actor, "K1", owner),
            lambda: ce.complete_keys(actor, owner, ["K1"]),
        ):
            with self.assertRaises(json.JSONDecodeError):
                call()

        owner_conn = _open_borrowed(owner)
        actor_conn = _open_borrowed(actor)
        try:
            self.assertTrue(ce.field_rows(actor, conn=actor_conn))
            for call in (
                lambda: ce.mapping(actor, owner, actor_conn=actor_conn,
                                   owner_conn=owner_conn),
                lambda: ce.evidence_state(actor, "K1", owner,
                                          actor_conn=actor_conn,
                                          owner_conn=owner_conn),
                lambda: ce.complete_keys(actor, owner, ["K1"],
                                         actor_conn=actor_conn,
                                         owner_conn=owner_conn),
            ):
                with self.assertRaises(json.JSONDecodeError):
                    call()
            self.assertTrue(actor_conn.in_transaction)
            self.assertTrue(owner_conn.in_transaction)
        finally:
            actor_conn.rollback()
            actor_conn.close()
            owner_conn.rollback()
            owner_conn.close()

    def test_07_complete_keys_mapping_issues_returns_before_extra_connect(self):
        actor, owner = self._pair(owner_prefix="Owner", actor_prefix="Actor")
        self.assertTrue(ce.mapping(actor, owner)[1])

        with mock.patch.object(ce.workflow, "connect",
                               wraps=ce.workflow.connect) as connect_mock:
            ce.mapping(actor, owner)
            baseline = connect_mock.call_count
            connect_mock.reset_mock()
            self.assertEqual(ce.complete_keys(actor, owner, ["K1"]), set())
            self.assertEqual(connect_mock.call_count, baseline)

    def test_08_programming_error_propagates(self):
        actor, owner = self._pair()
        owner_conn = _open_borrowed(owner, factory=_ProgrammingErrorConnection)
        actor_conn = _open_borrowed(actor, factory=_ProgrammingErrorConnection)
        try:
            self.assertTrue(ce.field_rows(actor, conn=actor_conn))
            for call in (
                lambda: ce.mapping(actor, owner, actor_conn=actor_conn,
                                   owner_conn=owner_conn),
                lambda: ce.evidence_state(actor, "K1", owner,
                                          actor_conn=actor_conn,
                                          owner_conn=owner_conn),
                lambda: ce.complete_keys(actor, owner, ["K1"],
                                         actor_conn=actor_conn,
                                         owner_conn=owner_conn),
            ):
                with self.assertRaises(sqlite3.ProgrammingError):
                    call()
            self.assertTrue(actor_conn.in_transaction)
            self.assertTrue(owner_conn.in_transaction)
        finally:
            actor_conn.rollback()
            actor_conn.close()
            owner_conn.rollback()
            owner_conn.close()


if __name__ == "__main__":
    unittest.main()
