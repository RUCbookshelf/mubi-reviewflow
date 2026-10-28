"""用户管理与 JWT 鉴权（custom_backend 专用，不改动 coscreen / ui）。

- 用户凭据存 ``{data_dir}/users.db``（SQLite，bcrypt 哈希，绝不存明文）；
- JWT：HS256 + 24h 过期，载荷含 sub(user_id) / username / iat / exp / jti；
- 签名密钥：优先环境变量 ``COBOOKSHELF_JWT_SECRET``，否则持久化到
  ``{data_dir}/.jwt_secret``（0600 权限创建），重启后令牌不失效；
- 密码策略：8~128 个字符，且 UTF-8 编码后 ≤72 字节（bcrypt 底层上限，
  一个汉字占 3 字节）；用户名：3~64 字符，白名单字符（字母数字 . _ - @ 与 CJK）。
"""

from __future__ import annotations

import logging
import os
import re
import secrets
import sqlite3
import threading
import uuid
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

import bcrypt
import jwt
from coscreen.security import sanitize_component
from coscreen.task_collaboration import DB_FILENAME as _COLLABORATION_DB_FILENAME

logger = logging.getLogger(__name__)

__all__ = [
    "AuthError",
    "TOKEN_TTL_HOURS",
    "create_access_token",
    "create_user",
    "decode_access_token",
    "get_user",
    "get_user_by_id",
    "reserved_screener_collision",
    "verify_password",
]

TOKEN_TTL_HOURS = 24
JWT_ALGORITHM = "HS256"
JWT_ISSUER = "cobookshelf"

#: 用户名白名单：字母数字、. _ - @ 与 CJK 汉字
_USERNAME_RE = re.compile(r"^[0-9A-Za-z._@\-\u4e00-\u9fff]{3,64}$")
_MIN_PASSWORD_LEN = 8
_MAX_PASSWORD_LEN = 128
#: G-01：bcrypt 底层只接受 ≤72 字节的口令，超限 ``hashpw``/``checkpw`` 直接
#: 抛 ``ValueError``。字节数按 UTF-8 编码计（一个汉字占 3 字节），据此在
#: ``validate_password`` 显式设限，避免裸 ``ValueError`` 穿透注册端点的
#: ``except AuthError``（曾 → 500 且限流占位不退）。
_MAX_PASSWORD_BYTES = 72
_PASSWORD_TOO_LONG_MSG = (
    "密码过长：UTF-8 编码后不得超过 72 字节（一个汉字占 3 字节），请缩短密码。"
)
_BCRYPT_ROUNDS = 12

_USERS_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  created_at TEXT NOT NULL
);
"""


class AuthError(Exception):
    """鉴权相关的业务错误（消息可直接展示给用户）。"""


def data_dir() -> Path:
    """应用数据目录（默认项目根 data/，可用环境变量覆盖，供测试隔离）。"""
    return Path(os.environ.get("COBOOKSHELF_DATA_DIR", "data"))


def _users_db() -> Path:
    return data_dir() / "users.db"


def _connect() -> sqlite3.Connection:
    path = _users_db()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_USERS_SCHEMA)
    return conn


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# 密码哈希（bcrypt，直接使用 bcrypt 库 —— passlib 1.7.4 与 bcrypt>=4 不兼容）
# ---------------------------------------------------------------------------

def hash_password(password: str) -> str:
    """bcrypt 哈希（rounds=12）；返回 ASCII 可存储的哈希串。

    G-01 兜底：>72 字节的口令 ``bcrypt.hashpw`` 抛 ``ValueError``——收敛为
    ``AuthError``，保证任何调用路径都走统一的 400+限流退款语义而非裸 500。
    """
    try:
        digest = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=_BCRYPT_ROUNDS))
    except ValueError as exc:
        raise AuthError(_PASSWORD_TOO_LONG_MSG) from exc
    return digest.decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    """恒定时间校验 bcrypt 哈希；哈希损坏按不匹配处理（绝不抛异常）。"""
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("ascii"))
    except (ValueError, TypeError):
        return False


# ---------------------------------------------------------------------------
# 用户 CRUD
# ---------------------------------------------------------------------------

#: 任务目录内保留的筛选员库文件名（大小写不敏感比对，Windows 文件系统
#: 不区分大小写）。用户名 slug 化后与之相撞的账号，其个人库会覆盖任务
#: 协作权限库 collaboration.db，注册时直接拒绝（U4-P2）。
_RESERVED_SCREENER_DB_NAMES = frozenset({_COLLABORATION_DB_FILENAME.casefold()})


def reserved_screener_collision(username: str) -> bool:
    """用户名 slug 化后是否与任务目录保留库名（collaboration.db）冲突。"""
    from coscreen import tasks as _tasks

    slug = _tasks.slugify_screener(username or "")
    return f"{slug}.db".casefold() in _RESERVED_SCREENER_DB_NAMES


def validate_username(username: str) -> str:
    clean = (username or "").strip()
    if not _USERNAME_RE.match(clean):
        raise AuthError(
            "用户名须为 3~64 个字符，且只含字母、数字、汉字与 . _ - @。"
        )
    return clean


def validate_password(password: str) -> str:
    if not isinstance(password, str) or not (_MIN_PASSWORD_LEN <= len(password) <= _MAX_PASSWORD_LEN):
        raise AuthError(f"密码长度须为 {_MIN_PASSWORD_LEN}~{_MAX_PASSWORD_LEN} 个字符。")
    if len(password.encode("utf-8")) > _MAX_PASSWORD_BYTES:
        # G-01：25 个汉字（75 字节）这类「字符数合法、字节数超限」的口令曾
        # 在 hash_password 抛裸 ValueError → 注册 500 且限流占位不退。
        raise AuthError(_PASSWORD_TOO_LONG_MSG)
    return password


def create_user(username: str, password: str) -> dict:
    """注册新用户；重名抛 AuthError。返回 ``{id, username}``。"""
    clean = validate_username(username)
    if reserved_screener_collision(clean):
        # U4-P2：slug 撞上任务目录保留文件名 collaboration.db 的用户名
        # 会把个人筛选库写进任务协作权限库，注册即拒绝。
        raise AuthError("该用户名与任务协作数据库保留文件名冲突，请换一个用户名。")
    validate_password(password)
    password_hash = hash_password(password)
    with closing(_connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        key = sanitize_component(clean, "_").casefold()
        if any(sanitize_component(row[0], "_").casefold() == key
               for row in conn.execute("SELECT username FROM users")):
            raise AuthError("用户名与已有账号的筛选员数据路径冲突，请换一个用户名。")
        try:
            cur = conn.execute(
                "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
                (clean, password_hash, _now_iso()),
            )
        except sqlite3.IntegrityError as exc:
            raise AuthError("用户名已被注册。") from exc
        return {"id": int(cur.lastrowid), "username": clean}


def screener_storage_conflict(username: str) -> bool:
    """旧账号若共用过筛选员文件名，则暂停访问，避免跨账号读取。"""
    key = sanitize_component(username, "_").casefold()
    with closing(_connect()) as conn:
        return sum(sanitize_component(row[0], "_").casefold() == key
                   for row in conn.execute("SELECT username FROM users")) > 1


def get_user(username: str) -> dict | None:
    """按用户名取用户行；不存在返回 None。"""
    with closing(_connect()) as conn:
        row = conn.execute(
            "SELECT id, username, password_hash FROM users WHERE username = ?",
            ((username or "").strip(),),
        ).fetchone()
    return {"id": int(row[0]), "username": str(row[1]), "password_hash": str(row[2])} if row else None


def get_user_by_id(user_id: int) -> dict | None:
    """Resolve a collaboration invite target from the server-side account store."""
    if type(user_id) is not int or user_id <= 0:
        return None
    with closing(_connect()) as conn:
        row = conn.execute(
            "SELECT id, username FROM users WHERE id = ?", (user_id,),
        ).fetchone()
    return {"id": int(row[0]), "username": str(row[1])} if row else None


def authenticate(username: str, password: str) -> dict | None:
    """校验用户名密码；成功返回 ``{id, username}``，失败返回 None。

    用户不存在时也对恒定哈希执行一次校验，抹平“用户不存在”与“密码错误”
    的响应时间差（防用户名枚举的时序侧信道）。
    """
    user = get_user(username)
    target = user["password_hash"] if user else hash_password("timing-equalizer")
    ok = verify_password(password or "", target)
    if user and ok:
        return {"id": user["id"], "username": user["username"]}
    return None


# ---------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------

#: MM-03/r7 复审：.jwt_secret 写回失败（只读介质/权限）时的进程内应急密钥。
#: ``_secret()`` 无进程缓存、每次读盘——若无此应急值，写失败会退化为
#: 「每次调用重生成内存钥」：create_access_token 成功而 decode_access_token
#: 恒 None（同一进程内静默 401 自锁）。应急钥使进程内签发/校验自洽；数据
#: 目录恢复可写后，下一次调用即写回自愈（届时换钥，已发令牌需重新登录）。
_EMERGENCY_SECRET: str | None = None
_EMERGENCY_WRITE_LOGGED = False
_secret_lock = threading.Lock()


def _atomic_write_secret(path: Path, secret: str) -> bool:
    """同目录临时文件 + os.replace 原子写回密钥（写后 fsync，0600）。

    覆盖目标已存在（含空文件）的场景；掉电/崩溃不会留下半截或空密钥文件。
    返回是否写回成功——失败由调用方走进程内应急口径并负责诊断日志。
    """
    tmp = path.with_name(f".jwt_secret.{os.getpid()}.tmp")
    try:
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(secret)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(str(tmp), str(path))
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass
        return False
    return True


def _emit_secret_via_emergency(secret: str) -> str:
    """写回失败时的进程内应急口径：稳定同钥 ＋ 一次性 ERROR 诊断日志。"""
    global _EMERGENCY_SECRET, _EMERGENCY_WRITE_LOGGED
    if not _EMERGENCY_WRITE_LOGGED:
        logger.error(
            "auth: failed to persist .jwt_secret (read-only data dir or "
            "permission error); using an in-process secret — login/auth keeps "
            "working inside this process, but tokens will not survive a "
            "restart until the data dir becomes writable")
        _EMERGENCY_WRITE_LOGGED = True
    if _EMERGENCY_SECRET is None:
        _EMERGENCY_SECRET = secret
    return _EMERGENCY_SECRET


def _secret() -> str:
    """签名密钥：环境变量优先，否则 ``{data_dir}/.jwt_secret``（0600 新建）。

    MM-03：空密钥文件（O_EXCL 创建与首写之间崩溃/掉电、备份截断）曾原样
    返回 ``""``——PyJWT 对空 HMAC 钥抛 ``InvalidKeyError``（**非**
    ``InvalidTokenError`` 子类），登录与全部鉴权请求永久 500 且不自愈。
    现空/空白文件一律视为未初始化：重新生成并原子写回（可自愈），并留
    warning 日志便于运维定位此前的空文件事件。写回失败（只读/权限）时退
    回**进程内稳定**的应急密钥（见 ``_EMERGENCY_SECRET``）并留 ERROR
    日志——绝不退回空串或每次换钥。
    """
    env = os.environ.get("COBOOKSHELF_JWT_SECRET", "").strip()
    if env:
        return env
    path = data_dir() / ".jwt_secret"
    try:
        existing = path.read_text(encoding="utf-8").strip()
    except OSError:
        existing = ""
    if existing:
        return existing
    global _EMERGENCY_SECRET
    with _secret_lock:
        # 双检：锁内可能已有并发线程写回成功（跨进程竞态仍由 O_EXCL 兜底）
        try:
            winner = path.read_text(encoding="utf-8").strip()
        except OSError:
            winner = ""
        if winner:
            return winner
        secret = secrets.token_hex(32)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and not _EMERGENCY_WRITE_LOGGED:
            # MM-03 主路径：文件存在但为空（首写前崩溃/掉电、备份截断）。
            logger.warning(
                "auth: empty .jwt_secret detected; regenerating and rewriting "
                "atomically (previously this broke login/auth with 500s)")
        try:
            fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            # 并发首登录竞态（跨进程）或既有空文件：优先读已存在内容，保证
            # 全体请求签发/校验同钥；仍为空则原子重写，失败走应急口径。
            try:
                winner = path.read_text(encoding="utf-8").strip()
            except OSError:
                return _emit_secret_via_emergency(secret)
            if winner:
                return winner
            if _atomic_write_secret(path, secret):
                _EMERGENCY_SECRET = None
                return secret
            return _emit_secret_via_emergency(secret)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(secret)
                fh.flush()
                os.fsync(fh.fileno())
        except OSError:
            # 首建后写失败（极可能留下空文件，下次调用走原子重写自愈）
            return _emit_secret_via_emergency(secret)
        _EMERGENCY_SECRET = None
        return secret


def create_access_token(user_id: int, username: str) -> str:
    """签发 HS256 JWT（24h 过期；载荷含 sub/username/iat/exp/jti/iss）。"""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(int(user_id)),
        "username": username,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=TOKEN_TTL_HOURS)).timestamp()),
        "jti": uuid.uuid4().hex,
        "iss": JWT_ISSUER,
    }
    return jwt.encode(payload, _secret(), algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> dict | None:
    """校验并解码 JWT；任何失败（过期/签名/格式）返回 None，绝不抛异常。

    MM-03：空 HMAC 钥触发 PyJWT ``InvalidKeyError``——它**不是**
    ``InvalidTokenError`` 子类（MRO 为 InvalidKeyError→PyJWTError→Exception），
    曾未被兜住而冒泡 500。纳入 except 后降级 None（→401）。
    """
    try:
        payload = jwt.decode(
            token,
            _secret(),
            algorithms=[JWT_ALGORITHM],
            issuer=JWT_ISSUER,
            options={"require": ["exp", "iat", "sub"]},
        )
    except (jwt.InvalidTokenError, jwt.InvalidKeyError, KeyError):
        return None
    if not isinstance(payload.get("sub"), str) or not payload.get("username"):
        return None
    return payload
