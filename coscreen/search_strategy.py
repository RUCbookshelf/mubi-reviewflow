"""检索式管理与证据链（2.1.5 Wave G1/W3）。

纯计算 + 任务级协作存储，两块职责：

1. **translate（纯函数，零 IO）**：把用户已有的 PubMed 语法检索式转写为
   ERIC / WoS 语法；或把 PICO 概念块交给 ``coscreen.protocol_designer``
   生成五库检索式。任何不可译项**降级**为自由词并返回结构化警告，绝不抛
   错（前端展示"需人工核对"）。
2. **统一检索式库**：任务级 SQLite（``{task_dir}/search_strategies.sqlite3``）
   两张表——``search_strategies``（草稿→定稿→历史 + 版本链）与
   ``search_strategy_snapshots``（不可变证据链，append-only）。W13 的旧
   ``living_review.json`` 已保存检索式在首次访问时一次性并入（备份 + 幂等
   标记 + 轨迹表；旧文件只读不删，回退仍可读）。

不可变口径（设计 §二.B.2 / §三）：

- 快照的 ``pico_json`` / ``strategies_json`` / ``content_sha256`` 等内容列
  由 SQLite 触发器拒绝 UPDATE/DELETE；唯一允许的写是 ``counts_json`` 回填
  与 ``is_current`` 标记。
- 策略的 ``final`` / ``historical`` 行禁止删除、禁止回退为草稿、禁止改内容；
  草稿可删可改。
- 所有 SQL 为常量字面量 + ``?`` 参数绑定（无拼接/format/f-string）。
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from coscreen.protocol_designer import (
    COCHRANE_URL_PREFIX,
    EMBASE_URL_PREFIX,
    ERIC_URL_PREFIX,
    PICOElement,
    PUBMED_URL_PREFIX,
    generate_cochrane_query,
    generate_embase_query,
    generate_eric_query,
    generate_eric_url,
    generate_pubmed_query,
    generate_pubmed_url,
    generate_wos_query,
)

__all__ = [
    "DATABASES",
    "DB_FILENAME",
    "HIT_SOURCES",
    "LEGACY_BACKUP_SUFFIX",
    "LEGACY_FILENAME",
    "LEGACY_MARKER_KEY",
    "SOURCES",
    "STATUSES",
    "SearchStrategyError",
    "appendix_csv",
    "appendix_text",
    "compat_records",
    "compat_upsert",
    "connect",
    "create_snapshot",
    "create_strategy",
    "database_label",
    "delete_draft",
    "ensure_library",
    "finalize_strategy",
    "get_snapshot",
    "get_strategy",
    "list_snapshots",
    "list_strategies",
    "migrate_legacy_saved_searches",
    "migration_info",
    "prisma_prefill",
    "record_check",
    "resolve_check_target",
    "revise_strategy",
    "set_current_snapshot",
    "set_snapshot_counts",
    "set_subscription",
    "snapshot_to_strategies",
    "store_path",
    "strategy_counts",
    "translate_pubmed_query",
    "translate_pico",
    "update_draft",
]

#: 任务级统一检索式库文件（可加幂等；旧版程序不读即忽略）。
DB_FILENAME = "search_strategies.sqlite3"

#: W13 旧存储（living_review.json 的 search_records）与备份后缀。
LEGACY_FILENAME = "living_review.json"
LEGACY_BACKUP_SUFFIX = ".bak"
LEGACY_MARKER_KEY = "legacy_saved_searches_migrated_v1"

DATABASES = ("pubmed", "embase", "wos", "cochrane", "eric", "other")
STATUSES = ("draft", "final", "historical")
SOURCES = ("builder", "manual", "legacy_subscription")
HIT_SOURCES = ("", "preview", "manual")

#: 旧 living_review 数据库值 → 新枚举（manual 是"手录/非库检索"，归 other）。
_LEGACY_DATABASE_MAP = {
    "pubmed": "pubmed",
    "embase": "embase",
    "wos": "wos",
    "cochrane": "cochrane",
    "eric": "eric",
    "manual": "other",
}

#: 新枚举 → 旧 living_review 展示值（兼容层反向映射）。
_COMPAT_DATABASE_MAP = {"other": "manual"}

DATABASE_LABELS = {
    "pubmed": "PubMed",
    "embase": "Embase",
    "wos": "Web of Science",
    "cochrane": "Cochrane Library",
    "eric": "ERIC",
    "other": "Other",
}

_DATE_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")

MAX_TITLE = 200
MAX_QUERY = 4000
MAX_NOTE = 4000

#: 进程内写串行化（uvicorn 线程池会并发执行同步端点；跨进程仍由
#: ``BEGIN IMMEDIATE`` 兜底）。
_WRITE_LOCK = threading.Lock()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS search_strategies (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  title         TEXT NOT NULL,
  database      TEXT NOT NULL,
  query_text    TEXT NOT NULL,
  syntax_note   TEXT NOT NULL DEFAULT '',
  url           TEXT NOT NULL DEFAULT '',
  status        TEXT NOT NULL DEFAULT 'draft',
  version       INTEGER NOT NULL DEFAULT 1,
  prev_id       INTEGER,
  snapshot_id   INTEGER,
  source        TEXT NOT NULL DEFAULT 'manual',
  subscribe     INTEGER NOT NULL DEFAULT 0,
  last_check_at TEXT NOT NULL DEFAULT '',
  last_count    INTEGER,
  last_run_date TEXT NOT NULL DEFAULT '',
  legacy_fingerprint TEXT NOT NULL DEFAULT '',
  hit_count     INTEGER,
  hit_source    TEXT NOT NULL DEFAULT '',
  hit_at        TEXT NOT NULL DEFAULT '',
  executed_at   TEXT NOT NULL DEFAULT '',
  note          TEXT NOT NULL DEFAULT '',
  created_by    TEXT NOT NULL DEFAULT '',
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS search_strategy_snapshots (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  pico_json      TEXT NOT NULL,
  strategies_json TEXT NOT NULL,
  counts_json    TEXT NOT NULL DEFAULT '{}',
  counts_source  TEXT NOT NULL DEFAULT '',
  content_sha256 TEXT NOT NULL,
  is_current     INTEGER NOT NULL DEFAULT 0,
  note           TEXT NOT NULL DEFAULT '',
  created_by     TEXT NOT NULL DEFAULT '',
  created_at     TEXT NOT NULL,
  updated_at     TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS search_strategy_meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS search_strategy_migrations (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  source         TEXT NOT NULL,
  rows_before    INTEGER NOT NULL,
  rows_migrated  INTEGER NOT NULL,
  rows_after     INTEGER NOT NULL,
  backup_path    TEXT NOT NULL DEFAULT '',
  backup_sha256  TEXT NOT NULL DEFAULT '',
  source_sha256  TEXT NOT NULL DEFAULT '',
  migrated_at    TEXT NOT NULL,
  detail_json    TEXT NOT NULL DEFAULT '{}'
);
"""

#: 不可变防线（触发器）：内容列禁改禁删；定稿/历史禁删、禁回草稿。
_IMMUTABLE_SQL = """
DROP TRIGGER IF EXISTS search_strategy_snapshots_no_content_update;
CREATE TRIGGER search_strategy_snapshots_no_content_update
BEFORE UPDATE OF pico_json, strategies_json, content_sha256, created_by, created_at, note
ON search_strategy_snapshots
BEGIN
  SELECT RAISE(ABORT, 'snapshot_immutable');
END;
CREATE TRIGGER IF NOT EXISTS search_strategy_snapshots_no_delete
BEFORE DELETE ON search_strategy_snapshots
BEGIN
  SELECT RAISE(ABORT, 'snapshot_immutable');
END;
DROP TRIGGER IF EXISTS search_strategies_no_locked_content_update;
CREATE TRIGGER search_strategies_no_locked_content_update
BEFORE UPDATE OF title, database, query_text, syntax_note, url, source, snapshot_id,
  created_by, created_at, hit_count, hit_source, hit_at, executed_at, version,
  prev_id, note
ON search_strategies
WHEN OLD.status IN ('final', 'historical')
BEGIN
  SELECT RAISE(ABORT, 'strategy_immutable');
END;
CREATE TRIGGER IF NOT EXISTS search_strategies_no_locked_delete
BEFORE DELETE ON search_strategies
WHEN OLD.status IN ('final', 'historical')
BEGIN
  SELECT RAISE(ABORT, 'strategy_locked');
END;
CREATE TRIGGER IF NOT EXISTS search_strategies_no_status_rollback
BEFORE UPDATE OF status
ON search_strategies
WHEN OLD.status IN ('final', 'historical') AND NEW.status = 'draft'
BEGIN
  SELECT RAISE(ABORT, 'strategy_locked');
END;
"""


class SearchStrategyError(ValueError):
    """检索式库业务错误（``code`` 供 API 映射为结构化 4xx，绝不 500）。"""

    def __init__(self, code: str, message: str = "", **params: Any) -> None:
        super().__init__(message or code)
        self.code = code
        self.params = params


# ---------------------------------------------------------------------------
# 存储基础
# ---------------------------------------------------------------------------

def store_path(task_dir: str | Path) -> Path:
    """任务级检索式库路径（恒在任务目录内；调用方负责路径白名单校验）。"""
    return Path(task_dir) / DB_FILENAME


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def connect(path: str | Path, *, readonly: bool = False,
            timeout: float = 10.0) -> sqlite3.Connection:
    """打开库；``readonly`` 用 ``mode=ro``（不建库、不跑 DDL）。"""
    path = Path(path)
    if readonly:
        uri = path.resolve().as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=timeout,
                               isolation_level=None)
    else:
        conn = sqlite3.connect(path, timeout=timeout, isolation_level=None)
    conn.row_factory = sqlite3.Row
    return conn


SCHEMA_VERSION = 1

_SCHEMA_MARKER_KEY = "schema_version"


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """首次/升级建表建触发器；已是最新版本时零写（读端点不再每次跑 DDL）。"""
    try:
        current = _meta_get(conn, _SCHEMA_MARKER_KEY)
    except sqlite3.DatabaseError:
        current = None
    if current == str(SCHEMA_VERSION):
        return
    try:
        conn.executescript(_SCHEMA)
        _ensure_columns(conn)
        conn.executescript(_IMMUTABLE_SQL)
        _meta_set(conn, _SCHEMA_MARKER_KEY, str(SCHEMA_VERSION))
    except sqlite3.DatabaseError as exc:
        raise SearchStrategyError("storage_unavailable", str(exc)) from exc


def _ensure_columns(conn: sqlite3.Connection) -> None:
    """可加幂等：为 2.1.5 早期开发库补两列（旧版忽略；新库已含）。"""
    columns = {str(row["name"]) for row in
               conn.execute("PRAGMA table_info(search_strategies)")}
    if "last_run_date" not in columns:
        conn.execute(
            "ALTER TABLE search_strategies ADD COLUMN"
            " last_run_date TEXT NOT NULL DEFAULT ''")
    if "legacy_fingerprint" not in columns:
        conn.execute(
            "ALTER TABLE search_strategies ADD COLUMN"
            " legacy_fingerprint TEXT NOT NULL DEFAULT ''")


def ensure_library(task_dir: str | Path, *, migrate_legacy: bool = True,
                   readonly: bool = False) -> sqlite3.Connection:
    """打开（必要时创建）任务检索式库，并按需并入 W13 旧数据。

    ``readonly=True`` 时不建库：库不存在返回一个指向空内存库的连接（上层
    按"暂无条目"展示，绝不因只读 GET 产生磁盘写）。迁移只在可写连接上执行
    （迁移本身就是一次性写操作）。
    """
    path = store_path(task_dir)
    if readonly and not path.is_file():
        conn = sqlite3.connect(":memory:", isolation_level=None)
        conn.row_factory = sqlite3.Row
        _ensure_schema(conn)
        return conn
    conn = connect(path, readonly=readonly)
    if not readonly:
        _ensure_schema(conn)
    if migrate_legacy and not readonly:
        try:
            migrate_legacy_saved_searches(task_dir, conn)
        except sqlite3.OperationalError:
            # 锁忙：本次读降级为"已迁移部分可见"，迁移标记未写，下次重试。
            pass
    return conn


def _begin(conn: sqlite3.Connection) -> None:
    conn.execute("BEGIN IMMEDIATE")


def _commit(conn: sqlite3.Connection) -> None:
    conn.execute("COMMIT")


def _rollback(conn: sqlite3.Connection) -> None:
    try:
        conn.execute("ROLLBACK")
    except sqlite3.Error:
        pass


def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def _strategy(row: sqlite3.Row | Mapping[str, Any]) -> dict[str, Any]:
    data = dict(row)
    data["subscribe"] = bool(data.get("subscribe"))
    for key in ("prev_id", "snapshot_id", "last_count", "hit_count"):
        data[key] = data.get(key)
    return data


def _snapshot(row: sqlite3.Row | Mapping[str, Any]) -> dict[str, Any]:
    data = dict(row)
    data["pico"] = json.loads(data.pop("pico_json") or "[]")
    data["strategies"] = json.loads(data.pop("strategies_json") or "[]")
    data["counts"] = json.loads(data.pop("counts_json") or "{}")
    data["is_current"] = bool(data.get("is_current"))
    return data


def database_label(database: str) -> str:
    return DATABASE_LABELS.get(database, database)


def database_url(database: str, query_text: str) -> str:
    """按数据库生成导航 URL（仅拼字符串，不联网）。"""
    query = (query_text or "").strip()
    if not query:
        return ""
    if database == "pubmed":
        return generate_pubmed_url(query)
    if database == "eric":
        return generate_eric_url(query)
    if database == "embase":
        return EMBASE_URL_PREFIX + _quote(query)
    if database == "cochrane":
        return COCHRANE_URL_PREFIX + _quote(query)
    return ""


def _quote(value: str) -> str:
    import urllib.parse

    return urllib.parse.quote(value)


# ---------------------------------------------------------------------------
# translate（纯函数，零 IO）
# ---------------------------------------------------------------------------

_FIELD_ALIASES = {
    "tiab": "tiab", "ti": "ti", "ab": "ab", "tw": "tw", "kw": "tw", "kf": "tw",
    "titleabstract": "tiab", "title": "ti", "abstract": "ab", "textword": "tw",
    "keyword": "tw", "keywords": "tw",
    "au": "au", "fa": "au", "author": "au", "authors": "au",
    "mh": "mesh", "mesh": "mesh", "meshterm": "mesh", "meshterms": "mesh",
    "meshheading": "mesh", "meshheadings": "mesh",
    "sh": "mesh", "subject": "mesh",
    "ta": "ta", "jour": "ta", "jn": "ta", "journal": "ta", "journalname": "ta",
    "source": "ta",
    "dp": "dp", "pdat": "dp", "edat": "dp", "pubdate": "dp",
    "publicationdate": "dp", "date": "dp",
    "pt": "pt", "publicationtype": "pt",
}

_UNMAPPED_FIELDS = {
    "cp", "ad", "aff", "la", "si", "sn", "vol", "ip", "pg", "isbn", "issn",
}


def _normalize_field(field: str | None) -> str | None:
    if not field:
        return None
    key = re.sub(r"[\s_/\-]+", "", str(field)).casefold()
    if not key:
        return None
    return _FIELD_ALIASES.get(key)


def _balanced(text: str) -> bool:
    depth = 0
    quote: str | None = None
    for char in text:
        if quote is not None:
            if char == quote:
                quote = None
            continue
        if char in "\"'":
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0 and quote is None


def _read_field(text: str, index: int) -> tuple[str | None, int]:
    """从 ``[`` 处读取字段名，返回 ``(field, next_index)``；``]`` 缺失时不吞词。"""
    if index >= len(text) or text[index] != "[":
        return None, index
    end = text.find("]", index + 1)
    if end < 0:
        return None, index
    return text[index + 1:end], end + 1


def _scan_tokens(text: str) -> list[tuple[str, str, str | None]]:
    """把 PubMed 检索式拆成 ``(kind, value, field)`` 序列。

    kind = ``lparen | rparen | bool | term``；term 的 value 保留原文引号。
    """
    tokens: list[tuple[str, str, str | None]] = []
    i, n = 0, len(text)
    while i < n:
        char = text[i]
        if char.isspace():
            i += 1
            continue
        if char == "(":
            tokens.append(("lparen", "(", None))
            i += 1
            continue
        if char == ")":
            tokens.append(("rparen", ")", None))
            i += 1
            continue
        if char in "\"'":
            quote = char
            j = i + 1
            while j < n:
                if text[j] == "\\":
                    j += 2
                    continue
                if text[j] == quote:
                    break
                j += 1
            end = min(j + 1, n)
            value = text[i:end]
            field, nxt = _read_field(text, end)
            tokens.append(("term", value, field))
            i = nxt
            continue
        j = i
        while j < n and not text[j].isspace() and text[j] not in "()":
            j += 1
        word = text[i:j]
        i = j
        if word.upper() in {"AND", "OR", "NOT"}:
            tokens.append(("bool", word.upper(), None))
            continue
        field = None
        if "[" in word and word.endswith("]"):
            base, _, rest = word.partition("[")
            word, field = base, rest[:-1]
        else:
            saved = i
            while i < n and text[i].isspace():
                i += 1
            if i < n and text[i] == "[":
                field, nxt = _read_field(text, i)
                i = nxt if field is not None else saved
            else:
                i = saved
        tokens.append(("term", word, field))
    return tokens


def _translate_term(term: str, field: str | None, database: str,
                    warnings: list[dict[str, Any]]) -> tuple[str, bool]:
    """单个词条 → 目标库片段；返回 ``(fragment, degraded)``。"""
    normalized = _normalize_field(field)
    degraded = False
    if field is not None and normalized is None:
        # 未映射字段：按自由词降级（ERiC 标题/摘要；WoS Topic）并留结构化警告。
        degraded = True
        warnings.append({"code": "search_strategy.field_unmapped",
                         "params": {"field": str(field), "database": database}})
        normalized = "tiab"
    if normalized is None:
        normalized = "tiab"
    if database == "eric":
        mapping = {
            "tiab": f"(title:({term}) OR description:({term}))",
            "ti": f"title:({term})",
            "ab": f"description:({term})",
            "au": f"author:({term})",
            "mesh": f"subject:({term})",
            "ta": f"source:({term})",
            "dp": f"pubdate:({term})",
            "pt": f"publicationtype:({term})",
        }
        return mapping[normalized], degraded
    # Web of Science（database == "wos"）
    if normalized == "mesh":
        warnings.append({"code": "search_strategy.mesh_unmapped",
                         "params": {"database": "wos"}})
        degraded = True
    mapping = {
        "tiab": f"TS=({term})",
        "ti": f"TI=({term})",
        "ab": f"AB=({term})",
        "au": f"AU=({term})",
        "mesh": f"TS=({term})",
        "ta": f"SO=({term})",
        "dp": f"PY=({term})",
        "pt": f"DT=({term})",
    }
    return mapping[normalized], degraded


def _parse_primary(tokens: list[tuple[str, str, str | None]], pos: int):
    """primary := '(' expr ')' | term | (NOT primary)。

    返回 ``(node, pos, ok)``；``ok=False`` 表示操作数缺失（由 ``_transcribe``
    统一降级为 query_incomplete，绝不静默丢运算符）。
    """
    if pos >= len(tokens):
        return None, pos, False
    kind, value, field = tokens[pos]
    if kind == "lparen":
        node, pos, ok = _parse_expr(tokens, pos + 1)
        if pos < len(tokens) and tokens[pos][0] == "rparen":
            return node, pos + 1, ok
        return node, pos, False
    if kind == "term":
        return ("term", value, field), pos + 1, True
    if kind == "bool" and value == "NOT":
        # 一元 NOT 在 PubMed/WoS 都不是合法检索语法；标记出来由上层整体降级。
        child, pos, ok = _parse_primary(tokens, pos + 1)
        return (("unary_not", child) if child is not None else None), pos, ok
    return None, pos, False


def _parse_expr(tokens: list[tuple[str, str, str | None]], pos: int):
    """expr := primary ((AND|OR|NOT|隐式) primary)*，全部左结合。

    PubMed 的布尔运算符**同级左结合**（``a OR b AND c`` = ``(a OR b) AND c``），
    而 ERIC/WoS 原生按 AND 优先解析。先把 PubMed 语义显式括号化，再渲染，
    避免输出端优先级漂移。返回 ``(node, pos, ok)``。
    """
    node, pos, ok = _parse_primary(tokens, pos)
    if node is None or not ok:
        return node, pos, False
    while pos < len(tokens):
        kind, value, _field = tokens[pos]
        if kind == "bool":
            operator = value.lower()
            pos += 1
        elif kind in ("term", "lparen"):
            operator = "and"  # 隐式相邻 = AND
        else:
            break
        right, pos, right_ok = _parse_primary(tokens, pos)
        if right is None or not right_ok:
            # 尾随运算符/空操作数：保留已解析部分但显式标记不完整。
            return node, pos, False
        node = (operator, node, right)
    return node, pos, True


def _contains_unary_not(node) -> bool:
    if not isinstance(node, tuple):
        return False
    if node[0] == "unary_not":
        return True
    return any(_contains_unary_not(child) for child in node[1:]
               if isinstance(child, tuple))


def _has_implicit_phrase(tokens: list[tuple[str, str, str | None]]) -> bool:
    """未加引号的词 + 带字段词相邻（`living review[tiab]`）视为短语歧义。

    PubMed 对被检索标签限定的多词词组可能按短语或隐式 AND 解析，不同版本/
    词表结果不一致；转写保留 AND 语义，但必须显式降级提示人工核对。
    """
    sentinel = object()
    previous: object = sentinel
    for kind, _value, field in tokens:
        if kind == "term":
            if previous is not sentinel and (previous is None or field is None):
                return True
            previous = field
        elif kind in ("bool", "rparen"):
            previous = sentinel
    return False


def _render_node(node, database: str, warnings: list[dict[str, Any]]) -> tuple[str, bool]:
    if node is None:
        return "", False
    kind = node[0]
    if kind == "term":
        return _translate_term(node[1], node[2], database, warnings)
    left, left_degraded = _render_node(node[1], database, warnings)
    right, right_degraded = _render_node(node[2], database, warnings)
    return (f"({left} {kind.upper()} {right})",
            left_degraded or right_degraded)


def _transcribe(query: str, database: str) -> dict[str, Any]:
    warnings: list[dict[str, Any]] = []
    if not _balanced(query):
        warnings.append({"code": "search_strategy.query_unbalanced", "params": {}})
        return {
            "database": database,
            "query_text": query,
            "url": database_url(database, query),
            "syntax_note": "unbalanced input returned unchanged",
            "degraded": True,
            "warnings": warnings,
        }
    tokens = _scan_tokens(query)
    node, pos, ok = _parse_expr(tokens, 0)
    if node is None or not ok or pos < len(tokens) or _contains_unary_not(node):
        # 解析不完整（尾随运算符/空操作数）或一元 NOT（PubMed/WoS 均非法）：
        # 原样返回 + 结构化警告，绝不猜语义或输出非法语法。
        warnings.append({"code": "search_strategy.query_incomplete",
                         "params": {"database": database}})
        return {
            "database": database,
            "query_text": query,
            "url": database_url(database, query),
            "syntax_note": "incomplete input returned unchanged",
            "degraded": True,
            "warnings": warnings,
        }
    translated, degraded = _render_node(node, database, warnings)
    if _has_implicit_phrase(tokens):
        # 未加引号的多词 + 字段标签：短语/隐式 AND 语义随词表版本而异，
        # 保留 AND 输出但显式降级提示人工核对。
        warnings.append({"code": "search_strategy.implicit_phrase_ambiguous",
                         "params": {"database": database}})
        degraded = True
    return {
        "database": database,
        "query_text": translated,
        "url": database_url(database, translated),
        "syntax_note": (f"{database} field syntax transcribed from PubMed syntax;"
                        " boolean precedence parenthesized"),
        "degraded": degraded,
        "warnings": warnings,
    }


def translate_pubmed_query(query: object) -> dict[str, Any]:
    """PubMed 既有语法 → PubMed（原样）/ ERIC / WoS 转写（纯函数、零 IO）。

    - 括号/引号不配对：三库条目均降级返回原文 + ``query_unbalanced`` 警告；
    - 未映射字段（如 ``[cp]``）：按自由词降级 + ``field_unmapped`` 警告；
    - WoS 无 MeSH 字段：``[mh]`` 按 ``TS=`` 降级 + ``mesh_unmapped`` 警告；
    - 任何输入都不抛异常（空输入返回空策略 + 警告），由 API 层做 400 校验。
    """
    text = "" if query is None else str(query)
    warnings: list[dict[str, Any]] = []
    if not text.strip():
        warnings.append({"code": "search_strategy.query_required", "params": {}})
        return {"source": "query_text", "strategies": [], "warnings": warnings}
    strategies = [{
        "database": "pubmed",
        "query_text": text,
        "url": generate_pubmed_url(text),
        "syntax_note": "PubMed original syntax",
        "degraded": False,
        "warnings": [],
    }]
    for database in ("eric", "wos"):
        strategies.append(_transcribe(text, database))
    for entry in strategies:
        warnings.extend(entry["warnings"])
    return {"source": "query_text", "strategies": strategies, "warnings": warnings}


def translate_pico(pico: Iterable[Any], date_from: str = "",
                   date_to: str = "") -> dict[str, Any]:
    """PICO 概念块 → 五库检索式（协议设计器生成器；纯函数、零 IO）。"""
    elements: list[PICOElement] = []
    for item in pico or []:
        if isinstance(item, PICOElement):
            elements.append(item)
            continue
        if not isinstance(item, Mapping):
            raise SearchStrategyError("pico_invalid")
        elements.append(PICOElement(
            key=str(item.get("key") or ""),
            label=str(item.get("label") or ""),
            value=str(item.get("value") or ""),
            synonyms=[str(term) for term in (item.get("synonyms") or [])],
            mesh_terms=[str(term) for term in (item.get("mesh_terms") or [])],
        ))
    generated = [
        ("pubmed", generate_pubmed_query(elements)),
        ("embase", generate_embase_query(elements)),
        ("cochrane", generate_cochrane_query(elements)),
        ("eric", generate_eric_query(elements)),
        ("wos", generate_wos_query(elements)),
    ]
    strategies: list[dict[str, Any]] = []
    for database, query in generated:
        url = (generate_pubmed_url(query, date_from, date_to)
               if database == "pubmed" and query else database_url(database, query))
        strategies.append({
            "database": database,
            "query_text": query,
            "url": url,
            "syntax_note": f"{database} syntax generated from PICO blocks",
            "degraded": False,
            "warnings": [],
        })
    return {"source": "pico", "strategies": strategies, "warnings": []}


# ---------------------------------------------------------------------------
# 策略 CRUD / 状态机
# ---------------------------------------------------------------------------

def get_strategy(conn: sqlite3.Connection, strategy_id: int) -> dict[str, Any]:
    row = conn.execute(
        "SELECT id, title, database, query_text, syntax_note, url, status,"
        " version, prev_id, snapshot_id, source, subscribe, last_check_at,"
        " last_count, last_run_date, hit_count, hit_source, hit_at, executed_at, note,"
        " created_by, created_at, updated_at"
        " FROM search_strategies WHERE id = ?", (int(strategy_id),)).fetchone()
    if row is None:
        raise SearchStrategyError("strategy_not_found", id=int(strategy_id))
    return _strategy(row)


def list_strategies(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT id, title, database, query_text, syntax_note, url, status,"
        " version, prev_id, snapshot_id, source, subscribe, last_check_at,"
        " last_count, last_run_date, hit_count, hit_source, hit_at, executed_at, note,"
        " created_by, created_at, updated_at FROM search_strategies"
        " ORDER BY CASE status WHEN 'final' THEN 0 WHEN 'draft' THEN 1"
        " ELSE 2 END, updated_at DESC, id DESC").fetchall()
    return [_strategy(row) for row in rows]


def strategy_counts(conn: sqlite3.Connection) -> dict[str, int]:
    counts = {status: 0 for status in STATUSES}
    for row in conn.execute(
            "SELECT status, COUNT(*) AS n FROM search_strategies GROUP BY status"):
        counts[str(row["status"])] = int(row["n"])
    counts["total"] = sum(counts[status] for status in STATUSES)
    return counts


def _clean_title(value: object) -> str:
    title = " ".join(str(value or "").split())
    if not title:
        raise SearchStrategyError("title_required")
    if len(title) > MAX_TITLE:
        raise SearchStrategyError("title_required", max=MAX_TITLE)
    return title


def _clean_query(value: object) -> str:
    query = "" if value is None else str(value)
    if not query.strip():
        raise SearchStrategyError("query_required")
    if len(query) > MAX_QUERY:
        raise SearchStrategyError("query_required", max=MAX_QUERY)
    return query


def _clean_database(value: object) -> str:
    database = str(value or "").strip().lower()
    if database not in DATABASES:
        raise SearchStrategyError("database_invalid", database=database)
    return database


def _clean_source(value: object, *, allow_legacy: bool = False) -> str:
    source = str(value or "manual").strip().lower() or "manual"
    allowed = SOURCES if allow_legacy else ("builder", "manual")
    if source not in allowed:
        raise SearchStrategyError("source_invalid", source=source)
    return source


def _clean_note(value: object) -> str:
    note = "" if value is None else str(value)
    if len(note) > MAX_NOTE:
        raise SearchStrategyError("note_invalid", max=MAX_NOTE)
    return note


def _clean_date(value: object, *, field: str = "executed_at") -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if not _DATE_RE.match(text):
        raise SearchStrategyError("executed_at_invalid", field=field)
    return text


def _clean_count(value: object) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise SearchStrategyError("counts_invalid", database="")
    if value < 0:
        raise SearchStrategyError("counts_invalid", database="")
    return int(value)


def create_strategy(conn: sqlite3.Connection, data: Mapping[str, Any], *,
                    actor: str, now: str | None = None) -> dict[str, Any]:
    """新建草稿（status 固定 draft；定稿只能走 :func:`finalize_strategy`）。"""
    now = now or _now()
    title = _clean_title(data.get("title"))
    database = _clean_database(data.get("database"))
    query = _clean_query(data.get("query_text"))
    source = _clean_source(data.get("source", "manual"))
    note = _clean_note(data.get("note"))
    subscribe = 1 if data.get("subscribe") else 0
    snapshot_id = data.get("snapshot_id")
    if snapshot_id is not None:
        snapshot_id = int(snapshot_id)
    url = database_url(database, query)
    with _WRITE_LOCK:
        _begin(conn)
        try:
            if snapshot_id is not None:
                exists = conn.execute(
                    "SELECT 1 FROM search_strategy_snapshots WHERE id = ?",
                    (snapshot_id,)).fetchone()
                if exists is None:
                    raise SearchStrategyError("snapshot_not_found", id=snapshot_id)
            cursor = conn.execute(
                "INSERT INTO search_strategies (title, database, query_text,"
                " syntax_note, url, status, version, prev_id, snapshot_id,"
                " source, subscribe, last_check_at, last_count, last_run_date,"
                " legacy_fingerprint, hit_count, hit_source, hit_at, executed_at,"
                " note, created_by, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, 'draft', 1, NULL, ?, ?, ?, '', NULL,"
                " '', '', NULL, '', '', '', ?, ?, ?, ?)",
                (title, database, query, str(data.get("syntax_note") or ""),
                 url, snapshot_id, source, subscribe, note, actor, now, now))
            sid = int(cursor.lastrowid)
            _commit(conn)
        except Exception:
            _rollback(conn)
            raise
    return get_strategy(conn, sid)


def update_draft(conn: sqlite3.Connection, strategy_id: int,
                 data: Mapping[str, Any], *, now: str | None = None) -> dict[str, Any]:
    """编辑草稿（仅 draft；每个可选字段一条常量 SQL，无动态拼装）。"""
    now = now or _now()
    with _WRITE_LOCK:
        _begin(conn)
        try:
            current = get_strategy(conn, strategy_id)
            if current["status"] != "draft":
                raise SearchStrategyError("strategy_not_draft",
                                          status=current["status"])
            if "title" in data:
                conn.execute(
                    "UPDATE search_strategies SET title = ?, updated_at = ? WHERE id = ?",
                    (_clean_title(data.get("title")), now, int(strategy_id)))
            database = current["database"]
            if "database" in data:
                database = _clean_database(data.get("database"))
                conn.execute(
                    "UPDATE search_strategies SET database = ?, updated_at = ? WHERE id = ?",
                    (database, now, int(strategy_id)))
            query = current["query_text"]
            if "query_text" in data:
                query = _clean_query(data.get("query_text"))
                conn.execute(
                    "UPDATE search_strategies SET query_text = ?, updated_at = ? WHERE id = ?",
                    (query, now, int(strategy_id)))
            if "syntax_note" in data:
                conn.execute(
                    "UPDATE search_strategies SET syntax_note = ?, updated_at = ? WHERE id = ?",
                    (str(data.get("syntax_note") or ""), now, int(strategy_id)))
            if "note" in data:
                conn.execute(
                    "UPDATE search_strategies SET note = ?, updated_at = ? WHERE id = ?",
                    (_clean_note(data.get("note")), now, int(strategy_id)))
            if "subscribe" in data:
                conn.execute(
                    "UPDATE search_strategies SET subscribe = ?, updated_at = ? WHERE id = ?",
                    (1 if data.get("subscribe") else 0, now, int(strategy_id)))
            if "database" in data or "query_text" in data:
                conn.execute(
                    "UPDATE search_strategies SET url = ?, updated_at = ? WHERE id = ?",
                    (database_url(database, query), now, int(strategy_id)))
            _commit(conn)
        except Exception:
            _rollback(conn)
            raise
    return get_strategy(conn, strategy_id)


def finalize_strategy(conn: sqlite3.Connection, strategy_id: int,
                      data: Mapping[str, Any] | None = None, *,
                      now: str | None = None) -> dict[str, Any]:
    """定稿：同库旧 final 全部转 historical；本条接版本链。"""
    now = now or _now()
    data = data or {}
    with _WRITE_LOCK:
        _begin(conn)
        try:
            current = get_strategy(conn, strategy_id)
            if current["status"] != "draft":
                raise SearchStrategyError("strategy_not_draft",
                                          status=current["status"])
            database = current["database"]
            finals = conn.execute(
                "SELECT id, version FROM search_strategies"
                " WHERE database = ? AND status = 'final'"
                " ORDER BY version DESC, id DESC", (database,)).fetchall()
            superseded = [int(row["id"]) for row in finals]
            latest = finals[0] if finals else None
            # 版本号只由已定稿/历史条目推进；草稿携带的 version（revise 生成）
            # 若更大则保留，避免"先 revise 再定稿"跳号。
            max_row = conn.execute(
                "SELECT MAX(version) AS v FROM search_strategies"
                " WHERE database = ? AND status IN ('final', 'historical')",
                (database,)).fetchone()
            max_version = int(max_row["v"] or 0)
            version = max(max_version + 1, int(current.get("version") or 1))
            prev_id = int(latest["id"]) if latest is not None else current.get("prev_id")
            for row in finals:
                conn.execute(
                    "UPDATE search_strategies SET status = 'historical',"
                    " subscribe = 0, updated_at = ? WHERE id = ?",
                    (now, int(row["id"])))
            executed_at = _clean_date(data.get("executed_at")
                                      or current.get("executed_at") or now[:10])
            hit_count = _clean_count(data.get("hit_count"))
            if hit_count is None and "hit_count" not in data:
                hit_count = current.get("hit_count")
            hit_source = str(data.get("hit_source") or "").strip().lower()
            if hit_source not in HIT_SOURCES:
                raise SearchStrategyError("hit_source_invalid", hit_source=hit_source)
            if hit_count is None:
                hit_source = ""
            elif not hit_source:
                hit_source = "manual"
            note = (_clean_note(data.get("note"))
                    if "note" in data else str(current.get("note") or ""))
            conn.execute(
                "UPDATE search_strategies SET status = 'final', version = ?,"
                " prev_id = ?, executed_at = ?, hit_count = ?, hit_source = ?,"
                " hit_at = ?, note = ?, updated_at = ? WHERE id = ?",
                (version, prev_id, executed_at, hit_count, hit_source,
                 now if hit_count is not None else "", note, now,
                 int(strategy_id)))
            _commit(conn)
        except Exception:
            _rollback(conn)
            raise
    result = get_strategy(conn, strategy_id)
    return {"strategy": result, "superseded": superseded,
            "chain": {"prev_id": prev_id, "version": version}}


def revise_strategy(conn: sqlite3.Connection, strategy_id: int, *,
                    actor: str, now: str | None = None) -> dict[str, Any]:
    """以现有条目为底开新草稿（版本 +1，prev_id 指向源条目）。"""
    now = now or _now()
    with _WRITE_LOCK:
        _begin(conn)
        try:
            source = get_strategy(conn, strategy_id)
            cursor = conn.execute(
                "INSERT INTO search_strategies (title, database, query_text,"
                " syntax_note, url, status, version, prev_id, snapshot_id,"
                " source, subscribe, last_check_at, last_count, last_run_date,"
                " legacy_fingerprint, hit_count, hit_source, hit_at, executed_at,"
                " note, created_by, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, 'draft', ?, ?, ?, ?, ?, '', NULL, ?,"
                " '', NULL, '', '', '', ?, ?, ?, ?)",
                (source["title"], source["database"], source["query_text"],
                 source["syntax_note"], source["url"],
                 int(source["version"]) + 1, int(source["id"]),
                 source["snapshot_id"], source["source"],
                 int(source["subscribe"]), source.get("last_run_date") or "",
                 source["note"], actor, now, now))
            sid = int(cursor.lastrowid)
            _commit(conn)
        except Exception:
            _rollback(conn)
            raise
    return get_strategy(conn, sid)


def delete_draft(conn: sqlite3.Connection, strategy_id: int) -> None:
    """删除条目：仅草稿；定稿/历史由业务层 + 触发器双重拒绝。"""
    with _WRITE_LOCK:
        _begin(conn)
        try:
            current = get_strategy(conn, strategy_id)
            if current["status"] != "draft":
                raise SearchStrategyError("final_not_deletable",
                                          status=current["status"])
            conn.execute("DELETE FROM search_strategies WHERE id = ? AND status = 'draft'",
                         (int(strategy_id),))
            _commit(conn)
        except Exception:
            _rollback(conn)
            raise


def set_subscription(conn: sqlite3.Connection, strategy_id: int,
                     subscribe: bool, *, now: str | None = None) -> dict[str, Any]:
    now = now or _now()
    with _WRITE_LOCK:
        _begin(conn)
        try:
            get_strategy(conn, strategy_id)  # 404 语义先于任何写
            # 2.1.5 W3 修订：historical 也允许开关订阅——版本链推进时旧定稿
            # 自动退订（finalize 内 subscribe=0），但用户仍可手动把历史条目
            # 重新纳入订阅视图（只影响展示/检查，不改变证据链内容）。
            conn.execute(
                "UPDATE search_strategies SET subscribe = ?, updated_at = ? WHERE id = ?",
                (1 if subscribe else 0, now, int(strategy_id)))
            _commit(conn)
        except Exception:
            _rollback(conn)
            raise
    return get_strategy(conn, strategy_id)


# ---------------------------------------------------------------------------
# 快照（不可变证据链）
# ---------------------------------------------------------------------------

def _canonical(pico: Any, strategies: Any) -> str:
    return json.dumps({"pico": pico, "strategies": strategies},
                      ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))


def create_snapshot(conn: sqlite3.Connection, data: Mapping[str, Any], *,
                    actor: str, now: str | None = None) -> dict[str, Any]:
    now = now or _now()
    pico = data.get("pico")
    strategies = data.get("strategies")
    if not isinstance(pico, list):
        raise SearchStrategyError("snapshot_payload_invalid", field="pico")
    if not isinstance(strategies, list) or not strategies:
        raise SearchStrategyError("snapshot_payload_invalid", field="strategies")
    for entry in strategies:
        if not isinstance(entry, Mapping) or not str(entry.get("database") or ""):
            raise SearchStrategyError("snapshot_payload_invalid", field="strategies")
        if not str(entry.get("query_text") or "").strip():
            raise SearchStrategyError("snapshot_payload_invalid", field="strategies")
    counts = _clean_counts(data.get("counts"))
    counts_source = str(data.get("counts_source") or "").strip().lower()
    note = _clean_note(data.get("note"))
    digest = hashlib.sha256(_canonical(pico, strategies).encode("utf-8")).hexdigest()
    make_current = bool(data.get("make_current"))
    with _WRITE_LOCK:
        _begin(conn)
        try:
            if make_current:
                conn.execute(
                    "UPDATE search_strategy_snapshots SET is_current = 0 WHERE is_current = 1")
            cursor = conn.execute(
                "INSERT INTO search_strategy_snapshots (pico_json, strategies_json,"
                " counts_json, counts_source, content_sha256, is_current, note,"
                " created_by, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '')",
                (json.dumps(pico, ensure_ascii=False, sort_keys=True),
                 json.dumps(strategies, ensure_ascii=False, sort_keys=True),
                 json.dumps(counts, ensure_ascii=False, sort_keys=True),
                 counts_source, digest, 1 if make_current else 0, note,
                 actor, now))
            sid = int(cursor.lastrowid)
            _commit(conn)
        except Exception:
            _rollback(conn)
            raise
    return get_snapshot(conn, sid)


def get_snapshot(conn: sqlite3.Connection, snapshot_id: int) -> dict[str, Any]:
    row = conn.execute(
        "SELECT id, pico_json, strategies_json, counts_json, counts_source,"
        " content_sha256, is_current, note, created_by, created_at, updated_at"
        " FROM search_strategy_snapshots WHERE id = ?",
        (int(snapshot_id),)).fetchone()
    if row is None:
        raise SearchStrategyError("snapshot_not_found", id=int(snapshot_id))
    return _snapshot(row)


def list_snapshots(conn: sqlite3.Connection) -> dict[str, Any]:
    rows = conn.execute(
        "SELECT id, pico_json, strategies_json, counts_json, counts_source,"
        " content_sha256, is_current, note, created_by, created_at, updated_at"
        " FROM search_strategy_snapshots ORDER BY id DESC").fetchall()
    snapshots = [_snapshot(row) for row in rows]
    current = next((entry["id"] for entry in snapshots if entry["is_current"]), None)
    return {"snapshots": snapshots, "current_id": current, "count": len(snapshots)}


def set_current_snapshot(conn: sqlite3.Connection, snapshot_id: int) -> dict[str, Any]:
    with _WRITE_LOCK:
        _begin(conn)
        try:
            get_snapshot(conn, snapshot_id)  # 404 语义先于任何写
            conn.execute(
                "UPDATE search_strategy_snapshots SET is_current = 0 WHERE is_current = 1")
            conn.execute(
                "UPDATE search_strategy_snapshots SET is_current = 1 WHERE id = ?",
                (int(snapshot_id),))
            _commit(conn)
        except Exception:
            _rollback(conn)
            raise
    return {"snapshot": get_snapshot(conn, snapshot_id), "current_id": int(snapshot_id)}


def _clean_counts(value: object) -> dict[str, int]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise SearchStrategyError("counts_invalid", database="")
    cleaned: dict[str, int] = {}
    for key, item in value.items():
        database = str(key)
        if database not in DATABASES:
            raise SearchStrategyError("counts_invalid", database=database)
        if item is None:
            continue
        if isinstance(item, bool) or not isinstance(item, int) or item < 0:
            raise SearchStrategyError("counts_invalid", database=database)
        cleaned[database] = int(item)
    return cleaned


def set_snapshot_counts(conn: sqlite3.Connection, snapshot_id: int,
                        counts: Mapping[str, Any], *, source: str = "manual",
                        now: str | None = None) -> dict[str, Any]:
    """回填命中数（唯一允许的快照写；不改内容哈希）。``None`` 表示清除该库。"""
    now = now or _now()
    incoming = dict(counts or {})
    with _WRITE_LOCK:
        _begin(conn)
        try:
            current = get_snapshot(conn, snapshot_id)
            merged = dict(current["counts"])
            for key, item in incoming.items():
                database = str(key)
                if database not in DATABASES:
                    raise SearchStrategyError("counts_invalid", database=database)
                if item is None:
                    merged.pop(database, None)
                    continue
                if isinstance(item, bool) or not isinstance(item, int) or item < 0:
                    raise SearchStrategyError("counts_invalid", database=database)
                merged[database] = int(item)
            conn.execute(
                "UPDATE search_strategy_snapshots SET counts_json = ?,"
                " counts_source = ?, updated_at = ? WHERE id = ?",
                (json.dumps(merged, ensure_ascii=False, sort_keys=True),
                 str(source or "manual"), now, int(snapshot_id)))
            _commit(conn)
        except Exception:
            _rollback(conn)
            raise
    return get_snapshot(conn, snapshot_id)


def snapshot_to_strategies(conn: sqlite3.Connection, snapshot_id: int,
                           databases: Iterable[str], *, actor: str,
                           subscribe: bool = False, title_prefix: str = "",
                           now: str | None = None) -> dict[str, Any]:
    """逐库勾选快照 → 草稿条目（source=builder, snapshot_id 留痕）。"""
    now = now or _now()
    snapshot = get_snapshot(conn, snapshot_id)
    wanted = [str(item) for item in (databases or [])]
    if not wanted:
        raise SearchStrategyError("databases_invalid", databases=[])
    by_database = {str(entry.get("database")): entry
                   for entry in snapshot["strategies"] if isinstance(entry, Mapping)}
    unknown = [name for name in wanted if name not in by_database]
    if unknown:
        raise SearchStrategyError("databases_invalid", databases=unknown)
    created: list[dict[str, Any]] = []
    for database in wanted:
        entry = by_database[database]
        title = f"{title_prefix}{database_label(database)} 检索式"
        created.append(create_strategy(conn, {
            "title": title,
            "database": database,
            "query_text": entry.get("query_text"),
            "syntax_note": entry.get("syntax_note") or "",
            "snapshot_id": snapshot["id"],
            "subscribe": subscribe,
            "source": "builder",
        }, actor=actor, now=now))
    return {"strategies": created, "created": len(created)}


# ---------------------------------------------------------------------------
# 附录 / PRISMA 预填
# ---------------------------------------------------------------------------

def _final_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT id, title, database, query_text, syntax_note, url, status,"
        " version, prev_id, snapshot_id, source, subscribe, last_check_at,"
        " last_count, last_run_date, hit_count, hit_source, hit_at, executed_at, note,"
        " created_by, created_at, updated_at FROM search_strategies"
        " WHERE status = 'final' ORDER BY database, version DESC, id DESC"
    ).fetchall()
    return [_strategy(row) for row in rows]


def appendix_text(conn: sqlite3.Connection) -> str:
    rows = _final_rows(conn)
    if not rows:
        raise SearchStrategyError("no_final_strategies")
    lines: list[str] = ["# 检索式附录", ""]
    for row in rows:
        lines += [f"## {database_label(row['database'])}", ""]
        lines += ["检索式：", "", "```text", row["query_text"], "```", ""]
        if row["executed_at"]:
            lines.append(f"执行日期：{row['executed_at']}")
        if row["hit_count"] is not None:
            source = row["hit_source"] or "manual"
            lines.append(f"命中数：{row['hit_count']}（来源：{source}）")
        lines.append(f"版本：{row['version']}")
        if row["note"]:
            lines.append(f"备注：{row['note']}")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def _csv_safe(value: object) -> object:
    text = str(value if value is not None else "")
    if text.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + text
    return value


def appendix_csv(conn: sqlite3.Connection) -> str:
    import csv
    import io

    rows = _final_rows(conn)
    if not rows:
        raise SearchStrategyError("no_final_strategies")
    out = io.StringIO(newline="")
    writer = csv.writer(out)
    writer.writerow(["database", "title", "query_text", "executed_at",
                     "hit_count", "hit_source", "version", "status"])
    for row in rows:
        writer.writerow([_csv_safe(row[key]) for key in (
            "database", "title", "query_text", "executed_at", "hit_count",
            "hit_source", "version", "status")])
    return out.getvalue()


def prisma_prefill(conn: sqlite3.Connection, *,
                   only_with_counts: bool = True) -> dict[str, Any]:
    """定稿条目 → PRISMA 逐库识别行（只读返回，不写任何表单）。"""
    rows: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    seen: set[str] = set()
    for strategy in _final_rows(conn):
        database = strategy["database"]
        if database in seen:
            continue
        seen.add(database)
        if strategy["hit_count"] is None:
            warnings.append({"code": "search_strategy.hit_count_missing",
                             "params": {"strategy_id": strategy["id"],
                                        "database": database}})
            continue
        rows.append({
            "source_type": "database",
            "name": database_label(database),
            "n": int(strategy["hit_count"]),
            "strategy_id": strategy["id"],
            "hit_source": strategy["hit_source"] or "manual",
            "executed_at": strategy["executed_at"],
        })
    if only_with_counts:
        pass  # 当前口径即"仅有命中数的库出识别行"
    return {"rows": rows, "warnings": warnings, "filled": len(rows),
            "target_field": "identified_rows_db"}


# ---------------------------------------------------------------------------
# living-review 兼容层（旧载荷形状）
# ---------------------------------------------------------------------------

def _compat_row(row: Mapping[str, Any]) -> dict[str, Any]:
    database = str(row.get("database") or "other")
    return {
        "database": _COMPAT_DATABASE_MAP.get(database, database),
        "query_string": row.get("query_text") or "",
        "last_run_date": (str(row.get("last_run_date") or "")
                          or str(row.get("executed_at") or "")[:10]),
        "url": row.get("url") or "",
        "total_hits_last": int(row.get("hit_count") or 0),
        "saved_at": row.get("created_at") or "",
        "last_fetched_count": row.get("last_count"),
        "last_checked_at": row.get("last_check_at") or "",
        "strategy_id": row.get("id"),
        "status": row.get("status"),
        "subscribe": bool(row.get("subscribe")),
    }


def compat_records(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """订阅视图（旧 search_records 形状；只列 subscribe=1）。

    排序与版本链对齐：当前 final 在前，其次草稿，历史条目最后（历史条目
    在定稿推进时会自动退订，只有用户显式重新订阅才会出现）。
    """
    rows = conn.execute(
        "SELECT id, database, query_text, url, hit_count, created_at,"
        " last_count, last_check_at, last_run_date, executed_at, status, subscribe"
        " FROM search_strategies WHERE subscribe = 1"
        " ORDER BY CASE status WHEN 'final' THEN 0 WHEN 'draft' THEN 1"
        " ELSE 2 END, id ASC").fetchall()
    return [_compat_row(dict(row)) for row in rows]


def compat_upsert(conn: sqlite3.Connection, *, database: str, query_string: str,
                  last_run_date: str = "", total_hits_last: int = 0,
                  now: str | None = None) -> dict[str, Any]:
    """旧 save-search → 统一库。

    - 同库同检索式（忽略首尾空白）已有条目时就地更新订阅游标，不新建第二
      行；命中 final/historical 时**只改游标/订阅位**，绝不改写定稿证据
      （executed_at/hit_count 等由触发器锁定）。
    - 无匹配时新建 ``source=manual`` 草稿并立即订阅。
    """
    now = now or _now()
    mapped = _LEGACY_DATABASE_MAP.get(str(database or "").strip().lower())
    if mapped is None:
        raise SearchStrategyError("database_invalid", database=str(database))
    query = str(query_string or "").strip()
    if not query:
        raise SearchStrategyError("query_required")
    cursor_date = _clean_date(last_run_date)
    hit_count = int(total_hits_last or 0)
    with _WRITE_LOCK:
        _begin(conn)
        try:
            existing = conn.execute(
                "SELECT id, title, database, query_text, syntax_note, url,"
                " status, version, prev_id, snapshot_id, source, subscribe,"
                " last_check_at, last_count, last_run_date, hit_count,"
                " hit_source, hit_at, executed_at, note, created_by,"
                " created_at, updated_at"
                " FROM search_strategies"
                " WHERE database = ?"
                " AND TRIM(query_text, ' ' || CHAR(9) || CHAR(10) || CHAR(13)) = ?"
                " ORDER BY CASE status WHEN 'final' THEN 0 WHEN 'draft' THEN 1"
                " ELSE 2 END, id ASC LIMIT 1", (mapped, query)).fetchone()
            if existing is not None and existing["status"] == "draft":
                conn.execute(
                    "UPDATE search_strategies SET url = ?, subscribe = 1,"
                    " last_run_date = ?, hit_count = ?, hit_source = ?,"
                    " hit_at = ?, updated_at = ? WHERE id = ?",
                    (database_url(mapped, query), cursor_date, hit_count,
                     "manual" if hit_count else "",
                     now if hit_count else "", now, int(existing["id"])))
                sid = int(existing["id"])
            elif existing is not None:
                # 定稿/历史证据不可改：只更新订阅游标与开关。
                conn.execute(
                    "UPDATE search_strategies SET subscribe = 1,"
                    " last_run_date = ?, updated_at = ? WHERE id = ?",
                    (cursor_date, now, int(existing["id"])))
                sid = int(existing["id"])
            else:
                label = database_label(mapped)
                title = f"{label} 订阅检索式：{query[:40]}"
                cursor = conn.execute(
                    "INSERT INTO search_strategies (title, database, query_text,"
                    " syntax_note, url, status, version, prev_id, snapshot_id,"
                    " source, subscribe, last_check_at, last_count, last_run_date,"
                    " legacy_fingerprint, hit_count, hit_source, hit_at,"
                    " executed_at, note, created_by, created_at, updated_at)"
                    " VALUES (?, ?, ?, '', ?, 'draft', 1, NULL, NULL, 'manual', 1,"
                    " '', NULL, ?, '', ?, ?, ?, '', '', 'manual', ?, ?)",
                    (title, mapped, query, database_url(mapped, query),
                     cursor_date, hit_count, "manual" if hit_count else "",
                     now if hit_count else "", now, now))
                sid = int(cursor.lastrowid)
            _commit(conn)
        except Exception:
            _rollback(conn)
            raise
    created = get_strategy(conn, sid)
    return _compat_row(created)


def resolve_check_target(conn: sqlite3.Connection, *,
                         strategy_id: int | None = None,
                         query_text: str = "",
                         database: str | None = None) -> dict[str, Any] | None:
    """check-now 的条目解析：优先显式 id；否则按检索式精确匹配订阅条目。

    匹配用 ``TRIM(query_text, 空白集)`` 对调用方去空白后的检索式（旧版原样
    存过带首尾空白/换行的记录）；``database`` 给定时只在该库内匹配（旧
    save-search 客户端只保存/检查 PubMed，必须保持 HEAD 行为，不能被同文本
    的 Embase/其他条目抢占）。多条命中时当前 final 优先。
    """
    if strategy_id is not None:
        row = conn.execute(
            "SELECT id, title, database, query_text, syntax_note, url, status,"
            " version, prev_id, snapshot_id, source, subscribe, last_check_at,"
            " last_count, last_run_date, hit_count, hit_source, hit_at,"
            " executed_at, note, created_by, created_at, updated_at"
            " FROM search_strategies WHERE id = ?",
            (int(strategy_id),)).fetchone()
        if row is None:
            raise SearchStrategyError("strategy_not_found", id=int(strategy_id))
        return _strategy(row)
    query = str(query_text or "").strip()
    if not query:
        return None
    if database is not None:
        row = conn.execute(
            "SELECT id, title, database, query_text, syntax_note, url, status,"
            " version, prev_id, snapshot_id, source, subscribe, last_check_at,"
            " last_count, last_run_date, hit_count, hit_source, hit_at,"
            " executed_at, note, created_by, created_at, updated_at"
            " FROM search_strategies WHERE subscribe = 1 AND database = ?"
            " AND TRIM(query_text, ' ' || CHAR(9) || CHAR(10) || CHAR(13)) = ?"
            " ORDER BY CASE status WHEN 'final' THEN 0 WHEN 'draft' THEN 1"
            " ELSE 2 END, id ASC LIMIT 1", (database, query)).fetchone()
        return _strategy(row) if row is not None else None
    row = conn.execute(
        "SELECT id, title, database, query_text, syntax_note, url, status,"
        " version, prev_id, snapshot_id, source, subscribe, last_check_at,"
        " last_count, last_run_date, hit_count, hit_source, hit_at,"
        " executed_at, note, created_by, created_at, updated_at"
        " FROM search_strategies WHERE subscribe = 1"
        " AND TRIM(query_text, ' ' || CHAR(9) || CHAR(10) || CHAR(13)) = ?"
        " ORDER BY CASE status WHEN 'final' THEN 0 WHEN 'draft' THEN 1"
        " ELSE 2 END, id ASC LIMIT 1", (query,)).fetchone()
    return _strategy(row) if row is not None else None


def record_check(conn: sqlite3.Connection, strategy_id: int | None, *,
                 fetched: int, now: str | None = None) -> None:
    """增量检查后续写订阅游标（失败路径不调用）。

    只写 ``last_check_at/last_count/last_run_date``；``executed_at`` 是
    **定稿证据的"执行日期"**，由 finalize 写入后不可再被增量检查改写。
    """
    if strategy_id is None:
        return
    now = now or _now()
    with _WRITE_LOCK:
        _begin(conn)
        try:
            conn.execute(
                "UPDATE search_strategies SET last_check_at = ?, last_count = ?,"
                " last_run_date = ?, updated_at = ? WHERE id = ?",
                (now, int(fetched), now[:10], now, int(strategy_id)))
            _commit(conn)
        except Exception:
            _rollback(conn)
            raise


# ---------------------------------------------------------------------------
# W13 迁移（一次性、幂等、备份 + 轨迹；旧文件只读不删）
# ---------------------------------------------------------------------------

def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _meta_get(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM search_strategy_meta WHERE key = ?",
                       (key,)).fetchone()
    return str(row["value"]) if row is not None else None


def _meta_set(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO search_strategy_meta (key, value) VALUES (?, ?)"
        " ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))


def migration_info(conn: sqlite3.Connection) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT source, rows_before, rows_migrated, rows_after, backup_path,"
        " backup_sha256, source_sha256, migrated_at, detail_json"
        " FROM search_strategy_migrations ORDER BY id DESC LIMIT 1").fetchone()
    if row is None:
        return None
    info = dict(row)
    info["migrated"] = True
    try:
        info["detail"] = json.loads(info.pop("detail_json") or "{}")
    except ValueError:
        info["detail"] = {}
    return info


def _legacy_records(state: Any) -> list[Any]:
    if not isinstance(state, Mapping):
        return []
    records = state.get("search_records")
    return list(records) if isinstance(records, list) else []


def _legacy_fingerprint(index: int, record: Mapping[str, Any]) -> str:
    """旧记录稳定指纹：序号 + 规范化载荷。

    序号参与哈希，保证**两条完全相同的旧记录也各得一行**（行数守恒）；
    二跑时序号不变 → 指纹不变 → 不重复插入。
    """
    payload = json.dumps(dict(record), ensure_ascii=False, sort_keys=True)
    return _sha256_bytes(f"{index}:{payload}".encode("utf-8"))


def _migrated_row(conn: sqlite3.Connection, fingerprint: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM search_strategies WHERE source = 'legacy_subscription'"
        " AND legacy_fingerprint = ? LIMIT 1", (fingerprint,)).fetchone() is not None


def _insert_legacy_row(conn: sqlite3.Connection, record: Mapping[str, Any],
                        now: str, fingerprint: str) -> None:
    # 旧记录可能缺字段/被手改：逐字段兜底，保证"一条旧记录 = 一条新行"。
    database = _LEGACY_DATABASE_MAP.get(
        str(record.get("database") or "").strip().lower(), "other")
    query = str(record.get("query_string") or "")
    last_run = str(record.get("last_run_date") or "").strip()
    if not _DATE_RE.match(last_run):
        last_run = last_run[:10] if _DATE_RE.match(last_run[:10]) else ""
    saved_at = str(record.get("saved_at") or "").strip() or now
    total = record.get("total_hits_last")
    try:
        total = int(total) if total is not None else 0
    except (TypeError, ValueError):
        total = 0
    if total < 0:
        total = 0
    fetched = record.get("last_fetched_count")
    try:
        fetched = int(fetched) if fetched is not None else total
    except (TypeError, ValueError):
        fetched = total
    if fetched < 0:
        fetched = total
    label = database_label(database)
    snippet = " ".join(query.split())[:40]
    title = f"{label} 订阅检索式：{snippet}" if snippet else f"{label} 订阅检索式"
    conn.execute(
        "INSERT INTO search_strategies (title, database, query_text,"
        " syntax_note, url, status, version, prev_id, snapshot_id, source,"
        " subscribe, last_check_at, last_count, last_run_date,"
        " legacy_fingerprint, hit_count, hit_source, hit_at, executed_at, note,"
        " created_by, created_at, updated_at)"
        " VALUES (?, ?, ?, '', ?, 'final', 1, NULL, NULL,"
        " 'legacy_subscription', 1, ?, ?, ?, ?, ?, 'manual', ?, ?, '',"
        " 'legacy_migration', ?, ?)",
        (title, database, query, database_url(database, query),
         str(record.get("last_checked_at") or saved_at), fetched, last_run,
         fingerprint, total, saved_at if total else "", last_run, saved_at,
         now))


def migrate_legacy_saved_searches(task_dir: str | Path,
                                  conn: sqlite3.Connection, *,
                                  now: str | None = None) -> dict[str, Any]:
    """把旧 ``living_review.json`` 的 search_records 一次性并入统一库。

    纪律（方案书 §〇.4）：先备份（``.bak``，SHA-256 双向记录）→ 幂等标记 →
    轨迹表留痕 → 行数守恒。旧文件**只读**；二跑不重复插入。
    """
    now = now or _now()
    if _meta_get(conn, LEGACY_MARKER_KEY) is not None:
        return {"migrated": False, "reason": "already_migrated",
                "rows_after": _legacy_row_count(conn)}
    legacy_path = Path(task_dir) / LEGACY_FILENAME
    if not legacy_path.is_file():
        return {"migrated": False, "reason": "no_legacy_file", "rows_after": 0}
    try:
        raw = legacy_path.read_bytes()
        state = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        # 损坏的旧文件不阻断新功能；本次不迁移、不置标记（修复后重试）。
        return {"migrated": False, "reason": "legacy_unreadable", "rows_after": 0}
    records = _legacy_records(state)
    source_sha = _sha256_bytes(raw)
    backup_path = legacy_path.with_name(legacy_path.name + LEGACY_BACKUP_SUFFIX)
    backup_sha = ""
    if records:
        try:
            if not backup_path.is_file():
                shutil.copyfile(legacy_path, backup_path)
            backup_sha = _sha256_bytes(backup_path.read_bytes())
        except OSError as exc:
            raise SearchStrategyError("legacy_backup_failed", str(exc)) from exc
        if backup_sha != source_sha:
            raise SearchStrategyError("legacy_backup_failed", "checksum_mismatch")
    rows_migrated = 0
    with _WRITE_LOCK:
        _begin(conn)
        try:
            # 写锁内二次检查：两个并发请求都通过上面的只读检查时，只有一个
            # 能进入本临界区。
            if _meta_get(conn, LEGACY_MARKER_KEY) is not None:
                _rollback(conn)
                return {"migrated": False, "reason": "already_migrated",
                        "rows_after": _legacy_row_count(conn)}
            for index, record in enumerate(records):
                payload = record if isinstance(record, Mapping) else {
                    "database": "other", "query_string": str(record)}
                fingerprint = _legacy_fingerprint(index, payload)
                if _migrated_row(conn, fingerprint):
                    continue
                _insert_legacy_row(conn, payload, now, fingerprint)
                rows_migrated += 1
            rows_after = _legacy_row_count(conn)
            detail = {"records": len(records), "inserted": rows_migrated}
            conn.execute(
                "INSERT INTO search_strategy_migrations (source, rows_before,"
                " rows_migrated, rows_after, backup_path, backup_sha256,"
                " source_sha256, migrated_at, detail_json)"
                " VALUES ('living_review.json', ?, ?, ?, ?, ?, ?, ?, ?)",
                (len(records), rows_migrated, rows_after,
                 backup_path.name if records else "", backup_sha, source_sha,
                 now, json.dumps(detail, ensure_ascii=False, sort_keys=True)))
            _meta_set(conn, LEGACY_MARKER_KEY, json.dumps({
                "migrated_at": now, "rows_before": len(records),
                "rows_migrated": rows_migrated, "rows_after": rows_after,
                "backup_sha256": backup_sha,
            }, ensure_ascii=False, sort_keys=True))
            _commit(conn)
        except Exception:
            _rollback(conn)
            raise
    return {"migrated": True, "reason": "migrated", "rows_before": len(records),
            "rows_migrated": rows_migrated, "rows_after": rows_after,
            "backup_path": backup_path.name if records else "",
            "backup_sha256": backup_sha, "source_sha256": source_sha,
            "migrated_at": now}


def _legacy_row_count(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM search_strategies"
        " WHERE source = 'legacy_subscription'").fetchone()
    return int(row["n"] if row is not None else 0)
