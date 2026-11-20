"""Additive workflow storage. Call migrate only after verified task authorization.

Article source links deliberately have no article foreign key: imports may keep
provenance while staging. Associate active canonical keys only at activation.
This module never edits decisions, field IDs, or researched article metadata.
"""
from __future__ import annotations

import json
import os
import random
import sqlite3
import threading
import unicodedata
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

UNASSIGNED = "00000000-0000-0000-0000-000000000000"
STAGES = ("screen", "fulltext", "coding")
SCHEMA_VERSION = 1
_SCHEMA = """
CREATE TABLE IF NOT EXISTS workflow_migrations(version INTEGER PRIMARY KEY,applied_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS workflow_state(singleton INTEGER PRIMARY KEY CHECK(singleton=1),
 version INTEGER NOT NULL DEFAULT 1,team_latched INTEGER NOT NULL DEFAULT 0,
 catalog_revision INTEGER NOT NULL DEFAULT 1,stages_json TEXT NOT NULL,evidence_json TEXT NOT NULL DEFAULT '[]');
CREATE TABLE IF NOT EXISTS workflow_sources(id TEXT PRIMARY KEY,name TEXT NOT NULL,normalized_name TEXT NOT NULL UNIQUE,
 active INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS workflow_article_sources(zotero_key TEXT NOT NULL,source_id TEXT NOT NULL REFERENCES workflow_sources(id),
 PRIMARY KEY(zotero_key,source_id));
CREATE INDEX IF NOT EXISTS workflow_source_keys ON workflow_article_sources(source_id,zotero_key);
CREATE TABLE IF NOT EXISTS workflow_reasons(id TEXT PRIMARY KEY,stage TEXT NOT NULL CHECK(stage IN ('screen','fulltext')),
 text TEXT NOT NULL,normalized_text TEXT NOT NULL,UNIQUE(stage,normalized_text));
CREATE TABLE IF NOT EXISTS workflow_random_order(actor_id TEXT NOT NULL,task_id TEXT NOT NULL,scope TEXT NOT NULL,
 zotero_key TEXT NOT NULL,position INTEGER NOT NULL,PRIMARY KEY(actor_id,task_id,scope,zotero_key));
CREATE TABLE IF NOT EXISTS workflow_skip_order(actor_id TEXT NOT NULL,task_id TEXT NOT NULL,stage TEXT NOT NULL CHECK(stage IN ('screen','fulltext')),
 zotero_key TEXT NOT NULL,skip_seq INTEGER NOT NULL,PRIMARY KEY(actor_id,task_id,stage,zotero_key));
CREATE TABLE IF NOT EXISTS workflow_catalog_sync(owner_identity TEXT PRIMARY KEY,revision INTEGER NOT NULL);
CREATE TRIGGER IF NOT EXISTS workflow_catalog_insert AFTER INSERT ON articles BEGIN
 UPDATE workflow_state SET catalog_revision=catalog_revision+1 WHERE singleton=1;
 INSERT OR IGNORE INTO workflow_article_sources(zotero_key,source_id)
 VALUES(NEW.zotero_key,'00000000-0000-0000-0000-000000000000'); END;
CREATE TRIGGER IF NOT EXISTS workflow_catalog_update AFTER UPDATE ON articles BEGIN
 UPDATE workflow_state SET catalog_revision=catalog_revision+1 WHERE singleton=1; END;
CREATE TRIGGER IF NOT EXISTS workflow_catalog_delete AFTER DELETE ON articles BEGIN
 UPDATE workflow_state SET catalog_revision=catalog_revision+1 WHERE singleton=1; END;
"""


def defaults() -> dict:
    # 2.1.5 W1：show_maybe 已退役（两阶段全程二选一）；新库不再写该键。
    return {stage: {"mode": "all", "source_ids": []} for stage in STAGES}


def normalized(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).split()).casefold()


def connect(db: str | Path, *, read_only: bool = False) -> sqlite3.Connection:
    path = Path(db).resolve()
    conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=15) if read_only else sqlite3.connect(path, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=15000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def has_table(conn: sqlite3.Connection, table: str) -> bool:
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone())


_FRESH_LOCK = threading.Lock()
_FRESH_DBS: dict[str, int] = {}


def note_freshly_created(db: str | Path) -> None:
    """登记「本进程新建的库」的文件身份（st_ino 文件索引）。

    首建迁移链中的库不含任何既有数据：升级前备份与逐事务 fsync 对它没有
    保护对象。登记按文件身份而非路径字符串生效——文件被删除替换后 st_ino
    变化，登记自动失效，不会误伤后来放到同一路径的旧库。
    """
    try:
        identity = os.stat(db).st_ino
    except OSError:
        return
    with _FRESH_LOCK:
        _FRESH_DBS[str(Path(db).resolve())] = identity


def migration_state(db: str | Path) -> set[int] | None:
    """一次只读连接读出 workflow_migrations 已落地的版本集合。

    供 context() 热路径合并版本核查（门禁墙钟：每个 API 请求原先对同一
    库开 5 个只读连接逐个重查 v1/v4/v5/v3/v6——各 migrate 在版本齐全时
    本就是无操作，此处一次判齐后跳过，首建/旧库升级行为不变）。库文件
    不存在或账本表未建时返回 None（调用方走完整迁移链）。
    """
    path = Path(db)
    if not path.is_file():
        return None
    with closing(connect(path, read_only=True)) as conn:
        if not has_table(conn, "workflow_migrations"):
            return None
        return {int(row[0]) for row in conn.execute("SELECT version FROM workflow_migrations")}


def is_fresh_database(db: str | Path) -> bool:
    """该库文件是否由本进程新建且从未被整体替换（可安全跳过升级前备份）。"""
    try:
        identity = os.stat(db).st_ino
    except OSError:
        return False
    with _FRESH_LOCK:
        return _FRESH_DBS.get(str(Path(db).resolve())) == identity


def connect_for_initial_migration(db: str | Path) -> sqlite3.Connection:
    """首建迁移链的写连接：对「本进程新建」的库临时放宽 synchronous。

    同一进程内 SCHEMA_VERSION 恒定，版本行缺失只可能是首建链尚未走完，此时
    库内不存在本进程之外写入的数据：断电最坏回到未建库状态，下次打开由幂等
    迁移链原样重建。真实旧库（非进程新建）仍走默认 FULL，升级行为不变。
    """
    conn = connect(db)
    if is_fresh_database(db):
        conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def migrate(db: str | Path) -> None:
    """Upgrade once, with SQLite's consistent backup before first existing DB upgrade."""
    path = Path(db)
    # 本调用即文件创建者：库内不可能存在既有数据，无需升级前备份。
    creating = not path.is_file()
    if not creating:
        with closing(connect(path, read_only=True)) as conn:
            if has_table(conn, "workflow_migrations") and conn.execute(
                    "SELECT 1 FROM workflow_migrations WHERE version=?", (SCHEMA_VERSION,)).fetchone():
                return
            # 备份只保护既有库的首次升级；本进程新建、尚在首建链中的库没有
            # 可备份的数据，跳过空库备份拷贝（真实旧库不是进程新建，照常备份）。
            if not is_fresh_database(path):
                backup = path.with_name(path.name + ".pre-workflow-v1.bak")
                if not backup.exists():
                    with closing(sqlite3.connect(backup)) as target:
                        conn.backup(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(path)
    if creating or is_fresh_database(path):
        # 首建链写事务免逐事务 fsync（依据见 connect_for_initial_migration 注释）。
        conn.execute("PRAGMA synchronous=NORMAL")
    with closing(conn), conn:
        # Account reason stores have no catalogue; task catalogues already exist.
        # 2.1.5 waveA 白名单（scan_dynamic_sql.whitelist）：脚本由模块内固定
        # _SCHEMA 常量与会话前缀拼接；executescript 不支持参数绑定，脚本内容
        # 不含任何外部输入。语句文本保留原样。
        schema = _SCHEMA if has_table(conn, "articles") else _SCHEMA.split("CREATE TRIGGER")[0]
        conn.executescript("BEGIN IMMEDIATE;\n" + schema)
        conn.execute("INSERT OR IGNORE INTO workflow_state(singleton,stages_json) VALUES(1,?)", (json.dumps(defaults()),))
        conn.execute("INSERT OR IGNORE INTO workflow_sources(id,name,normalized_name,created_at) VALUES(?,?,?,?)",
                     (UNASSIGNED, "Unassigned", normalized("Unassigned"), datetime.now(timezone.utc).isoformat()))
        if has_table(conn, "articles"):
            conn.execute("INSERT OR IGNORE INTO workflow_article_sources SELECT zotero_key,? FROM articles a "
                         "WHERE NOT EXISTS(SELECT 1 FROM workflow_article_sources s WHERE s.zotero_key=a.zotero_key)", (UNASSIGNED,))
        conn.execute("INSERT OR IGNORE INTO workflow_migrations VALUES(?,?)", (SCHEMA_VERSION, datetime.now(timezone.utc).isoformat()))
    if creating:
        # 只有「本调用创建了该文件」才登记；升级既有旧库时不能把旧库标成新建，
        # 否则下游 v4/v5/v3/v6 会误跳过它们对旧库的保护性备份。
        note_freshly_created(path)


def _default_settings() -> dict:
    return {"version": 1, "stages": defaults(), "evidence_required_field_ids": [],
            "team_latched": False, "catalog_revision": 0}


def _default_sources() -> list[dict]:
    return [{"id": UNASSIGNED, "name": "Unassigned", "active": True, "reserved": True}]


def _require_borrowed_connection(conn: sqlite3.Connection, *, api: str) -> None:
    """Borrowed-read contract: native connection, active caller txn, caller Row.

    The guard never opens/closes a connection, starts/ends a transaction, runs
    PRAGMA/DDL, or changes row_factory. Violations are programming errors.
    """
    if not isinstance(conn, sqlite3.Connection):
        raise RuntimeError(
            f"{api} requires an existing sqlite3.Connection (borrowed; never opened here).")
    if not conn.in_transaction:
        raise RuntimeError(
            f"{api} requires an active caller transaction; this function never starts one.")
    if conn.row_factory is not sqlite3.Row:
        raise RuntimeError(
            f"{api} requires the caller connection to use sqlite3.Row; "
            "this function never changes row_factory.")


def _settings_on_conn(conn: sqlite3.Connection) -> dict:
    result = _default_settings()
    if has_table(conn, "workflow_state"):
        row = conn.execute("SELECT * FROM workflow_state WHERE singleton=1").fetchone()
        if row:
            result.update(version=row["version"], stages=json.loads(row["stages_json"]),
                          evidence_required_field_ids=json.loads(row["evidence_json"]),
                          team_latched=bool(row["team_latched"]),
                          catalog_revision=row["catalog_revision"])
    return result


def _sources_on_conn(conn: sqlite3.Connection) -> list[dict]:
    if has_table(conn, "workflow_sources"):
        from coscreen.import_sources import decorate
        rows = [{**dict(row), "active": bool(row["active"]),
                 "reserved": row["id"] == UNASSIGNED}
                for row in conn.execute(
                    "SELECT id,name,active FROM workflow_sources ORDER BY created_at,id")]
        return decorate(conn, rows)
    return _default_sources()


def _source_links_on_conn(conn: sqlite3.Connection) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    if has_table(conn, "workflow_article_sources"):
        for row in conn.execute("SELECT zotero_key,source_id FROM workflow_article_sources"):
            result.setdefault(row[0], set()).add(row[1])
    return result


def settings(db: str | Path, *, conn: sqlite3.Connection | None = None) -> dict:
    if conn is not None:
        _require_borrowed_connection(conn, api="settings")
        return _settings_on_conn(conn)
    if not Path(db).is_file():
        return _default_settings()
    with closing(connect(db, read_only=True)) as path_conn:
        return _settings_on_conn(path_conn)


def sources(db: str | Path, *, conn: sqlite3.Connection | None = None) -> list[dict]:
    if conn is not None:
        _require_borrowed_connection(conn, api="sources")
        return _sources_on_conn(conn)
    if Path(db).is_file():
        with closing(connect(db, read_only=True)) as path_conn:
            return _sources_on_conn(path_conn)
    return _default_sources()


def source_links(db: str | Path, *, conn: sqlite3.Connection | None = None) -> dict[str, set[str]]:
    if conn is not None:
        _require_borrowed_connection(conn, api="source_links")
        return _source_links_on_conn(conn)
    if Path(db).is_file():
        with closing(connect(db, read_only=True)) as path_conn:
            return _source_links_on_conn(path_conn)
    return {}


def associate_sources(db: str | Path, keys: Iterable[str], source_ids: Iterable[str], *,
                      conn: sqlite3.Connection | None = None) -> None:
    """Append overlapping source links; caller uses write_guard and activation transaction.

    Explicit provenance replaces only the placeholder Unassigned link. Keys are
    checked against active canonical articles, never inserted into articles here.
    Pass conn to make source association atomic with staged batch activation.
    """
    if conn is None:
        migrate(db)
        with closing(connect(db)) as owned, owned:
            associate_sources(db, keys, source_ids, conn=owned)
        return
    ids = set(source_ids)
    valid = {row[0] for row in conn.execute("SELECT id FROM workflow_sources")}
    if not ids or not ids <= valid:
        raise ValueError("Select valid source IDs.")
    for key in set(keys):
        row = conn.execute("SELECT is_duplicate_of FROM articles WHERE zotero_key=?", (key,)).fetchone()
        if row is None or row[0] is not None:
            raise ValueError("Sources can be linked only to active canonical articles.")
        if ids != {UNASSIGNED}:
            conn.execute("DELETE FROM workflow_article_sources WHERE zotero_key=? AND source_id=?", (key, UNASSIGNED))
        conn.executemany("INSERT OR IGNORE INTO workflow_article_sources VALUES(?,?)", [(key, sid) for sid in ids])


def bump_catalog_revision(db: str | Path, *, conn: sqlite3.Connection | None = None) -> None:
    if conn is not None:
        conn.execute("UPDATE workflow_state SET catalog_revision=catalog_revision+1 WHERE singleton=1")
    else:
        with closing(connect(db)) as owned, owned:
            bump_catalog_revision(db, conn=owned)


def catalog_revision(db: str | Path, *, conn: sqlite3.Connection | None = None) -> int:
    return int(settings(db, conn=conn)["catalog_revision"])


def random_order(db: str | Path, actor_id: str, task_id: str, scope: str, keys: list[str],
                 pending: set[str], *, shuffle: bool = False) -> list[str]:
    """Keep persisted positions; append new keys, shuffle only pending positions."""
    migrate(db)
    with closing(connect(db)) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute("SELECT zotero_key,position FROM workflow_random_order WHERE actor_id=? AND task_id=? AND scope=? ORDER BY position",
                            (actor_id, task_id, scope)).fetchall()
        positions = {row[0]: row[1] for row in rows}
        previous = dict(positions)
        additions = [key for key in keys if key not in positions]
        if not rows:
            random.SystemRandom().shuffle(additions)
        offset = max(positions.values(), default=-1) + 1
        positions.update({key: offset + i for i, key in enumerate(additions)})
        if shuffle:
            remaining = [key for key in keys if key in pending]
            slots = sorted(positions[key] for key in remaining)
            random.SystemRandom().shuffle(remaining)
            positions.update(dict(zip(remaining, slots)))
        conn.executemany("INSERT INTO workflow_random_order VALUES(?,?,?,?,?) ON CONFLICT(actor_id,task_id,scope,zotero_key) DO UPDATE SET position=excluded.position",
                         [(actor_id, task_id, scope, key, position) for key, position in positions.items()
                          if previous.get(key) != position])
    return sorted(keys, key=positions.__getitem__)


_SKIP_ORDER_DDL = ("CREATE TABLE IF NOT EXISTS workflow_skip_order(actor_id TEXT NOT NULL,task_id TEXT NOT NULL,"
                   "stage TEXT NOT NULL CHECK(stage IN ('screen','fulltext')),zotero_key TEXT NOT NULL,"
                   "skip_seq INTEGER NOT NULL,PRIMARY KEY(actor_id,task_id,stage,zotero_key));")


class SkipDecisionExistsError(Exception):
    """skip_queue_item 的类型化拒绝：该文献在该阶段已有决策记录。

    跳过只对未决策（pending）文献有意义；已决策文献的重看/改判走决策保存
    与撤销（workspace_undo reopen）路径。API 层捕获后映射为结构化 409
    （带 pending 与 skipped_pending 计数，供前端完成门引导）。
    """

    error_code = "skip_decision_exists"

    def __init__(self, stage: str, zotero_key: str) -> None:
        self.stage, self.zotero_key = stage, zotero_key
        super().__init__(
            f"Article {zotero_key} already has a {stage} decision; "
            "skip applies only to pending articles.")


def skip_queue_item(db: str | Path, actor_id: str, task_id: str, stage: str,
                    zotero_key: str) -> int:
    """「暂时跳过」留痕（2026-11-18 裁定 #2/#3/#7/#8）。

    只写 ``workflow_skip_order`` 覆盖层，**不写任何决策表**——跳过不是决
    策，文献保持待筛选；ML 训练数据源=决策表，跳过表无算法消费方。与
    :func:`random_order` 同事务/连接模式（migrate → 单写连接 + BEGIN
    IMMEDIATE）。存量 v1 库 migrate 对已记版本早返回、不重跑 ``_SCHEMA``，
    故事务内先幂等自愈建表（CREATE IF NOT EXISTS；新库/已建库为无操作）。

    - ``stage`` 仅接受 screen/fulltext（coding 不参与跳过）。
    - 该文献已有该阶段决策 → :class:`SkipDecisionExistsError`。
    - ``skip_seq`` = 该 (actor,task,stage) 当前 max+1；重复跳过=主键四列
      UPSERT 更新序号（越跳越沉底，裁定 #3）。撤销决策回待筛后旧记录自
      动重新生效（读取端按表合成，无需清理代码，裁定 #8）。
    """
    if stage not in ("screen", "fulltext"):
        raise ValueError("Skip supports only screen and fulltext stages.")
    migrate(db)
    with closing(connect(db)) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(_SKIP_ORDER_DDL)
        if has_table(conn, "decisions" if stage == "screen" else "stage2_decisions"):
            # 表名只来自上方固定枚举（screen/fulltext）：两条字面量 SQL，不做
            # 任何字符串插值（与 _saved_rows_on_conn 同口径）。
            if stage == "screen":
                decided = conn.execute(
                    "SELECT 1 FROM decisions WHERE zotero_key=?", (zotero_key,)).fetchone()
            else:
                decided = conn.execute(
                    "SELECT 1 FROM stage2_decisions WHERE zotero_key=?", (zotero_key,)).fetchone()
            if decided is not None:
                raise SkipDecisionExistsError(stage, zotero_key)
        row = conn.execute(
            "SELECT MAX(skip_seq) FROM workflow_skip_order WHERE actor_id=? AND task_id=? AND stage=?",
            (actor_id, task_id, stage)).fetchone()
        skip_seq = int(row[0] or 0) + 1
        conn.execute("INSERT INTO workflow_skip_order VALUES(?,?,?,?,?) "
                     "ON CONFLICT(actor_id,task_id,stage,zotero_key) DO UPDATE SET skip_seq=excluded.skip_seq",
                     (actor_id, task_id, stage, zotero_key, skip_seq))
    return skip_seq
