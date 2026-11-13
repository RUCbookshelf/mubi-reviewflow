"""Highlight comment persistence/authorization contracts, using only temporary data.

Run this file in an isolated pytest process. RF_PRODUCT_ROOT and
RF_BACKEND_CANDIDATE optionally select a staged backend before integration.
Authentication decoding uses synthetic identities; no users or secrets are saved.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import sys
import threading
from types import SimpleNamespace
import uuid

import pytest
from fastapi.testclient import TestClient


USERS = {
    "owner": {"user_id": 71001, "username": "note_owner"},
    "peer": {"user_id": 71002, "username": "note_peer"},
    "outsider": {"user_id": 71003, "username": "note_outsider"},
    "reconciler": {"user_id": 71004, "username": "note_reconciler"},
}


@pytest.fixture(scope="module")
def backend(tmp_path_factory):
    root = Path(os.environ.get("RF_PRODUCT_ROOT", Path(__file__).resolve().parents[2]))
    source = Path(os.environ.get("RF_BACKEND_CANDIDATE", root / "custom_backend/main.py"))
    data = tmp_path_factory.mktemp("note")
    with pytest.MonkeyPatch.context() as patch:
        patch.syspath_prepend(str(root))
        patch.setenv("COBOOKSHELF_DATA_DIR", str(data))
        patch.setenv("REVIEWFLOW_MODE", "server")
        module_name = "custom_backend._highlight_note_test_app"
        spec = importlib.util.spec_from_file_location(module_name, source)
        module = importlib.util.module_from_spec(spec)
        patch.setitem(sys.modules, module_name, module)
        spec.loader.exec_module(module)

        def decode(token):
            user = USERS.get(token)
            return {"sub": str(user["user_id"]), "username": user["username"]} if user else None

        patch.setattr(module.auth, "decode_access_token", decode)
        patch.setattr(module.auth, "screener_storage_conflict", lambda _name: False)
        yield module
        assert not list(data.rglob(".jwt_secret"))
        assert not list(data.rglob("users.db"))


@pytest.fixture
def sample(backend):
    from coscreen import db, tasks, task_collaboration, fulltext
    from coscreen.models import Article

    task = tasks.create_task("note " + uuid.uuid4().hex[:8], data_dir=backend.DATA_DIR)
    task_collaboration.initialize_task_store(
        task.dir, task_id=task.task_id, owner_user_id=USERS["owner"]["user_id"],
        owner_username=USERS["owner"]["username"],
    )
    store = task_collaboration.shared_store_path(task.dir)
    for who, role in (("peer", "coder"), ("reconciler", "reconciler")):
        task_collaboration.grant_role(
            store, actor_user_id=USERS["owner"]["user_id"],
            user_id=USERS[who]["user_id"], role=role, task_id=task.task_id,
        )
    runtime = backend.app.state.review_workflow
    databases = {}
    for who in ("owner", "peer", "reconciler"):
        tasks.register_screener(task.task_id, USERS[who]["username"], backend.DATA_DIR)
        ctx = runtime.context(task.task_id, USERS[who])
        databases[who] = ctx.actor_db
        db.upsert_articles(ctx.actor_db, [Article(zotero_key="HK1", title="First article"),
                                           Article(zotero_key="HK2", title="Second article")], [])
        db.save_decision(ctx.actor_db, "HK1", "include")
        db.save_decision(ctx.actor_db, "HK2", "include")
        fulltext.add_highlight_rects(ctx.actor_db, "HK1", [
            {"kind": "highlight", "page": 1, "text": "first phrase", "note": "first note",
             "color": "jade", "rects": [[0.1, 0.2, 0.3, 0.4]]},
            {"kind": "highlight", "page": 1, "text": "second phrase", "note": "second note",
             "color": "amber", "rects": [[0.5, 0.6, 0.7, 0.8]]},
        ])
    ctx = runtime.context(task.task_id, USERS["owner"])
    runtime.sync_catalog(ctx)

    def rows(who="owner"):
        with closing(db._connect(databases[who])) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(row) for row in conn.execute("SELECT * FROM highlights ORDER BY id")]

    client = TestClient(backend.app)
    url = f"/api/tasks/{task.task_id}/highlights"

    def edit(note="edited note", expected="first note", *, who="owner", key="HK1",
             highlight_id=1, query="", extra=None):
        body = {"zotero_key": key, "note": note, "expected_note": expected}
        if extra:
            body.update(extra)
        return client.patch(f"{url}/{highlight_id}{query}", json=body,
                            headers={"Authorization": f"Bearer {who}"})

    yield SimpleNamespace(backend=backend, client=client, task=task, db=databases,
                          rows=rows, edit=edit, url=url, context=ctx, store=store)
    client.close()


@pytest.mark.parametrize("who", ["owner", "peer"])
def test_save_only_selected_highlight_and_clear(sample, who):
    before = {name: sample.rows(name) for name in ("owner", "peer")}
    result = sample.edit("  Evidence comment 中文  ", who=who)
    assert result.status_code == 200, result.text
    assert result.json() == {"highlight": {"id": 1, "zotero_key": "HK1", "note": "Evidence comment 中文"}}
    after = sample.rows(who)
    expected = dict(before[who][0], note="Evidence comment 中文")
    assert after == [expected, before[who][1]]
    other = "peer" if who == "owner" else "owner"
    assert sample.rows(other) == before[other]
    fetched = sample.client.get(f"{sample.url}/HK1", headers={"Authorization": f"Bearer {who}"})
    assert fetched.status_code == 200
    assert fetched.json()["highlights"][0]["note"] == "Evidence comment 中文"
    cleared = sample.edit("", expected="Evidence comment 中文", who=who)
    assert cleared.status_code == 200
    assert sample.rows(who)[0]["note"] == ""


@pytest.mark.parametrize("changes", [{"key": "HK2"}, {"highlight_id": 99999}])
def test_missing_or_mismatched_highlight_is_404(sample, changes):
    before = sample.rows()
    result = sample.edit(**changes)
    assert result.status_code == 404, result.text
    assert sample.rows() == before


def test_stale_expected_note_cannot_overwrite(sample):
    assert sample.edit("newer comment").status_code == 200
    result = sample.edit("stale draft")
    assert result.status_code == 409, result.text
    assert sample.rows()[0]["note"] == "newer comment"
    assert sample.rows()[1]["note"] == "second note"


def test_simultaneous_expected_note_updates_have_one_winner(sample):
    start = threading.Barrier(2)

    def update(text):
        start.wait(timeout=5)
        return sample.edit(text)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(update, ["window A", "window B"]))
    assert sorted(response.status_code for response in results) == [200, 409]
    committed = next(response.json()["highlight"]["note"] for response in results if response.status_code == 200)
    assert sample.rows()[0]["note"] == committed
    assert sample.rows()[1]["note"] == "second note"


@pytest.mark.parametrize("who,query", [
    ("owner", "?screener=note_peer"),
    ("peer", "?screener=note_owner"),
    ("owner", "?screener=note_owner&screener=note_peer"),
    ("outsider", ""),
    ("reconciler", ""),
])
def test_foreign_database_and_disallowed_members_are_rejected(sample, who, query):
    before = {name: sample.rows(name) for name in ("owner", "peer", "reconciler")}
    result = sample.edit(who=who, query=query)
    assert result.status_code == 403, result.text
    assert {name: sample.rows(name) for name in before} == before


def test_authentication_required(sample):
    before = sample.rows()
    result = sample.client.patch(f"{sample.url}/1", json={
        "zotero_key": "HK1", "note": "attack", "expected_note": "first note"})
    assert result.status_code == 401
    assert sample.edit(who="invalid").status_code == 401
    assert sample.rows() == before


@pytest.mark.parametrize("mode,reason", [("paused", "stage_paused"), ("selected", "article_outside_scope")])
def test_stage_and_source_policy_reject_comment_write(sample, mode, reason):
    from coscreen import review_workflow
    state = review_workflow.settings(sample.context.owner_db)
    stages = state["stages"]
    stages["fulltext"] = {"mode": mode, "source_ids": ["another-source"] if mode == "selected" else []}
    with closing(review_workflow.connect(sample.context.owner_db)) as conn, conn:
        if mode == "selected":
            conn.execute("INSERT INTO workflow_sources(id,name,normalized_name,active,created_at) VALUES(?,?,?,?,?)",
                         ("another-source", "Another source", "another source", 1, "2026-01-01"))
        conn.execute("UPDATE workflow_state SET stages_json=? WHERE singleton=1", (json.dumps(stages),))
    before = sample.rows()
    result = sample.edit()
    assert result.status_code == 409, result.text
    assert result.json()["detail"]["code"] == reason
    assert sample.rows() == before


def test_expected_workflow_version_and_initial_eligibility(sample):
    from coscreen import db, review_workflow
    state = review_workflow.settings(sample.context.owner_db)
    before = sample.rows()
    conflict = sample.edit(extra={"expected_policy_version": state["version"] + 1})
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "workflow_version_conflict"
    db.save_decision(sample.db["owner"], "HK1", "exclude", exclusion_reason="Outside scope")
    excluded = sample.edit()
    assert excluded.status_code == 409
    assert excluded.json()["detail"]["code"] == "article_not_eligible"
    assert sample.rows() == before


@pytest.mark.parametrize("changes,status", [
    ({"note": "x" * 4001}, 422),
    ({"expected": "x" * 4001}, 422),
    ({"extra": {"color": "terra"}}, 422),
    ({"key": "../HK1"}, 400),
])
def test_schema_validation_cannot_mutate_other_fields(sample, changes, status):
    before = sample.rows()
    result = sample.edit(**changes)
    assert result.status_code == status, result.text
    assert sample.rows() == before
