"""Append-only independent coding, consensus, and frozen consensus snapshots.

The caller supplies the SQLite file that defines the collaboration scope and the
authenticated actor ID. Existing single-coder coding tables are left untouched.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing, contextmanager
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from typing import TYPE_CHECKING

StrPath = str | Path

if TYPE_CHECKING:
    from coscreen.research_trace import TraceContext

_SCHEMA = """
CREATE TABLE IF NOT EXISTS coding_rater_records (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  item_key TEXT NOT NULL,
  field_key TEXT NOT NULL,
  rater_id TEXT NOT NULL,
  version INTEGER NOT NULL CHECK(version > 0),
  supersedes_id INTEGER REFERENCES coding_rater_records(id),
  value_json TEXT NOT NULL,
  source_json TEXT NOT NULL,
  rationale TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(item_key, field_key, rater_id, version)
);
CREATE INDEX IF NOT EXISTS idx_coding_rater_records_cell
  ON coding_rater_records(item_key, field_key, rater_id, version);
CREATE TABLE IF NOT EXISTS coding_consensus_records (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  item_key TEXT NOT NULL,
  field_key TEXT NOT NULL,
  version INTEGER NOT NULL CHECK(version > 0),
  supersedes_id INTEGER REFERENCES coding_consensus_records(id),
  reconciler_id TEXT NOT NULL,
  value_json TEXT NOT NULL,
  source_json TEXT NOT NULL,
  rationale TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(item_key, field_key, version)
);
CREATE INDEX IF NOT EXISTS idx_coding_consensus_cell
  ON coding_consensus_records(item_key, field_key, version);
CREATE TABLE IF NOT EXISTS coding_consensus_sources (
  consensus_id INTEGER NOT NULL REFERENCES coding_consensus_records(id),
  rater_record_id INTEGER NOT NULL REFERENCES coding_rater_records(id),
  PRIMARY KEY(consensus_id, rater_record_id)
);
CREATE TRIGGER IF NOT EXISTS coding_rater_records_no_update
BEFORE UPDATE ON coding_rater_records BEGIN
  SELECT RAISE(ABORT, 'rater records are append-only');
END;
CREATE TRIGGER IF NOT EXISTS coding_rater_records_no_delete
BEFORE DELETE ON coding_rater_records BEGIN
  SELECT RAISE(ABORT, 'rater records are append-only');
END;
CREATE TRIGGER IF NOT EXISTS coding_consensus_records_no_update
BEFORE UPDATE ON coding_consensus_records BEGIN
  SELECT RAISE(ABORT, 'consensus records are append-only');
END;
CREATE TRIGGER IF NOT EXISTS coding_consensus_records_no_delete
BEFORE DELETE ON coding_consensus_records BEGIN
  SELECT RAISE(ABORT, 'consensus records are append-only');
END;
CREATE TRIGGER IF NOT EXISTS coding_consensus_sources_no_update
BEFORE UPDATE ON coding_consensus_sources BEGIN
  SELECT RAISE(ABORT, 'consensus sources are append-only');
END;
CREATE TRIGGER IF NOT EXISTS coding_consensus_sources_no_delete
BEFORE DELETE ON coding_consensus_sources BEGIN
  SELECT RAISE(ABORT, 'consensus sources are append-only');
END;
CREATE TABLE IF NOT EXISTS coding_freezes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  version INTEGER NOT NULL CHECK(version > 0),
  previous_freeze_id INTEGER REFERENCES coding_freezes(id),
  frozen_by TEXT NOT NULL,
  created_at TEXT NOT NULL,
  records_json TEXT NOT NULL,
  content_sha256 TEXT NOT NULL,
  UNIQUE(name, version)
);
CREATE TRIGGER IF NOT EXISTS coding_freezes_no_update
BEFORE UPDATE ON coding_freezes BEGIN
  SELECT RAISE(ABORT, 'coding freezes are immutable');
END;
CREATE TRIGGER IF NOT EXISTS coding_freezes_no_delete
BEFORE DELETE ON coding_freezes BEGIN
  SELECT RAISE(ABORT, 'coding freezes are immutable');
END;
"""


def _connect(path: StrPath) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SCHEMA)
    return conn


@contextmanager
def _write(path: StrPath) -> Iterator[sqlite3.Connection]:
    with closing(_connect(path)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def _text(value: Any, name: str, limit: int, *, required: bool = True) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be text")
    cleaned = value.strip()
    if (required and not cleaned) or len(cleaned) > limit:
        raise ValueError(f"{name} must contain 1 to {limit} characters")
    return cleaned


def _actor(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError("actor ID must come from authenticated user identity")
    return _text(str(value), "actor ID", 200)


def _json(value: Any) -> str:
    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("value and source must be finite JSON data") from exc
    if len(encoded.encode("utf-8")) > 1_000_000:
        raise ValueError("value and source must be at most 1 MB")
    return encoded


def _source(value: Any) -> str:
    if not isinstance(value, dict) or not value:
        raise ValueError("source must be a non-empty object")
    return _json(value)


def _ids(values: Any, name: str) -> list[int]:
    if not isinstance(values, (list, tuple)) or not values or len(values) > 10_000:
        raise ValueError(f"{name} must contain 1 to 10000 record IDs")
    if any(type(value) is not int or value <= 0 for value in values):
        raise ValueError(f"{name} must contain positive integer IDs")
    result = list(values)
    if len(result) != len(set(result)):
        raise ValueError(f"{name} must contain unique IDs")
    return result


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _record(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "item_key": row["item_key"],
        "field_key": row["field_key"],
        "rater_id": row["rater_id"],
        "version": int(row["version"]),
        "supersedes_id": row["supersedes_id"],
        "value": json.loads(row["value_json"]),
        "source": json.loads(row["source_json"]),
        "rationale": row["rationale"],
        "created_at": row["created_at"],
    }


def append_rater_record(
    db: StrPath,
    *,
    item_key: str,
    field_key: str,
    rater_id: str | int,
    value: Any,
    source: dict[str, Any],
    rationale: str = "",
    trace_context: TraceContext | None = None,
) -> dict[str, Any]:
    """Append a coder's version; `rater_id` must be supplied from server auth."""
    item_key = _text(item_key, "item_key", 300)
    field_key = _text(field_key, "field_key", 200)
    rater_id = _actor(rater_id)
    value_json, source_json = _json(value), _source(source)
    rationale = _text(rationale, "rationale", 4000, required=False)
    if trace_context is not None:
        trace_context = trace_context.normalized()
        if str(trace_context.actor_id) != rater_id:
            raise ValueError("trace actor must match the authenticated rater ID")
    with _write(db) as conn:
        if trace_context is not None:
            from coscreen.research_trace import begin_write_request, is_recording

            recording = is_recording(conn, trace_context, "coding")
            replay, result = begin_write_request(
                conn, trace_context, stage="coding", operation="rater_record_save",
                subject_id=item_key,
                payload={"field_key": field_key, "value": json.loads(value_json),
                         "source": json.loads(source_json), "rationale": rationale},
            )
            if replay:
                return result
        previous = conn.execute(
            "SELECT * FROM coding_rater_records "
            "WHERE item_key=? AND field_key=? AND rater_id=? ORDER BY version DESC LIMIT 1",
            (item_key, field_key, rater_id),
        ).fetchone()
        if previous is not None and trace_context is not None:
            if (
                recording
                and previous["value_json"] == value_json
                and previous["source_json"] == source_json
                and previous["rationale"] == rationale
            ):
                result = _record(previous)
                from coscreen.research_trace import finish_write_request

                finish_write_request(conn, trace_context, result)
                return result
        version = int(previous["version"]) + 1 if previous else 1
        cursor = conn.execute(
            "INSERT INTO coding_rater_records "
            "(item_key,field_key,rater_id,version,supersedes_id,value_json,source_json,rationale,created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (item_key, field_key, rater_id, version, previous["id"] if previous else None,
             value_json, source_json, rationale, _now()),
        )
        row = conn.execute("SELECT * FROM coding_rater_records WHERE id=?", (cursor.lastrowid,)).fetchone()
        if trace_context is not None:
            from coscreen.research_trace import capture_event

            before = ({
                "value": json.loads(previous["value_json"]),
                "source": json.loads(previous["source_json"]),
                "rationale": previous["rationale"],
            } if previous else None)
            after = {
                "value": json.loads(value_json), "source": json.loads(source_json),
                "rationale": rationale,
            }
            event_context = replace(trace_context, source_revision_id=str(cursor.lastrowid))
            field = source.get("_reviewflow_field") or {"field_id": field_key}
            capture_event(
                conn, event_context, stage="coding",
                action="rater_record_created" if previous is None else "rater_record_changed",
                subject_id=item_key, before=before, after=after, field=field,
            )
        result = _record(row)
        if trace_context is not None:
            from coscreen.research_trace import finish_write_request

            finish_write_request(conn, trace_context, result)
        return result


def list_rater_records(
    db: StrPath, *, item_key: str, field_key: str, rater_id: str | int | None = None
) -> list[dict[str, Any]]:
    item_key = _text(item_key, "item_key", 300)
    field_key = _text(field_key, "field_key", 200)
    sql = "SELECT * FROM coding_rater_records WHERE item_key=? AND field_key=?"
    args: list[Any] = [item_key, field_key]
    if rater_id is not None:
        sql += " AND rater_id=?"
        args.append(_actor(rater_id))
    sql += " ORDER BY rater_id, version"
    with closing(_connect(db)) as conn:
        return [_record(row) for row in conn.execute(sql, args).fetchall()]


def latest_rater_records_for_field(
    db: StrPath, *, field_key: str, rater_ids: tuple[str, str]
) -> list[dict[str, Any]]:
    """Return each selected coder's latest immutable revision for one field."""
    field_key = _text(field_key, "field_key", 200)
    if len(rater_ids) != 2 or rater_ids[0] == rater_ids[1]:
        raise ValueError("select two different coders")
    raters = tuple(_actor(value) for value in rater_ids)
    with closing(_connect(db)) as conn:
        rows = conn.execute(
            "SELECT * FROM (SELECT *, ROW_NUMBER() OVER (PARTITION BY item_key,rater_id "
            "ORDER BY version DESC) AS latest FROM coding_rater_records "
            "WHERE field_key=? AND rater_id IN (?,?)) WHERE latest=1 ORDER BY item_key,rater_id",
            (field_key, *raters),
        ).fetchall()
    return [_record(row) for row in rows]


def _consensus_record(conn: sqlite3.Connection, consensus_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM coding_consensus_records WHERE id=?", (consensus_id,)).fetchone()
    if row is None:
        return None
    payload = {
        "id": int(row["id"]),
        "item_key": row["item_key"],
        "field_key": row["field_key"],
        "version": int(row["version"]),
        "supersedes_id": row["supersedes_id"],
        "reconciler_id": row["reconciler_id"],
        "value": json.loads(row["value_json"]),
        "source": json.loads(row["source_json"]),
        "rationale": row["rationale"],
        "rater_record_ids": [r[0] for r in conn.execute(
            "SELECT rater_record_id FROM coding_consensus_sources WHERE consensus_id=? ORDER BY rater_record_id",
            (consensus_id,),
        )],
        "created_at": row["created_at"],
    }
    return payload


def append_consensus_record(
    db: StrPath,
    *,
    item_key: str,
    field_key: str,
    reconciler_id: str | int,
    value: Any,
    source: dict[str, Any],
    rationale: str,
    rater_record_ids: tuple[int, ...] | list[int] = (),
) -> dict[str, Any]:
    """Append a consensus entity linked to its independent inputs and rationale."""
    item_key = _text(item_key, "item_key", 300)
    field_key = _text(field_key, "field_key", 200)
    reconciler_id = _actor(reconciler_id)
    value_json, source_json = _json(value), _source(source)
    rationale = _text(rationale, "rationale", 4000)
    ids = _ids(rater_record_ids, "rater_record_ids") if rater_record_ids else []
    with _write(db) as conn:
        if ids:
            rows = conn.execute(
                f"SELECT id,item_key,field_key FROM coding_rater_records WHERE id IN ({','.join('?' for _ in ids)})",
                ids,
            ).fetchall()
            if len(rows) != len(ids) or any(
                row["item_key"] != item_key or row["field_key"] != field_key for row in rows
            ):
                raise ValueError("consensus inputs must be existing coder records for the same item and field")
        previous = conn.execute(
            "SELECT id,version FROM coding_consensus_records WHERE item_key=? AND field_key=? "
            "ORDER BY version DESC LIMIT 1",
            (item_key, field_key),
        ).fetchone()
        cursor = conn.execute(
            "INSERT INTO coding_consensus_records "
            "(item_key,field_key,version,supersedes_id,reconciler_id,value_json,source_json,rationale,created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (item_key, field_key, int(previous["version"]) + 1 if previous else 1,
             previous["id"] if previous else None, reconciler_id, value_json, source_json, rationale, _now()),
        )
        consensus_id = int(cursor.lastrowid)
        conn.executemany(
            "INSERT INTO coding_consensus_sources(consensus_id,rater_record_id) VALUES (?,?)",
            ((consensus_id, record_id) for record_id in ids),
        )
        return _consensus_record(conn, consensus_id)


def list_consensus_records(
    db: StrPath, *, item_key: str, field_key: str
) -> list[dict[str, Any]]:
    item_key = _text(item_key, "item_key", 300)
    field_key = _text(field_key, "field_key", 200)
    with closing(_connect(db)) as conn:
        ids = conn.execute(
            "SELECT id FROM coding_consensus_records WHERE item_key=? AND field_key=? ORDER BY version",
            (item_key, field_key),
        ).fetchall()
        return [_consensus_record(conn, row[0]) for row in ids]


def freeze_consensus(
    db: StrPath,
    *,
    name: str,
    frozen_by: str | int,
    consensus_ids: tuple[int, ...] | list[int],
) -> dict[str, Any]:
    """Freeze explicit consensus record IDs as an immutable, named version."""
    name = _text(name, "freeze name", 200)
    frozen_by = _actor(frozen_by)
    ids = _ids(consensus_ids, "consensus_ids")
    with _write(db) as conn:
        rows = conn.execute(
            f"SELECT id,item_key,field_key FROM coding_consensus_records WHERE id IN ({','.join('?' for _ in ids)})",
            ids,
        ).fetchall()
        if len(rows) != len(ids):
            raise ValueError("all selected consensus records must exist")
        cells = [(row["item_key"], row["field_key"]) for row in rows]
        if len(cells) != len(set(cells)):
            raise ValueError("select at most one consensus version per item and field")
        records = [_consensus_record(conn, record_id) for record_id in ids]
        records.sort(key=lambda record: (record["item_key"], record["field_key"]))
        records_json = _json(records)
        digest = hashlib.sha256(records_json.encode("utf-8")).hexdigest()
        previous = conn.execute(
            "SELECT id,version FROM coding_freezes WHERE name=? ORDER BY version DESC LIMIT 1", (name,)
        ).fetchone()
        version = int(previous["version"]) + 1 if previous else 1
        cursor = conn.execute(
            "INSERT INTO coding_freezes "
            "(name,version,previous_freeze_id,frozen_by,created_at,records_json,content_sha256) "
            "VALUES (?,?,?,?,?,?,?)",
            (name, version, previous["id"] if previous else None, frozen_by, _now(), records_json, digest),
        )
        return _freeze_row(conn.execute("SELECT * FROM coding_freezes WHERE id=?", (cursor.lastrowid,)).fetchone())


def _freeze_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "name": row["name"],
        "version": int(row["version"]),
        "previous_freeze_id": row["previous_freeze_id"],
        "frozen_by": row["frozen_by"],
        "created_at": row["created_at"],
        "content_sha256": row["content_sha256"],
        "records": json.loads(row["records_json"]),
    }


def get_freeze(db: StrPath, freeze_id: int) -> dict[str, Any] | None:
    with closing(_connect(db)) as conn:
        row = conn.execute("SELECT * FROM coding_freezes WHERE id=?", (int(freeze_id),)).fetchone()
        return None if row is None else _freeze_row(row)


def list_freezes(db: StrPath, *, name: str | None = None) -> list[dict[str, Any]]:
    sql, args = "SELECT * FROM coding_freezes", []
    if name is not None:
        sql += " WHERE name=?"
        args.append(_text(name, "freeze name", 200))
    sql += " ORDER BY name,version"
    with closing(_connect(db)) as conn:
        return [
            {key: value for key, value in _freeze_row(row).items() if key != "records"}
            for row in conn.execute(sql, args).fetchall()
        ]
