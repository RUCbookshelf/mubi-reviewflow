"""Personal workspace Undo endpoints; reuse the application's access checks."""
from __future__ import annotations

import sqlite3
import json
from typing import Annotated, Literal

from fastapi import Depends, HTTPException, File, Form, UploadFile
from fastapi.responses import FileResponse
from pathlib import Path
from pydantic import BaseModel, ConfigDict, Field

from coscreen import coding, coding_evidence, tasks, workspace_undo
from coscreen import review_workflow as workflow_store
from contextlib import closing
from coscreen.research_trace import TraceContext

Stage = Literal["screen", "fulltext", "coding"]


class UndoIn(BaseModel):
    entry_id: int = Field(ge=1, le=2**53 - 1)
    expected_policy_version: int | None = Field(default=None, ge=1)


class EvidenceIn(BaseModel):
    """单个维度的证据保存意图。

    r9 L3（b11·F3-23 的 API 面）：``keep_image_ids`` 缺省/``null`` = 保留该维度
    现有**全部**图片，与模块层 ``coding_evidence.save_form`` 的"不带该键即
    全部保留"同一读法。修复前默认空列表且上限 100：单备注累积 >100 张后既
    发不出 101 条（422），省略又按空集删光——"全部保留"根本无法表达。显式
    列表仍是精确集合语义（列出即保留这些、其余删除），上限保持 100 不变。
    """
    keep_image_ids: list[Annotated[int, Field(ge=1)]] | None = Field(default=None, max_length=100)
    upload_ids: list[Annotated[str, Field(max_length=80)]] = Field(default_factory=list, max_length=100)


def _evidence_intents(evidence: dict[int, EvidenceIn] | None) -> dict[int, dict] | None:
    """把请求模型转成模块层意图：``keep_image_ids`` 为 None 时**省略该键**。

    不能只把 None 传下去——模块层按"键是否存在"区分"全部保留"与"精确集合"，
    ``{'keep_image_ids': None}`` 会让 ``set(None)`` 抛 TypeError（500）。
    """
    if evidence is None:
        return None
    intents: dict[int, dict] = {}
    for did, value in evidence.items():
        intent = value.model_dump()
        if intent.get("keep_image_ids") is None:
            intent.pop("keep_image_ids", None)
        intents[did] = intent
    return intents


class CodingBatchIn(BaseModel):
    expected_policy_version: int | None = Field(default=None, ge=1)
    zotero_key: str = Field(min_length=1, max_length=300)
    values: dict[Annotated[int, Field(ge=1, le=10**9)], Annotated[str, Field(max_length=4000)]] = Field(
        default_factory=dict, max_length=1000)
    notes: dict[Annotated[int, Field(ge=1, le=10**9)], Annotated[str, Field(max_length=100000)]] | None = Field(default=None, max_length=1000)
    evidence: dict[Annotated[int, Field(ge=1, le=10**9)], EvidenceIn] | None = Field(default=None, max_length=1000)
    draft: bool = False


class SubmissionEvidenceIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_policy_version: int | None = Field(default=None, ge=1)
    zotero_key: str = Field(min_length=1, max_length=300)
    notes: dict[Annotated[int, Field(ge=1, le=10**9)], Annotated[str, Field(max_length=100000)]] | None = Field(default=None, max_length=1000)
    evidence: dict[Annotated[int, Field(ge=1, le=10**9)], EvidenceIn] | None = Field(default=None, max_length=1000)
    draft: bool = False


def register_workspace_undo_routes(app, require_user, task_lookup, screener_db, trace_context, data_dir) -> None:
    async def upload_evidence(task_id: str, zotero_key: str, dim_id: int, file: UploadFile,
                              user: dict, *, canonical_only: bool = False,
                              expected_version: int | None = None) -> dict:
        # R6-C3/#22：路由原始 task_id（Windows 尾点别名）统一为规范 id——
        # stage_upload 落库的 task_id 与 save_form 附着查询（trace_context 的
        # info.task_id）同键，别名路径上传的图片才能被表单保存附着。
        info = task_lookup(task_id)
        task_id = info.task_id
        data = await file.read(coding_evidence.MAX_UPLOAD_BYTES + 1)
        await file.close()
        try:
            runtime = app.state.review_workflow
            with runtime.write_guard(task_id, user, 'coding', zotero_key, expected_version,
                                     canonical_only=canonical_only) as ctx:
                if canonical_only:
                    runtime.validate_evidence_dimensions(ctx, {dim_id})
                return coding_evidence.stage_upload(ctx.actor_db, task_id, user['user_id'], zotero_key, dim_id, data)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post('/api/tasks/{task_id}/coding/evidence/uploads')
    async def upload(task_id: str, zotero_key: str = Form(..., min_length=1, max_length=300),
                     dim_id: int = Form(..., ge=1), file: UploadFile = File(...),
                     user: dict = Depends(require_user)) -> dict:
        return await upload_evidence(task_id, zotero_key, dim_id, file, user)

    @app.post('/api/tasks/{task_id}/coding/submission-evidence/uploads')
    async def submission_upload(task_id: str, zotero_key: str = Form(..., min_length=1, max_length=300),
                                dim_id: int = Form(..., ge=1), file: UploadFile = File(...),
                                expected_policy_version: int | None = Form(default=None, ge=1),
                                user: dict = Depends(require_user)) -> dict:
        return await upload_evidence(task_id, zotero_key, dim_id, file, user,
                                     canonical_only=True, expected_version=expected_policy_version)

    @app.get('/api/tasks/{task_id}/coding/submission-evidence')
    def submission_queue(task_id: str, source_id: str | None = None, user: dict = Depends(require_user)) -> dict:
        return app.state.review_workflow.submission_evidence_queue(task_id, user, source_id)

    @app.get('/api/tasks/{task_id}/coding/submission-evidence/{zotero_key}')
    def submission_item(task_id: str, zotero_key: str, user: dict = Depends(require_user)) -> dict:
        return app.state.review_workflow.work_item(task_id, user, 'coding', zotero_key,
                                                   submission_evidence=True)

    @app.post('/api/tasks/{task_id}/coding/submission-evidence')
    def save_submission_evidence(task_id: str, body: SubmissionEvidenceIn,
                                 user: dict = Depends(require_user)) -> dict:
        info = task_lookup(task_id)
        task_id = info.task_id  # R6-C3/#22：锁/守卫统一规范 id（别名同键）
        runtime = app.state.review_workflow
        try:
            with runtime.write_guard(task_id, user, 'coding', body.zotero_key,
                                     body.expected_policy_version, canonical_only=True) as ctx:
                dimensions = set(body.notes or {}) | set(body.evidence or {})
                runtime.validate_evidence_dimensions(ctx, dimensions)
                context = trace_context(ctx.info, user)
                if context is None:
                    raise HTTPException(503, 'Undo is unavailable. Please try again.')
                coding_evidence.save_form(ctx.actor_db, body.zotero_key, {}, notes=body.notes,
                    evidence=_evidence_intents(body.evidence),
                    trace_context=context, submission_evidence=True)
                result = runtime.coding_result(ctx, body.zotero_key, body.draft)
                result['access_context'] = workspace_undo.SUBMISSION_EVIDENCE
                result['submission_evidence'] = runtime.submission_evidence_capability(ctx)
        except (ValueError, sqlite3.IntegrityError) as exc:
            raise HTTPException(400, str(exc) if isinstance(exc, ValueError) else 'Evidence field or article is unavailable.') from exc
        tasks.touch_task(task_id, data_dir)
        return result

    def image_file(task_id: str, identity: str | int, staged: bool, user: dict):
        # R6-C3/#22：锁、ctx 与 staged 上传行查询统一规范 task_id（别名同键）。
        info = task_lookup(task_id)
        task_id = info.task_id
        runtime = app.state.review_workflow
        with runtime.lock(task_id):
            ctx = runtime.context(task_id, user)
            with closing(workflow_store.connect(ctx.actor_db, read_only=True)) as conn:
                if staged:
                    # r6 #28：尾点别名存量 staged 行同键兼容（规范 id 恒无尾点，
                    # rtrim 只影响修复前以别名落库的行）。
                    row = conn.execute("SELECT path,zotero_key FROM workflow_evidence_uploads WHERE id=? AND rtrim(task_id,'. ')=? AND actor_id=?",
                                       (identity, task_id.rstrip('. '), str(user['user_id']))).fetchone()
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
        # R6-U4/#24：状态查询整体只读——不建库、不跑自愈 DDL（写路径保持懒建）。
        # 库文件不存在时在触达 runtime.context（其自身的懒建链会重建属主库）
        # 之前直接返回空态：删除个人库后 GET /undo/* 不再把库重建出来。
        db = screener_db(info, screener or user["username"], ensure=False)
        if not db.is_file():
            # 无库空态：保留任务可见性口径（不得因省去 context 而放宽越权探测）。
            if not app.state.review_workflow.visible(info, user):
                raise HTTPException(403, "Undo is limited to your own workspace.")
            return {"latest": None, "undo_available": True}
        # 只读上下文：不建库、不跑迁移/ensure_fields（修复前 context() 会让成员
        # 请求顺带创建属主库并灌入整套表，且把删掉的列/表自愈补回）。
        if app.state.review_workflow.readonly_context(info.task_id, user).actor_db.resolve() != db.resolve():
            raise HTTPException(403, 'Undo is limited to your own workspace.')
        context = TraceContext(task_id=info.task_id, actor_id=user["user_id"])
        latest = workspace_undo.latest(db, context, stage)
        available = bool(latest.get('undo_available', True)) if latest else True
        if latest is not None and available:
            # R6-U5/#23：只读守卫预检——与 POST restore 的 snapshot_matches 拒绝
            # 同口径，下游工作已变更（必然 409 "downstream work has changed"）
            # 的条目不再标为可撤销。
            available = workspace_undo.guard_matches(db, context, stage, latest['entry_id'])
        if latest is not None and not available:
            # R6-U5/#25：文件缺失/守卫失配的条目置空 latest，前端撤销按钮禁用，
            # 409 后 refresh 即停止循环点击。
            latest = None
        return {"latest": latest, "undo_available": available}

    @app.post("/api/tasks/{task_id}/undo/{stage}")
    def restore(task_id: str, stage: Stage, body: UndoIn, screener: str | None = None,
                user: dict = Depends(require_user)) -> dict:
        info = task_lookup(task_id)
        # R6-C3/#22：预读 journal、任务锁、写守卫与 trace 一律用规范 task_id——
        # 尾点别名与规范 id 是同一物理任务，journal/锁/守卫必须同键（修复前
        # 别名预读 None -> 永久 409 "There is no saved operation to undo"）。
        task_id = info.task_id
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
                    entry = conn.execute("SELECT zotero_key,before_json,after_json FROM workspace_undo WHERE id=? AND task_id=? AND actor_id=? AND stage=?",
                                         (body.entry_id, task_id, str(user["user_id"]), stage)).fetchone() if workflow_store.has_table(conn, "workspace_undo") else None
                if entry is None:
                    raise HTTPException(409, "There is no saved operation to undo.")
                before, after = json.loads(entry[1]), json.loads(entry[2])
                canonical_only = stage == 'coding' and workspace_undo.is_submission_evidence(before, after)
                with runtime.write_guard(task_id, user, stage, entry[0], body.expected_policy_version,
                                         canonical_only=canonical_only) as ctx:
                    if canonical_only:
                        runtime.validate_evidence_dimensions(ctx, {int(dim) for dim in after.get('values', {})})
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
        task_id = info.task_id  # R6-C3/#22：锁/守卫统一规范 id（别名同键）
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
                    evidence=_evidence_intents(body.evidence),
                    trace_context=context)
                result = app.state.review_workflow.coding_result(ctx, body.zotero_key, body.draft)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except sqlite3.IntegrityError as exc:
            raise HTTPException(400, "维度或文献不存在（请确认后重试）。") from exc
        tasks.touch_task(info.task_id, data_dir)
        return result
