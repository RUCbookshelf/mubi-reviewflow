"""P07　请求回执与归档（team flow receipts，C09）。

合同权威：docs/superpowers/plans/2026-11-16-2.1.6-team-contracts.md 的 C02
（键/连接/错误共同合同）与 C09（请求回执与归档）全节。风格与 coscreen 既有
P 包对齐：append-only 物理表＋scoped unique＋borrowed-connection 读写
（参照 team_vote_ledger.py P04、team_terminal_facts.py F1、
team_terminal_registry.py F2 的 canonical JSON/UTC/错误类做法）。

本模块只做库层四 API；Idempotency-Key/X-ReviewFlow-Request-Id 头、来源
退休编排、先查 collab 归档再查 target 等属 C09 的 HTTP/管理层条款，由后续
包（P15+/P18）消费，本文件不实现 HTTP 语义。

合同要点（不放宽）：
- append-only：表上 BEFORE UPDATE/BEFORE DELETE 触发器一律 RAISE(ABORT)，
  另有 BEFORE INSERT 守卫触发器（同 receipt_id 或同四 scope 已存在即
  RAISE(ABORT)）堵 INSERT OR REPLACE 物理绕过（REPLACE 的隐式 DELETE 不
  触发 no_delete 触发器）；四 scope 字段（task_instance_id+actor_user_id+
  operation+request_id）精确 UNIQUE；receipt_id = 四 scope 字段 canonical
  JSON 的 SHA-256，
  payload_sha256 = 完整 payload canonical JSON 的 SHA-256（canonical JSON
  与既有实现一致：ensure_ascii=False、sort_keys、紧凑分隔符、allow_nan=
  False；时间一律 RFC3339 UTC 毫秒 Z）。
- 幂等：同 scope 首次成功后，同完整 canonical payload 的再次 append 返回
  原记录（原 recorded_at 不重生成、原结果不重算）；payload 任何差异
  （含 request_sha256 不同）＝TeamFlowReceiptConflict（上游映射 409）。
  借用连接写后在同一连接读回并核对完整记录；意外的触发器/索引会让
  _require_prepared 判 schema_mismatch，而不是让 INSERT 被静默抑制后返回
  假成功。
- 与既有 research_trace 请求追踪并存：本模块不 import、不读写、不迁移
  research_trace 的任何表（trace_write_operations 等仍由其自身合同管理），
  不替代/不吞掉原 trace 语义。
- payload 精确字段集：schema='team_flow_receipt.v1'、task_instance_id、
  actor_user_id、operation、request_id、request_sha256、result（公开 JSON
  对象）、internal_evidence（私有 JSON 对象，仅服务端可读，本层随 payload
  原样存取，不向任何 peer 做裁剪投影——裁剪属 HTTP 层）、recorded_at
  （规范 UTC，append 不重生成时间）、source_registration_id、
  source_generation（collab 原产二者 None；actor 原产为真实绑定，二者必须
  同空同现，origin 由该成对字段推导）。JSON 拒 NaN/无穷/坏 Unicode（lone
  surrogate）；actor_user_id/source_generation 拒 bool、严格 1..int64 上界。
- 借用合同（C02）：*_on 只借调用方连接——真实 type 继承判断（拒 __class__
  伪装代理，closed native 连接的 ProgrammingError 原样透传）、活动事务、
  row_factory is sqlite3.Row；不 BEGIN/SAVEPOINT/commit/rollback/close、
  不改 PRAGMA/factory、不重连/迁移；自建 Cursor 完整物化并确定关闭，主错
  与清理错都不吞（清理失败挂 cleanup_errors）。archive 保原 payload 字节、
  原 receipt_id/payload_sha256、原 recorded_at；archived_at 只在真正新插入
  时取本次时间，幂等命中保留首次 archived_at。
- prepare_team_flow_receipts 是唯一自管连接入口：mode=rw 自开（绝不创建
  缺失文件）、单 BEGIN IMMEDIATE、建表或复核既装布局、COMMIT 后关闭；
  成功 commit 后清理失败如实标记已提交（.committed=True）。

无 import 期 SQL；除标准库外不依赖任何模块（与 research_trace 及 backend
均无环）。
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import sys
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

__all__ = [
    "prepare_team_flow_receipts",
    "read_receipt_on",
    "append_receipt_on",
    "archive_receipts_on",
    "TeamFlowReceiptPayloadInvalid",
    "TeamFlowReceiptConflict",
    "TeamFlowReceiptUnavailable",
]

PAYLOAD_SCHEMA = "team_flow_receipt.v1"
SCHEMA_VERSION = 1

TABLE = "team_flow_receipts"
META_TABLE = "team_flow_receipts_meta"
TRIGGER_NO_UPDATE = "team_flow_receipts_no_update"
TRIGGER_NO_DELETE = "team_flow_receipts_no_delete"
TRIGGER_GUARD_INSERT = "team_flow_receipts_guard_insert"

ORIGINS = ("actor", "collab", "actor_archive")
ORIGIN_ACTOR = "actor"
ORIGIN_COLLAB = "collab"
ORIGIN_ARCHIVE = "actor_archive"

#: 允许的 ``TeamFlowReceiptUnavailable.reason`` 取值（与 F1/F2 同族）。
REASONS = frozenset({
    "not_prepared",
    "schema_mismatch",
    "unreadable",
    "corrupt",
    "connection_contract",
})

_MAX_INT64 = 9223372036854775807
_HASH_RE = re.compile(r"[0-9a-f]{64}\Z")
_TIMESTAMP_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z\Z")

_PAYLOAD_KEYS = frozenset({
    "schema",
    "task_instance_id",
    "actor_user_id",
    "operation",
    "request_id",
    "request_sha256",
    "result",
    "internal_evidence",
    "recorded_at",
    "source_registration_id",
    "source_generation",
})
#: append/read 返回的记录 = payload 精确字段 + receipt_id + payload_sha256。
_RECORD_KEYS = _PAYLOAD_KEYS | frozenset({"receipt_id", "payload_sha256"})
_SCOPE_COLUMNS = ("task_instance_id", "actor_user_id", "operation", "request_id")
_RECORD_COLUMNS = (
    "receipt_id",
    "task_instance_id",
    "actor_user_id",
    "operation",
    "request_id",
    "payload_json",
    "payload_sha256",
    "origin",
    "archived_at",
)
_SELECT_RECORD = (
    "SELECT " + ", ".join(_RECORD_COLUMNS) + " FROM main." + TABLE
)

# 受支持规范 DDL（schema 1）。表上三条触发器保证 append-only（含 INSERT
# 守卫，堵 INSERT OR REPLACE 物理绕过）；TEXT 主键与四字段 UNIQUE 恰好各
# 产生一个 SQLite 自动索引（_1/_2）——守卫触发器不产生任何索引。
TABLE_DDL = (
    "CREATE TABLE team_flow_receipts ("
    "receipt_id TEXT NOT NULL PRIMARY KEY CHECK(length(receipt_id)=64),"
    "task_instance_id TEXT NOT NULL CHECK(length(task_instance_id)>=1),"
    "actor_user_id INTEGER NOT NULL CHECK("
    "typeof(actor_user_id)='integer' AND actor_user_id>=1),"
    "operation TEXT NOT NULL CHECK(length(operation)>=1),"
    "request_id TEXT NOT NULL CHECK(length(request_id)>=1),"
    "payload_json TEXT NOT NULL,"
    "payload_sha256 TEXT NOT NULL CHECK(length(payload_sha256)=64),"
    "origin TEXT NOT NULL CHECK(origin IN ('actor','collab','actor_archive')),"
    "archived_at TEXT NULL,"
    "UNIQUE(task_instance_id, actor_user_id, operation, request_id)"
    ")"
)

META_DDL = (
    "CREATE TABLE team_flow_receipts_meta ("
    "singleton INTEGER NOT NULL PRIMARY KEY CHECK(singleton=1),"
    "schema_version INTEGER NOT NULL CHECK(schema_version=1)"
    ")"
)

TRIGGER_NO_UPDATE_DDL = (
    "CREATE TRIGGER team_flow_receipts_no_update "
    "BEFORE UPDATE ON team_flow_receipts "
    "BEGIN SELECT RAISE(ABORT, 'team flow receipts are append-only'); END"
)

TRIGGER_NO_DELETE_DDL = (
    "CREATE TRIGGER team_flow_receipts_no_delete "
    "BEFORE DELETE ON team_flow_receipts "
    "BEGIN SELECT RAISE(ABORT, 'team flow receipts are append-only'); END"
)

# N1 守卫触发器：INSERT OR REPLACE 的隐式 DELETE 不触发 no_delete 触发器，
# 故 BEFORE INSERT 时旧行仍在；同 receipt_id 或同四 scope 的（再）插入一律
# RAISE(ABORT)，堵住物理绕过 append-only 的缺口。正常首插（无同键行）不
# 命中 WHEN，不受影响；本模块自身的幂等路径先读后写/撞键后重读判等，语义
# 不变（RAISE(ABORT) 以 IntegrityError 透出，恰好落入既有撞键分支）。
TRIGGER_GUARD_INSERT_DDL = (
    "CREATE TRIGGER team_flow_receipts_guard_insert "
    "BEFORE INSERT ON team_flow_receipts "
    "WHEN EXISTS(SELECT 1 FROM team_flow_receipts WHERE "
    "receipt_id = NEW.receipt_id "
    "OR (task_instance_id = NEW.task_instance_id "
    "AND actor_user_id = NEW.actor_user_id "
    "AND operation = NEW.operation "
    "AND request_id = NEW.request_id)) "
    "BEGIN SELECT RAISE(ABORT, "
    "'team_flow_receipts is append-only: reject re-insert/replace'); END"
)

_EXPECTED_TABLE_INFO = (
    ("receipt_id", "TEXT", 1, 1),
    ("task_instance_id", "TEXT", 1, 0),
    ("actor_user_id", "INTEGER", 1, 0),
    ("operation", "TEXT", 1, 0),
    ("request_id", "TEXT", 1, 0),
    ("payload_json", "TEXT", 1, 0),
    ("payload_sha256", "TEXT", 1, 0),
    ("origin", "TEXT", 1, 0),
    ("archived_at", "TEXT", 0, 0),
)

_SQLITE_CORRUPT = 11
_SQLITE_NOTADB = 26
# 本 DDL 恰好产生这两个 SQLite 自有自动索引：_1 对应 TEXT PRIMARY KEY
# (receipt_id)，_2 对应四字段 UNIQUE（task_instance_id, actor_user_id,
# operation, request_id）。meta 表为 INTEGER 主键，无自动索引。
_EXPECTED_AUTOINDEXES = frozenset({
    "sqlite_autoindex_" + TABLE + "_1",
    "sqlite_autoindex_" + TABLE + "_2",
})


# ---------------------------------------------------------------------------
# 异常（命名对齐 coscreen 既有 Terminal* 三分法：PayloadInvalid/Conflict/
# Unavailable；Conflict 上游映射 HTTP 409）
# ---------------------------------------------------------------------------

class TeamFlowReceiptPayloadInvalid(ValueError):
    """receipt payload/记录结构、类型或 JSON 合法性不符。"""


class TeamFlowReceiptConflict(RuntimeError):
    """同一四字段 scope 已持有不同内容（幂等冲突；HTTP 层映射 409）。"""


class TeamFlowReceiptUnavailable(RuntimeError):
    """存储缺失/不符/不可读/损坏，或借用连接合同违约；reason 取 REASONS 之一。"""

    def __init__(self, reason: str, message: str = "") -> None:
        if reason not in REASONS:
            raise ValueError(
                f"unknown TeamFlowReceiptUnavailable reason: {reason!r}")
        self.reason = reason
        super().__init__(message or reason)


# ---------------------------------------------------------------------------
# 小工具：异常工厂 / canonical JSON / 哈希 / 时间 / 标量校验
# ---------------------------------------------------------------------------

def _payload_invalid(message: str) -> TeamFlowReceiptPayloadInvalid:
    return TeamFlowReceiptPayloadInvalid(message)


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant is not allowed: {value}")


def _loads_strict(value: str) -> Any:
    """严格 JSON 解析（NaN/Infinity 常量拒绝），用于读回存储的 payload_json。"""
    return json.loads(value, parse_constant=_reject_constant)


def _canonical_json(value: Any) -> str:
    """canonical JSON：与 team_terminal_facts.canonical_json 同一规则。

    ensure_ascii=False、sort_keys=True、紧凑分隔符、allow_nan=False；再以
    UTF-8 编码核一次，拒 lone surrogate 等“坏 Unicode”。
    """
    try:
        text = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise _payload_invalid(
            "value is not canonical-JSON serializable") from exc
    try:
        text.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise _payload_invalid(
            "value contains text that is not valid UTF-8") from exc
    return text


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _receipt_id(task_instance_id: str, actor_user_id: int,
                operation: str, request_id: str) -> str:
    """C09：receipt_id = 四个 scope 字段 canonical JSON 的 SHA-256。"""
    return _sha256_text(_canonical_json({
        "task_instance_id": task_instance_id,
        "actor_user_id": actor_user_id,
        "operation": operation,
        "request_id": request_id,
    }))


def _storage_unavailable(exc: sqlite3.Error, *, api: str) \
        -> TeamFlowReceiptUnavailable:
    """真实 SQLite 存储失败按主结果码映射 corrupt/unreadable（F1 同族）。"""
    code = getattr(exc, "sqlite_errorcode", None)
    primary = code & 0xFF if isinstance(code, int) else None
    reason = ("corrupt" if primary in (_SQLITE_CORRUPT, _SQLITE_NOTADB)
              else "unreadable")
    return TeamFlowReceiptUnavailable(
        reason,
        f"{api} requires storage that raised {type(exc).__name__}: {exc}")


def _norm_sql(sql: Any) -> str:
    return " ".join((sql or "").split())


def _schema_mismatch(message: str) -> None:
    raise TeamFlowReceiptUnavailable("schema_mismatch", message)


def _not_prepared(message: str = "team flow receipts table is not prepared") \
        -> None:
    raise TeamFlowReceiptUnavailable("not_prepared", message)


def _corrupt(message: str) -> None:
    raise TeamFlowReceiptUnavailable("corrupt", message)


def _strict_text(value: Any, name: str) -> str:
    """非空、无 NUL、可严格 UTF-8 编码的文本（对齐 registry._strict_text）。"""
    if not isinstance(value, str) or not value:
        raise _payload_invalid(f"{name} must be non-empty text")
    if "\x00" in value:
        raise _payload_invalid(f"{name} must not contain NUL")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise _payload_invalid(f"{name} must be valid UTF-8 text") from exc
    return value


def _check_json_value(value: Any, name: str) -> None:
    """递归核 JSON 兼容性：拒 NaN/无穷、非字符串对象键、坏 Unicode、集/元类型。"""
    if value is None or type(value) is bool:
        return
    if type(value) is int:
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise _payload_invalid(
                f"{name} must not contain NaN or infinite numbers")
        return
    if isinstance(value, str):
        try:
            value.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise _payload_invalid(
                f"{name} must be valid UTF-8 text") from exc
        return
    if isinstance(value, Mapping):
        for key in value.keys():
            if not isinstance(key, str):
                raise _payload_invalid(
                    f"{name} object keys must be JSON strings")
            try:
                key.encode("utf-8")
            except UnicodeEncodeError as exc:
                raise _payload_invalid(
                    f"{name} object keys must be valid UTF-8 text") from exc
        for item in value.values():
            _check_json_value(item, name)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _check_json_value(item, name)
        return
    raise _payload_invalid(f"{name} is not JSON-compatible data")


def _json_object(value: Any, name: str) -> Mapping[str, Any]:
    """result/internal_evidence 必须是键为字符串的 JSON 对象且整体合法。"""
    if not isinstance(value, Mapping):
        raise _payload_invalid(f"{name} must be a JSON object")
    _check_json_value(value, name)
    return value


def _hash64(value: Any, name: str) -> str:
    if not isinstance(value, str) or _HASH_RE.fullmatch(value) is None:
        raise _payload_invalid(
            f"{name} must be 64 lowercase hex characters")
    return value


def _valid_timestamp(value: Any) -> bool:
    if not isinstance(value, str) or _TIMESTAMP_RE.fullmatch(value) is None:
        return False
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ")
    except ValueError:
        return False
    return True


def _timestamp(value: Any, name: str) -> str:
    if not _valid_timestamp(value):
        raise _payload_invalid(
            f"{name} must be an RFC3339 UTC timestamp with millisecond "
            "precision")
    return value


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(
        timespec="milliseconds").replace("+00:00", "Z")


def _uid(value: Any, name: str) -> int:
    """严格 int（拒 bool）且 1..int64 上界（C09：类型与 int64 边界严格）。"""
    if type(value) is not int or value < 1 or value > _MAX_INT64:
        raise _payload_invalid(
            f"{name} must be an integer in [1, {_MAX_INT64}]")
    return value


def _positive_int(value: Any, name: str) -> int:
    if type(value) is not int or value < 1 or value > _MAX_INT64:
        raise _payload_invalid(
            f"{name} must be an integer in [1, {_MAX_INT64}]")
    return value


# ---------------------------------------------------------------------------
# payload 校验 / scope / origin 推导
# ---------------------------------------------------------------------------

def _validate_payload(payload: Any) -> dict[str, Any]:
    """校验并归一 C09 v1 payload：精确 11 键、逐字段类型/边界、成对源绑定。"""
    if not isinstance(payload, Mapping):
        raise _payload_invalid("payload must be a JSON object")
    actual = set(payload.keys())
    if actual != _PAYLOAD_KEYS:
        missing = sorted(key for key in _PAYLOAD_KEYS if key not in actual)
        unknown = sorted(repr(key) for key in actual if key not in _PAYLOAD_KEYS)
        raise _payload_invalid(
            f"payload fields mismatch (missing={missing}, unknown={unknown})")
    if payload["schema"] != PAYLOAD_SCHEMA:
        raise _payload_invalid(
            f"payload.schema must be {PAYLOAD_SCHEMA!r}")
    task_instance_id = _strict_text(
        payload["task_instance_id"], "task_instance_id")
    actor_user_id = _uid(payload["actor_user_id"], "actor_user_id")
    operation = _strict_text(payload["operation"], "operation")
    request_id = _strict_text(payload["request_id"], "request_id")
    request_sha256 = _hash64(payload["request_sha256"], "request_sha256")
    result = _json_object(payload["result"], "result")
    internal_evidence = _json_object(
        payload["internal_evidence"], "internal_evidence")
    recorded_at = _timestamp(payload["recorded_at"], "recorded_at")
    registration = payload["source_registration_id"]
    generation = payload["source_generation"]
    if registration is None and generation is None:
        pass  # collab 原产：二者 None
    elif registration is None or generation is None:
        raise _payload_invalid(
            "source_registration_id and source_generation must be both null "
            "or both present")
    else:
        registration = _strict_text(
            registration, "source_registration_id")
        generation = _positive_int(generation, "source_generation")
    return {
        "schema": PAYLOAD_SCHEMA,
        "task_instance_id": task_instance_id,
        "actor_user_id": actor_user_id,
        "operation": operation,
        "request_id": request_id,
        "request_sha256": request_sha256,
        "result": result,
        "internal_evidence": internal_evidence,
        "recorded_at": recorded_at,
        "source_registration_id": registration,
        "source_generation": generation,
    }


def _scope_of(payload: Mapping[str, Any]) -> tuple[str, int, str, str]:
    return (
        payload["task_instance_id"],
        payload["actor_user_id"],
        payload["operation"],
        payload["request_id"],
    )


def _origin_of_payload(payload: Mapping[str, Any]) -> str:
    """源绑定成对在场＝actor 原产；二者 None＝collab 原产（C09 payload 规则）。"""
    if payload["source_registration_id"] is None:
        return ORIGIN_COLLAB
    return ORIGIN_ACTOR


# ---------------------------------------------------------------------------
# 借用连接与 SQL 执行（C02：不 BEGIN/SAVEPOINT/commit/rollback/close、不改
# PRAGMA/factory；自建 Cursor 完整物化并确定关闭；主错/清理错都不吞）
# ---------------------------------------------------------------------------

def _require_on_conn(conn: sqlite3.Connection, *, api: str) -> None:
    """C02 借用合同：真实 type 继承（拒 __class__ 伪装）→ 活动事务 → Row 工厂。

    closed native 连接在 ``conn.in_transaction`` 处抛出的
    sqlite3.ProgrammingError 原样透传，不包装。
    """
    if not issubclass(type(conn), sqlite3.Connection):
        raise TeamFlowReceiptUnavailable(
            "connection_contract",
            f"{api} requires a native sqlite3.Connection or native subclass")
    if not conn.in_transaction:
        raise TeamFlowReceiptUnavailable(
            "connection_contract",
            f"{api} requires an active caller transaction")
    if conn.row_factory is not sqlite3.Row:
        raise TeamFlowReceiptUnavailable(
            "connection_contract",
            f"{api} requires conn.row_factory is sqlite3.Row")


def _execute(conn: sqlite3.Connection, sql: str,
             params: Iterable[Any] = (), *, api: str,
             fetch: Literal["one", "all"]) -> Any:
    """借用读：单一边界内执行并完整取回；自建 Cursor 确定关闭（F1 同族）。"""
    if fetch not in ("one", "all"):
        raise ValueError("fetch must be 'one' or 'all'")
    cursor = None
    fetched = False
    try:
        cursor = conn.execute(sql, params)
        result = cursor.fetchone() if fetch == "one" else cursor.fetchall()
        fetched = True
        return result
    except sqlite3.ProgrammingError:
        raise
    except sqlite3.Error as exc:
        raise _storage_unavailable(exc, api=api) from exc
    finally:
        if cursor is not None:
            # 自建 Cursor 属本函数：完整物化后确定关闭；保留的 traceback 不得
            # 让语句保持活动。成功调用在他人 except 块内时无自己的 primary。
            primary = None if fetched else sys.exc_info()[1]
            try:
                cursor.close()
            except Exception as cleanup:  # noqa: BLE001 - 清理错必须可观察
                if primary is not None:
                    primary.cleanup_errors = (
                        *getattr(primary, "cleanup_errors", ()), cleanup)
                elif isinstance(cleanup, sqlite3.ProgrammingError):
                    raise
                elif isinstance(cleanup, sqlite3.Error):
                    raise _storage_unavailable(
                        cleanup, api=f"{api} cursor close") from cleanup
                else:
                    raise


def _execute_write(conn: sqlite3.Connection, sql: str,
                   params: Iterable[Any] = (), *, api: str) -> None:
    """借用写（INSERT）：IntegrityError 原样透出供撞键判定；Cursor 确定关闭。"""
    cursor = None
    finished = False
    try:
        cursor = conn.execute(sql, params)
        if cursor.description is not None:
            # 结果产出的 DML 必须先取完，SQLite 才暴露最终状态。
            cursor.fetchall()
        finished = True
    except (sqlite3.IntegrityError, sqlite3.ProgrammingError):
        raise
    except sqlite3.Error as exc:
        raise _storage_unavailable(exc, api=api) from exc
    finally:
        if cursor is not None:
            primary = None if finished else sys.exc_info()[1]
            try:
                cursor.close()
            except Exception as cleanup:  # noqa: BLE001 - 清理错必须可观察
                if primary is not None:
                    primary.cleanup_errors = (
                        *getattr(primary, "cleanup_errors", ()), cleanup)
                elif isinstance(cleanup, sqlite3.ProgrammingError):
                    raise
                elif isinstance(cleanup, sqlite3.Error):
                    raise _storage_unavailable(
                        cleanup, api=f"{api} cursor close") from cleanup
                else:
                    raise


# ---------------------------------------------------------------------------
# 布局门（borrowed：SELECT-only；PRAGMA 仅 prepare 自管连接内使用）
# ---------------------------------------------------------------------------

def _objects(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """检视 main 对象并拒绝相关的连接私有 TEMP 对象（F1 同族）。"""
    reserved_names = (
        TABLE, META_TABLE, TRIGGER_NO_UPDATE, TRIGGER_NO_DELETE,
        TRIGGER_GUARD_INSERT, *sorted(_EXPECTED_AUTOINDEXES),
    )
    predicate = (
        " WHERE tbl_name COLLATE NOCASE IN (?,?) OR name COLLATE NOCASE IN ("
        + ",".join("?" for _ in reserved_names) + ")"
    )
    params = (TABLE, META_TABLE, *reserved_names)
    rows = _execute(
        conn,
        "SELECT name,type,tbl_name,sql FROM main.sqlite_master" + predicate,
        params, api="team flow receipts schema", fetch="all",
    )
    temp_rows = _execute(
        conn,
        "SELECT name,type,tbl_name,sql FROM temp.sqlite_master" + predicate,
        params, api="team flow receipts TEMP schema", fetch="all",
    )
    if temp_rows:
        _schema_mismatch("team flow receipts schema has related TEMP objects")
    return rows


def _require_prepared(conn: sqlite3.Connection) -> None:
    """借用布局门：精确对象集（两表、三触发器、两个自动索引、marker 行）。

    额外触发器（含 RAISE(IGNORE) 的 INSERT 抑制器）或额外用户索引＝
    schema_mismatch，而非被静默抑制后的假成功。SELECT-only，无 PRAGMA。
    """
    rows = _objects(conn)
    if not rows:
        _not_prepared()
    expected_tables = {TABLE: TABLE_DDL, META_TABLE: META_DDL}
    expected_triggers = {
        TRIGGER_NO_UPDATE: TRIGGER_NO_UPDATE_DDL,
        TRIGGER_NO_DELETE: TRIGGER_NO_DELETE_DDL,
        TRIGGER_GUARD_INSERT: TRIGGER_GUARD_INSERT_DDL,
    }
    # 触发器与表/索引名字空间分离：先拒重名，再建名字映射。
    names = [row["name"] for row in rows]
    if len(set(names)) != len(names):
        _schema_mismatch(
            "team flow receipts schema has duplicate object names")
    by_name = {row["name"]: row for row in rows}
    for name, ddl in expected_tables.items():
        row = by_name.get(name)
        if row is None:
            _schema_mismatch(f"team flow receipts schema is missing {name}")
        if (row["type"] != "table" or row["tbl_name"] != name
                or _norm_sql(row["sql"]) != _norm_sql(ddl)):
            _schema_mismatch(
                f"team flow receipts schema mismatch for {name}")
    for name, ddl in expected_triggers.items():
        row = by_name.get(name)
        if row is None:
            _schema_mismatch(f"team flow receipts schema is missing {name}")
        if (row["type"] != "trigger" or row["tbl_name"] != TABLE
                or _norm_sql(row["sql"]) != _norm_sql(ddl)):
            _schema_mismatch(
                f"team flow receipts schema mismatch for {name}")
    autoindexes: set[str] = set()
    for row in rows:
        name, kind, owner = row["name"], row["type"], row["tbl_name"]
        if kind == "table":
            if name not in expected_tables:
                _schema_mismatch(
                    f"team flow receipts schema has unexpected table {name!r}")
        elif kind == "trigger":
            if name not in expected_triggers or owner != TABLE:
                _schema_mismatch(
                    f"team flow receipts schema has unexpected trigger "
                    f"{name!r}")
        elif kind == "index":
            if (owner == TABLE and name in _EXPECTED_AUTOINDEXES
                    and row["sql"] is None):
                autoindexes.add(name)
            else:
                _schema_mismatch(
                    f"team flow receipts schema has unexpected index "
                    f"{name!r}")
        else:
            _schema_mismatch(
                f"team flow receipts schema has unexpected {kind} {name!r}")
    if autoindexes != _EXPECTED_AUTOINDEXES:
        _schema_mismatch(
            "team flow receipts automatic index layout does not match the "
            "schema")
    marker = _execute(
        conn,
        "SELECT singleton,schema_version FROM main." + META_TABLE,
        api="team flow receipts marker", fetch="all",
    )
    if [tuple(row) for row in marker] != [(1, SCHEMA_VERSION)]:
        _schema_mismatch(
            "team flow receipts marker is missing or inconsistent")


def _verify_prepared(conn: sqlite3.Connection) -> None:
    """prepare 自管连接内的完整复核（PRAGMA 仅允许在此路径）。"""
    _require_prepared(conn)
    info = conn.execute(f"PRAGMA main.table_info({TABLE})").fetchall()
    actual = [
        (row["name"], (row["type"] or "").upper(), int(row["notnull"]),
         int(row["pk"]))
        for row in info
    ]
    if actual != list(_EXPECTED_TABLE_INFO):
        _schema_mismatch(
            "team flow receipts table columns do not match the schema")
    indexes = conn.execute(
        f"PRAGMA main.index_list({TABLE})").fetchall()
    if len(indexes) != len(_EXPECTED_AUTOINDEXES):
        _schema_mismatch(
            "team flow receipts automatic index count does not match the "
            "schema")
    if {row["name"] for row in indexes} != _EXPECTED_AUTOINDEXES:
        _schema_mismatch(
            "team flow receipts automatic index names do not match the "
            "schema")
    pk_indexes = [row for row in indexes if row["origin"] == "pk"]
    unique_indexes = [row for row in indexes if row["origin"] == "u"]
    if len(pk_indexes) != 1 or len(unique_indexes) != 1:
        _schema_mismatch(
            "team flow receipts primary/unique index layout does not match "
            "the schema")
    pk_columns = [
        row["name"]
        for row in conn.execute(
            f"PRAGMA main.index_info({pk_indexes[0]['name']})").fetchall()
    ]
    if pk_columns != ["receipt_id"]:
        _schema_mismatch(
            "team flow receipts primary key index is not (receipt_id)")
    unique_columns = [
        row["name"]
        for row in conn.execute(
            f"PRAGMA main.index_info({unique_indexes[0]['name']})").fetchall()
    ]
    if unique_columns != list(_SCOPE_COLUMNS):
        _schema_mismatch(
            "team flow receipts scoped unique index is not "
            "(task_instance_id, actor_user_id, operation, request_id)")


# ---------------------------------------------------------------------------
# 记录重构与读写原语
# ---------------------------------------------------------------------------

def _record_from_row(row: sqlite3.Row) -> dict[str, Any]:
    """行 → 内部完整视图；严格核存储自洽（canonical/hash/ID/scope/origin）。

    任一不一致＝corrupt（fail-closed），不返回伪造记录。
    """
    try:
        raw = {name: row[name] for name in _RECORD_COLUMNS}
    except (IndexError, KeyError, TypeError) as exc:
        raise TeamFlowReceiptUnavailable(
            "corrupt", "stored receipt row is missing columns") from exc
    for name in ("receipt_id", "task_instance_id", "operation", "request_id",
                 "payload_json", "payload_sha256", "origin"):
        if not isinstance(raw[name], str):
            _corrupt(f"stored {name} must be text")
    actor_user_id = raw["actor_user_id"]
    if (not isinstance(actor_user_id, int) or isinstance(actor_user_id, bool)
            or not 1 <= actor_user_id <= _MAX_INT64):
        _corrupt("stored actor_user_id must be an in-range integer")
    archived_at = raw["archived_at"]
    if archived_at is not None and not isinstance(archived_at, str):
        _corrupt("stored archived_at must be text or null")

    try:
        payload = _validate_payload(_loads_strict(raw["payload_json"]))
        payload_json = _canonical_json(payload)
    except ValueError as exc:
        raise TeamFlowReceiptUnavailable(
            "corrupt", "stored receipt payload is invalid") from exc
    if payload_json != raw["payload_json"]:
        _corrupt("stored payload_json is not canonical")
    if raw["payload_sha256"] != _sha256_text(payload_json):
        _corrupt("stored payload_sha256 does not match payload_json")
    scope = _scope_of(payload)
    if raw["receipt_id"] != _receipt_id(*scope):
        _corrupt("stored receipt_id does not match its scope fields")
    if (raw["task_instance_id"], raw["actor_user_id"], raw["operation"],
            raw["request_id"]) != scope:
        _corrupt("stored scope columns do not match the payload")

    origin = raw["origin"]
    if origin not in ORIGINS:
        _corrupt("stored origin is not supported")
    derived = _origin_of_payload(payload)
    if origin == ORIGIN_ARCHIVE:
        # 归档行只能来自 actor 原产 receipt 的字节保真拷贝。
        if derived != ORIGIN_ACTOR:
            _corrupt("archived receipt must originate from an actor binding")
        if archived_at is None or not _valid_timestamp(archived_at):
            _corrupt("origin 'actor_archive' requires a valid archived_at")
    else:
        if origin != derived:
            _corrupt("stored origin does not match the payload source binding")
        if archived_at is not None:
            _corrupt(f"origin {origin!r} must not carry archived_at")
    if not _valid_timestamp(payload["recorded_at"]):
        _corrupt("stored recorded_at is not a valid UTC timestamp")
    return {
        "payload": payload,
        "payload_json": payload_json,
        "receipt_id": raw["receipt_id"],
        "payload_sha256": raw["payload_sha256"],
        "origin": origin,
        "archived_at": archived_at,
        "scope": scope,
    }


def _public_record(stored: Mapping[str, Any]) -> dict[str, Any]:
    """对外记录形状（C09）：payload 精确字段 + receipt_id + payload_sha256。"""
    record = dict(stored["payload"])
    record["receipt_id"] = stored["receipt_id"]
    record["payload_sha256"] = stored["payload_sha256"]
    return record


def _select_by_scope(conn: sqlite3.Connection,
                     scope: tuple[str, int, str, str], *,
                     api: str = "team flow receipts read") -> sqlite3.Row | None:
    return _execute(
        conn,
        _SELECT_RECORD + " WHERE task_instance_id=? AND actor_user_id=? "
        "AND operation=? AND request_id=?",
        scope, api=api, fetch="one",
    )


def _select_by_receipt_id(conn: sqlite3.Connection, receipt_id: str, *,
                          api: str = "team flow receipts read") \
        -> sqlite3.Row | None:
    return _execute(
        conn, _SELECT_RECORD + " WHERE receipt_id=?", (receipt_id,),
        api=api, fetch="one",
    )


def _insert_receipt(conn: sqlite3.Connection, *, receipt_id: str,
                    scope: tuple[str, int, str, str], payload_json: str,
                    payload_sha256: str, origin: str,
                    archived_at: str | None) -> None:
    _execute_write(
        conn,
        "INSERT INTO main." + TABLE + " ("
        "receipt_id,task_instance_id,actor_user_id,operation,request_id,"
        "payload_json,payload_sha256,origin,archived_at"
        ") VALUES (?,?,?,?,?,?,?,?,?)",
        (receipt_id, scope[0], scope[1], scope[2], scope[3], payload_json,
         payload_sha256, origin, archived_at),
        api="team flow receipts insert",
    )


# ---------------------------------------------------------------------------
# 公共 API（C09 接口精确为以下四个函数）
# ---------------------------------------------------------------------------

def prepare_team_flow_receipts(db_path: str | Path) -> None:
    """在现存库文件上创建/复核 team_flow_receipts 精确布局后关闭连接。

    唯一自管连接入口：mode=rw 自开（绝不创建缺失文件）、单 BEGIN IMMEDIATE、
    空则建两表三触发器（含 INSERT 守卫）+marker、既装则完整复核，COMMIT
    后关闭；半装/异布局不修补（schema_mismatch）。成功 commit 后清理失败
    如实标记已提交（``.committed = True``）。未结事务回滚属自管连接自己的
    清理，不涉调用方。
    """
    try:
        path = Path(db_path)
    except TypeError as exc:
        raise TeamFlowReceiptUnavailable(
            "unreadable", "db_path must be a filesystem path") from exc
    try:
        exists = path.exists()
        is_file = path.is_file() if exists else False
    except OSError as exc:
        raise TeamFlowReceiptUnavailable(
            "unreadable", "database path cannot be inspected") from exc
    if not exists:
        raise FileNotFoundError(
            f"team flow receipts prepare requires an existing database "
            f"file: {path}")
    if not is_file:
        raise TeamFlowReceiptUnavailable(
            "unreadable",
            "team flow receipts prepare requires a regular database file")

    conn: sqlite3.Connection | None = None
    committed = False
    original: Exception | None = None
    cleanup: list[Exception] = []
    try:
        uri = path.resolve().as_uri() + "?mode=rw"
        conn = sqlite3.connect(uri, uri=True, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN IMMEDIATE")
        found = _objects(conn)
        if found:
            _verify_prepared(conn)
        else:
            conn.execute(TABLE_DDL)
            conn.execute(TRIGGER_NO_UPDATE_DDL)
            conn.execute(TRIGGER_NO_DELETE_DDL)
            conn.execute(TRIGGER_GUARD_INSERT_DDL)
            conn.execute(META_DDL)
            conn.execute(
                "INSERT INTO main." + META_TABLE +
                " (singleton,schema_version) VALUES (1,?)", (SCHEMA_VERSION,))
            _verify_prepared(conn)
        conn.execute("COMMIT")
        committed = True
    except Exception as exc:  # noqa: BLE001 - 主失败身份在下方保留上抛
        if isinstance(exc, sqlite3.Error) and not isinstance(
                exc, sqlite3.ProgrammingError):
            original = _storage_unavailable(exc, api="team flow receipts prepare")
            original.__cause__ = exc
        else:
            # typed 失败与普通异常保持原身份（含 native ProgrammingError），
            # 清理诊断不得覆盖其类型或 traceback。
            original = exc
    finally:
        if conn is not None:
            if not committed:
                try:
                    if conn.in_transaction:
                        conn.execute("ROLLBACK")
                except Exception as exc:  # noqa: BLE001
                    cleanup.append(exc)
            try:
                conn.close()
            except Exception as exc:  # noqa: BLE001
                cleanup.append(exc)

    if original is not None:
        original.cleanup_errors = tuple(cleanup)
        raise original

    if cleanup:
        # COMMIT 已成功：close 失败不能撤销既成事实，但必须如实可见。
        if isinstance(cleanup[0], sqlite3.ProgrammingError):
            failure: Exception = cleanup[0]
        else:
            if isinstance(cleanup[0], sqlite3.Error):
                failure = _storage_unavailable(
                    cleanup[0],
                    api="team flow receipts prepare committed, but close "
                        "failed")
            else:
                failure = TeamFlowReceiptUnavailable(
                    "unreadable",
                    "team flow receipts prepare committed, but close failed")
            failure.__cause__ = cleanup[0]
        failure.cleanup_errors = tuple(cleanup)
        failure.committed = True  # type: ignore[attr-defined]
        raise failure


def read_receipt_on(conn: sqlite3.Connection, *, task_instance_id: str,
                    actor_user_id: int, operation: str,
                    request_id: str) -> dict[str, Any] | None:
    """按四字段 scope 读单条 receipt；None 仅表示该 scope 无记录。

    借用调用方活动事务（同 snapshot 语义），fail-closed：行存在但存储不自洽
    ＝corrupt，不返回伪记录。借用合同见 :func:`_require_on_conn`。
    """
    _require_on_conn(conn, api="read_receipt_on")
    instance = _strict_text(task_instance_id, "task_instance_id")
    uid = _uid(actor_user_id, "actor_user_id")
    op = _strict_text(operation, "operation")
    req = _strict_text(request_id, "request_id")
    _require_prepared(conn)
    row = _select_by_scope(conn, (instance, uid, op, req))
    if row is None:
        return None
    return _public_record(_record_from_row(row))


def append_receipt_on(conn: sqlite3.Connection,
                      payload: Mapping[str, object]) -> dict[str, Any]:
    """追加一条 receipt（幂等）：首次成功写入并读回核对完整记录。

    同 scope 且完整 canonical payload 一致 → 返回原存储记录（recorded_at/
    结果均首次值，不重生成时间）；payload 任何差异（含 request_sha256 不同）
    → TeamFlowReceiptConflict（上游 409）。origin 由源绑定成对字段推导：
    二者 None＝collab 原产，成对在场＝actor 原产；同内容跨 origin 不算幂等
    命中。写后在同一连接读回并核对，INSERT 被抑制/丢失＝typed 失败而非假
    成功。借用合同见 :func:`_require_on_conn`。
    """
    _require_on_conn(conn, api="append_receipt_on")
    _require_prepared(conn)
    normalized = _validate_payload(payload)
    origin = _origin_of_payload(normalized)
    scope = _scope_of(normalized)
    receipt_id = _receipt_id(*scope)
    payload_json = _canonical_json(normalized)
    payload_sha256 = _sha256_text(payload_json)

    existing_row = _select_by_scope(conn, scope)
    if existing_row is not None:
        stored = _record_from_row(existing_row)
        if _same_stored(stored, payload_json=payload_json,
                        receipt_id=receipt_id):
            if stored["origin"] != origin:
                raise TeamFlowReceiptConflict(
                    "receipt already exists for this scope under a "
                    "different origin")
            return _public_record(stored)
        raise TeamFlowReceiptConflict(
            "receipt already exists for this scope with different content")

    clash = _select_by_receipt_id(conn, receipt_id)
    if clash is not None:
        _corrupt("receipt_id is already stored under a different scope")

    try:
        _insert_receipt(
            conn, receipt_id=receipt_id, scope=scope,
            payload_json=payload_json, payload_sha256=payload_sha256,
            origin=origin, archived_at=None,
        )
    except sqlite3.IntegrityError as exc:
        raced = _select_by_scope(conn, scope)
        if raced is None:
            raise _storage_unavailable(
                exc, api="team flow receipts insert") from exc
        stored = _record_from_row(raced)
        if (stored["origin"] == origin
                and _same_stored(stored, payload_json=payload_json,
                                 receipt_id=receipt_id)):
            return _public_record(stored)
        raise TeamFlowReceiptConflict(
            "receipt insert collided with different content") from exc

    stored_row = _select_by_scope(conn, scope)
    if stored_row is None:
        raise TeamFlowReceiptUnavailable(
            "unreadable",
            "receipt insert did not persist a readable row")
    stored = _record_from_row(stored_row)
    if (stored["payload_json"] != payload_json
            or stored["receipt_id"] != receipt_id
            or stored["payload_sha256"] != payload_sha256
            or stored["origin"] != origin
            or stored["archived_at"] is not None):
        raise TeamFlowReceiptUnavailable(
            "corrupt",
            "receipt insert read-back does not match the written payload")
    return _public_record(stored)


def _same_stored(stored: Mapping[str, Any], *, payload_json: str,
                 receipt_id: str) -> bool:
    """幂等命中判定：完整 canonical payload 字节一致且 receipt_id 一致。

    payload_json 一致即蕴含 request_sha256/result/internal_evidence/
    recorded_at 等全部 payload 字段一致（canonical 序列化无二次歧义）。
    """
    return (stored["payload_json"] == payload_json
            and stored["receipt_id"] == receipt_id)


def _same_archive(stored: Mapping[str, Any],
                  candidate: Mapping[str, Any]) -> bool:
    """归档幂等命中：目标行 origin='actor_archive' 且 ID/hash/payload 字节一致。"""
    if stored["origin"] != ORIGIN_ARCHIVE:
        return False
    return (
        stored["receipt_id"] == candidate["receipt_id"]
        and stored["payload_sha256"] == candidate["payload_sha256"]
        and stored["payload_json"] == candidate["payload_json"]
    )


def _same_candidate(left: Mapping[str, Any],
                    right: Mapping[str, Any]) -> bool:
    return (
        left["receipt_id"] == right["receipt_id"]
        and left["payload_sha256"] == right["payload_sha256"]
        and left["payload_json"] == right["payload_json"]
    )


def _archive_candidate(item: Any) -> tuple[tuple[str, int, str, str],
                                           dict[str, Any]]:
    """归档输入校验：精确记录键集、仅 actor 原产、源 ID/hash 与 payload 一致。"""
    if not isinstance(item, Mapping):
        raise _payload_invalid("each archived receipt must be a mapping")
    actual = set(item.keys())
    if actual != _RECORD_KEYS:
        missing = sorted(key for key in _RECORD_KEYS if key not in actual)
        unknown = sorted(repr(key) for key in actual if key not in _RECORD_KEYS)
        raise _payload_invalid(
            f"receipt record fields mismatch (missing={missing}, "
            f"unknown={unknown})")
    payload = _validate_payload(
        {key: item[key] for key in _PAYLOAD_KEYS})
    if _origin_of_payload(payload) != ORIGIN_ACTOR:
        raise _payload_invalid("only actor-origin receipts can be archived")
    scope = _scope_of(payload)
    receipt_id = _receipt_id(*scope)
    payload_json = _canonical_json(payload)
    payload_sha256 = _sha256_text(payload_json)
    if item["receipt_id"] != receipt_id:
        raise _payload_invalid("source receipt_id does not match its payload")
    if item["payload_sha256"] != payload_sha256:
        raise _payload_invalid(
            "source payload_sha256 does not match its payload")
    return scope, {
        "payload": payload,
        "payload_json": payload_json,
        "receipt_id": receipt_id,
        "payload_sha256": payload_sha256,
    }


def archive_receipts_on(conn: sqlite3.Connection,
                        receipts: Iterable[Mapping[str, object]]) \
        -> list[dict[str, Any]]:
    """把 actor 原产 receipts 字节保真归档到目标（collab）库的同一布局。

    保原 payload 字节、原 receipt_id/payload_sha256、原 recorded_at（一律
    不重生成）；archived_at 仅对真正新插入的行取本次时间，幂等命中（目标已
    有同内容 actor_archive 行）保留首次 archived_at 并返回既有记录。同批
    同 scope 不同内容、或目标已有同 scope 不同内容 → Conflict；批内同一
    receipt_id 落不同 scope → Conflict。整批同事务借用（不自行提交），
    返回与输入等长、按输入顺序的记录列表。借用合同见
    :func:`_require_on_conn`。
    """
    _require_on_conn(conn, api="archive_receipts_on")
    try:
        items = list(receipts)
    except TypeError as exc:
        raise _payload_invalid(
            "receipts must be an iterable of receipt records") from exc
    _require_prepared(conn)
    if not items:
        return []
    stamp = _utc_now()

    candidates = [_archive_candidate(item) for item in items]
    planned: dict[tuple[str, int, str, str], dict[str, Any]] = {}
    results: dict[tuple[str, int, str, str], dict[str, Any]] = {}
    planned_receipt_ids: dict[str, tuple[str, int, str, str]] = {}
    for scope, candidate in candidates:
        if scope in planned:
            if _same_candidate(planned[scope], candidate):
                continue
            raise TeamFlowReceiptConflict(
                "the same batch contains conflicting copies for one "
                "receipt scope")
        clash_scope = planned_receipt_ids.get(candidate["receipt_id"])
        if clash_scope is not None and clash_scope != scope:
            raise TeamFlowReceiptConflict(
                "the same batch holds one receipt_id under different scopes")
        existing_row = _select_by_scope(conn, scope)
        if existing_row is not None:
            stored = _record_from_row(existing_row)
            if not _same_archive(stored, candidate):
                raise TeamFlowReceiptConflict(
                    "archive target already holds different content for "
                    "this scope")
            results[scope] = stored
            continue
        clash = _select_by_receipt_id(conn, candidate["receipt_id"])
        if clash is not None:
            _corrupt(
                "archive target holds this receipt_id under a different "
                "scope")
        planned[scope] = candidate
        planned_receipt_ids[candidate["receipt_id"]] = scope
        results[scope] = {
            "payload": candidate["payload"],
            "payload_json": candidate["payload_json"],
            "receipt_id": candidate["receipt_id"],
            "payload_sha256": candidate["payload_sha256"],
            "origin": ORIGIN_ARCHIVE,
            "archived_at": stamp,
            "scope": scope,
        }

    for scope, candidate in planned.items():
        try:
            _insert_receipt(
                conn, receipt_id=candidate["receipt_id"], scope=scope,
                payload_json=candidate["payload_json"],
                payload_sha256=candidate["payload_sha256"],
                origin=ORIGIN_ARCHIVE, archived_at=stamp,
            )
        except sqlite3.IntegrityError as exc:
            raced = _select_by_scope(conn, scope)
            if raced is None:
                raise _storage_unavailable(
                    exc, api="team flow receipts archive insert") from exc
            stored = _record_from_row(raced)
            if not _same_archive(stored, candidate):
                raise TeamFlowReceiptConflict(
                    "archive insert collided with different content") from exc
            # 已存在的等价拷贝保留其原 archived_at；仅新 INSERT 才要求读回
            # 本次 stamp。
            results[scope] = stored
            continue

        stored_row = _select_by_scope(conn, scope)
        if stored_row is None:
            raise TeamFlowReceiptUnavailable(
                "unreadable",
                "archive insert did not persist a readable row")
        stored = _record_from_row(stored_row)
        if not _same_archive(stored, candidate) \
                or stored["archived_at"] != stamp:
            raise TeamFlowReceiptUnavailable(
                "corrupt",
                "archive insert read-back does not match the copied receipt")
        results[scope] = stored

    return [_public_record(results[scope]) for scope, _candidate in candidates]
