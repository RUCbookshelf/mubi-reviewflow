"""持久化文献导入记录与上传原件。"""

from __future__ import annotations

import json
import hashlib
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


def _connect(path: str | Path) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
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
    return import_id


def list_imports(
    path: str | Path, *, task_id: str, screener: str, limit: int = 20, offset: int = 0,
) -> tuple[list[dict], int | None]:
    """Return newest history rows scoped to exactly one task and screener."""
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
            "result": json.loads(row[5]),
            "files": files_by_import.get(row[0], []),
        }
        for row in rows
    ]
    return imports, offset + limit if has_more else None


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
