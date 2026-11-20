# -*- coding: utf-8 -*-
"""P09 keyed team views + 受保护只读 scope 测试候选 —— 静态撰写，作者未运行。

目标产品候选（本文件唯一针对的实现，冻结版本 SHA 已在箱外独立核验一致）：
- E:/rf216-scratch/p09-keyed-views-20261118T231348Z-8840/candidate/
  team_screening_runtime.py
  SHA-256 dcfd5775095508c49d9c59440a193b1e8bc0efeff02ff540d1cf4b444c7d4c31
  （主仓基线 = 08e5ff906b3abaa7d8823495ad0a6edaae12eb2d38b3a3c1327fa8f54cf3b3a0；
  差量为 P09 追加：视图 item_keys 形参/DTO 新默认字段/按键装载/附件接口/
  _ro_connect Row 工厂/_build_scope collab_conn；既有全域路径函数体零改动）
- 同箱 candidate/team_keyed_reads.py（新文件）
  SHA-256 6e6ced2a60d5ea264d4dc06e19a6ab79aaf2095f829d0c57c16e7d8057f98b24
- 已过 g-flash 复审 PASS-with-notes（HIGH×2 独立裁定同意＋主控备案裁定全接受）。

装载协议（P11 教训 / P03 candidate4 配方，两文件同用）：
- 主仓无这两文件的候选版（P09 未整合；custom_backend/ 下无 team_keyed_reads.py，
  team_screening_runtime.py 基线 SHA=08e5ff90…）：本文件把主仓根 E:/mubi-reviewflow
  插入 sys.path 供 coscreen 依赖（P00/P02/P03/P04/P05/S1a/S1b）与
  custom_backend.team_keyed_inputs/team_keyed_domain/review_workflow_api/auth 解析，
  候选副本经箱根/candidate/ 以 importlib 文件位置加载：**exec_module 前先注册
  sys.modules[stem]（P03 candidate4 配方——未注册 stem 下 exec 会在模块对象
  身份校验处炸），失败路径 identity-guard 回滚注册再原样上抛**；stem 用全名
  ``custom_backend.team_screening_runtime`` / ``custom_backend.team_keyed_reads``
  ——后者 import 前者必须命中候选（基线同名字段不齐）。装载先于任何
  custom_backend.* 导入；review_workflow_api:492 的惰性
  ``from custom_backend.team_screening_runtime import …`` 因此同样命中候选。
  运行解释器必须 -I -B 且 TEMP/TMP 指向测试箱（不向只读主仓写 __pycache__）。

用例 → 合同映射（5 具名用例，工单钉死名称逐字）：
- EachKeyHasValueOrIssueTests.test_each_key_has_value_or_issue
  断言锚：``set(view.statuses).isdisjoint(view.issues)`` 且
  ``(set(view.statuses) | set(view.issues)) == set(view.keys)``；链视图
  ``chain ∪ issues`` 同理（stage2 子视图逐级成立）。夹具=见证键 A（collab
  F1 unanimous include）＋冲突 live 键 B（alice/bob 分歧→awaiting_arbitration）
  ＋非 canonical 键 X（key_not_in_domain issue）；另钉 KeyIssue DTO 形状与
  ``completion_required_uids``/``completion`` 的 persisted=True 侧。
- CompletedBypassTests.test_completed_bypasses_unrelated_config_and_votes
  plan 见证键先于票/仲裁读取定 FINAL：config 坏（bob 双 grant 冲突→
  config.ok False / conflicting）＋成员票脏（alice.db 对见证键决策 'bogus'——
  若按键路径读过成员票即 vote_domain_violation typed raise）均不推翻；见证
  unanimous 不造仲裁 ID、adjudicated 见证 payload 仲裁对进 applied（C08）；
  全完成请求零成员库打开（行为锚：脏票存在而视图成功＝票面从未读；Windows
  句柄锁探针 os.replace 成功＝零泄漏句柄，P08 A3-A5 机制）。
- ReadNeverPersistsTests.test_read_never_persists_observation
  scope 退出后 collab/owner/成员/users 全库零持久化（sha256＋sqlite_master
  对象清单＋全表行数前后相等；team_flow_receipts 两侧缺席＝零 receipt；
  team_terminal_seals 行数不变＝零 seal；F2/F2 审计行数不变＝零 prepare/
  零 DML）。语句面探针（真实 native 子类连接，P04/P05 先例）：scope 级
  借用形态 collab 连接全语句 ∈ {SELECT, PRAGMA}、commit/rollback/close
  计数 0；单元级 ``_release_read_handle``（受保护 scope 唯一的事务收尾面）：
  正常路径 trace 恰一条 ROLLBACK、closed 连接清理失败 add_note 附着不吞主错、
  无主错时清理错误如实上抛。诚实边界见「诚实记录 1」。
- ChainPruningTests.test_chain_pruning_and_single_required
  stage1 FINAL_EXCLUDE 剪 stage2（J 见证 exclude → J 不进 stage2.keys/
  statuses/issues，链呈 upstream_stage1_final_exclude 且 completion 取
  stage1 persisted=True）；单 required 全票一致直接终局（L 两次 unanimous
  → final_include/decided_by consensus、applied 无、completion
  persisted=False kind unanimous trigger last_vote、零仲裁读取——collab 全程
  无 attestation 表而视图成功）。
- IssueHelperOrderTests.test_issue_helpers_preserve_order
  stage_final_keys/arbitration_ready_keys 按 view.keys 序跳 issue 键：
  请求序 (C1, ZZ, D, C4, C3)（ZZ=域外 issue；C1 见证 include；D 见证
  exclude；C4/C3 冲突待裁）→ stage_final_keys==["C1"]、
  arbitration_ready_keys==["C4","C3"]（请求序，非 owner 域序 (C1,C3,C4,D)）。
支撑用例（3 个，先例允许；P08/P04 包均 3 支撑）：
- LegacyRegressionDtoTests.test_legacy_global_path_regression_and_dto_defaults
  旧全域路径正控：item_keys=None 行为不变（A/B/C 全域 status 逐键钉定）＋
  **NOTE-1**：asdict 序列化面新增默认键——``TeamStageView`` 追加
  issues/completion/completion_required_uids、``TeamChainView`` 追加
  issues/completion（全域路径恒 {} / ()）；本测试钉新键名与新默认值。
  主仓既有锚完整 DTO dict 的测试（build/tests/
  test_2_1_6_team_screening_runtime.py::G8DomainDTOChain::
  test_dto_zero_leakage_and_chain_views 两处 assertEqual(set(payload)…)
  与 allowed_keys 白名单）需在集成时同步——本包给出精确增量。按键视图与
  全域视图 statuses 逐键相等（同一 votes 快照两装载管道等值正控）。
- CacheIsolationTests.test_cache_isolation_between_keyed_and_global
  按键缓存不污染全域/全域不污染按键：全域→按键（滤波复用分支）、
  按键→全域、按键小子域→按键大子域 三方向，各自与同一夹具新 scope
  pristine 对照 statuses/completion 逐键相等。
- HighAnchorsTests.test_high_anchors_row_factory_and_role_state_equivalence
  HIGH×2 行为锚：①_ro_connect Row 工厂下旧读取器双兼容——全域路径
  （vote_snapshot 位置访问）与按键路径（vote_snapshot_keys_on **硬性要求**
  row_factory is sqlite3.Row，基线 fresh 连接无 Row 工厂必
  member_vote_unreadable/TypeError 面）同夹具双双健康、无
  member_vote_unreadable/vote_row_* issue；②role_state 默认路径等值——
  同夹具 fresh（stat＋自开连接路径）vs collab_conn 预置（屏障读路径）两
  scope 的 role_state() dict 相等、按键/全域视图 statuses+config 相等。

夹具设计（零 mock；全部真实 SQLite 文件/真实产品 API 构造，参照 P05/P08
测试箱与主仓 S1c TSR 回归 test_2_1_6_team_screening_runtime.py 的构造模式）：
- collab 库＝真实 ``collab.initialize_task_store``（collaboration_* 族）＋
  ``tr.prepare_team_terminal_registry``（F2 两表两唯一索引四触发器）＋
  ``tf.prepare_team_terminal_facts``（F1 seals/meta 两表两触发器）同文件共存；
  F2 protocol 行经 ``tr.register_protocol_on``（trusted_new_task）；
  见证 seal 经真实 ``tf.append_seal_on``（v2 observation payload origin=collab；
  adjudicated 见证经 v1 payload kind=adjudicated/trigger=arbitration，origin
  contract 允许面），P08 箱已验同参确定性→同 seal_id。
- owner/成员库＝真实 ``dbm.init_db``（articles/decisions/stage2_decisions/
  coding/title 三表＋workflow_state＋P04 账本四对象六触发器一真实安装）；
  票经直插 decisions/stage2_decisions（INSERT 触发真实 trigger 推进 revision，
  守恒自洽——P04/S1c 先例）；owner 库 articles=canonical 键域。
- 账号库 users.db＝真实 ``auth.create_user``（bcrypt；COBOOKSHELF_DATA_DIR
  类级指箱内 users 临时根，addCleanup 还原——S1c _ensure_users 先例）。
- 受保护 scope＝真实 ``WorkflowRuntime``（review_workflow_api）＋真实
  ``runtime.readonly_context``/``runtime.lock``＋宿主形 task_lookup（返回真实
  ``tasks.TaskInfo`` 值——生产由 main._task_or_404 提供，此处按夹具任务构造
  真实 dataclass 值，非 mock）＋真实 ``tkr.protected_keyed_read_scope``。
- 语句面探针＝真实 ``sqlite3.Connection`` 子类（set_trace_callback 登记＋
  commit/rollback/close 计数，P04 _kv_ProbeConnection 先例；真实子类过 C02
  门，非 __class__ 伪装）。

SQL 预检纪律（P07 教训）：本文件全部静态 SQL 常量（_P09_INSERT_ARTICLE/
_P09_INSERT_STAGE1_VOTE/_P09_INSERT_STAGE2_VOTE）已由本箱 scripts/ddl_scan.py
在真实 init_db schema（内存库）上逐条 EXPLAIN 语法验证；DDL 组合扫描（P11
教训）同一脚本在真实五族（collaboration_*／筛选器 articles/decisions/…／
P04 账本 team_vote_*／F1 team_terminal_seals／F2 team_task_protocol+registry）
同文件共存后断言对象名零冲突、逐库 schema 元组与实际操作表核对（报告附表）。

诚实记录（机制实测项，不伪造测试）：
1. manager 自开的 collab 屏障连接（mode=rw，``_open_barrier_connection``）
   无法在零 mock 下注入探针（sqlite3.connect 无 factory 注入口；猴子补丁
   违反零 mock 纪律）。其语句面以三条真实锚间接钉定：scope 级借用形态探针
   （同一读取代码路径：role_state/read_seal_on/read_attestations_for_keys_on
   /canonical 窄读全部过该连接）＋单元级 ``_release_read_handle`` probe
   （屏障唯一的事务收尾面）＋受保护真路径的全库文件态不变性。与 P08 箱
   诚实记录 1（N2 打开失败分支）同型：单元级钉法＋集成级行为锚。
2. ``_witness_payload`` 的 witness_unreadable/witness_drift 分支需要 holder
   保留连接或计划后漂移：本包夹具见证全部 collab 持有（source_path=None，
   经屏障读），holder 见证分支由 P08 箱（发现侧）覆盖，本包不重复钉。
3. 仲裁读取统一走「未 prepare＝合法空」（老 seam 与 C06 按键读同语义）：
   全包不建 attestation 表，冲突键恒 awaiting_arbitration；真实 attestation
   写入/采信面由 S1b/P05 箱覆盖。
4. users.db 复用类级实例（bcrypt 建号耗时）；各用例任务夹具独立
   TemporaryDirectory（TEMP 指箱）。users.db 哈希纳入 T3 零持久化快照。

撰写协议（同 P04/P08 先例）：本文件每个 def/class 符号（含 __init__/
__call__/__enter__ 类方法；文件按构造不含 lambda、不含内嵌 def——scripts/
selfcheck_ast.py AST 自检钉住）在写入前均逐项运行 GitNexus CLI upstream
impact（--repo E:/mubi-reviewflow --file build/tests/test_216_keyed_team_views.py
--kind Class/Method/Function --include-tests）；新文件预期 not found/UNKNOWN，
raw/rc/MANIFEST 存档于本箱 impact-raw/（每次 CLI 前经 ps 门 gate.ps1 确认无
其他 gitnexus 作业）。

未运行声明：作者仅静态撰写测试用例；唯一执行物为四项环境可行性探针
（不执行任何测试用例路径）：scripts/ddl_scan.py（静态 SQL 常量 EXPLAIN+
实插、五族 DDL 组合扫描）、scripts/loader_probe.py（P03 配方装载两候选＋
合同形状/Row 工厂单元实测）、scripts/import_probe.py（测试模块本体
exec_module 装载可行性＋用例可发现性＝8）、scripts/mech_probe.py
（closed 连接 in_transaction=ProgrammingError/trace 登记 ROLLBACK/add_note
三机制实测）；日志存本箱 artifacts/。测试执行留给独立 runner 在应用产品
候选后进行（运行命令：TEMP 指本箱 tmp 后
E:/rf216-scratch/validation-venv312/Scripts/python.exe -I -B
<本箱>/build/tests/test_216_keyed_team_views.py -v）。禁主仓/前端/真实
data/8613/旧证据箱写入；夹具全部位于测试自有 TemporaryDirectory（运行时
TEMP 需指测试箱）；零 mock（无替换对象/无猴子补丁；全部真实文件系统与
真实产品 API）。自包含：不依赖 conftest/Safety 夹具、不触 HTTP 层；静态
消费者仅 unittest。
"""

from __future__ import annotations

import dataclasses
import hashlib
import importlib.util
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

_P09_BOX_ROOT = Path(__file__).resolve().parents[2]
_P09_RUNTIME_SOURCE = _P09_BOX_ROOT / "candidate" / "team_screening_runtime.py"
_P09_READS_SOURCE = _P09_BOX_ROOT / "candidate" / "team_keyed_reads.py"
_P09_REPO_ROOT = Path("E:/mubi-reviewflow")
if not _P09_REPO_ROOT.is_dir():
    raise RuntimeError(
        "P09 测试需要主仓 E:/mubi-reviewflow（候选 import coscreen 全族与 "
        "custom_backend P00/P06/S1b/review_workflow_api/auth）；主仓缺位时"
        "本文件拒绝静默降级")
if not _P09_RUNTIME_SOURCE.is_file() or not _P09_READS_SOURCE.is_file():
    raise RuntimeError(
        "P09 测试需要箱内候选副本（箱根/candidate/team_screening_runtime.py 与 "
        "team_keyed_reads.py）。对主仓基线运行时本文件在模块加载期诚实红："
        "主仓尚无 P09 候选版，本文件不冒充行为红证（集成时由协调者迁移加载路径）")
if str(_P09_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_P09_REPO_ROOT))


def _p09_load_candidate_module(stem, path):
    """P03 candidate4 装载配方（两文件同用）：exec_module 前注册 sys.modules
    ＋identity-guard 回滚。

    dataclass/`from X import Y` 在未注册 stem 下 exec 会因模块对象不在
    sys.modules 而炸（P11 教训），故先注册；执行失败时回滚注册（仅当仍为
    本模块对象——identity guard）再原样上抛。同名已注册＝装配错误，拒绝
    静默顶替（候选必须先于任何 custom_backend.* 导入装载）。
    """
    previous = sys.modules.get(stem)
    if previous is not None:
        raise RuntimeError(
            "P09 loader collision: %s already registered (%r)" % (stem, previous))
    spec = importlib.util.spec_from_file_location(stem, path)
    if spec is None or spec.loader is None:
        raise RuntimeError("P09 spec load failed: %s" % path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[stem] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        if sys.modules.get(stem) is module:
            del sys.modules[stem]
        raise
    return module


tsr = _p09_load_candidate_module(
    "custom_backend.team_screening_runtime", _P09_RUNTIME_SOURCE)
tkr = _p09_load_candidate_module(
    "custom_backend.team_keyed_reads", _P09_READS_SOURCE)

from coscreen import db as dbm  # noqa: E402
from coscreen import task_collaboration as collab  # noqa: E402
from coscreen import tasks as tasks_mod  # noqa: E402
from coscreen import team_terminal_facts as tf  # noqa: E402
from coscreen import team_terminal_registry as tr  # noqa: E402
from coscreen.team_flow_types import (  # noqa: E402
    CompletionProjection,
    KeyedReadPlan,
    TeamOperation,
)
from coscreen.team_screening_eligibility import AWAITING_MEMBERS  # noqa: E402
import custom_backend.auth as auth  # noqa: E402
from custom_backend.review_workflow_api import WorkflowRuntime  # noqa: E402
from custom_backend.team_keyed_inputs import build_keyed_read_plan  # noqa: E402

#: 冻结候选 SHA-256（运行时打印实测值供核对，不做硬断言：候选后续修订经
#: 复审后 SHA 自然变化，硬断言会制造假红——P08 先例）。
P09_RUNTIME_CANDIDATE_SHA = (
    "dcfd5775095508c49d9c59440a193b1e8bc0efeff02ff540d1cf4b444c7d4c31")
P09_READS_CANDIDATE_SHA = (
    "6e6ced2a60d5ea264d4dc06e19a6ab79aaf2095f829d0c57c16e7d8057f98b24")
P09_RUNTIME_BASELINE_SHA = (
    "08e5ff906b3abaa7d8823495ad0a6edaae12eb2d38b3a3c1327fa8f54cf3b3a0")

#: 时间戳字面量（F1 sealed_at 合同形态，毫秒 Z；成员票 screened/updated 用
#: 带时区 ISO——_aware_utc 逐时刻可核）。
P09_TS1 = "2026-11-18T09:00:00.000Z"
P09_TS2 = "2026-11-18T09:30:00.000Z"
P09_VOTE_SCREENED = "2026-11-14T08:00:00+00:00"
P09_VOTE_UPDATED = "2026-11-14T09:00:00+00:00"
P09_VOTE_UPDATED_LATE = "2026-11-14T10:00:00+00:00"

#: owner（collab 权威域）library_id——32hex 形态常量（payload 与 F2 protocol
#: 行同值）。
P09_OWNER_LIBRARY = "1a" * 16

#: 账号名与口令（真实 auth 建号；uid 以真实库行为唯一事实源）。
P09_USER_NAMES = ("owner", "alice", "bob")
P09_PASSWORD = "p09-pass-9527"

#: 类级共享账号映射（真实 auth 注册；setUpClass 填充）。
USERS = {}

#: provenance 打印哨兵（每进程一次——P08 先例）。
_P09_PROVENANCE_PRINTED = False

# ---------------------------------------------------------------------------
# 静态 SQL 常量（全部经 scripts/ddl_scan.py 在真实 init_db schema 上 EXPLAIN
# 预检；表/列名来自 coscreen.db 冻结 DDL，禁止臆造）。
# ---------------------------------------------------------------------------

_P09_INSERT_ARTICLE = (
    "INSERT INTO articles (zotero_key, title, is_duplicate_of, import_order)"
    " VALUES (?, ?, NULL, ?)")
_P09_INSERT_STAGE1_VOTE = (
    "INSERT INTO decisions (zotero_key, decision, exclusion_reason, notes,"
    " tags, screened_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)")
_P09_INSERT_STAGE2_VOTE = (
    "INSERT INTO stage2_decisions (zotero_key, decision, exclusion_reason,"
    " notes, tags, screened_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)")

#: F1 payload canonical JSON 镜像（独立重实现，钉 F1 合同序列化参数——P08 先例）。
_P09_JSON_KW = {"ensure_ascii": False, "sort_keys": True,
                "separators": (",", ":"), "allow_nan": False}


def _p09_sha256_file(path):
    """文件字节哈希（零持久化快照与候选来源核验的观测锚）。"""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _p09_canonical_json(value):
    """canonical JSON 镜像（tf.sha256 的输入序；P08 箱同款独立实现）。"""
    return json.dumps(value, **_P09_JSON_KW)


def _p09_close_quietly(conn):
    """测试自有连接安静回收（rollback 余留事务后 close；探测计数随后取）。"""
    try:
        if conn.in_transaction:
            conn.rollback()
    except sqlite3.Error:
        pass
    try:
        conn.close()
    except sqlite3.Error:
        pass


def _p09_borrowed_writer(path):
    """C02 借用形态写连接（F2 登记/F1 seal 的真实 append 入口要求）：
    native＋Row 工厂＋活动 IMMEDIATE 事务（P08 p08_borrowed 镜像）。"""
    conn = sqlite3.connect(str(path), timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("BEGIN IMMEDIATE")
    return conn


def _p09_member_library_id(uid):
    """payload 内各来源 library_id 的确定性 32hex 形态（P08 镜像）。"""
    return "%032x" % uid


def _p09_default_roster():
    """F1 payload 名册（历史快照形态）：owner=uid1 reconciler 非必需筛选人；
    alice/bob=coder 必需筛选人（P08 p08_default_roster 同构、账号名/uid 经
    真实 USERS 映射）。"""
    return [
        {"uid": USERS["owner"], "username": "owner", "role": "reconciler",
         "is_owner": True, "screening_enabled": False,
         "required_screener": False, "source": "owner-source", "config": "ok"},
        {"uid": USERS["alice"], "username": "alice", "role": "coder",
         "is_owner": False, "screening_enabled": None,
         "required_screener": True, "source": "import-source", "config": "ok"},
        {"uid": USERS["bob"], "username": "bob", "role": "coder",
         "is_owner": False, "screening_enabled": None,
         "required_screener": True, "source": "grant-source", "config": "ok"},
    ]


def _p09_make_opinion(uid, decision):
    """单个必需筛选人意见（opinion/source_row 哈希按 F1 合同算法独立重算，
    P08 p08_make_opinion 镜像）。"""
    opinion = {
        "uid": uid, "decision": decision, "exclusion_reason": "", "notes": "",
        "library_id": _p09_member_library_id(uid), "revision": uid + 10,
        "source_row": {"tags": "tag-%s" % uid,
                       "screened_at": "2026-01-02 03:04:05",
                       "updated_at": None if uid % 2 else "2026-01-03 04:05:06"},
        "opinion_sha256": "", "source_row_sha256": "",
    }
    opinion["opinion_sha256"] = hashlib.sha256(_p09_canonical_json({
        "decision": decision.strip().lower(), "exclusion_reason": "",
        "notes": ""}).encode("utf-8")).hexdigest()
    opinion["source_row_sha256"] = hashlib.sha256(_p09_canonical_json(
        opinion["source_row"]).encode("utf-8")).hexdigest()
    return opinion


def _p09_observation_payload(*, task_id, instance, stage, item_key, decision,
                             sealed_at=P09_TS1):
    """v2 observation seal payload（P08 箱已验 append_seal_on origin=collab
    接受；同参确定性→同 seal_id）。required_uids/绑定覆盖 required∪observer。"""
    roster = _p09_default_roster()
    required_uids = sorted(entry["uid"] for entry in roster
                           if entry["required_screener"])
    opinions = [_p09_make_opinion(uid, decision) for uid in required_uids]
    bindings = [
        {"source_user_id": uid, "registration_id": "registration-%d" % uid,
         "generation": 1, "source_library_id": _p09_member_library_id(uid)}
        for uid in sorted(set(required_uids) | {USERS["owner"]})
    ]
    return {
        "schema": tf.OBSERVATION_PAYLOAD_SCHEMA,
        "task": {"task_id": task_id, "task_instance_id": instance,
                 "owner_user_id": USERS["owner"], "owner_username": "owner",
                 "owner_library_id": P09_OWNER_LIBRARY},
        "stage": stage, "item_key": item_key, "round_id": None,
        "kind": "unanimous", "trigger": "current_observation",
        "final_decision": decision, "roster": roster,
        "required_uids": required_uids, "opinions": opinions,
        "arbitration": None,
        "protocol": {"source_registration_id": "registration-%d"
                     % USERS["owner"], "roster_revision": 7},
        "source_request": {"operation": "completion_observe",
                           "request_id": "request-p09-1",
                           "request_sha256": "b" * 64},
        "sealed_at": sealed_at,
        "observation": {"host_operation": "completion_observe",
                        "host_stage": "fulltext", "host_item_keys": [item_key],
                        "observed_by_user_id": USERS["owner"],
                        "basis": "current_snapshot"},
        "source_bindings": bindings,
    }


def _p09_adjudicated_payload(*, task_id, instance, stage, item_key,
                             final_decision, sealed_at, attestation_id,
                             record_id):
    """v1 adjudicated seal payload（origin=collab 合同允许面
    kind=adjudicated/trigger=arbitration）：意见含 include+exclude 双值，
    input_proof 逐 required uid 匹配意见 library/revision（F1 校验合同）。"""
    roster = _p09_default_roster()
    required_uids = sorted(entry["uid"] for entry in roster
                           if entry["required_screener"])
    per_uid_decision = {required_uids[0]: "include",
                        required_uids[1]: "exclude"}
    opinions = [_p09_make_opinion(uid, per_uid_decision[uid])
                for uid in required_uids]
    proof = {item_key: {str(uid): {"library_id": _p09_member_library_id(uid),
                                   "revision": uid + 10}
                        for uid in required_uids}}
    arbitration = {
        "arbitrator_user_id": USERS["owner"], "arbitrator_role": "reconciler",
        "record_id": record_id, "attestation_id": attestation_id,
        "attested_at": sealed_at,
        "resolutions_digest": "d" * 64, "result_sha256": "b" * 64,
        "attest_sha256": "a" * 64, "rationale": "team decision",
        "input_proof": proof,
    }
    return {
        "schema": tf.PAYLOAD_SCHEMA,
        "task": {"task_id": task_id, "task_instance_id": instance,
                 "owner_user_id": USERS["owner"], "owner_username": "owner",
                 "owner_library_id": P09_OWNER_LIBRARY},
        "stage": stage, "item_key": item_key, "round_id": None,
        "kind": "adjudicated", "trigger": "arbitration",
        "final_decision": final_decision, "roster": roster,
        "required_uids": required_uids, "opinions": opinions,
        "arbitration": arbitration,
        "protocol": {"source_registration_id": None, "roster_revision": None},
        "source_request": {"operation": "arbitration_finish",
                           "request_id": "request-p09-att-1",
                           "request_sha256": "f" * 64},
        "sealed_at": sealed_at,
    }


def _p09_restore_env(key, previous):
    """TRACKED_ENV 还原（auth 建号窗口的 COBOOKSHELF_DATA_DIR；S1c 先例）。"""
    if previous is None:
        os.environ.pop(key, None)
    else:
        os.environ[key] = previous


def _p09_visible_true(info, user):
    """宿主 visible 钩子（真实函数；owner 已解析路径下不触达）。"""
    return True


def _p09_is_readonly_statement(sql):
    """语句面白名单：仅 SELECT/PRAGMA 视为只读类（T3 探针断言用）。"""
    head = sql.lstrip()[:16].upper()
    return head.startswith("SELECT") or head.startswith("PRAGMA")


def _p09_replaceable(path):
    """Windows 句柄锁探针（P08 A3-A5 实测机制）：任何打开句柄都令
    os.replace PermissionError；操作本身即断言（泄漏即诚实红）。"""
    moved = Path(str(path) + ".p09probe")
    os.replace(path, moved)
    os.replace(moved, path)
    return Path(path).is_file()


class _P09TaskLookup:
    """宿主 task_lookup 真实载体：生产由 main._task_or_404 提供；此处按测试
    夹具任务返回真实 ``tasks.TaskInfo`` dataclass 值（非 mock——不拦改任何
    行为，仅宿主装配形状）。"""

    def __init__(self, task_id, info):
        self._task_id = task_id
        self._info = info

    def __call__(self, wanted):
        if wanted != self._task_id:
            raise KeyError(wanted)
        return self._info


class _P09ProbeConnection(sqlite3.Connection):
    """Native 子类探针：set_trace_callback 登记＋commit/rollback/close 计数
    （P04 _kv_ProbeConnection 先例）。真实 type 继承——C02 借用门接受；
    trace 覆盖经 cursor 执行的全部语句（不止 conn.execute）。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.statements = []
        self.commit_calls = 0
        self.rollback_calls = 0
        self.close_calls = 0
        self.set_trace_callback(self.statements.append)

    def commit(self):
        self.commit_calls += 1
        return super().commit()

    def rollback(self):
        self.rollback_calls += 1
        return super().rollback()

    def close(self):
        super().close()
        self.close_calls += 1


class KeyedTeamViewsBase(unittest.TestCase):
    """公共基座：类级真实 users.db（auth 建号）＋用例级真实任务夹具
    （TemporaryDirectory + 真实三/四库产品 API 构造），零 mock。"""

    maxDiff = None

    @classmethod
    def setUpClass(cls):
        cls._users_root_obj = tempfile.TemporaryDirectory(
            prefix="rf216_p09_users_")
        cls.addClassCleanup(cls._users_root_obj.cleanup)
        cls.users_root = Path(cls._users_root_obj.name)
        cls.users_db = cls.users_root / "users.db"
        previous = os.environ.get("COBOOKSHELF_DATA_DIR")
        cls.addClassCleanup(_p09_restore_env, "COBOOKSHELF_DATA_DIR", previous)
        os.environ["COBOOKSHELF_DATA_DIR"] = str(cls.users_root)
        for name in P09_USER_NAMES:
            created = auth.create_user(name, P09_PASSWORD)
            USERS[name] = int(created["id"])
        global _P09_PROVENANCE_PRINTED
        if not _P09_PROVENANCE_PRINTED:
            _P09_PROVENANCE_PRINTED = True
            print("P09_TEST_FILE_SHA",
                  _p09_sha256_file(Path(__file__).resolve()))
            print("P09_RUNTIME_CANDIDATE_SHA",
                  _p09_sha256_file(_P09_RUNTIME_SOURCE))
            print("P09_READS_CANDIDATE_SHA",
                  _p09_sha256_file(_P09_READS_SOURCE))
            print("P09_RUNTIME_PINNED_SHA", P09_RUNTIME_CANDIDATE_SHA)
            print("P09_READS_PINNED_SHA", P09_READS_CANDIDATE_SHA)
            print("P09_RUNTIME_BASELINE_SHA", P09_RUNTIME_BASELINE_SHA)
            print("P09_REPO_TYPES_SHA", _p09_sha256_file(
                _P09_REPO_ROOT / "coscreen" / "team_flow_types.py"))
            print("P09_REPO_FACTS_SHA", _p09_sha256_file(
                _P09_REPO_ROOT / "coscreen" / "team_terminal_facts.py"))
            print("P09_REPO_REGISTRY_SHA", _p09_sha256_file(
                _P09_REPO_ROOT / "coscreen" / "team_terminal_registry.py"))
            print("P09_REPO_LEDGER_SHA", _p09_sha256_file(
                _P09_REPO_ROOT / "coscreen" / "team_vote_ledger.py"))
            print("P09_REPO_KEYED_INPUTS_SHA", _p09_sha256_file(
                _P09_REPO_ROOT / "custom_backend" / "team_keyed_inputs.py"))
            print("P09_REPO_KEYED_DOMAIN_SHA", _p09_sha256_file(
                _P09_REPO_ROOT / "custom_backend" / "team_keyed_domain.py"))
            print("P09_PYTHON", sys.version.split()[0])
            print("P09_SQLITE_VERSION", sqlite3.sqlite_version)

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="rf216_p09_case_")
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)

    # -- 场景构建 helper（全部真实公共 setup API） ---------------------------

    def _task_info(self, task_dir, task_id):
        """真实 TaskInfo dataclass 值（task_id/dir/display_name 最小合同）。"""
        return tasks_mod.TaskInfo(task_id=task_id, dir=task_dir,
                                  display_name=task_id)

    def _make_runtime(self, task_dir, task_id):
        """真实 WorkflowRuntime（宿主装配形状）：真实 readonly_context/lock；
        task_lookup 经 _P09TaskLookup 返回夹具任务的真实 TaskInfo。"""
        info = self._task_info(task_dir, task_id)
        return WorkflowRuntime(_P09TaskLookup(task_id, info), None,
                               self.users_root, _p09_visible_true)

    def _init_team_task(self, name, task_id):
        """真实 initialize_task_store 建协作库；属主显式 reconciler choice
        （config.ok 必须为 True——evaluate_stage 配置不完备时全键
        awaiting_config，会淹没 live 键状态锚；reconciler 非必需筛选人）。"""
        task_dir = self.base / name
        task_dir.mkdir()
        collab.initialize_task_store(
            task_dir, task_id=task_id, owner_user_id=USERS["owner"],
            owner_username="owner")
        collab.set_business_role(
            task_dir / "collaboration.db", actor_user_id=USERS["owner"],
            user_id=USERS["owner"], business_role="reconciler",
            task_id=task_id)
        return task_dir

    def _grant_member(self, task_dir, task_id, username, role="coder"):
        """真实 grant_role（属主授权）。"""
        collab.grant_role(
            task_dir / "collaboration.db", actor_user_id=USERS["owner"],
            user_id=USERS[username], role=role, task_id=task_id)

    def _choice_member(self, task_dir, task_id, username, business_role="coder"):
        """真实 set_business_role（业务角色单选；属主可写）。"""
        collab.set_business_role(
            task_dir / "collaboration.db", actor_user_id=USERS["owner"],
            user_id=USERS[username], business_role=business_role,
            task_id=task_id)

    def _screener_db(self, task_dir, username, *, articles=(), votes1=(),
                     votes2=()):
        """真实 db.init_db（含 P04 账本真实安装）＋静态 SQL 直插种子：
        articles 先插（键＝articles∪票键，import_order 即 seeding 序），
        票 INSERT 触发真实 trigger 推进 revision（守恒自洽——P04/S1c 先例）。
        votes1/votes2: (key, decision, updated_at, screened_at) 元组序列；
        decision 可为 'bogus' 等域外脏值（无 CHECK 约束，读取方 fail-closed
        ——T2 脏票锚）。"""
        path = task_dir / (tasks_mod.slugify_screener(username) + ".db")
        dbm.init_db(path)
        vote_keys = [row[0] for row in votes1] + [row[0] for row in votes2]
        seeded = list(dict.fromkeys(list(articles) + vote_keys))
        conn = sqlite3.connect(str(path), timeout=15)
        try:
            for order, key in enumerate(seeded):
                conn.execute(_P09_INSERT_ARTICLE, (key, "p09-" + key, order))
            for key, decision, updated, screened in votes1:
                conn.execute(_P09_INSERT_STAGE1_VOTE,
                             (key, decision, "", "", "", screened, updated))
            for key, decision, updated, screened in votes2:
                conn.execute(_P09_INSERT_STAGE2_VOTE,
                             (key, decision, "", "", "", screened, updated))
            conn.commit()
        finally:
            conn.close()
        return path

    def _prepare_f2(self, collab_path, task_id, instance):
        """F2 真实 prepare＋protocol 行（trusted_new_task；P08 p08_collab_db
        同款借用写连接）。"""
        tr.prepare_team_terminal_registry(collab_path)
        writer = _p09_borrowed_writer(collab_path)
        try:
            tr.register_protocol_on(
                writer, task_id=task_id, task_instance_id=instance,
                owner_user_id=USERS["owner"],
                owner_library_id=P09_OWNER_LIBRARY,
                creation_path="trusted_new_task")
            writer.commit()
        finally:
            _p09_close_quietly(writer)

    def _prepare_f1(self, collab_path):
        """F1 真实 prepare（seals/meta 两表两触发器）。"""
        tf.prepare_team_terminal_facts(collab_path)

    def _append_collab_seal(self, collab_path, payload):
        """真实 append_seal_on（origin=collab；v2 observation 与 v1
        adjudicated 双 payload 形态共用入口；borrowed 写连接 commit 收尾）。"""
        writer = _p09_borrowed_writer(collab_path)
        try:
            record = tf.append_seal_on(writer, payload, origin="collab")
            writer.commit()
            return record
        finally:
            _p09_close_quietly(writer)

    def _snapshot_state(self, paths):
        """零持久化快照：逐库 sha256＋sqlite_master 对象清单＋全表行数
        （表名来自库自身 introspection，仅读取不拼改）。"""
        snapshot = {}
        for path in paths:
            path = Path(path)
            entry = {"sha256": _p09_sha256_file(path), "objects": [],
                     "counts": {}}
            conn = sqlite3.connect(str(path), timeout=15)
            try:
                rows = conn.execute(
                    "SELECT name, type FROM sqlite_master"
                    " WHERE name NOT LIKE 'sqlite_%' ORDER BY name, type"
                ).fetchall()
                for name, kind in rows:
                    entry["objects"].append((name, kind))
                    if kind == "table":
                        quoted = '"%s"' % name.replace('"', '""')
                        entry["counts"][name] = int(conn.execute(
                            "SELECT COUNT(*) FROM %s" % quoted).fetchone()[0])
            finally:
                conn.close()
            snapshot[str(path)] = entry
        return snapshot


class EachKeyHasValueOrIssueTests(KeyedTeamViewsBase):
    """用例 1：每请求键恰有 status 或 issue（不交、并集=keys）；链视图同。"""

    def test_each_key_has_value_or_issue(self):
        task_id = "p09-t1-each-key"
        instance = "inst-p09-t1"
        task_dir = self._init_team_task("t1", task_id)
        self._grant_member(task_dir, task_id, "alice")
        self._choice_member(task_dir, task_id, "alice")
        self._grant_member(task_dir, task_id, "bob")
        self._choice_member(task_dir, task_id, "bob")
        self._screener_db(task_dir, "owner", articles=("A", "B"))
        self._screener_db(task_dir, "alice", votes1=(
            ("A", "include", P09_VOTE_UPDATED, P09_VOTE_SCREENED),
            ("B", "include", P09_VOTE_UPDATED, P09_VOTE_SCREENED)))
        self._screener_db(task_dir, "bob", votes1=(
            ("A", "include", P09_VOTE_UPDATED_LATE, P09_VOTE_SCREENED),
            ("B", "exclude", P09_VOTE_UPDATED_LATE, P09_VOTE_SCREENED)))
        collab_path = task_dir / "collaboration.db"
        self._prepare_f2(collab_path, task_id, instance)
        self._prepare_f1(collab_path)
        self._append_collab_seal(collab_path, _p09_observation_payload(
            task_id=task_id, instance=instance, stage="stage1", item_key="A",
            decision="include", sealed_at=P09_TS1))
        op = TeamOperation(name="p09-t1-screen", stage="screen")
        runtime = self._make_runtime(task_dir, task_id)
        keys = ("A", "X", "B")
        with tkr.protected_keyed_read_scope(
                runtime, self._task_info(task_dir, task_id),
                {"user_id": USERS["owner"], "username": "owner"},
                operation=op, item_keys=keys) as scope:
            plan = scope.keyed_plan()
            self.assertIsInstance(plan, KeyedReadPlan,
                                  "T1: 受保护 scope 附着 KeyedReadPlan")
            self.assertEqual(plan.item_keys, keys, "T1: 计划键域=请求键域")
            view = tsr.team_stage_view(scope, "stage1", item_keys=keys)
            chain = tsr.team_chain_view(scope, "stage2", item_keys=keys)
        # —— 断言锚（工单逐字）：单阶段视图 ——
        self.assertTrue(set(view.statuses).isdisjoint(view.issues),
                        "T1: statuses 与 issues 不交")
        self.assertEqual(set(view.statuses) | set(view.issues), set(view.keys),
                         "T1: 并集恰为请求键域")
        self.assertEqual(view.keys, keys, "T1: keys=请求序回显")
        self.assertEqual(set(view.statuses), {"A", "B"}, "T1: 见证+冲突键")
        self.assertEqual(set(view.issues), {"X"}, "T1: 域外键恰一 issue")
        self.assertEqual(view.statuses["A"].status, tsr.FINAL_INCLUDE,
                         "T1: 见证键 FINAL（完成事实优先）")
        self.assertEqual(view.statuses["B"].status, tsr.AWAITING_ARBITRATION,
                         "T1: 分歧 live 键待裁")
        issue = view.issues["X"]
        self.assertEqual(dataclasses.asdict(issue),
                         {"reason": "key_not_in_domain", "stage": "stage1",
                          "key": "X", "source_role": "owner",
                          "retryable": False},
                         "T1: KeyIssue DTO 形状与 key_not_in_domain 语义钉定")
        self.assertEqual(view.completion["A"].persisted, True,
                         "T1: F1 见证完成 persisted=True")
        self.assertIsNotNone(view.completion["A"].seal_id,
                             "T1: 见证完成带 seal_id")
        self.assertEqual(view.completion_required_uids,
                         tuple(sorted((USERS["alice"], USERS["bob"]))),
                         "T1: 完成 required uid 升序并集")
        self.assertEqual(view.applied, {},
                         "T1: unanimous 见证不造仲裁 ID（C08）")
        # —— 断言锚：链视图（chain ∪ issues 同理）——
        self.assertTrue(set(chain.chain).isdisjoint(chain.issues),
                        "T1: chain 与 issues 不交")
        self.assertEqual(set(chain.chain) | set(chain.issues), set(keys),
                         "T1: 链并集恰为请求键域")
        self.assertEqual(chain.issues["X"].reason, "key_not_in_domain",
                         "T1: 链 issue 取 stage1 优先")
        self.assertTrue(chain.chain["A"].cleared, "T1: stage2 目标=上游终局即 cleared")
        self.assertEqual(chain.completion["A"], view.completion["A"],
                         "T1: 链完成投影=决定阶段（stage1）完成事实")
        # —— 断言锚：stage2 子视图逐级成立 ——
        self.assertTrue(set(chain.stage2.statuses).isdisjoint(
            chain.stage2.issues), "T1: stage2 子视图不交")
        self.assertEqual(
            set(chain.stage2.statuses) | set(chain.stage2.issues),
            set(chain.stage2.keys), "T1: stage2 子视图并集=其键域")
        self.assertEqual(tsr.stage_final_keys(view), ["A"],
                         "T1: helper 按 keys 序输出 final_include")
        self.assertEqual(tsr.arbitration_ready_keys(view), ["B"],
                         "T1: helper 按 keys 序输出就绪待裁集")


class CompletedBypassTests(KeyedTeamViewsBase):
    """用例 2：见证键先于票/仲裁读取定 FINAL；config 坏/成员票脏不推翻；
    全完成请求零成员库打开。"""

    def test_completed_bypasses_unrelated_config_and_votes(self):
        task_id = "p09-t2-bypass"
        instance = "inst-p09-t2"
        task_dir = self._init_team_task("t2", task_id)
        self._grant_member(task_dir, task_id, "alice")
        self._choice_member(task_dir, task_id, "alice")
        # config 坏注入：bob 双 grant 无 choice → conflicting（config.ok=False）
        self._grant_member(task_dir, task_id, "bob")
        self._grant_member(task_dir, task_id, "bob", role="reconciler")
        self._screener_db(task_dir, "owner", articles=("K1", "K2"))
        # 成员票脏注入：见证键 K1/K2 决策域外——按键路径若读过即
        # vote_domain_violation typed raise（fail-closed 行为锚）。
        self._screener_db(task_dir, "alice", votes1=(
            ("K1", "bogus", P09_VOTE_UPDATED, P09_VOTE_SCREENED),
            ("K2", "bogus", P09_VOTE_UPDATED, P09_VOTE_SCREENED)))
        collab_path = task_dir / "collaboration.db"
        self._prepare_f2(collab_path, task_id, instance)
        self._prepare_f1(collab_path)
        seal_unanimous = self._append_collab_seal(
            collab_path, _p09_observation_payload(
                task_id=task_id, instance=instance, stage="stage1",
                item_key="K1", decision="include", sealed_at=P09_TS1))
        seal_adjudicated = self._append_collab_seal(
            collab_path, _p09_adjudicated_payload(
                task_id=task_id, instance=instance, stage="stage1",
                item_key="K2", final_decision="include", sealed_at=P09_TS2,
                attestation_id=9001, record_id=7001))
        op = TeamOperation(name="p09-t2-screen", stage="screen")
        runtime = self._make_runtime(task_dir, task_id)
        keys = ("K1", "K2")
        with tkr.protected_keyed_read_scope(
                runtime, self._task_info(task_dir, task_id),
                {"user_id": USERS["owner"], "username": "owner"},
                operation=op, item_keys=keys) as scope:
            view = tsr.team_stage_view(scope, "stage1", item_keys=keys)
        # —— 全局 config 诊断不推翻完成 A ——
        self.assertFalse(view.config.ok, "T2: config 坏注入生效（conflicting）")
        self.assertIn("bob", view.config.conflicting,
                      "T2: 双 grant 冲突进 config 诊断")
        # —— 见证键 FINAL，票面脏值从未读（否则 vote_domain_violation）——
        self.assertEqual(set(view.statuses) | set(view.issues), set(keys),
                         "T2: 每键恰有 status 或 issue")
        self.assertEqual(view.issues, {}, "T2: 见证键零 issue")
        self.assertEqual(view.statuses["K1"].status, tsr.FINAL_INCLUDE,
                         "T2: 见证键 FINAL_INCLUDE")
        self.assertEqual(view.statuses["K1"].decided_by, "consensus",
                         "T2: unanimous 见证 decided_by=consensus")
        self.assertNotIn("K1", view.applied,
                         "T2: unanimous 不造仲裁 ID（C08）")
        self.assertEqual(view.statuses["K2"].status, tsr.FINAL_INCLUDE,
                         "T2: adjudicated 见证键 FINAL_INCLUDE")
        self.assertEqual(view.statuses["K2"].decided_by, "arbitration",
                         "T2: adjudicated 见证 decided_by=arbitration")
        self.assertEqual(view.applied.get("K2"), (9001, 7001),
                         "T2: applied 采信 payload 仲裁对 (attest, generic)")
        self.assertEqual(view.completion["K1"].persisted, True,
                         "T2: K1 完成事实 persisted=True")
        self.assertEqual(view.completion["K1"].seal_id,
                         seal_unanimous["seal_id"], "T2: K1 seal_id 同源")
        self.assertEqual(view.completion["K2"].persisted, True,
                         "T2: K2 完成事实 persisted=True")
        self.assertEqual(view.completion["K2"].kind, "adjudicated",
                         "T2: K2 完成投影 kind=adjudicated")
        self.assertEqual(view.completion["K2"].trigger, "arbitration",
                         "T2: adjudicated trigger=arbitration")
        self.assertEqual(view.completion_required_uids,
                         tuple(sorted((USERS["alice"], USERS["bob"]))),
                         "T2: 完成 required uid 并集来自 payload")
        self.assertEqual(view.loader_stale, (), "T2: 无 stale 令牌")
        # —— 全完成请求零成员库打开（句柄探针：scope 退出后可换名）——
        alice_db = task_dir / (tasks_mod.slugify_screener("alice") + ".db")
        self.assertTrue(_p09_replaceable(alice_db),
                        "T2: alice.db 无泄漏句柄（零成员库打开行为锚）")
        self.assertTrue(_p09_replaceable(collab_path),
                        "T2: collab 屏障已释放（os.replace 成功）")
        self.assertTrue(_p09_replaceable(
            task_dir / (tasks_mod.slugify_screener("owner") + ".db")),
            "T2: owner catalog 连接已释放")


class ReadNeverPersistsTests(KeyedTeamViewsBase):
    """用例 3：受保护只读 scope 零持久化（文件态+语句面+收尾面三重锚）。"""

    def _build_t3_fixture(self, tag):
        """T3 混合夹具：见证 K1（unanimous include）＋一致 K2（live 共识）
        ＋冲突 K3（attestation 未 prepare→合法空→待裁）。"""
        task_id = "p09-t3-%s" % tag
        instance = "inst-p09-t3-%s" % tag
        task_dir = self._init_team_task("t3-" + tag, task_id)
        self._grant_member(task_dir, task_id, "alice")
        self._choice_member(task_dir, task_id, "alice")
        self._grant_member(task_dir, task_id, "bob")
        self._choice_member(task_dir, task_id, "bob")
        self._screener_db(task_dir, "owner", articles=("K1", "K2", "K3"))
        self._screener_db(task_dir, "alice", votes1=(
            ("K2", "include", P09_VOTE_UPDATED, P09_VOTE_SCREENED),
            ("K3", "include", P09_VOTE_UPDATED, P09_VOTE_SCREENED)))
        self._screener_db(task_dir, "bob", votes1=(
            ("K2", "include", P09_VOTE_UPDATED_LATE, P09_VOTE_SCREENED),
            ("K3", "exclude", P09_VOTE_UPDATED_LATE, P09_VOTE_SCREENED)))
        collab_path = task_dir / "collaboration.db"
        self._prepare_f2(collab_path, task_id, instance)
        self._prepare_f1(collab_path)
        self._append_collab_seal(collab_path, _p09_observation_payload(
            task_id=task_id, instance=instance, stage="stage1", item_key="K1",
            decision="include", sealed_at=P09_TS1))
        paths = (collab_path, task_dir / "owner.db", task_dir / "alice.db",
                 task_dir / "bob.db", self.users_db)
        return task_dir, task_id, instance, collab_path, paths

    def _assert_mixed_view(self, view):
        """T3 共用状态锚：见证/共识/待裁三态齐备且零 issue。"""
        self.assertEqual(view.statuses["K1"].status, tsr.FINAL_INCLUDE,
                         "T3: 见证键 FINAL")
        self.assertEqual(view.completion["K1"].persisted, True,
                         "T3: K1 persisted=True")
        self.assertEqual(view.statuses["K2"].status, tsr.FINAL_INCLUDE,
                         "T3: live 共识键 FINAL")
        self.assertEqual(view.completion["K2"].persisted, False,
                         "T3: K2 当前一致 persisted=False（不推造 seal/时间）")
        self.assertIsNone(view.completion["K2"].seal_id,
                          "T3: K2 seal_id=None（不造 seal）")
        self.assertEqual(view.statuses["K3"].status, tsr.AWAITING_ARBITRATION,
                         "T3: 冲突键待裁（attestation 未 prepare=合法空）")
        self.assertEqual(view.issues, {}, "T3: 健康夹具零 issue")
        self.assertEqual(set(view.statuses) | set(view.issues),
                         set(view.keys), "T3: 并集合同成立")

    def test_read_never_persists_observation(self):
        # ---- 子探针 a：受保护真路径——全库文件态零变化 ----
        with self.subTest(section="protected-path-file-state"):
            task_dir, task_id, instance, collab_path, paths = \
                self._build_t3_fixture("prot")
            runtime = self._make_runtime(task_dir, task_id)
            op = TeamOperation(name="p09-t3-screen", stage="screen")
            before = self._snapshot_state(paths)
            self.assertNotIn("team_flow_receipts", before[str(collab_path)]["counts"],
                             "T3: receipts 表两侧缺席（零 receipt 前提）")
            with tkr.protected_keyed_read_scope(
                    runtime, self._task_info(task_dir, task_id),
                    {"user_id": USERS["owner"], "username": "owner"},
                    operation=op, item_keys=("K1", "K2", "K3")) as scope:
                view = tsr.team_stage_view(scope, "stage1",
                                           item_keys=("K1", "K2", "K3"))
                self._assert_mixed_view(view)
                chain = tsr.team_chain_view(scope, "coding",
                                            item_keys=("K1", "K2", "K3"))
                self.assertEqual(set(chain.chain) | set(chain.issues),
                                 set(chain.stage1.keys),
                                 "T3: 链并集合同成立")
            after = self._snapshot_state(paths)
            self.assertEqual(before, after,
                             "T3: scope 退出后全库字节级+对象级+行级零变化")
            self.assertNotIn("team_flow_receipts",
                             after[str(collab_path)]["counts"],
                             "T3: 零 receipt（表未建）")
            self.assertEqual(
                after[str(collab_path)]["counts"]["team_terminal_seals"],
                before[str(collab_path)]["counts"]["team_terminal_seals"],
                "T3: 零 seal 写入")
            self.assertTrue(_p09_replaceable(collab_path),
                            "T3: 屏障句柄已释放（os.replace 成功）")
        # ---- 子探针 b：scope 级语句面（真实子类借用形态 collab 连接）----
        with self.subTest(section="scope-statement-surface"):
            task_dir, task_id, instance, collab_path, paths = \
                self._build_t3_fixture("probe")
            runtime = self._make_runtime(task_dir, task_id)
            op = TeamOperation(name="p09-t3-screen", stage="screen")
            probe = sqlite3.connect(str(collab_path), timeout=15,
                                    factory=_P09ProbeConnection)
            probe.row_factory = sqlite3.Row
            probe.execute("BEGIN IMMEDIATE")
            probe.statements.clear()
            try:
                ctx = runtime.readonly_context(task_id, {
                    "user_id": USERS["owner"], "username": "owner"})
                plan = build_keyed_read_plan(
                    ctx, collab_conn=probe, operation=op,
                    item_keys=("K1", "K2", "K3"))
                scope = tsr._build_scope(
                    ctx.info, accounts_db=self.users_db, collab_path=None,
                    owner_path=None, member_paths={}, connections={},
                    missing_members=frozenset(), fresh=True, collab_conn=probe)
                scope.attach_keyed_plan(plan, {}, frozenset())
                view = tsr.team_stage_view(scope, "stage1",
                                           item_keys=("K1", "K2", "K3"))
                self._assert_mixed_view(view)
                self.assertTrue(
                    all(_p09_is_readonly_statement(sql)
                        for sql in probe.statements),
                    "T3: collab 侧语句面全为 SELECT/PRAGMA（零 DML/DDL）："
                    + repr([sql for sql in probe.statements
                            if not _p09_is_readonly_statement(sql)]))
                self.assertEqual(probe.commit_calls, 0,
                                 "T3: 产品侧零 commit（借用形态连接）")
                self.assertEqual(probe.rollback_calls, 0,
                                 "T3: 产品侧零 rollback（scope 级借用连接"
                                 "收尾归调用方；屏障收尾见子探针 c）")
                self.assertEqual(probe.close_calls, 0,
                                 "T3: 产品侧零 close（借用形态连接）")
                scope.close_fresh()
            finally:
                _p09_close_quietly(probe)
        # ---- 子探针 c：单元级 _release_read_handle 收尾面 ----
        with self.subTest(section="release-handle-unit"):
            work = self.base / "release-probe"
            work.mkdir()
            probe_path = work / "probe.db"
            sqlite3.connect(str(probe_path)).close()
            probe = sqlite3.connect(str(probe_path), timeout=15,
                                    factory=_P09ProbeConnection)
            probe.execute("BEGIN")
            probe.statements.clear()
            tkr._release_read_handle(probe, primary=None)
            self.assertEqual(probe.statements, ["ROLLBACK"],
                             "T3: 收尾面恰一条 ROLLBACK（绝不 commit）")
            self.assertEqual(probe.commit_calls, 0, "T3: 收尾零 commit")
            self.assertEqual(probe.rollback_calls, 0,
                             "T3: 收尾走 execute('ROLLBACK') 语句面（P09 契约）"
                             "而非 conn.rollback() 方法")
            self.assertEqual(probe.close_calls, 1, "T3: 收尾必 close")
            _p09_close_quietly(probe)
            # closed 连接＝清理失败真实形态：主错经 add_note 附着不吞。
            closed = sqlite3.connect(str(probe_path), timeout=15,
                                     factory=_P09ProbeConnection)
            closed.execute("BEGIN")
            closed.close()
            primary = RuntimeError("p09-release-primary")
            tkr._release_read_handle(closed, primary=primary)
            notes = getattr(primary, "__notes__", ())
            self.assertTrue(
                any("protected keyed read handle release failure" in note
                    for note in notes),
                "T3: 清理失败 add_note 附着于主错（不遮蔽）")
            self.assertEqual(primary.args, ("p09-release-primary",),
                             "T3: 主错类型/消息不被改写")
            orphan = sqlite3.connect(str(probe_path), timeout=15,
                                     factory=_P09ProbeConnection)
            orphan.execute("BEGIN")
            orphan.close()
            with self.assertRaises(sqlite3.ProgrammingError,
                                   msg="T3: 无主错时清理错误如实上抛"):
                tkr._release_read_handle(orphan, primary=None)


class ChainPruningTests(KeyedTeamViewsBase):
    """用例 4：stage1 FINAL_EXCLUDE 剪 stage2；单 required 全票一致直接终局。"""

    def test_chain_pruning_and_single_required(self):
        task_id = "p09-t4-prune"
        instance = "inst-p09-t4"
        task_dir = self._init_team_task("t4", task_id)
        self._grant_member(task_dir, task_id, "alice")
        self._choice_member(task_dir, task_id, "alice")
        self._screener_db(task_dir, "owner", articles=("J", "L"))
        self._screener_db(task_dir, "alice", votes1=(
            ("L", "include", P09_VOTE_UPDATED, P09_VOTE_SCREENED),),
            votes2=(("L", "include", P09_VOTE_UPDATED_LATE,
                     P09_VOTE_SCREENED),))
        collab_path = task_dir / "collaboration.db"
        self._prepare_f2(collab_path, task_id, instance)
        self._prepare_f1(collab_path)
        self._append_collab_seal(collab_path, _p09_observation_payload(
            task_id=task_id, instance=instance, stage="stage1", item_key="J",
            decision="exclude", sealed_at=P09_TS1))
        op = TeamOperation(name="p09-t4-coding", stage="coding")
        runtime = self._make_runtime(task_dir, task_id)
        keys = ("J", "L")
        with tkr.protected_keyed_read_scope(
                runtime, self._task_info(task_dir, task_id),
                {"user_id": USERS["owner"], "username": "owner"},
                operation=op, item_keys=keys) as scope:
            chain_view = tsr.team_chain_view(scope, "coding", item_keys=keys)
            stage1 = chain_view.stage1
            stage2 = chain_view.stage2
        # —— stage1 见证 exclude 定 FINAL ——
        self.assertEqual(stage1.statuses["J"].status, tsr.FINAL_EXCLUDE,
                         "T4: stage1 见证 exclude FINAL_EXCLUDE")
        self.assertEqual(stage1.completion["J"].persisted, True,
                         "T4: J 完成事实 persisted=True")
        # —— 剪枝锚：J 不进 stage2 读取域 ——
        self.assertEqual(stage2.keys, ("L",),
                         "T4: stage1 FINAL_EXCLUDE 已剪出 stage2 键域")
        self.assertNotIn("J", stage2.statuses,
                         "T4: 剪枝键无 stage2 status")
        self.assertNotIn("J", stage2.issues,
                         "T4: 剪枝键无 stage2 issue")
        # —— 链结果与链级完成投影 ——
        self.assertFalse(chain_view.chain["J"].cleared, "T4: J 链阻断")
        self.assertEqual(chain_view.chain["J"].blocked_reason,
                         "upstream_stage1_final_exclude",
                         "T4: 阻断令牌=upstream_stage1_final_exclude")
        self.assertEqual(chain_view.completion["J"],
                         stage1.completion["J"],
                         "T4: 阻断链完成投影取阻断阶段（stage1）")
        self.assertTrue(chain_view.chain["L"].cleared, "T4: L 链 cleared")
        self.assertEqual(chain_view.completion["L"], stage2.completion["L"],
                         "T4: coding 目标 cleared 完成投影取 stage2 优先")
        # —— 单 required 全票一致直接终局（无仲裁读取：collab 全程无
        #    attestation 表而视图成功即行为锚）——
        self.assertEqual(stage1.statuses["L"].status, tsr.FINAL_INCLUDE,
                         "T4: 单 required 全票一致直接终局")
        self.assertEqual(stage1.statuses["L"].decided_by, "consensus",
                         "T4: decided_by=consensus（非仲裁）")
        self.assertIsNone(stage1.statuses["L"].applied_arbitration,
                          "T4: unanimous 不造仲裁 ID（C08）")
        self.assertNotIn("L", stage1.applied, "T4: applied 无 L")
        self.assertEqual(stage1.completion["L"],
                         CompletionProjection(
                             seal_id=None, kind="unanimous",
                             trigger="last_vote", sealed_at=None,
                             final_decision="include", persisted=False),
                         "T4: live 共识完成投影 persisted=False 全字段钉定")
        self.assertEqual(tsr.arbitration_ready_keys(stage1), [],
                         "T4: 单 required 无待裁键")
        self.assertEqual(tsr.stage_final_keys(stage1), ["L"],
                         "T4: helper 跳过 final_exclude 的 J")
        self.assertEqual(tsr.chain_cleared_keys(chain_view), ["L"],
                         "T4: 链 cleared helper")
        self.assertEqual(set(chain_view.chain) | set(chain_view.issues),
                         set(keys), "T4: 链并集合同成立")


class IssueHelperOrderTests(KeyedTeamViewsBase):
    """用例 5：stage_final_keys/arbitration_ready_keys 按 view.keys 序跳
    issue 键（请求序≠owner 域序，helper 输出钉请求序）。"""

    def test_issue_helpers_preserve_order(self):
        task_id = "p09-t5-helpers"
        instance = "inst-p09-t5"
        task_dir = self._init_team_task("t5", task_id)
        self._grant_member(task_dir, task_id, "alice")
        self._choice_member(task_dir, task_id, "alice")
        self._grant_member(task_dir, task_id, "bob")
        self._choice_member(task_dir, task_id, "bob")
        # owner 域序（import_order）=(C1, D, C3, C4)；请求序刻意穿插 ZZ 与
        # 逆序 C4/C3——helper 若误用域序/排序序即被此夹具识别。
        self._screener_db(task_dir, "owner",
                          articles=("C1", "D", "C3", "C4"))
        self._screener_db(task_dir, "alice", votes1=(
            ("C3", "include", P09_VOTE_UPDATED, P09_VOTE_SCREENED),
            ("C4", "exclude", P09_VOTE_UPDATED, P09_VOTE_SCREENED)))
        self._screener_db(task_dir, "bob", votes1=(
            ("C3", "exclude", P09_VOTE_UPDATED_LATE, P09_VOTE_SCREENED),
            ("C4", "include", P09_VOTE_UPDATED_LATE, P09_VOTE_SCREENED)))
        collab_path = task_dir / "collaboration.db"
        self._prepare_f2(collab_path, task_id, instance)
        self._prepare_f1(collab_path)
        self._append_collab_seal(collab_path, _p09_observation_payload(
            task_id=task_id, instance=instance, stage="stage1", item_key="C1",
            decision="include", sealed_at=P09_TS1))
        self._append_collab_seal(collab_path, _p09_observation_payload(
            task_id=task_id, instance=instance, stage="stage1", item_key="D",
            decision="exclude", sealed_at=P09_TS1))
        op = TeamOperation(name="p09-t5-screen", stage="screen")
        runtime = self._make_runtime(task_dir, task_id)
        keys = ("C1", "ZZ", "D", "C4", "C3")
        with tkr.protected_keyed_read_scope(
                runtime, self._task_info(task_dir, task_id),
                {"user_id": USERS["owner"], "username": "owner"},
                operation=op, item_keys=keys) as scope:
            view = tsr.team_stage_view(scope, "stage1", item_keys=keys)
        self.assertEqual(view.keys, keys, "T5: 请求序回显")
        self.assertEqual(set(view.issues), {"ZZ"}, "T5: ZZ 恰一域外 issue")
        self.assertEqual(view.statuses["C1"].status, tsr.FINAL_INCLUDE,
                         "T5: 见证 include")
        self.assertEqual(view.statuses["D"].status, tsr.FINAL_EXCLUDE,
                         "T5: 见证 exclude")
        self.assertEqual(view.statuses["C4"].status, tsr.AWAITING_ARBITRATION,
                         "T5: C4 冲突待裁")
        self.assertEqual(view.statuses["C3"].status, tsr.AWAITING_ARBITRATION,
                         "T5: C3 冲突待裁")
        self.assertEqual(tsr.stage_final_keys(view), ["C1"],
                         "T5: final keys=keys 序跳 issue/exclude 键")
        self.assertEqual(tsr.arbitration_ready_keys(view), ["C4", "C3"],
                         "T5: ready keys=keys 序跳 issue 键"
                         "（域序/排序序会得 [C3, C4]）")
        self.assertNotIn("ZZ", tsr.arbitration_ready_keys(view),
                         "T5: issue 键被跳过")
        self.assertNotIn("ZZ", tsr.stage_final_keys(view),
                         "T5: issue 键被跳过")


class LegacyRegressionDtoTests(KeyedTeamViewsBase):
    """支撑 1：旧全域路径正控（item_keys=None 行为不变）＋ NOTE-1 新默认键。"""

    def _build_s1_fixture(self, tag):
        """A/B/C 夹具：A 全票 include（共识终局）、B 分歧（待裁）、C 缺 bob
        票（awaiting_members）。"""
        task_id = "p09-s1-%s" % tag
        instance = "inst-p09-s1-%s" % tag
        task_dir = self._init_team_task("s1-" + tag, task_id)
        self._grant_member(task_dir, task_id, "alice")
        self._choice_member(task_dir, task_id, "alice")
        self._grant_member(task_dir, task_id, "bob")
        self._choice_member(task_dir, task_id, "bob")
        self._screener_db(task_dir, "owner", articles=("A", "B", "C"))
        self._screener_db(task_dir, "alice", votes1=(
            ("A", "include", P09_VOTE_UPDATED, P09_VOTE_SCREENED),
            ("B", "include", P09_VOTE_UPDATED, P09_VOTE_SCREENED),
            ("C", "include", P09_VOTE_UPDATED, P09_VOTE_SCREENED)))
        self._screener_db(task_dir, "bob", votes1=(
            ("A", "include", P09_VOTE_UPDATED_LATE, P09_VOTE_SCREENED),
            ("B", "exclude", P09_VOTE_UPDATED_LATE, P09_VOTE_SCREENED)))
        collab_path = task_dir / "collaboration.db"
        self._prepare_f2(collab_path, task_id, instance)
        self._prepare_f1(collab_path)
        return task_dir, task_id, collab_path

    def test_legacy_global_path_regression_and_dto_defaults(self):
        task_dir, task_id, collab_path = self._build_s1_fixture("global")
        with tsr.fresh_team_read_scope(
                self._task_info(task_dir, task_id),
                accounts_db=self.users_db) as scope:
            global_view = tsr.team_stage_view(scope, "stage1")
            global_chain = tsr.team_chain_view(scope, "stage2")
            keyed_view = tsr.team_stage_view(scope, "stage1",
                                             item_keys=("A", "B", "C"))
            keyed_chain = tsr.team_chain_view(scope, "stage2",
                                              item_keys=("A", "B", "C"))
        # —— 旧全域路径正控：逐键状态与 pending 语义不变 ——
        self.assertEqual(global_view.keys, ("A", "B", "C"),
                         "S1: 全域 keys=canonical 域（import_order 序）")
        self.assertEqual(global_view.statuses["A"].status, tsr.FINAL_INCLUDE,
                         "S1: A 共识终局")
        self.assertEqual(global_view.statuses["A"].decided_by, "consensus",
                         "S1: A decided_by=consensus")
        self.assertEqual(global_view.statuses["B"].status,
                         tsr.AWAITING_ARBITRATION, "S1: B 分歧待裁")
        self.assertEqual(global_view.statuses["C"].status,
                         AWAITING_MEMBERS, "S1: C 缺票待完成")
        self.assertEqual(global_view.statuses["C"].pending_reasons,
                         (("bob", "missing_vote"),),
                         "S1: C pending 语义逐成员钉定")
        self.assertTrue(global_view.config.ok, "S1: 名册配置完备（正控前提）")
        self.assertEqual(global_view.loader_stale, (), "S1: 无 stale")
        # —— NOTE-1：asdict 序列化面新增默认键（全域路径恒默认值）——
        self.assertEqual(
            set(dataclasses.asdict(global_view)),
            {"stage", "task_id", "round_id", "config", "statuses",
             "loader_stale", "keys", "applied", "issues", "completion",
             "completion_required_uids"},
            "S1: TeamStageView asdict 新增 issues/completion/"
            "completion_required_uids 三键（NOTE-1 集成同步点）")
        self.assertEqual(global_view.issues, {},
                         "S1: 全域路径 issues 恒空")
        self.assertEqual(global_view.completion, {},
                         "S1: 全域路径 completion 恒空")
        self.assertEqual(global_view.completion_required_uids, (),
                         "S1: 全域路径 completion_required_uids 恒 ()")
        self.assertEqual(
            set(dataclasses.asdict(global_chain)),
            {"target", "chain", "stage1", "stage2", "issues", "completion"},
            "S1: TeamChainView asdict 新增 issues/completion 两键")
        self.assertEqual(global_chain.issues, {},
                         "S1: 全域链 issues 恒空")
        self.assertEqual(global_chain.completion, {},
                         "S1: 全域链 completion 恒空")
        # —— 按键视图与全域视图等值正控（同 votes 快照两装载管道）——
        self.assertEqual(keyed_view.statuses, global_view.statuses,
                         "S1: 按键 statuses 与全域逐键相等")
        self.assertEqual(keyed_view.keys, global_view.keys,
                         "S1: 按键 keys=请求序回显")
        self.assertEqual(keyed_view.issues, {},
                         "S1: 健康键域按键零 issue")
        self.assertEqual(keyed_view.completion,
                         {"A": CompletionProjection(
                             seal_id=None, kind="unanimous",
                             trigger="last_vote", sealed_at=None,
                             final_decision="include", persisted=False)},
                         "S1: 按键 completion 钉定（共识键 persisted=False）")
        self.assertEqual(keyed_view.completion_required_uids,
                         tuple(sorted((USERS["alice"], USERS["bob"]))),
                         "S1: 按键完成 required uid 并集")
        self.assertEqual(keyed_chain.chain, global_chain.chain,
                         "S1: 按键链与全域链逐键相等")
        self.assertEqual(keyed_chain.completion["A"],
                         keyed_view.completion["A"],
                         "S1: 链完成投影（stage2 目标取 stage1）")


class CacheIsolationTests(KeyedTeamViewsBase):
    """支撑 2：按键 cache 与全域 cache 互不污染（三方向 pristine 对照）。"""

    def _build_s2_fixture(self, tag):
        """同 S1 形态夹具（A/B/C；C 缺 bob 票）。"""
        task_id = "p09-s2-%s" % tag
        instance = "inst-p09-s2-%s" % tag
        task_dir = self._init_team_task("s2-" + tag, task_id)
        self._grant_member(task_dir, task_id, "alice")
        self._choice_member(task_dir, task_id, "alice")
        self._grant_member(task_dir, task_id, "bob")
        self._choice_member(task_dir, task_id, "bob")
        self._screener_db(task_dir, "owner", articles=("A", "B", "C"))
        self._screener_db(task_dir, "alice", votes1=(
            ("A", "include", P09_VOTE_UPDATED, P09_VOTE_SCREENED),
            ("B", "include", P09_VOTE_UPDATED, P09_VOTE_SCREENED),
            ("C", "include", P09_VOTE_UPDATED, P09_VOTE_SCREENED)))
        self._screener_db(task_dir, "bob", votes1=(
            ("A", "include", P09_VOTE_UPDATED_LATE, P09_VOTE_SCREENED),
            ("B", "exclude", P09_VOTE_UPDATED_LATE, P09_VOTE_SCREENED)))
        collab_path = task_dir / "collaboration.db"
        self._prepare_f2(collab_path, task_id, instance)
        self._prepare_f1(collab_path)
        return task_dir, task_id, collab_path

    def test_cache_isolation_between_keyed_and_global(self):
        task_dir, task_id, collab_path = self._build_s2_fixture("cache")
        # 方向 1（全域→按键）：按键路径复用全域快照滤波，结果与 pristine 相等。
        with tsr.fresh_team_read_scope(
                self._task_info(task_dir, task_id),
                accounts_db=self.users_db) as scope:
            tsr.team_stage_view(scope, "stage1")
            polluted = tsr.team_stage_view(scope, "stage1",
                                           item_keys=("A", "B"))
        with tsr.fresh_team_read_scope(
                self._task_info(task_dir, task_id),
                accounts_db=self.users_db) as scope:
            pristine = tsr.team_stage_view(scope, "stage1",
                                           item_keys=("A", "B"))
        self.assertEqual(polluted.statuses, pristine.statuses,
                         "S2 方向1: 全域后按键=pristine（滤波复用正确）")
        self.assertEqual(polluted.completion, pristine.completion,
                         "S2 方向1: completion 隔离")
        # 方向 2（按键→全域）：全域路径必须自行全量读取（按键小子域缓存
        # 不得供全域使用），结果与 pristine 相等。
        with tsr.fresh_team_read_scope(
                self._task_info(task_dir, task_id),
                accounts_db=self.users_db) as scope:
            tsr.team_stage_view(scope, "stage1", item_keys=("C",))
            polluted_global = tsr.team_stage_view(scope, "stage1")
        with tsr.fresh_team_read_scope(
                self._task_info(task_dir, task_id),
                accounts_db=self.users_db) as scope:
            pristine_global = tsr.team_stage_view(scope, "stage1")
        self.assertEqual(polluted_global.statuses, pristine_global.statuses,
                         "S2 方向2: 按键后全域=pristine（全域不读按键缓存）")
        self.assertEqual(polluted_global.config, pristine_global.config,
                         "S2 方向2: config 隔离")
        # 方向 3（按键小子域→按键大子域）：cache 键含键域——(A,) 后 (A,B)
        # 的 B 必须真实读取，A 与 pristine 相等、B 不缺评。
        with tsr.fresh_team_read_scope(
                self._task_info(task_dir, task_id),
                accounts_db=self.users_db) as scope:
            tsr.team_stage_view(scope, "stage1", item_keys=("A",))
            polluted_big = tsr.team_stage_view(scope, "stage1",
                                               item_keys=("A", "B"))
        with tsr.fresh_team_read_scope(
                self._task_info(task_dir, task_id),
                accounts_db=self.users_db) as scope:
            pristine_big = tsr.team_stage_view(scope, "stage1",
                                               item_keys=("A", "B"))
        self.assertEqual(polluted_big.statuses["A"].status,
                         pristine_big.statuses["A"].status,
                         "S2 方向3: A 跨键域复用一致")
        self.assertEqual(polluted_big.statuses["B"].status,
                         pristine_big.statuses["B"].status,
                         "S2 方向3: B 不被小子域缓存吞掉（键域入 cache 键）")
        self.assertEqual(polluted_big.statuses, pristine_big.statuses,
                         "S2 方向3: 大子域整体=pristine")


class HighAnchorsTests(KeyedTeamViewsBase):
    """支撑 3：HIGH×2 行为锚（Row 工厂双兼容 / role_state 默认路径等值）。"""

    def test_high_anchors_row_factory_and_role_state_equivalence(self):
        task_id = "p09-s3-anchors"
        instance = "inst-p09-s3"
        task_dir = self._init_team_task("s3", task_id)
        self._grant_member(task_dir, task_id, "alice")
        self._choice_member(task_dir, task_id, "alice")
        self._grant_member(task_dir, task_id, "bob")
        self._choice_member(task_dir, task_id, "bob")
        self._screener_db(task_dir, "owner", articles=("A", "B", "C"))
        self._screener_db(task_dir, "alice", votes1=(
            ("A", "include", P09_VOTE_UPDATED, P09_VOTE_SCREENED),
            ("B", "include", P09_VOTE_UPDATED, P09_VOTE_SCREENED),
            ("C", "include", P09_VOTE_UPDATED, P09_VOTE_SCREENED)))
        self._screener_db(task_dir, "bob", votes1=(
            ("A", "include", P09_VOTE_UPDATED_LATE, P09_VOTE_SCREENED),
            ("B", "exclude", P09_VOTE_UPDATED_LATE, P09_VOTE_SCREENED)))
        collab_path = task_dir / "collaboration.db"
        self._prepare_f2(collab_path, task_id, instance)
        self._prepare_f1(collab_path)
        info = self._task_info(task_dir, task_id)
        keys = ("A", "B", "C")
        # —— 锚 1（HIGH-1）：_ro_connect Row 工厂下旧读取器双兼容 ——
        # 全域路径（vote_snapshot 位置访问）在 Row 工厂连接上照常工作；
        # 按键路径 vote_snapshot_keys_on 硬性要求 row_factory is sqlite3.Row
        # （基线 fresh 连接无 Row 工厂必 member_vote_unreadable 面）——
        # 同一 fresh scope 双路径健康即双兼容+Row 供给双锚。
        with tsr.fresh_team_read_scope(info, accounts_db=self.users_db) as scope:
            keyed = tsr.team_stage_view(scope, "stage1", item_keys=keys)
            legacy = tsr.team_stage_view(scope, "stage1")
        self.assertEqual(keyed.issues, {},
                         "锚1: 按键路径零 member_vote_unreadable/vote_row_*"
                         "（Row 工厂供给成立）")
        self.assertEqual(keyed.statuses["A"].status, tsr.FINAL_INCLUDE,
                         "锚1: 真票读取成功（consensus 终局）")
        self.assertEqual(legacy.statuses, keyed.statuses,
                         "锚1: 全域旧读取器（位置访问）与按键路径等值")
        self.assertEqual(legacy.statuses["B"].status,
                         tsr.AWAITING_ARBITRATION, "锚1: B 分歧语义不变")
        # —— 锚 2（HIGH-2）：role_state 默认路径等值 ——
        # fresh（stat＋自开连接路径）vs collab_conn 预置（屏障读路径）：
        # role_state() dict 相等、按键/全域视图 statuses+config 相等。
        barrier = sqlite3.connect(str(collab_path), timeout=15)
        barrier.row_factory = sqlite3.Row
        barrier.execute("BEGIN IMMEDIATE")
        try:
            scope_barrier = tsr._build_scope(
                info, accounts_db=self.users_db, collab_path=None,
                owner_path=None, member_paths={}, connections={},
                missing_members=frozenset(), fresh=True, collab_conn=barrier)
            try:
                scope_selfopen = tsr._build_scope(
                    info, accounts_db=self.users_db, collab_path=None,
                    owner_path=None, member_paths={}, connections={},
                    missing_members=frozenset(), fresh=True)
                try:
                    state_barrier = scope_barrier.role_state()
                    state_selfopen = scope_selfopen.role_state()
                    self.assertEqual(state_barrier, state_selfopen,
                                     "锚2: role_state 默认路径（stat+自开）"
                                     "与预置连接路径 dict 相等")
                    self.assertEqual(state_barrier["classification"],
                                     "owner_match", "锚2: owner_match 前提")
                    keyed_barrier = tsr.team_stage_view(
                        scope_barrier, "stage1", item_keys=keys)
                    keyed_selfopen = tsr.team_stage_view(
                        scope_selfopen, "stage1", item_keys=keys)
                    self.assertEqual(keyed_barrier.statuses,
                                     keyed_selfopen.statuses,
                                     "锚2: 两形态按键 statuses 相等")
                    self.assertEqual(keyed_barrier.config,
                                     keyed_selfopen.config,
                                     "锚2: 两形态 config（名册投影）相等")
                    legacy_barrier = tsr.team_stage_view(
                        scope_barrier, "stage1")
                    legacy_selfopen = tsr.team_stage_view(
                        scope_selfopen, "stage1")
                    self.assertEqual(legacy_barrier.config,
                                     legacy_selfopen.config,
                                     "锚2: 全域 config 两形态相等")
                    self.assertEqual(legacy_barrier.statuses,
                                     legacy_selfopen.statuses,
                                     "锚2: 全域 statuses 两形态相等")
                finally:
                    scope_selfopen.close_fresh()
            finally:
                scope_barrier.close_fresh()
        finally:
            _p09_close_quietly(barrier)


if __name__ == "__main__":
    unittest.main()
