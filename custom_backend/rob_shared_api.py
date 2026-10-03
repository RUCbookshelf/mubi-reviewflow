"""Task-shared manual RoB records anchored to one canonical effect store."""

from __future__ import annotations

import json
from contextlib import closing
from typing import Any, Callable

from fastapi import Depends, HTTPException, Query
from fastapi.responses import Response

from coscreen import db, review_analysis
from coscreen.analysis_storage import load_analysis_run
from coscreen.risk_of_bias_contract import RobContractError, build_chart_data, from_legacy_row
from coscreen.rob_storage import list_records, save_consensus, save_record
from coscreen.rob_v2_sensitivity import RobV2SensitivityError, analyze
from custom_backend import auth
from custom_backend.analysis_audit_report import audit_response


def register_rob_shared_routes(
    app: Any, require_user: Callable[..., dict], task_or_404: Callable[[str], Any],
    shared_db: Callable[[Any], Any], authorize: Callable[[Any, dict, str], bool],
    contract_error: Callable[[RobContractError], HTTPException],
) -> None:
    """Register shared RoB routes; the host supplies canonical owner DB and ACL."""

    def bad(status: int, code: str, message: str) -> HTTPException:
        return HTTPException(status, {"error_code": code, "message_key": "analysis.rob." + code,
                                      "params": {}, "details": message, "message": message})

    def context(task_id: str, user: dict, action: str):
        info = task_or_404(task_id)
        if authorize(info, user, action) is not True:
            raise bad(403, "rob_access_denied", "Task RoB access denied.")
        return info, shared_db(info)

    @app.post("/api/tasks/{task_id}/analysis/risk-of-bias/v2/shared", status_code=201)
    def create_independent(task_id: str, body: dict, user: dict = Depends(require_user)):
        _, path = context(task_id, user, "coding:submit")
        with closing(db._connect(path)) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                return save_record(conn, body, actor_id=user["username"], expected_revision=0)
            except RobContractError as exc:
                raise contract_error(exc) from exc

    @app.patch("/api/tasks/{task_id}/analysis/risk-of-bias/v2/shared/{record_id}")
    def revise_independent(task_id: str, record_id: str, body: dict,
                           expected_revision: int = Query(ge=1),
                           user: dict = Depends(require_user)):
        _, path = context(task_id, user, "coding:submit")
        with closing(db._connect(path)) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                return save_record(conn, body, actor_id=user["username"],
                                   record_id=record_id, expected_revision=expected_revision)
            except RobContractError as exc:
                raise contract_error(exc) from exc

    @app.get("/api/tasks/{task_id}/analysis/risk-of-bias/v2/shared")
    def list_shared(task_id: str, user: dict = Depends(require_user)):
        info = task_or_404(task_id)
        can_reconcile = authorize(info, user, "consensus:read") is True
        if not can_reconcile and authorize(info, user, "coding:submit") is not True:
            raise bad(403, "rob_access_denied", "Task RoB access denied.")
        path = shared_db(info)
        with closing(db._connect(path)) as conn:
            records = list_records(conn)
        if not can_reconcile:
            return {"records": [row for row in records
                                if row.get("record_kind") == "independent"
                                and row.get("reviewer_id") == user["username"]],
                    "legacy_pending": []}
        return {"records": records,
                "legacy_pending": [from_legacy_row(row)
                                   for row in review_analysis.list_rob(path)]}

    def save_shared_consensus(task_id: str, body: dict, user: dict,
                              record_id: str | None, expected_revision: int | None):
        _, path = context(task_id, user, "consensus:write")
        if not isinstance(body, dict) or set(body) != {"assessment", "source_record_ids"}:
            raise bad(400, "invalid_consensus_request", "Consensus needs assessment and source_record_ids.")
        ids = body["source_record_ids"]
        if not isinstance(ids, list) or not 2 <= len(ids) <= 20 or \
                any(not isinstance(value, str) or not value for value in ids):
            raise bad(400, "invalid_consensus_sources", "Select 2 to 20 source record IDs.")
        with closing(db._connect(path)) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            records, owners = [], {}
            for source_id in ids:
                row = conn.execute("SELECT record_json FROM rob_assessments WHERE record_id=?",
                                   (source_id,)).fetchone()
                if row is None:
                    raise bad(400, "source_assessment_missing", "A source assessment is absent from this task.")
                record = json.loads(row[0])
                account = auth.get_user(record.get("reviewer_id", ""))
                if account is None or account["username"] != record.get("reviewer_id"):
                    raise bad(400, "source_assessor_missing", "Source assessor identity is unavailable.")
                records.append(record)
                owners[source_id] = account["username"]
            try:
                return save_consensus(
                    conn, body["assessment"], source_record_ids=ids,
                    source_records=records, authenticated_source_owners=owners,
                    reconciler_id=user["username"], record_id=record_id,
                    expected_revision=expected_revision)
            except RobContractError as exc:
                raise contract_error(exc) from exc

    @app.post("/api/tasks/{task_id}/analysis/risk-of-bias/v2/shared/consensus", status_code=201)
    def create_consensus(task_id: str, body: dict, user: dict = Depends(require_user)):
        return save_shared_consensus(task_id, body, user, None, 0)

    @app.patch("/api/tasks/{task_id}/analysis/risk-of-bias/v2/shared/consensus/{record_id}")
    def revise_consensus(task_id: str, record_id: str, body: dict,
                         expected_revision: int = Query(ge=1),
                         user: dict = Depends(require_user)):
        return save_shared_consensus(task_id, body, user, record_id, expected_revision)

    @app.post("/api/tasks/{task_id}/analysis/risk-of-bias/v2/shared/chart")
    def shared_chart(task_id: str, body: dict, user: dict = Depends(require_user)):
        _, path = context(task_id, user, "consensus:read")
        refs = body.get("result_refs")
        if not isinstance(refs, list) or not 1 <= len(refs) <= 500:
            raise bad(400, "result_selection_required", "Select 1 to 500 exact result references.")
        effects = []
        with closing(db._connect(path)) as conn:
            for ref in refs:
                try:
                    result_id, version_id = int(ref["result_id"]), int(ref["effect_version_id"])
                except (KeyError, TypeError, ValueError):
                    raise bad(400, "invalid_result_ref", "Invalid result reference.") from None
                if not (0 < result_id < 2**63 and 0 < version_id < 2**63):
                    raise bad(400, "invalid_result_ref", "Result reference is out of range.")
                row = conn.execute(
                    "SELECT result_id,effect_version_id,study_id,comparison,outcome,timepoint "
                    "FROM review_effect_versions WHERE result_id=? AND effect_version_id=?",
                    (result_id, version_id)).fetchone()
                if row is None:
                    raise bad(400, "effect_version_unavailable", "An exact result version is unavailable.")
                effects.append(dict(zip(("result_id", "effect_version_id", "study_id",
                                         "comparison", "outcome", "timepoint"), row)))
            records = list_records(conn)
        records += [from_legacy_row(row) for row in review_analysis.list_rob(path)]
        try:
            return build_chart_data(
                records, effects, framework=body["framework"],
                tool_version=body["tool_version"], variant=body["variant"],
                selector=body["selector"], result_refs=refs,
                statistical_unit=body["statistical_unit"])
        except RobContractError as exc:
            raise contract_error(exc) from exc
        except (KeyError, TypeError, ValueError) as exc:
            raise bad(400, "invalid_chart_request", str(exc)) from exc

    @app.post("/api/tasks/{task_id}/analysis/risk-of-bias/v2/shared/sensitivity")
    def shared_sensitivity(task_id: str, body: dict, user: dict = Depends(require_user)):
        _, path = context(task_id, user, "consensus:read")
        try:
            return analyze(path, **body)
        except RobV2SensitivityError as exc:
            raise HTTPException(400, {"error_code": exc.code,
                                      "message_key": "analysis.rob." + exc.code,
                                      "params": exc.details, "details": str(exc),
                                      "message": str(exc)}) from exc
        except TypeError as exc:
            raise bad(400, "invalid_sensitivity_request", "Invalid sensitivity request.") from exc

    @app.get("/api/tasks/{task_id}/analysis/risk-of-bias/v2/shared/runs/{run_id}")
    def shared_run(task_id: str, run_id: str, user: dict = Depends(require_user)):
        _, path = context(task_id, user, "consensus:read")
        with closing(db._connect(path)) as conn:
            run = load_analysis_run(conn, run_id)
        if run is None or run["specification"].get("analysis_type") != "rob_v2_exclusion_sensitivity":
            raise bad(404, "analysis_run_not_found", "Shared RoB sensitivity run not found.")
        return run

    @app.get("/api/tasks/{task_id}/analysis/risk-of-bias/v2/shared/runs/{run_id}/export")
    def shared_run_export(task_id: str, run_id: str,
                          format: str, user: dict = Depends(require_user)) -> Response:
        if format not in {"csv", "tex", "docx", "pptx"}:
            raise bad(400, "invalid_export_format", "Unsupported saved-run export format.")
        run = shared_run(task_id, run_id, user)
        return audit_response(run, format, "Saved RoB exclusion sensitivity run",
                              filename=f"reviewflow_run_{run_id}")
