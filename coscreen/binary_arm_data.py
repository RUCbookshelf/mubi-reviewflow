"""Persist raw binary arm counts for sparse-data analyses."""

import sqlite3
from collections.abc import Mapping
from contextlib import closing
from pathlib import Path

from coscreen.db import _add_column_if_missing, _connect
from coscreen.review_analysis import _linked

DbPath = str | Path
_FIELDS = ("events_t", "total_t", "events_c", "total_c")
#: 只读连接的锁等待预算：WAL 下读者本不需要等写者，取值只为覆盖极短的
#: 检查点窗口；不沿用 `_connect` 的迁移路径（那条路径会 BEGIN IMMEDIATE）。
_READ_TIMEOUT_SECONDS = 5.0
_TABLE = """
CREATE TABLE IF NOT EXISTS review_binary_arms (
  study_id TEXT NOT NULL REFERENCES review_studies(id),
  comparison TEXT NOT NULL,
  outcome TEXT NOT NULL,
  timepoint TEXT NOT NULL,
  events_t INTEGER NOT NULL CHECK(typeof(events_t)='integer' AND events_t>=0),
  total_t INTEGER NOT NULL CHECK(typeof(total_t)='integer' AND total_t>0),
  events_c INTEGER NOT NULL CHECK(typeof(events_c)='integer' AND events_c>=0),
  total_c INTEGER NOT NULL CHECK(typeof(total_c)='integer' AND total_c>0),
  source_key TEXT NOT NULL REFERENCES articles(zotero_key),
  source_locator TEXT NOT NULL DEFAULT '',
  CHECK(events_t<=total_t),
  CHECK(events_c<=total_c),
  PRIMARY KEY(study_id,comparison,outcome,timepoint)
)
"""


def _table_present(conn) -> bool:
    """只读探测本模块自建表是否存在（sqlite_master 查询不取写锁）。"""
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='review_binary_arms'"
    ).fetchone() is not None


def _ensure_table(conn) -> None:
    conn.execute(_TABLE)
    _add_column_if_missing(conn, "review_binary_arms", "source_locator",
                           "ALTER TABLE review_binary_arms ADD COLUMN source_locator TEXT NOT NULL DEFAULT ''")


def _schema_missing(conn) -> bool:
    """只读判定写路径是否需要自愈建表/补列。

    写路径据此跳过 `_ensure_table`（该函数无条件 `BEGIN IMMEDIATE`，F2-64：
    每次保存都多抢一次写锁，长写事务并发时拖住别的请求）。读路径走
    `_read_connect` + `_table_present`，完全不碰 ALTER。
    """
    if not _table_present(conn):
        return True
    return "source_locator" not in {row[1] for row in conn.execute(
        "PRAGMA table_info(review_binary_arms)")}


def _read_connect(db: DbPath) -> sqlite3.Connection:
    """读路径专用连接：不走 `db._connect` 的自愈迁移序列。

    F2-64：`db._connect` 每次打开都做「schema cookie 比对 + 完整自愈」，
    缓存未命中（新连接/库被外部改动）时整条迁移序列（含 `_add_column_if_missing`
    的 BEGIN IMMEDIATE）会在长写事务期间把读端点阻塞到锁超时抛
    OperationalError → 500。读路径只需要一条 SELECT，故在此自建连接
    （模块内单域修，不动 db.py）。

    这里也**不改** journal_mode：WAL 是库文件的持久属性（建库时
    `db._connect` 已设置），读连接若尝试切换会在他人持写锁时反向阻塞，
    正是不该出现的副作用。
    """
    return sqlite3.connect(db, timeout=_READ_TIMEOUT_SECONDS)


def save_binary_arms(db: DbPath, study_id: str, comparison: str, outcome: str,
                     timepoint: str, source_key: str, arms: Mapping,
                     source_locator: str = "") -> None:
    if any(not isinstance(value, str) or not value.strip()
           for value in (study_id, comparison, outcome, timepoint, source_key)):
        raise ValueError("study, comparison, outcome, timepoint and source report are required")
    # F2-65：source_locator 是六个字符串参数里唯一此前无类型校验的（直连调用
    # 传 None/123/[...] 会以 AttributeError 穿出只捕 ValueError 的端点边界）；
    # 空串仍是合法默认值，故只查类型、不查非空。
    if not isinstance(source_locator, str):
        raise ValueError("source locator must be a string")
    if not isinstance(arms, Mapping) or set(arms) != set(_FIELDS):
        raise ValueError("arm inputs must contain events_t, total_t, events_c and total_c")
    counts = [arms[field] for field in _FIELDS]
    if any(type(value) is not int or value < 0 or value > 2**63 - 1 for value in counts):
        raise ValueError("binary arm counts must be nonnegative SQLite integers")
    events_t, total_t, events_c, total_c = counts
    if total_t == 0 or total_c == 0 or events_t > total_t or events_c > total_c:
        raise ValueError("arm totals must be positive and events cannot exceed totals")

    study_id, comparison, outcome, timepoint, source_key = (
        value.strip() for value in (study_id, comparison, outcome, timepoint, source_key))
    with closing(_connect(db)) as conn:
        if _schema_missing(conn):
            _ensure_table(conn)
        # F2-63：来源链接校验必须与 INSERT 同处一个写事务。此前 `_linked`
        # 在 autocommit 下先读、INSERT 时才隐式开写事务，两者之间的窗口内
        # 并发 save_study 删掉该链接即可落下一行孤儿 arms（此后该行无法再
        # 经 API 更新）；BEGIN IMMEDIATE 先取写锁再复查，删链接与写 arms
        # 严格串行：后到者要么看到孤儿行而拒绝（save_study 的占用守卫），
        # 要么看到链接已删而 400。
        try:
            conn.execute("BEGIN IMMEDIATE")
            _linked(conn, study_id, source_key)
            conn.execute(
                "INSERT INTO review_binary_arms "
                "(study_id,comparison,outcome,timepoint,events_t,total_t,events_c,total_c,source_key,source_locator) "
                "VALUES (?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(study_id,comparison,outcome,timepoint) DO UPDATE SET "
                "events_t=excluded.events_t,total_t=excluded.total_t,events_c=excluded.events_c,"
                "total_c=excluded.total_c,source_locator=CASE WHEN source_key=excluded.source_key "
                "THEN COALESCE(NULLIF(excluded.source_locator,''),source_locator) ELSE excluded.source_locator END,"
                "source_key=excluded.source_key",
                (study_id, comparison, outcome, timepoint, *counts, source_key, source_locator.strip()),
            )
            conn.commit()
        except BaseException:
            conn.rollback()
            raise


def list_binary_arms(db: DbPath) -> list[dict]:
    """Return saved arm counts.

    纯读路径（F2-64）：建表/补列的自愈只在写路径做。表尚未建立时没有任何
    行可读，直接返回空表；旧库缺 source_locator 列时按空定位符读取，不为此
    触发 ALTER。因此本函数不取写锁，长写事务并发期间照常返回。
    """
    if not Path(db).exists():
        # 读路径不得有副作用：库文件不存在时连空文件都不建（此前经
        # db._connect 会顺手建库并跑迁移序列）。
        return []
    with closing(_read_connect(db)) as conn:
        if not _table_present(conn):
            return []
        has_locator = "source_locator" in {row[1] for row in conn.execute(
            "PRAGMA table_info(review_binary_arms)")}
        locator = "source_locator" if has_locator else "''"
        rows = conn.execute(
            "SELECT study_id,comparison,outcome,timepoint,source_key," + locator + ",events_t,total_t,events_c,total_c "
            "FROM review_binary_arms ORDER BY study_id,comparison,outcome,timepoint"
        ).fetchall()
    return [{"study_id": row[0], "comparison": row[1], "outcome": row[2], "timepoint": row[3],
             "source_key": row[4], "source_locator": row[5],
             "arms": dict(zip(_FIELDS, row[6:]))} for row in rows]
