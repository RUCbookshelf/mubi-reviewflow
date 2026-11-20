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

#: 团队业务角色（2.1.6 FLOW 阶段 II-A）：单一有效角色选择表 + append-only 审计。
#: 成员关系（grants）、当前有效业务角色（choice/grant 派生）、历史授权是三个
#: 概念——choice 覆盖旧 grants 的业务效力，但不授予成员权（见
#: OWNER_BUSINESS_ROLE_DECISION_20261114）。
_BUSINESS_ROLE_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS collaboration_business_role ("
    " user_id INTEGER PRIMARY KEY CHECK(user_id > 0),"
    " business_role TEXT NOT NULL CHECK(business_role IN ('coder', 'reconciler')),"
    " set_by INTEGER NOT NULL CHECK(set_by > 0),"
    " set_at TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS collaboration_business_role_events ("
    " id INTEGER PRIMARY KEY AUTOINCREMENT,"
    " user_id INTEGER NOT NULL CHECK(user_id > 0),"
    " old_role TEXT, new_role TEXT NOT NULL,"
    " set_by INTEGER NOT NULL CHECK(set_by > 0), set_at TEXT NOT NULL)",
    "CREATE TRIGGER IF NOT EXISTS collaboration_business_role_events_no_update "
    "BEFORE UPDATE ON collaboration_business_role_events "
    "BEGIN SELECT RAISE(ABORT, 'business role events are append-only'); END",
    "CREATE TRIGGER IF NOT EXISTS collaboration_business_role_events_no_delete "
    "BEFORE DELETE ON collaboration_business_role_events "
    "BEGIN SELECT RAISE(ABORT, 'business role events are append-only'); END",
)


class TeamRoleGateError(Exception):
    """team 业务角色门的结构化拒绝（HTTP 层按 ``status``/``code`` 映射）。

    与 ``ArbitrationDecisionRejected`` 同风格：``code``/``message_key``/
    ``params`` 原样进响应载荷，``message`` 为中文源串（前端十语言词典按此
    注册）。子类固定四码：unconfigured/conflict（409）、denied（403）；
    非法配置值是输入错误，走 ``ValueError``→400，不用本类。
    """

    code = "team.role.denied"
    message_key = "team.role.denied"
    status = 403

    def __init__(self, message: str, *, params: dict | None = None) -> None:
        super().__init__(message)
        self.params = params or {}


class TeamRoleUnconfigured(TeamRoleGateError):
    code = "team.role.unconfigured"
    message_key = "team.role.unconfigured"
    status = 409


class TeamRoleConflict(TeamRoleGateError):
    code = "team.role.conflict"
    message_key = "team.role.conflict"
    status = 409


class TeamRoleDenied(TeamRoleGateError):
    code = "team.role.denied"
    message_key = "team.role.denied"
    status = 403

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


def _management_statement_result(
    conn: sqlite3.Connection,
    sql: str,
    parameters: tuple[object, ...],
) -> int:
    """Drain one borrowed DML cursor before reading its final rowcount."""
    cursor = conn.execute(sql, parameters)
    try:
        cursor.fetchall()
        rowcount = cursor.rowcount
    except BaseException as primary:
        try:
            cursor.close()
        except BaseException as cleanup:
            primary.add_note(f"management cursor close also failed: {cleanup!r}")
        raise
    cursor.close()
    return rowcount


def _management_read_rows(
    conn: sqlite3.Connection,
    sql: str,
    parameters: tuple[object, ...],
    *,
    optional_table: bool = False,
) -> list[sqlite3.Row] | None:
    """Materialize and close one internal borrowed-connection read cursor.

    None marks a missing optional table detected by execute; an empty list
    represents a successful query with no rows.
    """
    try:
        cursor = conn.execute(sql, parameters)
    except sqlite3.OperationalError as exc:
        if optional_table and "no such table" in str(exc).lower():
            return None
        raise
    try:
        rows = cursor.fetchall()
    except BaseException as primary:
        try:
            cursor.close()
        except BaseException as cleanup:
            primary.add_note(f"management cursor close also failed: {cleanup!r}")
        raise
    cursor.close()
    return rows


@contextmanager
def _management_conn(
    store: str | Path,
    conn: sqlite3.Connection | None,
    *,
    operation: str,
    role: str | None = None,
    require_member_settings: bool = True,
    require_business: bool = False,
    require_backfill: bool = True,
) -> Iterator[tuple[sqlite3.Connection, bool]]:
    """四管理 writer 的连接接缝：None 走原 _write，传入 conn 则仅借用。

    借用分支不解析 store、不打开/关闭连接、不开启/结束事务、不建表/迁移/
    PRAGMA/改 row_factory；按固定 operation profile 预检 main schema 后把原连接
    交给调用方。TEMP 保留名按 SQLite ASCII NOCASE 检查；无关 TEMP 对象允许。
    """
    if conn is None:
        with _write(Path(store)) as path_conn:
            yield path_conn, False
        return
    if not isinstance(conn, sqlite3.Connection):
        raise TypeError(
            f"conn must be sqlite3.Connection, got {type(conn).__name__!r}")
    # 已关闭连接在这里抛原生 sqlite3.ProgrammingError，原样透传。
    if not conn.in_transaction:
        raise RuntimeError(
            "borrowed management conn requires an open caller transaction")
    if conn.row_factory is not sqlite3.Row:
        raise RuntimeError(
            "borrowed management conn requires row_factory=sqlite3.Row")
    if operation not in {
        "grant_role", "set_member_screening", "set_business_role", "revoke_role",
    }:
        raise ValueError(f"unknown borrowed management operation: {operation!r}")
    if operation in {"grant_role", "revoke_role", "set_business_role"}:
        role = _role(role)
    elif role is not None:
        raise ValueError(f"operation {operation!r} does not accept a role profile")
    ascii_fold = str.maketrans(
        "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")

    required_tables: dict[str, tuple[str, ...]] = {
        "collaboration_task": (
            "singleton", "task_id", "owner_user_id", "owner_username",
            "created_at"),
        "collaboration_role_grants": (
            "user_id", "role", "granted_by", "granted_at"),
    }
    if require_member_settings or require_backfill:
        required_tables["collaboration_settings"] = ("key", "value")
        required_tables["collaboration_member_settings"] = (
            "user_id", "screening_enabled", "updated_by", "updated_at")
    expected_trigger_sql: dict[str, str] = {}
    if require_business:
        required_tables["collaboration_business_role"] = (
            "user_id", "business_role", "set_by", "set_at")
        required_tables["collaboration_business_role_events"] = (
            "id", "user_id", "old_role", "new_role", "set_by", "set_at")
        trigger_prefix = "CREATE TRIGGER IF NOT EXISTS "
        for statement in _BUSINESS_ROLE_SCHEMA:
            if statement.startswith(trigger_prefix):
                trigger_name = statement[len(trigger_prefix):].split(None, 1)[0]
                expected_trigger_sql[trigger_name] = statement

    insert_columns: dict[str, tuple[str, ...]] = {}
    if operation == "grant_role":
        insert_columns["collaboration_role_grants"] = (
            "user_id", "role", "granted_by", "granted_at")
        if role == "reconciler":
            insert_columns["collaboration_member_settings"] = (
                "user_id", "screening_enabled", "updated_by", "updated_at")
    elif operation == "set_member_screening":
        insert_columns["collaboration_member_settings"] = (
            "user_id", "screening_enabled", "updated_by", "updated_at")
    elif operation == "set_business_role":
        insert_columns["collaboration_business_role"] = (
            "user_id", "business_role", "set_by", "set_at")
        insert_columns["collaboration_business_role_events"] = (
            "user_id", "old_role", "new_role", "set_by", "set_at")
        insert_columns["collaboration_role_grants"] = (
            "user_id", "role", "granted_by", "granted_at")
        if role == "reconciler":
            insert_columns["collaboration_member_settings"] = (
                "user_id", "screening_enabled", "updated_by", "updated_at")

    def _normalize_trigger_header(
        sql: str, trigger: str, table: str,
    ) -> str:
        """Only normalize the CREATE TRIGGER object header.

        Literal/quoted body text and comments are preserved; unknown header
        shapes are left untouched and therefore fail the exact comparison.
        """
        text = sql.strip()
        prefix = "CREATE TRIGGER "
        if not text.startswith(prefix):
            return text
        rest = text[len(prefix):]
        if rest.startswith("IF NOT EXISTS "):
            rest = rest[len("IF NOT EXISTS "):]
        if rest.startswith("main." + trigger):
            rest = rest[len("main."):]
        text = prefix + rest
        marker = " ON main." + table
        if marker in text:
            head, separator, tail = text.partition(" BEGIN ")
            if not separator:
                return text
            head = head.replace(marker, " ON " + table, 1)
            text = head + separator + tail
        return text

    trigger_table = "collaboration_business_role_events"
    mutation_tables = set(insert_columns)
    delete_tables: set[str] = set()
    if operation == "revoke_role":
        mutation_tables.add("collaboration_role_grants")
        delete_tables.add("collaboration_role_grants")
        if role == "reconciler":
            mutation_tables.add("collaboration_member_settings")
            delete_tables.add("collaboration_member_settings")
    table_names = tuple(sorted(required_tables))
    temp_names = tuple(sorted(set(required_tables) | set(expected_trigger_sql)))
    table_placeholders = ",".join("?" for _ in table_names)
    temp_placeholders = ",".join("?" for _ in temp_names)
    # borrowed owner/resolver/writer SQL is main-qualified; this TEMP guard is
    # the second barrier against reserved-name or required-table TEMP objects.
    with closing(conn.execute(
        "SELECT type,name,tbl_name FROM temp.sqlite_temp_master "
        f"WHERE name COLLATE NOCASE IN ({temp_placeholders}) "
        f"OR tbl_name COLLATE NOCASE IN ({temp_placeholders})",
        (*temp_names, *temp_names),
    )) as cursor:
        shadow = cursor.fetchone()
    if shadow is not None:
        raise RuntimeError(
            "borrowed management conn has a TEMP object shadowing "
            f"{shadow['type']}:{shadow['name']} "
            f"(tbl_name={shadow['tbl_name']})")

    mutation_names = tuple(sorted(mutation_tables))
    mutation_placeholders = ",".join("?" for _ in mutation_names)
    with closing(conn.execute(
        "SELECT name,tbl_name,sql FROM main.sqlite_master "
        "WHERE type='trigger' "
        f"AND tbl_name COLLATE NOCASE IN ({mutation_placeholders})",
        mutation_names,
    )) as cursor:
        relevant_triggers = cursor.fetchall()
    expected_trigger_names = {
        name.translate(ascii_fold) for name in expected_trigger_sql}
    for trigger_row in relevant_triggers:
        if str(trigger_row["name"]).translate(ascii_fold) not in expected_trigger_names:
            raise RuntimeError(
                "borrowed management dependency unconfirmed: unexpected trigger "
                f"{trigger_row['name']} on {trigger_row['tbl_name']}")

    if delete_tables:
        with closing(conn.execute(
            "SELECT name FROM main.sqlite_master WHERE type='table'",
        )) as cursor:
            main_table_names = [str(row["name"]) for row in cursor.fetchall()]
        deleted_names = {name.translate(ascii_fold) for name in delete_tables}
        for main_table_name in main_table_names:
            try:
                with closing(conn.execute(
                    'SELECT "table" AS referenced_table,on_delete '
                    "FROM pragma_foreign_key_list(?, 'main')",
                    (main_table_name,),
                )) as cursor:
                    foreign_key_rows = cursor.fetchall()
            except sqlite3.OperationalError as exc:
                message = str(exc).lower()
                if (
                    "no such table" in message
                    or "no such function" in message
                    or "no such column" in message
                    or "too many arguments" in message
                ):
                    raise RuntimeError(
                        "borrowed management metadata unavailable: "
                        "pragma_foreign_key_list is required for delete preflight") from exc
                raise
            for foreign_key_row in foreign_key_rows:
                referenced = str(foreign_key_row["referenced_table"]).translate(ascii_fold)
                on_delete = str(foreign_key_row["on_delete"]).upper()
                if referenced in deleted_names and on_delete in {
                    "CASCADE", "SET NULL", "SET DEFAULT",
                }:
                    raise RuntimeError(
                        "borrowed management dependency unconfirmed: foreign key "
                        f"from {main_table_name} adds {on_delete} writes to "
                        f"{foreign_key_row['referenced_table']} deletion")

    with closing(conn.execute(
        "SELECT name FROM main.sqlite_master WHERE type='table' "
        f"AND name COLLATE NOCASE IN ({table_placeholders})",
        table_names,
    )) as cursor:
        present = {str(row["name"]).translate(ascii_fold) for row in cursor.fetchall()}
    missing = sorted(
        table for table in required_tables if table.translate(ascii_fold) not in present)
    if missing:
        raise RuntimeError(
            "borrowed management schema incomplete; missing tables: "
            + ", ".join(missing))

    # SELECT ... WHERE 0 验证必要列，不读取任何业务行，也不创建对象。
    for table, columns in required_tables.items():
        sql = "SELECT " + ",".join(columns) + f" FROM main.{table} WHERE 0"
        try:
            with closing(conn.execute(sql)) as cursor:
                cursor.fetchall()
        except sqlite3.OperationalError as exc:
            message = str(exc).lower()
            if "no such column" in message or "no such table" in message:
                raise RuntimeError(
                    f"borrowed management schema incomplete: {table}: {exc}"
                ) from exc
            raise

    for table, written_columns in insert_columns.items():
        written_column_names = {
            column.translate(ascii_fold) for column in written_columns}
        try:
            with closing(conn.execute(
                'SELECT name,type,"notnull" AS not_null,dflt_value,pk,hidden '
                "FROM pragma_table_xinfo(?, 'main')",
                (table,),
            )) as cursor:
                column_rows = cursor.fetchall()
        except sqlite3.OperationalError as exc:
            message = str(exc).lower()
            if (
                "no such table" in message
                or "no such function" in message
                or "no such column" in message
                or "too many arguments" in message
            ):
                raise RuntimeError(
                    "borrowed management metadata unavailable: "
                    "pragma_table_xinfo is required for write preflight") from exc
            raise
        if not column_rows:
            raise RuntimeError(
                f"borrowed management schema incomplete: metadata missing for {table}")
        if any(int(row["hidden"]) in (2, 3) for row in column_rows
               if str(row["name"]).translate(ascii_fold) in written_column_names):
            raise RuntimeError(
                f"borrowed management schema incomplete: INSERT explicitly writes "
                f"a generated column in {table}")

        omitted = [row for row in column_rows
                   if str(row["name"]).translate(ascii_fold) not in written_column_names
                   and int(row["hidden"]) == 0]
        needs_rowid_proof = any(
            str(row["type"]).strip().upper() == "INTEGER"
            and int(row["pk"]) > 0
            for row in omitted
        )
        rowid_auto_column: str | None = None
        if needs_rowid_proof:
            try:
                with closing(conn.execute(
                    "SELECT schema,name,type,wr FROM pragma_table_list "
                    "WHERE schema='main' AND name COLLATE NOCASE=?",
                    (table,),
                )) as cursor:
                    table_layout = cursor.fetchone()
            except sqlite3.OperationalError as exc:
                message = str(exc).lower()
                if (
                    "no such table" in message
                    or "no such function" in message
                    or "no such column" in message
                ):
                    raise RuntimeError(
                        "borrowed management metadata unavailable: "
                        "pragma_table_list is required to prove an omitted INTEGER PK") from exc
                raise
            pk_columns = [row for row in column_rows if int(row["pk"]) > 0]
            if (
                table_layout is not None
                and str(table_layout["type"]) == "table"
                and int(table_layout["wr"]) == 0
                and len(pk_columns) == 1
                and str(pk_columns[0]["type"]).strip().upper() == "INTEGER"
            ):
                try:
                    with closing(conn.execute(
                        "SELECT origin FROM pragma_index_list(?, 'main')",
                        (table,),
                    )) as cursor:
                        index_origins = {str(row["origin"]) for row in cursor.fetchall()}
                except sqlite3.OperationalError as exc:
                    message = str(exc).lower()
                    if (
                        "no such table" in message
                        or "no such function" in message
                        or "no such column" in message
                        or "too many arguments" in message
                    ):
                        raise RuntimeError(
                            "borrowed management metadata unavailable: "
                            "pragma_index_list is required to distinguish INTEGER PK DESC") from exc
                    raise
                if "pk" not in index_origins:
                    rowid_auto_column = str(pk_columns[0]["name"])
        if (
            table == "collaboration_business_role_events"
            and (rowid_auto_column is None
                 or rowid_auto_column.translate(ascii_fold) != "id")
        ):
            raise RuntimeError(
                "borrowed management schema incomplete: business role event id "
                "is not a proven INTEGER PRIMARY KEY rowid alias")

        for row in omitted:
            name = str(row["name"])
            generated = int(row["hidden"]) in (2, 3)
            default = row["dflt_value"]
            default_text = "" if default is None else str(default).strip()
            while len(default_text) >= 2 and default_text[0] == "(" and default_text[-1] == ")":
                default_text = default_text[1:-1].strip()
            default_is_null = default_text.upper() == "NULL"
            auto_id = name == rowid_auto_column
            if generated or auto_id or not bool(row["not_null"]):
                continue
            if default is None or default_is_null:
                raise RuntimeError(
                    "borrowed management schema incomplete: omitted required "
                    f"column {table}.{name} has no usable default")

    for trigger, expected_sql in expected_trigger_sql.items():
        with closing(conn.execute(
            "SELECT tbl_name,sql FROM main.sqlite_master "
            "WHERE type='trigger' AND name COLLATE NOCASE=?",
            (trigger,),
        )) as cursor:
            row = cursor.fetchone()
        if (
            row is None
            or str(row["tbl_name"]).translate(ascii_fold)
            != trigger_table.translate(ascii_fold)
        ):
            raise RuntimeError(
                "borrowed management schema incomplete: missing trigger "
                f"{trigger}")
        actual_sql = str(row["sql"] or "")
        if (
            _normalize_trigger_header(actual_sql, trigger, trigger_table)
            != _normalize_trigger_header(expected_sql, trigger, trigger_table)
        ):
            raise RuntimeError(
                "borrowed management schema incomplete: trigger "
                f"{trigger} is not the expected append-only definition")

    if require_backfill:
        with closing(conn.execute(
            "SELECT value FROM main.collaboration_settings WHERE key=?",
            (SCREENING_BACKFILL_KEY,),
        )) as cursor:
            row = cursor.fetchone()
        value = None if row is None else row["value"]
        if not isinstance(value, str) or not value.strip():
            raise RuntimeError(
                "borrowed management schema incomplete: screening backfill "
                "marker missing")
        value_text = value.strip()
        if "T" not in value_text:
            raise RuntimeError(
                "borrowed management schema incomplete: screening backfill "
                "marker is not a UTC completion timestamp")
        try:
            parsed = datetime.fromisoformat(
                value_text.replace("Z", "+00:00"))
        except ValueError as exc:
            raise RuntimeError(
                "borrowed management schema incomplete: screening backfill "
                "marker is not a UTC completion timestamp") from exc
        offset = parsed.utcoffset()
        if parsed.tzinfo is None or offset is None or offset.total_seconds() != 0:
            raise RuntimeError(
                "borrowed management schema incomplete: screening backfill "
                "marker is not a UTC completion timestamp")

    # EXPLAIN compiles only statements reachable from this fixed operation
    # profile; it does not execute a business statement or its opcodes.
    explain_probes: list[tuple[str, tuple[object, ...]]] = []
    if operation in {"grant_role", "set_business_role"}:
        explain_probes.append((
            "EXPLAIN INSERT INTO main.collaboration_role_grants"
            "(user_id,role,granted_by,granted_at) VALUES(?,?,?,?) "
            "ON CONFLICT(user_id,role) DO NOTHING",
            (1, role, 1, "1970-01-01T00:00:00Z"),
        ))
    if operation == "set_member_screening" or (
        operation in {"grant_role", "set_business_role"}
        and role == "reconciler"
    ):
        explain_probes.append((
            "EXPLAIN INSERT INTO main.collaboration_member_settings"
            "(user_id,screening_enabled,updated_by,updated_at) "
            "VALUES(?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET "
            "screening_enabled=excluded.screening_enabled",
            (1, 0, 1, "1970-01-01T00:00:00Z"),
        ))
    if operation == "set_business_role":
        explain_probes.extend([
            (
                "EXPLAIN INSERT INTO main.collaboration_business_role"
                "(user_id,business_role,set_by,set_at) VALUES(?,?,?,?) "
                "ON CONFLICT(user_id) DO UPDATE SET "
                "business_role=excluded.business_role",
                (1, role, 1, "1970-01-01T00:00:00Z"),
            ),
            (
                "EXPLAIN INSERT INTO "
                "main.collaboration_business_role_events"
                "(user_id,old_role,new_role,set_by,set_at) "
                "VALUES(?,?,?,?,?)",
                (1, None, role, 1, "1970-01-01T00:00:00Z"),
            ),
        ])
    if operation == "revoke_role":
        explain_probes.append((
            "EXPLAIN DELETE FROM main.collaboration_role_grants "
            "WHERE user_id=? AND role=?",
            (1, role),
        ))
        if role == "reconciler":
            explain_probes.append((
                "EXPLAIN DELETE FROM main.collaboration_member_settings "
                "WHERE user_id=?",
                (1,),
            ))
    for sql, params in explain_probes:
        try:
            cursor = conn.execute(sql, params)
            try:
                cursor.fetchall()
            finally:
                cursor.close()
        except sqlite3.OperationalError as exc:
            message = str(exc).lower()
            if (
                "no such column" in message
                or " has no column named " in message
                or "no such table" in message
                or "no such index" in message
                or "no such function" in message
                or "on conflict clause does not match" in message
                or "cannot upsert" in message
                or "upsert not implemented" in message
                or "ambiguous column name" in message
                or "syntax error" in message
            ):
                raise RuntimeError(
                    "borrowed management schema incomplete; EXPLAIN failed: "
                    f"{exc}") from exc
            raise

    yield conn, True


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


def _owner(
    conn: sqlite3.Connection, task_id: str | None, *,
    main_only: bool = False,
) -> sqlite3.Row:
    # 先判表存在再查询：把“库未初始化（无 collaboration_task 表）”归为
    # legacy（ValueError），而锁超时/读失败（OperationalError 等）原样上抛，
    # 由调用方决定是 fail-closed 布尔、还是 OwnerUnavailableError。
    prefix = "main." if main_only else ""
    has_table = conn.execute(
        f"SELECT 1 FROM {prefix}sqlite_master "
        "WHERE type='table' AND name='collaboration_task'"
    ).fetchone()
    if has_table is None:
        raise ValueError("task has no registered collaboration owner")
    row = conn.execute(
        "SELECT task_id, owner_user_id, owner_username, created_at "
        f"FROM {prefix}collaboration_task WHERE singleton=1"
    ).fetchone()
    if row is None or (task_id is not None and row["task_id"] != _task_id(task_id)):
        raise ValueError("task has no registered collaboration owner")
    return row


def _management_owner(
    conn: sqlite3.Connection, task_id: str | None,
) -> sqlite3.Row:
    """Resolve the main-schema owner while closing borrowed read cursors."""
    tables = _management_read_rows(
        conn,
        "SELECT 1 FROM main.sqlite_master "
        "WHERE type='table' AND name='collaboration_task'",
        (),
    )
    if not tables:
        raise ValueError("task has no registered collaboration owner")
    rows = _management_read_rows(
        conn,
        "SELECT task_id, owner_user_id, owner_username, created_at "
        "FROM main.collaboration_task WHERE singleton=1",
        (),
    )
    row = rows[0] if rows else None
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
    conn: sqlite3.Connection | None = None,
) -> dict[str, object]:
    """Grant a coder/reconciler role; only the registered owner may grant.

    handoff C5（复审扫尾）：task_id 必传，理由同 authorize_action。
    """
    actor_user_id = _user_id(actor_user_id, "actor_user_id")
    user_id = _user_id(user_id)
    role = _role(role)
    if task_id is None:
        raise ValueError("task_id is required for grant_role")
    with _management_conn(
        store, conn, operation="grant_role", role=role,
        require_member_settings=role == "reconciler",
        require_backfill=role == "reconciler",
    ) as (db, borrowed):
        table_prefix = "main." if borrowed else ""
        owner = (
            _management_owner(db, task_id) if borrowed
            else _owner(db, task_id, main_only=borrowed)
        )
        if int(owner["owner_user_id"]) != actor_user_id:
            raise PermissionError("only the task owner may grant collaboration roles")
        # 先完成存量回填（升级前库），再写入本次授权——保证「新授权默认关」
        # 与「存量授权默认开」互不污染。借用连接由协调器预先完成准备。
        if not borrowed:
            _migrate_member_settings(db)
        grant_parameters = (user_id, role, actor_user_id, _now())
        if borrowed:
            grant_rowcount = _management_statement_result(
                db,
                "INSERT INTO main.collaboration_role_grants"
                "(user_id,role,granted_by,granted_at) VALUES(?,?,?,?) "
                "ON CONFLICT(user_id,role) DO NOTHING",
                grant_parameters,
            )
        else:
            cursor = db.execute(
                "INSERT OR IGNORE INTO "
                "collaboration_role_grants"
                "(user_id,role,granted_by,granted_at) VALUES(?,?,?,?)",
                grant_parameters,
            )
            grant_rowcount = cursor.rowcount
        # 升级后新授权的 reconciler 默认关闭筛选参与权；重复授权（已有 grant）
        # 不重置负责人此后手动调整过的开关。
        if role == "reconciler" and grant_rowcount == 1:
            settings_sql = (
                "INSERT INTO "
                f"{table_prefix}collaboration_member_settings"
                "(user_id,screening_enabled,updated_by,updated_at) "
                "VALUES(?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET "
                "screening_enabled=0, updated_by=excluded.updated_by, "
                "updated_at=excluded.updated_at"
            )
            settings_parameters = (user_id, 0, actor_user_id, _now())
            if borrowed:
                _management_statement_result(db, settings_sql, settings_parameters)
            else:
                db.execute(settings_sql, settings_parameters)
        grant_select_sql = (
            "SELECT user_id,role,granted_by,granted_at FROM "
            f"{table_prefix}collaboration_role_grants "
            "WHERE user_id=? AND role=?"
        )
        if borrowed:
            with closing(db.execute(grant_select_sql, (user_id, role))) as cursor:
                row = cursor.fetchone()
            if row is None:
                raise RuntimeError(
                    "borrowed grant_role completed without a persisted role grant")
        else:
            row = db.execute(grant_select_sql, (user_id, role)).fetchone()
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
    conn: sqlite3.Connection, user_id: int, task_id: str, *, team: bool = False,
) -> bool:
    """连接内的筛选参与权判定（调用方负责异常映射）。

    ``team=False``（缺省）保持既有语义字节不变：属主/coder/legacy 恒 True、
    纯 reconciler 按参与开关——personal/legacy/solo 路径零回归。
    ``team=True``（2.1.6 FLOW II-A）：按单一有效业务角色判定——coder True；
    reconciler（含属主）按开关；未配置/双角色冲突 False（结构化原因由
    :func:`team_screening_permission` 提供，本布尔口径只做 fail-closed）。
    """
    owner = _owner(conn, task_id)
    owner_user_id = int(owner["owner_user_id"])
    if team:
        resolution = _resolve_business_role_in_conn(conn, owner_user_id, user_id)
        if not resolution["participant"]:
            return True  # 非角色成员不受该开关约束（沿用旧语义）
        if resolution["conflict"] or resolution["role"] is None:
            return False
        if resolution["role"] == "coder":
            return True
        return _reconciler_screening_enabled(conn, user_id)
    if owner_user_id == user_id:
        return True  # 属主永不受该开关影响（非 team 旧语义）
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
    store: str | Path, user_id: int, *, task_id: str | None = None,
    team: bool = False,
) -> bool:
    """该成员当前是否可写初筛/复筛（只读；不创建/不迁移任何表）。

    - ``team=False``（缺省，personal/legacy 调用方）：属主、coder（含
      coder+reconciler 双角色）、非角色成员恒为 True；纯 reconciler 读
      collaboration_member_settings；无表/无标记（升级前）-> True；确定性
      否定（无协作库/无属主）-> True（legacy 零回归）；
    - ``team=True``：见 :func:`_screening_allowed_in_conn` 的角色口径；
    - 锁超时/读失败 -> OwnerUnavailableError；永久损坏 ->
      CollaborationStoreCorruptError（绝不折叠成 False/403）。
    """
    user_id = _user_id(user_id)
    if task_id is None:
        raise ValueError("task_id is required for screening_allowed")
    try:
        with closing(_connect(Path(store), read_only=True)) as conn:
            return _screening_allowed_in_conn(conn, user_id, task_id, team=team)
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
    conn: sqlite3.Connection | None = None,
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
    borrowed = conn is not None
    try:
        with _management_conn(
            store, conn, operation="set_member_screening",
        ) as (db, borrowed_conn):
            table_prefix = "main." if borrowed_conn else ""
            owner = (
                _management_owner(db, task_id) if borrowed_conn
                else _owner(db, task_id, main_only=borrowed_conn)
            )
            owner_user_id = int(owner["owner_user_id"])
            if owner_user_id != actor_user_id:
                raise PermissionError("only the task owner may manage screening participation")
            if not borrowed_conn:
                _migrate_member_settings(db)
            grant_sql = (
                "SELECT 1 FROM "
                f"{table_prefix}collaboration_role_grants "
                "WHERE user_id=? AND role=?"
            )
            if borrowed_conn:
                with closing(db.execute(
                    grant_sql, (user_id, "reconciler"),
                )) as cursor:
                    grant = cursor.fetchone()
            else:
                grant = db.execute(
                    grant_sql, (user_id, "reconciler"),
                ).fetchone()
            if grant is None:
                # 2.1.6 FLOW II-A：选择 reconciler 的属主无自 grant 也须能配置
                # 参与开关（与普通同角色成员同等可配置，裁定 2 末条）。仅认
                # **当前有效** reconciler 角色（choice/单一 grant 派生），不认
                # 残留 choice 或旧 grants。
                if borrowed_conn:
                    resolution = _management_resolve_business_role(
                        db, owner_user_id, user_id)
                else:
                    resolution = _resolve_business_role_in_conn(
                        db, owner_user_id, user_id, main_only=borrowed_conn)
                if not (resolution["is_owner"] and resolution["role"] == "reconciler"):
                    raise ValueError("target user has no reconciler role")
            now = _now()
            settings_sql = (
                "INSERT INTO "
                f"{table_prefix}collaboration_member_settings"
                "(user_id,screening_enabled,updated_by,updated_at) "
                "VALUES(?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET "
                "screening_enabled=excluded.screening_enabled, "
                "updated_by=excluded.updated_by, "
                "updated_at=excluded.updated_at"
            )
            settings_parameters = (
                user_id, 1 if enabled else 0, actor_user_id, now)
            if borrowed_conn:
                _management_statement_result(
                    db, settings_sql, settings_parameters)
            else:
                db.execute(settings_sql, settings_parameters)
            settings_select_sql = (
                "SELECT screening_enabled, updated_by, updated_at FROM "
                f"{table_prefix}collaboration_member_settings "
                "WHERE user_id=?"
            )
            if borrowed_conn:
                with closing(db.execute(
                    settings_select_sql, (user_id,),
                )) as cursor:
                    row = cursor.fetchone()
            else:
                row = db.execute(
                    settings_select_sql, (user_id,),
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
        if borrowed:
            raise
        raise OwnerUnavailableError(f"screening setting write failed: {exc}") from exc
    except sqlite3.DatabaseError as exc:
        if borrowed:
            raise
        raise CollaborationStoreCorruptError(
            f"task collaboration store is unreadable: {exc}") from exc
    except (OSError, sqlite3.Error) as exc:
        if borrowed:
            raise
        raise OwnerUnavailableError(f"screening setting write failed: {exc}") from exc


# ---------------------------------------------------------------------------
# 业务角色（2.1.6 FLOW 阶段 II-A）：单一有效角色、配置 API 与 team 授权
# ---------------------------------------------------------------------------

def _ensure_business_role_schema(conn: sqlite3.Connection) -> None:
    """幂等建业务角色表/审计表/append-only 触发器（仅在写事务内调用）。

    逐条 ``CREATE ... IF NOT EXISTS`` 而不用 ``executescript``：后者会先提交
    挂起事务，破坏 ``_write`` 的 BEGIN IMMEDIATE 串行化（与 revoke 的互斥）。
    """
    for statement in _BUSINESS_ROLE_SCHEMA:
        conn.execute(statement)


def _business_role_choice(
    conn: sqlite3.Connection, user_id: int, *, main_only: bool = False,
) -> str | None:
    """读单一有效角色选择；旧库未建表 = 无选择（None）。只读，绝不建表。"""
    table = (
        "main.collaboration_business_role" if main_only
        else "collaboration_business_role"
    )
    try:
        row = conn.execute(
            f"SELECT business_role FROM {table} WHERE user_id=?",
            (user_id,),
        ).fetchone()
    except sqlite3.OperationalError as exc:
        if "no such table" not in str(exc).lower():
            raise
        return None
    return row[0] if row is not None else None


def _reconciler_screening_enabled(
    conn: sqlite3.Connection, user_id: int, *, main_only: bool = False,
) -> bool:
    """reconciler 有效角色的筛选参与开关（含属主），新旧迁移口径与
    ``_screening_allowed_in_conn`` 完全一致。只读。"""
    prefix = "main." if main_only else ""
    try:
        row = conn.execute(
            f"SELECT screening_enabled FROM "
            f"{prefix}collaboration_member_settings WHERE user_id=?",
            (user_id,),
        ).fetchone()
        if row is not None:
            return bool(row[0])
        completed = conn.execute(
            f"SELECT 1 FROM {prefix}collaboration_settings WHERE key=?",
            (SCREENING_BACKFILL_KEY,),
        ).fetchone()
        return completed is None
    except sqlite3.OperationalError as exc:
        if "no such table" not in str(exc).lower():
            raise
        return True


def _resolve_business_role_in_conn(
    conn: sqlite3.Connection, owner_user_id: int, user_id: int, *,
    main_only: bool = False,
) -> dict:
    """连接内解析单一有效业务角色（读时派生，零回写）。

    规则（OWNER_BUSINESS_ROLE_DECISION_20261114；fix-r1 R1）：
    - choice 优先：属主的 choice 恒有效（成员权来自所有权）；成员的 choice
      须仍持有该角色 grant——撤销所选授权后旧 choice 不能独立维持权限，
      **也不得静默回退为另一旧 grant**（role=None、source="stale_choice"，
      业务未配置/不可用；属主显式重新选择才可授另一能力）。
    - 无 choice：恰持单一 grant → 复用该角色（旧单 grant 兼容，不误锁）；
      双 grant → conflict（不猜选）；零 grant → 属主为未配置、成员为非参与者。
    - ``required_screener``：coder 恒需筛选；reconciler 按参与开关；属主同规则。
    """
    is_owner_user = owner_user_id == user_id
    prefix = "main." if main_only else ""
    grants = {str(row[0]) for row in conn.execute(
        f"SELECT role FROM {prefix}collaboration_role_grants WHERE user_id=?",
        (user_id,))}
    choice = _business_role_choice(conn, user_id, main_only=main_only)
    role: str | None = None
    source = "none"
    conflict = False
    if choice is not None:
        if is_owner_user or choice in grants:
            role, source = choice, "choice"
        else:
            # R1（fix-r1）：所选 grant 已撤销的失效 choice——不回退另一旧
            # grant、不删 choice（审计保留）；有效角色不可用，等显式重选。
            source = "stale_choice"
    elif len(grants) == 1:
        role, source = next(iter(grants)), "grant"
    elif len(grants) >= 2:
        conflict = True
    participant = is_owner_user or bool(grants)
    screening_enabled: bool | None = None
    if role == "reconciler":
        screening_enabled = _reconciler_screening_enabled(
            conn, user_id, main_only=main_only)
    if conflict or role is None:
        config = "conflict" if conflict else "unconfigured"
    else:
        config = "ok"
    return {
        "user_id": user_id,
        "is_owner": is_owner_user,
        "participant": participant,
        "role": role,
        "source": source,
        "conflict": conflict,
        "screening_enabled": screening_enabled,
        "required_screener": role == "coder" or (
            role == "reconciler" and bool(screening_enabled)),
        "config": config,
    }


def _management_resolve_business_role(
    conn: sqlite3.Connection, owner_user_id: int, user_id: int,
) -> dict:
    """Resolve a role on a borrowed connection and close each read cursor."""
    is_owner_user = owner_user_id == user_id
    grant_rows = _management_read_rows(
        conn,
        "SELECT role FROM main.collaboration_role_grants WHERE user_id=?",
        (user_id,),
    )
    grants = {str(row[0]) for row in grant_rows}
    choice_rows = _management_read_rows(
        conn,
        "SELECT business_role FROM main.collaboration_business_role WHERE user_id=?",
        (user_id,),
        optional_table=True,
    )
    choice = choice_rows[0][0] if choice_rows else None
    role: str | None = None
    source = "none"
    conflict = False
    if choice is not None:
        if is_owner_user or choice in grants:
            role, source = choice, "choice"
        else:
            source = "stale_choice"
    elif len(grants) == 1:
        role, source = next(iter(grants)), "grant"
    elif len(grants) >= 2:
        conflict = True
    participant = is_owner_user or bool(grants)
    screening_enabled: bool | None = None
    if role == "reconciler":
        screening_rows = _management_read_rows(
            conn,
            "SELECT screening_enabled FROM main.collaboration_member_settings WHERE user_id=?",
            (user_id,),
            optional_table=True,
        )
        if screening_rows is None:
            screening_enabled = True
        elif screening_rows:
            screening_enabled = bool(screening_rows[0][0])
        else:
            completed_rows = _management_read_rows(
                conn,
                "SELECT 1 FROM main.collaboration_settings WHERE key=?",
                (SCREENING_BACKFILL_KEY,),
                optional_table=True,
            )
            screening_enabled = not completed_rows
    if conflict or role is None:
        config = "conflict" if conflict else "unconfigured"
    else:
        config = "ok"
    return {
        "user_id": user_id,
        "is_owner": is_owner_user,
        "participant": participant,
        "role": role,
        "source": source,
        "conflict": conflict,
        "screening_enabled": screening_enabled,
        "required_screener": role == "coder" or (
            role == "reconciler" and bool(screening_enabled)),
        "config": config,
    }


def resolve_business_role(
    store: str | Path, *, user_id: int, task_id: str | None = None,
) -> dict:
    """单一有效业务角色解析（只读；异常口径与 authorize_action 一致）。

    确定性否定（库不存在/无属主/绑定不匹配）返回 ``participant=False`` 的
    未配置视图——legacy/personal 调用方不应走本函数（team 专用），误用时
    得到的是"无团队角色"而非异常；锁超时/读失败/永久损坏照 503/修库契约上抛。
    """
    user_id = _user_id(user_id)
    if task_id is None:
        raise ValueError("task_id is required for resolve_business_role")
    try:
        with closing(_connect(Path(store), read_only=True)) as conn:
            owner = _owner(conn, task_id)
            return _resolve_business_role_in_conn(
                conn, int(owner["owner_user_id"]), user_id)
    except FileNotFoundError:
        return {"user_id": user_id, "is_owner": False, "participant": False,
                "role": None, "source": "none", "conflict": False,
                "screening_enabled": None, "required_screener": False,
                "config": "unconfigured"}
    except ValueError:
        return {"user_id": user_id, "is_owner": False, "participant": False,
                "role": None, "source": "none", "conflict": False,
                "screening_enabled": None, "required_screener": False,
                "config": "unconfigured"}
    except sqlite3.OperationalError as exc:
        raise OwnerUnavailableError(f"business role lookup failed: {exc}") from exc
    except sqlite3.DatabaseError as exc:
        raise CollaborationStoreCorruptError(
            f"task collaboration store is unreadable: {exc}") from exc
    except (OSError, sqlite3.Error) as exc:
        raise OwnerUnavailableError(f"business role lookup failed: {exc}") from exc


def list_business_roles(
    store: str | Path, *, actor_user_id: int, task_id: str | None = None,
) -> list[dict]:
    """属主视角的全部参与者有效角色清单（只读；异常契约同 list_role_grants）。

    参与者 = 属主 + 当前 grant 持有者；choice 行不维持成员关系（已移除者
    不出现）。GET 侧绝不建表——旧库未建 choice 表按"无选择"派生。
    """
    actor_user_id = _user_id(actor_user_id, "actor_user_id")
    if task_id is None:
        raise ValueError("task_id is required for list_business_roles")
    try:
        with closing(_connect(Path(store), read_only=True)) as conn:
            owner = _owner(conn, task_id)
            if int(owner["owner_user_id"]) != actor_user_id:
                raise PermissionError("only the task owner may list business roles")
            owner_user_id = int(owner["owner_user_id"])
            ids = {owner_user_id}
            ids.update(int(row[0]) for row in conn.execute(
                "SELECT DISTINCT user_id FROM collaboration_role_grants"))
            return [
                {**_resolve_business_role_in_conn(conn, owner_user_id, uid),
                 "grants": sorted(str(row[0]) for row in conn.execute(
                     "SELECT role FROM collaboration_role_grants WHERE user_id=?",
                     (uid,)))}
                for uid in sorted(ids)
            ]
    except PermissionError:
        raise
    except (FileNotFoundError, ValueError):
        raise PermissionError("only the task owner may list business roles")
    except sqlite3.OperationalError as exc:
        raise OwnerUnavailableError(f"business role lookup failed: {exc}") from exc
    except sqlite3.DatabaseError as exc:
        raise CollaborationStoreCorruptError(
            f"task collaboration store is unreadable: {exc}") from exc
    except (OSError, sqlite3.Error) as exc:
        raise OwnerUnavailableError(f"business role lookup failed: {exc}") from exc


def set_business_role(
    store: str | Path,
    *,
    actor_user_id: int,
    user_id: int,
    business_role: str,
    task_id: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> dict[str, object]:
    """配置单一有效业务角色（仅属主；单事务原子互斥 + append-only 审计）。

    语义（合同 §2 / 裁定 2）：
    - 目标须为属主本人或**当前** grant 持有者——choice 不授予已移除/未知/
      跨任务账号成员权；
    - 成员切换到尚无 grant 的角色 = 本次显式授权：同一事务补齐所选 grant
      （与 grant_role 同口径：新 reconciler grant 默认关参与）+ choice + 审计；
      旧 grant 保留为历史来源，但不再绕过有效单选（解析器优先 choice）；
    - 不调用 revoke/quarantine、不改名/删除任何个人库；变更历史完整落
      ``collaboration_business_role_events``（目标/前值/新值/设置者/时间）。
    """
    actor_user_id = _user_id(actor_user_id, "actor_user_id")
    user_id = _user_id(user_id)
    business_role = _role(business_role)
    if task_id is None:
        raise ValueError("task_id is required for set_business_role")
    with _management_conn(
        store, conn, operation="set_business_role", role=business_role,
        require_business=True,
    ) as (db, borrowed):
        table_prefix = "main." if borrowed else ""
        owner = (
            _management_owner(db, task_id) if borrowed
            else _owner(db, task_id, main_only=borrowed)
        )
        if int(owner["owner_user_id"]) != actor_user_id:
            raise PermissionError("only the task owner may set business roles")
        if not borrowed:
            _ensure_business_role_schema(db)
            _migrate_member_settings(db)
        owner_user_id = int(owner["owner_user_id"])
        grants_sql = (
            "SELECT role FROM "
            f"{table_prefix}collaboration_role_grants WHERE user_id=?"
        )
        if borrowed:
            with closing(db.execute(grants_sql, (user_id,))) as cursor:
                grants = {str(row[0]) for row in cursor.fetchall()}
        else:
            grants = {str(row[0]) for row in db.execute(
                grants_sql, (user_id,))}
        is_owner_target = owner_user_id == user_id
        if not is_owner_target and not grants:
            raise ValueError(
                "target user is not a current task participant; a role choice "
                "cannot grant membership")
        # R10（fix-r1）：审计 old_role/响应 previous_role 记**切换前有效单值
        # 角色**（choice→单一 grant 派生），不再只读旧 choice——单 coder grant
        # 无 choice 成员首切 reconciler 时 old_role 如实为 "coder"。未配置/
        # 冲突的旧状态有效角色为 None（响应 previous_config 区分二者，不伪造）。
        if borrowed:
            prior = _management_resolve_business_role(db, owner_user_id, user_id)
        else:
            prior = _resolve_business_role_in_conn(
                db, owner_user_id, user_id, main_only=borrowed)
        previous_role = prior["role"]
        now = _now()
        choice_sql = (
            "INSERT INTO "
            f"{table_prefix}collaboration_business_role"
            "(user_id,business_role,set_by,set_at) "
            "VALUES(?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET "
            "business_role=excluded.business_role, set_by=excluded.set_by, "
            "set_at=excluded.set_at"
        )
        choice_parameters = (user_id, business_role, actor_user_id, now)
        event_sql = (
            "INSERT INTO "
            f"{table_prefix}collaboration_business_role_events"
            "(user_id,old_role,new_role,set_by,set_at) VALUES(?,?,?,?,?)"
        )
        event_parameters = (user_id, previous_role, business_role, actor_user_id, now)
        if borrowed:
            _management_statement_result(db, choice_sql, choice_parameters)
            _management_statement_result(db, event_sql, event_parameters)
        else:
            db.execute(choice_sql, choice_parameters)
            db.execute(event_sql, event_parameters)
        # 成员侧 choice 兼作显式授权：补齐所选 grant（reconciler 默认关参与，
        # 与 grant_role 一致）；属主无需 grant（成员权来自所有权）。
        if not is_owner_target and business_role not in grants:
            grant_parameters = (user_id, business_role, actor_user_id, now)
            if borrowed:
                grant_rowcount = _management_statement_result(
                    db,
                    "INSERT INTO main.collaboration_role_grants"
                    "(user_id,role,granted_by,granted_at) VALUES(?,?,?,?) "
                    "ON CONFLICT(user_id,role) DO NOTHING",
                    grant_parameters,
                )
                with closing(db.execute(
                    "SELECT 1 FROM main.collaboration_role_grants "
                    "WHERE user_id=? AND role=?",
                    (user_id, business_role),
                )) as cursor:
                    grant_exists = cursor.fetchone()
                if grant_exists is None:
                    raise RuntimeError(
                        "borrowed set_business_role completed without a persisted role grant")
            else:
                cursor = db.execute(
                    "INSERT OR IGNORE INTO "
                    "collaboration_role_grants"
                    "(user_id,role,granted_by,granted_at) VALUES(?,?,?,?)",
                    grant_parameters)
                grant_rowcount = cursor.rowcount
            if business_role == "reconciler" and grant_rowcount == 1:
                settings_sql = (
                    "INSERT INTO "
                    f"{table_prefix}collaboration_member_settings"
                    "(user_id,screening_enabled,updated_by,updated_at) "
                    "VALUES(?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET "
                    "screening_enabled=0, updated_by=excluded.updated_by, "
                    "updated_at=excluded.updated_at"
                )
                settings_parameters = (user_id, 0, actor_user_id, now)
                if borrowed:
                    _management_statement_result(
                        db, settings_sql, settings_parameters)
                else:
                    db.execute(settings_sql, settings_parameters)
        return {
            "user_id": user_id,
            "business_role": business_role,
            "previous_role": previous_role,
            "previous_config": prior["config"],
            "is_owner": is_owner_target,
            "set_by": actor_user_id,
            "changed_at": now,
        }


def authorize_team_action(
    store: str | Path, user_id: int, action: str, *, task_id: str | None = None,
) -> bool:
    """team 任务的业务动作授权：按**单一有效业务角色**逐 action 判定。

    与 :func:`authorize_action` 的区别只在属主分支：管理身份不再直通业务，
    属主按其有效角色（choice → 单一自 grant → 未配置）同普通成员规则判定。
    非 team（personal/legacy）调用方继续用 authorize_action，语义零变化。

    返回/异常：
    - True：角色匹配；
    - False：非参与者（无 grant 且非属主）——调用方沿用既有 403；
    - TeamRoleUnconfigured / TeamRoleConflict（409）、TeamRoleDenied（403）：
      结构化角色门（params: participant/action/required_role）；
    - 基础设施异常与 authorize_action 同契约（503/修库）。
    """
    user_id = _user_id(user_id)
    if action not in _ACTION_ROLES:
        raise ValueError(f"unknown collaboration action: {action}")
    if task_id is None:
        raise ValueError("task_id is required for authorize_team_action")
    try:
        with closing(_connect(Path(store), read_only=True)) as conn:
            owner = _owner(conn, task_id)
            resolution = _resolve_business_role_in_conn(
                conn, int(owner["owner_user_id"]), user_id)
            if not resolution["participant"]:
                return False
            if resolution["conflict"]:
                raise TeamRoleConflict(
                    "该成员同时持有编码者与仲裁者授权，请先选择单一有效业务角色。",
                    params={"participant": user_id})
            if resolution["role"] is None:
                raise TeamRoleUnconfigured(
                    "该成员的业务角色尚未配置，请先在成员管理中选择编码者或仲裁者。",
                    params={"participant": user_id})
            required_role = _ACTION_ROLES[action]
            if resolution["role"] != required_role:
                raise TeamRoleDenied(
                    "当前业务角色无权执行该操作。",
                    params={"participant": user_id, "action": action,
                            "required_role": required_role})
            return True
    except (TeamRoleGateError, PermissionError, ValueError):
        raise
    except FileNotFoundError:
        return False  # 库不存在 = legacy：确无角色（与 authorize_action 同口径）
    except sqlite3.OperationalError as exc:
        raise OwnerUnavailableError(f"team action authorization lookup failed: {exc}") from exc
    except sqlite3.DatabaseError as exc:
        raise CollaborationStoreCorruptError(
            f"task collaboration store is unreadable: {exc}") from exc
    except (OSError, sqlite3.Error) as exc:
        raise OwnerUnavailableError(f"team action authorization lookup failed: {exc}") from exc


def authorize_team_read(
    store: str | Path, user_id: int, action: str, *, task_id: str | None = None,
) -> bool:
    """team 任务的共识级/汇总/历史**读取**授权（R-1：管理读取保留属主）。

    与 :func:`authorize_team_action`（业务写，严格角色 + 结构化 409/403）互补：
    - 属主恒 True——无论其业务角色已配置/未配置/冲突，既有管理/汇总/历史
      查看不因角色收紧，也不因此获得任何业务写；
    - 成员须该 action 的**当前有效单一角色**（如 consensus:read → reconciler）；
      缺配置/冲突/角色不符一律 False——读拒绝沿用各端点既有 403 形状，
      **不抛** team.role.* 结构化错误（那属于写路径）；
    - 非参与者 False；基础设施异常照 503/修库契约。
    """
    user_id = _user_id(user_id)
    if action not in _ACTION_ROLES:
        raise ValueError(f"unknown collaboration action: {action}")
    if task_id is None:
        raise ValueError("task_id is required for authorize_team_read")
    try:
        with closing(_connect(Path(store), read_only=True)) as conn:
            owner = _owner(conn, task_id)
            if int(owner["owner_user_id"]) == user_id:
                return True
            resolution = _resolve_business_role_in_conn(
                conn, int(owner["owner_user_id"]), user_id)
            if not resolution["participant"] or resolution["conflict"]:
                return False
            return resolution["role"] == _ACTION_ROLES[action]
    except FileNotFoundError:
        return False  # 库不存在 = legacy：确无角色（与 authorize_action 同口径）
    except ValueError:
        return False
    except sqlite3.OperationalError as exc:
        raise OwnerUnavailableError(f"team read authorization lookup failed: {exc}") from exc
    except sqlite3.DatabaseError as exc:
        raise CollaborationStoreCorruptError(
            f"task collaboration store is unreadable: {exc}") from exc
    except (OSError, sqlite3.Error) as exc:
        raise OwnerUnavailableError(f"team read authorization lookup failed: {exc}") from exc


def has_team_action_role(
    store: str | Path, *, user_id: int, action: str, task_id: str | None = None,
) -> bool:
    """authorize_team_action 的纯布尔版（能力显示/第三方提案人核对）。

    未配置/冲突/角色不符/非参与者一律 False（不抛角色门异常）；基础设施
    异常照 503/修库契约上抛。"""
    try:
        return authorize_team_action(store, user_id, action, task_id=task_id)
    except TeamRoleGateError:
        return False


def team_screening_permission(
    store: str | Path, *, user_id: int, task_id: str | None = None,
) -> dict:
    """team 任务的筛选写资格（含原因令牌，供结构化错误映射）。

    返回 ``{allowed, reason, role, is_owner, participant}``；reason ∈
    None | "unconfigured" | "conflict" | "reconciler_disabled"。非参与者
    （无 grant 且非属主）沿用旧语义 allowed=True（成员资格由其他门核，本
    开关 historically 不约束非角色成员）。"""
    user_id = _user_id(user_id)
    if task_id is None:
        raise ValueError("task_id is required for team_screening_permission")
    try:
        with closing(_connect(Path(store), read_only=True)) as conn:
            owner = _owner(conn, task_id)
            resolution = _resolve_business_role_in_conn(
                conn, int(owner["owner_user_id"]), user_id)
            if not resolution["participant"]:
                return {"allowed": True, "reason": None, "role": None,
                        "is_owner": resolution["is_owner"], "participant": False}
            if resolution["conflict"]:
                return {"allowed": False, "reason": "conflict", "role": None,
                        "is_owner": resolution["is_owner"], "participant": True}
            if resolution["role"] is None:
                return {"allowed": False, "reason": "unconfigured", "role": None,
                        "is_owner": resolution["is_owner"], "participant": True}
            if resolution["role"] == "coder":
                return {"allowed": True, "reason": None, "role": "coder",
                        "is_owner": resolution["is_owner"], "participant": True}
            enabled = _reconciler_screening_enabled(conn, user_id)
            return {"allowed": enabled, "reason": None if enabled else "reconciler_disabled",
                    "role": "reconciler", "is_owner": resolution["is_owner"],
                    "participant": True}
    except FileNotFoundError:
        return {"allowed": True, "reason": None, "role": None,
                "is_owner": False, "participant": False}
    except ValueError:
        return {"allowed": True, "reason": None, "role": None,
                "is_owner": False, "participant": False}
    except sqlite3.OperationalError as exc:
        raise OwnerUnavailableError(f"team screening permission lookup failed: {exc}") from exc
    except sqlite3.DatabaseError as exc:
        raise CollaborationStoreCorruptError(
            f"task collaboration store is unreadable: {exc}") from exc
    except (OSError, sqlite3.Error) as exc:
        raise OwnerUnavailableError(f"team screening permission lookup failed: {exc}") from exc


def active_screening_roster(
    store: str | Path, *, task_id: str | None = None,
) -> list[dict] | None:
    """当前应筛/角色配置投影的数据层（只读；用户名/slug 由调用方 enrich）。

    每参与者一项：user_id / is_owner / role / source / conflict /
    screening_enabled（仅 reconciler 有值）/ required_screener / config。
    参与者 = 属主 + 当前 grant 持有者（choice/db 残留不维持成员关系）；可读
    名册恒含属主（≥1 项）。**R9（fix-r1）**：库不存在/无属主行/绑定不匹配对
    已确认 team 任务是"名册不可读"，返回 ``None``（调用方省略投影键或显式
    不可用），绝不伪装空名册 ``[]``；真实空名册在本数据层不可能出现。锁超时/
    读失败/永久损坏照 503/修库契约上抛。username 解析失败的账号照常保留
    （user_id 为稳定身份）。"""
    if task_id is None:
        raise ValueError("task_id is required for active_screening_roster")
    try:
        with closing(_connect(Path(store), read_only=True)) as conn:
            owner = _owner(conn, task_id)
            owner_user_id = int(owner["owner_user_id"])
            ids = {owner_user_id}
            ids.update(int(row[0]) for row in conn.execute(
                "SELECT DISTINCT user_id FROM collaboration_role_grants"))
            return [
                _resolve_business_role_in_conn(conn, owner_user_id, uid)
                for uid in sorted(ids)
            ]
    except FileNotFoundError:
        return None  # R9：库消失＝不可读（可读名册恒≥1），非真实空名册
    except ValueError:
        return None  # 无属主行/绑定不匹配＝不可读，不伪装 []
    except sqlite3.OperationalError as exc:
        raise OwnerUnavailableError(f"active roster lookup failed: {exc}") from exc
    except sqlite3.DatabaseError as exc:
        raise CollaborationStoreCorruptError(
            f"task collaboration store is unreadable: {exc}") from exc
    except (OSError, sqlite3.Error) as exc:
        raise OwnerUnavailableError(f"active roster lookup failed: {exc}") from exc


def has_non_owner_grants(
    store: str | Path, *, task_id: str, owner_user_id: int,
) -> bool:
    """协作库是否存在**当前非属主** grant（R6 fix-r1 只读派生辅助）。

    前置契约：调用方（readonly_context/is_team）刚以同一 task_id 完成属主
    解析（get_owner 非 None）——因此本函数内的库/属主异常都是**中途状态
    变化或不可读**，按 OwnerUnavailableError（503 重试）/
    CollaborationStoreCorruptError（修库契约）上抛，绝不折叠成"无 grant →
    personal"：
    - 属主行消失/绑定不匹配（``_owner`` ValueError）→ OwnerUnavailableError；
    - 属主 user_id 与调用方刚验证值不一致（rebind 竞态）→ OwnerUnavailableError；
    - grants 表缺失而 collaboration_task 存在＝初始化契约不可出现态 →
      OwnerUnavailableError（不猜"无 grant"）；
    - 可验证的"不存在业务状态"仅一种：grants 表存在且无非属主行 → False。

    只读、零建表、零迁移、零 latch（与 list_role_grants 的 PermissionError
    折叠口径不同，本函数逐来源精确分类）。"""
    owner_user_id = _user_id(owner_user_id, "owner_user_id")
    task_id = _task_id(task_id)
    try:
        with closing(_connect(Path(store), read_only=True)) as conn:
            try:
                owner = _owner(conn, task_id)
            except ValueError as exc:
                raise OwnerUnavailableError(
                    "collaboration owner changed during read; retry") from exc
            if int(owner["owner_user_id"]) != owner_user_id:
                raise OwnerUnavailableError(
                    "collaboration owner changed during read; retry")
            has_table = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='collaboration_role_grants'").fetchone()
            if has_table is None:
                raise OwnerUnavailableError(
                    "collaboration grants table missing after owner verification")
            row = conn.execute(
                "SELECT 1 FROM collaboration_role_grants WHERE user_id != ? LIMIT 1",
                (owner_user_id,)).fetchone()
            return row is not None
    except OwnerUnavailableError:
        raise
    except FileNotFoundError as exc:
        raise OwnerUnavailableError(
            f"collaboration store unreadable: {exc}") from exc
    except sqlite3.OperationalError as exc:
        # 锁忙/暂时读失败＝503 重试语义（顺序先于 DatabaseError：其子类）。
        raise OwnerUnavailableError(
            f"collaboration grant lookup failed: {exc}") from exc
    except sqlite3.DatabaseError as exc:
        raise CollaborationStoreCorruptError(
            f"task collaboration store is unreadable: {exc}") from exc
    except (OSError, sqlite3.Error) as exc:
        raise OwnerUnavailableError(
            f"collaboration grant lookup failed: {exc}") from exc


def read_team_role_state_on(
    conn: sqlite3.Connection, *, task_id: str,
) -> dict:
    """S1c（2.1.6 team flow）：在**调用方既有连接**上只读聚合当前角色状态。

    与 path 版 :func:`active_screening_roster` 的区别：零自开连接、零建库、
    零 row_factory 假设——owner 行按显式列＋**位置解码**（不调用依赖
    named-row 的 ``_owner``），tuple/Row 双兼容；供
    ``custom_backend.team_screening_runtime`` 的 fresh/borrowed 读作用域复用。
    业务角色解析仍走 :func:`_resolve_business_role_in_conn`（其内部全为位置
    访问）。**不**读取 owner 个人库的 ``team_latched``（latch 属 owner
    catalog 库，由调用方从 owner 连接现读后合成 team 判定）；本函数只报告
    协作库角色事实。

    返回（三分类；``unconfirmed`` 不得被调用方折叠成 legacy/personal）：

    - ``classification``：``owner_match``（匹配当前 task 的 owner）；
      ``legacy_uninitialized``（确证无 ``collaboration_task`` 表＝从未启用
      协作）；``unconfirmed``（有表但缺 singleton 行／task 绑定不匹配／
      owner 字段坏／``collaboration_role_grants`` 表缺失等不可确认态）。
    - ``owner``：owner_match 时的属主行（task_id/owner_user_id/
      owner_username/created_at），其余 ``None``。
    - ``has_non_owner_grants``：participants 中存在当前非属主 grant。
    - ``participants``：owner＋全部当前 grant 持有者的角色解析（含未配置／
      失效 choice／冲突者——**不过滤**，awaiting_config 判定交给资格核心）。

    契约：只读——不 BEGIN/commit/rollback/close、不改 PRAGMA/row_factory；
    ``task_id`` 形状非法抛 ``ValueError``（复用 :func:`_task_id`）；grants
    行损坏（user_id 非正整数）按 ``unconfirmed`` fail-closed，不猜"无 grant"。

    FIX-R1 F5/F6（本函数内收敛，不改旧 path API/旧 resolver）：
    - F5：owner 行/grants 查询遇**结构损坏**（no such column/table）→
      ``unconfirmed``（detail 携带原因）；瞬时读故障（busy/locked/IO）原样
      上抛由调用方 typed 映射。grants 聚合逐行校验 user_id 为正整数。
    - F6：已回填（``collaboration_settings`` 存在
      ``screening_enabled_backfill_completed_at`` 标记）而
      ``collaboration_member_settings`` 表缺失＝损坏 → ``unconfirmed``
      （fail-closed，防止旧 resolver 兼容分支把缺失表当"默认开"）；确证
      旧版（无标记）沿旧只读兼容。

    FIX-R2（同上收敛）：
    - R2-3：owner_user_id 与 grant user_id **转换前**严格 ``type is int`` 且
      >0（INTEGER affinity 可存 REAL，int() 截断会冒充属主/吞 grant）。
    - R2-5：participants 解析（经 ``_resolve_business_role_in_conn``）对
      no such column/table 窄捕获 → ``unconfirmed``；busy/IO 原样上抛；
      ``ProgrammingError`` 透传。
    """
    task_id = _task_id(task_id)

    def _unconfirmed(detail: str) -> dict:
        # 返回形状保持冻结契约（四键）；诊断细节进日志（不扩 DTO/返回形状）。
        logger.warning("read_team_role_state_on: unconfirmed — %s", detail)
        return {"classification": "unconfirmed", "owner": None,
                "has_non_owner_grants": False, "participants": []}

    def _structural(exc: sqlite3.Error) -> bool:
        message = str(exc).lower()
        return "no such column" in message or "no such table" in message

    has_owner_table = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' "
        "AND name='collaboration_task'"
    ).fetchone()
    if has_owner_table is None:
        return {"classification": "legacy_uninitialized", "owner": None,
                "has_non_owner_grants": False, "participants": []}
    try:
        row = conn.execute(
            "SELECT task_id, owner_user_id, owner_username, created_at "
            "FROM collaboration_task WHERE singleton=1"
        ).fetchone()
    except sqlite3.Error as exc:
        if _structural(exc):
            return _unconfirmed(f"collaboration_task 结构损坏: {exc}")
        raise
    if row is None:
        return _unconfirmed("collaboration_task 缺 singleton 行")
    bound_task, owner_user_id, owner_username, created_at = row[0], row[1], row[2], row[3]
    # FIX-R2 R2-3：转换前严格原值类型——INTEGER affinity 可存 REAL（如 1.5），
    # int() 截断会冒充属主/吞 grant；只接受 type(...) is int 且 >0。
    if isinstance(owner_user_id, bool) or type(owner_user_id) is not int \
            or owner_user_id <= 0:
        return _unconfirmed(f"owner_user_id 非严格正整数: {owner_user_id!r}")
    if (bound_task != task_id
            or not isinstance(owner_username, str) or not owner_username
            or not isinstance(created_at, str) or not created_at):
        return _unconfirmed("collaboration_task 行字段坏或 task 绑定不匹配")
    has_grants_table = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' "
        "AND name='collaboration_role_grants'"
    ).fetchone()
    if has_grants_table is None:
        # 协作库初始化契约下两表同建；有 owner 表而无 grants 表＝不可确认。
        return _unconfirmed("collaboration_role_grants 表缺失")
    ids = {owner_user_id}
    try:
        for grant_row in conn.execute(
                "SELECT DISTINCT user_id FROM collaboration_role_grants"):
            # FIX-R2 R2-3：同 owner 规则——type 严格正整数，不做 int() 容错
            # （REAL 1.5 等非整原值＝损坏来源，不猜"无 grant"）。
            value = grant_row[0]
            if isinstance(value, bool) or type(value) is not int or value <= 0:
                return _unconfirmed(f"grant user_id 非严格正整数: {value!r}")
            ids.add(value)
    except sqlite3.Error as exc:
        if _structural(exc):
            return _unconfirmed(f"collaboration_role_grants 结构损坏: {exc}")
        raise
    # FIX-R1 F6：已回填缺成员设置表＝损坏（fail-closed）。
    has_settings_table = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' "
        "AND name='collaboration_settings'"
    ).fetchone()
    if has_settings_table is not None:
        try:
            backfilled = conn.execute(
                "SELECT 1 FROM collaboration_settings WHERE key=?",
                (SCREENING_BACKFILL_KEY,)).fetchone()
        except sqlite3.Error as exc:
            if _structural(exc):
                return _unconfirmed(f"collaboration_settings 结构损坏: {exc}")
            raise
        if backfilled is not None:
            has_member_settings = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='collaboration_member_settings'"
            ).fetchone()
            if has_member_settings is None:
                return _unconfirmed(
                    "已回填(screening_enabled_backfill_completed_at)而 "
                    "collaboration_member_settings 表缺失")
    # FIX-R2 R2-5：resolver 查询（如 grants 缺 role 列）窄结构捕获——
    # no such column/table → unconfirmed；busy/IO 等 OperationalError 原样
    # 上抛（调用方 typed collab_unreadable，不伪"缺配置"）；ProgrammingError
    # （调用写法缺陷）透传。
    try:
        participants = [
            _resolve_business_role_in_conn(conn, owner_user_id, uid)
            for uid in sorted(ids)
        ]
    except sqlite3.ProgrammingError:
        raise
    except sqlite3.OperationalError as exc:
        if _structural(exc):
            return _unconfirmed(f"角色解析必需结构缺失: {exc}")
        raise
    return {
        "classification": "owner_match",
        "owner": {"task_id": bound_task, "owner_user_id": owner_user_id,
                  "owner_username": owner_username, "created_at": created_at},
        "has_non_owner_grants": any(not p["is_owner"] for p in participants),
        "participants": participants,
    }


def revoke_role(
    store: str | Path,
    *,
    actor_user_id: int,
    user_id: int,
    role: str,
    task_id: str | None = None,
    conn: sqlite3.Connection | None = None,
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
    reconciler = role == "reconciler"
    with _management_conn(
        store, conn, operation="revoke_role", role=role,
        require_member_settings=reconciler,
        require_backfill=reconciler,
    ) as (db, borrowed):
        table_prefix = "main." if borrowed else ""
        owner = (
            _management_owner(db, task_id) if borrowed
            else _owner(db, task_id, main_only=borrowed)
        )
        if int(owner["owner_user_id"]) != actor_user_id:
            raise PermissionError("only the task owner may revoke collaboration roles")
        delete_grant_sql = (
            "DELETE FROM "
            f"{table_prefix}collaboration_role_grants "
            "WHERE user_id=? AND role=?"
        )
        if borrowed:
            deleted = _management_statement_result(
                db, delete_grant_sql, (user_id, role))
        else:
            cursor = db.execute(delete_grant_sql, (user_id, role))
            deleted = cursor.rowcount
        if reconciler:
            # 撤销即清掉开关行：再次授权按「新授权默认关」重新落 0，不继承旧值。
            # 借用连接由协调器预先完成准备，本路径不再迁移/回填。
            if not borrowed:
                _migrate_member_settings(db)
            delete_settings_sql = (
                "DELETE FROM "
                f"{table_prefix}collaboration_member_settings "
                "WHERE user_id=?"
            )
            if borrowed:
                _management_statement_result(db, delete_settings_sql, (user_id,))
            else:
                db.execute(delete_settings_sql, (user_id,))
        return deleted == 1


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
