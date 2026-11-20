# -*- coding: utf-8 -*-
"""F1 terminal completion-fact storage (v1 seals and v2 observations).

This module is the low-level, self-sufficient storage component for the
contract-v2 §9 completion facts.  It intentionally has no dependency on any
other ``coscreen``/``custom_backend`` module and does not read product paths or
databases: callers hand in an existing connection (borrowed-read contract) or
an existing database file for the one explicit ``prepare`` step.

Scope (authoritative: ``terminal-f1-storage-brief-20261116.md``):

* five storage entry points only -- ``prepare_team_terminal_facts``,
  ``append_seal_on``, ``read_seal_on``, ``list_seals_on``,
  ``archive_seals_on``;
* borrowed ``*_on`` functions never open/close connections, never start or end
  transactions, never run DDL/PRAGMA, and never change ``row_factory``;
* ``prepare_team_terminal_facts`` is the only function that owns a connection
  and executes DDL; it opens the existing file read/write, uses one explicit
  ``BEGIN IMMEDIATE`` and never creates a missing database;
* payloads keep the complete completion-time roster, the complete raw ballot
  face (``source_row``) for both kinds, and self-contained hashes.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import sys
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

__all__ = [
    "prepare_team_terminal_facts",
    "append_seal_on",
    "read_seal_on",
    "list_seals_on",
    "archive_seals_on",
    "TerminalFactsPayloadInvalid",
    "TerminalFactsConflict",
    "TerminalFactsUnavailable",
    "canonical_json",
    "sha256",
    "OBSERVATION_PAYLOAD_SCHEMA",
]

PAYLOAD_SCHEMA = "team_terminal_seal.v1"
OBSERVATION_PAYLOAD_SCHEMA = "team_terminal_seal.v2"
SEAL_ID_SCHEMA = "team_terminal_seal_id.v1"
SCHEMA_VERSION = 1

TABLE = "team_terminal_seals"
META_TABLE = "team_terminal_facts_meta"
TRIGGER_NO_UPDATE = "team_terminal_seals_no_update"
TRIGGER_NO_DELETE = "team_terminal_seals_no_delete"

ORIGINS = ("actor", "collab", "actor_archive")
ORIGIN_ACTOR = "actor"
ORIGIN_COLLAB = "collab"
ORIGIN_ARCHIVE = "actor_archive"
STAGES = ("stage1", "stage2")
KINDS = ("unanimous", "adjudicated")
TRIGGERS = ("last_vote", "arbitration", "roster_change")
DECISIONS = ("include", "exclude")
ROLES = ("coder", "reconciler")
OPERATIONS = ("vote_save", "arbitration_finish", "roster_change")

#: Allowed ``TerminalFactsUnavailable.reason`` values.
REASONS = frozenset({
    "not_prepared",
    "schema_mismatch",
    "unreadable",
    "corrupt",
    "connection_contract",
})

_HASH_RE = re.compile(r"[0-9a-f]{64}\Z")
_LIBRARY_ID_RE = re.compile(r"[0-9a-f]{32}\Z")
_TIMESTAMP_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z\Z")

TABLE_DDL = (
    "CREATE TABLE team_terminal_seals ("
    "seal_id TEXT NOT NULL PRIMARY KEY CHECK(length(seal_id)=64),"
    "task_instance_id TEXT NOT NULL CHECK(length(task_instance_id)>=1),"
    "stage TEXT NOT NULL CHECK(stage IN ('stage1','stage2')),"
    "item_key TEXT NOT NULL CHECK(length(item_key)>=1),"
    "kind TEXT NOT NULL CHECK(kind IN ('unanimous','adjudicated')),"
    "final_decision TEXT NOT NULL CHECK(final_decision IN ('include','exclude')),"
    "content_sha256 TEXT NOT NULL CHECK(length(content_sha256)=64),"
    "payload_sha256 TEXT NOT NULL CHECK(length(payload_sha256)=64),"
    "sealed_at TEXT NOT NULL,"
    "archived_at TEXT NULL,"
    "origin TEXT NOT NULL CHECK(origin IN ('actor','collab','actor_archive')),"
    "payload_json TEXT NOT NULL,"
    "UNIQUE(task_instance_id,stage,item_key)"
    ")"
)

META_DDL = (
    "CREATE TABLE team_terminal_facts_meta ("
    "singleton INTEGER NOT NULL PRIMARY KEY CHECK(singleton=1),"
    "schema_version INTEGER NOT NULL CHECK(schema_version=1)"
    ")"
)

TRIGGER_NO_UPDATE_DDL = (
    "CREATE TRIGGER team_terminal_seals_no_update "
    "BEFORE UPDATE ON team_terminal_seals "
    "BEGIN SELECT RAISE(ABORT, 'team terminal seals are append-only'); END"
)

TRIGGER_NO_DELETE_DDL = (
    "CREATE TRIGGER team_terminal_seals_no_delete "
    "BEFORE DELETE ON team_terminal_seals "
    "BEGIN SELECT RAISE(ABORT, 'team terminal seals are append-only'); END"
)

_EXPECTED_TABLE_INFO = (
    ("seal_id", "TEXT", 1, 1),
    ("task_instance_id", "TEXT", 1, 0),
    ("stage", "TEXT", 1, 0),
    ("item_key", "TEXT", 1, 0),
    ("kind", "TEXT", 1, 0),
    ("final_decision", "TEXT", 1, 0),
    ("content_sha256", "TEXT", 1, 0),
    ("payload_sha256", "TEXT", 1, 0),
    ("sealed_at", "TEXT", 1, 0),
    ("archived_at", "TEXT", 0, 0),
    ("origin", "TEXT", 1, 0),
    ("payload_json", "TEXT", 1, 0),
)

_RECORD_COLUMNS = (
    "seal_id",
    "task_instance_id",
    "stage",
    "item_key",
    "kind",
    "final_decision",
    "content_sha256",
    "payload_sha256",
    "sealed_at",
    "archived_at",
    "origin",
    "payload_json",
)

_SELECT_RECORD = "SELECT " + ", ".join(_RECORD_COLUMNS) + " FROM main." + TABLE

_PAYLOAD_KEYS = frozenset({
    "schema", "task", "stage", "item_key", "round_id", "kind", "trigger",
    "final_decision", "roster", "required_uids", "opinions", "arbitration",
    "protocol", "source_request", "sealed_at",
})
_OBSERVATION_PAYLOAD_KEYS = _PAYLOAD_KEYS | frozenset({
    "observation", "source_bindings",
})
_OBSERVATION_KEYS = frozenset({
    "host_operation", "host_stage", "host_item_keys", "observed_by_user_id",
    "basis",
})
_SOURCE_BINDING_KEYS = frozenset({
    "source_user_id", "registration_id", "generation", "source_library_id",
})
_TASK_KEYS = frozenset({
    "task_id", "task_instance_id", "owner_user_id", "owner_username",
    "owner_library_id",
})
_ROSTER_KEYS = frozenset({
    "uid", "username", "role", "is_owner", "screening_enabled",
    "required_screener", "source", "config",
})
_OPINION_KEYS = frozenset({
    "uid", "decision", "exclusion_reason", "notes", "opinion_sha256",
    "library_id", "revision", "source_row", "source_row_sha256",
})
_SOURCE_ROW_KEYS = frozenset({"tags", "screened_at", "updated_at"})
_ARBITRATION_KEYS = frozenset({
    "arbitrator_user_id", "arbitrator_role", "record_id", "attestation_id",
    "attested_at", "resolutions_digest", "result_sha256", "attest_sha256",
    "rationale", "input_proof",
})
_PROOF_ENTRY_KEYS = frozenset({"library_id", "revision"})
_PROTOCOL_KEYS = frozenset({"source_registration_id", "roster_revision"})
_REQUEST_KEYS = frozenset({"operation", "request_id", "request_sha256"})

_OPERATION_BY_TRIGGER = {
    "last_vote": "vote_save",
    "arbitration": "arbitration_finish",
    "roster_change": "roster_change",
}


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class TerminalFactsPayloadInvalid(ValueError):
    """The supplied payload or seal record is structurally invalid."""


class TerminalFactsConflict(RuntimeError):
    """A semantically identical key already holds different content."""


class TerminalFactsUnavailable(RuntimeError):
    """The terminal-facts store is missing, mismatched, unreadable or corrupt."""

    def __init__(self, reason: str, message: str = "") -> None:
        if reason not in REASONS:
            raise ValueError(f"unknown TerminalFactsUnavailable reason: {reason!r}")
        self.reason = reason
        super().__init__(message or reason)


# ---------------------------------------------------------------------------
# Canonical JSON and hashes (self-contained; no private product imports)
# ---------------------------------------------------------------------------

def canonical_json(value: Any) -> str:
    """Canonical JSON used by every hash in this module."""
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def sha256(value: Any) -> str:
    """SHA-256 hex digest of :func:`canonical_json` encoded as UTF-8."""
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _content_sha256(payload: Mapping[str, Any]) -> str:
    content = {key: item for key, item in payload.items() if key != "sealed_at"}
    return sha256(content)


def _seal_id(task_instance_id: str, stage: str, item_key: str,
             content_sha256: str) -> str:
    return sha256({
        "schema": SEAL_ID_SCHEMA,
        "task_instance_id": task_instance_id,
        "stage": stage,
        "item_key": item_key,
        "content_sha256": content_sha256,
    })


# ---------------------------------------------------------------------------
# Scalar validators
# ---------------------------------------------------------------------------

def _bad(message: str) -> None:
    raise TerminalFactsPayloadInvalid(message)


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _bad(f"{name} must be a JSON object")
    return value


def _exact_keys(value: Mapping[str, Any], expected: frozenset[str],
                name: str) -> None:
    actual = set(value.keys())
    if actual != expected:
        missing = [key for key in expected if key not in actual]
        unknown = [key for key in actual if key not in expected]
        _bad(f"{name} fields mismatch (missing={_safe_keys(missing)}, "
             f"unknown={_safe_keys(unknown)})")
    for key in value.keys():
        if not isinstance(key, str):
            _bad(f"{name} object keys must be JSON strings")
        _json_text(key, f"{name} object key")


def _safe_keys(keys: Iterable[Any]) -> list[str]:
    """Deterministic, type-safe rendering of unexpected keys for messages."""
    return sorted(
        (repr(key) for key in keys),
    )


def _json_text(value: str, name: str) -> str:
    """Reject text that cannot be encoded as UTF-8 JSON (e.g. lone surrogate)."""
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        _bad(f"{name} must be UTF-8 encodable text")
    return value


def _required_text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _bad(f"{name} must be a non-empty text value")
    return _json_text(value, name)


def _optional_text(value: Any, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        _bad(f"{name} must be text or null")
    return _json_text(value, name)


def _uid(value: Any, name: str) -> int:
    if type(value) is not int or value <= 0:
        _bad(f"{name} must be a positive integer")
    return value


def _positive_int(value: Any, name: str) -> int:
    if type(value) is not int or value <= 0:
        _bad(f"{name} must be a positive integer")
    return value


def _bool(value: Any, name: str) -> bool:
    if type(value) is not bool:
        _bad(f"{name} must be a boolean")
    return value


def _hash(value: Any, name: str) -> str:
    if not isinstance(value, str) or _HASH_RE.fullmatch(value) is None:
        _bad(f"{name} must be 64 lowercase hex characters")
    return value


def _library_id(value: Any, name: str) -> str:
    if not isinstance(value, str) or _LIBRARY_ID_RE.fullmatch(value) is None:
        _bad(f"{name} must be 32 lowercase hex characters")
    return value


def _timestamp(value: Any, name: str) -> str:
    if not isinstance(value, str) or not _valid_timestamp(value):
        _bad(f"{name} must be an RFC3339 UTC timestamp with millisecond precision")
    return value


def _valid_timestamp(value: Any) -> bool:
    if not isinstance(value, str) or _TIMESTAMP_RE.fullmatch(value) is None:
        return False
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ")
    except ValueError:
        return False
    return True


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(
        timespec="milliseconds").replace("+00:00", "Z")


def _task_id(value: Any) -> str:
    """Host-compatible canonical task id (same range as task_collaboration)."""
    if not isinstance(value, str):
        _bad("task.task_id must be text")
    if value != value.strip() or not 1 <= len(value) <= 200:
        _bad("task.task_id must be a canonical 1 to 200 character task id")
    if any(ch in value for ch in "/\\\x00"):
        _bad("task.task_id must not contain '/', '\\\\' or NUL")
    return _json_text(value, "task.task_id")


def _task_instance_id(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        _bad("task.task_instance_id must be a non-empty canonical text value")
    return _json_text(value, "task.task_instance_id")


def _item_key(value: Any) -> str:
    if not isinstance(value, str) or value == "":
        _bad("item_key must be non-empty text")
    return _json_text(value, "item_key")


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant is not allowed: {value}")


def _loads_strict(value: str) -> Any:
    return json.loads(value, parse_constant=_reject_constant)


# ---------------------------------------------------------------------------
# Payload validation / normalization
# ---------------------------------------------------------------------------

def _validate_payload(payload: Any) -> dict[str, Any]:
    """Validate a strict v1 or current-observation v2 payload."""
    payload = _mapping(payload, "payload")
    if payload.get("schema") == OBSERVATION_PAYLOAD_SCHEMA:
        return _validate_observation_payload(payload)
    _exact_keys(payload, _PAYLOAD_KEYS, "payload")

    if payload["schema"] != PAYLOAD_SCHEMA:
        _bad(f"payload.schema must be {PAYLOAD_SCHEMA!r}")

    task = _mapping(payload["task"], "task")
    _exact_keys(task, _TASK_KEYS, "task")
    task_id = _task_id(task["task_id"])
    task_instance_id = _task_instance_id(task["task_instance_id"])
    owner_user_id = _uid(task["owner_user_id"], "task.owner_user_id")
    owner_username = _required_text(task["owner_username"], "task.owner_username")
    owner_library_id = _library_id(task["owner_library_id"],
                                   "task.owner_library_id")

    stage = payload["stage"]
    if stage not in STAGES:
        _bad("stage must be 'stage1' or 'stage2'")
    item_key = _item_key(payload["item_key"])
    if payload["round_id"] is not None:
        _bad("round_id must be null in v1")
    kind = payload["kind"]
    if kind not in KINDS:
        _bad("kind must be 'unanimous' or 'adjudicated'")
    trigger = payload["trigger"]
    if trigger not in TRIGGERS:
        _bad("trigger must be 'last_vote', 'arbitration' or 'roster_change'")
    final_decision = payload["final_decision"]
    if final_decision not in DECISIONS:
        _bad("final_decision must be 'include' or 'exclude'")
    sealed_at = _timestamp(payload["sealed_at"], "sealed_at")

    roster_raw = payload["roster"]
    if not isinstance(roster_raw, list) or not roster_raw:
        _bad("roster must be a non-empty list")
    roster: list[dict[str, Any]] = []
    seen_uids: set[int] = set()
    for index, raw_entry in enumerate(roster_raw):
        name = f"roster[{index}]"
        entry = _mapping(raw_entry, name)
        _exact_keys(entry, _ROSTER_KEYS, name)
        uid = _uid(entry["uid"], f"{name}.uid")
        if uid in seen_uids:
            _bad(f"{name}.uid is duplicated")
        seen_uids.add(uid)
        username = _required_text(entry["username"], f"{name}.username")
        role = entry["role"]
        if role not in ROLES:
            _bad(f"{name}.role must be 'coder' or 'reconciler'")
        is_owner = _bool(entry["is_owner"], f"{name}.is_owner")
        required_screener = _bool(entry["required_screener"],
                                  f"{name}.required_screener")
        screening_enabled = entry["screening_enabled"]
        if role == "coder":
            if not required_screener:
                _bad(f"{name}: coder must be a required screener")
            if screening_enabled is not None and type(screening_enabled) is not bool:
                _bad(f"{name}.screening_enabled must be a boolean or null")
        else:
            if type(screening_enabled) is not bool:
                _bad(f"{name}.screening_enabled must be a boolean for reconciler")
            if screening_enabled is not required_screener:
                _bad(f"{name}.screening_enabled must equal required_screener")
        source = _required_text(entry["source"], f"{name}.source")
        if entry["config"] != "ok":
            _bad(f"{name}.config must be 'ok'")
        roster.append({
            "uid": uid,
            "username": username,
            "role": role,
            "is_owner": is_owner,
            "screening_enabled": screening_enabled,
            "required_screener": required_screener,
            "source": source,
            "config": "ok",
        })

    roster_uids = [entry["uid"] for entry in roster]
    if roster_uids != sorted(roster_uids):
        _bad("roster must be sorted by uid ascending")
    owners = [entry for entry in roster if entry["is_owner"]]
    if len(owners) != 1:
        _bad("roster must contain exactly one owner entry")
    if owners[0]["uid"] != owner_user_id:
        _bad("roster owner uid must equal task.owner_user_id")

    required_raw = payload["required_uids"]
    if not isinstance(required_raw, list) or not required_raw:
        _bad("required_uids must be a non-empty list")
    required_uids = [_uid(value, "required_uids[]") for value in required_raw]
    if required_uids != sorted(required_uids):
        _bad("required_uids must be sorted ascending")
    if len(set(required_uids)) != len(required_uids):
        _bad("required_uids must be unique")
    expected_required = [entry["uid"] for entry in roster
                         if entry["required_screener"]]
    if required_uids != expected_required:
        _bad("required_uids must equal the required_screener roster uids")

    opinions_raw = payload["opinions"]
    if not isinstance(opinions_raw, list):
        _bad("opinions must be a list")
    opinions: list[dict[str, Any]] = []
    seen_opinion_uids: set[int] = set()
    for index, raw_entry in enumerate(opinions_raw):
        name = f"opinions[{index}]"
        entry = _mapping(raw_entry, name)
        _exact_keys(entry, _OPINION_KEYS, name)
        uid = _uid(entry["uid"], f"{name}.uid")
        if uid in seen_opinion_uids:
            _bad(f"{name}.uid is duplicated")
        seen_opinion_uids.add(uid)
        decision = entry["decision"]
        if decision not in DECISIONS:
            _bad(f"{name}.decision must be 'include' or 'exclude'")
        exclusion_reason = entry["exclusion_reason"]
        if not isinstance(exclusion_reason, str):
            _bad(f"{name}.exclusion_reason must be text")
        exclusion_reason = _json_text(exclusion_reason,
                                      f"{name}.exclusion_reason")
        notes = entry["notes"]
        if not isinstance(notes, str):
            _bad(f"{name}.notes must be text")
        notes = _json_text(notes, f"{name}.notes")
        opinion_sha256 = _hash(entry["opinion_sha256"], f"{name}.opinion_sha256")
        library_id = _library_id(entry["library_id"], f"{name}.library_id")
        revision = _positive_int(entry["revision"], f"{name}.revision")
        source_row = _mapping(entry["source_row"], f"{name}.source_row")
        _exact_keys(source_row, _SOURCE_ROW_KEYS, f"{name}.source_row")
        tags = source_row["tags"]
        if not isinstance(tags, str):
            _bad(f"{name}.source_row.tags must be text")
        tags = _json_text(tags, f"{name}.source_row.tags")
        screened_at = _optional_text(source_row["screened_at"],
                                     f"{name}.source_row.screened_at")
        updated_at = _optional_text(source_row["updated_at"],
                                    f"{name}.source_row.updated_at")
        source_row = {
            "tags": tags,
            "screened_at": screened_at,
            "updated_at": updated_at,
        }
        source_row_sha256 = _hash(entry["source_row_sha256"],
                                  f"{name}.source_row_sha256")
        expected_opinion_sha = sha256({
            "decision": decision.strip().lower(),
            "exclusion_reason": exclusion_reason,
            "notes": notes,
        })
        if opinion_sha256 != expected_opinion_sha:
            _bad(f"{name}.opinion_sha256 does not match its content")
        if source_row_sha256 != sha256(source_row):
            _bad(f"{name}.source_row_sha256 does not match source_row")
        opinions.append({
            "uid": uid,
            "decision": decision,
            "exclusion_reason": exclusion_reason,
            "notes": notes,
            "opinion_sha256": opinion_sha256,
            "library_id": library_id,
            "revision": revision,
            "source_row": source_row,
            "source_row_sha256": source_row_sha256,
        })

    if [entry["uid"] for entry in opinions] != sorted(seen_opinion_uids):
        _bad("opinions must be sorted by uid ascending")
    if [entry["uid"] for entry in opinions] != required_uids:
        _bad("opinions must cover exactly required_uids in uid order")

    arbitration_raw = payload["arbitration"]
    if kind == "unanimous":
        if trigger not in ("last_vote", "roster_change"):
            _bad("unanimous seals require trigger 'last_vote' or 'roster_change'")
        if arbitration_raw is not None:
            _bad("unanimous seals must not carry arbitration")
        for entry in opinions:
            if entry["decision"] != final_decision:
                _bad("unanimous opinions must all equal final_decision")
        arbitration = None
    else:
        if trigger != "arbitration":
            _bad("adjudicated seals require trigger 'arbitration'")
        if len(required_uids) < 2:
            _bad("adjudicated seals require at least two required screeners")
        if {entry["decision"] for entry in opinions} != set(DECISIONS):
            _bad("adjudicated seals require both include and exclude opinions")
        arbitration = _validate_arbitration(
            arbitration_raw, item_key=item_key, roster=roster,
            required_uids=required_uids, opinions=opinions,
            final_decision=final_decision, sealed_at=sealed_at,
        )

    protocol = _mapping(payload["protocol"], "protocol")
    _exact_keys(protocol, _PROTOCOL_KEYS, "protocol")
    source_registration_id = _optional_text(
        protocol["source_registration_id"], "protocol.source_registration_id")
    roster_revision = protocol["roster_revision"]
    if roster_revision is not None and (
            type(roster_revision) is not int or roster_revision < 0):
        _bad("protocol.roster_revision must be a non-negative integer or null")

    source_request = _mapping(payload["source_request"], "source_request")
    _exact_keys(source_request, _REQUEST_KEYS, "source_request")
    operation = source_request["operation"]
    if operation not in OPERATIONS:
        _bad("source_request.operation is not supported")
    if operation != _OPERATION_BY_TRIGGER[trigger]:
        _bad("source_request.operation must match the seal trigger")
    request_id = _optional_text(source_request["request_id"],
                                "source_request.request_id")
    request_sha256 = source_request["request_sha256"]
    if request_sha256 is not None:
        request_sha256 = _hash(request_sha256, "source_request.request_sha256")
    if operation == "roster_change":
        if request_id == "":
            _bad("source_request.request_id must be null or non-empty text")
    else:
        if not isinstance(request_id, str) or not request_id.strip():
            _bad("source_request.request_id is required for this operation")
        if request_sha256 is None:
            _bad("source_request.request_sha256 is required for this operation")

    return {
        "schema": PAYLOAD_SCHEMA,
        "task": {
            "task_id": task_id,
            "task_instance_id": task_instance_id,
            "owner_user_id": owner_user_id,
            "owner_username": owner_username,
            "owner_library_id": owner_library_id,
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
        "arbitration": arbitration,
        "protocol": {
            "source_registration_id": source_registration_id,
            "roster_revision": roster_revision,
        },
        "source_request": {
            "operation": operation,
            "request_id": request_id,
            "request_sha256": request_sha256,
        },
        "sealed_at": sealed_at,
    }


def _validate_observation_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Validate v2 metadata while reusing the unchanged v1 ballot contract.

    Caller guarantees observer legitimacy (roster membership is not checked here).
    host_item_keys must be per-item valid, unique, and include payload.item_key.
    """
    _exact_keys(payload, _OBSERVATION_PAYLOAD_KEYS, "payload")
    if payload["schema"] != OBSERVATION_PAYLOAD_SCHEMA:
        _bad(f"payload.schema must be {OBSERVATION_PAYLOAD_SCHEMA!r}")
    if payload["kind"] != "unanimous":
        _bad("current_observation seals must be unanimous")
    if payload["trigger"] != "current_observation":
        _bad("v2 payload trigger must be 'current_observation'")
    if payload["arbitration"] is not None:
        _bad("current_observation seals must not carry arbitration")

    observation_raw = _mapping(payload["observation"], "observation")
    _exact_keys(observation_raw, _OBSERVATION_KEYS, "observation")
    host_operation = _required_text(
        observation_raw["host_operation"], "observation.host_operation")
    host_stage = observation_raw["host_stage"]
    if host_stage is not None and host_stage not in ("screen", "fulltext", "coding"):
        _bad("observation.host_stage must be screen, fulltext, coding or null")
    host_item_keys_raw = observation_raw["host_item_keys"]
    if not isinstance(host_item_keys_raw, list) or not host_item_keys_raw:
        _bad("observation.host_item_keys must be a non-empty list")
    host_item_keys: list[str] = []
    seen_host_keys: set[str] = set()
    for index, raw_key in enumerate(host_item_keys_raw):
        key = _item_key(raw_key)
        if "\x00" in key:
            _bad(f"observation.host_item_keys[{index}] must not contain NUL")
        if key in seen_host_keys:
            _bad("observation.host_item_keys must contain unique canonical keys")
        seen_host_keys.add(key)
        host_item_keys.append(key)
    item_key = _item_key(payload["item_key"])
    if item_key not in seen_host_keys:
        _bad("observation.host_item_keys must contain payload.item_key")
    observed_by_user_id = _uid(
        observation_raw["observed_by_user_id"],
        "observation.observed_by_user_id")
    if observation_raw["basis"] != "current_snapshot":
        _bad("observation.basis must be 'current_snapshot'")

    source_request_raw = _mapping(payload["source_request"], "source_request")
    if source_request_raw.get("operation") != "completion_observe":
        _bad("v2 source_request.operation must be 'completion_observe'")
    projected = dict(payload)
    projected["schema"] = PAYLOAD_SCHEMA
    projected["trigger"] = "last_vote"
    projected.pop("source_bindings")
    projected.pop("observation")
    projected_request = dict(source_request_raw)
    projected_request["operation"] = "vote_save"
    projected["source_request"] = projected_request
    normalized = _validate_payload(projected)

    source_bindings_raw = payload["source_bindings"]
    if not isinstance(source_bindings_raw, list) or not source_bindings_raw:
        _bad("source_bindings must be a non-empty list")
    source_bindings: list[dict[str, Any]] = []
    binding_by_uid: dict[int, dict[str, Any]] = {}
    uid_by_registration_id: dict[str, int] = {}
    previous_uid = 0
    for index, raw_binding in enumerate(source_bindings_raw):
        name = f"source_bindings[{index}]"
        binding = _mapping(raw_binding, name)
        _exact_keys(binding, _SOURCE_BINDING_KEYS, name)
        uid = _uid(binding["source_user_id"], f"{name}.source_user_id")
        if uid <= previous_uid:
            _bad("source_bindings must be sorted by source_user_id without duplicates")
        previous_uid = uid
        registration_id = _required_text(
            binding["registration_id"], f"{name}.registration_id")
        generation = binding["generation"]
        if type(generation) is not int or not 1 <= generation <= 9223372036854775807:
            _bad(f"{name}.generation must be a positive int64")
        source_library_id = _library_id(
            binding["source_library_id"], f"{name}.source_library_id")
        normalized_binding = {
            "source_user_id": uid,
            "registration_id": registration_id,
            "generation": generation,
            "source_library_id": source_library_id,
        }
        if "\x00" in registration_id:
            _bad(f"{name}.registration_id (uid {uid}) must not contain NUL: "
                 f"{registration_id!r}")
        if registration_id in uid_by_registration_id:
            first_uid = uid_by_registration_id[registration_id]
            _bad(f"{name}.registration_id is duplicated across bindings: "
                 f"{registration_id!r} already used by uid {first_uid} (current uid {uid})")
        uid_by_registration_id[registration_id] = uid
        source_bindings.append(normalized_binding)
        binding_by_uid[uid] = normalized_binding

    expected_binding_uids = set(normalized["required_uids"]) | {observed_by_user_id}
    if set(binding_by_uid) != expected_binding_uids:
        _bad("source_bindings must cover required_uids and observed_by_user_id")
    for opinion in normalized["opinions"]:
        binding = binding_by_uid[opinion["uid"]]
        if opinion["library_id"] != binding["source_library_id"]:
            _bad("opinion library_id must match its source binding")
    observer_binding = binding_by_uid[observed_by_user_id]
    if normalized["protocol"]["source_registration_id"] != observer_binding[
            "registration_id"]:
        _bad("protocol.source_registration_id must match the observer binding")

    observation = {
        "host_operation": host_operation,
        "host_stage": host_stage,
        "host_item_keys": host_item_keys,
        "observed_by_user_id": observed_by_user_id,
        "basis": "current_snapshot",
    }
    normalized["schema"] = OBSERVATION_PAYLOAD_SCHEMA
    normalized["trigger"] = "current_observation"
    normalized["protocol"]["source_registration_id"] = observer_binding[
        "registration_id"]
    normalized["source_request"]["operation"] = "completion_observe"
    normalized["observation"] = observation
    normalized["source_bindings"] = source_bindings
    return normalized


def _validate_arbitration(raw: Any, *, item_key: str,
                          roster: list[dict[str, Any]],
                          required_uids: list[int],
                          opinions: list[dict[str, Any]],
                          final_decision: str,
                          sealed_at: str) -> dict[str, Any]:
    arbitration = _mapping(raw, "arbitration")
    _exact_keys(arbitration, _ARBITRATION_KEYS, "arbitration")
    arbitrator_user_id = _uid(arbitration["arbitrator_user_id"],
                              "arbitration.arbitrator_user_id")
    if arbitration["arbitrator_role"] != "reconciler":
        _bad("arbitration.arbitrator_role must be 'reconciler'")
    record_id = _positive_int(arbitration["record_id"], "arbitration.record_id")
    attestation_id = _positive_int(arbitration["attestation_id"],
                                   "arbitration.attestation_id")
    attested_at = _timestamp(arbitration["attested_at"],
                             "arbitration.attested_at")
    if attested_at != sealed_at:
        _bad("sealed_at must equal arbitration.attested_at")
    resolutions_digest = _hash(arbitration["resolutions_digest"],
                               "arbitration.resolutions_digest")
    result_sha256 = _hash(arbitration["result_sha256"],
                          "arbitration.result_sha256")
    attest_sha256 = _hash(arbitration["attest_sha256"],
                          "arbitration.attest_sha256")
    rationale = arbitration["rationale"]
    if not isinstance(rationale, str):
        _bad("arbitration.rationale must be text")
    rationale = _json_text(rationale, "arbitration.rationale")
    if final_decision == "exclude" and not rationale.strip():
        _bad("arbitration.rationale must be non-empty for an exclude decision")

    proof = _mapping(arbitration["input_proof"], "arbitration.input_proof")
    if set(proof.keys()) != {item_key}:
        _bad("arbitration.input_proof must contain exactly this item_key")
    entries = _mapping(proof[item_key], "arbitration.input_proof[item_key]")
    expected_uids = {str(uid) for uid in required_uids}
    if set(entries.keys()) != expected_uids:
        _bad("arbitration.input_proof uids must equal required_uids")
    roster_by_uid = {entry["uid"]: entry for entry in roster}
    arbitrator_entry = roster_by_uid.get(arbitrator_user_id)
    if arbitrator_entry is None:
        _bad("arbitration.arbitrator_user_id must appear in the roster")
    if arbitrator_entry["role"] != "reconciler":
        _bad("arbitration.arbitrator_user_id must have role 'reconciler'")
    by_uid = {entry["uid"]: entry for entry in opinions}
    proof_out: dict[str, dict[str, Any]] = {}
    for uid in required_uids:
        name = f"arbitration.input_proof[{item_key}][{uid}]"
        entry = _mapping(entries[str(uid)], name)
        _exact_keys(entry, _PROOF_ENTRY_KEYS, name)
        library_id = _library_id(entry["library_id"], f"{name}.library_id")
        revision = _positive_int(entry["revision"], f"{name}.revision")
        opinion = by_uid[uid]
        if library_id != opinion["library_id"] or revision != opinion["revision"]:
            _bad(f"{name} must match the opinion library_id/revision")
        proof_out[str(uid)] = {"library_id": library_id, "revision": revision}

    return {
        "arbitrator_user_id": arbitrator_user_id,
        "arbitrator_role": "reconciler",
        "record_id": record_id,
        "attestation_id": attestation_id,
        "attested_at": attested_at,
        "resolutions_digest": resolutions_digest,
        "result_sha256": result_sha256,
        "attest_sha256": attest_sha256,
        "rationale": rationale,
        "input_proof": {item_key: proof_out},
    }


def _enforce_origin(origin: str, payload: Mapping[str, Any]) -> None:
    kind, trigger = payload["kind"], payload["trigger"]
    is_observation = (
        payload["schema"] == OBSERVATION_PAYLOAD_SCHEMA
        and kind == "unanimous"
        and trigger == "current_observation"
    )
    if origin == ORIGIN_ACTOR:
        if not is_observation and (kind != "unanimous" or trigger != "last_vote"):
            _bad("origin 'actor' only accepts unanimous/last_vote or v2 observation seals")
        return
    if origin == ORIGIN_COLLAB:
        allowed = (
            is_observation
            or (kind == "adjudicated" and trigger == "arbitration")
            or (kind == "unanimous" and trigger == "roster_change")
        )
        if not allowed:
            _bad("origin 'collab' only accepts adjudicated/arbitration, "
                 "unanimous/roster_change, or v2 observation seals")
        return
    _bad(f"origin must be 'actor' or 'collab', not {origin!r}")


# ---------------------------------------------------------------------------
# Schema helpers
# ---------------------------------------------------------------------------

def _norm_sql(sql: Any) -> str:
    return " ".join((sql or "").split())


def _schema_mismatch(message: str) -> None:
    raise TerminalFactsUnavailable("schema_mismatch", message)


def _not_prepared(message: str = "terminal facts table is not prepared") -> None:
    raise TerminalFactsUnavailable("not_prepared", message)


def _corrupt(message: str) -> None:
    raise TerminalFactsUnavailable("corrupt", message)


_SQLITE_CORRUPT = 11
_SQLITE_NOTADB = 26
# This exact DDL yields precisely these two SQLite-owned autoindexes on the
# seal table: _1 for the TEXT PRIMARY KEY (seal_id), _2 for the UNIQUE
# semantic key (task_instance_id, stage, item_key).  The marker table has a
# singleton INTEGER PRIMARY KEY and therefore no autoindex at all.
_EXPECTED_SEAL_AUTOINDEXES = frozenset({
    "sqlite_autoindex_" + TABLE + "_1",
    "sqlite_autoindex_" + TABLE + "_2",
})


def _storage_unavailable(exc: sqlite3.Error, *, api: str) -> TerminalFactsUnavailable:
    """Map a real SQLite storage failure to the contract's typed reasons.

    The primary result code (low 8 bits of ``sqlite_errorcode``) decides:
    SQLITE_CORRUPT / SQLITE_NOTADB are corruption, every other genuine storage
    failure defaults to unreadable.  The original exception is preserved as
    ``__cause__`` by the caller's ``raise ... from exc``.
    """
    code = getattr(exc, "sqlite_errorcode", None)
    primary = code & 0xFF if isinstance(code, int) else None
    reason = "corrupt" if primary in (_SQLITE_CORRUPT, _SQLITE_NOTADB) else "unreadable"
    return TerminalFactsUnavailable(
        reason, f"{api} requires storage that raised {type(exc).__name__}: {exc}")


def _execute(conn: sqlite3.Connection, sql: str, params: Iterable[Any] = (), *,
             api: str, fetch: Literal["one", "all"]):
    """Execute and fully fetch a borrowed read within one error boundary."""
    if fetch not in ("one", "all"):
        raise ValueError("fetch must be 'one' or 'all'")
    cursor = None
    fetched = False
    try:
        cursor = conn.execute(sql, params)
        result = cursor.fetchone() if fetch == "one" else cursor.fetchall()
        fetched = True
        return result
    except sqlite3.ProgrammingError:
        raise
    except sqlite3.Error as exc:
        raise _storage_unavailable(exc, api=api) from exc
    finally:
        if cursor is not None:
            # We own this cursor, not its caller-owned connection.  A retained
            # fetch traceback must not keep its statement active after return.
            # Inspect the pending error without catching BaseException; normal
            # unwinding (including SystemExit) must keep its original exception.
            # A successful call inside somebody else's except block has no
            # primary of its own, even though sys.exc_info() sees that caller.
            primary = None if fetched else sys.exc_info()[1]
            try:
                cursor.close()
            except Exception as cleanup:
                if primary is not None:
                    primary.cleanup_errors = (
                        *getattr(primary, "cleanup_errors", ()), cleanup)
                elif isinstance(cleanup, sqlite3.ProgrammingError):
                    raise
                elif isinstance(cleanup, sqlite3.Error):
                    raise _storage_unavailable(
                        cleanup, api=f"{api} cursor close") from cleanup
                else:
                    raise


def _execute_write(conn: sqlite3.Connection, sql: str,
                   params: Iterable[Any] = (), *, api: str):
    """Run a borrowed INSERT; IntegrityError stays available for collision proof."""
    try:
        return conn.execute(sql, params)
    except (sqlite3.IntegrityError, sqlite3.ProgrammingError):
        raise
    except sqlite3.Error as exc:
        raise _storage_unavailable(exc, api=api) from exc


def _objects(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Inspect main objects and reject relevant connection-private TEMP objects.

    Reserved names must be checked independently of table ownership: a trigger
    or index on another table can occupy one of our names in a partial layout.
    Unrelated TEMP objects are deliberately outside this gate.
    """
    reserved_names = (
        TABLE, META_TABLE, TRIGGER_NO_UPDATE, TRIGGER_NO_DELETE,
        *sorted(_EXPECTED_SEAL_AUTOINDEXES),
    )
    predicate = (
        " WHERE tbl_name COLLATE NOCASE IN (?,?) OR name COLLATE NOCASE IN ("
        + ",".join("?" for _ in reserved_names) + ")"
    )
    params = (TABLE, META_TABLE, *reserved_names)
    rows = _execute(
        conn,
        "SELECT name,type,tbl_name,sql FROM main.sqlite_master" + predicate,
        params, api="terminal facts schema", fetch="all",
    )
    temp_rows = _execute(
        conn,
        "SELECT name,type,tbl_name,sql FROM temp.sqlite_master" + predicate,
        params, api="terminal facts TEMP schema", fetch="all",
    )
    if temp_rows:
        _schema_mismatch("terminal facts schema has related TEMP objects")
    return rows


def _require_prepared(conn: sqlite3.Connection) -> None:
    """Borrowed-schema gate: SELECT-only, no PRAGMA, no migration.

    The exact object set is enforced, not just the expected names: extra
    triggers (including a RAISE(IGNORE) INSERT suppressor) or extra user
    indexes make the layout a mismatch instead of a silently unsafe success.
    """
    rows = _objects(conn)
    if not rows:
        _not_prepared()
    expected_tables = {TABLE: TABLE_DDL, META_TABLE: META_DDL}
    expected_triggers = {
        TRIGGER_NO_UPDATE: TRIGGER_NO_UPDATE_DDL,
        TRIGGER_NO_DELETE: TRIGGER_NO_DELETE_DDL,
    }
    # SQLite trigger names have a separate namespace from tables/indexes.
    # Reject duplicates before constructing a name map; no row may disappear
    # merely because a different object type has the same reserved name.
    names = [row["name"] for row in rows]
    if len(set(names)) != len(names):
        _schema_mismatch("terminal facts schema has duplicate object names")
    by_name = {row["name"]: row for row in rows}
    for name, ddl in expected_tables.items():
        row = by_name.get(name)
        if row is None:
            _schema_mismatch(f"terminal facts schema is missing {name}")
        if (row["type"] != "table" or row["tbl_name"] != name
                or _norm_sql(row["sql"]) != _norm_sql(ddl)):
            _schema_mismatch(f"terminal facts schema mismatch for {name}")
    for name, ddl in expected_triggers.items():
        row = by_name.get(name)
        if row is None:
            _schema_mismatch(f"terminal facts schema is missing {name}")
        if (row["type"] != "trigger" or row["tbl_name"] != TABLE
                or _norm_sql(row["sql"]) != _norm_sql(ddl)):
            _schema_mismatch(f"terminal facts schema mismatch for {name}")
    seal_indexes: set[str] = set()
    for row in rows:
        name, kind, owner = row["name"], row["type"], row["tbl_name"]
        if kind == "table":
            if name not in expected_tables:
                _schema_mismatch(
                    f"terminal facts schema has unexpected table {name!r}")
        elif kind == "trigger":
            if name not in expected_triggers or owner != TABLE:
                _schema_mismatch(
                    f"terminal facts schema has unexpected trigger {name!r}")
        elif kind == "index":
            if (owner == TABLE and name in _EXPECTED_SEAL_AUTOINDEXES
                    and row["sql"] is None):
                seal_indexes.add(name)
            else:
                _schema_mismatch(
                    f"terminal facts schema has unexpected index {name!r}")
        else:
            _schema_mismatch(
                f"terminal facts schema has unexpected {kind} {name!r}")
    if seal_indexes != _EXPECTED_SEAL_AUTOINDEXES:
        _schema_mismatch(
            "terminal facts automatic index layout does not match the schema")
    marker = _execute(
        conn, "SELECT singleton,schema_version FROM main." + META_TABLE,
        api="terminal facts marker", fetch="all")
    if [tuple(row) for row in marker] != [(1, SCHEMA_VERSION)]:
        _schema_mismatch("terminal facts marker is missing or inconsistent")


def _verify_prepared(conn: sqlite3.Connection) -> None:
    """Full prepare-time verification (PRAGMA is allowed only here)."""
    _require_prepared(conn)
    info = conn.execute(f"PRAGMA main.table_info({TABLE})").fetchall()
    actual = [
        (row["name"], (row["type"] or "").upper(), int(row["notnull"]),
         int(row["pk"]))
        for row in info
    ]
    if actual != list(_EXPECTED_TABLE_INFO):
        _schema_mismatch("terminal facts table columns do not match the schema")
    indexes = conn.execute(f"PRAGMA main.index_list({TABLE})").fetchall()
    if len(indexes) != len(_EXPECTED_SEAL_AUTOINDEXES):
        _schema_mismatch(
            "terminal facts automatic index count does not match the schema")
    if {row["name"] for row in indexes} != _EXPECTED_SEAL_AUTOINDEXES:
        _schema_mismatch(
            "terminal facts automatic index names do not match the schema")
    pk_indexes = [row for row in indexes if row["origin"] == "pk"]
    unique_indexes = [row for row in indexes if row["origin"] == "u"]
    if len(pk_indexes) != 1 or len(unique_indexes) != 1:
        _schema_mismatch(
            "terminal facts primary/unique index layout does not match the schema")
    pk_columns = [
        row["name"]
        for row in conn.execute(
            f"PRAGMA main.index_info({pk_indexes[0]['name']})").fetchall()
    ]
    if pk_columns != ["seal_id"]:
        _schema_mismatch("terminal facts primary key index is not (seal_id)")
    unique_columns = [
        row["name"]
        for row in conn.execute(
            f"PRAGMA main.index_info({unique_indexes[0]['name']})").fetchall()
    ]
    if unique_columns != ["task_instance_id", "stage", "item_key"]:
        _schema_mismatch(
            "terminal facts unique index is not (task_instance_id,stage,item_key)")


# ---------------------------------------------------------------------------
# Borrowed-connection contract
# ---------------------------------------------------------------------------

def _require_on_conn(conn: sqlite3.Connection, *, api: str) -> None:
    if not isinstance(conn, sqlite3.Connection):
        raise TerminalFactsUnavailable(
            "connection_contract",
            f"{api} requires an existing sqlite3.Connection")
    # A closed native connection raises sqlite3.ProgrammingError here and that
    # native exception is intentionally propagated unchanged.
    if not conn.in_transaction:
        raise TerminalFactsUnavailable(
            "connection_contract",
            f"{api} requires an active caller transaction")
    if conn.row_factory is not sqlite3.Row:
        raise TerminalFactsUnavailable(
            "connection_contract",
            f"{api} requires the caller connection to use sqlite3.Row")


# ---------------------------------------------------------------------------
# Record construction and validation
# ---------------------------------------------------------------------------

def _row_values(row: sqlite3.Row) -> dict[str, Any]:
    try:
        return {name: row[name] for name in _RECORD_COLUMNS}
    except (IndexError, KeyError, TypeError) as exc:
        raise TerminalFactsUnavailable(
            "corrupt", "stored terminal facts row is missing columns") from exc


def _record_from_row(row: sqlite3.Row) -> dict[str, Any]:
    raw = _row_values(row)
    for name in (
        "seal_id", "task_instance_id", "stage", "item_key", "kind",
        "final_decision", "content_sha256", "payload_sha256", "sealed_at",
        "origin", "payload_json",
    ):
        if not isinstance(raw[name], str):
            _corrupt(f"stored {name} must be text")
    if raw["archived_at"] is not None and not isinstance(raw["archived_at"], str):
        _corrupt("stored archived_at must be text or null")

    try:
        payload = _validate_payload(_loads_strict(raw["payload_json"]))
    except (ValueError, TypeError) as exc:
        raise TerminalFactsUnavailable(
            "corrupt", "stored payload JSON is invalid") from exc

    if canonical_json(payload) != raw["payload_json"]:
        _corrupt("stored payload_json is not canonical")
    if raw["content_sha256"] != _content_sha256(payload):
        _corrupt("stored content_sha256 does not match the payload")
    if raw["payload_sha256"] != _sha256_text(raw["payload_json"]):
        _corrupt("stored payload_sha256 does not match payload_json")
    task_instance_id = payload["task"]["task_instance_id"]
    if raw["seal_id"] != _seal_id(task_instance_id, payload["stage"],
                                  payload["item_key"],
                                  raw["content_sha256"]):
        _corrupt("stored seal_id does not match its semantic key and content")
    if raw["task_instance_id"] != task_instance_id:
        _corrupt("stored task_instance_id does not match the payload")
    if raw["stage"] != payload["stage"]:
        _corrupt("stored stage does not match the payload")
    if raw["item_key"] != payload["item_key"]:
        _corrupt("stored item_key does not match the payload")
    if raw["kind"] != payload["kind"]:
        _corrupt("stored kind does not match the payload")
    if raw["final_decision"] != payload["final_decision"]:
        _corrupt("stored final_decision does not match the payload")
    if raw["sealed_at"] != payload["sealed_at"]:
        _corrupt("stored sealed_at does not match the payload")
    if not _valid_timestamp(raw["sealed_at"]):
        _corrupt("stored sealed_at is not a valid UTC timestamp")

    origin = raw["origin"]
    archived_at = raw["archived_at"]
    if origin not in ORIGINS:
        _corrupt("stored origin is not supported")
    is_observation = (
        payload["schema"] == OBSERVATION_PAYLOAD_SCHEMA
        and payload["kind"] == "unanimous"
        and payload["trigger"] == "current_observation"
    )
    if origin == ORIGIN_ACTOR:
        if (not is_observation
                and (payload["kind"] != "unanimous"
                     or payload["trigger"] != "last_vote")):
            _corrupt("origin 'actor' only permits unanimous/last_vote or v2 observation")
        if archived_at is not None:
            _corrupt("origin 'actor' must not be archived")
    elif origin == ORIGIN_COLLAB:
        allowed = (
            is_observation
            or (payload["kind"] == "adjudicated"
                and payload["trigger"] == "arbitration")
            or (payload["kind"] == "unanimous"
                and payload["trigger"] == "roster_change")
        )
        if not allowed:
            _corrupt("origin 'collab' payload kind/trigger is inconsistent")
        if archived_at is not None:
            _corrupt("origin 'collab' must not carry archived_at")
    else:
        if (not is_observation
                and (payload["kind"] != "unanimous"
                     or payload["trigger"] != "last_vote")):
            _corrupt("origin 'actor_archive' only permits unanimous/last_vote or v2 observation")
        if archived_at is None or not _valid_timestamp(archived_at):
            _corrupt("origin 'actor_archive' requires a valid archived_at")

    return {
        "payload": payload,
        "seal_id": raw["seal_id"],
        "content_sha256": raw["content_sha256"],
        "payload_sha256": raw["payload_sha256"],
        "origin": origin,
        "sealed_at": raw["sealed_at"],
        "archived_at": archived_at,
    }


def _build_record(payload: dict[str, Any], *, seal_id: str,
                  content_sha256: str, payload_sha256: str, origin: str,
                  archived_at: str | None) -> dict[str, Any]:
    return {
        "payload": payload,
        "seal_id": seal_id,
        "content_sha256": content_sha256,
        "payload_sha256": payload_sha256,
        "origin": origin,
        "sealed_at": payload["sealed_at"],
        "archived_at": archived_at,
    }


def _select_by_key(conn: sqlite3.Connection, key: tuple[str, str, str],
                   *, api: str = "terminal facts read"):
    return _execute(
        conn,
        _SELECT_RECORD + " WHERE task_instance_id=? AND stage=? AND item_key=?",
        key, api=api, fetch="one",
    )


def _select_by_seal_id(conn: sqlite3.Connection, seal_id: str,
                       *, api: str = "terminal facts read"):
    return _execute(
        conn, _SELECT_RECORD + " WHERE seal_id=?", (seal_id,), api=api,
        fetch="one",
    )


def _insert_record(conn: sqlite3.Connection, *, seal_id: str,
                   task_instance_id: str, stage: str, item_key: str,
                   kind: str, final_decision: str, content_sha256: str,
                   payload_sha256: str, sealed_at: str,
                   archived_at: str | None, origin: str,
                   payload_json: str, api: str = "terminal facts insert") -> None:
    _execute_write(
        conn,
        "INSERT INTO main." + TABLE + " ("
        "seal_id,task_instance_id,stage,item_key,kind,final_decision,"
        "content_sha256,payload_sha256,sealed_at,archived_at,origin,payload_json"
        ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (seal_id, task_instance_id, stage, item_key, kind, final_decision,
         content_sha256, payload_sha256, sealed_at, archived_at, origin,
         payload_json),
        api=api,
    )


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------

def prepare_team_terminal_facts(db_path: Any) -> None:
    """Create/verify the terminal-facts schema in an existing database file.

    This is the only function in the module that owns a connection and runs
    DDL/PRAGMA.  It never creates a missing database, never drops or patches a
    partially existing layout, and is idempotent only for the complete schema.
    """
    try:
        path = Path(db_path)
    except TypeError as exc:
        raise TerminalFactsUnavailable(
            "unreadable", "db_path must be a filesystem path") from exc
    try:
        exists = path.exists()
        is_file = path.is_file() if exists else False
    except OSError as exc:
        raise TerminalFactsUnavailable(
            "unreadable", "database path cannot be inspected") from exc
    if not exists:
        raise FileNotFoundError(
            f"terminal facts prepare requires an existing database file: {path}")
    if not is_file:
        raise TerminalFactsUnavailable(
            "unreadable",
            "terminal facts prepare requires a regular database file")

    conn: sqlite3.Connection | None = None
    committed = False
    original: Exception | None = None
    cleanup: list[Exception] = []
    try:
        uri = path.resolve().as_uri() + "?mode=rw"
        conn = sqlite3.connect(uri, uri=True, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN IMMEDIATE")
        found = _objects(conn)
        if found:
            _verify_prepared(conn)
        else:
            conn.execute(TABLE_DDL)
            conn.execute(TRIGGER_NO_UPDATE_DDL)
            conn.execute(TRIGGER_NO_DELETE_DDL)
            conn.execute(META_DDL)
            conn.execute(
                "INSERT INTO main." + META_TABLE +
                " (singleton,schema_version) VALUES (1,?)", (SCHEMA_VERSION,))
            _verify_prepared(conn)
        conn.execute("COMMIT")
        committed = True
    except Exception as exc:
        if isinstance(exc, sqlite3.Error) and not isinstance(
                exc, sqlite3.ProgrammingError):
            original = _storage_unavailable(exc, api="terminal facts prepare")
            original.__cause__ = exc
        else:
            # Keep typed failures and ordinary body exceptions themselves,
            # including native ProgrammingError and RuntimeError.  Cleanup
            # diagnostics must not replace their identity or traceback.
            original = exc
    finally:
        if conn is not None:
            if not committed:
                try:
                    if conn.in_transaction:
                        conn.execute("ROLLBACK")
                except Exception as exc:
                    cleanup.append(exc)
            try:
                conn.close()
            except Exception as exc:
                cleanup.append(exc)

    if original is not None:
        original.cleanup_errors = tuple(cleanup)
        raise original

    if cleanup:
        # COMMIT succeeded: a close failure cannot undo that fact.  Preserve
        # native ProgrammingError here too, rather than wrapping its type.
        if isinstance(cleanup[0], sqlite3.ProgrammingError):
            failure = cleanup[0]
        else:
            if isinstance(cleanup[0], sqlite3.Error):
                failure = _storage_unavailable(
                    cleanup[0], api="terminal facts prepare committed, but close failed")
            else:
                failure = TerminalFactsUnavailable(
                    "unreadable", "terminal facts prepare committed, but close failed")
            failure.__cause__ = cleanup[0]
        failure.cleanup_errors = tuple(cleanup)
        failure.committed = True
        raise failure


def append_seal_on(conn: sqlite3.Connection, payload: Any, *,
                   origin: Literal["actor", "collab"]) -> dict[str, Any]:
    """Append one seal on a borrowed, already-transactional connection."""
    _require_on_conn(conn, api="append_seal_on")
    _require_prepared(conn)
    if origin not in (ORIGIN_ACTOR, ORIGIN_COLLAB):
        raise TerminalFactsPayloadInvalid(
            "origin must be 'actor' or 'collab'")
    normalized = _validate_payload(payload)
    _enforce_origin(origin, normalized)

    task_instance_id = normalized["task"]["task_instance_id"]
    stage = normalized["stage"]
    item_key = normalized["item_key"]
    content_sha256 = _content_sha256(normalized)
    seal_id = _seal_id(task_instance_id, stage, item_key, content_sha256)
    payload_json = canonical_json(normalized)
    payload_sha256 = _sha256_text(payload_json)
    key = (task_instance_id, stage, item_key)

    existing_row = _select_by_key(conn, key)
    if existing_row is not None:
        existing = _record_from_row(existing_row)
        if (existing["content_sha256"] == content_sha256
                and existing["seal_id"] == seal_id):
            if existing["origin"] != origin:
                # Same content under a different storage origin is not the
                # same seal: never report a cross-origin copy as idempotent.
                raise TerminalFactsConflict(
                    "terminal seal already exists for this semantic key "
                    "under a different origin")
            return existing
        raise TerminalFactsConflict(
            "terminal seal already exists for this semantic key with "
            "different content")

    clash = _select_by_seal_id(conn, seal_id)
    if clash is not None:
        raise TerminalFactsUnavailable(
            "corrupt", "seal_id is already stored under a different semantic key")

    try:
        _insert_record(
            conn, seal_id=seal_id, task_instance_id=task_instance_id,
            stage=stage, item_key=item_key, kind=normalized["kind"],
            final_decision=normalized["final_decision"],
            content_sha256=content_sha256, payload_sha256=payload_sha256,
            sealed_at=normalized["sealed_at"], archived_at=None,
            origin=origin, payload_json=payload_json,
        )
    except sqlite3.IntegrityError as exc:
        existing_row = _select_by_key(conn, key)
        if existing_row is None:
            raise _storage_unavailable(
                exc, api="terminal facts insert") from exc
        existing = _record_from_row(existing_row)
        if (existing["content_sha256"] == content_sha256
                and existing["seal_id"] == seal_id):
            if existing["origin"] != origin:
                raise TerminalFactsConflict(
                    "terminal seal insert collided under a different origin") from exc
            return existing
        raise TerminalFactsConflict(
            "terminal seal insert collided with different content") from exc

    stored_row = _select_by_key(conn, key)
    if stored_row is None:
        raise TerminalFactsUnavailable(
            "unreadable",
            "terminal facts insert did not persist a readable row")
    stored = _record_from_row(stored_row)
    expected = _build_record(
        normalized, seal_id=seal_id, content_sha256=content_sha256,
        payload_sha256=payload_sha256, origin=origin, archived_at=None)
    if stored != expected:
        raise TerminalFactsUnavailable(
            "unreadable",
            "terminal facts insert read-back does not match the written seal")
    return stored


def read_seal_on(conn: sqlite3.Connection, *, task_instance_id: str,
                 stage: str, item_key: str) -> dict[str, Any] | None:
    """Read one seal; ``None`` only means the complete F1 table has no key."""
    _require_on_conn(conn, api="read_seal_on")
    task_instance_id = _task_instance_id(task_instance_id)
    if stage not in STAGES:
        raise ValueError("stage must be 'stage1' or 'stage2'")
    item_key = _item_key(item_key)
    _require_prepared(conn)
    row = _select_by_key(conn, (task_instance_id, stage, item_key))
    if row is None:
        return None
    return _record_from_row(row)


def list_seals_on(conn: sqlite3.Connection, *, task_instance_id: str,
                  stage: str | None = None,
                  origin: str | None = None) -> list[dict[str, Any]]:
    """List seals for one task instance; any corrupt row fails the batch."""
    _require_on_conn(conn, api="list_seals_on")
    task_instance_id = _task_instance_id(task_instance_id)
    if stage is not None and stage not in STAGES:
        raise ValueError("stage must be 'stage1' or 'stage2' or None")
    if origin is not None and origin not in ORIGINS:
        raise ValueError("origin must be 'actor', 'collab', 'actor_archive' or None")
    _require_prepared(conn)
    sql = _SELECT_RECORD + " WHERE task_instance_id=?"
    params: list[Any] = [task_instance_id]
    if stage is not None:
        sql += " AND stage=?"
        params.append(stage)
    if origin is not None:
        sql += " AND origin=?"
        params.append(origin)
    sql += " ORDER BY stage,item_key,seal_id"
    rows = _execute(conn, sql, params, api="list_seals_on", fetch="all")
    return [_record_from_row(row) for row in rows]


def _value_of(item: Mapping[str, Any] | sqlite3.Row, name: str) -> Any:
    try:
        return item[name]
    except (IndexError, KeyError, TypeError) as exc:
        raise TerminalFactsPayloadInvalid(
            f"seal record is missing {name!r}") from exc


def _archive_candidate(item: Any) -> tuple[tuple[str, str, str],
                                          dict[str, Any]]:
    if not isinstance(item, (Mapping, sqlite3.Row)):
        raise TerminalFactsPayloadInvalid(
            "each archived seal must be a mapping or sqlite3.Row")
    available = set(item.keys())
    required = {
        "payload", "seal_id", "content_sha256", "payload_sha256", "origin",
        "sealed_at", "archived_at",
    }
    if not required <= available:
        raise TerminalFactsPayloadInvalid(
            "seal record must contain payload, seal_id, content_sha256, "
            "payload_sha256, origin, sealed_at and archived_at")
    origin = _value_of(item, "origin")
    if origin != ORIGIN_ACTOR:
        raise TerminalFactsPayloadInvalid(
            "only origin 'actor' seals can be archived")
    if _value_of(item, "archived_at") is not None:
        raise TerminalFactsPayloadInvalid(
            "an actor source seal must not already be archived")

    payload = _validate_payload(_value_of(item, "payload"))
    archiveable_observation = (
        payload["schema"] == OBSERVATION_PAYLOAD_SCHEMA
        and payload["kind"] == "unanimous"
        and payload["trigger"] == "current_observation"
    )
    if (not archiveable_observation
            and (payload["kind"] != "unanimous"
                 or payload["trigger"] != "last_vote")):
        raise TerminalFactsPayloadInvalid(
            "only unanimous/last_vote actor seals or v2 observations can be archived")
    payload_json = canonical_json(payload)
    content_sha256 = _content_sha256(payload)
    payload_sha256 = _sha256_text(payload_json)
    seal_id = _seal_id(payload["task"]["task_instance_id"], payload["stage"],
                       payload["item_key"], content_sha256)
    if _value_of(item, "seal_id") != seal_id:
        raise TerminalFactsPayloadInvalid(
            "source seal_id does not match its payload")
    if _value_of(item, "content_sha256") != content_sha256:
        raise TerminalFactsPayloadInvalid(
            "source content_sha256 does not match its payload")
    if _value_of(item, "payload_sha256") != payload_sha256:
        raise TerminalFactsPayloadInvalid(
            "source payload_sha256 does not match its payload")
    if _value_of(item, "sealed_at") != payload["sealed_at"]:
        raise TerminalFactsPayloadInvalid(
            "source sealed_at does not match its payload")
    key = (payload["task"]["task_instance_id"], payload["stage"],
           payload["item_key"])
    return key, {
        "payload": payload,
        "payload_json": payload_json,
        "seal_id": seal_id,
        "content_sha256": content_sha256,
        "payload_sha256": payload_sha256,
        "sealed_at": payload["sealed_at"],
    }


def _same_archive(existing: Mapping[str, Any],
                  candidate: Mapping[str, Any]) -> bool:
    if existing["origin"] != ORIGIN_ARCHIVE:
        return False
    return (
        existing["seal_id"] == candidate["seal_id"]
        and existing["content_sha256"] == candidate["content_sha256"]
        and existing["payload_sha256"] == candidate["payload_sha256"]
        and canonical_json(existing["payload"]) == candidate["payload_json"]
        and existing["sealed_at"] == candidate["sealed_at"]
    )


def _same_candidate(left: Mapping[str, Any],
                    right: Mapping[str, Any]) -> bool:
    return (
        left["seal_id"] == right["seal_id"]
        and left["content_sha256"] == right["content_sha256"]
        and left["payload_sha256"] == right["payload_sha256"]
        and left["payload_json"] == right["payload_json"]
        and left["sealed_at"] == right["sealed_at"]
    )


def archive_seals_on(dst_conn: sqlite3.Connection, seals: Iterable[Any], *,
                     archived_at: str | None = None) -> list[dict[str, Any]]:
    """Copy actor seals into the archive target, validating the whole batch."""
    _require_on_conn(dst_conn, api="archive_seals_on")
    if archived_at is None:
        stamp = _utc_now()
    else:
        stamp = _timestamp(archived_at, "archived_at")
    try:
        items = list(seals)
    except TypeError as exc:
        raise TerminalFactsPayloadInvalid(
            "seals must be an iterable of seal records") from exc
    _require_prepared(dst_conn)
    if not items:
        return []

    candidates = [_archive_candidate(item) for item in items]
    planned: dict[tuple[str, str, str], dict[str, Any]] = {}
    results: dict[tuple[str, str, str], dict[str, Any]] = {}
    planned_seal_ids: dict[str, tuple[str, str, str]] = {}
    for key, candidate in candidates:
        if key in planned:
            if _same_candidate(planned[key], candidate):
                continue
            raise TerminalFactsConflict(
                "the same batch contains conflicting copies for one seal key")
        clash_key = planned_seal_ids.get(candidate["seal_id"])
        if clash_key is not None and clash_key != key:
            raise TerminalFactsConflict(
                "the same batch holds one seal_id under different seal keys")
        existing_row = _select_by_key(dst_conn, key)
        if existing_row is not None:
            existing = _record_from_row(existing_row)
            if not _same_archive(existing, candidate):
                raise TerminalFactsConflict(
                    "archive target already holds different content for this key")
            results[key] = existing
            continue
        clash = _select_by_seal_id(dst_conn, candidate["seal_id"])
        if clash is not None:
            raise TerminalFactsUnavailable(
                "corrupt",
                "archive target holds this seal_id under a different key")
        planned[key] = candidate
        planned_seal_ids[candidate["seal_id"]] = key
        results[key] = _build_record(
            candidate["payload"], seal_id=candidate["seal_id"],
            content_sha256=candidate["content_sha256"],
            payload_sha256=candidate["payload_sha256"],
            origin=ORIGIN_ARCHIVE, archived_at=stamp)

    for key, candidate in planned.items():
        payload = candidate["payload"]
        try:
            _insert_record(
                dst_conn, seal_id=candidate["seal_id"],
                task_instance_id=key[0], stage=key[1], item_key=key[2],
                kind=payload["kind"],
                final_decision=payload["final_decision"],
                content_sha256=candidate["content_sha256"],
                payload_sha256=candidate["payload_sha256"],
                sealed_at=candidate["sealed_at"], archived_at=stamp,
                origin=ORIGIN_ARCHIVE, payload_json=candidate["payload_json"],
            )
        except sqlite3.IntegrityError as exc:
            existing_row = _select_by_key(dst_conn, key)
            if existing_row is None:
                raise _storage_unavailable(
                    exc, api="terminal facts archive insert") from exc
            existing = _record_from_row(existing_row)
            if not _same_archive(existing, candidate):
                raise TerminalFactsConflict(
                    "archive insert collided with different content") from exc
            # A proven existing copy keeps its original archive time.  Only a
            # new INSERT below must read back this invocation's fresh stamp.
            results[key] = existing
            continue

        stored_row = _select_by_key(dst_conn, key)
        if stored_row is None:
            raise TerminalFactsUnavailable(
                "unreadable",
                "terminal facts archive insert did not persist a readable row")
        stored = _record_from_row(stored_row)
        if not _same_archive(stored, candidate) or stored["archived_at"] != stamp:
            raise TerminalFactsUnavailable(
                "unreadable",
                "terminal facts archive read-back does not match the copied seal")
        results[key] = stored

    return [results[key] for key, _candidate in candidates]
