"""Co-Bookshelf FastAPI 后端（custom_backend.main:app）。

把 coscreen 包的真实功能映射为带鉴权的 REST API：

- 认证：JWT（HS256，24h）+ bcrypt 用户表（custom_backend/auth.py）；
- 除 /api/auth/login、/api/auth/register、/api/health 外，全部端点要求
  ``Authorization: Bearer <token>``；
- 输入校验：Pydantic 模型（422）+ 业务层 ValueError（400/409）；
- 上传：magic bytes + 扩展名白名单 + 100MB 上限 + 文件名清洗（security.py）；
- 错误：全局兜底只回 500 通用文案，完整堆栈仅记服务端日志；
- 路径：任务/文件路径一律由服务端从白名单目录构造，并做 realpath 逃逸校验；
- 数据目录：``data/``（环境变量 ``COBOOKSHELF_DATA_DIR`` 覆盖，供测试隔离）。

生产部署：uvicorn 应置于 nginx/Caddy 等 TLS 反向代理之后（HTTPS 终止在代理层），
例如::

    uvicorn custom_backend.main:app --host 0.0.0.0 --port 8613 --workers 2
"""

from __future__ import annotations

from typing import Literal

import asyncio
import json
import hashlib
import csv
import io
import logging
import math
import os
import re
import shutil
import sqlite3
import uuid
import tempfile
import threading
import traceback
from datetime import datetime
from io import BytesIO
from pathlib import Path
from urllib.parse import quote

import pandas as pd
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from coscreen import coding as coding_mod
from coscreen import task_collaboration as collaboration_mod
from coscreen import review_analysis as analysis_mod
from coscreen import advanced_analysis as advanced_mod
from coscreen import network_analysis as network_mod
from coscreen import dose_analysis as dose_mod
from coscreen import ipd_analysis as ipd_mod
from coscreen import config
from coscreen import db as db_mod
from coscreen import import_history as import_history_mod
from coscreen import dta_analysis as dta_mod
from coscreen import qualitative_analysis as qual_mod
from coscreen import tasks as tasks_mod
from coscreen.pool_compatibility import NON_DIRECTIONAL_MEASURES
from coscreen.al.ranker import ActiveLearningRanker
from coscreen.al.stop import current_streak as al_current_streak
from coscreen.al.stop import suggest_stop as al_suggest_stop
from coscreen.dedup import (
    find_duplicates,
    find_title_candidate_groups,
    normalize_title_candidate,
)
from coscreen.export import (
    export_articles_ris,
    export_coding_matrix,
    export_decisions,
    export_highlights_markdown,
    export_stage2_decisions,
    load_decisions_csv,
)
from coscreen.fulltext import (
    get_fulltext,
    get_highlights_with_rects,
    get_stage2_progress,
    list_stage2_decisions,
    load_pdf_bytes,
    save_pdf,
    save_stage2,
    stage2_queue,
)
from coscreen.parsers import ParseError, parse_file
from coscreen.stats import (
    agreement_payload,
    exclusion_reason_distribution_multi,
    light_kappa,
    merge_coding,
    merge_decisions_multi,
    pairwise_kappa,
    summarize_coding,
    summarize_multi,
)
from coscreen.stats.prisma_form import (
    ATTRIBUTION,
    DB_SOURCE_TYPES,
    DTA_REASON_VOCABULARY,
    OTHER_SOURCE_TYPES,
    PrismaFormData,
    IdentifiedRow,
    ReasonRow,
    REVIEW_MODES,
    SOURCES_SCOPES,
    form_to_dict,
    form_json,
    prisma_form_dot,
    validate,
)
from coscreen.security import sanitize_component

from custom_backend import auth
from custom_backend.update_check import check_latest_release, current_version
from custom_backend.source_provenance import source_articles
from custom_backend.security import (
    MAX_UPLOAD_BYTES,  # noqa: F401  上限常量随模块口径导出
    TEXT_EXTENSIONS,
    UploadRejected,
    check_extension,
    check_pdf_bytes,
    check_text_bytes,
    clean_upload_name,
    is_within,
    login_limiter,
    stem_of,
)
from custom_backend.security import SecurityHeadersMiddleware

logger = logging.getLogger("cobookshelf.api")

# ---------------------------------------------------------------------------
# 应用与中间件
# ---------------------------------------------------------------------------

DATA_DIR = Path(os.environ.get("COBOOKSHELF_DATA_DIR", "data"))
if os.environ.get("REVIEWFLOW_MODE") == "server" and not DATA_DIR.is_absolute():
    raise RuntimeError("Server mode requires an absolute COBOOKSHELF_DATA_DIR")

_DECISION_RE = re.compile(r"^(include|exclude|maybe)$")
_KEY_RE = re.compile(r"^[^/\\\x00]{1,300}$")            # zotero_key / 通用 id
_TASK_ID_RE = re.compile(r"^[^/\\\x00]{1,200}$")        # 任务目录名（find_task 再兜底）


def _cors_origins() -> list[str]:
    """CORS 白名单：环境变量 ``COBOOKSHELF_CORS_ORIGINS``（逗号分隔）。"""
    raw = os.environ.get(
        "COBOOKSHELF_CORS_ORIGINS",
        "http://localhost:8614,http://127.0.0.1:8614,"
        "http://localhost:8615,http://127.0.0.1:8615",
    )
    return [origin.strip().rstrip("/") for origin in raw.split(",") if origin.strip()]


app = FastAPI(
    title="Co-Bookshelf API",
    version=current_version(),
    docs_url=None,
    redoc_url=None,
    openapi_url=None,  # 产品化：不对外暴露接口文档
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
    max_age=600,
)
app.add_middleware(SecurityHeadersMiddleware)


class TaskLockMiddleware:
    """请求期任务锁：``/api/tasks/{task_id}/**`` 处理期间持有 ``{taskId}.lock``。

    此前生产代码从不 acquire_task_lock，delete_task 的"任务被其他窗口使用"
    防线是死代码（审查 P1-3）。现按路径统一 acquire/release：另一存活后端
    进程正在处理同一任务时返回 409（同进程并发请求按引用计数共享一把锁，
    锁只在最后一个在途请求结束后释放）。
    """

    _PATH_RE = re.compile(r"^/api/tasks/([^/]+)")
    # task_id -> [在途请求数, 锁路径]：同任务并发请求共享一把锁，最后一个
    # 在途请求结束时释放（无论谁最初获取）。
    _held: dict[str, list] = {}
    _guard = threading.Lock()

    def __init__(self, app):  # noqa: ANN001  ASGI 约定
        self.app = app

    async def __call__(self, scope, receive, send):  # noqa: ANN001
        if scope["type"] != "http" or scope.get("method") == "OPTIONS":
            await self.app(scope, receive, send)
            return
        if re.fullmatch(r"/api/tasks/[^/]+/import-progress/[0-9a-fA-F-]{36}",
                        scope.get("path", "")):
            await self.app(scope, receive, send)
            return
        match = self._PATH_RE.match(scope.get("path", ""))
        if not match:
            await self.app(scope, receive, send)
            return
        task_id = match.group(1)
        entry = None
        busy_response = None
        info = tasks_mod.find_task(task_id, DATA_DIR)
        if info is not None:
            with self._guard:  # 锁内不做 await（事件循环单线程，await 让位时会死锁）
                entry = self._held.get(task_id)
                if entry is None:
                    lock_path = tasks_mod.acquire_task_lock(
                        task_id, DATA_DIR, task_dir=info.dir)
                    if lock_path is None:
                        busy_response = JSONResponse(status_code=409, content={
                            "detail": "任务正被其他窗口（另一个后端进程）使用，请稍后重试。"})
                    else:
                        entry = [0, lock_path]
                        self._held[task_id] = entry
                if entry is not None:
                    entry[0] += 1
        if busy_response is not None:
            await busy_response(scope, receive, send)
            return
        try:
            await self.app(scope, receive, send)
        finally:
            if entry is not None:
                with self._guard:
                    entry[0] -= 1
                    last = entry[0] <= 0
                    if last:
                        self._held.pop(task_id, None)
                if last:
                    tasks_mod.release_task_lock(entry[1])


app.add_middleware(TaskLockMiddleware)


@app.exception_handler(Exception)
async def _unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """全局兜底：完整堆栈只进服务端日志，响应只给通用文案（任务书第 9 条）。"""
    logger.error(
        "Unhandled error on %s %s\n%s", request.method, request.url.path,
        "".join(traceback.format_exception(exc)),
    )
    return JSONResponse(status_code=500, content={"detail": "服务暂时无法完成请求，请稍后重试。"})


@app.exception_handler(RequestValidationError)
async def _validation_error_handler(request: Request, exc: RequestValidationError) -> Response:
    if '/analysis/ipd/' in request.url.path:
        return JSONResponse(status_code=422, content={'detail': [
            {key: error[key] for key in ('loc', 'msg', 'type')} for error in exc.errors()
        ]})
    return await request_validation_exception_handler(request, exc)


@app.exception_handler(UploadRejected)
async def _upload_rejected_handler(request: Request, exc: UploadRejected) -> JSONResponse:
    return JSONResponse(status_code=exc.status, content={"detail": str(exc)})


# ---------------------------------------------------------------------------
# 鉴权依赖与请求模型
# ---------------------------------------------------------------------------

def require_user(
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict:
    """校验 Bearer JWT；缺失/无效一律 401（带 WWW-Authenticate）。"""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            401, "未登录或缺少访问令牌。", headers={"WWW-Authenticate": "Bearer"}
        )
    payload = auth.decode_access_token(authorization[7:].strip())
    if payload is None:
        raise HTTPException(
            401, "访问令牌无效或已过期，请重新登录。", headers={"WWW-Authenticate": "Bearer"}
        )
    try:
        user_id = int(payload["sub"])
    except (KeyError, TypeError, ValueError):
        # sub 形状非法（正常签发不会出现）——按无效令牌处理而非 500
        raise HTTPException(
            401, "访问令牌无效或已过期，请重新登录。", headers={"WWW-Authenticate": "Bearer"}
        ) from None
    username = str(payload["username"])
    if auth.screener_storage_conflict(username):
        raise HTTPException(409, "多个旧账号映射到同一个筛选员数据库。已暂停访问，请先人工核对并拆分数据。")
    if any(name and name != username for name in request.query_params.getlist("screener")):
        raise HTTPException(403, "只能访问自己的筛选员数据库。")
    user = {"user_id": user_id, "username": username}
    request_id = request.headers.get("X-Request-ID")
    if request_id is not None:
        try:
            request_id = str(uuid.UUID(request_id))
        except (ValueError, AttributeError) as exc:
            raise HTTPException(400, "X-Request-ID must be a UUID.") from exc
        user["_trace_request_id"] = request_id
    task_id = request.path_params.get("task_id")
    if task_id:
        info = _task_or_404(task_id)
        if not _task_visible_to(info, user):
            raise HTTPException(403, "当前账号不是该任务的参与者。")
    return user


from custom_backend.local_storage_api import register_local_storage_routes

register_local_storage_routes(app, require_user, DATA_DIR)


def _server_mode() -> bool:
    return os.environ.get("REVIEWFLOW_MODE", "desktop") == "server"


class CredentialsIn(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=8, max_length=128)


class TaskCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)


class TaskUpdateIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    archived: bool | None = None


class DecisionIn(BaseModel):
    zotero_key: str = Field(min_length=1, max_length=300)
    decision: str = Field(pattern="^(include|exclude|maybe)$")
    reason: str = Field(default="", max_length=2000)
    notes: str = Field(default="", max_length=8000)
    tags: str | None = Field(default=None, max_length=500)


class TitleDuplicateDecisionIn(BaseModel):
    decision: Literal["duplicate", "not_duplicate", "undecided"]
    keeper_key: str = Field(default="", max_length=300)


class DimensionIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    dtype: str = Field(pattern="^(choice|text|numeric)$")
    options: list[str] = Field(default_factory=list, max_length=100)
    multi_select: bool = False
    unit: str = Field(default="", max_length=100)
    description: str = Field(default="", max_length=2000)
    section: str = Field(default="", max_length=200)


class DimensionUpdateIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    dtype: str | None = Field(default=None, pattern="^(choice|text|numeric)$")
    options: list[str] | None = Field(default=None, max_length=100)
    multi_select: bool | None = None
    unit: str | None = Field(default=None, max_length=100)
    description: str | None = Field(default=None, max_length=2000)
    section: str | None = Field(default=None, max_length=200)
    position: int | None = Field(default=None, ge=0, le=100000)


class CodingValueIn(BaseModel):
    dim_id: int = Field(ge=1, le=10**9)
    zotero_key: str = Field(min_length=1, max_length=300)
    value: str = Field(default="", max_length=4000)


class StudyIn(BaseModel):
    id: str = Field(min_length=1, max_length=200)
    label: str = Field(min_length=1, max_length=300)
    reports: list[str] = Field(min_length=1, max_length=100)


class EffectIn(BaseModel):
    study_id: str = Field(min_length=1, max_length=200)
    comparison: str = Field(min_length=1, max_length=300)
    outcome: str = Field(min_length=1, max_length=300)
    timepoint: str = Field(min_length=1, max_length=100)
    measure: str = Field(pattern="^(RR|OR|RD|MD|SMD|HR|RATE_RATIO|FISHER_Z|MEAN|LOGIT_PROP|LOG_RATE|SMCR|SMCC|LOG_ROM|PAIRED_OR|PAIRED_RD|PHI|R_EQUIV_APPROX)$")
    estimate: float
    se: float
    source_key: str = Field(min_length=1, max_length=300)
    source_locator: str = Field(default="", max_length=500)
    append: bool = False
    standardizer: str | None = Field(default=None, pattern="^(control_arm_sd|pooled_within_group_sd)$")
    effect_direction: Literal["first_vs_second", "second_vs_first"] | None = None
    se_scale: Literal["log", "natural"] | None = None


class EffectDirectionConfirmationIn(BaseModel):
    effect_direction: Literal["first_vs_second", "second_vs_first"]
    source_locator: str = Field(default="", max_length=500)


class ArmEffectIn(BaseModel):
    study_id: str = Field(min_length=1, max_length=200)
    comparison: str = Field(min_length=1, max_length=300)
    outcome: str = Field(min_length=1, max_length=300)
    timepoint: str = Field(min_length=1, max_length=100)
    measure: str = Field(pattern="^(RR|OR|RD|MD|SMD)$")
    source_key: str = Field(min_length=1, max_length=300)
    arms: dict[str, int | float]
    source_locator: str = Field(default="", max_length=500)
    append: bool = False
    effect_direction: Literal["first_vs_second", "second_vs_first"]


class MultiArmEffectIn(BaseModel):
    study_id: str = Field(min_length=1, max_length=200)
    comparison: str = Field(min_length=1, max_length=300)
    outcome: str = Field(min_length=1, max_length=300)
    timepoint: str = Field(min_length=1, max_length=100)
    measure: str = Field(pattern="^(RR|OR|RD|MD|SMD)$")
    effect_direction: Literal["first_vs_second", "second_vs_first"]
    source_key: str = Field(min_length=1, max_length=300)
    source_locator: str = Field(default="", max_length=500)
    append: bool = False
    intervention_arms: list[dict[str, object]] = Field(min_length=1)
    comparator_arms: list[dict[str, object]] = Field(min_length=1)
    combination_rationale: str = Field(min_length=1, max_length=2000)


class BinaryArmsIn(BaseModel):
    study_id: str = Field(min_length=1, max_length=200)
    comparison: str = Field(min_length=1, max_length=300)
    outcome: str = Field(min_length=1, max_length=300)
    timepoint: str = Field(min_length=1, max_length=100)
    source_key: str = Field(min_length=1, max_length=300)
    source_locator: str = Field(default="", max_length=500)
    arms: dict[str, object]


class SingleGroupCountIn(BaseModel):
    study_id: str = Field(min_length=1, max_length=200)
    comparison: str = Field(min_length=1, max_length=300)
    outcome: str = Field(min_length=1, max_length=300)
    timepoint: str = Field(min_length=1, max_length=100)
    kind: str = Field(pattern="^(proportion|rate)$")
    events: int = Field(ge=0)
    total: int | None = Field(default=None, ge=1)
    person_time: float | None = Field(default=None, gt=0)
    time_unit: str = Field(default="", max_length=100)
    source_key: str = Field(min_length=1, max_length=300)
    source_locator: str = Field(default="", max_length=500)
    append: bool = False


class OrwinIn(BaseModel):
    comparison: str = Field(min_length=1, max_length=300)
    outcome: str = Field(min_length=1, max_length=300)
    timepoint: str = Field(min_length=1, max_length=100)
    measure: str = Field(pattern="^(RR|OR|RD|MD|SMD|HR|RATE_RATIO|FISHER_Z)$")
    target_effect: float
    missing_effect: float


class TrimFillIn(BaseModel):
    comparison: str = Field(min_length=1, max_length=300)
    outcome: str = Field(min_length=1, max_length=300)
    timepoint: str = Field(min_length=1, max_length=100)
    measure: str = Field(pattern="^(RR|OR|RD|MD|SMD|HR|RATE_RATIO|FISHER_Z)$")
    side: str = Field(pattern="^(left|right)$")
    estimator: str = Field(pattern="^(L0|R0|Q0)$")
    pooling_method: str = Field(pattern="^(FE|DL|REML)$")


class EffectCalculateIn(BaseModel):
    measure: str = Field(pattern="^(RR|OR|RD|MD|SMD)$")
    events_t: int | None = None
    total_t: int | None = None
    events_c: int | None = None
    total_c: int | None = None
    n_t: int | None = None
    mean_t: float | None = None
    sd_t: float | None = None
    n_c: int | None = None
    mean_c: float | None = None
    sd_c: float | None = None


class SynthesisRunIn(BaseModel):
    comparison: str = Field(min_length=1, max_length=300)
    outcome: str = Field(min_length=1, max_length=300)
    timepoint: str = Field(min_length=1, max_length=100)
    measure: str = Field(min_length=1, max_length=30)
    model: str = Field(min_length=1, max_length=30)
    ci_method: str = Field(min_length=1, max_length=30)
    coding_freeze_id: int | None = Field(default=None, gt=0)


class CollaborationRoleIn(BaseModel):
    user_id: int = Field(gt=0)
    role: Literal["coder", "reconciler"]


class CollaborationInviteIn(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    role: Literal["coder", "reconciler"] = "coder"


class EffectDesignIn(BaseModel):
    cluster_id: str = Field(min_length=1, max_length=200)
    sample_id: str = Field(min_length=1, max_length=200)
    structure_note: str = Field(min_length=1, max_length=1000)
    expected_revision: int = Field(ge=0)


class ResultModeratorIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    value: object
    value_type: Literal["continuous", "categorical"]
    source_key: str = Field(min_length=1, max_length=300)
    source_locator: str = Field(min_length=1, max_length=500)
    expected_revision: int = Field(ge=0)


class HedgesGIn(BaseModel):
    study_id: str = Field(min_length=1, max_length=200)
    comparison: str = Field(min_length=1, max_length=300)
    outcome: str = Field(min_length=1, max_length=300)
    timepoint: str = Field(min_length=1, max_length=100)
    source_key: str = Field(min_length=1, max_length=300)
    source_locator: str = Field(min_length=1, max_length=500)
    format_name: Literal["independent_summary_stats", "independent_t_n",
                         "independent_d_n", "independent_p_n", "independent_f_n",
                         "independent_g_n"]
    values: dict[str, object]
    effect_direction: Literal["first_vs_second", "second_vs_first"]
    append: bool = False


class FormatEffectIn(BaseModel):
    study_id: str = Field(min_length=1, max_length=200)
    comparison: str = Field(min_length=1, max_length=300)
    outcome: str = Field(min_length=1, max_length=300)
    timepoint: str = Field(min_length=1, max_length=100)
    source_key: str = Field(min_length=1, max_length=300)
    format_name: str = Field(pattern="^(correlation_r_n|hazard_ratio_ci|survival_logrank_oe_v|rate_ratio_events_time|generic_ci|generic_wald_p|paired_md_sd_diff|paired_md_correlation|parallel_change_sd|parallel_change_correlations|crossover_2x2_md|single_group_standardized_change|single_mean_sd_n|single_proportion_events_n|single_rate_events_time|single_mean_direct|single_proportion_direct|single_rate_direct|independent_t_n|independent_p_n|independent_f_n|independent_d_n|cluster_trial_design_effect|cluster_trial_variable_size_design_effect|zero_cell_binary|ratio_of_means_two_arm|glass_delta_two_arm|binary_correlation_conversion)$")
    standardizer: str | None = Field(default=None, pattern="^(pretest_sd|change_sd)$")
    values: dict[str, object]
    source_locator: str = Field(default="", max_length=500)
    append: bool = False
    effect_direction: Literal["first_vs_second", "second_vs_first", "not_applicable"] | None = None


class ZeroCellPreviewIn(BaseModel):
    events_t: int = Field(ge=0)
    total_t: int = Field(ge=1)
    events_c: int = Field(ge=0)
    total_c: int = Field(ge=1)
    measure: str = Field(pattern="^(OR|RR)$")
    correction: str | float


class BayesianNormalMetaIn(BaseModel):
    comparison: str = Field(min_length=1)
    outcome: str = Field(min_length=1)
    timepoint: str = Field(min_length=1)
    measure: str = Field(min_length=1)
    mu_prior_mean: float
    mu_prior_sd: float = Field(gt=0)
    tau_prior_scale: float = Field(gt=0)


class MultilevelRandomIn(BaseModel):
    result_ids: list[int] = Field(min_length=5, max_length=500)
    comparison: str = Field(min_length=1)
    measure: str = Field(min_length=1)
    construct_confirmed: bool
    sampling_error_assumption: str = Field(pattern="^(independent_within_studies|supplied_known_within_study_covariance)$")
    within_study_covariances: dict[str, list[list[float]]] | None = None


class MetaModeratorIn(BaseModel):
    dimension_id: int = Field(ge=1)
    kind: str = Field(pattern="^(continuous|categorical)$")
    reference: str | None = Field(default=None, max_length=4000)


class MultiMetaRegressionIn(BaseModel):
    comparison: str = Field(min_length=1, max_length=300)
    outcome: str = Field(min_length=1, max_length=300)
    timepoint: str = Field(min_length=1, max_length=100)
    measure: str = Field(pattern="^(RR|OR|RD|MD|SMD|HR|RATE_RATIO|FISHER_Z)$")
    moderators: list[MetaModeratorIn] = Field(min_length=1, max_length=20)
    interactions: list[tuple[int, int]] = Field(default_factory=list, max_length=20)
    ci_method: str = Field(pattern="^(normal|knha)$")
    vcov_type: Literal['cr0','cr1','cr2'] | None = None
    model: Literal['fixed','dl','reml'] | None = None
    level: float | None = Field(default=None,gt=0,lt=1,allow_inf_nan=False)


class DependentSynthesisBlockIn(BaseModel):
    study_id: str = Field(min_length=1, max_length=200)
    result_ids: list[int] = Field(min_length=1, max_length=20)
    covariance: list[list[float]] = Field(min_length=1, max_length=20)
    covariance_source_note: str = Field(min_length=1, max_length=2000)


class DependentSynthesisIn(BaseModel):
    blocks: list[DependentSynthesisBlockIn] = Field(min_length=2, max_length=100)
    model: Literal['fixed', 'random_reml_un']


class NetworkContrastIn(BaseModel):
    treatment: str = Field(min_length=1, max_length=200)
    comparator: str = Field(min_length=1, max_length=200)
    estimate: float


class NetworkStudyIn(BaseModel):
    study_id: str = Field(min_length=1, max_length=200)
    source_key: str = Field(min_length=1, max_length=300)
    source_locator: str = Field(default="", max_length=500)
    outcome: str = Field(min_length=1, max_length=300)
    timepoint: str = Field(min_length=1, max_length=100)
    measure: str = Field(pattern="^(RR|OR|RD|MD|SMD)$")
    contrasts: list[NetworkContrastIn] = Field(min_length=1, max_length=20)
    covariance: list[list[float]] = Field(min_length=1, max_length=20)
    covariance_scale: str = Field(pattern="^(log|natural)$")


class NetworkArmsIn(BaseModel):
    study_id: str = Field(min_length=1, max_length=200)
    source_key: str = Field(min_length=1, max_length=300)
    source_locator: str = Field(default="", max_length=500)
    outcome: str = Field(min_length=1, max_length=300)
    timepoint: str = Field(min_length=1, max_length=100)
    measure: str = Field(pattern="^(MD|RR|OR)$")
    reference: str = Field(min_length=1, max_length=200)
    arms: list[dict] = Field(min_length=3, max_length=20)


class DtaThresholdIn(BaseModel):
    index_test: str = Field(min_length=1, max_length=300)
    target_condition: str = Field(min_length=1, max_length=300)
    reference_standard: str | None = Field(default=None, max_length=300)
    threshold_by_study: dict[str, float] | None = None


class RobSensitivityIn(BaseModel):
    comparison: str = Field(min_length=1)
    outcome: str = Field(min_length=1)
    timepoint: str = Field(min_length=1)
    measure: str = Field(min_length=1)
    framework: str = Field(min_length=1)
    exclude_judgements: list[str] = Field(min_length=1)
    model: str
    ci_method: str


class RobIn(BaseModel):
    study_id: str = Field(min_length=1, max_length=200)
    comparison: str = Field(default="", max_length=300)
    outcome: str = Field(min_length=1, max_length=300)
    timepoint: str = Field(min_length=1, max_length=100)
    framework: str
    domains: dict[str, dict[str, str]]
    overall: str
    overall_rationale: str = Field(min_length=1, max_length=4000)
    source_key: str = Field(min_length=1, max_length=300)
    source_locator: str = Field(default="", max_length=500)


class DtaResultIn(BaseModel):
    study_id: str = Field(min_length=1, max_length=200)
    index_test: str = Field(min_length=1, max_length=300)
    target_condition: str = Field(min_length=1, max_length=300)
    threshold: str = Field(min_length=1, max_length=100)
    reference_standard: str = Field(min_length=1, max_length=300)
    tp: int = Field(ge=0)
    fp: int = Field(ge=0)
    fn: int = Field(ge=0)
    tn: int = Field(ge=0)
    source_key: str = Field(min_length=1, max_length=300)
    source_locator: str = Field(default="", max_length=500)


class DoseCurveIn(BaseModel):
    study_id: str = Field(min_length=1, max_length=200)
    outcome: str = Field(min_length=1, max_length=300)
    timepoint: str = Field(min_length=1, max_length=100)
    measure: str = Field(pattern="^(RR|OR|RD|MD|SMD|RATE_RATIO)$")
    reference_dose: float
    dose_unit: str = Field(min_length=1, max_length=100)
    contrasts: list[dict[str, float]] = Field(min_length=2, max_length=100)
    covariance: list[list[float]] = Field(min_length=2, max_length=100)
    source_key: str = Field(min_length=1, max_length=300)
    source_locator: str = Field(default="", max_length=1000)


class DoseNonlinearIn(BaseModel):
    outcome: str = Field(min_length=1, max_length=300)
    timepoint: str = Field(min_length=1, max_length=100)
    measure: str = Field(pattern="^(RR|OR|RD|MD|SMD|RATE_RATIO)$")
    knots: list[float] = Field(min_length=3, max_length=3)
    prediction_reference_dose: float | None = None


class IpdSurvivalIn(BaseModel):
    participants: list[dict[str, object]] = Field(min_length=4, max_length=100000)
    study_sources: dict[str, str] = Field(min_length=1)
    ties: str = Field(pattern="^(efron|breslow)$")


class IpdAdjustedSurvivalIn(IpdSurvivalIn):
    covariate_center: float
    covariate_scale: float = Field(gt=0)


class IpdInteractionIn(BaseModel):
    rows: list[dict[str, object]] = Field(min_length=4, max_length=100000)
    study_sources: dict[str, str] = Field(min_length=2)
    covariate_center: float


class IpdStudyIn(BaseModel):
    study_id: str = Field(min_length=1, max_length=200)
    comparison: str = Field(min_length=1, max_length=300)
    outcome: str = Field(min_length=1, max_length=300)
    timepoint: str = Field(min_length=1, max_length=100)
    measure: str = Field(pattern="^(RR|OR|RD|MD|SMD)$")
    effect_direction: Literal["first_vs_second", "second_vs_first"]
    source_key: str = Field(min_length=1, max_length=300)
    participants: list[dict] = Field(min_length=4, max_length=100000)


class QualFindingIn(BaseModel):
    study_id: str = Field(min_length=1, max_length=200)
    source_key: str = Field(min_length=1, max_length=300)
    finding: str = Field(min_length=1, max_length=8000)
    illustration: str = Field(default="", max_length=8000)
    locator: str = Field(default="", max_length=300)
    credibility: str = Field(pattern="^(unequivocal|credible|unsupported)$")


class QualCategoryIn(BaseModel):
    label: str = Field(min_length=1, max_length=1000)
    finding_ids: list[int] = Field(min_length=1, max_length=500)


class QualSynthesisIn(BaseModel):
    finding: str = Field(min_length=1, max_length=8000)
    category_ids: list[int] = Field(min_length=1, max_length=500)


class HighlightEventIn(BaseModel):
    kind: str = Field(default="highlight", pattern="^(highlight|note)$")
    page: int = Field(ge=1, le=100000)
    text: str = Field(default="", max_length=8000)
    color: str = Field(default="jade", pattern="^(jade|amber|terra)$")
    note: str = Field(default="", max_length=4000)
    rects: list[list[float]] = Field(default_factory=list, max_length=200)


class HighlightsIn(BaseModel):
    zotero_key: str = Field(min_length=1, max_length=300)
    events: list[HighlightEventIn] = Field(min_length=1, max_length=500)


class SchemeImportIn(BaseModel):
    scheme: dict
    replace: bool = True


class ALSettingsIn(BaseModel):
    """主动学习（AI 排序）设置：全部可选，缺省字段不覆盖已存值。"""

    strategy: str | None = Field(default=None, pattern="^(max|uncertainty|mixed)$")
    seed: int | None = Field(default=None, ge=-(2**31), le=2**31)
    min_labeled: int | None = Field(default=None, ge=1, le=1000)
    stop_threshold: int | None = Field(default=None, ge=0, le=10000)


class PrismaGenerateIn(BaseModel):
    """任务书契约：``POST /api/prisma/generate`` 的请求体为 ``{"form": {...}}``。"""

    form: PrismaFormIn


class PrismaRowIn(BaseModel):
    source_type: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=300)
    n: int = Field(ge=0, le=10**9)


class PrismaReasonIn(BaseModel):
    reason: str = Field(min_length=1, max_length=300)
    n: int = Field(ge=0, le=10**9)


class PrismaFormIn(BaseModel):
    review_mode: str = Field(default="new", pattern="^(new|updated|dta)$")
    sources_scope: str = Field(default="db_reg", pattern="^(db_reg|db_reg_other)$")
    identified_rows_db: list[PrismaRowIn] = Field(default_factory=list, max_length=50)
    identified_rows_other: list[PrismaRowIn] = Field(default_factory=list, max_length=50)
    duplicates_removed: int = Field(default=0, ge=0, le=10**9)
    automation_ineligible: int = Field(default=0, ge=0, le=10**9)
    removed_other: int = Field(default=0, ge=0, le=10**9)
    removed_other_note: str = Field(default="", max_length=200)
    records_screened: int = Field(default=0, ge=0, le=10**9)
    records_excluded_screening: int = Field(default=0, ge=0, le=10**9)
    screening_exclusion_reasons: list[PrismaReasonIn] = Field(default_factory=list, max_length=100)
    human_excluded_n: int | None = Field(default=None, ge=0, le=10**9)
    automation_excluded_n: int | None = Field(default=None, ge=0, le=10**9)
    reports_sought_db: int = Field(default=0, ge=0, le=10**9)
    reports_not_retrieved_db: int = Field(default=0, ge=0, le=10**9)
    reports_assessed_db: int = Field(default=0, ge=0, le=10**9)
    exclusion_reasons_db: list[PrismaReasonIn] = Field(default_factory=list, max_length=100)
    studies_included: int = Field(default=0, ge=0, le=10**9)
    reports_of_included_studies: int = Field(default=0, ge=0, le=10**9)
    reports_sought_other: int = Field(default=0, ge=0, le=10**9)
    reports_not_retrieved_other: int = Field(default=0, ge=0, le=10**9)
    reports_assessed_other: int = Field(default=0, ge=0, le=10**9)
    exclusion_reasons_other: list[PrismaReasonIn] = Field(default_factory=list, max_length=100)
    previous_studies: int = Field(default=0, ge=0, le=10**9)
    previous_reports: int = Field(default=0, ge=0, le=10**9)
    new_studies: int = Field(default=0, ge=0, le=10**9)
    new_reports: int = Field(default=0, ge=0, le=10**9)
    total_studies: int = Field(default=0, ge=0, le=10**9)
    total_reports: int = Field(default=0, ge=0, le=10**9)
    studies_included_meta_analysis: int | None = Field(default=None, ge=0, le=10**9)


# ---------------------------------------------------------------------------
# 公共助手
# ---------------------------------------------------------------------------

def _bad(message: str | ValueError, status: int = 400) -> HTTPException:
    from coscreen.pool_compatibility import PoolCompatibilityError

    if isinstance(message, PoolCompatibilityError):
        return HTTPException(status, {'code':'incompatible_effect_pool', 'message':str(message),
                                      'reasons':message.reasons})
    return HTTPException(status, str(message))


def _rob_contract_error(exc) -> HTTPException:
    status = 409 if exc.code == "revision_conflict" else 403 if exc.code == "reviewer_forbidden" else 400
    return HTTPException(status, {"error_code": exc.code, "message_key": "analysis.rob." + exc.code,
                                  "params": exc.details, "details": str(exc), "message": str(exc)})


def _dim_error(exc: ValueError) -> HTTPException:
    """编码维度的业务错误：维度不存在 -> 404，其余（如选项冲突）-> 400。"""
    return _bad(str(exc), 404 if "不存在" in str(exc) else 400)


def _task_or_404(task_id: str) -> tasks_mod.TaskInfo:
    """按 id 取任务；id 形状或目录逃逸校验失败一律 404（不泄露文件系统信息）。"""
    if not _TASK_ID_RE.match(task_id or "") or Path(task_id).name != task_id:
        raise _bad("任务不存在。", 404)
    info = tasks_mod.find_task(task_id, DATA_DIR)
    if info is None:
        raise _bad("任务不存在。", 404)
    if not is_within(info.dir, tasks_mod.tasks_root(DATA_DIR).resolve()):
        raise _bad("任务不存在。", 404)
    return info


def _screener_db(info: tasks_mod.TaskInfo, screener: str) -> Path:
    """筛选员 -> ``{task_dir}/{slug}.db``（懒建库；路径白名单校验）。"""
    slug = tasks_mod.slugify_screener(screener)
    db = info.dir / f"{slug}.db"
    if not is_within(db, info.dir):
        raise _bad("非法筛选员名。", 400)
    db_mod.init_db(db)  # 幂等：确保表齐全（首次使用即建库）
    return db


def _import_history_db(info: tasks_mod.TaskInfo) -> Path:
    path = info.dir / "import_history.sqlite3"
    if not is_within(path, info.dir):
        raise _bad("非法任务路径。", 400)
    return path


def _pdfs_dir(info: tasks_mod.TaskInfo) -> Path:
    d = info.dir / "pdfs"
    if not is_within(d, info.dir):
        raise _bad("非法路径。", 400)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _article_dump(art) -> dict:
    """Article -> JSON dict（raw 展开为对象，避免双重编码）。"""
    d = art.to_dict()
    try:
        d["raw"] = json.loads(d.get("raw") or "{}")
    except (ValueError, TypeError):
        d["raw"] = {}
    return d


def _run_export(exporter, suffix: str) -> bytes:
    """把 coscreen 导出函数的落盘产物读为字节（临时目录，用后即删）。"""
    workdir = Path(tempfile.mkdtemp(prefix="cobook_export_"))
    try:
        out = exporter(workdir / f"export{suffix}")
        return Path(out).read_bytes()
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _file_response(data: bytes, media_type: str, filename: str) -> Response:
    safe_name = clean_upload_name(filename, "download")
    ascii_name = safe_name.encode("ascii", "ignore").decode("ascii") or "download"
    return Response(
        content=data,
        media_type=media_type,
        headers={
            "Content-Disposition": (
                f"attachment; filename=\"{ascii_name}\"; "
                f"filename*=UTF-8''{safe_name}"
            )
        },
    )


def _frame_rows(frame: pd.DataFrame, limit: int = 2000) -> list[dict]:
    """DataFrame -> JSON 安全行列表（NaN/numpy 已处理，行数封顶）。"""
    text = frame.head(limit).to_json(orient="records", force_ascii=False)
    return json.loads(text) if text else []


def _upload_text_frame(filename: str, data: bytes) -> pd.DataFrame:
    """上传字节 -> 决策 DataFrame（列校验用 coscreen.load_decisions_csv）。"""
    tmpdir = Path(tempfile.mkdtemp(prefix="cobook_upload_"))
    try:
        path = tmpdir / clean_upload_name(filename, "decisions.csv")
        path.write_bytes(data)
        return load_decisions_csv(path)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _upload_matrix_frame(data: bytes) -> pd.DataFrame:
    """上传字节 -> 编码矩阵 DataFrame（宽表；zotero_key 必需由 merge_coding 校验）。"""
    return pd.read_csv(
        BytesIO(data), dtype=str, keep_default_na=False, encoding="utf-8-sig"
    )


# ---------------------------------------------------------------------------
# 健康检查与认证
# ---------------------------------------------------------------------------

@app.get("/api/health")
def health() -> dict:
    """存活探针（公开；不含任何用户或系统信息）。"""
    return {"status": "ok"}


@app.get("/api/runtime")
def runtime() -> dict:
    return {"mode": "server" if _server_mode() else "desktop",
            "storage": "server" if _server_mode() else "local"}


@app.get("/api/app-update/check")
def app_update_check() -> dict:
    """Check the public stable GitHub Release; server deployments cannot self-update."""
    if _server_mode():
        return {"supported": False, "current_version": current_version(), "has_update": False}
    return {"supported": True, **check_latest_release()}


@app.post("/api/auth/register")
def register(body: CredentialsIn, request: Request) -> dict:
    allowed, retry_after = login_limiter.check(f"register:{request.client.host if request.client else '?'}")
    if not allowed:
        raise HTTPException(429, "注册尝试过于频繁，请稍后再试。", headers={"Retry-After": str(retry_after)})
    try:
        user = auth.create_user(body.username, body.password)
    except auth.AuthError as exc:
        raise _bad(str(exc)) from exc
    return {"token": auth.create_access_token(user["id"], user["username"]), "user": user}


@app.post("/api/auth/login")
def login(body: CredentialsIn, request: Request) -> dict:
    allowed, retry_after = login_limiter.check(f"login:{request.client.host if request.client else '?'}")
    if not allowed:
        raise HTTPException(429, "登录尝试过于频繁，请 1 分钟后再试。", headers={"Retry-After": str(retry_after)})
    user = auth.authenticate(body.username, body.password)
    if user is None:
        raise _bad("用户名或密码错误。", 401)
    return {"token": auth.create_access_token(user["id"], user["username"]), "user": user}


@app.get("/api/auth/me")
def me(user: dict = Depends(require_user)) -> dict:
    return {"user_id": user["user_id"], "username": user["username"]}


# ---------------------------------------------------------------------------
# 任务 CRUD
# ---------------------------------------------------------------------------

def _task_payload(info: tasks_mod.TaskInfo) -> dict:
    return {
        "task_id": info.task_id,
        "name": info.display_name,
        "description": info.description,
        "screeners": tasks_mod.screener_options(info, DATA_DIR),
        "created_at": info.created_at,
        "last_used_at": info.last_used_at,
        "archived": info.archived,
        "degraded": info.degraded,
    }


@app.get("/api/tasks")
def list_all_tasks(
    include_archived: bool = False, user: dict = Depends(require_user)
) -> dict:
    infos = tasks_mod.list_tasks(DATA_DIR, include_archived=include_archived)
    # 水平权限：仅返回当前用户属主/协作参与的任务；legacy 无协作库的任务
    # 尚无属主记录（显式迁移路径未定），保持历史可见性。
    visible = [i for i in infos if _task_visible_to(i, user)]
    return {"tasks": [_task_payload(i) for i in visible]}


@app.post("/api/tasks", status_code=201)
def create_task(body: TaskCreateIn, user: dict = Depends(require_user)) -> dict:
    try:
        info = tasks_mod.create_task(body.name, body.description, DATA_DIR)
        collaboration_mod.initialize_task_store(
            info.dir, task_id=info.task_id, owner_user_id=user["user_id"],
            owner_username=user["username"],
        )
        tasks_mod.register_screener(info.task_id, user["username"], DATA_DIR)
        info = tasks_mod.find_task(info.task_id, DATA_DIR) or info
    except ValueError as exc:
        raise _bad(str(exc), 409) from exc
    return _task_payload(info)


def _collaboration_store(info: tasks_mod.TaskInfo) -> Path:
    path = collaboration_mod.shared_store_path(info.dir)
    if not is_within(path, info.dir):
        raise _bad("非法任务路径。", 400)
    return path


def _collaboration_owner_store(task_id: str, user: dict) -> Path:
    info = _task_or_404(task_id)
    path = _collaboration_store(info)
    owner = collaboration_mod.get_owner(path, task_id=info.task_id)
    if owner is None:
        raise HTTPException(409, {"error_code": "collaboration_owner_unset",
                                  "message_key": "coding.collaboration_owner_unset", "params": {},
                                  "details": "Legacy task needs explicit owner migration",
                                  "message": "Legacy task needs explicit owner migration"})
    if owner["owner_user_id"] != user["user_id"]:
        raise HTTPException(403, "Only the task owner can manage collaborators.")
    return path


def _task_visible_to(info: tasks_mod.TaskInfo, user: dict) -> bool:
    """任务对当前用户是否可见：属主/协作者可见，legacy 无属主任务保持可见。"""
    try:
        path = _collaboration_store(info)
    except HTTPException:
        return False
    owner = collaboration_mod.get_owner(path, task_id=info.task_id)
    if owner is None:
        return not _server_mode()
    return (owner["owner_user_id"] == user["user_id"]
            or collaboration_mod.is_participant(path, user["user_id"],
                                                task_id=info.task_id))


def _require_task_owner(task_id: str, user: dict) -> tasks_mod.TaskInfo:
    """任务改名/描述/归档/删除的属主闸门。

    已登记属主的任务仅属主可改（非属主 403）；legacy 任务尚无属主记录
    （创建于协作库机制之前、迁移路径未定），保持既有可管理性。
    """
    info = _task_or_404(task_id)
    owner = collaboration_mod.get_owner(_collaboration_store(info), task_id=info.task_id)
    if owner is not None and owner["owner_user_id"] != user["user_id"]:
        raise HTTPException(403, "Only the task owner can modify or delete this task.")
    return info


def _run_coding_freeze(info: tasks_mod.TaskInfo, user: dict,
                       freeze_id: int | None) -> dict | None:
    if freeze_id is None:
        return None
    from coscreen.coding_consensus import get_freeze
    if type(freeze_id) is not int or freeze_id <= 0:
        raise _bad("Invalid coding freeze ID.")
    store = _collaboration_store(info)
    if not collaboration_mod.authorize_action(
            store, user["user_id"], "consensus:read", task_id=info.task_id):
        raise HTTPException(403, "Task coding access denied.")
    freeze = get_freeze(store, freeze_id)
    if freeze is None:
        raise _bad("Coding freeze not found.", 404)
    return {key: freeze[key] for key in ("id", "name", "version", "content_sha256")}


@app.get("/api/tasks/{task_id}/collaboration/roles")
def collaboration_roles(task_id: str, user: dict = Depends(require_user)) -> dict:
    path = _collaboration_owner_store(task_id, user)
    return {"roles": collaboration_mod.list_role_grants(
        path, actor_user_id=user["user_id"], task_id=task_id)}


@app.post("/api/tasks/{task_id}/collaboration/invite", status_code=201)
def collaboration_invite(task_id: str, body: CollaborationInviteIn,
                         user: dict = Depends(require_user)) -> dict:
    if not _server_mode():
        raise HTTPException(404, "团队协作仅在服务器模式下开放。")
    path = _collaboration_owner_store(task_id, user)
    target = auth.get_user(body.username)
    if target is None:
        raise HTTPException(404, "该账号不存在。请对方先在此服务器注册。")
    grant = collaboration_mod.grant_role(
        path, actor_user_id=user["user_id"], user_id=target["id"],
        role=body.role, task_id=task_id)
    tasks_mod.register_screener(task_id, target["username"], DATA_DIR)
    return {"grant": {**grant, "username": target["username"]}}


@app.get("/api/tasks/{task_id}/team-progress")
def team_progress(task_id: str, user: dict = Depends(require_user)) -> dict:
    if not _server_mode():
        raise HTTPException(404, "团队进度仅在服务器模式下开放。")
    info = _task_or_404(task_id)
    store = _collaboration_store(info)
    owner = collaboration_mod.get_owner(store, task_id=task_id)
    if owner is None:
        raise HTTPException(409, "旧任务需先完成属主迁移，才能使用团队进度。")
    try:
        ids = collaboration_mod.list_participant_ids(
            store, actor_user_id=user["user_id"], task_id=task_id)
    except PermissionError as exc:
        raise HTTPException(403, "当前账号不是该任务的参与者。") from exc
    names = [account["username"] for uid in ids
             if (account := auth.get_user_by_id(uid)) is not None]
    from custom_backend.team_progress import list_team_progress
    return {"members": list_team_progress(task_id, names, DATA_DIR),
            "can_manage": owner["owner_user_id"] == user["user_id"]}


@app.post("/api/tasks/{task_id}/collaboration/roles", status_code=201)
def collaboration_grant_role(task_id: str, body: CollaborationRoleIn,
                             user: dict = Depends(require_user)) -> dict:
    path = _collaboration_owner_store(task_id, user)
    target = auth.get_user_by_id(body.user_id)
    if target is None:
        raise HTTPException(400, {"error_code": "collaborator_not_found",
                                  "message_key": "coding.collaborator_not_found", "params": {},
                                  "details": "Account ID does not exist", "message": "Account ID does not exist"})
    grant = collaboration_mod.grant_role(
        path, actor_user_id=user["user_id"], user_id=body.user_id,
        role=body.role, task_id=task_id)
    return {"grant": {**grant, "username": target["username"]}}


@app.delete("/api/tasks/{task_id}/collaboration/roles/{target_user_id}/{role}")
def collaboration_revoke_role(task_id: str, target_user_id: int,
                              role: Literal["coder", "reconciler"],
                              user: dict = Depends(require_user)) -> dict:
    path = _collaboration_owner_store(task_id, user)
    return {"revoked": collaboration_mod.revoke_role(
        path, actor_user_id=user["user_id"], user_id=target_user_id,
        role=role, task_id=task_id)}


@app.put("/api/tasks/{task_id}")
def update_task(
    task_id: str, body: TaskUpdateIn, user: dict = Depends(require_user)
) -> dict:
    _require_task_owner(task_id, user)
    try:
        if body.name is not None:
            tasks_mod.rename_task(task_id, body.name, DATA_DIR)
        if body.description is not None:
            info = tasks_mod.find_task(task_id, DATA_DIR)
            if info is None or info.degraded:
                raise ValueError("任务描述文件已损坏，无法修改。")
            info.description = body.description.strip()
            tasks_mod._write_task_json(  # noqa: SLF001  与 rename 同一条写路径
                info.dir, tasks_mod._json_payload(info)
            )
        if body.archived is not None:
            tasks_mod.archive_task(task_id, DATA_DIR, archived=body.archived)
    except ValueError as exc:
        raise _bad(str(exc), 409) from exc
    return _task_payload(tasks_mod.find_task(task_id, DATA_DIR))


@app.delete("/api/tasks/{task_id}")
def delete_task(task_id: str, user: dict = Depends(require_user)) -> dict:
    _require_task_owner(task_id, user)
    try:
        trash_path = tasks_mod.delete_task(task_id, DATA_DIR)
    except ValueError as exc:
        raise _bad(str(exc), 409) from exc
    return {"deleted": True, "trash": trash_path.name}


# ---------------------------------------------------------------------------
# 文献与导入
# ---------------------------------------------------------------------------

@app.get("/api/tasks/{task_id}/articles")
def list_articles(
    task_id: str,
    screener: str | None = None,
    include_duplicates: bool = False,
    user: dict = Depends(require_user),
) -> dict:
    info = _task_or_404(task_id)
    if _server_mode():
        owner = collaboration_mod.get_owner(_collaboration_store(info), task_id=task_id)
        if owner is not None and owner["owner_user_id"] != user["user_id"]:
            owner_db = info.dir / f"{tasks_mod.slugify_screener(owner['owner_username'])}.db"
            if owner_db.is_file():
                from custom_backend.shared_corpus import sync_shared_articles
                sync_shared_articles(
                    info.dir, owner_username=owner["owner_username"],
                    member_username=user["username"],
                )
    db = _screener_db(info, screener or user["username"])
    articles = db_mod.list_articles(db, include_duplicates=include_duplicates)
    review = db_mod.title_duplicate_review_progress(db)
    return {"articles": [_article_dump(a) for a in articles], "total": len(articles),
            "title_review_pending": review["pending_groups"],
            "title_review_progress": review}


async def _parse_upload_to_articles(upload: UploadFile, tmpdir: Path, idx: int) -> tuple[str, list, Path]:
    """校验并把一个上传文件解析为 Article 列表（落盘到 tmpdir，调用方负责清理）。

    返回 (清洗后的文件名, 文章列表, 原件临时路径)；调用方负责清理 tmpdir。
    """
    name = clean_upload_name(upload.filename or "", "import")
    data = await upload.read()
    check_extension(name, TEXT_EXTENSIONS)
    check_text_bytes(data)
    src = tmpdir / f"{idx}_{name}"  # 序号前缀防同名文件互相覆盖
    src.write_bytes(data)
    try:
        return name, parse_file(src), src
    except ParseError as exc:
        raise _bad(f"解析失败（{name}）：{exc}") from exc


_IMPORT_PROGRESS_DIR = Path(tempfile.gettempdir()) / "reviewflow_import_progress"


def _import_progress_path(job_id: str) -> Path:
    try:
        safe_id = str(uuid.UUID(job_id))
    except (ValueError, AttributeError) as exc:
        raise _bad("导入进度编号无效。", 400) from exc
    return _IMPORT_PROGRESS_DIR / f"{safe_id}.json"


def _set_import_progress(job_id: str | None, task_id: str, user_id: str,
                         phase: str, percent: int, **extra) -> None:
    if not job_id:
        return
    path = _import_progress_path(job_id)
    payload = {"job_id": str(uuid.UUID(job_id)), "task_id": task_id,
               "user_id": user_id, "phase": phase,
               "percent": max(0, min(100, int(percent))), **extra}
    temp = path.with_suffix(f".{threading.get_ident()}.tmp")
    try:
        _IMPORT_PROGRESS_DIR.mkdir(parents=True, exist_ok=True)
        temp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        temp.replace(path)
    except OSError as exc:
        # Progress reporting must not interrupt an otherwise successful import.
        logger.debug("Unable to persist import progress %s: %s", job_id, exc)


def _fuzzy_progress(job_id: str | None, task_id: str, user_id: str,
                    stage: str, done: int, total: int,
                    start: int = 10, span: int = 85) -> None:
    if stage == "exact":
        percent = start
        phase = "exact"
    else:
        ratio = 1.0 if total <= 0 else min(1.0, done / total)
        percent = start + int(span * ratio)
        phase = "fuzzy"
    _set_import_progress(job_id, task_id, user_id, phase, percent,
                         completed=done, total=total)


def _keys_of(articles: list) -> set:
    return {a.zotero_key for a in articles}


def _stabilize_generated_import_keys(existing: list, incoming: list) -> None:
    """Keep legacy fallback keys when possible, while separating same-title rows.

    Generated ``auto-*`` values are storage identifiers, not evidence of a match.
    The source digest + row locator makes collision suffixes stable on re-import.
    """
    occupied = {a.zotero_key for a in existing}
    key_by_fallback_id: dict[str, str] = {}
    for article in existing:
        fallback_id = str(article.raw.get("_fallback_id") or "")
        if fallback_id:
            key_by_fallback_id[fallback_id] = article.zotero_key
    for article in incoming:
        if not article.raw.get("_generated_key"):
            continue
        base_key = str(article.raw.get("_fallback_key") or article.zotero_key)
        fallback_id = str(article.raw.get("_fallback_id") or "")
        known_key = key_by_fallback_id.get(fallback_id) if fallback_id else None
        if known_key:
            chosen = known_key
        elif base_key not in occupied:
            chosen = base_key
        else:
            suffix = hashlib.sha256(fallback_id.encode("utf-8")).hexdigest()[:12]
            chosen = f"{base_key}-{suffix}"
            serial = 2
            while chosen in occupied:
                chosen = f"{base_key}-{suffix}-{serial}"
                serial += 1
        article.zotero_key = chosen
        occupied.add(chosen)
        if fallback_id:
            key_by_fallback_id[fallback_id] = chosen


def _title_review_groups(exact_survivors: list, incoming_keys: set,
                         fuzzy_pairs: list | None = None) -> list[dict]:
    """Combine identical-title and fuzzy title/author links for human review."""
    eligible = [article for article in exact_survivors if not article.is_duplicate_of]
    articles_by_key = {article.zotero_key: article for article in eligible}
    parent = {key: key for key in articles_by_key}

    def find(key: str) -> str:
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    def union(left: str, right: str) -> None:
        root_left, root_right = find(left), find(right)
        if root_left != root_right:
            parent[max(root_left, root_right)] = min(root_left, root_right)

    for group in find_title_candidate_groups(eligible):
        first_key = group[0].zotero_key
        for article in group[1:]:
            union(first_key, article.zotero_key)
    fuzzy_pairs = fuzzy_pairs or []
    for pair in fuzzy_pairs:
        if pair.kept_key in parent and pair.dup_key in parent:
            union(pair.kept_key, pair.dup_key)

    components: dict[str, list] = {}
    for key, article in articles_by_key.items():
        components.setdefault(find(key), []).append(article)

    fuzzy_keys = {key for pair in fuzzy_pairs for key in (pair.kept_key, pair.dup_key)}
    result = []
    for group in components.values():
        if len(group) < 2 or not any(a.zotero_key in incoming_keys for a in group):
            continue
        is_fuzzy = any(a.zotero_key in fuzzy_keys for a in group)
        members = []
        for article in group:
            raw = article.raw if isinstance(article.raw, dict) else {}
            def raw_value(*names):
                lowered = {str(k).lower(): v for k, v in raw.items()}
                for name in names:
                    value = lowered.get(name.lower())
                    if value not in (None, "", []):
                        return value
                return ""
            page_start = raw_value("start_page", "SP")
            page_end = raw_value("end_page", "EP")
            page_text = (f"{page_start}-{page_end}" if page_start and page_end
                         else raw_value("pages", "page", "start_page", "SP", "C7"))
            source_record = {k: v for k, v in raw.items() if not str(k).startswith("_")}
            members.append({
                "zotero_key": article.zotero_key,
                "title": article.title,
                "authors": article.authors,
                "year": article.year,
                "journal": article.journal,
                "volume": raw_value("volume", "VL"),
                "issue": raw_value("issue", "IS"),
                "pages": page_text,
                "doi": article.doi,
                "source_file": raw.get("source_file", ""),
                "imported_in_batch": article.zotero_key in incoming_keys,
                "match_kind": "fuzzy" if is_fuzzy else "title",
                "raw": source_record,
            })
        if is_fuzzy:
            digest = hashlib.sha256(
                "\0".join(sorted(a.zotero_key for a in group)).encode("utf-8")
            ).hexdigest()[:20]
            group_key = f"fuzzy:{digest}"
        else:
            group_key = normalize_title_candidate(group[0].title)
        result.append({
            "normalized_title": group_key,
            "members": members,
        })
    return result


def _exact_duplicate_metrics(report, incoming_keys: set | None = None,
                             previous_report=None) -> dict:
    hits = [p for p in report.exact_pairs
            if incoming_keys is None or p.dup_key in incoming_keys]
    groups = {p.kept_key for p in hits}
    previous_excess = (
        max(0, previous_report.total - previous_report.unique)
        if previous_report is not None else 0
    )
    return {
        "exact_match_hits": len(hits),
        "exact_duplicate_groups": len(groups),
        "exact_duplicate_excess": max(
            0, report.total - report.unique - previous_excess
        ),
    }


def _count_title_groups_with_incoming(groups: list[list], incoming_keys: set) -> tuple[int, int]:
    selected = []
    for group in groups:
        eligible = [article for article in group if not article.is_duplicate_of]
        if len(eligible) > 1 and any(a.zotero_key in incoming_keys for a in eligible):
            selected.append(eligible)
    return len(selected), sum(len(g) - 1 for g in selected)


@app.post("/api/tasks/{task_id}/import")
async def import_articles(
    task_id: str,
    file: UploadFile = File(...),
    job_id: str | None = Form(default=None),
    screener: str | None = None,
    user: dict = Depends(require_user),
) -> dict:
    info = _task_or_404(task_id)
    screener_name = screener or user["username"]
    db = _screener_db(info, screener_name)
    tmpdir = Path(tempfile.mkdtemp(prefix="cobook_import_"))
    try:
        _set_import_progress(job_id, task_id, user["user_id"], "parse", 2)
        name, articles, raw_path = await _parse_upload_to_articles(file, tmpdir, 0)
        for article in articles:
            article.raw["source_file"] = name

        # 与库中已有文献合并后统一去重：修复"先导 A 再导 B 时，B 不与 A 的文献去重"的缺口。
        existing = db_mod.list_articles(db, include_duplicates=True)
        _stabilize_generated_import_keys(existing, articles)
        combined = existing + articles
        title_groups_all = find_title_candidate_groups(combined)
        exact_survivors, exact_report = await asyncio.to_thread(
            find_duplicates, combined, include_fuzzy=False
        )
        _, previous_exact_report = find_duplicates(existing, include_fuzzy=False)
        _set_import_progress(job_id, task_id, user["user_id"], "fuzzy", 10,
                             completed=0, total=len(exact_survivors))
        _, report = await asyncio.to_thread(
            find_duplicates, combined,
            progress_callback=lambda stage, done, total: _fuzzy_progress(
                job_id, task_id, user["user_id"], stage, done, total
            ),
        )

        # DOI/Key 精确匹配仍自动标记；模糊匹配只进入核验清单，用户确认后才排除。
        pairs = report.exact_pairs
        new_keys = _keys_of(articles)
        existing_keys = _keys_of(existing)
        duplicates_against_db = sum(
            1 for p in pairs if p.dup_key in new_keys and p.kept_key in existing_keys
        )
        title_groups = _title_review_groups(
            exact_survivors,
            new_keys - existing_keys,
            report.fuzzy_pairs,
        )
        _set_import_progress(job_id, task_id, user["user_id"], "save", 97)
        summary = db_mod.upsert_articles(db, articles, pairs, title_groups)
        review_progress = db_mod.title_duplicate_review_progress(db)
        db_mod.set_meta(db, "task", info.display_name)
        db_mod.set_meta(db, "screener", screener_name)
        db_mod.set_meta(db, "source_file", name)
        tasks_mod.touch_task(info.task_id, DATA_DIR, throttle=0)
        result = {
            "n_parsed": len(articles),
            "n_imported": summary.n_imported,
            "n_duplicates": summary.n_duplicates,
            "n_new": summary.n_new,
            "n_existing": summary.n_existing,
            "unique_after_exact_dedup": max(
                0, len(articles) - max(0, exact_report.total - exact_report.unique
                                       - (previous_exact_report.total - previous_exact_report.unique))
            ),
            "fuzzy_candidate_excess": sum(
                1 for pair in report.fuzzy_pairs if pair.dup_key in new_keys
            ),
            "duplicates_against_db": duplicates_against_db,
            **_exact_duplicate_metrics(exact_report, new_keys, previous_exact_report),
            "title_candidate_groups_including_exact": _count_title_groups_with_incoming(title_groups_all, new_keys)[0],
            "title_candidate_excess_including_exact": _count_title_groups_with_incoming(title_groups_all, new_keys)[1],
            "title_review_groups": len(title_groups),
            "title_review_pending": review_progress["pending_groups"],
            "title_review_pending_records": review_progress["pending_records"],
            "title_review_excess": sum(len(g["members"]) - 1 for g in title_groups),
            "note": report.note,
            "progress": db_mod.get_progress(db),
        }
        result["import_id"] = import_history_mod.save_import(
            _import_history_db(info), task_id=task_id, screener=screener_name,
            import_type="single", stats=result, files=[(name, raw_path)],
        )
        _set_import_progress(job_id, task_id, user["user_id"], "complete", 100)
        return result
    except Exception as exc:
        _set_import_progress(job_id, task_id, user["user_id"], "error", 100,
                             error=str(exc))
        raise
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


@app.post("/api/tasks/{task_id}/import-batch")
async def import_articles_batch(
    task_id: str,
    files: list[UploadFile] = File(...),
    job_id: str | None = Form(default=None),
    screener: str | None = None,
    user: dict = Depends(require_user),
) -> dict:
    """多文件批量导入 + 跨文件全局去重（系统综述合并多数据库导出的场景）。

    所有文件一起解析 → 合并为一个列表（每条打上 source_file 标签）→
    对合并列表跑 find_duplicates（天然得到跨文件去重）→ upsert 去重后的保留条目
    → 返回逐文件统计 + 跨文件重复明细。
    """
    info = _task_or_404(task_id)
    screener_name = screener or user["username"]
    db = _screener_db(info, screener_name)
    if not files:
        raise _bad("请至少选择一个文件。", 400)
    if len(files) > 20:
        raise _bad(f"批量导入一次最多 20 个文件（收到 {len(files)} 个），请分批上传。", 400)

    tmpdir = Path(tempfile.mkdtemp(prefix="cobook_import_"))
    try:
        _set_import_progress(job_id, task_id, user["user_id"], "parse", 2)
        per_file: list[dict] = []  # {"name", "parsed", "articles", "raw_path"}
        combined: list = []
        for i, f in enumerate(files):
            name, articles, raw_path = await _parse_upload_to_articles(f, tmpdir, i)
            for a in articles:
                a.raw["source_file"] = name  # 来源文件标签（随 raw_json 入库）
            combined.extend(articles)
            per_file.append({"name": name, "parsed": len(articles), "articles": articles,
                             "raw_path": raw_path})
            _set_import_progress(job_id, task_id, user["user_id"], "parse",
                                 min(9, 2 + int(7 * (i + 1) / len(files))),
                                 completed=i + 1, total=len(files))

        existing = db_mod.list_articles(db, include_duplicates=True)
        _stabilize_generated_import_keys(existing, combined)
        title_groups_all = find_title_candidate_groups(existing + combined)
        title_groups_with_incoming = _count_title_groups_with_incoming(title_groups_all, _keys_of(combined))
        exact_survivors, exact_report = await asyncio.to_thread(
            find_duplicates, combined, include_fuzzy=False
        )
        kept = exact_survivors
        pairs = exact_report.exact_pairs

        # key -> 来源文件 / 标题（用于逐文件重复计数与跨文件重复明细）
        source_of: dict[str, str] = {}
        title_of: dict[str, str] = {}
        for a in combined:
            source_of.setdefault(a.zotero_key, a.raw.get("source_file", ""))
            title_of.setdefault(a.zotero_key, a.title)

        dup_within: dict[str, int] = {pf["name"]: 0 for pf in per_file}
        cross_file_duplicates: list[dict] = []
        for p in pairs:
            kept_src, dup_src = source_of.get(p.kept_key, ""), source_of.get(p.dup_key, "")
            if kept_src and kept_src == dup_src:
                dup_within[kept_src] = dup_within.get(kept_src, 0) + 1
            else:
                cross_file_duplicates.append({
                    "kept_from": kept_src,
                    "dup_from": dup_src,
                    "method": p.method,
                    "title": title_of.get(p.dup_key, ""),
                })

        # 与库内已有文献做第二次精确匹配；模糊命中保留为人工核验候选。
        existing_keys = _keys_of(existing)
        global_articles = existing + kept
        global_exact_survivors, global_exact_report = await asyncio.to_thread(
            find_duplicates, global_articles, include_fuzzy=False
        )
        _set_import_progress(job_id, task_id, user["user_id"], "fuzzy", 10,
                             completed=0, total=global_exact_report.unique)
        _, global_fuzzy_report = await asyncio.to_thread(
            find_duplicates, global_articles,
            progress_callback=lambda stage, done, total: _fuzzy_progress(
                job_id, task_id, user["user_id"], stage, done, total,
                start=10, span=85,
            ),
        )
        global_pairs = global_exact_report.exact_pairs
        kept_keys = _keys_of(kept)
        duplicates_against_db = sum(
            1 for p in global_pairs
            if p.dup_key in kept_keys and p.kept_key in existing_keys and p.kept_key not in kept_keys
        )

        title_groups = _title_review_groups(
            global_exact_survivors,
            kept_keys - existing_keys,
            global_fuzzy_report.fuzzy_pairs,
        )
        _set_import_progress(job_id, task_id, user["user_id"], "save", 97)
        summary = db_mod.upsert_articles(db, kept, global_pairs, title_groups)
        review_progress = db_mod.title_duplicate_review_progress(db)
        db_mod.set_meta(db, "task", info.display_name)
        db_mod.set_meta(db, "screener", screener_name)
        tasks_mod.touch_task(info.task_id, DATA_DIR, throttle=0)
        result = {
            "files": [
                {"name": pf["name"], "parsed": pf["parsed"], "duplicates_within": dup_within.get(pf["name"], 0)}
                for pf in per_file
            ],
            "total_parsed": exact_report.total,
            "cross_file_duplicates": cross_file_duplicates,
            "exact_duplicates": exact_report.total - exact_report.unique,
            **_exact_duplicate_metrics(exact_report),
            "fuzzy_candidate_excess": sum(
                1 for pair in global_fuzzy_report.fuzzy_pairs
                if pair.dup_key in _keys_of(combined)
            ),
            "unique_after_dedup": exact_report.unique,
            "imported": summary.n_imported,
            "new": summary.n_new,
            "existing": summary.n_existing,
            "duplicates_against_db": duplicates_against_db,
            "title_candidate_groups_including_exact": title_groups_with_incoming[0],
            "title_candidate_excess_including_exact": title_groups_with_incoming[1],
            "title_review_groups": len(title_groups),
            "title_review_pending": review_progress["pending_groups"],
            "title_review_pending_records": review_progress["pending_records"],
            "title_review_excess": sum(len(g["members"]) - 1 for g in title_groups),
            "note": exact_report.note or global_fuzzy_report.note,
            "progress": db_mod.get_progress(db),
        }
        result["import_id"] = import_history_mod.save_import(
            _import_history_db(info), task_id=task_id, screener=screener_name,
            import_type="batch", stats=result,
            files=[(pf["name"], pf["raw_path"]) for pf in per_file],
        )
        _set_import_progress(job_id, task_id, user["user_id"], "complete", 100)
        return result
    except Exception as exc:
        _set_import_progress(job_id, task_id, user["user_id"], "error", 100,
                             error=str(exc))
        raise
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


@app.get("/api/tasks/{task_id}/import-progress/{job_id}")
def import_progress(task_id: str, job_id: str,
                    user: dict = Depends(require_user)) -> dict:
    """Read-only import progress, stored in a temp file so API workers can share it."""
    _task_or_404(task_id)
    path = _import_progress_path(job_id)
    if not path.is_file():
        return {"job_id": job_id, "phase": "starting", "percent": 1}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"job_id": job_id, "phase": "starting", "percent": 1}
    if state.get("task_id") != task_id or state.get("user_id") != user["user_id"]:
        raise _bad("找不到此导入进度。", 404)
    return {key: value for key, value in state.items() if key != "user_id"}


@app.get("/api/tasks/{task_id}/title-duplicate-reviews")
def list_title_duplicate_reviews(
    task_id: str,
    screener: str | None = None,
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    user: dict = Depends(require_user),
) -> dict:
    info = _task_or_404(task_id)
    db = _screener_db(info, screener or user["username"])
    groups, total = db_mod.list_title_duplicate_reviews(db, limit=limit, offset=offset)
    return {"groups": groups, "total": total, "offset": offset,
            "progress": db_mod.title_duplicate_review_progress(db)}


@app.post("/api/tasks/{task_id}/title-duplicate-reviews/{review_id}")
def decide_title_duplicate_review(
    task_id: str, review_id: str, body: TitleDuplicateDecisionIn,
    screener: str | None = None, user: dict = Depends(require_user),
) -> dict:
    info = _task_or_404(task_id)
    db = _screener_db(info, screener or user["username"])
    try:
        found = db_mod.decide_title_duplicate_review(
            db, review_id, body.decision, body.keeper_key, user["username"]
        )
    except ValueError as exc:
        raise _bad(str(exc), 400) from exc
    if not found:
        raise _bad("标题候选组不存在。", 404)
    tasks_mod.touch_task(info.task_id, DATA_DIR)
    return {"progress": db_mod.title_duplicate_review_progress(db),
            "screening": db_mod.get_progress(db)}


@app.get("/api/tasks/{task_id}/title-duplicate-reviews/export")
def export_title_duplicate_reviews(
    task_id: str, screener: str | None = None,
    user: dict = Depends(require_user),
) -> Response:
    info = _task_or_404(task_id)
    db = _screener_db(info, screener or user["username"])
    groups, _ = db_mod.list_title_duplicate_reviews(
        db, status=None, limit=1_000_000, offset=0
    )
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow([
        "review_id", "status", "decision_by", "updated_at", "normalized_title",
        "keeper_key", "zotero_key",
        "imported_in_batch", "source_file", "title", "authors", "year", "journal",
        "volume", "issue", "pages", "doi", "original_record_json",
    ])
    for group in groups:
        for member in group["members"]:
            writer.writerow([
                group["review_id"], group["status"], group["decision_by"],
                group["updated_at"], group["normalized_title"],
                group["keeper_key"], member["zotero_key"],
                member["imported_in_batch"], member["source_file"], member["title"],
                member["authors"], member["year"], member["journal"],
                member["volume"], member["issue"], member["pages"], member["doi"],
                json.dumps(member["raw"], ensure_ascii=False),
            ])
    data = ("\ufeff" + output.getvalue()).encode("utf-8")
    return _file_response(data, "text/csv; charset=utf-8", "title-duplicate-review.csv")


@app.get("/api/tasks/{task_id}/import-history")
def list_import_history(
    task_id: str,
    screener: str | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    user: dict = Depends(require_user),
) -> dict:
    info = _task_or_404(task_id)
    imports, next_offset = import_history_mod.list_imports(
        _import_history_db(info), task_id=task_id,
        screener=screener or user["username"],
        limit=limit, offset=offset,
    )
    return {"imports": imports, "next_offset": next_offset}


@app.get("/api/tasks/{task_id}/import-history/{batch_id}/files/{file_id}")
def download_import_file(
    task_id: str,
    batch_id: str,
    file_id: int,
    screener: str | None = None,
    user: dict = Depends(require_user),
) -> Response:
    info = _task_or_404(task_id)
    found = import_history_mod.get_import_file(
        _import_history_db(info), task_id=task_id,
        screener=screener or user["username"], import_id=batch_id, file_index=file_id,
    )
    if found is None:
        raise _bad("导入原文件不存在。", 404)
    filename, content = found
    filename = clean_upload_name(filename, "import")
    ascii_name = filename.encode("ascii", "ignore").decode("ascii") or "import"
    return Response(
        content=content,
        media_type="application/octet-stream",
        headers={"Content-Disposition": (
            f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{quote(filename, safe="")}'
        )},
    )


@app.get("/api/tasks/{task_id}/progress")
def progress(task_id: str, screener: str | None = None, user: dict = Depends(require_user)) -> dict:
    info = _task_or_404(task_id)
    db = _screener_db(info, screener or user["username"])
    return {
        "stage1": db_mod.get_progress(db),
        "stage2": get_stage2_progress(db),
        "coding": coding_mod.coding_progress(db),
    }


# ---------------------------------------------------------------------------
# 初筛 / 复筛决策
# ---------------------------------------------------------------------------

def _trace_context(info, user, source="manual"):
    try:
        from coscreen.research_trace import TraceContext
    except Exception as exc:
        # Disabled users retain the ordinary save path if this optional module fails.
        for path in {_screener_db(info, user["username"]), _coding_owner_db(info)}:
            try:
                with sqlite3.connect(path) as conn:
                    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='trace_settings'").fetchone():
                        continue
                    row = conn.execute("SELECT status FROM trace_settings WHERE task_id=? AND actor_id=?",
                                       (info.task_id, str(user["user_id"]))).fetchone()
                    if row and row[0] == "recording":
                        raise HTTPException(503, "本次修改尚未保存。轨迹记录不可用，请重试或暂停记录后重新保存。") from exc
            except sqlite3.Error as db_exc:
                raise HTTPException(503, "本次修改尚未保存。无法确认轨迹记录状态。") from db_exc
        return None
    return TraceContext(task_id=info.task_id, actor_id=user["user_id"], source=source, request_id=user.get("_trace_request_id"))


def _save_decision_checked(db: Path, body: DecisionIn, saver, *, trace_context=None) -> None:
    if db_mod.has_pending_title_duplicate_review(db, body.zotero_key):
        raise _bad("请先完成该条目的标题重复核验，再进行文献初筛。", 409)
    try:
        saver(db, body.zotero_key, body.decision, body.reason, body.notes, body.tags,
              **({"trace_context": trace_context} if trace_context is not None else {}))
    except ValueError as exc:
        raise _bad(str(exc)) from exc
    except sqlite3.IntegrityError as exc:
        # 外键约束：zotero_key 不在 articles 表（防错库/伪造键）
        raise _bad("文献键不存在（请确认已导入该文献）。", 400) from exc


@app.get("/api/tasks/{task_id}/decisions")
def list_decisions(task_id: str, screener: str | None = None, user: dict = Depends(require_user)) -> dict:
    info = _task_or_404(task_id)
    db = _screener_db(info, screener or user["username"])
    rows = db_mod.list_decisions(db)
    titles = {a.zotero_key: a.title for a in db_mod.list_articles(db, include_duplicates=True)}
    return {
        "decisions": [{**r, "title": titles.get(r["zotero_key"], "")} for r in rows],
        "total": len(rows),
    }


@app.post("/api/tasks/{task_id}/decisions")
def save_decision(task_id: str, body: DecisionIn, screener: str | None = None,
                  user: dict = Depends(require_user)) -> dict:
    info = _task_or_404(task_id)
    db = _screener_db(info, screener or user["username"])
    _save_decision_checked(db, body, db_mod.save_decision, trace_context=_trace_context(info, user))
    tasks_mod.touch_task(info.task_id, DATA_DIR)
    return {"saved": True, "progress": db_mod.get_progress(db)}


@app.get("/api/tasks/{task_id}/stage2/decisions")
def list_stage2(task_id: str, screener: str | None = None, user: dict = Depends(require_user)) -> dict:
    info = _task_or_404(task_id)
    db = _screener_db(info, screener or user["username"])
    rows = list_stage2_decisions(db)
    titles = {a.zotero_key: a.title for a in db_mod.list_articles(db, include_duplicates=True)}
    return {
        "decisions": [{**r, "title": titles.get(r["zotero_key"], "")} for r in rows],
        "total": len(rows),
    }


@app.post("/api/tasks/{task_id}/stage2/decisions")
def save_stage2_decision(task_id: str, body: DecisionIn, screener: str | None = None,
                         user: dict = Depends(require_user)) -> dict:
    info = _task_or_404(task_id)
    db = _screener_db(info, screener or user["username"])
    _save_decision_checked(db, body, save_stage2, trace_context=_trace_context(info, user))
    tasks_mod.touch_task(info.task_id, DATA_DIR)
    return {"saved": True, "progress": get_stage2_progress(db)}


@app.get("/api/tasks/{task_id}/stage2/queue")
def stage2_queue_ep(task_id: str, screener: str | None = None,
                    user: dict = Depends(require_user)) -> dict:
    """复筛队列（mode=own：本人初筛纳入，双盲）。"""
    info = _task_or_404(task_id)
    db = _screener_db(info, screener or user["username"])
    keys = stage2_queue(db, mode="own")
    by_key = {a.zotero_key: a for a in db_mod.list_articles(db, include_duplicates=True)}
    items = [
        {
            "zotero_key": k,
            "title": by_key[k].title,
            "journal": by_key[k].journal,
            "year": by_key[k].year,
            "authors": by_key[k].authors,
        }
        for k in keys if k in by_key
    ]
    return {"queue": items, "total": len(items)}


# ---------------------------------------------------------------------------
# 主动学习（AI 排序）：排序 / 状态 / 设置
# ---------------------------------------------------------------------------

_AL_STRATEGIES = ("max", "uncertainty", "mixed")


def _al_settings_path(info: tasks_mod.TaskInfo, screener: str) -> Path:
    """任务 + 筛选员 -> ``{task_dir}/al_settings_{slug}.json``（白名单校验）。"""
    slug = tasks_mod.slugify_screener(screener)
    path = info.dir / f"al_settings_{slug}.json"
    if not is_within(path, info.dir):
        raise _bad("非法筛选员名。", 400)
    return path


def _load_al_settings(info: tasks_mod.TaskInfo, screener: str) -> dict:
    """读 AL 设置（缺省取 coscreen.config）；形状非法的文件按默认值兜底。"""
    settings: dict = {
        "strategy": config.AL_DEFAULT_STRATEGY,
        "seed": int(config.AL_SEED),
        "min_labeled": int(config.AL_MIN_LABELED),
        "stop_threshold": int(config.AL_STOP_STREAK_EXCLUDE),
    }
    path = _al_settings_path(info, screener)
    if path.exists():
        try:
            stored = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            stored = None
        if isinstance(stored, dict):
            if stored.get("strategy") in _AL_STRATEGIES:
                settings["strategy"] = stored["strategy"]
            try:
                settings["seed"] = int(stored["seed"])
            except (KeyError, TypeError, ValueError):
                pass
            try:
                settings["min_labeled"] = max(1, int(stored["min_labeled"]))
            except (KeyError, TypeError, ValueError):
                pass
            try:
                settings["stop_threshold"] = max(0, int(stored["stop_threshold"]))
            except (KeyError, TypeError, ValueError):
                pass
    return settings


def _al_stop_payload(db: Path, stop_threshold: int) -> dict:
    """停止建议：按时间先后取决策值 -> 尾部连续排除数 -> 是否建议暂停。"""
    chrono = [str(r["decision"]) for r in db_mod.list_decisions_chrono(db)]
    streak = al_current_streak(chrono)
    return {
        "streak": streak,
        "threshold": int(stop_threshold),
        "suggest": al_suggest_stop(streak, int(stop_threshold)),
    }


def _al_n_labeled(db: Path) -> int:
    """可用标签数（include + exclude；maybe 不参与训练）。"""
    return sum(
        1 for r in db_mod.list_decisions(db)
        if r["decision"] in ("include", "exclude")
    )


@app.get("/api/tasks/{task_id}/al/rank")
def al_rank(task_id: str, screener: str | None = None,
            user: dict = Depends(require_user)) -> dict:
    """对当前筛选员的待筛队列做主动学习重排（复用 coscreen.al 全套实现）。"""
    info = _task_or_404(task_id)
    screener_name = screener or user["username"]
    db = _screener_db(info, screener_name)
    settings = _load_al_settings(info, screener_name)
    articles = db_mod.list_articles(db, include_duplicates=False)
    decisions = {
        r["zotero_key"]: r["decision"] for r in db_mod.list_decisions(db)
    }
    stop_suggestion = _al_stop_payload(db, settings["stop_threshold"])
    profile = _task_al_profile(info)
    if profile is not None:
        from coscreen.al.profiles import PROFILES

        if profile not in PROFILES:
            raise _bad(f"任务保存的 AI 模型预设无效：{profile}", 409)
    ranker = ActiveLearningRanker(
        strategy=settings["strategy"],
        seed=settings["seed"],
        min_labeled=settings["min_labeled"],
        profile=profile,
    )
    result = ranker.rank(articles, decisions)
    if result is None:
        return {
            "available": False,
            "reason": "not_enough_labels",
            "n_labeled": _al_n_labeled(db),
            "min_labeled": int(settings["min_labeled"]),
            "strategy": settings["strategy"],
            "seed": int(settings["seed"]),
            "order": [],
            "stop_suggestion": stop_suggestion,
        }
    order = [
        {"zotero_key": k, "score": float(result.scores.get(k, 0.0))}
        for k in result.order
    ]
    return {
        "available": True,
        "n_labeled": result.n_labeled,
        "min_labeled": int(settings["min_labeled"]),
        "strategy": result.strategy,
        "seed": result.seed,
        "order": order,
        "stop_suggestion": stop_suggestion,
    }


@app.get("/api/tasks/{task_id}/al/status")
def al_status(task_id: str, screener: str | None = None,
              user: dict = Depends(require_user)) -> dict:
    """轻量状态：标签数是否够训练 + 当前连续排除数与停止建议。"""
    info = _task_or_404(task_id)
    screener_name = screener or user["username"]
    db = _screener_db(info, screener_name)
    settings = _load_al_settings(info, screener_name)
    stop = _al_stop_payload(db, settings["stop_threshold"])
    n_labeled = _al_n_labeled(db)
    return {
        "available": n_labeled >= int(settings["min_labeled"]),
        "n_labeled": n_labeled,
        "strategy": settings["strategy"],
        "last_streak": stop["streak"],
        "suggest_stop": stop["suggest"],
    }


@app.get("/api/tasks/{task_id}/al/settings")
def al_get_settings(task_id: str, screener: str | None = None,
                    user: dict = Depends(require_user)) -> dict:
    info = _task_or_404(task_id)
    return _load_al_settings(info, screener or user["username"])


@app.put("/api/tasks/{task_id}/al/settings")
def al_update_settings(task_id: str, body: ALSettingsIn,
                       screener: str | None = None,
                       user: dict = Depends(require_user)) -> dict:
    """更新 AL 设置（按任务 + 筛选员存 JSON；未提供的字段保留原值）。"""
    info = _task_or_404(task_id)
    screener_name = screener or user["username"]
    path = _al_settings_path(info, screener_name)
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    settings = _load_al_settings(info, screener_name)
    settings.update(updates)
    path.write_text(
        json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return settings


# ---------------------------------------------------------------------------
# 导出
# ---------------------------------------------------------------------------

@app.get("/api/tasks/{task_id}/export/decisions")
def export_decisions_ep(task_id: str, screener: str | None = None,
                        user: dict = Depends(require_user)) -> Response:
    info = _task_or_404(task_id)
    db = _screener_db(info, screener or user["username"])
    data = _run_export(lambda out: export_decisions(db, out), ".csv")
    return _file_response(data, "text/csv; charset=utf-8", "decisions.csv")


@app.get("/api/tasks/{task_id}/export/stage2")
def export_stage2_ep(task_id: str, screener: str | None = None,
                     user: dict = Depends(require_user)) -> Response:
    info = _task_or_404(task_id)
    db = _screener_db(info, screener or user["username"])
    data = _run_export(lambda out: export_stage2_decisions(db, out), ".csv")
    return _file_response(data, "text/csv; charset=utf-8", "decisions2.csv")


@app.get("/api/tasks/{task_id}/export/matrix")
def export_matrix_ep(task_id: str, screener: str | None = None,
                     user: dict = Depends(require_user)) -> Response:
    info = _task_or_404(task_id)
    db = _screener_db(info, screener or user["username"])
    data = _run_export(lambda out: export_coding_matrix(db, out), ".csv")
    return _file_response(data, "text/csv; charset=utf-8", "coding_matrix.csv")


@app.get("/api/tasks/{task_id}/export/ris")
def export_ris_ep(
    task_id: str,
    decision: str = Query(default="include", pattern="^(include|exclude|maybe)$"),
    source: str = Query(default="stage1", pattern="^(stage1|stage2)$"),
    screener: str | None = None,
    user: dict = Depends(require_user),
) -> Response:
    info = _task_or_404(task_id)
    db = _screener_db(info, screener or user["username"])
    data = _run_export(
        lambda out: export_articles_ris(db, out, decision_filter=decision, decision_source=source),
        ".ris",
    )
    return _file_response(data, "application/x-research-info-systems; charset=utf-8", "included.ris")


@app.get("/api/tasks/{task_id}/export/highlights")
def export_highlights_ep(task_id: str, screener: str | None = None,
                         user: dict = Depends(require_user)) -> Response:
    info = _task_or_404(task_id)
    db = _screener_db(info, screener or user["username"])
    data = _run_export(lambda out: export_highlights_markdown(db, out), ".md")
    return _file_response(data, "text/markdown; charset=utf-8", "highlights.md")


# ---------------------------------------------------------------------------
# 合并与编码一致性（多文件上传）
# ---------------------------------------------------------------------------

async def _read_name_frame(upload: UploadFile, *, matrix: bool = False) -> tuple[str, pd.DataFrame]:
    """上传 -> (清洗后的名称主干, DataFrame)。

    ``matrix=False``：决策 CSV（load_decisions_csv 校验 zotero_key/decision 列）；
    ``matrix=True``：编码矩阵宽表（仅要求可读 CSV，列一致性交给 merge_coding）。
    CSV 解析失败（缺列 / 空文件 / 格式损坏）一律 400，绝不落到 500 兜底。
    """
    data = await upload.read()
    name = clean_upload_name(upload.filename or "", "upload")
    check_extension(name, TEXT_EXTENSIONS)
    check_text_bytes(data)
    try:
        frame = _upload_matrix_frame(data) if matrix else _upload_text_frame(name, data)
    except ValueError as exc:  # 含 pandas ParserError / EmptyDataError / 缺必需列
        raise _bad(f"无法解析 CSV「{name}」：{exc}") from exc
    return stem_of(name), frame


@app.post("/api/merge/decisions")
async def merge_decisions_ep(
    files: list[UploadFile] = File(...), user: dict = Depends(require_user)
) -> dict:
    """上传 2~9 位筛选员的决策 CSV -> 合并 + 一致率 + 两两 kappa + Light's kappa。"""
    if not (2 <= len(files) <= 9):
        raise _bad("请同时上传 2~9 份决策 CSV 文件。")
    named: dict[str, pd.DataFrame] = {}
    for i, upload in enumerate(files, start=1):
        stem, frame = await _read_name_frame(upload)
        key = stem or f"筛选员{i}"
        if key in named:
            raise _bad(f"存在重名的筛选员文件：{key}（请用不同文件名区分）。")
        named[key] = frame
    try:
        from coscreen.screening_arbitration import merge_decision_packages
        merged = merge_decision_packages(named)
        summary = summarize_multi(merged)
        named_labels = {n: merged[f"decision_{n}"].tolist() for n in named}
        pair_results = pairwise_kappa(named_labels)
        light = light_kappa(named_labels)
        reasons = exclusion_reason_distribution_multi(merged)
    except ValueError as exc:
        raise _bad(str(exc)) from exc

    pairs = [
        {
            "a": a,
            "b": b,
            "kappa": round(res.kappa, 4),
            "n": res.n,
            "interpretation": res.interpretation,
        }
        for (a, b), res in pair_results.items()
    ]
    return {
        "summary": {
            "n_total": summary.n_total,
            "n_agree": summary.n_agree,
            "n_conflict": summary.n_conflict,
            "n_incomplete": summary.n_incomplete,
            "agreement_rate": round(summary.agreement_rate, 4),
            "participation": summary.participation,
            "pairwise_agreement": {
                f"{a}↔{b}": round(v, 4) for (a, b), v in summary.pairwise_agreement.items()
            },
        },
        "light_kappa": None if light is None else round(light, 4),
        "pairwise_kappa": pairs,
        "exclusion_reasons": reasons,
        # ponytail: send the full preview; persist/page server-side if very large reviews outgrow JSON responses.
        "merged": _frame_rows(merged, limit=len(merged)),
        "n_rows_returned": len(merged),
    }


def _arbitration_store(task_id: str, user: dict) -> Path:
    info = _task_or_404(task_id)
    store = _collaboration_store(info)
    owner = collaboration_mod.get_owner(store, task_id=task_id)
    if owner is None:
        if _server_mode():
            raise HTTPException(409, "旧任务需先完成属主迁移。")
    elif owner["owner_user_id"] != user["user_id"] and not collaboration_mod.authorize_action(
            store, user["user_id"], "consensus:write", task_id=task_id):
        raise HTTPException(403, "仅任务属主或仲裁员可以保存仲裁结果。")
    return store


@app.post("/api/tasks/{task_id}/screening-arbitrations", status_code=201)
async def save_screening_arbitration(
    task_id: str,
    files: list[UploadFile] = File(...),
    stage: Literal["stage1", "stage2"] = Form(...),
    resolutions: str = Form(...),
    user: dict = Depends(require_user),
) -> dict:
    store = _arbitration_store(task_id, user)
    if not 2 <= len(files) <= 9:
        raise _bad("请上传 2 到 9 份决策 CSV。")
    try:
        choices = json.loads(resolutions)
    except json.JSONDecodeError as exc:
        raise _bad("仲裁数据不是有效 JSON。") from exc
    named: dict[str, pd.DataFrame] = {}
    for upload in files:
        name, frame = await _read_name_frame(upload)
        if name in named:
            raise _bad("筛选员文件名重复。")
        named[name] = frame
    from coscreen.screening_arbitration import (
        append_decision_arbitration, merge_decision_packages,
    )
    try:
        merged = merge_decision_packages(named)
        saved = append_decision_arbitration(
            store, stage=stage, merged=merged, resolutions=choices,
            arbitrator_id=user["user_id"],
        )
    except ValueError as exc:
        raise _bad(str(exc)) from exc
    return saved


@app.get("/api/tasks/{task_id}/screening-arbitrations/latest")
def latest_screening_arbitration(
    task_id: str, stage: Literal["stage1", "stage2"] = "stage1",
    user: dict = Depends(require_user),
) -> dict:
    store = _arbitration_store(task_id, user)
    from coscreen.screening_arbitration import latest_decision_arbitration
    record = latest_decision_arbitration(store, stage)
    if record is None:
        raise _bad("尚无仲裁记录。", 404)
    return record


@app.get("/api/tasks/{task_id}/screening-arbitrations/{record_id}")
def screening_arbitration(task_id: str, record_id: int,
                          user: dict = Depends(require_user)) -> dict:
    store = _arbitration_store(task_id, user)
    from coscreen.screening_arbitration import get_decision_arbitration
    record = get_decision_arbitration(store, record_id)
    if record is None:
        raise _bad("仲裁记录不存在。", 404)
    return record


@app.post("/api/coding/agreement")
async def coding_agreement_ep(
    files: list[UploadFile] = File(...), user: dict = Depends(require_user)
) -> dict:
    """上传 2~9 位编码员的编码矩阵 CSV -> 逐维度一致性（kappa / Light / 并排冲突）。"""
    if not (2 <= len(files) <= 9):
        raise _bad("请同时上传 2~9 份编码矩阵 CSV 文件。")
    named: dict[str, pd.DataFrame] = {}
    for i, upload in enumerate(files, start=1):
        stem, frame = await _read_name_frame(upload, matrix=True)
        key = stem or f"编码员{i}"
        if key in named:
            raise _bad(f"存在重名的编码员文件：{key}（请用不同文件名区分）。")
        if "zotero_key" not in frame.columns:
            raise _bad(f"{key} 的矩阵缺少 zotero_key 列。")
        named[key] = frame
    try:
        merged = merge_coding(named)
        dtype_map = {}
        for dim, dtype in zip(merged["dimension"], merged["dtype"]):
            dtype_map.setdefault(str(dim), str(dtype))
        summary = summarize_coding(merged, dtype_map)
        payload = agreement_payload(summary)
    except ValueError as exc:
        raise _bad(str(exc)) from exc
    conflict_mask = merged["status"] == "conflict"
    return {
        **payload,
        "conflicts": _frame_rows(merged.loc[conflict_mask]),
        "merged_csv_b64_note": "合并明细可用 conflicts_csv / merged_coding_csv 的导出流程获取。",
    }


# ---------------------------------------------------------------------------
# PRISMA
# ---------------------------------------------------------------------------

@app.get("/api/prisma/form")
def prisma_form(user: dict = Depends(require_user)) -> dict:
    """官方结构 PRISMA 表单：空表单契约 + 受控词表。"""
    return {
        "form": form_to_dict(PrismaFormData()),
        "review_modes": list(REVIEW_MODES),
        "sources_scopes": list(SOURCES_SCOPES),
        "db_source_types": list(DB_SOURCE_TYPES),
        "other_source_types": list(OTHER_SOURCE_TYPES),
        "dta_reason_vocabulary": DTA_REASON_VOCABULARY,
        "attribution": ATTRIBUTION,
    }


@app.post("/api/prisma/generate")
def prisma_generate(body: PrismaGenerateIn, user: dict = Depends(require_user)) -> dict:
    """表单 -> 校验结果 + 官方结构 DOT + 数字 JSON（引擎口径 = coscreen.stats.prisma_form）。"""
    f = body.form
    allowed_db = set(DB_SOURCE_TYPES)
    allowed_other = set(OTHER_SOURCE_TYPES)
    for row in f.identified_rows_db:
        if row.source_type not in allowed_db:
            raise _bad(f"数据库/注册库来源类型 {row.source_type!r} 不合法。", 422)
    for row in f.identified_rows_other:
        if row.source_type not in allowed_other:
            raise _bad(f"其他来源类型 {row.source_type!r} 不合法。", 422)
    form = PrismaFormData(
        review_mode=f.review_mode,
        sources_scope=f.sources_scope,
        identified_rows_db=[IdentifiedRow(r.source_type, r.name, r.n) for r in f.identified_rows_db],
        identified_rows_other=[IdentifiedRow(r.source_type, r.name, r.n) for r in f.identified_rows_other],
        duplicates_removed=f.duplicates_removed,
        automation_ineligible=f.automation_ineligible,
        removed_other=f.removed_other,
        removed_other_note=f.removed_other_note.strip(),
        records_screened=f.records_screened,
        records_excluded_screening=f.records_excluded_screening,
        screening_exclusion_reasons=[ReasonRow(r.reason, r.n) for r in f.screening_exclusion_reasons],
        human_excluded_n=f.human_excluded_n,
        automation_excluded_n=f.automation_excluded_n,
        reports_sought_db=f.reports_sought_db,
        reports_not_retrieved_db=f.reports_not_retrieved_db,
        reports_assessed_db=f.reports_assessed_db,
        exclusion_reasons_db=[ReasonRow(r.reason, r.n) for r in f.exclusion_reasons_db],
        studies_included=f.studies_included,
        reports_of_included_studies=f.reports_of_included_studies,
        reports_sought_other=f.reports_sought_other,
        reports_not_retrieved_other=f.reports_not_retrieved_other,
        reports_assessed_other=f.reports_assessed_other,
        exclusion_reasons_other=[ReasonRow(r.reason, r.n) for r in f.exclusion_reasons_other],
        previous_studies=f.previous_studies,
        previous_reports=f.previous_reports,
        new_studies=f.new_studies,
        new_reports=f.new_reports,
        total_studies=f.total_studies,
        total_reports=f.total_reports,
        studies_included_meta_analysis=f.studies_included_meta_analysis,
    )
    result = validate(form)
    return {
        "ok": result.ok,
        "errors": [i.text for i in result.errors],
        "warnings": [i.text for i in result.warnings],
        "hints": [i.text for i in result.hints],
        "dot": prisma_form_dot(form),
        "json": form_json(form),
        "form": form_to_dict(form),
    }


# ---------------------------------------------------------------------------
# 编码方案与编码值
# ---------------------------------------------------------------------------

@app.get("/api/tasks/{task_id}/coding/dimensions")
def coding_dimensions(task_id: str, screener: str | None = None,
                      user: dict = Depends(require_user)) -> dict:
    info = _task_or_404(task_id)
    db = _screener_db(info, screener or user["username"])
    return {"dimensions": coding_mod.list_dimensions(db)}


@app.post("/api/tasks/{task_id}/coding/dimensions", status_code=201)
def add_dimension(task_id: str, body: DimensionIn, screener: str | None = None,
                  user: dict = Depends(require_user)) -> dict:
    info = _task_or_404(task_id)
    db = _screener_db(info, screener or user["username"])
    try:
        dim_id = coding_mod.add_dimension(
            db, body.name, body.dtype, body.options,
            multi_select=body.multi_select, unit=body.unit,
            description=body.description, section=body.section,
            trace_context=_trace_context(info, user),
        )
    except ValueError as exc:
        raise _bad(str(exc)) from exc
    return {"id": dim_id, "dimensions": coding_mod.list_dimensions(db)}


@app.put("/api/tasks/{task_id}/coding/dimensions/{dim_id}")
def update_dimension_ep(task_id: str, dim_id: int, body: DimensionUpdateIn,
                        screener: str | None = None, user: dict = Depends(require_user)) -> dict:
    info = _task_or_404(task_id)
    db = _screener_db(info, screener or user["username"])
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    if not fields:
        raise _bad("没有需要更新的字段。")
    try:
        coding_mod.update_dimension(db, dim_id, trace_context=_trace_context(info, user), **fields)
    except ValueError as exc:
        raise _dim_error(exc) from exc
    return {"updated": True, "dimensions": coding_mod.list_dimensions(db)}


@app.delete("/api/tasks/{task_id}/coding/dimensions/{dim_id}")
def delete_dimension_ep(task_id: str, dim_id: int, screener: str | None = None,
                        user: dict = Depends(require_user)) -> dict:
    info = _task_or_404(task_id)
    db = _screener_db(info, screener or user["username"])
    try:
        coding_mod.delete_dimension(db, dim_id, trace_context=_trace_context(info, user))
    except ValueError as exc:
        raise _dim_error(exc) from exc
    return {"deleted": True, "dimensions": coding_mod.list_dimensions(db)}


@app.post("/api/tasks/{task_id}/coding/values")
def save_coding_value(task_id: str, body: CodingValueIn, screener: str | None = None,
                      user: dict = Depends(require_user)) -> dict:
    info = _task_or_404(task_id)
    db = _screener_db(info, screener or user["username"])
    try:
        coding_mod.save_coding_value(db, body.dim_id, body.zotero_key, body.value, trace_context=_trace_context(info, user))
    except ValueError as exc:
        raise _bad(str(exc)) from exc
    except sqlite3.IntegrityError as exc:
        raise _bad("维度或文献不存在（请确认后重试）。", 400) from exc
    tasks_mod.touch_task(info.task_id, DATA_DIR)
    return {"saved": True, "progress": coding_mod.coding_progress(db)}


@app.get("/api/tasks/{task_id}/coding/values/{zotero_key}")
def coding_values_for(task_id: str, zotero_key: str, screener: str | None = None,
                      user: dict = Depends(require_user)) -> dict:
    if not _KEY_RE.match(zotero_key or ""):
        raise _bad("非法文献键。", 400)
    info = _task_or_404(task_id)
    db = _screener_db(info, screener or user["username"])
    values = {str(k): v for k, v in coding_mod.get_coding_values(db, zotero_key).items()}
    note_map: dict[str, str] = {}
    for dim in coding_mod.list_dimensions(db):
        note = coding_mod.get_coding_note(db, int(dim["id"]), zotero_key)
        if note and (note.get("text") or "").strip():
            note_map[str(int(dim["id"]))] = note.get("text") or ""
    return {"values": values, "notes": note_map}


@app.get("/api/tasks/{task_id}/coding/queue")
def coding_queue_ep(task_id: str, screener: str | None = None,
                    user: dict = Depends(require_user)) -> dict:
    info = _task_or_404(task_id)
    db = _screener_db(info, screener or user["username"])
    try:
        keys = coding_mod.coding_queue(db)
    except ValueError as exc:
        raise _bad(str(exc)) from exc
    by_key = {a.zotero_key: a for a in db_mod.list_articles(db, include_duplicates=True)}
    items = [
        {"zotero_key": k, "title": by_key[k].title, "year": by_key[k].year}
        for k in keys if k in by_key
    ]
    return {"queue": items, "total": len(items), "progress": coding_mod.coding_progress(db)}


@app.get("/api/tasks/{task_id}/coding/scheme")
def export_scheme_ep(task_id: str, screener: str | None = None,
                     user: dict = Depends(require_user)) -> dict:
    info = _task_or_404(task_id)
    db = _screener_db(info, screener or user["username"])
    return coding_mod.export_scheme(db)


@app.post("/api/tasks/{task_id}/coding/scheme")
def import_scheme_ep(task_id: str, body: SchemeImportIn, screener: str | None = None,
                     user: dict = Depends(require_user)) -> dict:
    info = _task_or_404(task_id)
    db = _screener_db(info, screener or user["username"])
    try:
        n = coding_mod.import_scheme(db, body.scheme, replace=body.replace, trace_context=_trace_context(info, user, "import"))
    except ValueError as exc:
        raise _bad(str(exc)) from exc
    return {"imported": n, "dimensions": coding_mod.list_dimensions(db)}


# ---------------------------------------------------------------------------
# 研究级综述分析（每个筛选员的任务数据库独立）
# ---------------------------------------------------------------------------

@app.get("/api/analysis/measures")
def analysis_measure_registry(user: dict = Depends(require_user)) -> dict:
    from coscreen.measure_registry import registry_snapshot, CAUTIONS
    return {"measures": registry_snapshot(), "cautions": CAUTIONS}


@app.get("/api/analysis/effect-estimators")
def analysis_effect_estimators(user: dict = Depends(require_user)) -> dict:
    from coscreen.smd_contract import CONTRACT_VERSION, estimator_registry_snapshot
    return {"contract_version": CONTRACT_VERSION,
            "estimators": estimator_registry_snapshot()}


@app.get("/api/tasks/{task_id}/analysis/studies")
def analysis_studies(task_id: str, user: dict = Depends(require_user)) -> dict:
    db = _screener_db(_task_or_404(task_id), user["username"])
    return {"studies": analysis_mod.list_studies(db)}


@app.post("/api/tasks/{task_id}/analysis/studies")
def analysis_save_study(task_id: str, body: StudyIn, user: dict = Depends(require_user)) -> dict:
    db = _screener_db(_task_or_404(task_id), user["username"])
    try:
        analysis_mod.save_study(db, body.id, body.label, body.reports)
    except ValueError as exc:
        raise _bad(exc) from exc
    return {"studies": analysis_mod.list_studies(db)}


@app.get("/api/tasks/{task_id}/analysis/summary")
def analysis_summary(task_id: str, dimension_id: int, kind: str = "categorical",
                     user: dict = Depends(require_user)) -> dict:
    db = _screener_db(_task_or_404(task_id), user["username"])
    try:
        if kind == "year":
            return analysis_mod.year_distribution(db, dimension_id)
        return analysis_mod.coding_summary(db, dimension_id, kind)
    except ValueError as exc:
        raise _bad(exc) from exc


@app.get("/api/tasks/{task_id}/analysis/cross-tab")
def analysis_cross_tab(task_id: str, dimension_a: int, dimension_b: int,
                       user: dict = Depends(require_user)) -> dict:
    db = _screener_db(_task_or_404(task_id), user["username"])
    try:
        return analysis_mod.coding_cross_tab(db, dimension_a, dimension_b)
    except ValueError as exc:
        raise _bad(exc) from exc


@app.get("/api/tasks/{task_id}/analysis/effects")
def analysis_effects(task_id: str, user: dict = Depends(require_user)) -> dict:
    db = _screener_db(_task_or_404(task_id), user["username"])
    return {"effects": analysis_mod.list_effects(db)}


@app.get("/api/tasks/{task_id}/analysis/effects/versions")
def analysis_effect_versions(task_id: str, result_ids: list[int] = Query(),
                             user: dict = Depends(require_user)) -> dict:
    from contextlib import closing
    from coscreen.analysis_storage import effect_history
    db = _screener_db(_task_or_404(task_id), user["username"])
    with closing(db_mod._connect(db)) as conn:
        try:
            return {"versions": effect_history(conn, result_ids)}
        except ValueError as exc:
            raise HTTPException(400, {"error_code": "effect_version_invalid_selection",
                                      "message_key": "analysis.effect_version_invalid_selection",
                                      "params": {}, "details": str(exc), "message": str(exc)}) from exc


@app.post("/api/tasks/{task_id}/analysis/effects/{result_id}/select")
def analysis_select_effect(task_id: str, result_id: int,
                           user: dict = Depends(require_user)) -> dict:
    db = _screener_db(_task_or_404(task_id), user["username"])
    try:
        analysis_mod.select_effect(db, result_id)
    except ValueError as exc:
        raise _bad(exc) from exc
    return {"effects": analysis_mod.list_effects(db)}


@app.post("/api/tasks/{task_id}/analysis/effects/{result_id}/direction")
def analysis_confirm_effect_direction(task_id: str, result_id: int,
                                      body: EffectDirectionConfirmationIn,
                                      user: dict = Depends(require_user)) -> dict:
    db = _screener_db(_task_or_404(task_id), user["username"])
    try:
        analysis_mod.confirm_effect_direction(
            db, result_id, body.effect_direction, body.source_locator,
            confirmed_by=user["username"],
            confirmed_at=datetime.now().isoformat(timespec="seconds"),
        )
    except ValueError as exc:
        raise _bad(exc, 404 if str(exc) == "effect result does not exist" else 400) from exc
    return {"effects": analysis_mod.list_effects(db)}


@app.post("/api/tasks/{task_id}/analysis/calculate")
def analysis_calculate(task_id: str, body: EffectCalculateIn,
                       user: dict = Depends(require_user)) -> dict:
    _task_or_404(task_id)
    try:
        if body.measure in {"RR", "OR", "RD"}:
            if any(v is None for v in (body.events_t, body.total_t, body.events_c, body.total_c)):
                raise ValueError("both binary arms need events and totals")
            return analysis_mod.binary_effect(body.events_t, body.total_t, body.events_c, body.total_c, body.measure)
        if any(v is None for v in (body.n_t, body.mean_t, body.sd_t, body.n_c, body.mean_c, body.sd_c)):
            raise ValueError("both continuous arms need n, mean and SD")
        return analysis_mod.continuous_effect(body.n_t, body.mean_t, body.sd_t, body.n_c, body.mean_c, body.sd_c, body.measure)
    except ValueError as exc:
        raise _bad(exc) from exc


@app.post("/api/tasks/{task_id}/analysis/effects")
def analysis_save_effect(task_id: str, body: EffectIn, user: dict = Depends(require_user)) -> dict:
    db = _screener_db(_task_or_404(task_id), user["username"])
    try:
        values = body.model_dump(exclude={"standardizer", "effect_direction", "se_scale"})
        directional = body.measure not in NON_DIRECTIONAL_MEASURES
        if directional and body.effect_direction is None:
            raise ValueError("Choose how the effect direction maps to Comparison")
        if not directional and body.effect_direction is not None:
            raise ValueError("Effect direction is not applicable to this measure")
        if body.effect_direction:
            values["input_data"] = {"effect_direction": body.effect_direction}
        if body.measure in analysis_mod.RATIOS:
            if body.se_scale == "natural":
                raise ValueError("ratio SE must be supplied on the log analysis scale")
            values["input_data"] = {
                **values.get("input_data", {}),
                "se_scale": "log",
                "se_scale_declaration": "explicit" if body.se_scale == "log" else "legacy_implicit",
            }
        elif body.se_scale is not None:
            raise ValueError("se_scale is only supported for ratio measures")
        if body.measure == "SMD":
            if body.standardizer is None:
                raise ValueError("Choose the SMD standardizer: Hedges g (pooled SD) or Glass delta (control SD)")
            values["input_data"] = {**values.get("input_data", {}), "standardizer": body.standardizer,
                                    "bias_correction": "none" if body.standardizer == "control_arm_sd" else "hedges"}
        elif body.standardizer is not None:
            raise ValueError("standardizer is only supported for SMD")
        analysis_mod.save_effect(db, **values)
    except ValueError as exc:
        raise _bad(exc) from exc
    return {"effects": analysis_mod.list_effects(db)}


@app.post("/api/tasks/{task_id}/analysis/effects/hedges-g")
def analysis_save_hedges_g(task_id: str, body: HedgesGIn,
                           user: dict = Depends(require_user)) -> dict:
    from coscreen.smd_contract import calculate_hedges_g
    db = _screener_db(_task_or_404(task_id), user["username"])
    try:
        effect = calculate_hedges_g(body.format_name, body.values,
                                    effect_direction=body.effect_direction,
                                    source_locator=body.source_locator)
        result_id = analysis_mod.save_effect(
            db, body.study_id, body.comparison, body.outcome, body.timepoint,
            effect["measure"], effect["estimate"], effect["se"], body.source_key,
            effect["input_data"], effect["entry_method"], append=body.append,
            source_locator=body.source_locator,
        )
    except ValueError as exc:
        raise HTTPException(400, {"error_code": "effect_calculation_invalid",
                                  "message_key": "analysis.effect_calculation_invalid", "params": {},
                                  "details": str(exc), "message": str(exc)}) from exc
    return {"result_id": result_id, "effect": effect}


@app.post("/api/tasks/{task_id}/analysis/format-effects")
def analysis_save_format_effect(task_id: str, body: FormatEffectIn,
                                user: dict = Depends(require_user)) -> dict:
    if body.format_name == "binary_correlation_conversion":
        from coscreen.binary_correlation_conversion import calculate_binary_correlation_conversion as calculate_effect
    elif body.format_name == "zero_cell_binary":
        from coscreen.zero_cell_effects import calculate_zero_cell_effect as calculate_effect
    elif body.format_name == "survival_logrank_oe_v":
        from coscreen.survival_logrank_effect import calculate_survival_logrank_effect as calculate_effect
    elif body.format_name == "cluster_trial_design_effect":
        from coscreen.cluster_trial_effects import calculate_cluster_trial_effect as calculate_effect
    elif body.format_name == "cluster_trial_variable_size_design_effect":
        from coscreen.cluster_trial_variable_size import calculate_cluster_trial_variable_size_effect as calculate_effect
    elif body.format_name == "crossover_2x2_md":
        from coscreen.crossover_effects import calculate_crossover_effect as calculate_effect
    elif body.format_name in {"single_mean_direct", "single_proportion_direct", "single_rate_direct"}:
        from coscreen.single_group_direct import calculate_single_group_direct_effect as calculate_effect
    elif body.format_name == "glass_delta_two_arm":
        from coscreen.glass_delta_effect import calculate_glass_delta_effect as calculate_effect
    elif body.format_name == "ratio_of_means_two_arm":
        from coscreen.ratio_of_means_effect import calculate_ratio_of_means as calculate_effect
    elif body.format_name == "single_group_standardized_change":
        from coscreen.single_group_standardized_change import calculate_single_group_standardized_change as calculate_effect
    elif body.format_name.startswith("single_"):
        from coscreen.single_group_formats import calculate_single_group_effect as calculate_effect
    elif body.format_name in {"independent_t_n", "independent_p_n", "independent_f_n", "independent_d_n"}:
        from coscreen.test_statistic_effect_formats import calculate_test_statistic_effect as calculate_effect
    elif body.format_name.startswith(("paired_md_", "parallel_change_")):
        from coscreen.dependent_effect_formats import calculate_dependent_effect as calculate_effect
    else:
        from coscreen.effect_size_formats import calculate_effect

    db = _screener_db(_task_or_404(task_id), user["username"])
    try:
        if body.standardizer is not None and body.format_name != "single_group_standardized_change":
            raise ValueError("standardizer is only supported for single-group standardized change")
        if body.format_name == "single_group_standardized_change":
            if body.standardizer is None:
                raise ValueError("choose the standardized-change denominator explicitly")
            result = calculate_effect(body.values, standardizer=body.standardizer)
            input_data = {**result["input_data"], "standardizer": body.standardizer,
                          "variance_method": result["variance_method"]}
            result.update(standardizer=body.standardizer, input_data=input_data)
        elif body.format_name == "zero_cell_binary":
            if set(body.values) != {"events_t", "total_t", "events_c", "total_c", "measure", "correction"}:
                raise ValueError("zero-cell values need events, totals, measure and explicit correction")
            result = calculate_effect(**body.values, source_key=body.source_key)
            if result["cell_status"] in {"double_zero", "all_event"}:
                raise ValueError("double-zero and all-event studies are uninformative for relative effects; save their original counts in the binary arm-data area")
            if not result["inverse_variance_ready"]:
                raise ValueError("selected correction does not yield a finite effect and SE; save original counts in the binary arm-data area")
            input_data = result
        else:
            result = (calculate_effect(body.values) if body.format_name in {"cluster_trial_design_effect", "cluster_trial_variable_size_design_effect", "survival_logrank_oe_v", "ratio_of_means_two_arm", "glass_delta_two_arm", "binary_correlation_conversion"}
                      else calculate_effect(body.format_name, body.values))
            input_data = result.get("input_data", body.values)
            if body.format_name in {"cluster_trial_design_effect", "cluster_trial_variable_size_design_effect"}:
                method_sources = ["Cochrane Handbook, chapter 23, sections 23.1.4–23.1.5 (cluster-trial design effects)."]
                if body.format_name == "cluster_trial_variable_size_design_effect":
                    method_sources.append("Eldridge et al. (2006), doi:10.1093/ije/dyl129; Rutterford et al. (2015), equation 17 (variable cluster sizes).")
                    if result.get("ci_method"):
                        method_sources.append("Donner & Klar (2000), Design and Analysis of Cluster Randomisation Trials in Health Research; Leyrat et al. (2018), doi:10.1093/ije/dyx169 (few-cluster interval).")
                input_data = {**input_data, "cluster_adjustment": result["cluster_adjustment"],
                              "assumptions": result["assumptions"], "method_sources": method_sources}
                result["input_data"] = input_data
            if body.format_name == "glass_delta_two_arm":
                input_data = {**input_data, "standardizer": result["standardizer"], "bias_correction": "none", "variance_method": "SMD1_LS"}
        directional = result["measure"] not in NON_DIRECTIONAL_MEASURES
        if directional:
            if body.effect_direction not in {"first_vs_second", "second_vs_first"}:
                raise ValueError("Choose how the effect direction maps to Comparison")
            input_data = {**input_data, "effect_direction": body.effect_direction}
            result["input_data"] = input_data
            result["effect_direction"] = body.effect_direction
        elif body.effect_direction not in {None, "not_applicable"}:
            raise ValueError("Effect direction is not applicable to this measure")
        result_id = analysis_mod.save_effect(db, body.study_id, body.comparison, body.outcome, body.timepoint,
                                             result["measure"], result["estimate"], result["se"],
                                             body.source_key, input_data,
                                             result.get("entry_method", body.format_name),
                                             append=body.append, source_locator=body.source_locator)
    except ValueError as exc:
        raise _bad(exc) from exc
    return {"effect": result, "result_id": result_id, "effects": analysis_mod.list_effects(db)}


@app.post("/api/tasks/{task_id}/analysis/zero-cell/preview")
def analysis_zero_cell_preview(task_id: str, body: ZeroCellPreviewIn,
                               user: dict = Depends(require_user)) -> dict:
    from coscreen.zero_cell_effects import calculate_zero_cell_effect
    _screener_db(_task_or_404(task_id), user["username"])
    try:
        return calculate_zero_cell_effect(**body.model_dump())
    except ValueError as exc:
        raise _bad(exc) from exc


@app.post("/api/tasks/{task_id}/analysis/arm-effects")
def analysis_save_arm_effect(task_id: str, body: ArmEffectIn,
                             user: dict = Depends(require_user)) -> dict:
    db = _screener_db(_task_or_404(task_id), user["username"])
    try:
        analysis_mod.save_arm_effect(db, **body.model_dump())
    except ValueError as exc:
        raise _bad(exc) from exc
    return {"effects": analysis_mod.list_effects(db)}


@app.post("/api/tasks/{task_id}/analysis/multi-arm-effects")
def analysis_save_multi_arm_effect(task_id: str, body: MultiArmEffectIn,
                                   user: dict = Depends(require_user)) -> dict:
    from coscreen import multi_arm_effects as multi_arm_mod

    db = _screener_db(_task_or_404(task_id), user["username"])
    try:
        effect = multi_arm_mod.calculate_multi_arm_effect(
            body.measure, body.intervention_arms, body.comparator_arms, body.combination_rationale,
        )
        effect["input_data"]["effect_direction"] = body.effect_direction
        result_id = analysis_mod.save_effect(
            db, body.study_id, body.comparison, body.outcome, body.timepoint, body.measure,
            effect["estimate"], effect["se"], body.source_key, effect["input_data"],
            "multi-arm combination", append=body.append, source_locator=body.source_locator,
        )
    except ValueError as exc:
        raise _bad(exc) from exc
    return {"effect": effect, "result_id": result_id, "effects": analysis_mod.list_effects(db)}


@app.get("/api/tasks/{task_id}/analysis/synthesis")
def analysis_synthesis(task_id: str, comparison: str, outcome: str, timepoint: str,
                       measure: str, model: str, ci_method: str,
                       user: dict = Depends(require_user)) -> dict:
    db = _screener_db(_task_or_404(task_id), user["username"])
    try:
        result = analysis_mod.synthesize(db, comparison, outcome, timepoint, measure, model, ci_method)
        from custom_backend.heterogeneity_view import heterogeneity_intervals
        result["heterogeneity_intervals"] = heterogeneity_intervals(result)
        return result
    except ValueError as exc:
        raise _bad(exc) from exc


@app.post("/api/tasks/{task_id}/analysis/runs")
def analysis_create_run(task_id: str, body: SynthesisRunIn,
                        user: dict = Depends(require_user)) -> dict:
    """Explicitly persist a synthesis; legacy GET remains recalculation-only."""
    from coscreen.analysis_storage import create_synthesis_run
    info = _task_or_404(task_id)
    db = _screener_db(info, user["username"])
    try:
        specification = body.model_dump(exclude={"coding_freeze_id"})
        freeze = _run_coding_freeze(info, user, body.coding_freeze_id)
        return create_synthesis_run(db, specification, task_id, freeze)
    except ValueError as exc:
        raise HTTPException(400, {"error_code": "analysis_invalid_spec",
                                  "message_key": "analysis.invalid_spec", "params": {},
                                  "details": str(exc), "message": str(exc)}) from exc


@app.post("/api/tasks/{task_id}/analysis/dependent-gls/runs")
def analysis_create_dependent_gls_run(task_id: str, body: dict,
                                      user: dict = Depends(require_user)) -> dict:
    from coscreen.dependent_effects import KernelError
    from coscreen.dependent_runs import create_dependent_gls_run
    info = _task_or_404(task_id)
    db = _screener_db(info, user["username"])
    try:
        request = dict(body)
        freeze_id = request.pop("coding_freeze_id", None)
        freeze = _run_coding_freeze(info, user, freeze_id)
        return create_dependent_gls_run(db, task_id, request, freeze)
    except KernelError as exc:
        raise HTTPException(400, {"error_code": exc.code,
                                  "message_key": "analysis.dependent_gls_invalid", "params": exc.details,
                                  "details": str(exc), "message": str(exc)}) from exc
    except ValueError as exc:
        raise HTTPException(400, {"error_code": "dependent_gls_invalid_spec",
                                  "message_key": "analysis.dependent_gls_invalid", "params": {},
                                  "details": str(exc), "message": str(exc)}) from exc


@app.post("/api/tasks/{task_id}/analysis/multilevel-reml/runs")
def analysis_create_multilevel_run(task_id: str, body: dict,
                                   user: dict = Depends(require_user)) -> dict:
    from coscreen.dependent_effects import KernelError
    from coscreen.multilevel_runs import create_multilevel_run
    info = _task_or_404(task_id)
    db = _screener_db(info, user["username"])
    request = dict(body)
    freeze_id = request.pop("coding_freeze_id", None)
    try:
        freeze = _run_coding_freeze(info, user, freeze_id)
        return create_multilevel_run(db, task_id, request, freeze)
    except KernelError as exc:
        raise HTTPException(400, {"error_code": exc.code,
                                  "message_key": "analysis.multilevel_invalid", "params": exc.details,
                                  "details": str(exc), "message": str(exc)}) from exc
    except ValueError as exc:
        raise HTTPException(400, {"error_code": "multilevel_invalid_spec",
                                  "message_key": "analysis.multilevel_invalid", "params": {},
                                  "details": str(exc), "message": str(exc)}) from exc


@app.get("/api/tasks/{task_id}/analysis/runs/{run_id}")
def analysis_get_run(task_id: str, run_id: str,
                     user: dict = Depends(require_user)) -> dict:
    from contextlib import closing
    from coscreen.analysis_storage import load_analysis_run
    db = _screener_db(_task_or_404(task_id), user["username"])
    with closing(db_mod._connect(db)) as conn:
        result = load_analysis_run(conn, run_id)
    if result is None:
        raise HTTPException(404, {"error_code": "analysis_run_not_found",
                                  "message_key": "analysis.run_not_found", "params": {},
                                  "details": None, "message": "analysis run not found"})
    return result


@app.get("/api/tasks/{task_id}/analysis/runs/{run_id}/export")
def analysis_export_run(task_id: str, run_id: str,
                        format: Literal["csv", "tex", "docx", "pptx"],
                        user: dict = Depends(require_user)) -> Response:
    from custom_backend.analysis_audit_report import audit_response
    run = analysis_get_run(task_id, run_id, user)
    title = ({"fixed_gls_with_supplied_total_covariance": "Saved dependent GLS run",
              "three_level_random_intercept_meta_regression": "Saved multilevel REML run"}
             .get(run["specification"].get("model_scope"), "Saved synthesis run"))
    if run["specification"].get("analysis_type") == "rob_v2_exclusion_sensitivity":
        title = "Saved RoB exclusion sensitivity run"
    return audit_response(run, format, title, filename=f"reviewflow_run_{run_id}")


@app.put("/api/tasks/{task_id}/analysis/effects/{result_id}/moderators")
def analysis_save_result_moderator(task_id: str, result_id: int, body: ResultModeratorIn,
                                   user: dict = Depends(require_user)) -> dict:
    from contextlib import closing
    from coscreen.analysis_storage import save_result_moderator
    db = _screener_db(_task_or_404(task_id), user["username"])
    with closing(db_mod._connect(db)) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            return save_result_moderator(conn, result_id, **body.model_dump())
        except ValueError as exc:
            status = 409 if "revision conflict" in str(exc) else 400
            raise HTTPException(status, {"error_code": "result_moderator_conflict" if status == 409
                                         else "result_moderator_invalid",
                                         "message_key": "analysis.result_moderator_invalid", "params": {},
                                         "details": str(exc), "message": str(exc)}) from exc


@app.get("/api/tasks/{task_id}/analysis/effects/moderators")
def analysis_get_result_moderators(task_id: str, result_ids: list[int] = Query(),
                                   user: dict = Depends(require_user)) -> dict:
    from contextlib import closing
    from coscreen.analysis_storage import result_moderators
    db = _screener_db(_task_or_404(task_id), user["username"])
    with closing(db_mod._connect(db)) as conn:
        try:
            return {"moderators": result_moderators(conn, result_ids)}
        except ValueError as exc:
            raise HTTPException(400, {"error_code": "result_moderator_invalid",
                                      "message_key": "analysis.result_moderator_invalid", "params": {},
                                      "details": str(exc), "message": str(exc)}) from exc


@app.put("/api/tasks/{task_id}/analysis/effects/{result_id}/design")
def analysis_set_effect_design(task_id: str, result_id: int, body: EffectDesignIn,
                               user: dict = Depends(require_user)) -> dict:
    from contextlib import closing
    from coscreen.analysis_storage import set_effect_design
    db = _screener_db(_task_or_404(task_id), user["username"])
    with closing(db_mod._connect(db)) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            return set_effect_design(conn, result_id, **body.model_dump())
        except ValueError as exc:
            status = 409 if "revision conflict" in str(exc) else 400
            raise HTTPException(status, {"error_code": "effect_design_conflict" if status == 409
                                          else "effect_design_invalid",
                                          "message_key": "analysis.effect_design_invalid",
                                          "params": {}, "details": str(exc),
                                          "message": str(exc)}) from exc


@app.get("/api/tasks/{task_id}/analysis/effects/designs")
def analysis_get_effect_designs(task_id: str, result_ids: list[int] = Query(),
                                user: dict = Depends(require_user)) -> dict:
    from contextlib import closing
    from coscreen.analysis_storage import effect_designs
    db = _screener_db(_task_or_404(task_id), user["username"])
    with closing(db_mod._connect(db)) as conn:
        try:
            return {"designs": effect_designs(conn, result_ids)}
        except ValueError as exc:
            raise HTTPException(400, {"error_code": "effect_design_incomplete",
                                      "message_key": "analysis.effect_design_incomplete", "params": {},
                                      "details": str(exc), "message": str(exc)}) from exc


@app.get("/api/tasks/{task_id}/analysis/report")
def analysis_synthesis_report(task_id: str, comparison: str, outcome: str, timepoint: str,
                              measure: str, model: str, ci_method: str,
                              format: str = Query(pattern="^(docx|pptx|tex)$"),
                              user: dict = Depends(require_user)) -> Response:
    db = _screener_db(_task_or_404(task_id), user["username"])
    try:
        result = analysis_mod.synthesize(db, comparison, outcome, timepoint, measure, model, ci_method)
        from custom_backend.heterogeneity_view import heterogeneity_intervals
        result["heterogeneity_intervals"] = heterogeneity_intervals(result)
    except ValueError as exc:
        raise _bad(exc) from exc

    labels = {study["id"]: study["label"] for study in analysis_mod.list_studies(db)}
    articles = {article.zotero_key: article for article in db_mod.list_articles(db)}
    for effect in result["effects"]:
        article = articles.get(effect["source_key"])
        effect.update({
            "study_label": labels.get(effect["study_id"], effect["study_id"]),
            "source_title": article.title if article else "",
            "source_authors": article.authors if article else "",
            "source_year": article.year if article else None,
            "source_journal": article.journal if article else "",
            "source_doi": article.doi if article else "",
        })

    from custom_backend.synthesis_report import build_docx, build_pptx, _heterogeneity_interval_note

    report = {"comparison": comparison, "outcome": outcome, "timepoint": timepoint,
              "measure": measure, "model": model, "ci_method": ci_method, "result": result,
              "heterogeneity_interval_note": _heterogeneity_interval_note(result)}
    if format == "tex":
        from custom_backend.analysis_audit_report import audit_response
        from custom_backend.synthesis_report import _citations, _number
        summary = ({"title": "Synthesis results", "columns": ["Measure", "Value"],
                    "rows": [["Pooled estimate", _number(result["pooled"])],
                             ["95% CI", f'{_number(result["ci_low"])} to {_number(result["ci_high"])}'],
                             ["Studies", result["n_studies"]],
                             ["Model / method", f'{result["model"]} / {result["method"]}']],
                    "note": "Study estimates and saved source inputs appear in the full audit below."},)
        return audit_response({**report, "citation_reminders": _citations(report)}, format,
                              "Synthesis report", summary, filename="reviewflow_synthesis")
    builder = build_docx if format == "docx" else build_pptx
    media_type = ("application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                  if format == "docx" else
                  "application/vnd.openxmlformats-officedocument.presentationml.presentation")
    return Response(
        content=builder(report),
        media_type=media_type,
        headers={"Content-Disposition": f"attachment; filename=reviewflow_synthesis.{format}"},
    )


@app.get("/api/tasks/{task_id}/analysis/binary-pooling")
def analysis_binary_pooling(task_id: str, comparison: str, outcome: str, timepoint: str,
                            measure: str, method: str, user: dict = Depends(require_user),
                            format: str | None = Query(default=None, pattern="^(docx|pptx|csv|tex)$")) -> dict:
    if method not in {"mh", "peto"} or measure not in {"RR", "OR", "RD"} or \
       (method == "peto" and measure != "OR"):
        raise _bad("MH supports RR, OR or RD; Peto supports OR only")
    db = _screener_db(_task_or_404(task_id), user["username"])
    from coscreen.binary_arm_data import list_binary_arms
    chosen = (comparison, outcome, timepoint)
    raw_rows = [row for row in list_binary_arms(db)
                if (row["comparison"], row["outcome"], row["timepoint"]) == chosen]
    rows_by_study = {row["study_id"]: {**row, "input_data": row["arms"], "entry_method": "binary arms"}
                     for row in raw_rows}
    try:
        selected_rows = analysis_mod.selected_effects(db, *chosen)
    except ValueError as exc:
        raise _bad(exc) from exc
    for row in selected_rows:
        rows_by_study.setdefault(row["study_id"], row)
    rows = list(rows_by_study.values())
    if len(rows) < 2:
        raise _bad("at least two independent studies are required")
    if any(row["entry_method"] != "binary arms" or
           not isinstance(row["input_data"], dict) or
           set(row["input_data"]) != {"events_t", "total_t", "events_c", "total_c"}
           for row in rows):
        raise _bad("all selected studies need saved binary arm counts; manual estimates cannot be used")
    studies = [{"study_id": row["study_id"], **row["input_data"]} for row in rows]
    try:
        if method == "mh":
            from coscreen.mantel_haenszel import pool_mantel_haenszel
            result = pool_mantel_haenszel(studies, measure)
        else:
            from coscreen.peto import pool_peto
            result = pool_peto(studies)
    except ValueError as exc:
        raise _bad(exc) from exc
    result["sources"] = {row["study_id"]: row["source_key"] for row in rows}
    result['method_sources'] = [
        'Borenstein et al. (2021), Introduction to Meta-Analysis, 2nd ed., ch. 42, pp. 369–376; MH odds ratio and Peto one-step odds ratio.',
        'Harrer et al. (2022), Doing Meta-Analysis with R, ch. 4, pp. 115–119; verify the original book text. MH risk ratio and risk difference use their own estimator definitions.']
    if format:
        from custom_backend.analysis_audit_report import audit_response
        table = dict(title='Binary arm-count synthesis',
                     columns=['Method', 'Measure', 'Studies', 'Estimate', '95% CI low', '95% CI high'],
                     rows=[[result['method'], measure, result['n_studies'], result['pooled'],
                            result['ci_low'], result['ci_high']]],
                     note='Full selected arm counts, zero-information exclusions, source records and method references follow in the audit appendix. Verify the original methods and cite the reports.')
        return audit_response({**result, 'request':dict(comparison=comparison, outcome=outcome,
                              timepoint=timepoint, measure=measure, method=method), 'input_data':rows,
                              'source_articles':source_articles(db, rows)},
                              format, 'Binary pooling audit', tables=[table])
    return result


@app.post("/api/tasks/{task_id}/analysis/binary-arms")
def analysis_save_binary_arms(task_id: str, body: BinaryArmsIn,
                              user: dict = Depends(require_user)) -> dict:
    from coscreen.binary_arm_data import save_binary_arms, list_binary_arms
    db = _screener_db(_task_or_404(task_id), user["username"])
    try:
        save_binary_arms(db, **body.model_dump())
    except ValueError as exc:
        raise _bad(exc) from exc
    return {"arms": list_binary_arms(db)}


@app.get("/api/tasks/{task_id}/analysis/binary-arms")
def analysis_list_binary_arms(task_id: str, user: dict = Depends(require_user)) -> dict:
    from coscreen.binary_arm_data import list_binary_arms
    db = _screener_db(_task_or_404(task_id), user["username"])
    return {"arms": list_binary_arms(db)}


@app.post("/api/tasks/{task_id}/analysis/single-group/counts")
def analysis_save_single_group_count(task_id: str, body: SingleGroupCountIn,
                                     user: dict = Depends(require_user)) -> dict:
    from coscreen.single_group_data import save_single_group_count, list_single_group_counts
    db = _screener_db(_task_or_404(task_id), user["username"])
    try:
        result_id = save_single_group_count(db, **body.model_dump())
    except ValueError as exc:
        raise _bad(exc) from exc
    return {"result_id": result_id, "results": list_single_group_counts(db)}


@app.get("/api/tasks/{task_id}/analysis/single-group/counts")
def analysis_list_single_group_counts(task_id: str, user: dict = Depends(require_user)) -> dict:
    from coscreen.single_group_data import list_single_group_counts
    db = _screener_db(_task_or_404(task_id), user["username"])
    return {"results": list_single_group_counts(db)}


@app.post("/api/tasks/{task_id}/analysis/single-group/counts/{result_id}/select")
def analysis_select_single_group_count(task_id: str, result_id: int,
                                       user: dict = Depends(require_user)) -> dict:
    from coscreen.single_group_data import select_single_group_count, list_single_group_counts
    db = _screener_db(_task_or_404(task_id), user["username"])
    try:
        select_single_group_count(db, result_id)
    except ValueError as exc:
        raise _bad(exc) from exc
    return {"results": list_single_group_counts(db)}


@app.get("/api/tasks/{task_id}/analysis/single-group/synthesis")
def analysis_single_group_synthesis(task_id: str, comparison: str, outcome: str,
                                    timepoint: str, kind: str,
                                    user: dict = Depends(require_user),
                                    format: str | None = Query(default=None, pattern="^(docx|pptx|csv|tex)$")) -> dict:
    from coscreen.single_group_data import selected_single_group_counts
    from coscreen.single_group_glmm import pool_single_group_counts
    db = _screener_db(_task_or_404(task_id), user["username"])
    try:
        rows = selected_single_group_counts(db, comparison, outcome, timepoint, kind)
        result = pool_single_group_counts(rows, kind)
    except ValueError as exc:
        raise _bad(exc) from exc
    result['method_sources'] = ([
        'Clopper & Pearson (1934), Biometrika 26:404–413, doi:10.1093/biomet/26.4.404; exact binomial interval for the common-proportion branch.',
        'Harrer et al. (2022), Doing Meta-Analysis with R, §§3.2.2 and 4.2.6; verify the original book text. Random branch: binomial-logit-normal GLMM with adaptive Gauss-Hermite quadrature.'
    ] if kind == 'proportion' else [
        'Garwood (1936), Biometrika 28:437–442, doi:10.1093/biomet/28.3-4.437; exact Poisson interval for the common-rate branch.',
        'Random branch: Poisson-log-normal GLMM with adaptive Gauss-Hermite quadrature; compare metafor rma.glmm documentation (https://wviechtb.github.io/metafor/reference/rma.glmm.html), verify the original method and person-time unit, and cite the method used.'
    ])
    if format:
        from custom_backend.analysis_audit_report import audit_response
        table = dict(title='Single-group event-count synthesis',
                     columns=['Branch', 'Method', 'Estimate', '95% CI low', '95% CI high', 'Status'],
                     rows=[[name, item.get('method') or item.get('model'), item.get('estimate'),
                            *(item.get('ci_95') or [None, None]), item.get('status', 'fitted')]
                           for name, item in [('Common parameter', result['fixed']),
                                              ('Random intercept', result['random'])]],
                     note=f"Kind={kind}; time unit={result['time_unit'] or 'not applicable'}. The common and random-intercept branches answer different questions; verify selected counts and cite the methods and source reports in the audit appendix.")
        return audit_response({**result, 'request':dict(comparison=comparison, outcome=outcome,
                              timepoint=timepoint, kind=kind),
                              'source_articles':source_articles(db, rows)}, format,
                              'Single-group count synthesis audit', tables=[table])
    return result


@app.post("/api/tasks/{task_id}/analysis/dependent-synthesis")
def analysis_dependent_synthesis(task_id: str, body: DependentSynthesisIn,
                                 user: dict = Depends(require_user),
                                 format: str | None = Query(default=None, pattern="^(docx|pptx|csv|tex)$")) -> dict:
    from coscreen.dependent_synthesis import synthesize_dependent_effects
    from coscreen.pool_compatibility import require_compatible_effects

    db = _screener_db(_task_or_404(task_id), user["username"])
    try:
        stored = analysis_mod.list_effects(db)
        result_ids = {result_id for block in body.blocks for result_id in block.result_ids}
        require_compatible_effects([row for row in stored if row["result_id"] in result_ids])
        result = synthesize_dependent_effects(
            stored, [block.model_dump() for block in body.blocks], body.model)
    except ValueError as exc:
        raise _bad(exc) from exc
    result['method_sources'] = [
        'Cheung (2015), Meta-Analysis: A Structural Equation Modeling Approach, ch. 5, pp. 121–133; check the original book text and cite the applicable GLS or random-effects section.',
        'Within-study covariances and their source notes are analyst supplied; verify each covariance against the original report or prespecified assumption.']
    if format:
        from custom_backend.analysis_audit_report import audit_response
        table = dict(title='Dependent-outcome synthesis',
                     columns=['Outcome', 'Studies', 'Estimate', 'SE', '95% CI low', '95% CI high'],
                     rows=[[row['outcome'], row['n_studies'], row['estimate'], row['se'],
                            row['ci_lower'], row['ci_upper']] for row in result['estimates']],
                     note=f"Model={result['model']}; analysis scale={result['analysis_scale']}. Full covariance blocks, source notes, selected effects and method references follow in the audit appendix.")
        return audit_response({**result, 'request':body.model_dump(),
                              'source_articles':source_articles(db, result['input_snapshot']['effects'])}, format,
                              'Dependent-outcome synthesis audit', tables=[table])
    return result


@app.post("/api/tasks/{task_id}/analysis/multilevel/random-intercept")
def analysis_multilevel_random_intercept(task_id: str, body: MultilevelRandomIn,
                                         user: dict = Depends(require_user),
                                         format: str | None = Query(default=None, pattern="^(docx|pptx|csv|tex)$")) -> dict:
    from coscreen.multilevel_random_intercept import fit_three_level_random_intercept
    from coscreen.pool_compatibility import require_compatible_effects
    db = _screener_db(_task_or_404(task_id), user["username"])
    try:
        if not body.construct_confirmed:
            raise ValueError("confirm the included effects have the same clinical construct, units and direction")
        if len(set(body.result_ids)) != len(body.result_ids):
            raise ValueError("result IDs must be unique")
        if (body.sampling_error_assumption == "independent_within_studies") != (body.within_study_covariances is None):
            raise ValueError("supply complete within-study covariance exactly when that assumption is selected")
        stored = {row["result_id"]: row for row in analysis_mod.list_effects(db)}
        if any(result_id not in stored for result_id in body.result_ids):
            raise ValueError("each result ID must exist in this task")
        selected = [stored[result_id] for result_id in body.result_ids]
        if any(not row["selected"] for row in selected):
            raise ValueError("all included result variants must be selected")
        if any(row["measure"] != body.measure or row["comparison"] != body.comparison for row in selected):
            raise ValueError("all included effects must have the requested comparison and measure")
        require_compatible_effects(selected, body.measure)
        scale = "log" if body.measure in analysis_mod.RATIOS | {"LOG_RATE", "LOG_ROM", "PAIRED_OR"} else "logit" if body.measure == "LOGIT_PROP" else "fisher_z" if body.measure == "FISHER_Z" else "natural"
        effects = [{"study_id": row["study_id"], "source_id": row["source_key"],
                    "source_locator": row["source_locator"], "effect_id": str(row["result_id"]),
                    "estimate": math.log(row["estimate"]) if body.measure in analysis_mod.RATIOS else row["estimate"],
                    "variance": row["se"] ** 2} for row in selected]
        result = fit_three_level_random_intercept(effects, body.within_study_covariances)
        result["measure"] = body.measure
        result["analysis_scale"] = scale
        result["comparison"] = body.comparison
        result["included_results"] = [{key: row[key] for key in
                                      ("result_id", "study_id", "source_key", "source_locator", "outcome", "timepoint")}
                                     for row in selected]
    except ValueError as exc:
        raise _bad(exc) from exc
    result['method_sources'] = [
        'Cheung (2015), Meta-Analysis: A Structural Equation Modeling Approach, ch. 6, pp. 183–184, equations 6.1–6.7; verify the original book text.',
        'Cheung (2014), Psychological Methods 19:211–229, doi:10.1037/a0032968; three-level dependent-effects model.',
        'Konstantopoulos (2011), Research Synthesis Methods 2:61–76, doi:10.1002/jrsm.35; variance components in three-level meta-analysis.']
    if format:
        from custom_backend.analysis_audit_report import audit_response
        table = dict(title='Three-level random-intercept synthesis',
                     columns=['Effects', 'Studies', 'Common mean', 'SE', '95% CI low', '95% CI high', 'Study variance', 'Within-study effect variance'],
                     rows=[[result['n_effects'], result['n_studies'], result['estimate'], result['se'],
                            *result['ci_95'], result['variance_components']['study'],
                            result['variance_components']['effect_within_study']]],
                     note=f"REML; sampling error assumption={body.sampling_error_assumption}. Full selected effects, supplied covariance matrices, optimizer diagnostics, parameters and method sources follow in the appendix.")
        return audit_response({**result, 'request':body.model_dump(), 'source_effects':selected,
                               'source_articles':source_articles(db, selected)},
                              format, 'Three-level synthesis audit', tables=[table])
    return result


@app.get("/api/tasks/{task_id}/analysis/leave-one-out")
def analysis_leave_one_out(task_id: str, comparison: str, outcome: str, timepoint: str,
                           measure: str, model: str, ci_method: str,
                           user: dict = Depends(require_user)) -> dict:
    db = _screener_db(_task_or_404(task_id), user["username"])
    try:
        return {"results": analysis_mod.leave_one_out(db, comparison, outcome, timepoint,
                                                      measure, model, ci_method)}
    except ValueError as exc:
        raise _bad(exc) from exc


@app.get("/api/tasks/{task_id}/analysis/influence")
def analysis_influence(task_id: str, comparison: str, outcome: str, timepoint: str,
                       measure: str, model: str,
                       user: dict = Depends(require_user),
                       format: str | None = Query(default=None, pattern="^(docx|pptx|csv|tex)$")) -> dict:
    from coscreen.influence_diagnostics import influence_diagnostics
    db = _screener_db(_task_or_404(task_id), user["username"])
    try:
        rows = analysis_mod.selected_effects(db, comparison, outcome, timepoint, measure)
        result = {"model": model, "measure": measure,
                  "results": influence_diagnostics(rows, measure, model),
                  "method_sources": [
                      "Viechtbauer & Cheung (2010), Research Synthesis Methods 1:112–125, doi:10.1002/jrsm.11; study-level influence and deleted-residual diagnostics.",
                      "Harrer et al. (2022), Doing Meta-Analysis with R, §5.4.2, pp. 156–162; verify the original book text."]}
        if format:
            from custom_backend.analysis_audit_report import audit_response
            table = dict(title='Study-level influence diagnostics',
                         columns=['Study', 'Source', 'Deleted pooled estimate', 'Deleted tau-squared', 'Externally standardized residual', "Cook's distance"],
                         rows=[[row['study_id'], row['source_key'], row['leave_one_out_pooled_analysis'],
                                row['leave_one_out_tau2'], row['externally_standardized_residual'], row['cooks_distance']]
                               for row in result['results']],
                         note='Each study is deleted and the selected model refitted. Diagnostics are not automatic exclusion rules; verify source records and cite the methods.')
            return audit_response({**result, 'request':dict(comparison=comparison, outcome=outcome,
                                  timepoint=timepoint, measure=measure, model=model), 'input_data':rows,
                                   'source_articles':source_articles(db, rows)},
                                  format, 'Influence diagnostics audit', tables=[table])
        return result
    except ValueError as exc:
        raise _bad(exc) from exc


@app.post("/api/tasks/{task_id}/analysis/bayesian/normal-meta")
def analysis_bayesian_normal_meta(task_id: str, body: BayesianNormalMetaIn,
                                  user: dict = Depends(require_user),
                                  format: str | None = Query(default=None, pattern="^(docx|pptx|csv|tex)$")) -> dict:
    from coscreen.bayesian_normal_meta import fit_bayesian_normal_meta
    db = _screener_db(_task_or_404(task_id), user["username"])
    try:
        rows = analysis_mod.selected_effects(db, body.comparison, body.outcome,
                                             body.timepoint, body.measure)
        result = fit_bayesian_normal_meta(rows, mu_prior_mean=body.mu_prior_mean,
                                          mu_prior_sd=body.mu_prior_sd,
                                          tau_prior_scale=body.tau_prior_scale)
        result["sources"] = [{"study_id": row["study_id"], "source_key": row["source_key"],
                              "source_locator": row["source_locator"], "result_id": row["result_id"]}
                             for row in rows]
        result['request'] = body.model_dump()
        result['method_sources'] = [
            'Grant & Di Tanna (2025), Bayesian Meta-Analysis, ch. 4.1 and §§4.2.1, 4.2.3, 4.4.4; verify the original book text.',
            'Prespecify and justify Normal μ and half-Normal τ priors; compare plausible prior choices before publication.',
        ]
        if format:
            from custom_backend.analysis_audit_report import audit_response
            table = dict(title='Bayesian posterior summary',
                columns=['Parameter','Mean','Median','95% credible interval'],
                rows=[[label,result[key]['mean'],result[key]['median'],result[key]['credible_interval_95']]
                      for key,label in [('mu','μ'),('tau','τ')]],
                note=f"Measure={body.measure}; analysis scale={result['analysis_scale']}; priors={result['priors']}. Posterior summaries condition on the selected independent effects and their known SEs. Full input effects, numerical integration diagnostics and citations follow in the audit appendix.")
            return audit_response({**result,'input_data':rows,
                                   'source_articles':source_articles(db, rows)},
                                  format,'Bayesian synthesis audit',tables=[table])
        return result
    except ValueError as exc:
        raise _bad(exc) from exc


@app.get("/api/tasks/{task_id}/analysis/tau2/profile-interval")
def analysis_tau2_profile_interval(task_id: str, comparison: str, outcome: str,
                                   timepoint: str, measure: str,
                                   user: dict = Depends(require_user),
                                   format: str | None = Query(default=None, pattern="^(docx|pptx|csv|tex)$")) -> dict:
    from coscreen.tau_profile_interval import profile_likelihood_tau2_interval
    db = _screener_db(_task_or_404(task_id), user["username"])
    try:
        rows = analysis_mod.selected_effects(db, comparison, outcome, timepoint, measure)
        effects = [math.log(row["estimate"]) if measure in analysis_mod.RATIOS else row["estimate"]
                   for row in rows]
        result = profile_likelihood_tau2_interval(effects, [row["se"] ** 2 for row in rows])
        result["measure"] = measure
        result["scale"] = ("log" if measure in analysis_mod.RATIOS | {"LOG_RATE", "LOG_ROM", "PAIRED_OR"}
                           else "logit" if measure == "LOGIT_PROP"
                           else "fisher_z" if measure == "FISHER_Z" else "natural")
        result["sources"] = [{"study_id": row["study_id"], "source_key": row["source_key"],
                              "result_id": row["result_id"]} for row in rows]
        result['method_sources'] = [
            'Viechtbauer (2007), Statistics in Medicine 26:37–52, doi:10.1002/sim.2514; heterogeneity interval methods.',
            'Harrer et al. (2022), Doing Meta-Analysis with R, §4.1.2.1, pp. 102–104, provides REML context; verify the original book text. The interval here uses an asymptotic REML profile-likelihood cutoff.']
        if format:
            from custom_backend.analysis_audit_report import audit_response
            table = dict(title='REML profile-likelihood tau-squared interval',
                         columns=['Tau-squared estimate', '95% lower', '95% upper', 'Converged', 'Analysis scale'],
                         rows=[[result['estimate'], result['lower'], result['upper'],
                                result['converged'], result['scale']]],
                         note='Profile-likelihood interval for between-study variance; upper bound may be unavailable. Verify selected effects and cite the original method.')
            return audit_response({**result, 'request':dict(comparison=comparison, outcome=outcome,
                                  timepoint=timepoint, measure=measure), 'input_data':rows,
                                   'source_articles':source_articles(db, rows)},
                                  format, 'Tau-squared profile interval audit', tables=[table])
        return result
    except (ValueError, RuntimeError) as exc:
        raise _bad(exc) from exc


@app.get("/api/tasks/{task_id}/analysis/advanced/{method}")
def analysis_advanced(task_id: str, method: str, comparison: str, outcome: str,
                      timepoint: str, measure: str, dimension_id: int | None = None,
                      model: str | None = None, user: dict = Depends(require_user)) -> dict:
    methods = {"subgroup": advanced_mod.subgroup,
               "meta-regression": advanced_mod.meta_regression,
               "small-study-effects": advanced_mod.small_study_effects,
               "cumulative": advanced_mod.cumulative}
    if method not in methods:
        raise _bad("unknown advanced analysis method")
    db = _screener_db(_task_or_404(task_id), user["username"])
    args = (db, comparison, outcome, timepoint, measure)
    try:
        if method in {"subgroup", "meta-regression", "cumulative"}:
            if dimension_id is None:
                raise ValueError("coding dimension is required")
            args += (dimension_id,)
        if method == "cumulative":
            if model is None:
                raise ValueError("choose a cumulative synthesis model explicitly")
            args += (model,)
        return methods[method](*args)
    except ValueError as exc:
        raise _bad(exc) from exc


@app.get("/api/tasks/{task_id}/analysis/publication-bias")
def analysis_publication_bias(task_id: str, comparison: str, outcome: str, timepoint: str,
                              measure: str, method: str, user: dict = Depends(require_user),
                              format: str | None = Query(default=None, pattern="^(docx|pptx|csv|tex)$")) -> dict:
    from coscreen.publication_bias import diagnose_publication_bias

    db = _screener_db(_task_or_404(task_id), user["username"])
    try:
        rows = analysis_mod.selected_effects(db, comparison, outcome, timepoint, measure)
        result = diagnose_publication_bias(rows, measure, method)
    except ValueError as exc:
        raise _bad(exc) from exc
    result['method_sources'] = ([
        'Begg & Mazumdar (1994), Biometrics 50:1088–1101, doi:10.2307/2533446.',
    ] if method == 'rank_correlation' else [
        'Rosenthal (1979), Psychological Bulletin 86:638–641, doi:10.1037/0033-2909.86.3.638.',
    ])
    if format:
        from custom_backend.analysis_audit_report import audit_response
        table = dict(title='Publication-bias diagnostic',
            columns=['Method','Studies','Statistic','Value','p-value'],
            rows=[[result['method_name'],result['n_studies'],
                   result.get('statistic_name','Fail-safe N'),
                   result.get('statistic',result.get('fail_safe_n')),
                   result.get('p_value',result.get('combined_p_value'))]],
            note='This diagnostic does not establish publication bias. Full source effects, assumptions, parameters and citations follow in the audit appendix.')
        return audit_response({**result,'request':dict(comparison=comparison,outcome=outcome,
                              timepoint=timepoint,measure=measure,method=method),
                              'input_data':rows,'source_articles':source_articles(db, rows)},
                              format,'Publication-bias audit',tables=[table])
    return result


@app.post("/api/tasks/{task_id}/analysis/publication-bias/orwin")
def analysis_orwin(task_id: str, body: OrwinIn, user: dict = Depends(require_user),
                   format: str | None = Query(default=None, pattern="^(docx|pptx|csv|tex)$")) -> dict:
    from coscreen.orwin_fail_safe import calculate_orwin_fail_safe
    db = _screener_db(_task_or_404(task_id), user["username"])
    try:
        rows = analysis_mod.selected_effects(db, body.comparison, body.outcome, body.timepoint, body.measure)
        result = calculate_orwin_fail_safe(rows, body.measure, body.target_effect, body.missing_effect)
    except ValueError as exc:
        raise _bad(exc) from exc
    result['method_sources'] = [
        'Orwin (1983), Journal of Educational Statistics 8:157–159, doi:10.3102/10769986008002157.',
        'Card (2012), Applied Meta-Analysis for Social Science Research, ch. 11, pp. 271–275; verify the original book text.',
    ]
    if format:
        from custom_backend.analysis_audit_report import audit_response
        table = dict(title='Orwin sensitivity count',
            columns=['Studies','Observed mean','Target effect','Missing-study mean','Fail-safe N'],
            rows=[[result['n_studies'],result['observed_mean'],result['target_effect'],
                   result['missing_effect'],result['fail_safe_n']]],
            note='Assumed missing-study mean and target are on the analysis scale. This count does not estimate actual missing studies. Full source effects, assumptions and citations follow in the audit appendix.')
        return audit_response({**result,'request':body.model_dump(),'input_data':rows,
                               'source_articles':source_articles(db, rows)},format,
                              'Orwin sensitivity audit',tables=[table])
    return result


@app.post("/api/tasks/{task_id}/analysis/publication-bias/trim-fill")
def analysis_trim_fill(task_id: str, body: TrimFillIn, user: dict = Depends(require_user),
                       format: str | None = Query(default=None, pattern="^(docx|pptx|csv|tex)$")) -> dict:
    db = _screener_db(_task_or_404(task_id), user["username"])
    try:
        rows = analysis_mod.selected_effects(db, body.comparison, body.outcome, body.timepoint, body.measure)
        if body.estimator == "L0" and body.pooling_method == "FE":
            from coscreen.trim_fill import trim_and_fill
            result = trim_and_fill(rows, body.measure, body.side)
        else:
            from coscreen.trim_fill_extended import trim_and_fill_extended
            result = trim_and_fill_extended(rows, body.measure, body.side,
                                           estimator=body.estimator, pooling_method=body.pooling_method)
        result.update(request=body.model_dump(), input_data=rows, method_sources=[
            "Duval & Tweedie (2000), JASA 95:89–98, doi:10.1080/01621459.2000.10473905",
            "Duval & Tweedie (2000), Biometrics 56:455–463, doi:10.1111/j.0006-341X.2000.00455.x",
        ])
        if format:
            from custom_backend.analysis_audit_report import audit_response
            table = dict(title='Trim-and-fill sensitivity',
                columns=['Series','Pooled estimate','95% CI','τ²'],
                rows=[[label,item['pooled'],f"{item['ci_low']} to {item['ci_high']}",result.get(key)]
                      for label,item,key in [('Observed',result['observed'],'observed_tau2'),
                                             ('Filled',result['adjusted'],'adjusted_tau2')]],
                note=f"Estimator={body.estimator}; side={body.side}; pooling={body.pooling_method}; imputed studies={result['n_missing']}. Imputed studies are hypothetical; the filled value is a sensitivity estimate. Full source effects, imputation trace, parameters and citations follow in the audit appendix.")
            return audit_response({**result,'source_articles':source_articles(db, rows)},
                                  format,'Trim-and-fill audit',tables=[table])
        return result
    except (ValueError, RuntimeError) as exc:
        raise _bad(exc) from exc


@app.get("/api/tasks/{task_id}/analysis/publication-bias/pet-peese")
def analysis_pet_peese(task_id: str, comparison: str, outcome: str, timepoint: str,
                       measure: str, user: dict = Depends(require_user),
                       format: str | None = Query(default=None, pattern="^(docx|pptx|csv|tex)$")) -> dict:
    from coscreen.pet_peese import diagnose_pet_peese
    db = _screener_db(_task_or_404(task_id), user["username"])
    try:
        rows = analysis_mod.selected_effects(db, comparison, outcome, timepoint, measure)
        result = diagnose_pet_peese(rows, measure)
    except ValueError as exc:
        raise _bad(exc) from exc
    result['method_sources'] = [
        'Stanley (2008), Oxford Bulletin of Economics and Statistics, doi:10.1111/j.1468-0084.2007.00487.x.',
        'Stanley & Doucouliagos (2014), Research Synthesis Methods, doi:10.1002/jrsm.1095.',
        'Harrer et al. (2022), Doing Meta-Analysis with R, ch. 9, p. 242; verify the original book text.',
    ]
    if format:
        from custom_backend.analysis_audit_report import audit_response
        table = dict(title='PET and PEESE coefficients',
            columns=['Fit','Term','Estimate','SE','95% CI'],
            rows=[[name,term,item['estimate'],item['se'],
                   f"{item['ci_low']} to {item['ci_high']}"]
                  for name,fit in result['fits'].items()
                  for term,item in fit['coefficients'].items()],
            note='Separate inverse-variance WLS sensitivity fits; intercepts extrapolate to SE=0. They do not prove publication bias or provide a causal correction. Full source effects, model parameters and citations follow in the audit appendix.')
        return audit_response({**result,'request':dict(comparison=comparison,outcome=outcome,
                              timepoint=timepoint,measure=measure),
                              'input_data':rows,'source_articles':source_articles(db, rows)},
                              format,'PET and PEESE audit',tables=[table])
    return result


@app.post("/api/tasks/{task_id}/analysis/meta-regression/multiple")
def analysis_meta_regression_multiple(task_id: str, body: MultiMetaRegressionIn,
                                      user: dict = Depends(require_user),
                                      format: str | None = Query(default=None, pattern="^(docx|pptx|csv|tex)$")) -> dict:
    from coscreen.meta_regression_model import fit_meta_regression

    db = _screener_db(_task_or_404(task_id), user["username"])
    dims = {dim["id"]: dim for dim in coding_mod.list_dimensions(db)}
    ids = [item.dimension_id for item in body.moderators]
    if len(set(ids)) != len(ids) or any(dim_id not in dims for dim_id in ids):
        raise _bad("moderator dimensions must be distinct and exist in this task")
    try:
        rows = analysis_mod.selected_effects(db, body.comparison, body.outcome, body.timepoint, body.measure)
    except ValueError as exc:
        raise _bad(exc) from exc
    if body.vcov_type is None:
        if body.model is not None or body.level is not None:
            raise _bad("model and level here require an explicit robust vcov_type")
        if len(rows) < 10:
            raise _bad("meta-regression requires at least ten independent studies")
    elif body.model is None or body.level is None or body.ci_method != "normal":
        raise _bad("robust inference requires explicit model and level; do not combine it with Knapp-Hartung")
    covariates = {row["study_id"]: {} for row in rows}
    spec, references = {}, {}
    try:
        for item in body.moderators:
            key = f"d{item.dimension_id}"
            values = advanced_mod._characteristics(db, rows, item.dimension_id)
            spec[key] = item.kind
            if item.kind == "categorical":
                if not item.reference or not item.reference.strip():
                    raise ValueError(f"reference level required for {dims[item.dimension_id]['name']}")
                references[key] = item.reference.strip()
            elif item.reference is not None:
                raise ValueError("continuous moderator cannot have a reference level")
            for study_id, raw in values.items():
                if item.kind == "continuous":
                    try:
                        value = float(raw)
                    except ValueError as exc:
                        raise ValueError(f"non-numeric moderator in study {study_id}") from exc
                    if not math.isfinite(value):
                        raise ValueError(f"non-finite moderator in study {study_id}")
                else:
                    value = raw.strip()
                covariates[study_id][key] = value
        interactions = []
        for a, b in body.interactions:
            if a == b or a not in ids or b not in ids:
                raise ValueError("interactions require two distinct selected moderators")
            pair = (f"d{a}", f"d{b}")
            if pair in interactions or pair[::-1] in interactions:
                raise ValueError("duplicate interaction")
            interactions.append(pair)
        if body.vcov_type:
            from custom_backend.robust_regression_view import fit_robust_view
            result = fit_robust_view(rows,covariates,spec,references,interactions,
                measure=body.measure,model=body.model,vcov_type=body.vcov_type,level=body.level)
            result['request'] = body.model_dump()
        else:
            result = fit_meta_regression(rows, covariates, spec, references, interactions, body.ci_method)
            result['tau2_method'] = 'profile REML'
            result['method_sources'] = [
                'Harrer et al. (2022), Doing Meta-Analysis with R, ch. 8, pp. 199–215: study-level meta-regression and REML context; verify the original book.',
                'Borenstein et al. (2021), Introduction to Meta-Analysis, 2nd ed., ch. 22: moderator coding and interpretation; its worked random-effects example uses a different tau-squared estimator.',
                ('Knapp & Hartung (2003), DOI 10.1002/sim.1482: t-based inference. This implementation uses modified Knapp–Hartung with max(1, residual Q/df), equivalent to metafor test=adhoc, not its unmodified test=knha.'
                 if body.ci_method == 'knha' else 'Normal Wald coefficient intervals and tests; consider a modified Knapp–Hartung sensitivity analysis when appropriate.'),
            ]
    except ValueError as exc:
        raise _bad(exc) from exc
    result["moderator_labels"] = {f"d{dim_id}": dims[dim_id]["name"] for dim_id in ids}
    result["sources"] = [{key: row.get(key) for key in ("study_id", "source_key", "source_locator", "result_id")}
                         for row in rows]
    if format:
        from custom_backend.analysis_audit_report import audit_response
        return audit_response({**result, 'request': body.model_dump(),
                               'source_effects': rows, 'coded_moderators': covariates,
                               'source_articles':source_articles(db, rows)},
                              format, 'Meta-regression audit')
    return result


@app.get("/api/tasks/{task_id}/analysis/network")
def analysis_network(task_id: str, outcome: str, timepoint: str, measure: str,
                     reference: str, user: dict = Depends(require_user)) -> dict:
    db = _screener_db(_task_or_404(task_id), user["username"])
    try:
        return network_mod.synthesize(db, outcome, timepoint, measure, reference)
    except ValueError as exc:
        raise _bad(exc) from exc


@app.post("/api/tasks/{task_id}/analysis/network/studies")
def save_network_study_ep(task_id: str, body: NetworkStudyIn,
                          user: dict = Depends(require_user)) -> dict:
    from coscreen.network_gls import validate_study
    from coscreen.network_study_data import save_network_study

    db = _screener_db(_task_or_404(task_id), user["username"])
    study = body.model_dump()
    try:
        validate_study(study, body.measure)
        save_network_study(db, body.study_id, body.outcome, body.timepoint, body.measure,
                           study["contrasts"], body.covariance, body.covariance_scale,
                           body.source_key, source_locator=body.source_locator)
    except ValueError as exc:
        raise _bad(exc) from exc
    return {"study_id": body.study_id, "n_contrasts": len(body.contrasts)}


@app.post("/api/tasks/{task_id}/analysis/network/from-arms")
def save_network_from_arms_ep(task_id: str, body: NetworkArmsIn,
                              user: dict = Depends(require_user)) -> dict:
    from coscreen.network_arm_covariance import arm_summaries_to_network_study
    from coscreen.network_study_data import save_network_study
    db = _screener_db(_task_or_404(task_id), user["username"])
    try:
        study = arm_summaries_to_network_study(body.study_id, body.arms,
                                               body.measure, body.reference)
        save_network_study(db, body.study_id, body.outcome, body.timepoint, body.measure,
                           study["contrasts"], study["covariance"],
                           study["covariance_scale"], body.source_key,
                           input_arms=body.arms, source_locator=body.source_locator)
    except ValueError as exc:
        raise _bad(exc) from exc
    return {"study": study, "source_key": body.source_key,
            "input_arms": body.arms}


@app.get("/api/tasks/{task_id}/analysis/network/studies")
def list_network_studies_ep(task_id: str, outcome: str, timepoint: str, measure: str,
                            user: dict = Depends(require_user)) -> dict:
    from coscreen.network_study_data import list_network_studies

    db = _screener_db(_task_or_404(task_id), user["username"])
    try:
        return {"studies": list_network_studies(db, outcome, timepoint, measure)}
    except ValueError as exc:
        raise _bad(exc) from exc


@app.get("/api/tasks/{task_id}/analysis/network/gls")
def analysis_network_gls_ep(task_id: str, outcome: str, timepoint: str, measure: str,
                            reference: str, model: str,
                            user: dict = Depends(require_user),
                            format: str | None = Query(default=None, pattern="^(docx|pptx|csv|tex)$")) -> dict:
    from coscreen.network_study_data import list_network_studies

    db = _screener_db(_task_or_404(task_id), user["username"])
    try:
        studies = list_network_studies(db, outcome, timepoint, measure)
        if model == "fixed":
            from coscreen.network_gls import synthesize_network
            result = synthesize_network(studies, measure, reference)
        elif model == "random_reml_common_tau":
            from coscreen.network_random import synthesize_network_random
            result = synthesize_network_random(studies, measure, reference)
        else:
            raise ValueError("network model must be fixed or random_reml_common_tau")
    except ValueError as exc:
        raise _bad(exc) from exc
    result["sources"] = {study["study_id"]: study["source_key"] for study in studies}
    result["method_sources"] = [
        "Harrer et al. (2022), Doing Meta-Analysis with R, ch. 12, pp. 336–338; verify the original book text.",
        "Hedges (2019), Handbook of Research Synthesis and Meta-Analysis, ch. 13, pp. 282–283.",
    ]
    if format:
        from custom_backend.analysis_audit_report import audit_response
        random_note = (f" Common-network REML τ²={result['random_effects']['tau2']} "
                       f"({result['random_effects']['scale']} variance scale)."
                       if result.get('random_effects') else '')
        table = dict(title='Network treatment estimates',
            columns=['Treatment','Comparator','Estimate',
                     f"SE ({result['estimates'][0]['se_scale']} scale)",'95% CI'],
            rows=[[row['treatment'],row['comparator'],row['estimate'],row['se'],
                   f"{row['ci_low']} to {row['ci_high']}"] for row in result['estimates']],
            note=f"Model={model}; reference={reference}; measure={measure}.{random_note} Residual Q is not a formal inconsistency test. Full contrast covariance, model diagnostics, parameters and citations follow in the audit appendix.")
        return audit_response({**result,'request':dict(outcome=outcome,timepoint=timepoint,
                              measure=measure,reference=reference,model=model),
                              'source_studies':studies,
                              'source_articles':source_articles(db, studies)},
                              format,'Network synthesis audit',tables=[table])
    return result


@app.get("/api/tasks/{task_id}/analysis/network/inconsistency")
def analysis_network_inconsistency_ep(task_id: str, outcome: str, timepoint: str,
                                      measure: str, model: Literal["fixed", "within_designs_moment", "preset"],
                                      user: dict = Depends(require_user),
                                      reference: str | None = None, tau2: float | None = None,
                                      format: str | None = Query(default=None, pattern="^(docx|pptx|csv|tex)$")) -> dict:
    from coscreen.network_study_data import list_network_studies
    from coscreen.network_inconsistency import assess_network_inconsistency

    db = _screener_db(_task_or_404(task_id), user["username"])
    try:
        studies = list_network_studies(db, outcome, timepoint, measure)
        if model == "fixed":
            if tau2 is not None:
                raise ValueError("Fixed-effect inconsistency does not accept a tau-squared input")
            result = assess_network_inconsistency(studies, measure)
        else:
            from coscreen.network_random_inconsistency import assess_network_random_inconsistency
            if not reference or not reference.strip():
                raise ValueError("Choose a reference treatment for random-effects inconsistency")
            if (model == "preset") != (tau2 is not None):
                raise ValueError("Provide tau-squared only for the explicit preset strategy")
            result = assess_network_random_inconsistency(studies, measure, reference, tau2=tau2)
        result["requested_model"] = model
        result["method_sources"] = [
            "Higgins et al. (2012), Research Synthesis Methods 3:98–110, doi:10.1002/jrsm.1044",
            "Jackson et al. (2012), Statistics in Medicine 31:3805–3820, doi:10.1002/sim.5453",
        ]
    except ValueError as exc:
        raise _bad(exc) from exc
    result["sources"] = {study["study_id"]: study["source_key"] for study in studies}
    result["source_locators"] = {study["study_id"]: study.get("source_locator", "") for study in studies}
    if format:
        from custom_backend.analysis_audit_report import audit_response
        h = result.get('heterogeneity')
        variance_note = (f" Within-designs or preset τ²={h['tau2']} ({h['source']}; "
                         f"{h['scale']} variance scale); whole-network REML τ²="
                         f"{h['consistency_reml']['tau2'] if h['consistency_reml']['tau2'] is not None else 'unavailable'}." if h else '')
        table = dict(title='Between-design inconsistency',
            columns=['Component','Q','df','p'],
            rows=[['Total',result['q_total'],result['df_total'],'—'],
                  ['Within designs',result['q_within_designs'],result['df_within_designs'],'—'],
                  ['Between designs',result['q_between_designs'],result['df_between_designs'],result['p_value']]],
            note=f"Model={model}; reference={reference}; preset tau-squared={tau2}.{variance_note} A non-significant result does not establish consistency or transitivity. Full source contrasts, covariance matrices and methods follow in the audit appendix.")
        return audit_response({**result,'request':dict(outcome=outcome,timepoint=timepoint,measure=measure,
                              model=model,reference=reference,tau2=tau2),'source_studies':studies,
                              'source_articles':source_articles(db, studies)},
                              format,'Network inconsistency audit',tables=[table])
    return result


@app.get("/api/tasks/{task_id}/analysis/network/node-split")
def analysis_network_node_split_ep(task_id: str, outcome: str, timepoint: str,
                                   measure: str, treatment: str, comparator: str, model: str,
                                   user: dict = Depends(require_user),
                                   format: str | None = Query(default=None, pattern="^(docx|pptx|csv|tex)$")) -> dict:
    from coscreen.network_study_data import list_network_studies
    if model == "fixed":
        from coscreen.network_node_split import split_network_contrast as split
    elif model == "random":
        from coscreen.network_random_node_split import split_network_contrast_random as split
    else:
        raise _bad("model must be fixed or random")
    db = _screener_db(_task_or_404(task_id), user["username"])
    try:
        studies = list_network_studies(db, outcome, timepoint, measure)
        result = split(studies, measure, treatment, comparator)
    except ValueError as exc:
        raise _bad(exc) from exc
    result["sources"] = {study["study_id"]: study["source_key"] for study in studies}
    result["source_locators"] = {study["study_id"]: study.get("source_locator", "") for study in studies}
    result["method_sources"] = [
        "Harrer et al. (2022), Doing Meta-Analysis with R, ch. 12, pp. 352–353; verify the original book text.",
        "Dias et al. (2010), Statistics in Medicine, doi:10.1002/sim.3767.",
    ]
    if format:
        from custom_backend.analysis_audit_report import audit_response
        partition_note = (f" Partition REML τ²: direct={result['direct']['tau2']}, "
                          f"indirect={result['indirect']['tau2']} "
                          f"({result['direct']['tau2_scale']} variance scale)."
                          if model == 'random' else '')
        table = dict(title='Direct and indirect evidence',
            columns=['Evidence','Estimate',f"SE ({result['direct']['se_scale']} scale)",
                     '95% CI','Study IDs'],
            rows=[[label,row['estimate'],row['se'],f"{row['ci_low']} to {row['ci_high']}",
                   ', '.join(row['study_ids'])] for label,row in
                  [('Direct',result['direct']),('Indirect',result['indirect'])]],
            note=f"Model={model}; treatment={treatment}; comparator={comparator}; disagreement p={result['disagreement']['p_value']}.{partition_note} This local diagnostic does not assess transitivity. Random effects use partition-specific REML tau-squared, unlike netmeta's full-network convention. Full source blocks, covariance, parameters and citations follow in the audit appendix.")
        return audit_response({**result,'request':dict(outcome=outcome,timepoint=timepoint,
                              measure=measure,treatment=treatment,comparator=comparator,model=model),
                              'source_studies':studies,
                              'source_articles':source_articles(db, studies)},
                              format,'Network node-split audit',tables=[table])
    return result


@app.get("/api/tasks/{task_id}/analysis/network/pscores")
def analysis_network_pscores_ep(task_id: str, outcome: str, timepoint: str, measure: str,
                                reference: str, benefit: str,
                                user: dict = Depends(require_user),
                                format: str | None = Query(default=None, pattern="^(docx|pptx|csv|tex)$")) -> dict:
    from coscreen.network_study_data import list_network_studies
    from coscreen.network_random import synthesize_network_random
    from coscreen.network_pscores import rank_network_pscores

    db = _screener_db(_task_or_404(task_id), user["username"])
    try:
        studies = list_network_studies(db, outcome, timepoint, measure)
        result = rank_network_pscores(
            synthesize_network_random(studies, measure, reference), benefit=benefit)
    except ValueError as exc:
        raise _bad(exc) from exc
    result["sources"] = {study["study_id"]: study["source_key"] for study in studies}
    result["method_sources"] = [
        "Rücker & Schwarzer (2015), BMC Medical Research Methodology 15:58, doi:10.1186/s12874-015-0060-8.",
        "Harrer et al. (2022), Doing Meta-Analysis with R, ch. 12, pp. 348–349; verify the original book text.",
    ]
    if format:
        from custom_backend.analysis_audit_report import audit_response
        table = dict(title='Treatment P-scores',columns=['Rank','Treatment','P-score'],
            rows=[[row['rank'],row['treatment'],row['p_score']] for row in result['ranking']],
            note=f"Benefit={benefit}; reference={reference}; model={result['source_model']}. P-scores are not probabilities of being best. Full input contrasts/covariances, parameters and citations follow in the audit appendix.")
        return audit_response({**result,'request':dict(outcome=outcome,timepoint=timepoint,
                              measure=measure,reference=reference,benefit=benefit),
                              'source_studies':studies,
                              'source_articles':source_articles(db, studies)},
                              format,'Network P-score audit',tables=[table])
    return result


@app.get("/api/tasks/{task_id}/analysis/dose/curves")
def analysis_dose_curves(task_id: str, user: dict = Depends(require_user)) -> dict:
    db = _screener_db(_task_or_404(task_id), user["username"])
    return {"curves": dose_mod.list_curves(db)}


@app.post("/api/tasks/{task_id}/analysis/dose/curves")
def analysis_dose_save(task_id: str, body: DoseCurveIn,
                       user: dict = Depends(require_user)) -> dict:
    db = _screener_db(_task_or_404(task_id), user["username"])
    try:
        dose_mod.save_curve(db, **body.model_dump(exclude={"source_locator"}),
                            input_data={"source_locator":body.source_locator} if body.source_locator else None)
    except ValueError as exc:
        raise _bad(exc) from exc
    return {"curves": dose_mod.list_curves(db)}


@app.get("/api/tasks/{task_id}/analysis/dose/synthesis")
def analysis_dose_synthesis(task_id: str, outcome: str, timepoint: str,
                            measure: str, model: str,
                            user: dict = Depends(require_user),
                            format: str | None = Query(default=None, pattern="^(docx|pptx|csv|tex)$")) -> dict:
    db = _screener_db(_task_or_404(task_id), user["username"])
    try:
        result = dose_mod.synthesize(db, outcome, timepoint, measure, model)
    except ValueError as exc:
        raise _bad(exc) from exc
    result["method_sources"] = [
        "Greenland & Longnecker (1992), American Journal of Epidemiology 135:1301–1309, doi:10.1093/oxfordjournals.aje.a116237; correlated dose contrasts.",
        "Crippa & Orsini (2016), BMC Medical Research Methodology 16:91, doi:10.1186/s12874-016-0189-0; two-stage GLS dose-response synthesis.",
    ]
    if model != "fixed":
        result["method_sources"].append(
            "Harrer et al. (2022), Doing Meta-Analysis with R, §§4.1.2.1–2, pp. 102–104; "
            "heterogeneity estimators and Hartung-Knapp intervals, not this exact dose-response combination."
        )
    if not format:
        return result
    from custom_backend.analysis_audit_report import audit_response
    curves = [row for row in dose_mod.list_curves(db)
              if (row["outcome"], row["timepoint"], row["measure"]) == (outcome, timepoint, measure)]
    by_study = {row["study_id"]:row for row in curves}
    interval = "normal 95%" if model == "fixed" else "modified HKSJ 95%"
    table = dict(title="Linear dose-response slopes", columns=["Study", "Slope", "SE", "Source", "Location"],
                 rows=[[row["study_id"], row["estimate"], row["se"], row["source_key"],
                        by_study[row["study_id"]]["input_data"].get("source_locator", "")]
                       for row in result["study_slopes"]],
                 note=f"{result['method']}; model={model}; {interval} pooled slope "
                      f"{result['pooled_slope']} [{result['ci_low']}, {result['ci_high']}]; "
                      f"τ²={result['tau2']}; {result['effect_scale']}; dose unit={result['dose_unit']}. "
                      "Check the source reports and full input covariance audit before citing this result.")
    return audit_response({**result, "request":dict(outcome=outcome, timepoint=timepoint,
                           measure=measure, model=model), "input_data":curves,
                           "source_articles":source_articles(db, curves)},
                          format, "Linear dose-response audit", tables=[table])


@app.post("/api/tasks/{task_id}/analysis/dose/nonlinear")
def analysis_dose_nonlinear(task_id: str, body: DoseNonlinearIn,
                            user: dict = Depends(require_user),
                            format: str | None = Query(default=None, pattern="^(docx|pptx|csv|tex)$")) -> dict:
    from coscreen.dose_nonlinear import fit_nonlinear_dose_response
    db = _screener_db(_task_or_404(task_id), user["username"])
    try:
        curves = [row for row in dose_mod.list_curves(db)
                  if (row["outcome"], row["timepoint"], row["measure"]) ==
                  (body.outcome, body.timepoint, body.measure)]
        result = fit_nonlinear_dose_response(curves, body.knots, body.prediction_reference_dose)
    except ValueError as exc:
        raise _bad(exc) from exc
    result['method_sources'] = [
        'Crippa & Orsini (2016), BMC Medical Research Methodology 16:91, doi:10.1186/s12874-016-0189-0; three-knot two-stage spline and multivariate meta-analysis.',
        'Orsini et al. (2012), American Journal of Epidemiology 175:66–73, doi:10.1093/aje/kwr265; reference-centered nonlinear dose-response framework. The present three-knot random-effects fit is an extension, not a reproduction of their four-knot fixed-effect macro.',
    ]
    if format:
        from custom_backend.analysis_audit_report import audit_response
        table = dict(title='Nonlinear dose-response curve on analysis scale',
                     columns=['Dose', 'Estimate', '95% CI low', '95% CI high'],
                     rows=[[row['dose'], row['estimate'], row['ci_low'], row['ci_high']]
                           for row in result['curve']],
                     note=f"{result['method']}; knots={body.knots}; reference dose={result['reference_dose']} {result['dose_unit']}. Curve intervals describe the mean curve, not a new-study prediction interval. Verify and cite the methods and source studies in the full audit.")
        return audit_response({**result, 'request':body.model_dump(), 'input_data':curves,
                               'source_articles':source_articles(db, curves)},
                              format, 'Nonlinear dose-response audit', tables=[table])
    return result


@app.post("/api/tasks/{task_id}/analysis/ipd/study")
def analysis_ipd_study(task_id: str, body: IpdStudyIn,
                       user: dict = Depends(require_user)) -> dict:
    db = _screener_db(_task_or_404(task_id), user["username"])
    try:
        return ipd_mod.import_study(db, **body.model_dump())
    except ValueError as exc:
        raise _bad(exc) from exc


@app.post("/api/tasks/{task_id}/analysis/ipd/survival/stratified-cox")
def analysis_ipd_survival(task_id: str, body: IpdSurvivalIn,
                          user: dict = Depends(require_user),
                          format: str | None = Query(default=None, pattern="^(docx|pptx|csv|tex)$")) -> dict:
    from coscreen.ipd_survival import fit_stratified_cox

    db = _screener_db(_task_or_404(task_id), user["username"])
    linked = {row["id"]: set(row["reports"]) for row in analysis_mod.list_studies(db)}
    if any(source not in linked.get(study_id, set())
           for study_id, source in body.study_sources.items()):
        raise _bad("each study source must be linked to its study in this task")
    try:
        result = fit_stratified_cox(body.participants, body.study_sources, ties=body.ties)
    except ValueError as exc:
        raise _bad(exc) from exc
    return _ipd_cox_summary_report(result, body, format, db)


def _ipd_cox_summary_report(result: dict, body: IpdSurvivalIn, format: str | None, db):
    result['method_sources'] = [
        'Cox (1972), Regression Models and Life-Tables, DOI 10.1111/j.2517-6161.1972.tb00899.x.',
        'Efron (1977), The Efficiency of Cox’s Likelihood Function for Censored Data, DOI 10.1080/01621459.1977.10480613.' if body.ties == 'efron'
        else 'Breslow (1974), Covariance Analysis of Censored Survival Data, DOI 10.2307/2529620.',
        'Borenstein et al. (2021), Introduction to Meta-Analysis, 2nd ed., ch. 39, pp. 351–355; conceptual IPD framing. Check the original book and cite the methods used.',
        'Cochrane Handbook, ch. 26, Individual participant data; check proportional-hazards assumptions and cite the original guidance.',
    ]
    if not format:
        return result
    from custom_backend.analysis_audit_report import audit_response
    terms = [('Treatment', 'hazard_ratio', 'hr_ci_low', 'hr_ci_high')]
    if isinstance(body, IpdAdjustedSurvivalIn):
        terms.append(('Covariate', 'covariate_hazard_ratio', 'covariate_hr_ci_low', 'covariate_hr_ci_high'))
    table = dict(title='Study-stratified Cox model',
                 columns=['Term', 'Hazard ratio', '95% CI low', '95% CI high'],
                 rows=[[label, result[estimate], result[low], result[high]]
                       for label, estimate, low, high in terms],
                 note=f"{result['method']}; {body.ties} ties; 95% Wald intervals. Check Cox (1972), the selected tie method, Cochrane Handbook ch. 26 and the original study reports; participant rows are omitted.")
    return audit_response({**result, 'parameters':body.model_dump(exclude={'participants'}),
                           'participant_count':len(body.participants), 'participant_rows_saved':False,
                           'source_articles':source_articles(db, [
                               {'source_key': key} for key in body.study_sources.values()])},
                          format, 'IPD Cox aggregate audit', tables=[table])


@app.post("/api/tasks/{task_id}/analysis/ipd/survival/adjusted-stratified-cox")
def analysis_ipd_adjusted_survival(task_id: str, body: IpdAdjustedSurvivalIn,
                                   user: dict = Depends(require_user),
                                   format: str | None = Query(default=None, pattern="^(docx|pptx|csv|tex)$")) -> dict:
    from coscreen.ipd_adjusted_cox import fit_adjusted_stratified_cox

    db = _screener_db(_task_or_404(task_id), user["username"])
    linked = {row["id"]: set(row["reports"]) for row in analysis_mod.list_studies(db)}
    if any(source not in linked.get(study_id, set())
           for study_id, source in body.study_sources.items()):
        raise _bad("each study source must be linked to its study in this task")
    try:
        result = fit_adjusted_stratified_cox(
            body.participants, body.study_sources,
            covariate_center=body.covariate_center,
            covariate_scale=body.covariate_scale, ties=body.ties)
    except ValueError as exc:
        raise _bad(exc) from exc
    return _ipd_cox_summary_report(result, body, format, db)


@app.post("/api/tasks/{task_id}/analysis/ipd/interaction/logistic")
def analysis_ipd_interaction(task_id: str, body: IpdInteractionIn,
                             user: dict = Depends(require_user),
                             format: str | None = Query(default=None, pattern="^(docx|pptx|csv|tex)$")) -> dict:
    from coscreen.ipd_interaction import fit_ipd_interaction

    db = _screener_db(_task_or_404(task_id), user["username"])
    linked = {row["id"]: set(row["reports"]) for row in analysis_mod.list_studies(db)}
    if any(source not in linked.get(study_id, set())
           for study_id, source in body.study_sources.items()):
        raise _bad("each study source must be linked to its study in this task")
    try:
        result = fit_ipd_interaction(body.rows, body.study_sources,
                                     covariate_center=body.covariate_center)
    except ValueError as exc:
        raise _bad(exc) from exc
    result['method_sources'] = [
        'Borenstein et al. (2021), Introduction to Meta-Analysis, 2nd ed., ch. 39, pp. 354–355; check the original book and cite the method used.',
        'Tierney, Stewart & Clarke, Cochrane Handbook, ch. 26, Individual participant data; check and cite the original guidance.',
        'Hua et al. (2017), Statistics in Medicine 36:772–789, DOI 10.1002/sim.7171; within-trial and across-trial interactions must be separated.',
    ]
    if format:
        from custom_backend.analysis_audit_report import audit_response
        table = dict(title='One-stage treatment-covariate interaction',
                     columns=['Term', 'Odds ratio', '95% CI low', '95% CI high'],
                     rows=[[label, result[key]['or'], *result[key]['ci95']]
                           for label, key in [('Treatment at reference', 'treatment_or_at_reference'),
                                              ('Within-study interaction', 'within_study_interaction_or_per_unit'),
                                              ('Across-study interaction', 'between_study_interaction_or_per_unit')]],
                     note='Wald intervals from a logistic model with study-specific intercepts. The across-study term is an ecological association. Verify source reports and cite Borenstein et al. (2021), Cochrane Handbook ch. 26, and Hua et al. (2017); participant rows are omitted.')
        return audit_response({**result, 'request':{'covariate_center':body.covariate_center,
                                                    'study_sources':body.study_sources,
                                                    'participant_count':len(body.rows)},
                               'source_articles':source_articles(db, [
                                   {'source_key': key} for key in body.study_sources.values()])},
                              format, 'IPD interaction aggregate audit', tables=[table])
    return result


@app.get("/api/tasks/{task_id}/analysis/risk-of-bias")
def analysis_rob(task_id: str, study_id: str | None = None, outcome: str | None = None,
                 user: dict = Depends(require_user)) -> dict:
    db = _screener_db(_task_or_404(task_id), user["username"])
    return {"assessments": analysis_mod.list_rob(db, study_id, outcome),
            "frameworks": {k: sorted(v[0]) for k, v in analysis_mod.ROB.items()}}


@app.get("/api/analysis/risk-of-bias/mappings")
def analysis_rob_mappings(user: dict = Depends(require_user)) -> dict:
    from coscreen.risk_of_bias_contract import CONTRACT_VERSION, mapping_catalog
    return {"contract_version": CONTRACT_VERSION, "mappings": mapping_catalog()}


@app.get("/api/tasks/{task_id}/analysis/risk-of-bias/v2")
def analysis_rob_v2(task_id: str, user: dict = Depends(require_user)) -> dict:
    from contextlib import closing
    from coscreen.rob_storage import list_records
    from coscreen.risk_of_bias_contract import from_legacy_row
    db = _screener_db(_task_or_404(task_id), user["username"])
    with closing(db_mod._connect(db)) as conn:
        records = list_records(conn)
    return {"records": records,
            "legacy_pending": [from_legacy_row(row) for row in analysis_mod.list_rob(db)]}


@app.post("/api/tasks/{task_id}/analysis/risk-of-bias/v2")
def analysis_create_rob_v2(task_id: str, body: dict,
                           user: dict = Depends(require_user)) -> dict:
    from contextlib import closing
    from coscreen.rob_storage import save_record
    from coscreen.risk_of_bias_contract import RobContractError
    if body.get("record_kind", "independent") != "independent":
        raise HTTPException(403, {"error_code": "consensus_role_required",
                                  "message_key": "analysis.rob.consensus_role_required", "params": {},
                                  "details": "Consensus requires a shared task role", "message": "Consensus requires a shared task role"})
    db = _screener_db(_task_or_404(task_id), user["username"])
    with closing(db_mod._connect(db)) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            return save_record(conn, body, actor_id=user["username"], expected_revision=0)
        except RobContractError as exc:
            raise _rob_contract_error(exc) from exc


@app.post("/api/tasks/{task_id}/analysis/risk-of-bias/v2/chart")
def analysis_rob_v2_chart(task_id: str, body: dict,
                          user: dict = Depends(require_user)) -> dict:
    from contextlib import closing
    from coscreen.rob_storage import list_records
    from coscreen.risk_of_bias_contract import RobContractError, build_chart_data, from_legacy_row
    db = _screener_db(_task_or_404(task_id), user["username"])
    try:
        refs = body["result_refs"]
        if not isinstance(refs, list) or not refs:
            raise ValueError("result_refs must be a nonempty list")
        effects = []
        with closing(db_mod._connect(db)) as conn:
            for ref in refs:
                rid, vid = int(ref["result_id"]), int(ref["effect_version_id"])
                row = conn.execute(
                    "SELECT result_id,effect_version_id,study_id,comparison,outcome,timepoint "
                    "FROM review_effect_versions WHERE result_id=? AND effect_version_id=?",
                    (rid, vid),
                ).fetchone()
                if row is not None:
                    effects.append(dict(zip(("result_id", "effect_version_id", "study_id",
                                             "comparison", "outcome", "timepoint"), row)))
            records = list_records(conn)
        records += [from_legacy_row(row) for row in analysis_mod.list_rob(db)]
        return build_chart_data(records, effects,
                                framework=body["framework"], tool_version=body["tool_version"],
                                variant=body["variant"], selector=body["selector"],
                                result_refs=refs, statistical_unit=body["statistical_unit"])
    except (KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, RobContractError):
            raise _rob_contract_error(exc) from exc
        raise HTTPException(400, {"error_code": "rob_chart_invalid_request",
                                  "message_key": "analysis.rob.chart_invalid_request", "params": {},
                                  "details": str(exc), "message": str(exc)}) from exc


@app.patch("/api/tasks/{task_id}/analysis/risk-of-bias/v2/{record_id}")
def analysis_revise_rob_v2(task_id: str, record_id: str, body: dict,
                           expected_revision: int = Query(ge=1),
                           user: dict = Depends(require_user)) -> dict:
    from contextlib import closing
    from coscreen.rob_storage import save_record
    from coscreen.risk_of_bias_contract import RobContractError
    db = _screener_db(_task_or_404(task_id), user["username"])
    with closing(db_mod._connect(db)) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            return save_record(conn, body, actor_id=user["username"],
                               expected_revision=expected_revision, record_id=record_id)
        except RobContractError as exc:
            raise _rob_contract_error(exc) from exc


@app.put("/api/tasks/{task_id}/analysis/risk-of-bias")
def analysis_save_rob(task_id: str, body: RobIn, user: dict = Depends(require_user)) -> dict:
    db = _screener_db(_task_or_404(task_id), user["username"])
    try:
        from coscreen.risk_of_bias_contract import check_legacy_put_scope, RobContractError
        check_legacy_put_scope(
            analysis_mod.list_rob(db, body.study_id, body.outcome),
            {**body.model_dump(), "reviewer": user["username"]},
        )
        analysis_mod.save_rob(db, reviewer=user["username"], **body.model_dump())
    except RobContractError as exc:
        raise HTTPException(409, {"error_code": exc.code,
                                  "message_key": "analysis.rob_ambiguous_scope", "params": exc.details,
                                  "details": str(exc), "message": str(exc)}) from exc
    except ValueError as exc:
        raise _bad(exc) from exc
    return {"assessments": analysis_mod.list_rob(db, body.study_id, body.outcome)}


@app.post("/api/tasks/{task_id}/analysis/risk-of-bias/v2/sensitivity")
def analysis_rob_v2_sensitivity(task_id: str, body: dict,
                                user: dict = Depends(require_user)) -> dict:
    from coscreen.rob_v2_sensitivity import RobV2SensitivityError, analyze
    selector = body.get("selector")
    if not isinstance(selector, dict) or selector.get("kind") != "reviewer" or \
            selector.get("reviewer_id") != user["username"]:
        raise HTTPException(403, {"error_code": "rob_assessor_scope_denied",
                                  "message_key": "analysis.rob.assessor_scope_denied", "params": {},
                                  "details": "Select the authenticated reviewer in this task database",
                                  "message": "Select the authenticated reviewer in this task database"})
    db = _screener_db(_task_or_404(task_id), user["username"])
    try:
        return analyze(db, **body)
    except RobV2SensitivityError as exc:
        raise HTTPException(400, {"error_code": exc.code,
                                  "message_key": "analysis.rob." + exc.code,
                                  "params": exc.details, "details": str(exc),
                                  "message": str(exc)}) from exc
    except TypeError as exc:
        raise HTTPException(400, {"error_code": "rob_sensitivity_invalid_request",
                                  "message_key": "analysis.rob.invalid_request", "params": {},
                                  "details": str(exc), "message": str(exc)}) from exc


@app.post("/api/tasks/{task_id}/analysis/risk-of-bias/sensitivity")
def analysis_rob_sensitivity(task_id: str, body: RobSensitivityIn,
                             user: dict = Depends(require_user),
                             format: str | None = Query(default=None, pattern="^(docx|pptx|csv|tex)$")) -> dict:
    from coscreen.rob_sensitivity import analyze
    db = _screener_db(_task_or_404(task_id), user["username"])
    try:
        result = analyze(db, body.comparison, body.outcome, body.timepoint, body.measure,
                         body.framework, user["username"], body.exclude_judgements,
                         body.model, body.ci_method)
    except ValueError as exc:
        raise _bad(exc) from exc
    result['method_sources'] = [
        'Cochrane Handbook for Systematic Reviews of Interventions, chs. 7–8 and §10.14; prespecify the judgement-based exclusion rule and cite the assessment framework.',
        'Harrer et al. (2022), Doing Meta-Analysis with R, chs. 3–5; verify synthesis and interval methods in the original book.']
    if format:
        from custom_backend.analysis_audit_report import audit_response
        rows = analysis_mod.selected_effects(db, body.comparison, body.outcome,
                                             body.timepoint, body.measure)
        table = dict(title='Risk-of-bias exclusion sensitivity',
                     columns=['Analysis', 'Studies', 'Pooled', '95% CI low', '95% CI high', 'Excluded study IDs'],
                     rows=[[label, item['n_studies'], item['pooled'], item['ci_low'], item['ci_high'],
                            ', '.join(item['excluded_study_ids'])]
                           for label, item in [('All assessed studies', result['all_studies']),
                                               ('After exclusion', result['exclusion_sensitivity'])]],
                     note='Exclusion is a sensitivity analysis and does not prove that a difference was caused by bias. Verify assessments and cite both assessment and synthesis methods.')
        return audit_response({**result, 'request':body.model_dump(), 'input_data':rows,
                               'source_articles':source_articles(db, rows)},
                              format, 'Risk-of-bias sensitivity audit', tables=[table])
    return result


@app.get("/api/tasks/{task_id}/analysis/dta/results")
def analysis_dta_results(task_id: str, user: dict = Depends(require_user)) -> dict:
    db = _screener_db(_task_or_404(task_id), user["username"])
    return {"results": dta_mod.list_results(db)}


@app.post("/api/tasks/{task_id}/analysis/dta/results")
def analysis_dta_save(task_id: str, body: DtaResultIn, user: dict = Depends(require_user)) -> dict:
    db = _screener_db(_task_or_404(task_id), user["username"])
    try:
        dta_mod.save_result(db, **body.model_dump())
    except ValueError as exc:
        raise _bad(exc) from exc
    return {"results": dta_mod.list_results(db)}


@app.get("/api/tasks/{task_id}/analysis/dta/synthesis")
def analysis_dta_synthesis(task_id: str, index_test: str, target_condition: str,
                           threshold: str | None = None, reference_standard: str | None = None,
                           user: dict = Depends(require_user),
                           format: str | None = Query(default=None, pattern="^(docx|pptx|csv|tex)$")) -> dict:
    db = _screener_db(_task_or_404(task_id), user["username"])
    try:
        result = dta_mod.synthesize(db, index_test, target_condition, threshold, reference_standard)
    except ValueError as exc:
        raise _bad(exc) from exc
    from custom_backend.dta_views import hsroc_view
    from custom_backend.dta_likelihood_view import likelihood_ratio_view
    from custom_backend.dta_prediction_view import prediction_region_view
    result = {**result, 'hsroc': hsroc_view(result), 'likelihood_ratios': likelihood_ratio_view(result),
              'prediction_region': prediction_region_view(result),
              'method_sources':[
                  'Reitsma et al. (2005), J Clin Epidemiol 58:982–990, doi:10.1016/j.jclinepi.2005.02.022.',
                  'Cochrane Handbook for Systematic Reviews of Diagnostic Test Accuracy, ch. 10; verify the current original text.',
              ]}
    if format:
        from custom_backend.analysis_audit_report import audit_response
        tables = [dict(title='Bivariate diagnostic summary',
            columns=['Studies','Sensitivity','Specificity','SD logit sensitivity','SD logit specificity','Method'],
            rows=[[result['n_studies'],result['sensitivity'],result['specificity'],
                   result['tau_sensitivity'],result['tau_specificity'],result['method']]],
            note='Between-study SDs are on logit sensitivity and specificity scales. Summary values are model based. Full 2×2 source tables, covariance, parameter estimates, optional HSROC/LR output and citations follow in the audit appendix.')]
        if result['hsroc'].get('available'):
            tables.append(dict(title='HSROC presentation',columns=['Parameter','Value'],
                rows=[[key,result['hsroc'][key]] for key in ('lambda','theta','beta','variance_alpha','variance_theta')],
                note='Accuracy and threshold variances are on latent HSROC parameter scales. Descriptive HSROC transform: no HSROC parameter confidence interval or shape significance test. The bivariate prediction region, when available, is reported separately.'))
        if result['prediction_region'].get('available'):
            tables.append(dict(title='Conditional between-study prediction region',
                columns=['Level', 'Boundary points', 'χ² df', 'χ² critical value'],
                rows=[[result['prediction_region']['confidence_level'],
                       len(result['prediction_region']['points']),
                       result['prediction_region']['chi_square_df'],
                       result['prediction_region']['chi_square_critical_value']]],
                note='Latent study operating points conditional on fitted bivariate-normal parameters; not a confidence region for the pooled mean. Coordinates, covariance, source tables and citations follow in the audit appendix.'))
        return audit_response({**result,'request':dict(index_test=index_test,target_condition=target_condition,
                              threshold=threshold,reference_standard=reference_standard),
                               'source_articles':source_articles(db, result['studies'])},
                              format,'Diagnostic accuracy audit',tables=tables)
    return result


@app.post("/api/tasks/{task_id}/analysis/dta/threshold-meta-regression")
def analysis_dta_threshold_meta_regression(task_id: str, body: DtaThresholdIn,
                                           user: dict = Depends(require_user),
                                           format: str | None = Query(default=None, pattern="^(docx|pptx|csv|tex)$")) -> dict:
    from coscreen.dta_threshold_model import synthesize
    db = _screener_db(_task_or_404(task_id), user["username"])
    try:
        result = synthesize(db, body.index_test, body.target_condition,
                            body.reference_standard, body.threshold_by_study)
    except ValueError as exc:
        raise _bad(exc) from exc
    result['method_sources'] = [
        'Cochrane DTA Handbook, ch. 10, §§10.1.4.2 and 10.5.3.1, archived version 1.0; verify the original/current edition.',
        'Reitsma et al. (2005), J Clin Epidemiol 58:982–990, doi:10.1016/j.jclinepi.2005.02.022.',
    ]
    if format:
        from custom_backend.analysis_audit_report import audit_response
        table = dict(title='Threshold association',columns=['Coefficient','Estimate','SE','95% CI'],
            rows=[[key,item['estimate'],item['se'],item['ci_95']]
                  for key,item in result['fixed_effects'].items()],
            note='Study-level exploratory association, not a causal effect of changing cutoff. Check linearity and study comparability. Full source 2×2 tables, selected thresholds, parameters and citations follow in the audit appendix.')
        return audit_response({**result,'request':body.model_dump(),
                               'source_articles':source_articles(db, result['studies'])},format,
                              'Diagnostic threshold audit',tables=[table])
    return result


@app.get("/api/tasks/{task_id}/analysis/qual/findings")
def analysis_qual_findings(task_id: str, user: dict = Depends(require_user)) -> dict:
    db = _screener_db(_task_or_404(task_id), user["username"])
    return {"findings": qual_mod.list_findings(db)}


@app.post("/api/tasks/{task_id}/analysis/qual/findings")
def analysis_qual_save_finding(task_id: str, body: QualFindingIn,
                               user: dict = Depends(require_user)) -> dict:
    db = _screener_db(_task_or_404(task_id), user["username"])
    try:
        fid = qual_mod.add_finding(db, reviewer=user["username"], **body.model_dump())
    except ValueError as exc:
        raise _bad(exc) from exc
    return {"id": fid, "findings": qual_mod.list_findings(db)}


@app.get("/api/tasks/{task_id}/analysis/qual/categories")
def analysis_qual_categories(task_id: str, user: dict = Depends(require_user)) -> dict:
    db = _screener_db(_task_or_404(task_id), user["username"])
    return {"categories": qual_mod.list_categories(db)}


@app.post("/api/tasks/{task_id}/analysis/qual/categories")
def analysis_qual_save_category(task_id: str, body: QualCategoryIn,
                                user: dict = Depends(require_user)) -> dict:
    db = _screener_db(_task_or_404(task_id), user["username"])
    try:
        cid = qual_mod.add_category(db, body.label, body.finding_ids, user["username"])
    except ValueError as exc:
        raise _bad(exc) from exc
    return {"id": cid, "categories": qual_mod.list_categories(db)}


@app.get("/api/tasks/{task_id}/analysis/qual/syntheses")
def analysis_qual_syntheses(task_id: str, user: dict = Depends(require_user)) -> dict:
    db = _screener_db(_task_or_404(task_id), user["username"])
    return {"syntheses": qual_mod.list_syntheses(db)}


@app.post("/api/tasks/{task_id}/analysis/qual/syntheses")
def analysis_qual_save_synthesis(task_id: str, body: QualSynthesisIn,
                                 user: dict = Depends(require_user)) -> dict:
    db = _screener_db(_task_or_404(task_id), user["username"])
    try:
        sid = qual_mod.add_synthesis(db, body.finding, body.category_ids, user["username"])
    except ValueError as exc:
        raise _bad(exc) from exc
    return {"id": sid, "trace": qual_mod.trace_synthesis(db, sid)}


# ---------------------------------------------------------------------------
# 全文（PDF）与高亮
# ---------------------------------------------------------------------------

@app.get("/api/tasks/{task_id}/fulltext/{zotero_key}")
def fulltext_pdf(task_id: str, zotero_key: str, screener: str | None = None,
                 user: dict = Depends(require_user)) -> Response:
    if not _KEY_RE.match(zotero_key or ""):
        raise _bad("非法文献键。", 400)
    info = _task_or_404(task_id)
    db = _screener_db(info, screener or user["username"])
    meta = get_fulltext(db, zotero_key)
    if meta is None:
        raise _bad("该文献尚未上传 PDF。", 404)
    data = load_pdf_bytes(meta)
    resolved = Path(meta.path).resolve()
    inside = is_within(resolved, info.dir.resolve()) or is_within(
        resolved, (DATA_DIR / "pdfs").resolve()
    )
    if data is None or not inside:
        raise _bad("PDF 文件不存在或已移动。", 404)
    return Response(
        content=data,
        media_type="application/pdf",
        headers={
            "Content-Disposition": (
                f"inline; filename=\"{clean_upload_name(meta.filename, 'document.pdf')}\""
            )
        },
    )


@app.post("/api/tasks/{task_id}/fulltext/{zotero_key}/upload")
async def upload_pdf(task_id: str, zotero_key: str, file: UploadFile = File(...),
                     screener: str | None = None, user: dict = Depends(require_user)) -> dict:
    if not _KEY_RE.match(zotero_key or ""):
        raise _bad("非法文献键。", 400)
    info = _task_or_404(task_id)
    db = _screener_db(info, screener or user["username"])
    data = await file.read()
    check_pdf_bytes(data)  # magic bytes + 100MB 上限（save_pdf 内还有一道校验）
    try:
        meta = save_pdf(db, zotero_key, data, pdfs_dir=_pdfs_dir(info), trace_context=_trace_context(info, user))
    except ValueError as exc:
        raise _bad(exc) from exc
    return {
        "zotero_key": meta.zotero_key,
        "filename": meta.filename,
        "sha256": meta.sha256,
        "n_pages": meta.n_pages,
        "added_at": meta.added_at,
    }


@app.get("/api/tasks/{task_id}/highlights/{zotero_key}")
def highlights_for(task_id: str, zotero_key: str, screener: str | None = None,
                   user: dict = Depends(require_user)) -> dict:
    if not _KEY_RE.match(zotero_key or ""):
        raise _bad("非法文献键。", 400)
    info = _task_or_404(task_id)
    db = _screener_db(info, screener or user["username"])
    return {"highlights": get_highlights_with_rects(db, zotero_key)}


@app.post("/api/tasks/{task_id}/highlights")
def add_highlights(task_id: str, body: HighlightsIn, screener: str | None = None,
                   user: dict = Depends(require_user)) -> dict:
    info = _task_or_404(task_id)
    db = _screener_db(info, screener or user["username"])
    events = [ev.model_dump() for ev in body.events]
    try:
        from coscreen.fulltext import add_highlight_rects

        count = add_highlight_rects(db, body.zotero_key, events)
    except ValueError as exc:
        raise _bad(exc) from exc
    except sqlite3.IntegrityError as exc:
        raise _bad("文献键不存在（请确认已导入该文献）。", 400) from exc
    return {"saved": count, "highlights": get_highlights_with_rects(db, body.zotero_key)}


@app.delete("/api/tasks/{task_id}/highlights/{highlight_id}")
def delete_highlight_ep(task_id: str, highlight_id: int, screener: str | None = None,
                        user: dict = Depends(require_user)) -> dict:
    info = _task_or_404(task_id)
    db = _screener_db(info, screener or user["username"])
    from coscreen.fulltext import delete_highlight

    deleted = delete_highlight(db, highlight_id)
    if not deleted:
        raise _bad("高亮不存在或已删除。", 404)
    return {"deleted": True}


# ---- Batch 11 wiring-prep endpoints (additive) ----
# 只读聚合端点（批次 11B）：把既有纯计算模块组合成"前端一次调用即得"的形态。
# 本段为纯追加：不修改上方任何既有行；两个端点均不写库。

class SensitivityComparisonIn(BaseModel):
    target: Literal['confidence', 'prediction']
    reference: str | None
    width_factor_threshold: float | None = Field(default=None, gt=1, allow_inf_nan=False)


class SensitivityViewIn(BaseModel):
    comparison: str = Field(min_length=1)
    outcome: str = Field(min_length=1)
    timepoint: str = Field(min_length=1)
    measure: str | None = None
    level: float = Field(default=0.95, gt=0, lt=1)
    comparison_view: SensitivityComparisonIn | None = None
    bootstrap_interval_type: Literal["percentile", "basic"] | None = None
    bootstrap_n: int | None = Field(default=None, ge=1, le=200000)
    seed: int | None = Field(default=None, ge=0)


class EntryGuidanceIn(BaseModel):
    available: list[str] = Field(min_length=1)


@app.post("/api/tasks/{task_id}/analysis/sensitivity-view")
def analysis_sensitivity_view(task_id: str, body: SensitivityViewIn,
                              user: dict = Depends(require_user),
                              format: str | None = Query(default=None, pattern="^(docx|pptx|csv|tex)$")) -> dict:
    """并排敏感性矩阵：模型 fixed/DL/REML × 区间方法 Wald/HKSJ/预测区间，
    附 τ² 三种区间、I² 及差异摘要；bootstrap 仅在显式给出 bootstrap_n+seed 时运行。
    只读：仅读取 selected_effects，不产生任何写库副作用。"""
    from coscreen.sensitivity_view import sensitivity_view

    db = _screener_db(_task_or_404(task_id), user["username"])
    try:
        rows = analysis_mod.selected_effects(db, body.comparison, body.outcome,
                                             body.timepoint, body.measure)
        if body.bootstrap_n is not None and body.bootstrap_interval_type is None:
            raise ValueError("Choose percentile or basic bootstrap intervals")
        if body.bootstrap_n is None and body.bootstrap_interval_type is not None:
            raise ValueError("Bootstrap interval type requires a replicate count and seed")
        result = sensitivity_view(rows, level=body.level,
                                  bootstrap_interval_type=body.bootstrap_interval_type or "percentile",
                                  bootstrap_n=body.bootstrap_n, seed=body.seed)
        result["sources"] = [{"study_id": row["study_id"], "source_key": row["source_key"],
                              "source_locator": row["source_locator"],
                              "result_id": row["result_id"]} for row in rows]
        if body.comparison_view and body.comparison_view.reference:
            from custom_backend.analysis_audit_report import sensitivity_differences
            result['comparison_view'] = sensitivity_differences(result, body.comparison_view.model_dump())
        if format:
            from custom_backend.analysis_audit_report import audit_response
            table = {
                'title': 'Sensitivity comparison',
                'columns': ['Model', 'Interval method', 'Estimate', 'Lower', 'Upper', 'Analysis-scale width'],
                'rows': [[cell['model'], cell['interval_method'], cell['estimate_display'],
                          *(cell['ci_display'] if cell['applicable'] else ['unavailable', 'unavailable']),
                          cell['width'] if cell['applicable'] else cell.get('reason', 'unavailable')]
                         for cell in result['cells']],
                'note': f"{result['k']} studies; {result['measure']}; level={body.level}; analysis scale={result['analysis_scale']}. "
                        'Wald/HKSJ confidence intervals and prediction intervals answer different questions. '
                        'The pooled-effect profile row uses random-effects ML, distinct from REML tau-squared profiling. '
                        'Cell-specific method sources, bootstrap settings, selected inputs and study sources are in the following audit appendix.',
            }
            profile = result['pooled_effect_profile']
            if profile['applicable']:
                table['rows'].append([profile['model'], profile['interval_method'], profile['estimate_display'],
                                      *profile['ci_display'], profile['width']])
            table['rows'] = [[f'{value:.6g}' if isinstance(value, float) else value for value in row] for row in table['rows']]
            tables = [table]
            if 'comparison_view' in result:
                comparison = result['comparison_view']
                tables.append(dict(title='Differences from selected reference',
                    columns=['Method','Estimate difference','Width / reference','Width factor','Threshold exceeded'],
                    rows=[[row['key'], *[f"{row[key]:.6g}" if row[key] is not None else 'unavailable'
                          for key in ['estimate_difference','width_ratio','width_factor']],
                          'Yes' if row['exceeds_threshold'] else 'No'] for row in comparison['rows']],
                    note=f"Reference={body.comparison_view.reference}; target={body.comparison_view.target}; "
                         f"threshold={body.comparison_view.width_factor_threshold}; scale={result['analysis_scale']}. " + comparison['note']))
            return audit_response({**result, 'request': body.model_dump(), 'source_effects': rows,
                                   'source_articles':source_articles(db, rows)},
                                  format, 'Method sensitivity audit', tables=tables)
        return result
    except ValueError as exc:
        raise _bad(exc) from exc


@app.post("/api/tasks/{task_id}/analysis/entry-guidance")
def analysis_entry_guidance(task_id: str, body: EntryGuidanceIn,
                            user: dict = Depends(require_user)) -> dict:
    """P4 引导式录入聚合：按 Tierney 精度阶梯返回候选录入路径与组合建议。
    纯静态规则引擎；连筛选库都不打开，端点零副作用。"""
    from coscreen.entry_guidance import recommend_entry_routes

    _task_or_404(task_id)  # 仅校验任务存在（含 404 路径）
    try:
        return recommend_entry_routes(body.available)
    except ValueError as exc:
        raise _bad(exc) from exc


# Optional route groups are isolated so a broken analysis cannot block core startup.
from importlib import import_module


def _coding_owner_db(info: tasks_mod.TaskInfo) -> Path | None:
    owner = collaboration_mod.get_owner(_collaboration_store(info), task_id=info.task_id)
    return _screener_db(info, str(owner["owner_username"])) if owner else None


def _shared_rob_db(info: tasks_mod.TaskInfo) -> Path:
    owner_db = _coding_owner_db(info)
    if owner_db is None:
        raise HTTPException(409, {"error_code": "collaboration_owner_unset",
                                  "message_key": "analysis.rob.collaboration_owner_unset", "params": {},
                                  "details": "Legacy task needs verified owner migration",
                                  "message": "Legacy task needs verified owner migration"})
    return owner_db


def _coding_target(info: tasks_mod.TaskInfo, item_key: str, field_key: str) -> bool:
    owner_db = _coding_owner_db(info)
    if owner_db is None or not field_key.isdecimal():
        return False
    return db_mod.get_article(owner_db, item_key) is not None and any(
        str(field["id"]) == field_key for field in coding_mod.list_dimensions(owner_db))


def _coding_field(info: tasks_mod.TaskInfo, field_key: str) -> dict | None:
    owner_db = _coding_owner_db(info)
    if owner_db is None or not field_key.isdecimal():
        return None
    field = next((item for item in coding_mod.list_dimensions(owner_db)
                  if str(item["id"]) == field_key), None)
    return ({key: field[key] for key in ("id", "name", "dtype", "options", "multi_select", "unit")}
            if field is not None else None)


def _coding_value(info: tasks_mod.TaskInfo, field_key: str, value: object) -> dict | None:
    field = _coding_field(info, field_key)
    if field is None:
        return None
    if field["dtype"] == "choice":
        if field["multi_select"]:
            valid = (isinstance(value, list) and all(isinstance(item, str) for item in value)
                     and len(value) == len(set(value)) and all(item in field["options"] for item in value))
        else:
            valid = isinstance(value, str) and value in field["options"]
    elif field["dtype"] == "numeric":
        try:
            valid = not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)
        except OverflowError:
            valid = False
    else:
        valid = isinstance(value, str)
    return field if valid else None


def _coding_authorize(info: tasks_mod.TaskInfo, user: dict, action: str) -> bool:
    return collaboration_mod.authorize_action(
        _collaboration_store(info), user["user_id"], action, task_id=info.task_id)

def _trace_authorize(info, user, action):
    # Trace is personal; membership grants no access to another actor's history.
    return _task_visible_to(info, user)


def _trace_personal_store(info, user):
    return _screener_db(info, user["username"])


def _trace_validate_subject(info, stage, subject_id):
    return db_mod.get_article(_coding_owner_db(info), subject_id) is not None


app.state.unavailable_analysis_extensions = []
for module_name, register_name, dependencies in [
    ("research_trace_api", "register_research_trace_routes",
     (app, require_user, _task_or_404, _trace_personal_store, _coding_owner_db,
      _trace_authorize, _trace_validate_subject)),
    ("study_intervals", "register_study_interval_routes", (app, require_user, _task_or_404)),
    ("nnt_view", "register_nnt_routes", (app, require_user, _task_or_404)),
    ("rosenberg_view", "register_rosenberg_routes", (app, require_user, _task_or_404, _screener_db, _bad)),
    ("peters_view", "register_peters_routes", (app, require_user, _task_or_404, _screener_db, _bad)),
    ("network_ranking_view", "register_network_ranking_routes", (app, require_user, _task_or_404, _screener_db, _bad)),
    ("subgroup_view", "register_subgroup_routes", (app, require_user, _task_or_404, _screener_db, _bad)),
    ("paired_binary_entry", "register_paired_binary_routes", (app, require_user, _task_or_404, _screener_db, _bad)),
    ("summary_stats_entry", "register_summary_stats_routes", (app, require_user, _task_or_404, _screener_db, _bad)),
    ("survival_summary_entry", "register_survival_summary_routes", (app, require_user, _task_or_404, _screener_db, _bad)),
    ("dta_exploratory_lr", "register_dta_exploratory_lr_routes", (app, require_user, _task_or_404, _screener_db, _bad)),
    ("dose_arm_entry", "register_dose_arm_routes", (app, require_user, _task_or_404, _screener_db, _bad)),
    ("bayesian_robust_view", "register_bayesian_robust_routes", (app, require_user, _task_or_404, _screener_db, _bad)),
    ("ipd_extended_view", "register_ipd_extended_routes", (app, require_user, _task_or_404, _screener_db, _bad)),
    ("coding_consensus_api", "register_coding_consensus_routes",
     (app, require_user, _task_or_404, _collaboration_store, _coding_authorize,
      _coding_target, _bad, _coding_value, _coding_field, _trace_context)),
    ("rob_shared_api", "register_rob_shared_routes",
     (app, require_user, _task_or_404, _shared_rob_db, _coding_authorize,
      _rob_contract_error)),
]:
    route_count = len(app.router.routes)
    try:
        extension = import_module('custom_backend.' + module_name)
        getattr(extension, register_name)(*dependencies)
    except Exception:
        # A registration failure may have added only part of a route group.
        del app.router.routes[route_count:]
        app.state.unavailable_analysis_extensions.append(module_name)
        logger.exception('Optional analysis extension unavailable: %s', module_name)


# ---- Batch W5 endpoints (additive) ----
# 方案设计器（Protocol Designer，衍生产品规格 §1）：保存 PICO 方案到 task.json 的
# "protocol" 字段并生成各数据库检索式。本段为纯追加，不修改上方任何既有行；
# 检索式生成是纯本地字符串计算（coscreen/protocol_designer.py，零副作用），不联网。

class ProtocolPICOIn(BaseModel):
    key: str = Field(min_length=1, max_length=50)
    label: str = Field(default="", max_length=100)
    value: str = Field(default="", max_length=2000)
    synonyms: list[str] = Field(default_factory=list, max_length=100)
    mesh_terms: list[str] = Field(default_factory=list, max_length=100)


class ProtocolIn(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    research_question: str = Field(default="", max_length=4000)
    pico: list[ProtocolPICOIn] = Field(min_length=1, max_length=20)
    inclusion_criteria: list[str] = Field(default_factory=list, max_length=100)
    exclusion_criteria: list[str] = Field(default_factory=list, max_length=100)
    date_from: str = Field(default="", max_length=20)
    date_to: str = Field(default="", max_length=20)


def _task_protocol(task_id: str) -> dict | None:
    """读 task.json 的 "protocol" 字段；任务损坏降级视作未保存（None）。"""
    info = _task_or_404(task_id)
    raw = tasks_mod._read_task_json(info.dir)  # noqa: SLF001  与 update_task 同一条读路径
    if not raw:  # None（无文件）或 {}（损坏降级）
        return None
    return raw.get("protocol")


@app.post("/api/tasks/{task_id}/protocol")
def create_protocol(task_id: str, body: ProtocolIn,
                    user: dict = Depends(require_user)) -> dict:
    """保存方案到 task.json 并生成检索式（纯本地计算，不联网）。"""
    from coscreen import protocol_designer
    from coscreen.protocol_designer import PICOElement, ProtocolDocument

    info = _task_or_404(task_id)
    pico = [PICOElement(
        key=p.key,
        label=(p.label or p.key).strip(),
        value=p.value.strip(),
        synonyms=[s.strip() for s in p.synonyms if s and s.strip()],
        mesh_terms=[m.strip() for m in p.mesh_terms if m and m.strip()],
    ) for p in body.pico]
    protocol = ProtocolDocument(
        title=body.title.strip(),
        research_question=body.research_question.strip(),
        pico=pico,
        inclusion_criteria=[c.strip() for c in body.inclusion_criteria if c and c.strip()],
        exclusion_criteria=[c.strip() for c in body.exclusion_criteria if c and c.strip()],
        search_strategies=protocol_designer.build_search_strategies(
            pico, date_from=body.date_from.strip(), date_to=body.date_to.strip()),
        created_at=datetime.now().isoformat(timespec="seconds"),
    )
    payload = protocol_designer.protocol_to_dict(protocol)
    # 读-改-写保留 task.json 的既有字段（display_name/screeners/...），仅设置 "protocol"；
    # 与 update_task 同一把路径级互斥锁，避免与并发的注册表写互相覆盖。
    with tasks_mod._task_json_lock(info.dir):  # noqa: SLF001  与 _mutate_task 同一把锁
        raw = tasks_mod._read_task_json(info.dir)  # noqa: SLF001
        if not raw:
            raise _bad("任务描述文件已损坏，无法保存方案。", 409)
        raw["protocol"] = payload
        tasks_mod._write_task_json(info.dir, raw)  # noqa: SLF001  原子写
    return {"protocol": payload, "sources": {}}


@app.get("/api/tasks/{task_id}/protocol")
def get_protocol(task_id: str, user: dict = Depends(require_user)) -> dict:
    """读取已保存的方案（未保存过 -> 404）。"""
    protocol = _task_protocol(task_id)
    if not protocol:
        raise _bad("该任务尚未保存方案。", 404)
    return {"protocol": protocol}


@app.get("/api/tasks/{task_id}/protocol/export")
def export_protocol(task_id: str, user: dict = Depends(require_user),
                    format: str = Query(default="markdown",
                                        pattern="^(markdown|json)$")) -> Response:
    """导出方案为 Markdown（PROSPERO 注册可复制粘贴）或 JSON（机器可读）。"""
    from coscreen import protocol_designer

    protocol = _task_protocol(task_id)
    if not protocol:
        raise _bad("该任务尚未保存方案。", 404)
    if format == "markdown":
        try:
            markdown = protocol_designer.protocol_to_markdown(protocol)
        except ValueError as exc:
            raise _bad(exc, 400) from exc
        return _file_response(markdown.encode("utf-8"), "text/markdown; charset=utf-8",
                              f"{task_id}-protocol.md")
    return _file_response(
        json.dumps(protocol, ensure_ascii=False, indent=2).encode("utf-8"),
        "application/json", f"{task_id}-protocol.json")


# ---- Batch W8 endpoints (additive) ----
# 证据地图（Evidence Mapper，衍生产品规格 §6）：TF-IDF 聚类 + x×y 交叉计数
# 热图 + 研究缺口识别。本段为纯追加，不修改上方任何既有行；端点只读筛选员
# 库（不写库），计算 100% 本地（coscreen/evidence_mapper.py，导入零副作用）。

EvidenceMapDimension = Literal["year", "journal", "study_design", "population", "cluster"]


@app.get("/api/tasks/{task_id}/evidence-map")
def get_evidence_map(
    task_id: str,
    x: EvidenceMapDimension = Query(description="X 轴维度（行标签）"),
    y: EvidenceMapDimension = Query(description="Y 轴维度（列标签，须不同于 x）"),
    clusters: int = Query(default=8, ge=1, le=64, description="TF-IDF 主题聚类数"),
    user: dict = Depends(require_user),
) -> dict:
    """生成证据地图数据（规格 §6.3；纯本地计算，不联网、不写库）。

    文献取当前筛选员库（重复条目已按 is_duplicate_of 过滤）。返回
    {"matrix", "x_labels", "y_labels", "clusters", "gaps"}：matrix[i][j] 为
    (x_labels[i], y_labels[j]) 组合的文献数；gaps 为计数 0 的单元格（研究
    缺口）。维度取值非法 → 422（Literal/范围校验）；x==y → 400（业务校验）。
    """
    from coscreen.evidence_mapper import generate_evidence_map

    db = _screener_db(_task_or_404(task_id), user["username"])
    articles = db_mod.list_articles(db, include_duplicates=False)
    try:
        return generate_evidence_map(
            [article.to_dict() for article in articles], x, y, cluster_count=clusters
        )
    except ValueError as exc:
        raise _bad(exc) from exc


# ---- Batch W7 endpoints (additive) ----
# GRADE 证据质量评估器（衍生产品规格 §3）：auto-suggest 从既有合成/RoB/发表偏倚
# 结果自动预填降级因子，save 保存用户评估（可覆盖自动建议，服务端复算最终
# 质量），export 导出 GRADE 证据剖面 / Summary of Findings 表。本段为纯追加，
# 不修改上方任何既有行；核心计算在 coscreen/grade_assessor.py（纯计算、零
# 副作用、不联网），grade_assessments 表经 coscreen/db.py 的 _connect 自愈
# 机制创建（旧库无需手工迁移）。

class GradeAssessmentIn(BaseModel):
    comparison: str = Field(min_length=1, max_length=300)
    outcome: str = Field(min_length=1, max_length=300)
    timepoint: str = Field(min_length=1, max_length=100)
    outcome_type: Literal['critical', 'important', 'not_critical']
    initial_design: Literal['rct', 'observational', 'diagnostic']
    rob_downgrade: Literal['none', 'serious', 'very_serious', 'very_very_serious']
    inconsistency_downgrade: Literal['none', 'serious', 'very_serious', 'very_very_serious']
    indirectness_downgrade: Literal['none', 'serious', 'very_serious', 'very_very_serious']
    imprecision_downgrade: Literal['none', 'serious', 'very_serious', 'very_very_serious']
    publication_bias_downgrade: Literal['none', 'serious', 'very_serious', 'very_very_serious']
    large_effect_upgrade: Literal['none', 'plus_one', 'plus_two'] = 'none'
    dose_response_upgrade: Literal['none', 'plus_one', 'plus_two'] = 'none'
    plausible_confounding_upgrade: Literal['none', 'plus_one', 'plus_two'] = 'none'
    signal_sources: dict = Field(default_factory=dict)
    override_notes: str = Field(default='', max_length=4000)


_GRADE_DOWNGRADE_COLUMNS = ('rob_downgrade', 'inconsistency_downgrade',
                            'indirectness_downgrade', 'imprecision_downgrade',
                            'publication_bias_downgrade')
_GRADE_UPGRADE_COLUMNS = ('large_effect_upgrade', 'dose_response_upgrade',
                          'plausible_confounding_upgrade')


def _grade_levels(body: 'GradeAssessmentIn') -> tuple[dict, dict | None]:
    """请求体列名 → grade_assessor 因子名（升级因子全 none 时传 None）。"""
    downgrades = {'rob': body.rob_downgrade,
                  'inconsistency': body.inconsistency_downgrade,
                  'indirectness': body.indirectness_downgrade,
                  'imprecision': body.imprecision_downgrade,
                  'publication_bias': body.publication_bias_downgrade}
    upgrades = {'large_effect': body.large_effect_upgrade,
                'dose_response': body.dose_response_upgrade,
                'plausible_confounding': body.plausible_confounding_upgrade}
    nonzero = {factor: level for factor, level in upgrades.items() if level != 'none'}
    return downgrades, (nonzero or None)


def _grade_latest_rob_counts(db: Path, comparison: str, outcome: str,
                             timepoint: str) -> dict | None:
    """按研究聚合 RoB overall（每研究取最新一条判定）→ overall_counts；无数据 None。"""
    rows = [row for row in analysis_mod.list_rob(db, outcome=outcome)
            if row['timepoint'] == timepoint and row['comparison'] in ('', comparison)]
    if not rows:
        return None
    latest: dict[str, dict] = {}
    for row in sorted(rows, key=lambda item: item['updated_at']):
        latest[row['study_id']] = row
    counts: dict[str, int] = {}
    for row in latest.values():
        counts[row['overall']] = counts.get(row['overall'], 0) + 1
    return {'overall_counts': counts}


def _grade_total_events(rows: list[dict]) -> int | None:
    """尽力从二分类效应 input_data 汇总事件总数；任一臂缺数则该研究不计，全缺返回 None。"""
    total, seen = 0, False
    for row in rows:
        data = row.get('input_data')
        if not isinstance(data, dict):
            continue
        arm_events = []
        for key in ('events_t', 'events_c'):
            value = data.get(key)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                arm_events = []
                break
            arm_events.append(value)
        if len(arm_events) == 2:
            seen = True
            total += sum(arm_events)
    return total if seen else None


@app.get("/api/tasks/{task_id}/analysis/grade/auto-suggest")
def grade_auto_suggest(task_id: str,
                       comparison: str = Query(min_length=1, max_length=300),
                       outcome: str = Query(min_length=1, max_length=300),
                       timepoint: str = Query(min_length=1, max_length=100),
                       initial_design: Literal['rct', 'observational', 'diagnostic'] = 'rct',
                       model: Literal['fixed', 'random', 'random_pm', 'random_reml'] = 'random',
                       user: dict = Depends(require_user)) -> dict:
    """从已有分析结果自动推导 GRADE 降级因子建议（纯本地计算，不联网）。"""
    from coscreen import grade_assessor

    info = _task_or_404(task_id)
    db = _screener_db(info, user["username"])
    try:
        rows = analysis_mod.selected_effects(db, comparison, outcome, timepoint)
        if not rows:
            raise ValueError("该比较/结局/时间点下没有已选定的效应量，无法自动预填")
        measure = rows[0]["measure"]
        synthesis = analysis_mod.synthesize(db, comparison, outcome, timepoint,
                                            measure, model=model)
    except ValueError as exc:
        raise _bad(exc) from exc
    # 展示尺度 CI（比值类为自然尺度）优先；不可表示时退回分析尺度。
    ci_low = synthesis["ci_low"] if synthesis["ci_low"] is not None else synthesis["ci_analysis_low"]
    ci_high = synthesis["ci_high"] if synthesis["ci_high"] is not None else synthesis["ci_analysis_high"]
    if measure in analysis_mod.RATIOS:
        null_value = 1.0
    elif measure == "LOGIT_PROP":
        null_value = 0.5  # logit 尺度 0 对应比例 0.5
    else:
        null_value = 0.0
    imprecision = {"ci_low": ci_low, "ci_high": ci_high, "null_value": null_value}
    total_events = _grade_total_events(rows)
    if total_events is not None:
        imprecision["total_events"] = total_events
    try:  # Egger 回归：<10 项研究或单组指标不可用 → 发表偏倚留空由用户手动评估
        egger = advanced_mod.small_study_effects(db, comparison, outcome, timepoint, measure)
        publication_bias = {"p_value": egger["egger_p"], "n_studies": egger["n_studies"]}
    except ValueError:
        publication_bias = None
    heterogeneity = {"i2_percent": synthesis["i2_percent"], "tau2": synthesis.get("tau2"),
                     "q_p": synthesis.get("q_p")}
    try:
        suggestion = grade_assessor.assess_grade(
            initial_design=initial_design,
            rob_assessment=_grade_latest_rob_counts(db, comparison, outcome, timepoint),
            heterogeneity=heterogeneity, imprecision=imprecision,
            publication_bias=publication_bias)
    except ValueError as exc:
        raise _bad(exc) from exc
    suggestion["synthesis"] = {
        "comparison": comparison, "outcome": outcome, "timepoint": timepoint,
        "measure": measure, "model": model, "ci_method": synthesis["ci_method"],
        "n_studies": synthesis["n_studies"], "pooled": synthesis["pooled"],
        "ci_low": synthesis["ci_low"], "ci_high": synthesis["ci_high"],
        "i2_percent": synthesis["i2_percent"], "total_events": total_events,
        "publication_bias_test_available": publication_bias is not None,
    }
    return suggestion


@app.post("/api/tasks/{task_id}/analysis/grade/save")
def grade_save(task_id: str, body: GradeAssessmentIn,
               user: dict = Depends(require_user)) -> dict:
    """保存用户的 GRADE 评估（可含对自动建议的覆盖）；服务端按同一规则复算最终质量。"""
    from contextlib import closing

    from coscreen import grade_assessor

    info = _task_or_404(task_id)
    db = _screener_db(info, user["username"])
    downgrades, upgrades = _grade_levels(body)
    try:
        outcome = grade_assessor.final_quality(body.initial_design, downgrades, upgrades)
    except ValueError as exc:
        raise _bad(exc) from exc
    signals = {"initial_design": body.initial_design,
               "final_quality": outcome["final_quality"],
               "signals": body.signal_sources if isinstance(body.signal_sources, dict) else {}}
    columns = ("task_id", "comparison", "outcome", "timepoint", "outcome_type",
               *_GRADE_DOWNGRADE_COLUMNS, *_GRADE_UPGRADE_COLUMNS,
               "signal_sources", "override_notes", "assessor", "created_at")
    # 冲突键（task_id, comparison, outcome, timepoint, assessor）不进 SET 子句；
    # 表/列名为本段固定字面量，数据值一律走参数占位符。
    conflict_keys = {"task_id", "comparison", "outcome", "timepoint", "assessor"}
    update_clause = ", ".join(f"{column}=excluded.{column}" for column in columns
                              if column not in conflict_keys)
    values = (task_id, body.comparison.strip(), body.outcome.strip(), body.timepoint.strip(),
              body.outcome_type,
              body.rob_downgrade, body.inconsistency_downgrade, body.indirectness_downgrade,
              body.imprecision_downgrade, body.publication_bias_downgrade,
              body.large_effect_upgrade, body.dose_response_upgrade,
              body.plausible_confounding_upgrade,
              json.dumps(signals, ensure_ascii=False), body.override_notes.strip(),
              user["username"], datetime.now().isoformat(timespec="seconds"))
    with closing(db_mod._connect(db)) as conn, conn:
        conn.execute(
            f"INSERT INTO grade_assessments ({', '.join(columns)}) "
            f"VALUES ({', '.join(['?'] * len(columns))}) "
            f"ON CONFLICT(task_id, comparison, outcome, timepoint, assessor) "
            f"DO UPDATE SET {update_clause}",
            values)
    return {"saved": True,
            "comparison": body.comparison.strip(), "outcome": body.outcome.strip(),
            "timepoint": body.timepoint.strip(), "downgrades": downgrades,
            "upgrades": {"large_effect": body.large_effect_upgrade,
                         "dose_response": body.dose_response_upgrade,
                         "plausible_confounding": body.plausible_confounding_upgrade},
            **outcome}


@app.get("/api/tasks/{task_id}/analysis/grade/export")
def grade_export(task_id: str,
                 format: str = Query(default="sof_table", pattern="^(sof_table)$"),
                 user: dict = Depends(require_user)) -> dict:
    """导出 GRADE 证据剖面 / Summary of Findings 表（结构化 JSON，可嵌入 Word/PPT）。"""
    from contextlib import closing

    from coscreen import grade_assessor

    info = _task_or_404(task_id)
    db = _screener_db(info, user["username"])
    columns = ("comparison", "outcome", "timepoint", "outcome_type",
               *_GRADE_DOWNGRADE_COLUMNS,
               *_GRADE_UPGRADE_COLUMNS, "signal_sources", "override_notes",
               "assessor", "created_at")
    with closing(db_mod._connect(db)) as conn:
        stored = conn.execute(
            f"SELECT {', '.join(columns)} FROM grade_assessments WHERE task_id=? "
            "ORDER BY outcome, timepoint, comparison, assessor", (task_id,)).fetchall()
    if not stored:
        raise _bad("该任务尚无已保存的 GRADE 评估。", 404)
    assessments = []
    for row in stored:
        record = dict(zip(columns, row))
        try:
            signals = json.loads(record.get("signal_sources") or "{}")
        except ValueError:
            signals = {}
        if not isinstance(signals, dict):
            signals = {}
        signal_sources = signals.get("signals", {})
        if not isinstance(signal_sources, dict):
            signal_sources = {}
        stored_final = signals.get("final_quality")
        try:  # 研究数为尽力补充：效应量缺失时留空，不影响导出
            n_studies = len(analysis_mod.selected_effects(
                db, record["comparison"], record["outcome"], record["timepoint"]))
        except ValueError:
            n_studies = None
        assessments.append({
            "comparison": record["comparison"], "outcome": record["outcome"],
            "timepoint": record["timepoint"], "outcome_type": record["outcome_type"],
            "signal_sources": signal_sources,
            "initial_design": signals.get("initial_design", "rct"),
            "downgrades": {"rob": record["rob_downgrade"],
                           "inconsistency": record["inconsistency_downgrade"],
                           "indirectness": record["indirectness_downgrade"],
                           "imprecision": record["imprecision_downgrade"],
                           "publication_bias": record["publication_bias_downgrade"]},
            "upgrades": {"large_effect": record["large_effect_upgrade"],
                         "dose_response": record["dose_response_upgrade"],
                         "plausible_confounding": record["plausible_confounding_upgrade"]},
            "final_quality": stored_final if stored_final in grade_assessor.GRADE_LEVELS else None,
            "n_studies": n_studies,
            "override_notes": record["override_notes"],
            "assessor": record["assessor"], "created_at": record["created_at"],
        })
    try:
        sof = grade_assessor.build_sof_table(assessments)
    except ValueError as exc:
        raise _bad(exc) from exc
    sof["n_assessments"] = len(assessments)
    sof["assessments"] = assessments
    return sof


# ---- Batch W6 endpoints (additive) ----
# 结构化数据提取模板（Data Extraction Templates，衍生产品规格 §2）：按研究设计的
# 提取表单一键映射为效应量入库。模板是代码不是数据（coscreen/extraction_templates.py，
# 零副作用纯计算）；效应量计算复用既有 arm-level / CI 换算实现，保存复用
# review_analysis.save_effect，入库前经 measure_registry.check_pool_compatibility 做
# 同池混指标校验。诊断 2×2 模板例外："DTA" 不是注册表已登记代码，按既有 DTA 架构
# 写入 review_dta_results（dta_analysis.save_result，bivariate 合成的输入）。

class ExtractionApplyIn(BaseModel):
    template_id: str = Field(min_length=1, max_length=100)
    study_id: str = Field(min_length=1, max_length=200)
    values: dict[str, float | int | str | bool | None] = Field(default_factory=dict)
    # review_effects 保存所需的分析层（comparison/outcome/timepoint）
    comparison: str = Field(default="", max_length=500)
    outcome: str = Field(default="", max_length=500)
    timepoint: str = Field(default="", max_length=500)
    effect_direction: str | None = Field(
        default=None, pattern="^(first_vs_second|second_vs_first|not_applicable)$")
    source_locator: str = Field(default="", max_length=500)
    # diagnostic_2x2 保存到 review_dta_results 所需的定位字段
    index_test: str = Field(default="", max_length=300)
    target_condition: str = Field(default="", max_length=300)
    threshold: str = Field(default="", max_length=300)
    reference_standard: str = Field(default="", max_length=300)


def _dta_count(values: dict, key: str) -> int:
    """提取值 -> 非负整数（DOR 信息性展示与 DTA 计数入库共用同一口径）。"""
    raw = values.get(key)
    if isinstance(raw, bool) or not isinstance(raw, (int, float)) or \
            float(raw) != int(raw) or int(raw) < 0:
        raise ValueError(f"{key.upper()} 必须是非负整数")
    return int(raw)


@app.get("/api/tasks/{task_id}/extraction/templates")
def list_extraction_templates(task_id: str, user: dict = Depends(require_user)) -> dict:
    """返回所有内置提取模板（纯静态：不校验任务存在性、不触碰数据库）。"""
    from coscreen import extraction_templates

    return {"templates": [t.to_dict() for t in extraction_templates.TEMPLATES.values()]}


@app.post("/api/tasks/{task_id}/extraction/{article_key}/apply")
def apply_extraction(task_id: str, article_key: str, body: ExtractionApplyIn,
                     user: dict = Depends(require_user)) -> dict:
    """校验 -> map_to_effect 计算效应量 -> 保存（review_effects 或 review_dta_results）。"""
    from coscreen import extraction_templates
    from coscreen import measure_registry
    from coscreen.pool_compatibility import PoolCompatibilityError

    info = _task_or_404(task_id)
    template = extraction_templates.TEMPLATES.get(body.template_id)
    if template is None:
        raise _bad("未知的提取模板。", 400)
    db = _screener_db(info, user["username"])
    try:
        effect = extraction_templates.map_to_effect(template, body.values,
                                                    body.study_id.strip(), article_key)
    except ValueError as exc:
        raise _bad(exc) from exc

    if effect["measure"] == extraction_templates.DTA_ROUTING_CODE:
        # 诊断 2×2：写入既有 DTA 存储（bivariate 合成的输入），不进入 review_effects。
        # （DTA 是路由标签而非注册表 measure 代码，见 extraction_templates 模块说明。）
        if body.effect_direction not in {None, "not_applicable"}:
            raise _bad("诊断 2×2 模板不适用效应方向。", 400)
        try:
            dta_mod.save_result(db, body.study_id.strip(), body.index_test.strip(),
                                body.target_condition.strip(), body.threshold.strip(),
                                body.reference_standard.strip(),
                                _dta_count(body.values, "tp"), _dta_count(body.values, "fp"),
                                _dta_count(body.values, "fn"), _dta_count(body.values, "tn"),
                                article_key, source_locator=body.source_locator.strip())
        except ValueError as exc:
            raise _bad(exc) from exc
        return {"saved": True, "measure": extraction_templates.DTA_ROUTING_CODE,
                "estimate": effect.get("estimate"), "se": effect.get("se"),
                "se_scale": effect.get("se_scale"),
                "dor_unavailable": bool(effect.get("dor_unavailable")),
                "storage": "review_dta_results"}

    # 通用效应量路径：经现有 save_effect 入库
    comparison, outcome, timepoint = (body.comparison.strip(), body.outcome.strip(),
                                      body.timepoint.strip())
    if not all((comparison, outcome, timepoint)):
        raise _bad("提取结果入库需要比较方向、结局与时间点。", 400)
    # 混池护栏（measure_registry）：同一 comparison/outcome/timepoint 池内
    # 不得出现不同 family 的效应量（与 §2.6 的注册表交互约定一致）。
    pooled = [row["measure"] for row in analysis_mod.list_effects(db)
              if (row["comparison"], row["outcome"], row["timepoint"]) == (comparison, outcome, timepoint)]
    try:
        compatibility = measure_registry.check_pool_compatibility([*pooled, effect["measure"]])
    except ValueError as exc:
        raise _bad(exc) from exc
    if not compatibility["compatible"]:
        raise _bad(PoolCompatibilityError(compatibility["reasons"]))
    # 效应方向策略与既有 /analysis/effects、/analysis/format-effects 完全一致：
    # 方向性指标必须显式选择，非方向指标不适用。
    directional = effect["measure"] not in NON_DIRECTIONAL_MEASURES
    if directional and body.effect_direction not in {"first_vs_second", "second_vs_first"}:
        raise _bad("请显式选择效应方向（试验组 vs 对照组）如何对应到比较方向。", 400)
    if not directional and body.effect_direction not in {None, "not_applicable"}:
        raise _bad("该效应量不适用效应方向。", 400)
    input_data = dict(effect["input_data"])
    if directional:
        input_data["effect_direction"] = body.effect_direction
    if effect["measure"] in analysis_mod.RATIOS:
        input_data["se_scale"] = "log"
        input_data["se_scale_declaration"] = ("explicit" if effect.get("se_scale") == "log"
                                              else "legacy_implicit")
    try:
        result_id = analysis_mod.save_effect(
            db, body.study_id.strip(), comparison, outcome, timepoint,
            effect["measure"], effect["estimate"], effect["se"], article_key,
            input_data, effect["entry_method"], source_locator=body.source_locator.strip())
    except ValueError as exc:
        raise _bad(exc) from exc
    return {"saved": True, "measure": effect["measure"], "estimate": effect["estimate"],
            "se": effect["se"], "se_scale": effect.get("se_scale"),
            "storage": "review_effects", "result_id": result_id}


# ---- Batch W11 endpoints (additive) ----
# AL 模型升级 API（规格书 docs/handoff/2026-09-30-al-model-upgrade-spec.md §6）：
#   ① GET/PUT /api/tasks/{id}/al/profile[s]——模型预设（W9 coscreen/al/profiles.py，
#      纯数据零副作用模块，handler 内延迟导入）；
#   ② GET /api/tasks/{id}/al/stop-rules——停止规则注册表（W10
#      coscreen/al/stop_registry.py，纯数据）；
#   ③ GET /api/tasks/{id}/al/stop-certificate/check——召回证书检查（W4
#      coscreen/al/stop_certificate.py，纯函数）：只读筛选进度与未筛池，
#      判断是否到达 look 点并返回所需抽样条数与当前 k_t；不执行抽样
#      （§10 不变量 3：抽样是用户手动操作，抽验结果的证书判定走
#      RecallCertificate.should_stop，不在本端点内）；
#   ④ GET /api/tasks/{id}/al/shared-ranking——双盲共享排序（S2 全量模拟
#      9,600 场：共享排序双盲拓扑 0/15 零漏检，支配独立模型）：合并任务
#      目录下所有筛选员库的决策训练一个排序器，输出统一 top-N。
# 本段为纯追加，不修改上方任何既有行；全部端点 require_user 鉴权（对
# 路径含 task_id 的请求，鉴权层已统一做任务存在性/可见性校验）。

class ALProfileSetIn(BaseModel):
    """PUT /al/profile 请求体：profile 必须是 §4.1 固定的四个预设名之一。"""

    profile: Literal["legacy", "tuned", "cjk", "svm"]


def _task_al_profile(info: tasks_mod.TaskInfo) -> str | None:
    """读 task.json 的 "al_profile" 字段（未设置/损坏降级 -> None=旧版行为）。"""
    raw = tasks_mod._read_task_json(info.dir)  # noqa: SLF001  与 W5 protocol 同一条读路径
    if not raw:  # None（无文件）或 {}（损坏降级）
        return None
    stored = raw.get("al_profile")
    return stored if isinstance(stored, str) and stored else None


@app.get("/api/tasks/{task_id}/al/profiles")
def get_al_profiles(task_id: str, user: dict = Depends(require_user)) -> dict:
    """返回可用的 AL 模型预设列表与任务当前显式选择。"""
    from coscreen.al.profiles import DEFAULT_PROFILE, PROFILES

    info = _task_or_404(task_id)
    return {
        "default": DEFAULT_PROFILE,
        "al_profile": _task_al_profile(info),
        "profiles": {
            name: {"name": p.name, "label": p.label, "description": p.description}
            for name, p in PROFILES.items()
        },
    }


@app.put("/api/tasks/{task_id}/al/profile")
def set_al_profile(task_id: str, body: ALProfileSetIn,
                   user: dict = Depends(require_user)) -> dict:
    """设置任务的 AL 模型预设（写入 task.json 的 "al_profile" 字段）。

    切换由用户显式操作（§0.4 第 4 条无静默默认变更）；已有的筛选标签
    不受影响，下一次重排起生效。字段随 TaskInfo/_json_payload round-trip，
    注册表写回（touch/rename/archive/register_screener）不会丢失。
    task.json 缺失/损坏（降级只读）时 409，与 W5 protocol 同一口径。
    """
    from coscreen.al.profiles import PROFILES

    info = _task_or_404(task_id)
    profile = PROFILES.get(body.profile)
    if profile is None:  # 双保险：Literal 已挡未知名，防 PROFILES 未来漂移
        raise _bad(f"未知的模型预设：{body.profile}", 422)
    # 读-改-写保留 task.json 既有字段（protocol/screeners/...），仅设置
    # "al_profile"；与 _mutate_task 同一把路径级互斥锁，避免并发覆盖。
    with tasks_mod._task_json_lock(info.dir):  # noqa: SLF001  与 _mutate_task 同一把锁
        raw = tasks_mod._read_task_json(info.dir)  # noqa: SLF001
        if not raw:
            raise _bad("任务描述文件已损坏，无法保存模型预设。", 409)
        raw["al_profile"] = body.profile
        tasks_mod._write_task_json(info.dir, raw)  # noqa: SLF001  原子写
    return {"al_profile": profile.name, "label": profile.label}


@app.get("/api/tasks/{task_id}/al/stop-rules")
def get_al_stop_rules(task_id: str, user: dict = Depends(require_user)) -> dict:
    """返回可用的停止规则列表（W10 注册表，纯静态：handler 不连库）。"""
    from coscreen.al.stop_registry import STOP_RULES

    return {"rules": STOP_RULES}


@app.get("/api/tasks/{task_id}/al/stop-certificate/check")
def check_stop_certificate(
    task_id: str,
    comparison: str,
    outcome: str,
    timepoint: str,
    screener: str | None = None,
    user: dict = Depends(require_user),
) -> dict:
    """检查当前是否满足召回证书的停止条件（只读，不执行抽样）。

    读当前筛选员的初筛进度（已筛数/纳入数）与未筛池，判断是否到达
    look 点（50%/70%/85%/95%）；到达时返回应随机抽验的条数
    m = ceil(池 × φ) 与当前容许剩余相关数 k_t = floor(found × (1-R)/R)。
    抽样本身由用户手动执行（§10 不变量 3），本端点不替用户选文献。
    comparison/outcome/timepoint 为评审问题上下文（与本库效应量定位
    三元组同构，便于前端把检查结果挂到对应比较/结局/时间点），原样
    回显，不参与证书数学。
    """
    from coscreen.al.stop_certificate import RecallCertificate

    info = _task_or_404(task_id)
    db = _screener_db(info, screener or user["username"])
    progress = db_mod.get_progress(db)
    total = int(progress["total"])
    screened = int(progress["screened"])
    found_relevant = int(progress["include"])
    pool_size = total - screened
    fraction = (screened / total) if total > 0 else 0.0

    certificate = RecallCertificate()  # S1 推荐参数：R=0.95 α=0.05 φ=0.30
    k_t = certificate.threshold(found_relevant)
    # 浮点容差：screened/total 与 look 字面量可能差 1 ulp（如 17/20 vs 0.85）
    reached = [f for f in certificate.look_fractions
               if total > 0 and fraction >= f - 1e-9]
    at_look = bool(reached)
    look_fraction = max(reached) if reached else None
    upcoming = [f for f in certificate.look_fractions if fraction < f - 1e-9]
    next_look = min(upcoming) if upcoming else None
    required = certificate.required_sample_size(pool_size, k_t) if at_look else None

    if total == 0:
        message = "语料为空，尚无法评估停止条件。"
    elif at_look:
        message = (
            f"已到达检查点（{look_fraction:.0%}）：从未筛池 {pool_size} 条中随机抽取 "
            f"{required} 条人工判读；抽验量足且未发现新的相关文献时，以 ≥95% 置信度"
            f"保证总召回 ≥95%（容许剩余相关 ≤ {k_t} 条）。"
        )
    else:
        message = (f"未到达检查点（当前已筛 {fraction:.1%}，下一检查点 "
                   f"{next_look:.0%}），请继续筛选。")

    return {
        "comparison": comparison,
        "outcome": outcome,
        "timepoint": timepoint,
        "certificate": {
            "recall_target": certificate.recall_target,
            "alpha": certificate.alpha,
            "sampling_fraction": certificate.sampling_fraction,
            "look_fractions": list(certificate.look_fractions),
        },
        "total_records": total,
        "screened_count": screened,
        "found_relevant": found_relevant,
        "pool_size": pool_size,
        "progress_fraction": fraction,
        "at_look_point": at_look,
        "look_fraction": look_fraction,
        "next_look_fraction": next_look,
        "k_t": k_t,
        "required_sample_size": required,
        "message": message,
    }


#: 共享排序的决策合并优先级：任一筛员纳入即按纳入训练（分歧键不静默
#: 丢弃；召回优先与产品哲学一致——证书目标是召回 ≥95%）。确定性合并，
#: 与库文件处理顺序无关。
_SHARED_DECISION_RANK = {"maybe": 0, "exclude": 1, "include": 2}


@app.get("/api/tasks/{task_id}/al/shared-ranking")
def get_shared_ranking(
    task_id: str,
    top: int | None = Query(default=None, ge=1, le=10000),
    screener: str | None = None,
    user: dict = Depends(require_user),
) -> dict:
    """双盲共享 AI 排序（S2 模拟：共享排序双盲拓扑最优，支配独立模型）。

    合并任务目录下所有筛选员库（{slug}.db，含当前用户）的决策
    （include > exclude > maybe），用合并标签训练**一个**排序器，
    返回统一排序供两个筛员共用。格式与现有 /al/rank 一致（附加
    al_profile/n_screeners/screeners 元数据）；top 省略时返回全量序。
    模型预设取 task.json 的 "al_profile"（PUT /al/profile 显式设置；
    未设置走旧版默认行为）。注意：既有 /al/rank 仍按各筛选员自己的
    标签独立排序（不在本段改动），需要共享模型时前端调本端点。
    """
    from coscreen.al.profiles import PROFILES

    info = _task_or_404(task_id)
    screener_name = screener or user["username"]
    db = _screener_db(info, screener_name)
    settings = _load_al_settings(info, screener_name)
    stop_suggestion = _al_stop_payload(db, settings["stop_threshold"])
    articles = db_mod.list_articles(db, include_duplicates=False)

    # 1. 合并筛选员决策：本人库在前，其余按文件名排序（结果与顺序无关，
    #    见 _SHARED_DECISION_RANK 的确定性合并）；手工拷入的损坏/非筛选
    #    库文件跳过，不阻塞共享排序（单点故障约束）。
    merged: dict[str, str] = {}
    contributors: list[str] = []
    for db_file in [db, *sorted(p for p in info.db_files
                                if p.resolve() != db.resolve())]:
        try:
            rows = db_mod.list_decisions(db_file)
        except sqlite3.DatabaseError:
            continue
        contributed = False
        for row in rows:
            decision = str(row["decision"])
            if decision not in _SHARED_DECISION_RANK:
                continue
            contributed = True
            key = str(row["zotero_key"])
            current = merged.get(key)
            if current is None or (_SHARED_DECISION_RANK[decision]
                                   > _SHARED_DECISION_RANK[current]):
                merged[key] = decision
        if contributed:
            contributors.append(db_file.stem)

    # 2. 任务级模型预设（用户显式设置才生效；被篡改成未知名时显式 409，
    #    不静默回落——§0.4 第 4 条）
    profile = _task_al_profile(info)
    if profile is not None and profile not in PROFILES:
        raise _bad(f"任务保存的 AI 模型预设无效：{profile}", 409)

    # 3. 合并标签训练一个排序器（n_labeled 为合并后的训练标签数）
    ranker = ActiveLearningRanker(
        strategy=settings["strategy"],
        seed=settings["seed"],
        min_labeled=settings["min_labeled"],
        profile=profile,
    )
    result = ranker.rank(articles, merged)
    meta = {
        "min_labeled": int(settings["min_labeled"]),
        "strategy": settings["strategy"],
        "seed": int(settings["seed"]),
        "stop_suggestion": stop_suggestion,
        "al_profile": profile,
        "n_screeners": len(contributors),
        "screeners": contributors,
    }
    if result is None:
        return {
            "available": False,
            "reason": "not_enough_labels",
            "n_labeled": sum(1 for v in merged.values() if v in ("include", "exclude")),
            "order": [],
            **meta,
        }
    order = [
        {"zotero_key": key, "score": float(result.scores.get(key, 0.0))}
        for key in result.order
    ]
    if top is not None:
        order = order[:top]
    return {"available": True, "n_labeled": result.n_labeled, "order": order, **meta}


# ---- Batch W13 endpoints (additive) ----
# 活体综述监控器（Living Review Monitor，衍生产品规格 §5）：
# - 第一层（默认，100% 离线）：检索式记忆 + 手动增量导入（RIS/CSV）→ 去重 →
#   AL 热启动 → 重跑 Meta → 前后对比 + 变更报告，计算全部走
#   coscreen/living_review.py（纯计算模块）；
# - 第二层：save-search 生成 PubMed 可点击 URL（仅拼字符串，点击后的检索由
#   用户浏览器完成，后端不联网）；
# - 第三层（可选插件，默认关闭）：check-now 延迟导入
#   custom_backend/living_review_plugin.py 调 PubMed E-utilities，失败返回降级
#   提示（不 500、不中断离线功能）；enable-auto 只把开关写入 task.json 的
#   "living_review_auto" 字段，绝不启动后台线程。
# 检索式与更新历史存 {task_dir}/living_review.json（读-改-写 + 原子写，与
# task.json 同一把路径级互斥锁）。本段为纯追加，不修改上方任何既有行。

_LIVING_REVIEW_DB_VALUES = ("pubmed", "embase", "cochrane", "manual")
_LR_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
#: 更新历史条数上限（最新在后写入，超限从头裁剪，防 living_review.json 无界增长）
_LR_HISTORY_LIMIT = 50


class LivingReviewSearchIn(BaseModel):
    database: str = Field(pattern="^(pubmed|embase|cochrane|manual)$")
    query_string: str = Field(min_length=1, max_length=4000)
    last_run_date: str = Field(default="", max_length=10)
    total_hits_last: int = Field(default=0, ge=0, le=10**9)


class LivingReviewAutoIn(BaseModel):
    enabled: bool
    interval_days: int = Field(default=30, ge=1, le=3650)


class LivingReviewCheckIn(BaseModel):
    """check-now 的可选结局层：缺省时只走去重/导入/AL，跳过 Meta 对比。"""

    comparison: str = Field(default="", max_length=300)
    outcome: str = Field(default="", max_length=300)
    timepoint: str = Field(default="", max_length=100)
    measure: str = Field(default="", max_length=30)


def _living_review_store(info: tasks_mod.TaskInfo) -> Path:
    """检索式 + 更新历史的存储文件：``{task_dir}/living_review.json``。"""
    path = info.dir / "living_review.json"
    if not is_within(path, info.dir):
        raise _bad("非法路径。", 400)
    return path


def _read_living_review_state(info: tasks_mod.TaskInfo) -> dict:
    """读 living_review.json；不存在返回空骨架；损坏显式 409（不静默重置数据）。"""
    path = _living_review_store(info)
    if not path.is_file():
        return {"search_records": [], "history": []}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise _bad("活体综述数据文件（living_review.json）已损坏。", 409)
    if not isinstance(state, dict):
        raise _bad("活体综述数据文件（living_review.json）已损坏。", 409)
    if not isinstance(state.get("search_records"), list):
        state["search_records"] = []
    if not isinstance(state.get("history"), list):
        state["history"] = []
    return state


def _mutate_living_review_state(info: tasks_mod.TaskInfo, mutate) -> dict:
    """living_review.json 的读-改-写骨架（与 task.json 同一把路径级互斥锁）。"""
    with tasks_mod._task_json_lock(info.dir):  # noqa: SLF001  与 _mutate_task 同一把锁
        state = _read_living_review_state(info)
        mutate(state)
        state["history"] = state["history"][-_LR_HISTORY_LIMIT:]
        tasks_mod._atomic_write_json(_living_review_store(info), state)  # noqa: SLF001  原子写
        return state


def _lr_validate_measure(measure: str) -> str:
    """指标代码校验：必须是合并引擎接受的指标（review_analysis.MEASURES）。"""
    if measure not in analysis_mod.MEASURES:
        raise _bad(f"不支持的指标代码：{measure}", 400)
    return measure


def _lr_history_entry(result, *, source: str, label: str,
                      comparison: str, outcome: str, timepoint: str,
                      measure: str) -> dict:
    """IncrementalResult -> 历史条目（GET history 的每行摘要）。"""
    return {
        "when": datetime.now().isoformat(timespec="seconds"),
        "source": source,  # "manual" | "pubmed"
        "label": label,    # 文件名 / 插件拉取标识
        "comparison": comparison, "outcome": outcome,
        "timepoint": timepoint, "measure": measure,
        "n_new": len(result.new_articles),
        "duplicates_removed": result.duplicates_removed,
        "al_available": result.al_available,
        "conclusion_changed": result.conclusion_changed,
        "meta_before": result.meta_before,
        "meta_after": result.meta_after,
        "change_report": result.change_report,
    }


def _lr_ranker_for_task(info: tasks_mod.TaskInfo, screener: str) -> ActiveLearningRanker:
    """按任务级 AL 设置构建热启动排序器（策略/种子/min_labeled/预设与 /al/rank 同源；
    预设只能是用户显式设置的 task.json "al_profile"，被篡改成未知名时显式 409）。"""
    from coscreen.al.profiles import PROFILES

    settings = _load_al_settings(info, screener)
    profile = _task_al_profile(info)
    if profile is not None and profile not in PROFILES:
        raise _bad(f"任务保存的 AI 模型预设无效：{profile}", 409)
    return ActiveLearningRanker(
        strategy=settings["strategy"], seed=settings["seed"],
        min_labeled=settings["min_labeled"], profile=profile,
    )


@app.get("/api/tasks/{task_id}/living-review/search-records")
def living_review_search_records(task_id: str,
                                 user: dict = Depends(require_user)) -> dict:
    """获取检索式列表和自动监控设置（纯读，不建筛选员库）。"""
    info = _task_or_404(task_id)
    state = _read_living_review_state(info)
    raw = tasks_mod._read_task_json(info.dir) or {}  # noqa: SLF001
    auto = raw.get("living_review_auto")
    if not isinstance(auto, dict):
        auto = {}
    enabled = auto.get("enabled")
    interval_days = auto.get("interval_days", 30)
    return {
        "search_records": state["search_records"],
        "living_review_auto": {
            "enabled": enabled if isinstance(enabled, bool) else False,
            "interval_days": (interval_days if isinstance(interval_days, int)
                              and not isinstance(interval_days, bool) else 30),
        },
    }


@app.post("/api/tasks/{task_id}/living-review/save-search")
def living_review_save_search(task_id: str, body: LivingReviewSearchIn,
                              user: dict = Depends(require_user)) -> dict:
    """保存/更新检索式（检索式记忆）。URL 仅对 PubMed 生成（拼字符串，不联网）。"""
    from coscreen.living_review import SearchRecord, record_to_dict
    from coscreen.protocol_designer import generate_pubmed_url

    info = _task_or_404(task_id)
    if body.last_run_date and not _LR_DATE_RE.match(body.last_run_date):
        raise _bad("last_run_date 必须为 YYYY-MM-DD。", 400)
    record = SearchRecord(
        database=body.database,
        query_string=body.query_string.strip(),
        last_run_date=body.last_run_date.strip(),
        url=(generate_pubmed_url(body.query_string.strip())
             if body.database == "pubmed" else ""),
        total_hits_last=body.total_hits_last,
        saved_at=datetime.now().isoformat(timespec="seconds"),
    )
    payload = record_to_dict(record)

    def _upsert(state: dict) -> None:
        kept = [
            row for row in state["search_records"]
            if not (row.get("database") == record.database
                    and row.get("query_string") == record.query_string)
        ]
        kept.append(payload)
        state["search_records"] = kept

    _mutate_living_review_state(info, _upsert)
    return {"search_record": payload, "search_records":
            _read_living_review_state(info)["search_records"]}


@app.post("/api/tasks/{task_id}/living-review/import-update")
async def living_review_import_update(
    task_id: str,
    file: UploadFile = File(...),
    comparison: str = Form(min_length=1, max_length=300),
    outcome: str = Form(min_length=1, max_length=300),
    timepoint: str = Form(min_length=1, max_length=100),
    measure: str = Form(min_length=1, max_length=30),
    screener: str | None = Query(default=None),
    user: dict = Depends(require_user),
) -> dict:
    """手动导入增量文献文件（第一层核心入口，100% 离线）。

    上传校验（check_text_bytes）→ coscreen.living_review.process_incremental_update
    （解析/去重/入库/AL 热启动/重跑 Meta/前后对比/变更报告）→ 历史留痕。
    """
    from coscreen import living_review as lr_mod
    from coscreen.parsers import ParseError

    info = _task_or_404(task_id)
    screener_name = screener or user["username"]
    db = _screener_db(info, screener_name)
    _lr_validate_measure(measure)

    name = clean_upload_name(file.filename or "", "update")
    data = await file.read()
    check_extension(name, TEXT_EXTENSIONS)
    check_text_bytes(data)  # 大小 + 可解码 + 无 NUL（先于解析执行）

    tmpdir = Path(tempfile.mkdtemp(prefix="cobook_living_"))
    try:
        src = tmpdir / name
        src.write_bytes(data)
        try:
            result = lr_mod.process_incremental_update(
                str(db), str(src), comparison, outcome, timepoint, measure,
                al_ranker=_lr_ranker_for_task(info, screener_name),
            )
        except ParseError as exc:
            raise _bad(f"解析失败（{name}）：{exc}") from exc
        except ValueError as exc:
            raise _bad(str(exc), 400) from exc
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    entry = _lr_history_entry(result, source="manual", label=name,
                              comparison=comparison, outcome=outcome,
                              timepoint=timepoint, measure=measure)

    def _append(state: dict) -> None:
        state["history"].append(entry)

    _mutate_living_review_state(info, _append)
    tasks_mod.touch_task(info.task_id, DATA_DIR, throttle=0)
    return {**result.to_dict(), "history_entry": entry}


@app.get("/api/tasks/{task_id}/living-review/history")
def living_review_history(task_id: str,
                          user: dict = Depends(require_user)) -> dict:
    """获取历次更新的影响评估摘要（最新在前；纯读，不建筛选员库）。"""
    info = _task_or_404(task_id)
    state = _read_living_review_state(info)
    return {"history": list(reversed(state["history"]))}


@app.post("/api/tasks/{task_id}/living-review/enable-auto")
def living_review_enable_auto(task_id: str, body: LivingReviewAutoIn,
                              user: dict = Depends(require_user)) -> dict:
    """开启/关闭自动监控（显式操作，默认关闭）。

    只把设置写入 task.json 的 "living_review_auto" 字段——不启动任何后台
    线程/定时任务；增量拉取永远由用户手动点击 check-now 触发。
    """
    info = _task_or_404(task_id)
    settings = {"enabled": bool(body.enabled),
                "interval_days": int(body.interval_days)}
    # 读-改-写保留 task.json 既有字段（与 create_protocol 同一条写路径与锁）
    with tasks_mod._task_json_lock(info.dir):  # noqa: SLF001  与 _mutate_task 同一把锁
        raw = tasks_mod._read_task_json(info.dir)  # noqa: SLF001
        if not raw:
            raise _bad("任务描述文件已损坏，无法保存自动监控设置。", 409)
        raw["living_review_auto"] = settings
        tasks_mod._write_task_json(info.dir, raw)  # noqa: SLF001  原子写
    return {"living_review_auto": settings}


@app.post("/api/tasks/{task_id}/living-review/check-now")
def living_review_check_now(task_id: str, body: LivingReviewCheckIn | None = None,
                            user: dict = Depends(require_user)) -> dict:
    """立即执行一次 PubMed 增量拉取（手动触发，非后台自动）。

    前置条件（缺一即 4xx）：用户已显式开启自动监控（task.json
    living_review_auto.enabled）；已保存 PubMed 检索式。成功 → 拉回的文献直接走
    增量管线（与手动导入同一条代码路径）；失败（网络不可达/响应不可解析）→
    200 + 降级提示，离线功能不受影响。本端点是全应用唯一触网入口。
    """
    from coscreen import living_review as lr_mod
    from coscreen.models import Article

    info = _task_or_404(task_id)
    screener_name = user["username"]
    db = _screener_db(info, screener_name)

    # 1. 显式开启校验（插件仅在用户开启自动监控后才允许被调用）
    raw = tasks_mod._read_task_json(info.dir)  # noqa: SLF001  与 update_task 同一条读路径
    auto = (raw or {}).get("living_review_auto")
    if not isinstance(auto, dict) or not auto.get("enabled"):
        raise _bad("自动监控未开启：请先开启自动监控（enable-auto）。", 409)

    # 2. 取 PubMed 检索式与起始日期（检索式记忆）
    state = _read_living_review_state(info)
    pubmed_records = [row for row in state["search_records"]
                      if row.get("database") == "pubmed"
                      and str(row.get("query_string") or "").strip()]
    if not pubmed_records:
        raise _bad("尚未保存 PubMed 检索式：请先保存检索式（save-search）。", 400)
    record = pubmed_records[-1]
    query = str(record["query_string"]).strip()
    since = str(record.get("last_run_date") or "").strip() or (info.created_at or "")[:10]

    # 3. 调用可选插件（延迟导入——main.py 默认导入不含该模块）
    from custom_backend import living_review_plugin

    try:
        fetched = living_review_plugin.fetch_pubmed_updates(query, since)
    except living_review_plugin.PubMedPluginError as exc:
        logger.warning("living-review check-now degraded: %s", exc)
        return {
            "ok": False,
            "degraded": True,
            "message": "网络不可达，请手动导出文件后导入。",
            "error": str(exc),
        }

    # 4. 拉回的文献走增量管线（与手动导入同一条路径；结局层缺省时跳过 Meta 对比）
    comparison = (body.comparison if body else "").strip()
    outcome = (body.outcome if body else "").strip()
    timepoint = (body.timepoint if body else "").strip()
    measure = (body.measure if body else "").strip()
    if measure:
        _lr_validate_measure(measure)
    articles = [
        Article(
            zotero_key=str(row["zotero_key"]),
            item_type="JOUR",
            title=str(row.get("title") or ""),
            authors=str(row.get("authors") or ""),
            journal=str(row.get("journal") or ""),
            year=row.get("year"),
            doi=str(row.get("doi") or ""),
            abstract=str(row.get("abstract") or ""),
            url=str(row.get("url") or ""),
            source_format=str(row.get("source_format") or "pubmed"),
            raw={"pmid": row.get("pmid", ""), "source": "pubmed-check-now"},
        )
        for row in fetched
    ]
    try:
        if articles:
            result = lr_mod.process_incremental_articles(
                str(db), articles, comparison, outcome, timepoint, measure,
                al_ranker=_lr_ranker_for_task(info, screener_name),
            )
        else:
            result = lr_mod.IncrementalResult(
                change_report=f"PubMed 无新增文献（检索式自 {since or '开始'} 起）",
                al_available=False, al_note="无新增文献",
            )
    except ValueError as exc:
        raise _bad(str(exc), 400) from exc

    entry = _lr_history_entry(result, source="pubmed",
                              label=f"PubMed（自 {since or '开始'}）",
                              comparison=comparison, outcome=outcome,
                              timepoint=timepoint, measure=measure)
    today = datetime.now().date().isoformat()

    def _apply(state: dict) -> None:
        state["history"].append(entry)
        for row in state["search_records"]:
            if (row.get("database") == "pubmed"
                    and row.get("query_string") == record["query_string"]):
                row["last_run_date"] = today
                row["total_hits_last"] = len(fetched)

    _mutate_living_review_state(info, _apply)
    tasks_mod.touch_task(info.task_id, DATA_DIR, throttle=0)
    return {"ok": True, "degraded": False, "fetched": len(fetched),
            **result.to_dict(), "history_entry": entry}


# ---- Batch W12 endpoints (additive) ----
# 综述撰写器（Manuscript Builder，衍生产品规格 §4）：从本任务已积累的数据源
# （W5 方案 / 提取效应量 / RoB / W7 GRADE / 筛选流程计数 / 文献计量）按
# MANUSCRIPT_SECTIONS 模板逐节生成 PRISMA 2020 合规的综述稿件框架
# （Word / LaTeX / Markdown）。本段为纯追加，不修改上方任何既有行；核心计算
# 在 coscreen/manuscript_builder.py（纯计算、零副作用、不联网，规则驱动非
# LLM）。Discussion/Limitations/Conclusions 三节仅留占位（需作者手动撰写）。
# 合成 model/ci_method 是用户显式请求参数（默认 random/normal），并在稿件
# meta 与方法学段落中透明记录——不静默切换用户分析口径（约束 #4）。

class ManuscriptIn(BaseModel):
    """稿件生成请求（规格 §4.3：format/sections/include_figures/reference_style）。

    model/ci_method 为附加可选参数：生成稿件时对各效应量层重新合成所需
    （与 /analysis/grade/auto-suggest 的 model 查询参数同一做法，显式可覆盖、
    默认 random/normal，并在输出 meta 与方法学段落中如实记录）。
    """
    format: Literal["docx", "latex", "markdown"] = "docx"
    sections: list[str] = Field(default_factory=lambda: ["all"], max_length=25)
    include_figures: bool = True
    reference_style: Literal["ama", "vancouver", "apa"] = "ama"
    model: Literal["fixed", "random", "random_pm", "random_reml"] = "random"
    ci_method: Literal["normal", "hksj"] = "normal"


def _manuscript_ref_dict(article) -> dict:
    """Article -> 参考文献条目 dict（卷/期/页从 raw 尽力提取，缺则留空不编造）。"""
    raw = article.raw if isinstance(article.raw, dict) else {}

    def first(keys: tuple[str, ...]) -> str:
        for key in keys:
            value = raw.get(key)
            if isinstance(value, (list, tuple)):
                value = next((item for item in value if str(item).strip()), "")
            text = str(value or "").strip()
            if text:
                return text
        return ""

    start = first(("pages", "start_page", "first_page", "SP"))
    end = first(("end_page", "EP"))
    pages = f"{start}-{end}" if start and end else start
    return {
        "zotero_key": article.zotero_key,
        "title": article.title or "",
        "authors": article.authors or "",
        "journal": article.journal or "",
        "year": article.year,
        "doi": article.doi or "",
        "volume": first(("volume", "VL")),
        "issue": first(("number", "issue", "IS")),
        "pages": pages,
    }


def _collect_manuscript_data(info: tasks_mod.TaskInfo, user: dict,
                             model: str, ci_method: str) -> dict:
    """汇总稿件全部数据源（纯读取：方案/文献/决策/效应量/RoB/GRADE/文献计量）。"""
    from contextlib import closing

    from coscreen import __version__

    raw_task = tasks_mod._read_task_json(info.dir) or {}  # noqa: SLF001  与 update_task 同一条读路径
    protocol = raw_task.get("protocol")
    db = _screener_db(info, user["username"])

    # --- 文献与 PRISMA 流程计数（当前筛选员库口径；含重复条目=检索识别数）---
    all_articles = db_mod.list_articles(db, include_duplicates=True)
    unique_articles = db_mod.list_articles(db, include_duplicates=False)
    stage1 = db_mod.get_progress(db)
    stage2 = get_stage2_progress(db)
    prisma = {
        "identified": len(all_articles),
        "duplicates_removed": len(all_articles) - len(unique_articles),
        "records_screened": stage1["screened"],
        "records_excluded": stage1["exclude"],
        "records_included": (stage2["include"] if stage2["screened"] else stage1["include"]),
        "stage2_assessed": stage2["screened"],
        "stage2_included": stage2["include"],
    }

    # --- 提取与合成（已选定效应量按比较/结局/时间点分组，逐层重新合成）---
    effects = analysis_mod.list_effects(db)
    selected = [row for row in effects if row["selected"]]
    strata: dict[tuple[str, str, str], list[dict]] = {}
    for row in selected:
        strata.setdefault((row["comparison"], row["outcome"], row["timepoint"]), []).append(row)
    study_labels = {study["id"]: study["label"] for study in analysis_mod.list_studies(db)}
    syntheses = []
    for (comparison, outcome, timepoint), rows in sorted(strata.items()):
        measure = rows[0]["measure"]
        entry = {"comparison": comparison, "outcome": outcome, "timepoint": timepoint,
                 "measure": measure, "model": model, "ci_method": ci_method,
                 "result": None, "error": None, "egger": None, "egger_note": None}
        try:
            result = analysis_mod.synthesize(db, comparison, outcome, timepoint,
                                             measure, model, ci_method)
            for effect in result["effects"]:  # 森林图用研究标签而非裸 id
                effect["study_label"] = study_labels.get(effect["study_id"],
                                                         effect["study_id"])
            entry["result"] = result
        except ValueError as exc:  # 层内不可合成（<2 项研究/混池等）→ 如实记为 error
            entry["error"] = str(exc)
        try:  # Egger：<10 项研究或单组指标不可用 → note 说明，发表偏倚节如实呈现
            entry["egger"] = advanced_mod.small_study_effects(
                db, comparison, outcome, timepoint, measure)
        except ValueError as exc:
            entry["egger_note"] = str(exc)
        syntheses.append(entry)
    extraction = {
        "n_studies": len({row["study_id"] for row in selected}),
        "n_effects": len(effects),
        "measures": sorted({row["measure"] for row in selected}),
        "entry_methods": sorted({row["entry_method"] for row in selected
                                 if row.get("entry_method")}),
    }

    # --- RoB（每研究取最新一条 overall，与 GRADE auto-suggest 同一聚合口径）---
    rob_rows = analysis_mod.list_rob(db)
    latest_rob: dict[str, dict] = {}
    for row in sorted(rob_rows, key=lambda item: item["updated_at"]):
        latest_rob[row["study_id"]] = row
    overall_counts: dict[str, int] = {}
    for row in latest_rob.values():
        overall_counts[row["overall"]] = overall_counts.get(row["overall"], 0) + 1
    frameworks: dict[str, int] = {}
    for row in rob_rows:
        frameworks[row["framework"]] = frameworks.get(row["framework"], 0) + 1
    rob = ({"n_assessments": len(rob_rows), "frameworks": frameworks,
            "overall_counts": overall_counts} if rob_rows else None)

    # --- GRADE（读 W7 的 grade_assessments 表，复用 build_sof_table 组表）---
    from coscreen import grade_assessor
    grade = None
    with closing(db_mod._connect(db)) as conn:  # noqa: SLF001  与 W7 段同一连接工厂
        stored = conn.execute(
            "SELECT comparison,outcome,timepoint,rob_downgrade,inconsistency_downgrade,"
            "indirectness_downgrade,imprecision_downgrade,publication_bias_downgrade,"
            "large_effect_upgrade,dose_response_upgrade,plausible_confounding_upgrade,"
            "signal_sources,override_notes FROM grade_assessments WHERE task_id=? "
            "ORDER BY outcome,timepoint,comparison,assessor", (info.task_id,)).fetchall()
    if stored:
        assessments = []
        for row in stored:
            record = dict(zip(("comparison", "outcome", "timepoint",
                               "rob_downgrade", "inconsistency_downgrade",
                               "indirectness_downgrade", "imprecision_downgrade",
                               "publication_bias_downgrade", "large_effect_upgrade",
                               "dose_response_upgrade", "plausible_confounding_upgrade",
                               "signal_sources", "override_notes"), row))
            try:
                signals = json.loads(record.get("signal_sources") or "{}")
            except ValueError:
                signals = {}
            if not isinstance(signals, dict):
                signals = {}
            try:
                n_studies = len(analysis_mod.selected_effects(
                    db, record["comparison"], record["outcome"], record["timepoint"]))
            except ValueError:
                n_studies = None
            assessments.append({
                "comparison": record["comparison"], "outcome": record["outcome"],
                "timepoint": record["timepoint"],
                "initial_design": signals.get("initial_design", "rct"),
                "downgrades": {"rob": record["rob_downgrade"],
                               "inconsistency": record["inconsistency_downgrade"],
                               "indirectness": record["indirectness_downgrade"],
                               "imprecision": record["imprecision_downgrade"],
                               "publication_bias": record["publication_bias_downgrade"]},
                "upgrades": {"large_effect": record["large_effect_upgrade"],
                             "dose_response": record["dose_response_upgrade"],
                             "plausible_confounding": record["plausible_confounding_upgrade"]},
                "final_quality": signals.get("final_quality"),
                "n_studies": n_studies,
                "override_notes": record["override_notes"],
            })
        try:
            sof = grade_assessor.build_sof_table(assessments)
        except ValueError:
            sof = None
        grade = {"n_assessments": len(assessments), "sof": sof}

    return {
        "task": {"task_id": info.task_id, "display_name": info.display_name},
        "protocol": protocol if isinstance(protocol, dict) else None,
        "prisma": prisma,
        "articles": [_manuscript_ref_dict(a) for a in unique_articles],
        "extraction": extraction,
        "rob": rob,
        "syntheses": syntheses,
        "grade": grade,
        "software": {"name": "ReviewFlow", "version": __version__},
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }


def _build_manuscript_or_400(data: dict, sections: list[str], include_figures: bool,
                             reference_style: str) -> dict:
    from coscreen import manuscript_builder

    try:
        return manuscript_builder.build_manuscript(
            data, sections=sections, include_figures=include_figures,
            reference_style=reference_style)
    except manuscript_builder.ManuscriptError as exc:
        raise _bad(exc, 400) from exc


_MANUSCRIPT_MEDIA = {
    "docx": ("application/vnd.openxmlformats-officedocument."
             "wordprocessingml.document", "docx"),
    "latex": ("application/x-tex; charset=utf-8", "tex"),
    "markdown": ("text/markdown; charset=utf-8", "md"),
}


@app.post("/api/tasks/{task_id}/analysis/manuscript/generate")
def manuscript_generate(task_id: str, body: ManuscriptIn,
                        user: dict = Depends(require_user)) -> Response:
    """生成稿件框架（Word/LaTeX/Markdown，规则驱动非 LLM，纯本地计算不联网）。"""
    from coscreen import manuscript_builder

    if body.model == "fixed" and body.ci_method == "hksj":
        raise _bad("固定效应模型不支持 HKSJ 置信区间（与 /analysis/synthesis 同一约束）。", 400)
    info = _task_or_404(task_id)
    data = _collect_manuscript_data(info, user, body.model, body.ci_method)
    manuscript = _build_manuscript_or_400(data, body.sections, body.include_figures,
                                          body.reference_style)
    media_type, suffix = _MANUSCRIPT_MEDIA[body.format]
    if body.format == "docx":
        payload = manuscript_builder.render_docx(manuscript)
    elif body.format == "latex":
        payload = manuscript_builder.render_latex(manuscript).encode("utf-8")
    else:
        payload = manuscript_builder.render_markdown(manuscript).encode("utf-8")
    return _file_response(payload, media_type, f"{task_id}-manuscript.{suffix}")


@app.get("/api/tasks/{task_id}/analysis/manuscript/preview")
def manuscript_preview(task_id: str,
                       reference_style: str = Query(
                           default="ama", pattern="^(ama|vancouver|apa)$"),
                       model: str = Query(
                           default="random", pattern="^(fixed|random|random_pm|random_reml)$"),
                       ci_method: str = Query(
                           default="normal", pattern="^(normal|hksj)$"),
                       user: dict = Depends(require_user)) -> dict:
    """返回 Markdown 预览（全部章节；图表内联为 data-URI SVG，前端直接渲染）。"""
    from coscreen import manuscript_builder

    if model == "fixed" and ci_method == "hksj":
        raise _bad("固定效应模型不支持 HKSJ 置信区间（与 /analysis/synthesis 同一约束）。", 400)
    info = _task_or_404(task_id)
    data = _collect_manuscript_data(info, user, model, ci_method)
    manuscript = _build_manuscript_or_400(data, ["all"], True, reference_style)
    return {
        "markdown": manuscript_builder.render_markdown(manuscript),
        "meta": manuscript["meta"],
        "sections": [{"key": section["key"], "title": section["title"],
                      "source": section["source"],
                      "placeholder": section["placeholder"]}
                     for section in manuscript["sections"]],
    }
