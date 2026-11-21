"""全文规范归档导出与任务整包导出/导入（2.1.5 Wave G3 · W5）。

两个出口共用 ``coscreen.fulltext_archive.archive_filename``（单一事实源）：

* ``POST /api/tasks/{id}/fulltext/archive-export``
  只导出 PDF 的 zip；条目名 = ``{key}-{一作姓氏}-{年份}.pdf``；
  ``scope`` 三选一（all / stage1_include / stage2_include），纳入口径 =
  **当前生效决定**（见 ``_effective_decisions``）；响应头带成功/跳过摘要。
  零全文 = 合法空 zip（不报错、不臆造）。总量上限与整包导出对齐
  （``MAX_ARCHIVE_EXPORT_BYTES``=2GB；终审 #44）：超限结构化 413
  ``archive_too_large``，且在写第一个文件字节之前拒绝。
* ``POST /api/tasks/{id}/package/export``
  任务整包 zip：task.json（脱敏重写）+ 各 ``{slug}.db`` 一致性快照 +
  collaboration.db（属主改写）+ import_history.sqlite3 + 可选全文
  （规范命名 + manifest 原名↔规范名映射）。单包上限 2GB，超限 413 并
  建议排除 PDF。
* ``GET  /api/tasks/{id}/package/preflight``
  导出前体检（成员/全文量/预计体积/是否超限），不生成 zip。
* ``POST /api/tasks/import-package/preflight``
  上传整包做完整校验并回 manifest 预览（零写入）。
* ``POST /api/tasks/import-package``
  上传 → 校验 → 在任务根下的临时目录原子落盘 → 整体改名为新任务目录；
  导入者为新属主；slug 冲突加 ``-imported`` 后缀并记录映射；PDF 按
  manifest 映射还原到内部安全命名存储（``save_pdf`` 单射命名）。

权限：两个导出端点要求任务属主（``can_manage`` 同义）；desktop 模式
legacy 任务（无属主记录）本人操作放行，server 模式 legacy 任务 409
``legacy_owner_unset``（与协作/检索式管理同口径）。导入端点只要求登录：
任何账号在任何部署上导入都成为新任务属主。

安全与降级：全部 SQL 为固定字面量 + ``?`` 参数绑定；zip 成员名白名单、
成员 sha256/尺寸校验、解压总量与压缩比上限、拒绝加密成员与重复成员；
落盘目标一律由服务器端生成/校验（成员名只做白名单匹配，绝不直接拼路径）；
锁忙/IO 故障 → 503；损坏包/校验不符 → 400/409；绝不 500。

终审 #41 加固（用户 2026-11-09 裁定）：

* **流式解压 + 三限额**：逐成员 ``ZipFile.open`` 流式写出，杜绝
  ``archive.read`` 整块入内存；单成员 100MB / 解压总量 2GB / 压缩比 2000x
  在声明值与实际字节两道各查一次，超限结构化 400/413 并清场。
* **60 分钟临时保留上限**：上传 zip、解压暂存、导出/归档/体检工作目录都是
  分钟级路过文件——操作结束（成功/失败/客户端中断下载）即刻删除；每次触达
  任一整包端点惰性清扫超过 ``PACKAGE_TEMP_TTL_SECONDS`` 的全部前缀残留。
  清扫只在确认无整包操作在途时进行（2.1.5-sec29：操作锁被持有时整体跳过
  ——mtime 不是在途证据，慢客户端下载在途的目录不得只凭 mtime 被 rmtree）。
* **全局整包操作互斥**：导入/导入体检（import-package/preflight）/整包导出
  /导出体检（package/preflight）/归档导出共用一把进程级锁（2.1.5-sec29 起
  两个 preflight 一并纳入——体检快照与上传暂存同样是 2GB 级磁盘占用，不纳
  入即绕过「全局互斥把磁盘峰值封死」的承诺），同一时刻只允许一个整包操作
  在途，第二个请求 409 ``task_package.operation_busy``。**如实的峰值口径**
  （sec29b-W1 修正）：互斥封顶的是**端点内**段——上传副本 + 解压 ≤4GB
  瞬态 + 单次体检快照；multipart **请求体 spool 发生在端点之前的解析层、
  在互斥之外**，随并发线性增长（N 个并发上传 = N×2GB 预检即落盘），
  sec29b-W1 起由上传在途并发闸（ASGI 层、解析前，上限 4，超出 503 结构化
  ``upload_busy``）另行封顶 spool 峰值。导出锁在下载送完或客户端中断时
  释放（下载在途也算操作在途；sec29b-W1 起下载流为异步生成器 + 盾化
  finally，uvicorn 实况的取消分支也确定释放）。
* **协作库改绑失败即坏包**（2.1.5-sec29 发现 3，对齐 fulltexts 对抗口径）：
  包内 ``collaboration.db`` 改绑失败（缺表/缺 singleton 行/挂触发器或视图
  /列不全）= 该包无效——预检与导入同口径结构化 400
  ``package_invalid{collaboration_rewrite}``，绝不静默 unlink 换空库假 200
  （那会丢包内判定/设置且无迹可查）。
* 上传 zip 落盘照旧（受 2GB 上传上限），但解压完成（成功或失败）立即删除。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import sqlite3
import tempfile
import threading
import time
import zipfile
from collections.abc import Callable, Iterable, Mapping
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Literal
from urllib.parse import quote

import anyio
from fastapi import Body, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict
from starlette.concurrency import run_in_threadpool

from coscreen import fulltext as fulltext_mod
from coscreen import sof_judgments as sof_judgments_mod
from coscreen import task_collaboration
from coscreen import tasks as tasks_mod
from coscreen.fulltext_archive import archive_filename
from coscreen.screening_arbitration import latest_decision_arbitration

__all__ = [
    "MAX_ARCHIVE_EXPORT_BYTES",
    "MAX_PACKAGE_BYTES",
    "MAX_MEMBER_BYTES",
    "MAX_UNCOMPRESSED_BYTES",
    "PACKAGE_SCHEMA",
    "PACKAGE_TEMP_TTL_SECONDS",
    "TaskPackageError",
    "register_task_package_routes",
    "validate_import_package",
]

logger = logging.getLogger(__name__)

PACKAGE_SCHEMA = "reviewflow.task-package/1"
#: 整包单 zip 上限（设计 §二.B.4 / task_package v1.0 §三）。
MAX_PACKAGE_BYTES = 2 * 1024 * 1024 * 1024
#: 解压总量上限（终审 #41 收紧为与单包 2GB 同额）：zip 在解压完成前仍在盘，
#: 峰值 = zip + 解压内容 ≤ 2GB + 2GB = 4GB 瞬态（用户 2026-11-09 裁定的
#: 空间承诺）；导出侧 total_bytes 同样受 2GB 上限约束，往返不会撞差异。
MAX_UNCOMPRESSED_BYTES = MAX_PACKAGE_BYTES
#: 单个解压成员实际上限（终审 #41）：db/协作库/历史库/PDF 一律 100MB——
#: 与单 PDF 上限同口径；恶意/超大会员在校验与解压两阶段按声明值+实际字节
#: 双重拦截（写盘数 MB 内即结构化 413/400 并清场）。
MAX_MEMBER_BYTES = 100 * 1024 * 1024
#: 成员数上限（正常任务远低于此）。
MAX_MEMBERS = 20_000
#: 解压/压缩比上限（沿 research_trace_export 纪律；终审 #41 起逐成员声明值
#: 与实际字节各查一次，垫片抬高总压缩尺寸不再能掩护单个炸弹成员）。
MAX_COMPRESSION_RATIO = 2_000
#: manifest.json 上限。
MAX_MANIFEST_BYTES = 8 * 1024 * 1024
#: task.sanitized.json 独立上限（终审 #41：preflight 阶段曾整读无上限）。
MAX_TASK_JSON_BYTES = 8 * 1024 * 1024
#: 单个 PDF 上限与既有上传端点一致（100MB）。
MAX_PDF_BYTES = 100 * 1024 * 1024
#: 归档导出（archive-export）zip 总量上限（终审 #44，用户 2026-11-09 裁定
#: 「加上限」）：与整包导出同一常量口径（MAX_PACKAGE_BYTES=2GB）——归档 zip
#: 在系统临时目录生成，超限结构化 413 ``task_package.archive_too_large``，
#: 且在写第一个文件字节之前拒绝（预检提前提示见 _package_inventory）。
MAX_ARCHIVE_EXPORT_BYTES = MAX_PACKAGE_BYTES
#: 整包临时文件保留上限（用户裁定 60 分钟；导出/归档/上传/预检/导入的
#: 一切临时残留惰性清扫兜底）。
PACKAGE_TEMP_TTL_SECONDS = 3600
#: 上传读取余量（multipart 开销）。
_UPLOAD_SLACK_BYTES = 1024 * 1024
#: 流式读写块大小。
_CHUNK = 1024 * 1024
#: 导入临时目录前缀（位于 tasks_root 内，保证最终 os.replace 同盘原子）。
_IMPORT_TMP_PREFIX = ".import-"
#: 导出/归档/体检工作目录与上传暂存文件前缀（系统临时目录内；TTL 清扫对象
#: ——目录与文件一律按 mtime 清扫，前缀共用一个元组）。
_PACKAGE_TEMP_DIR_PREFIXES = ("rf-package-export-", "rf-archive-export-",
                              "rf-package-preflight-", "rf-package-upload-")
_PACKAGE_UPLOAD_PREFIX = "rf-package-upload-"
#: 固定 zip 时间戳：导出副本不带本机 mtime（减少环境信息、便于比对）。
_ZIP_DATE = (1980, 1, 1, 0, 0, 0)

#: 全局整包操作互斥（导入/导入体检/整包导出/导出体检/归档导出共用一把锁，
#: 用户 2026-11-09 裁定；2.1.5-sec29 起两个 preflight 一并纳入）：同一时刻
#: 全服务器（进程内）只允许一个整包操作在途。**如实口径**（sec29b-W1 修正）：
#: 互斥封顶的是端点内段——上传副本 2GB + 解压 2GB 的 ≤4GB 瞬态与体检快照；
#: multipart 请求体 spool 在端点之前的解析层发生、在互斥**之外**，随并发
#: 线性（修复前实测：互斥被占时并发上传仍各自预检落盘）——由
#: :class:`_PackageUploadInflightGate`（上限 4）封顶 spool 峰值，而不是本锁。
#: 第二个请求结构化 409 ``task_package.operation_busy``。导出侧锁在下载完成
#: /客户端中断（异步生成器的盾化 finally）时释放，避免「下载中 + 新导入」
#: 的 6GB 叠加。
_PACKAGE_OP_LOCK = threading.Lock()

#: 整包上传在途并发上限（sec29b-W1·发现 4）：两个上传端点的互斥锁在
#: **端点内**才抢，而 Starlette/FastAPI 在端点运行前已完成 multipart 解析
#: ——>1MB 的文件 part 已先落盘 spool（复审 N 实测：互斥被占时 4 个并发
#: 上传仍各自 spool 满 3MiB 后才 409），「磁盘峰值恒定 ≤4GB」的承诺被
#: 这段互斥之外的预端点 spool 绕过（随并发线性）。本闸在 ASGI 层（路由/
#: 解析之前）对两个上传路径计在途请求数：达上限即刻 503 结构化
#: ``task_package.upload_busy``（不消费请求体、不 spool），把 spool 峰值
#: 一并封顶（≤ 上限 × 2GB）。
_PACKAGE_UPLOAD_INFLIGHT_LIMIT = 4
#: 受闸路径（与 upload_rules 追加规则同一组正则，口径一致）。
_PACKAGE_UPLOAD_PATH_RULES = (re.compile(r"^/api/tasks/import-package$"),
                              re.compile(r"^/api/tasks/import-package/preflight$"))


class _PackageUploadInflightGate:
    """整包上传在途并发闸（纯 ASGI 中间件，sec29b-W1·发现 4）。

    只拦两个整包上传路径的 POST：请求进入路由/multipart 解析**之前**计数
    +1、请求结束（正常完成或任何取消/异常）finally -1；在途数已达上限时
    直接应答 503（结构化 ``upload_busy``，与文件内既有 409/503 结构化错误
    同形：``{"detail":{error_code,message_key,params}}``），不读请求体——
    因此**不会 spool**，这正是本闸存在的目的（互斥锁挡不住预端点 spool）。
    其余路径原样透传。计数是**进程内**计数（多 worker 部署各 worker 独立
    计数——与进程级互斥锁同一粒度）；增减在一把小锁内完成、绝不跨 await
    持有，同一进程内多事件循环/多线程共用本实例时计数仍然精确。未做 CORS
    头补齐：闸触发是罕见的服务端忙信号，同源部署（本产品常态）不受影响；
    跨域错误可读性不构成本闸的安全属性。
    """

    def __init__(self, app, limit: int = _PACKAGE_UPLOAD_INFLIGHT_LIMIT) -> None:  # noqa: ANN001
        self.app = app
        self.limit = limit
        self._inflight = 0
        self._guard = threading.Lock()

    async def __call__(self, scope, receive, send) -> None:  # noqa: ANN001
        if (scope.get("type") != "http" or scope.get("method") != "POST"
                or not any(rule.match(scope.get("path", ""))
                           for rule in _PACKAGE_UPLOAD_PATH_RULES)):
            await self.app(scope, receive, send)
            return
        with self._guard:
            admitted = self._inflight < self.limit
            if admitted:
                self._inflight += 1
        if not admitted:
            body = json.dumps({"detail": {
                "error_code": "upload_busy",
                "message_key": "task_package.upload_busy",
                "params": {"inflight_limit": self.limit},
            }}, ensure_ascii=False).encode("utf-8")
            await send({
                "type": "http.response.start", "status": 503,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                    (b"cache-control", b"no-store"),
                    (b"retry-after", b"1"),
                    # 早拒不消费请求体：关闭连接，避免服务端继续吸收数据
                    # （与 security.UploadSizeLimitMiddleware 同做法）。
                    (b"connection", b"close"),
                ],
            })
            await send({"type": "http.response.body", "body": body})
            return
        try:
            await self.app(scope, receive, send)
        finally:
            with self._guard:
                self._inflight -= 1


def _acquire_package_op_lock() -> None:
    """非阻塞抢占整包操作锁；被占即结构化 409（绝不排队放大占用）。"""
    if not _PACKAGE_OP_LOCK.acquire(blocking=False):
        raise TaskPackageError("operation_busy", status=409)


def _acquire_package_op_lock_or_409() -> None:
    """端点用的抢锁缝：抢不到直接映射结构化 409（HTTPException）。"""
    try:
        _acquire_package_op_lock()
    except TaskPackageError as exc:
        raise _error(exc) from exc


def _sweep_stale_package_temp(tasks_root: Path | None = None, *,
                              ttl: int = PACKAGE_TEMP_TTL_SECONDS,
                              now: float | None = None) -> int:
    """惰性清扫：删除修改时间超过 TTL 的整包临时残留（进程崩溃孤儿兜底）。

    覆盖系统临时目录里的导出/归档/体检工作目录与上传暂存文件，以及
    ``tasks_root`` 内的 ``.import-*`` 解压暂存目录。60 分钟内在途文件不误删
    （目录 mtime 随内部写文件刷新）。清扫是尽力而为：任何 OSError 静默跳过，
    绝不让清扫失败变成端点 500。返回删除条目数。

    2.1.5-sec29（发现 2）：mtime 不是在途证据——慢客户端下载/超大包导出可让
    在途工作目录的 mtime 超过 TTL 仍在被操作使用。任一整包操作在途（操作锁
    被持有，含下载在途）时**整体跳过**本轮清扫；清扫只发生在确认无整包操作
    在途时（各端点均在抢锁之前触达本函数，不存在「自己持锁自己跳过」）。
    """
    if _PACKAGE_OP_LOCK.locked():
        return 0
    cutoff = (time.time() if now is None else now) - ttl
    removed = 0
    roots: list[tuple[Path, tuple[str, ...]]] = [
        (Path(tempfile.gettempdir()), _PACKAGE_TEMP_DIR_PREFIXES),
    ]
    if tasks_root is not None:
        roots.append((Path(tasks_root), (_IMPORT_TMP_PREFIX,)))
    for root, prefixes in roots:
        try:
            entries = list(root.iterdir())
        except OSError:
            continue
        for entry in entries:
            if not entry.name.startswith(prefixes):
                continue
            try:
                if entry.stat().st_mtime >= cutoff:
                    continue
                if entry.is_dir():
                    shutil.rmtree(entry, ignore_errors=True)
                else:
                    entry.unlink(missing_ok=True)
            except OSError:
                continue
            removed += 1
    return removed

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SLUG_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
_TASK_ID_KEY_RE = re.compile(r"^[^/\\\x00]{1,300}$")
_ARCHIVE_NAME_RE = re.compile(r"^[^/\\\x00]{1,255}\.pdf$")
_MEMBER_NAME_RE = re.compile(
    r"^(?:manifest\.json|task\.sanitized\.json|collaboration\.db|"
    r"import-history\.sqlite3|db/[A-Za-z0-9._-]{1,100}\.db|"
    r"fulltext/[^/\\\x00]{1,255})$"
)
_JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")

#: task.json 脱敏：凭据/密钥/会话类键（大小写不敏感）。
_SENSITIVE_KEY_RE = re.compile(
    r"(pass(word|wd)?|secret|token|jwt|api[_-]?key|access[_-]?key|"
    r"private[_-]?key|credential|authorization|cookie|session|salt)",
    re.I,
)
#: 环境/身份类键：导出的 task.json 不应携带源机器或旧属主信息。
_ENV_KEY_RE = re.compile(
    r"(^|_)(env|environment|home|userprofile|hostname|host|cwd|"
    r"owner|owner_user_id|owner_username|created_by|created_by_user_id)(_|$)",
    re.I,
)
#: task.json 的固定字段：导入时由目标任务重写。
_RESERVED_TASK_KEYS = frozenset({
    "task_id", "display_name", "description", "screeners", "created_at",
    "last_used_at", "archived",
})


class TaskPackageError(Exception):
    """结构化失败（HTTP 层映射 ``{error_code, message_key, params}``）。"""

    def __init__(self, code: str, *, status: int = 400,
                 params: Mapping[str, Any] | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.status = status
        self.params = dict(params or {})


class ArchiveExportIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope: Literal["all", "stage1_include", "stage2_include"] = "all"


class PackageExportIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    include_pdfs: bool = True


def _error(exc: TaskPackageError) -> HTTPException:
    return HTTPException(exc.status, {
        "error_code": exc.code,
        "message_key": "task_package." + exc.code,
        "params": exc.params,
    })


async def _run_or_error(func: Callable[..., Any], *args: Any) -> Any:
    """线程池执行并把 TaskPackageError 映射为结构化 HTTPException。"""
    try:
        return await run_in_threadpool(func, *args)
    except TaskPackageError as exc:
        raise _error(exc) from exc


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _local_now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# 路径安全：所有落盘/读盘路径在访问前就地校验
# ---------------------------------------------------------------------------

def _contained(base: Path, *parts: str) -> Path:
    """``base`` 内的确定性路径分量拼接；越界（含 ..）一律结构化拒绝。"""
    root = Path(base).resolve()
    candidate = root.joinpath(*parts).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        raise TaskPackageError("package_invalid",
                               params={"reason": "member_name_invalid"}) from None
    return candidate


def _read_only_uri(path: Path) -> str:
    # quote：路径含 %/?/# 时未编码会被 SQLite URI 语法吃掉（沿 tasks.iter_fulltexts）。
    return f"file:{quote(str(Path(path).resolve()))}?mode=ro"


def _open_read_only(path: Path) -> sqlite3.Connection:
    """只读连接（锁忙映射 503；不可解析的库由调用方降级）。"""
    try:
        return sqlite3.connect(_read_only_uri(path), uri=True, timeout=15)
    except sqlite3.OperationalError as exc:
        if "locked" in str(exc).lower() or "busy" in str(exc).lower():
            raise TaskPackageError("storage_unavailable", status=503) from exc
        raise


def _open_rw(base: Path, name: str) -> sqlite3.Connection:
    """``base`` 内已校验目标的读写连接（目标由 :func:`_contained` 生成）。"""
    return sqlite3.connect(str(_contained(base, name)), timeout=15)


def _stage1_rows(path: Path) -> list[tuple]:
    try:
        with closing(_open_read_only(path)) as conn:
            return conn.execute("SELECT zotero_key, decision FROM decisions").fetchall()
    except sqlite3.OperationalError as exc:
        # 锁忙/读写冲突 = 暂时不可读（503 重试语义），绝不能当成「无决策/
        # 无全文」让 scope 静默缩小；缺表等其余 Operational 错误按空表降级。
        if "locked" in str(exc).lower() or "busy" in str(exc).lower():
            raise TaskPackageError("storage_unavailable", status=503) from exc
        return []
    except sqlite3.DatabaseError:
        return []
    except OSError as exc:
        raise TaskPackageError("storage_unavailable", status=503) from exc


def _stage2_rows(path: Path) -> list[tuple]:
    try:
        with closing(_open_read_only(path)) as conn:
            return conn.execute(
                "SELECT zotero_key, decision FROM stage2_decisions").fetchall()
    except sqlite3.OperationalError as exc:
        # 锁忙/读写冲突 = 暂时不可读（503 重试语义），绝不能当成「无决策/
        # 无全文」让 scope 静默缩小；缺表等其余 Operational 错误按空表降级。
        if "locked" in str(exc).lower() or "busy" in str(exc).lower():
            raise TaskPackageError("storage_unavailable", status=503) from exc
        return []
    except sqlite3.DatabaseError:
        return []
    except OSError as exc:
        raise TaskPackageError("storage_unavailable", status=503) from exc


def _article_rows(path: Path) -> list[tuple]:
    try:
        with closing(_open_read_only(path)) as conn:
            return conn.execute("SELECT zotero_key, authors, year FROM articles").fetchall()
    except sqlite3.OperationalError as exc:
        # 锁忙/读写冲突 = 暂时不可读（503 重试语义），绝不能当成「无决策/
        # 无全文」让 scope 静默缩小；缺表等其余 Operational 错误按空表降级。
        if "locked" in str(exc).lower() or "busy" in str(exc).lower():
            raise TaskPackageError("storage_unavailable", status=503) from exc
        return []
    except sqlite3.DatabaseError:
        return []
    except OSError as exc:
        raise TaskPackageError("storage_unavailable", status=503) from exc


def _fulltext_rows(path: Path) -> list[tuple]:
    try:
        with closing(_open_read_only(path)) as conn:
            return conn.execute(
                "SELECT zotero_key, filename, path, sha256 FROM fulltexts").fetchall()
    except sqlite3.OperationalError as exc:
        # 锁忙/读写冲突 = 暂时不可读（503 重试语义），绝不能当成「无决策/
        # 无全文」让 scope 静默缩小；缺表等其余 Operational 错误按空表降级。
        if "locked" in str(exc).lower() or "busy" in str(exc).lower():
            raise TaskPackageError("storage_unavailable", status=503) from exc
        return []
    except sqlite3.DatabaseError:
        return []
    except OSError as exc:
        raise TaskPackageError("storage_unavailable", status=503) from exc


def _fulltext_key_rows(path: Path) -> list[tuple]:
    try:
        with closing(_open_read_only(path)) as conn:
            return conn.execute("SELECT zotero_key FROM fulltexts").fetchall()
    except sqlite3.OperationalError as exc:
        # 锁忙/读写冲突 = 暂时不可读（503 重试语义），绝不能当成「无决策/
        # 无全文」让 scope 静默缩小；缺表等其余 Operational 错误按空表降级。
        if "locked" in str(exc).lower() or "busy" in str(exc).lower():
            raise TaskPackageError("storage_unavailable", status=503) from exc
        return []
    except sqlite3.DatabaseError:
        return []
    except OSError as exc:
        raise TaskPackageError("storage_unavailable", status=503) from exc


def _read_stage_decisions(path: Path, stage: str) -> dict[str, str]:
    """单个筛选员库某阶段的 ``zotero_key -> decision``（只读、缺表降级为空）。"""
    rows = _stage2_rows(path) if stage == "stage2" else _stage1_rows(path)
    return {str(row[0]): str(row[1]) for row in rows if row and row[0]}


def _collaboration_store(info: Any) -> Path:
    return task_collaboration.shared_store_path(info.dir)


def _effective_decisions(info: Any, stage: str) -> dict[str, str]:
    """任务级「当前生效决定」（scope 口径的唯一实现）。

    规则（与 ``custom_backend.prisma_task_metrics.outcomes`` 的团队口径对齐，
    但只读、不写任何库）：

    1. 收集任务目录内全部筛选员个人库的该阶段决策行；**没有任何决策行的库
       不参与**（尚未开筛的成员不阻塞已开始的筛选）。
    2. 只有一个筛选员有决策 → 其决策即生效（单机/单人任务的常态）。
    3. 多个筛选员：
       - 所有筛选员都投了该条目且取值一致 → 该值生效；
       - 否则查最新仲裁快照（collaboration.db）：仅当快照的投票人集合与
         当前投票人一致、且每个投票人的决策与快照记录逐项相等时，采用
         ``final_decision``（``agreed``/``adjudicated``）；过期快照一律
         忽略（防止改票后旧仲裁继续生效）。
    4. 冲突且无有效仲裁 → 该条目不进入任何 include 范围（保守口径，
       绝不把未解决冲突当成纳入）。
    """
    votes: dict[str, dict[str, str]] = {}
    for db_file in info.db_files:
        rows = _read_stage_decisions(db_file, stage)
        if rows:
            votes[db_file.stem] = rows
    if not votes:
        return {}
    if len(votes) == 1:
        return dict(next(iter(votes.values())))

    snapshot: dict[str, Any] | None
    try:
        snapshot = latest_decision_arbitration(_collaboration_store(info), stage)
    except (sqlite3.Error, OSError):
        logger.warning("仲裁快照读取失败，冲突条目按未解决处理", exc_info=True)
        snapshot = None
    snap_rows: dict[str, dict[str, Any]] = {}
    if snapshot:
        snap_rows = {
            str(record.get("zotero_key")): record
            for record in snapshot.get("records") or []
            if isinstance(record, dict) and record.get("zotero_key")
        }

    names = set(votes)
    keys: set[str] = set()
    for rows in votes.values():
        keys.update(rows)
    effective: dict[str, str] = {}
    for key in keys:
        current = {name: votes[name][key] for name in names if key in votes[name]}
        if len(current) == len(names) and len(set(current.values())) == 1:
            effective[key] = next(iter(current.values()))
            continue
        record = snap_rows.get(key)
        if not record or record.get("arbitration_status") not in {"agreed", "adjudicated"}:
            continue
        final = record.get("final_decision")
        if not final:
            continue
        snap_names = {
            column[len("decision_"):]
            for column in record
            if column.startswith("decision_")
        }
        if snap_names != names:
            continue
        if all(str(record.get("decision_" + name, "")) == current.get(name)
               for name in names):
            effective[key] = str(final)
    return effective


def _article_meta(info: Any) -> dict[str, dict[str, Any]]:
    """``zotero_key -> {authors, year}``（跨筛选员库首个非空值胜出）。"""
    meta: dict[str, dict[str, Any]] = {}
    for db_file in info.db_files:
        for row in _article_rows(db_file):
            if not row or not row[0]:
                continue
            key = str(row[0])
            entry = meta.setdefault(key, {"authors": "", "year": None})
            if not entry["authors"] and row[1]:
                entry["authors"] = str(row[1])
            if entry["year"] in (None, 0, "") and row[2]:
                entry["year"] = row[2]
    return meta


def _fulltext_allowed_roots(info: Any) -> list[Path]:
    """fulltexts.path 的合法根：**仅任务目录**（终审 #39 r2 收窄）。

    旧口径还接受共享平铺池 ``{data_dir}/pdfs``——正是跨任务/跨用户读文件
    链路的出口。导入侧重写 + 恶意 schema 拒绝（见
    :func:`_neutralize_unrestored_fulltexts`）之外，这里再做一道与导入无关
    的纵深：指向任务目录之外的行一律按 missing_file 跳过（含存量未迁移
    平铺池行——行为变化已在契约披露）。
    """
    return [Path(info.dir).resolve()]


def _safe_fulltext_path(info: Any, data_dir: str | Path, raw: str, db_file: Path) -> Path | None:
    """解析 fulltexts.path 并做包含性校验（合法根 = 任务目录，见
    :func:`_fulltext_allowed_roots`）——被篡改/恶意导入的库行不能把任意
    本机文件读进导出包。
    """
    resolved = tasks_mod.resolve_fulltext_raw(raw, db_file, data_dir=data_dir)
    if resolved is None:
        return None
    actual = Path(resolved).resolve()
    for root in _fulltext_allowed_roots(info):
        try:
            actual.relative_to(root)
            return actual
        except ValueError:
            continue
    return None


def _fulltext_entries(info: Any, data_dir: str | Path) -> dict[str, dict[str, Any]]:
    """枚举任务内全部全文登记（按 key 去重；首个可解析且路径安全的胜出）。"""
    entries: dict[str, dict[str, Any]] = {}
    for db_file in info.db_files:
        for row in _fulltext_rows(db_file):
            if not row or not row[0]:
                continue
            key = str(row[0])
            entry = entries.setdefault(key, {
                "filename": str(row[1] or ""),
                "path": str(row[2] or ""),
                "sha256": str(row[3] or ""),
                "resolved": None,
            })
            if entry["resolved"] is None and row[2]:
                entry["resolved"] = _safe_fulltext_path(
                    info, data_dir, str(row[2]), db_file)
    return entries


def _archive_candidates(info: Any, data_dir: str | Path, scope: str
                        ) -> tuple[list[dict[str, Any]], list[str], dict[str, int]]:
    """按 scope 生成归档条目（排序确定；范围外条目不计入跳过数）。"""
    entries = _fulltext_entries(info, data_dir)
    articles = _article_meta(info)
    keys = sorted(entries)
    if scope != "all":
        stage = "stage1" if scope == "stage1_include" else "stage2"
        effective = _effective_decisions(info, stage)
        keys = [key for key in keys if effective.get(key) == "include"]

    items: list[dict[str, Any]] = []
    skipped: list[str] = []
    reasons: dict[str, int] = {"missing_file": 0, "unreadable": 0}
    taken: set[str] = set()
    allowed_roots = _fulltext_allowed_roots(info)   # 仅任务目录（终审 #39 r2）
    for key in keys:
        entry = entries[key]
        resolved = entry.get("resolved")
        if resolved is None:
            skipped.append(key)
            reasons["missing_file"] += 1
            continue
        actual = Path(resolved).resolve()
        allowed = False
        for root in allowed_roots:
            try:
                actual.relative_to(root)
                allowed = True
                break
            except ValueError:
                continue
        if not allowed or not actual.is_file():
            skipped.append(key)
            reasons["missing_file"] += 1
            continue
        try:
            with open(actual, "rb") as probe:
                probe.read(1)
        except OSError:
            skipped.append(key)
            reasons["unreadable"] += 1
            continue
        meta = articles.get(key) or {}
        name = archive_filename(
            {
                "key": key,
                "authors": meta.get("authors"),
                "year": meta.get("year"),
            },
            taken=taken,
        )
        taken.add(name)
        items.append({
            "key": key,
            "archive_name": name,
            "path": actual,
            "original_filename": entry["filename"],
            "sha256": entry["sha256"],
        })
    return items, skipped, reasons


def _zip_info(arcname: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(arcname, date_time=_ZIP_DATE)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o600 << 16
    return info


def _add_file_to_zip(archive: zipfile.ZipFile, arcname: str, path: Path,
                     *, allowed_roots: Iterable[Path]) -> tuple[str, int]:
    """把**已校验且位于允许根内**的磁盘文件流式写入 zip，返回 (sha256, size)。"""
    actual = Path(path).resolve()
    allowed = False
    for root in allowed_roots:
        try:
            actual.relative_to(Path(root).resolve())
            allowed = True
            break
        except ValueError:
            continue
    if not allowed or not actual.is_file():
        raise TaskPackageError("package_member_invalid",
                               params={"member": arcname, "reason": "path_outside_task"})
    digest = hashlib.sha256()
    size = 0
    with open(actual, "rb") as source, archive.open(_zip_info(arcname), "w") as target:
        while chunk := source.read(_CHUNK):
            digest.update(chunk)
            size += len(chunk)
            target.write(chunk)
    return digest.hexdigest(), size


# ---------------------------------------------------------------------------
# task.json 脱敏
# ---------------------------------------------------------------------------

def _sanitize_task_payload(raw: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """递归删除凭据/环境/旧属主字段与 JWT 字面量；返回 (清洗后, 删除路径列表)。"""
    redacted: list[str] = []

    def walk(value: Any, prefix: str) -> Any:
        if isinstance(value, dict):
            out: dict[str, Any] = {}
            for key, item in value.items():
                name = str(key)
                path = f"{prefix}.{name}" if prefix else name
                if _SENSITIVE_KEY_RE.search(name) or _ENV_KEY_RE.search(name):
                    redacted.append(path)
                    continue
                if isinstance(item, str) and _JWT_RE.search(item):
                    redacted.append(path)
                    continue
                out[name] = walk(item, path)
            return out
        if isinstance(value, list):
            return [walk(item, f"{prefix}[{index}]") for index, item in enumerate(value)]
        return value

    cleaned = walk(dict(raw), "")
    assert isinstance(cleaned, dict)
    return cleaned, redacted


def _task_payload_for_package(info: Any) -> tuple[dict[str, Any], list[str]]:
    raw = tasks_mod._read_task_json(info.dir) or {}  # noqa: SLF001 与 update_task 同读路径
    cleaned, redacted = _sanitize_task_payload(raw)
    payload: dict[str, Any] = {
        "task_id": info.task_id,
        "display_name": info.display_name,
        "description": info.description,
        "screeners": list(info.screeners),
        "created_at": info.created_at,
        "last_used_at": info.last_used_at,
        "archived": False,
    }
    for key, value in cleaned.items():
        if key not in _RESERVED_TASK_KEYS:
            payload[key] = value
    return payload, redacted


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


# ---------------------------------------------------------------------------
# 整包源文件收集（导出/导出体检共用）
# ---------------------------------------------------------------------------

def _snapshot_sqlite(source: Path, base: Path, name: str, *,
                     allowed_roots: Iterable[Path]) -> Path:
    """用 SQLite backup API 生成一致性快照（含已提交的 WAL 内容）。"""
    actual_source = Path(source).resolve()
    allowed = False
    for root in allowed_roots:
        try:
            actual_source.relative_to(Path(root).resolve())
            allowed = True
            break
        except ValueError:
            continue
    if not allowed or not actual_source.is_file():
        raise TaskPackageError("package_member_invalid", params={
            "member": actual_source.name, "reason": "path_outside_task"})
    destination = _contained(base, "snapshots", name)
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with closing(_open_read_only(actual_source)) as src:
            with closing(sqlite3.connect(str(destination), timeout=15)) as dst:
                src.backup(dst)
    except TaskPackageError:
        raise
    except sqlite3.DatabaseError as exc:
        raise TaskPackageError("package_member_invalid", params={
            "member": actual_source.name, "reason": "sqlite_unreadable"}) from exc
    except OSError as exc:
        raise TaskPackageError("storage_unavailable", status=503) from exc
    return destination


def _relativize_snapshot_fulltexts(snapshot: Path, info: Any, data_dir: str | Path,
                                   live_db: Path) -> int:
    """终审 #43（用户裁定「相对化」）：导出快照内 fulltexts.path 一律任务目录相对。

    修复前 ``_snapshot_sqlite`` 纯 backup 后 db 成员原样入包——fulltexts.path
    存源机器绝对路径（含盘符/用户名/目录布局），与设计「整包不含任何环境
    信息」口径不符。改写规则（只动快照、绝不动源库）：

    * 行 path 可解析且位于任务目录内 → ``pdfs/<名>.pdf`` 形式的 POSIX 相对
      路径（``resolve_fulltext_raw`` 本就支持任务目录相对形态，导入/读取侧
      无需改动）；
    * 其余非空 path（指向共享平铺池/任意本机路径/文件已缺失）→ 清空——与
      终审 #39 的导出口径一致（这些行在导出/归档出口本就按 missing 跳过）。

    对抗口径与导入侧 :func:`_neutralize_unrestored_fulltexts` 同纪律：fulltexts
    是视图或挂触发器即拒绝（防 AFTER UPDATE 把 path 写回）；提交后重新 SELECT
    逐行断言零绝对路径，任一不符 → 结构化拒绝（绝不静默带出源机器路径）。
    """
    rewritten = 0
    with closing(sqlite3.connect(str(snapshot), timeout=15)) as conn:
        try:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(fulltexts)")}
        except sqlite3.Error:
            return 0          # 无 fulltexts 表（协作库/历史库）：无需改写
        if "path" not in columns or "zotero_key" not in columns:
            return 0
        if _fulltexts_schema_hostile(conn):
            raise TaskPackageError("package_member_invalid", params={
                "member": Path(snapshot).name, "reason": "fulltext_relativize_failed"})
        task_root = Path(info.dir).resolve()
        rows = conn.execute("SELECT zotero_key, path FROM fulltexts").fetchall()
        updates: list[tuple[str, object]] = []
        for row in rows:
            if not row or row[0] is None:
                continue
            raw = row[1]
            new_path = ""
            if isinstance(raw, str) and raw:
                resolved = tasks_mod.resolve_fulltext_raw(raw, live_db, data_dir=data_dir)
                if resolved is not None:
                    actual = Path(resolved).resolve()
                    try:
                        actual.relative_to(task_root)
                    except ValueError:
                        pass
                    else:
                        new_path = actual.relative_to(task_root).as_posix()
            if new_path != (raw or ""):
                updates.append((new_path, row[0]))
        conn.execute("BEGIN IMMEDIATE")
        try:
            for new_path, key in updates:
                conn.execute("UPDATE fulltexts SET path=? WHERE zotero_key=?",
                             (new_path, key))   # 原始对象做参数：BLOB 键类型才匹配
            conn.commit()
        except sqlite3.Error as exc:
            conn.rollback()
            raise TaskPackageError("package_member_invalid", params={
                "member": Path(snapshot).name,
                "reason": "fulltext_relativize_failed"}) from exc
        rewritten = len(updates)
        # 事后核验（不信任 rowcount/触发器）：快照内零绝对路径、零上跳。
        for key, path in conn.execute("SELECT zotero_key, path FROM fulltexts").fetchall():
            if key is None or not path:
                continue
            if Path(str(path)).is_absolute() or str(path).replace("\\", "/").startswith("../"):
                raise TaskPackageError("package_member_invalid", params={
                    "member": Path(snapshot).name,
                    "reason": "fulltext_relativize_failed"})
    return rewritten


def _collect_package_sources(info: Any, data_dir: str | Path, include_pdfs: bool,
                             workdir: Path) -> dict[str, Any]:
    """把整包成员落到 ``workdir`` 并返回成员清单（大小/路径/全文映射）。"""
    sources: list[dict[str, Any]] = []
    task_payload, redacted = _task_payload_for_package(info)
    task_bytes = _json_bytes(task_payload)
    task_path = _contained(workdir, "task.sanitized.json")
    task_path.write_bytes(task_bytes)
    sources.append({"arcname": "task.sanitized.json", "path": task_path,
                    "size": len(task_bytes)})

    task_roots = [Path(info.dir).resolve()]
    for db_file in info.db_files:
        source_slug = db_file.stem
        snapshot = _snapshot_sqlite(
            db_file, workdir, f"{source_slug}.db", allowed_roots=task_roots)
        # 终审 #43：db 成员的 fulltexts.path 相对化（快照内零源机器绝对路径；
        # 尺寸在改写后重读）。
        _relativize_snapshot_fulltexts(snapshot, info, data_dir, db_file)
        sources.append({"arcname": f"db/{source_slug}.db", "path": snapshot,
                        "size": snapshot.stat().st_size})

    collab = _collaboration_store(info)
    if collab.is_file():
        snapshot = _snapshot_sqlite(
            collab, workdir, "collaboration.db", allowed_roots=task_roots)
        sources.append({"arcname": "collaboration.db", "path": snapshot,
                        "size": snapshot.stat().st_size})

    history = info.dir / "import_history.sqlite3"
    if history.is_file():
        snapshot = _snapshot_sqlite(
            history, workdir, "import-history.sqlite3", allowed_roots=task_roots)
        sources.append({"arcname": "import-history.sqlite3", "path": snapshot,
                        "size": snapshot.stat().st_size})

    pdf_entries: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    if include_pdfs:
        items, skipped_keys, reasons = _archive_candidates(info, data_dir, "all")
        for item in items:
            arcname = f"fulltext/{item['archive_name']}"
            size = item["path"].stat().st_size
            sources.append({"arcname": arcname, "path": item["path"], "size": size})
            pdf_entries.append({
                "zotero_key": item["key"],
                "original_filename": item["original_filename"],
                "archive_name": item["archive_name"],
                "archive_path": arcname,
                "size": size,
            })
        for key in skipped_keys:
            skipped.append({"zotero_key": key,
                            "reason": "unreadable" if reasons["unreadable"] else "missing_file"})

    return {
        "sources": sources,
        "task_payload": task_payload,
        "redacted_fields": redacted,
        "fulltexts": pdf_entries,
        "skipped_fulltexts": skipped,
        "total_bytes": sum(item["size"] for item in sources),
    }


def _package_limit_error(total: int, pdf_bytes: int, pdf_count: int) -> TaskPackageError:
    return TaskPackageError("package_too_large", status=413, params={
        "limit_bytes": MAX_PACKAGE_BYTES,
        "total_bytes": total,
        "pdf_bytes": pdf_bytes,
        "pdf_count": pdf_count,
    })


def _build_package_zip(info: Any, data_dir: str | Path, include_pdfs: bool,
                       workdir: Path) -> tuple[Path, dict[str, Any]]:
    """生成整包 zip 到 ``workdir/package.zip`` 并返回 (路径, 摘要)。"""
    collected = _collect_package_sources(info, data_dir, include_pdfs, workdir)
    pdf_bytes = sum(entry["size"] for entry in collected["fulltexts"])
    if collected["total_bytes"] > MAX_PACKAGE_BYTES:
        raise _package_limit_error(collected["total_bytes"], pdf_bytes,
                                   len(collected["fulltexts"]))

    allowed_roots = [workdir.resolve()] + _fulltext_allowed_roots(info)
    members: list[dict[str, Any]] = []
    zip_path = _contained(workdir, "package.zip")
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED,
                         compresslevel=6) as archive:
        written_total = 0
        for source in collected["sources"]:
            digest, size = _add_file_to_zip(
                archive, source["arcname"], source["path"], allowed_roots=allowed_roots)
            members.append({"path": source["arcname"], "sha256": digest, "size": size})
            written_total += size
            # review67 L-30：流中总量复核（对齐归档导出终审 #44 的闸）——
            # PDF 成员直接从任务目录流式读取，收集期 stat 与写入之间被并发
            # 抓取增大的文件会整份写入 zip；超限即刻中止（异常沿 _build 的
            # except 分支清掉整个 workdir），绝不产出超过 2GB 上限的整包，
            # 也不让 X-Package-Bytes 头报收集期的陈旧值。
            if written_total > MAX_PACKAGE_BYTES:
                raise _package_limit_error(written_total, pdf_bytes,
                                           len(collected["fulltexts"]))
        member_hash = {member["path"]: member["sha256"] for member in members}
        manifest = {
            "schema": PACKAGE_SCHEMA,
            "created_at": _utc_now(),
            "source_task": {
                "task_id": info.task_id,
                "display_name": info.display_name,
                "screeners": list(info.screeners),
            },
            "options": {"include_pdfs": bool(include_pdfs)},
            "members": members,
            "slug_map": {},
            "fulltexts": [
                {
                    "zotero_key": entry["zotero_key"],
                    "original_filename": entry["original_filename"],
                    "archive_name": entry["archive_name"],
                    "archive_path": entry["archive_path"],
                    "sha256": member_hash.get(entry["archive_path"], ""),
                    "size": entry["size"],
                }
                for entry in collected["fulltexts"]
            ],
            "skipped_fulltexts": collected["skipped_fulltexts"],
            "redacted_fields": collected["redacted_fields"],
            "notes": {
                # 轨迹事件与设置存放在 {slug}.db / collaboration.db 内部，
                # 已随成员原样携带；不重复打包 trace/ 以免包体翻倍。
                "trace_members": "embedded_in_db_members",
                # 终审 #43（2.1.5-sec7 起）：db 成员内 fulltexts.path 一律任务
                # 目录相对（pdfs/<名>.pdf）；旧版包可能携带源机器绝对路径，
                # 导入侧两种格式都兼容（覆盖 key 重写、其余清空）。
                "fulltext_paths": "task_relative",
            },
            "summary": {
                "members": len(members),
                "pdf_exported": len(collected["fulltexts"]),
                "pdf_skipped": len(collected["skipped_fulltexts"]),
                "pdf_bytes": pdf_bytes,
                # review67 L-30：摘要/响应头报**实际写入**的成员字节总量
                #（流中累计），收集期 stat 值只在预检用——并发抓取使 PDF
                # 在收集与写入间增长时两者才会分歧，此时上方流中闸已先行
                # 拒绝，成功路径两值恒等。
                "total_bytes": written_total,
            },
        }
        archive.writestr(_zip_info("manifest.json"), _json_bytes(manifest))

    return zip_path, {"summary": manifest["summary"], "total_bytes": written_total}


def _package_inventory(info: Any, data_dir: str | Path, include_pdfs: bool
                       ) -> dict[str, Any]:
    """导出前体检：不生成 zip，只算成员与体积（SQLite 快照落临时目录后删除）。"""
    workdir = Path(tempfile.mkdtemp(prefix="rf-package-preflight-"))
    try:
        collected = _collect_package_sources(info, data_dir, include_pdfs, workdir)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    pdf_bytes = sum(entry["size"] for entry in collected["fulltexts"])
    total = collected["total_bytes"]
    # 终审 #44：归档导出上限的预检提前提示（archive-export 只打 PDF，scope=all
    # 的全文量是任意 scope 的上界；导出/体检共用 _archive_candidates 的同一计数）。
    archive_items, _archive_skipped, _archive_reasons = _archive_candidates(
        info, data_dir, "all")
    archive_bytes = sum(item["path"].stat().st_size for item in archive_items)
    return {
        "schema": PACKAGE_SCHEMA,
        "source_task": {"task_id": info.task_id, "display_name": info.display_name,
                        "screeners": list(info.screeners)},
        "options": {"include_pdfs": bool(include_pdfs)},
        "members": [
            {"path": source["arcname"], "size": source["size"]}
            for source in collected["sources"]
        ],
        "fulltexts": {
            "count": len(collected["fulltexts"]),
            "bytes": pdf_bytes,
            "skipped": len(collected["skipped_fulltexts"]),
            "skipped_keys": [entry["zotero_key"] for entry in collected["skipped_fulltexts"]],
        },
        "slug_map": {},
        "total_bytes": total,
        "limit_bytes": MAX_PACKAGE_BYTES,
        "over_limit": total > MAX_PACKAGE_BYTES,
        "suggest_exclude_pdfs": total > MAX_PACKAGE_BYTES and include_pdfs,
        "archive_export": {
            "limit_bytes": MAX_ARCHIVE_EXPORT_BYTES,
            "bytes": archive_bytes,
            "over_limit": archive_bytes > MAX_ARCHIVE_EXPORT_BYTES,
        },
        "redacted_fields": collected["redacted_fields"],
    }


# ---------------------------------------------------------------------------
# 导入包校验
# ---------------------------------------------------------------------------

def _strict_json(raw: bytes, *, reason: str = "manifest_unreadable") -> Any:
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        # 终审 #40：深嵌套 JSON（约 400KB 即可，远小于 8MB 上限）此前让
        # RecursionError（非 ValueError 子类）穿透 → 500；一并折进同一个
        # package_invalid{reason} 结构化 400。
        raise TaskPackageError("package_invalid", params={"reason": reason}) from exc


def _validate_member_name(name: str) -> None:
    if not name or not _MEMBER_NAME_RE.match(name):
        raise TaskPackageError("package_invalid", params={"reason": "member_name_invalid"})
    pure = PurePosixPath(name)
    if pure.is_absolute() or ".." in pure.parts or "\\" in name or "\x00" in name:
        raise TaskPackageError("package_invalid", params={"reason": "member_name_invalid"})


def _check_member_declared(info: zipfile.ZipInfo) -> None:
    """三限额的声明值预检（写盘前零成本拦截，终审 #41）。

    zip 头部声明的单成员尺寸/压缩比先于任何解压 IO 检查：诚实申报的炸弹
    在这里零字节写盘即被拒；谎报者在 :func:`_check_member_actual` 的实际
    字节流里被拦。压缩比用「每成员」口径——垫高其他成员的压缩总量不再能
    掩护单个 58 万倍压缩比的炸弹成员。
    """
    if info.file_size > MAX_MEMBER_BYTES:
        raise TaskPackageError("package_invalid",
                               params={"reason": "member_too_large",
                                       "member": info.filename})
    if info.file_size > max(1, info.compress_size) * MAX_COMPRESSION_RATIO:
        raise TaskPackageError("package_invalid",
                               params={"reason": "compression_ratio",
                                       "member": info.filename})


def _check_member_actual(info: zipfile.ZipInfo, size_so_far: int,
                         total_so_far: int) -> None:
    """三限额的实际字节执行（流式解压逐块核对，终审 #41）。"""
    if size_so_far > MAX_MEMBER_BYTES:
        raise TaskPackageError("package_invalid",
                               params={"reason": "member_too_large",
                                       "member": info.filename})
    if size_so_far > max(1, info.compress_size) * MAX_COMPRESSION_RATIO:
        raise TaskPackageError("package_invalid",
                               params={"reason": "compression_ratio",
                                       "member": info.filename})
    if total_so_far > MAX_UNCOMPRESSED_BYTES:
        raise TaskPackageError("package_invalid",
                               params={"reason": "uncompressed_too_large"})


#: 伪造 central directory / CRC 不符（zipfile 在流读中途抛 BadZipFile）→
#: 结构化 400（r2 复核：旧实现未捕获 → 500，违反「绝不 500」承诺）。
def _zip_corrupt_error(member: str) -> TaskPackageError:
    return TaskPackageError("package_invalid",
                            params={"reason": "zip_corrupt", "member": member})


def _read_member_bounded(archive: zipfile.ZipFile, info: zipfile.ZipInfo,
                         limit: int, *, reason: str) -> bytes:
    """流式有界读取单个成员（杜绝 archive.read 整块入内存，终审 #41）。"""
    buffer = bytearray()
    try:
        with archive.open(info, "r") as handle:
            while chunk := handle.read(_CHUNK):
                buffer.extend(chunk)
                if len(buffer) > limit:
                    raise TaskPackageError("package_invalid", params={"reason": reason})
    except zipfile.BadZipFile as exc:
        raise _zip_corrupt_error(info.filename) from exc
    return bytes(buffer)


def _stream_member_digest(archive: zipfile.ZipFile, info: zipfile.ZipInfo,
                          *, total_before: int = 0) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    try:
        with archive.open(info, "r") as handle:
            while chunk := handle.read(_CHUNK):
                size += len(chunk)
                _check_member_actual(info, size, total_before + size)
                digest.update(chunk)
    except zipfile.BadZipFile as exc:
        raise _zip_corrupt_error(info.filename) from exc
    return digest.hexdigest(), size


def _sha256_file(path: Path) -> str:
    actual = Path(path).resolve()
    root = Path(tempfile.gettempdir()).resolve()
    try:
        actual.relative_to(root)
    except ValueError:
        raise TaskPackageError("storage_unavailable", status=503) from None
    digest = hashlib.sha256()
    with open(actual, "rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def validate_import_package(path: str | Path, *, package_sha256: str | None = None
                            ) -> dict[str, Any]:
    """校验整包并返回解析结果；任何不一致抛 :class:`TaskPackageError`。"""
    path = Path(path)
    if not path.is_file() or path.stat().st_size == 0:
        raise TaskPackageError("package_invalid", params={"reason": "empty_upload"})
    if path.stat().st_size > MAX_PACKAGE_BYTES + _UPLOAD_SLACK_BYTES:
        raise TaskPackageError("package_too_large", status=413, params={
            "limit_bytes": MAX_PACKAGE_BYTES, "total_bytes": path.stat().st_size,
            "pdf_bytes": 0, "pdf_count": 0,
        })
    actual_sha = package_sha256 or _sha256_file(path)
    try:
        archive = zipfile.ZipFile(path, "r")
    except (zipfile.BadZipFile, OSError) as exc:
        raise TaskPackageError("package_invalid", params={"reason": "zip_unreadable"}) from exc

    with archive:
        infos = archive.infolist()
        if not infos:
            raise TaskPackageError("package_invalid", params={"reason": "zip_empty"})
        if len(infos) > MAX_MEMBERS:
            raise TaskPackageError("package_invalid", params={
                "reason": "too_many_members", "count": len(infos)})
        names: list[str] = []
        for info in infos:
            _validate_member_name(info.filename)
            if info.is_dir():
                raise TaskPackageError("package_invalid", params={"reason": "directory_member"})
            if info.flag_bits & 0x1:
                raise TaskPackageError("package_invalid", params={"reason": "encrypted_member"})
            _check_member_declared(info)   # 终审 #41：单成员/压缩比声明值预检
            names.append(info.filename)
        if len(names) != len(set(names)):
            raise TaskPackageError("package_invalid", params={"reason": "duplicate_member"})
        total_uncompressed = sum(info.file_size for info in infos)
        total_compressed = sum(info.compress_size for info in infos) or 1
        if total_uncompressed > MAX_UNCOMPRESSED_BYTES:
            raise TaskPackageError("package_invalid", params={"reason": "uncompressed_too_large"})
        if total_uncompressed > total_compressed * MAX_COMPRESSION_RATIO:
            raise TaskPackageError("package_invalid", params={"reason": "compression_ratio"})

        if "manifest.json" not in names:
            raise TaskPackageError("package_invalid", params={"reason": "manifest_missing"})
        manifest_info = archive.getinfo("manifest.json")
        if manifest_info.file_size > MAX_MANIFEST_BYTES:
            raise TaskPackageError("package_invalid", params={"reason": "manifest_too_large"})
        # 终审 #41：manifest 不在 members 清单内、无摘要交叉校验——实际字节
        # 必须有界读取（伪造 central directory 谎报小尺寸不能换整块入内存）。
        manifest = _strict_json(_read_member_bounded(
            archive, manifest_info, MAX_MANIFEST_BYTES, reason="manifest_too_large"))
        if not isinstance(manifest, dict) or manifest.get("schema") != PACKAGE_SCHEMA:
            raise TaskPackageError("package_invalid", params={"reason": "schema_unsupported"})

        listed = manifest.get("members")
        if not isinstance(listed, list):
            raise TaskPackageError("package_invalid", params={"reason": "members_invalid"})
        expected: dict[str, dict[str, Any]] = {}
        for item in listed:
            if not isinstance(item, dict):
                raise TaskPackageError("package_invalid", params={"reason": "members_invalid"})
            member_path = item.get("path")
            digest = item.get("sha256")
            size = item.get("size")
            if not isinstance(member_path, str) or not isinstance(digest, str) \
                    or not _SHA256_RE.match(digest) or not isinstance(size, int) or size < 0:
                raise TaskPackageError("package_invalid", params={"reason": "members_invalid"})
            _validate_member_name(member_path)
            if member_path == "manifest.json" or member_path in expected:
                raise TaskPackageError("package_invalid", params={"reason": "members_invalid"})
            expected[member_path] = {"sha256": digest, "size": size}
        actual = set(names) - {"manifest.json"}
        if set(expected) != actual:
            raise TaskPackageError("package_invalid", params={"reason": "member_set_mismatch"})

        actual_total = 0
        for name, declared in expected.items():
            _validate_member_name(name)
            digest, size = _stream_member_digest(
                archive, archive.getinfo(name), total_before=actual_total)
            actual_total += size
            if size != declared["size"] or digest != declared["sha256"]:
                raise TaskPackageError("package_checksum_mismatch", status=409,
                                       params={"member": name})

        # 终审 #41：task.sanitized.json 独立上限（曾无上限整读，preflight 阶段
        # 128MiB 声明即整块入内存）；有界流式读取。
        # 终审 #40（剩余分支①）：manifest 与成员集合自洽但**缺
        # task.sanitized.json** 时 getinfo 抛 KeyError → 500；显式断言成员
        # 存在，按坏包拒绝（结构化 400，reason 区分）。
        if "task.sanitized.json" not in actual:
            raise TaskPackageError("package_invalid",
                                   params={"reason": "task_json_missing"})
        task_info = archive.getinfo("task.sanitized.json")
        if task_info.file_size > MAX_TASK_JSON_BYTES:
            raise TaskPackageError("package_invalid",
                                   params={"reason": "task_json_too_large"})
        task_payload = _strict_json(
            _read_member_bounded(archive, task_info, MAX_TASK_JSON_BYTES,
                                 reason="task_json_too_large"),
            reason="task_json_unreadable")
        if not isinstance(task_payload, dict):
            raise TaskPackageError("package_invalid", params={"reason": "task_json_unreadable"})
        display_name = task_payload.get("display_name")
        if display_name is not None and not isinstance(display_name, str):
            raise TaskPackageError("package_invalid", params={"reason": "task_json_unreadable"})

        db_members = sorted(name for name in actual if name.startswith("db/"))
        for name in db_members:
            slug = PurePosixPath(name).stem
            if not _SLUG_RE.match(slug) or slug.casefold() == "collaboration":
                raise TaskPackageError("package_invalid", params={"reason": "slug_invalid"})
        # 数据库成员必须是可识别的 SQLite 文件（防「包内塞任意字节」在导入后
        # 才以损坏库形式暴露；协作库/历史库同理）。
        sqlite_members = db_members + [
            name for name in ("collaboration.db", "import-history.sqlite3")
            if name in actual
        ]
        for name in sqlite_members:
            with archive.open(archive.getinfo(name), "r") as handle:
                header = handle.read(16)
            if header != b"SQLite format 3\x00":
                raise TaskPackageError("package_invalid", params={
                    "reason": "member_not_sqlite", "member": name})

        # 2.1.5-sec29（发现 3）：协作库改绑前置条件在预检层即成立——改绑会
        # 失败/可被绕过的包，导入侧将被结构化拒绝，预检不得放行（不假 200）。
        if "collaboration.db" in actual:
            collab_dir = Path(tempfile.mkdtemp(prefix="rf-package-preflight-"))
            try:
                _check_collaboration_member_rewritable(archive, collab_dir)
            finally:
                shutil.rmtree(collab_dir, ignore_errors=True)

        fulltexts = manifest.get("fulltexts") or []
        if not isinstance(fulltexts, list):
            raise TaskPackageError("package_invalid", params={"reason": "fulltexts_invalid"})
        pdf_keys: set[str] = set()
        pdf_names: set[str] = set()
        for item in fulltexts:
            if not isinstance(item, dict):
                raise TaskPackageError("package_invalid", params={"reason": "fulltexts_invalid"})
            key = item.get("zotero_key")
            archive_path = item.get("archive_path")
            digest = item.get("sha256")
            size = item.get("size")
            if not isinstance(key, str) or not _TASK_ID_KEY_RE.match(key) or key in pdf_keys:
                raise TaskPackageError("package_invalid", params={"reason": "fulltexts_invalid"})
            if not isinstance(archive_path, str) or not archive_path.startswith("fulltext/"):
                raise TaskPackageError("package_invalid", params={"reason": "fulltexts_invalid"})
            _validate_member_name(archive_path)
            archive_name = archive_path[len("fulltext/"):]
            if not _ARCHIVE_NAME_RE.match(archive_name) or archive_name in pdf_names:
                raise TaskPackageError("package_invalid", params={"reason": "fulltexts_invalid"})
            if archive_path not in expected:
                raise TaskPackageError("package_invalid",
                                       params={"reason": "fulltext_member_missing"})
            if not isinstance(digest, str) or not _SHA256_RE.match(digest) \
                    or digest != expected[archive_path]["sha256"]:
                raise TaskPackageError("package_checksum_mismatch", status=409,
                                       params={"member": archive_path})
            if not isinstance(size, int) or size < 0 or size > MAX_PDF_BYTES:
                raise TaskPackageError("package_invalid", params={"reason": "pdf_too_large"})
            # 终审 #41：fulltexts[].size 必须与成员清单声明的实际尺寸一致——
            # 旧口径只查「≤100MB」，谎报 size=1 的 2MiB 成员照单全收（实测
            # MAX_PDF_BYTES 猴补为 1024 后仍 ACCEPTED）。
            if size != expected[archive_path]["size"]:
                raise TaskPackageError("package_invalid",
                                       params={"reason": "pdf_size_mismatch",
                                               "member": archive_path})
            pdf_keys.add(key)
            pdf_names.add(archive_name)

        skipped = manifest.get("skipped_fulltexts") or []
        if not isinstance(skipped, list):
            raise TaskPackageError("package_invalid", params={"reason": "fulltexts_invalid"})

    return {
        "package_sha256": actual_sha,
        "manifest": manifest,
        "task_payload": task_payload,
        "db_members": db_members,
        "members": [
            {"path": name, "size": expected[name]["size"]}
            for name in sorted(expected)
        ],
        "fulltexts": fulltexts,
        "skipped_fulltexts": skipped,
        "total_bytes": path.stat().st_size,
    }


def _slug_map_for(db_members: Iterable[str]) -> dict[str, str]:
    """新任务目录内无同名库，唯一冲突源是保留名/大小写重复；仍按纪律加后缀。"""
    mapping: dict[str, str] = {}
    used: set[str] = set()
    for name in sorted(db_members):
        source = PurePosixPath(name).stem
        target = source
        if target.casefold() == "collaboration" or target.casefold() in used:
            target = f"{source}-imported"
            counter = 2
            while target.casefold() in used:
                target = f"{source}-imported-{counter}"
                counter += 1
        used.add(target.casefold())
        mapping[source] = target
    return mapping


# ---------------------------------------------------------------------------
# 导入落地
# ---------------------------------------------------------------------------

def _atomic_write_task_json(task_dir: Path, payload: Mapping[str, Any]) -> None:
    target = _contained(task_dir, "task.json")
    tmp = _contained(task_dir, f"task.json.tmp-{os.getpid()}")
    try:
        tmp.relative_to(Path(task_dir).resolve())
    except ValueError:
        raise TaskPackageError("import_failed", params={"reason": "task_json_path"}) from None
    tmp.write_bytes(_json_bytes(payload))
    os.replace(tmp, target)


def _rewrite_collaboration_store(base: Path, name: str, *, task_id: str,
                                 owner_id: int, owner_name: str) -> bool:
    """把整包里的协作库改绑到导入者；返回是否成功改绑。

    旧部署的 role grants / member settings 引用源部署用户 ID，在新部署无意义
    且可能误授权，一律清空；``collaboration_settings``（含回填标记）保留。
    2.1.5-sof：``sof_judgments`` 的 ``decided_by``（含 prev 快照里的）同样指向
    源部署用户 ID——判定是研究产出、随包保留（不清空），但署名改绑给导入者
    （判定随包迁移的承接责任人）；表不存在（旧包/无判定）时静默跳过。
    sec29b-W1：v2.1.3 及更早的老协作库没有 ``collaboration_member_settings``
    （r12 才引入、产品侧惰性创建）——仅缺该产品自身表时按
    task_collaboration 的建表口径（字面量 DDL 与其 _SCHEMA 逐字一致）惰性
    补建空表后继续改绑；随后 DELETE 清空（旧部署用户 ID 本就无意义，
    reconciler 回填语义留给产品自身 ``_migrate_member_settings`` 首写时
    处理——老库届时同样按升级前口径回填，语义不丢失）。
    """
    try:
        with closing(_open_rw(base, name)) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS collaboration_member_settings ("
                    "user_id INTEGER PRIMARY KEY CHECK(user_id > 0), "
                    "screening_enabled INTEGER NOT NULL "
                    "CHECK(screening_enabled IN (0, 1)), "
                    "updated_by INTEGER NOT NULL CHECK(updated_by > 0), "
                    "updated_at TEXT NOT NULL)")
                cursor = conn.execute(
                    "UPDATE collaboration_task SET task_id=?, owner_user_id=?, "
                    "owner_username=? WHERE singleton=1",
                    (task_id, owner_id, owner_name),
                )
                if cursor.rowcount == 0:
                    conn.rollback()
                    return False
                conn.execute("DELETE FROM collaboration_role_grants")
                conn.execute("DELETE FROM collaboration_member_settings")
                sof_judgments_mod.rebind_decided_by(conn, owner_id)
                conn.commit()
                return True
            except sqlite3.Error:
                conn.rollback()
                return False
    except (sqlite3.Error, TaskPackageError):
        return False


def _rewrite_history(base: Path, name: str, *, task_id: str,
                     screener_map: Mapping[str, str]) -> None:
    try:
        with closing(_open_rw(base, name)) as conn, conn:
            conn.execute("UPDATE imports SET task_id=?", (task_id,))
            for source, target in screener_map.items():
                if source and source != target:
                    conn.execute("UPDATE imports SET screener=? WHERE screener=?",
                                 (target, source))
    except (sqlite3.Error, TaskPackageError):
        # 历史库仅作展示，结构损坏不阻断整包导入（数据已随成员落盘）。
        logger.warning("导入历史库改写失败（保留原样）", exc_info=True)


def _fulltext_rows_by_key(base: Path, names: Iterable[str]) -> dict[str, list[str]]:
    rows: dict[str, list[str]] = {}
    for name in names:
        target = _contained(base, name)
        try:
            target.relative_to(Path(base).resolve())
        except ValueError:
            raise TaskPackageError("import_failed", params={"reason": "db_path"}) from None
        for row in _fulltext_key_rows(target):
            if row and row[0]:
                rows.setdefault(str(row[0]), []).append(name)
    return rows


def _update_fulltext_row(base: Path, name: str, *, key: str, path: str,
                         filename: str, sha256: str, n_pages: int) -> None:
    try:
        with closing(_open_rw(base, name)) as conn, conn:
            cursor = conn.execute(
                "UPDATE fulltexts SET path=?, filename=?, sha256=?, n_pages=? "
                "WHERE zotero_key=?",
                (path, filename, sha256, n_pages, key),
            )
            if cursor.rowcount < 1:
                # r2 复核：包内库可装 BEFORE UPDATE 触发器使改写静默失效——
                # manifest 覆盖的 key 必须真实改写，否则拒绝（绝不假 200）。
                raise TaskPackageError("import_failed",
                                       params={"reason": "fulltext_rewrite"})
    except sqlite3.Error as exc:
        raise TaskPackageError("import_failed", params={"reason": "fulltext_rewrite"}) from exc


def _fulltexts_schema_hostile(conn: sqlite3.Connection) -> bool:
    """fulltexts 是否被做了手脚：是视图，或其上挂有任意触发器（r3）。

    产品自身的筛选员库 fulltexts 永远是普通表、无触发器（coscreen.db.init_db）；
    出现视图/触发器只可能来自自造包。BEFORE ``RAISE(IGNORE)``、``AFTER UPDATE``
    把 path 写回、``INSTEAD OF`` 等任何形态都能让「UPDATE rowcount=1 但行未
    清空」——在 schema 层直接拒绝，不逐一猜触发器写法。
    """
    rows = conn.execute(
        "SELECT type FROM sqlite_master "
        "WHERE (type='view' AND lower(name)='fulltexts') "
        "OR (type='trigger' AND lower(tbl_name)='fulltexts')").fetchall()
    return bool(rows)


#: 协作库改绑（:func:`_rewrite_collaboration_store`）触及的表（2.1.5-sec29
#: 发现 3）：缺表/缺列/无 singleton 行 = 改绑必失败；表上有视图/触发器 =
#: 改绑可被静默吞掉或写回——预检与导入同口径拒绝，绝不静默换空库假 200。
#: sec29b-W1：``collaboration_member_settings`` 是唯一可缺席的表（v2.1.3
#: 及更早部署的协作库没有它——r12/7fb49a0 才引入且惰性创建；仅缺该产品
#: 自身表且其余前置全过时由改绑惰性补建，见
#: :func:`_check_collaboration_member_rewritable`）。
_COLLABORATION_REWRITE_TABLES = ("collaboration_task",
                                 "collaboration_role_grants",
                                 "collaboration_member_settings")
#: 唯一允许缺席的改绑目标表（v2.1.3 老形状；其余任何缺表仍敌意）。
_COLLABORATION_LAZY_CREATE_TABLE = "collaboration_member_settings"
_COLLABORATION_TASK_COLUMNS = frozenset({
    "singleton", "task_id", "owner_user_id", "owner_username"})
#: 产品自身协作库中**唯一**合法的触发器形状（sec29b-W1 全库盘点实测；
#: P18 D-1 修轮扩精确复刻）。两组：
#:
#: 1. 仲裁快照的 append-only 守卫（screening_arbitration._ARBITRATION_SCHEMA，
#:    存进协作库）；
#: 2. P14 团队流三件（F2 registry/F1 facts/P07 receipts）在 collab 上的 9 个
#:    产品触发器（P18 create/rebind/管理 hook 经 ``prepare_team_protocol_storage``
#:    安装）+ task_collaboration 自身的 business_role_events append-only 两守卫
#:    （set_business_role 安装；修轮实测共 11 个非仲裁触发器）。修轮前白名单
#:    只有第 1 组 → 自家导出的任务包导入即 400 package_invalid、带历史分支
#:    结构性不可达——run1 复审 D-1。
#:
#: 键 = (trigger 名, 目标表)，值 = sqlite_master 中应逐字一致的 CREATE 文本
#: （空白归一比较；registry 族的 ``main.`` 限定符按 sqlite_master 实存形态
#: 剥离后比对）——同名异表/同名异体（改写触发器体注入 grants 等）一律按
#: 敌意拒绝；能逐字复刻的触发器语义与产品自有的等价（RAISE(ABORT) 守卫），
#: 无注入面。产品协作库从不创建任何视图。9 条团队流文本由真实
#: ``prepare_team_protocol_storage`` 产物逐字导出钉死（records/
#: gen_trigger_whitelist.txt）——产品 DDL 变更时本表需随版本同步。
_TEAM_FLOW_TRIGGER_REPLICAS = {
    ('collaboration_business_role_events_no_delete',
     'collaboration_business_role_events'):
        "CREATE TRIGGER collaboration_business_role_events_no_delete BEFORE DELETE ON collaboration_business_role_events BEGIN SELECT RAISE(ABORT, 'business role events are append-only'); END",
    ('collaboration_business_role_events_no_update',
     'collaboration_business_role_events'):
        "CREATE TRIGGER collaboration_business_role_events_no_update BEFORE UPDATE ON collaboration_business_role_events BEGIN SELECT RAISE(ABORT, 'business role events are append-only'); END",
    ('team_flow_receipts_guard_insert', 'team_flow_receipts'):
        "CREATE TRIGGER team_flow_receipts_guard_insert BEFORE INSERT ON team_flow_receipts WHEN EXISTS(SELECT 1 FROM team_flow_receipts WHERE receipt_id = NEW.receipt_id OR (task_instance_id = NEW.task_instance_id AND actor_user_id = NEW.actor_user_id AND operation = NEW.operation AND request_id = NEW.request_id)) BEGIN SELECT RAISE(ABORT, 'team_flow_receipts is append-only: reject re-insert/replace'); END",
    ('team_flow_receipts_no_delete', 'team_flow_receipts'):
        "CREATE TRIGGER team_flow_receipts_no_delete BEFORE DELETE ON team_flow_receipts BEGIN SELECT RAISE(ABORT, 'team flow receipts are append-only'); END",
    ('team_flow_receipts_no_update', 'team_flow_receipts'):
        "CREATE TRIGGER team_flow_receipts_no_update BEFORE UPDATE ON team_flow_receipts BEGIN SELECT RAISE(ABORT, 'team flow receipts are append-only'); END",
    ('team_source_registry_guard_update', 'team_source_registry'):
        "CREATE TRIGGER team_source_registry_guard_update BEFORE UPDATE ON team_source_registry WHEN OLD.registration_id IS NOT NEW.registration_id OR OLD.task_instance_id IS NOT NEW.task_instance_id OR OLD.source_user_id IS NOT NEW.source_user_id OR OLD.source_library_id IS NOT NEW.source_library_id OR OLD.source_path_identity IS NOT NEW.source_path_identity OR OLD.generation IS NOT NEW.generation OR OLD.registered_at IS NOT NEW.registered_at OR (OLD.status = 'retired' AND NEW.status IS NOT 'retired') OR ( OLD.status = 'retired' AND ( NEW.retired_at IS NOT OLD.retired_at OR NEW.retired_reason IS NOT OLD.retired_reason OR NEW.archive_watermark IS NOT OLD.archive_watermark ) ) OR ( OLD.status = 'active' AND NEW.status = 'active' AND ( NEW.retired_at IS NOT NULL OR NEW.retired_reason IS NOT NULL OR NEW.archive_watermark IS NOT NULL ) ) BEGIN SELECT RAISE(ABORT, 'team_source_registry identity/retirement guard'); END",
    ('team_source_registry_no_delete', 'team_source_registry'):
        "CREATE TRIGGER team_source_registry_no_delete BEFORE DELETE ON team_source_registry BEGIN SELECT RAISE(ABORT, 'team_source_registry delete forbidden'); END",
    ('team_task_protocol_guard_update', 'team_task_protocol'):
        "CREATE TRIGGER team_task_protocol_guard_update BEFORE UPDATE ON team_task_protocol WHEN OLD.task_instance_id IS NOT NEW.task_instance_id OR OLD.task_id IS NOT NEW.task_id OR OLD.owner_user_id IS NOT NEW.owner_user_id OR OLD.owner_library_id IS NOT NEW.owner_library_id OR OLD.protocol_version IS NOT NEW.protocol_version OR OLD.created_at IS NOT NEW.created_at OR NEW.updated_at < OLD.updated_at OR NEW.roster_revision < OLD.roster_revision OR NEW.roster_revision > OLD.roster_revision + 1 OR (OLD.coverage_state = 'new_covered' AND NEW.coverage_state IS NOT 'new_covered') OR (OLD.coverage_state = 'legacy_verified' AND NEW.coverage_state IS NOT 'legacy_verified') OR ( OLD.coverage_state = 'legacy_pending_verification' AND NEW.coverage_state NOT IN ('legacy_pending_verification','legacy_verified') ) OR (OLD.adopted_at IS NOT NULL AND NEW.adopted_at IS NOT OLD.adopted_at) OR ( OLD.adoption_evidence_ref IS NOT NULL AND NEW.adoption_evidence_ref IS NOT OLD.adoption_evidence_ref ) OR ( OLD.adoption_evidence_json IS NOT NULL AND NEW.adoption_evidence_json IS NOT OLD.adoption_evidence_json ) OR ( OLD.adoption_evidence_sha256 IS NOT NULL AND NEW.adoption_evidence_sha256 IS NOT OLD.adoption_evidence_sha256 ) BEGIN SELECT RAISE(ABORT, 'team_task_protocol identity/state regression'); END",
    ('team_task_protocol_no_delete', 'team_task_protocol'):
        "CREATE TRIGGER team_task_protocol_no_delete BEFORE DELETE ON team_task_protocol BEGIN SELECT RAISE(ABORT, 'team_task_protocol delete forbidden'); END",
    ('team_terminal_seals_no_delete', 'team_terminal_seals'):
        "CREATE TRIGGER team_terminal_seals_no_delete BEFORE DELETE ON team_terminal_seals BEGIN SELECT RAISE(ABORT, 'team terminal seals are append-only'); END",
    ('team_terminal_seals_no_update', 'team_terminal_seals'):
        "CREATE TRIGGER team_terminal_seals_no_update BEFORE UPDATE ON team_terminal_seals BEGIN SELECT RAISE(ABORT, 'team terminal seals are append-only'); END",
}

_COLLABORATION_OWN_TRIGGERS = {
    "screening_arbitrations_no_update": (
        "CREATE TRIGGER screening_arbitrations_no_update "
        "BEFORE UPDATE ON screening_arbitrations "
        "BEGIN SELECT RAISE(ABORT, 'screening arbitrations are append-only'); END"),
    "screening_arbitrations_no_delete": (
        "CREATE TRIGGER screening_arbitrations_no_delete "
        "BEFORE DELETE ON screening_arbitrations "
        "BEGIN SELECT RAISE(ABORT, 'screening arbitrations are append-only'); END"),
}


def _collaboration_expected_trigger(name: str, tbl_name: str) -> str | None:
    """产品自有触发器形状查表（两组白名单合一；None=不在白名单）。"""
    key = (name.lower(), tbl_name.lower())
    if key in _TEAM_FLOW_TRIGGER_REPLICAS:
        return _TEAM_FLOW_TRIGGER_REPLICAS[key]
    if name.lower() in _COLLABORATION_OWN_TRIGGERS:
        return _COLLABORATION_OWN_TRIGGERS[name.lower()]
    return None


def _collaboration_rewrite_error(member: str = "collaboration.db"
                                 ) -> TaskPackageError:
    """改绑前置条件不成立/改绑失败共用的结构化错误（400，reason 单一口径）。"""
    return TaskPackageError("package_invalid", params={
        "reason": "collaboration_rewrite", "member": member})


def _collaboration_schema_hostile(conn: sqlite3.Connection) -> bool:
    """协作库是否被做了手脚：库内存在**任意**视图，或任一**非产品自有形状**
    的触发器（不再按改绑目标表的 tbl_name 过滤）。

    sec29b-W1（复审 z3 端到端实测复现）：修复前只按 4 张改绑相关表的
    tbl_name 查触发器——挂在 ``collaboration_settings`` 等其他表上的
    AFTER INSERT 触发器逃逸，导入后产品自身 team-progress 的回填 INSERT
    引爆触发器注入 grants（权限提升）。导入后的产品自身写入不止改绑四表
    （``_migrate_member_settings`` 写 settings、grant_role 写 grants、仲裁
    追加写 screening_arbitrations……），任何表上的恶意触发器都是注入面，
    因此对**全库**触发器逐一判定：仅放行
    :func:`_collaboration_expected_trigger` 两组白名单——仲裁 append-only
    两守卫 + P18 D-1 扩的团队流 9 个产品触发器精确复刻（同名同表且 CREATE
    文本逐字一致），其余任何触发器/视图（无论挂在哪张表）一律敌意——
    BEFORE ``RAISE(IGNORE)`` 让 UPDATE rowcount=0、
    AFTER 把属主改回旧值、AFTER INSERT 自授角色等任何形态都在 schema 层
    直接拒绝，不逐一猜触发器写法。
    """
    if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='view' LIMIT 1"
    ).fetchone() is not None:
        return True
    for name, tbl_name, sql in conn.execute(
            "SELECT name, tbl_name, sql FROM sqlite_master WHERE type='trigger'"):
        expected = _collaboration_expected_trigger(
            str(name), str(tbl_name))
        if expected is None or " ".join((sql or "").split()) != expected:
            return True
    return False


def _check_collaboration_member_rewritable(archive: zipfile.ZipFile,
                                           base: Path) -> None:
    """预检/导入共用：包内 ``collaboration.db`` 必须可安全改绑到导入者。

    2.1.5-sec29 发现 3：修复前改绑失败（返回 False）被静默 unlink 换空库
    ——包内判定/设置丢失且导入仍 200。现对齐 fulltexts 的对抗口径，改绑
    前置条件在 :func:`validate_import_package`（预检与导入共同入口）即判定，
    任一不成立 → 结构化 400 ``collaboration_rewrite``：

    * 改绑目标表都是普通表（缺表 = DELETE/UPDATE 必抛错）；sec29b-W1 起
      **唯一例外**：仅缺 ``collaboration_member_settings``（v2.1.3 及更早
      部署的自家老形状，该表 r12 才引入且产品侧惰性创建）且其余前置全过
      时放行——由 :func:`_rewrite_collaboration_store` 惰性补建空表后
      继续改绑（老部署往返恢复 200/200）；缺其他任何表/与其他敌意痕迹
      并存仍 400；
    * ``collaboration_task`` 具备改绑所需列且存在 ``singleton=1`` 行
      （UPDATE rowcount=0 = 改绑无对象）；
    * 库内无视图、无非产品自有形状的触发器（全库判定，sec29b-W1 收口：
      防改绑被静默吞掉/写回，防导入后产品自身写入引爆任意表上的触发器）；
    * ``sof_judgments`` 若存在则具备 ``decided_by``/``prev_json`` 列
      （rebind_decided_by 的 SELECT/UPDATE 目标列）。

    成员解压复用 :func:`_extract_member` 有界流式通道（终审 #41 三限额照常
    生效）；校验后中转文件即刻删除。
    """
    scratch = _extract_member(archive, "collaboration.db", base,
                              "collaboration.db")
    try:
        with closing(_open_read_only(scratch)) as conn:
            tables = {str(row[0]).lower() for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            missing = [table for table in _COLLABORATION_REWRITE_TABLES
                       if table not in tables]
            if missing and missing != [_COLLABORATION_LAZY_CREATE_TABLE]:
                raise _collaboration_rewrite_error()
            if _collaboration_schema_hostile(conn):
                raise _collaboration_rewrite_error()
            columns = {str(row[1]).lower() for row in conn.execute(
                "PRAGMA table_info(collaboration_task)")}
            if not _COLLABORATION_TASK_COLUMNS <= columns:
                raise _collaboration_rewrite_error()
            row = conn.execute(
                "SELECT COUNT(*) FROM collaboration_task WHERE singleton=1"
            ).fetchone()
            if row is None or int(row[0]) < 1:
                raise _collaboration_rewrite_error()
            sof_columns = {str(row[1]).lower() for row in conn.execute(
                "PRAGMA table_info(sof_judgments)")}
            if sof_columns and not {"decided_by", "prev_json"} <= sof_columns:
                raise _collaboration_rewrite_error()
    except sqlite3.DatabaseError as exc:
        # 头 16 字节是 SQLite 但库体损坏/不可读：改绑必然失败，同口径拒绝。
        raise _collaboration_rewrite_error() from exc
    finally:
        scratch.unlink(missing_ok=True)


def _neutralize_unrestored_fulltexts(base: Path, names: Iterable[str],
                                     covered_paths: Mapping[str, str]) -> int:
    """终审 #39：manifest 未覆盖的 fulltexts 行 ``path`` 一律清空。

    导入的库成员可能携带指向共享 ``{data_dir}/pdfs`` 池（或其他本机路径）的
    旧行；原样保留会让导出/下载出口把其他任务/遗留任务的文件当作本任务全文
    读出（跨任务读文件）。只有 manifest 覆盖、已改写到新任务目录的 key 保留
    path；其余行 path 清空。返回实际清空的行数。

    对抗口径（攻击者完全控制包内 .db）：
    * r2：UPDATE 参数用 SELECT 返回的**原始对象**（BLOB 键 str() 后永不匹配），
      逐行校验 ``rowcount``；
    * r3：``AFTER UPDATE`` 触发器可把 path 写回而外层 rowcount 仍为 1——因此
      ① fulltexts 是视图或挂有任何触发器即拒绝（:func:`_fulltexts_schema_hostile`）；
      ② commit 后**重新 SELECT 全部行逐行断言**：覆盖 key 的 path 必须等于本次
      写入的新任务目录路径、其余行 path 必须为空。任一不符 → 结构化 400
      （``fulltext_neutralize_failed`` / 覆盖 key 为 ``import_failed`` +
      ``fulltext_rewrite``），由 _apply_import 的异常路径清场。绝不给
      「假 200 + 行保留」。
    """
    covered = {str(key): str(path) for key, path in covered_paths.items()}
    cleared = 0
    for name in names:
        try:
            with closing(_open_rw(base, name)) as conn:
                try:
                    rows = conn.execute(
                        "SELECT zotero_key, path FROM fulltexts").fetchall()
                except sqlite3.Error:
                    # 打不开/缺表/坏库：SELECT 读不出任何行 → 无行可泄露
                    # （导出侧 _fulltext_rows 同口径降级为空），跳过不阻断。
                    logger.warning(
                        "导入库 fulltexts 处置跳过（不可读/缺表）: %s", name)
                    continue
                if _fulltexts_schema_hostile(conn):
                    raise TaskPackageError("package_invalid", params={
                        "reason": "fulltext_neutralize_failed", "member": name})
                pending = [row for row in rows
                           if row and row[0] is not None
                           and str(row[0]) not in covered and row[1]]
                for row in pending:
                    cursor = conn.execute(
                        "UPDATE fulltexts SET path='' WHERE zotero_key=? AND path<>''",
                        (row[0],),        # 原始对象做参数：BLOB 键类型才匹配
                    )
                    if cursor.rowcount != 1:
                        raise TaskPackageError("package_invalid", params={
                            "reason": "fulltext_neutralize_failed", "member": name})
                    cleared += cursor.rowcount
                conn.commit()
                # 事后核验（不信任 rowcount）：提交后的真实状态必须成立。
                for key, path in conn.execute(
                        "SELECT zotero_key, path FROM fulltexts").fetchall():
                    if key is None:
                        continue
                    expected = covered.get(str(key))
                    actual = "" if path is None else str(path)
                    if expected is not None:
                        if actual != expected:
                            raise TaskPackageError(
                                "import_failed", params={"reason": "fulltext_rewrite"})
                    elif actual != "":
                        raise TaskPackageError("package_invalid", params={
                            "reason": "fulltext_neutralize_failed", "member": name})
        except TaskPackageError:
            raise
        except sqlite3.Error as exc:
            # UPDATE/commit/核验阶段失败：SELECT 已读到行、清洗必须成立——拒绝。
            raise TaskPackageError("package_invalid", params={
                "reason": "fulltext_neutralize_failed", "member": name}) from exc
    return cleared


def _extract_member(archive: zipfile.ZipFile, member_name: str, base: Path,
                    target_name: str, *, total_before: int = 0) -> Path:
    """把包成员**流式**解压到 ``base`` 内（成员名/目标名就地校验，防 zip-slip）。

    目标文件名由服务器端从**已校验 slug**派生（不以包内任意字符串直接做路径）。
    终审 #45（用户裁定「统一按预检」）：成员名判定**直接调用预检同一函数**
    ``_validate_member_name``（parts 级 '..' 检查）——修复前这里内联子串级
    ``".." in name`` 更严，合法带点名（``db/u5..c9.db``、带点 zotero_key 的
    规范 PDF 名）预检放行、导入被拒，应用自导出的整包无法再导入。非法名
    （``../`` 上跳、绝对路径、反斜杠、NUL）两处仍同一拒绝。
    终审 #41：逐块写出（杜绝 ``archive.read`` 整块入内存——旧实现实测 64MiB
    成员峰值 141MiB、数 GB 成员可 OOM）；单成员上限/压缩比/解压总量三限额在
    声明值与实际字节两道各查一次，超限抛结构化错误由调用方清场。
    """
    if not isinstance(member_name, str):
        raise TaskPackageError("package_invalid", params={"reason": "member_name_invalid"})
    _validate_member_name(member_name)
    safe_name = os.path.basename(target_name)
    if safe_name != target_name or not _SLUG_RE.match(PurePosixPath(safe_name).stem):
        raise TaskPackageError("package_invalid", params={"reason": "member_name_invalid"})
    target = _contained(base, safe_name)
    try:
        target.relative_to(Path(base).resolve())
    except ValueError:
        raise TaskPackageError("package_invalid",
                               params={"reason": "member_name_invalid"}) from None
    info = archive.getinfo(member_name)
    _check_member_declared(info)
    size = 0
    try:
        with archive.open(info, "r") as source, open(target, "wb") as sink:
            while chunk := source.read(_CHUNK):
                size += len(chunk)
                _check_member_actual(info, size, total_before + size)
                sink.write(chunk)
    except zipfile.BadZipFile as exc:
        raise _zip_corrupt_error(member_name) from exc
    if size != info.file_size:
        # 声明与实际不符（校验后文件被换/并发写坏）：按不一致拒绝并清场。
        raise TaskPackageError("package_invalid",
                               params={"reason": "member_size_mismatch",
                                       "member": member_name})
    return target


#: R8（用户裁定 a，2026-11-21）：带历史团队包导入时的协议重绑审计操作名。
#: 落 ``team_flow_receipts``（既有 append-only 审计先例，collab 原产：
#: source_registration_id/source_generation 二者 None），随包随任务迁移。
IMPORT_PROTOCOL_REBIND_OPERATION = "team_protocol:import_rebind"

#: 迁移完成提示词条（服务端契约键；十语言词条由前端 batch6_locale.js 注册，
#: 本包只交付键与中文兜底串——见 REPORT 词条清单）。
IMPORT_PROTOCOL_REBIND_MESSAGE_KEY = "task_package.team_protocol_rebound"
IMPORT_PROTOCOL_REBIND_MESSAGE = (
    "已为您完成迁移：团队协议已绑定到新任务，历史协作记录保持不变。")

#: 受控重建的中转表名（固定字面量——SQL 零拼接、零外部标识符）。
#: 包内若已占用该名字：重建前置检查拒绝整个导入（fail-closed，不猜）。
_PROTOCOL_REBIND_STALE_TABLE = "team_task_protocol_pre_import"


def _utc_now_ms() -> str:
    """RFC3339 UTC 毫秒 Z（receipt/F1 同口径）。"""
    return datetime.now(timezone.utc).isoformat(
        timespec="milliseconds").replace("+00:00", "Z")


def _rebind_import_failed(reason: str, detail: str = "") -> TaskPackageError:
    params: dict[str, Any] = {"reason": reason}
    if detail:
        params["detail"] = detail
    return TaskPackageError("import_failed", params=params)


def _rebind_imported_team_protocol(task_dir: Path, task_id: str, owner_id: int,
                                   owner_name: str,
                                   preserved_instance: str) -> dict[str, Any]:
    """R8：把包内协议行重绑到本次导入的新 ``task_id``（导入组装窗口内）。

    问题（P3 复审探针实证）：带历史团队包导入后 protocol 行仍绑定源部署
    的 ``task_id``；产品守卫触发器 ``team_task_protocol_guard_update``
    禁止 UPDATE 改绑（ABORT identity/state regression），keyed 计划按新
    task_id 解析落空 → team 写 503 ``task_not_registered``（"能看不能写"）。

    受控路径（不削弱守卫）：在临时目录整体改名（``os.replace``）之前，
    即协作库尚未进入服务期的组装窗口内，以**单事务**完成协议表重建：

    1. ``ALTER TABLE ... RENAME TO team_task_protocol_pre_import``（固定
       中转名，前置检查占用即拒绝）；表上的两条守卫触发器随表迁移；
    2. 用产品 canonical DDL ``_TABLE_PROTOCOL_SQL`` 建回同名表；
    3. 以**原 instance/library/coverage/roster_revision/adopted_at** 插入
       重绑行：仅 ``task_id`` 改为本次新 id、``owner_user_id`` 改为导入者
       （collaboration_task 已由 ``_rewrite_collaboration_store`` 改绑到
       导入者；两处 owner 不一致会让写窗 ``identity_unconfirmed``），
       ``owner_library_id`` 原样保留（P18 恢复契约「不悄换 instance/
       library」）；``legacy_verified`` 行随绑定字段重算 adoption evidence
       JSON/哈希（文档内嵌 task_id/owner_user_id），其余字段原样；
    4. 删除中转旧表（其守卫触发器随之消失），用产品 canonical DDL
       ``_TRIGGER_PROTOCOL_NO_DELETE_SQL`` / ``_TRIGGER_PROTOCOL_GUARD_SQL``
       **逐字重建两条守卫**；
    5. 同事务内经产品强读 ``read_protocol_on`` 复核新行与完整布局；
    6. 追加迁移审计 receipt（旧/新 task_id、时间、触发者=importer、
       evidence 是否重算、原 owner uid/library）；
    7. 提交后经产品 ``prepare_team_terminal_registry``（精确布局断言）与
       ``read_protocol_for_task_on``（按新 task_id 正查）终验。

    语义边界：守卫触发器文本/布局零变化（重建后仍禁 UPDATE/DELETE，测试
    钉死）；重建在导入原子边界内，失败=typed 导入失败并清场（不留半迁移
    状态）；幂等（同包导入两次产生两个新 task_id 与各自 receipt）。
    """
    from coscreen import team_terminal_registry as _registry
    from coscreen.team_flow_receipts import append_receipt_on

    instance = str(preserved_instance or "")
    if not instance:
        raise _rebind_import_failed("protocol_instance_missing")
    collab = task_dir / "collaboration.db"
    conn = sqlite3.connect(str(collab), timeout=15)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("BEGIN IMMEDIATE")
        try:
            occupied = conn.execute(
                "SELECT 1 FROM main.sqlite_master WHERE name=?",
                (_PROTOCOL_REBIND_STALE_TABLE,)).fetchone()
            if occupied is not None:
                raise _rebind_import_failed("protocol_rebind_staging_name_taken")
            rows = conn.execute(
                "SELECT task_instance_id FROM main.team_task_protocol"
            ).fetchall()
            if len(rows) != 1:
                raise _rebind_import_failed(
                    "protocol_row_count", detail=f"rows={len(rows)}")
            record = _registry.read_protocol_on(
                conn, task_instance_id=instance)
            if record is None:
                raise _rebind_import_failed("protocol_instance_mismatch")
            old_task_id = str(record["task_id"])
            if old_task_id == task_id:
                conn.commit()
                return {"rebound": False, "reason": "already_bound",
                        "old_task_id": old_task_id, "new_task_id": task_id}
            coverage = str(record["coverage_state"])
            evidence_ref = record["adoption_evidence_ref"]
            evidence_json = record["adoption_evidence_json"]
            evidence_sha = record["adoption_evidence_sha256"]
            evidence_recomposed = False
            if coverage == "legacy_verified":
                # 证据文档内嵌 task_id/owner_user_id——重绑后必须按产品
                # canonical JSON 规则重算（否则强读判 corrupt/绑定不符）。
                # 复用 registry 自身的 canonical JSON/哈希实现（单一事实源：
                # 存储校验侧也用同一对函数，绝不在本文件复制一份可能漂移的
                # 序列化口径）。文档其余字段（sources/scope/method/
                # adopted_at/prior_history_unknown）一字不动。
                document = json.loads(str(evidence_json))
                document["task_id"] = task_id
                document["owner_user_id"] = owner_id
                evidence_json = _registry._canonical_json(document)
                evidence_sha = _registry._sha256_text(evidence_json)
                evidence_recomposed = True
            rebound_at = _utc_now_ms()
            updated_at = max(rebound_at, str(record["updated_at"]))
            # SQL 全字面量（Mimosa 约束）：中转表名是模块常量，此处按
            # 「校验后的字面量」展开为单串（与 PRAGMA/ATTACH 白名单同口径），
            # 绝不由外部输入拼接。
            conn.execute(
                "ALTER TABLE main.team_task_protocol"
                " RENAME TO team_task_protocol_pre_import")
            conn.execute(_registry._TABLE_PROTOCOL_SQL)
            conn.execute(
                "INSERT INTO main.team_task_protocol"
                "(task_instance_id, task_id, owner_user_id, owner_library_id,"
                " protocol_version, coverage_state, roster_revision, adopted_at,"
                " adoption_evidence_ref, adoption_evidence_json,"
                " adoption_evidence_sha256, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (instance, task_id, owner_id, record["owner_library_id"],
                 record["protocol_version"], coverage,
                 record["roster_revision"], record["adopted_at"],
                 evidence_ref, evidence_json, evidence_sha,
                 record["created_at"], updated_at))
            conn.execute("DROP TABLE main.team_task_protocol_pre_import")
            conn.execute(_registry._TRIGGER_PROTOCOL_NO_DELETE_SQL)
            conn.execute(_registry._TRIGGER_PROTOCOL_GUARD_SQL)
            # 同事务产品强读复核：精确布局 + 新行全字段校验（含重算证据）。
            rebound = _registry.read_protocol_on(
                conn, task_instance_id=instance)
            if rebound is None or rebound["task_id"] != task_id:
                raise _rebind_import_failed("protocol_rebind_readback")
            audit = {
                "old_task_id": old_task_id,
                "new_task_id": task_id,
                "rebound_at": rebound_at,
                "importer_user_id": owner_id,
                "importer_username": owner_name,
                "evidence_recomposed": evidence_recomposed,
            }
            request_sha256 = hashlib.sha256(
                json.dumps(audit, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":"), allow_nan=False).encode("utf-8")
            ).hexdigest()
            receipt = append_receipt_on(conn, {
                "schema": "team_flow_receipt.v1",
                "task_instance_id": instance,
                "actor_user_id": owner_id,
                "operation": IMPORT_PROTOCOL_REBIND_OPERATION,
                "request_id": f"import-rebind-{os.urandom(16).hex()}",
                "request_sha256": request_sha256,
                "result": audit,
                "internal_evidence": {
                    "trigger": "task_package_import",
                    # supersede 痕迹：被替代绑定（旧 task_id/owner uid/library）
                    # 与本次重绑的中转表名——审计可复原"哪一行被哪一次导入
                    # 取代"，无需读取已删除的中转表。
                    "supersede": {
                        "task_id": old_task_id,
                        "owner_user_id": int(record["owner_user_id"]),
                        "owner_library_id": record["owner_library_id"],
                        "coverage_state": coverage,
                    },
                    "staging_table": _PROTOCOL_REBIND_STALE_TABLE,
                    "guard_recreated_verbatim": True,
                    "guard_names": [
                        "team_task_protocol_guard_update",
                        "team_task_protocol_no_delete",
                    ],
                },
                "recorded_at": rebound_at,
                "source_registration_id": None,
                "source_generation": None,
            })
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    except TaskPackageError:
        raise
    except Exception as exc:  # noqa: BLE001 - 重绑任何失败=typed 导入失败
        # registry 强读/布局断言（TerminalRegistryUnavailable 等 RuntimeError）
        # 与坏库/锁忙同口径：绝不逃逸成 500，一律 typed import_failed 并清场。
        raise _rebind_import_failed(
            "team_protocol_rebind_failed",
            detail=f"{type(exc).__name__}: {exc}") from exc
    finally:
        conn.close()

    # 提交后产品终验：精确布局（含逐字守卫）＋按新 task_id 正查。
    try:
        _registry.prepare_team_terminal_registry(collab)
        verify = sqlite3.connect(str(collab), timeout=15)
        try:
            verify.row_factory = sqlite3.Row
            verify.execute("BEGIN")
            final = _registry.read_protocol_for_task_on(
                verify, task_id=task_id)
        finally:
            verify.close()
    except Exception as exc:  # noqa: BLE001 - 终验失败=typed 导入失败（清场）
        raise _rebind_import_failed(
            "team_protocol_rebind_verify_failed",
            detail=f"{type(exc).__name__}: {exc}") from exc
    if final is None or final["task_instance_id"] != instance:
        raise _rebind_import_failed("team_protocol_rebind_verify_failed")
    return {
        "rebound": True,
        "old_task_id": old_task_id,
        "new_task_id": task_id,
        "rebound_at": rebound_at,
        "actor_user_id": owner_id,
        "importer_username": owner_name,
        "receipt_id": receipt["receipt_id"],
        "evidence_recomposed": evidence_recomposed,
        "message_key": IMPORT_PROTOCOL_REBIND_MESSAGE_KEY,
        "params": {"old_task_id": old_task_id, "new_task_id": task_id},
        "message": IMPORT_PROTOCOL_REBIND_MESSAGE,
    }


def _import_team_provenance(task_dir: Path, task_id: str, owner_id: int,
                            owner_name: str) -> dict[str, Any]:
    """P18 需求 2：整包导入/恢复的协议绑定 hook（恢复不悄换 instance/library）。

    - 协作库内已有 ``team_task_protocol`` 行（带历史 task-package）：原样保留
      其 ``task_instance_id`` / ``owner_library_id``（恢复绝不静默改换
      instance/library）；本 hook 只补装缺失的 F2/F1/receipts schema（P14
      ``prepare_team_protocol_storage``，幂等）。缺失/冲突的成员源**不自动
      隔离**——留给 owner 显式 repair/adoption 处置。
    - 无 protocol 行（fresh 语义，含包内无协作库时新建的空库）：登记
      ``creation_path="trusted_new_task"`` protocol 行，与带历史包区分。
      owner_library_id 取导入者个人库的**真实** P04 账本身份（S-3：init_db
      安装账本生成新 UUID，不登记猜想占位值；registry 身份守卫不可后改）。

    任何登记失败如实上抛（import 失败清场，不留半登记状态）；fresh 登记
    属于导入原子边界的一部分（temp_dir 整体改名）。
    """
    import uuid as _uuid
    from contextlib import closing as _closing

    from custom_backend import team_source_lifecycle as team_lifecycle
    from coscreen import team_terminal_registry as _registry
    from coscreen.team_vote_ledger import library_identity_header_on

    collab = task_dir / "collaboration.db"
    if not collab.is_file():
        return {"has_history": False, "registered": False,
                "reason": "no_collaboration_store"}
    team_lifecycle.prepare_team_protocol_storage(
        collab_path=collab, source_paths=())
    history_row: sqlite3.Row | None = None
    conn = sqlite3.connect(str(collab), timeout=15)
    try:
        conn.row_factory = sqlite3.Row
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if "team_task_protocol" in tables:
            history_row = conn.execute(
                "SELECT task_instance_id, task_id, owner_library_id,"
                " coverage_state FROM main.team_task_protocol"
                " ORDER BY created_at LIMIT 1").fetchone()
    finally:
        conn.close()
    if history_row is not None:
        # R8（用户裁定 a，2026-11-21）：带历史包在导入组装窗口内受控重绑
        # 协议到新 task_id（守卫触发器禁 UPDATE；经产品 canonical DDL 单事务
        # 重建，守卫逐字重建不削弱）＋迁移审计 receipt＋响应提示。
        # instance/library 原样保留（P18 契约）。重绑开启自持连接，故上面
        # 的读连接必须先关（SQLite 单写者；同进程双连接会自锁）。
        rebound = _rebind_imported_team_protocol(
            task_dir, task_id, owner_id, owner_name,
            str(history_row["task_instance_id"]))
        return {"has_history": True, "registered": False,
                "preserved_task_instance_id": history_row["task_instance_id"],
                "preserved_task_id": history_row["task_id"],
                "preserved_owner_library_id": history_row["owner_library_id"],
                "coverage_state": history_row["coverage_state"],
                "rebound": bool(rebound.get("rebound")),
                "old_task_id": rebound.get("old_task_id"),
                "new_task_id": task_id,
                "rebound_at": rebound.get("rebound_at"),
                "rebind_receipt_id": rebound.get("receipt_id"),
                "rebind_message_key": rebound.get("message_key"),
                "rebind_params": rebound.get("params"),
                "rebind_message": rebound.get("message")}
    conn = sqlite3.connect(str(collab), timeout=15)
    try:
        conn.row_factory = sqlite3.Row
        # S-3：fresh 登记的 owner_library=导入者个人库真实账本身份。
        from coscreen import db as _cdb
        slug = tasks_mod.slugify_screener(owner_name)
        owner_db = task_dir / f"{slug}.db"
        if not owner_db.is_file():
            _cdb.init_db(owner_db)
        own = sqlite3.connect(str(owner_db), timeout=15)
        try:
            own.row_factory = sqlite3.Row  # C02 借用合同
            own.execute("BEGIN")
            header = library_identity_header_on(own)
        finally:
            own.close()
        owner_library = (header.library_id if header is not None else None)
        if not owner_library:
            raise TaskPackageError("import_failed", params={
                "reason": "owner_ledger_identity_unavailable"})
        conn.execute("BEGIN IMMEDIATE")
        record = _registry.register_protocol_on(
            conn, task_id=task_id,
            task_instance_id=f"{task_id}-inst-import-{_uuid.uuid4().hex[:12]}",
            owner_user_id=owner_id,
            owner_library_id=owner_library,
            creation_path="trusted_new_task")
        conn.commit()
        return {"has_history": False, "registered": True,
                "task_instance_id": record["task_instance_id"],
                "creation_path": "trusted_new_task"}
    finally:
        conn.close()


def _prepare_imported_team_storage(task_dir: Path, task_id: str,
                                   owner_name: str,
                                   member_slugs: Iterable[str],
                                   provenance: Mapping[str, Any]) -> dict[str, Any]:
    """E7/P3（GAP_DESIGN_SURVEY 2026-11-20 §2 E7+§7 P3）：导入后团队存储
    准备对齐（owner+成员库 schema 准备、F2 旧 source 行身份对齐）。

    缺口（survey E7）：整包导入只对 collab 调 ``prepare_team_protocol_storage``
    （``source_paths=()``），owner/成员库按包内**原样**落盘——旧包成员库可
    无 F1/receipts；且 ``_rewrite_collaboration_store`` 只清 grants/settings，
    旧部署 business-role **choice** 随 collab 残留（属主 choice 恒有效）。
    导入后属主 coder choice 命中同 uid（典型=单人部署自导自恢复）时首次
    appraisal 直调 ``runtime.mutate``（无 A hook）→ actor receipts 读 →
    未处理 500；成员库同因缺 receipts 首写 500。

    修法（「导入时即准备」根治，不依赖 P1 写路径 auto-prepare）：导入原子
    边界内（temp dir 整体改名前）对属主+全部成员库执行与邀请流程同款的
    **两步准备**——复用产品函数 ``main._prepare_invited_member_storage``
    （appraisal ensure + P14 ``prepare_team_protocol_storage``），零手写
    DDL。任何准备失败如实 typed 拒绝**整个导入**（失败清场、不留半准备
    状态；与协作库改绑失败=包无效同一口径；准备幂等、重试安全）。

    F2 身份对齐（survey E7/§5.4：旧 source 行可能指向旧库身份 → 发现层
    issue）：带历史包（protocol 行随包保留）下以成员库**真实 ledger 身份**
    为连接键，经产品原语 ``retire_source_on``/``register_source_on`` 在短
    collab 事务内幂等对齐 active 行：
    - 行身份在本地成员库存在 → 已对齐，原样保留；
    - 行身份本地缺失且有未被任何 active 行认领的本地库 → **失配**：旧行
      retire（``imported_source_identity_mismatch``）+ 原行 uid/真实身份
      重登记（新 generation；token 与运行时 ``make_source_identity`` 同
      口径，不手写）；
    - 行身份本地缺失且无可认领库 → 旧行 retire（
      ``imported_source_library_absent``）——失效，不再指向不存在的库。
    fresh 导入（无 protocol 行）无存量行：只做准备，不做对齐。对齐失败
    如实 typed 上抛（import 失败清场）。
    """
    from types import SimpleNamespace as _ShimInfo
    from custom_backend import main as main_mod
    from custom_backend import team_source_lifecycle as team_lifecycle
    from custom_backend.appraisal_api import prepare_appraisal_storage_for
    from coscreen import team_terminal_registry as _registry
    from coscreen.team_vote_ledger import library_identity_header_on

    owner_slug = tasks_mod.slugify_screener(owner_name)
    collab = task_dir / "collaboration.db"

    # 作用域闸：E7 缺口只在**带历史团队包**（collab+protocol 行随包、choice
    # 可残留）可达——choice 存于 collab，fresh 导入（trusted_new_task）无
    # 团队语境（登记前属主写为 solo 路径），且 legacy/个人包成员库可为任意
    # 非 sqlite 形状、按包内原样落盘是既有契约（2.1.5 导入回归钉）。故准备
    # 与对齐仅在 has_history 下执行，fresh 如实上报 skip。
    if not provenance.get("has_history"):
        return {"skipped": "fresh_import_no_team_history",
                "owner_slug": owner_slug}

    # -- 1) 属主+成员库 schema 准备（复用产品两步；失败=typed 导入失败） ----
    # targets[0]=属主（包内缺库时由 _screener_db ensure 按产品原语建）；其余
    # 为包内实际落盘的成员库（manifest db_members，缺文件者跳过不凭空建库）。
    targets: list[str] = [owner_slug]
    seen: set[str] = {owner_slug}
    for slug in member_slugs:
        if slug and slug not in seen:
            seen.add(slug)
            targets.append(slug)
    shim = _ShimInfo(dir=task_dir, task_id=task_id)
    for slug in targets:
        db_path = task_dir / f"{slug}.db"
        if slug != owner_slug and not db_path.is_file():
            continue  # 包内本就无该成员库：不凭空建（登记语义归入场入口）
        if tasks_mod.slugify_screener(slug) == slug:
            # 常规路径：邀请流程产品函数整函数复用（username→slug 恒等，
            # 路径解析/collab 撞名守卫与邀请逐字一致）。
            result = main_mod._prepare_invited_member_storage(
                shim, {"username": slug})
            if not result.get("library_prepared"):
                raise TaskPackageError("import_failed", params={
                    "reason": "team_storage_prepare_failed", "member": slug,
                    "detail": result.get("prepare_error")})
            continue
        # 非常规库 stem（slug 不可逆的历史包名）：同款两步产品原语、显式
        # 路径（appraisal prepare 的宿主解析器直接绑定该库路径）。
        try:
            prepare_appraisal_storage_for(
                shim, slug, lambda _info, _name, _p=db_path: _p)
            team_lifecycle.prepare_team_protocol_storage(
                collab_path=collab, source_paths=(db_path,))
        except Exception as exc:  # noqa: BLE001 - 准备失败=导入失败（清场）
            raise TaskPackageError("import_failed", params={
                "reason": "team_storage_prepare_failed", "member": slug,
                "detail": f"{type(exc).__name__}: {exc}"}) from exc
    prepared: dict[str, Any] = {"owner_slug": owner_slug,
                                "member_slugs": targets[1:]}

    # -- 2) F2 旧 source 行身份对齐（带历史包才有存量行） -------------------
    alignment: dict[str, int] = {"active_rows": 0, "aligned": 0,
                                 "re-registered": 0, "retired_mismatch": 0,
                                 "retired_absent": 0}
    instance = provenance.get("preserved_task_instance_id")
    if provenance.get("has_history") and instance:
        # 本地 ledger 身份表（属主+成员库；身份不可读的库不参与对齐）。
        local_libs: dict[str, Path] = {}
        for slug in targets:
            db_path = task_dir / f"{slug}.db"
            if not db_path.is_file():
                continue
            try:
                with closing(_open_read_only(db_path)) as ro:
                    # 与 _register_member_source 同口径（C02 借用合同）：Row
                    # 工厂 + 活动事务下读账本身份头。
                    ro.row_factory = sqlite3.Row
                    ro.execute("BEGIN")
                    header = library_identity_header_on(ro)
            except Exception:  # noqa: BLE001 - 身份不可读的库不参与对齐
                continue
            library_id = getattr(header, "library_id", None)
            if library_id:
                local_libs[library_id] = db_path
        conn = sqlite3.connect(str(collab), timeout=15)
        try:
            conn.row_factory = sqlite3.Row  # C02 借用合同
            conn.execute("BEGIN IMMEDIATE")
            rows = _registry.list_sources_on(
                conn, task_instance_id=str(instance), status="active")
            alignment["active_rows"] = len(rows)
            active_libs = {row["source_library_id"] for row in rows}
            claimed: set[str] = set()
            for row in rows:
                library_id = row["source_library_id"]
                if library_id in local_libs:
                    alignment["aligned"] += 1
                    continue
                # 失配行 → 本地库认领：**只按旧行 token 内的相对路径基名**
                # 认领同一成员的库文件（token=make_source_identity 产物，
                # 基名跨部署稳定；绝不跨成员错配——认领不到即失效，不猜）。
                preferred: list[str] = []
                try:
                    token = json.loads(str(row["source_path_identity"]))
                    base = PurePosixPath(str(token.get("relative", ""))).name
                    preferred = [
                        candidate for candidate, path in local_libs.items()
                        if candidate not in claimed
                        and candidate not in active_libs
                        and path.name == base]
                except ValueError:
                    preferred = []
                if preferred:
                    # 失配 → 重登记：retire 旧行后以原行 uid + 真实身份补登
                    # （同事务内 retire 先行，active 唯一性约束不冲突）。
                    chosen = sorted(preferred)[0]
                    uid = int(row["source_user_id"])
                    _registry.retire_source_on(
                        conn, registration_id=row["registration_id"],
                        retired_reason="imported_source_identity_mismatch")
                    _registry.register_source_on(
                        conn, task_instance_id=str(instance),
                        source_user_id=uid, source_library_id=chosen,
                        source_path_identity=team_lifecycle.make_source_identity(
                            task_dir=task_dir, source_path=local_libs[chosen],
                            source_user_id=uid, source_library_id=chosen))
                    claimed.add(chosen)
                    alignment["re-registered"] += 1
                    alignment["retired_mismatch"] += 1
                else:
                    _registry.retire_source_on(
                        conn, registration_id=row["registration_id"],
                        retired_reason="imported_source_library_absent")
                    alignment["retired_absent"] += 1
            conn.commit()
        except Exception as exc:  # noqa: BLE001 - 对齐失败=typed 导入失败
            conn.rollback()
            raise TaskPackageError("import_failed", params={
                "reason": "team_source_alignment_failed",
                "detail": f"{type(exc).__name__}: {exc}"}) from exc
        finally:
            conn.close()
        prepared["source_alignment"] = alignment
    return prepared


def _apply_import(parsed: Mapping[str, Any], package_path: Path, *,
                  user: Mapping[str, Any], data_dir: str | Path) -> dict[str, Any]:
    """在临时目录完整落盘后整体改名（失败清理，不留半成品任务）。"""
    tasks_root = Path(tasks_mod.tasks_root(data_dir)).resolve()
    tasks_root.mkdir(parents=True, exist_ok=True)
    task_payload = dict(parsed["task_payload"])
    # 名称只从包内数据取值；极端缺失时退回可作目录名的中性标识（不新增
    # 面向用户的散文文案——文案只进词典/docs）。
    display_name = (str(task_payload.get("display_name") or "").strip()
                    or str(task_payload.get("task_id") or "").strip()
                    or "imported-task")

    new_id = tasks_mod.new_task_id(display_name)
    while (tasks_root / new_id).exists():
        new_id = tasks_mod.new_task_id(display_name)
    final_dir = _contained(tasks_root, new_id)

    slug_map = _slug_map_for(parsed["db_members"])
    # 导入历史库的 screener 列存的是**展示名**（如邮箱用户名），而数据库文件
    # 用 slug；两条键都要映射到导入后的目标 slug。
    screener_map: dict[str, str] = dict(slug_map)
    for display in task_payload.get("screeners") or []:
        if isinstance(display, str) and display.strip():
            slug = tasks_mod.slugify_screener(display)
            if slug in slug_map:
                screener_map[display] = slug_map[slug]
    temp_dir = Path(tempfile.mkdtemp(prefix=_IMPORT_TMP_PREFIX, dir=tasks_root)).resolve()
    try:
        temp_dir.relative_to(tasks_root)
    except ValueError:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise TaskPackageError("import_failed", params={"reason": "temp_outside_tasks"}) from None
    try:
        db_names: list[str] = []
        extracted_total = 0
        with zipfile.ZipFile(package_path, "r") as archive:
            for name in parsed["db_members"]:
                source_slug = PurePosixPath(name).stem
                target_name = f"{slug_map[source_slug]}.db"
                _extract_member(archive, name, temp_dir, target_name,
                                total_before=extracted_total)
                extracted_total += archive.getinfo(name).file_size
                db_names.append(target_name)

            collab_target = _contained(temp_dir, "collaboration.db")
            if "collaboration.db" in archive.namelist():
                _extract_member(archive, "collaboration.db", temp_dir, "collaboration.db")

            if "import-history.sqlite3" in archive.namelist():
                # 包内成员用连字符（manifest 契约），任务目录运行时文件名是
                # 下划线（main._import_history_db）；导入落位时改回运行时名。
                _extract_member(archive, "import-history.sqlite3", temp_dir,
                                "import_history.sqlite3")
                _rewrite_history(temp_dir, "import_history.sqlite3",
                                 task_id=new_id, screener_map=screener_map)

            owner_id = int(user["user_id"])
            owner_name = str(user["username"])
            if collab_target.is_file():
                # 2.1.5-sec29（发现 3，对齐 fulltexts 对抗口径）：包内协作库
                # 改绑失败 = 该包无效——结构化拒绝并清场，绝不静默 unlink 换
                # 空库假 200（那会丢包内判定/设置且无迹可查）。前置条件已在
                # validate_import_package（预检/导入共同入口）判定；此处兜底
                # 运行期改绑失败（IO/并发写坏等）。
                if not _rewrite_collaboration_store(
                        temp_dir, "collaboration.db", task_id=new_id,
                        owner_id=owner_id, owner_name=owner_name):
                    raise _collaboration_rewrite_error()
            else:
                # 包内无协作库（合法可选成员）：导入者即首任属主，初始化新库。
                task_collaboration.initialize_task_store(
                    temp_dir, task_id=new_id, owner_user_id=owner_id,
                    owner_username=owner_name,
                )
            # P18 需求 2 导入/恢复绑定 hook：带历史包（协议行随包保留原
            # instance/library，绝不悄换；缺失/冲突源不自动隔离）与 fresh
            # 导入（登记 trusted_new_task）在此区分并上报 provenance。
            team_provenance = _import_team_provenance(
                temp_dir, new_id, owner_id, owner_name)
            # E7/P3：导入原子边界内完成属主+成员库 schema 准备与 F2 旧
            # source 行身份对齐（失败=typed 导入失败，temp_dir 整体清场，
            # 不留半准备/半对齐状态）；详见 _prepare_imported_team_storage。
            team_provenance["storage_prepared"] = \
                _prepare_imported_team_storage(
                    temp_dir, new_id, owner_name,
                    tuple(slug_map.values()), team_provenance)

            restored = 0
            covered_paths: dict[str, str] = {}
            if parsed["fulltexts"]:
                rows_by_key = _fulltext_rows_by_key(temp_dir, db_names)
                for restore_index, entry in enumerate(parsed["fulltexts"]):
                    key = entry["zotero_key"]
                    archive_path = entry["archive_path"]
                    # 就地校验：与预检同一判定函数（终审 #45「统一按预检」）。
                    if not isinstance(archive_path, str):
                        raise TaskPackageError("package_invalid",
                                               params={"reason": "member_name_invalid"})
                    _validate_member_name(archive_path)
                    targets = rows_by_key.get(key)
                    if not targets:
                        raise TaskPackageError("package_invalid", params={
                            "reason": "fulltext_key_missing", "zotero_key": key})
                    info = archive.getinfo(archive_path)
                    # D1（2.1.5 收尾清债）：PDF 成员流式恢复——先经既有
                    # ``_extract_member`` 有界解压到任务临时目录内的中转文件
                    # （_CHUNK 循环；终审 #41 三限额：单成员 ≤MAX_MEMBER_BYTES
                    # （=MAX_PDF_BYTES）、压缩比、声明/实际一致），再交给
                    # ``save_pdf(data_path=...)`` 流式落盘。旧实现
                    # ``_read_member_bounded`` 把 ≤100MB 成员整个堆进一个
                    # bytearray（峰值 = 成员大小）；新路径峰值 = 单块。
                    # 中转文件用服务器端派生名（restore 序号），成功后立即
                    # 删除（temp_dir 事后整体改名成任务目录，不得残留）；
                    # 失败路径由外层 ``shutil.rmtree(temp_dir)`` 清场。
                    scratch = _extract_member(
                        archive, archive_path, temp_dir,
                        f"pdf-restore-{restore_index}.pdf")
                    first_db = _contained(temp_dir, targets[0])
                    try:
                        meta = fulltext_mod.save_pdf(
                            first_db, key, None, pdfs_dir=temp_dir / "pdfs",
                            data_path=scratch)
                    except ValueError as exc:
                        raise TaskPackageError("package_invalid", params={
                            "reason": "pdf_unreadable", "zotero_key": key}) from exc
                    except sqlite3.IntegrityError as exc:
                        raise TaskPackageError("package_invalid", params={
                            "reason": "fulltext_key_missing", "zotero_key": key}) from exc
                    except sqlite3.OperationalError as exc:
                        # 终审 #40（剩余分支④）：包内库是旧/残缺 schema
                        # （fulltexts 缺 n_pages/added_at 列 → "no such column"）
                        # 此前未捕获 → 500。锁忙类保持 503 重试语义
                        # （与 _fulltext_key_rows 同口径），其余按坏包拒绝；
                        # 外层 except Exception 清场原子回滚不变。
                        text = str(exc).lower()
                        if "locked" in text or "busy" in text:
                            raise TaskPackageError(
                                "storage_unavailable", status=503) from exc
                        raise TaskPackageError("package_invalid", params={
                            "reason": "fulltext_schema_unsupported",
                            "zotero_key": key}) from exc
                    # 成功即删中转文件（temp_dir 稍后整体改名为任务目录；
                    # 异常路径由外层 rmtree(temp_dir) 清场，见上）。
                    scratch.unlink(missing_ok=True)
                    final_path = str(final_dir / "pdfs" / Path(meta.path).name)
                    # filename 列是**展示名**：优先还原 manifest 记录的原名
                    # （旧库可能保留自定义展示名）；磁盘路径始终是 save_pdf
                    # 的安全单射名。
                    display_filename = str(
                        entry.get("original_filename") or meta.filename)
                    for db_name in targets:
                        _update_fulltext_row(
                            temp_dir, db_name, key=key, path=final_path,
                            filename=display_filename, sha256=meta.sha256,
                            n_pages=meta.n_pages)
                    covered_paths[key] = final_path
                    restored += 1

            # 终审 #39：manifest 未覆盖的 fulltexts 行（含指向共享 pdfs 池的
            # 旧行）path 一律清空——绝不把其他任务/遗留文件当本任务全文导出。
            neutralized = _neutralize_unrestored_fulltexts(
                temp_dir, db_names, covered_paths)

        # 解压完成（成功或失败都走得到这里/异常路径）：上传 zip 立即删除
        # （用户裁定不做「zip 不落盘」，落盘副本只活到解压结束；端点内峰值
        # = zip ≤2GB + 解压内容 ≤2GB = ≤4GB 瞬态，互斥下全服务器至多一个
        # 在途；请求体 spool 在端点前、属互斥之外，另由上传在途闸封顶——
        # sec29b-W1 如实口径）。
        package_path.unlink(missing_ok=True)

        # review67 增补 F2（GitNexus P2 复核属实）：捕获原任务号——成员库内嵌
        # trace 表的 task_id 需要随之旧→新重写（见 _atomic_write_task_json 后）。
        old_task_id = str(task_payload.get("task_id") or "")
        task_payload.update({
            "task_id": new_id,
            "display_name": display_name,
            "description": str(task_payload.get("description") or ""),
            "screeners": [slug_map[source] for source in slug_map],
            "created_at": _local_now(),
            "last_used_at": _local_now(),
            "archived": False,
        })
        _atomic_write_task_json(temp_dir, task_payload)
        if old_task_id and old_task_id != new_id:
            # review67 增补 F2：成员库内嵌 trace 五表的 task_id 旧→新重写——
            # 此前事件按旧任务号沉底（轨迹页/摘要/导出按新号查询全读不到）、
            # 记录设置被新任务的缺省初始化覆盖（原「仅初筛」口径丢失）。
            # actor_id 原样保留（原事件作者归属不变）；settings 先删同键缺省
            # 再改写避免主键冲突；表缺失（老库从未启用 trace）静默跳过；
            # 逐库独立事务，任一失败即整包导入失败（不留半改库）。
            for db_file in sorted(temp_dir.glob("*.db")):
                conn = sqlite3.connect(str(db_file))
                try:
                    try:
                        existing = {row[0] for row in conn.execute(
                            "SELECT name FROM sqlite_master WHERE type='table'")}
                    except sqlite3.DatabaseError:
                        # 垃圾/炸弹成员（SecurityLimitsPins 构造的非库字节）——
                        # trace 改写尽力而为，处置交由既有的成员校验路径。
                        continue
                    if not existing & {"trace_settings", "trace_events"}:
                        continue
                    with conn:
                        conn.execute("BEGIN IMMEDIATE")
                        if "trace_settings" in existing:
                            conn.execute(
                                "DELETE FROM trace_settings WHERE task_id=? AND actor_id IN "
                                "(SELECT actor_id FROM trace_settings WHERE task_id=?)",
                                (new_id, old_task_id))
                            conn.execute(
                                "UPDATE trace_settings SET task_id=? WHERE task_id=?",
                                (new_id, old_task_id))
                        # 全字面量四连（execute 位零变量零拼接——Mimosa SQL
                        # 约束；值一律 ? 参数绑定；表缺失经守卫跳过）。
                        if "trace_operations" in existing:
                            conn.execute(
                                "UPDATE trace_operations SET task_id=? WHERE task_id=?",
                                (new_id, old_task_id))
                        if "trace_clear_operations" in existing:
                            conn.execute(
                                "UPDATE trace_clear_operations SET task_id=? WHERE task_id=?",
                                (new_id, old_task_id))
                        if "trace_write_operations" in existing:
                            conn.execute(
                                "UPDATE trace_write_operations SET task_id=? WHERE task_id=?",
                                (new_id, old_task_id))
                        if "trace_events" in existing:
                            conn.execute(
                                "UPDATE trace_events SET task_id=? WHERE task_id=?",
                                (new_id, old_task_id))
                finally:
                    conn.close()
        if final_dir.exists():  # 理论上不可达（new_task_id 已避让），防御性兜底。
            raise TaskPackageError("import_failed", status=409,
                                   params={"reason": "task_id_collision"})
        os.replace(str(temp_dir), str(final_dir))
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise

    return {
        "task_id": new_id,
        "display_name": display_name,
        "member_slug_map": slug_map,
        "db_count": len(db_names),
        "fulltexts_restored": restored,
        "fulltexts_neutralized": neutralized,
        "fulltexts_skipped": len(parsed.get("skipped_fulltexts") or []),
        "package_sha256": parsed["package_sha256"],
        "team_provenance": team_provenance,
    }


async def _read_upload_bounded(upload: UploadFile, limit: int) -> Path:
    """把上传流写入临时文件（有界读取）；超限 413，空体 400。"""
    handle = tempfile.NamedTemporaryFile(prefix="rf-package-upload-", delete=False)
    path = Path(handle.name)
    total = 0
    try:
        while chunk := await upload.read(_CHUNK):
            total += len(chunk)
            if total > limit:
                raise TaskPackageError("package_too_large", status=413, params={
                    "limit_bytes": limit - _UPLOAD_SLACK_BYTES,
                    "total_bytes": total, "pdf_bytes": 0, "pdf_count": 0,
                })
            handle.write(chunk)
    except Exception:
        handle.close()
        path.unlink(missing_ok=True)
        raise
    handle.close()
    if total == 0:
        path.unlink(missing_ok=True)
        raise TaskPackageError("package_invalid", params={"reason": "empty_upload"})
    return path


def _ascii_filename(name: str, fallback: str) -> str:
    ascii_name = name.encode("ascii", "ignore").decode("ascii").strip()
    return ascii_name or fallback


class _CleanupStreamingResponse(StreamingResponse):
    """导出/归档下载流（sec29b-W1）：响应调用结束的**任何**路径都确定关闭
    异步生成器，从而确定触发其盾化 finally（删 zip/工作目录、释放整包
    操作锁）。

    StreamingResponse 的取消分支（ASGI spec 2.3 任务组，uvicorn 实况）里，
    取消可能落在 ``send()`` 而非 ``__anext__()``——生成器停在 yield 上，
    只能等 GC 的 asyncgen 收尾（非确定时序）。本子类在 ``__call__`` 的
    finally 里用 :class:`anyio.CancelScope`\\ ``(shield=True)`` 盾化
    ``aclose()``：GeneratorExit 立即抛进生成器帧执行清理。幂等：正常送完
    （生成器已耗尽）或清理已跑过时 ``aclose()`` 为空操作，不会二次执行
    finally 里的删除/释放。
    """

    async def __call__(self, scope, receive, send) -> None:  # noqa: ANN001
        try:
            await super().__call__(scope, receive, send)
        finally:
            close = getattr(self.body_iterator, "aclose", None)
            if close is None:
                return
            try:
                with anyio.CancelScope(shield=True):
                    await close()
            except (RuntimeError, StopAsyncIteration):
                # 生成器已在收尾/从未启动：清理已由其 finally 完成。
                pass


def register_task_package_routes(
    app,
    require_user: Callable[..., dict],
    task_lookup: Callable[[str], Any],
    task_visible: Callable[[Any, dict], bool] | None = None,
    server_mode: Callable[[], bool] | None = None,
    data_dir: str | Path = "data",
    upload_rules: list | None = None,
) -> None:
    """在 ``app`` 上注册 W5 端点（main.py 注册区一行调用）。"""

    def _task_info(task_id: str, user: dict) -> Any:
        info = task_lookup(task_id)
        if task_visible is not None and not task_visible(info, user):
            raise _error(TaskPackageError("task_forbidden", status=403))
        return info

    def _manage_info(task_id: str, user: dict) -> Any:
        """导出闸：任务属主（desktop legacy 本人放行 / server legacy 409）。"""
        info = _task_info(task_id, user)
        store = _collaboration_store(info)
        try:
            owner = task_collaboration.get_owner(store, task_id=info.task_id)
        except task_collaboration.CollaborationStoreCorruptError as exc:
            raise _error(TaskPackageError("storage_corrupt", status=409)) from exc
        except task_collaboration.OwnerUnavailableError as exc:
            raise _error(TaskPackageError("storage_unavailable", status=503)) from exc
        if owner is None:
            if server_mode is not None and server_mode():
                raise _error(TaskPackageError("legacy_owner_unset", status=409))
            return info
        if int(owner["owner_user_id"]) != int(user["user_id"]):
            raise _error(TaskPackageError("write_forbidden", status=403))
        return info

    def _stream_file(path: Path, media_type: str, headers: dict[str, str],
                     cleanup_dir: Path | None = None,
                     finally_hook: Callable[[], None] | None = None) -> StreamingResponse:
        actual = Path(path).resolve()
        root = Path(tempfile.gettempdir()).resolve()
        try:
            actual.relative_to(root)
        except ValueError:
            if finally_hook is not None:
                finally_hook()
            raise _error(TaskPackageError("storage_unavailable", status=503)) from None
        if not actual.is_file():
            if finally_hook is not None:
                finally_hook()
            raise _error(TaskPackageError("storage_unavailable", status=503))

        async def generator():
            # sec29b-W1（复审 u/l 真实 uvicorn 实测）：同步生成器在 ASGI
            # spec 2.3 的任务组取消分支（uvicorn 实况）被整支遗弃——
            # iterate_in_threadpool 的桥断开后 finally 不执行，客户端读到
            # 响应头即断开 → 锁悬挂（>2s 实测、依赖 GC 才补跑）+ 工作目录
            # 泄漏 + 后续导出 409。改用**异步生成器**承载：取消/断开异常
            # 会抛进生成器帧，finally 确定执行；清理放在
            # anyio.CancelScope(shield=True) 里——外部取消不得再打断锁
            # 释放与目录删除（盾化保证清理内的检查点不被二次取消）。
            # finally 同时覆盖：正常送完、客户端中断下载（生成器被取消/
            # 关闭）、读取中途 IO 故障——zip/工作目录即刻删除、整包操作锁
            # 即刻释放（终审 #41：下载在途也算操作在途，否则「下载中 +
            # 新导入」可叠 6GB）。
            try:
                with open(actual, "rb") as handle:
                    while chunk := await run_in_threadpool(handle.read, _CHUNK):
                        yield chunk
            finally:
                with anyio.CancelScope(shield=True):
                    actual.unlink(missing_ok=True)
                    if cleanup_dir is not None:
                        shutil.rmtree(cleanup_dir, ignore_errors=True)
                    if finally_hook is not None:
                        finally_hook()

        return _CleanupStreamingResponse(generator(), media_type=media_type,
                                         headers=headers)

    # ---- 全文规范归档导出 ------------------------------------------------
    @app.post("/api/tasks/{task_id}/fulltext/archive-export")
    async def archive_export(task_id: str, body: ArchiveExportIn | None = Body(default=None),
                             user: dict = Depends(require_user)):
        info = _manage_info(task_id, user)
        scope = body.scope if body is not None else "all"
        _sweep_stale_package_temp(Path(tasks_mod.tasks_root(data_dir)))

        def _build() -> tuple[Path, dict[str, int], Path]:
            items, _skipped_keys, reasons = _archive_candidates(info, data_dir, scope)
            # 终审 #44：总量上限与整包导出对齐（MAX_PACKAGE_BYTES=2GB）。预检在
            # 打开 zip **之前**完成——超限时零文件字节流开始、零临时 zip 落盘。
            declared_total = sum(item["path"].stat().st_size for item in items)
            if declared_total > MAX_ARCHIVE_EXPORT_BYTES:
                raise TaskPackageError("archive_too_large", status=413, params={
                    "limit_bytes": MAX_ARCHIVE_EXPORT_BYTES,
                    "total_bytes": declared_total,
                })
            workdir = Path(tempfile.mkdtemp(prefix="rf-archive-export-"))
            try:
                zip_path = _contained(workdir, "archive.zip")
                allowed_roots = _fulltext_allowed_roots(info)
                written_total = 0
                with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED,
                                     compresslevel=6) as archive:
                    for item in items:
                        _digest, size = _add_file_to_zip(
                            archive, item["archive_name"], item["path"],
                            allowed_roots=allowed_roots)
                        written_total += size
                        # 流中复核（预检后文件仍在长大/被替换的兜底）：超限即刻
                        # 中止并清场（上方 except 分支删 workdir）。
                        if written_total > MAX_ARCHIVE_EXPORT_BYTES:
                            raise TaskPackageError("archive_too_large", status=413, params={
                                "limit_bytes": MAX_ARCHIVE_EXPORT_BYTES,
                                "total_bytes": written_total,
                            })
            except Exception:
                shutil.rmtree(workdir, ignore_errors=True)
                raise
            return zip_path, {
                "exported": len(items),
                "skipped": reasons["missing_file"] + reasons["unreadable"],
                "missing_file": reasons["missing_file"],
                "unreadable": reasons["unreadable"],
            }, workdir

        # 全局整包操作互斥（导入/整包导出/归档导出共用一把锁，终审 #41）；
        # 抢不到锁直接 409（在 try 外——不进释放分支）。
        _acquire_package_op_lock_or_409()
        try:
            zip_path, summary, workdir = await _run_or_error(_build)
        except BaseException:
            _PACKAGE_OP_LOCK.release()
            raise
        filename = _ascii_filename(f"fulltext_archive_{info.task_id}.zip",
                                   "fulltext_archive.zip")
        headers = {
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
            "X-Archive-Exported": str(summary["exported"]),
            "X-Archive-Skipped": str(summary["skipped"]),
            "X-Archive-Summary": f"{summary['exported']}/{summary['skipped']}",
            "X-Archive-Skipped-Reasons": (
                f"missing_file={summary['missing_file']},"
                f"unreadable={summary['unreadable']}"
            ),
        }
        # 摘要响应头是契约的一部分：无法用消息体承载（响应体必须是合法 zip）。
        # 锁在流式生成器 finally（送完/客户端中断/IO 故障）释放。
        return _stream_file(zip_path, "application/zip", headers, cleanup_dir=workdir,
                            finally_hook=_PACKAGE_OP_LOCK.release)

    # ---- 整包导出体检 ----------------------------------------------------
    @app.get("/api/tasks/{task_id}/package/preflight")
    async def package_preflight(task_id: str, include_pdfs: bool = Query(default=True),
                                user: dict = Depends(require_user)) -> dict:
        info = _manage_info(task_id, user)
        _sweep_stale_package_temp(Path(tasks_mod.tasks_root(data_dir)))
        # 全局整包操作互斥（2.1.5-sec29 发现 1 与 review67 L-29 同裁定）：
        # 体检对全部成员库做 SQLite backup 快照（rf-package-preflight- 工作
        # 目录，单次瞬态可达 ~2GB），与持锁在途的导入/导出叠加会突破模块头
        # 「磁盘峰值恒定 ≤4GB 瞬态」承诺——与导出/归档/导入同一把全局互斥
        # 锁，非阻塞抢锁；抢不到 409 operation_busy（与其他端点同一口径）。
        _acquire_package_op_lock_or_409()
        try:
            return await _run_or_error(_package_inventory, info, data_dir,
                                       include_pdfs)
        finally:
            _PACKAGE_OP_LOCK.release()

    # ---- 整包导出 --------------------------------------------------------
    @app.post("/api/tasks/{task_id}/package/export")
    async def package_export(task_id: str, body: PackageExportIn | None = Body(default=None),
                             user: dict = Depends(require_user)):
        info = _manage_info(task_id, user)
        include_pdfs = True if body is None else bool(body.include_pdfs)
        _sweep_stale_package_temp(Path(tasks_mod.tasks_root(data_dir)))

        def _build() -> tuple[Path, dict[str, Any], Path]:
            workdir = Path(tempfile.mkdtemp(prefix="rf-package-export-"))
            try:
                zip_path, summary = _build_package_zip(
                    info, data_dir, include_pdfs, workdir)
            except Exception:
                shutil.rmtree(workdir, ignore_errors=True)
                raise
            return zip_path, summary, workdir

        # 全局整包操作互斥（导入/整包导出/归档导出共用一把锁，终审 #41）；
        # 抢不到锁直接 409（在 try 外——不进释放分支）。
        _acquire_package_op_lock_or_409()
        try:
            zip_path, summary, workdir = await _run_or_error(_build)
            digest = await _run_or_error(_sha256_file, zip_path)
        except BaseException:
            _PACKAGE_OP_LOCK.release()
            raise
        filename = _ascii_filename(f"task_package_{info.task_id}.zip", "task_package.zip")
        headers = {
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
            "X-Package-Sha256": digest,
            "X-Package-Members": str(summary["summary"]["members"]),
            "X-Package-Pdfs": str(summary["summary"]["pdf_exported"]),
            "X-Package-Pdfs-Skipped": str(summary["summary"]["pdf_skipped"]),
            "X-Package-Bytes": str(summary["total_bytes"]),
        }
        return _stream_file(zip_path, "application/zip", headers, cleanup_dir=workdir,
                            finally_hook=_PACKAGE_OP_LOCK.release)

    # ---- 导入体检 --------------------------------------------------------
    @app.post("/api/tasks/import-package/preflight")
    async def import_package_preflight(
        package: UploadFile = File(..., alias="package"),
        user: dict = Depends(require_user),
    ) -> dict:
        _sweep_stale_package_temp(Path(tasks_mod.tasks_root(data_dir)))
        # 全局整包操作互斥（2.1.5-sec29 发现 1：导入体检落 2GB 上传暂存 +
        # 全成员双遍哈希——与 import-package 同一把锁、同 409 语义）。
        # 注意（sec29b-W1 如实口径）：multipart 请求体在进入本端点前已被
        # 解析层 spool（>1MB part 落盘），本锁挡不住那段——预端点的 spool
        # 峰值由 _PackageUploadInflightGate（ASGI 层，上限 4）封顶。
        _acquire_package_op_lock_or_409()
        upload_path: Path | None = None
        try:
            upload_path = await _read_upload_bounded(
                package, MAX_PACKAGE_BYTES + _UPLOAD_SLACK_BYTES)
            parsed = await run_in_threadpool(validate_import_package, upload_path)
        except TaskPackageError as exc:
            raise _error(exc) from exc
        finally:
            if upload_path is not None:
                upload_path.unlink(missing_ok=True)
            _PACKAGE_OP_LOCK.release()
        manifest = parsed["manifest"]
        return {
            "package_sha256": parsed["package_sha256"],
            "schema": manifest.get("schema"),
            "source_task": manifest.get("source_task") or {},
            "members": parsed["members"],
            "slug_map": _slug_map_for(parsed["db_members"]),
            "fulltexts": {
                "count": len(parsed["fulltexts"]),
                "skipped": len(parsed.get("skipped_fulltexts") or []),
            },
            "total_bytes": parsed["total_bytes"],
            "warnings": (["fulltext_files_missing"]
                         if parsed.get("skipped_fulltexts") else []),
        }

    # ---- 导入（上传 → 校验 → 原子落盘） ---------------------------------
    @app.post("/api/tasks/import-package")
    async def import_package(
        package: UploadFile = File(..., alias="package"),
        preview_sha256: str | None = Form(default=None),
        user: dict = Depends(require_user),
    ) -> dict:
        _sweep_stale_package_temp(Path(tasks_mod.tasks_root(data_dir)))
        # 全局整包操作互斥：读上传之前抢锁——两个并发导入不会各自缓冲 2GB；
        # 抢不到直接 409（在 try 外，不进 finally 释放分支）。预端点的
        # multipart spool 不在本锁管辖内（sec29b-W1 如实口径，由
        # _PackageUploadInflightGate 封顶）。
        _acquire_package_op_lock_or_409()
        upload_path: Path | None = None
        try:
            upload_path = await _read_upload_bounded(
                package, MAX_PACKAGE_BYTES + _UPLOAD_SLACK_BYTES)
            parsed = await run_in_threadpool(validate_import_package, upload_path)
            if preview_sha256 is not None:
                if not preview_sha256.isascii() \
                        or not _SHA256_RE.match(preview_sha256.lower()):
                    raise TaskPackageError("package_invalid",
                                           params={"reason": "preview_sha256_invalid"})
                if preview_sha256.lower() != parsed["package_sha256"]:
                    raise TaskPackageError("package_preview_mismatch", status=409)
            result = await run_in_threadpool(
                _apply_import, parsed, upload_path, user=user, data_dir=data_dir)
        except TaskPackageError as exc:
            raise _error(exc) from exc
        finally:
            if upload_path is not None:
                upload_path.unlink(missing_ok=True)
            _PACKAGE_OP_LOCK.release()
        return result

    # 上传中间件预检规则：整包可到 2GB，必须在 multipart 解析前放行同一上限
    # （主程序 rules 表按路径匹配；模块只追加两条，不改其他端点）。
    if upload_rules is not None:
        limit = MAX_PACKAGE_BYTES + _UPLOAD_SLACK_BYTES
        upload_rules.append((re.compile(r"^/api/tasks/import-package$"), limit, 1))
        upload_rules.append((re.compile(r"^/api/tasks/import-package/preflight$"), limit, 1))
    # sec29b-W1：上传在途并发闸（ASGI 层、multipart 解析之前——互斥锁在
    # 端点内抢、挡不住预端点 spool，见 _PackageUploadInflightGate）。注册
    # 必须发生在应用启动前（本函数在 main 导入期被调用，早于首个请求）；
    # 同一 app 重复注册只装一层（幂等标记）。
    if not getattr(app.state, "_package_upload_inflight_gate", False):
        app.add_middleware(_PackageUploadInflightGate)
        app.state._package_upload_inflight_gate = True
