"""Authenticated task routes for actor-scoped research trace operations."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import sqlite3
import threading
import time
import uuid
from collections import OrderedDict
from contextlib import closing
from io import BytesIO
from pathlib import Path
from typing import Any, Callable, Literal
from zipfile import ZipFile

from fastapi import Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from coscreen import coding, db, fulltext, research_trace
from coscreen.normalize import normalize_doi
from coscreen.research_trace_export import (
    MAX_PACKAGE_BYTES,
    MAX_EVENTS,
    TracePackageError,
    build_export_zip,
    research_operations,
    validate_import_package,
)


MAX_TRACE_BODY_BYTES = MAX_PACKAGE_BYTES + 1024 * 1024
MAX_PAGE_SIZE = 200
_STAGES = set(research_trace.STAGES)
_CURSOR_FILTERS = ("stage", "action", "subject_id", "since", "until")
_PREVIEW_TTL_SECONDS = 600
_PREVIEW_CACHE_BYTES = 64 * 1024 * 1024
_PREVIEW_CACHE_ENTRIES = 8
_PREVIEW_LOCK = threading.Lock()
_PREVIEWS: OrderedDict[str, dict[str, Any]] = OrderedDict()
_PREVIEW_BYTES = 0


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TraceSettingsIn(_Input):
    status: Literal["off", "recording", "paused"]
    stages: list[Literal["screen", "fulltext", "coding"]] = Field(max_length=3)
    expected_version: int = Field(ge=0)
    operation_id: str | None = Field(default=None, min_length=1, max_length=200)


class TraceExportIn(_Input):
    preset: Literal["public", "complete"] | None = None
    author_alias: str = Field(default="Reviewer", min_length=1, max_length=100)
    language: Literal["zh-CN", "en", "fr", "ru", "es", "ja", "pt", "de", "sr", "ko"] = "en"
    included_private_fields: list[Literal["notes", "source_quotes"]] = Field(default_factory=list, max_length=2)
    filters: "TraceExportFilters" = Field(default_factory=lambda: TraceExportFilters())
    preview_id: str | None = Field(default=None, min_length=20, max_length=100)


class TraceExportFilters(_Input):
    stage: Literal["screen", "fulltext", "coding", "control"] | None = None
    action: str | None = Field(default=None, max_length=80)
    subject_id: str | None = Field(default=None, max_length=500)
    since: str | None = Field(default=None, max_length=80)
    until: str | None = Field(default=None, max_length=80)


class HistoryClearIn(_Input):
    confirm: bool
    expected_version: int | None = Field(default=None, ge=0)
    operation_id: str | None = Field(default=None, min_length=1, max_length=200)


TraceExportIn.model_rebuild()


async def _body_within_limit(request: Request) -> None:
    raw = request.headers.get("content-length")
    try:
        length = int(raw) if raw is not None else None
    except ValueError:
        length = None
    if length is not None and length > MAX_TRACE_BODY_BYTES:
        raise HTTPException(413, "Request body exceeds the research trace limit.")


def _bounded_upload(data: bytes) -> bytes:
    if len(data) > MAX_PACKAGE_BYTES:
        raise HTTPException(413, "Trace package exceeds the upload size limit.")
    return data


def _parse_json_form(raw: str, name: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(400, f"{name} must be a JSON object.") from exc
    if not isinstance(value, dict) or len(value) > MAX_TRACE_BODY_BYTES // 2:
        raise HTTPException(400, f"{name} must be a bounded JSON object.")
    return value


def _iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _canonical_filters(values: dict[str, Any]) -> dict[str, str | None]:
    return {key: None if values.get(key) is None else str(values[key]) for key in _CURSOR_FILTERS}


def _encode_cursor(value: dict[str, Any]) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(raw: str | None, filters: dict[str, str | None], *, task_id: str, actor_id: str) -> dict[str, Any] | None:
    if raw is None:
        return None
    if len(raw) > 4096:
        raise HTTPException(400, "Trace cursor is too large.")
    try:
        padded = raw + "=" * (-len(raw) % 4)
        value = json.loads(base64.urlsafe_b64decode(padded.encode()).decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(400, "Trace cursor is invalid.") from exc
    if (not isinstance(value, dict) or value.get("v") != 1 or value.get("filters") != filters
            or not isinstance(value.get("positions"), dict)
            or not isinstance(value.get("cutoffs"), dict)):
        raise HTTPException(400, "Trace cursor does not match the current filters.")
    if value.get("task_id") != task_id or value.get("actor_id") != actor_id:
        raise HTTPException(400, "Trace cursor does not match the current task and user.")
    for collection in (value["positions"], value["cutoffs"]):
        for number in collection.values():
            if number is not None and (type(number) is not int or number < 0):
                raise HTTPException(400, "Trace cursor contains an invalid sequence.")
    return value


def _read_state(path: Any, task_id: str, actor_id: int | str, *, include_summary: bool = False, initialize_default: bool = False) -> dict[str, Any] | None:
    if path is None:
        return None
    try:
        with closing(db._connect(path)) as conn:
            if initialize_default:
                research_trace.ensure_default_recording(conn, research_trace.TraceContext(task_id, actor_id))
            settings = research_trace.get_settings(conn, task_id, actor_id)
            summary = research_trace.summary(conn, task_id, actor_id) if include_summary else None
    except (OSError, sqlite3.Error):
        return None
    return {"settings": settings, "summary": summary}


def _latest_action_time(events: list[dict[str, Any]], action: str) -> str | None:
    return next((str(event["recorded_at"]) for event in events if event.get("action") == action), None)


def _open_coverage_start(events: list[dict[str, Any]], status: str) -> str | None:
    """Infer only a currently open recording interval from recent control rows."""
    if status != "recording":
        return None
    starts = {"recording_started", "recording_resumed", "history_cleared"}
    ends = {"recording_paused", "recording_stopped"}
    for event in events:  # Store helper returns newest first.
        action = event.get("action")
        if action in starts:
            return str(event["recorded_at"])
        if action in ends:
            return None
    return None


def _current_pause_start(events: list[dict[str, Any]], status: str) -> str | None:
    if status != "paused":
        return None
    for event in events:
        action = event.get("action")
        if action == "recording_paused":
            return str(event["recorded_at"])
        if action in {"recording_started", "recording_resumed", "recording_stopped", "history_cleared"}:
            return None
    return None


def _current_progress(paths: dict[str, Any], actor_id: int | str) -> dict[str, Any]:
    """Read current decisions from primary stores, never infer them from trace counts."""
    personal = paths.get("personal")
    unavailable = {"available": False, "scope": "personal current result state"}
    if personal is None:
        screen = dict(unavailable)
        fulltext_progress = dict(unavailable)
        coding_progress = dict(unavailable)
    else:
        try:
            screen = {**db.get_progress(personal), "available": True,
                      "scope": "personal current screening decisions"}
        except (OSError, sqlite3.Error, ValueError):
            screen = dict(unavailable)
        try:
            fulltext_progress = {**fulltext.get_stage2_progress(personal), "available": True,
                                 "scope": "personal current full-text decisions"}
        except (OSError, sqlite3.Error, ValueError):
            fulltext_progress = dict(unavailable)
        try:
            coding_progress = {**coding.coding_progress(personal), "available": True,
                               "scope": "personal current coding values",
                               "completion_rule": "Existing coding_progress rule: every current queue paper has a non-empty value in every coding dimension; an empty scheme reports coded=0."}
        except (OSError, sqlite3.Error, ValueError):
            coding_progress = dict(unavailable)
    shared = paths.get("shared")
    rater_progress: dict[str, Any] = {
        "available": False, "scope": "latest records for authenticated actor only",
        "completion_claim": False,
        "semantic": "Current latest rater cells and non-empty values; not a scheme-completion measure.",
        "latest_cell_count": 0, "nonempty_cell_count": 0,
        "documents_with_latest_cells": 0, "documents_with_nonempty_values": 0,
    }
    if shared is not None:
        try:
            with closing(db._connect(shared)) as conn:
                has_table = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='coding_rater_records'"
                ).fetchone() is not None
                if has_table:
                    latest = conn.execute(
                        "SELECT item_key,field_key,value_json FROM ("
                        "SELECT item_key,field_key,value_json,"
                        "ROW_NUMBER() OVER(PARTITION BY item_key,field_key ORDER BY version DESC,id DESC) AS rn "
                        "FROM coding_rater_records WHERE rater_id=?) WHERE rn=1",
                        (str(actor_id),),
                    ).fetchall()
                    filled_items: set[str] = set()
                    latest_items: set[str] = set()
                    nonempty = 0
                    for item_key, _field_key, encoded in latest:
                        latest_items.add(str(item_key))
                        try:
                            value = json.loads(encoded)
                        except (TypeError, json.JSONDecodeError):
                            value = None
                        if value is None or value == "" or value == [] or value == {}:
                            continue
                        nonempty += 1
                        filled_items.add(str(item_key))
                    rater_progress.update({
                        "available": True,
                        "latest_cell_count": len(latest),
                        "nonempty_cell_count": nonempty,
                        "documents_with_latest_cells": len(latest_items),
                        "documents_with_nonempty_values": len(filled_items),
                    })
        except (OSError, sqlite3.Error):
            pass
    return {"screen": screen, "fulltext": fulltext_progress,
            "coding": coding_progress, "coding_rater_records": rater_progress}


def _attach_subject_titles(paths: dict[str, Any], events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Attach corpus titles with at most one bounded IN query per physical store."""
    subject_ids = list(dict.fromkeys(
        str(event.get("subject_id")) for event in events
        if event.get("stage") != "control" and event.get("subject_id") is not None
    ))[:200]
    if not subject_ids:
        return events
    title_by_subject: dict[str, str] = {}
    title_paths = {name: paths.get(name) for name in ("shared", "personal") if paths.get(name) is not None}
    placeholders = ",".join("?" for _ in subject_ids)
    for _name, path in _unique_paths(title_paths):
        try:
            with closing(db._connect(path)) as conn:
                has_articles = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='articles'"
                ).fetchone() is not None
                if not has_articles:
                    continue
                rows = conn.execute(
                    f"SELECT zotero_key,title FROM articles WHERE zotero_key IN ({placeholders})",
                    subject_ids,
                ).fetchall()
            for subject, title in rows:
                if title and str(subject) not in title_by_subject:
                    title_by_subject[str(subject)] = str(title)
        except (OSError, sqlite3.Error):
            continue
        if len(title_by_subject) == len(subject_ids):
            break
    for event in events:
        title = title_by_subject.get(str(event.get("subject_id")))
        if title:
            event["subject_title"] = title
    return events


def _cross_store_subject_counts(
    task_id: str, actor_id: int | str, stores: list[tuple[Any, int]],
) -> dict[str, Any]:
    """Compute exact cross-partition distinct counts in SQLite, with bounded Python memory."""
    result: dict[str, Any] = {"unique_subject_count": 0, "modified_subject_count": 0,
                              "by_stage": {}, "daily": {}}
    if not stores:
        return result
    # ATTACH uses read-only file URIs; Windows SQLite requires explicit URI support.
    conn = sqlite3.connect(":memory:", uri=True)
    try:
        branches = []
        params: list[Any] = []
        for index, (path, cutoff) in enumerate(stores):
            schema = f"trace_store_{index}"
            uri = Path(path).resolve().as_uri() + "?mode=ro"
            conn.execute(f"ATTACH DATABASE ? AS {schema}", (uri,))
            branches.append(
                f"SELECT stage,substr(recorded_at,1,10) AS day,subject_id,action "
                f"FROM {schema}.trace_events WHERE task_id=? AND actor_id=? AND sequence<=? "
                "AND stage IN ('screen','fulltext','coding') AND "
                "(action LIKE 'decision_%' OR action LIKE 'coding_value_%' OR action LIKE 'coding_note_%' "
                "OR action LIKE 'rater_record_%' OR action LIKE 'fulltext_%' OR action LIKE 'imported_decision_%' "
                "OR action LIKE 'imported_fulltext_%' OR action LIKE 'imported_coding_value_%' "
                "OR action LIKE 'imported_coding_note_%' OR action LIKE 'imported_rater_record_%')"
            )
            params.extend((str(task_id), str(actor_id), int(cutoff)))
        query = (
            "WITH research AS (" + " UNION ALL ".join(branches) + ") "
            "SELECT 'unique' AS metric,'' AS label,COUNT(DISTINCT subject_id) AS n FROM research "
            "UNION ALL SELECT 'modified','',COUNT(DISTINCT subject_id) FROM research "
            "WHERE action LIKE '%_changed' OR action LIKE '%_cleared' "
            "OR action LIKE 'imported_%_changed' OR action LIKE 'imported_%_cleared' "
            "UNION ALL SELECT 'stage',stage,COUNT(DISTINCT subject_id) FROM research GROUP BY stage "
            "UNION ALL SELECT 'daily',day,COUNT(DISTINCT subject_id) FROM research GROUP BY day"
        )
        for metric, label, count in conn.execute(query, params):
            if metric == "unique":
                result["unique_subject_count"] = int(count or 0)
            elif metric == "modified":
                result["modified_subject_count"] = int(count or 0)
            elif metric == "stage":
                result["by_stage"][str(label)] = int(count or 0)
            elif metric == "daily":
                result["daily"][str(label)] = int(count or 0)
    finally:
        conn.close()
    return result


def _partition_paths(info: Any, user: dict[str, Any], personal_store: Callable,
                     shared_store: Callable) -> dict[str, Any]:
    return {
        "personal": personal_store(info, user),
        "shared": shared_store(info),
    }


def _path_key(path: Any) -> str | None:
    if path is None:
        return None
    try:
        return str(Path(path).resolve())
    except (OSError, TypeError, ValueError):
        return str(path)


def _unique_paths(paths: dict[str, Any]) -> list[tuple[str, Any]]:
    seen: set[str] = set()
    result = []
    for name, path in paths.items():
        key = _path_key(path)
        if key is None or key in seen:
            continue
        seen.add(key)
        result.append((name, path))
    return result


def _settings_payload(info: Any, actor_id: int | str, paths: dict[str, Any]) -> dict[str, Any]:
    partitions: dict[str, dict[str, Any]] = {}
    selected: set[str] = set()
    version = 0
    statuses: list[str] = []
    state_by_path: dict[str, dict[str, Any] | None] = {}
    canonical_name_by_path: dict[str, str] = {}
    for name, path in paths.items():
        key = _path_key(path)
        if key is not None and key in state_by_path:
            state = state_by_path[key]
            canonical = canonical_name_by_path[key]
            if state is None:
                partitions[name] = {"available": False, "status": "unavailable", "stages": [],
                                    "version": 0, "store_id": None, "updated_at": None,
                                    "alias_of": canonical}
            else:
                settings = state["settings"]
                partitions[name] = {"available": True, "status": settings["status"],
                                    "stages": settings["stages"], "version": settings["version"],
                                    "store_id": settings["store_id"], "updated_at": settings["updated_at"],
                                    "operation_id": settings["operation_id"], "alias_of": canonical}
                selected.update(settings["stages"])
            continue
        state = _read_state(path, str(info.task_id), actor_id)
        if key is not None:
            state_by_path[key] = state
            canonical_name_by_path[key] = name
        if state is None:
            partitions[name] = {"available": False, "status": "unavailable", "stages": [],
                                "version": 0, "store_id": None, "updated_at": None}
            continue
        settings = state["settings"]
        version += int(settings["version"])
        selected.update(settings["stages"])
        statuses.append(settings["status"])
        partitions[name] = {
            "available": True, "status": settings["status"], "stages": settings["stages"],
            "version": settings["version"], "store_id": settings["store_id"],
            "updated_at": settings["updated_at"], "operation_id": settings["operation_id"],
        }
    missing = (("screen" in selected or "fulltext" in selected) and not paths.get("personal"))
    missing = missing or ("coding" in selected and
                          (not paths.get("personal") or not paths.get("shared")))
    needed_statuses: list[str] = []
    if selected:
        needed_statuses.append(partitions["personal"]["status"])
        if "coding" in selected:
            needed_statuses.append(partitions["shared"]["status"])
    if missing or (selected and any(state not in {"recording", "paused"} for state in needed_statuses)):
        status = "partial"
    elif selected and needed_statuses and all(state == "paused" for state in needed_statuses):
        status = "paused"
    elif selected and needed_statuses and all(state == "recording" for state in needed_statuses):
        status = "recording"
    else:
        status = "off"
    return {"task_id": str(info.task_id), "status": status, "stages": sorted(selected),
            "version": version, "partitions": partitions}


def _set_status(
    info: Any,
    actor_id: int | str,
    paths: dict[str, Any],
    body: TraceSettingsIn,
) -> dict[str, Any]:
    current = _settings_payload(info, actor_id, paths)
    stages = [] if body.status == "off" else sorted(set(body.stages))
    operation_id = body.operation_id or str(uuid.uuid4())
    targets: dict[str, tuple[str, Any, set[str]]] = {}
    for name, path in paths.items():
        if path is None:
            continue
        key = _path_key(path)
        if key is None:
            continue
        if key not in targets:
            targets[key] = (name, path, set())
        target_stages = set(stages if name == "personal" else (["coding"] if "coding" in stages else []))
        targets[key][2].update(target_stages)
    if body.expected_version != current["version"]:
        completed = False
        if body.operation_id:
            for name, _path, stage_set in targets.values():
                prior = current["partitions"][name]
                expected_stages = sorted(stage_set)
                expected_status = body.status if expected_stages else "off"
                if (prior.get("operation_id") == operation_id
                        and prior["stages"] == expected_stages
                        and prior["status"] == expected_status):
                    completed = True
                    break
        if not completed:
            raise HTTPException(409, {
                "error_code": "trace_settings_conflict",
                "message": "Trace settings changed; reload and retry.",
                "current": current,
            })
    failures: dict[str, dict[str, str]] = {}
    for name, path, stage_set in targets.values():
        partition_stages = sorted(stage_set)
        partition_status = body.status if partition_stages else "off"
        prior = current["partitions"][name]
        if (body.operation_id and prior.get("operation_id") == operation_id
                and prior["stages"] == partition_stages and prior["status"] == partition_status):
            continue
        conn = None
        try:
            conn = db._connect(path)
            conn.execute("BEGIN IMMEDIATE")
            expected = int(prior["version"])
            research_trace.update_settings(
                conn, research_trace.TraceContext(str(info.task_id), actor_id, source="api"),
                stages=partition_stages, status=partition_status,
                operation_id=operation_id, expected_version=expected,
            )
            conn.commit()
        except research_trace.TraceVersionConflict as exc:
            if conn is not None:
                conn.rollback()
            raise HTTPException(409, {
                "error_code": "trace_settings_conflict",
                "message": "Trace settings changed; reload and retry.",
                "current_partition": exc.current,
            }) from exc
        except research_trace.TraceConflict:
            if conn is not None:
                try:
                    conn.rollback()
                except sqlite3.Error:
                    pass
            raise
        except (OSError, sqlite3.Error, ValueError, TypeError) as exc:
            if conn is not None:
                try:
                    conn.rollback()
                except sqlite3.Error:
                    pass
            failures[name] = {"error": "partition_write_failed", "details": type(exc).__name__}
        finally:
            if conn is not None:
                conn.close()
    result = _settings_payload(info, actor_id, paths)
    if failures:
        result["status"] = "partial"
        result["failures"] = failures
    if body.status == "recording" and stages:
        required = ["personal"]
        if "coding" in stages and _path_key(paths.get("shared")) != _path_key(paths.get("personal")):
            required.append("shared")
        if any(not paths.get(name) for name in required):
            result["status"] = "partial"
    return result


def _summary_payload(info: Any, actor_id: int | str, paths: dict[str, Any]) -> dict[str, Any]:
    result = {
        "event_count": 0, "change_count": 0, "unique_subject_count": 0,
        "modified_subject_count": 0, "field_change_count": 0,
        "by_stage": {stage: {"event_count": 0, "unique_subject_count": 0}
                     for stage in research_trace.STAGES},
        "daily": [], "partitions": {}, "recent_events": [], "coverage_boundaries": {},
        "current_progress": {}, "pause_count": 0, "history_clear_count": 0,
        "has_known_coverage_gaps": False, "first_at": None, "last_at": None,
        "recent_events_order": "recorded_at_desc_then_partition_then_local_sequence; cross-partition timestamps are approximate",
    }
    aggregate_stores: list[tuple[Any, int]] = []
    daily: dict[str, dict[str, Any]] = {}
    recent_rows: list[tuple[str, str, int, dict[str, Any]]] = []
    first_times: list[str] = []
    last_times: list[str] = []
    for name, path in _unique_paths(paths):
        state = _read_state(path, str(info.task_id), actor_id, include_summary=True)
        if state is None:
            result["partitions"][name] = {"available": False, "event_count": 0,
                                          "change_count": 0, "status": "unavailable"}
            continue
        summary = state["summary"]
        aggregate_stores.append((path, int(summary["snapshot_sequence"])))
        result["partitions"][name] = {
            "available": True, "event_count": summary["event_count"],
            "change_count": summary.get("change_count", 0), "status": summary["status"],
            "store_id": summary["store_id"], "first_at": summary["first_at"],
            "last_at": summary["last_at"], "first_sequence": summary["first_sequence"],
            "last_sequence": summary["last_sequence"],
        }
        result["event_count"] += summary["event_count"]
        result["change_count"] += summary.get("change_count", 0)
        result["field_change_count"] += summary.get("field_change_count", 0)
        for stage in research_trace.STAGES:
            result["by_stage"][stage]["event_count"] += summary["by_stage"][stage]["event_count"]
        with closing(db._connect(path)) as conn:
            recent = research_trace.recent_events(
                conn, str(info.task_id), actor_id, limit=10,
                snapshot_sequence=summary["snapshot_sequence"],
            )
            controls = research_trace.recent_events(
                conn, str(info.task_id), actor_id, limit=20, stage="control",
                snapshot_sequence=summary["snapshot_sequence"],
            )
            for event in recent:
                recent_rows.append((str(event["recorded_at"]), name,
                                    int(event["sequence"]), {**event, "partition": name}))
            control_boundaries = []
            for event in controls:
                before = event.get("before") if isinstance(event.get("before"), dict) else {}
                after = event.get("after") if isinstance(event.get("after"), dict) else {}
                control_boundaries.append({
                    "sequence": int(event["sequence"]), "recorded_at": event["recorded_at"],
                    "action": event["action"], "stage": "control",
                    "before": {key: before[key] for key in ("status", "stages", "event_count") if key in before},
                    "after": {key: after[key] for key in ("status", "stages", "coverage_starts_after_clear") if key in after},
                })
            control_counts = summary.get("control_counts", {})
            pause_count = int(summary.get("pause_count", 0))
            history_clear_count = int(control_counts.get("history_cleared", 0))
            result["pause_count"] += pause_count
            result["history_clear_count"] += history_clear_count
            if summary.get("first_at"):
                first_times.append(str(summary["first_at"]))
            if summary.get("last_at"):
                last_times.append(str(summary["last_at"]))
            current_start = _open_coverage_start(controls, summary["status"])
            result["coverage_boundaries"][name] = {
                "recent_controls": control_boundaries,
                "recent_control_limit": 20,
                "recent_controls_truncated": int(summary.get("control_event_count", 0)) > len(controls),
                "pause_count": pause_count,
                "history_clear_count": history_clear_count,
                "known_gap_boundary_count": pause_count + history_clear_count,
                "has_known_coverage_gaps": bool(pause_count or history_clear_count),
                "coverage_start_at": current_start if summary["status"] == "recording" else None,
                "coverage_start_ambiguous": bool(
                    summary["status"] == "recording"
                    and int(summary.get("control_event_count", 0)) > len(controls)
                    and current_start is None
                ),
                "paused_since_at": _current_pause_start(controls, summary["status"]),
            }
            result["partitions"][name].update({
                "control_event_count": int(summary.get("control_event_count", 0)),
                "pause_count": pause_count,
                "history_clear_count": history_clear_count,
                "coverage_start_at": current_start if summary["status"] == "recording" else None,
                "first_at": summary.get("first_at"), "last_at": summary.get("last_at"),
            })
        for day in summary.get("daily", []):
            if not isinstance(day, dict) or not day.get("date"):
                continue
            target = daily.setdefault(str(day["date"]), {"date": str(day["date"]),
                                                          "event_count": 0, "unique_subject_count": 0,
                                                          "by_stage": {}})
            target["event_count"] += int(day.get("event_count", 0))
            # Per-partition subject unions cannot be summed safely; exact daily union
            # is filled from SQLite below.
            for stage, data in (day.get("by_stage") or {}).items():
                out = target["by_stage"].setdefault(stage, {"event_count": 0})
                out["event_count"] += int(data.get("event_count", 0))
    cross_counts = _cross_store_subject_counts(str(info.task_id), actor_id, aggregate_stores)
    result["unique_subject_count"] = cross_counts["unique_subject_count"]
    result["modified_subject_count"] = cross_counts["modified_subject_count"]
    for stage in research_trace.STAGES:
        result["by_stage"][stage]["unique_subject_count"] = cross_counts["by_stage"].get(stage, 0)
    for day, daily_row in daily.items():
        daily_row["unique_subject_count"] = cross_counts["daily"].get(day, 0)
    result["daily"] = sorted(daily.values(), key=lambda row: row["date"])
    recent_rows.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
    result["recent_events"] = _attach_subject_titles(
        paths, [row[3] for row in recent_rows[:10]])
    result["first_at"] = min(first_times) if first_times else None
    result["last_at"] = max(last_times) if last_times else None
    result["has_known_coverage_gaps"] = bool(result["pause_count"] or result["history_clear_count"])
    result["current_progress"] = _current_progress(paths, actor_id)
    settings = _settings_payload(info, actor_id, paths)
    for name, part in settings["partitions"].items():
        alias = part.get("alias_of")
        if alias and alias in result["partitions"]:
            result["partitions"][name] = {**result["partitions"][alias], "alias_of": alias}
            if alias in result["coverage_boundaries"]:
                result["coverage_boundaries"][name] = {
                    **result["coverage_boundaries"][alias], "alias_of": alias,
                }
        elif name not in result["partitions"]:
            result["partitions"][name] = {"available": part["available"],
                                          "status": part["status"], "event_count": 0,
                                          "change_count": 0, "stages": part["stages"],
                                          "store_id": part["store_id"]}
            result["coverage_boundaries"][name] = {
                "available": part["available"], "recent_controls": [],
                "recent_control_limit": 20, "recent_controls_truncated": False,
                "pause_count": 0, "history_clear_count": 0,
                "known_gap_boundary_count": 0, "has_known_coverage_gaps": False,
                "coverage_start_at": None, "paused_since_at": None,
            }
        if name in result["partitions"]:
            result["partitions"][name].update({
                "available": part["available"], "status": part["status"],
                "stages": part["stages"], "settings_updated_at": part["updated_at"],
            })
    result["status"] = settings["status"]
    result["stages"] = settings["stages"]
    result["version"] = settings["version"]
    return result


def _snapshot(info: Any, actor_id: int | str, paths: dict[str, Any],
              filters: TraceExportFilters | None = None) -> dict[str, Any]:
    filter_values = filters.model_dump(exclude_none=True) if filters else {}
    stores = []
    seen_paths: set[str] = set()
    for name, path in paths.items():
        if path is None:
            stores.append({
                "partition": name, "store_id": None,
                "settings": {"status": "unavailable", "stages": [], "version": None},
                "summary": {"event_count": 0, "history_event_count": 0,
                            "first_sequence": None, "last_sequence": None,
                            "first_at": None, "last_at": None,
                            "status": "unavailable", "stages": []},
                "cutoff_sequence": None, "events": [],
            })
            continue
        key = _path_key(path)
        if key is not None and key in seen_paths:
            continue
        if key is not None:
            seen_paths.add(key)
        try:
            with closing(db._connect(path)) as conn:
                # Keep a SQLite read transaction open across settings, cutoff, and
                # all pages. A concurrent clear cannot change later pages mid-export.
                conn.execute("BEGIN")
                settings = research_trace.get_settings(conn, str(info.task_id), actor_id)
                cutoff = research_trace.snapshot_cutoff(conn, str(info.task_id), actor_id)
                whole_summary = research_trace.summary(
                    conn, str(info.task_id), actor_id, snapshot_sequence=cutoff,
                )
                control_events = research_trace.recent_events(
                    conn, str(info.task_id), actor_id, limit=20, stage="control",
                    snapshot_sequence=cutoff,
                )
                recent_controls = []
                for control in control_events:
                    before = control.get("before") if isinstance(control.get("before"), dict) else {}
                    after = control.get("after") if isinstance(control.get("after"), dict) else {}
                    recent_controls.append({
                        "sequence": int(control["sequence"]),
                        "recorded_at": control["recorded_at"],
                        "action": control["action"],
                        "before": {key: before[key] for key in ("status", "stages", "event_count") if key in before},
                        "after": {key: after[key] for key in ("status", "stages", "coverage_starts_after_clear") if key in after},
                    })
                control_count = int(whole_summary.get("control_event_count", 0))
                start_at = _open_coverage_start(control_events, settings["status"])
                page = research_trace.list_events(
                    conn, str(info.task_id), actor_id, limit=200, snapshot_sequence=cutoff,
                    stage=filter_values.get("stage"), action=filter_values.get("action"),
                    subject_id=filter_values.get("subject_id"), since=filter_values.get("since"),
                    until=filter_values.get("until"),
                )
                events = list(page["events"])
                while page["has_more"]:
                    if not events:
                        raise HTTPException(500, "Trace pagination returned an invalid empty page.")
                    page = research_trace.list_events(
                        conn, str(info.task_id), actor_id, limit=200, cursor=events[-1]["sequence"],
                        snapshot_sequence=cutoff, stage=filter_values.get("stage"),
                        action=filter_values.get("action"), subject_id=filter_values.get("subject_id"),
                        since=filter_values.get("since"), until=filter_values.get("until"),
                    )
                    events.extend(page["events"])
                    if len(events) > MAX_EVENTS:
                        raise HTTPException(413, f"Filtered export exceeds {MAX_EVENTS} events.")
                if len(events) > MAX_EVENTS:
                    raise HTTPException(413, f"Filtered export exceeds {MAX_EVENTS} events.")
                summary = {
                    "event_count": len(events),
                    "history_event_count": whole_summary["event_count"],
                    "first_sequence": whole_summary["first_sequence"],
                    "last_sequence": whole_summary["last_sequence"],
                    "first_at": whole_summary["first_at"],
                    "last_at": whole_summary["last_at"],
                    "selected_first_at": events[0]["recorded_at"] if events else None,
                    "selected_last_at": events[-1]["recorded_at"] if events else None,
                    "status": settings["status"], "stages": settings["stages"],
                    "pause_count": whole_summary["pause_count"],
                    "history_clear_count": whole_summary["control_counts"].get("history_cleared", 0),
                    "control_counts": whole_summary["control_counts"],
                    "coverage_start_at": start_at if settings["status"] == "recording" else None,
                    "coverage_start_ambiguous": bool(
                        settings["status"] == "recording" and control_count > len(control_events)
                        and start_at is None
                    ),
                    "recent_controls": recent_controls,
                    "recent_controls_truncated": control_count > len(control_events),
                }
        except (OSError, sqlite3.Error):
            stores.append({
                "partition": name, "store_id": None,
                "settings": {"status": "unavailable", "stages": [], "version": None},
                "summary": {"event_count": 0, "history_event_count": 0,
                            "first_sequence": None, "last_sequence": None,
                            "first_at": None, "last_at": None,
                            "status": "unavailable", "stages": []},
                "cutoff_sequence": None, "events": [],
            })
            continue
        for start in range(0, len(events), 200):
            _attach_subject_titles(paths, events[start:start + 200])
        stores.append({"partition": name, "store_id": settings["store_id"],
                       "settings": settings, "summary": summary,
                       "cutoff_sequence": cutoff, "events": events})
    return {"task_id": str(info.task_id), "actor_id": str(actor_id),
            "selection": filter_values, "stores": stores}


def _cache_prune(now: float) -> None:
    global _PREVIEW_BYTES
    for key, item in list(_PREVIEWS.items()):
        if item["expires_at"] <= now:
            _PREVIEW_BYTES -= len(item["package"])
            del _PREVIEWS[key]


def _cache_preview(task_id: str, actor_id: str, package: bytes, preset: str) -> str:
    global _PREVIEW_BYTES
    now = time.time()
    owner = (task_id, actor_id, preset)
    with _PREVIEW_LOCK:
        _cache_prune(now)
        for key, item in list(_PREVIEWS.items()):
            if item["owner"] == owner:
                _PREVIEW_BYTES -= len(item["package"])
                del _PREVIEWS[key]
        while (_PREVIEW_BYTES + len(package) > _PREVIEW_CACHE_BYTES or
               len(_PREVIEWS) >= _PREVIEW_CACHE_ENTRIES) and _PREVIEWS:
            _, removed = _PREVIEWS.popitem(last=False)
            _PREVIEW_BYTES -= len(removed["package"])
        if len(package) > _PREVIEW_CACHE_BYTES:
            raise HTTPException(413, "The export is too large to preview. Reduce the selected range or content and try again.")
        preview_id = uuid.uuid4().hex + uuid.uuid4().hex[:8]
        _PREVIEWS[preview_id] = {"owner": owner, "package": package,
                                 "expires_at": now + _PREVIEW_TTL_SECONDS}
        _PREVIEW_BYTES += len(package)
    return preview_id


def _take_preview(preview_id: str, task_id: str, actor_id: str) -> bytes | None:
    global _PREVIEW_BYTES
    now = time.time()
    with _PREVIEW_LOCK:
        _cache_prune(now)
        item = _PREVIEWS.get(preview_id)
        if item is None or item["owner"][:2] != (task_id, actor_id):
            return None
        del _PREVIEWS[preview_id]
        _PREVIEW_BYTES -= len(item["package"])
        return item["package"]


def _package_preview(package: bytes, preview_id: str) -> dict[str, Any]:
    parsed = validate_import_package(package)
    manifest = parsed["manifest"]
    with ZipFile(BytesIO(package)) as archive:
        report_markdown = archive.read("report.md").decode("utf-8")
        report_html = archive.read("report.html").decode("utf-8")
    events = parsed["events"]
    baseline_count = sum(str(event.get("action", "")).startswith(("baseline_", "imported_baseline_")) for event in events)
    research = research_operations(events)
    samples = research[:25]
    return {
        "preview_id": preview_id,
        "package_sha256": parsed["package_sha256"],
        "privacy_mode": manifest["privacy_mode"],
        "selection": manifest.get("selection", {}),
        "included_private_fields": manifest.get("included_private_fields", []),
        "event_count": manifest["event_count"],
        "coverage": manifest.get("stores", []),
        "checksums": manifest["files"],
        "sample_events": samples,
        "research_event_count": len(research),
        "baseline_event_count": baseline_count,
        "control_event_count": sum(event.get("stage") == "control" for event in events),
        "report_markdown": report_markdown,
        "report_html": report_html,
        "manifest": manifest,
        "expires_in_seconds": _PREVIEW_TTL_SECONDS,
    }


def _import_metadata(info: Any, actor_id: int | str, package: dict[str, Any], path: Any,
                     corpus_paths: dict[str, Any] | None = None) -> dict[str, Any]:
    events = package["events"]
    actors = sorted(package["origin_actors"])
    origin = package["manifest"].get("origin", {})
    if not actors:
        source_actor = origin.get("actor_id", origin.get("author_alias")) if isinstance(origin, dict) else None
        if source_actor is not None:
            actors = [str(source_actor)]
    subjects: dict[str, dict[str, Any]] = {}
    statuses = {"new": 0, "duplicate": 0, "conflict": 0}
    with closing(db._connect(path)) as conn:
        for event in events:
            digest = research_trace.origin_event_sha256(event)
            prior = research_trace.lookup_import(
                conn, task_id=str(info.task_id), actor_id=actor_id,
                origin_store_id=str(event["store_id"]),
                origin_event_id=str(event["event_id"]), origin_sha256=digest,
            )
            statuses[prior["status"] if prior else "new"] += 1
            if event.get("stage") == "control":
                continue
            subject = str(event["subject_id"])
            item = subjects.setdefault(subject, {
                "subject_id": subject, "stages": set(), "event_count": 0, "source_dois": set(),
            })
            item["stages"].add(event["stage"])
            item["event_count"] += 1
            after = event.get("after")
            if event.get("action") == "baseline_item" and isinstance(after, dict):
                doi = normalize_doi(after.get("doi", ""))
                if doi:
                    item["source_dois"].add(doi)

    candidates: dict[str, dict[str, dict[str, str]]] = {subject: {} for subject in subjects}
    candidate_paths = corpus_paths or {"personal": path}
    for _name, candidate_path in _unique_paths(candidate_paths):
        with closing(db._connect(candidate_path)) as conn:
            conn.execute("CREATE TEMP TABLE trace_import_subjects (subject_id TEXT PRIMARY KEY)")
            conn.executemany(
                "INSERT INTO trace_import_subjects(subject_id) VALUES(?)",
                ((subject,) for subject in subjects),
            )
            conn.execute(
                "CREATE TEMP TABLE trace_import_dois (subject_id TEXT, normalized_doi TEXT, "
                "PRIMARY KEY(subject_id,normalized_doi))"
            )
            conn.executemany(
                "INSERT OR IGNORE INTO trace_import_dois(subject_id,normalized_doi) VALUES(?,?)",
                ((subject, doi) for subject, item in subjects.items() for doi in item["source_dois"]),
            )
            conn.execute(
                "CREATE INDEX trace_import_dois_normalized_idx "
                "ON trace_import_dois(normalized_doi)"
            )
            conn.create_function("trace_normalize_doi", 1, normalize_doi, deterministic=True)
            # Cap corpus rows before joining them to untrusted package subjects. A DOI can
            # be duplicated many times in either input; joining the raw article table
            # directly would multiply those counts before Python has a chance to dedupe.
            conn.execute(
                "CREATE TEMP TABLE trace_import_doi_targets ("
                "normalized_doi TEXT NOT NULL, target_subject TEXT NOT NULL, title TEXT, "
                "PRIMARY KEY(normalized_doi,target_subject))"
            )
            conn.execute(
                "WITH normalized_targets AS ("
                "SELECT trace_normalize_doi(doi) AS normalized_doi, "
                "zotero_key AS target_subject, title FROM articles WHERE doi IS NOT NULL"
                "), ranked_targets AS ("
                "SELECT normalized_doi,target_subject,title,ROW_NUMBER() OVER ("
                "PARTITION BY normalized_doi ORDER BY target_subject) AS target_rank "
                "FROM normalized_targets WHERE normalized_doi<>''"
                ") INSERT INTO trace_import_doi_targets(normalized_doi,target_subject,title) "
                "SELECT normalized_doi,target_subject,title FROM ranked_targets WHERE target_rank<=20"
            )
            candidate_rows = conn.execute(
                "WITH all_candidates AS ("
                "SELECT s.subject_id AS source_subject,a.zotero_key AS target_subject,"
                "a.title AS title,'key' AS match_basis,0 AS priority "
                "FROM trace_import_subjects AS s JOIN articles AS a ON a.zotero_key=s.subject_id "
                "UNION ALL "
                "SELECT s.subject_id AS source_subject,t.target_subject AS target_subject,"
                "t.title AS title,'doi' AS match_basis,1 AS priority "
                "FROM trace_import_dois AS s JOIN trace_import_doi_targets AS t "
                "USING(normalized_doi)"
                "), deduplicated AS ("
                "SELECT source_subject,target_subject,title,match_basis,priority,"
                "ROW_NUMBER() OVER (PARTITION BY source_subject,target_subject ORDER BY priority) "
                "AS reason_rank FROM all_candidates"
                "), ranked AS ("
                "SELECT source_subject,target_subject,title,match_basis,priority,"
                "ROW_NUMBER() OVER (PARTITION BY source_subject ORDER BY priority,target_subject) "
                "AS candidate_rank FROM deduplicated WHERE reason_rank=1"
                ") SELECT source_subject,target_subject,title,match_basis FROM ranked "
                "WHERE candidate_rank<=20 ORDER BY source_subject,candidate_rank"
            )
            for subject, target, title, basis in candidate_rows:
                current = candidates[subject].get(target)
                if current is None or (current["match_basis"] == "doi" and basis == "key"):
                    candidates[subject][target] = {
                        "subject_id": target, "title": title or "", "match_basis": basis,
                    }
    return {
        "package_sha256": package["package_sha256"],
        "privacy_mode": package["manifest"]["privacy_mode"],
        "origin_task_id": (origin.get("task_id") if isinstance(origin, dict) else None),
        "event_count": len(events),
        "source_aliases": actors,
        "subjects": [{
            "subject_id": item["subject_id"], "stages": sorted(item["stages"]),
            "event_count": item["event_count"],
            "candidates": sorted(candidates[item["subject_id"]].values(),
                                 key=lambda candidate: (candidate["match_basis"] != "key",
                                                        candidate["subject_id"]))[:20],
        } for item in sorted(subjects.values(), key=lambda row: row["subject_id"])],
        "events": events[:25],
        "already_imported": statuses["duplicate"],
        "conflicts": statuses["conflict"],
        "new_events": statuses["new"],
        "unmapped_subject_count": len(subjects),
    }


def register_research_trace_routes(
    app: Any,
    require_user: Callable[..., dict],
    task_or_404: Callable[[str], Any],
    personal_store: Callable[[Any, dict], Any],
    shared_store: Callable[[Any], Any],
    authorize_task: Callable[[Any, dict, str], bool],
    validate_subject: Callable[[Any, str, str], bool] | None = None,
) -> None:
    """Register actor-private trace routes. Actor identity always comes from auth.

    The host supplies a personal DB resolver, an optional collaboration/shared DB
    resolver, task ACL and a canonical-document mapping validator. Supported ACL
    actions are ``trace:read`` and ``trace:write``. Personal snapshots are stored
    in the actor's personal database; newer coding snapshots are read from the
    shared task database but are filtered by authenticated actor ID.
    """

    def context(task_id: str, user: dict[str, Any], action: str):
        info = task_or_404(task_id)
        if authorize_task(info, user, action) is not True:
            raise HTTPException(403, "Task trace access denied.")
        actor_id = user.get("user_id")
        if isinstance(actor_id, bool) or not isinstance(actor_id, (int, str)) or not str(actor_id):
            raise HTTPException(401, "Authenticated user ID is missing.")
        return info, actor_id, _partition_paths(info, user, personal_store, shared_store)

    @app.get("/api/tasks/{task_id}/trace/settings")
    def get_settings(task_id: str, user: dict = Depends(require_user)):
        info, actor_id, paths = context(task_id, user, "trace:read")
        for path in paths.values():
            _read_state(path, str(info.task_id), actor_id, initialize_default=True)
        return _settings_payload(info, actor_id, paths)

    @app.put("/api/tasks/{task_id}/trace/settings", dependencies=[Depends(_body_within_limit)])
    def put_settings(task_id: str, body: TraceSettingsIn, user: dict = Depends(require_user)):
        info, actor_id, paths = context(task_id, user, "trace:write")
        try:
            return _set_status(info, actor_id, paths, body)
        except research_trace.TraceConflict as exc:
            raise HTTPException(409, {"error_code": "trace_operation_conflict", "message": str(exc)}) from exc

    @app.delete("/api/tasks/{task_id}/trace/history", dependencies=[Depends(_body_within_limit)])
    def clear_history(task_id: str, body: HistoryClearIn, user: dict = Depends(require_user)):
        info, actor_id, paths = context(task_id, user, "trace:write")
        if body.confirm is not True:
            raise HTTPException(400, "Explicit confirmation is required to clear personal trace history.")
        operation_id = body.operation_id or str(uuid.uuid4())
        before = _settings_payload(info, actor_id, paths)
        retrying_partial = body.operation_id is not None and any(
            part.get("operation_id") == operation_id for part in before["partitions"].values()
        )
        if (body.expected_version is not None and body.expected_version != before["version"]
                and not retrying_partial):
            raise HTTPException(409, {"error_code": "trace_settings_conflict", "current": before})
        cleared = {}
        failures = {}
        total_deleted = 0
        seen_paths: set[str] = set()
        for name, path in paths.items():
            if path is None:
                failures[name] = {"error": "partition_unavailable"}
                continue
            key = _path_key(path)
            if key is not None and key in seen_paths:
                continue
            if key is not None:
                seen_paths.add(key)
            conn = None
            try:
                conn = db._connect(path)
                conn.execute("BEGIN IMMEDIATE")
                settings = research_trace.get_settings(conn, str(info.task_id), actor_id)
                expected_version = int(before["partitions"].get(name, {}).get("version", 0))
                if settings["operation_id"] == operation_id:
                    expected_version -= 1
                result = research_trace.clear_history(
                    conn, research_trace.TraceContext(str(info.task_id), actor_id, source="api"),
                    operation_id=operation_id, expected_version=expected_version,
                )
                conn.commit()
                cleared[name] = result
                total_deleted += result["deleted_event_count"]
            except research_trace.TraceVersionConflict as exc:
                if conn is not None:
                    try:
                        conn.rollback()
                    except sqlite3.Error:
                        pass
                failures[name] = {"error": "version_conflict", "current": exc.current}
            except research_trace.TraceConflict as exc:
                if conn is not None:
                    try:
                        conn.rollback()
                    except sqlite3.Error:
                        pass
                raise HTTPException(409, {"error_code": "trace_operation_conflict",
                                          "message": str(exc)}) from exc
            except (OSError, sqlite3.Error, ValueError, TypeError) as exc:
                if conn is not None:
                    try:
                        conn.rollback()
                    except sqlite3.Error:
                        pass
                failures[name] = {"error": "clear_failed", "details": type(exc).__name__}
            finally:
                if conn is not None:
                    try:
                        conn.close()
                    except sqlite3.Error:
                        failures.setdefault(name, {"error": "clear_failed", "details": "close_failed"})
        result = _settings_payload(info, actor_id, paths)
        return {"status": "partial" if failures else "cleared", "operation_id": operation_id,
                "deleted_event_count": total_deleted,
                "partitions": cleared, "failures": failures, "settings": result,
                "research_data_retained": True}

    @app.get("/api/tasks/{task_id}/trace/summary")
    def get_summary(task_id: str, user: dict = Depends(require_user)):
        info, actor_id, paths = context(task_id, user, "trace:read")
        return _summary_payload(info, actor_id, paths)

    @app.get("/api/tasks/{task_id}/trace/events")
    def get_events(
        task_id: str,
        limit: int = Query(default=50, ge=1, le=MAX_PAGE_SIZE),
        cursor: str | None = Query(default=None, max_length=4096),
        stage: str | None = Query(default=None, max_length=40),
        action: str | None = Query(default=None, max_length=80),
        subject_id: str | None = Query(default=None, max_length=500),
        since: str | None = Query(default=None, max_length=80),
        until: str | None = Query(default=None, max_length=80),
        user: dict = Depends(require_user),
    ):
        info, actor_id, paths = context(task_id, user, "trace:read")
        if stage is not None and stage not in {*_STAGES, "control"}:
            raise HTTPException(400, "Unknown trace stage.")
        filters = _canonical_filters({"stage": stage, "action": action, "subject_id": subject_id,
                                      "since": since, "until": until})
        decoded = _decode_cursor(cursor, filters, task_id=str(info.task_id), actor_id=str(actor_id))
        unique = _unique_paths(paths)
        expected_parts = [name for name, _ in unique]
        if decoded and (set(decoded["positions"]) != set(expected_parts)
                        or set(decoded["cutoffs"]) != set(expected_parts)):
            raise HTTPException(409, "Trace partitions changed; restart pagination.")
        positions = decoded["positions"] if decoded else {name: None for name in expected_parts}
        cutoffs = decoded["cutoffs"] if decoded else {name: None for name in expected_parts}
        fetched: dict[str, dict[str, Any]] = {}
        all_rows = []
        for name in expected_parts:
            with closing(db._connect(paths[name])) as conn:
                page = research_trace.list_events(
                    conn, str(info.task_id), actor_id, limit=limit, cursor=positions.get(name),
                    stage=stage, action=action, subject_id=subject_id, since=since, until=until,
                    snapshot_sequence=cutoffs.get(name),
                )
            fetched[name] = page
            cutoffs[name] = page["snapshot_sequence"]
            for event in page["events"]:
                all_rows.append((event["recorded_at"], name, event))
        all_rows.sort(key=lambda row: (row[0], row[1], row[2]["sequence"]))
        selected = all_rows[:limit]
        result_rows = []
        for _, name, event in selected:
            positions[name] = event["sequence"]
            result_rows.append({**event, "partition": name})
        _attach_subject_titles(paths, result_rows)
        has_more = len(all_rows) > len(selected) or any(page["has_more"] for page in fetched.values())
        next_cursor = None
        if has_more:
            next_cursor = _encode_cursor({"v": 1, "filters": filters, "task_id": str(info.task_id), "actor_id": str(actor_id),
                                          "positions": positions, "cutoffs": cutoffs})
        return {"events": result_rows, "limit": limit, "next_cursor": next_cursor,
                "has_more": has_more, "snapshot_sequences": cutoffs}

    @app.get("/api/tasks/{task_id}/trace/events/{event_id}")
    def get_event(task_id: str, event_id: str, user: dict = Depends(require_user)):
        info, actor_id, paths = context(task_id, user, "trace:read")
        for name, path in _unique_paths(paths):
            with closing(db._connect(path)) as conn:
                event = research_trace.get_event(conn, str(info.task_id), actor_id, event_id)
            if event is not None:
                row = {**event, "partition": name}
                _attach_subject_titles(paths, [row])
                return row
        raise HTTPException(404, "Trace event not found.")

    def export_package(task_id: str, preset: str, author_alias: str, language: str,
                       included_private_fields: list[str],
                       filters: TraceExportFilters, info: Any,
                       actor_id: int | str, paths: dict[str, Any]) -> bytes:
        snapshot = _snapshot(info, actor_id, paths, filters)
        selected_private_fields = sorted(set(included_private_fields)) if preset == "public" else []
        snapshot["selection"]["included_private_fields"] = selected_private_fields
        try:
            return build_export_zip(snapshot, public=preset == "public", author_alias=author_alias,
                                    language=language, include_private_fields=selected_private_fields)
        except TracePackageError as exc:
            raise HTTPException(413, str(exc)) from exc

    @app.post("/api/tasks/{task_id}/trace/exports/preview", dependencies=[Depends(_body_within_limit)])
    def export_preview(task_id: str, body: TraceExportIn, user: dict = Depends(require_user)):
        info, actor_id, paths = context(task_id, user, "trace:read")
        preset = body.preset or "public"
        package = export_package(task_id, preset, body.author_alias, body.language,
                                 body.included_private_fields,
                                 body.filters, info, actor_id, paths)
        preview_id = _cache_preview(str(info.task_id), str(actor_id), package, preset)
        return _package_preview(package, preview_id)

    @app.post("/api/tasks/{task_id}/trace/exports", dependencies=[Depends(_body_within_limit)])
    def create_export(task_id: str, body: TraceExportIn, user: dict = Depends(require_user)) -> Response:
        info, actor_id, paths = context(task_id, user, "trace:read")
        if body.preview_id:
            package = _take_preview(body.preview_id, str(info.task_id), str(actor_id))
            if package is None:
                raise HTTPException(404, "Export preview expired or belongs to another actor/task.")
            try:
                mode = validate_import_package(package)["manifest"]["privacy_mode"]
            except TracePackageError as exc:
                raise HTTPException(500, "The export preview could not be verified. Generate a new preview and try again.") from exc
        else:
            mode = body.preset or "public"
            package = export_package(task_id, mode, body.author_alias, body.language,
                                     body.included_private_fields,
                                     body.filters, info, actor_id, paths)
        filename = f"research_trace_{mode}.zip"
        return Response(package, media_type="application/zip", headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        })

    @app.post("/api/tasks/{task_id}/trace/imports/preview", dependencies=[Depends(_body_within_limit)])
    async def import_preview(
        task_id: str,
        package_file: UploadFile = File(..., alias="package"),
        user: dict = Depends(require_user),
    ):
        info, actor_id, paths = context(task_id, user, "trace:write")
        raw = _bounded_upload(await package_file.read(MAX_PACKAGE_BYTES + 1))
        try:
            parsed = validate_import_package(raw)
        except TracePackageError as exc:
            raise HTTPException(400, {"error_code": "invalid_trace_package", "message": str(exc)}) from exc
        personal_path = paths.get("personal")
        if personal_path is None:
            raise HTTPException(409, "Personal trace records cannot be imported into this task right now.")
        return _import_metadata(info, actor_id, parsed, personal_path,
                                 {"shared": paths.get("shared"), "personal": personal_path})

    @app.post("/api/tasks/{task_id}/trace/imports/commit", dependencies=[Depends(_body_within_limit)])
    async def import_commit(
        task_id: str,
        preview_sha256: str = Form(..., min_length=64, max_length=64),
        actor_aliases: str = Form(..., max_length=20_000),
        subject_mappings: str = Form(default="{}", max_length=2_000_000),
        confirm_unmapped: bool = Form(default=False),
        package_file: UploadFile = File(..., alias="package"),
        user: dict = Depends(require_user),
    ):
        info, actor_id, paths = context(task_id, user, "trace:write")
        raw = _bounded_upload(await package_file.read(MAX_PACKAGE_BYTES + 1))
        try:
            parsed = validate_import_package(raw)
        except TracePackageError as exc:
            raise HTTPException(400, {"error_code": "invalid_trace_package", "message": str(exc)}) from exc
        if not hmac.compare_digest(parsed["package_sha256"], preview_sha256.lower()):
            raise HTTPException(409, "Uploaded package differs from the reviewed preview.")
        personal_path = paths.get("personal")
        if personal_path is None:
            raise HTTPException(409, "Personal trace records cannot be imported into this task right now.")
        aliases = _parse_json_form(actor_aliases, "actor_aliases")
        mappings = _parse_json_form(subject_mappings, "subject_mappings")
        metadata = _import_metadata(info, actor_id, parsed, personal_path,
                                    {"shared": paths.get("shared"), "personal": personal_path})
        required_aliases = set(metadata["source_aliases"])
        if set(aliases) != required_aliases or any(
            not isinstance(label, str) or not label.strip() or len(label) > 200
            for label in aliases.values()
        ):
            raise HTTPException(400, "Confirm a display alias for each source actor.")
        subjects = {row["subject_id"]: row["stages"] for row in metadata["subjects"]}
        if set(mappings) - set(subjects):
            raise HTTPException(400, "Subject mapping contains an ID absent from the package.")
        if len(subjects) > 20_000 or len(mappings) > 20_000:
            raise HTTPException(413, "Trace package has too many subject mappings.")
        unmapped = set(subjects) - set(mappings)
        if unmapped and not confirm_unmapped:
            raise HTTPException(400, "Explicitly confirm that unmapped source subjects will be retained as unlinked.")
        if mappings and validate_subject is None:
            raise HTTPException(400, "We cannot confirm that the source reference belongs to this task. You can keep it unlinked and continue.")
        for source_subject, target_subject in mappings.items():
            if not isinstance(source_subject, str) or not isinstance(target_subject, str) or not target_subject.strip():
                raise HTTPException(400, "Subject mappings must map text IDs to text IDs.")
            if len(target_subject) > 500:
                raise HTTPException(400, "Mapped subject ID is too long.")
            for stage in subjects[source_subject]:
                if validate_subject(info, stage, target_subject) is not True:
                    raise HTTPException(400, f"Mapped subject is not in this task for stage {stage}.")
        conflicts = []
        imported = 0
        duplicates = 0
        with closing(db._connect(personal_path)) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            for event in parsed["events"]:
                origin_digest = research_trace.origin_event_sha256(event)
                existing = research_trace.lookup_import(
                    conn, task_id=str(info.task_id), actor_id=actor_id,
                    origin_store_id=str(event["store_id"]),
                    origin_event_id=str(event["event_id"]), origin_sha256=origin_digest,
                )
                if existing and existing["status"] == "conflict":
                    conflicts.append({"store_id": event["store_id"], "event_id": event["event_id"]})
                    continue
                if existing and existing["status"] == "duplicate":
                    duplicates += 1
                    continue
                actor_key = str(event.get("actor_id", event.get("actor_alias", "")))
                source_alias = aliases[actor_key]
                mapped_subject = (mappings.get(str(event["subject_id"]))
                                  if event.get("stage") != "control" else None)
                try:
                    result = research_trace.import_event(
                        conn, task_id=str(info.task_id), importer_actor_id=actor_id,
                        event=event, mapped_subject_id=mapped_subject, source_alias=source_alias,
                    )
                except (ValueError, research_trace.TraceConflict) as exc:
                    raise HTTPException(400, {"error_code": "trace_import_rejected", "message": str(exc)}) from exc
                if result["status"] == "conflict":
                    conflicts.append({"store_id": event["store_id"], "event_id": event["event_id"]})
                elif result["status"] == "duplicate":
                    duplicates += 1
                else:
                    imported += 1
            if conflicts:
                conn.rollback()
                raise HTTPException(409, {"error_code": "trace_import_conflict", "conflicts": conflicts})
        return {"status": "committed", "event_count": len(parsed["events"]),
                "imported": imported, "duplicates": duplicates,
                "unmapped_subjects": len(unmapped), "mapped_subjects": len(mappings),
                "decisions_changed": False}
