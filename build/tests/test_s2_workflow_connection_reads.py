# -*- coding: utf-8 -*-
"""S2-C1 borrowed connection reads for WorkflowRuntime and coding_progress_on.

This file is test-only.  It is written statically and is not imported/run by the
author.  It targets the frozen C1 API plus the fixed coding_progress_on signature;
it does not implement or test the pending final-roster policy.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing, contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from fastapi import HTTPException  # noqa: E402

from coscreen import coding  # noqa: E402
from coscreen import coding_evidence as ce  # noqa: E402
from coscreen import db as dbm  # noqa: E402
from coscreen import review_workflow as wf  # noqa: E402
from coscreen import task_collaboration as collab  # noqa: E402
from custom_backend import review_workflow_api as api  # noqa: E402
from custom_backend import team_screening_runtime as tsr  # noqa: E402
from custom_backend.team_screening_write import TeamWriteInputs  # noqa: E402

TASK_ID = "task-c1-read"
USERS = {"owner": 1, "alice": 2, "bob": 3}
STAMP = "2026-11-15T00:00:00+00:00"
DIM_IDS = (1, 2)
COMPACT_KEYS = {
    "zotero_key", "title", "authors", "journal", "year", "doi",
    "item_type", "is_duplicate_of", "import_order",
}


class _ProbeConnection(sqlite3.Connection):
    """Native connection recording SQL and explicit ownership calls."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.statements = []
        self.commit_calls = 0
        self.rollback_calls = 0
        self.close_calls = 0
        self.executescript_calls = 0
        self.set_trace_callback(self.statements.append)

    def reset_probe(self):
        self.statements.clear()
        self.commit_calls = 0
        self.rollback_calls = 0
        self.close_calls = 0
        self.executescript_calls = 0

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


def _open_conn(path, *, factory=None):
    kwargs = {"timeout": 15}
    if factory is not None:
        kwargs["factory"] = factory
    conn = sqlite3.connect(str(path), **kwargs)
    conn.row_factory = sqlite3.Row
    conn.execute("BEGIN IMMEDIATE")
    return conn


def _definition(name, *, description=""):
    return json.dumps({
        "name": name, "dtype": "text", "options": [], "multi_select": False,
        "unit": "", "description": description,
    }, ensure_ascii=False, sort_keys=True)


def _insert_coding(conn, *, keys, note_keys=()):
    dims = (
        dict(id=1, name="Alpha", dtype="text", options=[], multi_select=False,
             unit="", description="", section="s", position=1, created_at=STAMP),
        dict(id=2, name="Beta", dtype="text", options=[], multi_select=False,
             unit="", description="", section="s", position=2, created_at=STAMP),
    )
    for dim in dims:
        conn.execute(
            "INSERT OR REPLACE INTO coding_dimensions"
            "(id,name,dtype,options_json,multi_select,unit,description,section,position,created_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?)",
            (dim["id"], dim["name"], dim["dtype"], json.dumps(dim["options"]),
             int(dim["multi_select"]), dim["unit"], dim["description"],
             dim["section"], dim["position"], dim["created_at"]))
        conn.execute(
            "INSERT OR REPLACE INTO workflow_coding_fields"
            "(dimension_id,field_id,definition_json) VALUES(?,?,?)",
            (dim["id"], f"f{dim['id']}", ce.definition(dim)))
    for key in keys:
        for dim_id in DIM_IDS:
            conn.execute(
                "INSERT OR REPLACE INTO coding_values"
                "(dimension_id,zotero_key,value,updated_at) VALUES(?,?,?,?)",
                (dim_id, key, "yes", STAMP))
    for key in note_keys:
        conn.execute(
            "INSERT INTO coding_notes(dimension_id,zotero_key,text,created_at,updated_at)"
            " VALUES(1,?,?,?,?)", (key, "old note", STAMP, STAMP))


def _seed_store(path, *, articles=("K1", "K2"), decisions=(), stage2=(),
                coding_rows=False, sources=False, note_keys=()):
    dbm.init_db(path)
    ce.migrate(path)
    with closing(sqlite3.connect(str(path))) as conn:
        for order, key in enumerate(articles):
            conn.execute(
                "INSERT OR IGNORE INTO articles"
                "(zotero_key,title,is_duplicate_of,import_order) VALUES(?,?,NULL,?)",
                (key, f"title-{key}", order))
        for key, decision in decisions:
            conn.execute(
                "INSERT OR REPLACE INTO decisions"
                "(zotero_key,decision,exclusion_reason,notes,tags,screened_at,updated_at)"
                " VALUES(?,?,?,?,?,?,?)",
                (key, decision, "", "", "", STAMP, STAMP))
        for key, decision in stage2:
            conn.execute(
                "INSERT OR REPLACE INTO stage2_decisions"
                "(zotero_key,decision,exclusion_reason,notes,tags,screened_at,updated_at)"
                " VALUES(?,?,?,?,?,?,?)",
                (key, decision, "", "", "", STAMP, STAMP))
        if sources:
            conn.execute(
                "INSERT INTO workflow_sources"
                "(id,name,normalized_name,active,created_at) VALUES(?,?,?,?,?)",
                ("S1", "Journal One", "journal one", 1, STAMP))
            conn.execute(
                "INSERT OR IGNORE INTO workflow_article_sources(zotero_key,source_id)"
                " VALUES(?,?)", ("K1", "S1"))
            conn.execute(
                "INSERT OR IGNORE INTO workflow_article_sources(zotero_key,source_id)"
                " VALUES(?,?)", ("K2", wf.UNASSIGNED))
        if coding_rows:
            _insert_coding(conn, keys=articles, note_keys=note_keys)
        conn.execute(
            "UPDATE workflow_state SET evidence_json='[]' WHERE singleton=1")
        conn.commit()


class _Team:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class WorkflowConnectionReadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory(prefix="rf216_s2c_reads_")
        cls.root = Path(cls._tmp.name)
        cls.addClassCleanup(cls._tmp.cleanup)

    def setUp(self):
        self.base = self.root / self.id().rsplit(".", 1)[-1]
        self.base.mkdir(parents=True, exist_ok=True)
        self.runtime = api.WorkflowRuntime(None, None, self.base, lambda *_: True)

    # -- fixtures -----------------------------------------------------------
    def _users_db(self):
        path = self.base / "users.db"
        with closing(sqlite3.connect(str(path))) as conn:
            conn.execute("CREATE TABLE users(id INTEGER PRIMARY KEY, username TEXT NOT NULL)")
            conn.executemany("INSERT INTO users(id,username) VALUES(?,?)",
                             [(USERS["owner"], "owner"),
                              (USERS["alice"], "alice"),
                              (USERS["bob"], "bob")])
            conn.commit()
        return path

    def _make_team(self, *, conflict=False):
        base = self.base / "team"
        task_dir = base / "task"
        task_dir.mkdir(parents=True)
        users_db = self._users_db()
        collab_db = task_dir / "collaboration.db"
        collab.initialize_task_store(
            task_dir, task_id=TASK_ID, owner_user_id=USERS["owner"],
            owner_username="owner")
        collab.grant_role(collab_db, actor_user_id=USERS["owner"],
                          user_id=USERS["alice"], role="coder", task_id=TASK_ID)
        collab.grant_role(collab_db, actor_user_id=USERS["owner"],
                          user_id=USERS["bob"], role="coder", task_id=TASK_ID)
        collab.set_business_role(
            collab_db, actor_user_id=USERS["owner"], user_id=USERS["owner"],
            business_role="reconciler", task_id=TASK_ID)
        if not conflict:
            collab.set_business_role(
                collab_db, actor_user_id=USERS["owner"], user_id=USERS["alice"],
                business_role="coder", task_id=TASK_ID)
            collab.set_business_role(
                collab_db, actor_user_id=USERS["owner"], user_id=USERS["bob"],
                business_role="coder", task_id=TASK_ID)
        else:
            collab.set_business_role(
                collab_db, actor_user_id=USERS["owner"], user_id=USERS["alice"],
                business_role="coder", task_id=TASK_ID)
            collab.grant_role(collab_db, actor_user_id=USERS["owner"],
                              user_id=USERS["bob"], role="reconciler", task_id=TASK_ID)
        owner_db = task_dir / "owner.db"
        alice_db = task_dir / "alice.db"
        bob_db = task_dir / "bob.db"
        _seed_store(owner_db, coding_rows=True, sources=True, note_keys=("K1",))
        _seed_store(
            alice_db,
            decisions=(("K1", "include"), ("K2", "include")),
            stage2=(("K1", "include"), ("K2", "include")),
            coding_rows=True, note_keys=("K1",))
        _seed_store(
            bob_db,
            decisions=(("K1", "include"), ("K2", "exclude")),
            stage2=(("K1", "include"), ("K2", "exclude")))
        return _Team(
            task_dir=task_dir, task_id=TASK_ID, users_db=users_db,
            collab_db=collab_db, owner_db=owner_db, alice_db=alice_db,
            bob_db=bob_db, owner=collab.get_owner(collab_db, task_id=TASK_ID))

    def _ctx(self, team, uid, username, actor_db):
        return api.WorkflowContext(
            info=SimpleNamespace(task_id=team.task_id, dir=team.task_dir),
            user={"user_id": uid, "username": username},
            owner=team.owner, owner_db=team.owner_db, actor_db=actor_db,
            can_manage=(uid == USERS["owner"]), task_kind="team",
            collaboration_db=team.collab_db)

    def _inputs(self, ctx, fx):
        actor_conn = fx.conns[ctx.actor_db]
        return TeamWriteInputs(
            ctx=ctx, scope=fx.scope, collab_conn=fx.conns[fx.team.collab_db],
            owner_conn=fx.conns[fx.team.owner_db], actor_conn=actor_conn,
            target_conn=actor_conn,
            connections=tuple(fx.conns.values()), roster=(),
            _reserved=SimpleNamespace(cleanup_errors=()))

    @contextmanager
    def _open_team(self, *, conflict=False):
        team = self._make_team(conflict=conflict)
        paths = (team.users_db, team.collab_db, team.owner_db,
                 team.alice_db, team.bob_db)
        conns = {path: _open_conn(path) for path in paths}
        info = SimpleNamespace(task_id=team.task_id, dir=team.task_dir)
        scope = tsr.borrowed_team_read_scope(
            info, accounts_path=team.users_db, collab_path=team.collab_db,
            owner_path=team.owner_db,
            member_paths={USERS["alice"]: team.alice_db, USERS["bob"]: team.bob_db},
            connections=conns, missing_members=frozenset())
        fx = SimpleNamespace(team=team, conns=conns, scope=scope,
                             owner_conn=conns[team.owner_db],
                             alice_conn=conns[team.alice_db],
                             bob_conn=conns[team.bob_db])
        fx.owner_ctx = self._ctx(team, USERS["owner"], "owner", team.owner_db)
        fx.alice_ctx = self._ctx(team, USERS["alice"], "alice", team.alice_db)
        fx.bob_ctx = self._ctx(team, USERS["bob"], "bob", team.bob_db)
        for ctx in (fx.owner_ctx, fx.alice_ctx, fx.bob_ctx):
            ctx.write_inputs = self._inputs(ctx, fx)
        try:
            yield fx
        finally:
            for conn in reversed(list(conns.values())):
                try:
                    if conn.in_transaction:
                        conn.rollback()
                finally:
                    conn.close()

    # -- 1. context / retained-input contract ------------------------------
    def test_01_write_inputs_contract_and_no_path_fallback(self):
        # WorkflowContext tail field defaults to None and preserves old construction.
        ctx0 = api.WorkflowContext(
            SimpleNamespace(task_id="x", dir=self.base), {}, None,
            self.base / "a.db", self.base / "b.db", False, "personal",
            self.base / "collab.db")
        self.assertIsNone(ctx0.write_inputs)

        db_path = self.base / "one.db"
        dbm.init_db(db_path)
        conn = _open_conn(db_path)
        ctx = SimpleNamespace(task_kind="team", write_inputs=None)
        inputs = SimpleNamespace(ctx=ctx, connections=(conn,),
                                 owner_conn=conn, actor_conn=conn)
        ctx.write_inputs = inputs
        self.assertIs(api._team_inputs(ctx), inputs)

        def expect_runtime(mutator):
            old = (ctx.task_kind, inputs.ctx, inputs.connections,
                   inputs.owner_conn, inputs.actor_conn)
            try:
                mutator()
                with self.assertRaises(RuntimeError):
                    api._team_inputs(ctx)
            finally:
                (ctx.task_kind, inputs.ctx, inputs.connections,
                 inputs.owner_conn, inputs.actor_conn) = old

        expect_runtime(lambda: setattr(inputs, "ctx", object()))
        expect_runtime(lambda: setattr(ctx, "task_kind", "personal"))
        expect_runtime(lambda: setattr(inputs, "connections", ()))
        expect_runtime(lambda: setattr(inputs, "connections", (conn, object())))
        expect_runtime(lambda: setattr(inputs, "connections", (conn,))
                       or setattr(inputs, "owner_conn", object()))
        ctx.task_kind = "team"

        inactive = sqlite3.connect(str(db_path), timeout=15)
        inactive.row_factory = sqlite3.Row
        no_row = sqlite3.connect(str(db_path), timeout=15)
        no_row.execute("BEGIN")
        closed = sqlite3.connect(str(db_path), timeout=15)
        closed.close()
        try:
            for bad, owner_bad, actor_bad in (
                    ((inactive,), inactive, inactive),
                    ((no_row,), no_row, no_row),
                    ((closed,), closed, closed)):
                inputs.connections, inputs.owner_conn, inputs.actor_conn = bad, owner_bad, actor_bad
                with self.assertRaises((RuntimeError, sqlite3.ProgrammingError)):
                    api._team_inputs(ctx)
        finally:
            inactive.close()
            no_row.rollback()
            no_row.close()

        # Invalid windows must not attempt a path reconnect.
        inputs.connections, inputs.owner_conn, inputs.actor_conn = (closed,), closed, closed
        with mock.patch.object(api.store, "connect", side_effect=AssertionError("path")), \
                mock.patch("sqlite3.connect", side_effect=AssertionError("connect")):
            with self.assertRaises(sqlite3.ProgrammingError):
                api._team_inputs(ctx)
        conn.rollback()
        conn.close()

    # -- 2. static four helpers: parity, shapes, defaults ------------------
    def test_02_static_reads_parity_shapes_and_defaults(self):
        with self._open_team() as fx:
            owner_conn, actor_conn = fx.owner_conn, fx.alice_conn
            path_rows = api.WorkflowRuntime.article_rows(fx.team.owner_db)
            conn_rows = api.WorkflowRuntime.article_rows(
                fx.team.owner_db, conn=owner_conn)
            self.assertEqual(conn_rows, path_rows)
            compact = api.WorkflowRuntime.article_rows(
                fx.team.owner_db, compact=True, conn=owner_conn)
            self.assertEqual(set(compact[0]), COMPACT_KEYS)
            self.assertEqual(
                api.WorkflowRuntime.article_row(fx.team.owner_db, "K1", conn=owner_conn)["zotero_key"],
                "K1")
            self.assertIsNone(api.WorkflowRuntime.article_row(
                fx.team.owner_db, "missing", conn=owner_conn))

            for stage, table in (("screen", "decisions"),
                                 ("fulltext", "stage2_decisions"),
                                 ("coding", "coding_values")):
                self.assertEqual(
                    api.WorkflowRuntime.saved_rows(
                        fx.team.alice_db, stage, conn=actor_conn),
                    api.WorkflowRuntime.saved_rows(fx.team.alice_db, stage))
            coding_rows = api.WorkflowRuntime.saved_rows(
                fx.team.alice_db, "coding", conn=actor_conn)
            self.assertEqual(coding_rows["K1"], {1: "yes", 2: "yes"})
            self.assertEqual(
                api.WorkflowRuntime.saved_item(
                    fx.team.alice_db, "screen", "K1", conn=actor_conn)["decision"],
                "include")
            self.assertEqual(
                api.WorkflowRuntime.saved_item(
                    fx.team.alice_db, "coding", "K1", conn=actor_conn),
                {1: "yes", 2: "yes"})
            self.assertIsNone(api.WorkflowRuntime.saved_item(
                fx.team.alice_db, "screen", "missing", conn=actor_conn))

            # Uncommitted article/value rows are visible on the borrowed conn only.
            owner_conn.execute(
                "INSERT INTO articles(zotero_key,title,is_duplicate_of,import_order)"
                " VALUES('K3','title-K3',NULL,99)")
            self.assertIn("K3", {r["zotero_key"] for r in api.WorkflowRuntime.article_rows(
                fx.team.owner_db, conn=owner_conn)})
            self.assertNotIn("K3", {r["zotero_key"] for r in api.WorkflowRuntime.article_rows(
                fx.team.owner_db)})
            actor_conn.execute(
                "UPDATE coding_values SET value='changed' WHERE dimension_id=1 AND zotero_key='K1'")
            self.assertEqual(
                api.WorkflowRuntime.saved_rows(
                    fx.team.alice_db, "coding", conn=actor_conn)["K1"][1],
                "changed")
            self.assertEqual(
                api.WorkflowRuntime.saved_rows(
                    fx.team.alice_db, "coding")["K1"][1], "yes")

            bare = self.base / "bare.db"
            sqlite3.connect(str(bare)).close()
            missing = self.base / "missing.db"
            self.assertEqual(api.WorkflowRuntime.article_rows(bare), [])
            self.assertIsNone(api.WorkflowRuntime.article_row(bare, "K1"))
            self.assertEqual(api.WorkflowRuntime.saved_rows(bare, "screen"), {})
            self.assertIsNone(api.WorkflowRuntime.saved_item(bare, "coding", "K1"))
            self.assertEqual(api.WorkflowRuntime.article_rows(missing), [])
            self.assertEqual(api.WorkflowRuntime.saved_rows(missing, "fulltext"), {})

    # -- 3. static helpers: conn-only, no FS/tx control --------------------
    def test_03_static_conn_only_no_fs_or_transaction_control(self):
        db_path = self.base / "probe.db"
        _seed_store(db_path, coding_rows=True)
        conn = _open_conn(db_path, factory=_ProbeConnection)
        conn.reset_probe()
        bogus = self.base / "does-not-exist.db"
        closed = sqlite3.connect(str(db_path))
        closed.close()
        try:
            with mock.patch.object(api.store, "connect",
                                   side_effect=AssertionError("path connect")), \
                    mock.patch("sqlite3.connect", side_effect=AssertionError("connect")), \
                    mock.patch.object(Path, "is_file",
                                      side_effect=AssertionError("FS is_file")):
                self.assertTrue(api.WorkflowRuntime.article_rows(bogus, conn=conn))
                self.assertIsNotNone(api.WorkflowRuntime.article_row(bogus, "K1", conn=conn))
                self.assertIn("K1", api.WorkflowRuntime.saved_rows(bogus, "coding", conn=conn))
                self.assertEqual(
                    api.WorkflowRuntime.saved_item(bogus, "coding", "K1", conn=conn),
                    {1: "yes", 2: "yes"})
            self.assertEqual(
                (conn.commit_calls, conn.rollback_calls, conn.executescript_calls),
                (0, 0, 0))
            self.assertTrue(conn.in_transaction)
            self.assertIs(conn.row_factory, sqlite3.Row)
            self.assertTrue(conn.statements)
            self.assertTrue(all(s.lstrip().upper().startswith("SELECT")
                                for s in conn.statements))
            with self.assertRaises(sqlite3.ProgrammingError):
                api.WorkflowRuntime.article_rows(bogus, conn=closed)
        finally:
            conn.rollback()
            conn.close()

    # -- 4. settings / capability / uncommitted policy ---------------------
    def test_04_settings_capability_and_uncommitted_owner_policy(self):
        with self._open_team() as fx:
            alice, owner = fx.alice_ctx, fx.owner_ctx
            self.assertTrue(self.runtime.can_submit_coding(alice))
            self.assertFalse(self.runtime.can_submit_coding(owner))

            ctx_inputs = alice.write_inputs
            alice.write_inputs = None
            path_settings = self.runtime.settings(alice)
            alice.write_inputs = ctx_inputs
            conn_settings = self.runtime.settings(alice)
            for key in ("version", "evidence_required_field_ids", "stages",
                        "sources", "evidence_fields", "mapping_issues"):
                self.assertEqual(conn_settings[key], path_settings[key])

            owner_conn = fx.owner_conn
            stages = {
                "screen": {"mode": "selected", "source_ids": ["S1"]},
                "fulltext": {"mode": "all", "source_ids": []},
                "coding": {"mode": "all", "source_ids": []},
            }
            owner_conn.execute(
                "UPDATE workflow_state SET version=7, stages_json=?, evidence_json=?"
                " WHERE singleton=1",
                (json.dumps(stages), json.dumps(["f1"])))
            owner_conn.execute(
                "UPDATE coding_dimensions SET description=?"
                " WHERE id=1", ("Alpha changed",))
            owner_conn.execute(
                "INSERT INTO workflow_sources"
                "(id,name,normalized_name,active,created_at) VALUES(?,?,?,?,?)",
                ("S2", "Journal Two", "journal two", 1, STAMP))
            owner_conn.execute(
                "INSERT OR IGNORE INTO workflow_article_sources(zotero_key,source_id)"
                " VALUES(?,?)", ("K2", "S2"))

            alice.write_inputs = ctx_inputs
            conn_settings = self.runtime.settings(alice)
            self.assertEqual(conn_settings["version"], 7)
            self.assertEqual(conn_settings["evidence_required_field_ids"], ["f1"])
            self.assertEqual(conn_settings["stages"]["screen"],
                             {"mode": "selected", "source_ids": ["S1"]})
            self.assertIn("S2", {s["id"] for s in conn_settings["sources"]})
            self.assertTrue(conn_settings["mapping_issues"])
            self.assertIs(conn_settings["submission_evidence"]["available"], True)

            alice.write_inputs = None
            path_settings = self.runtime.settings(alice)
            alice.write_inputs = ctx_inputs
            self.assertEqual(path_settings["version"], 1)
            self.assertNotIn("S2", {s["id"] for s in path_settings["sources"]})
            self.assertFalse(path_settings["mapping_issues"])

            self.assertFalse(self.runtime.can_submit_coding(owner))
            self.assertFalse(self.runtime.settings(owner)["submission_evidence"]["available"])

    # -- 5. capability is coder-only, conflict is not coder ----------------
    def test_05_capability_coder_only_owner_no_bypass_and_conflict(self):
        with self._open_team(conflict=True) as fx:
            self.assertTrue(self.runtime.can_submit_coding(fx.alice_ctx))
            self.assertFalse(self.runtime.can_submit_coding(fx.bob_ctx))
            self.assertFalse(self.runtime.can_submit_coding(fx.owner_ctx))
            self.assertIs(
                self.runtime.settings(fx.bob_ctx)["submission_evidence"]["available"],
                False)
            self.assertIs(
                self.runtime.settings(fx.owner_ctx)["submission_evidence"]["available"],
                False)

    # -- 6. evidence and complete conn reads -------------------------------
    def test_06_evidence_and_complete_see_uncommitted_borrowed_rows(self):
        with self._open_team() as fx:
            alice, actor_conn = fx.alice_ctx, fx.alice_conn
            inputs = alice.write_inputs
            alice.write_inputs = None
            path_before = self.runtime.evidence_state(alice, "K1")
            alice.write_inputs = inputs
            conn_before = self.runtime.evidence_state(alice, "K1")
            self.assertEqual(conn_before, path_before)
            self.assertTrue(self.runtime.coding_complete(alice, "K1", {}, set()))

            actor_conn.execute(
                "UPDATE coding_notes SET text='new note'"
                " WHERE dimension_id=1 AND zotero_key='K1'")
            actor_conn.execute(
                "UPDATE coding_values SET value=''"
                " WHERE dimension_id=1 AND zotero_key='K1'")
            alice.write_inputs = inputs
            conn_after = self.runtime.evidence_state(alice, "K1")
            self.assertEqual(conn_after["evidence"]["1"]["text"], "new note")
            self.assertFalse(self.runtime.coding_complete(alice, "K1", {}, set()))
            alice.write_inputs = None
            path_after = self.runtime.evidence_state(alice, "K1")
            self.assertEqual(path_after["evidence"]["1"]["text"], "old note")
            self.assertTrue(self.runtime.coding_complete(alice, "K1", {}, set()))
            alice.write_inputs = inputs

    # -- 7. validate uses uncommitted owner mapping ------------------------
    def test_07_validate_uses_uncommitted_owner_mapping(self):
        with self._open_team() as fx:
            fx.owner_conn.execute(
                "UPDATE workflow_state SET evidence_json=? WHERE singleton=1",
                (json.dumps(["f1"]),))
            fx.owner_conn.execute(
                "UPDATE coding_dimensions SET description=?"
                " WHERE id=1", ("Alpha changed",))
            self.runtime.validate_evidence_dimensions(fx.alice_ctx, {2})
            with self.assertRaises(HTTPException) as caught:
                self.runtime.validate_evidence_dimensions(fx.alice_ctx, {1})
            self.assertEqual(caught.exception.status_code, 409)

    # -- 8. allowed/secopped use uncommitted owner policy ------------------
    def test_08_allowed_and_scoped_use_uncommitted_owner_policy(self):
        with self._open_team() as fx:
            alice, owner_conn = fx.alice_ctx, fx.owner_conn
            inputs = alice.write_inputs
            rows = [{"zotero_key": "K1"}, {"zotero_key": "K2"}]
            alice.write_inputs = None
            path_allowed_before = self.runtime.allowed_sources(alice, "screen")
            path_scoped_before = [r["zotero_key"] for r in
                                  self.runtime.scoped(alice, "screen", rows)]
            alice.write_inputs = inputs
            conn_allowed_before = self.runtime.allowed_sources(alice, "screen")
            self.assertEqual(conn_allowed_before, path_allowed_before)
            self.assertEqual(
                [r["zotero_key"] for r in self.runtime.scoped(alice, "screen", rows)],
                path_scoped_before)

            stages = {
                "screen": {"mode": "selected", "source_ids": ["S1"]},
                "fulltext": {"mode": "all", "source_ids": []},
                "coding": {"mode": "all", "source_ids": []},
            }
            owner_conn.execute(
                "UPDATE workflow_state SET stages_json=? WHERE singleton=1",
                (json.dumps(stages),))
            self.assertEqual(self.runtime.allowed_sources(alice, "screen"), ["S1"])
            self.assertEqual(
                [r["zotero_key"] for r in self.runtime.scoped(alice, "screen", rows)],
                ["K1"])
            alice.write_inputs = None
            path_allowed_after = self.runtime.allowed_sources(alice, "screen")
            self.assertIn(wf.UNASSIGNED, path_allowed_after)
            self.assertIn("S1", path_allowed_after)
            self.assertEqual(
                [r["zotero_key"] for r in self.runtime.scoped(alice, "screen", rows)],
                ["K1", "K2"])
            alice.write_inputs = inputs

    # -- 9. real TSR coding chain -> coding_result progress ----------------
    def test_09_team_coding_result_uses_real_tsr_coding_queue(self):
        with self._open_team() as fx:
            chain = tsr.team_chain_view(fx.scope, "coding")
            self.assertEqual(chain.stage1.statuses["K1"].status, "final_include")
            self.assertEqual(chain.stage2.statuses["K1"].status, "final_include")
            self.assertTrue(chain.chain["K1"].cleared)
            self.assertFalse(chain.chain["K2"].cleared)
            self.assertEqual(tsr.chain_cleared_keys(chain), ["K1"])

            result = self.runtime.coding_result(fx.alice_ctx, "K1")
            self.assertIs(result["saved"], True)
            self.assertIs(result["complete"], True)
            self.assertIs(result["draft"], False)
            self.assertEqual(set(result["progress"]),
                             {"total", "coded", "remaining", "per_dimension"})
            self.assertEqual(result["progress"]["total"], 1)
            self.assertEqual(result["progress"]["coded"], 1)
            self.assertEqual(result["progress"]["remaining"], 0)
            self.assertEqual(result["progress"]["per_dimension"], {1: 1, 2: 1})

            result_k2 = self.runtime.coding_result(fx.alice_ctx, "K2")
            self.assertEqual(result_k2["progress"]["total"], 1)
            self.assertFalse(chain.chain["K2"].cleared)

    # -- 10. coding_progress_on: explicit queue, empty queue/plan ----------
    def test_10_coding_progress_on_explicit_queue_and_empty_cases(self):
        with self._open_team() as fx:
            def progress(queue):
                return coding.coding_progress_on(
                    fx.alice_conn, owner_conn=fx.owner_conn,
                    actor_db=fx.team.alice_db, owner_db=fx.team.owner_db,
                    queue=queue)

            p1 = progress(["K1"])
            self.assertEqual(set(p1), {"total", "coded", "remaining", "per_dimension"})
            self.assertEqual((p1["total"], p1["coded"], p1["remaining"]), (1, 1, 0))
            self.assertEqual(p1["per_dimension"], {1: 1, 2: 1})
            p2 = progress(["K2"])
            self.assertEqual((p2["total"], p2["coded"]), (1, 1))
            p0 = progress([])
            self.assertEqual((p0["total"], p0["coded"], p0["remaining"]), (0, 0, 0))
            self.assertEqual(p0["per_dimension"], {1: 0, 2: 0})

            fx.alice_conn.execute(
                "UPDATE coding_values SET value='' WHERE dimension_id=1 AND zotero_key='K1'")
            pu = progress(["K1"])
            self.assertEqual((pu["total"], pu["coded"], pu["remaining"]), (1, 0, 1))
            self.assertEqual(pu["per_dimension"], {1: 0, 2: 1})

            for bad_queue in ("K1", b"K1", bytearray(b"K1"), [""], [b"K1"]):
                with self.assertRaises(ValueError):
                    progress(bad_queue)

            inactive = sqlite3.connect(str(fx.team.alice_db), timeout=15)
            inactive.row_factory = sqlite3.Row
            no_row = sqlite3.connect(str(fx.team.alice_db), timeout=15)
            no_row.execute("BEGIN")
            closed = sqlite3.connect(str(fx.team.alice_db), timeout=15)
            closed.close()
            try:
                with self.assertRaises(RuntimeError):
                    coding.coding_progress_on(
                        object(), owner_conn=fx.owner_conn,
                        actor_db=fx.team.alice_db, owner_db=fx.team.owner_db,
                        queue=["K1"])
                with self.assertRaises(RuntimeError):
                    coding.coding_progress_on(
                        inactive, owner_conn=fx.owner_conn,
                        actor_db=fx.team.alice_db, owner_db=fx.team.owner_db,
                        queue=["K1"])
                with self.assertRaises(RuntimeError):
                    coding.coding_progress_on(
                        no_row, owner_conn=fx.owner_conn,
                        actor_db=fx.team.alice_db, owner_db=fx.team.owner_db,
                        queue=["K1"])
                with self.assertRaises(sqlite3.ProgrammingError):
                    coding.coding_progress_on(
                        closed, owner_conn=fx.owner_conn,
                        actor_db=fx.team.alice_db, owner_db=fx.team.owner_db,
                        queue=["K1"])
                with self.assertRaises(RuntimeError):
                    coding.coding_progress_on(
                        fx.alice_conn, owner_conn=inactive,
                        actor_db=fx.team.alice_db, owner_db=fx.team.owner_db,
                        queue=["K1"])
                with self.assertRaises(RuntimeError):
                    coding.coding_progress_on(
                        fx.alice_conn, owner_conn=no_row,
                        actor_db=fx.team.alice_db, owner_db=fx.team.owner_db,
                        queue=["K1"])
                with self.assertRaises(sqlite3.ProgrammingError):
                    coding.coding_progress_on(
                        fx.alice_conn, owner_conn=closed,
                        actor_db=fx.team.alice_db, owner_db=fx.team.owner_db,
                        queue=["K1"])
            finally:
                inactive.close()
                no_row.rollback()
                no_row.close()

        empty_db = self.base / "empty_plan.db"
        dbm.init_db(empty_db)
        ce.migrate(empty_db)
        conn = _open_conn(empty_db)
        try:
            empty = coding.coding_progress_on(
                conn, owner_conn=conn, actor_db=empty_db, owner_db=empty_db,
                queue=["K1", "K2"])
            self.assertEqual(set(empty), {"total", "coded", "remaining", "per_dimension"})
            self.assertEqual((empty["total"], empty["coded"], empty["remaining"]),
                             (2, 0, 2))
            self.assertEqual(empty["per_dimension"], {})
        finally:
            conn.rollback()
            conn.close()

    # -- 11. no path reconnect / no transaction ownership -----------------
    def test_11_team_helpers_do_not_reconnect_or_own_callers_transaction(self):
        with self._open_team() as fx:
            missing = self.base / "missing.db"
            with mock.patch.object(api.store, "connect",
                                   side_effect=AssertionError("path connect")), \
                    mock.patch("sqlite3.connect", side_effect=AssertionError("connect")), \
                    mock.patch.object(Path, "is_file",
                                      side_effect=AssertionError("FS is_file")):
                self.assertTrue(self.runtime.settings(fx.alice_ctx))
                self.assertFalse(self.runtime.can_submit_coding(fx.owner_ctx))
                self.runtime.validate_evidence_dimensions(fx.alice_ctx, {1, 2})
                self.assertTrue(self.runtime.evidence_state(fx.alice_ctx, "K1"))
                self.assertTrue(self.runtime.coding_complete(fx.alice_ctx, "K1", {}, set()))
                self.assertTrue(self.runtime.allowed_sources(fx.alice_ctx, "screen"))
                self.assertEqual(
                    [r["zotero_key"] for r in self.runtime.scoped(
                        fx.alice_ctx, "screen", [{"zotero_key": "K1"}])],
                    ["K1"])
                self.assertTrue(self.runtime.coding_result(fx.alice_ctx, "K1")["progress"])
                self.assertTrue(api.WorkflowRuntime.article_rows(missing, conn=fx.owner_conn))
                self.assertIsNotNone(api.WorkflowRuntime.article_row(
                    missing, "K1", conn=fx.owner_conn))
                self.assertTrue(api.WorkflowRuntime.saved_rows(
                    missing, "screen", conn=fx.alice_conn))
                self.assertIsNotNone(api.WorkflowRuntime.saved_item(
                    missing, "screen", "K1", conn=fx.alice_conn))
            for conn in fx.conns.values():
                self.assertTrue(conn.in_transaction)
                self.assertIs(conn.row_factory, sqlite3.Row)

    # -- 12. actor == owner same connection --------------------------------
    def test_12_same_database_and_connection_actor_owner(self):
        with self._open_team() as fx:
            owner = fx.owner_ctx
            self.assertTrue(self.runtime.settings(owner))
            self.runtime.validate_evidence_dimensions(owner, {1, 2})
            self.assertTrue(self.runtime.evidence_state(owner, "K1"))
            self.assertTrue(self.runtime.coding_complete(owner, "K1", {}, set()))
            self.assertTrue(self.runtime.allowed_sources(owner, "screen"))
            self.assertEqual(
                [r["zotero_key"] for r in self.runtime.scoped(
                    owner, "screen", [{"zotero_key": "K1"}, {"zotero_key": "K2"}])],
                ["K1", "K2"])
            self.assertIs(fx.owner_ctx.write_inputs.owner_conn,
                          fx.owner_ctx.write_inputs.actor_conn)


if __name__ == "__main__":
    unittest.main()
