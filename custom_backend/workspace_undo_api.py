"""Personal workspace Undo endpoints; reuse the application's access checks."""
from __future__ import annotations

import sqlite3
from typing import Annotated, Literal

from fastapi import Depends, HTTPException, File, Form, UploadFile
from fastapi.responses import FileResponse
from pathlib import Path
from pydantic import BaseModel, Field

from coscreen import coding, coding_evidence, tasks, workspace_undo
from coscreen import review_workflow as workflow_store
from contextlib import closing
from coscreen.research_trace import TraceContext

Stage = Literal["screen", "fulltext", "coding"]


class UndoIn(BaseModel):
    entry_id: int = Field(ge=1, le=2**53 - 1)
    expected_policy_version: int | None = Field(default=None, ge=1)


class EvidenceIn(BaseModel):
    keep_image_ids: list[Annotated[int, Field(ge=1)]] = Field(default_factory=list, max_length=100)
    upload_ids: list[Annotated[str, Field(max_length=80)]] = Field(default_factory=list, max_length=100)


class CodingBatchIn(BaseModel):
    expected_policy_version: int | None = Field(default=None, ge=1)
    zotero_key: str = Field(min_length=1, max_length=300)
    values: dict[Annotated[int, Field(ge=1, le=10**9)], Annotated[str, Field(max_length=4000)]] = Field(
        default_factory=dict, max_length=1000)
    notes: dict[Annotated[int, Field(ge=1, le=10**9)], Annotated[str, Field(max_length=100000)]] | None = Field(default=None, max_length=1000)
    evidence: dict[Annotated[int, Field(ge=1, le=10**9)], EvidenceIn] | None = Field(default=None, max_length=1000)
    draft: bool = False


def register_workspace_undo_routes(app, require_user, task_lookup, screener_db, trace_context, data_dir) -> None:
    @app.post('/api/tasks/{task_id}/coding/evidence/uploads')
    async def upload(task_id: str, zotero_key: str = Form(..., min_length=1, max_length=300),
                     dim_id: int = Form(..., ge=1), file: UploadFile = File(...),
                     user: dict = Depends(require_user)) -> dict:
        data = await file.read(coding_evidence.MAX_UPLOAD_BYTES + 1)
        await file.close()
        try:
            with app.state.review_workflow.write_guard(task_id, user, 'coding', zotero_key) as ctx:
                return coding_evidence.stage_upload(ctx.actor_db, task_id, user['user_id'], zotero_key, dim_id, data)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    def image_file(task_id: str, identity: str | int, staged: bool, user: dict):
        runtime = app.state.review_workflow
        with runtime.lock(task_id):
            ctx = runtime.context(task_id, user)
            with closing(workflow_store.connect(ctx.actor_db, read_only=True)) as conn:
                if staged:
                    row = conn.execute('SELECT path,zotero_key FROM workflow_evidence_uploads WHERE id=? AND task_id=? AND actor_id=?',
                                       (identity, task_id, str(user['user_id']))).fetchone()
                else:
                    row = conn.execute('SELECT i.path,n.zotero_key FROM coding_note_images i JOIN coding_notes n ON n.id=i.note_id WHERE i.id=?', (identity,)).fetchone()
            if row is None or runtime.article_row(ctx.actor_db, row[1]) is None:
                raise HTTPException(404, 'Evidence image not found.')
            path = Path(row[0])
            private = coding_evidence.assets_dir(ctx.actor_db)
            legacy = ctx.actor_db.parent.resolve() / coding.CODING_ASSETS_DIRNAME
            # Legacy rows may reference the old common assets directory. Only an
            # authenticated actor's own association permits those exact paths.
            if path.is_symlink() or path.resolve().parent not in ({private} if staged else {private, legacy}) or not path.is_file():
                raise HTTPException(404, 'Evidence image not found.')
            media_type = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp'}.get(path.suffix.lower())
            if media_type is None:
                raise HTTPException(404, 'Evidence image not found.')
            return FileResponse(path, media_type=media_type, headers={'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff'})

    @app.get('/api/tasks/{task_id}/coding/evidence/uploads/{upload_id}')
    def preview(task_id: str, upload_id: str, user: dict = Depends(require_user)):
        return image_file(task_id, upload_id, True, user)

    @app.get('/api/tasks/{task_id}/coding/evidence/images/{image_id}')
    def image(task_id: str, image_id: int, user: dict = Depends(require_user)):
        return image_file(task_id, image_id, False, user)

    @app.get("/api/tasks/{task_id}/undo/{stage}")
    def status(task_id: str, stage: Stage, screener: str | None = None,
               user: dict = Depends(require_user)) -> dict:
        info = task_lookup(task_id)
        db = screener_db(info, screener or user["username"])
        if app.state.review_workflow.context(task_id, user).actor_db.resolve() != db.resolve():
            raise HTTPException(403, 'Undo is limited to your own workspace.')
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
                                                       body.expected_policy_version) as ctx:
                if ctx.actor_db.resolve() != db.resolve():
                    raise HTTPException(403, 'Coding evidence is limited to your own workspace.')
                coding_evidence.save_form(db, body.zotero_key, body.values, notes=body.notes,
                    evidence={did: value.model_dump() for did, value in body.evidence.items()} if body.evidence is not None else None,
                    trace_context=context)
                result = app.state.review_workflow.coding_result(ctx, body.zotero_key, body.draft)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except sqlite3.IntegrityError as exc:
            raise HTTPException(400, "维度或文献不存在（请确认后重试）。") from exc
        tasks.touch_task(info.task_id, data_dir)
        return result
