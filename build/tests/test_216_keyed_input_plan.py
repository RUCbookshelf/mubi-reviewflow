# -*- coding: utf-8 -*-
"""P08 按键发现与 KeyedReadPlan（C07）独立测试候选 —— 静态撰写，作者未运行。

目标产品候选（本文件唯一针对的实现）：
- E:/rf216-scratch/p08-keyed-plan-20261118T193028Z-1377/candidate3/team_keyed_inputs.py
- SHA-256 5583cab70bbfdbc86409f8c6c5b6de1816c4eee462a8000690c1612e73ec5825
  （581 行；已过单点差量复审 PASS 冻结：全 pending 扫描快照 + found 三
  分支裁决（elif 互斥→conflicting_seals / else 同 seal 幂等保留首个 /
  blocked 复活防护）——首轮测试作者呈报的结构性不可达（candidate2 快照
  过滤）已修，冲突子断言现走主流程可达）。前版 candidate2 =
  45f704f13b956334de3a969431b9ba39a538dfb4c314b67220cac56e12a8acad（本箱
  首轮交付所测；首轮报告存档于会话记录）。
- 主仓基线不含 P08（已核：全仓无 team_keyed_inputs.py，custom_backend/ 下
  无同名文件）：本文件在测试箱内对候选副本（箱根/candidate/
  team_keyed_inputs.py，SHA 已核一致）经 importlib 文件位置加载运行
  （parents 协议同 P05/P06/P07 测试箱）；若对主仓 build/tests 运行将在模块
  加载期因 candidate/ 缺位而诚实红（明确 RuntimeError，不以 ImportError
  冒充行为红证；集成时由协调者迁移加载路径）。
- 候选 import coscreen.team_flow_types / team_terminal_facts /
  team_terminal_registry（P00/P02/P03 主仓现状）与
  custom_backend.team_source_lifecycle（P06 已落地主仓，SHA 见运行时打印）：
  本文件把主仓根 E:/mubi-reviewflow 插入 sys.path 供其解析；运行解释器必须
  -I -B 且 TEMP/TMP 指向测试箱 tmp/（隔离模式不向只读主仓写 __pycache__，
  临时文件不落系统盘）。主仓缺位时本文件以明确 RuntimeError 拒绝静默降级。

合同依据（全部只读引用，本文不编辑任何合同/产品文件）：
- docs/superpowers/plans/2026-11-16-2.1.6-team-contracts.md C07（按键发现与
  KeyedReadPlan）+ C02 借用纪律 + C04 覆盖词表；P08 包工单钉定本文件名与
  5 个具名用例（名称逐字）。

用例 → 合同映射（5 具名用例，工单钉死名称）：
- AWitnessSurvivesBadDTests.test_a_witness_survives_bad_d
    坏来源不遮蔽已获见证键：锚 plan.item_keys == ('A',)（请求键规整回显）
    + 坏 D 物理路径绝不进 selected_paths；坏 D 双形态（扫描内容坏：raw
    INSERT 坏 content_sha256 行→corrupt / DROP TRIGGER→schema_mismatch）只
    记入仍无见证键的临时失败表；先坏后好（BAD uid 2 < GOOD uid 9）与先好
    后坏（GOOD uid 2 < BAD uid 9，枚举序＝(source_user_id, generation)）两
    序同果；附 ('A','D') 双键形态钉"D 无见证→holder_unreadable issue、A 照常
    见证、D 保留 live 读依赖"。
- NoWitnessIncompleteDiscoveryTests.test_no_witness_with_incomplete_discovery_is_issue
    覆盖未核（coverage_status ∈ {pending_verification, unknown}）时无见证键
    一律 completion_source_unresolved issue（source_role=registry,
    retryable=True），绝不当干净负发现、issue 绝不缺位；干净负发现（覆盖
    完整 new_covered + 全部枚举 holder 可核）才进 live_dependencies；附带
    retired 代次不计 live、not_prepared（无 F1 表）≠失败、live 键序＝请求序。
- ConflictingSealsTests.test_conflicting_seals
    不同 holder 对同 (stage,key) 不同 seal（不同 sealed_at→不同 seal_id/
    content）→ plan.issues[...].reason=='conflicting_seals'（无 witness/无
    live 依赖/涉键不进 selected_paths）；三档联动（c3 各命中独立路径）：
    ①两 holder 互斥 seal→issue（elif 冲突路径）②同 seal 双 holder→幂等
    保留确定序首个（else 幂等路径）③冲突 blocked 后第三方（u606）再证
    →仍 issue 不复活（guard 复活防护路径）；B1 修复面：被撤销原见证键
    （cross_uid_alias：同物理路径×两 UID 登记）同样可达 issue 呈报
    （witness 撤销、blocked 进 issues、不保留 live）。
- Stage1ExcludePrunesStage2Tests.test_stage1_exclude_prunes_stage2
    coding/appraisal 链式操作 collab 命中 stage1 exclude（payload.
    final_decision=='exclude'）→ pending/holder 发现/live 依赖全无 stage2
    （同 plan 内未剪枝键 J 对照：两阶段 live 正常）；screen 单阶段不受
    影响（照常取 collab stage1 见证）；fulltext（HIGH#1 B1 后链式双阶段）
    同构受剪枝（stage1 exclude 剪掉 stage2 目标，链腿取 collab stage1
    见证）。
- ProvisionalHandlesReleasedTests.test_all_provisional_handles_released
    每 holder open→scan→release；_release_provisional_reader 单元语义（正常
    释放→关闭；rollback 非 commit；primary 优先+add_note 附着不遮蔽；无
    primary 时清理失败如实上抛）；pending 空（collab 全见证）零枚举零触碰来
    源库（对照：source 域布局破坏 + pending 非空 → registry_unavailable
    任务级 fail-closed，证明零枚举锚真实）；连接不出函数域（gc 反射计数
    无新增驻留 + Windows 句柄锁探针：os.replace 对已释放文件成功）。

支撑用例（3 个，先例允许）：
- CollabBorrowDisciplineTests.test_collab_borrowing_discipline
    C02 三查固定序（native/真实子类 → 活动事务 → Row 工厂）：伪装代理
    （__class__ property 冒充 sqlite3.Connection）拒 TypeError；真实子类
    （factory=子类）过门；无事务+错工厂双坏 → 先报事务；closed 连接
    ProgrammingError 原样透传（不包装）；零写路径（collab/holder 文件哈希
    调用前后不变）。
- FinalityPriorityTests.test_finality_priority_order
    五级终局优先序成对组合：见证>冲突（KW 有 collab 见证，坏 D+覆盖未决
    皆不产生 issue）＞冲突>失败（KC 先记 holder_unreadable 后见冲突，终局
    conflicting_seals）＞失败>覆盖未决（KF 终局 holder_unreadable 而非
    completion_source_unresolved）＞覆盖未决>负发现（KP 带 issue 而非干净
    负发现；对照 KN 完整覆盖才是干净负发现）；带 issue 键仍保留 live。
- StageDomainRejectTests.test_stage_domain_rejected
    stage 域拒：management/history 无 F1 适配 → ValueError（形状误用通道）；
    非 TeamOperation / 空 name / 非 str stage → TypeError/ValueError；四个
    合法 stage 全量冒烟（plan.item_keys 回显+干净负发现形状）。

夹具设计（零 mock；全部真实 SQLite 文件/真实产品 API 构造）：
- collab 库＝单文件共存 F2（prepare_team_terminal_registry：两表四触发器
  一部分唯一索引）+ F1（prepare_team_terminal_facts：seals/meta 表+两触发
  器）+ protocol 行（register_protocol_on trusted_new_task→new_covered /
  legacy_adoption→pending_verification；unknown＝raw INSERT legacy_verified
  行+坏 adoption_evidence_json——UPDATE 被触发器拦、INSERT 不拦，探针 C1
  已验 read_task_discovery_header_on 呈 unknown）+ source 行
  （register_source_on+make_source_identity 真 token；retire_source_on 制
  retired 代次）。
- holder 成员库＝task_dir 内真实文件，P04 meta canonical DDL 冻结镜像
  （P06 测试先例，探针 B1 已验与 library_identity_header_on 兼容）+ F1
  prepare（_objects 只扫 F1 自有对象名，无关表共存不误伤——探针 B1 已验）
  + seal 行（append_seal_on v2 observation payload，origin=actor；探针 D1
  已验双侧 origin 接受）。
- seal 内容形态：同 payload（同 sealed_at）跨库同 seal_id（幂等）/不同
  sealed_at 不同 seal_id（冲突）——探针 D2 已验。
- 坏 D：raw INSERT 坏 content_sha256 行（过 DDL CHECK 长度约束、被
  read_seal_on 判 corrupt，探针 E1）；DROP TRIGGER 形态（schema_mismatch，
  探针 E2）；无 F1 表形态（not_prepared=明确无键非失败，探针 E3）。

诚实记录（环境不可达/机制实测项，不伪造测试；探针日志
selfcheck/probe_env.log 存档，19 项机制/夹具检查全按预期；E4＝隔离运行
夹具 bug 回归钉）：
1. 打开失败（N2 路径，_open_provisional_reader 的 except
   sqlite3.DatabaseError 分支）在集成流不可零 mock 顺序构造：P06 身份解析
   先以同一 mode=ro URI 打开同一文件，任何打开层失败都先以
   SourceIdentityIssue(library_unverifiable)→source_identity_unresolved 呈
   现，轮不到第二次打开。故 N2 分支以单元级钉（缺失文件/目录路径 →
   sqlite3.DatabaseError 家族），集成级坏 D 以扫描内容坏双形态（corrupt 行/
   DROP TRIGGER）覆盖。
2. build_keyed_read_plan 扫描窗口内（reader 已开后）的非 TerminalFacts
   异常路径（先释放再重抛+finally 兜底的中段）同样不可顺序构造
   （read_seal_on 把全部 sqlite 错误包成 TerminalFactsUnavailable，
   ProgrammingError 仅 closed 连接触发，而模块自建 reader 不会中途变
   closed）。_release_provisional_reader 的 primary/add_note/无 primary
   语义以真实连接单元级钉；finally 兜底由"任何返回/异常路径后文件可
   replace、哈希不变"覆盖。
3. sqlite3.Connection 不支持 weakref（探针 A1 TypeError 实测）；泄漏反射
   改用 gc.get_objects() 计数（探针 A2 实测 gc 可见+本用例内置机制自证：
   打开即 +1），辅以 Windows 句柄锁探针（探针 A3/A4/A5 实测：任何打开句
   柄——含活动只读事务——都令 os.replace PermissionError，关闭后成功；
   该探针即"连接不出函数域"的行为断言）。
4. pending 空零打开的可观测锚：source 域布局破坏（DROP 部分唯一索引）+
   collab 全见证 → plan 照常成功（枚举根本不发生，iter_source_discovery_on
   的 eager source 域校验不被触发）；对照（同布局+一 pending 键）→
   KeyedPlanUnavailableError('registry_unavailable')。
5. Windows 本机无文件 symlink 特权（P06 箱 probe 先例）；本文件不需要
   junction 夹具（cross_uid_alias 用同文件×两 token 构造，探针 F3 已验两
   token 均解析成功）。
6. 【首轮三项发现·主控裁定回执（联动 c3）】①candidate2 两 holder
   conflicting_seals 分支结构性不可达（快照＝pending∩unresolved + 首命中
   原子 discard）＝已修产品：candidate3 扫描快照改全 pending + found 三
   分支裁决 + blocked 复活防护，本文件冲突子断言与"冲突>失败"子断言现
   走主流程可达、应绿，并补三档联动子断言（elif/else/guard 各自独立
   命中）；②N2 打开失败分支集成级不可零 mock 顺序构造（P06 身份解析先
   以同一 ro URI 打开同一文件）＝单元级钉法接受（维持本文件现状）；
   ③F1 扫描失败但身份已核+active 的来源仍计 live_dependencies（append
   先于 failure 检查）＝维持 fail-safe 语义接受，测试侧只钉好来源侧、
   注释保留、不越权钉死候选选择。
7.【隔离运行回执（c3 配对首轮：6/8 绿，1F+2E，两项均已定因修复）】
   ①corrupt-row ERROR×2（坏序/好序同因）＝夹具 bug：坏库双注入（键 A/D）
   的 corrupt 行共用死值 seal_id="d"*64，第二行 INSERT 撞 seal_id 唯一
   约束、在构造期即炸，到不了被测扫描路径——修复为按 (instance,stage,
   key) 确定性唯一 seal_id（corrupt 面仍是 digest 失配，非唯一约束）；
   ②gc 驻留 2>1（用例 5）＝观测窗错位（测试 bug），非产品泄漏：已核
   candidate3 :466-532 open/release 流（两 reader 均 release→close→
   open_readers.remove，finally 兜底空；P06 身份连接自有 close；异常无
   留存通道），且首轮算术恰为 sensitivity(1)+自有借用 conn(1)——基线拍
   在自有连接打开之前所致的恒 +1 假红（若产品泄漏应呈 3>1）——修复为
   基线移到自有连接打开之后 + 计数前 gc.collect()（口径：可达驻留）。

撰写协议（同 P04/P06/P07 先例）：本文件每个 def/class 符号（含 __init__
与两个支撑类的方法，共 48 项，与 raw/SYMBOLS.tsv+SYMBOLS_DELTA.tsv 一致；
文件按构造不含 lambda、不含内嵌 def——scripts/selfcheck.py AST 自检钉住）
在写入前均逐项运行 GitNexus CLI upstream impact（--repo E:/mubi-reviewflow
--file build/tests/test_216_keyed_input_plan.py --kind Class/Method/Function
--include-tests）；新文件预期 not found/UNKNOWN，raw/rc/MANIFEST 存档于本箱
raw/（41+7 项，每次 CLI 前经 ps 门确认无其他 gitnexus 作业）。

未运行声明：作者仅静态撰写（ast.parse/compile 与符号清单自检），未运行
任何测试用例/产品候选 API；唯一执行物为环境可行性探针（scripts/
probe_env.py，只读引用主仓产品 API 对探针自建库操作，日志存
selfcheck/probe_env.log）与 AST 自检脚本。执行留给独立 runner 在应用产品
候选后进行。禁主仓/前端/真实 data/8613/旧证据箱写入；夹具全部位于测试
自有 TemporaryDirectory（运行时 TEMP 需指测试箱）；零 mock（无替换对象/
无猴子补丁；全部真实文件系统与真实产品 API）。自包含：不依赖 conftest/
Safety 夹具、不触 HTTP 层；静态消费者仅 unittest。
"""

from __future__ import annotations

import gc
import hashlib
import importlib.util
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

_P08_BOX_ROOT = Path(__file__).resolve().parents[2]
_P08_CANDIDATE = _P08_BOX_ROOT / "candidate" / "team_keyed_inputs.py"
_P08_REPO_ROOT = Path("E:/mubi-reviewflow")
if not _P08_REPO_ROOT.is_dir():
    raise RuntimeError(
        "P08 测试需要主仓 E:/mubi-reviewflow（候选 import coscreen P00/P02/P03 "
        "与 custom_backend P06）；主仓缺位时本文件拒绝静默降级")
if not _P08_CANDIDATE.is_file():
    raise RuntimeError(
        "P08 测试需要箱内候选副本（箱根/candidate/team_keyed_inputs.py）。"
        "对主仓基线运行时本文件在模块加载期诚实红：主仓尚无 P08，本文件"
        "不冒充行为红证（集成时由协调者迁移加载路径）")
if str(_P08_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_P08_REPO_ROOT))
_P08_SPEC = importlib.util.spec_from_file_location(
    "team_keyed_inputs", _P08_CANDIDATE)
tki = importlib.util.module_from_spec(_P08_SPEC)
_P08_SPEC.loader.exec_module(tki)

from coscreen import team_terminal_facts as tf          # noqa: E402
from coscreen import team_terminal_registry as tr       # noqa: E402
from coscreen.team_flow_types import KeyedReadPlan, TeamOperation  # noqa: E402
from custom_backend import team_source_lifecycle as tsl  # noqa: E402

#: 冻结候选 SHA-256（运行时打印实测值供核对，不做硬断言：候选后续修订经
#: 复审后 SHA 自然变化，硬断言会制造假红）。
P08_CANDIDATE_SHA = (
    "5583cab70bbfdbc86409f8c6c5b6de1816c4eee462a8000690c1612e73ec5825")

#: 时间戳字面量（F1 sealed_at 合同形态，毫秒 Z）。
P08_TS1 = "2026-11-18T09:00:00.000Z"
P08_TS2 = "2026-11-18T09:30:00.000Z"
P08_TS3 = "2026-11-18T10:15:00.000Z"

#: owner（collab 权威域）library_id；各 holder uid → library_id 确定性取值。
P08_OWNER_LIBRARY = "1a" * 16
P08_LIB = {
    2: "22" * 16, 5: "50" * 16, 7: "70" * 16, 9: "90" * 16, 11: "b0" * 16,
    202: "2a" * 16, 303: "3b" * 16, 404: "4c" * 16, 505: "5d" * 16,
    606: "6e" * 16,
}

#: P04 身份头所需 meta 表 canonical DDL 冻结镜像（P06 测试先例；
#: coscreen/team_vote_ledger.py _META_DDL，format 2；本箱探针 B1 已验
#: 与 library_identity_header_on 兼容且与 F1 两表共存无碍）。
P04_META_DDL = (
    "CREATE TABLE team_vote_revision_meta (\n"
    "  singleton INTEGER PRIMARY KEY CHECK(singleton=1),\n"
    "  format_version INTEGER NOT NULL CHECK(format_version=2),\n"
    "  library_id TEXT NOT NULL CHECK(length(library_id)=32),\n"
    "  seeded_stage1 INTEGER NOT NULL"
    " CHECK(typeof(seeded_stage1)='integer' AND seeded_stage1>=0),\n"
    "  seeded_stage2 INTEGER NOT NULL"
    " CHECK(typeof(seeded_stage2)='integer' AND seeded_stage2>=0),\n"
    "  advances INTEGER NOT NULL"
    " CHECK(typeof(advances)='integer' AND advances>=0) DEFAULT 0\n"
    ")"
)

TAG_BAD_D = "P08-bad D does not mask witnessed A"
TAG_COVERAGE = "P08-incomplete discovery is issue never negative"
TAG_CONFLICT = "P08-conflicting seals"
TAG_PRUNE = "P08-stage1 exclude prunes stage2"
TAG_RELEASE = "P08-provisional handles released"
TAG_BORROW = "P08-collab borrow discipline"
TAG_PRIORITY = "P08-finality priority order"
TAG_STAGE = "P08-stage domain reject"

_P08_PROVENANCE_PRINTED = False


def p08_real(path):
    """realpath 归一（镜像产品定位/包含判定的物理基准）。"""
    return Path(os.path.realpath(str(path)))


def p08_sha256_file(path):
    """文件字节哈希（零写纪律/泄漏探针的观测锚）。"""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def p08_ctx(task_id, task_dir):
    """构造 ctx 最小结构合同载体（.info.task_id / .info.dir）。"""
    return _P08Ctx(task_id, task_dir)


def p08_default_roster():
    """三成员 roster（owner uid1 非必需筛选人；uid2/3 必需筛选人）。"""
    return [
        {"uid": 1, "username": "owner", "role": "reconciler",
         "is_owner": True, "screening_enabled": False,
         "required_screener": False, "source": "owner-source", "config": "ok"},
        {"uid": 2, "username": "alice", "role": "coder",
         "is_owner": False, "screening_enabled": None,
         "required_screener": True, "source": "import-source", "config": "ok"},
        {"uid": 3, "username": "bob", "role": "coder",
         "is_owner": False, "screening_enabled": None,
         "required_screener": True, "source": "grant-source", "config": "ok"},
    ]


def p08_make_opinion(uid, decision):
    """单个必需筛选人意见（意见/行哈希按 F1 合同算法独立重算）。"""
    opinion = {
        "uid": uid, "decision": decision, "exclusion_reason": "", "notes": "",
        "library_id": "%032x" % uid, "revision": uid + 10,
        "source_row": {"tags": "tag-%s" % uid,
                       "screened_at": "2026-01-02 03:04:05",
                       "updated_at": None if uid % 2 else "2026-01-03 04:05:06"},
        "opinion_sha256": "", "source_row_sha256": "",
    }
    opinion["opinion_sha256"] = hashlib.sha256(p08_canonical_json({
        "decision": decision.strip().lower(), "exclusion_reason": "",
        "notes": ""}).encode("utf-8")).hexdigest()
    opinion["source_row_sha256"] = hashlib.sha256(p08_canonical_json(
        opinion["source_row"]).encode("utf-8")).hexdigest()
    return opinion


def p08_canonical_json(value):
    """canonical JSON 镜像（独立重实现，钉 F1 合同序列化参数）。"""
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def p08_observation_payload(*, item_key, stage, decision,
                            task_instance_id, sealed_at=P08_TS1):
    """v2 observation seal payload（探针 D1 已验 origin=collab/actor 双侧
    append_seal_on 接受；同参确定性→同 seal_id，探针 D2）。"""
    roster = p08_default_roster()
    required_uids = sorted(e["uid"] for e in roster if e["required_screener"])
    opinions = [p08_make_opinion(uid, decision) for uid in required_uids]
    bindings = [
        {"source_user_id": uid, "registration_id": "registration-%d" % uid,
         "generation": 1, "source_library_id": "%032x" % uid}
        for uid in sorted(set(required_uids) | {1})
    ]
    return {
        "schema": tf.OBSERVATION_PAYLOAD_SCHEMA,
        "task": {"task_id": "task-p08", "task_instance_id": task_instance_id,
                 "owner_user_id": 1, "owner_username": "owner",
                 "owner_library_id": P08_OWNER_LIBRARY},
        "stage": stage, "item_key": item_key, "round_id": None,
        "kind": "unanimous", "trigger": "current_observation",
        "final_decision": decision, "roster": roster,
        "required_uids": required_uids, "opinions": opinions,
        "arbitration": None,
        "protocol": {"source_registration_id": "registration-1",
                     "roster_revision": 7},
        "source_request": {"operation": "completion_observe",
                           "request_id": "request-p08-1",
                           "request_sha256": "b" * 64},
        "sealed_at": sealed_at,
        "observation": {"host_operation": "completion_observe",
                        "host_stage": "fulltext", "host_item_keys": [item_key],
                        "observed_by_user_id": 1,
                        "basis": "current_snapshot"},
        "source_bindings": bindings,
    }


def p08_borrowed(db_path):
    """C02 借用形态连接：native、Row 工厂、活动 IMMEDIATE 事务。"""
    conn = sqlite3.connect(str(db_path), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("BEGIN IMMEDIATE")
    return conn


def p08_close_quietly(conn):
    """测试自有连接安静回收（rollback 余留事务后 close）。"""
    try:
        if conn.in_transaction:
            conn.rollback()
    except sqlite3.Error:
        pass
    try:
        conn.close()
    except sqlite3.Error:
        pass


def p08_add_seal(db_path, *, task_instance_id, stage, item_key, decision,
                 sealed_at=P08_TS1, origin):
    """向任一 F1 已备库追加一枚 v2 seal（真实 append_seal_on），返回记录。"""
    conn = p08_borrowed(db_path)
    try:
        record = tf.append_seal_on(
            conn, p08_observation_payload(
                item_key=item_key, stage=stage, decision=decision,
                task_instance_id=task_instance_id, sealed_at=sealed_at),
            origin=origin)
        conn.commit()
        return record
    finally:
        p08_close_quietly(conn)


def p08_insert_corrupt_seal_row(db_path, *, task_instance_id, stage,
                                item_key, sealed_at=P08_TS1):
    """坏 D 形态一：raw INSERT 坏 content_sha256 行（过 DDL CHECK、被
    read_seal_on 判 corrupt——UPDATE/DELETE 被触发器拦，INSERT 不拦；
    探针 E1 已验）。seal_id 按 (instance,stage,key) 确定性唯一：corrupt 的
    本意是"扫描内容坏"（digest 与 payload 失配，read_seal_on 首判即拒），
    绝不是撞 seal_id 唯一约束——同库多枚 corrupt 行（键 A/D 双注入）不得
    在 INSERT 期互撞（隔离运行首轮 ERROR 即此夹具 bug）。注入字段自查：
    语义键 (instance,stage,item_key) 每 key 至多一行；kind/final_decision/
    origin/sealed_at/payload_json/payload_sha256 全为合法真值；唯一失配面
    =content_sha256（"e"*64 ≠ payload 真实 digest）。"""
    payload = p08_observation_payload(
        item_key=item_key, stage=stage, decision="include",
        task_instance_id=task_instance_id, sealed_at=sealed_at)
    payload_json = p08_canonical_json(payload)
    corrupt_seal_id = hashlib.sha256(
        ("p08-corrupt-seal:%s:%s:%s" % (task_instance_id, stage, item_key))
        .encode("utf-8")).hexdigest()
    conn = p08_borrowed(db_path)
    try:
        conn.execute(
            "INSERT INTO main.team_terminal_seals (seal_id, task_instance_id,"
            " stage, item_key, kind, final_decision, content_sha256,"
            " payload_sha256, sealed_at, archived_at, origin, payload_json)"
            " VALUES (?,?,?,?,?,?,?,?,?,NULL,?,?)",
            (corrupt_seal_id, task_instance_id, stage, item_key, "unanimous",
             "include", "e" * 64,
             hashlib.sha256(payload_json.encode("utf-8")).hexdigest(),
             payload["sealed_at"], "actor", payload_json))
        conn.commit()
    finally:
        p08_close_quietly(conn)


def p08_collab_db(base, name, *, task_id, task_instance_id,
                  coverage="new_covered", owner_library_id=P08_OWNER_LIBRARY):
    """collab 库：F2+F1 同文件共存 + protocol 行（三种 coverage 形态）。

    coverage: new_covered（trusted_new_task）/ pending_verification
    （legacy_adoption）/ unknown（raw INSERT legacy_verified 行 + 坏
    adoption_evidence_json——探针 C1 已验 read_task_discovery_header_on
    呈 unknown）。"""
    path = Path(base) / name
    conn = sqlite3.connect(str(path))
    conn.close()
    tr.prepare_team_terminal_registry(path)
    tf.prepare_team_terminal_facts(path)
    if coverage == "new_covered":
        creation = "trusted_new_task"
    elif coverage == "pending_verification":
        creation = "legacy_adoption"
    elif coverage != "unknown":
        raise ValueError("unknown coverage fixture form: %r" % coverage)
    writer = p08_borrowed(path)
    try:
        if coverage == "unknown":
            writer.execute(
                "INSERT INTO main.team_task_protocol (task_instance_id,"
                " task_id, owner_user_id, owner_library_id, protocol_version,"
                " coverage_state, roster_revision, adopted_at,"
                " adoption_evidence_ref, adoption_evidence_json,"
                " adoption_evidence_sha256, created_at, updated_at)"
                " VALUES (?,?,?,?,1,'legacy_verified',0,"
                "'2026-11-16T10:00:00.000Z','ref-p08','not json at all',?,"
                "'2026-11-16T12:00:00.000Z','2026-11-16T12:00:00.000Z')",
                (task_instance_id, task_id, 1, owner_library_id, "c" * 64))
        else:
            tr.register_protocol_on(
                writer, task_id=task_id, task_instance_id=task_instance_id,
                owner_user_id=1, owner_library_id=owner_library_id,
                creation_path=creation)
        writer.commit()
    finally:
        p08_close_quietly(writer)
    return path


def p08_register_source(collab_path, *, task_instance_id, uid, library_id,
                        task_dir, member_path, registration_id):
    """F2 登记一个来源：P06 真 token（make_source_identity）+
    register_source_on；registration_id 确定性（含 g2 再登记形态）。"""
    token = tsl.make_source_identity(
        task_dir=task_dir, source_path=member_path, source_user_id=uid,
        source_library_id=library_id)
    conn = p08_borrowed(collab_path)
    try:
        record = tr.register_source_on(
            conn, task_instance_id=task_instance_id, source_user_id=uid,
            source_library_id=library_id, source_path_identity=token,
            registration_id=registration_id)
        conn.commit()
        return record
    finally:
        p08_close_quietly(conn)


def p08_member_library(task_dir, relative, library_id, *, with_f1=True):
    """holder 成员库：task_dir 内真实文件，P04 meta canonical 行（身份头）
    +（默认）F1 prepare。返回绝对路径。"""
    path = Path(task_dir) / Path(relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(P04_META_DDL)
        conn.execute(
            "INSERT INTO team_vote_revision_meta (singleton, format_version,"
            " library_id, seeded_stage1, seeded_stage2, advances)"
            " VALUES (1, 2, ?, 0, 0, 0)", (library_id,))
        conn.commit()
    finally:
        conn.close()
    if with_f1:
        tf.prepare_team_terminal_facts(path)
    return path


def p08_count_live_connections():
    """gc 反射计数 sqlite3.Connection（探针 A2 实测 gc 可见；weakref 不支持
    ——探针 A1，故以计数+句柄锁探针双轨钉泄漏）。计数前先 gc.collect()：
    清掉不可达循环垃圾再观测——本计数钉的是"可达驻留引用"（真泄漏），
    可回收的循环暂存不算泄漏，同时消除回收时机噪声。"""
    gc.collect()
    count = 0
    for obj in gc.get_objects():
        if isinstance(obj, sqlite3.Connection):
            count += 1
    return count


class _P08CtxInfo:
    """ctx.info 载体（task_id / dir 两字段最小合同）。"""

    def __init__(self, task_id, task_dir):
        self.task_id = task_id
        self.dir = task_dir


class _P08Ctx:
    """WorkflowContext 形状载体（.info）。"""

    def __init__(self, task_id, task_dir):
        self.info = _P08CtxInfo(task_id, task_dir)


class P08NativeSubConn(sqlite3.Connection):
    """真实 native 子类（C02：真实子类过门——非伪装代理）。"""


class P08DisguiseProxy:
    """伪装代理：__class__ property 冒充 sqlite3.Connection（C02 拒绝面）。"""

    def __init__(self, inner):
        self._inner = inner

    @property
    def __class__(self):
        return sqlite3.Connection

    def __getattr__(self, name):
        return getattr(self._inner, name)


class KeyedInputPlanTestBase(unittest.TestCase):
    """公共基座：真实文件系统夹具（TemporaryDirectory + 真实 SQLite 文件），
    全部经主仓产品 API 构造，零 mock。"""

    maxDiff = None

    def setUp(self):
        global _P08_PROVENANCE_PRINTED
        self._tmp = tempfile.TemporaryDirectory(prefix="rf216_p08_")
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        if not _P08_PROVENANCE_PRINTED:
            _P08_PROVENANCE_PRINTED = True
            print("P08_TEST_FILE_SHA",
                  p08_sha256_file(Path(__file__).resolve()))
            print("P08_CANDIDATE_SOURCE_SHA",
                  p08_sha256_file(_P08_CANDIDATE))
            print("P08_CANDIDATE_PINNED_SHA", P08_CANDIDATE_SHA)
            print("P08_REPO_TYPES_SHA", p08_sha256_file(
                _P08_REPO_ROOT / "coscreen" / "team_flow_types.py"))
            print("P08_REPO_FACTS_SHA", p08_sha256_file(
                _P08_REPO_ROOT / "coscreen" / "team_terminal_facts.py"))
            print("P08_REPO_REGISTRY_SHA", p08_sha256_file(
                _P08_REPO_ROOT / "coscreen" / "team_terminal_registry.py"))
            print("P08_REPO_LIFECYCLE_SHA", p08_sha256_file(
                _P08_REPO_ROOT / "custom_backend"
                / "team_source_lifecycle.py"))
            print("P08_PYTHON", sys.version.split()[0])
            print("P08_SQLITE_VERSION", sqlite3.sqlite_version)

    def _task_dir(self, name="task"):
        """新建真实任务目录（绝对路径）。"""
        path = self.base / name
        path.mkdir()
        return path

    def _collab(self, name, *, task_id, instance, coverage="new_covered"):
        """collab 库捷径（见 p08_collab_db）。"""
        return p08_collab_db(
            self.base, name, task_id=task_id, task_instance_id=instance,
            coverage=coverage)

    def _member(self, task_dir, uid, *, with_f1=True):
        """成员库捷径：members/u<uid>/library.db，library_id 按 uid 确定。"""
        return p08_member_library(
            task_dir, "members/u%d/library.db" % uid, P08_LIB[uid],
            with_f1=with_f1)

    def _register(self, collab_path, *, instance, uid, task_dir,
                  member_path, suffix=""):
        """登记捷径：registration_id = reg-u<uid><suffix>。"""
        return p08_register_source(
            collab_path, task_instance_id=instance, uid=uid,
            library_id=P08_LIB[uid], task_dir=task_dir,
            member_path=member_path, registration_id="reg-u%d%s" % (uid,
                                                                    suffix))

    def _plan(self, collab_path, *, task_id, task_dir, operation, keys):
        """以借用连接调用候选 build_keyed_read_plan（自管连接回收）。"""
        conn = p08_borrowed(collab_path)
        try:
            return tki.build_keyed_read_plan(
                p08_ctx(task_id, task_dir), collab_conn=conn,
                operation=operation, item_keys=keys)
        finally:
            p08_close_quietly(conn)

    def _assert_replaceable(self, path, tag):
        """Windows 句柄锁探针（探针 A3-A5 实测）：任何打开句柄都令
        os.replace PermissionError；操作本身即断言（泄漏即诚实红）。"""
        moved = Path(str(path) + ".p08probe")
        os.replace(path, moved)
        os.replace(moved, path)
        self.assertTrue(Path(path).is_file(), tag)


class AWitnessSurvivesBadDTests(KeyedInputPlanTestBase):
    """用例 1：坏来源 D 不遮蔽已获见证键 A；失败只记仍无见证键。"""

    def test_a_witness_survives_bad_d(self):
        op = TeamOperation(name="p08-screen", stage="screen")
        # --- 单元级：N2 打开失败分支的异常类合同（缺失文件/目录路径） ---
        # 集成级不可顺序构造的理由见模块 docstring 诚实记录 1。
        missing = self.base / "no_such_holder.db"
        with self.subTest(open_fail="missing file"):
            with self.assertRaises(sqlite3.DatabaseError, msg=TAG_BAD_D):
                tki._open_provisional_reader(missing)
        directory = self.base / "a_directory"
        directory.mkdir()
        with self.subTest(open_fail="directory path"):
            with self.assertRaises(sqlite3.DatabaseError, msg=TAG_BAD_D):
                tki._open_provisional_reader(directory)
        # --- 集成级双形态 × 双序（坏 D 只影响仍无见证键） ---
        for form in ("corrupt-row", "dropped-trigger"):
            for bad_uid, good_uid in ((2, 9), (9, 2)):
                with self.subTest(form=form, order="bad-first" if bad_uid <
                                  good_uid else "good-first"):
                    task = self._task_dir(
                        "task-%s-%d%d" % (form, bad_uid, good_uid))
                    good = self._member(task, good_uid)
                    bad = self._member(task, bad_uid)
                    seal = p08_add_seal(
                        good, task_instance_id="instance-badd",
                        stage="stage1", item_key="A", decision="include",
                        origin="actor")
                    if form == "corrupt-row":
                        p08_insert_corrupt_seal_row(
                            bad, task_instance_id="instance-badd",
                            stage="stage1", item_key="A")
                        p08_insert_corrupt_seal_row(
                            bad, task_instance_id="instance-badd",
                            stage="stage1", item_key="D")
                    else:
                        conn = p08_borrowed(bad)
                        try:
                            conn.execute(
                                "DROP TRIGGER"
                                " main.team_terminal_seals_no_delete")
                            conn.commit()
                        finally:
                            p08_close_quietly(conn)
                    collab = self._collab(
                        "collab-badd-%s-%d%d.db" % (form, bad_uid, good_uid),
                        task_id="task-badd-%s-%d%d" % (form, bad_uid,
                                                       good_uid),
                        instance="instance-badd")
                    self._register(
                        collab, instance="instance-badd", uid=bad_uid,
                        task_dir=task, member_path=bad)
                    self._register(
                        collab, instance="instance-badd", uid=good_uid,
                        task_dir=task, member_path=good)
                    bad_hash = p08_sha256_file(bad)
                    good_hash = p08_sha256_file(good)
                    # 形态 a：请求键 ('A',) —— 锚 item_keys == ('A',)
                    plan = self._plan(
                        collab, task_id="task-badd-%s-%d%d" % (form, bad_uid,
                                                               good_uid),
                        task_dir=task, operation=op, keys=["A"])
                    self.assertIsInstance(plan, KeyedReadPlan, TAG_BAD_D)
                    self.assertEqual(plan.item_keys, ("A",), TAG_BAD_D)
                    witness = plan.witnesses.get(("stage1", "A"))
                    self.assertIsNotNone(witness, TAG_BAD_D + ": A 见证存活")
                    self.assertEqual(witness.seal_id, seal["seal_id"],
                                     TAG_BAD_D)
                    self.assertEqual(witness.source_path, p08_real(good),
                                     TAG_BAD_D + ": A 见证锚好来源")
                    self.assertEqual(witness.registration_id,
                                     "reg-u%d" % good_uid, TAG_BAD_D)
                    self.assertEqual(witness.generation, 1, TAG_BAD_D)
                    self.assertEqual(witness.library_id, P08_LIB[good_uid],
                                     TAG_BAD_D)
                    self.assertEqual(plan.selected_paths, (p08_real(good),),
                                     TAG_BAD_D + ": selected 只含好来源")
                    self.assertNotIn(p08_real(bad), plan.selected_paths,
                                     TAG_BAD_D + ": 坏 D 不进 selected_paths")
                    self.assertNotIn(("stage1", "A"), plan.issues,
                                     TAG_BAD_D + ": 已见证键无 issue")
                    self.assertEqual(plan.issues, {}, TAG_BAD_D)
                    self.assertEqual(plan.live_dependencies, {}, TAG_BAD_D)
                    self.assertEqual(p08_sha256_file(bad), bad_hash,
                                     TAG_BAD_D + ": 坏 D 文件零写")
                    self.assertEqual(p08_sha256_file(good), good_hash,
                                     TAG_BAD_D + ": 好来源文件零写")
                    # 形态 b：请求键 ('A','D') —— 失败只记仍无见证键
                    plan2 = self._plan(
                        collab, task_id="task-badd-%s-%d%d" % (form, bad_uid,
                                                               good_uid),
                        task_dir=task, operation=op, keys=["A", "D"])
                    self.assertEqual(plan2.item_keys, ("A", "D"), TAG_BAD_D)
                    self.assertIn(("stage1", "A"), plan2.witnesses,
                                  TAG_BAD_D + ": 双键形态 A 仍见证")
                    issue = plan2.issues.get(("stage1", "D"))
                    self.assertIsNotNone(issue,
                                         TAG_BAD_D + ": 无见证 D 必有 issue"
                                         "（绝不为 None）")
                    self.assertEqual(issue.reason, "holder_unreadable",
                                     TAG_BAD_D)
                    self.assertEqual(issue.source_role, "actor", TAG_BAD_D)
                    self.assertTrue(issue.retryable, TAG_BAD_D)
                    self.assertEqual(
                        plan2.live_dependencies.get((good_uid, "stage1")),
                        ("D",),
                        TAG_BAD_D + ": issue 只阻塞完成主张，不阻塞 live 读"
                        "（坏来源自身是否也计入 live 属候选语义，工单未钉，"
                        "故只钉好来源侧）")
                    self.assertEqual(plan2.selected_paths, (p08_real(good),),
                                     TAG_BAD_D)


class NoWitnessIncompleteDiscoveryTests(KeyedInputPlanTestBase):
    """用例 2：覆盖未核＝issue（绝不负发现/绝不缺位）；干净负发现才进 live。"""

    def test_no_witness_with_incomplete_discovery_is_issue(self):
        op = TeamOperation(name="p08-screen", stage="screen")
        task = self._task_dir()
        member9 = self._member(task, 9)
        member11 = self._member(task, 11, with_f1=False)
        # --- pending_verification：legacy_adoption 真路径 ---
        collab_p = self._collab("collab-pend.db", task_id="task-p08-pend",
                                instance="instance-p", coverage=
                                "pending_verification")
        self._register(collab_p, instance="instance-p", uid=9, task_dir=task,
                       member_path=member9)
        plan = self._plan(collab_p, task_id="task-p08-pend", task_dir=task,
                          operation=op, keys=["K"])
        self.assertIsNotNone(plan, TAG_COVERAGE + ": plan 绝不为 None")
        issue = plan.issues.get(("stage1", "K"))
        self.assertIsNotNone(issue,
                             TAG_COVERAGE + ": issue 绝不缺位（不为 None）")
        self.assertEqual(issue.reason, "completion_source_unresolved",
                         TAG_COVERAGE)
        self.assertEqual(issue.source_role, "registry", TAG_COVERAGE)
        self.assertTrue(issue.retryable, TAG_COVERAGE)
        self.assertEqual(plan.witnesses, {}, TAG_COVERAGE)
        self.assertEqual(plan.selected_paths, (), TAG_COVERAGE)
        self.assertEqual(plan.live_dependencies, {(9, "stage1"): ("K",)},
                         TAG_COVERAGE + ": issue 不阻塞 live 读依赖")
        # --- unknown：raw INSERT legacy_verified + 坏证据（探针 C1 形态） ---
        collab_u = self._collab("collab-unk.db", task_id="task-p08-unk",
                                instance="instance-u", coverage="unknown")
        self._register(collab_u, instance="instance-u", uid=9, task_dir=task,
                       member_path=member9)
        plan = self._plan(collab_u, task_id="task-p08-unk", task_dir=task,
                          operation=op, keys=["K"])
        issue = plan.issues.get(("stage1", "K"))
        self.assertIsNotNone(issue, TAG_COVERAGE + ": unknown 同样绝不缺位")
        self.assertEqual(issue.reason, "completion_source_unresolved",
                         TAG_COVERAGE)
        self.assertEqual(issue.source_role, "registry", TAG_COVERAGE)
        self.assertTrue(issue.retryable, TAG_COVERAGE)
        self.assertEqual(plan.live_dependencies, {(9, "stage1"): ("K",)},
                         TAG_COVERAGE)
        # --- 干净负发现：new_covered + 全 holder 可核（含 not_prepared≠失败
        #     与 retired 代次不计 live） ---
        collab_c = self._collab("collab-clean.db", task_id="task-p08-clean",
                                instance="instance-c")
        self._register(collab_c, instance="instance-c", uid=9, task_dir=task,
                       member_path=member9, suffix="g1")
        conn = p08_borrowed(collab_c)
        try:
            tr.retire_source_on(conn, registration_id="reg-u9g1",
                                retired_reason="p08-fixture-retire")
            conn.commit()
        finally:
            p08_close_quietly(conn)
        self._register(collab_c, instance="instance-c", uid=9, task_dir=task,
                       member_path=member9, suffix="g2")
        self._register(collab_c, instance="instance-c", uid=11, task_dir=task,
                       member_path=member11)
        plan = self._plan(collab_c, task_id="task-p08-clean", task_dir=task,
                          operation=op, keys=["K2", "K1"])
        self.assertEqual(plan.issues, {},
                         TAG_COVERAGE + ": 完整覆盖的负发现无 issue")
        self.assertEqual(plan.witnesses, {}, TAG_COVERAGE)
        self.assertEqual(
            plan.live_dependencies,
            {(9, "stage1"): ("K2", "K1"), (11, "stage1"): ("K2", "K1")},
            TAG_COVERAGE + ": 干净负发现才进 live；键序=请求序；"
            "not_prepared（无 F1 表）同样可核计入")
        # --- retired-only：无 active 来源 → 干净负发现但零 live 依赖 ---
        collab_r = self._collab("collab-ret.db", task_id="task-p08-ret",
                                instance="instance-r")
        self._register(collab_r, instance="instance-r", uid=11, task_dir=task,
                       member_path=member11)
        conn = p08_borrowed(collab_r)
        try:
            tr.retire_source_on(conn, registration_id="reg-u11",
                                retired_reason="p08-fixture-retire")
            conn.commit()
        finally:
            p08_close_quietly(conn)
        plan = self._plan(collab_r, task_id="task-p08-ret", task_dir=task,
                          operation=op, keys=["K"])
        self.assertEqual(plan.issues, {}, TAG_COVERAGE)
        self.assertEqual(plan.live_dependencies, {},
                         TAG_COVERAGE + ": retired 代次不计 live")


class ConflictingSealsTests(KeyedInputPlanTestBase):
    """用例 3：可见冲突 seal 拒绝；同 seal 幂等；B1 撤销见证键可达呈报。"""

    def test_conflicting_seals(self):
        op = TeamOperation(name="p08-screen", stage="screen")
        task = self._task_dir()
        lib202 = self._member(task, 202)
        lib303 = self._member(task, 303)
        lib606 = self._member(task, 606)
        shared = p08_member_library(
            task, "members/shared/library.db", P08_LIB[404])
        # K：两 holder 不同 sealed_at → 不同 seal（①elif 冲突路径）；
        #     u606 第三方再证（③guard 复活防护路径——blocked 后不复活）
        p08_add_seal(lib202, task_instance_id="instance-conf", stage="stage1",
                     item_key="K", decision="include", sealed_at=P08_TS1,
                     origin="actor")
        p08_add_seal(lib303, task_instance_id="instance-conf", stage="stage1",
                     item_key="K", decision="include", sealed_at=P08_TS2,
                     origin="actor")
        p08_add_seal(
            lib606, task_instance_id="instance-conf", stage="stage1",
            item_key="K", decision="include", sealed_at=P08_TS3,
            origin="actor")
        # I：两 holder 同 payload → 同 seal（②else 幂等路径）
        idem = p08_add_seal(lib202, task_instance_id="instance-conf",
                            stage="stage1", item_key="I", decision="include",
                            sealed_at=P08_TS1, origin="actor")
        p08_add_seal(lib303, task_instance_id="instance-conf", stage="stage1",
                     item_key="I", decision="include", sealed_at=P08_TS1,
                     origin="actor")
        # W：shared 文件持有 seal；同物理路径×两 UID（404/505）登记
        p08_add_seal(shared, task_instance_id="instance-conf", stage="stage1",
                     item_key="W", decision="include", sealed_at=P08_TS1,
                     origin="actor")
        collab = self._collab("collab-conf.db", task_id="task-p08-conf",
                              instance="instance-conf")
        self._register(collab, instance="instance-conf", uid=202,
                       task_dir=task, member_path=lib202)
        self._register(collab, instance="instance-conf", uid=303,
                       task_dir=task, member_path=lib303)
        self._register(collab, instance="instance-conf", uid=404,
                       task_dir=task, member_path=shared)
        # B1 面：同物理文件 × 第二 UID 的 token 必须声明该文件的真实
        # library_id（P06 允许同文件多 UID 正确声明——探针 F3），两 token
        # 均可解析后，候选自身的 路径×UID 二次去重才接管（cross_uid_alias）。
        p08_register_source(
            collab, task_instance_id="instance-conf", uid=505,
            library_id=P08_LIB[404], task_dir=task, member_path=shared,
            registration_id="reg-u505")
        self._register(collab, instance="instance-conf", uid=606,
                       task_dir=task, member_path=lib606)
        plan = self._plan(collab, task_id="task-p08-conf", task_dir=task,
                          operation=op, keys=["K", "I", "W"])
        self.assertEqual(plan.item_keys, ("K", "I", "W"), TAG_CONFLICT)
        # ①elif 冲突路径：两 holder 互斥 seal（TS1 vs TS2 → 不同 seal_id/
        #   content）→ conflicting_seals，无 witness/无 live/不进 selected
        issue_k = plan.issues.get(("stage1", "K"))
        self.assertIsNotNone(issue_k, TAG_CONFLICT + " ①互斥→issue")
        self.assertEqual(issue_k.reason, "conflicting_seals", TAG_CONFLICT)
        self.assertEqual(issue_k.source_role, "actor", TAG_CONFLICT)
        self.assertFalse(issue_k.retryable, TAG_CONFLICT)
        self.assertNotIn(("stage1", "K"), plan.witnesses, TAG_CONFLICT)
        self.assertNotIn((202, "stage1"), plan.live_dependencies, TAG_CONFLICT)
        self.assertNotIn((303, "stage1"), plan.live_dependencies, TAG_CONFLICT)
        # ③guard 复活防护：冲突 blocked 后第三方（u606，TS3 第三枚互斥
        #   seal）再证不复活见证——K 终局仍为 conflicting_seals issue，
        #   witnesses 不含 K（第三方 seal 不成为见证）
        self.assertEqual(issue_k.reason, "conflicting_seals",
                         TAG_CONFLICT + " ③blocked 终局不被第三方改写")
        self.assertNotIn(("stage1", "K"), plan.witnesses,
                         TAG_CONFLICT + " ③第三方再证不复活 witness")
        # ②else 幂等路径：同 seal 双 holder → 保留确定序首个（u202）
        witness_i = plan.witnesses.get(("stage1", "I"))
        self.assertIsNotNone(witness_i,
                             TAG_CONFLICT + " ②同 seal 幂等保留见证")
        self.assertEqual(witness_i.seal_id, idem["seal_id"], TAG_CONFLICT)
        self.assertEqual(witness_i.source_path, p08_real(lib202),
                         TAG_CONFLICT + " ②首扫描 holder（uid 序）胜出")
        self.assertEqual(witness_i.registration_id, "reg-u202", TAG_CONFLICT)
        self.assertNotIn(("stage1", "I"), plan.issues, TAG_CONFLICT)
        # B1：cross_uid_alias 撤销原见证后仍可达 issue 呈报
        issue_w = plan.issues.get(("stage1", "W"))
        self.assertIsNotNone(issue_w,
                             TAG_CONFLICT + ": 被撤销见证键可达 issue 呈报")
        self.assertEqual(issue_w.reason, "cross_uid_alias", TAG_CONFLICT)
        self.assertEqual(issue_w.source_role, "actor", TAG_CONFLICT)
        self.assertFalse(issue_w.retryable, TAG_CONFLICT)
        self.assertNotIn(("stage1", "W"), plan.witnesses,
                         TAG_CONFLICT + ": 撤销后无 witness")
        self.assertEqual(set(plan.witnesses), {("stage1", "I")}, TAG_CONFLICT)
        self.assertEqual(plan.live_dependencies, {}, TAG_CONFLICT)
        self.assertEqual(plan.selected_paths, (p08_real(lib202),),
                         TAG_CONFLICT)
        self.assertNotIn(p08_real(shared), plan.selected_paths,
                         TAG_CONFLICT + ": 撤销路径不进 selected_paths")


class Stage1ExcludePrunesStage2Tests(KeyedInputPlanTestBase):
    """用例 4：链式操作 collab 命中 stage1 exclude → stage2 全剪枝；
    screen 单阶段不受影响；fulltext（HIGH#1 B1 后）为链式双阶段同构。"""

    def test_stage1_exclude_prunes_stage2(self):
        task = self._task_dir()
        good = self._member(task, 9)
        collab = self._collab("collab-prune.db", task_id="task-p08-prune",
                              instance="instance-pr")
        exclude = p08_add_seal(
            collab, task_instance_id="instance-pr", stage="stage1",
            item_key="K", decision="exclude", origin="collab")
        p08_add_seal(good, task_instance_id="instance-pr", stage="stage2",
                     item_key="K", decision="include", origin="actor")
        self._register(collab, instance="instance-pr", uid=9, task_dir=task,
                       member_path=good)
        # --- 链式（coding/appraisal）：collab stage1 exclude → 无 stage2 ---
        for stage_name in ("coding", "appraisal"):
            with self.subTest(chained=stage_name):
                op = TeamOperation(name="p08-" + stage_name, stage=stage_name)
                plan = self._plan(collab, task_id="task-p08-prune",
                                  task_dir=task, operation=op,
                                  keys=["K", "J"])
                self.assertEqual(plan.item_keys, ("K", "J"), TAG_PRUNE)
                witness1 = plan.witnesses.get(("stage1", "K"))
                self.assertIsNotNone(witness1, TAG_PRUNE)
                self.assertEqual(witness1.seal_id, exclude["seal_id"],
                                 TAG_PRUNE + ": collab stage1 见证在场")
                self.assertIsNone(witness1.source_path, TAG_PRUNE)
                self.assertNotIn(("stage2", "K"), plan.witnesses,
                                 TAG_PRUNE + ": pending 全无 stage2 K")
                self.assertNotIn(("stage2", "K"), plan.issues,
                                 TAG_PRUNE + ": holder 发现全无 stage2 K")
                self.assertNotIn(
                    "K", plan.live_dependencies.get((9, "stage2"), ()),
                    TAG_PRUNE + ": live 全无 stage2 K")
                # 未剪枝键 J 对照：两阶段干净负发现（live 覆盖两阶段）
                self.assertEqual(
                    plan.live_dependencies,
                    {(9, "stage1"): ("J",), (9, "stage2"): ("J",)},
                    TAG_PRUNE + ": 未剪枝键两阶段 live 正常")
                self.assertEqual(plan.issues, {}, TAG_PRUNE)
                self.assertEqual(plan.selected_paths, (), TAG_PRUNE)
        # --- fulltext（HIGH#1 B1 后链式双阶段）：与 coding 同构——collab
        # stage1 exclude 剪掉 K 的 stage2 目标（旧"单阶段照常取 holder
        # stage2 见证"合同不再成立，按 2026-11-20 设计 §4.3 更新）；J
        # 两阶段干净负发现（live 覆盖两阶段）。---
        op_ft = TeamOperation(name="p08-fulltext", stage="fulltext")
        plan = self._plan(collab, task_id="task-p08-prune", task_dir=task,
                          operation=op_ft, keys=["K", "J"])
        witness1 = plan.witnesses.get(("stage1", "K"))
        self.assertIsNotNone(witness1,
                             TAG_PRUNE + ": fulltext 链腿取 collab stage1 见证")
        self.assertEqual(witness1.seal_id, exclude["seal_id"], TAG_PRUNE)
        self.assertNotIn(("stage2", "K"), plan.witnesses,
                         TAG_PRUNE + ": fulltext stage2 目标被 stage1 exclude 剪掉")
        self.assertNotIn(("stage2", "K"), plan.issues,
                         TAG_PRUNE + ": holder 发现全无 stage2 K")
        self.assertEqual(
            plan.live_dependencies,
            {(9, "stage1"): ("J",), (9, "stage2"): ("J",)},
            TAG_PRUNE + ": 未剪枝键两阶段 live 正常")
        self.assertEqual(plan.issues, {}, TAG_PRUNE)
        self.assertEqual(plan.selected_paths, (), TAG_PRUNE)
        # --- screen（单阶段 stage1）：照常取 collab stage1 见证 ---
        op_sc = TeamOperation(name="p08-screen", stage="screen")
        plan = self._plan(collab, task_id="task-p08-prune", task_dir=task,
                          operation=op_sc, keys=["K", "J"])
        witness1 = plan.witnesses.get(("stage1", "K"))
        self.assertIsNotNone(witness1,
                             TAG_PRUNE + ": screen 照常取 stage1 见证")
        self.assertEqual(witness1.seal_id, exclude["seal_id"], TAG_PRUNE)
        self.assertEqual(plan.live_dependencies, {(9, "stage1"): ("J",)},
                         TAG_PRUNE)
        self.assertEqual(plan.selected_paths, (), TAG_PRUNE)


class ProvisionalHandlesReleasedTests(KeyedInputPlanTestBase):
    """用例 5：临时 reader 全释放；连接不出函数域；pending 空零枚举零触碰。"""

    def test_all_provisional_handles_released(self):
        op = TeamOperation(name="p08-screen", stage="screen")
        task = self._task_dir()
        good = self._member(task, 9)
        bad = self._member(task, 2)
        p08_add_seal(good, task_instance_id="instance-rel", stage="stage1",
                     item_key="K", decision="include", origin="actor")
        p08_insert_corrupt_seal_row(bad, task_instance_id="instance-rel",
                                    stage="stage1", item_key="K")
        collab = self._collab("collab-rel.db", task_id="task-p08-rel",
                              instance="instance-rel")
        self._register(collab, instance="instance-rel", uid=2, task_dir=task,
                       member_path=bad)
        self._register(collab, instance="instance-rel", uid=9, task_dir=task,
                       member_path=good)
        # --- (a) 正常路径：open→scan→release + 连接不出函数域 ---
        good_hash = p08_sha256_file(good)
        bad_hash = p08_sha256_file(bad)
        baseline = p08_count_live_connections()
        sensitivity = sqlite3.connect(
            str(self.base / "mechanism_probe.db"))
        try:
            self.assertGreaterEqual(
                p08_count_live_connections(), baseline + 1,
                TAG_RELEASE + ": gc 计数机制自证（打开即+1；探针 A2）")
        finally:
            sensitivity.close()
        conn = p08_borrowed(collab)
        # 观测窗对齐（隔离运行定因：2>1 假红非产品泄漏）——基线必须在打开
        # 自有借用连接**之后**拍：该连接在调用结束后被 finally 关闭、但局部
        # 名仍引用其（已关闭）对象，gc 计数两侧都必须包含它，否则 post 恒=
        # pre+1。产品侧真泄漏仍会以 post>pre 呈现（首轮实测 2>1 恰等于
        # sensitivity(1)+自有 conn(1)，无产品驻留）。
        pre_build = p08_count_live_connections()
        try:
            plan = tki.build_keyed_read_plan(
                p08_ctx("task-p08-rel", task), collab_conn=conn,
                operation=op, item_keys=["K"])
        finally:
            # 借用连接不被模块收走：仍 native 事务/Row/可执行（C02 纪律）
            self.assertTrue(conn.in_transaction,
                            TAG_RELEASE + ": collab 借用连接事务未被收走")
            self.assertIs(conn.row_factory, sqlite3.Row, TAG_RELEASE)
            self.assertIsNotNone(conn.execute("SELECT 1").fetchone(),
                                 TAG_RELEASE + ": 借用连接未被 close")
            p08_close_quietly(conn)
        self.assertIn(("stage1", "K"), plan.witnesses, TAG_RELEASE)
        post_build = p08_count_live_connections()
        self.assertLessEqual(
            post_build, pre_build,
            TAG_RELEASE + ": 连接不出函数域（gc 反射无新增驻留连接）")
        self.assertEqual(p08_sha256_file(good), good_hash,
                         TAG_RELEASE + ": holder 零写")
        self._assert_replaceable(good, TAG_RELEASE + ": 已释放（句柄锁探针）")
        # --- (b) 扫描失败路径：reader 同样释放（坏 D 首 token 首键读即抛，
        #     失败记入当刻仍无见证键 K/X；K 随后由好来源见证清除） ---
        plan_bad = self._plan(collab, task_id="task-p08-rel", task_dir=task,
                              operation=op, keys=["K", "X"])
        issue = plan_bad.issues.get(("stage1", "X"))
        self.assertIsNotNone(issue, TAG_RELEASE)
        self.assertEqual(issue.reason, "holder_unreadable", TAG_RELEASE)
        self.assertEqual(p08_sha256_file(bad), bad_hash,
                         TAG_RELEASE + ": 失败路径零写")
        self._assert_replaceable(bad,
                                 TAG_RELEASE + ": 失败路径 reader 已释放")
        # --- (c) _release_provisional_reader 单元语义（真实连接） ---
        reader = tki._open_provisional_reader(good)
        self.assertTrue(reader.in_transaction,
                        TAG_RELEASE + ": 临时 reader 以活动事务打开")
        tki._release_provisional_reader(reader, primary=None)
        with self.assertRaises(sqlite3.ProgrammingError, msg=TAG_RELEASE):
            reader.in_transaction  # noqa: B018 — 关闭后访问即断言
        scratch = self.base / "release_rollback.db"
        scratch_conn = sqlite3.connect(str(scratch))
        scratch_conn.close()
        rw = sqlite3.connect(str(scratch))
        try:
            rw.execute("BEGIN IMMEDIATE")
            rw.execute("CREATE TABLE p08_release_probe (x INTEGER)")
            tki._release_provisional_reader(rw, primary=None)
            check = sqlite3.connect(str(scratch))
            try:
                leftover = check.execute(
                    "SELECT name FROM main.sqlite_master"
                    " WHERE name='p08_release_probe'").fetchone()
            finally:
                check.close()
            self.assertIsNone(
                leftover, TAG_RELEASE + ": 释放=rollback 非 commit")
        finally:
            p08_close_quietly(rw)
        primary = RuntimeError("p08-primary-boom")
        closed = sqlite3.connect(str(scratch))
        closed.close()
        tki._release_provisional_reader(closed, primary=primary)
        notes = getattr(primary, "__notes__", [])
        self.assertTrue(
            any("provisional reader release failure" in note
                for note in notes),
            TAG_RELEASE + ": 清理失败附着 add_note（不遮蔽主错）")
        with self.assertRaises(RuntimeError, msg=TAG_RELEASE) as caught:
            raise primary
        self.assertIs(caught.exception, primary,
                      TAG_RELEASE + ": 主错原样重抛（不被替换）")
        closed2 = sqlite3.connect(str(scratch))
        closed2.close()
        with self.assertRaises(sqlite3.ProgrammingError,
                               msg=TAG_RELEASE + ": 无 primary 时清理失败"
                               " 如实上抛") as caught2:
            tki._release_provisional_reader(closed2, primary=None)
        self.assertIs(type(caught2.exception), sqlite3.ProgrammingError,
                      TAG_RELEASE)
        # --- (d) pending 空：零枚举零触碰来源库（可观测锚+对照） ---
        collab_full = self._collab("collab-full.db",
                                   task_id="task-p08-full",
                                   instance="instance-full")
        p08_add_seal(collab_full, task_instance_id="instance-full",
                     stage="stage1", item_key="K", decision="include",
                     origin="collab")
        self._register(collab_full, instance="instance-full", uid=9,
                       task_dir=task, member_path=good)
        # 破坏 source 域布局（DROP 部分唯一索引；protocol 域不受影响）
        layout_conn = p08_borrowed(collab_full)
        try:
            layout_conn.execute(
                "DROP INDEX main.team_source_registry_one_active")
            layout_conn.commit()
        finally:
            p08_close_quietly(layout_conn)
        plan_full = self._plan(collab_full, task_id="task-p08-full",
                               task_dir=task, operation=op, keys=["K"])
        self.assertEqual(plan_full.item_keys, ("K",), TAG_RELEASE)
        witness = plan_full.witnesses[("stage1", "K")]
        self.assertIsNone(witness.source_path,
                          TAG_RELEASE + ": collab 见证无物理路径")
        self.assertIsNone(witness.registration_id, TAG_RELEASE)
        self.assertIsNone(witness.generation, TAG_RELEASE)
        self.assertEqual(witness.library_id, P08_OWNER_LIBRARY,
                         TAG_RELEASE + ": collab 权威域锚点")
        self.assertEqual(plan_full.selected_paths, (),
                         TAG_RELEASE + ": 零 holder 打开→零 selected_paths")
        self.assertEqual(plan_full.live_dependencies, {},
                         TAG_RELEASE + ": 零 live 依赖")
        self.assertEqual(plan_full.issues, {},
                         TAG_RELEASE + ": 已见证键零触碰来源库（source 域"
                         " 布局已破坏仍无感知）")
        self._assert_replaceable(
            good, TAG_RELEASE + ": 未被触碰（句柄锁探针）")
        # 对照：同布局 + 一 pending 键 → 枚举发生 → 任务级 fail-closed
        with self.assertRaises(tki.KeyedPlanUnavailableError,
                               msg=TAG_RELEASE) as caught3:
            self._plan(collab_full, task_id="task-p08-full", task_dir=task,
                       operation=op, keys=["K", "PENDING"])
        self.assertEqual(caught3.exception.reason, "registry_unavailable",
                         TAG_RELEASE + ": 枚举失败 fail-closed（对照）")


class CollabBorrowDisciplineTests(KeyedInputPlanTestBase):
    """支撑 1：C02 借用三查固定序/伪装拒/真实子类过门/closed 透传/零写。"""

    def test_collab_borrowing_discipline(self):
        op = TeamOperation(name="p08-screen", stage="screen")
        task = self._task_dir()
        collab = self._collab("collab-borrow.db",
                              task_id="task-p08-borrow",
                              instance="instance-borrow")
        member = self._member(task, 9)
        p08_add_seal(member, task_instance_id="instance-borrow",
                     stage="stage1", item_key="K", decision="include",
                     origin="actor")
        self._register(collab, instance="instance-borrow", uid=9,
                       task_dir=task, member_path=member)
        collab_hash = p08_sha256_file(collab)
        member_hash = p08_sha256_file(member)
        ctx = p08_ctx("task-p08-borrow", task)
        # --- 查 1：伪装代理拒（type() 不上当 __class__ property） ---
        real = sqlite3.connect(str(collab))
        real.row_factory = sqlite3.Row
        real.execute("BEGIN IMMEDIATE")
        proxy = P08DisguiseProxy(real)
        try:
            with self.assertRaises(TypeError, msg=TAG_BORROW) as caught:
                tki.build_keyed_read_plan(
                    ctx, collab_conn=proxy, operation=op, item_keys=["K"])
            self.assertIn("native", str(caught.exception), TAG_BORROW)
        finally:
            p08_close_quietly(real)
        # --- 查 2 先于查 3：无事务+错工厂双坏 → 先报活动事务 ---
        idle = sqlite3.connect(str(collab))
        try:
            with self.assertRaises(RuntimeError, msg=TAG_BORROW) as caught:
                tki.build_keyed_read_plan(
                    ctx, collab_conn=idle, operation=op, item_keys=["K"])
            self.assertIn("active caller transaction",
                          str(caught.exception), TAG_BORROW)
        finally:
            idle.close()
        # --- 查 3：活动事务但非 Row 工厂 → RuntimeError ---
        wrong_factory = sqlite3.connect(str(collab))
        try:
            wrong_factory.execute("BEGIN IMMEDIATE")
            with self.assertRaises(RuntimeError, msg=TAG_BORROW) as caught:
                tki.build_keyed_read_plan(
                    ctx, collab_conn=wrong_factory, operation=op,
                    item_keys=["K"])
            self.assertIn("sqlite3.Row", str(caught.exception), TAG_BORROW)
        finally:
            p08_close_quietly(wrong_factory)
        # --- 真实子类过门（native subclass 合同面） ---
        sub = sqlite3.connect(str(collab), factory=P08NativeSubConn)
        try:
            sub.row_factory = sqlite3.Row
            sub.execute("BEGIN IMMEDIATE")
            plan = tki.build_keyed_read_plan(
                ctx, collab_conn=sub, operation=op, item_keys=["K"])
            self.assertEqual(plan.item_keys, ("K",),
                             TAG_BORROW + ": 真实子类完整过门")
        finally:
            p08_close_quietly(sub)
        # --- closed 连接 ProgrammingError 原样透传（不包装） ---
        closed = sqlite3.connect(str(collab))
        closed.row_factory = sqlite3.Row
        closed.execute("BEGIN IMMEDIATE")
        closed.close()
        with self.assertRaises(sqlite3.ProgrammingError,
                               msg=TAG_BORROW) as caught:
            tki.build_keyed_read_plan(
                ctx, collab_conn=closed, operation=op, item_keys=["K"])
        self.assertIs(type(caught.exception), sqlite3.ProgrammingError,
                      TAG_BORROW + ": closed 透传非 TypeError/RuntimeError")
        # --- 零写路径：成功调用前后 collab/holder 文件字节不变 ---
        plan = self._plan(collab, task_id="task-p08-borrow", task_dir=task,
                          operation=op, keys=["K"])
        self.assertIn(("stage1", "K"), plan.witnesses, TAG_BORROW)
        self.assertEqual(p08_sha256_file(collab), collab_hash,
                         TAG_BORROW + ": collab 零写")
        self.assertEqual(p08_sha256_file(member), member_hash,
                         TAG_BORROW + ": holder 零写")


class FinalityPriorityTests(KeyedInputPlanTestBase):
    """支撑 2：五级终局优先序成对组合（见证>冲突>失败>覆盖未决>负发现）。"""

    def test_finality_priority_order(self):
        op = TeamOperation(name="p08-screen", stage="screen")
        task = self._task_dir()
        bad = self._member(task, 2)
        h1 = self._member(task, 5)
        h2 = self._member(task, 7)
        p08_insert_corrupt_seal_row(bad, task_instance_id="instance-prio",
                                    stage="stage1", item_key="KF")
        p08_add_seal(h1, task_instance_id="instance-prio", stage="stage1",
                     item_key="KC", decision="include", sealed_at=P08_TS1,
                     origin="actor")
        p08_add_seal(h2, task_instance_id="instance-prio", stage="stage1",
                     item_key="KC", decision="include", sealed_at=P08_TS2,
                     origin="actor")
        # --- PlanA：覆盖未决 + 坏 D + 冲突对（KW 见证 > KC 冲突 > KF 失败） ---
        collab_a = self._collab(
            "collab-prio-a.db", task_id="task-p08-prio-a",
            instance="instance-prio", coverage="pending_verification")
        witness_seal = p08_add_seal(
            collab_a, task_instance_id="instance-prio", stage="stage1",
            item_key="KW", decision="include", origin="collab")
        self._register(collab_a, instance="instance-prio", uid=2,
                       task_dir=task, member_path=bad)
        self._register(collab_a, instance="instance-prio", uid=5,
                       task_dir=task, member_path=h1)
        self._register(collab_a, instance="instance-prio", uid=7,
                       task_dir=task, member_path=h2)
        plan_a = self._plan(collab_a, task_id="task-p08-prio-a",
                            task_dir=task, operation=op,
                            keys=["KW", "KC", "KF"])
        # 见证 > 一切：KW 有 collab 见证，坏 D+覆盖未决都不产生 issue
        witness_kw = plan_a.witnesses.get(("stage1", "KW"))
        self.assertIsNotNone(witness_kw, TAG_PRIORITY)
        self.assertEqual(witness_kw.seal_id, witness_seal["seal_id"],
                         TAG_PRIORITY)
        self.assertNotIn(("stage1", "KW"), plan_a.issues,
                         TAG_PRIORITY + ": 见证 > 冲突/失败/覆盖（成对上界）")
        # 冲突 > 失败：KC 先被坏 D 记 holder_unreadable（uid 序 2<5<7），
        # 后两 holder 冲突 → 终局 conflicting_seals
        issue_kc = plan_a.issues.get(("stage1", "KC"))
        self.assertIsNotNone(issue_kc, TAG_PRIORITY)
        self.assertEqual(issue_kc.reason, "conflicting_seals",
                         TAG_PRIORITY + ": 冲突 > 失败")
        self.assertNotIn(("stage1", "KC"), plan_a.witnesses, TAG_PRIORITY)
        # 失败 > 覆盖未决：KF 只有坏 D 失败 + 覆盖未决 → holder_unreadable
        issue_kf = plan_a.issues.get(("stage1", "KF"))
        self.assertIsNotNone(issue_kf, TAG_PRIORITY)
        self.assertEqual(issue_kf.reason, "holder_unreadable",
                         TAG_PRIORITY + ": 失败 > 覆盖未决")
        self.assertNotEqual(issue_kf.reason, "completion_source_unresolved",
                            TAG_PRIORITY)
        # 带 issue 键仍保留已核 active 源 live 读依赖（坏来源 uid2 自身
        # 是否也计入 live 属候选语义——其 F1 扫描失败但身份已核+active，
        # 工单未钉，故只钉两好来源侧）
        self.assertEqual(plan_a.live_dependencies.get((5, "stage1")),
                         ("KF",), TAG_PRIORITY + ": issue 不阻塞当前票读取")
        self.assertEqual(plan_a.live_dependencies.get((7, "stage1")),
                         ("KF",), TAG_PRIORITY)
        # --- PlanB：覆盖未决>负发现（无失败源） ---
        collab_b = self._collab(
            "collab-prio-b.db", task_id="task-p08-prio-b",
            instance="instance-prio-b", coverage="pending_verification")
        self._register(collab_b, instance="instance-prio-b", uid=5,
                       task_dir=task, member_path=h1)
        plan_b = self._plan(collab_b, task_id="task-p08-prio-b",
                            task_dir=task, operation=op, keys=["KP"])
        issue_kp = plan_b.issues.get(("stage1", "KP"))
        self.assertIsNotNone(issue_kp,
                             TAG_PRIORITY + ": 覆盖未决 > 负发现（必 issue）")
        self.assertEqual(issue_kp.reason, "completion_source_unresolved",
                         TAG_PRIORITY)
        self.assertEqual(plan_b.live_dependencies, {(5, "stage1"): ("KP",)},
                         TAG_PRIORITY)
        # --- PlanC：负发现本体（完整覆盖 + 可核 holder → 干净负发现） ---
        collab_c = self._collab(
            "collab-prio-c.db", task_id="task-p08-prio-c",
            instance="instance-prio-c")
        self._register(collab_c, instance="instance-prio-c", uid=5,
                       task_dir=task, member_path=h1)
        plan_c = self._plan(collab_c, task_id="task-p08-prio-c",
                            task_dir=task, operation=op, keys=["KN"])
        self.assertEqual(plan_c.issues, {},
                         TAG_PRIORITY + ": 完整覆盖的负发现无 issue")
        self.assertEqual(plan_c.live_dependencies, {(5, "stage1"): ("KN",)},
                         TAG_PRIORITY + ": 干净负发现进 live")


class StageDomainRejectTests(KeyedInputPlanTestBase):
    """支撑 3：stage 域拒（management/history ValueError）+ 形状误用族。"""

    def test_stage_domain_rejected(self):
        task = self._task_dir()
        collab = self._collab("collab-stage.db", task_id="task-p08-stage",
                              instance="instance-stage")
        conn = p08_borrowed(collab)
        ctx = p08_ctx("task-p08-stage", task)
        try:
            for bad_stage in ("management", "history"):
                with self.subTest(stage=bad_stage):
                    operation = TeamOperation(name="p08-op",
                                              stage=bad_stage)
                    with self.assertRaises(ValueError,
                                           msg=TAG_STAGE) as caught:
                        tki.build_keyed_read_plan(
                            ctx, collab_conn=conn, operation=operation,
                            item_keys=["K"])
                    self.assertIn("no fixed F1 adapter",
                                  str(caught.exception), TAG_STAGE)
            with self.subTest(stage="non-str"):
                operation = TeamOperation(name="p08-op", stage=7)
                with self.assertRaises(ValueError, msg=TAG_STAGE):
                    tki.build_keyed_read_plan(
                        ctx, collab_conn=conn, operation=operation,
                        item_keys=["K"])
            with self.subTest(operation="non-TeamOperation lookalike"):
                lookalike = p08_ctx("not-an-operation", task)
                with self.assertRaises(TypeError, msg=TAG_STAGE):
                    tki.build_keyed_read_plan(
                        ctx, collab_conn=conn, operation=lookalike,
                        item_keys=["K"])
            with self.subTest(operation="plain str"):
                with self.assertRaises(TypeError, msg=TAG_STAGE):
                    tki.build_keyed_read_plan(
                        ctx, collab_conn=conn, operation="screen",
                        item_keys=["K"])
            with self.subTest(name="empty"):
                operation = TeamOperation(name="", stage="screen")
                with self.assertRaises(TypeError, msg=TAG_STAGE):
                    tki.build_keyed_read_plan(
                        ctx, collab_conn=conn, operation=operation,
                        item_keys=["K"])
            with self.subTest(keys="str not iterable-of-str"):
                operation = TeamOperation(name="p08-op", stage="screen")
                with self.assertRaises(ValueError, msg=TAG_STAGE):
                    tki.build_keyed_read_plan(
                        ctx, collab_conn=conn, operation=operation,
                        item_keys="K")
            # 四个合法 stage 冒烟：完整可用（干净负发现形状 + item_keys 回显）
            for good_stage in ("screen", "fulltext", "coding", "appraisal"):
                with self.subTest(smoke=good_stage):
                    operation = TeamOperation(name="p08-op",
                                              stage=good_stage)
                    plan = tki.build_keyed_read_plan(
                        ctx, collab_conn=conn, operation=operation,
                        item_keys=["K"])
                    self.assertIsInstance(plan, KeyedReadPlan, TAG_STAGE)
                    self.assertEqual(plan.item_keys, ("K",), TAG_STAGE)
                    self.assertEqual(plan.witnesses, {}, TAG_STAGE)
                    self.assertEqual(plan.issues, {}, TAG_STAGE)
                    self.assertEqual(plan.live_dependencies, {}, TAG_STAGE)
                    self.assertEqual(plan.selected_paths, (), TAG_STAGE)
        finally:
            p08_close_quietly(conn)


if __name__ == "__main__":
    unittest.main()
