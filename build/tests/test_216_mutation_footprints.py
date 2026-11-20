# -*- coding: utf-8 -*-
"""P11 真实 mutation footprint（team write footprints）独立测试候选 —— 静态撰写，作者未运行。

目标产品候选（本文件唯一针对的实现）：
- E:/rf216-scratch/p11-footprints-20261118T123733Z-7401/candidate3/team_write_footprints.py
- SHA-256 8aacbc6951ab7e4a192f63c2a5786f07451c7fd45c1ac72e0ea6fce97b657fd1
  （757 行；已过两轮差量复审+主控快审——B1-B7 全修，含 origin_store_id 双形态）
- 主仓基线不含 P11（已核：全仓无 team_write_footprints.py）：本文件在测试箱内对
  候选副本（箱根/candidate/team_write_footprints.py，SHA 已核一致）经 importlib
  文件位置加载运行（parents 协议同 P05/P06/P07 测试箱；装载在 exec 前注册
  sys.modules、失败 identity-guard 回滚——P03 candidate4 配方：候选含
  @dataclass+future annotations，CPython 3.12 dataclasses._is_type 需模块已
  注册，否则首个 @dataclass 即 AttributeError；candidate1 隔离 run 0/8 收集
  即此因）；若对主仓 build/tests 运行将在模块加载期因 candidate/ 缺位而诚实红
  （不以 ImportError 冒充行为红证；集成时由协调者迁移加载路径）。
- 候选 import coscreen.team_flow_types（P00 AffectedCell/MutationFootprint/
  TeamOperation）与 custom_backend.team_keyed_domain（再导出 P00
  normalize_item_keys）：本文件把主仓根 E:/mubi-reviewflow 插入 sys.path 供其
  解析；运行解释器必须 -B（隔离模式不向只读主仓写 __pycache__）。主仓缺位时本
  文件以明确 RuntimeError 拒绝静默降级。

合同依据（全部只读引用，本文不编辑任何合同/产品文件）：
- 2.1.6 team contracts C08/C11（真实 mutation footprint）与 C02（键/连接/错误
  共同合同）相关节；P11 包工单钉定本文件名与 5 个具名用例。
- 复审已定口径（写测试必须遵守，本文件逐条落实）：
  1. B1：freeze 的 artifact_id=coding_consensus_records.id（点读 item_key），
     不再读 coding_freezes 表——用例 5 以"coding_freezes 行 id 命中而
     consensus 无此 id"与"仅存 coding_freezes 表"两个诱饵库钉死。
  2. B2：team_trace_import/restore 的 artifact=已存在 trace_events 行
     （str=event_id/int=sequence）；行不存在→complete=False——用例 3/5 各钉
     str/int 两形态缺失。
  3. B6：operation.stage∈记录阶段时行 stage 必须一致否则 complete=False；
     ='history' 放行任意合法行 stage——用例 4/5/支撑 3 逐处落实。
  4. B3 表=25 条目类 action（含 rater_record_changed）+12 非条目+3 刻意排除
     （ai_suggestion_*/report_evidence_changed fail-closed）——本文件以测试侧
     冻结镜像表逐名循环钉（不取自模块，独立钉住合同形状）；未知 action→
     complete=False 是正控不是缺陷（支撑 2 构造点之一）。
  5. B7 两形态：origin_store_id=规范 UUID 通过、='Store 1'（public 脱敏别名）
     通过、='garbage' 拒；origin_event_id 非 UUID 拒；origin_sha256 非 64 位
     小写 hex 拒；origin_json 坏 JSON/非对象拒——用例 3 全矩阵。
  6. 正数校验：artifact_id int<=0→ValueError（bool 同拒）——用例 1 输入合同
     节，且以探针窗口证"输入合同错误零 SQL"（先于任何存储访问）。
  7. request_keys 仅输入合同校验（P00 normalize：拒 str/bytes/空串/NUL/坏
     Unicode/空集），绝不参与 artifact 路径单元格计算——用例 1 主体。

用例 → 合同条款映射（5 具名用例，工单钉死名称）：
- ArtifactKeySpoofingTests.test_artifact_key_spoofing
    C08 键权威：六个 artifact 家族（undo/highlight×2 op/coding_note/freeze/
    trace×str+int/import）的存储行键≠请求欺骗键，footprint 全部取存储行键，
    欺骗键绝不入格；请求键坏形态（空集/裸 str/NUL/坏 Unicode/非 str 成员）在
    artifact 分支同样先校验 ValueError；200 字符 event_id 上界正控；输入合同
    ValueError 家族零 SQL（探针窗口为空）。
- RemovedCatalogKeyTests.test_removed_catalog_key_still_protected
    C08 保护面：无 articles 表的库、有 catalog 但键已离开名单、旧 round 任务行
    三种构造下，六个家族的历史键照样完整入格；请求键族（member_vote）同样不过
    滤；探针窗口零 articles/canonical/decisions 域读取（解析全程无域过滤）。
- UnknownImportOriginTests.test_unknown_import_origin_rejected
    C08 import 溯源：非 source='import' 行（即使 origin 见证齐备）/缺任一见证/
    store 双形态正控+垃圾拒/大写非规范 UUID 拒/event_id 非 UUID 拒/sha 63 位与
    大写拒/origin_json 坏 JSON 与非对象拒；B2 缺失行 str/int 拒；B6 import 行
    阶段不一致拒、'history' 放行正控；全部 complete=False 伴 reason 非空。
- CodingNotScreeningTests.test_coding_is_not_screening_history
    C08 阶段语义：25 条目类 action 逐名合法阶段→完整单格、全部非法阶段→
    complete=False（_TRACE_ITEM_ACTION_STAGES 强制，经 history 放行隔离钉）；
    12 非条目（control 行经 history + 记录阶段变体）→完整零格；3 刻意排除 +
    imported_ 变体 + import 路径变体→fail-closed；编码类 artifact（coding_note/
    undo coding 行/rater_record_changed）取行内真实 coding 格；筛选完成不外推
    coding 锁（fulltext 决策行产 fulltext 格、screening op 消费 coding 行必拒、
    member_undo 声明 screen 指 coding 行必拒、vote/appraisal 各归各阶段）。
- RestoreSnapshotTests.test_restore_full_snapshot_cells
    C08 快照恢复：undo restore 的 footprint=行内 (stage,zotero_key) 单格
    （screen/fulltext/coding 三行、载荷不影响格）；行 stage 与 operation.stage
    不一致/行 stage 非记录阶段（'history'/'weird'）/行缺失/表未准备→
    complete=False 且不建表（无 DDL 自愈）；freeze 经 consensus 记录展开
    item_key 单格 + B1 双诱饵；trace restore str/int 同格、缺失与空白 padding
    不模糊匹配；锚 assert footprint.complete or footprint.reason is not None
    经 _footprint 助手对全路径生效（本用例文档化）。

支撑用例（工单允许 2-3 个，参照 P05/P07 先例）：
- BorrowedDisciplineTests.test_borrowed_connection_contract_and_zero_side_effects
    C02 借用纪律：非 native/`__class__` 伪装→TypeError；无活动事务/非 Row 工厂
    →RuntimeError；closed 连接 sqlite3.ProgrammingError 精确透传；native 子类
    探针正控——纯 SELECT 语句面、零 txn/DDL/DML/PRAGMA、自建 Cursor 全部
    fetchall 物化并确定关闭、零 commit/rollback/close、连接所有权未被动、表集
    与行数不变。
- IncompleteReasonTests.test_incomplete_footprint_carries_reason
    完整性口径：complete=False⇒reason 必填且 cells=() 的构造点——行缺失/
    subject 无法分类（未知 action）/存储行键 BLOB 非 str/键含 NUL/subject 含
    NUL，各配正控对照（同形好键完整）。
- HistoryStageDomainTests.test_history_stage_domain_boundaries
    B5 history 域：trace 三名（edit/delete/restore）×三记录阶段全放行；import
    族 history 放行（三条带完整见证行）；history 不豁免行阶段合法性（未知行阶
    段仍拒）；control 行经 history 完整零格、具体阶段 op 指控制行必拒；十个非
    trace 名全拒 'history'（ValueError）；未知 name/坏 stage（control/stage1/
    stage2/空串）ValueError；expected_version 拒负数与 bool、收 0 与正数。

夹具设计（零 mock）：真实临时库 + 真实表结构（DDL 逐字镜像 db.py /
coding_consensus.py / workspace_undo.py / research_trace.py 的 CREATE TABLE；
workspace_undo 镜像为基表+_ensure_journal_columns 终态列内联等价）+ 直插行；
探针为真实 sqlite3.Connection 原生子类（type 继承判断接受），非替换对象；
不触发任何产品 ensure/自愈路径（候选自身零 DDL，负控由"调用后表仍缺位"钉）。

诚实记录（不可达/不钉项，不伪造测试）：
- reason 具体文案不钉（只钉非空 str 与行为）；消息措辞属实现自由度。
- origin_json 的 BLOB 形态（直插 bytes，TEXT 亲缘不转换 BLOB）会被冻结候选按
  json.loads(bytes) 接受——工单未定义其拒收，本文件不构造也不钉该形态。
- 成员投票/批量保存等请求键族的 canonical 域校验属写窗职责（候选 docstring
  明示不过滤），本文件只钉"不过滤"，不冒充写窗断言。
- research_trace.get_event / ensure_schema 路径未被候选借用（docstring 明示），
  其行为不在本包测试面；"不走 ensure"以无表库调用后仍无表钉。
- 探针 cursor 提供 fetchmany 仅为代理完整性；冻结候选只用 execute/fetchall/
  close（纪律断言按 fetchall+closed 钉，不要求 fetchmany 被调）。

撰写协议（同 P05/P06/P07 先例）：本文件每个 def/class 符号（含 dunder 与
`__class__` property，共 47 项，与 raw/SYMBOLS.tsv 一致）在写入前均逐项运行
GitNexus CLI upstream impact（--repo E:/mubi-reviewflow --file
build/tests/test_216_mutation_footprints.py --kind Class/Method/Function
--include-tests）；新文件预期 not found/UNKNOWN，raw/rc/MANIFEST 存档于
E:/rf216-scratch/p11-foot-tests-20261118T191112Z-5331/raw/（共 47 项，每次
CLI 前经 ps 门确认无其他 gitnexus 作业；全 47 项 not found + risk UNKNOWN 已
核）。本文件零 lambda、零内嵌 def（AST 自检钉），符号清单与文件一致。

未运行声明：作者仅静态撰写（ast.parse/compile 与符号清单自检），未运行任何
测试用例/产品候选 API，未查询真实库；执行留给独立 runner 在应用产品候选后进行
（运行解释器须 -B；夹具全部位于测试自有 TemporaryDirectory）。禁主仓/前端/
真实 data/旧证据箱写入；零 mock；自包含：不依赖 conftest/Safety 夹具、不触
HTTP 层；静态消费者仅 unittest。
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import sqlite3
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

_P11_BOX_ROOT = Path(__file__).resolve().parents[2]
_P11_CANDIDATE = _P11_BOX_ROOT / "candidate" / "team_write_footprints.py"
_P11_REPO_ROOT = Path("E:/mubi-reviewflow")
if not _P11_REPO_ROOT.is_dir():
    raise RuntimeError(
        "P11 测试需要主仓 E:/mubi-reviewflow（候选 import coscreen P00 类型与"
        " custom_backend.team_keyed_domain 再导出的 normalize_item_keys）；"
        "主仓缺位时本文件拒绝静默降级")
if str(_P11_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_P11_REPO_ROOT))
from coscreen.team_flow_types import (  # noqa: E402
    AffectedCell,
    MutationFootprint,
    TeamOperation,
)
_P11_SPEC = importlib.util.spec_from_file_location(
    "team_write_footprints", _P11_CANDIDATE)
twf = importlib.util.module_from_spec(_P11_SPEC)
# candidate2 装载器修（P03 candidate4 标准配方，逐句照抄）：exec 前先注册
# sys.modules；失败仅当 sys.modules.get(spec.name) is module（identity-guard，
# 防误删并发加载的同名模块）才回滚再抛。候选 = ``from __future__ import
# annotations`` + @dataclass（team_write_footprints.py:193 _ArtifactSpec），
# CPython 3.12 dataclasses._is_type 对字符串注解无守护解引用
# sys.modules.get(cls.__module__).__dict__——不注册即在首个 @dataclass 上
# AttributeError（candidate1 隔离 run 0/8 收集即此因：模块导入期
# exec_module → :193 @dataclass）。
sys.modules[_P11_SPEC.name] = twf
try:
    _P11_SPEC.loader.exec_module(twf)
except BaseException:
    if sys.modules.get(_P11_SPEC.name) is twf:
        del sys.modules[_P11_SPEC.name]
    raise

#: 冻结候选 SHA-256（运行时打印实测值供核对，不做硬断言：候选后续修订经
#: 复审后 SHA 自然变化，硬断言会制造假红）。
P11_CANDIDATE_SHA = (
    "8aacbc6951ab7e4a192f63c2a5786f07451c7fd45c1ac72e0ea6fce97b657fd1")

P11_RECORD_STAGES = frozenset({"screen", "fulltext", "coding"})
P11_TRACE_OPERATION_STAGES = P11_RECORD_STAGES | {"history"}

#: B3 条目类 action 镜像（测试侧冻结常量，不取自模块）：25 项。
P11_ITEM_ACTION_STAGES = {
    "decision_created": frozenset({"screen", "fulltext"}),
    "decision_changed": frozenset({"screen", "fulltext"}),
    "decision_imported": frozenset({"screen", "fulltext"}),
    "decision_undone": frozenset({"screen", "fulltext"}),
    "baseline_decision": frozenset({"screen", "fulltext"}),
    "baseline_item": frozenset({"screen"}),
    "fulltext_added": frozenset({"fulltext"}),
    "fulltext_replaced": frozenset({"fulltext"}),
    "coding_value_created": frozenset({"coding"}),
    "coding_value_changed": frozenset({"coding"}),
    "coding_value_cleared": frozenset({"coding"}),
    "coding_values_undone": frozenset({"coding"}),
    "coding_note_created": frozenset({"coding"}),
    "coding_note_changed": frozenset({"coding"}),
    "coding_note_cleared": frozenset({"coding"}),
    "coding_note_image_added": frozenset({"coding"}),
    "coding_note_image_removed": frozenset({"coding"}),
    "coding_evidence_undone": frozenset({"coding"}),
    "coding_image_ocr_changed": frozenset({"coding"}),
    "baseline_value": frozenset({"coding"}),
    "baseline_coding_note": frozenset({"coding"}),
    "baseline_coding_image": frozenset({"coding"}),
    "baseline_rater_revision": frozenset({"coding"}),
    "rater_record_created": frozenset({"coding"}),
    "rater_record_changed": frozenset({"coding"}),
}

#: B3 非条目类 action 镜像：12 项（协议标识/field UUID/控制主体）。
P11_NON_ITEM_ACTIONS = frozenset({
    "baseline_protocol", "baseline_field",
    "field_definition_created", "field_definition_changed",
    "field_definition_deleted",
    "recording_started", "recording_resumed", "recording_paused",
    "recording_stopped", "stages_changed", "history_cleared",
    "binary_maybe_reset",
})

#: B3 刻意排除（fail-closed，待 P21/P24 裁定）：3 项。
P11_EXCLUDED_ACTIONS = frozenset({
    "ai_suggestion_generated", "ai_suggestion_decided",
    "report_evidence_changed",
})

#: 确定性规范 UUID 文本（uuid5，运行/复核皆可复算；str(uuid.UUID(v))==v）。
P11_STORE_UUID = str(uuid.uuid5(uuid.NAMESPACE_DNS, "p11.footprints.store"))
P11_ORIGIN_UUIDS = tuple(
    str(uuid.uuid5(uuid.NAMESPACE_DNS, "p11.footprints.origin.%d" % index))
    for index in range(16))

P11_ORIGIN_SHA = hashlib.sha256(b"P11-footprint-origin-record").hexdigest()
P11_ORIGIN_JSON_TEXT = json.dumps(
    {"event_id": P11_ORIGIN_UUIDS[0], "sequence": 7, "stage": "screen",
     "action": "decision_changed", "subject_id": "IMP-ORIGIN",
     "recorded_at": "2026-11-01T00:00:00Z"},
    sort_keys=True, separators=(",", ":"))

# ---------------------------------------------------------------------------
# 真实表结构 DDL 冻结镜像（逐字对照主仓建表；仅镜像 CREATE TABLE，索引与
# footprint 点读行为无关故不附）：
# - articles/highlights/coding_notes：coscreen/db.py
# - workspace_undo：coscreen/workspace_undo.py 基表 + _ensure_journal_columns
#   终态列（request_id/superseded）内联等价
# - coding_consensus_records/coding_freezes：coscreen/coding_consensus.py
# - trace_events：coscreen/research_trace.py
# ---------------------------------------------------------------------------

P11_ARTICLES_DDL = (
    "CREATE TABLE IF NOT EXISTS articles (\n"
    "  zotero_key TEXT PRIMARY KEY, item_type TEXT, title TEXT, authors TEXT,\n"
    "  journal TEXT, year INTEGER, doi TEXT, abstract TEXT, url TEXT,\n"
    "  source_format TEXT, raw_json TEXT, content_hash TEXT,\n"
    "  is_duplicate_of TEXT,\n"
    "  import_order INTEGER\n"
    ")"
)

P11_HIGHLIGHTS_DDL = (
    "CREATE TABLE IF NOT EXISTS highlights (\n"
    "  id INTEGER PRIMARY KEY AUTOINCREMENT,\n"
    "  zotero_key TEXT NOT NULL REFERENCES articles(zotero_key),\n"
    "  page INTEGER NOT NULL,\n"
    "  text TEXT NOT NULL,\n"
    "  color TEXT NOT NULL DEFAULT 'jade',\n"
    "  note TEXT DEFAULT '',\n"
    "  created_at TEXT NOT NULL\n"
    ")"
)

P11_CODING_NOTES_DDL = (
    "CREATE TABLE IF NOT EXISTS coding_notes (\n"
    "  id INTEGER PRIMARY KEY AUTOINCREMENT, dimension_id INTEGER NOT NULL,"
    " zotero_key TEXT NOT NULL,\n"
    "  text TEXT DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL,\n"
    "  UNIQUE(dimension_id, zotero_key))"
)

P11_WORKSPACE_UNDO_DDL = (
    "CREATE TABLE IF NOT EXISTS workspace_undo (\n"
    "id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL,"
    " actor_id TEXT NOT NULL,\n"
    "stage TEXT NOT NULL, zotero_key TEXT NOT NULL, before_json TEXT NOT NULL,\n"
    "after_json TEXT NOT NULL, undone INTEGER NOT NULL DEFAULT 0,\n"
    "request_id TEXT, superseded INTEGER NOT NULL DEFAULT 0)"
)

P11_CONSENSUS_RECORDS_DDL = (
    "CREATE TABLE IF NOT EXISTS coding_consensus_records (\n"
    "  id INTEGER PRIMARY KEY AUTOINCREMENT,\n"
    "  item_key TEXT NOT NULL,\n"
    "  field_key TEXT NOT NULL,\n"
    "  version INTEGER NOT NULL CHECK(version > 0),\n"
    "  supersedes_id INTEGER REFERENCES coding_consensus_records(id),\n"
    "  reconciler_id TEXT NOT NULL,\n"
    "  value_json TEXT NOT NULL,\n"
    "  source_json TEXT NOT NULL,\n"
    "  rationale TEXT NOT NULL,\n"
    "  created_at TEXT NOT NULL,\n"
    "  UNIQUE(item_key, field_key, version)\n"
    ")"
)

P11_CODING_FREEZES_DDL = (
    "CREATE TABLE IF NOT EXISTS coding_freezes (\n"
    "  id INTEGER PRIMARY KEY AUTOINCREMENT,\n"
    "  name TEXT NOT NULL,\n"
    "  version INTEGER NOT NULL CHECK(version > 0),\n"
    "  previous_freeze_id INTEGER REFERENCES coding_freezes(id),\n"
    "  frozen_by TEXT NOT NULL,\n"
    "  created_at TEXT NOT NULL,\n"
    "  records_json TEXT NOT NULL,\n"
    "  content_sha256 TEXT NOT NULL,\n"
    "  UNIQUE(name, version)\n"
    ")"
)

P11_TRACE_EVENTS_DDL = (
    "CREATE TABLE IF NOT EXISTS trace_events (\n"
    "sequence INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL UNIQUE,"
    " \n"
    "store_id TEXT NOT NULL, task_id TEXT NOT NULL, actor_id TEXT NOT NULL, \n"
    "stage TEXT NOT NULL, action TEXT NOT NULL, subject_id TEXT NOT NULL, \n"
    "before_json TEXT, after_json TEXT, field_json TEXT, source TEXT NOT NULL,"
    " \n"
    "source_record_id TEXT, source_revision_id TEXT, batch_id TEXT,"
    " request_id TEXT, \n"
    "recorded_at TEXT NOT NULL, schema_version TEXT NOT NULL,"
    " app_version TEXT NOT NULL, \n"
    "origin_store_id TEXT, origin_event_id TEXT, origin_sequence INTEGER, \n"
    "origin_actor_id TEXT, imported_at TEXT, origin_alias TEXT,"
    " origin_json TEXT, \n"
    "origin_sha256 TEXT, UNIQUE(task_id,actor_id,origin_store_id,"
    "origin_event_id))"
)

TAG_SPOOF = "P11-artifact key spoofing"
TAG_CATALOG = "P11-removed catalog key protection"
TAG_IMPORT = "P11-unknown import origin"
TAG_CODING = "P11-coding is not screening history"
TAG_RESTORE = "P11-restore full snapshot cells"
TAG_BORROW = "P11-borrowed connection discipline"
TAG_REASON = "P11-incomplete footprint reason"
TAG_HISTORY = "P11-history stage domain"

_P11_TXN_KEYWORDS = frozenset(
    {"BEGIN", "COMMIT", "ROLLBACK", "SAVEPOINT", "RELEASE", "END"})
_P11_DDL_KEYWORDS = frozenset(
    {"CREATE", "DROP", "ALTER", "VACUUM", "REINDEX", "ANALYZE"})
_P11_DML_KEYWORDS = frozenset(
    {"INSERT", "UPDATE", "DELETE", "REPLACE", "MERGE"})

_P11_PROVENANCE_PRINTED = False


def _p11_first_keyword(sql_text):
    """剥前导 ``--`` 注释行后取首关键字/记号（P07 先例 _p07_first_keyword）。"""
    text = sql_text.strip()
    while text.startswith("--"):
        remainder = text.split("\n", 1)
        text = remainder[1].strip() if len(remainder) > 1 else ""
    if not text:
        return ""
    return re.split(r"[\s(]", text, 1)[0].upper()


def _p11_classify_statements(statements):
    """按首真实关键字分类语句（select/dml/ddl/txn/pragma/other 六桶）。"""
    buckets = {
        "select": [], "dml": [], "ddl": [], "txn": [], "pragma": [], "other": [],
    }
    for statement in statements:
        keyword = _p11_first_keyword(statement)
        if keyword in _P11_DML_KEYWORDS:
            buckets["dml"].append(statement)
        elif keyword in _P11_DDL_KEYWORDS:
            buckets["ddl"].append(statement)
        elif keyword in _P11_TXN_KEYWORDS:
            buckets["txn"].append(statement)
        elif keyword == "PRAGMA":
            buckets["pragma"].append(statement)
        elif keyword in ("SELECT", "WITH"):
            buckets["select"].append(statement)
        else:
            buckets["other"].append(statement)
    return buckets


# ---------------------------------------------------------------------------
# 观测探针（调用方自建连接，真实 native 子类，零 mock；C02 的真实 type 继承
# 判断接受 _P11ProbeConnection——它是真 sqlite3.Connection 子类；
# _P11DelegatingConnection 是 __class__ 伪装反例）。
# ---------------------------------------------------------------------------

class _P11ProbeCursor:
    """Cursor 代理（仅观测）：登记 execute SQL 与 fetch/close 行为。"""

    def __init__(self, cursor, registry=None):
        self._cursor = cursor
        self.sql = ""
        self._registry = registry
        self.rows_returned = 0
        self.fetchall_called = False
        self.fetchone_called = False
        self.saw_empty = False
        self.closed = False
        if registry is not None:
            registry.append(self)

    def execute(self, sql, parameters=()):
        self._cursor.execute(sql, parameters)
        self.sql = sql
        return self

    def fetchone(self):
        row = self._cursor.fetchone()
        self.fetchone_called = True
        if row is None:
            self.saw_empty = True
        else:
            self.rows_returned += 1
        return row

    def fetchall(self):
        rows = self._cursor.fetchall()
        self.fetchall_called = True
        self.rows_returned += len(rows)
        if not rows:
            self.saw_empty = True
        return rows

    def fetchmany(self, size=-1):
        rows = self._cursor.fetchmany(size)
        self.rows_returned += len(rows)
        if not rows:
            self.saw_empty = True
        return rows

    def close(self):
        if self.closed:
            return
        self._cursor.close()
        self.closed = True


class _P11ProbeConnection(sqlite3.Connection):
    """Native 子类探针：语句 trace + commit/rollback/close 计数 + cursor 登记。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.statements = []
        self.cursors = []
        self.commit_calls = 0
        self.rollback_calls = 0
        self.close_calls = 0
        self._p11_closed = False
        self.set_trace_callback(self.statements.append)

    def execute(self, sql, parameters=()):
        raw = super().execute(sql, parameters)
        cursor = _P11ProbeCursor(raw, self.cursors)
        cursor.sql = sql
        return cursor

    def commit(self):
        self.commit_calls += 1
        return super().commit()

    def rollback(self):
        self.rollback_calls += 1
        return super().rollback()

    def close(self):
        if self._p11_closed:
            return
        super().close()
        self._p11_closed = True
        self.close_calls += 1


class _P11DelegatingConnection:
    """非 native 伪装代理：转发 row_factory/in_transaction 等属性。

    C02 要求真实 type 继承判断，``__class__`` 伪装必须被拒。
    """

    def __init__(self, conn):
        self._conn = conn

    @property
    def __class__(self):
        return sqlite3.Connection

    def __getattr__(self, name):
        return getattr(self._conn, name)


# ---------------------------------------------------------------------------
# 共用基座：临时箱 + 真实表结构建库 + 直插行夹具 + 借用连接工厂 + footprint 助手
# ---------------------------------------------------------------------------

class MutationFootprintTestBase(unittest.TestCase):
    """公共基座：全部库文件由本测试自建（DDL 冻结镜像），零产品 ensure。"""

    maxDiff = None

    def setUp(self):
        global _P11_PROVENANCE_PRINTED
        self._tmp = tempfile.TemporaryDirectory(prefix="rf216_p11_")
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        self._p11_ev_auto = 0
        if not _P11_PROVENANCE_PRINTED:
            _P11_PROVENANCE_PRINTED = True
            print("P11_TEST_FILE_SHA",
                  hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
            print("P11_CANDIDATE_SOURCE_SHA",
                  hashlib.sha256(_P11_CANDIDATE.read_bytes()).hexdigest())
            print("P11_CANDIDATE_PINNED_SHA", P11_CANDIDATE_SHA)
            print("P11_PYTHON", sys.version.split()[0])
            print("P11_SQLITE_VERSION", sqlite3.sqlite_version)

    def _new_db(self, name, ddls=()):
        """真实临时库：建文件 → 执行给定 DDL 镜像 → 关闭（无产品路径）。"""
        path = self.base / name
        setup = sqlite3.connect(str(path))
        try:
            for ddl in ddls:
                setup.execute(ddl)
            setup.commit()
        finally:
            setup.close()
        return path

    def _borrow(self, path, *, factory=None, begin=True):
        """C02 借用形态：Row 工厂 + 调用方活动事务（可选探针子类）。"""
        if factory is None:
            conn = sqlite3.connect(str(path), timeout=15)
        else:
            conn = sqlite3.connect(str(path), timeout=15, factory=factory)
        self.addCleanup(self._close_quietly, conn)
        conn.row_factory = sqlite3.Row
        if begin:
            conn.execute("BEGIN IMMEDIATE")
        return conn

    def _footprint(self, conn, name, stage, *, artifact_id=None,
                   request_keys=("P11-REQ-KEY",), expected_version=None):
        """统一入口 + 完整性锚：complete 或 reason 必填（全路径生效）。"""
        operation = TeamOperation(
            name=name, stage=stage, expected_version=expected_version)
        footprint = twf.mutation_footprint_on(
            conn, operation=operation, artifact_id=artifact_id,
            request_keys=request_keys)
        self.assertIsInstance(footprint, MutationFootprint)
        self.assertTrue(
            footprint.complete or footprint.reason is not None,
            "锚失败：complete=False 必须 reason 非空（%s/%s）" % (name, stage))
        if footprint.complete:
            self.assertIsNone(footprint.reason)
        else:
            self.assertIsInstance(footprint.reason, str)
            self.assertTrue(footprint.reason)
            self.assertEqual(footprint.cells, ())
        return footprint

    def _insert_undo(self, conn, *, stage, zotero_key, task_id="p11-task",
                     actor_id="p11-actor",
                     before_json='{"record": null}',
                     after_json='{"record": {"decision": "include"}}'):
        cursor = conn.execute(
            "INSERT INTO main.workspace_undo (task_id,actor_id,stage,zotero_key,"
            "before_json,after_json) VALUES (?,?,?,?,?,?)",
            (task_id, actor_id, stage, zotero_key, before_json, after_json))
        return cursor.lastrowid

    def _insert_highlight(self, conn, *, zotero_key, page=1):
        cursor = conn.execute(
            "INSERT INTO main.highlights (zotero_key,page,text,color,note,"
            "created_at) VALUES (?,?,?,?,?,?)",
            (zotero_key, page, "P11 选段文本", "jade", "",
             "2026-11-18T12:00:00Z"))
        return cursor.lastrowid

    def _insert_coding_note(self, conn, *, zotero_key, dimension_id=1):
        cursor = conn.execute(
            "INSERT INTO main.coding_notes (dimension_id,zotero_key,text,"
            "created_at,updated_at) VALUES (?,?,?,?,?)",
            (dimension_id, zotero_key, "P11 编码备注", "2026-11-18T12:00:00Z",
             "2026-11-18T12:00:00Z"))
        return cursor.lastrowid

    def _insert_consensus(self, conn, *, item_key,
                          field_key="11111111-2222-4333-8444-555566667777",
                          version=1):
        cursor = conn.execute(
            "INSERT INTO main.coding_consensus_records (item_key,field_key,"
            "version,reconciler_id,value_json,source_json,rationale,created_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (item_key, field_key, version, "p11-reconciler",
             '{"value": "yes"}', '{"rater": "p11"}', "P11 fixture consensus",
             "2026-11-18T12:00:00Z"))
        return cursor.lastrowid

    def _insert_trace(self, conn, *, stage, action, subject_id,
                      source="manual", event_id=None, task_id="p11-task",
                      actor_id="p11-actor", origin_store_id=None,
                      origin_event_id=None, origin_json=None,
                      origin_sha256=None):
        if event_id is None:
            self._p11_ev_auto += 1
            event_id = "p11-ev-auto-%03d" % self._p11_ev_auto
        cursor = conn.execute(
            "INSERT INTO main.trace_events (event_id,store_id,task_id,actor_id,"
            "stage,action,subject_id,source,recorded_at,schema_version,"
            "app_version,origin_store_id,origin_event_id,origin_json,"
            "origin_sha256) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (event_id, P11_STORE_UUID, task_id, actor_id, stage, action,
             subject_id, source, "2026-11-18T12:00:00Z", "p11-test", "2.1.6",
             origin_store_id, origin_event_id, origin_json, origin_sha256))
        return cursor.lastrowid, event_id

    def _table_names(self, conn):
        rows = conn.execute(
            "SELECT name FROM main.sqlite_master WHERE type='table'"
        ).fetchall()
        return {row[0] for row in rows}

    @staticmethod
    def _close_quietly(conn):
        try:
            if conn.in_transaction:
                conn.rollback()
        except sqlite3.Error:
            pass
        try:
            conn.close()
        except sqlite3.Error:
            pass


# ---------------------------------------------------------------------------
# 用例 1：artifact 键权威——单元格全部来自存储行，request_keys 仅输入校验
# ---------------------------------------------------------------------------

class ArtifactKeySpoofingTests(MutationFootprintTestBase):

    def test_artifact_key_spoofing(self):
        db = self._new_db("spoof.db", (
            P11_WORKSPACE_UNDO_DDL, P11_HIGHLIGHTS_DDL, P11_CODING_NOTES_DDL,
            P11_CONSENSUS_RECORDS_DDL, P11_TRACE_EVENTS_DDL))
        conn = self._borrow(db)

        # --- A. 六个 artifact 家族：存储行键 ≠ 请求欺骗键，格取存储行 ---
        undo_id = self._insert_undo(conn, stage="screen",
                                    zotero_key="ROW-TRUTH-1")
        fp = self._footprint(conn, "member_undo", "screen",
                             artifact_id=undo_id,
                             request_keys=("SPOOF-A", "SPOOF-B"))
        self.assertTrue(fp.complete, TAG_SPOOF)
        self.assertEqual(
            fp.cells, (AffectedCell(stage="screen", item_key="ROW-TRUTH-1"),),
            TAG_SPOOF + ": undo 取存储行键")
        self.assertFalse(
            any(cell.item_key.startswith("SPOOF") for cell in fp.cells),
            TAG_SPOOF + ": 欺骗键绝不入格")

        hl_id = self._insert_highlight(conn, zotero_key="ROW-TRUTH-2")
        for op_name in ("member_annotation", "member_annotation_note"):
            with self.subTest(op=op_name):
                fp = self._footprint(conn, op_name, "fulltext",
                                     artifact_id=hl_id,
                                     request_keys=("SPOOF-C",))
                self.assertTrue(fp.complete, TAG_SPOOF)
                self.assertEqual(
                    fp.cells,
                    (AffectedCell(stage="fulltext", item_key="ROW-TRUTH-2"),),
                    TAG_SPOOF + ": highlight 取存储行键")

        note_id = self._insert_coding_note(conn, zotero_key="ROW-TRUTH-3")
        fp = self._footprint(conn, "member_coding_note", "coding",
                             artifact_id=note_id, request_keys=("SPOOF-D",))
        self.assertTrue(fp.complete, TAG_SPOOF)
        self.assertEqual(
            fp.cells, (AffectedCell(stage="coding", item_key="ROW-TRUTH-3"),),
            TAG_SPOOF + ": coding note 取存储行键")

        cons_id = self._insert_consensus(conn, item_key="ROW-TRUTH-4")
        fp = self._footprint(conn, "coding_consensus_freeze", "coding",
                             artifact_id=cons_id, request_keys=("SPOOF-E",))
        self.assertTrue(fp.complete, TAG_SPOOF)
        self.assertEqual(
            fp.cells, (AffectedCell(stage="coding", item_key="ROW-TRUTH-4"),),
            TAG_SPOOF + ": freeze 经 consensus 记录取 item_key")

        trace_seq, trace_ev = self._insert_trace(
            conn, stage="fulltext", action="decision_changed",
            subject_id="ROW-TRUTH-5")
        for artifact in (trace_ev, trace_seq):
            with self.subTest(trace_artifact=artifact):
                fp = self._footprint(conn, "team_trace_delete", "fulltext",
                                     artifact_id=artifact,
                                     request_keys=("SPOOF-F",))
                self.assertTrue(fp.complete, TAG_SPOOF)
                self.assertEqual(
                    fp.cells,
                    (AffectedCell(stage="fulltext", item_key="ROW-TRUTH-5"),),
                    TAG_SPOOF + ": trace 取行内 subject（str/int 同）")

        import_seq, import_ev = self._insert_trace(
            conn, stage="screen", action="imported_decision_changed",
            subject_id="ROW-TRUTH-6", source="import",
            origin_store_id=P11_STORE_UUID,
            origin_event_id=P11_ORIGIN_UUIDS[0],
            origin_json=P11_ORIGIN_JSON_TEXT, origin_sha256=P11_ORIGIN_SHA)
        for artifact in (import_ev, import_seq):
            with self.subTest(import_artifact=artifact):
                fp = self._footprint(conn, "team_trace_import", "screen",
                                     artifact_id=artifact,
                                     request_keys=("SPOOF-G",))
                self.assertTrue(fp.complete, TAG_SPOOF)
                self.assertEqual(
                    fp.cells,
                    (AffectedCell(stage="screen", item_key="ROW-TRUTH-6"),),
                    TAG_SPOOF + ": import 取行内 subject（str/int 同）")

        # --- B. 请求键仍过完整输入合同（artifact 分支同样先校验）---
        for bad_keys in ((), "SPOOF-A", ("SPOOF\x00A",), ("x\ud800",),
                         (5,), (b"SPOOF",)):
            with self.subTest(bad_keys=repr(bad_keys)):
                with self.assertRaises(ValueError, msg=TAG_SPOOF):
                    self._footprint(conn, "member_undo", "screen",
                                    artifact_id=undo_id, request_keys=bad_keys)

        # --- C. 请求键族正控：normalize 保序去重后即格（无 artifact 家族）---
        fp = self._footprint(conn, "member_vote", "screen",
                             request_keys=("KEY-2", "KEY-1", "KEY-2"))
        self.assertTrue(fp.complete, TAG_SPOOF)
        self.assertEqual(
            fp.cells,
            (AffectedCell(stage="screen", item_key="KEY-2"),
             AffectedCell(stage="screen", item_key="KEY-1")),
            TAG_SPOOF + ": 请求键族保首见序去重")
        fp = self._footprint(conn, "coding_rater_submit", "coding",
                             request_keys=("KEY-9",))
        self.assertEqual(
            fp.cells, (AffectedCell(stage="coding", item_key="KEY-9"),),
            TAG_SPOOF)

        # --- D. 输入合同 ValueError 家族：先于任何存储访问（零 SQL）---
        probe_db = self._new_db("spoof_probe.db")
        probe = self._borrow(probe_db, factory=_P11ProbeConnection)
        self.assertEqual(len(probe.statements), 1,
                         TAG_SPOOF + ": 前置仅 BEGIN")
        start = len(probe.statements)
        contract_cases = (
            ("member_undo", "screen", 0),
            ("member_undo", "screen", -3),
            ("member_undo", "screen", True),
            ("member_annotation_note", "fulltext", ""),
            ("team_trace_edit", "screen", ""),
            ("team_trace_edit", "screen", "   "),
            ("team_trace_edit", "screen", "x" * 201),
            ("team_trace_edit", "history", True),
            ("member_vote", "screen", 1),
            ("member_vote_nope", "screen", None),
            ("member_vote", "control", None),
        )
        for name, stage, artifact in contract_cases:
            with self.subTest(contract=(name, stage, repr(artifact)[:14])):
                with self.assertRaises(ValueError, msg=TAG_SPOOF):
                    self._footprint(probe, name, stage, artifact_id=artifact,
                                    request_keys=("K",))
        self.assertEqual(
            probe.statements[start:], [],
            TAG_SPOOF + ": 输入合同错误零 SQL（请求键仅输入校验）")

        # --- E. event_id 200 字符上界正控（201 已在 D 拒）---
        long_ev = "e" * 200
        self._insert_trace(conn, stage="screen", action="decision_created",
                           subject_id="ROW-TRUTH-7", event_id=long_ev)
        fp = self._footprint(conn, "team_trace_edit", "screen",
                             artifact_id=long_ev, request_keys=("SPOOF-H",))
        self.assertTrue(fp.complete, TAG_SPOOF)
        self.assertEqual(
            fp.cells, (AffectedCell(stage="screen", item_key="ROW-TRUTH-7"),),
            TAG_SPOOF + ": 200 字符 event_id 上界正控")


# ---------------------------------------------------------------------------
# 用例 2：离开 catalog/旧 round 的历史键照样受保护（解析全程零域过滤）
# ---------------------------------------------------------------------------

class RemovedCatalogKeyTests(MutationFootprintTestBase):

    def test_removed_catalog_key_still_protected(self):
        # --- A. 完全无 catalog（articles 表不存在）：六家族历史键全入格 ---
        db = self._new_db("nocatalog.db", (
            P11_WORKSPACE_UNDO_DDL, P11_HIGHLIGHTS_DDL, P11_CODING_NOTES_DDL,
            P11_CONSENSUS_RECORDS_DDL, P11_TRACE_EVENTS_DDL))
        conn = self._borrow(db)
        self.assertNotIn("articles", self._table_names(conn), TAG_CATALOG)

        undo_id = self._insert_undo(conn, stage="coding",
                                    zotero_key="GONE-UNDO")
        fp = self._footprint(conn, "member_undo", "coding",
                             artifact_id=undo_id, request_keys=("REQ",))
        self.assertTrue(fp.complete, TAG_CATALOG)
        self.assertEqual(
            fp.cells, (AffectedCell(stage="coding", item_key="GONE-UNDO"),),
            TAG_CATALOG + ": 离开名单的 undo 键入格")

        hl_id = self._insert_highlight(conn, zotero_key="GONE-HL")
        fp = self._footprint(conn, "member_annotation_note", "fulltext",
                             artifact_id=hl_id, request_keys=("REQ",))
        self.assertTrue(fp.complete, TAG_CATALOG)
        self.assertEqual(
            fp.cells, (AffectedCell(stage="fulltext", item_key="GONE-HL"),),
            TAG_CATALOG)

        note_id = self._insert_coding_note(conn, zotero_key="GONE-CN")
        fp = self._footprint(conn, "member_coding_note", "coding",
                             artifact_id=note_id, request_keys=("REQ",))
        self.assertEqual(
            fp.cells, (AffectedCell(stage="coding", item_key="GONE-CN"),),
            TAG_CATALOG)

        cons_id = self._insert_consensus(conn, item_key="GONE-CONS")
        fp = self._footprint(conn, "coding_consensus_freeze", "coding",
                             artifact_id=cons_id, request_keys=("REQ",))
        self.assertEqual(
            fp.cells, (AffectedCell(stage="coding", item_key="GONE-CONS"),),
            TAG_CATALOG)

        trace_seq, trace_ev = self._insert_trace(
            conn, stage="screen", action="decision_undone",
            subject_id="GONE-TRACE")
        fp = self._footprint(conn, "team_trace_restore", "screen",
                             artifact_id=trace_ev, request_keys=("REQ",))
        self.assertTrue(fp.complete, TAG_CATALOG)
        self.assertEqual(
            fp.cells, (AffectedCell(stage="screen", item_key="GONE-TRACE"),),
            TAG_CATALOG + ": trace subject 不在 catalog 照样入格")

        imp_seq, imp_ev = self._insert_trace(
            conn, stage="fulltext", action="imported_fulltext_added",
            subject_id="GONE-IMP", source="import",
            origin_store_id=P11_STORE_UUID,
            origin_event_id=P11_ORIGIN_UUIDS[1],
            origin_json=P11_ORIGIN_JSON_TEXT, origin_sha256=P11_ORIGIN_SHA)
        fp = self._footprint(conn, "team_trace_import", "fulltext",
                             artifact_id=imp_ev, request_keys=("REQ",))
        self.assertTrue(fp.complete, TAG_CATALOG)
        self.assertEqual(
            fp.cells, (AffectedCell(stage="fulltext", item_key="GONE-IMP"),),
            TAG_CATALOG + ": import subject 不在 catalog 照样入格")

        # --- B. 有 catalog 但键已离开名单：与在名单中的键同样入格 ---
        # candidate3 夹具修：补 P11_TRACE_EVENTS_DDL（C 节在本库直插
        # OLD-ROUND-TRACE 行；candidate2 漏带 → run2 1E no such table）。
        db2 = self._new_db("stalekey.db", (P11_ARTICLES_DDL,
                                           P11_WORKSPACE_UNDO_DDL,
                                           P11_TRACE_EVENTS_DDL))
        conn2 = self._borrow(db2)
        conn2.execute(
            "INSERT INTO main.articles (zotero_key, import_order)"
            " VALUES ('IN-CATALOG-1', 1)")
        in_id = self._insert_undo(conn2, stage="screen",
                                  zotero_key="IN-CATALOG-1")
        out_id = self._insert_undo(conn2, stage="screen",
                                   zotero_key="REMOVED-FROM-CATALOG")
        fp_in = self._footprint(conn2, "member_undo", "screen",
                                artifact_id=in_id, request_keys=("REQ",))
        fp_out = self._footprint(conn2, "member_undo", "screen",
                                 artifact_id=out_id, request_keys=("REQ",))
        self.assertTrue(fp_in.complete and fp_out.complete, TAG_CATALOG)
        self.assertEqual(
            fp_out.cells,
            (AffectedCell(stage="screen", item_key="REMOVED-FROM-CATALOG"),),
            TAG_CATALOG + ": 移出名单与在名单同等保护")

        # --- C. 旧 round 任务行：footprint 不按任务轮过滤 ---
        old_id = self._insert_undo(conn2, stage="fulltext",
                                   zotero_key="OLD-ROUND-KEY",
                                   task_id="round-0-old")
        fp = self._footprint(conn2, "member_undo", "fulltext",
                             artifact_id=old_id, request_keys=("REQ",))
        self.assertTrue(fp.complete, TAG_CATALOG)
        self.assertEqual(
            fp.cells,
            (AffectedCell(stage="fulltext", item_key="OLD-ROUND-KEY"),),
            TAG_CATALOG + ": 旧 round 键照样入格")
        old_seq, old_ev = self._insert_trace(
            conn2, stage="coding", action="coding_value_changed",
            subject_id="OLD-ROUND-TRACE", task_id="round-0-old")
        fp = self._footprint(conn2, "team_trace_edit", "coding",
                             artifact_id=old_ev, request_keys=("REQ",))
        self.assertEqual(
            fp.cells,
            (AffectedCell(stage="coding", item_key="OLD-ROUND-TRACE"),),
            TAG_CATALOG)

        # --- D. 请求键族同样不过滤 + 探针证零域读取 ---
        probe_db = self._new_db("nocatalog_probe.db",
                                (P11_WORKSPACE_UNDO_DDL,))
        probe_setup = sqlite3.connect(str(probe_db))
        try:
            probe_undo_id = self._insert_undo(probe_setup, stage="coding",
                                              zotero_key="GONE-PROBE")
            probe_setup.commit()
        finally:
            probe_setup.close()
        probe = self._borrow(probe_db, factory=_P11ProbeConnection)
        start = len(probe.statements)
        fp = self._footprint(probe, "member_undo", "coding",
                             artifact_id=probe_undo_id, request_keys=("ANY",))
        self.assertTrue(fp.complete, TAG_CATALOG)
        window = probe.statements[start:]
        self.assertTrue(window, TAG_CATALOG + ": 解析窗口有语句（点读面）")
        for statement in window:
            lowered = statement.lower()
            self.assertNotIn("articles", lowered,
                             TAG_CATALOG + ": 不读 catalog 表")
            self.assertNotIn("canonical", lowered,
                             TAG_CATALOG + ": 不做 canonical 域读取")
            self.assertNotIn("decisions", lowered,
                             TAG_CATALOG + ": 不读决策域表")
        fp = self._footprint(probe, "member_vote", "screen",
                             request_keys=("NEVER-CATALOGED",))
        self.assertTrue(fp.complete, TAG_CATALOG)
        self.assertEqual(
            fp.cells,
            (AffectedCell(stage="screen", item_key="NEVER-CATALOGED"),),
            TAG_CATALOG + ": 请求键族也不过滤")


# ---------------------------------------------------------------------------
# 用例 3：unknown import origin 拒绝（含 B7 双形态与四见证校验）
# ---------------------------------------------------------------------------

class UnknownImportOriginTests(MutationFootprintTestBase):

    def test_unknown_import_origin_rejected(self):
        db = self._new_db("import.db", (P11_TRACE_EVENTS_DDL,))
        conn = self._borrow(db)

        # --- A. 正控 1：origin_store_id=规范 UUID（B7 形态一）---
        uuid_seq, uuid_ev = self._insert_trace(
            conn, stage="screen", action="imported_decision_changed",
            subject_id="IMP-OK-UUID", source="import",
            event_id="p11-imp-ok-uuid", origin_store_id=P11_STORE_UUID,
            origin_event_id=P11_ORIGIN_UUIDS[0],
            origin_json=P11_ORIGIN_JSON_TEXT, origin_sha256=P11_ORIGIN_SHA)
        fp = self._footprint(conn, "team_trace_import", "screen",
                             artifact_id=uuid_ev, request_keys=("REQ",))
        self.assertTrue(fp.complete, TAG_IMPORT + ": 规范 UUID 通过")
        self.assertEqual(
            fp.cells, (AffectedCell(stage="screen", item_key="IMP-OK-UUID"),),
            TAG_IMPORT)
        # 同一行按 sequence 整数解析 → 同格。
        fp_seq = self._footprint(conn, "team_trace_import", "screen",
                                 artifact_id=uuid_seq, request_keys=("REQ",))
        self.assertTrue(fp_seq.complete, TAG_IMPORT)
        self.assertEqual(fp_seq.cells, fp.cells, TAG_IMPORT + ": str/int 同格")

        # --- B. 正控 2：origin_store_id='Store 1'（B7 形态二，public 脱敏）---
        self._insert_trace(
            conn, stage="screen", action="imported_decision_changed",
            subject_id="IMP-OK-STORE1", source="import",
            event_id="p11-imp-ok-store1", origin_store_id="Store 1",
            origin_event_id=P11_ORIGIN_UUIDS[1],
            origin_json=P11_ORIGIN_JSON_TEXT, origin_sha256=P11_ORIGIN_SHA)
        fp = self._footprint(conn, "team_trace_import", "screen",
                             artifact_id="p11-imp-ok-store1",
                             request_keys=("REQ",))
        self.assertTrue(fp.complete, TAG_IMPORT + ": Store 1 通过")
        self.assertEqual(
            fp.cells,
            (AffectedCell(stage="screen", item_key="IMP-OK-STORE1"),),
            TAG_IMPORT)

        # --- C. 负控矩阵：单字段缺陷（其余见证齐备）→ complete=False+reason ---
        negatives = (
            ("source-manual-witnesses", {"source": "manual"}),
            ("origin-store-null", {"origin_store_id": None}),
            ("origin-event-null", {"origin_event_id": None}),
            ("origin-json-null", {"origin_json": None}),
            ("origin-sha-null", {"origin_sha256": None}),
            ("store-garbage", {"origin_store_id": "garbage"}),
            ("store-uppercase-uuid",
             {"origin_store_id": P11_STORE_UUID.upper()}),
            ("origin-event-non-uuid", {"origin_event_id": "not-a-uuid"}),
            ("sha-63-hex", {"origin_sha256": P11_ORIGIN_SHA[:63]}),
            ("sha-uppercase-hex", {"origin_sha256": P11_ORIGIN_SHA.upper()}),
            ("origin-json-broken", {"origin_json": '{"broken'}),
            ("origin-json-array", {"origin_json": "[1, 2, 3]"}),
        )
        for index, (label, overrides) in enumerate(negatives):
            with self.subTest(negative=label):
                event_id = "p11-imp-bad-%02d" % index
                params = dict(
                    stage="screen", action="imported_decision_changed",
                    subject_id="IMP-BAD", source="import", event_id=event_id,
                    origin_store_id=P11_STORE_UUID,
                    origin_event_id=P11_ORIGIN_UUIDS[2 + index],
                    origin_json=P11_ORIGIN_JSON_TEXT,
                    origin_sha256=P11_ORIGIN_SHA)
                params.update(overrides)
                self._insert_trace(conn, **params)
                fp = self._footprint(conn, "team_trace_import", "screen",
                                     artifact_id=event_id,
                                     request_keys=("REQ",))
                self.assertFalse(fp.complete,
                                 TAG_IMPORT + ": " + label + " 必拒")
                self.assertTrue(fp.reason,
                                TAG_IMPORT + ": " + label + " reason 必填")
                self.assertEqual(fp.cells, (), TAG_IMPORT)

        # --- D. B2：行不存在（str/int 两形态）→ complete=False ---
        for missing in ("p11-imp-missing-ev", 4242):
            with self.subTest(missing=missing):
                fp = self._footprint(conn, "team_trace_import", "screen",
                                     artifact_id=missing,
                                     request_keys=("REQ",))
                self.assertFalse(fp.complete, TAG_IMPORT + ": 缺行必拒")
                self.assertTrue(fp.reason, TAG_IMPORT)

        # --- E. B6 在 import 族同样生效：好行+阶段不一致拒；history 放行 ---
        fp = self._footprint(conn, "team_trace_import", "fulltext",
                             artifact_id=uuid_ev, request_keys=("REQ",))
        self.assertFalse(fp.complete,
                         TAG_IMPORT + ": 行 screen/op fulltext 不一致必拒")
        self.assertTrue(fp.reason, TAG_IMPORT)
        fp = self._footprint(conn, "team_trace_import", "history",
                             artifact_id=uuid_ev, request_keys=("REQ",))
        self.assertTrue(fp.complete, TAG_IMPORT + ": history 放行正控")
        self.assertEqual(
            fp.cells, (AffectedCell(stage="screen", item_key="IMP-OK-UUID"),),
            TAG_IMPORT)


# ---------------------------------------------------------------------------
# 用例 4：coding ≠ screening history（行内真实阶段 + B3 分类表强制）
# ---------------------------------------------------------------------------

class CodingNotScreeningTests(MutationFootprintTestBase):

    def test_coding_is_not_screening_history(self):
        db = self._new_db("coding.db", (P11_TRACE_EVENTS_DDL,
                                        P11_CODING_NOTES_DDL,
                                        P11_WORKSPACE_UNDO_DDL))
        conn = self._borrow(db)

        # --- A. B3 镜像表自检：恰 25 条目类 action。 ---
        self.assertEqual(len(P11_ITEM_ACTION_STAGES), 25, TAG_CODING)

        # --- B. 逐 action：合法阶段→完整单格；全部非法阶段→complete=False。
        #     一律经 op stage='history' 解析（对齐放行），拒因只能来自
        #     _TRACE_ITEM_ACTION_STAGES 分类，隔离钉该表强制。 ---
        for action in sorted(P11_ITEM_ACTION_STAGES):
            allowed = P11_ITEM_ACTION_STAGES[action]
            legal_stage = sorted(allowed)[0]
            with self.subTest(action=action, legal=legal_stage):
                ev_ok = "p11-code-ok-" + action
                self._insert_trace(conn, stage=legal_stage, action=action,
                                   subject_id="KEY-" + action, event_id=ev_ok)
                fp = self._footprint(conn, "team_trace_edit", "history",
                                     artifact_id=ev_ok, request_keys=("REQ",))
                self.assertTrue(fp.complete, TAG_CODING)
                self.assertEqual(
                    fp.cells,
                    (AffectedCell(stage=legal_stage,
                                  item_key="KEY-" + action),),
                    TAG_CODING + ": " + action)
            for illegal_stage in sorted(P11_RECORD_STAGES - allowed):
                with self.subTest(action=action, illegal=illegal_stage):
                    ev_bad = "p11-code-bad-%s-%s" % (action, illegal_stage)
                    self._insert_trace(conn, stage=illegal_stage,
                                       action=action,
                                       subject_id="KEY-" + action,
                                       event_id=ev_bad)
                    fp = self._footprint(conn, "team_trace_edit", "history",
                                         artifact_id=ev_bad,
                                         request_keys=("REQ",))
                    self.assertFalse(
                        fp.complete,
                        TAG_CODING + ": %s@%s 必拒" % (action, illegal_stage))
                    self.assertTrue(fp.reason, TAG_CODING)

        # --- C. 12 非条目 action：control 行经 history → 完整零格 ---
        self.assertEqual(len(P11_NON_ITEM_ACTIONS), 12, TAG_CODING)
        for action in sorted(P11_NON_ITEM_ACTIONS):
            with self.subTest(non_item=action):
                ev = "p11-nonitem-" + action
                self._insert_trace(conn, stage="control", action=action,
                                   subject_id="protocol-or-field-uuid",
                                   event_id=ev)
                fp = self._footprint(conn, "team_trace_restore", "history",
                                     artifact_id=ev, request_keys=("REQ",))
                self.assertTrue(fp.complete, TAG_CODING + ": " + action)
                self.assertEqual(fp.cells, (),
                                 TAG_CODING + ": 非条目零单元格")
        # 记录阶段的非条目 action 同样零格（阶段无关，field UUID 不猜成文献）。
        self._insert_trace(conn, stage="coding", action="baseline_field",
                           subject_id=str(uuid.uuid4()),
                           event_id="p11-nonitem-record-stage")
        fp = self._footprint(conn, "team_trace_edit", "history",
                             artifact_id="p11-nonitem-record-stage",
                             request_keys=("REQ",))
        self.assertTrue(fp.complete, TAG_CODING)
        self.assertEqual(fp.cells, (), TAG_CODING)

        # --- D. 3 刻意排除（fail-closed 正控，含 imported_ 变体与 import 路径）---
        self.assertEqual(len(P11_EXCLUDED_ACTIONS), 3, TAG_CODING)
        for action in sorted(P11_EXCLUDED_ACTIONS):
            for variant in (action, "imported_" + action):
                with self.subTest(excluded=variant):
                    ev = "p11-excl-" + variant
                    self._insert_trace(conn, stage="screen", action=variant,
                                       subject_id="KEY-PLAUSIBLE", event_id=ev)
                    fp = self._footprint(conn, "team_trace_edit", "history",
                                         artifact_id=ev,
                                         request_keys=("REQ",))
                    self.assertFalse(fp.complete,
                                     TAG_CODING + ": " + variant + " fail-closed")
                    self.assertTrue(fp.reason, TAG_CODING)
        self._insert_trace(
            conn, stage="screen", action="imported_ai_suggestion_generated",
            subject_id="KEY-PLAUSIBLE", source="import",
            event_id="p11-excl-import-path", origin_store_id=P11_STORE_UUID,
            origin_event_id=P11_ORIGIN_UUIDS[14],
            origin_json=P11_ORIGIN_JSON_TEXT, origin_sha256=P11_ORIGIN_SHA)
        fp = self._footprint(conn, "team_trace_import", "screen",
                             artifact_id="p11-excl-import-path",
                             request_keys=("REQ",))
        self.assertFalse(fp.complete, TAG_CODING + ": import 路径同样 fail-closed")
        self.assertTrue(fp.reason, TAG_CODING)

        # --- E. 编码类 artifact/行取行内真实 coding 阶段 ---
        note_id = self._insert_coding_note(conn, zotero_key="KEY-CN-1")
        fp = self._footprint(conn, "member_coding_note", "coding",
                             artifact_id=note_id, request_keys=("REQ",))
        self.assertTrue(fp.complete, TAG_CODING)
        self.assertEqual(
            fp.cells, (AffectedCell(stage="coding", item_key="KEY-CN-1"),),
            TAG_CODING + ": coding note 产 coding 格")
        coding_undo_id = self._insert_undo(conn, stage="coding",
                                           zotero_key="KEY-UNDO-1")
        fp = self._footprint(conn, "member_undo", "coding",
                             artifact_id=coding_undo_id, request_keys=("REQ",))
        self.assertEqual(
            fp.cells, (AffectedCell(stage="coding", item_key="KEY-UNDO-1"),),
            TAG_CODING + ": coding undo 行产 coding 格")
        self._insert_trace(conn, stage="coding", action="rater_record_changed",
                           subject_id="KEY-RR", event_id="p11-code-rr")
        fp = self._footprint(conn, "team_trace_edit", "coding",
                             artifact_id="p11-code-rr", request_keys=("REQ",))
        self.assertTrue(fp.complete, TAG_CODING)
        self.assertEqual(
            fp.cells, (AffectedCell(stage="coding", item_key="KEY-RR"),),
            TAG_CODING + ": rater_record_changed 属 coding")

        # --- F. 筛选完成不外推 coding 锁 ---
        self._insert_trace(conn, stage="fulltext", action="decision_undone",
                           subject_id="KEY-D", event_id="p11-code-d")
        fp = self._footprint(conn, "team_trace_edit", "history",
                             artifact_id="p11-code-d", request_keys=("REQ",))
        self.assertEqual(
            fp.cells, (AffectedCell(stage="fulltext", item_key="KEY-D"),),
            TAG_CODING + ": fulltext 决策行产 fulltext 格而非 coding")
        self._insert_trace(conn, stage="coding", action="coding_value_created",
                           subject_id="KEY-C", event_id="p11-code-c")
        fp = self._footprint(conn, "team_trace_edit", "screen",
                             artifact_id="p11-code-c", request_keys=("REQ",))
        self.assertFalse(fp.complete,
                         TAG_CODING + ": screening op 不能消费 coding 行")
        self.assertTrue(fp.reason, TAG_CODING)
        fp = self._footprint(conn, "member_undo", "screen",
                             artifact_id=coding_undo_id, request_keys=("REQ",))
        self.assertFalse(fp.complete,
                         TAG_CODING + ": undo 声明 screen 指 coding 行必拒")
        self.assertTrue(fp.reason, TAG_CODING)
        fp = self._footprint(conn, "member_vote", "screen",
                             request_keys=("SV-1", "SV-2"))
        self.assertTrue(fp.complete, TAG_CODING)
        self.assertEqual(
            fp.cells,
            (AffectedCell(stage="screen", item_key="SV-1"),
             AffectedCell(stage="screen", item_key="SV-2")),
            TAG_CODING + ": 筛选写只产筛选格")
        fp = self._footprint(conn, "member_appraisal", "coding",
                             request_keys=("AP-1",))
        self.assertEqual(
            fp.cells, (AffectedCell(stage="coding", item_key="AP-1"),),
            TAG_CODING + ": 编码族才产 coding 格")


# ---------------------------------------------------------------------------
# 用例 5：undo restore 全快照单格 + freeze 经 consensus 展开（B1/B2/B6）
# ---------------------------------------------------------------------------

class RestoreSnapshotTests(MutationFootprintTestBase):

    def test_restore_full_snapshot_cells(self):
        db = self._new_db("restore.db", (
            P11_WORKSPACE_UNDO_DDL, P11_CONSENSUS_RECORDS_DDL,
            P11_CODING_FREEZES_DDL, P11_TRACE_EVENTS_DDL))
        conn = self._borrow(db)

        # --- A. undo restore：footprint=行内 (stage, zotero_key) 单格，
        #     载荷（全快照 before/after）不影响格形状。 ---
        rich_before = json.dumps(
            {"record": None, "values": {"1": "old"}, "notes": ["n1"],
             "evidence": [{"dimension_id": 1, "sha256": P11_ORIGIN_SHA}]})
        rich_after = json.dumps(
            {"record": {"decision": "include"}, "values": {"1": "new"},
             "notes": [], "evidence": []})
        undo_rows = (
            ("screen", "RESTORE-S", rich_before, rich_after),
            ("fulltext", "RESTORE-F", '{"record": null}',
             '{"record": {"decision": "exclude"}}'),
            ("coding", "RESTORE-C", '{"values": {}}', '{"values": {"2": 1}}'),
        )
        for stage, key, before, after in undo_rows:
            with self.subTest(undo=(stage, key)):
                row_id = self._insert_undo(conn, stage=stage, zotero_key=key,
                                           before_json=before,
                                           after_json=after)
                fp = self._footprint(conn, "member_undo", stage,
                                     artifact_id=row_id, request_keys=("REQ",))
                self.assertTrue(fp.complete, TAG_RESTORE)
                self.assertEqual(
                    fp.cells, (AffectedCell(stage=stage, item_key=key),),
                    TAG_RESTORE + ": 单格=(行 stage, zotero_key)")
                self.assertEqual(len(fp.cells), 1, TAG_RESTORE)

        # --- B. 行 stage 与 operation.stage 不一致 → complete=False ---
        coding_id = self._insert_undo(conn, stage="coding",
                                      zotero_key="RESTORE-MISMATCH")
        fp = self._footprint(conn, "member_undo", "screen",
                             artifact_id=coding_id, request_keys=("REQ",))
        self.assertFalse(fp.complete,
                         TAG_RESTORE + ": 客户端错传 stage 不能绕过")
        self.assertTrue(fp.reason, TAG_RESTORE)

        # --- C. 行 stage 不是记录阶段（'history'/'weird'）→ complete=False ---
        for bad_stage in ("history", "weird"):
            with self.subTest(bad_undo_stage=bad_stage):
                row_id = self._insert_undo(conn, stage=bad_stage,
                                           zotero_key="RESTORE-BADSTAGE")
                fp = self._footprint(conn, "member_undo", "screen",
                                     artifact_id=row_id,
                                     request_keys=("REQ",))
                self.assertFalse(fp.complete, TAG_RESTORE)
                self.assertTrue(fp.reason, TAG_RESTORE)

        # --- D. 行不存在 → complete=False ---
        fp = self._footprint(conn, "member_undo", "screen",
                             artifact_id=99999, request_keys=("REQ",))
        self.assertFalse(fp.complete, TAG_RESTORE + ": undo 缺行必拒")
        self.assertTrue(fp.reason, TAG_RESTORE)

        # --- E. 表未准备 → complete=False 且不建表（零 DDL 自愈）---
        bare_db = self._new_db("restore-bare.db")
        bare_conn = self._borrow(bare_db)
        fp = self._footprint(bare_conn, "member_undo", "screen",
                             artifact_id=1, request_keys=("REQ",))
        self.assertFalse(fp.complete, TAG_RESTORE + ": journal 未准备必拒")
        self.assertTrue(fp.reason, TAG_RESTORE)
        self.assertNotIn("workspace_undo", self._table_names(bare_conn),
                         TAG_RESTORE + ": 调用后仍无表（不回 ensure）")

        # --- F. freeze：artifact_id=consensus 记录 ID，点读 item_key（B1）---
        cons_id = self._insert_consensus(conn, item_key="RESTORE-CONS-1")
        fp = self._footprint(conn, "coding_consensus_freeze", "coding",
                             artifact_id=cons_id, request_keys=("REQ",))
        self.assertTrue(fp.complete, TAG_RESTORE)
        self.assertEqual(
            fp.cells,
            (AffectedCell(stage="coding", item_key="RESTORE-CONS-1"),),
            TAG_RESTORE + ": freeze 经 consensus 记录展开 item_key")
        fp = self._footprint(conn, "coding_consensus_freeze", "coding",
                             artifact_id=424242, request_keys=("REQ",))
        self.assertFalse(fp.complete, TAG_RESTORE + ": consensus 缺记录必拒")
        self.assertTrue(fp.reason, TAG_RESTORE)
        # B1 诱饵 1：coding_freezes 有 id=3、consensus 只有 id∈{1,2} → 必拒
        # （若仍读 coding_freezes 会把 freeze 行当 artifact 放行）。
        self._insert_consensus(conn, item_key="RESTORE-CONS-2", version=2)
        for freeze_index in range(3):
            conn.execute(
                "INSERT INTO main.coding_freezes (name,version,frozen_by,"
                "created_at,records_json,content_sha256)"
                " VALUES (?,?,?,?,?,?)",
                ("p11-freeze-%d" % freeze_index, freeze_index + 1,
                 "p11-freezer", "2026-11-18T12:00:00Z", "[]", P11_ORIGIN_SHA))
        freeze_bait_id = conn.execute(
            "SELECT id FROM main.coding_freezes ORDER BY id"
        ).fetchall()[-1][0]
        self.assertGreater(freeze_bait_id, 2,
                           TAG_RESTORE + ": 诱饵前提：freeze id 超 consensus 域")
        fp = self._footprint(conn, "coding_consensus_freeze", "coding",
                             artifact_id=freeze_bait_id, request_keys=("REQ",))
        self.assertFalse(fp.complete,
                         TAG_RESTORE + ": 不读 coding_freezes（B1）")
        self.assertTrue(fp.reason, TAG_RESTORE)
        # B1 诱饵 2：仅有 coding_freezes 表（consensus 表缺位）→ 同拒。
        frozen_db = self._new_db("restore-frozen.db",
                                 (P11_CODING_FREEZES_DDL,))
        frozen_conn = self._borrow(frozen_db)
        frozen_conn.execute(
            "INSERT INTO main.coding_freezes (name,version,frozen_by,"
            "created_at,records_json,content_sha256) VALUES (?,?,?,?,?,?)",
            ("p11-only-freeze", 1, "p11-freezer", "2026-11-18T12:00:00Z",
             "[]", P11_ORIGIN_SHA))
        fp = self._footprint(frozen_conn, "coding_consensus_freeze", "coding",
                             artifact_id=1, request_keys=("REQ",))
        self.assertFalse(fp.complete,
                         TAG_RESTORE + ": 仅 coding_freezes 不满足（B1）")
        self.assertTrue(fp.reason, TAG_RESTORE)
        self.assertNotIn("coding_consensus_records",
                         self._table_names(frozen_conn), TAG_RESTORE)

        # --- G. trace restore（B2）：str/int 命中同格；缺失/空白不模糊匹配 ---
        trace_seq, trace_ev = self._insert_trace(
            conn, stage="coding", action="coding_note_created",
            subject_id="RESTORE-TRACE")
        fp_ev = self._footprint(conn, "team_trace_restore", "coding",
                                artifact_id=trace_ev, request_keys=("REQ",))
        self.assertTrue(fp_ev.complete, TAG_RESTORE)
        self.assertEqual(
            fp_ev.cells,
            (AffectedCell(stage="coding", item_key="RESTORE-TRACE"),),
            TAG_RESTORE + ": restore 单格=(行 stage, subject)")
        fp_seq = self._footprint(conn, "team_trace_restore", "coding",
                                 artifact_id=trace_seq, request_keys=("REQ",))
        self.assertTrue(fp_seq.complete, TAG_RESTORE)
        self.assertEqual(fp_seq.cells, fp_ev.cells,
                         TAG_RESTORE + ": str/int 同格")
        fp = self._footprint(conn, "team_trace_restore", "coding",
                             artifact_id="  " + trace_ev + "  ",
                             request_keys=("REQ",))
        self.assertFalse(fp.complete,
                         TAG_RESTORE + ": 空白 padding 不模糊匹配")
        self.assertTrue(fp.reason, TAG_RESTORE)
        for missing in ("p11-restore-missing", 424242):
            with self.subTest(trace_missing=missing):
                fp = self._footprint(conn, "team_trace_restore", "coding",
                                     artifact_id=missing,
                                     request_keys=("REQ",))
                self.assertFalse(fp.complete, TAG_RESTORE)
                self.assertTrue(fp.reason, TAG_RESTORE)

        # --- H. 完整性锚：本用例全部 footprint 经 _footprint 助手，
        #     assert footprint.complete or footprint.reason is not None
        #     已对每一条调用生效（含 complete=True⇒reason=None 与
        #     complete=False⇒cells=() 的成对钉）。 ---


# ---------------------------------------------------------------------------
# 支撑 1：C02 借用纪律（连接门/物化关闭/closed 透传/零副作用）
# ---------------------------------------------------------------------------

class BorrowedDisciplineTests(MutationFootprintTestBase):

    def test_borrowed_connection_contract_and_zero_side_effects(self):
        # --- A. 非 native 连接：TypeError（含 __class__ 伪装代理）---
        with self.assertRaises(TypeError, msg=TAG_BORROW):
            self._footprint(object(), "member_vote", "screen",
                            request_keys=("K",))
        bare_db = self._new_db("borrow-bare.db")
        real = sqlite3.connect(str(bare_db), timeout=15)
        self.addCleanup(real.close)
        real.row_factory = sqlite3.Row
        real.execute("BEGIN")
        proxy = _P11DelegatingConnection(real)
        # 前置自证：伪装成立（isinstance 真 / type 继承假 / Row+事务转发）。
        self.assertIsInstance(proxy, sqlite3.Connection, TAG_BORROW)
        self.assertFalse(issubclass(type(proxy), sqlite3.Connection), TAG_BORROW)
        self.assertIs(proxy.row_factory, sqlite3.Row, TAG_BORROW)
        self.assertTrue(proxy.in_transaction, TAG_BORROW)
        with self.assertRaises(TypeError, msg=TAG_BORROW):
            self._footprint(proxy, "member_vote", "screen",
                            request_keys=("K",))
        real.rollback()

        # --- B. 无活动事务 → RuntimeError（且未发任何语句）---
        idle_db = self._new_db("borrow-idle.db")
        idle = self._borrow(idle_db, factory=_P11ProbeConnection, begin=False)
        self.assertFalse(idle.in_transaction, TAG_BORROW)
        with self.assertRaises(RuntimeError, msg=TAG_BORROW):
            self._footprint(idle, "member_vote", "screen", request_keys=("K",))
        self.assertFalse(idle.in_transaction, TAG_BORROW + ": 不得偷开事务")
        self.assertEqual(idle.statements, [], TAG_BORROW + ": 未发任何语句")

        # --- C. 有事务但非 Row 工厂 → RuntimeError ---
        tuple_conn = sqlite3.connect(str(idle_db), timeout=15)
        self.addCleanup(tuple_conn.close)
        tuple_conn.execute("BEGIN IMMEDIATE")
        with self.assertRaises(RuntimeError, msg=TAG_BORROW):
            self._footprint(tuple_conn, "member_vote", "screen",
                            request_keys=("K",))
        self.assertTrue(tuple_conn.in_transaction, TAG_BORROW)
        tuple_conn.rollback()

        # --- D. 已关闭连接：sqlite3.ProgrammingError 精确透传 ---
        closed_db = self._new_db("borrow-closed.db")
        closed_conn = self._borrow(closed_db)
        closed_conn.close()
        with self.assertRaises(sqlite3.ProgrammingError, msg=TAG_BORROW) as caught:
            self._footprint(closed_conn, "member_vote", "screen",
                            request_keys=("K",))
        self.assertIs(type(caught.exception), sqlite3.ProgrammingError,
                      TAG_BORROW + ": closed 精确类型透传")

        # --- E. native 子类探针正控：纯读 + C02 全纪律 ---
        probe_db = self._new_db("borrow-probe.db", (
            P11_WORKSPACE_UNDO_DDL, P11_TRACE_EVENTS_DDL))
        probe_setup = sqlite3.connect(str(probe_db))
        try:
            probe_undo_id = self._insert_undo(probe_setup, stage="screen",
                                              zotero_key="BORROW-1")
            probe_trace_seq, probe_trace_ev = self._insert_trace(
                probe_setup, stage="coding", action="coding_value_changed",
                subject_id="BORROW-2")
            probe_setup.commit()
        finally:
            probe_setup.close()
        probe = self._borrow(probe_db, factory=_P11ProbeConnection)
        tables_before = self._table_names(probe)
        undo_count_before = probe.execute(
            "SELECT COUNT(*) FROM main.workspace_undo").fetchone()[0]
        trace_count_before = probe.execute(
            "SELECT COUNT(*) FROM main.trace_events").fetchone()[0]
        start = len(probe.statements)
        start_cursors = len(probe.cursors)
        fp_undo = self._footprint(probe, "member_undo", "screen",
                                  artifact_id=probe_undo_id,
                                  request_keys=("REQ",))
        fp_trace = self._footprint(probe, "team_trace_edit", "coding",
                                   artifact_id=probe_trace_ev,
                                   request_keys=("REQ",))
        fp_vote = self._footprint(probe, "member_vote", "screen",
                                  request_keys=("BORROW-3",))
        self.assertTrue(fp_undo.complete and fp_trace.complete
                        and fp_vote.complete, TAG_BORROW)
        window = probe.statements[start:]
        buckets = _p11_classify_statements(window)
        self.assertEqual(buckets["txn"], [], TAG_BORROW + ": 零 txn 控制")
        self.assertEqual(buckets["ddl"], [], TAG_BORROW + ": 零 DDL")
        self.assertEqual(buckets["dml"], [], TAG_BORROW + ": 零 DML")
        self.assertEqual(buckets["pragma"], [], TAG_BORROW + ": 零 PRAGMA")
        self.assertEqual(buckets["other"], [], TAG_BORROW)
        # undo/trace 各一次表存在性点读 + 一次行点读；member_vote 零语句。
        self.assertEqual(len(buckets["select"]), 4, TAG_BORROW + ": 纯 SELECT 面")
        cursors = probe.cursors[start_cursors:]
        self.assertEqual(len(cursors), 4, TAG_BORROW)
        for cursor in cursors:
            self.assertTrue(cursor.fetchall_called,
                            TAG_BORROW + ": cursor 必须 fetchall 物化")
            self.assertTrue(cursor.closed, TAG_BORROW + ": cursor 确定关闭")
        self.assertEqual(probe.commit_calls, 0, TAG_BORROW)
        self.assertEqual(probe.rollback_calls, 0, TAG_BORROW)
        self.assertEqual(probe.close_calls, 0,
                         TAG_BORROW + ": 不 close 调用方连接")
        self.assertTrue(probe.in_transaction,
                        TAG_BORROW + ": 不结束调用方事务")
        self.assertIs(probe.row_factory, sqlite3.Row,
                      TAG_BORROW + ": 不改 row_factory")
        self.assertEqual(self._table_names(probe), tables_before,
                         TAG_BORROW + ": 表集不变")
        self.assertEqual(
            probe.execute("SELECT COUNT(*) FROM main.workspace_undo")
            .fetchone()[0], undo_count_before, TAG_BORROW + ": 行数不变")
        self.assertEqual(
            probe.execute("SELECT COUNT(*) FROM main.trace_events")
            .fetchone()[0], trace_count_before, TAG_BORROW + ": 行数不变")
        self.assertEqual(probe.execute("SELECT 1").fetchone()[0], 1,
                         TAG_BORROW + ": 连接所有权未被动")


# ---------------------------------------------------------------------------
# 支撑 2：complete=False ⇒ reason 必填（两处以上构造点 + 正控对照）
# ---------------------------------------------------------------------------

class IncompleteReasonTests(MutationFootprintTestBase):

    def test_incomplete_footprint_carries_reason(self):
        db = self._new_db("reason.db", (P11_WORKSPACE_UNDO_DDL,
                                        P11_TRACE_EVENTS_DDL))
        conn = self._borrow(db)

        # 构造点 1：artifact 行缺失（resolver 的"不存在"分支）。
        fp = self._footprint(conn, "member_undo", "screen",
                             artifact_id=777, request_keys=("REQ",))
        self.assertFalse(fp.complete, TAG_REASON + ": 缺行构造点")
        self.assertIsInstance(fp.reason, str)
        self.assertTrue(fp.reason, TAG_REASON)
        self.assertEqual(fp.cells, (), TAG_REASON)

        # 构造点 2：trace subject 无法分类（未知 action fail-closed 正控）。
        seq, ev = self._insert_trace(conn, stage="screen",
                                     action="mystery_action",
                                     subject_id="KEY-M")
        fp = self._footprint(conn, "team_trace_edit", "screen",
                             artifact_id=ev, request_keys=("REQ",))
        self.assertFalse(fp.complete, TAG_REASON + ": 未知 action 构造点")
        self.assertTrue(fp.reason, TAG_REASON)
        self.assertEqual(fp.cells, (), TAG_REASON)

        # 构造点 3：存储行键坏形态——BLOB 非文本（P00 拒非 str）。
        conn.execute(
            "INSERT INTO main.workspace_undo (task_id,actor_id,stage,"
            "zotero_key,before_json,after_json) VALUES (?,?,?,?,?,?)",
            ("p11-task", "p11-actor", "screen", b"\x00\x01\x02",
             '{"record": null}', '{"record": null}'))
        blob_id = conn.execute(
            "SELECT id FROM main.workspace_undo ORDER BY id"
        ).fetchall()[-1][0]
        fp = self._footprint(conn, "member_undo", "screen",
                             artifact_id=blob_id, request_keys=("REQ",))
        self.assertFalse(fp.complete, TAG_REASON + ": BLOB 行键构造点")
        self.assertTrue(fp.reason, TAG_REASON)
        self.assertEqual(fp.cells, (), TAG_REASON)

        # 构造点 4：存储行键含 NUL / trace subject 含 NUL（P00 拒 NUL）。
        nul_id = self._insert_undo(conn, stage="screen",
                                   zotero_key="BAD\x00KEY")
        fp = self._footprint(conn, "member_undo", "screen",
                             artifact_id=nul_id, request_keys=("REQ",))
        self.assertFalse(fp.complete, TAG_REASON + ": NUL 行键构造点")
        self.assertTrue(fp.reason, TAG_REASON)
        self.assertEqual(fp.cells, (), TAG_REASON)
        seq_n, ev_n = self._insert_trace(conn, stage="fulltext",
                                         action="decision_changed",
                                         subject_id="SUB\x00J")
        fp = self._footprint(conn, "team_trace_edit", "fulltext",
                             artifact_id=ev_n, request_keys=("REQ",))
        self.assertFalse(fp.complete, TAG_REASON + ": NUL subject 构造点")
        self.assertTrue(fp.reason, TAG_REASON)
        self.assertEqual(fp.cells, (), TAG_REASON)

        # 正控对照：同形好键完整（确保上述拒绝源于键形态而非夹具形态）。
        good_id = self._insert_undo(conn, stage="screen",
                                    zotero_key="GOOD-KEY")
        fp = self._footprint(conn, "member_undo", "screen",
                             artifact_id=good_id, request_keys=("REQ",))
        self.assertTrue(fp.complete, TAG_REASON + ": 正控对照完整")
        self.assertEqual(
            fp.cells, (AffectedCell(stage="screen", item_key="GOOD-KEY"),),
            TAG_REASON)
        seq_g, ev_g = self._insert_trace(conn, stage="fulltext",
                                         action="decision_changed",
                                         subject_id="GOOD-SUBJECT")
        fp = self._footprint(conn, "team_trace_edit", "fulltext",
                             artifact_id=ev_g, request_keys=("REQ",))
        self.assertTrue(fp.complete, TAG_REASON)
        self.assertEqual(
            fp.cells,
            (AffectedCell(stage="fulltext", item_key="GOOD-SUBJECT"),),
            TAG_REASON)


# ---------------------------------------------------------------------------
# 支撑 3：B5 history 阶段域（trace 族接受 / 其余拒绝）
# ---------------------------------------------------------------------------

class HistoryStageDomainTests(MutationFootprintTestBase):

    def test_history_stage_domain_boundaries(self):
        db = self._new_db("history.db", (P11_TRACE_EVENTS_DDL,))
        conn = self._borrow(db)

        # --- A. trace 三名（edit/delete/restore）×三记录阶段：history 全放行 ---
        manual_rows = {}
        for stage_name, action, subject in (
                ("screen", "decision_created", "HIST-S"),
                ("fulltext", "fulltext_added", "HIST-F"),
                ("coding", "coding_value_created", "HIST-C")):
            seq, ev = self._insert_trace(conn, stage=stage_name,
                                         action=action, subject_id=subject)
            manual_rows[stage_name] = (ev, subject)
        for op_name in ("team_trace_edit", "team_trace_delete",
                        "team_trace_restore"):
            for stage_name in ("screen", "fulltext", "coding"):
                with self.subTest(op=op_name, row_stage=stage_name):
                    ev, subject = manual_rows[stage_name]
                    fp = self._footprint(conn, op_name, "history",
                                         artifact_id=ev,
                                         request_keys=("REQ",))
                    self.assertTrue(fp.complete, TAG_HISTORY)
                    self.assertEqual(
                        fp.cells,
                        (AffectedCell(stage=stage_name, item_key=subject),),
                        TAG_HISTORY + ": history 放行任意合法行阶段")

        # --- B. import 族 history 同样放行（三条完整见证行）---
        for index, (stage_name, action, subject) in enumerate((
                ("screen", "imported_decision_created", "HIST-IMP-S"),
                ("fulltext", "imported_fulltext_added", "HIST-IMP-F"),
                ("coding", "imported_coding_value_created", "HIST-IMP-C"))):
            with self.subTest(import_history=stage_name):
                seq, ev = self._insert_trace(
                    conn, stage=stage_name, action=action, subject_id=subject,
                    source="import", origin_store_id=P11_STORE_UUID,
                    origin_event_id=P11_ORIGIN_UUIDS[4 + index],
                    origin_json=P11_ORIGIN_JSON_TEXT,
                    origin_sha256=P11_ORIGIN_SHA)
                fp = self._footprint(conn, "team_trace_import", "history",
                                     artifact_id=ev, request_keys=("REQ",))
                self.assertTrue(fp.complete, TAG_HISTORY)
                self.assertEqual(
                    fp.cells,
                    (AffectedCell(stage=stage_name, item_key=subject),),
                    TAG_HISTORY)

        # --- C. history 不豁免行阶段合法性：未知行阶段仍拒 ---
        seq, bad_ev = self._insert_trace(conn, stage="bogus",
                                         action="decision_created",
                                         subject_id="HIST-B")
        fp = self._footprint(conn, "team_trace_edit", "history",
                             artifact_id=bad_ev, request_keys=("REQ",))
        self.assertFalse(fp.complete,
                         TAG_HISTORY + ": history 不放行非法行阶段")
        self.assertTrue(fp.reason, TAG_HISTORY)

        # --- D. control 行：history 完整零格；具体阶段 op 指控制行必拒 ---
        seq, ctl_ev = self._insert_trace(conn, stage="control",
                                         action="recording_stopped",
                                         subject_id="protocol")
        fp = self._footprint(conn, "team_trace_edit", "history",
                             artifact_id=ctl_ev, request_keys=("REQ",))
        self.assertTrue(fp.complete, TAG_HISTORY)
        self.assertEqual(fp.cells, (), TAG_HISTORY + ": control 经 history 零格")
        fp = self._footprint(conn, "team_trace_edit", "screen",
                             artifact_id=ctl_ev, request_keys=("REQ",))
        self.assertFalse(fp.complete,
                         TAG_HISTORY + ": 具体阶段 op 不能指向控制行")
        self.assertTrue(fp.reason, TAG_HISTORY)

        # --- E. 非 trace 族全拒 'history'（十个已落地/占位名逐一）---
        for name in ("member_vote", "member_undo", "member_coding",
                     "member_appraisal", "member_annotation",
                     "member_annotation_note", "member_coding_note",
                     "coding_rater_submit", "coding_consensus_append",
                     "coding_consensus_freeze"):
            with self.subTest(non_trace=name):
                with self.assertRaises(ValueError, msg=TAG_HISTORY):
                    self._footprint(conn, name, "history",
                                    request_keys=("K",))

        # --- F. stage 域负控：control/stage1/stage2/空串全 ValueError ---
        for bad_stage in ("control", "stage1", "stage2", ""):
            with self.subTest(bad_stage=bad_stage):
                with self.assertRaises(ValueError, msg=TAG_HISTORY):
                    self._footprint(conn, "member_vote", bad_stage,
                                    request_keys=("K",))

        # --- G. expected_version：拒负数与 bool；收 0 与正数 ---
        for bad_version in (-1, True):
            with self.subTest(bad_version=bad_version):
                with self.assertRaises(ValueError, msg=TAG_HISTORY):
                    self._footprint(conn, "member_vote", "screen",
                                    expected_version=bad_version,
                                    request_keys=("K",))
        fp = self._footprint(conn, "member_vote", "screen",
                             expected_version=0, request_keys=("K",))
        self.assertTrue(fp.complete, TAG_HISTORY + ": version=0 合法边界")
        fp = self._footprint(conn, "member_vote", "screen",
                             expected_version=7, request_keys=("K",))
        self.assertTrue(fp.complete, TAG_HISTORY)


if __name__ == "__main__":
    unittest.main()
