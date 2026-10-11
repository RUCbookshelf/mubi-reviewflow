"""Transactional, personal Undo history for review decisions and coding saves."""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from coscreen.research_trace import TraceContext

STAGES = ("screen", "fulltext", "coding")
SUBMISSION_EVIDENCE = 'coding_submission_evidence'
_DECISION_COLUMNS = "decision,exclusion_reason,notes,tags,screened_at,updated_at"
_TABLES = {"screen": "decisions", "fulltext": "stage2_decisions"}


def begin_save(conn: sqlite3.Connection) -> None:
    """Acquire the write lock before reading snapshots; join the caller's transaction."""
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS workspace_undo ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, actor_id TEXT NOT NULL, "
        "stage TEXT NOT NULL, zotero_key TEXT NOT NULL, before_json TEXT NOT NULL, "
        "after_json TEXT NOT NULL, undone INTEGER NOT NULL DEFAULT 0)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS workspace_undo_latest "
                 "ON workspace_undo(task_id,actor_id,stage,undone,id)")
    if 'request_id' not in {r[1] for r in conn.execute('PRAGMA table_info(workspace_undo)')}:
        conn.execute('ALTER TABLE workspace_undo ADD COLUMN request_id TEXT')


def _row(conn: sqlite3.Connection, table: str, key: str) -> list | None:
    row = conn.execute(f"SELECT {_DECISION_COLUMNS} FROM {table} WHERE zotero_key=?", (key,)).fetchone()
    return list(row) if row else None


def snapshot(conn: sqlite3.Connection, stage: str, key: str,
             dimensions: list[int] | None = None, *, evidence: bool = False) -> dict[str, Any]:
    if stage not in STAGES:
        raise ValueError("Unknown Undo stage.")
    if stage in _TABLES:
        state: dict[str, Any] = {"record": _row(conn, _TABLES[stage], key)}
        # Do not undo past downstream edits or silently make newly completed work ineligible.
        state["coding"] = [list(r) for r in conn.execute(
            "SELECT dimension_id,value,updated_at FROM coding_values WHERE zotero_key=? ORDER BY dimension_id", (key,))]
        state['coding_evidence'] = evidence_snapshot(conn, key)
        if stage == "screen":
            state["fulltext"] = _row(conn, "stage2_decisions", key)
        return state
    values, definitions = {}, {}
    for dim in sorted(set(dimensions or [])):
        row = conn.execute("SELECT value,updated_at FROM coding_values WHERE zotero_key=? AND dimension_id=?",
                           (key, dim)).fetchone()
        values[str(dim)] = list(row) if row else None
        definition = conn.execute("SELECT * FROM coding_dimensions WHERE id=?", (dim,)).fetchone()
        definitions[str(dim)] = list(definition) if definition else None
    result = {"values": values, "definitions": definitions}
    if evidence:
        result['evidence'] = evidence_snapshot(conn, key, dimensions)
        from coscreen.review_workflow import has_table
        result['stable_fields'] = [list(r) for r in conn.execute('SELECT * FROM workflow_coding_fields ORDER BY dimension_id')
                                   if r[0] in (dimensions or [])] if has_table(conn, 'workflow_coding_fields') else []
    return result


def evidence_snapshot(conn: sqlite3.Connection, key: str, dimensions: list[int] | None = None) -> dict:
    from coscreen.coding import _NOTE_COLUMNS, _IMAGE_COLUMNS
    dims = dimensions if dimensions is not None else [r[0] for r in conn.execute('SELECT id FROM coding_dimensions')]
    result = {}
    for dim in sorted(set(dims)):
        note = conn.execute(f"SELECT {','.join(_NOTE_COLUMNS)} FROM coding_notes WHERE zotero_key=? AND dimension_id=?", (key, dim)).fetchone()
        images = [list(r) for r in conn.execute(f"SELECT {','.join(_IMAGE_COLUMNS)} FROM coding_note_images WHERE note_id=? ORDER BY id", (note[0],))] if note else []
        result[str(dim)] = {'note': list(note) if note else None, 'images': images}
    return result


def record_save(conn: sqlite3.Connection, context: TraceContext, stage: str, key: str,
                before: dict, after: dict) -> None:
    context = context.normalized()
    if before == after:
        return
    conn.execute(
        "INSERT INTO workspace_undo(task_id,actor_id,stage,zotero_key,before_json,after_json,request_id) VALUES(?,?,?,?,?,?,?)",
        (context.task_id, str(context.actor_id), stage, key, json.dumps(before, ensure_ascii=False),
         json.dumps(after, ensure_ascii=False), context.request_id),
    )


def _exists(conn: sqlite3.Connection) -> bool:
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='workspace_undo'").fetchone())


def is_submission_evidence(before: dict, after: dict) -> bool:
    """Only server-marked evidence-only operations can use canonical coding access."""
    return (before.get('access_context') == after.get('access_context') == SUBMISSION_EVIDENCE
            and isinstance(before.get('evidence'), dict) and isinstance(after.get('evidence'), dict)
            and isinstance(before.get('values'), dict)
            and before['values'] == after.get('values')
            and set(before['evidence']) == set(after['evidence']) == set(before['values'])
            and before.get('definitions') == after.get('definitions')
            and before.get('stable_fields') == after.get('stable_fields'))


def snapshot_matches(conn: sqlite3.Connection, stage: str, key: str, before: dict, after: dict) -> bool:
    dims = [int(d) for d in after.get('values', {})]
    current = snapshot(conn, stage, key, dims, evidence='evidence' in after)
    if stage == 'coding' and is_submission_evidence(before, after):
        current['access_context'] = SUBMISSION_EVIDENCE
    # Historical entries only protect the state they actually captured.
    return {name: value for name, value in current.items() if name in after} == after


def _latest(conn: sqlite3.Connection, context: TraceContext, stage: str) -> dict | None:
    if not _exists(conn):
        return None
    row = conn.execute(
        "SELECT id,zotero_key,before_json,after_json FROM workspace_undo WHERE task_id=? AND actor_id=? AND stage=? "
        "AND undone=0 ORDER BY id DESC LIMIT 1", (context.task_id, str(context.actor_id), stage),
    ).fetchone()
    if not row:
        return None
    canonical = stage == 'coding' and is_submission_evidence(json.loads(row[2]), json.loads(row[3]))
    return {'entry_id': row[0], 'zotero_key': row[1],
            'access_context': SUBMISSION_EVIDENCE if canonical else 'personal'}


def latest(db: str | Path, context: TraceContext, stage: str) -> dict | None:
    from coscreen.db import _connect
    if stage not in STAGES:
        raise ValueError("Unknown Undo stage.")
    with closing(_connect(db)) as conn:
        return _latest(conn, context.normalized(), stage)


def undo(db: str | Path, context: TraceContext, stage: str, entry_id: int) -> dict:
    """Restore exactly one identified operation; retries never pop the next entry."""
    from coscreen.db import _connect
    from coscreen.research_trace import begin_capture_transaction, capture_event

    if stage not in STAGES:
        raise ValueError("Unknown Undo stage.")
    context = context.normalized()
    with closing(_connect(db)) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        if not _exists(conn):
            raise ValueError("There is no saved operation to undo.")
        row = conn.execute(
            "SELECT zotero_key,before_json,after_json,undone FROM workspace_undo "
            "WHERE id=? AND task_id=? AND actor_id=? AND stage=?",
            (entry_id, context.task_id, str(context.actor_id), stage),
        ).fetchone()
        if not row:
            raise ValueError("There is no saved operation to undo.")
        key, before_json, after_json, undone = row
        before, after = json.loads(before_json), json.loads(after_json)
        if stage == 'coding' and is_submission_evidence(before, after):
            context = replace(context, source_record_id=f'workspace_undo:{entry_id}')
        if not undone:
            last = _latest(conn, context, stage)
            if not last or last["entry_id"] != entry_id:
                raise ValueError("Undo history changed. Reload and try again.")
            if not conn.execute("SELECT 1 FROM articles WHERE zotero_key=?", (key,)).fetchone():
                raise ValueError("This article is no longer available for Undo.")
            if not snapshot_matches(conn, stage, key, before, after):
                raise ValueError("The saved result or downstream work has changed. Undo cannot overwrite newer changes.")
            begin_capture_transaction(conn, context, stage)
            if stage in _TABLES:
                table = _TABLES[stage]
                conn.execute(f"DELETE FROM {table} WHERE zotero_key=?", (key,))
                if before["record"] is not None:
                    conn.execute(f"INSERT INTO {table}(zotero_key,{_DECISION_COLUMNS}) VALUES(?,?,?,?,?,?,?)",
                                 (key, *before["record"]))
                names = _DECISION_COLUMNS.split(",")
                capture_event(conn, context, stage=stage, action="decision_undone", subject_id=key,
                              before=dict(zip(names, after["record"])) if after["record"] else None,
                              after=dict(zip(names, before["record"])) if before["record"] else None)
            else:
                from coscreen.coding import _require_dimension, _trace_dimension
                from coscreen.research_trace import is_recording
                recording = is_recording(conn, context, stage)
                for dim, value in before["values"].items():
                    conn.execute("DELETE FROM coding_values WHERE zotero_key=? AND dimension_id=?", (key, int(dim)))
                    if value is not None:
                        conn.execute("INSERT INTO coding_values(dimension_id,zotero_key,value,updated_at) VALUES(?,?,?,?)",
                                     (int(dim), key, *value))
                    if recording:
                        saved_value = after["values"][dim]
                        capture_event(conn, context, stage=stage, action="coding_values_undone", subject_id=key,
                                      before={"value": saved_value[0] if saved_value else ""},
                                      after={"value": value[0] if value else ""},
                                      field=_trace_dimension(conn, context, _require_dimension(conn, int(dim))))
                if 'evidence' in before:
                    from coscreen.coding import _NOTE_COLUMNS, _IMAGE_COLUMNS
                    for dim, state in before['evidence'].items():
                        note = conn.execute('SELECT id FROM coding_notes WHERE dimension_id=? AND zotero_key=?', (int(dim), key)).fetchone()
                        if note:
                            conn.execute('DELETE FROM coding_note_images WHERE note_id=?', (note[0],))
                            conn.execute('DELETE FROM coding_notes WHERE id=?', (note[0],))
                        if state['note']:
                            conn.execute(f"INSERT INTO coding_notes({','.join(_NOTE_COLUMNS)}) VALUES(?,?,?,?,?,?)", state['note'])
                            for image in state['images']:
                                conn.execute(f"INSERT INTO coding_note_images({','.join(_IMAGE_COLUMNS)}) VALUES(?,?,?,?,?,?,?)", image)
                        # Trace evidence has content hashes, never filesystem paths.
                        def public(value):
                            return {'text': value['note'][3] if value['note'] else '',
                                    'images': [{'id': r[0], 'sha256': r[3]} for r in value['images']]}
                        capture_event(conn, context, stage='coding', action='coding_evidence_undone', subject_id=key,
                                      before=public(after['evidence'][dim]), after=public(state),
                                      field=_trace_dimension(conn, context, _require_dimension(conn, int(dim))) if recording else None)
            conn.execute("UPDATE workspace_undo SET undone=1 WHERE id=?", (entry_id,))
        restored = (_row(conn, _TABLES[stage], key) if stage in _TABLES else None)
        return {"undone": True, "zotero_key": key, "latest": _latest(conn, context, stage),
                'access_context': SUBMISSION_EVIDENCE if stage == 'coding' and is_submission_evidence(before, after) else 'personal',
                "decision": (dict(zip(_DECISION_COLUMNS.split(","), restored)) if restored else None)}
