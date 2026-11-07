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


class CollaborationStoreCorruptError(OwnerUnavailableError):
    """协作库文件存在但不是可读的 SQLite 库（永久损坏，重试无益）。

    典型成因：collaboration.db 被截断/写坏/被非 SQLite 内容覆盖。与锁超时
    等**暂时性** OwnerUnavailableError 区分：HTTP 层在 desktop 模式下映射为
    409 并给出修复指引（备份后删除或改名该文件即可重建），server 模式维持
    fail-closed（503/409 均不放行）。
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
CREATE TABLE IF NOT EXISTS collaboration_settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS collaboration_member_settings (
  user_id INTEGER PRIMARY KEY CHECK(user_id > 0),
  screening_enabled INTEGER NOT NULL CHECK(screening_enabled IN (0, 1)),
  updated_by INTEGER NOT NULL CHECK(updated_by > 0),
  updated_at TEXT NOT NULL
);
"""

#: 筛选参与权一次性回填的完成时刻（collaboration_settings 行）。标记存在 =
#: 该库已按「升级后」口径运行：此后的新 reconciler 授权默认关；标记不存在 =
#: 升级前库，回填时点已存在的 reconciler 授权一律默认开。语义只看标记与行，
#: 绝不按 grant 时间戳「读时猜新旧」。
SCREENING_BACKFILL_KEY = "screening_enabled_backfill_completed_at"



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


def _migrate_member_settings(conn: sqlite3.Connection) -> bool:
    """幂等一次性回填：存量 reconciler 授权 -> 筛选参与权「开」。

    返回是否在本调用中完成了回填。确定性规则：
    - ``collaboration_settings`` 中无 ``SCREENING_BACKFILL_KEY`` 行 = 升级前库
      → 把此刻已存在的全部 reconciler grant 写成 enabled=1，再标记完成时刻；
    - 标记已存在 → 不再回填（此后的新授权由 grant_role 显式写默认关）。

    调用方必须持有写事务（``_write`` / 初始化路径）。全部 SQL 为固定字面量
    + ``?`` 参数绑定。
    """
    conn.execute("CREATE TABLE IF NOT EXISTS collaboration_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.execute("CREATE TABLE IF NOT EXISTS collaboration_member_settings (user_id INTEGER PRIMARY KEY CHECK(user_id > 0), screening_enabled INTEGER NOT NULL CHECK(screening_enabled IN (0, 1)), updated_by INTEGER NOT NULL CHECK(updated_by > 0), updated_at TEXT NOT NULL)")
    completed = conn.execute("SELECT 1 FROM collaboration_settings WHERE key = ?", (SCREENING_BACKFILL_KEY,)).fetchone()
    if completed is not None:
        return False
    now = _now()
    conn.execute("INSERT INTO collaboration_settings(key, value) VALUES(?, ?)", (SCREENING_BACKFILL_KEY, now))
    conn.execute("INSERT OR IGNORE INTO collaboration_member_settings(user_id, screening_enabled, updated_by, updated_at) SELECT user_id, 1, granted_by, ? FROM collaboration_role_grants WHERE role = ?", (now, "reconciler"))
    return True


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
            # 新建库即写入回填完成标记：此后 grant_role 的新 reconciler 授权
            # 一律显式默认关（语义见 _migrate_member_settings）。
            _migrate_member_settings(conn)
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
    except sqlite3.OperationalError as exc:
        # 锁超时/忙 等**暂时性**失败：可重试语义（503）。
        raise OwnerUnavailableError(f"task owner lookup failed: {exc}") from exc
    except sqlite3.DatabaseError as exc:
        # "file is not a database" / "database disk image is malformed" 等
        # **永久损坏**：重试无益，需人工修库（删除/改名 collaboration.db）。
        raise CollaborationStoreCorruptError(f"task collaboration store is unreadable: {exc}") from exc
    except (OSError, sqlite3.Error) as exc:
        raise OwnerUnavailableError(f"task owner lookup failed: {exc}") from exc


def is_owner(
    store: str | Path, user_id: int, *, task_id: str | None = None
) -> bool:
    """是否任务属主；异常口径与 get_owner/is_participant 对齐（#36 扫尾）。

    只有"确无属主"（无属主行/绑定不匹配/库未初始化/不存在）返回 False；
    锁超时/读失败抛 OwnerUnavailableError、库永久损坏抛
    CollaborationStoreCorruptError——读取失败不得折叠成"不是属主"。
    """
    user_id = _user_id(user_id)
    try:
        with closing(_connect(Path(store), read_only=True)) as conn:
            row = _owner(conn, task_id)
            return int(row["owner_user_id"]) == user_id
    except FileNotFoundError:
        return False  # 库不存在 = legacy：确无属主
    except ValueError:
        return False  # 无属主行 / 绑定不匹配 / 表未初始化：确无属主
    except sqlite3.OperationalError as exc:
        raise OwnerUnavailableError(f"owner lookup failed: {exc}") from exc
    except sqlite3.DatabaseError as exc:
        raise CollaborationStoreCorruptError(
            f"task collaboration store is unreadable: {exc}") from exc
    except (OSError, sqlite3.Error) as exc:
        raise OwnerUnavailableError(f"owner lookup failed: {exc}") from exc


def is_participant(
    store: str | Path, user_id: int, *, task_id: str | None = None
) -> bool:
    """Owner or holder of any granted role (coder/reconciler); False for legacy/unowned.

    异常口径与 get_owner 对齐（#36/R6-C6）：只有"确无属主/角色"（无属主行、
    task_id 绑定不匹配、库未初始化/不存在）返回 False；锁超时/读失败抛
    OwnerUnavailableError（HTTP 层 503 重试语义）、库永久损坏抛
    CollaborationStoreCorruptError——读取失败不得折叠成"不是参与者"（403），
    那会把可重试/可修复的故障误报成权限拒绝。
    """
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
    except FileNotFoundError:
        return False  # 库不存在 = legacy：确无参与者角色
    except ValueError:
        return False  # 无属主行 / 绑定不匹配 / 表未初始化：确无角色
    except sqlite3.OperationalError as exc:
        raise OwnerUnavailableError(f"participant lookup failed: {exc}") from exc
    except sqlite3.DatabaseError as exc:
        raise CollaborationStoreCorruptError(
            f"task collaboration store is unreadable: {exc}") from exc
    except (OSError, sqlite3.Error) as exc:
        raise OwnerUnavailableError(f"participant lookup failed: {exc}") from exc


def grant_role(
    store: str | Path,
    *,
    actor_user_id: int,
    user_id: int,
    role: str,
    task_id: str | None = None,
) -> dict[str, object]:
    """Grant a coder/reconciler role; only the registered owner may grant.

    handoff C5（复审扫尾）：task_id 必传，理由同 authorize_action。
    """
    actor_user_id = _user_id(actor_user_id, "actor_user_id")
    user_id = _user_id(user_id)
    role = _role(role)
    if task_id is None:
        raise ValueError("task_id is required for grant_role")
    with _write(Path(store)) as conn:
        owner = _owner(conn, task_id)
        if int(owner["owner_user_id"]) != actor_user_id:
            raise PermissionError("only the task owner may grant collaboration roles")
        # 先完成存量回填（升级前库），再写入本次授权——保证「新授权默认关」
        # 与「存量授权默认开」互不污染。
        _migrate_member_settings(conn)
        cursor = conn.execute(
            "INSERT OR IGNORE INTO collaboration_role_grants(user_id,role,granted_by,granted_at) VALUES(?,?,?,?)",
            (user_id, role, actor_user_id, _now()),
        )
        # 升级后新授权的 reconciler 默认关闭筛选参与权；重复授权（已有 grant）
        # 不重置负责人此后手动调整过的开关。
        if role == "reconciler" and cursor.rowcount == 1:
            conn.execute(
                "INSERT INTO collaboration_member_settings(user_id,screening_enabled,updated_by,updated_at) VALUES(?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET screening_enabled=0, updated_by=excluded.updated_by, updated_at=excluded.updated_at",
                (user_id, 0, actor_user_id, _now()),
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


def ensure_screening_backfill(store: str | Path, *, task_id: str | None = None) -> bool:
    """属主侧读取前的幂等回填（team-progress 调用）。

    标记已存在 -> 无操作；升级前库 -> 一次性把存量 reconciler 授权写为「开」。
    异常口径与模块其它写函数一致：确定性否定（库不存在/无属主/绑定不匹配）
    抛 ValueError，锁超时/读失败抛 OwnerUnavailableError，永久损坏抛
    CollaborationStoreCorruptError（HTTP 层 409/503，不折叠成 403）。
    """
    if task_id is None:
        raise ValueError("task_id is required for ensure_screening_backfill")
    # 先只读探测标记：已迁移的库绝不获取写锁（修前每次 team-progress 都走
    # BEGIN IMMEDIATE，任何并发写都会把属主的读变成 503）。
    try:
        with closing(_connect(Path(store), read_only=True)) as conn:
            _owner(conn, task_id)
            try:
                completed = conn.execute(
                    "SELECT 1 FROM collaboration_settings WHERE key=?",
                    (SCREENING_BACKFILL_KEY,),
                ).fetchone()
            except sqlite3.OperationalError as exc:
                if "no such table" not in str(exc).lower():
                    raise
                completed = None
            if completed is not None:
                return False
    except FileNotFoundError as exc:
        raise ValueError("task collaboration store is not initialized") from exc
    except (PermissionError, ValueError):
        raise
    except sqlite3.OperationalError as exc:
        raise OwnerUnavailableError(f"screening backfill failed: {exc}") from exc
    except sqlite3.DatabaseError as exc:
        raise CollaborationStoreCorruptError(
            f"task collaboration store is unreadable: {exc}") from exc
    except (OSError, sqlite3.Error) as exc:
        raise OwnerUnavailableError(f"screening backfill failed: {exc}") from exc
    try:
        with _write(Path(store)) as conn:
            _owner(conn, task_id)
            return _migrate_member_settings(conn)
    except (PermissionError, ValueError):
        raise
    except sqlite3.OperationalError as exc:
        raise OwnerUnavailableError(f"screening backfill failed: {exc}") from exc
    except sqlite3.DatabaseError as exc:
        raise CollaborationStoreCorruptError(
            f"task collaboration store is unreadable: {exc}") from exc
    except (OSError, sqlite3.Error) as exc:
        raise OwnerUnavailableError(f"screening backfill failed: {exc}") from exc


def _screening_allowed_in_conn(
    conn: sqlite3.Connection, user_id: int, task_id: str,
) -> bool:
    """连接内的筛选参与权判定（调用方负责异常映射）。"""
    owner = _owner(conn, task_id)
    if int(owner["owner_user_id"]) == user_id:
        return True  # 属主永不受该开关影响
    coder = conn.execute(
        "SELECT 1 FROM collaboration_role_grants WHERE user_id=? AND role=?",
        (user_id, "coder"),
    ).fetchone()
    if coder is not None:
        # coder 不受筛选开关影响；同时持 coder+reconciler 的双角色成员按
        # coder 口径放行（授权 API/主键允许双角色，不能被 reconciler 开关误伤）。
        return True
    grant = conn.execute(
        "SELECT 1 FROM collaboration_role_grants WHERE user_id=? AND role=?",
        (user_id, "reconciler"),
    ).fetchone()
    if grant is None:
        return True  # 非角色成员不受该开关约束
    try:
        row = conn.execute(
            "SELECT screening_enabled FROM collaboration_member_settings WHERE user_id=?",
            (user_id,),
        ).fetchone()
        if row is not None:
            return bool(row[0])
        completed = conn.execute(
            "SELECT 1 FROM collaboration_settings WHERE key=?",
            (SCREENING_BACKFILL_KEY,),
        ).fetchone()
        # 标记不存在 = 升级前库：回填时点已存在的授权一律「开」；
        # 标记存在但无该行 = 升级后新授权应显式写行，缺失按默认「关」。
        return completed is None
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc).lower():
            return True  # 旧库尚未建新表：等价于升级前 -> 开
        raise


def screening_allowed(
    store: str | Path, user_id: int, *, task_id: str | None = None
) -> bool:
    """该成员当前是否可写初筛/复筛（只读；不创建/不迁移任何表）。

    - 属主、coder（含 coder+reconciler 双角色）、非角色成员恒为 True；
    - 纯 reconciler：读 collaboration_member_settings；无表/无标记（升级前）-> True；
    - 确定性否定（无协作库/无属主）-> True（legacy 零回归）；
    - 锁超时/读失败 -> OwnerUnavailableError；永久损坏 ->
      CollaborationStoreCorruptError（绝不折叠成 False/403）。
    """
    user_id = _user_id(user_id)
    if task_id is None:
        raise ValueError("task_id is required for screening_allowed")
    try:
        with closing(_connect(Path(store), read_only=True)) as conn:
            return _screening_allowed_in_conn(conn, user_id, task_id)
    except FileNotFoundError:
        return True  # 无协作库 = legacy，无筛选参与权限制
    except ValueError:
        return True  # 无属主行/绑定不匹配/库未初始化 = legacy
    except sqlite3.OperationalError as exc:
        raise OwnerUnavailableError(f"screening permission lookup failed: {exc}") from exc
    except sqlite3.DatabaseError as exc:
        raise CollaborationStoreCorruptError(
            f"task collaboration store is unreadable: {exc}") from exc
    except (OSError, sqlite3.Error) as exc:
        raise OwnerUnavailableError(f"screening permission lookup failed: {exc}") from exc


def list_screening_flags(
    store: str | Path, *, task_id: str | None = None
) -> dict[int, bool]:
    """全部 reconciler 成员的筛选参与权（team-progress 批量读取）。

    legacy/无库返回空映射（调用方按 True 处理）；不确定故障按模块契约抛出。
    """
    if task_id is None:
        raise ValueError("task_id is required for list_screening_flags")
    try:
        with closing(_connect(Path(store), read_only=True)) as conn:
            _owner(conn, task_id)
            reconcilers = [int(row[0]) for row in conn.execute(
                "SELECT user_id FROM collaboration_role_grants WHERE role=? AND user_id NOT IN (SELECT user_id FROM collaboration_role_grants WHERE role=?) ORDER BY user_id",
                ("reconciler", "coder"),
            )]
            try:
                stored = {int(row[0]): bool(row[1]) for row in conn.execute(
                    "SELECT user_id, screening_enabled FROM collaboration_member_settings")}
                completed = conn.execute(
                    "SELECT 1 FROM collaboration_settings WHERE key=?",
                    (SCREENING_BACKFILL_KEY,),
                ).fetchone()
            except sqlite3.OperationalError as exc:
                if "no such table" in str(exc).lower():
                    return {user_id: True for user_id in reconcilers}
                raise
            default = completed is None
            return {user_id: stored.get(user_id, default) for user_id in reconcilers}
    except (FileNotFoundError, ValueError):
        return {}
    except sqlite3.OperationalError as exc:
        raise OwnerUnavailableError(f"screening permission lookup failed: {exc}") from exc
    except sqlite3.DatabaseError as exc:
        raise CollaborationStoreCorruptError(
            f"task collaboration store is unreadable: {exc}") from exc
    except (OSError, sqlite3.Error) as exc:
        raise OwnerUnavailableError(f"screening permission lookup failed: {exc}") from exc


def set_member_screening(
    store: str | Path,
    *,
    actor_user_id: int,
    user_id: int,
    enabled: bool,
    task_id: str | None = None,
) -> dict[str, object]:
    """设置 reconciler 成员的筛选参与权；仅属主可写，写操作留痕。

    与 grant_role 同口径（属主校验、task_id 必传、异常契约）：属主校验失败抛
    PermissionError（HTTP 403）；目标无 reconciler 授权抛 ValueError（HTTP 400）；
    锁超时/读失败 -> OwnerUnavailableError（503）；永久损坏 ->
    CollaborationStoreCorruptError（409/503）。
    """
    actor_user_id = _user_id(actor_user_id, "actor_user_id")
    user_id = _user_id(user_id)
    if not isinstance(enabled, bool):
        raise ValueError("enabled must be a boolean")
    if task_id is None:
        raise ValueError("task_id is required for set_member_screening")
    try:
        with _write(Path(store)) as conn:
            owner = _owner(conn, task_id)
            if int(owner["owner_user_id"]) != actor_user_id:
                raise PermissionError("only the task owner may manage screening participation")
            _migrate_member_settings(conn)
            grant = conn.execute(
                "SELECT 1 FROM collaboration_role_grants WHERE user_id=? AND role=?",
                (user_id, "reconciler"),
            ).fetchone()
            if grant is None:
                raise ValueError("target user has no reconciler role")
            now = _now()
            conn.execute(
                "INSERT INTO collaboration_member_settings(user_id,screening_enabled,updated_by,updated_at) VALUES(?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET screening_enabled=excluded.screening_enabled, updated_by=excluded.updated_by, updated_at=excluded.updated_at",
                (user_id, 1 if enabled else 0, actor_user_id, now),
            )
            row = conn.execute(
                "SELECT screening_enabled, updated_by, updated_at FROM collaboration_member_settings WHERE user_id=?",
                (user_id,),
            ).fetchone()
            return {
                "user_id": user_id,
                "screening_enabled": bool(row["screening_enabled"]),
                "updated_by": int(row["updated_by"]),
                "updated_at": row["updated_at"],
            }
    except (PermissionError, ValueError):
        raise
    except sqlite3.OperationalError as exc:
        raise OwnerUnavailableError(f"screening setting write failed: {exc}") from exc
    except sqlite3.DatabaseError as exc:
        raise CollaborationStoreCorruptError(
            f"task collaboration store is unreadable: {exc}") from exc
    except (OSError, sqlite3.Error) as exc:
        raise OwnerUnavailableError(f"screening setting write failed: {exc}") from exc


def revoke_role(
    store: str | Path,
    *,
    actor_user_id: int,
    user_id: int,
    role: str,
    task_id: str | None = None,
) -> bool:
    """Remove a role grant; only the registered owner may revoke.

    L-09-01（复审扫尾）：task_id 必传，理由同 authorize_action——缺省时
    _owner 的属主-任务绑定校验被跳过，属主判定退化为「该库登记的任意任务」。
    """
    actor_user_id = _user_id(actor_user_id, "actor_user_id")
    user_id = _user_id(user_id)
    role = _role(role)
    if task_id is None:
        raise ValueError("task_id is required for revoke_role")
    with _write(Path(store)) as conn:
        owner = _owner(conn, task_id)
        if int(owner["owner_user_id"]) != actor_user_id:
            raise PermissionError("only the task owner may revoke collaboration roles")
        cursor = conn.execute(
            "DELETE FROM collaboration_role_grants WHERE user_id=? AND role=?",
            (user_id, role),
        )
        if role == "reconciler":
            # 撤销即清掉开关行：再次授权按「新授权默认关」重新落 0，不继承旧值。
            _migrate_member_settings(conn)
            conn.execute(
                "DELETE FROM collaboration_member_settings WHERE user_id=?",
                (user_id,),
            )
        return cursor.rowcount == 1


def list_role_grants(
    store: str | Path, *, actor_user_id: int, task_id: str | None = None
) -> list[dict[str, object]]:
    """List grants for the owner; members cannot enumerate task collaborators.

    L-09-01（复审扫尾）：task_id 必传，理由同 authorize_action——缺省时
    _owner 的属主-任务绑定校验被跳过，属主判定退化为「该库登记的任意任务」。
    该守卫在异常映射之外先抛 ValueError——必须早于下方
    ``except (FileNotFoundError, ValueError)``，否则 L-09-01 的必传契约
    会被折叠成 PermissionError。

    异常口径与 get_owner/is_owner/is_participant 对齐（r6 #35）：确定性否定
    （库不存在/无属主行/绑定不匹配 -> 不是属主，无权枚举）抛 PermissionError；
    不确定故障（锁超时/读失败 -> OwnerUnavailableError、库永久损坏 ->
    CollaborationStoreCorruptError）上抛由 HTTP 层映射 503/409——修复前裸
    ``sqlite3.OperationalError``/``sqlite3.DatabaseError`` 使 team_progress/
    context 以 500 收场，而非该函数族自立的 503 重试口径。"""
    actor_user_id = _user_id(actor_user_id, "actor_user_id")
    if task_id is None:
        raise ValueError("task_id is required for list_role_grants")
    try:
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
    except PermissionError:
        raise
    except (FileNotFoundError, ValueError):
        # 确定性否定：无属主/绑定不匹配/库未初始化——无人有权枚举。
        raise PermissionError("only the task owner may list collaboration roles")
    except sqlite3.OperationalError as exc:
        raise OwnerUnavailableError(f"role grant lookup failed: {exc}") from exc
    except sqlite3.DatabaseError as exc:
        raise CollaborationStoreCorruptError(
            f"task collaboration store is unreadable: {exc}") from exc
    except (OSError, sqlite3.Error) as exc:
        raise OwnerUnavailableError(f"role grant lookup failed: {exc}") from exc


def list_participant_ids(
    store: str | Path, *, actor_user_id: int, task_id: str
) -> list[int]:
    """Return task member IDs only to a member of a verified task.

    异常契约与 get_owner/is_participant 对齐（#36/R6-C6 复审扫尾）：
    - **确定性否定**（无属主行、task_id 绑定不匹配、库未初始化/不存在）
      返回空列表——对该 task_id 而言"无参与者"是事实，不是伪空表；
    - **不确定故障**（锁超时/读失败 → OwnerUnavailableError、库永久损坏
      → CollaborationStoreCorruptError）必须抛出——修复前 BEGIN EXCLUSIVE
      锁窗口内曾折叠成 members:[] 使 team_progress 统计清零、notify 静默
      丢事件（r5 rv5_stats_probe B1 场景）；
    - 非参与者仍抛 PermissionError。
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
    except (FileNotFoundError, ValueError):
        return []  # 确定性否定：该 task_id 确无参与者
    except sqlite3.OperationalError as exc:
        raise OwnerUnavailableError(f"participant lookup failed: {exc}") from exc
    except sqlite3.DatabaseError as exc:
        raise CollaborationStoreCorruptError(
            f"task collaboration store is unreadable: {exc}") from exc
    except (OSError, sqlite3.Error) as exc:
        raise OwnerUnavailableError(f"participant lookup failed: {exc}") from exc


def authorize_action(
    store: str | Path,
    user_id: int,
    action: str,
    *,
    task_id: str | None = None,
) -> bool:
    """Fail closed unless the authenticated user has an explicit required role.

    handoff C5（复审扫尾）：task_id 必传——缺省时按"任意属主"判定的旧
    口径会让拷贝/错放的任务库以任意任务身份通过授权，调用方必须显式给
    出当前任务 id（全部既有调用点均已传 info.task_id）。

    异常口径与 is_owner/is_participant 对齐（r6 #36 / MM-11）：只有**确定性
    否定**（库不存在/无属主行/绑定不匹配 -> legacy，确无该角色）返回 False；
    锁超时/读失败抛 OwnerUnavailableError（HTTP 503 重试）、库永久损坏抛
    CollaborationStoreCorruptError（desktop 409 修库指引 / server 503）——
    修复前 ``except (...): return False`` 把可重试故障折叠成 403 权限拒绝。
    """
    user_id = _user_id(user_id)
    if action not in _ACTION_ROLES:
        raise ValueError(f"unknown collaboration action: {action}")
    if task_id is None:
        raise ValueError("task_id is required for authorize_action")
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
    except FileNotFoundError:
        return False  # 库不存在 = legacy：确无角色
    except ValueError:
        return False  # 无属主行 / 绑定不匹配 / 表未初始化：确无角色
    except sqlite3.OperationalError as exc:
        raise OwnerUnavailableError(f"action authorization lookup failed: {exc}") from exc
    except sqlite3.DatabaseError as exc:
        raise CollaborationStoreCorruptError(
            f"task collaboration store is unreadable: {exc}") from exc
    except (OSError, sqlite3.Error) as exc:
        raise OwnerUnavailableError(f"action authorization lookup failed: {exc}") from exc
