"""Transactional, personal Undo history for review decisions and coding saves."""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from coscreen.research_trace import TraceContext

STAGES = ("screen", "fulltext", "coding")
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


def _row(conn: sqlite3.Connection, table: str, key: str) -> list | None:
    row = conn.execute(f"SELECT {_DECISION_COLUMNS} FROM {table} WHERE zotero_key=?", (key,)).fetchone()
    return list(row) if row else None


def snapshot(conn: sqlite3.Connection, stage: str, key: str,
             dimensions: list[int] | None = None) -> dict[str, Any]:
    if stage not in STAGES:
        raise ValueError("Unknown Undo stage.")
    if stage in _TABLES:
        state: dict[str, Any] = {"record": _row(conn, _TABLES[stage], key)}
        # Do not undo past downstream edits or silently make newly completed work ineligible.
        state["coding"] = [list(r) for r in conn.execute(
            "SELECT dimension_id,value,updated_at FROM coding_values WHERE zotero_key=? ORDER BY dimension_id", (key,))]
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
    return {"values": values, "definitions": definitions}


def record_save(conn: sqlite3.Connection, context: TraceContext, stage: str, key: str,
                before: dict, after: dict) -> None:
    context = context.normalized()
    if before == after:
        return
    conn.execute(
        "INSERT INTO workspace_undo(task_id,actor_id,stage,zotero_key,before_json,after_json) VALUES(?,?,?,?,?,?)",
        (context.task_id, str(context.actor_id), stage, key, json.dumps(before, ensure_ascii=False),
         json.dumps(after, ensure_ascii=False)),
    )


def _exists(conn: sqlite3.Connection) -> bool:
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='workspace_undo'").fetchone())


def _latest(conn: sqlite3.Connection, context: TraceContext, stage: str) -> dict | None:
    if not _exists(conn):
        return None
    row = conn.execute(
        "SELECT id,zotero_key FROM workspace_undo WHERE task_id=? AND actor_id=? AND stage=? "
        "AND undone=0 ORDER BY id DESC LIMIT 1", (context.task_id, str(context.actor_id), stage),
    ).fetchone()
    return {"entry_id": row[0], "zotero_key": row[1]} if row else None


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
        if not undone:
            last = _latest(conn, context, stage)
            if not last or last["entry_id"] != entry_id:
                raise ValueError("Undo history changed. Reload and try again.")
            if not conn.execute("SELECT 1 FROM articles WHERE zotero_key=?", (key,)).fetchone():
                raise ValueError("This article is no longer available for Undo.")
            before, after = json.loads(before_json), json.loads(after_json)
            dims = [int(d) for d in after.get("values", {})]
            if snapshot(conn, stage, key, dims) != after:
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
            conn.execute("UPDATE workspace_undo SET undone=1 WHERE id=?", (entry_id,))
        restored = (_row(conn, _TABLES[stage], key) if stage in _TABLES else None)
        return {"undone": True, "zotero_key": key, "latest": _latest(conn, context, stage),
                "decision": (dict(zip(_DECISION_COLUMNS.split(","), restored)) if restored else None)}
