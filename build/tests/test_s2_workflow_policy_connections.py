# -*- coding: utf-8 -*-
"""S2 workflow policy borrowed reads (conn= seam) independent regression.

Contract: `.superpowers/sdd/2026-11-14-2.1.6-maintenance/s2b-policy-conn-brief.md`.
Only new test symbols; no existing product/test changed. Import time creates no
database and runs no business operation. Fixtures use real temporary SQLite and
the existing review_workflow.migrate path API for schema setup; borrowed-mode
tests use native connections with sqlite3.Row.

Coverage:
  1. Missing DB path keeps all four old path defaults and creates no file.
  2. Empty schema keeps path/borrowed defaults (settings/sources/links/revision).
  3. Committed policy/sources/links/catalog are byte-for-byte equal in path and
     borrowed modes; decorator fields, order, bool/reserved and int conversion
     are preserved.
  4. Borrowed reads see same-transaction uncommitted policy/source/link/catalog
     changes; caller rollback removes them, proving no hidden commit.
  5. Native probe connection: bogus db path is ignored, no FS/connect/PRAGMA/
     DDL/write/transaction-control/row_factory change, only SELECT statements.
  6. Non-native, missing active transaction and non-Row factory are rejected
     with RuntimeError without altering the connection.
  7. Invalid persisted settings JSON is not swallowed by settings, sources or
     catalog_revision in either mode.
  8. sqlite3.ProgrammingError propagates unchanged in borrowed mode.
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

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from coscreen import review_workflow as wf  # noqa: E402

STAGES_JSON = {
    "screen": {"mode": "selected", "source_ids": ["s1"]},
    "fulltext": {"mode": "all", "source_ids": []},
    "coding": {"mode": "paused", "source_ids": ["s2"]},
}
EVIDENCE_JSON = ["f1", "f2"]


def _sha256_file(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _default_settings() -> dict:
    return {
        "version": 1,
        "stages": wf.defaults(),
        "evidence_required_field_ids": [],
        "team_latched": False,
        "catalog_revision": 0,
    }


def _default_sources() -> list[dict]:
    return [{"id": wf.UNASSIGNED, "name": "Unassigned", "active": True, "reserved": True}]


def _open_borrowed(store, *, factory=None) -> sqlite3.Connection:
    kwargs = {"timeout": 15}
    if factory is not None:
        kwargs["factory"] = factory
    conn = sqlite3.connect(str(store), **kwargs)
    conn.row_factory = sqlite3.Row
    conn.execute("BEGIN IMMEDIATE")
    return conn


def _seed_policy(store) -> None:
    """Create realistic committed policy/source/link/catalog state."""
    wf.migrate(store)
    with closing(sqlite3.connect(str(store), timeout=15)) as conn:
        conn.execute("CREATE TABLE articles (zotero_key TEXT PRIMARY KEY, is_duplicate_of TEXT)")
        conn.executemany(
            "INSERT INTO articles(zotero_key,is_duplicate_of) VALUES(?,?)",
            [("K1", None), ("K2", None)],
        )
        conn.execute(
            "CREATE TABLE workflow_source_details("
            "source_id TEXT PRIMARY KEY, identification TEXT NOT NULL, source_type TEXT, "
            "provider TEXT NOT NULL DEFAULT '', suggestion TEXT NOT NULL DEFAULT '', "
            "evidence_json TEXT NOT NULL, identity TEXT NOT NULL)")
        conn.execute(
            "CREATE TABLE workflow_source_records("
            "source_id TEXT NOT NULL, filename TEXT NOT NULL)")
        conn.executemany(
            "INSERT INTO workflow_sources(id,name,normalized_name,active,created_at) "
            "VALUES(?,?,?,?,?)",
            [
                ("s1", "One", "one", 1, "2026-01-01T00:00:00Z"),
                ("s2", "Two", "two", 0, "2026-01-02T00:00:00Z"),
            ],
        )
        conn.execute(
            "INSERT INTO workflow_source_details VALUES(?,?,?,?,?,?,?)",
            ("s1", "confirmed", "database", "Provider", "Suggestion", "{}", "identity:s1"),
        )
        conn.execute("INSERT INTO workflow_source_records VALUES(?,?)", ("s1", "one.ris"))
        conn.execute("INSERT INTO workflow_article_sources VALUES(?,?)", ("K1", "s1"))
        conn.execute("INSERT INTO workflow_article_sources VALUES(?,?)", ("K2", "s2"))
        conn.execute(
            "UPDATE workflow_state SET stages_json=?, evidence_json=?, "
            "team_latched=1, catalog_revision=7 WHERE singleton=1",
            (json.dumps(STAGES_JSON), json.dumps(EVIDENCE_JSON)),
        )
        conn.commit()


class _ProbeConnection(sqlite3.Connection):
    """Native connection that records SQL and explicit control calls."""

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
    """Native connection that rejects reads of the four policy tables."""

    def execute(self, sql, parameters=()):
        if any(token in sql for token in (
                "FROM workflow_state",
                "FROM workflow_sources",
                "FROM workflow_article_sources")):
            raise sqlite3.ProgrammingError("injected policy read programming error")
        return super().execute(sql, parameters)


class PolicyConnectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory(prefix="rf216_policy_conn_")
        cls.root = Path(cls._tmp.name)
        cls.addClassCleanup(cls._tmp.cleanup)
        print("S2_POLICY_CONN_TEST_SHA", _sha256_file(__file__))
        print("S2_POLICY_CONN_PRODUCT_SHA", _sha256_file(wf.__file__))
        print("S2_POLICY_CONN_ISOLATED", sys.flags.isolated)
        print("S2_POLICY_CONN_DONT_WRITE_BC", sys.flags.dont_write_bytecode)

    def setUp(self):
        self.base = self.root / self.id().rsplit(".", 1)[-1]
        self.base.mkdir(parents=True, exist_ok=True)

    def _store(self, name="policy.db", *, seed=True):
        store = self.base / name
        if seed:
            _seed_policy(store)
        return store

    def test_01_missing_database_path_defaults_without_creation(self):
        missing = self.base / "missing.db"
        self.assertEqual(wf.settings(missing), _default_settings())
        self.assertEqual(wf.sources(missing), _default_sources())
        self.assertEqual(wf.source_links(missing), {})
        self.assertEqual(wf.catalog_revision(missing), 0)
        self.assertFalse(missing.exists())

    def test_02_empty_schema_defaults_path_and_borrowed(self):
        empty = self.base / "empty.db"
        sqlite3.connect(str(empty)).close()
        self.assertEqual(wf.settings(empty), _default_settings())
        self.assertEqual(wf.sources(empty), _default_sources())
        self.assertEqual(wf.source_links(empty), {})
        self.assertEqual(wf.catalog_revision(empty), 0)

        conn = _open_borrowed(empty)
        try:
            self.assertEqual(wf.settings(empty, conn=conn), _default_settings())
            self.assertEqual(wf.sources(empty, conn=conn), _default_sources())
            self.assertEqual(wf.source_links(empty, conn=conn), {})
            self.assertEqual(wf.catalog_revision(empty, conn=conn), 0)
            self.assertTrue(conn.in_transaction)
        finally:
            conn.rollback()
            conn.close()

    def test_03_path_and_borrowed_committed_values_match(self):
        store = self._store()
        path_settings = wf.settings(store)
        path_sources = wf.sources(store)
        path_links = wf.source_links(store)
        path_revision = wf.catalog_revision(store)

        self.assertEqual(path_settings, {
            "version": 1,
            "stages": STAGES_JSON,
            "evidence_required_field_ids": EVIDENCE_JSON,
            "team_latched": True,
            "catalog_revision": 7,
        })
        self.assertIs(type(path_revision), int)
        self.assertEqual(path_revision, 7)
        self.assertEqual([source["id"] for source in path_sources], ["s1", "s2"])
        self.assertEqual(path_links, {"K1": {"s1"}, "K2": {"s2"}})

        s1, s2 = path_sources
        self.assertIs(s1["active"], True)
        self.assertIs(s1["reserved"], False)
        self.assertEqual(s1["identification"], "confirmed")
        self.assertEqual(s1["source_type"], "database")
        self.assertEqual(s1["record_count"], 1)
        self.assertEqual(s1["linked_count"], 1)
        self.assertEqual(s1["files"], ["one.ris"])
        self.assertIs(s2["active"], False)
        self.assertIs(s2["reserved"], False)
        self.assertEqual(s2["identification"], "legacy")
        self.assertEqual(s2["record_count"], 0)
        self.assertEqual(s2["linked_count"], 1)
        self.assertEqual(s2["files"], [])

        conn = _open_borrowed(store)
        try:
            self.assertEqual(wf.settings(store, conn=conn), path_settings)
            self.assertEqual(wf.sources(store, conn=conn), path_sources)
            self.assertEqual(wf.source_links(store, conn=conn), path_links)
            self.assertEqual(wf.catalog_revision(store, conn=conn), path_revision)
            self.assertTrue(conn.in_transaction)
        finally:
            conn.rollback()
            conn.close()

    def test_04_borrowed_sees_uncommitted_and_rollback_reverts(self):
        store = self._store()
        old_settings = wf.settings(store)
        old_sources = wf.sources(store)
        old_links = wf.source_links(store)
        old_revision = wf.catalog_revision(store)

        conn = _open_borrowed(store)
        try:
            conn.execute("INSERT INTO articles VALUES('K3',NULL)")
            conn.execute(
                "INSERT INTO workflow_sources VALUES(?,?,?,?,?)",
                ("s3", "Three", "three", 1, "2026-01-03T00:00:00Z"),
            )
            conn.execute("INSERT INTO workflow_article_sources VALUES(?,?)", ("K3", "s3"))
            new_stages = {
                "screen": {"mode": "selected", "source_ids": ["s1", "s3"]},
                "fulltext": {"mode": "all", "source_ids": []},
                "coding": {"mode": "paused", "source_ids": ["s2"]},
            }
            conn.execute(
                "UPDATE workflow_state SET stages_json=?, evidence_json=?, "
                "team_latched=0, catalog_revision=8 WHERE singleton=1",
                (json.dumps(new_stages), json.dumps(["f3"])),
            )

            observed_settings = wf.settings(store, conn=conn)
            observed_sources = wf.sources(store, conn=conn)
            observed_links = wf.source_links(store, conn=conn)
            observed_revision = wf.catalog_revision(store, conn=conn)

            self.assertEqual(observed_settings["catalog_revision"], 8)
            self.assertIs(observed_settings["team_latched"], False)
            self.assertEqual(observed_settings["evidence_required_field_ids"], ["f3"])
            self.assertEqual(
                observed_settings["stages"]["screen"]["source_ids"], ["s1", "s3"])
            self.assertIn("s3", [source["id"] for source in observed_sources])
            self.assertEqual(observed_links.get("K3"), {"s3"})
            self.assertEqual(observed_revision, 8)
            self.assertTrue(conn.in_transaction)
        finally:
            conn.rollback()
            conn.close()

        self.assertEqual(wf.settings(store), old_settings)
        self.assertEqual(wf.sources(store), old_sources)
        self.assertEqual(wf.source_links(store), old_links)
        self.assertEqual(wf.catalog_revision(store), old_revision)
        self.assertNotIn("K3", wf.source_links(store))

    def test_05_borrowed_probe_ignores_fs_and_never_controls_connection(self):
        store = self._store()
        expected_settings = wf.settings(store)
        expected_sources = wf.sources(store)
        expected_links = wf.source_links(store)
        expected_revision = wf.catalog_revision(store)
        missing = self.base / "must-not-be-opened.db"
        conn = _open_borrowed(store, factory=_ProbeConnection)
        conn.reset_probe()
        try:
            self.assertEqual(wf.settings(missing, conn=conn), expected_settings)
            self.assertEqual(wf.sources(missing, conn=conn), expected_sources)
            self.assertEqual(wf.source_links(missing, conn=conn), expected_links)
            self.assertEqual(wf.catalog_revision(missing, conn=conn), expected_revision)

            self.assertEqual(conn.commit_calls, 0)
            self.assertEqual(conn.rollback_calls, 0)
            self.assertEqual(conn.close_calls, 0)
            self.assertEqual(conn.executescript_calls, 0)
            self.assertTrue(conn.in_transaction)
            self.assertIs(conn.row_factory, sqlite3.Row)
            self.assertFalse(missing.exists())
            self.assertTrue(conn.statements)
            for statement in conn.statements:
                self.assertTrue(
                    statement.lstrip().upper().startswith("SELECT"),
                    f"borrowed read executed non-SELECT SQL: {statement!r}",
                )
        finally:
            conn.rollback()
            conn.close()

    def test_06_borrowed_requires_native_active_transaction_and_row_factory(self):
        store = self._store()
        apis = (wf.settings, wf.sources, wf.source_links, wf.catalog_revision)

        for api in apis:
            with self.subTest(api=api.__name__, case="non-native-object"):
                with self.assertRaises(RuntimeError):
                    api(store, conn=object())
            with self.subTest(api=api.__name__, case="non-native-string"):
                with self.assertRaises(RuntimeError):
                    api(store, conn="not-a-connection")

        conn = sqlite3.connect(str(store), timeout=15)
        conn.row_factory = sqlite3.Row
        try:
            self.assertFalse(conn.in_transaction)
            for api in apis:
                with self.subTest(api=api.__name__, case="missing-transaction"):
                    with self.assertRaises(RuntimeError):
                        api(store, conn=conn)
            self.assertFalse(conn.in_transaction)
            self.assertIs(conn.row_factory, sqlite3.Row)
        finally:
            conn.close()

        conn = sqlite3.connect(str(store), timeout=15)
        conn.execute("BEGIN IMMEDIATE")
        try:
            for api in apis:
                with self.subTest(api=api.__name__, case="tuple-row-factory"):
                    with self.assertRaises(RuntimeError):
                        api(store, conn=conn)
            self.assertTrue(conn.in_transaction)
            self.assertIsNone(conn.row_factory)
        finally:
            conn.rollback()
            conn.close()

        conn = sqlite3.connect(str(store), timeout=15)
        conn.row_factory = lambda cursor, row: row
        conn.execute("BEGIN IMMEDIATE")
        try:
            for api in apis:
                with self.subTest(api=api.__name__, case="custom-row-factory"):
                    with self.assertRaises(RuntimeError):
                        api(store, conn=conn)
            self.assertTrue(conn.in_transaction)
        finally:
            conn.rollback()
            conn.close()

    def test_07_settings_and_catalog_json_errors_not_swallowed(self):
        store = self._store()
        with closing(sqlite3.connect(str(store), timeout=15)) as conn:
            conn.execute(
                "UPDATE workflow_state SET stages_json='not-json' WHERE singleton=1")
            conn.commit()

        for api in (wf.settings, wf.catalog_revision, wf.sources):
            with self.subTest(api=api.__name__, mode="path"):
                with self.assertRaises(json.JSONDecodeError):
                    api(store)

        conn = _open_borrowed(store)
        try:
            for api in (wf.settings, wf.catalog_revision, wf.sources):
                with self.subTest(api=api.__name__, mode="borrowed"):
                    with self.assertRaises(json.JSONDecodeError):
                        api(store, conn=conn)
            self.assertTrue(conn.in_transaction)
        finally:
            conn.rollback()
            conn.close()

    def test_08_programming_error_propagates(self):
        store = self._store()
        conn = _open_borrowed(store, factory=_ProgrammingErrorConnection)
        try:
            for api in (wf.settings, wf.sources, wf.source_links, wf.catalog_revision):
                with self.subTest(api=api.__name__):
                    with self.assertRaises(sqlite3.ProgrammingError):
                        api(store, conn=conn)
            self.assertTrue(conn.in_transaction)
        finally:
            conn.rollback()
            conn.close()


if __name__ == "__main__":
    unittest.main()
