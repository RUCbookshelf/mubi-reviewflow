"""Optional, actor-scoped research trace storage for existing SQLite databases."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from coscreen import __version__

SCHEMA_VERSION = "reviewflow.research-trace/1"
STAGES = ("screen", "fulltext", "coding")
_MAX_JSON_BYTES = 1_000_000
_TRACE_TABLES = {
    "trace_store", "trace_settings", "trace_operations", "trace_clear_operations",
    "trace_write_operations", "trace_events", "trace_field_lifecycles",
}

_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS trace_store ("
    "singleton INTEGER PRIMARY KEY CHECK(singleton=1), store_id TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS trace_settings ("
    "task_id TEXT NOT NULL, actor_id TEXT NOT NULL, stages_json TEXT NOT NULL, "
    "status TEXT NOT NULL CHECK(status IN ('off','recording','paused')), "
    "operation_id TEXT NOT NULL, version INTEGER NOT NULL, updated_at TEXT NOT NULL, "
    "PRIMARY KEY(task_id,actor_id))",
    "CREATE TABLE IF NOT EXISTS trace_operations ("
    "task_id TEXT NOT NULL, actor_id TEXT NOT NULL, operation_id TEXT NOT NULL, "
    "payload_sha256 TEXT NOT NULL, result_json TEXT NOT NULL, "
    "PRIMARY KEY(task_id,actor_id,operation_id))",
    "CREATE TABLE IF NOT EXISTS trace_clear_operations ("
    "task_id TEXT NOT NULL, actor_id TEXT NOT NULL, operation_id TEXT NOT NULL, "
    "payload_sha256 TEXT NOT NULL, result_json TEXT NOT NULL, "
    "PRIMARY KEY(task_id,actor_id,operation_id))",
    "CREATE TABLE IF NOT EXISTS trace_write_operations ("
    "task_id TEXT NOT NULL, actor_id TEXT NOT NULL, request_id TEXT NOT NULL, "
    "stage TEXT NOT NULL, operation TEXT NOT NULL, subject_id TEXT NOT NULL, "
    "payload_sha256 TEXT NOT NULL, result_json TEXT NOT NULL, created_at TEXT NOT NULL, "
    "PRIMARY KEY(task_id,actor_id,request_id))",
    "CREATE TABLE IF NOT EXISTS trace_events ("
    "sequence INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL UNIQUE, "
    "store_id TEXT NOT NULL, task_id TEXT NOT NULL, actor_id TEXT NOT NULL, "
    "stage TEXT NOT NULL, action TEXT NOT NULL, subject_id TEXT NOT NULL, "
    "before_json TEXT, after_json TEXT, field_json TEXT, source TEXT NOT NULL, "
    "source_record_id TEXT, source_revision_id TEXT, batch_id TEXT, request_id TEXT, "
    "recorded_at TEXT NOT NULL, schema_version TEXT NOT NULL, app_version TEXT NOT NULL, "
    "origin_store_id TEXT, origin_event_id TEXT, origin_sequence INTEGER, "
    "origin_actor_id TEXT, imported_at TEXT, origin_alias TEXT, origin_json TEXT, "
    "origin_sha256 TEXT, UNIQUE(task_id,actor_id,origin_store_id,origin_event_id))",
    "CREATE INDEX IF NOT EXISTS idx_trace_events_actor_sequence "
    "ON trace_events(task_id,actor_id,sequence)",
    "CREATE INDEX IF NOT EXISTS idx_trace_events_actor_stage_subject "
    "ON trace_events(task_id,actor_id,stage,subject_id,sequence)",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_trace_events_revision "
    "ON trace_events(task_id,actor_id,source_revision_id) "
    "WHERE source_revision_id IS NOT NULL",
    "CREATE TABLE IF NOT EXISTS trace_field_lifecycles ("
    "task_id TEXT NOT NULL, actor_id TEXT NOT NULL, legacy_field_id TEXT NOT NULL, "
    "field_uuid TEXT NOT NULL, definition_json TEXT NOT NULL, created_at TEXT NOT NULL, "
    "deleted_at TEXT, PRIMARY KEY(task_id,actor_id,field_uuid))",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_trace_field_active "
    "ON trace_field_lifecycles(task_id,actor_id,legacy_field_id) WHERE deleted_at IS NULL",
)


@dataclass(frozen=True)
class TraceContext:
    """Trusted write context, supplied by the authenticated caller, never a request body."""

    task_id: str
    actor_id: str | int
    source: str = "manual"
    source_record_id: str | int | None = None
    source_revision_id: str | int | None = None
    batch_id: str | int | None = None
    request_id: str | None = None

    def normalized(self) -> "TraceContext":
        task = _text(self.task_id, "task_id", 200)
        if isinstance(self.actor_id, bool) or not isinstance(self.actor_id, (str, int)):
            raise ValueError("actor_id must be a trusted authenticated ID")
        actor = _text(str(self.actor_id), "actor_id", 200)
        source = _text(self.source, "source", 40)
        return TraceContext(
            task, actor, source,
            _optional_text(self.source_record_id, "source_record_id", 300),
            _optional_text(self.source_revision_id, "source_revision_id", 300),
            _optional_text(self.batch_id, "batch_id", 300),
            _optional_text(self.request_id, "request_id", 200),
        )


class TraceVersionConflict(ValueError):
    def __init__(self, current: dict[str, Any]):
        super().__init__("Trace settings changed; reload and retry.")
        self.current = current


class TraceConflict(ValueError):
    pass


def _text(value: Any, name: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > limit:
        raise ValueError(f"{name} must contain 1 to {limit} characters")
    return value.strip()


def _optional_text(value: Any, name: str, limit: int) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError(f"{name} must be text or an integer ID")
    result = str(value).strip()
    if not result or len(result) > limit:
        raise ValueError(f"{name} must contain 1 to {limit} characters")
    return result


def _json(value: Any) -> str:
    try:
        result = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("Trace values must be finite JSON data") from exc
    if len(result.encode("utf-8")) > _MAX_JSON_BYTES:
        raise ValueError("Trace value exceeds 1 MB")
    return result


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _schema_migration_id(conn: sqlite3.Connection) -> str | None:
    tables = {
        str(row[0]) for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'trace_%'"
        )
    }
    if "trace_store" not in tables:
        return "initial-v1"
    if "trace_events" not in tables:
        return "complete-v1"
    event_columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(trace_events)")}
    if "request_id" not in event_columns or "trace_write_operations" not in tables:
        return "request-id-v1"
    if not _TRACE_TABLES.issubset(tables):
        return "complete-v1"
    return None


def _backup_before_schema_migration(conn: sqlite3.Connection, migration_id: str) -> str | None:
    """Create/verify a separate online SQLite backup before file-backed DDL.

    Trace initialization is additive but can still fail mid-deployment. Create a
    fresh backup for each detected schema step before applying DDL; a retry after
    failed migration therefore captures the latest committed database state.
    In-memory databases have no durable source to back up. The copy uses a separate
    read-only source connection and the stdlib
    SQLite backup API, so an active caller transaction is never backed up into
    itself and WAL content is included consistently.
    """
    main = next((row for row in conn.execute("PRAGMA database_list") if row[1] == "main"), None)
    filename = str(main[2]) if main else ""
    if not filename or filename == ":memory:":
        return None
    source_path = Path(filename).resolve()
    prefix = f".{source_path.name}.research-trace-{migration_id}-pre-migration-"

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup_path = source_path.parent / f"{prefix}{stamp}-{uuid.uuid4().hex[:8]}.bak"
    fd, temporary_name = tempfile.mkstemp(
        prefix=backup_path.name + ".", suffix=".tmp", dir=source_path.parent,
    )
    os.close(fd)
    temporary_path = Path(temporary_name)
    os.chmod(temporary_path, 0o600)
    source_conn = None
    destination_conn = None
    try:
        source_conn = sqlite3.connect(source_path.as_uri() + "?mode=ro", uri=True, timeout=5)
        destination_conn = sqlite3.connect(temporary_path, timeout=30)
        source_conn.execute("PRAGMA busy_timeout=5000")
        backup_deadline = time.monotonic() + 60

        def check_backup_deadline(status: int, remaining: int, total: int) -> None:
            if time.monotonic() >= backup_deadline:
                raise TimeoutError("pre-migration SQLite backup exceeded 60 seconds")

        source_conn.backup(
            destination_conn, pages=256, progress=check_backup_deadline, sleep=0.05,
        )
        check_deadline = time.monotonic() + 60

        def check_integrity_deadline() -> int:
            return 1 if time.monotonic() >= check_deadline else 0

        destination_conn.set_progress_handler(check_integrity_deadline, 10000)
        integrity = destination_conn.execute("PRAGMA integrity_check").fetchone()[0]
        destination_conn.set_progress_handler(None, 0)
        if integrity != "ok":
            raise sqlite3.DatabaseError(f"pre-migration backup integrity check failed: {integrity}")
        destination_conn.close()
        destination_conn = None
        source_conn.close()
        source_conn = None
        os.replace(temporary_path, backup_path)
        return str(backup_path)
    except Exception:
        if destination_conn is not None:
            destination_conn.close()
        if source_conn is not None:
            source_conn.close()
        temporary_path.unlink(missing_ok=True)
        raise


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Create additive trace tables on the research database connection."""
    migration_id = _schema_migration_id(conn)
    if migration_id is not None:
        _backup_before_schema_migration(conn, migration_id)
    own_transaction = not conn.in_transaction
    if own_transaction:
        conn.execute("SAVEPOINT trace_schema_init")
    try:
        for statement in _SCHEMA:
            conn.execute(statement)
        event_columns = {row[1] for row in conn.execute("PRAGMA table_info(trace_events)")}
        if "request_id" not in event_columns:
            conn.execute("ALTER TABLE trace_events ADD COLUMN request_id TEXT")
        row = conn.execute("SELECT store_id FROM trace_store WHERE singleton=1").fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO trace_store(singleton,store_id) VALUES(1,?)",
                (str(uuid.uuid4()),),
            )
        if own_transaction:
            conn.execute("RELEASE SAVEPOINT trace_schema_init")
    except Exception:
        if own_transaction:
            conn.execute("ROLLBACK TO SAVEPOINT trace_schema_init")
            conn.execute("RELEASE SAVEPOINT trace_schema_init")
        raise


def _store_id(conn: sqlite3.Connection) -> str:
    return str(conn.execute("SELECT store_id FROM trace_store WHERE singleton=1").fetchone()[0])


def _row_dict(cursor: sqlite3.Cursor, row: Any) -> dict[str, Any]:
    return dict(row) if isinstance(row, sqlite3.Row) else dict(zip((c[0] for c in cursor.description), row))


def _settings_row(conn: sqlite3.Connection, task_id: str, actor_id: str) -> dict[str, Any] | None:
    cursor = conn.execute(
        "SELECT task_id,actor_id,stages_json,status,operation_id,version,updated_at "
        "FROM trace_settings WHERE task_id=? AND actor_id=?", (task_id, actor_id),
    )
    row = cursor.fetchone()
    return _row_dict(cursor, row) if row else None


def get_settings(conn: sqlite3.Connection, task_id: str, actor_id: str | int) -> dict[str, Any]:
    ensure_schema(conn)
    task = _text(task_id, "task_id", 200)
    actor = _text(str(actor_id), "actor_id", 200)
    row = _settings_row(conn, task, actor)
    if row is None:
        return {
            "task_id": task, "actor_id": actor, "status": "off", "stages": [],
            "operation_id": None, "version": 0, "updated_at": None, "store_id": _store_id(conn),
        }
    return {
        "task_id": task, "actor_id": actor, "status": row["status"],
        "stages": json.loads(row["stages_json"]), "operation_id": row["operation_id"],
        "version": int(row["version"]), "updated_at": row["updated_at"],
        "store_id": _store_id(conn),
    }


def _insert_event(
    conn: sqlite3.Connection,
    *,
    task_id: str,
    actor_id: str,
    stage: str,
    action: str,
    subject_id: str,
    before: Any,
    after: Any,
    field: Any = None,
    source: str,
    source_record_id: str | None = None,
    source_revision_id: str | None = None,
    batch_id: str | None = None,
    request_id: str | None = None,
    recorded_at: str | None = None,
    origin: dict[str, Any] | None = None,
) -> int:
    before_json = None if before is None else _json(before)
    after_json = None if after is None else _json(after)
    field_json = None if field is None else _json(field)
    origin_json = None
    origin_vals: tuple[Any, ...] = (None,) * 8
    if origin is not None:
        origin_json = _json(origin["event"])
        origin_vals = (
            origin["store_id"], origin["event_id"], origin["sequence"],
            origin["actor_id"], origin["imported_at"], origin.get("alias"),
            origin_json, origin["sha256"],
        )
    cur = conn.execute(
        "INSERT INTO trace_events (event_id,store_id,task_id,actor_id,stage,action,subject_id,"
        "before_json,after_json,field_json,source,source_record_id,source_revision_id,batch_id,"
        "request_id,recorded_at,schema_version,app_version,origin_store_id,origin_event_id,origin_sequence,"
        "origin_actor_id,imported_at,origin_alias,origin_json,origin_sha256) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (str(uuid.uuid4()), _store_id(conn), task_id, actor_id, stage, action, subject_id,
         before_json, after_json, field_json, source, source_record_id, source_revision_id,
         batch_id, request_id, recorded_at or _now(), SCHEMA_VERSION, __version__, *origin_vals),
    )
    return int(cur.lastrowid)


def capture_event(
    conn: sqlite3.Connection,
    context: TraceContext,
    *,
    stage: str,
    action: str,
    subject_id: str | int,
    before: Any,
    after: Any,
    field: Any = None,
) -> int | None:
    """Append a change only when this actor enabled its stage in this same database."""
    context = context.normalized()
    if stage not in STAGES:
        raise ValueError(f"unknown trace stage: {stage!r}")
    if not _table_exists(conn, "trace_settings"):
        return None
    setting = _settings_row(conn, context.task_id, str(context.actor_id))
    if not setting or setting["status"] != "recording" or stage not in json.loads(setting["stages_json"]):
        return None
    before_json = None if before is None else _json(before)
    after_json = None if after is None else _json(after)
    field_json = None if field is None else _json(field)
    if before_json == after_json:
        return None
    revision_id = context.source_revision_id
    if revision_id:
        old = conn.execute(
            "SELECT sequence,before_json,after_json,field_json,action,subject_id FROM trace_events "
            "WHERE task_id=? AND actor_id=? AND source_revision_id=?",
            (context.task_id, str(context.actor_id), revision_id),
        ).fetchone()
        if old:
            if tuple(old[1:]) != (before_json, after_json, field_json, action, str(subject_id)):
                raise TraceConflict("source revision ID was already captured with different content")
            return int(old[0])
    return _insert_event(
        conn, task_id=context.task_id, actor_id=str(context.actor_id), stage=stage,
        action=_text(action, "action", 80), subject_id=_text(str(subject_id), "subject_id", 500),
        before=before, after=after, field=field, source=context.source,
        source_record_id=context.source_record_id, source_revision_id=revision_id,
        batch_id=context.batch_id, request_id=context.request_id,
    )


def is_recording(conn: sqlite3.Connection, context: TraceContext, stage: str) -> bool:
    context = context.normalized()
    if not _table_exists(conn, "trace_settings"):
        return False
    row = _settings_row(conn, context.task_id, str(context.actor_id))
    return bool(
        row and row["status"] == "recording" and stage in json.loads(row["stages_json"])
    )


def ensure_default_recording(conn: sqlite3.Connection, context: TraceContext) -> None:
    """Enable all stages once; saved off/paused preferences remain authoritative."""
    context = context.normalized()
    ensure_schema(conn)
    if _settings_row(conn, context.task_id, str(context.actor_id)) is not None:
        return
    own_transaction = not conn.in_transaction
    if own_transaction:
        conn.execute("BEGIN IMMEDIATE")
    try:
        if _settings_row(conn, context.task_id, str(context.actor_id)) is None:
            update_settings(conn, context, stages=list(STAGES), status="recording",
                            operation_id="default-recording", expected_version=0)
        if own_transaction:
            conn.commit()
    except Exception:
        if own_transaction:
            conn.rollback()
        raise


def begin_capture_transaction(
    conn: sqlite3.Connection, context: TraceContext, stage: str
) -> bool:
    """Read this actor's setting first, then start an appropriately locked transaction.

    Call before reading the old business value. In WAL mode, a concurrent settings
    commit after an off snapshot makes promotion to a writer fail with
    ``SQLITE_BUSY_SNAPSHOT`` and the research write rolls back. In rollback-journal
    mode the settings writer waits for this read transaction. Active recording uses
    ``BEGIN IMMEDIATE`` after a lightweight preflight, then rechecks the setting
    before any business-state read; this serializes concurrent recorded writes.

    Off/paused contexts use a deferred transaction, avoiding an eager write lock.
    """
    context = context.normalized()
    if stage not in STAGES:
        raise ValueError(f"unknown trace stage: {stage!r}")
    ensure_default_recording(conn, context)
    if conn.in_transaction:
        return is_recording(conn, context, stage)

    # The preflight is only a lock-mode hint. The authoritative setting read is
    # repeated in the transaction before caller code may inspect old business state.
    was_recording = is_recording(conn, context, stage)
    conn.execute("BEGIN IMMEDIATE" if was_recording else "BEGIN")
    recording = is_recording(conn, context, stage)
    if recording and not was_recording:
        # Recording was enabled between preflight and the deferred snapshot. Restart
        # with a write lock so concurrent events have a single before/after order.
        conn.rollback()
        conn.execute("BEGIN IMMEDIATE")
        recording = is_recording(conn, context, stage)
    return recording


def begin_write_request(
    conn: sqlite3.Connection,
    context: TraceContext,
    *,
    stage: str,
    operation: str,
    subject_id: str | int,
    payload: Any,
) -> tuple[bool, Any]:
    """Reserve a validated request ID inside the current write transaction.

    Returns ``(is_replay, original_result)``. Recording-off writes do not create
    request rows, preserving the opt-in boundary. The request ID is scoped to this
    authenticated task/actor and binds the stage, operation, subject and normalized
    intent. A retry skips the mutation; reuse with different intent raises
    ``TraceConflict``. Caller must call :func:`finish_write_request` before commit.
    """
    context = context.normalized()
    if context.request_id is None or not _table_exists(conn, "trace_write_operations"):
        return False, None
    operation = _text(operation, "operation", 80)
    subject = _text(str(subject_id), "subject_id", 500)
    digest = hashlib.sha256(_json({
        "stage": stage, "operation": operation, "subject_id": subject, "payload": payload,
    }).encode("utf-8")).hexdigest()
    row = conn.execute(
        "SELECT stage,operation,subject_id,payload_sha256,result_json FROM trace_write_operations "
        "WHERE task_id=? AND actor_id=? AND request_id=?",
        (context.task_id, str(context.actor_id), context.request_id),
    ).fetchone()
    if row is not None:
        if tuple(row[:4]) != (stage, operation, subject, digest):
            raise TraceConflict("request ID was already used with different content")
        return True, json.loads(row[4])
    if not is_recording(conn, context, stage):
        return False, None
    conn.execute(
        "INSERT INTO trace_write_operations(task_id,actor_id,request_id,stage,operation,subject_id,"
        "payload_sha256,result_json,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
        (context.task_id, str(context.actor_id), context.request_id, stage, operation, subject,
         digest, "null", _now()),
    )
    return False, None


def finish_write_request(conn: sqlite3.Connection, context: TraceContext, result: Any = None) -> None:
    """Save the first successful response for an operation in the same transaction."""
    context = context.normalized()
    if context.request_id is None or not _table_exists(conn, "trace_write_operations"):
        return
    conn.execute(
        "UPDATE trace_write_operations SET result_json=? WHERE task_id=? AND actor_id=? AND request_id=?",
        (_json(result), context.task_id, str(context.actor_id), context.request_id),
    )


def field_lifecycle(
    conn: sqlite3.Connection,
    context: TraceContext,
    legacy_field_id: str | int,
    definition: dict[str, Any],
) -> str:
    """Get or create a stable UUID for one legacy field lifecycle."""
    context = context.normalized()
    legacy = _text(str(legacy_field_id), "legacy_field_id", 300)
    definition_json = _json(definition)
    row = conn.execute(
        "SELECT field_uuid FROM trace_field_lifecycles WHERE task_id=? AND actor_id=? "
        "AND legacy_field_id=? AND deleted_at IS NULL",
        (context.task_id, str(context.actor_id), legacy),
    ).fetchone()
    if row:
        conn.execute(
            "UPDATE trace_field_lifecycles SET definition_json=? WHERE task_id=? AND actor_id=? "
            "AND legacy_field_id=? AND field_uuid=?",
            (definition_json, context.task_id, str(context.actor_id), legacy, row[0]),
        )
        return str(row[0])
    field_uuid = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO trace_field_lifecycles(task_id,actor_id,legacy_field_id,field_uuid,definition_json,created_at) "
        "VALUES(?,?,?,?,?,?)",
        (context.task_id, str(context.actor_id), legacy, field_uuid, definition_json, _now()),
    )
    return field_uuid


def retire_field_lifecycle(
    conn: sqlite3.Connection, context: TraceContext, legacy_field_id: str | int,
) -> None:
    context = context.normalized()
    conn.execute(
        "UPDATE trace_field_lifecycles SET deleted_at=? WHERE task_id=? AND actor_id=? "
        "AND legacy_field_id=? AND deleted_at IS NULL",
        (_now(), context.task_id, str(context.actor_id), str(legacy_field_id)),
    )


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,),
    ).fetchone() is not None


def _field_snapshot(conn: sqlite3.Connection, context: TraceContext, row: Any) -> dict[str, Any]:
    field = {
        "legacy_id": int(row["id"] if isinstance(row, sqlite3.Row) else row[0]),
        "name": row["name"] if isinstance(row, sqlite3.Row) else row[1],
        "type": row["dtype"] if isinstance(row, sqlite3.Row) else row[2],
        "options": json.loads(row["options_json"] if isinstance(row, sqlite3.Row) else row[3]),
        "multi_select": bool(row["multi_select"] if isinstance(row, sqlite3.Row) else row[4]),
        "unit": row["unit"] if isinstance(row, sqlite3.Row) else row[5],
        "description": row["description"] if isinstance(row, sqlite3.Row) else row[6],
        "section": row["section"] if isinstance(row, sqlite3.Row) else row[7],
        "position": int(row["position"] if isinstance(row, sqlite3.Row) else row[8]),
    }
    field["field_id"] = field_lifecycle(conn, context, field["legacy_id"], field)
    return field


def _snapshot_stage(conn: sqlite3.Connection, context: TraceContext, stage: str) -> int:
    count = 0
    data_tables = {"screen": "decisions", "fulltext": "stage2_decisions"}
    table = data_tables.get(stage)
    if table and _table_exists(conn, table):
        for row in conn.execute(
            f"SELECT zotero_key,decision,exclusion_reason,notes,tags,screened_at FROM {table} ORDER BY zotero_key"
        ):
            _insert_event(
                conn, task_id=context.task_id, actor_id=str(context.actor_id), stage=stage,
                action="baseline_decision", subject_id=str(row[0]), before=None,
                after={"decision": row[1], "exclusion_reason": row[2] or "", "notes": row[3] or "",
                       "tags": row[4] or "", "screened_at": row[5]}, source="baseline",
            )
            count += 1
    if stage == "coding" and _table_exists(conn, "coding_dimensions"):
        dims = conn.execute(
            "SELECT id,name,dtype,options_json,multi_select,unit,description,section,position "
            "FROM coding_dimensions ORDER BY id"
        ).fetchall()
        fields: dict[int, dict[str, Any]] = {}
        for dim in dims:
            field = _field_snapshot(conn, context, dim)
            fields[int(field["legacy_id"])] = field
            _insert_event(
                conn, task_id=context.task_id, actor_id=str(context.actor_id), stage=stage,
                action="baseline_field", subject_id=field["field_id"], before=None,
                after=field, field=field, source="baseline",
            )
            count += 1
        if _table_exists(conn, "coding_values"):
            for dim_id, key, value in conn.execute(
                "SELECT dimension_id,zotero_key,value FROM coding_values ORDER BY dimension_id,zotero_key"
            ):
                field = fields.get(int(dim_id))
                if field:
                    _insert_event(
                        conn, task_id=context.task_id, actor_id=str(context.actor_id), stage=stage,
                        action="baseline_value", subject_id=str(key), before=None,
                        after={"value": value or ""}, field=field, source="baseline",
                    )
                    count += 1
        if _table_exists(conn, "coding_notes"):
            for note_id, dim_id, key, text, created_at, updated_at in conn.execute(
                "SELECT id,dimension_id,zotero_key,text,created_at,updated_at "
                "FROM coding_notes ORDER BY dimension_id,zotero_key"
            ):
                field = fields.get(int(dim_id))
                if field:
                    _insert_event(
                        conn, task_id=context.task_id, actor_id=str(context.actor_id), stage=stage,
                        action="baseline_coding_note", subject_id=str(key), before=None,
                        after={"note_id": int(note_id), "text": text or "",
                               "created_at": created_at, "updated_at": updated_at},
                        field=field, source="baseline",
                    )
                    count += 1
        if _table_exists(conn, "coding_note_images"):
            for image_id, note_id, digest, ocr_text, ocr_status, created_at, dim_id, key in conn.execute(
                "SELECT i.id,i.note_id,i.sha256,i.ocr_text,i.ocr_status,i.created_at,"
                "n.dimension_id,n.zotero_key FROM coding_note_images i "
                "JOIN coding_notes n ON n.id=i.note_id ORDER BY n.dimension_id,n.zotero_key,i.id"
            ):
                field = fields.get(int(dim_id))
                if field:
                    _insert_event(
                        conn, task_id=context.task_id, actor_id=str(context.actor_id), stage=stage,
                        action="baseline_coding_image", subject_id=str(key), before=None,
                        after={"image_id": int(image_id), "note_id": int(note_id),
                               "sha256": digest, "ocr_text": ocr_text or "",
                               "ocr_status": ocr_status, "created_at": created_at},
                        field=field, source="baseline",
                    )
                    count += 1
    if stage == "coding" and _table_exists(conn, "coding_rater_records"):
        rows = conn.execute(
            "SELECT item_key,field_key,version,supersedes_id,value_json,source_json,rationale,created_at "
            "FROM coding_rater_records WHERE rater_id=? ORDER BY item_key,field_key,version",
            (str(context.actor_id),),
        )
        for row in rows:
            source = json.loads(row[5])
            source_field = source.get("_reviewflow_field")
            field = dict(source_field) if isinstance(source_field, dict) else {}
            field["field_id"] = str(row[1])
            _insert_event(
                conn, task_id=context.task_id, actor_id=str(context.actor_id), stage=stage,
                action="baseline_rater_revision", subject_id=str(row[0]), before=None,
                after={"field_key": row[1], "version": int(row[2]), "supersedes_id": row[3],
                       "value": json.loads(row[4]), "source": source,
                       "rationale": row[6], "created_at": row[7]},
                field=field, source="baseline",
            )
            count += 1
    return count


def update_settings(
    conn: sqlite3.Connection,
    context: TraceContext,
    *,
    stages: list[str] | tuple[str, ...],
    status: str,
    operation_id: str,
    expected_version: int,
) -> dict[str, Any]:
    """Set this actor's local recording scope, add its start snapshot and boundary."""
    context = context.normalized()
    if status not in {"off", "recording", "paused"}:
        raise ValueError("status must be off, recording or paused")
    if not isinstance(stages, (list, tuple)) or any(stage not in STAGES for stage in stages):
        raise ValueError(f"stages must use {STAGES}")
    stage_list = sorted(set(stages))
    operation_id = _text(operation_id, "operation_id", 200)
    if type(expected_version) is not int or expected_version < 0:
        raise ValueError("expected_version must be a non-negative integer")
    payload = {"stages": stage_list, "status": status, "expected_version": expected_version}
    payload_sha = hashlib.sha256(_json(payload).encode()).hexdigest()
    ensure_schema(conn)
    own_transaction = not conn.in_transaction
    if own_transaction:
        conn.execute("BEGIN IMMEDIATE")
    savepoint = "trace_settings_change"
    conn.execute(f"SAVEPOINT {savepoint}")
    try:
        prior_op = conn.execute(
            "SELECT payload_sha256,result_json FROM trace_operations WHERE task_id=? AND actor_id=? AND operation_id=?",
            (context.task_id, str(context.actor_id), operation_id),
        ).fetchone()
        if prior_op:
            if prior_op[0] != payload_sha:
                raise TraceConflict("operation ID was already used with different settings")
            result = json.loads(prior_op[1])
            conn.execute(f"RELEASE SAVEPOINT {savepoint}")
            if own_transaction:
                conn.commit()
            return result
        previous = get_settings(conn, context.task_id, context.actor_id)
        if previous["version"] != expected_version:
            raise TraceVersionConflict(previous)
        now = _now()
        version = previous["version"] + 1
        conn.execute(
            "INSERT INTO trace_settings(task_id,actor_id,stages_json,status,operation_id,version,updated_at) "
            "VALUES(?,?,?,?,?,?,?) ON CONFLICT(task_id,actor_id) DO UPDATE SET "
            "stages_json=excluded.stages_json,status=excluded.status,operation_id=excluded.operation_id, "
            "version=excluded.version,updated_at=excluded.updated_at",
            (context.task_id, str(context.actor_id), _json(stage_list), status, operation_id, version, now),
        )
        next_settings = {
            "task_id": context.task_id, "actor_id": str(context.actor_id), "status": status,
            "stages": stage_list, "operation_id": operation_id, "version": version,
            "updated_at": now, "store_id": _store_id(conn),
        }
        changed = previous["status"] != status or previous["stages"] != stage_list
        if changed:
            if status == "recording" and previous["status"] != "recording":
                action = "recording_started" if previous["status"] == "off" else "recording_resumed"
            elif status == "paused":
                action = "recording_paused"
            elif status == "off":
                action = "recording_stopped"
            else:
                action = "stages_changed"
            _insert_event(
                conn, task_id=context.task_id, actor_id=str(context.actor_id), stage="control",
                action=action, subject_id="settings", before=previous, after=next_settings,
                source="control",
            )
        newly_selected = set(stage_list) - set(previous["stages"])
        if status == "recording" and previous["status"] != "recording":
            newly_selected = set(stage_list)
            if _table_exists(conn, "articles"):
                for row in conn.execute(
                    "SELECT zotero_key,doi,item_type FROM articles ORDER BY import_order,zotero_key"
                ):
                    _insert_event(
                        conn, task_id=context.task_id, actor_id=str(context.actor_id), stage="screen",
                        action="baseline_item", subject_id=str(row[0]), before=None,
                        after={"zotero_key": row[0], "doi": row[1] or "", "item_type": row[2] or ""},
                        source="baseline",
                    )
                if _table_exists(conn, "meta"):
                    version_row = conn.execute(
                        "SELECT value FROM meta WHERE key='protocol_version'"
                    ).fetchone()
                    _insert_event(
                        conn, task_id=context.task_id, actor_id=str(context.actor_id), stage="screen",
                        action="baseline_protocol", subject_id="protocol", before=None,
                        after={"protocol_version": version_row[0] if version_row else None},
                        source="baseline",
                    )
        if status == "recording":
            for stage in STAGES:
                if stage in newly_selected:
                    _snapshot_stage(conn, context, stage)
        conn.execute(
            "INSERT INTO trace_operations(task_id,actor_id,operation_id,payload_sha256,result_json) "
            "VALUES(?,?,?,?,?)",
            (context.task_id, str(context.actor_id), operation_id, payload_sha, _json(next_settings)),
        )
        conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        if own_transaction:
            conn.commit()
        return next_settings
    except Exception:
        conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
        conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        if own_transaction:
            conn.rollback()
        raise


def clear_history(
    conn: sqlite3.Connection,
    context: TraceContext,
    *,
    operation_id: str,
    expected_version: int,
) -> dict[str, Any]:
    """Clear only one actor/task trace and start a fresh, explicitly bounded history."""
    context = context.normalized()
    operation_id = _text(operation_id, "operation_id", 200)
    if type(expected_version) is not int or expected_version < 0:
        raise ValueError("expected_version must be a non-negative integer")
    ensure_schema(conn)
    own_transaction = not conn.in_transaction
    if own_transaction:
        conn.execute("BEGIN IMMEDIATE")
    payload_sha = hashlib.sha256(_json({"expected_version": expected_version}).encode()).hexdigest()
    savepoint = "trace_history_clear"
    conn.execute(f"SAVEPOINT {savepoint}")
    try:
        old_op = conn.execute(
            "SELECT payload_sha256,result_json FROM trace_clear_operations "
            "WHERE task_id=? AND actor_id=? AND operation_id=?",
            (context.task_id, str(context.actor_id), operation_id),
        ).fetchone()
        if old_op:
            if old_op[0] != payload_sha:
                raise TraceConflict("clear operation ID was already used with different settings")
            result = json.loads(old_op[1])
            conn.execute(f"RELEASE SAVEPOINT {savepoint}")
            if own_transaction:
                conn.commit()
            return result
        previous = get_settings(conn, context.task_id, context.actor_id)
        if previous["version"] != expected_version:
            raise TraceVersionConflict(previous)
        deleted_count = int(conn.execute(
            "SELECT COUNT(*) FROM trace_events WHERE task_id=? AND actor_id=?",
            (context.task_id, str(context.actor_id)),
        ).fetchone()[0])
        conn.execute(
            "DELETE FROM trace_events WHERE task_id=? AND actor_id=?",
            (context.task_id, str(context.actor_id)),
        )
        # Request receipts may contain the first successful response and a body
        # digest. They are trace data too, so a history clear removes them along
        # with field snapshots and prior settings/clear operation receipts.
        for table in ("trace_operations", "trace_write_operations", "trace_field_lifecycles", "trace_clear_operations"):
            conn.execute(
                f"DELETE FROM {table} WHERE task_id=? AND actor_id=?",
                (context.task_id, str(context.actor_id)),
            )
        now = _now()
        version = previous["version"] + 1
        conn.execute(
            "INSERT INTO trace_settings(task_id,actor_id,stages_json,status,operation_id,version,updated_at) "
            "VALUES(?,?,?,?,?,?,?) ON CONFLICT(task_id,actor_id) DO UPDATE SET "
            "operation_id=excluded.operation_id,version=excluded.version,updated_at=excluded.updated_at",
            (context.task_id, str(context.actor_id), _json(previous["stages"]), previous["status"],
             operation_id, version, now),
        )
        next_settings = {
            "task_id": context.task_id, "actor_id": str(context.actor_id), "status": previous["status"],
            "stages": previous["stages"], "operation_id": operation_id, "version": version,
            "updated_at": now, "store_id": _store_id(conn),
        }
        sequence = _insert_event(
            conn, task_id=context.task_id, actor_id=str(context.actor_id), stage="control",
            action="history_cleared", subject_id="history", before={"event_count": deleted_count},
            after={"coverage_starts_after_clear": True, "status": previous["status"],
                   "stages": previous["stages"]}, source="control",
        )
        if previous["status"] != "off":
            for stage in STAGES:
                if stage in previous["stages"]:
                    _snapshot_stage(conn, context, stage)
        result = {
            "deleted_event_count": deleted_count, "settings": next_settings,
            "boundary_sequence": sequence,
        }
        conn.execute(
            "INSERT INTO trace_clear_operations(task_id,actor_id,operation_id,payload_sha256,result_json) "
            "VALUES(?,?,?,?,?)",
            (context.task_id, str(context.actor_id), operation_id, payload_sha, _json(result)),
        )
        conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        if own_transaction:
            conn.commit()
        return result
    except Exception:
        conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
        conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        if own_transaction:
            conn.rollback()
        raise


def _event_from_row(cursor: sqlite3.Cursor, row: Any) -> dict[str, Any]:
    item = _row_dict(cursor, row)
    for key, output in (("before_json", "before"), ("after_json", "after"), ("field_json", "field"), ("origin_json", "origin")):
        raw = item.pop(key, None)
        item[output] = json.loads(raw) if raw is not None else None
    return item


def get_event(
    conn: sqlite3.Connection,
    task_id: str,
    actor_id: str | int,
    event_id: str,
) -> dict[str, Any] | None:
    """Fetch one detail row without allowing caller-supplied actor scope to broaden."""
    ensure_schema(conn)
    task, actor = _text(task_id, "task_id", 200), _text(str(actor_id), "actor_id", 200)
    cur = conn.execute(
        "SELECT sequence,event_id,store_id,task_id,actor_id,stage,action,subject_id,before_json,after_json,field_json,"
        "source,source_record_id,source_revision_id,batch_id,request_id,recorded_at,schema_version,app_version,origin_store_id,"
        "origin_event_id,origin_sequence,origin_actor_id,imported_at,origin_alias,origin_json,origin_sha256 "
        "FROM trace_events WHERE task_id=? AND actor_id=? AND event_id=?",
        (task, actor, _text(event_id, "event_id", 80)),
    )
    row = cur.fetchone()
    return _event_from_row(cur, row) if row else None


def _origin_record(event: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(event, dict):
        raise ValueError("event must be an object")
    return {key: event.get(key) for key in (
        "event_id", "store_id", "sequence", "task_id", "actor_id", "stage", "action", "subject_id",
        "before", "after", "field", "source", "source_record_id", "source_revision_id", "batch_id", "request_id",
        "recorded_at", "schema_version", "app_version", "origin", "actor_alias",
    )}


def origin_event_sha256(event: dict[str, Any]) -> str:
    """Canonical digest used for import-preview duplicate/conflict checks."""
    return hashlib.sha256(_json(_origin_record(event)).encode()).hexdigest()


def lookup_import(
    conn: sqlite3.Connection,
    *,
    task_id: str,
    actor_id: str | int,
    origin_store_id: str,
    origin_event_id: str,
    origin_sha256: str,
) -> dict[str, Any] | None:
    """Return an existing same-origin row as duplicate/conflict for import previews."""
    ensure_schema(conn)
    row = conn.execute(
        "SELECT event_id,sequence,origin_sha256 FROM trace_events "
        "WHERE task_id=? AND actor_id=? AND origin_store_id=? AND origin_event_id=?",
        (_text(task_id, "task_id", 200), _text(str(actor_id), "actor_id", 200),
         _text(origin_store_id, "origin_store_id", 200), _text(origin_event_id, "origin_event_id", 80)),
    ).fetchone()
    if row is None:
        return None
    return {
        "status": "duplicate" if row[2] == origin_sha256 else "conflict",
        "event_id": row[0], "sequence": int(row[1]), "origin_sha256": row[2],
    }


def list_events(
    conn: sqlite3.Connection,
    task_id: str,
    actor_id: str | int,
    *,
    limit: int = 50,
    cursor: int | None = None,
    stage: str | None = None,
    action: str | None = None,
    subject_id: str | None = None,
    since: str | None = None,
    until: str | None = None,
    snapshot_sequence: int | None = None,
) -> dict[str, Any]:
    ensure_schema(conn)
    if type(limit) is not int or not 1 <= limit <= 200:
        raise ValueError("limit must be between 1 and 200")
    task, actor = _text(task_id, "task_id", 200), _text(str(actor_id), "actor_id", 200)
    max_row = conn.execute(
        "SELECT COALESCE(MAX(sequence),0) FROM trace_events WHERE task_id=? AND actor_id=?",
        (task, actor),
    ).fetchone()
    cutoff = int(max_row[0]) if snapshot_sequence is None else snapshot_sequence
    if type(cutoff) is not int or cutoff < 0:
        raise ValueError("snapshot_sequence must be a non-negative integer")
    clauses = ["task_id=?", "actor_id=?", "sequence<=?"]
    args: list[Any] = [task, actor, cutoff]
    if cursor is not None:
        clauses.append("sequence>?")
        args.append(int(cursor))
    for column, value in (("stage", stage), ("action", action), ("subject_id", subject_id)):
        if value is not None:
            clauses.append(f"{column}=?")
            args.append(value)
    if since is not None:
        clauses.append("recorded_at>=?")
        args.append(since)
    if until is not None:
        clauses.append("recorded_at<=?")
        args.append(until)
    query = (
        "SELECT sequence,event_id,store_id,task_id,actor_id,stage,action,subject_id,before_json,after_json,field_json,"
        "source,source_record_id,source_revision_id,batch_id,request_id,recorded_at,schema_version,app_version,origin_store_id,"
        "origin_event_id,origin_sequence,origin_actor_id,imported_at,origin_alias,origin_json,origin_sha256 "
        "FROM trace_events WHERE " + " AND ".join(clauses) + " ORDER BY sequence LIMIT ?"
    )
    cur = conn.execute(query, (*args, limit + 1))
    rows = cur.fetchall()
    more = len(rows) > limit
    rows = rows[:limit]
    events = [_event_from_row(cur, row) for row in rows]
    return {
        "events": events,
        "next_cursor": int(rows[-1][0]) if more and rows else None,
        "has_more": more,
        "snapshot_sequence": cutoff,
    }


def recent_events(
    conn: sqlite3.Connection,
    task_id: str,
    actor_id: str | int,
    *,
    limit: int = 10,
    stage: str | None = None,
    snapshot_sequence: int | None = None,
) -> list[dict[str, Any]]:
    """Fetch a bounded newest-first event window for one actor/task."""
    ensure_schema(conn)
    if type(limit) is not int or not 1 <= limit <= 200:
        raise ValueError("limit must be between 1 and 200")
    task, actor = _text(task_id, "task_id", 200), _text(str(actor_id), "actor_id", 200)
    cutoff = snapshot_cutoff(conn, task, actor) if snapshot_sequence is None else snapshot_sequence
    if type(cutoff) is not int or cutoff < 0:
        raise ValueError("snapshot_sequence must be a non-negative integer")
    clauses = ["task_id=?", "actor_id=?", "sequence<=?"]
    args: list[Any] = [task, actor, cutoff]
    if stage is not None:
        if stage not in (*STAGES, "control"):
            raise ValueError("stage is invalid")
        clauses.append("stage=?")
        args.append(stage)
    cur = conn.execute(
        "SELECT sequence,event_id,store_id,task_id,actor_id,stage,action,subject_id,before_json,after_json,field_json,"
        "source,source_record_id,source_revision_id,batch_id,request_id,recorded_at,schema_version,app_version,origin_store_id,"
        "origin_event_id,origin_sequence,origin_actor_id,imported_at,origin_alias,origin_json,origin_sha256 "
        "FROM trace_events WHERE " + " AND ".join(clauses) + " ORDER BY sequence DESC LIMIT ?",
        (*args, limit),
    )
    return [_event_from_row(cur, row) for row in cur.fetchall()]


def snapshot_events(
    conn: sqlite3.Connection,
    task_id: str,
    actor_id: str | int,
    *,
    cutoff: int | None = None,
) -> dict[str, Any]:
    """Read a fixed actor snapshot; callers may page with its returned cutoff."""
    ensure_schema(conn)
    task, actor = _text(task_id, "task_id", 200), _text(str(actor_id), "actor_id", 200)
    if cutoff is None:
        cutoff = int(conn.execute(
            "SELECT COALESCE(MAX(sequence),0) FROM trace_events WHERE task_id=? AND actor_id=?",
            (task, actor),
        ).fetchone()[0])
    first = list_events(conn, task, actor, limit=200, snapshot_sequence=cutoff)
    events = list(first["events"])
    while first["has_more"]:
        first = list_events(
            conn, task, actor, limit=200, cursor=events[-1]["sequence"],
            snapshot_sequence=cutoff,
        )
        events.extend(first["events"])
    return {"events": events, "cutoff_sequence": cutoff}


def snapshot_cutoff(conn: sqlite3.Connection, task_id: str, actor_id: str | int) -> int:
    ensure_schema(conn)
    return int(conn.execute(
        "SELECT COALESCE(MAX(sequence),0) FROM trace_events WHERE task_id=? AND actor_id=?",
        (_text(task_id, "task_id", 200), _text(str(actor_id), "actor_id", 200)),
    ).fetchone()[0])


_RESEARCH_ACTION_SQL = (
    "(action LIKE 'decision_%' OR action LIKE 'coding_value_%' OR action LIKE 'coding_note_%' "
    "OR action LIKE 'coding_image_%' OR action LIKE 'coding_note_image_%' "
    "OR action LIKE 'rater_record_%' OR action LIKE 'fulltext_%' OR action LIKE 'imported_decision_%' "
    "OR action LIKE 'imported_fulltext_%' "
    "OR action LIKE 'imported_coding_value_%' OR action LIKE 'imported_coding_note_%' "
    "OR action LIKE 'imported_coding_image_%' OR action LIKE 'imported_coding_note_image_%' "
    "OR action LIKE 'imported_rater_record_%')"
)
_MODIFIED_ACTION_SQL = "(action LIKE '%_changed' OR action LIKE '%_cleared' OR action LIKE '%_removed')"


def subject_ids(
    conn: sqlite3.Connection,
    task_id: str,
    actor_id: str | int,
    *,
    kind: str = "touched",
    snapshot_sequence: int | None = None,
) -> list[str]:
    """Return unique document subjects for exact cross-partition UI aggregation."""
    ensure_schema(conn)
    if kind not in {"touched", "modified"}:
        raise ValueError("kind must be touched or modified")
    task, actor = _text(task_id, "task_id", 200), _text(str(actor_id), "actor_id", 200)
    cutoff = snapshot_cutoff(conn, task, actor) if snapshot_sequence is None else snapshot_sequence
    action_filter = _RESEARCH_ACTION_SQL
    if kind == "modified":
        action_filter = (
            f"({_MODIFIED_ACTION_SQL} OR action LIKE 'imported_%_changed' "
            "OR action LIKE 'imported_%_cleared' OR action LIKE 'imported_%_removed')"
        )
    rows = conn.execute(
        "SELECT DISTINCT subject_id FROM trace_events WHERE task_id=? AND actor_id=? "
        "AND stage IN ('screen','fulltext','coding') AND sequence<=? AND " + action_filter +
        " ORDER BY subject_id", (task, actor, cutoff),
    )
    return [str(row[0]) for row in rows]


def summary(
    conn: sqlite3.Connection,
    task_id: str,
    actor_id: str | int,
    *,
    snapshot_sequence: int | None = None,
) -> dict[str, Any]:
    ensure_schema(conn)
    task, actor = _text(task_id, "task_id", 200), _text(str(actor_id), "actor_id", 200)
    cutoff = snapshot_cutoff(conn, task, actor) if snapshot_sequence is None else snapshot_sequence
    modified_filter = (
        f"({_MODIFIED_ACTION_SQL} OR action LIKE 'imported_%_changed' "
        "OR action LIKE 'imported_%_cleared' OR action LIKE 'imported_%_removed')"
    )
    research_stage = "stage IN ('screen','fulltext','coding') AND " + _RESEARCH_ACTION_SQL
    aggregates = [
        "COUNT(*)", "MIN(sequence)", "MAX(sequence)", "MIN(recorded_at)", "MAX(recorded_at)",
        f"COUNT(DISTINCT CASE WHEN {research_stage} THEN subject_id END)",
        f"COUNT(DISTINCT CASE WHEN {research_stage} AND {modified_filter} THEN subject_id END)",
        "SUM(CASE WHEN action LIKE 'field_%' OR action LIKE 'imported_field_%' THEN 1 ELSE 0 END)",
    ]
    for stage in STAGES:
        aggregates.extend((
            f"SUM(CASE WHEN stage='{stage}' AND {_RESEARCH_ACTION_SQL} THEN 1 ELSE 0 END)",
            f"COUNT(DISTINCT CASE WHEN stage='{stage}' AND {_RESEARCH_ACTION_SQL} THEN subject_id END)",
        ))
    row = conn.execute(
        "SELECT " + ",".join(aggregates) +
        " FROM trace_events WHERE task_id=? AND actor_id=? AND sequence<=?",
        (task, actor, cutoff),
    ).fetchone()
    touched_count, modified_count, field_change_count = int(row[5] or 0), int(row[6] or 0), int(row[7] or 0)
    by_stage = {key: {"event_count": 0, "unique_subject_count": 0} for key in STAGES}
    change_count = 0
    for index, stage in enumerate(STAGES):
        event_count, unique_count = int(row[8 + index * 2] or 0), int(row[9 + index * 2] or 0)
        by_stage[stage] = {"event_count": event_count, "unique_subject_count": unique_count}
        change_count += event_count
    daily_map: dict[str, dict[str, Any]] = {}
    for day, stage, count, distinct in conn.execute(
        "SELECT substr(recorded_at,1,10),stage,COUNT(*),COUNT(DISTINCT subject_id) "
        "FROM trace_events WHERE task_id=? AND actor_id=? AND stage IN ('screen','fulltext','coding') "
        "AND sequence<=? AND " + _RESEARCH_ACTION_SQL + " GROUP BY substr(recorded_at,1,10),stage "
        "ORDER BY substr(recorded_at,1,10),stage", (task, actor, cutoff),
    ):
        daily = daily_map.setdefault(day, {"date": day, "event_count": 0, "unique_subject_count": 0, "by_stage": {}})
        daily["event_count"] += int(count)
        daily["unique_subject_count"] += int(distinct)
        daily["by_stage"][stage] = {"event_count": int(count), "unique_subject_count": int(distinct)}
    # Sum stage-specific counts above, but compute the daily union in SQL because a
    # document may change in several stages on one day. This keeps Python memory
    # bounded even when the event log reaches a million rows.
    for day, distinct in conn.execute(
        "SELECT substr(recorded_at,1,10),COUNT(DISTINCT subject_id) FROM trace_events "
        "WHERE task_id=? AND actor_id=? AND stage IN ('screen','fulltext','coding') "
        "AND sequence<=? AND " + _RESEARCH_ACTION_SQL + " GROUP BY substr(recorded_at,1,10)",
        (task, actor, cutoff),
    ):
        if day in daily_map:
            daily_map[day]["unique_subject_count"] = int(distinct)
    control_counts = {
        str(action): int(count)
        for action, count in conn.execute(
            "SELECT action,COUNT(*) FROM trace_events WHERE task_id=? AND actor_id=? "
            "AND stage='control' AND sequence<=? GROUP BY action ORDER BY action",
            (task, actor, cutoff),
        )
    }
    settings = get_settings(conn, task, actor)
    return {
        "event_count": int(row[0]), "change_count": change_count,
        "unique_subject_count": touched_count, "modified_subject_count": modified_count,
        "field_change_count": field_change_count,
        "by_stage": by_stage, "daily": list(daily_map.values()),
        "control_event_count": sum(control_counts.values()),
        "pause_count": control_counts.get("recording_paused", 0),
        "control_counts": control_counts,
        "first_sequence": row[1], "last_sequence": row[2],
        "first_at": row[3], "last_at": row[4], "status": settings["status"],
        "stages": settings["stages"], "store_id": settings["store_id"],
        "snapshot_sequence": cutoff,
    }


def import_event(
    conn: sqlite3.Connection,
    *,
    task_id: str,
    importer_actor_id: str | int,
    event: dict[str, Any],
    mapped_subject_id: str | None = None,
    source_alias: str | None = None,
) -> dict[str, Any]:
    """Append one validated origin event under the importing actor, preserving its author."""
    ensure_schema(conn)
    task = _text(task_id, "task_id", 200)
    actor = _text(str(importer_actor_id), "importer_actor_id", 200)
    if not isinstance(event, dict):
        raise ValueError("event must be an object")
    required = ("event_id", "store_id", "sequence", "stage", "action", "subject_id", "recorded_at")
    if any(key not in event for key in required) or not (event.get("actor_id") or event.get("actor_alias")):
        raise ValueError("origin event is missing required fields")
    try:
        origin_event_id = str(uuid.UUID(str(event["event_id"])))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError("origin event_id must be a UUID") from exc
    origin_store_id = _text(str(event["store_id"]), "origin store_id", 200)
    if type(event["sequence"]) is not int or event["sequence"] <= 0:
        raise ValueError("origin sequence must be a positive integer")
    if event["stage"] not in (*STAGES, "control"):
        raise ValueError("origin stage is invalid")
    original = _origin_record(event)
    original_json = _json(original)
    digest = origin_event_sha256(event)
    existing = conn.execute(
        "SELECT event_id,sequence,origin_sha256 FROM trace_events WHERE task_id=? AND actor_id=? "
        "AND origin_store_id=? AND origin_event_id=?",
        (task, actor, origin_store_id, origin_event_id),
    ).fetchone()
    if existing:
        if existing[2] == digest:
            return {"status": "duplicate", "event_id": existing[0], "sequence": int(existing[1])}
        return {"status": "conflict", "event_id": existing[0], "sequence": int(existing[1])}
    origin = {
        "store_id": origin_store_id, "event_id": origin_event_id,
        "sequence": event["sequence"], "actor_id": str(event["actor_id"]) if event.get("actor_id") else None,
        "imported_at": _now(), "alias": _optional_text(source_alias, "source_alias", 200)
        or _optional_text(event.get("actor_alias"), "actor_alias", 200),
        "event": original, "sha256": digest,
    }
    mapped = _optional_text(mapped_subject_id, "mapped_subject_id", 500)
    local_event_id = str(uuid.uuid4())
    cur = conn.execute(
        "INSERT INTO trace_events (event_id,store_id,task_id,actor_id,stage,action,subject_id,before_json,after_json,"
        "field_json,source,source_record_id,source_revision_id,batch_id,recorded_at,schema_version,app_version,"
        "origin_store_id,origin_event_id,origin_sequence,origin_actor_id,imported_at,origin_alias,origin_json,origin_sha256) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (local_event_id, _store_id(conn), task, actor, event["stage"], "imported_" + str(event["action"]),
         mapped or str(event["subject_id"]), None if event.get("before") is None else _json(event.get("before")),
         None if event.get("after") is None else _json(event.get("after")),
         None if event.get("field") is None else _json(event.get("field")), "import",
         _optional_text(event.get("source_record_id"), "source_record_id", 300), None,
         _optional_text(event.get("batch_id"), "batch_id", 300), _text(str(event["recorded_at"]), "recorded_at", 80),
         str(event.get("schema_version") or SCHEMA_VERSION), str(event.get("app_version") or "unknown"),
         origin["store_id"], origin["event_id"], origin["sequence"], origin["actor_id"], origin["imported_at"],
         origin["alias"], _json(original), digest),
    )
    return {"status": "imported", "event_id": local_event_id, "sequence": int(cur.lastrowid)}


def demo() -> None:
    """Small runnable check for default-off, append, pause and actor isolation."""
    conn = sqlite3.connect(":memory:")
    context = TraceContext("demo", 1)
    assert get_settings(conn, "demo", 1)["status"] == "off"
    assert capture_event(conn, context, stage="screen", action="decision_changed", subject_id="a", before=None, after={"decision": "include"}) is None
    update_settings(conn, context, stages=["screen"], status="recording", operation_id="one", expected_version=0)
    assert capture_event(conn, context, stage="screen", action="decision_changed", subject_id="a", before=None, after={"decision": "include"})
    assert summary(conn, "demo", 1)["modified_subject_count"] == 1
    assert list_events(conn, "demo", 1)["events"]
    update_settings(conn, context, stages=["screen"], status="paused", operation_id="two", expected_version=1)
    assert capture_event(conn, context, stage="screen", action="decision_changed", subject_id="b", before=None, after={"decision": "exclude"}) is None
    assert summary(conn, "demo", 2)["event_count"] == 0


if __name__ == "__main__":
    demo()
