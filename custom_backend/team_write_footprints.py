# -*- coding: utf-8 -*-
"""P11 真实 mutation footprint（C08/C11；纯读取、借用连接、零 DDL）。

在调用方已保留好事务的团队写窗口连接上，把服务端 artifact ID（notes/
highlight/undo/freeze/trace/import 原始记录）解析成它**实际**影响的
``(stage, item_key)`` 单元格集合（``MutationFootprint``）：

- 客户端随请求送来的 ``request_keys`` 只做输入合同校验（P00
  ``normalize_item_keys``，拒 str/bytes/空串/NUL/坏 Unicode/空集），绝不
  作为 artifact 路径的权威键域：删除/改写某条 artifact 时，受保护单元格
  来自保留连接上的存储行本身（客户端伪键不能缩小或改写 footprint）。
- 已离开当前 catalog 或属于旧 round 的历史键照样进入 footprint——本模块
  不做 canonical 域过滤（那是写窗/资格服务的职责），保护面不因条目被移出
  名单而丢失。
- ``AffectedCell.stage`` 是记录**真正所属**的业务阶段（screen/fulltext/
  coding），不是其上游链资格阶段：highlights/页笔记属于 fulltext，
  coding_notes/coding consensus/rater 记录属于 coding；筛选（screen/
  fulltext）完成不外推成 coding 历史锁。trace 记录里的协议标识
  （``protocol``/``settings``/``history``/``binary-reset``）
  与 field UUID（``field_definition_*``、``baseline_field`` 的 subject）
  不是文献键，不猜成 ``AffectedCell``。``report-evidence`` 域主体与
  ``ai_suggestion_*`` 未入分类表，**代码按 fail-closed 处理**（无法分类即
  ``complete=False``），归属待 P21/P24 裁定——不因其形似而当作协议标识
  放行。
- 完整性口径：``complete=False`` 不是"没有受保护内容"，而是**不能安全执
  行**——artifact 行缺失、存储结构未准备、载荷 JSON 损坏（import 的
  origin_json 解析失败）、记录阶段与
  operation 声明不一致、trace subject 无法分类、import 原始来源无法确认
  时，返回 ``complete=False`` 且 ``reason`` 必填，绝不降级成空集。锚：
  ``assert footprint.complete or footprint.reason is not None``。

操作表（固定；未列 name 或 stage 组合一律 ``ValueError``，operation 由服
务端选择，绝不接受客户端送 operation 字符串以外的旁路）：

======================  =====================  =======================  =====================
name                    stage                  artifact 家族            artifact ID 形态
======================  =====================  =======================  =====================
member_vote             screen / fulltext      无（请求键即主体）       必须为 None
member_undo             screen/fulltext/coding workspace_undo 行        int（journal id）
member_coding           coding                 无（请求键即主体）       必须为 None
member_appraisal        coding                 无（请求键即主体）       必须为 None
member_annotation       fulltext               highlights 行            可选 int（None=新增）
member_annotation_note  fulltext               highlights 行            int（highlight id）
member_coding_note      coding                 coding_notes 行          可选 int（None=新建）
coding_rater_submit     coding                 无（请求键即主体）       必须为 None
coding_consensus_append coding                 无（请求键即主体）       必须为 None
coding_consensus_freeze coding                 coding_consensus_records int（consensus 记录 ID）
team_trace_edit         screen/fulltext/       trace_events 行          str(event_id)|int(seq)
                        coding/history
team_trace_delete       screen/fulltext/       trace_events 行          str(event_id)|int(seq)
                        coding/history
team_trace_restore      screen/fulltext/       trace_events 行          str(event_id)|int(seq)
                        coding/history
team_trace_import       screen/fulltext/       trace_events import 行   str(event_id)|int(seq)
                        coding/history
======================  =====================  =======================  =====================

与 ``custom_backend/team_write_checks._OPERATIONS`` 已落地口径一致的九个
name（逐名）：member_vote、member_undo、member_coding、member_appraisal、
member_annotation、member_annotation_note、coding_rater_submit、
coding_consensus_append、coding_consensus_freeze。``member_coding_note`` 与
``team_trace_edit/delete/restore/import`` 为本包占位名，待 P12/P21 同步冻结
——``_OPERATION_ARTIFACTS`` 是唯一注册点，定稿时只改这张表。

阶段与 artifact 语义边界（OC-DS 复审 B1/B2/B5/B6 裁定）：

- ``coding_consensus_freeze`` 是 **create 语义**：freeze 行（coding_freezes，
  coding_consensus.py:84-93）由 writer 在提交阶段才创建，纯检查阶段它不存
  在；artifact_id 解释为被冻结的 ``coding_consensus_records.id``，查其
  item_key 得实际键域，批量 freeze 由调用方逐记录解析求并集。
- ``team_trace_import``/``team_trace_restore`` 的 artifact 语义=**已存在**
  的 trace_events 行（编辑/删除/恢复已记录的行）；行不存在即
  ``complete=False``。"imports/commit 提交时新建 N 行"的包事件语义属 P21
  定稿，本包不实现。
- trace 族 stage 域含 ``history``（C02 混合历史 stage='history'）：
  operation.stage 为具体记录阶段时行 stage 必须一致；``history`` 时行可为
  任意合法记录阶段（合法性仍由 subject 分类核）。

读取口径（"复用已闭 prepared history/trace reader，不回到 path-only 默认
ensure"）：trace/undo/highlight/note/consensus 全部走保留连接上的失败关闭点读
——缺表/缺行/坏记录即 ``complete=False`` 拒绝，绝不调用
``research_trace.ensure_schema``/任何 DDL/备份/自愈（S2-B2 prepared seam 同
语义；``research_trace.get_event`` 走默认 ``ensure_schema``，故不借用）。
C02 借用合同：本模块不 BEGIN/SAVEPOINT/commit/rollback/close、不改
PRAGMA/``row_factory``；自建 Cursor 全部 ``fetchall`` 物化并经 ``closing``
确定关闭；``sqlite3.ProgrammingError``（含已关闭连接）原样透传，其余
``sqlite3`` 错误不吞不换壳。
"""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from collections.abc import Iterable, Mapping
from contextlib import closing
from dataclasses import dataclass

from coscreen.team_flow_types import (
    AffectedCell,
    MutationFootprint,
    TeamOperation,
)
from custom_backend.team_keyed_domain import normalize_item_keys

__all__ = ["mutation_footprint_on"]

#: 记录真正所属的业务阶段（链资格阶段 stage1/stage2/appraisal 不进入单元格）。
_RECORD_STAGES = frozenset({"screen", "fulltext", "coding"})

#: trace 族 operation 的 stage 域：记录阶段 + C02:79 混合历史 ``history``。
_TRACE_OPERATION_STAGES = _RECORD_STAGES | {"history"}

#: trace import 落库时对原始 action 加的前缀（research_trace.import_event）。
_IMPORTED_ACTION_PREFIX = "imported_"

#: 决策族 action 允许的记录阶段（decisions/stage2_decisions 两表同名 action）。
_SCREENING_ITEM_STAGES = frozenset({"screen", "fulltext"})
_CODING_ITEM_STAGES = frozenset({"coding"})
_SCREEN_ONLY_ITEM_STAGES = frozenset({"screen"})
_FULLTEXT_ONLY_ITEM_STAGES = frozenset({"fulltext"})

#: subject_id 是文献条目键的 trace action → 允许的记录所属阶段。
#: 依据：coscreen/db.py、fulltext.py、coding.py、coding_consensus.py、
#: research_trace.py（_snapshot_stage/_snapshot_articles_and_protocol）与
#: review_imports.has_research 的既有 action 全集；不在本表且不在
#: ``_TRACE_NON_ITEM_ACTIONS`` 的 action 一律视为无法分类（fail-closed），
#: 不猜成文献键。刻意未入表、待 P21/P24 裁定的 action 见模块 docstring 与
#: 包报告：ai_suggestion_generated/decided、report_evidence_changed（及其
#: imported_ 变体）。
_TRACE_ITEM_ACTION_STAGES: Mapping[str, frozenset[str]] = {
    # screen/fulltext 决策（decisions / stage2_decisions 同名 action）。
    "decision_created": _SCREENING_ITEM_STAGES,
    "decision_changed": _SCREENING_ITEM_STAGES,
    "decision_imported": _SCREENING_ITEM_STAGES,
    "decision_undone": _SCREENING_ITEM_STAGES,
    "baseline_decision": _SCREENING_ITEM_STAGES,
    # 全局文献基线（research_trace._snapshot_articles_and_protocol 固定
    # stage='screen'，仅作归类，不代表筛选阶段被纳入录制范围）。
    "baseline_item": _SCREEN_ONLY_ITEM_STAGES,
    # 全文附件（fulltext.add_file）。
    "fulltext_added": _FULLTEXT_ONLY_ITEM_STAGES,
    "fulltext_replaced": _FULLTEXT_ONLY_ITEM_STAGES,
    # coding 取值/备注/证据/评分员记录（coding_consensus.append_rater_record
    # 的 created/changed 两态，coding_consensus.py:387）。
    "coding_value_created": _CODING_ITEM_STAGES,
    "coding_value_changed": _CODING_ITEM_STAGES,
    "coding_value_cleared": _CODING_ITEM_STAGES,
    "coding_values_undone": _CODING_ITEM_STAGES,
    "coding_note_created": _CODING_ITEM_STAGES,
    "coding_note_changed": _CODING_ITEM_STAGES,
    "coding_note_cleared": _CODING_ITEM_STAGES,
    "coding_note_image_added": _CODING_ITEM_STAGES,
    "coding_note_image_removed": _CODING_ITEM_STAGES,
    "coding_evidence_undone": _CODING_ITEM_STAGES,
    "coding_image_ocr_changed": _CODING_ITEM_STAGES,
    "baseline_value": _CODING_ITEM_STAGES,
    "baseline_coding_note": _CODING_ITEM_STAGES,
    "baseline_coding_image": _CODING_ITEM_STAGES,
    "baseline_rater_revision": _CODING_ITEM_STAGES,
    "rater_record_created": _CODING_ITEM_STAGES,
    "rater_record_changed": _CODING_ITEM_STAGES,
}

#: subject_id 是协议标识/field UUID/控制主体的 trace action——不产生文献
#: 单元格（"protocol标识/field UUID 不猜成文献"），但 footprint 仍完整。
_TRACE_NON_ITEM_ACTIONS = frozenset({
    "baseline_protocol",            # subject 固定为 'protocol'
    "baseline_field",               # subject 是 field UUID
    "field_definition_created",     # subject 是 field UUID
    "field_definition_changed",
    "field_definition_deleted",
    "recording_started",            # 以下为 control 阶段设置/边界事件
    "recording_resumed",
    "recording_paused",
    "recording_stopped",
    "stages_changed",
    "history_cleared",
    "binary_maybe_reset",           # subject 固定为 'binary-reset'
})

#: artifact 家族标记：'request_keys' 表示无服务端 artifact，请求键即主体。
_KIND_REQUEST_KEYS = "request_keys"
_KIND_UNDO = "undo"
_KIND_HIGHLIGHT = "highlight"
_KIND_CODING_NOTE = "coding_note"
_KIND_FREEZE = "freeze"
_KIND_TRACE = "trace"
_KIND_IMPORT = "import"


@dataclass(frozen=True)
class _ArtifactSpec:
    """单个 operation 的 artifact 解析画像（固定表驱动，不让客户端选）。"""

    kind: str                    # _KIND_* 家族标记
    stages: frozenset[str]       # 允许的 operation.stage
    artifact_required: bool      # True=必须给 artifact ID（缺失 ValueError）
    label: str                   # 错误文案中的 artifact 名称


#: 固定 operation 表：name → 解析画像。九个 name 与 team_write_checks 落地
#: 表逐名一致（见模块 docstring）；member_coding_note 与 team_trace_* 为本包
#: 占位名，待 P12/P21 同步冻结。
_OPERATION_ARTIFACTS: Mapping[str, _ArtifactSpec] = {
    "member_vote": _ArtifactSpec(
        _KIND_REQUEST_KEYS, _SCREENING_ITEM_STAGES, False, "member_vote"),
    "member_undo": _ArtifactSpec(
        _KIND_UNDO, _RECORD_STAGES, True, "workspace_undo 条目"),
    "member_coding": _ArtifactSpec(
        _KIND_REQUEST_KEYS, _CODING_ITEM_STAGES, False, "member_coding"),
    "member_appraisal": _ArtifactSpec(
        _KIND_REQUEST_KEYS, _CODING_ITEM_STAGES, False, "member_appraisal"),
    "member_annotation": _ArtifactSpec(
        _KIND_HIGHLIGHT, _FULLTEXT_ONLY_ITEM_STAGES, False, "highlights 条目"),
    "member_annotation_note": _ArtifactSpec(
        _KIND_HIGHLIGHT, _FULLTEXT_ONLY_ITEM_STAGES, True, "highlights 条目"),
    "member_coding_note": _ArtifactSpec(
        _KIND_CODING_NOTE, _CODING_ITEM_STAGES, False, "coding_notes 条目"),
    "coding_rater_submit": _ArtifactSpec(
        _KIND_REQUEST_KEYS, _CODING_ITEM_STAGES, False, "coding_rater_submit"),
    "coding_consensus_append": _ArtifactSpec(
        _KIND_REQUEST_KEYS, _CODING_ITEM_STAGES, False,
        "coding_consensus_append"),
    "coding_consensus_freeze": _ArtifactSpec(
        _KIND_FREEZE, _CODING_ITEM_STAGES, True, "coding consensus 记录"),
    "team_trace_edit": _ArtifactSpec(
        _KIND_TRACE, _TRACE_OPERATION_STAGES, True, "trace 记录"),
    "team_trace_delete": _ArtifactSpec(
        _KIND_TRACE, _TRACE_OPERATION_STAGES, True, "trace 记录"),
    "team_trace_restore": _ArtifactSpec(
        _KIND_TRACE, _TRACE_OPERATION_STAGES, True, "trace 记录"),
    "team_trace_import": _ArtifactSpec(
        _KIND_IMPORT, _TRACE_OPERATION_STAGES, True, "trace import 记录"),
}

#: trace artifact 的 event_id 文本上限（对齐 research_trace 对 event_id/
#: store_id 的文本限长，防无界字符串进 SQL 参数）。
_MAX_EVENT_ID_TEXT = 200


class _RecordUnresolvable(Exception):
    """内部：artifact 原始记录无法解析出可确认的完整 footprint（消息即 reason）。"""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _require_borrowed_footprint_connection(
    conn: sqlite3.Connection, *, api: str,
) -> None:
    """C02 借用连接契约：真实 native 连接（type 继承，拒 __class__ 伪装）、
    调用方活动事务、``row_factory is sqlite3.Row``。

    已关闭连接的 ``conn.in_transaction`` 读取自然抛
    ``sqlite3.ProgrammingError``——原样透传。零 SQL、零连接管理。
    """
    if not issubclass(type(conn), sqlite3.Connection):
        raise TypeError(
            f"{api} requires a native sqlite3.Connection or native subclass")
    if not conn.in_transaction:
        raise RuntimeError(f"{api} requires an active caller transaction")
    if conn.row_factory is not sqlite3.Row:
        raise RuntimeError(f"{api} requires conn.row_factory is sqlite3.Row")


def _point_lookup(
    conn: sqlite3.Connection, sql: str, parameters: tuple[object, ...],
) -> list:
    """一次点读：自建 Cursor ``fetchall`` 物化后经 ``closing`` 确定关闭。"""
    with closing(conn.execute(sql, parameters)) as cursor:
        return cursor.fetchall()


def _table_exists_on(conn: sqlite3.Connection, table: str) -> bool:
    """按精确表名查 ``main.sqlite_master``（参数化，无 DDL、无自愈）。"""
    return bool(_point_lookup(
        conn,
        "SELECT name FROM main.sqlite_master WHERE type='table' AND name=?",
        (table,),
    ))


def _validated_operation(operation: TeamOperation) -> _ArtifactSpec:
    """校验服务端 operation 并返回其 artifact 画像；未知组合 ``ValueError``。

    stage 域按画像核：trace 族（含 import）为 ``_TRACE_OPERATION_STAGES``
    （记录阶段 + ``history``，B5），其余 operation 不接受 ``history``。
    """
    if not isinstance(operation, TeamOperation):
        raise TypeError("operation 必须为 coscreen.team_flow_types.TeamOperation")
    name = operation.name
    if not isinstance(name, str):
        raise ValueError(f"未知 team 写 operation: {name!r}")
    spec = _OPERATION_ARTIFACTS.get(name)
    if spec is None:
        raise ValueError(f"未知 team 写 operation: {name!r}")
    stage = operation.stage
    if not isinstance(stage, str) or stage not in spec.stages:
        raise ValueError(f"operation {name!r} 不接受 stage {stage!r}")
    version = operation.expected_version
    if version is not None:
        if isinstance(version, bool) or type(version) is not int or version < 0:
            raise ValueError("expected_version 必须为非负整数")
    return spec


def _validated_artifact_id(
    spec: _ArtifactSpec, artifact_id: object,
) -> str | int | None:
    """按 artifact 家族校验 ID 形态；``None`` 仅允许出现在可选/无 artifact 家族。

    整数 ID 一律要求正数（AUTOINCREMENT 行 ID/sequence 从 1 起；零/负数不
    是合法行标识，属输入合同错误 ``ValueError``，不做无结果点读）。
    """
    if artifact_id is None:
        if spec.artifact_required:
            raise ValueError(
                f"operation 需要 {spec.label} 的 artifact ID，不能为 None")
        return None
    if spec.kind == _KIND_REQUEST_KEYS:
        raise ValueError("该 operation 不接受 artifact ID（请求键即变更主体）")
    if isinstance(artifact_id, bool):
        raise ValueError(f"{spec.label} artifact ID 不能为 bool")
    if type(artifact_id) is int:
        if artifact_id <= 0:
            raise ValueError(f"{spec.label} artifact ID 必须为正整数")
        return artifact_id
    if spec.kind in (_KIND_UNDO, _KIND_HIGHLIGHT, _KIND_CODING_NOTE, _KIND_FREEZE):
        raise ValueError(f"{spec.label} artifact ID 必须为整数行 ID")
    # trace / import：int=trace_events.sequence，str=event_id。
    if not isinstance(artifact_id, str):
        raise ValueError(
            f"{spec.label} artifact ID 必须为 event_id 文本或 sequence 整数")
    text = artifact_id.strip()
    if not text or len(text) > _MAX_EVENT_ID_TEXT:
        raise ValueError(
            f"{spec.label} artifact event_id 必须为 1..{_MAX_EVENT_ID_TEXT} 字符")
    return artifact_id


def _validated_row_item_key(raw: object, origin: str) -> str:
    """存储行读出的条目键过 P00 规范（拒空串/NUL/坏 Unicode）；坏键不可确认。"""
    if not isinstance(raw, str):
        raise _RecordUnresolvable(f"{origin} 行的条目键不是文本: {raw!r}")
    try:
        return normalize_item_keys((raw,))[0]
    except (TypeError, ValueError) as exc:
        raise _RecordUnresolvable(f"{origin} 行的条目键不合法: {raw!r}") from exc


def _is_canonical_uuid_text(value: object) -> bool:
    """是否为规范 UUID 文本（小写连字符形态，``str(uuid.UUID(v)) == v``）。

    依据写入路径实测：``trace_events.origin_store_id`` 源自 trace_store 单例
    ``store_id``（research_trace._ensure_store_id 用 ``str(uuid.uuid4())`` 落
    库）或 public 导出脱敏别名 ``Store N``（research_trace_export 的
    ``store_aliases``，见调用方的两形态校验）；``origin_event_id`` 由
    import_event 以 ``str(uuid.UUID(str(event['event_id']))`` 规范化后落库，
    不受脱敏影响、恒为规范 UUID。
    """
    if not isinstance(value, str):
        return False
    try:
        return str(uuid.UUID(value)) == value
    except (ValueError, TypeError, AttributeError):
        return False


def _require_trace_stage_alignment(
    operation: TeamOperation, row_stage: object, origin: str,
) -> None:
    """B6：trace/import 族行 stage 与 operation.stage 的一致性（fail-closed）。

    operation.stage 为具体记录阶段（screen/fulltext/coding）时，目标行必须
    属同一阶段（control 行亦视为不一致——具体阶段的 operation 不能指向控制
    事件）；operation.stage='history'（C02 混合历史）时行可为任意阶段，其
    合法性仍由 ``_trace_subject_cells`` 分类核（未知阶段照旧拒绝）。
    """
    if operation.stage == "history":
        return
    if row_stage != operation.stage:
        raise _RecordUnresolvable(
            f"{origin} 的记录阶段为 {row_stage!r}，与 operation 声明的 "
            f"{operation.stage!r} 不一致，不能安全执行")


def _dedupe_cells(cells: Iterable[AffectedCell]) -> tuple[AffectedCell, ...]:
    """保首次出现顺序去重（frozen datacell 可哈希）。"""
    ordered: dict[AffectedCell, None] = {}
    for cell in cells:
        if not isinstance(cell, AffectedCell):
            raise TypeError("footprint cells 必须为 AffectedCell")
        ordered.setdefault(cell, None)
    return tuple(ordered)


def _completed_footprint(cells: Iterable[AffectedCell]) -> MutationFootprint:
    """完整 footprint：``complete=True``、``reason=None``。"""
    return MutationFootprint(cells=_dedupe_cells(cells), complete=True, reason=None)


def _unresolvable_footprint(reason: str) -> MutationFootprint:
    """不可确认 footprint：``complete=False`` 且 ``reason`` 必填，不当空集。"""
    return MutationFootprint(cells=(), complete=False, reason=reason)


def _resolve_request_key_cells(
    stage: str, request_keys: tuple[str, ...],
) -> MutationFootprint:
    """无服务端 artifact 的写入（投票/批量保存/评价/评分员提交/共识 append）。

    请求键即变更主体；单元格阶段取 operation 的记录所属阶段（member_vote
    的 screen/fulltext、coding 族的 coding——编码/评价记录真正所属 coding，
    上游链资格阶段不进入单元格）。
    """
    return _completed_footprint(
        AffectedCell(stage=stage, item_key=key) for key in request_keys)


def _resolve_undo_artifact(
    conn: sqlite3.Connection, operation: TeamOperation, artifact_id: int,
) -> MutationFootprint:
    """workspace_undo 条目：单元格=该行自己的 (stage, zotero_key)。

    全快照 restore（coding 条目恢复 values/备注/证据、screen/fulltext 条目
    恢复决策行）实际影响的键/阶段取自存储行；行内 stage 与 operation 声明
    不一致时**不能安全执行**（客户端漏传/错传 stage 不能绕过），返回
    ``complete=False``。条目是否已 undone/superseded 属资格判断（P12），
    不属于 footprint。
    """
    if not _table_exists_on(conn, "workspace_undo"):
        raise _RecordUnresolvable(
            f"undo artifact {artifact_id!r} 无法解析：保留连接上没有 "
            "workspace_undo 表（journal 未准备）")
    rows = _point_lookup(
        conn,
        "SELECT stage, zotero_key FROM main.workspace_undo WHERE id=?",
        (artifact_id,),
    )
    if not rows:
        raise _RecordUnresolvable(
            f"undo artifact {artifact_id!r} 在保留连接上不存在，无法确认完整 "
            "footprint")
    origin = f"workspace_undo:{artifact_id}"
    row_stage = rows[0]["stage"]
    key = _validated_row_item_key(rows[0]["zotero_key"], origin)
    if row_stage not in _RECORD_STAGES:
        raise _RecordUnresolvable(
            f"{origin} 的记录阶段 {row_stage!r} 不是 screen/fulltext/coding")
    if row_stage != operation.stage:
        raise _RecordUnresolvable(
            f"{origin} 实际所属阶段为 {row_stage!r}，与 operation 声明的 "
            f"{operation.stage!r} 不一致，不能安全执行")
    return _completed_footprint([AffectedCell(stage=row_stage, item_key=key)])


def _resolve_highlight_artifact(
    conn: sqlite3.Connection, operation: TeamOperation, artifact_id: int,
) -> MutationFootprint:
    """highlights 行（高亮增删/页笔记）：单元格=(fulltext, zotero_key)。

    高亮与页笔记记录真正所属 fulltext（复筛），不因 operation 的链资格
    （stage2）改写单元格阶段；受保护键来自存储行，不来自客户端 zotero_key。
    """
    if not _table_exists_on(conn, "highlights"):
        raise _RecordUnresolvable(
            f"highlight artifact {artifact_id!r} 无法解析：保留连接上没有 "
            "highlights 表")
    rows = _point_lookup(
        conn, "SELECT zotero_key FROM main.highlights WHERE id=?", (artifact_id,))
    if not rows:
        raise _RecordUnresolvable(
            f"highlight artifact {artifact_id!r} 在保留连接上不存在，无法确认"
            "完整 footprint")
    key = _validated_row_item_key(rows[0]["zotero_key"], f"highlights:{artifact_id}")
    return _completed_footprint(
        [AffectedCell(stage="fulltext", item_key=key)])


def _resolve_coding_note_artifact(
    conn: sqlite3.Connection, operation: TeamOperation, artifact_id: int,
) -> MutationFootprint:
    """coding_notes 行（编码图文备注）：单元格=(coding, zotero_key)。

    编码备注记录真正所属 coding；其 dimension_id/field 身份不是文献键。
    """
    if not _table_exists_on(conn, "coding_notes"):
        raise _RecordUnresolvable(
            f"coding note artifact {artifact_id!r} 无法解析：保留连接上没有 "
            "coding_notes 表")
    rows = _point_lookup(
        conn, "SELECT zotero_key FROM main.coding_notes WHERE id=?", (artifact_id,))
    if not rows:
        raise _RecordUnresolvable(
            f"coding note artifact {artifact_id!r} 在保留连接上不存在，无法确认"
            "完整 footprint")
    key = _validated_row_item_key(
        rows[0]["zotero_key"], f"coding_notes:{artifact_id}")
    return _completed_footprint([AffectedCell(stage="coding", item_key=key)])


def _resolve_freeze_artifact(
    conn: sqlite3.Connection, operation: TeamOperation, artifact_id: int,
) -> MutationFootprint:
    """coding_consensus_freeze 的纯检查阶段 footprint：消费 consensus 选择。

    freeze 行（coding_freezes，coding_consensus.py:84-93：name/version/
    records_json/content_sha256，不可 UPDATE/DELETE）由 writer 在提交阶段才
    创建——纯检查阶段它尚不存在。artifact_id 因此解释为**被冻结的
    consensus 记录 ID**（``coding_consensus_records.id``），查其 item_key 得
    到实际受影响单元格 ``(coding, item_key)``；批量 freeze 由调用方对每个
    consensus 记录各解析一次再求并集（整批合法才进入 writer）。field_key 是
    field UUID，不猜成文献。被选记录不存在/结构未准备/键不合法 → 不能确认
    完整 footprint（complete=False），不当空集。
    """
    if not _table_exists_on(conn, "coding_consensus_records"):
        raise _RecordUnresolvable(
            f"consensus artifact {artifact_id!r} 无法解析：保留连接上没有 "
            "coding_consensus_records 表（freeze 的 writer 阶段才建 "
            "coding_freezes）")
    rows = _point_lookup(
        conn,
        "SELECT item_key FROM main.coding_consensus_records WHERE id=?",
        (artifact_id,),
    )
    if not rows:
        raise _RecordUnresolvable(
            f"consensus artifact {artifact_id!r} 在保留连接上不存在，无法确认"
            "完整 footprint")
    key = _validated_row_item_key(
        rows[0]["item_key"], f"coding_consensus_records:{artifact_id}")
    return _completed_footprint([AffectedCell(stage="coding", item_key=key)])


def _trace_subject_cells(
    stage: object, action: object, subject_id: object, origin: str,
) -> tuple[AffectedCell, ...]:
    """把一条 trace 记录的 (stage, action, subject_id) 分类成文献单元格。

    - control 阶段（设置/清空边界）：不是文献条目变更，无单元格但 footprint
      完整。
    - 条目类 action（``_TRACE_ITEM_ACTION_STAGES``）：subject 即文献键；记录
      阶段必须与该 action 的真实所属阶段一致（决策族 screen/fulltext、
      coding 族 coding），不一致=坏记录，拒绝而不是猜。
    - 协议/field UUID/控制类 action（``_TRACE_NON_ITEM_ACTIONS``）：无单元格。
    - 未知 action（含 ``imported_`` 前缀剥离后仍未知，如 ai_suggestion_*、
      report_evidence_changed）：无法分类，``_RecordUnresolvable``——不猜成
      文献，也不静默当协议标识（该两类是否入表待 P21/P24 裁定）。
    """
    if stage == "control":
        return ()
    if stage not in _RECORD_STAGES:
        raise _RecordUnresolvable(
            f"{origin}: 未知 trace 记录阶段 {stage!r}")
    if not isinstance(action, str) or not action:
        raise _RecordUnresolvable(f"{origin}: trace action 不合法: {action!r}")
    effective = action
    if effective.startswith(_IMPORTED_ACTION_PREFIX):
        effective = effective[len(_IMPORTED_ACTION_PREFIX):]
    expected_stages = _TRACE_ITEM_ACTION_STAGES.get(effective)
    if expected_stages is not None:
        if stage not in expected_stages:
            raise _RecordUnresolvable(
                f"{origin}: action {action!r} 的记录所属阶段应为 "
                f"{sorted(expected_stages)}，实际为 {stage!r}")
        key = _validated_row_item_key(subject_id, f"{origin}.subject_id")
        return (AffectedCell(stage=stage, item_key=key),)
    if effective in _TRACE_NON_ITEM_ACTIONS:
        return ()
    raise _RecordUnresolvable(
        f"{origin}: trace action {action!r} 的 subject 无法分类为文献键或"
        "协议/字段标识")


def _resolve_trace_artifact(
    conn: sqlite3.Connection, operation: TeamOperation, artifact_id: str | int,
) -> MutationFootprint:
    """trace_events 行（history/trace edit/delete/restore 的目标记录）。

    artifact 语义=**已存在**的 trace_events 行（B2）：int=sequence、
    str=event_id；行不存在 → ``complete=False``（"imports/commit 提交时新建
    N 行"的包事件语义属 P21 定稿，本包不实现）。单元格阶段取行内 stage
    （记录真正所属阶段），subject 分类见 ``_trace_subject_cells``；行 stage
    与 operation.stage 的一致性按 ``_require_trace_stage_alignment`` 核
    （'history' 混合历史放行任意合法记录阶段）。失败关闭点读，不走
    ``ensure_schema`` 默认 ensure。
    """
    if not _table_exists_on(conn, "trace_events"):
        raise _RecordUnresolvable(
            "trace artifact 无法解析：保留连接上没有 trace_events 表"
            "（trace 存储未准备）")
    sql = ("SELECT stage, action, subject_id FROM main.trace_events "
           "WHERE sequence=?")
    origin = f"trace_events.sequence={artifact_id}"
    if isinstance(artifact_id, str):
        sql = ("SELECT stage, action, subject_id FROM main.trace_events "
               "WHERE event_id=?")
        origin = f"trace_events.event_id={artifact_id!r}"
    rows = _point_lookup(conn, sql, (artifact_id,))
    if not rows:
        raise _RecordUnresolvable(
            f"{origin} 在保留连接上不存在，无法确认完整 footprint")
    row = rows[0]
    _require_trace_stage_alignment(operation, row["stage"], origin)
    cells = _trace_subject_cells(
        row["stage"], row["action"], row["subject_id"], origin)
    return _completed_footprint(cells)


def _resolve_import_artifact(
    conn: sqlite3.Connection, operation: TeamOperation, artifact_id: str | int,
) -> MutationFootprint:
    """trace import 原始记录：必须能在保留连接上确认其 origin 才可用。

    C08"unknown import origin 拒绝"：artifact 指向的行（B2：**已存在**的
    trace_events 行，import/restore 的包事件新建语义属 P21）必须是一条真正
    由 ``research_trace.import_event`` 落库的 import 记录——``source='import'``
    且 origin 四字段齐备并过格式校验（B7）：

    - ``origin_sha256``：64 位小写 hex（hexdigest 固有形态）；
    - ``origin_store_id``：规范 UUID 文本（本地/complete 包，源自
      trace_store 单例 ``str(uuid.uuid4())``）**或** public 导出脱敏别名
      ``Store N``（research_trace_export._public_snapshot 的
      ``store_aliases``，正则 ``^Store \\d+$``，public 包可正常导入）——
      校验防的是无意义垃圾文本，而非脱敏形态；
    - ``origin_event_id``：规范 UUID 文本（import_event 以
      ``str(uuid.UUID(...))`` 规范化落库）；
    - ``origin_json``：可 ``json.loads`` 且为 JSON 对象。

    格式与可解析性已核；**摘要与内容一致性由写入路径保证**
    （origin_event_sha256 在 import_event 落库时计算），本模块不重算，以免
    与历史序列化口径漂移。任一不满足 → ``complete=False``——不能把来源不明
    的记录当成可执行的 import 足迹。单元格分类复用
    ``_trace_subject_cells``（``imported_*`` action 剥前缀后按原 action
    分类）；行 stage 与 operation.stage 一致性同 trace 族（'history' 放行）。
    """
    if not _table_exists_on(conn, "trace_events"):
        raise _RecordUnresolvable(
            "import artifact 无法解析：保留连接上没有 trace_events 表"
            "（trace 存储未准备）")
    sql = ("SELECT stage, action, subject_id, source, origin_store_id, "
           "origin_event_id, origin_json, origin_sha256 FROM main.trace_events "
           "WHERE sequence=?")
    origin = f"trace_events.sequence={artifact_id}"
    if isinstance(artifact_id, str):
        sql = ("SELECT stage, action, subject_id, source, origin_store_id, "
               "origin_event_id, origin_json, origin_sha256 FROM main.trace_events "
               "WHERE event_id=?")
        origin = f"trace_events.event_id={artifact_id!r}"
    rows = _point_lookup(conn, sql, (artifact_id,))
    if not rows:
        raise _RecordUnresolvable(
            f"import artifact {origin} 在保留连接上不存在，无法确认完整 "
            "footprint")
    row = rows[0]
    if (row["source"] != "import" or not row["origin_store_id"]
            or not row["origin_event_id"]):
        raise _RecordUnresolvable(
            f"import artifact {origin} 不是可确认原始来源的 trace import 记录"
            "（unknown import origin）")
    if not row["origin_json"] or not row["origin_sha256"]:
        raise _RecordUnresolvable(
            f"import artifact {origin} 缺少完整 origin 见证（origin_json/"
            "origin_sha256），原始来源无法确认")
    sha256_text = row["origin_sha256"]
    if (not isinstance(sha256_text, str) or len(sha256_text) != 64
            or any(character not in "0123456789abcdef"
                   for character in sha256_text)):
        raise _RecordUnresolvable(
            f"import artifact {origin} 的 origin_sha256 不是 64 位小写 hex，"
            "原始来源无法确认")
    store_id_text = row["origin_store_id"]
    if not (_is_canonical_uuid_text(store_id_text)
            or (isinstance(store_id_text, str)
                and re.fullmatch(r"Store \d+", store_id_text) is not None)):
        raise _RecordUnresolvable(
            f"import artifact {origin} 的 origin_store_id 不是规范 UUID 文本"
            "或 public 导出脱敏别名（Store N），原始来源无法确认")
    if not _is_canonical_uuid_text(row["origin_event_id"]):
        raise _RecordUnresolvable(
            f"import artifact {origin} 的 origin_event_id 不是规范 UUID 文本，"
            "原始来源无法确认")
    try:
        decoded_origin = json.loads(row["origin_json"]) if isinstance(
            row["origin_json"], (str, bytes, bytearray)) else None
    except ValueError:
        decoded_origin = None
    if not isinstance(decoded_origin, dict):
        raise _RecordUnresolvable(
            f"import artifact {origin} 的 origin_json 不能解析为 JSON 对象，"
            "原始来源无法确认")
    _require_trace_stage_alignment(operation, row["stage"], origin)
    cells = _trace_subject_cells(
        row["stage"], row["action"], row["subject_id"], origin)
    return _completed_footprint(cells)


#: artifact 家族 → 解析器（全部 (conn, operation, artifact_id) 同签名）。
_ARTIFACT_RESOLVERS: Mapping[str, object] = {
    _KIND_UNDO: _resolve_undo_artifact,
    _KIND_HIGHLIGHT: _resolve_highlight_artifact,
    _KIND_CODING_NOTE: _resolve_coding_note_artifact,
    _KIND_FREEZE: _resolve_freeze_artifact,
    _KIND_TRACE: _resolve_trace_artifact,
    _KIND_IMPORT: _resolve_import_artifact,
}


def mutation_footprint_on(
    conn: sqlite3.Connection,
    *,
    operation: TeamOperation,
    artifact_id: str | int | None,
    request_keys: Iterable[str],
) -> MutationFootprint:
    """在保留写窗口连接上解析一次团队 mutation 的真实受影响单元格。

    - ``conn``：C02 借用合同（native 连接/type 继承、活动事务、
      ``row_factory is sqlite3.Row``；已关闭连接的 ``ProgrammingError``
      原样透传）。本函数零 DDL、零事务控制、不改 PRAGMA/factory；自建
      Cursor 全部物化并确定关闭。
    - ``operation``：服务端选择的 ``TeamOperation``；未知 name/stage 组合、
      非法 ``expected_version`` → ``ValueError``（编程错误，不是
      footprint 语义）。trace 族（含 import）stage 域为记录阶段 + 
      ``history``（C02 混合历史）。
    - ``artifact_id``：服务端 artifact ID（undo/highlight/coding note 的整数
      行 ID；consensus freeze 的被冻结 consensus 记录整数 ID；trace/import
      的 event_id 文本或 sequence 整数；均须正数）。无 artifact 家族必须为
      ``None``（请求键即主体）；必填家族缺 ID → ``ValueError``。
    - ``request_keys``：客户端请求键，仅做 P00 规范校验（拒 str/bytes/
      空串/NUL/坏 Unicode/空集），**不当权威**——artifact 路径的单元格
      全部来自保留连接上的原始记录。

    返回：``complete=True`` 时 ``cells`` 是实际受影响单元格（可为空：例
    如协议/field UUID/控制类 trace 记录确实不碰文献）；``complete=False``
    时 ``reason`` 必填（artifact 缺行/结构未准备/载荷损坏/阶段不一致/
    subject 无法分类/import 原始来源未确认），表示**不能安全执行**，绝不
    当空集放行。锚：``assert footprint.complete or footprint.reason
    is not None``。
    """
    _require_borrowed_footprint_connection(conn, api="mutation_footprint_on")
    keys = normalize_item_keys(request_keys)
    spec = _validated_operation(operation)
    artifact = _validated_artifact_id(spec, artifact_id)
    if artifact is None:
        # 新增型写入（annotation add/note create/投票/批量保存）：尚无服务端
        # artifact 行，请求键即变更主体；canonical 域校验由写窗负责，本模块
        # 不过滤（离开 catalog 的历史键仍受保护）。
        return _resolve_request_key_cells(operation.stage, keys)
    resolver = _ARTIFACT_RESOLVERS[spec.kind]
    try:
        return resolver(conn, operation, artifact)  # type: ignore[operator]
    except _RecordUnresolvable as exc:
        return _unresolvable_footprint(exc.reason)
