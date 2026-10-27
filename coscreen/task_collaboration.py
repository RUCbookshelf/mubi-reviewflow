"""Task-shared collaboration storage and explicit role checks.

The host creates this store once when creating a new task, using the
authenticated user's ID. Ordinary access checks never create or claim a store,
so existing tasks remain unowned until an explicit migration path is chosen.

Legacy（无属主记录）任务的处理口径（2.1.4 披露）：
- server 模式：legacy 任务在列表中不可见，改名/归档/删除等管理操作与
  协作/仲裁写一律 409 fail-closed（error_code=collaboration_owner_unset，
  需先完成属主迁移），与协作管理端点口径一致；
- desktop 模式：单机安装维持历史行为（legacy 任务对本机可见、可管理），
  属有意保留的向后兼容，不构成多用户安全边界；
- 属主解析异常（锁超时/读失败/库损坏）不再折叠成“无 owner=legacy”，
  而是抛 OwnerUnavailableError（HTTP 层映射 503 重试语义）。
"""

from __future__ import annotations

import logging
import sqlite3
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

DB_FILENAME = "collaboration.db"
ROLES = frozenset({"coder", "reconciler"})

logger = logging.getLogger(__name__)


class OwnerUnavailableError(RuntimeError):
    """Owner lookup could not be completed (lock timeout / read failure).

    与“无属主记录的 legacy 任务”（get_owner 返回 None）不同：这是暂时或
    需人工介入的读取失败，调用方不得把它当成 legacy 处理；HTTP 层应映射
    为 503（可重试）而不是放行或 409 迁移提示。
    """

_ACTION_ROLES = {
    "coding:submit": "coder",
    "coding:read_all": "reconciler",
    "consensus:write": "reconciler",
    "consensus:read": "reconciler",
    "consensus:freeze": "reconciler",
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS collaboration_task (
  singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
  task_id TEXT NOT NULL UNIQUE,
  owner_user_id INTEGER NOT NULL CHECK(owner_user_id > 0),
  owner_username TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS collaboration_role_grants (
  user_id INTEGER NOT NULL CHECK(user_id > 0),
  role TEXT NOT NULL CHECK(role IN ('coder', 'reconciler')),
  granted_by INTEGER NOT NULL CHECK(granted_by > 0),
  granted_at TEXT NOT NULL,
  PRIMARY KEY(user_id, role)
);
"""


def shared_store_path(task_dir: str | Path) -> Path:
    """Return the one shared collaboration DB path for a task directory."""
    return Path(task_dir) / DB_FILENAME


def _user_id(value: object, name: str = "user_id") -> int:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{name} must be a positive authenticated user ID")
    return value


def _task_id(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("task_id must be text")
    value = value.strip()
    if not value or len(value) > 200 or any(ch in value for ch in "/\\\x00"):
        raise ValueError("task_id must contain 1 to 200 safe characters")
    return value


def _username(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("owner_username must be text from authenticated user identity")
    value = value.strip()
    if not value or len(value) > 64 or any(ch in value for ch in "/\\\x00"):
        raise ValueError("owner_username must contain 1 to 64 safe characters")
    return value


def _role(value: object) -> str:
    if not isinstance(value, str) or value not in ROLES:
        raise ValueError("role must be 'coder' or 'reconciler'")
    return str(value)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _connect(path: Path, *, read_only: bool = False) -> sqlite3.Connection:
    if read_only:
        if not path.is_file():
            raise FileNotFoundError(path)
        uri = path.resolve().as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=15)
    else:
        conn = sqlite3.connect(path, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=15000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def _write(path: Path) -> Iterator[sqlite3.Connection]:
    # ponytail: SQLite serializes writes per task; move hot tasks to a shared DB if this contends.
    if not path.is_file():
        raise ValueError("task collaboration store is not initialized")
    with closing(_connect(path)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def initialize_task_store(
    task_dir: str | Path, *, task_id: str, owner_user_id: int, owner_username: str
) -> dict[str, object]:
    """Create the shared store for a newly created task, bound to its owner.

    Call only from the new-task creation path with authenticated user identity.
    Repeated calls are safe only for the same task and owner.
    """
    task_id = _task_id(task_id)
    owner_user_id = _user_id(owner_user_id, "owner_user_id")
    owner_username = _username(owner_username)
    path = shared_store_path(task_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(_connect(path)) as conn:
        conn.executescript(_SCHEMA)
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                "SELECT task_id, owner_user_id, owner_username, created_at "
                "FROM collaboration_task WHERE singleton=1"
            ).fetchone()
            if row is None:
                created_at = _now()
                conn.execute(
                    "INSERT INTO collaboration_task"
                    "(singleton,task_id,owner_user_id,owner_username,created_at) VALUES(1,?,?,?,?)",
                    (task_id, owner_user_id, owner_username, created_at),
                )
                result = {
                    "task_id": task_id,
                    "owner_user_id": owner_user_id,
                    "owner_username": owner_username,
                    "created_at": created_at,
                }
            else:
                if (
                    row["task_id"] != task_id
                    or int(row["owner_user_id"]) != owner_user_id
                    or row["owner_username"] != owner_username
                ):
                    raise ValueError("task collaboration store is already bound to another owner or task")
                result = dict(row)
                result["owner_user_id"] = int(result["owner_user_id"])
            conn.commit()
            return result
        except Exception:
            conn.rollback()
            raise


def _owner(conn: sqlite3.Connection, task_id: str | None) -> sqlite3.Row:
    # 先判表存在再查询：把“库未初始化（无 collaboration_task 表）”归为
    # legacy（ValueError），而锁超时/读失败（OperationalError 等）原样上抛，
    # 由调用方决定是 fail-closed 布尔、还是 OwnerUnavailableError。
    has_table = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='collaboration_task'"
    ).fetchone()
    if has_table is None:
        raise ValueError("task has no registered collaboration owner")
    row = conn.execute(
        "SELECT task_id, owner_user_id, owner_username, created_at "
        "FROM collaboration_task WHERE singleton=1"
    ).fetchone()
    if row is None or (task_id is not None and row["task_id"] != _task_id(task_id)):
        raise ValueError("task has no registered collaboration owner")
    return row


def get_owner(
    store: str | Path, *, task_id: str | None = None
) -> dict[str, object] | None:
    """Return registered owner identity; legacy/uninitialized tasks return None.

    ``task_id`` 现为必传语义：属主-任务绑定校验不可静默跳过。参数保留
    ``None`` 默认仅为签名兼容——不传时绑定无法核实，直接按不可确认属主
    处理（返回 None，fail-closed），不再退化为“跳过绑定校验”的旧默认。
    读取失败（锁超时/IO/库损坏）抛 OwnerUnavailableError（503 重试语义），
    不折叠成“无 owner=legacy”。
    """
    if task_id is None:
        return None
    try:
        with closing(_connect(Path(store), read_only=True)) as conn:
            row = _owner(conn, task_id)
            return {
                "task_id": row["task_id"],
                "owner_user_id": int(row["owner_user_id"]),
                "owner_username": row["owner_username"],
                "created_at": row["created_at"],
            }
    except FileNotFoundError:
        # 库文件不存在 = 任务从未启用协作机制 -> legacy。
        return None
    except ValueError:
        # 无属主行 / task_id 不匹配 / 表未初始化 -> legacy。
        return None
    except (OSError, sqlite3.Error) as exc:
        raise OwnerUnavailableError(f"task owner lookup failed: {exc}") from exc


def is_owner(
    store: str | Path, user_id: int, *, task_id: str | None = None
) -> bool:
    user_id = _user_id(user_id)
    try:
        with closing(_connect(Path(store), read_only=True)) as conn:
            row = _owner(conn, task_id)
            return int(row["owner_user_id"]) == user_id
    except (OSError, sqlite3.Error, ValueError):
        return False


def is_participant(
    store: str | Path, user_id: int, *, task_id: str | None = None
) -> bool:
    """Owner or holder of any granted role (coder/reconciler); False for legacy/unowned."""
    user_id = _user_id(user_id)
    try:
        with closing(_connect(Path(store), read_only=True)) as conn:
            owner = _owner(conn, task_id)
            if int(owner["owner_user_id"]) == user_id:
                return True
            row = conn.execute(
                "SELECT 1 FROM collaboration_role_grants WHERE user_id=? LIMIT 1",
                (user_id,),
            ).fetchone()
            return row is not None
    except (OSError, sqlite3.Error, ValueError):
        return False


def grant_role(
    store: str | Path,
    *,
    actor_user_id: int,
    user_id: int,
    role: str,
    task_id: str | None = None,
) -> dict[str, object]:
    """Grant a coder/reconciler role; only the registered owner may grant."""
    actor_user_id = _user_id(actor_user_id, "actor_user_id")
    user_id = _user_id(user_id)
    role = _role(role)
    with _write(Path(store)) as conn:
        owner = _owner(conn, task_id)
        if int(owner["owner_user_id"]) != actor_user_id:
            raise PermissionError("only the task owner may grant collaboration roles")
        conn.execute(
            "INSERT OR IGNORE INTO collaboration_role_grants(user_id,role,granted_by,granted_at) VALUES(?,?,?,?)",
            (user_id, role, actor_user_id, _now()),
        )
        row = conn.execute(
            "SELECT user_id,role,granted_by,granted_at FROM collaboration_role_grants WHERE user_id=? AND role=?",
            (user_id, role),
        ).fetchone()
        return {
            "user_id": int(row["user_id"]),
            "role": row["role"],
            "granted_by": int(row["granted_by"]),
            "granted_at": row["granted_at"],
        }


def revoke_role(
    store: str | Path,
    *,
    actor_user_id: int,
    user_id: int,
    role: str,
    task_id: str | None = None,
) -> bool:
    """Remove a role grant; only the registered owner may revoke."""
    actor_user_id = _user_id(actor_user_id, "actor_user_id")
    user_id = _user_id(user_id)
    role = _role(role)
    with _write(Path(store)) as conn:
        owner = _owner(conn, task_id)
        if int(owner["owner_user_id"]) != actor_user_id:
            raise PermissionError("only the task owner may revoke collaboration roles")
        cursor = conn.execute(
            "DELETE FROM collaboration_role_grants WHERE user_id=? AND role=?",
            (user_id, role),
        )
        return cursor.rowcount == 1


def list_role_grants(
    store: str | Path, *, actor_user_id: int, task_id: str | None = None
) -> list[dict[str, object]]:
    """List grants for the owner; members cannot enumerate task collaborators."""
    actor_user_id = _user_id(actor_user_id, "actor_user_id")
    with closing(_connect(Path(store), read_only=True)) as conn:
        owner = _owner(conn, task_id)
        if int(owner["owner_user_id"]) != actor_user_id:
            raise PermissionError("only the task owner may list collaboration roles")
        return [
            {
                "user_id": int(row["user_id"]),
                "role": row["role"],
                "granted_by": int(row["granted_by"]),
                "granted_at": row["granted_at"],
            }
            for row in conn.execute(
                "SELECT user_id,role,granted_by,granted_at FROM collaboration_role_grants "
                "ORDER BY user_id,role"
            )
        ]


def list_participant_ids(
    store: str | Path, *, actor_user_id: int, task_id: str
) -> list[int]:
    """Return task member IDs only to a member of a verified task.

    异常契约与同文件其他公开函数对齐：store 未初始化/读失败/task_id 不
    匹配（OSError/sqlite3.Error/ValueError）按“无参与者”降级处理并记日志
    （notify/team_progress 不再 500）；非参与者仍抛 PermissionError。
    """
    actor_user_id = _user_id(actor_user_id, "actor_user_id")
    try:
        with closing(_connect(Path(store), read_only=True)) as conn:
            owner = _owner(conn, task_id)
            ids = {int(owner["owner_user_id"])}
            ids.update(int(row[0]) for row in conn.execute(
                "SELECT DISTINCT user_id FROM collaboration_role_grants"))
            if actor_user_id not in ids:
                raise PermissionError("task membership required")
            return sorted(ids)
    except PermissionError:
        raise
    except (OSError, sqlite3.Error, ValueError) as exc:
        logger.warning("list_participant_ids degraded to no participants: %s", exc)
        return []


def authorize_action(
    store: str | Path,
    user_id: int,
    action: str,
    *,
    task_id: str | None = None,
) -> bool:
    """Fail closed unless the authenticated user has an explicit required role."""
    user_id = _user_id(user_id)
    if action not in _ACTION_ROLES:
        raise ValueError(f"unknown collaboration action: {action}")
    try:
        with closing(_connect(Path(store), read_only=True)) as conn:
            owner = _owner(conn, task_id)
            if int(owner["owner_user_id"]) == user_id:
                return True
            row = conn.execute(
                "SELECT 1 FROM collaboration_role_grants WHERE user_id=? AND role=? LIMIT 1",
                (user_id, _ACTION_ROLES[action]),
            ).fetchone()
            return row is not None
    except (OSError, sqlite3.Error, ValueError):
        return False
