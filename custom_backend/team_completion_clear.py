# -*- coding: utf-8 -*-
"""P21　团队 trace 历史清空/写入保护协调器（C 专用；HISTORY_CLEAR_TRANSACTION_RULING_SOL.txt）。

多分区边界（裁定 §10–§35 的最小首版实现）：

- **team 分支先于一切副作用**：``run_team_history_clear`` 经
  ``runtime.readonly_context``（零建库/零迁移）解析身份/属主/两分区路径，
  非 team 任务返回 ``None`` 交回 personal/legacy 原路；确认/认证失败在
  任何 resolver ensure、默认 get_settings 或业务语句前拒绝。
- **连续 collab 屏障到末目标清理**：取得与仲裁提交同一 canonical
  ``runtime.lock(task_id)``，协作库专用 ``mode=rw``+``BEGIN IMMEDIATE``
  从第一分区检查保持到最后一个目标 commit/rollback 与清理完成；collab
  绝不作业务提交。既阻挡本进程并发，也阻挡跨进程仲裁写（SQLite 写锁）。
- **owner-first/物理去重**：按 collab→owner(shared)→personal 顺序取得现存
  目标专用写连接；同物理库（st_dev+st_ino，samefile 兜底）只保留一个
  连接/一次执行/一次提交；不同 uid 的物理别名**拒绝**，不静默合并授权。
  本协调器不使用 B1（不调 select_target、不扩通用保留层）。
- **首笔 DML 前全批检查**：全部分区以借用连接纯读 settings（prepared）、
  版本/幂等回执、将删+将重建的逐 stage/item 键并集（含旧 catalog 外的
  历史键；off 也检查全部将删阶段）；随后在同一 collab 事务上调
  ``team_history_guard.assert_history_writable_on``（screen/fulltext 精确
  映射，coding 不外推裁决锁）并核对两分区 F1 完成封条
  （``team_terminal_facts.list_seals_on``，只读本人/属主两库，不枚举
  required 成员、不读其票/ledger）。任一命中 → 整次 409 保护拒绝，
  全部 rollback/close，零 DML/零 receipt/零 baseline。
- **保护通过后逐库 clear(prepared=True)+commit**：物理重合的逻辑分区只
  执行/提交一次；writer/commit 失败的库回滚、其余按既有 partial 语义
  完成并如实报告每库事实；成功 commit 后 close 故障只记录，不谎报回滚。
- **响应**由同批借用连接读出的 settings/提交状态组装；不在锁内或 finally
  再调 path-only ``_settings_payload``。

同一 footprint 只读保护也覆盖 trace 写入口的 edit（PUT settings）与
restore（imports/commit）：``guard_team_trace_writes`` 以同一 collab 屏障
+历史门+封条对服务端导出的键域做零写断言，通过后交回既有写入算法。
"""

from __future__ import annotations

import os
import sqlite3
import uuid
from collections.abc import Iterable, Mapping
from contextlib import closing
from pathlib import Path
from typing import Any

from coscreen import research_trace, task_collaboration
from coscreen.team_history_guard import (
    TeamHistoryReadOnlyError,
    adjudicated_history_keys_on,
)
from coscreen.team_terminal_facts import TerminalFactsUnavailable, list_seals_on

__all__ = [
    "guard_team_trace_writes",
    "run_team_history_clear",
]

#: 界面阶段 → 仲裁/F1 ledger 阶段（与 team_write_checks._STAGE_VIEW_STAGES
#: 同一映射事实；coding 无筛选裁决来源，不进入）。
_LEDGER_STAGES = {"screen": "stage1", "fulltext": "stage2"}

#: trace_events 的业务阶段（control 行的 subject 是 'settings'/'history'
#: 等控制标识，不是文献键——裁定 §20：不把控制标识当 zotero_key 猜测）。
_BUSINESS_STAGES = ("screen", "fulltext")

_ALIAS_URI_SUFFIX = "?mode=rw"


class TeamTraceClearRejected(RuntimeError):
    """typed 保护性/输入性拒绝（HTTP 层映射 409；零业务写）。"""

    status = 409

    def __init__(self, payload: Mapping[str, Any]) -> None:
        self.payload = dict(payload)
        super().__init__(str(self.payload.get("error_code", "team_trace_clear_rejected")))


def _open_rw_begin(path: Path, *, timeout: float = 15.0) -> sqlite3.Connection:
    """打开现存库的专用写连接并立即 BEGIN IMMEDIATE（绝不创建/迁移）。"""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"team trace 目标必须是现存库文件：{path}")
    conn = sqlite3.connect(path.resolve().as_uri() + _ALIAS_URI_SUFFIX,
                           uri=True, timeout=timeout)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("BEGIN IMMEDIATE")
    except BaseException:
        try:
            conn.close()
        except BaseException:
            pass
        raise
    return conn


def _physical_identity(path: Path) -> tuple[object, ...] | None:
    """物理文件身份（st_dev+st_ino；st_ino=0 退化规范路径，samefile 兜底）。"""
    try:
        info = Path(path).stat()
    except OSError:
        return None
    inode = getattr(info, "st_ino", 0)
    if inode:
        return ("inode", getattr(info, "st_dev", 0), inode)
    return ("path", os.path.normcase(os.fspath(Path(path).resolve())))


def _close_quietly(conn: sqlite3.Connection | None, *, rollback: bool = True) -> str | None:
    """回滚+关闭；清理故障返回描述字符串（绝不遮盖原异常/成功语义）。"""
    if conn is None:
        return None
    detail: str | None = None
    try:
        if rollback and conn.in_transaction:
            conn.rollback()
    except BaseException as exc:  # pragma: no cover - 清理路径
        detail = f"rollback failed: {type(exc).__name__}: {exc}"
    try:
        conn.close()
    except BaseException as exc:  # pragma: no cover - 清理路径
        detail = detail or f"close failed: {type(exc).__name__}: {exc}"
    return detail


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def _borrowed_settings(conn: sqlite3.Connection, task_id: str, actor_id: Any) -> dict[str, Any]:
    """纯读分区 settings（prepared=True；结构缺失→RuntimeError fail-closed）。"""
    return research_trace.get_settings(conn, task_id, actor_id, prepared=True)


def _subject_keys(conn: sqlite3.Connection, task_id: str, actor_id: Any, stage: str) -> set[str]:
    """该 stage 将删除的历史事件键（含旧 catalog 外键；不按当前目录过滤）。"""
    keys: set[str] = set()
    if not _table_exists(conn, "trace_events"):
        return keys
    for row in conn.execute(
        "SELECT DISTINCT subject_id FROM trace_events "
        "WHERE task_id=? AND actor_id=? AND stage=?",
        (task_id, str(actor_id), stage),
    ):
        subject = row[0]
        if isinstance(subject, str) and subject:
            keys.add(subject)
    return keys


def _column_keys(conn: sqlite3.Connection, table: str, column: str) -> set[str]:
    """快照源表的真实键域（与 _snapshot_stage 实际 SELECT 一致；表缺→空）。"""
    keys: set[str] = set()
    if not _table_exists(conn, table):
        return keys
    for row in conn.execute(f"SELECT DISTINCT {column} FROM {table}"):  # noqa: S608 - 常量表/列名
        key = row[0]
        if isinstance(key, str) and key:
            keys.add(key)
    return keys


def _stored_stage_domain(conn: sqlite3.Connection, task_id: str, actor_id: Any,
                         stage: str, *, include_articles: bool) -> set[str]:
    """该 stage 的存储域（与快照实际 SELECT 一致；coding 不进保护域）。

    screen：历史事件键 ∪ decisions 键 ∪（``include_articles`` 时 articles
    全局基线键 + 'protocol'——固定 screen 标签）；fulltext：历史事件键 ∪
    stage2_decisions 键。
    """
    domain = _subject_keys(conn, task_id, actor_id, stage)
    if stage == "screen":
        domain |= _column_keys(conn, "decisions", "zotero_key")
        if include_articles:
            domain |= _column_keys(conn, "articles", "zotero_key")
            if _table_exists(conn, "articles") and _table_exists(conn, "meta"):
                domain.add("protocol")
    elif stage == "fulltext":
        domain |= _column_keys(conn, "stage2_decisions", "zotero_key")
    return domain


def _stage_key_domain(conn: sqlite3.Connection, task_id: str, actor_id: Any,
                      settings: Mapping[str, Any], stage: str) -> set[str]:
    """整次「删除+重建」的逐 stage 键并集（裁定 §18/§19）。

    删除域（历史事件）恒查——off 也查全部将删阶段；重建域（articles 全局
    基线+protocol）只在 status!=off（clear 后确会重建）时计入。
    """
    return _stored_stage_domain(
        conn, task_id, actor_id, stage,
        include_articles=settings.get("status") != "off")


def _instance_id(conn: sqlite3.Connection, task_id: str) -> str:
    """协作库 team_task_protocol 的规范实例 ID（借用连接同事务快照）。"""
    try:
        row = conn.execute(
            "SELECT task_instance_id FROM team_task_protocol WHERE task_id=?",
            (task_id,),
        ).fetchone()
    except sqlite3.Error as exc:
        raise TeamTraceClearRejected({
            "error_code": "trace_team_unconfirmed",
            "status": "unconfirmed",
            "message": "Team task protocol registration is not readable.",
            "details": type(exc).__name__,
        }) from exc
    if row is None or not isinstance(row[0], str) or not row[0]:
        raise TeamTraceClearRejected({
            "error_code": "trace_team_unconfirmed",
            "status": "unconfirmed",
            "message": "Team task protocol registration is missing.",
        })
    return row[0]


def _protected_rejection(exc: TeamHistoryReadOnlyError) -> TeamTraceClearRejected:
    return TeamTraceClearRejected({
        "error_code": exc.code,
        "message_key": exc.message_key,
        "status": "protected",
        "task": exc.task_id,
        "stage": exc.stage,
        "item_keys": list(exc.item_keys),
    })


def _seal_blocked_keys(target_conns: Iterable[sqlite3.Connection], instance: str,
                       stage: str, domain: Iterable[str]) -> list[str]:
    """两分区 F1 完成封条与保护域的交集（只读；F1 表未备=该库无封条）。"""
    wanted = set(domain)
    blocked: set[str] = set()
    ledger = _LEDGER_STAGES[stage]
    for conn in target_conns:
        try:
            seals = list_seals_on(conn, task_instance_id=instance, stage=ledger)
        except TerminalFactsUnavailable:
            continue
        for seal in seals:
            payload = seal.get("payload")
            key = payload.get("item_key") if isinstance(payload, Mapping) else None
            if isinstance(key, str) and key in wanted:
                blocked.add(key)
    return sorted(blocked)


def _assert_domains_writable(collab_conn: sqlite3.Connection,
                             target_conns: list[sqlite3.Connection],
                             *, task_id: str, instance: str,
                             domains: Mapping[str, set[str]]) -> None:
    """同一 collab 事务上的历史门+完成封条整批断言（零写入）。"""
    for stage in _BUSINESS_STAGES:
        domain = domains.get(stage) or set()
        if not domain:
            continue
        # 1) raw 已生效仲裁历史锁（screen/fulltext 精确映射；coding 不外推）。
        protected = adjudicated_history_keys_on(collab_conn, task_id=task_id, stage=stage)
        hit = sorted(domain & protected)
        if hit:
            raise _protected_rejection(
                TeamHistoryReadOnlyError(task_id, stage, hit))
        # 2) 同阶段 F1 完成封条（只读两实际 trace 目标，不枚举成员票）。
        sealed = _seal_blocked_keys(target_conns, instance, stage, domain)
        if sealed:
            raise TeamTraceClearRejected({
                "error_code": "stage_completed_read_only",
                "message_key": "team.stage_completed_read_only",
                "status": "protected",
                "task": task_id,
                "stage": stage,
                "item_keys": sealed,
            })


def _resolve_partitions(collab_conn: sqlite3.Connection, *, task_id: str,
                        owner_db: Path, actor_db: Path,
                        owner_uid: int, actor_uid: int) -> list[dict[str, Any]]:
    """owner-first 物理去重解析；不同 uid 的物理别名拒绝（裁定 §13/§15）。

    返回按 collab→owner→personal 顺序的物理目标（shared=owner screener 库、
    personal=actor screener 库）；同物理文件合并为单目标，逻辑名集合挂
    ``names``；``uid`` 是该物理库的属主 claim。
    """
    claims: list[tuple[str, Path, int]] = [
        ("shared", Path(owner_db), owner_uid),
        ("personal", Path(actor_db), actor_uid),
    ]
    targets: list[dict[str, Any]] = []
    seen: dict[tuple[object, ...], dict[str, Any]] = {}
    for name, path, uid in claims:
        identity = _physical_identity(path)
        entry = seen.get(identity) if identity is not None else None
        if entry is not None:
            if entry["uid"] != uid and entry["path"].resolve() != path.resolve():
                raise TeamTraceClearRejected({
                    "error_code": "trace_store_alias_denied",
                    "status": "protected",
                    "message": "Physical alias between different member stores cannot be merged for clearing.",
                    "task": task_id,
                    "partitions": [entry["names"][0], name],
                })
            entry["names"].append(name)
            continue
        entry = {"names": [name], "path": path, "uid": uid, "identity": identity}
        if identity is not None:
            seen[identity] = entry
        targets.append(entry)
    return targets


def _open_targets(targets: list[dict[str, Any]], *, task_id: str,
                  actor_id: Any) -> list[dict[str, Any]]:
    """逐物理目标开写连接；缺文件/缺结构=typed unavailable（零写拒绝，§24/§25）。"""
    opened: list[dict[str, Any]] = []
    try:
        for entry in targets:
            try:
                conn = _open_rw_begin(entry["path"])
                settings = _borrowed_settings(conn, task_id, actor_id)
            except (FileNotFoundError, RuntimeError, sqlite3.Error, OSError) as exc:
                raise TeamTraceClearRejected({
                    "error_code": "trace_partition_unavailable",
                    "status": "unavailable",
                    "message": "A required team trace partition is not prepared for clearing.",
                    "partitions": list(entry["names"]),
                    "details": type(exc).__name__,
                }) from exc
            entry = dict(entry, conn=conn, settings=settings)
            opened.append(entry)
    except BaseException:
        for entry in opened:
            _close_quietly(entry.get("conn"))
        raise
    return opened


def _httpize(exc: BaseException) -> BaseException:
    """把 typed 拒绝映射成结构化 HTTPException；其余原样透传。"""
    from fastapi import HTTPException

    if isinstance(exc, TeamTraceClearRejected):
        detail = dict(exc.payload)
        detail.setdefault("status", "protected")
        # §44：结构化 code/message_key 与既有 typed error_code 双名同载。
        detail.setdefault("code", detail.get("error_code", "team_trace_clear_rejected"))
        assert detail.get("status") != "partial"
        return HTTPException(exc.status, detail)
    return exc


def guard_team_trace_writes(
    runtime: Any, info: object, user: Mapping[str, Any],
    *,
    stage_keys: Mapping[str, Iterable[str]] | None = None,
    settings_transition: tuple[str, Iterable[str]] | None = None,
) -> None:
    """team 任务 trace 写入口（edit/restore）的同一 footprint 只读保护。

    D1 修轮：域派生按**真实转写足迹**（与 ``update_settings`` 实际写面
    逐条对齐），不再按 body.stages 粗粒度外推：

    - ``settings_transition=(status, stages)``（PUT settings/edit）：
      * status 为 off/paused——真实写面仅 control 事件（subject
        'settings'，非文献键），**不阻断**（合法 off 放行；已裁决键存在
        时的删除保护仍由 clear 路径承担——off 也全量检查，语义一致）；
      * 进入 recording（分区此前非 recording，含结构未备的缺省起步）——
        ``update_settings`` 无条件转写全部 articles+protocol 基线（R3-06
        无阶段门控，固定 screen 标签）——保护域**同样无条件**含全部
        articles/protocol/decisions/事件键，不按 body.stages 派生：存在
        已裁决 article 即整批拒（与 clear 路径同批同语义，§19 对齐）；
        另计入新选 fulltext/screen 的阶段快照源域；
      * 停留 recording 仅追加新选阶段——按新选阶段的存储域（不含全局
        articles 基线，该路径不重写基线）。
    - ``stage_keys``（imports/commit/restore）：服务端导出的显式键域。

    零写入：collab 屏障（canonical 锁+mode=rw+BEGIN IMMEDIATE）上断言
    未命中仲裁历史锁/完成封条，随后回滚关闭。非 team 任务直接返回；
    命中抛 ``TeamTraceClearRejected``（HTTP 层 409）。不枚举成员、不读票。
    """
    if settings_transition is not None and stage_keys is not None:
        raise TypeError("stage_keys 与 settings_transition 不能同时提供")
    if settings_transition is not None:
        status = settings_transition[0]
        if status != "recording":
            # off/paused 转换的真实写面仅 control 事件——合法转换不阻断。
            return
    with _team_barrier(runtime, info, user) as barrier:
        if barrier is None:
            return
        collab_conn, owner_db, actor_db, task_id, owner_uid, actor_uid = barrier
        instance = _instance_id(collab_conn, task_id)
        targets = _resolve_partitions(
            collab_conn, task_id=task_id, owner_db=owner_db, actor_db=actor_db,
            owner_uid=owner_uid, actor_uid=actor_uid)
        actor_id = int(user["user_id"])
        domains: dict[str, set[str]] = {}
        conns = []
        try:
            for entry in targets:
                if not entry["path"].is_file():
                    continue
                conn = _open_rw_begin(entry["path"])
                conns.append(conn)
                if settings_transition is not None:
                    status, stages = settings_transition
                    stage_set = {s for s in stages if isinstance(s, str)}
                    try:
                        previous = _borrowed_settings(conn, task_id, actor_id)
                        prev_status = previous["status"]
                        prev_stages = set(previous["stages"])
                    except (RuntimeError, sqlite3.Error, OSError):
                        # 结构未备：真实写入会 ensure 后按缺省起步——视作
                        # 此前 off/空选（进入 recording 即无条件全量基线）。
                        prev_status, prev_stages = "off", set()
                    entering = prev_status != "recording"
                    if entering:
                        # R3-06：进入 recording 无条件写全部 articles+protocol
                        # 基线——保护域不按 body.stages 派生。
                        domains.setdefault("screen", set()).update(
                            _stored_stage_domain(
                                conn, task_id, actor_id, "screen",
                                include_articles=True))
                        newly = stage_set
                    else:
                        newly = stage_set - prev_stages
                    if "fulltext" in newly:
                        domains.setdefault("fulltext", set()).update(
                            _stored_stage_domain(
                                conn, task_id, actor_id, "fulltext",
                                include_articles=False))
                    if "screen" in newly and not entering:
                        domains.setdefault("screen", set()).update(
                            _stored_stage_domain(
                                conn, task_id, actor_id, "screen",
                                include_articles=False))
                else:
                    for stage, keys in (stage_keys or {}).items():
                        if stage not in _BUSINESS_STAGES:
                            continue
                        domains.setdefault(stage, set()).update(
                            key for key in keys if isinstance(key, str) and key)
            _assert_domains_writable(
                collab_conn, conns, task_id=task_id, instance=instance,
                domains=domains)
        finally:
            for conn in conns:
                _close_quietly(conn)


class _TeamBarrier:
    """canonical 锁 + collab mode=rw BEGIN IMMEDIATE 的连续屏障上下文。"""

    def __init__(self, runtime: Any, info: object, user: Mapping[str, Any]) -> None:
        self.runtime = runtime
        self.info = info
        self.user = dict(user)
        self.result: tuple | None = None
        self._conn: sqlite3.Connection | None = None
        self._lock = None

    def __enter__(self) -> tuple | None:
        ctx = self.runtime.readonly_context(
            str(getattr(self.info, "task_id", "")), self.user)
        if getattr(ctx, "task_kind", None) != "team":
            return None
        task_id = ctx.info.task_id
        owner_uid = int(ctx.owner["owner_user_id"])
        self._lock = self.runtime.lock(task_id)
        self._lock.acquire()
        try:
            collab_path = Path(ctx.collaboration_db)
            if not collab_path.is_file():
                raise TeamTraceClearRejected({
                    "error_code": "trace_team_unconfirmed",
                    "status": "unconfirmed",
                    "message": "Collaboration store is not available for this task.",
                })
            self._conn = _open_rw_begin(collab_path)
            owner = task_collaboration.get_owner(collab_path, task_id=task_id)
            if owner is None or int(owner["owner_user_id"]) != owner_uid:
                self._release()
                return None
            actor_uid = int(self.user["user_id"])
            if actor_uid != owner_uid and not task_collaboration.is_participant(
                    collab_path, user_id=actor_uid, task_id=task_id):
                raise TeamTraceClearRejected({
                    "error_code": "trace_access_denied",
                    "status": "denied",
                    "message": "Task trace access denied.",
                })
            self.result = (self._conn, ctx.owner_db, ctx.actor_db,
                           task_id, owner_uid, actor_uid)
            return self.result
        except BaseException:
            self._release()
            raise

    def _release(self, *, lock_only: bool = False) -> None:
        if self._conn is None and self._lock is None:
            return  # 已由 finish() 幂等收尾。
        if not lock_only:
            # collab 从不作业务提交：屏障结束一律回滚+关闭（裁定 §29）。
            _close_quietly(self._conn)
            self._conn = None
        if self._lock is not None:
            self._lock.release()
            self._lock = None

    def finish(self) -> str | None:
        """正常路径在响应组装前收尾：回滚/关闭 collab、释放 canonical 锁。

        返回清理故障描述（无故障 None）；成功收尾后 ``__exit__`` 幂等空转。
        供协调器把 collab close 故障如实记入响应（不谎报回滚也不静默丢弃）。
        """
        detail = _close_quietly(self._conn)
        self._conn = None
        if self._lock is not None:
            self._lock.release()
            self._lock = None
        return detail

    def __exit__(self, exc_type, exc, tb) -> bool:
        self._release()
        return False


def _team_barrier(runtime: Any, info: object, user: Mapping[str, Any]) -> _TeamBarrier:
    return _TeamBarrier(runtime, info, user)


def run_team_history_clear(
    runtime: Any,
    info: object,
    user: Mapping[str, Any],
    *,
    request: Mapping[str, object],
) -> Mapping[str, object] | None:
    """P21 团队多分区 trace 历史清空（HTTP 入口经宿主 seam 调入）。

    返回既有响应形状 ``{status, operation_id, deleted_event_count,
    partitions, failures, settings, research_data_retained}``；非 team 任务
    返回 ``None`` 交回 personal/legacy 原路。保护性拒绝（历史门/完成封条/
    别名/版本/幂等/分区不可用）在首笔 DML 前整批拒绝且**永不**返回
    ``status='partial'``；保护通过后的普通 writer/commit 失败按既有
    partial 语义保留每库事实（成功 commit 后的 close/清理故障如实记入响应
    ``cleanup_errors``，不谎报回滚、不静默丢弃）。
    """
    if not isinstance(request, Mapping):
        raise TeamTraceClearRejected({
            "error_code": "trace_clear_request_invalid", "status": "invalid"})
    if request.get("confirm") is not True:
        raise TeamTraceClearRejected({
            "error_code": "trace_clear_confirmation_required", "status": "invalid",
            "message": "Explicit confirmation is required to clear personal trace history."})
    operation_id = request.get("operation_id")
    if operation_id is not None and (not isinstance(operation_id, str)
                                     or not 1 <= len(operation_id) <= 200):
        raise TeamTraceClearRejected({
            "error_code": "trace_clear_request_invalid", "status": "invalid"})
    if operation_id in (None, ""):
        operation_id = str(uuid.uuid4())
    expected_total = request.get("expected_version")
    if expected_total is not None and (isinstance(expected_total, bool)
                                       or not isinstance(expected_total, int)
                                       or expected_total < 0):
        raise TeamTraceClearRejected({
            "error_code": "trace_clear_request_invalid", "status": "invalid"})
    actor_id = user.get("user_id")
    if isinstance(actor_id, bool) or not isinstance(actor_id, int) or not str(actor_id):
        raise TeamTraceClearRejected({
            "error_code": "trace_actor_unauthenticated", "status": "denied"})

    barrier_ctx = _team_barrier(runtime, info, user)
    with barrier_ctx as barrier:
        if barrier is None:
            return None
        collab_conn, owner_db, actor_db, task_id, owner_uid, actor_uid = barrier
        instance = _instance_id(collab_conn, task_id)
        targets = _resolve_partitions(
            collab_conn, task_id=task_id, owner_db=owner_db, actor_db=actor_db,
            owner_uid=owner_uid, actor_uid=actor_uid)
        # 首笔 DML 前：开全部物理目标（owner-first 顺序），纯读 settings/域。
        opened = _open_targets(targets, task_id=task_id, actor_id=actor_id)
        try:
            domains: dict[str, set[str]] = {}
            for entry in opened:
                for stage in _BUSINESS_STAGES:
                    domains.setdefault(stage, set()).update(_stage_key_domain(
                        entry["conn"], task_id, actor_id, entry["settings"], stage))
            # 版本/幂等整批预检（借用连接纯读；不靠旧 before 缓存授权）。
            total_version = 0
            retrying_partial = False
            for entry in opened:
                settings = entry["settings"]
                total_version += int(settings["version"])
                if settings.get("operation_id") == operation_id:
                    retrying_partial = True
                row = entry["conn"].execute(
                    "SELECT payload_sha256 FROM trace_clear_operations "
                    "WHERE task_id=? AND actor_id=? AND operation_id=?",
                    (task_id, str(actor_id), operation_id),
                ).fetchone()
                if row is not None:
                    retrying_partial = True
            if (expected_total is not None and expected_total != total_version
                    and not retrying_partial):
                raise TeamTraceClearRejected({
                    "error_code": "trace_settings_conflict", "status": "conflict",
                    "current": _assembled_settings(opened, task_id, actor_id)})
            # 同一 collab 事务上的历史门+完成封条整批断言（零写入）。
            _assert_domains_writable(
                collab_conn, [entry["conn"] for entry in opened],
                task_id=task_id, instance=instance, domains=domains)
            # 保护通过：逐物理目标 clear(prepared=True)+commit（单目标内
            # delete/基线/settings/receipt 同事务；物理重合逻辑分区一次）。
            cleared: dict[str, dict[str, Any]] = {}
            failures: dict[str, dict[str, Any]] = {}
            total_deleted = 0
            alias_of: dict[str, str] = {}
            for entry in opened:
                settings = entry["settings"]
                part_version = int(settings["version"])
                if settings.get("operation_id") == operation_id:
                    part_version -= 1
                primary = entry["names"][0]
                try:
                    result = research_trace.clear_history(
                        entry["conn"],
                        research_trace.TraceContext(task_id, actor_id, source="api"),
                        operation_id=operation_id, expected_version=part_version,
                        prepared=True)
                    entry["conn"].commit()
                    entry["committed"] = True
                except research_trace.TraceVersionConflict as exc:
                    failures[primary] = {"error": "version_conflict",
                                         "current": exc.current}
                    continue
                except research_trace.TraceConflict as exc:
                    raise TeamTraceClearRejected({
                        "error_code": "trace_operation_conflict", "status": "conflict",
                        "message": str(exc)}) from exc
                except (OSError, sqlite3.Error, ValueError, TypeError) as exc:
                    failures[primary] = {"error": "clear_failed",
                                         "details": type(exc).__name__}
                    continue
                for name in entry["names"]:
                    cleared[name] = result
                total_deleted += int(result["deleted_event_count"])
                for name in entry["names"][1:]:
                    alias_of[name] = primary
            response_settings = _assembled_settings(opened, task_id, actor_id)
            status = "partial" if failures else "cleared"
            payload = {
                "status": status, "operation_id": operation_id,
                "deleted_event_count": total_deleted,
                "partitions": cleared, "failures": failures,
                "settings": response_settings,
                "research_data_retained": True,
            }
            # 逐库清理在响应组装前完成，清理故障如实入响应（不谎报回滚、
            # 不静默丢弃）：已 commit 的目标只 close；未提交的回滚+关闭；
            # collab 屏障同窗收尾（回滚+close+释放 canonical 锁）。
            cleanup_errors: list[str] = []
            for entry in opened:
                detail = _close_quietly(entry.get("conn"),
                                        rollback=not entry.get("committed", False))
                if detail:
                    cleanup_errors.append(f"{entry['names'][0]}: {detail}")
                entry["conn"] = None
            collab_detail = barrier_ctx.finish()
            if collab_detail:
                cleanup_errors.append(f"collab: {collab_detail}")
            if cleanup_errors:
                payload["cleanup_errors"] = cleanup_errors
            return payload
        except BaseException:
            for entry in opened:
                _close_quietly(entry.get("conn"),
                               rollback=not entry.get("committed", False))
                entry["conn"] = None
            raise


def _assembled_settings(opened: list[dict[str, Any]], task_id: str,
                        actor_id: Any) -> dict[str, Any]:
    """同批借用连接读出的 settings 组装（裁定 §35；不再 path-only 重读）。"""
    partitions: dict[str, dict[str, Any]] = {}
    selected: set[str] = set()
    version = 0
    statuses: list[str] = []
    alias: dict[str, str] = {}
    for entry in opened:
        settings = entry["settings"]
        payload = {
            "available": True, "status": settings["status"],
            "stages": settings["stages"], "version": settings["version"],
            "store_id": settings["store_id"],
            "updated_at": settings["updated_at"],
            "operation_id": settings["operation_id"],
        }
        committed = entry.get("committed")
        if committed is not None:
            payload["commit_state"] = "committed" if committed else "rolled_back"
        primary = entry["names"][0]
        partitions[primary] = payload
        for name in entry["names"][1:]:
            partitions[name] = dict(payload, alias_of=primary)
            alias[name] = primary
        version += int(settings["version"])
        selected.update(settings["stages"])
        statuses.append(settings["status"])
    active = [partitions[name]["status"] for name in ("personal", "shared")
              if name in partitions and "alias_of" not in partitions[name]]
    if selected and any(state not in {"recording", "paused"} for state in active):
        status = "partial"
    elif selected and active and all(state == "paused" for state in active):
        status = "paused"
    elif selected and active and all(state == "recording" for state in active):
        status = "recording"
    else:
        status = "off"
    return {"task_id": task_id, "status": status, "stages": sorted(selected),
            "version": version, "partitions": partitions}


# ---------------------------------------------------------------------------
# HTTP 适配（宿主 seam 用；延迟导入 fastapi）
# ---------------------------------------------------------------------------

def run_team_history_clear_http(
    runtime: Any, info: object, user: Mapping[str, Any],
    *, request: Mapping[str, object],
) -> Mapping[str, object] | None:
    """HTTP 包装：typed 拒绝映射结构化 409（保护性拒绝永非 partial）。"""
    try:
        result = run_team_history_clear(runtime, info, user, request=request)
    except TeamTraceClearRejected as exc:
        raise _httpize(exc) from exc
    return result


def guard_team_trace_writes_http(
    runtime: Any, info: object, user: Mapping[str, Any],
    *, guard_request: Mapping[str, Any],
) -> None:
    """HTTP 包装：PUT settings（edit）/imports commit（restore）的写前保护。

    ``guard_request`` 单载荷双通道：``{"settings_transition": (status,
    stages)}`` 或 ``{"stage_keys": {stage: keys}}``（见 guard 文档）。
    """
    try:
        transition = guard_request.get("settings_transition")
        keys = guard_request.get("stage_keys")
        if transition is not None:
            guard_team_trace_writes(
                runtime, info, user,
                settings_transition=(transition[0], transition[1]))
        else:
            guard_team_trace_writes(runtime, info, user, stage_keys=keys or {})
    except TeamTraceClearRejected as exc:
        raise _httpize(exc) from exc
