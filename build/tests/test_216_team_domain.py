"""P00 keyed-domain contract tests (2.1.6 team work packages, P00).

Test-only file.  It is authored statically: the author does not import the
product, execute SQL, or run any test.  Execution is left to an independent
runner after re-impact and review.

Scope: the P00 common type module (``coscreen.team_flow_types``) and the
keyed owner-domain module (``custom_backend.team_keyed_domain``) --
``normalize_item_keys``, ``canonical_item_rows_on`` and
``iter_canonical_item_keys_on`` -- against C01/C02/C07, the P00 work package
and the NULL-first pagination ruling.

Authoritative expectation sources (must be read together):

- docs/superpowers/plans/2026-11-16-2.1.6-team-work-packages.md (P00)
- docs/superpowers/plans/2026-11-16-2.1.6-team-contracts.md (C01/C02/C07)
- out/maintenance216/dag_exec_20261116/P00/P00_NULL_ORDER_RULING_TERRA.md
- out/maintenance216/dag_exec_20261116/P00/P00_NULL_ORDER_ADDENDUM_LUNA.md
- out/maintenance216/dag_exec_20261116/P00/P00_TEST_PLAN_EGG.md (+ P1-P4 rulings)

Frozen contract points (test author EGG, coordinator rulings 2026-11-17):

- ``normalize_item_keys`` rejections and paging-shape rejections are
  ValueError-compatible (C02: call/argument errors must not masquerade as
  data unavailability).
- Non-native connections raise TypeError; native connections without an
  active caller transaction or without ``row_factory is sqlite3.Row`` raise
  RuntimeError; closed native connections propagate ``sqlite3.ProgrammingError``
  unchanged.
- Owner data/structure failures raise ``OwnerKeyedDomainUnavailableError``
  (reason ``owner_db_unreadable`` semantics): missing/paired tables or
  required columns, storage classes that cannot be expressed by the
  pagination contract, unreadable keys.
- ``canonical_item_rows_on`` omits non-canonical requested keys (duplicate /
  title-pending / absent) without fabricating rows; the caller can discover
  the missing set by diffing against ``normalize_item_keys``.  Empty input is
  a ValueError (internal normalize).
- ``iter_canonical_item_keys_on`` owns the ordering contract: SQLite
  ``import_order ASC, zotero_key ASC`` with NULLs first, negative integers
  legal, ties broken by key.  Cursor is the last actually returned row of the
  page; the limit+1 sentinel never leaks into a cursor; a page without a
  sentinel ends the enumeration with cursor None.  Cursors are pure keyset
  bounds (no existence check).  ``limit`` is a non-bool integer 1..200.
- The owner-domain queries touch only ``main`` tables: no business DML, no
  vote-table reads (authorizer proof), no transaction control, cursors they
  create are fully materialized and deterministically closed.  Read-only
  ``PRAGMA main.table_info``/``table_xinfo`` metadata reads are allowed;
  setter PRAGMAs are not.
- Cursor double-fault (C02 "do not swallow main or cleanup errors"): fetch
  and close faults must both remain observable, while the same real cursor
  must actually be closed.  Exception identity, a particular cleanup field,
  and a top-level exception type are not required.  A successful fetch
  followed by a close fault must not return a fake success.  C02 separately
  requires a closed connection's ``sqlite3.ProgrammingError`` to pass through.

No private implementation is imported and no private helper is called
directly.  Every test class/method/helper symbol in this file has been run
through GitNexus ``impact --direction upstream`` before writing (raws in
out/maintenance216/dag_exec_20261116/P00/impact_test/).
"""

from __future__ import annotations

import dataclasses
import importlib
import json
import re
import sqlite3
import subprocess
import sys
import tempfile
import typing
import unittest
from collections.abc import Mapping
from contextlib import closing, contextmanager
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# 票面/账本/工作流表：owner 域读取绝不触碰（authorizer 拒读清单）。
_KD_VOTE_TABLES = (
    "decisions",
    "stage2_decisions",
    "team_vote_revisions",
    "team_vote_revision_meta",
    "rankings",
    "workflow_state",
    "title_duplicate_undo",
)

# canonical 域依赖的三张 main 表（trace main. 限定断言 / authorizer 允许面）。
_KD_MAIN_TABLES = (
    "articles",
    "title_duplicate_members",
    "title_duplicate_reviews",
)

# P00 模块禁止反向拉起的 runtime / write-check / 导入校验模块前缀。
_KD_FORBIDDEN_IMPORT_PREFIXES = (
    "custom_backend.team_screening_runtime",
    "coscreen.team_screening_runtime",
    "custom_backend.team_screening_write",
    "custom_backend.team_write_checks",
    "coscreen.team_vote_ledger",
    "coscreen.review_workflow",
    "coscreen.task_collaboration",
    "coscreen.screening_arbitration",
    "coscreen.arbitration_session",
    "custom_backend.main",
    "custom_backend.review_workflow_api",
)

# C02/C07 共同 DTO 的精确字段序（含唯一允许默认值的 expected_version）。
_KD_DTO_FIELDS = {
    "TeamOperation": ("name", "stage", "expected_version"),
    "KeyIssue": ("reason", "stage", "key", "source_role", "retryable"),
    "TaskBinding": (
        "task_id", "task_instance_id", "owner_user_id", "owner_library_id",
    ),
    "CompletionProjection": (
        "seal_id", "kind", "trigger", "sealed_at", "final_decision",
        "persisted",
    ),
    "AffectedCell": ("stage", "item_key"),
    "MutationFootprint": ("cells", "complete", "reason"),
    "SealWitness": (
        "stage", "item_key", "source_path", "registration_id", "generation",
        "library_id", "seal_id", "content_sha256", "payload_sha256",
        "sealed_at",
    ),
    "KeyedReadPlan": (
        "binding", "operation", "item_keys", "witnesses",
        "live_dependencies", "issues", "selected_paths",
    ),
}

# 旧 owner 域 SELECT 文本提示；不作为故障注入目标或产品合同。
_KD_DOMAIN_SELECT_RE = re.compile(r"FROM\s+main\.articles\b", re.IGNORECASE)

# FROM/JOIN 直接引用域表而不带 main. 限定（禁止形状）。
_KD_UNQUALIFIED_DOMAIN_RE = re.compile(
    r"(FROM|JOIN)\s+(articles|title_duplicate_members|title_duplicate_reviews)\b",
    re.IGNORECASE,
)


class _kd_ProbeCursor:
    """Cursor 代理：登记 SQL/fetch/close，可注入一次性 close 故障。

    close 故障按同一 cursor 的 fetch 错误或预先观察的唯一执行事件选中；
    真实 close 先执行、注入故障后抛，一次性后自动清除。fetch 错误
    原样透传并登记，供双故障可观察性断言；选择失败必须标记待审。
    """

    close_fault = None
    close_fault_mode = None
    close_fault_target = None
    event_serial = 0

    def __init__(self, cursor, sql, registry=None):
        self._cursor = cursor
        self.sql = sql or ""
        self.parameters = ()
        self._registry = registry
        self.executions = []
        self.rows_returned = 0
        self.fetchall_called = False
        self.iteration_exhausted = False
        self.saw_empty = False
        self.closed = False
        self.close_attempts = 0
        self.close_fault_fired = False
        self.fetch_errors = []
        if registry is not None:
            registry.append(self)

    def execute(self, sql, parameters=()):
        self.sql = sql
        self.parameters = parameters
        self._cursor.execute(sql, parameters)
        type(self).event_serial += 1
        self.executions.append({
            "seq": type(self).event_serial, "sql": sql,
            "parameters": dict(parameters) if isinstance(parameters, dict) else tuple(parameters),
            "columns": tuple(col[0] for col in (self._cursor.description or ())),
            "rows": 0, "keys": [], "complete": False,
        })
        self.fetchall_called = self.iteration_exhausted = self.saw_empty = False
        return self

    def fetchone(self):
        try:
            row = self._cursor.fetchone()
        except sqlite3.Error as exc:
            self.fetch_errors.append(exc)
            raise
        if row is None:
            self.saw_empty = True
            if self.executions:
                self.executions[-1]["complete"] = True
        else:
            self.rows_returned += 1
            if self.executions:
                event = self.executions[-1]
                event["rows"] += 1
                if isinstance(row, sqlite3.Row) and "zotero_key" in row.keys():
                    event["keys"].append(row["zotero_key"])
        return row

    def fetchall(self):
        try:
            rows = self._cursor.fetchall()
        except sqlite3.Error as exc:
            self.fetch_errors.append(exc)
            raise
        self.fetchall_called = True
        self.rows_returned += len(rows)
        if self.executions:
            event = self.executions[-1]
            event["rows"] += len(rows)
            event["complete"] = True
            event["keys"].extend(row["zotero_key"] for row in rows
                                 if isinstance(row, sqlite3.Row)
                                 and "zotero_key" in row.keys())
        return rows

    def fetchmany(self, *args):
        try:
            rows = self._cursor.fetchmany(*args)
        except sqlite3.Error as exc:
            self.fetch_errors.append(exc)
            raise
        self.rows_returned += len(rows)
        if self.executions:
            event = self.executions[-1]
            event["rows"] += len(rows)
            event["keys"].extend(row["zotero_key"] for row in rows
                                 if isinstance(row, sqlite3.Row)
                                 and "zotero_key" in row.keys())
            if not rows:
                event["complete"] = True
        if not rows:
            self.saw_empty = True
        return rows

    def __iter__(self):
        return self

    def __next__(self):
        try:
            row = next(self._cursor)
        except StopIteration:
            self.iteration_exhausted = True
            if self.executions:
                self.executions[-1]["complete"] = True
            raise
        except sqlite3.Error as exc:
            self.fetch_errors.append(exc)
            raise
        self.rows_returned += 1
        if self.executions:
            event = self.executions[-1]
            event["rows"] += 1
            if isinstance(row, sqlite3.Row) and "zotero_key" in row.keys():
                event["keys"].append(row["zotero_key"])
        return row

    def close(self):
        self.close_attempts += 1
        fault = None
        selector_matches = False
        if type(self).close_fault_mode == "fetch_error":
            selector_matches = bool(self.fetch_errors)
        elif (type(self).close_fault_mode == "observed"
              and self._registry is not None and self.executions):
            event = self.executions[-1]
            signature = (self._registry.index(self), event["sql"], event["parameters"],
                         event["columns"], event["rows"], tuple(event["keys"]),
                         event["complete"])
            selector_matches = signature == type(self).close_fault_target
        if type(self).close_fault is not None and selector_matches:
            fault = type(self).close_fault
            type(self).close_fault = None
        if self.closed:
            if fault is not None:
                self.close_fault_fired = True
                raise fault
            return
        self._cursor.close()
        self.closed = True
        if fault is not None:
            self.close_fault_fired = True
            raise fault

    def fully_fetched(self):
        return self.fetchall_called or self.iteration_exhausted or self.saw_empty

    @property
    def rowcount(self):
        return self._cursor.rowcount

    @property
    def description(self):
        return self._cursor.description

    @property
    def lastrowid(self):
        return self._cursor.lastrowid

    def __getattr__(self, name):
        return getattr(self._cursor, name)


class _kd_ProbeConnection(sqlite3.Connection):
    """Native 子类：真实连接 + 语句/cursor 登记 + commit/rollback/close 计数。

    事务控制与 close 计数供“借用 API 不控制 caller txn”断言；语句登记供
    trace 分类复核（_kd_trace 挂接期间 probe 自身登记暂停）。
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.statements = []
        self.cursors = []
        self.statement_offset = 0
        self.cursor_offset = 0
        self.commit_calls = 0
        self.rollback_calls = 0
        self.close_calls = 0
        self.executescript_calls = 0
        self._kd_closed = False
        self.set_trace_callback(self.statements.append)

    def execute(self, sql, parameters=()):
        raw = super().execute(sql, parameters)
        cursor = _kd_ProbeCursor(raw, sql, self.cursors)
        cursor.parameters = parameters
        _kd_ProbeCursor.event_serial += 1
        cursor.executions.append({
            "seq": _kd_ProbeCursor.event_serial, "sql": sql,
            "parameters": dict(parameters) if isinstance(parameters, dict) else tuple(parameters),
            "columns": tuple(col[0] for col in (raw.description or ())),
            "rows": 0, "keys": [], "complete": False,
        })
        return cursor

    def executemany(self, sql, seq_of_parameters):
        raw = super().executemany(sql, seq_of_parameters)
        return _kd_ProbeCursor(raw, sql, self.cursors)

    def cursor(self, factory=None):
        raw = super().cursor(factory) if factory is not None else super().cursor()
        return _kd_ProbeCursor(raw, "", self.cursors)

    def commit(self):
        self.commit_calls += 1
        return super().commit()

    def rollback(self):
        self.rollback_calls += 1
        return super().rollback()

    def close(self):
        if self._kd_closed:
            return
        super().close()
        self._kd_closed = True
        self.close_calls += 1


class _kd_DelegatingConnection:
    """非 native 伪装代理：转发 row_factory/in_transaction 等属性。

    C02 要求以真实 type 继承判断，``__class__`` 伪装必须被 TypeError 拒绝。
    """

    def __init__(self, conn):
        self._conn = conn

    @property
    def __class__(self):
        return sqlite3.Connection

    def __getattr__(self, name):
        return getattr(self._conn, name)


@contextmanager
def _kd_trace(conn):
    """在 probe 连接上捕获窗口，退出后恢复其常规语句登记。"""
    statements = []
    conn.set_trace_callback(statements.append)
    try:
        yield statements
    finally:
        conn.set_trace_callback(conn.statements.append)


@contextmanager
def _kd_deny_writes_and_vote_reads(conn):
    """真实 authorizer：拒写/DDL/ATTACH/DETACH/设置性 PRAGMA/跨库读/票表读。

    只放行 main 库读取与 table_info/table_xinfo 只读元数据（主控 2026-11-17
    裁定：C02 禁止更改 PRAGMA 状态，不禁止只读元数据查询）。yield 回调本
    身供正控复用。
    """

    def callback(action, arg1, arg2, dbname, trigger):
        if action == sqlite3.SQLITE_PRAGMA:
            if arg1 in ("table_info", "table_xinfo"):
                return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_DENY
        if action in (
            sqlite3.SQLITE_INSERT,
            sqlite3.SQLITE_UPDATE,
            sqlite3.SQLITE_DELETE,
            sqlite3.SQLITE_CREATE_TABLE, sqlite3.SQLITE_CREATE_INDEX,
            sqlite3.SQLITE_CREATE_VIEW, sqlite3.SQLITE_CREATE_TRIGGER,
            sqlite3.SQLITE_CREATE_TEMP_TABLE, sqlite3.SQLITE_CREATE_TEMP_INDEX,
            sqlite3.SQLITE_CREATE_TEMP_VIEW, sqlite3.SQLITE_CREATE_TEMP_TRIGGER,
            sqlite3.SQLITE_CREATE_VTABLE,
            sqlite3.SQLITE_DROP_TABLE, sqlite3.SQLITE_DROP_INDEX,
            sqlite3.SQLITE_DROP_VIEW, sqlite3.SQLITE_DROP_TRIGGER,
            sqlite3.SQLITE_DROP_TEMP_TABLE, sqlite3.SQLITE_DROP_TEMP_INDEX,
            sqlite3.SQLITE_DROP_TEMP_VIEW, sqlite3.SQLITE_DROP_TEMP_TRIGGER,
            sqlite3.SQLITE_DROP_VTABLE,
            sqlite3.SQLITE_ALTER_TABLE, sqlite3.SQLITE_REINDEX,
            sqlite3.SQLITE_ANALYZE, sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH,
        ):
            return sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_READ:
            if dbname not in ("main", ""):
                return sqlite3.SQLITE_DENY
            if arg1 in _KD_VOTE_TABLES:
                return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    conn.set_authorizer(callback)
    try:
        yield callback
    finally:
        conn.set_authorizer(None)


def _kd_first_keyword(sql_text):
    """剥前导 ``--`` 注释行后取首关键字/记号（先例 _s2mrw_classify_trace）。"""
    text = sql_text.strip()
    while text.startswith("--"):
        remainder = text.split("\n", 1)
        text = remainder[1].strip() if len(remainder) > 1 else ""
    if not text:
        return ""
    return re.split(r"[\s(]", text, 1)[0].upper()


def _kd_classify_statements(statements):
    """按首真实关键字分类；PRAGMA 单列并区分只读元数据与设置性 PRAGMA。"""
    buckets = {
        "select": [],
        "metadata_pragma": [],
        "other_pragma": [],
        "dml": [],
        "ddl": [],
        "txn": [],
        "other": [],
    }
    dml_keywords = {"INSERT", "UPDATE", "DELETE", "REPLACE", "MERGE"}
    ddl_keywords = {"CREATE", "DROP", "ALTER", "VACUUM", "REINDEX", "ANALYZE"}
    txn_keywords = {"BEGIN", "COMMIT", "ROLLBACK", "SAVEPOINT", "RELEASE",
                    "END"}
    for statement in statements:
        keyword = _kd_first_keyword(statement)
        if keyword in dml_keywords:
            buckets["dml"].append(statement)
        elif keyword in ddl_keywords:
            buckets["ddl"].append(statement)
        elif keyword in txn_keywords:
            buckets["txn"].append(statement)
        elif keyword == "PRAGMA":
            match = re.match(r"PRAGMA\s+(?:main\.)?(\w+)", statement,
                             re.IGNORECASE)
            pragma_name = match.group(1).lower() if match else ""
            if (pragma_name in ("table_info", "table_xinfo")
                    and "=" not in statement):
                buckets["metadata_pragma"].append(statement)
            else:
                buckets["other_pragma"].append(statement)
        elif keyword in ("SELECT", "WITH"):
            buckets["select"].append(statement)
        else:
            buckets["other"].append(statement)
    return buckets


def _kd_analyze_rows_access(cursors, trace, requested, *, iterator=False):
    """证明已审窄形态；其他语句或观测缺口一律待审，不是产品失败。"""
    events = sorted((event for cursor in cursors for event in cursor.executions),
                    key=lambda event: event["seq"])
    seen = set()
    expanded = []
    data_events = 0
    if not events:
        return "EVIDENCE_PENDING_AUDIT", "no recorded executions", seen
    for event in events:
        sql, parameters = event["sql"], event["parameters"]
        if re.fullmatch(
                r"\s*SELECT\s+(?:\w+\.)?(?:\*|zotero_key|import_order)"
                r"(?:\s*,\s*(?:\w+\.)?(?:zotero_key|import_order))*"
                r"\s+FROM\s+main\.articles(?:\s+(?:AS\s+)?\w+)?\s*;?\s*",
                sql, re.IGNORECASE):
            if iterator:
                return "EVIDENCE_PENDING_AUDIT", f"page read needs audit: {event!r}", seen
            return "PROVEN_VIOLATION", f"unbounded article read: {sql!r}", seen
        if not isinstance(parameters, tuple):
            return "EVIDENCE_PENDING_AUDIT", f"parameter form: {event!r}", seen
        metadata = (
            sql == "SELECT name, type FROM main.sqlite_master "
                   "WHERE type = 'table' AND name IN (?, ?, ?)"
            and len(parameters) == 3 and set(parameters) == set(_KD_MAIN_TABLES)
        ) or (sql in {f"PRAGMA main.table_info('{table}')" for table in _KD_MAIN_TABLES}
              and not parameters)
        if not metadata:
            if iterator:
                template = (
                    "SELECT a.zotero_key, a.import_order FROM main.articles AS a "
                    "WHERE a.is_duplicate_of IS NULL AND NOT EXISTS ("
                    "SELECT 1 FROM main.title_duplicate_members AS m "
                    "JOIN main.title_duplicate_reviews AS r ON r.review_id = m.review_id "
                    "WHERE m.zotero_key = a.zotero_key AND m.imported_in_batch = ? "
                    "AND r.status IN (?, ?)) ORDER BY a.import_order ASC, "
                    "a.zotero_key ASC LIMIT ?"
                )
                if (sql != template or len(parameters) != 4
                        or parameters[:3] != (1, "pending", "undecided")
                        or type(parameters[3]) is not int):
                    return "EVIDENCE_PENDING_AUDIT", f"unrecognized page execution: {event!r}", seen
            else:
                count = len(parameters) - 3
                template = (
                    "SELECT a.* FROM main.articles AS a WHERE a.zotero_key IN ("
                    + ", ".join("?" for _ in range(max(count, 0)))
                    + ") AND a.is_duplicate_of IS NULL AND NOT EXISTS ("
                      "SELECT 1 FROM main.title_duplicate_members AS m "
                      "JOIN main.title_duplicate_reviews AS r ON r.review_id = m.review_id "
                      "WHERE m.zotero_key = a.zotero_key AND m.imported_in_batch = ? "
                      "AND r.status IN (?, ?))"
                )
                if sql != template or parameters[-3:] != (1, "pending", "undecided"):
                    return "EVIDENCE_PENDING_AUDIT", f"unrecognized execution: {event!r}", seen
                keys = parameters[:-3]
                if not keys or len(keys) > 200 or any(key not in requested for key in keys):
                    return "PROVEN_VIOLATION", f"article key scope or block: {event!r}", seen
                seen.update(keys)
            data_events += 1
        # 仅对完整匹配的已审模板还原 trace；不解析任意 SQL。
        parts = sql.split("?")
        if len(parts) != len(parameters) + 1:
            return "EVIDENCE_PENDING_AUDIT", f"placeholder mismatch: {event!r}", seen
        rendered = parts[0]
        for value, part in zip(parameters, parts[1:]):
            if isinstance(value, str) and "\x00" not in value:
                rendered += "'" + value.replace("'", "''") + "'"
            elif type(value) is int:
                rendered += str(value)
            else:
                return "EVIDENCE_PENDING_AUDIT", f"unsupported binding: {event!r}", seen
            rendered += part
        expanded.append(rendered)
    if expanded != trace:
        return "EVIDENCE_PENDING_AUDIT", f"trace/execution mismatch: {events!r}; {trace!r}", seen
    if not data_events or (not iterator and seen != set(requested)):
        return "EVIDENCE_PENDING_AUDIT", f"data or request coverage incomplete: {seen!r}", seen
    return "PROVEN_BOUNDED", "all observed executions match reviewed scope", seen


def _kd_exception_roots(exc):
    """收集异常图（__cause__/__context__/cleanup_errors 通道）全部根异常。

    cleanup_errors 为对象级清理错误通道（先例
    test_s2_terminal_facts.py：primary 保持主错、cleanup_errors 不改写
    cause 链）。含去重，None 跳过。
    """
    roots = []
    seen = set()
    stack = [exc]
    while stack:
        current = stack.pop()
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        stack.extend(getattr(current, "cleanup_errors", ()) or ())
        if isinstance(current, BaseExceptionGroup):
            stack.extend(current.exceptions)
        stack.append(current.__cause__)
        stack.append(current.__context__)
        roots.append(current)
    return roots


def _kd_fault_visible(roots, fault):
    """注入故障对象（或同型同文重建实例）是否可观察。"""
    return any(
        root is fault
        or (type(root) is type(fault) and root.args == fault.args)
        for root in roots
    )


class TeamDomainTestBase(unittest.TestCase):
    """公共基座：临时箱 + 延迟产品导入 + 真实 owner 库 fixture。"""

    maxDiff = None

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="rf216-kd-")
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        self.kdm = importlib.import_module("custom_backend.team_keyed_domain")
        self.tft = importlib.import_module("coscreen.team_flow_types")
        self.dbm = importlib.import_module("coscreen.db")

    # ------------------------------------------------------------------
    # Fixtures（真实 init_db schema + 直插种子；先例 _screener_db）
    # ------------------------------------------------------------------

    def _kd_make_owner_db(self, path, *, articles=(), dups=(),
                          title_reviews=(), votes=(), corrupt_rows=()):
        """真实 dbm.init_db（含账本安装）+ 直插种子。

        articles: (key, import_order) —— import_order 可为 None/负数
        dups: (key, keeper, import_order)
        title_reviews: {review_id, status, members=[(key, batch)], keeper_key}
        votes: [(key, decision)] —— decisions 真实 INSERT（触发真实 trigger）
        corrupt_rows: [(sql, params)] —— 测试内静态字面量（BLOB/REAL/TEXT
        storage class、无效 UTF-8 文本等结构反例行），非产品输入。
        """
        self.dbm.init_db(path)
        conn = sqlite3.connect(str(path))
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            for key, order in articles:
                conn.execute(
                    "INSERT INTO articles (zotero_key, title,"
                    " is_duplicate_of, import_order) VALUES (?, ?, NULL, ?)",
                    (key, f"title-{key}", order))
            for key, keeper, order in dups:
                conn.execute(
                    "INSERT INTO articles (zotero_key, title,"
                    " is_duplicate_of, import_order) VALUES (?, ?, ?, ?)",
                    (key, f"title-{key}", keeper, order))
            for review in title_reviews:
                members_json = json.dumps([
                    {"zotero_key": key, "imported_in_batch": bool(batch)}
                    for key, batch in review["members"]
                ], ensure_ascii=False)
                conn.execute(
                    "INSERT INTO title_duplicate_reviews (review_id,"
                    " normalized_title, status, members_json, keeper_key,"
                    " decision_by, created_at, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, '',"
                    " '2026-11-15T00:00:00+00:00',"
                    " '2026-11-15T00:00:00+00:00')",
                    (review["review_id"], "norm-" + review["review_id"],
                     review["status"], members_json, review.get("keeper_key", "")))
                for key, batch in review["members"]:
                    conn.execute(
                        "INSERT INTO title_duplicate_members (review_id,"
                        " zotero_key, imported_in_batch) VALUES (?, ?, ?)",
                        (review["review_id"], key, batch))
            for key, decision in votes:
                conn.execute(
                    "INSERT INTO decisions (zotero_key, decision,"
                    " exclusion_reason, notes, tags, screened_at, updated_at)"
                    " VALUES (?, ?, '', '', '',"
                    " '2026-11-15T00:00:00+00:00',"
                    " '2026-11-15T00:00:00+00:00')", (key, decision))
            for statement, params in corrupt_rows:
                conn.execute(statement, params)
            conn.commit()
        finally:
            conn.close()
        return path

    def _kd_open_borrowed(self, path, *, begin=True):
        """Probe 工厂打开借用连接（Row factory + 活动事务由 caller 开启）。"""
        conn = sqlite3.connect(str(path), timeout=15,
                               factory=_kd_ProbeConnection)
        self._kd_register_probe(conn)
        conn.row_factory = sqlite3.Row
        if begin:
            with closing(conn.execute("BEGIN IMMEDIATE")):
                pass
        return conn

    def _kd_register_probe(self, probe):
        self.addCleanup(self._kd_close_probe, probe)

    def _kd_close_probe(self, probe):
        if getattr(probe, "_kd_closed", False):
            return
        for cursor in list(getattr(probe, "cursors", ())):
            cursor.close()
        probe.close()

    def _kd_reset_probe(self, probe):
        """偏移复位：仅观察此后的语句/cursor/计数。"""
        probe.statement_offset = len(probe.statements)
        probe.cursor_offset = len(probe.cursors)
        probe.commit_calls = 0
        probe.rollback_calls = 0
        probe.close_calls = 0
        probe.executescript_calls = 0

    def _kd_variant_db(self, name, *, seed=None, drop_tables=(),
                       drop_columns=()):
        """结构反例库：真实 init_db + 种子 + 裸连接（FK OFF）DROP/重建。"""
        path = self.base / f"variant-{name}.db"
        self._kd_make_owner_db(path, **(seed or {}))
        conn = sqlite3.connect(str(path))
        conn.row_factory = sqlite3.Row
        try:
            # 仅结构破坏反例关闭 FK；正常种子在 FK ON 下建立。
            conn.execute("PRAGMA foreign_keys=OFF")
            for table in drop_tables:
                conn.execute(f"DROP TABLE main.{table}")
            for table, column in drop_columns:
                self._kd_drop_column_compat(conn, table, column)
            conn.commit()
        finally:
            conn.close()
        return path

    def _kd_drop_column_compat(self, conn, table, column):
        """版本无关去列重建（不假定 ALTER TABLE DROP COLUMN / SQLite 3.35+）。

        依 PRAGMA main.table_info 数据驱动重建：保留列名/类型/NOT NULL 与
        主键布局，建影子表→拷行→DROP 原表→RENAME。仅用于测试结构反例。
        """
        info = list(conn.execute(f"PRAGMA main.table_info('{table}')"))
        kept = [row for row in info if row["name"] != column]
        if len(kept) != len(info) - 1:
            raise AssertionError(f"column {column!r} missing in {table!r}")
        column_defs = []
        for row in kept:
            piece = f"{row['name']} {row['type'] or 'TEXT'}"
            if row["notnull"]:
                piece += " NOT NULL"
            column_defs.append(piece)
        primary_key = [
            row["name"] for row in sorted(
                (item for item in kept if item["pk"]), key=lambda r: r["pk"])
        ]
        if primary_key:
            column_defs.append(
                "PRIMARY KEY (" + ", ".join(primary_key) + ")")
        kept_names = ", ".join(row["name"] for row in kept)
        conn.execute(
            f"CREATE TABLE main.{table}_kdrebuild ({', '.join(column_defs)})")
        conn.execute(
            f"INSERT INTO main.{table}_kdrebuild ({kept_names}) "
            f"SELECT {kept_names} FROM main.{table}")
        conn.execute(f"DROP TABLE main.{table}")
        conn.execute(f"ALTER TABLE main.{table}_kdrebuild RENAME TO {table}")

    @staticmethod
    def _kd_article_columns(path):
        """现读真实 articles 列序（字段断言以实际 schema 为准）。"""
        with closing(sqlite3.connect(str(path))) as conn:
            return tuple(
                row[1] for row in conn.execute(
                    "PRAGMA main.table_info('articles')"))

    # ------------------------------------------------------------------
    # 共享种子
    # ------------------------------------------------------------------

    def _kd_main_seed(self):
        """§2.1 主 fixture：A1..A5 + 元字符键 + 已决/批次反例 + dup/pending。

        canonical 全序期望（iter 合同）：
        ('A1','A2','A3','A4','A5','K\\'1','K--1','K_x','K%1',
         'DONE1','DONE2','B0')
        """
        return dict(
            articles=[
                ("A1", 0), ("A2", 1), ("A3", 2), ("A4", 3), ("A5", 4),
                ("K'1", 10), ("K--1", 11), ("K_x", 12), ("K%1", 13),
                ("TP1", 80), ("TP2", 81), ("DONE1", 82), ("DONE2", 83),
                ("B0", 84), ("TP3", 85),
            ],
            dups=[("DK1", "A1", 90), ("DK2", "A2", 91), ("DUPDASH", "", 92)],
            title_reviews=[
                {"review_id": "rev-TP1", "status": "pending",
                 "members": [("TP1", 1)]},
                {"review_id": "rev-TP2", "status": "undecided",
                 "members": [("TP2", 1)]},
                {"review_id": "rev-DONE1", "status": "duplicate",
                 "keeper_key": "A1", "members": [("A1", 1), ("DONE1", 1)]},
                {"review_id": "rev-DONE2", "status": "not_duplicate",
                 "members": [("DONE2", 1)]},
                {"review_id": "rev-B0", "status": "pending",
                 "members": [("B0", 0)]},
                {"review_id": "rev-TP3A", "status": "pending",
                 "members": [("TP3", 1)]},
                {"review_id": "rev-TP3B", "status": "duplicate",
                 "members": [("TP3", 1)]},
            ],
            votes=[("A1", "exclude")],
        )

    def _kd_expected_canonical_keys(self):
        return (
            "A1", "A2", "A3", "A4", "A5",
            "K'1", "K--1", "K_x", "K%1",
            "DONE1", "DONE2", "B0",
        )


class NormalizeItemKeysContractTests(TeamDomainTestBase):
    """normalize_item_keys：去重保序 / 空/NUL/非法 Unicode 拒绝。"""

    def test_key_order_and_empty_rejection(self):
        normalize = self.kdm.normalize_item_keys
        # 工作包原文样例：去重保首次出现序。
        self.assertEqual(normalize(["A", "B", "A"]), ("A", "B"))
        self.assertEqual(normalize(("B", "A")), ("B", "A"))
        self.assertEqual(normalize(["Z", "A", "M", "A", "Z"]), ("Z", "A", "M"))
        # 迭代器输入一次性消费完。
        iterator = iter(["K2", "K1", "K2"])
        self.assertEqual(normalize(iterator), ("K2", "K1"))
        with self.assertRaises(StopIteration):
            next(iterator)
        # 返回 tuple 而非 list。
        self.assertIsInstance(normalize(["A"]), tuple)
        # 空集合必须 ValueError，不得返回全域（工作包原文注释）。
        with self.assertRaises(ValueError):
            normalize([])
        with self.assertRaises(ValueError):
            normalize(tuple())
        # 归属唯一：keyed_domain 持有实现；若 types 亦暴露则必须同一对象。
        self.assertTrue(callable(normalize))
        if hasattr(self.tft, "normalize_item_keys"):
            self.assertIs(self.tft.normalize_item_keys,
                          self.kdm.normalize_item_keys)

    def test_rejects_bare_str_bytes_and_non_str_elements(self):
        normalize = self.kdm.normalize_item_keys
        for bad_input in ("AB", b"AB", bytearray(b"AB")):
            with self.subTest(bad=bad_input):
                with self.assertRaises(ValueError):
                    normalize(bad_input)
        for bad_item in (["A", None], ["A", 1], ["A", b"B"], ["A", ["B"]]):
            with self.subTest(bad=bad_item):
                with self.assertRaises(ValueError):
                    normalize(bad_item)
        # 正控：形状合法即接受（拒绝按形状精确生效）。
        self.assertEqual(normalize(["A", "B"]), ("A", "B"))

    def test_rejects_empty_nul_and_surrogate_keys(self):
        normalize = self.kdm.normalize_item_keys
        for bad_input in (
            [""],
            ["A\x00B"],
            ["\x00"],
            ["A", "B\x00"],
            ["\ud800"],
            ["A\udfff"],
        ):
            with self.subTest(bad=bad_input):
                with self.assertRaises(ValueError):
                    normalize(bad_input)
        # 正控（拒绝最小化 + 不做 Unicode 规范化）：空白/CJK/emoji/变体
        # 序列均合法，NFC 与 NFD 是两个不同 key。
        self.assertEqual(
            normalize(["A B", "文献A", "K\t1", "K\n2", "\U0001F600"]),
            ("A B", "文献A", "K\t1", "K\n2", "\U0001F600"))
        self.assertEqual(normalize(["ä", "a\u0308"]), ("ä", "a\u0308"))


class CommonTypeShapeTests(TeamDomainTestBase):
    """C02/C07 共同 DTO 精确形状 + 反向 import 禁令。"""

    def test_common_dto_shapes_exact(self):
        for name, expected_fields in _KD_DTO_FIELDS.items():
            cls = getattr(self.tft, name)
            with self.subTest(dto=name):
                self.assertTrue(dataclasses.is_dataclass(cls))
                self.assertTrue(cls.__dataclass_params__.frozen)
                actual = tuple(field.name for field in dataclasses.fields(cls))
                self.assertEqual(actual, expected_fields)
                # 仅 TeamOperation.expected_version 允许默认值（C02 原文）；
                # MutationFootprint.reason 无默认值，不假设 None。
                for field in dataclasses.fields(cls):
                    if name == "TeamOperation" and field.name == \
                            "expected_version":
                        self.assertIs(field.default, None)
                    else:
                        self.assertIs(field.default, dataclasses.MISSING)
                # frozen：任一字段赋值即 FrozenInstanceError。
                instance = cls.__new__(cls)
                with self.assertRaises(dataclasses.FrozenInstanceError):
                    setattr(instance, expected_fields[0], "kd-frozen-probe")
        # 类型按语义比较，Mapping 的 typing/collections.abc 形式等价。
        self.assertIs(typing.get_origin(self.tft.Stage), typing.Literal)
        self.assertEqual(set(typing.get_args(self.tft.Stage)), {"stage1", "stage2"})
        self.assertIs(typing.get_origin(self.tft.TargetKind), typing.Literal)
        self.assertEqual(set(typing.get_args(self.tft.TargetKind)), {"actor", "collab"})
        expected_types = {
            "TeamOperation": {"name": str, "stage": str, "expected_version": int | None},
            "KeyIssue": {"reason": str, "stage": self.tft.Stage, "key": str,
                         "source_role": str, "retryable": bool},
            "TaskBinding": {"task_id": str, "task_instance_id": str,
                            "owner_user_id": int, "owner_library_id": str},
            "CompletionProjection": {"seal_id": str | None, "kind": str,
                                     "trigger": str, "sealed_at": str | None,
                                     "final_decision": str, "persisted": bool},
            "AffectedCell": {"stage": str, "item_key": str},
            "MutationFootprint": {"cells": tuple[self.tft.AffectedCell, ...],
                                  "complete": bool, "reason": str | None},
            "SealWitness": {"stage": self.tft.Stage, "item_key": str,
                            "source_path": Path | None, "registration_id": str | None,
                            "generation": int | None, "library_id": str,
                            "seal_id": str, "content_sha256": str,
                            "payload_sha256": str, "sealed_at": str},
            "KeyedReadPlan": {"binding": self.tft.TaskBinding,
                              "operation": self.tft.TeamOperation,
                              "item_keys": tuple[str, ...],
                              "selected_paths": tuple[Path, ...]},
        }
        for name, fields in expected_types.items():
            hints = typing.get_type_hints(getattr(self.tft, name))
            for field, expected in fields.items():
                with self.subTest(dto=name, field=field):
                    self.assertEqual(hints[field], expected)
        plan_hints = typing.get_type_hints(self.tft.KeyedReadPlan)
        for field, args in {
            "witnesses": (tuple[str, str], self.tft.SealWitness),
            "live_dependencies": (tuple[int, str], tuple[str, ...]),
            "issues": (tuple[str, str], self.tft.KeyIssue),
        }.items():
            self.assertIs(typing.get_origin(plan_hints[field]), Mapping)
            self.assertEqual(typing.get_args(plan_hints[field]), args)
        # P00 不在共同 types 中预定义 runtime 状态对象（产品报告边界）。
        self.assertFalse(hasattr(self.tft, "TeamReadScope"))
        self.assertFalse(hasattr(self.tft, "TeamWriteInputs"))
        # keyed_domain 不强制 re-export DTO；若暴露同名则必须同一对象
        # （不造同名第二份）。
        for name in _KD_DTO_FIELDS:
            if hasattr(self.kdm, name):
                self.assertIs(getattr(self.kdm, name),
                              getattr(self.tft, name),
                              f"keyed_domain redefines DTO {name}")

    def test_no_reverse_runtime_imports(self):
        for module_name in ("coscreen.team_flow_types",
                            "custom_backend.team_keyed_domain"):
            with self.subTest(module=module_name):
                probe_code = (
                    "import sys\n"
                    "sys.path.insert(0, sys.argv[1])\n"
                    f"import {module_name}\n"
                    "print('\\n'.join(sorted(sys.modules)))\n"
                )
                completed = subprocess.run(
                    [sys.executable, "-I", "-B", "-c", probe_code,
                     str(REPO_ROOT)],
                    cwd=str(REPO_ROOT), capture_output=True, text=True,
                    timeout=30)
                self.assertEqual(
                    completed.returncode, 0,
                    f"probe import failed: {completed.stderr}")
                loaded = set(completed.stdout.split())
                self.assertIn(module_name, loaded)
                forbidden = sorted(
                    module for module in loaded
                    if any(module == prefix or module.startswith(prefix + ".")
                           for prefix in _KD_FORBIDDEN_IMPORT_PREFIXES)
                    or (module_name == "coscreen.team_flow_types"
                        and (module == "custom_backend"
                             or module.startswith("custom_backend."))))
                self.assertEqual(
                    forbidden, [],
                    f"reverse runtime import via {module_name}: {forbidden}")


class CanonicalItemRowsDomainTests(TeamDomainTestBase):
    """canonical_item_rows_on：请求键限制 owner 域 / title pending/duplicate。"""

    def test_requested_domain_not_full_catalog(self):
        path = self._kd_make_owner_db(
            self.base / "rows-catalog.db", **self._kd_main_seed(),
            corrupt_rows=[
                ("INSERT INTO articles (zotero_key, title, is_duplicate_of,"
                 " import_order) VALUES ('UNREQUESTED_BAD', CAST(X'80' AS TEXT),"
                 " NULL, 200)", ()),
            ])
        columns = set(self._kd_article_columns(path))
        conn = self._kd_open_borrowed(path)
        result = self.kdm.canonical_item_rows_on(conn, item_keys=("A3", "A1"))
        self.assertEqual(set(result), {"A1", "A3"})
        for key, order in (("A1", 0), ("A3", 2)):
            row = result[key]
            self.assertEqual(set(row), columns)
            self.assertEqual(row["zotero_key"], key)
            self.assertEqual(row["title"], f"title-{key}")
            self.assertEqual(row["import_order"], order)
            self.assertIsNone(row["is_duplicate_of"])
        # 迭代器 + 重复输入：内部 normalize，结果与已规范化请求一致。
        self.assertEqual(
            set(self.kdm.canonical_item_rows_on(
                conn, item_keys=iter(("A3", "A1", "A3")))),
            {"A1", "A3"})
        # SQL 元字符键为绑定值：精确返回，不被当 SQL/LIKE 结构。
        metachar = self.kdm.canonical_item_rows_on(
            conn, item_keys=("K'1", "K--1", "K_x", "K%1"))
        self.assertEqual(set(metachar), {"K'1", "K--1", "K_x", "K%1"})
        for key, order in (("K'1", 10), ("K--1", 11), ("K_x", 12),
                           ("K%1", 13)):
            self.assertEqual(metachar[key]["import_order"], order)
        # 前置：未请求坏文本真实存在，避免以不存在的行证明有界。
        with closing(conn.execute(
                "SELECT typeof(title), length(CAST(title AS BLOB)) "
                "FROM main.articles WHERE zotero_key=?", ("UNREQUESTED_BAD",))) as c:
            self.assertEqual(tuple(c.fetchone()), ("text", 1))
        with closing(conn.execute(
                "SELECT zotero_key FROM main.articles "
                "WHERE zotero_key IN ('A2','A4','DK1','TP1')")) as c:
            self.assertEqual({row[0] for row in c.fetchall()}, {"A2", "A4", "DK1", "TP1"})
        self._kd_reset_probe(conn)
        with _kd_trace(conn) as statements:
            bounded = self.kdm.canonical_item_rows_on(conn, item_keys=("A3",))
        self.assertEqual(set(bounded), {"A3"})
        # 已证明全扫和未识别形式是不同状态；未知不进入自动 PASS。
        for sql, expected_status in (
            ("SELECT * FROM main.articles", "PROVEN_VIOLATION"),
            ("SELECT a.zotero_key FROM main.articles AS a", "PROVEN_VIOLATION"),
            ("SELECT zotero_key FROM main.articles WHERE 1=1", "EVIDENCE_PENDING_AUDIT"),
        ):
            control = _kd_ProbeCursor(None, sql)
            control.executions.append({"seq": 0, "sql": sql, "parameters": ()})
            status, _, _ = _kd_analyze_rows_access([control], [sql], {"A3"})
            self.assertEqual(status, expected_status)
        status, detail, _ = _kd_analyze_rows_access(
            conn.cursors[conn.cursor_offset:], statements, {"A3"})
        if status == "EVIDENCE_PENDING_AUDIT":
            self.fail(f"EVIDENCE_PENDING_AUDIT: {detail}")
        self.assertEqual(status, "PROVEN_BOUNDED", detail)
        # 真实绑定值与坏行不能单独证明所有 SQL 形式有界。
        bound = [value for cursor in conn.cursors[conn.cursor_offset:]
                 for event in cursor.executions
                 for value in (event["parameters"].values()
                               if isinstance(event["parameters"], dict)
                               else event["parameters"])]
        self.assertIn("A3", bound)

    def test_requested_out_of_domain_keys_omitted(self):
        path = self._kd_make_owner_db(
            self.base / "rows-omit.db", **self._kd_main_seed())
        conn = self._kd_open_borrowed(path)
        rows_on = self.kdm.canonical_item_rows_on
        with closing(conn.execute(
                "SELECT a.is_duplicate_of, r.status, m.imported_in_batch "
                "FROM main.articles AS a "
                "JOIN main.title_duplicate_members AS m ON m.zotero_key=a.zotero_key "
                "JOIN main.title_duplicate_reviews AS r ON r.review_id=m.review_id "
                "WHERE a.zotero_key=?", ("TP1",))) as c:
            self.assertEqual(tuple(c.fetchone()), (None, "pending", 1))
        # duplicate 请求键：omit，不伪造行。
        self.assertEqual(set(rows_on(conn, item_keys=("A2", "DK1"))),
                         {"A2"})
        # title-pending 请求键：omit。
        self.assertEqual(rows_on(conn, item_keys=("TP1",)), {})
        # 完全不存在的请求键：omit。
        self.assertEqual(rows_on(conn, item_keys=("ZZZ",)), {})
        # 混合：域内返回、域外缺失，缺失集可由调用方 diff 发现。
        result = rows_on(conn, item_keys=("A1", "ZZZ"))
        self.assertEqual(set(result), {"A1"})
        missing = set(self.kdm.normalize_item_keys(("A1", "ZZZ"))) - set(result)
        self.assertEqual(missing, {"ZZZ"})
        # 空输入：内部 normalize 空集拒绝 → ValueError（P1）。
        with self.assertRaises(ValueError):
            rows_on(conn, item_keys=())
        # 正控：同连接合法单键精确返回。
        single = rows_on(conn, item_keys=("A2",))
        self.assertEqual(set(single), {"A2"})
        self.assertEqual(single["A2"]["import_order"], 1)

    def test_title_pending_and_duplicate(self):
        path = self._kd_make_owner_db(
            self.base / "rows-title.db", **self._kd_main_seed())
        conn = self._kd_open_borrowed(path)
        expected = set(self._kd_expected_canonical_keys())
        excluded = ("DK1", "DK2", "DUPDASH", "TP1", "TP2", "TP3")
        with closing(conn.execute(
                "SELECT a.zotero_key, a.is_duplicate_of, r.status, m.imported_in_batch "
                "FROM main.articles AS a "
                "JOIN main.title_duplicate_members AS m ON m.zotero_key=a.zotero_key "
                "JOIN main.title_duplicate_reviews AS r ON r.review_id=m.review_id "
                "WHERE a.zotero_key IN ('TP1','TP2','TP3','DONE1','DONE2','B0')")) as c:
            self.assertEqual({tuple(row) for row in c.fetchall()}, {
                ("TP1", None, "pending", 1), ("TP2", None, "undecided", 1),
                ("TP3", None, "pending", 1), ("TP3", None, "duplicate", 1),
                ("DONE1", None, "duplicate", 1),
                ("DONE2", None, "not_duplicate", 1), ("B0", None, "pending", 0),
            })
        with closing(conn.execute(
                "SELECT zotero_key FROM main.articles "
                "WHERE zotero_key IN ('DK1','DK2','DUPDASH') "
                "AND is_duplicate_of IS NOT NULL")) as c:
            self.assertEqual({row[0] for row in c.fetchall()}, set(excluded[:3]))
        result = self.kdm.canonical_item_rows_on(
            conn, item_keys=(*self._kd_expected_canonical_keys(), *excluded))
        self.assertEqual(set(result), expected)
        # out-of-domain 家族全不在结果。
        for absent_key in ("DK1", "DK2", "DUPDASH", "TP1", "TP2", "TP3"):
            self.assertNotIn(absent_key, result)
        # decided review（duplicate/not_duplicate）不挡域内键。
        self.assertIn("DONE1", result)
        self.assertIn("DONE2", result)
        # pending review 但 imported_in_batch=0：仍在域内（join 必须带
        # batch 条件）。
        self.assertIn("B0", result)
        self.assertEqual(result["B0"]["import_order"], 84)
        # 双 review 联合：任一 pending/undecided membership 即挡。
        # （TP3 同属 pending rev-TP3A 与 duplicate rev-TP3B → 出域。）
        # iter 全枚举与 rows 键集一致（域定义唯一实现）。
        keys, cursor = self.kdm.iter_canonical_item_keys_on(conn)
        self.assertEqual(set(keys), expected)
        self.assertIsNone(cursor)

    def test_missing_paired_tables_typed_not_where0(self):
        rows_on = self.kdm.canonical_item_rows_on
        iter_page = self.kdm.iter_canonical_item_keys_on
        unavailable = self.kdm.OwnerKeyedDomainUnavailableError
        base_seed = dict(articles=[("A1", 0), ("TP1", 1)], title_reviews=[
            {"review_id": "rev-TP1", "status": "pending",
             "members": [("TP1", 1)]},
        ])
        # members 存在而 reviews 缺失：有真实 pending member 行作诱饵，
        # 不能 WHERE0 空连接静默漏挡。
        path = self._kd_variant_db(
            "members-no-reviews", seed=base_seed,
            drop_tables=("title_duplicate_reviews",))
        conn = self._kd_open_borrowed(path)
        with self.assertRaises(unavailable):
            rows_on(conn, item_keys=("A1",))
        with self.assertRaises(unavailable):
            iter_page(conn)
        # reviews 存在而 members 缺失（双向配对，比现网 canonical_keys 更严）。
        path = self._kd_variant_db(
            "reviews-no-members", seed=base_seed,
            drop_tables=("title_duplicate_members",))
        conn = self._kd_open_borrowed(path)
        with self.assertRaises(unavailable):
            rows_on(conn, item_keys=("A1",))
        with self.assertRaises(unavailable):
            iter_page(conn)
        # articles 缺失：不伪零文章（先例 team_screening_runtime L521-523）。
        path = self._kd_variant_db(
            "no-articles", seed=base_seed, drop_tables=("articles",))
        conn = self._kd_open_borrowed(path)
        with self.assertRaises(unavailable):
            rows_on(conn, item_keys=("A1",))
        with self.assertRaises(unavailable):
            iter_page(conn)
        # 缺必要列（版本无关重建，不假定 ALTER DROP COLUMN / SQLite 3.35+）：
        # articles 失去 is_duplicate_of、reviews 失去 status → typed。
        path = self._kd_variant_db(
            "no-isdup-column", seed=base_seed,
            drop_columns=(("articles", "is_duplicate_of"),))
        conn = self._kd_open_borrowed(path)
        with self.assertRaises(unavailable):
            rows_on(conn, item_keys=("A1",))
        with self.assertRaises(unavailable):
            iter_page(conn)
        path = self._kd_variant_db(
            "no-status-column", seed=base_seed,
            drop_columns=(("title_duplicate_reviews", "status"),))
        conn = self._kd_open_borrowed(path)
        with self.assertRaises(unavailable):
            rows_on(conn, item_keys=("A1",))
        with self.assertRaises(unavailable):
            iter_page(conn)
        for table, column in (
            ("articles", "zotero_key"), ("articles", "import_order"),
            ("title_duplicate_reviews", "review_id"),
            ("title_duplicate_members", "imported_in_batch"),
        ):
            with self.subTest(table=table, column=column):
                path = self._kd_variant_db(
                    f"missing-{table}-{column}", seed=base_seed,
                    drop_columns=((table, column),))
                conn = self._kd_open_borrowed(path)
                with closing(conn.execute(f"PRAGMA main.table_info('{table}')")) as c:
                    self.assertNotIn(column, {row["name"] for row in c.fetchall()})
                with self.assertRaises(unavailable):
                    rows_on(conn, item_keys=("A1",))
                with self.assertRaises(unavailable):
                    iter_page(conn)
        # TEMP 同名数据故意与 main 冲突，两个 API 仍须使用 main。
        path = self._kd_make_owner_db(self.base / "main-shadow.db", **base_seed)
        conn = self._kd_open_borrowed(path)
        for statement in (
            "CREATE TEMP TABLE articles (zotero_key TEXT, is_duplicate_of TEXT, import_order INTEGER)",
            "INSERT INTO temp.articles VALUES ('A1','other',0)",
            "CREATE TEMP TABLE title_duplicate_reviews (review_id TEXT, status TEXT)",
            "INSERT INTO temp.title_duplicate_reviews VALUES ('shadow','pending')",
            "CREATE TEMP TABLE title_duplicate_members (review_id TEXT, zotero_key TEXT, imported_in_batch INTEGER)",
            "INSERT INTO temp.title_duplicate_members VALUES ('shadow','A1',1)",
        ):
            with closing(conn.execute(statement)):
                pass
        self.assertEqual(set(rows_on(conn, item_keys=("A1",))), {"A1"})
        keys, cursor = iter_page(conn)
        self.assertEqual(keys, ("A1",))
        self.assertIsNone(cursor)

    def test_corrupt_catalog_rows_typed_not_dropped(self):
        unavailable = self.kdm.OwnerKeyedDomainUnavailableError
        rows_on = self.kdm.canonical_item_rows_on
        iter_page = self.kdm.iter_canonical_item_keys_on
        # 脏 key 行（NULL / BLOB / 空串 zotero_key）：iter 不得静默丢行
        # （裁定"不得跳过该行"；现网 canonical_keys 的 Python 侧丢弃不进入
        # 新共同实现）；rows_on 有界语义不受请求外脏行毒化（P3）。
        path = self._kd_make_owner_db(
            self.base / "corrupt-keys.db",
            articles=[("A1", 0), ("TP1", 1)], title_reviews=[
                {"review_id": "rev-TP1", "status": "pending",
                 "members": [("TP1", 1)]},
            ],
            corrupt_rows=[
                ("INSERT INTO articles (zotero_key, title, is_duplicate_of,"
                 " import_order) VALUES (NULL, 'bad', NULL, 30)", ()),
                ("INSERT INTO articles (zotero_key, title, is_duplicate_of,"
                 " import_order) VALUES (CAST(X'80' AS BLOB), 'bad', NULL,"
                 " 31)", ()),
                ("INSERT INTO articles (zotero_key, title, is_duplicate_of,"
                 " import_order) VALUES ('', 'bad', NULL, 32)", ()),
            ])
        conn = self._kd_open_borrowed(path)
        with self.assertRaises(unavailable):
            iter_page(conn)
        good = rows_on(conn, item_keys=("A1",))
        self.assertEqual(set(good), {"A1"})
        self.assertEqual(good["A1"]["import_order"], 0)
        # 各存储类独立一库：REAL 会排在 TEXT/BLOB 之前，不能将三种
        # 坏值放在同一页后误以为都已经测试到。
        for storage, value in (("real", 100.5), ("text", "x"),
                               ("blob", b"\x00")):
            with self.subTest(storage=storage):
                path = self._kd_make_owner_db(
                    self.base / f"corrupt-io-{storage}.db",
                    articles=[("A1", 0), ("A2", 1), ("A3", 2),
                              ("A4", 3), ("A5", 4)],
                    corrupt_rows=[
                        ("INSERT INTO articles (zotero_key, title, is_duplicate_of,"
                         " import_order) VALUES ('BADIO', 'bad', NULL, ?)", (value,)),
                    ])
                conn = self._kd_open_borrowed(path)
                with closing(conn.execute(
                        "SELECT zotero_key, typeof(import_order) FROM main.articles "
                        "ORDER BY import_order, zotero_key")) as c:
                    self.assertEqual([tuple(row) for row in c.fetchall()], [
                        ("A1", "integer"), ("A2", "integer"), ("A3", "integer"),
                        ("A4", "integer"), ("A5", "integer"), ("BADIO", storage),
                    ])
                keys1, cursor1 = iter_page(conn, limit=2)
                self.assertEqual(keys1, ("A1", "A2"))
                self.assertEqual(cursor1, (1, "A2"))
                keys2, cursor2 = iter_page(conn, after=cursor1, limit=2)
                self.assertEqual(keys2, ("A3", "A4"))
                self.assertEqual(cursor2, (3, "A4"))
                # 坏值为 sentinel 时当前页也不得泄漏；作为实际行亦 typed。
                with self.assertRaises(unavailable):
                    iter_page(conn, after=cursor2, limit=1)
                with self.assertRaises(unavailable):
                    iter_page(conn, after=(4, "A5"), limit=2)
        # NULL import_order：iter 合法枚举（NULL-first，裁定覆盖原
        # fail-closed 方案）；rows_on 值按实际 schema 保留 None。
        path = self._kd_make_owner_db(
            self.base / "null-io.db",
            articles=[("A1", 0)],
            corrupt_rows=[
                ("INSERT INTO articles (zotero_key, title, is_duplicate_of,"
                 " import_order) VALUES ('NULLIO', 't', NULL, NULL)", ()),
            ])
        conn = self._kd_open_borrowed(path)
        keys, cursor = iter_page(conn)
        self.assertEqual(keys, ("NULLIO", "A1"))
        self.assertIsNone(cursor)
        both = rows_on(conn, item_keys=("A1", "NULLIO"))
        self.assertEqual(set(both), {"A1", "NULLIO"})
        self.assertIsNone(both["NULLIO"]["import_order"])
        self.assertEqual(both["A1"]["import_order"], 0)

    def test_internal_sql_blocks_capped_at_200_keys(self):
        count = 450
        articles = [(f"K{index:04d}", index) for index in range(count)]
        path = self._kd_make_owner_db(
            self.base / "rows-chunk.db",
            articles=articles + [("PENDX", 901)],
            dups=[("DUPX", "K0000", 900)],
            title_reviews=[
                {"review_id": "rev-PENDX", "status": "pending",
                 "members": [("PENDX", 1)]},
            ],
            corrupt_rows=[
                ("INSERT INTO articles (zotero_key, title, is_duplicate_of,"
                 " import_order) VALUES ('UNREQUESTED_BAD', CAST(X'80' AS TEXT),"
                 " NULL, 902)", ()),
            ])
        conn = self._kd_open_borrowed(path)
        requested = tuple(key for key, _ in articles)
        with closing(conn.execute(
                "SELECT a.is_duplicate_of, r.status, m.imported_in_batch "
                "FROM main.articles a JOIN main.title_duplicate_members m "
                "ON m.zotero_key=a.zotero_key JOIN main.title_duplicate_reviews r "
                "ON r.review_id=m.review_id WHERE a.zotero_key='PENDX'")) as c:
            self.assertEqual(tuple(c.fetchone()), (None, "pending", 1))
        with closing(conn.execute(
                "SELECT is_duplicate_of FROM main.articles WHERE zotero_key='DUPX'")) as c:
            self.assertEqual(c.fetchone()[0], "K0000")
        with closing(conn.execute(
                "SELECT typeof(title), length(CAST(title AS BLOB)) FROM main.articles "
                "WHERE zotero_key='UNREQUESTED_BAD'")) as c:
            self.assertEqual(tuple(c.fetchone()), ("text", 1))
        self.assertEqual(
            self.kdm.canonical_item_rows_on(conn, item_keys=("PENDX", "DUPX")), {})
        result = self.kdm.canonical_item_rows_on(conn, item_keys=requested)
        self.assertEqual(set(result), set(requested))
        self.assertEqual(result["K0000"]["import_order"], 0)
        self.assertEqual(result["K0449"]["import_order"], 449)
        self.assertNotIn("DUPX", result)
        self.assertNotIn("PENDX", result)
        self._kd_reset_probe(conn)
        with _kd_trace(conn) as statements:
            self.kdm.canonical_item_rows_on(conn, item_keys=requested)
        requested_set = set(requested)
        status, detail, seen_keys = _kd_analyze_rows_access(
            conn.cursors[conn.cursor_offset:], statements, requested_set)
        if status == "EVIDENCE_PENDING_AUDIT":
            self.fail(f"EVIDENCE_PENDING_AUDIT: {detail}")
        self.assertEqual(status, "PROVEN_BOUNDED", detail)
        self.assertEqual(seen_keys, requested_set)
        # 每次 execute 分别核块，不因 cursor 复用丢掉先前语句。
        for c in conn.cursors[conn.cursor_offset:]:
            for event in c.executions:
                values = (event["parameters"].values()
                          if isinstance(event["parameters"], dict) else event["parameters"])
                hits = [value for value in values
                        if isinstance(value, str) and value in requested_set]
                self.assertLessEqual(
                    len(hits), 200,
                    f"unbounded key block: {len(hits)} keys in one statement")


class CanonicalKeysPaginationTests(TeamDomainTestBase):
    """iter_canonical_item_keys_on：NULL-first/负数/同值 keyset 分页合同。"""

    def _kd_tie_fixture(self, path):
        """同 io 决胜 fixture：io=10 处 K3a/k3a 大小写混合（BINARY 序）。"""
        return self._kd_make_owner_db(path, articles=[
            ("A1", 0), ("A2", 1), ("B1", 2),
            ("K3a", 10), ("k3a", 10),
            ("K5", 20), ("K6", 21), ("K7", 30), ("TP9", 80),
        ], dups=[("DK9", "A1", 90)], title_reviews=[
            {"review_id": "rev-TP9", "status": "pending",
             "members": [("TP9", 1)]},
        ])

    def test_iter_canonical_keys_pages_cursor_chain(self):
        path = self._kd_tie_fixture(self.base / "iter-chain.db")
        conn = self._kd_open_borrowed(path)
        iter_page = self.kdm.iter_canonical_item_keys_on
        expected = ("A1", "A2", "B1", "K3a", "k3a", "K5", "K6", "K7")
        # limit=3 链式翻页：无重叠/无缺口，cursor=本页最后实际返回行。
        collected = []
        cursor = None
        for _ in range(100):
            keys, cursor = iter_page(conn, after=cursor, limit=3)
            self.assertIsInstance(keys, tuple)
            self.assertLessEqual(len(keys), 3)
            collected.extend(keys)
            if cursor is None:
                break
            self.assertIsInstance(cursor, tuple)
            self.assertEqual(len(cursor), 2)
        else:
            self.fail("pagination did not terminate within 100 pages")
        self.assertEqual(tuple(collected), expected)
        # 每页精确值与 cursor 精确值（哨兵不入 cursor）。
        keys, cursor = iter_page(conn, limit=3)
        self.assertEqual(keys, ("A1", "A2", "B1"))
        self.assertEqual(cursor, (2, "B1"))
        keys, cursor = iter_page(conn, after=cursor, limit=3)
        self.assertEqual(keys, ("K3a", "k3a", "K5"))
        self.assertEqual(cursor, (20, "K5"))
        keys, cursor = iter_page(conn, after=cursor, limit=3)
        self.assertEqual(keys, ("K6", "K7"))
        self.assertIsNone(cursor)
        # 恰好整页：有哨兵页 cursor=最后实际返回行；恰满页无哨兵 → None。
        path = self._kd_make_owner_db(
            self.base / "iter-exact.db",
            articles=[(f"E{index}", index) for index in range(6)])
        conn = self._kd_open_borrowed(path)
        keys, cursor = iter_page(conn, limit=3)
        self.assertEqual(keys, ("E0", "E1", "E2"))
        self.assertEqual(cursor, (2, "E2"))
        self.assertNotEqual(cursor, (5, "E5"))
        keys, cursor = iter_page(conn, after=cursor, limit=3)
        self.assertEqual(keys, ("E3", "E4", "E5"))
        self.assertIsNone(cursor)
        # 显式 after：同 io 按 key 决胜边界（BINARY：'K3a' < 'k3a'）。
        path = self._kd_tie_fixture(self.base / "iter-after.db")
        conn = self._kd_open_borrowed(path)
        keys, cursor = iter_page(conn, after=(10, "K3a"), limit=3)
        self.assertEqual(keys, ("k3a", "K5", "K6"))
        self.assertEqual(cursor, (21, "K6"))
        keys, cursor = iter_page(conn, after=(10, "k3a"), limit=3)
        self.assertEqual(keys, ("K5", "K6", "K7"))
        self.assertIsNone(cursor)
        # 越过末尾的合法比较界 → 空页 + None。
        self.assertEqual(
            iter_page(conn, after=(99, "ZZZ")), ((), None))
        # 空域 → ([], None)（裁定反例清单）。
        path = self._kd_make_owner_db(self.base / "iter-empty.db")
        conn = self._kd_open_borrowed(path)
        self.assertEqual(iter_page(conn), ((), None))
        # limit=1 逐键推进链。
        path = self._kd_tie_fixture(self.base / "iter-one.db")
        conn = self._kd_open_borrowed(path)
        collected = []
        cursor = None
        for _ in range(100):
            keys, cursor = iter_page(conn, after=cursor, limit=1)
            self.assertLessEqual(len(keys), 1)
            collected.extend(keys)
            if cursor is None:
                break
        else:
            self.fail("pagination did not terminate within 100 pages")
        self.assertEqual(tuple(collected), expected)

    def test_iter_rejects_bad_paging_inputs(self):
        path = self._kd_tie_fixture(self.base / "iter-bad.db")
        conn = self._kd_open_borrowed(path)
        iter_page = self.kdm.iter_canonical_item_keys_on
        # limit：非 bool 整数 1..200（裁定 F2）。
        for bad_limit in (0, -1, "x", None, 2.5, True, 201):
            with self.subTest(limit=bad_limit):
                with self.assertRaises(ValueError):
                    iter_page(conn, limit=bad_limit)
        # after 形状（裁定 F3）。
        for bad_after in (
            ("K1",), (0, "K1", "x"), ("5", "K1"), (2.5, "K1"),
            (True, "K1"), [0, "K1"],
        ):
            with self.subTest(after=bad_after):
                with self.assertRaises(ValueError):
                    iter_page(conn, after=bad_after)
        # after 键须过规范键合同（不能把坏 cursor 当首页）。
        for bad_key in ("", "A\x00B", "\ud800"):
            with self.subTest(after_key=bad_key):
                with self.assertRaises(ValueError):
                    iter_page(conn, after=(0, bad_key))
        # 排序值须可绑定为 SQLite 整数（int64 边界）。
        for bad_order in (2 ** 63, -(2 ** 63) - 1):
            with self.subTest(order=bad_order):
                with self.assertRaises(ValueError):
                    iter_page(conn, after=(bad_order, "A1"))
        # foreign cursor 是合法纯 keyset 比较界（裁定未要求存在性检查）：
        # (20,'NOPE') → io>20 的 K6/K7；(999,'A1') → 空页。
        keys, cursor = iter_page(conn, after=(20, "NOPE"))
        self.assertEqual(keys, ("K6", "K7"))
        self.assertIsNone(cursor)
        self.assertEqual(
            iter_page(conn, after=(999, "A1")), ((), None))
        # 正控：None 首页；(0,'A1') 精确续页。
        keys, cursor = iter_page(conn)
        self.assertEqual(keys, ("A1", "A2", "B1", "K3a", "k3a", "K5", "K6",
                                "K7"))
        self.assertIsNone(cursor)
        keys, cursor = iter_page(conn, after=(0, "A1"))
        self.assertEqual(keys, ("A2", "B1", "K3a", "k3a", "K5", "K6", "K7"))
        self.assertIsNone(cursor)

    def test_iter_null_first_and_negative_order_segments(self):
        # 裁定反例清单：多条 NULL（跨页）、NULL→负整数、正负整数、
        # 重复排序值按 key。NULL-first、负数数值升序。
        path = self._kd_make_owner_db(
            self.base / "iter-null.db",
            articles=[
                ("M3", 0), ("M4", 4), ("M1", -7), ("M2", -2),
                ("N1", None), ("N2", None), ("N3", None), ("N4", None),
                ("N5", None),
            ])
        conn = self._kd_open_borrowed(path)
        iter_page = self.kdm.iter_canonical_item_keys_on
        expected = ("N1", "N2", "N3", "N4", "N5", "M1", "M2", "M3", "M4")
        keys, cursor = iter_page(conn)
        self.assertEqual(keys, expected)
        self.assertIsNone(cursor)
        # NULL 段跨页 + NULL→负整数边界一页跨过不丢项；cursor 首元 None。
        keys, cursor = iter_page(conn, limit=2)
        self.assertEqual(keys, ("N1", "N2"))
        self.assertEqual(cursor, (None, "N2"))
        keys, cursor = iter_page(conn, after=cursor, limit=2)
        self.assertEqual(keys, ("N3", "N4"))
        self.assertEqual(cursor, (None, "N4"))
        keys, cursor = iter_page(conn, after=cursor, limit=2)
        self.assertEqual(keys, ("N5", "M1"))
        self.assertEqual(cursor, (-7, "M1"))
        keys, cursor = iter_page(conn, after=cursor, limit=2)
        self.assertEqual(keys, ("M2", "M3"))
        self.assertEqual(cursor, (0, "M3"))
        keys, cursor = iter_page(conn, after=cursor, limit=2)
        self.assertEqual(keys, ("M4",))
        self.assertIsNone(cursor)
        # NULL 段内续页 predicate：剩余 NULL 键 + 全部整数行。
        keys, cursor = iter_page(conn, after=(None, "N2"))
        self.assertEqual(keys, ("N3", "N4", "N5", "M1", "M2", "M3", "M4"))
        self.assertIsNone(cursor)
        # 负数续页（-2 与 0 之间无丢项）。
        keys, cursor = iter_page(conn, after=(-2, "M2"))
        self.assertEqual(keys, ("M3", "M4"))
        self.assertIsNone(cursor)
        # rows 键集/值与 iter 枚举一致（NULL io 值保留 None）。
        rows = self.kdm.canonical_item_rows_on(conn, item_keys=expected)
        self.assertEqual(set(rows), set(expected))
        for key in ("N1", "N2", "N3", "N4", "N5"):
            self.assertIsNone(rows[key]["import_order"])
        self.assertEqual(rows["M1"]["import_order"], -7)
        self.assertEqual(rows["M4"]["import_order"], 4)

    def test_iter_matches_rows_domain_consistency(self):
        path = self._kd_make_owner_db(
            self.base / "iter-consistency.db", **self._kd_main_seed())
        conn = self._kd_open_borrowed(path)
        expected = self._kd_expected_canonical_keys()
        keys, cursor = self.kdm.iter_canonical_item_keys_on(conn)
        self.assertEqual(keys, expected)
        self.assertIsNone(cursor)
        rows = self.kdm.canonical_item_rows_on(
            conn, item_keys=(*expected, "DK1", "DK2", "DUPDASH", "TP1", "TP2", "TP3"))
        self.assertEqual(set(rows), set(expected))
        # iter 序下 import_order 严格非降（iter 持有排序合同；rows_on 不定序）。
        orders = [rows[key]["import_order"] for key in keys]
        self.assertEqual(orders, sorted(orders))
        for key in ("DK1", "DK2", "DUPDASH", "TP1", "TP2", "TP3"):
            self.assertNotIn(key, rows)
            self.assertNotIn(key, keys)


class KeyedDomainBorrowedContractTests(TeamDomainTestBase):
    """native/Row/活动事务 / 事务所有权 / authorizer+trace / cursor 双故障。"""

    def _kd_both_apis(self):
        """双函数 subTest 载荷（合法连接上的最小调用）。"""
        return (
            ("canonical_item_rows_on",
             lambda conn: self.kdm.canonical_item_rows_on(
                 conn, item_keys=("A1", "A3"))),
            ("iter_canonical_item_keys_on",
             lambda conn: self.kdm.iter_canonical_item_keys_on(conn,
                                                               limit=2)),
        )

    def test_native_transaction_ownership(self):
        path = self._kd_make_owner_db(
            self.base / "borrowed-native.db", **self._kd_main_seed())
        for api_name, call in self._kd_both_apis():
            with self.subTest(api=api_name):
                # 非 native 对象 → TypeError。
                with self.assertRaises(TypeError):
                    call(object())
                # __class__ 伪装代理（转发 Row/active 属性）→ TypeError
                # （真实 type 继承判断）。
                real = sqlite3.connect(str(path))
                self.addCleanup(real.close)
                real.row_factory = sqlite3.Row
                real.execute("BEGIN")
                proxy = _kd_DelegatingConnection(real)
                self.assertTrue(isinstance(proxy, sqlite3.Connection))
                self.assertFalse(issubclass(type(proxy), sqlite3.Connection))
                self.assertIs(proxy.row_factory, sqlite3.Row)
                self.assertTrue(proxy.in_transaction)
                with self.assertRaises(TypeError):
                    call(proxy)
                real.rollback()
                # native 但 row_factory 非 Row（活动事务）→ RuntimeError。
                plain = sqlite3.connect(str(path))
                self.addCleanup(plain.close)
                plain.execute("BEGIN IMMEDIATE")
                with self.assertRaises(RuntimeError):
                    call(plain)
                self.assertTrue(plain.in_transaction)
                plain.rollback()
                # native+Row 但无活动事务 → RuntimeError（且未偷 BEGIN）。
                inactive = sqlite3.connect(str(path))
                self.addCleanup(inactive.close)
                inactive.row_factory = sqlite3.Row
                with self.assertRaises(RuntimeError):
                    call(inactive)
                self.assertFalse(inactive.in_transaction)
                # closed native → sqlite3.ProgrammingError 原样透传。
                closed = sqlite3.connect(str(path))
                closed.row_factory = sqlite3.Row
                closed.execute("BEGIN")
                closed.close()
                with self.assertRaises(sqlite3.ProgrammingError) as caught:
                    call(closed)
                self.assertIs(type(caught.exception),
                              sqlite3.ProgrammingError)
        # 正控：native+Row+BEGIN IMMEDIATE → 成功；事务所有权归 caller。
        path = self._kd_make_owner_db(
            self.base / "borrowed-ok.db", **self._kd_main_seed())
        conn = self._kd_open_borrowed(path)
        self._kd_reset_probe(conn)
        self.assertTrue(conn.in_transaction)
        rows = self.kdm.canonical_item_rows_on(conn, item_keys=("A3", "A1",
                                                                "A3"))
        self.assertEqual(set(rows), {"A1", "A3"})
        keys, cursor = self.kdm.iter_canonical_item_keys_on(conn, limit=2)
        self.assertIsInstance(keys, tuple)
        self.assertLessEqual(len(keys), 2)
        # 调用前后事务状态/工厂/连接所有权不变。
        self.assertTrue(conn.in_transaction)
        self.assertIs(conn.row_factory, sqlite3.Row)
        self.assertEqual(conn.commit_calls, 0)
        self.assertEqual(conn.rollback_calls, 0)
        self.assertEqual(conn.close_calls, 0)
        self.assertEqual(conn.executescript_calls, 0)
        # trace 分类：无事务控制语句、无 DML/DDL/设置性 PRAGMA。
        buckets = _kd_classify_statements(
            conn.statements[conn.statement_offset:])
        self.assertEqual(buckets["txn"], [])
        self.assertEqual(buckets["dml"], [])
        self.assertEqual(buckets["ddl"], [])
        self.assertEqual(buckets["other_pragma"], [])
        self.assertEqual(buckets["other"], [])
        # 连接未被 close：caller 可继续使用（随后显式 rollback 交还清理）。
        with closing(conn.execute("SELECT 1")) as probe_select:
            probe_select.fetchall()
        conn.rollback()
        self.assertFalse(conn.in_transaction)

    def test_authorizer_blocks_business_dml_and_unkeyed_vote_reads(self):
        path = self._kd_make_owner_db(
            self.base / "authz.db", **self._kd_main_seed())
        conn = self._kd_open_borrowed(path)
        with _kd_deny_writes_and_vote_reads(conn):
            # 真实 authorizer 下成功：不发业务 DML、不扫无关键票
            # （票表/undo/workflow_state 任何 READ 都会 DENY 爆）。
            rows = self.kdm.canonical_item_rows_on(
                conn, item_keys=("A1", "A2"))
            self.assertEqual(set(rows), {"A1", "A2"})
            keys, cursor = self.kdm.iter_canonical_item_keys_on(conn, limit=2)
            self.assertIsInstance(keys, tuple)
            self.assertLessEqual(len(keys), 2)
        self.assertTrue(conn.in_transaction)
        conn.rollback()
        # 正控（同一库上另开独立连接、同一回调逐一验证拒绝真实生效——
        # 库内有 decisions/articles 表，故失败只能是 authorizer 拒绝而非
        # no-such-table）。
        control = sqlite3.connect(str(path))
        self.addCleanup(control.close)
        with _kd_deny_writes_and_vote_reads(control):
            with self.assertRaises(sqlite3.Error):
                control.execute(
                    "INSERT INTO decisions (zotero_key, decision,"
                    " exclusion_reason, notes, tags, screened_at,"
                    " updated_at) VALUES ('A1', 'include', '', '', '',"
                    " '2026-11-15T00:00:00+00:00',"
                    " '2026-11-15T00:00:00+00:00')")
            with self.assertRaises(sqlite3.Error):
                control.execute("SELECT COUNT(*) FROM main.decisions")
            with self.assertRaises(sqlite3.Error):
                control.execute("DROP TABLE main.articles")
            with self.assertRaises(sqlite3.Error):
                control.execute("PRAGMA foreign_keys")
            with self.assertRaises(sqlite3.DatabaseError) as denied:
                control.execute("CREATE INDEX main.kd_authz_control ON articles(zotero_key)")
            self.assertEqual(denied.exception.sqlite_errorcode, sqlite3.SQLITE_AUTH)
            with self.assertRaises(sqlite3.DatabaseError) as denied:
                control.execute("WITH chosen AS (SELECT 'A1' AS k) "
                                "DELETE FROM main.articles WHERE zotero_key IN "
                                "(SELECT k FROM chosen)")
            self.assertEqual(denied.exception.sqlite_errorcode, sqlite3.SQLITE_AUTH)
            # 允许面正控：main 读与只读元数据不被拒（authorizer 非全禁）。
            control.execute("SELECT COUNT(*) FROM main.articles").fetchall()
            control.execute(
                "PRAGMA main.table_info('articles')").fetchall()

    def test_trace_read_only_bounded_and_cursors_closed(self):
        path = self._kd_make_owner_db(
            self.base / "trace.db", **self._kd_main_seed())
        conn = self._kd_open_borrowed(path)
        # 先核前三条 canonical 行和 rows 负控真实存在。
        with closing(conn.execute(
                "SELECT a.zotero_key, a.import_order FROM main.articles a "
                "WHERE a.zotero_key IN ('A1','A2','A3','A4') "
                "AND a.is_duplicate_of IS NULL AND NOT EXISTS ("
                "SELECT 1 FROM main.title_duplicate_members m "
                "JOIN main.title_duplicate_reviews r ON r.review_id=m.review_id "
                "WHERE m.zotero_key=a.zotero_key AND m.imported_in_batch=1 "
                "AND r.status IN ('pending','undecided')) "
                "ORDER BY a.import_order, a.zotero_key")) as c:
            self.assertEqual([tuple(row) for row in c.fetchall()],
                             [("A1", 0), ("A2", 1), ("A3", 2), ("A4", 3)])
        self._kd_reset_probe(conn)
        # 分别记录 rows 与 iter；authorizer 独立拒绝写入，覆盖 WITH DML。
        rows_offset = len(conn.cursors)
        with _kd_deny_writes_and_vote_reads(conn):
            with _kd_trace(conn) as row_statements:
                rows = self.kdm.canonical_item_rows_on(
                    conn, item_keys=("A1", "A3"))
            iter_offset = len(conn.cursors)
            with _kd_trace(conn) as iter_statements:
                keys, cursor = self.kdm.iter_canonical_item_keys_on(conn, limit=2)
        self.assertEqual(set(rows), {"A1", "A3"})
        self.assertEqual(keys, ("A1", "A2"))
        self.assertEqual(cursor, (1, "A2"))
        row_cursors = conn.cursors[rows_offset:iter_offset]
        status, detail, row_keys = _kd_analyze_rows_access(
            row_cursors, row_statements, {"A1", "A3"})
        if status == "EVIDENCE_PENDING_AUDIT":
            self.fail(f"EVIDENCE_PENDING_AUDIT: {detail}")
        self.assertEqual(status, "PROVEN_BOUNDED", detail)
        self.assertEqual(row_keys, {"A1", "A3"})
        row_values = [value for c in row_cursors for event in c.executions
                      for value in (event["parameters"].values()
                                    if isinstance(event["parameters"], dict)
                                    else event["parameters"])]
        self.assertTrue({"A1", "A3"}.issubset(set(row_values)))
        iter_cursors = conn.cursors[iter_offset:]
        status, detail, _ = _kd_analyze_rows_access(
            iter_cursors, iter_statements, set(), iterator=True)
        if status == "EVIDENCE_PENDING_AUDIT":
            self.fail(f"EVIDENCE_PENDING_AUDIT: {detail}")
        self.assertEqual(status, "PROVEN_BOUNDED", detail)
        page_events = [(c, event) for c in iter_cursors for event in c.executions
                       if "zotero_key" in event["columns"]]
        if len(page_events) != 1 or not page_events[0][1]["complete"]:
            self.fail(f"EVIDENCE_PENDING_AUDIT: ambiguous page materialization {page_events!r}")
        page_cursor, page_event = page_events[0]
        self.assertTrue(page_cursor.closed)
        self.assertEqual(page_event["rows"], 3)
        self.assertEqual(page_event["keys"], ["A1", "A2", "A3"])
        statements = row_statements + iter_statements
        buckets = _kd_classify_statements(statements)
        # 不要求所有语句首词都是 SELECT（允许只读元数据 PRAGMA）；
        # 但 DML/DDL/事务/设置性 PRAGMA 一律为零。
        self.assertEqual(buckets["dml"], [])
        self.assertEqual(buckets["ddl"], [])
        self.assertEqual(buckets["txn"], [])
        self.assertEqual(buckets["other_pragma"], [])
        self.assertEqual(buckets["other"], [])
        self.assertTrue(buckets["select"])
        for statement in buckets["metadata_pragma"]:
            self.assertRegex(statement.upper(), r"PRAGMA\s+MAIN\.(TABLE_INFO|"
                                                r"TABLE_XINFO)\(")
        joined = "\n".join(statements)
        # 域表 FROM/JOIN 必须 main. 限定；分页哨兵由返回页和游标验证。
        self.assertIsNone(_KD_UNQUALIFIED_DOMAIN_RE.search(joined))
        # 自建 cursor 全部完整物化并确定关闭；连接仍开着。
        for probe_cursor in conn.cursors[conn.cursor_offset:]:
            self.assertTrue(probe_cursor.closed,
                            f"cursor left open: {probe_cursor.sql!r}")
            self.assertTrue(
                probe_cursor.fully_fetched(),
                f"cursor not fully fetched: {probe_cursor.sql!r}")
        recorded_before = len(conn.statements)
        with closing(conn.execute("SELECT 1")) as probe_select:
            probe_select.fetchall()
        self.assertGreater(len(conn.statements), recorded_before)
        # 窗口二：分类器正控 —— 真实 INSERT（触发真实票账 trigger）被分类
        # 进 dml，证明分类器能识别 DML（防分类器假绿）。
        with _kd_trace(conn) as control_statements:
            with closing(conn.execute(
                    "INSERT INTO decisions (zotero_key, decision,"
                    " exclusion_reason, notes, tags, screened_at,"
                    " updated_at) VALUES ('A3', 'exclude', '', '', '',"
                    " '2026-11-15T00:00:00+00:00',"
                    " '2026-11-15T00:00:00+00:00')")) as control_cursor:
                control_cursor.fetchall()
        self.assertTrue(_kd_classify_statements(control_statements)["dml"])
        conn.rollback()
        self.assertFalse(conn.in_transaction)

    def test_fetch_main_error_preserved_with_observable_close_fault(self):
        # 真实 fetch 解码错误 + 同一 cursor 的真实 close 后注入故障。
        # 两种诊断均须可观察，但不要求异常身份、单一通道或顶层类型。
        path = self._kd_make_owner_db(
            self.base / "df-main.db",
            articles=[("A1", 0)],
            corrupt_rows=[
                ("INSERT INTO articles (zotero_key, title, is_duplicate_of,"
                 " import_order) VALUES (CAST(X'80' AS TEXT), 'bad', NULL,"
                 " 35)", ()),
            ])
        conn = self._kd_open_borrowed(path)
        self._kd_reset_probe(conn)
        close_fault = sqlite3.OperationalError("injected cursor close failure")
        _kd_ProbeCursor.close_fault = close_fault
        _kd_ProbeCursor.close_fault_mode = "fetch_error"
        exc = None
        try:
            self.kdm.iter_canonical_item_keys_on(conn)
        except Exception as caught:
            exc = caught
        finally:
            _kd_ProbeCursor.close_fault = None
            _kd_ProbeCursor.close_fault_mode = None
        faulting_cursors = [
            probe_cursor for probe_cursor in conn.cursors[conn.cursor_offset:]
            if probe_cursor.fetch_errors and probe_cursor.close_fault_fired]
        if len(faulting_cursors) != 1:
            self.fail("EVIDENCE_PENDING_AUDIT: fault_injection_unmatched; "
                      f"raised={exc!r}, cursors={conn.cursors[conn.cursor_offset:]!r}")
        self.assertIsNotNone(exc, "injected fetch/close faults were swallowed")
        faulting = faulting_cursors[0]
        self.assertGreaterEqual(faulting.close_attempts, 1)
        self.assertTrue(faulting.closed, "underlying cursor was not closed")
        native_fetch_error = faulting.fetch_errors[0]
        roots = _kd_exception_roots(exc)
        for fault in (native_fetch_error, close_fault):
            diagnostic = str(fault)
            self.assertTrue(
                _kd_fault_visible(roots, fault)
                or (diagnostic and any(
                    diagnostic in str(root) or any(
                        diagnostic in note for note in getattr(root, "__notes__", ()))
                    for root in roots)),
                f"fault not observable: {fault!r}; raised {exc!r}")
        # 连接所有权仍在 caller（未 close/未回滚）。
        self.assertTrue(conn.in_transaction)
        self.assertEqual(conn.close_calls, 0)
        conn.rollback()

    def test_success_fetch_close_fault_not_fake_success(self):
        # 成功 fetch 后 close 故障：不得返回假成功，故障与真实 close 可观察。
        path = self._kd_make_owner_db(
            self.base / "df-close.db",
            articles=[("A1", 0), ("A2", 1), ("A3", 2)])
        for api_name, call in (
            ("canonical_item_rows_on",
             lambda conn: self.kdm.canonical_item_rows_on(
                 conn, item_keys=("A1", "A3"))),
            ("iter_canonical_item_keys_on",
             lambda conn: self.kdm.iter_canonical_item_keys_on(conn,
                                                               limit=2)),
        ):
            for fault in (
                sqlite3.OperationalError("injected close failure"),
                sqlite3.ProgrammingError("injected close PE"),
            ):
                with self.subTest(api=api_name, fault=type(fault).__name__):
                    baseline = self._kd_open_borrowed(path)
                    self._kd_reset_probe(baseline)
                    baseline_result = call(baseline)
                    if api_name == "canonical_item_rows_on":
                        self.assertEqual(set(baseline_result), {"A1", "A3"})
                    else:
                        self.assertEqual(baseline_result, (("A1", "A2"), (1, "A2")))
                    candidates = [(index, c, event)
                                  for index, c in enumerate(baseline.cursors)
                                  if index >= baseline.cursor_offset and c.closed
                                  for event in c.executions
                                  if "zotero_key" in event["columns"]
                                  and event["rows"] and event["complete"]]
                    if len(candidates) != 1:
                        self.fail("EVIDENCE_PENDING_AUDIT: ambiguous baseline cursor; "
                                  f"candidates={candidates!r}")
                    index, _, event = candidates[0]
                    target = (index, event["sql"], event["parameters"], event["columns"],
                              event["rows"], tuple(event["keys"]), event["complete"])
                    self.assertTrue(baseline.in_transaction)
                    baseline.rollback()
                    conn = self._kd_open_borrowed(path)
                    self._kd_reset_probe(conn)
                    _kd_ProbeCursor.close_fault = fault
                    _kd_ProbeCursor.close_fault_mode = "observed"
                    _kd_ProbeCursor.close_fault_target = target
                    exc = None
                    try:
                        call(conn)
                    except Exception as caught:
                        exc = caught
                    finally:
                        _kd_ProbeCursor.close_fault = None
                        _kd_ProbeCursor.close_fault_mode = None
                        _kd_ProbeCursor.close_fault_target = None
                    faulting = [c for c in conn.cursors[conn.cursor_offset:]
                                if c.close_fault_fired]
                    if len(faulting) != 1:
                        self.fail("EVIDENCE_PENDING_AUDIT: fault_injection_unmatched; "
                                  f"target={target!r}, raised={exc!r}")
                    self.assertIsNotNone(exc, "injected close fault was swallowed")
                    roots = _kd_exception_roots(exc)
                    diagnostic = str(fault)
                    self.assertTrue(
                        _kd_fault_visible(roots, fault)
                        or (diagnostic and any(
                            diagnostic in str(root) or any(
                                diagnostic in note for note in getattr(root, "__notes__", ()))
                            for root in roots)),
                        f"close fault swallowed: {exc!r}")
                    faulting = [c for c in conn.cursors[conn.cursor_offset:]
                                if c.close_fault_fired]
                    self.assertTrue(faulting, "close fault was not injected")
                    self.assertGreaterEqual(faulting[0].close_attempts, 1)
                    self.assertTrue(faulting[0].closed)
                    self.assertTrue(faulting[0].fully_fetched())
                    # 连接所有权仍在 caller。
                    self.assertTrue(conn.in_transaction)
                    self.assertEqual(conn.close_calls, 0)
                    conn.rollback()


if __name__ == "__main__":
    unittest.main()
