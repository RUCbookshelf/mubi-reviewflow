# -*- coding: utf-8 -*-
"""P07 请求回执与归档（team flow receipts）独立测试候选 —— 静态撰写，作者未运行。

目标产品候选（本文件唯一针对的实现）：
- E:/rf216-scratch/p07-receipts-20261118T123005Z-6382/candidate2/team_flow_receipts.py
- SHA-256 19853a6afe521cd941553064ad9872c289e8c0620d635220b606f9d5c915a462
  （1221 行；已过产品复审 PASS-with-notes + N1 单点差量 PASS；三条触发器：
  no_update / no_delete / INSERT 守卫）
- 主仓基线不含 P07（未整合）：本文件在测试箱内对候选副本
  （箱根/candidate/team_flow_receipts.py，SHA 已核一致）运行；若对主仓
  build/tests 运行将因 candidate/ 缺位而诚实红（不以 ImportError 冒充行为
  红证；集成时由协调者迁移加载路径）。

合同依据（全部只读引用，本文不编辑任何合同/产品文件）：
- 2026-11-16-2.1.6-team-contracts.md C02（键/连接/错误共同合同）与 C09
  （请求回执与归档）全节；P07 包工单钉定本文件名与 5 个具名用例。
- 复审已定口径（写测试必须遵守，本文件逐条落实）：
  1. receipt_id 的 preimage = 四 scope 字段（task_instance_id/actor_user_id/
     operation/request_id）的命名字段对象 canonical JSON。preimage 形状属
     未冻结项：mirror_receipt_id 按“钉现状”实现并在此声明。
  2. 返回形状 = 扁平 13 键（payload 11 + receipt_id + payload_sha256）；
     archive/read 均不含 origin/archived_at（存储层保留，但不断言 API 可见）。
  3. 幂等语义 = 完整 canonical payload 相等（含 recorded_at）：上层重试必须
     先读 scoped receipt 并复用首次 recorded_at；“重试”夹具即按此构造。
  4. candidate1 建的旧库不可复用（schema_mismatch）：所有夹具一律用
     candidate2 prepare 新建；“旧式布局”夹具用候选自身冻结 DDL 去掉
     INSERT 守卫后手工构造（candidate1 时代形态的忠实模拟，不触碰任何
     candidate1 遗留库文件）。
  5. 时间戳正则接受 Unicode 数字（未冻结项）：本文件只做 ASCII 正控，
     不做 Unicode 拒绝断言。
  6. 嵌套 JSON 大整数合法：int64 边界只适用 SQLite INTEGER 列
     （actor_user_id/source_generation），result/internal_evidence 内 2**64
     合法并有正控。

用例 → 合同条款映射（5 具名用例，工单钉死名称）：
- ReceiptRetryTests.test_retry_preserves_first_result
    C09 幂等：同 scope 同完整 canonical payload 再 append 返回原记录
    （recorded_at/result/hash 原样、时间不重生成）；read_receipt_on 读回
    同一记录；两 SHA（receipt_id/payload_sha256）可按 canonical 规则独立
    复算（mirror 实现见下）；trace 面无第二条 INSERT。
- ReceiptConflictTests.test_request_hash_conflict
    C09 冲突：payload 任何差异（含 request_sha256、recorded_at、origin
    源绑定翻转）→ TeamFlowReceiptConflict 且不写第二行；裸 INSERT 撞同
    scope / 伪 receipt_id → IntegrityError（守卫触发器 RAISE(ABORT) 路径，
    即产品撞键重读分支的入口）；同连接重读：同内容幂等返回原行、异内容
    Conflict；no_update/no_delete 触发器拦物理 UPDATE/DELETE。
- ScopeDomainTests.test_cross_actor_and_task_scope
    C09 四字段 scoped UNIQUE：任一 scope 字段不同独立成行、receipt_id
    互异且各等于四字段镜像哈希；read 仅四字段精确命中（单字段错位 None）；
    actor_user_id 拒 bool/越 int64/float（1 与 int64 上界为合法边界对）；
    operation/request_id（及 read 路径 task_instance_id）拒 NUL/空串/坏
    Unicode；嵌套 JSON 大整数正控。
- ArchiveProjectionTests.test_archive_and_no_secret_projection
    C09 归档：仅 actor 原产可归档、collab 拒；保原 payload 字节/ID/hash/
    recorded_at（源目标 raw payload_json 逐字节相等）；幂等重归档保首次
    archived_at；批内重复等长按序返回；批内冲突与目标冲突均 Conflict 且
    零覆写。internal_evidence 低层可见属合同内（HTTP 裁剪归 P15+）：本
    用例断言“归档不新增/不裁剪任何字段、不引入 peer 票/path 等额外泄露
    面”（返回形状=扁平 13 键冻结集），不断言 internal_evidence 不存在。
- StorageFaultRollbackTests.test_storage_fault_and_caller_rollback
    C02 借用纪律：零 txn/PRAGMA/close（探针语句面+计数）、Cursor 完整
    物化并确定关闭（探针 cursor 面）、主错优先清理错挂 cleanup_errors /
    无主错时清理错原样上抛（调用方侧真实 native 子类故障注入，零 mock）；
    typed 错误族 connection_contract/not_prepared/schema_mismatch/
    unreadable/corrupt 各至少一路真实构造；closed 连接 ProgrammingError
    精确透传；调用方 rollback 后行消失；prepare mode=rw 不建文件、目录
    路径 unreadable、空文件可建布局且幂等复核；INSERT OR REPLACE 被守卫
    拦（正控：守卫触发前 candidate 行完好、字节未动）。

诚实记录（不可达分支，不伪造测试）：
- prepare “COMMIT 后清理失败 .committed=True”与非空 prepare cleanup_errors
  （ROLLBACK/close 失败）：prepare 自管连接由模块内部 sqlite3.connect 创建，
  黑盒无注入口；唯一确定性触发手段是 patch 模块内 sqlite3.connect（F1 v1
  先例 test_s2_terminal_facts.py 即用 unittest.mock），但本包工单钉死零
  mock，故不覆盖，仅覆盖可达邻接面：prepare 失败携带 .cleanup_errors==()
  且不携带 .committed。该缺口留待运行方/后续包决定是否以注入口覆盖。
- append 公开路径的 “同内容不同 origin” Conflict 消息分支：origin 由 payload
  源绑定成对字段推导，canonical payload 相等 ⟹ origin 相等，该分支公开
  不可达（防御性代码）；“不同内容” Conflict 分支已覆盖 origin 翻转变体。
- archive “批内同一 receipt_id 落不同 scope” Conflict 分支：_archive_candidate
  要求 receipt_id 与 scope 推导值一致，不合法记录先 PayloadInvalid，该
  分支对合法输入不可达；批内同 scope 不同内容与目标冲突两分支已覆盖。
- 四字段 UNIQUE 自动索引为守卫触发器前的第二道防线：同 scope 裸 INSERT
  先命中守卫（WHEN EXISTS 含四字段等值分支），UNIQUE 索引单独行为不可
  区分观测，不单独伪造（sqlite_master 布局校验已由 schema_mismatch 族
  用例覆盖其存在性）。
- Unicode 数字时间戳：正则 \\d 记法接受非 ASCII 数字（未冻结项），按口径只做
  ASCII 正控，不做拒绝断言。

撰写协议（同 P04/F1 先例）：本文件每个 def/class 符号（含 dunder）在写入前均
逐项运行 GitNexus CLI upstream impact（--repo E:/mubi-reviewflow --file
build/tests/test_216_team_receipts.py --kind Class/Method/Function
--include-tests）；新文件预期 not found/UNKNOWN，raw/rc/MANIFEST 存档于
E:/rf216-scratch/p07-receipt-tests-20261118T163829Z-9993/raw/。candidate1 共
50 项（raw/SYMBOLS.tsv）；candidate2 增量补 _P07ProbeCursor/_P07FaultCursor
两个 description property（delta raw 51/52，全量 52 项=
raw/SYMBOLS-candidate2.tsv）；candidate3 定因 SQLite trace 事件≠执行（语句
带触发器时一次 INSERT 执行上报多个同文本 SQLITE_TRACE_STMT 事件——触发器
体/RAISE 子程序各加报，复现证据箱 E:/rf216-scratch/p07-rc-analysis-20261119/
），观测仪器改 total_changes 快照判等 + _p07_collapse_adjacent 折叠相邻同
文本事件（delta raw 53，全量 53 项=raw/SYMBOLS-candidate3.tsv），每次 CLI
前经 ps 门确认无其他 gitnexus 作业。

未运行声明：作者仅静态撰写（ast.parse/compile 与符号清单自检）；candidate5
起按主控处方增加夹具静态 SQL 常量的内存 SQLite 语法预检（scripts/
sql_precheck.py：:memory: + 产品冻结 DDL 经 AST 抽取构建 schema，不触产品
API/不跑用例/不查真实库），除此之外未运行任何测试/SQL/产品/API；执行留给
独立 runner 在应用产品候选后进行。
禁主仓/前端/真实 data/旧证据箱写入；夹具全部位于测试自有 TemporaryDirectory。
自包含：不依赖 conftest/Safety 夹具、不触 HTTP 层；静态消费者仅 unittest。
"""

from __future__ import annotations

import gc
import hashlib
import importlib.util
import json
import re
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path

_P07_BOX_ROOT = Path(__file__).resolve().parents[2]
_P07_CANDIDATE = _P07_BOX_ROOT / "candidate" / "team_flow_receipts.py"
_P07_SPEC = importlib.util.spec_from_file_location(
    "team_flow_receipts", _P07_CANDIDATE)
tfr = importlib.util.module_from_spec(_P07_SPEC)
_P07_SPEC.loader.exec_module(tfr)

#: 冻结候选 SHA-256（运行时打印实测值供核对，不做硬断言：候选后续修订经
#: 复审后 SHA 自然变化，硬断言会制造假红）。
P07_CANDIDATE_SHA = (
    "19853a6afe521cd941553064ad9872c289e8c0620d635220b606f9d5c915a462")

P07_T1 = "2026-11-18T09:15:30.123Z"
P07_T2 = "2026-11-18T09:16:45.456Z"

#: C09 v1 payload 精确 11 键与记录扁平 13 键（测试侧冻结常量，不取自模块，
#: 以独立钉住合同形状）。
P07_PAYLOAD_SCHEMA = "team_flow_receipt.v1"
P07_PAYLOAD_KEYS = (
    "schema", "task_instance_id", "actor_user_id", "operation", "request_id",
    "request_sha256", "result", "internal_evidence", "recorded_at",
    "source_registration_id", "source_generation",
)
P07_EXPECTED_RECORD_KEYS = frozenset(P07_PAYLOAD_KEYS) | frozenset(
    {"receipt_id", "payload_sha256"})

#: ASCII 正控时间戳正则（口径：Unicode 数字接受性未冻结，不做拒绝断言）。
P07_ASCII_TIMESTAMP_RE = re.compile(
    r"\A\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z\Z")

TAG_RETRY = "P07-retry idempotent append"
TAG_CONFLICT = "P07-request-hash conflict"
TAG_SCOPE = "P07-cross actor/task scope"
TAG_ARCHIVE = "P07-archive projection"
TAG_FAULT = "P07-storage fault rollback"

_P07_TXN_KEYWORDS = frozenset(
    {"BEGIN", "COMMIT", "ROLLBACK", "SAVEPOINT", "RELEASE", "END"})
_P07_DDL_KEYWORDS = frozenset(
    {"CREATE", "DROP", "ALTER", "VACUUM", "REINDEX", "ANALYZE"})
_P07_DML_KEYWORDS = frozenset(
    {"INSERT", "UPDATE", "DELETE", "REPLACE", "MERGE"})

_P07_PROVENANCE_PRINTED = False


# ---------------------------------------------------------------------------
# canonical JSON / 哈希镜像（独立重实现，非任意冻结字面量；断言候选输出 ==
# 镜像输出，即与冻结算法等价）。receipt_id preimage 形状未冻结，钉现状：
# 四 scope 字段命名字段对象的 canonical JSON。
# ---------------------------------------------------------------------------

def mirror_canonical_json(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False)


def mirror_sha256_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def mirror_receipt_id(task_instance_id, actor_user_id, operation, request_id):
    return mirror_sha256_text(mirror_canonical_json({
        "task_instance_id": task_instance_id,
        "actor_user_id": actor_user_id,
        "operation": operation,
        "request_id": request_id,
    }))


def mirror_payload_sha256(payload):
    return mirror_sha256_text(mirror_canonical_json(payload))


def fixture_receipt_payload(*, task_instance_id="task-1", actor_user_id=101,
                            operation="terminal.submit",
                            request_id="req-001", request_sha256="b" * 64,
                            result=None, internal_evidence=None,
                            recorded_at=P07_T1, source_registration_id=None,
                            source_generation=None):
    """构造一条 C09 v1 receipt payload（collab 原产为默认：源绑定二者 None）。"""
    return {
        "schema": P07_PAYLOAD_SCHEMA,
        "task_instance_id": task_instance_id,
        "actor_user_id": actor_user_id,
        "operation": operation,
        "request_id": request_id,
        "request_sha256": request_sha256,
        "result": {} if result is None else result,
        "internal_evidence": {} if internal_evidence is None
                             else internal_evidence,
        "recorded_at": recorded_at,
        "source_registration_id": source_registration_id,
        "source_generation": source_generation,
    }


def _p07_first_keyword(sql_text):
    """剥前导 ``--`` 注释行后取首关键字/记号（先例 _kv_first_keyword）。"""
    text = sql_text.strip()
    while text.startswith("--"):
        remainder = text.split("\n", 1)
        text = remainder[1].strip() if len(remainder) > 1 else ""
    if not text:
        return ""
    return re.split(r"[\s(]", text, 1)[0].upper()


def _p07_classify_statements(statements):
    """按首真实关键字分类语句（txm/DDL/DML/PRAGMA/SELECT/其他 六桶）。"""
    buckets = {
        "select": [], "dml": [], "ddl": [], "txn": [], "pragma": [], "other": [],
    }
    for statement in statements:
        keyword = _p07_first_keyword(statement)
        if keyword in _P07_DML_KEYWORDS:
            buckets["dml"].append(statement)
        elif keyword in _P07_DDL_KEYWORDS:
            buckets["ddl"].append(statement)
        elif keyword in _P07_TXN_KEYWORDS:
            buckets["txn"].append(statement)
        elif keyword == "PRAGMA":
            buckets["pragma"].append(statement)
        elif keyword in ("SELECT", "WITH"):
            buckets["select"].append(statement)
        else:
            buckets["other"].append(statement)
    return buckets


def _p07_collapse_adjacent(statements):
    """折叠相邻同文本语句事件（防误用注释）。

    SQLite trace 特性：语句上挂触发器时，一次执行会上报多个同文本
    SQLITE_TRACE_STMT 事件（触发器体/RAISE 子程序各加报）——trace 计数是
    事件数不是执行数；同一次执行的多事件文本相同且相邻，折叠后即按执行次
    语义（不影响 INSERT OR REPLACE 等首关键字检测）。真实写入行数必须以
    ``conn.total_changes`` 增量判等（守卫 RAISE 的 INSERT 不计入 changes），
    不得数 trace 事件。
    """
    collapsed = []
    for statement in statements:
        if collapsed and collapsed[-1] == statement:
            continue
        collapsed.append(statement)
    return collapsed


# ---------------------------------------------------------------------------
# 观测/故障探针（调用方自建连接，真实 native 子类，零 mock；C02 的真实 type
# 继承判断接受 _P07ProbeConnection/_P07FaultConnection——它们是真 sqlite3.
# Connection 子类；_P07DelegatingConnection 是 __class__ 伪装反例）。
# ---------------------------------------------------------------------------

class _P07ProbeCursor:
    """Cursor 代理（仅观测）：登记 execute SQL/参数与 fetch/close 行为。"""

    def __init__(self, cursor, registry=None):
        self._cursor = cursor
        self.sql = ""
        self.parameters = ()
        self._registry = registry
        self.rows_returned = 0
        self.fetchone_called = False
        self.fetchall_called = False
        self.saw_empty = False
        self.has_description = False
        self.closed = False
        if registry is not None:
            registry.append(self)

    @property
    def description(self):
        """透传被包裹游标的 description（产品 _execute_write 直读；修订点）。"""
        return self._cursor.description

    def execute(self, sql, parameters=()):
        self._cursor.execute(sql, parameters)
        self.sql = sql
        if isinstance(parameters, dict):
            self.parameters = parameters
        else:
            self.parameters = tuple(parameters)
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


class _P07ProbeConnection(sqlite3.Connection):
    """Native 子类探针：语句 trace（原始事件面：带触发器语句一次执行可上报
    多个同文本事件，见 _p07_collapse_adjacent）+ commit/rollback/close 计数
    + cursor 登记 + changes_at_reset 快照（total_changes 增量判等用）。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.statements = []
        self.cursors = []
        self.statement_offset = 0
        self.cursor_offset = 0
        self.commit_calls = 0
        self.rollback_calls = 0
        self.close_calls = 0
        self.changes_at_reset = self.total_changes
        self._p07_closed = False
        self.set_trace_callback(self.statements.append)

    def execute(self, sql, parameters=()):
        raw = super().execute(sql, parameters)
        cursor = _P07ProbeCursor(raw, self.cursors)
        cursor.has_description = raw.description is not None
        cursor.sql = sql
        if isinstance(parameters, dict):
            cursor.parameters = parameters
        else:
            cursor.parameters = tuple(parameters)
        return cursor

    def commit(self):
        self.commit_calls += 1
        return super().commit()

    def rollback(self):
        self.rollback_calls += 1
        return super().rollback()

    def close(self):
        if self._p07_closed:
            return
        super().close()
        self._p07_closed = True
        self.close_calls += 1


class _P07DelegatingConnection:
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


class _P07FaultCursor:
    """调用方侧真实 cursor 包装（故障注入）：按类开关注入真实异常。

    零 mock：包装真 sqlite3.Cursor，抛真实异常对象；仅调用方自建连接生效。
    """

    def __init__(self, cursor):
        self._cursor = cursor

    @property
    def description(self):
        """透传被包裹游标的 description（与探针面对齐；产品写路径直读）。"""
        return self._cursor.description

    def fetchone(self):
        if _P07FaultConnection.fail_fetch:
            raise sqlite3.OperationalError("injected fetch failure")
        return self._cursor.fetchone()

    def fetchall(self):
        if _P07FaultConnection.fail_fetch:
            raise sqlite3.OperationalError("injected fetch failure")
        return self._cursor.fetchall()

    def close(self):
        if _P07FaultConnection.fail_close:
            raise RuntimeError("injected cursor close failure")
        self._cursor.close()


class _P07FaultConnection(sqlite3.Connection):
    """Native 子类故障源：execute 返回 _P07FaultCursor（类级开关）。"""

    fail_fetch = False
    fail_close = False

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def execute(self, sql, parameters=()):
        return _P07FaultCursor(super().execute(sql, parameters))


# ---------------------------------------------------------------------------
# 共用基座：临时箱 + candidate2 prepare 新建夹具 + 借用连接工厂 + raw 行读取
# ---------------------------------------------------------------------------

class TeamReceiptsTestBase(unittest.TestCase):
    """公共基座：所有库文件一律由候选 prepare 新建（口径：不Reuse candidate1）。"""

    maxDiff = None

    def setUp(self):
        global _P07_PROVENANCE_PRINTED
        self._tmp = tempfile.TemporaryDirectory(prefix="rf216_p07_")
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        if not _P07_PROVENANCE_PRINTED:
            _P07_PROVENANCE_PRINTED = True
            print("P07_TEST_FILE_SHA",
                  hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
            print("P07_CANDIDATE_SOURCE_SHA",
                  hashlib.sha256(_P07_CANDIDATE.read_bytes()).hexdigest())
            print("P07_CANDIDATE_PINNED_SHA", P07_CANDIDATE_SHA)
            print("P07_PYTHON", sys.version.split()[0])
            print("P07_SQLITE_VERSION", sqlite3.sqlite_version)

    def _prepared_db(self, name):
        """空文件 → 候选 prepare（candidate2 唯一夹具来源）。"""
        path = self.base / name
        touch = sqlite3.connect(str(path))
        touch.close()
        tfr.prepare_team_flow_receipts(path)
        return path

    def _open(self, path, *, factory=None, begin=True):
        """C02 借用形态：Row 工厂 + 调用方活动事务（可选探针/故障子类）。"""
        if factory is None:
            conn = sqlite3.connect(str(path), timeout=15)
        else:
            conn = sqlite3.connect(str(path), timeout=15, factory=factory)
        self.addCleanup(TeamReceiptsTestBase._close_quietly, conn)
        conn.row_factory = sqlite3.Row
        if begin:
            conn.execute("BEGIN IMMEDIATE")
        return conn

    def _reset_probe(self, probe):
        """偏移复位：仅观察此后的语句/cursor/计数。"""
        probe.statement_offset = len(probe.statements)
        probe.cursor_offset = len(probe.cursors)
        probe.commit_calls = 0
        probe.rollback_calls = 0
        probe.close_calls = 0
        probe.changes_at_reset = probe.total_changes

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

    def _stored_columns(self, conn, receipt_id):
        """raw 读存储行 9 列（dict）；缺行返回 None。"""
        row = conn.execute(
            "SELECT receipt_id, task_instance_id, actor_user_id, operation,"
            " request_id, payload_json, payload_sha256, origin, archived_at"
            " FROM main.team_flow_receipts WHERE receipt_id=?",
            (receipt_id,)).fetchone()
        return None if row is None else dict(row)

    def _table_count(self, conn):
        return conn.execute(
            "SELECT COUNT(*) FROM main.team_flow_receipts").fetchone()[0]


# ---------------------------------------------------------------------------
# 用例 1：幂等重试保首次结果
# ---------------------------------------------------------------------------

class ReceiptRetryTests(TeamReceiptsTestBase):

    def test_retry_preserves_first_result(self):
        db = self._prepared_db("retry.db")
        conn = self._open(db, factory=_P07ProbeConnection)
        payload = fixture_receipt_payload(
            result={"accepted": True, "tally": 3},
            internal_evidence={"seq": 1, "note": "私有-注记"},
        )
        self._reset_probe(conn)
        first = tfr.append_receipt_on(conn, payload)
        # 重试（口径：复用首次 recorded_at 的完整 canonical payload）与
        # 键序重排变体（canonical 排序 ⟹ 同 payload）均须命中同一原记录。
        second = tfr.append_receipt_on(conn, payload)
        reordered = fixture_receipt_payload(
            result={"tally": 3, "accepted": True},
            internal_evidence={"note": "私有-注记", "seq": 1},
        )
        third = tfr.append_receipt_on(conn, reordered)
        read_back = tfr.read_receipt_on(
            conn, task_instance_id="task-1", actor_user_id=101,
            operation="terminal.submit", request_id="req-001")
        self.assertEqual(set(first), P07_EXPECTED_RECORD_KEYS, TAG_RETRY)
        self.assertEqual(first["schema"], P07_PAYLOAD_SCHEMA, TAG_RETRY)
        self.assertEqual(second, first, TAG_RETRY + ": 重试返回原记录")
        self.assertEqual(third, first, TAG_RETRY + ": 键序无关幂等")
        self.assertEqual(read_back, first, TAG_RETRY + ": 读回同一记录")
        self.assertEqual(first["recorded_at"], P07_T1,
                         TAG_RETRY + ": recorded_at 不重生成")
        self.assertEqual(first["result"], payload["result"],
                         TAG_RETRY + ": 原结果不重算")
        self.assertEqual(self._table_count(conn), 1, TAG_RETRY)
        # trace 面 + changes 面：两次重试 + 读回不新增真实 INSERT 执行。
        # SQLite trace 特性：带触发器的 INSERT 一次执行上报多个同文本
        # SQLITE_TRACE_STMT 事件，事件数≠执行数（见 _p07_collapse_adjacent
        # 注释）；真实写入行数以 total_changes 增量判等（守卫 RAISE 的
        # INSERT 不计入 changes），故此处不数 trace 事件。
        window = conn.statements[conn.statement_offset:]
        inserts = [s for s in window if _p07_first_keyword(s) == "INSERT"]
        self.assertTrue(inserts, TAG_RETRY)
        self.assertIn("team_flow_receipts", inserts[0], TAG_RETRY)
        self.assertEqual(conn.total_changes - conn.changes_at_reset, 1,
                         TAG_RETRY + ": 真实 INSERT 执行恰 1 次（无第二条）")
        # 两 SHA 按 canonical 规则独立复算（preimage 形状未冻结，钉现状）。
        expected_id = mirror_receipt_id(
            "task-1", 101, "terminal.submit", "req-001")
        expected_hash = mirror_payload_sha256(payload)
        self.assertEqual(first["receipt_id"], expected_id, TAG_RETRY)
        self.assertEqual(first["payload_sha256"], expected_hash, TAG_RETRY)
        # 镜像区分度负控：内容/scope 变化必改变两 SHA（镜像非空转）。
        self.assertNotEqual(
            mirror_receipt_id("task-1", 101, "terminal.submit", "req-002"),
            expected_id, TAG_RETRY)
        self.assertNotEqual(
            mirror_payload_sha256(
                fixture_receipt_payload(request_sha256="c" * 64)),
            expected_hash, TAG_RETRY)
        conn.rollback()


# ---------------------------------------------------------------------------
# 用例 2：payload 任何差异 → Conflict；守卫触发器路径与 append-only 物理面
# ---------------------------------------------------------------------------

class ReceiptConflictTests(TeamReceiptsTestBase):

    def test_request_hash_conflict(self):
        db = self._prepared_db("conflict.db")
        conn = self._open(db)
        base = fixture_receipt_payload(
            result={"accepted": True}, internal_evidence={"seq": 1})
        original = tfr.append_receipt_on(conn, base)
        stored_original = self._stored_columns(conn, original["receipt_id"])
        # 正控：完全相同 canonical payload → 幂等返回原行。
        self.assertEqual(tfr.append_receipt_on(conn, base), original,
                         TAG_CONFLICT + ": 同内容幂等正控")
        variants = (
            ("request_sha256", fixture_receipt_payload(
                request_sha256="c" * 64, result={"accepted": True},
                internal_evidence={"seq": 1})),
            ("result", fixture_receipt_payload(
                result={"accepted": False}, internal_evidence={"seq": 1})),
            ("internal_evidence", fixture_receipt_payload(
                result={"accepted": True}, internal_evidence={"seq": 2})),
            ("recorded_at", fixture_receipt_payload(
                recorded_at=P07_T2, result={"accepted": True},
                internal_evidence={"seq": 1})),
            ("origin-flip", fixture_receipt_payload(
                result={"accepted": True}, internal_evidence={"seq": 1},
                source_registration_id="registration-7",
                source_generation=7)),
        )
        for label, variant in variants:
            with self.subTest(variant=label):
                with self.assertRaises(tfr.TeamFlowReceiptConflict, msg=TAG_CONFLICT):
                    tfr.append_receipt_on(conn, variant)
                self.assertEqual(
                    self._table_count(conn), 1,
                    TAG_CONFLICT + ": 不写第二行 " + label)
                self.assertEqual(
                    self._stored_columns(conn, original["receipt_id"]),
                    stored_original,
                    TAG_CONFLICT + ": 原行字节不动 " + label)
        # 裸 INSERT 撞同 scope（同 receipt_id 同内容）→ IntegrityError，
        # 即产品撞键重读分支的守卫触发器入口（RAISE(ABORT) 透出形态）。
        with self.assertRaises(sqlite3.IntegrityError,
                               msg=TAG_CONFLICT) as caught_insert:
            conn.execute(
                "INSERT INTO main.team_flow_receipts (receipt_id,"
                " task_instance_id, actor_user_id, operation, request_id,"
                " payload_json, payload_sha256, origin, archived_at)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (stored_original["receipt_id"], "task-1", 101,
                 "terminal.submit", "req-001", stored_original["payload_json"],
                 stored_original["payload_sha256"], "collab", None))
        self.assertIn("append-only", str(caught_insert.exception),
                      TAG_CONFLICT + ": 守卫触发器消息")
        # 裸 INSERT 撞同 scope 但伪 receipt_id/异内容 → 守卫 scope 分支同拦。
        with self.assertRaises(sqlite3.IntegrityError,
                               msg=TAG_CONFLICT) as caught_scope:
            conn.execute(
                "INSERT INTO main.team_flow_receipts (receipt_id,"
                " task_instance_id, actor_user_id, operation, request_id,"
                " payload_json, payload_sha256, origin, archived_at)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                ("e" * 64, "task-1", 101, "terminal.submit", "req-001",
                 '{"x":1}', "0" * 64, "collab", None))
        self.assertIn("append-only", str(caught_scope.exception), TAG_CONFLICT)
        self.assertEqual(self._table_count(conn), 1,
                         TAG_CONFLICT + ": 守卫后行数仍 1")
        # 同连接重读：同内容幂等返回原行；异内容 Conflict。
        self.assertEqual(tfr.append_receipt_on(conn, base), original,
                         TAG_CONFLICT + ": 撞键后同内容幂等")
        with self.assertRaises(tfr.TeamFlowReceiptConflict, msg=TAG_CONFLICT):
            tfr.append_receipt_on(conn, variants[0][1])
        self.assertEqual(self._table_count(conn), 1, TAG_CONFLICT)
        # no_update / no_delete：物理 UPDATE/DELETE 一律 RAISE(ABORT)。
        with self.assertRaises(sqlite3.IntegrityError,
                               msg=TAG_CONFLICT) as caught_update:
            conn.execute(
                "UPDATE main.team_flow_receipts SET payload_json='{}'"
                " WHERE receipt_id=?", (original["receipt_id"],))
        self.assertIn("append-only", str(caught_update.exception), TAG_CONFLICT)
        with self.assertRaises(sqlite3.IntegrityError,
                               msg=TAG_CONFLICT) as caught_delete:
            conn.execute(
                "DELETE FROM main.team_flow_receipts WHERE receipt_id=?",
                (original["receipt_id"],))
        self.assertIn("append-only", str(caught_delete.exception), TAG_CONFLICT)
        self.assertEqual(self._table_count(conn), 1, TAG_CONFLICT)
        self.assertEqual(
            self._stored_columns(conn, original["receipt_id"]), stored_original,
            TAG_CONFLICT + ": 触发器拦截后原行完好")
        self.assertEqual(
            tfr.read_receipt_on(conn, task_instance_id="task-1",
                                actor_user_id=101,
                                operation="terminal.submit",
                                request_id="req-001"),
            original, TAG_CONFLICT + ": 末态读回原记录")


# ---------------------------------------------------------------------------
# 用例 3：跨 actor/task scope 独立成行 + 字段域校验
# ---------------------------------------------------------------------------

class ScopeDomainTests(TeamReceiptsTestBase):

    def test_cross_actor_and_task_scope(self):
        db = self._prepared_db("scope.db")
        conn = self._open(db)
        scopes = (
            ("task-1", 101, "terminal.submit", "req-001"),
            ("task-2", 101, "terminal.submit", "req-001"),
            ("task-1", 202, "terminal.submit", "req-001"),
            ("task-1", 101, "terminal.amend", "req-001"),
            ("task-1", 101, "terminal.submit", "req-002"),
        )
        records = {}
        for task, actor, op, req in scopes:
            record = tfr.append_receipt_on(
                conn, fixture_receipt_payload(task_instance_id=task,
                                              actor_user_id=actor,
                                              operation=op, request_id=req))
            records[(task, actor, op, req)] = record
        self.assertEqual(self._table_count(conn), len(scopes),
                         TAG_SCOPE + ": 任一 scope 字段不同独立成行")
        ids = [record["receipt_id"] for record in records.values()]
        self.assertEqual(len(set(ids)), len(scopes),
                         TAG_SCOPE + ": receipt_id 互异")
        for (task, actor, op, req), record in records.items():
            self.assertEqual(
                record["receipt_id"], mirror_receipt_id(task, actor, op, req),
                TAG_SCOPE + ": 四字段镜像哈希 " + req)
            self.assertEqual(
                tfr.read_receipt_on(conn, task_instance_id=task,
                                    actor_user_id=actor, operation=op,
                                    request_id=req),
                record, TAG_SCOPE + ": 四字段精确命中 " + req)
        # read 仅四字段精确命中：单字段错位 → None。
        self.assertIsNone(
            tfr.read_receipt_on(conn, task_instance_id="task-X",
                                actor_user_id=101,
                                operation="terminal.submit",
                                request_id="req-001"), TAG_SCOPE)
        self.assertIsNone(
            tfr.read_receipt_on(conn, task_instance_id="task-1",
                                actor_user_id=303,
                                operation="terminal.submit",
                                request_id="req-001"), TAG_SCOPE)
        self.assertIsNone(
            tfr.read_receipt_on(conn, task_instance_id="task-1",
                                actor_user_id=101,
                                operation="terminal.submit",
                                request_id="req-XXX"), TAG_SCOPE)
        self.assertIsNone(
            tfr.read_receipt_on(conn, task_instance_id="task-1",
                                actor_user_id=101, operation="terminal.other",
                                request_id="req-001"), TAG_SCOPE)
        # actor_user_id：拒 bool / 0 / 负 / 越 int64 上界 / float。
        for bad_actor in (True, False, 0, -1, 9223372036854775808, 1.0):
            with self.subTest(bad_actor=bad_actor):
                with self.assertRaises(tfr.TeamFlowReceiptPayloadInvalid,
                                       msg=TAG_SCOPE):
                    tfr.append_receipt_on(
                        conn, fixture_receipt_payload(actor_user_id=bad_actor,
                                                      request_id="req-bad"))
                with self.assertRaises(tfr.TeamFlowReceiptPayloadInvalid,
                                       msg=TAG_SCOPE):
                    tfr.read_receipt_on(
                        conn, task_instance_id="task-1",
                        actor_user_id=bad_actor, operation="terminal.submit",
                        request_id="req-001")
        # 合法边界对：1 与 int64 上界均独立成行。
        low = tfr.append_receipt_on(
            conn, fixture_receipt_payload(actor_user_id=1,
                                          request_id="req-boundary-lo"))
        high = tfr.append_receipt_on(
            conn, fixture_receipt_payload(
                actor_user_id=9223372036854775807,
                request_id="req-boundary-hi"))
        self.assertEqual(self._table_count(conn), len(scopes) + 2, TAG_SCOPE)
        self.assertNotEqual(low["receipt_id"], high["receipt_id"], TAG_SCOPE)
        # operation/request_id：拒空串 / NUL / 坏 Unicode（lone surrogate）。
        for field in ("operation", "request_id"):
            for bad_text in ("", "\x00x", "x\ud800"):
                with self.subTest(field=field, bad=repr(bad_text)):
                    with self.assertRaises(tfr.TeamFlowReceiptPayloadInvalid,
                                           msg=TAG_SCOPE):
                        tfr.append_receipt_on(
                            conn, fixture_receipt_payload(**{field: bad_text}))
        # read 路径同规则（含 task_instance_id）。
        with self.assertRaises(tfr.TeamFlowReceiptPayloadInvalid,
                               msg=TAG_SCOPE):
            tfr.read_receipt_on(conn, task_instance_id="task-1",
                                actor_user_id=101, operation="",
                                request_id="req-001")
        with self.assertRaises(tfr.TeamFlowReceiptPayloadInvalid,
                               msg=TAG_SCOPE):
            tfr.read_receipt_on(conn, task_instance_id="task-1",
                                actor_user_id=101, operation="terminal.submit",
                                request_id="\x00")
        with self.assertRaises(tfr.TeamFlowReceiptPayloadInvalid,
                               msg=TAG_SCOPE):
            tfr.read_receipt_on(conn, task_instance_id="\ud800",
                                actor_user_id=101, operation="terminal.submit",
                                request_id="req-001")
        self.assertEqual(self._table_count(conn), len(scopes) + 2,
                         TAG_SCOPE + ": 拒绝不落行")
        # 嵌套 JSON 大整数合法（int64 边界只限 SQLite INTEGER 列）。
        big = fixture_receipt_payload(
            request_id="req-bigint",
            result={"tally": {"votes": 2 ** 64, "neg": -(2 ** 64)}},
            internal_evidence={"trace": [2 ** 63, -(2 ** 63)]})
        big_record = tfr.append_receipt_on(conn, big)
        self.assertEqual(big_record["result"], big["result"], TAG_SCOPE)
        self.assertEqual(
            tfr.read_receipt_on(conn, task_instance_id="task-1",
                                actor_user_id=101, operation="terminal.submit",
                                request_id="req-bigint"),
            big_record, TAG_SCOPE + ": 大整数往返")


# ---------------------------------------------------------------------------
# 用例 4：归档与无秘密投影（13 键冻结形状）
# ---------------------------------------------------------------------------

class ArchiveProjectionTests(TeamReceiptsTestBase):

    def test_archive_and_no_secret_projection(self):
        src = self._prepared_db("archive_src.db")
        tgt = self._prepared_db("archive_tgt.db")
        src_conn = self._open(src)
        actor_payload = fixture_receipt_payload(
            request_id="req-actor",
            result={"accepted": True, "items": ["a", "b"]},
            internal_evidence={"server_note": "私有-可见", "seq": 7,
                               "big": 2 ** 64},
            source_registration_id="registration-7", source_generation=7)
        actor_record = tfr.append_receipt_on(src_conn, actor_payload)
        collab_record = tfr.append_receipt_on(
            src_conn, fixture_receipt_payload(request_id="req-collab",
                                              result={"ok": 1}))
        src_stored = self._stored_columns(src_conn, actor_record["receipt_id"])
        # 源绑定成对约束与 source_generation 边界（append 路径）。
        bad_pairs = (
            fixture_receipt_payload(request_id="req-pair1",
                                    source_registration_id="registration-9",
                                    source_generation=None),
            fixture_receipt_payload(request_id="req-pair2",
                                    source_registration_id=None,
                                    source_generation=9),
            fixture_receipt_payload(request_id="req-pair3",
                                    source_registration_id="registration-9",
                                    source_generation=True),
            fixture_receipt_payload(request_id="req-pair4",
                                    source_registration_id="registration-9",
                                    source_generation=0),
            fixture_receipt_payload(request_id="req-pair5",
                                    source_registration_id="registration-9",
                                    source_generation=9223372036854775808),
        )
        for bad_payload in bad_pairs:
            with self.subTest(pairing=bad_payload["request_id"]):
                with self.assertRaises(tfr.TeamFlowReceiptPayloadInvalid,
                                       msg=TAG_ARCHIVE):
                    tfr.append_receipt_on(src_conn, bad_payload)
        self.assertEqual(self._table_count(src_conn), 2, TAG_ARCHIVE)
        # 归档正控：actor 原产 → 目标库字节保真拷贝（时间戳为 ASCII 正控）。
        tgt_conn = self._open(tgt)
        archived = tfr.archive_receipts_on(tgt_conn, [actor_record])
        self.assertEqual(len(archived), 1, TAG_ARCHIVE)
        archived_record = archived[0]
        self.assertEqual(archived_record, actor_record,
                         TAG_ARCHIVE + ": 归档返回=原记录")
        self.assertEqual(set(archived_record), P07_EXPECTED_RECORD_KEYS,
                         TAG_ARCHIVE + ": 扁平 13 键冻结集")
        self.assertNotIn("origin", archived_record, TAG_ARCHIVE)
        self.assertNotIn("archived_at", archived_record, TAG_ARCHIVE)
        self.assertEqual(archived_record["internal_evidence"],
                         actor_payload["internal_evidence"],
                         TAG_ARCHIVE + ": internal_evidence 原样（低层合同内）")
        tgt_stored = self._stored_columns(tgt_conn, actor_record["receipt_id"])
        self.assertEqual(tgt_stored["origin"], "actor_archive", TAG_ARCHIVE)
        self.assertEqual(tgt_stored["payload_json"],
                         src_stored["payload_json"],
                         TAG_ARCHIVE + ": 保原 payload 字节")
        self.assertEqual(tgt_stored["payload_sha256"],
                         src_stored["payload_sha256"],
                         TAG_ARCHIVE + ": 保原 payload_sha256")
        self.assertEqual(tgt_stored["receipt_id"], src_stored["receipt_id"],
                         TAG_ARCHIVE + ": 保原 receipt_id")
        self.assertIsNotNone(tgt_stored["archived_at"], TAG_ARCHIVE)
        self.assertRegex(tgt_stored["archived_at"], P07_ASCII_TIMESTAMP_RE,
                         TAG_ARCHIVE + ": archived_at ASCII 正控")
        self.assertEqual(
            tfr.read_receipt_on(tgt_conn, task_instance_id="task-1",
                                actor_user_id=101,
                                operation="terminal.submit",
                                request_id="req-actor"),
            actor_record, TAG_ARCHIVE + ": 归档目标读回仍 13 键投影")
        # 幂等重归档：保首次 archived_at（时间充分分离，重生成必可观测）。
        time.sleep(0.02)
        re_archived = tfr.archive_receipts_on(tgt_conn, [actor_record])
        self.assertEqual(re_archived, [actor_record],
                         TAG_ARCHIVE + ": 重归档幂等返回")
        self.assertEqual(
            self._stored_columns(tgt_conn, actor_record["receipt_id"]),
            tgt_stored, TAG_ARCHIVE + ": 首次 archived_at 保留")
        self.assertEqual(self._table_count(tgt_conn), 1, TAG_ARCHIVE)
        # 批内同 receipt 重复：等长按序返回，仅一行。
        tgt2 = self._prepared_db("archive_tgt2.db")
        tgt2_conn = self._open(tgt2)
        duplicated = tfr.archive_receipts_on(tgt2_conn,
                                             [actor_record, actor_record])
        self.assertEqual(duplicated, [actor_record, actor_record],
                         TAG_ARCHIVE + ": 等长按序")
        self.assertEqual(self._table_count(tgt2_conn), 1, TAG_ARCHIVE)
        # collab 原产拒归档（负控）。
        with self.assertRaises(tfr.TeamFlowReceiptPayloadInvalid,
                               msg=TAG_ARCHIVE) as caught_collab:
            tfr.archive_receipts_on(tgt2_conn, [collab_record])
        self.assertIn("only actor-origin", str(caught_collab.exception),
                      TAG_ARCHIVE)
        # 输入侧形状负控：额外字段（peer 票等）不得进入归档面。
        with self.assertRaises(tfr.TeamFlowReceiptPayloadInvalid,
                               msg=TAG_ARCHIVE) as caught_extra:
            tfr.archive_receipts_on(tgt2_conn,
                                    [dict(actor_record, peer_votes=[])])
        self.assertIn("fields mismatch", str(caught_extra.exception),
                      TAG_ARCHIVE)
        self.assertEqual(self._table_count(tgt2_conn), 1, TAG_ARCHIVE)
        # 批内同 scope 不同内容 → Conflict，整批零写入。
        mutant = dict(actor_record)
        mutant["result"] = {"accepted": False}
        mutant["payload_sha256"] = mirror_payload_sha256(
            {key: mutant[key] for key in P07_PAYLOAD_KEYS})
        tgt3 = self._prepared_db("archive_tgt3.db")
        tgt3_conn = self._open(tgt3)
        with self.assertRaises(tfr.TeamFlowReceiptConflict,
                               msg=TAG_ARCHIVE) as caught_batch:
            tfr.archive_receipts_on(tgt3_conn, [actor_record, mutant])
        self.assertIn("conflicting copies", str(caught_batch.exception),
                      TAG_ARCHIVE)
        self.assertEqual(self._table_count(tgt3_conn), 0,
                         TAG_ARCHIVE + ": 批内冲突零写入")
        # 目标已有同 scope 不同内容 → Conflict，已归档行零覆写。
        with self.assertRaises(tfr.TeamFlowReceiptConflict,
                               msg=TAG_ARCHIVE) as caught_target:
            tfr.archive_receipts_on(tgt_conn, [mutant])
        self.assertIn("already holds different content",
                      str(caught_target.exception), TAG_ARCHIVE)
        self.assertEqual(
            self._stored_columns(tgt_conn, actor_record["receipt_id"]),
            tgt_stored, TAG_ARCHIVE + ": 目标冲突不覆写")
        # 目标即源（actor 原产行占位）→ 同样 Conflict。
        with self.assertRaises(tfr.TeamFlowReceiptConflict, msg=TAG_ARCHIVE):
            tfr.archive_receipts_on(src_conn, [actor_record])
        self.assertEqual(self._table_count(src_conn), 2, TAG_ARCHIVE)


# ---------------------------------------------------------------------------
# 用例 5：存储故障族、借用纪律与调用方回滚
# ---------------------------------------------------------------------------

class StorageFaultRollbackTests(TeamReceiptsTestBase):

    def test_storage_fault_and_caller_rollback(self):
        # --- A. 借用纪律：零 txn/PRAGMA/close、Cursor 物化关闭、计数不变 ---
        src = self._prepared_db("fault_borrowed.db")
        tgt = self._prepared_db("fault_borrowed_tgt.db")
        conn = self._open(src, factory=_P07ProbeConnection)
        self._reset_probe(conn)
        actor_record = tfr.append_receipt_on(
            conn, fixture_receipt_payload(
                request_id="req-fault", source_registration_id="registration-7",
                source_generation=7))
        collab_record = tfr.append_receipt_on(
            conn, fixture_receipt_payload(request_id="req-fault-c"))
        self.assertEqual(
            tfr.read_receipt_on(conn, task_instance_id="task-1",
                                actor_user_id=101,
                                operation="terminal.submit",
                                request_id="req-fault"),
            actor_record, TAG_FAULT)
        tgt_conn = self._open(tgt, factory=_P07ProbeConnection)
        self._reset_probe(tgt_conn)
        tfr.archive_receipts_on(tgt_conn, [actor_record])
        for probe, expected_rows in ((conn, 2), (tgt_conn, 1)):
            # SQLite trace 特性：事件数≠执行数（带触发器的 INSERT 一次执行
            # 上报多个同文本事件，见 _p07_collapse_adjacent 注释）——语句面
            # 在折叠相邻同文本后断言（折叠后即执行次语义，不影响
            # INSERT OR REPLACE 检测），写入行数以 total_changes 增量判等。
            buckets = _p07_classify_statements(_p07_collapse_adjacent(
                probe.statements[probe.statement_offset:]))
            self.assertEqual(buckets["txn"], [], TAG_FAULT + ": 零 txn 控制")
            self.assertEqual(buckets["pragma"], [], TAG_FAULT + ": 零 PRAGMA")
            self.assertEqual(buckets["ddl"], [], TAG_FAULT + ": 零 DDL")
            self.assertEqual(buckets["other"], [], TAG_FAULT)
            self.assertEqual(probe.total_changes - probe.changes_at_reset,
                             expected_rows,
                             TAG_FAULT + ": 真实 INSERT 行数增量判等")
            for statement in buckets["dml"]:
                self.assertIn("INSERT INTO main.team_flow_receipts",
                              statement, TAG_FAULT)
            self.assertEqual(probe.commit_calls, 0, TAG_FAULT)
            self.assertEqual(probe.rollback_calls, 0, TAG_FAULT)
            self.assertEqual(probe.close_calls, 0,
                             TAG_FAULT + ": 不 close 调用方连接")
            self.assertTrue(probe.in_transaction,
                            TAG_FAULT + ": 不结束调用方事务")
            self.assertIs(probe.row_factory, sqlite3.Row,
                          TAG_FAULT + ": 不改 row_factory")
            cursors = probe.cursors[probe.cursor_offset:]
            self.assertTrue(cursors, TAG_FAULT + ": 观测窗口非空")
            for cursor in cursors:
                self.assertTrue(
                    cursor.fetchall_called or cursor.fetchone_called
                    or cursor.saw_empty or not cursor.has_description,
                    TAG_FAULT + ": cursor 必须完整物化（有结果集必取尽）")
                self.assertTrue(cursor.closed,
                                TAG_FAULT + ": cursor 确定关闭")
            self.assertEqual(probe.execute("SELECT 1").fetchone()[0], 1,
                             TAG_FAULT + ": 连接所有权未被动")
        conn.rollback()
        tgt_conn.rollback()

        # --- B1. connection_contract：非 native / 伪装 / 无事务 / 非 Row ---
        cc = self._prepared_db("fault_cc.db")
        with self.assertRaises(tfr.TeamFlowReceiptUnavailable,
                               msg=TAG_FAULT) as caught_obj_r:
            tfr.read_receipt_on(object(), task_instance_id="t",
                                actor_user_id=1, operation="o",
                                request_id="r")
        self.assertEqual(caught_obj_r.exception.reason,
                         "connection_contract", TAG_FAULT)
        with self.assertRaises(tfr.TeamFlowReceiptUnavailable,
                               msg=TAG_FAULT) as caught_obj_a:
            tfr.append_receipt_on(object(), fixture_receipt_payload())
        self.assertEqual(caught_obj_a.exception.reason,
                         "connection_contract", TAG_FAULT)
        with self.assertRaises(tfr.TeamFlowReceiptUnavailable,
                               msg=TAG_FAULT) as caught_obj_z:
            tfr.archive_receipts_on(object(), [])
        self.assertEqual(caught_obj_z.exception.reason,
                         "connection_contract", TAG_FAULT)
        real = sqlite3.connect(str(cc), timeout=15)
        self.addCleanup(real.close)
        real.row_factory = sqlite3.Row
        real.execute("BEGIN")
        proxy = _P07DelegatingConnection(real)
        # 前置自证：伪装成立（isinstance 真 / type 继承假 / Row+事务转发）。
        self.assertIsInstance(proxy, sqlite3.Connection, TAG_FAULT)
        self.assertFalse(issubclass(type(proxy), sqlite3.Connection),
                         TAG_FAULT)
        self.assertIs(proxy.row_factory, sqlite3.Row, TAG_FAULT)
        self.assertTrue(proxy.in_transaction, TAG_FAULT)
        with self.assertRaises(tfr.TeamFlowReceiptUnavailable,
                               msg=TAG_FAULT) as caught_proxy:
            tfr.append_receipt_on(proxy, fixture_receipt_payload())
        self.assertEqual(caught_proxy.exception.reason,
                         "connection_contract", TAG_FAULT)
        real.rollback()
        inactive = self._open(cc, factory=_P07ProbeConnection, begin=False)
        self.assertFalse(inactive.in_transaction, TAG_FAULT)
        with self.assertRaises(tfr.TeamFlowReceiptUnavailable,
                               msg=TAG_FAULT) as caught_inactive:
            tfr.append_receipt_on(inactive, fixture_receipt_payload())
        self.assertEqual(caught_inactive.exception.reason,
                         "connection_contract", TAG_FAULT)
        self.assertFalse(inactive.in_transaction,
                         TAG_FAULT + ": 不得偷开事务")
        self.assertEqual(len(inactive.statements), 0,
                         TAG_FAULT + ": 未发任何语句")
        tuple_conn = sqlite3.connect(str(cc), timeout=15)
        self.addCleanup(tuple_conn.close)
        tuple_conn.execute("BEGIN IMMEDIATE")
        with self.assertRaises(tfr.TeamFlowReceiptUnavailable,
                               msg=TAG_FAULT) as caught_tuple:
            tfr.read_receipt_on(tuple_conn, task_instance_id="task-1",
                                actor_user_id=101, operation="terminal.submit",
                                request_id="req-fault")
        self.assertEqual(caught_tuple.exception.reason,
                         "connection_contract", TAG_FAULT)
        self.assertTrue(tuple_conn.in_transaction, TAG_FAULT)
        tuple_conn.rollback()
        # 成对正控：native 子类探针（Row+活动事务）三 API 全部成功。
        ok_probe = self._open(cc, factory=_P07ProbeConnection)
        ok_record = tfr.append_receipt_on(ok_probe, fixture_receipt_payload(
            request_id="req-cc-ok"))
        self.assertEqual(tfr.read_receipt_on(
            ok_probe, task_instance_id="task-1", actor_user_id=101,
            operation="terminal.submit", request_id="req-cc-ok"), ok_record,
            TAG_FAULT)
        ok_tgt = self._prepared_db("fault_cc_tgt.db")
        ok_tgt_conn = self._open(ok_tgt, factory=_P07ProbeConnection)
        self.assertEqual(
            tfr.archive_receipts_on(ok_tgt_conn, []),
            [], TAG_FAULT + ": 空批正控")
        # 空批后真实归档正控（actor 记录）。
        archived_ok = tfr.archive_receipts_on(ok_tgt_conn, [actor_record])
        self.assertEqual(archived_ok, [actor_record], TAG_FAULT)
        ok_probe.rollback()
        ok_tgt_conn.rollback()

        # --- B2. not_prepared：存在但未 prepare 的空文件 ---
        empty = self.base / "fault_empty.db"
        touch = sqlite3.connect(str(empty))
        touch.close()
        empty_conn = self._open(empty)
        with self.assertRaises(tfr.TeamFlowReceiptUnavailable,
                               msg=TAG_FAULT) as caught_np_r:
            tfr.read_receipt_on(empty_conn, task_instance_id="t",
                                actor_user_id=1, operation="o",
                                request_id="r")
        self.assertEqual(caught_np_r.exception.reason, "not_prepared",
                         TAG_FAULT)
        with self.assertRaises(tfr.TeamFlowReceiptUnavailable,
                               msg=TAG_FAULT) as caught_np_a:
            tfr.append_receipt_on(empty_conn, fixture_receipt_payload())
        self.assertEqual(caught_np_a.exception.reason, "not_prepared",
                         TAG_FAULT)

        # --- B3. schema_mismatch：旧式布局（缺 INSERT 守卫）/ 额外索引 /
        #     INSERT 抑制触发器（候选 DDL 自建，不触碰 candidate1 遗留库）---
        old_style = self.base / "fault_old_style.db"
        old_touch = sqlite3.connect(str(old_style))
        old_touch.close()
        old_raw = sqlite3.connect(str(old_style))
        try:
            old_raw.execute(tfr.TABLE_DDL)
            old_raw.execute(tfr.TRIGGER_NO_UPDATE_DDL)
            old_raw.execute(tfr.TRIGGER_NO_DELETE_DDL)
            old_raw.execute(tfr.META_DDL)
            old_raw.execute(
                "INSERT INTO main.team_flow_receipts_meta"
                " (singleton, schema_version) VALUES (1, ?)",
                (tfr.SCHEMA_VERSION,))
            old_raw.commit()
        finally:
            old_raw.close()
        old_conn = self._open(old_style)
        with self.assertRaises(tfr.TeamFlowReceiptUnavailable,
                               msg=TAG_FAULT) as caught_old_a:
            tfr.append_receipt_on(old_conn, fixture_receipt_payload())
        self.assertEqual(caught_old_a.exception.reason, "schema_mismatch",
                         TAG_FAULT + ": 缺守卫的旧式布局拒绝")
        with self.assertRaises(tfr.TeamFlowReceiptUnavailable,
                               msg=TAG_FAULT) as caught_old_r:
            tfr.read_receipt_on(old_conn, task_instance_id="t",
                                actor_user_id=1, operation="o",
                                request_id="r")
        self.assertEqual(caught_old_r.exception.reason, "schema_mismatch",
                         TAG_FAULT)
        old_conn.rollback()
        with self.assertRaises(tfr.TeamFlowReceiptUnavailable,
                               msg=TAG_FAULT) as caught_old_p:
            tfr.prepare_team_flow_receipts(old_style)
        self.assertEqual(caught_old_p.exception.reason, "schema_mismatch",
                         TAG_FAULT + ": 半装不修补")
        extra = self._prepared_db("fault_extra_index.db")
        extra_raw = sqlite3.connect(str(extra))
        try:
            extra_raw.execute(
                "CREATE INDEX p07_extra_idx ON team_flow_receipts"
                " (operation)")
            extra_raw.commit()
        finally:
            extra_raw.close()
        extra_conn = self._open(extra)
        with self.assertRaises(tfr.TeamFlowReceiptUnavailable,
                               msg=TAG_FAULT) as caught_extra_a:
            tfr.append_receipt_on(extra_conn, fixture_receipt_payload())
        self.assertEqual(caught_extra_a.exception.reason, "schema_mismatch",
                         TAG_FAULT + ": 额外索引拒绝")
        supp = self._prepared_db("fault_suppress.db")
        supp_raw = sqlite3.connect(str(supp))
        try:
            supp_raw.execute(
                "CREATE TRIGGER p07_extra_suppress BEFORE INSERT ON"
                " team_flow_receipts BEGIN SELECT RAISE(IGNORE); END")
            supp_raw.commit()
        finally:
            supp_raw.close()
        supp_conn = self._open(supp)
        with self.assertRaises(tfr.TeamFlowReceiptUnavailable,
                               msg=TAG_FAULT) as caught_supp:
            tfr.append_receipt_on(supp_conn, fixture_receipt_payload())
        self.assertEqual(caught_supp.exception.reason, "schema_mismatch",
                         TAG_FAULT + ": INSERT 抑制器=拒而非假成功")
        self.assertEqual(self._table_count(supp_conn), 0, TAG_FAULT)

        # --- B4. unreadable：存储行含非法 UTF-8 TEXT（真实读失败）---
        utf8_db = self._prepared_db("fault_utf8.db")
        utf8_raw = sqlite3.connect(str(utf8_db))
        try:
            utf8_raw.execute(
                "INSERT INTO main.team_flow_receipts (receipt_id,"
                " task_instance_id, actor_user_id, operation, request_id,"
                " payload_json, payload_sha256, origin, archived_at)"
                " VALUES (?, ?, ?, ?, ?, CAST(X'80' AS TEXT), ?, 'collab',"
                " NULL)",
                ("a" * 64, "task-utf8", 101, "op.utf8", "req-utf8",
                 "0" * 64))
            utf8_raw.commit()
        finally:
            utf8_raw.close()
        utf8_conn = self._open(utf8_db)
        with self.assertRaises(tfr.TeamFlowReceiptUnavailable,
                               msg=TAG_FAULT) as caught_utf8:
            tfr.read_receipt_on(utf8_conn, task_instance_id="task-utf8",
                                actor_user_id=101, operation="op.utf8",
                                request_id="req-utf8")
        self.assertEqual(caught_utf8.exception.reason, "unreadable", TAG_FAULT)
        self.assertIsInstance(caught_utf8.exception.__cause__, sqlite3.Error,
                              TAG_FAULT + ": 原生失败为因")

        # --- B5. corrupt：坏哈希行 / origin 失配行 / 非库文件 prepare ---
        corrupt_db = self._prepared_db("fault_corrupt.db")
        bad_hash_payload = fixture_receipt_payload(
            task_instance_id="task-corrupt", operation="op.corrupt",
            request_id="req-corrupt1", result={"x": 1})
        bad_hash_json = mirror_canonical_json(bad_hash_payload)
        wrong_origin_payload = fixture_receipt_payload(
            task_instance_id="task-corrupt", operation="op.corrupt",
            request_id="req-corrupt2", source_registration_id="registration-7",
            source_generation=7)
        corrupt_raw = sqlite3.connect(str(corrupt_db))
        try:
            corrupt_raw.execute(
                "INSERT INTO main.team_flow_receipts (receipt_id,"
                " task_instance_id, actor_user_id, operation, request_id,"
                " payload_json, payload_sha256, origin, archived_at)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (mirror_receipt_id("task-corrupt", 101, "op.corrupt",
                                   "req-corrupt1"),
                 "task-corrupt", 101, "op.corrupt", "req-corrupt1",
                 bad_hash_json, "0" * 64, "collab", None))
            corrupt_raw.execute(
                "INSERT INTO main.team_flow_receipts (receipt_id,"
                " task_instance_id, actor_user_id, operation, request_id,"
                " payload_json, payload_sha256, origin, archived_at)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (mirror_receipt_id("task-corrupt", 101, "op.corrupt",
                                   "req-corrupt2"),
                 "task-corrupt", 101, "op.corrupt", "req-corrupt2",
                 mirror_canonical_json(wrong_origin_payload),
                 mirror_payload_sha256(wrong_origin_payload), "collab", None))
            corrupt_raw.commit()
        finally:
            corrupt_raw.close()
        corrupt_conn = self._open(corrupt_db)
        with self.assertRaises(tfr.TeamFlowReceiptUnavailable,
                               msg=TAG_FAULT) as caught_bad_hash:
            tfr.read_receipt_on(corrupt_conn,
                                task_instance_id="task-corrupt",
                                actor_user_id=101, operation="op.corrupt",
                                request_id="req-corrupt1")
        self.assertEqual(caught_bad_hash.exception.reason, "corrupt",
                         TAG_FAULT + ": 存储哈希失配 fail-closed")
        with self.assertRaises(tfr.TeamFlowReceiptUnavailable,
                               msg=TAG_FAULT) as caught_bad_append:
            tfr.append_receipt_on(corrupt_conn, bad_hash_payload)
        self.assertEqual(caught_bad_append.exception.reason, "corrupt",
                         TAG_FAULT + ": 撞坏行 append 同样 fail-closed")
        with self.assertRaises(tfr.TeamFlowReceiptUnavailable,
                               msg=TAG_FAULT) as caught_bad_origin:
            tfr.read_receipt_on(corrupt_conn,
                                task_instance_id="task-corrupt",
                                actor_user_id=101, operation="op.corrupt",
                                request_id="req-corrupt2")
        self.assertEqual(caught_bad_origin.exception.reason, "corrupt",
                         TAG_FAULT + ": origin 与源绑定失配")
        garbage = self.base / "fault_garbage.db"
        garbage.write_bytes(b"not a sqlite database at all" * 8)
        with self.assertRaises(tfr.TeamFlowReceiptUnavailable,
                               msg=TAG_FAULT) as caught_garbage:
            tfr.prepare_team_flow_receipts(garbage)
        self.assertEqual(caught_garbage.exception.reason, "corrupt",
                         TAG_FAULT + ": 非库文件 NOTADB→corrupt")
        self.assertEqual(caught_garbage.exception.cleanup_errors, (),
                         TAG_FAULT + ": 清理干净的失败携带空 cleanup_errors")
        self.assertFalse(hasattr(caught_garbage.exception, "committed"),
                         TAG_FAULT + ": 未提交失败不得带 committed 标记")

        # --- B6. closed 连接：ProgrammingError 精确透传 ---
        closed_db = self._prepared_db("fault_closed.db")
        closed_conn = self._open(closed_db)
        closed_conn.close()
        with self.assertRaises(sqlite3.ProgrammingError,
                               msg=TAG_FAULT) as caught_closed_a:
            tfr.append_receipt_on(closed_conn, fixture_receipt_payload())
        self.assertIs(type(caught_closed_a.exception),
                      sqlite3.ProgrammingError,
                      TAG_FAULT + ": closed 精确类型透传")
        with self.assertRaises(sqlite3.ProgrammingError,
                               msg=TAG_FAULT) as caught_closed_r:
            tfr.read_receipt_on(closed_conn, task_instance_id="t",
                                actor_user_id=1, operation="o",
                                request_id="r")
        self.assertIs(type(caught_closed_r.exception),
                      sqlite3.ProgrammingError, TAG_FAULT)

        # --- C. 调用方 rollback 后行消失（正控：事务内可见）---
        rb = self._prepared_db("fault_rollback.db")
        rb_conn = self._open(rb)
        rb_record = tfr.append_receipt_on(
            rb_conn, fixture_receipt_payload(request_id="req-rb"))
        self.assertEqual(self._table_count(rb_conn), 1,
                         TAG_FAULT + ": 事务内可见")
        self.assertEqual(
            tfr.read_receipt_on(rb_conn, task_instance_id="task-1",
                                actor_user_id=101, operation="terminal.submit",
                                request_id="req-rb"),
            rb_record, TAG_FAULT)
        rb_conn.rollback()
        rb_conn.execute("BEGIN")
        self.assertIsNone(
            tfr.read_receipt_on(rb_conn, task_instance_id="task-1",
                                actor_user_id=101, operation="terminal.submit",
                                request_id="req-rb"),
            TAG_FAULT + ": rollback 后行消失")
        self.assertEqual(self._table_count(rb_conn), 0, TAG_FAULT)

        # --- D. prepare：mode=rw 不建文件 / 目录 unreadable / 空文件正控 ---
        missing = self.base / "fault_missing.db"
        self.assertFalse(missing.exists(), TAG_FAULT)
        with self.assertRaises(FileNotFoundError, msg=TAG_FAULT):
            tfr.prepare_team_flow_receipts(missing)
        self.assertFalse(missing.exists(),
                         TAG_FAULT + ": mode=rw 绝不创建缺失文件")
        directory = self.base / "adir"
        directory.mkdir()
        with self.assertRaises(tfr.TeamFlowReceiptUnavailable,
                               msg=TAG_FAULT) as caught_dir:
            tfr.prepare_team_flow_receipts(directory)
        self.assertEqual(caught_dir.exception.reason, "unreadable", TAG_FAULT)
        fresh = self.base / "fault_fresh.db"
        fresh_touch = sqlite3.connect(str(fresh))
        fresh_touch.close()
        tfr.prepare_team_flow_receipts(fresh)
        tfr.prepare_team_flow_receipts(fresh)
        fresh_conn = self._open(fresh)
        fresh_record = tfr.append_receipt_on(
            fresh_conn, fixture_receipt_payload(request_id="req-fresh"))
        self.assertEqual(self._table_count(fresh_conn), 1,
                         TAG_FAULT + ": 空文件可建布局且幂等复核")

        # --- E. INSERT OR REPLACE 被守卫拦（正控：守卫前 candidate 行完好）---
        ir = self._prepared_db("fault_replace.db")
        ir_conn = self._open(ir)
        ir_candidate = tfr.append_receipt_on(
            ir_conn, fixture_receipt_payload(request_id="req-ir",
                                             result={"v": 1}))
        ir_stored = self._stored_columns(ir_conn, ir_candidate["receipt_id"])
        with self.assertRaises(sqlite3.IntegrityError,
                               msg=TAG_FAULT) as caught_replace:
            ir_conn.execute(
                "INSERT OR REPLACE INTO main.team_flow_receipts (receipt_id,"
                " task_instance_id, actor_user_id, operation, request_id,"
                " payload_json, payload_sha256, origin, archived_at)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (ir_stored["receipt_id"], "task-1", 101, "terminal.submit",
                 "req-ir", '{"schema":"x"}', "9" * 64, "collab", None))
        self.assertIn("append-only", str(caught_replace.exception),
                      TAG_FAULT + ": 守卫拦 REPLACE")
        self.assertEqual(self._table_count(ir_conn), 1,
                         TAG_FAULT + ": 隐式 DELETE 未发生")
        self.assertEqual(
            self._stored_columns(ir_conn, ir_candidate["receipt_id"]),
            ir_stored, TAG_FAULT + ": candidate 行完好、字节未动")

        # --- F. 主错优先清理错挂 cleanup_errors / 无主错清理错不吞
        #     （调用方侧真实 native 子类故障注入，零 mock）---
        # teardown 修复（run5 定因）：fail_close 注入使产品侧 cursor.close()
        # 失败，未 finalize 的活动语句经 close_v2 的 zombie 连接滞留 OS 文件
        # 句柄（Windows 下主 TemporaryDirectory unlink 报 WinError 32）。
        # 故 fault_cursor.db 移出主临时箱，入独立 best-effort 子箱
        # （ignore_cleanup_errors=True），并按 LIFO 次序注册：连接先关 →
        # gc.collect() 终结残留 traceback 环持有的语句/僵尸连接 → 再清理
        # 该子箱；主目录清理不再被它阻断。
        fault_box = tempfile.TemporaryDirectory(
            prefix="rf216_p07_fault_", ignore_cleanup_errors=True)
        self.addCleanup(fault_box.cleanup)
        self.addCleanup(gc.collect)
        fault_db = Path(fault_box.name) / "fault_cursor.db"
        fault_touch = sqlite3.connect(str(fault_db))
        fault_touch.close()
        tfr.prepare_team_flow_receipts(fault_db)
        self.addCleanup(setattr, _P07FaultConnection, "fail_fetch", False)
        self.addCleanup(setattr, _P07FaultConnection, "fail_close", False)
        _P07FaultConnection.fail_fetch = True
        _P07FaultConnection.fail_close = True
        fault_conn = self._open(fault_db, factory=_P07FaultConnection)
        with self.assertRaises(tfr.TeamFlowReceiptUnavailable,
                               msg=TAG_FAULT) as caught_primary:
            tfr.read_receipt_on(fault_conn, task_instance_id="t",
                                actor_user_id=1, operation="o",
                                request_id="r")
        primary = caught_primary.exception
        self.assertEqual(primary.reason, "unreadable",
                         TAG_FAULT + ": 主错 typed 身份优先")
        cleanup = getattr(primary, "cleanup_errors", None)
        self.assertIsInstance(cleanup, tuple,
                              TAG_FAULT + ": cleanup_errors 挂主错")
        self.assertEqual(len(cleanup), 1, TAG_FAULT)
        self.assertIsInstance(cleanup[0], RuntimeError, TAG_FAULT)
        self.assertIn("injected cursor close failure", str(cleanup[0]),
                      TAG_FAULT)
        fault_conn.rollback()
        _P07FaultConnection.fail_fetch = False
        _P07FaultConnection.fail_close = True
        fault_conn2 = self._open(fault_db, factory=_P07FaultConnection)
        with self.assertRaises(RuntimeError,
                               msg=TAG_FAULT) as caught_cleanup:
            tfr.read_receipt_on(fault_conn2, task_instance_id="t",
                                actor_user_id=1, operation="o",
                                request_id="r")
        self.assertIn("injected cursor close failure",
                      str(caught_cleanup.exception),
                      TAG_FAULT + ": 无主错时清理错原样上抛")
        _P07FaultConnection.fail_fetch = False
        _P07FaultConnection.fail_close = False
        # teardown ①：断言全部完成后复位开关并显式再 close（native
        # sqlite3.Connection.close 幂等，二次 close 无副作用；addCleanup
        # 的 _close_quietly 再关一次亦无害）。
        fault_conn.close()
        fault_conn2.close()


if __name__ == "__main__":
    unittest.main()
