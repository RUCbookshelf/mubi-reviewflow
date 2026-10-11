"""Personal workspace Undo endpoints; reuse the application's access checks."""
from __future__ import annotations

import sqlite3
from typing import Annotated, Literal

from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field

from coscreen import coding, tasks, workspace_undo
from coscreen.research_trace import TraceContext

Stage = Literal["screen", "fulltext", "coding"]


class UndoIn(BaseModel):
    entry_id: int = Field(ge=1, le=2**53 - 1)


class CodingBatchIn(BaseModel):
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
            coding.save_coding_values_batch(db, body.zotero_key, body.values, trace_context=context)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except sqlite3.IntegrityError as exc:
            raise HTTPException(400, "维度或文献不存在（请确认后重试）。") from exc
        tasks.touch_task(info.task_id, data_dir)
        return {"saved": True, "progress": coding.coding_progress(db)}
