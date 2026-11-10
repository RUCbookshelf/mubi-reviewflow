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
* **全局整包操作互斥**：导入/整包导出/归档导出共用一把进程级锁，同一时刻
  只允许一个整包操作在途，第二个请求 409 ``task_package.operation_busy``；
  磁盘峰值恒定 ≤4GB 瞬态（导入 zip 2GB + 解压 2GB）。导出锁在下载送完或
  客户端中断时释放（下载在途也算操作在途）。
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

#: 全局整包操作互斥（导入/整包导出/归档导出共用一把锁，用户 2026-11-09
#: 裁定）：同一时刻全服务器（进程内）只允许一个整包操作在途——磁盘峰值从
#: 「并发数 × 2GB」封死为恒定 ≤4GB 瞬态（导入 2+2 或导出 2）。第二个请求
#: 结构化 409 ``task_package.operation_busy``。导出侧锁在下载完成/客户端
#: 中断（流式生成器 finally）时释放，避免「下载中 + 新导入」的 6GB 叠加。
_PACKAGE_OP_LOCK = threading.Lock()


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
    """
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
        for source in collected["sources"]:
            digest, size = _add_file_to_zip(
                archive, source["arcname"], source["path"], allowed_roots=allowed_roots)
            members.append({"path": source["arcname"], "sha256": digest, "size": size})
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
                "total_bytes": collected["total_bytes"],
            },
        }
        archive.writestr(_zip_info("manifest.json"), _json_bytes(manifest))

    return zip_path, {"summary": manifest["summary"], "total_bytes": collected["total_bytes"]}


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
    """
    try:
        with closing(_open_rw(base, name)) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
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
            if not (collab_target.is_file() and _rewrite_collaboration_store(
                    temp_dir, "collaboration.db", task_id=new_id, owner_id=owner_id,
                    owner_name=owner_name)):
                collab_target.unlink(missing_ok=True)
                task_collaboration.initialize_task_store(
                    temp_dir, task_id=new_id, owner_user_id=owner_id,
                    owner_username=owner_name,
                )

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
        # （用户裁定不做「zip 不落盘」，落盘副本只活到解压结束；峰值 =
        # zip ≤2GB + 解压内容 ≤2GB = ≤4GB 瞬态，全局互斥下全服务器恒定）。
        package_path.unlink(missing_ok=True)

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

        def generator():
            # finally 同时覆盖：正常送完、客户端中断下载（生成器被关闭）、
            # 读取中途 IO 故障——zip/工作目录即刻删除、整包操作锁即刻释放
            # （终审 #41：下载在途也算操作在途，否则「下载中 + 新导入」可叠 6GB）。
            try:
                with open(actual, "rb") as handle:
                    while chunk := handle.read(_CHUNK):
                        yield chunk
            finally:
                actual.unlink(missing_ok=True)
                if cleanup_dir is not None:
                    shutil.rmtree(cleanup_dir, ignore_errors=True)
                if finally_hook is not None:
                    finally_hook()

        return StreamingResponse(generator(), media_type=media_type, headers=headers)

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
        return await _run_or_error(_package_inventory, info, data_dir, include_pdfs)

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
        # 抢不到直接 409（在 try 外，不进 finally 释放分支）。
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
