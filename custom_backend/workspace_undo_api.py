"""Personal workspace Undo endpoints; reuse the application's access checks."""
from __future__ import annotations

import sqlite3
from typing import Annotated, Literal

from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field

from coscreen import coding, tasks, workspace_undo
from coscreen import review_workflow as workflow_store
from contextlib import closing
from coscreen.research_trace import TraceContext

Stage = Literal["screen", "fulltext", "coding"]


class UndoIn(BaseModel):
    entry_id: int = Field(ge=1, le=2**53 - 1)
    expected_policy_version: int | None = Field(default=None, ge=1)


class CodingBatchIn(BaseModel):
    expected_policy_version: int | None = Field(default=None, ge=1)
    zotero_key: str = Field(min_length=1, max_length=300)
    values: dict[Annotated[int, Field(ge=1, le=10**9)], Annotated[str, Field(max_length=4000)]] = Field(
        min_length=1, max_length=1000)


def register_workspace_undo_routes(app, require_user, task_lookup, screener_db, trace_context, data_dir) -> None:
    @app.get("/api/tasks/{task_id}/undo/{stage}")
    def status(task_id: str, stage: Stage, screener: str | None = None,
               user: dict = Depends(require_user)) -> dict:
        info = task_lookup(task_id)
        db = screener_db(info, screener or user["username"])
        context = TraceContext(task_id=info.task_id, actor_id=user["user_id"])
        return {"latest": workspace_undo.latest(db, context, stage)}

    @app.post("/api/tasks/{task_id}/undo/{stage}")
    def restore(task_id: str, stage: Stage, body: UndoIn, screener: str | None = None,
                user: dict = Depends(require_user)) -> dict:
        info = task_lookup(task_id)
        db = screener_db(info, screener or user["username"])
        context = trace_context(info, user, source="undo")
        if context is None:
            raise HTTPException(503, "Undo is unavailable. Please try again.")
        try:
            runtime = app.state.review_workflow
            with runtime.lock(task_id):
                current = runtime.context(task_id, user)
                if current.actor_db.resolve() != db.resolve():
                    raise HTTPException(403, "Undo is limited to your own workspace.")
                with closing(workflow_store.connect(db, read_only=True)) as conn:
                    entry = conn.execute("SELECT zotero_key FROM workspace_undo WHERE id=? AND task_id=? AND actor_id=? AND stage=?",
                                         (body.entry_id, task_id, str(user["user_id"]), stage)).fetchone() if workflow_store.has_table(conn, "workspace_undo") else None
                if entry is None:
                    raise HTTPException(409, "There is no saved operation to undo.")
                with runtime.write_guard(task_id, user, stage, entry[0], body.expected_policy_version):
                    # Restoration may contain historical Maybe; existing Undo
                    # snapshots already reject changed downstream work.
                    result = workspace_undo.undo(db, context, stage, body.entry_id)
        except (ValueError, sqlite3.IntegrityError) as exc:
            message = str(exc) if isinstance(exc, ValueError) else "The saved result or downstream work has changed. Undo cannot overwrite newer changes."
            raise HTTPException(409, message) from exc
        tasks.touch_task(info.task_id, data_dir)
        return result

    @app.post("/api/tasks/{task_id}/coding/values-batch")
    def save_form(task_id: str, body: CodingBatchIn, screener: str | None = None,
                  user: dict = Depends(require_user)) -> dict:
        info = task_lookup(task_id)
        db = screener_db(info, screener or user["username"])
        context = trace_context(info, user)
        if context is None:
            raise HTTPException(503, "Undo is unavailable. Please try again.")
        try:
            with app.state.review_workflow.write_guard(task_id, user, "coding", body.zotero_key,
                                                       body.expected_policy_version):
                coding.save_coding_values_batch(db, body.zotero_key, body.values, trace_context=context)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except sqlite3.IntegrityError as exc:
            raise HTTPException(400, "维度或文献不存在（请确认后重试）。") from exc
        tasks.touch_task(info.task_id, data_dir)
        return {"saved": True, "progress": coding.coding_progress(db)}
