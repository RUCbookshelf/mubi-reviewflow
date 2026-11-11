# -*- coding: utf-8 -*-
"""找回密码重置码库（2.1.5 记住我+找回密码；authb 2026-11-11 重写为 SQLite）。

存储：``{DATA_DIR}/auth_reset_codes.db``（SQLite，WAL+busy_timeout）——首版
JSON+进程内锁被复审证实跨进程竞态（CLI 签发进程与 API 消费进程双写者，
后写旧快照抹回 ``used_at``=已消费码复活、并发签发静默丢码，端到端账号接
管实证）；SQLite 的跨进程事务一次性消灭竞态/复活/丢码/形状损坏四类问题。

口径：sha256 落盘（明文码只在签发瞬间出现一次）、15 分钟 TTL、每用户名
5 枚活码、消费=原子 ``UPDATE ... WHERE used_at IS NULL AND expires_at>?``
（rowcount=1 即唯一赢家）、损坏库 fail-closed（连接/查询异常一律 False）、
成功重置后 ``revoke_all_codes`` 回收兄弟码（复审 M4：用户以为止损而能力
未收回）。用户名两侧统一 strip 规范化（复审：空白键错位曾让合法请求 401
烧限流桶、空白变体堆 30 枚活码）。
"""
from __future__ import annotations

import contextlib

import hashlib
import hmac
import sqlite3
import threading
import time

from custom_backend import auth

#: 重置码有效期：15 分钟（管理员/本机转交窗口足够，短窗压泄露面）。
RESET_CODE_TTL_SECONDS = 15 * 60
#: 每用户名同时活跃（未过期未消费）上限。
RESET_CODES_PER_USER = 5

#: 进程内串行（跨进程由 SQLite 事务保证；此锁只减少本进程连接争用）。
_LOCK = threading.Lock()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS reset_codes (
  sha256 TEXT PRIMARY KEY,
  username TEXT NOT NULL,
  created_at REAL NOT NULL,
  expires_at REAL NOT NULL,
  used_at REAL
);
CREATE INDEX IF NOT EXISTS idx_reset_codes_username
  ON reset_codes(username, used_at, expires_at);
"""


def _db_path():
    return auth.data_dir() / "auth_reset_codes.db"


def _connect() -> sqlite3.Connection:
    """WAL+busy_timeout 连接；建表幂等（进程首连一次后即纯快路径）。"""
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10.0)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=10000")
        conn.executescript(_SCHEMA)
    except BaseException:
        # 坏库上 executescript 抛出时连接必须关闭——泄漏句柄会让外部
        # unlink/清理报 WinError 32（Windows 不许删打开中的文件）。
        conn.close()
        raise
    return conn


def _digest(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def _norm(username: str) -> str:
    """用户名规范化（strip）：签发/消费/回收同键，杜绝空白变体错位与堆码。"""
    return str(username or "").strip()


def _prune_expired(conn: sqlite3.Connection, now: float) -> None:
    """写路径顺带清扫过期/已用记录（替代首版 _MAX_RECORDS 静默截断——那会
    丢他人仍在有效期的活码）。"""
    conn.execute(
        "DELETE FROM reset_codes WHERE expires_at <= ? OR used_at IS NOT NULL",
        (now,))


def issue_reset_code(username: str, *, now: float | None = None) -> str | None:
    """为**已确认存在**的用户名签发一枚重置码；返回明文码（仅此一次出现）。

    调用方（CLI 通道）须先自行确认用户存在（防未认证枚举）。事务内：清过期
    → 活码超上限先撤最旧 → 插入。任何存储异常返回 None（不抛、不确认）。
    """
    ts = time.time() if now is None else now
    name = _norm(username)
    code = "rf-" + secrets_token()
    try:
        with _LOCK, contextlib.closing(_connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            _prune_expired(conn, ts)
            active = conn.execute(
                "SELECT sha256, created_at FROM reset_codes"
                " WHERE username=? AND used_at IS NULL AND expires_at>?"
                " ORDER BY created_at", (name, ts)).fetchall()
            while len(active) >= RESET_CODES_PER_USER:
                conn.execute("DELETE FROM reset_codes WHERE sha256=?",
                             (active.pop(0)[0],))
            conn.execute(
                "INSERT INTO reset_codes VALUES (?,?,?,?,NULL)",
                (_digest(code), name, ts, ts + RESET_CODE_TTL_SECONDS))
            conn.commit()
    except sqlite3.Error:
        return None
    return code


def consume_reset_code(username: str, code: str, *,
                       now: float | None = None) -> bool:
    """消费一枚重置码（一次性、跨进程原子）：命中且未用未过期 → 置 used。

    rowcount=1 即本请求为唯一赢家（并发同码重放恒败）；任何存储异常
    fail-closed 返回 False（绝不抛——曾形状损坏冒 500 且构成存在性判别）。
    """
    ts = time.time() if now is None else now
    if not code:
        return False
    try:
        with _LOCK, contextlib.closing(_connect()) as conn:
            cur = conn.execute(
                "UPDATE reset_codes SET used_at=?"
                " WHERE sha256=? AND username=? AND used_at IS NULL"
                " AND expires_at>?", (ts, _digest(code), _norm(username), ts))
            conn.commit()  # closing() 只关不提交——未提交 DML 会被 close 回滚
            return cur.rowcount == 1
    except sqlite3.Error:
        return False


def rearm_reset_code(code: str) -> None:
    """消费后置失败补偿（复审 M6）：set_password 异常时把码还回去。

    码未过期才有效（过期侧退款无意义）；任何异常静默（调用方已在异常路径）。
    """
    try:
        with _LOCK, contextlib.closing(_connect()) as conn:
            conn.execute(
                "UPDATE reset_codes SET used_at=NULL"
                " WHERE sha256=? AND used_at IS NOT NULL AND expires_at>?",
                (_digest(code), time.time()))
            conn.commit()
    except sqlite3.Error:
        pass


def revoke_all_codes(username: str) -> None:
    """回收该用户名全部活码（成功重置后调用，复审 M4：兄弟码 15 分钟内
    仍可再改密=「以为止损而能力未收回」）。"""
    try:
        with _LOCK, contextlib.closing(_connect()) as conn:
            conn.execute(
                "DELETE FROM reset_codes WHERE username=? AND used_at IS NULL",
                (_norm(username),))
            conn.commit()
    except sqlite3.Error:
        pass


def dummy_verify(code: str) -> None:
    """不存在用户名的等时垫（复审：旧版纯哈希垫比真实路径便宜 22-85×）。

    与真实消费同构：一次库读 + 对同形状记录集的 compare_digest 扫描
    （dummy 行恒不匹配）；绝不抛错、结果不使用。
    """
    if not code:
        return
    target = _digest(code)
    try:
        with _LOCK, contextlib.closing(_connect()) as conn:
            rows = conn.execute(
                "SELECT sha256 FROM reset_codes LIMIT ?", (5,)).fetchall()
    except sqlite3.Error:
        rows = []
    for (stored,) in rows:
        hmac.compare_digest(str(stored), target)


def secrets_token() -> str:
    import secrets
    return secrets.token_urlsafe(24)
