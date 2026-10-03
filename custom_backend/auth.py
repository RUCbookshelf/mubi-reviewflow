"""用户管理与 JWT 鉴权（custom_backend 专用，不改动 coscreen / ui）。

- 用户凭据存 ``{data_dir}/users.db``（SQLite，bcrypt 哈希，绝不存明文）；
- JWT：HS256 + 24h 过期，载荷含 sub(user_id) / username / iat / exp / jti；
- 签名密钥：优先环境变量 ``COBOOKSHELF_JWT_SECRET``，否则持久化到
  ``{data_dir}/.jwt_secret``（0600 权限创建），重启后令牌不失效；
- 密码策略：8~128 字节；用户名：3~64 字符，白名单字符（字母数字 . _ - @ 与 CJK）。
"""

from __future__ import annotations

import os
import re
import secrets
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

import bcrypt
import jwt
from coscreen.security import sanitize_component

__all__ = [
    "AuthError",
    "TOKEN_TTL_HOURS",
    "create_access_token",
    "create_user",
    "decode_access_token",
    "get_user",
    "get_user_by_id",
    "verify_password",
]

TOKEN_TTL_HOURS = 24
JWT_ALGORITHM = "HS256"
JWT_ISSUER = "cobookshelf"

#: 用户名白名单：字母数字、. _ - @ 与 CJK 汉字
_USERNAME_RE = re.compile(r"^[0-9A-Za-z._@\-\u4e00-\u9fff]{3,64}$")
_MIN_PASSWORD_LEN = 8
_MAX_PASSWORD_LEN = 128
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
    """bcrypt 哈希（rounds=12）；返回 ASCII 可存储的哈希串。"""
    digest = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=_BCRYPT_ROUNDS))
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
    return password


def create_user(username: str, password: str) -> dict:
    """注册新用户；重名抛 AuthError。返回 ``{id, username}``。"""
    clean = validate_username(username)
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

def _secret() -> str:
    """签名密钥：环境变量优先，否则 ``{data_dir}/.jwt_secret``（0600 新建）。"""
    env = os.environ.get("COBOOKSHELF_JWT_SECRET", "").strip()
    if env:
        return env
    path = data_dir() / ".jwt_secret"
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        pass
    secret = secrets.token_hex(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        # 并发首登录竞态：另一请求已生成密钥——优先读它，保证全体请求签发/校验同钥
        try:
            existing = path.read_text(encoding="utf-8").strip()
        except OSError:
            return secret
        return existing or secret
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(secret)
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
    """校验并解码 JWT；任何失败（过期/签名/格式）返回 None，绝不抛异常。"""
    try:
        payload = jwt.decode(
            token,
            _secret(),
            algorithms=[JWT_ALGORITHM],
            issuer=JWT_ISSUER,
            options={"require": ["exp", "iat", "sub"]},
        )
    except (jwt.InvalidTokenError, KeyError):
        return None
    if not isinstance(payload.get("sub"), str) or not payload.get("username"):
        return None
    return payload
