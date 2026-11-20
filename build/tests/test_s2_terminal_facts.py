"""Independent F1 terminal-facts storage tests (static freeze; not run yet).

Authority: .superpowers/sdd/2026-11-14-2.1.6-maintenance/terminal-f1-storage-brief-20261116.md
(2026-11-16).  This file is test-only.  It uses real SQLite databases and the
five public functions from coscreen.team_terminal_facts; it does not mock the
storage core, import product internals to compute expectations, or touch real
data.  Expected canonical JSON and hashes are computed below with the standard
library only.

Contract clarifications used in this corrected draft:
- source_request.operation is fixed and intentionally differs from trigger:
  last_vote -> vote_save; arbitration -> arbitration_finish;
  roster_change -> roster_change.  Tests never use trigger text as operation.
- prepare_team_terminal_facts on a missing file raises FileNotFoundError and does
  not create it; an existing bad layout raises typed schema_mismatch.
- Idempotent append/archive requires matching origin; same content with a
  different origin is Conflict, never a silent re-origin.  In particular archive
  never converts a target's existing actor original into actor_archive.
- UID ordering is a hard payload rule: roster, required_uids, and opinions must
  already be UID-ascending; bad order is PayloadInvalid, not silently sorted.
- task_instance_id is opaque non-empty text, not a UUID shape.
- The optional prepare schema/version marker is treated as implementation
  private; tests do not assume a marker name.  Wrong table and missing trigger
  cases cover partial layout.
- append schema/trigger validation is not separately assumed; read/list/prepare
  enforce the visible layout/trigger contract.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import sqlite3
import tempfile
import unittest
from contextlib import closing, contextmanager
from pathlib import Path
from unittest import mock

from coscreen import team_terminal_facts as tf


STAMP = "2026-11-16T10:00:00.000Z"
STAMP2 = "2026-11-16T10:00:01.000Z"
REGISTRATION = "registration-1"
OPERATION_BY_TRIGGER = {
    "last_vote": "vote_save",
    "arbitration": "arbitration_finish",
    "roster_change": "roster_change",
}
RECORD_KEYS = {
    "payload", "seal_id", "content_sha256", "payload_sha256",
    "origin", "sealed_at", "archived_at",
}
TABLE_COLUMNS = {
    "seal_id", "task_instance_id", "stage", "item_key", "kind",
    "final_decision", "content_sha256", "payload_sha256", "sealed_at",
    "archived_at", "origin", "payload_json",
}


class _RollbackFailConnection(sqlite3.Connection):
    """Targeted prepare-cleanup stand-in: real connection, injected cleanup faults.

    This is deliberately not a real disk/IO fault; it proves only that rollback
    and close failures remain observable and that close is still attempted.
    """

    close_calls = 0
    rollback_failures = 0
    close_failures = 0
    body_failures = 0
    fail_rollback = True
    fail_close = False
    body_error = None
    body_error_on = "SELECT"

    def execute(self, sql, *args, **kwargs):
        text = str(sql).strip().upper()
        if (type(self).body_error is not None
                and type(self).body_error_on
                and text.startswith(type(self).body_error_on)):
            type(self).body_failures += 1
            raise type(self).body_error
        if (type(self).fail_rollback and text == "ROLLBACK"):
            type(self).rollback_failures += 1
            raise sqlite3.OperationalError("injected rollback failure")
        return super().execute(sql, *args, **kwargs)

    def close(self):
        type(self).close_calls += 1
        result = super().close()
        if type(self).fail_close:
            type(self).close_failures += 1
            raise sqlite3.OperationalError("injected close failure")
        return result


class _FetchFailCursor:
    """Cursor stand-in whose fetch phase fails after real execute succeeded."""

    def __init__(self, cursor, events):
        self._cursor = cursor
        self._events = events

    def fetchone(self):
        self._events.append("fetchone")
        raise sqlite3.OperationalError("injected fetch failure")

    def fetchall(self):
        self._events.append("fetchall")
        raise sqlite3.OperationalError("injected fetch failure")

    def __getattr__(self, name):
        return getattr(self._cursor, name)


class _FetchFailConnection(sqlite3.Connection):
    """Targeted native connection for phase-selected fetch failures.

    SQL matching is by explicit table token, so both unqualified and
    main-qualified forms (`FROM main.team_terminal_seals`) are recognized.
    """

    fail_mode = None
    fetch_events = []
    executed_sql = []
    insert_events = []
    insert_seen = False

    def execute(self, sql, *args, **kwargs):
        text = " ".join(str(sql).split()).lower()
        type(self).executed_sql.append(text)
        if (text.startswith("insert")
                and re.search(r"\bteam_terminal_seals\b", text)):
            type(self).insert_events.append(text)
            type(self).insert_seen = True
        cursor = super().execute(sql, *args, **kwargs)
        if not text.startswith(("select", "with")):
            return cursor
        mode = type(self).fail_mode
        target = {
            "schema": "sqlite_master",
            "marker": "team_terminal_facts_meta",
            "fact": "team_terminal_seals",
            "readback": "team_terminal_seals",
        }.get(mode)
        if target is None or not re.search(
                r"\b" + re.escape(target) + r"\b", text):
            return cursor
        if mode == "readback" and not type(self).insert_seen:
            return cursor
        return _FetchFailCursor(cursor, type(self).fetch_events)


class _ArchiveCollisionConnection(sqlite3.Connection):
    """Targeted native connection: pre-insert an old identical archive row.

    The real archive INSERT then hits UNIQUE on the caller's own connection;
    no thread or race is used.
    """

    collision_sql = None
    collision_params = ()
    collision_done = False
    insert_events = []

    def execute(self, sql, *args, **kwargs):
        text = " ".join(str(sql).split()).lower()
        if (not type(self).collision_done
                and text.startswith("insert")
                and re.search(r"\bteam_terminal_seals\b", text)):
            type(self).collision_done = True
            type(self).insert_events.append(text)
            if type(self).collision_sql is not None:
                super().execute(
                    type(self).collision_sql, type(self).collision_params)
        return super().execute(sql, *args, **kwargs)


def canonical(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False)


def sha256_text(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_json(value):
    return sha256_text(canonical(value))


def opinion_hash(opinion):
    return sha256_json({
        "decision": str(opinion["decision"]).strip().lower(),
        "exclusion_reason": (
            "" if opinion.get("exclusion_reason") is None
            else str(opinion["exclusion_reason"])),
        "notes": "" if opinion.get("notes") is None else str(opinion["notes"]),
    })


def source_row_hash(source_row):
    return sha256_json(source_row)


def expected_metadata(payload):
    content = copy.deepcopy(payload)
    content.pop("sealed_at")
    content_sha = sha256_json(content)
    seal_id = sha256_json({
        "schema": "team_terminal_seal_id.v1",
        "task_instance_id": payload["task"]["task_instance_id"],
        "stage": payload["stage"],
        "item_key": payload["item_key"],
        "content_sha256": content_sha,
    })
    payload_json = canonical(payload)
    return {
        "content_sha256": content_sha,
        "seal_id": seal_id,
        "payload_json": payload_json,
        "payload_sha256": sha256_text(payload_json),
    }


def default_roster():
    return [
        {
            "uid": 1, "username": "owner", "role": "reconciler",
            "is_owner": True, "screening_enabled": False,
            "required_screener": False, "source": "owner-source", "config": "ok",
        },
        {
            "uid": 2, "username": "alice", "role": "coder",
            "is_owner": False, "screening_enabled": None,
            "required_screener": True, "source": "import-source", "config": "ok",
        },
        {
            "uid": 3, "username": "bob", "role": "coder",
            "is_owner": False, "screening_enabled": None,
            "required_screener": True, "source": "grant-source", "config": "ok",
        },
    ]


def make_opinion(uid, decision, *, source_row=None):
    if source_row is None:
        source_row = {
            "tags": "tag-%s" % uid,
            "screened_at": "2026-01-02 03:04:05",
            "updated_at": None if uid % 2 else "2026-01-03 04:05:06",
        }
    return {
        "uid": uid,
        "decision": decision,
        "exclusion_reason": "",
        "notes": "",
        "library_id": "%032x" % uid,
        "revision": uid + 10,
        "source_row": copy.deepcopy(source_row),
        "opinion_sha256": "",
        "source_row_sha256": "",
    }


def make_payload(
        *,
        kind="unanimous",
        trigger=None,
        final_decision="include",
        task_id="task-1",
        task_instance_id="instance-1",
        item_key="K1",
        stage="stage1",
        sealed_at=STAMP,
        roster=None,
        required_uids=None,
        opinions=None,
        source_registration_id=REGISTRATION,
        roster_revision=7,
):
    if trigger is None:
        trigger = "arbitration" if kind == "adjudicated" else "last_vote"
    roster = copy.deepcopy(default_roster() if roster is None else roster)
    if required_uids is None:
        required_uids = sorted(
            entry["uid"] for entry in roster if entry["required_screener"])
    else:
        required_uids = list(required_uids)
    if opinions is None:
        if kind == "adjudicated":
            decisions = {}
            for index, uid in enumerate(required_uids):
                decisions[uid] = "include" if index == 0 else "exclude"
        else:
            decisions = {uid: final_decision for uid in required_uids}
        opinions = [
            make_opinion(uid, decisions[uid]) for uid in required_uids]
    else:
        opinions = copy.deepcopy(opinions)
    for opinion in opinions:
        opinion["opinion_sha256"] = opinion_hash(opinion)
        opinion["source_row_sha256"] = source_row_hash(opinion["source_row"])
    payload = {
        "schema": "team_terminal_seal.v1",
        "task": {
            "task_id": task_id,
            "task_instance_id": task_instance_id,
            "owner_user_id": 1,
            "owner_username": "owner",
            "owner_library_id": "a" * 32,
        },
        "stage": stage,
        "item_key": item_key,
        "round_id": None,
        "kind": kind,
        "trigger": trigger,
        "final_decision": final_decision,
        "roster": roster,
        "required_uids": required_uids,
        "opinions": opinions,
        "arbitration": None,
        "protocol": {
            "source_registration_id": source_registration_id,
            "roster_revision": roster_revision,
        },
        "source_request": {
            "operation": OPERATION_BY_TRIGGER.get(trigger, trigger),
            "request_id": (
                None if trigger == "roster_change" else "request-1"),
            "request_sha256": (
                None if trigger == "roster_change" else "b" * 64),
        },
        "sealed_at": sealed_at,
    }
    if kind == "adjudicated":
        payload["arbitration"] = {
            "arbitrator_user_id": 1,
            "arbitrator_role": "reconciler",
            "record_id": 7,
            "attestation_id": 9,
            "attested_at": sealed_at,
            "resolutions_digest": "c" * 64,
            "result_sha256": "d" * 64,
            "attest_sha256": "e" * 64,
            "rationale": "tie-break",
            "input_proof": {
                item_key: {
                    str(opinion["uid"]): {
                        "library_id": opinion["library_id"],
                        "revision": opinion["revision"],
                    }
                    for opinion in opinions
                }
            },
        }
    return payload


def refresh_payload(payload):
    for opinion in payload["opinions"]:
        opinion["opinion_sha256"] = opinion_hash(opinion)
        opinion["source_row_sha256"] = source_row_hash(opinion["source_row"])
    if payload.get("arbitration") is not None:
        payload["arbitration"]["input_proof"] = {
            payload["item_key"]: {
                str(opinion["uid"]): {
                    "library_id": opinion["library_id"],
                    "revision": opinion["revision"],
                }
                for opinion in payload["opinions"]
            }
        }
    return payload


class TerminalFactsTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="rf216_f1_")
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        self.db = self.base / "actor.db"
        self._new_db(self.db)
        tf.prepare_team_terminal_facts(self.db)

    def _new_db(self, path):
        path = Path(path)
        with closing(sqlite3.connect(str(path))) as conn, conn:
            pass
        return path

    def _prepared_db(self, name):
        path = self._new_db(self.base / name)
        tf.prepare_team_terminal_facts(path)
        return path

    def _open(self, path=None, *, write=True, row=True, begin=True):
        conn = sqlite3.connect(str(path or self.db), timeout=10)
        self.addCleanup(self._close, conn)
        if row:
            conn.row_factory = sqlite3.Row
        if begin:
            conn.execute("BEGIN IMMEDIATE" if write else "BEGIN")
        return conn

    @staticmethod
    def _close(conn):
        try:
            if conn.in_transaction:
                conn.rollback()
        except sqlite3.Error:
            pass
        try:
            conn.close()
        except sqlite3.Error:
            pass

    @contextmanager
    def _trace(self, conn):
        statements = []
        conn.set_trace_callback(statements.append)
        try:
            yield statements
        finally:
            conn.set_trace_callback(None)

    @contextmanager
    def authorizer_denial(self, conn, predicate):
        def callback(action, arg1, arg2, dbname, trigger):
            if predicate(action, arg1, arg2):
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        conn.set_authorizer(callback)
        try:
            yield
        finally:
            conn.set_authorizer(None)

    def schema_objects(self, conn, table):
        rows = conn.execute(
            "SELECT type,name,sql FROM sqlite_master "
            "WHERE tbl_name=? AND type IN ('trigger','index') "
            "ORDER BY type,name",
            (table,),
        ).fetchall()
        return {
            "triggers": {
                row["name"] for row in rows if row["type"] == "trigger"},
            "user_indexes": {
                row["name"] for row in rows
                if row["type"] == "index" and row["sql"] is not None},
            "auto_indexes": {
                row["name"] for row in rows
                if row["type"] == "index" and row["sql"] is None},
        }

    def assert_unavailable_with_cause(self, reason, cause_type, call):
        with self.assertRaises(tf.TerminalFactsUnavailable) as caught:
            call()
        exc = caught.exception
        self.assertEqual(exc.reason, reason)
        cause = exc.__cause__ if exc.__cause__ is not None else exc.__context__
        self.assertIsInstance(cause, cause_type)
        return cause

    @staticmethod
    def exception_roots(exc):
        roots = []
        stack = [exc]
        seen = set()
        while stack:
            current = stack.pop()
            if id(current) in seen:
                continue
            seen.add(id(current))
            roots.append(current)
            if isinstance(current, BaseExceptionGroup):
                stack.extend(current.exceptions)
            if current.__cause__ is not None:
                stack.append(current.__cause__)
            if current.__context__ is not None:
                stack.append(current.__context__)
            cleanup = getattr(current, "cleanup_errors", ())
            if isinstance(cleanup, tuple):
                stack.extend(
                    item for item in cleanup
                    if isinstance(item, BaseException))
        return roots

    def assert_unavailable(self, reason, call):
        with self.assertRaises(tf.TerminalFactsUnavailable) as caught:
            call()
        self.assertEqual(caught.exception.reason, reason)

    def assert_payload_invalid(self, payload, *, origin="actor"):
        conn = self._open(write=True)
        try:
            with self.assertRaises(tf.TerminalFactsPayloadInvalid):
                tf.append_seal_on(conn, payload, origin=origin)
            self.assertTrue(conn.in_transaction)
        finally:
            conn.rollback()
            conn.close()

    def assert_record(self, record, payload, *, origin, archived_at=None):
        self.assertEqual(set(record), RECORD_KEYS)
        self.assertEqual(record["payload"], payload)
        expected = expected_metadata(payload)
        self.assertEqual(record["seal_id"], expected["seal_id"])
        self.assertEqual(record["content_sha256"], expected["content_sha256"])
        self.assertEqual(record["payload_sha256"], expected["payload_sha256"])
        self.assertEqual(record["origin"], origin)
        self.assertEqual(record["sealed_at"], payload["sealed_at"])
        self.assertEqual(record["archived_at"], archived_at)

    def raw_row(self, conn, payload):
        return conn.execute(
            "SELECT * FROM team_terminal_seals "
            "WHERE task_instance_id=? AND stage=? AND item_key=?",
            (payload["task"]["task_instance_id"], payload["stage"],
             payload["item_key"]),
        ).fetchone()

    def insert_raw(self, conn, payload, *, seal_id=None, content_sha256=None,
                   payload_sha256=None, payload_json=None, origin="actor",
                   sealed_at=STAMP, archived_at=None, kind=None,
                   final_decision=None):
        expected = expected_metadata(payload)
        conn.execute(
            "INSERT INTO team_terminal_seals("
            "seal_id,task_instance_id,stage,item_key,kind,final_decision,"
            "content_sha256,payload_sha256,sealed_at,archived_at,origin,payload_json"
            ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                seal_id or expected["seal_id"],
                payload["task"]["task_instance_id"], payload["stage"],
                payload["item_key"],
                payload["kind"] if kind is None else kind,
                payload["final_decision"] if final_decision is None else final_decision,
                content_sha256 or expected["content_sha256"],
                payload_sha256 or expected["payload_sha256"],
                sealed_at, archived_at, origin,
                expected["payload_json"] if payload_json is None else payload_json,
            ),
        )

    def triggers(self, conn):
        return [
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger' "
                "AND tbl_name='team_terminal_seals' ORDER BY name")
        ]


class PrepareTests(TerminalFactsTestBase):
    def test_prepare_creates_complete_layout_and_is_idempotent(self):
        conn = self._open(write=True)
        columns = {
            row[1] for row in conn.execute(
                "PRAGMA table_info(team_terminal_seals)")
        }
        self.assertEqual(columns, TABLE_COLUMNS)
        fact = self.schema_objects(conn, "team_terminal_seals")
        meta = self.schema_objects(conn, "team_terminal_facts_meta")
        self.assertEqual(
            fact["triggers"],
            {"team_terminal_seals_no_update", "team_terminal_seals_no_delete"},
        )
        self.assertEqual(fact["user_indexes"], set())
        self.assertEqual(len(fact["auto_indexes"]), 2)
        self.assertEqual(meta["triggers"], set())
        self.assertEqual(meta["user_indexes"], set())
        self.assertEqual(meta["auto_indexes"], set())
        conn.rollback()
        conn.close()

        tf.prepare_team_terminal_facts(self.db)
        conn = self._open(write=True)
        self.assertEqual(
            {row[1] for row in conn.execute(
                "PRAGMA table_info(team_terminal_seals)")},
            TABLE_COLUMNS,
        )
        fact = self.schema_objects(conn, "team_terminal_seals")
        meta = self.schema_objects(conn, "team_terminal_facts_meta")
        self.assertEqual(
            fact["triggers"],
            {"team_terminal_seals_no_update", "team_terminal_seals_no_delete"},
        )
        self.assertEqual(fact["user_indexes"], set())
        self.assertEqual(len(fact["auto_indexes"]), 2)
        self.assertEqual(meta["triggers"], set())
        self.assertEqual(meta["user_indexes"], set())
        self.assertEqual(meta["auto_indexes"], set())

    def test_prepare_missing_db_fails_without_creating_it(self):
        missing = self.base / "missing.db"
        self.assertFalse(missing.exists())
        with self.assertRaises(FileNotFoundError):
            tf.prepare_team_terminal_facts(missing)
        self.assertFalse(missing.exists())

    def test_prepare_rejects_wrong_table_without_repair(self):
        path = self._new_db(self.base / "wrong_table.db")
        with closing(sqlite3.connect(str(path))) as conn, conn:
            conn.execute(
                "CREATE TABLE team_terminal_seals (seal_id TEXT PRIMARY KEY)")
        with self.assertRaises(tf.TerminalFactsUnavailable) as caught:
            tf.prepare_team_terminal_facts(path)
        self.assertEqual(caught.exception.reason, "schema_mismatch")
        with closing(sqlite3.connect(str(path))) as conn, conn:
            columns = {
                row[1] for row in conn.execute(
                    "PRAGMA table_info(team_terminal_seals)")
            }
            self.assertEqual(columns, {"seal_id"})
            self.assertEqual(self.triggers(conn), [])

    def test_prepare_rejects_missing_trigger_without_repair(self):
        path = self._prepared_db("partial_trigger.db")
        payload = make_payload()
        conn = self._open(path, write=True)
        tf.append_seal_on(conn, payload, origin="actor")
        conn.commit()
        before = set(self.triggers(conn))
        trigger = sorted(before)[0]
        conn.execute('DROP TRIGGER "%s"' % trigger)
        conn.commit()
        conn.close()

        with self.assertRaises(tf.TerminalFactsUnavailable) as caught:
            tf.prepare_team_terminal_facts(path)
        self.assertEqual(caught.exception.reason, "schema_mismatch")
        with closing(sqlite3.connect(str(path))) as conn, conn:
            conn.row_factory = sqlite3.Row
            self.assertEqual(set(self.triggers(conn)), before - {trigger})
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM team_terminal_seals").fetchone()[0],
                1,
            )


    def test_extra_schema_objects_rejected_everywhere(self):
        source_conn = self._open(write=True)
        tf.append_seal_on(
            source_conn, make_payload(item_key="SOURCE"), origin="actor")
        source_record = tf.read_seal_on(
            source_conn, task_instance_id="instance-1", stage="stage1",
            item_key="SOURCE")
        source_conn.commit()
        source_conn.close()

        cases = (
            (
                "fact_trigger", "team_terminal_seals", "triggers",
                "extra_suppress",
                "CREATE TRIGGER extra_suppress BEFORE INSERT ON "
                "team_terminal_seals BEGIN SELECT RAISE(IGNORE); END",
            ),
            (
                "fact_index", "team_terminal_seals", "user_indexes",
                "extra_fact_idx",
                "CREATE INDEX extra_fact_idx ON team_terminal_seals(item_key)",
            ),
            (
                "meta_trigger", "team_terminal_facts_meta", "triggers",
                "extra_meta",
                "CREATE TRIGGER extra_meta BEFORE INSERT ON "
                "team_terminal_facts_meta BEGIN SELECT RAISE(IGNORE); END",
            ),
            (
                "meta_index", "team_terminal_facts_meta", "user_indexes",
                "extra_meta_idx",
                "CREATE INDEX extra_meta_idx ON "
                "team_terminal_facts_meta(schema_version)",
            ),
        )
        for label, table, bucket, name, ddl in cases:
            with self.subTest(label=label):
                target = self._prepared_db("extra_%s.db" % label)
                with closing(sqlite3.connect(str(target))) as conn, conn:
                    conn.execute(ddl)

                with self.assertRaises(tf.TerminalFactsUnavailable) as caught:
                    tf.prepare_team_terminal_facts(target)
                self.assertEqual(caught.exception.reason, "schema_mismatch")

                check = self._open(target, write=False)
                self.assertIn(
                    name, self.schema_objects(check, table)[bucket])
                self._close(check)

                append_conn = self._open(target, write=True)
                self.assert_unavailable(
                    "schema_mismatch",
                    lambda: tf.append_seal_on(
                        append_conn, make_payload(item_key="APPEND"),
                        origin="actor"),
                )
                self.assertEqual(
                    append_conn.execute(
                        "SELECT COUNT(*) FROM team_terminal_seals"
                    ).fetchone()[0],
                    0,
                )
                self.assertTrue(append_conn.in_transaction)
                self._close(append_conn)

                read_conn = self._open(target, write=False)
                self.assert_unavailable(
                    "schema_mismatch",
                    lambda: tf.read_seal_on(
                        read_conn, task_instance_id="instance-1",
                        stage="stage1", item_key="K1"),
                )
                self.assert_unavailable(
                    "schema_mismatch",
                    lambda: tf.list_seals_on(
                        read_conn, task_instance_id="instance-1"),
                )
                self._close(read_conn)

                target_conn = self._open(target, write=True)
                self.assert_unavailable(
                    "schema_mismatch",
                    lambda: tf.archive_seals_on(
                        target_conn, [source_record]),
                )
                self.assertEqual(
                    target_conn.execute(
                        "SELECT COUNT(*) FROM team_terminal_seals"
                    ).fetchone()[0],
                    0,
                )
                self.assertTrue(target_conn.in_transaction)
                self._close(target_conn)

    def test_reserved_name_other_owner_objects_rejected(self):
        source_conn = self._open(write=True)
        payload = make_payload()
        tf.append_seal_on(source_conn, payload, origin="actor")
        source_record = tf.read_seal_on(
            source_conn, task_instance_id="instance-1", stage="stage1",
            item_key="K1")
        source_conn.commit()
        source_conn.close()

        cases = (
            ("trigger", "team_terminal_seals_no_update", "trigger",
             "CREATE TRIGGER team_terminal_seals_no_update "
             "BEFORE INSERT ON other BEGIN SELECT RAISE(IGNORE); END"),
            ("trigger_upper", "TEAM_TERMINAL_SEALS_NO_UPDATE", "trigger",
             "CREATE TRIGGER TEAM_TERMINAL_SEALS_NO_UPDATE "
             "BEFORE INSERT ON other BEGIN SELECT RAISE(IGNORE); END"),
            ("trigger_mixed", "Team_Terminal_Seals_No_Update", "trigger",
             "CREATE TRIGGER Team_Terminal_Seals_No_Update "
             "BEFORE INSERT ON other BEGIN SELECT RAISE(IGNORE); END"),
            ("index", "team_terminal_seals_no_delete", "index",
             "CREATE INDEX team_terminal_seals_no_delete ON other(id)"),
            ("index_upper", "TEAM_TERMINAL_SEALS_NO_DELETE", "index",
             "CREATE INDEX TEAM_TERMINAL_SEALS_NO_DELETE ON other(id)"),
        )
        for label, reserved_name, object_type, ddl in cases:
            with self.subTest(label=label):
                path = self._new_db(self.base / ("reserved_%s.db" % label))
                with closing(sqlite3.connect(str(path))) as conn, conn:
                    conn.execute("CREATE TABLE other (id INTEGER PRIMARY KEY)")
                    conn.execute(ddl)

                for attempt in (1, 2):
                    with self.assertRaises(
                            tf.TerminalFactsUnavailable) as caught:
                        tf.prepare_team_terminal_facts(path)
                    self.assertEqual(
                        caught.exception.reason, "schema_mismatch")

                with closing(sqlite3.connect(str(path))) as check, check:
                    rows = {
                        (row[0], row[1], row[2])
                        for row in check.execute(
                            "SELECT name,type,tbl_name FROM sqlite_master")
                    }
                    self.assertIn(
                        (reserved_name, object_type, "other"), rows)
                    self.assertNotIn(
                        ("team_terminal_seals", "table", "team_terminal_seals"),
                        rows)
                    self.assertNotIn(
                        ("team_terminal_facts_meta", "table",
                         "team_terminal_facts_meta"),
                        rows)

                conn = self._open(path, write=True)
                self.assert_unavailable(
                    "schema_mismatch",
                    lambda: tf.append_seal_on(
                        conn, make_payload(item_key="RESERVED"),
                        origin="actor"))
                self.assert_unavailable(
                    "schema_mismatch",
                    lambda: tf.read_seal_on(
                        conn, task_instance_id="instance-1", stage="stage1",
                        item_key="K1"))
                self.assert_unavailable(
                    "schema_mismatch",
                    lambda: tf.list_seals_on(
                        conn, task_instance_id="instance-1"))
                self.assert_unavailable(
                    "schema_mismatch",
                    lambda: tf.archive_seals_on(conn, [source_record]))
                self.assertTrue(conn.in_transaction)
                self._close(conn)

        # MAIN reserved table with wrong layout, uppercase/mixed spelling.
        for label, table_name in (
                ("main_reserved_upper", "TEAM_TERMINAL_SEALS"),
                ("main_reserved_mixed", "Team_Terminal_Seals")):
            with self.subTest(label=label):
                path = self._new_db(self.base / ("reserved_%s.db" % label))
                with closing(sqlite3.connect(str(path))) as conn, conn:
                    conn.execute(
                        "CREATE TABLE %s (seal_id TEXT PRIMARY KEY)"
                        % table_name)
                for attempt in (1, 2):
                    with self.assertRaises(
                            tf.TerminalFactsUnavailable) as caught:
                        tf.prepare_team_terminal_facts(path)
                    self.assertEqual(
                        caught.exception.reason, "schema_mismatch")
                conn = self._open(path, write=True)
                self.assert_unavailable(
                    "schema_mismatch",
                    lambda: tf.append_seal_on(
                        conn, make_payload(item_key="CASE-RESERVED"),
                        origin="actor"))
                self.assert_unavailable(
                    "schema_mismatch",
                    lambda: tf.read_seal_on(
                        conn, task_instance_id="instance-1", stage="stage1",
                        item_key="K1"))
                self.assert_unavailable(
                    "schema_mismatch",
                    lambda: tf.list_seals_on(
                        conn, task_instance_id="instance-1"))
                self.assert_unavailable(
                    "schema_mismatch",
                    lambda: tf.archive_seals_on(conn, [source_record]))
                self.assertTrue(conn.in_transaction)
                self._close(conn)

    def test_prepare_cleanup_faults_observable_and_close_attempted(self):
        # Targeted prepare-cleanup stand-in, not a real disk/IO fault.
        def reset_cleanup(fail_rollback, fail_close, body_error=None):
            _RollbackFailConnection.close_calls = 0
            _RollbackFailConnection.rollback_failures = 0
            _RollbackFailConnection.close_failures = 0
            _RollbackFailConnection.body_failures = 0
            _RollbackFailConnection.fail_rollback = fail_rollback
            _RollbackFailConnection.fail_close = fail_close
            _RollbackFailConnection.body_error = body_error

        def run_prepare(path):
            real_connect = tf.sqlite3.connect

            def connect_factory(*args, **kwargs):
                kwargs["factory"] = _RollbackFailConnection
                return real_connect(*args, **kwargs)

            with mock.patch.object(
                    tf.sqlite3, "connect", side_effect=connect_factory):
                try:
                    tf.prepare_team_terminal_facts(path)
                except Exception as exc:
                    return exc
            self.fail("prepare unexpectedly succeeded")

        # A) original schema_mismatch + rollback failure + close failure.
        reset_cleanup(True, True)
        path = self._new_db(self.base / "prepare_cleanup.db")
        with closing(sqlite3.connect(str(path))) as conn, conn:
            conn.execute(
                "CREATE TABLE team_terminal_seals (seal_id TEXT PRIMARY KEY)")
        observed = run_prepare(path)
        roots = self.exception_roots(observed)
        self.assertTrue(any(
            isinstance(exc, tf.TerminalFactsUnavailable)
            and exc.reason == "schema_mismatch"
            for exc in roots))
        self.assertTrue(any(
            isinstance(exc, sqlite3.OperationalError)
            and "injected rollback failure" in str(exc)
            for exc in roots))
        self.assertTrue(any(
            isinstance(exc, sqlite3.OperationalError)
            and "injected close failure" in str(exc)
            for exc in roots))
        self.assertGreaterEqual(_RollbackFailConnection.rollback_failures, 1)
        self.assertEqual(_RollbackFailConnection.close_failures, 1)
        self.assertEqual(_RollbackFailConnection.close_calls, 1)

        # C) native ProgrammingError primary + rollback/close cleanup failures.
        # The primary exception must stay native PE; cleanup_errors is the
        # object-level channel, not a forced cause-chain rewrite.
        reset_cleanup(
            True, True, sqlite3.ProgrammingError("injected body PE"))
        pe_path = self._new_db(self.base / "prepare_pe_cleanup.db")
        observed = run_prepare(pe_path)
        self.assertIs(type(observed), sqlite3.ProgrammingError)
        self.assertEqual(str(observed), "injected body PE")
        cleanup = getattr(observed, "cleanup_errors", None)
        self.assertIsInstance(cleanup, tuple)
        self.assertTrue(cleanup)
        roots = self.exception_roots(observed)
        self.assertTrue(any(
            isinstance(exc, sqlite3.OperationalError)
            and "injected rollback failure" in str(exc)
            for exc in roots))
        self.assertTrue(any(
            isinstance(exc, sqlite3.OperationalError)
            and "injected close failure" in str(exc)
            for exc in roots))
        self.assertEqual(_RollbackFailConnection.body_failures, 1)
        self.assertGreaterEqual(_RollbackFailConnection.rollback_failures, 1)
        self.assertEqual(_RollbackFailConnection.close_failures, 1)
        self.assertEqual(_RollbackFailConnection.close_calls, 1)

        # D) ordinary RuntimeError primary + cleanup failures.
        reset_cleanup(True, True, RuntimeError("injected body runtime"))
        runtime_path = self._new_db(self.base / "prepare_runtime_cleanup.db")
        observed = run_prepare(runtime_path)
        self.assertIs(type(observed), RuntimeError)
        self.assertEqual(str(observed), "injected body runtime")
        cleanup = getattr(observed, "cleanup_errors", None)
        self.assertIsInstance(cleanup, tuple)
        self.assertTrue(cleanup)
        roots = self.exception_roots(observed)
        self.assertTrue(any(
            isinstance(exc, sqlite3.OperationalError)
            and "injected rollback failure" in str(exc)
            for exc in roots))
        self.assertTrue(any(
            isinstance(exc, sqlite3.OperationalError)
            and "injected close failure" in str(exc)
            for exc in roots))
        self.assertEqual(_RollbackFailConnection.body_failures, 1)
        self.assertGreaterEqual(_RollbackFailConnection.rollback_failures, 1)
        self.assertEqual(_RollbackFailConnection.close_failures, 1)
        self.assertEqual(_RollbackFailConnection.close_calls, 1)

        # B) commit succeeds, close fails: observable close fault and a fresh
        # native connection proves the commit, never claimed as a rollback.
        reset_cleanup(False, True)
        commit_path = self._new_db(self.base / "prepare_commit_close.db")
        observed = run_prepare(commit_path)
        roots = self.exception_roots(observed)
        self.assertTrue(any(
            isinstance(exc, sqlite3.OperationalError)
            and "injected close failure" in str(exc)
            for exc in roots))
        self.assertEqual(_RollbackFailConnection.rollback_failures, 0)
        self.assertEqual(_RollbackFailConnection.close_failures, 1)
        self.assertEqual(_RollbackFailConnection.close_calls, 1)
        check = sqlite3.connect(str(commit_path))
        try:
            names = {
                row[0] for row in check.execute(
                    "SELECT name FROM sqlite_master WHERE name IN (?,?,?,?)",
                    ("team_terminal_seals", "team_terminal_facts_meta",
                     "team_terminal_seals_no_update",
                     "team_terminal_seals_no_delete"),
                )
            }
            self.assertEqual(names, {
                "team_terminal_seals", "team_terminal_facts_meta",
                "team_terminal_seals_no_update",
                "team_terminal_seals_no_delete",
            })
        finally:
            check.close()


class BorrowedContractTests(TerminalFactsTestBase):
    def test_read_list_require_native_active_row(self):
        with self.assertRaises(tf.TerminalFactsUnavailable) as caught:
            tf.read_seal_on(object(), task_instance_id="i", stage="stage1",
                            item_key="K1")
        self.assertEqual(caught.exception.reason, "connection_contract")
        with self.assertRaises(tf.TerminalFactsUnavailable) as caught:
            tf.list_seals_on(object(), task_instance_id="i")
        self.assertEqual(caught.exception.reason, "connection_contract")

        inactive = sqlite3.connect(str(self.db))
        inactive.row_factory = sqlite3.Row
        self.addCleanup(inactive.close)
        self.assert_unavailable(
            "connection_contract",
            lambda: tf.read_seal_on(
                inactive, task_instance_id="i", stage="stage1", item_key="K1"),
        )
        self.assertFalse(inactive.in_transaction)

        active_tuple = sqlite3.connect(str(self.db))
        active_tuple.execute("BEGIN")
        self.addCleanup(active_tuple.close)
        self.assert_unavailable(
            "connection_contract",
            lambda: tf.list_seals_on(active_tuple, task_instance_id="i"),
        )
        self.assertTrue(active_tuple.in_transaction)
        active_tuple.rollback()

        closed = sqlite3.connect(str(self.db))
        closed.close()
        with self.assertRaises(sqlite3.ProgrammingError):
            tf.read_seal_on(
                closed, task_instance_id="i", stage="stage1", item_key="K1")
        with self.assertRaises(sqlite3.ProgrammingError):
            tf.list_seals_on(closed, task_instance_id="i")

    def test_append_archive_require_native_active_row(self):
        payload = make_payload()
        with self.assertRaises(tf.TerminalFactsUnavailable) as caught:
            tf.append_seal_on(object(), payload, origin="actor")
        self.assertEqual(caught.exception.reason, "connection_contract")
        with self.assertRaises(tf.TerminalFactsUnavailable) as caught:
            tf.archive_seals_on(object(), [])
        self.assertEqual(caught.exception.reason, "connection_contract")

        inactive = sqlite3.connect(str(self.db))
        inactive.row_factory = sqlite3.Row
        self.addCleanup(inactive.close)
        self.assert_unavailable(
            "connection_contract",
            lambda: tf.append_seal_on(inactive, payload, origin="actor"),
        )
        self.assert_unavailable(
            "connection_contract",
            lambda: tf.archive_seals_on(inactive, []),
        )
        self.assertFalse(inactive.in_transaction)

        active_tuple = sqlite3.connect(str(self.db))
        active_tuple.execute("BEGIN")
        self.addCleanup(active_tuple.close)
        self.assert_unavailable(
            "connection_contract",
            lambda: tf.append_seal_on(active_tuple, payload, origin="actor"),
        )
        self.assert_unavailable(
            "connection_contract",
            lambda: tf.archive_seals_on(active_tuple, []),
        )
        self.assertTrue(active_tuple.in_transaction)
        active_tuple.rollback()

        closed = sqlite3.connect(str(self.db))
        closed.close()
        with self.assertRaises(sqlite3.ProgrammingError):
            tf.append_seal_on(closed, payload, origin="actor")
        with self.assertRaises(sqlite3.ProgrammingError):
            tf.archive_seals_on(closed, [])

    def test_borrowed_fetch_stage_faults_typed_with_cause(self):
        def fetch_conn(path, mode):
            _FetchFailConnection.fail_mode = mode
            _FetchFailConnection.fetch_events = []
            _FetchFailConnection.executed_sql = []
            _FetchFailConnection.insert_events = []
            _FetchFailConnection.insert_seen = False
            conn = sqlite3.connect(
                str(path), timeout=10, factory=_FetchFailConnection)
            conn.row_factory = sqlite3.Row
            conn.execute("BEGIN IMMEDIATE")
            self.addCleanup(self._close, conn)
            return conn

        # Real SQLite invalid-UTF8 TEXT row: execute succeeds, fetch fails.
        utf8 = self._prepared_db("fetch_invalid_utf8.db")
        writer = self._open(utf8, write=True)
        writer.execute(
            "INSERT INTO team_terminal_seals("
            "seal_id,task_instance_id,stage,item_key,kind,final_decision,"
            "content_sha256,payload_sha256,sealed_at,archived_at,origin,"
            "payload_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,CAST(X'80' AS TEXT))",
            (
                "f" * 64, "instance-1", "stage1", "UTF8", "unanimous",
                "include", "0" * 64, "0" * 64, STAMP, None, "actor",
            ),
        )
        writer.commit()
        writer.close()
        read = self._open(utf8, write=False)
        self.assert_unavailable_with_cause(
            "unreadable", sqlite3.Error,
            lambda: tf.read_seal_on(
                read, task_instance_id="instance-1", stage="stage1",
                item_key="UTF8"))
        self.assertTrue(read.in_transaction)
        self.assert_unavailable_with_cause(
            "unreadable", sqlite3.Error,
            lambda: tf.list_seals_on(
                read, task_instance_id="instance-1"))
        self._close(read)

        payload = make_payload()
        writer = self._open(write=True)
        tf.append_seal_on(writer, payload, origin="actor")
        writer.commit()
        writer.close()

        # Schema fetchall: sqlite_master fetch fails.
        conn = fetch_conn(self.db, "schema")
        self.assert_unavailable_with_cause(
            "unreadable", sqlite3.Error,
            lambda: tf.read_seal_on(
                conn, task_instance_id="instance-1", stage="stage1",
                item_key="K1"))
        self.assertTrue(conn.in_transaction)
        self.assertEqual(_FetchFailConnection.fetch_events, ["fetchall"])
        self.assertTrue(any(
            "sqlite_master" in sql
            for sql in _FetchFailConnection.executed_sql))
        self._close(conn)

        # Marker fetchall: schema passes, marker fetch fails.
        conn = fetch_conn(self.db, "marker")
        self.assert_unavailable_with_cause(
            "unreadable", sqlite3.Error,
            lambda: tf.read_seal_on(
                conn, task_instance_id="instance-1", stage="stage1",
                item_key="K1"))
        self.assertTrue(conn.in_transaction)
        self.assertEqual(_FetchFailConnection.fetch_events, ["fetchall"])
        self.assertTrue(any(
            "team_terminal_facts_meta" in sql
            for sql in _FetchFailConnection.executed_sql))
        self._close(conn)

        # Single-row fetchone on the fact table.
        conn = fetch_conn(self.db, "fact")
        self.assert_unavailable_with_cause(
            "unreadable", sqlite3.Error,
            lambda: tf.read_seal_on(
                conn, task_instance_id="instance-1", stage="stage1",
                item_key="K1"))
        self.assertTrue(conn.in_transaction)
        self.assertEqual(_FetchFailConnection.fetch_events, ["fetchone"])
        self.assertTrue(any(
            "team_terminal_seals" in sql
            for sql in _FetchFailConnection.executed_sql))
        self._close(conn)

        # List fetchall on the fact table.
        conn = fetch_conn(self.db, "fact")
        self.assert_unavailable_with_cause(
            "unreadable", sqlite3.Error,
            lambda: tf.list_seals_on(
                conn, task_instance_id="instance-1"))
        self.assertTrue(conn.in_transaction)
        self.assertEqual(_FetchFailConnection.fetch_events, ["fetchall"])
        self.assertTrue(any(
            "team_terminal_seals" in sql
            for sql in _FetchFailConnection.executed_sql))
        self._close(conn)

        # Write readback: pre-insert SELECT passes, real INSERT happens, then
        # the post-insert readback fetch fails.  Positive control asserts the
        # INSERT occurred and the row is present uncommitted.
        readback = self._prepared_db("fetch_readback.db")
        conn = fetch_conn(readback, "readback")
        readback_payload = make_payload(item_key="READBACK")
        self.assert_unavailable_with_cause(
            "unreadable", sqlite3.Error,
            lambda: tf.append_seal_on(
                conn, readback_payload, origin="actor"))
        self.assertTrue(conn.in_transaction)
        self.assertTrue(_FetchFailConnection.insert_events)
        self.assertEqual(_FetchFailConnection.fetch_events, ["fetchone"])
        _FetchFailConnection.fail_mode = None
        self.assertEqual(
            conn.execute(
                "SELECT COUNT(*) FROM team_terminal_seals").fetchone()[0], 1)
        conn.rollback()
        conn.close()
        check = self._open(readback, write=False)
        self.assertEqual(
            check.execute(
                "SELECT COUNT(*) FROM team_terminal_seals").fetchone()[0], 0)
        self._close(check)

    def test_temp_namespace_shadow_and_temp_trigger_rejected(self):
        payload = make_payload()
        writer = self._open(write=True)
        tf.append_seal_on(writer, payload, origin="actor")
        writer.commit()
        writer.close()

        shadow_ddl = (
            "CREATE TEMP TABLE team_terminal_seals ("
            "seal_id TEXT NOT NULL PRIMARY KEY,"
            "task_instance_id TEXT NOT NULL,"
            "stage TEXT NOT NULL,"
            "item_key TEXT NOT NULL,"
            "kind TEXT NOT NULL,"
            "final_decision TEXT NOT NULL,"
            "content_sha256 TEXT NOT NULL,"
            "payload_sha256 TEXT NOT NULL,"
            "sealed_at TEXT NOT NULL,"
            "archived_at TEXT,"
            "origin TEXT NOT NULL,"
            "payload_json TEXT NOT NULL)"
        )

        for label, ddl in (
                ("temp_table_shadow", shadow_ddl),
                ("temp_table_shadow_upper",
                 shadow_ddl.replace(
                     "team_terminal_seals", "TEAM_TERMINAL_SEALS", 1)),
                ("temp_table_shadow_mixed",
                 shadow_ddl.replace(
                     "team_terminal_seals", "Team_Terminal_Seals", 1)),
                ("temp_trigger",
                 "CREATE TEMP TRIGGER extra_temp_guard BEFORE INSERT ON "
                 "team_terminal_seals BEGIN SELECT RAISE(IGNORE); END")):
            with self.subTest(label=label):
                conn = self._open(write=True)
                positive = tf.read_seal_on(
                    conn, task_instance_id="instance-1", stage="stage1",
                    item_key="K1")
                self.assert_record(positive, payload, origin="actor")
                source_record = positive
                conn.execute(ddl)

                self.assert_unavailable(
                    "schema_mismatch",
                    lambda: tf.read_seal_on(
                        conn, task_instance_id="instance-1", stage="stage1",
                        item_key="K1"))
                self.assert_unavailable(
                    "schema_mismatch",
                    lambda: tf.list_seals_on(
                        conn, task_instance_id="instance-1"))
                self.assert_unavailable(
                    "schema_mismatch",
                    lambda: tf.append_seal_on(
                        conn, make_payload(item_key="TEMP"), origin="actor"))
                self.assert_unavailable(
                    "schema_mismatch",
                    lambda: tf.archive_seals_on(conn, [source_record]))
                self.assertTrue(conn.in_transaction)
                self.assertEqual(
                    conn.execute(
                        "SELECT COUNT(*) FROM main.team_terminal_seals"
                    ).fetchone()[0],
                    1,
                )
                self._close(conn)

        # Unrelated TEMP objects remain legal; main fact stays readable.
        conn = self._open(write=True)
        conn.execute("CREATE TEMP TABLE unrelated_temp (x TEXT)")
        conn.execute(
            "CREATE TEMP TRIGGER unrelated_guard BEFORE INSERT ON "
            "unrelated_temp BEGIN SELECT RAISE(IGNORE); END")
        self.assert_record(
            tf.read_seal_on(
                conn, task_instance_id="instance-1", stage="stage1",
                item_key="K1"),
            payload, origin="actor",
        )
        self.assertEqual(
            len(tf.list_seals_on(
                conn, task_instance_id="instance-1")),
            1,
        )
        self._close(conn)

        # Truly empty F1 namespace stays not_prepared.
        empty = self._new_db(self.base / "r3_empty.db")
        conn = self._open(empty, write=True)
        self.assert_unavailable(
            "not_prepared",
            lambda: tf.read_seal_on(
                conn, task_instance_id="instance-1", stage="stage1",
                item_key="K1"))
        self.assert_unavailable(
            "not_prepared",
            lambda: tf.list_seals_on(
                conn, task_instance_id="instance-1"))
        self._close(conn)

    def test_borrowed_authorizer_faults_typed_with_cause(self):
        payload = make_payload()
        writer = self._open(write=True)
        tf.append_seal_on(writer, payload, origin="actor")
        source_record = tf.read_seal_on(
            writer, task_instance_id="instance-1", stage="stage1",
            item_key="K1")
        writer.commit()
        writer.close()

        # Schema access denial: schema gate cannot read sqlite_master.
        conn = self._open(write=False)
        with self.authorizer_denial(
                conn,
                lambda action, arg1, arg2: action == sqlite3.SQLITE_SELECT):
            self.assert_unavailable_with_cause(
                "unreadable", sqlite3.Error,
                lambda: tf.read_seal_on(
                    conn, task_instance_id="instance-1", stage="stage1",
                    item_key="K1"))
            self.assertTrue(conn.in_transaction)
        self._close(conn)

        # Row access denial: schema/meta reads pass, fact-table read fails.
        conn = self._open(write=False)
        with self.authorizer_denial(
                conn,
                lambda action, arg1, arg2: (
                    action == sqlite3.SQLITE_READ
                    and arg1 == "team_terminal_seals")):
            self.assert_unavailable_with_cause(
                "unreadable", sqlite3.Error,
                lambda: tf.read_seal_on(
                    conn, task_instance_id="instance-1", stage="stage1",
                    item_key="K1"))
            self.assert_unavailable_with_cause(
                "unreadable", sqlite3.Error,
                lambda: tf.list_seals_on(
                    conn, task_instance_id="instance-1"))
            self.assertTrue(conn.in_transaction)
        self._close(conn)

        # Actual INSERT denial for append: schema/read pass, insert fails.
        append_path = self._prepared_db("authorizer_append.db")
        conn = self._open(append_path, write=True)
        with self.authorizer_denial(
                conn,
                lambda action, arg1, arg2: (
                    action == sqlite3.SQLITE_INSERT
                    and arg1 == "team_terminal_seals")):
            self.assert_unavailable_with_cause(
                "unreadable", sqlite3.Error,
                lambda: tf.append_seal_on(conn, payload, origin="actor"))
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM team_terminal_seals"
                ).fetchone()[0],
                0,
            )
            self.assertTrue(conn.in_transaction)
        self._close(conn)

        # Actual INSERT denial for archive target.
        archive_path = self._prepared_db("authorizer_archive.db")
        conn = self._open(archive_path, write=True)
        with self.authorizer_denial(
                conn,
                lambda action, arg1, arg2: (
                    action == sqlite3.SQLITE_INSERT
                    and arg1 == "team_terminal_seals")):
            self.assert_unavailable_with_cause(
                "unreadable", sqlite3.Error,
                lambda: tf.archive_seals_on(conn, [source_record]))
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM team_terminal_seals"
                ).fetchone()[0],
                0,
            )
            self.assertTrue(conn.in_transaction)
        self._close(conn)

        # Post-INSERT readback denial.  cached_statements=0 is deliberate:
        # the pre-insert _select_by_key SQL must not hide the authorizer call.
        readback_path = self._prepared_db("authorizer_readback.db")
        uncached = sqlite3.connect(
            str(readback_path), timeout=10, cached_statements=0)
        uncached.row_factory = sqlite3.Row
        uncached.execute("BEGIN IMMEDIATE")
        self.addCleanup(self._close, uncached)
        state = {"inserted": False}

        def readback_predicate(action, arg1, arg2):
            if (action == sqlite3.SQLITE_INSERT
                    and arg1 == "team_terminal_seals"):
                state["inserted"] = True
                return False
            if (state["inserted"]
                    and action == sqlite3.SQLITE_READ
                    and arg1 == "team_terminal_seals"):
                return True
            return False

        with self.authorizer_denial(uncached, readback_predicate):
            self.assert_unavailable_with_cause(
                "unreadable", sqlite3.Error,
                lambda: tf.append_seal_on(uncached, payload, origin="actor"))
            self.assertTrue(uncached.in_transaction)
        self.assertEqual(
            uncached.execute(
                "SELECT COUNT(*) FROM team_terminal_seals").fetchone()[0], 1)
        uncached.rollback()
        uncached.close()

        check = self._open(readback_path, write=False)
        self.assertEqual(
            check.execute(
                "SELECT COUNT(*) FROM team_terminal_seals").fetchone()[0], 0)
        self._close(check)

        # Real non-SQLite byte file, no disk-corruption experiment: the native
        # NOTADB cause must map to reason corrupt, and the file stays untouched.
        not_db = self.base / "not_a_database.bin"
        original = b"this is not a sqlite database"
        not_db.write_bytes(original)
        with self.assertRaises(tf.TerminalFactsUnavailable) as caught:
            tf.prepare_team_terminal_facts(not_db)
        self.assertEqual(caught.exception.reason, "corrupt")
        cause = (
            caught.exception.__cause__
            if caught.exception.__cause__ is not None
            else caught.exception.__context__)
        self.assertIsInstance(cause, sqlite3.DatabaseError)
        self.assertIn("not a database", str(cause).lower())
        self.assertEqual(not_db.read_bytes(), original)

    def test_read_list_do_not_begin_rollback_or_connect(self):
        payload = make_payload()
        conn = self._open(write=True)
        tf.append_seal_on(conn, payload, origin="actor")
        with self._trace(conn) as statements:
            with mock.patch("sqlite3.connect",
                            side_effect=AssertionError("no connect")):
                record = tf.read_seal_on(
                    conn, task_instance_id="instance-1", stage="stage1",
                    item_key="K1")
                rows = tf.list_seals_on(
                    conn, task_instance_id="instance-1", stage="stage1")
        self.assertEqual(record["seal_id"], expected_metadata(payload)["seal_id"])
        self.assertEqual(len(rows), 1)
        self.assertTrue(conn.in_transaction)
        for statement in statements:
            self.assertTrue(
                statement.lstrip().upper().startswith("SELECT"), statement)

    def test_append_archive_do_not_commit_or_ddl(self):
        payload = make_payload()
        conn = self._open(write=True)
        with self._trace(conn) as statements:
            with mock.patch("sqlite3.connect",
                            side_effect=AssertionError("no connect")):
                record = tf.append_seal_on(conn, payload, origin="actor")
        self.assertEqual(record["seal_id"], expected_metadata(payload)["seal_id"])
        self.assertTrue(conn.in_transaction)
        for statement in statements:
            upper = statement.lstrip().upper()
            self.assertTrue(
                upper.startswith("SELECT") or upper.startswith("INSERT"),
                statement)

        target = self._prepared_db("collab_append.db")
        target_conn = self._open(target, write=True)
        with self._trace(target_conn) as target_statements:
            with mock.patch("sqlite3.connect",
                            side_effect=AssertionError("no connect")):
                archived = tf.archive_seals_on(target_conn, [record])
        self.assertEqual(archived[0]["origin"], "actor_archive")
        self.assertTrue(target_conn.in_transaction)
        for statement in target_statements:
            upper = statement.lstrip().upper()
            self.assertTrue(
                upper.startswith("SELECT") or upper.startswith("INSERT"),
                statement)


class PayloadValidationTests(TerminalFactsTestBase):
    def test_valid_unanimous_roundtrip(self):
        payload = make_payload()
        conn = self._open(write=True)
        record = tf.append_seal_on(conn, payload, origin="actor")
        self.assert_record(record, payload, origin="actor")
        expected = expected_metadata(payload)
        row = self.raw_row(conn, payload)
        self.assertEqual(row["payload_json"], expected["payload_json"])
        self.assertEqual(row["seal_id"], expected["seal_id"])
        self.assertEqual(row["content_sha256"], expected["content_sha256"])
        self.assertEqual(row["payload_sha256"], expected["payload_sha256"])
        conn.commit()
        conn.close()

        check = self._open(write=False)
        self.assert_record(
            tf.read_seal_on(
                check, task_instance_id="instance-1", stage="stage1",
                item_key="K1"),
            payload, origin="actor",
        )

    def test_valid_adjudicated_roundtrip_is_self_contained(self):
        payload = make_payload(kind="adjudicated")
        collab = self._prepared_db("collab_roundtrip.db")
        conn = self._open(collab, write=True)
        record = tf.append_seal_on(conn, payload, origin="collab")
        self.assert_record(record, payload, origin="collab")
        self.assertIsNone(record["archived_at"])
        self.assertEqual(record["payload"]["required_uids"], [2, 3])
        self.assertEqual(
            [entry["uid"] for entry in record["payload"]["roster"]],
            [1, 2, 3],
        )
        self.assertEqual(
            [opinion["uid"] for opinion in record["payload"]["opinions"]],
            [2, 3],
        )
        arbitration = record["payload"]["arbitration"]
        self.assertEqual(arbitration["arbitrator_user_id"], 1)
        owner_entry = next(
            entry for entry in record["payload"]["roster"]
            if entry["uid"] == arbitration["arbitrator_user_id"])
        self.assertEqual(owner_entry["role"], "reconciler")
        self.assertFalse(owner_entry["required_screener"])
        expected = expected_metadata(payload)
        row = self.raw_row(conn, payload)
        self.assertEqual(row["payload_json"], expected["payload_json"])
        self.assertEqual(row["content_sha256"], expected["content_sha256"])
        self.assertEqual(row["payload_sha256"], expected["payload_sha256"])
        self.assertEqual(row["seal_id"], expected["seal_id"])
        conn.commit()
        conn.close()

        check = self._open(collab, write=False)
        loaded = tf.read_seal_on(
            check, task_instance_id="instance-1", stage="stage1", item_key="K1")
        self.assertEqual(loaded["payload"]["arbitration"]["record_id"], 7)
        self.assertEqual(loaded["payload"]["arbitration"]["attestation_id"], 9)

    def test_source_request_operation_mapping(self):
        unanimous = make_payload()
        self.assertEqual(
            unanimous["source_request"]["operation"], "vote_save")
        self.assertNotEqual(
            unanimous["source_request"]["operation"], unanimous["trigger"])
        self.assertIsNotNone(unanimous["source_request"]["request_id"])
        self.assertIsNotNone(unanimous["source_request"]["request_sha256"])

        adjudicated = make_payload(kind="adjudicated")
        self.assertEqual(
            adjudicated["source_request"]["operation"], "arbitration_finish")
        self.assertNotEqual(
            adjudicated["source_request"]["operation"], adjudicated["trigger"])
        self.assertIsNotNone(adjudicated["source_request"]["request_id"])
        self.assertIsNotNone(adjudicated["source_request"]["request_sha256"])

        roster_change = make_payload(
            trigger="roster_change", item_key="K2",
            source_registration_id=None, roster_revision=None)
        self.assertEqual(
            roster_change["source_request"]["operation"], "roster_change")
        self.assertIsNone(roster_change["source_request"]["request_id"])
        self.assertIsNone(roster_change["source_request"]["request_sha256"])

        conn = self._open(write=True)
        tf.append_seal_on(conn, unanimous, origin="actor")
        conn.rollback()

        collab = self._prepared_db("collab_operation_map.db")
        conn = self._open(collab, write=True)
        tf.append_seal_on(conn, adjudicated, origin="collab")
        tf.append_seal_on(conn, roster_change, origin="collab")
        self.assertEqual(conn.execute(
            "SELECT COUNT(*) FROM team_terminal_seals").fetchone()[0], 2)
        conn.rollback()

    def test_task_id_and_instance_are_independent(self):
        first = make_payload(task_id="task-A", task_instance_id="instance-A")
        second = make_payload(
            task_id="task-A", task_instance_id="instance-B", item_key="K2")
        conn = self._open(write=True)
        tf.append_seal_on(conn, first, origin="actor")
        tf.append_seal_on(conn, second, origin="actor")
        rows = tf.list_seals_on(conn, task_instance_id="instance-A")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["payload"]["task"]["task_id"], "task-A")
        rows_b = tf.list_seals_on(conn, task_instance_id="instance-B")
        self.assertEqual(len(rows_b), 1)
        self.assertEqual(rows_b[0]["payload"]["task"]["task_id"], "task-A")

        conflicting = make_payload(
            task_id="task-B", task_instance_id="instance-A")
        with self.assertRaises(tf.TerminalFactsConflict):
            tf.append_seal_on(conn, conflicting, origin="actor")
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM team_terminal_seals").fetchone()[0],
            2,
        )

    def test_item_key_has_no_300_character_cap(self):
        long_key = "K" * 400
        payload = make_payload(item_key=long_key)
        conn = self._open(write=True)
        tf.append_seal_on(conn, payload, origin="actor")
        loaded = tf.read_seal_on(
            conn, task_instance_id="instance-1", stage="stage1",
            item_key=long_key)
        self.assertEqual(loaded["payload"]["item_key"], long_key)

    def test_full_roster_and_non_required_members(self):
        roster = default_roster() + [{
            "uid": 4, "username": "carol", "role": "reconciler",
            "is_owner": False, "screening_enabled": True,
            "required_screener": True, "source": "reconciler-source",
            "config": "ok",
        }, {
            "uid": 5, "username": "dave", "role": "reconciler",
            "is_owner": False, "screening_enabled": False,
            "required_screener": False, "source": "observer-source",
            "config": "ok",
        }]
        payload = make_payload(roster=roster, required_uids=[2, 3, 4])
        conn = self._open(write=True)
        record = tf.append_seal_on(conn, payload, origin="actor")
        self.assertEqual(
            [entry["uid"] for entry in record["payload"]["roster"]],
            [1, 2, 3, 4, 5],
        )
        self.assertEqual(record["payload"]["required_uids"], [2, 3, 4])
        self.assertEqual(
            [opinion["uid"] for opinion in record["payload"]["opinions"]],
            [2, 3, 4],
        )

    def test_single_required_unanimous_is_legal(self):
        roster = [default_roster()[0], default_roster()[1]]
        payload = make_payload(roster=roster, required_uids=[2])
        conn = self._open(write=True)
        tf.append_seal_on(conn, payload, origin="actor")
        loaded = tf.read_seal_on(
            conn, task_instance_id="instance-1", stage="stage1", item_key="K1")
        self.assertEqual(loaded["payload"]["required_uids"], [2])

    def test_roster_required_and_opinion_coverage_rejections(self):
        cases = []
        cases.append(("empty_required", make_payload(required_uids=[])))
        cases.append(("duplicate_required", make_payload(required_uids=[2, 2])))
        cases.append(("unsorted_required", make_payload(required_uids=[3, 2])))
        cases.append(("missing_required_uid", make_payload(required_uids=[2])))
        cases.append(("extra_required_uid", make_payload(required_uids=[2, 3, 4])))
        cases.append(("unsorted_roster", make_payload(
            roster=list(reversed(default_roster())))))
        duplicate_roster = default_roster()
        duplicate_roster[2]["uid"] = 2
        cases.append(("duplicate_roster_uid", make_payload(
            roster=duplicate_roster, required_uids=[2])))
        bool_uid_roster = default_roster()
        bool_uid_roster[1]["uid"] = True
        cases.append(("bool_uid", make_payload(
            roster=bool_uid_roster, required_uids=[True, 3])))

        missing_opinion = make_payload()
        missing_opinion["opinions"] = missing_opinion["opinions"][:1]
        cases.append(("missing_opinion", missing_opinion))
        extra_opinion = make_payload()
        extra_opinion["opinions"].append(make_opinion(4, "include"))
        refresh_payload(extra_opinion)
        cases.append(("extra_opinion", extra_opinion))
        fake_vote = make_payload()
        fake_vote["opinions"].append(make_opinion(1, "include"))
        refresh_payload(fake_vote)
        cases.append(("non_required_opinion", fake_vote))
        unsorted_opinions = make_payload()
        unsorted_opinions["opinions"] = list(
            reversed(unsorted_opinions["opinions"]))
        refresh_payload(unsorted_opinions)
        cases.append(("unsorted_opinions", unsorted_opinions))
        duplicate_opinion = make_payload()
        duplicate_opinion["opinions"].append(
            copy.deepcopy(duplicate_opinion["opinions"][0]))
        refresh_payload(duplicate_opinion)
        cases.append(("duplicate_opinion", duplicate_opinion))

        for label, payload in cases:
            with self.subTest(label=label):
                self.assert_payload_invalid(payload)

    def test_owner_role_and_roster_field_rejections(self):
        cases = []

        no_owner = make_payload()
        no_owner["roster"][0]["is_owner"] = False
        cases.append(("no_owner", no_owner))
        two_owners = make_payload()
        two_owners["roster"][1]["is_owner"] = True
        cases.append(("two_owners", two_owners))
        owner_mismatch = make_payload()
        owner_mismatch["task"]["owner_user_id"] = 99
        cases.append(("owner_mismatch", owner_mismatch))
        owner_not_in_roster = make_payload()
        owner_not_in_roster["roster"] = owner_not_in_roster["roster"][1:]
        cases.append(("owner_not_in_roster", owner_not_in_roster))
        coder_not_required = make_payload(required_uids=[3])
        coder_not_required["roster"][1]["required_screener"] = False
        refresh_payload(coder_not_required)
        cases.append(("coder_not_required", coder_not_required))
        reconciler_mismatch = make_payload()
        reconciler_mismatch["roster"][0]["screening_enabled"] = True
        cases.append(("reconciler_screening_mismatch", reconciler_mismatch))
        bad_config = make_payload()
        bad_config["roster"][1]["config"] = "unconfigured"
        cases.append(("bad_config", bad_config))
        bad_role = make_payload()
        bad_role["roster"][1]["role"] = "owner"
        cases.append(("bad_role", bad_role))
        empty_source = make_payload()
        empty_source["roster"][1]["source"] = ""
        cases.append(("empty_source", empty_source))
        empty_username = make_payload()
        empty_username["roster"][1]["username"] = ""
        cases.append(("empty_username", empty_username))
        bool_owner_id = make_payload()
        bool_owner_id["task"]["owner_user_id"] = True
        cases.append(("bool_owner_id", bool_owner_id))
        bad_owner_library = make_payload()
        bad_owner_library["task"]["owner_library_id"] = "A" * 32
        cases.append(("bad_owner_library_id", bad_owner_library))
        empty_task_id = make_payload(task_id="")
        cases.append(("empty_task_id", empty_task_id))
        empty_instance = make_payload(task_instance_id="")
        cases.append(("empty_instance_id", empty_instance))
        empty_owner_name = make_payload()
        empty_owner_name["task"]["owner_username"] = ""
        cases.append(("empty_owner_username", empty_owner_name))

        for label, payload in cases:
            with self.subTest(label=label):
                self.assert_payload_invalid(payload)

        coder_with_bool_flag = make_payload()
        coder_with_bool_flag["roster"][1]["screening_enabled"] = True
        conn = self._open(write=True)
        tf.append_seal_on(conn, coder_with_bool_flag, origin="actor")
        self.assertEqual(conn.execute(
            "SELECT COUNT(*) FROM team_terminal_seals").fetchone()[0], 1)
        conn.rollback()

    def test_decision_rules(self):
        cases = []
        mixed_unanimous = make_payload()
        mixed_unanimous["opinions"][1]["decision"] = (
            "exclude" if mixed_unanimous["final_decision"] == "include"
            else "include")
        refresh_payload(mixed_unanimous)
        cases.append(("unanimous_mixed", mixed_unanimous))
        maybe_unanimous = make_payload()
        maybe_unanimous["opinions"][0]["decision"] = "maybe"
        refresh_payload(maybe_unanimous)
        cases.append(("unanimous_maybe", maybe_unanimous))
        wrong_final = make_payload()
        wrong_final["final_decision"] = "exclude"
        cases.append(("unanimous_wrong_final", wrong_final))
        cases.append(("adjudicated_one_required",
                      make_payload(
                          kind="adjudicated",
                          roster=[default_roster()[0], default_roster()[1]],
                          required_uids=[2])))
        all_include = make_payload(kind="adjudicated")
        for opinion in all_include["opinions"]:
            opinion["decision"] = "include"
        refresh_payload(all_include)
        cases.append(("adjudicated_all_include", all_include))
        all_exclude = make_payload(kind="adjudicated", final_decision="exclude")
        for opinion in all_exclude["opinions"]:
            opinion["decision"] = "exclude"
        refresh_payload(all_exclude)
        cases.append(("adjudicated_all_exclude", all_exclude))
        missing_arbitration = make_payload(kind="adjudicated")
        missing_arbitration["arbitration"] = None
        cases.append(("adjudicated_no_arbitration", missing_arbitration))
        extra_arbitration = make_payload()
        extra_arbitration["arbitration"] = copy.deepcopy(
            make_payload(kind="adjudicated")["arbitration"])
        cases.append(("unanimous_with_arbitration", extra_arbitration))
        empty_exclude_reason = make_payload(
            kind="adjudicated", final_decision="exclude")
        empty_exclude_reason["arbitration"]["rationale"] = ""
        cases.append(("adjudicated_exclude_empty_reason", empty_exclude_reason))

        for label, payload in cases:
            with self.subTest(label=label):
                self.assert_payload_invalid(payload)

        valid_exclude = make_payload(
            kind="adjudicated", final_decision="exclude")
        collab = self._prepared_db("collab_decision.db")
        conn = self._open(collab, write=True)
        tf.append_seal_on(conn, valid_exclude, origin="collab")
        self.assertEqual(conn.execute(
            "SELECT COUNT(*) FROM team_terminal_seals").fetchone()[0], 1)
        conn.rollback()

    def test_source_row_and_hash_requirements_for_both_kinds(self):
        for kind in ("unanimous", "adjudicated"):
            with self.subTest(kind=kind, case="missing_source_row"):
                payload = make_payload(kind=kind)
                payload["opinions"][0].pop("source_row")
                self.assert_payload_invalid(
                    payload, origin="collab" if kind == "adjudicated" else "actor")
            with self.subTest(kind=kind, case="null_source_row"):
                payload = make_payload(kind=kind)
                payload["opinions"][0]["source_row"] = None
                self.assert_payload_invalid(
                    payload, origin="collab" if kind == "adjudicated" else "actor")
            with self.subTest(kind=kind, case="missing_source_row_sha"):
                payload = make_payload(kind=kind)
                payload["opinions"][0].pop("source_row_sha256")
                self.assert_payload_invalid(
                    payload, origin="collab" if kind == "adjudicated" else "actor")
            with self.subTest(kind=kind, case="bad_source_row_sha"):
                payload = make_payload(kind=kind)
                payload["opinions"][0]["source_row_sha256"] = "0" * 64
                self.assert_payload_invalid(
                    payload, origin="collab" if kind == "adjudicated" else "actor")
            with self.subTest(kind=kind, case="bad_opinion_sha"):
                payload = make_payload(kind=kind)
                payload["opinions"][0]["opinion_sha256"] = "0" * 64
                self.assert_payload_invalid(
                    payload, origin="collab" if kind == "adjudicated" else "actor")

        raw_times = make_payload()
        raw_times["opinions"][0]["source_row"] = {
            "tags": "",
            "screened_at": "2026-01-02 03:04:05",
            "updated_at": None,
        }
        refresh_payload(raw_times)
        conn = self._open(write=True)
        record = tf.append_seal_on(conn, raw_times, origin="actor")
        self.assertEqual(
            record["payload"]["opinions"][0]["source_row"]["screened_at"],
            "2026-01-02 03:04:05",
        )
        conn.rollback()

        extra_source_key = make_payload()
        extra_source_key["opinions"][0]["source_row"]["extra"] = "x"
        refresh_payload(extra_source_key)
        self.assert_payload_invalid(extra_source_key)

        bool_revision = make_payload()
        bool_revision["opinions"][0]["revision"] = True
        self.assert_payload_invalid(bool_revision)

        bad_library_id = make_payload()
        bad_library_id["opinions"][0]["library_id"] = "A" * 32
        self.assert_payload_invalid(bad_library_id)

    def test_opinion_text_fields_strict_both_kinds(self):
        for kind, origin in (("unanimous", "actor"), ("adjudicated", "collab")):
            for field in ("exclusion_reason", "notes"):
                payload = make_payload(kind=kind)
                payload["opinions"][0][field] = None
                payload["opinions"][0]["opinion_sha256"] = opinion_hash(
                    payload["opinions"][0])
                with self.subTest(kind=kind, field=field):
                    self.assert_payload_invalid(payload, origin=origin)

        check = self._open(write=False)
        self.assertEqual(
            check.execute(
                "SELECT COUNT(*) FROM team_terminal_seals").fetchone()[0], 0)
        self._close(check)

        collab = self._prepared_db("opinion_text_collab.db")
        for kind, origin, path in (
                ("unanimous", "actor", self.db),
                ("adjudicated", "collab", collab)):
            payload = make_payload(kind=kind)
            for opinion in payload["opinions"]:
                opinion["exclusion_reason"] = ""
                opinion["notes"] = ""
            refresh_payload(payload)
            conn = self._open(path, write=True)
            record = tf.append_seal_on(conn, payload, origin=origin)
            for opinion in record["payload"]["opinions"]:
                self.assertEqual(opinion["exclusion_reason"], "")
                self.assertEqual(opinion["notes"], "")
                self.assertEqual(
                    opinion["opinion_sha256"], opinion_hash(opinion))
            conn.rollback()
            conn.close()

    def test_mixed_unknown_keys_and_surrogate_payload_invalid(self):
        cases = []
        mixed = make_payload()
        mixed[1] = "x"
        mixed["unknown"] = "y"
        cases.append(("mixed_unknown_keys", mixed))
        nested = make_payload()
        nested["roster"][0][1] = "x"
        cases.append(("nested_unknown_int_key", nested))
        surrogate_user = make_payload()
        surrogate_user["roster"][1]["username"] = "\ud800"
        cases.append(("surrogate_username", surrogate_user))
        surrogate_note = make_payload()
        surrogate_note["opinions"][0]["notes"] = "\ud800"
        cases.append(("surrogate_notes", surrogate_note))

        for label, payload in cases:
            with self.subTest(label=label):
                self.assert_payload_invalid(payload)

        check = self._open(write=False)
        self.assertEqual(
            check.execute(
                "SELECT COUNT(*) FROM team_terminal_seals").fetchone()[0], 0)
        self._close(check)

        legal = make_payload()
        legal["roster"][1]["username"] = "\u540d\u5b57"
        legal["opinions"][0]["notes"] = "\u8bf4\u660e"
        refresh_payload(legal)
        conn = self._open(write=True)
        record = tf.append_seal_on(conn, legal, origin="actor")
        self.assertEqual(
            record["payload"]["roster"][1]["username"], "\u540d\u5b57")
        self.assertEqual(
            record["payload"]["opinions"][0]["notes"], "\u8bf4\u660e")
        conn.rollback()
        conn.close()

    def test_persisted_bad_opinions_and_surrogate_are_corrupt(self):
        writer = self._open(write=True)
        bad_keys = []
        for kind, origin in (("unanimous", "actor"), ("adjudicated", "collab")):
            for field in ("exclusion_reason", "notes"):
                item_key = "NULL-%s-%s" % (kind, field)
                payload = make_payload(kind=kind, item_key=item_key)
                payload["opinions"][0][field] = None
                payload["opinions"][0]["opinion_sha256"] = opinion_hash(
                    payload["opinions"][0])
                self.insert_raw(writer, payload, origin=origin)
                bad_keys.append(item_key)

        surrogate = make_payload(item_key="SURR")
        expected = expected_metadata(surrogate)
        raw = expected["payload_json"].replace(
            '"username":"alice"', '"username":"\\ud800"')
        self.assertNotEqual(raw, expected["payload_json"])
        self.insert_raw(writer, surrogate, payload_json=raw)
        bad_keys.append("SURR")
        writer.commit()
        writer.close()

        check = self._open(write=False)
        for item_key in bad_keys:
            with self.subTest(item_key=item_key):
                self.assert_unavailable(
                    "corrupt",
                    lambda item_key=item_key: tf.read_seal_on(
                        check, task_instance_id="instance-1",
                        stage="stage1", item_key=item_key),
                )
        self.assert_unavailable(
            "corrupt",
            lambda: tf.list_seals_on(
                check, task_instance_id="instance-1"),
        )
        self._close(check)

    def test_strict_schema_and_unknown_fields(self):
        cases = []
        wrong_schema = make_payload()
        wrong_schema["schema"] = "team_terminal_seal.v2"
        cases.append(("wrong_schema", wrong_schema))
        extra_top = make_payload()
        extra_top["extra"] = 1
        cases.append(("extra_top", extra_top))
        extra_task = make_payload()
        extra_task["task"]["extra"] = 1
        cases.append(("extra_task", extra_task))
        extra_roster = make_payload()
        extra_roster["roster"][1]["extra"] = 1
        cases.append(("extra_roster", extra_roster))
        extra_opinion = make_payload()
        extra_opinion["opinions"][0]["extra"] = 1
        cases.append(("extra_opinion", extra_opinion))
        missing_owner_library = make_payload()
        missing_owner_library["task"].pop("owner_library_id")
        cases.append(("missing_owner_library", missing_owner_library))
        bad_stage = make_payload(stage="stage3")
        cases.append(("bad_stage", bad_stage))
        bad_kind = make_payload(kind="other")
        cases.append(("bad_kind", bad_kind))
        bad_trigger = make_payload(trigger="other")
        cases.append(("bad_trigger", bad_trigger))
        bad_final = make_payload(final_decision="maybe")
        cases.append(("bad_final", bad_final))
        non_null_round = make_payload()
        non_null_round["round_id"] = "round-1"
        cases.append(("non_null_round", non_null_round))
        missing_protocol = make_payload()
        missing_protocol.pop("protocol")
        cases.append(("missing_protocol", missing_protocol))
        bad_protocol_revision = make_payload()
        bad_protocol_revision["protocol"]["roster_revision"] = True
        cases.append(("bool_roster_revision", bad_protocol_revision))
        nan_protocol_revision = make_payload()
        nan_protocol_revision["protocol"]["roster_revision"] = float("nan")
        cases.append(("nan_roster_revision", nan_protocol_revision))
        bad_request_operation = make_payload()
        bad_request_operation["source_request"]["operation"] = "arbitration_finish"
        cases.append(("request_operation_mismatch", bad_request_operation))
        trigger_as_operation = make_payload()
        trigger_as_operation["source_request"]["operation"] = "last_vote"
        cases.append(("trigger_text_as_operation", trigger_as_operation))
        missing_request_id = make_payload()
        missing_request_id["source_request"]["request_id"] = None
        cases.append(("missing_request_id", missing_request_id))
        missing_request_sha = make_payload()
        missing_request_sha["source_request"]["request_sha256"] = None
        cases.append(("missing_request_sha", missing_request_sha))
        bad_request_sha = make_payload()
        bad_request_sha["source_request"]["request_sha256"] = "x"
        cases.append(("bad_request_sha", bad_request_sha))
        missing_sealed_at = make_payload()
        missing_sealed_at.pop("sealed_at")
        cases.append(("missing_sealed_at", missing_sealed_at))
        bad_sealed_at = make_payload(sealed_at="2026-11-16 10:00:00")
        cases.append(("bad_sealed_at", bad_sealed_at))
        bad_attested = make_payload(kind="adjudicated")
        bad_attested["arbitration"]["attested_at"] = STAMP2
        cases.append(("bad_attested_at", bad_attested))

        for label, payload in cases:
            with self.subTest(label=label):
                origin = "collab" if payload.get("kind") == "adjudicated" else "actor"
                self.assert_payload_invalid(payload, origin=origin)

        no_registration = make_payload(
            source_registration_id=None, roster_revision=None)
        conn = self._open(write=True)
        tf.append_seal_on(conn, no_registration, origin="actor")
        conn.rollback()

    def test_adjudicated_proof_binding_rejections(self):
        cases = []

        def mutate(label, callback):
            payload = make_payload(kind="adjudicated")
            callback(payload)
            cases.append((label, payload))

        mutate("empty_input_proof",
               lambda p: p["arbitration"].__setitem__("input_proof", {}))
        mutate("extra_item_key",
               lambda p: p["arbitration"]["input_proof"].__setitem__(
                   "K2", p["arbitration"]["input_proof"]["K1"]))
        mutate("missing_uid",
               lambda p: p["arbitration"]["input_proof"]["K1"].pop("3"))
        mutate("extra_uid",
               lambda p: p["arbitration"]["input_proof"]["K1"].__setitem__(
                   "99", {"library_id": "a" * 32, "revision": 1}))
        mutate("library_mismatch",
               lambda p: p["arbitration"]["input_proof"]["K1"]["2"].__setitem__(
                   "library_id", "f" * 32))
        mutate("revision_mismatch",
               lambda p: p["arbitration"]["input_proof"]["K1"]["2"].__setitem__(
                   "revision", 999))
        mutate("bad_record_id",
               lambda p: p["arbitration"].__setitem__("record_id", True))
        mutate("zero_attestation_id",
               lambda p: p["arbitration"].__setitem__("attestation_id", 0))
        mutate("bad_arbitrator_id",
               lambda p: p["arbitration"].__setitem__(
                   "arbitrator_user_id", False))
        mutate("arbitrator_not_in_roster",
               lambda p: p["arbitration"].__setitem__(
                   "arbitrator_user_id", 99))
        mutate("arbitrator_is_coder_labeled_reconciler",
               lambda p: p["arbitration"].__setitem__(
                   "arbitrator_user_id", 2))
        mutate("bad_arbitrator_role",
               lambda p: p["arbitration"].__setitem__(
                   "arbitrator_role", "coder"))
        mutate("bad_result_sha",
               lambda p: p["arbitration"].__setitem__("result_sha256", "x"))
        mutate("bad_attest_sha",
               lambda p: p["arbitration"].__setitem__("attest_sha256", "x"))
        mutate("bad_resolutions_digest",
               lambda p: p["arbitration"].__setitem__(
                   "resolutions_digest", "x"))
        mutate("non_text_rationale",
               lambda p: p["arbitration"].__setitem__("rationale", 7))

        for label, payload in cases:
            with self.subTest(label=label):
                self.assert_payload_invalid(payload, origin="collab")

    def test_origin_kind_trigger_restrictions(self):
        actor_ok = make_payload()
        conn = self._open(write=True)
        tf.append_seal_on(conn, actor_ok, origin="actor")
        conn.rollback()

        with self.subTest(case="actor_adjudicated"):
            self.assert_payload_invalid(
                make_payload(kind="adjudicated"), origin="actor")
        with self.subTest(case="actor_roster_change"):
            self.assert_payload_invalid(
                make_payload(trigger="roster_change"), origin="actor")
        with self.subTest(case="collab_last_vote"):
            self.assert_payload_invalid(make_payload(), origin="collab")

        collab = self._prepared_db("collab_origin.db")
        conn = self._open(collab, write=True)
        roster_change = make_payload(
            trigger="roster_change", source_registration_id=None,
            roster_revision=None)
        tf.append_seal_on(conn, roster_change, origin="collab")
        adjudicated = make_payload(kind="adjudicated", item_key="K2")
        tf.append_seal_on(conn, adjudicated, origin="collab")
        self.assertEqual(conn.execute(
            "SELECT COUNT(*) FROM team_terminal_seals").fetchone()[0], 2)
        conn.rollback()


class AppendReadTests(TerminalFactsTestBase):
    def test_append_idempotent_same_content_new_sealed_at(self):
        payload = make_payload()
        conn = self._open(write=True)
        first = tf.append_seal_on(conn, payload, origin="actor")
        raw_before = self.raw_row(conn, payload)["payload_json"]
        retry = copy.deepcopy(payload)
        retry["sealed_at"] = STAMP2
        second = tf.append_seal_on(conn, retry, origin="actor")
        self.assertEqual(second, first)
        self.assertEqual(second["sealed_at"], STAMP)
        self.assertEqual(
            self.raw_row(conn, payload)["payload_json"], raw_before)
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM team_terminal_seals").fetchone()[0],
            1,
        )

    def test_append_idempotence_requires_same_origin(self):
        payload = make_payload()
        conn = self._open(write=True)
        self.insert_raw(conn, payload, origin="actor_archive",
                        archived_at=STAMP2)
        with self.assertRaises(tf.TerminalFactsConflict):
            tf.append_seal_on(conn, payload, origin="actor")
        row = self.raw_row(conn, payload)
        self.assertEqual(row["origin"], "actor_archive")
        self.assertEqual(row["archived_at"], STAMP2)
        self.assertTrue(conn.in_transaction)

    def test_append_conflict_does_not_overwrite_or_rollback(self):
        payload = make_payload()
        conn = self._open(write=True)
        first = tf.append_seal_on(conn, payload, origin="actor")
        changed = make_payload(final_decision="exclude")
        for opinion in changed["opinions"]:
            opinion["decision"] = "exclude"
        refresh_payload(changed)
        with self.assertRaises(tf.TerminalFactsConflict):
            tf.append_seal_on(conn, changed, origin="actor")
        self.assertTrue(conn.in_transaction)
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM team_terminal_seals").fetchone()[0],
            1,
        )
        self.assertEqual(
            tf.read_seal_on(
                conn, task_instance_id="instance-1", stage="stage1",
                item_key="K1"),
            first,
        )

    def test_append_rollback_and_commit_visibility(self):
        payload = make_payload()
        conn = self._open(write=True)
        tf.append_seal_on(conn, payload, origin="actor")
        self.assertEqual(
            conn.execute(
                "SELECT COUNT(*) FROM team_terminal_seals").fetchone()[0], 1)
        conn.rollback()
        conn.close()

        check = self._open(write=False)
        self.assertIsNone(tf.read_seal_on(
            check, task_instance_id="instance-1", stage="stage1", item_key="K1"))
        check.rollback()
        check.close()

        conn = self._open(write=True)
        tf.append_seal_on(conn, payload, origin="actor")
        conn.commit()
        conn.close()

        check = self._open(write=False)
        self.assert_record(
            tf.read_seal_on(
                check, task_instance_id="instance-1", stage="stage1",
                item_key="K1"),
            payload, origin="actor",
        )

    def test_read_joins_caller_transaction_and_does_not_open_snapshot(self):
        payload = make_payload()
        writer = self._open(write=True)
        tf.append_seal_on(writer, payload, origin="actor")

        visible = tf.read_seal_on(
            writer, task_instance_id="instance-1", stage="stage1",
            item_key="K1")
        self.assert_record(visible, payload, origin="actor")

        other = self._open(write=False)
        self.assertIsNone(tf.read_seal_on(
            other, task_instance_id="instance-1", stage="stage1",
            item_key="K1"))
        other.rollback()
        other.close()
        writer.commit()

        check = self._open(write=False)
        self.assert_record(
            tf.read_seal_on(
                check, task_instance_id="instance-1", stage="stage1",
                item_key="K1"),
            payload, origin="actor",
        )

    def test_read_absent_key_and_list_filters(self):
        first = make_payload(item_key="K1", stage="stage1")
        second = make_payload(item_key="K2", stage="stage2")
        conn = self._open(write=True)
        tf.append_seal_on(conn, first, origin="actor")
        tf.append_seal_on(conn, second, origin="actor")
        self.assertIsNone(tf.read_seal_on(
            conn, task_instance_id="instance-1", stage="stage1",
            item_key="missing"))
        rows = tf.list_seals_on(
            conn, task_instance_id="instance-1", stage="stage1")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["payload"]["item_key"], "K1")
        self.assertEqual(
            len(tf.list_seals_on(
                conn, task_instance_id="instance-1", origin="actor")),
            2,
        )
        self.assertEqual(
            tf.list_seals_on(
                conn, task_instance_id="instance-1", origin="collab"),
            [],
        )

    def test_missing_table_is_not_prepared(self):
        empty = self._new_db(self.base / "empty.db")
        conn = self._open(empty, write=True)
        payload = make_payload()
        self.assert_unavailable(
            "not_prepared",
            lambda: tf.append_seal_on(conn, payload, origin="actor"))
        self.assertTrue(conn.in_transaction)
        read_conn = self._open(empty, write=False)
        self.assert_unavailable(
            "not_prepared",
            lambda: tf.read_seal_on(
                read_conn, task_instance_id="instance-1", stage="stage1",
                item_key="K1"))
        self.assert_unavailable(
            "not_prepared",
            lambda: tf.list_seals_on(read_conn, task_instance_id="instance-1"))

    def test_missing_trigger_is_schema_mismatch(self):
        payload = make_payload()
        conn = self._open(write=True)
        tf.append_seal_on(conn, payload, origin="actor")
        before = set(self.triggers(conn))
        trigger = sorted(before)[0]
        conn.execute('DROP TRIGGER "%s"' % trigger)
        conn.commit()
        conn.close()

        read_conn = self._open(write=False)
        self.assert_unavailable(
            "schema_mismatch",
            lambda: tf.read_seal_on(
                read_conn, task_instance_id="instance-1", stage="stage1",
                item_key="K1"))
        self.assert_unavailable(
            "schema_mismatch",
            lambda: tf.list_seals_on(read_conn, task_instance_id="instance-1"))
        with closing(sqlite3.connect(str(self.db))) as check, check:
            self.assertEqual(set(self.triggers(check)), before - {trigger})
            self.assertEqual(
                check.execute(
                    "SELECT COUNT(*) FROM team_terminal_seals").fetchone()[0],
                1,
            )

    def test_wrong_schema_is_schema_mismatch(self):
        path = self._new_db(self.base / "wrong_read.db")
        with closing(sqlite3.connect(str(path))) as conn, conn:
            conn.execute(
                "CREATE TABLE team_terminal_seals (seal_id TEXT PRIMARY KEY)")
        conn = self._open(path, write=False)
        self.assert_unavailable(
            "schema_mismatch",
            lambda: tf.read_seal_on(
                conn, task_instance_id="instance-1", stage="stage1",
                item_key="K1"))
        self.assert_unavailable(
            "schema_mismatch",
            lambda: tf.list_seals_on(conn, task_instance_id="instance-1"))

    def test_corrupt_json_hash_and_row_binding(self):
        payload = make_payload()
        conn = self._open(write=True)
        self.insert_raw(
            conn, make_payload(item_key="bad-json"), payload_json="{bad",
            seal_id="0" * 64, content_sha256="0" * 64,
            payload_sha256="0" * 64)
        self.insert_raw(
            conn, make_payload(item_key="bad-hash"),
            payload_sha256="0" * 64)
        self.insert_raw(
            conn, make_payload(item_key="bad-binding"), seal_id="1" * 64)
        self.insert_raw(
            conn, make_payload(item_key="bad-time"), sealed_at=STAMP2)
        self.insert_raw(
            conn, make_payload(item_key="bad-kind"), kind="adjudicated")
        self.insert_raw(
            conn, make_payload(item_key="bad-final"), final_decision="exclude")
        self.insert_raw(
            conn, make_payload(item_key="bad-origin"), origin="collab")
        conn.commit()
        conn.close()

        check = self._open(write=False)
        for key in ("bad-json", "bad-hash", "bad-binding", "bad-time",
                    "bad-kind", "bad-final", "bad-origin"):
            with self.subTest(key=key):
                self.assert_unavailable(
                    "corrupt",
                    lambda key=key: tf.read_seal_on(
                        check, task_instance_id="instance-1", stage="stage1",
                        item_key=key))
        self.assert_unavailable(
            "corrupt",
            lambda: tf.list_seals_on(check, task_instance_id="instance-1"))

    def test_list_does_not_skip_one_bad_row(self):
        valid = make_payload(item_key="K1")
        conn = self._open(write=True)
        tf.append_seal_on(conn, valid, origin="actor")
        self.insert_raw(
            conn, make_payload(item_key="K2"), payload_json="{bad",
            seal_id="0" * 64, content_sha256="0" * 64,
            payload_sha256="0" * 64)
        conn.commit()
        conn.close()

        check = self._open(write=False)
        self.assert_unavailable(
            "corrupt",
            lambda: tf.list_seals_on(check, task_instance_id="instance-1"))

    def test_triggers_block_update_and_delete(self):
        payload = make_payload()
        conn = self._open(write=True)
        tf.append_seal_on(conn, payload, origin="actor")
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute(
                "UPDATE team_terminal_seals SET final_decision='exclude'")
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM team_terminal_seals")
        row = self.raw_row(conn, payload)
        self.assertEqual(row["final_decision"], "include")
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM team_terminal_seals").fetchone()[0],
            1,
        )


class ArchiveTests(TerminalFactsTestBase):
    def _actor_record(self, payload=None):
        payload = payload or make_payload()
        conn = self._open(write=True)
        tf.append_seal_on(conn, payload, origin="actor")
        record = tf.read_seal_on(
            conn, task_instance_id=payload["task"]["task_instance_id"],
            stage=payload["stage"], item_key=payload["item_key"])
        conn.commit()
        conn.close()
        return payload, record

    def test_archive_exact_copy_and_idempotent(self):
        payload, record = self._actor_record()
        target = self._prepared_db("collab_archive.db")
        target_conn = self._open(target, write=True)
        archived = tf.archive_seals_on(target_conn, [record])
        self.assertEqual(len(archived), 1)
        self.assert_record(
            archived[0], payload, origin="actor_archive",
            archived_at=archived[0]["archived_at"])
        self.assertIsNotNone(archived[0]["archived_at"])

        source_row = self.raw_row(target_conn, payload)
        actor_conn = self._open(self.db, write=False)
        actor_row = self.raw_row(actor_conn, payload)
        self.assertEqual(source_row["payload_json"], actor_row["payload_json"])
        self.assertEqual(source_row["seal_id"], actor_row["seal_id"])
        self.assertEqual(
            source_row["content_sha256"], actor_row["content_sha256"])
        self.assertEqual(
            source_row["payload_sha256"], actor_row["payload_sha256"])
        self.assertEqual(source_row["sealed_at"], actor_row["sealed_at"])
        self.assertEqual(source_row["origin"], "actor_archive")
        self.assertEqual(actor_row["origin"], "actor")
        self.assertIsNone(actor_row["archived_at"])

        archived_at = archived[0]["archived_at"]
        again = tf.archive_seals_on(
            target_conn, [record], archived_at=STAMP2)
        self.assertEqual(again[0]["archived_at"], archived_at)
        self.assertEqual(
            target_conn.execute(
                "SELECT COUNT(*) FROM team_terminal_seals").fetchone()[0],
            1,
        )

    def test_archive_does_not_reorigin_existing_actor_row(self):
        payload, record = self._actor_record()
        target = self._prepared_db("collab_actor_original.db")
        target_conn = self._open(target, write=True)
        self.insert_raw(target_conn, payload, origin="actor")
        with self.assertRaises(tf.TerminalFactsConflict):
            tf.archive_seals_on(target_conn, [record])
        row = self.raw_row(target_conn, payload)
        self.assertEqual(row["origin"], "actor")
        self.assertIsNone(row["archived_at"])
        self.assertEqual(
            row["payload_json"],
            expected_metadata(payload)["payload_json"],
        )
        self.assertEqual(
            target_conn.execute(
                "SELECT COUNT(*) FROM team_terminal_seals").fetchone()[0],
            1,
        )
        self.assertTrue(target_conn.in_transaction)

    def test_archive_batch_prevalidation_zero_dml(self):
        payload, record = self._actor_record()
        target = self._prepared_db("collab_batch.db")
        target_conn = self._open(target, write=True)
        bad = copy.deepcopy(record)
        bad["payload"] = {}
        with self.assertRaises(tf.TerminalFactsPayloadInvalid):
            tf.archive_seals_on(target_conn, [record, bad])
        self.assertEqual(
            target_conn.execute(
                "SELECT COUNT(*) FROM team_terminal_seals").fetchone()[0],
            0,
        )
        self.assertTrue(target_conn.in_transaction)
        archived = tf.archive_seals_on(target_conn, [record])
        self.assertEqual(len(archived), 1)
        self.assertEqual(
            target_conn.execute(
                "SELECT COUNT(*) FROM team_terminal_seals").fetchone()[0],
            1,
        )

    def test_archive_batch_second_insert_storage_failure_rollback(self):
        first_payload = make_payload(item_key="LIMIT-1")
        second_payload = make_payload(item_key="LIMIT-2")
        for opinion in second_payload["opinions"]:
            opinion["notes"] = "x" * 30000
        refresh_payload(second_payload)

        source = self._open(write=True)
        tf.append_seal_on(source, first_payload, origin="actor")
        tf.append_seal_on(source, second_payload, origin="actor")
        first_record = tf.read_seal_on(
            source, task_instance_id="instance-1", stage="stage1",
            item_key="LIMIT-1")
        second_record = tf.read_seal_on(
            source, task_instance_id="instance-1", stage="stage1",
            item_key="LIMIT-2")
        source.commit()
        source.close()

        first_json = expected_metadata(first_payload)["payload_json"]
        second_json = expected_metadata(second_payload)["payload_json"]
        target = self._prepared_db("collab_limit.db")
        target_conn = self._open(target, write=True)
        limit = max(8192, len(first_json) + 2048)
        self.assertLess(len(first_json), limit)
        self.assertGreater(len(second_json), limit)
        target_conn.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, limit)
        try:
            with self.assertRaises(tf.TerminalFactsUnavailable) as caught:
                tf.archive_seals_on(
                    target_conn, [first_record, second_record])
            self.assertEqual(caught.exception.reason, "unreadable")
            cause = (
                caught.exception.__cause__
                if caught.exception.__cause__ is not None
                else caught.exception.__context__)
            self.assertIsInstance(cause, sqlite3.DataError)
            self.assertTrue(target_conn.in_transaction)
            self.assertEqual(
                target_conn.execute(
                    "SELECT COUNT(*) FROM team_terminal_seals"
                ).fetchone()[0],
                1,
            )
        finally:
            target_conn.rollback()
            target_conn.close()

        check = self._open(target, write=False)
        self.assertEqual(
            check.execute(
                "SELECT COUNT(*) FROM team_terminal_seals").fetchone()[0], 0)
        self.assertIsNone(tf.read_seal_on(
            check, task_instance_id="instance-1", stage="stage1",
            item_key="LIMIT-1"))
        self._close(check)

    def test_archive_same_content_collision_preserves_old_archived_at(self):
        payload = make_payload()
        source = self._open(write=True)
        tf.append_seal_on(source, payload, origin="actor")
        record = tf.read_seal_on(
            source, task_instance_id="instance-1", stage="stage1",
            item_key="K1")
        source.commit()
        source.close()

        expected = expected_metadata(payload)
        target = self._prepared_db("collab_collision.db")
        _ArchiveCollisionConnection.collision_done = False
        _ArchiveCollisionConnection.insert_events = []
        _ArchiveCollisionConnection.collision_sql = (
            "INSERT INTO main.team_terminal_seals("
            "seal_id,task_instance_id,stage,item_key,kind,final_decision,"
            "content_sha256,payload_sha256,sealed_at,archived_at,origin,payload_json"
            ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?)"
        )
        _ArchiveCollisionConnection.collision_params = (
            expected["seal_id"],
            payload["task"]["task_instance_id"],
            payload["stage"],
            payload["item_key"],
            payload["kind"],
            payload["final_decision"],
            expected["content_sha256"],
            expected["payload_sha256"],
            payload["sealed_at"],
            STAMP2,
            "actor_archive",
            expected["payload_json"],
        )
        conn = sqlite3.connect(
            str(target), timeout=10, factory=_ArchiveCollisionConnection)
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN IMMEDIATE")
        self.addCleanup(self._close, conn)

        archived = tf.archive_seals_on(conn, [record])
        self.assertEqual(len(archived), 1)
        self.assertEqual(archived[0]["archived_at"], STAMP2)
        self.assertTrue(_ArchiveCollisionConnection.collision_done)
        self.assertTrue(_ArchiveCollisionConnection.insert_events)
        self.assertEqual(
            conn.execute(
                "SELECT COUNT(*) FROM main.team_terminal_seals"
            ).fetchone()[0],
            1,
        )
        row = conn.execute(
            "SELECT archived_at FROM main.team_terminal_seals "
            "WHERE task_instance_id=? AND stage=? AND item_key=?",
            ("instance-1", "stage1", "K1"),
        ).fetchone()
        self.assertEqual(row["archived_at"], STAMP2)
        self.assertTrue(conn.in_transaction)
        conn.rollback()
        conn.close()

    def test_archive_conflict_does_not_overwrite(self):
        first_payload = make_payload(final_decision="include")
        second_payload = make_payload(final_decision="exclude")
        for opinion in second_payload["opinions"]:
            opinion["decision"] = "exclude"
        refresh_payload(second_payload)

        first_db = self._prepared_db("actor_first.db")
        second_db = self._prepared_db("actor_second.db")
        first_conn = self._open(first_db, write=True)
        tf.append_seal_on(first_conn, first_payload, origin="actor")
        first_record = tf.read_seal_on(
            first_conn, task_instance_id="instance-1", stage="stage1",
            item_key="K1")
        first_conn.commit()
        first_conn.close()
        second_conn = self._open(second_db, write=True)
        tf.append_seal_on(second_conn, second_payload, origin="actor")
        second_record = tf.read_seal_on(
            second_conn, task_instance_id="instance-1", stage="stage1",
            item_key="K1")
        second_conn.commit()
        second_conn.close()

        target = self._prepared_db("collab_conflict.db")
        target_conn = self._open(target, write=True)
        tf.archive_seals_on(target_conn, [first_record])

        new_payload = make_payload(item_key="K2")
        first_conn = self._open(first_db, write=True)
        tf.append_seal_on(first_conn, new_payload, origin="actor")
        new_record = tf.read_seal_on(
            first_conn, task_instance_id="instance-1", stage="stage1",
            item_key="K2")
        first_conn.commit()
        first_conn.close()

        with self.assertRaises(tf.TerminalFactsConflict):
            tf.archive_seals_on(target_conn, [new_record, second_record])
        self.assertTrue(target_conn.in_transaction)
        self.assertIsNone(tf.read_seal_on(
            target_conn, task_instance_id="instance-1", stage="stage1",
            item_key="K2"))
        row = self.raw_row(target_conn, first_payload)
        self.assertEqual(
            row["payload_json"],
            expected_metadata(first_payload)["payload_json"],
        )
        self.assertEqual(
            target_conn.execute(
                "SELECT COUNT(*) FROM team_terminal_seals").fetchone()[0],
            1,
        )

    def test_archive_rejects_non_actor_or_non_last_vote_sources(self):
        collab = self._prepared_db("collab_sources.db")
        conn = self._open(collab, write=True)
        adjudicated = make_payload(kind="adjudicated")
        tf.append_seal_on(conn, adjudicated, origin="collab")
        adjudicated_record = tf.read_seal_on(
            conn, task_instance_id="instance-1", stage="stage1",
            item_key="K1")
        roster_change = make_payload(
            trigger="roster_change", source_registration_id=None,
            roster_revision=None, item_key="K2")
        tf.append_seal_on(conn, roster_change, origin="collab")
        roster_change_record = tf.read_seal_on(
            conn, task_instance_id="instance-1", stage="stage1",
            item_key="K2")
        conn.commit()
        conn.close()

        target = self._prepared_db("collab_archive_reject.db")
        target_conn = self._open(target, write=True)
        with self.assertRaises(tf.TerminalFactsPayloadInvalid):
            tf.archive_seals_on(target_conn, [adjudicated_record])
        with self.assertRaises(tf.TerminalFactsPayloadInvalid):
            tf.archive_seals_on(target_conn, [roster_change_record])
        self.assertTrue(target_conn.in_transaction)
        self.assertEqual(
            target_conn.execute(
                "SELECT COUNT(*) FROM team_terminal_seals").fetchone()[0],
            0,
        )

    def test_archive_target_missing_table_is_not_prepared(self):
        _, record = self._actor_record()
        empty = self._new_db(self.base / "empty_target.db")
        target_conn = self._open(empty, write=True)
        self.assert_unavailable(
            "not_prepared",
            lambda: tf.archive_seals_on(target_conn, [record]))
        self.assertTrue(target_conn.in_transaction)

    def test_archive_transaction_rollback_and_commit(self):
        _, record = self._actor_record()
        target = self._prepared_db("collab_tx.db")

        target_conn = self._open(target, write=True)
        tf.archive_seals_on(target_conn, [record])
        self.assertEqual(
            target_conn.execute(
                "SELECT COUNT(*) FROM team_terminal_seals").fetchone()[0], 1)
        target_conn.rollback()
        target_conn.close()
        check = self._open(target, write=False)
        self.assertIsNone(tf.read_seal_on(
            check, task_instance_id="instance-1", stage="stage1",
            item_key="K1"))
        check.rollback()
        check.close()

        target_conn = self._open(target, write=True)
        archived = tf.archive_seals_on(target_conn, [record])
        target_conn.commit()
        target_conn.close()
        check = self._open(target, write=False)
        self.assert_record(
            tf.read_seal_on(
                check, task_instance_id="instance-1", stage="stage1",
                item_key="K1"),
            record["payload"], origin="actor_archive",
            archived_at=archived[0]["archived_at"],
        )


class CanonicalTests(unittest.TestCase):
    def test_canonical_json_parity_with_arbitration_serializer(self):
        from coscreen.screening_arbitration import _canonical_json

        values = [
            None,
            True,
            False,
            0,
            1.5,
            "plain",
            "zh-\u4e2d\u6587",
            {"z": 1, "a": [True, None, "x"]},
            {"nested": {"b": 2, "a": "\u00e9"}},
        ]
        for value in values:
            with self.subTest(value=value):
                self.assertEqual(canonical(value), _canonical_json(value))

    def test_expected_hashes_are_stdlib_computed(self):
        payload = make_payload()
        expected = expected_metadata(payload)
        self.assertEqual(expected["content_sha256"], sha256_json({
            key: value for key, value in payload.items() if key != "sealed_at"
        }))
        self.assertEqual(
            expected["payload_sha256"], sha256_text(canonical(payload)))
        self.assertEqual(
            expected["seal_id"],
            sha256_json({
                "schema": "team_terminal_seal_id.v1",
                "task_instance_id": "instance-1",
                "stage": "stage1",
                "item_key": "K1",
                "content_sha256": expected["content_sha256"],
            }),
        )


if __name__ == "__main__":
    unittest.main()
