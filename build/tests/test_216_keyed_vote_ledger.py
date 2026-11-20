# -*- coding: utf-8 -*-
"""P04 ledger 窄读接口（keyed vote ledger）独立测试候选 —— 静态撰写，作者未运行。

目标产品候选（本文件唯一针对的实现）：
- E:/rf216-scratch/p04-vote-ledger-20261117T134945Z-2e3936/candidate/team_vote_ledger.py
- SHA-256 69764b70e16d7b53dfdf369f666aab289c46392942a060ce319c8890ae42dc99
  （11 个新符号：LedgerIdentityHeader/KeyedVoteIssue/KeyedVoteSnapshot/
  KEYED_VOTE_ISSUE_REASONS/library_identity_header_on/vote_snapshot_keys_on
  及 5 个私有 helper；旧强 API 21 符号原样保留）
- 主仓基线（coscreen/team_vote_ledger.py）SHA-256
  03a55ee67744c20ae9cab36398f2f9fb9f7a4bde1a87e033dcad6d0714e2be21 —— 不含
  P04 新接口；对本基线运行本文件时新接口用例将 AttributeError（诚实红，
  同 S1a before 侧协议：不以 ImportError 冒充行为红证，运行协议见下）。

合同依据（全部只读引用，本文不编辑任何合同/产品文件）：
- docs/superpowers/plans/2026-11-16-2.1.6-team-work-packages.md（P04 节：文件名、
  五个具名用例、rows/issues/absent 互斥并集断言、旧 vote_snapshot/revisions 正控保持）
- docs/superpowers/plans/2026-11-16-2.1.6-team-contracts.md（C02 键/连接/错误共同
  合同；C04 F2 与 ledger 窄读取：DTO 精确字段、header 仅核 meta schema/format/
  单例、keyed 一次全局守恒校验＋逐请求主键、BLOB 取回严格解码）
- P00 ``custom_backend.team_keyed_domain.normalize_item_keys`` 语义镜像（C02：
  去重保首现；拒 bare str/bytes、非 str、空串、NUL、非法 Unicode、空集合；异常
  类型 ValueError；不采用 PLAN_CC 旧"重复键拒绝"分叉）
- CC-DS 异构静态复审动态矩阵（条件性可接受，2026-11-17）：header canonical/
  各类损坏/unmigrated vs meta_corrupt/revisions 缺但 header 成功；全局校验失败
  typed 无 DTO；逐键正常/tombstone absent/row_missing 优先于 unreadable/
  非法 UTF-8、存储类型、key 不匹配/其他键不受影响；未请求键隔离；C02 连接
  合同；rows 与旧 vote_snapshot 形状逐字段一致；issue.detail 不含原始字节。

诚实记录（不可达分支，不伪造测试）：
- ``zotero_key 解码后与请求键不一致``（key mismatch）防御分支：公开读取路径的
  ``WHERE d.zotero_key=?`` 文本等值匹配已保证命中行的解码文本与请求键一致，
  不可经真实库损坏构造；不调用产品私有 helper 伪造覆盖（如需覆盖须产品另立
  可注入口，超出 P04 范围）。
- integer/real 存储类型：两票表文本列均 TEXT 亲和，数值经正常写入会被转成
  TEXT 存储；可达的存储类损坏是 blob（与 CAST(X'..' AS TEXT) 的非法 UTF-8
  文本）——按可达损坏覆盖。
- 逐键 ``revision 非法`` unreadable 分支：全局校验通过要求 revisions 表保持
  canonical DDL，其 CHECK(typeof='integer' AND revision>=1) 阻止坏 revision 行
  在全局通过前提下存在；不可达 → 不伪造。row_missing 对 unreadable 的优先级
  以"票面 blob 损坏＋守恒一致缺行"的组合损伤钉住。
- 守恒一致的缺行损伤（row_missing 逐键观测的前提）：直接删除 ledger 行会先
  触发全局守恒失败；本文件按 S1a"健康构造/损坏构造"先例做补偿构造——删行同
  时等额回减 advances，保持 Σrevision==seeded1+seeded2+advances，使全局校验
  通过而 JOIN 缺行可观测。这是对攻击者可造状态的忠实构造，不伪造产品输出。
- 产品验证链内部 cursor（sqlite_master/meta SELECT/守恒扫描）未显式 close
  （候选实现交由替换/GC）：本文件断言其完整物化；确定性 close 仅钉按键
  cursor（候选显式 try/finally close）。该观察已在冻结报告记录，不在本文件
  制造红。

覆盖矩阵（矩阵项 → 用例）：
- header canonical 健康          → HeaderIdentityTests.test_header_canonical_positive_control
- revisions 缺/损坏 header 仍成功 → HeaderIdentityTests.test_header_independent_from_broken_revisions
- unmigrated vs meta_corrupt     → HeaderIdentityTests.test_header_unmigrated_vs_partial_meta_corrupt
- meta 各类损坏 → meta_corrupt   → HeaderIdentityTests.test_header_meta_row_and_field_corruption
- DTO 形状/reason 域             → KeyedSnapshotDomainTests.test_dto_shapes_and_reason_domain
- rows 与旧 vote_snapshot 逐字段 → KeyedSnapshotDomainTests.test_rows_shape_matches_legacy_vote_snapshot
- tombstone absent＋row_missing  → KeyedSnapshotDomainTests.test_tombstone_absent_and_row_missing
- row_missing 优先于 unreadable  → KeyedSnapshotDomainTests.test_row_missing_priority_over_unreadable
- 非法 UTF-8/存储类型→unreadable → KeyedSnapshotDomainTests.test_unreadable_storage_type_and_invalid_utf8
- 未请求键隔离                   → KeyedSnapshotDomainTests.test_unrequested_bad_text_is_not_fetched
- stage 路由与跨 stage 隔离      → KeyedSnapshotDomainTests.test_stage2_routing_and_cross_stage_isolation
- 全局 counter/触发器/账行损伤    → GlobalDamageBlocksLiveTests.test_global_counter_damage_still_blocks_live
- 全局失败 typed 无 DTO          → GlobalDamageBlocksLiveTests.test_damage_matrix_typed_failures_without_dto
- C02 事务/工厂不可变＋cursor     → BorrowedConnectionContractTests.test_no_transaction_or_factory_mutation
- C02 非 native/closed/无事务等   → BorrowedConnectionContractTests.test_connection_contract_negative_paths
- P00 键语义（去重/各类拒绝）     → NormalizeKeySemanticsTests（5 用例）
- 旧强 API 正控保持              → LegacyStrongApiControlsTests（2 用例）

撰写协议（同 P00 先例）：本文件每个 def test_/类/helper 符号在写入前均逐项
运行 GitNexus CLI upstream impact（--repo E:/mubi-reviewflow --file
build/tests/test_216_keyed_vote_ledger.py --kind Method/Class --include-tests；
新文件预期 not found/UNKNOWN；raw/rc 存档于
E:/rf216-scratch/p04-vote-tests-20261117T2212Z-q7m4k9/impact-raw/，共 64 项，
每次 CLI 前经 ps 门确认无其他 gitnexus 作业、常驻 idle MCP 不计）。

未运行声明：作者仅静态撰写（ast.parse/compile 与符号清单自检），未运行任何
测试/SQL/产品/API，未查询真实库；执行留给独立 runner 在应用产品候选后进行。
禁主仓/前端/真实 data/旧证据箱写入；夹具全部位于测试自有 TemporaryDirectory。
自包含：不依赖 conftest/Safety 夹具、不触 HTTP 层；静态消费者仅 unittest。
"""

from __future__ import annotations

import hashlib
import importlib
import re
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

STAGE1, STAGE2 = "stage1", "stage2"

#: 目标产品候选与主仓基线的 SHA-256（运行时打印观测值供核对，不做硬断言：
#: 候选后续修订经复审后 SHA 自然变化，硬断言会制造假红）。
P04_CANDIDATE_LEDGER_SHA = (
    "69764b70e16d7b53dfdf369f666aab289c46392942a060ce319c8890ae42dc99")
P04_BASELINE_LEDGER_SHA = (
    "03a55ee67744c20ae9cab36398f2f9fb9f7a4bde1a87e033dcad6d0714e2be21")
P04_CANDIDATE_SOURCE = (
    "E:/rf216-scratch/p04-vote-ledger-20261117T134945Z-2e3936/candidate/"
    "team_vote_ledger.py")

_KV_HEX32 = re.compile(r"^[0-9a-f]{32}$")

#: 旧 vote_snapshot / keyed rows 行字典的合同字段集（决策表 7 列＋revision）。
_KV_ROW_FIELDS = frozenset({
    "zotero_key", "decision", "exclusion_reason", "notes",
    "tags", "screened_at", "updated_at", "revision",
})

_KV_TS1 = "2026-11-17T08:00:00+00:00"
_KV_TS2 = "2026-11-17T09:00:00+00:00"

#: 红侧失败输出定位标签（真实缺口按标签归组）。
TAG_HEADER = "P04-header identity narrow read"
TAG_KEYED_DOMAIN = "P04-keyed per-key domain"
TAG_GLOBAL = "P04-global typed failure no pseudo DTO"
TAG_CONTRACT = "P04-C02 borrowed connection contract"
TAG_NORMALIZE = "P04-P00 key normalization semantics"
TAG_LEGACY = "P04-legacy strong API controls"

#: S1a 先例常量：账行被删同时构成 JOIN 缺行（row_missing）与 Σ 缺口
#: （conservation_mismatch），旧强 vote_snapshot 的归因在该域内自由；新 keyed
#: 一次全局校验先行，钉 conservation_mismatch（matrix 要求）。
REASONS_LEGACY_DELETE = ("row_missing", "conservation_mismatch")

_KV_PROVENANCE_PRINTED = False


def _kv_sha256(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _kv_vote_row(key, decision="include", **overrides):
    """构造一条票面种子（7 列显式值；None 允许写入可空自由文本列）。"""
    row = {
        "zotero_key": key,
        "decision": decision,
        "exclusion_reason": "",
        "notes": "",
        "tags": "",
        "screened_at": _KV_TS1,
        "updated_at": _KV_TS1,
    }
    row.update(overrides)
    return row


def _kv_first_keyword(sql_text):
    """剥前导 ``--`` 注释行后取首关键字/记号（先例 _kd_first_keyword）。"""
    text = sql_text.strip()
    while text.startswith("--"):
        remainder = text.split("\n", 1)
        text = remainder[1].strip() if len(remainder) > 1 else ""
    if not text:
        return ""
    return re.split(r"[\s(]", text, 1)[0].upper()


def _kv_classify_statements(statements):
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
        keyword = _kv_first_keyword(statement)
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


_KV_DECISIONS_TABLE_RE = re.compile(
    r"FROM\s+(?:main\.)?(decisions|stage2_decisions)\b", re.IGNORECASE)


def _kv_decisions_reads_bounded(cursors, requested):
    """验证票表读取事件全部以请求键为界（未请求键零读取）。

    返回 (票表读取事件数, 违规描述列表)。违规包括：票表 SELECT 未绑定任何
    请求域内键参数（全域扫描/未键定读取的形态证据）。
    """
    allowed = set(requested)
    count = 0
    violations = []
    for cursor in cursors:
        sql = cursor.sql or ""
        if not _KV_DECISIONS_TABLE_RE.search(sql):
            continue
        count += 1
        params = cursor.parameters
        if not isinstance(params, tuple):
            params = ()
        bound = [p for p in params if isinstance(p, str) and p in allowed]
        if not bound:
            violations.append(
                "票表读取未绑定请求域键（未按键窄读）: %r" % sql[:100])
    return count, violations


class _kv_ProbeCursor:
    """Cursor 代理（P04 窄读观测）：登记 execute SQL/参数与 fetch/close 行为。

    仅观测；不做故障注入（清理双故障合同由 P00 侧覆盖，本包矩阵未含）。
    """

    def __init__(self, cursor, registry=None):
        self._cursor = cursor
        self.sql = ""
        self.parameters = ()
        self._registry = registry
        self.rows_returned = 0
        self.fetchone_called = False
        self.fetchall_called = False
        self.saw_empty = False
        self.closed = False
        if registry is not None:
            registry.append(self)

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


class _kv_ProbeConnection(sqlite3.Connection):
    """Native 子类探针：语句 trace 登记 + commit/rollback/close 计数 + cursor 登记。

    供"借用 API 不控制调用方事务/不 close 连接/只读语句面"断言；C02 的真实
    type 继承判断接受本类（真实 native 子类，非 __class__ 伪装）。
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
        self._kv_closed = False
        self.set_trace_callback(self.statements.append)

    def execute(self, sql, parameters=()):
        raw = super().execute(sql, parameters)
        cursor = _kv_ProbeCursor(raw, self.cursors)
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
        if self._kv_closed:
            return
        super().close()
        self._kv_closed = True
        self.close_calls += 1


class _kv_DelegatingConnection:
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


class KeyedVoteLedgerTestBase(unittest.TestCase):
    """公共基座：临时箱 + 延迟产品导入 + 真实库 fixture/损伤构造 + 共用断言。"""

    maxDiff = None

    def setUp(self):
        global _KV_PROVENANCE_PRINTED
        self._tmp = tempfile.TemporaryDirectory(prefix="rf216-p04-kv-")
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        self.ledger = importlib.import_module("coscreen.team_vote_ledger")
        self.dbm = importlib.import_module("coscreen.db")
        if not _KV_PROVENANCE_PRINTED:
            _KV_PROVENANCE_PRINTED = True
            print("P04_TEST_FILE_SHA", _kv_sha256(__file__))
            print("P04_LEDGER_SOURCE_SHA", _kv_sha256(self.ledger.__file__))
            print("P04_LEDGER_TARGET_CANDIDATE_SHA", P04_CANDIDATE_LEDGER_SHA)
            print("P04_LEDGER_BASELINE_SHA", P04_BASELINE_LEDGER_SHA)
            print("P04_PYTHON", sys.version.split()[0])
            print("P04_SQLITE_VERSION", sqlite3.sqlite_version)

    # ------------------------------------------------------------------
    # 连接工厂
    # ------------------------------------------------------------------

    def _kv_open_borrowed(self, path, *, begin=True):
        """C02 借用形态：native 子类探针 + Row 工厂 + 调用方活动事务。"""
        conn = sqlite3.connect(str(path), timeout=15,
                               factory=_kv_ProbeConnection)
        self.addCleanup(self._kv_close_probe, conn)
        conn.row_factory = sqlite3.Row
        if begin:
            with closing(conn.execute("BEGIN IMMEDIATE")):
                pass
        return conn

    def _kv_open_plain(self, path):
        """旧强 API 形态：普通连接（无 Row 工厂、无调用方事务，快照自管）。"""
        return sqlite3.connect(str(path), timeout=15)

    def _kv_close_probe(self, probe):
        if getattr(probe, "_kv_closed", False):
            return
        for cursor in list(getattr(probe, "cursors", ())):
            try:
                cursor.close()
            except sqlite3.Error:
                pass
        probe.close()

    def _kv_reset_probe(self, probe):
        """偏移复位：仅观察此后的语句/cursor/计数。"""
        probe.statement_offset = len(probe.statements)
        probe.cursor_offset = len(probe.cursors)
        probe.commit_calls = 0
        probe.rollback_calls = 0
        probe.close_calls = 0

    # ------------------------------------------------------------------
    # Fixture（真实 init_db schema + 直插种子；损坏构造如实记录）
    # ------------------------------------------------------------------

    def _kv_make_ledger_db(self, path, *, votes1=(), votes2=(),
                           articles_extra=(), corrupt_rows=()):
        """真实 init_db（含账本安装）+ FK ON 直插种子。

        votes1/votes2: ``_kv_vote_row`` 字典（触发真实触发器，守恒自洽）。
        articles_extra: 仅建 article 不投票的键。
        corrupt_rows: [(sql, params)] —— FK ON 下可施加的值级损坏（非键列
        blob/非法 UTF-8 文本等；触发器随 UPDATE 推进，守恒保持）。
        """
        self.dbm.init_db(path)
        conn = sqlite3.connect(str(path), timeout=15)
        try:
            conn.execute("PRAGMA busy_timeout=15000")
            conn.execute("PRAGMA foreign_keys=ON")
            all_keys = ([row["zotero_key"] for row in votes1]
                        + [row["zotero_key"] for row in votes2]
                        + list(articles_extra))
            for key in all_keys:
                conn.execute(
                    "INSERT OR IGNORE INTO articles"
                    " (zotero_key, title, is_duplicate_of, import_order)"
                    " VALUES (?, ?, NULL, ?)", (key, "p04-" + key, None))
            for table, votes in (("decisions", votes1),
                                 ("stage2_decisions", votes2)):
                for row in votes:
                    conn.execute(
                        "INSERT INTO %s (zotero_key, decision,"
                        " exclusion_reason, notes, tags, screened_at,"
                        " updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)" % table,
                        (row["zotero_key"], row["decision"],
                         row["exclusion_reason"], row["notes"], row["tags"],
                         row["screened_at"], row["updated_at"]))
            for statement, params in corrupt_rows:
                conn.execute(statement, params)
            conn.commit()
        finally:
            conn.close()
        return path

    def _kv_make_old_style_db(self, path, stage1_keys=(), stage2_keys=()):
        """真实旧库形态：手工建 articles+两决策表，零账本对象（unmigrated）。"""
        conn = sqlite3.connect(str(path), timeout=15)
        try:
            conn.execute("PRAGMA busy_timeout=15000")
            conn.executescript("""
CREATE TABLE articles(
  zotero_key TEXT PRIMARY KEY, title TEXT NOT NULL, doi TEXT, abstract TEXT,
  content_hash TEXT, is_duplicate_of TEXT, import_order INTEGER);
CREATE TABLE decisions(
  zotero_key TEXT PRIMARY KEY REFERENCES articles(zotero_key),
  decision TEXT NOT NULL, exclusion_reason TEXT DEFAULT '',
  notes TEXT DEFAULT '', tags TEXT DEFAULT '',
  screened_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE stage2_decisions(
  zotero_key TEXT PRIMARY KEY REFERENCES articles(zotero_key),
  decision TEXT NOT NULL, exclusion_reason TEXT DEFAULT '',
  notes TEXT DEFAULT '', tags TEXT DEFAULT '',
  screened_at TEXT NOT NULL, updated_at TEXT NOT NULL);
""")
            for key in stage1_keys:
                conn.execute(
                    "INSERT INTO articles(zotero_key,title) VALUES (?, ?)",
                    (key, "old-" + key))
                conn.execute(
                    "INSERT INTO decisions(zotero_key,decision,screened_at,"
                    "updated_at) VALUES (?, 'include', ?, ?)",
                    (key, _KV_TS1, _KV_TS1))
            for key in stage2_keys:
                if not conn.execute(
                        "SELECT 1 FROM articles WHERE zotero_key=?",
                        (key,)).fetchone():
                    conn.execute(
                        "INSERT INTO articles(zotero_key,title) VALUES (?, ?)",
                        (key, "old-" + key))
                conn.execute(
                    "INSERT INTO stage2_decisions(zotero_key,decision,"
                    "screened_at,updated_at) VALUES (?, 'exclude', ?, ?)",
                    (key, _KV_TS2, _KV_TS2))
            conn.commit()
        finally:
            conn.close()
        return path

    def _kv_apply_damage(self, path, statements, *, fk=False):
        """裸连接直施损伤（结构反例默认 FK OFF，先例 _kd_variant_db）。"""
        conn = sqlite3.connect(str(path), timeout=15)
        try:
            conn.execute("PRAGMA busy_timeout=15000")
            conn.execute("PRAGMA foreign_keys=%s" % ("ON" if fk else "OFF"))
            for statement, params in statements:
                conn.execute(statement, params)
            conn.commit()
        finally:
            conn.close()
        return path

    def _kv_rebuild_meta_weak(self, path, ddl, insert_sql, insert_params):
        """把 meta 表重建为无 CHECK 弱形态并写入指定行（真实历史坏态构造）。

        弱 DDL 族在 canonical DDL 比对层即判 meta_corrupt；canonical 表的
        CHECK 使行级坏值（坏 format/坏 id/双行）不可经正常写达成——两层的
        失败分类同为 meta_corrupt（合同域），如实按可达层覆盖。
        """
        return self._kv_apply_damage(path, [
            ("DROP TABLE team_vote_revision_meta", ()),
            (ddl, ()),
            (insert_sql, insert_params),
        ])

    def _kv_rebuild_decisions_nullable(self, path, extra_inserts):
        """decisions 重建为 decision/时间列可空形态（触发器 DDL 原文重建）。

        捕获 sqlite_master 中的三条触发器原文 → DROP 表（其触发器随之删）→
        建可空表 → 拷回既有行（无触发器，不推进）→ 按原文重建触发器 → 施加
        extra_inserts（经触发器，守恒推进）。账本 canonical DDL 全程保持。
        """
        conn = sqlite3.connect(str(path), timeout=15)
        try:
            conn.execute("PRAGMA busy_timeout=15000")
            conn.execute("PRAGMA foreign_keys=OFF")
            trigger_ddls = [
                row[0] for row in conn.execute(
                    "SELECT sql FROM sqlite_master WHERE type='trigger' "
                    "AND tbl_name='decisions' AND sql IS NOT NULL "
                    "ORDER BY name").fetchall()]
            existing = conn.execute(
                "SELECT zotero_key, decision, exclusion_reason, notes, tags,"
                " screened_at, updated_at FROM decisions").fetchall()
            conn.execute("DROP TABLE decisions")
            conn.execute(
                "CREATE TABLE decisions(\n"
                "  zotero_key TEXT PRIMARY KEY REFERENCES articles(zotero_key),\n"
                "  decision TEXT, exclusion_reason TEXT,\n"
                "  notes TEXT, tags TEXT,\n"
                "  screened_at TEXT, updated_at TEXT)")
            for row in existing:
                conn.execute(
                    "INSERT INTO decisions(zotero_key, decision,"
                    " exclusion_reason, notes, tags, screened_at, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?)", tuple(row))
            for ddl in trigger_ddls:
                conn.execute(ddl)
            for statement, params in extra_inserts:
                conn.execute(statement, params)
            conn.commit()
        finally:
            conn.close()
        return path

    def _kv_compensated_row_missing(self, path, key, stage=STAGE1):
        """守恒一致的缺行损伤：删除该键 ledger 行并等额回减 advances。

        使全局守恒校验通过而票行缺 ledger 行可经 keyed 公开路径观测（直接
        删行会先触发全局 conservation_mismatch，见文件 docstring 诚实记录）。
        """
        conn = sqlite3.connect(str(path), timeout=15)
        try:
            conn.execute("PRAGMA busy_timeout=15000")
            row = conn.execute(
                "SELECT revision FROM team_vote_revisions"
                " WHERE stage=? AND item_key=?", (stage, key)).fetchone()
            if row is None:
                self.fail("fixture 前置失败：%s 无 ledger 行可删" % key)
            revision = int(row[0])
            advances = int(conn.execute(
                "SELECT advances FROM team_vote_revision_meta").fetchone()[0])
            if advances < revision:
                self.fail(
                    "fixture 前置失败：advances=%d < revision=%d，无法构造"
                    " 守恒一致缺行" % (advances, revision))
            conn.execute(
                "DELETE FROM team_vote_revisions WHERE stage=? AND item_key=?",
                (stage, key))
            conn.execute(
                "UPDATE team_vote_revision_meta SET advances=advances-?",
                (revision,))
            conn.commit()
        finally:
            conn.close()
        return path

    # ------------------------------------------------------------------
    # 共用断言
    # ------------------------------------------------------------------

    def _kv_snapshot_domain_assert(self, snapshot, *, expected_rows=None,
                                   expected_absent=None, expected_issues=None):
        """P04 规格断言：三桶互斥、并集==请求域、absent 依请求序。"""
        self.assertIsInstance(snapshot.requested_keys, tuple)
        self.assertIsInstance(snapshot.absent_keys, tuple)
        self.assertIsInstance(snapshot.rows, dict)
        self.assertIsInstance(snapshot.issues, dict)
        self.assertTrue(set(snapshot.rows).isdisjoint(snapshot.issues),
                        TAG_KEYED_DOMAIN + ": rows/issues 必须互斥")
        self.assertTrue(set(snapshot.rows).isdisjoint(snapshot.absent_keys),
                        TAG_KEYED_DOMAIN + ": rows/absent 必须互斥")
        self.assertTrue(
            set(snapshot.absent_keys).isdisjoint(snapshot.issues),
            TAG_KEYED_DOMAIN + ": absent/issues 必须互斥")
        self.assertEqual(
            set(snapshot.rows) | set(snapshot.absent_keys)
            | set(snapshot.issues),
            set(snapshot.requested_keys),
            TAG_KEYED_DOMAIN + ": 三桶并集必须等于请求域")
        absent_set = set(snapshot.absent_keys)
        self.assertEqual(
            snapshot.absent_keys,
            tuple(k for k in snapshot.requested_keys if k in absent_set),
            TAG_KEYED_DOMAIN + ": absent_keys 必须保持请求首现顺序")
        for issue in snapshot.issues.values():
            self.assertIsInstance(issue, self.ledger.KeyedVoteIssue,
                                  TAG_KEYED_DOMAIN + ": issues 值类型")
            self.assertIn(issue.reason,
                          self.ledger.KEYED_VOTE_ISSUE_REASONS,
                          TAG_KEYED_DOMAIN + ": issue.reason 合同域")
        if expected_rows is not None:
            self.assertEqual(set(snapshot.rows), set(expected_rows),
                             TAG_KEYED_DOMAIN + ": rows 键集")
        if expected_absent is not None:
            self.assertEqual(set(snapshot.absent_keys), set(expected_absent),
                             TAG_KEYED_DOMAIN + ": absent 键集")
        if expected_issues is not None:
            self.assertEqual(set(snapshot.issues), set(expected_issues),
                             TAG_KEYED_DOMAIN + ": issues 键集")

    def _kv_assert_issue_detail_clean(self, issue, raw, tag):
        """issue.detail 不含原始字节：严格 UTF-8 可编码＋latin-1/hex 形态缺席。"""
        self.assertIsInstance(issue.detail, str, tag + ": detail 类型")
        try:
            issue.detail.encode("utf-8", "strict")
        except UnicodeEncodeError:
            self.fail(tag + ": issue.detail 含代理转义原始字节: %r"
                      % (issue.detail,))
        if raw:
            self.assertNotIn(raw.decode("latin-1"), issue.detail,
                             tag + ": detail 不得含原始字节（latin-1 形态）")
            hexed = raw.hex()
            self.assertNotIn(hexed, issue.detail,
                             tag + ": detail 不得含原始字节（hex 小写形态）")
            self.assertNotIn(hexed.upper(), issue.detail,
                             tag + ": detail 不得含原始字节（hex 大写形态）")


class HeaderIdentityTests(KeyedVoteLedgerTestBase):
    """C04 header：canonical meta/单例/format2/32hex；不读 revisions/触发器/
    counter/票面。"""

    def test_header_canonical_positive_control(self):
        db = self._kv_make_ledger_db(
            self.base / "header-ok.db",
            votes1=[_kv_vote_row("A1", "include", notes="n-A1", tags="t-A1")])
        probe = self._kv_open_borrowed(db)
        try:
            header = self.ledger.library_identity_header_on(probe)
            self.assertIsInstance(header, self.ledger.LedgerIdentityHeader,
                                  TAG_HEADER)
            self.assertEqual(self.ledger.LedgerIdentityHeader._fields,
                             ("library_id", "format_version"), TAG_HEADER)
            self.assertEqual(header.format_version,
                             self.ledger.LEDGER_FORMAT_VERSION, TAG_HEADER)
            self.assertEqual(header.format_version, 2, TAG_HEADER)
            self.assertRegex(header.library_id, _KV_HEX32, TAG_HEADER)
            header_again = self.ledger.library_identity_header_on(probe)
            self.assertEqual(header_again, header,
                             TAG_HEADER + ": 幂等一致")
        finally:
            probe.close()
        # 与旧强 API 同一库身份一致（健康库正控：强路径可用且一致）。
        with closing(self._kv_open_plain(db)) as plain:
            self.assertEqual(self.ledger.ledger_state(plain), "installed",
                             TAG_HEADER)
            self.assertEqual(self.ledger.library_id(plain),
                             header.library_id, TAG_HEADER)

    def test_header_independent_from_broken_revisions(self):
        """revisions 表缺/清空/缺行时 header 仍成功；强路径与 keyed typed 拒绝。"""
        variants = (
            ("drop-revisions-table",
             [("DROP TABLE team_vote_revisions", ())], "meta_corrupt"),
            ("revisions-emptied",
             [("DELETE FROM team_vote_revisions", ())],
             "conservation_mismatch"),
            ("revisions-one-row-deleted",
             [("DELETE FROM team_vote_revisions WHERE stage=? AND item_key=?",
               (STAGE1, "A2"))], "conservation_mismatch"),
        )
        for label, damage, keyed_reason in variants:
            with self.subTest(variant=label):
                db = self._kv_make_ledger_db(
                    self.base / ("hdr-%s.db" % label),
                    votes1=[_kv_vote_row("A1"), _kv_vote_row("A2")])
                probe = self._kv_open_borrowed(db)
                try:
                    healthy = self.ledger.library_identity_header_on(probe)
                    snap = self.ledger.vote_snapshot_keys_on(
                        probe, STAGE1, ("A1",))
                    self.assertEqual(set(snap.rows), {"A1"},
                                     TAG_HEADER + ": 损伤前健康正控")
                finally:
                    probe.close()
                self._kv_apply_damage(db, damage)
                probe2 = self._kv_open_borrowed(db)
                try:
                    after = self.ledger.library_identity_header_on(probe2)
                    self.assertEqual(
                        after, healthy,
                        TAG_HEADER + ": revisions 损伤不得影响身份头")
                    with self.assertRaises(
                            self.ledger.VoteLedgerUnavailable,
                            msg=TAG_HEADER) as caught:
                        self.ledger.vote_snapshot_keys_on(probe2, STAGE1,
                                                          ("A1",))
                    self.assertEqual(
                        caught.exception.reason, keyed_reason,
                        TAG_HEADER + ": keyed 全局校验 typed 拒绝")
                finally:
                    probe2.close()
                with closing(self._kv_open_plain(db)) as plain:
                    self.assertEqual(self.ledger.ledger_state(plain),
                                     "broken", TAG_HEADER)
                    with self.assertRaises(self.ledger.VoteLedgerUnavailable,
                                           msg=TAG_HEADER) as caught2:
                        self.ledger.library_id(plain)
                    self.assertIn(caught2.exception.reason,
                                  self.ledger.REASONS, TAG_HEADER)

    def test_header_unmigrated_vs_partial_meta_corrupt(self):
        # 未迁移：旧式库（票表在、零账本对象）→ header/keyed 均 unmigrated。
        old_db = self._kv_make_old_style_db(
            self.base / "old-style.db", stage1_keys=("U1",))
        probe = self._kv_open_borrowed(old_db)
        try:
            self._kv_reset_probe(probe)
            with self.assertRaises(self.ledger.VoteLedgerUnavailable,
                                   msg=TAG_HEADER) as caught:
                self.ledger.library_identity_header_on(probe)
            self.assertEqual(caught.exception.reason, "unmigrated",
                             TAG_HEADER + ": 零账本对象=unmigrated")
            with self.assertRaises(self.ledger.VoteLedgerUnavailable,
                                   msg=TAG_HEADER) as caught2:
                self.ledger.vote_snapshot_keys_on(probe, STAGE1, ("U1",))
            self.assertEqual(caught2.exception.reason, "unmigrated",
                             TAG_HEADER)
            # 无副作用：失败不安装/不建对象（语句面无 DDL/DML/txn 控制）。
            buckets = _kv_classify_statements(
                probe.statements[probe.statement_offset:])
            self.assertEqual(buckets["ddl"], [], TAG_HEADER + ": 不得 DDL")
            self.assertEqual(buckets["dml"], [], TAG_HEADER + ": 不得 DML")
            self.assertEqual(buckets["txn"], [],
                             TAG_HEADER + ": 不得事务控制")
        finally:
            probe.close()
        with closing(self._kv_open_plain(old_db)) as plain:
            self.assertEqual(self.ledger.ledger_state(plain), "absent",
                             TAG_HEADER + ": 失败后仍未安装")
        # 部分对象：revisions 在而 meta 缺 → meta_corrupt（不是 unmigrated）。
        partial = self._kv_make_ledger_db(
            self.base / "partial.db", votes1=[_kv_vote_row("A1")])
        self._kv_apply_damage(
            partial, [("DROP TABLE team_vote_revision_meta", ())])
        probe2 = self._kv_open_borrowed(partial)
        try:
            with self.assertRaises(self.ledger.VoteLedgerUnavailable,
                                   msg=TAG_HEADER) as caught3:
                self.ledger.library_identity_header_on(probe2)
            self.assertEqual(caught3.exception.reason, "meta_corrupt",
                             TAG_HEADER + ": meta 缺而 revisions 在=部分对象")
            with self.assertRaises(self.ledger.VoteLedgerUnavailable,
                                   msg=TAG_HEADER) as caught4:
                self.ledger.vote_snapshot_keys_on(probe2, STAGE1, ("A1",))
            self.assertEqual(caught4.exception.reason, "meta_corrupt",
                             TAG_HEADER)
        finally:
            probe2.close()
        # 成对正控：健康安装库 header 成功。
        healthy = self._kv_make_ledger_db(
            self.base / "healthy.db", votes1=[_kv_vote_row("A1")])
        probe3 = self._kv_open_borrowed(healthy)
        try:
            header = self.ledger.library_identity_header_on(probe3)
            self.assertRegex(header.library_id, _KV_HEX32, TAG_HEADER)
        finally:
            probe3.close()

    def test_header_meta_row_and_field_corruption(self):
        # 成对正控：canonical meta → header 成功。
        healthy = self._kv_make_ledger_db(
            self.base / "meta-ok.db", votes1=[_kv_vote_row("A1")])
        probe0 = self._kv_open_borrowed(healthy)
        try:
            good = self.ledger.library_identity_header_on(probe0)
            self.assertEqual(good.format_version, 2, TAG_HEADER)
        finally:
            probe0.close()
        weak_ddl = ("CREATE TABLE team_vote_revision_meta("
                    "singleton INTEGER PRIMARY KEY, format_version INTEGER, "
                    "library_id TEXT)")
        bad_id = "z" * 32
        variants = (
            ("meta-rows-emptied",
             [("DELETE FROM team_vote_revision_meta", ())]),
            ("meta-weak-unsupported-format",
             [("DROP TABLE team_vote_revision_meta", ()),
              (weak_ddl, ()),
              ("INSERT INTO team_vote_revision_meta"
               "(singleton,format_version,library_id) VALUES(1, ?, 'x')",
               (self.ledger.LEDGER_FORMAT_VERSION + 1,))]),
            ("meta-weak-bad-library-id",
             [("DROP TABLE team_vote_revision_meta", ()),
              (weak_ddl, ()),
              ("INSERT INTO team_vote_revision_meta"
               "(singleton,format_version,library_id) VALUES(1, ?, ?)",
               (self.ledger.LEDGER_FORMAT_VERSION, bad_id))]),
            ("meta-weak-duplicate-singleton",
             [("DROP TABLE team_vote_revision_meta", ()),
              (weak_ddl, ()),
              ("INSERT INTO team_vote_revision_meta"
               "(singleton,format_version,library_id)"
               " VALUES(1, ?, 'a'),(2, ?, 'b')",
               (self.ledger.LEDGER_FORMAT_VERSION,
                self.ledger.LEDGER_FORMAT_VERSION))]),
        )
        for label, damage in variants:
            with self.subTest(variant=label):
                db = self._kv_make_ledger_db(
                    self.base / ("meta-%s.db" % label),
                    votes1=[_kv_vote_row("A1")])
                self._kv_apply_damage(db, damage)
                probe = self._kv_open_borrowed(db)
                try:
                    with self.assertRaises(
                            self.ledger.VoteLedgerUnavailable,
                            msg=TAG_HEADER) as caught:
                        self.ledger.library_identity_header_on(probe)
                    self.assertEqual(caught.exception.reason, "meta_corrupt",
                                     TAG_HEADER + ": " + label)
                    with self.assertRaises(
                            self.ledger.VoteLedgerUnavailable,
                            msg=TAG_HEADER) as caught2:
                        self.ledger.vote_snapshot_keys_on(probe, STAGE1,
                                                          ("A1",))
                    self.assertEqual(caught2.exception.reason, "meta_corrupt",
                                     TAG_HEADER + ": " + label)
                finally:
                    probe.close()
                with closing(self._kv_open_plain(db)) as plain:
                    with self.assertRaises(
                            self.ledger.VoteLedgerUnavailable,
                            msg=TAG_HEADER) as caught3:
                        self.ledger.library_id(plain)
                    self.assertEqual(caught3.exception.reason, "meta_corrupt",
                                     TAG_HEADER + ": " + label)


class KeyedSnapshotDomainTests(KeyedVoteLedgerTestBase):
    """C04 keyed：互斥三桶、逐键正常/absent/issue、形状与旧 API 一致、隔离。"""

    def test_dto_shapes_and_reason_domain(self):
        self.assertEqual(self.ledger.KEYED_VOTE_ISSUE_REASONS,
                         ("row_missing", "unreadable"), TAG_KEYED_DOMAIN)
        self.assertEqual(self.ledger.KeyedVoteIssue._fields,
                         ("reason", "detail"), TAG_KEYED_DOMAIN)
        self.assertEqual(self.ledger.KeyedVoteSnapshot._fields,
                         ("stage", "library_id", "requested_keys", "rows",
                          "absent_keys", "issues"), TAG_KEYED_DOMAIN)
        self.assertEqual(self.ledger.LedgerIdentityHeader._fields,
                         ("library_id", "format_version"), TAG_KEYED_DOMAIN)
        issue = self.ledger.KeyedVoteIssue("row_missing", "d")
        self.assertEqual((issue.reason, issue.detail),
                         ("row_missing", "d"), TAG_KEYED_DOMAIN)
        self.assertEqual(self.ledger.KeyedVoteIssue("unreadable").detail, "",
                         TAG_KEYED_DOMAIN)

    def test_rows_shape_matches_legacy_vote_snapshot(self):
        db = self._kv_make_ledger_db(
            self.base / "shape.db",
            votes1=[
                _kv_vote_row("A1", "include", exclusion_reason="er-A1",
                             notes="n-A1", tags="t-A1"),
                _kv_vote_row("B2", "exclude", exclusion_reason=None,
                             notes=None, tags=None),
                _kv_vote_row("D4", "include"),
            ],
            articles_extra=("C3", "E5"))
        # 结构改造：decisions 重建为可空 decision/时间列（触发器原文重建，
        # 账本 canonical 保持），插入 C3（decision NULL、两时间 NULL）。
        self._kv_rebuild_decisions_nullable(db, extra_inserts=[
            ("INSERT INTO decisions(zotero_key, decision, exclusion_reason,"
             " notes, tags, screened_at, updated_at)"
             " VALUES(?, ?, ?, ?, ?, ?, ?)",
             ("C3", None, "er-C3", None, "t-C3", None, None))])
        # D4 删除票行 → 墓碑（revision 2）；E5 有 article 无票；GHOST 全无。
        self._kv_apply_damage(
            db, [("DELETE FROM decisions WHERE zotero_key='D4'", ())])
        plain = self._kv_open_plain(db)
        try:
            lib_old, mapping_old = self.ledger.vote_snapshot(plain, STAGE1)
            self.assertEqual(set(mapping_old), {"A1", "B2", "C3"},
                             TAG_KEYED_DOMAIN)
            for key, row in mapping_old.items():
                self.assertEqual(set(row), _KV_ROW_FIELDS,
                                 TAG_KEYED_DOMAIN + ": 行字典字段集 " + key)
            self.assertEqual(mapping_old["A1"], {
                "zotero_key": "A1", "decision": "include",
                "exclusion_reason": "er-A1", "notes": "n-A1", "tags": "t-A1",
                "screened_at": _KV_TS1, "updated_at": _KV_TS1,
                "revision": 1}, TAG_KEYED_DOMAIN)
            # 三个自由文本 NULL → ''；decision/两时间保留 None（旧合同形状）。
            self.assertEqual(mapping_old["B2"]["exclusion_reason"], "",
                             TAG_KEYED_DOMAIN)
            self.assertEqual(mapping_old["B2"]["notes"], "", TAG_KEYED_DOMAIN)
            self.assertEqual(mapping_old["B2"]["tags"], "", TAG_KEYED_DOMAIN)
            self.assertIsNone(mapping_old["C3"]["decision"], TAG_KEYED_DOMAIN)
            self.assertIsNone(mapping_old["C3"]["screened_at"],
                              TAG_KEYED_DOMAIN)
            self.assertIsNone(mapping_old["C3"]["updated_at"],
                              TAG_KEYED_DOMAIN)
            self.assertEqual(mapping_old["C3"]["notes"], "",
                             TAG_KEYED_DOMAIN)
            self.assertIsInstance(mapping_old["A1"]["revision"], int,
                                  TAG_KEYED_DOMAIN)
            # 墓碑正证：revisions 仍含 D4=2（absent≠从未投票证明）。
            self.assertEqual(
                self.ledger.revisions(plain, STAGE1),
                {"A1": 1, "B2": 1, "C3": 1, "D4": 2}, TAG_KEYED_DOMAIN)
        finally:
            plain.close()
        keys = ("A1", "B2", "C3", "D4", "E5", "GHOST-NO-ARTICLE")
        probe = self._kv_open_borrowed(db)
        try:
            header = self.ledger.library_identity_header_on(probe)
            snap = self.ledger.vote_snapshot_keys_on(probe, STAGE1, keys)
        finally:
            probe.close()
        self.assertEqual(snap.library_id, lib_old,
                         TAG_KEYED_DOMAIN + ": keyed 与旧 API 同一 library_id")
        self.assertEqual(header.library_id, lib_old, TAG_KEYED_DOMAIN)
        self.assertEqual(snap.stage, STAGE1, TAG_KEYED_DOMAIN)
        self._kv_snapshot_domain_assert(
            snap, expected_rows=("A1", "B2", "C3"),
            expected_absent={"D4", "E5", "GHOST-NO-ARTICLE"},
            expected_issues=set())
        # 逐字段与旧 vote_snapshot 一致（含 None/'' 语义与 revision）。
        for key in ("A1", "B2", "C3"):
            self.assertEqual(snap.rows[key], mapping_old[key],
                             TAG_KEYED_DOMAIN + ": rows 形状与旧 API 一致 "
                             + key)
        self.assertEqual(snap.rows["B2"]["tags"], "", TAG_KEYED_DOMAIN)
        self.assertIsNone(snap.rows["C3"]["decision"], TAG_KEYED_DOMAIN)

    def test_tombstone_absent_and_row_missing(self):
        db = self._kv_make_ledger_db(
            self.base / "tombstone.db",
            votes1=[_kv_vote_row("A1", "include", notes="n-A1"),
                    _kv_vote_row("K2", "exclude"),
                    _kv_vote_row("T9", "include")])
        # 健康正控：全部现存票键正常返回、无票键 absent。
        probe = self._kv_open_borrowed(db)
        try:
            snap = self.ledger.vote_snapshot_keys_on(
                probe, STAGE1, ("A1", "K2", "T9", "NEVER-VOTED"))
            self._kv_snapshot_domain_assert(
                snap, expected_rows={"A1", "K2", "T9"},
                expected_absent={"NEVER-VOTED"}, expected_issues=set())
        finally:
            probe.close()
        # T9 删除票行 → 墓碑 absent；K2 守恒一致缺行 → row_missing。
        self._kv_apply_damage(
            db, [("DELETE FROM decisions WHERE zotero_key='T9'", ())])
        self._kv_compensated_row_missing(db, "K2")
        probe2 = self._kv_open_borrowed(db)
        try:
            snap2 = self.ledger.vote_snapshot_keys_on(
                probe2, STAGE1, ("A1", "K2", "T9", "NEVER-VOTED"))
        finally:
            probe2.close()
        self._kv_snapshot_domain_assert(
            snap2, expected_rows={"A1"},
            expected_absent={"T9", "NEVER-VOTED"},
            expected_issues={"K2"})
        self.assertEqual(snap2.absent_keys, ("T9", "NEVER-VOTED"),
                         TAG_KEYED_DOMAIN + ": absent 保持请求顺序")
        issue = snap2.issues["K2"]
        self.assertIsInstance(issue, self.ledger.KeyedVoteIssue,
                              TAG_KEYED_DOMAIN)
        self.assertEqual(issue.reason, "row_missing",
                         TAG_KEYED_DOMAIN + ": 有票缺 ledger 行=row_missing")
        self._kv_assert_issue_detail_clean(
            issue, b"", TAG_KEYED_DOMAIN + ": row_missing detail")
        # 其他键不受影响：A1 逐字段完好。
        self.assertEqual(snap2.rows["A1"], {
            "zotero_key": "A1", "decision": "include",
            "exclusion_reason": "", "notes": "n-A1", "tags": "",
            "screened_at": _KV_TS1, "updated_at": _KV_TS1,
            "revision": 1}, TAG_KEYED_DOMAIN)
        # 墓碑正证：revisions 仍含 T9；K2 缺行。旧强 API 同损伤同拒（一致）。
        plain = self._kv_open_plain(db)
        try:
            self.assertEqual(self.ledger.revisions(plain, STAGE1),
                             {"A1": 1, "T9": 2}, TAG_KEYED_DOMAIN)
            with self.assertRaises(self.ledger.VoteLedgerUnavailable,
                                   msg=TAG_KEYED_DOMAIN) as caught:
                self.ledger.vote_snapshot(plain, STAGE1)
            self.assertEqual(caught.exception.reason, "row_missing",
                             TAG_KEYED_DOMAIN + ": 旧 API 对同损伤同拒")
        finally:
            plain.close()

    def test_row_missing_priority_over_unreadable(self):
        db = self._kv_make_ledger_db(
            self.base / "priority.db",
            votes1=[_kv_vote_row("A1"), _kv_vote_row("K2")])
        raw = b"\x00\xff\xee"
        # 健康正控：K2 正常返回。
        probe = self._kv_open_borrowed(db)
        try:
            snap = self.ledger.vote_snapshot_keys_on(probe, STAGE1,
                                                     ("A1", "K2"))
            self._kv_snapshot_domain_assert(
                snap, expected_rows={"A1", "K2"}, expected_absent=set(),
                expected_issues=set())
        finally:
            probe.close()
        # 第一段损伤：K2 票面 blob 化 → unreadable（正控损伤真实生效）。
        self._kv_apply_damage(
            db, [("UPDATE decisions SET notes=? WHERE zotero_key='K2'",
                  (raw,))], fk=True)
        probe2 = self._kv_open_borrowed(db)
        try:
            snap2 = self.ledger.vote_snapshot_keys_on(probe2, STAGE1,
                                                      ("A1", "K2"))
        finally:
            probe2.close()
        self._kv_snapshot_domain_assert(
            snap2, expected_rows={"A1"}, expected_absent=set(),
            expected_issues={"K2"})
        self.assertEqual(snap2.issues["K2"].reason, "unreadable",
                         TAG_KEYED_DOMAIN)
        self._kv_assert_issue_detail_clean(
            snap2.issues["K2"], raw, TAG_KEYED_DOMAIN + ": unreadable detail")
        # 第二段损伤：再叠加守恒一致缺行 → row_missing 必须优先于 unreadable。
        self._kv_compensated_row_missing(db, "K2")
        probe3 = self._kv_open_borrowed(db)
        try:
            snap3 = self.ledger.vote_snapshot_keys_on(probe3, STAGE1,
                                                      ("A1", "K2"))
        finally:
            probe3.close()
        self._kv_snapshot_domain_assert(
            snap3, expected_rows={"A1"}, expected_absent=set(),
            expected_issues={"K2"})
        self.assertEqual(
            snap3.issues["K2"].reason, "row_missing",
            TAG_KEYED_DOMAIN
            + ": 票面不可读与缺 ledger 行并存时 row_missing 优先")
        # 其他键不受影响。
        self.assertEqual(snap3.rows["A1"]["zotero_key"], "A1",
                         TAG_KEYED_DOMAIN)
        self.assertEqual(snap3.rows["A1"]["revision"], 1, TAG_KEYED_DOMAIN)

    def test_unreadable_storage_type_and_invalid_utf8(self):
        raw_notes = b"\x00\xff\xee"
        raw_decision = b"\x01\x02"
        raw_exclusion = b"\xde\xad\xbe\xef"
        raw_badtext = b"\xff\x8c\xbaD"
        db = self._kv_make_ledger_db(
            self.base / "unreadable.db",
            votes1=[_kv_vote_row("GOOD1", "include", notes="n1"),
                    _kv_vote_row("GOOD2", "exclude"),
                    _kv_vote_row("UB"), _kv_vote_row("UD"),
                    _kv_vote_row("UI"), _kv_vote_row("UT"),
                    _kv_vote_row("UK")],
            corrupt_rows=[
                # blob 存储类（TEXT 亲和列保 blob）：notes/decision/exclusion。
                ("UPDATE decisions SET notes=? WHERE zotero_key='UB'",
                 (raw_notes,)),
                ("UPDATE decisions SET decision=? WHERE zotero_key='UD'",
                 (raw_decision,)),
                ("UPDATE decisions SET exclusion_reason=?"
                 " WHERE zotero_key='UI'", (raw_exclusion,)),
                # typeof='text' 但字节非法 UTF-8（CAST 不校验编码）。
                ("UPDATE decisions SET notes=CAST(? AS TEXT)"
                 " WHERE zotero_key='UT'", (raw_badtext,)),
            ])
        # 键列 blob 化（改键须 FK OFF；触发器随 UPDATE 推进，守恒保持）：
        # 文本请求键不再等值命中该行 → absent（如实钉住可达行为）。
        self._kv_apply_damage(
            db, [("UPDATE decisions SET zotero_key=? WHERE zotero_key=?",
                  (b"\x80\x81", "UK"))], fk=False)
        probe = self._kv_open_borrowed(db)
        try:
            snap = self.ledger.vote_snapshot_keys_on(
                probe, STAGE1,
                ("GOOD1", "UB", "UD", "UI", "UT", "UK", "GOOD2"))
        finally:
            probe.close()
        self._kv_snapshot_domain_assert(
            snap, expected_rows={"GOOD1", "GOOD2"}, expected_absent={"UK"},
            expected_issues={"UB", "UD", "UI", "UT"})
        expected_raw = {"UB": raw_notes, "UD": raw_decision,
                        "UI": raw_exclusion, "UT": raw_badtext}
        for key, raw_bytes in expected_raw.items():
            issue = snap.issues[key]
            self.assertIsInstance(issue, self.ledger.KeyedVoteIssue,
                                  TAG_KEYED_DOMAIN + ": " + key)
            self.assertEqual(issue.reason, "unreadable",
                             TAG_KEYED_DOMAIN + ": " + key)
            self._kv_assert_issue_detail_clean(
                issue, raw_bytes, TAG_KEYED_DOMAIN + ": detail " + key)
        # 其他键不受影响：健康键逐字段完好、revision 为 int>=1。
        self.assertEqual(snap.rows["GOOD1"]["notes"], "n1",
                         TAG_KEYED_DOMAIN)
        self.assertEqual(snap.rows["GOOD1"]["decision"], "include",
                         TAG_KEYED_DOMAIN)
        for key in ("GOOD1", "GOOD2"):
            self.assertIsInstance(snap.rows[key]["revision"], int,
                                  TAG_KEYED_DOMAIN)
            self.assertGreaterEqual(snap.rows[key]["revision"], 1,
                                    TAG_KEYED_DOMAIN)

    def test_unrequested_bad_text_is_not_fetched(self):
        raw = b"\xbe\xef"
        db = self._kv_make_ledger_db(
            self.base / "isolation.db",
            votes1=[_kv_vote_row("A1"), _kv_vote_row("B1"),
                    _kv_vote_row("UX1", notes="will-corrupt")],
            corrupt_rows=[
                ("UPDATE decisions SET notes=? WHERE zotero_key='UX1'",
                 (raw,))])
        probe = self._kv_open_borrowed(db)
        try:
            self._kv_reset_probe(probe)
            snap = self.ledger.vote_snapshot_keys_on(probe, STAGE1,
                                                     ("A1", "B1"))
            self._kv_snapshot_domain_assert(
                snap, expected_rows={"A1", "B1"}, expected_absent=set(),
                expected_issues=set())
            self.assertNotIn("UX1", snap.rows, TAG_KEYED_DOMAIN)
            self.assertNotIn("UX1", snap.absent_keys, TAG_KEYED_DOMAIN)
            self.assertNotIn("UX1", snap.issues, TAG_KEYED_DOMAIN)
            # 语句面：窗口内仅 SELECT（无 txn/DML/DDL/设置性 PRAGMA）。
            buckets = _kv_classify_statements(
                probe.statements[probe.statement_offset:])
            self.assertEqual(buckets["txn"], [], TAG_CONTRACT)
            self.assertEqual(buckets["dml"], [], TAG_CONTRACT)
            self.assertEqual(buckets["ddl"], [], TAG_CONTRACT)
            self.assertEqual(buckets["other_pragma"], [], TAG_CONTRACT)
            self.assertEqual(buckets["other"], [], TAG_CONTRACT)
            for statement in probe.statements[probe.statement_offset:]:
                self.assertNotIn(
                    "UX1", statement,
                    TAG_KEYED_DOMAIN + ": 未请求键不得出现在任何语句中")
            # cursor 面：票表读取恰为请求键数、全部绑定请求域键、完整物化。
            window = probe.cursors[probe.cursor_offset:]
            count, violations = _kv_decisions_reads_bounded(window,
                                                            ("A1", "B1"))
            self.assertEqual(violations, [],
                             TAG_KEYED_DOMAIN + ": 未按键窄读: %r"
                             % (violations,))
            self.assertEqual(count, 2,
                             TAG_KEYED_DOMAIN + ": 票表读取次数==请求键数")
            for cursor in window:
                self.assertTrue(
                    cursor.fetchall_called or cursor.saw_empty
                    or cursor.fetchone_called,
                    TAG_CONTRACT + ": cursor 必须完整物化")
                if "d.zotero_key" in cursor.sql:
                    self.assertTrue(cursor.closed,
                                    TAG_CONTRACT + ": 按键 cursor 必须确定关闭")
        finally:
            probe.close()
        # 成对正控：同一库显式请求 UX1 → 真实得到 unreadable issue
        # （证明损坏存在，隔离来自按键窄读而非数据不存在）。
        probe2 = self._kv_open_borrowed(db)
        try:
            snap2 = self.ledger.vote_snapshot_keys_on(probe2, STAGE1,
                                                      ("A1", "UX1"))
        finally:
            probe2.close()
        self._kv_snapshot_domain_assert(
            snap2, expected_rows={"A1"}, expected_absent=set(),
            expected_issues={"UX1"})
        self.assertEqual(snap2.issues["UX1"].reason, "unreadable",
                         TAG_KEYED_DOMAIN)
        self._kv_assert_issue_detail_clean(
            snap2.issues["UX1"], raw, TAG_KEYED_DOMAIN + ": UX1 detail")

    def test_stage2_routing_and_cross_stage_isolation(self):
        db = self._kv_make_ledger_db(
            self.base / "stages.db",
            votes1=[_kv_vote_row("S1A", "include")],
            votes2=[_kv_vote_row("S2A", "exclude", notes="n-s2")])
        probe = self._kv_open_borrowed(db)
        try:
            snap2 = self.ledger.vote_snapshot_keys_on(probe, STAGE2,
                                                      ("S2A", "S1A"))
            snap1 = self.ledger.vote_snapshot_keys_on(probe, STAGE1,
                                                      ("S2A", "S1A"))
        finally:
            probe.close()
        self.assertEqual(snap2.stage, STAGE2, TAG_KEYED_DOMAIN)
        self._kv_snapshot_domain_assert(
            snap2, expected_rows={"S2A"}, expected_absent={"S1A"},
            expected_issues=set())
        self.assertEqual(snap2.rows["S2A"]["decision"], "exclude",
                         TAG_KEYED_DOMAIN + ": stage2 路由到 stage2_decisions")
        self.assertEqual(snap2.rows["S2A"]["notes"], "n-s2",
                         TAG_KEYED_DOMAIN)
        self.assertEqual(snap1.stage, STAGE1, TAG_KEYED_DOMAIN)
        self._kv_snapshot_domain_assert(
            snap1, expected_rows={"S1A"}, expected_absent={"S2A"},
            expected_issues=set())
        self.assertEqual(snap1.library_id, snap2.library_id,
                         TAG_KEYED_DOMAIN + ": 同库身份跨 stage 一致")
        with closing(self._kv_open_plain(db)) as plain:
            self.assertEqual(self.ledger.revisions(plain, STAGE2),
                             {"S2A": 1}, TAG_KEYED_DOMAIN)


class GlobalDamageBlocksLiveTests(KeyedVoteLedgerTestBase):
    """全局损伤（counter/触发器/账行/meta）→ keyed typed 失败，不返回伪空 DTO。"""

    def test_global_counter_damage_still_blocks_live(self):
        variants = (
            ("advances-inflated",
             [("UPDATE team_vote_revision_meta SET advances=advances+1",
               ())], "conservation_mismatch"),
            ("trigger-dropped",
             [("DROP TRIGGER decisions_tvr_insert", ())], "trigger_missing"),
            ("revisions-row-deleted",
             [("DELETE FROM team_vote_revisions WHERE stage=? AND item_key=?",
               (STAGE1, "A2"))], "conservation_mismatch"),
        )
        for label, damage, keyed_reason in variants:
            with self.subTest(variant=label):
                db = self._kv_make_ledger_db(
                    self.base / ("live-%s.db" % label),
                    votes1=[_kv_vote_row("A1"), _kv_vote_row("A2")])
                probe = self._kv_open_borrowed(db)
                try:
                    healthy = self.ledger.library_identity_header_on(probe)
                    snap = self.ledger.vote_snapshot_keys_on(
                        probe, STAGE1, ("A1", "A2"))
                    self.assertEqual(set(snap.rows), {"A1", "A2"},
                                     TAG_GLOBAL + ": 损伤前健康正控")
                finally:
                    probe.close()
                self._kv_apply_damage(db, damage)
                probe2 = self._kv_open_borrowed(db)
                try:
                    after = self.ledger.library_identity_header_on(probe2)
                    self.assertEqual(
                        after, healthy,
                        TAG_GLOBAL + ": " + label
                        + " 不得影响身份头（meta 未动的损伤面）")
                    with self.assertRaises(
                            self.ledger.VoteLedgerUnavailable,
                            msg=TAG_GLOBAL) as caught:
                        self.ledger.vote_snapshot_keys_on(probe2, STAGE1,
                                                          ("A1",))
                    self.assertIs(type(caught.exception),
                                  self.ledger.VoteLedgerUnavailable,
                                  TAG_GLOBAL + ": typed 精确类型")
                    self.assertEqual(caught.exception.reason, keyed_reason,
                                     TAG_GLOBAL + ": " + label)
                finally:
                    probe2.close()
                with closing(self._kv_open_plain(db)) as plain:
                    self.assertEqual(self.ledger.ledger_state(plain),
                                     "broken", TAG_GLOBAL + ": " + label)

    def test_damage_matrix_typed_failures_without_dto(self):
        weak_ddl = ("CREATE TABLE team_vote_revision_meta("
                    "singleton INTEGER PRIMARY KEY, format_version INTEGER, "
                    "library_id TEXT)")
        variants = (
            ("revisions-emptied",
             [("DELETE FROM team_vote_revisions", ())],
             "conservation_mismatch"),
            ("meta-rows-emptied",
             [("DELETE FROM team_vote_revision_meta", ())],
             "meta_corrupt"),
            ("meta-weak-unsupported-format",
             [("DROP TABLE team_vote_revision_meta", ()),
              (weak_ddl, ()),
              ("INSERT INTO team_vote_revision_meta"
               "(singleton,format_version,library_id) VALUES(1, ?, 'x')",
               (self.ledger.LEDGER_FORMAT_VERSION + 1,))],
             "meta_corrupt"),
        )
        for label, damage, keyed_reason in variants:
            with self.subTest(variant=label):
                db = self._kv_make_ledger_db(
                    self.base / ("matrix-%s.db" % label),
                    votes1=[_kv_vote_row("A1")])
                self._kv_apply_damage(db, damage)
                probe = self._kv_open_borrowed(db)
                try:
                    with self.assertRaises(
                            self.ledger.VoteLedgerUnavailable,
                            msg=TAG_GLOBAL + ": " + label) as caught:
                        self.ledger.vote_snapshot_keys_on(probe, STAGE1,
                                                          ("A1",))
                    exc = caught.exception
                    self.assertIs(type(exc), self.ledger.VoteLedgerUnavailable,
                                  TAG_GLOBAL + ": typed 精确类型")
                    self.assertEqual(exc.reason, keyed_reason,
                                     TAG_GLOBAL + ": " + label)
                    self.assertIn(exc.reason, self.ledger.REASONS,
                                  TAG_GLOBAL + ": reason 合同域")
                finally:
                    probe.close()
                # 旧强 API 对同损伤同样 typed 拒绝（新旧一致 fail-closed；
                # 账行删除的旧归因域按 S1a 先例两 reason 皆合同内）。
                with closing(self._kv_open_plain(db)) as plain:
                    self.assertEqual(self.ledger.ledger_state(plain), "broken",
                                     TAG_GLOBAL + ": " + label)
                    with self.assertRaises(self.ledger.VoteLedgerUnavailable,
                                           msg=TAG_GLOBAL) as caught2:
                        self.ledger.vote_snapshot(plain, STAGE1)
                    if label == "revisions-emptied":
                        self.assertIn(caught2.exception.reason,
                                      REASONS_LEGACY_DELETE, TAG_GLOBAL)
                    else:
                        self.assertEqual(caught2.exception.reason,
                                         keyed_reason, TAG_GLOBAL)


class BorrowedConnectionContractTests(KeyedVoteLedgerTestBase):
    """C02 借用合同：native/Row/活动事务、事务与工厂不可变、closed 透传。"""

    def test_no_transaction_or_factory_mutation(self):
        db = self._kv_make_ledger_db(
            self.base / "borrowed-ok.db", votes1=[_kv_vote_row("A1")])
        probe = self._kv_open_borrowed(db)
        try:
            self._kv_reset_probe(probe)
            self.assertTrue(probe.in_transaction, TAG_CONTRACT)
            header = self.ledger.library_identity_header_on(probe)
            self.assertRegex(header.library_id, _KV_HEX32, TAG_CONTRACT)
            snap = self.ledger.vote_snapshot_keys_on(probe, STAGE1, ("A1",))
            self.assertEqual(set(snap.rows), {"A1"}, TAG_CONTRACT)
            # 事务/工厂/连接所有权前后不变。
            self.assertTrue(probe.in_transaction,
                            TAG_CONTRACT + ": 不得结束调用方事务")
            self.assertIs(probe.row_factory, sqlite3.Row,
                          TAG_CONTRACT + ": 不得改 row_factory")
            self.assertEqual(probe.commit_calls, 0, TAG_CONTRACT)
            self.assertEqual(probe.rollback_calls, 0, TAG_CONTRACT)
            self.assertEqual(probe.close_calls, 0,
                             TAG_CONTRACT + ": 不得 close 调用方连接")
            # 语句面：无事务控制/DML/DDL/设置性 PRAGMA/其他语句。
            buckets = _kv_classify_statements(
                probe.statements[probe.statement_offset:])
            self.assertEqual(buckets["txn"], [], TAG_CONTRACT)
            self.assertEqual(buckets["dml"], [], TAG_CONTRACT)
            self.assertEqual(buckets["ddl"], [], TAG_CONTRACT)
            self.assertEqual(buckets["other_pragma"], [],
                             TAG_CONTRACT + ": 不得改 PRAGMA")
            self.assertEqual(buckets["other"], [], TAG_CONTRACT)
            # cursor 面：全部完整物化；按键 cursor 确定关闭（候选对验证链
            # 内部 cursor 未显式 close——见文件 docstring 诚实记录，不钉）。
            window = probe.cursors[probe.cursor_offset:]
            self.assertTrue(window, TAG_CONTRACT + ": 观测窗口非空")
            per_key = [c for c in window if "d.zotero_key" in c.sql]
            self.assertEqual(len(per_key), 1, TAG_CONTRACT)
            for cursor in window:
                self.assertTrue(
                    cursor.fetchall_called or cursor.saw_empty
                    or cursor.fetchone_called,
                    TAG_CONTRACT + ": cursor 必须完整物化")
            for cursor in per_key:
                self.assertTrue(cursor.closed,
                                TAG_CONTRACT + ": 按键 cursor 必须确定关闭")
            # 调用方仍可继续使用连接（所有权未被动）。
            with closing(probe.execute("SELECT 1")) as probe_select:
                self.assertEqual(probe_select.fetchone()[0], 1, TAG_CONTRACT)
            probe.rollback()
            self.assertFalse(probe.in_transaction, TAG_CONTRACT)
        finally:
            probe.close()

    def test_connection_contract_negative_paths(self):
        db = self._kv_make_ledger_db(
            self.base / "borrowed-neg.db", votes1=[_kv_vote_row("A1")])
        for api in ("library_identity_header_on", "vote_snapshot_keys_on"):
            with self.subTest(api=api):
                # 1) 非 native 对象 → TypeError。
                with self.assertRaises(TypeError, msg=TAG_CONTRACT):
                    if api == "library_identity_header_on":
                        self.ledger.library_identity_header_on(object())
                    else:
                        self.ledger.vote_snapshot_keys_on(
                            object(), STAGE1, ("A1",))
                # 2) __class__ 伪装代理（转发 Row/active 属性）→ TypeError。
                real = sqlite3.connect(str(db), timeout=15)
                self.addCleanup(real.close)
                real.row_factory = sqlite3.Row
                real.execute("BEGIN")
                proxy = _kv_DelegatingConnection(real)
                self.assertTrue(isinstance(proxy, sqlite3.Connection),
                                TAG_CONTRACT + ": 代理伪装前置自证")
                self.assertFalse(issubclass(type(proxy), sqlite3.Connection),
                                 TAG_CONTRACT + ": 代理伪装前置自证")
                self.assertIs(proxy.row_factory, sqlite3.Row,
                              TAG_CONTRACT + ": 代理伪装前置自证")
                self.assertTrue(proxy.in_transaction,
                                TAG_CONTRACT + ": 代理伪装前置自证")
                with self.assertRaises(TypeError, msg=TAG_CONTRACT):
                    if api == "library_identity_header_on":
                        self.ledger.library_identity_header_on(proxy)
                    else:
                        self.ledger.vote_snapshot_keys_on(
                            proxy, STAGE1, ("A1",))
                real.rollback()
                # 3) native 活动事务但 row_factory 非 Row → RuntimeError。
                plain_conn = sqlite3.connect(str(db), timeout=15)
                self.addCleanup(plain_conn.close)
                plain_conn.execute("BEGIN IMMEDIATE")
                with self.assertRaises(RuntimeError, msg=TAG_CONTRACT):
                    if api == "library_identity_header_on":
                        self.ledger.library_identity_header_on(plain_conn)
                    else:
                        self.ledger.vote_snapshot_keys_on(
                            plain_conn, STAGE1, ("A1",))
                self.assertTrue(plain_conn.in_transaction, TAG_CONTRACT)
                plain_conn.rollback()
                # 4) native+Row 无活动事务 → RuntimeError，且未偷 BEGIN。
                inactive = self._kv_open_borrowed(db, begin=False)
                self._kv_reset_probe(inactive)
                self.assertFalse(inactive.in_transaction, TAG_CONTRACT)
                with self.assertRaises(RuntimeError, msg=TAG_CONTRACT):
                    if api == "library_identity_header_on":
                        self.ledger.library_identity_header_on(inactive)
                    else:
                        self.ledger.vote_snapshot_keys_on(
                            inactive, STAGE1, ("A1",))
                self.assertFalse(
                    inactive.in_transaction,
                    TAG_CONTRACT + ": 不得偷开事务")
                self.assertEqual(len(inactive.statements), 0,
                                 TAG_CONTRACT + ": 未发任何语句")
                inactive.close()
                # 5) closed native → sqlite3.ProgrammingError 原样透传。
                closed = sqlite3.connect(str(db), timeout=15)
                closed.row_factory = sqlite3.Row
                closed.execute("BEGIN")
                closed.close()
                with self.assertRaises(sqlite3.ProgrammingError,
                                       msg=TAG_CONTRACT) as caught:
                    if api == "library_identity_header_on":
                        self.ledger.library_identity_header_on(closed)
                    else:
                        self.ledger.vote_snapshot_keys_on(
                            closed, STAGE1, ("A1",))
                self.assertIs(type(caught.exception),
                              sqlite3.ProgrammingError,
                              TAG_CONTRACT + ": closed 精确类型透传")
        # 成对正控：native 子类探针（Row+活动事务）两 API 均成功。
        probe = self._kv_open_borrowed(db)
        try:
            header = self.ledger.library_identity_header_on(probe)
            self.assertRegex(header.library_id, _KV_HEX32, TAG_CONTRACT)
            snap = self.ledger.vote_snapshot_keys_on(probe, STAGE1, ("A1",))
            self.assertEqual(set(snap.rows), {"A1"}, TAG_CONTRACT)
        finally:
            probe.close()


class NormalizeKeySemanticsTests(KeyedVoteLedgerTestBase):
    """P00 normalize 语义镜像：去重保首现；各类拒绝均 ValueError。"""

    def test_key_dedupe_preserves_first_seen_order(self):
        db = self._kv_make_ledger_db(
            self.base / "dedupe.db",
            votes1=[_kv_vote_row("A1"), _kv_vote_row("A2")])
        probe = self._kv_open_borrowed(db)
        try:
            snap = self.ledger.vote_snapshot_keys_on(
                probe, STAGE1, ("A2", "A1", "A2", "A1"))
            self.assertEqual(snap.requested_keys, ("A2", "A1"),
                             TAG_NORMALIZE + ": 去重保首现顺序")
            self._kv_snapshot_domain_assert(
                snap, expected_rows={"A1", "A2"}, expected_absent=set(),
                expected_issues=set())
            # 生成器输入（Iterable 合同）单遍消费、顺序保持（匿名 genexp：
            # 本文件不引入未过 impact 的嵌套 def 符号）。
            snap2 = self.ledger.vote_snapshot_keys_on(
                probe, STAGE1, (k for k in ("A1", "A3", "A1")))
            self.assertEqual(snap2.requested_keys, ("A1", "A3"),
                             TAG_NORMALIZE)
            self._kv_snapshot_domain_assert(
                snap2, expected_rows={"A1"}, expected_absent={"A3"},
                expected_issues=set())
        finally:
            probe.close()

    def test_rejects_bare_str_bytes_and_non_iterable(self):
        db = self._kv_make_ledger_db(
            self.base / "reject-bare.db", votes1=[_kv_vote_row("A1")])
        probe = self._kv_open_borrowed(db)
        try:
            for bad in ("stage1", b"stage1", 42, None, 3.5):
                with self.subTest(bad=repr(bad)):
                    with self.assertRaises(ValueError, msg=TAG_NORMALIZE):
                        self.ledger.vote_snapshot_keys_on(probe, STAGE1, bad)
            # 成对正控：合法列表输入成功（同连接可继续用）。
            snap = self.ledger.vote_snapshot_keys_on(probe, STAGE1, ["A1"])
            self.assertEqual(set(snap.rows), {"A1"}, TAG_NORMALIZE)
        finally:
            probe.close()

    def test_rejects_empty_collection_and_empty_string(self):
        db = self._kv_make_ledger_db(
            self.base / "reject-empty.db", votes1=[_kv_vote_row("A1")])
        probe = self._kv_open_borrowed(db)
        try:
            for bad in ((), [], ("",), ("A1", "")):
                with self.subTest(bad=repr(bad)):
                    with self.assertRaises(ValueError, msg=TAG_NORMALIZE):
                        self.ledger.vote_snapshot_keys_on(probe, STAGE1, bad)
            snap = self.ledger.vote_snapshot_keys_on(probe, STAGE1, ("A1",))
            self.assertEqual(set(snap.rows), {"A1"}, TAG_NORMALIZE)
        finally:
            probe.close()

    def test_rejects_nul_non_str_and_surrogate(self):
        db = self._kv_make_ledger_db(
            self.base / "reject-nul.db", votes1=[_kv_vote_row("A1")])
        probe = self._kv_open_borrowed(db)
        try:
            for bad in (("A\x00B",), (b"A1",), (1,), ("A1", 2),
                        ("A\ud800",), ("\udcff",)):
                with self.subTest(bad=repr(bad)):
                    with self.assertRaises(ValueError, msg=TAG_NORMALIZE):
                        self.ledger.vote_snapshot_keys_on(probe, STAGE1, bad)
            snap = self.ledger.vote_snapshot_keys_on(probe, STAGE1, ("A1",))
            self.assertEqual(set(snap.rows), {"A1"}, TAG_NORMALIZE)
        finally:
            probe.close()

    def test_stage_must_be_stage1_or_stage2(self):
        db = self._kv_make_ledger_db(
            self.base / "stage.db", votes1=[_kv_vote_row("A1")])
        probe = self._kv_open_borrowed(db)
        try:
            # C02：业务阶段 screen/fulltext 由适配层映射；coding 等不得直送
            # 票面 API（含大小写变体/空串/None）。
            for bad_stage in ("coding", "STAGE1", "", "stage3", None):
                with self.subTest(stage=repr(bad_stage)):
                    with self.assertRaises(ValueError, msg=TAG_NORMALIZE):
                        self.ledger.vote_snapshot_keys_on(
                            probe, bad_stage, ("A1",))
            # 成对正控：两合法 stage 均成功。
            snap1 = self.ledger.vote_snapshot_keys_on(probe, STAGE1, ("A1",))
            self.assertEqual(snap1.stage, STAGE1, TAG_NORMALIZE)
            snap2 = self.ledger.vote_snapshot_keys_on(probe, STAGE2, ("A1",))
            self.assertEqual(snap2.stage, STAGE2, TAG_NORMALIZE)
            self._kv_snapshot_domain_assert(
                snap2, expected_rows=set(), expected_absent={"A1"},
                expected_issues=set())
        finally:
            probe.close()


class LegacyStrongApiControlsTests(KeyedVoteLedgerTestBase):
    """P04：旧 vote_snapshot/revisions/ledger_state/library_id 正控保持。"""

    def test_legacy_strong_apis_healthy_positive_control(self):
        db = self._kv_make_ledger_db(
            self.base / "legacy-ok.db",
            votes1=[_kv_vote_row("A1", "include"),
                    _kv_vote_row("A2", "exclude", notes="n-A2")],
            votes2=[_kv_vote_row("S2A", "exclude")])
        plain = self._kv_open_plain(db)
        try:
            self.assertEqual(self.ledger.ledger_state(plain), "installed",
                             TAG_LEGACY)
            lib = self.ledger.library_id(plain)
            self.assertRegex(lib, _KV_HEX32, TAG_LEGACY)
            self.assertEqual(self.ledger.revisions(plain, STAGE1),
                             {"A1": 1, "A2": 1}, TAG_LEGACY)
            self.assertEqual(self.ledger.revisions(plain, STAGE2),
                             {"S2A": 1}, TAG_LEGACY)
            lib2, mapping = self.ledger.vote_snapshot(plain, STAGE1)
            self.assertEqual(lib2, lib, TAG_LEGACY)
            self.assertEqual(set(mapping), {"A1", "A2"}, TAG_LEGACY)
            for key, row in mapping.items():
                self.assertEqual(set(row), _KV_ROW_FIELDS,
                                 TAG_LEGACY + ": 行字典字段集 " + key)
            self.assertEqual(mapping["A2"]["notes"], "n-A2", TAG_LEGACY)
        finally:
            plain.close()
        # 与新窄读接口一致：同一身份与票面。
        probe = self._kv_open_borrowed(db)
        try:
            header = self.ledger.library_identity_header_on(probe)
            self.assertEqual(header.library_id, lib, TAG_LEGACY)
            snap = self.ledger.vote_snapshot_keys_on(probe, STAGE1,
                                                     ("A1", "A2"))
        finally:
            probe.close()
        for key in ("A1", "A2"):
            self.assertEqual(snap.rows[key], mapping[key],
                             TAG_LEGACY + ": 新旧票面逐字段一致 " + key)

    def test_legacy_strong_apis_fail_closed_after_damage(self):
        db = self._kv_make_ledger_db(
            self.base / "legacy-broken.db", votes1=[_kv_vote_row("A1")])
        self._kv_apply_damage(
            db, [("DROP TRIGGER decisions_tvr_insert", ())])
        plain = self._kv_open_plain(db)
        try:
            self.assertEqual(self.ledger.ledger_state(plain), "broken",
                             TAG_LEGACY)
            with self.assertRaises(self.ledger.VoteLedgerUnavailable,
                                   msg=TAG_LEGACY) as caught:
                self.ledger.library_id(plain)
            self.assertEqual(caught.exception.reason, "trigger_missing",
                             TAG_LEGACY)
            with self.assertRaises(self.ledger.VoteLedgerUnavailable,
                                   msg=TAG_LEGACY) as caught2:
                self.ledger.revisions(plain, STAGE1)
            self.assertEqual(caught2.exception.reason, "trigger_missing",
                             TAG_LEGACY)
            with self.assertRaises(self.ledger.VoteLedgerUnavailable,
                                   msg=TAG_LEGACY) as caught3:
                self.ledger.vote_snapshot(plain, STAGE1)
            self.assertEqual(caught3.exception.reason, "trigger_missing",
                             TAG_LEGACY)
            # broken 不自动补齐：幂等 install 同样 typed 拒绝。
            with self.assertRaises(self.ledger.VoteLedgerUnavailable,
                                   msg=TAG_LEGACY) as caught4:
                self.ledger.install(plain)
            self.assertEqual(caught4.exception.reason, "trigger_missing",
                             TAG_LEGACY)
        finally:
            plain.close()
        # 不自修：触发器仍缺失（裸连接直查）。
        with closing(sqlite3.connect(str(db), timeout=15)) as raw:
            self.assertIsNone(
                raw.execute("SELECT 1 FROM sqlite_master"
                            " WHERE name='decisions_tvr_insert'").fetchone(),
                TAG_LEGACY + ": 不得静默重建触发器")
