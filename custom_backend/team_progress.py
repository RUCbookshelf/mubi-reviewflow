"""Read aggregate progress for usernames already authorized by the caller."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Iterable

from coscreen import config, tasks


def _safe_task_dir(task_id: str, data_dir: str | Path) -> Path:
    if (not isinstance(task_id, str) or not task_id or len(task_id) > 200
            or any(ch in task_id for ch in "/\\\x00") or task_id in {".", ".."}):
        raise ValueError("invalid task identity")
    root = tasks.tasks_root(data_dir).resolve()
    directory = root / task_id
    try:
        resolved = directory.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise ValueError("task path is unavailable") from exc
    info = tasks.find_task(task_id, data_dir)
    if (info is None or info.degraded or info.task_id != task_id
            or info.dir.resolve() != resolved or resolved.parent != root):
        raise ValueError("task registration is unavailable")
    return resolved


def _participant_names(usernames: Iterable[str]) -> list[str]:
    if isinstance(usernames, (str, bytes)):
        raise ValueError("usernames must be an iterable of authenticated usernames")
    names: list[str] = []
    seen: set[str] = set()
    for username in usernames:
        if (not isinstance(username, str) or not username or username != username.strip()
                or len(username) > 64
                or any(ord(ch) < 32 or ch in "/\\\x00" for ch in username)):
            raise ValueError("invalid authenticated username")
        if username not in seen:
            names.append(username)
            seen.add(username)
    return names


def _readonly_connection(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
    conn.execute("PRAGMA query_only=ON")
    return conn


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _screen_progress(conn: sqlite3.Connection, table: str) -> dict[str, object]:
    if not _has_table(conn, "articles"):
        return {"completed": 0, "total": 0, "updated_at": None}
    total = int(conn.execute(
        "SELECT COUNT(*) FROM articles WHERE is_duplicate_of IS NULL"
    ).fetchone()[0])
    if not _has_table(conn, table):
        return {"completed": 0, "total": total, "updated_at": None}
    completed, updated_at = conn.execute(
        f"SELECT COUNT(*), MAX(d.updated_at) FROM {table} d "
        "JOIN articles a ON a.zotero_key=d.zotero_key "
        "WHERE a.is_duplicate_of IS NULL AND d.decision IN (?, ?, ?)",
        tuple(config.DECISIONS),
    ).fetchone()
    return {"completed": int(completed), "total": total, "updated_at": updated_at}


def _coding_progress(conn: sqlite3.Connection) -> dict[str, object]:
    if not all(_has_table(conn, name) for name in ("articles", "decisions")):
        return {"completed": 0, "total": 0, "updated_at": None}

    # Keep the same stage2-include, then stage1-include fallback as coding_progress().
    queue = []
    if _has_table(conn, "stage2_decisions"):
        queue = [row[0] for row in conn.execute(
            "SELECT s.zotero_key FROM stage2_decisions s "
            "LEFT JOIN articles a ON a.zotero_key=s.zotero_key "
            "WHERE s.decision='include' ORDER BY a.import_order, s.zotero_key"
        )]
    if not queue:
        queue = [row[0] for row in conn.execute(
            "SELECT d.zotero_key FROM decisions d "
            "LEFT JOIN articles a ON a.zotero_key=d.zotero_key "
            "WHERE d.decision='include' ORDER BY a.import_order, d.zotero_key"
        )]
    total = len(queue)
    dimensions = ([row[0] for row in conn.execute("SELECT id FROM coding_dimensions")]
                  if _has_table(conn, "coding_dimensions") else [])
    has_values = _has_table(conn, "coding_values")
    if not dimensions or not queue or not has_values:
        updated_at = (conn.execute("SELECT MAX(updated_at) FROM coding_values").fetchone()[0]
                      if has_values else None)
        return {"completed": 0, "total": total, "updated_at": updated_at}

    queue_set = set(queue)
    filled: dict[int, set[str]] = {}
    for dimension_id, key in conn.execute(
        "SELECT dimension_id, zotero_key FROM coding_values "
        "WHERE TRIM(COALESCE(value, '')) <> ''"
    ):
        if key in queue_set:
            filled.setdefault(int(dimension_id), set()).add(key)
    completed = sum(
        all(key in filled.get(int(dimension_id), set()) for dimension_id in dimensions)
        for key in queue
    )
    updated_at = conn.execute("SELECT MAX(updated_at) FROM coding_values").fetchone()[0]
    return {"completed": completed, "total": total, "updated_at": updated_at}


def list_team_progress(
    task_id: str,
    authorized_usernames: Iterable[str],
    data_dir: str | Path = "data",
) -> list[dict[str, object]]:
    """List counts and timestamps for caller-authorized task users.

    The caller must resolve these usernames from authenticated task membership.
    Missing participant databases are reported as zero progress and are never created.
    """
    task_dir = _safe_task_dir(task_id, data_dir)
    result: list[dict[str, object]] = []
    for username in _participant_names(authorized_usernames):
        slug = tasks.slugify_screener(username)
        path = task_dir / f"{slug}.db"
        try:
            resolved = path.resolve(strict=True)
            resolved.relative_to(task_dir)
        except FileNotFoundError:
            result.append({
                "username": username,
                "stage1": {"completed": 0, "total": 0, "updated_at": None},
                "stage2": {"completed": 0, "total": 0, "updated_at": None},
                "coding": {"completed": 0, "total": 0, "updated_at": None},
            })
            continue
        except (OSError, RuntimeError, ValueError) as exc:
            raise ValueError("participant database path is unavailable") from exc
        if not resolved.is_file():
            raise ValueError("participant database path is unavailable")
        if resolved.parent != task_dir or resolved.name != path.name:
            raise ValueError("participant database path is unavailable")
        with closing(_readonly_connection(resolved)) as conn:
            result.append({
                "username": username,
                "stage1": _screen_progress(conn, "decisions"),
                "stage2": _screen_progress(conn, "stage2_decisions"),
                "coding": _coding_progress(conn),
            })
    return result
