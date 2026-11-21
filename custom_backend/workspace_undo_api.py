"""Personal workspace Undo endpoints; reuse the application's access checks."""
from __future__ import annotations

import sqlite3
import hashlib
import json
import uuid
from typing import Annotated, Literal

from fastapi import Depends, Header, HTTPException, File, Form, UploadFile
from fastapi.responses import FileResponse, JSONResponse
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


# F64（UX 批准单 §七）：保存端点（编码表单/提交证据）在轨迹上下文不可用时的
# 503 文案，逐字对齐 main.py 决策保存的口径「先说明本次修改尚未保存、再给补救
# 动作」。该串同时在 interface_locale.js 双键注册十语言（errOf 的 detail→T()），
# 因此中文以外的界面也拿到本语言的同一句，而不是英文的撤销文案。
SAVE_NOT_SAVED_503 = "本次修改尚未保存。轨迹记录不可用，请重试或暂停记录后重新保存。"


def _storage_unavailable(exc: Exception) -> HTTPException:
    """S4（store 裸调收口）：sqlite 族故障 → 结构化 503（storage unavailable）。

    站点 175/374/460/462 此前不在任何 sqlite 感知 try 内，
    ``sqlite3.DatabaseError`` 逃逸全局兜底成通用 500。信封含
    error_code/message_key/retryable（锁忙/IO 抖动可稍后重试）。
    ``sqlite3.IntegrityError`` 不在此收敛（member_prepare_trace 内显式放行
    上抛），既有调用方 except 含 IntegrityError → 400/409 的语义不变。
    """
    return HTTPException(503, {
        "error_code": "workspace_undo_storage_unavailable",
        "message_key": "workspace_undo.storage_unavailable",
        "params": {},
        "retryable": True,
        "details": f"{type(exc).__name__}: {exc}",
        "message": "工作区数据库暂时不可用（可能正被其他操作占用，或磁盘只读/已满），请稍后重试。"})


# ---------------------------------------------------------------------------
# P16（C12 作者侧；2.1.6 team contracts P16 节）：成员写路由的 mutate 适配
# 助手。本文件四入口（values-batch/evidence uploads/submission-evidence/
# restore）与 main.py 的 save_decision/save_stage2_decision/save_coding_value
# 在**服务端可信状态**判定的 team 任务上改经 P15
# ``WorkflowRuntime.mutate``；solo/legacy 保持既有 write_guard 路径字节不变
# （P15 契约：旧 solo 路由继续直接走 write_guard，P24 负责最终接线与无漏网
# 检查）。
#
# P24 修轮 D1 → 包 B 缺口 2 收口：四入口全部换接 main.py
# ``_member_mutate_notified``（values-batch 先行，evidence uploads/
# submission-evidence/restore 本包补齐）——unknown 信封→409
# ``team_flow_unknown_outcome``、成功 commit 且锁释后 postcommit SSE、
# P15 信封头（X-Request-Id/X-Receipt-Id/X-Idempotent-Replay）透传真实
# HTTP 响应；``member_mutate`` 保留供延迟导入环规避前的兼容引用。
# ---------------------------------------------------------------------------

def member_request_fingerprint(request_id: str | None, payload: dict) -> tuple[str, str]:
    """P16 需求 2 前半：进入写窗口**之前**解析并固定请求内容与 SHA。

    ``request_id`` 取客户端 ``X-Request-Id``（缺省由服务端生成 uuid4——单次
    请求、不参与回执幂等，legacy 客户端行为不变；非法形状原样透传，由
    mutate 的 team 路径按 ``invalid_request_id`` 400 拒绝）。``payload`` 是
    服务端路由代码构造的规范化请求视图（路由名/规范 task_id/body 或固定后
    的上传内容 SHA），``request_sha256`` 是其 canonical JSON 的 SHA-256——
    同 id 同内容的客户端重试命中回执幂等，同 id 异内容 409（C09）。
    """
    rid = request_id if request_id is not None else str(uuid.uuid4())
    sha = hashlib.sha256(json.dumps(payload, sort_keys=True,
                                    ensure_ascii=False, default=str)
                         .encode("utf-8")).hexdigest()
    return rid, sha


def _json_string_keys(value):
    """递归把 dict 键规范为字符串（P07 回执 result.writer 契约；幂等）。"""
    if isinstance(value, dict):
        return {k if isinstance(k, str) else str(k): _json_string_keys(v)
                for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_string_keys(item) for item in value]
    return value


def member_mutate(runtime, info, user, *, op_name: str, stage: str, key: str,
                  expected_version: int | None, request_id: str,
                  request_sha256: str, writer):
    """P16 需求 1：stage/key 由服务端路由代码/请求体/持久 journal 归属导出。

    ``operation`` 只在此构造（``TeamOperation`` DTO）——客户端载荷绝不能充当
    路由键；team 路径经 P13 提交协议，writer 在窗口 yield 内收到绑定了
    ``ctx.write_inputs`` 的原生 WorkflowContext（保留连接读取）。

    返回**业务载荷**（writer 返回值）：P13 的 ``mutate`` 返回映射里
    ``result`` 是回执信封 ``{"request_id","operation","item_keys",
    "writer":<业务载荷>,"seals":[...]}``——路由层必须解信封取 ``writer``，
    业务返回形状才与 solo 分支兼容（幂等重放命中时同为回执信封形态，
    一致解包）。
    """
    from coscreen.team_flow_types import TeamOperation

    def canonical_writer(ctx):
        # P07 回执严格契约：writer 结果（回执 result.writer）对象键必须全是
        # JSON 字符串——业务载荷（如 coding_result 的 int 维度键嵌套 dict）在
        # 进回执前递归字符串键化；solo 分支的响应序列化本就把 int 键强转为
        # 字符串，客户端看到的 JSON 逐字相同（业务返回兼容零变化）。
        return _json_string_keys(writer(ctx))

    outcome = runtime.mutate(
        info.task_id, user,
        operation=TeamOperation(name=op_name, stage=stage,
                                expected_version=expected_version),
        item_keys=(key,), request_id=request_id, request_sha256=request_sha256,
        writer=canonical_writer)
    result = outcome.get("result")
    if isinstance(result, dict) and "writer" in result:
        return result["writer"]
    return result


def member_prepare_trace(db_path, task_id: str, user_id: int) -> None:
    """P16/A1：team borrowed writer 的 prepare——**最终资格窗口之前**在 actor
    库以 path 连接完成 trace 三表（ensure_schema）与本 task/actor 的默认
    settings 行（ensure_default_recording；默认 recording off，与 solo 首存
    的懒 ensure 同语义）。S2-A1 prepared 契约：窗口内
    ``begin_capture_transaction(prepared=True)`` 只读复用，不做 DDL/自愈；
    缺 prepare 即 fail-closed（OperationalError），本函数就是那个 prepare。
    幂等：表/行已存在时零写入。
    """
    from coscreen import research_trace
    from coscreen.research_trace import TraceContext
    try:
        with closing(workflow_store.connect(db_path)) as conn, conn:
            # undo journal 表/索引/列：begin_save 的 standalone 建表分支（prepared
            # 分支只核事务，schema 由本 prepare 负责——S2-A1 §2.2）。
            workspace_undo.begin_save(conn, prepared=False)
            research_trace.ensure_schema(conn)
            research_trace.ensure_default_recording(
                conn, TraceContext(task_id=task_id, actor_id=user_id))
    except sqlite3.IntegrityError:
        # 既有调用方语义保留：save_submission_evidence/restore/save_form 的
        # except 均含 IntegrityError（→400/409），不得改判 503。
        raise
    except sqlite3.DatabaseError as exc:
        # S4：本 prepare 的三个 in-file 调用点与 main.py 三处内联调用均无
        # sqlite 感知兜底——坏库/锁忙在此收敛为 typed 503。
        raise _storage_unavailable(exc) from exc


def borrowed_target_conn(ctx):
    """P16 断言锚：真实保存 writer 的连接必须与检查/提交目标**同一对象**。

    team 窗口内 writer 只允许使用 ``ctx.write_inputs`` 的 actor 连接，且该
    连接必须是本窗口的 target 连接（member_* 操作 target 恒 actor）——检查/
    业务写/提交同连接同事务，任何漂移立即 fail-closed（RuntimeError），
    绝不静默换目标。solo/legacy 不经此函数（无绑定窗口）。
    """
    inputs = getattr(ctx, "write_inputs", None)
    if inputs is None:
        raise RuntimeError("team writer requires bound ctx.write_inputs")
    conn = inputs.actor_conn
    if conn is not inputs.target_conn:
        raise RuntimeError(
            "borrowed writer connection is not the guarded target connection")
    return conn


def register_workspace_undo_routes(app, require_user, task_lookup, screener_db, trace_context, data_dir,
                                   screening_allowed=None, screening_disabled_error=None) -> None:
    async def upload_evidence(task_id: str, zotero_key: str, dim_id: int, file: UploadFile,
                              user: dict, *, canonical_only: bool = False,
                              expected_version: int | None = None,
                              request_id: str | None = None) -> dict:
        # R6-C3/#22：路由原始 task_id（Windows 尾点别名）统一为规范 id——
        # stage_upload 落库的 task_id 与 save_form 附着查询（trace_context 的
        # info.task_id）同键，别名路径上传的图片才能被表单保存附着。
        info = task_lookup(task_id)
        task_id = info.task_id
        # P16 需求 2：先解析/固定请求内容与 SHA——读满请求字节并关闭
        # UploadFile 后才进入写窗口；窗口/writer 不再触碰请求流。上传的
        # request_sha256 覆盖规范 task_id/zotero_key/dim_id/canonical_only 与
        # 内容 SHA：客户端重试同 id 同内容幂等命中，同 id 异内容 409。
        data = await file.read(coding_evidence.MAX_UPLOAD_BYTES + 1)
        await file.close()
        payload_id, payload_sha = member_request_fingerprint(request_id, {
            "task_id": task_id, "zotero_key": zotero_key, "dim_id": dim_id,
            "canonical_only": canonical_only,
            "content_sha256": hashlib.sha256(data).hexdigest()})
        try:
            runtime = app.state.review_workflow
            if runtime.is_team(task_id):
                # P16：真实 team 上传经 P15 mutate（member_coding/coding，stage/
                # key 服务端导出）；stage_upload 使用 borrowed actor——检查/
                # 暂存落库/提交同连接。FS 暂存不具 SQLite 原子性：文件写在
                # caller 事务前发生，窗口回滚可能留下**可回收孤儿文件**，
                # 只交给既有 GC 清理（如实，不假装原子、绝不删除已提交引用
                # 仍持有的文件）。
                def writer(ctx):
                    conn = borrowed_target_conn(ctx)
                    if canonical_only:
                        runtime.validate_evidence_dimensions(ctx, {dim_id})
                    return coding_evidence.stage_upload(
                        ctx.actor_db, task_id, user['user_id'], zotero_key,
                        dim_id, data, conn=conn)
                # P24 修轮 D1 同款接线（包 B 缺口 2）：evidence uploads 与
                # main.py 六路由/values-batch 同款信封感知——unknown→409
                # team_flow_unknown_outcome、锁外 postcommit SSE、S3 信封头
                # 透传（延迟导入避免 main→本模块的环）。
                from custom_backend.main import _member_mutate_notified
                result, member_headers = _member_mutate_notified(
                    runtime, info, user, op_name="member_coding",
                    stage="coding", key=zotero_key,
                    expected_version=expected_version,
                    request_id=payload_id,
                    request_sha256=payload_sha,
                    writer=writer, change_kind="vote")
                if member_headers:
                    return JSONResponse(result, headers=member_headers)
                return result
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
                     x_request_id: str | None = Header(default=None, max_length=128),
                     user: dict = Depends(require_user)) -> dict:
        return await upload_evidence(task_id, zotero_key, dim_id, file, user,
                                     request_id=x_request_id)

    @app.post('/api/tasks/{task_id}/coding/submission-evidence/uploads')
    async def submission_upload(task_id: str, zotero_key: str = Form(..., min_length=1, max_length=300),
                                dim_id: int = Form(..., ge=1), file: UploadFile = File(...),
                                expected_policy_version: int | None = Form(default=None, ge=1),
                                x_request_id: str | None = Header(default=None, max_length=128),
                                user: dict = Depends(require_user)) -> dict:
        return await upload_evidence(task_id, zotero_key, dim_id, file, user,
                                     canonical_only=True,
                                     expected_version=expected_policy_version,
                                     request_id=x_request_id)

    @app.get('/api/tasks/{task_id}/coding/submission-evidence')
    def submission_queue(task_id: str, source_id: str | None = None, user: dict = Depends(require_user)) -> dict:
        return app.state.review_workflow.submission_evidence_queue(task_id, user, source_id)

    @app.get('/api/tasks/{task_id}/coding/submission-evidence/{zotero_key}')
    def submission_item(task_id: str, zotero_key: str, user: dict = Depends(require_user)) -> dict:
        return app.state.review_workflow.work_item(task_id, user, 'coding', zotero_key,
                                                   submission_evidence=True)

    @app.post('/api/tasks/{task_id}/coding/submission-evidence')
    def save_submission_evidence(task_id: str, body: SubmissionEvidenceIn,
                                 x_request_id: str | None = Header(default=None, max_length=128),
                                 user: dict = Depends(require_user)) -> dict:
        info = task_lookup(task_id)
        task_id = info.task_id  # R6-C3/#22：锁/守卫统一规范 id（别名同键）
        runtime = app.state.review_workflow
        try:
            if runtime.is_team(task_id):
                # P16：真实 team 提交证据保存经 P15 mutate（member_coding/
                # coding；stage/key 服务端导出），writer 使用同一
                # ctx.write_inputs target；canonical 维度校验在窗口内以保留
                # 连接读取（validate_evidence_dimensions 感知 write_inputs）。
                payload_id, payload_sha = member_request_fingerprint(
                    x_request_id, {"task_id": task_id,
                                   "route": "submission-evidence",
                                   "body": body.model_dump()})

                member_prepare_trace(
                    screener_db(info, user["username"]), task_id,
                    user["user_id"])

                def writer(ctx):
                    conn = borrowed_target_conn(ctx)
                    dimensions = set(body.notes or {}) | set(body.evidence or {})
                    runtime.validate_evidence_dimensions(ctx, dimensions)
                    context = trace_context(ctx.info, user)
                    if context is None:
                        # F64：同下方 solo 分支口径（本次保存尚未生效）。
                        raise HTTPException(503, SAVE_NOT_SAVED_503)
                    coding_evidence.save_form(ctx.actor_db, body.zotero_key, {},
                        notes=body.notes,
                        evidence=_evidence_intents(body.evidence),
                        trace_context=context, submission_evidence=True,
                        conn=conn)
                    result = runtime.coding_result(ctx, body.zotero_key, body.draft)
                    result['access_context'] = workspace_undo.SUBMISSION_EVIDENCE
                    result['submission_evidence'] = runtime.submission_evidence_capability(ctx)
                    return result
                # P24 修轮 D1 同款接线（包 B 缺口 2）：submission-evidence
                # 换接 _member_mutate_notified——unknown→409
                # team_flow_unknown_outcome、锁外 postcommit SSE、S3 信封头
                # 透传（延迟导入避免 main→本模块的环）。
                from custom_backend.main import _member_mutate_notified
                result, member_headers = _member_mutate_notified(
                    runtime, info, user, op_name="member_coding",
                    stage="coding", key=body.zotero_key,
                    expected_version=body.expected_policy_version,
                    request_id=payload_id,
                    request_sha256=payload_sha,
                    writer=writer, change_kind="vote")
            else:
                member_headers = {}
                with runtime.write_guard(task_id, user, 'coding', body.zotero_key,
                                         body.expected_policy_version, canonical_only=True) as ctx:
                    dimensions = set(body.notes or {}) | set(body.evidence or {})
                    runtime.validate_evidence_dimensions(ctx, dimensions)
                    context = trace_context(ctx.info, user)
                    if context is None:
                        # F64（UX 批准单 §七）：保存端点的 503 此前复用撤销端点的
                        # 「Undo is unavailable」，用户看不出这次保存是否生效。改为
                        # 对齐 main.py 决策保存的 503 口径：先说明「本次修改尚未保存」，
                        # 再给补救动作。撤销端点（下方 restore）仍保留撤销专属文案。
                        raise HTTPException(503, SAVE_NOT_SAVED_503)
                    coding_evidence.save_form(ctx.actor_db, body.zotero_key, {}, notes=body.notes,
                        evidence=_evidence_intents(body.evidence),
                        trace_context=context, submission_evidence=True)
                    result = runtime.coding_result(ctx, body.zotero_key, body.draft)
                    result['access_context'] = workspace_undo.SUBMISSION_EVIDENCE
                    result['submission_evidence'] = runtime.submission_evidence_capability(ctx)
        except (ValueError, sqlite3.IntegrityError) as exc:
            raise HTTPException(400, str(exc) if isinstance(exc, ValueError) else 'Evidence field or article is unavailable.') from exc
        tasks.touch_task(task_id, data_dir)
        if member_headers:
            return JSONResponse(result, headers=member_headers)
        return result

    def image_file(task_id: str, identity: str | int, staged: bool, user: dict):
        # R6-C3/#22：锁、ctx 与 staged 上传行查询统一规范 task_id（别名同键）。
        info = task_lookup(task_id)
        task_id = info.task_id
        runtime = app.state.review_workflow
        with runtime.lock(task_id):
            ctx = runtime.context(task_id, user)
            try:
                with closing(workflow_store.connect(ctx.actor_db, read_only=True)) as conn:
                    if staged:
                        # r6 #28：尾点别名存量 staged 行同键兼容（规范 id 恒无尾点，
                        # rtrim 只影响修复前以别名落库的行）。
                        row = conn.execute("SELECT path,zotero_key FROM workflow_evidence_uploads WHERE id=? AND rtrim(task_id,'. ')=? AND actor_id=?",
                                           (identity, task_id.rstrip('. '), str(user['user_id']))).fetchone()
                    else:
                        row = conn.execute('SELECT i.path,n.zotero_key FROM coding_note_images i JOIN coding_notes n ON n.id=i.note_id WHERE i.id=?', (identity,)).fetchone()
            except sqlite3.DatabaseError as exc:
                # S4：preview/image 两路由（396/400）无局部兜底——只读连接/
                # 查询的 sqlite 族收敛为 typed 503（404 判定路径不变）。
                raise _storage_unavailable(exc) from exc
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
                x_request_id: str | None = Header(default=None, max_length=128),
                user: dict = Depends(require_user)) -> dict:
        info = task_lookup(task_id)
        # R2：撤销也是初筛/复筛写入面——被负责人关闭参与权的裁决员不得借
        # undo 绕过 decision 写闸门；coding 撤销不受该开关影响。
        if (stage in ("screen", "fulltext") and screening_allowed is not None
                and screening_disabled_error is not None):
            if not screening_allowed(info, user):
                raise screening_disabled_error()
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
                try:
                    with closing(workflow_store.connect(db, read_only=True)) as conn:
                        entry = conn.execute("SELECT zotero_key,before_json,after_json FROM workspace_undo WHERE id=? AND task_id=? AND actor_id=? AND stage=?",
                                             (body.entry_id, task_id, str(user["user_id"]), stage)).fetchone() if workflow_store.has_table(conn, "workspace_undo") else None
                except sqlite3.DatabaseError as exc:
                    # S4：POST undo 端点体（connect+has_table 两站）——sqlite 族
                    # 收敛为 typed 503；外层 except (ValueError, IntegrityError)
                    # →409 的既有语义不受影响（HTTPException 不被其捕获）。
                    raise _storage_unavailable(exc) from exc
                if entry is None:
                    raise HTTPException(409, "There is no saved operation to undo.")
                before, after = json.loads(entry[1]), json.loads(entry[2])
                canonical_only = stage == 'coding' and workspace_undo.is_submission_evidence(before, after)
                if runtime.is_team(task_id):
                    # P16 需求 1：undo 的 stage/key 由**持久 journal 归属导出**
                    # （上方锁内只读预解析 entry[0]），再经 P15 mutate 开窗
                    # （member_undo/<stage>）；writer 内**同窗复核**同一条目归属
                    # 后，把保留的 target 连接传给真实 workspace_undo.undo——
                    # journal/请求摘要/trace/恢复全部同连接，提交权归窗口。
                    payload_id, payload_sha = member_request_fingerprint(
                        x_request_id, {"task_id": task_id, "route": "undo",
                                       "stage": stage, "entry_id": body.entry_id,
                                       "zotero_key": entry[0],
                                       "expected_policy_version": body.expected_policy_version})

                    member_prepare_trace(
                        screener_db(info, user["username"]), task_id,
                        user["user_id"])

                    def writer(ctx):
                        conn = borrowed_target_conn(ctx)
                        if ctx.actor_db.resolve() != db.resolve():
                            raise HTTPException(403, "Undo is limited to your own workspace.")
                        row = conn.execute(
                            "SELECT zotero_key FROM workspace_undo WHERE id=? AND task_id=? AND actor_id=? AND stage=?",
                            (body.entry_id, task_id, str(user["user_id"]), stage)).fetchone()
                        if row is None or row[0] != entry[0]:
                            raise ValueError("There is no saved operation to undo.")
                        if canonical_only:
                            runtime.validate_evidence_dimensions(
                                ctx, {int(dim) for dim in after.get('values', {})})
                        # Restoration may contain historical Maybe; existing Undo
                        # snapshots already reject changed downstream work.
                        return workspace_undo.undo(db, context, stage,
                                                   body.entry_id, conn=conn)
                    # P24 修轮 D1 同款接线（包 B 缺口 2）：restore（member_undo）
                    # 换接 _member_mutate_notified——unknown→409
                    # team_flow_unknown_outcome、锁外 postcommit SSE、S3 信封头
                    # 透传（延迟导入避免 main→本模块的环）。
                    from custom_backend.main import _member_mutate_notified
                    result, member_headers = _member_mutate_notified(
                        runtime, info, user, op_name="member_undo",
                        stage=stage, key=entry[0],
                        expected_version=body.expected_policy_version,
                        request_id=payload_id, request_sha256=payload_sha,
                        writer=writer, change_kind="vote")
                else:
                    member_headers = {}
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
        if member_headers:
            return JSONResponse(result, headers=member_headers)
        return result

    @app.post("/api/tasks/{task_id}/coding/values-batch")
    def save_form(task_id: str, body: CodingBatchIn, screener: str | None = None,
                  x_request_id: str | None = Header(default=None, max_length=128),
                  user: dict = Depends(require_user)) -> dict:
        info = task_lookup(task_id)
        task_id = info.task_id  # R6-C3/#22：锁/守卫统一规范 id（别名同键）
        db = screener_db(info, screener or user["username"])
        context = trace_context(info, user)
        if context is None:
            raise HTTPException(503, SAVE_NOT_SAVED_503)
        try:
            runtime = app.state.review_workflow
            if runtime.is_team(task_id):
                # P16：真实 team 批量编码保存经 P15 mutate（member_coding/
                # coding）；writer 使用同一 ctx.write_inputs target（borrowed
                # actor 连接）落真实 coding_evidence.save_form，结果组装
                # （coding_result）也在窗口内以保留连接读取。
                payload_id, payload_sha = member_request_fingerprint(
                    x_request_id, {"task_id": task_id, "route": "values-batch",
                                   "body": body.model_dump()})

                # P16/A1：窗口前 prepare trace 三表+默认 settings 行（幂等）。
                member_prepare_trace(
                    screener_db(info, user["username"]), task_id,
                    user["user_id"])

                def writer(ctx):
                    conn = borrowed_target_conn(ctx)
                    if ctx.actor_db.resolve() != db.resolve():
                        raise HTTPException(403, 'Coding evidence is limited to your own workspace.')
                    coding_evidence.save_form(ctx.actor_db, body.zotero_key, body.values,
                        notes=body.notes,
                        evidence=_evidence_intents(body.evidence),
                        trace_context=context, conn=conn)
                    return runtime.coding_result(ctx, body.zotero_key, body.draft)
                # P24 修轮 D1：values-batch 与 main.py 六路由同款信封感知
                # 接线——unknown→409 team_flow_unknown_outcome、锁外 postcommit
                # SSE、S3 信封头透传（延迟导入避免 main→本模块的环）。
                from custom_backend.main import _member_mutate_notified
                result, member_headers = _member_mutate_notified(
                    runtime, info, user, op_name="member_coding",
                    stage="coding", key=body.zotero_key,
                    expected_version=body.expected_policy_version,
                    request_id=payload_id,
                    request_sha256=payload_sha,
                    writer=writer, change_kind="vote")
            else:
                member_headers = {}
                with runtime.write_guard(task_id, user, "coding", body.zotero_key,
                                         body.expected_policy_version) as ctx:
                    if ctx.actor_db.resolve() != db.resolve():
                        raise HTTPException(403, 'Coding evidence is limited to your own workspace.')
                    coding_evidence.save_form(db, body.zotero_key, body.values, notes=body.notes,
                        evidence=_evidence_intents(body.evidence),
                        trace_context=context)
                    result = runtime.coding_result(ctx, body.zotero_key, body.draft)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except sqlite3.IntegrityError as exc:
            raise HTTPException(400, "维度或文献不存在（请确认后重试）。") from exc
        tasks.touch_task(info.task_id, data_dir)
        if member_headers:
            return JSONResponse(result, headers=member_headers)
        return result
