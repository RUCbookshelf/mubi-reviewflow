"""Task API routes for persistent independent coding and consensus.

The host must supply a task-scoped shared DB path and an authorization callback.
The existing per-user screener DB is not a shared consensus store.
"""

from typing import Any, Callable

from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from coscreen import coding_consensus
from coscreen.coding_agreement import summarize_agreement


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


#: 编码共识端点的请求体大小上限（2MB；字段本身有 max_length，此处挡无界
#: value/source/ID 列表的整包放大）。分块传输无 Content-Length 时不设卡。
MAX_CONSENSUS_BODY_BYTES = 2 * 1024 * 1024


async def _body_within_limit(request: Request) -> None:
    raw = request.headers.get("content-length")
    try:
        length = int(raw) if raw is not None else None
    except ValueError:
        length = None
    if length is not None and length > MAX_CONSENSUS_BODY_BYTES:
        raise HTTPException(413, "Request body exceeds the coding consensus limit (2 MB).")


class RaterRecordIn(_Input):
    item_key: str = Field(min_length=1, max_length=300)
    field_key: str = Field(min_length=1, max_length=200)
    value: Any
    source: dict[str, Any]
    rationale: str = Field(default="", max_length=4000)


class ConsensusRecordIn(RaterRecordIn):
    rationale: str = Field(min_length=1, max_length=4000)
    rater_record_ids: list[int] = Field(default_factory=list, max_length=10000)


class FreezeIn(_Input):
    name: str = Field(min_length=1, max_length=200)
    consensus_ids: list[int] = Field(min_length=1, max_length=10000)


class AgreementIn(_Input):
    field_key: str = Field(min_length=1, max_length=200)
    rater_ids: tuple[str, str]
    absolute_tolerance: Any = None


def register_coding_consensus_routes(
    app: Any,
    require_user: Callable[..., dict],
    task_or_404: Callable[[str], Any],
    store_path: Callable[[Any], str],
    authorize_task: Callable[[Any, dict, str], bool],
    validate_target: Callable[[Any, str, str], bool],
    bad: Callable[[Any], HTTPException],
    validate_value: Callable[[Any, str, Any], dict | None] | None = None,
    get_field: Callable[[Any, str], dict | None] | None = None,
    trace_context_factory: Callable[..., Any] | None = None,
) -> None:
    """Register routes; `authorize_task` must return True or the request is denied.

    Actions are `coding:submit`, `coding:read_all`, `consensus:write`,
    `consensus:read`, and `consensus:freeze`. `store_path` must return the
    shared task DB selected by the host. `validate_target` checks that the item
    and stable field key belong to the task. Actor IDs always come from `require_user`.
    """

    def context(
        task_id: str, user: dict, action: str,
        item_key: str | None = None, field_key: str | None = None,
    ):
        info = task_or_404(task_id)
        if authorize_task(info, user, action) is not True:
            raise HTTPException(403, "Task access denied.")
        if item_key is not None and field_key is not None and validate_target(info, item_key, field_key) is not True:
            raise HTTPException(400, "Coding item or field does not belong to this task.")
        if "user_id" not in user:
            raise HTTPException(401, "Authenticated user ID is missing.")
        return info, store_path(info), user["user_id"]

    @app.post("/api/tasks/{task_id}/coding/records", status_code=201, dependencies=[Depends(_body_within_limit)])
    def append_rater_record(task_id: str, body: RaterRecordIn, user: dict = Depends(require_user)):
        info, path, actor_id = context(task_id, user, "coding:submit", body.item_key, body.field_key)
        field = validate_value(info, body.field_key, body.value) if validate_value else None
        if validate_value and not isinstance(field, dict):
            raise HTTPException(400, "Value does not match the canonical coding field.")
        try:
            return coding_consensus.append_rater_record(
                path, item_key=body.item_key, field_key=body.field_key, rater_id=actor_id,
                value=body.value, source={**body.source, "_reviewflow_field": field} if field else body.source,
                rationale=body.rationale,
                trace_context=trace_context_factory(info, user) if trace_context_factory else None,
            )
        except ValueError as exc:
            raise bad(str(exc)) from exc

    @app.get("/api/tasks/{task_id}/coding/records")
    def list_rater_records(
        task_id: str, item_key: str, field_key: str, rater_id: str | None = None,
        user: dict = Depends(require_user),
    ):
        info, path, _ = context(task_id, user, "coding:read_all", item_key, field_key)
        try:
            return {"records": coding_consensus.list_rater_records(
                path, item_key=item_key, field_key=field_key, rater_id=rater_id
            )}
        except ValueError as exc:
            raise bad(str(exc)) from exc

    @app.post("/api/tasks/{task_id}/coding/agreement", dependencies=[Depends(_body_within_limit)])
    def agreement(task_id: str, body: AgreementIn, user: dict = Depends(require_user)):
        info, path, _ = context(task_id, user, "coding:read_all")
        field = get_field(info, body.field_key) if get_field else None
        if field is None:
            raise HTTPException(400, "Coding field does not belong to this task.")
        field_type = ("multiselect" if field["multi_select"] else "categorical") \
            if field["dtype"] == "choice" else field["dtype"]
        try:
            records = coding_consensus.latest_rater_records_for_field(
                path, field_key=body.field_key, rater_ids=body.rater_ids)
            if any(record["source"].get("_reviewflow_field") != field for record in records):
                raise HTTPException(409, {"error_code": "coding_field_revision_mismatch",
                                          "message_key": "coding.field_revision_mismatch", "params": {},
                                          "details": "Coding field changed; select revisions under one field definition.",
                                          "message": "Coding field changed; select revisions under one field definition."})
            result = summarize_agreement(
                records, body.rater_ids, field_type,
                absolute_tolerance=body.absolute_tolerance,
            )
            return {"field": field, **result}
        except ValueError as exc:
            raise bad(str(exc)) from exc

    @app.post("/api/tasks/{task_id}/coding/consensus", status_code=201, dependencies=[Depends(_body_within_limit)])
    def append_consensus(task_id: str, body: ConsensusRecordIn, user: dict = Depends(require_user)):
        info, path, actor_id = context(task_id, user, "consensus:write", body.item_key, body.field_key)
        field = validate_value(info, body.field_key, body.value) if validate_value else None
        if validate_value and not isinstance(field, dict):
            raise HTTPException(400, "Value does not match the canonical coding field.")
        try:
            return coding_consensus.append_consensus_record(
                path, item_key=body.item_key, field_key=body.field_key,
                reconciler_id=actor_id, value=body.value,
                source={**body.source, "_reviewflow_field": field} if field else body.source,
                rationale=body.rationale, rater_record_ids=body.rater_record_ids,
            )
        except ValueError as exc:
            raise bad(str(exc)) from exc

    @app.get("/api/tasks/{task_id}/coding/consensus")
    def list_consensus(task_id: str, item_key: str, field_key: str, user: dict = Depends(require_user)):
        info, path, _ = context(task_id, user, "consensus:read", item_key, field_key)
        try:
            return {"records": coding_consensus.list_consensus_records(
                path, item_key=item_key, field_key=field_key
            )}
        except ValueError as exc:
            raise bad(str(exc)) from exc

    @app.post("/api/tasks/{task_id}/coding/freezes", status_code=201, dependencies=[Depends(_body_within_limit)])
    def freeze(task_id: str, body: FreezeIn, user: dict = Depends(require_user)):
        info, path, actor_id = context(task_id, user, "consensus:freeze")
        try:
            return coding_consensus.freeze_consensus(
                path, name=body.name, frozen_by=actor_id, consensus_ids=body.consensus_ids
            )
        except ValueError as exc:
            raise bad(str(exc)) from exc

    @app.get("/api/tasks/{task_id}/coding/freezes")
    def list_freezes(task_id: str, name: str | None = None, user: dict = Depends(require_user)):
        info, path, _ = context(task_id, user, "consensus:read")
        try:
            return {"freezes": coding_consensus.list_freezes(path, name=name)}
        except ValueError as exc:
            raise bad(str(exc)) from exc

    @app.get("/api/tasks/{task_id}/coding/freezes/{freeze_id}")
    def get_freeze(task_id: str, freeze_id: int, user: dict = Depends(require_user)):
        info, path, _ = context(task_id, user, "consensus:read")
        freeze_record = coding_consensus.get_freeze(path, freeze_id)
        if freeze_record is None:
            raise HTTPException(404, "Coding freeze not found.")
        return freeze_record
