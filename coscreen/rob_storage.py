"""Versioned storage for manual, result-linked risk-of-bias records."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from coscreen.risk_of_bias_contract import (
    RobContractError, create_assessment, get_mapping, revise_assessment,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS rob_assessments (
  record_id TEXT PRIMARY KEY, revision INTEGER NOT NULL,
  framework TEXT NOT NULL, study_id TEXT NOT NULL,
  reviewer_id TEXT, consensus_set_id TEXT,
  record_json TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS rob_assessment_revisions (
  record_id TEXT NOT NULL, revision INTEGER NOT NULL,
  record_json TEXT NOT NULL, recorded_at TEXT NOT NULL,
  PRIMARY KEY(record_id,revision));
CREATE TABLE IF NOT EXISTS rob_assessment_links (
  record_id TEXT NOT NULL, result_id INTEGER NOT NULL,
  effect_version_id INTEGER NOT NULL, analysis_run_id TEXT,
  PRIMARY KEY(record_id,result_id,effect_version_id));
CREATE INDEX IF NOT EXISTS idx_rob_assessment_links_result
  ON rob_assessment_links(result_id,effect_version_id);
CREATE TABLE IF NOT EXISTS rob_assessment_revision_links (
  record_id TEXT NOT NULL, revision INTEGER NOT NULL,
  result_id INTEGER NOT NULL, effect_version_id INTEGER NOT NULL,
  analysis_run_id TEXT,
  PRIMARY KEY(record_id,revision,result_id,effect_version_id));
CREATE TRIGGER IF NOT EXISTS rob_revisions_no_update BEFORE UPDATE ON rob_assessment_revisions
  BEGIN SELECT RAISE(ABORT, 'RoB revisions are immutable'); END;
CREATE TRIGGER IF NOT EXISTS rob_revisions_no_delete BEFORE DELETE ON rob_assessment_revisions
  BEGIN SELECT RAISE(ABORT, 'RoB revisions are immutable'); END;
CREATE TRIGGER IF NOT EXISTS rob_revision_links_no_update BEFORE UPDATE ON rob_assessment_revision_links
  BEGIN SELECT RAISE(ABORT, 'RoB revision links are immutable'); END;
CREATE TRIGGER IF NOT EXISTS rob_revision_links_no_delete BEFORE DELETE ON rob_assessment_revision_links
  BEGIN SELECT RAISE(ABORT, 'RoB revision links are immutable'); END;
"""


def ensure_rob_storage(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA)


def _check_links(conn: sqlite3.Connection, record: dict) -> list[tuple]:
    source = record["source"]["report_id"]
    if conn.execute("SELECT 1 FROM review_reports WHERE study_id=? AND zotero_key=?",
                    (record["study_id"], source)).fetchone() is None:
        raise RobContractError("source_unlinked", "Assessment source report is not linked to its study")
    links = []
    seen = set()
    for item in record["result_links"]:
        try:
            result_id, version_id = int(item["result_id"]), int(item["effect_version_id"])
        except (TypeError, ValueError, OverflowError) as exc:
            raise RobContractError("invalid_result_link", "Stored result and version IDs must be integers") from exc
        if not (0 < result_id < 2**63 and 0 < version_id < 2**63):
            raise RobContractError("invalid_result_link", "Result and version IDs are out of range")
        if (result_id, version_id) in seen:
            raise RobContractError("duplicate_result_link", "Duplicate result/version link")
        seen.add((result_id, version_id))
        item["result_id"], item["effect_version_id"] = result_id, str(version_id)
        row = conn.execute(
            "SELECT study_id,comparison,outcome,timepoint FROM review_effect_versions "
            "WHERE result_id=? AND effect_version_id=?", (result_id, version_id),
        ).fetchone()
        if row is None:
            raise RobContractError("result_version_missing", "The exact result version does not exist",
                                   result_id=result_id, effect_version_id=version_id)
        if row != (record["study_id"], record["comparison"], record["outcome"], record["timepoint"]):
            raise RobContractError("result_scope_mismatch", "Result version and assessment scope differ",
                                   result_id=result_id)
        run_id = item.get("analysis_run_id")
        if run_id:
            run = conn.execute("SELECT input_snapshot_json FROM analysis_runs WHERE run_id=?",
                               (run_id,)).fetchone()
            if run is None or not any(
                effect["result_id"] == result_id and effect["effect_version_id"] == version_id
                for effect in json.loads(run[0]).get("effects", [])
            ):
                raise RobContractError("run_link_mismatch", "Run does not contain the exact result version",
                                       analysis_run_id=run_id)
        links.append((record["record_id"], result_id, version_id, run_id))
    return links


def _persist_record(conn: sqlite3.Connection, record: dict, *, expected_revision: int | None = None) -> dict:
    links = _check_links(conn, record)
    encoded = json.dumps(record, ensure_ascii=False, allow_nan=False, sort_keys=True)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    record_id = record["record_id"]
    if expected_revision is None:
        conn.execute("INSERT INTO rob_assessments VALUES (?,?,?,?,?,?,?,?)",
                     (record_id, record["revision"], record["framework"], record["study_id"],
                      record["reviewer_id"], record["consensus_set_id"], encoded, now))
    else:
        cursor = conn.execute(
            "UPDATE rob_assessments SET revision=?,record_json=?,updated_at=? "
            "WHERE record_id=? AND revision=?",
            (record["revision"], encoded, now, record_id, expected_revision),
        )
        if cursor.rowcount != 1:
            raise RobContractError("revision_conflict", "RoB record changed during update")
        conn.execute("DELETE FROM rob_assessment_links WHERE record_id=?", (record_id,))
    conn.execute("INSERT INTO rob_assessment_revisions VALUES (?,?,?,?)",
                 (record_id, record["revision"], encoded, now))
    conn.executemany("INSERT INTO rob_assessment_links VALUES (?,?,?,?)", links)
    conn.executemany("INSERT INTO rob_assessment_revision_links VALUES (?,?,?,?,?)",
                     [(record_id, record["revision"], result_id, version_id, run_id)
                      for _, result_id, version_id, run_id in links])
    return record


def save_record(conn: sqlite3.Connection, payload: dict, *, actor_id: str,
                expected_revision: int | None = None, record_id: str | None = None) -> dict:
    """Create or revise one independent manual record inside the caller's transaction."""
    if record_id is None:
        if expected_revision not in (None, 0):
            raise RobContractError("revision_conflict", "New record revision must start at zero")
        kind = payload.get("record_kind", "independent")
        if kind != "independent":
            raise RobContractError("consensus_validation_required",
                                   "Use save_consensus with authenticated source snapshots")
        record = create_assessment(payload, reviewer_id=actor_id, actor_id=actor_id)
    else:
        row = conn.execute("SELECT record_json FROM rob_assessments WHERE record_id=?",
                           (record_id,)).fetchone()
        if row is None:
            raise RobContractError("record_not_found", "RoB record does not exist")
        if expected_revision is None:
            raise RobContractError("revision_required", "Expected revision is required for an update")
        current = json.loads(row[0])
        if current["record_kind"] != "independent":
            raise RobContractError("consensus_validation_required",
                                   "Use save_consensus to revise a consensus record")
        if current["record_kind"] == "independent" and current["reviewer_id"] != actor_id:
            raise RobContractError("reviewer_forbidden", "Another reviewer's independent record cannot be edited")
        record = revise_assessment(current, payload, expected_revision=expected_revision,
                                   actor_id=actor_id)
    return _persist_record(conn, record, expected_revision=expected_revision if record_id else None)


def _text(value, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RobContractError("invalid_field", f"{field} must be a non-empty string", field=field)
    return value.strip()


def _exact_refs(record: dict) -> set[tuple[int, int]]:
    links = record.get("result_links")
    if not isinstance(links, list) or not links:
        raise RobContractError("consensus_result_alignment_mismatch",
                               "Every independent source must link exact result and version IDs")
    refs = set()
    try:
        for link in links:
            if not isinstance(link, dict) or isinstance(link.get("result_id"), bool) or \
                    isinstance(link.get("effect_version_id"), bool):
                raise ValueError
            result_id, version_id = int(link["result_id"]), int(link["effect_version_id"])
            if result_id < 1 or version_id < 1 or (result_id, version_id) in refs:
                raise ValueError
            refs.add((result_id, version_id))
    except (KeyError, TypeError, ValueError) as exc:
        raise RobContractError("consensus_result_alignment_mismatch",
                               "Result and effect-version IDs must be unique positive integers") from exc
    return refs


def _consensus_inputs(conn: sqlite3.Connection, payload: dict, source_record_ids: list[str],
                      source_records: list[dict], authenticated_source_owners: dict[str, str]) -> tuple[list[dict], set[tuple[int, int]]]:
    if not isinstance(payload, dict) or payload.get("record_kind") != "consensus":
        raise RobContractError("invalid_record_kind", "A consensus payload must explicitly set record_kind=consensus")
    for field in ("domains", "overall"):
        if field not in payload:
            raise RobContractError("consensus_judgement_required",
                                   f"Consensus requires an explicit {field} value", field=field)
    rationale = _text(payload.get("consensus_rationale"), "consensus_rationale")
    if not isinstance(source_record_ids, list) or len(source_record_ids) < 2:
        raise RobContractError("consensus_sources_required", "At least two independent source record IDs are required")
    ids = [_text(value, "source_record_id") for value in source_record_ids]
    if len(set(ids)) != len(ids):
        raise RobContractError("duplicate_consensus_source", "Source record IDs must be unique")
    if not isinstance(source_records, list) or len(source_records) != len(ids):
        raise RobContractError("consensus_sources_required", "Provide one source snapshot for every source record ID")
    if not isinstance(authenticated_source_owners, dict) or set(authenticated_source_owners) != set(ids):
        raise RobContractError("consensus_source_owner_mismatch",
                               "Host-authenticated source owners must be supplied for every source record ID")

    mapping = get_mapping(payload.get("framework"), payload.get("tool_version"), payload.get("variant"))
    domain_values = payload.get("domains")
    if not isinstance(domain_values, dict) or not set(item["key"] for item in mapping["domains"]).issubset(domain_values):
        raise RobContractError("consensus_judgement_required",
                               "Supply an explicit recorded or not_recorded value for every mapped domain")
    try:
        _text(payload.get("consensus_set_id"), "consensus_set_id")
        _text(payload.get("study_id"), "study_id")
        _text(payload.get("outcome"), "outcome")
        _text(payload.get("timepoint"), "timepoint")
    except RobContractError:
        raise

    supplied_by_id = {}
    for source in source_records:
        if not isinstance(source, dict):
            raise RobContractError("invalid_consensus_source", "Each source snapshot must be an assessment object")
        source_id = _text(source.get("record_id"), "source.record_id")
        if source_id in supplied_by_id:
            raise RobContractError("duplicate_consensus_source", "Source snapshots must have unique record IDs")
        supplied_by_id[source_id] = source
    if set(supplied_by_id) != set(ids):
        raise RobContractError("consensus_source_id_mismatch",
                               "Source snapshot record IDs must exactly match source_record_ids")

    scope_fields = ("study_id", "comparison", "outcome", "timepoint", "framework",
                    "tool_version", "variant", "mapping_version", "mapping_id", "mapping_status")
    expected_mapping = (mapping["framework"], mapping["tool_version"], mapping["variant"],
                        mapping["mapping_version"], mapping["mapping_id"], "supported")
    payload_scope = (payload.get("study_id"), payload.get("comparison", ""), payload.get("outcome"),
                     payload.get("timepoint"), payload.get("framework"), payload.get("tool_version"),
                     payload.get("variant"), mapping["mapping_version"], mapping["mapping_id"], "supported")
    if payload_scope[4:] != expected_mapping:
        raise RobContractError("consensus_mapping_mismatch", "Consensus payload does not use its registered manual mapping")

    snapshots, refs, assessors = [], None, set()
    for source_id in ids:
        source = supplied_by_id[source_id]
        if source.get("record_kind") != "independent":
            raise RobContractError("consensus_source_not_independent",
                                   "Consensus sources must be independent assessment records", record_id=source_id)
        reviewer_id = _text(source.get("reviewer_id"), "source.reviewer_id")
        owner_id = _text(authenticated_source_owners.get(source_id), "authenticated_source_owner")
        if reviewer_id != owner_id:
            raise RobContractError("consensus_source_owner_mismatch",
                                   "Source record reviewer must match the host-authenticated owner",
                                   record_id=source_id)
        if reviewer_id in assessors:
            raise RobContractError("consensus_assessors_not_distinct",
                                   "Consensus requires independent assessments by distinct assessors")
        assessors.add(reviewer_id)
        revision = source.get("revision")
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
            raise RobContractError("invalid_consensus_source", "Source revision must be a positive integer",
                                   record_id=source_id)
        source_scope = tuple(source.get(field, "" if field == "comparison" else None)
                             for field in scope_fields)
        if source_scope != payload_scope:
            raise RobContractError("consensus_scope_mismatch",
                                   "Independent source mapping and result scope must match the consensus",
                                   record_id=source_id)
        source_refs = _exact_refs(source)
        if refs is None:
            refs = source_refs
        elif refs != source_refs:
            raise RobContractError("consensus_result_alignment_mismatch",
                                   "Independent sources must reference the same exact result versions")
        try:
            snapshot = json.loads(json.dumps(source, ensure_ascii=False, allow_nan=False, sort_keys=True))
        except (TypeError, ValueError) as exc:
            raise RobContractError("invalid_consensus_source", "Source snapshot must contain finite JSON values",
                                   record_id=source_id) from exc
        snapshots.append(snapshot)

    consensus_refs = _exact_refs(payload)
    if consensus_refs != refs:
        raise RobContractError("consensus_result_alignment_mismatch",
                               "Consensus must link exactly the result versions linked by every independent source")
    payload["consensus_rationale"] = rationale
    return snapshots, consensus_refs


def save_consensus(conn: sqlite3.Connection, payload: dict, *, source_record_ids: list[str],
                   source_records: list[dict], authenticated_source_owners: dict[str, str],
                   reconciler_id: str, expected_revision: int | None = None,
                   record_id: str | None = None) -> dict:
    """Persist a manually entered consensus using host-loaded independent source snapshots.

    The host must load source_records from the assessors' task stores and bind each
    ID to its authenticated owner before calling this function. This function
    validates and snapshots those records in the shared database.
    """
    actor = _text(reconciler_id, "authenticated reconciler")
    if not isinstance(payload, dict):
        raise RobContractError("invalid_assessment", "Assessment must be an object")
    forbidden = {"record_id", "revision", "reviewer_id", "created_by", "updated_by",
                 "reconciled_by", "source_record_ids", "source_assessments"}
    if forbidden.intersection(payload):
        raise RobContractError("host_fields_forbidden",
                               "Identity and source provenance fields are assigned by the storage host",
                               fields=sorted(forbidden.intersection(payload)))
    payload = dict(payload)
    source_snapshots, _ = _consensus_inputs(
        conn, payload, source_record_ids, source_records, authenticated_source_owners)
    payload["source_record_ids"] = list(source_record_ids)
    payload["source_assessments"] = source_snapshots
    payload["reconciled_by"] = actor

    if record_id is None:
        if expected_revision not in (None, 0):
            raise RobContractError("revision_conflict", "New record revision must start at zero")
        record = create_assessment(payload, reviewer_id=None, actor_id=actor)
        return _persist_record(conn, record)

    row = conn.execute("SELECT record_json FROM rob_assessments WHERE record_id=?", (record_id,)).fetchone()
    if row is None:
        raise RobContractError("record_not_found", "RoB record does not exist")
    if expected_revision is None:
        raise RobContractError("revision_required", "Expected revision is required for an update")
    current = json.loads(row[0])
    if current.get("record_kind") != "consensus":
        raise RobContractError("consensus_record_required", "Only a consensus record can be revised with save_consensus")
    if current.get("source_record_ids") != list(source_record_ids):
        raise RobContractError("consensus_source_set_immutable",
                               "Changing the source assessor set requires a new consensus record")
    prior_sources = {item["record_id"]: item for item in current.get("source_assessments", [])}
    for source in source_snapshots:
        prior = prior_sources[source["record_id"]]
        if source["revision"] < prior["revision"]:
            raise RobContractError("consensus_source_revision_stale",
                                   "A consensus revision cannot replace a source with an older revision",
                                   record_id=source["record_id"])
        if source["revision"] == prior["revision"] and source != prior:
            raise RobContractError("consensus_source_revision_conflict",
                                   "A source snapshot cannot change without a new source revision",
                                   record_id=source["record_id"])
    for field in ("record_kind", "consensus_set_id", "study_id"):
        if field in payload and payload[field] != current.get(field):
            raise RobContractError("immutable_record_identity", "Consensus identity cannot change in a revision",
                                   field=field)
    changes = {key: value for key, value in payload.items()
               if key not in {"record_kind", "consensus_set_id", "study_id"}}
    record = revise_assessment(current, changes, expected_revision=expected_revision, actor_id=actor)
    return _persist_record(conn, record, expected_revision=expected_revision)


def list_records(conn: sqlite3.Connection) -> list[dict]:
    return [json.loads(row[0]) for row in conn.execute(
        "SELECT record_json FROM rob_assessments ORDER BY study_id,framework,record_id")]


def record_history(conn: sqlite3.Connection, record_id: str) -> list[dict]:
    return [json.loads(row[0]) for row in conn.execute(
        "SELECT record_json FROM rob_assessment_revisions WHERE record_id=? ORDER BY revision",
        (record_id,),
    )]
