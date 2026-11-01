"""持久化文献导入记录与上传原件。"""

from __future__ import annotations

import json
import hashlib
import logging
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

logger = logging.getLogger(__name__)

#: 连接忙等上限（毫秒）。历史库与任务库同目录、被并发导入与列表端点共享：
#: 原先沿用 sqlite3 默认 5 000ms 且 journal=delete，另一进程持写锁超过 5s
#: 时 save_import 直接抛 ``OperationalError: database is locked``（r9 夜扫
#: b06·F4-71 实测 5.48s）。放宽忙等并切 WAL：读不阻塞写、写不阻塞读，写写
#: 之间排队而非立即失败。
_BUSY_TIMEOUT_MS = 15000

#: 失败导入的原件保留期（秒）：失败批次的原始字节只用于排障，到期后在
#: 下一次写入时清理；历史行与失败原因永久保留。刻意不立即删除——失败路径
#: 也覆盖「catalogue 已变，请重试」这类可重试 409，立刻删原件会破坏重试。
FAILED_FILE_RETENTION_SECONDS = 7 * 24 * 3600

#: 每个 (task_id, screener) 作用域保留的最新导入条数。历史库此前只增不删：
#: 每次导入（含失败导入）都把原件 BLOB（单次上限 20×100MB）永久留在库里
#: （r9 夜扫 b06·F4-72）。保留策略按作用域裁剪最旧行并级联删除原件；本次
#: 写入的行恒在保留窗口内，合法大导入不会被拒绝，不构成 R2。
MAX_RETAINED_IMPORTS = 200


def _connect(path: str | Path) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=_BUSY_TIMEOUT_MS / 1000)
    conn.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
    # WAL 是库文件级持久设置；对既有库是幂等切换，对并发读者无破坏。
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS imports (
            import_id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL,
            screener TEXT NOT NULL,
            imported_at TEXT NOT NULL,
            import_type TEXT NOT NULL CHECK (import_type IN ('single', 'batch')),
            stats_json TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS imports_scope_idx
            ON imports(task_id, screener, imported_at DESC);
        CREATE TABLE IF NOT EXISTS import_files (
            import_id TEXT NOT NULL REFERENCES imports(import_id) ON DELETE CASCADE,
            file_index INTEGER NOT NULL CHECK (file_index >= 0),
            filename TEXT NOT NULL,
            content BLOB NOT NULL,
            sha256 TEXT NOT NULL,
            PRIMARY KEY(import_id, file_index)
        );
    """)
    return conn


def delete_import(
    path: str | Path, import_id: str, *,
    task_id: str | None = None, screener: str | None = None,
) -> bool:
    """删除一条导入历史记录及其原件；返回是否删除。

    r9 夜扫 b06·F4-72：历史库此前没有任何删除入口（失败导入也永久保留原件
    BLOB）。作用域参数可选：给出时只有匹配 task/screener 的行可被删除，
    与读路径的范围口径一致。
    """
    with closing(_connect(path)) as conn, conn:
        sql = "SELECT 1 FROM imports WHERE import_id=?"
        params: list[object] = [import_id]
        if task_id is not None:
            sql += " AND task_id=?"
            params.append(task_id)
        if screener is not None:
            sql += " AND screener=?"
            params.append(screener)
        if conn.execute(sql, params).fetchone() is None:
            return False
        conn.execute("DELETE FROM import_files WHERE import_id=?", (import_id,))
        return conn.execute(
            "DELETE FROM imports WHERE import_id=?", (import_id,)).rowcount > 0


def _prune_imports(conn: sqlite3.Connection, task_id: str, screener: str) -> int:
    """按保留策略裁剪该作用域最旧的导入行（级联删除原件），返回删除条数。

    只删**本作用域**中排序在最新 ``MAX_RETAINED_IMPORTS`` 之外的行：本次
    刚写入的导入恒在窗口内，合法大导入不会被拒绝（R2）。
    """
    stale = [row[0] for row in conn.execute(
        "SELECT import_id FROM imports WHERE task_id=? AND screener=? "
        "ORDER BY imported_at DESC, import_id DESC LIMIT -1 OFFSET ?",
        (task_id, screener, MAX_RETAINED_IMPORTS),
    ).fetchall()]
    for import_id in stale:
        conn.execute("DELETE FROM import_files WHERE import_id=?", (import_id,))
        conn.execute("DELETE FROM imports WHERE import_id=?", (import_id,))
    return len(stale)


def _prune_failed_files(conn: sqlite3.Connection) -> int:
    """清理**已过保留期**的失败导入原件，返回清理条数（b06·F4-72）。

    失败批次的原始字节只用于排障；超过 ``FAILED_FILE_RETENTION_SECONDS``
    后删除，历史行与失败原因保留。保留期而非立即删除是刻意的：失败路径
    也覆盖「catalogue 在匹配期间变化，请重试」这类**可重试** 409，立刻删
    原件会让用户的重试丢掉原始文件（R2：不得把合法导入路径变难）。
    """
    cutoff = (datetime.now(timezone.utc)
              - timedelta(seconds=FAILED_FILE_RETENTION_SECONDS)
              ).isoformat(timespec="microseconds")
    stale = [row[0] for row in conn.execute(
        "SELECT i.import_id FROM imports i "
        "WHERE i.imported_at < ? "
        "AND json_extract(i.stats_json, '$.status') = 'failed' "
        "AND EXISTS (SELECT 1 FROM import_files f WHERE f.import_id = i.import_id) "
        "ORDER BY i.imported_at",
        (cutoff,),
    ).fetchall()]
    for import_id in stale:
        conn.execute("DELETE FROM import_files WHERE import_id=?", (import_id,))
    return len(stale)


def save_import(
    path: str | Path,
    *,
    task_id: str,
    screener: str,
    import_type: str,
    stats: dict,
    files: Iterable[tuple[str, bytes | str | Path]],
) -> str:
    """Store one successful import and its original files; return its history ID."""
    if import_type not in {"single", "batch"}:
        raise ValueError("import_type must be 'single' or 'batch'")
    import_id = uuid.uuid4().hex
    imported_at = datetime.now(timezone.utc).isoformat(timespec="microseconds")
    serialized_stats = json.dumps(stats, ensure_ascii=False, allow_nan=False)
    try:
        with closing(_connect(path)) as conn, conn:
            conn.execute(
                "INSERT INTO imports VALUES (?, ?, ?, ?, ?, ?)",
                (import_id, task_id, screener, imported_at, import_type, serialized_stats),
            )
            for index, (filename, content) in enumerate(files):
                raw = content if isinstance(content, bytes) else Path(content).read_bytes()
                conn.execute(
                    "INSERT INTO import_files VALUES (?, ?, ?, ?, ?)",
                    (import_id, index, filename, sqlite3.Binary(raw), hashlib.sha256(raw).hexdigest()),
                )
            _prune_imports(conn, task_id, screener)
            _prune_failed_files(conn)
    except sqlite3.OperationalError as exc:
        # r9 夜扫 b06·F4-71：锁等待耗尽后原样抛英文 OperationalError，调用方
        # 只能给用户一个 500。翻译成可展示的中文 ValueError。
        raise ValueError(f"导入历史库暂不可写，请稍后重试（{exc}）。") from exc
    except BaseException:
        # 失败导入不得留下半截行/孤儿 BLOB（b06·F4-72）：清理本次写入的
        # 记录，历史库保持写入前状态。
        try:
            with closing(_connect(path)) as conn, conn:
                conn.execute("DELETE FROM import_files WHERE import_id=?", (import_id,))
                conn.execute("DELETE FROM imports WHERE import_id=?", (import_id,))
        except (sqlite3.Error, ValueError):
            logger.warning("清理失败导入历史 %s 时出错", import_id, exc_info=True)
        raise
    return import_id


def list_imports(
    path: str | Path, *, task_id: str, screener: str, limit: int = 20, offset: int = 0,
) -> tuple[list[dict], int | None]:
    """Return newest history rows scoped to exactly one task and screener.

    ``limit`` 必须 ≥ 1、``offset`` 必须 ≥ 0：此前 ``limit=0`` 时游标不前进
    （按 next 续拉无限空转）、``limit=-1`` 时 next 为负（r9 夜扫 b15·F4-75）。
    HTTP 调用方已用 ``ge=1``/``ge=0`` 挡住，模块层同样拒绝非法分页参数。
    """
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise ValueError("limit 必须是 ≥ 1 的整数")
    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
        raise ValueError("offset 必须是 ≥ 0 的整数")
    with closing(_connect(path)) as conn:
        rows = conn.execute(
            "SELECT import_id, task_id, screener, imported_at, import_type, stats_json "
            "FROM imports WHERE task_id = ? AND screener = ? "
            "ORDER BY imported_at DESC, import_id DESC LIMIT ? OFFSET ?",
            (task_id, screener, limit + 1, offset),
        ).fetchall()
        has_more = len(rows) > limit
        rows = rows[:limit]
        ids = [row[0] for row in rows]
        file_rows = []
        if ids:
            marks = ",".join("?" for _ in ids)
            file_rows = conn.execute(
                "SELECT import_id, file_index, filename, length(content), sha256 "
                f"FROM import_files WHERE import_id IN ({marks}) "
                "ORDER BY import_id, file_index",
                ids,
            ).fetchall()

    files_by_import: dict[str, list[dict]] = {}
    for import_id, index, filename, size_bytes, sha256 in file_rows:
        files_by_import.setdefault(import_id, []).append({
            "id": index,
            "name": filename,
            "size": size_bytes,
            "sha256": sha256,
        })
    imports = [
        {
            "id": row[0],
            "created_at": row[3],
            "result": _decode_stats(row[5]),
            "files": files_by_import.get(row[0], []),
        }
        for row in rows
    ]
    return imports, offset + limit if has_more else None


def _decode_stats(text: object) -> dict:
    """逐行解码 stats_json；损坏行降级为空摘要而不是拖垮整个端点。

    r9 夜扫 b15·F4-74：此前 ``json.loads`` 无防护，一行坏数据令
    ``GET /import-history`` 每次 500（故障半径=整端点）。正常写入（:save_
    import / update_import_stats 均 allow_nan=False）不会产生坏行，触发需
    库外改写/部分写；降级后该行仍出现在列表里，其余行与分页完整。
    """
    try:
        value = json.loads(text) if isinstance(text, (str, bytes, bytearray)) else None
    except (TypeError, ValueError):
        logger.warning("导入历史 stats_json 损坏，已降级为空摘要：%r", text)
        return {}
    return value if isinstance(value, dict) else {}


def get_import_file(
    path: str | Path,
    *,
    task_id: str,
    screener: str,
    import_id: str,
    file_index: int,
) -> tuple[str, bytes] | None:
    """Fetch one original file only through its task and screener scope."""
    with closing(_connect(path)) as conn:
        row = conn.execute(
            "SELECT f.filename, f.content FROM import_files f "
            "JOIN imports i ON i.import_id = f.import_id "
            "WHERE i.task_id = ? AND i.screener = ? AND i.import_id = ? "
            "AND f.file_index = ?",
            (task_id, screener, import_id, file_index),
        ).fetchone()
    return (row[0], bytes(row[1])) if row else None


def update_import_stats(
    path: str | Path, import_id: str, stats: dict, *,
    task_id: str | None = None, screener: str | None = None,
) -> None:
    """Update derived progress only; archived original bytes remain immutable.

    r9 夜扫 b15·F4-73：此前 UPDATE 只按裸 ``import_id`` 匹配且不查 rowcount，
    未知 id 静默 no-op，任何调用方拿到别的作用域的 id 即可跨 screener 改写。
    现支持 task_id/screener 作用域过滤，匹配 0 行时抛 ValueError（未知 id
    不是合法输入，不静默吞掉）。

    另（b06·F4-72）：``status='failed'`` 的导入不再永久占用原始字节——失败
    批次没有产生任何 active 行，原件只用于排障；历史行与失败原因保留。
    """
    payload = json.dumps(stats, ensure_ascii=False, allow_nan=False)
    sql = "UPDATE imports SET stats_json=? WHERE import_id=?"
    params: list[object] = [payload, import_id]
    if task_id is not None:
        sql += " AND task_id=?"
        params.append(task_id)
    if screener is not None:
        sql += " AND screener=?"
        params.append(screener)
    with closing(_connect(path)) as conn, conn:
        changed = conn.execute(sql, params).rowcount
        if changed == 0:
            raise ValueError("导入历史记录不存在或不属于当前任务/筛选员，统计未更新。")
