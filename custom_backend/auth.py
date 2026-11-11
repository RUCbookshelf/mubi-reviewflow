"""用户管理与 JWT 鉴权（custom_backend 专用，不改动 coscreen / ui）。

- 用户凭据存 ``{data_dir}/users.db``（SQLite，bcrypt 哈希，绝不存明文）；
  POSIX 建库后随即把库文件收紧为 0600（Windows 无 POSIX 权限位，no-op，
  依赖部署侧目录 ACL，见 docs/deployment/UBUNTU_SERVER.md 数据目录小节）；
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
import time
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
    # H组#4：是否本次建库（之后立即收紧权限）；存量库走补收紧（sec29b）。
    created = not path.exists()
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_USERS_SCHEMA)
    # authb（复审 M2）：补列按**库路径**进程内只做一次——每连接都带写意图
    # 会让未迁移旧库在并发写锁下把纯读路径拖成 5s 阻塞→500（实测）；按路径
    # 而非按进程记账，库文件被删除重建（测试夹具/运维换库）时新库仍会补列。
    global _PWD_COLUMN_READY
    key = str(path.resolve())
    if created:
        # 库文件不存在=全新库：executescript 建出的是无列旧形状表——从
        # 「已补列」集除名，强制本连接补列（同路径删库重建曾因集合残留
        # 被跳过 → get_user 查列 500）。
        _PWD_COLUMN_READY.discard(key)
    if key not in _PWD_COLUMN_READY:
        _ensure_pwd_version_column(conn)
        _PWD_COLUMN_READY.add(key)
    if created:
        _restrict_db_permissions(path)
    else:
        _tighten_existing_db_permissions(path)
    return conn


def _restrict_db_permissions(path: Path) -> None:
    """users.db 权限收紧为 0600（H组#4，仅 POSIX 实际生效）。

    deepseek 复审：sqlite3.connect 按进程 umask 建库，POSIX 默认 0644，
    同机其他账户可读 bcrypt 哈希表。建库后立即 chmod 0600；Windows 无
    POSIX 权限位（FAT/网络盘同理），任何 ``OSError`` 都按 no-op 吞掉，
    绝不影响建库/鉴权主路径（与 ai_keys._chmod_best_effort 同口径）。
    """
    try:
        os.chmod(str(path), 0o600)
    except OSError:
        pass


#: 已补收紧过的存量库路径（进程内去重，避免每连接重复 chmod 系统调用）。
_PERMS_TIGHTENED: set[str] = set()


def _tighten_existing_db_permissions(path: Path) -> None:
    """存量 users.db 的补收紧（sec29b·复审#3/#11）。

    H 组只对「本次新建」chmod 0600——升级部署的既有库保持 umask 的
    0644，原发现对存量未关闭。进程内每路径只补一次：POSIX 上组/其他位
    有权限才 chmod；Windows 无 POSIX 位（st_mode 恒 0666 形态），直接
    登记跳过以免每连接空转（部署文档已按目录 ACL 口径兜底）。
    """
    key = str(path.resolve())
    if key in _PERMS_TIGHTENED:
        return
    _PERMS_TIGHTENED.add(key)
    if os.name != "posix":
        return
    try:
        if path.stat().st_mode & 0o077:
            os.chmod(str(path), 0o600)
    except OSError:
        pass


def _ensure_pwd_version_column(conn: sqlite3.Connection) -> None:
    """users.pwd_version 幂等补列（2.1.5 记住我；authb 起每进程仅首连执行）。

    ALTER 先例：coscreen/db.py._add_column_if_missing——先查 pragma_table_info，
    缺列才 ALTER；撞 duplicate column（并发补列）按幂等成功。默认 1，改密/
    重置时 +1，使携带 pv 的令牌即时失效。
    """
    global _PWD_COLUMN_READY
    cols = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
    if "pwd_version" in cols:
        _PWD_COLUMN_READY.add(str(_users_db().resolve()))
        return
    try:
        conn.execute(
            "ALTER TABLE users ADD COLUMN pwd_version INTEGER NOT NULL DEFAULT 1")
        conn.commit()
    except sqlite3.OperationalError:
        cols_after = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
        if "pwd_version" not in cols_after:
            raise


#: 已补列的库路径（authb M2：按路径进程内去重，稳态连接零写意图；库删除
#: 重建后新路径/同路径重扫即重新补列——进程级布尔曾让重建库漏列 500）。
_PWD_COLUMN_READY: set[str] = set()


def set_password(user_id: int, new_password: str) -> None:
    """重置/改密落库（2.1.5 找回密码）：写新哈希并 pwd_version+1。

    调用方负责 validate_password 与重置码校验；+1 使所有携带 pv 的长时
    令牌在下一次 require_user 核验时失效（默认 24h 令牌按原语义自然过期）。
    """
    with closing(_connect()) as conn, conn:
        cur = conn.execute(
            "UPDATE users SET password_hash=?, pwd_version=pwd_version+1 WHERE id=?",
            (hash_password(new_password), int(user_id)))
        if cur.rowcount == 0:
            raise AuthError("用户不存在。")


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# 密码哈希（bcrypt，直接使用 bcrypt 库 —— passlib 1.7.4 与 bcrypt>=4 不兼容）
# ---------------------------------------------------------------------------

#: 缺失用户路径的哑哈希缓存单元（首次使用时计算一次；sec29）。此前每次
#: 「用户不存在」登录都现算 rounds=12 hashpw——缺失路径 hashpw+checkpw
#: 两次 bcrypt、真实路径 checkpw 一次，~2× 反向时延差本身构成用户名枚举
#: 的时序侧信道，且未认证者可用随机用户名把每请求 CPU 放大一倍。
_TIMING_EQUALIZER_HASH: list[str] = []


def _timing_equalizer_hash() -> str:
    if not _TIMING_EQUALIZER_HASH:
        _TIMING_EQUALIZER_HASH.append(
            bcrypt.hashpw(b"timing-equalizer",
                          bcrypt.gensalt(rounds=_BCRYPT_ROUNDS)).decode("ascii"))
    return _TIMING_EQUALIZER_HASH[0]


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
    """恒定时间校验 bcrypt 哈希；哈希损坏按不匹配处理（绝不抛异常）。

    sec29（DS·凭据与令牌）：口令统一截到 72 字节再入 checkpw——bcrypt 5.x
    对超长输入抛 ``ValueError``（此前折叠成 False），截断后与 bcrypt 自身
    「前 72 字节等价」语义对齐、两条路径行为一致；注册侧仍由
    validate_password/G-01 拒绝超长口令，语义不变。
    """
    try:
        return bcrypt.checkpw((password or "").encode("utf-8")[:72],
                              password_hash.encode("ascii"))
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
    """注册新用户；重名抛 AuthError。返回 ``{id, username}``。

    r6 #39/#45：重名检查前移到 ``hash_password`` **之前**——此前哈希先于
    查重，每次重名注册都烧一轮 rounds=12 bcrypt（r6 实测 12 连重名 2.17s
    CPU ≈ 0.18s/次），而注册端点对失败尝试 refund 不占限流额，未认证者
    可用重名/保留名无限轰炸 CPU。前移后失败注册只剩一次只读查询，「重名
    重试不占额」的宽容语义保留且不再有 bcrypt 消耗；事务内的最终查重
    保留，并发注册同一用户名仍由 BEGIN IMMEDIATE 临界区兜底。
    """
    clean = validate_username(username)
    if reserved_screener_collision(clean):
        # U4-P2：slug 撞上任务目录保留文件名 collaboration.db 的用户名
        # 会把个人筛选库写进任务协作权限库，注册即拒绝。
        raise AuthError("该用户名与任务协作数据库保留文件名冲突，请换一个用户名。")
    validate_password(password)
    key = sanitize_component(clean, "_").casefold()
    # r6 #45：查重前移（hash 前）——见 docstring；此处只读预检。
    with closing(_connect()) as conn:
        if any(sanitize_component(row[0], "_").casefold() == key
               for row in conn.execute("SELECT username FROM users")):
            raise AuthError("用户名与已有账号的筛选员数据路径冲突，请换一个用户名。")
    password_hash = hash_password(password)
    with closing(_connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
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
    """按用户名取用户行（含 pwd_version，2.1.5 记住我）；不存在返回 None。"""
    with closing(_connect()) as conn:
        row = conn.execute(
            "SELECT id, username, password_hash, pwd_version"
            " FROM users WHERE username = ?",
            ((username or "").strip(),),
        ).fetchone()
    if not row:
        return None
    return {"id": int(row[0]), "username": str(row[1]),
            "password_hash": str(row[2]),
            "pwd_version": int(row[3] if row[3] is not None else 1)}


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

    用户不存在时也对**预置**哑哈希执行一次同构校验，抹平「用户不存在」与
    「密码错误」的响应时间差（防用户名枚举的时序侧信道；sec29 前现算哈希
    造成 ~2× 反向时延差与 CPU 放大，见 ``_TIMING_EQUALIZER_HASH``）。
    """
    user = get_user(username)
    target = user["password_hash"] if user else _timing_equalizer_hash()
    ok = verify_password(password or "", target)
    if user and ok:
        # pwd_version 透传（2.1.5 记住我）：login 端点据此给 remember 令牌
        # 盖版本戳；多余键对既有调用方无影响。
        return {"id": user["id"], "username": user["username"],
                "pwd_version": user.get("pwd_version", 1)}
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

#: R3-02：并发首初始化的收敛等待窗口。只在**确证存在竞争者**时进入（见
#: ``_atomic_write_secret`` 的 reason 口径）。窗口取 120ms：真实创建者的
#: 首写 + fsync + replace 实测上限约 16ms（本机 20 次采样），留约 7 倍余量。
_SECRET_CONVERGE_TIMEOUT_SECONDS = 0.12
_SECRET_CONVERGE_STEP_SECONDS = 0.02
#: ``_REPLACE_BLOCKED`` 的进程内负缓存：同一路径在窗口内等不到收敛，说明
#: 「替换被永久挡住」（Windows 上被杀软/索引/备份以 share-deny-delete 打开
#: 是常态），此时本进程在冷却期内**不再做收敛等待**（读取照旧、写入仍每次
#: 重试），避免每个鉴权请求反复付窗口代价。
#: 该判断只在「目标文件与安装缓存时形态相同」的前提下成立——写入成功、读到
#: 有效持久钥（文件已恢复/并发创建者已写完），或目标文件指纹（stat）改变时，
#: 缓存立即清除（见 :func:`_clear_blocked_cooldown` 与
#: :func:`_target_fingerprint`），不会把真实并发创建者静默跳过收敛。
_SECRET_BLOCKED_COOLDOWN_SECONDS = 30.0
#: 持久密钥的可采用形态：64 位十六进制（``token_hex(32)`` 的产物）。大小写
#: 均接受——产品写入恒为小写，但校验只用于排除「对方尚未写完的截断钥」，
#: 不应因大小写差异把合法的完整密钥判为半截。重读值须匹配才采用。
_SECRET_HEX_RE = re.compile(r"^[0-9a-fA-F]{64}$")

#: ``_atomic_write_secret`` 的失败归因（写入 reason 列表首项）。
_REPLACE_BLOCKED = "replace_blocked"  # 临时文件已写好，仅替换被并发持有者挡住
_UNWRITABLE = "unwritable"            # 目录/文件不可写（只读介质、权限、占位）

#: 路径 -> ``(冷却截止 monotonic 时刻, 目标文件指纹)``（_REPLACE_BLOCKED
#: 负缓存）。指纹见 :func:`_target_fingerprint`。
_replace_blocked_until: dict[str, tuple[float, tuple[int, int] | None]] = {}


def _target_fingerprint(path: Path) -> tuple[int, int] | None:
    """``.jwt_secret`` 的轻量指纹 ``(st_mtime_ns, st_size)``；取不到为 None。

    只用于判断「负缓存安装后目标文件是否变过」。Windows 上 ``share-deny-
    delete`` 打开不影响 ``stat``，因此该调用通常不会因独占而失败。取不到
    （返回 None）时由调用方比较：两次都取不到视为「形态未变」，一次取不到
    一次取到视为「形态变了」。无论哪种情况，冷却期内的零等待重读仍在，文件
    真正恢复（可读且形态合法）时会被读到并清除缓存，故 stat 不可用不会让
    并发创建者被静默跳过。
    """
    try:
        stat = path.stat()
    except OSError:
        return None
    return (stat.st_mtime_ns, stat.st_size)


def _clear_blocked_cooldown(path: Path) -> None:
    """清除该路径的 ``_REPLACE_BLOCKED`` 负缓存。

    负缓存只编码「替换被**永久**挡住」这一判断；一旦写入成功（临时文件已
    落位、replace 成功）或读到有效的持久钥（文件已恢复/并发创建者已写完），
    该判断即失效，必须立即清除，否则冷却期内的真实并发创建者会被静默跳过
    收敛：本进程返回应急钥而磁盘是对方的钥，同一部署内签发/校验不同钥。
    该操作在每次成功路径上都是 O(1) 的 dict pop，不构成稳态开销。
    """
    _replace_blocked_until.pop(str(path), None)


def _atomic_write_secret(path: Path, secret: str, *, reason: list | None = None) -> bool:
    """同目录临时文件 + os.replace 原子写回密钥（写后 fsync，0600）。

    覆盖目标已存在（含空文件）的场景；掉电/崩溃不会留下半截或空密钥文件。
    返回是否写回成功——失败由调用方走进程内应急口径并负责诊断日志。
    ``reason``（可选，调用方传入的列表）收到失败归因：``_UNWRITABLE`` 表示
    存储层不可写；``_REPLACE_BLOCKED`` 表示临时文件已成功落盘、只有最终替换
    被挡住（典型为另一进程仍持有首建文件句柄）——后者才值得短暂等待收敛。
    """
    tmp = path.with_name(f".jwt_secret.{os.getpid()}.tmp")
    try:
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(secret)
            fh.flush()
            os.fsync(fh.fileno())
    except OSError:
        if reason is not None:
            reason.append(_UNWRITABLE)
        try:
            tmp.unlink()
        except OSError:
            pass
        return False
    try:
        os.replace(str(tmp), str(path))
    except OSError:
        if reason is not None:
            reason.append(_REPLACE_BLOCKED)
        try:
            tmp.unlink()
        except OSError:
            pass
        return False
    return True


def _secret_shape_ok(value: str) -> bool:
    """持久密钥形态校验：64 位十六进制（大小写均接受）。

    用于排除「并发创建者尚未写完的截断钥」（空串/半截同样不匹配）；产品
    写入恒为小写 ``token_hex(32)``。只施加于并发窗口内读到的值，早退路径
    不施加该限制（见 ``_secret`` docstring 的边界说明）。
    """
    return bool(_SECRET_HEX_RE.match(value or ""))


def _await_concurrent_creator(path: Path) -> str | None:
    """有界重读：并发创建者原子写入后采用其持久钥；否则 None。

    只在调用方**确证**替换被并发持有者挡住（``_REPLACE_BLOCKED``）时调用，
    因此这里的等待是「一次首初始化的竞态窗口」而非每个请求的固定开销。
    读到的值须通过 :func:`_secret_shape_ok`，否则继续等/放弃（不采用半截钥）。
    """
    deadline = time.monotonic() + _SECRET_CONVERGE_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        time.sleep(_SECRET_CONVERGE_STEP_SECONDS)
        try:
            winner = path.read_text(encoding="utf-8").strip()
        except OSError:
            winner = ""
        if _secret_shape_ok(winner):
            return winner
    return None


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
    R3-03：应急口径同样覆盖**首次建库**失败——父目录不可创建（被普通
    文件占位/无权限）与首开 ``O_EXCL`` 抛非 ``FileExistsError`` 的存储
    ``OSError``（密钥路径被目录占用、只读数据目录）此前直接冒泡，login/
    create_access_token 全线 500，与本 docstring 承诺的「只读/权限 → 应
    急密钥」相反。非 ``OSError`` 的编程错误不在此被掩盖。
    R3-02：并发首初始化收敛——O_EXCL 只保证唯一创建者，不保证各进程返回
    同一密钥（创建者在首写前被并发原子替换时，其 fd 写入落在已脱离目录
    的旧 inode 上）。首建者写后重读持久值、替换被并发持有者挡住的一方有界
    等待重读，任一路径都以磁盘持久钥为返回基准，消除「一个 worker 的钥 ≠
    持久钥」。
    收敛等待的边界（有意为之，勿当成缺陷）：
    - 等待窗口 120ms（真实创建者首写+fsync+replace 实测上限约 16ms）；
    - 不可写介质（只读/权限/路径被占位）零等待直接降级；
    - ``os.replace`` 被**永久**挡住时（Windows 上杀软/索引/备份以
      share-deny-delete 打开 ``.jwt_secret`` 是常态），首个请求付一次窗口
      代价后做 30s 进程内负缓存：期间**不再付窗口代价**（每次仍尝试原子写，
      并做一次零等待重读，见下），数据目录恢复可写后下一次调用即自愈。即该
      场景是「一次 120ms + 稳态零额外开销」，不是每请求付费。
      负缓存**不是**单向的：它只编码「替换被永久挡住」这一判断，以下任一
      情形立即失效（见 :func:`_clear_blocked_cooldown` /
      :func:`_target_fingerprint`）——
        * 任一成功写回（首建 O_EXCL 写完，或原子替换成功）；
        * 读到有效的持久钥（早退、锁内双检、并发收敛等待成功，以及冷却期内
          那一次零等待重读读到形态合法的值）；
        * 目标文件指纹（``st_mtime_ns`` / ``st_size``）与安装缓存时不同，
          即「文件先坏后好再坏」：旧判断已失效，重新付一次完整窗口去等真实
          并发创建者。
      因此冷却期内的真实并发创建者不会被静默跳过收敛（R3-02 语义不缩水），
      同时稳态仍不为每个请求付 120ms。
    - 形态校验（64 位 hex）只施加于**并发窗口内读到的值**（排除对方尚未
      写完的截断钥）；早退路径直接采用文件/环境变量里已有的密钥，不施加
      该限制，以免拒绝运维手工放置的非 token_hex 形态密钥。
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
        # 文件已恢复/对方已写完：负缓存所编码的「永久挡住」判断失效。
        _clear_blocked_cooldown(path)
        return existing
    global _EMERGENCY_SECRET
    with _secret_lock:
        # 双检：锁内可能已有并发线程写回成功（跨进程竞态仍由 O_EXCL 兜底）
        try:
            winner = path.read_text(encoding="utf-8").strip()
        except OSError:
            winner = ""
        if winner:
            _clear_blocked_cooldown(path)
            return winner
        secret = secrets.token_hex(32)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            # R3-03：父目录不可创建（被普通文件占位 → FileExistsError、无
            # 权限 → PermissionError）——存储层失败，与空密钥写回失败同一
            # 层的有界应急回退，不冒泡 500。
            return _emit_secret_via_emergency(secret)
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
                _clear_blocked_cooldown(path)
                return winner
            reason: list[str] = []
            if _atomic_write_secret(path, secret, reason=reason):
                _clear_blocked_cooldown(path)
                _EMERGENCY_SECRET = None
                return secret
            # R3-02（Windows 镜像）：原子替换失败常因另一进程正持有首建 fd、
            # 其首写尚未落盘——此刻并非「只读介质」。**只有**确证「临时文件
            # 已落盘、仅替换被并发持有者挡住」才做有界等待重读（对方完成后
            # 采用其持久钥）；不可写介质一律零等待直接落应急口径，不给每个
            # 鉴权请求叠加阻塞。
            if _REPLACE_BLOCKED in reason:
                key = str(path)
                now = time.monotonic()
                cached = _replace_blocked_until.get(key)
                if (cached is not None and cached[0] > now
                        and cached[1] is not None
                        and cached[1] == _target_fingerprint(path)):
                    # 冷却期内且文件形态未变：不再付完整窗口代价，但仍做一次
                    # **零等待重读**——真实并发创建者往往刚刚写完，这一步以
                    # 一次 read（微秒级）把它接住，不静默跳过收敛。读到有效
                    # 钥即采用并清除缓存。该分支只会在「本次调用开始时文件为
                    # 空、现在非空」时命中，即一定是并发写入，故施加形态校验
                    # （对方可能只写了一半），不以截断钥签发令牌。
                    try:
                        late = path.read_text(encoding="utf-8").strip()
                    except OSError:
                        late = ""
                    if _secret_shape_ok(late):
                        _clear_blocked_cooldown(path)
                        _EMERGENCY_SECRET = None
                        return late
                else:
                    # 无冷却项、冷却已过期，或目标文件指纹变了（先坏后好再坏
                    # / 对方刚写完）：旧判断失效，重新付一次完整窗口。
                    converged = _await_concurrent_creator(path)
                    if converged:
                        _clear_blocked_cooldown(path)
                        _EMERGENCY_SECRET = None
                        return converged
                    _replace_blocked_until[key] = (
                        now + _SECRET_BLOCKED_COOLDOWN_SECONDS,
                        _target_fingerprint(path),
                    )
            return _emit_secret_via_emergency(secret)
        except OSError:
            # R3-03：首开失败但非「已存在」（密钥路径被目录占用、只读数据
            # 目录等）——同为存储层失败，走应急口径；编程错误仍照常冒泡。
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
        # 首建成功即写入成功：负缓存所编码的「替换被永久挡住」判断失效。
        _clear_blocked_cooldown(path)
        # R3-02：并发首初始化收敛——O_EXCL 创建与首写之间，另一进程可能已把
        # 空文件原子替换为新钥（POSIX 下本 fd 指向已被替换的旧 inode，写入
        # 落在已解除链接的副本上；Windows 下对方替换失败、走应急口径）。写后
        # 重读持久值并以其为准，保证本进程返回的密钥与磁盘持久钥一致；只采用
        # 形态合法的值（对方可能尚未写完），否则以本进程已落盘的 secret 为准。
        try:
            persisted = path.read_text(encoding="utf-8").strip()
        except OSError:
            persisted = ""
        return persisted if _secret_shape_ok(persisted) else secret


def create_access_token(user_id: int, username: str, *,
                        ttl_hours: int | None = None,
                        pwd_version: int | None = None) -> str:
    """签发 HS256 JWT（默认 24h；remember 令牌 7 天）。

    2.1.5 记住我/authb（复审 M1）：``pwd_version`` 给定时载荷附带 ``pv``
    ——require_user 对带 pv 的令牌逐请求核验，改密/重置（pwd_version+1）
    即全量失效。authb 起 login/register **恒传** pwd_version，一切新签令牌
    均可吊销（修复「重置后 24h 短令牌仍存活」——重置是产品唯一凭据补救
    动作）；不传 pwd_version 的调用（兼容/测试）保持旧载荷形状，旧令牌
    ≤24h 自然过期。长时令牌漏传 pwd_version 会静默盖 pv=1 使改密用户误
    401（复审实证），调用方不得依赖缺省。
    """
    now = datetime.now(timezone.utc)
    hours = TOKEN_TTL_HOURS if ttl_hours is None else int(ttl_hours)
    payload = {
        "sub": str(int(user_id)),
        "username": username,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=hours)).timestamp()),
        "jti": uuid.uuid4().hex,
        "iss": JWT_ISSUER,
    }
    if pwd_version is not None:
        payload["pv"] = int(pwd_version)
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
