"""撤稿/更正关注（Retraction Watch 检查）API（2.1.5 Wave G4/W9）。

权威设计：``out/release_plan/drafts/design_2.1.5_retraction_watch.md`` v1.0
§二（产品语义）/§三（数据模型与契约）/§四（后端改动清单 B1–B5）。
前端契约：``frontend_contract_2.1.5-w9-retraction-watch.md``。

四个端点（全部挂在任务下）：

- ``POST /api/tasks/{task_id}/retraction-check``
  ``{scope, cursor? | run_id?}`` → ``{processed, total, cursor, done, findings_delta, …}``。
  分批游标式（每批 50 条），仅属主/can_manage 可触发。
- ``GET  /api/tasks/{task_id}/retraction-runs``
- ``GET  /api/tasks/{task_id}/retraction-runs/{run_id}``
  run 清单/详情（含发现），**全员可读**——团队都该看到警示；两处都返回
  ``can_manage``（与触发闸同一判定），供前端决定历史视图丰富度/入口显隐。
- ``GET  /api/tasks/{task_id}/retraction-badge``
  ``zotero_key -> 最新发现类型`` 映射（跨 run 去重）。

存储（协作库 ``collaboration.db``，**可加、幂等**、旧版程序不查询即忽略）：
``retraction_runs``（每次运行一行，append-only）与 ``retraction_findings``
（发现行；主键 ``(run_id, zotero_key, doi, update_type, source_doi)``——去重
语义仍是「同一篇文献 + 同一 DOI + 同一更新类型 + 同一来源 DOI 只记一次」，
加上文献键是为了让共享同一 DOI 的重复题录**各自**都能拿到徽标）。重复运行 =
新 run，旧 run 与其发现**绝不改写**（历史可查）。

治理红线落实：

- **出站仅配置的 Crossref base**（默认 ``https://api.crossref.org``）：复用
  ``custom_backend.pdf_fetch_api`` 的 ``crossref_base_url`` 配置键、白名单 URL
  构造（:func:`pdf_fetch_api.crossref_works_url`）、拒重定向 opener、15s 短超时
  与 2 MiB 读取上限——**不另建出站通道**。出站内容只有 DOI（题录标识符），
  不带摘要/全文/标题。DOI 经 ``quote(..., safe='')`` 全量编码且构造后再断言
  主机等于配置 base 主机，上游数据无法把请求导向其它主机。
- **显式手动触发、无定时无后台**：只有 HTTP 请求驱动；模块内**没有**线程、
  scheduler、asyncio 任务、``__del__`` 钩子或启动期预热。分批靠前端反复调用
  本端点（游标协议），不起后台常驻。
- **同一任务同时只有一个在途 run**：由协作库上的**唯一部分索引**
  ``idx_retraction_runs_active (task_id) WHERE status='running'`` 在数据库层
  保证（并发双击的第二个 INSERT 触发 IntegrityError → 409 ``run_in_progress``，
  与顺序调用同形）；不用进程内锁，也不因此引入线程原语。崩在中途的 run 可凭
  ``GET runs`` 的 ``active_run_id`` + ``run_id`` 续跑（不会永久卡死）。
- **绝不 500**：单条 DOI 的失败（超时/连接失败/上游状态/坏 JSON/形状异常）
  一律降级为 ``unknown`` 并继续；整批仍返回 200。存储故障映射 503 结构化错误。
- **结果=提示不是判决**：本模块**不写任何筛选员库**、不改任何决定、不删除
  文献。全流程前后 ``decisions`` / ``stage2_decisions`` 零变化（红钉覆盖）。
- **隐私**：只读本任务目录；出站只到 Crossref（自托管 base 可配，与 pdf_fetch
  同一信任边界——base 由负责人配置，不来自上游数据）。
- **诚实披露**：Crossref 只覆盖**有 DOI 且 Publisher 已上报**的文献；无 DOI
  条目计 ``unknown`` 且不出站；结果页披露语「未发现 ≠ 没有」（文案键
  ``retraction.disclosure_*``，原文见 ``docs/RETRACTION_WATCH.zh-CN.md``）。

错误形状统一为 ``{"error_code", "message_key": "retraction.<code>", "params"}``；
界面文案由前端词条渲染（新增文案只进结构化 message_key 或 docs）。
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator
from urllib.parse import quote

from fastapi import Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from coscreen import task_collaboration
from custom_backend import pdf_fetch_api

__all__ = ["register_retraction_routes"]

logger = logging.getLogger(__name__)

#: 单批处理的条目数（前端连续调用直到 ``done=true``）。
BATCH_SIZE = 50
#: 单次运行最多检查的**带 DOI**条目数（超出按文献键序截断并在结果中披露）。
MAX_DOIS_PER_RUN = 300
#: 礼貌节奏：两次出站之间的短间隔（秒）。测试置 0。
POLITE_INTERVAL_SECONDS = 0.1
#: 允许的范围口径。默认复筛纳入（设计 §二.1）。
SCOPES = ("stage2_include", "stage1_include")
DEFAULT_SCOPE = "stage2_include"

#: Crossref ``update-to`` / ``updated-by`` 的 type → 本模块的规范化类型名。
#: **只做拼写归一，不丢类型**：未知类型保留（降为 "other" 类），绝不因「不在
#: 白名单里」而漏报——漏掉一条撤稿比多显示一条同类提示糟糕得多。
_TYPE_ALIASES = {
    "expression_of_concern": "expression_of_concern",
    "expression-of-concern": "expression_of_concern",
    "expressionofconcern": "expression_of_concern",
    "partial_retraction": "partial_retraction",
    "partial-retraction": "partial_retraction",
}

#: 类型 → 等级类（决定徽标颜色与严重度）。
#: 负责人 2026-11-08 裁定：**撤稿类**（retraction / partial_retraction /
#: withdrawal / removal）一律最高、徽标红；编辑关切橙；更正类与**一切未知
#: 类型**最低、徽标灰——未知类型不得高于撤稿类，也不得被丢弃。
_TYPE_CLASS = {
    "retraction": "retraction",
    "partial_retraction": "retraction",
    "withdrawal": "retraction",
    "removal": "retraction",
    "expression_of_concern": "expression_of_concern",
    "correction": "correction",
    "erratum": "correction",
    "corrigendum": "correction",
}
#: 未登记类型所属的类别（最低档）。
DEFAULT_CLASS = "other"
#: 类别 → 严重度（徽标着色：3 红 / 2 橙 / 1 灰）。
SEVERITY_BY_CLASS = {"retraction": 3, "expression_of_concern": 2,
                     "correction": 1, "other": 1}
#: 类型名长度上限（Crossref 的 type 是短词；防御畸形载荷）。
_MAX_TYPE_LEN = 40

#: 披露语键（原文只在 docs 与前端词典；后端只回键）。
DISCLOSURE_KEYS = (
    "retraction.disclosure_not_found_is_not_none",
    "retraction.disclosure_no_doi_unavailable",
    "retraction.disclosure_no_auto_removal",
    "retraction.disclosure_crossref_coverage",
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS retraction_runs (
  run_id         INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id        TEXT    NOT NULL,
  scope          TEXT    NOT NULL,
  status         TEXT    NOT NULL,          -- running | done
  cursor_pos     INTEGER NOT NULL DEFAULT 0,
  total_count    INTEGER NOT NULL DEFAULT 0,
  eligible_count INTEGER NOT NULL DEFAULT 0,
  truncated      INTEGER NOT NULL DEFAULT 0,
  remaining_dois INTEGER NOT NULL DEFAULT 0,
  checked_count  INTEGER NOT NULL DEFAULT 0,
  found_count    INTEGER NOT NULL DEFAULT 0,
  unknown_count  INTEGER NOT NULL DEFAULT 0,
  started_by     TEXT    NOT NULL,
  started_at     TEXT    NOT NULL,
  finished_at    TEXT,
  targets_json   TEXT    NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS idx_retraction_runs_task
  ON retraction_runs(task_id, run_id);
CREATE TABLE IF NOT EXISTS retraction_findings (
  run_id       INTEGER NOT NULL,
  zotero_key   TEXT    NOT NULL,
  doi          TEXT    NOT NULL,
  update_type  TEXT    NOT NULL,
  source_doi   TEXT    NOT NULL DEFAULT '',
  source_date  TEXT    NOT NULL DEFAULT '',
  source_title TEXT    NOT NULL DEFAULT '',
  url          TEXT    NOT NULL DEFAULT '',
  found_at     TEXT    NOT NULL,
  PRIMARY KEY (run_id, zotero_key, doi, update_type, source_doi)
);
CREATE INDEX IF NOT EXISTS idx_retraction_findings_key
  ON retraction_findings(zotero_key);
-- 同一任务同时只允许一个在途 run：唯一部分索引在**数据库层**挡住并发双击
-- 造成的双 run（不靠进程内锁——本模块刻意不引入线程原语）。
CREATE UNIQUE INDEX IF NOT EXISTS idx_retraction_runs_active
  ON retraction_runs(task_id) WHERE status = 'running';
"""

#: 游标形状 ``{run_id}:{offset}``（正整数；offset 允许 0）。
_CURSOR_RE = re.compile(r"^([1-9][0-9]{0,18}):(0|[1-9][0-9]{0,8})$")
_KEY_CAP = 300                       # 与 main._KEY_RE 同量级；仅作防御
_MAX_STORED_TEXT = 300               # 来源标题/日期入库截断


# ---------------------------------------------------------------------------
# 纯函数：类型归一 / 更新关系解析（零 I/O，单测直接钉）
# ---------------------------------------------------------------------------

def canonical_type(raw: Any) -> str:
    """Crossref 更新类型归一：**永不丢弃**。

    先做拼写别名归一（见 :data:`_TYPE_ALIASES`），再把任意其余写法压成
    ``[a-z0-9_]`` 记号（长度有界）；缺失/空值 → ``"unknown"``。未知类型保留
    原名并从属于 "other" 类（最低档灰），这样「不在白名单」也不会变成漏报。
    """
    text = " ".join(str(raw or "").strip().lower().split())
    if not text:
        return "unknown"
    if text in _TYPE_ALIASES:
        return _TYPE_ALIASES[text]
    token = re.sub(r"[^a-z0-9]+", "_", text).strip("_")[:_MAX_TYPE_LEN]
    return token or "unknown"


def update_class(update_type: str) -> str:
    """类型 → 等级类（retraction / expression_of_concern / correction / other）。"""
    return _TYPE_CLASS.get(str(update_type or ""), DEFAULT_CLASS)


def severity_of(update_type: str) -> int:
    """类型 → 严重度（3 红 / 2 橙 / 1 灰）；未知类型落 "other" 类 = 1。"""
    return SEVERITY_BY_CLASS[update_class(update_type)]


def _entry_date(entry: dict) -> str:
    """更新条目的日期：优先 ``updated``/``created`` 的 date-parts → ``YYYY-MM-DD``。"""
    for field in ("updated", "created", "published"):
        node = entry.get(field)
        if not isinstance(node, dict):
            continue
        parts = node.get("date-parts")
        if not isinstance(parts, list) or not parts:
            continue
        row = parts[0] if isinstance(parts[0], list) else None
        if not row or not isinstance(row[0], int):
            continue
        year = int(row[0])
        month = int(row[1]) if len(row) > 1 and isinstance(row[1], int) else 0
        day = int(row[2]) if len(row) > 2 and isinstance(row[2], int) else 0
        if not 1000 <= year <= 9999:
            continue
        if month:
            return f"{year:04d}-{month:02d}-{day:02d}" if day else f"{year:04d}-{month:02d}"
        return f"{year:04d}"
    return ""


def _iter_update_entries(message: dict) -> Iterator[tuple[str, dict]]:
    """产出 ``(direction, entry)``；direction ∈ {``update-to``, ``updated-by``}。

    Crossref 把更新关系放在顶层 ``update-to`` / ``updated-by``，部分记录只在
    ``relationship`` 子对象里给出——两处都读（重复项由去重键吸收）。
    """
    containers: list[dict] = [message]
    relationship = message.get("relationship")
    if isinstance(relationship, dict):
        containers.append(relationship)
    for container in containers:
        for direction in ("update-to", "updated-by"):
            entries = container.get(direction)
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if isinstance(entry, dict):
                    yield direction, entry


def parse_update_findings(message: dict, doi: str,
                          checked_dois: set[str]) -> list[dict]:
    """从一条 works ``message`` 解析受影响的 DOI 及其更新类型。

    方向语义（关键，勿混）：某条记录 X 的 ``update-to`` 表示「X 是这些作品的
    更新通告」，被更新的作品才受影响；``updated-by`` 表示「X 被这些作品更新」，
    X 自己受影响。因此 ``update-to`` 的受影响方是条目里的 DOI（来源=X），
    ``updated-by`` 的受影响方是 X（来源=条目 DOI）。

    ``checked_dois`` 是本批**已在检查范围内**的 DOI 集合（小写）：只有受影响的
    DOI 落在其中才产出发现——否则「我们检查的通告 DOI 指向范围外作品」会被
    误标成该通告本身有问题。
    """
    checked_here = {value.lower() for value in checked_dois}
    self_doi = (doi or "").lower()
    findings: list[dict] = []
    for direction, entry in _iter_update_entries(message):
        # 未知类型**不丢**：归一到 "other" 类（最低档灰），见 canonical_type。
        update_type = canonical_type(entry.get("type"))
        entry_doi = pdf_fetch_api.normalize_doi(entry.get("DOI")) or ""
        if direction == "updated-by":
            affected, source = self_doi, entry_doi
        else:
            affected, source = entry_doi.lower(), self_doi
        if not affected or (affected == self_doi and direction == "update-to"):
            continue
        if affected not in checked_here:
            continue
        findings.append({
            "doi": affected,
            "update_type": update_type,
            "source_doi": source,
            "source_date": _entry_date(entry)[:_MAX_STORED_TEXT],
            "source_title": str(entry.get("label") or "")[:_MAX_STORED_TEXT],
            "url": f"https://doi.org/{source}" if source else "",
        })
    return findings


def _unknown_reason(exc: BaseException | str) -> str:
    """出站失败 → 结构化原因桶（不回落文案；界面按 message_key 渲染）。"""
    reason = exc.reason if isinstance(exc, pdf_fetch_api.OutboundError) else str(exc)
    if reason in ("timeout", "unreachable", "upstream_status", "bad_response",
                  "too_large", "invalid_doi", "unsafe_url", "private_host"):
        return reason
    return "outbound_failed"


# ---------------------------------------------------------------------------
# 存储（协作库；可加幂等）
# ---------------------------------------------------------------------------

def _store_path(info: Any) -> Path:
    return task_collaboration.shared_store_path(info.dir)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _storage_http_error() -> HTTPException:
    """存储不可用（503 结构化错误；锁忙/损坏/IO）。

    **模块级**：``_read_articles`` / ``_doi_by_key`` 等模块函数在闭包之外，不能
    引用 ``register_retraction_routes`` 里的内部 helper（否则筛选员库被锁时抛
    NameError → 500，违反「绝不 500」与契约 §四的 503 口径）。
    """
    return HTTPException(503, {
        "error_code": "storage_unavailable",
        "message_key": "retraction.storage_unavailable",
        "params": {},
    })


def _ro_uri(path: Path) -> str:
    return f"file:{quote(str(Path(path).resolve()))}?mode=ro"


def _connect_ro(path: Path) -> sqlite3.Connection:
    """只读连接（GET 路径专用：绝不建库/建表/写任何字节）。"""
    conn = sqlite3.connect(_ro_uri(path), uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def _connect_rw(info: Any) -> sqlite3.Connection:
    """协作库读写连接并幂等补建两表（只加不改，旧版忽略）。"""
    conn = sqlite3.connect(str(_store_path(info)), timeout=15)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def _run_payload(row) -> tuple[list[dict], dict]:
    """解析 ``targets_json``：新形态 ``{"meta": …, "items": […]}}``；旧纯 list 兜底。

    窗口元数据（``offset`` / ``next_offset``）随目标清单一同冻结在 run 行里，
    因此续跑与历史回看都不需要额外的表列。
    """
    try:
        data = json.loads(row["targets_json"] or "[]")
    except (TypeError, ValueError):
        return [], {}
    if isinstance(data, dict):
        items = data.get("items")
        meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
    elif isinstance(data, list):
        items, meta = data, {}
    else:
        return [], {}
    if not isinstance(items, list):
        return [], meta
    return [item for item in items
            if isinstance(item, dict) and isinstance(item.get("key"), str)
            and len(item["key"]) <= _KEY_CAP], meta


def _run_meta(row) -> dict:
    return _run_payload(row)[1]


# ---------------------------------------------------------------------------
# 范围口径：当前生效决定（唯一实现由 W5 ``task_package_api`` 提供）
# ---------------------------------------------------------------------------

def _effective_decisions(info: Any, stage: str) -> dict[str, str]:
    """复用 W5 的「当前生效决定」实现（scope 口径唯一事实源，避免口径漂移）。

    跨模块引用一个私有函数是刻意的：复制一份 70 行的仲裁/过期快照逻辑会立刻
    产生第二套口径，而这正是设计要防的。若 W5 重命名/改签名，W9 测试会红。
    """
    from custom_backend import task_package_api

    return task_package_api._effective_decisions(info, stage)


def _read_articles(path: Path) -> list[tuple]:
    try:
        with closing(_connect_ro(path)) as conn:
            return conn.execute(
                "SELECT zotero_key, doi FROM articles").fetchall()
    except sqlite3.OperationalError as exc:
        text = str(exc).lower()
        if "locked" in text or "busy" in text:
            raise _storage_http_error() from None
        return []                    # 缺表等其余形态按空表降级
    except sqlite3.DatabaseError:
        return []
    except OSError:
        raise _storage_http_error() from None


def _doi_by_key(info: Any) -> dict[str, str]:
    """``zotero_key -> 规范化 DOI``（跨筛选员库首个非空值胜出）。"""
    mapping: dict[str, str] = {}
    for db_file in info.db_files:
        for row in _read_articles(db_file):
            if not row or not row[0]:
                continue
            key = str(row[0])
            if mapping.get(key):
                continue
            doi = pdf_fetch_api.normalize_doi(row[1])
            if doi:
                mapping[key] = doi
    return mapping


def _scope_targets(info: Any, scope: str, offset: int = 0
                   ) -> tuple[list[dict], dict[str, int]]:
    """按 scope 生成目标清单（确定性顺序）+ 窗口统计。

    返回 ``(targets, stats)``；``targets`` 元素 ``{"key", "doi"|None}``。

    窗口口径（r2 复审）：带 DOI 的条目按 ``zotero_key`` 顺序排好后，每次运行只
    取 ``[offset, offset + MAX_DOIS_PER_RUN)`` 这一段——``offset`` 由请求给出，
    使「剩下的条目」**真的可以继续检查**（``next_offset`` 告诉前端下一窗起点），
    而不是每次重跑都取同一段前 300 条。无 DOI 条目不出站、不占 DOI 名额，
    每个窗口都计入 unknown。
    """
    stage = "stage1" if scope == "stage1_include" else "stage2"
    effective = _effective_decisions(info, stage)
    keys = sorted(key for key, decision in effective.items() if decision == "include")
    doi_map = _doi_by_key(info)
    with_doi = [{"key": key, "doi": doi_map.get(key)} for key in keys]
    doi_entries = [item for item in with_doi if item["doi"]]
    doi_total = len(doi_entries)
    window = doi_entries[offset:offset + MAX_DOIS_PER_RUN]
    keep = {item["key"] for item in window}
    remaining = max(0, doi_total - (offset + len(window)))
    targets = [item for item in with_doi
               if item["doi"] is None or item["key"] in keep]
    stats = {
        "eligible": len(keys),
        "offset": offset,
        "dois_total": doi_total,
        "truncated": 1 if remaining else 0,
        "remaining": remaining,
        "next_offset": (offset + len(window)) if remaining else None,
    }
    return targets, stats


# ---------------------------------------------------------------------------
# Crossref 逐 DOI 查询（单条失败降级 unknown，绝不 500）
# ---------------------------------------------------------------------------

def _pace() -> None:
    delay = POLITE_INTERVAL_SECONDS
    if delay > 0:
        time.sleep(delay)


def _check_one(base_url: str, doi: str,
               checked_dois: set[str]) -> tuple[list[dict], str | None]:
    """单 DOI 查询 → ``(findings, unknown_reason|None)``。任何异常都不外抛。"""
    try:
        message = pdf_fetch_api.fetch_crossref_work(base_url, doi)
    except pdf_fetch_api.OutboundError as exc:
        return [], _unknown_reason(exc)
    except (AssertionError, KeyboardInterrupt):
        raise                        # 程序/测试缺陷不降级（不自我掩蔽）
    except Exception as exc:         # noqa: BLE001  红线：绝不因上游形态 500
        logger.warning("撤稿检查出站异常（已降级 unknown）", exc_info=True)
        return [], _unknown_reason(type(exc).__name__)
    if message is None:
        return [], "not_indexed"
    try:
        return parse_update_findings(message, doi, checked_dois), None
    except Exception:                # noqa: BLE001  畸形元数据 → unknown，不 500
        logger.warning("撤稿检查解析异常（已降级 unknown）", exc_info=True)
        return [], "bad_response"


# ---------------------------------------------------------------------------
# 路由注册（依赖注入模式同 pdf_fetch_api / search_strategy_api）
# ---------------------------------------------------------------------------

class CheckIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope: str = Field(default=DEFAULT_SCOPE, max_length=40)
    #: 续跑游标（首调省略）。与 ``run_id`` 二选一。
    cursor: str | None = Field(default=None, max_length=40)
    #: 直接续跑指定 run（页面刷新后凭 GET runs 恢复；与 cursor 二选一）。
    run_id: int | None = Field(default=None, ge=1)
    #: 新运行的窗口起点（0 起）。``truncated=true`` 时前端用返回的
    #: ``next_offset`` 再发一次即可检查剩余条目；续跑时忽略（窗口已冻结）。
    offset: int = Field(default=0, ge=0, le=1_000_000)


def register_retraction_routes(app, require_user, task_lookup, task_visible,
                               server_mode: Callable[[], bool]) -> None:
    """注册 W9 撤稿/更正关注路由（main.py 注册区追加式一行调用）。"""

    # ---- 结构化错误 -------------------------------------------------------
    def _error(status: int, code: str, params: dict | None = None) -> HTTPException:
        return HTTPException(status, {
            "error_code": code,
            "message_key": "retraction." + code,
            "params": params or {},
        })

    #: 模块级 `_storage_http_error` 的闭包别名（读筛选员库的模块级 helper 也要用）。
    _storage_error = _storage_http_error

    def _info(task_id: str, user: dict):
        info = task_lookup(task_id)
        try:
            visible = task_visible(info, user)
        except task_collaboration.OwnerUnavailableError:
            raise _storage_error() from None
        if not visible:
            raise _error(403, "task_forbidden")
        return info

    def _manage_state(info: Any, user: dict) -> tuple[bool, tuple[int, str] | None]:
        """触发闸的**唯一判定源**：返回 ``(can_manage, 拒绝码|None)``。

        ``POST retraction-check`` 的放行/拒绝与 ``GET runs`` / 详情里的
        ``can_manage`` 布尔都由本函数给出（同一函数，不复制口径）：

        - 属主 → ``(True, None)``；
        - 普通成员 → ``(False, (403, "owner_required"))``；
        - server 模式 legacy（无属主记录）→ ``(False, (409, "legacy_owner_unset"))``
          （纵深防御：实际请求会先被全局闸门以 ``collaboration_owner_unset``
          拦下，见 I-18）；
        - desktop 模式 legacy → ``(True, None)``（既有口径：单机本人全权）。
        """
        try:
            owner = task_collaboration.get_owner(
                _store_path(info), task_id=info.task_id)
        except task_collaboration.OwnerUnavailableError:
            raise _storage_error() from None
        if owner is None:
            if server_mode():
                return False, (409, "legacy_owner_unset")
            return True, None
        if owner["owner_user_id"] != user["user_id"]:
            return False, (403, "owner_required")
        return True, None

    def _owner(info: Any, user: dict) -> None:
        """触发闸：属主/can_manage（判定见 :func:`_manage_state`）。"""
        _, denial = _manage_state(info, user)
        if denial is not None:
            status, code = denial
            raise _error(status, code)

    # ---- 存储小工具 -------------------------------------------------------
    def _run_row(conn: sqlite3.Connection, task_id: str, run_id: int):
        return conn.execute(
            "SELECT * FROM retraction_runs WHERE run_id=? AND task_id=?",
            (run_id, task_id)).fetchone()

    def _active_params(conn, task_id: str) -> dict:
        """在途 run 的续跑参数（409 ``run_in_progress`` 的 params）。"""
        row = conn.execute(
            "SELECT run_id, cursor_pos, scope FROM retraction_runs "
            "WHERE task_id=? AND status='running' ORDER BY run_id DESC LIMIT 1",
            (task_id,)).fetchone()
        if row is None:
            return {}
        return {
            "run_id": int(row["run_id"]),
            "scope": str(row["scope"]),
            "cursor": _encode_cursor(int(row["run_id"]), int(row["cursor_pos"])),
        }

    def _serialize_run(row) -> dict:
        meta = _run_meta(row)
        return {
            "run_id": int(row["run_id"]),
            "scope": str(row["scope"]),
            "status": str(row["status"]),
            "checked_count": int(row["checked_count"]),
            "found_count": int(row["found_count"]),
            "unknown_count": int(row["unknown_count"]),
            "total_count": int(row["total_count"]),
            "eligible_count": int(row["eligible_count"]),
            "offset": int(meta.get("offset") or 0),
            "next_offset": meta.get("next_offset"),
            "truncated": bool(row["truncated"]),
            "remaining_dois": int(row["remaining_dois"]),
            "cursor": (None if str(row["status"]) == "done"
                       else _encode_cursor(int(row["run_id"]), int(row["cursor_pos"]))),
            "started_by": str(row["started_by"]),
            "started_at": str(row["started_at"]),
            "finished_at": row["finished_at"],
        }

    def _serialize_finding(row) -> dict:
        return {
            "zotero_key": str(row["zotero_key"]),
            "doi": str(row["doi"]),
            "update_type": str(row["update_type"]),
            "source_doi": str(row["source_doi"]),
            "source_date": str(row["source_date"]),
            "source_title": str(row["source_title"]),
            "url": str(row["url"]),
            "found_at": str(row["found_at"]),
        }

    def _progress_payload(row, *, processed_delta: int, findings_delta: int,
                          unknown_delta: int) -> dict:
        status = str(row["status"])
        done = status == "done"
        meta = _run_meta(row)
        return {
            "run_id": int(row["run_id"]),
            "scope": str(row["scope"]),
            "status": status,
            "processed": int(row["cursor_pos"]),
            "processed_delta": processed_delta,
            "total": int(row["total_count"]),
            "cursor": (None if done else
                       _encode_cursor(int(row["run_id"]), int(row["cursor_pos"]))),
            "done": done,
            "findings_delta": findings_delta,
            "unknown_delta": unknown_delta,
            "found_count": int(row["found_count"]),
            "unknown_count": int(row["unknown_count"]),
            "truncated": bool(row["truncated"]),
            "remaining_dois": int(row["remaining_dois"]),
            "offset": int(meta.get("offset") or 0),
            "next_offset": meta.get("next_offset"),
            "disclosure_keys": list(DISCLOSURE_KEYS),
            "message_key": ("retraction.check_done" if done
                            else "retraction.check_progress"),
        }

    # ---- 1. 触发/续跑 -----------------------------------------------------
    @app.post("/api/tasks/{task_id}/retraction-check")
    def retraction_check(task_id: str, body: CheckIn,
                         user: dict = Depends(require_user)) -> dict:
        if body.scope not in SCOPES:
            raise _error(400, "scope_invalid", {"scopes": list(SCOPES)})
        if body.cursor is not None and body.run_id is not None:
            raise _error(400, "cursor_conflict")

        try:
            info = _info(task_id, user)
            _owner(info, user)
        except HTTPException:
            raise
        except (sqlite3.Error, OSError):
            raise _storage_error() from None

        try:
            conn = _connect_rw(info)
        except (sqlite3.Error, OSError):
            raise _storage_error() from None

        try:
            with closing(conn):
                # --- 定位或新建 run -------------------------------------
                if body.run_id is not None or body.cursor is not None:
                    if body.run_id is not None:
                        run_id, offset = body.run_id, None
                    else:
                        match = _CURSOR_RE.match(body.cursor or "")
                        if not match:
                            raise _error(400, "cursor_invalid")
                        run_id = int(match.group(1))
                        offset = int(match.group(2))
                    row = _run_row(conn, info.task_id, run_id)
                    if row is None:
                        raise _error(404, "run_not_found")
                    if str(row["scope"]) != body.scope:
                        raise _error(409, "scope_mismatch",
                                     {"run_scope": str(row["scope"])})
                    current = int(row["cursor_pos"])
                    if offset is None or offset == current:
                        offset = current
                    elif offset < current:
                        # 幂等重放：该批已处理，直接回当前进度（不重复出站）。
                        return _progress_payload(
                            row, processed_delta=0,
                            findings_delta=0, unknown_delta=0)
                    else:
                        raise _error(409, "cursor_stale", {"cursor_pos": current})
                else:
                    active = conn.execute(
                        "SELECT run_id FROM retraction_runs "
                        "WHERE task_id=? AND status='running' "
                        "ORDER BY run_id DESC LIMIT 1",
                        (info.task_id,)).fetchone()
                    if active is not None:
                        raise _error(409, "run_in_progress",
                                     _active_params(conn, info.task_id))
                    targets, stats = _scope_targets(info, body.scope, body.offset)
                    started_at = _now()
                    meta = {"offset": stats["offset"],
                            "dois_total": stats["dois_total"],
                            "next_offset": stats["next_offset"]}
                    try:
                        conn.execute(
                            "INSERT INTO retraction_runs "
                            "(task_id, scope, status, cursor_pos, total_count, "
                            " eligible_count, truncated, remaining_dois, checked_count, "
                            " found_count, unknown_count, started_by, started_at, "
                            " finished_at, targets_json) "
                            "VALUES (?,?,'running',0,?,?,?,?,0,0,0,?,?,NULL,?)",
                            (info.task_id, body.scope, len(targets),
                             stats["eligible"], stats["truncated"], stats["remaining"],
                             user["username"], started_at,
                             json.dumps({"meta": meta, "items": targets},
                                        ensure_ascii=False)))
                    except sqlite3.IntegrityError:
                        # 并发双击：唯一部分索引已挡住第二个在途 run。回读在途
                        # run 返回与顺序调用一致的 409（不是 500）。
                        raise _error(409, "run_in_progress",
                                     _active_params(conn, info.task_id)) from None
                    run_id = int(conn.execute(
                        "SELECT last_insert_rowid() AS rid").fetchone()["rid"])
                    conn.commit()
                    row = _run_row(conn, info.task_id, run_id)
                    offset = 0

                if str(row["status"]) == "done":
                    # 已完成的 run：重复调用是幂等空操作。
                    return _progress_payload(row, processed_delta=0,
                                             findings_delta=0, unknown_delta=0)

                targets = _load_targets(row)
                checked_dois = {item["doi"].lower()
                                for item in targets if item.get("doi")}
                # DOI→文献键映射取**整个 run**（不是本批）：通告侧解析出的发现
                # 可能指向后面批次才检查的文献，必须现在就能落到它的名下。
                # 同一 DOI 可能对应多条重复题录——**每一条都要打徽标**（漏报
                # 比重复更糟：团队都该看到警示）。
                keys_by_doi: dict[str, list[str]] = {}
                for item in targets:
                    if item.get("doi"):
                        keys_by_doi.setdefault(
                            item["doi"].lower(), []).append(item["key"])
                batch = targets[offset:offset + BATCH_SIZE]
                payload = _run_batch(info, conn, row, batch, checked_dois,
                                     keys_by_doi)
                return payload
        except HTTPException:
            raise
        except (sqlite3.Error, OSError):
            raise _storage_error() from None

    # ---- 批处理（事务内写发现 + 推进游标） --------------------------------
    def _load_targets(row) -> list[dict]:
        return _run_payload(row)[0]

    def _run_batch(info, conn, row, batch, checked_dois, keys_by_doi) -> dict:
        run_id = int(row["run_id"])
        try:
            base_url = pdf_fetch_api.crossref_base_for(info)
        except Exception:            # noqa: BLE001  设置文件损毁 → 用默认端点
            base_url = pdf_fetch_api.DEFAULT_CROSSREF_BASE

        inserted = 0
        unknown_delta = 0
        failed: list[dict] = []
        #: 同一批内同一 DOI 只出站一次（重复题录共享一次查询，发现落到每篇名下）。
        memo: dict[str, tuple[list[dict], str | None]] = {}
        outbound_done = False
        for item in batch:
            doi = item.get("doi")
            if not doi:
                unknown_delta += 1          # 无 DOI：不出站，直接计 unknown
                continue
            low = doi.lower()
            if low not in memo:
                if outbound_done:
                    _pace()
                outbound_done = True
                memo[low] = _check_one(base_url, doi, checked_dois)
            findings, reason = memo[low]
            if reason is not None:
                failed.append({"key": item["key"], "doi": doi, "reason": reason})
                continue
            for finding in findings:
                for key in keys_by_doi.get(finding["doi"], ()):
                    inserted += _insert_finding(conn, run_id, key, finding)

        # 失败条目里若本 run 已有发现（例如由另一条记录的通告解析得到），
        # 就不重复计入 unknown——它其实已被核实。
        if failed:
            known = {str(r["doi"]).lower() for r in conn.execute(
                "SELECT DISTINCT doi FROM retraction_findings WHERE run_id=?",
                (run_id,))}
            unknown_delta += sum(1 for item in failed
                                 if item["doi"].lower() not in known)

        # review67 L-28：游标的读-改-写必须与出站（可达数分钟）隔离后原子
        # 落库。此前直接按**出站前的陈旧基线** UPDATE+commit：并发续跑同一
        # run 会各自重复出站同一批、迟到提交把 cursor_pos 写回旧值（进度
        # 回归），旧 run 已 done 时迟到 UPDATE 置回 running 还会撞部分唯一
        # 索引（idx_retraction_runs_active）被外层误映射成 503 伪存储故障。
        # 现在先提交本批发现（INSERT OR IGNORE 幂等，重放安全），再
        # BEGIN IMMEDIATE 抢写锁并**重读** run 行，按最新游标决定回写或跳过。
        conn.commit()
        conn.execute("BEGIN IMMEDIATE")
        baseline = int(row["cursor_pos"])
        intended = baseline + len(batch)
        fresh_lock = _run_row(conn, info.task_id, run_id)
        if (fresh_lock is None or str(fresh_lock["status"]) == "done"
                or int(fresh_lock["cursor_pos"]) > baseline):
            # 并发写入者已推进过本批读时基线（或已收尾）：回写只会倒退
            # 进度/复活已 done 的 run。幂等跳过（与顺序重放同一回报口径）；
            # fresh == baseline（含空批 0==0）时本请求仍是首个写者，照常
            # 回写（空批也能把 total==0 的 run 置 done）。
            conn.rollback()
            return _progress_payload(fresh_lock or row, processed_delta=0,
                                     findings_delta=0, unknown_delta=0)
        new_offset = intended
        done = new_offset >= int(fresh_lock["total_count"])
        finished_at = _now() if done else None
        found_count = int(conn.execute(
            "SELECT COUNT(*) AS n FROM retraction_findings WHERE run_id=?",
            (run_id,)).fetchone()["n"])
        conn.execute(
            "UPDATE retraction_runs SET status=?, cursor_pos=?, checked_count=?, "
            "found_count=?, unknown_count=?, finished_at=? WHERE run_id=?",
            ("done" if done else "running", new_offset, new_offset, found_count,
             int(fresh_lock["unknown_count"]) + unknown_delta, finished_at, run_id))
        conn.commit()
        fresh = _run_row(conn, info.task_id, run_id)
        return _progress_payload(fresh, processed_delta=len(batch),
                                 findings_delta=inserted,
                                 unknown_delta=unknown_delta)

    def _insert_finding(conn, run_id: int, key: str, finding: dict) -> int:
        cur = conn.execute(
            "INSERT OR IGNORE INTO retraction_findings "
            "(run_id, zotero_key, doi, update_type, source_doi, source_date, "
            " source_title, url, found_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (run_id, key, finding["doi"], finding["update_type"],
             finding["source_doi"], finding["source_date"],
             finding["source_title"], finding["url"], _now()))
        return 1 if cur.rowcount and cur.rowcount > 0 else 0

    # ---- 2. run 清单 ------------------------------------------------------
    @app.get("/api/tasks/{task_id}/retraction-runs")
    def retraction_runs(task_id: str,
                        user: dict = Depends(require_user)) -> dict:
        info = _info(task_id, user)
        can_manage, _ = _manage_state(info, user)
        path = _store_path(info)
        runs: list[dict] = []
        if path.is_file():
            try:
                with closing(_connect_ro(path)) as conn:
                    try:
                        rows = conn.execute(
                            "SELECT * FROM retraction_runs WHERE task_id=? "
                            "ORDER BY run_id DESC LIMIT 200",
                            (info.task_id,)).fetchall()
                    except sqlite3.OperationalError:
                        rows = []       # 未建表 = 从未运行（空清单，不 500）
            except (sqlite3.Error, OSError):
                raise _storage_error() from None
            runs = [_serialize_run(row) for row in rows]
        return {
            "runs": runs,
            "count": len(runs),
            "active_run_id": next((item["run_id"] for item in runs
                                   if item["status"] == "running"), None),
            # 与 POST retraction-check 的属主/can_manage 闸同一判定函数；
            # 前端据此决定 F5 历史视图的丰富度与 F1 入口显隐。
            "can_manage": can_manage,
            "disclosure_keys": list(DISCLOSURE_KEYS),
        }

    # ---- 3. run 详情（含发现） --------------------------------------------
    @app.get("/api/tasks/{task_id}/retraction-runs/{run_id}")
    def retraction_run_detail(task_id: str, run_id: str,
                              user: dict = Depends(require_user)) -> dict:
        if not re.fullmatch(r"[1-9][0-9]{0,18}", run_id or ""):
            raise _error(404, "run_not_found")
        run_id_int = int(run_id)
        info = _info(task_id, user)
        can_manage, _ = _manage_state(info, user)
        path = _store_path(info)
        if not path.is_file():
            raise _error(404, "run_not_found")
        try:
            with closing(_connect_ro(path)) as conn:
                try:
                    row = conn.execute(
                        "SELECT * FROM retraction_runs WHERE task_id=? AND run_id=?",
                        (info.task_id, run_id_int)).fetchone()
                except sqlite3.OperationalError:
                    row = None
                if row is None:
                    raise _error(404, "run_not_found")
                try:
                    finding_rows = conn.execute(
                        "SELECT * FROM retraction_findings WHERE run_id=? "
                        "ORDER BY zotero_key, update_type, source_doi",
                        (run_id_int,)).fetchall()
                except sqlite3.OperationalError:
                    finding_rows = []
        except HTTPException:
            raise
        except (sqlite3.Error, OSError):
            raise _storage_error() from None
        findings = [_serialize_finding(item) for item in finding_rows]
        return {
            "run": _serialize_run(row),
            "findings": findings,
            "finding_count": len(findings),
            "can_manage": can_manage,
            "disclosure_keys": list(DISCLOSURE_KEYS),
        }

    # ---- 4. 徽标映射（跨 run 去重） ---------------------------------------
    @app.get("/api/tasks/{task_id}/retraction-badge")
    def retraction_badge(task_id: str,
                         user: dict = Depends(require_user)) -> dict:
        info = _info(task_id, user)
        path = _store_path(info)
        badges: dict[str, dict] = {}
        if path.is_file():
            try:
                with closing(_connect_ro(path)) as conn:
                    try:
                        rows = conn.execute(
                            "SELECT f.* FROM retraction_findings f "
                            "JOIN retraction_runs r ON r.run_id = f.run_id "
                            "WHERE r.task_id=? ORDER BY f.run_id DESC",
                            (info.task_id,)).fetchall()
                    except sqlite3.OperationalError:
                        rows = []
            except (sqlite3.Error, OSError):
                raise _storage_error() from None
            badges = _badge_map(rows)
        return {
            "badges": badges,
            "count": len(badges),
            "has_any": bool(badges),
            # 类别 → 严重度（3 红 / 2 橙 / 1 灰）；未知类型落 "other" = 1。
            "severity": dict(SEVERITY_BY_CLASS),
            "disclosure_keys": list(DISCLOSURE_KEYS),
        }

    def _badge_map(rows) -> dict[str, dict]:
        """跨 run 去重：同一 ``(文献键, doi, update_type, source_doi)`` 取最新
        run 的一条（文献键必须在去重键里——重复题录共享同一 DOI 时每一条都要
        拿到徽标）；同一文献多种类型时徽标取**最严重**的等级类（负责人
        2026-11-08 裁定：撤稿类红不会被后来的更正降级；未知类型与更正同级=灰）。

        同严重度时的并列：保留先到者（查询已 ``ORDER BY run_id DESC``，即最新
        run 的那条）。
        """
        by_key: dict[str, dict] = {}
        seen: set[tuple] = set()
        for row in rows:
            dedupe = (str(row["zotero_key"]), str(row["doi"]).lower(),
                      str(row["update_type"]), str(row["source_doi"]).lower())
            if dedupe in seen:
                continue
            seen.add(dedupe)
            key = str(row["zotero_key"])
            finding = _serialize_finding(row)
            entry = by_key.get(key)
            if entry is None:
                by_key[key] = {
                    "zotero_key": key,
                    "type": finding["update_type"],
                    "severity": severity_of(finding["update_type"]),
                    "doi": finding["doi"],
                    "latest_run_id": int(row["run_id"]),
                    "latest_found_at": finding["found_at"],
                    "source_doi": finding["source_doi"],
                    "source_date": finding["source_date"],
                    "source_title": finding["source_title"],
                    "url": finding["url"],
                    "types": [finding["update_type"]],
                }
                continue
            if finding["update_type"] not in entry["types"]:
                entry["types"].append(finding["update_type"])
            if severity_of(finding["update_type"]) > severity_of(entry["type"]):
                entry.update({
                    "type": finding["update_type"],
                    "severity": severity_of(finding["update_type"]),
                    "doi": finding["doi"],
                    "source_doi": finding["source_doi"],
                    "source_date": finding["source_date"],
                    "source_title": finding["source_title"],
                    "url": finding["url"],
                })
        return by_key


def _encode_cursor(run_id: int, offset: int) -> str:
    return f"{int(run_id)}:{int(offset)}"
