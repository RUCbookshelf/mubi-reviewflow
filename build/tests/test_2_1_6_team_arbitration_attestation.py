# -*- coding: utf-8 -*-
"""2.1.6 S1b 团队仲裁追加证明存储（attestation DDL/prepare/append(conn)/可信RO读出）独立回归 —— 新增独立测试文件。

合同依据（全部只读引用，本文不编辑任何合同文件）：
- `.superpowers/sdd/2026-11-14-2.1.6-maintenance/flow-attestation-s1b-brief.md`
  （RELEASED_PHASE2_IMPLEMENTATION，含 PHASE2 API 冻结：四接缝 + meta 恰六键 +
  RO 故障不得吞空 + prepare 维护事务与 append 纯借用分离）
- `out/maintenance216/team_flow_integration_design.md` §10.3（append-only 授权
  attestation schema/身份矩阵）、§11.3（input_proof = item_key→user_id(str)→
  {library_id:32lowerhex, revision:正整数非bool}；attest_sha256 幂等；双 ID 响应）、
  §11.6（generic+attest+session 同协作库连接单事务；borrowed conn 禁
  BEGIN/commit/close/PRAGMA/row_factory/executescript）、§13.8（S1b conn 协议：
  仅接受外部 collab conn、只执行语句）
- 主控冻结错误族（2026-11-15）：`ArbitrationAttestationMetaInvalid(ValueError)`
  code='arbitration_attestation_meta_invalid'；其子类
  `ArbitrationAttestationProofInvalid` code='arbitration_attestation_proof_invalid'；
  `ArbitrationConnectionContractError(RuntimeError)`；RO busy/corrupt/权限/IO
  故障的准确类型 `ArbitrationAttestationStoreUnavailable`（RuntimeError 族，
  不伪 None/[]；`ProgrammingError` 原样上抛）。
- 主控 API 还原（2026-11-15）：有效 roles 仅 reconciler（快照取值域，非授权
  事实）；meta/round 无凭空长度 cap；required_names ≤100 有旧 merge 真实出处、
  proof item_key ≤300 有旧 canonical 出处；RO 读出为与 append 返回同构的
  四键组合行（attestation/record/attestation_id/record_id）。
- R1 更正（2026-11-15，out/maintenance216/review_s1b_attestation_sol.md
  F4/P2）：查询命中 attest 行而其引用 generic 行缺失（孤儿）→ 冻结准确类型
  ArbitrationAttestationStoreUnavailable，不返回 record=None 组合行；健康行
  单条 get 不受未匹配孤儿影响；存储层不修库/不删孤儿/不全库扫描。原「孤儿
  record=None 如实呈现」为测试首轮错误固定口径，不属冻结合同。

覆盖实质场景（按 brief「必要意义回归」节，不镜像 schema、不抄哈希算法）：
  T1  prepare 维护事务：缺文件建库返回 None 且幂等；既有 generic 数据零破坏；
      prepare 后新 store 即可走文档化 append 流。
  T13 prepare 整体初装故障：全新 store 上 authorizer 精确拒 attest CREATE →
      按 PHASE1 合同 generic+attest DDL 应整体回滚（文件可存在但无半装对象）。
  T14 R1 限定回归（fix-r1 独立新增类，不触既有类）：真实已存在目录伪装
      store 路径 → get/list typed StoreUnavailable（只有真实 FileNotFoundError
      允许空）；持久 JSON 逻辑损坏（generic records_json / attest
      required_names_json / input_proof_json；'not-json'→JSONDecodeError 反例、
      非法 UTF-8 BLOB→UnicodeDecodeError 反例；SQLite TEXT 亲和下数字字面量
      会存成合法 JSON 文本，不作反例、不强造不可能 SQL 类型）→ 两公开 API
      均 typed StoreUnavailable（不裸 JSONDecodeError/UnicodeDecodeError、
      不伪空）；损坏行不毒化另一独立 generic（actor 维度另立）的健康行单条
      get；坏值无自愈原样在场。
  T2  append 双 ID 响应形状恰四键；generic 记录与旧 generic 函数真实调用逐字段
      语义等价（对照非抄算法）；round 显式 None/命名值原样存取；role 取值域
      当前仅 reconciler（快照保存；低层不查授权名册）。
  T3  同输入重试双 ID 同 + attested_at 时间原；中间插入其他 attestation 后重试
      仍返回原 ID（内容寻址幂等，非 latest 覆盖）。
  T4  proof 版本变而 generic 内容同 → generic ID 同、新 attestation ID + 真实新
      时间（真实时钟范围断言，不硬钉严格递增）；actor 换 → 新 attestation 带
      当前 UID，不把 generic 旧 arbitrator 当新证明身份。
  T5  task/round/required 内容/required 保序/proof revision/proof library_id/
      stage/resolutions 各维度隔离；多 attestation 引用同一 generic 记录；
      RO list 过滤（stage/task_id/record_id 及组合）。
  T6  attest 表 append-only（UPDATE/DELETE 拒绝）；attested 路径不放宽 generic
      包门（非 conflict 键/非法 actor 仍按 generic 校验拒绝且零写入）；
      supersedes 链经 attested 路径保持。
  T7  meta 形状严格（非 dict/缺键/多键；空 task/单人 required/重复/非文本；
      user_id 正整数非 bool 严格；role 非空文本）→ 精确
      ArbitrationAttestationMetaInvalid；错误类契约本身。
  T8  input_proof 形状严格（item_key/user_id/内层恰两键/library_id 32 位小写
      hex/revision 正整数非 bool）→ 精确 ArbitrationAttestationProofInvalid；
      多键多成员合法 proof 原样存取。
  T9  borrowed conn 契约：caller 未 BEGIN 仅精确 ContractError 且零写入不暗开启；
      两种 row_factory/PRAGMA foreign_keys/事务态不变；无 migration（对象名集
      不变，仅容忍 sqlite_sequence 内部出现）；caller 提交前回滚全弃（无自提交）。
  T10 真实 authorizer 精确拒 attest/generic INSERT（不 mock Connection.execute）
      → caller rollback：generic+attest+模拟 session 状态一起退，无半写。
  T11 可信 RO：缺文件/缺表/无匹配 ID → 空且零创建（不建文件/目录/表/DDL）；
      既有 store 损坏/锁忙必须以冻结准确类型 ArbitrationAttestationStoreUnavailable
      raise（不伪空）；RO 行为与 append 同构四键组合、引用 generic 全量记录；
      孤儿引用 → typed StoreUnavailable（R1 更正：原 record=None 成功口径系
      测试侧错误固定）；健康行单条 get 不受孤儿影响；非法过滤/ID →
      ValueError；旧 generic RO 旧兼容口径不改变（损坏仍 None）。
  T12 旧 generic 锚点（不依赖冻结 API，before 侧真实可跑）：缺/损文件 None、
      merge 2-9 门与重复包拒绝、重试幂等/supersedes/latest 轮回。

诚实记录（不伪造红）：四接缝与错误类为 S1b PHASE2 新增；before 冻结
（screening_arbitration.py SHA d642e76c…，主控实核 MATCH）不存在这些符号。
首轮 raw 运行事实更正（R1；原「首个 raw 即为 39 项矩阵」表述失实）：首个
raw before1（20261115_024045）为候选早期 35 项矩阵，不是旧产品 before 红侧；
39 项矩阵的 raw 为 run2/run3/run4_final（对主控冻结候选 screening_arbitration.py
SHA c6f9607e…/53316B，箱内源码与主仓 SAME）：38 项绿，唯一红＝T13 prepare
整体初装故障（authorizer 精确拒 attest CREATE 后 generic 三对象半装在场＝
generic executescript 先自提交、attest BEGIN 后装的分离事务，与 PHASE1
「自有单 BEGIN、generic+attest 整体 DDL 回滚」合同不符）——按主控指令保持
合同期望不放松，红侧原始日志全留呈报主控（Sol 独立核此点）。
R1 报告事实更正（s1b_tests_report_egg.md §4）：旧报告「后续（重试成功/
可用性）断言 PASS」无执行证据——T13.1 在半装断言失败处即止，后续重试代码
未执行；原 raw 保持不改，修后实际跑到重试方可称重试验证。T11.5 原孤儿
record=None 成功期望系测试侧错误固定（Sol 正式补核 F4/P2），非产品行为红、
非冻结合同。运行协议：
全新纯源码箱、venv312 Python 3.12.13 -I -B、绝对 scratch DATA、短 E
TEMP/TMP/TMPDIR/RF_R9_TMP*、箱身份/源码 hash/defaultdata 初末核验全打印、
argv/cwd/env 覆盖/exit/stdout/stderr 逐 run 编号日志不覆盖；禁主仓 app/API/
pytest、真实 data/8613、网络/凭据、旧证据箱写入。
如实残差（已呈报主控）：dict 映射型 row_factory（tuple(dict) 取键序）下
append 位置读取会碎——冻结合同只承诺默认 tuple 与 sqlite.Row 两种 caller
工厂（不扩 dict 工厂支持）；task_id 仅「非空文本」无 strip 规则（纯空白串
低层当前未拒绝，不称用户/冻结合同已裁定允许；S1c 须由可信任务解析产生
规范 strip 后非空 info.task_id）；
ProgrammingError 原样上抛为产品内部口径，无外部可触发的稳定注入面，不在
矩阵内硬造。

自包含（不依赖 conftest/Safety 夹具、不触 HTTP 层、不导入 db.py——无自愈连接
工厂参与）：REPO_ROOT 由 ``__file__`` 定位，隔离箱内自动指向箱根。断言钉不变式
（双 ID 内容寻址、append-only、borrowed 零副作用、无半写、RO 零创建/fail-raised），
不镜像触发器文本/哈希算法/内部错误文案。无 skip/无全局 ignore/无容差放宽；
异常断言按前置精确族（MetaInvalid/ProofInvalid/ContractError/sqlite3 家族/
OSError/ValueError），不用宽 Exception。静态消费者仅为 unittest 框架；本文件
只新增 test 符号，不改任何既有符号。
"""

from __future__ import annotations

import gc
import hashlib
import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

import pandas as pd  # noqa: E402

from coscreen import screening_arbitration as sarb  # noqa: E402

# ---------------------------------------------------------------------------
# 冻结面（PHASE2 API + 主控错误族）
# ---------------------------------------------------------------------------

API_PREPARE = "prepare_arbitration_attestation_storage"
API_APPEND = "append_decision_arbitration_attested"
API_GET = "get_decision_arbitration_attestation"
API_LIST = "list_decision_arbitration_attestations"
FROZEN_API = (API_PREPARE, API_APPEND, API_GET, API_LIST)

META_KEYS = frozenset({
    "task_id", "round", "required_names", "arbitrator_user_id",
    "arbitrator_role", "input_proof",
})

#: attest 表名按 brief「必须冻结的存储行为」原文冻结，仅用于裸 SQL 观察探针
#:（计数/append-only 触发探测/authorizer 目标），内容断言一律走可信 RO 读出。
ATTEST_TABLE = "screening_arbitration_attestations"
GENERIC_TABLE = "screening_arbitrations"

#: 红侧失败输出定位标签。
TAG_PREPARE = "S1b-T1 prepare/maintenance-transaction"
TAG_SHAPE = "S1b-T2 double-id shape/generic-equivalence/round/role-snapshot"
TAG_RETRY = "S1b-T3 retry identity/time-original"
TAG_PROOF_ACTOR = "S1b-T4 proof-change/actor-change identity"
TAG_ISOLATION = "S1b-T5 meta-dimension isolation/multi-attest/list filters"
TAG_APPENDONLY = "S1b-T6 append-only/generic gates/supersedes"
TAG_META = "S1b-T7 meta shape strict"
TAG_PROOF = "S1b-T8 proof shape strict"
TAG_BORROW = "S1b-T9 borrowed-conn contract"
TAG_ROLLBACK = "S1b-T10 authorizer failure no-half-write"
TAG_RO = "S1b-T11 trusted RO semantics"
TAG_ANCHOR = "S1b-T12 old-generic anchors"
TAG_PREPARE_FAIL = "S1b-T13 prepare install-failure whole-DDL rollback"
#: R1 限定回归（fix-r1）红侧定位标签：目录伪装存储缺失 / 持久 JSON 字段损坏。
TAG_RO_FIXR1 = "S1b-F2F3F4 store-unavailable typed contracts (fix-r1)"

FIXED_ISO = "2026-11-14T09:00:00.000Z"


def _sha256_file(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _hex32(seed: str) -> str:
    """确定性 32 位小写 hex（证明 library_id 形状夹具，非真实账本库）。"""
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:32]


def _require_frozen_api() -> None:
    missing = [n for n in FROZEN_API if not callable(getattr(sarb, n, None))]
    if missing:
        raise AssertionError(
            "FROZEN_S1B_API_ABSENT: " + ",".join(missing)
            + "（before 侧如实：S1b PHASE2 冻结 API 未实现，本回归无法运行；"
              "不以 ImportError 冒充行为红证）")


def _require_frozen_errors() -> None:
    names = ("ArbitrationAttestationMetaInvalid", "ArbitrationAttestationProofInvalid",
             "ArbitrationConnectionContractError")
    missing = [n for n in names if not isinstance(getattr(sarb, n, None), type)]
    if missing:
        raise AssertionError(
            "FROZEN_S1B_ERROR_CLASSES_ABSENT: " + ",".join(missing)
            + "（主控冻结错误族未实现）")


# ---------------------------------------------------------------------------
# 夹具构造（全部经真实旧 generic helpers，不手工伪造合并表）
# ---------------------------------------------------------------------------

def _reviewer_frame(rows) -> pd.DataFrame:
    return pd.DataFrame(
        rows,
        columns=["zotero_key", "decision", "exclusion_reason", "notes", "tags", "screened_at"],
    )


def _merged_two_reviewers() -> pd.DataFrame:
    """2-reviewer 有效合并表：K1 agree(include)、K2 conflict、K3 agree(exclude)、
    K4 conflict（alice exclude/bob include）。"""
    alice = _reviewer_frame([
        ("K1", "include", "", "", "", "2026-11-14T10:00:00Z"),
        ("K2", "include", "", "", "", "2026-11-14T10:00:01Z"),
        ("K3", "exclude", "人群不符", "", "", "2026-11-14T10:00:02Z"),
        ("K4", "exclude", "研究设计不符", "", "", "2026-11-14T10:00:03Z"),
    ])
    bob = _reviewer_frame([
        ("K1", "include", "", "", "", "2026-11-14T11:00:00Z"),
        ("K2", "exclude", "主题不符合", "", "", "2026-11-14T11:00:01Z"),
        ("K3", "exclude", "人群不符", "", "", "2026-11-14T11:00:02Z"),
        ("K4", "include", "", "", "", "2026-11-14T11:00:03Z"),
    ])
    return sarb.merge_decision_packages({"alice": alice, "bob": bob})


def _base_resolutions() -> list:
    return [{"zotero_key": "K2", "decision": "include", "rationale": "采信 alice 的纳入判断"}]


def _multi_resolutions() -> list:
    return [
        {"zotero_key": "K2", "decision": "include", "rationale": "采信 alice 的纳入判断"},
        {"zotero_key": "K4", "decision": "exclude", "rationale": "研究设计不符，仲裁排除"},
    ]


def _proof(keys=("K2",), revisions=None, libs=None) -> dict:
    """input_proof 夹具：item_key→user_id(str)→{library_id:32hex, revision:正整数}。

    revisions/libs 为 {member: 值} 覆盖；未覆盖默认 revision=1、库 ID _hex32(member)。
    """
    revs = {"alice": 1, "bob": 1}
    revs.update(revisions or {})
    lib_map = {"alice": _hex32("lib-alice-v1"), "bob": _hex32("lib-bob-v1")}
    lib_map.update(libs or {})
    return {
        key: {name: {"library_id": lib_map[name], "revision": revs[name]}
              for name in ("alice", "bob")}
        for key in keys
    }


def _base_meta(**overrides) -> dict:
    meta = {
        "task_id": "task-attest-216",
        "round": None,
        "required_names": ["alice", "bob"],
        "arbitrator_user_id": 101,
        "arbitrator_role": "reconciler",
        "input_proof": _proof(("K2",)),
    }
    meta.update(overrides)
    return meta


# ---------------------------------------------------------------------------
# 观察/断言辅助（纯 SQLite/路径/来源；全部在 smoke1 实证可执行）
# ---------------------------------------------------------------------------

def _raw_conn(db_path) -> sqlite3.Connection:
    """裸 sqlite3 连接（不触任何产品连接工厂；观察失败态避免自愈污染）。"""
    conn = sqlite3.connect(str(db_path), timeout=15)
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _object_names(db_path) -> set:
    with closing(_raw_conn(db_path)) as conn:
        rows = conn.execute("SELECT name, type FROM sqlite_master").fetchall()
    return {(name, typ) for name, typ in rows}


def _raw_counts(db_path):
    """(generic 行数, attest 行数或 None[表不存在])——裸只读观察。"""
    with closing(_raw_conn(db_path)) as conn:
        gen = conn.execute(
            f"SELECT count(*) FROM {GENERIC_TABLE}").fetchone()[0]
        present = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (ATTEST_TABLE,)).fetchone()
        attest = None
        if present:
            attest = conn.execute(
                f"SELECT count(*) FROM {ATTEST_TABLE}").fetchone()[0]
    return gen, attest


#: attestation 读出字段的合同别名（键拼写属产品自由度；值断言严格）。
_ATTEST_FIELDS = {
    "id": ("id", "attestation_id"),
    "record_id": ("record_id",),
    "task_id": ("task_id", "task"),
    "round": ("round_id", "round"),
    "stage": ("stage",),
    "arbitrator_user_id": ("arbitrator_user_id",),
    "arbitrator_role": ("arbitrator_role", "role"),
    "required_names": ("required_names", "required_names_json"),
    "input_proof": ("input_proof", "input_proof_json"),
    "resolutions_digest": ("resolutions_digest",),
    "attest_sha256": ("attest_sha256",),
    "attested_at": ("attested_at",),
}


def _as_attestation(obj, tag: str = ""):
    """RO 读出为与 append 返回同构的四键组合行；本 helper 取出 attestation 部分。

    append 返回与 RO 行均形如 {record, attestation, record_id, attestation_id}；
    若传入已是 attestation 明细（含 attest_sha256）则原样返回。"""
    if isinstance(obj, dict) and "attestation" in obj and "attest_sha256" not in obj:
        return obj["attestation"]
    if isinstance(obj, dict) and "attest_sha256" in obj:
        return obj
    raise AssertionError(f"{tag}: 读出对象既非组合行也非 attestation 明细: {sorted(obj) if isinstance(obj, dict) else obj}")


def _att_field(attestation, field: str, tag: str):
    assert field in _ATTEST_FIELDS, field
    aliases = _ATTEST_FIELDS[field]
    present = [a for a in aliases if a in attestation]
    if not present:
        raise AssertionError(
            f"{tag}: attestation 缺合同字段 {field}（别名 {aliases}）；"
            f"实际键={sorted(attestation)}")
    value = attestation[present[0]]
    # 结构字段若以 JSON 字符串载体存储，解析为结构——合同要求 RO 返回完整来源
    # 数据供 S1c 逐键裁适用，结构可解析是最低可用性要求。
    if isinstance(value, str) and field in ("required_names", "input_proof"):
        value = json.loads(value)
    return value


def _parse_aware_utc(value: str) -> datetime:
    """合法 aware UTC 时间解析（'+00:00Z' 双后缀等非法串直接抛 ValueError）。"""
    if not isinstance(value, str):
        raise AssertionError(f"timestamp not str: {value!r}")
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None or dt.utcoffset() != timedelta(0):
        raise AssertionError(f"not aware UTC (offset 0): {value!r}")
    return dt


def _assert_real_time_window(testcase, value, t0, t1, tag) -> datetime:
    """合法 aware UTC 且落在真实调用窗口内（毫秒截断允许下界贴齐；不硬钉严格递增）。"""
    dt = _parse_aware_utc(value) if isinstance(value, str) else None
    testcase.assertIsInstance(dt, datetime, tag + " 时间必须可解析为 aware UTC")
    lower = t0.replace(microsecond=(t0.microsecond // 1000) * 1000)
    testcase.assertLessEqual(lower, dt, tag + f" 时间早于真实调用窗口下界: {value}")
    testcase.assertLessEqual(dt, t1, tag + f" 时间晚于真实调用窗口上界: {value}")
    return dt


class _AttestBox(unittest.TestCase):
    """隔离数据根 + 箱身份/来源核验（contract 隔离与交付节）。"""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory(prefix="rf216_s1b_attest_")
        cls.root = Path(cls._tmp.name)

        def _cleanup():
            gc.collect()
            cls._tmp.cleanup()

        cls.addClassCleanup(_cleanup)
        # 箱身份：导入面必须全部落在 REPO_ROOT（隔离纯源码箱）内，主仓不参与。
        for module in (sarb,):
            src = Path(module.__file__).resolve()
            assert src.is_relative_to(REPO_ROOT), (module.__name__, str(src))
        assert Path(sarb.__file__).resolve().parent == (REPO_ROOT / "coscreen").resolve()
        print("S1B_TEST_FILE_SHA", _sha256_file(__file__))
        print("S1B_SOURCE_ARB_SHA", _sha256_file(sarb.__file__))
        print("S1B_REPO_ROOT", str(REPO_ROOT))
        print("S1B_PYTHON", sys.version.split()[0])
        print("S1B_SQLITE_VERSION", sqlite3.sqlite_version)
        print("S1B_ISOLATED_FLAG", sys.flags.isolated)
        print("S1B_DONT_WRITE_BC", sys.flags.dont_write_bytecode)
        print("S1B_CWD", str(Path.cwd()))
        print("S1B_DEFAULT_DATA_ABSENT_PRE", not (REPO_ROOT / "data").exists())
        assert not (REPO_ROOT / "data").exists(), "隔离箱内不得落 default data 目录"
        cls.addClassCleanup(cls._post_box_checks)

    @classmethod
    def _post_box_checks(cls):
        absent = not (REPO_ROOT / "data").exists()
        print("S1B_DEFAULT_DATA_ABSENT_POST", absent)
        assert absent, "隔离箱内不得落 default data 目录"

    def setUp(self):
        self.base = self.root / self.id().rsplit(".", 1)[-1]
        self.base.mkdir(parents=True, exist_ok=True)

    # -- 常用构造 -----------------------------------------------------------
    def _store(self, name="attest.db") -> Path:
        return self.base / name

    def _prepared(self, name="attest.db") -> Path:
        store = self._store(name)
        result = getattr(sarb, API_PREPARE)(store)
        self.assertIsNone(result, TAG_PREPARE + " prepare 返回必须为 None")
        return store

    def _caller_conn(self, store, row_factory=sqlite3.Row) -> sqlite3.Connection:
        """协作库 caller 连接（显式 BEGIN IMMEDIATE；与生产同款默认隔离级别）。"""
        conn = sqlite3.connect(str(store), timeout=15)
        conn.row_factory = row_factory
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("BEGIN IMMEDIATE")
        return conn

    def _append_once(self, store, *, stage="stage1", merged=None, resolutions=None,
                     arbitrator_id="alice", meta=None, row_factory=sqlite3.Row):
        """一次完整 caller 事务流：BEGIN → append_attested → commit（返回结果并关闭）。"""
        conn = self._caller_conn(store, row_factory=row_factory)
        try:
            result = getattr(sarb, API_APPEND)(
                conn,
                stage=stage,
                merged=_merged_two_reviewers() if merged is None else merged,
                resolutions=_base_resolutions() if resolutions is None else resolutions,
                arbitrator_id=arbitrator_id,
                attestation_meta=_base_meta() if meta is None else meta,
            )
            conn.commit()
        finally:
            conn.close()
        return result

    def _seed_session_fixture(self, store) -> None:
        """模拟 session 提交状态表（epoch/partial submitted 语义的最小替身）。"""
        with closing(_raw_conn(store)) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS team_attest_session_fixture("
                "epoch INTEGER NOT NULL, submitted_keys TEXT NOT NULL, updated_at TEXT NOT NULL)")
            conn.execute(
                "INSERT INTO team_attest_session_fixture VALUES (0, '[]', ?)", (FIXED_ISO,))
            conn.commit()

    def _bump_session(self, conn) -> None:
        conn.execute(
            "UPDATE team_attest_session_fixture SET epoch=epoch+1, submitted_keys=?, updated_at=? "
            "WHERE epoch=(SELECT max(epoch) FROM team_attest_session_fixture)",
            ('["K2"]', FIXED_ISO))

    def _session_epoch(self, store):
        with closing(_raw_conn(store)) as conn:
            row = conn.execute(
                "SELECT epoch FROM team_attest_session_fixture ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
        return None if row is None else row[0]

    def _attest_ids(self, store):
        return [_att_field(_as_attestation(row, TAG_RO), "id", TAG_RO)
                for row in getattr(sarb, API_LIST)(store)]

    # -- attested 结果公用断言 ----------------------------------------------
    def _assert_result_shape(self, result, tag):
        self.assertIsInstance(result, dict, tag)
        self.assertEqual(
            set(result), {"record", "attestation", "record_id", "attestation_id"},
            tag + " 冻结返回形状恰四键")
        self.assertEqual(result["record_id"], result["record"]["id"], tag)
        self.assertEqual(result["attestation_id"], _att_field(
            result["attestation"], "id", tag), tag)

    def _assert_attestation_content(self, attestation, *, meta, stage, tag):
        self.assertEqual(_att_field(attestation, "task_id", tag), meta["task_id"],
                         tag + " task_id 原样存取")
        self.assertIsNone(_att_field(attestation, "round", tag), tag + " round None 原样")
        self.assertEqual(_att_field(attestation, "stage", tag), stage, tag)
        self.assertEqual(_att_field(attestation, "arbitrator_user_id", tag),
                         meta["arbitrator_user_id"], tag + " 当前 UID 原样存取")
        self.assertEqual(_att_field(attestation, "arbitrator_role", tag),
                         meta["arbitrator_role"], tag + " role 快照逐字保存")
        self.assertEqual(_att_field(attestation, "required_names", tag),
                         list(meta["required_names"]), tag + " required 保序存取")
        self.assertEqual(_att_field(attestation, "input_proof", tag),
                         meta["input_proof"], tag + " input_proof 结构化原样存取")
        for field in ("resolutions_digest", "attest_sha256"):
            value = _att_field(attestation, field, tag)
            self.assertIsInstance(value, str, tag + f" {field} 必须为文本")
            self.assertRegex(value, r"^[0-9a-f]{64}$", tag + f" {field} 64 位小写 hex")

    def _assert_attestation_time(self, attestation, t0, t1, tag):
        value = _att_field(attestation, "attested_at", tag)
        _assert_real_time_window(self, value, t0, t1, tag)
        return value


class _FrozenApiBox(_AttestBox):
    """依赖冻结 API 的用例基类：setUpClass 如实拒绝 before 侧（不伪造行为红）。"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        _require_frozen_api()
        _require_frozen_errors()

    @property
    def MetaInvalid(self):
        return sarb.ArbitrationAttestationMetaInvalid

    @property
    def ProofInvalid(self):
        return sarb.ArbitrationAttestationProofInvalid

    @property
    def ContractError(self):
        return sarb.ArbitrationConnectionContractError


# ---------------------------------------------------------------------------
# T1 prepare 维护事务
# ---------------------------------------------------------------------------

class T1_PrepareStorageTests(_FrozenApiBox):

    def test_01_missing_file_creates_store_returns_none_idempotent(self):
        store_dir = self.base / "fresh_dir"
        store = store_dir / "attest.db"
        self.assertFalse(store.exists(), TAG_PREPARE)
        result = sarb.prepare_arbitration_attestation_storage(store)
        self.assertIsNone(result, TAG_PREPARE + " prepare 返回必须为 None")
        self.assertTrue(store.is_file(), TAG_PREPARE + " prepare 须建库（自有事务可建 schema）")
        # 幂等：重复 prepare 零破坏
        self.assertIsNone(sarb.prepare_arbitration_attestation_storage(store), TAG_PREPARE)
        self.assertIsNone(sarb.prepare_arbitration_attestation_storage(store), TAG_PREPARE)
        # 空库 RO：零行、零创建副作用
        self.assertEqual(sarb.list_decision_arbitration_attestations(store), [], TAG_PREPARE)
        self.assertIsNone(sarb.get_decision_arbitration_attestation(store, 1), TAG_PREPARE)
        gen, attest = _raw_counts(store)
        self.assertEqual((gen, attest), (0, 0), TAG_PREPARE)

    def test_02_prepare_existing_store_preserves_generic_data(self):
        store = self._store("generic_first.db")
        merged = _merged_two_reviewers()
        rec = sarb.append_decision_arbitration(
            store, stage="stage1", merged=merged,
            resolutions=_base_resolutions(), arbitrator_id="alice")
        before_names = _object_names(store)
        self.assertIsNone(sarb.prepare_arbitration_attestation_storage(store), TAG_PREPARE)
        self.assertIsNone(sarb.prepare_arbitration_attestation_storage(store), TAG_PREPARE)
        # generic 记录逐字段零破坏（含 id/created_at 原值）
        self.assertEqual(sarb.get_decision_arbitration(store, rec["id"]), rec,
                         TAG_PREPARE + " 既有 generic 数据零破坏")
        self.assertEqual(sarb.latest_decision_arbitration(store, "stage1")["id"], rec["id"],
                         TAG_PREPARE)
        # 既有 generic store 上 attest 表为空
        self.assertEqual(sarb.list_decision_arbitration_attestations(store), [], TAG_PREPARE)
        # prepare 不得改动既有 generic 对象（只增不删不改名）
        after_names = _object_names(store)
        self.assertTrue(before_names <= after_names,
                        TAG_PREPARE + f" 既有对象被删改: {before_names - after_names}")

    def test_03_prepare_makes_fresh_store_append_ready_and_stays_idempotent(self):
        """文档化生产流：prepare（窗口前）→ caller BEGIN → append_attested → commit。"""
        store = self._prepared("ready.db")
        self._seed_session_fixture(store)
        conn = self._caller_conn(store)
        try:
            t0 = datetime.now(timezone.utc)
            result = sarb.append_decision_arbitration_attested(
                conn, stage="stage1", merged=_merged_two_reviewers(),
                resolutions=_base_resolutions(), arbitrator_id="alice",
                attestation_meta=_base_meta())
            t1 = datetime.now(timezone.utc)
            self.assertTrue(conn.in_transaction, TAG_PREPARE + " append 不得终结 caller 事务")
            self._bump_session(conn)
            conn.commit()
        finally:
            conn.close()
        self._assert_result_shape(result, TAG_PREPARE)
        self._assert_attestation_content(result["attestation"], meta=_base_meta(),
                                         stage="stage1", tag=TAG_PREPARE)
        self._assert_attestation_time(result["attestation"], t0, t1, TAG_PREPARE)
        gen, attest = _raw_counts(store)
        self.assertEqual((gen, attest), (1, 1), TAG_PREPARE)
        self.assertEqual(self._session_epoch(store), 1, TAG_PREPARE + " session 状态同事务提交")
        # 数据在库后 prepare 仍幂等且零破坏
        self.assertIsNone(sarb.prepare_arbitration_attestation_storage(store), TAG_PREPARE)
        self.assertEqual(len(sarb.list_decision_arbitration_attestations(store)), 1, TAG_PREPARE)


# ---------------------------------------------------------------------------
# T2 append 双 ID 形状 / generic 等价对照 / round 与 role 快照
# ---------------------------------------------------------------------------

class T2_AppendShapeTests(_FrozenApiBox):

    def test_01_double_id_shape_and_generic_field_equivalence_vs_old_call(self):
        merged = _merged_two_reviewers()
        resolutions = _base_resolutions()
        # 对照：旧 generic 函数真实调用（非抄算法），同输入在对照库的 generic 记录。
        control = self._store("control.db")
        rec_old = sarb.append_decision_arbitration(
            control, stage="stage1", merged=merged,
            resolutions=resolutions, arbitrator_id="alice")
        # 主库：attested 路径。
        store = self._prepared("main.db")
        t0 = datetime.now(timezone.utc)
        result = self._append_once(store, merged=merged, resolutions=resolutions,
                                   arbitrator_id="alice")
        t1 = datetime.now(timezone.utc)
        self._assert_result_shape(result, TAG_SHAPE)
        record = result["record"]
        # generic 记录逐字段语义等价（id/created_at 属各库真实值，不跨库比较）。
        for field in ("stage", "merge_sha256", "result_sha256", "supersedes_id",
                      "arbitrator_id", "records"):
            self.assertEqual(record[field], rec_old[field],
                             TAG_SHAPE + f" generic {field} 必须与旧 generic 调用等价")
        self.assertEqual(record["merge_sha256"], rec_old["merge_sha256"],
                         TAG_SHAPE + " merge 哈希逐字段等价（hash 不变）")
        # generic 记录可经旧 generic API 读回（旧读路径零改动）。
        got = sarb.get_decision_arbitration(store, result["record_id"])
        for field in ("stage", "merge_sha256", "result_sha256", "supersedes_id",
                      "arbitrator_id", "records"):
            self.assertEqual(got[field], record[field],
                             TAG_SHAPE + f" 旧 get 读回 {field} 与 append 一致")
        self.assertEqual(sarb.latest_decision_arbitration(store, "stage1")["id"],
                         result["record_id"], TAG_SHAPE + " generic latest 反映新记录")
        # attestation 内容 + 真实时间。
        self._assert_attestation_content(result["attestation"], meta=_base_meta(),
                                         stage="stage1", tag=TAG_SHAPE)
        self._assert_attestation_time(result["attestation"], t0, t1, TAG_SHAPE)
        self.assertEqual(_att_field(result["attestation"], "record_id", TAG_SHAPE),
                         result["record_id"], TAG_SHAPE + " attest 引用本次 generic 记录")

    def test_02_round_explicit_values_passthrough_verbatim(self):
        store = self._prepared("round.db")
        named = self._append_once(store, meta=_base_meta(round="r7"))
        self.assertEqual(_att_field(named["attestation"], "round", TAG_SHAPE), "r7",
                         TAG_SHAPE + " 显式 round 原样存值（不丢弃非 None 伪绑定）")
        none_round = self._append_once(store)
        self.assertIsNone(_att_field(none_round["attestation"], "round", TAG_SHAPE),
                          TAG_SHAPE + " 显式 None 存 NULL")
        self.assertNotEqual(named["attestation_id"], none_round["attestation_id"],
                            TAG_SHAPE + " round 参与证明身份")

    def test_03_role_value_domain_currently_reconciler_snapshot_not_authorizer(self):
        """主控 API 还原：有效 roles 仅 reconciler（快照取值域）——'reconciler'
        合法保存；其他角色串 MetaInvalid。低层只存快照，不查授权名册（授权与
        all-complete 语义属后续 trusted runtime）。"""
        store = self._prepared("role.db")
        result = self._append_once(store, meta=_base_meta(arbitrator_role="reconciler"))
        self.assertEqual(_att_field(result["attestation"], "arbitrator_role", TAG_SHAPE),
                         "reconciler", TAG_SHAPE + " role 快照逐字保存")
        conn = self._caller_conn(store)
        try:
            for bad_role in ("admin", "arbitrator-role-fixture", "Reconciler", "reconciler "):
                with self.subTest(bad_role=bad_role):
                    with self.assertRaises(
                            sarb.ArbitrationAttestationMetaInvalid,
                            msg=TAG_SHAPE + f" 角色 {bad_role!r} 不在当前取值域"):
                        sarb.append_decision_arbitration_attested(
                            conn, stage="stage1", merged=_merged_two_reviewers(),
                            resolutions=_base_resolutions(), arbitrator_id="alice",
                            attestation_meta=_base_meta(arbitrator_role=bad_role))
        finally:
            conn.rollback()
            conn.close()
        self.assertEqual(_raw_counts(store), (1, 1),
                         TAG_SHAPE + " 非法角色拒绝零写入（仅既有 1 行）")


# ---------------------------------------------------------------------------
# T3 重试身份
# ---------------------------------------------------------------------------

class T3_RetryIdentityTests(_FrozenApiBox):

    def test_01_same_input_retry_same_double_id_and_time_original(self):
        store = self._prepared("retry.db")
        merged = _merged_two_reviewers()
        meta = _base_meta()
        first = self._append_once(store, merged=merged, meta=meta)
        # 崩溃/重试＝新 caller 事务、同输入同裁决。
        retry = self._append_once(store, merged=merged, meta=meta)
        self.assertEqual(retry["record_id"], first["record_id"], TAG_RETRY + " generic ID 同")
        self.assertEqual(retry["attestation_id"], first["attestation_id"],
                         TAG_RETRY + " attest ID 同（attest_sha 幂等命中）")
        self.assertEqual(_att_field(retry["attestation"], "attested_at", TAG_RETRY),
                         _att_field(first["attestation"], "attested_at", TAG_RETRY),
                         TAG_RETRY + " 时间原（返回原始行，不刷新）")
        self.assertEqual(_att_field(retry["attestation"], "attest_sha256", TAG_RETRY),
                         _att_field(first["attestation"], "attest_sha256", TAG_RETRY))
        self.assertEqual(len(sarb.list_decision_arbitration_attestations(store)), 1,
                         TAG_RETRY + " 重试不追加行")
        self.assertEqual(_raw_counts(store), (1, 1), TAG_RETRY)

    def test_02_retry_after_other_attestation_keeps_original_identity(self):
        """内容寻址幂等：中间插入其他 attestation 后，原输入重试仍返回原 ID。"""
        store = self._prepared("retry_mid.db")
        first = self._append_once(store)
        other = self._append_once(
            store, meta=_base_meta(
                input_proof=_proof(("K2",), revisions={"alice": 2})))
        self.assertGreater(other["attestation_id"], first["attestation_id"],
                           TAG_RETRY + " 不同输入新 attestation ID 递增")
        retry = self._append_once(store)
        self.assertEqual(retry["attestation_id"], first["attestation_id"],
                         TAG_RETRY + " 原输入重试返回原 ID（非 latest 覆盖）")
        ids = self._attest_ids(store)
        self.assertEqual(sorted(ids), ids, TAG_RETRY + " 追加序 id 单调")
        self.assertEqual(len(ids), 2, TAG_RETRY)


# ---------------------------------------------------------------------------
# T4 proof 变 / actor 换身份
# ---------------------------------------------------------------------------

class T4_ProofAndActorTests(_FrozenApiBox):

    def test_01_proof_revision_changed_same_content_new_attest_real_new_time(self):
        """A 改票改回（generic 内容全同）→ generic ID 同；proof 版本变必须新
        attestation ID + 真实新时间（真实时钟窗口断言，不硬钉严格递增）。"""
        store = self._prepared("proof_change.db")
        merged = _merged_two_reviewers()
        meta1 = _base_meta()
        first = self._append_once(store, merged=merged, meta=meta1)
        t0 = datetime.now(timezone.utc)
        second = self._append_once(
            store, merged=merged,
            meta=_base_meta(input_proof=_proof(("K2",), revisions={"alice": 2})))
        t1 = datetime.now(timezone.utc)
        self.assertEqual(second["record_id"], first["record_id"],
                         TAG_PROOF_ACTOR + " 内容全同 generic ID 同（幂等命中）")
        self.assertGreater(second["attestation_id"], first["attestation_id"],
                           TAG_PROOF_ACTOR + " proof 版本变必须新 attestation ID")
        self.assertNotEqual(
            _att_field(second["attestation"], "attest_sha256", TAG_PROOF_ACTOR),
            _att_field(first["attestation"], "attest_sha256", TAG_PROOF_ACTOR),
            TAG_PROOF_ACTOR + " attest_sha 区分 proof 版本")
        # 真实新时间：落在第二次调用的真实窗口内（若复用旧时间必然早于窗口下界）。
        new_time = self._assert_attestation_time(second["attestation"], t0, t1,
                                                 TAG_PROOF_ACTOR)
        old_time = _parse_aware_utc(
            _att_field(first["attestation"], "attested_at", TAG_PROOF_ACTOR))
        print("S1B_T4_PROOF_CHANGE_TIMES", old_time.isoformat(), new_time.isoformat()
              if isinstance(new_time, datetime) else new_time)
        # 旧 attestation 原样可读（append-only 不被覆盖改写）。
        got_old = sarb.get_decision_arbitration_attestation(store, first["attestation_id"])
        old_att = _as_attestation(got_old, TAG_PROOF_ACTOR)
        self.assertEqual(_att_field(old_att, "input_proof", TAG_PROOF_ACTOR),
                         meta1["input_proof"], TAG_PROOF_ACTOR + " 旧行 proof 原样保留")
        self.assertEqual(_att_field(old_att, "attested_at", TAG_PROOF_ACTOR),
                         _att_field(first["attestation"], "attested_at", TAG_PROOF_ACTOR),
                         TAG_PROOF_ACTOR + " 旧行时间不被改写")
        self.assertEqual(len(sarb.list_decision_arbitration_attestations(
            store, record_id=first["record_id"])), 2, TAG_PROOF_ACTOR)

    def test_02_actor_change_new_attest_carries_current_uid_not_old_identity(self):
        """actor 换：新 attestation 的证明身份＝本次 meta 当前 UID；不得把 generic
        旧 arbitrator 当新证明身份（generic 按冻结哈希规则另立新记录）。"""
        store = self._prepared("actor.db")
        first = self._append_once(store, arbitrator_id="alice",
                                  meta=_base_meta(arbitrator_user_id=101))
        # 对照库：bob 同内容旧 generic 调用（哈希规则含 arbitrator → 新 generic 记录）。
        control = self._store("actor_control.db")
        rec_bob = sarb.append_decision_arbitration(
            control, stage="stage1", merged=_merged_two_reviewers(),
            resolutions=_base_resolutions(), arbitrator_id="bob")
        t0 = datetime.now(timezone.utc)
        second = self._append_once(store, arbitrator_id="bob",
                                   meta=_base_meta(arbitrator_user_id=202))
        t1 = datetime.now(timezone.utc)
        self.assertNotEqual(second["record_id"], first["record_id"],
                            TAG_PROOF_ACTOR + " 冻结 generic 哈希含 arbitrator → 新 generic 记录")
        self.assertEqual(second["record"]["arbitrator_id"], "bob",
                         TAG_PROOF_ACTOR + " generic 记录带本次 actor")
        # supersedes_id 属库内拓扑（主库有前驱、对照库没有），不跨库比较，
        # 另行断言主库链：同 stage 同 merge 前驱 = first。
        for field in ("stage", "merge_sha256", "result_sha256", "records"):
            self.assertEqual(second["record"][field], rec_bob[field],
                             TAG_PROOF_ACTOR + f" generic {field} 与旧 generic 调用等价")
        self.assertEqual(second["record"]["supersedes_id"], first["record_id"],
                         TAG_PROOF_ACTOR + " 同 stage 同 merge 前驱 supersedes 链保持")
        self.assertEqual(_att_field(second["attestation"], "arbitrator_user_id",
                                    TAG_PROOF_ACTOR), 202,
                         TAG_PROOF_ACTOR + " 新 attest 用当前 UID（非旧 101/非 generic 旧身份）")
        self.assertEqual(_att_field(second["attestation"], "record_id", TAG_PROOF_ACTOR),
                         second["record_id"], TAG_PROOF_ACTOR + " attest 引用本次 generic")
        self._assert_attestation_time(second["attestation"], t0, t1, TAG_PROOF_ACTOR)
        self.assertNotEqual(
            _att_field(second["attestation"], "attest_sha256", TAG_PROOF_ACTOR),
            _att_field(first["attestation"], "attest_sha256", TAG_PROOF_ACTOR),
            TAG_PROOF_ACTOR + " arbitrator 参与证明身份")


# ---------------------------------------------------------------------------
# T5 维度隔离 / 多 attest 同 generic / list 过滤
# ---------------------------------------------------------------------------

class T5_IsolationAndListTests(_FrozenApiBox):

    def test_01_meta_dimension_isolation_new_attest_same_generic(self):
        """task/round/required 内容/required 保序/proof revision/proof library_id
        任一变 → 新 attestation；merged/resolutions/actor 不变 → 同 generic 记录。"""
        store = self._prepared("isolation.db")
        merged = _merged_two_reviewers()
        base = self._append_once(store, merged=merged)
        variants = {
            "task": _base_meta(task_id="task-attest-other"),
            "round": _base_meta(round="r1"),
            "required_content": _base_meta(required_names=["alice", "bob", "carol"]),
            "required_order": _base_meta(required_names=["bob", "alice"]),
            "proof_revision": _base_meta(input_proof=_proof(("K2",), revisions={"bob": 3})),
            "proof_library": _base_meta(
                input_proof=_proof(("K2",), libs={"bob": _hex32("lib-bob-v2")})),
        }
        prev_id = base["attestation_id"]
        for label, meta in variants.items():
            result = self._append_once(store, merged=merged, meta=meta)
            self.assertGreater(result["attestation_id"], prev_id,
                               TAG_ISOLATION + f" {label} 变体必须新 attestation ID")
            self.assertEqual(result["record_id"], base["record_id"],
                             TAG_ISOLATION + f" {label} 变体不影响 generic 内容 → 同 generic 记录")
            self.assertNotEqual(
                _att_field(result["attestation"], "attest_sha256", TAG_ISOLATION),
                _att_field(base["attestation"], "attest_sha256", TAG_ISOLATION),
                TAG_ISOLATION + f" {label} 变体 attest_sha 区分")
            prev_id = result["attestation_id"]
        # 多 attestation 引用同一 generic 记录（1 base + 6 variants）。
        rows = sarb.list_decision_arbitration_attestations(store, record_id=base["record_id"])
        self.assertEqual(len(rows), 7, TAG_ISOLATION + " 多 attest 引用同 generic")
        ids = self._attest_ids(store)
        self.assertEqual(sorted(ids), ids, TAG_ISOLATION + " 追加序单调")
        # 每个 variant 的对应字段确实存了变体值（保序/内容可读回）。
        # 追加序：0=base, 1=task, 2=round, 3=required_content, 4=required_order,
        # 5=proof_revision, 6=proof_library（variants dict 插入序）。
        rows = sarb.list_decision_arbitration_attestations(store)
        by_id = {_att_field(_as_attestation(r, TAG_ISOLATION), "id", TAG_ISOLATION): r
                 for r in rows}
        self.assertEqual(_att_field(_as_attestation(by_id[ids[4]], TAG_ISOLATION),
                                    "required_names", TAG_ISOLATION),
                         ["bob", "alice"], TAG_ISOLATION + " required 保序读回")
        self.assertEqual(_att_field(_as_attestation(by_id[ids[3]], TAG_ISOLATION),
                                    "required_names", TAG_ISOLATION),
                         ["alice", "bob", "carol"], TAG_ISOLATION + " required 内容读回")
        self.assertEqual(_att_field(_as_attestation(by_id[ids[5]], TAG_ISOLATION),
                                    "input_proof", TAG_ISOLATION),
                         _proof(("K2",), revisions={"bob": 3}),
                         TAG_ISOLATION + " proof revision 变体读回")
        self.assertEqual(_att_field(_as_attestation(by_id[ids[6]], TAG_ISOLATION),
                                    "input_proof", TAG_ISOLATION),
                         _proof(("K2",), libs={"bob": _hex32("lib-bob-v2")}),
                         TAG_ISOLATION + " 同名新库 library_id 不同不与旧证明混同")

    def test_02_stage_and_resolutions_isolation_new_generic_and_attest(self):
        store = self._prepared("stage_res.db")
        merged = _merged_two_reviewers()
        base = self._append_once(store, merged=merged)
        stage2 = self._append_once(store, merged=merged, stage="stage2",
                                   meta=_base_meta())
        self.assertNotEqual(stage2["record_id"], base["record_id"],
                            TAG_ISOLATION + " stage 进 generic 哈希 → 新 generic 记录")
        self.assertEqual(_att_field(stage2["attestation"], "stage", TAG_ISOLATION), "stage2",
                         TAG_ISOLATION)
        changed = self._append_once(store, merged=merged,
                                    resolutions=_multi_resolutions(),
                                    meta=_base_meta(input_proof=_proof(("K2", "K4"))))
        self.assertNotEqual(changed["record_id"], base["record_id"],
                            TAG_ISOLATION + " resolutions 变 → result 哈希变 → 新 generic")
        self.assertNotEqual(
            _att_field(changed["attestation"], "resolutions_digest", TAG_ISOLATION),
            _att_field(base["attestation"], "resolutions_digest", TAG_ISOLATION),
            TAG_ISOLATION + " 结论摘要区分")
        self.assertEqual(len(sarb.list_decision_arbitration_attestations(store, stage="stage1")),
                         2, TAG_ISOLATION)
        self.assertEqual(len(sarb.list_decision_arbitration_attestations(store, stage="stage2")),
                         1, TAG_ISOLATION)
        self.assertEqual(len(sarb.list_decision_arbitration_attestations(store)), 3,
                         TAG_ISOLATION)

    def test_03_list_filters_stage_task_record_and_combinations(self):
        store = self._prepared("filters.db")
        base = self._append_once(store)  # task A, stage1, G1
        proof2 = self._append_once(store, meta=_base_meta(
            input_proof=_proof(("K2",), revisions={"alice": 2})))  # task A, stage1, G1
        round1 = self._append_once(store, meta=_base_meta(round="r1"))  # task A, stage1, G1
        other_task = self._append_once(store, meta=_base_meta(task_id="task-other-216"))
        # task 变体不进 generic 哈希 → other_task 与 base 同 generic G1；stage2 → G2。
        self.assertEqual(other_task["record_id"], base["record_id"],
                         TAG_ISOLATION + " task 变体同 generic 记录")
        stage2 = self._append_once(store, stage="stage2", meta=_base_meta())  # task A, G2

        def ids_of(rows):
            return {_att_field(_as_attestation(r, TAG_ISOLATION), "id", TAG_ISOLATION)
                    for r in rows}

        all_rows = sarb.list_decision_arbitration_attestations(store)
        self.assertEqual(len(all_rows), 5, TAG_ISOLATION + " 全量 5 行")
        # 组合行与 append 返回同构（冻结四键）且引用 generic 全量记录。
        for row in all_rows:
            self.assertEqual(
                set(row), {"record", "attestation", "record_id", "attestation_id"},
                TAG_ISOLATION + " RO 行冻结四键组合形状")
            self.assertEqual(row["record_id"], row["record"]["id"], TAG_ISOLATION)
            self.assertEqual(row["attestation_id"],
                             _att_field(_as_attestation(row, TAG_ISOLATION), "id", TAG_ISOLATION),
                             TAG_ISOLATION)
        self.assertEqual(ids_of(sarb.list_decision_arbitration_attestations(
            store, record_id=base["record_id"])),
            {base["attestation_id"], proof2["attestation_id"], round1["attestation_id"],
             other_task["attestation_id"]},
            TAG_ISOLATION + " record_id 过滤＝同 generic 的全部 attest（含 task 变体）")
        self.assertEqual(ids_of(sarb.list_decision_arbitration_attestations(
            store, record_id=stage2["record_id"])), {stage2["attestation_id"]}, TAG_ISOLATION)
        self.assertEqual(ids_of(sarb.list_decision_arbitration_attestations(
            store, task_id="task-other-216")), {other_task["attestation_id"]},
            TAG_ISOLATION + " task_id 过滤")
        self.assertEqual(len(sarb.list_decision_arbitration_attestations(
            store, task_id="task-attest-216")), 4, TAG_ISOLATION)
        self.assertEqual(ids_of(sarb.list_decision_arbitration_attestations(
            store, stage="stage1", record_id=base["record_id"])),
            {base["attestation_id"], proof2["attestation_id"], round1["attestation_id"],
             other_task["attestation_id"]},
            TAG_ISOLATION + " 组合过滤")
        self.assertEqual(sarb.list_decision_arbitration_attestations(
            store, record_id=stage2["record_id"], stage="stage1"), [],
            TAG_ISOLATION + " 交叉无匹配 → []")
        self.assertEqual(sarb.list_decision_arbitration_attestations(
            store, task_id="task-none"), [], TAG_ISOLATION)
        self.assertEqual(sarb.list_decision_arbitration_attestations(
            store, record_id=base["record_id"] + 500), [], TAG_ISOLATION)
        # 行内容完整（S1c 逐键裁适用所需来源数据齐备）。
        row = sarb.list_decision_arbitration_attestations(
            store, record_id=stage2["record_id"])[0]
        self._assert_attestation_content(
            _as_attestation(row, TAG_ISOLATION), meta=_base_meta(), stage="stage2",
            tag=TAG_ISOLATION)
        # 引用的 generic 记录可经旧 generic API 对读（全量 rows 呈现）。
        self.assertEqual(row["record"]["id"], stage2["record_id"], TAG_ISOLATION)
        self.assertEqual(row["record"]["stage"], "stage2", TAG_ISOLATION)


# ---------------------------------------------------------------------------
# T6 append-only / generic 包门 / supersedes
# ---------------------------------------------------------------------------

class T6_AppendOnlyAndGatesTests(_FrozenApiBox):

    def test_01_attestation_table_update_delete_rejected(self):
        store = self._prepared("appendonly.db")
        result = self._append_once(store)
        before = sarb.list_decision_arbitration_attestations(store)
        with closing(_raw_conn(store)) as conn:
            with self.assertRaises(sqlite3.IntegrityError,
                                   msg=TAG_APPENDONLY + " UPDATE 必须被 append-only 触发拒绝"):
                conn.execute(
                    f"UPDATE {ATTEST_TABLE} SET task_id='tampered' WHERE id=?",
                    (result["attestation_id"],))
            with self.assertRaises(sqlite3.IntegrityError,
                                   msg=TAG_APPENDONLY + " DELETE 必须被 append-only 触发拒绝"):
                conn.execute(f"DELETE FROM {ATTEST_TABLE} WHERE id=?",
                             (result["attestation_id"],))
        after = sarb.list_decision_arbitration_attestations(store)
        self.assertEqual(after, before, TAG_APPENDONLY + " 拒绝后数据原样")

    def test_02_attested_path_does_not_relax_generic_gates(self):
        store = self._prepared("gates.db")
        merged = _merged_two_reviewers()
        conn = self._caller_conn(store)
        try:
            # K1 是 agree 行：generic 仲裁门只允许 conflict 行。
            with self.assertRaises(ValueError,
                                   msg=TAG_APPENDONLY + " 非 conflict 键必须按 generic 门拒绝"):
                sarb.append_decision_arbitration_attested(
                    conn, stage="stage1", merged=merged,
                    resolutions=[{"zotero_key": "K1", "decision": "include",
                                  "rationale": "不该被接受"}],
                    arbitrator_id="alice", attestation_meta=_base_meta())
            self.assertTrue(conn.in_transaction,
                            TAG_APPENDONLY + " 拒绝后 caller 事务仍由 caller 控制")
            # 非法 actor 沿用 generic actor 校验（不放宽包门）。
            with self.assertRaises(ValueError,
                                   msg=TAG_APPENDONLY + " 非法 arbitrator_id 必须 ValueError"):
                sarb.append_decision_arbitration_attested(
                    conn, stage="stage1", merged=merged,
                    resolutions=_base_resolutions(), arbitrator_id=True,
                    attestation_meta=_base_meta())
            conn.rollback()
        finally:
            conn.close()
        self.assertEqual(_raw_counts(store), (0, 0),
                         TAG_APPENDONLY + " 被拒调用零写入")

    def test_03_generic_supersedes_chain_preserved_through_attested_path(self):
        store = self._prepared("supersedes.db")
        merged = _merged_two_reviewers()
        first = self._append_once(store, merged=merged,
                                  resolutions=_base_resolutions())
        second = self._append_once(store, merged=merged,
                                   resolutions=_multi_resolutions(),
                                   meta=_base_meta(input_proof=_proof(("K2", "K4"))))
        got = sarb.get_decision_arbitration(store, second["record_id"])
        self.assertEqual(got["supersedes_id"], first["record_id"],
                         TAG_APPENDONLY + " attested 路径保持 generic supersedes 链")
        self.assertEqual(sarb.latest_decision_arbitration(store, "stage1")["id"],
                         second["record_id"], TAG_APPENDONLY + " generic latest 语义不变")
        # 对照：旧 generic 路径同输入同拓扑。
        control = self._store("supersedes_control.db")
        c1 = sarb.append_decision_arbitration(
            control, stage="stage1", merged=merged, resolutions=_base_resolutions(),
            arbitrator_id="alice")
        c2 = sarb.append_decision_arbitration(
            control, stage="stage1", merged=merged, resolutions=_multi_resolutions(),
            arbitrator_id="alice")
        self.assertEqual(c2["supersedes_id"], c1["id"],
                         TAG_APPENDONLY + " 对照库同拓扑（链行为不因新路径改变）")


# ---------------------------------------------------------------------------
# T7 meta 形状严格
# ---------------------------------------------------------------------------

class T7_MetaShapeTests(_FrozenApiBox):

    def test_01_error_class_contract(self):
        _require_frozen_errors()
        self.assertTrue(issubclass(sarb.ArbitrationAttestationMetaInvalid, ValueError),
                        TAG_META + " MetaInvalid 是 ValueError 子类")
        self.assertEqual(sarb.ArbitrationAttestationMetaInvalid.code,
                         "arbitration_attestation_meta_invalid", TAG_META)
        self.assertTrue(issubclass(sarb.ArbitrationAttestationProofInvalid,
                                   sarb.ArbitrationAttestationMetaInvalid),
                        TAG_META + " ProofInvalid 是 MetaInvalid 子类")
        self.assertEqual(sarb.ArbitrationAttestationProofInvalid.code,
                         "arbitration_attestation_proof_invalid", TAG_META)
        self.assertTrue(issubclass(sarb.ArbitrationConnectionContractError, RuntimeError),
                        TAG_META + " ContractError 是 RuntimeError 子类")
        self.assertFalse(issubclass(sarb.ArbitrationConnectionContractError, ValueError),
                         TAG_META + " ContractError 不得混入 ValueError 族")

    def test_02_meta_structure_not_dict_missing_extra_keys(self):
        store = self._prepared("meta_struct.db")
        merged = _merged_two_reviewers()
        conn = self._caller_conn(store)
        bads = []
        for broken in (None, "meta", [], 42):
            bads.append((f"type-{type(broken).__name__}", broken))
        missing = _base_meta()
        del missing["round"]
        bads.append(("missing-round", missing))
        extra = _base_meta()
        extra["unexpected"] = 1
        bads.append(("extra-key", extra))
        try:
            for label, meta in bads:
                with self.subTest(case=label):
                    with self.assertRaises(
                            sarb.ArbitrationAttestationMetaInvalid,
                            msg=TAG_META + f" {label} 必须精确 MetaInvalid") as caught:
                        sarb.append_decision_arbitration_attested(
                            conn, stage="stage1", merged=merged,
                            resolutions=_base_resolutions(), arbitrator_id="alice",
                            attestation_meta=meta)
                    self.assertEqual(caught.exception.code,
                                     "arbitration_attestation_meta_invalid",
                                     TAG_META + f" {label} code 必须为冻结值")
                    self.assertTrue(conn.in_transaction,
                                    TAG_META + f" {label} 后 caller 事务仍开启")
        finally:
            conn.rollback()
            conn.close()
        self.assertEqual(_raw_counts(store), (0, 0), TAG_META + " 拒绝零写入")

    def test_03_task_round_required_role_and_userid_strict_shape(self):
        store = self._prepared("meta_fields.db")
        merged = _merged_two_reviewers()
        conn = self._caller_conn(store)
        bads = []
        # task 仅要求非空文本（无凭空长度 cap/strip 规则——空串/非文本/缺省拒绝）。
        for bad_task in ("", 42, None):
            bads.append((f"task-{bad_task!r}", _base_meta(task_id=bad_task)))
        # round 显式 None 或非空文本。
        for bad_round in ("", 42):
            bads.append((f"round-{bad_round!r}", _base_meta(round=bad_round)))
        for label, names in (
                ("required-empty", []), ("required-single", ["alice"]),
                ("required-dup", ["alice", "alice"]),
                ("required-nonstr", ["alice", "bob", 42]),
                ("required-notlist", "alice bob"),
                ("required-none-item", ["alice", None]),
                # ≤100 出处 merge_decision_packages 筛选员名 1..100 规则（主控 API 还原）。
                ("required-over-100", ["alice", "b" * 101])):
            bads.append((label, _base_meta(required_names=names)))
        for bad_uid in (0, -5, True, 2.5, "101", None):
            bads.append((f"uid-{bad_uid!r}", _base_meta(arbitrator_user_id=bad_uid)))
        for bad_role in ("", "   ", 42, None, "admin"):
            bads.append((f"role-{bad_role!r}", _base_meta(arbitrator_role=bad_role)))
        try:
            for label, meta in bads:
                with self.subTest(case=label):
                    with self.assertRaises(
                            sarb.ArbitrationAttestationMetaInvalid,
                            msg=TAG_META + f" {label} 必须精确 MetaInvalid") as caught:
                        sarb.append_decision_arbitration_attested(
                            conn, stage="stage1", merged=merged,
                            resolutions=_base_resolutions(), arbitrator_id="alice",
                            attestation_meta=meta)
                    self.assertEqual(caught.exception.code,
                                     "arbitration_attestation_meta_invalid", TAG_META)
                    self.assertTrue(conn.in_transaction, TAG_META + " caller 事务仍开启")
        finally:
            conn.rollback()
            conn.close()
        self.assertEqual(_raw_counts(store), (0, 0), TAG_META + " 拒绝零写入")


# ---------------------------------------------------------------------------
# T8 input_proof 形状严格
# ---------------------------------------------------------------------------

class T8_ProofShapeTests(_FrozenApiBox):

    def _proof_bads(self):
        base = _proof(("K2",))
        bads = []

        def add(label, proof):
            bads.append((label, _base_meta(input_proof=proof)))

        for broken in (None, "proof", [], 42, {}):
            add(f"proof-type-{type(broken).__name__}", broken)
        add("item-key-empty", {"": base["K2"]})
        add("item-key-nonstr", {42: base["K2"]})
        add("item-value-nonstr-dict", {"K2": "alice-proof"})
        add("user-key-nonstr", {"K2": {101: base["K2"]["alice"]}})
        add("user-value-notdict", {"K2": {"alice": "x"}})

        def with_alice(inner):
            return {"K2": {"alice": inner, "bob": base["K2"]["bob"]}}

        add("lib-missing", with_alice({"revision": 1}))
        add("lib-empty", with_alice({"library_id": "", "revision": 1}))
        add("lib-nonhex", with_alice({"library_id": "zz" * 16, "revision": 1}))
        add("lib-31hex", with_alice({"library_id": "a" * 31, "revision": 1}))
        add("lib-33hex", with_alice({"library_id": "a" * 33, "revision": 1}))
        add("lib-uppercase", with_alice({"library_id": "A" * 32, "revision": 1}))
        add("lib-nonstr", with_alice({"library_id": 42, "revision": 1}))
        add("rev-missing", with_alice({"library_id": _hex32("lib-alice-v1")}))
        add("rev-zero", with_alice({"library_id": _hex32("lib-alice-v1"), "revision": 0}))
        add("rev-negative", with_alice({"library_id": _hex32("lib-alice-v1"), "revision": -1}))
        add("rev-bool-true", with_alice({"library_id": _hex32("lib-alice-v1"), "revision": True}))
        add("rev-str", with_alice({"library_id": _hex32("lib-alice-v1"), "revision": "2"}))
        add("rev-float", with_alice({"library_id": _hex32("lib-alice-v1"), "revision": 2.5}))
        add("rev-none", with_alice({"library_id": _hex32("lib-alice-v1"), "revision": None}))
        add("inner-extra-key", with_alice({
            "library_id": _hex32("lib-alice-v1"), "revision": 1, "note": "x"}))
        return bads

    def test_01_input_proof_structure_strict(self):
        store = self._prepared("proof_shape.db")
        merged = _merged_two_reviewers()
        conn = self._caller_conn(store)
        try:
            for label, meta in self._proof_bads():
                with self.subTest(case=label):
                    with self.assertRaises(
                            sarb.ArbitrationAttestationProofInvalid,
                            msg=TAG_PROOF + f" {label} 必须精确 ProofInvalid") as caught:
                        sarb.append_decision_arbitration_attested(
                            conn, stage="stage1", merged=merged,
                            resolutions=_base_resolutions(), arbitrator_id="alice",
                            attestation_meta=meta)
                    self.assertEqual(caught.exception.code,
                                     "arbitration_attestation_proof_invalid", TAG_PROOF)
                    self.assertTrue(issubclass(type(caught.exception),
                                               sarb.ArbitrationAttestationMetaInvalid),
                                    TAG_PROOF + " ProofInvalid 保持 MetaInvalid 子类关系")
                    self.assertTrue(conn.in_transaction, TAG_PROOF + " caller 事务仍开启")
        finally:
            conn.rollback()
            conn.close()
        self.assertEqual(_raw_counts(store), (0, 0), TAG_PROOF + " 拒绝零写入")

    def test_02_multi_key_multi_member_valid_proof_roundtrip(self):
        """全部 evaluated 键 × 全 required 成员的合法 proof：结构化原样存取。"""
        store = self._prepared("proof_multi.db")
        meta = _base_meta(input_proof=_proof(("K2", "K4")))
        result = self._append_once(store, resolutions=_multi_resolutions(), meta=meta)
        self.assertEqual(_att_field(result["attestation"], "input_proof", TAG_PROOF),
                         meta["input_proof"], TAG_PROOF + " 多键多成员 proof 原样存取")
        self.assertEqual(_att_field(result["attestation"], "required_names", TAG_PROOF),
                         ["alice", "bob"], TAG_PROOF)

    def test_03_bool_revision_accepted_int_value_matches(self):
        """revision 合法域＝正整数非 bool：bool 即便值等价也必须拒绝（V-bool 已在
        T8.1 拒绝侧覆盖），此处补钉 int(1) 与 bool(True) 内容寻址不同构。"""
        store = self._prepared("proof_bool.db")
        good = self._append_once(store, meta=_base_meta(
            input_proof=_proof(("K2",), revisions={"alice": 1})))
        self.assertEqual(_att_field(good["attestation"], "input_proof", TAG_PROOF),
                         _proof(("K2",), revisions={"alice": 1}), TAG_PROOF)
        # True 作为 revision 之一直接拒绝（精确 ProofInvalid）。
        conn = self._caller_conn(store)
        try:
            bad = _proof(("K2",))
            bad["K2"]["alice"]["revision"] = True
            with self.assertRaises(sarb.ArbitrationAttestationProofInvalid,
                                   msg=TAG_PROOF + " revision True 非 bool 域"):
                sarb.append_decision_arbitration_attested(
                    conn, stage="stage1", merged=_merged_two_reviewers(),
                    resolutions=_base_resolutions(), arbitrator_id="alice",
                    attestation_meta=_base_meta(input_proof=bad))
        finally:
            conn.rollback()
            conn.close()


# ---------------------------------------------------------------------------
# T9 borrowed conn 契约
# ---------------------------------------------------------------------------

class T9_BorrowedConnTests(_FrozenApiBox):

    def test_01_caller_transaction_not_begun_contract_error_only(self):
        store = self._prepared("notbegun.db")
        conn = sqlite3.connect(str(store), timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        self.assertFalse(conn.in_transaction, TAG_BORROW)
        try:
            with self.assertRaises(
                    sarb.ArbitrationConnectionContractError,
                    msg=TAG_BORROW + " caller 未 BEGIN 必须精确 ContractError") as caught:
                sarb.append_decision_arbitration_attested(
                    conn, stage="stage1", merged=_merged_two_reviewers(),
                    resolutions=_base_resolutions(), arbitrator_id="alice",
                    attestation_meta=_base_meta())
            self.assertIsInstance(caught.exception, RuntimeError,
                                  TAG_BORROW + " RuntimeError 族")
            self.assertFalse(isinstance(caught.exception, ValueError),
                             TAG_BORROW + " 不得混入 ValueError 族")
            self.assertFalse(conn.in_transaction,
                             TAG_BORROW + " 不得暗开启 caller 事务")
        finally:
            if conn.in_transaction:
                conn.rollback()
            conn.close()
        self.assertEqual(_raw_counts(store), (0, 0), TAG_BORROW + " 零写入")
        # caller 恢复：正常 BEGIN 后同输入可成功（无半写残留、连接仍可用）。
        result = self._append_once(store)
        self.assertEqual(_raw_counts(store), (1, 1), TAG_BORROW + " 恢复后正常落库")

    def test_02_row_factory_pragma_txn_state_unchanged_and_no_migration(self):
        # 两种现实 caller row_factory：生产协作库同款 sqlite3.Row 与默认（None→
        # 普通元组）。产品按位置读取并 tuple(row) 转换——两者都支持。
        # （如实记录：dict 映射型 row_factory 下 tuple(dict) 取键序会碎——
        # 冻结合同未承诺任意 callable 工厂，此残差已呈报主控。）
        for factory in (sqlite3.Row, None):
            with self.subTest(factory=getattr(factory, "__name__", "default-none")):
                store = self._prepared(f"borrow_{getattr(factory, '__name__', 'none')}.db")
                conn = self._caller_conn(store, row_factory=factory)
                try:
                    before = _object_names(store)
                    fk_before = self._fk_on(conn)
                    result = sarb.append_decision_arbitration_attested(
                        conn, stage="stage1", merged=_merged_two_reviewers(),
                        resolutions=_base_resolutions(), arbitrator_id="alice",
                        attestation_meta=_base_meta())
                    self.assertIs(conn.row_factory, factory,
                                  TAG_BORROW + " row_factory 零变动")
                    self.assertEqual(self._fk_on(conn), fk_before,
                                     TAG_BORROW + " caller PRAGMA 状态零变动")
                    self.assertTrue(conn.in_transaction,
                                    TAG_BORROW + " caller 事务持续（无自提交）")
                    after = _object_names(store)
                    self.assertTrue(before <= after, TAG_BORROW + " 不得删改既有对象")
                    extra = after - before
                    self.assertTrue(
                        extra <= {("sqlite_sequence", "table")},
                        TAG_BORROW + f" 无 migration（新增对象仅容忍内部 sqlite_sequence）: {extra}")
                    conn.rollback()
                finally:
                    conn.close()
                # caller 提交前回滚 → 全弃（append 无自提交的直接证据）。
                self.assertEqual(_raw_counts(store), (0, 0),
                                 TAG_BORROW + " caller 回滚全弃")

    def test_03_same_txn_generic_attest_session_flow_and_conn_still_usable(self):
        store = self._prepared("sametxn.db")
        self._seed_session_fixture(store)
        conn = self._caller_conn(store)
        try:
            self._bump_session(conn)
            t0 = datetime.now(timezone.utc)
            result = sarb.append_decision_arbitration_attested(
                conn, stage="stage1", merged=_merged_two_reviewers(),
                resolutions=_base_resolutions(), arbitrator_id="alice",
                attestation_meta=_base_meta())
            t1 = datetime.now(timezone.utc)
            self.assertTrue(conn.in_transaction, TAG_BORROW + " append 后事务仍在 caller")
            # 连接未被关闭：caller 可继续用同一连接做后续语句。
            conn.execute(
                "INSERT INTO team_attest_session_fixture VALUES (2, '[]', ?)", (FIXED_ISO,))
            conn.commit()
        finally:
            conn.close()
        self._assert_result_shape(result, TAG_BORROW)
        self._assert_attestation_time(result["attestation"], t0, t1, TAG_BORROW)
        with closing(_raw_conn(store)) as raw:
            self.assertEqual(raw.execute(
                "SELECT count(*) FROM team_attest_session_fixture").fetchone()[0], 2,
                TAG_BORROW + " session 两行同事务提交")
            self.assertEqual(raw.execute(
                "SELECT epoch FROM team_attest_session_fixture ORDER BY rowid LIMIT 1"
            ).fetchone()[0], 1, TAG_BORROW + " epoch 更新同事务生效")
        self.assertEqual(_raw_counts(store), (1, 1), TAG_BORROW + " generic+attest 同事务落库")

    @staticmethod
    def _fk_on(conn):
        row = conn.execute("PRAGMA foreign_keys").fetchone()
        return row["foreign_keys"] if isinstance(row, dict) else row[0]


# ---------------------------------------------------------------------------
# T10 故障注入（真实 authorizer）无半写
# ---------------------------------------------------------------------------

class _DenyInsertAuthorizer:
    """真实 authorizer（不 mock Connection.execute）：精确拒绝单表 INSERT。"""

    def __init__(self, table):
        self.table = table
        self.denied = []

    def __call__(self, action, arg1, arg2, db_name, trigger_or_view):
        insert = getattr(sqlite3, "SQLITE_INSERT", 18)
        if action == insert and arg1 == self.table:
            self.denied.append(arg1)
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK


class T10_FailureRollbackTests(_FrozenApiBox):

    def _deny_append_and_rollback(self, store, denied_table, tag):
        self._seed_session_fixture(store)
        conn = self._caller_conn(store)
        authorizer = _DenyInsertAuthorizer(denied_table)
        conn.set_authorizer(authorizer)
        try:
            self._bump_session(conn)
            with self.assertRaises(sqlite3.DatabaseError,
                                   msg=tag + " 注入故障必须以真实 sqlite 错误上抛（不吞不换族）"):
                sarb.append_decision_arbitration_attested(
                    conn, stage="stage1", merged=_merged_two_reviewers(),
                    resolutions=_base_resolutions(), arbitrator_id="alice",
                    attestation_meta=_base_meta())
            self.assertTrue(authorizer.denied, tag + " deny 必须确实命中目标表")
            conn.rollback()
        finally:
            conn.set_authorizer(None)
            conn.close()
        # 无半写：generic + attest + 模拟 session 状态一起退。
        gen, attest = _raw_counts(store)
        self.assertEqual((gen, attest), (0, 0), tag + " 无半写")
        self.assertEqual(self._session_epoch(store), 0, tag + " 模拟 session 状态一并回退")

    def test_01_authorizer_denies_attest_insert_no_half_write(self):
        store = self._prepared("deny_attest.db")
        self._deny_append_and_rollback(store, ATTEST_TABLE,
                                       TAG_ROLLBACK + " attest-INSERT-deny")
        # 精确性对照（同一 authorizer 语义）：generic 表 INSERT 不在 deny 域。
        conn = self._caller_conn(store)
        authorizer = _DenyInsertAuthorizer(ATTEST_TABLE)
        conn.set_authorizer(authorizer)
        try:
            conn.execute(
                f"INSERT INTO {GENERIC_TABLE} "
                "(stage,merge_sha256,result_sha256,supersedes_id,arbitrator_id,records_json,created_at) "
                "VALUES(?,?,?,?,?,?,?)",
                ("stage1", "e" * 64, "f" * 64, None, "probe", "[]", FIXED_ISO))
            conn.rollback()
        finally:
            conn.set_authorizer(None)
            conn.close()
        self.assertEqual(authorizer.denied, [],
                         TAG_ROLLBACK + " deny 只命中 attest 表（精确注入）")

    def test_02_authorizer_denies_generic_insert_no_half_write(self):
        store = self._prepared("deny_generic.db")
        self._deny_append_and_rollback(store, GENERIC_TABLE,
                                       TAG_ROLLBACK + " generic-INSERT-deny")


# ---------------------------------------------------------------------------
# T11 可信 RO 语义
# ---------------------------------------------------------------------------

class T11_TrustedReadOnlyTests(_FrozenApiBox):

    def test_01_missing_store_empty_and_zero_creation(self):
        missing_dir = self.base / "ro_missing"
        store = missing_dir / "attest.db"
        self.assertIsNone(sarb.get_decision_arbitration_attestation(store, 1), TAG_RO)
        self.assertEqual(sarb.list_decision_arbitration_attestations(store), [], TAG_RO)
        self.assertFalse(store.exists(), TAG_RO + " RO 缺文件不得建库")
        self.assertFalse(missing_dir.exists(), TAG_RO + " RO 不得建父目录")

    def test_02_attest_table_absent_empty_and_zero_ddl(self):
        store = self._store("generic_only.db")
        rec = sarb.append_decision_arbitration(
            store, stage="stage1", merged=_merged_two_reviewers(),
            resolutions=_base_resolutions(), arbitrator_id="alice")
        names_before = _object_names(store)
        self.assertTrue(
            all(name != ATTEST_TABLE for name, _ in names_before),
            TAG_RO + " 夹具前置：该 store 尚无 attest 表")
        self.assertIsNone(sarb.get_decision_arbitration_attestation(store, 1), TAG_RO)
        self.assertEqual(sarb.list_decision_arbitration_attestations(store), [], TAG_RO)
        self.assertIsNone(sarb.get_decision_arbitration_attestation(store, rec["id"]), TAG_RO)
        names_after = _object_names(store)
        self.assertEqual(names_before, names_after, TAG_RO + " 可信只读零 DDL/迁移")
        # 旧 generic 读路径不受影响。
        self.assertEqual(sarb.get_decision_arbitration(store, rec["id"])["id"], rec["id"],
                         TAG_RO + " generic 数据原样")

    def test_03_no_matching_id_and_positive_roundtrip(self):
        store = self._prepared("roundtrip.db")
        result = self._append_once(store)
        got = sarb.get_decision_arbitration_attestation(store, result["attestation_id"])
        self.assertIsInstance(got, dict, TAG_RO + " 命中返回 dict")
        self.assertEqual(
            set(got), {"record", "attestation", "record_id", "attestation_id"},
            TAG_RO + " RO 与 append 返回同构冻结四键组合")
        self.assertEqual(got["record_id"], result["record_id"], TAG_RO)
        self.assertEqual(got["attestation_id"], result["attestation_id"], TAG_RO)
        att = _as_attestation(got, TAG_RO)
        self.assertEqual(_att_field(att, "id", TAG_RO), result["attestation_id"], TAG_RO)
        self.assertEqual(_att_field(att, "record_id", TAG_RO), result["record_id"], TAG_RO)
        self.assertEqual(_att_field(att, "input_proof", TAG_RO),
                         _base_meta()["input_proof"], TAG_RO + " 完整来源数据可读回")
        # 引用 generic 全量记录与旧 generic API 对读一致。
        old_generic = sarb.get_decision_arbitration(store, result["record_id"])
        self.assertEqual(got["record"]["records"], old_generic["records"], TAG_RO)
        self.assertEqual(got["record"]["merge_sha256"], old_generic["merge_sha256"], TAG_RO)
        self.assertIsNone(
            sarb.get_decision_arbitration_attestation(store, result["attestation_id"] + 1000),
            TAG_RO + " 无匹配 ID → None")
        self.assertIsNone(
            sarb.get_decision_arbitration_attestation(store, result["record_id"] + 7),
            TAG_RO + " generic id 不冒充 attest id")
        self.assertEqual(sarb.list_decision_arbitration_attestations(
            store, stage="stage2"), [], TAG_RO)
        self.assertEqual(sarb.list_decision_arbitration_attestations(
            store, task_id="task-none"), [], TAG_RO)

    def test_04_invalid_id_and_filters_valueerror(self):
        """主控 API 还原：RO 入参校验（正整数 ID/合法过滤值）→ ValueError。"""
        store = self._prepared("ro_args.db")
        self._append_once(store)
        for bad_id in (0, -1, True, 2.5, "5", None):
            with self.subTest(bad_id=bad_id):
                with self.assertRaises(ValueError,
                                       msg=TAG_RO + f" get 非法 id {bad_id!r} 必须 ValueError"):
                    sarb.get_decision_arbitration_attestation(store, bad_id)
        for kwargs in ({"stage": "stage3"}, {"stage": 42}, {"task_id": ""},
                       {"task_id": 42}, {"record_id": 0}, {"record_id": True}):
            with self.subTest(**kwargs):
                with self.assertRaises(ValueError,
                                       msg=TAG_RO + f" list 非法过滤 {kwargs} 必须 ValueError"):
                    sarb.list_decision_arbitration_attestations(store, **kwargs)

    def test_05_orphan_generic_reference_typed_unavailable_healthy_get_intact(self):
        """R1 更正（review_s1b_attestation_sol.md F4/P2）：查询命中 attest 行而
        其引用 generic 行缺失（外部篡改/漂移）→ 冻结准确类型
        ArbitrationAttestationStoreUnavailable（RuntimeError 族），不伪
        record=None、不伪空；不修库/不删孤儿/不全库扫描；健康行单条 get 不受
        未匹配孤儿影响。原「孤儿 record=None 成功」为测试首轮错误固定口径，
        不属冻结合同。影响记录（2026-11-15）：GitNexus impact(upstream) 对
        build/tests 本符号两索引副本均 UNKNOWN（索引未覆盖 build/tests），按
        非零风险对待＋静态补充：仅 unittest 框架发现本符号，无其他导入方。"""
        store = self._prepared("orphan.db")
        result = self._append_once(store)
        # 裸连接直插孤儿 attest 行（绕开 FK；模拟外部篡改/漂移来源）。
        with closing(sqlite3.connect(str(store), timeout=15)) as conn:
            conn.execute(
                f"INSERT INTO {ATTEST_TABLE} "
                "(record_id,task_id,round_id,stage,arbitrator_user_id,arbitrator_role,"
                "required_names_json,input_proof_json,resolutions_digest,attest_sha256,attested_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (result["record_id"] + 9000, "task-orphan", None, "stage1", 999,
                 "reconciler", '["alice","bob"]', '{}', "a" * 64, "b" * 64, FIXED_ISO))
            conn.commit()
        # list 命中孤儿 → typed StoreUnavailable（不得伪空/record=None 组合行）。
        with self.assertRaises(
                sarb.ArbitrationAttestationStoreUnavailable,
                msg=TAG_RO + " 命中 attest 缺 generic 必须 typed "
                             "StoreUnavailable（不得伪空/record=None）") as caught_list:
            sarb.list_decision_arbitration_attestations(store)
        self.assertIsInstance(caught_list.exception, RuntimeError,
                              TAG_RO + " StoreUnavailable 是 RuntimeError 族")
        self.assertFalse(isinstance(caught_list.exception, ValueError),
                         TAG_RO + " 不得混入 ValueError 族")
        # get 单条孤儿同口径。
        with self.assertRaises(
                sarb.ArbitrationAttestationStoreUnavailable,
                msg=TAG_RO + " get 孤儿引用同口径 typed StoreUnavailable"):
            sarb.get_decision_arbitration_attestation(
                store, result["attestation_id"] + 1)
        # 健康行单条 get 不受未匹配孤儿影响：完整四键组合 + record 在场。
        healthy = sarb.get_decision_arbitration_attestation(
            store, result["attestation_id"])
        self.assertEqual(
            set(healthy), {"record", "attestation", "record_id", "attestation_id"},
            TAG_RO + " 健康行仍返回同构四键组合")
        self.assertEqual(healthy["record_id"], result["record_id"],
                         TAG_RO + " 健康行引用原 generic")
        self.assertIsNotNone(healthy["record"],
                             TAG_RO + " 健康行 record 完整在场")
        # 无自愈：坏行原样在场（存储层不删孤儿/不修库），行数与内容不变。
        self.assertEqual(_raw_counts(store), (1, 2),
                         TAG_RO + " 故障后无自愈删行")
        with closing(_raw_conn(store)) as conn:
            kept = conn.execute(
                f"SELECT count(*) FROM {ATTEST_TABLE} WHERE task_id='task-orphan'"
            ).fetchone()[0]
        self.assertEqual(kept, 1, TAG_RO + " 孤儿行不被删除（存储层不修库）")

    def test_06_corrupt_existing_store_raises_not_fake_empty(self):
        store = self._prepared("corrupt.db")
        result = self._append_once(store)
        self.assertEqual(_raw_counts(store), (1, 1), TAG_RO)
        # 关闭全部连接后损坏既有 store 文件。
        store.write_bytes(b"this is definitely not a sqlite database file" * 4)
        with self.assertRaises(
                sarb.ArbitrationAttestationStoreUnavailable,
                msg=TAG_RO + " 既有 store 损坏必须以冻结准确类型 raise（不得伪空）") as caught:
            sarb.get_decision_arbitration_attestation(store, result["attestation_id"])
        self.assertIsInstance(caught.exception, RuntimeError,
                              TAG_RO + " StoreUnavailable 是 RuntimeError 族")
        self.assertFalse(isinstance(caught.exception, ValueError),
                         TAG_RO + " 不得混入 ValueError 族")
        with self.assertRaises(sarb.ArbitrationAttestationStoreUnavailable,
                               msg=TAG_RO + " list 同口径必须 raise"):
            sarb.list_decision_arbitration_attestations(store)
        # 旧 generic RO 旧兼容口径不改变：损坏仍降级 None（不因新接口改动）。
        self.assertIsNone(sarb.get_decision_arbitration(store, result["record_id"]),
                          TAG_RO + " 旧 generic get 损坏降级 None 口径保持")
        self.assertIsNone(sarb.latest_decision_arbitration(store, "stage1"),
                          TAG_RO + " 旧 generic latest 同口径保持")

    def test_07_busy_store_raises_then_recovers(self):
        store = self._prepared("busy.db")
        result = self._append_once(store)
        holder = _raw_conn(store)
        try:
            holder.execute("BEGIN EXCLUSIVE")
            holder.execute(f"SELECT count(*) FROM {GENERIC_TABLE}").fetchone()
            with self.assertRaises(
                    sarb.ArbitrationAttestationStoreUnavailable,
                    msg=TAG_RO + " 既有 store 锁忙必须以冻结准确类型 raise（不得吞成伪空）"):
                sarb.get_decision_arbitration_attestation(store, result["attestation_id"])
            holder.rollback()
        finally:
            holder.close()
        got = sarb.get_decision_arbitration_attestation(store, result["attestation_id"])
        self.assertEqual(_att_field(_as_attestation(got, TAG_RO), "id", TAG_RO),
                         result["attestation_id"], TAG_RO + " 释放锁后恢复可读")


# ---------------------------------------------------------------------------
# T12 旧 generic 锚点（不依赖冻结 API——before 侧真实可跑并保持绿）
# ---------------------------------------------------------------------------

class T12_OldGenericAnchors(_AttestBox):
    """既有 generic 行为锚点：S1b 纯新增不得改变旧 generic 口径（实际调用作对照）。"""

    def test_01_old_ro_missing_and_corrupt_compat_unchanged(self):
        missing = self.base / "anchor_missing" / "x.db"
        self.assertIsNone(sarb.get_decision_arbitration(missing, 1), TAG_ANCHOR)
        self.assertIsNone(sarb.latest_decision_arbitration(missing, "stage1"), TAG_ANCHOR)
        self.assertFalse(missing.exists(), TAG_ANCHOR + " 旧 RO 缺文件不建库")
        corrupt = self.base / "anchor_corrupt.db"
        corrupt.write_bytes(b"not a database" * 8)
        self.assertIsNone(sarb.get_decision_arbitration(corrupt, 1),
                          TAG_ANCHOR + " 旧 get 损坏降级 None 口径保持")
        self.assertIsNone(sarb.latest_decision_arbitration(corrupt, "stage1"),
                          TAG_ANCHOR + " 旧 latest 同口径保持")

    def test_02_merge_tools_gates_two_to_nine_and_duplicate_reject(self):
        single = _reviewer_frame([("K1", "include", "", "", "", FIXED_ISO)])
        with self.assertRaises(ValueError, msg=TAG_ANCHOR + " 2-9 份门保持"):
            sarb.merge_decision_packages({"alice": single})
        with self.assertRaises(ValueError, msg=TAG_ANCHOR + " 内容相同包拒绝保持"):
            sarb.merge_decision_packages({"alice": single, "bob": single})
        bob = _reviewer_frame([("K1", "exclude", "人群不符", "", "", FIXED_ISO)])
        merged = sarb.merge_decision_packages({"alice": single, "bob": bob})
        self.assertEqual(dict(zip(merged["zotero_key"], merged["status"])),
                         {"K1": "conflict"}, TAG_ANCHOR + " 合并工具行为不变")

    def test_03_old_generic_retry_supersedes_latest_roundtrip(self):
        store = self._store("anchor_generic.db")
        merged = _merged_two_reviewers()
        rec1 = sarb.append_decision_arbitration(
            store, stage="stage1", merged=merged,
            resolutions=_base_resolutions(), arbitrator_id="alice")
        rec_retry = sarb.append_decision_arbitration(
            store, stage="stage1", merged=merged,
            resolutions=_base_resolutions(), arbitrator_id="alice")
        self.assertEqual(rec_retry["id"], rec1["id"], TAG_ANCHOR + " 重试幂等保持")
        self.assertEqual(rec_retry["created_at"], rec1["created_at"],
                         TAG_ANCHOR + " 时间原保持")
        rec2 = sarb.append_decision_arbitration(
            store, stage="stage1", merged=merged,
            resolutions=_multi_resolutions(), arbitrator_id="alice")
        self.assertEqual(rec2["supersedes_id"], rec1["id"], TAG_ANCHOR + " supersedes 链保持")
        self.assertEqual(sarb.get_decision_arbitration(store, rec2["id"])["id"], rec2["id"],
                         TAG_ANCHOR + " get 轮回保持")
        self.assertEqual(sarb.latest_decision_arbitration(store, "stage1")["id"], rec2["id"],
                         TAG_ANCHOR + " latest 保持")


# ---------------------------------------------------------------------------
# T13 prepare 整体初装故障（PHASE1 合同：自有单 BEGIN，generic+attest 整体回滚）
# ---------------------------------------------------------------------------

class _DenyCreateTableAuthorizer:
    """真实 authorizer（connection factory 注入，不 patch 不可变 execute）：
    精确拒绝对指定表名的 CREATE TABLE，其余放行。"""

    def __init__(self, table):
        self.table = table
        self.denied = []
        self.allowed_creates = []

    def __call__(self, action, arg1, arg2, db_name, trigger_or_view):
        create_table = getattr(sqlite3, "SQLITE_CREATE_TABLE", 0)
        if action == create_table:
            if arg1 == self.table:
                self.denied.append(arg1)
                return sqlite3.SQLITE_DENY
            self.allowed_creates.append(arg1)
        return sqlite3.SQLITE_OK


class T13_PrepareInstallRollbackTests(_FrozenApiBox):
    """主控指令（2026-11-15）：PHASE1 合同 prepare 自有单 BEGIN——generic+attest
    应整体 DDL 回滚（文件可存在但无半装对象）。全新 store（记录原始 schema 后
    注入故障），authorizer 精确拒 attest CREATE TABLE；不把既有 generic schema
    在场的情形误当初装回滚测试。期望按合同判定，不为迎候选放松；红即合同缺口，
    呈报主控并保留 first raw 日志。"""

    def test_01_fresh_store_attest_create_denied_no_half_install(self):
        store = self.base / "prepare_fail.db"
        # 原始 schema 记录：全新 store，故障注入前零对象。
        self.assertFalse(store.exists(), TAG_PREPARE_FAIL)
        original_connect = sqlite3.connect
        authorizer = _DenyCreateTableAuthorizer(ATTEST_TABLE)

        def factory(path, *args, **kwargs):
            conn = original_connect(path, *args, **kwargs)
            conn.set_authorizer(authorizer)
            return conn

        with mock.patch.object(sqlite3, "connect", factory):
            with self.assertRaises(
                    sqlite3.DatabaseError,
                    msg=TAG_PREPARE_FAIL + " 真实 authorizer 拒绝必须以 sqlite 错误上抛"):
                sarb.prepare_arbitration_attestation_storage(store)
        self.assertTrue(authorizer.denied,
                        TAG_PREPARE_FAIL + " deny 必须确实命中 attest CREATE TABLE")
        # 精确性：同 authorizer 语义下其他 CREATE TABLE 被放行（deny 只打 attest）。
        self.assertIn(GENERIC_TABLE, authorizer.allowed_creates,
                      TAG_PREPARE_FAIL + " generic CREATE 曾被执行（故障前真实安装动作）")
        # 文件可存在，但按合同不得有半装对象：attest 与 generic DDL 都必须缺席。
        names = _object_names(store) if store.exists() else set()
        attest_left = sorted(n for n, _ in names
                             if n == ATTEST_TABLE or n.startswith(ATTEST_TABLE + "_"))
        generic_left = sorted(n for n, _ in names
                              if n == GENERIC_TABLE or n.startswith(GENERIC_TABLE + "_"))
        self.assertEqual(attest_left, [],
                         TAG_PREPARE_FAIL + f" attest 对象不得半装: {attest_left}")
        self.assertEqual(
            generic_left, [],
            TAG_PREPARE_FAIL
            + f" PHASE1 合同：prepare 单 BEGIN，generic+attest 应整体回滚（半装=违约）: {generic_left}")
        # 故障后 store 可重试：无 authorizer 干扰下 prepare 幂等成功、可用。
        result = sarb.prepare_arbitration_attestation_storage(store)
        self.assertIsNone(result, TAG_PREPARE_FAIL + " 故障后重试成功（可恢复）")
        self.assertEqual(sarb.list_decision_arbitration_attestations(store), [],
                         TAG_PREPARE_FAIL + " 重试后空表可读")

    def test_02_authorizer_precision_control(self):
        """同一 deny 语义控制组：其他表 CREATE 放行、attest 表 CREATE 拒绝。"""
        scratch = self.base / "auth_control.db"
        original_connect = sqlite3.connect
        authorizer = _DenyCreateTableAuthorizer(ATTEST_TABLE)

        def factory(path, *args, **kwargs):
            conn = original_connect(path, *args, **kwargs)
            conn.set_authorizer(authorizer)
            return conn

        with mock.patch.object(sqlite3, "connect", factory):
            conn = sqlite3.connect(str(scratch))  # 走 factory：真实连接+authorizer
            try:
                conn.execute("CREATE TABLE probe_other(x)")  # 放行
                conn.commit()
                with self.assertRaises(sqlite3.DatabaseError,
                                       msg=TAG_PREPARE_FAIL + " attest 表 CREATE 被精确拒绝"):
                    conn.execute(f"CREATE TABLE {ATTEST_TABLE}(id INTEGER PRIMARY KEY)")
            finally:
                conn.close()
        self.assertEqual(authorizer.denied, [ATTEST_TABLE], TAG_PREPARE_FAIL)
        self.assertIn("probe_other", authorizer.allowed_creates, TAG_PREPARE_FAIL)


# ---------------------------------------------------------------------------
# T14 R1 限定回归（fix-r1）：目录伪装存储缺失 / 持久 JSON 字段损坏 → 冻结准确
# 类型（review_s1b_attestation_sol.md F2/P2、F3/P2；get/list 同合同）
# ---------------------------------------------------------------------------

class T14_StoreUnavailableContractsTests(_FrozenApiBox):
    """独立新增用例类（不触既有类；影响记录：GitNexus impact 对 build/tests
    UNKNOWN——索引未覆盖，按非零风险对待；静态范围仅 unittest 发现）。

    反例注入经裸 SQL 直插合成坏行（INSERT 是唯一写入路径；两表 no_update/
    no_delete 触发器精确拒绝 UPDATE/DELETE 属产品真实 append-only 门——不删
    触发器、不改 schema、不绕开；坏行模拟外部篡改/漂移来源的历史落库，同型
    于 Sol「插坏 generic+关联 attest」合成反例）。损坏型 fixture 每例独立新建
    （损坏后不再经可信 reader 取后续健康前置，前置 ID 一律在损坏前经裸 SQL/
    真实 append 取得）；健康对照行经 actor 维度另立 generic（冻结 generic
    哈希含 arbitrator），坏行不毒化任何健康行单条 get。
    TEXT 亲和下数字字面量会存成合法 JSON 文本——不作反例、不强造不可能 SQL
    类型：JSONDecodeError 反例用 'not-json'，UnicodeDecodeError 反例用非法
    UTF-8 BLOB（真实可触发，BLOB 不经亲和转换）。
    断言窄口径按具体类型：RuntimeError 族 StoreUnavailable 且不混 ValueError
    族（裸 JSONDecodeError/UnicodeDecodeError 均为 ValueError 子类，被此断言
    排除）；无宽 Exception、无 skip。"""

    def _two_generics_store(self, name: str):
        """两独立 generic 记录（actor 维度）各带一条 attest 的健康 fixture。"""
        store = self._prepared(name)
        first = self._append_once(store, arbitrator_id="alice",
                                  meta=_base_meta(arbitrator_user_id=101))
        second = self._append_once(store, arbitrator_id="carol",
                                   meta=_base_meta(arbitrator_user_id=202))
        self.assertNotEqual(first["record_id"], second["record_id"],
                            TAG_RO_FIXR1 + " 夹具前置：两 generic 独立记录")
        self.assertNotEqual(first["attestation_id"], second["attestation_id"],
                            TAG_RO_FIXR1 + " 夹具前置：两 attest 独立记录")
        return store, first, second

    def _assert_typed_unavailable(self, store, *, get_id, tag):
        """两公开 API 同合同：typed StoreUnavailable（RuntimeError 族、不混
        ValueError 族＝排除裸 JSONDecodeError/UnicodeDecodeError、不伪空）。"""
        with self.assertRaises(
                sarb.ArbitrationAttestationStoreUnavailable,
                msg=tag + " get 必须 typed StoreUnavailable"
                          "（不裸 JSON 异常/不伪空）") as caught:
            sarb.get_decision_arbitration_attestation(store, get_id)
        self.assertIsInstance(caught.exception, RuntimeError,
                              tag + " StoreUnavailable 是 RuntimeError 族")
        self.assertFalse(isinstance(caught.exception, ValueError),
                         tag + " 不得混 ValueError 族（排除裸 "
                               "JSONDecodeError/UnicodeDecodeError）")
        with self.assertRaises(
                sarb.ArbitrationAttestationStoreUnavailable,
                msg=tag + " list 同合同 typed StoreUnavailable"):
            sarb.list_decision_arbitration_attestations(store)

    def _assert_healthy_sibling(self, store, sibling, tag):
        """健康对照行（另一独立 generic）单条 get 不受坏行影响。"""
        healthy = sarb.get_decision_arbitration_attestation(
            store, sibling["attestation_id"])
        self.assertEqual(
            set(healthy), {"record", "attestation", "record_id", "attestation_id"},
            tag + " 健康行单条 get 不受其他坏行影响（四键组合完整）")
        self.assertEqual(healthy["record_id"], sibling["record_id"],
                         tag + " 健康行引用自身 generic")
        self.assertIsNotNone(healthy["record"], tag + " 健康行 record 完整")

    def test_01_real_directory_store_typed_unavailable_not_fake_empty(self):
        """F2：stat 成功但非普通文件（真实已存在目录）→ 两公开 API typed
        StoreUnavailable；只有真实 FileNotFoundError 允许空；目录不被删除/
        替换、目录内零创建（无自愈/无伪空）。"""
        parent = self.base / "dir_store"
        parent.mkdir(parents=True, exist_ok=True)
        store = parent / "attest.db"
        store.mkdir()
        self.assertTrue(store.is_dir(), TAG_RO_FIXR1 + " 夹具前置：路径为真实目录")
        self._assert_typed_unavailable(store, get_id=1,
                                       tag=TAG_RO_FIXR1 + " directory")
        self.assertTrue(store.is_dir(),
                        TAG_RO_FIXR1 + " 目录不被删除/替换（无自愈）")
        self.assertEqual(sorted(p.name for p in parent.iterdir()), ["attest.db"],
                         TAG_RO_FIXR1 + " 零创建（目录内不得新增文件）")

    def _run_json_corruption_case(self, label, bad_field, bad_value, store_name):
        """单损坏模式：健康双 generic fixture → 裸 SQL 直插合成坏行（含坏值
        持久字段，其余字段合法）→ 两 API typed + 全部健康行单条 get 不受影响
        + 坏值无自愈原样在场。坏行前置 ID 全部经裸 SQL 在断言前取得。"""
        store, first, second = self._two_generics_store(store_name)
        bad_record_id = None
        with closing(_raw_conn(store)) as conn:
            if bad_field == "records_json":
                # 坏 generic 行 + 关联 attest 行（同型于 Sol 合成反例）。
                cur = conn.execute(
                    f"INSERT INTO {GENERIC_TABLE} "
                    "(stage,merge_sha256,result_sha256,supersedes_id,arbitrator_id,"
                    "records_json,created_at) VALUES(?,?,?,?,?,?,?)",
                    ("stage1", "0" * 64, "1" * 64, None, "probe", bad_value,
                     FIXED_ISO))
                bad_record_id = cur.lastrowid
                conn.execute(
                    f"INSERT INTO {ATTEST_TABLE} "
                    "(record_id,task_id,round_id,stage,arbitrator_user_id,"
                    "arbitrator_role,required_names_json,input_proof_json,"
                    "resolutions_digest,attest_sha256,attested_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (bad_record_id, "task-corrupt", None, "stage1", 999,
                     "reconciler", '["alice","bob"]', '{}', "c" * 64, "d" * 64,
                     FIXED_ISO))
            else:
                # 坏 attest 字段行挂在健康 generic 上（其余 attest 字段合法）。
                conn.execute(
                    f"INSERT INTO {ATTEST_TABLE} "
                    "(record_id,task_id,round_id,stage,arbitrator_user_id,"
                    "arbitrator_role,required_names_json,input_proof_json,"
                    "resolutions_digest,attest_sha256,attested_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (first["record_id"], "task-corrupt", None, "stage1", 999,
                     "reconciler",
                     bad_value if bad_field == "required_names_json"
                     else '["alice","bob"]',
                     bad_value if bad_field == "input_proof_json" else '{}',
                     "c" * 64, "d" * 64, FIXED_ISO))
            bad_attest_id = conn.execute(
                f"SELECT id FROM {ATTEST_TABLE} WHERE task_id='task-corrupt' "
                "ORDER BY id DESC LIMIT 1").fetchone()[0]
            conn.commit()
        tag = f"{TAG_RO_FIXR1} {bad_field}[{label}]"
        self._assert_typed_unavailable(store, get_id=bad_attest_id, tag=tag)
        # 坏行不毒化健康行：两条真实 appends（两独立 generic）单条 get 均完整。
        self._assert_healthy_sibling(store, first, tag)
        self._assert_healthy_sibling(store, second, tag)
        # 无自愈：坏值原样在场（存储层不改写/不清理）。
        with closing(_raw_conn(store)) as conn:
            if bad_field == "records_json":
                stored = conn.execute(
                    f"SELECT {bad_field} FROM {GENERIC_TABLE} WHERE id=?",
                    (bad_record_id,)).fetchone()[0]
            else:
                stored = conn.execute(
                    f"SELECT {bad_field} FROM {ATTEST_TABLE} WHERE id=?",
                    (bad_attest_id,)).fetchone()[0]
        self.assertEqual(stored, bad_value, tag + " 坏值原样在场（无自愈改写）")

    def test_02_generic_records_json_corrupt_typed_unavailable(self):
        """F3：generic records_json 持久损坏（合成坏 generic+关联 attest）→
        get/list 均 typed StoreUnavailable（不裸 JSONDecodeError/UnicodeDecode
        Error）；健康 generic 行单条 get 不受影响。"""
        for label, bad_value in (
                ("not-json", "not-json"),
                ("bad-utf8-blob", sqlite3.Binary(b"\x80\x81\x82"))):
            with self.subTest(corrupt=label):
                self._run_json_corruption_case(
                    label, "records_json", bad_value,
                    f"corrupt_records_{label}.db")

    def test_03_attest_required_names_json_corrupt_typed_unavailable(self):
        """F3：attest required_names_json 持久损坏（合成坏 attest 行）→
        get/list 均 typed StoreUnavailable（不裸 JSONDecodeError/UnicodeDecode
        Error）；健康行单条 get 不受影响。"""
        for label, bad_value in (
                ("not-json", "not-json"),
                ("bad-utf8-blob", sqlite3.Binary(b"\x80\x81\x82"))):
            with self.subTest(corrupt=label):
                self._run_json_corruption_case(
                    label, "required_names_json", bad_value,
                    f"corrupt_required_{label}.db")

    def test_04_attest_input_proof_json_corrupt_typed_unavailable(self):
        """F3：attest input_proof_json 持久损坏（合成坏 attest 行）→ get/list
        均 typed StoreUnavailable（不裸 JSONDecodeError/UnicodeDecodeError）；
        健康行单条 get 不受影响。"""
        for label, bad_value in (
                ("not-json", "not-json"),
                ("bad-utf8-blob", sqlite3.Binary(b"\x80\x81\x82"))):
            with self.subTest(corrupt=label):
                self._run_json_corruption_case(
                    label, "input_proof_json", bad_value,
                    f"corrupt_proof_{label}.db")


if __name__ == "__main__":
    unittest.main()
