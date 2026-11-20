# -*- coding: utf-8 -*-
"""P05 仲裁按键证据读取（keyed attestations）独立测试候选 —— 静态撰写，作者未运行。

目标产品候选（本文件唯一针对的实现）：
- E:/rf216-scratch/p05-attestations-20261118T123002Z-1592/candidate/screening_arbitration.py
- SHA-256 07e09d29704bf3b32469b44534f6b2566ae8fd2c39c6e7bb8121e3fec1be6efd
  （基线 a79b670b 纯追加 372 行：KeyedAttestations、read_attestations_for_keys_on
  及 8 个私有 helper；S1b/S1c 与 generic 旧 API 原样保留）
- 主仓基线（coscreen/screening_arbitration.py）SHA-256
  a79b670b330ed3e76a264dcf3aceef42e5d155affe748908b9305305216e4702 —— 不含
  P05 新接口；对本基线运行本文件时新接口用例将 AttributeError（诚实红，
  同 P02/P03/P04 测试箱协议：不以 ImportError 冒充行为红证）。
- 主仓 coscreen/team_flow_types.py SHA-256
  3dbca0b7d0d1020931989a6379aa628aa68937b3c25cb96b758dd5edffbcaa73
  （normalize_item_keys 已归位，候选函数内 import 在当前主仓状态直接可用）。

合同依据（全部只读引用，本文不编辑任何合同/产品文件）：
- docs/superpowers/plans/2026-11-16-2.1.6-team-work-packages.md（P05 节：文件名、
  五个具名用例、SQL header→records membership→命中键 proof/时间、按记录 ID 分页
  /BLOB/Python 解码不用 json_each、generic append/latest/list 不改义）
- docs/superpowers/plans/2026-11-16-2.1.6-team-contracts.md（C02 键/连接/错误
  共同合同：normalize_item_keys 语义、每块至多 200 行、native 连接+活动事务+
  Row 工厂、closed 连接 ProgrammingError 透传、借用 API 零事务/零 PRAGMA/自建
  Cursor 完整物化确定关闭；C06 按键仲裁读：KeyedAttestations DTO、可信 header
  先筛、可证明 B-only 才跳、坏 JSON/缺 key/未知 membership 影响该 task/stage
  请求中仍需仲裁的键、不以 stage-global merge SHA 判 A）

用例 → 合同条款映射（包规格钉定的五个具名用例 + 三个支撑用例）：
- test_proven_b_only_bad_proof_does_not_block_a → C06「可证明 B-only 的坏 proof
  不进入 A 的 issues」（锚 assert 'A' not in result.issues）；proof_missing/
  proof_invalid/adjudication_row_invalid 三类单键局部损害只记该键；健康正控
  A/B 双命中；by_key 条目形状（attestation/record/双 ID/item_key/decision/
  rationale/proof）。
- test_unknown_membership_is_issue → C06 坏 records JSON/非列表/非 dict 条目/
  缺 zotero_key/重复键/空键/孤儿 attestation → membership 未知影响该请求全部
  仍需仲裁键；已获有效命中的键不再列入 issues；task/stage/round header SQL
  先筛隔离；无 attestation 表=合法空（KeyedAttestations 空）；表在而缺
  generic 表/缺列=ArbitrationAttestationStoreUnavailable（typed 503 类，
  不伪「无证据」）。
- test_targeted_bad_time_and_orphan → 命中键的非空+ISO 可解析 attested_at/
  created_at 校验；记录级损害只影响请求键∩记录键域（域内 agreed 键同样
  记 issue、域外键零影响）；孤儿与同请求健康记录并存时健康键保持命中；
  digest/required_names/stage_mismatch 同为记录级；同键多记录损害按 id
  升序取首因。含未冻结项记录性断言（date-only ISO 现状被接受）。
- test_no_json1_dependency → 语句面零 JSON1 函数（json_each/json_extract/
  json_quote/…与 json( ) 全族正则扫描，检测器带正控）；membership 行为等价
  于纯 Python json.loads 解码（\\u 转义键可命中、正文提及他键不产生假命中）；
  按记录 ID 分页每块至多 200 行（页 SELECT LIMIT 绑定值==200）且 >200 行
  完整读回、按 attestation id 升序。
- test_generic_latest_unchanged → 既有 generic append（幂等同 ID 同 created_at、
  改判新 ID+supersedes、不建 attestation 表）/latest（按 id 最新、缺文件
  None、坏 stage ValueError）/get（按 ID 往返、缺 ID None）零变化正控；S1b
  attested append+list_on+get_by_id 健康正控；新 keyed 读与 S1b list_on 同
  证一致、对 generic-only 存储返回合法空。
- test_no_transaction_or_factory_mutation（支撑）→ C02 借用纪律：窗口内仅
  SELECT+只读元数据 PRAGMA，零 txn/DML/DDL/设置性 PRAGMA，不结束调用方事务、
  不改 row_factory、不 commit/rollback/close；自建 Cursor 全部完整物化并确定
  关闭。
- test_connection_contract_negative_paths（支撑）→ C02 连接门负路：非 native
  对象、__class__ 伪装代理、活动事务但非 Row 工厂、native+Row 但无事务（且
  不偷 BEGIN、零语句）均 ArbitrationConnectionContractError（精确类型）；
  closed 连接 sqlite3.ProgrammingError 原样透传（精确类型）；成对正控。
- test_key_domain_normalization_semantics（支撑）→ P00/C02 键域语义经本 API
  边界：去重保首现（by_key/issues 键集反映）、生成器单遍、拒 bare str/
  bytes/非 str/空集合/空串/NUL/代理对，全部 ValueError；失败后连接仍可用。

诚实记录（契约未冻结项，只作记录性断言、不钉必红，docstring 就地标注）：
- date-only ISO 时间（如 '2026-11-18'）现状被 _keyed_attestation_time_ok 接受
  （datetime.fromisoformat 可解析）——未冻结项，见 TargetedTimeAndOrphanTests。
- rationale/arbitrator_role 读取侧不重验（写入侧两道闸负责）——未冻结项：
  本文件不把「坏 role 仍命中」钉成必红，仅在时间用例 docstring 注明。
- BLOB/非法 UTF-8 的 records JSON 现状走 json.loads 的 UnicodeDecodeError 分支
  记 records_unreadable（与坏 JSON 同桶）——与 C06「坏 JSON→未知 membership」
  同语义，未单列分桶。
- 环境记录：CPython 自带 sqlite ≥3.38 恒含 JSON1；NoJson1DependencyTests 用
  :memory: 连接探测 json_valid 可用后断言零使用，证明「不用 JSON1」是实现
  选择而非环境缺失。
- 本文件不调用产品私有 helper 伪造输出；所有夹具经真实公开路径
  （prepare→append_decision_arbitration_attested／merge_decision_packages）
  或对测试自有库的直接 SQL INSERT/DDL 损伤构造（触发器只禁 UPDATE/DELETE，
  损伤行全部以 INSERT 形态达成，除缺表/缺列 DDL 变体外零触发器手术）。

撰写协议（同 P02/P03/P04 先例）：本文件每个类/方法/函数符号在写入前均逐项
运行 GitNexus CLI upstream impact（--repo E:/mubi-reviewflow --file
build/tests/test_216_keyed_attestations.py --kind Method/Class/Function
--include-tests；新文件预期 not found/UNKNOWN——build/tests 不入图为已知边界，
raw/rc 存档于 E:/rf216-scratch/p05-attest-tests-20261118T150228Z-2247/impact/，
共 50 项；两次门控 busy 中止后续跑补齐，MANIFEST.tsv 为追加式真日志）。模块
级常量（非 def/class 符号）列入文件头清单：STAGE1/STAGE2/P05_*_SHA/
P05_CANDIDATE_SOURCE/TAG_*/_KA_TS1/_KA_TS2/_KA_LIBRARY_ID/_KA_LIBRARY_ID2/
_KA_DIGEST_RANGES/_KA_ENTRY_FIELDS/_KA_ATTESTATION_FIELDS/_KA_RECORD_FIELDS/
_KA_ISSUE_REASONS/_KA_HEX64_RE/_KA_JSON1_SQL_RE/_KA_MERGED_COLUMNS/
_KA_REQUIRED_NAMES_JSON/_KA_PROVENANCE_PRINTED 及 __main__ 卫语句。

未运行声明：作者仅静态撰写（ast.parse/compile 与符号清单自检），未运行任何
测试/SQL/产品/API，未查询真实库；执行留给独立 runner 在应用产品候选后进行。
禁主仓/前端/真实 data/旧证据箱写入；夹具全部位于测试自有 TemporaryDirectory。
自包含：不依赖 conftest/Safety 夹具、不触 HTTP 层；静态消费者仅 unittest。
"""

from __future__ import annotations

import hashlib
import importlib
import json
import re
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

STAGE1, STAGE2 = "stage1", "stage2"

#: 目标产品候选与主仓基线的 SHA-256（运行时打印观测值供核对，不做硬断言：
#: 候选后续修订经复审后 SHA 自然变化，硬断言会制造假红）。
P05_CANDIDATE_ARB_SHA = (
    "07e09d29704bf3b32469b44534f6b2566ae8fd2c39c6e7bb8121e3fec1be6efd")
P05_BASELINE_ARB_SHA = (
    "a79b670b330ed3e76a264dcf3aceef42e5d155affe748908b9305305216e4702")
P05_CANDIDATE_SOURCE = (
    "E:/rf216-scratch/p05-attestations-20261118T123002Z-1592/candidate/"
    "screening_arbitration.py")

#: 红侧失败输出定位标签（真实缺口按标签归组）。
TAG_KEYED = "P05-keyed by_key/issues domain"
TAG_B_ONLY = "P05-proven B-only isolation"
TAG_UNKNOWN = "P05-unknown membership scope"
TAG_TIME = "P05-targeted time/orphan checks"
TAG_JSON1 = "P05-no JSON1 python membership"
TAG_GENERIC = "P05-generic append/latest unchanged"
TAG_CONTRACT = "P05-C02 borrowed connection contract"
TAG_KEYS = "P05-item key normalization"
TAG_STORE = "P05-store precondition typed 503"

_KA_TS1 = "2026-11-18T08:00:00Z"
_KA_TS2 = "2026-11-18T09:30:00Z"

#: proof 成员库 ID（32 位小写 hex，镜像 S1b 测试先例的确定性取值）。
_KA_LIBRARY_ID = "1a" * 16
_KA_LIBRARY_ID2 = "2b" * 16

#: 直接 INSERT 夹具的确定性 64 位小写 hex 取值区间（避开 API 产出的真实哈希
#: 与彼此冲突；UNIQUE/CHECK(length=64) 均满足）。
_KA_DIGEST_RANGES = {
    "merge": 0x00FACE000000,
    "result": 0x00BEEF000000,
    "attest": 0x00C0FFEE0000,
    "resolutions": 0x00D1CE000000,
}

#: KeyedAttestations.by_key 条目/内嵌 attestation/record 的合同字段集
#: （C06 冻结返回形状；候选 docstring 明示）。
_KA_ENTRY_FIELDS = frozenset({
    "attestation", "record", "attestation_id", "record_id",
    "item_key", "decision", "rationale", "proof",
})
_KA_ATTESTATION_FIELDS = frozenset({
    "id", "record_id", "task_id", "round_id", "stage", "arbitrator_user_id",
    "arbitrator_role", "required_names", "input_proof", "resolutions_digest",
    "attest_sha256", "attested_at",
})
_KA_RECORD_FIELDS = frozenset({
    "id", "stage", "merge_sha256", "result_sha256", "supersedes_id",
    "arbitrator_id", "created_at", "records",
})

#: issues 取值域（候选冻结词表；新 reason 的加入属合同演进，本文件只断言
#: 观测到的取值 ∈ 该词表，并在各损伤变体断言精确 reason）。
_KA_ISSUE_REASONS = frozenset({
    "attestation_orphan", "records_unreadable", "stage_mismatch",
    "required_names_invalid", "attestation_digest_invalid",
    "attested_at_invalid", "created_at_invalid", "input_proof_invalid",
    "attestation_row_invalid", "record_row_invalid",
    "adjudication_row_invalid", "proof_missing", "proof_invalid",
})

_KA_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")

#: JSON1 SQL 函数族检测（语句面零使用断言 + 检测器正控；列名
#: required_names_json/records_json 等尾部 _json 不含 "json(" 或 "json_" 前缀
#: 记号，不误报）。
_KA_JSON1_SQL_RE = re.compile(r"\bjson_\w+\b|\bjson\s*\(", re.IGNORECASE)

#: 经 merge_decision_packages 构造的决策包列形状（S1b 测试同款）。
_KA_MERGED_COLUMNS = [
    "zotero_key", "decision", "exclusion_reason", "notes", "tags", "screened_at",
]

_KA_REQUIRED_NAMES_JSON = '["alice","bob"]'

_KA_PROVENANCE_PRINTED = False


def _ka_sha256(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _ka_row_spec(key, alice, bob):
    """一对筛选员决策行（同键异见即 conflict、同见即 agree；时间戳区分包）。

    返回 (alice_row, bob_row) 元组，列形状 _KA_MERGED_COLUMNS；与 S1b 测试
    先例一致经 merge_decision_packages 走真实合并路径。
    """
    alice_row = (key, alice, "", "", "", _KA_TS1)
    bob_row = (key, bob, "", "", "", _KA_TS2)
    return (alice_row, bob_row)


def _ka_merged_frame(sarb, row_pairs):
    """经产品 merge_decision_packages 构造合并表（真实 canonical 路径）。"""
    alice_rows = []
    bob_rows = []
    for alice_row, bob_row in row_pairs:
        alice_rows.append(alice_row)
        bob_rows.append(bob_row)
    frames = {
        "alice": pd.DataFrame(alice_rows, columns=_KA_MERGED_COLUMNS),
        "bob": pd.DataFrame(bob_rows, columns=_KA_MERGED_COLUMNS),
    }
    return sarb.merge_decision_packages(frames)


def _ka_proof(keys, *, revision=1):
    """input_proof 夹具：item_key→成员→{library_id: 32hex, revision}。"""
    proof = {}
    for key in keys:
        proof[key] = {
            "alice": {"library_id": _KA_LIBRARY_ID, "revision": revision},
            "bob": {"library_id": _KA_LIBRARY_ID2, "revision": revision},
        }
    return proof


def _ka_attestation_meta(proof, *, task_id="T1", round_id=None,
                         required=("alice", "bob"), user_id=7,
                         role="reconciler"):
    """append_decision_arbitration_attested 的 meta 夹具（冻结键集）。"""
    return {
        "task_id": task_id,
        "round": round_id,
        "required_names": list(required),
        "arbitrator_user_id": user_id,
        "arbitrator_role": role,
        "input_proof": proof,
    }


def _ka_first_keyword(sql_text):
    """剥前导 ``--`` 注释行后取首关键字/记号（P04 先例 _kv_first_keyword）。"""
    text = sql_text.strip()
    while text.startswith("--"):
        remainder = text.split("\n", 1)
        text = remainder[1].strip() if len(remainder) > 1 else ""
    if not text:
        return ""
    return re.split(r"[\s(]", text, 1)[0].upper()


def _ka_classify_statements(statements):
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
        keyword = _ka_first_keyword(statement)
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


class _ka_ProbeCursor:
    """Cursor 代理（P05 窄读观测）：登记 execute SQL/参数与 fetch/close 行为。

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


class _ka_ProbeConnection(sqlite3.Connection):
    """Native 子类探针：语句 trace 登记 + commit/rollback/close 计数 + cursor 登记。

    供"借用 API 不控制调用方事务/不 close 连接/只读语句面/自建 Cursor 确定
    关闭"断言；C02 的真实 type 继承判断接受本类（真实 native 子类，非
    __class__ 伪装）。健康夹具写入走普通连接（append 需 lastrowid 透传），
    探针只用于读取观测。
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
        self._ka_closed = False
        self.set_trace_callback(self.statements.append)

    def execute(self, sql, parameters=()):
        raw = super().execute(sql, parameters)
        cursor = _ka_ProbeCursor(raw, self.cursors)
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
        if self._ka_closed:
            return
        super().close()
        self._ka_closed = True
        self.close_calls += 1


class _ka_DelegatingConnection:
    """非 native 伪装代理：转发 row_factory/in_transaction 等属性。

    C02 要求以真实 type 继承判断，``__class__`` 伪装必须被拒绝。
    """

    def __init__(self, conn):
        self._conn = conn

    @property
    def __class__(self):
        return sqlite3.Connection

    def __getattr__(self, name):
        return getattr(self._conn, name)


class KeyedAttestationsTestBase(unittest.TestCase):
    """公共基座：临时箱 + 延迟产品导入 + 真实库 fixture/损伤构造 + 共用断言。"""

    maxDiff = None

    def setUp(self):
        global _KA_PROVENANCE_PRINTED
        self._tmp = tempfile.TemporaryDirectory(prefix="rf216-p05-ka-")
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        self.arb = importlib.import_module("coscreen.screening_arbitration")
        self._ka_craft_counter = 0
        if not _KA_PROVENANCE_PRINTED:
            _KA_PROVENANCE_PRINTED = True
            print("P05_TEST_FILE_SHA", _ka_sha256(__file__))
            print("P05_ARB_SOURCE_SHA", _ka_sha256(self.arb.__file__))
            print("P05_ARB_TARGET_CANDIDATE_SHA", P05_CANDIDATE_ARB_SHA)
            print("P05_ARB_BASELINE_SHA", P05_BASELINE_ARB_SHA)
            print("P05_PYTHON", sys.version.split()[0])
            print("P05_SQLITE_VERSION", sqlite3.sqlite_version)

    # ------------------------------------------------------------------
    # 连接工厂
    # ------------------------------------------------------------------

    def _ka_open_borrowed(self, path, *, begin=True):
        """C02 借用形态：native 子类探针 + Row 工厂 + 调用方活动事务。"""
        conn = sqlite3.connect(str(path), timeout=15,
                               factory=_ka_ProbeConnection)
        self.addCleanup(self._ka_close_probe, conn)
        conn.row_factory = sqlite3.Row
        if begin:
            with closing(conn.execute("BEGIN IMMEDIATE")):
                pass
        return conn

    def _ka_open_plain(self, path):
        """普通连接：夹具写入（append_attested 借用）/旧 path API/裸损伤。"""
        return sqlite3.connect(str(path), timeout=15)

    def _ka_close_probe(self, probe):
        if getattr(probe, "_ka_closed", False):
            return
        for cursor in list(getattr(probe, "cursors", ())):
            try:
                cursor.close()
            except sqlite3.Error:
                pass
        probe.close()

    def _ka_reset_probe(self, probe):
        """偏移复位：仅观察此后的语句/cursor/计数。"""
        probe.statement_offset = len(probe.statements)
        probe.cursor_offset = len(probe.cursors)
        probe.commit_calls = 0
        probe.rollback_calls = 0
        probe.close_calls = 0

    # ------------------------------------------------------------------
    # Fixture（真实 prepare/append 或直接 INSERT 损伤构造；零触发器手术）
    # ------------------------------------------------------------------

    def _ka_make_store(self, path, *, row_pairs, resolutions, proof,
                       task_id="T1", stage=STAGE1, round_id=None,
                       arbitrator_id="arb-1"):
        """prepare + 借用连接一次完整 attested append（真实公开写入路径）。

        返回 append_decision_arbitration_attested 的结果字典
        （record/attestation/record_id/attestation_id）。
        """
        self.arb.prepare_arbitration_attestation_storage(path)
        merged = _ka_merged_frame(self.arb, row_pairs)
        meta = _ka_attestation_meta(proof, task_id=task_id, round_id=round_id)
        conn = self._ka_open_plain(path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            result = self.arb.append_decision_arbitration_attested(
                conn, stage=stage, merged=merged, resolutions=resolutions,
                arbitrator_id=arbitrator_id, attestation_meta=meta)
            conn.commit()
        finally:
            conn.close()
        return result

    def _ka_insert_generic(self, path, *, records_json, stage=STAGE1,
                           created_at=_KA_TS1, merge_sha=None, result_sha=None,
                           arbitrator_id="crafter", supersedes_id=None):
        """直接 INSERT 一条 generic 记录（INSERT 不触发 append-only 触发器）。

        merge/result 摘要缺省取确定性计数器值（64 位小写 hex，满足 CHECK/
        UNIQUE）；返回新行 id。
        """
        if merge_sha is None:
            merge_sha = "%064x" % (
                _KA_DIGEST_RANGES["merge"] + self._ka_craft_counter)
        if result_sha is None:
            result_sha = "%064x" % (
                _KA_DIGEST_RANGES["result"] + self._ka_craft_counter)
        self._ka_craft_counter += 1
        conn = self._ka_open_plain(path)
        try:
            cursor = conn.execute(
                "INSERT INTO screening_arbitrations"
                "(stage,merge_sha256,result_sha256,supersedes_id,arbitrator_id,"
                "records_json,created_at) VALUES(?,?,?,?,?,?,?)",
                (stage, merge_sha, result_sha, supersedes_id, arbitrator_id,
                 records_json, created_at))
            new_id = int(cursor.lastrowid)
            conn.commit()
        finally:
            conn.close()
        return new_id

    def _ka_insert_attestation(self, path, *, record_id, input_proof_json,
                               task_id="T1", stage=STAGE1, round_id=None,
                               required_names_json=_KA_REQUIRED_NAMES_JSON,
                               resolutions_digest=None, attest_sha=None,
                               attested_at=_KA_TS2, arbitrator_user_id=7,
                               arbitrator_role="reconciler"):
        """直接 INSERT 一条 attestation（可选指向不存在 generic 行=孤儿）。

        双摘要缺省取确定性计数器值（满足 CHECK(length=64)/UNIQUE）；返回新行
        id。
        """
        if resolutions_digest is None:
            resolutions_digest = "%064x" % (
                _KA_DIGEST_RANGES["resolutions"] + self._ka_craft_counter)
        if attest_sha is None:
            attest_sha = "%064x" % (
                _KA_DIGEST_RANGES["attest"] + self._ka_craft_counter)
        self._ka_craft_counter += 1
        conn = self._ka_open_plain(path)
        try:
            cursor = conn.execute(
                "INSERT INTO screening_arbitration_attestations"
                "(record_id,task_id,round_id,stage,arbitrator_user_id,"
                "arbitrator_role,required_names_json,input_proof_json,"
                "resolutions_digest,attest_sha256,attested_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (record_id, task_id, round_id, stage, arbitrator_user_id,
                 arbitrator_role, required_names_json, input_proof_json,
                 resolutions_digest, attest_sha, attested_at))
            new_id = int(cursor.lastrowid)
            conn.commit()
        finally:
            conn.close()
        return new_id

    def _ka_craft_pair(self, path, *, records_json, input_proof_json,
                       task_id="T1", stage=STAGE1, generic_stage=None,
                       round_id=None, attested_at=_KA_TS2,
                       required_names_json=_KA_REQUIRED_NAMES_JSON,
                       created_at=_KA_TS1, attest_sha=None,
                       resolutions_digest=None):
        """自包含损伤构造：prepare + 直接 INSERT generic 行与 attestation 行。

        generic_stage 允许两行 stage 不一致（stage_mismatch 变体）；
        attest_sha/resolutions_digest 透传损伤覆盖；返回
        (record_id, attestation_id)。
        """
        self.arb.prepare_arbitration_attestation_storage(path)
        record_id = self._ka_insert_generic(
            path, records_json=records_json,
            stage=stage if generic_stage is None else generic_stage,
            created_at=created_at)
        attestation_id = self._ka_insert_attestation(
            path, record_id=record_id, input_proof_json=input_proof_json,
            task_id=task_id, stage=stage, round_id=round_id,
            attested_at=attested_at, required_names_json=required_names_json,
            attest_sha=attest_sha, resolutions_digest=resolutions_digest)
        return (record_id, attestation_id)

    # ------------------------------------------------------------------
    # 共用断言
    # ------------------------------------------------------------------

    def _ka_result_domain_assert(self, result, requested, *,
                                 expected_by_key=None, expected_issues=None):
        """P05 规格断言：by_key/issues ⊆ 请求域、互斥、值形状、reason 合同域。"""
        requested_set = set(requested)
        self.assertIsInstance(result, self.arb.KeyedAttestations, TAG_KEYED)
        self.assertIsInstance(result.by_key, dict, TAG_KEYED)
        self.assertIsInstance(result.issues, dict, TAG_KEYED)
        self.assertTrue(set(result.by_key) <= requested_set,
                        TAG_KEYED + ": by_key 键集必须 ⊆ 请求域")
        self.assertTrue(set(result.issues) <= requested_set,
                        TAG_KEYED + ": issues 键集必须 ⊆ 请求域")
        self.assertTrue(set(result.by_key).isdisjoint(result.issues),
                        TAG_KEYED + ": 已命中键不得再列 issues（互斥）")
        for key, entries in result.by_key.items():
            self.assertIsInstance(entries, tuple,
                                  TAG_KEYED + ": by_key 值必须是条目元组 " + key)
            for entry in entries:
                self.assertIsInstance(entry, dict,
                                      TAG_KEYED + ": 条目必须是映射 " + key)
        for reason in result.issues.values():
            self.assertIsInstance(reason, str, TAG_KEYED + ": issue 取值类型")
            self.assertIn(reason, _KA_ISSUE_REASONS,
                          TAG_KEYED + ": issue 取值合同域")
        if expected_by_key is not None:
            self.assertEqual(set(result.by_key), set(expected_by_key),
                             TAG_KEYED + ": by_key 键集")
        if expected_issues is not None:
            self.assertEqual(result.issues, expected_issues,
                             TAG_KEYED + ": issues 精确键→原因")

    def _ka_entry_shape_assert(self, entry, *, item_key, decision, proof):
        """C06 冻结返回形状：条目/attestation/record 字段集与关键字段类型。"""
        self.assertEqual(frozenset(entry), _KA_ENTRY_FIELDS,
                         TAG_KEYED + ": 条目字段集")
        self.assertEqual(entry["item_key"], item_key, TAG_KEYED)
        self.assertEqual(entry["decision"], decision, TAG_KEYED)
        self.assertEqual(entry["proof"], proof, TAG_KEYED + ": 该键 proof 节选")
        self.assertIsInstance(entry["rationale"], str, TAG_KEYED)
        self.assertIsInstance(entry["attestation_id"], int, TAG_KEYED)
        self.assertIsInstance(entry["record_id"], int, TAG_KEYED)
        attestation = entry["attestation"]
        record = entry["record"]
        self.assertEqual(frozenset(attestation), _KA_ATTESTATION_FIELDS,
                         TAG_KEYED + ": attestation 字段集")
        self.assertEqual(attestation["id"], entry["attestation_id"],
                         TAG_KEYED + ": 双 ID 一致（attestation）")
        self.assertEqual(attestation["record_id"], entry["record_id"],
                         TAG_KEYED)
        self.assertEqual(attestation["required_names"], ["alice", "bob"],
                         TAG_KEYED)
        self.assertIsInstance(attestation["input_proof"], dict, TAG_KEYED)
        self.assertRegex(attestation["attest_sha256"], _KA_HEX64_RE,
                         TAG_KEYED + ": attest_sha256 64 位小写 hex")
        self.assertRegex(attestation["resolutions_digest"], _KA_HEX64_RE,
                         TAG_KEYED + ": resolutions_digest 64 位小写 hex")
        self.assertTrue(attestation["attested_at"], TAG_KEYED)
        self.assertEqual(frozenset(record), _KA_RECORD_FIELDS,
                         TAG_KEYED + ": record 字段集")
        self.assertEqual(record["id"], entry["record_id"],
                         TAG_KEYED + ": 双 ID 一致（record）")
        self.assertIsInstance(record["records"], list, TAG_KEYED)


class ProvenBOnlyTests(KeyedAttestationsTestBase):
    """C06：可证明单键局部的坏 proof 只记该键 issue，不阻塞同记录其他键。"""

    def test_proven_b_only_bad_proof_does_not_block_a(self):
        pair_ab = (_ka_row_spec("A", "include", "exclude"),
                   _ka_row_spec("B", "include", "exclude"))
        resolutions_ab = [
            {"zotero_key": "A", "decision": "include", "rationale": ""},
            {"zotero_key": "B", "decision": "exclude", "rationale": "B-out"},
        ]
        good_records_json = json.dumps([
            {"zotero_key": "A", "arbitration_status": "adjudicated",
             "final_decision": "include", "arbitration_reason": ""},
            {"zotero_key": "B", "arbitration_status": "adjudicated",
             "final_decision": "exclude", "arbitration_reason": "B-out"},
        ])
        bad_decision_records_json = json.dumps([
            {"zotero_key": "A", "arbitration_status": "adjudicated",
             "final_decision": "include", "arbitration_reason": ""},
            {"zotero_key": "B", "arbitration_status": "adjudicated",
             "final_decision": "maybe", "arbitration_reason": ""},
        ])

        # -- 正控：proof 覆盖 A/B → 双命中、零 issue（锚两侧均不红）。 --
        store = self._ka_make_store(
            self.base / "healthy.db", row_pairs=pair_ab,
            resolutions=resolutions_ab, proof=_ka_proof(("A", "B")))
        probe = self._ka_open_borrowed(self.base / "healthy.db")
        try:
            result = self.arb.read_attestations_for_keys_on(
                probe, task_id="T1", stage=STAGE1, round_id=None,
                item_keys=("A", "B"))
            self.assertNotIn("A", result.issues, TAG_B_ONLY + ": 正控 A")
            self.assertNotIn("B", result.issues, TAG_B_ONLY + ": 正控 B")
            self._ka_result_domain_assert(
                result, ("A", "B"), expected_by_key={"A", "B"},
                expected_issues={})
            self.assertEqual(len(result.by_key["A"]), 1, TAG_B_ONLY)
            self._ka_entry_shape_assert(
                result.by_key["A"][0], item_key="A", decision="include",
                proof=_ka_proof(("A",))["A"])
            self.assertEqual(result.by_key["B"][0]["decision"], "exclude",
                             TAG_B_ONLY)
            self.assertEqual(result.by_key["B"][0]["rationale"], "B-out",
                             TAG_B_ONLY)
            self.assertEqual(
                result.by_key["A"][0]["attestation_id"],
                store["attestation_id"], TAG_B_ONLY + ": 与写入侧同一 ID")
        finally:
            probe.close()

        # -- proof_missing：API 真实写入的单记录，proof 只覆盖 A。 --
        self._ka_make_store(
            self.base / "proof-missing.db", row_pairs=pair_ab,
            resolutions=resolutions_ab, proof=_ka_proof(("A",)))
        probe = self._ka_open_borrowed(self.base / "proof-missing.db")
        try:
            result = self.arb.read_attestations_for_keys_on(
                probe, task_id="T1", stage=STAGE1, round_id=None,
                item_keys=("A", "B"))
            self.assertNotIn("A", result.issues,
                             TAG_B_ONLY + ": B 缺 proof 不连坐 A（锚）")
            self.assertIn("A", result.by_key, TAG_B_ONLY + ": A 正常命中")
            self.assertEqual(result.issues, {"B": "proof_missing"}, TAG_B_ONLY)
            self.assertNotIn("B", result.by_key, TAG_B_ONLY)
            self._ka_entry_shape_assert(
                result.by_key["A"][0], item_key="A", decision="include",
                proof=_ka_proof(("A",))["A"])
        finally:
            probe.close()

        # -- proof_invalid：B 条目 library_id 非 32 位小写 hex（ crafted 行）。 --
        bad_lib_proof = {
            "A": _ka_proof(("A",))["A"],
            "B": {"alice": {"library_id": "xy" * 16, "revision": 1}},
        }
        self._ka_craft_pair(
            self.base / "proof-bad-lib.db",
            records_json=good_records_json,
            input_proof_json=json.dumps(bad_lib_proof))
        probe = self._ka_open_borrowed(self.base / "proof-bad-lib.db")
        try:
            result = self.arb.read_attestations_for_keys_on(
                probe, task_id="T1", stage=STAGE1, round_id=None,
                item_keys=("A", "B"))
            self.assertNotIn("A", result.issues,
                             TAG_B_ONLY + ": B 坏 proof 形状不连坐 A（锚）")
            self.assertEqual(result.issues, {"B": "proof_invalid"}, TAG_B_ONLY)
            self.assertEqual(set(result.by_key), {"A"}, TAG_B_ONLY)
        finally:
            probe.close()

        # -- proof_invalid：B 条目键集不恰为 {library_id, revision}。 --
        extra_key_proof = {
            "A": _ka_proof(("A",))["A"],
            "B": {"alice": {"library_id": _KA_LIBRARY_ID, "revision": 1,
                            "extra": 1}},
        }
        self._ka_craft_pair(
            self.base / "proof-extra-key.db",
            records_json=good_records_json,
            input_proof_json=json.dumps(extra_key_proof))
        probe = self._ka_open_borrowed(self.base / "proof-extra-key.db")
        try:
            result = self.arb.read_attestations_for_keys_on(
                probe, task_id="T1", stage=STAGE1, round_id=None,
                item_keys=("A", "B"))
            self.assertNotIn("A", result.issues, TAG_B_ONLY + "（锚）")
            self.assertEqual(result.issues, {"B": "proof_invalid"}, TAG_B_ONLY)
            self.assertEqual(set(result.by_key), {"A"}, TAG_B_ONLY)
        finally:
            probe.close()

        # -- adjudication_row_invalid：B 裁决行坏值（maybe）也是可证明单键局部。 --
        self._ka_craft_pair(
            self.base / "bad-decision.db",
            records_json=bad_decision_records_json,
            input_proof_json=json.dumps(_ka_proof(("A", "B"))))
        probe = self._ka_open_borrowed(self.base / "bad-decision.db")
        try:
            result = self.arb.read_attestations_for_keys_on(
                probe, task_id="T1", stage=STAGE1, round_id=None,
                item_keys=("A", "B"))
            self.assertNotIn("A", result.issues,
                             TAG_B_ONLY + ": B 坏裁决行不连坐 A（锚）")
            self.assertEqual(result.issues,
                             {"B": "adjudication_row_invalid"}, TAG_B_ONLY)
            self.assertEqual(set(result.by_key), {"A"}, TAG_B_ONLY)
            self._ka_entry_shape_assert(
                result.by_key["A"][0], item_key="A", decision="include",
                proof=_ka_proof(("A", "B"))["A"])
        finally:
            probe.close()


class UnknownMembershipTests(KeyedAttestationsTestBase):
    """C06：membership 未知（坏 JSON/缺 key/孤儿）影响该请求全部仍需仲裁键。"""

    def test_unknown_membership_is_issue(self):
        pair_ab = (_ka_row_spec("A", "include", "exclude"),
                   _ka_row_spec("B", "include", "exclude"))
        resolutions_ab = [
            {"zotero_key": "A", "decision": "include", "rationale": ""},
            {"zotero_key": "B", "decision": "exclude", "rationale": "B-out"},
        ]
        good_records_json = json.dumps([
            {"zotero_key": "A", "arbitration_status": "adjudicated",
             "final_decision": "include", "arbitration_reason": ""},
            {"zotero_key": "B", "arbitration_status": "adjudicated",
             "final_decision": "exclude", "arbitration_reason": "B-out"},
        ])
        both_proof_json = json.dumps(_ka_proof(("A", "B")))

        # -- 正控：健康 crafted 记录 → 双命中零 issue。 --
        self._ka_craft_pair(
            self.base / "craft-ok.db", records_json=good_records_json,
            input_proof_json=both_proof_json)
        probe = self._ka_open_borrowed(self.base / "craft-ok.db")
        try:
            result = self.arb.read_attestations_for_keys_on(
                probe, task_id="T1", stage=STAGE1, round_id=None,
                item_keys=("A", "B"))
            self._ka_result_domain_assert(
                result, ("A", "B"), expected_by_key={"A", "B"},
                expected_issues={})
        finally:
            probe.close()

        # -- 坏 records JSON 族：membership 未知 → 全部请求键受损。 --
        bad_json_variants = (
            ("truncated-json", '["zotero_key": "A"'),
            ("json-object-not-list", '{"zotero_key": "A"}'),
            ("item-not-dict", '[1]'),
            ("item-missing-zotero-key",
             '[{"arbitration_status": "adjudicated",'
             ' "final_decision": "include"}]'),
            ("duplicate-zotero-key",
             '[{"zotero_key": "A", "arbitration_status": "adjudicated",'
             ' "final_decision": "include"},'
             '{"zotero_key": "A", "arbitration_status": "adjudicated",'
             ' "final_decision": "exclude"}]'),
            ("empty-zotero-key",
             '[{"zotero_key": "", "arbitration_status": "adjudicated",'
             ' "final_decision": "include"}]'),
        )
        for label, records_json in bad_json_variants:
            with self.subTest(variant=label):
                db = self.base / ("unknown-%s.db" % label)
                self._ka_craft_pair(db, records_json=records_json,
                                    input_proof_json=both_proof_json)
                probe = self._ka_open_borrowed(db)
                try:
                    result = self.arb.read_attestations_for_keys_on(
                        probe, task_id="T1", stage=STAGE1, round_id=None,
                        item_keys=("A", "B"))
                    self.assertEqual(
                        result.issues,
                        {"A": "records_unreadable",
                         "B": "records_unreadable"},
                        TAG_UNKNOWN + ": " + label)
                    self.assertEqual(result.by_key, {}, TAG_UNKNOWN)
                finally:
                    probe.close()

        # -- 孤儿 attestation（record_id 指向不存在的 generic 行）同桶全键。 --
        orphan_db = self.base / "orphan.db"
        self.arb.prepare_arbitration_attestation_storage(orphan_db)
        self._ka_insert_attestation(
            orphan_db, record_id=424242, input_proof_json=both_proof_json)
        probe = self._ka_open_borrowed(orphan_db)
        try:
            result = self.arb.read_attestations_for_keys_on(
                probe, task_id="T1", stage=STAGE1, round_id=None,
                item_keys=("A", "B"))
            self.assertEqual(result.issues,
                             {"A": "attestation_orphan",
                              "B": "attestation_orphan"},
                             TAG_UNKNOWN + ": 孤儿=membership 未知")
            self.assertEqual(result.by_key, {}, TAG_UNKNOWN)
        finally:
            probe.close()

        # -- 已获有效命中的键不再列入 issues（「仍需仲裁」语义）。 --
        mixed_db = self.base / "hit-not-reissued.db"
        self._ka_make_store(
            mixed_db, row_pairs=pair_ab,
            resolutions=[{"zotero_key": "A", "decision": "include",
                          "rationale": ""}],
            proof=_ka_proof(("A",)))
        self._ka_craft_pair(
            mixed_db, records_json='["broken',
            input_proof_json=both_proof_json)
        probe = self._ka_open_borrowed(mixed_db)
        try:
            result = self.arb.read_attestations_for_keys_on(
                probe, task_id="T1", stage=STAGE1, round_id=None,
                item_keys=("A", "B"))
            self.assertIn("A", result.by_key,
                          TAG_UNKNOWN + ": A 已有有效命中")
            self.assertNotIn("A", result.issues,
                             TAG_UNKNOWN + ": 已命中键不再列 issues")
            self.assertEqual(result.issues, {"B": "records_unreadable"},
                             TAG_UNKNOWN)
        finally:
            probe.close()

        # -- 可信 SQL header 先筛：他 task/stage/round 的坏记录不泄漏本请求。 --
        iso_db = self.base / "header-isolation.db"
        self._ka_make_store(
            iso_db, row_pairs=pair_ab, resolutions=resolutions_ab,
            proof=_ka_proof(("A", "B")))
        self._ka_craft_pair(
            iso_db, records_json='["broken', input_proof_json=both_proof_json,
            task_id="T2")
        self._ka_craft_pair(
            iso_db, records_json='["broken', input_proof_json=both_proof_json,
            stage=STAGE2, generic_stage=STAGE2)
        self._ka_craft_pair(
            iso_db, records_json='["broken', input_proof_json=both_proof_json,
            round_id="r1")
        probe = self._ka_open_borrowed(iso_db)
        try:
            clean = self.arb.read_attestations_for_keys_on(
                probe, task_id="T1", stage=STAGE1, round_id=None,
                item_keys=("A", "B"))
            self._ka_result_domain_assert(
                clean, ("A", "B"), expected_by_key={"A", "B"},
                expected_issues={},
                )
            for task_id, stage, round_id in (("T2", STAGE1, None),
                                             ("T1", STAGE2, None),
                                             ("T1", STAGE1, "r1")):
                with self.subTest(header="%s/%s/%s" % (task_id, stage,
                                                       round_id)):
                    damaged = self.arb.read_attestations_for_keys_on(
                        probe, task_id=task_id, stage=stage,
                        round_id=round_id, item_keys=("A", "B"))
                    self.assertEqual(
                        set(damaged.issues), {"A", "B"},
                        TAG_UNKNOWN + ": 坏记录只影响自身 header 域")
                    self.assertEqual(damaged.by_key, {}, TAG_UNKNOWN)
        finally:
            probe.close()

        # -- 无 attestation 表（generic-only 存储或空库）= 合法空，不建不抛。 --
        generic_only = self.base / "generic-only.db"
        merged = _ka_merged_frame(self.arb, pair_ab)
        self.arb.append_decision_arbitration(
            generic_only, stage=STAGE1, merged=merged,
            resolutions=resolutions_ab, arbitrator_id="arb-1")
        with closing(self._ka_open_plain(generic_only)) as raw:
            tables = {row[0] for row in raw.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertNotIn("screening_arbitration_attestations", tables,
                         TAG_STORE + ": 前置自证：generic append 不建 attestation 表")
        probe = self._ka_open_borrowed(generic_only)
        try:
            result = self.arb.read_attestations_for_keys_on(
                probe, task_id="T1", stage=STAGE1, round_id=None,
                item_keys=("A", "B"))
            self._ka_result_domain_assert(
                result, ("A", "B"), expected_by_key=set(),
                expected_issues={})
        finally:
            probe.close()
        empty_db = self.base / "empty.db"
        with closing(self._ka_open_plain(empty_db)):
            pass
        probe = self._ka_open_borrowed(empty_db)
        try:
            result = self.arb.read_attestations_for_keys_on(
                probe, task_id="T1", stage=STAGE1, round_id=None,
                item_keys=("A",))
            self.assertEqual(result.by_key, {}, TAG_STORE + ": 空库合法空")
            self.assertEqual(result.issues, {}, TAG_STORE)
        finally:
            probe.close()

        # -- 表在而缺 generic 表/缺列 = typed 503 类，绝不伪「无证据」。 --
        no_generic = self.base / "no-generic.db"
        self.arb.prepare_arbitration_attestation_storage(no_generic)
        with closing(self._ka_open_plain(no_generic)) as raw:
            raw.execute("DROP TABLE screening_arbitrations")
            raw.commit()
        probe = self._ka_open_borrowed(no_generic)
        try:
            with self.assertRaises(
                    self.arb.ArbitrationAttestationStoreUnavailable,
                    msg=TAG_STORE) as caught:
                self.arb.read_attestations_for_keys_on(
                    probe, task_id="T1", stage=STAGE1, round_id=None,
                    item_keys=("A",))
            self.assertIs(type(caught.exception),
                          self.arb.ArbitrationAttestationStoreUnavailable,
                          TAG_STORE + ": typed 精确类型")
            self.assertIn("missing the generic", str(caught.exception),
                          TAG_STORE)
        finally:
            probe.close()

        weak_attest_ddl = (
            "CREATE TABLE screening_arbitration_attestations ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "record_id INTEGER NOT NULL, task_id TEXT NOT NULL, "
            "round_id TEXT, stage TEXT NOT NULL, "
            "arbitrator_user_id INTEGER NOT NULL, "
            "arbitrator_role TEXT NOT NULL, required_names_json TEXT NOT NULL, "
            "input_proof_json TEXT NOT NULL, resolutions_digest TEXT NOT NULL, "
            "attested_at TEXT NOT NULL)")
        weak_generic_ddl = (
            "CREATE TABLE screening_arbitrations ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, stage TEXT NOT NULL, "
            "merge_sha256 TEXT NOT NULL, result_sha256 TEXT NOT NULL, "
            "supersedes_id INTEGER, arbitrator_id TEXT NOT NULL, "
            "created_at TEXT NOT NULL)")
        column_variants = (
            ("attest-missing-column",
             [("DROP TABLE screening_arbitration_attestations", ()),
              (weak_attest_ddl, ())],
             "screening_arbitration_attestations is missing columns"),
            ("generic-missing-column",
             [("DROP TABLE screening_arbitrations", ()),
              (weak_generic_ddl, ())],
             "screening_arbitrations is missing columns"),
        )
        for label, damage, message_part in column_variants:
            with self.subTest(variant=label):
                db = self.base / ("weak-%s.db" % label)
                self.arb.prepare_arbitration_attestation_storage(db)
                conn = self._ka_open_plain(db)
                try:
                    for statement, params in damage:
                        conn.execute(statement, params)
                    conn.commit()
                finally:
                    conn.close()
                probe = self._ka_open_borrowed(db)
                try:
                    with self.assertRaises(
                            self.arb.ArbitrationAttestationStoreUnavailable,
                            msg=TAG_STORE) as caught:
                        self.arb.read_attestations_for_keys_on(
                            probe, task_id="T1", stage=STAGE1, round_id=None,
                            item_keys=("A",))
                    self.assertIs(
                        type(caught.exception),
                        self.arb.ArbitrationAttestationStoreUnavailable,
                        TAG_STORE + ": " + label)
                    self.assertIn(message_part, str(caught.exception),
                                  TAG_STORE + ": " + label)
                finally:
                    probe.close()


class TargetedTimeAndOrphanTests(KeyedAttestationsTestBase):
    """C06：命中键的非空+ISO 可解析时间核验与孤儿处理（记录级损害键域界定）。

    未冻结项（记录性断言，随合同冻结修订）：date-only ISO（'2026-11-18'）现状
    被 _keyed_attestation_time_ok 接受；rationale/arbitrator_role 读取侧不重
    验（写入侧两道闸负责）。两者均只记现状，不钉必红。
    """

    def test_targeted_bad_time_and_orphan(self):
        domain_records_json = json.dumps([
            {"zotero_key": "A", "arbitration_status": "adjudicated",
             "final_decision": "include", "arbitration_reason": ""},
            {"zotero_key": "G", "arbitration_status": "agreed",
             "final_decision": "include", "arbitration_reason": ""},
        ])
        both_proof_json = json.dumps(_ka_proof(("A",)))

        # -- 非空 + ISO 可解析：坏 attested_at 只影响请求键∩记录键域。 --
        # G 是域内 agreed 键（本不会命中）同样记 issue；Z 在域外零影响。
        time_variants = (
            ("attested-at-empty", {"attested_at": ""},
             {"A": "attested_at_invalid", "G": "attested_at_invalid"}),
            ("attested-at-non-iso", {"attested_at": "yesterday-at-noon"},
             {"A": "attested_at_invalid", "G": "attested_at_invalid"}),
            ("created-at-non-iso", {"created_at": "2026-13-45 99:99"},
             {"A": "created_at_invalid", "G": "created_at_invalid"}),
            ("attest-digest-uppercase", {"attest_sha": "AB" * 32},
             {"A": "attestation_digest_invalid",
              "G": "attestation_digest_invalid"}),
            ("required-names-single",
             {"required_names_json": '["alice"]'},
             {"A": "required_names_invalid", "G": "required_names_invalid"}),
            ("stage-mismatch", {"generic_stage": STAGE2},
             {"A": "stage_mismatch", "G": "stage_mismatch"}),
        )
        for label, overrides, expected_issues in time_variants:
            with self.subTest(variant=label):
                db = self.base / ("time-%s.db" % label)
                self._ka_craft_pair(
                    db, records_json=domain_records_json,
                    input_proof_json=both_proof_json, **overrides)
                probe = self._ka_open_borrowed(db)
                try:
                    result = self.arb.read_attestations_for_keys_on(
                        probe, task_id="T1", stage=STAGE1, round_id=None,
                        item_keys=("A", "G", "Z"))
                    self.assertEqual(result.issues, expected_issues,
                                     TAG_TIME + ": " + label)
                    self.assertEqual(result.by_key, {},
                                     TAG_TIME + ": " + label)
                    self._ka_result_domain_assert(
                        result, ("A", "G", "Z"), expected_by_key=set(),
                        expected_issues=expected_issues)
                finally:
                    probe.close()

        # -- 孤儿与同请求健康记录并存：健康键保持命中，不因孤儿被 issue 覆盖。 --
        mixed_db = self.base / "orphan-mixed.db"
        pair_c = (_ka_row_spec("C", "include", "exclude"),)
        self._ka_make_store(
            mixed_db, row_pairs=pair_c,
            resolutions=[{"zotero_key": "C", "decision": "include",
                          "rationale": ""}],
            proof=_ka_proof(("C",)))
        self._ka_insert_attestation(
            mixed_db, record_id=424242,
            input_proof_json=json.dumps(_ka_proof(("A",))))
        probe = self._ka_open_borrowed(mixed_db)
        try:
            result = self.arb.read_attestations_for_keys_on(
                probe, task_id="T1", stage=STAGE1, round_id=None,
                item_keys=("A", "B", "C"))
            self.assertEqual(set(result.by_key), {"C"},
                             TAG_TIME + ": 健康键保持命中")
            self.assertEqual(result.issues,
                             {"A": "attestation_orphan",
                              "B": "attestation_orphan"},
                             TAG_TIME + ": 孤儿影响仍需仲裁键")
            self.assertNotIn("C", result.issues, TAG_TIME)
            self._ka_entry_shape_assert(
                result.by_key["C"][0], item_key="C", decision="include",
                proof=_ka_proof(("C",))["C"])
        finally:
            probe.close()

        # -- 同键多记录损害：按 attestation id 升序取首因（setdefault 语义）。 --
        first_cause_db = self.base / "first-cause.db"
        single_records_json = json.dumps([
            {"zotero_key": "A", "arbitration_status": "adjudicated",
             "final_decision": "include", "arbitration_reason": ""},
        ])
        self._ka_craft_pair(
            first_cause_db, records_json=single_records_json,
            input_proof_json=both_proof_json,
            required_names_json='["alice"]')
        self._ka_craft_pair(
            first_cause_db, records_json=single_records_json,
            input_proof_json=both_proof_json, attested_at="not-a-time")
        probe = self._ka_open_borrowed(first_cause_db)
        try:
            result = self.arb.read_attestations_for_keys_on(
                probe, task_id="T1", stage=STAGE1, round_id=None,
                item_keys=("A",))
            self.assertEqual(result.issues, {"A": "required_names_invalid"},
                             TAG_TIME + ": 首因=id 升序首个损害")
        finally:
            probe.close()

        # -- 未冻结项（记录性断言）：date-only ISO 现状被接受。 --
        date_only_db = self.base / "date-only.db"
        self._ka_craft_pair(
            date_only_db, records_json=single_records_json,
            input_proof_json=both_proof_json, attested_at="2026-11-18")
        probe = self._ka_open_borrowed(date_only_db)
        try:
            result = self.arb.read_attestations_for_keys_on(
                probe, task_id="T1", stage=STAGE1, round_id=None,
                item_keys=("A",))
            self.assertEqual(set(result.by_key), {"A"},
                             TAG_TIME + ": 未冻结项——date-only ISO 现状接受")
            self.assertEqual(result.issues, {}, TAG_TIME)
        finally:
            probe.close()


class NoJson1DependencyTests(KeyedAttestationsTestBase):
    """C06/P05：membership 纯 Python 解码——零 JSON1 SQL、行为等价、分页有界。"""

    def test_no_json1_dependency(self):
        # -- 检测器正控：JSON1 函数族确实会被本测试的扫描捕获。 --
        self.assertIsNotNone(
            _KA_JSON1_SQL_RE.search("SELECT json_each(rec) FROM t"),
            TAG_JSON1 + ": 检测器正控 json_each")
        self.assertIsNotNone(
            _KA_JSON1_SQL_RE.search("select json_extract(a, b) from t"),
            TAG_JSON1 + ": 检测器正控 json_extract")
        self.assertIsNone(
            _KA_JSON1_SQL_RE.search(
                "SELECT required_names_json FROM screening_arbitrations"),
            TAG_JSON1 + ": 列名 _json 尾缀不误报")
        # 环境记录：当前解释器自带 JSON1（非使用是选择而非环境缺失）。
        with closing(sqlite3.connect(":memory:")) as raw:
            self.assertEqual(
                raw.execute("SELECT json_valid('[1]')").fetchone()[0], 1,
                TAG_JSON1 + ": 环境记录——本机 sqlite 恒含 JSON1")

        # -- 行为等价夹具：\u 转义键（解码后命中）+ 正文提及他键（不得假命中）。 --
        trap_db = self.base / "traps.db"
        escape_records_json = (
            '[{"zotero_key":"\\u0041","arbitration_status":"adjudicated",'
            '"final_decision":"include","arbitration_reason":""}]')
        mention_records_json = (
            '[{"zotero_key":"B","arbitration_status":"adjudicated",'
            '"final_decision":"exclude",'
            '"arbitration_reason":"mentions A and C inside rationale"}]')
        self._ka_craft_pair(
            trap_db, records_json=escape_records_json,
            input_proof_json=json.dumps(_ka_proof(("A",))))
        self._ka_craft_pair(
            trap_db, records_json=mention_records_json,
            input_proof_json=json.dumps(_ka_proof(("B",))))
        probe = self._ka_open_borrowed(trap_db)
        try:
            self._ka_reset_probe(probe)
            result = self.arb.read_attestations_for_keys_on(
                probe, task_id="T1", stage=STAGE1, round_id=None,
                item_keys=("A", "B", "C"))
            self._ka_result_domain_assert(
                result, ("A", "B", "C"), expected_by_key={"A", "B"},
                expected_issues={})
            self._ka_entry_shape_assert(
                result.by_key["A"][0], item_key="A", decision="include",
                proof=_ka_proof(("A",))["A"])
            self.assertEqual(result.by_key["B"][0]["decision"], "exclude",
                             TAG_JSON1)
            self.assertEqual(
                result.by_key["B"][0]["rationale"],
                "mentions A and C inside rationale", TAG_JSON1)
            for statement in probe.statements[probe.statement_offset:]:
                self.assertIsNone(
                    _KA_JSON1_SQL_RE.search(statement),
                    TAG_JSON1 + ": 语句面零 JSON1 函数: %r" % statement[:80])
        finally:
            probe.close()

        # -- 分页有界 + 完整：205 条 attestation（跨 200 行页界）全部读回。 --
        page_db = self.base / "pagination.db"
        pair_p = (_ka_row_spec("P", "include", "exclude"),)
        self._ka_make_store(
            page_db, row_pairs=pair_p,
            resolutions=[{"zotero_key": "P", "decision": "include",
                          "rationale": ""}],
            proof=_ka_proof(("P",)))
        record_id = None
        with closing(self._ka_open_plain(page_db)) as raw:
            row = raw.execute(
                "SELECT id FROM screening_arbitrations ORDER BY id LIMIT 1"
            ).fetchone()
            record_id = int(row[0])
        proof_json = json.dumps(_ka_proof(("P",)))
        for _ in range(204):
            self._ka_insert_attestation(
                page_db, record_id=record_id, input_proof_json=proof_json)
        probe = self._ka_open_borrowed(page_db)
        try:
            self._ka_reset_probe(probe)
            result = self.arb.read_attestations_for_keys_on(
                probe, task_id="T1", stage=STAGE1, round_id=None,
                item_keys=("P",))
            self.assertEqual(len(result.by_key.get("P", ())), 205,
                             TAG_JSON1 + ": 跨页完整读回 205 条")
            ids = [entry["attestation_id"] for entry in result.by_key["P"]]
            self.assertEqual(ids, list(range(1, 206)),
                             TAG_JSON1 + ": 条目按 attestation id 升序")
            self._ka_entry_shape_assert(
                result.by_key["P"][0], item_key="P", decision="include",
                proof=_ka_proof(("P",))["P"])
            self._ka_entry_shape_assert(
                result.by_key["P"][204], item_key="P", decision="include",
                proof=_ka_proof(("P",))["P"])
            window_cursors = probe.cursors[probe.cursor_offset:]
            page_cursors = [
                cursor for cursor in window_cursors
                if "FROM screening_arbitration_attestations" in cursor.sql
                and "LIMIT" in cursor.sql]
            self.assertEqual(len(page_cursors), 2,
                             TAG_JSON1 + ": 205 行恰两页（200+5）")
            for cursor in page_cursors:
                self.assertEqual(cursor.parameters[-1], 200,
                                 TAG_JSON1 + ": 每页 LIMIT 绑定值至多 200")
                self.assertTrue(cursor.fetchall_called,
                                TAG_JSON1 + ": 页 cursor 完整物化")
            for statement in probe.statements[probe.statement_offset:]:
                self.assertIsNone(
                    _KA_JSON1_SQL_RE.search(statement),
                    TAG_JSON1 + ": 分页读取语句面零 JSON1 函数")
        finally:
            probe.close()


class GenericRegressionTests(KeyedAttestationsTestBase):
    """P05：既有 generic append/latest/get 与 S1b attested 追加/列举零变化正控。"""

    def test_generic_latest_unchanged(self):
        missing = self.base / "missing.db"
        self.assertIsNone(
            self.arb.latest_decision_arbitration(missing, STAGE1),
            TAG_GENERIC + ": 缺文件 None")
        self.assertIsNone(
            self.arb.get_decision_arbitration(missing, 1),
            TAG_GENERIC + ": 缺文件 None")
        self.assertRaisesRegex(
            ValueError, "stage", self.arb.latest_decision_arbitration,
            missing, "fulltext")

        store = self.base / "generic.db"
        pair_ab = (_ka_row_spec("A", "include", "exclude"),
                   _ka_row_spec("B", "include", "exclude"))
        merged = _ka_merged_frame(self.arb, pair_ab)
        resolutions = [
            {"zotero_key": "A", "decision": "include", "rationale": ""},
            {"zotero_key": "B", "decision": "exclude", "rationale": "B-out"},
        ]
        rec1 = self.arb.append_decision_arbitration(
            store, stage=STAGE1, merged=merged, resolutions=resolutions,
            arbitrator_id="arb-1")
        self.assertEqual(frozenset(rec1), _KA_RECORD_FIELDS,
                         TAG_GENERIC + ": generic 记录字段集零变化")
        self.assertEqual(rec1["stage"], STAGE1, TAG_GENERIC)
        self.assertEqual(rec1["arbitrator_id"], "arb-1", TAG_GENERIC)
        self.assertIsNone(rec1["supersedes_id"], TAG_GENERIC)
        self.assertTrue(rec1["created_at"].endswith("Z"), TAG_GENERIC)
        self.assertEqual(len(rec1["records"]), 2, TAG_GENERIC)
        statuses = {row["zotero_key"]: row["arbitration_status"]
                    for row in rec1["records"]}
        self.assertEqual(statuses, {"A": "adjudicated", "B": "adjudicated"},
                         TAG_GENERIC)

        # 幂等：同输入同结论 → 同 ID、同 created_at（旧行原样返回）。
        rec_again = self.arb.append_decision_arbitration(
            store, stage=STAGE1, merged=merged, resolutions=resolutions,
            arbitrator_id="arb-1")
        self.assertEqual(rec_again, rec1, TAG_GENERIC + ": 幂等同记录")

        # 改判 → 新记录、supersedes 前者；latest 跟随最新。
        resolutions_flip = [
            {"zotero_key": "A", "decision": "exclude", "rationale": "flip"},
            {"zotero_key": "B", "decision": "exclude", "rationale": "B-out"},
        ]
        rec2 = self.arb.append_decision_arbitration(
            store, stage=STAGE1, merged=merged, resolutions=resolutions_flip,
            arbitrator_id="arb-1")
        self.assertNotEqual(rec2["id"], rec1["id"], TAG_GENERIC)
        self.assertEqual(rec2["supersedes_id"], rec1["id"], TAG_GENERIC)
        latest = self.arb.latest_decision_arbitration(store, STAGE1)
        self.assertEqual(latest["id"], rec2["id"], TAG_GENERIC)
        self.assertEqual(latest["created_at"], rec2["created_at"], TAG_GENERIC)
        self.assertEqual(latest["records"], rec2["records"], TAG_GENERIC)
        self.assertIsNone(self.arb.latest_decision_arbitration(store, STAGE2),
                          TAG_GENERIC + ": 另 stage 无记录")
        self.assertEqual(self.arb.get_decision_arbitration(store, rec1["id"]),
                         rec1, TAG_GENERIC + ": 按 ID 往返")
        self.assertIsNone(self.arb.get_decision_arbitration(store, 99999),
                          TAG_GENERIC + ": 缺 ID None")

        # generic append 不建 attestation 表（旧语义：只装 generic schema）。
        with closing(self._ka_open_plain(store)) as raw:
            tables = {row[0] for row in raw.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertNotIn("screening_arbitration_attestations", tables,
                         TAG_GENERIC + ": 不建 attestation 表")

        # S1b 正控：prepare + attested append + path get + 借用 list_on。
        store2 = self.base / "attested.db"
        seeded = self._ka_make_store(
            store2, row_pairs=pair_ab, resolutions=resolutions,
            proof=_ka_proof(("A", "B")))
        got = self.arb.get_decision_arbitration_attestation(
            store2, seeded["attestation_id"])
        self.assertEqual(frozenset(got),
                         {"attestation", "record", "attestation_id",
                          "record_id"},
                         TAG_GENERIC + ": S1b get 返回形状零变化")
        self.assertEqual(got["attestation_id"], seeded["attestation_id"],
                         TAG_GENERIC)
        self.assertIsNone(
            self.arb.get_decision_arbitration_attestation(store2, 424242),
            TAG_GENERIC + ": 缺 attestation ID None")
        with closing(self._ka_open_plain(store2)) as plain:
            listed = self.arb.list_decision_arbitration_attestations_on(plain)
            self.assertEqual(len(listed), 1, TAG_GENERIC)
            self.assertEqual(listed[0]["attestation_id"],
                             seeded["attestation_id"], TAG_GENERIC)
            self.assertEqual(listed[0]["attestation"]["task_id"], "T1",
                             TAG_GENERIC)
            self.assertRaisesRegex(
                ValueError, "stage",
                self.arb.list_decision_arbitration_attestations_on, plain,
                stage="coding")

        # 新 keyed 读与 S1b 证据一致；generic-only 存储返回合法空。
        probe = self._ka_open_borrowed(store2)
        try:
            result = self.arb.read_attestations_for_keys_on(
                probe, task_id="T1", stage=STAGE1, round_id=None,
                item_keys=("A", "B"))
            self._ka_result_domain_assert(
                result, ("A", "B"), expected_by_key={"A", "B"},
                expected_issues={})
            self.assertEqual(result.by_key["A"][0]["attestation_id"],
                             seeded["attestation_id"],
                             TAG_GENERIC + ": 新旧读取同一 attestation")
        finally:
            probe.close()
        probe = self._ka_open_borrowed(store)
        try:
            result = self.arb.read_attestations_for_keys_on(
                probe, task_id="T1", stage=STAGE1, round_id=None,
                item_keys=("A",))
            self.assertEqual(result.by_key, {},
                             TAG_GENERIC + ": generic-only 存储合法空")
            self.assertEqual(result.issues, {}, TAG_GENERIC)
        finally:
            probe.close()


class BorrowedConnectionContractTests(KeyedAttestationsTestBase):
    """C02 借用合同：语句面/事务/工厂不可变、连接门负路、键域规范化语义。"""

    def test_no_transaction_or_factory_mutation(self):
        db = self.base / "borrowed-ok.db"
        self._ka_make_store(
            db,
            row_pairs=(_ka_row_spec("A", "include", "exclude"),
                       _ka_row_spec("B", "include", "exclude")),
            resolutions=[
                {"zotero_key": "A", "decision": "include", "rationale": ""},
                {"zotero_key": "B", "decision": "exclude",
                 "rationale": "B-out"},
            ],
            proof=_ka_proof(("A", "B")))
        probe = self._ka_open_borrowed(db)
        try:
            self._ka_reset_probe(probe)
            self.assertTrue(probe.in_transaction, TAG_CONTRACT)
            result = self.arb.read_attestations_for_keys_on(
                probe, task_id="T1", stage=STAGE1, round_id=None,
                item_keys=("A", "B"))
            self.assertEqual(set(result.by_key), {"A", "B"}, TAG_CONTRACT)
            # 事务/工厂/连接所有权前后不变。
            self.assertTrue(probe.in_transaction,
                            TAG_CONTRACT + ": 不得结束调用方事务")
            self.assertIs(probe.row_factory, sqlite3.Row,
                          TAG_CONTRACT + ": 不得改 row_factory")
            self.assertEqual(probe.commit_calls, 0, TAG_CONTRACT)
            self.assertEqual(probe.rollback_calls, 0, TAG_CONTRACT)
            self.assertEqual(probe.close_calls, 0,
                             TAG_CONTRACT + ": 不得 close 调用方连接")
            # 语句面：仅 SELECT + 只读元数据 PRAGMA（表存在性/列集核验）。
            buckets = _ka_classify_statements(
                probe.statements[probe.statement_offset:])
            self.assertEqual(buckets["txn"], [], TAG_CONTRACT)
            self.assertEqual(buckets["dml"], [], TAG_CONTRACT)
            self.assertEqual(buckets["ddl"], [], TAG_CONTRACT)
            self.assertEqual(buckets["other_pragma"], [],
                             TAG_CONTRACT + ": 不得改 PRAGMA")
            self.assertEqual(buckets["other"], [], TAG_CONTRACT)
            self.assertTrue(buckets["select"], TAG_CONTRACT + ": 有界 SELECT")
            # cursor 面：自建 Cursor 全部完整物化并确定关闭（C02）。
            window = probe.cursors[probe.cursor_offset:]
            self.assertTrue(window, TAG_CONTRACT + ": 观测窗口非空")
            for cursor in window:
                self.assertTrue(
                    cursor.fetchall_called or cursor.saw_empty
                    or cursor.fetchone_called,
                    TAG_CONTRACT + ": cursor 必须完整物化")
                self.assertTrue(cursor.closed,
                                TAG_CONTRACT + ": 自建 cursor 必须确定关闭")
            # 调用方仍可继续使用连接（所有权未被动）。
            alive = probe.execute("SELECT 1")
            self.assertEqual(alive.fetchone()[0], 1, TAG_CONTRACT)
            alive.close()
            probe.rollback()
            self.assertFalse(probe.in_transaction, TAG_CONTRACT)
        finally:
            probe.close()

    def test_connection_contract_negative_paths(self):
        db = self.base / "borrowed-neg.db"
        self._ka_make_store(
            db,
            row_pairs=(_ka_row_spec("A", "include", "exclude"),
                       _ka_row_spec("B", "include", "exclude")),
            resolutions=[
                {"zotero_key": "A", "decision": "include", "rationale": ""},
                {"zotero_key": "B", "decision": "exclude",
                 "rationale": "B-out"},
            ],
            proof=_ka_proof(("A", "B")))
        # 1) 非 native 对象 → ArbitrationConnectionContractError（精确类型）。
        with self.assertRaises(self.arb.ArbitrationConnectionContractError,
                               msg=TAG_CONTRACT) as caught:
            self.arb.read_attestations_for_keys_on(
                object(), task_id="T1", stage=STAGE1, round_id=None,
                item_keys=("A",))
        self.assertIs(type(caught.exception),
                      self.arb.ArbitrationConnectionContractError,
                      TAG_CONTRACT + ": 非 native 精确类型")
        # 2) __class__ 伪装代理（转发 Row/active 属性）→ 同拒。
        real = sqlite3.connect(str(db), timeout=15)
        self.addCleanup(real.close)
        real.row_factory = sqlite3.Row
        real.execute("BEGIN")
        proxy = _ka_DelegatingConnection(real)
        self.assertTrue(isinstance(proxy, sqlite3.Connection),
                        TAG_CONTRACT + ": 代理伪装前置自证")
        self.assertFalse(issubclass(type(proxy), sqlite3.Connection),
                         TAG_CONTRACT + ": 代理伪装前置自证")
        self.assertIs(proxy.row_factory, sqlite3.Row,
                      TAG_CONTRACT + ": 代理伪装前置自证")
        self.assertTrue(proxy.in_transaction,
                        TAG_CONTRACT + ": 代理伪装前置自证")
        with self.assertRaises(self.arb.ArbitrationConnectionContractError,
                               msg=TAG_CONTRACT) as caught_proxy:
            self.arb.read_attestations_for_keys_on(
                proxy, task_id="T1", stage=STAGE1, round_id=None,
                item_keys=("A",))
        self.assertIs(type(caught_proxy.exception),
                      self.arb.ArbitrationConnectionContractError,
                      TAG_CONTRACT + ": __class__ 伪装必须被真实 type 判断拒绝")
        real.rollback()
        # 3) native 活动事务但 row_factory 非 Row → 合同错误。
        plain_conn = sqlite3.connect(str(db), timeout=15)
        self.addCleanup(plain_conn.close)
        plain_conn.execute("BEGIN IMMEDIATE")
        with self.assertRaises(self.arb.ArbitrationConnectionContractError,
                               msg=TAG_CONTRACT) as caught_factory:
            self.arb.read_attestations_for_keys_on(
                plain_conn, task_id="T1", stage=STAGE1, round_id=None,
                item_keys=("A",))
        self.assertIs(type(caught_factory.exception),
                      self.arb.ArbitrationConnectionContractError,
                      TAG_CONTRACT)
        self.assertTrue(plain_conn.in_transaction, TAG_CONTRACT)
        plain_conn.rollback()
        # 4) native+Row 无活动事务 → 合同错误，且未偷 BEGIN、零语句。
        inactive = self._ka_open_borrowed(db, begin=False)
        self._ka_reset_probe(inactive)
        self.assertFalse(inactive.in_transaction, TAG_CONTRACT)
        with self.assertRaises(self.arb.ArbitrationConnectionContractError,
                               msg=TAG_CONTRACT) as caught_txn:
            self.arb.read_attestations_for_keys_on(
                inactive, task_id="T1", stage=STAGE1, round_id=None,
                item_keys=("A",))
        self.assertIs(type(caught_txn.exception),
                      self.arb.ArbitrationConnectionContractError,
                      TAG_CONTRACT)
        self.assertFalse(inactive.in_transaction,
                         TAG_CONTRACT + ": 不得偷开事务")
        self.assertEqual(len(inactive.statements), 0,
                         TAG_CONTRACT + ": 未发任何语句")
        inactive.close()
        # 5) closed native → sqlite3.ProgrammingError 原样透传（精确类型）。
        closed = sqlite3.connect(str(db), timeout=15)
        closed.row_factory = sqlite3.Row
        closed.execute("BEGIN")
        closed.close()
        with self.assertRaises(sqlite3.ProgrammingError,
                               msg=TAG_CONTRACT) as caught_closed:
            self.arb.read_attestations_for_keys_on(
                closed, task_id="T1", stage=STAGE1, round_id=None,
                item_keys=("A",))
        self.assertIs(type(caught_closed.exception),
                      sqlite3.ProgrammingError,
                      TAG_CONTRACT + ": closed 精确类型透传")
        # 成对正控：native 子类探针（Row+活动事务）成功。
        probe = self._ka_open_borrowed(db)
        try:
            result = self.arb.read_attestations_for_keys_on(
                probe, task_id="T1", stage=STAGE1, round_id=None,
                item_keys=("A",))
            self.assertEqual(set(result.by_key), {"A"}, TAG_CONTRACT)
        finally:
            probe.close()

    def test_key_domain_normalization_semantics(self):
        db = self.base / "normalize.db"
        pair_a = (_ka_row_spec("A", "include", "exclude"),)
        self._ka_make_store(
            db, row_pairs=pair_a,
            resolutions=[{"zotero_key": "A", "decision": "include",
                          "rationale": ""}],
            proof=_ka_proof(("A",)))
        probe = self._ka_open_borrowed(db)
        try:
            # 去重保首现：重复键不重复出现于 by_key/issues 键集。
            deduped = self.arb.read_attestations_for_keys_on(
                probe, task_id="T1", stage=STAGE1, round_id=None,
                item_keys=("A", "A", "A"))
            self._ka_result_domain_assert(
                deduped, ("A",), expected_by_key={"A"}, expected_issues={})
            self.assertEqual(len(deduped.by_key["A"]), 1,
                             TAG_KEYS + ": 去重不重复计证据")
            # 生成器输入（Iterable 合同）单遍消费。
            streamed = self.arb.read_attestations_for_keys_on(
                probe, task_id="T1", stage=STAGE1, round_id=None,
                item_keys=(key for key in ("A", "B", "A")))
            self._ka_result_domain_assert(
                streamed, ("A", "B"), expected_by_key={"A"},
                expected_issues={})
            # 拒绝族：全部 ValueError（P00/C02 normalize 语义经本 API 边界）。
            bad_inputs = (
                "A", b"A", 42, None, 3.5,
                (), [],
                ("",), ("A", ""),
                ("A\x00B",), (b"A",), (1,), ("A", 2),
                ("A\ud800",), ("\udcff",),
            )
            for bad in bad_inputs:
                with self.subTest(bad=repr(bad)):
                    with self.assertRaises(ValueError, msg=TAG_KEYS):
                        self.arb.read_attestations_for_keys_on(
                            probe, task_id="T1", stage=STAGE1, round_id=None,
                            item_keys=bad)
            # 参数门：task_id/stage/round_id 非法 → ValueError。
            gate_kwargs = (
                {"task_id": "", "stage": STAGE1, "round_id": None,
                 "item_keys": ("A",)},
                {"task_id": 5, "stage": STAGE1, "round_id": None,
                 "item_keys": ("A",)},
                {"task_id": "T1", "stage": "coding", "round_id": None,
                 "item_keys": ("A",)},
                {"task_id": "T1", "stage": "", "round_id": None,
                 "item_keys": ("A",)},
                {"task_id": "T1", "stage": STAGE1, "round_id": "",
                 "item_keys": ("A",)},
            )
            for kwargs in gate_kwargs:
                with self.subTest(kwargs=repr(kwargs)):
                    with self.assertRaises(ValueError, msg=TAG_KEYS):
                        self.arb.read_attestations_for_keys_on(
                            probe, **kwargs)
            # 失败后连接仍可用（成对正控）。
            healthy = self.arb.read_attestations_for_keys_on(
                probe, task_id="T1", stage=STAGE1, round_id=None,
                item_keys=["A"])
            self.assertEqual(set(healthy.by_key), {"A"}, TAG_KEYS)
        finally:
            probe.close()


if __name__ == "__main__":
    unittest.main()
