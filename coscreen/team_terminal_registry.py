"""Team terminal registry storage (F2).

Self-contained stdlib-only low-level store for task instance/protocol and per-source
registration history.  It is intentionally not wired to any route, producer, F1
module, or existing collaboration writer.

Design authority: F2_API_CONTRACT_R1.txt + F2_API_CONTRACT_R1_ADDENDUM.txt and the
final main-control brief terminal-f2-registry-brief-20261116.md.

No SQL is executed at import time.  ``prepare_team_terminal_registry`` is the only
self-owned connection/DDL path; every other API borrows a caller connection and an
active caller transaction.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
import uuid
from collections.abc import Iterator
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from coscreen.team_flow_types import TaskBinding

__all__ = [
    "TerminalRegistryPayloadInvalid",
    "TerminalRegistryConflict",
    "TerminalRegistryStateError",
    "TerminalRegistryUnavailable",
    "prepare_team_terminal_registry",
    "read_protocol_for_task_on",
    "read_protocol_on",
    "coverage_status_on",
    "register_protocol_on",
    "mark_legacy_verified_on",
    "set_roster_revision_on",
    "register_source_on",
    "retire_source_on",
    "read_source_on",
    "list_sources_on",
    "active_source_on",
    "source_status_on",
    "TaskDiscoveryHeader",
    "SourceDiscoveryRow",
    "read_task_discovery_header_on",
    "iter_source_discovery_on",
]

MAX_INT64 = 9223372036854775807
PROTOCOL_VERSION = 1
EVIDENCE_SCHEMA = "team_terminal_legacy_adoption.v1"

_COVERAGE_STATES = frozenset(
    {"new_covered", "legacy_pending_verification", "legacy_verified"}
)
_SOURCE_STATUSES = frozenset({"active", "retired"})
_UNAVAILABLE_REASONS = frozenset(
    {"not_prepared", "schema_mismatch", "unreadable", "corrupt", "connection_contract"}
)
_EVIDENCE_CALLER_KEYS = frozenset(
    {
        "sources",
        "verification_scope",
        "verification_method",
        "conflict_handling",
        "prior_history_unknown",
    }
)
_EVIDENCE_TOP_KEYS = frozenset(
    {
        "schema",
        "task_instance_id",
        "task_id",
        "owner_user_id",
        "owner_library_id",
        "sources",
        "verification_scope",
        "verification_method",
        "conflict_handling",
        "prior_history_unknown",
        "forward_coverage_start",
    }
)
_EVIDENCE_SOURCE_KEYS = frozenset(
    {"source_user_id", "registration_id", "generation", "source_library_id"}
)

_PROTOCOL_COLUMNS = (
    "task_instance_id",
    "task_id",
    "owner_user_id",
    "owner_library_id",
    "protocol_version",
    "coverage_state",
    "roster_revision",
    "adopted_at",
    "adoption_evidence_ref",
    "adoption_evidence_json",
    "adoption_evidence_sha256",
    "created_at",
    "updated_at",
)
_SOURCE_COLUMNS = (
    "registration_id",
    "task_instance_id",
    "source_user_id",
    "source_library_id",
    "source_path_identity",
    "generation",
    "status",
    "registered_at",
    "retired_at",
    "retired_reason",
    "archive_watermark",
)


class TerminalRegistryPayloadInvalid(ValueError):
    """Input payload/identity/state shape is invalid."""


class TerminalRegistryConflict(RuntimeError):
    """Idempotency, identity, generation, CAS, or state-transition conflict."""


class TerminalRegistryStateError(RuntimeError):
    """The required protocol/source state does not exist for this operation."""


class TerminalRegistryUnavailable(RuntimeError):
    """Storage/layout is unavailable; ``reason`` is a fixed contract reason."""

    def __init__(
        self,
        reason: str,
        message: str = "",
        cause: BaseException | None = None,
    ) -> None:
        if reason not in _UNAVAILABLE_REASONS:
            raise ValueError(f"invalid TerminalRegistryUnavailable reason: {reason!r}")
        self.reason = reason
        self.cause = cause
        text = reason if not message else f"{reason}: {message}"
        super().__init__(text)
        if cause is not None:
            self.__cause__ = cause


# ---------------------------------------------------------------------------
# Discovery DTOs (C04 narrow reads)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TaskDiscoveryHeader:
    """Minimal per-task discovery header.

    ``binding`` is the strictly validated immutable task/instance/owner
    identity.  Coverage-side health travels separately: ``coverage_status``
    uses the C04 vocabulary new_covered/legacy_forward_only/
    pending_verification/unknown, ``coverage_evidence`` carries the parsed
    adoption document of a healthy legacy_verified row (else ``None``), and
    ``coverage_issue`` locates coverage-side damage (else ``None``).  An
    unreadable ``roster_revision`` is reported as ``None`` through the issue
    channel instead of denying the identity.
    """

    binding: TaskBinding
    roster_revision: int | None
    coverage_status: str
    coverage_evidence: Mapping[str, object] | None
    coverage_issue: str | None


@dataclass(frozen=True)
class SourceDiscoveryRow:
    """One enumerated registry row: exactly one of ``source``/``issue`` set."""

    registration_id: str | None
    source: Mapping[str, object] | None
    issue: str | None


# ---------------------------------------------------------------------------
# Schema (exact object set: two tables, four triggers, one partial unique index)
# ---------------------------------------------------------------------------

_TABLE_PROTOCOL_SQL = """CREATE TABLE main.team_task_protocol (
  task_instance_id TEXT NOT NULL PRIMARY KEY,
  task_id TEXT NOT NULL UNIQUE CHECK (length(task_id) BETWEEN 1 AND 200),
  owner_user_id INTEGER NOT NULL CHECK (
    typeof(owner_user_id) = 'integer' AND owner_user_id > 0
  ),
  owner_library_id TEXT NOT NULL CHECK (
    length(owner_library_id) = 32 AND
    owner_library_id NOT GLOB '*[^0-9a-f]*'
  ),
  protocol_version INTEGER NOT NULL DEFAULT 1 CHECK (
    typeof(protocol_version) = 'integer' AND protocol_version = 1
  ),
  coverage_state TEXT NOT NULL CHECK (
    coverage_state IN ('new_covered','legacy_pending_verification','legacy_verified')
  ),
  roster_revision INTEGER NOT NULL DEFAULT 0 CHECK (
    typeof(roster_revision) = 'integer' AND
    roster_revision BETWEEN 0 AND 9223372036854775807
  ),
  adopted_at TEXT NULL,
  adoption_evidence_ref TEXT NULL,
  adoption_evidence_json TEXT NULL,
  adoption_evidence_sha256 TEXT NULL CHECK (
    adoption_evidence_sha256 IS NULL OR (
      length(adoption_evidence_sha256) = 64 AND
      adoption_evidence_sha256 NOT GLOB '*[^0-9a-f]*'
    )
  ),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  CHECK (
    (
      coverage_state IN ('new_covered','legacy_pending_verification') AND
      adopted_at IS NULL AND
      adoption_evidence_ref IS NULL AND
      adoption_evidence_json IS NULL AND
      adoption_evidence_sha256 IS NULL
    ) OR (
      coverage_state = 'legacy_verified' AND
      adopted_at IS NOT NULL AND
      adoption_evidence_ref IS NOT NULL AND
      adoption_evidence_json IS NOT NULL AND
      adoption_evidence_sha256 IS NOT NULL
    )
  )
)"""

_TABLE_SOURCE_SQL = """CREATE TABLE main.team_source_registry (
  registration_id TEXT NOT NULL PRIMARY KEY,
  task_instance_id TEXT NOT NULL,
  source_user_id INTEGER NOT NULL CHECK (
    typeof(source_user_id) = 'integer' AND source_user_id > 0
  ),
  source_library_id TEXT NOT NULL CHECK (
    length(source_library_id) = 32 AND
    source_library_id NOT GLOB '*[^0-9a-f]*'
  ),
  source_path_identity TEXT NOT NULL,
  generation INTEGER NOT NULL CHECK (
    typeof(generation) = 'integer' AND
    generation BETWEEN 1 AND 9223372036854775807
  ),
  status TEXT NOT NULL CHECK (status IN ('active','retired')),
  registered_at TEXT NOT NULL,
  retired_at TEXT NULL,
  retired_reason TEXT NULL,
  archive_watermark TEXT NULL,
  UNIQUE (task_instance_id, source_user_id, generation),
  CHECK (
    (
      status = 'active' AND
      retired_at IS NULL AND
      retired_reason IS NULL AND
      archive_watermark IS NULL
    ) OR (
      status = 'retired' AND
      retired_at IS NOT NULL AND
      retired_reason IS NOT NULL
    )
  )
)"""

_INDEX_SQL = """CREATE UNIQUE INDEX main.team_source_registry_one_active
ON team_source_registry(task_instance_id, source_user_id)
WHERE status = 'active'"""

_TRIGGER_PROTOCOL_NO_DELETE_SQL = """CREATE TRIGGER main.team_task_protocol_no_delete
BEFORE DELETE ON team_task_protocol
BEGIN
  SELECT RAISE(ABORT, 'team_task_protocol delete forbidden');
END"""

_TRIGGER_PROTOCOL_GUARD_SQL = """CREATE TRIGGER main.team_task_protocol_guard_update
BEFORE UPDATE ON team_task_protocol
WHEN
  OLD.task_instance_id IS NOT NEW.task_instance_id OR
  OLD.task_id IS NOT NEW.task_id OR
  OLD.owner_user_id IS NOT NEW.owner_user_id OR
  OLD.owner_library_id IS NOT NEW.owner_library_id OR
  OLD.protocol_version IS NOT NEW.protocol_version OR
  OLD.created_at IS NOT NEW.created_at OR
  NEW.updated_at < OLD.updated_at OR
  NEW.roster_revision < OLD.roster_revision OR
  NEW.roster_revision > OLD.roster_revision + 1 OR
  (OLD.coverage_state = 'new_covered' AND NEW.coverage_state IS NOT 'new_covered') OR
  (OLD.coverage_state = 'legacy_verified' AND NEW.coverage_state IS NOT 'legacy_verified') OR
  (
    OLD.coverage_state = 'legacy_pending_verification' AND
    NEW.coverage_state NOT IN ('legacy_pending_verification','legacy_verified')
  ) OR
  (OLD.adopted_at IS NOT NULL AND NEW.adopted_at IS NOT OLD.adopted_at) OR
  (
    OLD.adoption_evidence_ref IS NOT NULL AND
    NEW.adoption_evidence_ref IS NOT OLD.adoption_evidence_ref
  ) OR
  (
    OLD.adoption_evidence_json IS NOT NULL AND
    NEW.adoption_evidence_json IS NOT OLD.adoption_evidence_json
  ) OR
  (
    OLD.adoption_evidence_sha256 IS NOT NULL AND
    NEW.adoption_evidence_sha256 IS NOT OLD.adoption_evidence_sha256
  )
BEGIN
  SELECT RAISE(ABORT, 'team_task_protocol identity/state regression');
END"""

_TRIGGER_SOURCE_NO_DELETE_SQL = """CREATE TRIGGER main.team_source_registry_no_delete
BEFORE DELETE ON team_source_registry
BEGIN
  SELECT RAISE(ABORT, 'team_source_registry delete forbidden');
END"""

_TRIGGER_SOURCE_GUARD_SQL = """CREATE TRIGGER main.team_source_registry_guard_update
BEFORE UPDATE ON team_source_registry
WHEN
  OLD.registration_id IS NOT NEW.registration_id OR
  OLD.task_instance_id IS NOT NEW.task_instance_id OR
  OLD.source_user_id IS NOT NEW.source_user_id OR
  OLD.source_library_id IS NOT NEW.source_library_id OR
  OLD.source_path_identity IS NOT NEW.source_path_identity OR
  OLD.generation IS NOT NEW.generation OR
  OLD.registered_at IS NOT NEW.registered_at OR
  (OLD.status = 'retired' AND NEW.status IS NOT 'retired') OR
  (
    OLD.status = 'retired' AND (
      NEW.retired_at IS NOT OLD.retired_at OR
      NEW.retired_reason IS NOT OLD.retired_reason OR
      NEW.archive_watermark IS NOT OLD.archive_watermark
    )
  ) OR
  (
    OLD.status = 'active' AND NEW.status = 'active' AND (
      NEW.retired_at IS NOT NULL OR
      NEW.retired_reason IS NOT NULL OR
      NEW.archive_watermark IS NOT NULL
    )
  )
BEGIN
  SELECT RAISE(ABORT, 'team_source_registry identity/retirement guard');
END"""

_ALL_DDL = (
    _TABLE_PROTOCOL_SQL,
    _TABLE_SOURCE_SQL,
    _INDEX_SQL,
    _TRIGGER_PROTOCOL_NO_DELETE_SQL,
    _TRIGGER_PROTOCOL_GUARD_SQL,
    _TRIGGER_SOURCE_NO_DELETE_SQL,
    _TRIGGER_SOURCE_GUARD_SQL,
)

_EXPECTED_TABLES = {
    "team_task_protocol": _TABLE_PROTOCOL_SQL,
    "team_source_registry": _TABLE_SOURCE_SQL,
}
_EXPECTED_TRIGGERS = {
    "team_task_protocol_no_delete": ("team_task_protocol", _TRIGGER_PROTOCOL_NO_DELETE_SQL),
    "team_task_protocol_guard_update": ("team_task_protocol", _TRIGGER_PROTOCOL_GUARD_SQL),
    "team_source_registry_no_delete": ("team_source_registry", _TRIGGER_SOURCE_NO_DELETE_SQL),
    "team_source_registry_guard_update": ("team_source_registry", _TRIGGER_SOURCE_GUARD_SQL),
}
_EXPECTED_INDEXES = {
    "team_source_registry_one_active": ("team_source_registry", _INDEX_SQL),
}
_EXPECTED_NAMES = frozenset(
    set(_EXPECTED_TABLES) | set(_EXPECTED_TRIGGERS) | set(_EXPECTED_INDEXES)
)

# Narrow-discovery scope sets (C04): a discovery read validates only the
# schema objects of the registry table it actually reads.  The full
# two-table layout contract above remains the one enforced by the strong
# APIs; these sets intentionally make no statement about the other table.
_PROTOCOL_SCOPE_TABLES = {"team_task_protocol": _TABLE_PROTOCOL_SQL}
_PROTOCOL_SCOPE_TRIGGERS = {
    "team_task_protocol_no_delete": ("team_task_protocol", _TRIGGER_PROTOCOL_NO_DELETE_SQL),
    "team_task_protocol_guard_update": ("team_task_protocol", _TRIGGER_PROTOCOL_GUARD_SQL),
}
_SOURCE_SCOPE_TABLES = {"team_source_registry": _TABLE_SOURCE_SQL}
_SOURCE_SCOPE_TRIGGERS = {
    "team_source_registry_no_delete": ("team_source_registry", _TRIGGER_SOURCE_NO_DELETE_SQL),
    "team_source_registry_guard_update": ("team_source_registry", _TRIGGER_SOURCE_GUARD_SQL),
}
_SOURCE_SCOPE_INDEXES = {
    "team_source_registry_one_active": ("team_source_registry", _INDEX_SQL),
}


# ---------------------------------------------------------------------------
# Small validation / serialization / SQL helpers
# ---------------------------------------------------------------------------


def _payload(message: str) -> "TerminalRegistryPayloadInvalid":
    return TerminalRegistryPayloadInvalid(message)


def _strict_text(value: object, name: str) -> str:
    if type(value) is not str:
        raise _payload(f"{name} must be strict text")
    if value == "":
        raise _payload(f"{name} must be non-empty")
    if "\x00" in value:
        raise _payload(f"{name} must not contain NUL")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise _payload(f"{name} must be valid UTF-8") from exc
    return value


def _host_task_id(value: object) -> str:
    if type(value) is not str:
        raise _payload("task_id must be text")
    text = value.strip()
    if not text or len(text) > 200 or any(ch in text for ch in "/\\\x00"):
        raise _payload("task_id must contain 1 to 200 safe characters")
    try:
        text.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise _payload("task_id must be valid UTF-8") from exc
    return text


def _int_in_range(value: object, name: str, minimum: int, maximum: int) -> int:
    if type(value) is not int:
        raise _payload(f"{name} must be an integer")
    if value < minimum or value > maximum:
        raise _payload(f"{name} is outside the SQLite INTEGER range")
    return value


def _hex32(value: object, name: str) -> str:
    text = _strict_text(value, name)
    if len(text) != 32 or any(ch not in "0123456789abcdef" for ch in text):
        raise _payload(f"{name} must be 32 lowercase hexadecimal characters")
    return text


def _timestamp(value: object, name: str) -> str:
    if type(value) is not str:
        raise _payload(f"{name} must be text")
    if len(value) != 24 or value[4] != "-" or value[10] != "T" or value[19] != "." or value[-1] != "Z":
        raise _payload(f"{name} must be RFC3339 UTC millisecond Z")
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ")
    except ValueError as exc:
        raise _payload(f"{name} must be a valid timestamp") from exc
    return value


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _canonical_json(value: object) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise _payload("evidence is not canonical-JSON serializable") from exc


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _record(row: sqlite3.Row, columns: tuple[str, ...], kind: str) -> dict[str, Any]:
    try:
        return {name: row[name] for name in columns}
    except (IndexError, KeyError) as exc:
        raise TerminalRegistryUnavailable("corrupt", f"{kind} row is missing columns", exc) from exc


class _Rows:
    """Fully materialized query result.

    ``_select`` executes the statement and fetches every row inside its typed
    storage boundary; callers never get a bare cursor whose fetch can escape the
    mapping of SQLite failures.
    """

    __slots__ = ("_rows",)

    def __init__(self, rows: list[sqlite3.Row]) -> None:
        self._rows = tuple(rows)

    def fetchone(self) -> sqlite3.Row | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[sqlite3.Row]:
        return list(self._rows)


def _select(conn: sqlite3.Connection, sql: str, params: tuple[object, ...] = ()) -> _Rows:
    cursor = None
    finished = False
    try:
        cursor = conn.execute(sql, params)
        result = _Rows(cursor.fetchall())
        finished = True
        return result
    except sqlite3.ProgrammingError:
        raise
    except sqlite3.OperationalError as exc:
        raise TerminalRegistryUnavailable("unreadable", "sqlite read failed", exc) from exc
    except sqlite3.DatabaseError as exc:
        raise TerminalRegistryUnavailable("corrupt", "sqlite read failed", exc) from exc
    finally:
        if cursor is not None:
            # Only the internal cursor belongs to this helper.  Materialized
            # rows do not need it, and retained fetch tracebacks must not keep
            # its statement active.  Ignore any unrelated caller except block
            # when this operation itself finished successfully.
            primary = None if finished else sys.exc_info()[1]
            try:
                cursor.close()
            except Exception as cleanup:
                if primary is not None:
                    _attach_cleanup(primary, [cleanup])
                elif isinstance(cleanup, sqlite3.ProgrammingError):
                    raise
                elif isinstance(cleanup, sqlite3.Error):
                    reason = "unreadable" if isinstance(
                        cleanup, sqlite3.OperationalError) else "corrupt"
                    raise TerminalRegistryUnavailable(
                        reason, "sqlite read cursor close failed", cleanup) from cleanup
                else:
                    raise


def _execute(
    conn: sqlite3.Connection, sql: str, params: tuple[object, ...] = (), *,
    cleanup_errors: list[Exception] | None = None,
) -> int:
    cursor = None
    finished = False
    try:
        cursor = conn.execute(sql, params)
        # Result-producing DML (including caller-enabled count_changes) must
        # finish before SQLite exposes its final rowcount.  Prepare also uses
        # this helper for statements without a result set.
        if cursor.description is not None:
            cursor.fetchall()
        count = int(cursor.rowcount)
        finished = True
        return count
    except sqlite3.ProgrammingError:
        raise
    except sqlite3.IntegrityError:
        raise
    except sqlite3.OperationalError as exc:
        raise TerminalRegistryUnavailable("unreadable", "sqlite write failed", exc) from exc
    except sqlite3.DatabaseError as exc:
        raise TerminalRegistryUnavailable("corrupt", "sqlite write failed", exc) from exc
    finally:
        if cursor is not None:
            # Keep the borrowed connection/transaction untouched.  As with
            # _select, a successful call in an outer except block has no
            # primary exception of its own to which cleanup can be attached.
            primary = None if finished else sys.exc_info()[1]
            try:
                cursor.close()
            except Exception as cleanup:
                if primary is not None:
                    if cleanup_errors is not None:
                        # A caller that can recover the primary error must
                        # still observe this call's actual cleanup failures.
                        cleanup_errors.append(cleanup)
                    _attach_cleanup(primary, [cleanup])
                elif isinstance(cleanup, sqlite3.ProgrammingError):
                    raise
                elif isinstance(cleanup, sqlite3.Error):
                    reason = "unreadable" if isinstance(
                        cleanup, sqlite3.OperationalError) else "corrupt"
                    raise TerminalRegistryUnavailable(
                        reason, "sqlite write cursor close failed", cleanup) from cleanup
                else:
                    raise


def _require_conn(conn: object) -> sqlite3.Connection:
    if not issubclass(type(conn), sqlite3.Connection):
        raise TerminalRegistryUnavailable("connection_contract", "conn must be native sqlite3.Connection")
    if not conn.in_transaction:
        raise TerminalRegistryUnavailable("connection_contract", "caller transaction is required")
    if conn.row_factory is not sqlite3.Row:
        raise TerminalRegistryUnavailable("connection_contract", "row_factory must be sqlite3.Row")
    return conn


def _require_one_mutation(
    count: int, *, operation: str, cas_satisfied: bool | None = None
) -> None:
    """Require a real one-row write, distinguishing a proven stale CAS.

    ``cas_satisfied`` describes a same-connection recheck after a zero-row
    UPDATE.  Missing rows or non-CAS writes provide no stale-CAS evidence.
    Full expected-record readback remains the caller's next step on success.
    """
    if count == 1:
        return
    if count == 0 and cas_satisfied is False:
        raise TerminalRegistryConflict(f"{operation} CAS condition is stale")
    raise TerminalRegistryUnavailable(
        "corrupt", f"{operation} affected {count} rows instead of one"
    )


def _norm_sql(sql: object) -> str:
    """Normalize only formatting and a CREATE object's optional main qualifier.

    Quoted tokens remain byte-for-byte text, including whitespace and doubled
    quote escapes.  Comments are outside the expected DDL and kept verbatim so
    they cannot be normalized into an accepted layout.
    """
    if not isinstance(sql, str):
        return ""
    tokens: list[tuple[str, str]] = []
    pos = 0
    while pos < len(sql):
        start = pos
        char = sql[pos]
        if char in " \t\r\n\f":
            pos += 1
            while pos < len(sql) and sql[pos] in " \t\r\n\f":
                pos += 1
            tokens.append(("space", " "))
        elif char in "'\"`[":
            end_quote = "]" if char == "[" else char
            pos += 1
            closed = False
            while pos < len(sql):
                if sql[pos] == end_quote:
                    pos += 1
                    if char != "[" and pos < len(sql) and sql[pos] == end_quote:
                        pos += 1
                        continue
                    closed = True
                    break
                pos += 1
            if not closed:
                return ""
            tokens.append(("literal" if char == "'" else "quoted", sql[start:pos]))
        elif sql.startswith("--", pos) or sql.startswith("/*", pos):
            # Expected definitions contain no comments.  Do not rewrite their
            # contents or line boundaries; any such definition stays different.
            return sql
        elif char.isalnum() or char in "_$":
            pos += 1
            while pos < len(sql) and (sql[pos].isalnum() or sql[pos] in "_$"):
                pos += 1
            tokens.append(("word", sql[start:pos]))
        else:
            pos += 1
            tokens.append(("punct", char))

    significant = [index for index, (kind, _text) in enumerate(tokens) if kind != "space"]
    head = [tokens[index] for index in significant]
    object_pos = None
    if head[:2] in ([('word', 'CREATE'), ('word', 'TABLE')],
                    [('word', 'CREATE'), ('word', 'TRIGGER')],
                    [('word', 'CREATE'), ('word', 'INDEX')]):
        object_pos = 2
    elif head[:3] == [('word', 'CREATE'), ('word', 'UNIQUE'), ('word', 'INDEX')]:
        object_pos = 3
    if object_pos is not None and len(head) > object_pos + 2:
        kind, schema = head[object_pos]
        if kind == "quoted":
            schema = schema[1:-1]
        ascii_lower = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")
        if (kind in ("word", "quoted") and schema.translate(ascii_lower) == "main"
                and head[object_pos + 1] == ("punct", ".")
                and head[object_pos + 2][0] in ("word", "quoted")):
            # Only this CREATE header qualifier may be removed; ON targets,
            # expressions and every string literal retain their original text.
            del tokens[significant[object_pos]:significant[object_pos + 1] + 1]
    while tokens and tokens[-1][0] == "space":
        tokens.pop()
    if tokens and tokens[-1] == ("punct", ";"):
        tokens.pop()
    return "".join(text for _kind, text in tokens).strip(" \t\r\n\f")


def _scan_objects(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    names = sorted(_EXPECTED_NAMES)
    placeholders = ",".join("?" for _ in names)
    return _select(
        conn,
        "SELECT type,name,tbl_name,sql FROM main.sqlite_master WHERE "
        "tbl_name COLLATE NOCASE IN (?,?) OR name COLLATE NOCASE IN (" + placeholders + ")",
        ("team_task_protocol", "team_source_registry", *names),
    ).fetchall()


def _assert_no_temp_shadow(conn: sqlite3.Connection) -> None:
    names = sorted(_EXPECTED_NAMES)
    placeholders = ",".join("?" for _ in names)
    rows = _select(
        conn,
        "SELECT type,name,tbl_name,sql FROM temp.sqlite_temp_master WHERE "
        "tbl_name COLLATE NOCASE IN (?,?) OR name COLLATE NOCASE IN (" + placeholders + ")",
        ("team_task_protocol", "team_source_registry", *names),
    ).fetchall()
    if rows:
        detail = ", ".join(f"{row['type']}:{row['name']}" for row in rows)
        raise TerminalRegistryUnavailable(
            "schema_mismatch", "temp schema shadows registry objects: " + detail
        )


def _assert_layout(conn: sqlite3.Connection) -> None:
    _assert_no_temp_shadow(conn)
    objects = [
        (str(row["type"]), str(row["name"]), str(row["tbl_name"]), row["sql"])
        for row in _scan_objects(conn)
    ]

    if not objects:
        raise TerminalRegistryUnavailable("not_prepared", "registry objects are absent")
    expected_keys = (
        {("table", name) for name in _EXPECTED_TABLES}
        | {("trigger", name) for name in _EXPECTED_TRIGGERS}
        | {("index", name) for name in _EXPECTED_INDEXES}
    )
    for kind, name, tbl_name, sql in objects:
        if (kind, name) not in expected_keys:
            if (kind == "index" and tbl_name in _EXPECTED_TABLES
                    and name.startswith("sqlite_autoindex_") and sql is None):
                continue
            # Discovery uses SQLite ASCII NOCASE, but the stored layout must
            # still have the exact names/types/owners.  Case variants and
            # cross-type reserved names cannot disappear into a missing-table
            # result, nor may an extra object hide behind a differently cased
            # tbl_name.
            raise TerminalRegistryUnavailable(
                "schema_mismatch", f"unexpected registry object: {kind}:{name}"
            )

    for name, expected_sql in _EXPECTED_TABLES.items():
        matches = [item for item in objects if item[0] == "table" and item[1] == name]
        if not matches:
            raise TerminalRegistryUnavailable("schema_mismatch", f"missing table in partial layout: {name}")
        if (
            len(matches) != 1
            or matches[0][2] != name
            or _norm_sql(matches[0][3]) != _norm_sql(expected_sql)
        ):
            raise TerminalRegistryUnavailable("schema_mismatch", f"table layout differs: {name}")

    for name, (table, expected_sql) in _EXPECTED_TRIGGERS.items():
        matches = [item for item in objects if item[0] == "trigger" and item[1] == name]
        if (
            len(matches) != 1
            or matches[0][2] != table
            or _norm_sql(matches[0][3]) != _norm_sql(expected_sql)
        ):
            raise TerminalRegistryUnavailable("schema_mismatch", f"trigger layout differs: {name}")

    for name, (table, expected_sql) in _EXPECTED_INDEXES.items():
        matches = [item for item in objects if item[0] == "index" and item[1] == name]
        if (
            len(matches) != 1
            or matches[0][2] != table
            or _norm_sql(matches[0][3]) != _norm_sql(expected_sql)
        ):
            raise TerminalRegistryUnavailable("schema_mismatch", f"index layout differs: {name}")

    for kind, name, tbl_name, sql in objects:
        if kind == "trigger" and tbl_name in _EXPECTED_TABLES and name not in _EXPECTED_TRIGGERS:
            raise TerminalRegistryUnavailable("schema_mismatch", f"extra trigger: {name}")
        if kind == "index" and tbl_name in _EXPECTED_TABLES and name not in _EXPECTED_INDEXES:
            if str(name).startswith("sqlite_autoindex_"):
                if sql is not None:
                    raise TerminalRegistryUnavailable(
                        "schema_mismatch", f"autoindex carries SQL: {name}"
                    )
            else:
                raise TerminalRegistryUnavailable("schema_mismatch", f"extra index: {name}")


def _assert_object_layout(
    conn: sqlite3.Connection,
    *,
    tables: Mapping[str, str],
    triggers: Mapping[str, tuple[str, str]],
    indexes: Mapping[str, tuple[str, str]],
) -> None:
    """Assert the exact schema-object set for the requested registry tables.

    Scoped companion of the full layout contract above, used by the C04
    narrow discovery reads: only the ``main`` objects of the requested
    tables are discovered and verified (exact table SQL, owning
    triggers/indexes, no extra objects, no temp shadow), mirroring the same
    rules the full check applies.  Objects of a non-requested registry
    table are neither required nor rejected here.
    """
    tbl_names = tuple(sorted(tables))
    object_names = tuple(sorted(set(tables) | set(triggers) | set(indexes)))
    tbl_placeholders = ",".join("?" for _ in tbl_names)
    name_placeholders = ",".join("?" for _ in object_names)

    shadow_rows = _select(
        conn,
        "SELECT type,name,tbl_name,sql FROM temp.sqlite_temp_master WHERE "
        "tbl_name COLLATE NOCASE IN (" + tbl_placeholders + ") OR "
        "name COLLATE NOCASE IN (" + name_placeholders + ")",
        (*tbl_names, *object_names),
    ).fetchall()
    if shadow_rows:
        detail = ", ".join(f"{row['type']}:{row['name']}" for row in shadow_rows)
        raise TerminalRegistryUnavailable(
            "schema_mismatch", "temp schema shadows registry objects: " + detail
        )

    objects = [
        (str(row["type"]), str(row["name"]), str(row["tbl_name"]), row["sql"])
        for row in _select(
            conn,
            "SELECT type,name,tbl_name,sql FROM main.sqlite_master WHERE "
            "tbl_name COLLATE NOCASE IN (" + tbl_placeholders + ") OR "
            "name COLLATE NOCASE IN (" + name_placeholders + ")",
            (*tbl_names, *object_names),
        ).fetchall()
    ]

    if not objects:
        raise TerminalRegistryUnavailable("not_prepared", "registry objects are absent")
    expected_keys = (
        {("table", name) for name in tables}
        | {("trigger", name) for name in triggers}
        | {("index", name) for name in indexes}
    )
    for kind, name, tbl_name, sql in objects:
        if (kind, name) not in expected_keys:
            if (kind == "index" and tbl_name in tables
                    and name.startswith("sqlite_autoindex_") and sql is None):
                continue
            # Same discovery/ownership rules as the full check: case
            # variants and cross-type reserved names must not disappear
            # into a missing-object result.
            raise TerminalRegistryUnavailable(
                "schema_mismatch", f"unexpected registry object: {kind}:{name}"
            )

    for name, expected_sql in tables.items():
        matches = [item for item in objects if item[0] == "table" and item[1] == name]
        if not matches:
            raise TerminalRegistryUnavailable("schema_mismatch", f"missing table in partial layout: {name}")
        if (
            len(matches) != 1
            or matches[0][2] != name
            or _norm_sql(matches[0][3]) != _norm_sql(expected_sql)
        ):
            raise TerminalRegistryUnavailable("schema_mismatch", f"table layout differs: {name}")

    for name, (table, expected_sql) in triggers.items():
        matches = [item for item in objects if item[0] == "trigger" and item[1] == name]
        if (
            len(matches) != 1
            or matches[0][2] != table
            or _norm_sql(matches[0][3]) != _norm_sql(expected_sql)
        ):
            raise TerminalRegistryUnavailable("schema_mismatch", f"trigger layout differs: {name}")

    for name, (table, expected_sql) in indexes.items():
        matches = [item for item in objects if item[0] == "index" and item[1] == name]
        if (
            len(matches) != 1
            or matches[0][2] != table
            or _norm_sql(matches[0][3]) != _norm_sql(expected_sql)
        ):
            raise TerminalRegistryUnavailable("schema_mismatch", f"index layout differs: {name}")

    for kind, name, tbl_name, sql in objects:
        if kind == "trigger" and tbl_name in tables and name not in triggers:
            raise TerminalRegistryUnavailable("schema_mismatch", f"extra trigger: {name}")
        if kind == "index" and tbl_name in tables and name not in indexes:
            if str(name).startswith("sqlite_autoindex_"):
                if sql is not None:
                    raise TerminalRegistryUnavailable(
                        "schema_mismatch", f"autoindex carries SQL: {name}"
                    )
            else:
                raise TerminalRegistryUnavailable("schema_mismatch", f"extra index: {name}")


def _assert_protocol_layout(conn: sqlite3.Connection) -> None:
    """Assert the protocol-side schema objects a discovery header reads."""
    _assert_object_layout(
        conn,
        tables=_PROTOCOL_SCOPE_TABLES,
        triggers=_PROTOCOL_SCOPE_TRIGGERS,
        indexes={},
    )


def _assert_source_layout(conn: sqlite3.Connection) -> None:
    """Assert the source-registry schema objects a discovery iterator reads."""
    _assert_object_layout(
        conn,
        tables=_SOURCE_SCOPE_TABLES,
        triggers=_SOURCE_SCOPE_TRIGGERS,
        indexes=_SOURCE_SCOPE_INDEXES,
    )


def _require_ready_on(conn: object) -> sqlite3.Connection:
    native = _require_conn(conn)
    _assert_layout(native)
    return native


def _present_expected(conn: sqlite3.Connection) -> set[str]:
    names = sorted(_EXPECTED_NAMES)
    placeholders = ",".join("?" for _ in names)
    rows = _select(
        conn,
        f"SELECT name FROM main.sqlite_master WHERE name COLLATE NOCASE IN ({placeholders})",
        tuple(names),
    ).fetchall()
    # Retain original names, including ASCII case variants discovered by
    # NOCASE, so prepare rejects a partial/different layout instead of creating.
    return {str(row["name"]) for row in rows}


def _attach_cleanup(primary: BaseException, cleanup: list[BaseException]) -> None:
    for exc in cleanup:
        primary.add_note(f"cleanup failure: {type(exc).__name__}: {exc}")


def _cleanup_after_failure(conn: sqlite3.Connection) -> list[BaseException]:
    failures: list[BaseException] = []
    try:
        conn.rollback()
    except Exception as exc:  # noqa: BLE001 - cleanup failures must stay observable
        failures.append(exc)
    try:
        conn.close()
    except Exception as exc:  # noqa: BLE001 - cleanup failures must stay observable
        failures.append(exc)
    return failures


def _commit_owned(conn: sqlite3.Connection) -> None:
    try:
        conn.commit()
    except sqlite3.ProgrammingError:
        raise
    except sqlite3.Error as exc:
        raise TerminalRegistryUnavailable("unreadable", "registry commit failed", exc) from exc


# ---------------------------------------------------------------------------
# Record validation
# ---------------------------------------------------------------------------


def _validate_sources_payload(sources: object) -> list[dict[str, Any]]:
    if type(sources) is not list:
        raise _payload("evidence.sources must be a list")
    normalized: list[dict[str, Any]] = []
    seen_registration: set[str] = set()
    seen_generation: set[tuple[int, int]] = set()
    for item in sources:
        if not isinstance(item, Mapping) or set(item.keys()) != _EVIDENCE_SOURCE_KEYS:
            raise _payload("evidence source has an invalid key set")
        uid = _int_in_range(item["source_user_id"], "source_user_id", 1, MAX_INT64)
        registration_id = _strict_text(item["registration_id"], "registration_id")
        generation = _int_in_range(item["generation"], "generation", 1, MAX_INT64)
        library_id = _hex32(item["source_library_id"], "source_library_id")
        if registration_id in seen_registration:
            raise _payload("duplicate evidence registration_id")
        if (uid, generation) in seen_generation:
            raise _payload("duplicate evidence source_user_id/generation")
        seen_registration.add(registration_id)
        seen_generation.add((uid, generation))
        normalized.append(
            {
                "source_user_id": uid,
                "registration_id": registration_id,
                "generation": generation,
                "source_library_id": library_id,
            }
        )
    ordered = sorted(normalized, key=lambda item: (item["source_user_id"], item["generation"]))
    if normalized != ordered:
        raise _payload("evidence.sources must be sorted by source_user_id/generation")
    return normalized


def _normalize_caller_evidence(evidence: object) -> dict[str, Any]:
    if not isinstance(evidence, Mapping) or set(evidence.keys()) != _EVIDENCE_CALLER_KEYS:
        raise _payload("adoption_evidence has an invalid key set")
    sources = _validate_sources_payload(evidence["sources"])
    scope = _strict_text(evidence["verification_scope"], "verification_scope")
    method = _strict_text(evidence["verification_method"], "verification_method")
    conflicts = _strict_text(evidence["conflict_handling"], "conflict_handling")
    if evidence["prior_history_unknown"] is not True:
        raise _payload("prior_history_unknown must be true")
    return {
        "sources": sources,
        "verification_scope": scope,
        "verification_method": method,
        "conflict_handling": conflicts,
        "prior_history_unknown": True,
    }


def _compose_adoption_evidence(
    protocol: Mapping[str, Any],
    normalized: Mapping[str, Any],
    forward_start: str,
) -> dict[str, Any]:
    return {
        "schema": EVIDENCE_SCHEMA,
        "task_instance_id": protocol["task_instance_id"],
        "task_id": protocol["task_id"],
        "owner_user_id": protocol["owner_user_id"],
        "owner_library_id": protocol["owner_library_id"],
        "sources": normalized["sources"],
        "verification_scope": normalized["verification_scope"],
        "verification_method": normalized["verification_method"],
        "conflict_handling": normalized["conflict_handling"],
        "prior_history_unknown": True,
        "forward_coverage_start": forward_start,
    }


def _check_evidence_source_bindings(
    conn: sqlite3.Connection,
    protocol: Mapping[str, Any],
    sources: list[dict[str, Any]],
) -> None:
    for item in sources:
        row = _select(
            conn,
            "SELECT registration_id, task_instance_id, source_user_id, "
            "source_library_id, generation, status FROM main.team_source_registry "
            "WHERE registration_id = ?",
            (item["registration_id"],),
        ).fetchone()
        if row is None:
            raise TerminalRegistryUnavailable(
                "corrupt", "adoption evidence references a missing source registration"
            )
        if str(row["task_instance_id"]) != protocol["task_instance_id"]:
            raise TerminalRegistryUnavailable(
                "corrupt", "adoption evidence references an orphan source"
            )
        if (
            int(row["source_user_id"]) != item["source_user_id"]
            or str(row["source_library_id"]) != item["source_library_id"]
            or int(row["generation"]) != item["generation"]
        ):
            raise TerminalRegistryUnavailable(
                "corrupt", "adoption evidence source binding differs"
            )
        if str(row["status"]) not in _SOURCE_STATUSES:
            raise TerminalRegistryUnavailable(
                "corrupt", "adoption evidence source has an invalid status"
            )


def _validate_stored_adoption(
    conn: sqlite3.Connection,
    record: Mapping[str, Any],
) -> None:
    adopted_at = record["adopted_at"]
    evidence_ref = record["adoption_evidence_ref"]
    evidence_json = record["adoption_evidence_json"]
    evidence_sha = record["adoption_evidence_sha256"]
    try:
        _timestamp(adopted_at, "adopted_at")
        _strict_text(evidence_ref, "adoption_evidence_ref")
        _strict_text(evidence_json, "adoption_evidence_json")
        if (
            type(evidence_sha) is not str
            or len(evidence_sha) != 64
            or any(ch not in "0123456789abcdef" for ch in evidence_sha)
        ):
            raise _payload("adoption_evidence_sha256 must be 64 lowercase hexadecimal characters")
        parsed = json.loads(evidence_json)
        if not isinstance(parsed, dict) or set(parsed.keys()) != _EVIDENCE_TOP_KEYS:
            raise _payload("stored adoption evidence has an invalid key set")
        if _canonical_json(parsed) != evidence_json:
            raise _payload("stored adoption evidence is not canonical JSON")
        if _sha256_text(evidence_json) != evidence_sha:
            raise _payload("stored adoption evidence hash differs")
        if parsed["schema"] != EVIDENCE_SCHEMA:
            raise _payload("stored adoption evidence schema differs")
        for field in ("task_instance_id", "task_id", "owner_user_id", "owner_library_id"):
            if parsed[field] != record[field]:
                raise _payload(f"stored adoption evidence binding differs: {field}")
        if parsed["prior_history_unknown"] is not True:
            raise _payload("stored adoption evidence must retain prior_history_unknown=true")
        if parsed["forward_coverage_start"] != adopted_at:
            raise _payload("stored adoption forward_coverage_start differs from adopted_at")
        _strict_text(parsed["verification_scope"], "verification_scope")
        _strict_text(parsed["verification_method"], "verification_method")
        _strict_text(parsed["conflict_handling"], "conflict_handling")
        sources = _validate_sources_payload(parsed["sources"])
    except TerminalRegistryPayloadInvalid as exc:
        raise TerminalRegistryUnavailable("corrupt", "stored adoption evidence is corrupt", exc) from exc
    except (json.JSONDecodeError, UnicodeEncodeError) as exc:
        raise TerminalRegistryUnavailable("corrupt", "stored adoption evidence is corrupt", exc) from exc
    _check_evidence_source_bindings(conn, record, sources)


def _validate_evidence_document(record: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the stored adoption evidence document without database access.

    Performs the document-side checks of the stored-evidence contract
    (canonical JSON, hash, schema, binding fields, prior_history_unknown,
    forward_coverage_start, strict verification texts, sorted sources
    payload, adopted_at not preceding the protocol row's created_at) but,
    unlike ``_validate_stored_adoption``, never consults the source
    registry, so a narrow discovery read can classify coverage damage
    without denying identity over unrelated bad source rows.  Evidence
    binding integers are strictly type-checked (``bool``/``float`` cannot
    impersonate ``int``) and range-checked before any value comparison.
    Returns the parsed document.  Raises ``TerminalRegistryPayloadInvalid``
    on document-side violations; JSON decode/encode errors propagate to the
    caller.
    """
    adopted_at = record["adopted_at"]
    evidence_ref = record["adoption_evidence_ref"]
    evidence_json = record["adoption_evidence_json"]
    evidence_sha = record["adoption_evidence_sha256"]
    _timestamp(adopted_at, "adopted_at")
    _strict_text(evidence_ref, "adoption_evidence_ref")
    _strict_text(evidence_json, "adoption_evidence_json")
    if (
        type(evidence_sha) is not str
        or len(evidence_sha) != 64
        or any(ch not in "0123456789abcdef" for ch in evidence_sha)
    ):
        raise _payload("adoption_evidence_sha256 must be 64 lowercase hexadecimal characters")
    parsed = json.loads(evidence_json)
    if not isinstance(parsed, dict) or set(parsed.keys()) != _EVIDENCE_TOP_KEYS:
        raise _payload("stored adoption evidence has an invalid key set")
    if _canonical_json(parsed) != evidence_json:
        raise _payload("stored adoption evidence is not canonical JSON")
    if _sha256_text(evidence_json) != evidence_sha:
        raise _payload("stored adoption evidence hash differs")
    if parsed["schema"] != EVIDENCE_SCHEMA:
        raise _payload("stored adoption evidence schema differs")
    evidence_owner_user_id = parsed["owner_user_id"]
    if type(evidence_owner_user_id) is not int:
        raise _payload("evidence owner_user_id must be a strict integer")
    if evidence_owner_user_id < 1 or evidence_owner_user_id > MAX_INT64:
        raise _payload("evidence owner_user_id is outside the SQLite INTEGER range")
    for field in ("task_instance_id", "task_id", "owner_user_id", "owner_library_id"):
        if parsed[field] != record[field]:
            raise _payload(f"stored adoption evidence binding differs: {field}")
    if parsed["prior_history_unknown"] is not True:
        raise _payload("stored adoption evidence must retain prior_history_unknown=true")
    if parsed["forward_coverage_start"] != adopted_at:
        raise _payload("stored adoption forward_coverage_start differs from adopted_at")
    _strict_text(parsed["verification_scope"], "verification_scope")
    _strict_text(parsed["verification_method"], "verification_method")
    _strict_text(parsed["conflict_handling"], "conflict_handling")
    _validate_sources_payload(parsed["sources"])
    created_at = _timestamp(record["created_at"], "created_at")
    if adopted_at < created_at:
        raise _payload("adopted_at precedes protocol created_at")
    return parsed


def _validate_protocol_record(conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    record = _record(row, _PROTOCOL_COLUMNS, "protocol")
    try:
        instance = _strict_text(record["task_instance_id"], "task_instance_id")
        if instance != record["task_instance_id"]:
            raise _payload("task_instance_id is not canonical")
        task_id = _host_task_id(record["task_id"])
        if task_id != record["task_id"]:
            raise _payload("task_id is not canonical")
        _int_in_range(record["owner_user_id"], "owner_user_id", 1, MAX_INT64)
        _hex32(record["owner_library_id"], "owner_library_id")
        _int_in_range(record["protocol_version"], "protocol_version", 1, MAX_INT64)
        if record["protocol_version"] != PROTOCOL_VERSION:
            raise _payload("unsupported protocol_version")
        state = record["coverage_state"]
        if state not in _COVERAGE_STATES:
            raise _payload("invalid coverage_state")
        _int_in_range(record["roster_revision"], "roster_revision", 0, MAX_INT64)
        created = _timestamp(record["created_at"], "created_at")
        updated = _timestamp(record["updated_at"], "updated_at")
        if updated < created:
            raise _payload("updated_at precedes created_at")
        evidence_fields = (
            record["adopted_at"],
            record["adoption_evidence_ref"],
            record["adoption_evidence_json"],
            record["adoption_evidence_sha256"],
        )
        if state == "legacy_verified":
            if any(value is None for value in evidence_fields):
                raise _payload("legacy_verified row is missing adoption evidence")
            _validate_stored_adoption(conn, record)
            if record["adopted_at"] < created:
                raise _payload("adopted_at precedes created_at")
        else:
            if any(value is not None for value in evidence_fields):
                raise _payload("non-verified row carries adoption evidence")
    except TerminalRegistryPayloadInvalid as exc:
        raise TerminalRegistryUnavailable("corrupt", "protocol row is corrupt", exc) from exc
    return record


def _validate_source_record(row: sqlite3.Row) -> dict[str, Any]:
    record = _record(row, _SOURCE_COLUMNS, "source")
    try:
        registration = _strict_text(record["registration_id"], "registration_id")
        if registration != record["registration_id"]:
            raise _payload("registration_id is not canonical")
        instance = _strict_text(record["task_instance_id"], "task_instance_id")
        if instance != record["task_instance_id"]:
            raise _payload("task_instance_id is not canonical")
        _int_in_range(record["source_user_id"], "source_user_id", 1, MAX_INT64)
        _hex32(record["source_library_id"], "source_library_id")
        _strict_text(record["source_path_identity"], "source_path_identity")
        _int_in_range(record["generation"], "generation", 1, MAX_INT64)
        status = record["status"]
        if status not in _SOURCE_STATUSES:
            raise _payload("invalid source status")
        _timestamp(record["registered_at"], "registered_at")
        if status == "active":
            if (
                record["retired_at"] is not None
                or record["retired_reason"] is not None
                or record["archive_watermark"] is not None
            ):
                raise _payload("active source carries retirement fields")
        else:
            if record["retired_at"] is None or record["retired_reason"] is None:
                raise _payload("retired source is missing retirement fields")
            _timestamp(record["retired_at"], "retired_at")
            _strict_text(record["retired_reason"], "retired_reason")
            if record["archive_watermark"] is not None:
                _strict_text(record["archive_watermark"], "archive_watermark")
    except TerminalRegistryPayloadInvalid as exc:
        raise TerminalRegistryUnavailable("corrupt", "source row is corrupt", exc) from exc
    return record


def _protocol_exists(conn: sqlite3.Connection, task_instance_id: str) -> bool:
    row = _select(
        conn,
        "SELECT 1 FROM main.team_task_protocol WHERE task_instance_id = ?",
        (task_instance_id,),
    ).fetchone()
    return row is not None


def _get_protocol_record(
    conn: sqlite3.Connection, task_instance_id: str
) -> dict[str, Any] | None:
    row = _select(
        conn,
        "SELECT * FROM main.team_task_protocol WHERE task_instance_id = ?",
        (task_instance_id,),
    ).fetchone()
    if row is None:
        return None
    return _validate_protocol_record(conn, row)


def _get_source_record(
    conn: sqlite3.Connection, registration_id: str
) -> dict[str, Any] | None:
    row = _select(
        conn,
        "SELECT * FROM main.team_source_registry WHERE registration_id = ?",
        (registration_id,),
    ).fetchone()
    if row is None:
        return None
    return _validate_source_record(row)


def _readback_protocol(
    conn: sqlite3.Connection, expected: Mapping[str, Any]
) -> dict[str, Any]:
    row = _select(
        conn,
        "SELECT * FROM main.team_task_protocol WHERE task_instance_id = ?",
        (expected["task_instance_id"],),
    ).fetchone()
    if row is None:
        raise TerminalRegistryUnavailable("corrupt", "protocol row missing after write")
    record = _validate_protocol_record(conn, row)
    for name in _PROTOCOL_COLUMNS:
        if record[name] != expected[name]:
            raise TerminalRegistryUnavailable(
                "corrupt", f"protocol post-write readback differs: {name}"
            )
    return record


def _readback_source(conn: sqlite3.Connection, expected: Mapping[str, Any]) -> dict[str, Any]:
    row = _select(
        conn,
        "SELECT * FROM main.team_source_registry WHERE registration_id = ?",
        (expected["registration_id"],),
    ).fetchone()
    if row is None:
        raise TerminalRegistryUnavailable("corrupt", "source row missing after write")
    record = _validate_source_record(row)
    for name in _SOURCE_COLUMNS:
        if record[name] != expected[name]:
            raise TerminalRegistryUnavailable(
                "corrupt", f"source post-write readback differs: {name}"
            )
    return record


def _validate_initial_source_coverage(
    conn: sqlite3.Connection,
    protocol: Mapping[str, Any],
    sources: list[dict[str, Any]],
) -> None:
    active_rows = _select(
        conn,
        "SELECT * FROM main.team_source_registry "
        "WHERE task_instance_id = ? AND status = 'active'",
        (protocol["task_instance_id"],),
    ).fetchall()
    active: dict[str, dict[str, Any]] = {}
    for row in active_rows:
        record = _validate_source_record(row)
        active[record["registration_id"]] = record

    if not sources:
        if active:
            raise TerminalRegistryStateError(
                "evidence.sources is empty while active sources exist"
            )
        return

    evidence_ids = {item["registration_id"] for item in sources}
    for item in sources:
        row = _get_source_record(conn, item["registration_id"])
        if row is None:
            raise TerminalRegistryStateError(
                "evidence references an unregistered source"
            )
        if row["task_instance_id"] != protocol["task_instance_id"]:
            raise TerminalRegistryUnavailable(
                "corrupt", "evidence references an orphan source"
            )
        if (
            row["source_user_id"] != item["source_user_id"]
            or row["source_library_id"] != item["source_library_id"]
            or row["generation"] != item["generation"]
        ):
            raise TerminalRegistryStateError(
                "evidence source identity does not match the registry"
            )
    missing_active = sorted(set(active) - evidence_ids)
    if missing_active:
        raise TerminalRegistryStateError(
            "evidence does not cover all active sources: " + ", ".join(missing_active)
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def prepare_team_terminal_registry(db_path: str | Path) -> None:
    """Create/validate the exact two-table registry layout on an existing DB file.

    This is the only self-owned connection/DDL path.  A missing file is never
    created; a partial or differing layout is never repaired.
    """

    path = Path(db_path)
    if not path.is_file():
        raise FileNotFoundError(str(path))
    try:
        uri = path.resolve().as_uri() + "?mode=rw"
        conn = sqlite3.connect(uri, uri=True, timeout=15)
    except sqlite3.ProgrammingError:
        raise
    except sqlite3.Error as exc:
        raise TerminalRegistryUnavailable("unreadable", "cannot open registry database", exc) from exc
    conn.row_factory = sqlite3.Row
    primary: BaseException | None = None
    try:
        _execute(conn, "PRAGMA busy_timeout=15000")
        _execute(conn, "PRAGMA foreign_keys=ON")
        _execute(conn, "BEGIN IMMEDIATE")
        _assert_no_temp_shadow(conn)
        present = _present_expected(conn)
        if not present:
            for ddl in _ALL_DDL:
                _execute(conn, ddl)
        elif present != set(_EXPECTED_NAMES):
            raise TerminalRegistryUnavailable("schema_mismatch", "registry layout is partial")
        _assert_layout(conn)
        _commit_owned(conn)
    except Exception as exc:  # noqa: BLE001 - primary failure is re-raised below
        primary = exc
        _attach_cleanup(exc, _cleanup_after_failure(conn))
        raise
    finally:
        if primary is None:
            try:
                conn.close()
            except sqlite3.ProgrammingError as exc:
                exc.add_note("registry prepared and committed; close raised ProgrammingError")
                raise
            except Exception as exc:  # noqa: BLE001 - close failure is observable and committed
                raise TerminalRegistryUnavailable(
                    "unreadable", "registry committed; close failed", exc
                ) from exc


def read_protocol_for_task_on(
    conn: sqlite3.Connection, *, task_id: str
) -> dict[str, Any] | None:
    native = _require_ready_on(conn)
    canonical_task_id = _host_task_id(task_id)
    row = _select(
        native,
        "SELECT * FROM main.team_task_protocol WHERE task_id = ?",
        (canonical_task_id,),
    ).fetchone()
    if row is None:
        return None
    return _validate_protocol_record(native, row)


def read_protocol_on(
    conn: sqlite3.Connection, *, task_instance_id: str
) -> dict[str, Any] | None:
    native = _require_ready_on(conn)
    instance = _strict_text(task_instance_id, "task_instance_id")
    return _get_protocol_record(native, instance)


def coverage_status_on(
    conn: sqlite3.Connection, *, task_instance_id: str
) -> Literal["new_covered", "legacy_forward_only", "pending_verification", "unknown"]:
    native = _require_ready_on(conn)
    instance = _strict_text(task_instance_id, "task_instance_id")
    record = _get_protocol_record(native, instance)
    if record is None:
        return "unknown"
    state = record["coverage_state"]
    if state == "new_covered":
        return "new_covered"
    if state == "legacy_verified":
        return "legacy_forward_only"
    if state == "legacy_pending_verification":
        return "pending_verification"
    raise TerminalRegistryUnavailable("corrupt", "protocol coverage_state is invalid")


def register_protocol_on(
    conn: sqlite3.Connection,
    *,
    task_id: str,
    task_instance_id: str,
    owner_user_id: int,
    owner_library_id: str,
    creation_path: Literal["trusted_new_task", "legacy_adoption"],
    roster_revision: int = 0,
) -> dict[str, Any]:
    native = _require_ready_on(conn)
    canonical_task_id = _host_task_id(task_id)
    instance = _strict_text(task_instance_id, "task_instance_id")
    uid = _int_in_range(owner_user_id, "owner_user_id", 1, MAX_INT64)
    library = _hex32(owner_library_id, "owner_library_id")
    revision = _int_in_range(roster_revision, "roster_revision", 0, MAX_INT64)
    if revision != 0:
        raise _payload("initial roster_revision must be 0")
    if type(creation_path) is not str:
        raise _payload("creation_path must be text")
    if creation_path == "trusted_new_task":
        state = "new_covered"
    elif creation_path == "legacy_adoption":
        state = "legacy_pending_verification"
    else:
        raise _payload("creation_path must be trusted_new_task or legacy_adoption")

    existing = _get_protocol_record(native, instance)
    if existing is not None:
        if (
            existing["task_id"] == canonical_task_id
            and existing["owner_user_id"] == uid
            and existing["owner_library_id"] == library
            and existing["coverage_state"] == state
            and existing["roster_revision"] == revision
        ):
            return existing
        raise TerminalRegistryConflict("task_instance_id is already bound differently")
    by_task = _select(
        native,
        "SELECT task_instance_id FROM main.team_task_protocol WHERE task_id = ?",
        (canonical_task_id,),
    ).fetchone()
    if by_task is not None:
        raise TerminalRegistryConflict("task_id is already bound to another instance")

    now = _now_utc()
    expected = {
        "task_instance_id": instance,
        "task_id": canonical_task_id,
        "owner_user_id": uid,
        "owner_library_id": library,
        "protocol_version": PROTOCOL_VERSION,
        "coverage_state": state,
        "roster_revision": 0,
        "adopted_at": None,
        "adoption_evidence_ref": None,
        "adoption_evidence_json": None,
        "adoption_evidence_sha256": None,
        "created_at": now,
        "updated_at": now,
    }
    try:
        count = _execute(
            native,
            "INSERT INTO main.team_task_protocol "
            "(task_instance_id, task_id, owner_user_id, owner_library_id, "
            " protocol_version, coverage_state, roster_revision, adopted_at, "
            " adoption_evidence_ref, adoption_evidence_json, adoption_evidence_sha256, "
            " created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                expected["task_instance_id"],
                expected["task_id"],
                expected["owner_user_id"],
                expected["owner_library_id"],
                expected["protocol_version"],
                expected["coverage_state"],
                expected["roster_revision"],
                expected["adopted_at"],
                expected["adoption_evidence_ref"],
                expected["adoption_evidence_json"],
                expected["adoption_evidence_sha256"],
                expected["created_at"],
                expected["updated_at"],
            ),
        )
    except sqlite3.IntegrityError as exc:
        if _get_protocol_record(native, instance) is not None:
            raise TerminalRegistryConflict("protocol registration raced with an existing row") from exc
        if _select(
            native,
            "SELECT 1 FROM main.team_task_protocol WHERE task_id = ?",
            (canonical_task_id,),
        ).fetchone() is not None:
            raise TerminalRegistryConflict("task_id is already bound to another instance") from exc
        raise TerminalRegistryConflict("protocol registration was rejected") from exc
    _require_one_mutation(count, operation="protocol registration")
    return _readback_protocol(native, expected)


def mark_legacy_verified_on(
    conn: sqlite3.Connection,
    *,
    task_instance_id: str,
    expected_roster_revision: int,
    adoption_evidence_ref: str,
    adoption_evidence: Mapping[str, Any],
) -> dict[str, Any]:
    native = _require_ready_on(conn)
    instance = _strict_text(task_instance_id, "task_instance_id")
    expected_revision = _int_in_range(
        expected_roster_revision, "expected_roster_revision", 0, MAX_INT64
    )
    evidence_ref = _strict_text(adoption_evidence_ref, "adoption_evidence_ref")
    normalized = _normalize_caller_evidence(adoption_evidence)

    record = _get_protocol_record(native, instance)
    if record is None:
        raise TerminalRegistryStateError("protocol row is not registered")
    state = record["coverage_state"]
    if state == "new_covered":
        raise TerminalRegistryStateError("new_covered is not a legacy adoption target")
    if state == "legacy_verified":
        if record["roster_revision"] != expected_revision:
            raise TerminalRegistryConflict("expected roster revision is stale")
        adopted_at = record["adopted_at"]
        if adopted_at is None:
            raise TerminalRegistryUnavailable("corrupt", "verified row has no adopted_at")
        candidate = _compose_adoption_evidence(record, normalized, adopted_at)
        candidate_json = _canonical_json(candidate)
        candidate_sha = _sha256_text(candidate_json)
        if (
            record["adoption_evidence_ref"] == evidence_ref
            and record["adoption_evidence_json"] == candidate_json
            and record["adoption_evidence_sha256"] == candidate_sha
        ):
            return record
        raise TerminalRegistryConflict("legacy verification evidence differs")
    if state != "legacy_pending_verification":
        raise TerminalRegistryStateError("protocol is not pending legacy verification")
    if record["roster_revision"] != expected_revision:
        raise TerminalRegistryConflict("expected roster revision is stale")

    _validate_initial_source_coverage(native, record, normalized["sources"])
    now = _now_utc()
    candidate = _compose_adoption_evidence(record, normalized, now)
    candidate_json = _canonical_json(candidate)
    candidate_sha = _sha256_text(candidate_json)
    expected = dict(record)
    expected.update(
        {
            "coverage_state": "legacy_verified",
            "adopted_at": now,
            "adoption_evidence_ref": evidence_ref,
            "adoption_evidence_json": candidate_json,
            "adoption_evidence_sha256": candidate_sha,
            "updated_at": now,
        }
    )
    try:
        count = _execute(
            native,
            "UPDATE main.team_task_protocol SET "
            "coverage_state = 'legacy_verified', adopted_at = ?, "
            "adoption_evidence_ref = ?, adoption_evidence_json = ?, "
            "adoption_evidence_sha256 = ?, updated_at = ? "
            "WHERE task_instance_id = ? "
            "AND coverage_state = 'legacy_pending_verification' "
            "AND roster_revision = ?",
            (
                expected["adopted_at"],
                expected["adoption_evidence_ref"],
                expected["adoption_evidence_json"],
                expected["adoption_evidence_sha256"],
                expected["updated_at"],
                instance,
                expected_revision,
            ),
        )
    except sqlite3.IntegrityError as exc:
        raise TerminalRegistryConflict("legacy verification update was rejected") from exc
    cas_satisfied = None
    if count == 0:
        current = _get_protocol_record(native, instance)
        if current is not None:
            cas_satisfied = (
                current["coverage_state"] == "legacy_pending_verification"
                and current["roster_revision"] == expected_revision
            )
    _require_one_mutation(
        count, operation="legacy verification", cas_satisfied=cas_satisfied
    )
    return _readback_protocol(native, expected)


def set_roster_revision_on(
    conn: sqlite3.Connection,
    *,
    task_instance_id: str,
    expected_revision: int,
    new_revision: int | None = None,
) -> dict[str, Any]:
    native = _require_ready_on(conn)
    instance = _strict_text(task_instance_id, "task_instance_id")
    expected = _int_in_range(expected_revision, "expected_revision", 0, MAX_INT64)
    if new_revision is not None:
        requested = _int_in_range(new_revision, "new_revision", 0, MAX_INT64)
        if requested != expected + 1:
            raise _payload("new_revision must equal expected_revision + 1")
    else:
        if expected == MAX_INT64:
            raise TerminalRegistryConflict("roster_revision is at maximum and cannot advance")
        requested = expected + 1

    record = _get_protocol_record(native, instance)
    if record is None:
        raise TerminalRegistryStateError("protocol row is not registered")
    if record["roster_revision"] != expected:
        raise TerminalRegistryConflict("expected roster revision is stale")
    now = _now_utc()
    new_record = dict(record)
    new_record["roster_revision"] = requested
    new_record["updated_at"] = now
    try:
        count = _execute(
            native,
            "UPDATE main.team_task_protocol SET roster_revision = ?, updated_at = ? "
            "WHERE task_instance_id = ? AND roster_revision = ?",
            (requested, now, instance, expected),
        )
    except sqlite3.IntegrityError as exc:
        raise TerminalRegistryConflict("roster revision update was rejected") from exc
    cas_satisfied = None
    if count == 0:
        current = _get_protocol_record(native, instance)
        if current is not None:
            cas_satisfied = current["roster_revision"] == expected
    _require_one_mutation(
        count, operation="roster revision", cas_satisfied=cas_satisfied
    )
    return _readback_protocol(native, new_record)


def register_source_on(
    conn: sqlite3.Connection,
    *,
    task_instance_id: str,
    source_user_id: int,
    source_library_id: str,
    source_path_identity: str,
    registration_id: str | None = None,
) -> dict[str, Any]:
    native = _require_ready_on(conn)
    instance = _strict_text(task_instance_id, "task_instance_id")
    uid = _int_in_range(source_user_id, "source_user_id", 1, MAX_INT64)
    library = _hex32(source_library_id, "source_library_id")
    path_identity = _strict_text(source_path_identity, "source_path_identity")
    if registration_id is not None:
        registration = _strict_text(registration_id, "registration_id")
    else:
        registration = None
    if not _protocol_exists(native, instance):
        raise TerminalRegistryStateError("protocol row is not registered")

    active_rows = _select(
        native,
        "SELECT * FROM main.team_source_registry "
        "WHERE task_instance_id = ? AND source_user_id = ? AND status = 'active'",
        (instance, uid),
    ).fetchall()
    if len(active_rows) > 1:
        raise TerminalRegistryUnavailable("corrupt", "more than one active source for task/uid")
    if active_rows:
        existing = _validate_source_record(active_rows[0])
        if existing["source_library_id"] != library or existing["source_path_identity"] != path_identity:
            raise TerminalRegistryConflict("active source identity differs")
        if registration is not None and registration != existing["registration_id"]:
            raise TerminalRegistryConflict("active source registration_id differs")
        return existing

    if registration is not None:
        if _get_source_record(native, registration) is not None:
            raise TerminalRegistryConflict("registration_id already exists")
    else:
        registration = uuid.uuid4().hex

    max_row = _select(
        native,
        "SELECT MAX(generation) AS max_generation FROM main.team_source_registry "
        "WHERE task_instance_id = ? AND source_user_id = ?",
        (instance, uid),
    ).fetchone()
    max_generation = 0 if max_row is None or max_row["max_generation"] is None else int(
        max_row["max_generation"]
    )
    if max_generation >= MAX_INT64:
        raise TerminalRegistryConflict("source generation is at maximum")
    generation = max_generation + 1
    now = _now_utc()
    expected = {
        "registration_id": registration,
        "task_instance_id": instance,
        "source_user_id": uid,
        "source_library_id": library,
        "source_path_identity": path_identity,
        "generation": generation,
        "status": "active",
        "registered_at": now,
        "retired_at": None,
        "retired_reason": None,
        "archive_watermark": None,
    }
    cursor_cleanup_errors: list[Exception] = []
    try:
        count = _execute(
            native,
            "INSERT INTO main.team_source_registry "
            "(registration_id, task_instance_id, source_user_id, source_library_id, "
            " source_path_identity, generation, status, registered_at, retired_at, "
            " retired_reason, archive_watermark) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                expected["registration_id"],
                expected["task_instance_id"],
                expected["source_user_id"],
                expected["source_library_id"],
                expected["source_path_identity"],
                expected["generation"],
                expected["status"],
                expected["registered_at"],
                expected["retired_at"],
                expected["retired_reason"],
                expected["archive_watermark"],
            ),
            cleanup_errors=cursor_cleanup_errors,
        )
    except sqlite3.IntegrityError as exc:
        raced = _select(
            native,
            "SELECT * FROM main.team_source_registry "
            "WHERE task_instance_id = ? AND source_user_id = ? AND status = 'active'",
            (instance, uid),
        ).fetchone()
        if raced is not None:
            existing = _validate_source_record(raced)
            if (
                existing["source_library_id"] == library
                and existing["source_path_identity"] == path_identity
                and (registration_id is None or existing["registration_id"] == registration_id)
            ):
                if cursor_cleanup_errors:
                    raise TerminalRegistryUnavailable(
                        "unreadable",
                        "source registration matched an existing row, but cursor cleanup failed",
                        exc,
                    ) from exc
                return existing
        raise TerminalRegistryConflict("source registration was rejected") from exc
    _require_one_mutation(count, operation="source registration")
    return _readback_source(native, expected)


def retire_source_on(
    conn: sqlite3.Connection,
    *,
    registration_id: str,
    retired_reason: str,
    archive_watermark: str | None = None,
) -> dict[str, Any]:
    native = _require_ready_on(conn)
    registration = _strict_text(registration_id, "registration_id")
    reason = _strict_text(retired_reason, "retired_reason")
    watermark = None if archive_watermark is None else _strict_text(
        archive_watermark, "archive_watermark"
    )
    record = _get_source_record(native, registration)
    if record is None:
        raise TerminalRegistryStateError("source registration does not exist")
    if not _protocol_exists(native, record["task_instance_id"]):
        raise TerminalRegistryUnavailable("corrupt", "source has no parent protocol")
    if record["status"] == "retired":
        if record["retired_reason"] == reason and record["archive_watermark"] == watermark:
            return record
        raise TerminalRegistryConflict("source retirement differs")
    if record["status"] != "active":
        raise TerminalRegistryUnavailable("corrupt", "source status is invalid")
    now = _now_utc()
    expected = dict(record)
    expected.update(
        {
            "status": "retired",
            "retired_at": now,
            "retired_reason": reason,
            "archive_watermark": watermark,
        }
    )
    try:
        count = _execute(
            native,
            "UPDATE main.team_source_registry SET status = 'retired', retired_at = ?, "
            "retired_reason = ?, archive_watermark = ? "
            "WHERE registration_id = ? AND status = 'active'",
            (now, reason, watermark, registration),
        )
    except sqlite3.IntegrityError as exc:
        raise TerminalRegistryConflict("source retirement was rejected") from exc
    cas_satisfied = None
    if count == 0:
        current = _get_source_record(native, registration)
        if current is not None:
            cas_satisfied = current["status"] == "active"
    _require_one_mutation(
        count, operation="source retirement", cas_satisfied=cas_satisfied
    )
    return _readback_source(native, expected)


def read_source_on(
    conn: sqlite3.Connection, *, registration_id: str
) -> dict[str, Any] | None:
    native = _require_ready_on(conn)
    registration = _strict_text(registration_id, "registration_id")
    record = _get_source_record(native, registration)
    if record is None:
        return None
    if not _protocol_exists(native, record["task_instance_id"]):
        raise TerminalRegistryUnavailable("corrupt", "source has no parent protocol")
    return record


def list_sources_on(
    conn: sqlite3.Connection,
    *,
    task_instance_id: str,
    status: Literal["active", "retired"] | None = None,
) -> list[dict[str, Any]]:
    native = _require_ready_on(conn)
    instance = _strict_text(task_instance_id, "task_instance_id")
    if status is not None:
        if type(status) is not str or status not in _SOURCE_STATUSES:
            raise _payload("status must be active, retired, or None")
    rows = _select(
        native,
        "SELECT * FROM main.team_source_registry WHERE task_instance_id = ? "
        "ORDER BY source_user_id, generation",
        (instance,),
    ).fetchall()
    if rows and not _protocol_exists(native, instance):
        raise TerminalRegistryUnavailable(
            "corrupt", "orphan source rows exist without a parent protocol"
        )
    if not rows and not _protocol_exists(native, instance):
        raise TerminalRegistryStateError("protocol row is not registered")
    records = [_validate_source_record(row) for row in rows]
    if status is not None:
        records = [record for record in records if record["status"] == status]
    return records


def active_source_on(
    conn: sqlite3.Connection,
    *,
    task_instance_id: str,
    source_user_id: int,
) -> dict[str, Any] | None:
    native = _require_ready_on(conn)
    instance = _strict_text(task_instance_id, "task_instance_id")
    uid = _int_in_range(source_user_id, "source_user_id", 1, MAX_INT64)
    row = _select(
        native,
        "SELECT * FROM main.team_source_registry "
        "WHERE task_instance_id = ? AND source_user_id = ? AND status = 'active'",
        (instance, uid),
    ).fetchone()
    if row is not None:
        if not _protocol_exists(native, instance):
            raise TerminalRegistryUnavailable("corrupt", "source has no parent protocol")
        return _validate_source_record(row)
    any_row = _select(
        native,
        "SELECT 1 FROM main.team_source_registry "
        "WHERE task_instance_id = ? AND source_user_id = ? LIMIT 1",
        (instance, uid),
    ).fetchone()
    if any_row is not None and not _protocol_exists(native, instance):
        raise TerminalRegistryUnavailable("corrupt", "source has no parent protocol")
    return None


def source_status_on(
    conn: sqlite3.Connection,
    *,
    task_instance_id: str,
    source_user_id: int,
) -> Literal["active", "retired", "unknown"]:
    native = _require_ready_on(conn)
    instance = _strict_text(task_instance_id, "task_instance_id")
    uid = _int_in_range(source_user_id, "source_user_id", 1, MAX_INT64)
    rows = _select(
        native,
        "SELECT * FROM main.team_source_registry "
        "WHERE task_instance_id = ? AND source_user_id = ?",
        (instance, uid),
    ).fetchall()
    if not rows:
        return "unknown"
    if not _protocol_exists(native, instance):
        raise TerminalRegistryUnavailable("corrupt", "source has no parent protocol")
    records = [_validate_source_record(row) for row in rows]
    if any(record["status"] == "active" for record in records):
        return "active"
    return "retired"


# ---------------------------------------------------------------------------
# Narrow discovery reads (C04)
# ---------------------------------------------------------------------------


def _protocol_discovery_binding(record: Mapping[str, Any]) -> TaskBinding:
    """Build the strictly validated immutable binding of a protocol row.

    Damage to any binding field or to the understood protocol version is a
    typed rejection; coverage-side damage is classified separately by
    ``_coverage_health`` and never denies this identity.
    """
    try:
        instance = _strict_text(record["task_instance_id"], "task_instance_id")
        if instance != record["task_instance_id"]:
            raise _payload("task_instance_id is not canonical")
        task_id = _host_task_id(record["task_id"])
        if task_id != record["task_id"]:
            raise _payload("task_id is not canonical")
        owner_user_id = _int_in_range(record["owner_user_id"], "owner_user_id", 1, MAX_INT64)
        owner_library_id = _hex32(record["owner_library_id"], "owner_library_id")
        protocol_version = _int_in_range(
            record["protocol_version"], "protocol_version", 1, MAX_INT64
        )
        if protocol_version != PROTOCOL_VERSION:
            raise _payload("unsupported protocol_version")
    except TerminalRegistryPayloadInvalid as exc:
        raise TerminalRegistryUnavailable(
            "corrupt", "protocol identity row is corrupt", exc
        ) from exc
    return TaskBinding(
        task_id=task_id,
        task_instance_id=instance,
        owner_user_id=owner_user_id,
        owner_library_id=owner_library_id,
    )


def _coverage_health(
    record: Mapping[str, Any],
) -> tuple[str, Mapping[str, Any] | None, str | None]:
    """Classify coverage-side health of a protocol row.

    Returns ``(coverage_status, coverage_evidence, coverage_issue)``.
    Coverage-state or adoption-document damage is reported through the
    issue channel with status ``unknown`` and evidence ``None``, so it
    never denies the already-proven binding; the evidence checks are
    document-side only and never consult the source registry.
    """
    issues: list[str] = []
    evidence_fields = (
        record["adopted_at"],
        record["adoption_evidence_ref"],
        record["adoption_evidence_json"],
        record["adoption_evidence_sha256"],
    )
    state = record["coverage_state"]
    status: str
    evidence: Mapping[str, Any] | None = None
    if state == "new_covered":
        status = "new_covered"
        if any(value is not None for value in evidence_fields):
            status = "unknown"
            issues.append("new_covered row carries adoption evidence")
    elif state == "legacy_pending_verification":
        status = "pending_verification"
        if any(value is not None for value in evidence_fields):
            status = "unknown"
            issues.append("pending row carries adoption evidence")
    elif state == "legacy_verified":
        if any(value is None for value in evidence_fields):
            status = "unknown"
            issues.append("legacy_verified row is missing adoption evidence")
        else:
            try:
                evidence = _validate_evidence_document(record)
                status = "legacy_forward_only"
            except (
                TerminalRegistryPayloadInvalid,
                json.JSONDecodeError,
                UnicodeEncodeError,
            ) as exc:
                status = "unknown"
                evidence = None
                issues.append(f"stored adoption evidence is corrupt: {exc}")
    else:
        status = "unknown"
        issues.append(f"invalid coverage_state: {state!r}")
    coverage_issue = "; ".join(issues) if issues else None
    return status, evidence, coverage_issue


def _discovery_batch_size(value: object) -> int:
    if type(value) is not int:
        raise _payload("batch_size must be an integer")
    if value < 1:
        raise _payload("batch_size must be a positive integer")
    return value


def _source_discovery_row(row: sqlite3.Row) -> SourceDiscoveryRow:
    """Convert one materialized registry row into a valid-or-issue row."""
    registration: str | None = None
    locating: list[str] = []
    try:
        raw_registration = row["registration_id"]
        if type(raw_registration) is str:
            registration = raw_registration
        locating.append(f"registration_id={raw_registration!r}")
    except (IndexError, KeyError):
        locating.append("registration_id is unavailable")
    try:
        locating.append(f"source_user_id={row['source_user_id']!r}")
        locating.append(f"generation={row['generation']!r}")
    except (IndexError, KeyError):
        locating.append("source coordinates are unavailable")
    try:
        record = _validate_source_record(row)
    except TerminalRegistryUnavailable as exc:
        cause = exc.__cause__
        cause_text = f" [{cause}]" if cause is not None else ""
        return SourceDiscoveryRow(
            registration_id=registration,
            source=None,
            issue=f"{exc}{cause_text} at " + ", ".join(locating),
        )
    return SourceDiscoveryRow(
        registration_id=record["registration_id"],
        source=record,
        issue=None,
    )


def _iter_source_discovery_rows(
    conn: sqlite3.Connection,
    instance: str,
    batch_size: int,
) -> Iterator[SourceDiscoveryRow]:
    """Yield every source generation of one instance page by page.

    Pages advance by the ``(source_user_id, generation)`` keyset in the same
    order the strong list API uses; each page is fully materialized and its
    cursor closed inside ``_select`` before any row of that page is
    yielded, so no cursor is ever suspended across a yield point.  Page or
    read failures raise instead of ending the iteration, keeping the
    "discovery incomplete" signal explicit; bad rows become issue rows
    rather than being skipped.
    """
    started = False
    cursor_source_user_id: object = None
    cursor_generation: object = None
    while True:
        if not started:
            rows = _select(
                conn,
                "SELECT * FROM main.team_source_registry WHERE task_instance_id = ? "
                "ORDER BY source_user_id, generation LIMIT ?",
                (instance, batch_size),
            ).fetchall()
        else:
            rows = _select(
                conn,
                "SELECT * FROM main.team_source_registry "
                "WHERE task_instance_id = ? AND "
                "(source_user_id > ? OR (source_user_id = ? AND generation > ?)) "
                "ORDER BY source_user_id, generation LIMIT ?",
                (instance, cursor_source_user_id, cursor_source_user_id,
                 cursor_generation, batch_size),
            ).fetchall()
        if not rows:
            return
        for row in rows:
            yield _source_discovery_row(row)
        last = rows[-1]
        cursor_source_user_id = last["source_user_id"]
        cursor_generation = last["generation"]
        started = True
        if len(rows) < batch_size:
            return


def read_task_discovery_header_on(
    conn: sqlite3.Connection,
    *,
    task_id: str,
) -> TaskDiscoveryHeader | None:
    """Read the minimal task discovery header for one host task id.

    Validates only the immutable task/instance/owner binding, the protocol
    version, and the protocol-side schema objects this read actually
    touches; it never invokes the strong paths that fully revalidate the
    adoption document against the source registry.  Coverage-side damage is
    reported through ``coverage_status``/``coverage_issue`` instead of
    denying the known binding.  Returns ``None`` when no protocol row is
    bound to ``task_id``.
    """
    native = _require_conn(conn)
    _assert_protocol_layout(native)
    canonical_task_id = _host_task_id(task_id)
    row = _select(
        native,
        "SELECT * FROM main.team_task_protocol WHERE task_id = ?",
        (canonical_task_id,),
    ).fetchone()
    if row is None:
        return None
    record = _record(row, _PROTOCOL_COLUMNS, "protocol")
    binding = _protocol_discovery_binding(record)
    roster_revision: int | None
    revision_issue: str | None = None
    try:
        roster_revision = _int_in_range(
            record["roster_revision"], "roster_revision", 0, MAX_INT64
        )
    except TerminalRegistryPayloadInvalid:
        roster_revision = None
        revision_issue = "roster_revision is not a readable integer"
    coverage_status, coverage_evidence, coverage_issue = _coverage_health(record)
    if revision_issue is not None:
        coverage_issue = (
            revision_issue if coverage_issue is None
            else coverage_issue + "; " + revision_issue
        )
    return TaskDiscoveryHeader(
        binding=binding,
        roster_revision=roster_revision,
        coverage_status=coverage_status,
        coverage_evidence=coverage_evidence,
        coverage_issue=coverage_issue,
    )


def iter_source_discovery_on(
    conn: sqlite3.Connection,
    *,
    task_instance_id: str,
    batch_size: int = 200,
) -> Iterator[SourceDiscoveryRow]:
    """Enumerate every registered source generation of one task instance.

    Covers active and retired rows of all generations.  The connection
    contract and the source-registry schema objects are validated eagerly,
    before the iterator is created.  Each yielded row is valid-or-issue:
    exactly one of ``source``/``issue`` is set, bad rows carry a locating
    issue instead of being skipped, and page or read failures raise so
    enumeration can never silently claim completeness.  Rows are a
    discovery aid only: a selected registration must be re-read with the
    strong ``read_source_on`` before it is relied upon.
    """
    native = _require_conn(conn)
    _assert_source_layout(native)
    instance = _strict_text(task_instance_id, "task_instance_id")
    size = _discovery_batch_size(batch_size)
    return _iter_source_discovery_rows(native, instance, size)
